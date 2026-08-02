from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtWidgets import QGraphicsItem
    from PyQt6.QtWidgets import QApplication, QGraphicsScene

    from circuit_viewer.graphics import (
        MAX_ACTIVE_ITEMS,
        DiagramView,
        ItemVirtualizer,
        LineNetworkItem,
        SEGMENT_SELECTION_WIDTH_PX,
        SegmentSelectionOverlayItem,
        SWITCH_COLOR,
        SwitchNetworkItem,
    )

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False

from circuit_viewer.model import (
    CircuitModel,
    FeatureSelection,
    LineNetworkModel,
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


if __name__ == "__main__":
    unittest.main()
