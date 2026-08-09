from __future__ import annotations

import unittest

import numpy as np

from circuit_viewer import (
    BranchAnalysisResult,
    BranchIssue,
    BranchRecord,
    BranchType,
    analyze_branches,
)
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("d", "D", 1),
        PhaseMappingEntry("e", "E", 1),
        PhaseMappingEntry("ab", "AB", 2),
        PhaseMappingEntry("def", "DEF", 3),
    )
)


def make_bars(count: int) -> CircuitModel:
    return CircuitModel(
        [f"B{index}" for index in range(count)],
        [f"CB{index}" for index in range(count)],
        [500_000.0 + index * 10.0 for index in range(count)],
        [8_000_000.0] * count,
        UtmCrs(21, northern=False),
    )


def make_network(
    bars: CircuitModel,
    starts: list[int],
    ends: list[int],
    phases: list[str],
    lengths: list[float] | None = None,
) -> LineNetworkModel:
    count = len(starts)
    return LineNetworkModel(
        bars,
        [f"T{index}" for index in range(count)],
        [f"CT{index}" for index in range(count)],
        phases,
        starts,
        ends,
        [""] * count,
        [""] * count,
        [""] * count,
        lengths or [10.0] * count,
    )


def make_loads(bars: CircuitModel, bar_indices: list[int]) -> LoadModel:
    count = len(bar_indices)
    return LoadModel(
        bars,
        [f"L{index}" for index in range(count)],
        bar_indices,
        [""] * count,
        [""] * count,
        [""] * count,
        [""] * count,
        [""] * count,
        [""] * count,
        [""] * count,
    )


def make_switch(
    network: LineNetworkModel,
    segment_index: int,
    *,
    state: str = "1",
    circuit_id: str = "C1",
) -> SwitchModel:
    return SwitchModel(
        network,
        ["CH1"],
        ["TIPO"],
        [circuit_id],
        [segment_index],
        ["CCH1"],
        [state],
        ["1"],
        [""],
        [""],
        [""],
    )


class BranchAnalysisTests(unittest.TestCase):
    def test_public_types_are_exposed(self) -> None:
        self.assertTrue(BranchRecord.__dataclass_fields__)
        self.assertTrue(BranchIssue.__dataclass_fields__)
        self.assertTrue(BranchAnalysisResult.__dataclass_fields__)
        self.assertEqual(BranchType.MONOPHASIC.value, "MONOFASICO")
        self.assertEqual(BranchType.BIPHASIC.value, "BIFASICO")

    def test_bifurcated_component_metrics_loads_phase_and_switch_depth(self) -> None:
        bars = make_bars(7)
        network = make_network(
            bars,
            [0, 1, 1, 3, 3, 4],
            [1, 2, 3, 4, 5, 6],
            ["DEF", "DEF", "D", "D", "D", "E"],
        )
        switches = make_switch(network, 3)
        catalog = CircuitCatalogModel.build(
            network,
            switches,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        loads = make_loads(bars, [1, 3, 3, 4])

        result = analyze_branches(catalog, PHASES, loads)

        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.circuit_id, "C1")
        self.assertEqual(record.branch_type, BranchType.MONOPHASIC)
        self.assertEqual(record.connection_bar_id, "B1")
        self.assertEqual(record.connection_bar_code, "CB1")
        self.assertEqual(record.topological_level, 1)
        self.assertEqual(record.first_segment_id, "T2")
        self.assertEqual(record.first_segment_code, "CT2")
        self.assertEqual(set(record.segment_indices), {2, 3, 4})
        self.assertEqual(set(record.bar_indices), {3, 4, 5})
        self.assertEqual(record.segment_count, 3)
        self.assertEqual(record.bar_count, 3)
        self.assertEqual(record.total_length, 30.0)
        self.assertEqual(record.load_count, 3)  # a carga da barra B1 foi excluída
        self.assertEqual(record.phase, "D")
        self.assertEqual(record.switch_count, 1)
        self.assertEqual(record.first_switch_position, 2)
        self.assertTrue(record.removable)
        self.assertEqual(record.topology, "Bifurcado")
        self.assertTrue(any(issue.kind == "single-phase-transition" for issue in result.issues))
        self.assertFalse(record.segment_indices.flags.writeable)
        self.assertFalse(record.bar_indices.flags.writeable)

    def test_missing_length_makes_total_unavailable(self) -> None:
        bars = make_bars(4)
        network = make_network(
            bars,
            [0, 1, 2],
            [1, 2, 3],
            ["DEF", "D", "D"],
            [10.0, 5.0, np.nan],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        record = analyze_branches(catalog, PHASES).records[0]

        self.assertIsNone(record.total_length)
        self.assertEqual(record.missing_length_count, 1)

    def test_topological_level_counts_trunk_segments_from_source(self) -> None:
        bars = make_bars(5)
        network = make_network(
            bars,
            [0, 1, 0, 1],
            [1, 2, 3, 4],
            ["DEF", "DEF", "D", "E"],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        records = analyze_branches(catalog, PHASES).records

        levels_by_connection = {
            record.connection_bar_id: record.topological_level
            for record in records
        }
        self.assertEqual(levels_by_connection, {"B0": 0, "B1": 1})

    def test_topological_level_uses_shortest_path_in_cyclic_trunk(self) -> None:
        bars = make_bars(4)
        network = make_network(
            bars,
            [0, 1, 0, 2],
            [1, 2, 2, 3],
            ["DEF", "DEF", "DEF", "D"],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        record = analyze_branches(catalog, PHASES).records[0]

        self.assertEqual(record.connection_bar_id, "B2")
        self.assertEqual(record.topological_level, 1)

    def test_closed_three_phase_switch_counts_as_a_topological_hop(self) -> None:
        bars = make_bars(4)
        network = make_network(
            bars,
            [0, 1, 1],
            [1, 2, 3],
            ["DEF", "DEF", "D"],
        )
        switches = make_switch(network, 0)
        catalog = CircuitCatalogModel.build(
            network,
            switches,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        record = analyze_branches(catalog, PHASES).records[0]

        self.assertEqual(record.connection_bar_id, "B1")
        self.assertEqual(record.topological_level, 1)

    def test_biphasic_branch_incorporates_every_single_phase_subtree(self) -> None:
        bars = make_bars(7)
        network = make_network(
            bars,
            [0, 1, 1, 3, 4, 4],
            [1, 2, 3, 4, 5, 6],
            ["DEF", "DEF", "AB", "ab", "D", "E"],
        )
        switches = make_switch(network, 4)
        catalog = CircuitCatalogModel.build(
            network,
            switches,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        loads = make_loads(bars, [1, 3, 4, 5, 6])

        result = analyze_branches(catalog, PHASES, loads)

        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.branch_type, BranchType.BIPHASIC)
        self.assertEqual(record.phases2, "AB")
        self.assertEqual(record.phase, "AB")
        self.assertEqual(set(record.segment_indices), {2, 3, 4, 5})
        self.assertEqual(set(record.bar_indices), {3, 4, 5, 6})
        self.assertEqual(set(record.load_indices), {1, 2, 3, 4})
        self.assertEqual(record.load_count, 4)
        self.assertEqual(record.first_switch_position, 3)
        self.assertTrue(record.removable)
        self.assertEqual(record.topology, "Bifurcado")

    def test_monophasic_and_biphasic_branches_coexist(self) -> None:
        bars = make_bars(5)
        network = make_network(
            bars,
            [0, 1, 1, 2],
            [1, 2, 3, 4],
            ["DEF", "DEF", "AB", "D"],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        result = analyze_branches(catalog, PHASES)

        self.assertEqual(
            [record.branch_type for record in result.records],
            [BranchType.BIPHASIC, BranchType.MONOPHASIC],
        )
        self.assertEqual([record.branch_id for record in result.records], [1, 2])

    def test_shared_single_phase_component_is_excluded_without_load_duplication(self) -> None:
        bars = make_bars(7)
        network = make_network(
            bars,
            [0, 1, 1, 2, 4, 6],
            [1, 2, 4, 5, 6, 5],
            ["DEF", "DEF", "AB", "AB", "D", "E"],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        loads = make_loads(bars, [4, 5, 6])

        result = analyze_branches(catalog, PHASES, loads)

        self.assertEqual(len(result.records), 2)
        self.assertTrue(
            all(record.branch_type == BranchType.BIPHASIC for record in result.records)
        )
        self.assertEqual(
            set().union(*(set(record.segment_indices) for record in result.records)),
            {2, 3},
        )
        self.assertEqual(
            set().union(*(set(record.load_indices) for record in result.records)),
            {0, 1},
        )
        self.assertTrue(
            any(
                issue.kind == "ambiguous-single-phase-subtree"
                for issue in result.issues
            )
        )

    def test_single_phase_bridge_to_trunk_is_excluded_and_reported(self) -> None:
        bars = make_bars(5)
        network = make_network(
            bars,
            [0, 1, 1, 3, 4],
            [1, 2, 3, 4, 2],
            ["DEF", "DEF", "AB", "D", "D"],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        loads = make_loads(bars, [3, 4])

        result = analyze_branches(catalog, PHASES, loads)

        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.branch_type, BranchType.BIPHASIC)
        self.assertEqual(set(record.segment_indices), {2})
        self.assertEqual(set(record.load_indices), {0})
        self.assertTrue(
            any(issue.kind == "single-phase-trunk-bridge" for issue in result.issues)
        )

    def test_biphasic_transition_is_a_boundary_and_cycle_is_classified(self) -> None:
        bars = make_bars(5)
        transition_phases = PhaseConfiguration(
            (*PHASES.entries, PhaseMappingEntry("bc", "BC", 2))
        )
        transition_network = make_network(
            bars,
            [0, 1, 2],
            [1, 2, 3],
            ["DEF", "AB", "BC"],
        )
        catalog = CircuitCatalogModel.build(
            transition_network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        result = analyze_branches(catalog, transition_phases)

        self.assertEqual(set(result.records[0].segment_indices), {1})
        self.assertTrue(any(issue.kind == "two-phase-transition" for issue in result.issues))

        cycle_bars = make_bars(4)
        cycle_network = make_network(
            cycle_bars,
            [0, 1, 2, 3],
            [1, 2, 3, 1],
            ["DEF", "AB", "AB", "AB"],
        )
        cycle_catalog = CircuitCatalogModel.build(
            cycle_network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        cycle_record = analyze_branches(cycle_catalog, PHASES).records[0]
        self.assertEqual(cycle_record.branch_type, BranchType.BIPHASIC)
        self.assertIn("Cíclico", cycle_record.topology)

    def test_multiple_trunk_connections_form_one_deterministic_record(self) -> None:
        bars = make_bars(5)
        network = make_network(
            bars,
            [0, 1, 1, 2, 3],
            [1, 2, 3, 4, 4],
            ["DEF", "DEF", "D", "D", "D"],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        result = analyze_branches(catalog, PHASES)

        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.first_segment_id, "T2")
        self.assertEqual(record.connection_bar_id, "B1")
        self.assertEqual(record.topological_level, 1)
        self.assertEqual(record.trunk_connection_count, 2)
        self.assertIn("Múltiplas conexões", record.topology)
        self.assertTrue(
            any(issue.kind == "multiple-trunk-connections" for issue in result.issues)
        )

    def test_cycle_is_reported_in_topology(self) -> None:
        bars = make_bars(6)
        network = make_network(
            bars,
            [0, 1, 1, 3, 4, 5],
            [1, 2, 3, 4, 5, 3],
            ["DEF", "DEF", "D", "D", "D", "D"],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        record = analyze_branches(catalog, PHASES).records[0]

        self.assertEqual(record.topology, "Cíclico")
        self.assertEqual(record.segment_count, 4)

    def test_open_switch_truncates_branch_and_is_not_removable(self) -> None:
        bars = make_bars(6)
        network = make_network(
            bars,
            [0, 1, 2, 3, 4],
            [1, 2, 3, 4, 5],
            ["DEF", "D", "D", "D", "D"],
        )
        switches = make_switch(network, 3, state="0")
        catalog = CircuitCatalogModel.build(
            network,
            switches,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        result = analyze_branches(catalog, PHASES)

        record = result.records[0]
        self.assertEqual(set(record.segment_indices), {1, 2})
        self.assertEqual(record.switch_count, 0)
        self.assertIsNone(record.first_switch_position)
        self.assertFalse(record.removable)
        self.assertTrue(any(issue.kind == "open-switch-boundary" for issue in result.issues))

    def test_closed_switch_at_fifth_level_is_removable(self) -> None:
        bars = make_bars(8)
        network = make_network(
            bars,
            list(range(7)),
            list(range(1, 8)),
            ["DEF"] + ["D"] * 6,
        )
        switches = make_switch(network, 5, state="1")
        catalog = CircuitCatalogModel.build(
            network,
            switches,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        record = analyze_branches(catalog, PHASES).records[0]

        self.assertEqual(record.first_switch_position, 5)
        self.assertTrue(record.removable)

    def test_overlapping_circuits_are_analyzed_independently(self) -> None:
        bars = make_bars(4)
        network = make_network(
            bars,
            [0, 1, 2],
            [1, 2, 3],
            ["DEF", "DEF", "D"],
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "", ""),
                CircuitDefinition("C2", "B2", "", ""),
            ],
        )

        result = analyze_branches(catalog, PHASES)

        self.assertEqual([record.circuit_id for record in result.records], ["C1", "C2"])
        self.assertEqual([record.topological_level for record in result.records], [2, 0])

    def test_missing_trunk_and_cancellation(self) -> None:
        bars = make_bars(3)
        network = make_network(bars, [0, 1], [1, 2], ["D", "D"])
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )

        result = analyze_branches(catalog, PHASES)
        self.assertEqual(result.records, ())
        self.assertTrue(
            any(issue.kind == "missing-three-phase-trunk" for issue in result.issues)
        )
        with self.assertRaises(InterruptedError):
            analyze_branches(catalog, PHASES, cancel_check=lambda: True)


if __name__ == "__main__":
    unittest.main()
