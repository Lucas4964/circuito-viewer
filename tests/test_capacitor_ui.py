"""Interface dos bancos de capacitores: símbolo, painel e cascata."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.capacitor_import import CapacitorCsvResult
    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import (
        CapacitorModel,
        CircuitModel,
        FeatureSelection,
        LineNetworkModel,
        UtmCrs,
    )
    from circuit_viewer.segment_import import SegmentLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


def make_bars() -> CircuitModel:
    return CircuitModel(
        ["B0", "B1", "B2"],
        ["COD-A", "COD-B", "COD-C"],
        [500_000.0, 500_100.0, 500_200.0],
        [8_000_000.0, 8_000_000.0, 8_000_000.0],
        UtmCrs(21, northern=False),
    )


def make_network(bars: CircuitModel) -> LineNetworkModel:
    return LineNetworkModel(
        bars,
        ["T0", "T1"],
        ["TR-1", "TR-2"],
        ["13", "13"],
        [0, 1],
        [1, 2],
        ["", ""],
        ["CB1", "CB1"],
        ["", ""],
        [250.0, 400.0],
    )


def make_capacitors(bars: CircuitModel) -> CapacitorModel:
    return CapacitorModel(
        bars,
        ["239"],
        [1],
        ["34559653"],
        ["CAP-1"],
        ["13.8"],
        ["600"],
        ["600"],
        ["600"],
        ["600"],
        ["DEFN"],
        ["0"],
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CapacitorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        return window

    def _load(self, window: MainWindow):  # noqa: ANN202
        bars = make_bars()
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0))
        network = make_network(bars)
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 2, 2, 0, (), 0)
        )
        model = make_capacitors(window._model)
        window._set_capacitor_model(model)
        self.app.processEvents()
        return model

    def test_the_action_and_the_status_follow_the_model(self) -> None:
        window = self._window()
        self.assertFalse(window.show_capacitors_action.isEnabled())
        self.assertEqual(window.capacitor_status.text(), "Capacitores: 0")

        self._load(window)

        self.assertTrue(window.show_capacitors_action.isEnabled())
        self.assertEqual(window.capacitor_status.text(), "Capacitores: 1")
        view_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.text() == "Visualizar"
        )
        self.assertIn(window.show_capacitors_action, view_menu.actions())

    def test_the_symbol_uses_its_own_kind(self) -> None:
        window = self._window()
        self._load(window)

        self.assertEqual(window.capacitor_virtualizer.symbol_kind, "capacitor")

    def test_selecting_a_bank_fills_the_panel(self) -> None:
        window = self._window()
        self._load(window)

        window._set_selection(FeatureSelection("capacitor", 0))

        values = {
            key: label.text()
            for key, label in window.capacitor_detail_labels.items()
        }
        self.assertEqual(
            values,
            {
                "capacitor_id": "239",
                "bar_id": "B1",
                "external_id": "34559653",
                "code": "CAP-1",
                "nominal_voltage": "13.8",
                "q1": "600",
                "q2": "600",
                "q3": "600",
                "q4": "600",
                "phases": "DEFN",
                "connection_type": "0",
            },
        )
        self.assertIs(
            window.details_stack.currentWidget(), window.capacitor_details_page
        )

    def test_a_model_from_other_bars_is_refused(self) -> None:
        window = self._window()
        self._load(window)
        other = make_capacitors(make_bars())

        with self.assertRaises(ValueError):
            window._set_capacitor_model(other)

    def test_reimporting_bars_drops_the_capacitors(self) -> None:
        """Os bancos penduram nas barras por índice: barras novas os invalidam."""

        window = self._window()
        self._load(window)
        self.assertIsNotNone(window._capacitor_model)

        window._on_import_finished(
            CsvLoadResult(make_bars(), "utf-8-sig", 3, 3, 0, (), 0)
        )
        self.app.processEvents()

        self.assertIsNone(window._capacitor_model)
        self.assertFalse(window.show_capacitors_action.isEnabled())

    def test_hiding_the_layer_clears_a_capacitor_selection(self) -> None:
        window = self._window()
        self._load(window)
        window._set_selection(FeatureSelection("capacitor", 0))

        window.show_capacitors_action.setChecked(False)
        self.app.processEvents()

        self.assertIsNone(window._selected_feature)

    def test_the_import_result_installs_the_model(self) -> None:
        window = self._window()
        bars = make_bars()
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0))
        model = make_capacitors(window._model)

        result = CapacitorCsvResult(model, "utf-8-sig", 1, 1, 0, (), 0)
        window._set_capacitor_model(result.model)

        self.assertIs(window._capacitor_model, model)
        self.assertFalse(result.has_warnings)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
