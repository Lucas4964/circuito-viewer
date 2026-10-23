"""Correção de demanda no motor: adaptador, exportação corrigida e DLL real.

O motor falso resolve um alimentador de brinquedo — saída = Σ cargas + perdas
quadráticas — sobre os nomes que o exportador de fato emitiu. É o bastante para
provar o que fica entre o método e a DLL: quais cargas recebem os fatores, em
que ordem kW e kvar são gravados e que cada solve é reposicionado no patamar.
"""

from __future__ import annotations

import math
import re
import tempfile
import unittest
from pathlib import Path

from circuit_viewer.demand_correction import (
    CorrectionSettings,
    DemandCorrection,
    DemandScope,
    PatamarStatus,
    PatamarTarget,
)
from circuit_viewer.model import LoadModel, LoadPatternModel, LoadPatternRecord
from circuit_viewer.opendss_demand_correction import (
    run_demand_correction,
    scoped_load_names,
)
from circuit_viewer.opendss_engine import (
    acquire_engine,
    ascii_workspace,
    power_flow_available,
)
from circuit_viewer.opendss_export import (
    SINGLE_PHASE_GENERATORS_FILENAME,
    THREE_PHASE_LOADS_FILENAME,
    build_export,
)
from circuit_viewer.opendss_powerflow import run_power_flow
from circuit_viewer.opendss_settings import OpenDssLoadSettings
from tests.test_opendss_energy_export import (
    PHASES as ENERGY_PHASES,
    energy_network,
    files as bundle_files,
)
from tests.test_opendss_powerflow import (
    PHASES,
    make_bars,
    make_cables,
    make_catalog,
    make_generator_updates,
    make_network,
)


ACTIVE = (100.0, 200.0, 300.0, 150.0)
REACTIVE = (40.0, 80.0, 100.0, 60.0)


def make_loads(bars):  # noqa: ANN001, ANN201
    """Uma carga trifásica e uma monofásica, com P e Q diferentes por patamar."""

    loads = LoadModel(
        bars,
        ["L1", "L2"],
        [1, 2],
        ["", ""],
        ["CARGA-1", "CARGA-2"],
        ["", ""],
        ["", ""],
        ["", ""],
        ["13", "1"],
        ["", ""],
    )

    def group(load_id: str, scale: float):  # noqa: ANN202
        return tuple(
            LoadPatternRecord(
                load_id,
                npat,
                str(ACTIVE[npat] * scale),
                str(ACTIVE[npat] * scale),
                str(ACTIVE[npat] * scale),
                str(REACTIVE[npat] * scale),
                str(REACTIVE[npat] * scale),
                str(REACTIVE[npat] * scale),
            )
            for npat in range(4)
        )

    return loads, LoadPatternModel(loads, [group("L1", 1.0), group("L2", 0.5)])


def shape_values(text: str, shape: str) -> tuple[list[float], list[float]]:
    line = next(
        item for item in text.splitlines() if item.startswith(f"New LoadShape.{shape} ")
    )
    active = re.search(r" mult=\[([^\]]*)\]", line).group(1).split()
    reactive = re.search(r" qmult=\[([^\]]*)\]", line).group(1).split()
    return [float(value) for value in active], [float(value) for value in reactive]


class FakeLoads:
    """``Loads`` do OpenDSS: cursor ``first``/``next``, ``idx`` e kW/kvar."""

    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner
        self._index = 0

    def first(self) -> int:
        self._index = 1
        self._owner.active = ("load", self._index)
        return 1 if self._owner.load_names else 0

    def next(self) -> int:
        if self._index >= len(self._owner.load_names):
            return 0
        self._index += 1
        self._owner.active = ("load", self._index)
        return 1

    @property
    def name(self) -> str:
        return self._owner.load_names[self._index - 1]

    @property
    def idx(self) -> int:
        return self._index

    @idx.setter
    def idx(self, value: int) -> None:
        self._index = int(value)
        self._owner.active = ("load", self._index)

    @property
    def kw(self) -> float:
        return self._owner.kw[self._index - 1]

    @kw.setter
    def kw(self, value: float) -> None:
        self._owner.writes.append(("kw", self.name))
        self._owner.kw[self._index - 1] = float(value)

    @property
    def kvar(self) -> float:
        return self._owner.kvar[self._index - 1]

    @kvar.setter
    def kvar(self, value: float) -> None:
        self._owner.writes.append(("kvar", self.name))
        self._owner.kvar[self._index - 1] = float(value)


class FakeCircuit:
    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner

    def set_active_element(self, name: str) -> int:
        self._owner.active = (name.casefold(), 0)
        return 0

    @property
    def losses(self) -> list[float]:
        # Em watts, como a DLL.
        return [self._owner.loss * 1000.0, 2.0 * self._owner.loss * 1000.0]


class FakeCktElement:
    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner

    @property
    def num_conductors(self) -> int:
        return 3

    @property
    def powers(self) -> list[float]:
        kind, index = self._owner.active
        if kind == "vsource.source":
            active, reactive = self._owner.head
            return [-active, -reactive, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        active, reactive = self._owner.load_power(index - 1)
        return [active, reactive, 0.0, 0.0]


class FakeSolution:
    def __init__(self, owner: FakeEngine) -> None:
        self._owner = owner
        self.converged = True

    def solve(self) -> None:
        self._owner.solve()


class FakeEngine:
    """Alimentador de brinquedo sobre os nomes que a exportação emitiu.

    ``base[nome]`` é o (P, Q) do perfil em cada patamar; a carga consome
    ``kW·P`` e ``kvar·Q``, como no OpenDSS com ``kW=1 kvar=1`` de base.
    """

    def __init__(self, base: dict[str, tuple[tuple[float, float], ...]], loss: float = 2e-5) -> None:
        self.base = base
        self.load_names = list(base)
        self.kw = [1.0] * len(base)
        self.kvar = [1.0] * len(base)
        self.loss_coefficient = loss
        self.commands: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.solved_steps: list[int] = []
        self.time_step = 0
        self.head = (0.0, 0.0)
        self.loss = 0.0
        self.active: tuple[str, int] = ("", 0)
        self.loads = FakeLoads(self)
        self.circuit = FakeCircuit(self)
        self.cktelement = FakeCktElement(self)
        self.solution = FakeSolution(self)
        self.lines = None
        self.transformers = None

    def text(self, command: str) -> str:
        self.commands.append(command)
        match = re.fullmatch(r"Set time=\((\d+), 0\)", command)
        if match:
            self.time_step = int(match.group(1))
        return ""

    def load_power(self, position: int) -> tuple[float, float]:
        name = self.load_names[position]
        active, reactive = self.base[name][self.time_step]
        return self.kw[position] * active, self.kvar[position] * reactive

    def solve(self) -> None:
        self.solved_steps.append(self.time_step)
        active = math.fsum(self.load_power(i)[0] for i in range(len(self.load_names)))
        reactive = math.fsum(self.load_power(i)[1] for i in range(len(self.load_names)))
        self.loss = self.loss_coefficient * (active**2 + reactive**2)
        self.head = (active + self.loss, reactive + 2.0 * self.loss)


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.workspace = Path(self._temp.name)
        bars = make_bars()
        self.catalog = make_catalog(make_network(bars))
        self.cables = make_cables()
        self.loads, self.patterns = make_loads(bars)
        self.generators = make_generator_updates(self.catalog)

    def _bundle(self):  # noqa: ANN202
        return build_export(
            self.catalog,
            self.cables,
            PHASES,
            (0,),
            loads=self.loads,
            patterns=self.patterns,
            generator_updates=self.generators,
            include_solve=False,
        )

    def _engine(self) -> FakeEngine:
        """Cargas do Interplan com a potência do perfil; o gerador a −2 kW."""

        bundle = self._bundle()
        base: dict[str, tuple[tuple[float, float], ...]] = {}
        for name in sorted(scoped_load_names(bundle, frozenset({DemandScope.CARGA}))):
            scale = 0.5 if name.startswith("carga-2") else 1.0
            base[name] = tuple(
                (ACTIVE[step] * scale, REACTIVE[step] * scale) for step in range(4)
            )
        generator_names = {
            line.split()[1].split(".", 1)[1].casefold()
            for line in dict(bundle.files)[SINGLE_PHASE_GENERATORS_FILENAME].splitlines()
            if line.startswith("New Load.")
        }
        self.assertTrue(generator_names)
        for name in generator_names:
            base[name] = ((-2.0, 0.0),) * 4
        return FakeEngine(base)

    def _run(self, engine: FakeEngine, targets, **kwargs):  # noqa: ANN001, ANN003, ANN202
        return run_demand_correction(
            engine,
            self.catalog,
            self.cables,
            PHASES,
            0,
            targets,
            kwargs.pop("scope", frozenset({DemandScope.CARGA})),
            workspace=self.workspace,
            loads=self.loads,
            patterns=self.patterns,
            generator_updates=self.generators,
            **kwargs,
        )

    def test_the_scope_comes_from_what_the_exporter_emitted(self) -> None:
        bundle = self._bundle()

        names = scoped_load_names(bundle, frozenset({DemandScope.CARGA}))

        self.assertEqual(
            names, {"carga-1-3f-d", "carga-1-3f-e", "carga-1-3f-f", "carga-2-1f-d"}
        )
        self.assertFalse(any(name.startswith("ger-") for name in names))
        self.assertEqual(scoped_load_names(bundle, frozenset({DemandScope.UC_BT})), frozenset())

    def test_each_patamar_reaches_its_target_with_independent_factors(self) -> None:
        engine = self._engine()
        # Base do NPAT 0: 3×100 + 50 kW e 3×40 + 20 kvar, menos o gerador.
        targets = (
            PatamarTarget(450.0, 40.0),
            PatamarTarget(300.0, 150.0),
            PatamarTarget(None, None),
            PatamarTarget(200.0, None),
        )

        result = self._run(engine, targets)

        settings = CorrectionSettings()
        for item, target in zip(result.patamares, targets):
            if target.is_empty:
                self.assertIs(item.status, PatamarStatus.SKIPPED)
                continue
            self.assertTrue(item.converged, item)
            self.assertLessEqual(abs(item.active_residual), settings.tolerance(target.active))
        self.assertEqual(result.patamares[3].reactive_factor, 1.0)
        # P sobe e Q desce no NPAT 0: os fatores são independentes.
        self.assertGreater(result.patamares[0].active_factor, 1.0)
        self.assertLess(result.patamares[0].reactive_factor, 1.0)
        # O gerador (−2 kW por fase) foi medido como fixo e não escalado.
        self.assertAlmostEqual(result.patamares[0].final.fixed_active, -2.0)
        self.assertEqual(result.scoped_load_count, 4)
        self.assertEqual(result.fixed_load_count, 1)
        self.assertEqual(result.correction().factors[2], (1.0, 1.0))

    def test_every_solve_is_repositioned_on_its_patamar(self) -> None:
        engine = self._engine()

        self._run(engine, (PatamarTarget(250.0, 40.0),) * 4)

        self.assertEqual(engine.commands[0], "Clear")
        self.assertTrue(engine.commands[1].startswith("Compile ["))
        self.assertIn("Set mode=daily", engine.commands)
        self.assertIn("Set number=1", engine.commands)
        times = [command for command in engine.commands if command.startswith("Set time=")]
        # O primeiro é o de _step_mode_commands; depois, um por solve.
        self.assertEqual(len(times) - 1, len(engine.solved_steps))
        self.assertEqual(sorted(set(engine.solved_steps)), [0, 1, 2, 3])
        self.assertEqual(engine.solved_steps, sorted(engine.solved_steps))

    def test_kw_is_always_written_before_kvar_and_only_in_the_scope(self) -> None:
        engine = self._engine()

        self._run(engine, (PatamarTarget(250.0, 40.0),) + (PatamarTarget(),) * 3)

        self.assertTrue(engine.writes)
        self.assertEqual(len(engine.writes) % 2, 0)
        for first, second in zip(engine.writes[0::2], engine.writes[1::2]):
            self.assertEqual(first[0], "kw")
            self.assertEqual(second[0], "kvar")
            self.assertEqual(first[1], second[1])
        touched = {name for _, name in engine.writes}
        self.assertFalse(any(name.startswith("ger-") for name in touched))

    def test_without_targets_or_scope_nothing_runs(self) -> None:
        engine = self._engine()
        with self.assertRaises(ValueError):
            self._run(engine, (PatamarTarget(),) * 4)
        with self.assertRaises(ValueError):
            self._run(engine, (PatamarTarget(1.0, 1.0),) * 4, scope=frozenset())
        with self.assertRaises(ValueError):
            # UCs só existem no modo Energia.
            self._run(engine, (PatamarTarget(1.0, 1.0),) * 4, scope=frozenset({DemandScope.UC_BT}))
        with self.assertRaises(ValueError):
            self._run(engine, (PatamarTarget(1.0, 1.0),) * 3)
        self.assertFalse(any(command.startswith("Compile") for command in engine.commands))

    def test_cancelling_interrupts(self) -> None:
        engine = self._engine()
        with self.assertRaises(InterruptedError):
            self._run(engine, (PatamarTarget(250.0, 40.0),) * 4, cancel_check=lambda: True)


class CorrectedExportTests(unittest.TestCase):
    FACTORS = ((1.5, 0.5), (2.0, 1.0), (1.0, 2.0), (0.5, 0.25))

    def test_the_profiles_carry_one_factor_per_patamar_and_kw_stays_one(self) -> None:
        bars = make_bars()
        catalog = make_catalog(make_network(bars))
        loads, patterns = make_loads(bars)
        generators = make_generator_updates(catalog)
        correction = DemandCorrection("C1", frozenset({DemandScope.CARGA}), self.FACTORS)

        def export(corrections):  # noqa: ANN001, ANN202
            return dict(
                build_export(
                    catalog,
                    make_cables(),
                    PHASES,
                    (0,),
                    loads=loads,
                    patterns=patterns,
                    generator_updates=generators,
                    demand_correction=corrections,
                ).files
            )

        corrected = export({"C1": correction})
        plain = export(None)
        other_circuit = export({"C2": DemandCorrection("C2", frozenset({DemandScope.CARGA}), self.FACTORS)})

        active, reactive = shape_values(corrected[THREE_PHASE_LOADS_FILENAME], "PERFIL-CARGA-1-3F-D")
        self.assertEqual(active, [value * f for value, (f, _) in zip(ACTIVE, self.FACTORS)])
        self.assertEqual(reactive, [value * f for value, (_, f) in zip(REACTIVE, self.FACTORS)])
        self.assertIn("kW=1 kvar=1 daily=PERFIL-CARGA-1-3F-D", corrected[THREE_PHASE_LOADS_FILENAME])
        # Gerador nunca escala; outro circuito não se mistura.
        self.assertEqual(
            corrected[SINGLE_PHASE_GENERATORS_FILENAME], plain[SINGLE_PHASE_GENERATORS_FILENAME]
        )
        self.assertEqual(other_circuit, plain)

    def test_energy_mode_scales_only_the_chosen_groups(self) -> None:
        composed, energy = energy_network()
        circuit_id = composed.catalog.definition(0).circuit_id
        correction = DemandCorrection(
            circuit_id, frozenset({DemandScope.UC_BT}), self.FACTORS, energy_mode=True
        )

        def everything(corrections):  # noqa: ANN001, ANN202
            bundle = build_export(
                composed.catalog,
                composed.cables,
                ENERGY_PHASES,
                (0,),
                loads=composed.loads,
                patterns=composed.patterns,
                energy=energy,
                demand_correction=corrections,
            )
            return "\n".join(bundle_files(bundle).values())

        corrected = everything({circuit_id: correction})
        plain = everything(None)

        base_active, base_reactive = shape_values(plain, "PERFIL-UC-BT-1001")
        active, reactive = shape_values(corrected, "PERFIL-UC-BT-1001")
        for step, (fp, fq) in enumerate(self.FACTORS):
            self.assertAlmostEqual(active[step], base_active[step] * fp, places=5)
            self.assertAlmostEqual(reactive[step], base_reactive[step] * fq, places=5)
        for shape in ("PERFIL-UC-MT-101", "PERFIL-IP-168662", "PERFIL-GD-BT-71", "PERFIL-GD-MT-201"):
            self.assertEqual(shape_values(corrected, shape), shape_values(plain, shape), shape)


@unittest.skipUnless(power_flow_available(), "DLL OpenDSS não disponível")
class RealEngineTests(unittest.TestCase):
    """O fluxo com a correção aplicada reproduz a medição, na DLL de verdade."""

    def _check(self, run_base, run_corrected, correct, circuit_id: str) -> None:  # noqa: ANN001
        with ascii_workspace() as workspace, acquire_engine() as engine:
            base = run_base(engine, workspace)
            # Mais carga em P e menos em Q. O P sobe por |P| para continuar
            # viável quando a GD inverte o fluxo na saída.
            targets = tuple(
                PatamarTarget(active + 0.5 * abs(active) + 1.0, reactive * 0.8 + 1.0)
                for active, reactive in base.head_powers[circuit_id]
            )
            result = correct(engine, workspace, targets)
            for item, (active, reactive) in zip(result.patamares, base.head_powers[circuit_id]):
                # Set time=(k, 0) + solve resolve o mesmo patamar k do fluxo; a
                # folga é a da solução iterativa do OpenDSS.
                self.assertAlmostEqual(item.initial.active, active, delta=1e-3)
                self.assertAlmostEqual(item.initial.reactive, reactive, delta=1e-3)
                self.assertTrue(item.converged, item)
            corrected = run_corrected(engine, workspace, {circuit_id: result.correction()})

        settings = CorrectionSettings()
        for target, (active, reactive) in zip(targets, corrected.head_powers[circuit_id]):
            self.assertLessEqual(abs(active - target.active), settings.tolerance(target.active))
            self.assertLessEqual(abs(reactive - target.reactive), settings.tolerance(target.reactive))

    def test_interplan_mode(self) -> None:
        bars = make_bars()
        catalog = make_catalog(make_network(bars))
        cables = make_cables()
        loads, patterns = make_loads(bars)
        models = dict(loads=loads, patterns=patterns, load_settings=OpenDssLoadSettings())

        self._check(
            lambda engine, workspace: run_power_flow(
                engine, catalog, cables, PHASES, [0], workspace=workspace, **models
            ),
            lambda engine, workspace, corrections: run_power_flow(
                engine, catalog, cables, PHASES, [0], workspace=workspace,
                demand_correction=corrections, **models
            ),
            lambda engine, workspace, targets: run_demand_correction(
                engine, catalog, cables, PHASES, 0, targets,
                frozenset({DemandScope.CARGA}), workspace=workspace, **models
            ),
            "C1",
        )

    def test_energy_mode_with_only_low_voltage_consumers(self) -> None:
        composed, energy = energy_network()
        circuit_id = composed.catalog.definition(0).circuit_id
        models = dict(loads=composed.loads, patterns=composed.patterns, energy=energy)
        arguments = (composed.catalog, composed.cables, ENERGY_PHASES)

        self._check(
            lambda engine, workspace: run_power_flow(
                engine, *arguments, [0], workspace=workspace, **models
            ),
            lambda engine, workspace, corrections: run_power_flow(
                engine, *arguments, [0], workspace=workspace,
                demand_correction=corrections, **models
            ),
            lambda engine, workspace, targets: run_demand_correction(
                engine, *arguments, 0, targets,
                frozenset({DemandScope.UC_BT}), workspace=workspace, **models
            ),
            circuit_id,
        )


if __name__ == "__main__":
    unittest.main()
