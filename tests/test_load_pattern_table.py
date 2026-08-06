from __future__ import annotations

import unittest
from decimal import Decimal

try:
    from PyQt6.QtCore import Qt

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover - depende do ambiente
    PYQT_AVAILABLE = False

if PYQT_AVAILABLE:
    from circuit_viewer.equivalent_network import EquivalentLoadPatternRecord
    from circuit_viewer.load_pattern_table import (
        DISPLAY_DECIMALS,
        LoadPatternTableModel,
    )
    from circuit_viewer.model import LoadPatternRecord


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class LoadPatternTableDisplayTests(unittest.TestCase):
    """A tabela arredonda a exibição sem tocar no valor guardado."""

    def _model_with(self, *values: str) -> LoadPatternTableModel:
        model = LoadPatternTableModel()
        model.set_records(
            tuple(
                LoadPatternRecord("L1", npat, *values)
                for npat in range(4)
            )
        )
        return model

    def _display(self, model: LoadPatternTableModel, column: int) -> str:
        return model.data(model.index(0, column))

    def test_four_decimals_for_dot_and_comma_separators(self) -> None:
        model = self._model_with(
            "1.23456789",
            "1,23456789",
            "3",
            "-2.5",
            "0",
            "1.5",
        )
        self.assertEqual(DISPLAY_DECIMALS, 4)
        self.assertEqual(self._display(model, 2), "1.2346")
        self.assertEqual(self._display(model, 3), "1.2346")
        self.assertEqual(self._display(model, 4), "3.0000")
        self.assertEqual(self._display(model, 5), "-2.5000")
        self.assertEqual(self._display(model, 6), "0.0000")
        self.assertEqual(self._display(model, 7), "1.5000")

    def test_non_numeric_text_survives_untouched(self) -> None:
        # Um valor que o usuário digitou e não é número precisa aparecer como
        # está; "1.234,56" mistura os dois separadores e é ambíguo de propósito.
        model = self._model_with("P1", "NOVO1", "1.234,56", "", "n/d", "--")
        self.assertEqual(self._display(model, 2), "P1")
        self.assertEqual(self._display(model, 3), "NOVO1")
        self.assertEqual(self._display(model, 4), "1.234,56")
        self.assertEqual(self._display(model, 5), "—")
        self.assertEqual(self._display(model, 6), "n/d")
        self.assertEqual(self._display(model, 7), "--")

    def test_tooltip_keeps_full_precision(self) -> None:
        model = self._model_with(*["1.23456789"] * 6)
        self.assertEqual(self._display(model, 2), "1.2346")
        self.assertEqual(
            model.data(model.index(0, 2), Qt.ItemDataRole.ToolTipRole),
            "1.23456789",
        )

    def test_identifier_columns_are_never_formatted(self) -> None:
        model = self._model_with(*["1.23456789"] * 6)
        self.assertEqual(self._display(model, 0), "L1")
        self.assertEqual(self._display(model, 1), "0")

    def test_decimal_records_are_rounded_without_going_through_float(self) -> None:
        value = Decimal("1.2345678901234567890123456789")
        model = LoadPatternTableModel()
        model.set_records(
            tuple(
                EquivalentLoadPatternRecord("L1", npat, *([value] * 6))
                for npat in range(4)
            )
        )
        self.assertEqual(self._display(model, 2), "1.2346")
        self.assertEqual(
            model.data(model.index(0, 2), Qt.ItemDataRole.ToolTipRole),
            str(value),
        )

    def test_stored_values_are_not_modified(self) -> None:
        model = self._model_with(*["1.23456789"] * 6)
        self.assertEqual(model.records[0].pd, "1.23456789")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
