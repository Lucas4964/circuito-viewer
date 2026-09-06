"""Benchmark determinístico dos layouts do grafo com 26 circuitos.

O cenário padrão contém 1.560 blocos, árvores locais ramificadas, ciclos,
chaves paralelas, autoenlaces e ligações entre circuitos. Ele mede apenas o
núcleo sem Qt e aceita a API antiga ou a API enriquecida com circuitos,
envelopes e razão de aspecto. Além do custo, protege a hierarquia vertical:
camadas rígidas por profundidade, arestas descendentes e subárvores contíguas.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import inspect
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import circuit_viewer.block_graph as block_graph_module
from circuit_viewer.block_analysis import BlockRecord
from circuit_viewer.block_graph import (
    BlockGraph,
    BlockGraphEdge,
    filter_block_graph,
    layout_block_graph,
    layout_block_graph_by_coordinates,
)
from circuit_viewer.graphviz_layout import (
    GRAPHVIZ_TIMEOUT_SECONDS,
    calculate_graphviz_layout,
    probe_graphviz_runtime,
    serialize_graphviz_dot,
)


CIRCUIT_COUNT = 26
BLOCKS_PER_CIRCUIT = 60
NODE_COUNT = CIRCUIT_COUNT * BLOCKS_PER_CIRCUIT
TARGET_ASPECT_RATIO = 16.0 / 9.0
FILTER_TARGET_SECONDS = 0.25
TREE_TARGET_SECONDS = 5.0
COORDINATE_TARGET_SECONDS = 8.0
COMPACT_COORDINATE_TARGET_SECONDS = 3.0
SINGLE_TREE_TARGET_SECONDS = 2.0
GRAPHVIZ_TARGET_SECONDS = GRAPHVIZ_TIMEOUT_SECONDS
MAX_ASPECT_DISTORTION = 3.0
# Camadas rígidas podem deixar vazios inevitáveis; 7,5% ainda reprova com boa
# margem o baseline problemático de aproximadamente 2,2%.
MIN_TREE_ENVELOPE_FILL_RATIO = 0.075
MIN_SINGLE_TREE_ENVELOPE_FILL_RATIO = 0.025
MIN_COORDINATE_ENVELOPE_FILL_RATIO = 0.015
MIN_COMPACT_ENVELOPE_FILL_RATIO = 0.025
MAX_P95_TO_MEDIAN_EDGE_RATIO = 10.0
MAX_TREE_TO_MEDIAN_EDGE_RATIO = 15.0
MAX_COORDINATE_TO_MEDIAN_EDGE_RATIO = 20.0
MAX_TREE_EDGE_CROSSINGS = 0
MAX_PARENT_CENTER_OFFSET_RATIO = 0.05

Point = tuple[float, float]
Segment = tuple[Point, Point]


@dataclass(frozen=True, slots=True)
class ReadabilityMetrics:
    width: float
    height: float
    aspect_ratio: float
    aspect_distortion: float
    envelope_fill_ratio: float
    median_edge_length: float
    p95_edge_length: float
    maximum_edge_length: float
    maximum_edge_label: str


@dataclass(frozen=True, slots=True)
class HierarchyMetrics:
    misaligned_layer_count: int
    reversed_or_skipped_tree_edge_count: int
    root_not_at_top_count: int
    parent_outside_children_count: int
    maximum_parent_center_offset_ratio: float
    interleaved_subtree_count: int


def _indices(*values: int) -> np.ndarray:
    result = np.asarray(values, dtype=np.intp)
    result.setflags(write=False)
    return result


def _record(block_id: int, *, source: bool) -> BlockRecord:
    return BlockRecord(
        block_id=block_id,
        bar_indices=_indices(block_id - 1),
        segment_indices=_indices(),
        load_indices=_indices(),
        boundary_switch_indices=_indices(),
        boundary_switch_codes=(),
        total_power=float(10 + (block_id * 37) % 990),
        total_length=None,
        contains_source=source,
    )


def create_scenario() -> tuple[
    BlockGraph,
    dict[int, int | None],
    dict[int, tuple[float, float]],
    dict[int, float],
]:
    """Monta uma rede grande, reproduzível e sem modelos gráficos."""

    records: list[BlockRecord] = []
    edges: list[BlockGraphEdge] = []
    block_circuits: dict[int, int | None] = {}
    anchors: dict[int, tuple[float, float]] = {}
    diameters: dict[int, float] = {}
    switch_index = 0

    def add_edge(start: int, end: int, prefix: str = "CH") -> None:
        nonlocal switch_index
        edges.append(
            BlockGraphEdge(
                switch_index=switch_index,
                switch_id=f"{prefix}-{switch_index:05d}",
                switch_code=f"{prefix}-{switch_index:05d}",
                state="0" if switch_index % 11 == 0 else "1",
                start_block_id=start,
                end_block_id=end,
            )
        )
        switch_index += 1

    for circuit_index in range(CIRCUIT_COUNT):
        base = circuit_index * BLOCKS_PER_CIRCUIT
        grid_row, grid_column = divmod(circuit_index, 7)
        center_x = grid_column * 85_000.0
        center_y = grid_row * 115_000.0
        # Um componente geograficamente remoto exercita a compressão de
        # distâncias sem mudar a conectividade elétrica do cenário.
        if circuit_index == CIRCUIT_COUNT - 1:
            center_x += 1_200_000.0
            center_y -= 750_000.0

        for local_index in range(BLOCKS_PER_CIRCUIT):
            block_id = base + local_index + 1
            records.append(_record(block_id, source=local_index == 0))
            block_circuits[block_id] = circuit_index
            diameter = 36.0 + float((block_id * 17) % 37)
            diameters[block_id] = diameter

            if local_index == 0:
                depth = 0
                slot = 0
            else:
                depth = int(math.log2(local_index + 1))
                slot = local_index - (2**depth - 1)
            slots_in_level = max(1, 2**depth)
            angle = ((slot + 0.5) / slots_in_level - 0.5) * 1.8
            radius = 520.0 * (depth + 0.35) ** 1.65
            anchors[block_id] = (
                center_x + radius * math.cos(angle),
                center_y + radius * math.sin(angle),
            )

        # Árvore binária local.
        for local_index in range(1, BLOCKS_PER_CIRCUIT):
            child = base + local_index + 1
            parent_local = (local_index - 1) // 2
            add_edge(base + parent_local + 1, child)

        # Relações que não pertencem à árvore geradora.
        add_edge(base + 13, base + 38, "CICLO")
        add_edge(base + 1, base + 2, "PAR")
        add_edge(base + 1, base + 1, "AUTO")

    # Cadeia principal do metagrafo, com alguns atalhos e paralelas.
    for circuit_index in range(CIRCUIT_COUNT - 1):
        start = circuit_index * BLOCKS_PER_CIRCUIT + 48
        end = (circuit_index + 1) * BLOCKS_PER_CIRCUIT + 9
        add_edge(start, end, "INTER")
        if circuit_index % 7 == 0:
            add_edge(start, end, "INTER-PAR")
    for circuit_index in range(0, CIRCUIT_COUNT - 5, 4):
        start = circuit_index * BLOCKS_PER_CIRCUIT + 55
        end = (circuit_index + 5) * BLOCKS_PER_CIRCUIT + 16
        add_edge(start, end, "ATALHO")

    return BlockGraph(tuple(records), tuple(edges)), block_circuits, anchors, diameters


def _node_envelopes(
    records: tuple[BlockRecord, ...],
    diameters: dict[int, float],
) -> dict[int, object] | None:
    factory = getattr(block_graph_module, "block_node_envelopes", None)
    if factory is not None:
        return factory(records, diameters)
    envelope_type = getattr(block_graph_module, "BlockNodeEnvelope", None)
    if envelope_type is None:
        return None
    return {
        block_id: envelope_type(
            width=diameter,
            height=diameter + 22.0,
        )
        for block_id, diameter in diameters.items()
    }


def _call_supported(
    function: Callable[..., Any],
    *args: object,
    **candidates: object,
) -> Any:
    """Passa somente extensões reconhecidas pela versão instalada da API."""

    parameters = inspect.signature(function).parameters
    accepts_extra = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    kwargs = (
        candidates
        if accepts_extra
        else {name: value for name, value in candidates.items() if name in parameters}
    )
    return function(*args, **kwargs)


def _measure(call: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    value = call()
    return value, time.perf_counter() - started


def _validate_layout(
    layout: object,
    graph: BlockGraph,
    *,
    require_routes: bool,
) -> None:
    positions = getattr(layout, "positions")
    assert set(positions) == set(graph.node_ids)
    assert all(
        math.isfinite(float(coordinate))
        for position in positions.values()
        for coordinate in position
    )
    switch_indices = {edge.switch_index for edge in graph.edges}
    for attribute in ("edge_routes", "edge_label_positions"):
        values = getattr(layout, attribute, None)
        if require_routes:
            assert values is not None
            assert set(values) == switch_indices


def _validate_tree_forest(layout: object, graph: BlockGraph) -> None:
    roots = tuple(getattr(layout, "root_ids"))
    tree_edge_indices = frozenset(getattr(layout, "tree_edge_indices"))
    assert len(tree_edge_indices) == len(graph.nodes) - len(roots)
    assert all(0 <= index < len(graph.edges) for index in tree_edge_indices)


def _tree_hierarchy_metrics(
    layout: object,
    graph: BlockGraph,
    group_by_node: dict[int, object],
) -> HierarchyMetrics:
    """Mede a fidelidade vertical sem depender de distâncias absolutas."""

    positions = getattr(layout, "positions")
    depths = getattr(layout, "depths")
    roots = frozenset(getattr(layout, "root_ids"))
    tree_edge_indices = tuple(sorted(getattr(layout, "tree_edge_indices")))
    epsilon = 1.0e-6

    parents: dict[int, int] = {}
    children: dict[int, list[int]] = {}
    reversed_or_skipped = 0
    for edge_index in tree_edge_indices:
        edge = graph.edges[edge_index]
        start_depth = depths[edge.start_block_id]
        end_depth = depths[edge.end_block_id]
        if start_depth < end_depth:
            parent, child = edge.start_block_id, edge.end_block_id
        else:
            parent, child = edge.end_block_id, edge.start_block_id
        if (
            depths[child] != depths[parent] + 1
            or positions[child][1] <= positions[parent][1] + epsilon
            or (child in parents and parents[child] != parent)
        ):
            reversed_or_skipped += 1
        parents[child] = parent
        children.setdefault(parent, []).append(child)

    grouped_nodes: dict[object, list[int]] = {}
    for block_id in graph.node_ids:
        grouped_nodes.setdefault(group_by_node[block_id], []).append(block_id)

    misaligned_layers = 0
    roots_not_at_top = 0
    interleaved_subtrees = 0
    for block_ids in grouped_nodes.values():
        levels: dict[int, list[int]] = {}
        for block_id in block_ids:
            levels.setdefault(depths[block_id], []).append(block_id)
        level_y: dict[int, float] = {}
        for depth, level_nodes in levels.items():
            y_values = [positions[block_id][1] for block_id in level_nodes]
            if max(y_values) - min(y_values) > epsilon:
                misaligned_layers += 1
            level_y[depth] = math.fsum(y_values) / len(y_values)
        ordered_depths = sorted(level_y)
        for previous, current in zip(ordered_depths, ordered_depths[1:]):
            if level_y[current] <= level_y[previous] + epsilon:
                misaligned_layers += 1

        group_roots = roots.intersection(block_ids)
        minimum_depth = min(ordered_depths)
        minimum_y = min(level_y.values())
        if not group_roots:
            roots_not_at_top += 1
        for root in group_roots:
            if (
                depths[root] != minimum_depth
                or positions[root][1] > minimum_y + epsilon
            ):
                roots_not_at_top += 1

        block_set = frozenset(block_ids)
        for depth in ordered_depths:
            if depth <= minimum_depth:
                continue
            ordered_level = sorted(
                levels[depth],
                key=lambda block_id: (positions[block_id][0], block_id),
            )
            for ancestor_depth in range(minimum_depth, depth):
                positions_by_ancestor: dict[int, list[int]] = {}
                for order_index, block_id in enumerate(ordered_level):
                    ancestor = block_id
                    while (
                        ancestor in parents
                        and depths[ancestor] > ancestor_depth
                    ):
                        ancestor = parents[ancestor]
                    if (
                        ancestor in block_set
                        and depths[ancestor] == ancestor_depth
                    ):
                        positions_by_ancestor.setdefault(ancestor, []).append(
                            order_index
                        )
                for occupied in positions_by_ancestor.values():
                    if occupied[-1] - occupied[0] + 1 != len(occupied):
                        interleaved_subtrees += 1

    parent_outside_children = 0
    maximum_center_offset_ratio = 0.0
    for parent, descendants in children.items():
        if len(descendants) < 2:
            continue
        child_x = [positions[block_id][0] for block_id in descendants]
        left = min(child_x)
        right = max(child_x)
        parent_x = positions[parent][0]
        if parent_x < left - epsilon or parent_x > right + epsilon:
            parent_outside_children += 1
        span = max(right - left, 1.0)
        maximum_center_offset_ratio = max(
            maximum_center_offset_ratio,
            abs(parent_x - (left + right) / 2.0) / span,
        )

    return HierarchyMetrics(
        misaligned_layer_count=misaligned_layers,
        reversed_or_skipped_tree_edge_count=reversed_or_skipped,
        root_not_at_top_count=roots_not_at_top,
        parent_outside_children_count=parent_outside_children,
        maximum_parent_center_offset_ratio=maximum_center_offset_ratio,
        interleaved_subtree_count=interleaved_subtrees,
    )


def _print_hierarchy_metrics(label: str, metrics: HierarchyMetrics) -> None:
    print(
        f"{label}: camadas desalinhadas={metrics.misaligned_layer_count:n}, "
        "arestas não descendentes/saltos="
        f"{metrics.reversed_or_skipped_tree_edge_count:n}, "
        f"raízes fora do topo={metrics.root_not_at_top_count:n}, "
        "pais fora dos filhos="
        f"{metrics.parent_outside_children_count:n}, "
        "desvio central máximo="
        f"{metrics.maximum_parent_center_offset_ratio:.3f}, "
        f"subárvores intercaladas={metrics.interleaved_subtree_count:n}"
    )


def _hierarchy_within_limits(metrics: HierarchyMetrics) -> bool:
    return (
        metrics.misaligned_layer_count == 0
        and metrics.reversed_or_skipped_tree_edge_count == 0
        and metrics.root_not_at_top_count == 0
        and metrics.parent_outside_children_count == 0
        and metrics.maximum_parent_center_offset_ratio
        <= MAX_PARENT_CENTER_OFFSET_RATIO
        and metrics.interleaved_subtree_count == 0
    )


def _envelope_dimensions(value: object | None) -> tuple[float, float]:
    if value is None:
        return 56.0, 78.0
    if hasattr(value, "width") and hasattr(value, "height"):
        return float(getattr(value, "width")), float(getattr(value, "height"))
    return float(value[0]), float(value[1])  # type: ignore[index]


def _readability_metrics(
    layout: object,
    graph: BlockGraph,
    envelopes: dict[int, object] | None,
    *,
    edge_indices: frozenset[int] | tuple[int, ...] | None = None,
) -> ReadabilityMetrics:
    positions = getattr(layout, "positions")
    bounds: list[tuple[float, float, float, float]] = []
    total_envelope_area = 0.0
    for block_id in graph.node_ids:
        width, height = _envelope_dimensions(
            None if envelopes is None else envelopes.get(block_id)
        )
        x, y = positions[block_id]
        bounds.append(
            (
                x - width / 2.0,
                y - height / 2.0,
                x + width / 2.0,
                y + height / 2.0,
            )
        )
        total_envelope_area += width * height
    left = min(value[0] for value in bounds)
    top = min(value[1] for value in bounds)
    right = max(value[2] for value in bounds)
    bottom = max(value[3] for value in bounds)
    width = max(right - left, 1.0)
    height = max(bottom - top, 1.0)
    aspect_ratio = width / height

    measured_edges: list[tuple[float, str]] = []
    endpoint_keys: set[tuple[int, int]] = set()
    measured_indices = (
        range(len(graph.edges))
        if edge_indices is None
        else sorted(edge_indices)
    )
    for edge_index in measured_indices:
        edge = graph.edges[edge_index]
        if edge.start_block_id == edge.end_block_id:
            continue
        if edge.endpoint_key in endpoint_keys:
            continue
        endpoint_keys.add(edge.endpoint_key)
        measured_edges.append(
            (
                math.dist(
                    positions[edge.start_block_id],
                    positions[edge.end_block_id],
                ),
                edge.label,
            )
        )
    measured_edges.sort(key=lambda value: (value[0], value[1]))
    lengths = [value[0] for value in measured_edges]
    median_edge_length = (
        float(np.median(np.asarray(lengths, dtype=np.float64))) if lengths else 0.0
    )
    p95_index = max(0, math.ceil(len(lengths) * 0.95) - 1)
    p95_edge_length = lengths[p95_index] if lengths else 0.0
    maximum_edge_length = lengths[-1] if lengths else 0.0
    return ReadabilityMetrics(
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        aspect_distortion=max(
            aspect_ratio / TARGET_ASPECT_RATIO,
            TARGET_ASPECT_RATIO / aspect_ratio,
        ),
        envelope_fill_ratio=total_envelope_area / (width * height),
        median_edge_length=median_edge_length,
        p95_edge_length=p95_edge_length,
        maximum_edge_length=maximum_edge_length,
        maximum_edge_label=measured_edges[-1][1] if measured_edges else "-",
    )


def _print_metrics(label: str, metrics: ReadabilityMetrics) -> None:
    median = max(metrics.median_edge_length, 1.0e-9)
    print(
        f"{label}: bounds={metrics.width:.0f}×{metrics.height:.0f}, "
        f"aspecto={metrics.aspect_ratio:.2f}, "
        f"distorção={metrics.aspect_distortion:.2f}×, "
        f"ocupação={metrics.envelope_fill_ratio:.3f}, "
        f"arestas p50/p95/máx={metrics.median_edge_length:.0f}/"
        f"{metrics.p95_edge_length:.0f}/{metrics.maximum_edge_length:.0f} "
        f"({metrics.maximum_edge_length / median:.1f}× p50, "
        f"{metrics.maximum_edge_label})"
    )


def _readability_within_limits(
    metrics: ReadabilityMetrics,
    *,
    minimum_fill_ratio: float,
    maximum_edge_ratio: float,
) -> bool:
    median = max(metrics.median_edge_length, 1.0e-9)
    return (
        metrics.aspect_distortion <= MAX_ASPECT_DISTORTION
        and metrics.envelope_fill_ratio >= minimum_fill_ratio
        and metrics.p95_edge_length / median <= MAX_P95_TO_MEDIAN_EDGE_RATIO
        and metrics.maximum_edge_length / median <= maximum_edge_ratio
    )


def _route_segments(route: object) -> tuple[Segment, ...]:
    points = tuple(getattr(route, "points"))
    if getattr(route, "curved", False) and len(points) == 3:
        start, control, end = points
        sampled: list[tuple[float, float]] = []
        for index in range(13):
            progress = index / 12.0
            inverse = 1.0 - progress
            sampled.append(
                (
                    inverse**2 * start[0]
                    + 2.0 * inverse * progress * control[0]
                    + progress**2 * end[0],
                    inverse**2 * start[1]
                    + 2.0 * inverse * progress * control[1]
                    + progress**2 * end[1],
                )
            )
        points = tuple(sampled)
    return tuple(zip(points, points[1:]))


def _segments_cross(
    first: Segment,
    second: Segment,
) -> bool:
    def orientation(
        start: tuple[float, float],
        end: tuple[float, float],
        point: tuple[float, float],
    ) -> float:
        return (end[0] - start[0]) * (point[1] - start[1]) - (
            end[1] - start[1]
        ) * (point[0] - start[0])

    first_start, first_end = first
    second_start, second_end = second
    first_side = orientation(first_start, first_end, second_start)
    second_side = orientation(first_start, first_end, second_end)
    third_side = orientation(second_start, second_end, first_start)
    fourth_side = orientation(second_start, second_end, first_end)
    epsilon = 1.0e-7
    return (
        first_side * second_side < -epsilon
        and third_side * fourth_side < -epsilon
    )


def _tree_edge_crossings(layout: object, graph: BlockGraph) -> int:
    tree_edge_indices = tuple(sorted(getattr(layout, "tree_edge_indices")))
    routes = getattr(layout, "edge_routes", {})
    candidates: list[tuple[frozenset[int], tuple[Segment, ...]]] = []
    positions = getattr(layout, "positions")
    for edge_index in tree_edge_indices:
        edge = graph.edges[edge_index]
        if edge.start_block_id == edge.end_block_id:
            continue
        route = routes.get(edge.switch_index)
        segments = (
            _route_segments(route)
            if route is not None
            else ((positions[edge.start_block_id], positions[edge.end_block_id]),)
        )
        candidates.append(
            (
                frozenset((edge.start_block_id, edge.end_block_id)),
                segments,
            )
        )

    # A grade torna a checagem subquadrática no caso comum: somente rotas que
    # compartilham ao menos uma célula podem formar um cruzamento.
    cell_size = 256.0
    grid: dict[tuple[int, int], set[int]] = {}
    for candidate_index, (_nodes, segments) in enumerate(candidates):
        for start, end in segments:
            minimum_x = math.floor(min(start[0], end[0]) / cell_size)
            maximum_x = math.floor(max(start[0], end[0]) / cell_size)
            minimum_y = math.floor(min(start[1], end[1]) / cell_size)
            maximum_y = math.floor(max(start[1], end[1]) / cell_size)
            for cell_x in range(minimum_x, maximum_x + 1):
                for cell_y in range(minimum_y, maximum_y + 1):
                    grid.setdefault((cell_x, cell_y), set()).add(candidate_index)

    possible_pairs: set[tuple[int, int]] = set()
    for occupants in grid.values():
        ordered = sorted(occupants)
        for offset, first_index in enumerate(ordered):
            for second_index in ordered[offset + 1 :]:
                possible_pairs.add((first_index, second_index))

    crossings = 0
    for first_index, second_index in sorted(possible_pairs):
        first_nodes, first_segments = candidates[first_index]
        second_nodes, second_segments = candidates[second_index]
        if first_nodes & second_nodes:
            continue
        if any(
            _segments_cross(first_segment, second_segment)
            for first_segment in first_segments
            for second_segment in second_segments
        ):
            crossings += 1
    return crossings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="retorna erro quando alguma meta de tempo for ultrapassada",
    )
    args = parser.parse_args()

    graph, block_circuits, anchors, diameters = create_scenario()
    selected = frozenset(range(CIRCUIT_COUNT))
    envelopes = _node_envelopes(graph.nodes, diameters)
    common_options: dict[str, object] = {
        "block_circuit_indices": block_circuits,
        "selected_circuit_indices": selected,
        "target_aspect_ratio": TARGET_ASPECT_RATIO,
        "edge_label_sizes": {
            edge.switch_index: (max(24.0, len(edge.label) * 7.0), 18.0)
            for edge in graph.edges
        },
    }
    if envelopes is not None:
        common_options["node_envelopes"] = envelopes

    visible_graph, filter_seconds = _measure(
        lambda: filter_block_graph(graph, block_circuits, selected)
    )

    def tree_call() -> object:
        return _call_supported(layout_block_graph, visible_graph, **common_options)

    def coordinate_call() -> object:
        return _call_supported(
            layout_block_graph_by_coordinates,
            visible_graph,
            anchors,
            diameters=diameters,
            **common_options,
        )

    tree_layout, tree_seconds = _measure(tree_call)
    coordinate_layout, coordinate_seconds = _measure(coordinate_call)
    graphviz_status = probe_graphviz_runtime()
    graphviz_layout = None
    graphviz_seconds = 0.0
    graphviz_deterministic = True
    if graphviz_status.available and envelopes is not None:
        dot_input = serialize_graphviz_dot(
            visible_graph,
            node_envelopes=envelopes,
            block_circuit_indices=block_circuits,
            selected_circuit_indices=selected,
        )

        def graphviz_call() -> object:
            return calculate_graphviz_layout(
                graphviz_status.executable,
                dot_input,
                visible_graph,
                envelopes,
            )

        graphviz_layout, graphviz_seconds = _measure(graphviz_call)
        _validate_layout(graphviz_layout, visible_graph, require_routes=True)
        graphviz_deterministic = graphviz_layout == graphviz_call()
    extended_api = "node_envelopes" in inspect.signature(
        layout_block_graph
    ).parameters
    _validate_layout(tree_layout, visible_graph, require_routes=extended_api)
    _validate_tree_forest(tree_layout, visible_graph)
    _validate_layout(
        coordinate_layout,
        visible_graph,
        require_routes=extended_api,
    )

    # A repetição não entra na métrica; ela funciona como contrato explícito de
    # que a mesma entrada não depende de ordem de hash nem de estado anterior.
    assert tree_layout == tree_call()
    assert coordinate_layout == coordinate_call()

    # Seis circuitos dispostos lado a lado reproduzem a seleção que mais tende
    # a virar uma faixa horizontal estreita no modo espacial.
    compact_selected = frozenset(range(6))
    compact_graph = filter_block_graph(graph, block_circuits, compact_selected)
    compact_node_ids = frozenset(compact_graph.node_ids)
    compact_anchors = {
        block_id: anchor
        for block_id, anchor in anchors.items()
        if block_id in compact_node_ids
    }
    compact_diameters = {
        block_id: diameter
        for block_id, diameter in diameters.items()
        if block_id in compact_node_ids
    }
    compact_envelopes = (
        None
        if envelopes is None
        else {
            block_id: envelope
            for block_id, envelope in envelopes.items()
            if block_id in compact_node_ids
        }
    )
    compact_options: dict[str, object] = {
        "block_circuit_indices": block_circuits,
        "selected_circuit_indices": compact_selected,
        "target_aspect_ratio": TARGET_ASPECT_RATIO,
        "edge_label_sizes": {
            edge.switch_index: (max(24.0, len(edge.label) * 7.0), 18.0)
            for edge in compact_graph.edges
        },
    }
    if compact_envelopes is not None:
        compact_options["node_envelopes"] = compact_envelopes
    compact_coordinate_layout, compact_coordinate_seconds = _measure(
        lambda: _call_supported(
            layout_block_graph_by_coordinates,
            compact_graph,
            compact_anchors,
            diameters=compact_diameters,
            **compact_options,
        )
    )
    _validate_layout(
        compact_coordinate_layout,
        compact_graph,
        require_routes=extended_api,
    )

    # Um único circuito com suas folhas externas precisa continuar compacto;
    # este é o caso de uso mais frequente e não pode virar uma tira horizontal.
    single_selected = frozenset((0,))
    single_graph = filter_block_graph(graph, block_circuits, single_selected)
    single_node_ids = frozenset(single_graph.node_ids)
    single_envelopes = (
        None
        if envelopes is None
        else {
            block_id: envelope
            for block_id, envelope in envelopes.items()
            if block_id in single_node_ids
        }
    )
    single_options: dict[str, object] = {
        "block_circuit_indices": block_circuits,
        "selected_circuit_indices": single_selected,
        "target_aspect_ratio": TARGET_ASPECT_RATIO,
        "edge_label_sizes": {
            edge.switch_index: (max(24.0, len(edge.label) * 7.0), 18.0)
            for edge in single_graph.edges
        },
    }
    if single_envelopes is not None:
        single_options["node_envelopes"] = single_envelopes
    single_tree_layout, single_tree_seconds = _measure(
        lambda: _call_supported(
            layout_block_graph,
            single_graph,
            **single_options,
        )
    )
    _validate_layout(
        single_tree_layout,
        single_graph,
        require_routes=extended_api,
    )
    _validate_tree_forest(single_tree_layout, single_graph)

    tree_readability = _readability_metrics(
        tree_layout,
        visible_graph,
        envelopes,
        edge_indices=frozenset(tree_layout.tree_edge_indices),
    )
    tree_all_edges_readability = _readability_metrics(
        tree_layout,
        visible_graph,
        envelopes,
    )
    coordinate_readability = _readability_metrics(
        coordinate_layout,
        visible_graph,
        envelopes,
    )
    compact_coordinate_readability = _readability_metrics(
        compact_coordinate_layout,
        compact_graph,
        compact_envelopes,
    )
    tree_crossings = _tree_edge_crossings(tree_layout, visible_graph)
    tree_hierarchy = _tree_hierarchy_metrics(
        tree_layout,
        visible_graph,
        block_circuits,
    )
    single_tree_readability = _readability_metrics(
        single_tree_layout,
        single_graph,
        single_envelopes,
        edge_indices=frozenset(single_tree_layout.tree_edge_indices),
    )
    single_tree_crossings = _tree_edge_crossings(
        single_tree_layout,
        single_graph,
    )
    single_tree_hierarchy = _tree_hierarchy_metrics(
        single_tree_layout,
        single_graph,
        {block_id: 0 for block_id in single_graph.node_ids},
    )

    route_count = len(getattr(tree_layout, "edge_routes", ()))
    print(f"Circuitos: {CIRCUIT_COUNT:n}")
    print(f"Blocos: {len(visible_graph.nodes):n}")
    print(f"Chaves: {len(visible_graph.edges):n}")
    print(f"Rotas explícitas: {route_count:n}")
    print(f"Filtro: {filter_seconds:.4f} s")
    print(f"Layout Árvore: {tree_seconds:.3f} s")
    if graphviz_layout is not None:
        print(
            "Layout Graphviz dot: "
            f"{graphviz_seconds:.3f} s "
            f"(determinístico: {'sim' if graphviz_deterministic else 'não'})"
        )
    else:
        print(f"Layout Graphviz dot: indisponível ({graphviz_status.reason})")
    print(f"Layout Árvore (1 circuito): {single_tree_seconds:.3f} s")
    print(f"Layout Coordenadas: {coordinate_seconds:.3f} s")
    print(
        "Layout Coordenadas (6 circuitos): "
        f"{compact_coordinate_seconds:.3f} s"
    )
    _print_metrics("Árvore completa (floresta)", tree_readability)
    _print_metrics("Árvore completa (todas as relações)", tree_all_edges_readability)
    print(f"Cruzamentos entre arestas da árvore: {tree_crossings:n}")
    _print_hierarchy_metrics("Hierarquia completa", tree_hierarchy)
    _print_metrics("Árvore, 1 circuito", single_tree_readability)
    print(
        "Cruzamentos na árvore de 1 circuito: "
        f"{single_tree_crossings:n}"
    )
    _print_hierarchy_metrics("Hierarquia, 1 circuito", single_tree_hierarchy)
    _print_metrics("Coordenadas completas", coordinate_readability)
    _print_metrics("Coordenadas, 6 circuitos", compact_coordinate_readability)

    if args.enforce and (
        len(visible_graph.nodes) != NODE_COUNT
        or filter_seconds > FILTER_TARGET_SECONDS
        or tree_seconds > TREE_TARGET_SECONDS
        or single_tree_seconds > SINGLE_TREE_TARGET_SECONDS
        or coordinate_seconds > COORDINATE_TARGET_SECONDS
        or compact_coordinate_seconds > COMPACT_COORDINATE_TARGET_SECONDS
        or (
            graphviz_layout is not None
            and (
                graphviz_seconds > GRAPHVIZ_TARGET_SECONDS
                or not graphviz_deterministic
            )
        )
        or tree_crossings > MAX_TREE_EDGE_CROSSINGS
        or single_tree_crossings > MAX_TREE_EDGE_CROSSINGS
        or not _hierarchy_within_limits(tree_hierarchy)
        or not _hierarchy_within_limits(single_tree_hierarchy)
        or not _readability_within_limits(
            tree_readability,
            minimum_fill_ratio=MIN_TREE_ENVELOPE_FILL_RATIO,
            maximum_edge_ratio=MAX_TREE_TO_MEDIAN_EDGE_RATIO,
        )
        or not _readability_within_limits(
            coordinate_readability,
            minimum_fill_ratio=MIN_COORDINATE_ENVELOPE_FILL_RATIO,
            maximum_edge_ratio=MAX_COORDINATE_TO_MEDIAN_EDGE_RATIO,
        )
        or not _readability_within_limits(
            single_tree_readability,
            minimum_fill_ratio=MIN_SINGLE_TREE_ENVELOPE_FILL_RATIO,
            maximum_edge_ratio=MAX_TREE_TO_MEDIAN_EDGE_RATIO,
        )
        or not _readability_within_limits(
            compact_coordinate_readability,
            minimum_fill_ratio=MIN_COMPACT_ENVELOPE_FILL_RATIO,
            maximum_edge_ratio=MAX_COORDINATE_TO_MEDIAN_EDGE_RATIO,
        )
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
