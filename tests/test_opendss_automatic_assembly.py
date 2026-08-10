from __future__ import annotations

import unittest

from circuit_viewer.model import CircuitModel, LineNetworkModel, UtmCrs
from circuit_viewer.opendss_automatic_assembly import build_automatic_assemblies
from circuit_viewer.opendss_library import (
    ArrangementDefinition,
    CableDefinition,
    ConductorPosition,
    OpenDssLibraryCatalog,
)
from circuit_viewer.opendss_mapping_store import (
    LibraryNameMapping,
    OpenDssLibraryMappings,
)
from circuit_viewer.phase_config import load_phase_configuration


def lines(
    phases: list[str],
    *,
    arrangements: list[str] | None = None,
    phase_cables: list[str] | None = None,
    neutral_cables: list[str] | None = None,
) -> LineNetworkModel:
    count = len(phases)
    bars = CircuitModel(
        [f"B{index}" for index in range(count + 1)],
        [f"BAR{index}" for index in range(count + 1)],
        [500_000.0 + index for index in range(count + 1)],
        [8_000_000.0] * (count + 1),
        UtmCrs(21, northern=False),
    )
    return LineNetworkModel(
        bars,
        [f"T{index + 1}" for index in range(count)],
        [f"L{index + 1}" for index in range(count)],
        phases,
        list(range(count)),
        list(range(1, count + 1)),
        arrangements or ["AR"] * count,
        phase_cables or ["CF"] * count,
        neutral_cables or ["CN"] * count,
        [10.0] * count,
    )


class AutomaticAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.phase_configuration = load_phase_configuration()
        self.phase_cable = CableDefinition("phase", "CABO FASE")
        self.neutral_cable = CableDefinition("neutral", "CABO NEUTRO")
        self.arrangement = ArrangementDefinition(
            "base",
            "BASE 3F N",
            3,
            "m",
            [
                ConductorPosition(-1.0, 10.0),
                ConductorPosition(0.0, 11.0),
                ConductorPosition(1.0, 10.0),
                ConductorPosition(0.0, 8.0),
            ],
        )
        self.catalog = OpenDssLibraryCatalog(
            [self.phase_cable, self.neutral_cable],
            [self.arrangement],
            [],
        )
        self.mappings = OpenDssLibraryMappings(
            cables=(
                LibraryNameMapping("CF", self.phase_cable.name),
                LibraryNameMapping("CN", self.neutral_cable.name),
            ),
            arrangements=(LibraryNameMapping("AR", self.arrangement.name),),
        )

    def build(self, model: LineNetworkModel):  # noqa: ANN201
        return build_automatic_assemblies(
            model,
            self.phase_configuration,
            self.catalog,
            self.mappings,
        )

    def test_triphase_two_phase_and_single_phase_use_prefix_positions(self) -> None:
        result = self.build(lines(["13", "7", "8", "9", "3"]))

        self.assertEqual(result.total_segments, 5)
        self.assertEqual(result.assembled_segments, 5)
        self.assertEqual(len(result.assemblies), 5)
        by_phases = {item.phase_letters: item for item in result.assemblies}
        self.assertEqual(
            set(by_phases),
            {
                ("D", "E", "F"),
                ("D", "E"),
                ("E", "F"),
                ("D", "F"),
                ("F",),
            },
        )

        fd = by_phases[("D", "F")]
        self.assertEqual(fd.geometry.cable_ids, ["phase", "phase", "neutral"])
        self.assertEqual(
            [(item.x, item.height) for item in fd.arrangement.positions],
            [(-1.0, 10.0), (0.0, 11.0), (0.0, 8.0)],
        )
        single = by_phases[("F",)]
        self.assertEqual(
            [(item.x, item.height) for item in single.arrangement.positions],
            [(-1.0, 10.0), (0.0, 8.0)],
        )

    def test_missing_neutral_removes_positions_and_reports_warning(self) -> None:
        result = self.build(lines(["9"], neutral_cables=["SEM-MAPA"]))

        self.assertEqual(result.assembled_segments, 1)
        assembly = result.assemblies[0]
        self.assertEqual(assembly.phase_letters, ("D", "F"))
        self.assertEqual(assembly.arrangement.conductor_count, 2)
        self.assertEqual(assembly.geometry.cable_ids, ["phase", "phase"])
        self.assertFalse(assembly.geometry.reduce)
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].severity, "warning")
        self.assertEqual(result.issues[0].field, "CABON_ID")
        self.assertIn("removidas", result.issues[0].reason)

    def test_phase_variants_are_distinct_and_identical_lines_are_grouped(self) -> None:
        result = self.build(lines(["7", "7", "8"]))

        self.assertEqual(len(result.assemblies), 2)
        by_phases = {item.phase_letters: item for item in result.assemblies}
        self.assertEqual(by_phases[("D", "E")].segment_ids, ("T1", "T2"))
        self.assertEqual(by_phases[("E", "F")].segment_ids, ("T3",))
        self.assertNotEqual(
            by_phases[("D", "E")].assembly_id,
            by_phases[("E", "F")].assembly_id,
        )

    def test_any_arrangement_with_enough_phase_slots_is_accepted(self) -> None:
        two_phase = ArrangementDefinition(
            "two",
            "BASE 2F",
            2,
            "m",
            [ConductorPosition(0.0, 9.0), ConductorPosition(1.0, 9.0)],
        )
        self.catalog.arrangements.append(two_phase)
        self.mappings = OpenDssLibraryMappings(
            cables=self.mappings.cables,
            arrangements=(LibraryNameMapping("AR", two_phase.name),),
        )

        accepted = self.build(lines(["9"]))
        refused = self.build(lines(["13"]))

        self.assertEqual(accepted.assembled_segments, 1)
        self.assertEqual(refused.assembled_segments, 0)
        self.assertEqual(refused.issues[0].field, "ARRANJO_ID")
        self.assertIn("oferece 2", refused.issues[0].reason)

    def test_unresolved_fields_are_grouped_without_blocking_other_lines(self) -> None:
        model = lines(
            ["desconhecida", "7", "8", "9"],
            arrangements=["AR", "SEM", "AR", "AR"],
            phase_cables=["CF", "CF", "SEM", "CF"],
        )

        result = self.build(model)

        self.assertEqual(result.assembled_segments, 1)
        self.assertEqual(result.unassembled_segments, 3)
        self.assertEqual(
            {item.field for item in result.issues if item.severity == "error"},
            {"FASES2", "ARRANJO_ID", "CABOF_ID"},
        )
        self.assertEqual(result.assemblies[0].segment_ids, ("T4",))

    def test_ids_and_output_order_do_not_depend_on_line_order(self) -> None:
        first = self.build(lines(["7", "8", "9"]))
        second = self.build(lines(["9", "7", "8"]))

        first_ids = {item.phase_letters: item.assembly_id for item in first.assemblies}
        second_ids = {item.phase_letters: item.assembly_id for item in second.assemblies}
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(
            [item.phase_letters for item in first.assemblies],
            [item.phase_letters for item in second.assemblies],
        )


if __name__ == "__main__":
    unittest.main()
