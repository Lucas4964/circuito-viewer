"""Expansão progressiva do grafo, compartilhada por todos os layouts."""
from dataclasses import replace
import math
import threading
from unittest.mock import patch

import pytest

from circuit_viewer.block_analysis import BlockAnalysisResult
from circuit_viewer.block_graph import (
    BlockGraph, BlockGraphLayoutMode, block_layout_groups, external_block_hosts,
    filter_block_graph, block_node_envelopes, layout_block_graph,
    layout_block_graph_by_coordinates,
)
from circuit_viewer.graphviz_layout import (
    bundled_graphviz_dot, calculate_graphviz_layout, serialize_graphviz_dot,
)
from tests.test_graphviz_layout import _record, _edge


def neighborhood():
    # A:1,2; B:3,4; C:5,6,7; D:8; E:9; indefinido:10; F:11 (isolado).
    nodes = tuple(replace(_record(i, source=i in (1, 3, 5, 11)),
                          total_power=float(i), consumer_count=i * 10) for i in range(1, 12))
    pairs = ((1, 2), (3, 4), (2, 3), (2, 5), (4, 5), (4, 6), (4, 8),
             (5, 7), (8, 9), (5, 6), (2, 5), (5, 5), (2, 10), (2, 2))
    edges = tuple(replace(_edge(i, *pair), state=str(i % 2)) for i, pair in enumerate(pairs))
    return BlockGraph(nodes, edges), {1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 2, 7: 2, 8: 3, 9: 4, 10: None, 11: 5}


@pytest.mark.parametrize("selected,nodes,edges", [
    ((0,), (1, 2, 3, 5), (0, 2, 3, 10, 13)),
    ((0, 1), (1, 2, 3, 4, 5, 6, 8), (0, 1, 2, 3, 4, 5, 6, 10, 13)),
    ((1, 0), (1, 2, 3, 4, 5, 6, 8), (0, 1, 2, 3, 4, 5, 6, 10, 13)),
    ((0, 1, 3), (1, 2, 3, 4, 5, 6, 8, 9), (0, 1, 2, 3, 4, 5, 6, 8, 10, 13)),
    ((0, 5), (1, 2, 3, 5, 11), (0, 2, 3, 10, 13)),
    ((), (), ()),
])
def test_one_hop_for_every_selected_circuit_preserves_original_records(selected, nodes, edges):
    graph, owners = neighborhood()
    result = filter_block_graph(graph, owners, selected)
    assert result.node_ids == nodes
    assert tuple(edge.switch_index for edge in result.edges) == edges
    assert all(node is graph.nodes[node.block_id - 1] for node in result.nodes)
    assert all(edge is graph.edges[edge.switch_index] for edge in result.edges)


def test_unresolved_choice_does_not_expand_another_neighborhood():
    graph, owners = neighborhood()
    assert filter_block_graph(graph, owners, (), include_unresolved=True).node_ids == (10,)
    result = filter_block_graph(graph, owners, (0, 1), include_unresolved=True)
    assert result.node_ids == (1, 2, 3, 4, 5, 6, 8, 10)
    assert result.edges[-2].switch_index == 12


def test_shared_external_node_uses_lowest_host_without_changing_owner():
    graph, owners = neighborhood()
    selected = filter_block_graph(graph, owners, (0, 1))
    hosts = external_block_hosts(selected, owners, (1, 0))
    assert hosts == {5: 0, 6: 1, 8: 1}
    groups = block_layout_groups(selected, owners, (1, 0))
    assert groups == {1: ("circuit", 0), 2: ("circuit", 0), 3: ("circuit", 1),
                      4: ("circuit", 1), 5: ("circuit", 0), 6: ("circuit", 1), 8: ("circuit", 1)}
    assert owners[5] == owners[6] == 2
    assert block_layout_groups(selected, owners, (0, 1)) == groups
    expanded = filter_block_graph(graph, owners, (0, 1, 2))
    groups = block_layout_groups(expanded, owners, (0, 1, 2))
    assert all(groups[i] == ("circuit", 2) for i in (5, 6, 7))


@pytest.mark.parametrize("mode", ["tree", "coordinates", "graphviz"])
def test_layouts_preserve_nodes_edges_and_caption_space(mode):
    graph, owners = neighborhood()
    graph = filter_block_graph(graph, owners, (0, 1))
    envelopes = block_node_envelopes(graph.nodes)
    kwargs = dict(node_envelopes=envelopes, block_circuit_indices=owners, selected_circuit_indices=(0, 1))
    if mode == "tree":
        layout = layout_block_graph(graph, **kwargs)
    elif mode == "coordinates":
        anchors = {i: (i * 40.0, (i % 3) * 25.0) for i in graph.node_ids}
        layout = layout_block_graph_by_coordinates(graph, anchors, **kwargs)
    else:
        dot = serialize_graphviz_dot(graph, **kwargs)
        assert dot.layout_groups == ((1, 2, 5), (3, 4, 6, 8))
        layout = calculate_graphviz_layout(bundled_graphviz_dot(), dot, graph, envelopes)
    assert set(layout.positions) == set(graph.node_ids)
    assert set(layout.edge_routes) == {edge.switch_index for edge in graph.edges}
    assert all(math.isfinite(v) for point in layout.positions.values() for v in point)
    if mode != "coordinates":
        assert set(layout.root_ids) == {1, 3}  # C:5 é fonte real, mas apenas vizinho neste recorte.
    for i, left in enumerate(graph.node_ids):
        for right in graph.node_ids[i+1:]:
            dx, dy = (abs(a-b) for a, b in zip(layout.positions[left], layout.positions[right]))
            assert (dx >= (envelopes[left].width + envelopes[right].width) / 2 - 0.01
                    or dy >= (envelopes[left].height + envelopes[right].height) / 2 - 0.01)


def graph_window():
    from circuit_viewer.block_graph_window import BlockGraphWindow
    graph, owners = neighborhood()
    window = BlockGraphWindow()
    with patch('circuit_viewer.block_graph_window.build_block_graph', return_value=graph):
        window.set_result(BlockAnalysisResult(graph.nodes))
    window.set_circuit_styles(owners, ('#112233', '#445566', '#778899', '#AABBCC', '#DDEEFF', '#123456'),
                              ('A', 'B', 'C', 'D', 'E', 'F'))
    window._coordinate_anchors = {i: (i * 40.0, (i % 3) * 25.0) for i in graph.node_ids}
    return window


def wait_ui(predicate):
    from PyQt6.QtTest import QTest
    for _ in range(500):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), "A interface não concluiu a operação no prazo."


def test_ui_expand_shrink_and_include_neighbors_keep_magenta_metadata():
    from circuit_viewer.block_graph_window import INTERCIRCUIT_COLOR
    window = graph_window()
    try:
        window._circuit_selection_changed((0,), False)
        assert set(window.view.node_items) == {1, 2, 3, 5}
        window._circuit_selection_changed((0, 1), False)
        assert set(window.view.node_items) == {1, 2, 3, 4, 5, 6, 8}
        assert window.circuit_selector_button.text() == "Circuitos exibidos: 2/6"
        shared = window.view.node_items[5]
        assert shared.record.consumer_count == 50
        assert "UC: 50" in shared.caption_text
        ties = [item for item in window.view.edge_items if item.intercircuit]
        assert {item.edge.switch_index for item in ties} == {2, 3, 4, 5, 6, 10}
        assert all(item.stroke_color.name().upper() == INTERCIRCUIT_COLOR for item in ties)
        window.select_switch(4)
        assert window.view.selected_switch_index == 4
        window._circuit_selection_changed((0,), False)
        assert set(window.view.node_items) == {1, 2, 3, 5}
        assert window.view.selected_switch_index is None
        window.circuit_selector_popup.include_neighbors_button.click()
        assert window.selected_circuit_indices == frozenset({0, 1, 2})
        assert set(window.view.node_items) == {1, 2, 3, 4, 5, 6, 7, 8}
        window._fallback_from_graphviz(window.view.graph, 'falha simulada')
        assert set(window.view.node_items) == {1, 2, 3, 4, 5, 6, 7, 8}
        assert window.layout_mode == BlockGraphLayoutMode.TREE
    finally:
        window.close()


def test_late_real_graphviz_worker_cannot_replace_expanded_selection():
    from circuit_viewer import block_graph_window as ui
    window = graph_window()
    ready, release = threading.Event(), threading.Event()
    original = ui.calculate_graphviz_layout
    calls = []
    def delayed(*args, **kwargs):
        first = not calls
        calls.append(first)
        result = original(*args, **kwargs)
        if first:
            ready.set()
            assert release.wait(5)
        return result
    try:
        window._circuit_selection_changed((0,), False)
        with patch.object(ui, 'calculate_graphviz_layout', side_effect=delayed):
            window.set_layout_mode(BlockGraphLayoutMode.GRAPHVIZ_DOT)
            wait_ui(ready.is_set)
            old_generation = window._graphviz_generation
            old_key = window._graphviz_jobs[old_generation][-1]
            window._circuit_selection_changed((0, 1), False)
            wait_ui(lambda: set(window.view.node_items) == {1, 2, 3, 4, 5, 6, 8})
            release.set()
            wait_ui(lambda: not window._graphviz_jobs)
            assert set(window.view.node_items) == {1, 2, 3, 4, 5, 6, 8}
            labels = [item.label_item.sceneBoundingRect() for item in window.view.edge_items]
            assert all(not left.intersects(right) for i, left in enumerate(labels) for right in labels[i+1:])
            assert old_key not in window._graphviz_cache
            count = len(calls)
            window._circuit_selection_changed((0,), False)
            wait_ui(lambda: not window._graphviz_jobs)
            window._circuit_selection_changed((0, 1), False)
            wait_ui(lambda: not window._graphviz_jobs)
            assert len(calls) == count + 1  # A+B reutiliza seu próprio cache.
            assert set(window.view.node_items) == {1, 2, 3, 4, 5, 6, 8}
    finally:
        release.set()
        wait_ui(lambda: not window._graphviz_jobs)
        window.close()
