"""Importação transacional de geradores de MT, independente da fonte."""

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
    normalize_header,
)
from .model import GeneratorModel, LoadModel


GENERATOR_HEADER = (
    "GERADOR_ID",
    "MT_CONS_ID",
    "CODIGO",
    "VNOM",
    "SNOM",
    "LIGACAO",
    "CURVA_ID",
    "GERACAO_KWH",
)
CONSUMER_HEADER = ("ID", "CARGA_ID", "CODIGO", "EXTERN_ID", "NOME", "FASES2")
MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int, int], None]
StageCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class GeneratorIssue:
    source: str
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class GeneratorCsvResult:
    model: GeneratorModel
    generator_encoding: str
    consumer_encoding: str
    generator_total_rows: int
    consumer_total_rows: int
    valid_rows: int
    invalid_rows: int
    issues: tuple[GeneratorIssue, ...]
    omitted_issues: int

    @property
    def total_rows(self) -> int:
        """Quantidade de linhas da tabela principal, para relatórios genéricos."""

        return self.generator_total_rows

    @property
    def has_warnings(self) -> bool:
        return (
            self.invalid_rows > 0
            or self.generator_encoding.lower() == "cp1252"
            or self.consumer_encoding.lower() == "cp1252"
        )


@dataclass(frozen=True, slots=True)
class _ConsumerSource:
    rows_by_code: dict[str, tuple[str, str, str, str, str, str]]
    ambiguous_codes: frozenset[str]
    total_rows: int


def _column_positions(
    raw_header: Iterable[str], required: tuple[str, ...], source_name: str
) -> dict[str, int]:
    header = normalize_header(raw_header)
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for name in required:
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
            f"Cabeçalho inválido em {source_name}; colunas obrigatórias "
            + "; ".join(details)
            + "."
        )
    return positions


def _parse_consumer_rows(
    raw_header: Iterable[str],
    rows: Iterable[TextRow],
    *,
    source_label: str,
    first_line_number: int,
    cancel_event: threading.Event | None,
    progress: RowProgress | None,
) -> _ConsumerSource:
    positions = _column_positions(raw_header, CONSUMER_HEADER, source_label)
    last_position = max(positions.values())
    rows_by_code: dict[str, tuple[str, str, str, str, str, str]] = {}
    ambiguous: set[str] = set()
    total_rows = 0
    for _line_number, row in enumerate(rows, start=first_line_number):
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        if not row or not any(value.strip() for value in row):
            continue
        total_rows += 1
        if progress is not None and total_rows % PROGRESS_ROW_INTERVAL == 0:
            progress(total_rows)
        if len(row) <= last_position:
            continue
        values = tuple(row[positions[name]].strip() for name in CONSUMER_HEADER)
        code = values[2]
        if not code:
            continue
        if code in rows_by_code:
            ambiguous.add(code)
            rows_by_code.pop(code, None)
        elif code not in ambiguous:
            rows_by_code[code] = values
    if progress is not None:
        progress(total_rows)
    return _ConsumerSource(rows_by_code, frozenset(ambiguous), total_rows)


def parse_generator_rows(
    raw_generator_header: Iterable[str],
    generator_rows: Iterable[TextRow],
    raw_consumer_header: Iterable[str],
    consumer_rows: Iterable[TextRow],
    loads: LoadModel,
    *,
    generator_source_label: str,
    consumer_source_label: str,
    generator_encoding: str,
    consumer_encoding: str,
    generator_first_line_number: int = 2,
    consumer_first_line_number: int = 2,
    cancel_event: threading.Event | None = None,
    generator_progress: RowProgress | None = None,
    consumer_progress: RowProgress | None = None,
    stage: StageCallback | None = None,
) -> GeneratorCsvResult:
    """Associa as duas fontes por ``CODIGO`` e resolve a barra pela carga.

    CSV e MDB entregam apenas cabeçalhos e iteradores de texto. Toda validação,
    diagnóstico e construção do modelo permanece concentrada nesta função.
    """

    if stage is not None:
        stage("MT_CONS")
    consumers = _parse_consumer_rows(
        raw_consumer_header,
        consumer_rows,
        source_label=consumer_source_label,
        first_line_number=consumer_first_line_number,
        cancel_event=cancel_event,
        progress=consumer_progress,
    )
    if stage is not None:
        stage("MT_GERADOR_CONS")

    positions = _column_positions(
        raw_generator_header, GENERATOR_HEADER, generator_source_label
    )
    last_position = max(positions.values())
    columns = {name: [] for name in GENERATOR_HEADER}
    consumer_columns = {name: [] for name in CONSUMER_HEADER}
    load_indices: list[int] = []
    seen_ids: set[str] = set()
    issues: list[GeneratorIssue] = []
    invalid_rows = 0
    total_rows = 0

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(
                GeneratorIssue(generator_source_label, line_number, reason)
            )

    for line_number, row in enumerate(
        generator_rows, start=generator_first_line_number
    ):
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        if not row or not any(value.strip() for value in row):
            continue
        total_rows += 1
        if (
            generator_progress is not None
            and total_rows % PROGRESS_ROW_INTERVAL == 0
        ):
            generator_progress(total_rows)
        if len(row) <= last_position:
            add_issue(line_number, "faltam valores em colunas obrigatórias")
            continue
        values = {name: row[positions[name]].strip() for name in GENERATOR_HEADER}
        generator_id = values["GERADOR_ID"]
        if not generator_id:
            add_issue(line_number, "GERADOR_ID vazio")
            continue
        if generator_id in seen_ids:
            add_issue(line_number, f"GERADOR_ID duplicado: {generator_id}")
            continue
        code = values["CODIGO"]
        if not code:
            add_issue(line_number, "CODIGO vazio")
            continue
        if code in consumers.ambiguous_codes:
            add_issue(line_number, f"CODIGO ambíguo em MT_CONS: {code}")
            continue
        consumer = consumers.rows_by_code.get(code)
        if consumer is None:
            add_issue(line_number, f"CODIGO inexistente em MT_CONS: {code}")
            continue
        load_id = consumer[1]
        load_index = loads.index_for_id(load_id)
        if not load_id or load_index is None:
            add_issue(line_number, f"carga inexistente: {load_id or '<vazio>'}")
            continue
        seen_ids.add(generator_id)
        load_indices.append(load_index)
        for name in GENERATOR_HEADER:
            columns[name].append(values[name])
        for name, value in zip(CONSUMER_HEADER, consumer, strict=True):
            consumer_columns[name].append(value)
    if generator_progress is not None:
        generator_progress(total_rows)

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not columns["GERADOR_ID"]:
        raise CsvImportError("Nenhum gerador válido foi encontrado nas fontes.")
    model = GeneratorModel(
        loads,
        columns["GERADOR_ID"],
        load_indices,
        columns["MT_CONS_ID"],
        columns["CODIGO"],
        columns["VNOM"],
        columns["SNOM"],
        columns["LIGACAO"],
        columns["CURVA_ID"],
        columns["GERACAO_KWH"],
        consumer_columns["ID"],
        consumer_columns["CODIGO"],
        consumer_columns["EXTERN_ID"],
        consumer_columns["NOME"],
        consumer_columns["FASES2"],
        source_paths=(generator_source_label, consumer_source_label),
    )
    return GeneratorCsvResult(
        model=model,
        generator_encoding=generator_encoding,
        consumer_encoding=consumer_encoding,
        generator_total_rows=total_rows,
        consumer_total_rows=consumers.total_rows,
        valid_rows=len(model),
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
    )


def _detect_encoding(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            while source.read(1_048_576):
                pass
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8-sig"


def _progress_emitter(source, offset: int, total_bytes: int, progress):  # noqa: ANN001
    if progress is None:
        return None

    def emit(rows: int) -> None:
        try:
            position = source.buffer.tell()
        except Exception:  # noqa: BLE001
            position = total_bytes - offset
        progress(rows, min(total_bytes, offset + int(position)), total_bytes)

    return emit


def load_generators_csv(
    generator_path: str | os.PathLike[str],
    consumer_path: str | os.PathLike[str],
    loads: LoadModel,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    stage: StageCallback | None = None,
) -> GeneratorCsvResult:
    """Adapta dois arquivos CSV ao parser compartilhado de geradores."""

    generator_csv = Path(generator_path)
    consumer_csv = Path(consumer_path)
    for path in (generator_csv, consumer_csv):
        if not path.is_file():
            raise CsvImportError(f"Arquivo não encontrado: {path}")
    generator_encoding = _detect_encoding(generator_csv)
    consumer_encoding = _detect_encoding(consumer_csv)
    generator_size = max(generator_csv.stat().st_size, 1)
    consumer_size = max(consumer_csv.stat().st_size, 1)
    total_bytes = generator_size + consumer_size

    with consumer_csv.open(
        "r", encoding=consumer_encoding, newline=""
    ) as consumer_source, generator_csv.open(
        "r", encoding=generator_encoding, newline=""
    ) as generator_source:
        consumer_reader = csv.reader(consumer_source, delimiter=";")
        generator_reader = csv.reader(generator_source, delimiter=";")
        try:
            consumer_header = next(consumer_reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo MT_CONS está vazio.") from exc
        try:
            generator_header = next(generator_reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo MT_GERADOR_CONS está vazio.") from exc
        return parse_generator_rows(
            generator_header,
            generator_reader,
            consumer_header,
            consumer_reader,
            loads,
            generator_source_label=str(generator_csv.resolve()),
            consumer_source_label=str(consumer_csv.resolve()),
            generator_encoding=generator_encoding,
            consumer_encoding=consumer_encoding,
            cancel_event=cancel_event,
            generator_progress=_progress_emitter(
                generator_source, consumer_size, total_bytes, progress
            ),
            consumer_progress=_progress_emitter(
                consumer_source, 0, total_bytes, progress
            ),
            stage=stage,
        )
