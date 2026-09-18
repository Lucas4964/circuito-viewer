"""Orientação da rede: quem alimenta quem, a partir das barras iniciais.

A rede do projeto é um grafo **não-direcionado**. ``NetworkTopology`` grava as
duas pontas de cada trecho, e ``start_indices``/``end_indices`` não dizem qual é
a de montante — ``branch_power_flow`` precisa negar o sinal da corrente
justamente porque a coluna não é confiável. Este módulo é o que dá direção à
rede: uma busca em largura a partir de **todas** as barras iniciais do catálogo,
que grava para cada barra o pai, o trecho que a alimenta e a profundidade.

Com isso "a jusante de X" é a subárvore de X, "a montante de X" é o caminho até
a raiz, e "qual ponta deste trecho é a de jusante" é uma consulta O(1). É a base
de toda análise sobre regiões da rede, e por isso mora num módulo próprio, e não
dentro da ferramenta que primeiro precisou dela.

**A configuração é a real.** Só conduz a chave com ``ESTADO == "1"`` — o mesmo
predicado literal de :meth:`NetworkTopology.trace`, porque divergir aí faria as
duas travessias discordarem sobre o que conduz sem ninguém perceber. Fechar as
chaves normalmente abertas fecharia laços, e a árvore de pais deixaria de ser
uma resposta para virar uma escolha.

**Divergência deliberada do ``trace``:** a orientação não testa ``CIRC_ID``.
Chave fechada conduz, seja de quem for. Onde a orientação e a associação do
catálogo discordarem, a orientação é a verdade física e a associação é a
cadastral — e nenhuma das duas pode ser apresentada como a outra.

**A rede não é garantidamente radial**, e o resultado não finge que é. O BFS
quebra cada laço num ponto; os trechos que sobram fora da árvore são as
**cordas**, e ficam registrados. Duas fontes alcançando a mesma ilha também: o
pai que cada barra recebe é o da fonte mais próxima em saltos, que é
determinístico mas não é o ponto real onde o fluxo se divide.

Camada de núcleo, sem Qt: testável sem interface, no molde de ``block_analysis``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .model import CircuitCatalogModel, IndexArray, NetworkTopology


CancelCheck = Callable[[], bool]

#: Mesma cadência de verificação das demais análises do projeto.
_CANCEL_CHECK_INTERVAL = 4_096

#: Conduz, mas nenhuma das pontas foi alcançada: uma ilha sem fonte.
SEGMENT_UNREACHED = 0
#: Aresta da árvore: é por ela que a barra filha é alimentada.
SEGMENT_TREE = 1
#: Conduz e fecha um laço; nunca foi aresta da árvore.
SEGMENT_CHORD = 2
#: Chave aberta, ou com ESTADO que não é ``"1"``: não conduz.
SEGMENT_OPEN = 3

#: Estados de chave reconhecidos. Fora deles a chave não conduz — como no
#: ``trace`` —, e a contagem vira ocorrência.
_KNOWN_STATES = frozenset({"0", "1"})


def _readonly_indices(values) -> IndexArray:  # noqa: ANN001
    result = np.ascontiguousarray(values, dtype=np.intp)
    if result.ndim != 1:
        raise ValueError("Os índices devem formar um vetor unidimensional.")
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class OrientationIssue:
    """Ocorrência que qualifica a orientação — nunca uma por barra."""

    kind: str
    message: str
    segment_id: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkOrientation:
    """A rede com direção: pais, profundidades e filhos de cada barra.

    Os vetores por barra usam ``-1`` para "sem resposta": a raiz não tem pai, e a
    barra inalcançável — além de chave aberta, numa ilha sem fonte, ou sem trecho
    algum — não tem pai, profundidade nem raiz. ``depth`` é ``-1`` e não ``0``
    nesse caso porque ``0`` é a profundidade da raiz, e os dois não podem colidir.

    Os filhos formam uma floresta em CSR: os filhos da barra ``b`` são
    ``children_bars[children_offsets[b]:children_offsets[b + 1]]``, alimentados
    pelos trechos de mesma posição em ``children_segments``. Como toda barra é
    filha de no máximo uma, descer a árvore não precisa de vetor de visitados.
    """

    topology: NetworkTopology
    catalog: CircuitCatalogModel
    parent_bar: IndexArray
    parent_segment: IndexArray
    depth: IndexArray
    root_of: IndexArray
    circuit_of_bar: IndexArray
    children_offsets: IndexArray
    children_bars: IndexArray
    children_segments: IndexArray
    segment_role: np.ndarray
    tree_child_bar: IndexArray
    chord_segment_indices: IndexArray
    root_bar_indices: IndexArray
    multi_source_contacts: tuple[tuple[int, int, int], ...]
    unreachable_bar_count: int
    issues: tuple[OrientationIssue, ...]

    def __post_init__(self) -> None:
        segments = self.topology.segments
        bar_count = len(segments.bars)
        segment_count = len(segments)
        for name, size in (
            ("parent_bar", bar_count),
            ("parent_segment", bar_count),
            ("depth", bar_count),
            ("root_of", bar_count),
            ("circuit_of_bar", bar_count),
            ("children_offsets", bar_count + 1),
            ("tree_child_bar", segment_count),
        ):
            values = getattr(self, name)
            if values.dtype != np.dtype(np.intp) or values.shape != (size,):
                raise ValueError(f"{name} deve ser um vetor de {size:n} índices.")
        if self.segment_role.shape != (segment_count,):
            raise ValueError("Deve haver um papel para cada trecho.")
        for values in (
            self.parent_bar,
            self.parent_segment,
            self.depth,
            self.root_of,
            self.circuit_of_bar,
            self.children_offsets,
            self.children_bars,
            self.children_segments,
            self.segment_role,
            self.tree_child_bar,
            self.chord_segment_indices,
            self.root_bar_indices,
        ):
            # Sobrevive entre cliques: um consumidor que mutasse uma fatia
            # corromperia a orientação para o resto da sessão.
            if values.flags.writeable:
                raise ValueError("Os vetores da orientação devem ser imutáveis.")

    @property
    def segments(self):  # noqa: ANN201 — LineNetworkModel
        return self.topology.segments

    @property
    def switches(self):  # noqa: ANN201 — SwitchModel | None
        return self.topology.switches

    def children_of(self, bar_index: int) -> tuple[IndexArray, IndexArray]:
        """Filhos diretos da barra e os trechos que os alimentam."""

        start = int(self.children_offsets[bar_index])
        stop = int(self.children_offsets[bar_index + 1])
        return self.children_bars[start:stop], self.children_segments[start:stop]

    def is_reached(self, bar_index: int) -> bool:
        return int(self.depth[bar_index]) >= 0


def conducting_segments(topology: NetworkTopology) -> np.ndarray:
    """Máscara dos trechos que conduzem na configuração atual.

    Trecho comum sempre conduz; trecho de chave só com ``ESTADO == "1"``. É o
    predicado de :meth:`NetworkTopology.trace`, extraído para quem precisa dele
    sem travessia.
    """

    conducts = np.ones(len(topology.segments), dtype=np.bool_)
    switches = topology.switches
    if switches is not None and len(switches):
        closed = np.fromiter(
            (state.strip() == "1" for state in switches.states),
            dtype=np.bool_,
            count=len(switches),
        )
        conducts[switches.segment_indices] = closed
    return conducts


def build_orientation(
    topology: NetworkTopology,
    catalog: CircuitCatalogModel,
    *,
    cancel_check: CancelCheck | None = None,
) -> NetworkOrientation:
    """Orienta a rede a partir das barras iniciais de todos os circuitos.

    Um BFS só, semeado com todas as raízes na ordem do catálogo: quando duas
    fontes alcançam a mesma barra, vence a mais próxima em saltos, e no empate a
    que vem antes no catálogo. O pai de cada barra é único e determinístico.
    """

    segments = topology.segments
    if catalog.segments is not segments or catalog.switches is not topology.switches:
        raise ValueError("O catálogo deve pertencer à rede da topologia.")
    switches = topology.switches
    bars = segments.bars
    bar_count = len(bars)
    segment_count = len(segments)
    issues: list[OrientationIssue] = []

    conducts = conducting_segments(topology)

    parent_bar = np.full(bar_count, -1, dtype=np.intp)
    parent_segment = np.full(bar_count, -1, dtype=np.intp)
    depth = np.full(bar_count, -1, dtype=np.intp)
    root_of = np.full(bar_count, -1, dtype=np.intp)
    circuit_of_bar = np.full(bar_count, -1, dtype=np.intp)
    segment_role = np.zeros(segment_count, dtype=np.int8)

    queue: deque[int] = deque()
    roots: list[int] = []
    for circuit_index, definition in enumerate(catalog.definitions):
        root = bars.index_for_id(definition.root_bar_id)
        if root is None:
            # O catálogo já recusa isto na construção; a guarda fica para não
            # depender de uma validação que mora em outro lugar.
            issues.append(
                OrientationIssue(
                    "raiz-inexistente",
                    f"Circuito {definition.circuit_id}: barra inicial "
                    f"{definition.root_bar_id} inexistente.",
                )
            )
            continue
        root = int(root)
        if depth[root] >= 0:
            issues.append(
                OrientationIssue(
                    "raiz-duplicada",
                    f"Circuito {definition.circuit_id} parte da mesma barra "
                    f"inicial ({definition.root_bar_id}) que o circuito "
                    f"{catalog.definition(int(circuit_of_bar[root])).circuit_id}; "
                    "a orientação usa a primeira.",
                )
            )
            continue
        depth[root] = 0
        root_of[root] = root
        circuit_of_bar[root] = circuit_index
        roots.append(root)
        queue.append(root)

    offsets = topology.incidence_offsets
    incident = topology.incidence_segments
    neighbors = topology.incidence_neighbors
    inspected = 0
    contacts: list[tuple[int, int, int]] = []
    while queue:
        bar = queue.popleft()
        for position in range(int(offsets[bar]), int(offsets[bar + 1])):
            inspected += 1
            if (
                cancel_check is not None
                and inspected % _CANCEL_CHECK_INTERVAL == 0
                and cancel_check()
            ):
                raise InterruptedError("Orientação da rede cancelada.")
            segment = int(incident[position])
            if not conducts[segment]:
                continue
            neighbor = int(neighbors[position])
            if depth[neighbor] >= 0:
                # A guarda do trecho-pai impede que a volta pela incidência
                # reversa rebaixe uma aresta da árvore a corda; a do papel impede
                # que a mesma corda seja contada pelas duas pontas.
                if segment != int(parent_segment[bar]) and segment_role[segment] == 0:
                    segment_role[segment] = SEGMENT_CHORD
                    if root_of[neighbor] != root_of[bar]:
                        contacts.append(
                            (int(root_of[bar]), int(root_of[neighbor]), segment)
                        )
                continue
            parent_bar[neighbor] = bar
            parent_segment[neighbor] = segment
            depth[neighbor] = depth[bar] + 1
            root_of[neighbor] = root_of[bar]
            circuit_of_bar[neighbor] = circuit_of_bar[bar]
            segment_role[segment] = SEGMENT_TREE
            queue.append(neighbor)

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Orientação da rede cancelada.")

    # O que não conduz é aberto; o que conduz e continua em zero é ilha sem
    # fonte, porque se uma ponta tivesse sido alcançada o trecho teria sido
    # visitado a partir dela.
    segment_role[~conducts] = SEGMENT_OPEN

    # Filhos em CSR: bincount + cumsum + argsort estável, o idioma que
    # branch_analysis já usa para cargas por barra. Nenhum laço por barra — numa
    # rede real a maioria das barras não toca trecho algum.
    children = np.flatnonzero(parent_bar >= 0).astype(np.intp, copy=False)
    parents = parent_bar[children]
    order = np.argsort(parents, kind="stable")
    counts = np.bincount(parents, minlength=bar_count).astype(np.intp, copy=False)
    children_offsets = np.empty(bar_count + 1, dtype=np.intp)
    children_offsets[0] = 0
    np.cumsum(counts, out=children_offsets[1:])
    children_bars = children[order]
    children_segments = parent_segment[children_bars]

    tree_child_bar = np.full(segment_count, -1, dtype=np.intp)
    tree_child_bar[parent_segment[children]] = children

    chords = np.flatnonzero(segment_role == SEGMENT_CHORD).astype(np.intp, copy=False)
    has_incident = offsets[1:] > offsets[:-1]
    unreachable = int(np.count_nonzero(has_incident & (depth < 0)))

    issues.extend(_aggregate_issues(topology, catalog, segment_role, chords, contacts))

    for values in (
        parent_bar,
        parent_segment,
        depth,
        root_of,
        circuit_of_bar,
        children_offsets,
        children_bars,
        children_segments,
        segment_role,
        tree_child_bar,
        chords,
    ):
        values.setflags(write=False)

    return NetworkOrientation(
        topology=topology,
        catalog=catalog,
        parent_bar=parent_bar,
        parent_segment=parent_segment,
        depth=depth,
        root_of=root_of,
        circuit_of_bar=circuit_of_bar,
        children_offsets=children_offsets,
        children_bars=children_bars,
        children_segments=children_segments,
        segment_role=segment_role,
        tree_child_bar=tree_child_bar,
        chord_segment_indices=chords,
        root_bar_indices=_readonly_indices(roots),
        multi_source_contacts=tuple(contacts),
        unreachable_bar_count=unreachable,
        issues=tuple(issues),
    )


def _aggregate_issues(
    topology: NetworkTopology,
    catalog: CircuitCatalogModel,
    segment_role: np.ndarray,
    chords: IndexArray,
    contacts: list[tuple[int, int, int]],
) -> list[OrientationIssue]:
    """Uma ocorrência por fato, com contagem e amostra — nunca uma por trecho."""

    segments = topology.segments
    issues: list[OrientationIssue] = []

    switches = topology.switches
    if switches is not None:
        invalid = sum(
            1 for state in switches.states if state.strip() not in _KNOWN_STATES
        )
        if invalid:
            issues.append(
                OrientationIssue(
                    "estado-invalido",
                    f"{invalid:n} chave(s) com ESTADO diferente de 0 e 1 foram "
                    "tratadas como abertas.",
                )
            )

    if chords.size:
        sample = segments.segment_ids[int(chords[0])]
        issues.append(
            OrientationIssue(
                "corda",
                f"{chords.size:n} trecho(s) fecham laço na configuração atual "
                f"(ex.: {sample}). A orientação escolheu um caminho; a região a "
                "jusante de um ponto dentro do laço é alimentada pelos dois lados.",
                sample,
            )
        )

    unreached = np.flatnonzero(segment_role == SEGMENT_UNREACHED)
    if unreached.size:
        sample = segments.segment_ids[int(unreached[0])]
        issues.append(
            OrientationIssue(
                "ilha-sem-fonte",
                f"{unreached.size:n} trecho(s) conduzem mas não são alcançados "
                f"por nenhuma barra inicial (ex.: {sample}).",
                sample,
            )
        )

    if contacts:
        # Mesmo vocabulário de project_topology.coupled_study_reason: duas frases
        # diferentes para o mesmo fato é como a interface passa a mentir.
        bars = segments.bars
        circuit_by_root = {
            int(root): index
            for index, definition in enumerate(catalog.definitions)
            if (root := bars.index_for_id(definition.root_bar_id)) is not None
        }
        pairs: dict[tuple[int, int], int] = {}
        for first, second, segment in contacts:
            key = (min(first, second), max(first, second))
            pairs.setdefault(key, segment)
        for (first, second), segment in pairs.items():
            labels = ", ".join(
                _circuit_label(catalog, circuit_by_root.get(root))
                for root in (first, second)
            )
            sample = segments.segment_ids[segment]
            issues.append(
                OrientationIssue(
                    "fonte-multipla",
                    f"Alimentadores unidos por conexões condutoras: {labels} "
                    f"(trecho {sample}). Cada barra foi atribuída à fonte mais "
                    "próxima em saltos, que não é o ponto real de divisão do "
                    "fluxo.",
                    sample,
                )
            )
    return issues


def _circuit_label(catalog: CircuitCatalogModel, index: int | None) -> str:
    if index is None:
        return "?"
    definition = catalog.definition(index)
    return definition.code or definition.circuit_id
