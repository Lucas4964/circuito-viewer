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
_BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"

# Envelope de um CRS UTM: easting fica entre 100 km e 900 km do falso leste e o
# northing entre 0 e 10.000 km. Coordenadas fora disso não são metros e fazem a
# projeção geográfica saturar (o ponto vai parar no oceano e a transformação
# deixa de ser invertível), o que desalinha completamente a camada de satélite.
UTM_EASTING_RANGE = (100_000.0, 900_000.0)
UTM_NORTHING_RANGE = (0.0, 10_000_000.0)

# Bases de dados de concessionárias costumam guardar as coordenadas em inteiros
# de decímetros ou centímetros; o divisor traz tudo de volta para metros.
COORDINATE_UNITS: tuple[tuple[float, str], ...] = (
    (1.0, "Metros"),
    (10.0, "Decímetros"),
    (100.0, "Centímetros"),
    (1000.0, "Milímetros"),
)
DEFAULT_SCALE_SAMPLE_SIZE = 5_000

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
    applied_scale: float = 1.0
    crs_warning: str | None = None

    @property
    def has_warnings(self) -> bool:
        return (
            self.invalid_rows > 0
            or self.encoding.lower() == "cp1252"
            or self.crs_warning is not None
        )


def _within(value_range: tuple[float, float], envelope: tuple[float, float]) -> bool:
    return envelope[0] <= value_range[0] and value_range[1] <= envelope[1]


def _fits_utm(
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    factor: float,
) -> bool:
    scaled_x = (x_range[0] / factor, x_range[1] / factor)
    scaled_y = (y_range[0] / factor, y_range[1] / factor)
    return _within(scaled_x, UTM_EASTING_RANGE) and _within(scaled_y, UTM_NORTHING_RANGE)


def utm_range_warning(
    x_range: tuple[float, float],
    y_range: tuple[float, float],
) -> str | None:
    """Descreve o desvio quando as coordenadas não são metros UTM plausíveis."""

    if _fits_utm(x_range, y_range, 1.0):
        return None
    problems: list[str] = []
    if not _within(x_range, UTM_EASTING_RANGE):
        problems.append(
            f"easting entre {x_range[0]:,.0f} e {x_range[1]:,.0f}".replace(",", ".")
        )
    if not _within(y_range, UTM_NORTHING_RANGE):
        problems.append(
            f"northing entre {y_range[0]:,.0f} e {y_range[1]:,.0f}".replace(",", ".")
        )
    return (
        "Coordenadas fora da faixa UTM válida (" + "; ".join(problems) + "). "
        "A imagem de satélite não será posicionada corretamente; confira a "
        "unidade das coordenadas e a zona informada."
    )


def detect_coordinate_scale(
    path: str | os.PathLike[str],
    *,
    sample_size: int = DEFAULT_SCALE_SAMPLE_SIZE,
) -> float:
    """Deduz o divisor que leva as coordenadas do arquivo para metros UTM.

    Lê apenas uma amostra do início do arquivo — em CSVs de centenas de milhares
    de linhas, uma passada completa antes do diálogo seria puro desperdício.
    Devolve o MENOR divisor de ``COORDINATE_UNITS`` que coloca as duas faixas
    dentro do envelope UTM.

    Volta a ``1.0`` quando o arquivo não pôde ser amostrado ou quando nenhuma
    unidade conhecida encaixa: nesses casos as coordenadas seguem como estão no
    arquivo e o relatório de importação denuncia o desvio pelo ``crs_warning``.
    """

    if sample_size <= 0:
        raise ValueError("A amostra deve conter ao menos uma linha.")
    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")

    default_factor = COORDINATE_UNITS[0][0]
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with csv_path.open("r", encoding=encoding, newline="") as source:
                reader = csv.reader(source, delimiter=";")
                try:
                    raw_header = next(reader)
                except StopIteration:
                    return default_factor
                header = tuple(
                    value.strip().lstrip(_BOM) for value in raw_header
                )
                try:
                    x_position = header.index("X")
                    y_position = header.index("Y")
                except ValueError:
                    return default_factor
                last_position = max(x_position, y_position)

                x_values: list[float] = []
                y_values: list[float] = []
                for row in reader:
                    if len(x_values) >= sample_size:
                        break
                    if not row or len(row) <= last_position:
                        continue
                    try:
                        x = _parse_coordinate(row[x_position])
                        y = _parse_coordinate(row[y_position])
                    except ValueError:
                        continue
                    x_values.append(x)
                    y_values.append(y)
        except UnicodeDecodeError:
            continue
        break
    else:
        return default_factor

    if not x_values:
        return default_factor

    x_range = (min(x_values), max(x_values))
    y_range = (min(y_values), max(y_values))
    for factor, _label in COORDINATE_UNITS:
        if _fits_utm(x_range, y_range, factor):
            return factor
    return default_factor


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
    scale: float = 1.0,
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
                x = _parse_coordinate(raw_x) / scale
                y = _parse_coordinate(raw_y) / scale
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
    bounds = model.bounds
    return CsvLoadResult(
        model=model,
        encoding=encoding,
        total_rows=total_rows,
        valid_rows=len(bar_ids),
        invalid_rows=invalid_rows,
        issues=tuple(issues),
        omitted_issues=max(0, invalid_rows - len(issues)),
        applied_scale=scale,
        crs_warning=utm_range_warning(
            (bounds.left, bounds.right),
            (bounds.top, bounds.bottom),
        ),
    )


def load_csv(
    path: str | os.PathLike[str],
    crs: UtmCrs,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    scale: float = 1.0,
) -> CsvLoadResult:
    """Carrega um CSV, tentando UTF-8 com BOM e depois CP-1252.

    ``scale`` é o divisor que leva as coordenadas do arquivo para METROS — a
    unidade canônica do modelo, a mesma de ``COMPR``. Bases que guardam X e Y em
    decímetros usam 10. Sem essa normalização, a projeção geográfica satura e a
    camada de satélite é posicionada a milhares de quilômetros da rede.

    O chamador recebe um modelo completo ou uma exceção; nenhum estado externo é
    alterado durante o processamento.
    """

    if not math.isfinite(scale) or scale <= 0:
        raise CsvImportError("A escala das coordenadas deve ser finita e positiva.")

    csv_path = Path(path)
    if not csv_path.is_file():
        raise CsvImportError(f"Arquivo não encontrado: {csv_path}")

    try:
        return _parse_file(csv_path, crs, "utf-8-sig", cancel_event, progress, scale)
    except UnicodeDecodeError:
        if _cancelled(cancel_event):
            raise CsvImportCancelled("Importação cancelada.")
        return _parse_file(csv_path, crs, "cp1252", cancel_event, progress, scale)
