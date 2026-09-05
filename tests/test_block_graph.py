from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np

from circuit_viewer.block_analysis import BlockRecord, analyze_blocks
from circuit_viewer.block_graph import (
    FIXED_NODE_DIAMETER,
    HORIZONTAL_NODE_GAP,
    MAX_NODE_DIAMETER,
    MIN_NODE_DIAMETER,
    VERTICAL_NODE_GAP,
    BlockGraph,
    BlockGraphEdge,
    block_coordinate_anchors,
    block_node_diameters,
    build_block_graph,
    direct_circuit_neighbors,
    filter_block_graph,
    layout_block_graph,
    layout_block_graph_by_coordinates,
    resolve_block_circuit_indices,
)

from test_block_analysis import make_bars, make_catalog, make_network, make_switches


def _indices(*values: int) -> np.ndarray:
    result = np.asarray(values, dtype=np.intp)
    result.setflags(write=False)
    return result


def _record(
    block_id: int,
    *,
    power: float | None = None,
    source: bool = False,
) -> BlockRecord:
    return BlockRecord(
        block_id=block_id,
        bar_indices=_indices(block_id - 1),
        segment_indices=_indices(),
        load_indices=_indices(),
        boundary_switch_indices=_indices(),
        boundary_switch_codes=(),
        total_power=power,
        total_length=None,
        contains_source=source,
    )


def _edge(index: int, start: int, end: int) -> BlockGraphEdge:
    return BlockGraphEdge(
        switch_index=index,
        switch_id=f"CH{index}",
        switch_code=f"COD-{index}",
        state="1",
        start_block_id=start,
        end_block_id=end,
    )


class BlockGraphBuildTests(unittest.TestCase):
    def test_a_boundary_switch_becomes_one_labeled_edge(self) -> None:
        bars = make_bars(4)
        network = make_network(bars, [0, 1, 2], [1, 2, 3])
        switches = make_switches(network, [(1, "1", "1")])
        result = analyze_blocks(make_catalog(network, switches), switches)

        graph = build_block_graph(result)

        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].label, "COD-CH0")
        self.assertEqual(
            set(
                (
                    graph.edges[0].start_block_id,
                    graph.edges[0].end_block_id,
                )
            ),
            {1, 2},
        )

    def test_parallel_switches_are_not_collapsed(self) -> None:
        bars = make_bars(2)
        network = make_network(bars, [0, 0], [1, 1])
        switches = make_switches(
            network,
            [(0, "1", "1"), (1, "1", "0")],
        )
        result = analyze_blocks(make_catalog(network, switches), switches)

        graph = build_block_graph(result)

        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(len(graph.edges), 2)
        self.assertEqual(
            {edge.endpoint_key for edge in graph.edges},
            {(1, 2)},
        )

    def test_a_switch_bypassed_by_a_common_path_is_a_self_loop(self) -> None:
        # A chave B0—B1 é retirada para formar blocos, mas B0—B2—B1 ainda une
        # suas pontas; a relação continua visível como autoenlace do bloco.
        bars = make_bars(3)
        network = make_network(bars, [0, 0, 2], [1, 2, 1])
        switches = make_switches(network, [(0, "1", "1")])
        result = analyze_blocks(make_catalog(network, switches), switches)

        graph = build_block_graph(result)

        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].endpoint_key, (1, 1))


class BlockGraphLayoutTests(unittest.TestCase):
    def test_sources_are_roots_and_bfs_defines_the_levels(self) -> None:
        graph = BlockGraph(
            (
                _record(1, source=True),
                _record(2),
                _record(3),
                _record(4),
            ),
            (_edge(0, 1, 2), _edge(1, 2, 3), _edge(2, 1, 4)),
        )

        layout = layout_block_graph(graph)

        self.assertEqual(layout.root_ids, (1,))
        self.assertEqual(layout.depths, {1: 0, 2: 1, 3: 2, 4: 1})
        self.assertLess(layout.positions[1][1], layout.positions[2][1])
        self.assertEqual(len(layout.tree_edge_indices), 3)
        self.assertGreaterEqual(
            abs(layout.positions[2][0] - layout.positions[4][0]),
            HORIZONTAL_NODE_GAP,
        )
        self.assertEqual(
            layout.positions[2][1] - layout.positions[1][1],
            VERTICAL_NODE_GAP,
        )

    def test_multiple_sources_share_the_first_level_and_cycles_remain(self) -> None:
        graph = BlockGraph(
            (
                _record(1, source=True),
                _record(2),
                _record(3, source=True),
            ),
            (_edge(0, 1, 2), _edge(1, 2, 3), _edge(2, 3, 1)),
        )

        layout = layout_block_graph(graph)

        self.assertEqual(layout.root_ids, (1, 3))
        self.assertEqual(layout.depths[1], 0)
        self.assertEqual(layout.depths[3], 0)
        self.assertEqual(layout.depths[2], 1)
        self.assertEqual(len(graph.edges), 3)
        self.assertLess(len(layout.tree_edge_indices), len(graph.edges))

    def test_a_component_without_source_uses_its_smallest_id(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2), _record(3), _record(4)),
            (_edge(0, 1, 2), _edge(1, 3, 4)),
        )

        layout = layout_block_graph(graph)

        self.assertEqual(layout.root_ids, (1, 3))
        self.assertEqual(layout.depths[3], 0)
        self.assertEqual(layout.depths[4], 1)
        self.assertGreater(layout.positions[3][0], layout.positions[1][0])


class BlockGraphCoordinateLayoutTests(unittest.TestCase):
    def test_boundary_bar_at_each_switch_side_is_the_block_anchor(self) -> None:
        bars = make_bars(4)
        network = make_network(bars, [0, 1, 2], [1, 2, 3])
        switches = make_switches(network, [(1, "1", "1")])
        result = analyze_blocks(make_catalog(network, switches), switches)
        graph = build_block_graph(result)

        anchors = block_coordinate_anchors(result)

        edge = graph.edges[0]
        self.assertEqual(
            anchors[edge.start_block_id],
            (float(bars.x[1]), -float(bars.y[1])),
        )
        self.assertEqual(
            anchors[edge.end_block_id],
            (float(bars.x[2]), -float(bars.y[2])),
        )

    def test_block_without_a_boundary_uses_all_of_its_bars(self) -> None:
        bars = make_bars(3)
        network = make_network(bars, [0, 1], [1, 2])
        result = analyze_blocks(make_catalog(network), None)

        anchors = block_coordinate_anchors(result)

        self.assertEqual(
            anchors,
            {1: (float(bars.x.mean()), -float(bars.y.mean()))},
        )

    def test_repeated_boundary_bar_has_only_one_vote_in_the_centroid(self) -> None:
        bars = make_bars(5)
        network = make_network(
            bars,
            [0, 0, 3, 0],
            [3, 1, 2, 4],
        )
        switches = make_switches(
            network,
            [(1, "1", "1"), (2, "1", "1"), (3, "1", "1")],
        )
        result = analyze_blocks(make_catalog(network, switches), switches)
        central = next(record for record in result.records if record.bar_count == 2)

        anchors = block_coordinate_anchors(result)

        self.assertEqual(
            anchors[central.block_id],
            (
                (float(bars.x[0]) + float(bars.x[3])) / 2.0,
                -(float(bars.y[0]) + float(bars.y[3])) / 2.0,
            ),
        )

    def test_uniform_scale_preserves_shape_and_targets_typical_edge_length(self) -> None:
        graph = BlockGraph(
            (_record(1), _record(2), _record(3)),
            (_edge(0, 1, 2), _edge(1, 2, 3)),
        )
        anchors = {1: (0.0, 0.0), 2: (10.0, 0.0), 3: (10.0, -10.0)}

        layout = layout_block_graph_by_coordinates(graph, anchors)

        first = layout.positions[1]
        second = layout.positions[2]
        third = layout.positions[3]
        self.assertAlmostEqual(math.dist(first, second), 200.0)
        self.assertAlmostEqual(math.dist(second, third), 200.0)
        self.assertAlmostEqual(first[1], second[1])
        self.assertLess(third[1], second[1])

    def test_coincident_nodes_receive_only_a_deterministic_minimum_separation(self) -> None:
        graph = BlockGraph(
            (_record(1), _record(2)),
            (_edge(0, 1, 2),),
        )
        anchors = {1: (5.0, 5.0), 2: (5.0, 5.0)}

        first = layout_block_graph_by_coordinates(graph, anchors)
        second = layout_block_graph_by_coordinates(graph, anchors)

        self.assertEqual(first.positions, second.positions)
        self.assertGreaterEqual(
            math.dist(first.positions[1], first.positions[2]),
            FIXED_NODE_DIAMETER + 24.0,
        )

    def test_missing_source_geometry_disables_coordinate_anchors(self) -> None:
        result = SimpleNamespace(
            records=(_record(1),),
            source_segments=None,
            source_switches=None,
        )

        self.assertEqual(block_coordinate_anchors(result), {})


class BlockGraphFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = BlockGraph(
            (
                _record(1, source=True),
                _record(2),
                _record(3),
                _record(4),
                _record(5),
            ),
            (
                _edge(0, 1, 2),
                _edge(1, 2, 3),
                _edge(2, 3, 4),
                _edge(3, 4, 4),
                _edge(4, 1, 2),
                _edge(5, 4, 5),
            ),
        )
        self.circuits = {1: 0, 2: 0, 3: 1, 4: 2, 5: None}

    def test_filter_is_induced_and_preserves_parallel_edges_and_indices(self) -> None:
        filtered = filter_block_graph(
            self.graph,
            self.circuits,
            frozenset({0, 1}),
        )

        self.assertEqual(filtered.node_ids, (1, 2, 3))
        self.assertEqual(
            tuple(edge.switch_index for edge in filtered.edges),
            (0, 1, 4),
        )

    def test_single_circuit_adds_only_direct_external_blocks_and_edges(self) -> None:
        filtered = filter_block_graph(
            self.graph,
            self.circuits,
            frozenset({0}),
        )

        self.assertEqual(filtered.node_ids, (1, 2, 3))
        self.assertEqual(
            tuple(edge.switch_index for edge in filtered.edges),
            (0, 1, 4),
        )
        self.assertNotIn(4, filtered.node_ids)
        self.assertNotIn(5, filtered.node_ids)

    def test_single_circuit_preserves_parallel_boundary_switches(self) -> None:
        circuits = {1: 0, 2: 1, 3: 1, 4: 2, 5: None}

        filtered = filter_block_graph(
            self.graph,
            circuits,
            frozenset({0}),
        )

        self.assertEqual(filtered.node_ids, (1, 2))
        self.assertEqual(
            tuple(edge.switch_index for edge in filtered.edges),
            (0, 4),
        )

    def test_single_circuit_does_not_add_an_unresolved_external_block(self) -> None:
        filtered = filter_block_graph(
            self.graph,
            self.circuits,
            frozenset({2}),
        )

        self.assertEqual(filtered.node_ids, (3, 4))
        self.assertEqual(
            tuple(edge.switch_index for edge in filtered.edges),
            (2, 3),
        )

    def test_unresolved_blocks_are_an_explicit_independent_choice(self) -> None:
        hidden = filter_block_graph(self.graph, self.circuits, frozenset())
        shown = filter_block_graph(
            self.graph,
            self.circuits,
            frozenset(),
            include_unresolved=True,
        )

        self.assertEqual(hidden.node_ids, ())
        self.assertEqual(shown.node_ids, (5,))
        self.assertEqual(shown.edges, ())

    def test_neighbors_expand_exactly_one_intercircuit_step(self) -> None:
        neighbors = direct_circuit_neighbors(
            self.graph,
            self.circuits,
            frozenset({0}),
        )

        self.assertEqual(neighbors, frozenset({1}))
        self.assertNotIn(2, neighbors)

    def test_same_circuit_self_loop_and_unresolved_edge_are_not_neighbors(self) -> None:
        neighbors = direct_circuit_neighbors(
            self.graph,
            self.circuits,
            frozenset({2}),
        )

        self.assertEqual(neighbors, frozenset({1}))


class BlockNodeDiameterTests(unittest.TestCase):
    def test_fixed_mode_ignores_power(self) -> None:
        records = (_record(1, power=None), _record(2, power=1_000.0))

        self.assertEqual(
            block_node_diameters(records, False),
            {1: FIXED_NODE_DIAMETER, 2: FIXED_NODE_DIAMETER},
        )

    def test_scaled_mode_makes_area_proportional_between_the_limits(self) -> None:
        records = (
            _record(1, power=0.0),
            _record(2, power=25.0),
            _record(3, power=100.0),
            _record(4, power=None),
            _record(5, power=-10.0),
        )

        diameters = block_node_diameters(records, True)

        self.assertEqual(diameters[1], MIN_NODE_DIAMETER)
        self.assertEqual(diameters[3], MAX_NODE_DIAMETER)
        self.assertEqual(MAX_NODE_DIAMETER, 72.0)
        self.assertEqual(diameters[4], MIN_NODE_DIAMETER)
        self.assertEqual(diameters[5], MIN_NODE_DIAMETER)
        expected = math.sqrt(
            MIN_NODE_DIAMETER**2
            + 0.25 * (MAX_NODE_DIAMETER**2 - MIN_NODE_DIAMETER**2)
        )
        self.assertAlmostEqual(diameters[2], expected)

    def test_without_positive_power_all_nodes_use_the_minimum(self) -> None:
        records = (_record(1, power=None), _record(2, power=0.0))

        self.assertEqual(
            block_node_diameters(records, True),
            {1: MIN_NODE_DIAMETER, 2: MIN_NODE_DIAMETER},
        )


class BlockCircuitResolutionTests(unittest.TestCase):
    @staticmethod
    def _catalog(*memberships):  # noqa: ANN205
        values = tuple(
            SimpleNamespace(bar_indices=_indices(*bar_indices))
            for bar_indices in memberships
        )
        return SimpleNamespace(
            memberships=values,
            index_for_id=lambda circuit_id: {"C1": 0, "C2": 1}.get(circuit_id),
        )

    def test_unique_bar_membership_assigns_each_block(self) -> None:
        result = SimpleNamespace(records=(_record(1), _record(2)), source_switches=None)

        resolved = resolve_block_circuit_indices(
            result,
            self._catalog((0,), (1,)),
        )

        self.assertEqual(resolved, {1: 0, 2: 1})

    def test_conflicting_bar_memberships_leave_the_block_neutral(self) -> None:
        result = SimpleNamespace(records=(_record(1),), source_switches=None)

        resolved = resolve_block_circuit_indices(
            result,
            self._catalog((0,), (0,)),
        )

        self.assertEqual(resolved, {1: None})

    def test_boundary_switch_is_a_fallback_for_an_unreached_block(self) -> None:
        record = replace(
            _record(1),
            boundary_switch_indices=_indices(0),
            boundary_switch_codes=("CH-1",),
        )
        switches = SimpleNamespace(
            record=lambda index: SimpleNamespace(circuit_id="C2")
        )
        result = SimpleNamespace(records=(record,), source_switches=switches)

        resolved = resolve_block_circuit_indices(
            result,
            self._catalog((), ()),
        )

        self.assertEqual(resolved, {1: 1})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
