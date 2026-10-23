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
        text = self.window.load_energy_label.text()
        self.assertIn("4 carga(s)", text)
        lines = text.splitlines()
        # Uma grandeza por linha, na ordem do painel.
        self.assertTrue(lines[1].startswith("Trafo "))
        self.assertTrue(lines[1].endswith(" (trifásico)"))
        self.assertEqual(
            lines[2:6],
            ["Fase = DEF", "V1 = 13.8 kV", "V2 = 220 V", "S = 30 kVA"],
        )
        self.assertRegex(lines[6], r"^Σ D = \d+\.\d{3} kW$")
        self.window._open_load_energy_table()
        table = self.window._energy_table_window
        self.assertEqual(table.table_model.rowCount(), 4)
        table.close()

    def test_a_transformer_is_single_or_three_phase_never_two(self) -> None:
        kind_and_phases = self.window._transformer_kind_and_phases
        self.assertEqual(kind_and_phases("1"), ("monofásico", "D"))
        self.assertEqual(kind_and_phases("5"), ("monofásico", "E"))
        self.assertEqual(kind_and_phases("6"), ("monofásico", "F"))
        self.assertEqual(kind_and_phases("13"), ("trifásico", "DEF"))
        self.assertEqual(kind_and_phases("14"), ("trifásico", "DEF"))
        # Monofásico entre fases (7/8/9) e duas fases (10/11/12) não existem
        # em trafo: são erro de cadastro, sem fase.
        for code in ("7", "8", "9", "10", "11", "12"):
            with self.subTest(code=code):
                self.assertEqual(
                    kind_and_phases(code), (f"FASES2 {code} inválido para trafo", None)
                )
        self.assertEqual(kind_and_phases("404"), ("FASES2 404 sem relação", None))

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

    def test_the_power_flow_result_survives_without_patterns(self) -> None:
        """Sem NPAT o fluxo leva as cargas; a revalidação precisa ver as mesmas."""

        window = self.window
        window._load_pattern_model = None
        self.assertIsNone(window._exportable_loads())
        loads, patterns = window._power_flow_loads()
        self.assertIs(loads, window._load_model)
        self.assertIsNone(patterns)
        window._power_flow_snapshot = (
            window._circuit_catalog,
            window._cable_model,
            window._phase_configuration,
            loads,
            patterns,
            window._exportable_generators(),
            window._opendss_line_parameter_mode,
        )
        result = mock.Mock()
        with mock.patch.object(window, "_refresh_voltage_classification"), mock.patch.object(
            window, "_apply_circuit_visibility"
        ), mock.patch.object(window, "_show_power_flow_report") as report:
            window._on_power_flow_finished(result)
        self.assertIs(window._power_flow_result, result)
        report.assert_called_once_with(result)

    def test_interplan_keeps_the_registry_generators(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        with mock.patch.object(
            MainWindow, "_ask_load_model_mode", return_value=LoadModelMode.INTERPLAN
        ):
            window._on_mdb_import_finished(self.result)
        self.assertIs(window.generator_virtualizer.model, window._generator_model)
        self.assertIsNone(window._exportable_energy())

    def _export_questions(self, window: MainWindow) -> list[str]:
        """Títulos das perguntas feitas antes do diálogo de exportação."""

        with mock.patch(
            "circuit_viewer.main_window.QMessageBox.question",
            return_value=mock.sentinel.cancel,
        ) as question, mock.patch(
            "circuit_viewer.main_window.OpenDssExportDialog.exec", return_value=0
        ):
            window._export_opendss()
        return [call.args[1] for call in question.call_args_list]

    def test_the_export_report_lists_the_energy_files(self) -> None:
        from circuit_viewer.opendss_export import build_export

        window = self.window
        bundle = build_export(
            window._circuit_catalog,
            window._cable_model,
            window._phase_configuration,
            (0,),
            loads=window._load_model,
            patterns=window._load_pattern_model,
            energy=window._exportable_energy(),
        )
        with mock.patch("circuit_viewer.main_window.QMessageBox") as box:
            window._show_opendss_export_report(bundle, Path("destino"))
        if bundle.has_warnings:
            dialog = box.return_value
            summary = dialog.setText.call_args.args[0]
            details = dialog.setInformativeText.call_args.args[0]
        else:
            summary = details = window.statusBar().currentMessage()
        self.assertIn("trafos do modo Energia", summary)
        if bundle.has_warnings:
            self.assertIn("trafostrifasicos.dss: 1 trafos do modo Energia", details)
            self.assertIn("iluminacaopublica.dss:", details)
            self.assertIn("UCs do modo Energia", details)

    def test_energy_export_does_not_ask_for_the_generator_update(self) -> None:
        """A GD de cada trafo já sai do cálculo do modo Energia."""

        self.assertIsNone(self.window._exportable_generators())
        self.assertFalse(self.window._generators_need_update())
        self.assertNotIn("Exportar sem geradores", self._export_questions(self.window))

    def test_interplan_export_still_asks_for_the_generator_update(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        with mock.patch.object(
            MainWindow, "_ask_load_model_mode", return_value=LoadModelMode.INTERPLAN
        ):
            window._on_mdb_import_finished(self.result)
        self.assertIn("Exportar sem geradores", self._export_questions(window))


if __name__ == "__main__":
    unittest.main()
