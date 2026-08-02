from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, QSize, Qt
    from PyQt6.QtGui import QKeySequence
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.load_import import LoadCsvResult
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import (
        CircuitCatalogModel,
        CircuitDefinition,
        CircuitModel,
        FeatureSelection,
        LineNetworkModel,
        LoadModel,
        SwitchModel,
        UtmCrs,
    )
    from circuit_viewer.segment_import import SegmentLoadResult
    from circuit_viewer.switch_import import SwitchLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class GlobalSearchUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        bars = CircuitModel(
            ["B0", "B1", "B2"],
            ["DUP", "BARRA-2", ""],
            [500_000.0, 500_100.0, 500_200.0],
            [8_000_000.0, 8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
            bars,
            ["T0", "T1"],
            ["DUP", "TRECHO-2"],
            ["ABC", "ABC"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["", ""],
            ["", ""],
            [100.0, 100.0],
        )
        switches = SwitchModel(
            segments,
            ["CH0"],
            ["TC"],
            ["C0"],
            [0],
            ["DUP"],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        catalog = CircuitCatalogModel.build(
            segments,
            switches,
            [CircuitDefinition("C0", "B0", "DUP", "13.8")],
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        window._on_import_finished(
            CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0)
        )
        window._on_segment_import_finished(
            SegmentLoadResult(segments, "utf-8-sig", 2, 2, 0, (), 0)
        )
        window._on_switch_import_finished(
            SwitchLoadResult(switches, "utf-8-sig", 1, 1, 0, (), 0)
        )
        window._set_circuit_catalog(catalog)
        self.app.processEvents()
        return window, bars, segments, switches, catalog

    def wait_until(self, predicate, timeout_ms: int = 3_000) -> bool:
        elapsed = 0
        while elapsed < timeout_ms:
            self.app.processEvents()
            if predicate():
                return True
            QTest.qWait(20)
            elapsed += 20
        self.app.processEvents()
        return bool(predicate())

    def test_action_opens_dialog_and_lists_duplicate_types(self) -> None:
        empty = MainWindow()
        self.addCleanup(empty.close)
        self.assertFalse(empty.search_action.isEnabled())

        window, _, _, _, _ = self.make_window()
        self.assertTrue(window.search_action.isEnabled())
        self.assertTrue(
            window.search_action.shortcut().matches(QKeySequence.StandardKey.Find)
            != QKeySequence.SequenceMatch.NoMatch
        )
        window.search_action.trigger()
        window.search_palette.input.setText("dup")
        self.app.processEvents()

        self.assertTrue(window.search_palette.isVisible())
        self.assertEqual(window.search_palette.results_list.count(), 4)
        labels = [
            window.search_palette.results_list.item(row).text()
            for row in range(4)
        ]
        self.assertTrue(any("Barra · B0" in label for label in labels))
        self.assertTrue(any("Trecho · T0" in label for label in labels))
        self.assertTrue(any("Chave · CH0 · T0" in label for label in labels))
        self.assertTrue(any("Circuito · C0 · origem B0" in label for label in labels))
        self.assertFalse(window.search_palette.isModal())
        self.assertIs(window.search_palette.parentWidget(), window)

        first_item = window.search_palette.results_list.item(0)
        item_rect = window.search_palette.results_list.visualItemRect(first_item)
        QTest.mouseClick(
            window.search_palette.results_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=item_rect.center(),
        )
        self.app.processEvents()
        self.assertFalse(window.search_palette.isVisible())
        self.assertEqual(window._selected_feature, FeatureSelection("bar", 0))

    def test_keyboard_activation_focuses_bar_and_escape_closes(self) -> None:
        window, bars, _, _, _ = self.make_window()
        window.search_action.trigger()
        window.search_palette.input.setText("barra-2")
        QTest.keyClick(window.search_palette.input, Qt.Key.Key_Return)
        self.app.processEvents()

        self.assertFalse(window.search_palette.isVisible())
        self.assertEqual(window._selected_feature, FeatureSelection("bar", 1))
        center = window.view.mapToScene(window.view.viewport().rect().center())
        self.assertAlmostEqual(center.x(), float(bars.x[1]), delta=2.0)
        self.assertAlmostEqual(center.y(), -float(bars.y[1]), delta=2.0)

        window.search_action.trigger()
        QTest.keyClick(window.search_palette.input, Qt.Key.Key_Escape)
        self.assertFalse(window.search_palette.isVisible())

    def test_dialog_can_close_move_resize_and_preserves_session_state(self) -> None:
        window, _, _, _, _ = self.make_window()
        dialog = window.search_palette
        window.search_action.trigger()
        dialog.input.setText("dup")
        dialog.any_column_checkbox.setChecked(True)
        target_position = dialog.pos() + QPoint(35, 25)
        target_size = QSize(640, 460)
        dialog.move(target_position)
        dialog.resize(target_size)

        close_button = dialog.buttons.buttons()[0]
        QTest.mouseClick(close_button, Qt.MouseButton.LeftButton)
        self.assertFalse(dialog.isVisible())

        window.search_action.trigger()
        self.assertEqual(dialog.pos(), target_position)
        self.assertEqual(dialog.size(), target_size)
        self.assertEqual(dialog.input.text(), "dup")
        self.assertTrue(dialog.any_column_checkbox.isChecked())

        dialog.close()  # mesmo closeEvent acionado pelo X da barra de título
        self.assertFalse(dialog.isVisible())
        window.search_action.trigger()
        QTest.keyClick(dialog.input, Qt.Key.Key_Escape)
        self.assertFalse(dialog.isVisible())

    def test_any_column_mode_is_async_limited_by_length_and_finds_fields(self) -> None:
        window, _, _, _, _ = self.make_window()
        self.assertTrue(
            self.wait_until(lambda: window.search_index.fields_ready),
            "o índice de todas as colunas não ficou pronto",
        )
        window.search_action.trigger()
        dialog = window.search_palette
        dialog.any_column_checkbox.setChecked(True)
        dialog.input.setText("ab")
        self.assertEqual(dialog.results_list.count(), 0)
        self.assertIn("pelo menos 3", dialog.summary.text())

        dialog.input.setText("500100")
        self.assertTrue(
            self.wait_until(
                lambda: dialog.results_list.count() == 1
                and "Buscando" not in dialog.summary.text()
            ),
            dialog.summary.text(),
        )
        item = dialog.results_list.item(0)
        self.assertIn("Barra · B1", item.text())
        self.assertIn("X: 500100.000", item.text())

        dialog.input.setText("8000000")
        self.assertTrue(
            self.wait_until(
                lambda: dialog.results_list.count() == 3
                and "Buscando" not in dialog.summary.text()
            ),
            dialog.summary.text(),
        )
        self.assertTrue(
            all(
                "Y: 8000000.000" in dialog.results_list.item(row).text()
                for row in range(dialog.results_list.count())
            )
        )

    def test_search_action_is_available_when_only_codes_are_empty(self) -> None:
        bars = CircuitModel(
            ["BARRA-SEM-CODIGO"],
            [""],
            [500_000.0],
            [8_000_000.0],
            UtmCrs(21, northern=False),
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window._on_import_finished(
            CsvLoadResult(bars, "utf-8-sig", 1, 1, 0, (), 0)
        )
        self.assertTrue(window.search_action.isEnabled())
        self.assertEqual(len(window.search_index), 0)

    def test_reimport_discards_pending_any_column_results(self) -> None:
        window, _, _, _, _ = self.make_window()
        self.assertTrue(self.wait_until(lambda: window.search_index.fields_ready))
        window.search_action.trigger()
        dialog = window.search_palette
        dialog.any_column_checkbox.setChecked(True)
        dialog.input.setText("500100")

        replacement = CircuitModel(
            ["B-NOVA"],
            ["NOVA"],
            [600_000.0],
            [8_100_000.0],
            UtmCrs(21, northern=False),
        )
        window._on_import_finished(
            CsvLoadResult(replacement, "utf-8-sig", 1, 1, 0, (), 0)
        )

        self.assertEqual(dialog.results_list.count(), 0)
        self.assertTrue(self.wait_until(lambda: window.search_index.fields_ready))
        self.assertTrue(
            self.wait_until(lambda: dialog.summary.text() == "Nenhum elemento encontrado.")
        )
        self.assertEqual(dialog.results_list.count(), 0)

    def test_switch_and_circuit_results_map_to_graphic_targets(self) -> None:
        window, _, _, _, _ = self.make_window()
        results = window.search_index.query("dup").results
        switch_result = next(result for result in results if result.kind == "switch")
        circuit_result = next(result for result in results if result.kind == "circuit")

        window._activate_search_result(switch_result)
        self.app.processEvents()
        self.assertEqual(window._selected_feature, FeatureSelection("segment", 0))
        self.assertEqual(window.details_dock.windowTitle(), "Chave selecionada")
        self.assertTrue(window.switch_details_section.isVisible())

        window._activate_search_result(circuit_result)
        self.assertEqual(window._selected_feature, FeatureSelection("bar", 0))
        self.assertIn("Circuito C0: origem B0", window.statusBar().currentMessage())

    def test_hidden_bar_gets_halo_without_changing_visibility(self) -> None:
        window, _, _, _, _ = self.make_window()
        window.show_bars_action.setChecked(False)
        result = next(
            result
            for result in window.search_index.query("barra-2").results
            if result.kind == "bar"
        )

        window._activate_search_result(result)

        self.assertFalse(window.show_bars_action.isChecked())
        self.assertFalse(window.view.bars_visible)
        self.assertEqual(window._selected_feature, FeatureSelection("bar", 1))
        self.assertTrue(window.virtualizer.selection_overlay.isVisible())
        self.assertIn("oculto pelos filtros", window.statusBar().currentMessage())

        window.search_action.trigger()
        self.assertIsNone(window._selected_feature)
        self.assertFalse(window.virtualizer.selection_overlay.isVisible())

    def test_circuit_filter_stays_off_and_data_replacement_clears_focus(self) -> None:
        window, _, _, _, catalog = self.make_window()
        window._set_circuit_catalog(catalog, checked=(False,))
        result = next(
            result
            for result in window.search_index.query("dup").results
            if result.kind == "bar"
        )

        window._activate_search_result(result)
        window._apply_circuit_visibility()

        self.assertEqual(window._circuit_visibility.checked_states, (False,))
        self.assertEqual(window._selected_feature, FeatureSelection("bar", 0))
        self.assertTrue(window.virtualizer.selection_overlay.isVisible())

        window._set_circuit_catalog(catalog, checked=(False,))
        self.assertIsNone(window._selected_feature)
        self.assertFalse(window.virtualizer.selection_overlay.isVisible())

    def test_load_results_focus_details_and_reveal_hidden_symbol(self) -> None:
        window, bars, _, _, _ = self.make_window()
        loads = LoadModel(
            bars,
            ["L0", "L1"],
            [0, 0],
            ["", ""],
            ["DUP", "DUP"],
            ["10", "20"],
            ["", ""],
            ["220", "220"],
            ["ABC", "ABC"],
            ["Y", "Y"],
        )
        window._on_load_import_finished(
            LoadCsvResult(loads, "utf-8-sig", 2, 2, 0, (), 0)
        )
        results = [
            result
            for result in window.search_index.query("dup").results
            if result.kind == "load"
        ]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].display_text, "DUP — Carga · L0 · B0")

        window.show_loads_action.setChecked(False)
        window._activate_search_result(results[1])
        self.app.processEvents()

        self.assertFalse(window.show_loads_action.isChecked())
        self.assertEqual(window._selected_feature, FeatureSelection("load", 1))
        self.assertEqual(window.details_dock.windowTitle(), "Carga selecionada")
        self.assertEqual(window.load_detail_labels["load_id"].text(), "L1")
        self.assertTrue(window.load_virtualizer.selection_overlay.isVisible())
        center = window.view.mapToScene(window.view.viewport().rect().center())
        self.assertAlmostEqual(center.x(), float(bars.x[0]), delta=2.0)
        self.assertAlmostEqual(center.y(), -float(bars.y[0]), delta=2.0)


if __name__ == "__main__":
    unittest.main()
