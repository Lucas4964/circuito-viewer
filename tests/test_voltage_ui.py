from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.graphics import POINT_COLOR
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import (
        CircuitCatalogModel,
        CircuitDefinition,
        CircuitModel,
        LineNetworkModel,
        SwitchModel,
        UtmCrs,
    )
    from circuit_viewer.opendss_powerflow import BarVoltages, PowerFlowResult
    from circuit_viewer.phase_config import load_phase_configuration
    from circuit_viewer.segment_import import SegmentLoadResult
    from circuit_viewer.voltage_bands import VoltageQuantity
    from circuit_viewer.voltage_bands_dialog import VoltageBandsDialog

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


def bar_voltages(per_unit: tuple[float, ...], nodes: tuple[int, ...]) -> "BarVoltages":
    angles = {1: 0.0, 2: -120.0, 3: 120.0}
    return BarVoltages(
        nodes=nodes,
        magnitudes=((7_967.0,) * len(nodes),),
        per_unit=(per_unit,),
        angles=(tuple(angles[node] for node in nodes),),
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class VoltageVisualizationUiTests(unittest.TestCase):
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
                    {"FASES2": "13", "NUMERO_FASES": 3},
                ]
            ),
            encoding="utf-8",
        )

    def make_window(self):
        bars = CircuitModel(
            [f"B{index}" for index in range(4)],
            [""] * 4,
            [500_000.0 + index * 100.0 for index in range(4)],
            [8_000_000.0] * 4,
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
            bars,
            [f"T{index}" for index in range(3)],
            [""] * 3,
            ["13", "13", "1"],
            [0, 1, 2],
            [1, 2, 3],
            [""] * 3,
            [""] * 3,
            [""] * 3,
            [100.0] * 3,
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
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 4, 4, 0, (), 0))
        window._on_segment_import_finished(
            SegmentLoadResult(segments, "utf-8-sig", 3, 3, 0, (), 0)
        )
        window._set_switch_model(switches)
        catalog = CircuitCatalogModel.build(
            segments,
            switches,
            [CircuitDefinition("C0", "B0", "", "")],
        )
        window._set_circuit_catalog(catalog, colors=("#7A2E8E",))
        self.app.processEvents()
        return window, catalog

    def give_result(self, window, catalog, voltages) -> None:
        """Entrega um resultado de fluxo pelo mesmo caminho da execução real."""

        result = PowerFlowResult(
            catalog=catalog,
            cables=None,
            phase_configuration=load_phase_configuration(self.config_path),
            loads=None,
            patterns=None,
            step_count=1,
            bar_voltages=voltages,
        )
        window._power_flow_result = result
        window._refresh_voltage_classification()
        window._apply_circuit_visibility()
        self.app.processEvents()

    def default_voltages(self):
        # B0 adequada, B1 crítica por subtensão, B2 crítica por sobretensão,
        # B3 monofásica — a barra do pedido.
        return {
            0: bar_voltages((1.00, 1.00, 1.00), (1, 2, 3)),
            1: bar_voltages((0.85, 0.86, 0.87), (1, 2, 3)),
            2: bar_voltages((1.12, 1.12, 1.12), (1, 2, 3)),
            3: bar_voltages((0.99,), (3,)),
        }

    # -- o modo -----------------------------------------------------------

    def test_the_action_lives_in_the_view_menu_and_starts_disabled(self) -> None:
        window = MainWindow(self.config_path)
        self.addCleanup(window.close)
        self.assertFalse(window.voltage_coloring_action.isEnabled())
        view_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.text() == "Visualizar"
        )
        self.assertIn(window.voltage_coloring_action, view_menu.actions())

    def test_the_action_only_turns_on_with_a_power_flow_result(self) -> None:
        window, catalog = self.make_window()
        self.assertFalse(window.voltage_coloring_action.isEnabled())
        window.voltage_coloring_action.setChecked(True)
        self.assertFalse(window.voltage_coloring_action.isChecked())

        self.give_result(window, catalog, self.default_voltages())
        self.assertTrue(window.voltage_coloring_action.isEnabled())

    def test_turning_the_mode_on_colors_the_bars(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)
        self.app.processEvents()

        overview = window.virtualizer.overview_item
        self.assertEqual(overview._colors, window._voltage_classification.colors)
        # Adequada, crítica baixa, crítica alta: três categorias distintas.
        self.assertEqual(overview.category_point_count, 3)
        self.assertTrue(window.voltage_legend.isVisible())

    def test_undervoltage_and_overvoltage_get_different_colors(self) -> None:
        """O ponto do pedido: o mapa tem de dizer se é alta ou baixa."""

        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)

        classification = window._voltage_classification
        low = classification.colors[int(classification.style_indices[1])]
        high = classification.colors[int(classification.style_indices[2])]
        self.assertNotEqual(low, high)
        self.assertIn("subtensão", classification.describe(1))
        self.assertIn("sobretensão", classification.describe(2))

    def test_a_single_phase_bar_is_black_in_line_mode(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)

        window.voltage_legend.quantity_combo.setCurrentIndex(
            window.voltage_legend.quantity_combo.findData(VoltageQuantity.LINE)
        )
        self.app.processEvents()

        classification = window._voltage_classification
        self.assertIs(classification.quantity, VoltageQuantity.LINE)
        self.assertEqual(
            classification.colors[int(classification.style_indices[3])],
            "#000000",
        )
        self.assertEqual(classification.describe(3), "Sem tensão de linha")
        # As trifásicas continuam na escala.
        self.assertNotEqual(
            classification.colors[int(classification.style_indices[0])],
            "#000000",
        )

    def test_the_same_bar_is_classified_in_phase_mode(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)

        classification = window._voltage_classification
        self.assertIs(classification.quantity, VoltageQuantity.PHASE)
        self.assertIn("Adequada", classification.describe(3))

    def test_the_step_selector_recomputes_the_classification(self) -> None:
        window, catalog = self.make_window()
        two_steps = {
            0: BarVoltages(
                nodes=(1, 2, 3),
                magnitudes=((7_967.0,) * 3,) * 2,
                per_unit=((1.00,) * 3, (0.85,) * 3),
                angles=((0.0, -120.0, 120.0),) * 2,
            )
        }
        self.give_result(window, catalog, two_steps)
        window.voltage_coloring_action.setChecked(True)
        self.assertIn("Adequada", window._voltage_classification.describe(0))

        window.voltage_legend.step_combo.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(window._voltage_step, 1)
        self.assertIn("Crítica", window._voltage_classification.describe(0))

    def test_discarding_the_power_flow_turns_the_mode_off(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)
        self.assertTrue(window.voltage_coloring_action.isChecked())

        window._invalidate_power_flow()
        self.app.processEvents()

        self.assertFalse(window.voltage_coloring_action.isChecked())
        self.assertFalse(window.voltage_coloring_action.isEnabled())
        self.assertIsNone(window._voltage_classification)
        self.assertFalse(window.voltage_legend.isVisible())
        overview = window.virtualizer.overview_item
        self.assertEqual(overview.category_point_count, 1)

    def test_turning_the_mode_off_restores_the_default_color(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)
        window.voltage_coloring_action.setChecked(False)
        self.app.processEvents()

        overview = window.virtualizer.overview_item
        self.assertEqual(overview.category_point_count, 1)
        self.assertEqual(overview._colors, ())
        self.assertFalse(window.voltage_legend.isVisible())

    def test_the_mode_does_not_disturb_the_phase_coloring_of_segments(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.phase_coloring_action.setChecked(True)
        window.voltage_coloring_action.setChecked(True)
        self.app.processEvents()

        # Um colore trechos, o outro barras: ligados juntos, os dois valem.
        self.assertTrue(window.phase_legend.isVisible())
        self.assertTrue(window.voltage_legend.isVisible())
        self.assertNotEqual(window._line_item.category_path_count, 0)
        self.assertNotEqual(
            window.virtualizer.overview_item.category_point_count, 1
        )

    def test_the_two_legends_do_not_overlap(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.phase_coloring_action.setChecked(True)
        window.voltage_coloring_action.setChecked(True)
        self.app.processEvents()

        voltage = window.voltage_legend
        phase = window.phase_legend
        self.assertLessEqual(phase.y() + phase.height(), voltage.y())

    def test_the_legend_counts_the_bars_of_each_category(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)
        self.app.processEvents()

        captions = [
            caption.text()
            for row, _, caption in window.voltage_legend._rows
            if row.isVisible()
        ]
        self.assertIn("Adequada (2)", captions)
        self.assertEqual(len(captions), 3)

    def test_the_wheel_over_the_step_selector_does_not_change_it(self) -> None:
        """A roda sobre o painel é zoom do mapa, nunca troca de patamar."""

        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QWheelEvent

        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)
        legend = window.voltage_legend
        combo = legend.step_combo
        before = combo.currentIndex()
        event = QWheelEvent(
            QPointF(5.0, 5.0),
            QPointF(5.0, 5.0),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        self.assertTrue(legend.eventFilter(combo, event))
        self.assertEqual(combo.currentIndex(), before)
        self.assertEqual(combo.focusPolicy(), Qt.FocusPolicy.NoFocus)

    # -- diálogo das faixas ------------------------------------------------

    def test_the_dialog_is_in_the_settings_menu(self) -> None:
        window = MainWindow(self.config_path)
        self.addCleanup(window.close)
        self.assertIn(
            window.voltage_bands_action,
            window.settings_menu.actions(),
        )

    def test_editing_a_band_color_repaints_the_canvas(self) -> None:
        window, catalog = self.make_window()
        self.give_result(window, catalog, self.default_voltages())
        window.voltage_coloring_action.setChecked(True)
        before = window._voltage_classification.colors

        dialog = VoltageBandsDialog(
            window._voltage_band_table,
            storage_path=self.root / "faixas.json",
            parent=window,
        )
        self.addCleanup(dialog.close)
        dialog.table.cellWidget(0, 1).set_color("#00FFAA")
        table = dialog.current_table()
        self.assertIsNotNone(table)
        window._on_voltage_bands_saved(table)
        self.app.processEvents()

        after = window._voltage_classification.colors
        self.assertNotEqual(before, after)
        self.assertEqual(after[0], "#00FFAA")
        self.assertEqual(window.virtualizer.overview_item._colors, after)

    def test_the_dialog_refuses_a_table_with_a_gap(self) -> None:
        window, _ = self.make_window()
        dialog = VoltageBandsDialog(
            window._voltage_band_table,
            storage_path=self.root / "faixas.json",
            parent=window,
        )
        self.addCleanup(dialog.close)
        # Puxa o mínimo da crítica baixa para cima e abre um buraco em 0 pu.
        row = next(
            index
            for index in range(dialog.table.rowCount())
            if dialog.table.item(index, 0).text().startswith("Crítica (sub")
        )
        dialog.table.cellWidget(row, 2).setValue(0.5)
        from unittest.mock import patch

        with patch(
            "circuit_viewer.voltage_bands_dialog.QMessageBox.warning"
        ) as warning:
            self.assertIsNone(dialog.current_table())
        self.assertIn("0", warning.call_args.args[2])

    def test_the_dialog_writes_infinity_as_the_top_of_the_spin(self) -> None:
        window, _ = self.make_window()
        dialog = VoltageBandsDialog(
            window._voltage_band_table,
            storage_path=self.root / "faixas.json",
            parent=window,
        )
        self.addCleanup(dialog.close)
        table = dialog.current_table()
        self.assertIsNotNone(table)
        self.assertTrue(any(math.isinf(band.maximum_pu) for band in table.bands))
        self.assertEqual(table, window._voltage_band_table)

    def test_the_default_bar_color_is_unchanged_without_the_mode(self) -> None:
        window, _ = self.make_window()
        overview = window.virtualizer.overview_item
        self.assertEqual(overview.category_point_count, 1)
        self.assertEqual(QColor(POINT_COLOR).name(), "#202020")


if __name__ == "__main__":
    unittest.main()
