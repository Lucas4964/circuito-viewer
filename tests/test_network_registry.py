"""Identidade, atualização transacional e reconstrução das referências da rede."""

from dataclasses import replace

import numpy as np
import pytest

from circuit_viewer.model import CircuitModel, LineNetworkModel, constructor_columns
from circuit_viewer.network_registry import EquipmentKey, FileIdentity, IdentityMapping, register_import
from circuit_viewer.source_composition import ENTITIES, CompositionError, SourceWorkspace, compose
from tests.test_source_composition import dataset, interconnected_dataset
from tests import test_source_composition as composition_examples


ORIGIN = FileIdentity("original.mdb", "signature-a")


def choose_incoming(conflicts):
    return {item.key: "incoming" for item in conflicts}


def import_source(workspace=None, source=None, origin=ORIGIN, **kwargs):
    return register_import(workspace if workspace is not None else SourceWorkspace(),
                           source if source is not None else dataset(), origin, **kwargs)


def test_roundtrip_preserves_all_columns_and_parent_identity():
    original = dataset()
    workspace, rebuilt = import_source(source=original)
    for entity in ENTITIES:
        before, after = getattr(original, entity.name), getattr(rebuilt, entity.name)
        if before is None:
            assert after is None
            continue
        for name, values in constructor_columns(before).items():
            np.testing.assert_equal(constructor_columns(after)[name], values)
        if entity.parent:
            parent_attribute = "bars" if entity.parent == "bars" else entity.parent
            assert getattr(after, parent_attribute) is getattr(rebuilt, entity.parent)
    assert rebuilt.catalog.segments is rebuilt.segments
    assert rebuilt.catalog.switches is rebuilt.switches
    assert rebuilt.patterns.loads is rebuilt.loads
    assert len(workspace) == 1
    with pytest.raises(TypeError):
        rebuilt.registry.records[EquipmentKey("x", "bars", "7")] = None


def test_reimport_is_idempotent_and_duplicate_copy_adds_provenance():
    first, original = import_source()
    twice, repeated = import_source(first)
    copied, duplicate = import_source(twice, origin=FileIdentity("copy.mdb", ORIGIN.digest))
    assert len(copied) == 1
    assert duplicate.tag == original.tag
    assert duplicate.registry.records.keys() == original.registry.records.keys()
    assert len(duplicate.bars) == 3
    assert len(original.registry.bindings) == 1  # Candidate creation never edits installed data.
    assert len(duplicate.registry.bindings) == 2
    record = copied.equipment(EquipmentKey(original.registry.network_id, "bars", "7"))
    assert {item.file.path for item in record.origins} == {"original.mdb", "copy.mdb"}


def test_distinct_databases_never_merge_by_id_label_or_coordinates():
    first, original = import_source()
    combined, other = import_source(first, origin=FileIdentity("other.mdb", "other-signature"))
    assert len(combined) == 2
    assert original.registry.network_id != other.registry.network_id
    model = compose(combined.datasets)
    assert len(model.bars) == 6
    key = model.provenance.equipment_key("bars", 3)
    assert key.network_id == other.registry.network_id
    assert combined.equipment(key) is not None


@pytest.mark.parametrize("reverse", [False, True])
def test_sequential_feeders_equal_joint_selection_including_interties(reverse):
    source = interconnected_dataset()
    groups = [("2",), ("3", "4")]
    if reverse:
        groups.reverse()
    first, _ = import_source(source=source, circuit_ids=groups[0])
    sequential, result = import_source(first, source, circuit_ids=groups[1])
    joint, expected = import_source(source=source, circuit_ids=("2", "3", "4"))
    assert len(sequential) == 1
    assert result.registry.records.keys() == expected.registry.records.keys()
    for key in result.registry.records:
        assert result.registry.records[key].values == expected.registry.records[key].values
    interconnections = composition_examples.InterconnectionSurvivalTests.interconnections
    assert interconnections(result) == interconnections(expected)
    assert len(result.switches) == 2


def test_changed_values_require_explicit_review_and_keep_identity():
    first, original = import_source()
    incoming = dataset()
    incoming = replace(incoming, bars=CircuitModel(incoming.bars.bar_ids, incoming.bars.codes,
                                                  incoming.bars.x + 10, incoming.bars.y, incoming.crs))
    changed = FileIdentity(ORIGIN.path, "new-signature")
    with pytest.raises(CompositionError, match="exigem revisão"):
        import_source(first, incoming, changed)
    with pytest.raises(InterruptedError):
        import_source(first, incoming, changed, resolve_conflicts=lambda conflicts: None)
    reviewed = []

    def review(conflicts):
        reviewed.extend(conflicts)
        return {item.key: "existing" if item.key.native_id == "7" else "incoming" for item in conflicts}

    final, updated = import_source(first, incoming, changed, resolve_conflicts=review)
    assert len(reviewed) == 3
    assert reviewed[0].differences == (("x", original.bars.x[0], incoming.bars.x[0]),)
    assert updated.registry.network_id == original.registry.network_id
    assert updated.bars.x[0] == original.bars.x[0]
    assert updated.bars.x[1] == incoming.bars.x[1]
    assert updated.segments.bars is updated.bars
    assert first.dataset_for(original.tag) is original
    np.testing.assert_equal(original.bars.x, dataset().bars.x)


def test_unresolved_conflicts_cannot_commit():
    workspace, _ = import_source()
    changed = dataset(BARRA=(["BARRA_ID", "BLOCO_ID", "CODIGO", "X", "Y", "PL_ANO"],
                            [(7, 2, "changed", 5989944, 82487703, 0),
                             (8, 2, "COD-B", 5990044, 82487803, 0),
                             (9, 2, "COD-C", 5990144, 82487903, 0)]))
    with pytest.raises(CompositionError, match="todos os conflitos"):
        import_source(workspace, changed, resolve_conflicts=lambda conflicts: {})


def test_missing_numeric_values_are_not_false_conflicts():
    original = dataset()
    columns = constructor_columns(original.segments)
    columns["lengths"] = np.full(len(original.segments), np.nan)
    original = replace(original, segments=LineNetworkModel(original.bars, **columns))
    workspace, _ = import_source(source=original)
    _, repeat = import_source(workspace, original)
    assert np.isnan(repeat.segments.lengths).all()


def test_linking_different_ids_remaps_all_references_and_remembers_mapping():
    workspace, original = import_source()
    incoming = dataset(offset=100)
    mappings = tuple(IdentityMapping(entity.name, str(int(native_id) + 100), native_id)
                     for entity in ENTITIES if entity.name != "cables"
                     for native_id in getattr(getattr(original, entity.name), entity.id_column))
    mappings += (IdentityMapping("circuits", "102", "2"),)
    origin = FileIdentity("related.mdb", "related-signature")
    updated, result = import_source(workspace, incoming, origin, target_tag=original.tag,
                                    correspondences=mappings, resolve_conflicts=choose_incoming)
    assert len(updated) == 1
    assert result.registry.records.keys() == original.registry.records.keys()
    assert result.loads.bar_indices.tolist() == original.loads.bar_indices.tolist()
    assert result.catalog.definitions[0].root_bar_id == "7"
    assert result.switches.record(0).circuit_id == "2"
    assert result.patterns.records_for_load(0)[0].load_id == original.loads.load_ids[0]
    again, second = import_source(updated, incoming, origin)
    assert len(again) == 1
    assert second.registry.records.keys() == result.registry.records.keys()
    key = EquipmentKey(result.registry.network_id, "bars", "7")
    assert second.registry.records[key].accepted_origin.native_id == "107"


def test_invalid_correspondences_and_conflicting_network_targets_are_rejected():
    workspace, original = import_source()
    other_file = FileIdentity("other.mdb", "other-signature")
    with pytest.raises(CompositionError, match="Destino inexistente"):
        import_source(workspace, origin=other_file, target_tag=original.tag,
                      correspondences=(IdentityMapping("bars", "7", "missing"),))
    with pytest.raises(CompositionError, match="dois registros"):
        import_source(workspace, origin=other_file, target_tag=original.tag,
                      correspondences=(IdentityMapping("bars", "8", "7"),))
    workspace, other = import_source(workspace, origin=other_file)
    with pytest.raises(CompositionError, match="já pertence"):
        import_source(workspace, target_tag=other.tag)


def test_partial_reimport_preserves_unmentioned_equipment_and_replace_limits_selection():
    source = interconnected_dataset()
    workspace, original = import_source(source=source)
    _, added = import_source(workspace, source, circuit_ids=("2",))
    assert len(added.bars) == len(original.bars)
    replaced, only_a = import_source(workspace, source, circuit_ids=("2",), replace_workspace=True)
    assert len(replaced) == 1
    assert only_a.chosen_circuit_ids == ("2",)
    assert only_a.registry.network_id == original.registry.network_id
    assert len(only_a.bars) == 2


def test_removal_and_rename_preserve_other_network_identities():
    workspace, first = import_source()
    workspace, second = import_source(workspace, origin=FileIdentity("b.mdb", "b"))
    remaining = workspace.renamed(second.tag, "Novo nome").without(first.tag)
    key = compose(remaining.datasets).provenance.equipment_key("bars", 0)
    assert key.network_id == second.registry.network_id
    assert remaining.equipment(key) is not None
    assert remaining.next_tag() == "F3"


def test_file_signature_recognizes_bytes_not_filename_and_can_cancel(tmp_path):
    a, b = tmp_path / "a.mdb", tmp_path / "b.mdb"
    a.write_bytes(b"test database bytes")
    b.write_bytes(a.read_bytes())
    assert FileIdentity.read(a).digest == FileIdentity.read(b).digest
    b.write_bytes(b"other")
    assert FileIdentity.read(a).digest != FileIdentity.read(b).digest
    with pytest.raises(InterruptedError):
        FileIdentity.read(a, lambda: True)


def test_cancelled_registry_build_leaves_workspace_untouched():
    workspace, original = import_source()
    with pytest.raises(InterruptedError):
        import_source(workspace, cancel_check=lambda: True)
    assert workspace.datasets == (original,)


def test_optional_schedules_and_allocations_are_aligned_after_registration():
    from circuit_viewer.allocation import PhaseValues, TransformerAllocationModel, TransformerAllocationRecord
    from circuit_viewer.calculation_levels import default_calculation_levels
    from circuit_viewer.circuit_calculation_levels import CircuitCalculationLevelsModel
    from circuit_viewer.phase_config import load_phase_configuration

    source = dataset()
    schedule = default_calculation_levels()
    allocation = TransformerAllocationRecord(source.loads.load_ids[0], PhaseValues(1, 2, 3), PhaseValues(4, 5, 6), 7, 8)
    source = replace(source,
        circuit_levels=CircuitCalculationLevelsModel(source.catalog, (schedule,)),
        allocations=TransformerAllocationModel(source.loads, load_phase_configuration(), (allocation,)))
    workspace, rebuilt = import_source(source=source)
    assert rebuilt.allocations.loads is rebuilt.loads
    assert rebuilt.allocations.record(0) == allocation
    assert rebuilt.circuit_levels.circuits is rebuilt.catalog
    assert rebuilt.circuit_levels.schedule(0) == schedule
    # A leitura parcial de outras entidades não apaga agendas/alocações existentes.
    _, partial = import_source(workspace, dataset())
    assert partial.allocations.record(0) == allocation
    assert partial.circuit_levels.schedule(0) == schedule


def test_database_row_order_is_not_equipment_identity():
    from tests.test_mdb_import import network_database

    db = network_database()
    columns = tuple(db.columns("BARRA"))
    rows = list(db.iter_rows("BARRA", columns))
    workspace, original = import_source()
    _, reordered = import_source(workspace, dataset(BARRA=(columns, list(reversed(rows)))))
    assert reordered.registry.records.keys() == original.registry.records.keys()
    assert reordered.segments.start_indices.tolist() == original.segments.start_indices.tolist()
