"""Escolha do modelo de cargas entre a leitura do banco e a instalação."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QDialogButtonBox

    from circuit_viewer.load_model_mode import LoadModelMode, LoadModelModeDialog
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import FeatureSelection

    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt6
    PYQT_AVAILABLE = False

from tests.test_mdb_import import network_database, run as run_import

try:
    from tempfile import TemporaryDirectory

    from circuit_viewer.curvas_store import categories_path_for, save_curve_categories, save_curves
    from circuit_viewer.transformer_consumers import TransformerGenerationModel
    from tests.test_energy_demand import CATEGORIES, CURVES
    from tests.test_opendss_energy_export import MT_GENERATOR_WITH_TYPE
    from tests.test_transformer_consumers import extended_tables
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt6
    pass


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

    def test_energia_without_consumers_warns_and_stays_interplan(self) -> None:
        with self._ask(LoadModelMode.ENERGIA), mock.patch(
            "circuit_viewer.main_window.QMessageBox.warning"
        ) as warning:
            self.window._on_mdb_import_finished(self.result)
        warning.assert_called_once()
        self.assertIn("Padrão Interplan", warning.call_args.args[2])
        self.assertIs(self.window._load_model, self.result.loads.model)
        self.assertIs(self.window._load_model_mode, LoadModelMode.INTERPLAN)
        self.assertIsNone(self.window._energy_model)

    def test_cancel_discards_the_import(self) -> None:
        with self._ask(None):
            self.window._on_mdb_import_finished(self.result)
        self.assertIsNone(self.window._model)
        self.assertIsNone(self.window._load_model)
        self.assertIs(self.window._load_model_mode, LoadModelMode.INTERPLAN)

    def test_cancel_keeps_the_previous_map(self) -> None:
        with self._ask(LoadModelMode.INTERPLAN):
            self.window._on_mdb_import_finished(self.result)
        previous = self.window._model
        with mock.patch.object(MainWindow, "_ask_load_model_mode", return_value=None):
            self.window._on_mdb_import_finished(run_import(network_database()))
        self.assertIs(self.window._model, previous)
        self.assertIs(self.window._load_model_mode, LoadModelMode.INTERPLAN)

    def test_a_database_without_loads_does_not_ask(self) -> None:
        database = network_database()
        del database._tables["CARGA"]
        result = run_import(database)
        ask = mock.Mock(return_value=None)
        with mock.patch.object(MainWindow, "_ask_load_model_mode", ask):
            self.window._on_mdb_import_finished(result)
        ask.assert_not_called()
        self.assertIs(self.window._model, result.bars.model)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class EnergyModeWindowTests(unittest.TestCase):
    """O modo Energia na janela: cálculo, símbolo agregado e tabelas."""

    def setUp(self) -> None:
        self.app = ensure_app()
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        curves_path = Path(directory.name) / "curvas.json"
        save_curves(CURVES, curves_path)
        save_curve_categories(CATEGORIES, categories_path_for(curves_path))
        self.window = MainWindow(curves_path=curves_path)
        self.addCleanup(self.window.close)
        tables = extended_tables()
        tables["MT_GERADOR_CONS"] = MT_GENERATOR_WITH_TYPE
        self.result = run_import(network_database(**tables))
        with mock.patch.object(
            MainWindow, "_ask_load_model_mode", return_value=LoadModelMode.ENERGIA
        ), mock.patch("circuit_viewer.main_window.QMessageBox.warning") as warning:
            self.window._on_mdb_import_finished(self.result)
        self.warning = warning

    def test_the_energy_model_is_calculated(self) -> None:
        self.assertIs(self.window._load_model_mode, LoadModelMode.ENERGIA)
        model = self.window._energy_model
        self.assertIsNotNone(model)
        self.assertIs(model.consumers, self.window._consumers_model)
        self.assertIs(self.window._exportable_energy(), model)
        self.warning.assert_not_called()

    def test_the_generator_layer_shows_one_symbol_per_transformer(self) -> None:
        display = self.window.generator_virtualizer.model
        self.assertIsInstance(display, TransformerGenerationModel)
        self.assertEqual(len(display), 1)
        self.assertIn("trafo", self.window.generator_status.text())

    def test_clicking_the_aggregate_opens_its_page_and_table(self) -> None:
        self.window._set_selection(FeatureSelection("generator", 0))
        self.assertEqual(self.window._selected_feature.kind, "transformer_generation")
        self.assertIs(
            self.window.details_stack.currentWidget(),
            self.window.transformer_generation_page,
        )
        self.assertIn("2 gerador(es)", self.window.transformer_generation_label.text())
        self.window._open_generation_energy_table()
        table = self.window._energy_table_window
        self.assertEqual(table.table_model.rowCount(), 2)
        table.close()

    def test_the_load_panel_lists_the_connected_loads(self) -> None:
        self.window._set_selection(FeatureSelection("load", 0))
        self.assertTrue(self.window.load_energy_section.isVisibleTo(self.window.load_details_body))
        self.assertIn("4 carga(s)", self.window.load_energy_label.text())
        self.window._open_load_energy_table()
        table = self.window._energy_table_window
        self.assertEqual(table.table_model.rowCount(), 4)
        table.close()

    def test_reinstalling_the_network_recalculates(self) -> None:
        """Acrescentar ou remover fonte e editar o projeto passam por aqui."""

        before = self.window._energy_model
        self.window._install_composed_chain(
            self.window._composition, self.window._workspace, adopt_project=False
        )
        after = self.window._energy_model
        self.assertIsNotNone(after)
        self.assertIsNot(after, before)
        self.assertIs(self.window._exportable_energy(), after)

    def test_interplan_keeps_the_registry_generators(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        with mock.patch.object(
            MainWindow, "_ask_load_model_mode", return_value=LoadModelMode.INTERPLAN
        ):
            window._on_mdb_import_finished(self.result)
        self.assertIs(window.generator_virtualizer.model, window._generator_model)
        self.assertIsNone(window._exportable_energy())


if __name__ == "__main__":
    unittest.main()
