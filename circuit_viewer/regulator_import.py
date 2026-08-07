"""Importação transacional dos reguladores de tensão associados aos trechos.

Segue o contrato comum dos importadores do projeto e o molde de
``switch_import``, com o mesmo vínculo 1:1 pelo ``TRECHO_ID``. A diferença não
está aqui e sim no destino: reguladores não entram na topologia, então importá-los
não reconstrói o catálogo de circuitos nem invalida análise alguma.
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
from .model import LineNetworkModel, RegulatorModel


EXPECTED_REGULATOR_HEADER = (
    "REGU_ID",
    "TRECHO_ID",
    "EXTERN_ID",
    "CODIGO",
    "LIGACAO",
    "SNOM",
    "FAIXA",
    "NPASSOS",
    "TAP",
    "INOM",
    "VNOM",
)
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class RegulatorIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class RegulatorLoadResult:
    model: RegulatorModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[RegulatorIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for required_name in EXPECTED_REGULATOR_HEADER:
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


def parse_regulator_rows(
    raw_header: Iterable[str],
    rows: Iterable[TextRow],
    segments: LineNetworkModel,
    *,
    source_label: str,
    encoding: str,
    first_line_number: int = 2,
    cancel_event: threading.Event | None = None,
    progress: RowProgress | None = None,
) -> RegulatorLoadResult:
    """Valida linhas de reguladores já em texto e devolve o modelo.

    Toda a validação vive aqui, independente da fonte: o CSV e o banco Access
    apenas entregam cabeçalho e linhas de texto.
    """

    regulator_ids: list[str] = []
    segment_indices: list[int] = []
    external_ids: list[str] = []
    codes: list[str] = []
    connections: list[str] = []
    snom_values: list[str] = []
    regulation_ranges: list[str] = []
    step_counts: list[str] = []
    tap_values: list[str] = []
    inom_values: list[str] = []
    vnom_values: list[str] = []
    seen_regulator_ids: set[str] = set()
    seen_segment_indices: set[int] = set()
    issues: list[RegulatorIssue] = []
    total_rows = 0
    invalid_rows = 0

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(RegulatorIssue(line_number, reason))

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
        regulator_id = values["REGU_ID"]
        if not regulator_id:
            add_issue(line_number, "REGU_ID vazio")
            continue
        if regulator_id in seen_regulator_ids:
            add_issue(line_number, f"REGU_ID duplicado: {regulator_id}")
            continue

        segment_id = values["TRECHO_ID"]
        segment_index = segments.index_for_id(segment_id)
        if not segment_id or segment_index is None:
            add_issue(
                line_number,
                f"trecho inexistente: {segment_id or '<vazio>'}",
            )
            continue
        if segment_index in seen_segment_indices:
            add_issue(
                line_number,
                f"TRECHO_ID com mais de um regulador: {segment_id}",
            )
            continue

        seen_regulator_ids.add(regulator_id)
        seen_segment_indices.add(segment_index)
        regulator_ids.append(regulator_id)
        segment_indices.append(segment_index)
        external_ids.append(values["EXTERN_ID"])
        codes.append(values["CODIGO"])
        connections.append(values["LIGACAO"])
        snom_values.append(values["SNOM"])
        regulation_ranges.append(values["FAIXA"])
        step_counts.append(values["NPASSOS"])
        tap_values.append(values["TAP"])
        inom_values.append(values["INOM"])
        vnom_values.append(values["VNOM"])

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not regulator_ids:
        raise CsvImportError("Nenhum regulador válido foi encontrado no arquivo.")
    if progress is not None:
        progress(total_rows)

    model = RegulatorModel(
        segments,
        regulator_ids,
        segment_indices,
        external_ids,
        codes,
        connections,
        snom_values,
        regulation_ranges,
        step_counts,
        tap_values,
        inom_values,
        vnom_values,
        source_path=source_label,
    )
    return RegulatorLoadResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=len(regulator_ids),
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
    )


def _parse_file(
    path: Path,
    segments: LineNetworkModel,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> RegulatorLoadResult:
    total_bytes = max(path.stat().st_size, 1)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de reguladores está vazio.") from exc
        return parse_regulator_rows(
            raw_header,
            reader,
            segments,
            source_label=str(path.resolve()),
            encoding=encoding,
            cancel_event=cancel_event,
            progress=byte_progress(source, total_bytes, progress),
        )


def load_regulators_csv(
    path: str | os.PathLike[str],
    segments: LineNetworkModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> RegulatorLoadResult:
    """Carrega reguladores vinculando cada registro ao trecho informado."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, segments, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, segments, "cp1252", cancel_event, progress)
