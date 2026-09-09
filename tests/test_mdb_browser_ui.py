from __future__ import annotations

from contextlib import contextmanager
import os
import threading
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtWidgets import QDialog

from circuit_viewer.mdb_engine import MdbPasswordError
from circuit_viewer.mdb_import_dialog import MdbImportDialog, MdbSelectionMode
from circuit_viewer.mdb_inspection import inspect_database
from circuit_viewer.workers import MdbInspectionWorker
from tests.test_mdb_inspection import hierarchy_database


@pytest.fixture(autouse=True)
def isolate_default_credential(monkeypatch):
    monkeypatch.setattr("circuit_viewer.mdb_import_dialog.load_default_password", lambda: None)


@pytest.fixture
def dialog(qtbot):
    result = inspect_database(hierarchy_database())
    widget = MdbImportDialog("rede.mdb", result.mapping, result.schema.table_names,
                             schema=result.schema, circuits=result.circuits,
                             substations=result.substations)
    qtbot.addWidget(widget)
    return widget


def activate(dialog, key):
    row = next(i for i, item in enumerate(dialog.substation_model.rows) if item[0] == key)
    dialog.substation_view.setCurrentIndex(dialog.substation_model.index(row, 0))


def choose_rows(view, *rows):
    view.clearSelection()
    for row in rows:
        view.selectionModel().select(view.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def test_initial_layout_and_empty_selection(dialog):
    assert [dialog.tabs.tabText(i) for i in range(3)] == ["Alimentadores", "Tabelas", "Coordenadas"]
    assert dialog.size().width() == 820
    assert dialog.path_input.isReadOnly()
    assert not dialog.load_button.isEnabled()
    assert dialog.available_model.rowCount() == 0
    assert dialog.selected_model.rowCount() == 0
    assert dialog.substation_model.rowCount() == 4
    assert dialog.crs().zone == 21
    assert dialog.coordinate_scale() == 10


def test_accumulate_filter_and_transfer_all_four_buttons(dialog):
    activate(dialog, "1")
    dialog.circuit_filter.setText("AGUA")
    assert dialog.available_model.rowCount() == 1
    dialog.transfer_buttons[">>"].click()
    assert dialog.selected_circuit_ids() == ("2",)
    assert dialog.available_model.rowCount() == 0
    dialog.circuit_filter.clear()
    choose_rows(dialog.available_view, 0)
    dialog.transfer_buttons[">"].click()
    activate(dialog, "2")
    dialog.transfer_buttons[">>"].click()
    assert dialog.selected_circuit_ids() == ("2", "3", "4")
    assert "2 subestações" in dialog.circuit_summary.text()
    dialog.tabs.setCurrentIndex(1)
    dialog.tabs.setCurrentIndex(2)
    dialog.tabs.setCurrentIndex(0)
    assert dialog.selected_circuit_ids() == ("2", "3", "4")
    choose_rows(dialog.selected_view, 0, 2)
    dialog.transfer_buttons["<"].click()
    assert dialog.selected_circuit_ids() == ("3",)
    dialog.transfer_buttons["<<"].click()
    assert dialog.selected_circuit_ids() == ()
    assert not dialog.load_button.isEnabled()


def test_substation_search_ignores_accents_and_does_not_erase_chosen(dialog):
    activate(dialog, "1")
    dialog.transfer_buttons[">>"].click()
    dialog.substation_filter.setText("sao jose")
    assert not dialog.substation_view.isRowHidden(0)
    assert not dialog.substation_view.isRowHidden(1)
    assert dialog.substation_view.isRowHidden(2)
    dialog.substation_filter.setText("3")
    assert dialog._active_substation is None
    assert dialog.available_model.rowCount() == 0
    assert dialog.selected_circuit_ids() == ("2", "3")


def test_empty_and_unlinked_substations(dialog):
    activate(dialog, "3")
    assert dialog.available_model.rowCount() == 0
    activate(dialog, "")
    assert dialog.available_model.rowCount() == 2
    assert "SE_ID" in dialog.available_model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
    dialog.transfer_buttons[">>"].click()
    assert dialog.selected_circuit_ids() == ("5", "6")


def test_double_click_adds_and_removes(dialog):
    activate(dialog, "1")
    choose_rows(dialog.available_view, 0)
    dialog.available_view.doubleClicked.emit(dialog.available_model.index(0, 0))
    assert dialog.selected_circuit_ids() == ("2",)
    choose_rows(dialog.selected_view, 0)
    dialog.selected_view.doubleClicked.emit(dialog.selected_model.index(0, 0))
    assert dialog.selected_circuit_ids() == ()


def test_explicit_whole_database_mode_and_dependencies(dialog):
    activate(dialog, "1")
    dialog.transfer_buttons[">>"].click()
    assert dialog.load_button.isEnabled()
    dialog.entity_checks["trechos"].setChecked(False)
    assert not dialog.load_button.isEnabled()
    dialog.whole_database_check.setChecked(True)
    assert dialog.load_button.isEnabled()
    assert not dialog.circuits_group.isEnabled()
    assert dialog.selection().mode == MdbSelectionMode.DATABASE
    assert dialog.selection().import_circuit_ids() == ()
    dialog.whole_database_check.setChecked(False)
    assert dialog.selected_circuit_ids() == ("2", "3")
    assert not dialog.load_button.isEnabled()


def test_manual_table_override_is_validated_and_recoverable(dialog):
    dialog.whole_database_check.setChecked(True)
    combo = dialog.entity_combos["barras"]
    combo.setCurrentIndex(combo.findData("SE"))
    assert not dialog.load_button.isEnabled()
    assert "BARRA_ID" in dialog.table_status.text()
    combo.setCurrentIndex(combo.findData("BARRA"))
    assert dialog.load_button.isEnabled()


def test_accept_cannot_bypass_empty_selection(dialog):
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    activate(dialog, "1")
    dialog.transfer_buttons[">>"].click()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_inspection_owns_connection_on_background_thread_and_closes_it(qtbot, monkeypatch):
    calls = []
    gui_thread = threading.get_ident()

    @contextmanager
    def open_database(path, password):
        calls.append(("open", threading.get_ident()))
        try:
            yield hierarchy_database()
        finally:
            calls.append(("close", threading.get_ident()))

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    widget.set_path("first.mdb")
    assert not widget.load_button.isEnabled()
    qtbot.waitUntil(lambda: not widget._jobs, timeout=5000)
    assert widget._ready
    assert [call[0] for call in calls] == ["open", "close"]
    assert calls[0][1] == calls[1][1] != gui_thread
    assert widget.substation_model.rowCount() == 4
    assert "SE_TRAFO" in widget.auxiliary_label.text()


def test_replacing_file_discards_late_result_and_resets_controls(qtbot, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    closed = []

    @contextmanager
    def open_database(path, password):
        if "first" in path:
            entered.set()
            release.wait(5)
        try:
            database = hierarchy_database()
            if "second" in path:
                database._tables["SE"] = (["SE_ID", "CODIGO", "NOME"], [(10, "NOVA", "Nova")])
            yield database
        finally:
            closed.append(path)

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    try:
        widget.set_path("first.mdb")
        qtbot.waitUntil(entered.is_set)
        old_id = widget._request_id
        widget.zone_input.setValue(25)
        widget.set_path("second.mdb")
        assert widget.zone_input.value() == 21
        assert not widget.whole_database_check.isChecked()
        qtbot.waitUntil(lambda: widget._ready)
        release.set()
        qtbot.waitUntil(lambda: not widget._jobs)
        widget._inspection_finished(old_id, inspect_database(hierarchy_database()))
        assert widget._substations[0].substation_id == "10"
        assert len(closed) == 2
    finally:
        release.set()
        qtbot.waitUntil(lambda: not widget._jobs, timeout=6000)


def test_close_waits_for_worker_and_never_installs_result(qtbot, monkeypatch):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    @contextmanager
    def open_database(path, password):
        entered.set()
        release.wait(5)
        try:
            yield hierarchy_database()
        finally:
            closed.set()

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    try:
        widget.show()
        widget.set_path("slow.mdb")
        qtbot.waitUntil(entered.is_set)
        widget.close()
        assert widget._closing
        assert widget._jobs
        release.set()
        qtbot.waitUntil(lambda: not widget._jobs)
        assert closed.is_set()
        assert not widget._ready
        assert not widget.isVisible()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not widget._jobs, timeout=6000)


def test_circuit_override_reloads_and_clears_chosen(dialog, qtbot, monkeypatch):
    database = hierarchy_database()
    database._tables["OUTRO"] = database._tables["CIRCUITO"]
    snapshot = inspect_database(database)
    dialog._schema = snapshot.schema
    combo = dialog.entity_combos["circuitos"]
    combo.addItem("OUTRO", "OUTRO")
    activate(dialog, "1")
    dialog.transfer_buttons[">>"].click()

    @contextmanager
    def open_database(path, password):
        yield database

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    combo.setCurrentIndex(combo.findData("OUTRO"))
    assert dialog.selected_circuit_ids() == ()
    assert not dialog.load_button.isEnabled()
    qtbot.waitUntil(lambda: not dialog._jobs)
    assert dialog._mapping.get("circuitos").table == "OUTRO"
    assert len(dialog._circuits) == 5
    assert dialog.selected_circuit_ids() == ()


def test_password_retry_and_cancel(qtbot, monkeypatch):
    attempts, prompts = [], []
    answers = iter(["wrong", "correct"])

    @contextmanager
    def open_database(path, password):
        attempts.append(password)
        if password != "correct":
            raise MdbPasswordError("protected")
        yield hierarchy_database()

    class PasswordDialog:
        def __init__(self, name, parent, *, retry):
            prompts.append(retry)
        def exec(self):
            return QDialog.DialogCode.Accepted
        def password(self):
            return next(answers)

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    monkeypatch.setattr("circuit_viewer.mdb_import_dialog.MdbPasswordDialog", PasswordDialog)
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    widget.set_path("protected.mdb")
    qtbot.waitUntil(lambda: widget._ready and not widget._jobs)
    assert attempts == [None, "wrong", "correct"]
    assert prompts == [False, True]
    assert widget.password() == "correct"
    widget.reject()
    assert widget.password() is None


@pytest.mark.parametrize("stored_password", ["correct", "outdated"])
def test_default_password_is_attempted_once_before_manual_prompt(qtbot, monkeypatch, stored_password):
    attempts = []

    @contextmanager
    def open_database(path, password):
        attempts.append(password)
        if password != "correct":
            raise MdbPasswordError("protected")
        yield hierarchy_database()

    prompt = Mock()
    prompt.exec.return_value = QDialog.DialogCode.Accepted
    prompt.password.return_value = "correct"
    factory = Mock(return_value=prompt)
    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    monkeypatch.setattr("circuit_viewer.mdb_import_dialog.load_default_password", lambda: stored_password)
    monkeypatch.setattr("circuit_viewer.mdb_import_dialog.MdbPasswordDialog", factory)
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    widget.set_path("protected.mdb")
    qtbot.waitUntil(lambda: widget._ready and not widget._jobs)
    assert attempts == ([None, "correct"] if stored_password == "correct" else [None, "outdated", "correct"])
    assert factory.call_count == (0 if stored_password == "correct" else 1)
    assert widget.password() == "correct"
    # Outro arquivo inicia uma nova tentativa, sem reutilizar a senha manual.
    attempts.clear()
    widget.set_path("another.mdb")
    qtbot.waitUntil(lambda: widget._ready and not widget._jobs)
    assert attempts[0:2] == [None, stored_password]


def test_unprotected_database_does_not_need_default_password(qtbot, monkeypatch):
    @contextmanager
    def open_database(path, password):
        assert password is None
        yield hierarchy_database()

    credential = Mock(return_value="saved")
    prompt = Mock()
    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    monkeypatch.setattr("circuit_viewer.mdb_import_dialog.load_default_password", credential)
    monkeypatch.setattr("circuit_viewer.mdb_import_dialog.MdbPasswordDialog", prompt)
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    widget.set_path("public.mdb")
    qtbot.waitUntil(lambda: widget._ready and not widget._jobs)
    credential.assert_not_called()
    prompt.assert_not_called()


def test_worker_failure_never_emits_password(qtbot, monkeypatch):
    @contextmanager
    def open_database(path, password):
        raise ValueError("PWD=secret")
        yield

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    worker = MdbInspectionWorker(7, "file.mdb", password="secret")
    failed, completed = Mock(), Mock()
    worker.failed.connect(failed)
    worker.completed.connect(completed)
    worker.run()
    assert failed.call_args.args[0] == 7
    assert "secret" not in failed.call_args.args[1]
    assert worker._password is None
    completed.assert_called_once()


def test_password_prompt_can_be_cancelled_without_import(qtbot, monkeypatch):
    @contextmanager
    def open_database(path, password):
        raise MdbPasswordError("protected")
        yield

    prompt = Mock()
    prompt.exec.return_value = QDialog.DialogCode.Rejected
    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    monkeypatch.setattr("circuit_viewer.mdb_import_dialog.MdbPasswordDialog", Mock(return_value=prompt))
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    widget.set_path("protected.mdb")
    qtbot.waitUntil(lambda: not widget._jobs)
    assert not widget._ready
    assert not widget.load_button.isEnabled()
    assert widget.password() is None
    assert "cancelada" in widget.warning_label.text()


def test_read_failure_can_recover_by_selecting_another_file(qtbot, monkeypatch):
    from circuit_viewer.mdb_engine import MdbEngineError

    @contextmanager
    def open_database(path, password):
        if "bad" in path:
            raise MdbEngineError("Arquivo não encontrado: bad.mdb")
        yield hierarchy_database()

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    widget.set_path("bad.mdb")
    qtbot.waitUntil(lambda: not widget._jobs)
    assert "Arquivo não encontrado" in widget.warning_label.text()
    assert not widget.load_button.isEnabled()
    widget.set_path("good.mdb")
    qtbot.waitUntil(lambda: not widget._jobs)
    assert widget._ready
    assert not widget._error


@pytest.mark.parametrize("replace", [True, False])
@pytest.mark.parametrize("accepted", [True, False])
def test_main_window_uses_new_dialog_for_replace_and_add(monkeypatch, replace, accepted):
    from circuit_viewer.main_window import MainWindow
    window = Mock()
    window._busy.return_value = False
    window._mdb_error = None
    window._model = object()
    window._mdb_table_mapping = ()
    window.patamares_window.confirm_pending_changes.return_value = True
    dialog = Mock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    dialog.path_input.text.return_value = "chosen.mdb"
    dialog.password.return_value = "in-memory"
    factory = Mock(return_value=dialog)
    monkeypatch.setattr("circuit_viewer.main_window.MdbImportDialog", factory)
    MainWindow._choose_mdb_import(window, replace=replace)
    assert "path" not in factory.call_args.kwargs  # A janela abre antes de escolher o arquivo.
    assert factory.call_args.kwargs["title"] == "Importar banco de dados no projeto"
    if accepted:
        window._start_mdb_import.assert_called_once_with(
            "chosen.mdb", dialog.selection.return_value, "in-memory", replace=False)
    else:
        window._start_mdb_import.assert_not_called()
    dialog.clear_password.assert_called_once()
    dialog.deleteLater.assert_called_once()


def test_accept_waits_for_inspection_thread_cleanup(qtbot, monkeypatch):
    @contextmanager
    def open_database(path, password):
        yield hierarchy_database()

    monkeypatch.setattr("circuit_viewer.workers.open_database", open_database)
    widget = MdbImportDialog()
    qtbot.addWidget(widget)
    original = widget._inspection_finished

    def finish(request_id, result):
        original(request_id, result)
        widget.whole_database_check.setChecked(True)
        widget.accept()

    widget._inspection_finished = finish
    widget.set_path("good.mdb")
    qtbot.waitUntil(lambda: widget.result() == QDialog.DialogCode.Accepted)
    assert not widget._jobs
