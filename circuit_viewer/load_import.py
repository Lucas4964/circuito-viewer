"""Importação transacional de cargas associadas às barras."""

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
from .model import CircuitModel, LoadModel


EXPECTED_LOAD_HEADER = (
    "CARGA_ID",
    "BARRA_ID",
    "EXTERN_ID",
    "CODIGO",
    "SNOM",
    "SADM",
    "VLINHASEC",
    "FASES2",
    "TIPO_LIG",
)
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class LoadIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class LoadCsvResult:
    model: LoadModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[LoadIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for required_name in EXPECTED_LOAD_HEADER:
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


def parse_load_rows(
    raw_header: Iterable[str],
    rows: Iterable[TextRow],
    bars: CircuitModel,
    *,
    source_label: str,
    encoding: str,
    first_line_number: int = 2,
    cancel_event: threading.Event | None = None,
    progress: RowProgress | None = None,
) -> LoadCsvResult:
    """Valida linhas de cargas já em texto e devolve o modelo.

    Toda a validação vive aqui, independente da fonte: o CSV e o banco Access
    apenas entregam cabeçalho e linhas de texto.
    """

    columns: dict[str, list[str]] = {
        name: [] for name in EXPECTED_LOAD_HEADER if name != "BARRA_ID"
    }
    bar_indices: list[int] = []
    seen_load_ids: set[str] = set()
    issues: list[LoadIssue] = []
    total_rows = 0
    invalid_rows = 0

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(LoadIssue(line_number, reason))

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
        load_id = values["CARGA_ID"]
        if not load_id:
            add_issue(line_number, "CARGA_ID vazio")
            continue
        if load_id in seen_load_ids:
            add_issue(line_number, f"CARGA_ID duplicado: {load_id}")
            continue

        bar_id = values["BARRA_ID"]
        bar_index = bars.index_for_id(bar_id)
        if not bar_id or bar_index is None:
            add_issue(line_number, f"barra inexistente: {bar_id or '<vazio>'}")
            continue

        seen_load_ids.add(load_id)
        bar_indices.append(bar_index)
        for name, target in columns.items():
            target.append(values[name])

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not columns["CARGA_ID"]:
        raise CsvImportError("Nenhuma carga válida foi encontrada no arquivo.")
    if progress is not None:
        progress(total_rows)

    model = LoadModel(
        bars,
        columns["CARGA_ID"],
        bar_indices,
        columns["EXTERN_ID"],
        columns["CODIGO"],
        columns["SNOM"],
        columns["SADM"],
        columns["VLINHASEC"],
        columns["FASES2"],
        columns["TIPO_LIG"],
        source_path=source_label,
    )
    return LoadCsvResult(
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
) -> LoadCsvResult:
    total_bytes = max(path.stat().st_size, 1)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de cargas está vazio.") from exc
        return parse_load_rows(
            raw_header,
            reader,
            bars,
            source_label=str(path.resolve()),
            encoding=encoding,
            cancel_event=cancel_event,
            progress=byte_progress(source, total_bytes, progress),
        )


def load_loads_csv(
    path: str | os.PathLike[str],
    bars: CircuitModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> LoadCsvResult:
    """Carrega cargas vinculando cada registro à barra informada."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, bars, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, bars, "cp1252", cancel_event, progress)
