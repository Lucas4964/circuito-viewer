"""Fluxo pelo worker e instalação transacional, sem Access real."""
from contextlib import contextmanager
from dataclasses import replace
import threading
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QDialogButtonBox

from circuit_viewer.main_window import MainWindow
from circuit_viewer.mdb_import import MdbSourceLoad
from circuit_viewer.network_registry import FileIdentity
from circuit_viewer.project_dialogs import ImportProposalDialog, FieldConflictDialog, ProjectProvenanceDialog
from circuit_viewer.project_state import ProjectState, propose_import, resolve_import, edit_equipment
from circuit_viewer.workers import MdbImportWorker
from tests.test_mdb_import import network_database
from tests.test_project_state import imported, row, changed_regulator, FILE
from tests.test_source_composition import CRS, dataset, SWITCH_TYPE_TABLES


@pytest.fixture
def window(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    for name in ("_show_mdb_import_report", "_show_composition_report", "_show_circuits_window"):
        monkeypatch.setattr(window, name, Mock())
    return window


def payload(change):
    return MdbSourceLoad(change.state.workspace.datasets[0], change.state.workspace,
                         change.composed, project_change=change)


def test_review_includes_unchanged_feeder_and_requires_explicit_decision(qtbot):
    proposal = propose_import(imported().state, dataset(), FILE)
    dialog = ImportProposalDialog(proposal)
    qtbot.addWidget(dialog)
    assert "sem alterações" in dialog.table.item(0, 1).text()
    button = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not button.isEnabled()
    dialog._choose_existing("keep")
    assert button.isEnabled()


def test_deletion_is_visible_before_confirmation(qtbot):
    project = imported().state
    source = replace(dataset(), regulators=None, provided_entities=frozenset({"bars", "segments", "circuits", "regulators"}))
    dialog = ImportProposalDialog(propose_import(project, source, FILE))
    qtbot.addWidget(dialog)
    combo = dialog.choices["2"]
    combo.setCurrentIndex(combo.findData("replace"))
    assert "Reguladores" in dialog.impact.toPlainText()


def test_per_field_review_has_no_implicit_overwrite(qtbot):
    project = imported().state
    project = edit_equipment(project, row(project, "regulators").equipment_id, {"vnom_values": "15"}).state
    conflicts = resolve_import(project, propose_import(project, changed_regulator("16"), FILE), {"2": "update"})
    dialog = FieldConflictDialog(conflicts)
    qtbot.addWidget(dialog)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert dialog.table.item(0, 3).text() == "15"
    assert dialog.table.item(0, 4).text() == "16"


def test_cancel_after_worker_finished_preserves_project(window):
    first = imported(window._project)
    window._commit_project_change(first)
    newer = imported(first.state, dataset(offset=100), FileIdentity("b", "b"))
    window._import_worker = type("Cancelled", (), {"_cancel_event": threading.Event()})()
    window._import_worker._cancel_event.set()
    window._on_mdb_import_finished(payload(newer))
    assert window._project is first.state
    assert window._model is first.composed.bars
    window._import_worker = None


def test_setter_failure_restores_models_including_absent_optional_models(window, monkeypatch):
    first = imported(window._project, source=replace(dataset(), cables=None, generators=None))
    window._commit_project_change(first)
    candidate = imported(first.state, dataset(offset=100), FileIdentity("b", "b"))
    setter = window._set_cable_model
    failed = False
    def failing_once(model):
        nonlocal failed
        if model is candidate.composed.cables and not failed:
            failed = True
            raise RuntimeError("Falha controlada na instalação")
        return setter(model)
    monkeypatch.setattr(window, "_set_cable_model", failing_once)
    with pytest.raises(RuntimeError, match="controlada"):
        window._commit_project_change(candidate)
    assert window._project is first.state
    assert window._model is first.composed.bars
    assert window._line_model is first.composed.segments
    assert window._cable_model is None and window._generator_model is None


def test_worker_reads_closes_reviews_and_commits_on_ui_thread(window, qtbot, monkeypatch, tmp_path):
    path = tmp_path / "fake.mdb"
    path.write_bytes(b"fixture")
    calls = []
    @contextmanager
    def open_fake(*args):
        calls.append(("open", QThread.currentThread()))
        try:
            yield network_database(**SWITCH_TYPE_TABLES)
        finally:
            calls.append(("close", QThread.currentThread()))
    monkeypatch.setattr("circuit_viewer.workers.open_database", open_fake)
    worker = MdbImportWorker(str(path), CRS, scale=10, project=window._project)
    thread = QThread(window)
    worker.moveToThread(thread)
    window._import_worker, window._import_thread = worker, thread
    def review(proposal):
        assert calls[-1][0] == "close"
        assert QThread.currentThread() is window.thread()
        worker.resolve_review({item.circuit_id: "update" for item in proposal.feeders})
    window._connect_operation_signal(worker.project_review_required, worker, review)
    done, errors = [], []
    def install(load):
        assert QThread.currentThread() is window.thread()
        window._on_mdb_import_finished(load)
        done.append(load)
    window._connect_operation_signal(worker.finished, worker, install)
    worker.failed.connect(errors.append)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.cancelled.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.start()
    try:
        qtbot.waitUntil(lambda: bool(done or errors), timeout=5000)
        qtbot.waitUntil(lambda: not thread.isRunning(), timeout=5000)
        assert not errors and window._project.revision == 1
        assert calls[0][1] is not window.thread()
        assert window._project.records
    finally:
        if thread.isRunning():
            worker.cancel()
            thread.quit()
            thread.wait(5000)
        window._import_worker = window._import_thread = None
        thread.deleteLater()


def test_stale_signal_does_not_install_into_new_project(window):
    class Signal:
        def connect(self, callback):
            self.callback = callback
    signal = Signal()
    source = object()
    window._power_flow_worker = source
    received = []
    def operation_finished(value):
        received.append(value)
    window._connect_operation_signal(signal, source, operation_finished)
    window._project = ProjectState()
    signal.callback("old")
    assert not received
    window._power_flow_worker = None


def test_provenance_contains_internal_identity_and_field_origin(qtbot):
    project = imported().state
    regulator = row(project, "regulators")
    project = edit_equipment(project, regulator.equipment_id, {"vnom_values": "15"}).state
    dialog = ProjectProvenanceDialog(project, [project.equipment(regulator.equipment_id)])
    qtbot.addWidget(dialog)
    from PyQt6.QtWidgets import QPlainTextEdit
    text = dialog.findChild(QPlainTextEdit).toPlainText()
    assert regulator.equipment_id in text and "manual" in text and "original.mdb" in text
