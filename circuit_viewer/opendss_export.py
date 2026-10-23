"""Exportação da rede como elementos do OpenDSS.

Camada de núcleo: não importa Qt, para poder ser testada headless e executada
em thread secundária sem cuidados de afinidade.

Dois arquivos de rede são sempre gerados — ``trechos.dss`` (``Line``) e
``chaves.dss`` (``Line ... Switch=Yes``). No modo de bibliotecas, eles são
precedidos por ``cabos.dss``, ``arranjos.dss`` e ``geometria_linhas.dss``.
Cargas e geradores acrescentam, cada um, um arquivo por contagem de fases
(``Load`` + ``LoadShape``). O par
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
cada ``Load`` é ligada entre fase e neutro (``conn=wye``). A exceção são as
cargas que o Interplan liga **entre fases**, decididas pelo FASES2 da CARGA na
tabela de ``TCarga::FasesMT`` (``fases2_carga.json``, ``LIGACAO`` delta): a
monofásica 7/8/9 (PD em D-E, PE em E-F, PF em F-D) e a trifásica 13. Elas
saem com ``conn=delta`` e a tensão de linha.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

from .demand_correction import (
    PATAMAR_COUNT,
    DemandCorrections,
    DemandScope,
    correction_for,
    energy_scope_for_kind,
)
from .dss_names import sanitize_dss_name
from .model import (
    CLOSED_SWITCH_STATE,
    CableModel,
    CapacitorModel,
    CircuitCatalogModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
    RegulatorModel,
    RegulatorRecord,
)
from .opendss_automatic_assembly import (
    AutomaticAssembly,
    build_automatic_assemblies,
)
from .opendss_library import (
    OPEN_DSS_UNITS,
    ArrangementDefinition,
    CableDefinition,
    OpenDssLibraryCatalog,
    cable_issues,
    coincident_positions,
)
from .opendss_line_mode import OpenDssLineParameterMode
from .opendss_mapping_store import OpenDssLibraryMappings
from .opendss_regulator_settings import (
    DEFAULT_OPENDSS_REGULATOR_SETTINGS,
    DEFAULT_REGULATION_RANGE,
    DEFAULT_STEP_COUNT,
    OpenDssRegulatorSettings,
    effective_band_pu,
    is_valid_vreg,
)
from .opendss_solution import (
    DEFAULT_MAX_POWER_FLOW_ITER,
    parse_max_power_flow_iterations,
)
from .phase_config import PhaseConfiguration

if TYPE_CHECKING:
    # Só para tipagem: em tempo de execução o import seria circular, porque
    # opendss_settings reusa o parse_number deste módulo — de propósito, para
    # não existirem duas regras de leitura de separador decimal no projeto.
    from .opendss_settings import OpenDssLoadSettings
    from .generator_update import GeneratorUpdateModel
    from .energy_demand import EnergyDemandModel


FREQUENCY_HZ = 60.0
LINES_FILENAME = "trechos.dss"
SWITCHES_FILENAME = "chaves.dss"
REGULATORS_FILENAME = "reguladores.dss"
CABOS_FILENAME = "cabos.dss"
ARRANGEMENTS_FILENAME = "arranjos.dss"
LINE_GEOMETRIES_FILENAME = "geometria_linhas.dss"
SINGLE_PHASE_LOADS_FILENAME = "cargasmonofasicas.dss"
TWO_PHASE_LOADS_FILENAME = "cargasbifasicas.dss"
THREE_PHASE_LOADS_FILENAME = "cargastrifasicas.dss"
SINGLE_PHASE_GENERATORS_FILENAME = "geradoresmonofasicos.dss"
TWO_PHASE_GENERATORS_FILENAME = "geradoresbifasicos.dss"
THREE_PHASE_GENERATORS_FILENAME = "geradorestrifasicos.dss"
# Arquivo único: bancos de capacitores são poucos e não se dividem por
# contagem de fases como cargas e geradores.
CAPACITORS_FILENAME = "capacitores.dss"
# O master e as coordenadas levam o código do circuito no nome, então só o
# sufixo é constante.
MASTER_FILENAME_SUFFIX = "_Master.dss"
BUSCOORDS_FILENAME_SUFFIX = "_Buscoords.csv"
# Barra infinita: o estudo de alimentador não modela a montante da subestação.
SOURCE_SHORT_CIRCUIT_MVA = "999999"
MAX_REPORTED_ISSUES = 200
LOAD_SHAPE_PREFIX = "PERFIL-"
# NPAT 0..3: o LoadShape diário tem exatamente quatro pontos.
LOAD_PATTERN_COUNT = 4
# A correção de demanda tem um par de fatores por ponto do LoadShape.
assert LOAD_PATTERN_COUNT == PATAMAR_COUNT
# Cada regulador vira três RegControl (um por fase), então um alimentador com
# poucos reguladores já passa do teto padrão de 10 do OpenDSS. Estourá-lo aborta
# a solução e nenhum tap comuta — teto folgado em todo master, não só onde há
# regulador, porque qualquer controle (RegControl, CapControl) divide o limite.
MAX_CONTROL_ITER = 100
# Padrão do OpenDSS, declarado para não depender do estado do engine singleton:
# a ação de controle é executada dentro do laço de iterações, ignorando atraso,
# até nenhum controle ficar pendente. É o modo de regime permanente.
CONTROL_MODE = "Static"
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

# Quem decide se a CARGA é ligada entre fases é o FASES2 dela (``TCarga``
# ``FasesMT``), não o TIPO_LIG: 13 é delta — PD/PE/PF do MODELO_CARGA são as
# potências dos pares DE/EF/FD — e 14 é estrela com neutro. Conferido contra as
# correntes do relatório: 2,809/2,809/0,069 kW nos três pares dão 0,22/0,37/0,22 A
# nas linhas, que em estrela seriam 0,37/0,37/0,01 A. O TIPO_LIG 2 (ET e EP)
# costuma acompanhar o 13; quando discorda do FASES2, a exportação avisa.
DELTA_CONNECTION_TYPE = "2"
# Par fase-fase de cada coluna de patamar: PD vai para DE, PE para EF, PF para FD.
_DELTA_PAIR_BY_PHASE = {
    "D": ("D", "E"),
    "E": ("E", "F"),
    "F": ("F", "D"),
}

# Impedância residual da chave fechada, escrita **depois** do ``Switch=Yes``.
# Sozinho, o ``Switch=Yes`` deixa 1 mΩ + j1 mΩ em cada chave (r1=1 por unidade
# com length=0,001), e ao longo de um tronco com várias chaves isso vira perda
# e queda de tensão que o Interplan não tem — lá a chave é ideal. O
# ``Switch=Yes`` só redefine os parâmetros no momento em que é lido, então as
# propriedades seguintes prevalecem e a linha continua marcada como chave.
SWITCH_IMPEDANCE_DIRECTIVE = (
    "r1=1e-6 x1=1e-6 r0=1e-6 x0=1e-6 c1=0 c0=0 Length=1 units=km"
)

# FASES2 do consumidor que não descreve fase alguma. O Interplan cria um
# consumidor sintético para o gerador de MT sem consumidor próprio ("Cons.
# 11185_AUX", FASES2 0) e trata o gerador como trifásico equilibrado — o
# relatório o lista como "Aterrado". A regra vale só para geradores: carga ou
# trecho sem fase continua sendo erro de cadastro.
UNSPECIFIED_GENERATOR_PHASES = frozenset({"", "0"})

# --- Modo de cargas Energia ---------------------------------------------------
# Um trafo por CARGA, com as UCs na barra secundária. A barra é fictícia — só
# existe no .dss, sem coordenada —, e o mapa não muda. Os arquivos seguem a
# convenção do modo Interplan: trafos por contagem de fases, UCs nos
# cargas*.dss e GD nos geradores*.dss pelo ``phases=`` da Load, IP à parte.
SINGLE_PHASE_TRANSFORMERS_FILENAME = "trafosmonofasicos.dss"
THREE_PHASE_TRANSFORMERS_FILENAME = "trafostrifasicos.dss"
PUBLIC_LIGHTING_FILENAME = "iluminacaopublica.dss"
_TRANSFORMER_FILES = {
    1: (SINGLE_PHASE_TRANSFORMERS_FILENAME, "monofasicos"),
    3: (THREE_PHASE_TRANSFORMERS_FILENAME, "trifasicos"),
}
ENERGY_TRANSFORMER_PREFIX = "TRF-"
ENERGY_SECONDARY_BUS_PREFIX = "BT_"
# Trafo ideal, como no agregar_trafos_dss: %R e reatâncias mínimos, sem perdas a
# vazio, sem magnetização e sem a reatância anti-flutuação (os enrolamentos já
# são aterrados). O trafo só muda a tensão: a carga vista pela MT é a soma das
# UCs. Da UTC vêm a ligação e as tensões.
ENERGY_TRANSFORMER_R_PERCENT = 0.001
ENERGY_TRANSFORMER_X_PERCENT = 0.001
# VPRIMARIO da UTC vale como tensão nominal do primário quando difere menos que
# isto da tensão da ligação — é o tap do trafo. Acima, é unidade trocada, e vale
# a do circuito.
ENERGY_PRIMARY_VOLTAGE_TOLERANCE = 0.15
# FP só para dimensionar o trafo ideal que não tem kVA no cadastro.
ENERGY_SIZING_POWER_FACTOR = 0.92

# Perfil diário da tensão da fonte (VSE1..VSE4 do CIRCUITO, em pu), um ponto por
# patamar como os das cargas.
SOURCE_VOLTAGE_SHAPE_PREFIX = "VSE-"
# Desvio abaixo do qual a tensão da fonte é tratada como nominal e o master sai
# sem o perfil, idêntico ao de antes.
_SOURCE_VOLTAGE_TOLERANCE = 1e-9

# --- Regulador de tensão -----------------------------------------------------
# Um regulador trifásico vira três transformadores monofásicos, um por fase, e
# cada um ganha o seu RegControl. Os valores abaixo são a modelagem adotada; o
# VREG e a banda vêm de ``opendss_regulator_settings``.
REGULATOR_NAME_PREFIX = "REG-"
# Prefixo próprio, no espírito do regulador: torna o arquivo
# autoexplicativo e afasta a colisão com cargas e geradores, que dividem o
# mesmo namespace ``Load.*``.
CAPACITOR_NAME_PREFIX = "CAP-"
REGULATOR_CONTROL_NAME_PREFIX = "CTRL-"
# Transformador quase ideal: o regulador injeta tensão em série, não impedância.
# A potência de cada unidade é fixa e grande, e não a SNOM: a SNOM do cadastro é
# a potência de regulação (±10 % da passante), e como base dos 0,01 % ela punha
# no modelo uma queda e perdas que o equipamento não tem — medido no 070004,
# +10 a +16 kW na saída e +18 a +32 kW de perdas, com a passante a 600 % da
# SNOM. Com 10 MVA por unidade a impedância fica abaixo de 1 mΩ em 13,8 kV.
REGULATOR_UNIT_KVA = 10_000.0
REGULATOR_XHL_PERCENT = 0.01
REGULATOR_LOAD_LOSS_PERCENT = 0.01
# Transformador de potencial: 115 V entre fases no secundário, como o primário,
# de modo que ``ptratio`` fica VNOM*1000/115 — o √3 dos dois lados se cancela.
# É também a base de vreg e band: o RegControl os lê no secundário do TP, de
# fase, então 1 pu vale 115/√3 ≈ 66,4 V.
REGULATOR_PT_SECONDARY_V = 115.0
# O regulador ocupa o lugar do trecho, então a impedância dele sai do modelo.
# Acima deste comprimento isso deixa de ser desprezível e vira aviso.
REGULATOR_SEGMENT_LENGTH_WARNING_M = 10.0
# VNOM do regulador e do circuito são a mesma tensão de linha, em kV. Uma
# divergência maior que esta denuncia unidade trocada (volts em vez de kV),
# que passaria despercebida por gerar um modelo aceito e absurdo.
REGULATOR_VOLTAGE_TOLERANCE = 0.10

_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:(?:\d+(?:[.,]\d*)?)|(?:[.,]\d+))(?:[eE][+-]?\d+)?$"
)

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class OpenDssExportIssue:
    """Ocorrência encontrada ao exportar um trecho."""

    segment_id: str
    reason: str


class OpenDssLibraryExportError(ValueError):
    """Falha estrita ao resolver as bibliotecas físicas de uma exportação."""

    def __init__(self, errors: Iterable[str]) -> None:
        unique = tuple(
            dict.fromkeys(
                str(error).strip() for error in errors if str(error).strip()
            )
        )
        if not unique:
            unique = ("erro desconhecido ao resolver as bibliotecas OpenDSS",)
        self.errors = unique
        super().__init__(
            "Não foi possível exportar usando as bibliotecas OpenDSS:\n• "
            + "\n• ".join(unique)
        )


@dataclass(frozen=True, slots=True)
class OpenDssLibraryExportResult:
    """Os três arquivos físicos que precedem ``trechos.dss`` no master."""

    cables_text: str
    arrangements_text: str
    line_geometries_text: str
    cable_count: int
    arrangement_count: int
    line_geometry_count: int


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
class OpenDssCapacitorExportResult:
    """Resultado do arquivo único de bancos de capacitores.

    ``exported_count`` conta **bancos de origem**, não linhas ``Load``: um banco
    trifásico vira três ``Load`` e ainda assim soma 1, para o relatório falar a
    mesma língua do cadastro.
    """

    text: str
    exported_count: int
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
class OpenDssEnergyExportResult:
    """Elementos do modo Energia, já separados pelos arquivos da convenção.

    ``own_files`` são os arquivos só do modo Energia — os dois de trafos e o de
    IP —, completos. ``sections`` são blocos (``LoadShape`` + ``Load``) a anexar
    aos ``cargas*.dss`` (UCs) e ``geradores*.dss`` (GD), por nome de arquivo:
    quem junta é o :class:`OpenDssExportBundle`, porque esses arquivos também
    recebem as CARGAs que ficaram no modelo do Interplan.

    ``exported_count`` conta trafos; ``load_count`` e ``generator_count``, as
    ``Load`` de UC/IP e de GD. ``replaced_load_indices`` são as CARGAs que o modo
    Energia assumiu — elas saem do arquivo de cargas, para a potência não entrar
    duas vezes. ``voltage_base_commands`` vão para o master, depois do
    ``calcvoltagebases``: é o que dá às barras secundárias a base certa.
    """

    own_files: tuple[tuple[str, str], ...]
    sections: tuple[tuple[str, str], ...]
    exported_count: int
    load_count: int
    generator_count: int
    discarded_count: int
    issues: tuple[OpenDssExportIssue, ...]
    omitted_issues: int
    used_names: frozenset[str] = frozenset()
    replaced_load_indices: frozenset[int] = frozenset()
    voltage_base_commands: tuple[str, ...] = ()
    # Contagens por arquivo, para o relatório: ``(phases, quantidade)``.
    transformer_counts: tuple[tuple[int, int], ...] = ()
    consumer_counts: tuple[tuple[int, int], ...] = ()
    lighting_count: int = 0
    generation_counts: tuple[tuple[int, int], ...] = ()

    @property
    def has_warnings(self) -> bool:
        return self.discarded_count > 0 or bool(self.issues)

    def section(self, filename: str) -> str:
        """O bloco a anexar a ``filename``, ou ``""``."""

        return next((text for name, text in self.sections if name == filename), "")


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
    capacitors: OpenDssCapacitorExportResult | None = None
    master: OpenDssMasterExportResult | None = None
    library: OpenDssLibraryExportResult | None = None
    input_revision: object | None = None
    energy: OpenDssEnergyExportResult | None = None

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

        No modo Energia, os trafos vêm **antes** das cargas — a barra
        ``BT_<CARGA>`` já existe quando a ``Load`` é lida —, as UCs e a GD
        entram nos ``cargas*.dss`` e ``geradores*.dss`` depois das CARGAs que
        ficaram no modelo do Interplan, e a IP tem arquivo próprio. Quando o
        modo Energia exporta, os três ``cargas*.dss`` saem sempre, e os três
        ``geradores*.dss`` saem se houver GD ou resultado de geradores.
        """

        regulators = self.regulators
        capacitors = self.capacitors
        library = self.library
        energy = self.energy if self.energy is not None and self.energy.exported_count else None
        own = {} if energy is None else dict(energy.own_files)
        loads = dict(self.loads_by_phase_count)
        generators = dict(self.generators_by_phase_count)
        energy_generation = energy is not None and any(
            energy.section(_GENERATOR_FILES[count][0]) for count in GENERATOR_PHASE_COUNTS
        )

        def merged(files, results, kind: str, with_energy: bool):  # noqa: ANN001, ANN202
            for count in sorted(files):
                filename, label = files[count]
                result = results.get(count)
                section = "" if energy is None else energy.section(filename)
                if result is None and not with_energy:
                    continue
                yield filename, _merged_file_text(
                    None if result is None else result.text,
                    section,
                    f"! {kind} {label} exportad{'a' if kind == 'Cargas' else 'o'}s "
                    "pelo Visualizador de Circuitos Eletricos (modo Energia)",
                )

        return (
            *(
                ()
                if library is None
                else (
                    (CABOS_FILENAME, library.cables_text),
                    (ARRANGEMENTS_FILENAME, library.arrangements_text),
                    (LINE_GEOMETRIES_FILENAME, library.line_geometries_text),
                )
            ),
            (LINES_FILENAME, self.lines.text),
            (SWITCHES_FILENAME, self.switches.text),
            *(
                ()
                if regulators is None or not regulators.exported_count
                else ((REGULATORS_FILENAME, regulators.text),)
            ),
            *(
                (filename, own[filename])
                for filename, _ in _TRANSFORMER_FILES.values()
                if filename in own
            ),
            *merged(_LOAD_FILES, loads, "Cargas", energy is not None),
            *(
                ((PUBLIC_LIGHTING_FILENAME, own[PUBLIC_LIGHTING_FILENAME]),)
                if PUBLIC_LIGHTING_FILENAME in own
                else ()
            ),
            *merged(_GENERATOR_FILES, generators, "Geradores", energy_generation),
            *(
                ()
                if capacitors is None or not capacitors.exported_count
                else ((CAPACITORS_FILENAME, capacitors.text),)
            ),
        )

    @property
    def files(self) -> tuple[tuple[str, str], ...]:
        master = self.master
        metadata = ()
        if self.input_revision is not None:
            import json
            from dataclasses import asdict
            metadata = (("estudo.json", json.dumps(asdict(self.input_revision), ensure_ascii=False, indent=2)),)
        if master is None or not master.text:
            return (*self.element_files, *metadata)
        return (
            *self.element_files,
            (master.master_filename, master.text),
            (master.buscoords_filename, master.buscoords_text),
            *metadata,
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
            *(() if self.energy is None else self.energy.issues),
            *(() if self.capacitors is None else self.capacitors.issues),
            *(() if self.master is None else self.master.issues),
        )

    @property
    def omitted_issues(self) -> int:
        total = self.lines.omitted_issues + self.switches.omitted_issues
        total += 0 if self.regulators is None else self.regulators.omitted_issues
        total += sum(result.omitted_issues for result in self._load_results)
        total += sum(result.omitted_issues for result in self._generator_results)
        total += 0 if self.energy is None else self.energy.omitted_issues
        total += 0 if self.capacitors is None else self.capacitors.omitted_issues
        return total + (0 if self.master is None else self.master.omitted_issues)

    @property
    def discarded_count(self) -> int:
        total = self.lines.discarded_count + self.switches.discarded_count
        total += 0 if self.regulators is None else self.regulators.discarded_count
        total += sum(result.discarded_count for result in self._load_results)
        total += sum(result.discarded_count for result in self._generator_results)
        total += 0 if self.energy is None else self.energy.discarded_count
        total += 0 if self.capacitors is None else self.capacitors.discarded_count
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
            or (self.energy is not None and self.energy.has_warnings)
            or (self.capacitors is not None and self.capacitors.has_warnings)
            or (self.master is not None and self.master.has_warnings)
        )


def _merged_file_text(base: str | None, section: str, standalone_title: str) -> str:
    """Um arquivo da convenção com o bloco do modo Energia anexado.

    ``base`` é o texto do modo Interplan (CARGAs ou geradores que ficaram fora
    do modo Energia), que segue intacto na frente; sem ele, o arquivo nasce com
    ``standalone_title``. Sem bloco, ``base`` sai idêntico.
    """

    if base is None:
        return f"{standalone_title}\n\n{section}" if section else f"{standalone_title}\n"
    if not section:
        return base
    return f"{base}\n{section}"


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


def _balanced_three_phase_entry(configuration: PhaseConfiguration):  # noqa: ANN202
    """A entrada trifásica D/E/F, para o gerador de FASES2 não informada."""

    return next(
        (
            entry
            for entry in configuration.entries
            if entry.phase_count == 3
            and entry.dss
            and _phase_letters(entry.name, 3) == ("D", "E", "F")
        ),
        None,
    )


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

    Quando o modelo é composto de várias fontes, ``bars.name_suffixes`` traz o
    desempate já calculado: dois bancos podem ter o mesmo CODIGO para barras
    diferentes, e sem o sufixo elas virariam **uma** barra no OpenDSS. O
    desempate vem pronto porque esta função é construída dentro de laços sobre
    todas as barras — calcular aqui seria custo quadrático.
    """

    bars = catalog.segments.bars
    suffixes = bars.name_suffixes

    def bus_name(bar_index: int) -> str:
        code = sanitize_dss_name(bars.codes[bar_index])
        base = code or sanitize_dss_name(bars.bar_ids[bar_index])
        if suffixes is None:
            return base
        suffix = suffixes[bar_index]
        return f"{base}_{suffix}" if suffix else base

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


@dataclass(frozen=True, slots=True)
class _LibraryLineCandidate:
    segment_index: int
    segment_id: str
    name: str
    dss_nodes: str
    phase_count: int
    bus1: str
    bus2: str
    length: float


def _full_precision(value: float | int) -> str:
    """Representação decimal curta que preserva o ``float`` da biblioteca."""

    number = float(value)
    if number.is_integer():
        return str(int(number))
    return repr(number)


def _usable_library_number(value: float | int | None) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) != 0.0
    )


def _namespace_names(
    entries: Sequence[tuple[object, str, str]],
) -> dict[object, str]:
    """Saneia e desambigua nomes dentro de um namespace OpenDSS."""

    used: set[str] = set()
    result: dict[object, str] = {}
    for key, raw_name, fallback in entries:
        base = sanitize_dss_name(raw_name) or sanitize_dss_name(fallback) or "ITEM"
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate.casefold())
        result[key] = candidate
    return result


def _render_definition_file(
    header: str,
    blocks: Sequence[Sequence[str]],
) -> str:
    lines = [header, ""]
    for block in blocks:
        lines.extend(block)
        lines.append("")
    return "\n".join(lines)


def _cable_block(cable: CableDefinition, dss_name: str) -> tuple[str, ...]:
    parameters: list[str] = [f"New WireData.{dss_name}"]
    if _usable_library_number(cable.rac):
        parameters.append(f"Rac={_full_precision(cable.rac)}")
    if _usable_library_number(cable.rdc):
        parameters.append(f"Rdc={_full_precision(cable.rdc)}")
    parameters.append(f"Runits={cable.resistance_units}")
    parameters.append(f"GMRac={_full_precision(cable.gmr)}")
    parameters.append(f"GMRunits={cable.gmr_units}")
    if _usable_library_number(cable.radius):
        parameters.append(f"Radius={_full_precision(cable.radius)}")
    elif _usable_library_number(cable.diameter):
        parameters.append(f"Diam={_full_precision(cable.diameter)}")
    parameters.append(f"Radunits={cable.radius_units}")
    if _usable_library_number(cable.normal_amps):
        parameters.append(f"Normamps={_full_precision(cable.normal_amps)}")
    if _usable_library_number(cable.emergency_amps):
        parameters.append(f"Emergamps={_full_precision(cable.emergency_amps)}")
    return (" ".join(parameters),)


def _segments_label(segment_ids: Iterable[str]) -> str:
    values = tuple(sorted(set(segment_ids), key=str.casefold))
    return ", ".join(values)


def build_library_line_export(
    catalog: CircuitCatalogModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    library_catalog: OpenDssLibraryCatalog,
    library_mappings: OpenDssLibraryMappings,
    *,
    skip_segments: frozenset[int] = frozenset(),
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[OpenDssLineExportResult, OpenDssLibraryExportResult]:
    """Gera linhas por ``geometry=`` e o fecho físico estritamente necessário."""

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
    owner_voltage: dict[int, tuple[str, float | None]] = {}
    used_names: dict[str, str] = {}
    used_names_casefold: dict[str, str] = {}
    switch_segments: set[int] = set()
    candidates: list[_LibraryLineCandidate] = []

    # Este laço replica apenas os filtros comuns do caminho legado. A resolução
    # física abaixo é deliberadamente paralela e não altera build_line_export().
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
            if record.length is None:
                report.add(segment_id, "COMPR ausente")
                continue

            name = sanitize_dss_name(record.code)
            if not name:
                name = sanitize_dss_name(segment_id) or "LINE"
                report.add(
                    segment_id,
                    "CODIGO vazio ou sem caracteres válidos; o nome da linha "
                    "usou um identificador alternativo",
                    discarded=False,
                )
            base_name = name
            suffix = 2
            while name.casefold() in used_names_casefold:
                name = f"{base_name}_{suffix}"
                suffix += 1
            if name != base_name:
                previous_segment = used_names_casefold[base_name.casefold()]
                report.add(
                    segment_id,
                    f"nome '{base_name}' colidiu com o trecho {previous_segment} "
                    f"no namespace Line; foi exportado como '{name}'",
                    discarded=False,
                )
            used_names[name] = segment_id
            used_names_casefold[name.casefold()] = segment_id
            candidates.append(
                _LibraryLineCandidate(
                    segment_index=segment_index,
                    segment_id=segment_id,
                    name=name,
                    dss_nodes=entry.dss,
                    phase_count=entry.phase_count,
                    bus1=bus_name(int(segments.start_indices[segment_index])),
                    bus2=bus_name(int(segments.end_indices[segment_index])),
                    length=record.length,
                )
            )

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Exportação cancelada.")

    automatic = build_automatic_assemblies(
        segments,
        phase_configuration,
        library_catalog,
        library_mappings,
    )
    candidate_indices = {item.segment_index for item in candidates}
    candidate_ids = {item.segment_id for item in candidates}
    strict_errors: list[str] = []
    for issue in automatic.issues:
        affected = tuple(
            segment_id for segment_id in issue.segment_ids if segment_id in candidate_ids
        )
        if affected:
            strict_errors.append(
                f"{issue.field} '{issue.value}': {issue.reason} "
                f"(trechos: {_segments_label(affected)})"
            )

    assembly_by_segment: dict[int, AutomaticAssembly] = {}
    for assembly in automatic.assemblies:
        for segment_index in assembly.segment_indices:
            if segment_index not in candidate_indices:
                continue
            previous = assembly_by_segment.setdefault(segment_index, assembly)
            if previous is not assembly:
                strict_errors.append(
                    f"trecho {segments.segment_ids[segment_index]} recebeu mais de uma montagem automática"
                )
    for candidate in candidates:
        if candidate.segment_index not in assembly_by_segment:
            strict_errors.append(
                f"trecho {candidate.segment_id} não recebeu montagem automática"
            )

    # spacing: arranjo base + quantidade de fases + presença efetiva de neutro.
    spacing_specs: dict[
        tuple[str, int, bool], tuple[ArrangementDefinition, str, set[str]]
    ] = {}
    # geometry: spacing + IDs crus de cabo, para honrar o Mapa de Cabos.
    geometry_specs: dict[
        tuple[tuple[str, int, bool], str, str | None],
        tuple[AutomaticAssembly, set[str]],
    ] = {}
    geometry_by_segment: dict[
        int, tuple[tuple[str, int, bool], str, str | None]
    ] = {}
    cable_uses: dict[str, set[str]] = {}

    for candidate in candidates:
        assembly = assembly_by_segment.get(candidate.segment_index)
        if assembly is None:
            continue
        neutral = assembly.key.neutral_cable_id is not None
        phase_count = len(assembly.phase_letters)
        spacing_key = (assembly.key.arrangement_id, phase_count, neutral)
        existing_spacing = spacing_specs.get(spacing_key)
        if existing_spacing is None:
            spacing_specs[spacing_key] = (
                assembly.arrangement,
                f"{phase_count}F{'N' if neutral else ''}",
                {candidate.segment_id},
            )
        else:
            existing_spacing[2].add(candidate.segment_id)
            first_positions = tuple(
                (item.x, item.height) for item in existing_spacing[0].positions
            )
            current_positions = tuple(
                (item.x, item.height) for item in assembly.arrangement.positions
            )
            if first_positions != current_positions:
                strict_errors.append(
                    f"arranjo {assembly.key.arrangement_id} gerou posições divergentes "
                    f"para {existing_spacing[1]}"
                )

        record = segments.record(candidate.segment_index)
        raw_phase = record.phase_cable_id.strip()
        raw_neutral = record.neutral_cable_id.strip() if neutral else None
        geometry_key = (spacing_key, raw_phase, raw_neutral)
        existing_geometry = geometry_specs.get(geometry_key)
        if existing_geometry is None:
            geometry_specs[geometry_key] = (assembly, {candidate.segment_id})
        else:
            existing_geometry[1].add(candidate.segment_id)
            if existing_geometry[0].key.phase_cable_id != assembly.key.phase_cable_id or (
                existing_geometry[0].key.neutral_cable_id
                != assembly.key.neutral_cable_id
            ):
                strict_errors.append(
                    f"os IDs de cabo da montagem {raw_phase} resolvem cabos físicos divergentes"
                )
        geometry_by_segment[candidate.segment_index] = geometry_key
        cable_uses.setdefault(assembly.key.phase_cable_id, set()).add(
            candidate.segment_id
        )
        if assembly.key.neutral_cable_id is not None:
            cable_uses.setdefault(assembly.key.neutral_cable_id, set()).add(
                candidate.segment_id
            )

    for spacing_key, (arrangement, config, uses) in spacing_specs.items():
        if arrangement.units not in OPEN_DSS_UNITS:
            strict_errors.append(
                f"arranjo {spacing_key[0]} ({config}) usa unidade inválida "
                f"'{arrangement.units}' (trechos: {_segments_label(uses)})"
            )
        if arrangement.phase_count not in {1, 2, 3}:
            strict_errors.append(
                f"arranjo {spacing_key[0]} gerou {arrangement.phase_count} fases; "
                "somente 1F, 2F e 3F são suportados"
            )
        if arrangement.conductor_count < arrangement.phase_count:
            strict_errors.append(
                f"arranjo {spacing_key[0]} ({config}) tem menos condutores que fases"
            )
        invalid_positions = [
            index + 1
            for index, position in enumerate(arrangement.positions)
            if not math.isfinite(float(position.x))
            or not math.isfinite(float(position.height))
        ]
        if invalid_positions:
            strict_errors.append(
                f"arranjo {spacing_key[0]} ({config}) possui coordenadas inválidas "
                f"nas posições {', '.join(map(str, invalid_positions))}"
            )
        repeated = coincident_positions(arrangement)
        if repeated:
            pairs = ", ".join(f"{left} e {right}" for left, right in repeated)
            strict_errors.append(
                f"arranjo {spacing_key[0]} ({config}) possui posições coincidentes: {pairs} "
                f"(trechos: {_segments_label(uses)})"
            )

    used_cables: dict[str, CableDefinition] = {}
    for cable_id, uses in cable_uses.items():
        cable = library_catalog.cable(cable_id)
        if cable is None:
            strict_errors.append(
                f"cabo {cable_id} ausente da biblioteca "
                f"(trechos: {_segments_label(uses)})"
            )
            continue
        used_cables[cable_id] = cable
        if cable.cable_type != "wire":
            strict_errors.append(
                f"cabo {cable.name} é {cable.cable_type}; este modo aceita somente WireData "
                f"(trechos: {_segments_label(uses)})"
            )
        missing = cable_issues(cable)
        if missing:
            strict_errors.append(
                f"cabo {cable.name} incompleto: falta {', '.join(missing)} "
                f"(trechos: {_segments_label(uses)})"
            )

    if strict_errors:
        raise OpenDssLibraryExportError(strict_errors)

    sorted_cables = sorted(
        used_cables.values(), key=lambda item: (item.name.casefold(), item.cable_id)
    )
    cable_names = _namespace_names(
        [
            (cable.cable_id, cable.name, cable.cable_id)
            for cable in sorted_cables
        ]
    )
    sorted_spacings = sorted(
        spacing_specs,
        key=lambda key: (
            library_catalog.arrangement(key[0]).name.casefold(),
            key[1],
            key[2],
            key[0],
        ),
    )
    spacing_names = _namespace_names(
        [
            (
                key,
                f"{library_catalog.arrangement(key[0]).name}-{spacing_specs[key][1]}",
                f"{key[0]}-{spacing_specs[key][1]}",
            )
            for key in sorted_spacings
        ]
    )
    sorted_geometries = sorted(
        geometry_specs,
        key=lambda key: (
            spacing_names[key[0]].casefold(),
            key[1].casefold(),
            (key[2] or "").casefold(),
        ),
    )
    geometry_name_entries: list[tuple[object, str, str]] = []
    for key in sorted_geometries:
        spacing_key, raw_phase, raw_neutral = key
        base = library_catalog.arrangement(spacing_key[0])
        config = spacing_specs[spacing_key][1]
        raw_name = f"{base.name}-{config}-{raw_phase}"
        if raw_neutral is not None:
            raw_name += f"-N-{raw_neutral}"
        geometry_name_entries.append((key, raw_name, geometry_specs[key][0].assembly_id))
    geometry_names = _namespace_names(geometry_name_entries)

    cable_blocks = [
        _cable_block(cable, cable_names[cable.cable_id]) for cable in sorted_cables
    ]
    spacing_blocks: list[tuple[str, ...]] = []
    for key in sorted_spacings:
        arrangement = spacing_specs[key][0]
        spacing_blocks.append(
            (
                f"New LineSpacing.{spacing_names[key]} "
                f"nconds={arrangement.conductor_count} "
                f"nphases={arrangement.phase_count} units={arrangement.units}",
                "~ x=["
                + ",".join(_full_precision(item.x) for item in arrangement.positions)
                + "]",
                "~ h=["
                + ",".join(
                    _full_precision(item.height) for item in arrangement.positions
                )
                + "]",
            )
        )

    geometry_blocks: list[tuple[str, ...]] = []
    for key in sorted_geometries:
        spacing_key = key[0]
        assembly = geometry_specs[key][0]
        arrangement = spacing_specs[spacing_key][0]
        block = [
            f"New LineGeometry.{geometry_names[key]} "
            f"nconds={arrangement.conductor_count} "
            f"nphases={arrangement.phase_count} "
            "reduce=yes "
            f"spacing={spacing_names[spacing_key]}"
        ]
        for index, cable_id in enumerate(assembly.geometry.cable_ids, start=1):
            block.append(f"~ cond={index} wire={cable_names[cable_id]}")
        geometry_blocks.append(tuple(block))

    line_entries: list[str] = []
    exported_segments: list[tuple[str, int]] = []
    for candidate in candidates:
        geometry_key = geometry_by_segment[candidate.segment_index]
        line_entries.append(
            f"New Line.{candidate.name}"
            f" Bus1={candidate.bus1}.{candidate.dss_nodes}"
            f" Bus2={candidate.bus2}.{candidate.dss_nodes}"
            f" Phases={candidate.phase_count}"
            f" geometry={geometry_names[geometry_key]}"
            f" Length={_format(candidate.length / 1_000.0)}"
            " units=km"
        )
        exported_segments.append((candidate.name, candidate.segment_index))

    if progress is not None:
        progress(total, total)
    line_header = (
        "! Trechos exportados pelo Visualizador de Circuitos Eletricos",
        "! Parametros fisicos em cabos.dss, arranjos.dss e geometria_linhas.dss",
        "! Circuitos: "
        + ", ".join(
            sanitize_dss_name(catalog.definition(index).circuit_id)
            for index in selected
        ),
        "",
    )
    line_text = "\n".join((*line_header, *line_entries))
    if line_entries:
        line_text += "\n"
    line_result = OpenDssLineExportResult(
        text=line_text,
        exported_count=len(line_entries),
        skipped_switch_count=len(switch_segments),
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        used_names=frozenset(used_names),
        exported_segments=tuple(exported_segments),
    )
    library_result = OpenDssLibraryExportResult(
        cables_text=_render_definition_file(
            "! Cabos WireData usados pelo circuito", cable_blocks
        ),
        arrangements_text=_render_definition_file(
            "! Arranjos LineSpacing usados pelo circuito", spacing_blocks
        ),
        line_geometries_text=_render_definition_file(
            "! Geometrias LineGeometry usadas pelo circuito", geometry_blocks
        ),
        cable_count=len(cable_blocks),
        arrangement_count=len(spacing_blocks),
        line_geometry_count=len(geometry_blocks),
    )
    return line_result, library_result


def build_switch_export(
    catalog: CircuitCatalogModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    reserved_names: frozenset[str] = frozenset(),
    resolve_name_collisions: bool = False,
    include_segments: frozenset[int] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssSwitchExportResult:
    """Gera o conteúdo de ``chaves.dss`` para os circuitos selecionados.

    Percorre ``switch_segment_indices``, o complemento exato de
    ``common_segment_indices`` usado por :func:`build_line_export`.

    A chave não consome cabo, ``COMPR`` nem ``VNOM``: ``Switch=Yes`` sobrescreve
    todos os parâmetros elétricos da linha, e :data:`SWITCH_IMPEDANCE_DIRECTIVE`
    os reduz em seguida a uma impedância residual — a chave fechada é ideal.
    """

    selected = _selected_indices(catalog, circuit_indices)
    segments = catalog.segments
    switches = catalog.switches
    entries_by_value = _entries_by_value(phase_configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)

    from .project_topology import physical_switch_scope
    internal_switches, boundary_switches = physical_switch_scope(catalog, selected)
    for segment in boundary_switches:
        report.add(segments.segment_ids[segment],
                   "chave de fronteira: uma ponta está fora do escopo exportado")
    total = len(internal_switches)
    processed = 0
    lines: list[str] = []
    open_commands: list[str] = []
    used_names: dict[str, str] = {}
    used_names_casefold: set[str] = set()
    reserved_names_casefold = {name.casefold() for name in reserved_names}
    exported_segments: list[tuple[str, int]] = []
    seen_segments: set[int] = set()

    for circuit_index in selected[:1]:
        for raw_index in internal_switches:
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
                if resolve_name_collisions and not name:
                    name = "SWITCH"
            if resolve_name_collisions:
                base_name = name
                suffix = 2
                while (
                    name.casefold() in reserved_names_casefold
                    or name.casefold() in used_names_casefold
                ):
                    name = f"{base_name}_{suffix}"
                    suffix += 1
                if name != base_name:
                    report.add(
                        segment_id,
                        f"nome '{base_name}' colidiu no namespace Line; "
                        f"a chave foi exportada como '{name}'",
                        discarded=False,
                    )
            else:
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
            # A impedância vem DEPOIS do Switch=Yes de propósito: ele redefine
            # r1/x1/r0/x0/c1/c0/length no momento em que é lido, então qualquer
            # parâmetro elétrico escrito antes dele seria apagado.
            lines.append(
                f"New Line.{name}"
                f" Bus1={bus1}.{entry.dss}"
                f" Bus2={bus2}.{entry.dss}"
                f" Phases={entry.phase_count}"
                " Switch=Yes"
                f" {SWITCH_IMPEDANCE_DIRECTIVE}"
            )
            used_names[name] = switch.switch_id
            used_names_casefold.add(name.casefold())
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
        "! Switch=Yes define r1/x1/r0/x0/c1/c0 e length no OpenDSS (1 mohm);",
        "! a impedancia escrita depois dele torna a chave fechada ideal",
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


def _regulation_range(value: str) -> float | None:
    """``FAIXA`` como fração (0,1 = ±10 %), ou ``None`` se inutilizável.

    O MDB grava fração, mas um cadastro em porcentagem (10) é comum o bastante
    para ser aceito: acima de 1 só pode ser porcentagem, porque uma faixa de
    ±100 % ou mais não existe.
    """

    number = parse_number(value)
    if number is None or number <= 0.0:
        return None
    if number > 1.0:
        number /= 100.0
    return number if number < 1.0 else None


def _step_count(value: str) -> int | None:
    """``NPASSOS`` como inteiro de ao menos 2, ou ``None`` se inutilizável."""

    number = parse_number(value)
    if number is None or number < 2.0 or number != int(number):
        return None
    return int(number)


def _vreg_levels(
    record: RegulatorRecord,
    default_pu: float,
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """VREG de cada patamar e os NPAT em que o cadastro deu lugar ao padrão.

    Mesma regra do Interplan: um ``VREG_Pn`` vazio, não numérico ou fora de
    [0,5; 1,5] vale o padrão (lá, o ``LR_VREG`` do ``Geral.mdb``; aqui, o das
    configurações).
    """

    levels: list[float] = []
    replaced: list[int] = []
    for npat, text in enumerate(record.vreg_by_level):
        value = parse_number(text)
        if is_valid_vreg(value):
            levels.append(float(value))
        else:
            levels.append(default_pu)
            replaced.append(npat)
    return tuple(levels), tuple(replaced)


def _single_vreg(levels: Sequence[float]) -> float:
    """O VREG mais frequente entre os patamares; no empate, o do primeiro.

    O ``RegControl`` tem um ``vreg`` só. Enquanto o master não emitir um
    comando por patamar, um regulador com VREG diferente entre patamares sai
    com um valor único, e a exportação avisa.
    """

    return min(levels, key=lambda value: (-levels.count(value), levels.index(value)))


def _is_fixed(value: str) -> bool:
    """``FIXO`` diferente de zero: o tap não é automático."""

    number = parse_number(value)
    return number is not None and number != 0.0


def _tap_fraction(value: str) -> float | None:
    """``TAP`` como fração (0,05 = +5 %), aceitando porcentagem como a ``FAIXA``."""

    number = parse_number(value)
    if number is None:
        return None
    if abs(number) >= 1.0:
        number /= 100.0
    return number


def regulators_without_power(
    catalog: CircuitCatalogModel,
    regulators: RegulatorModel | None,
    circuit_indices: Sequence[int] | Iterable[int],
) -> tuple[RegulatorRecord, ...]:
    """Reguladores dos circuitos indicados cuja ``SNOM`` não é positiva.

    Eles são exportados mesmo assim, como reguladores ideais (ver
    :func:`build_regulator_export`). A lista existe para a interface avisar
    antes da exportação: um cadastro sem potência costuma denunciar outros
    campos incompletos, e o usuário pode preferir completá-lo no painel.
    Considera os mesmos trechos que a exportação: só os comuns, porque o
    regulador num trecho de chave não é exportado.
    """

    if regulators is None:
        return ()
    by_segment = regulators.record_indices_by_segment
    seen: set[int] = set()
    found: list[RegulatorRecord] = []
    for circuit_index in _selected_indices(catalog, circuit_indices):
        for raw_index in catalog.membership(circuit_index).common_segment_indices:
            record_index = int(by_segment[int(raw_index)])
            if record_index < 0 or record_index in seen:
                continue
            seen.add(record_index)
            record = regulators.record(record_index)
            power = parse_number(record.snom)
            if power is None or power <= 0.0:
                found.append(record)
    return tuple(found)


def build_regulator_export(
    catalog: CircuitCatalogModel,
    regulators: RegulatorModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    settings: OpenDssRegulatorSettings | None = None,
    include_segments: frozenset[int] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssRegulatorExportResult:
    """Gera o conteúdo de ``reguladores.dss`` para os circuitos selecionados.

    Um regulador vira **uma unidade monofásica por fase do trecho** — três num
    trecho trifásico, duas num bifásico, uma num monofásico —, cada uma um
    ``Transformer`` com o seu ``RegControl``. Cada unidade usa a tensão de fase
    do banco, ``kV = VNOM/√3`` (a mesma :func:`phase_voltage_kv` do ``C1`` dos
    trechos e do ``kV`` das cargas).

    **O regulador é ideal.** A potência da unidade é :data:`REGULATOR_UNIT_KVA`,
    e não a ``SNOM``, então a ``SNOM`` não entra no modelo: um cadastro sem ela
    — comum no MDB — exporta o regulador do mesmo jeito, com uma ocorrência que
    não o descarta. É o que o Interplan faz, e o equipamento existe em campo.

    A faixa e os passos vêm de ``FAIXA`` e ``NPASSOS`` (``MaxTap``, ``MinTap`` e
    ``NumTaps``); vazios ou nulos, valem ±10 % em 32 passos, como no Interplan.
    O VREG e a banda vêm de ``settings``, com a banda elevada a um degrau quando
    for menor (ver :func:`~circuit_viewer.opendss_regulator_settings.effective_band_pu`).

    Quem decide o número de fases é o ``FASES2`` do **trecho**, não o cadastro
    do regulador: o MDB traz ``LIGACAO`` e ``FASES`` zerados em todos os
    registros, então eles não distinguem nada. Numa rede predominantemente
    monofásica — o alimentador medido tem 18.647 trechos monofásicos de 23.133 —
    restringir a exportação ao trifásico deixaria de fora justamente os
    reguladores das pontas, que é onde a tensão afunda.

    **O regulador ocupa o lugar do trecho.** Os transformadores ligam as duas
    barras do trecho, exatamente como a chave de :func:`build_switch_export`
    faz, e é por isso que o trecho não pode sair também como ``Line``: duas
    ligações entre as mesmas barras poriam a linha em paralelo com o regulador,
    curto-circuitando a injeção de tensão. Quem cumpre isso é
    ``replaced_segments``, consumido por :func:`build_line_export`.

    Um trecho cujo ``FASES2`` não tenha relação em ``fases2.json``, ou cujo
    ``NOME`` não resolva as fases declaradas, vira ocorrência e mantém a linha
    do trecho — sem fase resolvida não há em que barra ligar o regulador.
    """

    selected = _selected_indices(catalog, circuit_indices)
    settings = DEFAULT_OPENDSS_REGULATOR_SETTINGS if settings is None else settings
    segments = catalog.segments
    entries_by_value = _entries_by_value(phase_configuration)
    terminals = _terminals_by_phase_letter(phase_configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)
    by_segment = regulators.record_indices_by_segment
    # Base de vreg e band: a tensão de fase no secundário do TP.
    pt_phase_volts = REGULATOR_PT_SECONDARY_V / math.sqrt(3.0)

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
            letters = _phase_letters(entry.name, entry.phase_count)
            if letters is None:
                report.add(
                    segment_id,
                    f"FASES2 '{entry.fases2}' com NOME "
                    f"'{(entry.name or '').strip() or '<vazio>'}' não resolve "
                    f"{entry.phase_count} fase(s) distinta(s) entre D, E e F",
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
                # Cadastro de regulador com VNOM zerada é comum no MDB. A
                # tensão nominal do equipamento é a do alimentador onde ele
                # está, então herdá-la é a leitura correta — e evita descartar
                # o regulador, que sairia como trecho comum sem regular nada.
                if circuit_voltage is None or circuit_voltage <= 0.0:
                    report.add(
                        segment_id,
                        f"regulador {regulator.regulator_id} sem VNOM numérica "
                        f"positiva ({regulator.vnom.strip() or '<vazio>'}) e o "
                        f"circuito {definition.circuit_id} também não informa "
                        "VNOM utilizável",
                    )
                    continue
                report.add(
                    segment_id,
                    f"regulador {regulator.regulator_id} sem VNOM numérica "
                    f"positiva ({regulator.vnom.strip() or '<vazio>'}); herdou "
                    f"a VNOM {definition.nominal_voltage.strip()} do circuito "
                    f"{definition.circuit_id}",
                    discarded=False,
                )
                voltage = circuit_voltage
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

            power = parse_number(regulator.snom)
            if power is None or power <= 0.0:
                report.add(
                    segment_id,
                    f"regulador {regulator.regulator_id} sem SNOM numérica "
                    f"positiva ({regulator.snom.strip() or '<vazio>'}); "
                    "exportado como regulador ideal, que não depende dela",
                    discarded=False,
                )
            regulation_range = _regulation_range(regulator.regulation_range)
            if regulation_range is None:
                report.add(
                    segment_id,
                    f"regulador {regulator.regulator_id} sem FAIXA utilizável "
                    f"({regulator.regulation_range.strip() or '<vazio>'}); "
                    "adotada ±10 %, como no Interplan",
                    discarded=False,
                )
                regulation_range = DEFAULT_REGULATION_RANGE
            step_count = _step_count(regulator.step_count)
            if step_count is None:
                report.add(
                    segment_id,
                    f"regulador {regulator.regulator_id} sem NPASSOS "
                    f"utilizável ({regulator.step_count.strip() or '<vazio>'}); "
                    f"adotados {DEFAULT_STEP_COUNT} passos, como no Interplan",
                    discarded=False,
                )
                step_count = DEFAULT_STEP_COUNT
            taps = (
                f" MaxTap={_format(1.0 + regulation_range)}"
                f" MinTap={_format(1.0 - regulation_range)}"
                f" NumTaps={step_count}"
            )
            fixed = _is_fixed(regulator.fixed)
            if fixed:
                # FIXO: o tap fica onde o cadastro manda e não há RegControl,
                # o mesmo que o Interplan faz com 1 + TAP.
                tap = _tap_fraction(regulator.tap)
                if tap is None:
                    report.add(
                        segment_id,
                        f"regulador {regulator.regulator_id} com FIXO e TAP "
                        f"não numérico ({regulator.tap.strip() or '<vazio>'}); "
                        "tap fixo no neutro",
                        discarded=False,
                    )
                    tap = 0.0
                if abs(tap) > regulation_range:
                    limited = math.copysign(regulation_range, tap)
                    report.add(
                        segment_id,
                        f"TAP {_format(tap * 100.0)} % do regulador "
                        f"{regulator.regulator_id} além da FAIXA; limitado a "
                        f"{_format(limited * 100.0)} %",
                        discarded=False,
                    )
                    tap = limited
                taps += f" Taps=[1, {_format(1.0 + tap)}]"
                report.add(
                    segment_id,
                    f"regulador {regulator.regulator_id} com FIXO: tap fixo em "
                    f"{_format(tap * 100.0)} %, sem controle automático",
                    discarded=False,
                )
            else:
                band_pu = effective_band_pu(
                    settings.band_pu, regulation_range, step_count
                )
                if band_pu > settings.band_pu:
                    report.add(
                        segment_id,
                        f"banda de {_format(settings.band_pu)} pu menor que um "
                        f"degrau do regulador {regulator.regulator_id}; usada "
                        f"{_format(band_pu)} pu para o tap não oscilar",
                        discarded=False,
                    )
                levels, replaced = _vreg_levels(regulator, settings.vreg_pu)
                invalid = [
                    npat for npat in replaced
                    if regulator.vreg_by_level[npat].strip()
                ]
                if invalid:
                    report.add(
                        segment_id,
                        f"regulador {regulator.regulator_id} com "
                        + ", ".join(
                            f"VREG_P{npat + 1} = "
                            f"{regulator.vreg_by_level[npat].strip()}"
                            for npat in invalid
                        )
                        + f" fora de [0,5; 1,5] pu; usado o padrão "
                        f"{_format(settings.vreg_pu)} pu, como no Interplan",
                        discarded=False,
                    )
                vreg_pu = _single_vreg(levels)
                if len(set(levels)) > 1:
                    report.add(
                        segment_id,
                        f"regulador {regulator.regulator_id} com VREG diferente "
                        "por patamar ("
                        + "; ".join(_format(value) for value in levels)
                        + f" pu); o RegControl aceita um só e saiu "
                        f"{_format(vreg_pu)} pu nos quatro patamares",
                        discarded=False,
                    )
                vreg = vreg_pu * pt_phase_volts
                band = band_pu * pt_phase_volts

            phase_kv = _format(phase_voltage_kv(voltage))
            unit_kva = _format(REGULATOR_UNIT_KVA)
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
                    f" kVAs=[{unit_kva}, {unit_kva}]"
                    f"{taps}"
                )
                if not fixed:
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
        "! Uma unidade monofasica (Transformer + RegControl) por fase do "
        "trecho: 3, 2 ou 1",
        "! kV = VNOM/raiz(3); o trecho regulado "
        f"nao sai em {LINES_FILENAME}",
        f"! Regulador ideal: {_format(REGULATOR_UNIT_KVA)} kVA por unidade, "
        "independente da SNOM; faixa e passos de FAIXA e NPASSOS",
        "! vreg e band em volts no secundario do TP (1 pu = 115/raiz(3) V)",
        "! VREG de VREG_P1..P4 do cadastro; vazio ou fora de [0,5; 1,5] pu vale "
        f"o padrao, {_format(settings.vreg_pu)} pu",
        f"! Banda de {_format(settings.band_pu)} pu, nunca menor que um degrau; "
        "FIXO diferente de zero fixa o tap em 1 + TAP, sem RegControl",
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


def _single_phase_delta_error(entry) -> str | None:  # noqa: ANN001
    """Por que o ``DSS`` de uma monofásica delta não é um par de fases.

    A carga 7/8/9 do Interplan é monofásica **entre fases**: o ``DSS`` precisa
    nomear dois nós de fase distintos (``1.2``), nunca o neutro.
    """

    nodes = [node.strip() for node in (entry.dss or "").split(".")]
    if len(nodes) == 2 and "0" not in nodes and nodes[0] != nodes[1] and all(nodes):
        return None
    return (
        f"FASES2 '{entry.fases2}' com LIGACAO delta precisa de DSS com duas "
        f"fases (ex.: 1.2), não '{entry.dss or '<vazio>'}'"
    )


# Tolerâncias da soma dos pesos ZIP do cadastro: fração (1) ou porcentagem
# (100), como o Interplan grava. Mesma precisão do diálogo para a fração.
_ZIP_FRACTION_TOLERANCE = 1e-4
_ZIP_PERCENT_TOLERANCE = 1e-2


def _format_zip_weight(value: float) -> str:
    """Quatro casas, como os pesos do diálogo (``opendss_settings``)."""

    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _registry_zip(record: LoadPatternRecord) -> tuple[float, float, float] | str | None:
    """Pesos (Z, I, P) do cadastro em fração, ``None`` sem modelo, ``"inválido"``.

    ``SCTE`` é a parcela de potência constante. Pesos em porcentagem (somando
    100) viram fração; uma soma que não feche nem em 1 nem em 100 é inválida.
    """

    texts = (record.zcte, record.icte, record.scte)
    if not any(text.strip() for text in texts):
        return None
    values = [parse_number(text) if text.strip() else 0.0 for text in texts]
    if any(value is None for value in values):
        return "inválido"
    total = math.fsum(values)
    if abs(total - 100.0) <= _ZIP_PERCENT_TOLERANCE:
        values = [value / 100.0 for value in values]
    elif abs(total - 1.0) > _ZIP_FRACTION_TOLERANCE:
        return "inválido"
    return (values[0], values[1], values[2])


def registry_zip_weights(
    group: Sequence[LoadPatternRecord],
) -> tuple[tuple[float, float, float] | None, list[str]]:
    """Pesos (Z, I, P) do cadastro de uma carga, e as observações.

    Devolve ``None`` quando nenhum patamar traz modelo válido — a carga segue o
    das configurações. A ``Load`` tem um ``ZIPV`` só: com modelos diferentes
    entre patamares vale o mais frequente (no empate, o do primeiro), como o
    VREG dos reguladores.
    """

    notes: list[str] = []
    weights: list[tuple[float, float, float]] = []
    for record in group:
        parsed = _registry_zip(record)
        if parsed is None:
            continue
        if isinstance(parsed, str):
            notes.append(
                f"pesos ZIP do NPAT {record.npat} não somam 1 nem 100 "
                f"(Z {record.zcte or '0'}, I {record.icte or '0'}, "
                f"P {record.scte or '0'}); ignorados"
            )
            continue
        weights.append(parsed)
    if not weights:
        return None, notes
    chosen = min(weights, key=lambda value: (-weights.count(value), weights.index(value)))
    if len(set(weights)) > 1:
        notes.append(
            "modelo ZIP diferente entre patamares; a Load aceita um só e saiu "
            "Z/I/P " + "/".join(_format_zip_weight(value) for value in chosen)
        )
    return chosen, notes


def zipv_directive(
    active: Sequence[float],
    reactive: Sequence[float],
    cutoff: float,
) -> str:
    """``model=8 ZIPV=[…]`` com os pesos (Z, I, P) da ativa e da reativa."""

    vector = ", ".join(
        _format_zip_weight(value) for value in (*active, *reactive, cutoff)
    )
    return f"model=8 ZIPV=[{vector}]"


def _registry_zip_directive(
    group: Sequence[LoadPatternRecord],
    cutoff: float,
) -> tuple[str | None, list[str]]:
    """Diretiva do modelo ZIP do cadastro da carga, ou ``None`` sem ele."""

    chosen, notes = registry_zip_weights(group)
    if chosen is None:
        return None, notes
    return zipv_directive(chosen, chosen, cutoff), notes


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
    load_settings: OpenDssLoadSettings | None = None,
    demand_correction: DemandCorrections | None = None,
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

    O FASES2 da CARGA é lido na tabela da CARGA
    (:meth:`PhaseConfiguration.for_loads`). As relações com ``LIGACAO`` delta
    são ligadas entre fases, como no Interplan, com ``conn=delta`` e o ``kV`` de
    linha. Na trifásica (13), a ``Load`` de cada letra vai para o par seguinte
    (``-D`` em DE, ``-E`` em EF, ``-F`` em FD). Na monofásica (7/8/9), a única
    ``Load`` vai para o par do ``DSS`` (``1.2``) e leva a coluna do ``NOME``
    (PD na 7). Nome e perfil não mudam — o ``-D`` continua levando PD/QD.

    ``CircuitMembership`` não associa cargas, apenas barras, então a carga é
    atribuída ao circuito pela barra em que está pendurada.

    Uma carga sai completa ou não sai: qualquer problema em uma das fases
    descarta todas, porque carga pela metade subestimaria a demanda em silêncio.

    ``demand_correction`` multiplica cada patamar do perfil pelo fator daquele
    patamar (``mult[k]·fP[k]``, ``qmult[k]·fQ[k]``) nas cargas do circuito
    corrigido. ``kW=1 kvar=1`` não muda: um ``kW`` diferente de 1 multiplicaria
    os quatro patamares pelo mesmo valor.
    """

    if phase_count not in _LOAD_FILES:
        raise ValueError(f"Contagem de fases sem arquivo: {phase_count}")

    # O modelo vale só para as cargas de consumo. Geradores e ramais
    # equivalentes são Load por dialeto do exportador, não por natureza, e
    # continuam em potência constante; capacitores, em impedância constante.
    # A carga com modelo ZIP no cadastro (MODELO_CARGA_SIZ) usa o dela.
    model_directive = (
        "model=1"
        if load_settings is None
        else load_settings.load_model_directive()
    )
    zip_cutoff = 0.0 if load_settings is None else load_settings.zipv.cutoff
    registry_zip_count = 0
    uses_zipv = False

    selected = _selected_indices(catalog, circuit_indices)
    load_configuration = phase_configuration.for_loads()
    entries_by_value = _entries_by_value(load_configuration)
    terminals = _terminals_by_phase_letter(load_configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)
    owner_by_bar, conflicting_bars = _bar_owners(catalog, selected)

    total = len(loads)
    processed = 0
    shapes: list[str] = []
    entries: list[str] = []
    used_names: dict[str, str] = {}
    # Duas fontes podem ter o mesmo CODIGO de carga. Sem o desempate, a segunda
    # seria descartada inteira mais abaixo, por nome já usado.
    load_suffixes = loads.name_suffixes
    exported = 0
    exported_delta = 0
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
        if node_error is None and entry.is_delta and phase_count == 1:
            node_error = _single_phase_delta_error(entry)
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
        correction = correction_for(demand_correction, owner_id, DemandScope.CARGA)
        if correction is not None:
            series = [correction.scale(active, reactive) for active, reactive in series]

        registry_directive, zip_notes = _registry_zip_directive(group, zip_cutoff)
        for note in zip_notes:
            report.add(load_id, note, discarded=False)
        load_directive = registry_directive or model_directive

        base_name = sanitize_dss_name(loads.codes[load_index])
        if not base_name:
            base_name = sanitize_dss_name(load_id)
            report.add(
                load_id,
                "CODIGO vazio ou sem caracteres válidos; o nome da carga usou "
                "o CARGA_ID",
                discarded=False,
            )
        # O sufixo entra ANTES do infixo de fases, para que o nome continue
        # terminando em "-3F-D" e as regras de leitura do fluxo sigam valendo.
        if load_suffixes is not None and load_suffixes[load_index]:
            base_name = f"{base_name}_{load_suffixes[load_index]}"
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

        if not entry.valid_for_transformer:
            report.add(
                load_id,
                f"FASES2 '{entry.fases2}' inválido para trafo (só monofásico "
                "fase-neutro ou trifásico); exportada como o Interplan a modela",
                discarded=False,
            )
        bus = bus_name(bar_index)
        delta = entry.is_delta
        connection_type = loads.connection_types[load_index].strip()
        if phase_count == 3 and (connection_type == DELTA_CONNECTION_TYPE) != delta:
            report.add(
                load_id,
                f"TIPO_LIG '{connection_type or '<vazio>'}' discorda do FASES2 "
                f"'{entry.fases2}' ({entry.name or '?'}); valeu o FASES2, como no "
                f"Interplan: ligada em {'delta' if delta else 'estrela'}",
                discarded=False,
            )
        if delta and phase_count == 3:
            # Os terminais das três fases já foram exigidos por _phase_nodes,
            # então o par de cada letra sempre resolve. Na monofásica entre
            # fases o nó já é o par do próprio DSS (``1.2``).
            nodes = tuple(
                ".".join(terminals[phase] for phase in _DELTA_PAIR_BY_PHASE[letter])
                for letter in letters
            )
        if delta:
            connection = "delta"
            voltage = _format(nominal_voltage)
        else:
            connection = "wye"
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
                f" conn={connection}"
                f" kV={voltage}"
                f" {load_directive} kW=1 kvar=1"
                f" daily={shape_name}"
                f" class={phase_count}"
            )
            used_names[name] = load_id
        exported += 1
        if registry_directive is not None:
            registry_zip_count += 1
        uses_zipv = uses_zipv or load_directive.startswith("model=8")
        if delta:
            exported_delta += 1

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Exportação cancelada.")
    if progress is not None:
        progress(total, total)
    # O ZIPV só vale entre vminpu e vmaxpu; fora da faixa a carga vira
    # impedância constante. Sem os limites ativos a faixa é 0,95–1,05, e o
    # modelo do cadastro seria ignorado justamente nas barras mais baixas.
    if registry_zip_count and (
        load_settings is None or not load_settings.voltage_limits_enabled
    ):
        report.add(
            "MODELO_CARGA_SIZ",
            f"{registry_zip_count:n} carga(s) com o modelo ZIP do cadastro, mas "
            "os limites de tensão das cargas estão desligados: o OpenDSS só "
            "aplica o ZIPV entre 0,95 e 1,05 pu. Ative-os nas Configurações "
            "do OpenDSS (o Preset Interplan usa 0,5 a 1,5)",
            discarded=False,
        )

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
        *(
            (
                "! kV e a tensao de fase do circuito (VNOM de linha dividida por "
                "raiz de 3)",
            )
            if exported_delta == 0 or exported_delta < exported
            else ()
        ),
        *(
            (
                "! FASES2 de LIGACAO delta (fases2_carga.json) e ligada entre "
                "fases, como no Interplan: conn=delta e kV de linha (VNOM)",
                "! -D, -E e -F ocupam os pares DE, EF e FD, com PD, PE e PF do "
                "patamar",
            )
            if exported_delta
            else ()
        ),
        *(
            (
                "! ZIPV: pesos Z/I/P da ativa, depois da reativa, e a tensao "
                "de corte em pu",
            )
            if uses_zipv
            else ()
        ),
        *(
            (
                "! Cargas com MODELO_CARGA_SIZ no cadastro usam o ZIPV dele, "
                "igual na ativa e na reativa",
            )
            if registry_zip_count
            else ()
        ),
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
    balanced_entry = _balanced_three_phase_entry(configuration)
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
        if entry is None and raw_phases.strip() in UNSPECIFIED_GENERATOR_PHASES:
            # Mesma regra de calculate_generator_demands, que já repartiu a
            # potência pelas três fases.
            entry = balanced_entry
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
        "! Potencia negativa representa geracao; qmult so nao e zero quando o "
        "cadastro informa Q1..Q4",
        "! model=1: potencia constante, como os geradores do Interplan",
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

def _energy_primary_kv(
    rated: float | None,
    expected: float,
    report: _ExportReport,
    load_id: str,
) -> float:
    """O ``VPRIMARIO`` da UTC, se for da ligação; senão a tensão do circuito."""

    if rated is None:
        return expected
    if abs(rated - expected) <= ENERGY_PRIMARY_VOLTAGE_TOLERANCE * expected:
        return rated
    report.add(
        load_id,
        f"VPRIMARIO {_format(rated)} kV longe dos {_format(expected)} kV da "
        "ligação; o trafo usou a tensão do circuito",
        discarded=False,
    )
    return expected


def _energy_secondary_nodes(
    letters: tuple[str, ...] | None,
    three_phase: bool,
    legs: dict[str, int],
    terminals: dict[str, str],
) -> tuple[tuple[int, ...], str | None]:
    """Nós da barra secundária de uma carga e, se for o caso, o porquê do ajuste.

    Trifásico: os nós das próprias fases. Split-phase: as pernas são as fases do
    secundário (``FASES_BT2``); a monofásica vai para a sua perna e a de duas ou
    três fases fica entre as pernas.
    """

    if three_phase:
        if letters and all(letter in terminals for letter in letters):
            return tuple(sorted(int(terminals[letter]) for letter in letters)), None
        return (1, 2, 3), "fases inválidas; ligada como trifásica"
    if letters and len(letters) == 1:
        if letters[0] in legs:
            return (legs[letters[0]],), None
        return (1,), f"fase {letters[0]} não existe no secundário; ligada na perna 1"
    if not letters or set(letters) != set(legs):
        shown = "".join(letters or ()) or "<vazia>"
        return (1, 2), f"fases {shown} num secundário de duas pernas; ligada entre as pernas"
    return (1, 2), None


def build_energy_export(
    catalog: CircuitCatalogModel,
    loads: LoadModel,
    energy: EnergyDemandModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    reserved_names: frozenset[str] = frozenset(),
    load_settings: OpenDssLoadSettings | None = None,
    demand_correction: DemandCorrections | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssEnergyExportResult:
    """Gera os elementos do modo de cargas Energia, separados por arquivo.

    Para cada CARGA com resultado no ``EnergyDemandModel``::

        barra MT ──[TRF-<CARGA>]── BT_<CARGA> ── Load de cada UC, IP e GD

    Os arquivos seguem a convenção do modo Interplan: o trafo vai para
    ``trafosmonofasicos.dss`` ou ``trafostrifasicos.dss``; a UC e a GD vão
    para o ``cargas*.dss`` e o ``geradores*.dss`` do ``phases=`` da ``Load`` —
    ``phases=1`` no mono, inclusive a ligada entre fases —; a IP vai para
    ``iluminacaopublica.dss``. Cada ``LoadShape`` fica no arquivo da ``Load``.

    O trafo é ideal e segue a topologia do ``agregar_trafos_dss``:

    - trifásico (CARGA com três fases): D-Y, dois enrolamentos, kV de linha;
    - monofásico (uma ou duas fases na MT): split-phase do OpenDSS, três
      enrolamentos, primário fase-neutro ou entre fases e o secundário em duas
      pernas de V/2 com o neutro central aterrado (``BT.1.0`` e ``BT.0.2``).

    Cada UC é **uma** ``Load``: fase-neutro em estrela, entre fases em delta,
    trifásica equilibrada. ``kW=1 kvar=1`` com a potência de cada patamar no
    ``LoadShape``, como nas cargas do Interplan; a GD é ``Load`` negativa com
    ``model=1``. A carga da própria CARGA (``MODELO_CARGA``) não sai: o modo
    Energia a substitui pelas UCs, e ``replaced_load_indices`` diz quais CARGAs
    o arquivo de cargas deve pular.

    Uma CARGA cujo trafo não dá para montar (sem VNOM, fases ou tensão do
    secundário) **segue no modelo do Interplan**, com o motivo no relatório:
    perder a carga em silêncio seria pior que usar a agregada.

    ``demand_correction`` multiplica, patamar a patamar, os níveis das UCs e da
    IP dos grupos corrigidos (ver :func:`build_load_export`). A GD não muda.
    """

    from .energy_demand import STATUS_NO_CURVE, STATUS_NO_GD_CURVE
    from .transformer_consumers import CONSUMER_KIND_IP

    if energy.consumers.loads is not loads:
        raise ValueError("O cálculo do modo Energia pertence a outras cargas.")
    model_directive = (
        "model=1" if load_settings is None else load_settings.load_model_directive()
    )
    selected = _selected_indices(catalog, circuit_indices)
    owner_by_bar, _ = _bar_owners(catalog, selected)
    terminals = _terminals_by_phase_letter(phase_configuration)
    # O primário lê o FASES2 da CARGA; UCs e GD seguem o fases2.json.
    load_configuration = phase_configuration.for_loads()
    bus_name = bus_namer(catalog)
    taken_buses = {bus_name(bar_index).casefold() for bar_index in owner_by_bar}
    used_transformers: set[str] = set()
    used_names: dict[str, str] = {}
    suffixes = loads.name_suffixes
    report = _ExportReport()
    # Trafos por contagem de fases do primário (split-phase é monofásico).
    transformers: dict[int, list[str]] = {count: [] for count in _TRANSFORMER_FILES}
    # Por arquivo de destino: (LoadShape, Load), na ordem de emissão. O perfil
    # fica no mesmo arquivo da Load que o usa.
    groups: dict[str, list[tuple[str, str]]] = {}
    consumer_counts: dict[int, int] = {}
    generation_counts: dict[int, int] = {}
    lighting_count = 0
    voltage_commands: list[str] = []
    replaced: set[int] = set()
    exported = load_count = generator_count = 0
    without_consumers = 0
    without_power = 0
    lost: dict[str, int] = {}
    sqrt3 = math.sqrt(3.0)
    total = len(energy.transformers)

    def unique(name: str, base: str, taken) -> str:  # noqa: ANN001
        if name.casefold() not in taken and name not in reserved_names:
            return name
        candidate = f"{name}_{base}"
        index = 2
        while candidate.casefold() in taken or candidate in reserved_names:
            candidate = f"{name}_{base}_{index}"
            index += 1
        return candidate

    def emit(name: str, nodes: tuple[int, ...], bus: str, vfn: float, vll: float,
             active: Sequence[float], reactive: Sequence[float], directive: str,
             sign: int) -> tuple[str, str, int]:
        """O ``LoadShape``, a ``Load`` e o ``phases`` dela, que escolhe o arquivo."""

        if len(nodes) == 1:
            phases, bus_spec, connection, voltage = 1, f"{bus}.{nodes[0]}.0", "wye", vfn
        elif len(nodes) == 2:
            phases, bus_spec, connection, voltage = 1, f"{bus}.{nodes[0]}.{nodes[1]}", "delta", vll
        else:
            phases, bus_spec, connection, voltage = 3, f"{bus}.1.2.3", "wye", vll
        shape_name = f"{LOAD_SHAPE_PREFIX}{name}"
        shape = (
            f"New LoadShape.{shape_name}"
            f" npts={LOAD_PATTERN_COUNT}"
            " interval=1"
            f" mult=[{' '.join(_format_pattern(value) for value in active)}]"
            f" qmult=[{' '.join(_format_pattern(value) for value in reactive)}]"
        )
        entry = (
            f"New Load.{name}"
            f" phases={phases}"
            f" bus1={bus_spec}"
            f" conn={connection}"
            f" kV={_format(voltage)}"
            f" {directive} kW=1 kvar=1"
            f" daily={shape_name}"
            f" class={sign * phases}"
        )
        return shape, entry, phases

    for load_index, demand in enumerate(energy.transformers):
        if cancel_check is not None and load_index % 1_024 == 0 and cancel_check():
            raise InterruptedError("Exportação cancelada.")
        if progress is not None and load_index % 1_000 == 0:
            progress(load_index, total)
        bar_index = int(loads.bar_indices[load_index])
        owner = owner_by_bar.get(bar_index)
        if owner is None:
            continue
        if demand is None:
            without_consumers += 1
            continue
        circuit_id, nominal_kv = owner
        load_id = loads.load_ids[load_index]
        record = energy.consumers.record(load_index)
        spec = record.transformer
        for item in demand.consumers:
            if item.status in (STATUS_NO_CURVE, STATUS_NO_GD_CURVE):
                lost[item.status] = lost.get(item.status, 0) + 1
        consumers = [item for item in demand.consumers if item.load_levels]
        generators = [item for item in demand.generators if item.levels]
        if not consumers and not generators:
            # Todas as UCs sem consumo, inativas ou sem curva: no modo Energia
            # a CARGA não tem carga, e a agregada do Interplan não a substitui.
            replaced.add(load_index)
            without_power += 1
            continue
        if any(
            len(levels) != LOAD_PATTERN_COUNT
            for levels in (
                *(item.load_levels for item in consumers),
                *(item.levels for item in generators),
            )
        ):
            report.add(load_id, "agenda de patamares incompleta; a CARGA seguiu no modelo do Interplan")
            continue
        if nominal_kv is None:
            report.add(
                load_id,
                f"circuito {circuit_id} sem VNOM numérica positiva; a CARGA seguiu "
                "no modelo do Interplan",
            )
            continue
        primary = load_configuration.phase_letters_for_value(loads.phases[load_index])
        primary_entry = load_configuration.entry_for_value(loads.phases[load_index])
        # 7/8/9: monofásico entre fases, com o par no DSS da tabela da CARGA.
        # Trafo assim não existe na rede; ele é montado como o Interplan o
        # modela, mas avisado quando a CARGA chega a ser exportada (abaixo).
        primary_pair = (
            primary_entry.dss
            if primary_entry is not None
            and primary_entry.is_delta
            and len(primary or ()) == 1
            and _single_phase_delta_error(primary_entry) is None
            else None
        )
        if not primary or any(letter not in terminals for letter in primary):
            report.add(
                load_id,
                f"FASES2 '{loads.phases[load_index].strip() or '<vazio>'}' da CARGA "
                "sem fases D/E/F; a CARGA seguiu no modelo do Interplan",
            )
            continue
        if spec is None or spec.secondary_v is None:
            report.add(
                load_id,
                "trafo sem tensão do secundário (VSECUNDA/VLINHASEC); a CARGA "
                "seguiu no modelo do Interplan",
            )
            continue
        if primary_entry is not None and not primary_entry.valid_for_transformer:
            report.add(
                load_id,
                f"FASES2 '{primary_entry.fases2}' inválido para trafo (só "
                "monofásico fase-neutro ou trifásico); trafo montado como o "
                "Interplan o modela",
                discarded=False,
            )

        base = sanitize_dss_name(loads.codes[load_index]) or sanitize_dss_name(load_id)
        if suffixes is not None and suffixes[load_index]:
            base = f"{base}_{suffixes[load_index]}"
        transformer = unique(f"{ENERGY_TRANSFORMER_PREFIX}{base}", sanitize_dss_name(load_id), used_transformers)
        used_transformers.add(transformer.casefold())
        bus = unique(f"{ENERGY_SECONDARY_BUS_PREFIX}{base}", sanitize_dss_name(load_id), taken_buses)
        taken_buses.add(bus.casefold())

        kva = spec.kva
        if not kva or kva <= 0:
            peak = sum(max(item.load_levels) for item in consumers) + sum(
                max(item.levels) for item in generators
            )
            kva = max(peak / ENERGY_SIZING_POWER_FACTOR, 1.0)
            report.add(
                load_id,
                f"trafo sem kVA no cadastro; o trafo ideal usou {_format(kva)} kVA",
                discarded=False,
            )
        vll = spec.secondary_v / 1000.0
        primary_bus = bus_name(bar_index)
        r, x, k = (
            _format(ENERGY_TRANSFORMER_R_PERCENT),
            _format(ENERGY_TRANSFORMER_X_PERCENT),
            _format(kva),
        )
        three_phase = len(primary) == 3
        if three_phase:
            primary_kv = _energy_primary_kv(spec.primary_kv, nominal_kv, report, load_id)
            vfn = vll / sqrt3
            nodes = ".".join(terminals[letter] for letter in primary)
            transformers[3].append(
                f"New Transformer.{transformer} phases=3 windings=2"
                f" buses=[{primary_bus}.{nodes} {bus}.1.2.3]"
                f" conns=[delta wye] kVs=[{_format(primary_kv)} {_format(vll)}]"
                f" kVAs=[{k} {k}] %Rs=[{r} {r}] XHL={x}"
                " %noloadloss=0 %imag=0 ppm_antifloat=0"
            )
            legs: dict[str, int] = {}
        else:
            if primary_pair is not None:
                primary_nodes = f".{primary_pair}"
                expected = nominal_kv
            elif len(primary) == 1:
                primary_nodes = f".{terminals[primary[0]]}.0"
                expected = nominal_kv / sqrt3
            else:
                primary_nodes = f".{terminals[primary[0]]}.{terminals[primary[1]]}"
                expected = nominal_kv
            primary_kv = _energy_primary_kv(spec.primary_kv, expected, report, load_id)
            vfn = vll / 2.0
            secondary = phase_configuration.phase_letters_for_value(spec.secondary_phases) or ()
            if len(secondary) != 2:
                report.add(
                    load_id,
                    f"trafo monofásico com secundário "
                    f"'{''.join(secondary) or spec.secondary_phases or '<vazio>'}'; "
                    "modelado como split-phase DE",
                    discarded=False,
                )
                secondary = ("D", "E")
            legs = {secondary[0]: 1, secondary[1]: 2}
            transformers[1].append(
                f"New Transformer.{transformer} phases=1 windings=3"
                f" buses=[{primary_bus}{primary_nodes} {bus}.1.0 {bus}.0.2]"
                f" conns=[wye wye wye]"
                f" kVs=[{_format(primary_kv)} {_format(vfn)} {_format(vfn)}]"
                f" kVAs=[{k} {k} {k}] %Rs=[{r} {r} {r}]"
                f" XHL={x} XHT={x} XLT={x}"
                " %noloadloss=0 %imag=0 ppm_antifloat=0"
            )
        voltage_commands.append(f"SetkVBase bus={bus} kVLN={_format(vfn)}")

        consumer_by_key = {
            (item.kind, item.consumer_id): item for item in record.consumers
        }
        for item in consumers:
            consumer = item.consumer
            prefix = "IP" if consumer.kind == CONSUMER_KIND_IP else f"UC-{consumer.kind}"
            name = unique(
                f"{prefix}-{sanitize_dss_name(consumer.consumer_id)}",
                base,
                {key.casefold() for key in used_names},
            )
            nodes, note = _energy_secondary_nodes(
                phase_configuration.phase_letters_for_value(consumer.phases),
                three_phase,
                legs,
                terminals,
            )
            if note is not None:
                report.add(load_id, f"{consumer.kind} {consumer.consumer_id}: {note}", discarded=False)
            active_levels, reactive_levels = item.load_levels, item.reactive_levels
            scope = energy_scope_for_kind(consumer.kind)
            correction = (
                None
                if scope is None
                else correction_for(demand_correction, circuit_id, scope)
            )
            if correction is not None:
                active_levels, reactive_levels = correction.scale(
                    active_levels, reactive_levels
                )
            shape, entry, phases = emit(
                name, nodes, bus, vfn, vll, active_levels, reactive_levels,
                model_directive, 1,
            )
            if consumer.kind == CONSUMER_KIND_IP:
                destination = PUBLIC_LIGHTING_FILENAME
                lighting_count += 1
            else:
                destination = _LOAD_FILES[phases][0]
                consumer_counts[phases] = consumer_counts.get(phases, 0) + 1
            groups.setdefault(destination, []).append((shape, entry))
            used_names[name] = load_id
            load_count += 1
        for item in generators:
            owner_consumer = consumer_by_key.get((item.generator.kind, item.generator.consumer_id))
            raw = "" if owner_consumer is None else owner_consumer.phases
            letters = (
                ("D", "E", "F")
                if raw.strip() in UNSPECIFIED_GENERATOR_PHASES
                else phase_configuration.phase_letters_for_value(raw)
            )
            nodes, note = _energy_secondary_nodes(letters, three_phase, legs, terminals)
            if note is not None:
                report.add(load_id, f"GD {item.generator.generator_id}: {note}", discarded=False)
            name = unique(
                f"GD-{item.generator.kind}-{sanitize_dss_name(item.generator.generator_id)}",
                base,
                {key.casefold() for key in used_names},
            )
            # 0.0 - valor, e não -valor: o patamar sem sol sairia "-0.000000".
            shape, entry, phases = emit(
                name, nodes, bus, vfn, vll, tuple(0.0 - value for value in item.levels),
                (0.0,) * len(item.levels), "model=1", -1,
            )
            groups.setdefault(_GENERATOR_FILES[phases][0], []).append((shape, entry))
            generation_counts[phases] = generation_counts.get(phases, 0) + 1
            used_names[name] = load_id
            generator_count += 1
        replaced.add(load_index)
        exported += 1

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Exportação cancelada.")
    if progress is not None:
        progress(total, total)
    if without_consumers:
        report.add(
            "modo Energia",
            f"{without_consumers:n} CARGA(s) sem UC conhecida seguiram no modelo do Interplan",
            discarded=False,
        )
    if without_power:
        report.add(
            "modo Energia",
            f"{without_power:n} CARGA(s) só com UCs sem potência (sem consumo, "
            "inativas ou sem curva) ficaram sem carga",
            discarded=False,
        )
    for status, count in sorted(lost.items()):
        report.add(
            "modo Energia",
            f"{count:n} UC(s) com status {status} ficaram sem potência",
            discarded=False,
        )

    circuits_line = "! Circuitos: " + ", ".join(
        sanitize_dss_name(catalog.definition(index).circuit_id) for index in selected
    )
    profile_line = (
        "! kW=1 e kvar=1 sao fixos: o LoadShape traz D x curva media na HORARIO_REF "
        "de cada patamar"
    )

    def joined(header: Sequence[str], lines: Sequence[str]) -> str:
        text = "\n".join((*header, "", *lines))
        return f"{text}\n" if lines else text

    def load_block(pairs: Sequence[tuple[str, str]]) -> list[str]:
        # Os perfis antes das Load, como nos arquivos de cargas do Interplan.
        shapes = [shape for shape, _ in pairs]
        entries = [entry for _, entry in pairs]
        return [*shapes, *(("",) if shapes and entries else ()), *entries]

    own_files: list[tuple[str, str]] = []
    if exported:
        for count, (filename, label) in _TRANSFORMER_FILES.items():
            own_files.append(
                (
                    filename,
                    joined(
                        (
                            f"! Trafos {label} do modo de cargas Energia: um trafo "
                            "por CARGA, com as UCs no secundario",
                            "! Trafo ideal (%R e X de 0,001 %, sem perdas): a carga "
                            "vista pela MT e a soma das UCs",
                            f"! {ENERGY_SECONDARY_BUS_PREFIX}<CARGA> e uma barra "
                            "ficticia, sem coordenada: o mapa nao muda",
                            "! UCs nos cargas*.dss, IP em "
                            f"{PUBLIC_LIGHTING_FILENAME} e GD nos geradores*.dss",
                            circuits_line,
                        ),
                        transformers[count],
                    ),
                )
            )
        own_files.append(
            (
                PUBLIC_LIGHTING_FILENAME,
                joined(
                    (
                        "! Iluminacao publica do modo de cargas Energia, no "
                        "secundario dos trafos",
                        profile_line,
                        circuits_line,
                    ),
                    load_block(groups.get(PUBLIC_LIGHTING_FILENAME, ())),
                ),
            )
        )

    sections: list[tuple[str, str]] = []
    for files, title in (
        (_LOAD_FILES, "UCs no secundario dos trafos"),
        (_GENERATOR_FILES, "GD no secundario dos trafos; Load negativa, model=1, FP 1"),
    ):
        for filename, _ in files.values():
            pairs = groups.get(filename)
            if pairs:
                sections.append(
                    (
                        filename,
                        joined(
                            (
                                f"! --- Modo Energia: {title} "
                                f"({ENERGY_SECONDARY_BUS_PREFIX}<CARGA>) ---",
                                profile_line,
                                circuits_line,
                            ),
                            load_block(pairs),
                        ),
                    )
                )

    return OpenDssEnergyExportResult(
        own_files=tuple(own_files),
        sections=tuple(sections),
        exported_count=exported,
        load_count=load_count,
        generator_count=generator_count,
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        used_names=frozenset(used_names),
        replaced_load_indices=frozenset(replaced),
        voltage_base_commands=tuple(voltage_commands),
        transformer_counts=tuple(
            (count, len(lines)) for count, lines in transformers.items()
        ),
        consumer_counts=tuple(sorted(consumer_counts.items())),
        lighting_count=lighting_count,
        generation_counts=tuple(sorted(generation_counts.items())),
    )


def _master_base_name(definition) -> str:  # noqa: ANN001
    """Nome do circuito no OpenDSS, com o CIRC_ID de reserva."""

    return sanitize_dss_name(definition.code) or sanitize_dss_name(
        definition.circuit_id
    )


def _source_voltage_profile(
    definition,  # noqa: ANN001
) -> tuple[tuple[float, ...] | None, str | None]:
    """VSE1..VSE4 em pu, ou ``None`` quando a fonte fica em 1 pu o dia todo.

    Vazio vale 1 pu, como na ausência da coluna. Um valor ilegível descarta o
    perfil inteiro — meio perfil deixaria um patamar com a tensão errada sem
    aviso — e devolve o motivo para o relatório.
    """

    values: list[float] = []
    for position, raw in enumerate(definition.source_voltages, start=1):
        text = str(raw).strip()
        if not text:
            values.append(1.0)
            continue
        parsed = parse_number(text)
        if parsed is None or parsed <= 0.0:
            return None, (
                f"VSE{position} '{text}' inválida; a fonte ficou em 1 pu em "
                "todos os patamares"
            )
        values.append(parsed)
    if all(abs(value - 1.0) <= _SOURCE_VOLTAGE_TOLERANCE for value in values):
        return None, None
    return tuple(values), None


def build_capacitor_export(
    catalog: CircuitCatalogModel,
    capacitors: CapacitorModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    reserved_names: frozenset[str] = frozenset(),
    include_bar_indices: frozenset[int] | None = None,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssCapacitorExportResult:
    """Emite ``capacitores.dss``: cada banco vira ``Load`` de reativo negativo.

    O banco é modelado como carga porque o projeto inteiro fala esse dialeto:
    uma ``Load`` por fase, tensão de fase, potência no ``LoadShape``. A
    compensação entra por ``qmult`` negativo — ``kW=0`` com ``mult`` zerado anula
    a potência ativa, e ``kvar=1`` deixa o perfil entregar o valor absoluto.

    ``model=2`` (impedância constante) porque é o que um banco de capacitores é:
    o reativo entregue cai com o quadrado da tensão, como no Interplan. Em
    potência constante o banco compensaria a mais justamente nas barras de
    tensão baixa. O ``qmult`` continua escalando o kvar nominal de cada patamar.

    ``Q1..Q4`` do cadastro é a potência do **banco inteiro**, e não por fase como
    o ``QD``/``QE``/``QF`` das cargas, então é dividida pelo número de fases: sem
    isso um banco de 600 kvar injetaria 1800.

    ``include_bar_indices`` restringe a exportação às barras informadas. É o que
    a rede simplificada usa para deixar de fora os bancos que caíram dentro de um
    ramal, cuja compensação ainda não tem tratamento definido.

    Um banco sai completo ou não sai: qualquer problema em uma das fases descarta
    todas, pela mesma razão das cargas.
    """

    selected = _selected_indices(catalog, circuit_indices)
    terminals = _terminals_by_phase_letter(phase_configuration)
    report = _ExportReport()
    bus_name = bus_namer(catalog)
    owner_by_bar, _ = _bar_owners(catalog, selected)

    total = len(capacitors)
    processed = 0
    shapes: list[str] = []
    entries: list[str] = []
    used_names: dict[str, str] = {}
    exported = 0

    for capacitor_index in range(total):
        if cancel_check is not None and processed % 4_096 == 0 and cancel_check():
            raise InterruptedError("Exportação cancelada.")
        processed += 1
        if progress is not None and processed % 1_000 == 0:
            progress(processed, total)

        bar_index = int(capacitors.bar_indices[capacitor_index])
        owner = owner_by_bar.get(bar_index)
        if owner is None:
            # O banco está fora dos circuitos selecionados.
            continue
        owner_id, nominal_voltage = owner
        record = capacitors.record(capacitor_index)
        capacitor_id = record.capacitor_id

        if include_bar_indices is not None and bar_index not in include_bar_indices:
            # Diagnosticado, nunca silencioso: é o que permite medir o efeito de
            # deixar a compensação de fora enquanto o tratamento não é decidido.
            report.add(
                capacitor_id,
                "banco em barra removida pela redução, isto é, dentro de um "
                "ramal; a compensação reativa dele não foi representada",
            )
            continue

        # A fase vem em FASES, a string de letras do cadastro, e não no código
        # numérico FASES2 das cargas. O N de DEFN é ignorado, como no resto do
        # exportador.
        raw_phases = record.phases.strip().upper()
        present = tuple(
            letter for letter in raw_phases if letter in _PATTERN_COLUMNS_BY_PHASE
        )
        letters = _phase_letters(raw_phases, len(present)) if present else None
        if letters is None:
            report.add(
                capacitor_id,
                f"FASES '{record.phases.strip() or '<vazio>'}' não resolve fases "
                "distintas entre D, E e F",
            )
            continue
        missing = next(
            (letter for letter in letters if letter not in terminals),
            None,
        )
        if missing is not None:
            report.add(
                capacitor_id,
                f"fase '{missing}' sem terminal DSS: nenhuma entrada monofásica "
                "de fases2.json a define",
            )
            continue

        if nominal_voltage is None:
            report.add(capacitor_id, f"circuito {owner_id} sem VNOM numérica positiva")
            continue

        phase_count = len(letters)
        reactive: list[float] = []
        invalid_column: str | None = None
        for position, value in enumerate(record.reactive_powers, start=1):
            parsed = parse_number(value)
            if parsed is None:
                invalid_column = f"Q{position}"
                break
            # `-0.0` seria formatado como "-0.000000" no arquivo; zero
            # não tem sinal para quem lê o .dss.
            reactive.append(-parsed / phase_count if parsed else 0.0)
        if invalid_column is not None:
            report.add(
                capacitor_id,
                f"{invalid_column} não numérica; o banco inteiro foi descartado",
            )
            continue

        base_name = sanitize_dss_name(record.code)
        if not base_name:
            base_name = sanitize_dss_name(capacitor_id)
            report.add(
                capacitor_id,
                "CODIGO do capacitor vazio ou sem caracteres válidos; o nome das "
                "cargas usou o CAPAC_ID",
                discarded=False,
            )
        if not base_name:
            report.add(
                capacitor_id, "CAPAC_ID sem caracteres válidos para um nome DSS"
            )
            continue

        phase_names = [
            f"{CAPACITOR_NAME_PREFIX}{base_name}-{phase_count}F-{letter}"
            for letter in letters
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
                f"pelo capacitor {used_names[taken]}"
                if taken in used_names
                else "por uma carga ou gerador"
            )
            report.add(
                capacitor_id,
                f"nome '{taken}' já usado {owner_note}; o banco inteiro foi "
                "descartado",
            )
            continue

        bus = bus_name(bar_index)
        voltage = _format(phase_voltage_kv(nominal_voltage))
        zeros = " ".join(_format_pattern(0.0) for _ in range(LOAD_PATTERN_COUNT))
        profile = " ".join(_format_pattern(value) for value in reactive)
        for name, letter in zip(phase_names, letters):
            node = terminals[letter]
            shape_name = f"{LOAD_SHAPE_PREFIX}{name}"
            shapes.append(
                f"New LoadShape.{shape_name}"
                f" npts={LOAD_PATTERN_COUNT}"
                " interval=1"
                f" mult=[{zeros}]"
                f" qmult=[{profile}]"
            )
            entries.append(
                f"New Load.{name}"
                " phases=1"
                f" bus1={bus}.{node}"
                " conn=wye"
                f" kV={voltage}"
                " model=2 kW=0 kvar=1"
                f" daily={shape_name}"
                f" class={phase_count}"
            )
            used_names[name] = capacitor_id
        exported += 1

    if progress is not None:
        progress(total, total)

    header = (
        "! Bancos de capacitores exportados pelo Visualizador de Circuitos Eletricos",
        "! Cada banco vira uma Load monofasica por fase, com reativo negativo",
        "! model=2: impedancia constante, o reativo varia com o quadrado da tensao",
        "! kW=0 e mult zerado: o banco nao consome potencia ativa",
        "! kvar=1 e fixo: a compensacao de cada patamar vem do qmult do LoadShape",
        "! Q do cadastro e do banco inteiro, entao e dividido pelo numero de fases",
        "! kV e a tensao de fase do circuito (VNOM de linha dividida por raiz de 3)",
        "! Circuitos: "
        + ", ".join(
            sanitize_dss_name(catalog.definition(index).circuit_id)
            for index in selected
        ),
        "",
    )
    body = (*shapes, *(("",) if shapes and entries else ()), *entries)
    text = "\n".join((*header, *body))
    if body:
        text += "\n"
    return OpenDssCapacitorExportResult(
        text=text,
        exported_count=exported,
        discarded_count=report.discarded,
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        used_names=frozenset(used_names),
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


EXPORT_DIRECTORY_SUFFIX = "_dss"
_STAGING_PREFIX = ".dss-export-"


def opendss_export_directory_name(
    catalog: CircuitCatalogModel,
    circuit_index: int,
) -> str:
    """Nome sugerido da pasta que recebe a exportação de um circuito.

    Usa a mesma base do master, para a pasta e o ``<base>_Master.dss`` dentro
    dela terem o mesmo nome.
    """

    base = _master_base_name(catalog.definition(int(circuit_index)))
    return f"{base}{EXPORT_DIRECTORY_SUFFIX}"


def stage_export_directory(
    base: str | os.PathLike[str],
    files: Iterable[tuple[str, str]],
    cancel_check: Callable[[], bool] | None = None,
) -> Path:
    """Grava os arquivos numa pasta temporária dentro de ``base``.

    A pasta temporária fica no mesmo volume do destino para que a troca final
    em :func:`commit_export_directory` seja um ``os.replace`` instantâneo. Em
    qualquer falha, ou cancelamento, ela é removida antes de propagar o erro.
    """

    base_path = Path(base)
    base_path.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=base_path))
    try:
        for filename, text in files:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Exportação cancelada.")
            (staging / filename).write_text(text, encoding="utf-8")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def commit_export_directory(
    staging: str | os.PathLike[str],
    final: str | os.PathLike[str],
) -> Path:
    """Substitui ``final`` inteira pela pasta preparada, com rollback.

    A pasta antiga só é apagada depois que a nova está no lugar; se a troca
    falhar, ela volta ao nome original e a temporária é descartada.
    """

    staging_path = Path(staging)
    final_path = Path(final)
    backup: Path | None = None
    try:
        if final_path.exists():
            backup = final_path.with_name(
                f".{final_path.name}.backup-{uuid.uuid4().hex}"
            )
            os.replace(final_path, backup)
        os.replace(staging_path, final_path)
    except BaseException:
        if backup is not None and backup.exists() and not final_path.exists():
            os.replace(backup, final_path)
        shutil.rmtree(staging_path, ignore_errors=True)
        raise
    if backup is not None:
        if backup.is_dir():
            shutil.rmtree(backup, ignore_errors=True)
        else:
            backup.unlink(missing_ok=True)
    return final_path


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
    max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER,
    include_bar_indices: frozenset[int] | None = None,
    include_solve: bool = True,
    voltage_base_commands: Sequence[str] = (),
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

    A tensão da saída por patamar (``VSE1``..``VSE4``) vira um ``LoadShape``
    diário no ``Vsource``. Só sai quando algum patamar difere de 1 pu: com a
    fonte nominal o master continua idêntico ao de sempre.

    ``max_power_flow_iterations`` vira o ``Set MaxIter``. Sem ele o OpenDSS usa
    o próprio padrão, 15, que é baixo demais para alimentador longo e carregado:
    a solução é abandonada antes de terminar a primeira passada, e nem o laço de
    controle chega a rodar. Ver ``opendss_solution``.

    ``include_solve`` decide se o arquivo termina resolvendo. Ligado — o padrão,
    e o que toda exportação em disco quer — o master traz ``Set number`` com um
    passo por patamar e o ``Solve``, de modo que abri-lo no OpenDSS já produza
    resultado. Desligado, o arquivo só **monta** o circuito.

    Quem desliga é o fluxo de potência interno, e a razão é que ``Compile``
    **executa** o arquivo: o ``Solve`` do master resolveria os quatro patamares
    antes de ``run_power_flow`` resolver os seus. Além do tempo gasto duas
    vezes, o tap de um regulador sobrevive entre soluções, então o primeiro
    patamar do laço partiria do estado deixado pelo último patamar do master —
    e, num patamar que não converge, o OpenDSS aborta o laço de controle e esse
    tap emprestado é o que sobra. Medido num alimentador de 23.857 barras: os
    quatro patamares relatavam o mesmo tap, o do master.

    ``voltage_base_commands`` entram logo depois do ``calcvoltagebases``, que
    daria às barras secundárias do modo Energia a base da MT — os ``SetkVBase``
    precisam das barras já listadas e vencer o cálculo automático.

    ``text`` vazio indica que o master não pôde ser montado; o motivo está nos
    ``issues``.
    """

    selected = _selected_indices(catalog, circuit_indices)
    iterations = parse_max_power_flow_iterations(max_power_flow_iterations)
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
    from .project_topology import coupled_study_reason
    reason = coupled_study_reason(catalog, selected)
    if reason:
        report.add(definition.circuit_id, reason)
        return _empty_master(report, master_filename, buscoords_filename)
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
    source_profile, source_issue = _source_voltage_profile(definition)
    if source_issue is not None:
        report.add(definition.circuit_id, source_issue, discarded=False)
    if source_profile is not None:
        # O LoadShape só existe depois do New Circuit, e o daily= do Vsource
        # precisa dele já definido. No modo diário o OpenDSS multiplica a
        # tensão da fonte pelo perfil, um ponto por patamar como nas cargas.
        shape_name = f"{SOURCE_VOLTAGE_SHAPE_PREFIX}{base}"
        lines.extend(
            [
                f"New LoadShape.{shape_name}"
                f" npts={LOAD_PATTERN_COUNT}"
                " interval=1"
                f" mult=[{' '.join(_format_pattern(value) for value in source_profile)}]",
                f"Edit Vsource.source daily={shape_name}",
                "",
            ]
        )
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
            *voltage_base_commands,
            f"Set ControlMode={CONTROL_MODE}",
            f"Set MaxControlIter={MAX_CONTROL_ITER}",
            f"Set MaxIter={iterations}",
            "Set mode=daily",
            "Set stepsize=1h",
            *(
                (
                    # Um passo por patamar: o LoadShape tem npts=4 e interval
                    # de 1 hora.
                    f"Set number={LOAD_PATTERN_COUNT}",
                )
                if include_solve
                else ()
            ),
            "Set time=(0, 0)",
            *(("Solve",) if include_solve else ()),
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
    cables: CableModel | None,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    loads: LoadModel | None = None,
    patterns: LoadPatternModel | None = None,
    generator_updates: GeneratorUpdateModel | None = None,
    regulators: RegulatorModel | None = None,
    capacitors: CapacitorModel | None = None,
    load_settings: OpenDssLoadSettings | None = None,
    regulator_settings: OpenDssRegulatorSettings | None = None,
    max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER,
    line_parameter_mode: OpenDssLineParameterMode = OpenDssLineParameterMode.ORIGINAL,
    library_catalog: OpenDssLibraryCatalog | None = None,
    library_mappings: OpenDssLibraryMappings | None = None,
    include_solve: bool = True,
    energy: EnergyDemandModel | None = None,
    demand_correction: DemandCorrections | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssExportBundle:
    """Monta todos os arquivos de uma exportação.

    Arquivos que compartilham namespace no OpenDSS reservam nomes entre si:
    trechos e chaves em ``Line.*``; cargas, geradores e capacitores em
    ``Load.*``, nesta ordem de prioridade.
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

    ``include_solve`` apenas atravessa até :func:`build_master_export`, que
    documenta por que o fluxo de potência interno o desliga.

    ``energy`` liga o modo de cargas Energia: as CARGAs que ele assume viram
    trafo + UCs (ver :func:`build_energy_export` para os arquivos) e a carga
    agregada delas sai dos arquivos de cargas; os geradores MT delas saem dos
    de geradores — a GD já está no secundário. Sem ele, nada muda.

    ``demand_correction`` (circuito → fatores) multiplica os perfis das cargas
    dos grupos corrigidos; geradores, GD e capacitores nunca mudam. É o mesmo
    dicionário para exportação e fluxo, para que o fluxo resolva exatamente o
    que seria exportado.
    """

    if isinstance(line_parameter_mode, OpenDssLineParameterMode):
        normalized_line_mode = line_parameter_mode
    else:
        try:
            normalized_line_mode = OpenDssLineParameterMode(
                str(line_parameter_mode).strip().casefold()
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Modo de parâmetros de linha inválido: {line_parameter_mode!r}."
            ) from exc

    selected = _selected_indices(catalog, circuit_indices)
    regulator_result = (
        None
        if regulators is None
        else build_regulator_export(
            catalog,
            regulators,
            phase_configuration,
            selected,
            settings=regulator_settings,
            cancel_check=cancel_check,
            progress=progress,
        )
    )
    skip_segments = (
        frozenset()
        if regulator_result is None
        else regulator_result.replaced_segments
    )
    library_result: OpenDssLibraryExportResult | None = None
    if normalized_line_mode is OpenDssLineParameterMode.LIBRARY:
        missing_library_inputs: list[str] = []
        if library_catalog is None:
            missing_library_inputs.append("biblioteca de cabos e arranjos não informada")
        if library_mappings is None:
            missing_library_inputs.append("mapas de cabos e arranjos não informados")
        if missing_library_inputs:
            raise OpenDssLibraryExportError(missing_library_inputs)
        assert library_catalog is not None
        assert library_mappings is not None
        line_result, library_result = build_library_line_export(
            catalog,
            phase_configuration,
            selected,
            library_catalog,
            library_mappings,
            skip_segments=skip_segments,
            cancel_check=cancel_check,
            progress=progress,
        )
    else:
        if cables is None:
            raise ValueError("O modo original requer o modelo de cabos importado.")
        line_result = build_line_export(
            catalog,
            cables,
            phase_configuration,
            selected,
            skip_segments=skip_segments,
            cancel_check=cancel_check,
            progress=progress,
        )
    switch_result = build_switch_export(
        catalog,
        phase_configuration,
        selected,
        reserved_names=line_result.used_names,
        resolve_name_collisions=(
            normalized_line_mode is OpenDssLineParameterMode.LIBRARY
        ),
        cancel_check=cancel_check,
        progress=progress,
    )
    energy_result: OpenDssEnergyExportResult | None = None
    replaced_loads: frozenset[int] = frozenset()
    if energy is not None and loads is not None:
        energy_result = build_energy_export(
            catalog,
            loads,
            energy,
            phase_configuration,
            selected,
            load_settings=load_settings,
            demand_correction=demand_correction,
            cancel_check=cancel_check,
            progress=progress,
        )
        replaced_loads = energy_result.replaced_load_indices
    load_results: dict[int, OpenDssLoadExportResult] = {}
    # A identidade amarra os patamares às cargas exibidas, como no resto do
    # projeto: modelos de importações diferentes nunca se combinam.
    if loads is not None and patterns is not None and patterns.loads is loads:
        reserved: frozenset[str] = (
            frozenset() if energy_result is None else energy_result.used_names
        )
        kept_loads = (
            None
            if not replaced_loads
            else frozenset(range(len(loads))) - replaced_loads
        )
        for count in LOAD_PHASE_COUNTS:
            result = build_load_export(
                catalog,
                loads,
                patterns,
                phase_configuration,
                selected,
                phase_count=count,
                reserved_names=reserved,
                include_load_indices=kept_loads,
                load_settings=load_settings,
                demand_correction=demand_correction,
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
        ) | (frozenset() if energy_result is None else energy_result.used_names)
        kept_generators = None
        if replaced_loads:
            generator_loads = generator_updates.generators.load_indices
            kept_generators = frozenset(
                index
                for index in range(len(generator_updates.generators))
                if int(generator_loads[index]) not in replaced_loads
            )
        for count in GENERATOR_PHASE_COUNTS:
            result = build_generator_export(
                catalog,
                generator_updates,
                selected,
                phase_count=count,
                reserved_names=reserved,
                include_generator_indices=kept_generators,
                cancel_check=cancel_check,
                progress=progress,
            )
            reserved |= result.used_names
            generator_results[count] = result
    capacitor_result: OpenDssCapacitorExportResult | None = None
    if capacitors is not None:
        # Último da fila em ``Load.*``: cargas e geradores têm prioridade
        # por compatibilidade com o que já era exportado.
        reserved = frozenset(
            name
            for result in (*load_results.values(), *generator_results.values())
            for name in result.used_names
        ) | (frozenset() if energy_result is None else energy_result.used_names)
        capacitor_result = build_capacitor_export(
            catalog,
            capacitors,
            phase_configuration,
            selected,
            reserved_names=reserved,
            cancel_check=cancel_check,
            progress=progress,
        )
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
        capacitors=capacitor_result,
        library=library_result,
        energy=energy_result,
    )
    # O master vem por último: ele chama os arquivos de elementos e por isso
    # precisa saber quais existem.
    exported_energy = energy_result is not None and energy_result.exported_count > 0
    master_result = build_master_export(
        catalog,
        selected,
        redirects=[name for name, _ in bundle.element_files],
        load_settings=(
            load_settings
            if load_results or generator_results or capacitor_result or exported_energy
            else None
        ),
        max_power_flow_iterations=max_power_flow_iterations,
        include_solve=include_solve,
        voltage_base_commands=(
            energy_result.voltage_base_commands if exported_energy else ()
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
        capacitors=capacitor_result,
        master=master_result,
        library=library_result,
        energy=energy_result,
    )
