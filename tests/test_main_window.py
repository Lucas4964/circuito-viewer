from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import (
        QApplication,
        QDialog,
        QFormLayout,
        QHeaderView,
        QTableView,
        QToolBar,
    )

    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.load_import import LoadCsvResult
    from circuit_viewer.load_pattern_import import LoadPatternCsvResult
    from circuit_viewer.main_window import (
        ImportChoiceDialog,
        MainWindow,
        UtmImportDialog,
    )
    from circuit_viewer.segment_import import SegmentLoadResult
    from circuit_viewer.switch_import import SwitchLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False

from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    FeatureSelection,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
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

    def _make_loads(self, bars: CircuitModel) -> LoadModel:
        return LoadModel(
            bars,
            ["L1", "L2"],
            [0, 0],
            ["EXT-1", "EXT-2"],
            ["CARGA-1", "CARGA-2"],
            ["10", "20"],
            ["8", "18"],
            ["220", "127"],
            ["ABC", "A"],
            ["Y", "D"],
        )

    def _make_patterns(
        self,
        loads: LoadModel,
        *,
        pd_prefix: str = "P",
    ) -> LoadPatternModel:
        groups: list[tuple[LoadPatternRecord, ...] | None] = [None] * len(loads)
        groups[0] = tuple(
            LoadPatternRecord(
                loads.load_ids[0],
                npat,
                "" if npat == 0 else f"{pd_prefix}{npat}",
                f"PE{npat}",
                f"PF{npat}",
                f"QD{npat}",
                f"QE{npat}",
                f"QF{npat}",
            )
            for npat in range(4)
        )
        return LoadPatternModel(loads, groups)

    def test_single_import_action_opens_choices_with_dependency_state(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        toolbar = window.findChild(QToolBar, "main_toolbar")
        import_actions = [
            action for action in toolbar.actions() if action.text().startswith("Importar")
        ]
        self.assertEqual(window.import_action.text(), "Importar CSV…")
        self.assertEqual(len(import_actions), 0)
        self.assertIn(window.import_action, window.file_menu.actions())
        self.assertTrue(window.show_bars_action.isChecked())
        self.assertFalse(window.show_bars_action.isEnabled())
        self.assertNotIn(window.show_bars_action, toolbar.actions())

        without_bars = ImportChoiceDialog(False, False, window)
        self.assertTrue(without_bars.bars_button.isEnabled())
        self.assertFalse(without_bars.segments_button.isEnabled())
        self.assertFalse(without_bars.loads_button.isEnabled())
        self.assertFalse(without_bars.load_patterns_button.isEnabled())
        self.assertFalse(without_bars.switches_button.isEnabled())
        without_bars.bars_button.click()
        self.assertEqual(without_bars.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(without_bars.selected_kind, "bars")

        with_bars = ImportChoiceDialog(True, False, window)
        self.assertTrue(with_bars.segments_button.isEnabled())
        self.assertTrue(with_bars.loads_button.isEnabled())
        self.assertFalse(with_bars.load_patterns_button.isEnabled())
        self.assertFalse(with_bars.switches_button.isEnabled())
        with_bars.segments_button.click()
        self.assertEqual(with_bars.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(with_bars.selected_kind, "segments")

        loads_choice = ImportChoiceDialog(True, False, window)
        loads_choice.loads_button.click()
        self.assertEqual(loads_choice.selected_kind, "loads")

        patterns_choice = ImportChoiceDialog(
            True,
            False,
            window,
            has_loads=True,
        )
        self.assertTrue(patterns_choice.load_patterns_button.isEnabled())
        patterns_choice.load_patterns_button.click()
        self.assertEqual(patterns_choice.selected_kind, "load_patterns")

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
            window.load_details_grid,
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
            *window.load_caption_labels.values(),
            *window.load_detail_labels.values(),
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

    def test_load_details_visibility_and_bar_reimport_invalidation(self) -> None:
        window, bars, _ = self._make_window()
        self.addCleanup(window.close)
        loads = self._make_loads(bars)
        window._on_load_import_finished(
            LoadCsvResult(loads, "utf-8-sig", 2, 2, 0, (), 0)
        )

        self.assertTrue(window.show_loads_action.isEnabled())
        self.assertEqual(window.load_status.text(), "Cargas: 2")
        window._set_selection(FeatureSelection("load", 0))
        self.assertEqual(window.details_dock.windowTitle(), "Carga selecionada")
        self.assertEqual(window.load_detail_labels["load_id"].text(), "L1")
        self.assertEqual(window.load_detail_labels["bar_id"].text(), "B1")
        self.assertEqual(window.load_detail_labels["external_id"].text(), "EXT-1")
        self.assertEqual(window.load_detail_labels["snom"].text(), "10")
        self.assertEqual(window.load_detail_labels["connection_type"].text(), "Y")
        self.assertTrue(window.load_virtualizer.selection_overlay.isVisible())

        window.show_bars_action.setChecked(False)
        self.assertEqual(window._selected_feature, FeatureSelection("load", 0))
        self.assertTrue(window.load_virtualizer.loads_visible)
        window.show_loads_action.setChecked(False)
        self.assertIsNone(window._selected_feature)
        self.assertFalse(window.load_virtualizer.loads_visible)

        window._on_import_finished(
            CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0)
        )
        self.assertIsNone(window._load_model)
        self.assertFalse(window.show_loads_action.isEnabled())
        self.assertEqual(window.load_status.text(), "Cargas: 0")

    def test_loads_follow_circuit_filters_but_not_bar_visibility(self) -> None:
        window, bars, network = self._make_window()
        self.addCleanup(window.close)
        loads = self._make_loads(bars)
        window._set_load_model(loads)
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B1", "CIR-1", "13.8")],
        )

        window._set_circuit_catalog(catalog, checked=(False,))

        self.assertFalse(window.load_virtualizer._visibility_mask[0])
        self.assertFalse(window.load_virtualizer._visibility_mask[1])
        self.assertEqual(window.load_virtualizer.overview_item.visible_point_count, 0)

        window._set_circuit_catalog(catalog, checked=(True,))
        window.show_bars_action.setChecked(False)
        self.assertTrue(window.load_virtualizer._visibility_mask[0])
        self.assertTrue(window.load_virtualizer.loads_visible)

    def test_load_patterns_table_is_conditional_ordered_and_read_only(self) -> None:
        window, bars, _ = self._make_window()
        self.addCleanup(window.close)
        loads = self._make_loads(bars)
        window._set_load_model(loads)
        window._set_selection(FeatureSelection("load", 0))
        self.assertFalse(window.load_patterns_section.isVisible())
        scene_items = tuple(window.scene.items())
        search_count = len(window.search_index)

        patterns = self._make_patterns(loads)
        window._on_load_pattern_import_finished(
            LoadPatternCsvResult(patterns, "utf-8-sig", 4, 4, 0, (), 0)
        )

        table_model = window.load_pattern_table_model
        self.assertTrue(window.load_patterns_section.isVisible())
        self.assertEqual(table_model.rowCount(), 4)
        self.assertEqual(table_model.columnCount(), 8)
        self.assertEqual(
            [
                table_model.headerData(
                    column,
                    Qt.Orientation.Horizontal,
                    Qt.ItemDataRole.DisplayRole,
                )
                for column in range(8)
            ],
            ["CARGA_ID", "NPAT", "PD", "PE", "PF", "QD", "QE", "QF"],
        )
        self.assertEqual(table_model.data(table_model.index(0, 1)), "0")
        self.assertEqual(table_model.data(table_model.index(3, 1)), "3")
        self.assertEqual(table_model.data(table_model.index(0, 2)), "—")
        self.assertEqual(
            table_model.data(
                table_model.index(1, 2),
                Qt.ItemDataRole.ToolTipRole,
            ),
            "P1",
        )
        self.assertFalse(
            table_model.flags(table_model.index(0, 0))
            & Qt.ItemFlag.ItemIsEditable
        )
        self.assertEqual(tuple(window.scene.items()), scene_items)
        self.assertEqual(len(window.search_index), search_count)

        replacement = self._make_patterns(loads, pd_prefix="NOVO")
        window._set_load_pattern_model(replacement)
        self.assertEqual(table_model.data(table_model.index(1, 2)), "NOVO1")

        window._set_selection(FeatureSelection("load", 1))
        self.assertFalse(window.load_patterns_section.isVisible())
        self.assertEqual(table_model.rowCount(), 0)

        window._set_load_model(loads)
        self.assertIsNone(window._load_pattern_model)
        self.assertFalse(window.load_patterns_section.isVisible())

    def test_detail_tables_allow_column_resizing(self) -> None:
        window, _, _ = self._make_window()
        self.addCleanup(window.close)

        tables = (
            window.load_patterns_table,
            window.equivalent_patterns_table,
            window.findChild(QTableView, "bar_power_flow_quantity_table"),
            window.findChild(QTableView, "segment_power_flow_quantity_table"),
        )
        for table in tables:
            self.assertIsNotNone(table)
            header = table.horizontalHeader()
            with self.subTest(table=table.objectName() or "patterns"):
                for column in range(header.count()):
                    self.assertEqual(
                        header.sectionResizeMode(column),
                        QHeaderView.ResizeMode.Interactive,
                    )

    def test_detail_tables_have_internal_cell_padding(self) -> None:
        # As quatro tabelas "estilo Excel" do painel — fluxo de potência e
        # patamares — precisam da margem interna, não só das outras janelas
        # com tabela que reaproveitam o mesmo mecanismo de arraste.
        window, _, _ = self._make_window()
        self.addCleanup(window.close)

        tables = (
            window.load_patterns_table,
            window.equivalent_patterns_table,
            window.findChild(QTableView, "bar_power_flow_quantity_table"),
            window.findChild(QTableView, "segment_power_flow_quantity_table"),
        )
        for table in tables:
            with self.subTest(table=table.objectName() or "patterns"):
                self.assertIn("padding", table.styleSheet())

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


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class UtmImportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_suggested_unit_is_preselected(self) -> None:
        dialog = UtmImportDialog("barras.csv", suggested_scale=10.0)
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.unit_input.currentText(), "Decímetros")
        self.assertEqual(dialog.coordinate_scale(), 10.0)

    def test_dialog_exposes_only_crs_and_unit(self) -> None:
        dialog = UtmImportDialog("barras.csv", suggested_scale=10.0)
        self.addCleanup(dialog.deleteLater)

        self.assertFalse(hasattr(dialog, "custom_scale_input"))
        self.assertFalse(hasattr(dialog, "range_label"))
        layout = dialog.layout()
        captions = [
            layout.itemAt(row, QFormLayout.ItemRole.LabelRole).widget().text()
            for row in range(layout.rowCount())
            if layout.itemAt(row, QFormLayout.ItemRole.LabelRole) is not None
        ]
        self.assertEqual(
            captions,
            ["Arquivo:", "Zona UTM:", "Hemisfério:", "Unidade das coordenadas:"],
        )

    def test_defaults_to_metres_without_a_suggestion(self) -> None:
        dialog = UtmImportDialog("barras.csv")
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.coordinate_scale(), 1.0)
        self.assertEqual(dialog.crs(), UtmCrs(21, northern=False))

    def test_unknown_factor_falls_back_to_metres(self) -> None:
        # Fator fora de COORDINATE_UNITS não deve deixar o combo em estado
        # inválido; o relatório de importação é quem avisa sobre o envelope UTM.
        dialog = UtmImportDialog("barras.csv", suggested_scale=2.5)
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.coordinate_scale(), 1.0)
        self.assertEqual(dialog.unit_input.currentText(), "Metros")


if __name__ == "__main__":
    unittest.main()
