"""Testes de interface do fluxo de potência.

Nenhum destes testes toca a DLL do OpenDSS: o resultado é montado à mão e
injetado, porque o que se verifica aqui é a integração — disponibilidade do
botão, exclusão mútua de threads, invalidação em cascata e o painel com
combobox.
"""

from __future__ import annotations

import math
import os
import unittest
from dataclasses import replace
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
        SegmentPowers,
    )
    from circuit_viewer.opendss_line_mode import OpenDssLineParameterMode
    from circuit_viewer.phase_config import (
        PhaseConfiguration,
        PhaseMappingEntry,
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
                    angles=(
                        (0.0, -120.0, 120.0),
                        (1.0, -119.0, 121.0),
                        (2.0, -118.0, 122.0),
                        (3.0, -117.0, 123.0),
                    ),
                )
            },
            segment_powers={
                0: SegmentPowers(
                    nodes=(1, 2, 3),
                    active=tuple(
                        (100.0 + step, 110.0 + step, 120.0 + step)
                        for step in range(4)
                    ),
                    reactive=tuple(
                        (30.0 + step, 33.0 + step, 36.0 + step)
                        for step in range(4)
                    ),
                    active_losses=(2.0, 2.1, 2.2, 2.3),
                    reactive_losses=(6.0, 6.1, 6.2, 6.3),
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
                    angles=(
                        (0.0, -120.0),
                        (1.0, -119.0),
                        (2.0, -118.0),
                        (3.0, -117.0),
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

    def test_library_mode_allows_power_flow_without_legacy_cables(self) -> None:
        window = self._window()
        self._load_everything(window)
        window._opendss_line_parameter_mode = OpenDssLineParameterMode.LIBRARY

        with patch(
            "circuit_viewer.main_window.power_flow_import_error",
            return_value=None,
        ):
            window._set_cable_model(None)
            window._sync_power_flow_availability()

        self.assertTrue(window.power_flow_action.isEnabled())

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
        # NPAT + três módulos + três ângulos.
        self.assertEqual(model.columnCount(), 7)
        self.assertEqual(
            model.labels,
            ("Fase D", "Fase E", "Fase F", "θD", "θE", "θF"),
        )
        self.assertEqual(model.rows[0], (10.0, 20.0, 30.0, 0.0, -120.0, 120.0))
        # Precisão fixa de 4 casas, inclusive na coluna de ângulo — não é mais
        # a 1 casa de antes desta mudança.
        self.assertEqual(model.data(model.index(0, 1)), "10.0000")
        self.assertEqual(model.data(model.index(0, 4)), "0.0000")
        self.assertEqual(model.data(model.index(0, 5)), "-120.0000")

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

        model = window.segment_power_flow_model
        rows = model.rows
        # 10 A sobre 340 A de IADM.
        self.assertAlmostEqual(rows[0][0], 10.0 / 340.0 * 100.0)
        self.assertTrue(window.segment_power_flow_note.isVisible())
        # Percentual também em 4 casas fixas, não mais 1.
        self.assertEqual(model.data(model.index(0, 1)), "2.9412")

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
        self.assertEqual(model.labels, ("Fase D", "Fase E", "θD", "θE"))
        self.assertEqual(model.rows[0], (7_960.0, 7_950.0, 0.0, -120.0))
        # Precisão fixa de 4 casas, com separador de milhar em espaço.
        self.assertEqual(model.data(model.index(0, 1)), "7 960.0000")

        combo = window.bar_power_flow_combo
        combo.setCurrentIndex(
            next(
                row
                for row in range(combo.count())
                if combo.itemData(row) == "per_unit"
            )
        )
        # O pu não traz ângulo: seria o mesmo da tensão de fase.
        per_unit_model = window.bar_power_flow_model
        self.assertEqual(per_unit_model.labels, ("Fase D", "Fase E"))
        self.assertEqual(per_unit_model.rows[0], (0.999, 0.998))
        self.assertEqual(per_unit_model.data(per_unit_model.index(0, 1)), "0.9990")

    def _select_quantity(self, combo, key: str) -> None:  # noqa: ANN001
        combo.setCurrentIndex(
            next(
                row
                for row in range(combo.count())
                if combo.itemData(row) == key
            )
        )

    def test_quantities_use_the_project_phase_names(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))

        captions = [
            window.bar_power_flow_combo.itemText(row)
            for row in range(window.bar_power_flow_combo.count())
        ]

        self.assertEqual(
            captions,
            [
                "Tensão de fase (V)",
                "Tensão de linha (V)",
                "Tensão de fase (pu)",
                "Tensão de linha (pu)",
                "Desequilíbrio de tensão (%)",
            ],
        )

    def test_phase_letters_come_from_the_configuration(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        # Configuração invertida: o nó 1 passa a ser a fase E e o nó 2, a D.
        # Se os rótulos fossem literais, nada mudaria aqui.
        window._phase_configuration = PhaseConfiguration(
            (
                PhaseMappingEntry("1", "E", 1, "1"),
                PhaseMappingEntry("2", "D", 1, "2"),
                PhaseMappingEntry("13", "DEF", 3, "1.2.3"),
            )
        )

        window._set_selection(FeatureSelection("bar", 0))

        self.assertEqual(
            window.bar_power_flow_model.labels,
            ("Fase E", "Fase D", "θE", "θD"),
        )

    def test_unmapped_node_falls_back_to_its_number(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._phase_configuration = None

        window._set_selection(FeatureSelection("bar", 0))

        self.assertEqual(
            window.bar_power_flow_model.labels,
            ("Fase 1", "Fase 2", "θ1", "θ2"),
        )

    def test_bar_panel_shows_the_line_voltage(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("bar", 0))

        self._select_quantity(window.bar_power_flow_combo, "line_voltage")

        model = window.bar_power_flow_model
        # A barra da fixture tem só as fases D e E, então sai um par só.
        self.assertEqual(model.labels, ("VDE", "θDE"))
        # 7960 ∠0° menos 7950 ∠-120° = 13778,47 ∠29,98°. A diferença de
        # módulos daria 10 V: é o que separa subtrair fasor de subtrair módulo.
        self.assertAlmostEqual(model.rows[0][0], 13_778.465081, places=4)
        self.assertAlmostEqual(model.rows[0][1], 29.979208, places=4)

    def test_line_voltage_is_disabled_on_a_single_phase_bar(self) -> None:
        window = self._window()
        self._load_everything(window)
        result = self._result(window)
        single = BarVoltages(
            nodes=(1,),
            magnitudes=((7_960.0,),) * 4,
            per_unit=((0.999,),) * 4,
            angles=((0.0,),) * 4,
        )
        self._install_result(
            window,
            replace(result, bar_voltages={0: single}),
        )

        window._set_selection(FeatureSelection("bar", 0))

        combo = window.bar_power_flow_combo
        row = next(
            index
            for index in range(combo.count())
            if combo.itemData(index) == "line_voltage"
        )
        self.assertFalse(combo.model().item(row).isEnabled())

        self._select_quantity(combo, "line_voltage")
        self.assertEqual(window.bar_power_flow_model.labels, ())
        self.assertIn("uma fase só", window.bar_power_flow_note.text())

    def test_segment_loading_has_no_angle_columns(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("segment", 0))

        self._select_quantity(window.segment_power_flow_combo, "loading")

        # Carregamento é razão de módulos: não há fasor a mostrar.
        self.assertEqual(
            window.segment_power_flow_model.labels,
            ("Fase D", "Fase E", "Fase F"),
        )

    def test_bar_shows_line_voltage_in_per_unit(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("bar", 0))

        self._select_quantity(window.bar_power_flow_combo, "line_per_unit")

        model = window.bar_power_flow_model
        self.assertEqual(model.labels, ("VDE",))
        # A pu de fase da fixture é ~0,999/0,998 defasadas de 120°: a de linha
        # renormaliza por √3 e fica na mesma ordem de grandeza, não em √3.
        self.assertAlmostEqual(model.rows[0][0], 0.99850, places=4)

    def test_bar_shows_the_voltage_unbalance(self) -> None:
        window = self._window()
        self._load_everything(window)
        result = self._result(window)
        balanced = BarVoltages(
            nodes=(1, 2, 3),
            magnitudes=((7_960.0, 7_960.0, 7_960.0),) * 4,
            per_unit=((1.0, 1.0, 1.0),) * 4,
            angles=((0.0, -120.0, 120.0),) * 4,
        )
        self._install_result(window, replace(result, bar_voltages={0: balanced}))
        window._set_selection(FeatureSelection("bar", 0))

        self._select_quantity(window.bar_power_flow_combo, "unbalance")

        self.assertEqual(window.bar_power_flow_model.labels, ("FD (%)",))
        self.assertAlmostEqual(window.bar_power_flow_model.rows[0][0], 0.0)

    def test_unbalance_is_disabled_without_three_phases(self) -> None:
        window = self._window()
        self._load_everything(window)
        # A fixture padrão tem uma barra de dois nós.
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("bar", 0))

        combo = window.bar_power_flow_combo
        row = next(
            index
            for index in range(combo.count())
            if combo.itemData(index) == "unbalance"
        )
        self.assertFalse(combo.model().item(row).isEnabled())

    def test_segment_shows_active_and_reactive_power(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("segment", 0))

        self._select_quantity(window.segment_power_flow_combo, "active_power")
        model = window.segment_power_flow_model
        self.assertEqual(model.labels, ("Fase D", "Fase E", "Fase F"))
        self.assertEqual(model.rows[0], (100.0, 110.0, 120.0))

        self._select_quantity(window.segment_power_flow_combo, "reactive_power")
        self.assertEqual(
            window.segment_power_flow_model.rows[0], (30.0, 33.0, 36.0)
        )

    def test_segment_shows_apparent_power_with_its_angle(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("segment", 0))

        self._select_quantity(window.segment_power_flow_combo, "apparent_power")

        model = window.segment_power_flow_model
        self.assertEqual(
            model.labels, ("Fase D", "Fase E", "Fase F", "θD", "θE", "θF")
        )
        self.assertAlmostEqual(model.rows[0][0], math.hypot(100.0, 30.0))
        self.assertAlmostEqual(
            model.rows[0][3], math.degrees(math.atan2(30.0, 100.0))
        )

    def test_segment_shows_the_three_phase_totals(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("segment", 0))

        self._select_quantity(
            window.segment_power_flow_combo, "three_phase_power"
        )

        model = window.segment_power_flow_model
        self.assertEqual(model.labels, ("P (kW)", "Q (kvar)", "S (kVA)", "θS"))
        active, reactive, apparent, _ = model.rows[0]
        self.assertAlmostEqual(active, 330.0)
        self.assertAlmostEqual(reactive, 99.0)
        self.assertAlmostEqual(apparent, math.hypot(330.0, 99.0))

    def test_segment_shows_power_factor_and_losses(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._install_result(window, self._result(window))
        window._set_selection(FeatureSelection("segment", 0))

        self._select_quantity(window.segment_power_flow_combo, "power_factor")
        model = window.segment_power_flow_model
        self.assertEqual(model.labels, ("Fase D", "Fase E", "Fase F", "3φ"))
        self.assertAlmostEqual(model.rows[0][0], 100.0 / math.hypot(100.0, 30.0))

        self._select_quantity(window.segment_power_flow_combo, "losses")
        model = window.segment_power_flow_model
        self.assertEqual(model.labels, ("ΔP (kW)", "ΔQ (kvar)"))
        self.assertEqual(model.rows[0], (2.0, 6.0))

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

    def test_power_flow_worker_receives_saved_library_snapshots(self) -> None:
        window = self._window()
        self._load_everything(window)
        window._opendss_line_parameter_mode = OpenDssLineParameterMode.LIBRARY
        window._set_cable_model(None)
        expected_catalog = window.opendss_library_session.saved_catalog()
        draft_cables = window.opendss_library_session.catalog.cables
        if draft_cables:
            draft_cables[0].name = "RASCUNHO NÃO SALVO"
        created = []

        class SignalStub:
            def connect(self, _callback) -> None:  # noqa: ANN001
                pass

        class RecordingWorker:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN003
                self.args = args
                self.kwargs = kwargs
                self.progress = SignalStub()
                self.finished = SignalStub()
                self.failed = SignalStub()
                self.cancelled = SignalStub()
                created.append(self)

            def moveToThread(self, _thread) -> None:  # noqa: N802, ANN001
                pass

            def run(self) -> None:
                pass

            def cancel(self) -> None:
                pass

            def deleteLater(self) -> None:  # noqa: N802
                pass

        with patch(
            "circuit_viewer.main_window.PowerFlowWorker",
            RecordingWorker,
        ), patch("circuit_viewer.main_window.QThread.start"):
            window._run_power_flow()

        worker = created[0]
        self.assertIsNone(worker.args[1])
        self.assertIs(
            worker.kwargs["line_parameter_mode"],
            OpenDssLineParameterMode.LIBRARY,
        )
        snapshot = worker.kwargs["library_catalog"]
        self.assertIsNot(snapshot, window.opendss_library_session.catalog)
        self.assertEqual(
            [(item.cable_id, item.name) for item in snapshot.cables],
            [(item.cable_id, item.name) for item in expected_catalog.cables],
        )
        self.assertEqual(
            worker.kwargs["library_mappings"],
            window.opendss_mapping_session.mappings,
        )
        self.assertIs(
            window._power_flow_snapshot[-1],
            OpenDssLineParameterMode.LIBRARY,
        )
        thread = window._power_flow_thread
        window._on_power_flow_thread_finished()
        if thread is not None:
            thread.deleteLater()
        self.app.processEvents()

    def test_saved_library_signals_invalidate_only_library_power_flow(self) -> None:
        window = self._window()

        class RunningWorker:
            def __init__(self) -> None:
                self.cancel_count = 0

            def cancel(self) -> None:
                self.cancel_count += 1

        worker = RunningWorker()
        window._power_flow_worker = worker
        marker = object()
        window._power_flow_result = marker
        window._power_flow_snapshot = (marker,)
        signals = (
            (window.opendss_library_session.cablesSaved, (1,)),
            (window.opendss_library_session.geometriesSaved, (1, 0)),
            (window.opendss_mapping_session.mapsSaved, (1, 1)),
        )

        for signal, args in signals:
            signal.emit(*args)
        self.assertIs(window._power_flow_result, marker)
        self.assertEqual(worker.cancel_count, 0)

        window._opendss_line_parameter_mode = OpenDssLineParameterMode.LIBRARY
        for signal, args in signals:
            window._power_flow_result = marker
            window._power_flow_snapshot = (marker,)
            before = worker.cancel_count
            signal.emit(*args)
            self.assertIsNone(window._power_flow_result)
            self.assertIsNone(window._power_flow_snapshot)
            self.assertEqual(worker.cancel_count, before + 1)
        window._power_flow_worker = None

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

    def test_run_without_updated_generators_asks_before_proceeding(self) -> None:
        window = self._window()
        self._load_everything(window)
        window._generator_model = object()

        with patch(
            "circuit_viewer.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as question:
            window._run_power_flow()

        self.assertIn("Atualizar Geradores", question.call_args.args[2])
        self.assertIsNone(window._power_flow_thread)


if __name__ == "__main__":
    unittest.main()
