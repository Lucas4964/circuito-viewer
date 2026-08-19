from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QColor, QKeyEvent
    from PyQt6.QtWidgets import QApplication, QDialog

    from circuit_viewer.circuits_window import ROOT_BAR_COLUMN, CircuitTableModel
    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.main_window import ImportChoiceDialog, MainWindow
    from circuit_viewer.model import (
        CircuitCatalogModel,
        CircuitDefinition,
        CircuitModel,
        FeatureSelection,
        LineNetworkModel,
        SwitchModel,
        UtmCrs,
    )
    from circuit_viewer.segment_import import SegmentLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CircuitsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        bars = CircuitModel(
            ["B0", "B1", "B2"],
            ["", "", ""],
            [500_000.0, 500_010.0, 500_020.0],
            [8_000_000.0, 8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T0", "T1"],
            ["", ""],
            ["ABC", "ABC"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["", ""],
            ["", ""],
            [10.0, 10.0],
        )
        window = MainWindow()
        window.show()
        window._on_import_finished(
            CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0)
        )
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 2, 2, 0, (), 0)
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "ALIMENTADOR", "13.8")],
        )
        window._set_circuit_catalog(catalog)
        self.app.processEvents()
        return window, bars, network, catalog

    def make_two_circuit_window(self):  # noqa: ANN201
        """Dois circuitos: C1 parte de B0, C2 de B2, longe um do outro."""

        bars = CircuitModel(
            ["B0", "B1", "B2", "B3"],
            ["", "", "", ""],
            [500_000.0, 500_050.0, 500_000.0, 500_050.0],
            [8_000_000.0, 8_000_000.0, 8_000_400.0, 8_000_400.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T0", "T1"],
            ["", ""],
            ["ABC", "ABC"],
            [0, 2],
            [1, 3],
            ["", ""],
            ["", ""],
            ["", ""],
            [50.0, 50.0],
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 4, 4, 0, (), 0))
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 2, 2, 0, (), 0)
        )
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "ALIM-1", "13.8"),
                CircuitDefinition("C2", "B2", "ALIM-2", "13.8"),
            ],
        )
        window._set_circuit_catalog(catalog)
        self.app.processEvents()
        return window, catalog

    # --------------------------------------- duplo clique na barra inicial

    def test_double_click_on_the_root_bar_frames_it_on_the_map(self) -> None:
        window, _ = self.make_two_circuit_window()
        model = window.circuit_table_model
        before = abs(window.view.transform().m11())

        window.circuits_window.table.doubleClicked.emit(
            model.index(1, ROOT_BAR_COLUMN)
        )
        self.app.processEvents()

        after = abs(window.view.transform().m11())
        self.assertNotEqual(after, before)
        self.assertLessEqual(after, 4.0)
        self.assertIn("B2", window.statusBar().currentMessage())

    def test_double_click_outside_the_root_bar_column_does_nothing(self) -> None:
        """A coluna \"Cor\" tem delegate próprio; enquadrar dali seria colisão."""

        window, _ = self.make_two_circuit_window()
        model = window.circuit_table_model
        before = abs(window.view.transform().m11())

        for column in (0, 1, 2, 4, 5):
            window.circuits_window.table.doubleClicked.emit(
                model.index(1, column)
            )
        self.app.processEvents()

        self.assertEqual(abs(window.view.transform().m11()), before)

    def test_framing_a_hidden_circuit_reactivates_it(self) -> None:
        window, _ = self.make_two_circuit_window()
        model = window.circuit_table_model
        model.setData(
            model.index(1, 0),
            Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        window._circuit_visibility_timer.stop()
        window._apply_circuit_visibility()
        self.assertFalse(window._circuit_visibility.is_visible(1))

        window.circuits_window.table.doubleClicked.emit(
            model.index(1, ROOT_BAR_COLUMN)
        )
        self.app.processEvents()

        self.assertTrue(window._circuit_visibility.is_visible(1))
        self.assertIn("reativado", window.statusBar().currentMessage())

    def test_the_root_bar_column_announces_the_double_click(self) -> None:
        window, _ = self.make_two_circuit_window()

        tooltip = window.circuit_table_model.headerData(
            ROOT_BAR_COLUMN,
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.ToolTipRole,
        )

        self.assertIn("Duplo clique", tooltip)
        self.assertEqual(
            window.circuit_table_model.headerData(
                ROOT_BAR_COLUMN,
                Qt.Orientation.Horizontal,
                Qt.ItemDataRole.DisplayRole,
            ),
            "BARRA_ID",
        )

    # ------------------------------------------ Visualizar Barra Inicial

    def test_the_root_bar_action_needs_a_catalog_and_lives_in_the_view_menu(
        self,
    ) -> None:
        empty = MainWindow()
        self.addCleanup(empty.close)
        view_menu = next(
            action.menu()
            for action in empty.menuBar().actions()
            if action.text() == "Visualizar"
        )

        self.assertIn(empty.root_bar_action, view_menu.actions())
        self.assertTrue(empty.root_bar_action.isCheckable())
        self.assertFalse(empty.root_bar_action.isChecked())
        self.assertFalse(empty.root_bar_action.isEnabled())

        window, _ = self.make_two_circuit_window()

        self.assertTrue(window.root_bar_action.isEnabled())

    def test_toggling_the_action_shows_one_ring_per_visible_circuit(self) -> None:
        window, _ = self.make_two_circuit_window()
        item = window._root_bar_item
        self.assertIsNotNone(item)
        self.assertFalse(item.isVisible())

        window.root_bar_action.setChecked(True)
        self.app.processEvents()

        self.assertTrue(item.isVisible())
        self.assertEqual(item.visible_ring_count, 2)
        self.assertEqual(
            [color.name().upper() for _, color, _ in item._rings],
            [value.upper() for value in window._circuit_visibility.colors],
        )

        window.root_bar_action.setChecked(False)
        self.app.processEvents()

        self.assertFalse(item.isVisible())

    def test_hiding_a_circuit_removes_only_its_ring(self) -> None:
        window, _ = self.make_two_circuit_window()
        window.root_bar_action.setChecked(True)
        self.app.processEvents()
        model = window.circuit_table_model

        model.setData(
            model.index(1, 0),
            Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        window._circuit_visibility_timer.stop()
        window._apply_circuit_visibility()

        self.assertEqual(window._root_bar_item.visible_ring_count, 1)

    def test_recolouring_a_circuit_recolours_its_ring(self) -> None:
        window, _ = self.make_two_circuit_window()
        window.root_bar_action.setChecked(True)
        self.app.processEvents()
        model = window.circuit_table_model

        model.setData(model.index(0, 1), "#123456", Qt.ItemDataRole.EditRole)
        window._circuit_visibility_timer.stop()
        window._apply_circuit_visibility()

        self.assertEqual(
            window._root_bar_item._rings[0][1].name().upper(), "#123456"
        )

    def test_dropping_the_catalog_removes_the_ring_item(self) -> None:
        window, _ = self.make_two_circuit_window()
        window.root_bar_action.setChecked(True)
        self.app.processEvents()

        window._set_circuit_catalog(None)

        self.assertIsNone(window._root_bar_item)
        self.assertFalse(window.root_bar_action.isEnabled())

    def test_import_choice_and_modeless_table(self) -> None:
        empty = MainWindow()
        self.addCleanup(empty.close)
        self.assertFalse(empty.circuits_action.isEnabled())
        without_segments = ImportChoiceDialog(True, False, empty)
        self.assertFalse(without_segments.circuits_button.isEnabled())
        with_segments = ImportChoiceDialog(True, True, empty)
        self.assertTrue(with_segments.circuits_button.isEnabled())
        with_segments.circuits_button.click()
        self.assertEqual(with_segments.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(with_segments.selected_kind, "circuits")

        window, _, _, _ = self.make_window()
        self.addCleanup(window.close)
        self.assertTrue(window.circuits_action.isEnabled())
        window._show_circuits_window()
        self.assertTrue(window.circuits_window.isVisible())
        self.assertFalse(window.circuits_window.isModal())
        table_model = window.circuit_table_model
        self.assertEqual(table_model.rowCount(), 1)
        self.assertEqual(table_model.columnCount(), 6)
        self.assertEqual(
            table_model.data(table_model.index(0, 0), Qt.ItemDataRole.CheckStateRole),
            Qt.CheckState.Checked,
        )
        color = table_model.data(table_model.index(0, 1))
        self.assertRegex(color, r"^#[0-9A-F]{6}$")
        sample = table_model.data(
            table_model.index(0, 1), Qt.ItemDataRole.DecorationRole
        )
        self.assertGreater(sample.width(), sample.height())
        self.assertEqual(table_model.data(table_model.index(0, 2)), "C1")
        self.assertEqual(table_model.data(table_model.index(0, 3)), "B0")
        self.assertEqual(table_model.data(table_model.index(0, 4)), "ALIMENTADOR")
        self.assertEqual(table_model.data(table_model.index(0, 5)), "13.8")
        style = window.circuits_window.table.styleSheet().lower()
        self.assertIn("palette(mid)", style)
        self.assertNotIn("background", style)
        self.assertNotIn("; color:", style)
        self.assertNotIn("font", style)
        self.assertTrue(window.circuits_window.table.showGrid())
        self.assertFalse(window.overlaps_action.isEnabled())

    def test_color_cell_changes_only_render_state_and_delegate_can_cancel(self) -> None:
        window, _, _, catalog = self.make_window()
        self.addCleanup(window.close)
        model = window.circuit_table_model
        color_index = model.index(0, 1)
        membership = catalog.membership(0)
        revision = window._line_item.geometry_revision

        self.assertTrue(
            model.setData(color_index, "#123456", Qt.ItemDataRole.EditRole)
        )
        window._apply_circuit_visibility()
        self.assertEqual(window._circuit_visibility.color(0), "#123456")
        self.assertIs(catalog.membership(0), membership)
        self.assertEqual(window._line_item.geometry_revision, revision)

        delegate = window.circuits_window.color_delegate
        previous = window._circuit_visibility.color(0)
        delegate.choose_color = lambda initial, parent: QColor()
        key_event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(delegate.editorEvent(key_event, model, None, color_index))
        self.assertEqual(window._circuit_visibility.color(0), previous)

        delegate.choose_color = lambda initial, parent: QColor("#ABCDEF")
        self.assertTrue(delegate.editorEvent(key_event, model, None, color_index))
        self.assertEqual(window._circuit_visibility.color(0), "#ABCDEF")

    def test_checkbox_filters_aggregates_and_does_not_change_membership(self) -> None:
        window, _, _, catalog = self.make_window()
        self.addCleanup(window.close)
        membership = catalog.membership(0)
        window._set_selection(FeatureSelection("segment", 0))
        model = window.circuit_table_model
        index = model.index(0, 0)
        self.assertTrue(
            model.setData(index, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        )
        window._apply_circuit_visibility()

        self.assertIs(catalog.membership(0), membership)
        self.assertEqual(window._line_item.visible_segment_count, 0)
        self.assertEqual(window.virtualizer.overview_item.visible_point_count, 0)
        self.assertIsNone(window._selected_feature)
        self.assertFalse(window._circuit_visibility.bar_visible_mask.any())
        self.assertFalse(window._circuit_visibility.segment_visible_mask.any())

        self.assertTrue(
            model.setData(index, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        )
        window._apply_circuit_visibility()
        self.assertEqual(window._line_item.visible_segment_count, 2)
        self.assertEqual(window.virtualizer.overview_item.visible_point_count, 3)

        window.show_bars_action.setChecked(False)
        model.setData(index, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        model.setData(index, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        window._apply_circuit_visibility()
        self.assertFalse(window.virtualizer.overview_item.isVisible())
        self.assertEqual(window._line_item.visible_segment_count, 2)

    def test_switch_reimport_rebuilds_membership_and_preserves_checkbox(self) -> None:
        window, _, network, old_catalog = self.make_window()
        self.addCleanup(window.close)
        table_model: CircuitTableModel = window.circuit_table_model
        table_model.setData(
            table_model.index(0, 0),
            Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        table_model.setData(
            table_model.index(0, 1),
            "#345678",
            Qt.ItemDataRole.EditRole,
        )
        switches = SwitchModel(
            network,
            ["CH1"],
            ["TIPO"],
            ["C1"],
            [1],
            [""],
            ["0"],
            ["1"],
            [""],
            [""],
            [""],
        )
        window._set_switch_model(switches)
        self.assertIsNot(window._circuit_catalog, old_catalog)
        self.assertEqual(window._circuit_visibility.checked_states, (False,))
        self.assertEqual(window._circuit_visibility.colors, ("#345678",))
        membership = window._circuit_catalog.membership(0)
        self.assertEqual(set(membership.bar_indices), {0, 1})
        self.assertEqual(set(membership.common_segment_indices), {0})
        self.assertEqual(set(membership.switch_segment_indices), {1})

    def test_overlap_report_and_first_visible_color_precedence(self) -> None:
        window, _, network, _ = self.make_window()
        self.addCleanup(window.close)
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "", ""),
                CircuitDefinition("C2", "B2", "", ""),
            ],
        )
        window._set_circuit_catalog(
            catalog,
            colors=("#123456", "#654321"),
        )
        self.app.processEvents()
        self.assertTrue(window.overlaps_action.isEnabled())
        self.assertTrue(window.overlap_report_window.isVisible())
        self.assertEqual(window.overlap_table_model.rowCount(), 2)
        self.assertEqual(window.overlap_table_model.columnCount(), 3)
        self.assertEqual(window.overlap_table_model.data(window.overlap_table_model.index(0, 0)), "T0")
        self.assertEqual(window.overlap_table_model.data(window.overlap_table_model.index(0, 1)), "2")
        self.assertEqual(window.overlap_table_model.data(window.overlap_table_model.index(0, 2)), "C1, C2")
        self.assertEqual(set(catalog.overlapping_segment_indices), {0, 1})
        self.assertEqual(list(catalog.circuit_indices_for_segment(0)), [0, 1])
        self.assertEqual(list(window._circuit_visibility.segment_style_indices), [0, 0])

        window.circuit_table_model.setData(
            window.circuit_table_model.index(0, 0),
            Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        window._apply_circuit_visibility()
        self.assertEqual(list(window._circuit_visibility.segment_style_indices), [1, 1])
        self.assertEqual(window.overlap_table_model.rowCount(), 2)
        self.assertEqual(window._line_item.category_path_count, 1)

    def test_replacing_segments_removes_catalog_and_hides_window(self) -> None:
        window, _, network, _ = self.make_window()
        self.addCleanup(window.close)
        window._show_circuits_window()
        self.assertTrue(window.circuits_window.isVisible())
        window._set_line_model(network)
        self.assertIsNone(window._circuit_catalog)
        self.assertFalse(window.circuits_action.isEnabled())
        self.assertFalse(window.circuits_window.isVisible())
        self.assertFalse(window.overlap_report_window.isVisible())
        self.assertFalse(window.overlaps_action.isEnabled())
        self.assertEqual(window.circuit_table_model.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
