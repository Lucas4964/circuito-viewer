from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np

from circuit_viewer.block_analysis import BlockRecord, analyze_blocks
from circuit_viewer.block_graph import (
    FIXED_NODE_DIAMETER,
    GEOGRAPHIC_TARGET_EDGE_LENGTH,
    MAX_COORDINATE_EDGE_LENGTH,
    MAX_NODE_DIAMETER,
    MIN_COORDINATE_EDGE_LENGTH,
    MIN_NODE_DIAMETER,
    TREE_BAND_GAP,
    TREE_SIBLING_GAP,
    BlockGraph,
    BlockGraphEdge,
    BlockGraphLayout,
    BlockNodeEnvelope,
    block_coordinate_anchors,
    block_node_diameters,
    block_node_envelopes,
    build_block_graph,
    direct_circuit_neighbors,
    filter_block_graph,
    layout_block_graph,
    layout_block_graph_by_coordinates,
    resolve_block_circuit_indices,
    route_block_graph_edges,
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
    def test_layout_metadata_defaults_keep_the_old_constructor_compatible(self) -> None:
        layout = BlockGraphLayout({}, {}, (), frozenset())

        self.assertEqual(layout.edge_routes, {})
        self.assertEqual(layout.edge_label_positions, {})
        self.assertEqual(layout.edge_label_leaders, {})

    def test_node_envelope_reserves_caption_and_keeps_the_circle_diameter(self) -> None:
        record = _record(1)

        envelope = block_node_envelopes((record,), {1: 72.0})[1]

        self.assertGreaterEqual(envelope.width, 138.0)
        self.assertGreater(envelope.height, 72.0 + 22.0)
        self.assertEqual(envelope.node_diameter, 72.0)

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
            FIXED_NODE_DIAMETER + TREE_SIBLING_GAP,
        )
        self.assertEqual(
            layout.positions[2][1] - layout.positions[1][1],
            BlockNodeEnvelope().height + TREE_BAND_GAP,
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
        self.assertEqual(layout.positions[1][1], layout.positions[3][1])
        self.assertLess(layout.positions[1][1], layout.positions[2][1])
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

    def test_a_wide_star_keeps_all_children_on_one_compact_level(self) -> None:
        graph = BlockGraph(
            tuple(_record(block_id, source=block_id == 1) for block_id in range(1, 22)),
            tuple(_edge(index, 1, index + 2) for index in range(20)),
        )
        envelopes = {
            block_id: BlockNodeEnvelope(60.0, 80.0, diameter=56.0)
            for block_id in graph.node_ids
        }

        first = layout_block_graph(graph, node_envelopes=envelopes)
        second = layout_block_graph(graph, node_envelopes=envelopes)

        self.assertEqual(first, second)
        leaves = tuple(range(2, 22))
        leaf_y = {first.positions[block_id][1] for block_id in leaves}
        self.assertEqual(leaf_y, {first.positions[2][1]})
        self.assertTrue(all(first.depths[block_id] == 1 for block_id in leaves))
        self.assertLess(first.positions[1][1], first.positions[2][1])

        leaf_x = sorted(first.positions[block_id][0] for block_id in leaves)
        expected_width = (len(leaves) - 1) * (60.0 + TREE_SIBLING_GAP)
        self.assertLessEqual(leaf_x[-1] - leaf_x[0], expected_width + 1.0e-6)
        self.assertAlmostEqual(
            first.positions[1][0],
            (leaf_x[0] + leaf_x[-1]) / 2.0,
        )
        for left, right in zip(leaf_x, leaf_x[1:]):
            self.assertGreaterEqual(
                right - left,
                60.0 + TREE_SIBLING_GAP - 1.0e-6,
            )

    def test_balanced_tree_has_vertical_layers_and_contiguous_subtrees(self) -> None:
        graph = BlockGraph(
            tuple(_record(block_id, source=block_id == 1) for block_id in range(1, 16)),
            tuple(
                _edge(index, parent, child)
                for index, (parent, child) in enumerate(
                    (
                        (1, 2),
                        (1, 3),
                        (2, 4),
                        (2, 5),
                        (3, 6),
                        (3, 7),
                        (4, 8),
                        (4, 9),
                        (5, 10),
                        (5, 11),
                        (6, 12),
                        (6, 13),
                        (7, 14),
                        (7, 15),
                    )
                )
            ),
        )
        envelopes = {
            block_id: BlockNodeEnvelope(60.0, 80.0, diameter=56.0)
            for block_id in graph.node_ids
        }

        layout = layout_block_graph(graph, node_envelopes=envelopes)

        level_y: dict[int, float] = {}
        for depth in range(4):
            values = {
                layout.positions[block_id][1]
                for block_id, block_depth in layout.depths.items()
                if block_depth == depth
            }
            self.assertEqual(len(values), 1)
            level_y[depth] = values.pop()
        self.assertEqual(
            [level_y[depth] for depth in range(4)],
            sorted(level_y.values()),
        )
        for depth in range(3):
            self.assertAlmostEqual(
                level_y[depth + 1] - level_y[depth],
                80.0 + TREE_BAND_GAP,
            )

        children = {
            1: (2, 3),
            2: (4, 5),
            3: (6, 7),
            4: (8, 9),
            5: (10, 11),
            6: (12, 13),
            7: (14, 15),
        }
        for parent, descendants in children.items():
            descendant_x = [layout.positions[value][0] for value in descendants]
            self.assertAlmostEqual(
                layout.positions[parent][0],
                (min(descendant_x) + max(descendant_x)) / 2.0,
            )

        left_branch = (2, 4, 5, 8, 9, 10, 11)
        right_branch = (3, 6, 7, 12, 13, 14, 15)
        left_span = (
            min(layout.positions[value][0] for value in left_branch),
            max(layout.positions[value][0] for value in left_branch),
        )
        right_span = (
            min(layout.positions[value][0] for value in right_branch),
            max(layout.positions[value][0] for value in right_branch),
        )
        self.assertTrue(
            left_span[1] < right_span[0] or right_span[1] < left_span[0]
        )

        edge_lengths = [
            math.dist(
                layout.positions[edge.start_block_id],
                layout.positions[edge.end_block_id],
            )
            for edge in graph.edges
        ]
        self.assertLessEqual(max(edge_lengths), 2.0 * (80.0 + TREE_BAND_GAP))

    def test_a_deep_tree_is_a_straight_compact_vertical_hierarchy(self) -> None:
        graph = BlockGraph(
            tuple(_record(block_id, source=block_id == 1) for block_id in range(1, 11)),
            tuple(_edge(index, index + 1, index + 2) for index in range(9)),
        )
        envelopes = {
            block_id: BlockNodeEnvelope(60.0, 80.0, diameter=56.0)
            for block_id in graph.node_ids
        }

        layout = layout_block_graph(graph, node_envelopes=envelopes)

        self.assertEqual(layout.root_ids, (1,))
        expected_depths = {
            block_id: block_id - 1 for block_id in graph.node_ids
        }
        self.assertEqual(layout.depths, expected_depths)
        self.assertEqual(
            {round(layout.positions[block_id][0], 9) for block_id in graph.node_ids},
            {round(layout.positions[1][0], 9)},
        )
        for parent in range(1, 10):
            self.assertAlmostEqual(
                layout.positions[parent + 1][1] - layout.positions[parent][1],
                80.0 + TREE_BAND_GAP,
            )

    def test_disconnected_components_keep_roots_above_children(self) -> None:
        graph = BlockGraph(
            (
                _record(1, source=True),
                _record(2),
                _record(3),
                _record(10, source=True),
                _record(11),
                _record(12),
                _record(20),
            ),
            (
                _edge(0, 1, 2),
                _edge(1, 2, 3),
                _edge(2, 10, 11),
                _edge(3, 11, 12),
            ),
        )

        layout = layout_block_graph(graph)

        self.assertEqual(set(layout.root_ids), {1, 10, 20})
        for root, child, grandchild in ((1, 2, 3), (10, 11, 12)):
            self.assertEqual(
                (layout.depths[root], layout.depths[child], layout.depths[grandchild]),
                (0, 1, 2),
            )
            self.assertLess(
                layout.positions[root][1],
                layout.positions[child][1],
            )
            self.assertLess(
                layout.positions[child][1],
                layout.positions[grandchild][1],
            )
        self.assertEqual(layout.depths[20], 0)

    def test_cycles_parallel_edges_and_self_loops_do_not_break_layers(self) -> None:
        graph = BlockGraph(
            tuple(_record(block_id, source=block_id == 1) for block_id in range(1, 6)),
            (
                _edge(0, 1, 2),
                _edge(1, 1, 3),
                _edge(2, 2, 4),
                _edge(3, 3, 5),
                _edge(4, 2, 3),
                _edge(5, 4, 5),
                _edge(6, 1, 2),
                _edge(7, 4, 4),
            ),
        )

        layout = layout_block_graph(graph)

        self.assertEqual(layout.depths, {1: 0, 2: 1, 3: 1, 4: 2, 5: 2})
        self.assertEqual(len(layout.tree_edge_indices), len(graph.nodes) - 1)
        self.assertNotIn(6, layout.tree_edge_indices)
        self.assertNotIn(7, layout.tree_edge_indices)
        self.assertEqual(set(layout.edge_routes), set(range(8)))
        y_by_depth = {
            depth: {
                layout.positions[block_id][1]
                for block_id, block_depth in layout.depths.items()
                if block_depth == depth
            }
            for depth in range(3)
        }
        self.assertTrue(all(len(values) == 1 for values in y_by_depth.values()))
        self.assertLess(
            next(iter(y_by_depth[0])),
            next(iter(y_by_depth[1])),
        )
        self.assertLess(
            next(iter(y_by_depth[1])),
            next(iter(y_by_depth[2])),
        )

    def test_hierarchical_positions_do_not_depend_on_input_order(self) -> None:
        nodes = tuple(
            _record(block_id, source=block_id == 1) for block_id in range(1, 8)
        )
        edges = (
            _edge(40, 1, 2),
            _edge(41, 1, 3),
            _edge(42, 2, 4),
            _edge(43, 2, 5),
            _edge(44, 3, 6),
            _edge(45, 3, 7),
        )

        forward = layout_block_graph(BlockGraph(nodes, edges))
        reversed_input = layout_block_graph(
            BlockGraph(tuple(reversed(nodes)), tuple(reversed(edges)))
        )

        self.assertEqual(forward.positions, reversed_input.positions)
        self.assertEqual(forward.depths, reversed_input.depths)
        self.assertEqual(forward.root_ids, reversed_input.root_ids)

    def test_single_circuit_external_block_is_a_leaf_and_never_a_root(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2), _record(3, source=True)),
            (_edge(0, 1, 2), _edge(1, 2, 3)),
        )

        layout = layout_block_graph(
            graph,
            block_circuit_indices={1: 0, 2: 0, 3: 1},
            selected_circuit_indices={0},
        )

        self.assertEqual(layout.root_ids, (1,))
        self.assertEqual(layout.depths[3], layout.depths[2] + 1)


class BlockGraphRoutingTests(unittest.TestCase):
    def test_routes_and_labels_use_original_switch_indices(self) -> None:
        graph = BlockGraph(
            (_record(1), _record(2), _record(3)),
            (_edge(17, 1, 3),),
        )
        positions = {1: (0.0, 0.0), 2: (100.0, 0.0), 3: (200.0, 0.0)}

        routes, labels, leaders = route_block_graph_edges(graph, positions)

        self.assertEqual(set(routes), {17})
        self.assertEqual(set(labels), {17})
        self.assertIsInstance(leaders, dict)
        self.assertGreaterEqual(len(routes[17].points), 3)
        self.assertNotEqual(routes[17].points[0], positions[1])
        self.assertNotEqual(routes[17].points[-1], positions[3])

    def test_route_is_clipped_at_the_real_circle_not_the_caption_envelope(self) -> None:
        graph = BlockGraph((_record(1), _record(2)), (_edge(3, 1, 2),))
        envelopes = {
            block_id: BlockNodeEnvelope(138.0, 130.0, diameter=72.0)
            for block_id in graph.node_ids
        }

        routes, _labels, _leaders = route_block_graph_edges(
            graph,
            {1: (0.0, 0.0), 2: (240.0, 0.0)},
            envelopes,
        )

        self.assertAlmostEqual(routes[3].points[0][0], 36.0)
        self.assertAlmostEqual(routes[3].points[-1][0], 204.0)

    def test_parallel_edges_and_self_loops_keep_distinct_symmetric_routes(self) -> None:
        graph = BlockGraph(
            (_record(1), _record(2)),
            (
                _edge(10, 1, 2),
                _edge(11, 1, 2),
                _edge(12, 1, 1),
                _edge(13, 1, 1),
            ),
        )

        routes, _labels, _leaders = route_block_graph_edges(
            graph,
            {1: (0.0, 0.0), 2: (200.0, 0.0)},
        )

        self.assertNotEqual(routes[10], routes[11])
        self.assertTrue(routes[10].curved)
        self.assertTrue(routes[11].curved)
        self.assertNotEqual(routes[12], routes[13])
        self.assertTrue(routes[12].curved)


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
        self.assertAlmostEqual(
            math.dist(first, second),
            GEOGRAPHIC_TARGET_EDGE_LENGTH,
        )
        self.assertAlmostEqual(
            math.dist(second, third),
            GEOGRAPHIC_TARGET_EDGE_LENGTH,
        )
        self.assertAlmostEqual(first[1], second[1])
        self.assertLess(third[1], second[1])

    def test_circuit_clusters_keep_east_west_and_north_south_orientation(self) -> None:
        graph = BlockGraph(
            tuple(_record(block_id) for block_id in range(1, 5)),
            (
                _edge(0, 1, 2),
                _edge(1, 2, 3),
                _edge(2, 3, 4),
                _edge(3, 4, 1),
            ),
        )
        # As coordenadas já usam o eixo Y da visualização: norte é
        # negativo, como nas âncoras produzidas por block_coordinate_anchors.
        anchors = {
            1: (0.0, 0.0),
            2: (100.0, 0.0),
            3: (100.0, -100.0),
            4: (0.0, -100.0),
        }
        circuits = {block_id: block_id - 1 for block_id in graph.node_ids}

        first = layout_block_graph_by_coordinates(
            graph,
            anchors,
            block_circuit_indices=circuits,
            selected_circuit_indices=frozenset(circuits.values()),
        )
        second = layout_block_graph_by_coordinates(
            graph,
            anchors,
            block_circuit_indices=circuits,
            selected_circuit_indices=frozenset(circuits.values()),
        )

        self.assertEqual(first, second)
        west_x = (first.positions[1][0] + first.positions[4][0]) / 2.0
        east_x = (first.positions[2][0] + first.positions[3][0]) / 2.0
        south_y = (first.positions[1][1] + first.positions[2][1]) / 2.0
        north_y = (first.positions[3][1] + first.positions[4][1]) / 2.0
        self.assertLess(west_x, east_x)
        self.assertLess(north_y, south_y)

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

    def test_extreme_coordinate_outlier_is_compressed_to_safe_edge_lengths(self) -> None:
        graph = BlockGraph(
            tuple(_record(block_id) for block_id in range(1, 5)),
            tuple(_edge(index, index + 1, index + 2) for index in range(3)),
        )
        anchors = {
            1: (0.0, 0.0),
            2: (10.0, 0.0),
            3: (20.0, 0.0),
            4: (10_000.0, 0.0),
        }

        layout = layout_block_graph_by_coordinates(graph, anchors)
        lengths = [
            math.dist(layout.positions[index], layout.positions[index + 1])
            for index in range(1, 4)
        ]

        self.assertGreaterEqual(min(lengths), MIN_COORDINATE_EDGE_LENGTH - 0.1)
        self.assertLessEqual(max(lengths), MAX_COORDINATE_EDGE_LENGTH + 0.1)

    def test_coordinate_collisions_use_the_full_visual_envelope(self) -> None:
        graph = BlockGraph(
            (_record(1), _record(2), _record(3)),
            (_edge(0, 1, 2), _edge(1, 2, 3)),
        )
        anchors = {1: (0.0, 0.0), 2: (0.0, 0.0), 3: (0.0, 0.0)}
        envelopes = {
            block_id: BlockNodeEnvelope(72.0, 110.0) for block_id in graph.node_ids
        }

        layout = layout_block_graph_by_coordinates(
            graph,
            anchors,
            node_envelopes=envelopes,
        )

        for left in graph.node_ids:
            for right in graph.node_ids:
                if right <= left:
                    continue
                dx = abs(layout.positions[left][0] - layout.positions[right][0])
                dy = abs(layout.positions[left][1] - layout.positions[right][1])
                self.assertTrue(dx >= 96.0 or dy >= 134.0)

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
