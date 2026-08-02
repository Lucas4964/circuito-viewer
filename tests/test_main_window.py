from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication, QDialog, QToolBar

    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.main_window import ImportChoiceDialog, MainWindow
    from circuit_viewer.segment_import import SegmentLoadResult
    from circuit_viewer.switch_import import SwitchLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False

from circuit_viewer.model import (
    CircuitModel,
    FeatureSelection,
    LineNetworkModel,
    SwitchModel,
    UtmCrs,
)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class MainWindowSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_window(self):
        bars = CircuitModel(
            ["B1", "B2", "B3"],
            ["C1", "C2", "C3"],
            [500_000.0, 500_100.0, 500_200.0],
            [8_000_000.0, 8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T1", "T2"],
            ["COD-T1", "COD-T2"],
            ["ABC", "AB"],
            [0, 1],
            [1, 2],
            ["ARR-1", "ARR-2"],
            ["CF-1", "CF-2"],
            ["CN-1", "CN-2"],
            [100.25, 100.0],
        )
        window = MainWindow()
        window.show()
        window._on_import_finished(
            CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0)
        )
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 2, 2, 0, (), 0)
        )
        self.app.processEvents()
        return window, bars, network

    def _make_switches(
        self, network: LineNetworkModel, *, code: str = "CH-COD"
    ) -> SwitchModel:
        return SwitchModel(
            network,
            ["CH1"],
            ["TIPO-1"],
            ["CIR-1"],
            [0],
            [code],
            ["A"],
            ["F"],
            ["N"],
            [""],
            ["FUSIVEL"],
        )

    def test_single_import_action_opens_choices_with_dependency_state(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        toolbar = window.findChild(QToolBar, "main_toolbar")
        import_actions = [
            action for action in toolbar.actions() if action.text().startswith("Importar")
        ]
        self.assertEqual(window.import_action.text(), "Importar…")
        self.assertEqual(len(import_actions), 1)
        self.assertTrue(window.show_bars_action.isChecked())
        self.assertFalse(window.show_bars_action.isEnabled())
        self.assertNotIn(window.show_bars_action, toolbar.actions())

        without_bars = ImportChoiceDialog(False, False, window)
        self.assertTrue(without_bars.bars_button.isEnabled())
        self.assertFalse(without_bars.segments_button.isEnabled())
        self.assertFalse(without_bars.switches_button.isEnabled())
        without_bars.bars_button.click()
        self.assertEqual(without_bars.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(without_bars.selected_kind, "bars")

        with_bars = ImportChoiceDialog(True, False, window)
        self.assertTrue(with_bars.segments_button.isEnabled())
        self.assertFalse(with_bars.switches_button.isEnabled())
        with_bars.segments_button.click()
        self.assertEqual(with_bars.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(with_bars.selected_kind, "segments")

        with_segments = ImportChoiceDialog(True, True, window)
        self.assertTrue(with_segments.switches_button.isEnabled())
        with_segments.switches_button.click()
        self.assertEqual(with_segments.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(with_segments.selected_kind, "switches")

    def test_show_bars_toggle_controls_selection_without_affecting_lines(self) -> None:
        window, _, network = self._make_window()
        self.addCleanup(window.close)
        self.assertTrue(window.show_bars_action.isEnabled())
        self.assertTrue(window.show_bars_action.isChecked())

        window._set_selection(FeatureSelection("bar", 0))
        window.show_bars_action.setChecked(False)
        self.assertIsNone(window._selected_feature)
        self.assertFalse(window.view.bars_visible)
        self.assertFalse(window.virtualizer.bars_visible)
        self.assertFalse(window.virtualizer.overview_item.isVisible())
        self.assertTrue(window._line_item.isVisible())

        window.show_bars_action.setChecked(True)
        window._set_selection(FeatureSelection("segment", 0))
        window._set_switch_model(self._make_switches(network))
        window.show_bars_action.setChecked(False)
        self.assertEqual(window._selected_feature, FeatureSelection("segment", 0))
        self.assertTrue(window.segment_selection_overlay.isVisible())
        self.assertTrue(window._line_item.isVisible())
        self.assertTrue(window._switch_item.isVisible())

    def test_hidden_state_survives_bar_reimport_and_resize(self) -> None:
        window, bars, _ = self._make_window()
        self.addCleanup(window.close)
        window.show_bars_action.setChecked(False)

        window._on_import_finished(
            CsvLoadResult(bars, "utf-8-sig", len(bars), len(bars), 0, (), 0)
        )
        window.resize(1000, 650)
        self.app.processEvents()

        self.assertTrue(window.show_bars_action.isEnabled())
        self.assertFalse(window.show_bars_action.isChecked())
        self.assertFalse(window.view.bars_visible)
        self.assertFalse(window.virtualizer.bars_visible)
        self.assertFalse(window.virtualizer.overview_item.isVisible())

    def test_panel_switches_between_segment_bar_and_empty_state(self) -> None:
        window, _, _ = self._make_window()
        self.addCleanup(window.close)

        window._set_selection(FeatureSelection("segment", 0))
        self.assertEqual(window.details_dock.windowTitle(), "Trecho selecionado")
        self.assertEqual(window.segment_detail_labels["segment_id"].text(), "T1")
        self.assertEqual(window.segment_detail_labels["code"].text(), "COD-T1")
        self.assertEqual(window.segment_detail_labels["phases"].text(), "ABC")
        self.assertEqual(window.segment_detail_labels["start_bar_id"].text(), "B1")
        self.assertEqual(window.segment_detail_labels["end_bar_id"].text(), "B2")
        self.assertEqual(window.segment_detail_labels["arrangement_id"].text(), "ARR-1")
        self.assertEqual(window.segment_detail_labels["phase_cable_id"].text(), "CF-1")
        self.assertEqual(window.segment_detail_labels["neutral_cable_id"].text(), "CN-1")
        self.assertEqual(window.segment_detail_labels["length"].text(), "100.250")
        self.assertTrue(window.segment_selection_overlay.isVisible())

        window._set_selection(FeatureSelection("bar", 0))
        self.assertEqual(window.details_dock.windowTitle(), "Barra selecionada")
        self.assertEqual(window.bar_detail_labels["bar_id"].text(), "B1")
        self.assertFalse(window.segment_selection_overlay.isVisible())

        window._set_selection(None)
        self.assertEqual(window.details_dock.windowTitle(), "Elemento selecionado")
        self.assertIs(window.details_stack.currentWidget(), window.empty_details_page)

    def test_detail_pages_use_a_continuous_grid_without_changing_colors(self) -> None:
        window, _, _ = self._make_window()
        self.addCleanup(window.close)
        window._set_selection(FeatureSelection("segment", 0))
        self.app.processEvents()

        for grid in (
            window.bar_details_grid,
            window.segment_details_grid,
            window.switch_details_grid,
        ):
            margins = grid.contentsMargins()
            self.assertEqual(
                (margins.left(), margins.top(), margins.right(), margins.bottom()),
                (0, 0, 0, 0),
            )
            self.assertEqual(grid.horizontalSpacing(), 0)
            self.assertEqual(grid.verticalSpacing(), 0)

        all_cells = (
            *window.bar_caption_labels.values(),
            *window.bar_detail_labels.values(),
            *window.segment_caption_labels.values(),
            *window.segment_detail_labels.values(),
            *window.switch_caption_labels.values(),
            *window.switch_detail_labels.values(),
        )
        expected_text_color = window.palette().color(QPalette.ColorRole.WindowText)
        for cell in all_cells:
            style = cell.styleSheet().lower()
            self.assertTrue(cell.property("detailCell"))
            self.assertIn("palette(mid)", style)
            self.assertNotIn("background", style)
            self.assertNotIn("color:", style)
            self.assertNotIn("font", style)
            self.assertFalse(cell.autoFillBackground())
            self.assertEqual(
                cell.palette().color(QPalette.ColorRole.WindowText),
                expected_text_color,
            )
            self.assertEqual(cell.font(), window.font())

        first_caption = window.segment_caption_labels["segment_id"]
        first_value = window.segment_detail_labels["segment_id"]
        second_caption = window.segment_caption_labels["code"]
        self.assertIn("border-top: 1px", first_caption.styleSheet())
        self.assertIn("border-left: 1px", first_caption.styleSheet())
        self.assertIn("border-left: 0px", first_value.styleSheet())
        self.assertIn("border-top: 0px", second_caption.styleSheet())
        self.assertEqual(first_caption.geometry().right() + 1, first_value.geometry().left())
        self.assertEqual(first_caption.geometry().bottom() + 1, second_caption.geometry().top())

        for value in window.segment_detail_labels.values():
            self.assertTrue(value.wordWrap())
            self.assertTrue(
                value.textInteractionFlags()
                & Qt.TextInteractionFlag.TextSelectableByMouse
            )

    def test_switch_table_is_conditional_and_reimport_preserves_selection(self) -> None:
        window, _, network = self._make_window()
        self.addCleanup(window.close)
        selection = FeatureSelection("segment", 0)
        window._set_selection(selection)
        self.assertFalse(window.switch_details_section.isVisible())

        switches = self._make_switches(network)
        window._on_switch_import_finished(
            SwitchLoadResult(switches, "utf-8-sig", 1, 1, 0, (), 0)
        )

        self.assertEqual(window._selected_feature, selection)
        self.assertTrue(window.switch_details_section.isVisible())
        self.assertEqual(window.segment_table_title.text(), "Dados do trecho")
        self.assertEqual(window.switch_table_title.text(), "Dados da chave")
        self.assertEqual(window.switch_detail_labels["switch_id"].text(), "CH1")
        self.assertEqual(window.switch_detail_labels["switch_type_id"].text(), "TIPO-1")
        self.assertEqual(window.switch_detail_labels["circuit_id"].text(), "CIR-1")
        self.assertEqual(window.switch_detail_labels["segment_id"].text(), "T1")
        self.assertEqual(window.switch_detail_labels["code"].text(), "CH-COD")
        self.assertEqual(window.switch_detail_labels["state"].text(), "A")
        self.assertEqual(window.switch_detail_labels["normal_state"].text(), "F")
        self.assertEqual(window.switch_detail_labels["corn"].text(), "N")
        self.assertEqual(window.switch_detail_labels["elo"].text(), "—")
        self.assertEqual(window.switch_detail_labels["elo_type"].text(), "FUSIVEL")

        window._set_selection(FeatureSelection("segment", 1))
        self.assertFalse(window.switch_details_section.isVisible())

        window._set_selection(selection)
        replacement = self._make_switches(network, code="NOVO")
        window._set_switch_model(replacement)
        self.assertEqual(window._selected_feature, selection)
        self.assertEqual(window.switch_detail_labels["code"].text(), "NOVO")

    def test_replacing_segments_removes_switch_model_and_red_layer(self) -> None:
        from circuit_viewer.graphics import SwitchNetworkItem

        window, _, network = self._make_window()
        self.addCleanup(window.close)
        window._set_switch_model(self._make_switches(network))
        self.assertIsNotNone(window._switch_model)
        self.assertEqual(
            len(
                [
                    item
                    for item in window.scene.items()
                    if isinstance(item, SwitchNetworkItem)
                ]
            ),
            1,
        )

        window._set_line_model(network)
        self.assertIsNone(window._switch_model)
        self.assertEqual(
            len(
                [
                    item
                    for item in window.scene.items()
                    if isinstance(item, SwitchNetworkItem)
                ]
            ),
            0,
        )

    def test_replacing_segments_preserves_bar_but_clears_segment_selection(self) -> None:
        window, _, network = self._make_window()
        self.addCleanup(window.close)

        window._set_selection(FeatureSelection("bar", 1))
        window._set_line_model(network)
        self.assertEqual(window._selected_feature, FeatureSelection("bar", 1))

        window._set_selection(FeatureSelection("segment", 0))
        window._set_line_model(network)
        self.assertIsNone(window._selected_feature)
        self.assertFalse(window.segment_selection_overlay.isVisible())

    def test_scene_keeps_one_network_item_and_one_selection_overlay(self) -> None:
        from circuit_viewer.graphics import LineNetworkItem, SegmentSelectionOverlayItem

        window, _, _ = self._make_window()
        self.addCleanup(window.close)
        self.assertEqual(
            len([item for item in window.scene.items() if isinstance(item, LineNetworkItem)]),
            1,
        )
        self.assertEqual(
            len(
                [
                    item
                    for item in window.scene.items()
                    if isinstance(item, SegmentSelectionOverlayItem)
                ]
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
