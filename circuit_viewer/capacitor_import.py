"""Importação transacional de bancos de capacitores associados às barras.

Mesmo contrato do importador de cargas: toda a validação vive em
``parse_capacitor_rows``, e CSV e banco Access apenas entregam cabeçalho e
linhas de texto.

Uma diferença do cadastro merece atenção: a fase vem em ``FASES``, a string de
letras (``DEFN``), e não no código numérico ``FASES2`` que as cargas usam. Aqui
ela é preservada como texto; quem resolve as letras é a exportação.
"""

from __future__ import annotations

import csv
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .csv_import import (
    PROGRESS_ROW_INTERVAL,
    CsvImportCancelled,
    CsvImportError,
    RowProgress,
    TextRow,
    byte_progress,
    normalize_header,
)
from .model import CapacitorModel, CircuitModel


EXPECTED_CAPACITOR_HEADER = (
    "CAPAC_ID",
    "BARRA_ID",
    "EXTERN_ID",
    "CODIGO",
    "VNOM",
    "Q1",
    "Q2",
    "Q3",
    "Q4",
    "FASES",
    "LIGACAO",
)
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class CapacitorIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class CapacitorCsvResult:
    model: CapacitorModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[CapacitorIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for required_name in EXPECTED_CAPACITOR_HEADER:
        matches = [index for index, name in enumerate(header) if name == required_name]
        if not matches:
            missing.append(required_name)
        elif len(matches) > 1:
            duplicated.append(required_name)
        else:
            positions[required_name] = matches[0]
    if missing or duplicated:
        problems: list[str] = []
        if missing:
            problems.append("ausentes: " + ", ".join(missing))
        if duplicated:
            problems.append("duplicadas: " + ", ".join(duplicated))
        raise CsvImportError(
            "Cabeçalho inválido; colunas obrigatórias " + "; ".join(problems) + "."
        )
    return positions


def parse_capacitor_rows(
    raw_header: Iterable[str],
    rows: Iterable[TextRow],
    bars: CircuitModel,
    *,
    source_label: str,
    encoding: str,
    first_line_number: int = 2,
    cancel_event: threading.Event | None = None,
    progress: RowProgress | None = None,
) -> CapacitorCsvResult:
    """Valida linhas de capacitores já em texto e devolve o modelo."""

    columns: dict[str, list[str]] = {
        name: [] for name in EXPECTED_CAPACITOR_HEADER if name != "BARRA_ID"
    }
    bar_indices: list[int] = []
    seen_capacitor_ids: set[str] = set()
    issues: list[CapacitorIssue] = []
    total_rows = 0
    invalid_rows = 0

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(CapacitorIssue(line_number, reason))

    header = normalize_header(raw_header)
    positions = _column_positions(header)
    last_required_position = max(positions.values())

    for line_number, row in enumerate(rows, start=first_line_number):
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        if not row or not any(value.strip() for value in row):
            continue
        total_rows += 1
        if progress is not None and total_rows % PROGRESS_ROW_INTERVAL == 0:
            progress(total_rows)
        if len(row) <= last_required_position:
            add_issue(line_number, "faltam valores em colunas obrigatórias")
            continue

        values = {name: row[index].strip() for name, index in positions.items()}
        capacitor_id = values["CAPAC_ID"]
        if not capacitor_id:
            add_issue(line_number, "CAPAC_ID vazio")
            continue
        if capacitor_id in seen_capacitor_ids:
            add_issue(line_number, f"CAPAC_ID duplicado: {capacitor_id}")
            continue

        bar_id = values["BARRA_ID"]
        bar_index = bars.index_for_id(bar_id)
        if not bar_id or bar_index is None:
            add_issue(line_number, f"barra inexistente: {bar_id or '<vazio>'}")
            continue

        seen_capacitor_ids.add(capacitor_id)
        bar_indices.append(bar_index)
        for name, target in columns.items():
            target.append(values[name])

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not columns["CAPAC_ID"]:
        raise CsvImportError("Nenhum capacitor válido foi encontrado no arquivo.")
    if progress is not None:
        progress(total_rows)

    model = CapacitorModel(
        bars,
        columns["CAPAC_ID"],
        bar_indices,
        columns["EXTERN_ID"],
        columns["CODIGO"],
        columns["VNOM"],
        columns["Q1"],
        columns["Q2"],
        columns["Q3"],
        columns["Q4"],
        columns["FASES"],
        columns["LIGACAO"],
        source_path=source_label,
    )
    return CapacitorCsvResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=len(model),
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
    )


def _parse_file(
    path: Path,
    bars: CircuitModel,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> CapacitorCsvResult:
    total_bytes = max(path.stat().st_size, 1)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de capacitores está vazio.") from exc
        return parse_capacitor_rows(
            raw_header,
            reader,
            bars,
            source_label=str(path.resolve()),
            encoding=encoding,
            cancel_event=cancel_event,
            progress=byte_progress(source, total_bytes, progress),
        )


def load_capacitors_csv(
    path: str | os.PathLike[str],
    bars: CircuitModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> CapacitorCsvResult:
    """Carrega capacitores vinculando cada banco à barra informada."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, bars, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, bars, "cp1252", cancel_event, progress)
