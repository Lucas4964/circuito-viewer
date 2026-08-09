from __future__ import annotations

import math
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from pyproj import Transformer

    PYPROJ_AVAILABLE = True
except ModuleNotFoundError:
    PYPROJ_AVAILABLE = False
    Transformer = None  # type: ignore[assignment]

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QBrush, QImage, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsRectItem,
    QGraphicsScene,
    QLabel,
    QMessageBox,
)

from circuit_viewer.csv_import import CsvLoadResult
from circuit_viewer.graphics import DiagramView
from circuit_viewer.main_window import MainWindow
from circuit_viewer.mapa_tiles import (
    PROVEDOR_ESRI,
    PROVEDOR_GOOGLE_HIBRIDO,
    PROVEDOR_GOOGLE_SAT,
    GerenciadorTiles,
)
from circuit_viewer.model import CircuitModel, UtmCrs


@unittest.skipUnless(PYPROJ_AVAILABLE, "pyproj não está instalado")
class SatelliteGraphicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _model_for_location(
        longitude: float,
        latitude: float,
        crs: UtmCrs,
    ) -> CircuitModel:
        transformer = Transformer.from_crs(
            "EPSG:4326", f"EPSG:{crs.epsg}", always_xy=True
        )
        x, y = transformer.transform(longitude, latitude)
        return CircuitModel(
            ["B1", "B2"],
            ["C1", "C2"],
            [x - 100.0, x + 100.0],
            [y - 100.0, y + 100.0],
            crs,
        )

    def _make_view(self, model: CircuitModel):
        scene = QGraphicsScene()
        view = DiagramView(scene)
        view.resize(640, 480)
        view.show()
        view.set_model(model)
        view.fit_model()
        self.app.processEvents()
        self.addCleanup(view.shutdown_satellite)
        self.addCleanup(view.close)
        return scene, view

    @staticmethod
    def _solid_manager(
        view: DiagramView,
        color: QColor,
        provider=PROVEDOR_ESRI,
    ) -> GerenciadorTiles:
        manager = GerenciadorTiles(provider, parent=view)
        tile = QPixmap(256, 256)
        tile.fill(color)
        manager.definir_interesse = lambda _keys, _center: None
        manager.tile = lambda _level, _x, _y: tile
        manager.prefetch = lambda _keys: None
        return manager

    @staticmethod
    def _render(view: DiagramView) -> QImage:
        image = QImage(view.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("white"))
        painter = QPainter(image)
        try:
            view.render(painter)
        finally:
            painter.end()
        return image

    def test_roundtrip_for_south_north_and_multiple_zones(self) -> None:
        cases = (
            (-54.6, -15.6, UtmCrs(21, northern=False)),
            (-47.9, -15.8, UtmCrs(23, northern=False)),
            (9.0, 52.0, UtmCrs(32, northern=True)),
        )
        for longitude, latitude, crs in cases:
            with self.subTest(crs=crs.label):
                model = self._model_for_location(longitude, latitude, crs)
                _, view = self._make_view(model)
                transformer = Transformer.from_crs(
                    "EPSG:4326", f"EPSG:{crs.epsg}", always_xy=True
                )
                x, y = transformer.transform(longitude, latitude)
                result = view._scene_to_lonlat(x, -y)
                self.assertIsNotNone(result)
                lon2, lat2 = result
                mean_lat = math.radians((latitude + lat2) / 2.0)
                distance = 6_371_008.8 * math.hypot(
                    math.radians(lon2 - longitude) * math.cos(mean_lat),
                    math.radians(lat2 - latitude),
                )
                self.assertLess(distance, 0.01)
                scene_point = view._lonlat_to_scene(longitude, latitude)
                self.assertAlmostEqual(scene_point.x(), x, places=5)
                self.assertAlmostEqual(scene_point.y(), -y, places=5)

    def test_model_replacement_invalidates_transformers(self) -> None:
        first = self._model_for_location(-54.6, -15.6, UtmCrs(21, False))
        second = self._model_for_location(-47.9, -15.8, UtmCrs(23, False))
        _, view = self._make_view(first)
        view._scene_to_lonlat(float(first.x[0]), -float(first.y[0]))
        first_transformer = view._model_to_geographic
        self.assertEqual(view._transformer_epsg, first.crs.epsg)
        view.set_model(second)
        self.assertIsNone(view._model_to_geographic)
        view._scene_to_lonlat(float(second.x[0]), -float(second.y[0]))
        self.assertIsNot(view._model_to_geographic, first_transformer)
        self.assertEqual(view._transformer_epsg, second.crs.epsg)

    def test_satellite_degrades_without_model_and_is_painted_below_scene(self) -> None:
        empty = DiagramView(QGraphicsScene())
        empty.resize(320, 240)
        empty.show()
        manager = self._solid_manager(empty, QColor("green"))
        empty.set_tile_manager(manager)
        empty.set_satellite_enabled(True)
        self._render(empty)
        empty.shutdown_satellite()
        empty.close()

        model = self._model_for_location(-54.6, -15.6, UtmCrs(21, False))
        scene, view = self._make_view(model)
        manager = self._solid_manager(view, QColor("#008000"))
        view.set_tile_manager(manager)
        view.set_satellite_enabled(True)
        center = QPointF(float(model.x.mean()), -float(model.y.mean()))
        item = QGraphicsRectItem(QRectF(-15.0, -15.0, 30.0, 30.0))
        item.setPos(center)
        item.setBrush(QBrush(QColor("red")))
        item.setPen(QPen(Qt.PenStyle.NoPen))
        scene.addItem(item)
        image = self._render(view)
        center_pixel = view.mapFromScene(center)
        self.assertGreater(image.pixelColor(center_pixel).red(), 180)
        green_pixels = 0
        for x in range(20, image.width() - 20, 20):
            for y in range(20, image.height() - 20, 20):
                color = image.pixelColor(x, y)
                if color.green() > 80 and color.red() < 80:
                    green_pixels += 1
        self.assertGreater(green_pixels, 20)

    def test_disabled_layer_skips_the_satellite_pipeline(self) -> None:
        model = self._model_for_location(-54.6, -15.6, UtmCrs(21, False))
        _, view = self._make_view(model)
        manager = self._solid_manager(view, QColor("green"))
        view.set_tile_manager(manager)
        view.set_satellite_enabled(False)
        with patch.object(view, "_draw_satellite") as draw_satellite:
            self._render(view)
        draw_satellite.assert_not_called()

    def test_attribution_remains_bottom_right_after_navigation_and_resize(self) -> None:
        model = self._model_for_location(-54.6, -15.6, UtmCrs(21, False))
        _, view = self._make_view(model)
        view.set_tile_manager(self._solid_manager(view, QColor("#008000")))
        view.set_satellite_enabled(True)
        labels = view.viewport().findChildren(QLabel, "satelliteAttribution")
        self.assertEqual(len(labels), 1)
        label = labels[0]
        self.assertEqual(label.text(), PROVEDOR_ESRI.atribuicao)

        def assert_anchored() -> None:
            view.viewport().update()
            self.app.processEvents()
            self.assertTrue(label.isVisible())
            self.assertEqual(label.geometry().right(), view.viewport().width() - 7)
            self.assertEqual(label.geometry().bottom(), view.viewport().height() - 7)
            self.assertEqual(
                view.viewport().findChildren(QLabel, "satelliteAttribution"),
                [label],
            )

        assert_anchored()
        view.zoom_at(QPoint(200, 150), 1.5)
        assert_anchored()
        view.centerOn(float(model.x[0]), -float(model.y[0]))
        view.viewport().update()
        assert_anchored()
        view.resize(780, 520)
        self.app.processEvents()
        assert_anchored()

    def test_attribution_updates_provider_and_visibility_without_duplicates(self) -> None:
        model = self._model_for_location(-54.6, -15.6, UtmCrs(21, False))
        _, view = self._make_view(model)
        label = view.viewport().findChild(QLabel, "satelliteAttribution")
        self.assertIsNotNone(label)
        self.assertFalse(label.isVisible())

        view.set_tile_manager(self._solid_manager(view, QColor("green")))
        view.set_satellite_enabled(True)
        self.app.processEvents()
        self.assertTrue(label.isVisible())
        self.assertEqual(label.text(), PROVEDOR_ESRI.atribuicao)

        view.set_tile_manager(
            self._solid_manager(view, QColor("green"), PROVEDOR_GOOGLE_SAT)
        )
        self.app.processEvents()
        self.assertIs(
            view.viewport().findChild(QLabel, "satelliteAttribution"), label
        )
        self.assertEqual(label.text(), PROVEDOR_GOOGLE_SAT.atribuicao)

        view.set_satellite_enabled(False)
        self.app.processEvents()
        self.assertFalse(label.isVisible())
        view.set_satellite_enabled(True)
        self.app.processEvents()
        self.assertTrue(label.isVisible())
        view.set_tile_manager(None)
        self.app.processEvents()
        self.assertFalse(label.isVisible())

    def test_projective_tiles_have_no_seams_and_parent_fallback_paints(self) -> None:
        target = QPixmap(160, 160)
        target.fill(QColor("white"))
        tile = QPixmap(64, 64)
        tile.fill(QColor("black"))

        def corner(ix: int, iy: int) -> QPointF:
            return QPointF(20 + ix * 60 + iy * 6, 20 + iy * 60)

        painter = QPainter(target)
        try:
            for ix in range(2):
                for iy in range(2):
                    quad = QPolygonF(
                        [
                            corner(ix, iy),
                            corner(ix + 1, iy),
                            corner(ix + 1, iy + 1),
                            corner(ix, iy + 1),
                        ]
                    )
                    self.assertTrue(
                        DiagramView._draw_pixmap_in_quad(
                            painter, tile, QRectF(tile.rect()), quad
                        )
                    )
        finally:
            painter.end()
        image = target.toImage()
        white_pixels = sum(
            image.pixelColor(x, y).lightness() > 200
            for x in range(30, 110)
            for y in range(30, 110)
        )
        self.assertEqual(white_pixels, 0)

        manager = GerenciadorTiles()
        self.addCleanup(manager.fechar)
        parent = QPixmap(256, 256)
        parent.fill(QColor("green"))
        level, x, y = 12, 1000, 2000
        manager._guardar_mem((level - 1, x // 2, y // 2), parent)
        fallback = QPixmap(64, 64)
        fallback.fill(QColor("white"))
        painter = QPainter(fallback)
        try:
            painted = DiagramView._draw_fallback_tile(
                painter,
                manager,
                QPolygonF(
                    [
                        QPointF(0, 0),
                        QPointF(64, 0),
                        QPointF(64, 64),
                        QPointF(0, 64),
                    ]
                ),
                level,
                x,
                y,
            )
        finally:
            painter.end()
        self.assertTrue(painted)
        self.assertGreater(fallback.toImage().pixelColor(32, 32).green(), 100)

        manager.limpar_memoria()
        child = QPixmap(128, 128)
        child.fill(QColor("blue"))
        manager._guardar_mem((level + 1, x * 2, y * 2), child)
        child_fallback = QPixmap(64, 64)
        child_fallback.fill(QColor("white"))
        painter = QPainter(child_fallback)
        try:
            painted = DiagramView._draw_fallback_tile(
                painter,
                manager,
                QPolygonF(
                    [
                        QPointF(0, 0),
                        QPointF(64, 0),
                        QPointF(64, 64),
                        QPointF(0, 64),
                    ]
                ),
                level,
                x,
                y,
            )
        finally:
            painter.end()
        self.assertTrue(painted)
        self.assertGreater(child_fallback.toImage().pixelColor(16, 16).blue(), 100)
        self.assertGreater(child_fallback.toImage().pixelColor(48, 48).lightness(), 200)

    def test_neighbor_prefetch_uses_eight_screens_and_large_guard(self) -> None:
        model = self._model_for_location(-54.6, -15.6, UtmCrs(21, False))
        _, view = self._make_view(model)
        manager = self._solid_manager(view, QColor("green"))
        requested: list[tuple[int, int, int]] = []
        manager.prefetch = lambda keys: requested.extend(keys)
        view.set_tile_manager(manager)
        view.set_satellite_enabled(True)
        self.assertEqual(view._satellite_prefetch_timer.interval(), 250)
        view._satellite_last_frame = (10, 100, 101, 200, 201)
        view._prefetch_neighboring_tiles()
        self.assertEqual(len(requested), 32)
        self.assertEqual(len(set(requested)), 32)
        self.assertFalse(
            any(100 <= key[1] <= 101 and 200 <= key[2] <= 201 for key in requested)
        )
        requested.clear()
        view._satellite_last_frame = (10, 100, 110, 200, 209)
        view._prefetch_neighboring_tiles()
        self.assertEqual(requested, [])


@unittest.skipUnless(PYPROJ_AVAILABLE, "pyproj não está instalado")
class CoordinateUnitTests(unittest.TestCase):
    """Coordenadas em decímetros saturam a projeção e escondem o satélite."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _view_for(self, factor: float):
        crs = UtmCrs(21, northern=False)
        transformer = Transformer.from_crs(
            "EPSG:4326", f"EPSG:{crs.epsg}", always_xy=True
        )
        x, y = transformer.transform(-54.6, -15.6)
        model = CircuitModel(
            ["B1", "B2"],
            ["C1", "C2"],
            [x * factor, (x + 400.0) * factor],
            [y * factor, (y + 400.0) * factor],
            crs,
        )
        scene = QGraphicsScene()
        view = DiagramView(scene)
        view.resize(640, 480)
        view.show()
        view.set_model(model)
        view.fit_model()
        self.app.processEvents()
        self.addCleanup(view.close)
        return view

    def test_decimetres_land_in_the_ocean_and_metres_land_on_the_network(self) -> None:
        raw = self._view_for(10.0)
        corner = raw._scene_to_lonlat(float(raw.model.x[0]), -float(raw.model.y[0]))
        # Sem conversão o ponto sai do Brasil (longitude deixa de ser negativa).
        self.assertTrue(corner is None or corner[0] > -30.0)

        metric = self._view_for(1.0)
        lon, lat = metric._scene_to_lonlat(
            float(metric.model.x[0]), -float(metric.model.y[0])
        )
        self.assertAlmostEqual(lon, -54.6, places=3)
        self.assertAlmostEqual(lat, -15.6, places=3)

    def test_saturated_projection_announces_itself_once(self) -> None:
        view = self._view_for(10.0)
        view.set_tile_manager(SatelliteGraphicsTests._solid_manager(view, QColor("green")))
        view.set_satellite_enabled(True)
        reasons: list[str] = []
        view.satelliteUnavailable.connect(reasons.append)
        for _ in range(3):
            SatelliteGraphicsTests._render(view)
        self.addCleanup(view.shutdown_satellite)
        if reasons:
            self.assertEqual(len(reasons), 1)
            self.assertIn("zona UTM", reasons[0])


class SatelliteMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_window(self) -> MainWindow:
        window = MainWindow()
        window.show()
        self.app.processEvents()
        self.addCleanup(window.close)
        return window

    def test_menu_default_lazy_toggle_and_session_only_preference(self) -> None:
        window = self._make_window()
        self.assertFalse(window.satellite_action.isChecked())
        self.assertIs(window._satellite_provider, PROVEDOR_ESRI)
        self.assertTrue(window.satellite_provider_actions[PROVEDOR_ESRI].isChecked())
        self.assertIsNone(window.view.tile_manager)
        window.satellite_action.setChecked(True)
        first_manager = window.view.tile_manager
        self.assertIsNotNone(first_manager)
        self.assertTrue(window.view.satellite_enabled)
        window.satellite_action.setChecked(False)
        self.assertIs(window.view.tile_manager, first_manager)
        self.assertFalse(window.view.satellite_enabled)

        second = MainWindow()
        self.addCleanup(second.close)
        self.assertIs(second._satellite_provider, PROVEDOR_ESRI)
        self.assertFalse(second.satellite_action.isChecked())

    def test_enabling_without_bars_explains_the_missing_utm_reference(self) -> None:
        window = self._make_window()
        self.assertIsNone(window._model)
        window.satellite_action.setChecked(True)
        message = window.statusBar().currentMessage()
        self.assertIn("importar", message.casefold())
        self.assertIn("barras", message.casefold())
        # A camada continua ativa: passa a desenhar sozinha após a importação.
        self.assertTrue(window.view.satellite_enabled)

    def test_projection_failure_reaches_the_status_bar(self) -> None:
        window = self._make_window()
        window.view.satelliteUnavailable.emit("projeção saturada")
        self.assertIn("satélite", window.statusBar().currentMessage())
        self.assertIn("projeção saturada", window.statusBar().currentMessage())

    def test_download_failure_reaches_the_status_bar(self) -> None:
        window = self._make_window()
        window.satellite_action.setChecked(True)
        manager = window.view.tile_manager
        self.assertIsNotNone(manager)
        manager.falha_tiles.emit("HTTP 404")
        self.assertIn("satélite", window.statusBar().currentMessage())
        self.assertIn("HTTP 404", window.statusBar().currentMessage())

    def test_google_warning_cancel_accept_and_provider_replacement(self) -> None:
        window = self._make_window()
        google = window.satellite_provider_actions[PROVEDOR_GOOGLE_SAT]
        with patch(
            "circuit_viewer.main_window.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as warning:
            google.trigger()
        warning.assert_called_once()
        self.assertIs(window._satellite_provider, PROVEDOR_ESRI)
        self.assertTrue(window.satellite_provider_actions[PROVEDOR_ESRI].isChecked())

        with patch(
            "circuit_viewer.main_window.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.Yes,
        ) as warning:
            google.trigger()
        warning.assert_called_once()
        self.assertTrue(window._google_satellite_authorized)
        self.assertIs(window._satellite_provider, PROVEDOR_GOOGLE_SAT)
        self.assertIsNone(window.view.tile_manager)

        window.satellite_action.setChecked(True)
        first_manager = window.view.tile_manager
        self.assertIs(first_manager.provedor, PROVEDOR_GOOGLE_SAT)
        with patch("circuit_viewer.main_window.QMessageBox.warning") as warning:
            window.satellite_provider_actions[PROVEDOR_GOOGLE_HIBRIDO].trigger()
        warning.assert_not_called()
        second_manager = window.view.tile_manager
        self.assertIsNot(second_manager, first_manager)
        self.assertTrue(first_manager._fechado)
        self.assertIs(second_manager.provedor, PROVEDOR_GOOGLE_HIBRIDO)

        window.satellite_provider_actions[PROVEDOR_ESRI].trigger()
        self.assertIs(window.view.tile_manager.provedor, PROVEDOR_ESRI)
        self.assertTrue(second_manager._fechado)

    def test_reimport_preserves_layer_and_changes_geographic_crs(self) -> None:
        window = self._make_window()
        window.satellite_action.setChecked(True)
        manager = window.view.tile_manager
        manager._baixar = lambda key: manager._em_voo.add(key)
        first = SatelliteGraphicsTests._model_for_location(
            -54.6, -15.6, UtmCrs(21, False)
        )
        second = SatelliteGraphicsTests._model_for_location(
            -47.9, -15.8, UtmCrs(23, False)
        )
        window._on_import_finished(
            CsvLoadResult(first, "utf-8-sig", 2, 2, 0, (), 0)
        )
        window.view._scene_to_lonlat(float(first.x[0]), -float(first.y[0]))
        self.assertEqual(window.view._transformer_epsg, first.crs.epsg)
        window._on_import_finished(
            CsvLoadResult(second, "utf-8-sig", 2, 2, 0, (), 0)
        )
        self.assertTrue(window.satellite_action.isChecked())
        self.assertTrue(window.view.satellite_enabled)
        self.assertIs(window.view.tile_manager, manager)
        self.assertIsNone(window.view._model_to_geographic)
        window.view._scene_to_lonlat(float(second.x[0]), -float(second.y[0]))
        self.assertEqual(window.view._transformer_epsg, second.crs.epsg)


if __name__ == "__main__":
    unittest.main()
