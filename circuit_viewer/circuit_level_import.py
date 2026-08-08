"""Importação compartilhada dos patamares horários por circuito."""

from __future__ import annotations

import csv
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .calculation_levels import (
    CALCULATION_LEVEL_COUNT,
    CalculationLevel,
    CalculationLevelSchedule,
)
from .circuit_calculation_levels import CircuitCalculationLevelsModel
from .csv_import import (
    PROGRESS_ROW_INTERVAL,
    CsvImportCancelled,
    CsvImportError,
    RowProgress,
    TextRow,
    byte_progress,
    normalize_header,
)
from .model import CircuitCatalogModel


EXPECTED_CIRCUIT_LEVEL_HEADER = (
    "CIRC_ID",
    "NPAT",
    "NOME",
    "HORARIO_INI",
    "HORARIO_FIM",
    "HORARIO_REF",
)
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class CircuitLevelIssue:
    line_number: int
    reason: str
    source: str = ""


@dataclass(frozen=True, slots=True)
class CircuitLevelCsvResult:
    model: CircuitCalculationLevelsModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[CircuitLevelIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for name in EXPECTED_CIRCUIT_LEVEL_HEADER:
        matches = [index for index, value in enumerate(header) if value == name]
        if not matches:
            missing.append(name)
        elif len(matches) > 1:
            duplicated.append(name)
        else:
            positions[name] = matches[0]
    if missing or duplicated:
        details: list[str] = []
        if missing:
            details.append("ausentes: " + ", ".join(missing))
        if duplicated:
            details.append("duplicadas: " + ", ".join(duplicated))
        raise CsvImportError(
            "Cabeçalho inválido; colunas obrigatórias " + "; ".join(details) + "."
        )
    return positions


def _integer(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} deve ser um número inteiro") from exc
    return parsed


def parse_circuit_level_rows(
    raw_header: Iterable[str],
    rows: Iterable[TextRow],
    circuits: CircuitCatalogModel,
    *,
    source_label: str,
    encoding: str,
    first_line_number: int = 2,
    cancel_event: threading.Event | None = None,
    progress: RowProgress | None = None,
) -> CircuitLevelCsvResult:
    """Agrupa linhas por ``CIRC_ID`` e valida cada agenda integralmente."""

    positions = _column_positions(normalize_header(raw_header))
    last_required = max(positions.values())
    groups: dict[str, list[tuple[int, dict[str, str]]]] = {}
    malformed: list[tuple[int, str]] = []
    total_rows = 0

    for line_number, row in enumerate(rows, start=first_line_number):
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        if not row or not any(str(value).strip() for value in row):
            continue
        total_rows += 1
        if progress is not None and total_rows % PROGRESS_ROW_INTERVAL == 0:
            progress(total_rows)
        if len(row) <= last_required:
            malformed.append((line_number, "faltam valores em colunas obrigatórias"))
            continue
        values = {name: str(row[index]).strip() for name, index in positions.items()}
        circuit_id = values["CIRC_ID"]
        if not circuit_id:
            malformed.append((line_number, "CIRC_ID vazio"))
            continue
        groups.setdefault(circuit_id, []).append((line_number, values))

    issues: list[CircuitLevelIssue] = []
    omitted = 0

    def report(line_number: int, reason: str) -> None:
        nonlocal omitted
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(CircuitLevelIssue(line_number, reason, source_label))
        else:
            omitted += 1

    invalid_groups = 0
    for line_number, reason in malformed:
        invalid_groups += 1
        report(line_number, reason)

    schedules: list[CalculationLevelSchedule | None] = [None] * len(circuits)
    valid_groups = 0
    for circuit_id, entries in groups.items():
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        first_line = entries[0][0]
        circuit_index = circuits.index_for_id(circuit_id)
        if circuit_index is None:
            invalid_groups += 1
            report(first_line, f"CIRC_ID inexistente no catálogo atual: {circuit_id}")
            continue
        by_npat: dict[int, CalculationLevel] = {}
        problem: tuple[int, str] | None = None
        for line_number, values in entries:
            try:
                npat = _integer(values["NPAT"], "NPAT")
                if npat in by_npat:
                    raise ValueError(f"NPAT duplicado no circuito {circuit_id}: {npat}")
                by_npat[npat] = CalculationLevel(
                    npat,
                    values["NOME"],
                    _integer(values["HORARIO_INI"], "HORARIO_INI"),
                    _integer(values["HORARIO_FIM"], "HORARIO_FIM"),
                    _integer(values["HORARIO_REF"], "HORARIO_REF"),
                )
            except (TypeError, ValueError) as exc:
                problem = (line_number, str(exc))
                break
        if problem is None and set(by_npat) != set(range(CALCULATION_LEVEL_COUNT)):
            missing = sorted(set(range(CALCULATION_LEVEL_COUNT)) - set(by_npat))
            extras = sorted(set(by_npat) - set(range(CALCULATION_LEVEL_COUNT)))
            details = []
            if missing:
                details.append("faltam NPAT " + ", ".join(map(str, missing)))
            if extras:
                details.append("NPAT fora do conjunto 0–3: " + ", ".join(map(str, extras)))
            problem = (first_line, "; ".join(details))
        if problem is None:
            try:
                schedule = CalculationLevelSchedule(tuple(by_npat.values()))
            except ValueError as exc:
                problem = (first_line, str(exc))
        if problem is not None:
            invalid_groups += 1
            report(problem[0], f"Circuito {circuit_id}: {problem[1]}")
            continue
        schedules[circuit_index] = schedule
        valid_groups += 1

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not valid_groups:
        raise CsvImportError("Nenhum circuito com quatro patamares válidos foi encontrado.")
    if progress is not None:
        progress(total_rows)
    model = CircuitCalculationLevelsModel(circuits, schedules, source_path=source_label)
    return CircuitLevelCsvResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=valid_groups,
        invalid_rows=invalid_groups,
        issues=tuple(issues),
        omitted_issues=omitted,
    )


def _parse_file(
    path: Path,
    circuits: CircuitCatalogModel,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> CircuitLevelCsvResult:
    total_bytes = max(path.stat().st_size, 1)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de patamares dos circuitos está vazio.") from exc
        return parse_circuit_level_rows(
            header,
            reader,
            circuits,
            source_label=str(path.resolve()),
            encoding=encoding,
            cancel_event=cancel_event,
            progress=byte_progress(source, total_bytes, progress),
        )


def load_circuit_levels_csv(
    path: str | os.PathLike[str],
    circuits: CircuitCatalogModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> CircuitLevelCsvResult:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, circuits, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, circuits, "cp1252", cancel_event, progress)
