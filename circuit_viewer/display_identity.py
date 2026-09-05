"""Identidades textuais compartilhadas pelas ferramentas de circuitos e blocos."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .block_analysis import BlockAnalysisResult
from .block_graph import resolve_block_circuit_indices
from .model import CircuitCatalogModel


UNRESOLVED_CIRCUIT_LABEL = "Sem circuito definido"
UNRESOLVED_BLOCK_PREFIX = "SEM-CIRCUITO"


@dataclass(frozen=True, slots=True)
class BlockDisplayIdentity:
    """Identidade visível de um bloco, separada de seu ``block_id`` técnico."""

    circuit_index: int | None
    circuit_label: str
    local_number: int
    graph_label: str

    def __post_init__(self) -> None:
        if self.circuit_index is not None and self.circuit_index < 0:
            raise ValueError("O índice do circuito não pode ser negativo.")
        if self.local_number <= 0:
            raise ValueError("O número local do bloco deve ser positivo.")
        if not self.circuit_label or not self.graph_label:
            raise ValueError("A identidade visível do bloco não pode ser vazia.")


def circuit_display_labels(catalog: CircuitCatalogModel) -> tuple[str, ...]:
    """Rótulos por ``CODIGO``, com ``CIRC_ID`` apenas como desambiguação.

    ``CODIGO`` continua texto do começo ao fim, portanto zeros à esquerda não
    são descartados. Códigos vazios e repetidos são aceitos pelo importador;
    nesses casos o identificador técnico aparece só para manter a UI inequívoca.
    """

    codes = tuple(str(definition.code).strip() for definition in catalog.definitions)
    counts = Counter(code for code in codes if code)
    labels: list[str] = []
    for definition, code in zip(catalog.definitions, codes, strict=True):
        if not code:
            labels.append(f"Sem código (CIRC_ID: {definition.circuit_id})")
        elif counts[code] > 1:
            labels.append(f"{code} (CIRC_ID: {definition.circuit_id})")
        else:
            labels.append(code)
    return tuple(labels)


def fallback_block_display_identities(
    result: BlockAnalysisResult,
) -> dict[int, BlockDisplayIdentity]:
    """Identidades neutras para consumidores que ainda não receberam catálogo."""

    return {
        record.block_id: BlockDisplayIdentity(
            circuit_index=None,
            circuit_label=UNRESOLVED_CIRCUIT_LABEL,
            local_number=position,
            graph_label=f"{UNRESOLVED_BLOCK_PREFIX}-{position:n}",
        )
        for position, record in enumerate(
            sorted(result.records, key=lambda item: item.block_id),
            start=1,
        )
    }


def build_block_display_identities(
    result: BlockAnalysisResult,
    catalog: CircuitCatalogModel,
) -> dict[int, BlockDisplayIdentity]:
    """Numera blocos por circuito sem alterar o identificador interno global."""

    circuit_indices = resolve_block_circuit_indices(result, catalog)
    circuit_labels = circuit_display_labels(catalog)
    counters: defaultdict[int | None, int] = defaultdict(int)
    identities: dict[int, BlockDisplayIdentity] = {}
    for record in sorted(result.records, key=lambda item: item.block_id):
        circuit_index = circuit_indices.get(record.block_id)
        counters[circuit_index] += 1
        local_number = counters[circuit_index]
        if circuit_index is None:
            circuit_label = UNRESOLVED_CIRCUIT_LABEL
            prefix = UNRESOLVED_BLOCK_PREFIX
        else:
            circuit_label = circuit_labels[circuit_index]
            definition = catalog.definition(circuit_index)
            code = str(definition.code).strip()
            if circuit_label == code:
                prefix = code
            elif code:
                prefix = f"{code}[{definition.circuit_id}]"
            else:
                prefix = definition.circuit_id
        identities[record.block_id] = BlockDisplayIdentity(
            circuit_index=circuit_index,
            circuit_label=circuit_label,
            local_number=local_number,
            graph_label=f"{prefix}-{local_number:n}",
        )
    return identities


__all__ = [
    "BlockDisplayIdentity",
    "UNRESOLVED_BLOCK_PREFIX",
    "UNRESOLVED_CIRCUIT_LABEL",
    "build_block_display_identities",
    "circuit_display_labels",
    "fallback_block_display_identities",
]
