from __future__ import annotations

import re
import unittest

from circuit_viewer.model import (
    CableModel,
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.opendss_export import (
    ARRANGEMENTS_FILENAME,
    CABOS_FILENAME,
    LINE_GEOMETRIES_FILENAME,
    LINES_FILENAME,
    SWITCHES_FILENAME,
    OpenDssLibraryExportError,
    build_export,
    build_library_line_export,
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
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("1", "D", 1, "1"),
        PhaseMappingEntry("2", "E", 1, "2"),
        PhaseMappingEntry("3", "F", 1, "3"),
        PhaseMappingEntry("7", "DE", 2, "1.2"),
        PhaseMappingEntry("8", "EF", 2, "2.3"),
        PhaseMappingEntry("9", "FD", 2, "1.3"),
        PhaseMappingEntry("13", "DEF", 3, "1.2.3"),
    )
)


def make_catalog(
    phases: tuple[str, ...],
    *,
    codes: tuple[str, ...] | None = None,
    arrangement_ids: tuple[str, ...] | None = None,
    phase_cable_ids: tuple[str, ...] | None = None,
    neutral_cable_ids: tuple[str, ...] | None = None,
) -> CircuitCatalogModel:
    count = len(phases)
    bars = CircuitModel(
        [f"B{index}" for index in range(count + 1)],
        [f"BARRA_{index}" for index in range(count + 1)],
        [500_000.0 + 10.0 * index for index in range(count + 1)],
        [8_000_000.0] * (count + 1),
        UtmCrs(21, northern=False),
    )
    network = LineNetworkModel(
        bars,
        [f"T{index}" for index in range(count)],
        codes or tuple(f"LINHA-{index + 1}" for index in range(count)),
        phases,
        range(count),
        range(1, count + 1),
        arrangement_ids or ("AR",) * count,
        phase_cable_ids or ("CABO X",) * count,
        neutral_cable_ids or ("-1",) * count,
        [100.0 + index for index in range(count)],
    )
    return CircuitCatalogModel.build(
        network,
        None,
        [CircuitDefinition("C1", "B0", "ALIMENTADOR", "13,8")],
    )


def make_wire(
    cable_id: str,
    name: str,
    *,
    cable_type: str = "wire",
) -> CableDefinition:
    return CableDefinition(
        cable_id=cable_id,
        name=name,
        cable_type=cable_type,
        rac=0.12345678901234566,
        rdc=0.1111111111111111,
        resistance_units="km",
        gmr=0.7654321098765432,
        gmr_units="cm",
        radius=0.4,
        diameter=0.8,
        radius_units="cm",
        normal_amps=321.1234567890123,
        emergency_amps=456.9876543210987,
        strand_count=10 if cable_type == "cn" else None,
        strand_diameter=0.1 if cable_type == "cn" else None,
        strand_resistance=1.0 if cable_type == "cn" else None,
        strand_gmr=0.05 if cable_type == "cn" else None,
        insulation_layer=0.2 if cable_type == "cn" else None,
        insulation_diameter=1.0 if cable_type == "cn" else None,
        cable_diameter=2.0 if cable_type == "cn" else None,
    )


def make_arrangement(
    arrangement_id: str = "arr-base",
    name: str = "INTERLAN PADRÃO",
    *,
    units: str = "m",
    coincident_neutral: bool = False,
) -> ArrangementDefinition:
    return ArrangementDefinition(
        arrangement_id=arrangement_id,
        name=name,
        phase_count=3,
        units=units,
        positions=[
            ConductorPosition(0.22, 10.0),
            ConductorPosition(0.92, 10.0),
            ConductorPosition(1.62, 10.0),
            ConductorPosition(0.22 if coincident_neutral else 0.60, 10.0),
        ],
    )


def make_library(
    *,
    phase_cable: CableDefinition | None = None,
    neutral_cable: CableDefinition | None = None,
    arrangement: ArrangementDefinition | None = None,
    map_neutral: bool = True,
) -> tuple[OpenDssLibraryCatalog, OpenDssLibraryMappings]:
    phase = phase_cable or make_wire("phase", "FASE ESPECIAL")
    neutral = neutral_cable or make_wire("neutral", "NEUTRO ESPECIAL")
    base = arrangement or make_arrangement()
    mappings = OpenDssLibraryMappings(
        cables=(
            LibraryNameMapping("CABO X", phase.name),
            *((LibraryNameMapping("CABO N", neutral.name),) if map_neutral else ()),
        ),
        arrangements=(LibraryNameMapping("AR", base.name),),
    )
    return OpenDssLibraryCatalog([phase, neutral], [base]), mappings


def make_original_cables() -> CableModel:
    return CableModel(
        ["CABO X"],
        ["1"],
        ["4/0"],
        ["340"],
        ["0,00824"],
        ["0,367"],
        ["0,42"],
        ["1,2"],
        ["0,551"],
        ["1,232"],
        ["0,367"],
        ["0,42"],
        ["ALUMINIO 4/0"],
        ["EXT-1"],
    )


class LibraryExportTests(unittest.TestCase):
    def test_six_configurations_deduplicate_phase_letters_and_order_files(self) -> None:
        # DE, EF e FD compartilham o mesmo LineSpacing/LineGeometry 2F.
        catalog = make_catalog(
            ("1", "1", "7", "7", "13", "13", "8", "9"),
            neutral_cable_ids=(
                "-1",
                "CABO N",
                "-1",
                "CABO N",
                "-1",
                "CABO N",
                "-1",
                "-1",
            ),
        )
        library, mappings = make_library()

        # Aceita também o valor persistido como texto, com caixa/espaços variados.
        bundle = build_export(
            catalog,
            None,
            PHASES,
            [0],
            line_parameter_mode=" LIBRARY ",
            library_catalog=library,
            library_mappings=mappings,
        )

        self.assertIsNotNone(bundle.library)
        assert bundle.library is not None
        self.assertEqual(bundle.library.cable_count, 2)
        self.assertEqual(bundle.library.arrangement_count, 6)
        self.assertEqual(bundle.library.line_geometry_count, 6)
        self.assertEqual(bundle.lines.exported_count, 8)

        spacing_names = set(
            re.findall(r"New LineSpacing\.([^\s]+)", bundle.library.arrangements_text)
        )
        self.assertEqual(
            spacing_names,
            {
                "INTERLAN_PADRAO-1F",
                "INTERLAN_PADRAO-1FN",
                "INTERLAN_PADRAO-2F",
                "INTERLAN_PADRAO-2FN",
                "INTERLAN_PADRAO-3F",
                "INTERLAN_PADRAO-3FN",
            },
        )
        self.assertIn(
            "New LineSpacing.INTERLAN_PADRAO-1F nconds=1 nphases=1 units=m\n"
            "~ x=[0.22]\n~ h=[10]",
            bundle.library.arrangements_text,
        )
        self.assertIn(
            "New LineSpacing.INTERLAN_PADRAO-2FN nconds=3 nphases=2 units=m",
            bundle.library.arrangements_text,
        )

        geometries = bundle.library.line_geometries_text
        self.assertIn("LineGeometry.INTERLAN_PADRAO-2F-CABO_X", geometries)
        self.assertIn(
            "LineGeometry.INTERLAN_PADRAO-3FN-CABO_X-N-CABO_N", geometries
        )
        self.assertEqual(geometries.count("reduce=yes"), 6)
        self.assertNotIn("reduce=no", geometries)
        self.assertNotIn("reduce=n ", geometries)
        self.assertEqual(geometries.count("LineGeometry.INTERLAN_PADRAO-2F-CABO_X"), 1)
        self.assertEqual(bundle.lines.text.count("geometry=INTERLAN_PADRAO-2F-CABO_X"), 3)
        self.assertNotIn(" R1=", bundle.lines.text)
        self.assertNotIn(" X1=", bundle.lines.text)
        self.assertNotIn(" C1=", bundle.lines.text)

        cables_text = bundle.library.cables_text
        self.assertIn("New WireData.FASE_ESPECIAL", cables_text)
        self.assertIn("New WireData.NEUTRO_ESPECIAL", cables_text)
        self.assertIn("Rac=0.12345678901234566", cables_text)
        self.assertIn("Radius=0.4", cables_text)
        self.assertNotIn("Diam=0.8", cables_text)

        expected_prefix = [
            CABOS_FILENAME,
            ARRANGEMENTS_FILENAME,
            LINE_GEOMETRIES_FILENAME,
            LINES_FILENAME,
            SWITCHES_FILENAME,
        ]
        self.assertEqual(
            [name for name, _ in bundle.element_files[:5]], expected_prefix
        )
        assert bundle.master is not None
        redirects = [
            line.removeprefix("Redirect ")
            for line in bundle.master.text.splitlines()
            if line.startswith("Redirect ")
        ]
        self.assertEqual(redirects[:5], expected_prefix)

    def test_minus_one_neutral_needs_no_mapping_and_empty_files_keep_headers(self) -> None:
        catalog = make_catalog(("1",), neutral_cable_ids=("-1",))
        library, mappings = make_library(map_neutral=False)

        lines, physical = build_library_line_export(
            catalog, PHASES, [0], library, mappings
        )

        self.assertEqual(lines.exported_count, 1)
        self.assertIn("geometry=INTERLAN_PADRAO-1F-CABO_X", lines.text)
        self.assertNotIn("-N-", physical.line_geometries_text)
        self.assertEqual(physical.cable_count, 1)

        empty_lines, empty = build_library_line_export(
            catalog,
            PHASES,
            [0],
            library,
            mappings,
            skip_segments=frozenset({0}),
        )
        self.assertEqual(empty_lines.exported_count, 0)
        self.assertEqual(empty.cable_count, 0)
        self.assertEqual(empty.arrangement_count, 0)
        self.assertEqual(empty.line_geometry_count, 0)
        self.assertEqual(empty.cables_text, "! Cabos WireData usados pelo circuito\n")
        self.assertEqual(
            empty.arrangements_text, "! Arranjos LineSpacing usados pelo circuito\n"
        )
        self.assertEqual(
            empty.line_geometries_text,
            "! Geometrias LineGeometry usadas pelo circuito\n",
        )

    def test_names_colliding_after_sanitization_get_stable_suffixes(self) -> None:
        catalog = make_catalog(
            ("1", "1"),
            arrangement_ids=("A1", "A2"),
            phase_cable_ids=("F 1", "F.1"),
        )
        first_cable = make_wire("f1", "CABO A")
        second_cable = make_wire("f2", "CABO.A")
        first_arrangement = make_arrangement("a1", "ARR A")
        second_arrangement = make_arrangement("a2", "ARR.A")
        library = OpenDssLibraryCatalog(
            [first_cable, second_cable],
            [first_arrangement, second_arrangement],
        )
        mappings = OpenDssLibraryMappings(
            cables=(
                LibraryNameMapping("F 1", first_cable.name),
                LibraryNameMapping("F.1", second_cable.name),
            ),
            arrangements=(
                LibraryNameMapping("A1", first_arrangement.name),
                LibraryNameMapping("A2", second_arrangement.name),
            ),
        )

        _, physical = build_library_line_export(
            catalog, PHASES, [0], library, mappings
        )

        self.assertIn("New WireData.CABO_A ", physical.cables_text)
        self.assertIn("New WireData.CABO_A_2 ", physical.cables_text)
        self.assertIn("New LineSpacing.ARR_A-1F ", physical.arrangements_text)
        self.assertIn("New LineSpacing.ARR_A-1F_2 ", physical.arrangements_text)
        self.assertIn("New LineGeometry.ARR_A-1F-F_1 ", physical.line_geometries_text)
        self.assertIn(
            "New LineGeometry.ARR_A-1F-F_1_2 ", physical.line_geometries_text
        )

    def test_line_names_colliding_after_sanitization_are_not_discarded(self) -> None:
        catalog = make_catalog(
            ("1", "1", "1"),
            codes=("A B", "A.B", "a_b"),
        )
        library, mappings = make_library()

        lines, _ = build_library_line_export(
            catalog, PHASES, [0], library, mappings
        )

        self.assertEqual(lines.exported_count, 3)
        self.assertEqual(lines.discarded_count, 0)
        self.assertIn("New Line.A_B ", lines.text)
        self.assertIn("New Line.A_B_2 ", lines.text)
        self.assertIn("New Line.a_b_3 ", lines.text)
        self.assertEqual(
            tuple(name for name, _ in lines.exported_segments),
            ("A_B", "A_B_2", "a_b_3"),
        )

    def test_switch_name_collision_is_renamed_and_open_reference_follows(self) -> None:
        original = make_catalog(
            ("1", "1"),
            codes=("A B", "TRECHO-CHAVE"),
        )
        network = original.segments
        switches = SwitchModel(
            network,
            ["CH1"],
            ["TC"],
            ["C1"],
            [1],
            ["a.b"],
            ["0"],
            ["1"],
            [""],
            [""],
            [""],
        )
        catalog = CircuitCatalogModel.build(
            network,
            switches,
            [original.definition(0)],
        )
        library, mappings = make_library()

        bundle = build_export(
            catalog,
            None,
            PHASES,
            [0],
            line_parameter_mode=OpenDssLineParameterMode.LIBRARY,
            library_catalog=library,
            library_mappings=mappings,
        )

        self.assertIn("New Line.A_B ", bundle.lines.text)
        self.assertIn("New Line.a_b_2 ", bundle.switches.text)
        self.assertIn("Open Line.a_b_2 1", bundle.switches.text)
        self.assertEqual(bundle.switches.exported_count, 1)
        self.assertEqual(bundle.switches.discarded_count, 0)

    def test_missing_inputs_cn_and_neutral_mapping_are_strict_errors(self) -> None:
        no_neutral_catalog = make_catalog(("1",))
        with self.assertRaises(OpenDssLibraryExportError) as missing:
            build_export(
                no_neutral_catalog,
                None,
                PHASES,
                [0],
                line_parameter_mode=OpenDssLineParameterMode.LIBRARY,
            )
        self.assertEqual(len(missing.exception.errors), 2)

        cn_library, cn_mappings = make_library(
            phase_cable=make_wire("phase-cn", "FASE CN", cable_type="cn")
        )
        with self.assertRaises(OpenDssLibraryExportError) as cn:
            build_library_line_export(
                no_neutral_catalog, PHASES, [0], cn_library, cn_mappings
            )
        self.assertTrue(any("somente WireData" in item for item in cn.exception.errors))

        neutral_catalog = make_catalog(("1",), neutral_cable_ids=("CABO N",))
        library, mappings = make_library(map_neutral=False)
        with self.assertRaises(OpenDssLibraryExportError) as neutral:
            build_library_line_export(
                neutral_catalog, PHASES, [0], library, mappings
            )
        self.assertTrue(any("CABON_ID" in item for item in neutral.exception.errors))
        self.assertTrue(
            any("sem vinculo" in item for item in neutral.exception.errors)
        )

    def test_invalid_cable_unit_and_coincident_positions_are_grouped(self) -> None:
        catalog = make_catalog(("1",), neutral_cable_ids=("CABO N",))
        invalid_phase = CableDefinition(
            cable_id="invalid",
            name="CABO INCOMPLETO",
            resistance_units="km",
            gmr_units="cm",
            radius_units="cm",
        )
        bad_arrangement = make_arrangement(
            units="yd", coincident_neutral=True
        )
        library, mappings = make_library(
            phase_cable=invalid_phase,
            arrangement=bad_arrangement,
        )

        with self.assertRaises(OpenDssLibraryExportError) as raised:
            build_library_line_export(catalog, PHASES, [0], library, mappings)

        errors = raised.exception.errors
        self.assertGreaterEqual(len(errors), 3)
        self.assertTrue(any("unidade inválida" in item for item in errors))
        self.assertTrue(any("posições coincidentes" in item for item in errors))
        self.assertTrue(any("incompleto" in item for item in errors))

    def test_original_default_and_explicit_mode_are_byte_identical(self) -> None:
        catalog = make_catalog(("13", "13"))
        cables = make_original_cables()

        default = build_export(catalog, cables, PHASES, [0])
        explicit = build_export(
            catalog,
            cables,
            PHASES,
            [0],
            line_parameter_mode=OpenDssLineParameterMode.ORIGINAL,
        )
        textual = build_export(
            catalog,
            cables,
            PHASES,
            [0],
            line_parameter_mode="original",
        )

        self.assertEqual(default.files, explicit.files)
        self.assertEqual(default.files, textual.files)
        self.assertIsNone(default.library)
        self.assertEqual(
            [name for name, _ in default.element_files[:2]],
            [LINES_FILENAME, SWITCHES_FILENAME],
        )


if __name__ == "__main__":
    unittest.main()
