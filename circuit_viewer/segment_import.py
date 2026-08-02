"""Importação transacional dos trechos da rede elétrica."""

from __future__ import annotations

import csv
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .csv_import import CsvImportCancelled, CsvImportError
from .model import CircuitModel, LineNetworkModel


EXPECTED_SEGMENT_HEADER = (
    "TRECHO_ID",
    "CODIGO",
    "FASES2",
    "BARRA1_ID",
    "BARRA2_ID",
    "ARRANJO_ID",
    "CABOF_ID",
    "CABON_ID",
    "COMPR",
)
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class SegmentIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class SegmentLoadResult:
    model: LineNetworkModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[SegmentIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for required_name in EXPECTED_SEGMENT_HEADER:
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


def _parse_length(value: str) -> float:
    text = value.strip()
    if not text:
        return math.nan
    if "," in text and "." in text:
        raise ValueError("COMPR possui separadores decimal e de milhar misturados")
    if "," in text:
        text = text.replace(",", ".")
    length = float(text)
    if not math.isfinite(length) or length < 0:
        raise ValueError("COMPR deve ser um número finito não negativo")
    return length


def _parse_file(
    path: Path,
    bars: CircuitModel,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> SegmentLoadResult:
    segment_ids: list[str] = []
    codes: list[str] = []
    phases: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    arrangements: list[str] = []
    phase_cables: list[str] = []
    neutral_cables: list[str] = []
    lengths: list[float] = []
    seen_ids: set[str] = set()
    issues: list[SegmentIssue] = []
    total_rows = 0
    invalid_rows = 0
    total_bytes = max(path.stat().st_size, 1)

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(SegmentIssue(line_number, reason))

    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de trechos está vazio.") from exc
        header = tuple(value.strip().lstrip("\ufeff") for value in raw_header)
        positions = _column_positions(header)
        last_required_position = max(positions.values())

        for line_number, row in enumerate(reader, start=2):
            if cancel_event is not None and cancel_event.is_set():
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

            values = {name: row[index].strip() for name, index in positions.items()}
            segment_id = values["TRECHO_ID"]
            if not segment_id:
                add_issue(line_number, "TRECHO_ID vazio")
                continue
            if segment_id in seen_ids:
                add_issue(line_number, f"TRECHO_ID duplicado: {segment_id}")
                continue

            start_id = values["BARRA1_ID"]
            end_id = values["BARRA2_ID"]
            start_index = bars.index_for_id(start_id)
            end_index = bars.index_for_id(end_id)
            missing_bars = [
                bar_id
                for bar_id, bar_index in ((start_id, start_index), (end_id, end_index))
                if not bar_id or bar_index is None
            ]
            if missing_bars:
                names = ", ".join(dict.fromkeys(value or "<vazia>" for value in missing_bars))
                add_issue(line_number, f"barra(s) inexistente(s): {names}")
                continue

            try:
                length = _parse_length(values["COMPR"])
            except ValueError as exc:
                add_issue(line_number, str(exc))
                continue

            seen_ids.add(segment_id)
            segment_ids.append(segment_id)
            codes.append(values["CODIGO"])
            phases.append(values["FASES2"])
            starts.append(int(start_index))
            ends.append(int(end_index))
            arrangements.append(values["ARRANJO_ID"])
            phase_cables.append(values["CABOF_ID"])
            neutral_cables.append(values["CABON_ID"])
            lengths.append(length)

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not segment_ids:
        raise CsvImportError("Nenhum trecho válido foi encontrado no arquivo.")
    if progress is not None:
        progress(total_rows, total_bytes, total_bytes)

    model = LineNetworkModel(
        bars,
        segment_ids,
        codes,
        phases,
        starts,
        ends,
        arrangements,
        phase_cables,
        neutral_cables,
        lengths,
        source_path=str(path.resolve()),
    )
    return SegmentLoadResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=len(segment_ids),
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
    )


def load_segments_csv(
    path: str | os.PathLike[str],
    bars: CircuitModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> SegmentLoadResult:
    """Carrega trechos vinculando seus extremos ao modelo de barras informado."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, bars, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, bars, "cp1252", cancel_event, progress)

