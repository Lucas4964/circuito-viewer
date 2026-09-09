"""Contrato do worker, revisão explícita e instalação do cadastro na janela."""

from contextlib import contextmanager
import threading
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialogButtonBox

from circuit_viewer.main_window import MainWindow
from circuit_viewer.mdb_import_dialog import MdbImportDialog
from circuit_viewer.network_registry import FileIdentity, IdentityMapping, register_import
from circuit_viewer.network_registry_dialog import NetworkConflictDialog, NetworkMappingDialog
from circuit_viewer.source_composition import SourceWorkspace
from circuit_viewer.workers import MdbImportWorker
from tests.test_mdb_import import network_database
from tests.test_source_composition import CRS, SWITCH_TYPE_TABLES, dataset


@pytest.fixture
def import_bank(qtbot, monkeypatch, tmp_path):
    path = tmp_path / "rede.mdb"
    path.write_bytes(b"database fixture")
    db = network_database(**SWITCH_TYPE_TABLES)

    @contextmanager
    def open_database(*args):
        yield db

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)

    def run(workspace=None, *, replace_workspace=False, review=None):
        worker = MdbImportWorker(str(path), CRS, scale=10, workspace=workspace,
                                 replace_workspace=replace_workspace, password="memory-only")
        finished, failed, cancelled = [], [], []
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)
        worker.cancelled.connect(lambda: cancelled.append(True))
        if review:
            worker.review_required.connect(lambda conflicts: worker.resolve_review(review(conflicts)))
        worker.run()
        assert worker._password is None
        return worker, finished, failed, cancelled

    return path, db, run


def test_worker_reimport_installs_same_network_and_preserves_display_choices(qtbot, monkeypatch, import_bank):
    _, _, run = import_bank
    _, loaded, failed, cancelled = run()
    assert loaded and not failed and not cancelled
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_show_mdb_import_report", Mock())
    monkeypatch.setattr(window, "_show_composition_report", Mock())
    monkeypatch.setattr(window, "_show_circuits_window", Mock())
    window._on_mdb_import_finished(loaded[0])
    state = window._circuit_display_state()
    window._workspace = window._workspace.renamed(loaded[0].dataset.tag, "Minha rede")
    _, again, failed, _ = run(window._workspace)
    assert not failed
    window._on_mdb_import_finished(again[0])
    assert len(window._workspace) == 1
    assert len(window._model) == 3
    assert window._workspace.datasets[0].name == "Minha rede"
    assert window._circuit_display_state() == state
    assert window._composition.provenance.equipment_key("bars", 0).network_id.startswith("access:")


def test_conflict_review_blocks_accept_until_all_records_are_resolved(qtbot):
    source = dataset()
    identity = FileIdentity("a.mdb", "a")
    workspace, _ = register_import(SourceWorkspace(), source, identity)
    changed = dataset(offset=100)
    seen = []
    mappings = (IdentityMapping("bars", "107", "7"),)

    def review(conflicts):
        seen.extend(conflicts)
        return {item.key: "incoming" for item in conflicts}

    register_import(workspace, changed, FileIdentity("b.mdb", "b"), target_tag="F1",
                    correspondences=mappings, resolve_conflicts=review)
    dialog = NetworkConflictDialog(seen)
    qtbot.addWidget(dialog)
    accept = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not accept.isEnabled()
    assert "codes" in dialog.model.data(dialog.model.index(0, 1))
    assert "Origem:" in dialog.model.data(dialog.model.index(0, 2), Qt.ItemDataRole.ToolTipRole)
    dialog.model.choose_all("existing")
    assert accept.isEnabled()
    dialog.model.setData(dialog.model.index(0, 4), None)
    assert not accept.isEnabled()
    dialog.model.setData(dialog.model.index(0, 4), "incoming")
    assert accept.isEnabled()


def test_mapping_controls_are_explicit_and_reset_on_target_change(qtbot):
    workspace, _ = register_import(SourceWorkspace(), dataset(), FileIdentity("a.mdb", "a"))
    widget = MdbImportDialog(workspace=workspace)
    qtbot.addWidget(widget)
    assert widget.network_combo.currentData() is None
    assert not widget.mapping_button.isEnabled()
    widget.network_combo.setCurrentIndex(1)
    assert widget.mapping_button.isEnabled()
    mapping = IdentityMapping("bars", "107", "7")
    editor = NetworkMappingDialog((mapping,))
    qtbot.addWidget(editor)
    assert editor.mappings() == (mapping,)
    widget._identity_mappings = editor.mappings()
    selection = widget.selection()
    assert selection.target_tag == "F1"
    assert selection.correspondences == (mapping,)
    widget.network_combo.setCurrentIndex(0)
    assert widget.selection().correspondences == ()


def test_worker_cancel_while_waiting_for_review_returns_without_install(qtbot, import_bank):
    path, _, run = import_bank
    _, loaded, _, _ = run()
    worker = MdbImportWorker(str(path), CRS, scale=1, workspace=loaded[0].workspace,
                             replace_workspace=False)  # Same IDs, different coordinates -> review.
    requested, finished, cancelled = [], [], []
    worker.review_required.connect(requested.append)
    worker.finished.connect(finished.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        qtbot.waitUntil(lambda: bool(requested), timeout=5000)
        worker.cancel()
        qtbot.waitUntil(lambda: bool(cancelled), timeout=5000)
    finally:
        worker.cancel()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not finished
    assert len(loaded[0].workspace.datasets[0].bars) == 3


def test_worker_rejects_a_file_that_changes_during_read(qtbot, monkeypatch, import_bank):
    path, db, run = import_bank

    @contextmanager
    def changing_database(*args):
        yield db
        path.write_bytes(b"modified during read")

    monkeypatch.setattr("circuit_viewer.workers.open_database", changing_database)
    _, loaded, failed, cancelled = run()
    assert not loaded and not cancelled
    assert "mudou durante" in failed[0]


def test_worker_read_failure_never_installs_or_exposes_password(qtbot, monkeypatch, import_bank):
    _, _, run = import_bank

    @contextmanager
    def broken_database(*args):
        raise ValueError("Failure with memory-only password")
        yield

    monkeypatch.setattr("circuit_viewer.workers.open_database", broken_database)
    _, loaded, failed, _ = run()
    assert not loaded
    assert "memory-only" not in failed[0]


def test_main_window_review_delivers_decisions_and_cancellation(monkeypatch):
    from PyQt6.QtWidgets import QDialog

    for accepted in (False, True):
        window, worker, dialog = Mock(), Mock(), Mock()
        window._import_worker = worker
        worker._cancel_event.is_set.return_value = False
        dialog.exec.return_value = QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
        monkeypatch.setattr("circuit_viewer.network_registry_dialog.NetworkConflictDialog", Mock(return_value=dialog))
        MainWindow._review_network_import(window, worker, ())
        worker.resolve_review.assert_called_once_with(dialog.model.decisions if accepted else None)
