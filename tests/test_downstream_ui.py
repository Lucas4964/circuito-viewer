from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QMenu

    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.downstream_table import (
        CARGA,
        CHAVE,
        DOWNSTREAM_CSV_HEADERS,
        DOWNSTREAM_KINDS,
        DOWNSTREAM_TABLE_HEADERS,
        TRECHO,
    )
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import FeatureSelection
    from circuit_viewer.segment_import import SegmentLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt
    PYQT_AVAILABLE = False

from test_block_analysis import (
    make_bars,
    make_catalog,
    make_loads,
    make_network,
    make_switches,
)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class DownstreamUiTests(unittest.TestCase):
    """B0 —T0— B1 —T1(chave)— B2 —T2— B3, com B1 —T3— B4."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "fases2.json"
        self.config_path.write_text(
            json.dumps([{"FASES2": "13", "NUMERO_FASES": 3}]),
            encoding="utf-8",
        )

    def make_window(self, switch_state: str = "1"):  # noqa: ANN201
        bars = make_bars(5)
        network = make_network(bars, [0, 1, 2, 1], [1, 2, 3, 4])
        switches = make_switches(network, [(1, "1", switch_state)])
        window = MainWindow(self.config_path)
        self.addCleanup(window.close)
        window.show()
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 5, 5, 0, (), 0))
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 4, 4, 0, (), 0)
        )
        window._set_switch_model(switches)
        window._set_circuit_catalog(make_catalog(network, switches))
        self.app.processEvents()
        return window, bars, network, switches

    # -- entradas -------------------------------------------------------------

    def test_the_action_lives_in_the_tools_menu_and_needs_a_network(self) -> None:
        empty = MainWindow(self.config_path)
        self.addCleanup(empty.close)
        tools = next(
            entry.menu()
            for entry in empty.menuBar().actions()
            if entry.text() == "Ferramentas"
        )
        self.assertIn(empty.downstream_action, tools.actions())
        self.assertFalse(empty.downstream_action.isEnabled())

        window, *_ = self.make_window()
        self.assertTrue(window.downstream_action.isEnabled())

    def test_the_map_menu_offers_it_for_a_segment_and_for_a_bar(self) -> None:
        window, *_ = self.make_window()
        for feature in (FeatureSelection("segment", 1), FeatureSelection("bar", 1)):
            with self.subTest(feature.kind):
                menu = QMenu()
                window._add_downstream_action(menu, feature)
                texts = [action.text() for action in menu.actions()]
                self.assertEqual(texts, ["Selecionar a jusante daqui"])

    def test_triggering_the_map_action_selects_downstream(self) -> None:
        window, *_ = self.make_window()
        menu = QMenu()
        window._add_downstream_action(menu, FeatureSelection("segment", 1))
        menu.actions()[0].trigger()
        self.assertIsNotNone(window._downstream_selection)
        self.assertEqual(window._downstream_selection.origin.index, 1)
        self.assertTrue(window.downstream_window.isVisible())

    def test_opening_from_the_menu_starts_from_the_map_selection(self) -> None:
        window, *_ = self.make_window()
        window._set_selection(FeatureSelection("bar", 1))
        window._show_downstream_window()
        self.assertEqual(window._downstream_selection.origin.kind, "bar")
        self.assertEqual(
            sorted(window._downstream_selection.bar_indices.tolist()),
            [1, 2, 3, 4],
        )

    # -- destaque ---------------------------------------------------------------

    def test_selecting_highlights_the_downstream_segments_in_yellow(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        overlay = window.downstream_highlight_overlay
        self.assertTrue(overlay.isVisible())
        self.assertEqual(sorted(overlay.segment_indices), [1, 2])

    def test_the_downstream_and_block_highlights_exclude_each_other(self) -> None:
        window, *_ = self.make_window()
        window._show_blocks()
        block = window.block_table_model.result.records[0]
        window._select_block(block)
        self.assertTrue(window.block_highlight_overlay.isVisible())

        window._select_downstream_from("segment", 1)
        self.assertFalse(window.block_highlight_overlay.isVisible())
        self.assertTrue(window.downstream_highlight_overlay.isVisible())

        window._select_block(block)
        self.assertFalse(window.downstream_highlight_overlay.isVisible())
        # A região continua na janela, pronta para ser destacada de novo.
        self.assertIsNotNone(window.downstream_window.selection)

    def test_escape_clears_the_highlight_before_the_selection(self) -> None:
        window, *_ = self.make_window()
        window._set_selection(FeatureSelection("segment", 1))
        window._select_downstream_from("segment", 1)

        window._escape_pressed()
        self.assertFalse(window.downstream_highlight_overlay.isVisible())
        self.assertIsNotNone(window._selected_feature)

        window._escape_pressed()
        self.assertIsNone(window._selected_feature)

    def test_clicking_inside_the_region_keeps_the_highlight(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("bar", 1)
        window._set_selection(FeatureSelection("segment", 2))
        self.assertTrue(window.downstream_highlight_overlay.isVisible())

    def test_clicking_outside_the_region_clears_the_highlight(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        window._set_selection(FeatureSelection("segment", 0))
        self.assertFalse(window.downstream_highlight_overlay.isVisible())

    # -- filtros ----------------------------------------------------------------

    def test_the_filters_drive_the_table_and_the_highlight(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        downstream = window.downstream_window

        downstream.kind_checks[TRECHO].setChecked(False)
        self.app.processEvents()

        kinds = {row.kind for row in downstream.table_model.rows}
        self.assertNotIn(TRECHO, kinds)
        self.assertIn(CHAVE, kinds)
        # Só a chave acende: o trecho que ela ocupa.
        self.assertEqual(
            list(window.downstream_highlight_overlay.segment_indices), [1]
        )

    def test_the_table_starts_in_kind_then_level_order(self) -> None:
        # Ligar a ordenação faria o Qt reordenar pela coluna TIPO na hora, e a
        # ordem exibida é também a do CSV.
        window, *_ = self.make_window()
        window._select_downstream_from("bar", 0)
        rows = window.downstream_window.visible_rows_in_display_order()
        kinds = [row.kind for row in rows]
        self.assertEqual(kinds, sorted(kinds, key=DOWNSTREAM_KINDS.index))
        segment_levels = [row.level for row in rows if row.kind == TRECHO]
        self.assertEqual(segment_levels, sorted(segment_levels))

    def test_the_columns_fit_their_contents(self) -> None:
        """A última coluna não estica para ocupar a sobra da janela."""

        window, *_ = self.make_window()
        window._select_downstream_from("bar", 0)
        table = window.downstream_window.table
        self.assertFalse(table.horizontalHeader().stretchLastSection())
        info = DOWNSTREAM_TABLE_HEADERS.index("INFO")
        fitted = max(
            table.sizeHintForColumn(info),
            table.horizontalHeader().sectionSizeHint(info),
        )
        self.assertLessEqual(table.columnWidth(info), fitted + 2)

    def test_the_window_opens_as_wide_as_its_table(self) -> None:
        window, *_ = self.make_window()
        downstream = window.downstream_window
        window._select_downstream_from("bar", 0)
        header_width = downstream.table.horizontalHeader().length()
        # A tabela inteira cabe, e a janela não sobra além da tabela — a não
        # ser pelo mínimo do próprio diálogo, que depende das fontes.
        self.assertGreaterEqual(downstream.width(), header_width)
        self.assertLessEqual(
            downstream.width(),
            max(header_width + 80, downstream.minimumSizeHint().width()),
        )

    def test_a_new_region_does_not_resize_the_window_again(self) -> None:
        window, *_ = self.make_window()
        downstream = window.downstream_window
        window._select_downstream_from("bar", 0)
        # Acima do mínimo do diálogo, que depende das fontes do ambiente.
        chosen = downstream.minimumSizeHint().width() + 150
        downstream.resize(chosen, downstream.height())
        window._select_downstream_from("segment", 1)
        self.assertEqual(downstream.width(), chosen)

    def test_the_segment_info_shows_the_phase_name(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        segment = next(
            row
            for row in window.downstream_window.table_model.rows
            if row.kind == TRECHO
        )
        # O fases2.json deste teste relaciona "13" sem NOME: fica o código.
        self.assertEqual(segment.info, "13")

    def test_a_kind_without_a_model_is_disabled(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        check = window.downstream_window.kind_checks[CARGA]
        self.assertFalse(check.isEnabled())
        self.assertIn("cargas", check.toolTip())

    # -- chave aberta -------------------------------------------------------------

    def test_the_beyond_option_recomputes_the_region(self) -> None:
        window, *_ = self.make_window(switch_state="0")
        window._select_downstream_from("bar", 0)
        self.assertEqual(
            sorted(window._downstream_selection.bar_indices.tolist()), [0, 1, 4]
        )

        window.downstream_window.beyond_check.setChecked(True)
        self.app.processEvents()
        self.assertEqual(
            sorted(window._downstream_selection.bar_indices.tolist()),
            [0, 1, 2, 3, 4],
        )

    def test_an_open_switch_origin_explains_the_empty_region(self) -> None:
        window, *_ = self.make_window(switch_state="0")
        window._select_downstream_from("segment", 1)
        text = window.downstream_window.issues_text.toPlainText()
        self.assertIn("está aberto", text)
        self.assertTrue(window.downstream_window.issues_text.isVisible())

    # -- ciclo de vida --------------------------------------------------------------

    def test_the_orientation_is_memoized_by_identity(self) -> None:
        window, *_ = self.make_window()
        first = window._ensure_network_orientation()
        self.assertIs(window._ensure_network_orientation(), first)

    def test_new_segments_drop_the_region_and_the_cache(self) -> None:
        window, bars, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        replacement = make_network(bars, [0], [1])

        window._set_line_model(replacement)

        self.assertIsNone(window._downstream_selection)
        self.assertIsNone(window._network_orientation)
        self.assertFalse(window.downstream_highlight_overlay.isVisible())
        self.assertIsNone(window.downstream_window.selection)

    def test_new_loads_keep_the_region_and_refresh_its_content(self) -> None:
        window, bars, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        self.assertEqual(window._downstream_selection.load_indices.size, 0)

        window._set_load_model(make_loads(bars, [3, 0], ["75", "30"]))

        selection = window._downstream_selection
        self.assertIsNotNone(selection)
        self.assertEqual(selection.origin.index, 1)
        self.assertEqual(selection.load_indices.tolist(), [0])
        self.assertTrue(window.downstream_window.kind_checks[CARGA].isEnabled())

    def test_recoloring_the_circuits_keeps_the_region(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        window._set_circuit_catalog(
            window._circuit_catalog, colors=("#123456",)
        )
        self.assertIsNotNone(window._downstream_selection)

    def test_activating_a_row_selects_it_without_losing_the_highlight(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("bar", 1)
        row = next(
            row
            for row in window.downstream_window.table_model.rows
            if row.kind == TRECHO and row.index == 2
        )
        window._activate_downstream_row(row)
        self.assertEqual(window._selected_feature, FeatureSelection("segment", 2))
        self.assertTrue(window.downstream_highlight_overlay.isVisible())

    # -- CSV --------------------------------------------------------------------

    def test_the_csv_export_writes_the_requested_columns(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        target = self.root / "jusante.csv"
        with patch(
            "circuit_viewer.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), "Arquivos CSV (*.csv)"),
        ):
            window.downstream_window.export_csv_button.click()

        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream, delimiter=";"))
        self.assertEqual(tuple(rows[0]), DOWNSTREAM_CSV_HEADERS)
        self.assertEqual(len(rows[0]), len(DOWNSTREAM_TABLE_HEADERS))
        exported = {(row[3], row[0]) for row in rows[1:]}
        self.assertIn((TRECHO, "T1"), exported)
        self.assertIn((CHAVE, "CH0"), exported)
        self.assertEqual(
            len(rows) - 1, len(window.downstream_window.table_model.rows)
        )

    def test_the_csv_follows_the_filters(self) -> None:
        window, *_ = self.make_window()
        window._select_downstream_from("segment", 1)
        for kind, check in window.downstream_window.kind_checks.items():
            check.setChecked(kind == CHAVE)
        target = self.root / "chaves.csv"
        with patch(
            "circuit_viewer.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), "Arquivos CSV (*.csv)"),
        ):
            window.downstream_window.export_csv_button.click()
        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream, delimiter=";"))
        self.assertEqual([row[3] for row in rows[1:]], [CHAVE])


if __name__ == "__main__":
    unittest.main()
