"""Importação transacional do catálogo de circuitos elétricos."""

from __future__ import annotations

import csv
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .csv_import import CsvImportCancelled, CsvImportError
from .model import (
    CircuitCatalogModel,
    CircuitDefinition,
    LineNetworkModel,
    SwitchModel,
)


EXPECTED_CIRCUIT_HEADER = ("CIRC_ID", "BARRA_ID", "CODIGO", "VNOM")
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class CircuitIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class CircuitLoadResult:
    model: CircuitCatalogModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[CircuitIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return (
            self.invalid_rows > 0
            or self.encoding.lower() == "cp1252"
            or bool(self.model.topology_warnings)
        )


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for required_name in EXPECTED_CIRCUIT_HEADER:
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
    switches: SwitchModel | None,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> CircuitLoadResult:
    definitions: list[CircuitDefinition] = []
    seen_ids: set[str] = set()
    issues: list[CircuitIssue] = []
    total_rows = 0
    invalid_rows = 0
    total_bytes = max(path.stat().st_size, 1)

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(CircuitIssue(line_number, reason))

    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de circuitos está vazio.") from exc
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
            circuit_id = values["CIRC_ID"]
            if not circuit_id:
                add_issue(line_number, "CIRC_ID vazio")
                continue
            if circuit_id in seen_ids:
                add_issue(line_number, f"CIRC_ID duplicado: {circuit_id}")
                continue
            root_bar_id = values["BARRA_ID"]
            if not root_bar_id:
                add_issue(line_number, "BARRA_ID vazio")
                continue
            if segments.bars.index_for_id(root_bar_id) is None:
                add_issue(line_number, f"barra inicial inexistente: {root_bar_id}")
                continue

            seen_ids.add(circuit_id)
            definitions.append(
                CircuitDefinition(
                    circuit_id=circuit_id,
                    root_bar_id=root_bar_id,
                    code=values["CODIGO"],
                    nominal_voltage=values["VNOM"],
                )
            )

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not definitions:
        raise CsvImportError("Nenhum circuito válido foi encontrado no arquivo.")
    try:
        model = CircuitCatalogModel.build(
            segments,
            switches,
            definitions,
            source_path=str(path.resolve()),
            cancel_check=(None if cancel_event is None else cancel_event.is_set),
        )
    except InterruptedError as exc:
        raise CsvImportCancelled("Importação cancelada.") from exc
    if progress is not None:
        progress(total_rows, total_bytes, total_bytes)
    return CircuitLoadResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=len(definitions),
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
    )


def load_circuits_csv(
    path: str | os.PathLike[str],
    segments: LineNetworkModel,
    switches: SwitchModel | None = None,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> CircuitLoadResult:
    """Carrega circuitos e calcula suas associações na topologia informada."""

    if switches is not None and switches.segments is not segments:
        raise ValueError("As chaves devem pertencer aos trechos informados.")
    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(
            csv_path,
            segments,
            switches,
            "utf-8-sig",
            cancel_event,
            progress,
        )
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(
            csv_path,
            segments,
            switches,
            "cp1252",
            cancel_event,
            progress,
        )
