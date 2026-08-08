from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox, QSpinBox, QStyleOptionViewItem

    from circuit_viewer.calculation_levels import default_calculation_levels
    from circuit_viewer.calculation_levels_store import load_calculation_levels
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.patamares_window import PatamaresWindow

    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False


def application():  # noqa: ANN201
    return QApplication.instance() or QApplication([])


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class PatamaresWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = application()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "patamares.json"
        self.window = PatamaresWindow(
            default_calculation_levels(), storage_path=self.path
        )
        self.addCleanup(self.window.deleteLater)
        self.window.show()

    def test_table_has_four_rows_five_exact_headers_and_every_cell_is_editable(self) -> None:
        model = self.window.table_model
        self.assertEqual(model.rowCount(), 4)
        self.assertEqual(model.columnCount(), 5)
        self.assertEqual(
            [model.headerData(column, Qt.Orientation.Horizontal) for column in range(5)],
            ["NPAT", "NOME", "HORARIO_INI", "HORARIO_FIM", "HORARIO_REF"],
        )
        for row in range(4):
            for column in range(5):
                self.assertTrue(
                    bool(model.flags(model.index(row, column)) & Qt.ItemFlag.ItemIsEditable)
                )

    def test_numeric_editors_enforce_npat_and_hour_ranges(self) -> None:
        option = QStyleOptionViewItem()
        npat_editor = self.window.number_delegate.createEditor(
            self.window.table, option, self.window.table_model.index(0, 0)
        )
        hour_editor = self.window.number_delegate.createEditor(
            self.window.table, option, self.window.table_model.index(0, 2)
        )
        self.assertIsInstance(npat_editor, QSpinBox)
        self.assertEqual((npat_editor.minimum(), npat_editor.maximum()), (-1, 3))
        self.assertEqual((hour_editor.minimum(), hour_editor.maximum()), (-1, 23))
        self.assertEqual(npat_editor.specialValueText(), "")
        self.assertEqual(hour_editor.specialValueText(), "")

    def test_editing_is_dirty_but_does_not_mutate_saved_schedule(self) -> None:
        saved = self.window.saved_schedule
        index = self.window.table_model.index(0, 1)
        self.assertTrue(self.window.table_model.setData(index, "Editado"))
        self.assertTrue(self.window.is_dirty)
        self.assertEqual(saved.level(0).name, "Madrugada")
        self.assertEqual(self.window.saved_schedule.level(0).name, "Madrugada")

    def test_invalid_joint_state_is_kept_for_correction_and_not_written(self) -> None:
        self.window.table_model.setData(self.window.table_model.index(1, 0), 0)
        self.assertFalse(self.window._save())
        self.assertIn("NPAT", self.window.status_label.text())
        self.assertFalse(self.path.exists())
        self.assertTrue(self.window.is_dirty)

    def test_numeric_cells_may_be_temporarily_empty_but_cannot_be_saved(self) -> None:
        index = self.window.table_model.index(0, 2)
        self.assertTrue(self.window.table_model.setData(index, ""))
        self.assertEqual(self.window.catalog.draft(0).start_hour, None)
        self.assertFalse(self.window._save())
        self.assertFalse(self.path.exists())

    def test_save_persists_emits_and_clears_dirty(self) -> None:
        received = []
        self.window.scheduleSaved.connect(received.append)
        self.window.table_model.setData(
            self.window.table_model.index(0, 1), "Madrugada especial"
        )
        self.assertTrue(self.window._save())
        self.assertFalse(self.window.is_dirty)
        self.assertEqual(len(received), 1)
        self.assertEqual(
            load_calculation_levels(self.path).schedule.level(0).name,
            "Madrugada especial",
        )

    def test_save_sorts_rows_by_npat(self) -> None:
        first = self.window.catalog.draft(0)
        last = self.window.catalog.draft(3)
        first.npat, last.npat = 3, 0
        first.name, last.name = last.name, first.name
        first.start_hour, last.start_hour = last.start_hour, first.start_hour
        first.end_hour, last.end_hour = last.end_hour, first.end_hour
        first.reference_hour, last.reference_hour = (
            last.reference_hour,
            first.reference_hour,
        )
        self.window._mark_dirty()
        self.assertTrue(self.window._save())
        self.assertEqual(
            [
                self.window.table_model.data(
                    self.window.table_model.index(row, 0)
                )
                for row in range(4)
            ],
            [0, 1, 2, 3],
        )

    def test_close_cancel_keeps_dirty_window_open(self) -> None:
        self.window.table_model.setData(self.window.table_model.index(0, 1), "X")
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.assertFalse(self.window.close())
        self.assertTrue(self.window.is_dirty)

    def test_close_discard_reloads_disk_or_defaults(self) -> None:
        self.window.table_model.setData(self.window.table_model.index(0, 1), "X")
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            self.assertTrue(self.window.close())
        self.assertFalse(self.window.is_dirty)
        self.assertEqual(self.window.catalog.draft(0).name, "Madrugada")

    def test_close_save_writes_valid_changes(self) -> None:
        self.window.table_model.setData(
            self.window.table_model.index(0, 1), "Salva ao fechar"
        )
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Save,
        ):
            self.assertTrue(self.window.close())
        self.assertEqual(
            load_calculation_levels(self.path).schedule.level(0).name,
            "Salva ao fechar",
        )

    def test_close_save_keeps_window_open_when_the_grid_is_invalid(self) -> None:
        self.window.table_model.setData(self.window.table_model.index(0, 2), "")
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Save,
        ):
            self.assertFalse(self.window.close())
        self.assertTrue(self.window.is_dirty)
        self.assertFalse(self.path.exists())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class PatamaresMainWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = application()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "patamares.json"

    def main_window(self) -> "MainWindow":
        window = MainWindow(patamares_path=self.path)
        self.addCleanup(window.close)
        return window

    def test_menu_entry_is_always_enabled_and_opens_non_modal_window(self) -> None:
        window = self.main_window()
        self.assertIn(window.patamares_action, window.settings_menu.actions())
        self.assertTrue(window.patamares_action.isEnabled())
        window._show_patamares_window()
        self.assertTrue(window.patamares_window.isVisible())
        self.assertFalse(window.patamares_window.isModal())

    def test_unsaved_edits_do_not_change_main_window_snapshot(self) -> None:
        window = self.main_window()
        window.patamares_window.table_model.setData(
            window.patamares_window.table_model.index(0, 1), "Pendente"
        )
        self.assertEqual(window.calculation_level_schedule.level(0).name, "Madrugada")
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            window.patamares_window.close()

    def test_saved_schedule_returns_on_the_next_main_window(self) -> None:
        first = self.main_window()
        first.patamares_window.table_model.setData(
            first.patamares_window.table_model.index(0, 1), "Persistente"
        )
        self.assertTrue(first.patamares_window._save())
        self.assertEqual(first.calculation_level_schedule.level(0).name, "Persistente")

        second = MainWindow(patamares_path=self.path)
        self.addCleanup(second.close)
        self.assertEqual(second.calculation_level_schedule.level(0).name, "Persistente")

    def test_corrupted_file_warns_and_starts_with_defaults(self) -> None:
        self.path.write_text("{ inválido", encoding="utf-8")
        with patch.object(QMessageBox, "warning") as warning:
            window = MainWindow(patamares_path=self.path)
            self.addCleanup(window.close)
            window.show()
            self.app.processEvents()
        self.assertEqual(warning.call_count, 1)
        self.assertEqual(window.calculation_level_schedule, default_calculation_levels())


if __name__ == "__main__":
    unittest.main()
