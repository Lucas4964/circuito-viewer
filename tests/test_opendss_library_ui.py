from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPalette, QPixmap
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import CircuitModel, LineNetworkModel, UtmCrs
    from circuit_viewer.opendss_automatic_assembly_session import (
        OpenDssAutomaticAssemblySession,
    )
    from circuit_viewer.opendss_cables_window import OpenDssCablesWindow
    from circuit_viewer.opendss_geometries_window import OpenDssGeometriesWindow
    from circuit_viewer.opendss_library import (
        ArrangementDefinition,
        CableDefinition,
        ConductorPosition,
        GeometryDefinition,
    )
    from circuit_viewer.opendss_library_help import OpenDssLibraryHelpDialog
    from circuit_viewer.opendss_library_session import OpenDssLibrarySession
    from circuit_viewer.opendss_mapping_session import OpenDssMappingSession
    from circuit_viewer.opendss_mapping_store import (
        LibraryNameMapping,
        OpenDssLibraryMappings,
        read_arrangement_map,
        read_cable_map,
    )
    from circuit_viewer.phase_config import load_phase_configuration

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class LibraryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.cables_path = root / "cabos.json"
        self.geometries_path = root / "geometrias.json"
        self.mapping_session = OpenDssMappingSession(
            cable_map_path=root / "mapa_cabos.json",
            arrangement_map_path=root / "mapa_arranjos.json",
        )
        self.session = OpenDssLibrarySession(
            cables_path=self.cables_path,
            geometries_path=self.geometries_path,
            mapping_session=self.mapping_session,
        )
        self.automatic_session = OpenDssAutomaticAssemblySession(
            self.session,
            self.mapping_session,
            load_phase_configuration(),
        )
        self.help = OpenDssLibraryHelpDialog()
        self.addCleanup(self.help.deleteLater)

    def _cables_window(self) -> "OpenDssCablesWindow":
        window = OpenDssCablesWindow(
            self.session,
            self.help,
            assembly_session=self.automatic_session,
        )
        window.show()
        self.app.processEvents()
        self.addCleanup(window.deleteLater)
        return window

    def _geometries_window(self) -> "OpenDssGeometriesWindow":
        window = OpenDssGeometriesWindow(
            self.session,
            self.help,
            assembly_session=self.automatic_session,
        )
        window.show()
        self.app.processEvents()
        self.addCleanup(window.deleteLater)
        return window

    def _set_automatic_lines(
        self,
        phases: tuple[str, ...] = ("9",),
        *,
        neutral_id: str = "CN",
    ) -> None:
        arrangement = self.session.catalog.arrangement("cruzeta_8ft_3fn")
        phase_cable = self.session.catalog.cable("acsr_556_5")
        neutral_cable = self.session.catalog.cable("acsr_4/0")
        self.mapping_session.save_maps(
            OpenDssLibraryMappings(
                cables=(
                    LibraryNameMapping("CF", phase_cable.name),
                    LibraryNameMapping("CN", neutral_cable.name),
                ),
                arrangements=(LibraryNameMapping("AR", arrangement.name),),
            )
        )
        count = len(phases)
        bars = CircuitModel(
            [f"B{index}" for index in range(count + 1)],
            [f"BAR{index}" for index in range(count + 1)],
            [500_000.0 + index for index in range(count + 1)],
            [8_000_000.0] * (count + 1),
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            [f"T{index + 1}" for index in range(count)],
            [f"L{index + 1}" for index in range(count)],
            phases,
            list(range(count)),
            list(range(1, count + 1)),
            ["AR"] * count,
            ["CF"] * count,
            [neutral_id] * count,
            [10.0] * count,
        )
        self.automatic_session.set_line_model(network)
        self.app.processEvents()

    def test_main_menu_keeps_imported_catalog_and_adds_libraries_after_tables(self) -> None:
        window = MainWindow(
            library_cables_path=self.cables_path,
            library_geometries_path=self.geometries_path,
        )
        self.addCleanup(window.deleteLater)
        labels = [action.text() for action in window.menuBar().actions()]
        self.assertEqual(labels[labels.index("Tabelas") + 1], "Bibliotecas")
        self.assertEqual(window.cables_action.text(), "Cabos importados…")
        self.assertEqual(
            [action.text() for action in window.libraries_menu.actions()],
            ["Cabos…", "Geometrias…"],
        )

        window.opendss_cables_action.trigger()
        window.opendss_geometries_action.trigger()
        self.app.processEvents()
        self.assertTrue(window.opendss_cables_window.isVisible())
        self.assertTrue(window.opendss_geometries_window.isVisible())
        self.assertFalse(window.opendss_cables_window.isModal())
        self.assertFalse(window.opendss_geometries_window.isModal())

    def test_cables_list_filters_and_marks_incomplete_records(self) -> None:
        window = self._cables_window()
        self.assertEqual(window.table_model.rowCount(), 58)
        window.search_edit.setText("CN_250_1_3")
        self.app.processEvents()
        self.assertEqual(window.proxy_model.rowCount(), 1)

        incomplete = CableDefinition("incomplete", "Meu cabo", resistance_units="", gmr_units="", radius_units="")
        self.session.catalog.cables.append(incomplete)
        self.session.mark_cables_changed()
        window.search_edit.setText("Meu cabo")
        self.app.processEvents()
        self.assertEqual(window.proxy_model.data(window.proxy_model.index(0, 8)), "Incompleto")

    def test_new_duplicate_and_explicit_save_write_only_on_save(self) -> None:
        window = self._cables_window()
        window._new_cable()
        self.app.processEvents()
        self.assertTrue(self.session.cables_dirty)
        self.assertFalse(self.cables_path.exists())
        first_id = window._selected_id
        window._duplicate_cable()
        self.app.processEvents()
        self.assertNotEqual(window._selected_id, first_id)
        self.assertEqual(len({item.name.casefold() for item in self.session.catalog.cables}), len(self.session.catalog.cables))

        self.assertTrue(window._save())
        self.assertTrue(self.cables_path.exists())
        self.assertFalse(self.session.cables_dirty)
        self.assertEqual(len(json.loads(self.cables_path.read_text(encoding="utf-8"))["cabos"]), 60)

    def test_cable_and_arrangement_editors_normalize_names_to_uppercase(self) -> None:
        cables = self._cables_window()
        cables.name_edit.setText("  cabo com caixa Mista  ")
        cables._edit_name()
        self.assertEqual(cables.selected_cable().name, "CABO COM CAIXA MISTA")
        self.assertEqual(cables.name_edit.text(), "CABO COM CAIXA MISTA")

        geometries = self._geometries_window()
        geometries.arrangement_name_edit.setText("  arranjo com caixa Mista  ")
        geometries._edit_arrangement_name()
        self.assertEqual(
            geometries.selected_arrangement().name,
            "ARRANJO COM CAIXA MISTA",
        )
        self.assertEqual(
            geometries.arrangement_name_edit.text(),
            "ARRANJO COM CAIXA MISTA",
        )

    def test_mapped_unused_cable_cannot_be_deleted(self) -> None:
        root = Path(self.directory.name)
        mapping_session = OpenDssMappingSession(
            cable_map_path=root / "mapa_cabos.json",
            arrangement_map_path=root / "mapa_arranjos.json",
        )
        session = OpenDssLibrarySession(
            cables_path=root / "mapped_cabos.json",
            geometries_path=root / "mapped_geometrias.json",
            mapping_session=mapping_session,
        )
        cable = next(
            item
            for item in session.catalog.cables
            if not session.catalog.geometries_using_cable(item.cable_id)
        )
        mapping_session.save_maps(
            OpenDssLibraryMappings(
                cables=(LibraryNameMapping("115", cable.name),),
            )
        )
        window = OpenDssCablesWindow(session, self.help)
        self.addCleanup(window.deleteLater)
        window._selected_id = cable.cable_id
        window._load_editor()

        with patch("circuit_viewer.opendss_cables_window.QMessageBox.warning") as warning:
            window._delete_cable()

        self.assertIsNotNone(session.catalog.cable(cable.cable_id))
        self.assertIn("CABO_ID", warning.call_args.args[2])
        self.assertIn("115", warning.call_args.args[2])

    def test_mapped_unused_arrangement_cannot_be_deleted(self) -> None:
        root = Path(self.directory.name)
        mapping_session = OpenDssMappingSession(
            cable_map_path=root / "mapa_cabos.json",
            arrangement_map_path=root / "mapa_arranjos.json",
        )
        session = OpenDssLibrarySession(
            cables_path=root / "mapped_cabos.json",
            geometries_path=root / "mapped_geometrias.json",
            mapping_session=mapping_session,
        )
        arrangement = ArrangementDefinition(
            "mapped-only",
            "Arranjo mapeado",
            1,
            "m",
            [ConductorPosition(0.0, 8.0)],
        )
        session.catalog.arrangements.append(arrangement)
        session.mark_geometries_changed()
        session.save_geometry_drafts()
        mapping_session.save_maps(
            OpenDssLibraryMappings(
                arrangements=(LibraryNameMapping("9", arrangement.name),),
            )
        )
        window = OpenDssGeometriesWindow(session, self.help)
        self.addCleanup(window.deleteLater)
        window._selected_arrangement_id = arrangement.arrangement_id
        window._load_arrangement_editor()

        with patch(
            "circuit_viewer.opendss_geometries_window.QMessageBox.warning"
        ) as warning:
            window._delete_arrangement()

        self.assertIsNotNone(session.catalog.arrangement(arrangement.arrangement_id))
        self.assertIn("ARRANJO_ID", warning.call_args.args[2])
        self.assertIn("9", warning.call_args.args[2])

    def test_saving_library_renames_migrates_maps_by_stable_id(self) -> None:
        root = Path(self.directory.name)
        cable_map_path = root / "rename_mapa_cabos.json"
        arrangement_map_path = root / "rename_mapa_arranjos.json"
        mapping_session = OpenDssMappingSession(
            cable_map_path=cable_map_path,
            arrangement_map_path=arrangement_map_path,
        )
        session = OpenDssLibrarySession(
            cables_path=root / "rename_cabos.json",
            geometries_path=root / "rename_geometrias.json",
            mapping_session=mapping_session,
        )
        cable = session.catalog.cables[0]
        arrangement = session.catalog.arrangements[0]
        mapping_session.save_maps(
            OpenDssLibraryMappings(
                cables=(LibraryNameMapping("115", cable.name),),
                arrangements=(LibraryNameMapping("1", arrangement.name),),
            )
        )

        cable.name = "CABO RENOMEADO"
        session.mark_cables_changed()
        session.save_cable_drafts()
        arrangement.name = "ARRANJO RENOMEADO"
        session.mark_geometries_changed()
        session.save_geometry_drafts()

        self.assertEqual(read_cable_map(cable_map_path)[0].library_name, "CABO RENOMEADO")
        self.assertEqual(
            read_arrangement_map(arrangement_map_path)[0].library_name,
            "ARRANJO RENOMEADO",
        )

    def test_delete_used_cable_is_blocked(self) -> None:
        self._set_automatic_lines()
        window = self._cables_window()
        window._selected_id = "acsr_556_5"
        window._load_editor()
        before = len(self.session.catalog.cables)
        with patch("circuit_viewer.opendss_cables_window.QMessageBox.warning") as warning:
            window._delete_cable()
        self.assertEqual(len(self.session.catalog.cables), before)
        self.assertIn("usado", warning.call_args.args[2])

    def test_cross_window_updates_usage_and_missing_reference_warning(self) -> None:
        self._set_automatic_lines()
        cables = self._cables_window()
        geometries = self._geometries_window()
        assembly = self.automatic_session.result.assemblies[0]
        cable_id = assembly.key.phase_cable_id
        source_row = next(index for index, cable in enumerate(self.session.catalog.cables) if cable.cable_id == cable_id)
        self.assertGreater(len(self.automatic_session.assemblies_using_cable(cable_id)), 0)
        self.assertNotEqual(cables.table_model.data(cables.table_model.index(source_row, 7)), "—")

        self._set_automatic_lines(neutral_id="SEM-MAPA")
        self.app.processEvents()
        self.assertIn("ocorrência", cables.reference_label.text().lower())
        self.assertIn("Diagnósticos", geometries.automatic_issue_summary.text())
        self.assertGreater(geometries.automatic_issue_model.rowCount(), 0)

    def test_arrangement_draft_only_rebuilds_automatic_mountings_after_save(self) -> None:
        self._set_automatic_lines()
        window = self._geometries_window()
        arrangement = self.session.catalog.arrangement("cruzeta_8ft_3fn")
        window._selected_arrangement_id = arrangement.arrangement_id
        window._load_arrangement_editor()
        automatic_before = self.automatic_session.result.assemblies[0]
        legacy_before = tuple(
            tuple(item.cable_ids) for item in self.session.legacy_geometries
        )

        window._add_position()
        self.app.processEvents()
        self.assertEqual(
            self.automatic_session.result.assemblies[0].arrangement.conductor_count,
            automatic_before.arrangement.conductor_count,
        )
        self.assertIn("última versão salva", window.automatic_draft_label.text())
        self.assertEqual(
            tuple(tuple(item.cable_ids) for item in self.session.legacy_geometries),
            legacy_before,
        )

        self.assertTrue(window._save())
        self.app.processEvents()
        self.assertEqual(
            self.automatic_session.result.assemblies[0].arrangement.conductor_count,
            automatic_before.arrangement.conductor_count + 1,
        )

    def test_position_and_assignment_tables_show_every_row_without_inner_scroll(self) -> None:
        self._set_automatic_lines()
        window = self._geometries_window()
        window._selected_arrangement_id = "cruzeta_8ft_3fn"
        window._load_arrangement_editor()

        for table in (window.positions_table, window.assignments_table):
            table.sync_height_to_rows()
            row_height = sum(table.rowHeight(row) for row in range(table.model().rowCount()))
            minimum = table.horizontalHeader().height() + row_height + 2 * table.frameWidth()
            self.assertEqual(
                table.verticalScrollBarPolicy(),
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )
            self.assertGreaterEqual(table.height(), minimum)

        original_height = window.positions_table.height()
        window._add_position()
        self.app.processEvents()
        window.positions_table.sync_height_to_rows()
        self.assertGreater(window.positions_table.height(), original_height)

        arrangement = window.selected_arrangement()
        last_row = arrangement.conductor_count - 1
        window.positions_table.setCurrentIndex(window.position_model.index(last_row, 0))
        window._remove_position()
        self.app.processEvents()
        window.positions_table.sync_height_to_rows()
        self.assertEqual(window.positions_table.height(), original_height)

    def test_automatic_assignment_model_is_read_only(self) -> None:
        self._set_automatic_lines()
        window = self._geometries_window()
        index = window.assignment_model.index(0, 4)
        self.assertFalse(
            bool(window.assignment_model.flags(index) & Qt.ItemFlag.ItemIsEditable)
        )
        self.assertFalse(
            window.assignment_model.setData(
                index, "acsr795", Qt.ItemDataRole.UserRole
            )
        )
        self.assertFalse(hasattr(window, "new_geometry_button"))
        self.assertFalse(hasattr(window, "delete_geometry_button"))

    def test_automatic_session_rebuilds_on_saved_maps_and_clears_with_lines(self) -> None:
        self._set_automatic_lines(("7", "8"))
        window = self._geometries_window()
        self.assertEqual(window.geometries_list.count(), 2)

        self.mapping_session.save_maps(OpenDssLibraryMappings())
        self.app.processEvents()
        self.assertEqual(window.geometries_list.count(), 0)
        self.assertEqual(self.automatic_session.result.unassembled_segments, 2)

        self.automatic_session.set_line_model(None)
        self.app.processEvents()
        self.assertEqual(self.automatic_session.result.total_segments, 0)
        self.assertIn("Importe trechos", window.geometry_empty_label.text())

    def test_automatic_list_marks_only_effective_neutral_with_n(self) -> None:
        self._set_automatic_lines(("9",))
        window = self._geometries_window()

        self.assertIn("| DFN |", window.geometries_list.item(0).text())

        self._set_automatic_lines(("9",), neutral_id="-1")
        self.app.processEvents()

        item_text = window.geometries_list.item(0).text()
        self.assertIn("| DF |", item_text)
        self.assertNotIn("| DFN |", item_text)
        self.assertEqual(self.automatic_session.result.issues, ())

    def test_automatic_preview_renders_phase_bindings(self) -> None:
        self._set_automatic_lines(("9",))
        window = self._geometries_window()
        window.tabs.setCurrentIndex(1)
        window.geometry_preview.resize(600, 340)
        self.app.processEvents()
        pixmap = QPixmap(window.geometry_preview.size())
        painter = QPainter(pixmap)
        window.geometry_preview.render(painter)
        painter.end()
        self.assertFalse(pixmap.isNull())
        image = pixmap.toImage()
        sampled = {
            image.pixelColor(x, y).rgba()
            for y in range(20, image.height(), 24)
            for x in range(0, image.width(), 20)
        }
        self.assertGreater(len(sampled), 1)
        self.assertEqual(window.geometry_preview.point_count, 3)
        self.assertTrue(window.geometry_preview.point_labels[0].startswith("F1→D"))
        self.assertTrue(window.geometry_preview.point_labels[1].startswith("F2→F"))
        self.assertTrue(all("Cabo:" in item.toolTip() for item in window.geometry_preview._point_items))
        self.assertIn("F1 → D", window.automatic_phases_label.text())
        self.assertIn("F2 → F", window.automatic_phases_label.text())

    def test_cartesian_graph_zoom_pan_fit_and_content_update_do_not_edit_coordinates(self) -> None:
        window = self._geometries_window()
        window.tabs.setCurrentIndex(0)
        arrangement = self.session.catalog.arrangement("cruzeta_8ft_3fn")
        view = window.arrangement_preview
        view.resize(620, 360)
        view.set_content(self.session.catalog, arrangement)
        view.fit_to_content()
        original_positions = tuple((item.x, item.height) for item in arrangement.positions)

        cursor = QPoint(460, 130)
        scene_under_cursor = view.mapToScene(cursor)
        initial_scale = view.transform().m11()
        view.zoom_by_steps(2.0, cursor)
        after_zoom = view.mapToScene(cursor)
        self.assertGreater(view.transform().m11(), initial_scale)
        self.assertAlmostEqual(after_zoom.x(), scene_under_cursor.x(), places=6)
        self.assertAlmostEqual(after_zoom.y(), scene_under_cursor.y(), places=6)

        center_before_pan = view.mapToScene(view.viewport().rect().center())
        view.pan_by_pixels(QPoint(45, -20))
        center_after_pan = view.mapToScene(view.viewport().rect().center())
        self.assertNotAlmostEqual(center_after_pan.x(), center_before_pan.x())
        self.assertTrue(view.camera_modified)
        self.assertEqual(tuple((item.x, item.height) for item in arrangement.positions), original_positions)

        transform_before_edit = view.transform()
        center_before_edit = view.mapToScene(view.viewport().rect().center())
        arrangement.positions[0].x += 0.25
        view.set_content(self.session.catalog, arrangement)
        center_after_edit = view.mapToScene(view.viewport().rect().center())
        self.assertAlmostEqual(view.transform().m11(), transform_before_edit.m11(), places=9)
        self.assertAlmostEqual(view.transform().m22(), transform_before_edit.m22(), places=9)
        self.assertAlmostEqual(center_after_edit.x(), center_before_edit.x(), places=6)
        self.assertAlmostEqual(center_after_edit.y(), center_before_edit.y(), places=6)

        view.zoom_by_steps(1000.0, cursor)
        self.assertEqual(view.zoom_level, view.MAX_ZOOM_LEVEL)
        view.zoom_by_steps(-1000.0, cursor)
        self.assertEqual(view.zoom_level, view.MIN_ZOOM_LEVEL)
        view.fit_to_content()
        self.assertEqual(view.zoom_level, 0.0)
        self.assertFalse(view.camera_modified)

    def test_cartesian_pan_matches_cursor_without_axis_drift_at_every_zoom(self) -> None:
        window = self._geometries_window()
        view = window.arrangement_preview
        arrangement = self.session.catalog.arrangements[0]
        view.resize(620, 360)
        view.set_content(self.session.catalog, arrangement)
        self.app.processEvents()
        scene_point = view._point_items[0].scenePos()

        for zoom in (-8.0, 0.0, 4.0, 12.0):
            view.fit_to_content()
            view.zoom_by_steps(zoom, QPoint(430, 120))
            for delta in (
                QPointF(40.0, 0.0),
                QPointF(0.0, 25.0),
                QPointF(-40.0, -25.0),
                QPointF(3.25, 2.5),
            ):
                before = view.viewportTransform().map(scene_point)
                view.pan_by_pixels(delta)
                after = view.viewportTransform().map(scene_point)
                visual_delta = after - before
                self.assertAlmostEqual(visual_delta.x(), delta.x(), places=8)
                self.assertAlmostEqual(visual_delta.y(), delta.y(), places=8)

    def test_cartesian_pan_preserves_fractional_movements_without_accumulated_error(self) -> None:
        window = self._geometries_window()
        view = window.arrangement_preview
        arrangement = self.session.catalog.arrangements[0]
        view.resize(620, 360)
        view.set_content(self.session.catalog, arrangement)
        view.fit_to_content()
        scene_point = view._point_items[0].scenePos()
        before = view.viewportTransform().map(scene_point)

        delta = QPointF(0.25, -0.4)
        repetitions = 40
        for _ in range(repetitions):
            view.pan_by_pixels(delta)

        visual_delta = view.viewportTransform().map(scene_point) - before
        self.assertAlmostEqual(visual_delta.x(), delta.x() * repetitions, places=8)
        self.assertAlmostEqual(visual_delta.y(), delta.y() * repetitions, places=8)

    def test_real_left_drag_uses_same_pan_in_arrangement_and_automatic_graphs(self) -> None:
        self._set_automatic_lines()
        window = self._geometries_window()
        arrangement = self.session.catalog.arrangements[0]
        assembly = self.automatic_session.result.assemblies[0]
        window.arrangement_preview.set_content(self.session.catalog, arrangement)
        window.geometry_preview.set_content(
            self.automatic_session.catalog,
            assembly.arrangement,
            assembly.geometry,
            assembly.phase_letters,
        )
        original_positions = tuple((item.x, item.height) for item in arrangement.positions)

        start = QPointF(120.25, 90.5)
        end = QPointF(157.75, 76.25)
        expected = end - start
        for view in (
            window.arrangement_preview,
            window.geometry_preview,
        ):
            view.resize(620, 360)
            view.fit_to_content()
            original_zoom = view.zoom_level
            scene_point = view._point_items[0].scenePos()
            before = view.viewportTransform().map(scene_point)
            press = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                start,
                start,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            move = QMouseEvent(
                QEvent.Type.MouseMove,
                end,
                end,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                end,
                end,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            view.mousePressEvent(press)
            self.assertEqual(view.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
            view.mouseMoveEvent(move)
            view.mouseReleaseEvent(release)
            after = view.viewportTransform().map(scene_point)
            visual_delta = after - before
            self.assertAlmostEqual(visual_delta.x(), expected.x(), places=8)
            self.assertAlmostEqual(visual_delta.y(), expected.y(), places=8)
            self.assertEqual(view.cursor().shape(), Qt.CursorShape.OpenHandCursor)
            self.assertFalse(view._dragging)
            self.assertTrue(view.camera_modified)
            self.assertEqual(view.zoom_level, original_zoom)

        self.assertEqual(tuple((item.x, item.height) for item in arrangement.positions), original_positions)

    def test_cartesian_graph_uses_palette_in_light_and_dark_themes(self) -> None:
        window = self._geometries_window()
        view = window.arrangement_preview
        arrangement = self.session.catalog.arrangements[0]
        view.resize(520, 300)
        view.set_content(self.session.catalog, arrangement)
        view.fit_to_content()

        def rendered_corner(base: str, text: str) -> QColor:
            palette = QPalette(view.palette())
            palette.setColor(QPalette.ColorRole.Base, QColor(base))
            palette.setColor(QPalette.ColorRole.Text, QColor(text))
            view.setPalette(palette)
            pixmap = QPixmap(view.size())
            painter = QPainter(pixmap)
            view.render(painter)
            painter.end()
            return pixmap.toImage().pixelColor(4, 75)

        light = rendered_corner("#f8f8f8", "#202020")
        dark = rendered_corner("#202124", "#f4f4f4")
        self.assertGreater(light.lightness(), dark.lightness())

    def test_help_opens_on_requested_section(self) -> None:
        window = self._geometries_window()
        window.help_button.click()
        self.app.processEvents()
        self.assertTrue(self.help.isVisible())
        self.assertEqual(self.help.tabs.currentIndex(), 1)

    def test_dirty_close_supports_cancel_discard_and_save(self) -> None:
        window = self._cables_window()
        window._new_cable()
        with patch(
            "circuit_viewer.opendss_cables_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.assertFalse(window.confirm_pending_changes())
            self.assertTrue(self.session.cables_dirty)

        with patch(
            "circuit_viewer.opendss_cables_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            self.assertTrue(window.confirm_pending_changes())
        self.assertFalse(self.session.cables_dirty)
        self.assertEqual(len(self.session.catalog.cables), 58)

        window._new_cable()
        with patch(
            "circuit_viewer.opendss_cables_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Save,
        ):
            self.assertTrue(window.confirm_pending_changes())
        self.assertTrue(self.cables_path.exists())

    def test_geometry_import_preserves_manual_mountings_only_as_legacy(self) -> None:
        window = self._geometries_window()
        source = Path(self.directory.name) / "custom_geometrias.json"
        source.write_text(
            json.dumps(
                {
                    "versao": 1,
                    "arranjos": [
                        {
                            "id": "a",
                            "nome": "A",
                            "nconds": 1,
                            "nphases": 1,
                            "unidades": "m",
                            "pos": [{"x": 0, "h": 8}],
                        }
                    ],
                    "montagens": [
                        {
                            "id": "g",
                            "nome": "G",
                            "arranjoId": "a",
                            "reduce": False,
                            "cabos": ["ausente"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with (
            patch(
                "circuit_viewer.opendss_geometries_window.QFileDialog.getOpenFileName",
                return_value=(str(source), "JSON (*.json)"),
            ),
            patch(
                "circuit_viewer.opendss_geometries_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch("circuit_viewer.opendss_geometries_window.QMessageBox.warning") as warning,
        ):
            window._import_geometries()
        self.assertEqual(self.session.catalog.geometries, [])
        self.assertEqual(
            self.session.legacy_geometries,
            (GeometryDefinition("g", "G", "a", ["ausente"], False),),
        )
        self.assertFalse(warning.called)
        self.assertTrue(self.session.geometries_dirty)
        self.assertTrue(window._save())
        payload = json.loads(self.geometries_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["montagens"][0]["id"], "g")
