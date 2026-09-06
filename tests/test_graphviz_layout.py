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
    BlockNodeEnvelope,
    build_block_graph_forest,
)
from circuit_viewer.graphviz_layout import (
    GRAPHVIZ_VERSION,
    GraphvizDotInput,
    GraphvizLayoutCancelled,
    GraphvizLayoutError,
    bundled_graphviz_dot,
    calculate_graphviz_layout,
    graphviz_layout_cache_key,
    parse_graphviz_json,
    probe_graphviz_runtime,
    run_graphviz_dot,
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
        self.assertIn('group="circuit_0"', dot_input.source)
        self.assertIn('"n_2" -> "n_3"', dot_input.source)
        self.assertNotIn(
            'id="switch_1", label="COD-1", constraint=false',
            dot_input.source,
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


if __name__ == "__main__":
    unittest.main()
