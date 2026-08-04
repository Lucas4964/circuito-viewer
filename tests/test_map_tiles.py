from __future__ import annotations

import hashlib
import math
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtNetwork import QNetworkReply
from PyQt6.QtWidgets import QApplication

from circuit_viewer.mapa_tiles import (
    PROVEDORES,
    PROVEDOR_ESRI,
    GerenciadorTiles,
    Provedor,
    cantos_lonlat_da_faixa,
    garantir_backend_tls,
    lonlat_para_tile,
    nivel_zoom,
    ordenar_chebyshev,
    sub_rect_no_pai,
    tile_bbox,
    tile_pai,
)


class TileMathTests(unittest.TestCase):
    def test_world_tile_quadrants_and_roundtrip(self) -> None:
        self.assertEqual(lonlat_para_tile(-45.0, -12.0, 0), (0, 0))
        lon_min, lat_min, lon_max, lat_max = tile_bbox(0, 0, 0)
        self.assertEqual((lon_min, lon_max), (-180.0, 180.0))
        self.assertTrue(math.isclose(lat_max, 85.0511, abs_tol=1e-3))
        self.assertTrue(math.isclose(lat_min, -85.0511, abs_tol=1e-3))
        self.assertEqual(lonlat_para_tile(-90.0, 45.0, 1), (0, 0))
        self.assertEqual(lonlat_para_tile(90.0, 45.0, 1), (1, 0))
        self.assertEqual(lonlat_para_tile(-90.0, -45.0, 1), (0, 1))
        self.assertEqual(lonlat_para_tile(90.0, -45.0, 1), (1, 1))
        for level in (5, 10, 14, 18):
            x, y = lonlat_para_tile(-54.6, -15.6, level)
            left, bottom, right, top = tile_bbox(x, y, level)
            self.assertLessEqual(left, -54.6)
            self.assertLessEqual(-54.6, right)
            self.assertLessEqual(bottom, -15.6)
            self.assertLessEqual(-15.6, top)

    def test_zoom_levels_provider_registry_and_shared_edges(self) -> None:
        self.assertLessEqual(
            nivel_zoom(0.01, -15.6), nivel_zoom(1.0, -15.6)
        )
        self.assertLessEqual(nivel_zoom(1.0, -15.6), nivel_zoom(50.0, -15.6))
        self.assertEqual(nivel_zoom(0.0, -15.6), 0)
        self.assertEqual(nivel_zoom(1e9, -15.6, z_max=17), 17)
        self.assertIs(PROVEDORES[0], PROVEDOR_ESRI)
        self.assertEqual(len({provider.nome for provider in PROVEDORES}), 3)
        for provider in PROVEDORES:
            url = provider.url_template.format(z=15, x=11414, y=17821)
            self.assertNotIn("{", url)
            self.assertTrue(provider.atribuicao)
        east = tile_bbox(1000, 2000, 12)[2]
        west = tile_bbox(1001, 2000, 12)[0]
        self.assertEqual(east, west)
        south = tile_bbox(1000, 2000, 12)[1]
        north = tile_bbox(1000, 2001, 12)[3]
        self.assertEqual(south, north)

    def test_parent_subrect_and_chebyshev_order(self) -> None:
        x, y, level = 45656, 71287, 17
        for levels in (1, 2, 3):
            parent_x, parent_y, parent_level = tile_pai(x, y, level, levels)
            self.assertEqual(parent_level, level - levels)
            fx, fy, fraction = sub_rect_no_pai(x, y, level, parent_level)
            self.assertGreaterEqual(fx, 0.0)
            self.assertGreaterEqual(fy, 0.0)
            self.assertEqual(fraction, 1.0 / (2**levels))
            child_left, _, child_right, _ = tile_bbox(x, y, level)
            parent_left, _, parent_right, _ = tile_bbox(
                parent_x, parent_y, parent_level
            )
            width = parent_right - parent_left
            self.assertTrue(
                math.isclose(child_left, parent_left + fx * width, abs_tol=1e-9)
            )
            self.assertTrue(
                math.isclose(
                    child_right,
                    parent_left + (fx + fraction) * width,
                    abs_tol=1e-9,
                )
            )
        self.assertEqual(sub_rect_no_pai(10, 20, 5, 5), (0.0, 0.0, 1.0))
        center = (10, 100, 100)
        keys = [
            (10, 100 + dx, 100 + dy)
            for dx in range(-2, 3)
            for dy in range(-2, 3)
        ]
        ordered = ordenar_chebyshev(keys, center)
        distances = [max(abs(key[1] - 100), abs(key[2] - 100)) for key in ordered]
        self.assertEqual(distances, sorted(distances))
        self.assertEqual(ordered[0], center)
        self.assertEqual(
            ordenar_chebyshev([(9, 50, 50), (10, 103, 100)], center)[0],
            (10, 103, 100),
        )

    def test_corner_grid_is_anchored_closed_and_oriented(self) -> None:
        x0, y0, level = 45656, 71287, 17
        nx, ny = 3, 2
        longitudes, latitudes = cantos_lonlat_da_faixa(
            x0, y0, nx, ny, level
        )
        self.assertEqual(len(longitudes), (nx + 1) * (ny + 1))
        self.assertEqual(longitudes[0], tile_bbox(x0, y0, level)[0])
        self.assertEqual(latitudes[0], tile_bbox(x0, y0, level)[3])
        self.assertEqual(
            longitudes[-1], tile_bbox(x0 + nx - 1, y0 + ny - 1, level)[2]
        )
        self.assertEqual(
            latitudes[-1], tile_bbox(x0 + nx - 1, y0 + ny - 1, level)[1]
        )
        for iy in range(ny + 1):
            row = [longitudes[iy * (nx + 1) + ix] for ix in range(nx + 1)]
            self.assertEqual(row, sorted(row))
        for ix in range(nx + 1):
            column = [latitudes[iy * (nx + 1) + ix] for iy in range(ny + 1)]
            self.assertEqual(column, sorted(column, reverse=True))


class TlsBackendTests(unittest.TestCase):
    """O backend OpenSSL do Qt trava com a libcrypto do pyproj/hashlib."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_openssl_is_replaced_only_when_schannel_exists(self) -> None:
        with patch("PyQt6.QtNetwork.QSslSocket") as ssl_socket:
            ssl_socket.activeBackend.return_value = "openssl"
            ssl_socket.availableBackends.return_value = ["openssl", "schannel"]
            ssl_socket.setActiveBackend.return_value = True
            self.assertEqual(garantir_backend_tls(), "schannel")
            ssl_socket.setActiveBackend.assert_called_once_with("schannel")

    def test_platforms_without_schannel_are_left_untouched(self) -> None:
        with patch("PyQt6.QtNetwork.QSslSocket") as ssl_socket:
            ssl_socket.activeBackend.return_value = "openssl"
            ssl_socket.availableBackends.return_value = ["openssl", "cert-only"]
            self.assertEqual(garantir_backend_tls(), "openssl")
            ssl_socket.setActiveBackend.assert_not_called()

    def test_active_schannel_is_kept_and_failed_switch_degrades(self) -> None:
        with patch("PyQt6.QtNetwork.QSslSocket") as ssl_socket:
            ssl_socket.activeBackend.return_value = "schannel"
            ssl_socket.availableBackends.return_value = ["openssl", "schannel"]
            self.assertEqual(garantir_backend_tls(), "schannel")
            ssl_socket.setActiveBackend.assert_not_called()

        # setActiveBackend devolve False quando o TLS já foi inicializado.
        with patch("PyQt6.QtNetwork.QSslSocket") as ssl_socket:
            ssl_socket.activeBackend.return_value = "openssl"
            ssl_socket.availableBackends.return_value = ["openssl", "schannel"]
            ssl_socket.setActiveBackend.return_value = False
            self.assertEqual(garantir_backend_tls(), "openssl")

    def test_manager_selects_the_backend_before_any_request(self) -> None:
        with patch(
            "circuit_viewer.mapa_tiles.garantir_backend_tls",
            return_value="schannel",
        ) as guard:
            manager = GerenciadorTiles()
            self.addCleanup(manager.fechar)
        guard.assert_called_once_with()
        self.assertEqual(manager.backend_tls, "schannel")


class _FakeReply:
    def __init__(self, key, error, *, status=None, data=b"") -> None:  # noqa: ANN001
        self.key = list(key)
        self.network_error = error
        self.status = status
        self.data = data
        self.deleted = False
        self.aborted = False

    def property(self, _name):  # noqa: ANN001
        return self.key

    def error(self):  # noqa: ANN201
        return self.network_error

    def attribute(self, _attribute):  # noqa: ANN001, ANN201
        return self.status

    def readAll(self):  # noqa: ANN201, N802
        return self.data

    def deleteLater(self) -> None:  # noqa: N802
        self.deleted = True

    def abort(self) -> None:
        self.aborted = True


class TileManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _manager_without_network(self):
        manager = GerenciadorTiles()
        manager._dir_cache = self.temp_dir.name
        downloads: list[tuple[int, int, int]] = []

        def fake_download(key) -> None:  # noqa: ANN001
            downloads.append(key)
            manager._em_voo.add(key)

        manager._baixar = fake_download
        self.addCleanup(manager.fechar)
        return manager, downloads

    def test_queue_limit_center_order_and_obsolete_requests(self) -> None:
        manager, downloads = self._manager_without_network()
        keys = [(10, 100 + index, 100) for index in range(10)]
        manager.definir_interesse(keys, (10, 100, 100))
        manager._pendentes = {key: 0 for key in reversed(keys)}
        manager._bombear()
        self.assertEqual(len(downloads), 6)
        self.assertEqual(downloads[0], (10, 100, 100))
        manager._em_voo.discard(downloads[0])
        manager._bombear()
        self.assertEqual(len(downloads), 7)

        obsolete = (10, 50, 50)
        current = (10, 200, 200)
        manager._em_voo.clear()
        manager._pendentes = {obsolete: 0, current: 0}
        manager.definir_interesse([current], current)
        self.assertNotIn(obsolete, downloads)
        self.assertIn(current, downloads)

    def test_visible_requests_precede_prefetch(self) -> None:
        manager, downloads = self._manager_without_network()
        manager._MAX_EM_VOO = 1
        visible = (10, 100, 100)
        neighbor = (10, 130, 100)
        manager._em_voo.add((0, 0, 0))
        manager.prefetch([neighbor])
        manager.definir_interesse([visible], visible)
        manager.tile(*visible)
        manager._em_voo.clear()
        manager._bombear()
        self.assertEqual(downloads, [visible])
        manager._em_voo.clear()
        manager._bombear()
        self.assertEqual(downloads, [visible, neighbor])

    def test_http_is_memoized_and_network_retries_three_times(self) -> None:
        manager, downloads = self._manager_without_network()
        missing = (10, 1, 2)
        manager.definir_interesse([missing], missing)
        manager.tile(*missing)
        manager._em_voo.discard(missing)
        manager._ao_terminar(
            missing,
            QNetworkReply.NetworkError.ContentNotFoundError,
            404,
            b"",
        )
        manager.tile(*missing)
        self.assertEqual(downloads.count(missing), 1)
        self.assertIn(missing, manager._indisponivel)

        retry = (10, 3, 4)
        manager.definir_interesse([retry], retry)
        manager.tile(*retry)
        for _ in range(3):
            manager._em_voo.discard(retry)
            manager._ao_terminar(
                retry,
                QNetworkReply.NetworkError.TimeoutError,
                None,
                b"",
            )
        self.assertEqual(downloads.count(retry), 3)
        self.assertIn(retry, manager._indisponivel)

    def test_permanent_failures_notify_the_interface_exactly_once(self) -> None:
        manager, _ = self._manager_without_network()
        reasons: list[str] = []
        manager.falha_tiles.connect(reasons.append)

        manager._ao_terminar(
            (10, 1, 2),
            QNetworkReply.NetworkError.ContentNotFoundError,
            404,
            b"",
        )
        self.assertEqual(reasons, ["HTTP 404"])

        # Uma tela inteira de tiles ausentes não vira uma enxurrada de avisos.
        manager._ao_terminar(
            (10, 5, 6),
            QNetworkReply.NetworkError.ContentAccessDenied,
            403,
            b"",
        )
        self.assertEqual(reasons, ["HTTP 404"])

    def test_network_notifies_only_after_retries_and_placeholder_is_silent(
        self,
    ) -> None:
        manager, _ = self._manager_without_network()
        reasons: list[str] = []
        manager.falha_tiles.connect(reasons.append)
        retry = (10, 3, 4)
        for _ in range(2):
            manager._ao_terminar(
                retry,
                QNetworkReply.NetworkError.TimeoutError,
                None,
                b"",
                "tempo esgotado",
            )
            self.assertEqual(reasons, [])
        manager._ao_terminar(
            retry,
            QNetworkReply.NetworkError.TimeoutError,
            None,
            b"",
            "tempo esgotado",
        )
        self.assertEqual(reasons, ["tempo esgotado"])

        # Placeholder do provedor é ausência de cobertura (resolvida por
        # overzoom), não falha de acesso: não deve alarmar o usuário.
        placeholder = b"sem cobertura"
        provider = Provedor(
            nome="Fake",
            url_template="http://localhost/{z}/{x}/{y}",
            atribuicao="Fake",
            hash_indisponivel=hashlib.md5(placeholder).hexdigest(),
        )
        quiet = GerenciadorTiles(provider)
        quiet._dir_cache = self.temp_dir.name
        self.addCleanup(quiet.fechar)
        quiet_reasons: list[str] = []
        quiet.falha_tiles.connect(quiet_reasons.append)
        quiet._ao_terminar(
            (10, 7, 8),
            QNetworkReply.NetworkError.NoError,
            200,
            placeholder,
        )
        self.assertEqual(quiet_reasons, [])
        self.assertIn((10, 7, 8), quiet._indisponivel)

    def test_lru_disk_namespaces_and_deterministic_shutdown(self) -> None:
        manager, _ = self._manager_without_network()
        manager._LIMITE_MEM_BYTES = 3 * 256 * 256 * 4
        for index in range(4):
            pixmap = QPixmap(256, 256)
            pixmap.fill()
            manager._guardar_mem((10, index, 0), pixmap)
        self.assertNotIn((10, 0, 0), manager._mem)
        manager.tile_do_cache(10, 1, 0)
        pixmap = QPixmap(256, 256)
        pixmap.fill()
        manager._guardar_mem((10, 9, 0), pixmap)
        self.assertIn((10, 1, 0), manager._mem)
        self.assertNotIn((10, 2, 0), manager._mem)

        source_path = os.path.join(self.temp_dir.name, "source.png")
        disk_pixmap = QPixmap(16, 16)
        disk_pixmap.fill()
        self.assertTrue(disk_pixmap.save(source_path, "PNG"))
        with open(source_path, "rb") as source:
            manager._gravar_disco((5, 1, 2), source.read())
        manager.limpar_memoria()
        restored = manager.tile_do_cache(5, 1, 2)
        self.assertIsNotNone(restored)
        self.assertFalse(restored.isNull())

        directories = []
        with patch("circuit_viewer.mapa_tiles.os.makedirs"):
            for provider in PROVEDORES:
                other = GerenciadorTiles(provider)
                directories.append(other._dir_cache)
                other.fechar()
        self.assertEqual(len(set(directories)), len(PROVEDORES))

        reply = _FakeReply((1, 1, 1), QNetworkReply.NetworkError.NoError)
        manager._replies.add(reply)
        manager._em_voo.add((1, 1, 1))
        manager.fechar()
        self.assertTrue(reply.aborted)
        self.assertTrue(reply.deleted)
        self.assertTrue(manager._fechado)
        self.assertFalse(manager._em_voo)

    def test_real_qt_network_delivery_reaches_the_tile_cache(self) -> None:
        image_path = os.path.join(self.temp_dir.name, "tile.png")
        source = QPixmap(8, 8)
        source.fill(QColor("#123456"))
        self.assertTrue(source.save(image_path, "PNG"))
        with open(image_path, "rb") as stream:
            payload = stream.read()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format, *args) -> None:  # noqa: ANN001
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1.0)
        self.addCleanup(server.shutdown)

        provider = Provedor(
            nome="Servidor local",
            url_template=(
                f"http://127.0.0.1:{server.server_port}/{{z}}/{{x}}/{{y}}"
            ),
            atribuicao="Teste",
        )
        manager = GerenciadorTiles(provider)
        manager._dir_cache = self.temp_dir.name
        self.addCleanup(manager.fechar)

        loop = QEventLoop()
        manager.tile_pronto.connect(loop.quit)
        key = (0, 0, 0)
        manager.definir_interesse([key], key)
        self.assertIsNone(manager.tile(*key))
        QTimer.singleShot(3000, loop.quit)
        loop.exec()

        tile = manager.tile_do_cache(*key)
        self.assertIsNotNone(tile)
        self.assertEqual(tile.size(), source.size())


if __name__ == "__main__":
    unittest.main()
