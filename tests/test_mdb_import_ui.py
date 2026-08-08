"""Testes de interface da importação por banco de dados.

A leitura do banco é testada em ``test_mdb_engine`` e a orquestração em
``test_mdb_import``; aqui o ``MdbImportResult`` é injetado direto, do mesmo modo
que ``test_powerflow_ui`` injeta um ``PowerFlowResult``. Nenhum teste exige
``pyodbc`` nem driver ODBC.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QDialogButtonBox

    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.mdb_import import MdbEntityOutcome, MdbImportResult
    from circuit_viewer.mdb_import_dialog import (
        AUTOMATIC_LABEL,
        MdbImportDialog,
        MdbPasswordDialog,
    )
    from circuit_viewer.mdb_import_report import (
        MdbImportReportWindow,
        issue_lines,
    )
    from circuit_viewer.mdb_mapping import (
        ENTITY_ORDER,
        GENERATOR_CONSUMER_ENTITY,
        resolve_mapping,
    )
    from circuit_viewer.model import UtmCrs

    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt6
    PYQT_AVAILABLE = False

from tests.test_mdb_import import network_database, run as run_import


CRS = UtmCrs(21, northern=False) if PYQT_AVAILABLE else None


def ensure_app():  # noqa: ANN201
    return QApplication.instance() or QApplication([])


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class MdbImportActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_the_action_lives_in_the_file_menu(self) -> None:
        file_menu = next(
            action.menu()
            for action in self.window.menuBar().actions()
            if action.text() == "Arquivo"
        )
        self.assertIn(self.window.mdb_import_action, file_menu.actions())

    def test_the_tooltip_explains_read_only(self) -> None:
        tooltip = self.window.mdb_import_action.toolTip()
        self.assertIn("somente leitura", tooltip)

    def test_a_busy_window_ignores_the_action(self) -> None:
        self.window._import_thread = object()
        try:
            # Não deve abrir diálogo algum nem levantar.
            self.window._choose_mdb_import()
        finally:
            self.window._import_thread = None

    def test_the_action_is_disabled_without_the_library(self) -> None:
        self.window._mdb_error = "pyodbc ausente"
        self.window.mdb_import_action.setEnabled(False)
        self.window._choose_mdb_import()
        self.assertFalse(self.window.mdb_import_action.isEnabled())

    def test_the_action_is_re_enabled_after_an_import(self) -> None:
        self.window.mdb_import_action.setEnabled(False)
        self.window._on_import_thread_finished()
        self.assertTrue(self.window.mdb_import_action.isEnabled())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class InstallResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.result = run_import(network_database())

    def test_installs_every_model(self) -> None:
        self.window._on_mdb_import_finished(self.result)
        self.assertIs(self.window._model, self.result.bars.model)
        self.assertIs(self.window._line_model, self.result.segments.model)
        self.assertIs(self.window._load_model, self.result.loads.model)
        self.assertIs(self.window._generator_model, self.result.generators.model)
        self.assertIs(self.window._load_pattern_model, self.result.patterns.model)
        self.assertIs(self.window._switch_model, self.result.switches.model)
        self.assertIs(self.window._regulator_model, self.result.regulators.model)
        self.assertIs(self.window._cable_model, self.result.cables.model)
        self.assertIs(self.window._circuit_catalog, self.result.circuits.model)

    def test_the_catalog_survives_the_switch_cascade(self) -> None:
        # _set_switch_model reconstrói o catálogo; instalá-lo antes dos
        # circuitos é o que evita perder as associações recém-calculadas.
        self.window._on_mdb_import_finished(self.result)
        self.assertIsNotNone(self.window._circuit_catalog)
        self.assertIs(
            self.window._circuit_catalog.switches, self.result.switches.model
        )

    def test_enables_the_view_actions(self) -> None:
        self.window._on_mdb_import_finished(self.result)
        self.assertTrue(self.window.fit_action.isEnabled())
        self.assertTrue(self.window.show_bars_action.isEnabled())
        self.assertTrue(self.window.search_action.isEnabled())

    def test_a_partial_database_installs_what_it_has(self) -> None:
        database = network_database()
        del database._tables["CHAVE"]
        del database._tables["REGULADOR"]
        result = run_import(database)
        self.window._on_mdb_import_finished(result)
        self.assertIsNotNone(self.window._line_model)
        self.assertIsNone(self.window._switch_model)
        self.assertIsNone(self.window._regulator_model)
        self.assertIsNotNone(self.window._circuit_catalog)

    def test_a_second_import_replaces_the_previous_models(self) -> None:
        self.window._on_mdb_import_finished(self.result)
        first = self.window._model
        second = run_import(network_database())
        self.window._on_mdb_import_finished(second)
        self.assertIsNot(self.window._model, first)
        # A cascata precisa ter trocado os dependentes junto.
        self.assertIs(self.window._line_model.bars, self.window._model)

    def test_bars_only_clears_the_previous_dependents(self) -> None:
        self.window._on_mdb_import_finished(self.result)
        database = network_database()
        for table in (
            "TRECHO", "CARGA", "MT_GERADOR_CONS", "MT_CONS",
            "MODELO_CARGA", "CHAVE", "REGULADOR", "CIRCUITO",
        ):
            del database._tables[table]
        self.window._on_mdb_import_finished(run_import(database))
        self.assertIsNone(self.window._line_model)
        self.assertIsNone(self.window._load_model)
        self.assertIsNone(self.window._generator_model)
        self.assertIsNone(self.window._circuit_catalog)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class ImportDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.database = network_database()
        self.plan = resolve_mapping(self.database)
        self.dialog = MdbImportDialog(
            r"C:\dados\rede.mdb",
            self.plan,
            self.database.tables(),
            suggested_scale=10.0,
        )
        self.addCleanup(self.dialog.close)

    def test_every_detected_entity_starts_checked(self) -> None:
        self.assertEqual(self.dialog.selected_entities(), ENTITY_ORDER)

    def test_the_detected_table_is_preselected(self) -> None:
        self.assertEqual(
            self.dialog.entity_combos["barras"].currentData(), "BARRA"
        )
        self.assertEqual(
            self.dialog.entity_combos["patamares"].currentData(), "MODELO_CARGA"
        )
        self.assertEqual(
            self.dialog.entity_combos["geradores"].currentData(),
            "MT_GERADOR_CONS",
        )
        self.assertEqual(
            self.dialog.entity_combos[GENERATOR_CONSUMER_ENTITY].currentData(),
            "MT_CONS",
        )

    def test_generators_are_one_checkbox_with_two_table_selectors(self) -> None:
        self.assertIn("geradores", self.dialog.entity_checks)
        self.assertIn("geradores", self.dialog.entity_combos)
        self.assertIn(GENERATOR_CONSUMER_ENTITY, self.dialog.entity_combos)
        self.assertEqual(
            sum(1 for entity in self.dialog.entity_checks if entity == "geradores"),
            1,
        )

    def test_the_suggested_unit_is_preselected(self) -> None:
        self.assertEqual(self.dialog.coordinate_scale(), 10.0)

    def test_an_undetected_entity_is_disabled_with_the_reason(self) -> None:
        database = network_database()
        del database._tables["REGULADOR"]
        dialog = MdbImportDialog(
            r"C:\dados\rede.mdb", resolve_mapping(database), database.tables()
        )
        self.addCleanup(dialog.close)
        self.assertNotIn("reguladores", dialog.selected_entities())
        self.assertFalse(dialog.entity_checks["reguladores"].isEnabled())
        self.assertIn("REGULADOR", dialog.entity_checks["reguladores"].toolTip())

    def test_choosing_a_table_by_hand_re_enables_an_entity(self) -> None:
        database = network_database()
        del database._tables["REGULADOR"]
        database._tables["REGU"] = network_database()._tables["REGULADOR"]
        dialog = MdbImportDialog(
            r"C:\dados\rede.mdb", resolve_mapping(database), database.tables()
        )
        self.addCleanup(dialog.close)
        combo = dialog.entity_combos["reguladores"]
        combo.setCurrentIndex(combo.findData("REGU"))
        self.assertIn("reguladores", dialog.selected_entities())
        self.assertEqual(dialog.overrides()["reguladores"], "REGU")

    def test_generators_need_both_tables_and_allow_independent_overrides(self) -> None:
        database = network_database()
        del database._tables["MT_CONS"]
        database._tables["CONS_ALT"] = network_database()._tables["MT_CONS"]
        dialog = MdbImportDialog(
            r"C:\dados\rede.mdb", resolve_mapping(database), database.tables()
        )
        self.addCleanup(dialog.close)
        self.assertNotIn("geradores", dialog.selected_entities())
        helper = dialog.entity_combos[GENERATOR_CONSUMER_ENTITY]
        helper.setCurrentIndex(helper.findData("CONS_ALT"))
        self.assertIn("geradores", dialog.selected_entities())
        self.assertEqual(
            dialog.overrides()[GENERATOR_CONSUMER_ENTITY], "CONS_ALT"
        )

    def test_overrides_are_empty_when_the_detection_is_kept(self) -> None:
        self.assertEqual(self.dialog.overrides(), {})

    def test_the_automatic_item_produces_no_override(self) -> None:
        combo = self.dialog.entity_combos["cabos"]
        combo.setCurrentIndex(combo.findText(AUTOMATIC_LABEL))
        self.assertNotIn("cabos", self.dialog.overrides())

    def test_unchecking_an_entity_removes_it_from_the_selection(self) -> None:
        self.dialog.entity_checks["patamares"].setChecked(False)
        self.assertNotIn("patamares", self.dialog.selected_entities())

    def test_bars_are_required_to_confirm(self) -> None:
        ok = self.dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.assertTrue(ok.isEnabled())
        self.dialog.entity_checks["barras"].setChecked(False)
        self.assertFalse(ok.isEnabled())
        self.assertIn("obrigatórias", self.dialog.warning_label.text())

    def test_the_selection_carries_everything(self) -> None:
        self.dialog.zone_input.setValue(22)
        selection = self.dialog.selection()
        self.assertEqual(selection.crs, UtmCrs(22, northern=False))
        self.assertEqual(selection.scale, 10.0)
        self.assertEqual(selection.entities, ENTITY_ORDER)

    def test_the_selection_repr_never_carries_a_password(self) -> None:
        # A senha não passa por aqui, e o repr é o que aparece num traceback.
        self.assertNotIn("password", repr(self.dialog.selection()))


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class PasswordDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()

    def test_the_field_is_masked(self) -> None:
        dialog = MdbPasswordDialog("rede.mdb")
        self.addCleanup(dialog.close)
        from PyQt6.QtWidgets import QLineEdit

        self.assertEqual(
            dialog.password_input.echoMode(), QLineEdit.EchoMode.Password
        )

    def test_the_retry_message_says_the_password_is_wrong(self) -> None:
        dialog = MdbPasswordDialog("rede.mdb", retry=True)
        self.addCleanup(dialog.close)
        texts = [
            child.text()
            for child in dialog.findChildren(type(dialog.password_input).__mro__[1])
            if hasattr(child, "text")
        ]
        self.assertTrue(any("não confere" in text for text in texts if text))

    def test_returns_the_typed_password(self) -> None:
        dialog = MdbPasswordDialog("rede.mdb")
        self.addCleanup(dialog.close)
        dialog.password_input.setText("segredo")
        self.assertEqual(dialog.password(), "segredo")


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()

    def test_one_row_per_entity(self) -> None:
        result = run_import(network_database())
        window = MdbImportReportWindow(result)
        self.addCleanup(window.close)
        self.assertEqual(window.table_model.rowCount(), len(ENTITY_ORDER))

    def test_generator_report_has_one_row_naming_both_tables(self) -> None:
        result = run_import(network_database())
        outcome = result.outcome_for("geradores")
        self.assertEqual(outcome.table, "MT_GERADOR_CONS + MT_CONS")
        self.assertEqual(
            sum(item.entity == "geradores" for item in result.outcomes), 1
        )

    def test_a_failure_shows_its_reason_in_the_situation_column(self) -> None:
        database = network_database()
        del database._tables["REGULADOR"]
        result = run_import(database)
        window = MdbImportReportWindow(result)
        self.addCleanup(window.close)
        situations = [
            window.table_model.data(window.table_model.index(row, 5))
            for row in range(window.table_model.rowCount())
        ]
        self.assertTrue(any("REGULADOR" in text for text in situations))

    def test_issue_lines_name_the_entity(self) -> None:
        database = network_database()
        del database._tables["CHAVE"]
        lines = issue_lines(run_import(database))
        self.assertTrue(any(line.startswith("Chaves:") for line in lines))

    def test_a_clean_import_has_no_issue_lines(self) -> None:
        self.assertEqual(issue_lines(run_import(network_database())), ())

    def test_the_issue_panel_is_hidden_when_there_is_nothing_to_say(self) -> None:
        window = MdbImportReportWindow(run_import(network_database()))
        self.addCleanup(window.close)
        self.assertFalse(window.issues_view.isVisible())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class ReportVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_a_clean_import_only_touches_the_status_bar(self) -> None:
        result = run_import(network_database())
        self.assertFalse(result.has_warnings)
        self.window._show_mdb_import_report(result)
        self.assertIn("Banco importado", self.window.statusBar().currentMessage())
        # Sem avisos não há relatório para abrir.
        self.assertIsNone(self.window._mdb_report_window)

    def test_a_partial_import_opens_the_report_without_blocking(self) -> None:
        database = network_database()
        del database._tables["REGULADOR"]
        result = run_import(database)
        self.assertTrue(result.has_warnings)
        self.window._show_mdb_import_report(result)
        window = self.window._mdb_report_window
        self.assertIsNotNone(window)
        self.addCleanup(window.close)
        # Não modal, como o relatório de sobreposições.
        self.assertFalse(window.isModal())

    def test_a_second_report_replaces_the_first(self) -> None:
        database = network_database()
        del database._tables["REGULADOR"]
        result = run_import(database)
        self.window._show_mdb_import_report(result)
        first = self.window._mdb_report_window
        self.window._show_mdb_import_report(result)
        self.addCleanup(self.window._mdb_report_window.close)
        self.assertIsNot(self.window._mdb_report_window, first)


class OutcomeTests(unittest.TestCase):
    """Não dependem de PyQt6."""

    def test_an_outcome_without_error_is_imported(self) -> None:
        outcome = MdbEntityOutcome("barras", "BARRA", 10, 10, 0)
        self.assertTrue(outcome.imported)
        self.assertEqual(outcome.label, "Barras")

    def test_an_outcome_with_an_error_is_not_imported(self) -> None:
        outcome = MdbEntityOutcome("chaves", None, error="Não encontrada.")
        self.assertFalse(outcome.imported)

    def test_the_result_lists_only_real_failures(self) -> None:
        result = MdbImportResult(
            source_path="x.mdb",
            bars=run_import(network_database()).bars,
            outcomes=(
                MdbEntityOutcome("barras", "BARRA", 3, 3, 0),
                MdbEntityOutcome("chaves", None, error="ausente"),
            ),
        )
        self.assertEqual(tuple(item.entity for item in result.failures), ("chaves",))
        self.assertEqual(result.imported_entities, ("barras",))


if __name__ == "__main__":
    unittest.main()
