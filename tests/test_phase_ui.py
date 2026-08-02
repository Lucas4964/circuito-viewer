from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import numpy as np
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import (
        CircuitCatalogModel,
        CircuitDefinition,
        CircuitModel,
        LineNetworkModel,
        SwitchModel,
        UtmCrs,
    )
    from circuit_viewer.phase_config import PHASE_COLORS
    from circuit_viewer.segment_import import SegmentLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class PhaseVisualizationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "fases2.json"
        self.config_path.write_text(
            json.dumps(
                [
                    {"FASES2": "1", "NUMERO_FASES": 1},
                    {"FASES2": "2", "NUMERO_FASES": 2},
                    {"FASES2": "13", "NUMERO_FASES": 3},
                ]
            ),
            encoding="utf-8",
        )

    def make_window(self):
        bars = CircuitModel(
            [f"B{index}" for index in range(5)],
            [""] * 5,
            [500_000.0 + index * 100.0 for index in range(5)],
            [8_000_000.0] * 5,
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
            bars,
            [f"T{index}" for index in range(4)],
            [""] * 4,
            ["1", "2", "13", "X"],
            [0, 1, 2, 3],
            [1, 2, 3, 4],
            [""] * 4,
            [""] * 4,
            [""] * 4,
            [100.0] * 4,
        )
        switches = SwitchModel(
            segments,
            ["CH0"],
            ["TC"],
            ["C0"],
            [1],
            [""],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        window = MainWindow(self.config_path)
        self.addCleanup(window.close)
        window.show()
        window._on_import_finished(
            CsvLoadResult(bars, "utf-8-sig", 5, 5, 0, (), 0)
        )
        window._on_segment_import_finished(
            SegmentLoadResult(segments, "utf-8-sig", 4, 4, 0, (), 0)
        )
        window._set_switch_model(switches)
        catalog = CircuitCatalogModel.build(
            segments,
            switches,
            [CircuitDefinition("C0", "B0", "", "")],
        )
        window._set_circuit_catalog(catalog, colors=("#7A2E8E",))
        self.app.processEvents()
        return window, bars, segments, switches, catalog

    def test_menu_mode_overrides_circuit_colors_and_restores_them(self) -> None:
        empty = MainWindow(self.config_path)
        self.addCleanup(empty.close)
        self.assertFalse(empty.phase_coloring_action.isEnabled())
        view_menu = next(
            action.menu()
            for action in empty.menuBar().actions()
            if action.text() == "Visualizar"
        )
        self.assertIn(empty.phase_coloring_action, view_menu.actions())

        window, _, _, _, _ = self.make_window()
        self.assertTrue(window.phase_coloring_action.isEnabled())
        window.phase_coloring_action.setChecked(True)
        self.app.processEvents()

        self.assertTrue(window.phase_legend.isVisible())
        self.assertEqual(window.phase_legend.labels[3].text(), "Sem relação (1)")
        self.assertEqual(window._line_item._colors, PHASE_COLORS)
        self.assertEqual(window._switch_item._colors, PHASE_COLORS)
        self.assertEqual(window._line_item.category_path_count, 3)
        self.assertEqual(window._switch_item.colored_path_count, 1)
        self.assertIn("FASES2: X", window.statusBar().currentMessage())

        window._circuit_visibility.set_color(0, "#123456")
        window._apply_circuit_visibility()
        self.assertEqual(window._line_item._colors, PHASE_COLORS)
        self.assertEqual(window._switch_item._colors, PHASE_COLORS)

        window.phase_coloring_action.setChecked(False)
        self.assertFalse(window.phase_legend.isVisible())
        self.assertEqual(window._line_item._colors, ("#123456",))
        self.assertEqual(window._switch_item.colored_path_count, 0)

    def test_mode_preserves_circuit_visibility_masks(self) -> None:
        window, _, _, _, catalog = self.make_window()
        window._set_circuit_catalog(catalog, checked=(False,), colors=("#7A2E8E",))
        expected_mask = window._circuit_visibility.segment_visible_mask.copy()

        window.phase_coloring_action.setChecked(True)

        self.assertEqual(window._circuit_visibility.checked_states, (False,))
        np.testing.assert_array_equal(window._line_item._visibility_mask, expected_mask)
        np.testing.assert_array_equal(
            window._switch_item._segment_visibility_mask,
            expected_mask,
        )

    def test_legend_remains_anchored_to_viewport_during_navigation(self) -> None:
        window, _, _, _, _ = self.make_window()
        window.phase_coloring_action.setChecked(True)
        self.app.processEvents()

        def assert_anchored() -> None:
            self.app.processEvents()
            viewport = window.view.viewport()
            legend = window.phase_legend
            self.assertEqual(legend.x(), 12)
            self.assertEqual(
                legend.y(),
                max(12, viewport.height() - legend.height() - 12),
            )

        assert_anchored()
        window.view.zoom_at(window.view.viewport().rect().center(), 10.0)
        assert_anchored()

        window._fit_all()
        assert_anchored()

        scroll_bar = window.view.horizontalScrollBar()
        current = scroll_bar.value()
        target = min(scroll_bar.maximum(), current + 25)
        if target == current:
            target = max(scroll_bar.minimum(), current - 25)
        scroll_bar.setValue(target)
        assert_anchored()

        window.view.focus_bar(2)
        assert_anchored()
        window.resize(860, 620)
        assert_anchored()

        window.view.zoom_at(window.view.viewport().rect().center(), 1e12)
        self.app.processEvents()
        self.assertEqual(
            window.statusBar().currentMessage(),
            "Limite máximo de zoom atingido.",
        )
        assert_anchored()

    def test_reimport_reclassifies_and_preserves_checked_mode(self) -> None:
        window, bars, _, _, _ = self.make_window()
        window.phase_coloring_action.setChecked(True)
        replacement = LineNetworkModel(
            bars,
            ["TN"],
            [""],
            ["1"],
            [0],
            [1],
            [""],
            [""],
            [""],
            [100.0],
        )

        window._set_line_model(replacement)

        self.assertTrue(window.phase_coloring_action.isChecked())
        self.assertTrue(window.phase_coloring_action.isEnabled())
        self.assertEqual(window._phase_classification.unmapped_count, 0)
        self.assertEqual(window.phase_legend.labels[3].text(), "Sem relação (0)")
        self.assertTrue(window.phase_legend.isVisible())
        self.assertEqual(window._line_item.category_path_count, 1)

    def test_invalid_configuration_warns_once_and_disables_mode(self) -> None:
        missing = self.root / "ausente.json"
        with patch.object(QMessageBox, "warning") as warning:
            window = MainWindow(missing)
            self.addCleanup(window.close)
            window.show()
            self.app.processEvents()

        self.assertEqual(warning.call_count, 1)
        self.assertFalse(window.phase_coloring_action.isEnabled())
        self.assertFalse(window.branches_action.isEnabled())
        self.assertIn("Não foi possível ler", window.phase_coloring_action.toolTip())
        self.assertIn("Não foi possível ler", window.branches_action.toolTip())
        self.assertIn(str(missing), warning.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
