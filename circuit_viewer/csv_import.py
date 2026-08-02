"""Importação transacional do CSV de barras."""

from __future__ import annotations

import csv
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .model import CircuitModel, UtmCrs


EXPECTED_HEADER = ("BARRA_ID", "CODIGO", "X", "Y")
MAX_REPORTED_ISSUES = 200

ProgressCallback = Callable[[int, int, int], None]


class CsvImportError(ValueError):
    """Erro fatal que impede a criação de um novo modelo."""


class CsvImportCancelled(RuntimeError):
    """Importação interrompida pelo usuário."""


@dataclass(frozen=True, slots=True)
class CsvIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class CsvLoadResult:
    model: CircuitModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[CsvIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _parse_coordinate(value: str) -> float:
    text = value.strip()
    if not text:
        raise ValueError("coordenada vazia")
    if "," in text and "." in text:
        raise ValueError("separadores decimal e de milhar misturados")
    if "," in text:
        text = text.replace(",", ".")
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("coordenada não finita")
    return number


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _parse_file(
    path: Path,
    crs: UtmCrs,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> CsvLoadResult:
    bar_ids: list[str] = []
    codes: list[str] = []
    xs: list[float] = []
    ys: list[float] = []
    seen_ids: set[str] = set()
    issues: list[CsvIssue] = []
    total_rows = 0
    invalid_rows = 0
    total_bytes = max(path.stat().st_size, 1)

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(CsvIssue(line_number, reason))

    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV está vazio.") from exc

        normalized_header = tuple(value.strip().lstrip("\ufeff") for value in header)
        column_positions: dict[str, int] = {}
        missing_columns: list[str] = []
        duplicated_columns: list[str] = []
        for required_name in EXPECTED_HEADER:
            positions = [
                index
                for index, column_name in enumerate(normalized_header)
                if column_name == required_name
            ]
            if not positions:
                missing_columns.append(required_name)
            elif len(positions) > 1:
                duplicated_columns.append(required_name)
            else:
                column_positions[required_name] = positions[0]

        if missing_columns or duplicated_columns:
            problems: list[str] = []
            if missing_columns:
                problems.append("ausentes: " + ", ".join(missing_columns))
            if duplicated_columns:
                problems.append("duplicadas: " + ", ".join(duplicated_columns))
            raise CsvImportError(
                "Cabeçalho inválido; colunas obrigatórias " + "; ".join(problems) + "."
            )

        last_required_position = max(column_positions.values())

        for line_number, row in enumerate(reader, start=2):
            if _cancelled(cancel_event):
                raise CsvImportCancelled("Importação cancelada.")
            if not row or not any(value.strip() for value in row):
                continue

            total_rows += 1
            if progress is not None and total_rows % 1_000 == 0:
                try:
                    position = source.buffer.tell()
                except (AttributeError, OSError):
                    position = 0
                progress(total_rows, min(position, total_bytes), total_bytes)
            if len(row) <= last_required_position:
                add_issue(line_number, "faltam valores em colunas obrigatórias")
                continue

            bar_id = row[column_positions["BARRA_ID"]].strip()
            code = row[column_positions["CODIGO"]].strip()
            raw_x = row[column_positions["X"]].strip()
            raw_y = row[column_positions["Y"]].strip()
            if not bar_id:
                add_issue(line_number, "BARRA_ID vazio")
                continue
            if bar_id in seen_ids:
                add_issue(line_number, f"BARRA_ID duplicado: {bar_id}")
                continue

            try:
                x = _parse_coordinate(raw_x)
                y = _parse_coordinate(raw_y)
            except ValueError as exc:
                add_issue(line_number, str(exc))
                continue

            seen_ids.add(bar_id)
            bar_ids.append(bar_id)
            codes.append(code)
            xs.append(x)
            ys.append(y)

    if _cancelled(cancel_event):
        raise CsvImportCancelled("Importação cancelada.")
    if not bar_ids:
        raise CsvImportError("Nenhuma linha válida foi encontrada no arquivo.")

    if progress is not None:
        progress(total_rows, total_bytes, total_bytes)

    model = CircuitModel(
        bar_ids,
        codes,
        xs,
        ys,
        crs,
        source_path=str(path.resolve()),
    )
    return CsvLoadResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=len(bar_ids),
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
    )


def load_csv(
    path: str | os.PathLike[str],
    crs: UtmCrs,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> CsvLoadResult:
    """Carrega um CSV, tentando UTF-8 com BOM e depois CP-1252.

    O chamador recebe um modelo completo ou uma exceção; nenhum estado externo é
    alterado durante o processamento.
    """

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")

    try:
        return _parse_file(csv_path, crs, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if _cancelled(cancel_event):
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, crs, "cp1252", cancel_event, progress)
