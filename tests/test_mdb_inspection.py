from __future__ import annotations

import threading

import pytest

from circuit_viewer.mdb_inspection import inspect_database
from circuit_viewer.mdb_import import list_circuits, list_substations
from circuit_viewer.mdb_mapping import resolve_mapping
from tests.test_mdb_import import network_database


def hierarchy_database():
    return network_database(
        SE=(["SE_ID", "CODIGO", "NOME"], [
            (1, "SE-A", "São José"), (2, "SE-A", "São José"), (3, "SE-C", "Vazia"),
        ]),
        CIRCUITO=(["CIRC_ID", "BARRA_ID", "CODIGO", "VNOM", "SE_ID"], [
            (2, 7, "Água", 13.8, 1), (3, 8, "Centro", 13.8, 1),
            (4, 9, "Industrial", 34.5, 2), (5, 9, "Órfão", 13.8, 99),
            (6, 9, "Sem vínculo", 13.8, None),
        ]),
    )


def test_catalog_preserves_distinct_ids_with_identical_names_and_empty_substations():
    result = inspect_database(hierarchy_database())
    assert [item.substation_id for item in result.substations] == ["1", "2", "3"]
    assert [item.substation_id for item in result.circuits] == ["1", "1", "2", "99", ""]
    assert result.circuits[0].substation_name == "São José"
    assert result.circuits[3].reason == "SE_ID '99' sem correspondência em SE."
    assert "SE_ID" in result.circuits[4].reason
    assert result.mapping.has_mandatory
    assert result.row_counts["CIRCUITO"] == 5


def test_no_transformer_table_or_column_is_needed_for_hierarchy_or_import():
    from tests.test_mdb_import import run
    database = hierarchy_database()
    assert "SE_TRAFO" not in database.tables()
    assert list_circuits(database, resolve_mapping(database).get("circuitos"))[0].substation_code == "SE-A"
    result = run(database)
    assert result.circuits.model.definition(0).substation_code == "SE-A"


def test_mapped_circuit_table_and_id_alias_are_used():
    from dataclasses import replace
    from circuit_viewer.mdb_mapping import load_table_mapping
    database = hierarchy_database()
    columns, rows = database._tables.pop("CIRCUITO")
    columns[0] = "FEEDER_ID"
    database._tables["ALIMENTADOR"] = (columns, rows)
    mapping = tuple(replace(item, aliases={**item.aliases, "CIRC_ID": ("FEEDER_ID",)})
                    if item.entity == "circuitos" else item for item in load_table_mapping())
    result = inspect_database(database, mapping, overrides={"circuitos": "ALIMENTADOR"})
    assert result.mapping.get("circuitos").table == "ALIMENTADOR"
    assert result.circuits[0].circuit_id == "2"
    assert result.circuits[0].substation_id == "1"


def test_schema_validates_overrides_without_reading_rows():
    database = hierarchy_database()
    result = inspect_database(database)
    database.statements.clear()
    invalid = resolve_mapping(result.schema, overrides={"barras": "SE"})
    assert not invalid.has_mandatory
    assert "BARRA_ID" in invalid.reason_for("barras")
    assert database.statements == []


def test_missing_se_and_missing_circuits_remain_explicit_states():
    database = hierarchy_database()
    del database._tables["SE"]
    result = inspect_database(database)
    assert result.substations == ()
    assert len(result.circuits) == 5
    assert all(item.reason for item in result.circuits)
    del database._tables["CIRCUITO"]
    result = inspect_database(database)
    assert result.circuits == ()
    assert result.mapping.get("circuitos") is None


def test_bad_se_does_not_hide_readable_circuits():
    database = hierarchy_database()
    original = database.iter_rows

    def rows(table, columns):
        if table == "SE":
            raise ValueError("unreadable")
        return original(table, columns)

    database.iter_rows = rows
    result = inspect_database(database)
    assert len(result.circuits) == 5
    assert result.substations == ()
    assert result.diagnostics


def test_auxiliary_status_and_unavailable_counts_are_informative():
    database = hierarchy_database()
    database.row_count = lambda table: (_ for _ in ()).throw(ValueError("unavailable"))
    result = inspect_database(database)
    assert result.row_counts == {}
    assert len(result.circuits) == 5
    status = {item.table: item.status for item in result.auxiliaries}
    assert status["SE"] == "Disponível"
    assert status["SE_TRAFO"] == "Ausente"


def test_cancellation_during_rows_is_not_swallowed():
    event = threading.Event()
    database = hierarchy_database()
    original = database.iter_rows

    def rows(table, columns):
        for row in original(table, columns):
            if table == "CIRCUITO":
                event.set()
            yield row

    database.iter_rows = rows
    with pytest.raises(InterruptedError):
        inspect_database(database, cancel_event=event)


def test_partial_substation_labels_and_duplicate_ids():
    database = hierarchy_database()
    database._tables["SE"] = (["SE_ID", "NOME"], [(1, "Primeira"), (1, "Duplicada"), (None, "Inválida")])
    result = list_substations(database)
    assert len(result) == 1
    assert result[0].name == "Primeira"
    assert result[0].code == ""


def test_transformer_code_remains_available_when_present():
    from tests.test_mdb_import import LoadDatabaseTests
    database = network_database(**LoadDatabaseTests.SUBSTATION_TABLES)
    choices = list_circuits(database, resolve_mapping(database).get("circuitos"))
    assert choices[0].transformer_code == "53244TRAFO_032"
