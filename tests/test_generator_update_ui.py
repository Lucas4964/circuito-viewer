from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

try:
    from PyQt6.QtCore import QModelIndex, Qt
    from PyQt6.QtWidgets import QApplication, QDialog

    from circuit_viewer.generator_update_dialog import UpdateGeneratorsDialog
    from circuit_viewer.generator_update_table import (
        GeneratorDemandTableModel,
        GeneratorPhasePowerTableModel,
    )
    from circuit_viewer.main_window import MainWindow

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False

from circuit_viewer.calculation_levels import default_calculation_levels
from circuit_viewer.circuit_calculation_levels import (
    CircuitCalculationLevelsController,
    CircuitCalculationLevelsModel,
)
from circuit_viewer.curvas import Curve
from circuit_viewer.generator_update import (
    GeneratorDemandRecord,
    GeneratorPhasePowerRecord,
    GeneratorScheduleMode,
    calculate_generator_demands,
)
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitMembership,
    CircuitModel,
    FeatureSelection,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    UtmCrs,
)
from circuit_viewer.phase_config import load_phase_configuration


def readonly(values: list[int]) -> np.ndarray:
    array = np.asarray(values, dtype=np.intp)
    array.setflags(write=False)
    return array


def make_models(
    *, energy: str = "720", phases: str = "13"
) -> tuple[GeneratorModel, CircuitCatalogModel]:
    bars = CircuitModel(
        ["B0", "B1", "B2", "B3"],
        ["CB0", "CB1", "CB2", "CB3"],
        [500_000.0, 500_010.0, 500_020.0, 500_030.0],
        [8_000_000.0] * 4,
        UtmCrs(21, False),
    )
    lines = LineNetworkModel(
        bars,
        ["T0", "T1", "T2"],
        ["TR0", "TR1", "TR2"],
        ["13", "13", "13"],
        [0, 1, 2],
        [1, 2, 3],
        ["", "", ""],
        ["", "", ""],
        ["", "", ""],
        [10.0, 10.0, 10.0],
    )
    empty = readonly([])
    memberships = (
        CircuitMembership(readonly([0, 1]), readonly([0]), empty, readonly([0])),
        CircuitMembership(readonly([2, 3]), readonly([2]), empty, readonly([2])),
    )
    circuits = CircuitCatalogModel(
        lines,
        None,
        (
            CircuitDefinition("C0", "B0", "COD-0", "13.8"),
            CircuitDefinition("C1", "B2", "COD-1", "13.8"),
        ),
        memberships,
    )
    loads = LoadModel(
        bars,
        ["L0"],
        [0],
        [""],
        ["LOAD-0"],
        [""],
        [""],
        [""],
        [phases],
        [""],
    )
    generators = GeneratorModel(
        loads,
        ["G0"],
        [0],
        ["MC0"],
        ["GEN-0"],
        ["13.8"],
        ["75"],
        ["Y"],
        ["CURVA-ORIGINAL"],
        [energy],
        ["CONS-0"],
        ["GEN-0"],
        [""],
        ["Gerador 0"],
        [phases],
    )
    return generators, circuits


def saved_curve(*, first: float = 1.0) -> Curve:
    values = [1.0] * 24
    values[0] = first
    return Curve("CURVA", "Residencial", tuple(values))


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class UpdateGeneratorsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_lists_saved_curves_and_all_circuits_start_with_default(self) -> None:
        _, circuits = make_models()
        own = default_calculation_levels()
        imported = CircuitCalculationLevelsModel(circuits, (own, None))
        controller = CircuitCalculationLevelsController(imported)
        dialog = UpdateGeneratorsDialog(
            (saved_curve(),),
            circuits,
            default_calculation_levels(),
            controller,
        )
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.curve_combo.count(), 1)
        self.assertEqual(dialog.selected_curve().curve_id, "CURVA")
        self.assertEqual(dialog.circuit_table.rowCount(), 2)
        self.assertEqual(
            [
                dialog.circuit_table.horizontalHeaderItem(column).text()
                for column in range(3)
            ],
            ["CIRC_ID", "CODIGO", "PATAMARES"],
        )
        self.assertEqual(
            dialog.schedule_modes(),
            (GeneratorScheduleMode.DEFAULT, GeneratorScheduleMode.DEFAULT),
        )
        self.assertEqual(dialog._mode_combos[0].count(), 2)
        self.assertEqual(dialog._mode_combos[1].count(), 1)
        self.assertEqual(dialog._mode_combos[0].itemText(1), "Próprios")

        dialog._mode_combos[0].setCurrentIndex(1)
        self.assertEqual(
            dialog.schedule_modes()[0], GeneratorScheduleMode.CIRCUIT
        )
        self.assertIs(dialog.effective_schedules()[0], own)
        self.assertIs(
            dialog.effective_schedules()[1], dialog._default_schedule
        )

    def test_rejects_controller_from_another_catalog(self) -> None:
        _, circuits = make_models()
        _, other = make_models()
        controller = CircuitCalculationLevelsController(
            CircuitCalculationLevelsModel(other, (default_calculation_levels(), None))
        )
        with self.assertRaisesRegex(ValueError, "pertencer"):
            UpdateGeneratorsDialog(
                (saved_curve(),),
                circuits,
                default_calculation_levels(),
                controller,
            )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class GeneratorResultTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_demand_table_has_exact_headers_four_rows_and_is_read_only(self) -> None:
        model = GeneratorDemandTableModel()
        model.set_records(
            tuple(GeneratorDemandRecord("G0", npat, npat + 0.12567) for npat in range(4))
        )

        self.assertEqual((model.rowCount(), model.columnCount()), (4, 2))
        self.assertEqual(
            [
                model.headerData(
                    column,
                    Qt.Orientation.Horizontal,
                    Qt.ItemDataRole.DisplayRole,
                )
                for column in range(2)
            ],
            ["NPAT", "DEMANDA"],
        )
        index = model.index(0, 1)
        self.assertEqual(model.data(index), "0.1257")
        self.assertEqual(model.data(index, Qt.ItemDataRole.ToolTipRole), "0.12567")
        self.assertFalse(model.flags(index) & Qt.ItemFlag.ItemIsEditable)
        self.assertEqual(model.flags(QModelIndex()), Qt.ItemFlag.NoItemFlags)

    def test_phase_table_matches_load_pattern_shape_and_zero_reactive_power(self) -> None:
        model = GeneratorPhasePowerTableModel()
        model.set_records(
            tuple(
                GeneratorPhasePowerRecord("G0", npat, 1.0, 2.0, 3.0)
                for npat in range(4)
            )
        )

        self.assertEqual((model.rowCount(), model.columnCount()), (4, 8))
        self.assertEqual(
            [
                model.headerData(
                    column,
                    Qt.Orientation.Horizontal,
                    Qt.ItemDataRole.DisplayRole,
                )
                for column in range(8)
            ],
            ["GERADOR_ID", "NPAT", "PD", "PE", "PF", "QD", "QE", "QF"],
        )
        self.assertEqual(model.data(model.index(0, 0)), "G0")
        self.assertEqual(model.data(model.index(0, 5)), "0.0000")


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class GeneratorUpdateMainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_window_with_models(self) -> tuple[MainWindow, GeneratorModel, CircuitCatalogModel]:
        generators, circuits = make_models()
        window = MainWindow()
        self.addCleanup(window.close)
        window._model = generators.bars
        window.view.set_model(generators.bars)
        window.virtualizer.reset_model(generators.bars)
        window._line_model = circuits.segments
        window._load_model = generators.loads
        window.view.set_load_model(generators.loads)
        window.load_virtualizer.reset_model(generators.loads)
        window._set_generator_model(generators)
        window._set_circuit_catalog(circuits)
        window._saved_curves = (saved_curve(),)
        window._sync_generator_update_availability()
        return window, generators, circuits

    def test_action_is_in_tools_and_requires_all_inputs(self) -> None:
        empty = MainWindow()
        self.addCleanup(empty.close)
        tools = next(
            menu for menu in empty.menuBar().findChildren(type(empty.tools_menu))
            if menu.title() == "Ferramentas"
        )
        self.assertIn(empty.update_generators_action, tools.actions())
        self.assertFalse(empty.update_generators_action.isEnabled())

        window, _, _ = self.make_window_with_models()
        self.assertTrue(window.update_generators_action.isEnabled())

    def test_panel_shows_both_tables_and_invalidation_clears_them(self) -> None:
        window, generators, circuits = self.make_window_with_models()
        schedule = default_calculation_levels()
        result = calculate_generator_demands(
            generators,
            circuits,
            window._phase_configuration,
            saved_curve(),
            (schedule, schedule),
            (GeneratorScheduleMode.DEFAULT, GeneratorScheduleMode.DEFAULT),
        )
        window._set_generator_update_result(result)
        window._set_selection(FeatureSelection("generator", 0))

        self.assertFalse(window.generator_demand_section.isHidden())
        self.assertFalse(window.generator_phase_power_section.isHidden())
        self.assertEqual(window.generator_demand_table_model.rowCount(), 4)
        self.assertEqual(window.generator_phase_power_table_model.rowCount(), 4)
        self.assertEqual(
            window.generator_demand_table_model.data(
                window.generator_demand_table_model.index(0, 1)
            ),
            "1.0000",
        )
        self.assertEqual(
            window.generator_phase_power_table_model.data(
                window.generator_phase_power_table_model.index(0, 2)
            ),
            "-0.3333",
        )
        self.assertTrue(window.generator_update_note.isHidden())

        window._on_calculation_levels_saved(default_calculation_levels())
        self.assertIsNone(window._generator_update_result)
        self.assertTrue(window.generator_demand_section.isHidden())
        self.assertTrue(window.generator_phase_power_section.isHidden())

    def test_omitted_generator_hides_tables_and_displays_reason(self) -> None:
        valid_generators, circuits = make_models()
        invalid_loads = LoadModel(
            valid_generators.bars,
            ["L0", "L1"],
            [0, 0],
            ["", ""],
            ["LOAD-0", "LOAD-1"],
            ["", ""],
            ["", ""],
            ["", ""],
            ["13", "999"],
            ["", ""],
        )
        generators = GeneratorModel(
            invalid_loads,
            ["G0", "G1"],
            [0, 1],
            ["MC0", "MC1"],
            ["GEN-0", "GEN-1"],
            ["13.8", "13.8"],
            ["75", "75"],
            ["Y", "Y"],
            ["X", "X"],
            ["720", "720"],
            ["C0", "C1"],
            ["GEN-0", "GEN-1"],
            ["", ""],
            ["Gerador 0", "Gerador 1"],
            ["13", "999"],
        )
        window = MainWindow()
        self.addCleanup(window.close)
        window._model = generators.bars
        window.view.set_model(generators.bars)
        window.virtualizer.reset_model(generators.bars)
        window._line_model = circuits.segments
        window._load_model = invalid_loads
        window.view.set_load_model(invalid_loads)
        window.load_virtualizer.reset_model(invalid_loads)
        window._set_generator_model(generators)
        window._set_circuit_catalog(circuits)
        schedule = default_calculation_levels()
        result = calculate_generator_demands(
            generators,
            circuits,
            window._phase_configuration,
            saved_curve(),
            (schedule, schedule),
            (GeneratorScheduleMode.DEFAULT, GeneratorScheduleMode.DEFAULT),
        )
        window._set_generator_update_result(result)

        window._set_selection(FeatureSelection("generator", 1))

        self.assertTrue(window.generator_demand_section.isHidden())
        self.assertTrue(window.generator_phase_power_section.isHidden())
        self.assertFalse(window.generator_update_note.isHidden())
        self.assertIn("FASES2", window.generator_update_note.text())

    def test_installing_or_invalidating_generator_result_clears_power_flow(self) -> None:
        window, generators, circuits = self.make_window_with_models()
        schedule = default_calculation_levels()
        result = calculate_generator_demands(
            generators,
            circuits,
            window._phase_configuration,
            saved_curve(),
            (schedule, schedule),
            (GeneratorScheduleMode.DEFAULT, GeneratorScheduleMode.DEFAULT),
        )

        window._power_flow_result = object()
        window._set_generator_update_result(result)
        self.assertIsNone(window._power_flow_result)

        window._power_flow_result = object()
        window._invalidate_generator_update()
        self.assertIsNone(window._power_flow_result)

    def test_pending_editor_cancel_prevents_opening_update_dialog(self) -> None:
        window, _, _ = self.make_window_with_models()
        with patch.object(
            window.curves_window, "confirm_pending_changes", return_value=False
        ), patch("circuit_viewer.main_window.UpdateGeneratorsDialog") as dialog:
            window._update_generators()
        dialog.assert_not_called()

    def test_confirmed_dialog_forwards_curve_schedules_and_modes(self) -> None:
        window, _, _ = self.make_window_with_models()
        schedule = default_calculation_levels()
        selected = saved_curve(first=2.0)
        with patch.object(
            window.curves_window, "confirm_pending_changes", return_value=True
        ), patch.object(
            window.patamares_window, "confirm_pending_changes", return_value=True
        ), patch(
            "circuit_viewer.main_window.UpdateGeneratorsDialog"
        ) as dialog_class, patch.object(
            window, "_start_generator_update"
        ) as start:
            dialog = dialog_class.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.selected_curve.return_value = selected
            dialog.effective_schedules.return_value = (schedule, schedule)
            dialog.schedule_modes.return_value = (
                GeneratorScheduleMode.DEFAULT,
                GeneratorScheduleMode.DEFAULT,
            )
            window._update_generators()

        start.assert_called_once_with(
            selected,
            (schedule, schedule),
            (GeneratorScheduleMode.DEFAULT, GeneratorScheduleMode.DEFAULT),
        )


if __name__ == "__main__":
    unittest.main()
