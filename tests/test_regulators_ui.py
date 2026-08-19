"""Testes de interface dos reguladores de tensão.

Cobrem o botão de importação, a seção do painel lateral, a cascata de
invalidação e a busca global. O importador em si é testado em
``test_regulator_import.py``; aqui os modelos são construídos direto.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox

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
    from circuit_viewer.opendss_powerflow import PowerFlowResult, RegulatorTap
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

    def test_editable_fields_are_line_edits_showing_the_registry(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)

        window._set_selection(FeatureSelection("segment", 0))

        self.assertEqual(set(window.regulator_value_editors), {"vnom", "snom"})
        self.assertEqual(window.regulator_value_editors["vnom"].text(), "13800")
        self.assertEqual(window.regulator_value_editors["snom"].text(), "1000")
        self.assertFalse(window.regulator_restore_button.isVisible())

    def test_an_edit_reaches_the_effective_model_without_touching_the_source(
        self,
    ) -> None:
        """O OpenDSS passa a ler o valor da tela; o retrato do MDB fica igual."""

        window = self._window()
        self._load_network(window)
        source = self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))

        editor = window.regulator_value_editors["snom"]
        editor.setText("276")
        editor.editingFinished.emit()
        self.app.processEvents()

        self.assertEqual(window._regulator_model.record(0).snom, "276")
        self.assertIsNot(window._regulator_model, source)
        # A fonte importada permanece exatamente como veio do banco.
        self.assertIs(window._regulator_source_model, source)
        self.assertEqual(source.record(0).snom, "1000")
        self.assertTrue(window.regulator_restore_button.isVisible())

    def test_an_invalid_number_is_refused_and_the_editor_reloads(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))
        editor = window.regulator_value_editors["snom"]

        editor.setText("1.234,5")
        with patch.object(QMessageBox, "warning") as warning:
            editor.editingFinished.emit()
        self.app.processEvents()

        warning.assert_called_once()
        self.assertEqual(editor.text(), "1000")
        self.assertEqual(window._regulator_model.record(0).snom, "1000")

    def test_typing_the_registry_value_back_clears_the_override(self) -> None:
        window = self._window()
        self._load_network(window)
        source = self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))
        editor = window.regulator_value_editors["snom"]

        editor.setText("276")
        editor.editingFinished.emit()
        editor.setText("1000")
        editor.editingFinished.emit()
        self.app.processEvents()

        self.assertTrue(window._regulator_overrides.is_empty)
        self.assertIs(window._regulator_model, source)
        self.assertFalse(window.regulator_restore_button.isVisible())

    def test_the_restore_button_returns_to_the_database_value(self) -> None:
        window = self._window()
        self._load_network(window)
        source = self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))
        editor = window.regulator_value_editors["snom"]
        editor.setText("276")
        editor.editingFinished.emit()
        self.app.processEvents()

        window.regulator_restore_button.click()
        self.app.processEvents()

        self.assertTrue(window._regulator_overrides.is_empty)
        self.assertIs(window._regulator_model, source)
        self.assertEqual(window.regulator_value_editors["snom"].text(), "1000")

    def test_reimporting_the_regulators_discards_the_session_edits(self) -> None:
        """Recarregar o circuito devolve exatamente o que está no banco."""

        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))
        editor = window.regulator_value_editors["snom"]
        editor.setText("276")
        editor.editingFinished.emit()
        self.app.processEvents()
        self.assertFalse(window._regulator_overrides.is_empty)

        reloaded = self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))

        self.assertTrue(window._regulator_overrides.is_empty)
        self.assertIs(window._regulator_model, reloaded)
        self.assertEqual(window.regulator_value_editors["snom"].text(), "1000")

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

    # ------------------------------------------------------ fluxo de potência

    def _taps_by_step(
        self,
        *,
        d_steps: tuple[float, ...] = (0.0, 8.0, -8.0, 16.0),
        num_taps: int = 32,
        minimum: float = 0.9,
        maximum: float = 1.1,
    ):  # noqa: ANN202
        """Quatro retratos, com o passo pedido na fase D e 0 nas demais.

        O passo vem em pu (``tap - 1.0``) para poder afirmar o inteiro
        resultante sem repetir a conta de ``RegulatorTap.step`` no teste.
        """

        step_size = (maximum - minimum) / num_taps
        return tuple(
            (
                RegulatorTap(
                    phase="D",
                    tap=1.0 + steps * step_size,
                    minimum=minimum,
                    maximum=maximum,
                    num_taps=num_taps,
                ),
                RegulatorTap(
                    phase="E", tap=1.0, minimum=minimum, maximum=maximum,
                    num_taps=num_taps,
                ),
                RegulatorTap(
                    phase="F", tap=1.0, minimum=minimum, maximum=maximum,
                    num_taps=num_taps,
                ),
            )
            for steps in d_steps
        )

    def _install_taps(self, window: MainWindow, taps_by_step) -> None:  # noqa: ANN001
        """Instala um resultado direto em ``_power_flow_result``.

        É o mesmo atalho já usado por
        ``test_importing_regulators_invalidates_only_the_power_flow``: os
        testes de painel só precisam que o resultado exista, não da
        consistência de instantâneo que ``_on_power_flow_finished`` confere.
        """

        window._power_flow_result = PowerFlowResult(
            catalog=None,
            cables=None,
            phase_configuration=None,
            loads=None,
            patterns=None,
            step_count=4,
            regulator_taps={0: taps_by_step},
        )

    def test_the_table_appears_only_with_a_result(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        window._set_selection(FeatureSelection("segment", 0))

        self.assertFalse(window.regulator_tap_table.isVisible())
        self.assertFalse(window.regulator_tap_table_title.isVisible())

        self._install_taps(window, self._taps_by_step())
        window._set_selection(FeatureSelection("segment", 0))

        self.assertTrue(window.regulator_tap_table.isVisible())
        self.assertTrue(window.regulator_tap_table_title.isVisible())

    def test_one_row_per_patamar_and_integer_steps(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        self._install_taps(window, self._taps_by_step())

        window._set_selection(FeatureSelection("segment", 0))

        model = window.regulator_tap_table_model
        self.assertEqual(model.rowCount(), 4)
        self.assertEqual(model.labels, ("Fase D", "Fase E", "Fase F"))
        self.assertEqual(
            [row[0] for row in model.rows], [0.0, 8.0, -8.0, 16.0]
        )
        # Sem casas decimais: "8", não "8.0000".
        self.assertEqual(model.data(model.index(1, 1)), "8")
        self.assertEqual(model.data(model.index(2, 1)), "-8")

    def test_the_resolved_label_still_shows_the_last_patamar(self) -> None:
        # A mudança de forma de regulator_taps não pode alterar esse texto —
        # ele já existia antes da tabela nova. O último patamar do fixture
        # (passo 16, o limite de ±10%/32) também prova que o aviso de fim de
        # curso continua lendo o tap certo.
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        self._install_taps(window, self._taps_by_step())

        window._set_selection(FeatureSelection("segment", 0))

        text = window.regulator_tap_label.text()
        self.assertIn("D: 1.1000", text)
        self.assertIn("no fim do curso", text)

    def test_no_taps_for_this_segment_hides_the_table(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        self._install_taps(window, self._taps_by_step())
        window._set_selection(FeatureSelection("segment", 0))
        self.assertTrue(window.regulator_tap_table.isVisible())

        # Trecho 1 não tem regulador, então result.regulator_taps não o lista.
        window._set_selection(FeatureSelection("segment", 1))

        self.assertFalse(window.regulator_tap_table.isVisible())
        self.assertEqual(window.regulator_tap_table_model.rowCount(), 0)

    def test_invalidating_the_result_clears_and_hides_the_table(self) -> None:
        window = self._window()
        self._load_network(window)
        self._load_regulators(window)
        self._install_taps(window, self._taps_by_step())
        window._set_selection(FeatureSelection("segment", 0))
        self.assertTrue(window.regulator_tap_table.isVisible())

        window._invalidate_power_flow()

        self.assertFalse(window.regulator_tap_table.isVisible())
        self.assertFalse(window.regulator_tap_table_title.isVisible())
        self.assertEqual(window.regulator_tap_table_model.rowCount(), 0)
        self.assertFalse(window.regulator_tap_label.isVisible())

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
