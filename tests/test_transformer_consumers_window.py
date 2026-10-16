"""Linhas das tabelas de cargas e de GD de um trafo do modo Energia."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.calculation_levels import default_calculation_levels
    from circuit_viewer.energy_demand import calculate_energy_demands
    from circuit_viewer.transformer_consumers import build_transformer_consumers
    from circuit_viewer.transformer_consumers_window import (
        TransformerTableWindow,
        consumer_rows,
        generator_rows,
        status_summary,
    )
    from tests.test_energy_demand import CATEGORIES, CURVES
    from tests.test_transformer_consumers import loads_model, standard_rows

    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt6
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class RowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        loads = loads_model()
        consumers = build_transformer_consumers(loads, **standard_rows())
        schedule = default_calculation_levels()
        energy = calculate_energy_demands(consumers, CURVES, CATEGORIES, (schedule,) * len(loads))
        self.index = loads.index_for_id("2")
        self.record = consumers.record(self.index)
        self.transformer = energy.transformer(self.index)

    def test_consumer_rows_follow_the_record_and_name_the_levels(self) -> None:
        headers, rows = consumer_rows(self.record, self.transformer)
        self.assertEqual(headers[-4:], tuple(
            f"P {level.name} (kW)" for level in default_calculation_levels().levels
        ))
        self.assertEqual([row[1][0] for row in rows], ["1001", "1002", "168662"])
        first = dict(zip(headers, (cell[0] for cell in rows[0])))
        self.assertEqual(first["Classe"], "0 RES")
        self.assertTrue(first["Curva de carga"].startswith("4 "))
        self.assertEqual(first["Status"], "AJUSTADO")
        lighting = dict(zip(headers, (cell[0] for cell in rows[2])))
        self.assertEqual(lighting["Classe"], "5 IP")
        self.assertEqual(lighting["Status"], "IP")

    def test_generator_rows_carry_the_share_of_g(self) -> None:
        headers, rows = generator_rows(self.record, self.transformer)
        (row,) = rows
        values = dict(zip(headers, (cell[0] for cell in row)))
        self.assertEqual(values["GERADOR_ID"], "71")
        self.assertEqual(values["UC"], "1001")
        self.assertEqual(values["Status da UC"], "AJUSTADO")
        self.assertTrue(float(values["G (kW)"]) > 0)

    def test_without_calculation_the_rows_still_list_the_loads(self) -> None:
        headers, rows = consumer_rows(self.record, None)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][headers.index("D (kW)")][0], "")
        self.assertIn("Sem cálculo", status_summary(None))

    def test_the_window_sorts_numbers_as_numbers(self) -> None:
        window = TransformerTableWindow()
        self.addCleanup(window.deleteLater)
        headers, rows = consumer_rows(self.record, self.transformer)
        window.show_rows("Cargas", "resumo", headers, rows)
        column = headers.index("Consumo (kWh)")
        window.table.sortByColumn(column, Qt.SortOrder.DescendingOrder)
        first = window.proxy.index(0, column).data()
        self.assertEqual(first, "120.50")
        window.close()


if __name__ == "__main__":
    unittest.main()
