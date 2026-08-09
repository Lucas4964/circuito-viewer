"""Colunas compartilhadas e exportação CSV pt-BR da tabela de ramais."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import csv
from dataclasses import dataclass
from decimal import Decimal
import io
import math
import os
from pathlib import Path
import tempfile

from .branch_analysis import BranchAnalysisResult, BranchRecord
from .equivalent_network import EquivalentNetworkResult
from .opendss_export import sanitize_dss_name


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]

BRANCH_TABLE_HEADERS = (
    "RAMAL_ID",
    "TIPO_RAMAL",
    "CIRC_ID",
    "BARRA_ID",
    "BARRA_CODIGO",
    "NIVEL_TOPOLOGICO",
    "TRECHO_ID",
    "TRECHO_CODIGO",
    "NUM_TRECHOS",
    "COMPR",
    "NUM_CARGAS",
    "DEMANDA_MAXIMA",
    "FASES2",
    "FASE",
    "REMANEJAVEL",
    "NUM_BARRAS",
    "NUM_CHAVES",
    "POS_PRIMEIRA_CHAVE",
    "NUM_CONEXOES_TRONCO",
    "NUM_COMPR_AUSENTE",
    "TOPOLOGIA",
)
BRANCH_NUMERIC_COLUMNS = frozenset(
    {0, 5, 8, 9, 10, 11, 14, 15, 16, 17, 18, 19}
)
BRANCH_LENGTH_COLUMN = 9
BRANCH_MAXIMUM_DEMAND_COLUMN = 11
BRANCH_REMOVABLE_COLUMN = 14


@dataclass(frozen=True, slots=True)
class BranchCsvExportResult:
    path: Path
    branch_count: int
    circuit_ids: tuple[str, ...]


def suggested_branch_csv_filename(circuit_id: str | None) -> str:
    if circuit_id is None:
        return "ramais_todos.csv"
    normalized = sanitize_dss_name(circuit_id) or "circuito"
    return f"ramais_{normalized}.csv"


def branch_table_values(
    record: BranchRecord,
    maximum_active_demand: Decimal | None,
) -> tuple[object, ...]:
    """Devolve a linha canônica usada tanto pelo Qt quanto pelo CSV."""

    return (
        record.branch_id,
        record.branch_type.value,
        record.circuit_id,
        record.connection_bar_id,
        record.connection_bar_code,
        record.topological_level,
        record.first_segment_id,
        record.first_segment_code,
        record.segment_count,
        record.total_length,
        record.load_count,
        maximum_active_demand,
        record.phases2,
        record.phase,
        int(record.removable),
        record.bar_count,
        record.switch_count,
        record.first_switch_position,
        record.trunk_connection_count,
        record.missing_length_count,
        record.topology,
    )


def _maximum_demands(
    branches: BranchAnalysisResult,
    equivalent: EquivalentNetworkResult | None,
) -> dict[int, Decimal | None]:
    if equivalent is None:
        return {}
    if equivalent.model.branches is not branches:
        raise ValueError("A rede equivalente não corresponde aos ramais exportados.")
    return {
        record.branch_id: record.maximum_active_demand
        for record in equivalent.model.records
    }


def _format_pt_br(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("O CSV não aceita valores decimais não finitos.")
        return format(value, "f").replace(".", ",")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("O CSV não aceita valores numéricos não finitos.")
        return repr(value).replace(".", ",")
    if isinstance(value, int):
        return str(value)
    return str(value)


def build_branches_csv_bytes(
    branches: BranchAnalysisResult,
    equivalent: EquivalentNetworkResult | None,
    branch_indices: Sequence[int],
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> bytes:
    """Serializa na ordem recebida, sem depender do modelo Qt."""

    selected = tuple(int(index) for index in branch_indices)
    if len(set(selected)) != len(selected):
        raise ValueError("A seleção contém ramais duplicados.")
    if any(index < 0 or index >= len(branches.records) for index in selected):
        raise IndexError("A seleção contém um ramal inexistente.")
    cancelled = cancel_check or (lambda: False)
    demands = _maximum_demands(branches, equivalent)
    stream = io.StringIO(newline="")
    writer = csv.writer(
        stream,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writerow(BRANCH_TABLE_HEADERS)
    total = len(selected)
    for position, branch_index in enumerate(selected, start=1):
        if cancelled():
            raise InterruptedError("Exportação CSV dos ramais cancelada.")
        record = branches.records[branch_index]
        writer.writerow(
            _format_pt_br(value)
            for value in branch_table_values(
                record,
                demands.get(record.branch_id),
            )
        )
        if progress is not None:
            progress(position, total)
    if cancelled():
        raise InterruptedError("Exportação CSV dos ramais cancelada.")
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def export_branches_csv(
    path: str | Path,
    branches: BranchAnalysisResult,
    equivalent: EquivalentNetworkResult | None,
    branch_indices: Sequence[int],
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> BranchCsvExportResult:
    """Gera o CSV por completo e só então substitui o destino atomicamente."""

    cancelled = cancel_check or (lambda: False)
    selected = tuple(int(index) for index in branch_indices)
    content = build_branches_csv_bytes(
        branches,
        equivalent,
        selected,
        cancel_check=cancelled,
        progress=progress,
    )
    if cancelled():
        raise InterruptedError("Exportação CSV dos ramais cancelada.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise InterruptedError("Exportação CSV dos ramais cancelada.")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    circuit_ids = tuple(
        dict.fromkeys(branches.records[index].circuit_id for index in selected)
    )
    return BranchCsvExportResult(target, len(selected), circuit_ids)
