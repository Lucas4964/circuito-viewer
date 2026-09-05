from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, QSettings, Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.block_graph import (
        FIXED_NODE_DIAMETER,
        MAX_NODE_DIAMETER,
    )
    from circuit_viewer.block_graph_window import (
        BLOCK_GRAPH_SCALE_SETTINGS_KEY,
        CANVAS_BACKGROUND,
        DEFAULT_NODE_COLOR,
        INTERCIRCUIT_COLOR,
        BlockGraphWindow,
        load_scale_nodes_by_power,
        parse_scale_nodes_by_power,
        save_scale_nodes_by_power,
    )
    from circuit_viewer.blocks_window import BlockTableModel, BlocksWindow
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import CircuitVisibilityController
    from circuit_viewer.theme import AppTheme, build_palette

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover - ambiente mínimo sem extras de UI
    PYQT_AVAILABLE = False

from test_blocks_ui import sample_result
from test_block_analysis import make_catalog


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BlockGraphWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, *, scale: bool = False):  # noqa: ANN202
        result, _, _ = sample_result()
        window = BlockGraphWindow(scale_nodes_by_power=scale)
        self.addCleanup(window.close)
        window.set_result(result)
        return window, result

    def test_the_window_starts_with_a_blank_graph(self) -> None:
        window = BlockGraphWindow()
        self.addCleanup(window.close)

        self.assertEqual(window.view.node_items, {})
        self.assertEqual(window.view.edge_items, [])
        self.assertFalse(window.fit_button.isEnabled())

    def test_result_builds_labeled_nodes_and_edges(self) -> None:
        window, result = self._window()

        self.assertEqual(set(window.view.node_items), {1, 2})
        self.assertEqual(len(window.view.edge_items), 1)
        self.assertEqual(window.view.edge_items[0].edge.label, "COD-CH0")
        for record in result.records:
            item = window.view.node_items[record.block_id]
            self.assertIn(f"Bloco {record.block_id}", item.toolTip())
            self.assertIn("kVA", item.toolTip())

    def test_window_content_and_canvas_are_always_white(self) -> None:
        window, _ = self._window()
        previous_palette = QPalette(self.app.palette())
        self.addCleanup(self.app.setPalette, previous_palette)

        self.app.setPalette(build_palette(AppTheme.DARK))
        self.app.processEvents()

        self.assertEqual(
            window.palette().color(QPalette.ColorRole.Window).name().upper(),
            CANVAS_BACKGROUND,
        )
        self.assertEqual(
            window.view.backgroundBrush().color().name().upper(),
            CANVAS_BACKGROUND,
        )

    def test_circuit_styles_color_nodes_and_highlight_intercircuit_switch(self) -> None:
        window, _ = self._window()

        window.set_circuit_styles(
            {1: 0, 2: 1},
            ("#112233", "#F1EEDD"),
            ("C1", "C2"),
        )

        self.assertEqual(window.view.node_items[1].fill_color.name(), "#112233")
        self.assertEqual(window.view.node_items[1].text_color.name(), "#ffffff")
        self.assertEqual(window.view.node_items[2].fill_color.name(), "#f1eedd")
        self.assertEqual(window.view.node_items[2].text_color.name(), "#000000")
        edge = window.view.edge_items[0]
        self.assertTrue(edge.intercircuit)
        self.assertEqual(edge.stroke_color.name().upper(), INTERCIRCUIT_COLOR)
        self.assertEqual(edge.stroke_width, 4.0)
        self.assertTrue(edge.label_item.highlighted)
        self.assertEqual(
            edge.label_item.background_color.name().upper(),
            INTERCIRCUIT_COLOR,
        )
        self.assertEqual(edge.label_item.text_color.name(), "#000000")
        self.assertIn("Interligação: C1 ↔ C2", edge.toolTip())

        window.set_circuit_styles(
            {1: 0, 2: 0},
            ("#112233",),
            ("C1",),
        )

        self.assertFalse(edge.intercircuit)
        self.assertFalse(edge.label_item.highlighted)

    def test_unresolved_block_uses_the_neutral_color(self) -> None:
        window, _ = self._window()

        window.set_circuit_styles({1: None, 2: None}, (), ())

        self.assertTrue(
            all(
                item.fill_color.name().upper() == DEFAULT_NODE_COLOR
                for item in window.view.node_items.values()
            )
        )

    def test_edge_labels_do_not_intersect_node_circles_at_maximum_size(self) -> None:
        window, _ = self._window(scale=True)

        node_bounds = tuple(
            item.mapToScene(item.shape()).boundingRect()
            for item in window.view.node_items.values()
        )
        for edge in window.view.edge_items:
            label_bounds = edge.label_item.sceneBoundingRect()
            self.assertFalse(any(label_bounds.intersects(node) for node in node_bounds))

    def test_clicking_a_node_emits_its_id_and_selects_it(self) -> None:
        window, _ = self._window()
        received: list[int] = []
        window.blockRequested.connect(received.append)
        window.show()
        self.app.processEvents()
        window.view.fit_to_content()
        position = window.view.mapFromScene(window.view.node_items[2].scenePos())

        QTest.mouseClick(
            window.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=position,
        )
        self.app.processEvents()

        self.assertEqual(received, [2])
        self.assertEqual(window.view.selected_block_id, 2)
        self.assertTrue(window.view.node_items[2].selected)

    def test_power_setting_rebuilds_nodes_and_preserves_selection(self) -> None:
        window, result = self._window()
        largest = max(
            result.records,
            key=lambda record: float(record.total_power or 0.0),
        )
        window.select_block(largest)

        window.set_scale_nodes_by_power(True)

        self.assertEqual(
            window.view.node_items[largest.block_id].diameter,
            MAX_NODE_DIAMETER,
        )
        self.assertEqual(window.view.selected_block_id, largest.block_id)
        self.assertNotEqual(
            {
                item.diameter
                for item in window.view.node_items.values()
            },
            {FIXED_NODE_DIAMETER},
        )

    def test_checkbox_changes_scaling_and_emits_the_preference(self) -> None:
        window, result = self._window()
        received: list[bool] = []
        window.scaleNodesByPowerChanged.connect(received.append)
        largest = max(
            result.records,
            key=lambda record: float(record.total_power or 0.0),
        )

        window.scale_by_power_checkbox.setChecked(True)

        self.assertEqual(received, [True])
        self.assertEqual(
            window.view.node_items[largest.block_id].diameter,
            MAX_NODE_DIAMETER,
        )

    def test_zoom_keeps_the_scene_point_under_the_cursor(self) -> None:
        window, _ = self._window()
        window.show()
        self.app.processEvents()
        window.view.fit_to_content()
        cursor = QPoint(300, 220)
        before = window.view.mapToScene(cursor)

        window.view.zoom_by_steps(2.0, cursor)

        after = window.view.mapToScene(cursor)
        self.assertAlmostEqual(after.x(), before.x(), places=6)
        self.assertAlmostEqual(after.y(), before.y(), places=6)

    def test_power_scaling_preserves_zoom_and_view_center(self) -> None:
        window, _ = self._window()
        window.show()
        self.app.processEvents()
        window.view.fit_to_content()
        cursor = QPoint(300, 220)
        window.view.zoom_by_steps(2.0, cursor)
        before_scale = window.view.transform().m11()
        before_center = window.view.mapToScene(window.view.viewport().rect().center())

        window.scale_by_power_checkbox.setChecked(True)

        after_center = window.view.mapToScene(window.view.viewport().rect().center())
        self.assertAlmostEqual(window.view.transform().m11(), before_scale, places=6)
        self.assertAlmostEqual(after_center.x(), before_center.x(), delta=1.0)
        self.assertAlmostEqual(after_center.y(), before_center.y(), delta=1.0)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BlockGraphSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = QSettings(
            str(Path(self.directory.name) / "settings.ini"),
            QSettings.Format.IniFormat,
        )

    def test_default_and_invalid_values_are_disabled(self) -> None:
        self.assertFalse(load_scale_nodes_by_power(self.settings))
        self.assertFalse(parse_scale_nodes_by_power("valor-inválido"))

    def test_the_preference_is_saved_and_reloaded(self) -> None:
        save_scale_nodes_by_power(self.settings, True)

        reloaded = QSettings(
            self.settings.fileName(),
            QSettings.Format.IniFormat,
        )

        self.assertTrue(load_scale_nodes_by_power(reloaded))
        self.assertTrue(reloaded.value(BLOCK_GRAPH_SCALE_SETTINGS_KEY, type=bool))


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BlockGraphIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = QSettings(
            str(Path(self.directory.name) / "settings.ini"),
            QSettings.Format.IniFormat,
        )

    def _window(self):  # noqa: ANN202
        result, network, switches = sample_result()
        window = MainWindow(settings=self.settings)
        self.addCleanup(window.close)
        window._model = network.bars
        window._line_model = network
        window._switch_model = switches
        window._circuit_catalog = make_catalog(network, switches)
        window._circuit_visibility = CircuitVisibilityController(
            window._circuit_catalog,
            colors=("#123456",),
        )
        window.circuit_table_model.set_source(
            window._circuit_catalog,
            window._circuit_visibility,
        )
        # O resultado já pronto deixa estes testes focados na integração das
        # janelas, sem repetir a análise coberta no núcleo.
        window.blocks_window.set_result(result)
        window.block_graph_window.set_result(result)
        window._sync_block_graph_styles()
        window._sync_branches_availability()
        return window, result

    def test_actions_live_in_the_agreed_menus(self) -> None:
        window, _ = self._window()
        tools = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Ferramentas"
        )
        settings = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Configurações"
        )

        self.assertIn(window.block_graph_action, tools.actions())
        self.assertNotIn(
            "Dimensionar nós dos blocos por potência instalada",
            [action.text() for action in settings.actions()],
        )
        self.assertTrue(window.block_graph_action.isEnabled())
        self.assertFalse(window.block_graph_window.scale_by_power_checkbox.isChecked())

    def test_the_tools_action_opens_the_graph(self) -> None:
        window, _ = self._window()

        window.block_graph_action.trigger()
        self.app.processEvents()

        self.assertTrue(window.block_graph_window.isVisible())

    def test_the_table_button_opens_the_graph(self) -> None:
        window, _ = self._window()

        window.blocks_window.graph_button.click()
        self.app.processEvents()

        self.assertTrue(window.block_graph_window.isVisible())

    def test_graph_click_selects_the_sorted_table_and_highlights_the_map(self) -> None:
        window, result = self._window()
        window.blocks_window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)

        window.block_graph_window.view._node_clicked(1)
        self.app.processEvents()

        current = window.blocks_window.table.currentIndex()
        source_index = window.blocks_window.proxy_model.mapToSource(current)
        self.assertEqual(window.block_table_model.record(source_index.row()).block_id, 1)
        self.assertEqual(window._selected_block, result.records[0])
        self.assertTrue(window.block_highlight_overlay.isVisible())

    def test_table_selection_and_clear_are_reflected_in_the_graph(self) -> None:
        window, _ = self._window()
        window.blocks_window.table.setCurrentIndex(
            window.blocks_window.proxy_model.index(1, 0)
        )
        self.app.processEvents()
        selected = window.block_graph_window.view.selected_block_id

        window.blocks_window.clear_selection()
        self.app.processEvents()

        self.assertIsNotNone(selected)
        self.assertIsNone(window.block_graph_window.view.selected_block_id)
        self.assertFalse(window.block_highlight_overlay.isVisible())

    def test_toggling_the_setting_updates_the_graph_and_persists(self) -> None:
        window, result = self._window()
        largest = max(
            result.records,
            key=lambda record: float(record.total_power or 0.0),
        )

        window.block_graph_window.scale_by_power_checkbox.setChecked(True)

        self.assertEqual(
            window.block_graph_window.view.node_items[largest.block_id].diameter,
            MAX_NODE_DIAMETER,
        )
        self.assertTrue(load_scale_nodes_by_power(self.settings))

        reloaded = MainWindow(settings=self.settings)
        self.addCleanup(reloaded.close)
        self.assertTrue(
            reloaded.block_graph_window.scale_by_power_checkbox.isChecked()
        )
        self.assertTrue(reloaded.block_graph_window.scale_nodes_by_power)

    def test_editing_a_circuit_color_updates_the_graph_node(self) -> None:
        window, _ = self._window()
        index = window.circuit_table_model.index(0, 1)

        self.assertTrue(
            window.circuit_table_model.setData(
                index,
                "#ABCDEF",
                Qt.ItemDataRole.EditRole,
            )
        )

        colors = {
            item.fill_color.name().upper()
            for item in window.block_graph_window.view.node_items.values()
        }
        self.assertEqual(colors, {"#ABCDEF"})

    def test_invalidation_clears_both_views(self) -> None:
        window, _ = self._window()

        window._invalidate_blocks()

        self.assertIsNone(window.block_table_model.result)
        self.assertIsNone(window.block_graph_window.result)
        self.assertEqual(window.block_graph_window.view.node_items, {})


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BlocksGraphButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_button_follows_result_availability(self) -> None:
        window = BlocksWindow(BlockTableModel())
        self.addCleanup(window.close)
        self.assertFalse(window.graph_button.isEnabled())

        result, _, _ = sample_result()
        window.set_result(result)

        self.assertTrue(window.graph_button.isEnabled())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
