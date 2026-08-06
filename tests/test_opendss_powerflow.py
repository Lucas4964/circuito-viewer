"""Testes do núcleo do fluxo de potência, com um motor OpenDSS falso.

O motor real é a DLL do ``py_dss_interface``; o que se testa aqui é tudo o que
fica **entre** a exportação e a DLL: quais comandos são enviados, em que ordem,
e como os vetores devolvidos viram resultado associado a trecho e barra.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from circuit_viewer.model import (
    CableModel,
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.opendss_export import LINES_FILENAME, build_export
from circuit_viewer.opendss_powerflow import (
    PowerFlowIssue,
    run_power_flow,
)
from circuit_viewer.opendss_settings import OpenDssLoadSettings
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
) -> LineNetworkModel:
    return LineNetworkModel(
        bars,
        ["T0", "T1"],
        list(codes),
        ["13", "13"],
        [0, 1],
        [1, 2],
        ["", ""],
        list(cables),
        ["", ""],
        [250.0, 400.0],
    )


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
        return 1 if self._owner.line_names else 0

    def next(self) -> int:
        self._position += 1
        return 1 if self._position < len(self._owner.line_names) else 0

    @property
    def name(self) -> str:
        return self._owner.line_names[self._position]


class FakeCktElement:
    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner

    @property
    def _line(self) -> str:
        return self._owner.lines.name

    @property
    def num_conductors(self) -> int:
        return len(self._owner.line_nodes[self._line])

    @property
    def node_order(self) -> list[int]:
        # Dois terminais: o OpenDSS lista os dois em sequência.
        return list(self._owner.line_nodes[self._line]) * 2

    @property
    def currents_mag_ang(self) -> list[float]:
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
                values.extend([magnitude + 1_000.0 * terminal, 0.0])
        return values


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
        node_names: tuple[str, ...] = (
            "barra_a.1",
            "barra_a.2",
            "barra_a.3",
            "barra_b.1",
        ),
        node_voltages: tuple[float, ...] = (7_960.0, 7_950.0, 7_940.0, 7_900.0),
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
        self.node_names = node_names
        self.node_voltages = node_voltages
        self.diverging_steps = diverging_steps
        self.circuit = FakeCircuit(self)
        self.lines = FakeLines(self)
        self.cktelement = FakeCktElement(self)
        self.solution = FakeSolution(self)

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
        self.assertLess(
            max(i for i, line in enumerate(lines) if line.startswith("BatchEdit")),
            lines.index("Solve"),
        )


class IssueTests(unittest.TestCase):
    def test_issue_is_immutable(self) -> None:
        issue = PowerFlowIssue("T0", "motivo")

        with self.assertRaises(Exception):
            issue.reason = "outro"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
