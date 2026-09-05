from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, QPointF, QSettings, Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.block_graph import (
        FIXED_NODE_DIAMETER,
        MAX_NODE_DIAMETER,
        BlockGraphLayoutMode,
    )
    from circuit_viewer.block_graph_window import (
        BLOCK_GRAPH_SCALE_SETTINGS_KEY,
        CANVAS_BACKGROUND,
        DEFAULT_NODE_COLOR,
        INTERCIRCUIT_COLOR,
        SWITCH_CLOSED_COLOR,
        SWITCH_OPEN_COLOR,
        SWITCH_UNKNOWN_COLOR,
        BlockGraphWindow,
        load_scale_nodes_by_power,
        parse_scale_nodes_by_power,
        save_scale_nodes_by_power,
    )
    from circuit_viewer.blocks_window import BlockTableModel, BlocksWindow
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import CircuitVisibilityController, FeatureSelection
    from circuit_viewer.theme import AppTheme, build_palette

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover - ambiente mínimo sem extras de UI
    PYQT_AVAILABLE = False

from test_blocks_ui import sample_result
from test_block_analysis import make_bars, make_catalog, make_network, make_switches
from circuit_viewer.block_analysis import analyze_blocks


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
        window.set_circuit_styles(
            {record.block_id: 0 for record in result.records},
            ("#2878B5",),
            ("C1",),
        )
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

    def test_layout_selector_defaults_to_tree_and_coordinates_follow_the_map(self) -> None:
        window, _ = self._window()

        self.assertTrue(window.layout_mode_combo.isEnabled())
        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.TREE)
        self.assertEqual(window.layout_mode_combo.currentText(), "Árvore")

        window.layout_mode_combo.setCurrentIndex(
            window.layout_mode_combo.findData(
                BlockGraphLayoutMode.COORDINATES.value
            )
        )

        first = window.view.node_items[1].scenePos()
        second = window.view.node_items[2].scenePos()
        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.COORDINATES)
        self.assertAlmostEqual(first.y(), second.y())
        self.assertLess(first.x(), second.x())

    def test_changing_layout_preserves_selection_and_reframes_geometry(self) -> None:
        window, _ = self._window()
        window.select_block(2)

        window.set_layout_mode(BlockGraphLayoutMode.COORDINATES)

        self.assertEqual(window.view.selected_block_id, 2)
        self.assertTrue(window.view.node_items[2].selected)
        self.assertTrue(window.view._fit_pending)

    def test_coordinate_mode_is_disabled_without_source_geometry(self) -> None:
        result, _, _ = sample_result()
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.set_result(replace(result, source_segments=None))

        self.assertFalse(window.coordinate_layout_available)
        self.assertFalse(window.layout_mode_combo.isEnabled())
        window.set_layout_mode(BlockGraphLayoutMode.COORDINATES)
        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.TREE)

    def test_coordinate_positions_stay_stable_while_filtering_circuits(self) -> None:
        result, _, _ = sample_result()
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.set_result(result)
        window.set_circuit_styles(
            {1: 0, 2: 1},
            ("#112233", "#DDEEFF"),
            ("C1", "C2"),
        )
        window._circuit_selection_changed(frozenset({0, 1}), False)
        window.set_layout_mode(BlockGraphLayoutMode.COORDINATES)
        original = window.view.node_items[1].scenePos()

        window._circuit_selection_changed(frozenset({0}), False)

        filtered = window.view.node_items[1].scenePos()
        self.assertAlmostEqual(filtered.x(), original.x())
        self.assertAlmostEqual(filtered.y(), original.y())

    def test_layout_choice_is_not_inherited_by_a_new_window(self) -> None:
        window, _ = self._window()
        window.set_layout_mode(BlockGraphLayoutMode.COORDINATES)
        fresh = BlockGraphWindow()
        self.addCleanup(fresh.close)

        self.assertEqual(fresh.layout_mode, BlockGraphLayoutMode.TREE)
        self.assertEqual(fresh.layout_mode_combo.currentText(), "Árvore")

    def test_coordinate_choice_survives_a_new_network_in_the_same_session(self) -> None:
        window, result = self._window()
        window.set_layout_mode(BlockGraphLayoutMode.COORDINATES)

        window.set_result(None)
        window.set_result(result)
        window.set_circuit_styles(
            {record.block_id: 0 for record in result.records},
            ("#2878B5",),
            ("C1",),
        )

        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.COORDINATES)
        self.assertEqual(
            window.view.layout_mode,
            BlockGraphLayoutMode.COORDINATES,
        )

    def test_one_circuit_is_selected_automatically(self) -> None:
        window, _ = self._window()

        self.assertEqual(window.selected_circuit_indices, frozenset({0}))
        self.assertEqual(
            window.circuit_selector_button.text(),
            "Circuitos exibidos: 1/1",
        )

    def test_two_circuits_start_empty_and_checking_updates_the_graph(self) -> None:
        result, _, _ = sample_result()
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.set_result(result)
        window.set_circuit_styles(
            {1: 0, 2: 1},
            ("#112233", "#DDEEFF"),
            ("C1", "C2"),
        )

        self.assertEqual(window.selected_circuit_indices, frozenset())
        self.assertEqual(window.view.node_items, {})
        self.assertEqual(
            window.circuit_selector_button.text(),
            "Circuitos exibidos: 0/2",
        )
        self.assertIn("Selecione", window.view._empty_label.text())

        first = window.circuit_selector_popup.circuit_list.item(0)
        first.setCheckState(Qt.CheckState.Checked)
        self.app.processEvents()

        self.assertEqual(window.selected_circuit_indices, frozenset({0}))
        self.assertEqual(set(window.view.node_items), {1})

    def test_selector_is_a_popup_and_closes_with_escape(self) -> None:
        window, _ = self._window()
        window.show()
        self.app.processEvents()

        window.circuit_selector_button.click()
        self.app.processEvents()

        self.assertTrue(window.circuit_selector_popup.isVisible())
        self.assertGreater(window.circuit_selector_popup.circuit_list.count(), 0)
        first = window.circuit_selector_popup.circuit_list.item(0)
        first.setCheckState(Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.assertTrue(window.circuit_selector_popup.isVisible())

        window.circuit_selector_button.click()
        self.app.processEvents()
        self.assertFalse(window.circuit_selector_popup.isVisible())

        window.circuit_selector_button.click()
        self.app.processEvents()
        self.assertTrue(window.circuit_selector_popup.isVisible())

        QTest.keyClick(
            window.circuit_selector_popup,
            Qt.Key.Key_Escape,
        )
        self.app.processEvents()

        self.assertFalse(window.circuit_selector_popup.isVisible())

    def test_select_all_and_clear_update_the_complete_selection(self) -> None:
        result, _, _ = sample_result()
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.set_result(result)
        window.set_circuit_styles(
            {1: 0, 2: 1},
            ("#112233", "#DDEEFF"),
            ("C1", "C2"),
        )

        window.circuit_selector_popup.select_all_button.click()

        self.assertEqual(window.selected_circuit_indices, frozenset({0, 1}))
        self.assertEqual(set(window.view.node_items), {1, 2})

        window.circuit_selector_popup.clear_button.click()

        self.assertEqual(window.selected_circuit_indices, frozenset())
        self.assertEqual(window.view.node_items, {})

    def test_neighbor_action_adds_only_direct_intercircuit_neighbor(self) -> None:
        result, _, _ = sample_result()
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.set_result(result)
        window.set_circuit_styles(
            {1: 0, 2: 1},
            ("#112233", "#DDEEFF"),
            ("C1", "C2"),
        )
        window._circuit_selection_changed(frozenset({0}), False)

        window.circuit_selector_popup.include_neighbors_button.click()

        self.assertEqual(window.selected_circuit_indices, frozenset({0, 1}))
        self.assertEqual(set(window.view.node_items), {1, 2})
        self.assertEqual(len(window.view.edge_items), 1)

    def test_color_update_preserves_the_filtered_circuit_selection(self) -> None:
        result, _, _ = sample_result()
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.set_result(result)
        window.set_circuit_styles(
            {1: 0, 2: 1},
            ("#112233", "#DDEEFF"),
            ("C1", "C2"),
        )
        window._circuit_selection_changed(frozenset({1}), False)

        window.set_circuit_styles(
            {1: 0, 2: 1},
            ("#445566", "#ABCDEF"),
            ("C1", "C2"),
        )

        self.assertEqual(window.selected_circuit_indices, frozenset({1}))
        self.assertEqual(set(window.view.node_items), {2})
        self.assertEqual(
            window.view.node_items[2].fill_color.name().upper(),
            "#ABCDEF",
        )

    def test_unresolved_blocks_have_an_explicit_selector_entry(self) -> None:
        window, _ = self._window()

        window.set_circuit_styles(
            {1: 0, 2: None},
            ("#112233",),
            ("C1",),
        )

        labels = [
            window.circuit_selector_popup.circuit_list.item(row).text()
            for row in range(window.circuit_selector_popup.circuit_list.count())
        ]
        self.assertIn("Sem circuito definido", labels)
        self.assertEqual(set(window.view.node_items), {1})

        unresolved = next(
            window.circuit_selector_popup.circuit_list.item(row)
            for row in range(window.circuit_selector_popup.circuit_list.count())
            if window.circuit_selector_popup.circuit_list.item(row).text()
            == "Sem circuito definido"
        )
        unresolved.setCheckState(Qt.CheckState.Checked)

        self.assertTrue(window.include_unresolved)
        self.assertEqual(set(window.view.node_items), {1, 2})

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
        window._circuit_selection_changed(frozenset({0, 1}), False)

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
            SWITCH_CLOSED_COLOR,
        )
        self.assertEqual(
            edge.label_item.border_color.name().upper(),
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
        self.assertEqual(edge.stroke_color.name().upper(), "#555555")
        self.assertEqual(edge.label_item.background_color.name().upper(), SWITCH_CLOSED_COLOR)

    def test_only_the_switch_label_uses_open_and_closed_state_colors(self) -> None:
        bars = make_bars(4)
        network = make_network(bars, [0, 1, 2], [1, 2, 3])
        switches = make_switches(network, [(1, "1", "0")])
        result = analyze_blocks(make_catalog(network, switches), switches)
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.set_result(result)
        window.set_circuit_styles(
            {record.block_id: 0 for record in result.records},
            ("#2878B5",),
            ("C1",),
        )

        edge = window.view.edge_items[0]
        self.assertEqual(edge.label_item.background_color.name().upper(), SWITCH_OPEN_COLOR)
        self.assertEqual(edge.label_item.text_color.name(), "#000000")
        self.assertEqual(edge.stroke_color.name().upper(), "#555555")

        edge.label_item.state = ""
        self.assertEqual(
            edge.label_item.background_color.name().upper(),
            SWITCH_UNKNOWN_COLOR,
        )

    def test_unresolved_block_uses_the_neutral_color(self) -> None:
        window, _ = self._window()

        window.set_circuit_styles(
            {1: None, 2: None},
            ("#112233",),
            ("C1",),
        )
        window._circuit_selection_changed(frozenset(), True)

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

    def test_zoom_limit_is_absolute_even_after_a_very_small_fit(self) -> None:
        window, _ = self._window()
        cursor = QPoint(300, 220)
        window.view.resetTransform()
        window.view.scale(1.0e-5, 1.0e-5)

        window.view.zoom_by_steps(200.0, cursor)

        self.assertAlmostEqual(
            window.view.transform().m11(),
            window.view.MAX_ZOOM_SCALE,
            places=6,
        )

    def test_edge_line_and_label_are_clickable(self) -> None:
        window, _ = self._window()
        received: list[int] = []
        window.switchRequested.connect(received.append)
        window.show()
        self.app.processEvents()
        window.view.fit_to_content()
        edge = window.view.edge_items[0]
        line_position = window.view.mapFromScene(
            edge.mapToScene(edge.path().pointAtPercent(0.20))
        )
        label_position = window.view.mapFromScene(
            edge.label_item.mapToScene(QPointF())
        )

        QTest.mouseClick(
            window.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=line_position,
        )
        QTest.mouseClick(
            window.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=label_position,
        )
        self.app.processEvents()

        self.assertEqual(
            received,
            [edge.edge.switch_index, edge.edge.switch_index],
        )
        self.assertEqual(
            window.view.selected_switch_index,
            edge.edge.switch_index,
        )
        self.assertTrue(edge.selected)

    def test_double_click_activates_nodes_switches_and_resets_on_empty(self) -> None:
        window, _ = self._window()
        blocks: list[int] = []
        switches: list[int] = []
        resets: list[bool] = []
        window.blockActivated.connect(blocks.append)
        window.switchActivated.connect(switches.append)
        window.resetRequested.connect(lambda: resets.append(True))
        window.show()
        self.app.processEvents()
        window.view.fit_to_content()

        node_position = window.view.mapFromScene(
            window.view.node_items[1].scenePos()
        )
        QTest.mouseDClick(
            window.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=node_position,
        )
        edge = window.view.edge_items[0]
        edge_position = window.view.mapFromScene(
            edge.label_item.mapToScene(QPointF())
        )
        QTest.mouseDClick(
            window.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=edge_position,
        )
        QTest.mouseDClick(
            window.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=QPoint(3, 3),
        )
        self.app.processEvents()

        self.assertEqual(blocks, [1])
        self.assertEqual(switches, [edge.edge.switch_index])
        self.assertEqual(resets, [True])

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
        self.addCleanup(window._circuit_visibility_timer.stop)
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

    def test_graph_node_activation_focuses_the_block_on_the_main_map(self) -> None:
        window, result = self._window()
        focused: list[tuple[int, ...]] = []
        window.view.focus_segments = lambda values: focused.append(
            tuple(int(value) for value in values)
        )

        window.block_graph_window.blockActivated.emit(1)

        self.assertEqual(focused, [tuple(result.records[0].segment_indices)])
        self.assertEqual(window._selected_block, result.records[0])

    def test_graph_switch_selection_opens_details_and_activation_focuses(self) -> None:
        window, _ = self._window()
        switch_index = 0
        segment_index = int(window._switch_model.segment_indices[switch_index])
        focused: list[int] = []
        window.view.focus_segment = lambda value: focused.append(int(value))

        window.block_graph_window.switchRequested.emit(switch_index)

        self.assertEqual(
            window._selected_feature,
            FeatureSelection("segment", segment_index),
        )
        self.assertEqual(window.details_dock.windowTitle(), "Chave selecionada")
        self.assertEqual(
            window.block_graph_window.view.selected_switch_index,
            switch_index,
        )

        window.block_graph_window.switchActivated.emit(switch_index)

        self.assertEqual(focused, [segment_index])

    def test_double_click_reset_clears_state_and_fits_the_main_map(self) -> None:
        window, _ = self._window()
        fitted: list[bool] = []
        window._fit_all = lambda: fitted.append(True)
        window.block_graph_window.switchRequested.emit(0)

        window.block_graph_window.resetRequested.emit()

        self.assertIsNone(window._selected_feature)
        self.assertIsNone(window._selected_block)
        self.assertIsNone(window.block_graph_window.view.selected_switch_index)
        self.assertEqual(fitted, [True])

    def test_filtering_out_the_selected_block_clears_the_map_highlight(self) -> None:
        window, _ = self._window()
        window.block_graph_window.view._node_clicked(1)
        self.assertTrue(window.block_highlight_overlay.isVisible())

        window.block_graph_window._circuit_selection_changed(
            frozenset(),
            False,
        )

        self.assertIsNone(window._selected_block)
        self.assertFalse(window.block_highlight_overlay.isVisible())
        self.assertIsNone(window.block_graph_window.view.selected_block_id)

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
