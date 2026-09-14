"""O sentido elétrico da chave não pode deformar a spline calculada pelo dot."""
from dataclasses import replace

import pytest

from circuit_viewer.block_graph import BlockGraph, block_node_envelopes
from circuit_viewer.graphviz_layout import (
    GraphvizDotInput, GraphvizLayoutError, GraphvizLayoutSettings, GraphvizEdgeRouting,
    bundled_graphviz_dot, parse_graphviz_json, run_graphviz_dot, serialize_graphviz_dot,
    separate_graphviz_circuit_groups,
)
from tests.test_graphviz_layout import _record, _edge


def example(operation="b"):
    graph = BlockGraph((_record(1), _record(2, source=True)), (_edge(7, 1, 2),))
    dot = GraphvizDotInput("digraph G {}", {1: 1, 2: 0}, (2,), frozenset({0}))
    points = [[0, 180], [30, 170], [35, 140], [40, 100], [45, 60], [60, 30], [100, 20]]
    payload = {
        "bb": "0,0,100,200",
        "objects": [{"_gvid": 14, "name": "n_1", "pos": "100,0"},
                    {"_gvid": 29, "name": "n_2", "pos": "0,200"}],
        "edges": [{"id": "switch_7", "tail": 29, "head": 14,
                   "lp": "60,100", "_draw_": [{"op": operation, "points": points}]}],
    }
    return graph, dot, payload, block_node_envelopes(graph.nodes)


@pytest.mark.parametrize("operation", ["b", "L"])
def test_reversed_direction_reverses_all_controls_before_clipping(operation):
    graph, dot, payload, envelopes = example(operation)
    layout = parse_graphviz_json(payload, graph, dot, envelopes)
    controls = payload["edges"][0]["_draw_"][0]["points"]
    expected = tuple((x-50, 100-y) for x, y in reversed(controls))
    route = layout.edge_routes[7]
    assert route.points[1:-1] == expected[1:-1]
    assert route.cubic == (operation == "b")
    assert layout.edge_label_positions[7] == (10, 0)
    assert graph.edges[0].start_block_id == 1
    forward = BlockGraph(graph.nodes, (replace(graph.edges[0], start_block_id=2, end_block_id=1),))
    natural = parse_graphviz_json(payload, forward, dot, envelopes)
    assert route.points == tuple(reversed(natural.edge_routes[7].points))
    assert layout.positions == natural.positions
    assert layout.edge_label_positions == natural.edge_label_positions


def test_circuit_separation_preserves_reversed_geometry():
    graph, dot, payload, envelopes = example()
    reverse = BlockGraph(graph.nodes, (replace(graph.edges[0], start_block_id=2, end_block_id=1),))
    layouts = [separate_graphviz_circuit_groups(
        parse_graphviz_json(payload, g, dot, envelopes), g, ((1,), (2,)), envelopes,
        minimum_separation=350.0) for g in (graph, reverse)]
    assert layouts[0].positions == layouts[1].positions
    for point, other in zip(layouts[0].edge_routes[7].points, reversed(layouts[1].edge_routes[7].points)):
        assert point == pytest.approx(other)
    assert layouts[0].edge_label_positions[7] == pytest.approx(layouts[1].edge_label_positions[7])


@pytest.mark.parametrize("problem", ["missing_gvid", "duplicate_gvid", "missing_tail", "missing_head", "unknown_tail", "wrong_head"])
def test_endpoint_metadata_is_required_and_must_match_the_equipment(problem):
    graph, dot, payload, envelopes = example()
    if problem == "missing_gvid":
        del payload["objects"][0]["_gvid"]
    elif problem == "duplicate_gvid":
        payload["objects"][1]["_gvid"] = 14
    elif problem.startswith("missing_"):
        del payload["edges"][0][problem.removeprefix("missing_")]
    elif problem == "unknown_tail":
        payload["edges"][0]["tail"] = 999
    else:
        payload["edges"][0]["head"] = 29
    with pytest.raises(GraphvizLayoutError):
        parse_graphviz_json(payload, graph, dot, envelopes)


def test_self_loop_keeps_its_control_order():
    graph, dot, payload, envelopes = example()
    graph = BlockGraph(graph.nodes, (replace(graph.edges[0], start_block_id=2, end_block_id=2),))
    payload["edges"][0]["head"] = 29
    layout = parse_graphviz_json(payload, graph, dot, envelopes)
    controls = payload["edges"][0]["_draw_"][0]["points"]
    assert layout.edge_routes[7].points[1:-1] == tuple((x-50, 100-y) for x, y in controls[1:-1])


@pytest.mark.parametrize("routing", list(GraphvizEdgeRouting))
@pytest.mark.parametrize("auxiliary", [False, True])
def test_real_fan_root_greater_than_children_keeps_geometry_in_both_directions(routing, auxiliary):
    nodes = tuple(_record(i, source=i==10) for i in range(1, 11))
    graph = BlockGraph(nodes, tuple(_edge(i, i, 10) for i in range(1, 10)) +
                       (_edge(20, 1, 10), _edge(21, 10, 10)))
    envelopes = block_node_envelopes(nodes)
    sizes = {e.switch_index: (64.0, 24.0) for e in graph.edges}
    settings = GraphvizLayoutSettings(edge_routing=routing, switches_as_nodes=auxiliary)
    dot = serialize_graphviz_dot(graph, node_envelopes=envelopes, edge_label_sizes=sizes, settings=settings)
    payload = run_graphviz_dot(bundled_graphviz_dot(), dot.source)
    reversed_graph = BlockGraph(nodes, tuple(replace(e, start_block_id=e.end_block_id,
                                                   end_block_id=e.start_block_id) for e in graph.edges))
    normal = parse_graphviz_json(payload, graph, dot, envelopes)
    reversed_layout = parse_graphviz_json(payload, reversed_graph, dot, envelopes)
    assert normal.positions == reversed_layout.positions
    assert normal.edge_label_positions == reversed_layout.edge_label_positions
    for edge in graph.edges:
        actual = normal.edge_routes[edge.switch_index]
        other = reversed_layout.edge_routes[edge.switch_index]
        expected = other.points if edge.start_block_id == edge.end_block_id else tuple(reversed(other.points))
        assert actual.cubic == other.cubic
        assert len(actual.points) == len(expected)
        for point, reference in zip(actual.points, expected):
            assert point == pytest.approx(reference)


def test_wrong_endpoint_error_uses_existing_ui_fallback_without_losing_edges():
    from tests.test_block_graph_neighbors import graph_window, wait_ui
    from circuit_viewer.block_graph import BlockGraphLayoutMode
    from unittest.mock import patch
    window = graph_window()
    try:
        window._circuit_selection_changed((0, 1), False)
        expected = tuple(e.switch_index for e in window.view.graph.edges)
        with patch('circuit_viewer.block_graph_window.calculate_graphviz_layout',
                   side_effect=GraphvizLayoutError('A chave Graphviz não liga os blocos esperados.')):
            window.set_layout_mode(BlockGraphLayoutMode.GRAPHVIZ_DOT)
            wait_ui(lambda: not window._graphviz_jobs)
        assert window.layout_mode == BlockGraphLayoutMode.TREE
        assert tuple(e.switch_index for e in window.view.graph.edges) == expected
        assert 'blocos esperados' in window.graphviz_status_label.toolTip()
    finally:
        window.close()
