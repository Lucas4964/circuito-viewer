"""Identificação dos blocos elétricos: as regiões que uma manobra isola.

Um bloco é a região da rede delimitada por **chaves manobráveis**, e o estado
delas não entra na conta: aberta ou fechada, a chave é fronteira. É o que
responde "o que eu isolo operando esta chave?", que é a pergunta de quem planeja
manobra — e não "o que está energizado agora", que é outra coisa.

**O fusível não delimita.** Ele não se opera para transferir carga, então fica
interno ao bloco mesmo aberto: a região além dele continua fazendo parte da
mesma ilha manobrável, e é isso que se quer ver ao estudar a transferência. No
alimentador medido isso importa muito — das 723 chaves, só 154 são manobráveis;
as outras 569 são fusíveis, e delimitar por todas produziria uma partição quatro
vezes mais fina que a útil.

Quem responde se um tipo de chave é manobrável é ``tipos_chave.json``, pela
``SwitchRecord.switchable``. Sem essa resposta — banco sem ``TIPOCHAVE``, ou
arquivo de configuração com problema — não há fronteira alguma e a rede inteira
vira um bloco só; a análise diz isso numa ocorrência em vez de fingir um
resultado.

Camada de núcleo, no molde de ``branch_analysis``: sem Qt, testável headless. A
adjacência vem do CSR de :class:`~circuit_viewer.model.NetworkTopology`, que já
existe e é o mesmo que a descoberta de circuitos percorre — não há uma segunda
travessia da rede aqui.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .model import (
    CircuitCatalogModel,
    IndexArray,
    LoadModel,
    NetworkTopology,
    SwitchModel,
)


MAX_BLOCK_ISSUES = 500
CancelCheck = Callable[[], bool]

# Marca de "manobrável" em ``SwitchRecord.switchable``. Texto porque é assim que
# o modelo guarda todo campo de chave, e vazio significa "não declarado" — que
# não é o mesmo que "não manobrável" e por isso não vira fronteira.
SWITCHABLE = "1"

# Mesma cadência de verificação das demais análises do projeto.
_CANCEL_CHECK_INTERVAL = 4_096


def _readonly_indices(values) -> IndexArray:  # noqa: ANN001
    result = np.ascontiguousarray(values, dtype=np.intp)
    if result.ndim != 1:
        raise ValueError("Os índices devem formar um vetor unidimensional.")
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class BlockIssue:
    """Ocorrência que limita ou qualifica o resultado da análise."""

    kind: str
    message: str


@dataclass(frozen=True, slots=True)
class BlockRecord:
    """Uma região delimitada por chaves manobráveis.

    ``boundary_switch_indices`` são os índices no modelo de chaves, não nos
    trechos: são eles que a interface converte em códigos, e são literalmente as
    operações que isolam este bloco.

    ``total_power`` soma o ``SNOM`` das cargas, em kVA, e ``total_length`` o
    ``COMPR`` dos trechos, **em metros** — a mesma unidade que o cadastro traz e
    que a tabela de ramais já usa na coluna de mesmo nome. Ambos são ``None``
    quando **nenhuma** parcela é numérica, e não zero: um bloco sem carga tem 0
    kVA, um bloco cujo cadastro não informa kVA não tem resposta, e a tabela
    precisa distinguir os dois.
    """

    block_id: int
    bar_indices: IndexArray
    segment_indices: IndexArray
    load_indices: IndexArray
    boundary_switch_indices: IndexArray
    boundary_switch_codes: tuple[str, ...]
    total_power: float | None
    total_length: float | None
    contains_source: bool

    def __post_init__(self) -> None:
        for values in (
            self.bar_indices,
            self.segment_indices,
            self.load_indices,
            self.boundary_switch_indices,
        ):
            if values.dtype != np.dtype(np.intp) or values.ndim != 1:
                raise ValueError("Os índices do bloco devem ser vetores inteiros.")
            if values.flags.writeable:
                raise ValueError("Os índices do bloco devem ser imutáveis.")
        if self.block_id <= 0:
            raise ValueError("BLOCO_ID deve ser um inteiro positivo.")
        if len(self.boundary_switch_codes) != len(self.boundary_switch_indices):
            raise ValueError(
                "Cada chave de fronteira deve ter exatamente um código."
            )
        if self.bar_indices.size == 0:
            raise ValueError("Um bloco deve conter ao menos uma barra.")

    @property
    def bar_count(self) -> int:
        return int(self.bar_indices.size)

    @property
    def segment_count(self) -> int:
        return int(self.segment_indices.size)

    @property
    def load_count(self) -> int:
        return int(self.load_indices.size)

    @property
    def boundary_count(self) -> int:
        return int(self.boundary_switch_indices.size)

    @property
    def is_dead_end(self) -> bool:
        """Bloco com uma fronteira só: não há para onde transferir a carga.

        Isolá-lo é possível — basta a única chave —, mas não existe alternativa
        de socorro. É o bloco que interessa a um estudo de confiabilidade.
        """

        return self.boundary_count == 1


@dataclass(frozen=True, slots=True)
class BlockAnalysisResult:
    records: tuple[BlockRecord, ...]
    issues: tuple[BlockIssue, ...] = ()
    omitted_issue_count: int = 0
    switchable_switch_count: int = 0
    source_segments: object | None = None
    source_switches: SwitchModel | None = None
    source_loads: LoadModel | None = None

    def __post_init__(self) -> None:
        if self.omitted_issue_count < 0 or self.switchable_switch_count < 0:
            raise ValueError("As contagens da análise não podem ser negativas.")
        if tuple(record.block_id for record in self.records) != tuple(
            range(1, len(self.records) + 1)
        ):
            raise ValueError("BLOCO_ID deve formar uma sequência de 1 até N.")

    @property
    def is_empty(self) -> bool:
        return not self.records


class _IssueLog:
    """Acumula ocorrências respeitando o teto de detalhamento."""

    __slots__ = ("issues", "total")

    def __init__(self) -> None:
        self.issues: list[BlockIssue] = []
        self.total = 0

    def add(self, kind: str, message: str) -> None:
        self.total += 1
        if len(self.issues) < MAX_BLOCK_ISSUES:
            self.issues.append(BlockIssue(kind, message))

    @property
    def omitted(self) -> int:
        return max(0, self.total - len(self.issues))


def boundary_segment_mask(
    segments,  # noqa: ANN001 — LineNetworkModel
    switches: SwitchModel | None,
) -> np.ndarray:
    """Máscara dos trechos que delimitam bloco: os de chave manobrável.

    Pública porque é a definição de fronteira, e tanto a análise quanto os testes
    precisam da mesma — duplicá-la seria criar uma segunda verdade.
    """

    mask = np.zeros(len(segments), dtype=bool)
    if switches is None:
        return mask
    for switch_index in range(len(switches)):
        if switches.record(switch_index).switchable != SWITCHABLE:
            continue
        mask[int(switches.segment_indices[switch_index])] = True
    return mask


def _components(
    topology: NetworkTopology,
    bar_count: int,
    boundary: np.ndarray,
    cancel_check: CancelCheck | None,
) -> tuple[np.ndarray, int]:
    """Componentes conexas das barras, ignorando os trechos de fronteira.

    Busca em profundidade iterativa sobre o CSR já montado pela topologia. A
    pilha explícita evita o limite de recursão: o maior bloco medido tem 1.638
    trechos, e uma rede em linha os empilharia todos.
    """

    offsets = topology.incidence_offsets
    incidence_segments = topology.incidence_segments
    incidence_neighbors = topology.incidence_neighbors
    component = np.full(bar_count, -1, dtype=np.intp)
    visited = 0
    total = 0
    for root in range(bar_count):
        if component[root] >= 0:
            continue
        component[root] = total
        stack = [root]
        while stack:
            bar = stack.pop()
            if (
                cancel_check is not None
                and visited % _CANCEL_CHECK_INTERVAL == 0
                and cancel_check()
            ):
                raise InterruptedError("Análise de blocos cancelada.")
            visited += 1
            for position in range(int(offsets[bar]), int(offsets[bar + 1])):
                if boundary[int(incidence_segments[position])]:
                    continue
                neighbor = int(incidence_neighbors[position])
                if component[neighbor] < 0:
                    component[neighbor] = total
                    stack.append(neighbor)
        total += 1
    return component, total


def _total(values: tuple[float, ...]) -> float | None:
    """Soma que distingue "zero" de "sem resposta"."""

    return float(sum(values)) if values else None


def analyze_blocks(
    catalog: CircuitCatalogModel,
    switches: SwitchModel | None = None,
    loads: LoadModel | None = None,
    *,
    cancel_check: CancelCheck | None = None,
) -> BlockAnalysisResult:
    """Identifica os blocos da rede do catálogo informado.

    ``switches`` ausente, ou sem nenhuma chave manobrável, produz um bloco só
    com a rede inteira — matematicamente correto e praticamente inútil, então a
    situação vira ocorrência para a interface poder explicá-la.
    """

    segments = catalog.segments
    if loads is not None and loads.bars is not segments.bars:
        raise ValueError("As cargas devem pertencer à rede do catálogo.")
    if switches is not None and switches.segments is not segments:
        raise ValueError("As chaves devem pertencer à rede do catálogo.")

    log = _IssueLog()
    boundary = boundary_segment_mask(segments, switches)
    switchable_count = int(boundary.sum())
    if switchable_count == 0:
        log.add(
            "sem-fronteira",
            "Nenhuma chave manobrável no modelo: a rede inteira forma um bloco "
            "único. Confira se as chaves foram importadas e se tipos_chave.json "
            "declara os tipos do cadastro.",
        )

    topology = NetworkTopology(segments, switches)
    bar_count = len(segments.bars)
    component, total = _components(topology, bar_count, boundary, cancel_check)

    bars_by_block: list[list[int]] = [[] for _ in range(total)]
    for bar_index in range(bar_count):
        bars_by_block[int(component[bar_index])].append(bar_index)

    # O trecho pertence ao bloco das suas barras; o de fronteira, a nenhum —
    # ele é a fronteira, não conteúdo. As duas barras de um trecho comum estão
    # sempre no mesmo bloco, justamente porque não há fronteira entre elas.
    segments_by_block: list[list[int]] = [[] for _ in range(total)]
    lengths_by_block: list[list[float]] = [[] for _ in range(total)]
    lengths = segments.lengths
    for segment_index in range(len(segments)):
        if boundary[segment_index]:
            continue
        block = int(component[int(segments.start_indices[segment_index])])
        segments_by_block[block].append(segment_index)
        length = lengths[segment_index]
        if length is not None and np.isfinite(length):
            lengths_by_block[block].append(float(length))

    loads_by_block: list[list[int]] = [[] for _ in range(total)]
    power_by_block: list[list[float]] = [[] for _ in range(total)]
    if loads is not None:
        for load_index in range(len(loads)):
            block = int(component[int(loads.bar_indices[load_index])])
            loads_by_block[block].append(load_index)
            power = _parse_power(loads.snom_values[load_index])
            if power is not None:
                power_by_block[block].append(power)

    # A chave de fronteira pertence aos **dois** blocos que ela separa: das duas
    # pontas dela se opera a mesma manobra, e cada bloco precisa vê-la na sua
    # lista. Um conjunto por bloco porque uma chave em laço tocaria o mesmo
    # bloco duas vezes.
    boundaries_by_block: list[set[int]] = [set() for _ in range(total)]
    if switches is not None:
        for switch_index in range(len(switches)):
            segment_index = int(switches.segment_indices[switch_index])
            if not boundary[segment_index]:
                continue
            for bar_index in (
                int(segments.start_indices[segment_index]),
                int(segments.end_indices[segment_index]),
            ):
                boundaries_by_block[int(component[bar_index])].add(switch_index)

    source_blocks = set()
    bars = segments.bars
    for circuit_index in range(len(catalog)):
        root_bar_id = catalog.definition(circuit_index).root_bar_id
        root_index = bars.index_for_id(root_bar_id)
        if root_index is not None:
            source_blocks.add(int(component[int(root_index)]))

    records: list[BlockRecord] = []
    for block in range(total):
        boundary_indices = sorted(boundaries_by_block[block])
        records.append(
            BlockRecord(
                block_id=len(records) + 1,
                bar_indices=_readonly_indices(bars_by_block[block]),
                segment_indices=_readonly_indices(segments_by_block[block]),
                load_indices=_readonly_indices(loads_by_block[block]),
                boundary_switch_indices=_readonly_indices(boundary_indices),
                boundary_switch_codes=tuple(
                    switches.record(index).code or switches.record(index).switch_id
                    for index in boundary_indices
                )
                if switches is not None
                else (),
                total_power=_total(tuple(power_by_block[block])),
                total_length=_total(tuple(lengths_by_block[block])),
                contains_source=block in source_blocks,
            )
        )

    return BlockAnalysisResult(
        records=tuple(records),
        issues=tuple(log.issues),
        omitted_issue_count=log.omitted,
        switchable_switch_count=switchable_count,
        source_segments=segments,
        source_switches=switches,
        source_loads=loads,
    )


def _parse_power(value: str) -> float | None:
    """Converte o ``SNOM`` textual da carga, tolerando vírgula decimal."""

    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if np.isfinite(number) else None


__all__ = [
    "BlockAnalysisResult",
    "BlockIssue",
    "BlockRecord",
    "MAX_BLOCK_ISSUES",
    "SWITCHABLE",
    "analyze_blocks",
    "boundary_segment_mask",
]
