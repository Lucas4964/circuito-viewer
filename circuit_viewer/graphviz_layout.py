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
GRAPHVIZ_NODE_SEPARATION_RANGE = (2.0, 500.0)
GRAPHVIZ_RANK_SEPARATION_RANGE = (2.0, 800.0)
GRAPHVIZ_TREE_EDGE_WEIGHT_RANGE = (1, 100)
GRAPHVIZ_TREE_EDGE_MINLEN_RANGE = (1, 10)
GRAPHVIZ_CROSSING_MINIMIZATION_RANGE = (0.1, 4.0)


class GraphvizEdgeRouting(str, Enum):
    """Traçados compatíveis com etiquetas e com o parser geométrico Qt."""

    SPLINE = "spline"
    POLYLINE = "polyline"
    LINE = "line"


@dataclass(frozen=True, slots=True)
class GraphvizLayoutSettings:
    """Parâmetros seguros de geometria expostos ao ajuste fino do usuário."""

    node_separation_px: float = 32.0
    rank_separation_px: float = 56.0
    edge_routing: GraphvizEdgeRouting = GraphvizEdgeRouting.SPLINE
    equal_rank_spacing: bool = False
    tree_edge_weight: int = 8
    tree_edge_minlen: int = 1
    crossing_minimization: float = 1.0

    def __post_init__(self) -> None:
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
        validations = (
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
        object.__setattr__(self, "rank_separation_px", rank_separation)
        object.__setattr__(self, "edge_routing", edge_routing)
        object.__setattr__(
            self,
            "equal_rank_spacing",
            bool(self.equal_rank_spacing),
        )
        object.__setattr__(self, "tree_edge_weight", tree_edge_weight)
        object.__setattr__(self, "tree_edge_minlen", tree_edge_minlen)
        object.__setattr__(self, "crossing_minimization", crossing_minimization)

    def as_mapping(self) -> dict[str, float | int | str | bool]:
        return {
            "node_separation_px": self.node_separation_px,
            "rank_separation_px": self.rank_separation_px,
            "edge_routing": self.edge_routing.value,
            "equal_rank_spacing": self.equal_rank_spacing,
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
    for group_number, key in enumerate(
        sorted(groups, key=lambda value: (value[0], value[1]))
    ):
        # Subgrafos comuns agrupam a serializacao sem ativar o mecanismo de
        # clusters do ``dot``. Clusters invisiveis provocam ``init_rank`` e
        # ``routesplines`` em grafos grandes com muitas arestas
        # ``constraint=false`` (inclusive no runtime oficial 15.1.1). O
        # atributo ``group`` preserva a afinidade vertical do circuito sem
        # criar caixas ou participar da renderizacao.
        group_name = f"circuit_{group_number}"
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
        lines.append("  }")
    for edge_index, edge in sorted(
        enumerate(graph.edges),
        key=lambda value: (value[1].switch_index, value[0]),
    ):
        tail = edge.start_block_id
        head = edge.end_block_id
        attributes = [
            f'id={_dot_quote(f"switch_{edge.switch_index}")}',
            f'label={_dot_quote(edge.label)}',
        ]
        if edge_index in tree_edges:
            child = child_by_edge[edge_index]
            owner = parent_by_node[child]
            if owner is None:  # pragma: no cover - invariante da floresta
                raise GraphvizLayoutError("Aresta de árvore sem nó pai.")
            tail, head = owner, child
            attributes.extend(
                (
                    f"weight={settings.tree_edge_weight}",
                    f"minlen={settings.tree_edge_minlen}",
                )
            )
        else:
            attributes.append("constraint=false")
        lines.append(
            f'  "n_{tail}" -> "n_{head}" [{", ".join(attributes)}];'
        )
    lines.append("}")
    return GraphvizDotInput(
        source="\n".join(lines) + "\n",
        depths=depths,
        root_ids=tuple(roots),
        tree_edge_indices=tree_edges,
    )


def graphviz_layout_cache_key(dot_input: GraphvizDotInput, version: str) -> str:
    digest = hashlib.sha256()
    digest.update(version.encode("ascii", errors="strict"))
    digest.update(b"\0")
    digest.update(dot_input.source.encode("utf-8"))
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
    raw_objects = document.get("objects", ())
    if not isinstance(raw_objects, Sequence) or isinstance(
        raw_objects, (str, bytes, bytearray)
    ):
        raise GraphvizLayoutError("Lista de nós Graphviz inválida.")
    for raw in raw_objects:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name", ""))
        if not name.startswith("n_"):
            continue
        try:
            block_id = int(name[2:])
            positions[block_id] = convert(_parse_point(raw["pos"]))
        except (KeyError, ValueError) as exc:
            raise GraphvizLayoutError("Posição de nó Graphviz inválida.") from exc
    expected_nodes = set(graph.node_ids)
    if set(positions) != expected_nodes:
        raise GraphvizLayoutError("O Graphviz não posicionou todos os blocos.")

    edge_by_switch = {edge.switch_index: edge for edge in graph.edges}
    routes: dict[int, BlockGraphEdgeRoute] = {}
    labels: dict[int, tuple[float, float]] = {}
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
        try:
            switch_index = int(identifier[len("switch_") :])
        except ValueError as exc:
            raise GraphvizLayoutError("Identificador de aresta Graphviz inválido.") from exc
        if switch_index not in edge_by_switch or switch_index in routes:
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
        edge = edge_by_switch[switch_index]
        start_center = positions[edge.start_block_id]
        end_center = positions[edge.end_block_id]
        if edge.start_block_id == edge.end_block_id:
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
        else:
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
        cubic = (
            len(spline_operations) == 1
            and spline_operations[0].get("op") in {"b", "B"}
            and (len(points) - 1) % 3 == 0
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


def calculate_graphviz_layout(
    executable: Path | str,
    dot_input: GraphvizDotInput,
    graph: BlockGraph,
    node_envelopes: Mapping[int, BlockNodeEnvelope],
    *,
    timeout: float = GRAPHVIZ_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
) -> BlockGraphLayout:
    payload = run_graphviz_dot(
        executable,
        dot_input.source,
        timeout=timeout,
        cancel_event=cancel_event,
    )
    return parse_graphviz_json(payload, graph, dot_input, node_envelopes)


__all__ = [
    "DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS",
    "GRAPHVIZ_CACHE_SIZE",
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
    "serialize_graphviz_dot",
]
