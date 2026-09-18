"""Seleção de tudo o que está a jusante de um ponto da rede.

O ponto é uma **barra** ou um **trecho** — e trecho inclui chave, que no modelo
é um trecho. A região a jusante é a subárvore da
:class:`~circuit_viewer.network_orientation.NetworkOrientation` que parte dele:
de uma barra, ela mesma e tudo abaixo; de um trecho, o próprio trecho e tudo
abaixo da ponta de jusante. É o que responde "se eu abrir esta chave, o que fica
sem?".

**O resultado é sempre completo.** Trechos, barras, chaves, cargas, capacitores,
geradores e reguladores saem juntos, e quem filtra por tipo é a apresentação. Um
resultado que omitisse capacitores porque um filtro estava desligado propagaria
a omissão para todo consumidor — e isto é base para outras análises. A única
opção que muda a travessia é ``beyond_open_switches``, porque ela muda quais
barras existem na região: é topologia, não apresentação.

**Onde a resposta é "nada", a seleção diz "nada".** Um trecho que fecha laço
(corda) não tem nada a jusante: abri-lo não desconecta ninguém, porque as duas
pontas continuam alimentadas pela árvore. Descer pela ponta mais profunda
devolveria uma região que continua energizada depois da abertura — exatamente o
que a ferramenta não pode afirmar. Pelo mesmo motivo, uma corda que liga a
região ao resto da rede vira ocorrência: ali a região é alimentada também por
fora, e "a jusante" é uma aproximação.

**Modo além de chaves abertas.** A região atravessa uma chave aberta só se a
ponta de fora estiver em barra **não alcançada** pela orientação (``depth < 0``).
Ponta de fora com ``depth >= 0`` já é alimentada por alguma raiz — a da própria
região ou a do vizinho — e a região para ali. Um teste só recusa os dois
vazamentos possíveis: engolir o alimentador vizinho pela chave NA, e engolir a
própria montante quando o tronco dá a volta. Basta porque toda barra ligada a uma
barra morta por trecho **condutor** também é morta — senão o BFS da orientação a
teria alcançado —, então a ilha morta só sai por chave aberta, e o portão é
sempre o mesmo.

Camada de núcleo, sem Qt. Não chama ``NetworkTopology.trace``, que muta as marcas
compartilhadas da topologia: uma seleção na thread da interface corromperia as
de uma análise de ramais rodando em outra thread.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .model import (
    CapacitorModel,
    GeneratorModel,
    IndexArray,
    LoadModel,
    RegulatorModel,
)
from .network_orientation import (
    SEGMENT_CHORD,
    SEGMENT_OPEN,
    SEGMENT_TREE,
    NetworkOrientation,
    OrientationIssue,
    conducting_segments,
)


CancelCheck = Callable[[], bool]
OriginKind = Literal["bar", "segment"]

_CANCEL_CHECK_INTERVAL = 4_096


def _readonly_indices(values) -> IndexArray:  # noqa: ANN001
    result = np.ascontiguousarray(values, dtype=np.intp)
    if result.ndim != 1:
        raise ValueError("Os índices devem formar um vetor unidimensional.")
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class DownstreamOrigin:
    """O ponto de onde a região desce: uma barra ou um trecho."""

    kind: OriginKind
    index: int

    def __post_init__(self) -> None:
        if self.kind not in ("bar", "segment"):
            raise ValueError(f"Origem desconhecida: {self.kind}")
        if self.index < 0:
            raise ValueError("O índice da origem não pode ser negativo.")


@dataclass(frozen=True, slots=True)
class DownstreamSelection:
    """A região a jusante de um ponto, completa, com o que explica a sua borda.

    ``bar_indices`` sai em ordem de nível — a barra de partida primeiro, depois
    seus filhos, e assim por diante —, com o nível de cada uma em
    ``bar_levels``, na mesma posição. O nível de um trecho é o da barra que ele
    alimenta; o trecho de origem fica no nível 0, junto com a barra de partida.

    ``switch_indices`` e ``regulator_indices`` são índices de **registro** nos
    respectivos modelos, não de trecho.
    """

    origin: DownstreamOrigin
    orientation: NetworkOrientation
    beyond_open_switches: bool
    start_bar_index: int
    bar_indices: IndexArray
    bar_levels: IndexArray
    segment_indices: IndexArray
    segment_levels: IndexArray
    switch_indices: IndexArray
    load_indices: IndexArray
    capacitor_indices: IndexArray
    generator_indices: IndexArray
    regulator_indices: IndexArray
    #: Chaves abertas com uma ponta dentro e outra fora: o que isola a região.
    boundary_switch_indices: IndexArray
    #: Chaves abertas com as duas pontas dentro. No modo "além", inclui as
    #: atravessadas.
    inner_open_switch_indices: IndexArray
    #: Cordas com as duas pontas dentro: fazem parte da região.
    internal_chord_segment_indices: IndexArray
    #: Cordas com uma ponta só dentro: a região é alimentada também por fora.
    boundary_chord_segment_indices: IndexArray
    issues: tuple[OrientationIssue, ...]
    loads: LoadModel | None = None
    capacitors: CapacitorModel | None = None
    generators: GeneratorModel | None = None
    regulators: RegulatorModel | None = None

    def __post_init__(self) -> None:
        for name in (
            "bar_indices",
            "bar_levels",
            "segment_indices",
            "segment_levels",
            "switch_indices",
            "load_indices",
            "capacitor_indices",
            "generator_indices",
            "regulator_indices",
            "boundary_switch_indices",
            "inner_open_switch_indices",
            "internal_chord_segment_indices",
            "boundary_chord_segment_indices",
        ):
            values = getattr(self, name)
            if values.dtype != np.dtype(np.intp) or values.ndim != 1:
                raise ValueError(f"{name} deve ser um vetor de índices.")
            if values.flags.writeable:
                raise ValueError(f"{name} deve ser imutável.")
        if self.bar_levels.size != self.bar_indices.size:
            raise ValueError("Deve haver um nível para cada barra.")
        if self.segment_levels.size != self.segment_indices.size:
            raise ValueError("Deve haver um nível para cada trecho.")

    @property
    def is_empty(self) -> bool:
        return self.bar_indices.size == 0 and self.segment_indices.size == 0

    @property
    def segments(self):  # noqa: ANN201 — LineNetworkModel
        return self.orientation.segments

    @property
    def switches(self):  # noqa: ANN201 — SwitchModel | None
        return self.orientation.switches

    def level_by_bar(self) -> dict[int, int]:
        return dict(
            zip(self.bar_indices.tolist(), self.bar_levels.tolist(), strict=True)
        )


def select_downstream(
    orientation: NetworkOrientation,
    origin: DownstreamOrigin,
    *,
    beyond_open_switches: bool = False,
    loads: LoadModel | None = None,
    capacitors: CapacitorModel | None = None,
    generators: GeneratorModel | None = None,
    regulators: RegulatorModel | None = None,
    cancel_check: CancelCheck | None = None,
) -> DownstreamSelection:
    """A região a jusante de ``origin``, com os elementos de cada tipo."""

    segments = orientation.segments
    bars = segments.bars
    for model, parent, label in (
        (loads, bars, "cargas"),
        (capacitors, bars, "capacitores"),
        (generators, bars, "geradores"),
    ):
        if model is not None and model.bars is not parent:
            raise ValueError(f"As {label} devem pertencer às barras da orientação.")
    if regulators is not None and regulators.segments is not segments:
        raise ValueError("Os reguladores devem pertencer aos trechos da orientação.")

    bar_count = len(bars)
    segment_count = len(segments)
    if origin.kind == "bar" and origin.index >= bar_count:
        raise IndexError(origin.index)
    if origin.kind == "segment" and origin.index >= segment_count:
        raise IndexError(origin.index)

    depth = orientation.depth
    issues: list[OrientationIssue] = []
    origin_segment = origin.index if origin.kind == "segment" else -1
    start, dead_start = _resolve_start(
        orientation, origin, beyond_open_switches, issues
    )

    # Nível -1 é "fora da região"; o vetor serve também de conjunto de visitados.
    level = np.full(bar_count, -1, dtype=np.intp)
    order: list[int] = []
    inspected = 0

    def check() -> None:
        nonlocal inspected
        inspected += 1
        if (
            cancel_check is not None
            and inspected % _CANCEL_CHECK_INTERVAL == 0
            and cancel_check()
        ):
            raise InterruptedError("Seleção a jusante cancelada.")

    if start >= 0:
        level[start] = 0
        order.append(start)
        if not dead_start:
            # Descida pela floresta de filhos: cada barra é filha de uma só, e
            # por isso não há visitados a conferir.
            queue: deque[int] = deque((start,))
            offsets = orientation.children_offsets
            children = orientation.children_bars
            while queue:
                bar = queue.popleft()
                for position in range(int(offsets[bar]), int(offsets[bar + 1])):
                    check()
                    child = int(children[position])
                    level[child] = level[bar] + 1
                    order.append(child)
                    queue.append(child)

        if beyond_open_switches or dead_start:
            _absorb_dead_islands(orientation, level, order, check)

    in_region = level >= 0
    starts = segments.start_indices
    ends = segments.end_indices
    start_inside = in_region[starts]
    end_inside = in_region[ends]
    both_inside = start_inside & end_inside
    one_inside = start_inside ^ end_inside

    in_segments = both_inside.copy()
    if origin_segment >= 0:
        # O trecho de origem tem uma ponta fora — a de montante — e entra mesmo
        # assim: é o elemento que o usuário escolheu. Entra também quando nada
        # está a jusante dele (corda, chave aberta), para a tela mostrar o que
        # foi clicado junto com a ocorrência que explica o vazio.
        in_segments[origin_segment] = True
        one_inside[origin_segment] = False

    segment_array = np.flatnonzero(in_segments).astype(np.intp, copy=False)
    # O nível de um trecho é o da barra que ele alimenta — a ponta mais funda.
    segment_levels = np.maximum(level[starts[segment_array]], level[ends[segment_array]])
    segment_levels = np.maximum(segment_levels, 0)
    by_level = np.argsort(segment_levels, kind="stable")
    segment_array = segment_array[by_level]
    segment_levels = segment_levels[by_level]

    conducts = conducting_segments(orientation.topology)
    role = orientation.segment_role
    boundary_open = np.flatnonzero(one_inside & ~conducts)
    inner_open = np.flatnonzero(both_inside & ~conducts)
    internal_chords = np.flatnonzero(both_inside & (role == SEGMENT_CHORD))
    boundary_chords = np.flatnonzero(one_inside & (role == SEGMENT_CHORD))

    if boundary_chords.size:
        sample = segments.segment_ids[int(boundary_chords[0])]
        issues.append(
            OrientationIssue(
                "corda-de-fronteira",
                f"A região também é alimentada por fora, por {boundary_chords.size:n} "
                f"trecho(s) que fecham laço com o resto da rede (ex.: {sample}). "
                "Abrir a origem não a desenergiza; \"a jusante\" é uma aproximação "
                "aqui.",
                sample,
            )
        )

    switches = orientation.switches
    switch_indices = _records_on(switches, in_segments)
    boundary_switch_indices = _records_for_segments(switches, boundary_open)
    inner_open_switch_indices = _records_for_segments(switches, inner_open)
    regulator_indices = _records_on(regulators, in_segments)

    bar_array = np.asarray(order, dtype=np.intp)
    bar_levels = level[bar_array] if bar_array.size else np.empty(0, dtype=np.intp)
    # A descida já sai por nível; as ilhas absorvidas chegam depois de toda a
    # parte energizada, e a ordenação estável as põe no lugar.
    by_level = np.argsort(bar_levels, kind="stable")
    bar_array = bar_array[by_level]
    bar_levels = bar_levels[by_level]

    return DownstreamSelection(
        origin=origin,
        orientation=orientation,
        beyond_open_switches=bool(beyond_open_switches),
        start_bar_index=int(start),
        bar_indices=_readonly_indices(bar_array),
        bar_levels=_readonly_indices(bar_levels),
        segment_indices=_readonly_indices(segment_array),
        segment_levels=_readonly_indices(segment_levels),
        switch_indices=switch_indices,
        load_indices=_members_on(loads, in_region),
        capacitor_indices=_members_on(capacitors, in_region),
        generator_indices=_members_on(generators, in_region),
        regulator_indices=regulator_indices,
        boundary_switch_indices=boundary_switch_indices,
        inner_open_switch_indices=inner_open_switch_indices,
        internal_chord_segment_indices=_readonly_indices(internal_chords),
        boundary_chord_segment_indices=_readonly_indices(boundary_chords),
        issues=tuple(issues),
        loads=loads,
        capacitors=capacitors,
        generators=generators,
        regulators=regulators,
    )


def _resolve_start(
    orientation: NetworkOrientation,
    origin: DownstreamOrigin,
    beyond: bool,
    issues: list[OrientationIssue],
) -> tuple[int, bool]:
    """A barra de onde se desce e se ela está numa ilha morta.

    ``-1`` quando nada está a jusante; o motivo vai para ``issues``.
    """

    depth = orientation.depth
    segments = orientation.segments

    if origin.kind == "bar":
        bar = origin.index
        if depth[bar] >= 0:
            return bar, False
        if beyond:
            # A barra está além de uma chave aberta: a região é a ilha dela.
            return bar, True
        issues.append(
            OrientationIssue(
                "barra-nao-alcancada",
                f"A barra {segments.bars.bar_ids[bar]} não é alimentada na "
                "configuração atual. Marque \"incluir além de chaves abertas\" "
                "para ver a ilha a que ela pertence.",
            )
        )
        return -1, False

    segment = origin.index
    segment_id = segments.segment_ids[segment]
    role = int(orientation.segment_role[segment])
    if role == SEGMENT_TREE:
        return int(orientation.tree_child_bar[segment]), False

    if role == SEGMENT_CHORD:
        issues.append(
            OrientationIssue(
                "corda",
                f"O trecho {segment_id} fecha um laço: as duas pontas continuam "
                "alimentadas se ele abrir, então nada fica a jusante dele.",
                segment_id,
            )
        )
        return -1, False

    if role == SEGMENT_OPEN:
        first = int(segments.start_indices[segment])
        second = int(segments.end_indices[segment])
        first_dead = depth[first] < 0
        second_dead = depth[second] < 0
        if not beyond:
            issues.append(
                OrientationIssue(
                    "chave-aberta",
                    f"O trecho {segment_id} está aberto: nada é alimentado através "
                    "dele. Marque \"incluir além de chaves abertas\" para ver o "
                    "que ele alimentaria.",
                    segment_id,
                )
            )
            return -1, False
        if first_dead != second_dead:
            return (first if first_dead else second), True
        if not first_dead:
            issues.append(
                OrientationIssue(
                    "chave-aberta-entre-alimentadas",
                    f"As duas pontas do trecho {segment_id} já são alimentadas; "
                    "fechá-lo fecharia um laço, e nada ficaria a jusante dele.",
                    segment_id,
                )
            )
        else:
            issues.append(
                OrientationIssue(
                    "chave-aberta-sem-fonte",
                    f"Nenhuma ponta do trecho {segment_id} é alimentada; não há "
                    "de onde medir o que está a jusante.",
                    segment_id,
                )
            )
        return -1, False

    issues.append(
        OrientationIssue(
            "trecho-sem-fonte",
            f"O trecho {segment_id} não é alcançado por nenhuma barra inicial.",
            segment_id,
        )
    )
    return -1, False


def _absorb_dead_islands(
    orientation: NetworkOrientation,
    level: np.ndarray,
    order: list[int],
    check: Callable[[], None],
) -> None:
    """Estende a região pelas ilhas desenergizadas vizinhas, transitivamente.

    O único portão é ``depth < 0`` na ponta de fora, tanto para atravessar uma
    chave aberta a partir de uma barra energizada quanto para caminhar dentro da
    ilha morta — e é o que impede o vazamento para qualquer barra alimentada.
    O vetor de visitados é o ``level`` local, nunca as marcas da topologia.
    """

    depth = orientation.depth
    topology = orientation.topology
    offsets = topology.incidence_offsets
    neighbors = topology.incidence_neighbors
    queue: deque[int] = deque(order)
    while queue:
        bar = queue.popleft()
        for position in range(int(offsets[bar]), int(offsets[bar + 1])):
            check()
            neighbor = int(neighbors[position])
            if depth[neighbor] >= 0 or level[neighbor] >= 0:
                continue
            level[neighbor] = level[bar] + 1
            order.append(neighbor)
            queue.append(neighbor)


def _members_on(model, in_region: np.ndarray) -> IndexArray:  # noqa: ANN001
    """Registros de um modelo pendurado em barra que caem na região."""

    if model is None or not len(model):
        return _readonly_indices(())
    return _readonly_indices(np.flatnonzero(in_region[model.bar_indices]))


def _records_on(model, in_segments: np.ndarray) -> IndexArray:  # noqa: ANN001
    """Registros de chave ou regulador cujo trecho está na região.

    ``flatnonzero`` sobre ``segment_indices`` já devolve a posição no modelo, que
    é o índice de registro — sem laço.
    """

    if model is None or not len(model):
        return _readonly_indices(())
    return _readonly_indices(np.flatnonzero(in_segments[model.segment_indices]))


def _records_for_segments(model, segment_values: np.ndarray) -> IndexArray:  # noqa: ANN001
    if model is None or not len(segment_values):
        return _readonly_indices(())
    records = model.record_indices_by_segment[segment_values]
    return _readonly_indices(records[records >= 0])
