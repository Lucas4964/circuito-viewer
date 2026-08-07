from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QHeaderView, QTableView

    from circuit_viewer.power_flow_table import PowerFlowTableModel
    from circuit_viewer.table_columns import (
        EXCEL_LIKE_TABLE_STYLE,
        enable_interactive_columns,
    )

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


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class AlwaysRefitTests(unittest.TestCase):
    """``always_refit=True`` é o que as tabelas de fluxo de potência e
    patamares precisam: o conjunto de colunas muda por completo a cada
    seleção ou troca de grandeza, então uma largura escolhida a dedo não pode
    sobreviver à próxima atualização.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _table(self) -> tuple[QTableView, PowerFlowTableModel]:
        model = PowerFlowTableModel()
        table = QTableView()
        self.addCleanup(table.deleteLater)
        table.setModel(model)
        enable_interactive_columns(table, always_refit=True)
        return table, model

    def _fill(self, model: PowerFlowTableModel, value: float) -> None:
        model.set_values(
            ("Fase D", "Fase E", "Fase F"),
            tuple((value, value, value) for _ in range(4)),
        )

    def test_manual_width_does_not_survive_new_data(self) -> None:
        table, model = self._table()
        header = table.horizontalHeader()
        self._fill(model, 1.0)

        header.resizeSection(1, 240)
        self.assertEqual(header.sectionSize(1), 240)

        # Ao contrário do modo padrão, a próxima reposição de dados descarta a
        # largura escolhida pelo usuário e reajusta ao novo conteúdo.
        self._fill(model, 123456789.123456)
        self.assertNotEqual(header.sectionSize(1), 240)

    def test_still_refits_when_the_user_never_dragged(self) -> None:
        # always_refit não pode quebrar o caminho comum, sem arraste algum.
        table, model = self._table()
        header = table.horizontalHeader()

        self._fill(model, 1.0)
        narrow = header.sectionSize(1)
        self._fill(model, 123456789.123456)
        self.assertGreater(header.sectionSize(1), narrow)

    def test_default_behaviour_is_unaffected(self) -> None:
        # A flag é opt-in: sem ela, o comportamento antigo (que outras
        # tabelas do projeto dependem) continua exatamente igual.
        model = PowerFlowTableModel()
        table = QTableView()
        self.addCleanup(table.deleteLater)
        table.setModel(model)
        enable_interactive_columns(table)
        header = table.horizontalHeader()
        self._fill(model, 1.0)

        header.resizeSection(1, 240)
        self._fill(model, 123456789.123456)
        self.assertEqual(header.sectionSize(1), 240)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CellPaddingStyleTests(unittest.TestCase):
    """``EXCEL_LIKE_TABLE_STYLE`` precisa alargar as colunas, não só colorir
    as linhas de grade — é o padding horizontal que dá a folga entre o valor e
    a borda da coluna.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _table(self) -> tuple[QTableView, PowerFlowTableModel]:
        model = PowerFlowTableModel()
        table = QTableView()
        self.addCleanup(table.deleteLater)
        table.setModel(model)
        enable_interactive_columns(table)
        model.set_values(
            ("Fase D", "Fase E", "Fase F"),
            tuple((123456789.123456,) * 3 for _ in range(4)),
        )
        return table, model

    def test_padding_widens_the_column(self) -> None:
        unpadded, _ = self._table()
        unpadded_width = unpadded.horizontalHeader().sectionSize(1)

        padded, _ = self._table()
        padded.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        padded.resizeColumnsToContents()

        self.assertGreater(
            padded.horizontalHeader().sectionSize(1), unpadded_width
        )

    def test_style_keeps_the_gridline_rule(self) -> None:
        # O padding é aditivo: a regra que já existia não pode desaparecer.
        self.assertIn("gridline-color", EXCEL_LIKE_TABLE_STYLE)

    def test_padding_is_only_horizontal(self) -> None:
        # Vertical não-zero brigaria com a altura fixa das linhas (28px) e o
        # AlignVCenter que os modelos dessas tabelas já usam.
        self.assertIn("padding: 0px 10px", EXCEL_LIKE_TABLE_STYLE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
