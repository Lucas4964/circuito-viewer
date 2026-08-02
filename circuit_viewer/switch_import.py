"""Importação transacional das chaves associadas aos trechos."""

from __future__ import annotations

import csv
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .csv_import import CsvImportCancelled, CsvImportError
from .model import LineNetworkModel, SwitchModel


EXPECTED_SWITCH_HEADER = (
    "CHAVE_ID",
    "TIPOCHV_ID",
    "CIRC_ID",
    "TRECHO_ID",
    "CODIGO",
    "ESTADO",
    "ESTADO_NORMAL",
    "CORN",
    "ELO",
    "ELO_TIPO",
)
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class SwitchIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class SwitchLoadResult:
    model: SwitchModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[SwitchIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for required_name in EXPECTED_SWITCH_HEADER:
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


def _parse_file(
    path: Path,
    segments: LineNetworkModel,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> SwitchLoadResult:
    switch_ids: list[str] = []
    switch_type_ids: list[str] = []
    circuit_ids: list[str] = []
    segment_indices: list[int] = []
    codes: list[str] = []
    states: list[str] = []
    normal_states: list[str] = []
    corn_values: list[str] = []
    elo_values: list[str] = []
    elo_types: list[str] = []
    seen_switch_ids: set[str] = set()
    seen_segment_indices: set[int] = set()
    issues: list[SwitchIssue] = []
    total_rows = 0
    invalid_rows = 0
    total_bytes = max(path.stat().st_size, 1)

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(SwitchIssue(line_number, reason))

    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de chaves está vazio.") from exc
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
            switch_id = values["CHAVE_ID"]
            if not switch_id:
                add_issue(line_number, "CHAVE_ID vazio")
                continue
            if switch_id in seen_switch_ids:
                add_issue(line_number, f"CHAVE_ID duplicado: {switch_id}")
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
                add_issue(line_number, f"TRECHO_ID com mais de uma chave: {segment_id}")
                continue

            seen_switch_ids.add(switch_id)
            seen_segment_indices.add(segment_index)
            switch_ids.append(switch_id)
            switch_type_ids.append(values["TIPOCHV_ID"])
            circuit_ids.append(values["CIRC_ID"])
            segment_indices.append(segment_index)
            codes.append(values["CODIGO"])
            states.append(values["ESTADO"])
            normal_states.append(values["ESTADO_NORMAL"])
            corn_values.append(values["CORN"])
            elo_values.append(values["ELO"])
            elo_types.append(values["ELO_TIPO"])

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not switch_ids:
        raise CsvImportError("Nenhuma chave válida foi encontrada no arquivo.")
    if progress is not None:
        progress(total_rows, total_bytes, total_bytes)

    model = SwitchModel(
        segments,
        switch_ids,
        switch_type_ids,
        circuit_ids,
        segment_indices,
        codes,
        states,
        normal_states,
        corn_values,
        elo_values,
        elo_types,
        source_path=str(path.resolve()),
    )
    return SwitchLoadResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=len(switch_ids),
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
    )


def load_switches_csv(
    path: str | os.PathLike[str],
    segments: LineNetworkModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> SwitchLoadResult:
    """Carrega chaves vinculando cada registro ao trecho informado."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, segments, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, segments, "cp1252", cancel_event, progress)
