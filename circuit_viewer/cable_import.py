"""Importação transacional do catálogo de cabos.

Diferente dos demais importadores, este não recebe modelo de dependência: o
catálogo de cabos é uma raiz independente. Os trechos apenas guardam
`CABOF_ID`/`CABON_ID` como texto, sem exigir que o cabo exista.

O arquivo de origem traz, para o mesmo `CABO_ID`, um registro por `TIPO`
(domínio `{1, 2}`); só o `TIPO=1` representa o cabo no catálogo. O filtro roda
antes da checagem de duplicidade, então o registro `TIPO=2` nunca colide com o
`TIPO=1` do mesmo `CABO_ID` — não é um erro de dados, é a regra de negócio.
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
from .model import CableModel


EXPECTED_CABLE_HEADER = (
    "CABO_ID",
    "TIPO",
    "CODIGO",
    "IADM",
    "GMR",
    "R",
    "X",
    "QCAP",
    "R0",
    "X0",
    "R1",
    "X1",
    "NOME",
    "EXTERN_ID",
)
MAX_REPORTED_ISSUES = 200
REQUIRED_CABLE_TYPE = "1"
ProgressCallback = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class CableIssue:
    line_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class CableCsvResult:
    model: CableModel
    encoding: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    ignored_type_rows: int
    issues: tuple[CableIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_rows > 0 or self.encoding.lower() == "cp1252"


def _column_positions(header: tuple[str, ...]) -> dict[str, int]:
    positions: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for required_name in EXPECTED_CABLE_HEADER:
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


def parse_cable_rows(
    raw_header: Iterable[str],
    rows: Iterable[TextRow],
    *,
    source_label: str,
    encoding: str,
    first_line_number: int = 2,
    cancel_event: threading.Event | None = None,
    progress: RowProgress | None = None,
) -> CableCsvResult:
    """Valida linhas de cabos já em texto e devolve o catálogo.

    Toda a validação vive aqui, independente da fonte: o CSV e o banco Access
    apenas entregam cabeçalho e linhas de texto. O filtro por ``TIPO=1`` vale
    igualmente nos dois casos, porque é regra de negócio e não do arquivo.
    """

    columns: dict[str, list[str]] = {name: [] for name in EXPECTED_CABLE_HEADER}
    seen_cable_ids: set[str] = set()
    issues: list[CableIssue] = []
    total_rows = 0
    invalid_rows = 0
    ignored_type_rows = 0

    def add_issue(line_number: int, reason: str) -> None:
        nonlocal invalid_rows
        invalid_rows += 1
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(CableIssue(line_number, reason))

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
        if values["TIPO"] != REQUIRED_CABLE_TYPE:
            # Não é um erro de dados: o arquivo traz um registro por TIPO
            # para o mesmo CABO_ID, e só o TIPO=1 representa o cabo.
            ignored_type_rows += 1
            continue

        cable_id = values["CABO_ID"]
        if not cable_id:
            add_issue(line_number, "CABO_ID vazio")
            continue
        if cable_id in seen_cable_ids:
            add_issue(line_number, f"CABO_ID duplicado: {cable_id}")
            continue

        seen_cable_ids.add(cable_id)
        for name in EXPECTED_CABLE_HEADER:
            columns[name].append(values[name])

    if cancel_event is not None and cancel_event.is_set():
        raise CsvImportCancelled("Importação cancelada.")
    if not columns["CABO_ID"]:
        raise CsvImportError("Nenhum cabo válido foi encontrado no arquivo.")
    if progress is not None:
        progress(total_rows)

    # Explícito de propósito: reordenar EXPECTED_CABLE_HEADER não pode embaralhar
    # silenciosamente as colunas do modelo.
    model = CableModel(
        columns["CABO_ID"],
        columns["TIPO"],
        columns["CODIGO"],
        columns["IADM"],
        columns["GMR"],
        columns["R"],
        columns["X"],
        columns["QCAP"],
        columns["R0"],
        columns["X0"],
        columns["R1"],
        columns["X1"],
        columns["NOME"],
        columns["EXTERN_ID"],
        source_path=source_label,
    )
    return CableCsvResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=len(columns["CABO_ID"]),
        invalid_rows=invalid_rows,
        ignored_type_rows=ignored_type_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
    )


def _parse_file(
    path: Path,
    encoding: str,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> CableCsvResult:
    total_bytes = max(path.stat().st_size, 1)
    with path.open("r", encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=";")
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvImportError("O arquivo CSV de cabos está vazio.") from exc
        return parse_cable_rows(
            raw_header,
            reader,
            source_label=str(path.resolve()),
            encoding=encoding,
            cancel_event=cancel_event,
            progress=byte_progress(source, total_bytes, progress),
        )


def load_cables_csv(
    path: str | os.PathLike[str],
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> CableCsvResult:
    """Carrega o catálogo de cabos preservando todos os campos como texto."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")
    try:
        return _parse_file(csv_path, "utf-8-sig", cancel_event, progress)
    except UnicodeDecodeError:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, "cp1252", cancel_event, progress)
