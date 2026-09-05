"""Modelo e layout determinístico do grafo simplificado de blocos.

Esta camada não importa Qt. Ela transforma o resultado da análise de blocos em
um multigrafo e oferece tanto a floresta em níveis quanto a projeção baseada nas
coordenadas da rede, deixando a interface apenas desenhar e interagir.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
import math
from statistics import median

from .block_analysis import BlockAnalysisResult, BlockRecord
from .model import CircuitCatalogModel


FIXED_NODE_DIAMETER = 56.0
MIN_NODE_DIAMETER = 36.0
MAX_NODE_DIAMETER = 72.0
HORIZONTAL_NODE_GAP = 200.0
VERTICAL_NODE_GAP = 170.0
COMPONENT_GAP = 260.0
GEOGRAPHIC_TARGET_EDGE_LENGTH = 200.0
GEOGRAPHIC_NODE_CLEARANCE = 24.0


class BlockGraphLayoutMode(StrEnum):
    """Formas disponíveis de organizar os blocos no canvas."""

    TREE = "tree"
    COORDINATES = "coordinates"


@dataclass(frozen=True, slots=True)
class BlockGraphEdge:
    """Uma chave manobrável e os blocos ligados pelas suas duas pontas."""

    switch_index: int
    switch_id: str
    switch_code: str
    state: str
    start_block_id: int
    end_block_id: int

    @property
    def label(self) -> str:
        return self.switch_code or self.switch_id

    @property
    def endpoint_key(self) -> tuple[int, int]:
        return tuple(sorted((self.start_block_id, self.end_block_id)))


@dataclass(frozen=True, slots=True)
class BlockGraph:
    nodes: tuple[BlockRecord, ...]
    edges: tuple[BlockGraphEdge, ...]

    @property
    def node_ids(self) -> tuple[int, ...]:
        return tuple(record.block_id for record in self.nodes)


@dataclass(frozen=True, slots=True)
class BlockGraphLayout:
    """Coordenadas lógicas e metadados da floresta geradora."""

    positions: dict[int, tuple[float, float]]
    depths: dict[int, int]
    root_ids: tuple[int, ...]
    tree_edge_indices: frozenset[int]


def filter_block_graph(
    graph: BlockGraph,
    block_circuit_indices: dict[int, int | None],
    selected_circuit_indices: frozenset[int] | set[int] | tuple[int, ...],
    *,
    include_unresolved: bool = False,
) -> BlockGraph:
    """Devolve o recorte dos circuitos escolhidos.

    A ordem dos nós e das arestas é preservada. Em particular,
    ``BlockGraphEdge.switch_index`` continua apontando para o registro original
    da chave, o que permite à interface navegar até o trecho correspondente.

    Com exatamente um circuito selecionado, acrescenta os blocos válidos dos
    outros circuitos diretamente ligados a ele e somente as arestas dessa
    ligação. O circuito vizinho não é expandido e seus demais enlaces não
    aparecem. Com zero ou vários circuitos, o recorte continua sendo induzido.
    """

    selected = frozenset(int(value) for value in selected_circuit_indices)
    base_visible_ids: set[int] = set()
    for record in graph.nodes:
        circuit_index = block_circuit_indices.get(record.block_id)
        if circuit_index is None:
            if include_unresolved:
                base_visible_ids.add(record.block_id)
        elif circuit_index in selected:
            base_visible_ids.add(record.block_id)

    visible_ids = set(base_visible_ids)
    direct_edge_indices: set[int] = set()
    if len(selected) == 1:
        selected_circuit = next(iter(selected))
        for edge_index, edge in enumerate(graph.edges):
            start_circuit = block_circuit_indices.get(edge.start_block_id)
            end_circuit = block_circuit_indices.get(edge.end_block_id)
            if (
                start_circuit == selected_circuit
                and end_circuit is not None
                and end_circuit != selected_circuit
            ):
                visible_ids.add(edge.end_block_id)
                direct_edge_indices.add(edge_index)
            elif (
                end_circuit == selected_circuit
                and start_circuit is not None
                and start_circuit != selected_circuit
            ):
                visible_ids.add(edge.start_block_id)
                direct_edge_indices.add(edge_index)
    nodes = tuple(
        record for record in graph.nodes if record.block_id in visible_ids
    )
    edges = tuple(
        edge
        for edge_index, edge in enumerate(graph.edges)
        if (
            edge.start_block_id in base_visible_ids
            and edge.end_block_id in base_visible_ids
        )
        or edge_index in direct_edge_indices
    )
    return BlockGraph(nodes, edges)


def direct_circuit_neighbors(
    graph: BlockGraph,
    block_circuit_indices: dict[int, int | None],
    circuit_indices: frozenset[int] | set[int] | tuple[int, ...],
) -> frozenset[int]:
    """Circuitos ligados diretamente aos informados por arestas intercircuito."""

    selected = frozenset(int(value) for value in circuit_indices)
    neighbors: set[int] = set()
    for edge in graph.edges:
        start = block_circuit_indices.get(edge.start_block_id)
        end = block_circuit_indices.get(edge.end_block_id)
        if start is None or end is None or start == end:
            continue
        if start in selected and end not in selected:
            neighbors.add(end)
        if end in selected and start not in selected:
            neighbors.add(start)
    return frozenset(neighbors)


def build_block_graph(result: BlockAnalysisResult) -> BlockGraph:
    """Converte cada bloco em nó e cada chave de fronteira em uma aresta.

    As pontas são obtidas das barras do trecho associado à chave. Isso preserva
    autoenlaces: em uma malha, retirar uma chave pode deixar suas duas pontas no
    mesmo bloco, mas a chave continua sendo uma relação que o grafo deve mostrar.
    """

    records = tuple(result.records)
    if not records:
        return BlockGraph((), ())

    segments = result.source_segments
    switches = result.source_switches
    if segments is None or switches is None:
        return BlockGraph(records, ())

    block_by_bar: dict[int, int] = {}
    boundary_indices: set[int] = set()
    for record in records:
        for bar_index in record.bar_indices.tolist():
            block_by_bar[int(bar_index)] = record.block_id
        boundary_indices.update(
            int(index) for index in record.boundary_switch_indices.tolist()
        )

    edges: list[BlockGraphEdge] = []
    for switch_index in sorted(boundary_indices):
        segment_index = int(switches.segment_indices[switch_index])
        start_bar = int(segments.start_indices[segment_index])
        end_bar = int(segments.end_indices[segment_index])
        try:
            start_block = block_by_bar[start_bar]
            end_block = block_by_bar[end_bar]
        except KeyError as exc:  # pragma: no cover - invariante da análise
            raise ValueError(
                "Uma chave de fronteira referencia barra sem bloco."
            ) from exc
        switch = switches.record(switch_index)
        edges.append(
            BlockGraphEdge(
                switch_index=switch_index,
                switch_id=switch.switch_id,
                switch_code=switch.code,
                state=switch.state,
                start_block_id=start_block,
                end_block_id=end_block,
            )
        )
    return BlockGraph(records, tuple(edges))


def layout_block_graph(graph: BlockGraph) -> BlockGraphLayout:
    """Organiza o multigrafo como floresta em camadas, sem omitir arestas.

    Cada componente usa todos os seus blocos-fonte como raízes simultâneas. Um
    componente sem fonte usa o menor ``block_id``. A BFS determina profundidade
    e pais; uma passada pelas árvores posiciona pais sobre o centro dos filhos.
    """

    node_ids = tuple(sorted(graph.node_ids))
    if not node_ids:
        return BlockGraphLayout({}, {}, (), frozenset())

    records = {record.block_id: record for record in graph.nodes}
    adjacency: dict[int, list[tuple[int, int]]] = {
        block_id: [] for block_id in node_ids
    }
    for edge_index, edge in enumerate(graph.edges):
        if edge.start_block_id == edge.end_block_id:
            continue
        adjacency[edge.start_block_id].append((edge.end_block_id, edge_index))
        adjacency[edge.end_block_id].append((edge.start_block_id, edge_index))
    for entries in adjacency.values():
        entries.sort(key=lambda value: (value[0], value[1]))

    components: list[tuple[int, ...]] = []
    unseen = set(node_ids)
    while unseen:
        first = min(unseen)
        stack = [first]
        component: list[int] = []
        unseen.remove(first)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor, _edge_index in reversed(adjacency[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(tuple(sorted(component)))

    positions: dict[int, tuple[float, float]] = {}
    depths: dict[int, int] = {}
    all_roots: list[int] = []
    tree_edges: set[int] = set()
    global_left = 0.0

    for component in components:
        component_set = set(component)
        roots = tuple(
            block_id
            for block_id in component
            if records[block_id].contains_source
        ) or (component[0],)
        all_roots.extend(roots)

        parent: dict[int, int | None] = {root: None for root in roots}
        depth: dict[int, int] = {root: 0 for root in roots}
        queue = deque(roots)
        while queue:
            current = queue.popleft()
            for neighbor, edge_index in adjacency[current]:
                if neighbor not in component_set or neighbor in depth:
                    continue
                parent[neighbor] = current
                depth[neighbor] = depth[current] + 1
                tree_edges.add(edge_index)
                queue.append(neighbor)

        # A componente é conexa pela construção acima. A salvaguarda mantém o
        # layout total mesmo se um grafo manual inconsistente chegar aos testes.
        for block_id in component:
            if block_id not in depth:
                parent[block_id] = None
                depth[block_id] = 0
                all_roots.append(block_id)

        children: dict[int, list[int]] = {block_id: [] for block_id in component}
        for child, owner in parent.items():
            if owner is not None:
                children[owner].append(child)
        for values in children.values():
            values.sort()

        local_x: dict[int, float] = {}
        next_leaf = 0.0

        def place(block_id: int) -> float:
            nonlocal next_leaf
            descendants = children[block_id]
            if not descendants:
                value = next_leaf
                next_leaf += HORIZONTAL_NODE_GAP
            else:
                child_x = [place(child) for child in descendants]
                value = (child_x[0] + child_x[-1]) / 2.0
            local_x[block_id] = value
            return value

        for root in roots:
            place(root)
        for block_id in component:
            if block_id not in local_x:
                place(block_id)

        minimum_x = min(local_x.values())
        maximum_x = max(local_x.values())
        for block_id in component:
            positions[block_id] = (
                global_left + local_x[block_id] - minimum_x,
                depth[block_id] * VERTICAL_NODE_GAP,
            )
            depths[block_id] = depth[block_id]
        width = max(maximum_x - minimum_x, HORIZONTAL_NODE_GAP)
        global_left += width + COMPONENT_GAP

    return BlockGraphLayout(
        positions=positions,
        depths=depths,
        root_ids=tuple(all_roots),
        tree_edge_indices=frozenset(tree_edges),
    )


def block_coordinate_anchors(
    result: BlockAnalysisResult,
) -> dict[int, tuple[float, float]]:
    """Calcula uma âncora espacial para cada bloco a partir das suas fronteiras.

    Cada barra na ponta de uma chave conta no máximo uma vez por bloco. Blocos
    sem chave de fronteira usam o centroide de todas as suas barras. O eixo Y é
    invertido aqui para seguir a mesma orientação norte-acima do mapa principal.
    Um dicionário vazio indica que a geometria de origem não está disponível ou
    é inconsistente e permite que a interface desabilite o modo espacial.
    """

    records = tuple(result.records)
    if not records:
        return {}
    segments = result.source_segments
    switches = result.source_switches
    if segments is None:
        return {}
    bars = getattr(segments, "bars", None)
    if bars is None:
        return {}

    block_by_bar: dict[int, int] = {}
    boundary_bars: dict[int, set[int]] = {
        record.block_id: set() for record in records
    }
    for record in records:
        for raw_bar_index in record.bar_indices:
            block_by_bar[int(raw_bar_index)] = record.block_id

    try:
        if switches is not None:
            boundary_switches = {
                int(raw_switch_index)
                for record in records
                for raw_switch_index in record.boundary_switch_indices
            }
            for switch_index in sorted(boundary_switches):
                segment_index = int(switches.segment_indices[switch_index])
                for raw_bar_index in (
                    segments.start_indices[segment_index],
                    segments.end_indices[segment_index],
                ):
                    bar_index = int(raw_bar_index)
                    block_id = block_by_bar.get(bar_index)
                    if block_id is not None:
                        boundary_bars[block_id].add(bar_index)

        anchors: dict[int, tuple[float, float]] = {}
        for record in records:
            indices = boundary_bars[record.block_id]
            if not indices:
                indices = {int(value) for value in record.bar_indices}
            ordered = sorted(indices)
            x = math.fsum(float(bars.x[index]) for index in ordered) / len(ordered)
            y = -math.fsum(float(bars.y[index]) for index in ordered) / len(ordered)
            if not math.isfinite(x) or not math.isfinite(y):
                return {}
            anchors[record.block_id] = (x, y)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {}
    return anchors


def _coordinate_scale(
    graph: BlockGraph,
    anchors: dict[int, tuple[float, float]],
) -> float:
    lengths: list[float] = []
    measured_pairs: set[tuple[int, int]] = set()
    for edge in graph.edges:
        if edge.start_block_id == edge.end_block_id:
            continue
        if edge.endpoint_key in measured_pairs:
            continue
        measured_pairs.add(edge.endpoint_key)
        start = anchors[edge.start_block_id]
        end = anchors[edge.end_block_id]
        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        if distance > 1.0e-9 and math.isfinite(distance):
            lengths.append(distance)
    if not lengths:
        ordered = sorted(anchors.values())
        for start, end in zip(ordered, ordered[1:]):
            distance = math.hypot(end[0] - start[0], end[1] - start[1])
            if distance > 1.0e-9 and math.isfinite(distance):
                lengths.append(distance)
    return (
        GEOGRAPHIC_TARGET_EDGE_LENGTH / median(lengths)
        if lengths
        else 1.0
    )


def _separate_coordinate_overlaps(
    positions: dict[int, tuple[float, float]],
    diameters: dict[int, float],
) -> dict[int, tuple[float, float]]:
    """Move deterministicamente apenas o nó cujo envelope colide ao inserir."""

    placed: dict[int, tuple[float, float]] = {}
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    for block_id in sorted(positions):
        anchor_x, anchor_y = positions[block_id]
        diameter = max(0.0, float(diameters.get(block_id, FIXED_NODE_DIAMETER)))

        def available(candidate_x: float, candidate_y: float) -> bool:
            for other_id, (other_x, other_y) in placed.items():
                other_diameter = max(
                    0.0,
                    float(diameters.get(other_id, FIXED_NODE_DIAMETER)),
                )
                minimum = (
                    (diameter + other_diameter) / 2.0
                    + GEOGRAPHIC_NODE_CLEARANCE
                )
                if math.hypot(candidate_x - other_x, candidate_y - other_y) < minimum:
                    return False
            return True

        if available(anchor_x, anchor_y):
            placed[block_id] = (anchor_x, anchor_y)
            continue

        step = max(diameter, MIN_NODE_DIAMETER) / 2.0 + GEOGRAPHIC_NODE_CLEARANCE
        attempt = 1
        while True:
            radius = step * math.sqrt(attempt)
            angle = attempt * golden_angle
            candidate = (
                anchor_x + radius * math.cos(angle),
                anchor_y + radius * math.sin(angle),
            )
            if available(*candidate):
                placed[block_id] = candidate
                break
            attempt += 1

    center_x = math.fsum(value[0] for value in placed.values()) / len(placed)
    center_y = math.fsum(value[1] for value in placed.values()) / len(placed)
    return {
        block_id: (x - center_x, y - center_y)
        for block_id, (x, y) in placed.items()
    }


def layout_block_graph_by_coordinates(
    graph: BlockGraph,
    anchors: dict[int, tuple[float, float]],
    diameters: dict[int, float] | None = None,
) -> BlockGraphLayout:
    """Preserva a forma espacial por uma transformação uniforme e sem rotação."""

    node_ids = tuple(sorted(graph.node_ids))
    if not node_ids:
        return BlockGraphLayout({}, {}, (), frozenset())
    if any(block_id not in anchors for block_id in node_ids):
        raise ValueError("Todos os blocos precisam de uma âncora espacial.")
    if any(
        not math.isfinite(float(value))
        for block_id in node_ids
        for value in anchors[block_id]
    ):
        raise ValueError("As âncoras espaciais devem ser finitas.")

    center_x = math.fsum(anchors[block_id][0] for block_id in node_ids) / len(node_ids)
    center_y = math.fsum(anchors[block_id][1] for block_id in node_ids) / len(node_ids)
    scale = _coordinate_scale(graph, anchors)
    normalized = {
        block_id: (
            (anchors[block_id][0] - center_x) * scale,
            (anchors[block_id][1] - center_y) * scale,
        )
        for block_id in node_ids
    }
    positions = _separate_coordinate_overlaps(
        normalized,
        diameters
        or {block_id: FIXED_NODE_DIAMETER for block_id in node_ids},
    )
    roots = tuple(
        record.block_id for record in graph.nodes if record.contains_source
    )
    return BlockGraphLayout(
        positions=positions,
        depths={},
        root_ids=roots,
        # No modo espacial, uma aresta simples não precisa da curvatura usada
        # para denunciar as relações que ficaram fora da floresta geradora.
        tree_edge_indices=frozenset(range(len(graph.edges))),
    )


def block_node_diameters(
    records: tuple[BlockRecord, ...],
    scale_by_power: bool,
) -> dict[int, float]:
    """Calcula diâmetros fixos ou com área proporcional à potência."""

    if not scale_by_power:
        return {record.block_id: FIXED_NODE_DIAMETER for record in records}

    powers = {
        record.block_id: max(0.0, float(record.total_power or 0.0))
        for record in records
    }
    maximum = max(powers.values(), default=0.0)
    if maximum <= 0.0:
        return {record.block_id: MIN_NODE_DIAMETER for record in records}

    minimum_area = MIN_NODE_DIAMETER**2
    area_span = MAX_NODE_DIAMETER**2 - minimum_area
    return {
        block_id: math.sqrt(minimum_area + (power / maximum) * area_span)
        if power > 0.0
        else MIN_NODE_DIAMETER
        for block_id, power in powers.items()
    }


def resolve_block_circuit_indices(
    result: BlockAnalysisResult,
    catalog: CircuitCatalogModel,
) -> dict[int, int | None]:
    """Resolve o único circuito de cada bloco sem inventar uma associação.

    A associação elétrica das barras é a fonte principal. Um bloco não
    alcançado no estado atual da rede ainda pode ser identificado quando todas
    as suas chaves de fronteira que declaram circuito concordam entre si.
    Ausência e conflito permanecem explícitos como ``None``.
    """

    block_for_bar: dict[int, int] = {}
    owners: dict[int, set[int]] = {
        record.block_id: set() for record in result.records
    }
    records_by_id = {record.block_id: record for record in result.records}
    for record in result.records:
        for raw_bar_index in record.bar_indices:
            block_for_bar[int(raw_bar_index)] = record.block_id

    for circuit_index, membership in enumerate(catalog.memberships):
        for raw_bar_index in membership.bar_indices:
            block_id = block_for_bar.get(int(raw_bar_index))
            if block_id is not None:
                owners[block_id].add(circuit_index)

    resolved: dict[int, int | None] = {}
    switches = result.source_switches
    for block_id, direct_owners in owners.items():
        if len(direct_owners) == 1:
            resolved[block_id] = next(iter(direct_owners))
            continue
        if direct_owners or switches is None:
            resolved[block_id] = None
            continue

        boundary_owners: set[int] = set()
        for raw_switch_index in records_by_id[block_id].boundary_switch_indices:
            circuit_id = switches.record(int(raw_switch_index)).circuit_id.strip()
            circuit_index = catalog.index_for_id(circuit_id)
            if circuit_index is not None:
                boundary_owners.add(circuit_index)
        resolved[block_id] = (
            next(iter(boundary_owners)) if len(boundary_owners) == 1 else None
        )
    return resolved


__all__ = [
    "BlockGraph",
    "BlockGraphEdge",
    "BlockGraphLayout",
    "BlockGraphLayoutMode",
    "COMPONENT_GAP",
    "FIXED_NODE_DIAMETER",
    "GEOGRAPHIC_NODE_CLEARANCE",
    "GEOGRAPHIC_TARGET_EDGE_LENGTH",
    "HORIZONTAL_NODE_GAP",
    "MAX_NODE_DIAMETER",
    "MIN_NODE_DIAMETER",
    "VERTICAL_NODE_GAP",
    "block_node_diameters",
    "block_coordinate_anchors",
    "build_block_graph",
    "direct_circuit_neighbors",
    "filter_block_graph",
    "layout_block_graph",
    "layout_block_graph_by_coordinates",
    "resolve_block_circuit_indices",
]
