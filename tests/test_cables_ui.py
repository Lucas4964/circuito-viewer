from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QDialog

    from circuit_viewer.cable_import import CableCsvResult
    from circuit_viewer.cables_window import CableTableModel, _as_number
    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.load_import import LoadCsvResult
    from circuit_viewer.main_window import ImportChoiceDialog, MainWindow
    from circuit_viewer.model import (
        CableModel,
        CircuitModel,
        FeatureSelection,
        LineNetworkModel,
        LoadModel,
        UtmCrs,
    )
    from circuit_viewer.segment_import import SegmentLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


def make_cables() -> CableModel:
    return CableModel(
        ["C1", "C2", "C3"],
        ["CA", "CAA", "CU"],
        ["4/0", "336", "16"],
        ["340", "20", "100"],
        *(["", "", ""] for _ in range(8)),
        ["Alumínio nu 4/0", "Alumínio com alma de aço", ""],
        ["EXT-1", "EXT-2", "EXT-3"],
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CablesWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        return window

    def _window_with_network(self) -> MainWindow:
        bars = CircuitModel(
            ["B0", "B1"],
            ["COD-B0", ""],  # a segunda barra não tem código
            [500_000.0, 500_010.0],
            [8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T0"],
            [""],
            ["13"],  # FASES2 mapeado como DEF no fases2.json
            [0],
            [1],
            [""],
            ["C1"],  # CABOF_ID conhecido
            ["C9"],  # CABON_ID ausente do catálogo
            [10.0],
        )
        window = self._window()
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 2, 2, 0, (), 0))
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 1, 1, 0, (), 0)
        )
        self.app.processEvents()
        return window

    def _window_with_loads(self) -> MainWindow:
        window = self._window_with_network()
        loads = LoadModel(
            window._model,
            ["CG1", "CG2"],
            [0, 1],  # a segunda carga fica na barra sem código
            ["EXT-1", "EXT-2"],
            ["COD-CG1", "COD-CG2"],
            ["10", "20"],
            ["12", "22"],
            ["220", "220"],
            ["13", "404"],  # FASES2 mapeado e sem relação no fases2.json
            ["Y", "Y"],
        )
        window._on_load_import_finished(
            LoadCsvResult(loads, "utf-8-sig", 2, 2, 0, (), 0)
        )
        self.app.processEvents()
        return window

    def _import_cables(self, window: MainWindow) -> None:
        window._on_cable_import_finished(
            CableCsvResult(make_cables(), "utf-8-sig", 3, 3, 0, 0, (), 0)
        )

    def test_tables_menu_opens_the_window_without_data(self) -> None:
        window = self._window()

        tables_menu = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Tabelas"
        )
        self.assertIn(window.cables_action, tables_menu.actions())
        # Sempre disponível: sem catálogo a janela oferece a importação.
        self.assertTrue(window.cables_action.isEnabled())

        window.cables_action.trigger()
        self.assertTrue(window.cables_window.isVisible())
        self.assertFalse(window.cables_window.isModal())
        self.assertTrue(window.cables_window.import_button.isVisible())
        self.assertFalse(window.cables_window.table.isVisible())
        self.assertIn("Nenhum cabo", window.cables_window.summary_label.text())

        # O botão precisa chegar ao mesmo fluxo de Arquivo > Importar CSV…; o
        # diálogo de arquivo é substituído por um cancelamento.
        with patch(
            "circuit_viewer.main_window.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ) as file_dialog:
            window.cables_window.import_button.click()

        file_dialog.assert_called_once()
        self.assertEqual(file_dialog.call_args.args[1], "Importar cabos")
        self.assertIsNone(window._import_thread)

    def test_import_choice_offers_cables_without_dependencies(self) -> None:
        window = self._window()

        dialog = ImportChoiceDialog(False, False, window)
        self.assertTrue(dialog.cables_button.isEnabled())
        dialog.cables_button.click()

        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.selected_kind, "cables")

    def test_table_shows_every_column_in_file_order(self) -> None:
        window = self._window()
        self._import_cables(window)
        window.cables_action.trigger()

        model = window.cable_table_model
        self.assertEqual(model.rowCount(), 3)
        self.assertEqual(model.columnCount(), 14)
        self.assertEqual(
            tuple(
                model.headerData(
                    column,
                    Qt.Orientation.Horizontal,
                    Qt.ItemDataRole.DisplayRole,
                )
                for column in range(model.columnCount())
            ),
            (
                "CABO_ID",
                "TIPO",
                "CODIGO",
                "IADM",
                "GMR",
                "R",
                "X",
                "QCAP",
                "R0",
                "X0",
                "R1",
                "X1",
                "NOME",
                "EXTERN_ID",
            ),
        )
        self.assertEqual(model.data(model.index(0, 0)), "C1")
        self.assertEqual(model.data(model.index(0, 12)), "Alumínio nu 4/0")
        self.assertEqual(model.data(model.index(2, 12)), "—")
        self.assertTrue(window.cables_window.table.isVisible())
        self.assertFalse(window.cables_window.import_button.isVisible())
        self.assertIn("3", window.cables_window.summary_label.text())

    def test_numeric_columns_sort_by_value(self) -> None:
        window = self._window()
        self._import_cables(window)
        proxy = window.cables_window.proxy_model

        proxy.sort(3, Qt.SortOrder.AscendingOrder)

        ordered = [
            proxy.data(proxy.index(row, 3)) for row in range(proxy.rowCount())
        ]
        # Lexicograficamente "100" viria antes de "20".
        self.assertEqual(ordered, ["20", "100", "340"])

    def test_number_parsing_accepts_a_single_decimal_separator(self) -> None:
        self.assertEqual(_as_number("0,42"), 0.42)
        self.assertEqual(_as_number("0.42"), 0.42)
        self.assertEqual(_as_number(""), float("inf"))
        self.assertEqual(_as_number("n/a"), float("inf"))
        self.assertEqual(_as_number("1.234,5"), float("inf"))

    def test_segment_details_show_the_cable_after_the_catalog_arrives(self) -> None:
        window = self._window_with_network()
        window._set_selection(FeatureSelection("segment", 0))

        companions = window.segment_companion_labels
        self.assertEqual(
            set(companions),
            {
                "segment_id",
                "code",
                "phases",
                "start_bar_id",
                "end_bar_id",
                "arrangement_id",
                "phase_cable_id",
                "neutral_cable_id",
                "length",
            },
        )
        # Sem catálogo de cabos as células de cabo ficam vazias, mas a coluna
        # já aparece por causa do NOME das fases.
        self.assertEqual(companions["phase_cable_id"].text(), "—")
        # BARRA1_ID/BARRA2_ID independem do catálogo de cabos: o código da
        # barra já está disponível pelo modelo de trechos.
        self.assertEqual(companions["start_bar_id"].text(), "COD-B0")
        self.assertEqual(companions["end_bar_id"].text(), "—")
        self.assertTrue(companions["start_bar_id"].isVisible())
        self.assertTrue(companions["end_bar_id"].isVisible())

        self._import_cables(window)
        self.app.processEvents()

        self.assertTrue(all(label.isVisible() for label in companions.values()))
        self.assertEqual(companions["phase_cable_id"].text(), "4/0")
        self.assertIn("IADM: 340", companions["phase_cable_id"].toolTip())
        # CABON_ID aponta para um cabo fora do catálogo.
        self.assertEqual(companions["neutral_cable_id"].text(), "—")
        self.assertEqual(companions["neutral_cable_id"].toolTip(), "")
        # Linhas sem cabo nem fases ficam vazias, apenas mantendo a grade.
        self.assertEqual(companions["code"].text(), "—")

        window._set_cable_model(None)
        self.assertEqual(companions["phase_cable_id"].text(), "—")

    def test_segment_details_show_the_phase_name(self) -> None:
        window = self._window_with_network()
        window._set_selection(FeatureSelection("segment", 0))

        companions = window.segment_companion_labels
        # A coluna aparece mesmo sem catálogo de cabos importado.
        self.assertTrue(companions["phases"].isVisible())
        self.assertEqual(companions["phases"].text(), "DEF")
        self.assertEqual(companions["phases"].toolTip(), "NUMERO_FASES: 3")

    def test_load_details_show_the_bar_code_and_the_phase_name(self) -> None:
        window = self._window_with_loads()
        window._set_selection(FeatureSelection("load", 0))

        companions = window.load_companion_labels
        self.assertEqual(
            set(companions),
            {
                "load_id",
                "bar_id",
                "external_id",
                "code",
                "snom",
                "sadm",
                "secondary_line_voltage",
                "phases",
                "connection_type",
            },
        )
        self.assertTrue(all(label.isVisible() for label in companions.values()))

        # BARRA_ID mostra o código da barra, não o próprio identificador.
        self.assertEqual(window.load_detail_labels["bar_id"].text(), "B0")
        self.assertEqual(companions["bar_id"].text(), "COD-B0")
        self.assertEqual(companions["bar_id"].toolTip(), "")

        self.assertEqual(window.load_detail_labels["phases"].text(), "13")
        self.assertEqual(companions["phases"].text(), "DEF")
        self.assertEqual(companions["phases"].toolTip(), "NUMERO_FASES: 3")

        # As demais linhas ficam vazias, apenas mantendo a grade.
        self.assertEqual(companions["snom"].text(), "—")
        self.assertEqual(companions["snom"].toolTip(), "")

    def test_load_details_fall_back_when_data_is_missing(self) -> None:
        window = self._window_with_loads()
        window._set_selection(FeatureSelection("load", 1))

        companions = window.load_companion_labels
        # A segunda carga está em uma barra sem CODIGO...
        self.assertEqual(companions["bar_id"].text(), "—")
        # ...e usa um FASES2 sem relação no fases2.json.
        self.assertEqual(companions["phases"].text(), "—")
        self.assertEqual(companions["phases"].toolTip(), "")

    def test_companion_labels_stay_out_of_the_value_dictionaries(self) -> None:
        window = self._window_with_loads()

        for key, label in window.load_companion_labels.items():
            self.assertIsNot(label, window.load_detail_labels[key])
            self.assertIsNot(label, window.load_caption_labels[key])

    def test_cable_import_does_not_disturb_other_models(self) -> None:
        window = self._window_with_network()
        line_model = window._line_model

        self._import_cables(window)

        self.assertIs(window._line_model, line_model)
        self.assertEqual(len(window._cable_model), 3)


if __name__ == "__main__":
    unittest.main()
