from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, QPointF, QRectF, QSettings, Qt
    from PyQt6.QtGui import QPalette, QTransform
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDialog

    from circuit_viewer.block_graph import (
        FIXED_NODE_DIAMETER,
        MAX_NODE_DIAMETER,
        BlockGraph,
        BlockGraphLayoutMode,
    )
    from circuit_viewer.block_graph_window import (
        BLOCK_GRAPH_SCALE_SETTINGS_KEY,
        CANVAS_BACKGROUND,
        DEFAULT_NODE_COLOR,
        INTERCIRCUIT_COLOR,
        NODE_POWER_MIN_PROJECTED_DIAMETER,
        NODE_TEXT_MIN_PROJECTED_DIAMETER,
        SWITCH_CLOSED_COLOR,
        SWITCH_LABEL_TEXT_MIN_PROJECTED_HEIGHT,
        SWITCH_OPEN_COLOR,
        SWITCH_UNKNOWN_COLOR,
        BlockGraphWindow,
        load_scale_nodes_by_power,
        parse_scale_nodes_by_power,
        save_scale_nodes_by_power,
    )
    from circuit_viewer.graphviz_layout import (
        DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS,
        GRAPHVIZ_CACHE_SIZE,
        GraphvizEdgeRouting,
        GraphvizLayoutSettings,
    )
    from circuit_viewer.graphviz_settings_dialog import (
        GRAPHVIZ_SETTINGS_PREFIX,
        GraphvizSettingsDialog,
        load_graphviz_layout_settings,
        save_graphviz_layout_settings,
    )
    from circuit_viewer.blocks_window import BlockTableModel, BlocksWindow
    from circuit_viewer.display_identity import BlockDisplayIdentity
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

    @staticmethod
    def _recording_painter(scale: float):  # noqa: ANN205
        class RecordingPainter:
            def __init__(self) -> None:
                self.drawn_texts: list[str] = []
                self.rounded_rects = []
                self._font = QApplication.font()
                self._transform = QTransform.fromScale(scale, scale)

            def worldTransform(self):  # noqa: ANN201, N802
                return self._transform

            def font(self):  # noqa: ANN201
                return self._font

            def setFont(self, font) -> None:  # noqa: ANN001, N802
                self._font = font

            def drawText(self, *arguments) -> None:  # noqa: ANN002, N802
                self.drawn_texts.append(str(arguments[-1]))

            def drawRoundedRect(self, rect, *_arguments) -> None:  # noqa: ANN001, ANN002, N802
                self.rounded_rects.append(QRectF(rect))

            def setRenderHint(self, *_arguments) -> None:  # noqa: ANN002, N802
                pass

            def setBrush(self, *_arguments) -> None:  # noqa: ANN002, N802
                pass

            def setPen(self, *_arguments) -> None:  # noqa: ANN002, N802
                pass

            def drawEllipse(self, *_arguments) -> None:  # noqa: ANN002, N802
                pass

        return RecordingPainter()

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
            self.assertIn(f"Bloco B{record.block_id}", item.toolTip())
            self.assertIn("kVA", item.toolTip())

    def test_view_uses_layout_routes_and_global_label_anchors(self) -> None:
        window, _ = self._window()
        edge_item = window.view.edge_items[0]
        switch_index = edge_item.edge.switch_index
        route = window.view.layout_result.edge_routes[switch_index]
        label_anchor = window.view.layout_result.edge_label_positions[switch_index]
        first = edge_item.path().elementAt(0)

        self.assertAlmostEqual(first.x, route.points[0][0])
        self.assertAlmostEqual(first.y, route.points[0][1])
        self.assertNotEqual(
            route.points[0],
            window.view.layout_result.positions[edge_item.edge.start_block_id],
        )
        self.assertAlmostEqual(edge_item.label_item.pos().x(), label_anchor[0])
        self.assertAlmostEqual(edge_item.label_item.pos().y(), label_anchor[1])

    def test_node_level_of_detail_follows_projected_diameter(self) -> None:
        window, _ = self._window()
        item = window.view.node_items[1]
        original_bounds = QRectF(item.boundingRect())
        original_shape = item.shape().boundingRect()

        hidden = self._recording_painter(
            (NODE_TEXT_MIN_PROJECTED_DIAMETER - 1.0) / item.diameter
        )
        item.paint(hidden, None)
        self.assertEqual(hidden.drawn_texts, [])

        identity = self._recording_painter(
            (
                NODE_TEXT_MIN_PROJECTED_DIAMETER
                + NODE_POWER_MIN_PROJECTED_DIAMETER
            )
            / 2.0
            / item.diameter
        )
        item.paint(identity, None)
        self.assertEqual(identity.drawn_texts, [item.display_label])

        complete = self._recording_painter(
            NODE_POWER_MIN_PROJECTED_DIAMETER / item.diameter
        )
        item.paint(complete, None)
        self.assertEqual(
            complete.drawn_texts,
            [item.display_label, f"{float(item.record.total_power):n} kVA"],
        )
        self.assertEqual(item.boundingRect(), original_bounds)
        self.assertEqual(item.shape().boundingRect(), original_shape)

    def test_selected_or_hovered_node_forces_complete_details(self) -> None:
        window, _ = self._window()
        item = window.view.node_items[1]
        tiny_scale = 1.0 / item.diameter

        item.set_selected(True)
        selected = self._recording_painter(tiny_scale)
        item.paint(selected, None)
        self.assertEqual(len(selected.drawn_texts), 2)

        item.set_selected(False)
        item._set_hovered(True)
        hovered = self._recording_painter(tiny_scale)
        item.paint(hovered, None)
        self.assertEqual(len(hovered.drawn_texts), 2)
        item._set_hovered(False)

    def test_switch_label_compacts_without_changing_hit_geometry(self) -> None:
        window, _ = self._window()
        edge = window.view.edge_items[0]
        label = edge.label_item
        original_bounds = QRectF(label.boundingRect())
        original_shape = edge.shape().boundingRect()
        compact_scale = (
            SWITCH_LABEL_TEXT_MIN_PROJECTED_HEIGHT - 1.0
        ) / label.boundingRect().height()

        compact = self._recording_painter(compact_scale)
        label.paint(compact, None)
        self.assertEqual(compact.drawn_texts, [])
        self.assertEqual(compact.rounded_rects, [label.marker_rect])

        edge.set_selected(True)
        selected = self._recording_painter(compact_scale)
        label.paint(selected, None)
        self.assertEqual(selected.drawn_texts, [edge.edge.label])
        self.assertEqual(label.boundingRect(), original_bounds)
        self.assertEqual(edge.shape().boundingRect(), original_shape)

        edge.set_selected(False)
        edge._set_hovered(True)
        hovered = self._recording_painter(compact_scale)
        label.paint(hovered, None)
        self.assertEqual(hovered.drawn_texts, [edge.edge.label])
        edge._set_hovered(False)

        label._set_hovered(True)
        hovered_label = self._recording_painter(compact_scale)
        label.paint(hovered_label, None)
        self.assertEqual(hovered_label.drawn_texts, [edge.edge.label])
        label._set_hovered(False)

    def test_nodes_and_edge_tooltips_use_the_composite_block_identity(self) -> None:
        window, result = self._window()
        identities = {
            record.block_id: BlockDisplayIdentity(
                0,
                "001001",
                position,
                f"001001-{position}",
            )
            for position, record in enumerate(result.records, start=1)
        }

        window.set_circuit_styles(
            {record.block_id: 0 for record in result.records},
            ("#2878B5",),
            ("001001",),
            identities,
        )

        self.assertEqual(window.view.node_items[1].display_label, "001001-1")
        self.assertIn("Bloco 001001-2", window.view.node_items[2].toolTip())
        self.assertIn(
            "Blocos: 001001-1 ↔ 001001-2",
            window.view.edge_items[0].toolTip(),
        )

    def test_layout_selector_defaults_to_tree_and_coordinates_follow_the_map(self) -> None:
        window, _ = self._window()

        self.assertTrue(window.layout_mode_combo.isEnabled())
        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.TREE)
        self.assertEqual(
            window.layout_mode_combo.currentText(),
            "Árvore — Interno",
        )
        self.assertEqual(window.layout_mode_combo.count(), 3)

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
        self.assertTrue(window.layout_mode_combo.isEnabled())
        coordinate_index = window.layout_mode_combo.findData(
            BlockGraphLayoutMode.COORDINATES.value
        )
        self.assertFalse(
            window.layout_mode_combo.model().item(coordinate_index).isEnabled()
        )
        window.set_layout_mode(BlockGraphLayoutMode.COORDINATES)
        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.TREE)

    def test_coordinate_layout_is_recomputed_for_the_visible_selection(self) -> None:
        bars = make_bars(5)
        network = make_network(bars, [0, 1, 2, 3], [1, 2, 3, 4])
        switches = make_switches(network, [(1, "1", "1"), (3, "1", "1")])
        result = analyze_blocks(make_catalog(network, switches), switches)
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.set_result(result)
        window.set_circuit_styles(
            {1: 0, 2: 1, 3: 1},
            ("#112233", "#DDEEFF"),
            ("C1", "C2"),
        )
        window._circuit_selection_changed(frozenset({0, 1}), False)
        window.set_layout_mode(BlockGraphLayoutMode.COORDINATES)
        original = window.view.node_items[1].scenePos()

        window._circuit_selection_changed(frozenset({0}), False)

        filtered = window.view.node_items[1].scenePos()
        self.assertEqual(set(window.view.node_items), {1, 2})
        self.assertTrue(
            abs(filtered.x() - original.x()) > 1.0e-6
            or abs(filtered.y() - original.y()) > 1.0e-6
        )

        first_pass = {
            block_id: (item.scenePos().x(), item.scenePos().y())
            for block_id, item in window.view.node_items.items()
        }
        window._circuit_selection_changed(frozenset({0, 1}), False)
        window._circuit_selection_changed(frozenset({0}), False)
        second_pass = {
            block_id: (item.scenePos().x(), item.scenePos().y())
            for block_id, item in window.view.node_items.items()
        }
        self.assertEqual(first_pass, second_pass)

    def test_layout_choice_is_not_inherited_by_a_new_window(self) -> None:
        window, _ = self._window()
        window.set_layout_mode(BlockGraphLayoutMode.COORDINATES)
        fresh = BlockGraphWindow()
        self.addCleanup(fresh.close)

        self.assertEqual(fresh.layout_mode, BlockGraphLayoutMode.TREE)
        self.assertEqual(
            fresh.layout_mode_combo.currentText(),
            "Árvore — Interno",
        )

    def test_graphviz_dot_is_an_experimental_geometry_only_mode(self) -> None:
        window, _ = self._window()
        graphviz_index = window.layout_mode_combo.findData(
            BlockGraphLayoutMode.GRAPHVIZ_DOT.value
        )

        self.assertGreaterEqual(graphviz_index, 0)
        self.assertEqual(
            window.layout_mode_combo.itemText(graphviz_index),
            "Árvore — Graphviz dot (experimental)",
        )
        self.assertEqual(
            window.layout_mode_combo.model().item(graphviz_index).isEnabled(),
            window.graphviz_layout_available,
        )
        if not window.graphviz_layout_available:
            self.assertTrue(
                window.layout_mode_combo.model().item(graphviz_index).toolTip()
            )
            return

        window.select_block(2)
        window.set_layout_mode(BlockGraphLayoutMode.GRAPHVIZ_DOT)
        for _attempt in range(200):
            self.app.processEvents()
            if window.view.node_items:
                break
            QTest.qWait(10)

        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.GRAPHVIZ_DOT)
        self.assertEqual(
            window.view.layout_mode,
            BlockGraphLayoutMode.GRAPHVIZ_DOT,
        )
        self.assertEqual(window.view.selected_block_id, 2)
        self.assertTrue(window.view.layout_result.edge_routes[0].cubic)
        self.assertGreaterEqual(len(window._graphviz_cache), 1)

    def test_graphviz_settings_button_follows_runtime_availability(self) -> None:
        window, _ = self._window()

        self.assertEqual(
            window.graphviz_settings_button.isEnabled(),
            window.graphviz_layout_available,
        )
        if window.graphviz_layout_available:
            self.assertIn(
                "espaçamentos",
                window.graphviz_settings_button.toolTip(),
            )
        else:
            self.assertEqual(
                window.graphviz_settings_button.toolTip(),
                window._graphviz_runtime.reason,
            )

    def test_settings_only_recalculate_while_graphviz_mode_is_active(self) -> None:
        window, _ = self._window()
        custom = GraphvizLayoutSettings(node_separation_px=80.0)
        refresh = Mock()
        window._refresh_filtered_graph = refresh

        window.set_graphviz_layout_settings(custom)

        self.assertEqual(window.graphviz_layout_settings, custom)
        refresh.assert_not_called()

        window.layout_mode = BlockGraphLayoutMode.GRAPHVIZ_DOT
        updated = GraphvizLayoutSettings(node_separation_px=96.0)
        window.set_graphviz_layout_settings(updated)

        refresh.assert_called_once_with()

    def test_dialog_application_updates_window_and_emits_once(self) -> None:
        window, _ = self._window()
        emitted: list[GraphvizLayoutSettings] = []
        window.graphvizLayoutSettingsChanged.connect(emitted.append)
        custom = GraphvizLayoutSettings(rank_separation_px=90.0)

        window._graphviz_settings_applied(custom)
        window._graphviz_settings_applied(custom)

        self.assertEqual(window.graphviz_layout_settings, custom)
        self.assertEqual(emitted, [custom])

    def test_obsolete_graphviz_result_is_ignored(self) -> None:
        window, _ = self._window()
        original_layout = window.view.layout_result
        window._graphviz_generation = 9
        window._graphviz_jobs[8] = (
            None,
            None,
            threading.Event(),
            window.view.graph,
            False,
            "obsolete-key",
        )

        window._graphviz_layout_finished(8, original_layout, None)

        self.assertIs(window.view.layout_result, original_layout)
        self.assertNotIn("obsolete-key", window._graphviz_cache)

    def test_graphviz_choice_survives_network_replacement_in_the_session(self) -> None:
        window, result = self._window()
        if not window.graphviz_layout_available:
            self.skipTest(window._graphviz_runtime.reason)
        window.set_layout_mode(BlockGraphLayoutMode.GRAPHVIZ_DOT)
        window.set_result(None)

        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.GRAPHVIZ_DOT)

        window.set_result(result)
        window.set_circuit_styles(
            {record.block_id: 0 for record in result.records},
            ("#2878B5",),
            ("C1",),
        )
        for _attempt in range(200):
            self.app.processEvents()
            if window.view.node_items:
                break
            QTest.qWait(10)

        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.GRAPHVIZ_DOT)
        self.assertEqual(set(window.view.node_items), {1, 2})

    def test_graphviz_failure_falls_back_with_only_one_session_warning(self) -> None:
        window, _ = self._window()
        window.layout_mode = BlockGraphLayoutMode.GRAPHVIZ_DOT

        window._fallback_from_graphviz(window.view.graph, "primeiro erro")
        window._show_graphviz_warning("segundo erro")

        self.assertEqual(window.layout_mode, BlockGraphLayoutMode.TREE)
        self.assertTrue(window.graphviz_status_label.isVisibleTo(window))
        self.assertEqual(window.graphviz_status_label.toolTip(), "primeiro erro")

    def test_graphviz_cache_keeps_only_the_eight_most_recent_results(self) -> None:
        window, _ = self._window()
        graph = window.view.graph
        layout = window.view.layout_result
        for generation in range(GRAPHVIZ_CACHE_SIZE + 2):
            cache_key = f"cache-{generation}"
            window._graphviz_generation = generation
            window._graphviz_jobs[generation] = (
                None,
                None,
                threading.Event(),
                graph,
                True,
                cache_key,
            )
            window._graphviz_layout_finished(generation, layout, None)

        self.assertEqual(len(window._graphviz_cache), GRAPHVIZ_CACHE_SIZE)
        self.assertNotIn("cache-0", window._graphviz_cache)
        self.assertNotIn("cache-1", window._graphviz_cache)
        self.assertIn(f"cache-{GRAPHVIZ_CACHE_SIZE + 1}", window._graphviz_cache)
        window._graphviz_jobs.clear()

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

    def test_two_circuits_start_empty_and_single_selection_adds_boundary_neighbor(self) -> None:
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
        self.assertEqual(set(window.view.node_items), {1, 2})
        self.assertEqual(len(window.view.edge_items), 1)

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
        self.assertEqual(set(window.view.node_items), {1, 2})
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

    def test_edge_labels_do_not_intersect_complete_node_envelopes(self) -> None:
        window, _ = self._window(scale=True)

        node_bounds = tuple(
            item.sceneBoundingRect()
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

    def test_fit_recalculates_layout_after_a_relevant_aspect_change(self) -> None:
        result, _, _ = sample_result()
        template = result.records[0]
        records = tuple(
            replace(
                template,
                block_id=block_id,
                contains_source=block_id == 1,
            )
            for block_id in range(1, 10)
        )
        graph = BlockGraph(records, ())
        window = BlockGraphWindow()
        self.addCleanup(window.close)
        window.resize(480, 900)
        window.show()
        self.app.processEvents()
        window.view.set_circuit_styles(
            {block_id: block_id - 1 for block_id in graph.node_ids},
            tuple("#2878B5" for _ in graph.node_ids),
            tuple(f"C{block_id}" for block_id in graph.node_ids),
        )
        window.view.set_graph(
            graph,
            scale_by_power=False,
            selected_circuit_indices=frozenset(range(len(graph.nodes))),
        )
        window.view.fit_to_content()
        portrait_aspect = window.view._layout_aspect_ratio
        portrait_positions = dict(window.view.layout_result.positions)

        window.resize(1_200, 420)
        self.app.processEvents()
        window.view.fit_to_content()
        self.app.processEvents()

        landscape_aspect = window.view._layout_aspect_ratio
        self.assertGreater(landscape_aspect, portrait_aspect * 1.5)
        self.assertNotEqual(
            window.view.layout_result.positions,
            portrait_positions,
        )
        self.assertFalse(window.view._fit_pending)
        projected = window.view.transform().mapRect(window.view._content_rect)
        self.assertLessEqual(projected.width(), window.view.viewport().width())
        self.assertLessEqual(projected.height(), window.view.viewport().height())

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

    def test_power_scaling_preserves_manual_camera_and_selection(self) -> None:
        window, _ = self._window()
        window.show()
        self.app.processEvents()
        window.view.fit_to_content()
        window.select_block(2)
        cursor = QPoint(300, 220)
        window.view.zoom_by_steps(2.0, cursor)
        before_transform = window.view.transform()
        before_center = window.view.mapToScene(window.view.viewport().rect().center())

        window.scale_by_power_checkbox.setChecked(True)

        after_center = window.view.mapToScene(window.view.viewport().rect().center())
        self.assertEqual(window.view.transform(), before_transform)
        self.assertAlmostEqual(after_center.x(), before_center.x(), delta=1.0)
        self.assertAlmostEqual(after_center.y(), before_center.y(), delta=1.0)
        self.assertTrue(window.view._camera_modified)
        self.assertFalse(window.view._fit_pending)
        self.assertEqual(window.view.selected_block_id, 2)


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

    def test_graphviz_settings_are_saved_and_reloaded(self) -> None:
        expected = GraphvizLayoutSettings(
            node_separation_px=48.0,
            rank_separation_px=112.0,
            edge_routing=GraphvizEdgeRouting.LINE,
            equal_rank_spacing=True,
            tree_edge_weight=16,
            tree_edge_minlen=2,
            crossing_minimization=2.4,
        )

        save_graphviz_layout_settings(self.settings, expected)
        reloaded = QSettings(
            self.settings.fileName(),
            QSettings.Format.IniFormat,
        )

        self.assertEqual(load_graphviz_layout_settings(reloaded), expected)
        self.assertEqual(
            reloaded.value(f"{GRAPHVIZ_SETTINGS_PREFIX}edge_routing"),
            "line",
        )

    def test_invalid_graphviz_preferences_fall_back_field_by_field(self) -> None:
        self.settings.setValue(
            f"{GRAPHVIZ_SETTINGS_PREFIX}node_separation_px",
            "inválido",
        )
        self.settings.setValue(
            f"{GRAPHVIZ_SETTINGS_PREFIX}rank_separation_px",
            140.0,
        )
        self.settings.setValue(
            f"{GRAPHVIZ_SETTINGS_PREFIX}edge_routing",
            "ortho",
        )

        loaded = load_graphviz_layout_settings(self.settings)

        self.assertEqual(loaded.node_separation_px, 32.0)
        self.assertEqual(loaded.rank_separation_px, 140.0)
        self.assertEqual(loaded.edge_routing, GraphvizEdgeRouting.SPLINE)

    def test_graphviz_dialog_applies_and_restores_without_implicit_run(self) -> None:
        initial = GraphvizLayoutSettings(
            node_separation_px=75.0,
            rank_separation_px=125.0,
            edge_routing=GraphvizEdgeRouting.POLYLINE,
            equal_rank_spacing=True,
            tree_edge_weight=14,
            tree_edge_minlen=2,
            crossing_minimization=2.0,
        )
        dialog = GraphvizSettingsDialog(initial)
        self.addCleanup(dialog.close)
        applied: list[GraphvizLayoutSettings] = []
        dialog.settingsApplied.connect(applied.append)

        self.assertFalse(dialog.advanced_panel.isVisible())
        dialog.advanced_button.setChecked(True)
        self.assertFalse(dialog.advanced_panel.isHidden())
        dialog.restore_defaults()

        self.assertEqual(dialog.settings(), DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS)
        self.assertEqual(dialog.applied_settings, initial)
        self.assertEqual(applied, [])

        dialog.apply_settings()
        dialog.apply_settings()

        self.assertEqual(
            dialog.applied_settings,
            DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS,
        )
        self.assertEqual(applied, [DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS])

    def test_graphviz_dialog_cancel_discards_unapplied_values(self) -> None:
        dialog = GraphvizSettingsDialog(DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS)
        self.addCleanup(dialog.close)
        applied: list[GraphvizLayoutSettings] = []
        dialog.settingsApplied.connect(applied.append)
        dialog.node_separation_input.setValue(222.0)

        dialog.reject()

        self.assertEqual(dialog.applied_settings, DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS)
        self.assertEqual(applied, [])

    def test_graphviz_dialog_ok_applies_and_accepts(self) -> None:
        dialog = GraphvizSettingsDialog(DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS)
        self.addCleanup(dialog.close)
        applied: list[GraphvizLayoutSettings] = []
        dialog.settingsApplied.connect(applied.append)
        dialog.rank_separation_input.setValue(88.0)

        dialog.accept_settings()

        self.assertEqual(dialog.result(), int(QDialog.DialogCode.Accepted))
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].rank_separation_px, 88.0)


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

    def test_main_window_shares_code_based_block_identities(self) -> None:
        window, _ = self._window()

        self.assertEqual(
            window.block_graph_window.view.node_items[1].display_label,
            "ALIM-1-1",
        )
        circuit_column = window.block_table_model.HEADERS.index("CIRCUITO")
        block_column = window.block_table_model.HEADERS.index("BLOCO")
        self.assertEqual(
            window.block_table_model.data(
                window.block_table_model.index(0, circuit_column)
            ),
            "ALIM-1",
        )
        self.assertEqual(
            window.block_table_model.data(
                window.block_table_model.index(1, block_column)
            ),
            "2",
        )

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

    def test_graphviz_fine_tuning_is_persisted_by_main_window(self) -> None:
        window, _ = self._window()
        expected = GraphvizLayoutSettings(
            node_separation_px=64.0,
            rank_separation_px=108.0,
            edge_routing=GraphvizEdgeRouting.POLYLINE,
            equal_rank_spacing=True,
            tree_edge_weight=10,
            tree_edge_minlen=2,
            crossing_minimization=1.8,
        )

        window.block_graph_window._graphviz_settings_applied(expected)

        self.assertEqual(load_graphviz_layout_settings(self.settings), expected)
        reloaded = MainWindow(settings=self.settings)
        self.addCleanup(reloaded.close)
        self.assertEqual(reloaded._graphviz_layout_settings, expected)
        self.assertEqual(
            reloaded.block_graph_window.graphviz_layout_settings,
            expected,
        )

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
