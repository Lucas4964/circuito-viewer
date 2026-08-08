from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from circuit_viewer.calculation_levels import default_calculation_levels
    from circuit_viewer.circuit_calculation_levels import (
        CircuitCalculationLevelsController,
    )
    from circuit_viewer.circuit_level_import import (
        EXPECTED_CIRCUIT_LEVEL_HEADER,
        parse_circuit_level_rows,
    )
    from circuit_viewer.patamares_window import PatamaresWindow
    from test_circuit_level_import import make_catalog, rows

    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False


def application():  # noqa: ANN201
    return QApplication.instance() or QApplication([])


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CircuitLevelsWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = application()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "patamares.json"
        catalog = make_catalog()
        imported = parse_circuit_level_rows(
            EXPECTED_CIRCUIT_LEVEL_HEADER,
            rows("2") + rows("3", name_suffix=" 3"),
            catalog,
            source_label="mem",
            encoding="ODBC",
        )
        self.controller = CircuitCalculationLevelsController(imported.model)
        self.window = PatamaresWindow(
            default_calculation_levels(), storage_path=self.path
        )
        self.addCleanup(self.window.deleteLater)
        self.assertTrue(self.window.set_circuit_levels(self.controller))

    def select_circuit(self, circuit_index: int) -> None:
        position = self.window.schedule_selector.findData(circuit_index)
        self.assertGreaterEqual(position, 0)
        self.window.schedule_selector.setCurrentIndex(position)

    def test_combo_lists_default_and_only_valid_imported_circuits(self) -> None:
        self.assertEqual(self.window.schedule_selector.count(), 3)
        self.assertEqual(self.window.schedule_selector.itemText(0), "DEFAULT")
        self.assertEqual(self.window.schedule_selector.itemText(1), "2 — 004001")
        self.assertEqual(self.window.schedule_selector.itemText(2), "3 — 004002")

    def test_saving_circuit_changes_only_session_and_not_json(self) -> None:
        self.select_circuit(0)
        self.window.table_model.setData(self.window.table_model.index(0, 1), "Virtual")
        self.assertTrue(self.window._save())
        self.assertFalse(self.path.exists())
        self.assertEqual(self.controller.schedule(0).level(0).name, "Virtual")
        self.assertIn("apenas nesta sessão", self.window.status_label.text())

        self.window.hide()
        self.window.show()
        self.assertEqual(self.window.catalog.draft(0).name, "Virtual")

    def test_default_still_saves_permanently(self) -> None:
        self.window.table_model.setData(self.window.table_model.index(0, 1), "Global")
        self.assertTrue(self.window._save())
        self.assertTrue(self.path.exists())
        self.assertEqual(self.window.saved_schedule.level(0).name, "Global")

    def test_selection_cancel_returns_to_previous_choice(self) -> None:
        self.window.table_model.setData(self.window.table_model.index(0, 1), "Pendente")
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.select_circuit(0)
        self.assertIsNone(self.window.selected_circuit_index)
        self.assertEqual(self.window.schedule_selector.currentText(), "DEFAULT")

    def test_selection_discard_restores_last_session_version(self) -> None:
        self.select_circuit(0)
        self.window.table_model.setData(self.window.table_model.index(0, 1), "Salvo")
        self.assertTrue(self.window._save())
        self.window.table_model.setData(self.window.table_model.index(0, 1), "Descartar")
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            self.window.schedule_selector.setCurrentIndex(0)
        self.select_circuit(0)
        self.assertEqual(self.window.catalog.draft(0).name, "Salvo")

    def test_new_controller_replaces_previous_virtual_edits(self) -> None:
        self.select_circuit(0)
        self.window.table_model.setData(self.window.table_model.index(0, 1), "Virtual")
        self.assertTrue(self.window._save())
        replacement = CircuitCalculationLevelsController(self.controller.model)
        self.assertTrue(self.window.set_circuit_levels(replacement))
        self.assertEqual(replacement.schedule(0).level(0).name, "Madrugada")


if __name__ == "__main__":
    unittest.main()
