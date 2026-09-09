"""Adaptador isolado entre o grafo de blocos e a engine Graphviz ``dot``.

O Graphviz calcula somente geometria. Nenhum SVG, bitmap, cor ou interação da
interface passa por esta camada; o resultado é convertido para o contrato puro
``BlockGraphLayout`` que a cena Qt já sabe desenhar.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import platform
import struct
import subprocess
import threading
import time

from .block_graph import (
    BlockGraph,
    BlockGraphEdgeRoute,
    BlockGraphLayout,
    BlockNodeEnvelope,
    build_block_graph_forest,
)


GRAPHVIZ_VERSION = "15.1.1"
GRAPHVIZ_DISTRIBUTION = "windows_10_cmake_Release_Graphviz-15.1.1-win64.zip"
GRAPHVIZ_DOWNLOAD_URL = (
    "https://gitlab.com/api/v4/projects/4207231/packages/generic/"
    "graphviz-releases/15.1.1/" + GRAPHVIZ_DISTRIBUTION
)
GRAPHVIZ_SHA256 = (
    "e8256ef077e601d9f284378d96cd17faa7910832cf6bb85c43005e66ec2f255e"
)
GRAPHVIZ_TIMEOUT_SECONDS = 30.0
GRAPHVIZ_CACHE_SIZE = 8
_POINTS_PER_INCH = 72.0
GRAPHVIZ_CIRCUIT_SEPARATION_RANGE = (0.0, 1000.0)
GRAPHVIZ_NODE_SEPARATION_RANGE = (2.0, 500.0)
GRAPHVIZ_RANK_SEPARATION_RANGE = (2.0, 800.0)
GRAPHVIZ_TREE_EDGE_WEIGHT_RANGE = (1, 100)
GRAPHVIZ_TREE_EDGE_MINLEN_RANGE = (1, 10)
GRAPHVIZ_CROSSING_MINIMIZATION_RANGE = (0.1, 4.0)
GRAPHVIZ_SWITCH_NODE_CLEARANCE_PX = 4.0


class GraphvizEdgeRouting(str, Enum):
    """Traçados compatíveis com etiquetas e com o parser geométrico Qt."""

    SPLINE = "spline"
    POLYLINE = "polyline"
    LINE = "line"


@dataclass(frozen=True, slots=True)
class GraphvizLayoutSettings:
    """Parâmetros seguros de geometria expostos ao ajuste fino do usuário."""

    circuit_separation_px: float = 120.0
    node_separation_px: float = 32.0
    rank_separation_px: float = 56.0
    edge_routing: GraphvizEdgeRouting = GraphvizEdgeRouting.SPLINE
    equal_rank_spacing: bool = False
    switches_as_nodes: bool = False
    tree_edge_weight: int = 8
    tree_edge_minlen: int = 1
    crossing_minimization: float = 1.0

    def __post_init__(self) -> None:
        circuit_separation = float(self.circuit_separation_px)
        node_separation = float(self.node_separation_px)
        rank_separation = float(self.rank_separation_px)
        crossing_minimization = float(self.crossing_minimization)
        integer_values: list[int] = []
        for raw, label in (
            (self.tree_edge_weight, "Peso das arestas hierárquicas"),
            (self.tree_edge_minlen, "Distância mínima em níveis"),
        ):
            try:
                numeric = float(raw)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{label} deve ser um número inteiro.") from exc
            if not math.isfinite(numeric) or not numeric.is_integer():
                raise ValueError(f"{label} deve ser um número inteiro.")
            integer_values.append(int(numeric))
        tree_edge_weight, tree_edge_minlen = integer_values
        edge_routing = GraphvizEdgeRouting(self.edge_routing)
        if not isinstance(self.equal_rank_spacing, bool):
            raise ValueError("A uniformização entre níveis deve ser booleana.")
        if not isinstance(self.switches_as_nodes, bool):
            raise ValueError("A modelagem das chaves como nós deve ser booleana.")
        validations = (
            (
                circuit_separation,
                GRAPHVIZ_CIRCUIT_SEPARATION_RANGE,
                "Espaçamento entre circuitos",
            ),
            (
                node_separation,
                GRAPHVIZ_NODE_SEPARATION_RANGE,
                "Espaçamento horizontal",
            ),
            (
                rank_separation,
                GRAPHVIZ_RANK_SEPARATION_RANGE,
                "Espaçamento vertical",
            ),
            (
                crossing_minimization,
                GRAPHVIZ_CROSSING_MINIMIZATION_RANGE,
                "Esforço para reduzir cruzamentos",
            ),
        )
        for value, limits, label in validations:
            if not math.isfinite(value) or not limits[0] <= value <= limits[1]:
                raise ValueError(
                    f"{label} deve estar entre {limits[0]:g} e {limits[1]:g}."
                )
        integer_validations = (
            (
                tree_edge_weight,
                GRAPHVIZ_TREE_EDGE_WEIGHT_RANGE,
                "Peso das arestas hierárquicas",
            ),
            (
                tree_edge_minlen,
                GRAPHVIZ_TREE_EDGE_MINLEN_RANGE,
                "Distância mínima em níveis",
            ),
        )
        for value, limits, label in integer_validations:
            if not limits[0] <= value <= limits[1]:
                raise ValueError(
                    f"{label} deve estar entre {limits[0]} e {limits[1]}."
                )
        object.__setattr__(self, "node_separation_px", node_separation)
        object.__setattr__(
            self,
            "circuit_separation_px",
            circuit_separation,
        )
        object.__setattr__(self, "rank_separation_px", rank_separation)
        object.__setattr__(self, "edge_routing", edge_routing)
        object.__setattr__(
            self,
            "equal_rank_spacing",
            bool(self.equal_rank_spacing),
        )
        object.__setattr__(self, "switches_as_nodes", bool(self.switches_as_nodes))
        object.__setattr__(self, "tree_edge_weight", tree_edge_weight)
        object.__setattr__(self, "tree_edge_minlen", tree_edge_minlen)
        object.__setattr__(self, "crossing_minimization", crossing_minimization)

    def as_mapping(self) -> dict[str, float | int | str | bool]:
        return {
            "circuit_separation_px": self.circuit_separation_px,
            "node_separation_px": self.node_separation_px,
            "rank_separation_px": self.rank_separation_px,
            "edge_routing": self.edge_routing.value,
            "equal_rank_spacing": self.equal_rank_spacing,
            "switches_as_nodes": self.switches_as_nodes,
            "tree_edge_weight": self.tree_edge_weight,
            "tree_edge_minlen": self.tree_edge_minlen,
            "crossing_minimization": self.crossing_minimization,
        }


DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS = GraphvizLayoutSettings()


def _setting_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "sim", "on"}:
            return True
        if normalized in {"0", "false", "no", "não", "nao", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return default


def graphviz_layout_settings_from_mapping(
    values: Mapping[str, object],
) -> GraphvizLayoutSettings:
    """Converte preferências heterogêneas, recuperando cada campo inválido."""

    defaults = DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS

    def bounded_float(name: str, limits: tuple[float, float]) -> float:
        try:
            value = float(values.get(name, getattr(defaults, name)))
        except (TypeError, ValueError):
            return float(getattr(defaults, name))
        if math.isfinite(value) and limits[0] <= value <= limits[1]:
            return value
        return float(getattr(defaults, name))

    def bounded_int(name: str, limits: tuple[int, int]) -> int:
        try:
            raw = values.get(name, getattr(defaults, name))
            value = int(raw)
            if isinstance(raw, float) and not raw.is_integer():
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            return int(getattr(defaults, name))
        if limits[0] <= value <= limits[1]:
            return value
        return int(getattr(defaults, name))

    try:
        edge_routing = GraphvizEdgeRouting(
            values.get("edge_routing", defaults.edge_routing.value)
        )
    except (TypeError, ValueError):
        edge_routing = defaults.edge_routing
    return GraphvizLayoutSettings(
        circuit_separation_px=bounded_float(
            "circuit_separation_px", GRAPHVIZ_CIRCUIT_SEPARATION_RANGE
        ),
        node_separation_px=bounded_float(
            "node_separation_px", GRAPHVIZ_NODE_SEPARATION_RANGE
        ),
        rank_separation_px=bounded_float(
            "rank_separation_px", GRAPHVIZ_RANK_SEPARATION_RANGE
        ),
        edge_routing=edge_routing,
        equal_rank_spacing=_setting_bool(
            values.get("equal_rank_spacing"), defaults.equal_rank_spacing
        ),
        switches_as_nodes=_setting_bool(
            values.get("switches_as_nodes"), defaults.switches_as_nodes
        ),
        tree_edge_weight=bounded_int(
            "tree_edge_weight", GRAPHVIZ_TREE_EDGE_WEIGHT_RANGE
        ),
        tree_edge_minlen=bounded_int(
            "tree_edge_minlen", GRAPHVIZ_TREE_EDGE_MINLEN_RANGE
        ),
        crossing_minimization=bounded_float(
            "crossing_minimization", GRAPHVIZ_CROSSING_MINIMIZATION_RANGE
        ),
    )


class GraphvizLayoutError(RuntimeError):
    """Falha validada do runtime, do processo ou da geometria Graphviz."""


class GraphvizLayoutCancelled(GraphvizLayoutError):
    """Cálculo cancelado porque uma solicitação mais recente o substituiu."""


@dataclass(frozen=True, slots=True)
class GraphvizRuntimeStatus:
    available: bool
    executable: Path | None
    version: str | None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GraphvizDotInput:
    source: str
    depths: dict[int, int]
    root_ids: tuple[int, ...]
    tree_edge_indices: frozenset[int]
    layout_groups: tuple[tuple[int, ...], ...] = ()
    circuit_separation_px: float = 0.0
    switches_as_nodes: bool = False


def bundled_graphviz_root() -> Path:
    return Path(__file__).resolve().parent / "vendor" / "graphviz-15.1.1-win64"


def bundled_graphviz_dot() -> Path:
    return bundled_graphviz_root() / "bin" / "dot.exe"


@lru_cache(maxsize=8)
def probe_graphviz_runtime(
    executable: Path | str | None = None,
    *,
    require_supported_platform: bool = True,
) -> GraphvizRuntimeStatus:
    """Valida plataforma, executável e versão sem instalar ou alterar PATH."""

    if require_supported_platform and (
        platform.system() != "Windows" or struct.calcsize("P") * 8 != 64
    ):
        return GraphvizRuntimeStatus(
            False,
            None,
            None,
            "O modo Graphviz está disponível somente no Windows 64 bits.",
        )
    path = Path(executable) if executable is not None else bundled_graphviz_dot()
    if not path.is_file():
        return GraphvizRuntimeStatus(
            False,
            path,
            None,
            "O runtime portátil Graphviz 15.1.1 não foi encontrado.",
        )
    try:
        completed = subprocess.run(
            [str(path), "-V"],
            cwd=str(path.parent),
            capture_output=True,
            timeout=5.0,
            check=False,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if platform.system() == "Windows"
                else 0
            ),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GraphvizRuntimeStatus(False, path, None, str(exc))
    output = (completed.stderr + completed.stdout).decode(
        "utf-8", errors="replace"
    ).strip()
    marker = "graphviz version "
    version = None
    if marker in output.casefold():
        start = output.casefold().index(marker) + len(marker)
        version = output[start:].split(maxsplit=1)[0]
    if completed.returncode != 0:
        return GraphvizRuntimeStatus(
            False,
            path,
            version,
            output or f"dot terminou com código {completed.returncode}.",
        )
    if version != GRAPHVIZ_VERSION:
        return GraphvizRuntimeStatus(
            False,
            path,
            version,
            f"Era esperado Graphviz {GRAPHVIZ_VERSION}, mas foi encontrado {version or 'desconhecido'}.",
        )
    return GraphvizRuntimeStatus(True, path, version)


def _dot_quote(value: object) -> str:
    """Escapa texto DOT pelo subconjunto compatível de strings JSON."""

    return json.dumps(str(value), ensure_ascii=False)


def _layout_groups(
    graph: BlockGraph,
    block_circuit_indices: Mapping[int, int | None] | None,
    selected_circuit_indices: Sequence[int] | frozenset[int] | set[int],
) -> dict[tuple[str, int], tuple[int, ...]]:
    selected = frozenset(int(value) for value in selected_circuit_indices)
    single_selected = next(iter(selected)) if len(selected) == 1 else None
    circuits = {
        block_id: (
            None
            if block_circuit_indices is None
            else block_circuit_indices.get(block_id)
        )
        for block_id in graph.node_ids
    }
    external: set[int] = set()
    if single_selected is not None:
        for edge in graph.edges:
            start = circuits[edge.start_block_id]
            end = circuits[edge.end_block_id]
            if start == single_selected and end not in (None, single_selected):
                external.add(edge.end_block_id)
            elif end == single_selected and start not in (None, single_selected):
                external.add(edge.start_block_id)
    grouped: dict[tuple[str, int], list[int]] = defaultdict(list)
    for block_id in sorted(graph.node_ids):
        circuit = circuits[block_id]
        if block_circuit_indices is None:
            key = ("all", 0)
        elif single_selected is not None and (
            circuit == single_selected or block_id in external
        ):
            key = ("circuit", single_selected)
        elif circuit is None:
            key = ("unresolved", 0)
        else:
            key = ("circuit", int(circuit))
        grouped[key].append(block_id)
    return {key: tuple(values) for key, values in grouped.items()}


def serialize_graphviz_dot(
    graph: BlockGraph,
    *,
    node_envelopes: Mapping[int, BlockNodeEnvelope],
    edge_label_sizes: Mapping[int, Sequence[float]] | None = None,
    block_circuit_indices: Mapping[int, int | None] | None = None,
    selected_circuit_indices: Sequence[int] | frozenset[int] | set[int] = (),
    settings: GraphvizLayoutSettings = DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS,
) -> GraphvizDotInput:
    """Produz DOT determinístico e a floresta que define suas restrições."""

    node_ids = tuple(sorted(graph.node_ids))
    if set(node_envelopes) != set(node_ids):
        raise GraphvizLayoutError("Os envelopes não correspondem aos nós do grafo.")
    if len({edge.switch_index for edge in graph.edges}) != len(graph.edges):
        raise GraphvizLayoutError("Os índices técnicos das chaves devem ser únicos.")

    selected = frozenset(int(value) for value in selected_circuit_indices)
    single_selected = next(iter(selected)) if len(selected) == 1 else None
    groups = _layout_groups(
        graph,
        block_circuit_indices,
        selected_circuit_indices,
    )
    group_for_node = {
        block_id: key for key, values in groups.items() for block_id in values
    }
    internal_edges: dict[tuple[str, int], set[int]] = defaultdict(set)
    for edge_index, edge in enumerate(graph.edges):
        if group_for_node[edge.start_block_id] == group_for_node[edge.end_block_id]:
            internal_edges[group_for_node[edge.start_block_id]].add(edge_index)

    records = {record.block_id: record for record in graph.nodes}
    depths: dict[int, int] = {}
    roots: list[int] = []
    parent_by_node: dict[int, int | None] = {}
    parent_edge_by_node: dict[int, int] = {}
    for key in sorted(groups, key=lambda value: (value[0], value[1])):
        values = groups[key]
        root_eligible = None
        if single_selected is not None and key == ("circuit", single_selected):
            root_eligible = {
                block_id
                for block_id in values
                if block_circuit_indices is not None
                and block_circuit_indices.get(block_id) == single_selected
            }
        forest = build_block_graph_forest(
            graph,
            values,
            internal_edges[key],
            records,
            root_eligible,
        )
        depths.update(forest.depths)
        roots.extend(forest.root_ids)
        parent_by_node.update(forest.parent_by_node)
        parent_edge_by_node.update(forest.parent_edge_by_node)

    tree_edges = frozenset(parent_edge_by_node.values())
    child_by_edge = {
        edge_index: block_id
        for block_id, edge_index in parent_edge_by_node.items()
    }
    node_separation = settings.node_separation_px / _POINTS_PER_INCH
    rank_separation_value = settings.rank_separation_px / _POINTS_PER_INCH
    rank_separation = f"{rank_separation_value:.9f}"
    if settings.equal_rank_spacing:
        rank_separation = _dot_quote(f"{rank_separation} equally")
    switch_node_sizes: dict[int, tuple[float, float]] = {}
    if settings.switches_as_nodes:
        expected_switches = {edge.switch_index for edge in graph.edges}
        if edge_label_sizes is None or set(edge_label_sizes) != expected_switches:
            raise GraphvizLayoutError(
                "Os envelopes das etiquetas não correspondem às chaves do grafo."
            )
        for switch_index in sorted(expected_switches):
            raw_size = edge_label_sizes[switch_index]
            if len(raw_size) < 2:
                raise GraphvizLayoutError("Envelope de etiqueta Graphviz inválido.")
            width = float(raw_size[0]) + 2.0 * GRAPHVIZ_SWITCH_NODE_CLEARANCE_PX
            height = float(raw_size[1]) + 2.0 * GRAPHVIZ_SWITCH_NODE_CLEARANCE_PX
            if not all(
                math.isfinite(value) and value > 0.0 for value in (width, height)
            ):
                raise GraphvizLayoutError("Envelope de etiqueta Graphviz inválido.")
            switch_node_sizes[switch_index] = (width, height)

    lines = [
        "digraph BlockGraph {",
        f"  graph [rankdir=TB, splines={settings.edge_routing.value}, "
        "outputorder=edgesfirst, "
        f"nodesep={node_separation:.9f}, ranksep={rank_separation}, "
        f"mclimit={settings.crossing_minimization:.9f}, "
        "pad=0.20, margin=0];",
        '  node [shape=ellipse, label="", fixedsize=true, margin=0];',
        '  edge [dir=none, fontname="Arial", fontsize=10];',
    ]
    ordered_group_keys = sorted(groups, key=lambda value: (value[0], value[1]))
    group_name_by_key = {
        key: f"circuit_{group_number}"
        for group_number, key in enumerate(ordered_group_keys)
    }
    switch_group: dict[int, str] = {}
    switches_by_group: dict[str, list[int]] = defaultdict(list)
    if settings.switches_as_nodes:
        for edge in graph.edges:
            start_group = group_for_node[edge.start_block_id]
            end_group = group_for_node[edge.end_block_id]
            if start_group == end_group:
                group_name = group_name_by_key[start_group]
                switch_group[edge.switch_index] = group_name
                switches_by_group[group_name].append(edge.switch_index)

    def switch_node_declaration(switch_index: int, indent: str) -> str:
        width, height = switch_node_sizes[switch_index]
        group = switch_group.get(switch_index)
        group_attribute = "" if group is None else f', group="{group}"'
        return (
            f'{indent}"s_{switch_index}" [id="switch_node_{switch_index}", '
            'shape=box, style=invis, label="", fixedsize=true, margin=0, '
            f"width={width / _POINTS_PER_INCH:.9f}, "
            f"height={height / _POINTS_PER_INCH:.9f}{group_attribute}];"
        )

    for key in ordered_group_keys:
        # Subgrafos comuns agrupam a serializacao sem ativar o mecanismo de
        # clusters do ``dot``. Clusters invisiveis provocam ``init_rank`` e
        # ``routesplines`` em grafos grandes com muitas arestas
        # ``constraint=false`` (inclusive no runtime oficial 15.1.1). O
        # atributo ``group`` preserva a afinidade vertical do circuito sem
        # criar caixas ou participar da renderizacao.
        group_name = group_name_by_key[key]
        lines.append(f"  subgraph {group_name} {{")
        for block_id in groups[key]:
            envelope = node_envelopes[block_id]
            width = envelope.width / _POINTS_PER_INCH
            height = envelope.height / _POINTS_PER_INCH
            lines.append(
                f'    "n_{block_id}" [id="block_{block_id}", '
                f'group="{group_name}", width={width:.9f}, '
                f"height={height:.9f}];"
            )
        if settings.switches_as_nodes:
            for switch_index in sorted(switches_by_group[group_name]):
                lines.append(switch_node_declaration(switch_index, "    "))
        lines.append("  }")

    if settings.switches_as_nodes:
        for edge in sorted(graph.edges, key=lambda value: value.switch_index):
            if edge.switch_index not in switch_group:
                lines.append(switch_node_declaration(edge.switch_index, "  "))

        layer_members: dict[int, list[str]] = defaultdict(list)
        for block_id in node_ids:
            layer_members[2 * depths[block_id]].append(f"n_{block_id}")
        for edge_index, edge in sorted(
            enumerate(graph.edges),
            key=lambda value: (value[1].switch_index, value[0]),
        ):
            if edge_index in tree_edges:
                child = child_by_edge[edge_index]
                owner = parent_by_node[child]
                if owner is None:  # pragma: no cover - invariante da floresta
                    raise GraphvizLayoutError("Aresta de árvore sem nó pai.")
                layer = 2 * depths[owner] + 1
            else:
                start_depth = depths[edge.start_block_id]
                end_depth = depths[edge.end_block_id]
                if (
                    edge.start_block_id == edge.end_block_id
                    or start_depth == end_depth
                ):
                    layer = 2 * start_depth + 1
                else:
                    layer = start_depth + end_depth
            layer_members[layer].append(f"s_{edge.switch_index}")

        highest_layer = max(layer_members, default=0)
        for layer in range(highest_layer + 1):
            lines.append(
                f'  "rank_anchor_{layer}" [shape=point, style=invis, label="", '
                'fixedsize=true, width=0.01, height=0.01];'
            )
        for layer in range(highest_layer + 1):
            members = sorted(
                layer_members.get(layer, ()),
                key=lambda name: (name[0] != "n", int(name[2:])),
            )
            rank_nodes = " ".join(
                _dot_quote(name) + ";" for name in members
            )
            lines.append(
                f'  subgraph rank_layer_{layer} {{ rank=same; '
                f'"rank_anchor_{layer}"; {rank_nodes} }}'
            )
        for layer in range(highest_layer):
            lines.append(
                f'  "rank_anchor_{layer}" -> "rank_anchor_{layer + 1}" '
                "[style=invis, weight=100, minlen=1];"
            )

    for edge_index, edge in sorted(
        enumerate(graph.edges),
        key=lambda value: (value[1].switch_index, value[0]),
    ):
        tail = edge.start_block_id
        head = edge.end_block_id
        attributes: list[str] = []
        if edge_index in tree_edges:
            child = child_by_edge[edge_index]
            owner = parent_by_node[child]
            if owner is None:  # pragma: no cover - invariante da floresta
                raise GraphvizLayoutError("Aresta de árvore sem nó pai.")
            tail, head = owner, child
            attributes.extend((
                f"weight={settings.tree_edge_weight}",
                f"minlen={settings.tree_edge_minlen}",
            ))
        else:
            attributes.append("constraint=false")
        if settings.switches_as_nodes:
            common = ", ".join(attributes)
            separator = ", " if common else ""
            lines.append(
                f'  "n_{tail}" -> "s_{edge.switch_index}" '
                f'[id={_dot_quote(f"switch_{edge.switch_index}_a")}'
                f"{separator}{common}];"
            )
            lines.append(
                f'  "s_{edge.switch_index}" -> "n_{head}" '
                f'[id={_dot_quote(f"switch_{edge.switch_index}_b")}'
                f"{separator}{common}];"
            )
        else:
            attributes[:0] = [
                f'id={_dot_quote(f"switch_{edge.switch_index}")}',
                f'label={_dot_quote(edge.label)}',
            ]
            lines.append(
                f'  "n_{tail}" -> "n_{head}" [{", ".join(attributes)}];'
            )
    lines.append("}")
    return GraphvizDotInput(
        source="\n".join(lines) + "\n",
        depths=depths,
        root_ids=tuple(roots),
        tree_edge_indices=tree_edges,
        layout_groups=tuple(
            groups[key] for key in ordered_group_keys
        ),
        circuit_separation_px=settings.circuit_separation_px,
        switches_as_nodes=settings.switches_as_nodes,
    )


def graphviz_layout_cache_key(dot_input: GraphvizDotInput, version: str) -> str:
    digest = hashlib.sha256()
    digest.update(version.encode("ascii", errors="strict"))
    digest.update(b"\0")
    digest.update(dot_input.source.encode("utf-8"))
    digest.update(b"\0")
    digest.update(f"{dot_input.circuit_separation_px:.9f}".encode("ascii"))
    return digest.hexdigest()


def run_graphviz_dot(
    executable: Path | str,
    source: str,
    *,
    timeout: float = GRAPHVIZ_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
) -> bytes:
    """Executa exclusivamente ``dot -Kdot -Tjson`` por stdin/stdout."""

    path = Path(executable)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [str(path), "-Kdot", "-Tjson"],
            cwd=str(path.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if platform.system() == "Windows"
                else 0
            ),
        )
    except OSError as exc:
        raise GraphvizLayoutError(f"Não foi possível iniciar o dot: {exc}") from exc
    pending_input: bytes | None = source.encode("utf-8")
    while True:
        if cancel_event is not None and cancel_event.is_set():
            process.kill()
            process.communicate()
            raise GraphvizLayoutCancelled("Cálculo Graphviz cancelado.")
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0.0:
            process.kill()
            _stdout, stderr = process.communicate()
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise GraphvizLayoutError(
                f"O cálculo Graphviz excedeu {timeout:g} segundos."
                + (f" {detail}" if detail else "")
            )
        try:
            stdout, stderr = process.communicate(
                input=pending_input,
                timeout=min(0.10, remaining),
            )
            break
        except subprocess.TimeoutExpired:
            pending_input = None
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise GraphvizLayoutError(
            f"dot terminou com código {process.returncode}: {detail or 'sem detalhes'}"
        )
    if not stdout:
        raise GraphvizLayoutError("dot não devolveu geometria JSON.")
    return stdout


def _parse_point(value: object) -> tuple[float, float]:
    parts = str(value).rstrip("!").split(",")
    if len(parts) != 2:
        raise GraphvizLayoutError(f"Coordenada Graphviz inválida: {value!r}")
    point = (float(parts[0]), float(parts[1]))
    if not all(math.isfinite(component) for component in point):
        raise GraphvizLayoutError("A geometria Graphviz contém valor não finito.")
    return point


def _route_midpoint(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    lengths = [math.dist(start, end) for start, end in zip(points, points[1:])]
    total = math.fsum(lengths)
    if total <= 1.0e-12:
        return points[0]
    target = total / 2.0
    traversed = 0.0
    for start, end, length in zip(points, points[1:], lengths):
        if traversed + length >= target:
            ratio = (target - traversed) / max(length, 1.0e-12)
            return (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
        traversed += length
    return points[-1]


def _circle_endpoint(
    center: tuple[float, float],
    toward: tuple[float, float],
    diameter: float,
) -> tuple[float, float]:
    dx = toward[0] - center[0]
    dy = toward[1] - center[1]
    length = math.hypot(dx, dy)
    if length <= 1.0e-12:
        return center
    radius = diameter / 2.0
    return (center[0] + dx / length * radius, center[1] + dy / length * radius)


@dataclass(frozen=True, slots=True)
class _GraphvizRoutePart:
    tail_name: str
    head_name: str
    points: tuple[tuple[float, float], ...]
    cubic: bool


def _merge_switch_route_parts(
    first: _GraphvizRoutePart,
    second: _GraphvizRoutePart,
    switch_center: tuple[float, float],
) -> BlockGraphEdgeRoute:
    """Une as duas splines ocultando a emenda sob a etiqueta Qt."""

    if first.cubic != second.cubic:
        raise GraphvizLayoutError("As duas metades da chave têm traçados incompatíveis.")
    points = list(first.points)
    if first.cubic:
        for destination in (switch_center, second.points[0]):
            origin = points[-1]
            if origin == destination:
                continue
            points.extend(
                (
                    (
                        origin[0] + (destination[0] - origin[0]) / 3.0,
                        origin[1] + (destination[1] - origin[1]) / 3.0,
                    ),
                    (
                        origin[0] + 2.0 * (destination[0] - origin[0]) / 3.0,
                        origin[1] + 2.0 * (destination[1] - origin[1]) / 3.0,
                    ),
                    destination,
                )
            )
        points.extend(second.points[1:])
        return BlockGraphEdgeRoute(tuple(points), cubic=True)

    for point in (switch_center, *second.points):
        if point != points[-1]:
            points.append(point)
    return BlockGraphEdgeRoute(tuple(points))


def parse_graphviz_json(
    payload: bytes | str | Mapping[str, object],
    graph: BlockGraph,
    dot_input: GraphvizDotInput,
    node_envelopes: Mapping[int, BlockNodeEnvelope],
) -> BlockGraphLayout:
    """Converte posições, splines e âncoras JSON para coordenadas do Qt."""

    try:
        document = (
            dict(payload)
            if isinstance(payload, Mapping)
            else json.loads(
                payload.decode("utf-8") if isinstance(payload, bytes) else payload
            )
        )
        bounds = [float(value) for value in str(document["bb"]).split(",")]
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise GraphvizLayoutError("JSON Graphviz inválido.") from exc
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        raise GraphvizLayoutError("Limites Graphviz inválidos.")
    center_x = (bounds[0] + bounds[2]) / 2.0
    center_y = (bounds[1] + bounds[3]) / 2.0

    def convert(point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] - center_x, center_y - point[1])

    positions: dict[int, tuple[float, float]] = {}
    switch_positions: dict[int, tuple[float, float]] = {}
    object_names: dict[int, str] = {}
    raw_objects = document.get("objects", ())
    if not isinstance(raw_objects, Sequence) or isinstance(
        raw_objects, (str, bytes, bytearray)
    ):
        raise GraphvizLayoutError("Lista de nós Graphviz inválida.")
    for raw in raw_objects:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name", ""))
        try:
            if "_gvid" in raw:
                object_index = int(raw["_gvid"])
                if object_index in object_names:
                    raise ValueError("_gvid duplicado")
                object_names[object_index] = name
            elif dot_input.switches_as_nodes:
                raise KeyError("_gvid")
            if name.startswith("n_"):
                block_id = int(name[2:])
                if block_id in positions:
                    raise ValueError("bloco duplicado")
                positions[block_id] = convert(_parse_point(raw["pos"]))
            elif dot_input.switches_as_nodes and name.startswith("s_"):
                switch_index = int(name[2:])
                if switch_index in switch_positions:
                    raise ValueError("chave duplicada")
                switch_positions[switch_index] = convert(_parse_point(raw["pos"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise GraphvizLayoutError("Posição de nó Graphviz inválida.") from exc
    expected_nodes = set(graph.node_ids)
    if set(positions) != expected_nodes:
        raise GraphvizLayoutError("O Graphviz não posicionou todos os blocos.")

    edge_by_switch = {edge.switch_index: edge for edge in graph.edges}
    if dot_input.switches_as_nodes and set(switch_positions) != set(edge_by_switch):
        raise GraphvizLayoutError("O Graphviz não posicionou todas as chaves.")
    routes: dict[int, BlockGraphEdgeRoute] = {}
    labels: dict[int, tuple[float, float]] = {}
    route_parts: dict[int, dict[str, _GraphvizRoutePart]] = defaultdict(dict)
    raw_edges = document.get("edges", ())
    if not isinstance(raw_edges, Sequence) or isinstance(
        raw_edges, (str, bytes, bytearray)
    ):
        raise GraphvizLayoutError("Lista de arestas Graphviz inválida.")
    for raw in raw_edges:
        if not isinstance(raw, Mapping):
            continue
        identifier = str(raw.get("id", ""))
        if not identifier.startswith("switch_"):
            continue
        raw_identifier = identifier[len("switch_") :]
        half = ""
        if dot_input.switches_as_nodes:
            raw_identifier, separator, half = raw_identifier.rpartition("_")
            if separator != "_" or half not in {"a", "b"}:
                raise GraphvizLayoutError(
                    "Identificador de metade de chave Graphviz inválido."
                )
        try:
            switch_index = int(raw_identifier)
        except ValueError as exc:
            raise GraphvizLayoutError("Identificador de aresta Graphviz inválido.") from exc
        if switch_index not in edge_by_switch:
            raise GraphvizLayoutError("Aresta Graphviz ausente ou duplicada.")
        if dot_input.switches_as_nodes:
            if half in route_parts[switch_index]:
                raise GraphvizLayoutError("Metade de chave Graphviz duplicada.")
        elif switch_index in routes:
            raise GraphvizLayoutError("Aresta Graphviz ausente ou duplicada.")
        raw_draw = raw.get("_draw_", ())
        if not isinstance(raw_draw, Sequence) or isinstance(
            raw_draw, (str, bytes, bytearray)
        ):
            raise GraphvizLayoutError("Spline Graphviz ausente ou inválida.")
        spline_operations = [
            operation
            for operation in raw_draw
            if isinstance(operation, Mapping)
            and operation.get("op") in {"b", "B", "L"}
        ]
        points: list[tuple[float, float]] = []
        for operation in spline_operations:
            operation_points = [
                convert((float(point[0]), float(point[1])))
                for point in operation.get("points", ())
            ]
            if points and operation_points and points[-1] == operation_points[0]:
                operation_points = operation_points[1:]
            points.extend(operation_points)
        if len(points) < 2 or not all(
            math.isfinite(value) for point in points for value in point
        ):
            raise GraphvizLayoutError("Spline Graphviz ausente ou inválida.")
        cubic = (
            len(spline_operations) == 1
            and spline_operations[0].get("op") in {"b", "B"}
            and (len(points) - 1) % 3 == 0
        )
        if dot_input.switches_as_nodes:
            try:
                tail_name = object_names[int(raw["tail"])]
                head_name = object_names[int(raw["head"])]
            except (KeyError, TypeError, ValueError) as exc:
                raise GraphvizLayoutError(
                    "Extremidades de chave Graphviz inválidas."
                ) from exc
            route_parts[switch_index][half] = _GraphvizRoutePart(
                tail_name,
                head_name,
                tuple(points),
                cubic,
            )
            continue

        edge = edge_by_switch[switch_index]
        start_center = positions[edge.start_block_id]
        end_center = positions[edge.end_block_id]
        points[0] = _circle_endpoint(
            start_center,
            points[1],
            node_envelopes[edge.start_block_id].node_diameter,
        )
        points[-1] = _circle_endpoint(
            end_center,
            points[-2],
            node_envelopes[edge.end_block_id].node_diameter,
        )
        routes[switch_index] = BlockGraphEdgeRoute(
            tuple(points),
            curved=not cubic and len(points) == 3,
            cubic=cubic,
        )
        label_position = raw.get("lp")
        labels[switch_index] = (
            convert(_parse_point(label_position))
            if label_position is not None
            else _route_midpoint(points)
        )

    if dot_input.switches_as_nodes:
        for switch_index, edge in sorted(edge_by_switch.items()):
            parts = route_parts.get(switch_index, {})
            if set(parts) != {"a", "b"}:
                raise GraphvizLayoutError("O Graphviz não calculou as duas metades da chave.")
            switch_name = f"s_{switch_index}"
            start_name = f"n_{edge.start_block_id}"
            end_name = f"n_{edge.end_block_id}"

            def oriented(
                part: _GraphvizRoutePart,
                expected_tail: str,
                expected_head: str,
            ) -> _GraphvizRoutePart:
                if (part.tail_name, part.head_name) == (
                    expected_tail,
                    expected_head,
                ):
                    return part
                if (part.tail_name, part.head_name) == (
                    expected_head,
                    expected_tail,
                ):
                    return _GraphvizRoutePart(
                        expected_tail,
                        expected_head,
                        tuple(reversed(part.points)),
                        part.cubic,
                    )
                raise GraphvizLayoutError(
                    "Uma metade da chave não liga os nós esperados."
                )

            if edge.start_block_id == edge.end_block_id:
                first = oriented(parts["a"], start_name, switch_name)
                second = oriented(parts["b"], switch_name, end_name)
            else:
                start_part = next(
                    (
                        part
                        for part in parts.values()
                        if {part.tail_name, part.head_name}
                        == {start_name, switch_name}
                    ),
                    None,
                )
                end_part = next(
                    (
                        part
                        for part in parts.values()
                        if {part.tail_name, part.head_name}
                        == {end_name, switch_name}
                    ),
                    None,
                )
                if start_part is None or end_part is None or start_part is end_part:
                    raise GraphvizLayoutError(
                        "As metades da chave não correspondem aos blocos."
                    )
                first = oriented(start_part, start_name, switch_name)
                second = oriented(end_part, switch_name, end_name)
            route = _merge_switch_route_parts(
                first,
                second,
                switch_positions[switch_index],
            )
            points = list(route.points)
            points[0] = _circle_endpoint(
                positions[edge.start_block_id],
                points[1],
                node_envelopes[edge.start_block_id].node_diameter,
            )
            points[-1] = _circle_endpoint(
                positions[edge.end_block_id],
                points[-2],
                node_envelopes[edge.end_block_id].node_diameter,
            )
            routes[switch_index] = BlockGraphEdgeRoute(
                tuple(points),
                curved=route.curved,
                cubic=route.cubic,
            )
            labels[switch_index] = switch_positions[switch_index]
    if set(routes) != set(edge_by_switch) or set(labels) != set(edge_by_switch):
        raise GraphvizLayoutError("O Graphviz não calculou todas as chaves.")
    if set(dot_input.depths) != expected_nodes:
        raise GraphvizLayoutError("A floresta Graphviz está incompleta.")
    return BlockGraphLayout(
        positions=positions,
        depths=dict(dot_input.depths),
        root_ids=dot_input.root_ids,
        tree_edge_indices=dot_input.tree_edge_indices,
        edge_routes=routes,
        edge_label_positions=labels,
    )


def _route_fractions(
    points: Sequence[tuple[float, float]],
) -> tuple[float, ...]:
    lengths = [math.dist(start, end) for start, end in zip(points, points[1:])]
    total = math.fsum(lengths)
    if total <= 1.0e-12:
        denominator = max(1, len(points) - 1)
        return tuple(index / denominator for index in range(len(points)))
    traversed = 0.0
    fractions = [0.0]
    for length in lengths:
        traversed += length
        fractions.append(traversed / total)
    return tuple(fractions)


def _point_route_fraction(
    point: tuple[float, float],
    route: Sequence[tuple[float, float]],
) -> float:
    """Localiza aproximadamente um ponto ao longo de uma rota lógica."""

    if len(route) < 2:
        return 0.5
    lengths = [math.dist(start, end) for start, end in zip(route, route[1:])]
    total = math.fsum(lengths)
    if total <= 1.0e-12:
        return 0.5
    best_distance = math.inf
    best_fraction = 0.5
    traversed = 0.0
    for start, end, length in zip(route, route[1:], lengths):
        if length <= 1.0e-12:
            traversed += length
            continue
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        projection = max(
            0.0,
            min(
                1.0,
                ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
                / (length * length),
            ),
        )
        nearest = (
            start[0] + dx * projection,
            start[1] + dy * projection,
        )
        distance = math.dist(point, nearest)
        if distance < best_distance:
            best_distance = distance
            best_fraction = (traversed + length * projection) / total
        traversed += length
    return best_fraction


def separate_graphviz_circuit_groups(
    layout: BlockGraphLayout,
    graph: BlockGraph,
    layout_groups: Sequence[Sequence[int]],
    node_envelopes: Mapping[int, BlockNodeEnvelope],
    *,
    minimum_separation: float,
    edge_label_sizes: Mapping[int, Sequence[float]] | None = None,
) -> BlockGraphLayout:
    """Afasta grupos do ``dot`` sem modificar sua geometria interna.

    O eixo Y permanece intocado para preservar os ranks. Uma varredura
    horizontal mantém a ordem original e garante a folga entre todos os grupos,
    mesmo quando seus envelopes ocupam alturas diferentes. Arestas entre grupos
    são alongadas pela interpolação dos deslocamentos das duas pontas.
    """

    separation = float(minimum_separation)
    if not math.isfinite(separation) or separation < 0.0:
        raise GraphvizLayoutError("Espaçamento entre circuitos inválido.")
    groups = tuple(
        tuple(int(block_id) for block_id in group)
        for group in layout_groups
    )
    if separation <= 0.0 or len(groups) < 2:
        return layout

    expected_nodes = set(graph.node_ids)
    grouped_nodes = [block_id for group in groups for block_id in group]
    if (
        len(grouped_nodes) != len(set(grouped_nodes))
        or set(grouped_nodes) != expected_nodes
    ):
        raise GraphvizLayoutError("Os grupos Graphviz não correspondem aos blocos.")
    if set(node_envelopes) != expected_nodes or set(layout.positions) != expected_nodes:
        raise GraphvizLayoutError("Os envelopes Graphviz não correspondem aos blocos.")

    group_by_node = {
        block_id: group_index
        for group_index, group in enumerate(groups)
        for block_id in group
    }
    bounds = [
        [math.inf, math.inf, -math.inf, -math.inf]
        for _group in groups
    ]

    def include_point(group_index: int, point: tuple[float, float]) -> None:
        box = bounds[group_index]
        box[0] = min(box[0], point[0])
        box[1] = min(box[1], point[1])
        box[2] = max(box[2], point[0])
        box[3] = max(box[3], point[1])

    for block_id, center in layout.positions.items():
        envelope = node_envelopes[block_id]
        half_width = envelope.width / 2.0
        half_height = envelope.height / 2.0
        group_index = group_by_node[block_id]
        include_point(group_index, (center[0] - half_width, center[1] - half_height))
        include_point(group_index, (center[0] + half_width, center[1] + half_height))

    edge_by_switch = {edge.switch_index: edge for edge in graph.edges}
    for switch_index, route in layout.edge_routes.items():
        edge = edge_by_switch.get(switch_index)
        if edge is None:
            continue
        start_group = group_by_node[edge.start_block_id]
        if start_group != group_by_node[edge.end_block_id]:
            continue
        for point in route.points:
            include_point(start_group, point)
        label = layout.edge_label_positions.get(switch_index)
        size = None if edge_label_sizes is None else edge_label_sizes.get(switch_index)
        if label is not None and size is not None and len(size) >= 2:
            width = float(size[0])
            height = float(size[1])
            if all(math.isfinite(value) and value >= 0.0 for value in (width, height)):
                include_point(
                    start_group,
                    (label[0] - width / 2.0, label[1] - height / 2.0),
                )
                include_point(
                    start_group,
                    (label[0] + width / 2.0, label[1] + height / 2.0),
                )
        leader = layout.edge_label_leaders.get(switch_index)
        if leader is not None:
            include_point(start_group, leader[0])
            include_point(start_group, leader[1])

    if any(not all(math.isfinite(value) for value in box) for box in bounds):
        raise GraphvizLayoutError("Envelope de circuito Graphviz inválido.")

    order = sorted(
        range(len(groups)),
        key=lambda index: (
            (bounds[index][0] + bounds[index][2]) / 2.0,
            groups[index],
        ),
    )
    translations: dict[int, float] = {}
    for order_index, group_index in enumerate(order):
        left = bounds[group_index][0]
        translation = 0.0
        for previous in order[:order_index]:
            previous_right = bounds[previous][2]
            translation = max(
                translation,
                previous_right + translations[previous] + separation - left,
            )
        translations[group_index] = translation

    if not any(abs(value) > 1.0e-9 for value in translations.values()):
        return layout
    final_left = min(
        bounds[index][0] + translations[index] for index in range(len(groups))
    )
    final_right = max(
        bounds[index][2] + translations[index] for index in range(len(groups))
    )
    center_x = (final_left + final_right) / 2.0
    translations = {
        index: value - center_x for index, value in translations.items()
    }
    translated_positions = {
        block_id: (
            position[0] + translations[group_by_node[block_id]],
            position[1],
        )
        for block_id, position in layout.positions.items()
    }

    translated_routes: dict[int, BlockGraphEdgeRoute] = {}
    translated_labels: dict[int, tuple[float, float]] = {}
    translated_leaders: dict[
        int,
        tuple[tuple[float, float], tuple[float, float]],
    ] = {}
    for switch_index, route in layout.edge_routes.items():
        edge = edge_by_switch[switch_index]
        start_group = group_by_node[edge.start_block_id]
        end_group = group_by_node[edge.end_block_id]
        start_translation = translations[start_group]
        end_translation = translations[end_group]
        raw_points = route.points
        fractions = _route_fractions(raw_points)
        points = [
            (
                point[0]
                + start_translation
                + (end_translation - start_translation) * fraction,
                point[1],
            )
            for point, fraction in zip(raw_points, fractions)
        ]
        if edge.start_block_id != edge.end_block_id and len(points) >= 2:
            start_toward = points[1]
            end_toward = points[-2]
            points[0] = _circle_endpoint(
                translated_positions[edge.start_block_id],
                start_toward,
                node_envelopes[edge.start_block_id].node_diameter,
            )
            points[-1] = _circle_endpoint(
                translated_positions[edge.end_block_id],
                end_toward,
                node_envelopes[edge.end_block_id].node_diameter,
            )
        translated_routes[switch_index] = BlockGraphEdgeRoute(
            tuple(points),
            curved=route.curved,
            cubic=route.cubic,
        )

        label = layout.edge_label_positions.get(switch_index)
        if label is not None:
            fraction = _point_route_fraction(label, raw_points)
            translated_labels[switch_index] = (
                label[0]
                + start_translation
                + (end_translation - start_translation) * fraction,
                label[1],
            )
        leader = layout.edge_label_leaders.get(switch_index)
        if leader is not None:
            translated = tuple(
                (
                    point[0]
                    + start_translation
                    + (end_translation - start_translation)
                    * _point_route_fraction(point, raw_points),
                    point[1],
                )
                for point in leader
            )
            translated_leaders[switch_index] = (translated[0], translated[1])

    return BlockGraphLayout(
        positions=translated_positions,
        depths=dict(layout.depths),
        root_ids=layout.root_ids,
        tree_edge_indices=layout.tree_edge_indices,
        edge_routes=translated_routes,
        edge_label_positions=translated_labels,
        edge_label_leaders=translated_leaders,
    )


def calculate_graphviz_layout(
    executable: Path | str,
    dot_input: GraphvizDotInput,
    graph: BlockGraph,
    node_envelopes: Mapping[int, BlockNodeEnvelope],
    *,
    timeout: float = GRAPHVIZ_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
    edge_label_sizes: Mapping[int, Sequence[float]] | None = None,
) -> BlockGraphLayout:
    payload = run_graphviz_dot(
        executable,
        dot_input.source,
        timeout=timeout,
        cancel_event=cancel_event,
    )
    layout = parse_graphviz_json(payload, graph, dot_input, node_envelopes)
    return separate_graphviz_circuit_groups(
        layout,
        graph,
        dot_input.layout_groups,
        node_envelopes,
        minimum_separation=dot_input.circuit_separation_px,
        edge_label_sizes=edge_label_sizes,
    )


__all__ = [
    "DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS",
    "GRAPHVIZ_CACHE_SIZE",
    "GRAPHVIZ_CIRCUIT_SEPARATION_RANGE",
    "GRAPHVIZ_CROSSING_MINIMIZATION_RANGE",
    "GRAPHVIZ_DISTRIBUTION",
    "GRAPHVIZ_DOWNLOAD_URL",
    "GRAPHVIZ_NODE_SEPARATION_RANGE",
    "GRAPHVIZ_RANK_SEPARATION_RANGE",
    "GRAPHVIZ_SHA256",
    "GRAPHVIZ_TIMEOUT_SECONDS",
    "GRAPHVIZ_TREE_EDGE_MINLEN_RANGE",
    "GRAPHVIZ_TREE_EDGE_WEIGHT_RANGE",
    "GRAPHVIZ_VERSION",
    "GraphvizDotInput",
    "GraphvizEdgeRouting",
    "GraphvizLayoutCancelled",
    "GraphvizLayoutError",
    "GraphvizLayoutSettings",
    "GraphvizRuntimeStatus",
    "bundled_graphviz_dot",
    "bundled_graphviz_root",
    "calculate_graphviz_layout",
    "graphviz_layout_cache_key",
    "graphviz_layout_settings_from_mapping",
    "parse_graphviz_json",
    "probe_graphviz_runtime",
    "run_graphviz_dot",
    "separate_graphviz_circuit_groups",
    "serialize_graphviz_dot",
]
