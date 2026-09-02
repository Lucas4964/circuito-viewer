from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPointF, QRectF
    from PyQt6.QtWidgets import QApplication, QDialog

    from circuit_viewer.graphics import (
        GENERATOR_DIAMETER_PX,
        LOAD_HEIGHT_PX,
        LOAD_WIDTH_PX,
        LoadItem,
        load_layout_offsets_for_models,
    )
    from circuit_viewer.main_window import (
        GeneratorCsvImportDialog,
        ImportChoiceDialog,
        MainWindow,
    )

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False

from circuit_viewer.model import (
    CircuitModel,
    FeatureSelection,
    GeneratorModel,
    LoadModel,
    UtmCrs,
)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class GeneratorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_models(self):
        bars = CircuitModel(
            ["B1"], ["BAR"], [500_000.0], [8_000_000.0], UtmCrs(21, False)
        )
        loads = LoadModel(
            bars,
            ["L2", "L1"],
            [0, 0],
            ["", ""],
            ["LOAD-2", "LOAD-1"],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
        )
        generators = GeneratorModel(
            loads,
            ["G2", "G1"],
            [0, 1],
            ["MC2", "MC1"],
            ["GEN-2", "GEN-1"],
            ["13.8", "13.8"],
            ["75", "50"],
            ["Y", "D"],
            ["CUR2", "CUR1"],
            ["200", "100"],
            ["C2", "C1"],
            ["GEN-2", "GEN-1"],
            ["E2", "E1"],
            ["Usina 2", "Usina 1"],
            ["ABC", "A"],
        )
        return bars, loads, generators

    def test_generator_symbol_is_a_circle_with_exact_hit_shape(self) -> None:
        _, _, generators = self.make_models()
        item = LoadItem(symbol_kind="generator")
        item.bind(generators, 0, 0.0, 6.0)
        self.assertEqual(item.symbol_rect.width(), GENERATOR_DIAMETER_PX)
        self.assertTrue(item.shape().contains(QPointF(0.0, 11.0)))
        self.assertFalse(item.shape().contains(QPointF(-4.9, 6.1)))

    def test_joint_layout_keeps_loads_and_generators_separated(self) -> None:
        _, loads, generators = self.make_models()
        load_layout, generator_layout = load_layout_offsets_for_models(
            (loads, generators)
        )
        rects: list[QRectF] = []
        for x, y in zip(*load_layout, strict=True):
            rects.append(
                QRectF(
                    float(x) - LOAD_WIDTH_PX / 2,
                    float(y),
                    LOAD_WIDTH_PX,
                    LOAD_HEIGHT_PX,
                )
            )
        for x, y in zip(*generator_layout, strict=True):
            rects.append(
                QRectF(
                    float(x) - GENERATOR_DIAMETER_PX / 2,
                    float(y),
                    GENERATOR_DIAMETER_PX,
                    GENERATOR_DIAMETER_PX,
                )
            )
        for index, first in enumerate(rects):
            for second in rects[index + 1 :]:
                self.assertFalse(first.adjusted(-1.49, -1.49, 1.49, 1.49).intersects(second))

    def test_layout_mask_does_not_reserve_space_for_hidden_zero_equivalents(self) -> None:
        _, loads, generators = self.make_models()
        baseline = load_layout_offsets_for_models((loads,))[0]

        load_layout, _ = load_layout_offsets_for_models(
            (loads, generators),
            (None, (False, False)),
        )

        self.assertEqual(tuple(load_layout[0]), tuple(baseline[0]))
        self.assertEqual(tuple(load_layout[1]), tuple(baseline[1]))

    def test_details_visibility_and_load_reimport_invalidation(self) -> None:
        bars, loads, generators = self.make_models()
        window = MainWindow()
        self.addCleanup(window.close)
        window._model = bars
        window.view.set_model(bars)
        window.virtualizer.reset_model(bars)
        window._set_load_model(loads)
        window._set_generator_model(generators)

        self.assertTrue(window.show_generators_action.isEnabled())
        # A camada nasce oculta; o realce de seleção só existe com ela ligada.
        window.show_generators_action.setChecked(True)
        window._set_selection(FeatureSelection("generator", 1))
        self.assertEqual(window.details_dock.windowTitle(), "Gerador selecionado")
        self.assertIs(
            window.details_stack.currentWidget(), window.generator_details_page
        )
        self.assertEqual(window.generator_detail_labels["generator_id"].text(), "G1")
        self.assertEqual(window.generator_consumer_detail_labels["load_id"].text(), "L1")
        self.assertTrue(window.generator_virtualizer.selection_overlay.isVisible())

        replacement = LoadModel(
            bars,
            ["L3"],
            [0],
            [""],
            [""],
            [""],
            [""],
            [""],
            [""],
            [""],
        )
        window._set_load_model(replacement)
        self.assertIsNone(window._generator_model)
        self.assertIsNone(window._selected_feature)
        self.assertFalse(window.show_generators_action.isEnabled())

    def test_generator_import_choice_requires_loads(self) -> None:
        without_loads = ImportChoiceDialog(True, True, has_loads=False)
        with_loads = ImportChoiceDialog(True, True, has_loads=True)
        self.addCleanup(without_loads.close)
        self.addCleanup(with_loads.close)
        self.assertFalse(without_loads.generators_button.isEnabled())
        self.assertTrue(with_loads.generators_button.isEnabled())

    def test_csv_dialog_requires_both_explicit_files_in_any_order(self) -> None:
        dialog = GeneratorCsvImportDialog()
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertIn("MT_GERADOR_CONS", dialog.generator_file_button.text())
        self.assertIn("MT_CONS", dialog.consumer_file_button.text())

        dialog.set_consumer_path(r"C:\dados\MT_CONS.csv")
        self.assertFalse(dialog.import_button.isEnabled())
        dialog.set_generator_path(r"C:\dados\MT_GERADOR_CONS.csv")
        self.assertTrue(dialog.import_button.isEnabled())
        self.assertEqual(dialog.generator_path(), r"C:\dados\MT_GERADOR_CONS.csv")
        self.assertEqual(dialog.consumer_path(), r"C:\dados\MT_CONS.csv")
        self.assertEqual(dialog.generator_file_label.text(), "MT_GERADOR_CONS.csv")
        self.assertEqual(
            dialog.consumer_file_label.toolTip(), r"C:\dados\MT_CONS.csv"
        )

    def test_csv_dialog_allows_replacing_each_file_and_cancel(self) -> None:
        dialog = GeneratorCsvImportDialog()
        self.addCleanup(dialog.close)
        dialog.set_generator_path(r"C:\a\primeiro.csv")
        dialog.set_generator_path(r"C:\b\segundo.csv")
        dialog.set_consumer_path(r"C:\b\cons.csv")
        self.assertEqual(dialog.generator_path(), r"C:\b\segundo.csv")
        dialog.reject()
        self.assertEqual(dialog.result(), dialog.DialogCode.Rejected)

    def test_main_window_forwards_both_confirmed_paths(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        window._load_model = object()
        with patch(
            "circuit_viewer.main_window.GeneratorCsvImportDialog"
        ) as dialog_class, patch.object(
            window, "_start_generator_import"
        ) as start:
            dialog = dialog_class.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.generator_path.return_value = r"C:\dados\MT_GERADOR_CONS.csv"
            dialog.consumer_path.return_value = r"C:\dados\MT_CONS.csv"
            window._choose_generators_csv()
        start.assert_called_once_with(
            r"C:\dados\MT_GERADOR_CONS.csv", r"C:\dados\MT_CONS.csv"
        )

    def test_main_window_cancel_does_not_start_generator_import(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        window._load_model = object()
        with patch(
            "circuit_viewer.main_window.GeneratorCsvImportDialog"
        ) as dialog_class, patch.object(
            window, "_start_generator_import"
        ) as start:
            dialog_class.return_value.exec.return_value = QDialog.DialogCode.Rejected
            window._choose_generators_csv()
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
