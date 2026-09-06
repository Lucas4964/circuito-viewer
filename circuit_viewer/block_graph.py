"""Modelo e layout determinístico do grafo simplificado de blocos.

Esta camada não importa Qt. Ela transforma o resultado da análise de blocos em
um multigrafo e oferece tanto a floresta em níveis quanto a projeção baseada nas
coordenadas da rede, deixando a interface apenas desenhar e interagir.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
GEOGRAPHIC_TARGET_EDGE_LENGTH = 190.0
GEOGRAPHIC_NODE_CLEARANCE = 24.0
TREE_SIBLING_GAP = 32.0
TREE_BAND_GAP = 56.0
CIRCUIT_GAP = 96.0
PACKED_COMPONENT_GAP = 120.0
MIN_COORDINATE_EDGE_LENGTH = 160.0
MAX_COORDINATE_EDGE_LENGTH = 420.0

Point = tuple[float, float]


class BlockGraphLayoutMode(StrEnum):
    """Formas disponíveis de organizar os blocos no canvas."""

    TREE = "tree"
    GRAPHVIZ_DOT = "graphviz_dot"
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
class BlockNodeEnvelope:
    """Envelope visual usado pelo layout, sem depender dos tipos do Qt.

    ``width`` inclui o círculo e ``height`` pode incluir a legenda de potência.
    A folga entre envelopes é aplicada separadamente pelos algoritmos, para que
    a interface possa informar medidas reais sem inflá-las previamente.
    """

    width: float = FIXED_NODE_DIAMETER
    height: float = FIXED_NODE_DIAMETER + 22.0
    diameter: float | None = None

    def __post_init__(self) -> None:
        if (
            not math.isfinite(float(self.width))
            or not math.isfinite(float(self.height))
            or self.width <= 0.0
            or self.height <= 0.0
            or (
                self.diameter is not None
                and (
                    not math.isfinite(float(self.diameter))
                    or self.diameter <= 0.0
                )
            )
        ):
            raise ValueError("O envelope do nó precisa ter dimensões positivas e finitas.")

    @property
    def node_diameter(self) -> float:
        """Diâmetro real do círculo, separado das legendas do envelope."""

        return (
            min(float(self.width), float(self.height))
            if self.diameter is None
            else float(self.diameter)
        )


@dataclass(frozen=True, slots=True)
class BlockGraphEdgeRoute:
    """Rota lógica de uma chave, indexada externamente por ``switch_index``.

    Duas pontas representam uma reta; três pontas com ``curved=True``
    representam uma curva quadrática; com ``cubic=True``, cada grupo de três
    pontos após o primeiro representa controles e destino de um Bézier cúbico.
    As demais sequências representam uma polilinha. Assim a camada Qt pode
    renderizar rotas ricas sem o núcleo puro conhecer ``QPainterPath``.
    """

    points: tuple[Point, ...]
    curved: bool = False
    cubic: bool = False


@dataclass(frozen=True, slots=True)
class BlockGraphForest:
    """Floresta BFS determinística compartilhada pelos layouts em árvore."""

    depths: dict[int, int]
    root_ids: tuple[int, ...]
    parent_by_node: dict[int, int | None]
    parent_edge_by_node: dict[int, int]
    children_by_node: dict[int, tuple[int, ...]]
    component_by_node: dict[int, int]

    @property
    def tree_edge_indices(self) -> frozenset[int]:
        return frozenset(self.parent_edge_by_node.values())


@dataclass(frozen=True, slots=True)
class BlockGraphLayout:
    """Coordenadas lógicas e metadados da floresta geradora."""

    positions: dict[int, tuple[float, float]]
    depths: dict[int, int]
    root_ids: tuple[int, ...]
    tree_edge_indices: frozenset[int]
    edge_routes: dict[int, BlockGraphEdgeRoute] = field(default_factory=dict)
    edge_label_positions: dict[int, Point] = field(default_factory=dict)
    edge_label_leaders: dict[int, tuple[Point, Point]] = field(
        default_factory=dict
    )


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


def _target_aspect_ratio(value: float) -> float:
    value = float(value)
    return value if math.isfinite(value) and value > 0.05 else 16.0 / 9.0


def _normalize_envelopes(
    node_ids: Sequence[int],
    values: Mapping[int, BlockNodeEnvelope | Sequence[float]] | None,
) -> dict[int, BlockNodeEnvelope]:
    normalized: dict[int, BlockNodeEnvelope] = {}
    for block_id in node_ids:
        raw = None if values is None else values.get(block_id)
        if raw is None:
            normalized[block_id] = BlockNodeEnvelope()
        elif isinstance(raw, BlockNodeEnvelope):
            normalized[block_id] = raw
        else:
            normalized[block_id] = BlockNodeEnvelope(
                width=float(raw[0]),
                height=float(raw[1]),
            )
    return normalized


def block_node_envelopes(
    records: Sequence[BlockRecord],
    diameters: Mapping[int, float] | None = None,
    *,
    caption_width: float = 132.0,
    caption_height: float = 22.0,
    caption_gap: float = 4.0,
    outline: float = 3.0,
) -> dict[int, BlockNodeEnvelope]:
    """Cria envelopes consistentes para o círculo e a legenda de potência."""

    caption_width = max(0.0, float(caption_width))
    caption = max(0.0, float(caption_height))
    caption_gap = max(0.0, float(caption_gap))
    outline = max(0.0, float(outline))
    normalized_diameters = {
        record.block_id: max(
            MIN_NODE_DIAMETER,
            float((diameters or {}).get(record.block_id, FIXED_NODE_DIAMETER)),
        )
        for record in records
    }
    return {
        record.block_id: BlockNodeEnvelope(
            width=max(normalized_diameters[record.block_id], caption_width)
            + 2.0 * outline,
            # O círculo permanece centrado no ponto lógico. Reservar a legenda
            # dos dois lados produz um envelope conservador e simétrico, embora
            # ela seja pintada somente abaixo do nó.
            height=normalized_diameters[record.block_id]
            + 2.0 * (caption_gap + caption + outline),
            diameter=normalized_diameters[record.block_id],
        )
        for record in records
    }


def _adjacency_for_edges(
    node_ids: Sequence[int],
    graph: BlockGraph,
    edge_indices: set[int] | frozenset[int] | None = None,
) -> dict[int, list[tuple[int, int]]]:
    """Adjacência simples: paralelas não pesam duas vezes na topologia."""

    allowed = None if edge_indices is None else frozenset(edge_indices)
    adjacency: dict[int, list[tuple[int, int]]] = {
        block_id: [] for block_id in node_ids
    }
    seen_pairs: set[tuple[int, int]] = set()
    for edge_index, edge in enumerate(graph.edges):
        if allowed is not None and edge_index not in allowed:
            continue
        if edge.start_block_id == edge.end_block_id:
            continue
        pair = edge.endpoint_key
        if pair in seen_pairs:
            continue
        if pair[0] not in adjacency or pair[1] not in adjacency:
            continue
        seen_pairs.add(pair)
        adjacency[pair[0]].append((pair[1], edge_index))
        adjacency[pair[1]].append((pair[0], edge_index))
    for entries in adjacency.values():
        entries.sort(key=lambda value: (value[0], value[1]))
    return adjacency


def _connected_components(
    node_ids: Sequence[int],
    adjacency: Mapping[int, Sequence[tuple[int, int]]],
) -> tuple[tuple[int, ...], ...]:
    unseen = set(node_ids)
    components: list[tuple[int, ...]] = []
    while unseen:
        first = min(unseen)
        queue = deque((first,))
        unseen.remove(first)
        values: list[int] = []
        while queue:
            current = queue.popleft()
            values.append(current)
            for neighbor, _edge_index in adjacency[current]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        components.append(tuple(sorted(values)))
    return tuple(components)


def _rect_bounds(
    position: Point,
    envelope: BlockNodeEnvelope,
    clearance: float = 0.0,
) -> tuple[float, float, float, float]:
    half_width = envelope.width / 2.0 + clearance
    half_height = envelope.height / 2.0 + clearance
    return (
        position[0] - half_width,
        position[1] - half_height,
        position[0] + half_width,
        position[1] + half_height,
    )


def _layout_bounds(
    positions: Mapping[int, Point],
    envelopes: Mapping[int, BlockNodeEnvelope],
) -> tuple[float, float, float, float]:
    if not positions:
        return (0.0, 0.0, 0.0, 0.0)
    rectangles = [
        _rect_bounds(position, envelopes[block_id])
        for block_id, position in positions.items()
    ]
    return (
        min(rect[0] for rect in rectangles),
        min(rect[1] for rect in rectangles),
        max(rect[2] for rect in rectangles),
        max(rect[3] for rect in rectangles),
    )


def _barycentric_level_order(
    levels: Mapping[int, Sequence[int]],
    adjacency: Mapping[int, Sequence[tuple[int, int]]],
    depths: Mapping[int, int],
) -> dict[int, list[int]]:
    """Executa varreduras Sugiyama simples com desempate determinístico."""

    ordered = {depth: sorted(values) for depth, values in levels.items()}
    maximum_depth = max(ordered, default=0)
    for _iteration in range(5):
        for depth in range(1, maximum_depth + 1):
            previous = ordered.get(depth - 1, [])
            rank = {block_id: index for index, block_id in enumerate(previous)}

            def downward_key(block_id: int) -> tuple[float, int]:
                owners = [
                    rank[neighbor]
                    for neighbor, _edge_index in adjacency[block_id]
                    if neighbor in rank
                ]
                return (
                    math.fsum(owners) / len(owners) if owners else math.inf,
                    block_id,
                )

            ordered.setdefault(depth, []).sort(key=downward_key)
        for depth in range(maximum_depth - 1, -1, -1):
            following = ordered.get(depth + 1, [])
            rank = {block_id: index for index, block_id in enumerate(following)}

            def upward_key(block_id: int) -> tuple[float, int]:
                children = [
                    rank[neighbor]
                    for neighbor, _edge_index in adjacency[block_id]
                    if neighbor in rank
                ]
                return (
                    math.fsum(children) / len(children)
                    if children
                    else math.inf,
                    block_id,
                )

            ordered.setdefault(depth, []).sort(key=upward_key)
    return ordered


def _tidy_forest_x_positions(
    roots: Sequence[int],
    children: Mapping[int, Sequence[int]],
    envelopes: Mapping[int, BlockNodeEnvelope],
    component_for_node: Mapping[int, int],
) -> dict[int, float]:
    """Distribui uma floresta por contornos como a etapa horizontal do dot.

    Esta é uma implementação iterativa de Buchheim/Walker para árvores
    ordenadas. Os modificadores preservam intervalos contínuos de subárvores e
    aproximam irmãos somente até seus contornos deixarem de se interceptar.
    Uma raiz virtual organiza várias fontes e componentes sem acrescentar um
    nível visível.
    """

    if not roots:
        return {}

    virtual_root = object()
    hierarchy_children: dict[object, tuple[object, ...]] = {
        block_id: tuple(children.get(block_id, ())) for block_id in children
    }
    hierarchy_children[virtual_root] = tuple(roots)
    hierarchy_parent: dict[object, object | None] = {virtual_root: None}
    left_sibling: dict[object, object | None] = {virtual_root: None}
    sibling_number: dict[object, int] = {virtual_root: 1}
    for owner, descendants in hierarchy_children.items():
        previous: object | None = None
        for number, descendant in enumerate(descendants, start=1):
            hierarchy_parent[descendant] = owner
            left_sibling[descendant] = previous
            sibling_number[descendant] = number
            previous = descendant

    prelim: dict[object, float] = defaultdict(float)
    modifier: dict[object, float] = defaultdict(float)
    change: dict[object, float] = defaultdict(float)
    shift: dict[object, float] = defaultdict(float)
    thread: dict[object, object | None] = defaultdict(lambda: None)
    ancestor: dict[object, object] = {
        node: node for node in hierarchy_children
    }

    def next_left(node: object) -> object | None:
        descendants = hierarchy_children[node]
        return descendants[0] if descendants else thread[node]

    def next_right(node: object) -> object | None:
        descendants = hierarchy_children[node]
        return descendants[-1] if descendants else thread[node]

    def node_width(node: object) -> float:
        return (
            0.0
            if node is virtual_root
            else envelopes[int(node)].width
        )

    def separation(left: object, right: object) -> float:
        gap = TREE_SIBLING_GAP
        if (
            left is not virtual_root
            and right is not virtual_root
            and component_for_node[int(left)] != component_for_node[int(right)]
        ):
            gap = PACKED_COMPONENT_GAP
        return (node_width(left) + node_width(right)) / 2.0 + gap

    def move_subtree(left: object, right: object, amount: float) -> None:
        count = sibling_number[right] - sibling_number[left]
        if count <= 0:  # pragma: no cover - invariante do Walker
            return
        ratio = amount / count
        change[right] -= ratio
        shift[right] += amount
        change[left] += ratio
        prelim[right] += amount
        modifier[right] += amount

    def owning_ancestor(
        inner_left: object,
        node: object,
        default_ancestor: object,
    ) -> object:
        candidate = ancestor[inner_left]
        if hierarchy_parent.get(candidate) == hierarchy_parent.get(node):
            return candidate
        return default_ancestor

    def apportion(node: object, default_ancestor: object) -> object:
        sibling = left_sibling[node]
        if sibling is None:
            return default_ancestor
        inner_right = node
        outer_right = node
        inner_left = sibling
        owner = hierarchy_parent[node]
        assert owner is not None
        outer_left = hierarchy_children[owner][0]
        inner_right_mod = modifier[inner_right]
        outer_right_mod = modifier[outer_right]
        inner_left_mod = modifier[inner_left]
        outer_left_mod = modifier[outer_left]
        while (
            next_right(inner_left) is not None
            and next_left(inner_right) is not None
        ):
            next_inner_left = next_right(inner_left)
            next_inner_right = next_left(inner_right)
            next_outer_left = next_left(outer_left)
            next_outer_right = next_right(outer_right)
            assert next_inner_left is not None
            assert next_inner_right is not None
            if next_outer_left is None or next_outer_right is None:  # pragma: no cover
                break
            inner_left = next_inner_left
            inner_right = next_inner_right
            outer_left = next_outer_left
            outer_right = next_outer_right
            ancestor[outer_right] = node
            amount = (
                prelim[inner_left]
                + inner_left_mod
                - prelim[inner_right]
                - inner_right_mod
                + separation(inner_left, inner_right)
            )
            if amount > 0.0:
                owner_ancestor = owning_ancestor(
                    inner_left,
                    node,
                    default_ancestor,
                )
                move_subtree(owner_ancestor, node, amount)
                inner_right_mod += amount
                outer_right_mod += amount
            inner_left_mod += modifier[inner_left]
            inner_right_mod += modifier[inner_right]
            outer_left_mod += modifier[outer_left]
            outer_right_mod += modifier[outer_right]
        remaining_right = next_right(inner_left)
        if remaining_right is not None and next_right(outer_right) is None:
            thread[outer_right] = remaining_right
            modifier[outer_right] += inner_left_mod - outer_right_mod
        remaining_left = next_left(inner_right)
        if remaining_left is not None and next_left(outer_left) is None:
            thread[outer_left] = remaining_left
            modifier[outer_left] += inner_right_mod - outer_left_mod
            default_ancestor = node
        return default_ancestor

    def execute_shifts(node: object) -> None:
        accumulated_shift = 0.0
        accumulated_change = 0.0
        for descendant in reversed(hierarchy_children[node]):
            prelim[descendant] += accumulated_shift
            modifier[descendant] += accumulated_shift
            accumulated_change += change[descendant]
            accumulated_shift += shift[descendant] + accumulated_change

    # Eventos explícitos evitam estouro de recursão em alimentadores longos.
    defaults: dict[object, object] = {}
    events: list[tuple[str, object, object | None]] = [
        ("enter", virtual_root, None)
    ]
    while events:
        event, node, child = events.pop()
        descendants = hierarchy_children[node]
        if event == "enter":
            if not descendants:
                sibling = left_sibling[node]
                if sibling is not None:
                    prelim[node] = prelim[sibling] + separation(sibling, node)
                continue
            defaults[node] = descendants[0]
            events.append(("finish", node, None))
            for descendant in reversed(descendants):
                events.append(("after", node, descendant))
                events.append(("enter", descendant, None))
            continue
        if event == "after":
            assert child is not None
            defaults[node] = apportion(child, defaults[node])
            continue
        execute_shifts(node)
        midpoint = (prelim[descendants[0]] + prelim[descendants[-1]]) / 2.0
        sibling = left_sibling[node]
        if sibling is None:
            prelim[node] = midpoint
        else:
            prelim[node] = prelim[sibling] + separation(sibling, node)
            modifier[node] = prelim[node] - midpoint

    positions: dict[int, float] = {}
    traversal: list[tuple[object, float]] = [(virtual_root, 0.0)]
    while traversal:
        node, inherited_modifier = traversal.pop()
        total_modifier = inherited_modifier + modifier[node]
        if node is not virtual_root:
            positions[int(node)] = prelim[node] + inherited_modifier
        traversal.extend(
            (descendant, total_modifier)
            for descendant in reversed(hierarchy_children[node])
        )
    return positions


def build_block_graph_forest(
    graph: BlockGraph,
    block_ids: Sequence[int] | None = None,
    edge_indices: set[int] | frozenset[int] | None = None,
    records: Mapping[int, BlockRecord] | None = None,
    root_eligible: set[int] | None = None,
) -> BlockGraphForest:
    """Constrói a floresta BFS determinística usada pelos layouts em árvore.

    Paralelas e autoenlaces não pesam na topologia. As arestas secundárias
    ainda orientam as varreduras baricêntricas e o desempate de pais, fazendo
    com que o layout interno e o adaptador Graphviz partam da mesma hierarquia.
    """

    normalized_ids = tuple(
        sorted(graph.node_ids if block_ids is None else block_ids)
    )
    if not normalized_ids:
        return BlockGraphForest({}, (), {}, {}, {}, {})
    record_map = (
        {record.block_id: record for record in graph.nodes}
        if records is None
        else records
    )
    adjacency = _adjacency_for_edges(normalized_ids, graph, edge_indices)
    components = _connected_components(normalized_ids, adjacency)
    depths: dict[int, int] = {}
    roots: list[int] = []
    component_for_node: dict[int, int] = {}

    for component_index, component in enumerate(components):
        component_for_node.update(
            (block_id, component_index) for block_id in component
        )
        eligible = (
            set(component)
            if root_eligible is None
            else set(component) & root_eligible
        )
        component_roots = tuple(
            block_id
            for block_id in component
            if block_id in eligible and record_map[block_id].contains_source
        )
        if not component_roots:
            component_roots = (min(eligible or set(component)),)
        roots.extend(component_roots)

        # A busca calcula somente ranks. A escolha das arestas da floresta é
        # adiada até existir uma ordem baricêntrica para resolver ambiguidades
        # de ciclos sem depender da ordem de entrada das chaves.
        component_depth = {root: 0 for root in component_roots}
        queue = deque(component_roots)
        while queue:
            current = queue.popleft()
            for neighbor, _edge_index in adjacency[current]:
                if neighbor in component_depth:
                    continue
                component_depth[neighbor] = component_depth[current] + 1
                queue.append(neighbor)
        depths.update(component_depth)

    levels: dict[int, list[int]] = defaultdict(list)
    for block_id in normalized_ids:
        levels[depths[block_id]].append(block_id)
    swept_order = _barycentric_level_order(levels, adjacency, depths)
    swept_rank = {
        block_id: index
        for depth in swept_order
        for index, block_id in enumerate(swept_order[depth])
    }
    rank_fraction = {
        block_id: (
            0.5
            if len(swept_order[depths[block_id]]) <= 1
            else swept_rank[block_id]
            / (len(swept_order[depths[block_id]]) - 1)
        )
        for block_id in normalized_ids
    }

    parents: dict[int, int | None] = {root: None for root in roots}
    parent_edges: dict[int, int] = {}
    for block_id in sorted(normalized_ids, key=lambda value: (depths[value], value)):
        if depths[block_id] == 0:
            continue
        candidates = [
            (neighbor, edge_index)
            for neighbor, edge_index in adjacency[block_id]
            if depths[neighbor] == depths[block_id] - 1
        ]
        owner, edge_index = min(
            candidates,
            key=lambda value: (
                abs(rank_fraction[value[0]] - rank_fraction[block_id]),
                swept_rank[value[0]],
                value[0],
                value[1],
            ),
        )
        parents[block_id] = owner
        parent_edges[block_id] = edge_index

    children: dict[int, list[int]] = {block_id: [] for block_id in normalized_ids}
    for block_id, owner in parents.items():
        if owner is not None:
            children[owner].append(block_id)

    # Use a posição desejada de toda a descendência como sinal de ordenação.
    # Isso deixa ciclos reduzirem cruzamentos sem intercalar duas famílias.
    subtree_signal: dict[int, tuple[float, int]] = {}
    for block_id in sorted(normalized_ids, key=lambda value: (-depths[value], value)):
        total = rank_fraction[block_id] * 2.0
        weight = 2
        for descendant in children[block_id]:
            child_total, child_weight = subtree_signal[descendant]
            total += child_total
            weight += child_weight
        subtree_signal[block_id] = (total, weight)
    for descendants in children.values():
        descendants.sort(
            key=lambda block_id: (
                subtree_signal[block_id][0] / subtree_signal[block_id][1],
                swept_rank[block_id],
                block_id,
            )
        )
    roots.sort(
        key=lambda block_id: (
            component_for_node[block_id],
            subtree_signal[block_id][0] / subtree_signal[block_id][1],
            swept_rank[block_id],
            block_id,
        )
    )

    return BlockGraphForest(
        depths=depths,
        root_ids=tuple(roots),
        parent_by_node=parents,
        parent_edge_by_node=parent_edges,
        children_by_node={
            block_id: tuple(descendants)
            for block_id, descendants in children.items()
        },
        component_by_node=component_for_node,
    )


def _layout_tree_cluster(
    graph: BlockGraph,
    block_ids: Sequence[int],
    edge_indices: set[int],
    envelopes: Mapping[int, BlockNodeEnvelope],
    records: Mapping[int, BlockRecord],
    target_aspect_ratio: float,
    root_eligible: set[int] | None = None,
) -> tuple[dict[int, Point], dict[int, int], tuple[int, ...], set[int]]:
    """Constrói uma floresta hierárquica Sugiyama/tidy para um circuito."""

    forest = build_block_graph_forest(
        graph,
        block_ids,
        edge_indices,
        records,
        root_eligible,
    )
    depths = forest.depths
    roots = list(forest.root_ids)
    children = forest.children_by_node
    component_for_node = forest.component_by_node
    levels: dict[int, list[int]] = defaultdict(list)
    for block_id in block_ids:
        levels[depths[block_id]].append(block_id)

    horizontal = _tidy_forest_x_positions(
        roots,
        children,
        envelopes,
        component_for_node,
    )
    level_heights = {
        depth: max(envelopes[block_id].height for block_id in values)
        for depth, values in levels.items()
    }
    horizontal_left = min(
        horizontal[block_id] - envelopes[block_id].width / 2.0
        for block_id in block_ids
    )
    horizontal_right = max(
        horizontal[block_id] + envelopes[block_id].width / 2.0
        for block_id in block_ids
    )
    maximum_depth = max(level_heights, default=0)
    # Uma árvore larga projetada com ranksep mínimo volta a parecer uma fileira
    # horizontal depois do auto-fit. Aumentar moderadamente o espaço *entre*
    # ranks usa melhor um viewport largo sem quebrar camadas nem alongar uma
    # cadeia estreita. O limite evita o salto exagerado de uma estrela rasa.
    vertical_gap = TREE_BAND_GAP
    if maximum_depth >= 2:
        horizontal_span = horizontal_right - horizontal_left
        desired_aspect = max(2.4, target_aspect_ratio * 1.35)
        desired_height = horizontal_span / desired_aspect
        envelope_height = math.fsum(level_heights.values())
        vertical_gap = min(
            240.0,
            max(
                TREE_BAND_GAP,
                (desired_height - envelope_height) / maximum_depth,
            ),
        )
    level_y: dict[int, float] = {}
    cursor_y = 0.0
    previous_height = 0.0
    for depth in sorted(level_heights):
        height = level_heights[depth]
        if depth == 0:
            cursor_y = height / 2.0
        else:
            cursor_y += previous_height / 2.0 + vertical_gap + height / 2.0
        level_y[depth] = cursor_y
        previous_height = height
    positions = {
        block_id: (horizontal[block_id], level_y[depths[block_id]])
        for block_id in block_ids
    }

    bounds = _layout_bounds(positions, envelopes)
    center_x = (bounds[0] + bounds[2]) / 2.0
    center_y = (bounds[1] + bounds[3]) / 2.0
    return (
        {
            block_id: (value[0] - center_x, value[1] - center_y)
            for block_id, value in positions.items()
        },
        depths,
        tuple(roots),
        set(forest.tree_edge_indices),
    )


def _cluster_sort_key(value: tuple[str, int]) -> tuple[int, int]:
    return (0 if value[0] == "circuit" else 1, value[1])


def _optimize_meta_grid_order(
    traversal: Sequence[tuple[str, int]],
    meta_adjacency: Mapping[tuple[str, int], set[tuple[str, int]]],
    columns: int,
    horizontal_step: float,
    vertical_step: float,
    *,
    restart_count: int | None = None,
    connection_endpoints: Sequence[
        tuple[tuple[str, int], Point, tuple[str, int], Point]
    ] = (),
) -> tuple[tuple[str, int], ...]:
    """Atribui circuitos a uma grade minimizando ligações extremas.

    Uma ordem serpentina é boa para uma cadeia, mas transforma qualquer
    atalho entre linhas em uma aresta que atravessa toda a janela. Este ajuste
    discreto troca pares de slots e minimiza primeiro o maior comprimento do
    metagrafo, depois sua cauda e seu comprimento total. Para redes grandes o
    conjunto de candidatos é limitado a circuitos incidentes nas piores
    ligações, mantendo o custo previsível.
    """

    ordered = tuple(traversal)
    if len(ordered) <= 2:
        return ordered
    if len(ordered) > 64:
        # A matriz de custos por par de slots é deliberadamente reservada
        # ao caso interativo comum. Em catálogos muito grandes, o objetivo por
        # centros continua linear em arestas e evita memória O(E * C²).
        connection_endpoints = ()
    slots = tuple(
        (
            float(index % columns) * horizontal_step,
            float(index // columns) * vertical_step,
        )
        for index in range(len(ordered))
    )
    connections = tuple(
        (key, neighbor)
        for key in sorted(ordered, key=_cluster_sort_key)
        for neighbor in sorted(meta_adjacency[key], key=_cluster_sort_key)
        if _cluster_sort_key(key) < _cluster_sort_key(neighbor)
    )
    if not connections:
        return ordered

    endpoint_costs: tuple[tuple[tuple[float, ...], ...], ...] = ()
    if connection_endpoints:
        endpoint_costs = tuple(
            tuple(
                tuple(
                    min(
                        math.dist(
                            (
                                start_center[0]
                                + (-start_local[0] if start_flipped else start_local[0]),
                                start_center[1] + start_local[1],
                            ),
                            (
                                end_center[0]
                                + (-end_local[0] if end_flipped else end_local[0]),
                                end_center[1] + end_local[1],
                            ),
                        )
                        for start_flipped in (False, True)
                        for end_flipped in (False, True)
                    )
                    for end_center in slots
                )
                for start_center in slots
            )
            for _start, start_local, _end, end_local in connection_endpoints
        )

    def score(values: Sequence[tuple[str, int]]) -> tuple[float, ...]:
        slot_for = {key: slots[index] for index, key in enumerate(values)}
        if connection_endpoints:
            index_for = {key: index for index, key in enumerate(values)}
            lengths = [
                endpoint_costs[edge_index][index_for[start]][index_for[end]]
                for edge_index, (start, _start_local, end, _end_local) in enumerate(
                    connection_endpoints
                )
            ]
            lengths.sort(reverse=True)
        else:
            lengths = sorted(
                (
                    math.dist(slot_for[start], slot_for[end])
                    for start, end in connections
                ),
                reverse=True,
            )
        # A cauda curta evita que uma melhoria minúscula no máximo espalhe
        # todas as demais interligações. O termo final estabiliza empates.
        tail = lengths[: min(12, len(lengths))]
        return (*tail, math.fsum(lengths))

    def improve(initial: Sequence[tuple[str, int]]) -> tuple[
        tuple[float, ...], tuple[tuple[str, int], ...]
    ]:
        assignment = list(initial)
        current_score = score(assignment)
        maximum_passes = 18 if len(assignment) <= 64 else 6
        for _iteration in range(maximum_passes):
            best_score = current_score
            best_swap: tuple[int, int] | None = None
            if len(assignment) <= 64:
                candidate_indices = tuple(range(len(assignment)))
            else:
                slot_for = {
                    key: slots[index] for index, key in enumerate(assignment)
                }
                worst = sorted(
                    connections,
                    key=lambda edge: (
                        -math.dist(slot_for[edge[0]], slot_for[edge[1]]),
                        _cluster_sort_key(edge[0]),
                        _cluster_sort_key(edge[1]),
                    ),
                )[:16]
                involved = {key for edge in worst for key in edge}
                candidate_indices = tuple(
                    index
                    for index, key in enumerate(assignment)
                    if key in involved
                )
            for left_position, left in enumerate(candidate_indices):
                for right in candidate_indices[left_position + 1 :]:
                    assignment[left], assignment[right] = (
                        assignment[right],
                        assignment[left],
                    )
                    candidate_score = score(assignment)
                    assignment[left], assignment[right] = (
                        assignment[right],
                        assignment[left],
                    )
                    if candidate_score < best_score:
                        best_score = candidate_score
                        best_swap = (left, right)
            if best_swap is None:
                break
            left, right = best_swap
            assignment[left], assignment[right] = assignment[right], assignment[left]
            current_score = best_score
        return current_score, tuple(assignment)

    starts: list[tuple[tuple[str, int], ...]] = [ordered]
    # Trocas gulosas podem ficar presas em um arranjo que protege a cadeia e
    # deixa um único atalho extremo. Reinícios pseudoaleatórios, com estado
    # local fixo, exploram outros encaixes sem perder a reprodutibilidade.
    if restart_count is None:
        restart_count = 8 if len(ordered) <= 64 else 2
    for seed in range(1, max(1, restart_count)):
        values = list(ordered)
        state = seed
        for index in range(len(values) - 1, 0, -1):
            state = (1664525 * state + 1013904223) & 0xFFFFFFFF
            other = state % (index + 1)
            values[index], values[other] = values[other], values[index]
        starts.append(tuple(values))
    improved = [improve(values) for values in starts]
    if not connection_endpoints:
        return min(improved)[1]

    def exact_score(values: Sequence[tuple[str, int]]) -> tuple[float, ...]:
        index_for = {key: index for index, key in enumerate(values)}

        def flip_score(flipped: set[tuple[str, int]]) -> tuple[float, ...]:
            lengths: list[float] = []
            for start, start_local, end, end_local in connection_endpoints:
                start_center = slots[index_for[start]]
                end_center = slots[index_for[end]]
                lengths.append(
                    math.dist(
                        (
                            start_center[0]
                            + (-start_local[0] if start in flipped else start_local[0]),
                            start_center[1] + start_local[1],
                        ),
                        (
                            end_center[0]
                            + (-end_local[0] if end in flipped else end_local[0]),
                            end_center[1] + end_local[1],
                        ),
                    )
                )
            lengths.sort(reverse=True)
            return (*lengths[: min(12, len(lengths))], math.fsum(lengths))

        flip_starts = (
            set(),
            set(values),
            {key for index, key in enumerate(values) if index % 2},
            {key for index, key in enumerate(values) if index % 2 == 0},
        )
        best: tuple[float, ...] | None = None
        for initial in flip_starts:
            flipped = set(initial)
            current = flip_score(flipped)
            for _iteration in range(8):
                changed = False
                for key in sorted(values, key=_cluster_sort_key):
                    if key in flipped:
                        flipped.remove(key)
                    else:
                        flipped.add(key)
                    candidate = flip_score(flipped)
                    if candidate < current:
                        current = candidate
                        changed = True
                    elif key in flipped:
                        flipped.remove(key)
                    else:
                        flipped.add(key)
                if not changed:
                    break
            if best is None or current < best:
                best = current
        assert best is not None
        return best

    return min(improved, key=lambda value: exact_score(value[1]))[1]


def _horizontal_mirror_choices(
    cluster_positions: Mapping[tuple[str, int], Mapping[int, Point]],
    centers: Mapping[tuple[str, int], Point],
    graph: BlockGraph,
    cluster_for_node: Mapping[int, tuple[str, int]],
) -> set[tuple[str, int]]:
    """Escolhe espelhamentos que minimizam a cauda das interligações."""

    keys = tuple(sorted(centers, key=_cluster_sort_key))

    def endpoint(block_id: int, key: tuple[str, int], flipped: bool) -> Point:
        local = cluster_positions[key][block_id]
        return (
            centers[key][0] + (-local[0] if flipped else local[0]),
            centers[key][1] + local[1],
        )

    connections: list[BlockGraphEdge] = []
    seen_connections: set[tuple[int, int, tuple[str, int], tuple[str, int]]] = set()
    for edge in graph.edges:
        start_key = cluster_for_node[edge.start_block_id]
        end_key = cluster_for_node[edge.end_block_id]
        if (
            start_key == end_key
            or start_key not in centers
            or end_key not in centers
        ):
            continue
        connection = (
            min(edge.start_block_id, edge.end_block_id),
            max(edge.start_block_id, edge.end_block_id),
            min(start_key, end_key, key=_cluster_sort_key),
            max(start_key, end_key, key=_cluster_sort_key),
        )
        if connection in seen_connections:
            continue
        seen_connections.add(connection)
        connections.append(edge)

    def score(mirrored: set[tuple[str, int]]) -> tuple[float, ...]:
        lengths: list[float] = []
        for edge in connections:
            start_key = cluster_for_node[edge.start_block_id]
            end_key = cluster_for_node[edge.end_block_id]
            lengths.append(
                math.dist(
                    endpoint(
                        edge.start_block_id,
                        start_key,
                        start_key in mirrored,
                    ),
                    endpoint(
                        edge.end_block_id,
                        end_key,
                        end_key in mirrored,
                    ),
                )
            )
        lengths.sort(reverse=True)
        return (*lengths[: min(12, len(lengths))], math.fsum(lengths))

    def improve(initial: set[tuple[str, int]]) -> tuple[
        tuple[float, ...], set[tuple[str, int]]
    ]:
        mirrored = set(initial)
        current = score(mirrored)
        for _iteration in range(8):
            changed = False
            for key in keys:
                if key in mirrored:
                    mirrored.remove(key)
                else:
                    mirrored.add(key)
                candidate = score(mirrored)
                if candidate < current:
                    current = candidate
                    changed = True
                elif key in mirrored:
                    mirrored.remove(key)
                else:
                    mirrored.add(key)
            if not changed:
                break
        return current, mirrored

    candidates = (
        improve(set()),
        improve(set(keys)),
        improve({key for index, key in enumerate(keys) if index % 2}),
        improve({key for index, key in enumerate(keys) if index % 2 == 0}),
    )
    return min(candidates, key=lambda value: value[0])[1]


def _pack_rectangles(
    sizes: Mapping[object, tuple[float, float]],
    *,
    target_aspect_ratio: float,
    gap: float,
) -> dict[object, Point]:
    """Empacota retângulos por prateleiras e escolhe a melhor razão de aspecto."""

    if not sizes:
        return {}
    ordered = sorted(sizes, key=lambda value: str(value))
    total_area = math.fsum(
        (sizes[key][0] + gap) * (sizes[key][1] + gap) for key in ordered
    )
    natural_width = math.sqrt(max(total_area, 1.0) * target_aspect_ratio)
    best: tuple[float, dict[object, Point]] | None = None
    for factor in (0.65, 0.85, 1.0, 1.2, 1.5, 1.9):
        limit = max(max(sizes[key][0] for key in ordered), natural_width * factor)
        cursor_x = 0.0
        cursor_y = 0.0
        row_height = 0.0
        placements: dict[object, Point] = {}
        maximum_x = 0.0
        for key in ordered:
            width, height = sizes[key]
            if cursor_x > 0.0 and cursor_x + width > limit:
                cursor_x = 0.0
                cursor_y += row_height + gap
                row_height = 0.0
            placements[key] = (cursor_x + width / 2.0, cursor_y + height / 2.0)
            cursor_x += width + gap
            row_height = max(row_height, height)
            maximum_x = max(maximum_x, cursor_x - gap)
        total_height = cursor_y + row_height
        actual_aspect = maximum_x / max(total_height, 1.0)
        score = abs(math.log(max(actual_aspect, 1.0e-9) / target_aspect_ratio))
        score += (maximum_x * total_height) / max(total_area, 1.0) * 0.01
        candidate = (score, placements)
        if best is None or candidate[0] < best[0]:
            best = candidate
    assert best is not None
    return best[1]


def _place_circuit_clusters(
    cluster_positions: Mapping[tuple[str, int], Mapping[int, Point]],
    cluster_sizes: Mapping[tuple[str, int], tuple[float, float]],
    graph: BlockGraph,
    cluster_for_node: Mapping[int, tuple[str, int]],
    target_aspect_ratio: float,
    *,
    allow_horizontal_mirroring: bool = False,
    cluster_anchors: Mapping[tuple[str, int], Point] | None = None,
) -> dict[tuple[str, int], Point]:
    """Empacota caixas de circuitos sem alterar sua geometria interna.

    No modo hierarquico, ``allow_horizontal_mirroring`` permite apenas
    translacao e espelhamento em X da caixa completa. As camadas Y locais nunca
    sao inclinadas, escaladas ou relaxadas. As caixas de uma mesma fileira sao
    alinhadas pelo topo para que suas raizes formem uma linha visual comum.
    """

    keys = tuple(sorted(cluster_positions, key=_cluster_sort_key))
    if len(keys) == 1:
        return {keys[0]: (0.0, 0.0)}
    meta_adjacency: dict[tuple[str, int], set[tuple[str, int]]] = {
        key: set() for key in keys
    }
    for edge in graph.edges:
        start = cluster_for_node[edge.start_block_id]
        end = cluster_for_node[edge.end_block_id]
        if start != end:
            meta_adjacency[start].add(end)
            meta_adjacency[end].add(start)

    meta_components: list[tuple[tuple[str, int], ...]] = []
    # No modo espacial a relação geográfica também existe entre
    # componentes eletricamente desconectados. Eles participam do mesmo
    # empacotamento ancorado, em vez de perder norte/leste numa prateleira.
    if cluster_anchors is not None:
        meta_components.append(keys)
    unseen = set(keys)
    for seed in (() if cluster_anchors is not None else keys):
        if seed not in unseen:
            continue
        unseen.remove(seed)
        queue = deque((seed,))
        component: list[tuple[str, int]] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in sorted(meta_adjacency[current], key=_cluster_sort_key):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        meta_components.append(tuple(component))

    relative: dict[tuple[str, int], Point] = {}
    component_sizes: dict[int, tuple[float, float]] = {}
    component_bounds: dict[int, tuple[float, float, float, float]] = {}
    for component_index, component in enumerate(meta_components):
        traversal: list[tuple[str, int]] = []
        visited: set[tuple[str, int]] = set()
        for traversal_seed in component:
            if traversal_seed in visited:
                continue
            stack = [traversal_seed]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                traversal.append(current)
                stack.extend(
                    reversed(
                        sorted(
                            (
                                neighbor
                                for neighbor in meta_adjacency[current]
                                if neighbor in component and neighbor not in visited
                            ),
                            key=_cluster_sort_key,
                        )
                    )
                )
        typical_width = median(cluster_sizes[key][0] for key in component)
        typical_height = median(cluster_sizes[key][1] for key in component)
        typical_box_aspect = typical_width / max(typical_height, 1.0)
        columns = max(
            1,
            round(
                math.sqrt(
                    len(traversal)
                    * target_aspect_ratio
                    / max(typical_box_aspect, 0.1)
                )
            ),
        )
        endpoint_connections: list[
            tuple[tuple[str, int], Point, tuple[str, int], Point]
        ] = []
        seen_endpoint_pairs: set[
            tuple[int, int, tuple[str, int], tuple[str, int]]
        ] = set()
        if allow_horizontal_mirroring:
            component_keys = set(component)
            for edge in graph.edges:
                start_key = cluster_for_node[edge.start_block_id]
                end_key = cluster_for_node[edge.end_block_id]
                if (
                    start_key == end_key
                    or start_key not in component_keys
                    or end_key not in component_keys
                ):
                    continue
                identity = (
                    min(edge.start_block_id, edge.end_block_id),
                    max(edge.start_block_id, edge.end_block_id),
                    min(start_key, end_key, key=_cluster_sort_key),
                    max(start_key, end_key, key=_cluster_sort_key),
                )
                if identity in seen_endpoint_pairs:
                    continue
                seen_endpoint_pairs.add(identity)
                endpoint_connections.append(
                    (
                        start_key,
                        cluster_positions[start_key][edge.start_block_id],
                        end_key,
                        cluster_positions[end_key][edge.end_block_id],
                    )
                )
        serpentine: list[tuple[str, int]] = []
        for row_start in range(0, len(traversal), columns):
            row_values = traversal[row_start : row_start + columns]
            if (row_start // columns) % 2:
                row_values = list(reversed(row_values))
            serpentine.extend(row_values)
        traversal = list(
            _optimize_meta_grid_order(
                serpentine,
                meta_adjacency,
                columns,
                typical_width + CIRCUIT_GAP,
                typical_height + CIRCUIT_GAP,
                restart_count=8 if allow_horizontal_mirroring else None,
                connection_endpoints=endpoint_connections,
            )
        )
        column_widths = [0.0] * columns
        row_count = math.ceil(len(traversal) / columns)
        row_heights = [0.0] * row_count
        slots: dict[tuple[str, int], tuple[int, int]] = {}
        for index, key in enumerate(traversal):
            row = index // columns
            column = index % columns
            slots[key] = (column, row)
            column_widths[column] = max(column_widths[column], cluster_sizes[key][0])
            row_heights[row] = max(row_heights[row], cluster_sizes[key][1])
        column_centers: list[float] = []
        cursor = 0.0
        for width in column_widths:
            column_centers.append(cursor + width / 2.0)
            cursor += width + CIRCUIT_GAP
        row_centers: list[float] = []
        row_tops: list[float] = []
        cursor = 0.0
        vertical_cluster_gap = (
            max(CIRCUIT_GAP, TREE_BAND_GAP * 2.0)
            if allow_horizontal_mirroring
            else CIRCUIT_GAP
        )
        for height in row_heights:
            row_tops.append(cursor)
            row_centers.append(cursor + height / 2.0)
            cursor += height + vertical_cluster_gap
        centers = {
            key: (
                column_centers[slots[key][0]],
                (
                    row_tops[slots[key][1]] + cluster_sizes[key][1] / 2.0
                    if allow_horizontal_mirroring
                    else row_centers[slots[key][1]]
                ),
            )
            for key in component
        }
        geographic_targets: dict[tuple[str, int], Point] | None = None
        if cluster_anchors is not None and len(component) > 1:
            raw_center = (
                median(cluster_anchors[key][0] for key in component),
                median(cluster_anchors[key][1] for key in component),
            )
            radii = [
                math.dist(cluster_anchors[key], raw_center)
                for key in component
                if math.dist(cluster_anchors[key], raw_center) > 1.0e-9
            ]
            reference_radius = median(radii) if radii else 0.0
            if reference_radius > 1.0e-9:
                compressed: dict[tuple[str, int], Point] = {}
                for key in component:
                    dx = cluster_anchors[key][0] - raw_center[0]
                    dy = cluster_anchors[key][1] - raw_center[1]
                    radius = math.hypot(dx, dy)
                    if radius <= 1.0e-12:
                        compressed[key] = (0.0, 0.0)
                    else:
                        compressed_radius = reference_radius * math.sqrt(
                            radius / reference_radius
                        )
                        compressed[key] = (
                            dx / radius * compressed_radius,
                            dy / radius * compressed_radius,
                        )
                connected_lengths = [
                    math.dist(compressed[key], compressed[neighbor])
                    for key in component
                    for neighbor in meta_adjacency[key]
                    if _cluster_sort_key(key) < _cluster_sort_key(neighbor)
                    and math.dist(compressed[key], compressed[neighbor]) > 1.0e-9
                ]
                if not connected_lengths:
                    connected_lengths = [
                        math.dist(compressed[left], compressed[right])
                        for left_index, left in enumerate(component)
                        for right in component[left_index + 1 :]
                        if math.dist(compressed[left], compressed[right]) > 1.0e-9
                    ]
                typical_distance = (
                    median(connected_lengths)
                    if connected_lengths
                    else reference_radius
                )
                typical_spacing = median(
                    math.sqrt(
                        max(cluster_sizes[key][0] * cluster_sizes[key][1], 1.0)
                    )
                    for key in component
                ) + CIRCUIT_GAP
                scale = typical_spacing / max(typical_distance, 1.0e-9)
                grid_center = (
                    median(centers[key][0] for key in component),
                    median(centers[key][1] for key in component),
                )
                geographic_targets = {
                    key: (
                        grid_center[0] + compressed[key][0] * scale,
                        grid_center[1] + compressed[key][1] * scale,
                    )
                    for key in component
                }
                # O grid assegura a ocupação 2-D; a parcela geográfica
                # conserva os quadrantes e bearings sem reabrir distâncias
                # extremas do cadastro original.
                geographic_weight = (
                    0.50
                    if len(component) <= 4
                    else 0.35
                    if len(component) <= 8
                    else 0.20
                )
                centers = {
                    key: (
                        centers[key][0] * (1.0 - geographic_weight)
                        + geographic_targets[key][0] * geographic_weight,
                        centers[key][1] * (1.0 - geographic_weight)
                        + geographic_targets[key][1] * geographic_weight,
                    )
                    for key in component
                }
        force_mirrors = (
            _horizontal_mirror_choices(
                cluster_positions,
                centers,
                graph,
                cluster_for_node,
            )
            if allow_horizontal_mirroring
            else set()
        )

        cross_edges = [
            edge
            for edge in graph.edges
            if cluster_for_node[edge.start_block_id]
            != cluster_for_node[edge.end_block_id]
            and cluster_for_node[edge.start_block_id] in component
            and cluster_for_node[edge.end_block_id] in component
        ]

        # Molas curtas aproximam circuitos relacionados, enquanto a repulsão
        # retangular preserva as caixas locais e o arranjo inicial 2-D.
        anchors = (
            dict(centers)
            if geographic_targets is None
            else dict(geographic_targets)
        )
        relaxation_iterations = 0 if allow_horizontal_mirroring else 48
        for iteration in range(relaxation_iterations):
            movements = {key: [0.0, 0.0] for key in component}
            strength = 0.035 * (1.0 - iteration / 72.0)
            for key in component:
                for neighbor in meta_adjacency[key]:
                    if _cluster_sort_key(neighbor) <= _cluster_sort_key(key):
                        continue
                    dx = centers[neighbor][0] - centers[key][0]
                    dy = centers[neighbor][1] - centers[key][1]
                    distance = max(math.hypot(dx, dy), 1.0e-9)
                    desired = max(
                        (cluster_sizes[key][0] + cluster_sizes[neighbor][0]) / 2.0,
                        (cluster_sizes[key][1] + cluster_sizes[neighbor][1]) / 2.0,
                    ) + CIRCUIT_GAP
                    force = (distance - desired) * strength
                    move_x = dx / distance * force / 2.0
                    move_y = dy / distance * force / 2.0
                    movements[key][0] += move_x
                    movements[key][1] += move_y
                    movements[neighbor][0] -= move_x
                    movements[neighbor][1] -= move_y
            endpoint_strength = 0.10 * (1.0 - iteration / 80.0)
            for edge in cross_edges:
                start_key = cluster_for_node[edge.start_block_id]
                end_key = cluster_for_node[edge.end_block_id]
                start_local = cluster_positions[start_key][edge.start_block_id]
                end_local = cluster_positions[end_key][edge.end_block_id]
                start_point = (
                    centers[start_key][0]
                    + (-start_local[0] if start_key in force_mirrors else start_local[0]),
                    centers[start_key][1] + start_local[1],
                )
                end_point = (
                    centers[end_key][0]
                    + (-end_local[0] if end_key in force_mirrors else end_local[0]),
                    centers[end_key][1] + end_local[1],
                )
                dx = end_point[0] - start_point[0]
                dy = end_point[1] - start_point[1]
                distance = max(math.hypot(dx, dy), 1.0e-9)
                force = min(180.0, (distance - 240.0) * endpoint_strength)
                move_x = dx / distance * force / 2.0
                move_y = dy / distance * force / 2.0
                movements[start_key][0] += move_x
                movements[start_key][1] += move_y
                movements[end_key][0] -= move_x
                movements[end_key][1] -= move_y
            for left_index, left in enumerate(component):
                for right in component[left_index + 1 :]:
                    dx = centers[right][0] - centers[left][0]
                    dy = centers[right][1] - centers[left][1]
                    overlap_x = (
                        (cluster_sizes[left][0] + cluster_sizes[right][0]) / 2.0
                        + CIRCUIT_GAP
                        - abs(dx)
                    )
                    overlap_y = (
                        (cluster_sizes[left][1] + cluster_sizes[right][1]) / 2.0
                        + CIRCUIT_GAP
                        - abs(dy)
                    )
                    if overlap_x <= 0.0 or overlap_y <= 0.0:
                        continue
                    if overlap_x <= overlap_y:
                        direction = 1.0 if dx >= 0.0 else -1.0
                        amount = overlap_x / 2.0 + 0.01
                        movements[left][0] -= direction * amount
                        movements[right][0] += direction * amount
                    else:
                        direction = 1.0 if dy >= 0.0 else -1.0
                        amount = overlap_y / 2.0 + 0.01
                        movements[left][1] -= direction * amount
                        movements[right][1] += direction * amount
            for key in component:
                anchor_strength = 0.002 if geographic_targets is not None else 0.008
                movements[key][0] += (
                    anchors[key][0] - centers[key][0]
                ) * anchor_strength
                movements[key][1] += (
                    anchors[key][1] - centers[key][1]
                ) * anchor_strength
                centers[key] = (
                    centers[key][0] + movements[key][0],
                    centers[key][1] + movements[key][1],
                )

        for _iteration in range(12):
            changed = False
            for left_index, left in enumerate(component):
                for right in component[left_index + 1 :]:
                    dx = centers[right][0] - centers[left][0]
                    dy = centers[right][1] - centers[left][1]
                    overlap_x = (
                        (cluster_sizes[left][0] + cluster_sizes[right][0]) / 2.0
                        + CIRCUIT_GAP
                        - abs(dx)
                    )
                    overlap_y = (
                        (cluster_sizes[left][1] + cluster_sizes[right][1]) / 2.0
                        + CIRCUIT_GAP
                        - abs(dy)
                    )
                    if overlap_x <= 0.0 or overlap_y <= 0.0:
                        continue
                    changed = True
                    if overlap_x <= overlap_y:
                        direction = 1.0 if dx >= 0.0 else -1.0
                        shift = overlap_x / 2.0 + 0.01
                        centers[left] = (centers[left][0] - direction * shift, centers[left][1])
                        centers[right] = (centers[right][0] + direction * shift, centers[right][1])
                    else:
                        direction = 1.0 if dy >= 0.0 else -1.0
                        shift = overlap_y / 2.0 + 0.01
                        centers[left] = (centers[left][0], centers[left][1] - direction * shift)
                        centers[right] = (centers[right][0], centers[right][1] + direction * shift)
            if not changed:
                break

        left = min(centers[key][0] - cluster_sizes[key][0] / 2.0 for key in component)
        top = min(centers[key][1] - cluster_sizes[key][1] / 2.0 for key in component)
        right = max(centers[key][0] + cluster_sizes[key][0] / 2.0 for key in component)
        bottom = max(centers[key][1] + cluster_sizes[key][1] / 2.0 for key in component)
        component_bounds[component_index] = (left, top, right, bottom)
        component_sizes[component_index] = (right - left, bottom - top)
        for key, center in centers.items():
            relative[key] = (center[0] - left, center[1] - top)

    packed = _pack_rectangles(
        component_sizes,
        target_aspect_ratio=target_aspect_ratio,
        gap=PACKED_COMPONENT_GAP,
    )
    placed: dict[tuple[str, int], Point] = {}
    for component_index, component in enumerate(meta_components):
        component_center = packed[component_index]
        width, height = component_sizes[component_index]
        origin = (component_center[0] - width / 2.0, component_center[1] - height / 2.0)
        for key in component:
            placed[key] = (
                origin[0] + relative[key][0],
                origin[1] + relative[key][1],
            )
    return placed


def layout_block_graph(
    graph: BlockGraph,
    *,
    node_envelopes: Mapping[int, BlockNodeEnvelope | Sequence[float]] | None = None,
    block_circuit_indices: Mapping[int, int | None] | None = None,
    selected_circuit_indices: Sequence[int] | frozenset[int] | set[int] = (),
    target_aspect_ratio: float = 16.0 / 9.0,
    edge_label_sizes: Mapping[int, Sequence[float]] | None = None,
) -> BlockGraphLayout:
    """Organiza o grafo em árvores compactas, agrupadas por circuito.

    A API antiga sem argumentos nomeados continua válida. Quando o mapa de
    circuitos é fornecido, cada circuito ganha uma árvore local e as caixas são
    aproximadas por um metagrafo de interligações. Com um único circuito
    selecionado, seus blocos externos aparecem como folhas da mesma árvore.
    """

    node_ids = tuple(sorted(graph.node_ids))
    if not node_ids:
        return BlockGraphLayout({}, {}, (), frozenset())
    aspect = _target_aspect_ratio(target_aspect_ratio)
    envelopes = _normalize_envelopes(node_ids, node_envelopes)
    records = {record.block_id: record for record in graph.nodes}
    circuits = {
        block_id: None
        if block_circuit_indices is None
        else block_circuit_indices.get(block_id)
        for block_id in node_ids
    }
    selected = frozenset(int(value) for value in selected_circuit_indices)
    single_selected = next(iter(selected)) if len(selected) == 1 else None

    external_for_selected: set[int] = set()
    if single_selected is not None and block_circuit_indices is not None:
        for edge in graph.edges:
            start_circuit = circuits[edge.start_block_id]
            end_circuit = circuits[edge.end_block_id]
            if start_circuit == single_selected and end_circuit not in (None, single_selected):
                external_for_selected.add(edge.end_block_id)
            elif end_circuit == single_selected and start_circuit not in (None, single_selected):
                external_for_selected.add(edge.start_block_id)

    cluster_for_node: dict[int, tuple[str, int]] = {}
    for block_id in node_ids:
        circuit = circuits[block_id]
        if block_circuit_indices is None:
            key = ("all", 0)
        elif single_selected is not None and (
            circuit == single_selected or block_id in external_for_selected
        ):
            key = ("circuit", single_selected)
        elif circuit is None:
            key = ("unresolved", 0)
        else:
            key = ("circuit", int(circuit))
        cluster_for_node[block_id] = key

    cluster_nodes: dict[tuple[str, int], list[int]] = defaultdict(list)
    for block_id, key in cluster_for_node.items():
        cluster_nodes[key].append(block_id)
    cluster_edges: dict[tuple[str, int], set[int]] = defaultdict(set)
    for edge_index, edge in enumerate(graph.edges):
        start_key = cluster_for_node[edge.start_block_id]
        if start_key == cluster_for_node[edge.end_block_id]:
            cluster_edges[start_key].add(edge_index)

    local_positions: dict[tuple[str, int], dict[int, Point]] = {}
    cluster_sizes: dict[tuple[str, int], tuple[float, float]] = {}
    depths: dict[int, int] = {}
    roots: list[int] = []
    tree_edges: set[int] = set()
    for key in sorted(cluster_nodes, key=_cluster_sort_key):
        values = tuple(sorted(cluster_nodes[key]))
        root_eligible = None
        if single_selected is not None and key == ("circuit", single_selected):
            root_eligible = {
                block_id
                for block_id in values
                if circuits[block_id] == single_selected
            }
        positions, local_depths, local_roots, local_tree_edges = _layout_tree_cluster(
            graph,
            values,
            cluster_edges[key],
            envelopes,
            records,
            aspect,
            root_eligible,
        )
        local_positions[key] = positions
        depths.update(local_depths)
        roots.extend(local_roots)
        tree_edges.update(local_tree_edges)
        bounds = _layout_bounds(positions, envelopes)
        cluster_sizes[key] = (
            max(bounds[2] - bounds[0], 1.0),
            max(bounds[3] - bounds[1], 1.0),
        )

    cluster_centers = _place_circuit_clusters(
        local_positions,
        cluster_sizes,
        graph,
        cluster_for_node,
        aspect,
        allow_horizontal_mirroring=True,
    )

    mirror_cluster = _horizontal_mirror_choices(
        local_positions,
        cluster_centers,
        graph,
        cluster_for_node,
    )

    positions: dict[int, Point] = {}
    for key, values in local_positions.items():
        center = cluster_centers[key]
        for block_id, (x, y) in values.items():
            positions[block_id] = (
                center[0] + (-x if key in mirror_cluster else x),
                center[1] + y,
            )

    routes, labels, leaders = route_block_graph_edges(
        graph,
        positions,
        envelopes,
        tree_edge_indices=frozenset(tree_edges),
        block_circuit_indices=circuits,
        edge_label_sizes=edge_label_sizes,
    )
    return BlockGraphLayout(
        positions=positions,
        depths=depths,
        root_ids=tuple(roots),
        tree_edge_indices=frozenset(tree_edges),
        edge_routes=routes,
        edge_label_positions=labels,
        edge_label_leaders=leaders,
    )


def _geometric_median(points: Sequence[Point]) -> Point:
    """Mediana geométrica determinística por Weiszfeld, com casos coincidentes."""

    if not points:
        raise ValueError("A mediana geométrica requer pelo menos um ponto.")
    if len(points) == 1:
        return points[0]
    if len(points) == 2:
        return (
            (points[0][0] + points[1][0]) / 2.0,
            (points[0][1] + points[1][1]) / 2.0,
        )
    first = points[0]
    direction = next(
        (
            (point[0] - first[0], point[1] - first[1])
            for point in points[1:]
            if math.dist(first, point) > 1.0e-12
        ),
        (0.0, 0.0),
    )
    if direction == (0.0, 0.0):
        return first
    direction_length = math.hypot(*direction)
    if all(
        abs(
            direction[0] * (point[1] - first[1])
            - direction[1] * (point[0] - first[0])
        )
        <= 1.0e-9 * direction_length
        for point in points
    ):
        unit = (direction[0] / direction_length, direction[1] / direction_length)
        ordered = sorted(
            points,
            key=lambda point: (point[0] - first[0]) * unit[0]
            + (point[1] - first[1]) * unit[1],
        )
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (
            (ordered[middle - 1][0] + ordered[middle][0]) / 2.0,
            (ordered[middle - 1][1] + ordered[middle][1]) / 2.0,
        )
    current = (
        math.fsum(point[0] for point in points) / len(points),
        math.fsum(point[1] for point in points) / len(points),
    )
    for _iteration in range(64):
        coincident = next(
            (point for point in points if math.dist(current, point) <= 1.0e-12),
            None,
        )
        if coincident is not None:
            # Para dois pontos (ou uma maioria coincidente), esse ponto já é
            # uma mediana válida e evita divisão por zero.
            if sum(math.dist(coincident, point) <= 1.0e-12 for point in points) * 2 >= len(points):
                return coincident
            current = (current[0] + 1.0e-10, current[1] + 1.0e-10)
        weights = [1.0 / max(math.dist(current, point), 1.0e-12) for point in points]
        total = math.fsum(weights)
        updated = (
            math.fsum(point[0] * weight for point, weight in zip(points, weights)) / total,
            math.fsum(point[1] * weight for point, weight in zip(points, weights)) / total,
        )
        if math.dist(current, updated) <= 1.0e-9:
            return updated
        current = updated
    return current


def block_coordinate_anchors(
    result: BlockAnalysisResult,
) -> dict[int, tuple[float, float]]:
    """Calcula uma âncora espacial para cada bloco a partir das suas fronteiras.

    Cada barra na ponta de uma chave conta no máximo uma vez por bloco. Blocos
    sem chave de fronteira usam a mediana geométrica de todas as suas barras. O eixo Y é
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
            x, y = _geometric_median(
                tuple(
                    (float(bars.x[index]), -float(bars.y[index]))
                    for index in ordered
                )
            )
            if not math.isfinite(x) or not math.isfinite(y):
                return {}
            anchors[record.block_id] = (x, y)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {}
    return anchors


def _unique_edge_indices(graph: BlockGraph) -> tuple[int, ...]:
    seen: set[tuple[int, int]] = set()
    values: list[int] = []
    for edge_index, edge in enumerate(graph.edges):
        if edge.start_block_id == edge.end_block_id or edge.endpoint_key in seen:
            continue
        seen.add(edge.endpoint_key)
        values.append(edge_index)
    return tuple(values)


def _resolve_node_collisions(
    positions: dict[int, Point],
    envelopes: Mapping[int, BlockNodeEnvelope],
    *,
    clearance: float = GEOGRAPHIC_NODE_CLEARANCE,
    iterations: int = 1,
) -> None:
    """Resolve colisões retangulares com grade espacial, sem passos aleatórios."""

    if len(positions) < 2:
        return
    cell_size = max(
        max(max(value.width, value.height) for value in envelopes.values())
        + clearance,
        1.0,
    )
    for _iteration in range(iterations):
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for block_id in sorted(positions):
            x, y = positions[block_id]
            grid[(math.floor(x / cell_size), math.floor(y / cell_size))].append(block_id)
        movements = {block_id: [0.0, 0.0] for block_id in positions}
        collisions = 0
        seen_pairs: set[tuple[int, int]] = set()
        for cell in sorted(grid):
            candidates: list[int] = []
            for offset_x in (-1, 0, 1):
                for offset_y in (-1, 0, 1):
                    candidates.extend(grid.get((cell[0] + offset_x, cell[1] + offset_y), ()))
            for left in grid[cell]:
                for right in candidates:
                    if right <= left or (left, right) in seen_pairs:
                        continue
                    seen_pairs.add((left, right))
                    dx = positions[right][0] - positions[left][0]
                    dy = positions[right][1] - positions[left][1]
                    overlap_x = (
                        (envelopes[left].width + envelopes[right].width) / 2.0
                        + clearance
                        - abs(dx)
                    )
                    overlap_y = (
                        (envelopes[left].height + envelopes[right].height) / 2.0
                        + clearance
                        - abs(dy)
                    )
                    if overlap_x <= 0.0 or overlap_y <= 0.0:
                        continue
                    collisions += 1
                    if overlap_x <= overlap_y:
                        direction = 1.0 if dx > 0.0 or (dx == 0.0 and (left + right) % 2) else -1.0
                        amount = overlap_x / 2.0 + 0.01
                        movements[left][0] -= direction * amount
                        movements[right][0] += direction * amount
                    else:
                        direction = 1.0 if dy > 0.0 or (dy == 0.0 and (left + right) % 2) else -1.0
                        amount = overlap_y / 2.0 + 0.01
                        movements[left][1] -= direction * amount
                        movements[right][1] += direction * amount
        if collisions == 0:
            return
        for block_id, movement in movements.items():
            positions[block_id] = (
                positions[block_id][0] + movement[0],
                positions[block_id][1] + movement[1],
            )


def _component_coordinate_layout(
    graph: BlockGraph,
    component: Sequence[int],
    edge_indices: Sequence[int],
    anchors: Mapping[int, Point],
    envelopes: Mapping[int, BlockNodeEnvelope],
    circuits: Mapping[int, int | None],
    single_selected: int | None,
) -> dict[int, Point]:
    raw_center = (
        median(anchors[block_id][0] for block_id in component),
        median(anchors[block_id][1] for block_id in component),
    )
    radii = [
        math.dist(anchors[block_id], raw_center)
        for block_id in component
        if math.dist(anchors[block_id], raw_center) > 1.0e-9
    ]
    reference_radius = median(radii) if radii else 1.0
    compressed: dict[int, Point] = {}
    for block_id in component:
        dx = anchors[block_id][0] - raw_center[0]
        dy = anchors[block_id][1] - raw_center[1]
        radius = math.hypot(dx, dy)
        if radius <= 1.0e-12:
            compressed[block_id] = (0.0, 0.0)
            continue
        compressed_radius = reference_radius * math.sqrt(radius / reference_radius)
        compressed[block_id] = (
            dx / radius * compressed_radius,
            dy / radius * compressed_radius,
        )

    raw_edge_lengths = [
        math.dist(
            anchors[graph.edges[index].start_block_id],
            anchors[graph.edges[index].end_block_id],
        )
        for index in edge_indices
        if math.dist(
            anchors[graph.edges[index].start_block_id],
            anchors[graph.edges[index].end_block_id],
        )
        > 1.0e-9
    ]
    typical_raw = median(raw_edge_lengths) if raw_edge_lengths else reference_radius
    compressed_edge_lengths = [
        math.dist(
            compressed[graph.edges[index].start_block_id],
            compressed[graph.edges[index].end_block_id],
        )
        for index in edge_indices
        if math.dist(
            compressed[graph.edges[index].start_block_id],
            compressed[graph.edges[index].end_block_id],
        )
        > 1.0e-9
    ]
    typical_compressed = (
        median(compressed_edge_lengths)
        if compressed_edge_lengths
        else reference_radius
    )
    scale = GEOGRAPHIC_TARGET_EDGE_LENGTH / max(typical_compressed, 1.0e-9)
    positions = {
        block_id: (compressed[block_id][0] * scale, compressed[block_id][1] * scale)
        for block_id in component
    }
    initial = dict(positions)
    desired_lengths: dict[int, float] = {}
    for edge_index in edge_indices:
        edge = graph.edges[edge_index]
        raw_length = math.dist(anchors[edge.start_block_id], anchors[edge.end_block_id])
        desired = GEOGRAPHIC_TARGET_EDGE_LENGTH * math.sqrt(
            max(raw_length, 1.0e-9) / max(typical_raw, 1.0e-9)
        )
        desired = min(MAX_COORDINATE_EDGE_LENGTH, max(MIN_COORDINATE_EDGE_LENGTH, desired))
        if single_selected is not None and (
            (circuits[edge.start_block_id] == single_selected)
            != (circuits[edge.end_block_id] == single_selected)
        ):
            desired = min(desired, 240.0)
        desired_lengths[edge_index] = desired

    for iteration in range(72):
        movements = {block_id: [0.0, 0.0] for block_id in component}
        spring_strength = 0.18 * (1.0 - iteration / 110.0)
        for edge_index in edge_indices:
            edge = graph.edges[edge_index]
            start = edge.start_block_id
            end = edge.end_block_id
            dx = positions[end][0] - positions[start][0]
            dy = positions[end][1] - positions[start][1]
            distance = math.hypot(dx, dy)
            if distance <= 1.0e-12:
                angle = (start * 0.61803398875 + end * 0.38196601125) * math.pi
                dx, dy, distance = math.cos(angle), math.sin(angle), 1.0
            force = (distance - desired_lengths[edge_index]) * spring_strength
            move_x = dx / distance * force / 2.0
            move_y = dy / distance * force / 2.0
            movements[start][0] += move_x
            movements[start][1] += move_y
            movements[end][0] -= move_x
            movements[end][1] -= move_y
        anchor_strength = 0.012
        for block_id in component:
            movements[block_id][0] += (
                initial[block_id][0] - positions[block_id][0]
            ) * anchor_strength
            movements[block_id][1] += (
                initial[block_id][1] - positions[block_id][1]
            ) * anchor_strength
            positions[block_id] = (
                positions[block_id][0] + movements[block_id][0],
                positions[block_id][1] + movements[block_id][1],
            )
        if iteration % 2 == 1:
            _resolve_node_collisions(positions, envelopes, iterations=1)
    # Uma projeção final limita outliers mesmo quando a âncora espacial de um
    # trecho é milhares de vezes maior que a mediana da rede. Em ciclos, as
    # correções convergem por pares e mantêm a orientação obtida acima.
    for _iteration in range(36):
        changed = False
        for edge_index in edge_indices:
            edge = graph.edges[edge_index]
            start = edge.start_block_id
            end = edge.end_block_id
            dx = positions[end][0] - positions[start][0]
            dy = positions[end][1] - positions[start][1]
            distance = math.hypot(dx, dy)
            maximum = MAX_COORDINATE_EDGE_LENGTH
            if single_selected is not None and (
                (circuits[start] == single_selected)
                != (circuits[end] == single_selected)
            ):
                maximum = 240.0
            target = min(maximum, max(MIN_COORDINATE_EDGE_LENGTH, distance))
            if abs(target - distance) <= 0.05:
                continue
            changed = True
            if distance <= 1.0e-12:
                angle = (start * 0.61803398875 + end * 0.38196601125) * math.pi
                unit = (math.cos(angle), math.sin(angle))
            else:
                unit = (dx / distance, dy / distance)
            correction = (target - distance) / 2.0
            positions[start] = (
                positions[start][0] - unit[0] * correction,
                positions[start][1] - unit[1] * correction,
            )
            positions[end] = (
                positions[end][0] + unit[0] * correction,
                positions[end][1] + unit[1] * correction,
            )
        _resolve_node_collisions(positions, envelopes, iterations=1)
        if not changed:
            break
    _resolve_node_collisions(positions, envelopes, iterations=12)

    bounds = _layout_bounds(positions, envelopes)
    center = ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
    return {
        block_id: (positions[block_id][0] - center[0], positions[block_id][1] - center[1])
        for block_id in component
    }


def _layout_coordinate_cluster(
    graph: BlockGraph,
    block_ids: Sequence[int],
    edge_indices: set[int],
    anchors: Mapping[int, Point],
    envelopes: Mapping[int, BlockNodeEnvelope],
    circuits: Mapping[int, int | None],
    single_selected: int | None,
    target_aspect_ratio: float,
) -> dict[int, Point]:
    """Normaliza componentes elétricos dentro de uma mesma caixa espacial."""

    adjacency = _adjacency_for_edges(block_ids, graph, edge_indices)
    components = _connected_components(block_ids, adjacency)
    component_for_node = {
        block_id: component_index
        for component_index, component in enumerate(components)
        for block_id in component
    }
    unique_internal_edges = [
        edge_index
        for edge_index in _unique_edge_indices(graph)
        if edge_index in edge_indices
    ]
    edges_by_component: dict[int, list[int]] = defaultdict(list)
    for edge_index in unique_internal_edges:
        edge = graph.edges[edge_index]
        edges_by_component[component_for_node[edge.start_block_id]].append(edge_index)

    relative: dict[int, dict[int, Point]] = {}
    component_sizes: dict[int, tuple[float, float]] = {}
    for component_index, component in enumerate(components):
        positions = _component_coordinate_layout(
            graph,
            component,
            edges_by_component[component_index],
            anchors,
            envelopes,
            circuits,
            single_selected,
        )
        relative[component_index] = positions
        bounds = _layout_bounds(positions, envelopes)
        component_sizes[component_index] = (
            max(bounds[2] - bounds[0], 1.0),
            max(bounds[3] - bounds[1], 1.0),
        )
    packed = _pack_rectangles(
        component_sizes,
        target_aspect_ratio=target_aspect_ratio,
        gap=PACKED_COMPONENT_GAP,
    )
    positions: dict[int, Point] = {}
    for component_index, values in relative.items():
        center = packed[component_index]
        for block_id, position in values.items():
            positions[block_id] = (
                center[0] + position[0],
                center[1] + position[1],
            )
    bounds = _layout_bounds(positions, envelopes)
    center = ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
    return {
        block_id: (position[0] - center[0], position[1] - center[1])
        for block_id, position in positions.items()
    }


def layout_block_graph_by_coordinates(
    graph: BlockGraph,
    anchors: Mapping[int, Point],
    diameters: Mapping[int, float] | None = None,
    *,
    node_envelopes: Mapping[int, BlockNodeEnvelope | Sequence[float]] | None = None,
    block_circuit_indices: Mapping[int, int | None] | None = None,
    selected_circuit_indices: Sequence[int] | frozenset[int] | set[int] = (),
    target_aspect_ratio: float = 16.0 / 9.0,
    edge_label_sizes: Mapping[int, Sequence[float]] | None = None,
) -> BlockGraphLayout:
    """Compõe coordenadas robustas e relaxamento topológico por componente.

    ``diameters`` permanece como terceiro argumento por compatibilidade. Novas
    integrações devem preferir ``node_envelopes``, que também protege legendas.
    """

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
    aspect = _target_aspect_ratio(target_aspect_ratio)
    if node_envelopes is None and diameters is not None:
        node_envelopes = {
            block_id: BlockNodeEnvelope(float(diameter), float(diameter))
            for block_id, diameter in diameters.items()
        }
    envelopes = _normalize_envelopes(node_ids, node_envelopes)
    circuits = {
        block_id: None
        if block_circuit_indices is None
        else block_circuit_indices.get(block_id)
        for block_id in node_ids
    }
    selected = frozenset(int(value) for value in selected_circuit_indices)
    single_selected = next(iter(selected)) if len(selected) == 1 else None
    external_for_selected: set[int] = set()
    if single_selected is not None and block_circuit_indices is not None:
        for edge in graph.edges:
            start_circuit = circuits[edge.start_block_id]
            end_circuit = circuits[edge.end_block_id]
            if (
                start_circuit == single_selected
                and end_circuit not in (None, single_selected)
            ):
                external_for_selected.add(edge.end_block_id)
            elif (
                end_circuit == single_selected
                and start_circuit not in (None, single_selected)
            ):
                external_for_selected.add(edge.start_block_id)

    cluster_for_node: dict[int, tuple[str, int]] = {}
    for block_id in node_ids:
        circuit = circuits[block_id]
        if block_circuit_indices is None:
            key = ("all", 0)
        elif single_selected is not None and (
            circuit == single_selected or block_id in external_for_selected
        ):
            key = ("circuit", single_selected)
        elif circuit is None:
            key = ("unresolved", 0)
        else:
            key = ("circuit", int(circuit))
        cluster_for_node[block_id] = key

    cluster_nodes: dict[tuple[str, int], list[int]] = defaultdict(list)
    for block_id, key in cluster_for_node.items():
        cluster_nodes[key].append(block_id)
    cluster_edges: dict[tuple[str, int], set[int]] = defaultdict(set)
    for edge_index, edge in enumerate(graph.edges):
        key = cluster_for_node[edge.start_block_id]
        if key == cluster_for_node[edge.end_block_id]:
            cluster_edges[key].add(edge_index)

    local_positions: dict[tuple[str, int], dict[int, Point]] = {}
    cluster_sizes: dict[tuple[str, int], tuple[float, float]] = {}
    for key in sorted(cluster_nodes, key=_cluster_sort_key):
        values = tuple(sorted(cluster_nodes[key]))
        local = _layout_coordinate_cluster(
            graph,
            values,
            cluster_edges[key],
            anchors,
            envelopes,
            circuits,
            single_selected,
            aspect,
        )
        local_positions[key] = local
        bounds = _layout_bounds(local, envelopes)
        cluster_sizes[key] = (
            max(bounds[2] - bounds[0], 1.0),
            max(bounds[3] - bounds[1], 1.0),
        )
    cluster_centers = _place_circuit_clusters(
        local_positions,
        cluster_sizes,
        graph,
        cluster_for_node,
        aspect,
        cluster_anchors={
            key: _geometric_median(
                tuple(anchors[block_id] for block_id in cluster_nodes[key])
            )
            for key in cluster_nodes
        },
    )
    positions: dict[int, Point] = {}
    for key, values in local_positions.items():
        center = cluster_centers[key]
        for block_id, position in values.items():
            positions[block_id] = (center[0] + position[0], center[1] + position[1])
    bounds = _layout_bounds(positions, envelopes)
    center = ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
    positions = {
        block_id: (position[0] - center[0], position[1] - center[1])
        for block_id, position in positions.items()
    }
    routes, labels, leaders = route_block_graph_edges(
        graph,
        positions,
        envelopes,
        tree_edge_indices=frozenset(range(len(graph.edges))),
        block_circuit_indices=circuits,
        edge_label_sizes=edge_label_sizes,
    )
    roots = tuple(record.block_id for record in graph.nodes if record.contains_source)
    return BlockGraphLayout(
        positions=positions,
        depths={},
        root_ids=roots,
        tree_edge_indices=frozenset(range(len(graph.edges))),
        edge_routes=routes,
        edge_label_positions=labels,
        edge_label_leaders=leaders,
    )


def _point_in_rect(point: Point, rect: tuple[float, float, float, float]) -> bool:
    return rect[0] <= point[0] <= rect[2] and rect[1] <= point[1] <= rect[3]


def _segments_intersect(start: Point, end: Point, other_start: Point, other_end: Point) -> bool:
    def orientation(first: Point, second: Point, third: Point) -> float:
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    first = orientation(start, end, other_start)
    second = orientation(start, end, other_end)
    third = orientation(other_start, other_end, start)
    fourth = orientation(other_start, other_end, end)
    epsilon = 1.0e-9
    if (
        abs(first) <= epsilon
        and abs(second) <= epsilon
        and abs(third) <= epsilon
        and abs(fourth) <= epsilon
    ):
        return not (
            max(start[0], end[0]) < min(other_start[0], other_end[0]) - epsilon
            or max(other_start[0], other_end[0]) < min(start[0], end[0]) - epsilon
            or max(start[1], end[1]) < min(other_start[1], other_end[1]) - epsilon
            or max(other_start[1], other_end[1]) < min(start[1], end[1]) - epsilon
        )
    return (first * second <= epsilon) and (third * fourth <= epsilon)


def _segment_intersects_rect(
    start: Point,
    end: Point,
    rect: tuple[float, float, float, float],
) -> bool:
    if _point_in_rect(start, rect) or _point_in_rect(end, rect):
        return True
    top_left = (rect[0], rect[1])
    top_right = (rect[2], rect[1])
    bottom_right = (rect[2], rect[3])
    bottom_left = (rect[0], rect[3])
    return any(
        _segments_intersect(start, end, edge_start, edge_end)
        for edge_start, edge_end in (
            (top_left, top_right),
            (top_right, bottom_right),
            (bottom_right, bottom_left),
            (bottom_left, top_left),
        )
    )


def _route_polyline(route: BlockGraphEdgeRoute) -> tuple[Point, ...]:
    if route.cubic:
        if len(route.points) < 4 or (len(route.points) - 1) % 3:
            return route.points
        sampled: list[Point] = [route.points[0]]
        for index in range(1, len(route.points), 3):
            start = route.points[index - 1]
            control_1 = route.points[index]
            control_2 = route.points[index + 1]
            end = route.points[index + 2]
            sampled.extend(
                (
                    (1.0 - ratio) ** 3 * start[0]
                    + 3.0 * (1.0 - ratio) ** 2 * ratio * control_1[0]
                    + 3.0 * (1.0 - ratio) * ratio**2 * control_2[0]
                    + ratio**3 * end[0],
                    (1.0 - ratio) ** 3 * start[1]
                    + 3.0 * (1.0 - ratio) ** 2 * ratio * control_1[1]
                    + 3.0 * (1.0 - ratio) * ratio**2 * control_2[1]
                    + ratio**3 * end[1],
                )
                for step in range(1, 13)
                for ratio in (step / 12.0,)
            )
        return tuple(sampled)
    if not route.curved or len(route.points) != 3:
        return route.points
    start, control, end = route.points
    return tuple(
        (
            (1.0 - step / 16.0) ** 2 * start[0]
            + 2.0 * (1.0 - step / 16.0) * (step / 16.0) * control[0]
            + (step / 16.0) ** 2 * end[0],
            (1.0 - step / 16.0) ** 2 * start[1]
            + 2.0 * (1.0 - step / 16.0) * (step / 16.0) * control[1]
            + (step / 16.0) ** 2 * end[1],
        )
        for step in range(17)
    )


def _clip_to_node_border(
    center: Point,
    toward: Point,
    envelope: BlockNodeEnvelope,
) -> Point:
    dx = toward[0] - center[0]
    dy = toward[1] - center[1]
    distance = math.hypot(dx, dy)
    if distance <= 1.0e-12:
        return center
    # O círculo ocupa a menor dimensão; a altura excedente pertence à legenda.
    radius = envelope.node_diameter / 2.0
    return (center[0] + dx / distance * radius, center[1] + dy / distance * radius)


def _route_length(points: Sequence[Point]) -> float:
    return math.fsum(math.dist(start, end) for start, end in zip(points, points[1:]))


def _route_collision_count(
    route: BlockGraphEdgeRoute,
    node_rectangles: Mapping[int, tuple[float, float, float, float]],
    terminal_ids: frozenset[int],
) -> int:
    points = _route_polyline(route)
    return sum(
        1
        for block_id, rectangle in node_rectangles.items()
        if block_id not in terminal_ids
        and any(
            _segment_intersects_rect(start, end, rectangle)
            for start, end in zip(points, points[1:])
        )
    )


def _route_crossing_count(
    route: BlockGraphEdgeRoute,
    existing: Sequence[BlockGraphEdgeRoute],
) -> int:
    points = _route_polyline(route)
    crossings = 0
    for other in existing:
        other_points = _route_polyline(other)
        if any(
            math.dist(point, other_point) <= 1.0e-6
            for point in (points[0], points[-1])
            for other_point in (other_points[0], other_points[-1])
        ):
            continue
        for start, end in zip(points, points[1:]):
            for other_start, other_end in zip(other_points, other_points[1:]):
                if _segments_intersect(start, end, other_start, other_end):
                    crossings += 1
    return crossings


def _route_point_and_normal(
    route: BlockGraphEdgeRoute,
    fraction: float,
) -> tuple[Point, Point]:
    points = _route_polyline(route)
    lengths = [math.dist(start, end) for start, end in zip(points, points[1:])]
    total = math.fsum(lengths)
    if total <= 1.0e-12:
        return points[0], (0.0, -1.0)
    target = total * min(1.0, max(0.0, fraction))
    traversed = 0.0
    for index, length in enumerate(lengths):
        if traversed + length >= target or index == len(lengths) - 1:
            ratio = 0.0 if length <= 1.0e-12 else (target - traversed) / length
            start, end = points[index], points[index + 1]
            point = (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
            if length <= 1.0e-12:
                return point, (0.0, -1.0)
            return point, (-(end[1] - start[1]) / length, (end[0] - start[0]) / length)
        traversed += length
    return points[-1], (0.0, -1.0)


def route_block_graph_edges(
    graph: BlockGraph,
    positions: Mapping[int, Point],
    node_envelopes: Mapping[int, BlockNodeEnvelope | Sequence[float]] | None = None,
    *,
    tree_edge_indices: frozenset[int] | set[int] = frozenset(),
    block_circuit_indices: Mapping[int, int | None] | None = None,
    edge_label_sizes: Mapping[int, Sequence[float]] | None = None,
) -> tuple[
    dict[int, BlockGraphEdgeRoute],
    dict[int, Point],
    dict[int, tuple[Point, Point]],
]:
    """Roteia arestas e posiciona suas etiquetas sem tipos da interface gráfica."""

    node_ids = tuple(sorted(positions))
    envelopes = _normalize_envelopes(node_ids, node_envelopes)
    rectangles = {
        block_id: _rect_bounds(positions[block_id], envelopes[block_id], 4.0)
        for block_id in node_ids
    }
    spatial_cell = max(
        max(max(value.width, value.height) for value in envelopes.values()) + 16.0,
        64.0,
    )

    def rectangle_cells(
        rectangle: tuple[float, float, float, float],
    ) -> tuple[tuple[int, int], ...]:
        return tuple(
            (cell_x, cell_y)
            for cell_x in range(
                math.floor(rectangle[0] / spatial_cell),
                math.floor(rectangle[2] / spatial_cell) + 1,
            )
            for cell_y in range(
                math.floor(rectangle[1] / spatial_cell),
                math.floor(rectangle[3] / spatial_cell) + 1,
            )
        )

    node_grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for block_id, rectangle in rectangles.items():
        for cell in rectangle_cells(rectangle):
            node_grid[cell].append(block_id)

    def route_candidate_nodes(route: BlockGraphEdgeRoute) -> set[int]:
        candidates: set[int] = set()
        points = _route_polyline(route)
        for start, end in zip(points, points[1:]):
            steps = max(
                1,
                math.ceil(
                    max(abs(end[0] - start[0]), abs(end[1] - start[1]))
                    / spatial_cell
                ),
            )
            for step in range(steps + 1):
                ratio = step / steps
                cell = (
                    math.floor((start[0] + (end[0] - start[0]) * ratio) / spatial_cell),
                    math.floor((start[1] + (end[1] - start[1]) * ratio) / spatial_cell),
                )
                for offset_x in (-1, 0, 1):
                    for offset_y in (-1, 0, 1):
                        candidates.update(
                            node_grid.get(
                                (cell[0] + offset_x, cell[1] + offset_y),
                                (),
                            )
                        )
        return candidates

    def fast_route_collision_count(
        route: BlockGraphEdgeRoute,
        terminal_ids: frozenset[int],
    ) -> int:
        points = _route_polyline(route)
        return sum(
            1
            for block_id in route_candidate_nodes(route)
            if block_id not in terminal_ids
            and any(
                _segment_intersects_rect(start, end, rectangles[block_id])
                for start, end in zip(points, points[1:])
            )
        )
    parallel: dict[tuple[int, int], list[int]] = defaultdict(list)
    for edge_index, edge in enumerate(graph.edges):
        parallel[edge.endpoint_key].append(edge_index)

    circuits = block_circuit_indices or {}

    def is_intercircuit(edge: BlockGraphEdge) -> bool:
        start = circuits.get(edge.start_block_id)
        end = circuits.get(edge.end_block_id)
        return start is not None and end is not None and start != end

    edge_order = sorted(
        range(len(graph.edges)),
        key=lambda index: (
            index not in tree_edge_indices,
            not is_intercircuit(graph.edges[index]),
            graph.edges[index].switch_index,
        ),
    )
    routes: dict[int, BlockGraphEdgeRoute] = {}
    routed: list[BlockGraphEdgeRoute] = []
    for edge_index in edge_order:
        edge = graph.edges[edge_index]
        start_center = positions[edge.start_block_id]
        end_center = positions[edge.end_block_id]
        group = parallel[edge.endpoint_key]
        parallel_index = group.index(edge_index)
        if edge.start_block_id == edge.end_block_id:
            radius = envelopes[edge.start_block_id].node_diameter / 2.0
            offset = parallel_index * 22.0
            start = (start_center[0] + radius * 0.55, start_center[1] - radius * 0.72)
            end = (start_center[0] - radius * 0.55, start_center[1] - radius * 0.72)
            control = (start_center[0], start_center[1] - radius - 70.0 - offset)
            chosen = BlockGraphEdgeRoute((start, control, end), curved=True)
            routes[edge.switch_index] = chosen
            routed.append(chosen)
            continue

        start = _clip_to_node_border(start_center, end_center, envelopes[edge.start_block_id])
        end = _clip_to_node_border(end_center, start_center, envelopes[edge.end_block_id])
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = max(math.hypot(dx, dy), 1.0e-9)
        normal = (-dy / length, dx / length)
        candidates: list[BlockGraphEdgeRoute] = []
        if len(group) > 1:
            offset = (parallel_index - (len(group) - 1) / 2.0) * 34.0
            control = (
                (start[0] + end[0]) / 2.0 + normal[0] * offset,
                (start[1] + end[1]) / 2.0 + normal[1] * offset,
            )
            candidates.append(BlockGraphEdgeRoute((start, control, end), curved=True))
        else:
            direct = BlockGraphEdgeRoute((start, end))
            terminal_ids = frozenset((edge.start_block_id, edge.end_block_id))
            if (
                edge_index in tree_edge_indices
                and fast_route_collision_count(direct, terminal_ids) == 0
            ):
                routes[edge.switch_index] = direct
                routed.append(direct)
                continue
            middle_y = (start[1] + end[1]) / 2.0
            channel = BlockGraphEdgeRoute(
                (start, (start[0], middle_y), (end[0], middle_y), end)
            )
            if edge_index in tree_edge_indices and abs(dy) > abs(dx) * 0.45:
                candidates.extend((channel, direct))
            else:
                candidates.extend((direct, channel))
            offsets = (
                (42.0, -42.0)
                if len(graph.edges) > 500
                else (42.0, -42.0, 78.0, -78.0, 120.0, -120.0)
            )
            for offset in offsets:
                candidates.append(
                    BlockGraphEdgeRoute(
                        (
                            start,
                            (
                                (start[0] + end[0]) / 2.0 + normal[0] * offset,
                                (start[1] + end[1]) / 2.0 + normal[1] * offset,
                            ),
                            end,
                        )
                    )
                )
        terminal_ids = frozenset((edge.start_block_id, edge.end_block_id))
        scored = []
        for candidate_index, candidate in enumerate(candidates):
            collisions = fast_route_collision_count(candidate, terminal_ids)
            crossings = _route_crossing_count(
                candidate,
                routed if len(graph.edges) <= 300 else routed[-24:],
            )
            scored.append(
                (
                    collisions * 4 + crossings,
                    collisions,
                    crossings,
                    _route_length(_route_polyline(candidate)),
                    candidate_index,
                    candidate,
                )
            )
        chosen = (
            min(scored)
            if scored
            else (0, 0, 0, 0.0, 0, BlockGraphEdgeRoute((start, end)))
        )
        routes[edge.switch_index] = chosen[-1]
        routed.append(chosen[-1])

    label_positions: dict[int, Point] = {}
    label_leaders: dict[int, tuple[Point, Point]] = {}
    label_grid: dict[
        tuple[int, int],
        list[tuple[float, float, float, float]],
    ] = defaultdict(list)
    route_polylines = {
        switch_index: _route_polyline(route)
        for switch_index, route in routes.items()
    }
    label_order = sorted(
        graph.edges,
        key=lambda edge: (not is_intercircuit(edge), edge.switch_index),
    )
    for edge in label_order:
        route = routes[edge.switch_index]
        raw_size = (
            None
            if edge_label_sizes is None
            else edge_label_sizes.get(edge.switch_index)
        )
        width = (
            max(18.0, float(raw_size[0]))
            if raw_size is not None
            else max(32.0, len(edge.label) * 7.0 + 12.0)
        )
        height = max(14.0, float(raw_size[1])) if raw_size is not None else 22.0
        best: tuple[int, float, int, Point, Point] | None = None
        for fraction_index, fraction in enumerate((0.5, 0.4, 0.6, 0.3, 0.7, 0.22, 0.78)):
            base, normal = _route_point_and_normal(route, fraction)
            for shift in (0.0, 20.0, -20.0, 38.0, -38.0, 58.0, -58.0, 82.0, -82.0):
                candidate = (base[0] + normal[0] * shift, base[1] + normal[1] * shift)
                rectangle = (
                    candidate[0] - width / 2.0 - 3.0,
                    candidate[1] - height / 2.0 - 3.0,
                    candidate[0] + width / 2.0 + 3.0,
                    candidate[1] + height / 2.0 + 3.0,
                )
                cells = rectangle_cells(rectangle)
                nearby_node_ids = {
                    block_id for cell in cells for block_id in node_grid.get(cell, ())
                }
                nearby_labels = {
                    other for cell in cells for other in label_grid.get(cell, ())
                }
                collisions = sum(
                    not (
                        rectangle[2] <= other[0]
                        or rectangle[0] >= other[2]
                        or rectangle[3] <= other[1]
                        or rectangle[1] >= other[3]
                    )
                    for other in (
                        *(rectangles[block_id] for block_id in nearby_node_ids),
                        *nearby_labels,
                    )
                )
                route_hits = 0
                if len(graph.edges) <= 300:
                    route_hits = sum(
                        _segment_intersects_rect(start, end, rectangle)
                        for switch_index, points in route_polylines.items()
                        if switch_index != edge.switch_index
                        for start, end in zip(points, points[1:])
                    )
                score = (collisions + route_hits, abs(shift), fraction_index, candidate, base)
                if best is None or score[:3] < best[:3]:
                    best = score
                if score[0] == 0 and shift == 0.0:
                    break
            if best is not None and best[0] == 0 and best[1] == 0.0:
                break
        assert best is not None
        candidate, base = best[3], best[4]
        label_positions[edge.switch_index] = candidate
        occupied_rectangle = (
            candidate[0] - width / 2.0 - 3.0,
            candidate[1] - height / 2.0 - 3.0,
            candidate[0] + width / 2.0 + 3.0,
            candidate[1] + height / 2.0 + 3.0,
        )
        for cell in rectangle_cells(occupied_rectangle):
            label_grid[cell].append(occupied_rectangle)
        if math.dist(candidate, base) > 8.0:
            label_leaders[edge.switch_index] = (candidate, base)
    return routes, label_positions, label_leaders


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
    "BlockGraphEdgeRoute",
    "BlockGraph",
    "BlockGraphEdge",
    "BlockGraphLayout",
    "BlockGraphLayoutMode",
    "BlockGraphForest",
    "BlockNodeEnvelope",
    "CIRCUIT_GAP",
    "COMPONENT_GAP",
    "FIXED_NODE_DIAMETER",
    "GEOGRAPHIC_NODE_CLEARANCE",
    "GEOGRAPHIC_TARGET_EDGE_LENGTH",
    "HORIZONTAL_NODE_GAP",
    "MAX_COORDINATE_EDGE_LENGTH",
    "MAX_NODE_DIAMETER",
    "MIN_COORDINATE_EDGE_LENGTH",
    "MIN_NODE_DIAMETER",
    "PACKED_COMPONENT_GAP",
    "TREE_BAND_GAP",
    "TREE_SIBLING_GAP",
    "VERTICAL_NODE_GAP",
    "block_node_envelopes",
    "block_node_diameters",
    "block_coordinate_anchors",
    "build_block_graph",
    "build_block_graph_forest",
    "direct_circuit_neighbors",
    "filter_block_graph",
    "layout_block_graph",
    "layout_block_graph_by_coordinates",
    "resolve_block_circuit_indices",
    "route_block_graph_edges",
]
