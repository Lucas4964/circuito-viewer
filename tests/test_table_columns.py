from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QHeaderView, QTableView

    from circuit_viewer.power_flow_table import PowerFlowTableModel
    from circuit_viewer.table_columns import enable_interactive_columns

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class InteractiveColumnsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _table(self) -> tuple[QTableView, PowerFlowTableModel]:
        model = PowerFlowTableModel()
        table = QTableView()
        self.addCleanup(table.deleteLater)
        table.setModel(model)
        enable_interactive_columns(table)
        return table, model

    def _fill(self, model: PowerFlowTableModel, value: float) -> None:
        model.set_values(
            ("Fase D", "Fase E", "Fase F"),
            tuple((value, value, value) for _ in range(4)),
        )

    def test_columns_become_draggable(self) -> None:
        table, _ = self._table()
        header = table.horizontalHeader()
        for column in range(header.count()):
            self.assertEqual(
                header.sectionResizeMode(column),
                QHeaderView.ResizeMode.Interactive,
            )
        self.assertFalse(header.stretchLastSection())

    def test_width_follows_contents_until_the_user_takes_over(self) -> None:
        table, model = self._table()
        header = table.horizontalHeader()

        self._fill(model, 1.0)
        narrow = header.sectionSize(1)
        # Números bem mais largos: sem intervenção, a coluna acompanha.
        self._fill(model, 123456789.123456)
        self.assertGreater(header.sectionSize(1), narrow)

    def test_manual_width_survives_new_data(self) -> None:
        table, model = self._table()
        header = table.horizontalHeader()
        self._fill(model, 1.0)

        header.resizeSection(1, 240)
        self.assertEqual(header.sectionSize(1), 240)

        # Trocar de elemento não pode desfazer a largura escolhida pelo usuário.
        self._fill(model, 123456789.123456)
        self.assertEqual(header.sectionSize(1), 240)

    def test_double_click_fits_contents_and_keeps_following(self) -> None:
        table, model = self._table()
        header = table.horizontalHeader()
        self._fill(model, 1.0)

        header.sectionHandleDoubleClicked.emit(1)
        fitted = header.sectionSize(1)

        # O duplo-clique pede o mesmo que o ajuste automático, então ele não
        # conta como largura escolhida a dedo: a coluna segue acompanhando.
        self._fill(model, 123456789.123456)
        self.assertGreater(header.sectionSize(1), fitted)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
