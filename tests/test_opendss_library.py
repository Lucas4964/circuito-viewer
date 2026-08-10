from __future__ import annotations

import unittest

from circuit_viewer.opendss_library import (
    ArrangementDefinition,
    CableDefinition,
    ConductorPosition,
    GeometryDefinition,
    OpenDssLibraryCatalog,
    cable_issues,
    coincident_positions,
    estimate_diameter_from_section,
    estimate_radius_from_gmr,
    geometry_ampacity,
    geometry_issues,
    normalize_library_name,
    phase_cable_types_are_homogeneous,
    unique_id,
    unique_name,
)


def complete_wire(cable_id: str = "wire", name: str = "Wire", amps: float | None = 200.0) -> CableDefinition:
    return CableDefinition(
        cable_id,
        name,
        rac=0.2,
        resistance_units="km",
        gmr=0.5,
        gmr_units="cm",
        radius=0.7,
        radius_units="cm",
        normal_amps=amps,
    )


def complete_cn(cable_id: str = "cn", name: str = "CN", amps: float | None = 180.0) -> CableDefinition:
    cable = complete_wire(cable_id, name, amps)
    cable.cable_type = "cn"
    cable.strand_count = 13
    cable.strand_diameter = 0.064
    cable.strand_resistance = 2.8
    cable.insulation_layer = 0.22
    cable.insulation_diameter = 1.06
    cable.cable_diameter = 1.16
    return cable


class CableValidationTests(unittest.TestCase):
    def test_cable_and_arrangement_names_are_canonical_uppercase(self) -> None:
        cable = CableDefinition("x", "  cabo Misto  ")
        arrangement = ArrangementDefinition("a", "  Cruzeta 3f  ", 3, "m", [])

        self.assertEqual(cable.name, "CABO MISTO")
        self.assertEqual(arrangement.name, "CRUZETA 3F")
        self.assertEqual(normalize_library_name("  cabo Misto  "), "CABO MISTO")

    def test_mounting_names_keep_their_original_spelling(self) -> None:
        geometry = GeometryDefinition("g", "Montagem Mista", "a", [])

        self.assertEqual(geometry.name, "Montagem Mista")

    def test_wire_requires_electrical_and_physical_fields(self) -> None:
        cable = CableDefinition("x", "Incompleto", resistance_units="", gmr_units="", radius_units="")

        self.assertEqual(
            cable_issues(cable),
            (
                "resistência (Rac ou Rdc)",
                "unidade de R",
                "GMR",
                "unidade do GMR",
                "diâmetro ou raio",
                "unidade de diâmetro/raio",
            ),
        )
        self.assertEqual(cable_issues(complete_wire()), ())

    def test_concentric_fields_are_required_but_gmrstrand_and_epsr_are_optional(self) -> None:
        cable = complete_wire()
        cable.cable_type = "cn"
        self.assertIn("nº de fios do neutro (k)", cable_issues(cable))

        cable = complete_cn()
        self.assertEqual(cable_issues(cable), ())
        self.assertIsNone(cable.strand_gmr)
        self.assertIsNone(cable.relative_permittivity)

    def test_estimates_radius_with_units_and_marks_it(self) -> None:
        cable = complete_wire()
        cable.gmr = 0.01
        cable.gmr_units = "m"
        cable.radius_units = "cm"
        cable.diameter = 3.0

        result = estimate_radius_from_gmr(cable)

        self.assertAlmostEqual(result, 1.284027, places=6)
        self.assertIsNone(cable.diameter)
        self.assertTrue(cable.radius_estimated)

    def test_estimates_diameter_from_section_and_validates_fill_factor(self) -> None:
        cable = complete_wire()
        result = estimate_diameter_from_section(cable, 95.0, 0.75)

        self.assertAlmostEqual(result, 1.269949, places=6)
        self.assertEqual(cable.nominal_section, 95.0)
        self.assertIsNone(cable.radius)
        self.assertTrue(cable.radius_estimated)
        with self.assertRaisesRegex(ValueError, "entre zero e um"):
            estimate_diameter_from_section(cable, 95.0, 1.1)


class GeometryDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.arrangement = ArrangementDefinition(
            "arr",
            "Arranjo",
            3,
            "m",
            [
                ConductorPosition(-1.0, 10.0),
                ConductorPosition(0.0, 10.0),
                ConductorPosition(1.0, 10.0),
                ConductorPosition(0.0, 8.0),
            ],
        )
        self.catalog = OpenDssLibraryCatalog(
            [complete_wire("phase", amps=230.0), complete_wire("neutral", amps=100.0), complete_cn()],
            [self.arrangement],
            [GeometryDefinition("geo", "Montagem", "arr", ["phase", "phase", "phase", "neutral"])],
        )

    def test_usage_indexes_and_ampacity_ignore_neutral(self) -> None:
        geometry = self.catalog.geometries[0]
        self.assertEqual(self.catalog.geometries_using_cable("phase"), (geometry,))
        self.assertEqual(self.catalog.geometries_using_arrangement("arr"), (geometry,))
        self.assertEqual(geometry_ampacity(geometry, self.catalog), 230.0)

    def test_missing_ampacity_of_any_phase_returns_none(self) -> None:
        self.catalog.cable("phase").normal_amps = None
        self.assertIsNone(geometry_ampacity(self.catalog.geometries[0], self.catalog))

    def test_phase_types_must_be_homogeneous_but_neutral_may_differ(self) -> None:
        geometry = self.catalog.geometries[0]
        geometry.cable_ids[-1] = "cn"
        self.assertTrue(phase_cable_types_are_homogeneous(geometry, self.arrangement, self.catalog))

        geometry.cable_ids[1] = "cn"
        self.assertFalse(phase_cable_types_are_homogeneous(geometry, self.arrangement, self.catalog))
        self.assertIn("as fases misturam fio nu e cabo concêntrico", geometry_issues(geometry, self.catalog))

    def test_coincident_positions_are_reported_with_one_based_indices(self) -> None:
        self.arrangement.positions[2] = ConductorPosition(-1.0, 10.0)
        self.assertEqual(coincident_positions(self.arrangement), ((1, 3),))

    def test_synchronizing_slots_pads_and_truncates_without_inventing_cables(self) -> None:
        geometry = self.catalog.geometries[0]
        self.arrangement.positions.append(ConductorPosition(2.0, 8.0))
        self.catalog.synchronize_geometry_slots("arr")
        self.assertEqual(geometry.cable_ids[-1], None)
        self.assertEqual(len(geometry.cable_ids), 5)

        del self.arrangement.positions[2:]
        self.catalog.synchronize_geometry_slots("arr")
        self.assertEqual(len(geometry.cable_ids), 2)

    def test_missing_references_are_visible_in_geometry_issues(self) -> None:
        geometry = GeometryDefinition("broken", "Quebrada", "missing", [])
        self.assertEqual(geometry_issues(geometry, self.catalog), ("arranjo ausente da biblioteca",))

        geometry.arrangement_id = "arr"
        geometry.cable_ids = ["missing"] * 4
        self.assertIn("posição sem cabo válido: 1, 2, 3, 4", geometry_issues(geometry, self.catalog))

    def test_generated_names_and_ids_are_deterministic(self) -> None:
        self.assertEqual(unique_name("Arranjo", ["arranjo", "Arranjo 2"]), "Arranjo 3")
        self.assertEqual(unique_id("Cabo 4/0 CAA", ["cabo_4_0_caa"]), "cabo_4_0_caa_2")
