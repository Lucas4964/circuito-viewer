from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWidgets import QApplication, QMessageBox

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover - depende do ambiente
    PYQT_AVAILABLE = False

if PYQT_AVAILABLE:
    from circuit_viewer.curva_chart import CurveChartWidget
    from circuit_viewer.curvas import (
        HOURLY_CURVE_POINT_COUNT,
        CurveCatalog,
        CurveDraft,
    )
    from circuit_viewer.curvas_store import load_curves, save_curves
    from circuit_viewer.curvas_window import CurvesWindow
    from circuit_viewer.main_window import MainWindow


def _application() -> "QApplication":
    return QApplication.instance() or QApplication([])


def _filled_draft(name: str, value: float = 1.0) -> "CurveDraft":
    draft = CurveDraft.new(name)
    for hour_index in range(HOURLY_CURVE_POINT_COUNT):
        draft.set_value(hour_index, value + hour_index)
    return draft


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CurvesWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _application()
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        # O caminho é sempre injetado: sem isso o teste gravaria por cima do
        # curvas.json real de quem está desenvolvendo.
        self.path = Path(self._directory.name) / "curvas.json"

    def _window(self, catalog: "CurveCatalog | None" = None) -> "CurvesWindow":
        window = CurvesWindow(
            catalog if catalog is not None else CurveCatalog(),
            storage_path=self.path,
        )
        self.addCleanup(window.deleteLater)
        # isVisible() de um filho só responde depois que a janela aparece; é o
        # mesmo motivo pelo qual test_cables_ui chama show().
        window.show()
        self.addCleanup(window.hide)
        return window

    def test_empty_state_hides_the_editor(self) -> None:
        window = self._window()
        self.assertTrue(window.empty_label.isVisible())
        self.assertFalse(window.table.isVisible())
        self.assertFalse(window.save_button.isVisible())
        self.assertFalse(window.delete_button.isEnabled())

    def test_new_curve_populates_the_list_and_shows_the_table(self) -> None:
        window = self._window()
        window._on_new_curve()
        self.assertEqual(window.curve_list.count(), 1)
        self.assertTrue(window.table.isVisible())
        self.assertFalse(window.empty_label.isVisible())
        self.assertTrue(window.is_dirty)

    def test_new_curves_get_distinct_names(self) -> None:
        window = self._window()
        window._on_new_curve()
        window._on_new_curve()
        names = [draft.name for draft in window.catalog.drafts]
        self.assertEqual(len(set(names)), 2)

    def test_rename_updates_the_list_without_changing_the_id(self) -> None:
        catalog = CurveCatalog([_filled_draft("Antiga")])
        window = self._window(catalog)
        original_id = catalog.draft(0).curve_id
        window.name_edit.setText("Nova")
        window._on_name_edited("Nova")
        self.assertEqual(window.curve_list.item(0).text(), "Nova")
        self.assertEqual(catalog.draft(0).curve_id, original_id)
        self.assertTrue(window.is_dirty)

    def test_duplicate_name_is_reported_but_not_blocked(self) -> None:
        catalog = CurveCatalog([_filled_draft("A"), _filled_draft("B")])
        window = self._window(catalog)
        window.curve_list.setCurrentRow(1)
        window._on_name_edited("A")
        self.assertIn("Já existe", window.status_label.text())
        self.assertEqual(catalog.draft(1).name, "A")

    def test_delete_respects_cancel(self) -> None:
        catalog = CurveCatalog([_filled_draft("A")])
        window = self._window(catalog)
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            window._on_delete_curve()
        self.assertEqual(len(catalog), 1)

    def test_delete_removes_on_confirmation(self) -> None:
        catalog = CurveCatalog([_filled_draft("A")])
        window = self._window(catalog)
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window._on_delete_curve()
        self.assertEqual(len(catalog), 0)
        self.assertTrue(window.is_dirty)
        self.assertTrue(window.empty_label.isVisible())

    def test_save_refuses_an_incomplete_curve(self) -> None:
        catalog = CurveCatalog([CurveDraft.new("Incompleta")])
        window = self._window(catalog)
        self.assertFalse(window._save())
        self.assertIn("Faltam valores", window.status_label.text())
        self.assertFalse(self.path.exists())

    def test_save_writes_and_clears_dirty(self) -> None:
        catalog = CurveCatalog([_filled_draft("Boa")])
        window = self._window(catalog)
        window._mark_dirty()
        received: list[int] = []
        window.curvesSaved.connect(received.append)
        self.assertTrue(window._save())
        self.assertFalse(window.is_dirty)
        self.assertEqual(received, [1])
        self.assertEqual(load_curves(self.path).curves[0].name, "Boa")

    def test_editing_a_cell_updates_the_chart(self) -> None:
        catalog = CurveCatalog([_filled_draft("A")])
        window = self._window(catalog)
        index = window.table_model.index(0, 1)
        window.table_model.setData(index, "99")
        self.assertEqual(window.chart.values[0], 99.0)
        self.assertTrue(window.is_dirty)

    def test_selecting_another_curve_loads_its_values(self) -> None:
        catalog = CurveCatalog(
            [_filled_draft("A", 1.0), _filled_draft("B", 100.0)]
        )
        window = self._window(catalog)
        window.curve_list.setCurrentRow(1)
        self.assertEqual(window.name_edit.text(), "B")
        self.assertEqual(window.chart.values[0], 100.0)

    def test_close_without_changes_does_not_ask(self) -> None:
        window = self._window(CurveCatalog([_filled_draft("A")]))
        with patch.object(QMessageBox, "question") as question:
            self.assertTrue(window.close())
            question.assert_not_called()

    def test_close_cancel_keeps_the_window_open(self) -> None:
        window = self._window(CurveCatalog([_filled_draft("A")]))
        window._mark_dirty()
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.assertFalse(window.close())
        self.assertTrue(window.is_dirty)

    def test_close_discard_reloads_from_disk(self) -> None:
        """Sem a releitura, reabrir mostraria o que o usuário descartou."""

        catalog = CurveCatalog([_filled_draft("Gravada")])
        window = self._window(catalog)
        self.assertTrue(window._save())
        window._on_name_edited("Editada sem salvar")
        self.assertTrue(window.is_dirty)
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Discard,
        ):
            self.assertTrue(window.close())
        self.assertFalse(window.is_dirty)
        self.assertEqual(window.catalog.draft(0).name, "Gravada")

    def test_close_save_writes(self) -> None:
        window = self._window(CurveCatalog([_filled_draft("A")]))
        window._mark_dirty()
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Save,
        ):
            self.assertTrue(window.close())
        self.assertTrue(self.path.exists())

    def test_close_save_refuses_when_invalid(self) -> None:
        window = self._window(CurveCatalog([CurveDraft.new("Incompleta")]))
        window._mark_dirty()
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Save,
        ):
            self.assertFalse(window.close())
        self.assertFalse(self.path.exists())

    def test_reload_after_external_save(self) -> None:
        window = self._window(CurveCatalog([_filled_draft("A")]))
        self.assertTrue(window._save())
        save_curves((), self.path)
        window._reload_from_disk()
        self.assertEqual(len(window.catalog), 0)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CurvesMenuTests(unittest.TestCase):
    """A entrada de menu existe e não depende de nenhuma importação."""

    def setUp(self) -> None:
        self.app = _application()
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "curvas.json"

    def _main_window(self) -> "MainWindow":
        window = MainWindow(curves_path=self.path)
        self.addCleanup(window.close)
        window.show()
        return window

    def test_menu_entry_exists_and_is_enabled(self) -> None:
        window = self._main_window()
        settings_menu = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Configurações"
        )
        self.assertIn(window.curves_action, settings_menu.actions())
        self.assertTrue(window.curves_action.isEnabled())

    def test_opening_the_window_needs_no_imported_data(self) -> None:
        window = self._main_window()
        window._show_curves_window()
        self.assertTrue(window.curves_window.isVisible())

    def test_saved_curves_come_back_on_the_next_run(self) -> None:
        first = self._main_window()
        first.curves_window.catalog.add(_filled_draft("Persistente"))
        self.assertTrue(first.curves_window._save())

        second = MainWindow(curves_path=self.path)
        self.addCleanup(second.close)
        self.assertEqual(len(second.curve_catalog), 1)
        self.assertEqual(second.curve_catalog.draft(0).name, "Persistente")

    def test_corrupted_file_does_not_prevent_startup(self) -> None:
        """O aviso é modal: processar os eventos sob o patch evita travar."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ nao e json", encoding="utf-8")
        with patch.object(QMessageBox, "warning") as warning:
            window = MainWindow(curves_path=self.path)
            self.addCleanup(window.close)
            window.show()
            self.app.processEvents()

        self.assertEqual(warning.call_count, 1)
        self.assertEqual(len(window.curve_catalog), 0)
        self.assertIsNotNone(window._curves_load.issue)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CurveChartTests(unittest.TestCase):
    """O gráfico precisa pintar sem levantar em todos os casos-limite."""

    def setUp(self) -> None:
        self.app = _application()

    def _render(self, values: list[float | None]) -> None:
        chart = CurveChartWidget()
        self.addCleanup(chart.deleteLater)
        chart.resize(400, 200)
        chart.set_values(values)
        pixmap = QPixmap(chart.size())
        chart.render(pixmap)

    def test_paints_when_completely_empty(self) -> None:
        self._render([None] * HOURLY_CURVE_POINT_COUNT)

    def test_paints_when_all_values_are_equal(self) -> None:
        self._render([2.5] * HOURLY_CURVE_POINT_COUNT)

    def test_paints_when_all_values_are_zero(self) -> None:
        """Faixa nula: sem a folga artificial isto seria divisão por zero."""

        self._render([0.0] * HOURLY_CURVE_POINT_COUNT)

    def test_paints_a_negative_range(self) -> None:
        self._render([float(v) for v in range(-12, 12)])

    def test_paints_with_gaps(self) -> None:
        values: list[float | None] = [1.0] * HOURLY_CURVE_POINT_COUNT
        values[5] = None
        values[6] = None
        self._render(values)

    def test_paints_when_tiny(self) -> None:
        chart = CurveChartWidget()
        self.addCleanup(chart.deleteLater)
        chart.resize(10, 10)
        chart.set_values([1.0] * HOURLY_CURVE_POINT_COUNT)
        chart.render(QPixmap(chart.size()))

    def test_set_values_is_a_no_op_when_unchanged(self) -> None:
        chart = CurveChartWidget()
        self.addCleanup(chart.deleteLater)
        values = [1.0] * HOURLY_CURVE_POINT_COUNT
        chart.set_values(values)
        chart.set_values(list(values))
        self.assertEqual(chart.values, tuple(values))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
