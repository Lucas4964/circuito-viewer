from __future__ import annotations

import unittest

try:
    from PyQt6.QtCore import Qt

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover - depende do ambiente
    PYQT_AVAILABLE = False

if PYQT_AVAILABLE:
    from circuit_viewer.power_flow_table import PowerFlowTableModel


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class PowerFlowTableDisplayTests(unittest.TestCase):
    """A tabela de fluxo de potência exibe com precisão fixa de 4 casas."""

    def _display(self, model: PowerFlowTableModel, row: int, column: int) -> str:
        return model.data(model.index(row, column))

    def test_default_decimals_is_four(self) -> None:
        # decimals=4 é o padrão de set_values() sem chamador explícito; todo
        # chamador do projeto passa o valor à mão, mas o padrão precisa refletir
        # a mesma regra.
        model = PowerFlowTableModel()
        model.set_values(("Fase D",), ((1.0,),))
        self.assertEqual(self._display(model, 0, 1), "1.0000")

    def test_an_integral_value_keeps_the_trailing_zeros(self) -> None:
        # O caso do pedido: "1.0000", não "1" nem "1.0".
        model = PowerFlowTableModel()
        model.set_values(("Fase D", "Fase E"), ((1.0, -1.0),), decimals=4)
        self.assertEqual(self._display(model, 0, 1), "1.0000")
        self.assertEqual(self._display(model, 0, 2), "-1.0000")

    def test_a_fractional_value_rounds_to_four_places(self) -> None:
        model = PowerFlowTableModel()
        model.set_values(("Fase D",), ((0.12345,),), decimals=4)
        self.assertEqual(self._display(model, 0, 1), "0.1235")

    def test_a_zero_value_keeps_four_zeros(self) -> None:
        model = PowerFlowTableModel()
        model.set_values(("θD",), ((0.0,),), decimals=4)
        self.assertEqual(self._display(model, 0, 1), "0.0000")

    def test_thousands_separator_is_a_space_not_a_comma(self) -> None:
        model = PowerFlowTableModel()
        model.set_values(("Fase D",), ((12345.6789,),), decimals=4)
        self.assertEqual(self._display(model, 0, 1), "12 345.6789")

    def test_missing_value_is_a_dash_not_a_number(self) -> None:
        model = PowerFlowTableModel()
        model.set_values(("Fase D",), ((None,),), decimals=4)
        self.assertEqual(self._display(model, 0, 1), "—")

    def test_per_column_decimals_still_work(self) -> None:
        # Módulo e ângulo dividem a tabela com precisões diferentes antes desta
        # mudança; o mecanismo de decimais por coluna continua disponível.
        model = PowerFlowTableModel()
        model.set_values(
            ("Fase D", "θD"), ((10.0, 0.0),), decimals=(4, 1)
        )
        self.assertEqual(self._display(model, 0, 1), "10.0000")
        self.assertEqual(self._display(model, 0, 2), "0.0")

    def test_patamar_column_is_never_formatted(self) -> None:
        model = PowerFlowTableModel()
        model.set_values(("Fase D",), ((1.0,), (1.0,), (1.0,), (1.0,)), decimals=4)
        for row in range(4):
            self.assertEqual(self._display(model, row, 0), str(row))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
