"""UC reais por identidade, importação, composição e apresentação dos blocos."""
from dataclasses import replace
import threading

import pytest

from circuit_viewer.block_analysis import analyze_blocks
from circuit_viewer.consumer_counts import read_consumer_counts
from circuit_viewer.csv_import import CsvImportCancelled
from circuit_viewer.mdb_import import load_database, dataset_from_result
from circuit_viewer.model import LoadModel, UtmCrs, constructor_columns
from circuit_viewer.network_registry import FileIdentity
from circuit_viewer.project_state import ProjectState, propose_import, resolve_import, ProjectChangeSet, edit_equipment
from circuit_viewer.source_composition import restrict_to_circuits, compose
from tests.test_mdb_import import network_database
from tests.test_block_analysis import make_bars, make_network, make_loads, make_catalog, make_switches


def bank():
    return network_database(
        BT_ET=(["ID", "MT_CAR_ID"], [(20, 2)]),
        # Repetição idêntica não duplica UC; ID=101 também existe em MT.
        BT_CONS=(["ID", "ET_ID"], [(101, 20), (102, 20), (101, 20)]),
    )


def imported(database=None):
    return load_database(database or bank(), UtmCrs(21, northern=False), source_path="source.mdb")


def project_import(state, source, file):
    proposal = propose_import(state, source, file)
    change = resolve_import(state, proposal, {f.circuit_id: "update" for f in proposal.feeders})
    assert isinstance(change, ProjectChangeSet)
    return change


def test_import_counts_distinct_bt_and_mt_without_energy_or_generator_tables():
    database = bank()
    del database._tables["MT_GERADOR_CONS"]
    result = imported(database)
    assert result.loads.model.consumer_counts == (3,)
    assert not result.consumer_count_diagnostics
    assert "BT_CONS.ID" in result.loads.model.consumer_count_sources[0]
    # Dependentes recebem exatamente o mesmo modelo enriquecido.
    assert result.patterns.model.loads is result.loads.model


@pytest.mark.parametrize("problem", ["missing_table", "missing_column", "missing_et", "ambiguous_id", "empty_id", "read_failure"])
def test_unreliable_data_is_unknown_and_preserves_loads(problem):
    database = bank()
    if problem == "missing_table":
        del database._tables["BT_CONS"]
    elif problem == "missing_column":
        database._tables["BT_CONS"] = (["ID"], [(101,)])
    elif problem == "missing_et":
        database._tables["BT_CONS"][1].append((103, 999))
    elif problem == "ambiguous_id":
        database._tables["BT_CONS"][1].append((101, 999))
    elif problem == "empty_id":
        database._tables["BT_CONS"][1].append((None, 20))
    else:
        original = database.iter_rows
        def failing(table, columns, **kwargs):
            if table == "BT_CONS":
                raise OSError("falha simulada")
            yield from original(table, columns, **kwargs)
        database.iter_rows = failing
    result = imported(database)
    assert result.loads.model.consumer_counts == (None,)
    assert result.consumer_count_diagnostics
    assert ("loads", "consumer_counts") in result.omitted_fields


def test_zero_is_known_when_consumer_tables_are_empty():
    database = bank()
    database._tables["BT_CONS"][1].clear()
    database._tables["MT_CONS"][1].clear()
    assert imported(database).loads.model.consumer_counts == (0,)


def test_unimported_loads_are_reported_without_inventing_bars():
    database = bank()
    database._tables["BT_ET"][1].append((21, 999))
    database._tables["BT_CONS"][1].append((103, 21))
    result = imported(database)
    assert result.loads.model.consumer_counts == (3,)
    assert "1 UC sem carga válida" in result.consumer_count_diagnostics[0]


def test_inactive_mt_is_still_a_registered_uc_and_manual_table_is_respected():
    database = bank()
    database._tables["OUTRA_MT"] = (["ID", "CARGA_ID", "ATIVO"], [(1, 2, 0), (2, 2, 1)])
    counted = read_consumer_counts(database, imported().loads.model, mt_table="OUTRA_MT")
    assert counted.counts == (4,)
    assert "OUTRA_MT" in counted.sources[0]


def test_cancel_during_consumer_iteration_closes_cursor():
    database = bank()
    event, closed = threading.Event(), []
    original = database.iter_rows
    def rows(table, columns, **kwargs):
        try:
            for row in original(table, columns, **kwargs):
                if table == "BT_CONS":
                    event.set()
                yield row
        finally:
            closed.append(table)
    database.iter_rows = rows
    with pytest.raises(CsvImportCancelled):
        read_consumer_counts(database, imported().loads.model, cancel_event=event)
    assert "BT_CONS" in closed


def test_counts_survive_restriction_multiple_sources_reimport_and_missing_tables():
    first_result = imported()
    first = dataset_from_result(first_result, tag="F1")
    restricted = restrict_to_circuits(first, (first.catalog.definition(0).circuit_id,))
    assert restricted.loads.consumer_counts == (3,)
    second = replace(first, tag="F2", source_path="other.mdb", registry=None)
    assert compose((restricted, second)).loads.consumer_counts == (3, 3)
    a, b = FileIdentity("source.mdb", "a"), FileIdentity("other.mdb", "b")
    change = project_import(ProjectState(), first, a)
    change = project_import(change.state, second, b)
    change = project_import(change.state, first, a)
    assert change.composed.loads.consumer_counts == (3, 3)
    incomplete = dataset_from_result(imported(network_database()), tag="F1")
    change = project_import(change.state, incomplete, a)
    assert change.composed.loads.consumer_counts == (3, 3)


def test_reimport_can_fill_previously_unknown_counts_and_update_external_changes():
    identity = FileIdentity("source.mdb", "a")
    incomplete = dataset_from_result(imported(network_database()), tag="F1")
    before = project_import(ProjectState(), incomplete, identity)
    load = next(row for row in before.state.records.values() if row.key.entity == "loads")
    before = edit_equipment(before.state, load.equipment_id, {"snom_values": "999"})
    source = dataset_from_result(imported(), tag="F1")
    after = project_import(before.state, source, identity)
    assert after.composed.loads.consumer_counts == (3,)
    assert after.composed.loads.snom_values == ("999",)
    database = bank()
    database._tables["BT_CONS"][1].append((103, 20))
    changed = project_import(after.state, dataset_from_result(imported(database), tag="F1"), identity)
    assert changed.composed.loads.consumer_counts == (4,)


@pytest.mark.parametrize("counts, expected", [((10, 20, 3), (10, 23)), ((0, None, 3), (0, None))])
def test_blocks_sum_their_loads_independent_of_switch_state(counts, expected):
    bars = make_bars(4)
    network = make_network(bars, [0, 1, 2], [1, 2, 3])
    raw_loads = make_loads(bars, [0, 2, 3], ["1", "2", "3"])
    columns = constructor_columns(raw_loads)
    columns["consumer_counts"] = counts
    loads = LoadModel(bars, **columns)
    for state in ("0", "1"):
        switches = make_switches(network, [(1, "1", state)])
        result = analyze_blocks(make_catalog(network, switches), switches, loads)
        assert tuple(record.consumer_count for record in result.records) == expected


def test_missing_load_model_is_unknown():
    bars = make_bars(2)
    network = make_network(bars, [0], [1])
    assert analyze_blocks(make_catalog(network)).records[0].consumer_count is None


def test_graph_caption_tooltip_and_layout_envelope():
    from PyQt6.QtWidgets import QApplication
    from circuit_viewer.block_graph_window import BlockNodeItem
    from circuit_viewer.block_graph import block_node_envelopes
    app = QApplication.instance() or QApplication([])
    bars = make_bars(2)
    record = analyze_blocks(make_catalog(make_network(bars, [0], [1]))).records[0]
    record = replace(record, consumer_count=1234)
    item = BlockNodeItem(record, 50)
    assert "UC: 1234" in item.caption_text
    assert "Unidades consumidoras cadastradas: 1234" in item.toolTip()
    envelope = block_node_envelopes((record,), {record.block_id: 50})[record.block_id]
    assert envelope.height / 2 >= item.boundingRect().bottom()
    assert app is not None
