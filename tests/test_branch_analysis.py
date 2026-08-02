from __future__ import annotations

import unittest

import numpy as np

from circuit_viewer import (
    BranchAnalysisResult,
    BranchIssue,
    BranchRecord,
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
        self.assertEqual(record.connection_bar_id, "B1")
        self.assertEqual(record.connection_bar_code, "CB1")
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
                CircuitDefinition("C2", "B0", "", ""),
            ],
        )

        result = analyze_branches(catalog, PHASES)

        self.assertEqual([record.circuit_id for record in result.records], ["C1", "C2"])

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
