"""Retrato de inspeção Access: somente dados, sem conexão compartilhada com a UI."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Sequence

from .mdb_engine import AccessDatabase
from .mdb_import import CircuitChoice, SubstationChoice, list_circuits, list_substations
from .mdb_mapping import EntityMapping, ResolvedMapping, resolve_mapping


@dataclass(frozen=True, slots=True)
class DatabaseSchema:
    """Metadados suficientes para validar overrides sem consultar ODBC na UI."""

    table_names: tuple[str, ...]
    table_columns: dict[str, tuple[str, ...]]

    def tables(self) -> tuple[str, ...]:
        return self.table_names

    def columns(self, table: str) -> tuple[str, ...]:
        if table not in self.table_columns:
            raise ValueError(f"Não foi possível ler as colunas de {table}.")
        return self.table_columns[table]


@dataclass(frozen=True, slots=True)
class AuxiliaryTable:
    table: str
    purpose: str
    status: str


@dataclass(frozen=True, slots=True)
class MdbInspection:
    schema: DatabaseSchema
    automatic_mapping: ResolvedMapping
    mapping: ResolvedMapping
    row_counts: dict[str, int]
    substations: tuple[SubstationChoice, ...]
    circuits: tuple[CircuitChoice, ...]
    auxiliaries: tuple[AuxiliaryTable, ...]
    diagnostics: tuple[str, ...]


class _CancellableDatabase:
    def __init__(self, database: AccessDatabase, event: threading.Event) -> None:
        self.database = database
        self.event = event

    def check(self) -> None:
        if self.event.is_set():
            raise InterruptedError("Inspeção cancelada.")

    def tables(self):
        self.check()
        return self.database.tables()

    def columns(self, table):
        self.check()
        return self.database.columns(table)

    def iter_rows(self, table, columns):
        self.check()
        rows = self.database.iter_rows(table, columns)
        try:
            for row in rows:
                self.check()
                yield row
        finally:
            close = getattr(rows, "close", None)
            if close is not None:
                close()


def inspect_database(
    database: AccessDatabase,
    table_mapping: Sequence[EntityMapping] | None = None,
    *,
    overrides: dict[str, str] | None = None,
    cancel_event: threading.Event | None = None,
) -> MdbInspection:
    from .mdb_import import (
        _ALLOCATION_TABLES, _SUBSTATION_TABLE, _SUBSTATION_TRANSFORMER_TABLE,
        _SWITCH_TYPE_TABLE,
    )

    reader = _CancellableDatabase(database, cancel_event or threading.Event())
    tables = tuple(reader.tables())
    columns = {}
    diagnostics: list[str] = []
    for table in tables:
        reader.check()
        try:
            columns[table] = tuple(reader.columns(table))
        except InterruptedError:
            raise
        except Exception:
            diagnostics.append(f"Não foi possível ler as colunas de {table}.")
    schema = DatabaseSchema(tables, columns)
    automatic = resolve_mapping(schema, table_mapping)
    mapping = resolve_mapping(schema, table_mapping, overrides=overrides)
    counts: dict[str, int] = {}
    for table in dict.fromkeys(item.table for item in mapping.resolved):
        reader.check()
        try:
            counts[table] = database.row_count(table)
        except Exception:
            pass  # Contagens são informativas.
    reader.check()
    try:
        substations = list_substations(reader)
    except InterruptedError:
        raise
    except Exception:
        substations = ()
        diagnostics.append("Não foi possível ler as subestações; verifique a tabela SE.")
    circuits = list_circuits(reader, mapping.get("circuitos"), diagnostics=diagnostics,
                             substations=substations)
    purposes = (
        (*_SUBSTATION_TABLE, "Subestações"),
        (*_SUBSTATION_TRANSFORMER_TABLE, "Transformadores das subestações"),
        (*_SWITCH_TYPE_TABLE, "Tipos de chave"),
        *((name, wanted, "Agregados de consumidores e geração") for name, wanted in _ALLOCATION_TABLES),
    )
    by_name = {name.casefold(): name for name in tables}
    auxiliaries = []
    for name, wanted, purpose in purposes:
        table = by_name.get(name.casefold())
        found = {column.casefold() for column in columns.get(table, ())}
        missing = [column for column in wanted if column.casefold() not in found]
        status = "Ausente" if table is None else (
            "Colunas ausentes: " + ", ".join(missing) if missing else "Disponível"
        )
        auxiliaries.append(AuxiliaryTable(table or name, purpose, status))
    reader.check()
    return MdbInspection(schema, automatic, mapping, counts, substations, circuits,
                         tuple(auxiliaries), tuple(diagnostics))
