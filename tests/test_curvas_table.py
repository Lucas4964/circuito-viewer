from __future__ import annotations

import unittest

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication, QHeaderView

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover - depende do ambiente
    PYQT_AVAILABLE = False

if PYQT_AVAILABLE:
    from circuit_viewer.curvas import HOURLY_CURVE_POINT_COUNT, CurveDraft
    from circuit_viewer.curvas_table import CurveTableView, CurveValuesTableModel
    from circuit_viewer.table_columns import enable_interactive_columns


def _application() -> "QApplication":
    return QApplication.instance() or QApplication([])


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CurveValuesTableModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _application()
        self.draft = CurveDraft.new("Curva")
        self.model = CurveValuesTableModel()
        self.model.set_draft(self.draft)

    def _index(self, row: int, column: int = 1):
        return self.model.index(row, column)

    def test_headers(self) -> None:
        self.assertEqual(
            [
                self.model.headerData(
                    section, Qt.Orientation.Horizontal
                )
                for section in range(2)
            ],
            ["Hora", "Valor"],
        )

    def test_row_count_requires_a_draft(self) -> None:
        self.assertEqual(self.model.rowCount(), HOURLY_CURVE_POINT_COUNT)
        self.model.set_draft(None)
        self.assertEqual(self.model.rowCount(), 0)

    def test_hour_column_is_synthetic_and_one_based(self) -> None:
        self.assertEqual(self.model.data(self._index(0, 0)), "1")
        self.assertEqual(self.model.data(self._index(23, 0)), "24")

    def test_hour_column_is_not_editable(self) -> None:
        self.assertFalse(
            self.model.flags(self._index(0, 0))
            & Qt.ItemFlag.ItemIsEditable
        )
        self.assertTrue(
            self.model.flags(self._index(0, 1))
            & Qt.ItemFlag.ItemIsEditable
        )

    def test_empty_cell_displays_nothing(self) -> None:
        self.assertEqual(self.model.data(self._index(0)), "")
        self.assertEqual(
            self.model.data(self._index(0), Qt.ItemDataRole.EditRole), ""
        )

    def test_edit_role_keeps_full_precision(self) -> None:
        """Com o texto de exibição, reabrir e confirmar truncaria o valor."""

        self.draft.set_value(0, 0.123456)
        self.assertEqual(self.model.data(self._index(0)), "0.1235")
        self.assertEqual(
            self.model.data(self._index(0), Qt.ItemDataRole.EditRole),
            "0.123456",
        )

    def test_set_data_accepts_both_decimal_separators(self) -> None:
        self.assertTrue(self.model.setData(self._index(0), "12,5"))
        self.assertTrue(self.model.setData(self._index(1), "12.5"))
        self.assertEqual(self.draft.values[0], 12.5)
        self.assertEqual(self.draft.values[1], 12.5)

    def test_set_data_rejects_text_and_reports(self) -> None:
        received: list[str] = []
        self.model.validationFailed.connect(received.append)
        self.assertFalse(self.model.setData(self._index(0), "abc"))
        self.assertIsNone(self.draft.values[0])
        self.assertEqual(len(received), 1)
        self.assertIn("abc", received[0])

    def test_empty_text_clears_the_cell(self) -> None:
        self.draft.set_value(0, 5.0)
        self.assertTrue(self.model.setData(self._index(0), ""))
        self.assertIsNone(self.draft.values[0])

    def test_set_data_reports_no_op(self) -> None:
        self.assertTrue(self.model.setData(self._index(0), "1"))
        self.assertFalse(self.model.setData(self._index(0), "1"))

    def test_set_data_ignores_the_hour_column(self) -> None:
        self.assertFalse(self.model.setData(self._index(0, 0), "9"))

    def test_data_changed_carries_the_display_roles(self) -> None:
        received: list[list] = []
        self.model.dataChanged.connect(
            lambda first, last, roles: received.append(roles)
        )
        self.model.setData(self._index(3), "2")
        self.assertEqual(len(received), 1)
        self.assertIn(Qt.ItemDataRole.DisplayRole, received[0])
        self.assertIn(Qt.ItemDataRole.EditRole, received[0])

    def test_value_changed_signal(self) -> None:
        received: list[tuple[int, object]] = []
        self.model.valueChanged.connect(
            lambda row, value: received.append((row, value))
        )
        self.model.setData(self._index(5), "7,5")
        self.assertEqual(received, [(5, 7.5)])

    def test_apply_values_emits_a_single_data_changed(self) -> None:
        """Uma colagem tem de repintar o gráfico uma vez, não vinte e quatro."""

        received: list[tuple[int, int]] = []
        self.model.dataChanged.connect(
            lambda first, last, roles: received.append(
                (first.row(), last.row())
            )
        )
        changed = self.model.apply_values(
            0, [float(v) for v in range(HOURLY_CURVE_POINT_COUNT)]
        )
        # As 24 mudam: None → 0.0 na primeira hora também é uma mudança.
        self.assertEqual(changed, HOURLY_CURVE_POINT_COUNT)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], (0, 23))

    def test_apply_values_stops_at_the_last_hour(self) -> None:
        changed = self.model.apply_values(22, [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(changed, 2)
        self.assertEqual(self.draft.values[22], 1.0)
        self.assertEqual(self.draft.values[23], 2.0)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CurveTableViewClipboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _application()
        self.draft = CurveDraft.new("Curva")
        self.model = CurveValuesTableModel()
        self.model.set_draft(self.draft)
        self.view = CurveTableView()
        self.view.setModel(self.model)
        self.addCleanup(self.view.deleteLater)
        self.reports: list[str] = []
        self.view.pasteReported.connect(self.reports.append)

    def _set_clipboard(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        self.assertIsNotNone(clipboard)
        clipboard.setText(text)

    def test_pastes_a_full_column(self) -> None:
        self._set_clipboard("\n".join(str(v) for v in range(1, 25)))
        self.view.paste_from_clipboard()
        self.assertEqual(self.draft.values[0], 1.0)
        self.assertEqual(self.draft.values[23], 24.0)
        self.assertTrue(self.draft.is_complete())

    def test_paste_honours_comma_decimals(self) -> None:
        self._set_clipboard("0,5\n0,7")
        self.view.paste_from_clipboard()
        self.assertEqual(self.draft.values[0], 0.5)
        self.assertEqual(self.draft.values[1], 0.7)

    def test_paste_starts_at_the_current_row(self) -> None:
        self.view.setCurrentIndex(self.model.index(10, 1))
        self._set_clipboard("1\n2")
        self.view.paste_from_clipboard()
        self.assertIsNone(self.draft.values[0])
        self.assertEqual(self.draft.values[10], 1.0)
        self.assertEqual(self.draft.values[11], 2.0)
        self.assertIn("hora 11", self.reports[0])

    def test_paste_truncates_beyond_the_last_hour(self) -> None:
        self.view.setCurrentIndex(self.model.index(22, 1))
        self._set_clipboard("1\n2\n3\n4")
        self.view.paste_from_clipboard()
        self.assertEqual(self.draft.values[22], 1.0)
        self.assertEqual(self.draft.values[23], 2.0)
        self.assertIn("2 valor(es) além da hora 24", self.reports[0])

    def test_two_column_block_uses_the_last_column(self) -> None:
        self._set_clipboard("1\t0,5\n2\t0,7")
        self.view.paste_from_clipboard()
        self.assertEqual(self.draft.values[0], 0.5)
        self.assertEqual(self.draft.values[1], 0.7)
        self.assertIn("foi usada a última", self.reports[0])

    def test_non_numeric_entry_keeps_the_following_hours_aligned(self) -> None:
        """Compactar o bloco deslocaria em uma hora tudo o que vem depois."""

        self._set_clipboard("1\nValor\n3")
        self.view.paste_from_clipboard()
        self.assertEqual(self.draft.values[0], 1.0)
        self.assertIsNone(self.draft.values[1])
        self.assertEqual(self.draft.values[2], 3.0)
        self.assertIn("1 valor(es) não numéricos", self.reports[0])

    def test_blank_middle_line_clears_that_hour_only(self) -> None:
        self.draft.set_value(1, 99.0)
        self._set_clipboard("1\n\n3")
        self.view.paste_from_clipboard()
        self.assertEqual(self.draft.values[0], 1.0)
        self.assertIsNone(self.draft.values[1])
        self.assertEqual(self.draft.values[2], 3.0)

    def test_trailing_blank_line_from_excel_is_ignored(self) -> None:
        self._set_clipboard("1\n2\n")
        self.view.paste_from_clipboard()
        self.assertEqual(self.draft.values[0], 1.0)
        self.assertEqual(self.draft.values[1], 2.0)
        self.assertIsNone(self.draft.values[2])

    def test_empty_clipboard_is_reported(self) -> None:
        self._set_clipboard("")
        self.view.paste_from_clipboard()
        self.assertIn("vazia", self.reports[0])

    def test_copy_without_selection_takes_the_whole_column(self) -> None:
        for hour_index in range(HOURLY_CURVE_POINT_COUNT):
            self.draft.set_value(hour_index, float(hour_index))
        self.view.clearSelection()
        self.view.copy_selection()
        text = QGuiApplication.clipboard().text()
        lines = text.split("\n")
        self.assertEqual(len(lines), HOURLY_CURVE_POINT_COUNT)
        self.assertEqual(lines[0], "0.0000")

    def test_copy_selection_produces_tsv(self) -> None:
        self.draft.set_value(0, 1.0)
        self.view.selectRow(0)
        self.view.copy_selection()
        self.assertIn("\t", QGuiApplication.clipboard().text())

    def test_clear_selection_empties_the_cells(self) -> None:
        self.draft.set_value(0, 1.0)
        self.view.selectRow(0)
        self.view.clear_selection_values()
        self.assertIsNone(self.draft.values[0])

    def test_interactive_columns_apply_after_set_model(self) -> None:
        enable_interactive_columns(self.view)
        self.assertEqual(
            self.view.horizontalHeader().sectionResizeMode(0),
            QHeaderView.ResizeMode.Interactive,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
