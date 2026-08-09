"""Exportação da rede como elementos do OpenDSS.

Camada de núcleo: não importa Qt, para poder ser testada headless e executada
em thread secundária sem cuidados de afinidade.

Dois arquivos de rede são sempre gerados — ``trechos.dss`` (``Line``) e
``chaves.dss`` (``Line ... Switch=Yes``). Cargas e geradores acrescentam, cada
um, um arquivo por contagem de fases (``Load`` + ``LoadShape``). O par
``<CODIGO>_Master.dss`` e ``<CODIGO>_Buscoords.csv`` cria o circuito, chama os
demais arquivos existentes e resolve.

Convenções de unidade adotadas, todas amarradas ao ``units=km`` emitido em cada
linha:

- ``R1/X1/R0/X0`` vêm do cabo de fase (``CABOF_ID``) em ohms por quilômetro;
- ``COMPR`` está em metros (unidade canônica do modelo) e vira quilômetros;
- ``QCAP`` é a potência reativa capacitiva do cabo em **kvar por quilômetro e
  por fase**; ``C1`` é a capacitância shunt de sequência positiva, isto é, o
  capacitor entre **fase e neutro**, então a tensão da conversão é a tensão de
  fase — e o circuito informa ``VNOM`` como tensão de **linha**.

O ``kV`` das cargas e dos geradores é a mesma tensão de fase, pela mesma razão:
cada ``Load`` é ligada entre fase e neutro (``conn=wye``).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

from .model import (
    CableModel,
    CircuitCatalogModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
)
from .phase_config import PhaseConfiguration

if TYPE_CHECKING:
    # Só para tipagem: em tempo de execução o import seria circular, porque
    # opendss_settings reusa o parse_number deste módulo — de propósito, para
    # não existirem duas regras de leitura de separador decimal no projeto.
    from .opendss_settings import OpenDssLoadSettings
    from .generator_update import GeneratorUpdateModel


FREQUENCY_HZ = 60.0
LINES_FILENAME = "trechos.dss"
SWITCHES_FILENAME = "chaves.dss"
REGULATORS_FILENAME = "reguladores.dss"
SINGLE_PHASE_LOADS_FILENAME = "cargasmonofasicas.dss"
TWO_PHASE_LOADS_FILENAME = "cargasbifasicas.dss"
THREE_PHASE_LOADS_FILENAME = "cargastrifasicas.dss"
SINGLE_PHASE_GENERATORS_FILENAME = "geradoresmonofasicos.dss"
TWO_PHASE_GENERATORS_FILENAME = "geradoresbifasicos.dss"
THREE_PHASE_GENERATORS_FILENAME = "geradorestrifasicos.dss"
# O master e as coordenadas levam o código do circuito no nome, então só o
# sufixo é constante.
MASTER_FILENAME_SUFFIX = "_Master.dss"
BUSCOORDS_FILENAME_SUFFIX = "_Buscoords.csv"
# Barra infinita: o estudo de alimentador não modela a montante da subestação.
SOURCE_SHORT_CIRCUIT_MVA = "999999"
CLOSED_SWITCH_STATE = "1"
MAX_REPORTED_ISSUES = 200
LOAD_SHAPE_PREFIX = "PERFIL-"
# NPAT 0..3: o LoadShape diário tem exatamente quatro pontos.
LOAD_PATTERN_COUNT = 4
# Um arquivo por contagem de fases, com o rótulo usado no cabeçalho.
_LOAD_FILES = {
    1: (SINGLE_PHASE_LOADS_FILENAME, "monofasicas"),
    2: (TWO_PHASE_LOADS_FILENAME, "bifasicas"),
    3: (THREE_PHASE_LOADS_FILENAME, "trifasicas"),
}
LOAD_PHASE_COUNTS = tuple(sorted(_LOAD_FILES))
_GENERATOR_FILES = {
    1: (SINGLE_PHASE_GENERATORS_FILENAME, "monofasicos"),
    2: (TWO_PHASE_GENERATORS_FILENAME, "bifasicos"),
    3: (THREE_PHASE_GENERATORS_FILENAME, "trifasicos"),
}
GENERATOR_PHASE_COUNTS = tuple(sorted(_GENERATOR_FILES))
# As letras do NOME em fases2.json dizem quais colunas de patamar a carga
# consome. "DN" (fase com neutro) usa as mesmas PD/QD de "D", e a bifásica "DE"
# usa PD/QD em uma fase e PE/QE na outra.
_PATTERN_COLUMNS_BY_PHASE = {
    "D": ("pd", "qd"),
    "E": ("pe", "qe"),
    "F": ("pf", "qf"),
}

# --- Regulador de tensão -----------------------------------------------------
# Um regulador trifásico vira três transformadores monofásicos, um por fase, e
# cada um ganha o seu RegControl. Os valores abaixo são a modelagem adotada; o
# CSV traz apenas VNOM e SNOM.
REGULATOR_NAME_PREFIX = "REG-"
REGULATOR_CONTROL_NAME_PREFIX = "CTRL-"
# Transformador quase ideal: o regulador injeta tensão em série, não impedância.
REGULATOR_XHL_PERCENT = 0.01
REGULATOR_LOAD_LOSS_PERCENT = 0.01
# Transformador de potencial: 115 V entre fases no secundário, como o primário,
# de modo que ``ptratio`` fica VNOM*1000/115 — o √3 dos dois lados se cancela.
REGULATOR_PT_SECONDARY_V = 115.0
# Banda fixa, na mesma base do TP secundário (115 V) que vreg — o mesmo jeito
# de especificar bandwidth no dial de um regulador real.
REGULATOR_BAND_V = 3.0
# O regulador ocupa o lugar do trecho, então a impedância dele sai do modelo.
# Acima deste comprimento isso deixa de ser desprezível e vira aviso.
REGULATOR_SEGMENT_LENGTH_WARNING_M = 10.0
# VNOM do regulador e do circuito são a mesma tensão de linha, em kV. Uma
# divergência maior que esta denuncia unidade trocada (volts em vez de kV),
# que passaria despercebida por gerar um modelo aceito e absurdo.
REGULATOR_VOLTAGE_TOLERANCE = 0.10

_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:(?:\d+(?:[.,]\d*)?)|(?:[.,]\d+))(?:[eE][+-]?\d+)?$"
)

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class OpenDssExportIssue:
    """Ocorrência encontrada ao exportar um trecho."""

    segment_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class OpenDssLineExportResult:
    text: str
    exported_count: int
    skipped_switch_count: int
    discarded_count: int
    issues: tuple[OpenDssExportIssue, ...]
    omitted_issues: int
    used_names: frozenset[str] = frozenset()
    # Índice reverso ``nome da Line`` → índice do trecho, na ordem de emissão.
    # Só quem exporta conhece as regras que produziram cada nome (CODIGO
    # saneado, fallback para o TRECHO_ID, descarte de homônimos), então
    # recompor esse vínculo depois exigiria uma segunda cópia dessas regras.
    exported_segments: tuple[tuple[str, int], ...] = ()

    @property
    def has_warnings(self) -> bool:
        return self.discarded_count > 0 or bool(self.issues)


@dataclass(frozen=True, slots=True)
class OpenDssSwitchExportResult:
    text: str
    exported_count: int
    open_count: int
    discarded_count: int
    issues: tuple[OpenDssExportIssue, ...]
    omitted_issues: int
    used_names: frozenset[str] = frozenset()
    # Mesmo índice reverso dos trechos; aqui o nome vem do CODIGO da chave.
    exported_segments: tuple[tuple[str, int], ...] = ()

    @property
    def has_warnings(self) -> bool:
        return self.discarded_count > 0 or bool(self.issues)


@dataclass(frozen=True, slots=True)
class OpenDssRegulatorExportResult:
    """Resultado de ``reguladores.dss``.

    ``exported_count`` conta reguladores, não elementos: cada um vira três
    ``Transformer`` mais três ``RegControl``.

    ``replaced_segments`` são os trechos que **deixaram de ser** ``Line``, e é o
    que :func:`build_line_export` recebe para não emitir uma linha em paralelo
    com o regulador. Só entra aqui o trecho cujo regulador foi de fato emitido:
    um regulador descartado precisa manter a linha dele, senão a exportação
    apagaria um ramo da rede em silêncio.
    """

    text: str
    exported_count: int
    discarded_count: int
    issues: tuple[OpenDssExportIssue, ...]
    omitted_issues: int
    used_names: frozenset[str] = frozenset()
    replaced_segments: frozenset[int] = frozenset()
    # Índice reverso ``nome do Transformer`` → (trecho, letra da fase), na ordem
    # de emissão. Mesma razão do ``exported_segments`` dos trechos: só o
    # exportador conhece as regras que produziram cada nome, e o fluxo de
    # potência precisa devolver o tap ao regulador certo.
    exported_units: tuple[tuple[str, int, str], ...] = ()

    @property
    def has_warnings(self) -> bool:
        return self.discarded_count > 0 or bool(self.issues)


@dataclass(frozen=True, slots=True)
class OpenDssLoadExportResult:
    """Resultado comum aos arquivos de carga, um por contagem de fases.

    ``exported_count`` conta **cargas de origem**, não linhas ``Load``: uma
    carga bifásica vira duas ``Load`` e ainda assim soma 1, para o relatório
    falar a mesma língua do CSV importado. ``skipped_other_phase_count`` conta
    as cargas de outra contagem de fases, que pertencem a outro arquivo.
    """

    text: str
    exported_count: int
    skipped_other_phase_count: int
    discarded_count: int
    issues: tuple[OpenDssExportIssue, ...]
    omitted_issues: int
    used_names: frozenset[str] = frozenset()

    @property
    def has_warnings(self) -> bool:
        return self.discarded_count > 0 or bool(self.issues)


@dataclass(frozen=True, slots=True)
class OpenDssGeneratorExportResult:
    """Resultado de um arquivo de geradores agrupado por número de fases."""

    text: str
    exported_count: int
    skipped_other_phase_count: int
    discarded_count: int
    issues: tuple[OpenDssExportIssue, ...]
    omitted_issues: int
    used_names: frozenset[str] = frozenset()

    @property
    def has_warnings(self) -> bool:
        return self.discarded_count > 0 or bool(self.issues)


@dataclass(frozen=True, slots=True)
class OpenDssMasterExportResult:
    """Arquivo principal e as coordenadas de barra que ele referencia.

    Os nomes vivem aqui, e não em constantes como os demais arquivos, porque
    dependem do código do circuito exportado. ``text`` vazio significa que o
    master não pôde ser montado — o motivo está em ``issues``.
    """

    master_filename: str
    text: str
    buscoords_filename: str
    buscoords_text: str
    bus_count: int
    discarded_count: int
    issues: tuple[OpenDssExportIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.discarded_count > 0 or bool(self.issues)


@dataclass(frozen=True, slots=True)
class OpenDssExportBundle:
    """Conjunto de arquivos gerados por uma única exportação.

    Os três resultados de carga são opcionais e andam juntos: sem cargas e
    patamares importados nenhum dos arquivos de carga é gerado, e a exportação
    segue produzindo apenas os dois arquivos de rede.

    ``regulators`` é opcional pela mesma razão, e some do ``element_files``
    quando nenhum regulador foi exportado: uma exportação sem reguladores
    continua gerando exatamente os mesmos arquivos de sempre.
    """

    lines: OpenDssLineExportResult
    switches: OpenDssSwitchExportResult
    regulators: OpenDssRegulatorExportResult | None = None
    single_phase_loads: OpenDssLoadExportResult | None = None
    two_phase_loads: OpenDssLoadExportResult | None = None
    three_phase_loads: OpenDssLoadExportResult | None = None
    single_phase_generators: OpenDssGeneratorExportResult | None = None
    two_phase_generators: OpenDssGeneratorExportResult | None = None
    three_phase_generators: OpenDssGeneratorExportResult | None = None
    master: OpenDssMasterExportResult | None = None

    @property
    def loads_by_phase_count(
        self,
    ) -> tuple[tuple[int, OpenDssLoadExportResult], ...]:
        """Resultados de carga presentes, em ordem de contagem de fases."""

        return tuple(
            (count, result)
            for count, result in (
                (1, self.single_phase_loads),
                (2, self.two_phase_loads),
                (3, self.three_phase_loads),
            )
            if result is not None
        )

    @property
    def _load_results(self) -> tuple[OpenDssLoadExportResult, ...]:
        return tuple(result for _, result in self.loads_by_phase_count)

    @property
    def generators_by_phase_count(
        self,
    ) -> tuple[tuple[int, OpenDssGeneratorExportResult], ...]:
        return tuple(
            (count, result)
            for count, result in (
                (1, self.single_phase_generators),
                (2, self.two_phase_generators),
                (3, self.three_phase_generators),
            )
            if result is not None
        )

    @property
    def _generator_results(self) -> tuple[OpenDssGeneratorExportResult, ...]:
        return tuple(result for _, result in self.generators_by_phase_count)

    @property
    def element_files(self) -> tuple[tuple[str, str], ...]:
        """Arquivos de elementos, na ordem em que o master os chama.

        ``reguladores.dss`` só entra quando há regulador exportado: um arquivo
        vazio mudaria o master de exportações que não têm reguladores.
        """

        regulators = self.regulators
        return (
            (LINES_FILENAME, self.lines.text),
            (SWITCHES_FILENAME, self.switches.text),
            *(
                ()
                if regulators is None or not regulators.exported_count
                else ((REGULATORS_FILENAME, regulators.text),)
            ),
            *(
                (_LOAD_FILES[count][0], result.text)
                for count, result in self.loads_by_phase_count
            ),
            *(
                (_GENERATOR_FILES[count][0], result.text)
                for count, result in self.generators_by_phase_count
            ),
        )

    @property
    def files(self) -> tuple[tuple[str, str], ...]:
        master = self.master
        if master is None or not master.text:
            return self.element_files
        return (
            *self.element_files,
            (master.master_filename, master.text),
            (master.buscoords_filename, master.buscoords_text),
        )

    @property
    def issues(self) -> tuple[OpenDssExportIssue, ...]:
        return (
            *self.lines.issues,
            *self.switches.issues,
            *(() if self.regulators is None else self.regulators.issues),
            *(issue for result in self._load_results for issue in result.issues),
            *(
                issue
                for result in self._generator_results
                for issue in result.issues
            ),
            *(() if self.master is None else self.master.issues),
        )

    @property
    def omitted_issues(self) -> int:
        total = self.lines.omitted_issues + self.switches.omitted_issues
        total += 0 if self.regulators is None else self.regulators.omitted_issues
        total += sum(result.omitted_issues for result in self._load_results)
        total += sum(result.omitted_issues for result in self._generator_results)
        return total + (0 if self.master is None else self.master.omitted_issues)

    @property
    def discarded_count(self) -> int:
        total = self.lines.discarded_count + self.switches.discarded_count
        total += 0 if self.regulators is None else self.regulators.discarded_count
        total += sum(result.discarded_count for result in self._load_results)
        total += sum(result.discarded_count for result in self._generator_results)
        return total + (
            0 if self.master is None else self.master.discarded_count
        )

    @property
    def has_warnings(self) -> bool:
        return (
            self.lines.has_warnings
            or self.switches.has_warnings
            or (self.regulators is not None and self.regulators.has_warnings)
            or any(result.has_warnings for result in self._load_results)
            or any(result.has_warnings for result in self._generator_results)
            or (self.master is not None and self.master.has_warnings)
        )


def sanitize_dss_name(value: str) -> str:
    """Reduz um código a um nome aceito pelo OpenDSS.

    O ponto separa nós de barra e o espaço separa propriedades, então nenhum dos
    dois pode sobreviver em um nome. Acentos são reduzidos a ASCII para o
    arquivo continuar legível por instalações que não leem UTF-8.
    """

    decomposed = unicodedata.normalize("NFKD", str(value))
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _INVALID_NAME_CHARS.sub("_", ascii_only).strip("_")


def parse_number(value: str) -> float | None:
    """Converte texto em número aceitando ponto **ou** vírgula decimal.

    Mesma regra dos demais módulos: os dois separadores juntos são ambíguos
    (separador de milhar) e por isso rejeitados.
    """

    text = str(value).strip()
    if not text or not _NUMBER_PATTERN.fullmatch(text):
        return None
    if "," in text:
        if "." in text:
            return None
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def phase_voltage_kv(nominal_voltage_kv: float) -> float:
    """Converte a tensão de linha do circuito (VNOM) em tensão de fase.

    Definição única reusada por ``C1`` dos trechos e pelo ``kV`` das cargas: as
    duas grandezas são de fase, e o circuito informa ``VNOM`` como tensão de
    linha.
    """

    if nominal_voltage_kv <= 0.0:
        raise ValueError("A tensão nominal deve ser positiva.")
    return nominal_voltage_kv / math.sqrt(3.0)


def positive_sequence_capacitance_nf(
    qcap_kvar_per_km: float,
    nominal_voltage_kv: float,
    *,
    frequency_hz: float = FREQUENCY_HZ,
) -> float:
    """Converte QCAP (kvar/km por fase) em C1 (nF/km).

    O capacitor é shunt, entre fase e neutro, logo a tensão da relação
    ``Q = 2·π·f·C·V²`` é a tensão de fase. Como ``nominal_voltage_kv`` é a
    tensão de linha, a conversão divide por ``√3`` antes de elevar ao quadrado.
    """

    if frequency_hz <= 0.0:
        raise ValueError("A frequência deve ser positiva.")
    phase_voltage_v = phase_voltage_kv(nominal_voltage_kv) * 1_000.0
    capacitance_f = (qcap_kvar_per_km * 1_000.0) / (
        2.0 * math.pi * frequency_hz * phase_voltage_v**2
    )
    return capacitance_f * 1e9


def _format(value: float) -> str:
    """Formata sem locale: o OpenDSS exige ponto decimal."""

    return f"{value:.6g}"


def _format_pattern(value: float) -> str:
    """Seis casas decimais: os patamares têm precisão excessiva na origem."""

    return f"{value:.6f}"


def _format_coordinate(value: float) -> str:
    """Casas fixas para as coordenadas UTM.

    O ``.6g`` de :func:`_format` guarda seis algarismos significativos, o que
    transformaria um northing de 8.000.000 em ``8e+06`` e truncaria as casas do
    easting. Milímetro é precisão de sobra para posicionar uma barra.
    """

    return f"{value:.3f}"


class _ExportReport:
    """Acumula ocorrências respeitando o teto de detalhamento."""

    __slots__ = ("issues", "discarded", "total")

    def __init__(self) -> None:
        self.issues: list[OpenDssExportIssue] = []
        self.discarded = 0
        self.total = 0

    def add(self, segment_id: str, reason: str, *, discarded: bool = True) -> None:
        """Registra uma ocorrência.

        ``discarded=False`` marca um aviso sobre um trecho que ainda assim foi
        exportado (código vazio, VNOM divergente entre circuitos donos).
        """

        self.total += 1
        if discarded:
            self.discarded += 1
        if len(self.issues) < MAX_REPORTED_ISSUES:
            self.issues.append(OpenDssExportIssue(segment_id, reason))

    @property
    def omitted(self) -> int:
        return max(0, self.total - len(self.issues))


def _entries_by_value(configuration: PhaseConfiguration) -> dict[str, object]:
    return {entry.fases2: entry for entry in configuration.entries}


def _phase_letter(name: str | None) -> str:
    """Primeira letra útil de um NOME, em maiúscula."""

    return (name or "").strip()[:1].upper()


def _terminals_by_phase_letter(
    configuration: PhaseConfiguration,
) -> dict[str, str]:
    """Terminal DSS de cada fase isolada, lido das entradas monofásicas.

    Toma o **primeiro nó** do ``DSS`` para tolerar tanto ``D`` (``"1"``) quanto
    ``DN`` (``"1.0"``, fase com neutro explícito); a primeira entrada de cada
    letra vence. O ``DSS`` da entrada bifásica não serve para isso: ele lista os
    nós em ordem crescente, não na ordem das letras do ``NOME`` — ``FD`` tem
    ``"1.3"``, então parear posicionalmente inverteria as duas fases.
    """

    terminals: dict[str, str] = {}
    for entry in configuration.entries:
        if entry.phase_count != 1 or not entry.dss:
            continue
        letter = _phase_letter(entry.name)
        if letter not in _PATTERN_COLUMNS_BY_PHASE or letter in terminals:
            continue
        node = entry.dss.split(".")[0].strip()
        if node:
            terminals[letter] = node
    return terminals


def phase_letters_by_node(
    configuration: PhaseConfiguration,
) -> dict[int, str]:
    """Letra da fase de cada nó DSS — o inverso de :func:`_terminals_by_phase_letter`.

    Público porque o painel de fluxo de potência rotula as colunas por fase e
    precisa da mesma correspondência que os ``Bus1``/``Bus2`` usam. Derivar do
    ``fases2.json`` em vez de fixar ``{1: "D", 2: "E", 3: "F"}`` evita uma
    segunda verdade que divergiria em silêncio de uma configuração customizada.
    """

    letters: dict[int, str] = {}
    for letter, node in _terminals_by_phase_letter(configuration).items():
        try:
            index = int(node)
        except ValueError:
            continue
        letters.setdefault(index, letter)
    return letters


def _phase_letters(name: str | None, expected: int) -> tuple[str, ...] | None:
    """As fases de um NOME, na ordem em que aparecem.

    Ignora letras fora de D/E/F, o que absorve o neutro de ``DN`` (→ ``D``) e de
    ``DEFN`` (→ ``D``, ``E``, ``F``) sem tratamento especial, e recusa qualquer
    NOME que não resolva exatamente ``expected`` fases distintas.
    """

    letters = tuple(
        char
        for char in (name or "").strip().upper()
        if char in _PATTERN_COLUMNS_BY_PHASE
    )
    if len(letters) != expected or len(set(letters)) != expected:
        return None
    return letters


def bus_namer(catalog: CircuitCatalogModel) -> Callable[[int], str]:
    """Resolve o nome da barra pelo CODIGO, com o BARRA_ID como reserva.

    Público porque é a **única** definição de "nome da barra no OpenDSS": os
    ``Bus1``/``Bus2`` dos trechos, o ``bus1`` das cargas, as coordenadas do
    ``Buscoords`` e a leitura dos resultados do fluxo de potência precisam
    concordar, e uma segunda definição divergiria em silêncio.
    """

    bars = catalog.segments.bars

    def bus_name(bar_index: int) -> str:
        code = sanitize_dss_name(bars.codes[bar_index])
        return code or sanitize_dss_name(bars.bar_ids[bar_index])

    return bus_name


def _selected_indices(
    catalog: CircuitCatalogModel,
    circuit_indices: Sequence[int] | Iterable[int],
) -> tuple[int, ...]:
    selected = tuple(int(index) for index in circuit_indices)
    for circuit_index in selected:
        if not 0 <= circuit_index < len(catalog):
            raise IndexError(circuit_index)
    return selected


def build_line_export(
    catalog: CircuitCatalogModel,
    cables: CableModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    skip_segments: frozenset[int] = frozenset(),
    include_segments: frozenset[int] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssLineExportResult:
    """Gera o conteúdo de ``trechos.dss`` para os circuitos selecionados.

    Percorre apenas ``common_segment_indices`` de cada associação: a busca
    topológica já separa ali os trechos que **não** representam chaves.

    ``skip_segments`` são os trechos já representados por outro elemento — hoje,
    os substituídos por um regulador (``replaced_segments`` de
    :func:`build_regulator_export`). Sair também como ``Line`` poria a linha em
    paralelo com o regulador. A omissão é silenciosa de propósito: quem
    substituiu o trecho já relatou a substituição.
    """

    selected = _selected_indices(catalog, circuit_indices)
    segments = catalog.segments
    entries_by_value = _entries_by_value(phase_configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)

    total = sum(
        int(catalog.membership(index).common_segment_indices.size)
        for index in selected
    )
    processed = 0
    lines: list[str] = []
    # Trecho sobreposto sai uma vez só: o primeiro circuito selecionado que o
    # contém é o dono e define a tensão usada em C1.
    owner_voltage: dict[int, tuple[str, float | None]] = {}
    used_names: dict[str, str] = {}
    exported_segments: list[tuple[str, int]] = []
    switch_segments: set[int] = set()

    for circuit_index in selected:
        definition = catalog.definition(circuit_index)
        membership = catalog.membership(circuit_index)
        switch_segments.update(int(value) for value in membership.switch_segment_indices)
        nominal_voltage = parse_number(definition.nominal_voltage)
        if nominal_voltage is not None and nominal_voltage <= 0.0:
            nominal_voltage = None

        for raw_index in membership.common_segment_indices:
            if cancel_check is not None and processed % 4_096 == 0 and cancel_check():
                raise InterruptedError("Exportação cancelada.")
            processed += 1
            if progress is not None and processed % 1_000 == 0:
                progress(processed, total)

            segment_index = int(raw_index)
            if include_segments is not None and segment_index not in include_segments:
                continue
            if segment_index in skip_segments:
                continue
            segment_id = segments.segment_ids[segment_index]
            previous = owner_voltage.get(segment_index)
            if previous is not None:
                owner_id, owner_kv = previous
                if owner_kv != nominal_voltage:
                    report.add(
                        segment_id,
                        f"trecho compartilhado com o circuito {definition.circuit_id}, "
                        f"de VNOM diferente; foi usada a do circuito {owner_id}",
                        discarded=False,
                    )
                continue
            owner_voltage[segment_index] = (definition.circuit_id, nominal_voltage)

            if nominal_voltage is None:
                report.add(
                    segment_id,
                    f"circuito {definition.circuit_id} sem VNOM numérica positiva "
                    f"({definition.nominal_voltage.strip() or '<vazio>'})",
                )
                continue

            record = segments.record(segment_index)
            entry = entries_by_value.get(record.phases.strip().casefold())
            if entry is None:
                report.add(
                    segment_id,
                    f"FASES2 '{record.phases.strip() or '<vazio>'}' sem relação "
                    "em fases2.json",
                )
                continue
            if not entry.dss:
                report.add(
                    segment_id,
                    f"FASES2 '{entry.fases2}' sem código DSS em fases2.json",
                )
                continue

            cable = cables.record_for_id(record.phase_cable_id)
            if cable is None:
                report.add(
                    segment_id,
                    f"CABOF_ID '{record.phase_cable_id.strip() or '<vazio>'}' "
                    "ausente do catálogo de cabos",
                )
                continue

            electrical: dict[str, float] = {}
            missing_field: str | None = None
            for field, raw in (
                ("R1", cable.r1),
                ("X1", cable.x1),
                ("R0", cable.r0),
                ("X0", cable.x0),
                ("QCAP", cable.qcap),
            ):
                parsed = parse_number(raw)
                if parsed is None:
                    missing_field = field
                    break
                electrical[field] = parsed
            if missing_field is not None:
                report.add(
                    segment_id,
                    f"cabo {cable.cable_id} sem {missing_field} numérico",
                )
                continue

            if record.length is None:
                report.add(segment_id, "COMPR ausente")
                continue

            name = sanitize_dss_name(record.code)
            if not name:
                name = sanitize_dss_name(segment_id)
                report.add(
                    segment_id,
                    "CODIGO vazio ou sem caracteres válidos; o nome da linha "
                    "usou o TRECHO_ID",
                    discarded=False,
                )
            if name in used_names:
                report.add(
                    segment_id,
                    f"nome '{name}' já usado pelo trecho {used_names[name]}",
                )
                continue

            capacitance = positive_sequence_capacitance_nf(
                electrical["QCAP"],
                nominal_voltage,
            )
            bus1 = bus_name(int(segments.start_indices[segment_index]))
            bus2 = bus_name(int(segments.end_indices[segment_index]))
            lines.append(
                f"New Line.{name}"
                f" Bus1={bus1}.{entry.dss}"
                f" Bus2={bus2}.{entry.dss}"
                f" Phases={entry.phase_count}"
                f" R1={_format(electrical['R1'])}"
                f" X1={_format(electrical['X1'])}"
                f" R0={_format(electrical['R0'])}"
                f" X0={_format(electrical['X0'])}"
                f" C1={_format(capacitance)}"
                f" C0={_format(capacitance)}"
                f" Length={_format(record.length / 1_000.0)}"
                " units=km"
            )
            used_names[name] = segment_id
            exported_segments.append((name, segment_index))

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Exportação cancelada.")
    if progress is not None:
        progress(total, total)

    header = (
        "! Trechos exportados pelo Visualizador de Circuitos Eletricos",
        "! R/X em ohm/km, C em nF/km, comprimento em km, "
        f"frequencia de {_format(FREQUENCY_HZ)} Hz",
        "! Circuitos: "
        + ", ".join(
            sanitize_dss_name(catalog.definition(index).circuit_id)
            for index in selected
        ),
        "",
    )
    text = "\n".join((*header, *lines))
    if lines:
        text += "\n"
    return OpenDssLineExportResult(
        text=text,
        exported_count=len(lines),
        skipped_switch_count=len(switch_segments),
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        used_names=frozenset(used_names),
        exported_segments=tuple(exported_segments),
    )


def build_switch_export(
    catalog: CircuitCatalogModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    reserved_names: frozenset[str] = frozenset(),
    include_segments: frozenset[int] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssSwitchExportResult:
    """Gera o conteúdo de ``chaves.dss`` para os circuitos selecionados.

    Percorre ``switch_segment_indices``, o complemento exato de
    ``common_segment_indices`` usado por :func:`build_line_export`.

    A chave não consome cabo, ``COMPR`` nem ``VNOM``: ``Switch=Yes`` sobrescreve
    todos os parâmetros elétricos da linha.
    """

    selected = _selected_indices(catalog, circuit_indices)
    segments = catalog.segments
    switches = catalog.switches
    entries_by_value = _entries_by_value(phase_configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)

    total = sum(
        int(catalog.membership(index).switch_segment_indices.size)
        for index in selected
    )
    processed = 0
    lines: list[str] = []
    open_commands: list[str] = []
    used_names: dict[str, str] = {}
    exported_segments: list[tuple[str, int]] = []
    seen_segments: set[int] = set()

    for circuit_index in selected:
        membership = catalog.membership(circuit_index)
        for raw_index in membership.switch_segment_indices:
            if cancel_check is not None and processed % 4_096 == 0 and cancel_check():
                raise InterruptedError("Exportação cancelada.")
            processed += 1
            if progress is not None and processed % 1_000 == 0:
                progress(processed, total)

            segment_index = int(raw_index)
            if include_segments is not None and segment_index not in include_segments:
                continue
            if segment_index in seen_segments:
                continue
            seen_segments.add(segment_index)
            segment_id = segments.segment_ids[segment_index]

            switch = (
                None
                if switches is None
                else switches.record_for_segment(segment_index)
            )
            if switch is None:
                report.add(segment_id, "trecho sem registro de chave")
                continue

            record = segments.record(segment_index)
            entry = entries_by_value.get(record.phases.strip().casefold())
            if entry is None:
                report.add(
                    segment_id,
                    f"FASES2 '{record.phases.strip() or '<vazio>'}' sem relação "
                    "em fases2.json",
                )
                continue
            if not entry.dss:
                report.add(
                    segment_id,
                    f"FASES2 '{entry.fases2}' sem código DSS em fases2.json",
                )
                continue

            name = sanitize_dss_name(switch.code)
            if not name:
                name = sanitize_dss_name(switch.switch_id)
                report.add(
                    segment_id,
                    "CODIGO da chave vazio ou sem caracteres válidos; o nome da "
                    "linha usou o CHAVE_ID",
                    discarded=False,
                )
            if name in reserved_names:
                report.add(
                    segment_id,
                    f"nome '{name}' já usado por um trecho em {LINES_FILENAME}",
                )
                continue
            if name in used_names:
                report.add(
                    segment_id,
                    f"nome '{name}' já usado pela chave {used_names[name]}",
                )
                continue

            bus1 = bus_name(int(segments.start_indices[segment_index]))
            bus2 = bus_name(int(segments.end_indices[segment_index]))
            # Switch=Yes é a ÚLTIMA propriedade de propósito: ele redefine
            # r1/x1/r0/x0/c1/c0/length, então qualquer parâmetro elétrico
            # escrito depois dele seria apagado pelo OpenDSS.
            lines.append(
                f"New Line.{name}"
                f" Bus1={bus1}.{entry.dss}"
                f" Bus2={bus2}.{entry.dss}"
                f" Phases={entry.phase_count}"
                " Switch=Yes"
            )
            used_names[name] = switch.switch_id
            exported_segments.append((name, segment_index))

            state = switch.state.strip()
            if state != CLOSED_SWITCH_STATE:
                if state not in {"0", CLOSED_SWITCH_STATE}:
                    report.add(
                        segment_id,
                        f"ESTADO '{state or '<vazio>'}' inválido; a chave foi "
                        "exportada como aberta",
                        discarded=False,
                    )
                open_commands.append(f"Open Line.{name} 1")

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Exportação cancelada.")
    if progress is not None:
        progress(total, total)

    header = (
        "! Chaves exportadas pelo Visualizador de Circuitos Eletricos",
        "! Switch=Yes define r1/x1/r0/x0/c1/c0 e length no OpenDSS",
        "! Os comandos Open no fim exigem o circuito ja definido",
        "! Circuitos: "
        + ", ".join(
            sanitize_dss_name(catalog.definition(index).circuit_id)
            for index in selected
        ),
        "",
    )
    # Todas as definições primeiro; só depois os comandos de abertura.
    body = (*lines, *(("",) if lines and open_commands else ()), *open_commands)
    text = "\n".join((*header, *body))
    if body:
        text += "\n"
    return OpenDssSwitchExportResult(
        text=text,
        exported_count=len(lines),
        open_count=len(open_commands),
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        used_names=frozenset(used_names),
        exported_segments=tuple(exported_segments),
    )


def build_regulator_export(
    catalog: CircuitCatalogModel,
    regulators: RegulatorModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    include_segments: frozenset[int] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssRegulatorExportResult:
    """Gera o conteúdo de ``reguladores.dss`` para os circuitos selecionados.

    Um regulador trifásico vira **três** transformadores monofásicos, um por
    fase, cada um com o seu ``RegControl``. As unidades monofásicas dividem a
    grandeza trifásica: ``kV = VNOM/√3`` (a mesma
    :func:`phase_voltage_kv` do ``C1`` dos trechos e do ``kV`` das cargas) e
    ``kVA = SNOM/3``.

    **O regulador ocupa o lugar do trecho.** Os transformadores ligam as duas
    barras do trecho, exatamente como a chave de :func:`build_switch_export`
    faz, e é por isso que o trecho não pode sair também como ``Line``: duas
    ligações entre as mesmas barras poriam a linha em paralelo com o regulador,
    curto-circuitando a injeção de tensão. Quem cumpre isso é
    ``replaced_segments``, consumido por :func:`build_line_export`.

    Só reguladores em trecho **trifásico** são exportados; os demais viram
    ocorrência e mantêm a linha do trecho.
    """

    selected = _selected_indices(catalog, circuit_indices)
    segments = catalog.segments
    entries_by_value = _entries_by_value(phase_configuration)
    terminals = _terminals_by_phase_letter(phase_configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)
    by_segment = regulators.record_indices_by_segment

    total = sum(
        int(catalog.membership(index).common_segment_indices.size)
        + int(catalog.membership(index).switch_segment_indices.size)
        for index in selected
    )
    processed = 0
    transformers: list[str] = []
    controls: list[str] = []
    exported_units: list[tuple[str, int, str]] = []
    used_names: dict[str, str] = {}
    replaced_segments: set[int] = set()
    seen_segments: set[int] = set()
    exported_count = 0

    for circuit_index in selected:
        definition = catalog.definition(circuit_index)
        membership = catalog.membership(circuit_index)
        circuit_voltage = parse_number(definition.nominal_voltage)
        if circuit_voltage is not None and circuit_voltage <= 0.0:
            circuit_voltage = None

        # Os trechos-chave entram só para o regulador que porventura esteja num
        # deles ser relatado: chave e regulador disputam a mesma Line, e um
        # regulador sumindo em silêncio é o que este laço evita.
        for raw_index in membership.switch_segment_indices:
            segment_index = int(raw_index)
            processed += 1
            if include_segments is not None and segment_index not in include_segments:
                continue
            if segment_index in seen_segments:
                continue
            record_index = int(by_segment[segment_index])
            if record_index < 0:
                continue
            seen_segments.add(segment_index)
            report.add(
                segments.segment_ids[segment_index],
                f"trecho já representa a chave do circuito "
                f"{definition.circuit_id}; o regulador "
                f"{regulators.record(record_index).regulator_id} não foi "
                "exportado",
            )

        for raw_index in membership.common_segment_indices:
            if cancel_check is not None and processed % 4_096 == 0 and cancel_check():
                raise InterruptedError("Exportação cancelada.")
            processed += 1
            if progress is not None and processed % 1_000 == 0:
                progress(processed, total)

            segment_index = int(raw_index)
            if include_segments is not None and segment_index not in include_segments:
                continue
            if segment_index in seen_segments:
                continue
            record_index = int(by_segment[segment_index])
            if record_index < 0:
                continue
            seen_segments.add(segment_index)
            segment_id = segments.segment_ids[segment_index]
            regulator = regulators.record(record_index)

            segment = segments.record(segment_index)
            entry = entries_by_value.get(segment.phases.strip().casefold())
            if entry is None:
                report.add(
                    segment_id,
                    f"FASES2 '{segment.phases.strip() or '<vazio>'}' sem relação "
                    "em fases2.json; o regulador não foi exportado",
                )
                continue
            if entry.phase_count != 3:
                report.add(
                    segment_id,
                    f"FASES2 '{entry.fases2}' não é trifásica; só reguladores "
                    "em trecho trifásico são exportados",
                )
                continue
            letters = _phase_letters(entry.name, 3)
            if letters is None:
                report.add(
                    segment_id,
                    f"FASES2 '{entry.fases2}' com NOME "
                    f"'{(entry.name or '').strip() or '<vazio>'}' não resolve "
                    "três fases distintas entre D, E e F",
                )
                continue
            missing = next(
                (letter for letter in letters if letter not in terminals),
                None,
            )
            if missing is not None:
                report.add(
                    segment_id,
                    f"fase '{missing}' sem terminal DSS: nenhuma entrada "
                    "monofásica de fases2.json a define",
                )
                continue

            voltage = parse_number(regulator.vnom)
            if voltage is None or voltage <= 0.0:
                report.add(
                    segment_id,
                    f"regulador {regulator.regulator_id} sem VNOM numérica "
                    f"positiva ({regulator.vnom.strip() or '<vazio>'})",
                )
                continue
            power = parse_number(regulator.snom)
            if power is None or power <= 0.0:
                report.add(
                    segment_id,
                    f"regulador {regulator.regulator_id} sem SNOM numérica "
                    f"positiva ({regulator.snom.strip() or '<vazio>'})",
                )
                continue
            # VNOM do regulador e do circuito são a mesma tensão de linha em kV.
            # Divergir muito quase sempre é unidade trocada, e o modelo
            # resultante seria aceito pelo OpenDSS e completamente errado.
            if circuit_voltage is not None and abs(
                voltage - circuit_voltage
            ) > REGULATOR_VOLTAGE_TOLERANCE * circuit_voltage:
                report.add(
                    segment_id,
                    f"VNOM '{regulator.vnom.strip()}' do regulador "
                    f"{regulator.regulator_id} incompatível com a VNOM "
                    f"{definition.nominal_voltage.strip()} do circuito "
                    f"{definition.circuit_id}; confira se a unidade é kV",
                )
                continue

            name = sanitize_dss_name(regulator.code)
            if not name:
                name = sanitize_dss_name(regulator.regulator_id)
                report.add(
                    segment_id,
                    "CODIGO do regulador vazio ou sem caracteres válidos; o "
                    "nome dos transformadores usou o REGU_ID",
                    discarded=False,
                )
            if name in used_names:
                report.add(
                    segment_id,
                    f"nome '{name}' já usado pelo regulador {used_names[name]}",
                )
                continue

            phase_kv = _format(phase_voltage_kv(voltage))
            phase_kva = _format(power / 3.0)
            vreg = REGULATOR_PT_SECONDARY_V / math.sqrt(3.0)
            band = REGULATOR_BAND_V
            ptratio = voltage * 1_000.0 / REGULATOR_PT_SECONDARY_V
            bus1 = bus_name(int(segments.start_indices[segment_index]))
            bus2 = bus_name(int(segments.end_indices[segment_index]))

            for letter in letters:
                node = terminals[letter]
                unit = f"{REGULATOR_NAME_PREFIX}{name}-{letter}"
                transformers.append(
                    f"New Transformer.{unit}"
                    " phases=1 windings=2"
                    f" XHL={_format(REGULATOR_XHL_PERCENT)}"
                    f" %LoadLoss={_format(REGULATOR_LOAD_LOSS_PERCENT)}"
                    f" Buses=[{bus1}.{node}.0, {bus2}.{node}.0]"
                    " conns=[wye, wye]"
                    f" kVs=[{phase_kv}, {phase_kv}]"
                    f" kVAs=[{phase_kva}, {phase_kva}]"
                )
                controls.append(
                    f"New RegControl."
                    f"{REGULATOR_CONTROL_NAME_PREFIX}{name}-{letter}"
                    f" transformer={unit}"
                    " winding=2"
                    f" vreg={_format(vreg)}"
                    f" band={_format(band)}"
                    f" ptratio={_format(ptratio)}"
                )
                exported_units.append((unit, segment_index, letter))

            used_names[name] = regulator.regulator_id
            replaced_segments.add(segment_index)
            exported_count += 1

            # O transformador não tem comprimento: a impedância do trecho que
            # ele substitui desaparece do modelo.
            length = segment.length
            if length is not None and length > REGULATOR_SEGMENT_LENGTH_WARNING_M:
                report.add(
                    segment_id,
                    f"trecho de {_format(length)} m substituído pelo regulador "
                    f"{regulator.regulator_id}; a impedância dele saiu do "
                    "modelo",
                    discarded=False,
                )

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Exportação cancelada.")
    if progress is not None:
        progress(total, total)

    header = (
        "! Reguladores exportados pelo Visualizador de Circuitos Eletricos",
        "! Um regulador trifasico = tres transformadores monofasicos + tres "
        "RegControl",
        "! kV = VNOM/raiz(3) e kVA = SNOM/3; o trecho regulado nao sai em "
        f"{LINES_FILENAME}",
        "! Circuitos: "
        + ", ".join(
            sanitize_dss_name(catalog.definition(index).circuit_id)
            for index in selected
        ),
        "",
    )
    # Todo RegControl referencia o seu Transformer pelo nome, então todas as
    # definições de transformador vêm antes de todos os controles.
    body = (
        *transformers,
        *(("",) if transformers and controls else ()),
        *controls,
    )
    text = "\n".join((*header, *body))
    if body:
        text += "\n"
    return OpenDssRegulatorExportResult(
        text=text,
        exported_count=exported_count,
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        used_names=frozenset(used_names),
        replaced_segments=frozenset(replaced_segments),
        exported_units=tuple(exported_units),
    )


def _bar_owners(
    catalog: CircuitCatalogModel,
    selected: tuple[int, ...],
) -> tuple[dict[int, tuple[str, float | None]], dict[int, str]]:
    """Resolve o circuito dono de cada barra dos circuitos selecionados.

    Mesma regra dos trechos sobrepostos: o primeiro circuito selecionado que
    contém a barra é o dono e define a VNOM usada. A divergência de VNOM é
    guardada à parte para só virar aviso quando uma carga de fato usar a barra.
    """

    owner_by_bar: dict[int, tuple[str, float | None]] = {}
    conflicting_bars: dict[int, str] = {}
    for circuit_index in selected:
        definition = catalog.definition(circuit_index)
        nominal_voltage = parse_number(definition.nominal_voltage)
        if nominal_voltage is not None and nominal_voltage <= 0.0:
            nominal_voltage = None
        for raw_index in catalog.membership(circuit_index).bar_indices:
            bar_index = int(raw_index)
            previous = owner_by_bar.get(bar_index)
            if previous is None:
                owner_by_bar[bar_index] = (definition.circuit_id, nominal_voltage)
            elif previous[1] != nominal_voltage and bar_index not in conflicting_bars:
                conflicting_bars[bar_index] = definition.circuit_id
    return owner_by_bar, conflicting_bars


def _phase_nodes(
    entry,  # noqa: ANN001
    letters: tuple[str, ...],
    terminals: dict[str, str],
) -> tuple[tuple[str, ...] | None, str | None]:
    """Nó DSS de cada fase da carga, ou a razão de não ser possível resolvê-lo.

    A monofásica usa o ``DSS`` da **própria entrada**, o que preserva o nó de
    neutro explícito de ``DN``/``EN``/``FN`` (``bus.1.0``). As multifásicas usam
    o terminal da fase isolada, porque o ``DSS`` delas lista os nós em ordem
    crescente e não na ordem das letras do ``NOME`` — ``FD`` tem ``"1.3"``,
    então parear posicionalmente inverteria as fases.
    """

    if len(letters) == 1:
        if not entry.dss:
            return None, (
                f"FASES2 '{entry.fases2}' sem código DSS em fases2.json"
            )
        return (entry.dss,), None
    missing = next(
        (letter for letter in letters if letter not in terminals),
        None,
    )
    if missing is not None:
        return None, (
            f"fase '{missing}' sem terminal DSS: nenhuma entrada monofásica "
            "de fases2.json a define"
        )
    return tuple(terminals[letter] for letter in letters), None


def build_load_export(
    catalog: CircuitCatalogModel,
    loads: LoadModel,
    patterns: LoadPatternModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    phase_count: int,
    reserved_names: frozenset[str] = frozenset(),
    include_load_indices: frozenset[int] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssLoadExportResult:
    """Gera o arquivo de cargas de ``phase_count`` fases.

    Cada carga vira **uma ``Load`` monofásica por fase**, com seu próprio
    ``LoadShape`` diário. Para as multifásicas é o que preserva o desequilíbrio:
    uma única ``Load`` de ``phases=2`` ou ``3`` distribuiria a potência
    igualmente entre as fases, apagando exatamente o que os patamares por fase
    (``PD``/``PE``/``PF``) descrevem. As monofásicas seguem a mesma forma por
    uniformidade, com uma fase só.

    O nome de cada ``Load`` é ``<CODIGO>-<N>F-<FASE>``, e o perfil acompanha com
    o prefixo ``PERFIL-``. ``kW=1 kvar=1`` são fixos: a potência real de cada
    patamar vive no perfil.

    ``CircuitMembership`` não associa cargas, apenas barras, então a carga é
    atribuída ao circuito pela barra em que está pendurada.

    Uma carga sai completa ou não sai: qualquer problema em uma das fases
    descarta todas, porque carga pela metade subestimaria a demanda em silêncio.
    """

    if phase_count not in _LOAD_FILES:
        raise ValueError(f"Contagem de fases sem arquivo: {phase_count}")

    selected = _selected_indices(catalog, circuit_indices)
    entries_by_value = _entries_by_value(phase_configuration)
    terminals = _terminals_by_phase_letter(phase_configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)
    owner_by_bar, conflicting_bars = _bar_owners(catalog, selected)

    total = len(loads)
    processed = 0
    shapes: list[str] = []
    entries: list[str] = []
    used_names: dict[str, str] = {}
    exported = 0
    skipped_other_phase = 0

    for load_index in range(total):
        if cancel_check is not None and processed % 4_096 == 0 and cancel_check():
            raise InterruptedError("Exportação cancelada.")
        processed += 1
        if progress is not None and processed % 1_000 == 0:
            progress(processed, total)

        if include_load_indices is not None and load_index not in include_load_indices:
            continue

        bar_index = int(loads.bar_indices[load_index])
        owner = owner_by_bar.get(bar_index)
        if owner is None:
            # A carga está fora dos circuitos selecionados.
            continue
        owner_id, nominal_voltage = owner
        load_id = loads.load_ids[load_index]

        raw_phases = loads.phases[load_index]
        entry = entries_by_value.get(raw_phases.strip().casefold())
        if entry is None:
            report.add(
                load_id,
                f"FASES2 '{raw_phases.strip() or '<vazio>'}' sem relação "
                "em fases2.json",
            )
            continue
        if entry.phase_count != phase_count:
            # Cargas de outra contagem pertencem a outro arquivo: contadas, não
            # diagnosticadas.
            skipped_other_phase += 1
            continue

        letters = _phase_letters(entry.name, phase_count)
        if letters is None:
            report.add(
                load_id,
                f"FASES2 '{entry.fases2}' com NOME "
                f"'{(entry.name or '').strip() or '<vazio>'}' não resolve "
                f"{phase_count} fase(s) distinta(s) entre D, E e F",
            )
            continue

        nodes, node_error = _phase_nodes(entry, letters, terminals)
        if node_error is not None:
            report.add(load_id, node_error)
            continue

        if nominal_voltage is None:
            report.add(
                load_id,
                f"circuito {owner_id} sem VNOM numérica positiva",
            )
            continue

        group = patterns.records_for_load(load_index)
        if len(group) != LOAD_PATTERN_COUNT:
            report.add(load_id, "sem os quatro patamares (NPAT 0 a 3)")
            continue

        # Todas as fases são resolvidas por inteiro antes de qualquer emissão:
        # zero é valor válido, só vazio e não numérico invalidam.
        series: list[tuple[list[float], list[float]]] = []
        invalid_column: str | None = None
        for letter in letters:
            active_column, reactive_column = _PATTERN_COLUMNS_BY_PHASE[letter]
            active: list[float] = []
            reactive: list[float] = []
            for record in group:
                for column, target in (
                    (active_column, active),
                    (reactive_column, reactive),
                ):
                    parsed = parse_number(getattr(record, column))
                    if parsed is None:
                        invalid_column = column.upper()
                        break
                    target.append(parsed)
                if invalid_column is not None:
                    break
            if invalid_column is not None:
                break
            series.append((active, reactive))
        if invalid_column is not None:
            report.add(
                load_id,
                f"patamar com {invalid_column} não numérico; a carga inteira "
                "foi descartada",
            )
            continue

        base_name = sanitize_dss_name(loads.codes[load_index])
        if not base_name:
            base_name = sanitize_dss_name(load_id)
            report.add(
                load_id,
                "CODIGO vazio ou sem caracteres válidos; o nome da carga usou "
                "o CARGA_ID",
                discarded=False,
            )
        phase_names = [
            f"{base_name}-{phase_count}F-{letter}" for letter in letters
        ]
        taken = next(
            (
                name
                for name in phase_names
                if name in reserved_names or name in used_names
            ),
            None,
        )
        if taken is not None:
            owner_note = (
                f"pela carga {used_names[taken]}"
                if taken in used_names
                else "por uma carga de outra contagem de fases"
            )
            report.add(
                load_id,
                f"nome '{taken}' já usado {owner_note}; a carga inteira foi "
                "descartada",
            )
            continue

        if bar_index in conflicting_bars:
            report.add(
                load_id,
                f"barra compartilhada com o circuito {conflicting_bars[bar_index]}, "
                f"de VNOM diferente; foi usada a do circuito {owner_id}",
                discarded=False,
            )

        bus = bus_name(bar_index)
        voltage = _format(phase_voltage_kv(nominal_voltage))
        for name, node, (active, reactive) in zip(phase_names, nodes, series):
            shape_name = f"{LOAD_SHAPE_PREFIX}{name}"
            shapes.append(
                f"New LoadShape.{shape_name}"
                f" npts={LOAD_PATTERN_COUNT}"
                " interval=1"
                f" mult=[{' '.join(_format_pattern(value) for value in active)}]"
                f" qmult=[{' '.join(_format_pattern(value) for value in reactive)}]"
            )
            entries.append(
                f"New Load.{name}"
                " phases=1"
                f" bus1={bus}.{node}"
                " conn=wye"
                f" kV={voltage}"
                " model=1 kW=1 kvar=1"
                f" daily={shape_name}"
                f" class={phase_count}"
            )
            used_names[name] = load_id
        exported += 1

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Exportação cancelada.")
    if progress is not None:
        progress(total, total)

    label = _LOAD_FILES[phase_count][1]
    header = (
        f"! Cargas {label} exportadas pelo Visualizador de Circuitos Eletricos",
        *(
            (
                f"! Cada carga vira {phase_count} Load monofasicas, uma por "
                "fase, para preservar o desequilibrio",
            )
            if phase_count > 1
            else ()
        ),
        "! kW=1 e kvar=1 sao fixos: a potencia de cada patamar vem do LoadShape",
        "! kV e a tensao de fase do circuito (VNOM de linha dividida por raiz de 3)",
        "! Circuitos: "
        + ", ".join(
            sanitize_dss_name(catalog.definition(index).circuit_id)
            for index in selected
        ),
        "",
    )
    # Os LoadShape vêm antes das Load: o daily= referencia um perfil que o
    # OpenDSS precisa já ter definido.
    body = (*shapes, *(("",) if shapes and entries else ()), *entries)
    text = "\n".join((*header, *body))
    if body:
        text += "\n"
    return OpenDssLoadExportResult(
        text=text,
        exported_count=exported,
        skipped_other_phase_count=skipped_other_phase,
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        used_names=frozenset(used_names),
    )


def build_generator_export(
    catalog: CircuitCatalogModel,
    updates: GeneratorUpdateModel,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    phase_count: int,
    reserved_names: frozenset[str] = frozenset(),
    include_generator_indices: frozenset[int] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssGeneratorExportResult:
    """Gera geradores como ``Load`` monofásicas com potência ativa negativa.

    O resultado de ``Atualizar Geradores`` já contém as quatro potências de
    cada fase com a convenção consumo positivo/geração negativa. O exportador
    apenas as transporta ao ``LoadShape``; inverter novamente faria o gerador
    voltar a se comportar como carga.
    """

    if phase_count not in _GENERATOR_FILES:
        raise ValueError(f"Contagem de fases sem arquivo: {phase_count}")
    if updates.circuits is not catalog:
        raise ValueError("Os resultados dos geradores pertencem a outros circuitos.")

    selected = frozenset(_selected_indices(catalog, circuit_indices))
    generators = updates.generators
    configuration = updates.phase_configuration
    entries_by_value = _entries_by_value(configuration)
    terminals = _terminals_by_phase_letter(configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)
    total = len(generators)
    processed = 0
    shapes: list[str] = []
    entries: list[str] = []
    used_names: dict[str, str] = {}
    exported = 0
    skipped_other_phase = 0

    for generator_index in range(total):
        if cancel_check is not None and processed % 4_096 == 0 and cancel_check():
            raise InterruptedError("Exportação cancelada.")
        processed += 1
        if progress is not None and processed % 1_000 == 0:
            progress(processed, total)

        if (
            include_generator_indices is not None
            and generator_index not in include_generator_indices
        ):
            continue

        circuit_index = updates.circuit_indices[generator_index]
        powers = updates.phase_power_records_for_generator(generator_index)
        # Geradores omitidos durante a atualização não são candidatos à
        # exportação. O relatório da atualização já contém a causa original.
        if circuit_index is None or not powers or circuit_index not in selected:
            continue

        record = generators.record(generator_index)
        raw_phases = record.phases
        entry = entries_by_value.get(raw_phases.strip().casefold())
        if entry is None:
            report.add(
                record.generator_id,
                f"FASES2 '{raw_phases.strip() or '<vazio>'}' sem relação "
                "em fases2.json",
            )
            continue
        if entry.phase_count != phase_count:
            skipped_other_phase += 1
            continue

        letters = _phase_letters(entry.name, phase_count)
        if letters is None:
            report.add(
                record.generator_id,
                f"FASES2 '{entry.fases2}' com NOME "
                f"'{(entry.name or '').strip() or '<vazio>'}' não resolve "
                f"{phase_count} fase(s) distinta(s) entre D, E e F",
            )
            continue
        nodes, node_error = _phase_nodes(entry, letters, terminals)
        if node_error is not None:
            report.add(record.generator_id, node_error)
            continue

        definition = catalog.definition(circuit_index)
        nominal_voltage = parse_number(definition.nominal_voltage)
        if nominal_voltage is None or nominal_voltage <= 0.0:
            report.add(
                record.generator_id,
                f"circuito {definition.circuit_id} sem VNOM numérica positiva",
            )
            continue

        base_value = sanitize_dss_name(record.generator_code)
        if not base_value:
            base_value = sanitize_dss_name(record.generator_id)
            report.add(
                record.generator_id,
                "CODIGO vazio ou sem caracteres válidos; o nome do gerador "
                "usou o GERADOR_ID",
                discarded=False,
            )
        if not base_value:
            report.add(
                record.generator_id,
                "GERADOR_ID não contém caracteres válidos para um nome OpenDSS",
            )
            continue
        base_name = f"GER-{base_value}"
        phase_names = [
            f"{base_name}-{phase_count}F-{letter}" for letter in letters
        ]
        taken = next(
            (
                name
                for name in phase_names
                if name in reserved_names or name in used_names
            ),
            None,
        )
        if taken is not None:
            owner_note = (
                f"pelo gerador {used_names[taken]}"
                if taken in used_names
                else "por uma carga ou gerador de outro arquivo"
            )
            report.add(
                record.generator_id,
                f"nome '{taken}' já usado {owner_note}; o gerador inteiro foi "
                "descartado",
            )
            continue

        series: list[tuple[list[float], list[float]]] = []
        for letter in letters:
            active_column, reactive_column = _PATTERN_COLUMNS_BY_PHASE[letter]
            series.append(
                (
                    [float(getattr(item, active_column)) for item in powers],
                    [float(getattr(item, reactive_column)) for item in powers],
                )
            )

        bus = bus_name(int(generators.bar_indices[generator_index]))
        voltage = _format(phase_voltage_kv(nominal_voltage))
        for name, node, (active, reactive) in zip(
            phase_names, nodes, series, strict=True
        ):
            shape_name = f"{LOAD_SHAPE_PREFIX}{name}"
            shapes.append(
                f"New LoadShape.{shape_name}"
                f" npts={LOAD_PATTERN_COUNT}"
                " interval=1"
                f" mult=[{' '.join(_format_pattern(value) for value in active)}]"
                f" qmult=[{' '.join(_format_pattern(value) for value in reactive)}]"
            )
            entries.append(
                f"New Load.{name}"
                " phases=1"
                f" bus1={bus}.{node}"
                " conn=wye"
                f" kV={voltage}"
                " model=1 kW=1 kvar=1"
                f" daily={shape_name}"
                f" class={-phase_count}"
            )
            used_names[name] = record.generator_id
        exported += 1

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Exportação cancelada.")
    if progress is not None:
        progress(total, total)

    label = _GENERATOR_FILES[phase_count][1]
    header = (
        f"! Geradores {label} exportados pelo Visualizador de Circuitos Eletricos",
        *(
            (
                f"! Cada gerador vira {phase_count} Load monofasicas, uma por "
                "fase, para preservar o desequilibrio",
            )
            if phase_count > 1
            else ()
        ),
        "! Potencia ativa negativa representa geracao; qmult permanece zerado",
        "! kW=1 e kvar=1 sao fixos: a potencia de cada patamar vem do LoadShape",
        "! kV e a tensao de fase do circuito (VNOM de linha dividida por raiz de 3)",
        "! Circuitos: "
        + ", ".join(
            sanitize_dss_name(catalog.definition(index).circuit_id)
            for index in sorted(selected)
        ),
        "",
    )
    body = (*shapes, *(("",) if shapes and entries else ()), *entries)
    text = "\n".join((*header, *body))
    if body:
        text += "\n"
    return OpenDssGeneratorExportResult(
        text=text,
        exported_count=exported,
        skipped_other_phase_count=skipped_other_phase,
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        used_names=frozenset(used_names),
    )

def _master_base_name(definition) -> str:  # noqa: ANN001
    """Nome do circuito no OpenDSS, com o CIRC_ID de reserva."""

    return sanitize_dss_name(definition.code) or sanitize_dss_name(
        definition.circuit_id
    )


def master_filenames(
    catalog: CircuitCatalogModel,
    circuit_indices: Sequence[int] | Iterable[int],
) -> tuple[str, str] | None:
    """Nomes do master e das coordenadas, ou ``None`` sem master a gerar.

    Público para a UI montar a confirmação de substituição **antes** de
    exportar, sem precisar rodar a exportação inteira.
    """

    selected = _selected_indices(catalog, circuit_indices)
    if len(selected) != 1:
        return None
    base = _master_base_name(catalog.definition(selected[0]))
    return (
        f"{base}{MASTER_FILENAME_SUFFIX}",
        f"{base}{BUSCOORDS_FILENAME_SUFFIX}",
    )


def _empty_master(
    report: _ExportReport,
    master_filename: str,
    buscoords_filename: str,
) -> OpenDssMasterExportResult:
    """Resultado sem master, carregando só o motivo."""

    return OpenDssMasterExportResult(
        master_filename=master_filename,
        text="",
        buscoords_filename=buscoords_filename,
        buscoords_text="",
        bus_count=0,
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
    )


def build_master_export(
    catalog: CircuitCatalogModel,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    redirects: Sequence[str] = (),
    load_settings: OpenDssLoadSettings | None = None,
    include_bar_indices: frozenset[int] | None = None,
) -> OpenDssMasterExportResult:
    """Gera o arquivo principal e o CSV de coordenadas de barra.

    O master cria o circuito, chama os arquivos de elementos e resolve. A ordem
    das seções não é estética: ``Set DefaultBaseFrequency`` precede o
    ``New Circuit`` porque a frequência base é fixada na criação do circuito, e
    os ``Redirect`` precedem o ``calcvoltagebases`` porque este precisa de todas
    as barras já definidas.

    ``load_settings`` traz os parâmetros globais das cargas escolhidos pelo
    usuário. Os ``BatchEdit`` que ele gera entram **depois** dos ``Redirect``,
    porque são comandos executivos e exigem as ``Load`` já definidas — mesma
    disciplina dos ``Open`` de ``chaves.dss``. Sem configuração, nenhuma linha é
    acrescentada e o arquivo permanece idêntico.

    Um ``New Circuit`` energiza um alimentador só, então o master exige
    **exatamente um** circuito selecionado. Com vários, o correto seria somar um
    ``Vsource`` por alimentador adicional — fica para quando a exportação
    múltipla for tratada.

    ``text`` vazio indica que o master não pôde ser montado; o motivo está nos
    ``issues``.
    """

    selected = _selected_indices(catalog, circuit_indices)
    report = _ExportReport()
    if len(selected) != 1:
        report.add(
            "master",
            f"{len(selected)} circuitos selecionados; o master exige "
            "exatamente um, porque um New Circuit energiza um alimentador só",
        )
        return _empty_master(report, "", "")

    definition = catalog.definition(selected[0])
    base = _master_base_name(definition)
    master_filename = f"{base}{MASTER_FILENAME_SUFFIX}"
    buscoords_filename = f"{base}{BUSCOORDS_FILENAME_SUFFIX}"
    if not sanitize_dss_name(definition.code):
        report.add(
            definition.circuit_id,
            "CODIGO vazio ou sem caracteres válidos; o nome do circuito usou "
            "o CIRC_ID",
            discarded=False,
        )

    nominal_voltage = parse_number(definition.nominal_voltage)
    if nominal_voltage is None or nominal_voltage <= 0.0:
        report.add(
            definition.circuit_id,
            f"circuito sem VNOM numérica positiva "
            f"({definition.nominal_voltage.strip() or '<vazio>'}); o master "
            "não foi gerado",
        )
        return _empty_master(report, master_filename, buscoords_filename)

    bars = catalog.segments.bars
    bus_name = bus_namer(catalog)
    # O construtor do catálogo já garante que a barra raiz existe.
    root_index = bars.index_for_id(definition.root_bar_id)
    voltage = _format(nominal_voltage)

    lines = [
        "Clear",
        f"Set DefaultBaseFrequency={_format(FREQUENCY_HZ)}",
        "",
        f"New Circuit.{base}",
        f"~ bus1={bus_name(int(root_index))}.1.2.3 phases=3"
        f" basekv={voltage} pu=1 angle=0 frequency={_format(FREQUENCY_HZ)}",
        f"~ MVAsc3={SOURCE_SHORT_CIRCUIT_MVA} MVAsc1={SOURCE_SHORT_CIRCUIT_MVA}",
        "",
    ]
    lines.extend(f"Redirect {name}" for name in redirects)
    if redirects:
        lines.append("")
    batch_edits = (
        () if load_settings is None else load_settings.batch_edit_commands()
    )
    if batch_edits:
        lines.extend(batch_edits)
        lines.append("")
    lines.extend(
        [
            f"Set Voltagebases=[{voltage}]",
            "calcvoltagebases",
            "Set mode=daily",
            "Set stepsize=1h",
            # Um passo por patamar: o LoadShape tem npts=4 e interval=1 hora.
            f"Set number={LOAD_PATTERN_COUNT}",
            "Set time=(0, 0)",
            "Solve",
            "",
            f"Buscoords {buscoords_filename}",
            "",
        ]
    )

    # As coordenadas usam o mesmo nome de barra dos Bus1/Bus2; é isso que faz o
    # OpenDSS casar cada ponto com o elemento correspondente.
    coordinates: list[str] = []
    seen_names: dict[str, str] = {}
    for raw_index in catalog.membership(selected[0]).bar_indices:
        bar_index = int(raw_index)
        if include_bar_indices is not None and bar_index not in include_bar_indices:
            continue
        name = bus_name(bar_index)
        bar_id = bars.bar_ids[bar_index]
        if name in seen_names:
            report.add(
                bar_id,
                f"nome de barra '{name}' já usado pela barra "
                f"{seen_names[name]}; a coordenada foi descartada",
            )
            continue
        seen_names[name] = bar_id
        coordinates.append(
            f"{name},{_format_coordinate(float(bars.x[bar_index]))},"
            f"{_format_coordinate(float(bars.y[bar_index]))}"
        )

    return OpenDssMasterExportResult(
        master_filename=master_filename,
        text="\n".join(lines),
        buscoords_filename=buscoords_filename,
        buscoords_text="\n".join((*coordinates, "")),
        bus_count=len(coordinates),
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
    )


def build_export(
    catalog: CircuitCatalogModel,
    cables: CableModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    loads: LoadModel | None = None,
    patterns: LoadPatternModel | None = None,
    generator_updates: GeneratorUpdateModel | None = None,
    regulators: RegulatorModel | None = None,
    load_settings: OpenDssLoadSettings | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssExportBundle:
    """Monta todos os arquivos de uma exportação.

    Arquivos que compartilham namespace no OpenDSS reservam nomes entre si:
    trechos e chaves em ``Line.*``; cargas e geradores em ``Load.*``.
    Sem a reserva, a segunda definição sobrescreveria a primeira em silêncio.
    Entre os arquivos de carga o infixo ``-1F-``/``-2F-``/``-3F-`` já torna a
    coincidência impossível na prática; a reserva permanece porque essa
    unicidade é propriedade do esquema de nomes, não invariante imposta.

    Os arquivos de carga só saem com cargas **e** patamares: sem os quatro NPAT
    não há ``LoadShape`` para o ``daily`` das cargas apontar. Quando saem, saem
    os três, mesmo que algum fique só com o cabeçalho — assim a lista de
    arquivos gerados não depende do conteúdo do CSV.

    Os arquivos de geradores só saem com um resultado vigente de sua
    atualização. Quando saem, também saem os três e usam diretamente a potência
    por fase já sinalizada no resultado.

    ``load_settings`` só chega ao master quando há algum ``Load`` a editar. A
    DLL trata ``BatchEdit`` sem alvo como no-op (``Elements edited: 0``), mas um
    comando que edita zero objetos num arquivo sem cargas ou geradores só
    confundiria quem o lesse. A divisão de responsabilidade é essa:
    ``build_master_export`` emite o
    que recebe, e é aqui que se decide se faz sentido.

    Os reguladores são construídos **antes** dos trechos porque cada regulador
    exportado toma o lugar da ``Line`` do seu trecho, e só depois de tentar
    exportá-los se sabe quais trechos não devem sair — um regulador descartado
    mantém a linha dele.
    """

    selected = _selected_indices(catalog, circuit_indices)
    regulator_result = (
        None
        if regulators is None
        else build_regulator_export(
            catalog,
            regulators,
            phase_configuration,
            selected,
            cancel_check=cancel_check,
            progress=progress,
        )
    )
    line_result = build_line_export(
        catalog,
        cables,
        phase_configuration,
        selected,
        skip_segments=(
            frozenset()
            if regulator_result is None
            else regulator_result.replaced_segments
        ),
        cancel_check=cancel_check,
        progress=progress,
    )
    switch_result = build_switch_export(
        catalog,
        phase_configuration,
        selected,
        reserved_names=line_result.used_names,
        cancel_check=cancel_check,
        progress=progress,
    )
    load_results: dict[int, OpenDssLoadExportResult] = {}
    # A identidade amarra os patamares às cargas exibidas, como no resto do
    # projeto: modelos de importações diferentes nunca se combinam.
    if loads is not None and patterns is not None and patterns.loads is loads:
        reserved: frozenset[str] = frozenset()
        for count in LOAD_PHASE_COUNTS:
            result = build_load_export(
                catalog,
                loads,
                patterns,
                phase_configuration,
                selected,
                phase_count=count,
                reserved_names=reserved,
                cancel_check=cancel_check,
                progress=progress,
            )
            reserved |= result.used_names
            load_results[count] = result
    generator_results: dict[int, OpenDssGeneratorExportResult] = {}
    if generator_updates is not None:
        if generator_updates.circuits is not catalog:
            raise ValueError(
                "Os resultados dos geradores pertencem a outros circuitos."
            )
        if generator_updates.phase_configuration is not phase_configuration:
            raise ValueError(
                "Os resultados dos geradores usam outra configuração de fases."
            )
        # ``Load.*`` é um namespace único no OpenDSS. As cargas têm prioridade
        # por compatibilidade, e cada arquivo de gerador reserva os nomes para
        # os seguintes.
        reserved = frozenset(
            name
            for result in load_results.values()
            for name in result.used_names
        )
        for count in GENERATOR_PHASE_COUNTS:
            result = build_generator_export(
                catalog,
                generator_updates,
                selected,
                phase_count=count,
                reserved_names=reserved,
                cancel_check=cancel_check,
                progress=progress,
            )
            reserved |= result.used_names
            generator_results[count] = result
    bundle = OpenDssExportBundle(
        lines=line_result,
        switches=switch_result,
        regulators=regulator_result,
        single_phase_loads=load_results.get(1),
        two_phase_loads=load_results.get(2),
        three_phase_loads=load_results.get(3),
        single_phase_generators=generator_results.get(1),
        two_phase_generators=generator_results.get(2),
        three_phase_generators=generator_results.get(3),
    )
    # O master vem por último: ele chama os arquivos de elementos e por isso
    # precisa saber quais existem.
    master_result = build_master_export(
        catalog,
        selected,
        redirects=[name for name, _ in bundle.element_files],
        load_settings=(
            load_settings if load_results or generator_results else None
        ),
    )
    return OpenDssExportBundle(
        lines=line_result,
        switches=switch_result,
        regulators=regulator_result,
        single_phase_loads=load_results.get(1),
        two_phase_loads=load_results.get(2),
        three_phase_loads=load_results.get(3),
        single_phase_generators=generator_results.get(1),
        two_phase_generators=generator_results.get(2),
        three_phase_generators=generator_results.get(3),
        master=master_result,
    )
