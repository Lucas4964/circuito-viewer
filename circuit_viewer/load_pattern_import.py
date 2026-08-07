"""Importação transacional dos patamares complementares de cargas."""

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
from .model import LoadModel, LoadPatternModel, LoadPatternRecord


EXPECTED_LOAD_PATTERN_HEADER = (
    "CARGA_ID",
    "NPAT",
    "PD",
    "PE",
    "PF",
    "QD",
    "QE",
    "QF",
)
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class LoadPatternIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class LoadPatternCsvResult:
    model: LoadPatternModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[LoadPatternIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for required_name in EXPECTED_LOAD_PATTERN_HEADER:
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


def parse_load_pattern_rows(
    raw_header: Iterable[str],
    rows: Iterable[TextRow],
    loads: LoadModel,
    *,
    source_label: str,
    encoding: str,
    first_line_number: int = 2,
    cancel_event: threading.Event | None = None,
    progress: RowProgress | None = None,
) -> LoadPatternCsvResult:
    """Valida linhas de patamares já em texto e devolve o modelo.

    Toda a validação vive aqui, independente da fonte: o CSV e o banco Access
    apenas entregam cabeçalho e linhas de texto.
    """

    records_by_load: dict[int, dict[int, LoadPatternRecord]] = {}
    first_line_by_load: dict[int, int] = {}
    invalid_groups: set[int] = set()
    issues: list[LoadPatternIssue] = []
    issue_count = 0
    total_rows = 0

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal issue_count
        issue_count += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(LoadPatternIssue(line_number, reason))

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
        load_index = loads.index_for_id(load_id)
        if not load_id or load_index is None:
            add_issue(line_number, f"carga inexistente: {load_id or '<vazio>'}")
            continue

        first_line_by_load.setdefault(load_index, line_number)
        group = records_by_load.setdefault(load_index, {})
        npat_text = values["NPAT"]
        if npat_text not in {"0", "1", "2", "3"}:
            invalid_groups.add(load_index)
            add_issue(
                line_number,
                f"NPAT inválido para CARGA_ID {load_id}: {npat_text or '<vazio>'}",
            )
            continue
        npat = int(npat_text)
        if npat in group:
            invalid_groups.add(load_index)
            add_issue(
                line_number,
                f"NPAT duplicado para CARGA_ID {load_id}: {npat}",
            )
            continue
        group[npat] = LoadPatternRecord(
            load_id=load_id,
            npat=npat,
            pd=values["PD"],
            pe=values["PE"],
            pf=values["PF"],
            qd=values["QD"],
            qe=values["QE"],
            qf=values["QF"],
        )

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")

    dense_groups: list[tuple[LoadPatternRecord, ...] | None] = [None] * len(loads)
    valid_rows = 0
    for load_index, group in records_by_load.items():
        if load_index in invalid_groups:
            continue
        missing = sorted({0, 1, 2, 3}.difference(group))
        if missing:
            add_issue(
                first_line_by_load[load_index],
                "grupo incompleto para CARGA_ID "
                f"{loads.load_ids[load_index]}; NPAT ausentes: "
                + ", ".join(str(value) for value in missing),
            )
            continue
        dense_groups[load_index] = tuple(group[npat] for npat in range(4))
        valid_rows += 4

    if valid_rows == 0:
        raise CsvImportError(
            "Nenhum grupo completo de patamares foi encontrado no arquivo."
        )
    if progress is not None:
        progress(total_rows)

    model = LoadPatternModel(
        loads,
        dense_groups,
        source_path=source_label,
    )
    invalid_rows = total_rows - valid_rows
    return LoadPatternCsvResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=valid_rows,
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, issue_count - len(issues)),
    )


def _parse_file(
    path: Path,
    loads: LoadModel,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> LoadPatternCsvResult:
    total_bytes = max(path.stat().st_size, 1)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de patamares está vazio.") from exc
        return parse_load_pattern_rows(
            raw_header,
            reader,
            loads,
            source_label=str(path.resolve()),
            encoding=encoding,
            cancel_event=cancel_event,
            progress=byte_progress(source, total_bytes, progress),
        )


def load_load_patterns_csv(
    path: str | os.PathLike[str],
    loads: LoadModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> LoadPatternCsvResult:
    """Carrega grupos completos NPAT 0–3 associados às cargas."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, loads, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, loads, "cp1252", cancel_event, progress)
