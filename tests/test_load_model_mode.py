"""Escolha do modelo de cargas entre a leitura do banco e a instalação."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QDialogButtonBox

    from circuit_viewer.load_model_mode import LoadModelMode, LoadModelModeDialog
    from circuit_viewer.main_window import MainWindow

    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt6
    PYQT_AVAILABLE = False

from tests.test_mdb_import import network_database, run as run_import


def ensure_app():  # noqa: ANN201
    return QApplication.instance() or QApplication([])


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class LoadModelModeDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.dialog = LoadModelModeDialog()
        self.addCleanup(self.dialog.deleteLater)

    def test_offers_both_modes(self) -> None:
        labels = [button.text() for button in self.dialog.buttons.values()]
        self.assertEqual(labels, ["Padrão Interplan", "Energia"])

    def test_starts_on_interplan(self) -> None:
        self.assertIs(self.dialog.selected_mode(), LoadModelMode.INTERPLAN)

    def test_reports_energia_when_chosen(self) -> None:
        self.dialog.buttons[LoadModelMode.ENERGIA].setChecked(True)
        self.assertIs(self.dialog.selected_mode(), LoadModelMode.ENERGIA)
        self.assertFalse(self.dialog.buttons[LoadModelMode.INTERPLAN].isChecked())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class AskLoadModelModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def _answer(self, mode, button) -> None:  # noqa: ANN001
        def respond() -> None:
            dialog = QApplication.activeModalWidget()
            if mode is not None:
                dialog.buttons[mode].setChecked(True)
            dialog.findChild(QDialogButtonBox).button(button).click()

        QTimer.singleShot(0, respond)

    def test_returns_the_chosen_mode(self) -> None:
        self._answer(LoadModelMode.ENERGIA, QDialogButtonBox.StandardButton.Ok)
        self.assertIs(self.window._ask_load_model_mode(), LoadModelMode.ENERGIA)

    def test_cancel_returns_none(self) -> None:
        self._answer(None, QDialogButtonBox.StandardButton.Cancel)
        self.assertIsNone(self.window._ask_load_model_mode())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class ImportAsksBeforeInstallingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.result = run_import(network_database())

    def _ask(self, mode):  # noqa: ANN001, ANN202
        # Confere que a pergunta acontece com o mapa ainda vazio.
        def ask(window):  # noqa: ANN001, ANN202
            self.assertIsNone(window._model)
            return mode

        return mock.patch.object(MainWindow, "_ask_load_model_mode", ask)

    def test_interplan_installs_the_same_models(self) -> None:
        with self._ask(LoadModelMode.INTERPLAN):
            self.window._on_mdb_import_finished(self.result)
        self.assertIs(self.window._model, self.result.bars.model)
        self.assertIs(self.window._load_model, self.result.loads.model)
        self.assertIs(self.window._load_model_mode, LoadModelMode.INTERPLAN)

    def test_energia_is_remembered(self) -> None:
        with self._ask(LoadModelMode.ENERGIA):
            self.window._on_mdb_import_finished(self.result)
        self.assertIs(self.window._load_model, self.result.loads.model)
        self.assertIs(self.window._load_model_mode, LoadModelMode.ENERGIA)

    def test_cancel_discards_the_import(self) -> None:
        with self._ask(None):
            self.window._on_mdb_import_finished(self.result)
        self.assertIsNone(self.window._model)
        self.assertIsNone(self.window._load_model)
        self.assertIs(self.window._load_model_mode, LoadModelMode.INTERPLAN)

    def test_cancel_keeps_the_previous_map(self) -> None:
        with self._ask(LoadModelMode.ENERGIA):
            self.window._on_mdb_import_finished(self.result)
        previous = self.window._model
        with mock.patch.object(MainWindow, "_ask_load_model_mode", return_value=None):
            self.window._on_mdb_import_finished(run_import(network_database()))
        self.assertIs(self.window._model, previous)
        self.assertIs(self.window._load_model_mode, LoadModelMode.ENERGIA)

    def test_a_database_without_loads_does_not_ask(self) -> None:
        database = network_database()
        del database._tables["CARGA"]
        result = run_import(database)
        ask = mock.Mock(return_value=None)
        with mock.patch.object(MainWindow, "_ask_load_model_mode", ask):
            self.window._on_mdb_import_finished(result)
        ask.assert_not_called()
        self.assertIs(self.window._model, result.bars.model)


if __name__ == "__main__":
    unittest.main()
