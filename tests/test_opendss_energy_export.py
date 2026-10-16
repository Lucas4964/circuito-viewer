"""Exportação do modo de cargas Energia: trafo, barra secundária e UCs."""

from __future__ import annotations

import math
import unittest
from unittest import mock

from circuit_viewer.calculation_levels import default_calculation_levels
from circuit_viewer.curvas import Curve
from circuit_viewer.energy_demand import calculate_energy_demands
from circuit_viewer.generator_update import (
    GeneratorScheduleMode,
    calculate_generator_demands,
)
from circuit_viewer.opendss_export import (
    ENERGY_TRANSFORMERS_FILENAME,
    THREE_PHASE_GENERATORS_FILENAME,
    THREE_PHASE_LOADS_FILENAME,
    _energy_secondary_nodes,
    build_export,
)
from circuit_viewer.phase_config import load_phase_configuration
from circuit_viewer.source_composition import compose
from tests.test_energy_demand import CATEGORIES, CURVES
from tests.test_source_composition import dataset
from tests.test_transformer_consumers import extended_tables


# Um objeto só: o build_export exige que geradores e exportação usem a mesma
# configuração de fases, por identidade.
PHASES = load_phase_configuration()

MT_GENERATOR_WITH_TYPE = (
    ["GERADOR_ID", "MT_CONS_ID", "CODIGO", "VNOM", "SNOM", "LIGACAO", "CURVA_ID",
     "GERACAO_KWH", "TIPOGD"],
    [(201, 101, "GEN-COD", 13.8, 75.0, "Y", 0, 1000.5, 0)],
)


def energy_network(**overrides):  # noqa: ANN003, ANN201
    """A rede falsa com uma CARGA de ET e EP, como a 17495 da base real."""

    tables = extended_tables()
    tables["MT_GERADOR_CONS"] = MT_GENERATOR_WITH_TYPE
    tables.update(overrides)
    composed = compose((dataset("F1", **tables),))
    schedule = default_calculation_levels()
    energy = calculate_energy_demands(
        composed.consumers, CURVES, CATEGORIES, (schedule,) * len(composed.loads)
    )
    return composed, energy


def export(composed, energy, **kwargs):  # noqa: ANN001, ANN003, ANN201
    return build_export(
        composed.catalog,
        composed.cables,
        PHASES,
        (0,),
        loads=composed.loads,
        patterns=composed.patterns,
        energy=energy,
        **kwargs,
    )


def files(bundle) -> dict[str, str]:  # noqa: ANN001
    return dict(bundle.files)


class ThreePhaseTransformerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.composed, self.energy = energy_network()
        self.bundle = export(self.composed, self.energy)
        self.text = files(self.bundle)[ENERGY_TRANSFORMERS_FILENAME]

    def lines(self, prefix: str) -> list[str]:
        return [line for line in self.text.splitlines() if line.startswith(prefix)]

    def test_the_carga_becomes_an_ideal_delta_wye_transformer(self) -> None:
        (transformer,) = self.lines("New Transformer.")
        self.assertIn("New Transformer.TRF-CARGA-1 phases=3 windings=2", transformer)
        self.assertIn("buses=[COD-C.1.2.3 BT_CARGA-1.1.2.3]", transformer)
        self.assertIn("conns=[delta wye] kVs=[13.8 0.22]", transformer)
        self.assertIn("kVAs=[30 30]", transformer)
        self.assertIn("%noloadloss=0 %imag=0 ppm_antifloat=0", transformer)

    def test_each_consumer_is_one_load_on_the_secondary(self) -> None:
        loads = {line.split()[1]: line for line in self.lines("New Load.")}
        self.assertIn("phases=1 bus1=BT_CARGA-1.1.0 conn=wye kV=0.127017", loads["Load.UC-BT-1001"])
        self.assertIn("phases=3 bus1=BT_CARGA-1.1.2.3 conn=wye kV=0.22", loads["Load.UC-MT-101"])
        self.assertIn("bus1=BT_CARGA-1.1.0", loads["Load.IP-168662"])
        # A UC 1002 é de uma classe sem curva: fica sem potência.
        self.assertNotIn("Load.UC-BT-1002", loads)
        self.assertIn("class=-3", loads["Load.GD-MT-201"])
        self.assertIn("model=1", loads["Load.GD-BT-71"])

    def test_generation_is_negative_and_never_minus_zero(self) -> None:
        (shape,) = self.lines("New LoadShape.PERFIL-GD-MT-201 ")
        values = shape.split("mult=[")[1].split("]")[0].split()
        self.assertTrue(all(not value.startswith("-0.000000") for value in values))
        self.assertTrue(any(value.startswith("-") for value in values))

    def test_the_master_gives_the_secondary_its_own_voltage_base(self) -> None:
        master = self.bundle.master.text.splitlines()
        position = master.index("calcvoltagebases")
        self.assertEqual(master[position + 1], f"SetkVBase bus=BT_CARGA-1 kVLN={0.22 / math.sqrt(3):.6g}")
        self.assertIn(f"Redirect {ENERGY_TRANSFORMERS_FILENAME}", master)

    def test_the_header_explains_the_fictitious_bus(self) -> None:
        self.assertIn("barra ficticia", self.text)


class NoDoubleCountingTests(unittest.TestCase):
    """O plano exige: cada potência entra uma vez só."""

    def setUp(self) -> None:
        self.composed, self.energy = energy_network()
        circuits = self.composed.catalog
        updates = calculate_generator_demands(
            self.composed.generators,
            circuits,
            PHASES,
            Curve("solar", "Solar", tuple(1.0 for _ in range(24))),
            (default_calculation_levels(),) * len(circuits),
            (GeneratorScheduleMode.DEFAULT,) * len(circuits),
        ).model
        self.bundle = export(self.composed, self.energy, generator_updates=updates)
        self.files = files(self.bundle)

    def test_the_aggregated_carga_load_is_not_exported(self) -> None:
        loads = self.files[THREE_PHASE_LOADS_FILENAME]
        self.assertNotIn("New Load.CARGA-1", loads)
        self.assertEqual(self.bundle.three_phase_loads.exported_count, 0)

    def test_the_mt_generator_leaves_the_generator_files(self) -> None:
        generators = self.files[THREE_PHASE_GENERATORS_FILENAME]
        self.assertNotIn("New Load.GER-", generators)
        self.assertEqual(
            sum(1 for line in self.files[ENERGY_TRANSFORMERS_FILENAME].splitlines()
                if line.startswith("New Load.GD-MT-201")),
            1,
        )

    def test_exactly_one_transformer_and_the_levels_add_up(self) -> None:
        text = self.files[ENERGY_TRANSFORMERS_FILENAME]
        self.assertEqual(text.count("New Transformer."), 1)
        transformer = self.energy.transformer(0)
        expected = [0.0] * 4
        for item in transformer.consumers:
            for step, value in enumerate(item.load_levels):
                expected[step] += value
        exported = [0.0] * 4
        for line in text.splitlines():
            if line.startswith("New LoadShape.PERFIL-UC-") or line.startswith("New LoadShape.PERFIL-IP-"):
                values = line.split(" mult=[")[1].split("]")[0].split()
                for step, value in enumerate(values):
                    exported[step] += float(value)
        for got, want in zip(exported, expected):
            self.assertAlmostEqual(got, want, places=4)


class SplitPhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        tables = extended_tables()
        tables["BT_UTC"] = (
            ["ID", "CODIGO", "ET_ID", "SNOM", "SADM", "VPRIMARIO", "VSECUNDA",
             "FASES_MT2", "FASES_BT2"],
            [(1, "UTC", 17, 25.0, 25.0, 7.967430114746094, 254.0, 1, 10)],
        )
        from tests.test_transformer_consumers import CARGA_COLUMNS

        tables["CARGA"] = (CARGA_COLUMNS, [(2, 9, 34722450, "CARGA-1", 25.0, 25.0, 254.0, 1, 0, None)])
        self.composed, self.energy = energy_network(**tables)
        self.bundle = export(self.composed, self.energy)
        self.text = files(self.bundle)[ENERGY_TRANSFORMERS_FILENAME]

    def test_a_single_phase_carga_is_a_split_phase_transformer(self) -> None:
        (transformer,) = [line for line in self.text.splitlines() if line.startswith("New Transformer.")]
        self.assertIn("phases=1 windings=3", transformer)
        self.assertIn("buses=[COD-C.1.0 BT_CARGA-1.1.0 BT_CARGA-1.0.2]", transformer)
        self.assertIn("kVs=[7.96743 0.127 0.127]", transformer)

    def test_consumers_go_to_their_leg_or_between_the_legs(self) -> None:
        loads = {line.split()[1]: line for line in self.text.splitlines() if line.startswith("New Load.")}
        self.assertIn("bus1=BT_CARGA-1.1.0 conn=wye kV=0.127", loads["Load.UC-BT-1001"])
        self.assertIn("bus1=BT_CARGA-1.1.2 conn=delta kV=0.254", loads["Load.UC-MT-101"])
        self.assertTrue(any("entre as pernas" in issue.reason for issue in self.bundle.energy.issues))

    def test_the_voltage_base_is_half_the_secondary(self) -> None:
        self.assertIn("SetkVBase bus=BT_CARGA-1 kVLN=0.127", self.bundle.master.text)


class FallbackTests(unittest.TestCase):
    def test_a_transformer_without_secondary_voltage_keeps_the_interplan_load(self) -> None:
        tables = extended_tables()
        tables["BT_UTC"] = (
            ["ID", "CODIGO", "ET_ID", "SNOM", "SADM", "VPRIMARIO", "VSECUNDA",
             "FASES_MT2", "FASES_BT2"],
            [(1, "UTC", 17, 30.0, 30.0, 13.8, None, 13, 14)],
        )
        from tests.test_transformer_consumers import CARGA_COLUMNS

        tables["CARGA"] = (CARGA_COLUMNS, [(2, 9, 34722450, "CARGA-1", 30.0, 30.0, None, 13, 2, None)])
        composed, energy = energy_network(**tables)
        bundle = export(composed, energy)
        self.assertEqual(bundle.energy.exported_count, 0)
        self.assertNotIn(ENERGY_TRANSFORMERS_FILENAME, files(bundle))
        self.assertIn("New Load.CARGA-1", files(bundle)[THREE_PHASE_LOADS_FILENAME])
        self.assertTrue(any("modelo do Interplan" in issue.reason for issue in bundle.energy.issues))

    def test_a_far_primary_rating_falls_back_to_the_circuit_voltage(self) -> None:
        tables = extended_tables()
        tables["BT_UTC"] = (
            ["ID", "CODIGO", "ET_ID", "SNOM", "SADM", "VPRIMARIO", "VSECUNDA",
             "FASES_MT2", "FASES_BT2"],
            [(1, "UTC", 17, 30.0, 30.0, 13800.0, 220.0, 13, 14)],
        )
        composed, energy = energy_network(**tables)
        bundle = export(composed, energy)
        self.assertIn("kVs=[13.8 0.22]", files(bundle)[ENERGY_TRANSFORMERS_FILENAME])
        self.assertTrue(any("VPRIMARIO" in issue.reason for issue in bundle.energy.issues))

    def test_without_energy_nothing_changes(self) -> None:
        composed, _ = energy_network()
        bundle = export(composed, None)
        self.assertIsNone(bundle.energy)
        self.assertNotIn(ENERGY_TRANSFORMERS_FILENAME, files(bundle))
        self.assertNotIn("SetkVBase", bundle.master.text)
        self.assertIn("New Load.CARGA-1", files(bundle)[THREE_PHASE_LOADS_FILENAME])

    def test_energy_from_other_loads_is_refused(self) -> None:
        composed, energy = energy_network()
        other, _ = energy_network()
        with self.assertRaises(ValueError):
            export(other, energy)


class SecondaryNodeTests(unittest.TestCase):
    TERMINALS = {"D": "1", "E": "2", "F": "3"}
    LEGS = {"D": 1, "E": 2}

    def test_three_phase(self) -> None:
        self.assertEqual(_energy_secondary_nodes(("E",), True, {}, self.TERMINALS), ((2,), None))
        self.assertEqual(_energy_secondary_nodes(("D", "F"), True, {}, self.TERMINALS), ((1, 3), None))
        nodes, note = _energy_secondary_nodes(None, True, {}, self.TERMINALS)
        self.assertEqual(nodes, (1, 2, 3))
        self.assertIsNotNone(note)

    def test_split_phase(self) -> None:
        self.assertEqual(_energy_secondary_nodes(("E",), False, self.LEGS, self.TERMINALS), ((2,), None))
        self.assertEqual(_energy_secondary_nodes(("D", "E"), False, self.LEGS, self.TERMINALS), ((1, 2), None))
        nodes, note = _energy_secondary_nodes(("F",), False, self.LEGS, self.TERMINALS)
        self.assertEqual(nodes, (1,))
        self.assertIn("perna 1", note)
        nodes, note = _energy_secondary_nodes(("D", "E", "F"), False, self.LEGS, self.TERMINALS)
        self.assertEqual(nodes, (1, 2))
        self.assertIn("entre as pernas", note)


class PowerFlowPlumbingTests(unittest.TestCase):
    def test_the_power_flow_passes_the_energy_model_to_the_export(self) -> None:
        from circuit_viewer import opendss_powerflow

        composed, energy = energy_network()

        class Stop(Exception):
            pass

        with mock.patch.object(opendss_powerflow, "build_export", side_effect=Stop) as build:
            with self.assertRaises(Stop):
                opendss_powerflow.run_power_flow(
                    object(),
                    composed.catalog,
                    composed.cables,
                    PHASES,
                    (0,),
                    workspace=None,
                    loads=composed.loads,
                    patterns=composed.patterns,
                    energy=energy,
                )
        self.assertIs(build.call_args.kwargs["energy"], energy)


if __name__ == "__main__":
    unittest.main()
