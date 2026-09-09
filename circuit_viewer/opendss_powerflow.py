"""Execução do fluxo de potência sobre o modelo exportado para o OpenDSS.

Camada de núcleo: não importa Qt **nem** ``py_dss_interface``. O motor entra por
parâmetro (:class:`~circuit_viewer.opendss_engine.DssEngine`), o que permite
testar todo o mapeamento de resultados headless, com um motor falso, e mantém a
biblioteca opcional confinada a ``opendss_engine``.

O modelo executado é **exatamente** o que a exportação gravaria: cada circuito
passa por :func:`~circuit_viewer.opendss_export.build_export`, os arquivos vão
para uma pasta de trabalho e o master é compilado. É isso que garante que o
resultado visto na aplicação coincida com o que o usuário obteria exportando à
mão e abrindo o OpenDSS.

Três decisões dão forma ao módulo:

- **Um solve por circuito.** ``New Circuit`` energiza um alimentador só, então
  não há como resolver vários circuitos numa passada; o módulo itera e acumula.
  Trecho ou barra compartilhados ficam com o resultado do **primeiro** circuito
  processado, a mesma regra de dono que a exportação usa para a ``VNOM``.
- **Quatro passos colhidos um a um.** O master termina com um ``Solve`` de
  ``number=4``, que deixa o circuito no estado do último patamar. Para ter os
  quatro, a solução é reconduzida pela API com ``number=1`` e um ``solve()`` por
  patamar, colhendo entre eles. Os passos alinham 1:1 com ``NPAT`` 0–3.
- **Associação pelo índice reverso do exportador.** O OpenDSS devolve resultados
  por nome (``Line.xyz``, ``barra.1``); os nomes nascem de regras não triviais
  que só o exportador conhece. O vínculo vem de ``exported_segments`` e de
  :func:`~circuit_viewer.opendss_export.bus_namer`, nunca de uma segunda
  implementação dessas regras.

Os nomes são comparados com ``casefold()`` porque o OpenDSS não diferencia
maiúsculas de minúsculas e devolve tudo em minúsculas. O modo de bibliotecas
desambigua homônimos com sufixos determinísticos; no modo original, dois códigos
que difiram só na caixa ainda podem chegar aqui como colisão e as duas entradas
são descartadas com diagnóstico, em vez de atribuir a corrente de um trecho ao
outro.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Mapping, Sequence

from .model import (
    CableModel,
    CapacitorModel,
    CircuitCatalogModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
)
from .opendss_engine import DssEngine
from .opendss_export import (
    CONTROL_MODE,
    LOAD_PATTERN_COUNT,
    MAX_CONTROL_ITER,
    MAX_REPORTED_ISSUES,
    OpenDssExportBundle,
    OpenDssLibraryExportError,
    build_export,
    bus_namer,
    parse_number,
    sanitize_dss_name,
)
from .opendss_library import OpenDssLibraryCatalog
from .opendss_line_mode import OpenDssLineParameterMode
from .opendss_mapping_store import OpenDssLibraryMappings
from .opendss_settings import OpenDssLoadSettings
from .opendss_solution import (
    DEFAULT_MAX_POWER_FLOW_ITER,
    parse_max_power_flow_iterations,
)
from .phase_config import PhaseConfiguration

if TYPE_CHECKING:
    from .generator_update import GeneratorUpdateModel


ProgressCallback = Callable[[int, int], None]

# Mesma cadência de verificação das demais análises do projeto.
_CANCEL_CHECK_INTERVAL = 4_096

# Abaixo disto o nó não está energizado — é o neutro, a terra, ou uma barra que
# ficou fora por chave aberta. Não é uma tensão baixa; é a ausência de tensão.
_DEAD_NODE_PU = 1e-9

# O que o OpenDSS usa quando ninguém configura ``Vminpu`` na ``Load``. Precisa
# ser conhecido aqui porque o corte relatado tem de ser o que **valeu**, não o
# que o usuário digitou — com os limites desligados, quem vale é este.
DEFAULT_DSS_VMINPU = 0.95

def _step_mode_commands(max_power_flow_iterations: int) -> tuple[str, ...]:
    """Comandos que reconduzem a solução ao primeiro patamar, um passo por vez.

    A ordem importa: ``number`` e ``stepsize`` são lidos pelo Solve, e ``time``
    precisa ser o último para não ser reposicionado pelos anteriores.

    O modo e os dois tetos são repetidos aqui de propósito: eles já saem no
    master, mas isto roda **depois** do Compile, sobre um engine singleton que
    pode carregar estado da execução anterior. Vale com mais força para o
    ``MaxIter``, que uma execução anterior pode ter deixado em outro valor.
    """

    return (
        f"Set ControlMode={CONTROL_MODE}",
        f"Set MaxControlIter={MAX_CONTROL_ITER}",
        f"Set MaxIter={parse_max_power_flow_iterations(max_power_flow_iterations)}",
        "Set mode=daily",
        "Set stepsize=1h",
        "Set number=1",
        "Set time=(0, 0)",
    )

# Pares fase-fase da tensão de linha, na ordem em que o painel os apresenta:
# VDE, VEF, VFD. Os números são os nós DSS das fases D, E e F.
LINE_VOLTAGE_PAIRS: tuple[tuple[int, int], ...] = ((1, 2), (2, 3), (3, 1))

# Operador de rotação das componentes simétricas: 1∠120°.
_ALPHA = complex(math.cos(math.radians(120.0)), math.sin(math.radians(120.0)))
_ALPHA_SQUARED = _ALPHA * _ALPHA

# A base pu do OpenDSS é a tensão de fase da barra; a de linha é √3 maior.
LINE_VOLTAGE_PU_BASE = math.sqrt(3.0)


@dataclass(frozen=True, slots=True)
class PowerFlowIssue:
    """Ocorrência encontrada ao executar ou ao ler o fluxo de potência."""

    element_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class SegmentCurrents:
    """Correntes de um trecho, por patamar e por nó.

    ``nodes`` são os nós do **terminal 1** da ``Line`` — a corrente que entra
    pela barra de montante. ``magnitudes[patamar][i]`` é o módulo em ampères do
    nó ``nodes[i]``.

    ``ampacity`` é o ``IADM`` do cabo de fase do trecho, usado pela interface
    para calcular o carregamento percentual. Vem do trecho mesmo quando ele
    carrega uma chave: ``Switch=Yes`` apaga os parâmetros elétricos da ``Line``,
    mas o condutor físico daquele ponto continua sendo o do ``CABOF_ID``.

    ``angles`` acompanha ``magnitudes`` e traz o ângulo do fasor em graus, no
    mesmo referencial do OpenDSS (a fonte em 0°). Vem de graça: o
    ``currents_mag_ang`` lido para o módulo já intercala os dois.
    """

    nodes: tuple[int, ...]
    magnitudes: tuple[tuple[float, ...], ...]
    ampacity: float | None = None
    angles: tuple[tuple[float, ...], ...] = ()

    @property
    def peak(self) -> float:
        """Maior módulo entre todos os patamares e nós."""

        return max((value for row in self.magnitudes for value in row), default=0.0)


@dataclass(frozen=True, slots=True)
class SegmentPowers:
    """Potências de um trecho no **terminal 1**, por patamar e por nó.

    ``active`` em kW e ``reactive`` em kvar, alinhados com ``nodes`` como as
    correntes. A **convenção de sinal do OpenDSS é preservada**: positivo é
    potência entrando pelo terminal 1, negativo é saindo. É o sinal que diz o
    sentido do fluxo, então nada aqui usa valor absoluto.

    ``active_losses``/``reactive_losses`` são do elemento inteiro, um valor por
    patamar — o OpenDSS as devolve somadas, não por fase.
    """

    nodes: tuple[int, ...]
    active: tuple[tuple[float, ...], ...]
    reactive: tuple[tuple[float, ...], ...]
    active_losses: tuple[float, ...] = ()
    reactive_losses: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class RegulatorTap:
    """Tap resolvido de uma unidade monofásica do regulador, num patamar.

    ``tap`` é a relação em pu do enrolamento 2, e ``minimum``/``maximum`` são os
    limites do transformador. ``num_taps`` é o total de passos do comutador
    entre ``minimum`` e ``maximum`` (``Transformer.NumTaps`` do OpenDSS — por
    ora sempre o padrão do motor, 32, porque ``FAIXA``/``NPASSOS`` do CSV ainda
    não alimentam a exportação; ver ``opendss_export.py``).

    ``at_limit`` é o que interessa na prática: um regulador encostado no fim do
    curso **parou de regular**, e sem isso a interface mostraria um número que
    parece normal.
    """

    phase: str
    tap: float
    minimum: float
    maximum: float
    num_taps: int = 0

    @property
    def at_limit(self) -> bool:
        span = self.maximum - self.minimum
        if span <= 0.0:
            return False
        tolerance = span * 1e-6
        return (
            self.tap <= self.minimum + tolerance
            or self.tap >= self.maximum - tolerance
        )

    @property
    def step(self) -> int:
        """Passos inteiros a partir do neutro (tap = 1,0 pu ⇒ passo 0).

        É a única grandeza de "quantos passos o comutador precisou dar" que a
        API do OpenDSS permite calcular: ela expõe a razão de tap resolvida,
        não um log de quantas vezes o ``RegControl`` moveu o tap durante a
        convergência.
        """

        span = self.maximum - self.minimum
        if span <= 0.0 or self.num_taps <= 0:
            return 0
        return round((self.tap - 1.0) / (span / self.num_taps))


@dataclass(frozen=True, slots=True)
class BarVoltages:
    """Tensões de uma barra, por patamar e por nó.

    ``magnitudes`` em volts e ``per_unit`` na base da barra; os dois vetores têm
    a mesma forma de ``nodes``, porque saem alinhados do mesmo ``AllNodeNames``.

    ``angles`` é o ângulo do fasor fase-neutro em graus, no referencial do
    OpenDSS (a fonte em 0°). É o que permite compor a tensão de linha, que é uma
    subtração de fasores e não de módulos.
    """

    nodes: tuple[int, ...]
    magnitudes: tuple[tuple[float, ...], ...]
    per_unit: tuple[tuple[float, ...], ...]
    angles: tuple[tuple[float, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class StepVoltages:
    """Extremos de tensão de um patamar, em pu, sobre o sistema inteiro.

    Sai do mesmo vetor que ``_harvest_bus_voltages`` já lê para distribuir as
    tensões pelas barras, então não custa solução nem leitura extra.

    Nós com tensão nula ficam de fora: são o neutro/terra e as barras
    desenergizadas, e nenhum dos dois é uma tensão que tenha afundado.

    ``nodes_below`` conta os nós abaixo de ``vminpu`` — o limite em que o
    OpenDSS converte a carga para impedância constante. É o número que diz se a
    faixa configurada está sendo exercida, e em que escala: um punhado de nós é
    a ponta de um ramal, um terço do sistema é outra história.
    """

    circuit_id: str
    step: int
    minimum_pu: float
    maximum_pu: float
    nodes_below: int
    vminpu: float


@dataclass(frozen=True, slots=True)
class PowerFlowResult:
    """Resultado de uma execução, pronto para ser associado à tela.

    Os modelos de entrada viajam no resultado para a interface poder
    revalidá-los por identidade na chegada, como as demais análises fazem: uma
    reimportação durante a execução torna o resultado obsoleto. Os reguladores
    entram nessa lista desde que passaram a ser exportados: eles mudam a tensão
    resolvida, então trocá-los invalida o resultado como qualquer outra fonte.
    """

    catalog: CircuitCatalogModel
    cables: CableModel | None
    phase_configuration: PhaseConfiguration
    loads: LoadModel | None
    patterns: LoadPatternModel | None
    step_count: int
    regulators: RegulatorModel | None = None
    generator_updates: GeneratorUpdateModel | None = None
    exported_generators: int = 0
    discarded_generators: int = 0
    segment_currents: Mapping[int, SegmentCurrents] = field(default_factory=dict)
    segment_powers: Mapping[int, SegmentPowers] = field(default_factory=dict)
    # Trecho → um retrato por patamar, cada um com um RegulatorTap por fase —
    # a mesma forma (externo = patamar, interno = fase) de segment_currents e
    # bar_voltages.
    regulator_taps: Mapping[int, tuple[tuple[RegulatorTap, ...], ...]] = field(
        default_factory=dict
    )
    bar_voltages: Mapping[int, BarVoltages] = field(default_factory=dict)
    solved_circuits: tuple[str, ...] = ()
    skipped_circuits: tuple[str, ...] = ()
    unconverged: tuple[tuple[str, int], ...] = ()
    # Teto de iterações efetivamente usado, para o relatório poder dizê-lo: sem
    # isso não há como saber, olhando a aplicação, qual valor estava valendo.
    max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER
    # (circuito, patamar, iterações de controle) do OpenDSS. Fica no resultado
    # como dado bruto de diagnóstico. **Não** serve para concluir que o laço de
    # controle não rodou: uma iteração é também o desfecho normal de um passo
    # que convergiu com todos os controles dentro da banda. O que de fato
    # aborta o laço é a não convergência — ``CheckControls`` do OpenDSS faz
    # ``ControlActionsDone := TRUE`` quando a solução falha —, e isso já está
    # em ``unconverged``.
    control_iterations: tuple[tuple[str, int, int], ...] = ()
    # Um registro por (circuito, patamar): é o que permite dizer *quanto* um
    # patamar reprovado afundou, em vez de só que ele reprovou.
    step_voltages: tuple[StepVoltages, ...] = ()
    issues: tuple[PowerFlowIssue, ...] = ()
    omitted_issues: int = 0
    input_revision: object | None = None

    @property
    def has_warnings(self) -> bool:
        return bool(self.issues or self.unconverged or self.skipped_circuits)

    @property
    def saturated_regulators(self) -> tuple[tuple[int, int, tuple[str, ...]], ...]:
        """``(trecho, patamar, fases)`` de cada unidade no fim do curso.

        Medido, não inferido: vem do tap que o OpenDSS resolveu, pela mesma
        :attr:`RegulatorTap.at_limit` que o painel do trecho já usa. Um
        regulador encostado no limite **parou de regular**, e é a explicação
        mais direta para uma tensão que não sobe.
        """

        return tuple(
            sorted(
                (
                    (
                        segment_index,
                        step,
                        tuple(tap.phase for tap in units if tap.at_limit),
                    )
                    for segment_index, steps in self.regulator_taps.items()
                    for step, units in enumerate(steps)
                    if any(tap.at_limit for tap in units)
                ),
                key=lambda item: (item[1], item[0]),
            )
        )

    def voltages_for(self, circuit_id: str, step: int) -> StepVoltages | None:
        """Extremos de um patamar, ou ``None`` se ele não foi medido."""

        return next(
            (
                item
                for item in self.step_voltages
                if item.circuit_id == circuit_id and item.step == step
            ),
            None,
        )

    @property
    def is_empty(self) -> bool:
        return not self.segment_currents and not self.bar_voltages


class _PowerFlowReport:
    """Acumula ocorrências respeitando o teto de detalhamento."""

    __slots__ = ("issues", "total")

    def __init__(self) -> None:
        self.issues: list[PowerFlowIssue] = []
        self.total = 0

    def add(self, element_id: str, reason: str) -> None:
        self.total += 1
        if len(self.issues) < MAX_REPORTED_ISSUES:
            self.issues.append(PowerFlowIssue(element_id, reason))

    @property
    def omitted(self) -> int:
        return max(0, self.total - len(self.issues))


def _selected_indices(
    catalog: CircuitCatalogModel,
    circuit_indices: Sequence[int] | Iterable[int],
) -> tuple[int, ...]:
    selected = tuple(int(index) for index in circuit_indices)
    for circuit_index in selected:
        if not 0 <= circuit_index < len(catalog):
            raise IndexError(circuit_index)
    return selected


def _casefolded_index(
    pairs: Iterable[tuple[str, int]],
    report: _PowerFlowReport,
    describe: Callable[[int], str],
    kind: str,
) -> dict[str, int]:
    """Índice reverso ``nome minúsculo`` → índice, sem entradas ambíguas.

    O OpenDSS ignora a caixa dos nomes, então dois nomes que só diferem nela
    apontariam para o mesmo objeto nativo. Nesse caso nenhuma das duas entradas
    sobrevive: um resultado atribuído ao elemento errado é pior que um resultado
    ausente.
    """

    index: dict[str, int] = {}
    ambiguous: set[str] = set()
    for name, element_index in pairs:
        key = name.casefold()
        if key in ambiguous:
            continue
        previous = index.pop(key, None)
        if previous is None:
            index[key] = element_index
            continue
        ambiguous.add(key)
        report.add(
            describe(element_index),
            f"nome {kind} '{name}' difere apenas na caixa de "
            f"{describe(previous)}; o OpenDSS não distingue os dois e os "
            "resultados de ambos foram descartados",
        )
    return index


def _bar_index_by_bus_name(
    catalog: CircuitCatalogModel,
    circuit_index: int,
    report: _PowerFlowReport,
) -> dict[str, int]:
    bars = catalog.segments.bars
    bus_name = bus_namer(catalog)
    pairs = tuple(
        (bus_name(int(raw_index)), int(raw_index))
        for raw_index in catalog.membership(circuit_index).bar_indices
    )
    return _casefolded_index(
        pairs,
        report,
        lambda index: bars.bar_ids[index],
        "de barra",
    )


def _segment_index_by_line_name(
    catalog: CircuitCatalogModel,
    bundle,  # noqa: ANN001 — OpenDssExportBundle, evitando o import circular de tipo
    report: _PowerFlowReport,
) -> dict[str, int]:
    segments = catalog.segments
    pairs = (
        *bundle.lines.exported_segments,
        *bundle.switches.exported_segments,
    )
    return _casefolded_index(
        pairs,
        report,
        lambda index: segments.segment_ids[index],
        "de linha",
    )


def _ampacity(
    cables: CableModel | None,
    cable_id: str,
    *,
    line_parameter_mode: OpenDssLineParameterMode,
    library_catalog: OpenDssLibraryCatalog | None,
    library_mappings: OpenDssLibraryMappings | None,
) -> float | None:
    if not cable_id:
        return None
    if line_parameter_mode is OpenDssLineParameterMode.LIBRARY:
        if library_catalog is None or library_mappings is None:
            return None
        source_id = str(cable_id).strip()
        if not source_id:
            return None
        library_name = next(
            (
                entry.library_name
                for entry in library_mappings.cables
                if entry.source_id == source_id
            ),
            None,
        )
        if library_name is None:
            return None
        cable = next(
            (item for item in library_catalog.cables if item.name == library_name),
            None,
        )
        value = None if cable is None else parse_number(cable.normal_amps)
    else:
        cable = cables.record_for_id(cable_id) if cables is not None else None
        value = None if cable is None else parse_number(cable.iadm)
    return value if value is not None and value > 0.0 else None


def line_voltages(
    nodes: Sequence[int],
    magnitudes: Sequence[Sequence[float]],
    angles: Sequence[Sequence[float]],
) -> tuple[
    tuple[tuple[int, int], ...],
    tuple[tuple[float, ...], ...],
    tuple[tuple[float, ...], ...],
]:
    """Tensões fase-fase a partir dos fasores fase-neutro da barra.

    ``VDE = VD − VE`` é subtração de **fasores**: com os módulos apenas, o
    resultado estaria errado sempre que as fases não estivessem alinhadas — que
    é o caso normal. Daí esta função exigir os ângulos.

    Devolve ``(pares, módulos, ângulos)``, com um par por coluna. Só entra o par
    cujas **duas** fases existem na barra, então uma barra bifásica sai com uma
    coluna em vez de duas colunas de traço, e uma monofásica sai vazia.

    Público e no núcleo porque é física, não apresentação: o painel só exibe o
    que sai daqui.
    """

    position_by_node = {int(node): index for index, node in enumerate(nodes)}
    pairs = tuple(
        pair
        for pair in LINE_VOLTAGE_PAIRS
        if pair[0] in position_by_node and pair[1] in position_by_node
    )
    if not pairs:
        return (), (), ()

    rows: list[tuple[float, ...]] = []
    angle_rows: list[tuple[float, ...]] = []
    for magnitude_row, angle_row in zip(magnitudes, angles, strict=True):
        values: list[float] = []
        row_angles: list[float] = []
        for first, second in pairs:
            head = _phasor(magnitude_row, angle_row, position_by_node[first])
            tail = _phasor(magnitude_row, angle_row, position_by_node[second])
            difference = head - tail
            values.append(abs(difference))
            row_angles.append(math.degrees(_phase_of(difference)))
        rows.append(tuple(values))
        angle_rows.append(tuple(row_angles))
    return pairs, tuple(rows), tuple(angle_rows)


def voltage_unbalance(
    nodes: Sequence[int],
    magnitudes: Sequence[Sequence[float]],
    angles: Sequence[Sequence[float]],
) -> tuple[float, ...]:
    """Fator de desequilíbrio de tensão, em %, por patamar.

    Definição do PRODIST Módulo 8: ``FD% = |V₋| / |V₊| × 100``, a razão entre as
    componentes de sequência negativa e positiva. Exige as três fases — com
    menos de três não há sistema trifásico a desequilibrar, e a função devolve
    vazio.

    Com os fasores em mãos esta é a definição exata, e não a aproximação por
    desvio máximo em torno da média, que ignora o ângulo.
    """

    position_by_node = {int(node): index for index, node in enumerate(nodes)}
    if any(node not in position_by_node for node in (1, 2, 3)):
        return ()

    values: list[float] = []
    for magnitude_row, angle_row in zip(magnitudes, angles, strict=True):
        phases = tuple(
            _phasor(magnitude_row, angle_row, position_by_node[node])
            for node in (1, 2, 3)
        )
        positive = (
            phases[0] + _ALPHA * phases[1] + _ALPHA_SQUARED * phases[2]
        ) / 3.0
        negative = (
            phases[0] + _ALPHA_SQUARED * phases[1] + _ALPHA * phases[2]
        ) / 3.0
        values.append(
            0.0 if abs(positive) == 0.0 else abs(negative) / abs(positive) * 100.0
        )
    return tuple(values)


def apparent_power(
    active: Sequence[Sequence[float]],
    reactive: Sequence[Sequence[float]],
) -> tuple[tuple[tuple[float, ...], ...], tuple[tuple[float, ...], ...]]:
    """Módulo e ângulo de ``S = P + jQ`` por fase, em kVA e graus."""

    magnitudes: list[tuple[float, ...]] = []
    angles: list[tuple[float, ...]] = []
    for active_row, reactive_row in zip(active, reactive, strict=True):
        magnitudes.append(
            tuple(
                math.hypot(p, q)
                for p, q in zip(active_row, reactive_row, strict=True)
            )
        )
        angles.append(
            tuple(
                math.degrees(math.atan2(q, p))
                for p, q in zip(active_row, reactive_row, strict=True)
            )
        )
    return tuple(magnitudes), tuple(angles)


def three_phase_power(
    active: Sequence[Sequence[float]],
    reactive: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], ...]:
    """Totais do elemento por patamar: ``(P₃ᵩ, Q₃ᵩ, |S₃ᵩ|, θ₃ᵩ)``.

    A soma é das potências **complexas** das fases, e o sinal de cada parcela é
    preservado: ele diz se o elemento recebe ou fornece pelo terminal 1.
    """

    rows: list[tuple[float, ...]] = []
    for active_row, reactive_row in zip(active, reactive, strict=True):
        total_active = math.fsum(active_row)
        total_reactive = math.fsum(reactive_row)
        rows.append(
            (
                total_active,
                total_reactive,
                math.hypot(total_active, total_reactive),
                math.degrees(math.atan2(total_reactive, total_active)),
            )
        )
    return tuple(rows)


def power_factor(
    active: Sequence[Sequence[float]],
    reactive: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], ...]:
    """``cos θ`` por fase mais o do total, por patamar.

    Sai **com sinal**, herdado de ``P``: negativo indica potência saindo pelo
    terminal 1. Potência nula não tem fator definido e vira ``0``.
    """

    rows: list[tuple[float, ...]] = []
    for active_row, reactive_row in zip(active, reactive, strict=True):
        values = [
            0.0 if math.hypot(p, q) == 0.0 else p / math.hypot(p, q)
            for p, q in zip(active_row, reactive_row, strict=True)
        ]
        total_active = math.fsum(active_row)
        total_apparent = math.hypot(total_active, math.fsum(reactive_row))
        values.append(
            0.0 if total_apparent == 0.0 else total_active / total_apparent
        )
        rows.append(tuple(values))
    return tuple(rows)


def _phasor(
    magnitudes: Sequence[float],
    angles: Sequence[float],
    position: int,
) -> complex:
    return complex(
        magnitudes[position] * math.cos(math.radians(angles[position])),
        magnitudes[position] * math.sin(math.radians(angles[position])),
    )


def _phase_of(value: complex) -> float:
    return math.atan2(value.imag, value.real)


def _write_circuit_files(workspace: Path, circuit_index: int, definition, bundle) -> Path:  # noqa: ANN001
    """Grava o bundle numa subpasta própria e devolve o caminho do master.

    O índice entra no nome da pasta porque dois circuitos podem ter o mesmo
    ``CODIGO`` saneado, e cada um precisa do seu ``Buscoords`` relativo.
    """

    base = sanitize_dss_name(definition.code) or sanitize_dss_name(
        definition.circuit_id
    )
    directory = workspace / f"{circuit_index:04d}_{base or 'circuito'}"
    directory.mkdir(parents=True, exist_ok=True)
    for filename, text in bundle.files:
        (directory / filename).write_text(text, encoding="utf-8")
    return directory / bundle.master.master_filename


def _harvest_bus_voltages(
    engine: DssEngine,
    node_names: Sequence[str],
    bar_by_bus_name: Mapping[str, int],
    nodes: dict[int, list[int]],
    magnitudes: dict[int, list[float]],
    per_unit: dict[int, list[float]],
    angles: dict[int, list[float]],
    report: _PowerFlowReport,
    circuit_id: str,
    *,
    first_step: bool,
    vminpu: float,
    step: int,
) -> StepVoltages | None:
    """Distribui ``AllBusVMag``/``AllBusVMagPu`` pelas barras da aplicação.

    Os vetores do OpenDSS são paralelos e cobrem o sistema inteiro, então uma
    leitura por passo resolve todas as barras — não há laço por elemento.

    ``AllBusVolts`` entra pelo ângulo: não existe um ``AllBusVMagAngle`` no
    ``py_dss_interface``, e é dele que sai o fasor de que a tensão de linha
    precisa. Ele traz **dois** doubles por nó (parte real e imaginária), daí a
    conferência de tamanho ser contra o dobro.

    Devolve os extremos do patamar de brinde: o vetor em pu já está em mãos, e
    resumi-lo aqui evita uma segunda travessia da DLL só para dizer quanto o
    circuito afundou.
    """

    values = engine.circuit.buses_vmag
    pu_values = engine.circuit.buses_vmag_pu
    volts = engine.circuit.buses_volts
    if (
        len(values) != len(node_names)
        or len(pu_values) != len(node_names)
        or len(volts) != 2 * len(node_names)
    ):
        report.add(
            circuit_id,
            "o OpenDSS devolveu tensões em quantidade diferente da lista de "
            "nós; as tensões deste circuito foram descartadas",
        )
        return None
    for position, node_name in enumerate(node_names):
        bus, _, node_text = node_name.rpartition(".")
        bar_index = bar_by_bus_name.get(bus.casefold())
        if bar_index is None:
            continue
        try:
            node = int(node_text)
        except ValueError:
            continue
        if node <= 0:
            # Nó de neutro/terra: não é tensão de fase.
            continue
        if first_step:
            nodes.setdefault(bar_index, []).append(node)
        magnitudes.setdefault(bar_index, []).append(float(values[position]))
        per_unit.setdefault(bar_index, []).append(float(pu_values[position]))
        real = float(volts[2 * position])
        imaginary = float(volts[2 * position + 1])
        angles.setdefault(bar_index, []).append(
            math.degrees(math.atan2(imaginary, real))
        )

    # Nó com tensão nula é neutro/terra ou barra desenergizada; nenhum dos dois
    # é uma tensão que afundou, e deixá-los entrar fixaria o mínimo em zero.
    energized = tuple(
        float(value) for value in pu_values if float(value) > _DEAD_NODE_PU
    )
    if not energized:
        return None
    return StepVoltages(
        circuit_id=circuit_id,
        step=step,
        minimum_pu=min(energized),
        maximum_pu=max(energized),
        nodes_below=sum(1 for value in energized if value < vminpu),
        vminpu=vminpu,
    )


@dataclass(frozen=True, slots=True)
class _TerminalReadings:
    """Grandezas do terminal 1 do elemento ativo, já sem o nó de terra.

    Serve tanto para a ``Line`` de um trecho comum quanto para o
    ``Transformer`` de uma unidade de regulador: o recorte do terminal e o
    filtro de nó são idênticos nos dois, e é isso que permite um trecho
    regulado chegar à tabela do painel pelo mesmo caminho dos demais.
    """

    nodes: tuple[int, ...]
    magnitudes: tuple[float, ...]
    angles: tuple[float, ...]
    active: tuple[float, ...]
    reactive: tuple[float, ...]
    active_loss: float
    reactive_loss: float

    @property
    def has_power(self) -> bool:
        """``True`` quando a potência acompanhou a corrente em todos os nós."""

        return len(self.active) == len(self.magnitudes)


def _terminal_one_readings(engine: DssEngine) -> _TerminalReadings | None:
    """Lê corrente, potência e perdas do terminal 1 do elemento ativo.

    ``powers`` tem o mesmo layout do ``currents_mag_ang`` — dois doubles por
    condutor, terminais em sequência —, só que o par é ``(kW, kvar)`` em vez de
    módulo e ângulo. Por isso as duas leituras andam juntas: mesmo recorte de
    terminal, mesmo filtro de nó, mesma indexação.

    Devolve ``None`` quando o terminal não expõe nenhum nó de fase: sem nó não
    há corrente a atribuir a trecho nenhum.
    """

    conductors = int(engine.cktelement.num_conductors)
    readings = engine.cktelement.currents_mag_ang
    order = engine.cktelement.node_order
    # currents_mag_ang intercala módulo e ângulo por condutor, com os terminais
    # em sequência; o terminal 1 são os primeiros condutores.
    powers = engine.cktelement.powers
    terminal_nodes: list[int] = []
    row: list[float] = []
    angle_row: list[float] = []
    active_row: list[float] = []
    reactive_row: list[float] = []
    for position in range(min(conductors, len(order))):
        node = int(order[position])
        if node <= 0:
            continue
        magnitude_at = 2 * position
        # O ângulo é o segundo double do par; ler os dois juntos mantém módulo
        # e fase do mesmo condutor sempre alinhados.
        if magnitude_at + 1 >= len(readings):
            break
        terminal_nodes.append(node)
        row.append(float(readings[magnitude_at]))
        angle_row.append(float(readings[magnitude_at + 1]))
        # Sinal preservado: positivo entra pelo terminal 1.
        if magnitude_at + 1 < len(powers):
            active_row.append(float(powers[magnitude_at]))
            reactive_row.append(float(powers[magnitude_at + 1]))
    if not row:
        return None
    # As perdas vêm do elemento inteiro, não por condutor — e em **watts**, ao
    # contrário de ``powers``, que vem em kW. Medido contra 3·R·I² de um trecho
    # conhecido: a razão deu exatamente 1000. Sem esta divisão a coluna erraria
    # por três ordens de grandeza sem nada denunciar.
    losses = engine.cktelement.losses
    return _TerminalReadings(
        nodes=tuple(terminal_nodes),
        magnitudes=tuple(row),
        angles=tuple(angle_row),
        active=tuple(active_row),
        reactive=tuple(reactive_row),
        active_loss=float(losses[0]) / 1_000.0 if len(losses) > 1 else 0.0,
        reactive_loss=float(losses[1]) / 1_000.0 if len(losses) > 1 else 0.0,
    )


def _accumulate_terminal_readings(
    segment_index: int,
    measured: _TerminalReadings,
    nodes: dict[int, tuple[int, ...]],
    magnitudes: dict[int, list[float]],
    angles: dict[int, list[float]],
    active: dict[int, list[float]],
    reactive: dict[int, list[float]],
    active_losses: dict[int, list[float]],
    reactive_losses: dict[int, list[float]],
) -> None:
    """Empilha a leitura de um patamar nos acumuladores achatados do trecho.

    O primeiro patamar fixa quais nós o trecho tem; os seguintes só entram se
    trouxerem exatamente os mesmos, porque ``_rows`` reparte a sequência por
    largura fixa e uma linha de tamanho diferente desalinharia tudo em silêncio.
    """

    known = nodes.get(segment_index)
    if known is None:
        nodes[segment_index] = measured.nodes
        magnitudes[segment_index] = list(measured.magnitudes)
        angles[segment_index] = list(measured.angles)
        if measured.has_power:
            active[segment_index] = list(measured.active)
            reactive[segment_index] = list(measured.reactive)
            active_losses[segment_index] = [measured.active_loss]
            reactive_losses[segment_index] = [measured.reactive_loss]
        return
    if known != measured.nodes:
        return
    magnitudes[segment_index].extend(measured.magnitudes)
    angles[segment_index].extend(measured.angles)
    if measured.has_power and segment_index in active:
        active[segment_index].extend(measured.active)
        reactive[segment_index].extend(measured.reactive)
        active_losses[segment_index].append(measured.active_loss)
        reactive_losses[segment_index].append(measured.reactive_loss)


def _harvest_line_currents(
    engine: DssEngine,
    segment_by_line_name: Mapping[str, int],
    nodes: dict[int, tuple[int, ...]],
    magnitudes: dict[int, list[float]],
    angles: dict[int, list[float]],
    active: dict[int, list[float]],
    reactive: dict[int, list[float]],
    active_losses: dict[int, list[float]],
    reactive_losses: dict[int, list[float]],
    cancel_check: Callable[[], bool] | None,
) -> None:
    """Percorre as ``Line`` do circuito lendo corrente e potência do terminal 1.

    Chaves também são ``Line`` no modelo exportado, então elas vêm de graça
    neste mesmo laço. Os trechos **regulados** não passam por aqui: eles são
    ``Transformer``, e quem os colhe é :func:`_harvest_regulators`.
    """

    processed = 0
    has_line = bool(engine.lines.first())
    while has_line:
        if (
            cancel_check is not None
            and processed % _CANCEL_CHECK_INTERVAL == 0
            and cancel_check()
        ):
            raise InterruptedError("Fluxo de potência cancelado.")
        processed += 1
        segment_index = segment_by_line_name.get(engine.lines.name.casefold())
        if segment_index is not None:
            measured = _terminal_one_readings(engine)
            if measured is not None:
                _accumulate_terminal_readings(
                    segment_index,
                    measured,
                    nodes,
                    magnitudes,
                    angles,
                    active,
                    reactive,
                    active_losses,
                    reactive_losses,
                )
        has_line = bool(engine.lines.next())


def _control_iterations(engine: DssEngine) -> int:
    """Iterações de controle do último ``solve()``, ou 0 se o motor não disser.

    Um motor falso — e versões antigas da biblioteca — pode não expor a
    propriedade. Ela alimenta um diagnóstico, então a ausência vira 0 em vez de
    derrubar uma execução que já produziu resultados válidos.
    """

    try:
        return int(engine.solution.control_iterations)
    except (AttributeError, TypeError, ValueError):
        return 0


def _regulator_unit_index(bundle) -> dict[str, tuple[int, str]]:  # noqa: ANN001
    """``nome do Transformer`` minúsculo → ``(índice do trecho, fase)``."""

    regulators = getattr(bundle, "regulators", None)
    if regulators is None:
        return {}
    return {
        name.casefold(): (segment_index, phase)
        for name, segment_index, phase in regulators.exported_units
    }


def _merge_unit_readings(
    units: Sequence[_TerminalReadings],
) -> _TerminalReadings | None:
    """Junta as unidades monofásicas de um regulador numa leitura de trecho.

    Um regulador trifásico são três ``Transformer`` independentes, um por fase;
    o trecho é um só. Ordenar por nó dá a mesma forma que uma ``Line`` trifásica
    produziria — e a mesma ordem em todos os patamares, que é o que
    ``_accumulate_terminal_readings`` exige para não desalinhar as linhas.

    As perdas são somadas: elas vêm por elemento, e o elemento aqui é o banco.
    """

    entries = sorted(
        (
            (node, position, unit)
            for unit in units
            for position, node in enumerate(unit.nodes)
        ),
        key=lambda item: item[0],
    )
    if not entries:
        return None
    has_power = all(unit.has_power for unit in units)
    return _TerminalReadings(
        nodes=tuple(node for node, _, _ in entries),
        magnitudes=tuple(unit.magnitudes[at] for _, at, unit in entries),
        angles=tuple(unit.angles[at] for _, at, unit in entries),
        active=(
            tuple(unit.active[at] for _, at, unit in entries) if has_power else ()
        ),
        reactive=(
            tuple(unit.reactive[at] for _, at, unit in entries) if has_power else ()
        ),
        active_loss=math.fsum(unit.active_loss for unit in units),
        reactive_loss=math.fsum(unit.reactive_loss for unit in units),
    )


def _harvest_regulators(
    engine: DssEngine,
    unit_by_name: Mapping[str, tuple[int, str]],
    taps: dict[int, list[RegulatorTap]],
    nodes: dict[int, tuple[int, ...]],
    magnitudes: dict[int, list[float]],
    angles: dict[int, list[float]],
    active: dict[int, list[float]],
    reactive: dict[int, list[float]],
    active_losses: dict[int, list[float]],
    reactive_losses: dict[int, list[float]],
) -> None:
    """Percorre os ``Transformer`` colhendo o tap e as grandezas do trecho.

    Os reguladores nunca aparecem no laço de ``lines``: no modelo exportado eles
    são ``Transformer``, e o trecho regulado **não sai como ``Line``** — se
    saísse, a linha ficaria em paralelo com o regulador e curto-circuitaria a
    injeção de tensão. Sem esta função o trecho ficaria sem corrente e sem
    potência no painel, que era o estado até aqui.

    As duas leituras saem da mesma visita porque ``cktelement`` acompanha o
    ``Transformer`` ativo, e ``transformers.wdg`` não troca o elemento ativo —
    ambos medidos contra a DLL. A corrente é lida **antes** de mexer em ``wdg``
    de qualquer forma: nada no protocolo do OpenDSS promete essa independência.

    O vínculo nome→trecho vem do índice reverso do exportador, como o das
    linhas — nunca de uma segunda implementação das regras de nome.
    """

    if not unit_by_name:
        return
    readings_by_segment: dict[int, list[_TerminalReadings]] = {}
    has_transformer = bool(engine.transformers.first())
    while has_transformer:
        found = unit_by_name.get(engine.transformers.name.casefold())
        if found is not None:
            segment_index, phase = found
            measured = _terminal_one_readings(engine)
            if measured is not None:
                readings_by_segment.setdefault(segment_index, []).append(measured)
            # Enrolamento 2 é o regulado, o mesmo que o RegControl monitora.
            engine.transformers.wdg = 2
            taps.setdefault(segment_index, []).append(
                RegulatorTap(
                    phase=phase,
                    tap=float(engine.transformers.tap),
                    minimum=float(engine.transformers.min_tap),
                    maximum=float(engine.transformers.max_tap),
                    num_taps=int(engine.transformers.num_taps),
                )
            )
        has_transformer = bool(engine.transformers.next())

    for segment_index, units in readings_by_segment.items():
        merged = _merge_unit_readings(units)
        if merged is None:
            continue
        _accumulate_terminal_readings(
            segment_index,
            merged,
            nodes,
            magnitudes,
            angles,
            active,
            reactive,
            active_losses,
            reactive_losses,
        )


def run_power_flow(
    engine: DssEngine,
    catalog: CircuitCatalogModel,
    cables: CableModel | None,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    workspace: Path,
    loads: LoadModel | None = None,
    patterns: LoadPatternModel | None = None,
    generator_updates: GeneratorUpdateModel | None = None,
    regulators: RegulatorModel | None = None,
    capacitors: CapacitorModel | None = None,
    load_settings: OpenDssLoadSettings | None = None,
    max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER,
    line_parameter_mode: OpenDssLineParameterMode = (
        OpenDssLineParameterMode.ORIGINAL
    ),
    library_catalog: OpenDssLibraryCatalog | None = None,
    library_mappings: OpenDssLibraryMappings | None = None,
    step_count: int = LOAD_PATTERN_COUNT,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> PowerFlowResult:
    """Resolve o fluxo de potência dos circuitos indicados e devolve as grandezas.

    Um circuito que não produza master — ``VNOM`` inválida, por exemplo — é
    registrado em ``skipped_circuits`` com o motivo nos ``issues``, e os demais
    seguem: uma falha de dado num alimentador não deve invalidar o estudo dos
    outros.

    ``load_settings`` apenas atravessa até ``build_export``: os ``BatchEdit``
    que ele gera ficam no master e são aplicados pelo ``Compile``. Nada precisa
    ser reemitido entre os quatro ``solve()`` — foi medido que as propriedades
    das ``Load`` sobrevivem às soluções seguintes.

    ``max_power_flow_iterations``, ao contrário, precisa dos dois caminhos: ele
    entra no master pelo ``build_export`` e é reemitido depois do ``Compile``,
    porque o engine é um singleton. Um teto baixo demais não é um detalhe de
    desempenho — a solução é abandonada antes de terminar a primeira passada, o
    laço de controle nunca roda e os taps dos reguladores ficam onde estavam.

    **Mas o teto não é a única causa desse sintoma, nem a mais comum nas redes
    grandes.** Um patamar cuja carga esteja além do limite de carregabilidade do
    alimentador **diverge**: não há solução a encontrar, e o resultado é o mesmo
    laço de controle abortado — com a diferença de que subir o teto não muda
    nada. Medido num alimentador de 23.857 barras e 2.250 km com ``vminpu=0,7``:
    dois patamares não convergem nem com 20.000 iterações, e a tensão mínima
    **oscila** entre as tentativas em vez de se aproximar de um valor. Por isso
    ``step_voltages`` acompanha ``unconverged`` no resultado: sem saber quanto o
    patamar afundou, os dois casos são indistinguíveis para quem lê o relatório.
    """

    if step_count <= 0:
        raise ValueError("O número de patamares deve ser positivo.")
    if generator_updates is not None:
        if generator_updates.circuits is not catalog:
            raise ValueError(
                "Os resultados dos geradores pertencem a outros circuitos."
            )
        if generator_updates.phase_configuration is not phase_configuration:
            raise ValueError(
                "Os resultados dos geradores usam outra configuração de fases."
            )

    selected = _selected_indices(catalog, circuit_indices)
    line_parameter_mode = OpenDssLineParameterMode(line_parameter_mode)
    from .project_topology import coupled_study_reason
    reason = coupled_study_reason(catalog, selected)
    if reason:
        raise ValueError(reason)

    def build_circuit_export(circuit_index: int) -> OpenDssExportBundle:
        return build_export(
            catalog,
            cables,
            phase_configuration,
            (circuit_index,),
            loads=loads,
            patterns=patterns,
            generator_updates=generator_updates,
            regulators=regulators,
            capacitors=capacitors,
            load_settings=load_settings,
            max_power_flow_iterations=max_power_flow_iterations,
            line_parameter_mode=line_parameter_mode,
            library_catalog=library_catalog,
            library_mappings=library_mappings,
            # Ver o docstring de build_master_export: Compile executa o
            # arquivo, e o laço abaixo é quem resolve os patamares.
            include_solve=False,
            cancel_check=cancel_check,
        )

    # No modo biblioteca, a consistência é uma propriedade do estudo inteiro:
    # uma referência ausente em qualquer alimentador deve falhar antes que o
    # motor compile ou resolva o primeiro. Os bundles também são reutilizados no
    # laço abaixo, evitando gerar cada circuito duas vezes.
    preflight_exports: dict[int, OpenDssExportBundle] = {}
    if line_parameter_mode is OpenDssLineParameterMode.LIBRARY:
        preflight_errors: list[str] = []
        for circuit_index in selected:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Fluxo de potência cancelado.")
            try:
                preflight_exports[circuit_index] = build_circuit_export(circuit_index)
            except OpenDssLibraryExportError as exc:
                circuit_id = catalog.definition(circuit_index).circuit_id
                preflight_errors.extend(
                    f"Circuito {circuit_id}: {error}" for error in exc.errors
                )
        if preflight_errors:
            raise OpenDssLibraryExportError(preflight_errors)

    report = _PowerFlowReport()
    segment_currents: dict[int, SegmentCurrents] = {}
    segment_powers: dict[int, SegmentPowers] = {}
    regulator_taps: dict[int, tuple[tuple[RegulatorTap, ...], ...]] = {}
    bar_voltages: dict[int, BarVoltages] = {}
    solved: list[str] = []
    skipped: list[str] = []
    unconverged: list[tuple[str, int]] = []
    control_iterations: list[tuple[str, int, int]] = []
    step_voltages: list[StepVoltages] = []
    # O corte que de fato vale nas Loads: com os limites desligados nenhum
    # BatchEdit sai no master, então quem vale é o padrão do próprio OpenDSS.
    effective_vminpu = (
        load_settings.vminpu
        if load_settings is not None and load_settings.voltage_limits_enabled
        else DEFAULT_DSS_VMINPU
    )
    exported_generators = 0
    discarded_generators = 0
    total = len(selected) * step_count
    completed = 0

    for circuit_index in selected:
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Fluxo de potência cancelado.")
        definition = catalog.definition(circuit_index)
        bundle = preflight_exports.get(circuit_index)
        if bundle is None:
            bundle = build_circuit_export(circuit_index)
        master = bundle.master
        if master is None or not master.text:
            reason = "; ".join(issue.reason for issue in (master.issues if master else ()))
            report.add(
                definition.circuit_id,
                "o arquivo master não pôde ser gerado, então o circuito não foi "
                f"resolvido{f': {reason}' if reason else ''}",
            )
            skipped.append(definition.circuit_id)
            completed += step_count
            if progress is not None:
                progress(min(completed, total), total)
            continue

        for _, generator_result in bundle.generators_by_phase_count:
            exported_generators += generator_result.exported_count
            discarded_generators += generator_result.discarded_count
            for issue in generator_result.issues:
                report.add(
                    issue.segment_id,
                    f"gerador não exportado: {issue.reason}",
                )
            report.total += generator_result.omitted_issues

        master_path = _write_circuit_files(
            workspace,
            circuit_index,
            definition,
            bundle,
        )
        segment_by_line_name = _segment_index_by_line_name(catalog, bundle, report)
        bar_by_bus_name = _bar_index_by_bus_name(catalog, circuit_index, report)

        engine.text("Clear")
        # Compile, e não Redirect: ele muda o diretório do OpenDSS para a pasta
        # do master, que é o que faz os Redirect internos e o Buscoords
        # relativo do fim do arquivo resolverem.
        engine.text(f"Compile [{master_path}]")
        for command in _step_mode_commands(max_power_flow_iterations):
            engine.text(command)

        node_names = tuple(engine.circuit.nodes_names)
        circuit_nodes: dict[int, list[int]] = {}
        circuit_magnitudes: dict[int, list[float]] = {}
        circuit_per_unit: dict[int, list[float]] = {}
        circuit_angles: dict[int, list[float]] = {}
        line_nodes: dict[int, tuple[int, ...]] = {}
        line_magnitudes: dict[int, list[float]] = {}
        line_angles: dict[int, list[float]] = {}
        line_active: dict[int, list[float]] = {}
        line_reactive: dict[int, list[float]] = {}
        line_active_losses: dict[int, list[float]] = {}
        line_reactive_losses: dict[int, list[float]] = {}
        # Índice nome→(trecho, fase) não muda entre patamares; calculado uma
        # vez fora do laço, como segment_by_line_name e bar_by_bus_name.
        regulator_unit_index = _regulator_unit_index(bundle)
        circuit_taps: dict[int, list[tuple[RegulatorTap, ...]]] = {}

        for step in range(step_count):
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Fluxo de potência cancelado.")
            engine.solution.solve()
            if not engine.solution.converged:
                unconverged.append((definition.circuit_id, step))
            # Lido junto do converged, do mesmo objeto e do mesmo passo. Um
            # motor que não exponha a propriedade não pode derrubar a execução:
            # a contagem é diagnóstico, não resultado.
            control_iterations.append(
                (
                    definition.circuit_id,
                    step,
                    _control_iterations(engine),
                )
            )
            measured = _harvest_bus_voltages(
                engine,
                node_names,
                bar_by_bus_name,
                circuit_nodes,
                circuit_magnitudes,
                circuit_per_unit,
                circuit_angles,
                report,
                definition.circuit_id,
                first_step=step == 0,
                vminpu=effective_vminpu,
                step=step,
            )
            if measured is not None:
                step_voltages.append(measured)
            _harvest_line_currents(
                engine,
                segment_by_line_name,
                line_nodes,
                line_magnitudes,
                line_angles,
                line_active,
                line_reactive,
                line_active_losses,
                line_reactive_losses,
                cancel_check,
            )
            # Um retrato por patamar: o tap resolvido muda a cada solve(), e a
            # tabela de passos por patamar do painel precisa de todos, não só
            # do último.
            step_taps: dict[int, list[RegulatorTap]] = {}
            _harvest_regulators(
                engine,
                regulator_unit_index,
                step_taps,
                line_nodes,
                line_magnitudes,
                line_angles,
                line_active,
                line_reactive,
                line_active_losses,
                line_reactive_losses,
            )
            for segment_index, units in step_taps.items():
                circuit_taps.setdefault(segment_index, []).append(tuple(units))
            completed += 1
            if progress is not None:
                progress(min(completed, total), total)

        for segment_index, steps in circuit_taps.items():
            regulator_taps.setdefault(segment_index, tuple(steps))

        _merge_circuit_results(
            catalog,
            cables,
            step_count,
            circuit_nodes,
            circuit_magnitudes,
            circuit_per_unit,
            circuit_angles,
            line_nodes,
            line_magnitudes,
            line_angles,
            line_active,
            line_reactive,
            line_active_losses,
            line_reactive_losses,
            bar_voltages,
            segment_currents,
            segment_powers,
            report,
            line_parameter_mode=line_parameter_mode,
            library_catalog=library_catalog,
            library_mappings=library_mappings,
        )
        solved.append(definition.circuit_id)

    return PowerFlowResult(
        catalog=catalog,
        cables=cables,
        phase_configuration=phase_configuration,
        loads=loads,
        patterns=patterns,
        regulators=regulators,
        generator_updates=generator_updates,
        exported_generators=exported_generators,
        discarded_generators=discarded_generators,
        step_count=step_count,
        segment_currents=segment_currents,
        segment_powers=segment_powers,
        regulator_taps=regulator_taps,
        bar_voltages=bar_voltages,
        solved_circuits=tuple(solved),
        skipped_circuits=tuple(skipped),
        unconverged=tuple(unconverged),
        max_power_flow_iterations=parse_max_power_flow_iterations(
            max_power_flow_iterations
        ),
        control_iterations=tuple(control_iterations),
        step_voltages=tuple(step_voltages),
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
    )


def _rows(values: Sequence[float], width: int, step_count: int) -> tuple[tuple[float, ...], ...] | None:
    """Reparte a sequência achatada em uma linha por patamar."""

    if width <= 0 or len(values) != width * step_count:
        return None
    return tuple(
        tuple(values[step * width : (step + 1) * width])
        for step in range(step_count)
    )


def _merge_circuit_results(
    catalog: CircuitCatalogModel,
    cables: CableModel | None,
    step_count: int,
    circuit_nodes: Mapping[int, Sequence[int]],
    circuit_magnitudes: Mapping[int, Sequence[float]],
    circuit_per_unit: Mapping[int, Sequence[float]],
    circuit_angles: Mapping[int, Sequence[float]],
    line_nodes: Mapping[int, tuple[int, ...]],
    line_magnitudes: Mapping[int, Sequence[float]],
    line_angles: Mapping[int, Sequence[float]],
    line_active: Mapping[int, Sequence[float]],
    line_reactive: Mapping[int, Sequence[float]],
    line_active_losses: Mapping[int, Sequence[float]],
    line_reactive_losses: Mapping[int, Sequence[float]],
    bar_voltages: dict[int, BarVoltages],
    segment_currents: dict[int, SegmentCurrents],
    segment_powers: dict[int, SegmentPowers],
    report: _PowerFlowReport,
    *,
    line_parameter_mode: OpenDssLineParameterMode,
    library_catalog: OpenDssLibraryCatalog | None,
    library_mappings: OpenDssLibraryMappings | None,
) -> None:
    """Converte os acumuladores do circuito em dataclasses e mescla no total.

    Elemento que já tenha resultado de um circuito anterior é preservado: em
    rede sobreposta o **primeiro** circuito processado é o dono, exatamente como
    na exportação, onde ele também define a ``VNOM`` usada.
    """

    bars = catalog.segments.bars
    segments = catalog.segments
    for bar_index, nodes in circuit_nodes.items():
        if bar_index in bar_voltages:
            continue
        width = len(nodes)
        magnitudes = _rows(circuit_magnitudes.get(bar_index, ()), width, step_count)
        per_unit = _rows(circuit_per_unit.get(bar_index, ()), width, step_count)
        bar_angles = _rows(circuit_angles.get(bar_index, ()), width, step_count)
        if magnitudes is None or per_unit is None or bar_angles is None:
            report.add(
                bars.bar_ids[bar_index],
                "as tensões lidas não completaram todos os patamares; a barra "
                "ficou sem resultado",
            )
            continue
        bar_voltages[bar_index] = BarVoltages(
            nodes=tuple(nodes),
            magnitudes=magnitudes,
            per_unit=per_unit,
            angles=bar_angles,
        )

    ampacity_by_cable_id: dict[str, float | None] = {}
    for segment_index, nodes in line_nodes.items():
        if segment_index in segment_currents:
            continue
        magnitudes = _rows(line_magnitudes.get(segment_index, ()), len(nodes), step_count)
        segment_angles = _rows(line_angles.get(segment_index, ()), len(nodes), step_count)
        if magnitudes is None or segment_angles is None:
            report.add(
                segments.segment_ids[segment_index],
                "as correntes lidas não completaram todos os patamares; o "
                "trecho ficou sem resultado",
            )
            continue
        cable_id = segments.record(segment_index).phase_cable_id
        if cable_id not in ampacity_by_cable_id:
            ampacity_by_cable_id[cable_id] = _ampacity(
                cables,
                cable_id,
                line_parameter_mode=line_parameter_mode,
                library_catalog=library_catalog,
                library_mappings=library_mappings,
            )
        segment_currents[segment_index] = SegmentCurrents(
            nodes=nodes,
            magnitudes=magnitudes,
            angles=segment_angles,
            ampacity=ampacity_by_cable_id[cable_id],
        )
        # A potência é opcional: um motor que não a forneça deixa o trecho com
        # corrente e sem potência, em vez de perder os dois resultados.
        active = _rows(line_active.get(segment_index, ()), len(nodes), step_count)
        reactive = _rows(line_reactive.get(segment_index, ()), len(nodes), step_count)
        if active is None or reactive is None:
            continue
        losses_active = tuple(line_active_losses.get(segment_index, ()))
        losses_reactive = tuple(line_reactive_losses.get(segment_index, ()))
        if len(losses_active) != step_count or len(losses_reactive) != step_count:
            losses_active = ()
            losses_reactive = ()
        segment_powers[segment_index] = SegmentPowers(
            nodes=nodes,
            active=active,
            reactive=reactive,
            active_losses=losses_active,
            reactive_losses=losses_reactive,
        )
