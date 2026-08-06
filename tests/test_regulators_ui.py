"""Testes de interface dos reguladores de tensão.

Cobrem o botão de importação, a seção do painel lateral, a cascata de
invalidação e a busca global. O importador em si é testado em
``test_regulator_import.py``; aqui os modelos são construídos direto.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.main_window import ImportChoiceDialog, MainWindow
    from circuit_viewer.model import (
        CircuitModel,
        FeatureSelection,
        LineNetworkModel,
        RegulatorModel,
        SwitchModel,
        UtmCrs,
    )
    from circuit_viewer.regulator_import import RegulatorLoadResult
    from circuit_viewer.segment_import import SegmentLoadResult
    from circuit_viewer.switch_import import SwitchLoadResult

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


def make_regulators(network: LineNetworkModel) -> RegulatorModel:
    """Um regulador no trecho 0; o trecho 1 fica sem."""

    return RegulatorModel(
        network,
        ["RG1"],
        [0],
        ["EXT-9"],
        ["REG-01"],
        ["Y"],
        ["1000"],
        ["10"],
        ["32"],
        ["3"],
        ["100"],
        ["13800"],
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class RegulatorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        return window

    def _load_network(self, window: MainWindow) -> LineNetworkModel:
        bars = make_bars()
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0))
        network = make_network(bars)
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 2, 2, 0, (), 0)
        )
        self.app.processEvents()
        return network

    def _load_regulators(self, window: MainWindow) -> RegulatorModel:
        model = make_regulators(window._line_model)
        window._on_regulator_import_finished(
            RegulatorLoadResult(model, "utf-8-sig", 1, 1, 0, (), 0)
        )
        self.app.processEvents()
        return model

    # -------------------------------------------------------------- importação

    def test_the_import_button_requires_segments(self) -> None:
        window = self._window()

        without = ImportChoiceDialog(False, False, window)
        self.addCleanup(without.close)
        self.assertFalse(without.regulators_button.isEnabled())

        with_segments = ImportChoiceDialog(True, True, window)
        self.addCleanup(with_segments.close)
        self.assertTrue(with_segments.regulators_button.isEnabled())

    def test_the_button_reports_the_chosen_kind(self) -> None:
        window = self._window()
        dialog = ImportChoiceDialog(True, True, window)
        self.addCleanup(dialog.close)

        dialog.regulators_button.click()

        self.assertEqual(dialog.selected_kind, "regulators")

    def test_a_model_from_another_import_is_refused(self) -> None:
        window = self._window()
        self._load_network(window)
        # Reguladores pendurados noutra instância de trechos: a regra de
        # identidade do projeto é por objeto, não por conteúdo.
        other = make_regulators(make_network(make_bars()))

        with self.assertRaises(ValueError):
            window._set_regulator_model(other)

    # ------------------------------------------------------------------ painel

    def test_the_section_appears_only_on_a_segment_with_a_regulator(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)

        window._set_selection(FeatureSelection("segment", 0))
        self.assertTrue(window.regulator_details_section.isVisible())

        window._set_selection(FeatureSelection("segment", 1))
        self.assertFalse(window.regulator_details_section.isVisible())

    def test_every_column_reaches_the_panel(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)

        window._set_selection(FeatureSelection("segment", 0))

        values = {
            key: label.text()
            for key, label in window.regulator_detail_labels.items()
        }
        self.assertEqual(
            values,
            {
                "regulator_id": "RG1",
                "segment_id": "T0",
                "external_id": "EXT-9",
                "code": "REG-01",
                "connection": "Y",
                "snom": "1000",
                "regulation_range": "10",
                "step_count": "32",
                "tap": "3",
                "inom": "100",
                "vnom": "13800",
            },
        )

    def test_empty_fields_become_a_dash(self) -> None:
        window = self._window()
        network = self._load_network(window)
        model = RegulatorModel(
            network,
            ["RG1"],
            [0],
            [""],
            [""],
            [""],
            [""],
            [""],
            [""],
            [""],
            [""],
            [""],
        )
        window._on_regulator_import_finished(
            RegulatorLoadResult(model, "utf-8-sig", 1, 1, 0, (), 0)
        )

        window._set_selection(FeatureSelection("segment", 0))

        self.assertEqual(window.regulator_detail_labels["code"].text(), "—")
        # O REGU_ID nunca é vazio: o modelo o recusaria.
        self.assertEqual(window.regulator_detail_labels["regulator_id"].text(), "RG1")

    def test_the_section_is_hidden_without_regulators(self) -> None:
        window = self._window()
        self._load_network(window)

        window._set_selection(FeatureSelection("segment", 0))

        self.assertFalse(window.regulator_details_section.isVisible())

    def test_selecting_a_bar_hides_the_section(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))

        window._set_selection(FeatureSelection("bar", 0))

        self.assertFalse(window.regulator_details_section.isVisible())

    def test_the_section_sits_below_the_switch_one(self) -> None:
        window = self._window()
        self._load_network(window)
        network = window._line_model
        switches = SwitchModel(
            network, ["CH1"], ["TC"], ["C1"], [0], ["CHV-1"], ["1"], ["1"],
            [""], [""], [""],
        )
        window._on_switch_import_finished(
            SwitchLoadResult(switches, "utf-8-sig", 1, 1, 0, (), 0)
        )
        self._load_regulators(window)

        window._set_selection(FeatureSelection("segment", 0))

        # Chave e regulador coexistem no mesmo trecho, nessa ordem no painel.
        self.assertTrue(window.switch_details_section.isVisible())
        self.assertTrue(window.regulator_details_section.isVisible())
        layout = window.segment_details_body.layout()
        self.assertLess(
            layout.indexOf(window.switch_details_section),
            layout.indexOf(window.regulator_details_section),
        )

    # --------------------------------------------------------------- cascata

    def test_reimporting_segments_discards_the_regulators(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))

        self._load_network(window)

        self.assertIsNone(window._regulator_model)
        self.assertFalse(window.regulator_details_section.isVisible())

    def test_importing_regulators_invalidates_only_the_power_flow(self) -> None:
        window = self._window()
        self._load_network(window)
        # Reguladores seguem fora da topologia, então o que depende dela não é
        # tocado. Mas eles são exportados e regulam a tensão: um resultado de
        # fluxo calculado com outro conjunto deixa de valer.
        marker = object()
        window._branch_analysis_result = marker
        window._power_flow_result = marker

        self._load_regulators(window)

        self.assertIs(window._branch_analysis_result, marker)
        self.assertIsNone(window._power_flow_result)

    # ------------------------------------------------------------------ busca

    def test_regulators_enter_the_global_search(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)

        results = window.search_index.query("REG-01").results
        matches = [result for result in results if result.kind == "regulator"]

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].entity_id, "RG1")
        # O alvo é o trecho: o regulador não tem geometria própria.
        self.assertEqual(matches[0].target, FeatureSelection("segment", 0))
        self.assertIn("Regulador", matches[0].identity_text)

    def test_activating_a_regulator_result_selects_its_segment(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        result = next(
            item
            for item in window.search_index.query("REG-01").results
            if item.kind == "regulator"
        )

        window._activate_search_result(result)

        self.assertEqual(window._selected_feature, FeatureSelection("segment", 0))
        self.assertTrue(window.regulator_details_section.isVisible())
        self.assertEqual(window.details_dock.windowTitle(), "Regulador selecionado")

    def test_the_search_forgets_them_when_the_segments_change(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)

        self._load_network(window)

        results = window.search_index.query("REG-01").results
        self.assertEqual(
            [result for result in results if result.kind == "regulator"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
