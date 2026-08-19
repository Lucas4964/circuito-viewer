"""Importação encadeada das dez entidades lógicas a partir de um banco Access.

Este módulo não valida nada por conta própria: ele resolve o mapeamento, lê as
linhas já convertidas em texto e as entrega às funções ``parse_*_rows`` dos
importadores de CSV. É o que garante que importar por banco e importar por CSV
produzam exatamente o mesmo modelo, com os mesmos diagnósticos.

O banco chega **por parâmetro**, como o motor do OpenDSS chega a
``opendss_powerflow``: assim a orquestração é exercitada headless com um banco
falso, sem ``pyodbc`` nem driver ODBC instalados.

A ordem de :data:`~circuit_viewer.mdb_mapping.ENTITY_ORDER` é obrigatória, não
estética — cada importador recebe o modelo do anterior, e os circuitos precisam
das chaves para calcular a topologia energizada.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from itertools import islice
from typing import Any, Callable, Iterator, Sequence

from .allocation import (
    BT_CONSUMER_DIAGNOSTIC_HEADER,
    BT_CONSUMER_HEADER,
    BT_ET_HEADER,
    BT_GENERATOR_HEADER,
    MT_CONSUMER_ENERGY_HEADER,
    MT_GENERATOR_ENERGY_HEADER,
    AllocationTable,
    TransformerAllocationModel,
    build_transformer_allocations,
)
from .cable_import import CableCsvResult, parse_cable_rows
from .capacitor_import import CapacitorCsvResult, parse_capacitor_rows
from .circuit_import import CircuitLoadResult, parse_circuit_rows
from .circuit_level_import import CircuitLevelCsvResult, parse_circuit_level_rows
from .csv_import import (
    DEFAULT_SCALE_SAMPLE_SIZE,
    CsvImportCancelled,
    CsvImportError,
    CsvLoadResult,
    ProgressCallback,
    parse_bar_rows,
    scale_from_ranges,
)
from .generator_import import GeneratorCsvResult, parse_generator_rows
from .load_import import LoadCsvResult, parse_load_rows
from .load_pattern_import import LoadPatternCsvResult, parse_load_pattern_rows
from .mdb_engine import AccessDatabase
from .mdb_mapping import (
    ENTITY_LABELS,
    ENTITY_ORDER,
    GENERATOR_CONSUMER_ENTITY,
    EntityMapping,
    ResolvedEntity,
    ResolvedMapping,
    resolve_mapping,
)
from .model import UtmCrs
from .phase_config import PhaseConfiguration
from .regulator_import import RegulatorLoadResult, parse_regulator_rows
from .segment_import import SegmentLoadResult, parse_segment_rows
from .switch_import import SwitchLoadResult, parse_switch_rows


# Os ``*LoadResult`` carregam a codificação do arquivo de origem; num banco não
# existe essa noção, e o texto aparece no relatório de importação. "ODBC" é
# honesto e, principalmente, não é "cp1252" — que dispararia ``has_warnings``.
MDB_ENCODING = "ODBC"

# A primeira linha de dados de uma tabela é a linha 1: não há cabeçalho ocupando
# a linha 1 como no CSV, onde os dados começam na 2.
FIRST_ROW_NUMBER = 1

# Dependências entre entidades: uma entidade só é tentada se todas as suas
# fontes tiverem sido importadas com sucesso.
ENTITY_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "barras": (),
    "cabos": (),
    "trechos": ("barras",),
    "cargas": ("barras",),
    "capacitores": ("barras",),
    "geradores": ("cargas",),
    "patamares": ("cargas",),
    "chaves": ("trechos",),
    "reguladores": ("trechos",),
    "circuitos": ("trechos",),
    "patamares_circuitos": ("circuitos",),
}


@dataclass(frozen=True, slots=True)
class MdbEntityOutcome:
    """O que aconteceu com uma entidade: importada, ou o motivo de não ter sido."""

    entity: str
    table: str | None = None
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    error: str | None = None

    @property
    def label(self) -> str:
        return ENTITY_LABELS.get(self.entity, self.entity)

    @property
    def imported(self) -> bool:
        return self.error is None and self.table is not None


@dataclass(frozen=True, slots=True)
class MdbImportResult:
    """Modelos importados de um banco, na ordem de dependência."""

    source_path: str
    bars: CsvLoadResult
    cables: CableCsvResult | None = None
    segments: SegmentLoadResult | None = None
    loads: LoadCsvResult | None = None
    capacitors: CapacitorCsvResult | None = None
    generators: GeneratorCsvResult | None = None
    patterns: LoadPatternCsvResult | None = None
    switches: SwitchLoadResult | None = None
    regulators: RegulatorLoadResult | None = None
    circuits: CircuitLoadResult | None = None
    circuit_levels: CircuitLevelCsvResult | None = None
    allocations: TransformerAllocationModel | None = None
    allocation_error: str | None = None
    outcomes: tuple[MdbEntityOutcome, ...] = ()
    applied_scale: float = 1.0

    def outcome_for(self, entity: str) -> MdbEntityOutcome | None:
        for item in self.outcomes:
            if item.entity == entity:
                return item
        return None

    @property
    def imported_entities(self) -> tuple[str, ...]:
        return tuple(item.entity for item in self.outcomes if item.imported)

    @property
    def failures(self) -> tuple[MdbEntityOutcome, ...]:
        return tuple(item for item in self.outcomes if item.error is not None)

    @property
    def has_warnings(self) -> bool:
        """``True`` quando o relatório precisa aparecer em diálogo.

        Uma entidade ausente conta como aviso: o usuário pediu o banco inteiro e
        precisa saber o que não veio.
        """

        if (
            self.failures
            or self.allocation_error is not None
            or (self.allocations is not None and bool(self.allocations.issues))
        ):
            return True
        results = (
            self.bars,
            self.cables,
            self.segments,
            self.loads,
            self.capacitors,
            self.generators,
            self.patterns,
            self.switches,
            self.regulators,
            self.circuits,
            self.circuit_levels,
        )
        return any(item is not None and item.has_warnings for item in results)


def source_label(source_path: str, table: str) -> str:
    """Rótulo gravado em ``source_path`` dos modelos vindos de um banco.

    Preserva a tabela de origem — sem ela, os modelos apontariam para o mesmo
    arquivo e o painel não diria de onde cada um veio.
    """

    return f"{source_path}::{table}"


def _rows(
    database: AccessDatabase,
    entity: ResolvedEntity,
) -> Iterator[tuple[str, ...]]:
    return database.iter_rows(entity.table, entity.columns)


def detect_database_scale(
    database: AccessDatabase,
    mapping: ResolvedMapping,
    *,
    sample_size: int = DEFAULT_SCALE_SAMPLE_SIZE,
) -> float:
    """Deduz o divisor que leva X e Y do banco para metros UTM.

    Espelha :func:`circuit_viewer.csv_import.detect_coordinate_scale`: amostra o
    início da tabela de barras e delega a decisão a ``scale_from_ranges``, que é
    a mesma para as duas fontes. Volta a ``1.0`` quando a amostra não serve —
    aí o diálogo abre em metros e o ``crs_warning`` denuncia o desvio.
    """

    bars = mapping.get("barras")
    if bars is None or sample_size <= 0:
        return 1.0
    positions = {name: index for index, name in enumerate(bars.header)}
    x_position = positions.get("X")
    y_position = positions.get("Y")
    if x_position is None or y_position is None:
        return 1.0

    x_values: list[float] = []
    y_values: list[float] = []
    rows = _rows(database, bars)
    try:
        for row in islice(rows, sample_size):
            try:
                x = float(row[x_position].strip().replace(",", "."))
                y = float(row[y_position].strip().replace(",", "."))
            except (ValueError, IndexError):
                continue
            x_values.append(x)
            y_values.append(y)
    except Exception:  # noqa: BLE001 — a dedução é conveniência, nunca fatal
        return 1.0
    finally:
        close = getattr(rows, "close", None)
        if close is not None:
            close()

    if not x_values:
        return 1.0
    return scale_from_ranges(
        (min(x_values), max(x_values)),
        (min(y_values), max(y_values)),
    )


class _ProgressTracker:
    """Converte o progresso por entidade num progresso único da cadeia.

    O total é a soma dos ``COUNT(*)`` das tabelas resolvidas, então a barra
    percorre a importação inteira uma vez só, em vez de reiniciar dez vezes.
    """

    def __init__(self, total_rows: int, progress: ProgressCallback | None) -> None:
        self._total = max(total_rows, 1)
        self._progress = progress
        self._done = 0

    def for_entity(self) -> Callable[[int], None] | None:
        if self._progress is None:
            return None
        base = self._done

        def emit(rows: int) -> None:
            current = min(base + rows, self._total)
            self._progress(current, current, self._total)

        return emit

    def finish_entity(self, rows: int) -> None:
        self._done = min(self._done + max(rows, 0), self._total)


def _count_rows(
    database: AccessDatabase,
    mapping: ResolvedMapping,
    wanted: set[str],
) -> int:
    total = 0
    for entity in ENTITY_ORDER:
        if entity not in wanted:
            continue
        targets = [mapping.get(entity)]
        if entity == "geradores":
            targets.append(mapping.get(GENERATOR_CONSUMER_ENTITY))
        for target in targets:
            if target is None:
                continue
            try:
                total += max(0, database.row_count(target.table))
            except Exception:  # noqa: BLE001 — o total é estimativa de barra
                continue
    return total


def load_database(
    database: AccessDatabase,
    crs: UtmCrs,
    *,
    source_path: str,
    mapping: Sequence[EntityMapping] | None = None,
    overrides: dict[str, str] | None = None,
    resolved: ResolvedMapping | None = None,
    entities: Sequence[str] | None = None,
    scale: float = 1.0,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    phase_configuration: PhaseConfiguration | None = None,
) -> MdbImportResult:
    """Importa as dez entidades lógicas de um banco, na ordem de dependência.

    ``entities`` restringe a importação ao que o usuário marcou no diálogo; o
    padrão é tudo o que o mapeamento resolveu.

    As barras são obrigatórias: sem elas não há o que desenhar, e a função
    levanta ``CsvImportError``. Qualquer outra entidade que falhe — tabela
    ausente, coluna faltando, nenhuma linha válida — vira uma ocorrência no
    relatório e **não interrompe as demais**, do mesmo modo que hoje importar
    chaves sem circuitos é um estado válido da aplicação.
    """

    plan = (
        resolve_mapping(database, mapping, overrides=overrides)
        if resolved is None
        else resolved
    )
    wanted = set(ENTITY_ORDER if entities is None else entities)
    tracker = _ProgressTracker(_count_rows(database, plan, wanted), progress)

    outcomes: list[MdbEntityOutcome] = []
    results: dict[str, Any] = {}

    def record(entity: str, table: str | None, result: Any, error: str | None) -> None:
        outcomes.append(
            MdbEntityOutcome(
                entity=entity,
                table=table,
                total_rows=getattr(result, "total_rows", 0),
                valid_rows=getattr(result, "valid_rows", 0),
                invalid_rows=getattr(result, "invalid_rows", 0),
                error=error,
            )
        )

    for entity in ENTITY_ORDER:
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")

        if entity not in wanted:
            record(entity, None, None, "Não selecionada para importação.")
            continue

        missing = [
            name
            for name in ENTITY_DEPENDENCIES[entity]
            if results.get(name) is None
        ]
        if missing:
            names = ", ".join(ENTITY_LABELS.get(name, name).lower() for name in missing)
            record(entity, None, None, f"Depende de {names}, que não foi importada.")
            continue

        target = plan.get(entity)
        consumer_target = (
            plan.get(GENERATOR_CONSUMER_ENTITY)
            if entity == "geradores"
            else None
        )
        if target is None or (entity == "geradores" and consumer_target is None):
            reasons = [plan.reason_for(entity)] if target is None else []
            if entity == "geradores" and consumer_target is None:
                reasons.append(plan.reason_for(GENERATOR_CONSUMER_ENTITY))
            record(
                entity,
                None,
                None,
                "; ".join(reason for reason in reasons if reason)
                or "Não encontrada.",
            )
            continue

        emit = tracker.for_entity()
        consumer_rows_seen = 0
        generator_rows_seen = 0

        def consumer_emit(rows: int) -> None:
            nonlocal consumer_rows_seen
            consumer_rows_seen = rows
            if emit is not None:
                emit(rows)

        def generator_emit(rows: int) -> None:
            nonlocal generator_rows_seen
            generator_rows_seen = rows
            if emit is not None:
                emit(consumer_rows_seen + rows)

        try:
            result = _import_entity(
                entity,
                database,
                target,
                crs,
                results,
                consumer_target=consumer_target,
                source_path=source_path,
                scale=scale,
                cancel_event=cancel_event,
                progress=generator_emit if entity == "geradores" else emit,
                consumer_progress=(
                    consumer_emit if entity == "geradores" else None
                ),
            )
        except CsvImportCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — uma entidade não derruba as outras
            if entity == "barras":
                raise
            if entity == "geradores":
                tracker.finish_entity(consumer_rows_seen + generator_rows_seen)
            table = (
                f"{target.table} + {consumer_target.table}"
                if consumer_target is not None
                else target.table
            )
            record(entity, table, None, str(exc))
            continue

        results[entity] = result
        table = (
            f"{target.table} + {consumer_target.table}"
            if consumer_target is not None
            else target.table
        )
        record(entity, table, result, None)
        rows_read = getattr(result, "total_rows", 0)
        if entity == "geradores":
            rows_read += getattr(result, "consumer_total_rows", 0)
        tracker.finish_entity(rows_read)

    bars = results.get("barras")
    if bars is None:  # pragma: no cover - garantido pelo raise acima
        raise CsvImportError("As barras não puderam ser importadas do banco.")

    allocations = None
    allocation_error = None
    if phase_configuration is not None and results.get("cargas") is not None:
        try:
            allocations = _load_transformer_allocations(
                database,
                results["cargas"].model,
                phase_configuration,
                source_path=source_path,
                cancel_event=cancel_event,
            )
        except InterruptedError:
            raise CsvImportCancelled("Importação cancelada.")
        except Exception as exc:  # noqa: BLE001 — agregado não derruba a rede
            allocation_error = str(exc)

    return MdbImportResult(
        source_path=source_path,
        bars=bars,
        cables=results.get("cabos"),
        segments=results.get("trechos"),
        loads=results.get("cargas"),
        capacitors=results.get("capacitores"),
        generators=results.get("geradores"),
        patterns=results.get("patamares"),
        switches=results.get("chaves"),
        regulators=results.get("reguladores"),
        circuits=results.get("circuitos"),
        circuit_levels=results.get("patamares_circuitos"),
        allocations=allocations,
        allocation_error=allocation_error,
        outcomes=tuple(outcomes),
        applied_scale=scale,
    )


_ALLOCATION_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BT_ET", BT_ET_HEADER),
    ("BT_CONS", BT_CONSUMER_HEADER),
    ("BT_GERADOR_CONS", BT_GENERATOR_HEADER),
    ("MT_CONS", MT_CONSUMER_ENERGY_HEADER),
    ("MT_GERADOR_CONS", MT_GENERATOR_ENERGY_HEADER),
)

_ALLOCATION_DIAGNOSTIC_COLUMNS: dict[str, tuple[str, ...]] = {
    "BT_CONS": BT_CONSUMER_DIAGNOSTIC_HEADER,
}


def _load_transformer_allocations(
    database: AccessDatabase,
    loads,  # noqa: ANN001 — LoadModel já garantido pelo resultado de cargas
    phase_configuration: PhaseConfiguration,
    *,
    source_path: str,
    cancel_event: threading.Event | None,
) -> TransformerAllocationModel | None:
    """Lê o agregado opcional quando ``BT_ET`` identifica o banco estendido."""

    table_by_name = {name.casefold(): name for name in database.tables()}
    if "bt_et" not in table_by_name:
        return None

    resolved: dict[
        str,
        tuple[str, tuple[str, ...], tuple[str, ...]],
    ] = {}
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for canonical_table, required in _ALLOCATION_TABLES:
        table = table_by_name.get(canonical_table.casefold())
        if table is None:
            missing_tables.append(canonical_table)
            continue
        columns = database.columns(table)
        by_name = {column.casefold(): column for column in columns}
        absent = [name for name in required if name.casefold() not in by_name]
        if absent:
            missing_columns.append(f"{canonical_table}: {', '.join(absent)}")
            continue
        optional = tuple(
            name
            for name in _ALLOCATION_DIAGNOSTIC_COLUMNS.get(canonical_table, ())
            if name.casefold() in by_name and name not in required
        )
        logical_columns = (*required, *optional)
        resolved[canonical_table] = (
            table,
            logical_columns,
            tuple(by_name[name.casefold()] for name in logical_columns),
        )
    if missing_tables or missing_columns:
        details: list[str] = []
        if missing_tables:
            details.append("tabelas ausentes: " + ", ".join(missing_tables))
        if missing_columns:
            details.append("colunas ausentes em " + "; ".join(missing_columns))
        raise ValueError(
            "Dados de alocação por energia incompletos; " + "; ".join(details) + "."
        )

    iterators: list[Iterator[tuple[str, ...]]] = []

    def allocation_table(name: str, header: tuple[str, ...]) -> AllocationTable:
        table, logical_columns, columns = resolved[name]
        if logical_columns[: len(header)] != header:  # pragma: no cover - constante
            raise AssertionError(f"Cabeçalho interno inesperado para {name}.")
        rows = database.iter_rows(table, columns)
        iterators.append(rows)
        return AllocationTable(
            logical_columns,
            rows,
            source_label(source_path, table),
            FIRST_ROW_NUMBER,
        )

    try:
        return build_transformer_allocations(
            loads,
            phase_configuration,
            bt_et=allocation_table("BT_ET", BT_ET_HEADER),
            bt_consumers=allocation_table("BT_CONS", BT_CONSUMER_HEADER),
            bt_generators=allocation_table(
                "BT_GERADOR_CONS", BT_GENERATOR_HEADER
            ),
            mt_consumers=allocation_table(
                "MT_CONS", MT_CONSUMER_ENERGY_HEADER
            ),
            mt_generators=allocation_table(
                "MT_GERADOR_CONS", MT_GENERATOR_ENERGY_HEADER
            ),
            source_path=source_path,
            cancel_check=(
                None if cancel_event is None else cancel_event.is_set
            ),
        )
    finally:
        for rows in iterators:
            close = getattr(rows, "close", None)
            if close is not None:
                close()


def _import_entity(
    entity: str,
    database: AccessDatabase,
    target: ResolvedEntity,
    crs: UtmCrs,
    results: dict[str, Any],
    *,
    consumer_target: ResolvedEntity | None,
    source_path: str,
    scale: float,
    cancel_event: threading.Event | None,
    progress: Callable[[int], None] | None,
    consumer_progress: Callable[[int], None] | None,
) -> Any:
    """Entrega as linhas da tabela ao ``parse_*_rows`` da entidade."""

    common = {
        "source_label": source_label(source_path, target.table),
        "encoding": MDB_ENCODING,
        "first_line_number": FIRST_ROW_NUMBER,
        "cancel_event": cancel_event,
        "progress": progress,
    }
    header = target.header
    rows = _rows(database, target)

    if entity == "geradores":
        if consumer_target is None:  # pragma: no cover - validado pelo chamador
            raise CsvImportError("A tabela MT_CONS não foi resolvida.")
        consumer_rows = _rows(database, consumer_target)
        try:
            return parse_generator_rows(
                header,
                rows,
                consumer_target.header,
                consumer_rows,
                results["cargas"].model,
                generator_source_label=source_label(source_path, target.table),
                consumer_source_label=source_label(
                    source_path, consumer_target.table
                ),
                generator_encoding=MDB_ENCODING,
                consumer_encoding=MDB_ENCODING,
                generator_first_line_number=FIRST_ROW_NUMBER,
                consumer_first_line_number=FIRST_ROW_NUMBER,
                cancel_event=cancel_event,
                generator_progress=progress,
                consumer_progress=consumer_progress,
            )
        finally:
            for iterator in (rows, consumer_rows):
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()

    if entity == "barras":
        return parse_bar_rows(header, rows, crs, scale=scale, **common)
    if entity == "cabos":
        return parse_cable_rows(header, rows, **common)
    if entity == "trechos":
        return parse_segment_rows(header, rows, results["barras"].model, **common)
    if entity == "cargas":
        return parse_load_rows(header, rows, results["barras"].model, **common)
    if entity == "capacitores":
        return parse_capacitor_rows(
            header, rows, results["barras"].model, **common
        )
    if entity == "patamares":
        return parse_load_pattern_rows(header, rows, results["cargas"].model, **common)
    if entity == "chaves":
        return parse_switch_rows(header, rows, results["trechos"].model, **common)
    if entity == "reguladores":
        return parse_regulator_rows(header, rows, results["trechos"].model, **common)
    if entity == "circuitos":
        switches = results.get("chaves")
        return parse_circuit_rows(
            header,
            rows,
            results["trechos"].model,
            None if switches is None else switches.model,
            **common,
        )
    if entity == "patamares_circuitos":
        return parse_circuit_level_rows(
            header,
            rows,
            results["circuitos"].model,
            **common,
        )
    raise CsvImportError(f"Entidade desconhecida: {entity}")  # pragma: no cover
