"""Importa correntes de cabeça por circuito e patamar para ``AllocateLoads``."""

from __future__ import annotations

import csv
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .csv_import import (
    CsvImportCancelled,
    CsvImportError,
    ProgressCallback,
    byte_progress,
    normalize_header,
)
from .model import CircuitCatalogModel
from .opendss_export import parse_number


EXPECTED_ALLOCATION_MEASUREMENT_HEADER = (
    "CODIGO",
    "NPAT",
    "ID",
    "IE",
    "IF",
)


@dataclass(frozen=True, slots=True)
class AllocationMeasurementRecord:
    circuit_id: str
    npat: int
    currents: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class AllocationMeasurementModel:
    circuits: CircuitCatalogModel
    _records: tuple[tuple[AllocationMeasurementRecord, ...] | None, ...]
    source_path: str | None = None

    def __post_init__(self) -> None:
        if len(self._records) != len(self.circuits):
            raise ValueError("Cada circuito deve possuir uma posição de medições.")
        for circuit_index, group in enumerate(self._records):
            if group is None:
                continue
            circuit_id = self.circuits.definition(circuit_index).circuit_id
            if (
                len(group) != 4
                or tuple(item.npat for item in group) != (0, 1, 2, 3)
                or any(item.circuit_id != circuit_id for item in group)
            ):
                raise ValueError("Cada grupo de medições deve conter NPAT 0 a 3.")

    def records_for_circuit(
        self, circuit_index: int
    ) -> tuple[AllocationMeasurementRecord, ...]:
        if not 0 <= int(circuit_index) < len(self.circuits):
            raise IndexError(circuit_index)
        values = self._records[int(circuit_index)]
        return () if values is None else values

    @property
    def available_indices(self) -> tuple[int, ...]:
        return tuple(index for index, group in enumerate(self._records) if group)


@dataclass(frozen=True, slots=True)
class AllocationMeasurementCsvResult:
    model: AllocationMeasurementModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int = 0
    issues: tuple[str, ...] = ()

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or bool(self.issues)


def _column_positions(header: Sequence[str]) -> dict[str, int]:
    normalized = normalize_header(header)
    missing = [
        name for name in EXPECTED_ALLOCATION_MEASUREMENT_HEADER if name not in normalized
    ]
    duplicated = [
        name
        for name in EXPECTED_ALLOCATION_MEASUREMENT_HEADER
        if normalized.count(name) > 1
    ]
    if missing or duplicated:
        details: list[str] = []
        if missing:
            details.append("ausentes: " + ", ".join(missing))
        if duplicated:
            details.append("duplicadas: " + ", ".join(duplicated))
        raise CsvImportError(
            "Cabeçalho inválido das correntes de alocação; "
            + "; ".join(details)
            + ". Cabeçalho esperado, separado por ponto e vírgula: "
            + ";".join(EXPECTED_ALLOCATION_MEASUREMENT_HEADER)
            + ". Exemplo: 004011;0;120.5;98.2;101.7."
        )
    return {
        name: normalized.index(name)
        for name in EXPECTED_ALLOCATION_MEASUREMENT_HEADER
    }


def parse_allocation_measurement_rows(
    raw_header: Sequence[str],
    rows: Iterable[Sequence[str]],
    circuits: CircuitCatalogModel,
    *,
    source_label: str,
    encoding: str,
    first_line_number: int = 2,
    cancel_event: threading.Event | None = None,
    progress: Callable[[int], None] | None = None,
) -> AllocationMeasurementCsvResult:
    """Valida grupos estritos e preserva circuitos completos independentes.

    Erros associados a um ``CODIGO`` recusam somente aquele grupo. Linhas sem
    circuito identificável são globais e recusam o arquivo inteiro.
    """

    positions = _column_positions(raw_header)
    last_required = max(positions.values())
    indices_by_code: dict[str, list[int]] = {}
    for circuit_index, definition in enumerate(circuits.definitions):
        code = definition.code.strip()
        if code:
            indices_by_code.setdefault(code, []).append(circuit_index)
    groups: dict[int, dict[int, AllocationMeasurementRecord]] = {}
    errors: list[str] = []
    global_errors: list[str] = []
    invalid_circuits: set[int] = set()
    total_rows = 0

    for line_number, row in enumerate(rows, start=first_line_number):
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        if not row or not any(str(value).strip() for value in row):
            continue
        total_rows += 1
        if progress is not None and total_rows % 1_000 == 0:
            progress(total_rows)
        if len(row) <= last_required:
            global_errors.append(
                f"linha {line_number}: faltam colunas obrigatórias"
            )
            continue
        values = {
            name: str(row[index]).strip() for name, index in positions.items()
        }
        circuit_code = values["CODIGO"]
        matching_indices = indices_by_code.get(circuit_code, ())
        if not circuit_code:
            global_errors.append(
                f"linha {line_number}: CODIGO do circuito vazio"
            )
            continue
        if not matching_indices:
            global_errors.append(
                f"linha {line_number}: CODIGO de circuito inexistente: "
                f"{circuit_code}"
            )
            continue
        if len(matching_indices) > 1:
            global_errors.append(
                f"linha {line_number}: CODIGO de circuito ambíguo: "
                f"{circuit_code} aparece {len(matching_indices)} vezes no catálogo"
            )
            continue
        circuit_index = matching_indices[0]
        circuit_id = circuits.definition(circuit_index).circuit_id
        try:
            npat = int(values["NPAT"])
        except ValueError:
            errors.append(
                f"circuito {circuit_code}, linha {line_number}: "
                "NPAT deve ser inteiro de 0 a 3"
            )
            invalid_circuits.add(circuit_index)
            continue
        if npat not in {0, 1, 2, 3}:
            errors.append(
                f"circuito {circuit_code}, linha {line_number}: "
                "NPAT deve estar entre 0 e 3"
            )
            invalid_circuits.add(circuit_index)
            continue
        parsed = tuple(parse_number(values[name]) for name in ("ID", "IE", "IF"))
        if any(value is None or value < 0.0 for value in parsed):
            errors.append(
                f"circuito {circuit_code}, linha {line_number}: "
                "ID, IE e IF devem ser números não negativos"
            )
            invalid_circuits.add(circuit_index)
            continue
        currents = (float(parsed[0]), float(parsed[1]), float(parsed[2]))
        by_npat = groups.setdefault(circuit_index, {})
        if npat in by_npat:
            errors.append(
                f"circuito {circuit_code}, linha {line_number}: "
                f"NPAT {npat} duplicado"
            )
            invalid_circuits.add(circuit_index)
            continue
        by_npat[npat] = AllocationMeasurementRecord(
            circuit_id,
            npat,
            currents,
        )

    for circuit_index, group in groups.items():
        missing = sorted({0, 1, 2, 3}.difference(group))
        if missing:
            definition = circuits.definition(circuit_index)
            circuit_label = definition.code.strip() or definition.circuit_id
            errors.append(
                f"circuito {circuit_label}: NPAT ausentes: "
                + ", ".join(str(value) for value in missing)
            )
            invalid_circuits.add(circuit_index)
    valid_groups = {
        circuit_index: group
        for circuit_index, group in groups.items()
        if circuit_index not in invalid_circuits and len(group) == 4
    }
    if not groups and not errors and not global_errors:
        global_errors.append("o arquivo não contém medições")
    fatal_errors = global_errors or ([] if valid_groups else errors)
    if fatal_errors:
        preview = "\n• ".join(fatal_errors[:20])
        suffix = (
            ""
            if len(fatal_errors) <= 20
            else f"\n… e mais {len(fatal_errors) - 20} erro(s)."
        )
        raise CsvImportError(
            "As correntes de alocação são inválidas:\n• " + preview + suffix
        )

    dense: list[tuple[AllocationMeasurementRecord, ...] | None] = [
        None
    ] * len(circuits)
    for circuit_index, group in valid_groups.items():
        dense[circuit_index] = tuple(group[npat] for npat in range(4))
    if progress is not None:
        progress(total_rows)
    return AllocationMeasurementCsvResult(
        AllocationMeasurementModel(circuits, tuple(dense), source_label),
        encoding,
        total_rows,
        len(valid_groups) * 4,
        total_rows - len(valid_groups) * 4,
        tuple(errors),
    )


def _parse_file(
    path: Path,
    circuits: CircuitCatalogModel,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> AllocationMeasurementCsvResult:
    total_bytes = max(path.stat().st_size, 1)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo de correntes está vazio.") from exc
        return parse_allocation_measurement_rows(
            header,
            reader,
            circuits,
            source_label=str(path.resolve()),
            encoding=encoding,
            cancel_event=cancel_event,
            progress=byte_progress(source, total_bytes, progress),
        )


def load_allocation_measurements_csv(
    path: str | os.PathLike[str],
    circuits: CircuitCatalogModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> AllocationMeasurementCsvResult:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, circuits, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, circuits, "cp1252", cancel_event, progress)


__all__ = [
    "AllocationMeasurementCsvResult",
    "AllocationMeasurementModel",
    "AllocationMeasurementRecord",
    "EXPECTED_ALLOCATION_MEASUREMENT_HEADER",
    "load_allocation_measurements_csv",
    "parse_allocation_measurement_rows",
]
