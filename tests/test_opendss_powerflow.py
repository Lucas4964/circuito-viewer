"""Testes do núcleo do fluxo de potência, com um motor OpenDSS falso.

O motor real é a DLL do ``py_dss_interface``; o que se testa aqui é tudo o que
fica **entre** a exportação e a DLL: quais comandos são enviados, em que ordem,
e como os vetores devolvidos viram resultado associado a trecho e barra.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from circuit_viewer.calculation_levels import default_calculation_levels
from circuit_viewer.curvas import Curve
from circuit_viewer.generator_update import (
    GeneratorScheduleMode,
    calculate_generator_demands,
)
from circuit_viewer.model import (
    CableModel,
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
    RegulatorModel,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.opendss_export import (
    LINES_FILENAME,
    OpenDssLibraryExportError,
    SINGLE_PHASE_GENERATORS_FILENAME,
    THREE_PHASE_GENERATORS_FILENAME,
    TWO_PHASE_GENERATORS_FILENAME,
    build_export,
)
from circuit_viewer.opendss_library import (
    ArrangementDefinition,
    CableDefinition,
    ConductorPosition,
    OpenDssLibraryCatalog,
)
from circuit_viewer.opendss_line_mode import OpenDssLineParameterMode
from circuit_viewer.opendss_mapping_store import (
    LibraryNameMapping,
    OpenDssLibraryMappings,
)
from circuit_viewer.opendss_powerflow import (
    DEFAULT_DSS_VMINPU,
    PowerFlowIssue,
    RegulatorTap,
    apparent_power,
    line_voltages,
    power_factor,
    run_power_flow,
    three_phase_power,
    voltage_unbalance,
)
from circuit_viewer.opendss_settings import OpenDssLoadSettings
from circuit_viewer.opendss_solution import DEFAULT_MAX_POWER_FLOW_ITER
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("1", "D", 1, "1"),
        PhaseMappingEntry("2", "E", 1, "2"),
        PhaseMappingEntry("3", "F", 1, "3"),
        PhaseMappingEntry("13", "DEF", 3, "1.2.3"),
    )
)


def make_cables(iadm: str = "340") -> CableModel:
    return CableModel(
        ["CB1"],
        ["1"],
        ["4/0"],
        [iadm],
        ["0,00824"],
        ["0,367"],
        ["0,42"],
        ["3,5"],
        ["0,6"],
        ["1,2"],
        ["0,2"],
        ["0,4"],
        ["CABO 4/0"],
        [""],
    )


def make_bars(codes: tuple[str, ...] = ("BARRA_A", "BARRA_B", "BARRA_C")) -> CircuitModel:
    return CircuitModel(
        ["B0", "B1", "B2"],
        list(codes),
        [500_000.0, 500_100.0, 500_200.0],
        [8_000_000.0, 8_000_000.0, 8_000_000.0],
        UtmCrs(21, northern=False),
    )


def make_network(
    bars: CircuitModel,
    *,
    codes: tuple[str, str] = ("TR-1", "TR-2"),
    cables: tuple[str, str] = ("CB1", "CB1"),
    arrangements: tuple[str, str] = ("", ""),
) -> LineNetworkModel:
    return LineNetworkModel(
        bars,
        ["T0", "T1"],
        list(codes),
        ["13", "13"],
        [0, 1],
        [1, 2],
        list(arrangements),
        list(cables),
        ["", ""],
        [250.0, 400.0],
    )


def make_library_inputs(
    *,
    normal_amps: float = 321.0,
) -> tuple[OpenDssLibraryCatalog, OpenDssLibraryMappings]:
    catalog = OpenDssLibraryCatalog(
        cables=[
            CableDefinition(
                "cable-library-id",
                "CABO LIB",
                rac=0.1,
                gmr=0.01,
                diameter=0.02,
                normal_amps=normal_amps,
            )
        ],
        arrangements=[
            ArrangementDefinition(
                "arrangement-library-id",
                "ARRANJO LIB",
                3,
                "m",
                [
                    ConductorPosition(-1.0, 10.0),
                    ConductorPosition(0.0, 10.0),
                    ConductorPosition(1.0, 10.0),
                ],
            )
        ],
    )
    mappings = OpenDssLibraryMappings(
        cables=(LibraryNameMapping("CB1", "CABO LIB"),),
        arrangements=(LibraryNameMapping("AR1", "ARRANJO LIB"),),
    )
    return catalog, mappings


def make_catalog(
    network: LineNetworkModel,
    *,
    switches: SwitchModel | None = None,
    voltage: str = "13,8",
) -> CircuitCatalogModel:
    return CircuitCatalogModel.build(
        network,
        switches,
        [CircuitDefinition("C1", "B0", "ALIMENTADOR", voltage)],
    )


def make_regulators(
    network: LineNetworkModel,
    *,
    segment_index: int = 0,
    code: str = "RG01",
    vnom: str = "13,8",
) -> RegulatorModel:
    """Um regulador trifásico no trecho indicado, exportável sem ocorrências.

    ``code`` sem acento nem espaço vira o nome do ``Transformer`` sem
    saneamento (``REG-<code>-<letra>``), o que mantém os testes lendo o nome
    exato sem duplicar ``sanitize_dss_name``.
    """

    return RegulatorModel(
        network,
        ["REGU1"],
        [segment_index],
        ["EXT-1"],
        [code],
        ["Y"],
        ["333"],
        ["0,1"],
        ["32"],
        ["0"],
        ["200"],
        [vnom],
    )


def make_generator_updates(catalog: CircuitCatalogModel):  # noqa: ANN201
    """Um gerador monofasico calculado, pronto para entrar no OpenDSS."""

    bars = catalog.segments.bars
    loads = LoadModel(
        bars,
        ["LG0"],
        [1],
        [""],
        ["CARGA-GERADOR"],
        [""],
        [""],
        [""],
        ["1"],
        [""],
    )
    generators = GeneratorModel(
        loads,
        ["G0"],
        [0],
        [""],
        ["SOLAR"],
        [""],
        [""],
        [""],
        ["CURVA-ORIGINAL"],
        ["720"],
        ["CONS0"],
        ["SOLAR"],
        [""],
        ["Gerador solar"],
        ["1"],
    )
    schedule = default_calculation_levels()
    return calculate_generator_demands(
        generators,
        catalog,
        PHASES,
        Curve("CURVA", "Constante", (2.0,) * 24),
        (schedule,),
        (GeneratorScheduleMode.DEFAULT,),
    ).model


class FakeSolution:
    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner

    def solve(self) -> None:
        self._owner.step += 1
        self._owner.solves.append(self._owner.step)

    @property
    def converged(self) -> bool:
        return self._owner.step not in self._owner.diverging_steps


class FakeLines:
    """Itera nomes de ``Line`` no mesmo protocolo first()/next() do OpenDSS."""

    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner
        self._position = -1

    def first(self) -> int:
        self._position = 0
        if not self._owner.line_names:
            return 0
        self._owner.active_element = ("line", self.name)
        return 1

    def next(self) -> int:
        self._position += 1
        if self._position >= len(self._owner.line_names):
            return 0
        self._owner.active_element = ("line", self.name)
        return 1

    @property
    def name(self) -> str:
        return self._owner.line_names[self._position]


class FakeCktElement:
    """Grandezas do **elemento ativo**, seja ele Line ou Transformer.

    Na DLL, ``Lines.First/Next`` e ``Transformers.First/Next`` trocam o elemento
    ativo, e ``CktElement`` sempre responde por ele — medido. O falso reproduz
    isso porque o colhedor de reguladores depende exatamente dessa propriedade:
    ele lê tap e corrente da mesma visita ao ``Transformer``.

    Uma unidade de regulador é monofásica com dois enrolamentos, então o
    terminal traz **dois** condutores — a fase e o neutro —, e o ``node_order``
    sai como ``[n, 0, n, 0]`` (medido contra a DLL). O nó de terra é o que o
    filtro do colhedor precisa descartar.
    """

    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner

    @property
    def _line(self) -> str:
        return self._owner.lines.name

    @property
    def _transformer(self) -> str | None:
        active = self._owner.active_element
        return active[1] if active is not None and active[0] == "transformer" else None

    def _transformer_nodes(self, name: str) -> tuple[int, ...]:
        return (self._owner.transformer_node(name), 0)

    @property
    def num_conductors(self) -> int:
        name = self._transformer
        if name is not None:
            return len(self._transformer_nodes(name))
        return len(self._owner.line_nodes[self._line])

    @property
    def node_order(self) -> list[int]:
        # Dois terminais: o OpenDSS lista os dois em sequência.
        name = self._transformer
        if name is not None:
            return list(self._transformer_nodes(name)) * 2
        return list(self._owner.line_nodes[self._line]) * 2

    @property
    def currents_mag_ang(self) -> list[float]:
        name = self._transformer
        if name is not None:
            magnitude = self._owner.transformer_currents[
                name
            ] + 100.0 * (self._owner.step - 1)
            angle = self._owner.transformer_current_angles[name]
            # Fase e neutro, nos dois terminais: o retorno pelo neutro leva o
            # mesmo módulo e a fase oposta.
            return [
                magnitude,
                angle,
                magnitude,
                angle - 180.0,
                magnitude,
                angle - 180.0,
                magnitude,
                angle,
            ]
        nodes = self._owner.line_nodes[self._line]
        base = self._owner.line_currents[self._line]
        values: list[float] = []
        for terminal in range(2):
            for position in range(len(nodes)):
                magnitude = (
                    base[position]
                    + 100.0 * (self._owner.step - 1)
                    # Cada Compile desloca a grandeza, para um teste conseguir
                    # provar de qual circuito veio o resultado guardado.
                    + 10_000.0 * (self._owner.compiles - 1)
                )
                angle = self._owner.line_current_angles[self._line][position]
                values.extend([magnitude + 1_000.0 * terminal, angle])
        return values

    @property
    def powers(self) -> list[float]:
        """Pares (kW, kvar) por condutor, terminais em sequência.

        Mesmo layout do ``currents_mag_ang``, e o terminal 2 leva o sinal
        trocado — como num elemento real, onde o que entra por um terminal sai
        pelo outro. Assim um teste prova que só o terminal 1 é guardado.
        """

        name = self._transformer
        if name is not None:
            active, reactive = self._owner.transformer_powers[name]
            shift = 10.0 * (self._owner.step - 1)
            # O condutor de neutro não carrega potência, como na DLL.
            return [
                active + shift,
                reactive,
                0.0,
                0.0,
                -(active + shift),
                -reactive,
                0.0,
                0.0,
            ]
        nodes = self._owner.line_nodes[self._line]
        base = self._owner.line_powers[self._line]
        values: list[float] = []
        for terminal in range(2):
            sign = 1.0 if terminal == 0 else -1.0
            for active, reactive in base[: len(nodes)]:
                shift = 10.0 * (self._owner.step - 1)
                values.extend([sign * (active + shift), sign * reactive])
        return values

    @property
    def losses(self) -> list[float]:
        name = self._transformer
        if name is not None:
            return list(self._owner.transformer_losses[name])
        return list(self._owner.line_losses[self._line])


class FakeTransformers:
    """Itera Transformer no protocolo first()/next(), como o OpenDSS.

    ``tap`` depende do passo (``owner.step``) pelo mesmo motivo de
    ``FakeCircuit.buses_vmag``: sem variação por patamar, um teste não consegue
    provar que os quatro retratos de tap foram colhidos separadamente, e não
    quatro cópias do mesmo estado.
    """

    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner
        self._position = -1
        self.wdg = 1

    def first(self) -> int:
        self._position = 0
        if not self._owner.transformer_taps:
            return 0
        self._owner.active_element = ("transformer", self.name)
        return 1

    def next(self) -> int:
        self._position += 1
        if self._position >= len(self._owner.transformer_taps):
            return 0
        self._owner.active_element = ("transformer", self.name)
        return 1

    @property
    def name(self) -> str:
        return list(self._owner.transformer_taps)[self._position]

    @property
    def tap(self) -> float:
        base = self._owner.transformer_taps[self.name]
        # step é 1-indexado (ver FakeSolution.solve); o primeiro patamar não
        # tem deslocamento algum, como em buses_vmag.
        return base + self._owner.transformer_tap_step * (self._owner.step - 1)

    @property
    def min_tap(self) -> float:
        return self._owner.transformer_tap_limits[0]

    @property
    def max_tap(self) -> float:
        return self._owner.transformer_tap_limits[1]

    @property
    def num_taps(self) -> int:
        return self._owner.transformer_num_taps


class FakeCircuit:
    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner

    @property
    def nodes_names(self) -> list[str]:
        return list(self._owner.node_names)

    @property
    def buses_vmag(self) -> list[float]:
        return [
            value + 10.0 * (self._owner.step - 1)
            for value in self._owner.node_voltages
        ]

    @property
    def buses_vmag_pu(self) -> list[float]:
        return [
            value / 7_967.0 for value in self.buses_vmag
        ]

    @property
    def buses_volts(self) -> list[float]:
        """Dois doubles por nó — parte real e imaginária, como o AllBusVolts.

        Deriva do próprio ``buses_vmag`` para módulo lido e módulo do fasor
        nunca discordarem, e usa o ângulo declarado no motor.
        """

        angles = self._owner.node_angles
        values: list[float] = []
        for position, magnitude in enumerate(self.buses_vmag):
            # Cíclico: os testes que encurtam a lista de nós continuam
            # exercitando o guarda de tamanho do colhedor, e não este falso.
            radians = math.radians(angles[position % len(angles)])
            values.extend(
                [magnitude * math.cos(radians), magnitude * math.sin(radians)]
            )
        return values


class FakeEngine:
    """Motor falso: guarda os comandos e devolve grandezas previsíveis.

    As grandezas dependem do passo (``step``) para que um teste consiga provar
    que os quatro patamares foram colhidos separadamente, e não quatro cópias do
    mesmo estado.
    """

    def __init__(
        self,
        *,
        line_names: tuple[str, ...] = ("tr-1", "tr-2"),
        line_nodes: dict[str, tuple[int, ...]] | None = None,
        line_currents: dict[str, tuple[float, ...]] | None = None,
        line_powers: dict[str, tuple[tuple[float, float], ...]] | None = None,
        line_losses: dict[str, tuple[float, float]] | None = None,
        transformer_taps: dict[str, float] | None = None,
        transformer_tap_step: float = 0.0,
        transformer_tap_limits: tuple[float, float] = (0.9, 1.1),
        transformer_num_taps: int = 32,
        transformer_nodes: dict[str, int] | None = None,
        transformer_currents: dict[str, float] | None = None,
        transformer_current_angles: dict[str, float] | None = None,
        transformer_powers: dict[str, tuple[float, float]] | None = None,
        transformer_losses: dict[str, tuple[float, float]] | None = None,
        node_names: tuple[str, ...] = (
            "barra_a.1",
            "barra_a.2",
            "barra_a.3",
            "barra_b.1",
        ),
        node_voltages: tuple[float, ...] = (7_960.0, 7_950.0, 7_940.0, 7_900.0),
        node_angles: tuple[float, ...] = (0.0, -120.0, 120.0, 0.0),
        line_current_angles: dict[str, tuple[float, ...]] | None = None,
        diverging_steps: frozenset[int] = frozenset(),
    ) -> None:
        self.commands: list[str] = []
        self.solves: list[int] = []
        self.step = 0
        self.compiles = 0
        self.line_names = line_names
        self.line_nodes = line_nodes or {
            name: (1, 2, 3) for name in line_names
        }
        self.line_currents = line_currents or {
            name: (10.0, 20.0, 30.0) for name in line_names
        }
        self.line_powers = line_powers or {
            name: ((100.0, 30.0), (110.0, 33.0), (120.0, 36.0))
            for name in line_names
        }
        # Em **watts**, como o OpenDSS de verdade devolve — ao contrário de
        # ``powers``, que vem em kW. Medido contra 3·R·I² num trecho conhecido.
        self.line_losses = line_losses or {
            name: (1_500.0, 4_500.0) for name in line_names
        }
        self.node_names = node_names
        self.node_voltages = node_voltages
        self.node_angles = node_angles
        self.line_current_angles = line_current_angles or {
            name: (0.0, -120.0, 120.0) for name in line_names
        }
        self.diverging_steps = diverging_steps
        self.transformer_taps = transformer_taps or {}
        self.transformer_tap_step = transformer_tap_step
        self.transformer_tap_limits = transformer_tap_limits
        self.transformer_num_taps = transformer_num_taps
        # Qual elemento a DLL considera ativo. É o que faz cktelement responder
        # ora pela Line, ora pelo Transformer, como no motor de verdade.
        self.active_element: tuple[str, str] | None = None
        self.transformer_nodes = transformer_nodes or {}
        # Grandezas por unidade, derivadas do nó para que cada fase se
        # distinga: sem isso um teste não provaria que as três unidades de um
        # regulador trifásico viraram três colunas distintas do trecho.
        names = tuple(self.transformer_taps)
        self.transformer_currents = transformer_currents or {
            name: 10.0 * self.transformer_node(name) for name in names
        }
        self.transformer_current_angles = transformer_current_angles or {
            name: -120.0 * (self.transformer_node(name) - 1) for name in names
        }
        self.transformer_powers = transformer_powers or {
            name: (100.0 * self.transformer_node(name), 30.0 * self.transformer_node(name))
            for name in names
        }
        # Em watts, como o OpenDSS devolve — a mesma convenção de line_losses.
        self.transformer_losses = transformer_losses or {
            name: (1_000.0 * self.transformer_node(name), 2_000.0 * self.transformer_node(name))
            for name in names
        }
        self.circuit = FakeCircuit(self)
        self.lines = FakeLines(self)
        self.transformers = FakeTransformers(self)
        self.cktelement = FakeCktElement(self)
        self.solution = FakeSolution(self)

    def transformer_node(self, name: str) -> int:
        """Nó DSS da unidade, deduzido do sufixo de fase do nome exportado.

        O exportador nomeia cada unidade ``REG-<código>-<letra>``, então a
        última letra é a fase. É a mesma correspondência que ``fases2.json``
        estabelece; fixá-la aqui mantém o falso legível sem uma configuração de
        fases só para ele.
        """

        if name in self.transformer_nodes:
            return self.transformer_nodes[name]
        return {"D": 1, "E": 2, "F": 3}.get(name.strip()[-1:].upper(), 1)

    def text(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("Compile"):
            # Compilar reinicia a solução, como no OpenDSS.
            self.step = 0
            self.compiles += 1
        return ""


class PowerFlowRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.network = make_network(make_bars())
        self.catalog = make_catalog(self.network)
        self.cables = make_cables()

    def _run(self, engine: FakeEngine, **kwargs):  # noqa: ANN003, ANN202
        return run_power_flow(
            engine,
            self.catalog,
            self.cables,
            PHASES,
            [0],
            workspace=self.workspace,
            **kwargs,
        )

    def test_writes_the_same_files_the_export_would(self) -> None:
        engine = FakeEngine()

        self._run(engine)

        expected = {
            filename
            for filename, _ in build_export(
                self.catalog,
                self.cables,
                PHASES,
                [0],
            ).files
        }
        written = {
            path.name
            for path in self.workspace.rglob("*")
            if path.is_file()
        }
        self.assertEqual(written, expected)
        self.assertIn(LINES_FILENAME, written)

    def test_generator_snapshot_is_written_and_compiled_with_the_power_flow(self) -> None:
        engine = FakeEngine()
        updates = make_generator_updates(self.catalog)

        result = self._run(engine, generator_updates=updates)

        self.assertIs(result.generator_updates, updates)
        self.assertEqual(result.exported_generators, 1)
        self.assertEqual(result.discarded_generators, 0)
        written = {
            path.name: path
            for path in self.workspace.rglob("*")
            if path.is_file()
        }
        for filename in (
            SINGLE_PHASE_GENERATORS_FILENAME,
            TWO_PHASE_GENERATORS_FILENAME,
            THREE_PHASE_GENERATORS_FILENAME,
        ):
            self.assertIn(filename, written)
        mono = written[SINGLE_PHASE_GENERATORS_FILENAME].read_text(encoding="utf-8")
        self.assertIn(
            "mult=[-2.000000 -2.000000 -2.000000 -2.000000]",
            mono,
        )
        master_path = Path(engine.commands[1].removeprefix("Compile [").removesuffix("]"))
        master = master_path.read_text(encoding="utf-8")
        self.assertIn(f"Redirect {SINGLE_PHASE_GENERATORS_FILENAME}", master)

    def test_compiles_the_master_and_steps_one_patamar_at_a_time(self) -> None:
        engine = FakeEngine()

        result = self._run(engine)

        self.assertEqual(engine.commands[0], "Clear")
        self.assertTrue(engine.commands[1].startswith("Compile ["))
        self.assertIn("Set mode=daily", engine.commands)
        self.assertIn("Set number=1", engine.commands)
        self.assertIn("Set time=(0, 0)", engine.commands)
        # Um Solve por patamar, não um Solve de number=4.
        self.assertEqual(engine.solves, [1, 2, 3, 4])
        self.assertEqual(result.step_count, 4)
        self.assertEqual(result.solved_circuits, ("C1",))

    def test_the_iteration_ceiling_is_reemitted_after_the_compile(self) -> None:
        # O engine é singleton: sem reemitir, um MaxIter de outra execução
        # continuaria valendo. E um teto baixo abandona a solução antes de o
        # laço de controle rodar, deixando os reguladores onde estavam.
        engine = FakeEngine()

        self._run(engine, max_power_flow_iterations=120)

        compile_at = next(
            index
            for index, command in enumerate(engine.commands)
            if command.startswith("Compile [")
        )
        self.assertIn("Set MaxIter=120", engine.commands[compile_at:])
        self.assertIn("Set MaxControlIter=100", engine.commands[compile_at:])
        # E também no master, para quem abrir os arquivos direto no OpenDSS.
        master = next(self.workspace.rglob("*_Master.dss"))
        self.assertIn("Set MaxIter=120", master.read_text(encoding="utf-8"))

    def test_the_default_iteration_ceiling_is_emitted(self) -> None:
        engine = FakeEngine()

        self._run(engine)

        self.assertIn(
            f"Set MaxIter={DEFAULT_MAX_POWER_FLOW_ITER}",
            engine.commands,
        )

    def test_the_result_records_the_ceiling_it_used(self) -> None:
        # Sem isto não há como saber, olhando a aplicação, qual teto valeu.
        result = self._run(FakeEngine(), max_power_flow_iterations=120)

        self.assertEqual(result.max_power_flow_iterations, 120)

    def test_the_result_records_the_control_iterations_of_each_step(self) -> None:
        result = self._run(FakeEngine())

        self.assertEqual(
            [circuit_id for circuit_id, _, _ in result.control_iterations],
            ["C1"] * 4,
        )
        self.assertEqual(
            [step for _, step, _ in result.control_iterations],
            [0, 1, 2, 3],
        )

    def test_the_result_records_the_voltage_extremes_of_each_step(self) -> None:
        # É o número que separa "faltou iteração" de "não existe solução". Sem
        # ele o relatório só sabe dizer que o patamar reprovou, e os dois casos
        # pedem providências opostas.
        engine = FakeEngine(
            node_voltages=(7_967.0, 7_000.0, 5_000.0, 4_000.0),
        )

        result = self._run(
            engine,
            load_settings=OpenDssLoadSettings(
                voltage_limits_enabled=True,
                vminpu=0.7,
            ),
        )

        self.assertEqual(len(result.step_voltages), 4)
        first = result.step_voltages[0]
        self.assertEqual((first.circuit_id, first.step), ("C1", 0))
        self.assertAlmostEqual(first.maximum_pu, 1.0)
        self.assertAlmostEqual(first.minimum_pu, 4_000.0 / 7_967.0)
        self.assertEqual(first.vminpu, 0.7)
        # 5.000 V (0,628 pu) e 4.000 V (0,502 pu) estão abaixo do corte;
        # 7.000 V (0,879 pu) não.
        self.assertEqual(first.nodes_below, 2)
        self.assertIs(result.voltages_for("C1", 0), first)
        self.assertIsNone(result.voltages_for("C1", 9))

    def test_the_reported_cut_is_the_one_that_applied(self) -> None:
        # Com os limites desligados nenhum BatchEdit sai no master, então quem
        # vale é o padrão do OpenDSS — e é ele que o relatório precisa citar,
        # não o número guardado na preferência.
        result = self._run(FakeEngine())

        self.assertEqual(result.step_voltages[0].vminpu, DEFAULT_DSS_VMINPU)

    def test_dead_nodes_do_not_become_the_minimum(self) -> None:
        # Neutro, terra e barra fora por chave aberta dão zero. Se entrassem,
        # a tensão mínima de todo circuito seria zero e não diria nada.
        engine = FakeEngine(
            node_voltages=(7_960.0, 7_950.0, 7_940.0, 0.0),
        )

        result = self._run(engine)

        self.assertAlmostEqual(
            result.step_voltages[0].minimum_pu,
            7_940.0 / 7_967.0,
        )

    def test_the_internal_master_does_not_solve(self) -> None:
        # Compile executa o arquivo: um Solve no master resolveria os quatro
        # patamares antes do laço abaixo, custando o dobro do tempo e deixando
        # o tap do último patamar como ponto de partida do primeiro.
        engine = FakeEngine()

        self._run(engine)

        master = next(self.workspace.rglob("*_Master.dss")).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Solve", master)
        self.assertNotIn("Set number=", master)
        # O resto do arquivo continua montando o circuito por inteiro.
        self.assertIn("calcvoltagebases", master)
        self.assertIn("Set mode=daily", master)

    def test_currents_land_on_the_right_segment(self) -> None:
        engine = FakeEngine()

        result = self._run(engine)

        # TR-1 é o trecho 0 e TR-2 o trecho 1, segundo o índice do exportador.
        self.assertEqual(set(result.segment_currents), {0, 1})
        first = result.segment_currents[0]
        self.assertEqual(first.nodes, (1, 2, 3))
        self.assertEqual(first.magnitudes[0], (10.0, 20.0, 30.0))

    def test_each_patamar_is_harvested_separately(self) -> None:
        engine = FakeEngine()

        result = self._run(engine)

        currents = result.segment_currents[0]
        self.assertEqual(len(currents.magnitudes), 4)
        # O motor falso soma 100 A por passo; quatro linhas distintas provam que
        # a colheita aconteceu entre os solves, e não só no fim.
        self.assertEqual(
            [row[0] for row in currents.magnitudes],
            [10.0, 110.0, 210.0, 310.0],
        )

    def test_only_the_first_terminal_is_kept(self) -> None:
        engine = FakeEngine()

        result = self._run(engine)

        # O motor falso soma 1.000 A ao terminal 2; nada disso pode aparecer.
        self.assertEqual(result.segment_currents[0].magnitudes[0], (10.0, 20.0, 30.0))

    def test_voltages_land_on_the_right_bar(self) -> None:
        engine = FakeEngine()

        result = self._run(engine)

        self.assertEqual(set(result.bar_voltages), {0, 1})
        first = result.bar_voltages[0]
        self.assertEqual(first.nodes, (1, 2, 3))
        self.assertEqual(first.magnitudes[0], (7_960.0, 7_950.0, 7_940.0))
        self.assertEqual(len(first.magnitudes), 4)
        self.assertEqual(first.magnitudes[1], (7_970.0, 7_960.0, 7_950.0))
        # A barra B1 só tem o nó 1 na lista devolvida pelo OpenDSS.
        self.assertEqual(result.bar_voltages[1].nodes, (1,))

    def test_per_unit_comes_from_the_dedicated_vector(self) -> None:
        engine = FakeEngine()

        result = self._run(engine)

        voltages = result.bar_voltages[0]
        self.assertAlmostEqual(voltages.per_unit[0][0], 7_960.0 / 7_967.0)

    def test_neutral_nodes_are_not_voltages(self) -> None:
        engine = FakeEngine(
            node_names=("barra_a.1", "barra_a.0"),
            node_voltages=(7_960.0, 0.0),
        )

        result = self._run(engine)

        self.assertEqual(result.bar_voltages[0].nodes, (1,))

    def test_ampacity_comes_from_the_phase_cable(self) -> None:
        engine = FakeEngine()

        result = self._run(engine)

        self.assertEqual(result.segment_currents[0].ampacity, 340.0)

    def test_missing_ampacity_is_none(self) -> None:
        self.cables = make_cables(iadm="")
        engine = FakeEngine()

        result = self._run(engine)

        self.assertIsNone(result.segment_currents[0].ampacity)

    def test_peak_is_the_largest_reading(self) -> None:
        engine = FakeEngine()

        result = self._run(engine)

        self.assertEqual(result.segment_currents[0].peak, 330.0)

    def test_unconverged_step_is_recorded_without_aborting(self) -> None:
        engine = FakeEngine(diverging_steps=frozenset({2}))

        result = self._run(engine)

        self.assertEqual(result.unconverged, (("C1", 1),))
        # O estudo segue: os demais patamares continuam sendo colhidos.
        self.assertEqual(len(result.segment_currents[0].magnitudes), 4)
        self.assertTrue(result.has_warnings)

    def test_unknown_element_names_are_ignored(self) -> None:
        engine = FakeEngine(line_names=("tr-1", "linha-que-nao-existe"))

        result = self._run(engine)

        self.assertEqual(set(result.segment_currents), {0})

    def test_progress_reaches_the_total(self) -> None:
        engine = FakeEngine()
        seen: list[tuple[int, int]] = []

        self._run(engine, progress=lambda current, total: seen.append((current, total)))

        self.assertEqual(seen[-1], (4, 4))
        self.assertEqual(len(seen), 4)

    def test_cancellation_raises_before_touching_the_engine(self) -> None:
        engine = FakeEngine()

        with self.assertRaises(InterruptedError):
            self._run(engine, cancel_check=lambda: True)

        self.assertEqual(engine.solves, [])

    def test_library_mode_harvests_with_no_legacy_cable_model(self) -> None:
        network = make_network(
            make_bars(),
            cables=(" CB1 ", "CB1"),
            arrangements=("AR1", "AR1"),
        )
        catalog = make_catalog(network)
        library_catalog, library_mappings = make_library_inputs(normal_amps=321.0)
        engine = FakeEngine()

        result = run_power_flow(
            engine,
            catalog,
            None,
            PHASES,
            [0],
            workspace=self.workspace,
            line_parameter_mode=OpenDssLineParameterMode.LIBRARY,
            library_catalog=library_catalog,
            library_mappings=library_mappings,
        )

        self.assertIsNone(result.cables)
        self.assertEqual(result.segment_currents[0].ampacity, 321.0)
        self.assertEqual(result.segment_currents[1].ampacity, 321.0)
        self.assertEqual(engine.compiles, 1)
        self.assertEqual(len(engine.solves), 4)

    def test_rejects_a_circuit_index_out_of_range(self) -> None:
        with self.assertRaises(IndexError):
            run_power_flow(
                FakeEngine(),
                self.catalog,
                self.cables,
                PHASES,
                [7],
                workspace=self.workspace,
            )

    def test_rejects_a_non_positive_step_count(self) -> None:
        with self.assertRaises(ValueError):
            self._run(FakeEngine(), step_count=0)


class PowerFlowDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_circuit_without_a_master_is_skipped_with_a_reason(self) -> None:
        network = make_network(make_bars())
        catalog = make_catalog(network, voltage="")
        engine = FakeEngine()

        result = run_power_flow(
            engine,
            catalog,
            make_cables(),
            PHASES,
            [0],
            workspace=self.workspace,
        )

        self.assertEqual(result.skipped_circuits, ("C1",))
        self.assertEqual(result.solved_circuits, ())
        self.assertTrue(result.is_empty)
        self.assertTrue(result.has_warnings)
        self.assertTrue(any("master" in issue.reason for issue in result.issues))
        # Sem master não há o que compilar.
        self.assertEqual(engine.solves, [])

    def test_names_differing_only_in_case_are_both_discarded(self) -> None:
        # O exportador aceita os dois (a checagem dele é sensível a caixa), mas
        # o OpenDSS os trata como o mesmo objeto.
        network = make_network(make_bars(), codes=("TR-1", "tr-1"))
        catalog = make_catalog(network)
        engine = FakeEngine(line_names=("tr-1",))

        result = run_power_flow(
            engine,
            catalog,
            make_cables(),
            PHASES,
            [0],
            workspace=self.workspace,
        )

        self.assertEqual(result.segment_currents, {})
        self.assertTrue(
            any("caixa" in issue.reason for issue in result.issues),
            result.issues,
        )

    def test_bar_names_differing_only_in_case_are_both_discarded(self) -> None:
        network = make_network(make_bars(codes=("BARRA_A", "barra_a", "BARRA_C")))
        catalog = make_catalog(network)
        engine = FakeEngine()

        result = run_power_flow(
            engine,
            catalog,
            make_cables(),
            PHASES,
            [0],
            workspace=self.workspace,
        )

        self.assertNotIn(0, result.bar_voltages)
        self.assertNotIn(1, result.bar_voltages)
        self.assertTrue(any("caixa" in issue.reason for issue in result.issues))

    def test_mismatched_voltage_vector_is_reported(self) -> None:
        network = make_network(make_bars())
        catalog = make_catalog(network)
        engine = FakeEngine(
            node_names=("barra_a.1", "barra_a.2"),
            node_voltages=(7_960.0,),  # um valor a menos que os nós
        )

        result = run_power_flow(
            engine,
            catalog,
            make_cables(),
            PHASES,
            [0],
            workspace=self.workspace,
        )

        self.assertEqual(result.bar_voltages, {})
        self.assertTrue(
            any("quantidade diferente" in issue.reason for issue in result.issues)
        )


class MultipleCircuitTests(unittest.TestCase):
    """Rede sobreposta: o primeiro circuito processado é o dono do resultado."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.network = make_network(make_bars())
        self.catalog = CircuitCatalogModel.build(
            self.network,
            None,
            [
                CircuitDefinition("C1", "B0", "A", "13,8"),
                CircuitDefinition("C2", "B2", "B", "13,8"),
            ],
        )

    def test_each_circuit_gets_its_own_compile(self) -> None:
        engine = FakeEngine()

        result = run_power_flow(
            engine,
            self.catalog,
            make_cables(),
            PHASES,
            [0, 1],
            workspace=self.workspace,
        )

        compiles = [c for c in engine.commands if c.startswith("Compile")]
        self.assertEqual(len(compiles), 2)
        self.assertEqual(result.solved_circuits, ("C1", "C2"))
        self.assertEqual(engine.solves, [1, 2, 3, 4, 1, 2, 3, 4])

    def test_library_errors_from_all_circuits_are_grouped_before_first_compile(self) -> None:
        engine = FakeEngine()
        first_error = OpenDssLibraryExportError(["referencia um cabo ausente"])
        second_error = OpenDssLibraryExportError(["referencia um arranjo ausente"])

        with patch(
            "circuit_viewer.opendss_powerflow.build_export",
            side_effect=[first_error, second_error],
        ) as builder, self.assertRaises(OpenDssLibraryExportError) as raised:
            run_power_flow(
                engine,
                self.catalog,
                None,
                PHASES,
                [0, 1],
                workspace=self.workspace,
                line_parameter_mode=OpenDssLineParameterMode.LIBRARY,
                library_catalog=OpenDssLibraryCatalog(),
                library_mappings=OpenDssLibraryMappings(),
            )

        self.assertEqual(builder.call_count, 2)
        self.assertEqual(builder.call_args_list[0].args[3], (0,))
        self.assertEqual(builder.call_args_list[1].args[3], (1,))
        self.assertEqual(
            raised.exception.errors,
            (
                "Circuito C1: referencia um cabo ausente",
                "Circuito C2: referencia um arranjo ausente",
            ),
        )
        self.assertEqual(engine.commands, [])
        self.assertEqual(engine.solves, [])

    def test_the_first_circuit_owns_a_shared_segment(self) -> None:
        # O motor falso soma 10.000 A por Compile: se o segundo circuito
        # sobrescrevesse o primeiro, o patamar 0 viria 10.010 em vez de 10.
        engine = FakeEngine()

        result = run_power_flow(
            engine,
            self.catalog,
            make_cables(),
            PHASES,
            [0, 1],
            workspace=self.workspace,
        )

        self.assertEqual(result.segment_currents[0].magnitudes[0], (10.0, 20.0, 30.0))

    def test_the_second_circuit_still_contributes_its_own_elements(self) -> None:
        # Só o circuito 2 alcança o trecho 1 a partir da raiz B2? Não — a rede
        # é compartilhada. O que este teste garante é que processar o segundo
        # circuito não apaga nem duplica nada do primeiro.
        engine = FakeEngine()

        result = run_power_flow(
            engine,
            self.catalog,
            make_cables(),
            PHASES,
            [0, 1],
            workspace=self.workspace,
        )

        self.assertEqual(set(result.segment_currents), {0, 1})
        for currents in result.segment_currents.values():
            self.assertEqual(len(currents.magnitudes), 4)

    def test_each_circuit_writes_to_its_own_folder(self) -> None:
        engine = FakeEngine()

        run_power_flow(
            engine,
            self.catalog,
            make_cables(),
            PHASES,
            [0, 1],
            workspace=self.workspace,
        )

        folders = sorted(path.name for path in self.workspace.iterdir())
        self.assertEqual(len(folders), 2)
        self.assertNotEqual(folders[0], folders[1])

    def test_progress_covers_every_circuit(self) -> None:
        engine = FakeEngine()
        seen: list[tuple[int, int]] = []

        run_power_flow(
            engine,
            self.catalog,
            make_cables(),
            PHASES,
            [0, 1],
            workspace=self.workspace,
            progress=lambda current, total: seen.append((current, total)),
        )

        self.assertEqual(seen[-1], (8, 8))


class SwitchCurrentTests(unittest.TestCase):
    """Chaves são ``Line`` no modelo exportado e vêm no mesmo laço."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_switch_segment_receives_currents_and_ampacity(self) -> None:
        network = make_network(make_bars())
        switches = SwitchModel(
            network,
            ["CH1"],
            ["TC"],
            ["C1"],
            [1],
            ["CHV-001"],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        catalog = make_catalog(network, switches=switches)
        engine = FakeEngine(line_names=("tr-1", "chv-001"))

        result = run_power_flow(
            engine,
            catalog,
            make_cables(),
            PHASES,
            [0],
            workspace=self.workspace,
        )

        # A chave está no trecho 1, e o nome dela vem do CODIGO da chave.
        self.assertIn(1, result.segment_currents)
        self.assertEqual(result.segment_currents[1].magnitudes[0], (10.0, 20.0, 30.0))
        # O condutor daquele ponto continua sendo o CABOF_ID do trecho.
        self.assertEqual(result.segment_currents[1].ampacity, 340.0)


class LoadSettingsTests(unittest.TestCase):
    """Os limites configurados precisam chegar ao arquivo que o motor compila.

    O ``BatchEdit`` não passa por ``engine.text()``: ele vive dentro do master,
    e é o ``Compile`` que o executa. Por isso o teste lê o arquivo gravado na
    pasta de trabalho — é exatamente o que a DLL enxerga.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.network = make_network(make_bars())
        self.catalog = make_catalog(self.network)
        self.loads = LoadModel(
            self.network.bars,
            ["CG1"],
            [2],
            ["EXT-1"],
            ["CARGA-1"],
            ["10"],
            ["12"],
            ["220"],
            ["13"],
            ["Y"],
        )
        self.patterns = LoadPatternModel(
            self.loads,
            [
                tuple(
                    LoadPatternRecord("CG1", npat, "1", "2", "3", "4", "5", "6")
                    for npat in range(4)
                )
            ],
        )

    def _run(self, load_settings):  # noqa: ANN001, ANN202
        return run_power_flow(
            FakeEngine(),
            self.catalog,
            make_cables(),
            PHASES,
            [0],
            workspace=self.workspace,
            loads=self.loads,
            patterns=self.patterns,
            load_settings=load_settings,
        )

    def _master_text(self) -> str:
        master = next(self.workspace.rglob("*_Master.dss"))
        return master.read_text(encoding="utf-8")

    def test_settings_reach_the_compiled_master(self) -> None:
        self._run(
            OpenDssLoadSettings(
                voltage_limits_enabled=True,
                vminpu=0.8,
                vmaxpu=1.2,
            )
        )

        text = self._master_text()
        self.assertIn("BatchEdit Load..* vminpu=0.8", text)
        self.assertIn("BatchEdit Load..* vmaxpu=1.2", text)

    def test_without_settings_the_master_is_unchanged(self) -> None:
        self._run(None)

        self.assertNotIn("BatchEdit", self._master_text())

    def test_disabled_settings_leave_the_master_unchanged(self) -> None:
        self._run(OpenDssLoadSettings(vminpu=0.8, vmaxpu=1.2))

        self.assertNotIn("BatchEdit", self._master_text())

    def test_the_commands_precede_the_solve(self) -> None:
        self._run(
            OpenDssLoadSettings(voltage_limits_enabled=True, vminpu=0.9)
        )

        lines = self._master_text().splitlines()
        # BatchEdit é executivo: depois das Load definidas, antes de resolver.
        self.assertLess(
            max(i for i, line in enumerate(lines) if line.startswith("Redirect")),
            min(i for i, line in enumerate(lines) if line.startswith("BatchEdit")),
        )
        # O master do fluxo interno não resolve — quem resolve é o laço de
        # patamares —, então a âncora é o início da seção de solução.
        self.assertLess(
            max(i for i, line in enumerate(lines) if line.startswith("BatchEdit")),
            lines.index("Set ControlMode=Static"),
        )


class LineVoltageTests(unittest.TestCase):
    """A tensão de linha é subtração de fasores, não de módulos."""

    def test_balanced_system_gives_root_three_at_thirty_degrees(self) -> None:
        pairs, magnitudes, angles = line_voltages(
            (1, 2, 3),
            ((1.0, 1.0, 1.0),),
            ((0.0, -120.0, 120.0),),
        )

        self.assertEqual(pairs, ((1, 2), (2, 3), (3, 1)))
        for value in magnitudes[0]:
            self.assertAlmostEqual(value, math.sqrt(3.0))
        # VDE adianta 30°, VEF atrasa 90° e VFD adianta 150°: o resultado
        # clássico do triângulo de tensões.
        self.assertAlmostEqual(angles[0][0], 30.0)
        self.assertAlmostEqual(angles[0][1], -90.0)
        self.assertAlmostEqual(angles[0][2], 150.0)

    def test_magnitudes_alone_would_be_wrong(self) -> None:
        # Duas fases de mesmo módulo dão diferença de módulos zero, mas a
        # tensão entre elas é √3 vezes a de fase. É a prova de que compor o
        # fasor não é preciosismo.
        _, magnitudes, _ = line_voltages(
            (1, 2),
            ((7_960.0, 7_960.0),),
            ((0.0, -120.0),),
        )

        self.assertAlmostEqual(magnitudes[0][0], 7_960.0 * math.sqrt(3.0))

    def test_only_pairs_with_both_phases_are_emitted(self) -> None:
        pairs, magnitudes, angles = line_voltages(
            (1, 2),
            ((1.0, 1.0),),
            ((0.0, -120.0),),
        )

        self.assertEqual(pairs, ((1, 2),))
        self.assertEqual(len(magnitudes[0]), 1)
        self.assertEqual(len(angles[0]), 1)

    def test_single_phase_bar_has_no_line_voltage(self) -> None:
        self.assertEqual(
            line_voltages((1,), ((1.0,),), ((0.0,),)),
            ((), (), ()),
        )

    def test_every_patamar_is_converted(self) -> None:
        _, magnitudes, _ = line_voltages(
            (1, 2, 3),
            tuple((1.0 + step, 1.0 + step, 1.0 + step) for step in range(4)),
            tuple((0.0, -120.0, 120.0) for _ in range(4)),
        )

        self.assertEqual(len(magnitudes), 4)
        self.assertAlmostEqual(magnitudes[3][0], 4.0 * math.sqrt(3.0))

    def test_angles_reach_the_result(self) -> None:
        engine = FakeEngine()
        with tempfile.TemporaryDirectory() as directory:
            result = run_power_flow(
                engine,
                make_catalog(make_network(make_bars())),
                make_cables(),
                PHASES,
                [0],
                workspace=Path(directory),
            )

        # O motor falso declara 0/-120/+120 nos três nós da barra A. A tensão
        # passa por retangular e volta, então compara com tolerância.
        for read, expected in zip(
            result.bar_voltages[0].angles[0], (0.0, -120.0, 120.0), strict=True
        ):
            self.assertAlmostEqual(read, expected)
        # A corrente vem direto do currents_mag_ang, sem conversão nenhuma.
        self.assertEqual(
            result.segment_currents[0].angles[0], (0.0, -120.0, 120.0)
        )


class VoltageUnbalanceTests(unittest.TestCase):
    """FD% do PRODIST: razão entre sequência negativa e positiva."""

    def test_balanced_system_has_no_unbalance(self) -> None:
        values = voltage_unbalance(
            (1, 2, 3), ((1.0, 1.0, 1.0),), ((0.0, -120.0, 120.0),)
        )

        self.assertEqual(len(values), 1)
        self.assertAlmostEqual(values[0], 0.0)

    def test_one_low_phase_gives_the_closed_form(self) -> None:
        # Va=0,9 e Vb=Vc=1,0: V₊=2,9/3 e V₋=−0,1/3, então FD = 0,1/2,9.
        values = voltage_unbalance(
            (1, 2, 3), ((0.9, 1.0, 1.0),), ((0.0, -120.0, 120.0),)
        )

        self.assertAlmostEqual(values[0], 0.1 / 2.9 * 100.0, places=6)

    def test_fewer_than_three_phases_is_undefined(self) -> None:
        self.assertEqual(
            voltage_unbalance((1, 2), ((1.0, 1.0),), ((0.0, -120.0),)),
            (),
        )


class PowerMathTests(unittest.TestCase):
    """As grandezas derivadas de P e Q, com o sinal do OpenDSS intacto."""

    def test_apparent_power_is_the_hypotenuse_and_its_angle(self) -> None:
        magnitudes, angles = apparent_power(((100.0, 0.0),), ((0.0, 50.0),))

        self.assertAlmostEqual(magnitudes[0][0], 100.0)
        self.assertAlmostEqual(angles[0][0], 0.0)
        # Só reativo: 90° de defasagem.
        self.assertAlmostEqual(magnitudes[0][1], 50.0)
        self.assertAlmostEqual(angles[0][1], 90.0)

    def test_three_phase_power_sums_the_complex_phases(self) -> None:
        rows = three_phase_power(
            ((100.0, 110.0, 120.0),), ((30.0, 33.0, 36.0),)
        )

        active, reactive, apparent, angle = rows[0]
        self.assertAlmostEqual(active, 330.0)
        self.assertAlmostEqual(reactive, 99.0)
        self.assertAlmostEqual(apparent, math.hypot(330.0, 99.0))
        self.assertAlmostEqual(angle, math.degrees(math.atan2(99.0, 330.0)))

    def test_reverse_flow_keeps_its_sign(self) -> None:
        # É o sinal que diz se o elemento recebe ou fornece pelo terminal 1;
        # perder isso apagaria o sentido do fluxo.
        rows = three_phase_power(((-100.0, -100.0, -100.0),), ((-30.0,) * 3,))
        active, reactive, apparent, _ = rows[0]

        self.assertAlmostEqual(active, -300.0)
        self.assertAlmostEqual(reactive, -90.0)
        self.assertGreater(apparent, 0.0)
        self.assertLess(power_factor(((-100.0,),), ((-30.0,),))[0][0], 0.0)

    def test_power_factor_appends_the_three_phase_total(self) -> None:
        rows = power_factor(((100.0, 100.0, 100.0),), ((0.0, 0.0, 0.0),))

        # Três fases mais o total.
        self.assertEqual(len(rows[0]), 4)
        for value in rows[0]:
            self.assertAlmostEqual(value, 1.0)

    def test_zero_power_has_no_factor(self) -> None:
        self.assertEqual(power_factor(((0.0,),), ((0.0,),))[0][0], 0.0)


class SegmentPowerHarvestTests(unittest.TestCase):
    """Potência e perdas chegam ao trecho, só do terminal 1."""

    def _run(self, engine: FakeEngine):  # noqa: ANN202
        with tempfile.TemporaryDirectory() as directory:
            return run_power_flow(
                engine,
                make_catalog(make_network(make_bars())),
                make_cables(),
                PHASES,
                [0],
                workspace=Path(directory),
            )

    def test_powers_land_on_the_segment(self) -> None:
        result = self._run(FakeEngine())

        powers = result.segment_powers[0]
        self.assertEqual(powers.nodes, (1, 2, 3))
        self.assertEqual(powers.active[0], (100.0, 110.0, 120.0))
        self.assertEqual(powers.reactive[0], (30.0, 33.0, 36.0))

    def test_only_the_first_terminal_is_kept(self) -> None:
        # O motor falso inverte o sinal no terminal 2; nada disso pode aparecer.
        result = self._run(FakeEngine())

        self.assertTrue(all(value > 0 for value in result.segment_powers[0].active[0]))

    def test_each_patamar_is_harvested_separately(self) -> None:
        result = self._run(FakeEngine())

        # O falso soma 10 kW por passo.
        self.assertEqual(
            [row[0] for row in result.segment_powers[0].active],
            [100.0, 110.0, 120.0, 130.0],
        )

    def test_losses_arrive_per_patamar(self) -> None:
        result = self._run(FakeEngine())

        powers = result.segment_powers[0]
        # O falso devolve 1500 W; o colhedor converte para kW.
        self.assertEqual(powers.active_losses, (1.5,) * 4)
        self.assertEqual(powers.reactive_losses, (4.5,) * 4)


class RegulatorTapTests(unittest.TestCase):
    """O tap volta do Transformer e denuncia o fim de curso."""

    def _tap(self, value: float) -> RegulatorTap:
        return RegulatorTap(phase="D", tap=value, minimum=0.9, maximum=1.1)

    def test_tap_inside_the_range_is_not_at_limit(self) -> None:
        self.assertFalse(self._tap(1.0437).at_limit)

    def test_tap_on_either_end_is_at_limit(self) -> None:
        self.assertTrue(self._tap(1.1).at_limit)
        self.assertTrue(self._tap(0.9).at_limit)

    def test_degenerate_range_is_never_at_limit(self) -> None:
        # Sem curso não há o que esgotar; evita alarme falso.
        self.assertFalse(
            RegulatorTap(phase="D", tap=1.0, minimum=1.0, maximum=1.0).at_limit
        )

    def _stepped(self, tap: float, num_taps: int = 32) -> RegulatorTap:
        return RegulatorTap(
            phase="D", tap=tap, minimum=0.9, maximum=1.1, num_taps=num_taps
        )

    def test_neutral_tap_is_step_zero(self) -> None:
        self.assertEqual(self._stepped(1.0).step, 0)

    def test_step_above_neutral(self) -> None:
        # ±10% em 32 passos ⇒ 0,00625 pu por passo; 0,05/0,00625 = 8.
        self.assertEqual(self._stepped(1.05).step, 8)

    def test_step_below_neutral_is_negative(self) -> None:
        self.assertEqual(self._stepped(0.95).step, -8)

    def test_step_rounds_to_the_nearest_integer(self) -> None:
        # 0,003/0,00625 = 0,48 ⇒ arredonda para 0.
        self.assertEqual(self._stepped(1.003).step, 0)
        # 0,004/0,00625 = 0,64 ⇒ arredonda para 1.
        self.assertEqual(self._stepped(1.004).step, 1)

    def test_step_at_either_limit(self) -> None:
        self.assertEqual(self._stepped(1.1).step, 16)
        self.assertEqual(self._stepped(0.9).step, -16)

    def test_zero_num_taps_is_step_zero(self) -> None:
        # Sem número de passos declarado (o campo tem padrão 0), não há como
        # calcular a granularidade; melhor 0 do que ZeroDivisionError.
        self.assertEqual(RegulatorTap(phase="D", tap=1.05, minimum=0.9, maximum=1.1).step, 0)

    def test_degenerate_range_is_step_zero(self) -> None:
        self.assertEqual(
            RegulatorTap(
                phase="D", tap=1.0, minimum=1.0, maximum=1.0, num_taps=32
            ).step,
            0,
        )


class RegulatorTapHarvestTests(unittest.TestCase):
    """O tap é colhido a cada patamar, não só depois do último solve()."""

    def _run(self, engine: FakeEngine, *, segments=None):  # noqa: ANN001, ANN202
        network = make_network(make_bars())
        with tempfile.TemporaryDirectory() as directory:
            return run_power_flow(
                engine,
                make_catalog(network),
                make_cables(),
                PHASES,
                [0],
                workspace=Path(directory),
                regulators=make_regulators(network),
            )

    def test_one_snapshot_per_patamar(self) -> None:
        engine = FakeEngine(
            transformer_taps={
                "REG-RG01-D": 1.0,
                "REG-RG01-E": 1.0,
                "REG-RG01-F": 1.0,
            }
        )

        result = self._run(engine)

        # Quatro retratos, um por NPAT — não mais um só, depois do último.
        self.assertEqual(len(result.regulator_taps[0]), 4)
        for step_taps in result.regulator_taps[0]:
            self.assertEqual({tap.phase for tap in step_taps}, {"D", "E", "F"})

    def test_the_tap_changes_across_patamares(self) -> None:
        # Um passo (0,00625 pu, ±10% em 32) a mais por patamar.
        engine = FakeEngine(
            transformer_taps={
                "REG-RG01-D": 1.0,
                "REG-RG01-E": 1.0,
                "REG-RG01-F": 1.0,
            },
            transformer_tap_step=0.00625,
        )

        result = self._run(engine)

        steps_by_patamar = [
            next(tap.step for tap in step_taps if tap.phase == "D")
            for step_taps in result.regulator_taps[0]
        ]
        self.assertEqual(steps_by_patamar, [0, 1, 2, 3])

    def test_num_taps_reaches_the_result(self) -> None:
        engine = FakeEngine(
            transformer_taps={
                "REG-RG01-D": 1.0,
                "REG-RG01-E": 1.0,
                "REG-RG01-F": 1.0,
            },
            transformer_num_taps=16,
        )

        result = self._run(engine)

        first_tap = result.regulator_taps[0][0][0]
        self.assertEqual(first_tap.num_taps, 16)

    def test_a_tap_at_the_end_of_travel_is_reported(self) -> None:
        # O painel do trecho já denunciava a saturação um trecho por vez; o
        # relatório precisa dela reunida, porque um regulador que esgotou o
        # curso é a explicação mais direta para uma tensão que não sobe.
        engine = FakeEngine(
            transformer_taps={
                "REG-RG01-D": 1.1,
                "REG-RG01-E": 1.0,
                "REG-RG01-F": 1.0,
            }
        )

        result = self._run(engine)

        self.assertEqual(
            result.saturated_regulators,
            ((0, 0, ("D",)), (0, 1, ("D",)), (0, 2, ("D",)), (0, 3, ("D",))),
        )

    def test_taps_inside_the_range_report_nothing(self) -> None:
        engine = FakeEngine(
            transformer_taps={
                "REG-RG01-D": 1.0,
                "REG-RG01-E": 1.0,
                "REG-RG01-F": 1.0,
            }
        )

        self.assertEqual(self._run(engine).saturated_regulators, ())

    def test_the_regulated_segment_gets_current_from_the_transformer(self) -> None:
        # O trecho regulado não sai como Line — se saísse, a linha ficaria em
        # paralelo com o regulador. Sem colher do Transformer ele ficaria sem
        # corrente nem potência no painel, que era o estado até aqui.
        engine = FakeEngine(
            transformer_taps={
                "REG-RG01-D": 1.0,
                "REG-RG01-E": 1.0,
                "REG-RG01-F": 1.0,
            }
        )

        result = self._run(engine)

        currents = result.segment_currents[0]
        # As três unidades monofásicas viram as três colunas do trecho,
        # ordenadas por nó como uma Line trifásica sairia.
        self.assertEqual(currents.nodes, (1, 2, 3))
        self.assertEqual(len(currents.magnitudes), 4)
        # Padrão do motor falso: 10 A por nó, deslocados 100 A por patamar.
        self.assertEqual(currents.magnitudes[0], (10.0, 20.0, 30.0))
        self.assertEqual(currents.magnitudes[1], (110.0, 120.0, 130.0))

    def test_the_regulated_segment_gets_power_from_the_transformer(self) -> None:
        engine = FakeEngine(
            transformer_taps={
                "REG-RG01-D": 1.0,
                "REG-RG01-E": 1.0,
                "REG-RG01-F": 1.0,
            }
        )

        result = self._run(engine)

        powers = result.segment_powers[0]
        self.assertEqual(powers.nodes, (1, 2, 3))
        # Sinal preservado: positivo entra pelo terminal 1, como nas linhas.
        self.assertEqual(powers.active[0], (100.0, 200.0, 300.0))
        self.assertEqual(powers.reactive[0], (30.0, 60.0, 90.0))
        # As perdas somam as três unidades e vêm em kW, não em watts.
        self.assertAlmostEqual(powers.active_losses[0], 6.0)
        self.assertAlmostEqual(powers.reactive_losses[0], 12.0)

    def test_a_single_phase_regulator_gives_a_one_node_segment(self) -> None:
        # Uma unidade só: o trecho sai com uma coluna, não com três de traço.
        engine = FakeEngine(transformer_taps={"REG-RG01-D": 1.0})

        result = self._run(engine)

        self.assertEqual(result.segment_currents[0].nodes, (1,))
        self.assertEqual(result.segment_currents[0].magnitudes[0], (10.0,))
        self.assertEqual(
            [tap.phase for tap in result.regulator_taps[0][0]],
            ["D"],
        )
        self.assertAlmostEqual(result.segment_powers[0].active_losses[0], 1.0)

    def test_the_regulated_segment_keeps_its_ampacity(self) -> None:
        # O trecho continua tendo CABOF_ID mesmo virando regulador, então o
        # carregamento percentual do painel continua calculável.
        engine = FakeEngine(transformer_taps={"REG-RG01-D": 1.0})

        result = self._run(engine)

        self.assertEqual(result.segment_currents[0].ampacity, 340.0)

    def test_a_segment_without_a_regulator_has_no_taps(self) -> None:
        engine = FakeEngine(
            transformer_taps={
                "REG-RG01-D": 1.0,
                "REG-RG01-E": 1.0,
                "REG-RG01-F": 1.0,
            }
        )

        result = self._run(engine)

        self.assertEqual(result.regulator_taps.get(1, ()), ())


class IssueTests(unittest.TestCase):
    def test_issue_is_immutable(self) -> None:
        issue = PowerFlowIssue("T0", "motivo")

        with self.assertRaises(Exception):
            issue.reason = "outro"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
