"""Modelo e layout determinístico do grafo simplificado de blocos.

Esta camada não importa Qt. Ela transforma o resultado da análise de blocos em
um multigrafo e calcula uma floresta em níveis, deixando a interface responsável
somente por desenhar e interagir com os itens.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

from .block_analysis import BlockAnalysisResult, BlockRecord
from .model import CircuitCatalogModel


FIXED_NODE_DIAMETER = 56.0
MIN_NODE_DIAMETER = 36.0
MAX_NODE_DIAMETER = 72.0
HORIZONTAL_NODE_GAP = 200.0
VERTICAL_NODE_GAP = 170.0
COMPONENT_GAP = 260.0


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
    "COMPONENT_GAP",
    "FIXED_NODE_DIAMETER",
    "HORIZONTAL_NODE_GAP",
    "MAX_NODE_DIAMETER",
    "MIN_NODE_DIAMETER",
    "VERTICAL_NODE_GAP",
    "block_node_diameters",
    "build_block_graph",
    "layout_block_graph",
    "resolve_block_circuit_indices",
]
