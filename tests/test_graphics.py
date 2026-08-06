from __future__ import annotations

import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter
    from PyQt6.QtWidgets import QGraphicsItem
    from PyQt6.QtWidgets import QApplication, QGraphicsScene

    from circuit_viewer.graphics import (
        BranchHighlightOverlayItem,
        MAX_ACTIVE_ITEMS,
        DiagramView,
        ItemVirtualizer,
        LineNetworkItem,
        LOAD_HEIGHT_PX,
        LOAD_WIDTH_PX,
        LoadItem,
        LoadVirtualizer,
        NORMAL_SEGMENT_WIDTH_PX,
        REGULATOR_COLOR,
        REGULATOR_DIAMETER_PX,
        RegulatorNetworkItem,
        SEGMENT_SELECTION_WIDTH_PX,
        SegmentSelectionOverlayItem,
        SWITCH_COLOR,
        SWITCH_SEGMENT_WIDTH_PX,
        SwitchNetworkItem,
    )

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False

from circuit_viewer.model import (
    CircuitModel,
    FeatureSelection,
    LineNetworkModel,
    LoadModel,
    RegulatorModel,
    SwitchModel,
    UtmCrs,
)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class GraphicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_canvas(self, count: int, cap: int):
        model = CircuitModel(
            [f"B{i}" for i in range(count)],
            [f"C{i}" for i in range(count)],
            [500_000.0 + i for i in range(count)],
            [8_000_000.0 + (i % 10) for i in range(count)],
            UtmCrs(21, northern=False),
        )
        scene = QGraphicsScene()
        scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        view = DiagramView(scene)
        view.resize(800, 600)
        view.show()
        virtualizer = ItemVirtualizer(scene, view, max_active_items=cap)
        view.set_model(model)
        virtualizer.reset_model(model)
        view.fit_model()
        virtualizer.refresh(force=True)
        for _ in range(100):
            self.app.processEvents()
            if not virtualizer._pending_indices:  # confirma o fim dos lotes
                break
        return model, scene, view, virtualizer

    def _make_load_canvas(self, load_ids: list[str], cap: int = 1000):
        bars = CircuitModel(
            ["B1", "B2"],
            ["", ""],
            [0.0, 100.0],
            [0.0, 0.0],
            UtmCrs(21, northern=False),
        )
        size = len(load_ids)
        loads = LoadModel(
            bars,
            load_ids,
            [0] * size,
            [""] * size,
            [f"C{i}" for i in range(size)],
            [""] * size,
            [""] * size,
            [""] * size,
            [""] * size,
            [""] * size,
        )
        scene = QGraphicsScene()
        scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        view = DiagramView(scene)
        view.resize(800, 600)
        view.show()
        bars_layer = ItemVirtualizer(scene, view, max_active_items=1000)
        loads_layer = LoadVirtualizer(scene, view, max_active_items=cap)
        view.set_load_layer(loads_layer)
        view.set_model(bars)
        view.set_load_model(loads)
        bars_layer.reset_model(bars)
        loads_layer.reset_model(loads)
        view.fit_model()
        bars_layer.refresh(force=True)
        loads_layer.refresh(force=True)
        for _ in range(100):
            self.app.processEvents()
            if not loads_layer._pending_indices:
                break
        return bars, loads, scene, view, bars_layer, loads_layer

    def test_overview_does_not_materialize_above_cap(self) -> None:
        _, _, view, virtualizer = self._make_canvas(100, 10)
        self.addCleanup(view.close)

        self.assertEqual(virtualizer.mode, "Visão geral")
        self.assertEqual(virtualizer.active_count, 0)
        self.assertTrue(virtualizer.overview_item.isVisible())
        self.assertEqual(
            virtualizer.overview_item.cacheMode(),
            QGraphicsItem.CacheMode.DeviceCoordinateCache,
        )

    def test_medium_dataset_uses_fast_overview(self) -> None:
        _, _, view, virtualizer = self._make_canvas(16_159, MAX_ACTIVE_ITEMS)
        self.addCleanup(view.close)

        self.assertEqual(virtualizer.mode, "Visão geral")
        self.assertEqual(virtualizer.active_count, 0)
        self.assertTrue(virtualizer.overview_item.isVisible())

    def test_bar_visibility_hides_and_restores_overview(self) -> None:
        _, _, view, virtualizer = self._make_canvas(100, 10)
        self.addCleanup(view.close)

        virtualizer.set_bars_visible(False)
        self.assertFalse(virtualizer.bars_visible)
        self.assertFalse(virtualizer.overview_item.isVisible())
        self.assertFalse(virtualizer.selection_overlay.isVisible())

        virtualizer.set_bars_visible(True)
        self.app.processEvents()
        self.assertTrue(virtualizer.bars_visible)
        self.assertTrue(virtualizer.overview_item.isVisible())

    def test_bar_visibility_hides_and_reuses_detailed_items(self) -> None:
        _, _, view, virtualizer = self._make_canvas(20, 100)
        self.addCleanup(view.close)
        virtualizer.set_selected_index(5)
        active_ids = {index: id(item) for index, item in virtualizer._active.items()}

        virtualizer.set_bars_visible(False)
        self.assertTrue(active_ids)
        self.assertTrue(all(not item.isVisible() for item in virtualizer._active.values()))
        self.assertFalse(virtualizer.selection_overlay.isVisible())

        virtualizer.set_bars_visible(True)
        for _ in range(20):
            self.app.processEvents()
            if not virtualizer._pending_indices:
                break
        self.assertEqual(
            active_ids,
            {index: id(item) for index, item in virtualizer._active.items()},
        )
        self.assertTrue(all(item.isVisible() for item in virtualizer._active.values()))
        self.assertTrue(virtualizer._active[5].isSelected())

    def test_detail_materializes_and_keeps_selection(self) -> None:
        _, _, view, virtualizer = self._make_canvas(100, 10)
        self.addCleanup(view.close)
        virtualizer.set_selected_index(5)
        self.assertTrue(virtualizer.selection_overlay.isVisible())

        virtualizer.max_active_items = 200
        virtualizer.refresh(force=True)
        for _ in range(100):
            self.app.processEvents()
            if not virtualizer._pending_indices:
                break

        self.assertEqual(virtualizer.mode, "Detalhado")
        self.assertEqual(virtualizer.active_count, 100)
        self.assertTrue(virtualizer._active[5].isSelected())
        self.assertFalse(virtualizer.selection_overlay.isVisible())
        self.assertFalse(virtualizer.overview_item.isVisible())
        self.assertTrue(
            virtualizer._active[5].flags()
            & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )

    def test_coordinate_orientation_and_cursor_anchored_zoom(self) -> None:
        model, _, view, _ = self._make_canvas(100, 200)
        self.addCleanup(view.close)

        scene_point = QPointF(float(model.x[50]), -float(model.y[50]))
        viewport_point = view.mapFromScene(scene_point)
        x, y = view.model_point_at(viewport_point)
        units_per_pixel = 1.0 / abs(view.transform().m11())
        self.assertAlmostEqual(x, float(model.x[50]), delta=units_per_pixel)
        self.assertAlmostEqual(y, float(model.y[50]), delta=units_per_pixel)

        anchor = QPoint(400, 300)
        before = view.mapToScene(anchor)
        view.zoom_at(anchor, 1.15)
        after = view.mapToScene(anchor)
        self.assertAlmostEqual(before.x(), after.x(), places=6)
        self.assertAlmostEqual(before.y(), after.y(), places=6)

    def test_zoom_is_clamped_once_and_remains_renderable_after_returning(self) -> None:
        bars = CircuitModel(
            ["B1", "B2"],
            ["", ""],
            [500_000.0, 500_100.0],
            [8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T1"],
            [""],
            ["1"],
            [0],
            [1],
            [""],
            [""],
            [""],
            [100.0],
        )
        scene = QGraphicsScene()
        view = DiagramView(scene)
        self.addCleanup(view.close)
        view.resize(800, 600)
        view.show()
        view.set_model(bars)
        line_item = LineNetworkItem(network)
        scene.addItem(line_item)
        view.fit_model()
        self.app.processEvents()

        initial_scale = abs(view.transform().m11())
        anchor = view.mapFromScene(QPointF(500_050.0, -8_000_000.0))
        scene_before = view.mapToScene(anchor)
        limit_notifications: list[None] = []
        view.zoomLimitReached.connect(lambda: limit_notifications.append(None))

        for _ in range(500):
            view.zoom_at(anchor, 1.15)
        self.app.processEvents()

        self.assertAlmostEqual(
            abs(view.transform().m11()), view.maximum_zoom_scale, places=9
        )
        self.assertLessEqual(view.maximum_zoom_scale, 100.0)
        self.assertEqual(len(limit_notifications), 1)
        scene_at_limit = view.mapToScene(anchor)
        self.assertAlmostEqual(scene_before.x(), scene_at_limit.x(), places=6)
        self.assertAlmostEqual(scene_before.y(), scene_at_limit.y(), places=6)
        image_at_limit = view.viewport().grab().toImage()
        colors_at_limit = {
            QColor(image_at_limit.pixel(anchor.x() + dx, anchor.y() + dy)).name()
            for dx in range(-3, 4)
            for dy in range(-3, 4)
        }
        self.assertIn("#555555", colors_at_limit)

        view.zoom_at(anchor, initial_scale / view.maximum_zoom_scale)
        self.app.processEvents()
        self.assertAlmostEqual(abs(view.transform().m11()), initial_scale, places=9)
        image_after_return = view.viewport().grab().toImage()
        colors_after_return = {
            QColor(
                image_after_return.pixel(anchor.x() + dx, anchor.y() + dy)
            ).name()
            for dx in range(-3, 4)
            for dy in range(-3, 4)
        }
        self.assertIn("#555555", colors_after_return)

    def test_dynamic_zoom_limit_accounts_for_absolute_scene_coordinates(self) -> None:
        model, _, view, _ = self._make_canvas(2, 10)
        self.addCleanup(view.close)
        rect = view.sceneRect().normalized()
        largest_coordinate = max(
            abs(rect.left()),
            abs(rect.right()),
            abs(rect.top()),
            abs(rect.bottom()),
        )

        self.assertLessEqual(
            view.maximum_zoom_scale * largest_coordinate,
            ((1 << 31) - 1) * 0.5,
        )

    def test_virtualizers_do_not_reuse_loaded_region_when_zooming_out(self) -> None:
        _, _, view, virtualizer = self._make_canvas(20, 100)
        self.addCleanup(view.close)
        current = virtualizer._last_view_rect
        self.assertIsNotNone(current)
        expanded = QRectF(
            current.left(),
            current.top(),
            current.width() * 1.01,
            current.height() * 1.01,
        )

        self.assertFalse(virtualizer._can_reuse_loaded_rect(expanded))

        _, _, _, load_view, _, load_virtualizer = self._make_load_canvas(["L1"])
        self.addCleanup(load_view.close)
        load_current = load_virtualizer._last_view_rect
        self.assertIsNotNone(load_current)
        load_expanded = QRectF(
            load_current.left(),
            load_current.top(),
            load_current.width() * 1.01,
            load_current.height() * 1.01,
        )
        self.assertFalse(load_virtualizer._can_reuse_loaded_rect(load_expanded))

    def test_focus_bar_centers_with_context_and_forces_hidden_halo(self) -> None:
        model, _, view, virtualizer = self._make_canvas(100, 10)
        self.addCleanup(view.close)
        index = 50

        view.focus_bar(index)
        center = view.mapToScene(view.viewport().rect().center())
        self.assertAlmostEqual(center.x(), float(model.x[index]), delta=1.0)
        self.assertAlmostEqual(center.y(), -float(model.y[index]), delta=1.0)

        virtualizer.set_selected_index(index, reveal_hidden=True)
        virtualizer.set_bars_visible(False)
        self.assertTrue(virtualizer.selection_overlay.isVisible())
        self.assertFalse(virtualizer.bars_visible)

        virtualizer.set_selected_index(index)
        self.assertFalse(virtualizer.selection_overlay.isVisible())

    def test_focus_segment_adds_margin_and_caps_zoom(self) -> None:
        bars = CircuitModel(
            ["B1", "B2"],
            ["", ""],
            [0.0, 0.0],
            [0.0, 0.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T1"],
            [""],
            ["ABC"],
            [0],
            [1],
            [""],
            [""],
            [""],
            [0.0],
        )
        scene = QGraphicsScene()
        view = DiagramView(scene)
        self.addCleanup(view.close)
        view.resize(800, 600)
        view.show()
        view.set_model(bars)
        view.set_line_model(network)

        view.focus_segment(0)

        center = view.mapToScene(view.viewport().rect().center())
        self.assertAlmostEqual(center.x(), 0.0, delta=1.0)
        self.assertAlmostEqual(center.y(), 0.0, delta=1.0)
        self.assertLessEqual(abs(view.transform().m11()), 4.0)

    # ------------------------------------------------- anel do regulador

    def _regulator_canvas(self):  # noqa: ANN202
        """Dois trechos horizontais; só o primeiro tem regulador."""

        bars = CircuitModel(
            ["B0", "B1", "B2"],
            ["A", "B", "C"],
            [0.0, 100.0, 200.0],
            [0.0, 0.0, 0.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T0", "T1"],
            ["TR-0", "TR-1"],
            ["13", "13"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["CB1", "CB1"],
            ["", ""],
            [100.0, 100.0],
        )
        regulators = RegulatorModel(
            network,
            ["RG1"],
            [0],
            [""],
            ["X"],
            ["Y"],
            ["333"],
            ["10"],
            ["32"],
            ["0"],
            ["100"],
            ["13,8"],
        )
        return network, regulators

    def _paint_regulator(self, item, width: int = 220):  # noqa: ANN001, ANN202
        image = QImage(width, 100, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.translate(0.0, 50.0)
        item.paint(painter, None)
        painter.end()
        return image

    def test_regulator_ring_marks_only_the_segment_that_has_one(self) -> None:
        _, regulators = self._regulator_canvas()
        item = RegulatorNetworkItem(regulators)

        self.assertEqual(item.regulator_count, 1)
        self.assertEqual(item.visible_regulator_count, 1)
        image = self._paint_regulator(item)
        target = REGULATOR_COLOR.name()
        # x=50 é o meio do trecho 0; x=150 o do trecho 1, sem regulador.
        with_regulator = [
            QColor(image.pixel(50, y)).name() for y in range(35, 66)
        ]
        without = [QColor(image.pixel(150, y)).name() for y in range(35, 66)]
        self.assertGreaterEqual(with_regulator.count(target), 2)
        self.assertEqual(without.count(target), 0)

    def test_regulator_ring_is_hollow(self) -> None:
        _, regulators = self._regulator_canvas()

        image = self._paint_regulator(RegulatorNetworkItem(regulators))

        # O centro precisa continuar mostrando o que está por baixo.
        self.assertEqual(QColor(image.pixel(50, 50)).name(), "#ffffff")

    def test_regulator_ring_is_one_cached_item_above_the_lines(self) -> None:
        _, regulators = self._regulator_canvas()
        item = RegulatorNetworkItem(regulators)

        self.assertEqual(
            item.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache
        )
        # Acima de trechos (-20) e chaves (-15), abaixo das barras.
        self.assertGreater(item.zValue(), -15.0)
        self.assertLess(item.zValue(), 0.0)
        self.assertFalse(
            item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )

    def test_hidden_segment_hides_its_regulator_ring(self) -> None:
        _, regulators = self._regulator_canvas()
        item = RegulatorNetworkItem(regulators)
        revision = item.geometry_revision

        item.set_visibility_mask(np.array([False, True], dtype=np.bool_))

        self.assertEqual(item.visible_regulator_count, 0)
        self.assertGreater(item.geometry_revision, revision)
        image = self._paint_regulator(item)
        self.assertEqual(
            [QColor(image.pixel(50, y)).name() for y in range(35, 66)].count(
                REGULATOR_COLOR.name()
            ),
            0,
        )

    def test_same_mask_does_not_recompile_the_geometry(self) -> None:
        _, regulators = self._regulator_canvas()
        item = RegulatorNetworkItem(regulators)
        revision = item.geometry_revision

        item.set_visibility_mask(np.ones(2, dtype=np.bool_))

        self.assertEqual(item.geometry_revision, revision)

    def test_regulator_ring_keeps_its_pixel_size_across_zoom(self) -> None:
        _, regulators = self._regulator_canvas()
        item = RegulatorNetworkItem(regulators)

        def ring_pixels(scale: float) -> int:
            image = QImage(220, 100, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            painter.translate(0.0, 50.0)
            painter.scale(scale, scale)
            item.paint(painter, None)
            painter.end()
            target = REGULATOR_COLOR.name()
            return sum(
                QColor(image.pixel(x, y)).name() == target
                for x in range(220)
                for y in range(100)
            )

        # O anel é medido em pixels de tela: dobrar o zoom não pode engordá-lo.
        self.assertEqual(REGULATOR_DIAMETER_PX, 9.0)
        self.assertAlmostEqual(ring_pixels(1.0), ring_pixels(2.0), delta=6)

    def test_branch_highlight_is_one_yellow_cosmetic_path_and_can_be_focused(self) -> None:
        bars = CircuitModel(
            ["B1", "B2", "B3"],
            ["", "", ""],
            [10.0, 90.0, 90.0],
            [50.0, 50.0, -30.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T1", "T2"],
            ["", ""],
            ["D", "D"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["", ""],
            ["", ""],
            [80.0, 80.0],
        )
        overlay = BranchHighlightOverlayItem()
        overlay.bind(network, [0, 1])

        self.assertTrue(overlay.isVisible())
        self.assertEqual(overlay.segment_indices, (0, 1))
        self.assertGreater(overlay.zValue(), 90.0)
        image = QImage(110, 110, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.translate(0.0, 100.0)
        overlay.paint(painter, None)
        painter.end()
        pixels = [QColor(image.pixel(50, y)).name() for y in range(46, 55)]
        self.assertGreaterEqual(pixels.count("#ffcc00"), 2)
        self.assertGreaterEqual(
            sum(color != "#ffffff" for color in pixels),
            3,
        )

        scene = QGraphicsScene()
        view = DiagramView(scene)
        self.addCleanup(view.close)
        view.resize(800, 600)
        view.show()
        view.set_model(bars)
        view.set_line_model(network)
        view.focus_segments([0, 1])
        center = view.mapToScene(view.viewport().rect().center())
        self.assertAlmostEqual(center.x(), 50.0, delta=1.0)
        self.assertAlmostEqual(center.y(), -10.0, delta=1.0)
        self.assertLessEqual(abs(view.transform().m11()), 4.0)

        overlay.clear()
        self.assertFalse(overlay.isVisible())
        self.assertEqual(overlay.segment_indices, ())

    def test_load_layout_is_deterministic_and_symbols_have_fixed_geometry(self) -> None:
        _, _, _, view, _, layer = self._make_load_canvas(
            ["L4", "L1", "L3", "L2"]
        )
        self.addCleanup(view.close)
        x_offsets, y_offsets = layer.layout_offsets

        self.assertEqual(x_offsets.tolist(), [7.5, -7.5, -7.5, 7.5])
        self.assertEqual(y_offsets.tolist(), [18.0, 6.0, 18.0, 6.0])
        self.assertEqual(layer.mode, "Detalhado")
        self.assertEqual(layer.active_count, 4)
        item = layer._active[1]
        self.assertEqual(item.symbol_rect.width(), LOAD_WIDTH_PX)
        self.assertEqual(item.symbol_rect.height(), LOAD_HEIGHT_PX)
        self.assertTrue(
            item.flags()
            & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        before = item.boundingRect()
        view.zoom_at(QPoint(400, 300), 2.0)
        self.assertEqual(item.boundingRect(), before)

    def test_load_layer_uses_overview_masks_and_hidden_selection_overlay(self) -> None:
        _, loads, _, view, _, layer = self._make_load_canvas(
            ["L1", "L2", "L3"], cap=1
        )
        self.addCleanup(view.close)

        self.assertEqual(layer.mode, "Visão geral")
        self.assertEqual(layer.active_count, 0)
        self.assertTrue(layer.overview_item.isVisible())
        mask = np.array([False, True, False], dtype=np.bool_)
        layer.set_visibility_mask(mask)
        self.assertEqual(layer.overview_item.visible_point_count, 1)

        layer.set_selected_index(1)
        self.assertTrue(layer.selection_overlay.isVisible())
        layer.set_loads_visible(False)
        self.assertFalse(layer.overview_item.isVisible())
        self.assertFalse(layer.selection_overlay.isVisible())
        layer.set_selected_index(1, reveal_hidden=True)
        self.assertTrue(layer.selection_overlay.isVisible())
        self.assertIs(layer.model, loads)

    def test_load_rectangle_has_priority_but_bar_keeps_terminal_priority(self) -> None:
        bars, _, _, view, _, layer = self._make_load_canvas(["L1"])
        self.addCleanup(view.close)
        selected: list[FeatureSelection | None] = []
        view.selectionRequested.connect(selected.append)
        anchor = view.mapFromScene(QPointF(float(bars.x[0]), -float(bars.y[0])))

        view._select_nearest(anchor + QPoint(0, 10))
        self.assertEqual(selected[-1], FeatureSelection("load", 0))

        view._select_nearest(anchor)
        self.assertEqual(selected[-1], FeatureSelection("bar", 0))

        layer.set_loads_visible(False)
        view.set_bars_visible(False)
        view._select_nearest(anchor + QPoint(0, 10))
        self.assertIsNone(selected[-1])

    def test_overview_load_border_is_selectable_while_center_selects_bar(self) -> None:
        bars, _, _, view, _, _ = self._make_load_canvas(["L1", "L2"], cap=1)
        self.addCleanup(view.close)
        selected: list[FeatureSelection | None] = []
        view.selectionRequested.connect(selected.append)
        anchor = view.mapFromScene(QPointF(float(bars.x[0]), -float(bars.y[0])))

        view._select_nearest(anchor + QPoint(4, 0))
        self.assertEqual(selected[-1], FeatureSelection("load", 0))
        view._select_nearest(anchor)
        self.assertEqual(selected[-1], FeatureSelection("bar", 0))

    def test_load_symbol_pixels_use_neutral_and_selection_colors(self) -> None:
        bars, loads, _, view, _, _ = self._make_load_canvas(["L1"])
        self.addCleanup(view.close)
        item = LoadItem()
        item.bind(loads, 0, 0.0, 6.0)
        image = QImage(40, 30, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.translate(20, 5)
        item.paint(painter, None)
        painter.end()

        self.assertEqual(QColor(image.pixel(20, 7)).name(), "#202020")
        self.assertEqual(QColor(image.pixel(20, 15)).name(), "#f7f7f7")

        item.setSelected(True)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.translate(20, 5)
        item.paint(painter, None)
        painter.end()
        self.assertEqual(QColor(image.pixel(20, 15)).name(), "#ffcc00")
        self.assertEqual(int(loads.bar_indices[0]), 0)
        self.assertEqual(float(bars.x[0]), 0.0)

    def test_line_network_is_one_cached_item_below_bars(self) -> None:
        bars, scene, view, virtualizer = self._make_canvas(100, 200)
        self.addCleanup(view.close)
        network = LineNetworkModel(
            bars,
            [f"T{i}" for i in range(99)],
            [""] * 99,
            ["ABC"] * 99,
            list(range(99)),
            list(range(1, 100)),
            [""] * 99,
            [""] * 99,
            [""] * 99,
            [1.0] * 99,
        )
        item = LineNetworkItem(network)
        scene.addItem(item)
        self.app.processEvents()

        self.assertEqual(item.segment_count, 99)
        self.assertEqual(item.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.assertLess(item.zValue(), virtualizer.overview_item.zValue())
        self.assertEqual(len([candidate for candidate in scene.items() if isinstance(candidate, LineNetworkItem)]), 1)

    def test_click_selects_segment_but_bar_has_priority_at_endpoint(self) -> None:
        bars = CircuitModel(
            ["B1", "B2", "B3"],
            ["", "", ""],
            [0.0, 100.0, 100.0],
            [0.0, 0.0, 100.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T1", "T2"],
            ["", ""],
            ["ABC", "ABC"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["", ""],
            ["", ""],
            [100.0, 100.0],
        )
        scene = QGraphicsScene()
        view = DiagramView(scene)
        self.addCleanup(view.close)
        view.resize(800, 600)
        view.show()
        view.set_model(bars)
        view.set_line_model(network)
        view.fit_model()
        selected: list[FeatureSelection | None] = []
        view.selectionRequested.connect(selected.append)

        view._select_nearest(view.mapFromScene(QPointF(50.0, 0.0)))
        self.assertEqual(selected[-1], FeatureSelection("segment", 0))

        view._select_nearest(view.mapFromScene(QPointF(0.0, 0.0)))
        self.assertEqual(selected[-1], FeatureSelection("bar", 0))

        view.set_bars_visible(False)
        view._select_nearest(view.mapFromScene(QPointF(0.0, 0.0)))
        self.assertEqual(selected[-1], FeatureSelection("segment", 0))

    def test_segment_overlay_is_single_cosmetic_three_pixel_item(self) -> None:
        bars, scene, view, _ = self._make_canvas(2, 10)
        self.addCleanup(view.close)
        network = LineNetworkModel(
            bars,
            ["T1"],
            [""],
            ["ABC"],
            [0],
            [1],
            [""],
            [""],
            [""],
            [1.0],
        )
        overlay = SegmentSelectionOverlayItem()
        scene.addItem(overlay)
        overlay.bind(network, 0)

        self.assertTrue(overlay.isVisible())
        self.assertTrue(overlay.pen().isCosmetic())
        self.assertEqual(overlay.pen().widthF(), SEGMENT_SELECTION_WIDTH_PX)
        view.zoom_at(QPoint(400, 300), 2.0)
        self.assertEqual(overlay.pen().widthF(), SEGMENT_SELECTION_WIDTH_PX)
        self.assertEqual(
            len(
                [
                    candidate
                    for candidate in scene.items()
                    if isinstance(candidate, SegmentSelectionOverlayItem)
                ]
            ),
            1,
        )

    def test_switches_are_one_cached_red_item_between_lines_and_bars(self) -> None:
        bars, scene, view, virtualizer = self._make_canvas(3, 10)
        self.addCleanup(view.close)
        network = LineNetworkModel(
            bars,
            ["T1", "T2"],
            ["", ""],
            ["ABC", "ABC"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["", ""],
            ["", ""],
            [1.0, 1.0],
        )
        switches = SwitchModel(
            network,
            ["CH1", "CH2"],
            ["", ""],
            ["", ""],
            [0, 1],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
        )
        line_item = LineNetworkItem(network)
        switch_item = SwitchNetworkItem(switches)
        scene.addItem(line_item)
        scene.addItem(switch_item)

        self.assertEqual(SWITCH_COLOR.name(), "#ff0000")
        self.assertEqual(switch_item.switch_count, 2)
        self.assertEqual(
            switch_item.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache
        )
        self.assertLess(line_item.zValue(), switch_item.zValue())
        self.assertLess(switch_item.zValue(), virtualizer.overview_item.zValue())
        self.assertEqual(
            len(
                [
                    candidate
                    for candidate in scene.items()
                    if isinstance(candidate, SwitchNetworkItem)
                ]
            ),
            1,
        )

    def test_normal_segments_are_three_pixels_and_switches_are_one_pixel(self) -> None:
        bars = CircuitModel(
            ["B1", "B2", "B3"],
            ["", "", ""],
            [10.0, 50.0, 90.0],
            [50.0, 50.0, 50.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T1", "T2"],
            ["", ""],
            ["ABC", "ABC"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["", ""],
            ["", ""],
            [40.0, 40.0],
        )
        switches = SwitchModel(
            network,
            ["CH1"],
            [""],
            ["C1"],
            [1],
            [""],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        mask = np.array([True, True], dtype=np.bool_)
        styles = np.array([0, 0], dtype=np.intp)
        line_item = LineNetworkItem(network)
        line_item.set_switch_segment_indices(switches.segment_indices)
        switch_item = SwitchNetworkItem(switches)
        line_item.set_circuit_rendering(mask, styles, ("#0066CC",))
        switch_item.set_circuit_rendering(mask, styles, ("#0066CC",))
        line_revision = line_item.geometry_revision
        switch_revision = switch_item.geometry_revision

        line_item.set_circuit_rendering(mask, styles, ("#7A2E8E",))
        switch_item.set_circuit_rendering(mask, styles, ("#0066CC",))
        self.assertEqual(line_item.geometry_revision, line_revision)
        self.assertEqual(switch_item.geometry_revision, switch_revision)
        self.assertEqual(line_item.category_path_count, 1)
        self.assertEqual(switch_item.colored_path_count, 0)
        self.assertEqual(NORMAL_SEGMENT_WIDTH_PX, 3.0)
        self.assertEqual(SWITCH_SEGMENT_WIDTH_PX, 1.0)

        image = QImage(100, 100, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.translate(0.0, 100.0)
        line_item.paint(painter, None)
        switch_item.paint(painter, None)
        painter.end()
        normal_pixels = [
            QColor(image.pixel(30, y)).name() for y in range(46, 55)
        ]
        switch_pixels = [
            QColor(image.pixel(70, y)).name() for y in range(46, 55)
        ]
        self.assertGreater(normal_pixels.count("#7a2e8e"), 1)
        self.assertEqual(switch_pixels.count("#ff0000"), 1)
        self.assertNotIn("#7a2e8e", switch_pixels)

    def test_phase_rendering_overrides_circuits_for_lines_and_switches(self) -> None:
        bars = CircuitModel(
            [f"B{index}" for index in range(8)],
            [""] * 8,
            [10.0, 90.0] * 4,
            [20.0, 20.0, 40.0, 40.0, 60.0, 60.0, 80.0, 80.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            [f"T{index}" for index in range(4)],
            [""] * 4,
            ["1", "2", "13", "X"],
            [0, 2, 4, 6],
            [1, 3, 5, 7],
            [""] * 4,
            [""] * 4,
            [""] * 4,
            [80.0] * 4,
        )
        switches = SwitchModel(
            network,
            [f"CH{index}" for index in range(4)],
            [""] * 4,
            [""] * 4,
            [0, 1, 2, 3],
            [""] * 4,
            ["1"] * 4,
            ["1"] * 4,
            [""] * 4,
            [""] * 4,
            [""] * 4,
        )
        mask = np.ones(4, dtype=np.bool_)
        circuit_styles = np.zeros(4, dtype=np.intp)
        phase_styles = np.array([0, 1, 2, -1], dtype=np.intp)
        phase_colors = ("#0000FF", "#00FF00", "#FF0000")
        line_item = LineNetworkItem(network)
        switch_item = SwitchNetworkItem(switches)
        line_item.set_circuit_rendering(mask, circuit_styles, ("#7A2E8E",))
        switch_item.set_circuit_rendering(mask, circuit_styles, ("#7A2E8E",))

        line_item.set_phase_rendering(mask, phase_styles, phase_colors)
        switch_item.set_phase_rendering(mask, phase_styles, phase_colors)

        self.assertEqual(line_item.category_path_count, 4)
        self.assertEqual(switch_item.colored_path_count, 4)
        expected = ("#0000ff", "#00ff00", "#ff0000", "#555555")
        for item, expected_width in ((line_item, 3), (switch_item, 1)):
            image = QImage(100, 100, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            painter.translate(0.0, 100.0)
            item.paint(painter, None)
            painter.end()
            for y, color in zip((80, 60, 40, 20), expected, strict=True):
                pixels = [QColor(image.pixel(50, offset)).name() for offset in range(y - 3, y + 4)]
                self.assertEqual(pixels.count(color), expected_width)
                self.assertNotIn("#7a2e8e", pixels)

        line_item.set_circuit_rendering(mask, circuit_styles, ("#7A2E8E",))
        switch_item.set_circuit_rendering(mask, circuit_styles, ("#7A2E8E",))
        self.assertEqual(switch_item.colored_path_count, 0)

        line_image = QImage(100, 100, QImage.Format.Format_RGB32)
        line_image.fill(Qt.GlobalColor.white)
        painter = QPainter(line_image)
        painter.translate(0.0, 100.0)
        line_item.paint(painter, None)
        painter.end()
        self.assertEqual(QColor(line_image.pixel(50, 80)).name(), "#7a2e8e")

        switch_image = QImage(100, 100, QImage.Format.Format_RGB32)
        switch_image.fill(Qt.GlobalColor.white)
        painter = QPainter(switch_image)
        painter.translate(0.0, 100.0)
        switch_item.paint(painter, None)
        painter.end()
        self.assertEqual(QColor(switch_image.pixel(50, 80)).name(), "#ff0000")


if __name__ == "__main__":
    unittest.main()
