"""Colunas compartilhadas da tabela de blocos.

Sem Qt, no molde de ``branch_table_export``: as colunas e a conversão de um
:class:`~circuit_viewer.block_analysis.BlockRecord` em linha são definidas aqui
para poderem ser testadas sem interface, e para a janela não ser a dona da
verdade sobre o que a tabela mostra.
"""

from __future__ import annotations

from .block_analysis import BlockRecord
from .display_identity import BlockDisplayIdentity


BLOCK_TABLE_HEADERS = (
    "CIRCUITO",
    "BLOCO",
    "NUM_BARRAS",
    "NUM_TRECHOS",
    "NUM_CARGAS",
    "SNOM",
    "COMPR",
    "NUM_CHAVES",
    "CHAVES",
    "FONTE",
)
BLOCK_NUMERIC_COLUMNS = frozenset(
    BLOCK_TABLE_HEADERS.index(name)
    for name in (
        "BLOCO",
        "NUM_BARRAS",
        "NUM_TRECHOS",
        "NUM_CARGAS",
        "SNOM",
        "COMPR",
        "NUM_CHAVES",
        "FONTE",
    )
)
BLOCK_CIRCUIT_COLUMN = BLOCK_TABLE_HEADERS.index("CIRCUITO")
BLOCK_NUMBER_COLUMN = BLOCK_TABLE_HEADERS.index("BLOCO")
BLOCK_POWER_COLUMN = BLOCK_TABLE_HEADERS.index("SNOM")
BLOCK_LENGTH_COLUMN = BLOCK_TABLE_HEADERS.index("COMPR")
BLOCK_SWITCHES_COLUMN = BLOCK_TABLE_HEADERS.index("CHAVES")
BLOCK_SOURCE_COLUMN = BLOCK_TABLE_HEADERS.index("FONTE")

# Separador da lista de chaves, tanto no que se copia quanto no tooltip.
SWITCH_SEPARATOR = ", "
# Sufixo que avisa "tem mais" sem dizer quantos: a coluna NUM_CHAVES, ao lado,
# já responde o quantos.
TRUNCATION_SUFFIX = "…"


def block_table_values(
    record: BlockRecord,
    identity: BlockDisplayIdentity,
) -> tuple[object | None, ...]:
    """Os valores brutos de uma linha, na ordem de :data:`BLOCK_TABLE_HEADERS`.

    Brutos de propósito: número continua número, para a ordenação da tabela
    comparar grandeza e não texto. Quem formata é a janela.
    """

    return (
        identity.circuit_label,
        identity.local_number,
        record.bar_count,
        record.segment_count,
        record.load_count,
        record.total_power,
        record.total_length,
        record.boundary_count,
        switch_list_text(record),
        1 if record.contains_source else 0,
    )


def switch_list_text(record: BlockRecord) -> str:
    """A lista completa de chaves de fronteira, para tooltip e cópia."""

    return SWITCH_SEPARATOR.join(record.boundary_switch_codes)


def switch_list_summary(record: BlockRecord) -> str:
    """A forma curta da lista: o primeiro código e reticências.

    Uma fronteira só sai sem reticências — não há o que reticenciar. Com mais de
    uma, o sufixo avisa que há continuação e o tooltip (ou o ``Ctrl+C``) mostra
    tudo. Medido no alimentador de Cocalinho, o máximo são cinco chaves e o
    comum são duas, então a lista inteira nunca vira parágrafo; o que se evita
    aqui é a coluna ficar larga o bastante para empurrar as outras da tela.
    """

    codes = record.boundary_switch_codes
    if not codes:
        return ""
    if len(codes) == 1:
        return codes[0]
    return f"{codes[0]}{TRUNCATION_SUFFIX}"


__all__ = [
    "BLOCK_CIRCUIT_COLUMN",
    "BLOCK_LENGTH_COLUMN",
    "BLOCK_NUMERIC_COLUMNS",
    "BLOCK_NUMBER_COLUMN",
    "BLOCK_POWER_COLUMN",
    "BLOCK_SOURCE_COLUMN",
    "BLOCK_SWITCHES_COLUMN",
    "BLOCK_TABLE_HEADERS",
    "SWITCH_SEPARATOR",
    "TRUNCATION_SUFFIX",
    "block_table_values",
    "switch_list_summary",
    "switch_list_text",
]
