from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.branch_analysis import analyze_branches
    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import (
        CircuitCatalogModel,
        CircuitDefinition,
        CircuitModel,
        FeatureSelection,
        LineNetworkModel,
        UtmCrs,
    )
    from circuit_viewer.phase_config import load_phase_configuration
    from circuit_viewer.segment_import import SegmentLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BranchesUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "fases2.json"
        self.config_path.write_text(
            json.dumps(
                [
                    {"FASES2": "D", "NOME": "D", "NUMERO_FASES": 1},
                    {"FASES2": "E", "NOME": "E", "NUMERO_FASES": 1},
                    {"FASES2": "DEF", "NOME": "DEF", "NUMERO_FASES": 3},
                ]
            ),
            encoding="utf-8",
        )

    def make_window(self, *, two_circuits: bool = False):
        bars = CircuitModel(
            ["B0", "B1", "B2", "B3", "B4"],
            ["CB0", "CB1", "CB2", "CB3", "CB4"],
            [500_000.0, 500_100.0, 500_200.0, 500_100.0, 500_100.0],
            [8_000_000.0, 8_000_000.0, 8_000_000.0, 7_999_900.0, 7_999_800.0],
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
            bars,
            ["T0", "T1", "T2", "T3"],
            ["CT0", "CT1", "CT2", "CT3"],
            ["DEF", "DEF", "D", "D"],
            [0, 1, 1, 3],
            [1, 2, 3, 4],
            [""] * 4,
            [""] * 4,
            [""] * 4,
            [100.0] * 4,
        )
        definitions = [CircuitDefinition("C1", "B0", "", "")]
        if two_circuits:
            definitions.append(CircuitDefinition("C2", "B0", "", ""))
        catalog = CircuitCatalogModel.build(segments, None, definitions)
        window = MainWindow(self.config_path)
        self.addCleanup(window.close)
        window.show()
        window._on_import_finished(
            CsvLoadResult(bars, "utf-8", 5, 5, 0, (), 0)
        )
        window._on_segment_import_finished(
            SegmentLoadResult(segments, "utf-8", 4, 4, 0, (), 0)
        )
        window._set_circuit_catalog(catalog)
        self.app.processEvents()
        return window, bars, segments, catalog

    def wait_for_analysis(self, window: MainWindow) -> None:
        deadline = time.monotonic() + 3.0
        while window._branch_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertIsNone(window._branch_thread)

    def test_tools_menu_runs_background_analysis_and_populates_table(self) -> None:
        empty = MainWindow(self.config_path)
        self.addCleanup(empty.close)
        tools_menu = next(
            action.menu()
            for action in empty.menuBar().actions()
            if action.text() == "Ferramentas"
        )
        self.assertIn(empty.branches_action, tools_menu.actions())
        self.assertFalse(empty.branches_action.isEnabled())

        window, _, _, _ = self.make_window()
        self.assertTrue(window.branches_action.isEnabled())

        window._show_or_analyze_branches()
        self.wait_for_analysis(window)

        self.assertIsNotNone(window._branch_analysis_result)
        self.assertTrue(window.branches_window.isVisible())
        self.assertFalse(window.branches_window.isModal())
        model = window.branch_table_model
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.columnCount(), 16)
        self.assertEqual(model.data(model.index(0, 0)), "C1")
        self.assertEqual(model.data(model.index(0, 1)), "B1")
        self.assertEqual(model.data(model.index(0, 3)), "T2")
        self.assertEqual(model.data(model.index(0, 5)), "2")
        self.assertEqual(model.data(model.index(0, 6)), "200.000")
        self.assertEqual(model.data(model.index(0, 8)), "D")

        cached = window._branch_analysis_result
        window._show_or_analyze_branches()
        self.assertIs(window._branch_analysis_result, cached)
        self.assertIsNone(window._branch_thread)

    def test_filter_selection_reactivates_circuit_and_highlights_whole_branch(self) -> None:
        window, _, segments, catalog = self.make_window(two_circuits=True)
        configuration = load_phase_configuration(self.config_path)
        result = analyze_branches(catalog, configuration)
        window._branch_analysis_result = result
        window.branches_window.set_result(result)
        window._show_branches_window()

        self.assertEqual(window.branches_window.circuit_filter.count(), 3)
        window.branches_window.circuit_filter.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(window.branches_window.proxy_model.rowCount(), 1)

        first_record = result.records[0]
        circuit_check = window.circuit_table_model.index(
            first_record.circuit_index,
            0,
        )
        window.circuit_table_model.setData(
            circuit_check,
            Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        window._apply_circuit_visibility()
        self.assertFalse(
            window._circuit_visibility.is_visible(first_record.circuit_index)
        )

        window.branches_window.table.selectRow(0)
        self.app.processEvents()

        self.assertTrue(
            window._circuit_visibility.is_visible(first_record.circuit_index)
        )
        self.assertIs(window._selected_branch, first_record)
        self.assertIsNone(window._selected_feature)
        self.assertTrue(window.branch_highlight_overlay.isVisible())
        self.assertEqual(
            set(window.branch_highlight_overlay.segment_indices),
            {2, 3},
        )
        self.assertGreater(
            window.branch_highlight_overlay.zValue(),
            window.segment_selection_overlay.zValue(),
        )

        before = abs(window.view.transform().m11())
        window._activate_branch(first_record)
        self.app.processEvents()
        self.assertLessEqual(abs(window.view.transform().m11()), 4.0)
        self.assertNotEqual(abs(window.view.transform().m11()), before)

        window._set_selection(FeatureSelection("segment", 0))
        self.assertFalse(window.branch_highlight_overlay.isVisible())
        self.assertFalse(window.branches_window.table.selectionModel().hasSelection())
        self.assertEqual(window._selected_feature, FeatureSelection("segment", 0))
        self.assertIs(window._line_model, segments)

    def test_phase_mode_survives_branch_selection_and_data_change_invalidates(self) -> None:
        window, _, _, catalog = self.make_window()
        result = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        window._branch_analysis_result = result
        window.branches_window.set_result(result)
        window.phase_coloring_action.setChecked(True)
        window.branches_window.table.selectRow(0)
        self.app.processEvents()

        self.assertTrue(window.phase_coloring_action.isChecked())
        self.assertTrue(window.branch_highlight_overlay.isVisible())

        window._set_load_model(None)

        self.assertIsNone(window._branch_analysis_result)
        self.assertFalse(window.branch_highlight_overlay.isVisible())
        self.assertFalse(window.branches_window.isVisible())
        self.assertTrue(window.branches_action.isEnabled())


if __name__ == "__main__":
    unittest.main()

