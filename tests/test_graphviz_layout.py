from __future__ import annotations

import math
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from circuit_viewer.block_analysis import BlockRecord
from circuit_viewer.block_graph import (
    BlockGraph,
    BlockGraphEdge,
    BlockGraphEdgeRoute,
    BlockGraphLayout,
    BlockNodeEnvelope,
    build_block_graph_forest,
)
from circuit_viewer.graphviz_layout import (
    DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS,
    GRAPHVIZ_CIRCUIT_SEPARATION_RANGE,
    GRAPHVIZ_VERSION,
    GraphvizDotInput,
    GraphvizEdgeRouting,
    GraphvizLayoutCancelled,
    GraphvizLayoutError,
    GraphvizLayoutSettings,
    bundled_graphviz_dot,
    calculate_graphviz_layout,
    graphviz_layout_cache_key,
    graphviz_layout_settings_from_mapping,
    parse_graphviz_json,
    probe_graphviz_runtime,
    run_graphviz_dot,
    separate_graphviz_circuit_groups,
    serialize_graphviz_dot,
)


def _indices(*values: int) -> np.ndarray:
    result = np.asarray(values, dtype=np.intp)
    result.setflags(write=False)
    return result


def _record(block_id: int, *, source: bool = False) -> BlockRecord:
    return BlockRecord(
        block_id=block_id,
        bar_indices=_indices(block_id - 1),
        segment_indices=_indices(),
        load_indices=_indices(),
        boundary_switch_indices=_indices(),
        boundary_switch_codes=(),
        total_power=None,
        total_length=None,
        contains_source=source,
    )


def _edge(
    switch_index: int,
    start: int,
    end: int,
    *,
    label: str | None = None,
) -> BlockGraphEdge:
    return BlockGraphEdge(
        switch_index=switch_index,
        switch_id=f"CH-{switch_index}",
        switch_code=label or f"COD-{switch_index}",
        state="1",
        start_block_id=start,
        end_block_id=end,
    )


class GraphvizSerializationTests(unittest.TestCase):
    def test_settings_defaults_match_the_original_dot_geometry(self) -> None:
        self.assertEqual(
            DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS,
            GraphvizLayoutSettings(
                circuit_separation_px=120.0,
                node_separation_px=32.0,
                rank_separation_px=56.0,
                edge_routing=GraphvizEdgeRouting.SPLINE,
                equal_rank_spacing=False,
                switches_as_nodes=False,
                tree_edge_weight=8,
                tree_edge_minlen=1,
                crossing_minimization=1.0,
            ),
        )

    def test_invalid_saved_fields_fall_back_independently(self) -> None:
        settings = graphviz_layout_settings_from_mapping(
            {
                "circuit_separation_px": 1001,
                "node_separation_px": "inválido",
                "rank_separation_px": 120,
                "edge_routing": "ortho",
                "equal_rank_spacing": "sim",
                "switches_as_nodes": "sim",
                "tree_edge_weight": 101,
                "tree_edge_minlen": 3,
                "crossing_minimization": float("nan"),
            }
        )

        self.assertEqual(settings.circuit_separation_px, 120.0)
        self.assertEqual(settings.node_separation_px, 32.0)
        self.assertEqual(settings.rank_separation_px, 120.0)
        self.assertEqual(settings.edge_routing, GraphvizEdgeRouting.SPLINE)
        self.assertTrue(settings.equal_rank_spacing)
        self.assertTrue(settings.switches_as_nodes)
        self.assertEqual(settings.tree_edge_weight, 8)
        self.assertEqual(settings.tree_edge_minlen, 3)
        self.assertEqual(settings.crossing_minimization, 1.0)

    def test_settings_reject_values_outside_the_public_ranges(self) -> None:
        self.assertEqual(GRAPHVIZ_CIRCUIT_SEPARATION_RANGE, (0.0, 1000.0))
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(circuit_separation_px=-1.0)
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(node_separation_px=1.0)
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(rank_separation_px=801.0)
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(tree_edge_weight=0)
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(tree_edge_weight=2.5)
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(tree_edge_minlen=11)
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(crossing_minimization=4.1)
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(equal_rank_spacing="false")
        with self.assertRaises(ValueError):
            GraphvizLayoutSettings(switches_as_nodes="true")

    def test_forest_is_shared_and_ignores_parallel_and_self_edges(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2), _record(3)),
            (
                _edge(10, 1, 2),
                _edge(11, 1, 2),
                _edge(12, 2, 3),
                _edge(13, 3, 3),
            ),
        )

        forest = build_block_graph_forest(graph)

        self.assertEqual(forest.root_ids, (1,))
        self.assertEqual(forest.depths, {1: 0, 2: 1, 3: 2})
        self.assertEqual(forest.tree_edge_indices, frozenset({0, 2}))

    def test_dot_is_deterministic_escaped_and_marks_secondary_edges(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2)),
            (
                _edge(10, 1, 2, label='CH "principal"'),
                _edge(11, 1, 2),
                _edge(12, 2, 2),
            ),
        )
        envelopes = {
            1: BlockNodeEnvelope(144.0, 108.0, 56.0),
            2: BlockNodeEnvelope(72.0, 90.0, 56.0),
        }

        first = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            block_circuit_indices={1: 0, 2: 0},
            selected_circuit_indices={0},
        )
        second = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            block_circuit_indices={1: 0, 2: 0},
            selected_circuit_indices={0},
        )

        self.assertEqual(first, second)
        self.assertIn('width=2.000000000, height=1.500000000', first.source)
        self.assertIn('label="CH \\"principal\\""', first.source)
        self.assertIn('id="switch_10"', first.source)
        self.assertIn('id="switch_11", label="COD-11", constraint=false', first.source)
        self.assertIn('id="switch_12", label="COD-12", constraint=false', first.source)
        self.assertEqual(first.tree_edge_indices, frozenset({0}))
        self.assertEqual(
            graphviz_layout_cache_key(first, GRAPHVIZ_VERSION),
            graphviz_layout_cache_key(second, GRAPHVIZ_VERSION),
        )

    def test_single_circuit_places_direct_external_block_in_same_group(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2), _record(3)),
            (_edge(0, 1, 2), _edge(1, 2, 3)),
        )
        envelopes = {
            block_id: BlockNodeEnvelope()
            for block_id in graph.node_ids
        }

        dot_input = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            block_circuit_indices={1: 0, 2: 0, 3: 1},
            selected_circuit_indices={0},
        )

        self.assertEqual(dot_input.source.count("subgraph circuit_"), 1)
        self.assertEqual(dot_input.layout_groups, ((1, 2, 3),))
        self.assertIn('group="circuit_0"', dot_input.source)
        self.assertIn('"n_2" -> "n_3"', dot_input.source)
        self.assertNotIn(
            'id="switch_1", label="COD-1", constraint=false',
            dot_input.source,
        )

    def test_custom_settings_are_serialized_and_change_the_cache_key(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2)),
            (_edge(10, 1, 2),),
        )
        envelopes = {
            block_id: BlockNodeEnvelope() for block_id in graph.node_ids
        }
        settings = GraphvizLayoutSettings(
            circuit_separation_px=240.0,
            node_separation_px=72.0,
            rank_separation_px=144.0,
            edge_routing=GraphvizEdgeRouting.POLYLINE,
            equal_rank_spacing=True,
            tree_edge_weight=12,
            tree_edge_minlen=2,
            crossing_minimization=2.5,
        )

        original = serialize_graphviz_dot(graph, node_envelopes=envelopes)
        customized = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            settings=settings,
        )

        self.assertIn("splines=polyline", customized.source)
        self.assertIn("nodesep=1.000000000", customized.source)
        self.assertIn('ranksep="2.000000000 equally"', customized.source)
        self.assertIn("mclimit=2.500000000", customized.source)
        self.assertIn("weight=12, minlen=2", customized.source)
        self.assertNotEqual(
            graphviz_layout_cache_key(original, GRAPHVIZ_VERSION),
            graphviz_layout_cache_key(customized, GRAPHVIZ_VERSION),
        )

    def test_switches_as_nodes_reserve_label_boxes_and_explicit_layers(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2), _record(3)),
            (
                _edge(10, 1, 2, label="CHAVE PRINCIPAL"),
                _edge(11, 1, 2),
                _edge(12, 2, 3),
                _edge(13, 3, 3),
            ),
        )
        envelopes = {
            block_id: BlockNodeEnvelope() for block_id in graph.node_ids
        }
        label_sizes = {
            10: (80.0, 20.0),
            11: (60.0, 20.0),
            12: (60.0, 20.0),
            13: (60.0, 20.0),
        }

        direct = serialize_graphviz_dot(graph, node_envelopes=envelopes)
        auxiliary = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            edge_label_sizes=label_sizes,
            settings=GraphvizLayoutSettings(switches_as_nodes=True),
        )

        self.assertTrue(auxiliary.switches_as_nodes)
        self.assertIn('"s_10" [id="switch_node_10"', auxiliary.source)
        self.assertIn("width=1.222222222", auxiliary.source)
        self.assertIn("height=0.388888889", auxiliary.source)
        self.assertIn('id="switch_10_a", weight=8, minlen=1', auxiliary.source)
        self.assertIn('id="switch_10_b", weight=8, minlen=1', auxiliary.source)
        self.assertIn('id="switch_11_a", constraint=false', auxiliary.source)
        self.assertIn('id="switch_13_b", constraint=false', auxiliary.source)
        self.assertIn("subgraph rank_layer_1", auxiliary.source)
        self.assertIn('"n_1" -> "s_10"', auxiliary.source)
        self.assertIn('"s_10" -> "n_2"', auxiliary.source)
        self.assertNotIn('label="CHAVE PRINCIPAL"', auxiliary.source)
        self.assertNotEqual(
            graphviz_layout_cache_key(direct, GRAPHVIZ_VERSION),
            graphviz_layout_cache_key(auxiliary, GRAPHVIZ_VERSION),
        )

    def test_switches_as_nodes_require_every_label_envelope(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2)),
            (_edge(10, 1, 2),),
        )
        envelopes = {
            block_id: BlockNodeEnvelope() for block_id in graph.node_ids
        }

        with self.assertRaisesRegex(GraphvizLayoutError, "etiquetas"):
            serialize_graphviz_dot(
                graph,
                node_envelopes=envelopes,
                settings=GraphvizLayoutSettings(switches_as_nodes=True),
            )

    def test_circuit_separation_changes_cache_without_changing_dot(self) -> None:
        graph = BlockGraph((_record(1), _record(2)), ())
        envelopes = {block_id: BlockNodeEnvelope() for block_id in graph.node_ids}

        compact = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            block_circuit_indices={1: 0, 2: 1},
            settings=GraphvizLayoutSettings(circuit_separation_px=0.0),
        )
        separated = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            block_circuit_indices={1: 0, 2: 1},
            settings=GraphvizLayoutSettings(circuit_separation_px=240.0),
        )

        self.assertEqual(compact.source, separated.source)
        self.assertNotEqual(
            graphviz_layout_cache_key(compact, GRAPHVIZ_VERSION),
            graphviz_layout_cache_key(separated, GRAPHVIZ_VERSION),
        )


class GraphvizCircuitSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = BlockGraph(
            (_record(1), _record(2), _record(3), _record(4)),
            (
                _edge(10, 1, 2),
                _edge(11, 3, 4),
                _edge(12, 1, 3),
            ),
        )
        self.envelopes = {
            block_id: BlockNodeEnvelope(40.0, 40.0, 40.0)
            for block_id in self.graph.node_ids
        }
        self.layout = BlockGraphLayout(
            positions={
                1: (-40.0, 0.0),
                2: (-40.0, 100.0),
                3: (40.0, 0.0),
                4: (40.0, 100.0),
            },
            depths={1: 0, 2: 1, 3: 0, 4: 1},
            root_ids=(1, 3),
            tree_edge_indices=frozenset({0, 1}),
            edge_routes={
                10: BlockGraphEdgeRoute(((-40.0, 20.0), (-40.0, 80.0))),
                11: BlockGraphEdgeRoute(((40.0, 20.0), (40.0, 80.0))),
                12: BlockGraphEdgeRoute(
                    ((-20.0, 0.0), (0.0, -20.0), (0.0, 20.0), (20.0, 0.0)),
                    cubic=True,
                ),
            },
            edge_label_positions={
                10: (-40.0, 50.0),
                11: (40.0, 50.0),
                12: (0.0, 0.0),
            },
        )

    @staticmethod
    def _node_bounds(
        layout: BlockGraphLayout,
        block_ids: tuple[int, ...],
    ) -> tuple[float, float]:
        centers = [layout.positions[block_id][0] for block_id in block_ids]
        return min(centers) - 20.0, max(centers) + 20.0

    def test_zero_and_single_group_preserve_original_geometry(self) -> None:
        disabled = separate_graphviz_circuit_groups(
            self.layout,
            self.graph,
            ((1, 2), (3, 4)),
            self.envelopes,
            minimum_separation=0.0,
        )
        single = separate_graphviz_circuit_groups(
            self.layout,
            self.graph,
            ((1, 2, 3, 4),),
            self.envelopes,
            minimum_separation=120.0,
        )

        self.assertIs(disabled, self.layout)
        self.assertIs(single, self.layout)

    def test_groups_move_rigidly_and_keep_the_requested_gap(self) -> None:
        separated = separate_graphviz_circuit_groups(
            self.layout,
            self.graph,
            ((1, 2), (3, 4)),
            self.envelopes,
            minimum_separation=120.0,
            edge_label_sizes={10: (24.0, 12.0), 11: (24.0, 12.0)},
        )

        first = self._node_bounds(separated, (1, 2))
        second = self._node_bounds(separated, (3, 4))
        self.assertAlmostEqual(second[0] - first[1], 120.0)
        self.assertEqual(
            separated.positions[2][0] - separated.positions[1][0],
            self.layout.positions[2][0] - self.layout.positions[1][0],
        )
        self.assertEqual(
            separated.positions[4][0] - separated.positions[3][0],
            self.layout.positions[4][0] - self.layout.positions[3][0],
        )
        self.assertEqual(
            {point[1] for point in separated.positions.values()},
            {0.0, 100.0},
        )
        self.assertTrue(separated.edge_routes[12].cubic)
        for block_id, point_index in ((1, 0), (3, -1)):
            center = separated.positions[block_id]
            endpoint = separated.edge_routes[12].points[point_index]
            self.assertAlmostEqual(math.dist(center, endpoint), 20.0)

    def test_vertically_distant_groups_still_receive_horizontal_gap(self) -> None:
        vertical_layout = BlockGraphLayout(
            positions={1: (0.0, 0.0), 2: (0.0, 300.0)},
            depths={1: 0, 2: 1},
            root_ids=(1,),
            tree_edge_indices=frozenset(),
        )
        graph = BlockGraph((_record(1), _record(2)), ())
        envelopes = {1: self.envelopes[1], 2: self.envelopes[2]}

        separated = separate_graphviz_circuit_groups(
            vertical_layout,
            graph,
            ((1,), (2,)),
            envelopes,
            minimum_separation=120.0,
        )

        first = self._node_bounds(separated, (1,))
        second = self._node_bounds(separated, (2,))
        self.assertAlmostEqual(second[0] - first[1], 120.0)
        self.assertEqual(separated.positions[1][1], 0.0)
        self.assertEqual(separated.positions[2][1], 300.0)

    def test_four_groups_all_participate_in_the_horizontal_spacing(self) -> None:
        graph = BlockGraph(tuple(_record(block_id) for block_id in range(1, 5)), ())
        envelopes = {
            block_id: BlockNodeEnvelope(40.0, 40.0, 40.0)
            for block_id in graph.node_ids
        }
        layout = BlockGraphLayout(
            positions={
                block_id: (0.0, block_id * 100.0)
                for block_id in graph.node_ids
            },
            depths={block_id: 0 for block_id in graph.node_ids},
            root_ids=graph.node_ids,
            tree_edge_indices=frozenset(),
        )

        separated = separate_graphviz_circuit_groups(
            layout,
            graph,
            ((1,), (2,), (3,), (4,)),
            envelopes,
            minimum_separation=120.0,
        )

        ordered = sorted(
            separated.positions,
            key=lambda block_id: separated.positions[block_id][0],
        )
        for previous, current in zip(ordered, ordered[1:]):
            previous_right = separated.positions[previous][0] + 20.0
            current_left = separated.positions[current][0] - 20.0
            self.assertAlmostEqual(current_left - previous_right, 120.0)
        self.assertNotEqual(separated.positions[2][0], layout.positions[2][0])
        self.assertNotEqual(separated.positions[3][0], layout.positions[3][0])

    def test_invalid_or_incomplete_groups_are_rejected(self) -> None:
        with self.assertRaises(GraphvizLayoutError):
            separate_graphviz_circuit_groups(
                self.layout,
                self.graph,
                ((1, 2), (2, 3, 4)),
                self.envelopes,
                minimum_separation=120.0,
            )


class GraphvizParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = BlockGraph(
            (_record(1, source=True), _record(2)),
            (_edge(7, 1, 2),),
        )
        self.envelopes = {
            1: BlockNodeEnvelope(72.0, 90.0, 56.0),
            2: BlockNodeEnvelope(72.0, 90.0, 56.0),
        }
        self.dot_input = GraphvizDotInput(
            "digraph G {}",
            {1: 0, 2: 1},
            (1,),
            frozenset({0}),
        )

    def _payload(self, *, include_label: bool = True) -> dict[str, object]:
        edge: dict[str, object] = {
            "id": "switch_7",
            "_draw_": [
                {
                    "op": "b",
                    "points": [[20, 80], [30, 65], [70, 35], [80, 20]],
                }
            ],
        }
        if include_label:
            edge["lp"] = "60,55"
        return {
            "bb": "0,0,100,100",
            "objects": [
                {"name": "n_1", "pos": "20,80"},
                {"name": "n_2", "pos": "80,20"},
            ],
            "edges": [edge],
        }

    def test_parser_inverts_y_and_preserves_cubic_spline_and_label(self) -> None:
        layout = parse_graphviz_json(
            self._payload(),
            self.graph,
            self.dot_input,
            self.envelopes,
        )

        self.assertEqual(layout.positions, {1: (-30.0, -30.0), 2: (30.0, 30.0)})
        route = layout.edge_routes[7]
        self.assertTrue(route.cubic)
        self.assertEqual(len(route.points), 4)
        self.assertEqual(layout.edge_label_positions[7], (10.0, -5.0))
        self.assertTrue(
            all(math.isfinite(value) for point in route.points for value in point)
        )

    def test_missing_label_anchor_uses_spline_midpoint(self) -> None:
        layout = parse_graphviz_json(
            self._payload(include_label=False),
            self.graph,
            self.dot_input,
            self.envelopes,
        )

        self.assertIn(7, layout.edge_label_positions)
        self.assertTrue(
            all(math.isfinite(value) for value in layout.edge_label_positions[7])
        )

    def test_incomplete_or_invalid_geometry_is_rejected(self) -> None:
        payload = self._payload()
        payload["objects"] = payload["objects"][:1]
        with self.assertRaises(GraphvizLayoutError):
            parse_graphviz_json(
                payload,
                self.graph,
                self.dot_input,
                self.envelopes,
            )

    def test_invalid_json_and_invalid_draw_list_are_rejected(self) -> None:
        with self.assertRaises(GraphvizLayoutError):
            parse_graphviz_json(
                "{",
                self.graph,
                self.dot_input,
                self.envelopes,
            )
        payload = self._payload()
        payload["edges"][0]["_draw_"] = None
        with self.assertRaises(GraphvizLayoutError):
            parse_graphviz_json(
                payload,
                self.graph,
                self.dot_input,
                self.envelopes,
            )

    def test_auxiliary_switch_node_becomes_label_anchor_and_one_route(self) -> None:
        dot_input = GraphvizDotInput(
            "digraph G {}",
            {1: 0, 2: 1},
            (1,),
            frozenset({0}),
            switches_as_nodes=True,
        )
        payload = {
            "bb": "0,0,100,200",
            "objects": [
                {"_gvid": 0, "name": "n_1", "pos": "50,180"},
                {"_gvid": 1, "name": "s_7", "pos": "50,100"},
                {"_gvid": 2, "name": "n_2", "pos": "50,20"},
            ],
            "edges": [
                {
                    "id": "switch_7_a",
                    "tail": 0,
                    "head": 1,
                    "_draw_": [{
                        "op": "b",
                        "points": [[50, 150], [50, 140], [50, 120], [50, 114]],
                    }],
                },
                {
                    "id": "switch_7_b",
                    "tail": 1,
                    "head": 2,
                    "_draw_": [{
                        "op": "b",
                        "points": [[50, 86], [50, 70], [50, 50], [50, 48]],
                    }],
                },
            ],
        }

        layout = parse_graphviz_json(
            payload,
            self.graph,
            dot_input,
            self.envelopes,
        )

        self.assertEqual(layout.edge_label_positions[7], (0.0, 0.0))
        self.assertEqual(set(layout.edge_routes), {7})
        self.assertTrue(layout.edge_routes[7].cubic)
        self.assertEqual(len(layout.edge_routes[7].points), 13)
        self.assertEqual(layout.edge_routes[7].points[6], (0.0, 0.0))

        payload["edges"] = payload["edges"][:1]
        with self.assertRaisesRegex(GraphvizLayoutError, "duas metades"):
            parse_graphviz_json(
                payload,
                self.graph,
                dot_input,
                self.envelopes,
            )

    def test_auxiliary_route_follows_the_original_application_orientation(self) -> None:
        graph = BlockGraph(
            (_record(1, source=True), _record(2)),
            (_edge(7, 2, 1),),
        )
        dot_input = GraphvizDotInput(
            "digraph G {}",
            {1: 0, 2: 1},
            (1,),
            frozenset({0}),
            switches_as_nodes=True,
        )
        payload = {
            "bb": "0,0,100,200",
            "objects": [
                {"_gvid": 0, "name": "n_1", "pos": "50,180"},
                {"_gvid": 1, "name": "s_7", "pos": "50,100"},
                {"_gvid": 2, "name": "n_2", "pos": "50,20"},
            ],
            "edges": [
                {
                    "id": "switch_7_a",
                    "tail": 0,
                    "head": 1,
                    "_draw_": [{
                        "op": "b",
                        "points": [[50, 150], [50, 140], [50, 120], [50, 114]],
                    }],
                },
                {
                    "id": "switch_7_b",
                    "tail": 1,
                    "head": 2,
                    "_draw_": [{
                        "op": "b",
                        "points": [[50, 86], [50, 70], [50, 50], [50, 48]],
                    }],
                },
            ],
        }

        layout = parse_graphviz_json(
            payload,
            graph,
            dot_input,
            self.envelopes,
        )

        route = layout.edge_routes[7]
        self.assertGreater(route.points[0][1], route.points[-1][1])
        self.assertEqual(layout.edge_label_positions[7], (0.0, 0.0))


class GraphvizProcessTests(unittest.TestCase):
    def test_nonzero_exit_reports_stderr(self) -> None:
        process = Mock()
        process.returncode = 7
        process.communicate.return_value = (b"", "falha detalhada".encode())

        with patch(
            "circuit_viewer.graphviz_layout.subprocess.Popen",
            return_value=process,
        ) as popen, patch(
            "circuit_viewer.graphviz_layout.platform.system",
            return_value="Windows",
        ), self.assertRaisesRegex(
            GraphvizLayoutError,
            "falha detalhada",
        ):
            run_graphviz_dot(Path("dot.exe"), "digraph G {}")

        self.assertEqual(
            popen.call_args.args[0],
            ["dot.exe", "-Kdot", "-Tjson"],
        )

    def test_timeout_kills_process_and_reports_configured_limit(self) -> None:
        process = Mock()
        process.communicate.return_value = (b"", b"ainda executando")

        with patch(
            "circuit_viewer.graphviz_layout.subprocess.Popen",
            return_value=process,
        ), patch(
            "circuit_viewer.graphviz_layout.platform.system",
            return_value="Windows",
        ), patch(
            "circuit_viewer.graphviz_layout.time.monotonic",
            side_effect=(10.0, 10.6),
        ), self.assertRaisesRegex(GraphvizLayoutError, "0.5 segundos"):
            run_graphviz_dot(Path("dot.exe"), "digraph G {}", timeout=0.5)

        process.kill.assert_called_once_with()


class GraphvizRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        probe_graphviz_runtime.cache_clear()

    def test_unsupported_platform_and_missing_runtime_explain_unavailability(self) -> None:
        probe_graphviz_runtime.cache_clear()
        with patch(
            "circuit_viewer.graphviz_layout.platform.system",
            return_value="Linux",
        ):
            unsupported = probe_graphviz_runtime()
        missing = probe_graphviz_runtime(
            Path("runtime-que-nao-existe/dot.exe"),
            require_supported_platform=False,
        )

        self.assertFalse(unsupported.available)
        self.assertIn("Windows 64 bits", unsupported.reason)
        self.assertFalse(missing.available)
        self.assertIn("não foi encontrado", missing.reason)

    def test_incompatible_runtime_version_is_rejected(self) -> None:
        completed = Mock(
            returncode=0,
            stderr=b"dot - graphviz version 99.0.0 (fake)",
            stdout=b"",
        )
        with patch(
            "circuit_viewer.graphviz_layout.subprocess.run",
            return_value=completed,
        ), patch(
            "circuit_viewer.graphviz_layout.platform.system",
            return_value="Windows",
        ):
            status = probe_graphviz_runtime(
                str(bundled_graphviz_dot()),
                require_supported_platform=False,
            )

        self.assertFalse(status.available)
        self.assertEqual(status.version, "99.0.0")
        self.assertIn(GRAPHVIZ_VERSION, status.reason)


class BundledGraphvizSmokeTests(unittest.TestCase):
    def test_bundled_dot_returns_complete_finite_geometry(self) -> None:
        status = probe_graphviz_runtime()
        if not status.available:
            self.skipTest(status.reason)
        graph = BlockGraph(
            (_record(1, source=True), _record(2), _record(3)),
            (
                _edge(0, 1, 2),
                _edge(1, 1, 2),
                _edge(2, 2, 3),
                _edge(3, 3, 3),
            ),
        )
        envelopes = {
            block_id: BlockNodeEnvelope(138.0, 110.0, 56.0)
            for block_id in graph.node_ids
        }
        dot_input = serialize_graphviz_dot(graph, node_envelopes=envelopes)

        first = calculate_graphviz_layout(
            status.executable,
            dot_input,
            graph,
            envelopes,
        )
        second = calculate_graphviz_layout(
            status.executable,
            dot_input,
            graph,
            envelopes,
        )

        self.assertEqual(first, second)
        self.assertEqual(set(first.positions), set(graph.node_ids))
        self.assertEqual(set(first.edge_routes), {0, 1, 2, 3})
        self.assertEqual(set(first.edge_label_positions), {0, 1, 2, 3})

    def test_pre_cancelled_process_request_does_not_leave_dot_running(self) -> None:
        status = probe_graphviz_runtime()
        if not status.available:
            self.skipTest(status.reason)
        graph = BlockGraph((_record(1),), ())
        envelopes = {1: BlockNodeEnvelope()}
        dot_input = serialize_graphviz_dot(graph, node_envelopes=envelopes)
        cancelled = threading.Event()
        cancelled.set()

        with self.assertRaises(GraphvizLayoutCancelled):
            calculate_graphviz_layout(
                status.executable,
                dot_input,
                graph,
                envelopes,
                cancel_event=cancelled,
            )

    def test_supported_edge_routing_modes_return_complete_geometry(self) -> None:
        status = probe_graphviz_runtime()
        if not status.available:
            self.skipTest(status.reason)
        graph = BlockGraph(
            (_record(1, source=True), _record(2), _record(3)),
            (
                _edge(0, 1, 2),
                _edge(1, 1, 2),
                _edge(2, 2, 3),
                _edge(3, 3, 3),
            ),
        )
        envelopes = {
            block_id: BlockNodeEnvelope(138.0, 110.0, 56.0)
            for block_id in graph.node_ids
        }

        for routing in GraphvizEdgeRouting:
            with self.subTest(routing=routing.value):
                dot_input = serialize_graphviz_dot(
                    graph,
                    node_envelopes=envelopes,
                    block_circuit_indices={1: 0, 2: 0, 3: 1},
                    selected_circuit_indices={0, 1},
                    settings=GraphvizLayoutSettings(
                        node_separation_px=48.0,
                        rank_separation_px=90.0,
                        edge_routing=routing,
                        equal_rank_spacing=True,
                        tree_edge_weight=12,
                        tree_edge_minlen=2,
                        crossing_minimization=2.0,
                    ),
                )
                layout = calculate_graphviz_layout(
                    status.executable,
                    dot_input,
                    graph,
                    envelopes,
                )

                self.assertEqual(set(layout.positions), set(graph.node_ids))
                self.assertEqual(set(layout.edge_routes), {0, 1, 2, 3})
                self.assertEqual(
                    set(layout.edge_label_positions),
                    {0, 1, 2, 3},
                )

    def test_bundled_dot_supports_auxiliary_switch_nodes(self) -> None:
        status = probe_graphviz_runtime()
        if not status.available:
            self.skipTest(status.reason)
        graph = BlockGraph(
            (_record(1, source=True), _record(2), _record(3)),
            (
                _edge(0, 1, 2),
                _edge(1, 1, 2),
                _edge(2, 2, 3),
                _edge(3, 3, 3),
            ),
        )
        envelopes = {
            block_id: BlockNodeEnvelope(138.0, 110.0, 56.0)
            for block_id in graph.node_ids
        }
        label_sizes = {edge.switch_index: (70.0, 22.0) for edge in graph.edges}
        dot_input = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            edge_label_sizes=label_sizes,
            settings=GraphvizLayoutSettings(switches_as_nodes=True),
        )

        layout = calculate_graphviz_layout(
            status.executable,
            dot_input,
            graph,
            envelopes,
            edge_label_sizes=label_sizes,
        )

        self.assertEqual(set(layout.positions), set(graph.node_ids))
        self.assertEqual(set(layout.edge_routes), {0, 1, 2, 3})
        self.assertEqual(set(layout.edge_label_positions), {0, 1, 2, 3})
        for switch_index, route in layout.edge_routes.items():
            self.assertIn(layout.edge_label_positions[switch_index], route.points)
            self.assertTrue(
                all(math.isfinite(value) for point in route.points for value in point)
            )
        self.assertLess(
            layout.positions[1][1],
            layout.edge_label_positions[0][1],
        )
        self.assertLess(
            layout.edge_label_positions[0][1],
            layout.positions[2][1],
        )
        self.assertLess(
            layout.positions[1][1],
            layout.edge_label_positions[1][1],
        )
        self.assertLess(
            layout.edge_label_positions[1][1],
            layout.positions[2][1],
        )
        self.assertGreater(
            layout.edge_label_positions[3][1],
            layout.positions[3][1],
        )

    def test_auxiliary_switch_nodes_support_every_routing_mode(self) -> None:
        status = probe_graphviz_runtime()
        if not status.available:
            self.skipTest(status.reason)
        graph = BlockGraph(
            (_record(1, source=True), _record(2)),
            (_edge(0, 1, 2),),
        )
        envelopes = {
            block_id: BlockNodeEnvelope(100.0, 90.0, 56.0)
            for block_id in graph.node_ids
        }
        label_sizes = {0: (70.0, 22.0)}

        for routing in GraphvizEdgeRouting:
            with self.subTest(routing=routing.value):
                dot_input = serialize_graphviz_dot(
                    graph,
                    node_envelopes=envelopes,
                    edge_label_sizes=label_sizes,
                    settings=GraphvizLayoutSettings(
                        switches_as_nodes=True,
                        edge_routing=routing,
                    ),
                )
                layout = calculate_graphviz_layout(
                    status.executable,
                    dot_input,
                    graph,
                    envelopes,
                    edge_label_sizes=label_sizes,
                )

                route = layout.edge_routes[0]
                self.assertIn(layout.edge_label_positions[0], route.points)
                self.assertGreaterEqual(len(route.points), 3)

    def test_auxiliary_nodes_keep_four_circuits_separated(self) -> None:
        status = probe_graphviz_runtime()
        if not status.available:
            self.skipTest(status.reason)
        graph = BlockGraph(
            tuple(
                _record(block_id, source=block_id % 2 == 1)
                for block_id in range(1, 9)
            ),
            (
                _edge(0, 1, 2),
                _edge(1, 3, 4),
                _edge(2, 5, 6),
                _edge(3, 7, 8),
                _edge(4, 2, 3),
                _edge(5, 4, 5),
                _edge(6, 6, 7),
            ),
        )
        block_circuits = {
            block_id: (block_id - 1) // 2 for block_id in graph.node_ids
        }
        envelopes = {
            block_id: BlockNodeEnvelope(72.0, 90.0, 56.0)
            for block_id in graph.node_ids
        }
        label_sizes = {edge.switch_index: (70.0, 22.0) for edge in graph.edges}
        dot_input = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            edge_label_sizes=label_sizes,
            block_circuit_indices=block_circuits,
            selected_circuit_indices={0, 1, 2, 3},
            settings=GraphvizLayoutSettings(
                switches_as_nodes=True,
                circuit_separation_px=120.0,
            ),
        )

        layout = calculate_graphviz_layout(
            status.executable,
            dot_input,
            graph,
            envelopes,
            edge_label_sizes=label_sizes,
        )

        bounds = []
        for circuit_index in range(4):
            node_ids = tuple(
                block_id
                for block_id, owner in block_circuits.items()
                if owner == circuit_index
            )
            left = min(layout.positions[block_id][0] - 36.0 for block_id in node_ids)
            right = max(layout.positions[block_id][0] + 36.0 for block_id in node_ids)
            bounds.append((left, right))
        bounds.sort()
        for previous, current in zip(bounds, bounds[1:]):
            self.assertGreaterEqual(current[0] - previous[1], 120.0 - 1.0e-6)
        for switch_index in (4, 5, 6):
            self.assertIn(
                layout.edge_label_positions[switch_index],
                layout.edge_routes[switch_index].points,
            )

    def test_bundled_dot_respects_the_visual_gap_between_circuits(self) -> None:
        status = probe_graphviz_runtime()
        if not status.available:
            self.skipTest(status.reason)
        graph = BlockGraph(
            (_record(1), _record(2), _record(3), _record(4)),
            (
                _edge(0, 1, 2),
                _edge(1, 3, 4),
                _edge(2, 2, 3),
            ),
        )
        envelopes = {
            block_id: BlockNodeEnvelope(72.0, 90.0, 56.0)
            for block_id in graph.node_ids
        }
        dot_input = serialize_graphviz_dot(
            graph,
            node_envelopes=envelopes,
            block_circuit_indices={1: 0, 2: 0, 3: 1, 4: 1},
            selected_circuit_indices={0, 1},
            settings=GraphvizLayoutSettings(circuit_separation_px=120.0),
        )

        layout = calculate_graphviz_layout(
            status.executable,
            dot_input,
            graph,
            envelopes,
        )

        def bounds(block_ids: tuple[int, ...]) -> tuple[float, float, float, float]:
            positions = [layout.positions[block_id] for block_id in block_ids]
            return (
                min(point[0] for point in positions) - 36.0,
                min(point[1] for point in positions) - 45.0,
                max(point[0] for point in positions) + 36.0,
                max(point[1] for point in positions) + 45.0,
            )

        first = bounds((1, 2))
        second = bounds((3, 4))
        horizontal_gap = max(first[0] - second[2], second[0] - first[2], 0.0)
        vertical_gap = max(first[1] - second[3], second[1] - first[3], 0.0)
        self.assertGreaterEqual(max(horizontal_gap, vertical_gap), 120.0 - 1.0e-6)


if __name__ == "__main__":
    unittest.main()
