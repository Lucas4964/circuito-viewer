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
maiúsculas de minúsculas e devolve tudo em minúsculas. Como a checagem de
homônimos do exportador **é** sensível a caixa, dois códigos que difiram só na
caixa chegam aqui como colisão: as duas entradas são descartadas com
diagnóstico, em vez de atribuir a corrente de um trecho ao outro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from .model import (
    CableModel,
    CircuitCatalogModel,
    LoadModel,
    LoadPatternModel,
)
from .opendss_engine import DssEngine
from .opendss_export import (
    LOAD_PATTERN_COUNT,
    MAX_REPORTED_ISSUES,
    build_export,
    bus_namer,
    parse_number,
    sanitize_dss_name,
)
from .opendss_settings import OpenDssLoadSettings
from .phase_config import PhaseConfiguration


ProgressCallback = Callable[[int, int], None]

# Mesma cadência de verificação das demais análises do projeto.
_CANCEL_CHECK_INTERVAL = 4_096

# Comandos que reconduzem a solução ao primeiro patamar, um passo por vez. A
# ordem importa: `number` e `stepsize` são lidos pelo Solve, e `time` precisa
# ser o último para não ser reposicionado pelos anteriores.
_STEP_MODE_COMMANDS = (
    "Set mode=daily",
    "Set stepsize=1h",
    "Set number=1",
    "Set time=(0, 0)",
)


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
    """

    nodes: tuple[int, ...]
    magnitudes: tuple[tuple[float, ...], ...]
    ampacity: float | None = None

    @property
    def peak(self) -> float:
        """Maior módulo entre todos os patamares e nós."""

        return max((value for row in self.magnitudes for value in row), default=0.0)


@dataclass(frozen=True, slots=True)
class BarVoltages:
    """Tensões de uma barra, por patamar e por nó.

    ``magnitudes`` em volts e ``per_unit`` na base da barra; os dois vetores têm
    a mesma forma de ``nodes``, porque saem alinhados do mesmo ``AllNodeNames``.
    """

    nodes: tuple[int, ...]
    magnitudes: tuple[tuple[float, ...], ...]
    per_unit: tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class PowerFlowResult:
    """Resultado de uma execução, pronto para ser associado à tela.

    Os cinco modelos de entrada viajam no resultado para a interface poder
    revalidá-los por identidade na chegada, como as demais análises fazem: uma
    reimportação durante a execução torna o resultado obsoleto.
    """

    catalog: CircuitCatalogModel
    cables: CableModel
    phase_configuration: PhaseConfiguration
    loads: LoadModel | None
    patterns: LoadPatternModel | None
    step_count: int
    segment_currents: Mapping[int, SegmentCurrents] = field(default_factory=dict)
    bar_voltages: Mapping[int, BarVoltages] = field(default_factory=dict)
    solved_circuits: tuple[str, ...] = ()
    skipped_circuits: tuple[str, ...] = ()
    unconverged: tuple[tuple[str, int], ...] = ()
    issues: tuple[PowerFlowIssue, ...] = ()
    omitted_issues: int = 0

    @property
    def has_warnings(self) -> bool:
        return bool(self.issues or self.unconverged or self.skipped_circuits)

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


def _ampacity(cables: CableModel, cable_id: str) -> float | None:
    cable = cables.record_for_id(cable_id) if cable_id else None
    if cable is None:
        return None
    value = parse_number(cable.iadm)
    return value if value is not None and value > 0.0 else None


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
    report: _PowerFlowReport,
    circuit_id: str,
    *,
    first_step: bool,
) -> None:
    """Distribui ``AllBusVMag``/``AllBusVMagPu`` pelas barras da aplicação.

    Os três vetores do OpenDSS são paralelos e cobrem o sistema inteiro, então
    uma leitura por passo resolve todas as barras — não há laço por elemento.
    """

    values = engine.circuit.buses_vmag
    pu_values = engine.circuit.buses_vmag_pu
    if len(values) != len(node_names) or len(pu_values) != len(node_names):
        report.add(
            circuit_id,
            "o OpenDSS devolveu tensões em quantidade diferente da lista de "
            "nós; as tensões deste circuito foram descartadas",
        )
        return
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


def _harvest_line_currents(
    engine: DssEngine,
    segment_by_line_name: Mapping[str, int],
    nodes: dict[int, tuple[int, ...]],
    magnitudes: dict[int, list[float]],
    cancel_check: Callable[[], bool] | None,
) -> None:
    """Percorre as ``Line`` do circuito lendo a corrente do terminal 1.

    Chaves também são ``Line`` no modelo exportado, então elas vêm de graça
    neste mesmo laço.
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
            conductors = int(engine.cktelement.num_conductors)
            readings = engine.cktelement.currents_mag_ang
            order = engine.cktelement.node_order
            # currents_mag_ang intercala módulo e ângulo por condutor, com os
            # terminais em sequência; o terminal 1 são os primeiros condutores.
            row: list[float] = []
            terminal_nodes: list[int] = []
            for position in range(min(conductors, len(order))):
                node = int(order[position])
                if node <= 0:
                    continue
                magnitude_at = 2 * position
                if magnitude_at >= len(readings):
                    break
                terminal_nodes.append(node)
                row.append(float(readings[magnitude_at]))
            if row:
                known = nodes.get(segment_index)
                if known is None:
                    nodes[segment_index] = tuple(terminal_nodes)
                    magnitudes[segment_index] = list(row)
                elif known == tuple(terminal_nodes):
                    magnitudes[segment_index].extend(row)
        has_line = bool(engine.lines.next())


def run_power_flow(
    engine: DssEngine,
    catalog: CircuitCatalogModel,
    cables: CableModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    workspace: Path,
    loads: LoadModel | None = None,
    patterns: LoadPatternModel | None = None,
    load_settings: OpenDssLoadSettings | None = None,
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
    """

    if step_count <= 0:
        raise ValueError("O número de patamares deve ser positivo.")

    selected = _selected_indices(catalog, circuit_indices)
    report = _PowerFlowReport()
    segment_currents: dict[int, SegmentCurrents] = {}
    bar_voltages: dict[int, BarVoltages] = {}
    solved: list[str] = []
    skipped: list[str] = []
    unconverged: list[tuple[str, int]] = []
    total = len(selected) * step_count
    completed = 0

    for circuit_index in selected:
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Fluxo de potência cancelado.")
        definition = catalog.definition(circuit_index)
        bundle = build_export(
            catalog,
            cables,
            phase_configuration,
            (circuit_index,),
            loads=loads,
            patterns=patterns,
            load_settings=load_settings,
            cancel_check=cancel_check,
        )
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
        for command in _STEP_MODE_COMMANDS:
            engine.text(command)

        node_names = tuple(engine.circuit.nodes_names)
        circuit_nodes: dict[int, list[int]] = {}
        circuit_magnitudes: dict[int, list[float]] = {}
        circuit_per_unit: dict[int, list[float]] = {}
        line_nodes: dict[int, tuple[int, ...]] = {}
        line_magnitudes: dict[int, list[float]] = {}

        for step in range(step_count):
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Fluxo de potência cancelado.")
            engine.solution.solve()
            if not engine.solution.converged:
                unconverged.append((definition.circuit_id, step))
            _harvest_bus_voltages(
                engine,
                node_names,
                bar_by_bus_name,
                circuit_nodes,
                circuit_magnitudes,
                circuit_per_unit,
                report,
                definition.circuit_id,
                first_step=step == 0,
            )
            _harvest_line_currents(
                engine,
                segment_by_line_name,
                line_nodes,
                line_magnitudes,
                cancel_check,
            )
            completed += 1
            if progress is not None:
                progress(min(completed, total), total)

        _merge_circuit_results(
            catalog,
            cables,
            step_count,
            circuit_nodes,
            circuit_magnitudes,
            circuit_per_unit,
            line_nodes,
            line_magnitudes,
            bar_voltages,
            segment_currents,
            report,
        )
        solved.append(definition.circuit_id)

    return PowerFlowResult(
        catalog=catalog,
        cables=cables,
        phase_configuration=phase_configuration,
        loads=loads,
        patterns=patterns,
        step_count=step_count,
        segment_currents=segment_currents,
        bar_voltages=bar_voltages,
        solved_circuits=tuple(solved),
        skipped_circuits=tuple(skipped),
        unconverged=tuple(unconverged),
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
    cables: CableModel,
    step_count: int,
    circuit_nodes: Mapping[int, Sequence[int]],
    circuit_magnitudes: Mapping[int, Sequence[float]],
    circuit_per_unit: Mapping[int, Sequence[float]],
    line_nodes: Mapping[int, tuple[int, ...]],
    line_magnitudes: Mapping[int, Sequence[float]],
    bar_voltages: dict[int, BarVoltages],
    segment_currents: dict[int, SegmentCurrents],
    report: _PowerFlowReport,
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
        if magnitudes is None or per_unit is None:
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
        )

    for segment_index, nodes in line_nodes.items():
        if segment_index in segment_currents:
            continue
        magnitudes = _rows(line_magnitudes.get(segment_index, ()), len(nodes), step_count)
        if magnitudes is None:
            report.add(
                segments.segment_ids[segment_index],
                "as correntes lidas não completaram todos os patamares; o "
                "trecho ficou sem resultado",
            )
            continue
        segment_currents[segment_index] = SegmentCurrents(
            nodes=nodes,
            magnitudes=magnitudes,
            ampacity=_ampacity(
                cables,
                segments.record(segment_index).phase_cable_id,
            ),
        )
