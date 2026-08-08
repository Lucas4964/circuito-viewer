from __future__ import annotations

import unittest

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
    UtmCrs,
)
from circuit_viewer.opendss_export import (
    SINGLE_PHASE_GENERATORS_FILENAME,
    SINGLE_PHASE_LOADS_FILENAME,
    THREE_PHASE_GENERATORS_FILENAME,
    THREE_PHASE_LOADS_FILENAME,
    TWO_PHASE_GENERATORS_FILENAME,
    TWO_PHASE_LOADS_FILENAME,
    build_export,
    build_generator_export,
)
from circuit_viewer.opendss_settings import OpenDssLoadSettings
from circuit_viewer.phase_config import (
    PhaseConfiguration,
    PhaseMappingEntry,
    load_phase_configuration,
)


def make_cables() -> CableModel:
    return CableModel(
        ["CB1"],
        ["1"],
        ["4/0"],
        ["340"],
        ["0.00824"],
        ["0.367"],
        ["0.42"],
        ["1.2"],
        ["0.551"],
        ["1.232"],
        ["0.367"],
        ["0.42"],
        ["ALUMINIO"],
        ["EXT"],
    )


def make_catalog(*, voltage: str = "13.8") -> CircuitCatalogModel:
    bars = CircuitModel(
        ["B0", "B1", "B2", "B3"],
        ["BARRA-0", "BARRA-1", "BARRA-2", "BARRA-3"],
        [500_000.0, 500_010.0, 500_100.0, 500_110.0],
        [8_000_000.0] * 4,
        UtmCrs(21, False),
    )
    lines = LineNetworkModel(
        bars,
        ["T0", "T1"],
        ["TR-0", "TR-1"],
        ["13", "13"],
        [0, 2],
        [1, 3],
        ["", ""],
        ["CB1", "CB1"],
        ["", ""],
        [10.0, 10.0],
    )
    return CircuitCatalogModel.build(
        lines,
        None,
        (
            CircuitDefinition("C0", "B0", "ALIM-0", voltage),
            CircuitDefinition("C1", "B2", "ALIM-1", voltage),
        ),
    )


def make_updates(
    catalog: CircuitCatalogModel,
    *,
    phases: tuple[str, ...] = ("2", "7", "14"),
    codes: tuple[str, ...] = ("GEN-MONO", "GEN-BI", "GEN-TRI"),
    bar_indices: tuple[int, ...] | None = None,
    configuration: PhaseConfiguration | None = None,
):
    size = len(phases)
    if bar_indices is None:
        bar_indices = (1,) * size
    loads = LoadModel(
        catalog.segments.bars,
        [f"L{index}" for index in range(size)],
        bar_indices,
        [""] * size,
        [f"LOAD-{index}" for index in range(size)],
        [""] * size,
        [""] * size,
        [""] * size,
        phases,
        [""] * size,
    )
    generators = GeneratorModel(
        loads,
        [f"G{index}" for index in range(size)],
        list(range(size)),
        [""] * size,
        codes,
        [""] * size,
        [""] * size,
        [""] * size,
        ["CURVA-IMPORTADA"] * size,
        ["720"] * size,
        [f"C{index}" for index in range(size)],
        codes,
        [""] * size,
        [f"Gerador {index}" for index in range(size)],
        phases,
    )
    phase_configuration = configuration or load_phase_configuration()
    selected_curve = Curve("CURVA", "Constante", (2.0,) * 24)
    schedule = default_calculation_levels()
    result = calculate_generator_demands(
        generators,
        catalog,
        phase_configuration,
        selected_curve,
        tuple(schedule for _ in range(len(catalog))),
        tuple(GeneratorScheduleMode.DEFAULT for _ in range(len(catalog))),
    )
    return result.model


def make_consumption_loads(catalog: CircuitCatalogModel, *, code: str = "CARGA"):
    loads = LoadModel(
        catalog.segments.bars,
        ["LC0"],
        [1],
        [""],
        [code],
        [""],
        [""],
        [""],
        ["1"],
        [""],
    )
    records = tuple(
        LoadPatternRecord("LC0", npat, "1", "0", "0", "0", "0", "0")
        for npat in range(4)
    )
    return loads, LoadPatternModel(loads, (records,))


def data_lines(text: str, prefix: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(prefix)]


class GeneratorOpenDssExportTests(unittest.TestCase):
    def test_mono_bi_and_three_phase_files_use_negative_profiles_and_classes(self) -> None:
        catalog = make_catalog()
        updates = make_updates(catalog)

        mono = build_generator_export(catalog, updates, (0,), phase_count=1)
        two = build_generator_export(catalog, updates, (0,), phase_count=2)
        three = build_generator_export(catalog, updates, (0,), phase_count=3)

        self.assertEqual(mono.exported_count, 1)
        self.assertEqual(two.exported_count, 1)
        self.assertEqual(three.exported_count, 1)
        mono_shape = data_lines(mono.text, "New LoadShape.")[0]
        self.assertIn("PERFIL-GER-GEN-MONO-1F-E", mono_shape)
        self.assertIn("mult=[-2.000000 -2.000000 -2.000000 -2.000000]", mono_shape)
        self.assertIn("qmult=[0.000000 0.000000 0.000000 0.000000]", mono_shape)
        mono_load = data_lines(mono.text, "New Load.")[0]
        self.assertIn("bus1=BARRA-1.2", mono_load)
        self.assertIn("conn=wye", mono_load)
        self.assertIn("kV=7.96743", mono_load)
        self.assertIn("class=-1", mono_load)

        two_shapes = data_lines(two.text, "New LoadShape.")
        self.assertEqual(len(two_shapes), 2)
        self.assertTrue(all("mult=[-1.000000" in line for line in two_shapes))
        self.assertTrue(all("class=-2" in line for line in data_lines(two.text, "New Load.")))

        three_shapes = data_lines(three.text, "New LoadShape.")
        self.assertEqual(len(three_shapes), 3)
        self.assertTrue(
            all("mult=[-0.666667" in line for line in three_shapes)
        )
        self.assertTrue(
            all("class=-3" in line for line in data_lines(three.text, "New Load."))
        )

    def test_selected_circuit_filters_generators(self) -> None:
        catalog = make_catalog()
        updates = make_updates(
            catalog,
            phases=("1", "1"),
            codes=("PRIMEIRO", "SEGUNDO"),
            bar_indices=(1, 3),
        )

        first = build_generator_export(catalog, updates, (0,), phase_count=1)
        second = build_generator_export(catalog, updates, (1,), phase_count=1)

        self.assertIn("GER-PRIMEIRO", first.text)
        self.assertNotIn("GER-SEGUNDO", first.text)
        self.assertIn("GER-SEGUNDO", second.text)
        self.assertNotIn("GER-PRIMEIRO", second.text)

    def test_generator_code_falls_back_to_id_and_name_collision_discards_whole_unit(self) -> None:
        catalog = make_catalog()
        fallback = make_updates(catalog, phases=("1",), codes=("!!!",))
        fallback_result = build_generator_export(
            catalog, fallback, (0,), phase_count=1
        )
        self.assertIn("New Load.GER-G0-1F-D", fallback_result.text)
        self.assertTrue(any("GERADOR_ID" in issue.reason for issue in fallback_result.issues))

        updates = make_updates(catalog, phases=("1",), codes=("COLISAO",))
        collision = build_generator_export(
            catalog,
            updates,
            (0,),
            phase_count=1,
            reserved_names=frozenset({"GER-COLISAO-1F-D"}),
        )
        self.assertEqual((collision.exported_count, collision.discarded_count), (0, 1))
        self.assertFalse(data_lines(collision.text, "New Load."))

    def test_missing_phase_terminal_or_circuit_voltage_discards_generator(self) -> None:
        no_terminals = PhaseConfiguration(
            (PhaseMappingEntry("7", "DE", 2, "1.2"),)
        )
        catalog = make_catalog()
        updates = make_updates(
            catalog,
            phases=("7",),
            codes=("SEM-NO",),
            configuration=no_terminals,
        )
        result = build_generator_export(catalog, updates, (0,), phase_count=2)
        self.assertEqual((result.exported_count, result.discarded_count), (0, 1))
        self.assertIn("terminal DSS", result.issues[0].reason)

        invalid_catalog = make_catalog(voltage="")
        invalid_voltage = make_updates(
            invalid_catalog, phases=("1",), codes=("SEM-VNOM",)
        )
        result = build_generator_export(
            invalid_catalog, invalid_voltage, (0,), phase_count=1
        )
        self.assertEqual((result.exported_count, result.discarded_count), (0, 1))
        self.assertIn("VNOM", result.issues[0].reason)

    def test_bundle_contains_six_phase_files_and_master_redirects_generators_after_loads(self) -> None:
        catalog = make_catalog()
        updates = make_updates(catalog, phases=("1",), codes=("GERADOR",))
        loads, patterns = make_consumption_loads(catalog)

        bundle = build_export(
            catalog,
            make_cables(),
            updates.phase_configuration,
            (0,),
            loads=loads,
            patterns=patterns,
            generator_updates=updates,
        )

        names = [name for name, _ in bundle.element_files]
        expected_loads = [
            SINGLE_PHASE_LOADS_FILENAME,
            TWO_PHASE_LOADS_FILENAME,
            THREE_PHASE_LOADS_FILENAME,
        ]
        expected_generators = [
            SINGLE_PHASE_GENERATORS_FILENAME,
            TWO_PHASE_GENERATORS_FILENAME,
            THREE_PHASE_GENERATORS_FILENAME,
        ]
        self.assertEqual(names[-6:], expected_loads + expected_generators)
        redirects = [
            line for line in bundle.master.text.splitlines() if line.startswith("Redirect ")
        ]
        self.assertEqual(
            redirects[-6:],
            [f"Redirect {name}" for name in expected_loads + expected_generators],
        )

    def test_load_namespace_has_priority_over_generator_namespace(self) -> None:
        catalog = make_catalog()
        updates = make_updates(catalog, phases=("1",), codes=("COLISAO",))
        loads, patterns = make_consumption_loads(
            catalog, code="GER-COLISAO"
        )

        bundle = build_export(
            catalog,
            make_cables(),
            updates.phase_configuration,
            (0,),
            loads=loads,
            patterns=patterns,
            generator_updates=updates,
        )

        self.assertEqual(bundle.single_phase_loads.exported_count, 1)
        self.assertEqual(bundle.single_phase_generators.exported_count, 0)
        self.assertEqual(bundle.single_phase_generators.discarded_count, 1)

    def test_generator_only_export_receives_global_load_settings(self) -> None:
        catalog = make_catalog()
        updates = make_updates(catalog, phases=("1",), codes=("GERADOR",))

        bundle = build_export(
            catalog,
            make_cables(),
            updates.phase_configuration,
            (0,),
            generator_updates=updates,
            load_settings=OpenDssLoadSettings(
                voltage_limits_enabled=True,
                vminpu=0.8,
                vmaxpu=1.2,
            ),
        )

        self.assertIn("BatchEdit Load..* vminpu=0.8", bundle.master.text)
        self.assertIn("BatchEdit Load..* vmaxpu=1.2", bundle.master.text)

    def test_identity_mismatch_and_cancel_are_rejected(self) -> None:
        catalog = make_catalog()
        updates = make_updates(catalog, phases=("1",), codes=("GERADOR",))
        with self.assertRaisesRegex(ValueError, "outros circuitos"):
            build_generator_export(
                make_catalog(), updates, (0,), phase_count=1
            )
        with self.assertRaises(InterruptedError):
            build_generator_export(
                catalog,
                updates,
                (0,),
                phase_count=1,
                cancel_check=lambda: True,
            )


if __name__ == "__main__":
    unittest.main()
