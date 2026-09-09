from dataclasses import replace

import pytest

from circuit_viewer.model import constructor_columns, CableModel
from circuit_viewer.network_registry import FileIdentity, IdentityMapping
from circuit_viewer.project_state import (
    ProjectState, ProjectChangeSet, propose_import, resolve_import, edit_equipment,
    create_equipment, undo, remove_network, detach_origins,
)
from circuit_viewer.source_composition import CompositionError, compose
from circuit_viewer.opendss_export import build_switch_export, build_line_export
from circuit_viewer.phase_config import load_phase_configuration
from tests.test_source_composition import dataset, interconnected_dataset

FILE = FileIdentity("original.mdb", "a")


def imported(project=None, source=None, file=FILE, ids=(), actions=None, **kwargs):
    project = project if project is not None else ProjectState()
    proposal = propose_import(project, source or dataset(), file, circuit_ids=ids, **kwargs)
    decisions = actions or {row.circuit_id: "update" for row in proposal.feeders}
    change = resolve_import(project, proposal, decisions)
    assert isinstance(change, ProjectChangeSet), change
    change.validate(project)
    return change


def row(project, entity, native_id=None):
    return next(record for record in project.records.values()
                if record.key.entity == entity and (native_id is None or record.key.native_id == native_id))


def test_identity_is_internal_and_idempotent_even_after_copy():
    first = imported()
    second = imported(first.state, file=FileIdentity("copy.mdb", FILE.digest))
    assert len(second.state.workspace) == 1
    assert {r.equipment_id for r in first.state.records.values()} == {r.equipment_id for r in second.state.records.values()}
    assert first.state.workspace.datasets[0].registry.network_id != "access:" + FILE.digest
    assert len(first.state.history) == 1
    assert second.state.revision == 2


def test_identical_feeder_is_reviewed_and_no_decision_is_implicit():
    project = imported().state
    proposal = propose_import(project, dataset(), FILE)
    assert proposal.feeders[0].exists and proposal.feeders[0].changed_count == 0
    with pytest.raises(CompositionError, match="Escolha"):
        resolve_import(project, proposal, {})


def test_local_edit_survives_other_source_reimport_and_undo():
    project = imported().state
    regulator = row(project, "regulators")
    edited = edit_equipment(project, regulator.equipment_id, {"vnom_values": "15"}).state
    third = imported(edited, dataset(offset=100), FileIdentity("other.mdb", "b"))
    repeated = imported(third.state)
    assert dict(repeated.state.equipment(regulator.equipment_id).values)["vnom_values"] == "15"
    assert dict(project.equipment(regulator.equipment_id).values)["vnom_values"] == "13.8"
    reverted = undo(edited)
    assert dict(reverted.state.equipment(regulator.equipment_id).values)["vnom_values"] == "13.8"
    assert reverted.state.revision > edited.revision
    assert reverted.state.previous is None


def changed_regulator(value):
    source = dataset()
    columns = constructor_columns(source.regulators)
    columns["vnom_values"] = (value,)
    return replace(source, regulators=type(source.regulators)(source.segments, **columns))


def test_external_change_is_proposed_without_losing_other_local_fields():
    project = imported().state
    regulator = row(project, "regulators")
    project = edit_equipment(project, regulator.equipment_id, {"snom_values": "400"}).state
    change = imported(project, changed_regulator("15"))
    values = dict(change.state.equipment(regulator.equipment_id).values)
    assert values["snom_values"] == "400" and values["vnom_values"] == "15"


@pytest.mark.parametrize("choice,expected", [("existing", "14"), ("incoming", "15")])
def test_three_way_conflict_is_per_field(choice, expected):
    project = imported().state
    regulator = row(project, "regulators")
    project = edit_equipment(project, regulator.equipment_id, {"vnom_values": "14"}).state
    proposal = propose_import(project, changed_regulator("15"), FILE)
    conflicts = resolve_import(project, proposal, {"2": "update"})
    assert len(conflicts) == 1
    assert conflicts[0].field == "vnom_values"
    assert conflicts[0].baseline == "13.8"
    change = resolve_import(project, proposal, {"2": "update"}, {conflicts[0].decision_key: choice})
    assert dict(change.state.equipment(regulator.equipment_id).values)["vnom_values"] == expected
    assert dict(project.equipment(regulator.equipment_id).values)["vnom_values"] == "14"


def test_omitted_field_and_table_do_not_erase_existing_values():
    project = imported().state
    source = replace(changed_regulator(""), omitted_fields=(("regulators", "vnom_values"),),
                     loads=None, patterns=None, generators=None)
    change = imported(project, source)
    assert dict(row(change.state, "regulators").values)["vnom_values"] == "13.8"
    assert row(change.state, "loads").equipment_id == row(project, "loads").equipment_id


def test_keep_preserves_existing_while_another_feeder_is_added():
    source = interconnected_dataset()
    project = imported(source=source, ids=("2",)).state
    change = imported(project, source, ids=("2", "3"), actions={"2": "keep", "3": "update"})
    assert len(change.composed.catalog) == 2
    assert len(change.composed.switches) == 1
    assert row(change.state, "circuits", "2").equipment_id == row(project, "circuits", "2").equipment_id


def test_pending_connection_resolves_from_another_linked_file_without_rereading_first():
    source = interconnected_dataset()
    project = imported(source=source, ids=("2",)).state
    assert len(project.pending) == 2
    change = imported(project, source, FileIdentity("part_b.mdb", "b"), ids=("3", "4"), target_tag="F1")
    assert len(change.composed.switches) == 2
    assert not change.state.pending
    export = build_switch_export(change.composed.catalog, load_phase_configuration(), (0, 1, 2))
    assert export.exported_count == 2 and export.discarded_count == 0


def test_switch_state_changes_operational_topology_without_changing_physical_graph():
    change = imported(source=interconnected_dataset())
    switch = row(change.state, "switches", "4")
    closed = edit_equipment(change.state, switch.equipment_id, {"states": "1"})
    assert closed.topology.physical_components == change.topology.physical_components
    assert closed.topology.operational_components != change.topology.operational_components
    assert closed.topology.coupled_circuits
    assert len(closed.composed.switches) == 2
    assert dict(row(closed.state, "switches", "4").values)["normal_states"] == dict(switch.values)["normal_states"]
    export = build_switch_export(closed.composed.catalog, load_phase_configuration(), (0, 1, 2))
    assert export.exported_count == 2


def test_unknown_switch_state_never_assumes_conduction():
    change = imported(source=interconnected_dataset())
    unknown = edit_equipment(change.state, row(change.state, "switches", "4").equipment_id, {"states": "?"})
    assert unknown.topology.issues
    assert not unknown.topology.coupled_circuits


def test_replacement_preserves_manual_equipment_and_ignores_omitted_entities():
    project = imported().state
    capacitor = row(project, "capacitors")
    manual = create_equipment(project, "F1", "capacitors", dict(capacitor.values))
    manual_id = next(r.equipment_id for r in manual.state.records.values() if r.origins[0].kind == "manual")
    source = replace(dataset(), regulators=None, provided_entities=frozenset({"bars", "segments", "circuits"}))
    replaced = imported(manual.state, source, actions={"2": "replace"})
    assert replaced.state.equipment(manual_id) is not None
    assert row(replaced.state, "regulators").equipment_id == row(project, "regulators").equipment_id


def test_replacement_removes_authoritatively_absent_exclusive_equipment():
    project = imported().state
    source = replace(dataset(), regulators=None, provided_entities=frozenset({"bars", "segments", "circuits", "regulators"}))
    change = imported(project, source, actions={"2": "replace"})
    assert not any(key.entity == "regulators" for key in change.state.records)


def test_stale_or_cancelled_transaction_cannot_commit():
    project = imported().state
    proposal = propose_import(project, dataset(), FILE)
    change = resolve_import(project, proposal, {"2": "update"})
    with pytest.raises(InterruptedError):
        change.validate(project, cancelled=True)
    with pytest.raises(CompositionError, match="mudou"):
        change.validate(change.state)
    with pytest.raises(InterruptedError):
        resolve_import(project, proposal, {"2": "update"}, cancel_check=lambda: True)


def test_removal_and_reimport_preserve_internal_identities():
    first = imported()
    removed = remove_network(first.state, "F1")
    again = imported(removed.state)
    assert {k: r.equipment_id for k, r in first.state.records.items()} == {k: r.equipment_id for k, r in again.state.records.items()}


def test_copy_of_bank_retains_previous_baseline_after_local_edit():
    project = imported().state
    regulator = row(project, "regulators")
    project = edit_equipment(project, regulator.equipment_id, {"vnom_values": "15"}).state
    repeated = imported(project, file=FileIdentity("moved.mdb", FILE.digest))
    assert dict(repeated.state.equipment(regulator.equipment_id).values)["vnom_values"] == "15"


def test_field_provenance_preserves_both_local_and_external_edits():
    project = imported().state
    regulator = row(project, "regulators")
    project = edit_equipment(project, regulator.equipment_id, {"snom_values": "400"}).state
    updated = imported(project, changed_regulator("15"))
    origins = dict(updated.state.equipment(regulator.equipment_id).field_origins)
    assert origins["snom_values"].kind == "manual"
    assert origins["vnom_values"].kind == "import"


def test_configuration_versions_are_content_based_and_part_of_study_inputs():
    from circuit_viewer.study_inputs import StudyInputRevision
    from circuit_viewer.opendss_library import OpenDssLibraryCatalog, CableDefinition
    project = imported().state
    library = OpenDssLibraryCatalog()
    first = StudyInputRevision.capture(project, phases=load_phase_configuration(), library=library)
    assert first == StudyInputRevision.capture(project, phases=load_phase_configuration(), library=library.clone())
    library.cables.append(CableDefinition(cable_id="manual", name="Teste"))
    second = StudyInputRevision.capture(project, phases=load_phase_configuration(), library=library)
    assert second.configuration_digest != first.configuration_digest


def test_partial_groups_with_absent_load_patterns_have_identical_records():
    from circuit_viewer.model import LoadModel, LoadPatternModel
    source = interconnected_dataset()
    columns = constructor_columns(source.loads)
    columns = {name: tuple(values) * 2 for name, values in columns.items()}
    columns.update(load_ids=("2", "3"), bar_indices=(1, 2))
    loads = LoadModel(source.bars, **columns)
    patterns = LoadPatternModel(loads, (source.patterns.records_for_load(0), None))
    source = replace(source, loads=loads, patterns=patterns, generators=None, allocations=None)
    all_at_once = imported(source=source)
    part = imported(source=source, ids=("2",)).state
    combined = imported(part, source=source, ids=("3", "4"))
    normalized = lambda state: {(k.entity, k.native_id): record.values for k, record in state.records.items()}
    assert normalized(all_at_once.state) == normalized(combined.state)
    assert not any(k.entity == "patterns" and not dict(r.values)["records"] for k, r in combined.state.records.items())


def test_circuit_schedule_edit_survives_another_import():
    from circuit_viewer.calculation_levels import default_calculation_levels
    project = imported().state
    schedule_record = row(project, "circuit_levels")
    schedule = default_calculation_levels()
    project = edit_equipment(project, schedule_record.equipment_id, {"schedule": schedule}).state
    again = imported(project)
    assert dict(again.state.equipment(schedule_record.equipment_id).values)["schedule"] == schedule


def test_replace_cannot_delete_a_cable_still_referenced_by_preserved_segments():
    project = imported().state
    source = replace(dataset(), cables=None, provided_entities=frozenset({"bars", "segments", "circuits", "cables"}))
    with pytest.raises(CompositionError, match="referência quebrada"):
        imported(project, source, actions={"2": "replace"})
    assert row(project, "cables")


def test_linked_partial_allocations_are_diagnosed_and_preserved_in_registry():
    from circuit_viewer.allocation import PhaseValues, TransformerAllocationModel, TransformerAllocationRecord
    source = dataset()
    allocation = TransformerAllocationRecord(source.loads.load_ids[0], PhaseValues(1, 2, 3), PhaseValues(4, 5, 6), 7, 8)
    source = replace(source, allocations=TransformerAllocationModel(source.loads, load_phase_configuration(), (allocation,)))
    first = imported(source=source)
    combined = imported(first.state, dataset(offset=100), FileIdentity("linked.mdb", "b"), target_tag="F1")
    assert combined.composed.allocations is None
    assert any("Alocações indisponíveis" in note for note in combined.composed.report.notes)
    assert any(key.entity == "allocations" for key in combined.state.records)


def test_saved_manual_correspondence_does_not_lose_later_interconnections():
    from circuit_viewer.source_composition import ENTITIES, MODEL_TYPES
    from circuit_viewer.model import CircuitCatalogModel
    source = interconnected_dataset()
    project = imported(source=source, ids=("2",)).state
    models = {}
    for entity in ENTITIES:
        model = getattr(source, entity.name)
        if model is None:
            models[entity.name] = None
            continue
        columns = constructor_columns(model)
        if entity.name == "bars":
            columns["bar_ids"] = tuple("107" if value == "7" else value for value in model.bar_ids)
        args = () if entity.parent is None else (models[entity.parent],)
        models[entity.name] = MODEL_TYPES[entity.name](*args, **columns)
    definitions = tuple(replace(d, root_bar_id="107" if d.root_bar_id == "7" else d.root_bar_id)
                        for d in source.catalog.definitions)
    models["catalog"] = CircuitCatalogModel.build(models["segments"], models["switches"], definitions)
    linked = replace(source, **models, patterns=None, allocations=None, circuit_levels=None)
    identity = FileIdentity("linked.mdb", "b")
    first = imported(project, linked, identity, ids=("2",), target_tag="F1",
                     correspondences=(IdentityMapping("bars", "107", "7"),))
    expanded = imported(first.state, linked, identity, ids=("3", "4"))
    assert len(expanded.composed.switches) == 2
    assert not expanded.state.pending
    assert row(project, "bars", "7").equipment_id == row(expanded.state, "bars", "7").equipment_id


def test_detaching_origin_preserves_equipment_and_provenance():
    project = imported().state
    detached = detach_origins(project, "F1")
    assert detached.state.records == project.records
    assert not detached.state.workspace.datasets[0].registry.recognizes(FILE)
    assert row(detached.state, "bars").origins == row(project, "bars").origins


def test_removal_undo_and_reimport_do_not_depend_on_files():
    project = imported().state
    other = imported(project, dataset(offset=100), FileIdentity("missing.mdb", "b"))
    removed = remove_network(other.state, "F1")
    assert len(removed.state.workspace) == 1
    restored = undo(removed.state)
    assert restored.state.records == other.state.records
    assert len(restored.composed.bars) == 6


def test_repeated_ids_only_propose_possible_correspondence():
    project = imported().state
    proposal = propose_import(project, dataset(), FileIdentity("different.mdb", "b"))
    assert proposal.feeders[0].possible_matches
    change = resolve_import(project, proposal, {"2": "update"})
    assert len(change.state.workspace) == 2
    assert len(change.composed.bars) == 6


def test_three_source_cable_variants_are_deduplicated():
    a = dataset("F1")
    columns = constructor_columns(a.cables)
    columns["codes"] = ("different",)
    b = replace(dataset("F2", offset=100), cables=CableModel(**columns))
    c = replace(dataset("F3", offset=200), cables=CableModel(**columns))
    merged = compose((a, b, c))
    assert len(merged.cables) == 2
    assert constructor_columns(merged.segments)["phase_cable_ids"][2] == constructor_columns(merged.segments)["phase_cable_ids"][4]


def test_missing_cable_is_not_resolved_by_an_independent_source():
    a = replace(dataset("F1"), cables=None)
    b = dataset("F2", offset=100)
    merged = compose((a, b))
    cable_id = constructor_columns(merged.segments)["phase_cable_ids"][0]
    assert merged.cables.index_for_id(cable_id) is None
    exported = build_line_export(merged.catalog, merged.cables, load_phase_configuration(), (0,))
    assert exported.exported_count == 0 and exported.discarded_count > 0
