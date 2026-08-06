"""Testes de interface do fluxo de potência.

Nenhum destes testes toca a DLL do OpenDSS: o resultado é montado à mão e
injetado, porque o que se verifica aqui é a integração — disponibilidade do
botão, exclusão mútua de threads, invalidação em cascata e o painel com
combobox.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QToolBar

    from circuit_viewer.cable_import import CableCsvResult
    from circuit_viewer.circuit_import import CircuitLoadResult
    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.load_import import LoadCsvResult
    from circuit_viewer.load_pattern_import import LoadPatternCsvResult
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import (
        CableModel,
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
    from circuit_viewer.opendss_powerflow import (
        BarVoltages,
        PowerFlowIssue,
        PowerFlowResult,
        SegmentCurrents,
    )
    from circuit_viewer.segment_import import SegmentLoadResult
    from circuit_viewer.switch_import import SwitchLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


def make_cables(iadm: str = "340") -> CableModel:
    return CableModel(
        ["CB1"],
        ["1"],
        ["4/0"],
        [iadm],
        ["0,00824"],
        ["0,367"],
        ["0,42"],
        ["1,2"],
        ["0,551"],
        ["1,232"],
        ["0,367"],
        ["0,42"],
        ["ALUMINIO 4/0"],
        ["EXT-1"],
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class PowerFlowUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        return window

    def _load_everything(
        self,
        window: MainWindow,
        *,
        with_loads: bool = True,
        cables: CableModel | None = None,
    ) -> None:
        bars = CircuitModel(
            ["B0", "B1", "B2"],
            ["COD-A", "COD-B", "COD-C"],
            [500_000.0, 500_100.0, 500_200.0],
            [8_000_000.0, 8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0))
        network = LineNetworkModel(
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
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 2, 2, 0, (), 0)
        )
        switches = SwitchModel(
            network,
            ["CH1"],
            ["TC"],
            ["C1"],
            [1],
            ["CHV-1"],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        window._on_switch_import_finished(
            SwitchLoadResult(switches, "utf-8-sig", 1, 1, 0, (), 0)
        )
        catalog = CircuitCatalogModel.build(
            network,
            switches,
            [CircuitDefinition("C1", "B0", "ALIMENTADOR", "13,8")],
        )
        window._on_circuit_import_finished(
            CircuitLoadResult(catalog, "utf-8-sig", 1, 1, 0, (), 0)
        )
        window._on_cable_import_finished(
            CableCsvResult(
                cables or make_cables(),
                "utf-8-sig",
                1,
                1,
                0,
                0,
                (),
                0,
            )
        )
        if with_loads:
            loads = LoadModel(
                bars,
                ["CG1"],
                [1],
                ["EXT-1"],
                ["CARGA-1"],
                ["10"],
                ["12"],
                ["220"],
                ["1"],
                ["Y"],
            )
            window._on_load_import_finished(
                LoadCsvResult(loads, "utf-8-sig", 1, 1, 0, (), 0)
            )
            patterns = LoadPatternModel(
                loads,
                [
                    tuple(
                        LoadPatternRecord(
                            "CG1", npat, f"{1.5 + npat}", f"{2.5 + npat}",
                            f"{3.5 + npat}", f"{0.25 + npat}",
                            f"{0.35 + npat}", f"{0.45 + npat}",
                        )
                        for npat in range(4)
                    )
                ],
            )
            window._on_load_pattern_import_finished(
                LoadPatternCsvResult(patterns, "utf-8-sig", 4, 4, 0, (), 0)
            )
        self.app.processEvents()

    def _result(self, window: MainWindow, *, ampacity: float | None = 340.0):
        """Resultado plausível para os modelos carregados por _load_everything."""

        return PowerFlowResult(
            catalog=window._circuit_catalog,
            cables=window._cable_model,
            phase_configuration=window._phase_configuration,
            loads=window._load_model,
            patterns=window._load_pattern_model,
            step_count=4,
            segment_currents={
                0: SegmentCurrents(
                    nodes=(1, 2, 3),
                    magnitudes=(
                        (10.0, 20.0, 30.0),
                        (11.0, 21.0, 31.0),
                        (12.0, 22.0, 32.0),
                        (13.0, 23.0, 33.0),
                    ),
                    ampacity=ampacity,
                )
            },
            bar_voltages={
                0: BarVoltages(
                    nodes=(1, 2),
                    magnitudes=(
                        (7_960.0, 7_950.0),
                        (7_961.0, 7_951.0),
                        (7_962.0, 7_952.0),
                        (7_963.0, 7_953.0),
                    ),
                    per_unit=(
                        (0.999, 0.998),
                        (0.997, 0.996),
                        (0.995, 0.994),
                        (0.993, 0.992),
                    ),
                )
            },
            solved_circuits=("C1",),
        )

    def _install_result(self, window: MainWindow, result) -> None:  # noqa: ANN001
        """Entrega o resultado pelo mesmo caminho do worker."""

        window._power_flow_snapshot = (
            window._circuit_catalog,
            window._cable_model,
            window._phase_configuration,
            result.loads,
            result.patterns,
        )
        window._on_power_flow_finished(result)
        self.app.processEvents()

    # ------------------------------------------------------------------ ação

    def test_action_lives_in_the_tools_menu_and_in_the_toolbar(self) -> None:
        window = self._window()

        tools_menu = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Ferramentas"
        )
        self.assertIn(window.power_flow_action, tools_menu.actions())
        toolbar_actions = [
            action
            for bar in window.findChildren(QToolBar)
            for action in bar.actions()
        ]
        self.assertIn(window.power_flow_action, toolbar_actions)

    def test_action_requires_every_source(self) -> None:
        window = self._window()

        self.assertFalse(window.power_flow_action.isEnabled())

        self._load_everything(window)
        self.assertTrue(window.power_flow_action.isEnabled())

        # Sem catálogo de cabos não há R/X/QCAP para montar as Line.
        window._set_cable_model(None)
        self.assertFalse(window.power_flow_action.isEnabled())

    def test_action_needs_at_least_one_visible_circuit(self) -> None:
        window = self._window()
        self._load_everything(window)

        window._circuit_visibility.set_visible(0, False)
        window._apply_circuit_visibility()
        self.assertFalse(window.power_flow_action.isEnabled())
        self.assertEqual(window._visible_circuit_indices(), ())

        window._circuit_visibility.set_visible(0, True)
        window._apply_circuit_visibility()
        self.assertTrue(window.power_flow_action.isEnabled())
        self.assertEqual(window._visible_circuit_indices(), (0,))

    def test_action_stays_disabled_without_the_library(self) -> None:
        with patch(
            "circuit_viewer.main_window.power_flow_import_error",
            return_value="py-dss-interface não está instalada",
        ):
            window = self._window()
            self._load_everything(window)

            self.assertFalse(window.power_flow_action.isEnabled())
            self.assertIn("py-dss-interface", window.power_flow_action.toolTip())

    # ----------------------------------------------------------------- painel

    def test_segment_panel_shows_the_currents(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))

        window._set_selection(FeatureSelection("segment", 0))

        self.assertTrue(window.segment_power_flow_section.isVisible())
        model = window.segment_power_flow_model
        self.assertEqual(model.rowCount(), 4)
        # Uma coluna de NPAT mais uma por fase.
        self.assertEqual(model.columnCount(), 4)
        self.assertEqual(model.nodes, (1, 2, 3))
        self.assertEqual(model.rows[0], (10.0, 20.0, 30.0))

    def test_segment_combobox_switches_to_loading(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("segment", 0))

        combo = window.segment_power_flow_combo
        combo.setCurrentIndex(
            next(
                row
                for row in range(combo.count())
                if combo.itemData(row) == "loading"
            )
        )

        rows = window.segment_power_flow_model.rows
        # 10 A sobre 340 A de IADM.
        self.assertAlmostEqual(rows[0][0], 10.0 / 340.0 * 100.0)
        self.assertTrue(window.segment_power_flow_note.isVisible())

    def test_loading_without_ampacity_is_disabled_and_dashed(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window, ampacity=None))
        window._set_selection(FeatureSelection("segment", 0))

        combo = window.segment_power_flow_combo
        row = next(
            index
            for index in range(combo.count())
            if combo.itemData(index) == "loading"
        )
        self.assertFalse(combo.model().item(row).isEnabled())

        combo.setCurrentIndex(row)
        self.assertEqual(window.segment_power_flow_model.rows[0], (None, None, None))
        self.assertIn("IADM", window.segment_power_flow_note.text())

    def test_segment_without_result_hides_the_section(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))

        # O trecho 1 não recebeu resultado no retrato injetado.
        window._set_selection(FeatureSelection("segment", 1))

        self.assertFalse(window.segment_power_flow_section.isVisible())

    def test_bar_panel_shows_volts_and_switches_to_per_unit(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))

        window._set_selection(FeatureSelection("bar", 0))

        self.assertTrue(window.bar_power_flow_section.isVisible())
        model = window.bar_power_flow_model
        self.assertEqual(model.nodes, (1, 2))
        self.assertEqual(model.rows[0], (7_960.0, 7_950.0))

        combo = window.bar_power_flow_combo
        combo.setCurrentIndex(
            next(
                row
                for row in range(combo.count())
                if combo.itemData(row) == "per_unit"
            )
        )
        self.assertEqual(window.bar_power_flow_model.rows[0], (0.999, 0.998))

    def test_selecting_another_kind_hides_both_sections(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("segment", 0))

        window._set_selection(FeatureSelection("bar", 1))

        self.assertFalse(window.segment_power_flow_section.isVisible())
        # A barra 1 também não tem resultado.
        self.assertFalse(window.bar_power_flow_section.isVisible())

    def test_result_refreshes_the_current_selection(self) -> None:
        window = self._window()
        self._load_everything(window)
        window._set_selection(FeatureSelection("segment", 0))
        self.assertFalse(window.segment_power_flow_section.isVisible())

        self._install_result(window, self._result(window))

        # Sem precisar reclicar no trecho, o painel passa a mostrar o resultado.
        self.assertTrue(window.segment_power_flow_section.isVisible())

    # ----------------------------------------------------- consistência/estado

    def test_stale_result_is_discarded(self) -> None:
        window = self._window()
        self._load_everything(window)
        result = self._result(window)
        window._power_flow_snapshot = (
            window._circuit_catalog,
            window._cable_model,
            window._phase_configuration,
            result.loads,
            result.patterns,
        )

        # Uma reimportação de cabos durante a execução troca o modelo.
        window._on_cable_import_finished(
            CableCsvResult(make_cables(), "utf-8-sig", 1, 1, 0, 0, (), 0)
        )
        window._on_power_flow_finished(result)

        self.assertIsNone(window._power_flow_result)

    def test_reimporting_segments_clears_the_result(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("segment", 0))

        self._load_everything(window)

        self.assertIsNone(window._power_flow_result)
        self.assertFalse(window.segment_power_flow_section.isVisible())
        self.assertFalse(window.bar_power_flow_section.isVisible())
        self.assertEqual(window.segment_power_flow_model.rowCount(), 0)

    def test_report_goes_to_the_status_bar_without_warnings(self) -> None:
        window = self._window()
        self._load_everything(window)

        self._install_result(window, self._result(window))

        message = window.statusBar().currentMessage()
        self.assertIn("1 circuito(s) resolvido(s)", message)

    def test_report_opens_a_dialog_with_warnings(self) -> None:
        window = self._window()
        self._load_everything(window)
        result = self._result(window)
        warned = PowerFlowResult(
            catalog=result.catalog,
            cables=result.cables,
            phase_configuration=result.phase_configuration,
            loads=result.loads,
            patterns=result.patterns,
            step_count=result.step_count,
            segment_currents=result.segment_currents,
            bar_voltages=result.bar_voltages,
            solved_circuits=result.solved_circuits,
            unconverged=(("C1", 2),),
            issues=(PowerFlowIssue("T1", "trecho sem cabo"),),
        )

        with patch(
            "circuit_viewer.main_window.QMessageBox.exec",
            return_value=0,
        ) as message_box:
            self._install_result(window, warned)

        self.assertTrue(message_box.called)
        self.assertIsNotNone(window._power_flow_result)

    def test_running_blocks_the_other_heavy_actions(self) -> None:
        window = self._window()
        self._load_everything(window)

        # Simula a execução em curso sem criar thread de verdade.
        window._power_flow_thread = object()
        window._sync_power_flow_availability()
        window._sync_branches_availability()

        self.assertFalse(window.power_flow_action.isEnabled())
        self.assertFalse(window.branches_action.isEnabled())

        window._power_flow_thread = None
        window._sync_power_flow_availability()
        window._sync_branches_availability()
        self.assertTrue(window.power_flow_action.isEnabled())
        self.assertTrue(window.branches_action.isEnabled())

    def test_run_without_loads_asks_before_proceeding(self) -> None:
        window = self._window()
        self._load_everything(window, with_loads=False)

        with patch(
            "circuit_viewer.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as question:
            window._run_power_flow()

        self.assertTrue(question.called)
        # Cancelar não inicia thread alguma.
        self.assertIsNone(window._power_flow_thread)

    def test_run_without_a_visible_circuit_only_warns(self) -> None:
        window = self._window()
        self._load_everything(window)
        window._circuit_visibility.set_visible(0, False)
        window._apply_circuit_visibility()

        window._run_power_flow()

        self.assertIsNone(window._power_flow_thread)
        self.assertIn("Marque ao menos um circuito", window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
