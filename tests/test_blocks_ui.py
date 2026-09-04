from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication, QKeyEvent
    from PyQt6.QtWidgets import QApplication, QMenu

    from circuit_viewer.blocks_window import BlocksWindow, BlockTableModel
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import FeatureSelection

    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt
    PYQT_AVAILABLE = False

from circuit_viewer.block_analysis import analyze_blocks
from circuit_viewer.block_table import BLOCK_TABLE_HEADERS

from test_block_analysis import (  # noqa: E402
    make_bars,
    make_catalog,
    make_loads,
    make_network,
    make_switches,
)


def sample_result():  # noqa: ANN201
    """B0 —T0— B1 —T1(manobrável)— B2 —T2— B3, com carga em B1 e B3."""

    bars = make_bars(4)
    network = make_network(bars, [0, 1, 2], [1, 2, 3], lengths=[30.0, 5.0, 70.0])
    switches = make_switches(network, [(1, "1", "1")])
    loads = make_loads(bars, [1, 3], ["10", "25,5"])
    return (
        analyze_blocks(make_catalog(network, switches), switches, loads),
        network,
        switches,
    )


def many_boundaries_result():  # noqa: ANN201
    """Um bloco central com três fronteiras, para exercitar a abreviação."""

    #        B1                B3
    #         |T1(m)            |T3(m)
    # B0 —T0(m)— B2 —T2(m)— B4
    bars = make_bars(5)
    network = make_network(bars, [0, 1, 2, 3], [2, 2, 4, 2])
    switches = make_switches(
        network, [(0, "1", "1"), (1, "1", "1"), (2, "1", "1"), (3, "1", "1")]
    )
    return analyze_blocks(make_catalog(network, switches), switches)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BlockTableModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.result, self.network, self.switches = sample_result()
        self.model = BlockTableModel()
        self.model.set_result(self.result)

    def _column(self, name: str) -> int:
        return BLOCK_TABLE_HEADERS.index(name)

    def _display(self, row: int, name: str):  # noqa: ANN202
        return self.model.data(
            self.model.index(row, self._column(name)),
            Qt.ItemDataRole.DisplayRole,
        )

    def test_the_table_has_the_agreed_columns_in_order(self) -> None:
        self.assertEqual(
            self.model.HEADERS,
            (
                "BLOCO_ID",
                "NUM_BARRAS",
                "NUM_TRECHOS",
                "NUM_CARGAS",
                "SNOM",
                "COMPR",
                "NUM_CHAVES",
                "CHAVES",
                "FONTE",
            ),
        )
        self.assertEqual(self.model.rowCount(), 2)

    def test_the_counts_reach_the_cells(self) -> None:
        self.assertEqual(self._display(0, "BLOCO_ID"), "1")
        self.assertEqual(self._display(0, "NUM_BARRAS"), "2")
        self.assertEqual(self._display(0, "NUM_TRECHOS"), "1")
        self.assertEqual(self._display(0, "NUM_CARGAS"), "1")
        self.assertEqual(self._display(0, "NUM_CHAVES"), "1")

    def test_the_source_block_is_marked(self) -> None:
        marks = {self._display(row, "FONTE") for row in range(2)}
        self.assertEqual(marks, {"0", "1"})

    def test_a_block_without_a_value_shows_a_dash(self) -> None:
        result = many_boundaries_result()
        model = BlockTableModel()
        model.set_result(result)
        row = next(
            index
            for index in range(model.rowCount())
            if model.record(index).segment_count == 0
        )

        self.assertEqual(
            model.data(
                model.index(row, self._column("COMPR")),
                Qt.ItemDataRole.DisplayRole,
            ),
            "—",
        )

    # ------------------------------------------------- a coluna CHAVES ------

    def test_a_single_boundary_is_shown_whole(self) -> None:
        # Uma chave só: não há o que reticenciar.
        self.assertEqual(self._display(0, "CHAVES"), "COD-CH0")

    def test_several_boundaries_are_abbreviated(self) -> None:
        result = many_boundaries_result()
        model = BlockTableModel()
        model.set_result(result)
        row = next(
            index
            for index in range(model.rowCount())
            if model.record(index).boundary_count > 1
        )
        column = self._column("CHAVES")

        display = model.data(model.index(row, column), Qt.ItemDataRole.DisplayRole)
        tooltip = model.data(model.index(row, column), Qt.ItemDataRole.ToolTipRole)
        copiable = model.data(model.index(row, column), Qt.ItemDataRole.EditRole)

        self.assertTrue(display.endswith("…"), display)
        # O tooltip e o Ctrl+C trazem a lista inteira; a tela, só a dica.
        self.assertNotIn("…", tooltip)
        self.assertEqual(copiable, tooltip)
        self.assertGreater(len(tooltip), len(display))
        for code in model.record(row).boundary_switch_codes:
            self.assertIn(code, tooltip)

    def test_only_the_switches_column_differs_between_shown_and_copied(self) -> None:
        # Nas demais o EditRole é nulo, e a cópia cai para o DisplayRole.
        for name in BLOCK_TABLE_HEADERS:
            if name == "CHAVES":
                continue
            with self.subTest(column=name):
                index = self.model.index(0, self._column(name))
                self.assertIsNone(
                    self.model.data(index, Qt.ItemDataRole.EditRole)
                )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BlocksWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):  # noqa: ANN202
        result, _, _ = sample_result()
        model = BlockTableModel()
        window = BlocksWindow(model)
        self.addCleanup(window.close)
        window.set_result(result)
        return window, model, result

    def test_the_summary_counts_blocks_boundaries_and_dead_ends(self) -> None:
        window, _, result = self._window()

        text = window.summary_label.text()
        self.assertIn("2 bloco(s)", text)
        self.assertIn("1 chave(s) manobrável(is)", text)
        self.assertIn("2 bloco(s) com fronteira única", text)

    def test_selecting_a_row_emits_the_record(self) -> None:
        window, model, _ = self._window()
        window.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        received: list[object] = []
        window.blockSelected.connect(received.append)

        window.table.setCurrentIndex(window.proxy_model.index(0, 0))
        self.app.processEvents()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].block_id, 1)
        self.assertEqual(model.highlight_row, 0)

    def test_the_highlight_follows_the_source_row_not_the_visible_one(self) -> None:
        # A tabela ordena, então a linha visível não é a do modelo fonte.
        # Realçar a visível pintaria a linha errada.
        window, model, _ = self._window()
        window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        received: list[object] = []
        window.blockSelected.connect(received.append)

        window.table.setCurrentIndex(window.proxy_model.index(0, 0))
        self.app.processEvents()

        self.assertEqual(received[-1].block_id, 2)
        self.assertEqual(model.highlight_row, 1)

    def test_clearing_the_selection_drops_the_highlight(self) -> None:
        window, model, _ = self._window()
        window.table.setCurrentIndex(window.proxy_model.index(0, 0))
        self.app.processEvents()

        window.clear_selection()

        self.assertEqual(model.highlight_row, -1)

    def test_copying_the_switches_cell_yields_the_whole_list(self) -> None:
        # É o escape para o valor que a tela abrevia.
        result = many_boundaries_result()
        model = BlockTableModel()
        window = BlocksWindow(model)
        self.addCleanup(window.close)
        window.set_result(result)
        row = next(
            index
            for index in range(model.rowCount())
            if model.record(index).boundary_count > 1
        )
        column = BLOCK_TABLE_HEADERS.index("CHAVES")
        window.table.setCurrentIndex(window.proxy_model.index(row, column))
        window.table.selectionModel().select(
            window.proxy_model.index(row, column),
            window.table.selectionModel().SelectionFlag.Select,
        )

        window.table.copy_selection()
        self.app.processEvents()

        pasted = QGuiApplication.clipboard().text()
        self.assertNotIn("…", pasted)
        for code in model.record(row).boundary_switch_codes:
            self.assertIn(code, pasted)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BlocksIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_the_action_lives_in_the_tools_menu_and_needs_a_network(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)

        tools = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Ferramentas"
        )
        self.assertIn(window.blocks_action, tools.actions())
        self.assertFalse(window.blocks_action.isEnabled())

    def test_selecting_a_block_highlights_it_and_drops_the_branch(self) -> None:
        # Os dois realces pintam na mesma cor; deixá-los conviver confundiria.
        window = MainWindow()
        self.addCleanup(window.close)
        result, network, switches = sample_result()
        window._line_model = network
        window.block_table_model.set_result(result)
        record = result.records[0]

        window._select_block(record)

        self.assertTrue(window.block_highlight_overlay.isVisible())
        self.assertEqual(
            window.block_highlight_overlay.segment_indices,
            tuple(record.segment_indices.tolist()),
        )
        self.assertFalse(window.branch_highlight_overlay.isVisible())

        window._clear_block_highlight()
        self.assertFalse(window.block_highlight_overlay.isVisible())

    def test_a_block_without_segments_says_so_instead_of_failing(self) -> None:
        window = MainWindow()
        self.addCleanup(window.close)
        result = many_boundaries_result()
        bars = make_bars(5)
        network = make_network(bars, [0, 1, 2, 3], [2, 2, 4, 2])
        window._line_model = network
        empty = next(
            record for record in result.records if record.segment_count == 0
        )

        window._select_block(empty)

        self.assertFalse(window.block_highlight_overlay.isVisible())
        self.assertIn("não possui trecho", window.statusBar().currentMessage())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class SelectBlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_the_row_is_found_through_the_current_sort(self) -> None:
        # A tabela ordena; procurar pela linha do modelo fonte selecionaria a
        # linha errada na tela.
        result, _, _ = sample_result()
        model = BlockTableModel()
        window = BlocksWindow(model)
        self.addCleanup(window.close)
        window.set_result(result)
        window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)

        self.assertTrue(window.select_block(1))

        current = window.table.currentIndex()
        source_row = window.proxy_model.mapToSource(current).row()
        self.assertEqual(model.record(source_row).block_id, 1)

    def test_an_unknown_block_is_refused_without_moving_the_selection(self) -> None:
        result, _, _ = sample_result()
        model = BlockTableModel()
        window = BlocksWindow(model)
        self.addCleanup(window.close)
        window.set_result(result)

        self.assertFalse(window.select_block(99))

    def test_selecting_without_a_result_is_refused(self) -> None:
        window = BlocksWindow(BlockTableModel())
        self.addCleanup(window.close)

        self.assertFalse(window.select_block(1))


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class MapContextMenuTests(unittest.TestCase):
    """O atalho do mapa: clicar num trecho e chegar ao bloco dele."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):  # noqa: ANN202
        bars = make_bars(4)
        network = make_network(bars, [0, 1, 2], [1, 2, 3])
        # T1 manobrável (fronteira), T2 fusível (interno).
        switches = make_switches(network, [(1, "1", "1"), (2, "0", "1")])
        window = MainWindow()
        self.addCleanup(window.close)
        window._model = bars
        window._line_model = network
        window._switch_model = switches
        window._set_circuit_catalog(None)
        window._circuit_catalog = make_catalog(network, switches)
        return window, network, switches

    def _texts(self, window, segment_index: int) -> list[str]:  # noqa: ANN001
        menu = QMenu()
        window._add_block_actions(menu, segment_index)
        return [action.text() for action in menu.actions()]

    def test_a_common_segment_offers_one_shortcut(self) -> None:
        window, _, _ = self._window()
        window._show_blocks()

        texts = self._texts(window, 0)

        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith("Ver bloco "))

    def test_a_boundary_switch_offers_one_shortcut_per_side(self) -> None:
        # Ela separa dois blocos; escolher qual ver é do usuário.
        window, _, _ = self._window()
        window._show_blocks()

        texts = self._texts(window, 1)

        self.assertEqual(len(texts), 2)
        for text in texts:
            self.assertIn("trecho(s)", text)
        self.assertNotEqual(texts[0], texts[1])

    def test_a_fuse_offers_the_block_it_sits_inside(self) -> None:
        window, _, _ = self._window()
        window._show_blocks()

        self.assertEqual(len(self._texts(window, 2)), 1)

    def test_before_the_analysis_the_shortcut_is_still_offered(self) -> None:
        # Sem isso o atalho exigiria passar antes pelo menu Ferramentas, e
        # deixaria de ser atalho.
        window, _, _ = self._window()

        self.assertEqual(self._texts(window, 0), ["Ver bloco"])

    def test_the_shortcut_computes_the_analysis_and_selects_the_block(self) -> None:
        window, _, _ = self._window()
        self.assertIsNone(window.block_table_model.result)

        window._view_block(0)
        self.app.processEvents()

        self.assertIsNotNone(window.block_table_model.result)
        self.assertTrue(window.blocks_window.isVisible())
        current = window.blocks_window.table.currentIndex()
        self.assertTrue(current.isValid())
        source_row = window.blocks_window.proxy_model.mapToSource(current).row()
        expected = window.block_table_model.result.blocks_for_segment(0)[0]
        self.assertEqual(
            window.block_table_model.record(source_row).block_id,
            expected.block_id,
        )

    def test_the_menu_stays_closed_when_nothing_applies(self) -> None:
        # Menu vazio, ou com tudo cinza, gasta um clique para não dizer nada.
        window, _, _ = self._window()
        window._circuit_catalog = None

        menu = QMenu()
        window._add_block_actions(menu, 0)

        self.assertTrue(menu.isEmpty())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class DismissHighlightTests(unittest.TestCase):
    """Como o usuário desfaz um destaque, que antes ficava de pé para sempre."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):  # noqa: ANN202
        result, network, switches = sample_result()
        window = MainWindow()
        self.addCleanup(window.close)
        window._model = network.bars
        window._line_model = network
        window._switch_model = switches
        window._circuit_catalog = make_catalog(network, switches)
        window.block_table_model.set_result(result)
        # O bloco de B0: contém o trecho 0 e as barras 0 e 1.
        record = next(
            item
            for item in result.records
            if 0 in set(item.bar_indices.tolist())
        )
        window._select_block(record)
        return window, record, network

    def test_clicking_inside_the_highlight_keeps_it(self) -> None:
        # Inspecionar um trecho da própria região não deveria custar o lugar.
        window, record, _ = self._window()
        inside = int(record.segment_indices[0])

        window._set_selection(FeatureSelection("segment", inside))

        self.assertTrue(window.block_highlight_overlay.isVisible())
        self.assertIsNotNone(window._selected_block)

    def test_clicking_a_bar_inside_the_highlight_keeps_it(self) -> None:
        window, record, _ = self._window()
        inside = int(record.bar_indices[0])

        window._set_selection(FeatureSelection("bar", inside))

        self.assertTrue(window.block_highlight_overlay.isVisible())

    def test_clicking_outside_clears_it(self) -> None:
        window, record, network = self._window()
        owned = set(record.segment_indices.tolist())
        outside = next(
            index for index in range(len(network)) if index not in owned
        )

        window._set_selection(FeatureSelection("segment", outside))

        self.assertFalse(window.block_highlight_overlay.isVisible())
        self.assertIsNone(window._selected_block)

    def test_clicking_empty_space_clears_it(self) -> None:
        window, _, _ = self._window()

        window._set_selection(None)

        self.assertFalse(window.block_highlight_overlay.isVisible())

    def test_escape_removes_the_highlight_before_the_selection(self) -> None:
        # Um nível por vez: dá para tirar o destaque e seguir examinando.
        window, record, _ = self._window()
        inside = int(record.segment_indices[0])
        window._set_selection(FeatureSelection("segment", inside))
        self.assertTrue(window.block_highlight_overlay.isVisible())

        window._escape_pressed()

        self.assertFalse(window.block_highlight_overlay.isVisible())
        self.assertIsNotNone(window._selected_feature)

        window._escape_pressed()

        self.assertIsNone(window._selected_feature)

    def test_the_power_flow_arriving_does_not_erase_the_highlight(self) -> None:
        # É o que preserve_highlight protege, e é fácil de quebrar sem notar.
        window, record, _ = self._window()
        window._set_selection(
            FeatureSelection("segment", int(record.segment_indices[0]))
        )

        window._set_selection(window._selected_feature, preserve_highlight=True)

        self.assertTrue(window.block_highlight_overlay.isVisible())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BlocksWindowEscapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):  # noqa: ANN202
        result, _, _ = sample_result()
        model = BlockTableModel()
        window = BlocksWindow(model)
        self.addCleanup(window.close)
        window.set_result(result)
        window.show()
        return window

    def _escape(self, window) -> None:  # noqa: ANN001
        window.keyPressEvent(
            QKeyEvent(
                QKeyEvent.Type.KeyPress,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.app.processEvents()

    def test_escape_unselects_the_row_without_closing(self) -> None:
        window = self._window()
        window.table.setCurrentIndex(window.proxy_model.index(0, 0))
        self.app.processEvents()

        self._escape(window)

        self.assertFalse(window.table.currentIndex().isValid())
        self.assertTrue(window.isVisible())

    def test_escape_without_a_row_closes_the_window(self) -> None:
        # O fechar-com-Esc do QDialog não se perde; vira o segundo passo.
        window = self._window()
        window.clear_selection()
        self.app.processEvents()

        self._escape(window)

        self.assertFalse(window.isVisible())


if __name__ == "__main__":
    unittest.main()
