from __future__ import annotations

import csv
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QPoint, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QAbstractItemView, QMessageBox

    from circuit_viewer.branch_analysis import analyze_branches
    from circuit_viewer.branch_power_source import BranchPowerSource
    from circuit_viewer.equivalent_network import build_equivalent_network
    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import (
        CircuitCatalogModel,
        CircuitDefinition,
        CircuitModel,
        FeatureSelection,
        LineNetworkModel,
        LoadModel,
        LoadPatternModel,
        LoadPatternRecord,
        SwitchModel,
        UtmCrs,
    )
    from circuit_viewer.phase_config import load_phase_configuration
    from circuit_viewer.segment_import import SegmentLoadResult
    from circuit_viewer.switch_import import SwitchLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class BranchesUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "fases2.json"
        self.config_path.write_text(
            json.dumps(
                [
                    {"FASES2": "D", "NOME": "D", "NUMERO_FASES": 1},
                    {"FASES2": "E", "NOME": "E", "NUMERO_FASES": 1},
                    {"FASES2": "AB", "NOME": "AB", "NUMERO_FASES": 2},
                    {"FASES2": "DEF", "NOME": "DEF", "NUMERO_FASES": 3},
                ]
            ),
            encoding="utf-8",
        )

    def make_window(
        self,
        *,
        two_circuits: bool = False,
        biphasic: bool = False,
        switch_on_branch: bool = False,
    ):
        bars = CircuitModel(
            ["B0", "B1", "B2", "B3", "B4"],
            ["CB0", "CB1", "CB2", "CB3", "CB4"],
            [500_000.0, 500_100.0, 500_200.0, 500_100.0, 500_100.0],
            [8_000_000.0, 8_000_000.0, 8_000_000.0, 7_999_900.0, 7_999_800.0],
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
            bars,
            ["T0", "T1", "T2", "T3"],
            ["CT0", "CT1", "CT2", "CT3"],
            ["DEF", "DEF", "AB" if biphasic else "D", "D"],
            [0, 1, 1, 3],
            [1, 2, 3, 4],
            [""] * 4,
            [""] * 4,
            [""] * 4,
            [100.0] * 4,
        )
        switches = None
        if switch_on_branch:
            switches = SwitchModel(
                segments,
                ["CH1"],
                ["TIPO"],
                ["C1"],
                [3],
                ["CCH1"],
                ["1"],
                ["1"],
                [""],
                [""],
                [""],
            )
        definitions = [CircuitDefinition("C1", "B0", "", "")]
        if two_circuits:
            definitions.append(CircuitDefinition("C2", "B0", "", ""))
        catalog = CircuitCatalogModel.build(segments, switches, definitions)
        window = MainWindow(self.config_path)
        self.addCleanup(window.close)
        window.show()
        window._on_import_finished(
            CsvLoadResult(bars, "utf-8", 5, 5, 0, (), 0)
        )
        window._on_segment_import_finished(
            SegmentLoadResult(segments, "utf-8", 4, 4, 0, (), 0)
        )
        if switches is not None:
            window._on_switch_import_finished(
                SwitchLoadResult(switches, "utf-8", 1, 1, 0, (), 0)
            )
        window._set_circuit_catalog(catalog)
        self.app.processEvents()
        return window, bars, segments, catalog

    def wait_for_analysis(self, window: MainWindow) -> None:
        deadline = time.monotonic() + 3.0
        while window._branch_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertIsNone(window._branch_thread)

    def wait_for_equivalent(self, window: MainWindow) -> None:
        deadline = time.monotonic() + 3.0
        while window._equivalent_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertIsNone(window._equivalent_thread)

    def wait_for_json_export(self, window: MainWindow) -> None:
        deadline = time.monotonic() + 3.0
        while window._branch_json_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertIsNone(window._branch_json_thread)

    def wait_for_csv_export(self, window: MainWindow) -> None:
        deadline = time.monotonic() + 3.0
        while window._branch_csv_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertIsNone(window._branch_csv_thread)

    def test_tools_menu_runs_background_analysis_and_populates_table(self) -> None:
        empty = MainWindow(self.config_path)
        self.addCleanup(empty.close)
        tools_menu = next(
            action.menu()
            for action in empty.menuBar().actions()
            if action.text() == "Ferramentas"
        )
        self.assertIn(empty.branches_action, tools_menu.actions())
        self.assertFalse(empty.branches_action.isEnabled())

        window, _, _, _ = self.make_window()
        self.assertTrue(window.branches_action.isEnabled())

        window._show_or_analyze_branches()
        self.wait_for_analysis(window)
        self.wait_for_equivalent(window)

        self.assertIsNotNone(window._branch_analysis_result)
        self.assertTrue(window.branches_window.isVisible())
        self.assertFalse(window.branches_window.isModal())
        self.assertFalse(window.simplified_network_action.isChecked())
        self.assertTrue(window.branches_window.export_json_button.isEnabled())
        self.assertTrue(window.branches_window.export_csv_button.isEnabled())
        model = window.branch_table_model
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.columnCount(), 24)
        self.assertEqual(
            model.headerData(6, Qt.Orientation.Horizontal),
            "NIVEL_TOPOLOGICO",
        )
        self.assertEqual(model.headerData(14, Qt.Orientation.Horizontal), "DEMANDA_MAXIMA")
        self.assertEqual(model.data(model.index(0, 1)), "1")
        self.assertEqual(model.data(model.index(0, 2)), "MONOFASICO")
        self.assertEqual(model.data(model.index(0, 3)), "C1")
        self.assertEqual(model.data(model.index(0, 4)), "B1")
        self.assertEqual(model.data(model.index(0, 6)), "1")
        self.assertEqual(model.data(model.index(0, 7)), "T2")
        self.assertEqual(model.data(model.index(0, 9)), "—")
        self.assertEqual(model.data(model.index(0, 11)), "2")
        self.assertEqual(model.data(model.index(0, 12)), "200.000")
        self.assertEqual(model.data(model.index(0, 14)), "0.0000")
        self.assertEqual(model.data(model.index(0, 15)), "D")
        self.assertEqual(model.data(model.index(0, 16)), "D")

        cached = window._branch_analysis_result
        window._show_or_analyze_branches()
        self.assertIs(window._branch_analysis_result, cached)
        self.assertIsNone(window._branch_thread)

    def test_measured_mode_refused_leaves_nothing_pending(self) -> None:
        window, _, _, _ = self.make_window()
        window._branch_power_source = BranchPowerSource.POWER_FLOW

        with patch(
            "circuit_viewer.main_window.power_flow_import_error",
            return_value=None,
        ), patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            window._show_or_analyze_branches()
            self.wait_for_analysis(window)

        self.assertTrue(question.called)
        self.assertIsNotNone(window._branch_analysis_result)
        self.assertIsNone(window._equivalent_thread)
        self.assertIsNone(window._equivalent_network_result)
        self.assertFalse(window._pending_power_flow_for_equivalent)
        self.assertFalse(window._pending_branch_metrics)

    def test_measured_mode_accepted_runs_the_power_flow_and_resumes(self) -> None:
        window, _, _, _ = self.make_window()
        window._branch_power_source = BranchPowerSource.POWER_FLOW

        def fake_run(self_) -> None:  # noqa: ANN001
            # A execução real cria a thread; aqui basta o sinal de que começou.
            self_._power_flow_thread = object()

        with patch(
            "circuit_viewer.main_window.power_flow_import_error",
            return_value=None,
        ), patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(MainWindow, "_run_power_flow", fake_run):
            window._show_or_analyze_branches()
            self.wait_for_analysis(window)

        self.assertIsNotNone(window._power_flow_thread)
        self.assertTrue(window._pending_power_flow_for_equivalent)
        self.assertIsNone(window._equivalent_thread)

        # Chegando o resultado, a construção interrompida é retomada.
        window._power_flow_thread = None
        window._power_flow_result = object()
        with patch.object(window, "_start_equivalent_build") as resume:
            window._on_power_flow_thread_finished()
            self.app.processEvents()

        resume.assert_called_once_with()
        self.assertFalse(window._pending_power_flow_for_equivalent)

    def test_equivalent_build_held_by_a_running_flow_resumes_after_it(self) -> None:
        window, _, _, _ = self.make_window()
        window._show_or_analyze_branches()
        self.wait_for_analysis(window)
        self.wait_for_equivalent(window)
        window._equivalent_network_result = None
        window._pending_branch_metrics = True

        # Exclusão mútua: com o fluxo em execução a construção não começa.
        window._power_flow_thread = object()
        window._start_equivalent_build()
        held_thread = window._equivalent_thread
        window._power_flow_thread = None

        self.assertIsNone(held_thread)
        self.assertTrue(window._pending_branch_metrics)

        window._on_power_flow_thread_finished()
        self.app.processEvents()
        self.wait_for_equivalent(window)

        self.assertIsNotNone(window._equivalent_network_result)
        self.assertFalse(window._pending_branch_metrics)

    def test_measured_mode_without_the_engine_explains_and_stops(self) -> None:
        window, _, _, _ = self.make_window()
        window._branch_power_source = BranchPowerSource.POWER_FLOW

        with patch(
            "circuit_viewer.main_window.power_flow_import_error",
            return_value="A biblioteca py-dss-interface não está disponível",
        ), patch.object(QMessageBox, "information") as information:
            window._show_or_analyze_branches()
            self.wait_for_analysis(window)

        information.assert_called_once()
        self.assertIn("py-dss-interface", information.call_args.args[2])
        self.assertIsNone(window._equivalent_thread)
        self.assertIsNone(window._equivalent_network_result)
        self.assertFalse(window._pending_branch_metrics)

    def test_json_button_exports_only_the_filtered_circuit(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        equivalent = build_equivalent_network(branches, None)
        window._branch_analysis_result = branches
        window._equivalent_network_result = equivalent
        window.branches_window.set_result(branches)
        window.branches_window.set_equivalent_result(equivalent)
        window._show_branches_window()
        window.branch_table_model.setData(
            window.branch_table_model.index(0, 0),
            Qt.CheckState.Checked,
            Qt.ItemDataRole.CheckStateRole,
        )
        window.branch_table_model.setData(
            window.branch_table_model.index(1, 0),
            Qt.CheckState.Checked,
            Qt.ItemDataRole.CheckStateRole,
        )
        window.branches_window.circuit_filter.setCurrentIndex(1)
        self.app.processEvents()
        target = Path(self.temp.name) / "circuito.json"

        with patch(
            "circuit_viewer.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), "Arquivos JSON (*.json)"),
        ):
            window.branches_window.export_json_button.click()
            self.wait_for_json_export(window)

        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(list(payload), ["ramais_interesse", "RAMAL-1"])
        self.assertEqual(payload["ramais_interesse"], [1])
        self.assertEqual(payload["RAMAL-1"]["barra_inicio"], "CB1")

    def test_all_circuits_filter_exposes_every_branch_for_json(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        equivalent = build_equivalent_network(branches, None)
        window._branch_analysis_result = branches
        window._equivalent_network_result = equivalent
        window.branches_window.set_result(branches)
        window.branches_window.set_equivalent_result(equivalent)

        self.assertIsNone(window.branches_window.selected_circuit_id())
        self.assertEqual(window.branches_window.visible_source_rows(), (0, 1))
        self.assertTrue(window.branches_window.export_json_button.isEnabled())
        self.assertTrue(window.branches_window.export_csv_button.isEnabled())

        window.branches_window.set_csv_export_pending(True)
        self.assertFalse(window.branches_window.export_json_button.isEnabled())
        self.assertFalse(window.branches_window.export_csv_button.isEnabled())
        window.branches_window.set_csv_export_pending(False)
        self.assertTrue(window.branches_window.export_json_button.isEnabled())
        self.assertTrue(window.branches_window.export_csv_button.isEnabled())

    def test_interest_checkboxes_survive_sort_filter_and_reset_on_new_result(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        branch_window = window.branches_window
        model = window.branch_table_model
        branch_window.set_result(branches)
        branch_window.show()
        self.app.processEvents()

        check = model.index(0, 0)
        self.assertTrue(model.flags(check) & Qt.ItemFlag.ItemIsUserCheckable)
        proxy_check = branch_window.proxy_model.index(0, 0)
        QTest.mouseClick(
            branch_window.table.viewport(),
            Qt.MouseButton.LeftButton,
            pos=branch_window.table.visualRect(proxy_check).center(),
        )
        self.app.processEvents()
        self.assertEqual(model.interest_branch_ids(), (1,))
        branch_window.proxy_model.sort(1, Qt.SortOrder.DescendingOrder)
        branch_window.circuit_filter.setCurrentIndex(2)
        self.app.processEvents()
        self.assertEqual(model.interest_branch_ids(), (1,))
        self.assertEqual(
            branch_window.interest_branch_ids_for_source_rows(
                branch_window.visible_source_rows()
            ),
            (),
        )

        model.set_result(branches)
        self.assertEqual(model.interest_branch_ids(), ())

    def test_current_row_is_highlighted_across_every_column(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        branch_window = window.branches_window
        branch_window.set_result(branches)
        model = window.branch_table_model
        self.app.processEvents()

        self.assertEqual(model.highlight_row(), -1)

        branch_window.table.setCurrentIndex(branch_window.proxy_model.index(1, 3))
        self.app.processEvents()

        highlighted = model.highlight_row()
        self.assertGreaterEqual(highlighted, 0)
        for column in range(model.columnCount()):
            self.assertIsNotNone(
                model.data(
                    model.index(highlighted, column),
                    Qt.ItemDataRole.BackgroundRole,
                ),
                f"coluna {column} sem faixa na linha corrente",
            )
        for row in range(model.rowCount()):
            if row == highlighted:
                continue
            self.assertIsNone(
                model.data(
                    model.index(row, 0),
                    Qt.ItemDataRole.BackgroundRole,
                )
            )

    def test_highlight_maps_visible_row_to_source_row_when_sorted(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        branch_window = window.branches_window
        branch_window.set_result(branches)
        model = window.branch_table_model
        self.assertGreater(model.rowCount(), 1)

        branch_window.proxy_model.sort(1, Qt.SortOrder.DescendingOrder)
        self.app.processEvents()
        branch_window.table.setCurrentIndex(branch_window.proxy_model.index(0, 1))
        self.app.processEvents()

        expected = branch_window.proxy_model.mapToSource(
            branch_window.proxy_model.index(0, 1)
        ).row()
        self.assertEqual(model.highlight_row(), expected)
        self.assertEqual(
            model.record(expected).branch_id,
            max(record.branch_id for record in branches.records),
        )

    def test_clear_selection_drops_the_highlight(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        branch_window = window.branches_window
        branch_window.set_result(branches)
        model = window.branch_table_model
        branch_window.table.setCurrentIndex(branch_window.proxy_model.index(0, 1))
        self.app.processEvents()
        self.assertGreaterEqual(model.highlight_row(), 0)

        branch_window.clear_selection()
        self.app.processEvents()

        self.assertEqual(model.highlight_row(), -1)
        self.assertIsNone(
            model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole)
        )

    def test_set_result_resets_the_highlight(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        branch_window = window.branches_window
        branch_window.set_result(branches)
        model = window.branch_table_model
        branch_window.table.setCurrentIndex(branch_window.proxy_model.index(0, 1))
        self.app.processEvents()
        self.assertGreaterEqual(model.highlight_row(), 0)

        model.set_result(branches)

        self.assertEqual(model.highlight_row(), -1)

    def test_table_copies_selected_cells_as_tsv_without_checkbox(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        branch_window = window.branches_window
        branch_window.set_result(branches)
        branch_window.proxy_model.sort(1, Qt.SortOrder.AscendingOrder)
        self.app.processEvents()

        self.assertEqual(
            branch_window.table.selectionBehavior(),
            QAbstractItemView.SelectionBehavior.SelectItems,
        )
        self.assertEqual(
            branch_window.table.selectionMode(),
            QAbstractItemView.SelectionMode.ExtendedSelection,
        )
        self.assertEqual(
            branch_window.table.editTriggers(),
            QAbstractItemView.EditTrigger.NoEditTriggers,
        )
        selection = QItemSelection(
            branch_window.proxy_model.index(0, 0),
            branch_window.proxy_model.index(1, 3),
        )
        branch_window.table.selectionModel().select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )

        branch_window.table.copy_selection()

        self.assertEqual(
            QApplication.clipboard().text(),
            "1\tMONOFASICO\tC1\n2\tMONOFASICO\tC2",
        )

    def test_csv_button_preserves_visual_filter_and_sort_order(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        window._branch_analysis_result = branches
        window.branches_window.set_result(branches)
        proxy = window.branches_window.proxy_model
        proxy.sort(1, Qt.SortOrder.DescendingOrder)
        self.app.processEvents()
        target = Path(self.temp.name) / "todos.csv"

        with patch(
            "circuit_viewer.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), "Arquivos CSV (*.csv)"),
        ):
            window.branches_window.export_csv_button.click()
            self.wait_for_csv_export(window)

        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream, delimiter=";"))
        self.assertEqual([row[0] for row in rows[1:]], ["2", "1"])

        window.branches_window.circuit_filter.setCurrentIndex(1)
        self.app.processEvents()
        filtered_target = Path(self.temp.name) / "filtrado.csv"
        with patch(
            "circuit_viewer.main_window.QFileDialog.getSaveFileName",
            return_value=(str(filtered_target), "Arquivos CSV (*.csv)"),
        ):
            window.branches_window.export_csv_button.click()
            self.wait_for_csv_export(window)

        with filtered_target.open(encoding="utf-8-sig", newline="") as stream:
            filtered_rows = list(csv.reader(stream, delimiter=";"))
        self.assertEqual(len(filtered_rows), 2)
        self.assertEqual(filtered_rows[1][2], "C1")

    def test_maximum_demand_formats_tooltip_and_sorts_numerically(self) -> None:
        window, _, _, catalog = self.make_window(two_circuits=True)
        branches = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        window.branches_window.set_result(branches)
        model = window.branch_table_model
        model._maximum_demand_by_branch = {  # índice derivado já calculado
            1: Decimal("10.123456789"),
            2: Decimal("2.5"),
        }
        model.dataChanged.emit(model.index(0, 14), model.index(1, 14))

        self.assertEqual(model.data(model.index(0, 14)), "10.1235")
        self.assertEqual(
            model.data(model.index(0, 14), Qt.ItemDataRole.ToolTipRole),
            "10.123456789",
        )
        proxy = window.branches_window.proxy_model
        proxy.sort(14, Qt.SortOrder.AscendingOrder)
        self.app.processEvents()

        ordered_ids = tuple(
            model.record(proxy.mapToSource(proxy.index(row, 0)).row()).branch_id
            for row in range(proxy.rowCount())
        )
        self.assertEqual(ordered_ids, (2, 1))

    def test_filter_selection_reactivates_circuit_and_highlights_whole_branch(self) -> None:
        window, _, segments, catalog = self.make_window(two_circuits=True)
        configuration = load_phase_configuration(self.config_path)
        result = analyze_branches(catalog, configuration)
        window._branch_analysis_result = result
        window.branches_window.set_result(result)
        window._show_branches_window()

        self.assertEqual(window.branches_window.circuit_filter.count(), 3)
        window.branches_window.circuit_filter.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(window.branches_window.proxy_model.rowCount(), 1)

        first_record = result.records[0]
        circuit_check = window.circuit_table_model.index(
            first_record.circuit_index,
            0,
        )
        window.circuit_table_model.setData(
            circuit_check,
            Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        window._apply_circuit_visibility()
        self.assertFalse(
            window._circuit_visibility.is_visible(first_record.circuit_index)
        )

        window.branches_window.table.selectRow(0)
        self.app.processEvents()

        self.assertTrue(
            window._circuit_visibility.is_visible(first_record.circuit_index)
        )
        self.assertIs(window._selected_branch, first_record)
        self.assertIsNone(window._selected_feature)
        self.assertTrue(window.branch_highlight_overlay.isVisible())
        self.assertEqual(
            set(window.branch_highlight_overlay.segment_indices),
            {2, 3},
        )
        self.assertGreater(
            window.branch_highlight_overlay.zValue(),
            window.segment_selection_overlay.zValue(),
        )

        before = abs(window.view.transform().m11())
        window._activate_branch(first_record)
        self.app.processEvents()
        self.assertLessEqual(abs(window.view.transform().m11()), 4.0)
        self.assertNotEqual(abs(window.view.transform().m11()), before)

        window._set_selection(FeatureSelection("segment", 0))
        self.assertFalse(window.branch_highlight_overlay.isVisible())
        self.assertFalse(window.branches_window.table.selectionModel().hasSelection())
        self.assertEqual(window._selected_feature, FeatureSelection("segment", 0))
        self.assertIs(window._line_model, segments)

    def _highlighted_window(self):  # noqa: ANN202
        """Janela com um ramal em destaque, pronta para os gestos de desfazer."""

        window, _, _, _ = self.make_window()
        window._show_or_analyze_branches()
        self.wait_for_analysis(window)
        self.wait_for_equivalent(window)
        record = window._branch_analysis_result.records[0]
        window._select_branch(record)
        self.app.processEvents()
        self.assertTrue(window.branch_highlight_overlay.isVisible())
        return window, record

    def test_clicking_inside_the_branch_keeps_the_highlight(self) -> None:
        # Antes, qualquer clique no mapa apagava o destaque do ramal — inclusive
        # o clique num trecho de dentro dele, para ver o cabo.
        window, record = self._highlighted_window()

        window._set_selection(
            FeatureSelection("segment", int(record.segment_indices[0]))
        )

        self.assertTrue(window.branch_highlight_overlay.isVisible())
        self.assertIsNotNone(window._selected_branch)

    def test_clicking_outside_the_branch_clears_the_highlight(self) -> None:
        window, record = self._highlighted_window()
        owned = set(record.segment_indices.tolist())
        outside = next(
            index
            for index in range(len(window._line_model))
            if index not in owned
        )

        window._set_selection(FeatureSelection("segment", outside))

        self.assertFalse(window.branch_highlight_overlay.isVisible())
        self.assertIsNone(window._selected_branch)

    def test_escape_clears_the_branch_highlight(self) -> None:
        window, _ = self._highlighted_window()

        window._escape_pressed()

        self.assertFalse(window.branch_highlight_overlay.isVisible())
        self.assertIsNone(window._selected_branch)

    def test_escape_in_the_window_unselects_before_closing(self) -> None:
        # Pela tabela, que é o caminho real: a linha selecionada é o que dispara
        # o destaque, e é ela que o Esc precisa desfazer primeiro.
        window, _, _, _ = self.make_window()
        window._show_or_analyze_branches()
        self.wait_for_analysis(window)
        self.wait_for_equivalent(window)
        branches = window.branches_window
        branches.table.setCurrentIndex(branches.proxy_model.index(0, 1))
        self.app.processEvents()
        self.assertTrue(branches.table.currentIndex().isValid())
        self.assertTrue(window.branch_highlight_overlay.isVisible())

        branches.keyPressEvent(
            QKeyEvent(
                QKeyEvent.Type.KeyPress,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.app.processEvents()

        self.assertFalse(branches.table.currentIndex().isValid())
        self.assertTrue(branches.isVisible())
        self.assertFalse(window.branch_highlight_overlay.isVisible())

    def test_phase_mode_survives_branch_selection_and_data_change_invalidates(self) -> None:
        window, _, _, catalog = self.make_window()
        result = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
        )
        window._branch_analysis_result = result
        window.branches_window.set_result(result)
        window.phase_coloring_action.setChecked(True)
        window.branches_window.table.selectRow(0)
        self.app.processEvents()

        self.assertTrue(window.phase_coloring_action.isChecked())
        self.assertTrue(window.branch_highlight_overlay.isVisible())

        window._set_load_model(None)

        self.assertIsNone(window._branch_analysis_result)
        self.assertFalse(window.branch_highlight_overlay.isVisible())
        self.assertFalse(window.branches_window.isVisible())
        self.assertTrue(window.branches_action.isEnabled())

    def test_simplified_mode_builds_selectable_derived_load_and_restores(self) -> None:
        window, bars, segments, catalog = self.make_window()
        loads = LoadModel(
            bars,
            ["L1", "L2"],
            [3, 4],
            ["", ""],
            ["", ""],
            ["1.5", "2.5"],
            ["2", "3"],
            ["", ""],
            ["", ""],
            ["", ""],
        )
        window._set_load_model(loads)
        # A camada de cargas nasce oculta; o equivalente é desenhado nela.
        window.show_loads_action.setChecked(True)
        result = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
            loads,
        )
        window._branch_analysis_result = result
        window.branches_window.set_result(result)

        window.simplified_network_action.setChecked(True)
        self.wait_for_equivalent(window)

        equivalent = window._equivalent_network_result
        self.assertIsNotNone(equivalent)
        self.assertTrue(window.simplified_network_action.isChecked())
        self.assertIs(window._load_model, loads)
        self.assertIs(window._line_model, segments)
        self.assertEqual(equivalent.model.record(0).load_id, "RAMAL-1")
        masks = equivalent.model.visibility_masks((True,))
        self.assertEqual(set(np.flatnonzero(masks.segment_mask)), {0, 1})
        self.assertFalse(bool(masks.source_load_mask[0]))
        self.assertFalse(bool(masks.source_load_mask[1]))

        window.equivalent_load_virtualizer.refresh(force=True)
        self.app.processEvents()
        self.app.processEvents()
        item = window.equivalent_load_virtualizer._active[0]
        self.assertEqual(
            item.toolTip(),
            "RAMAL-1 — carga equivalente de ramal",
        )
        anchor = window.view.mapFromScene(item.pos())
        center = item.symbol_rect.center()
        window.view._select_nearest(
            anchor + QPoint(round(center.x()), round(center.y()))
        )
        self.assertEqual(
            window._selected_feature,
            FeatureSelection("equivalent_load", 0),
        )

        window._set_selection(FeatureSelection("equivalent_load", 0))
        self.assertEqual(window.details_dock.windowTitle(), "Carga equivalente de ramal")
        self.assertEqual(window.equivalent_detail_labels["origin"].text(), "Ramal agregado")
        self.assertEqual(window.equivalent_detail_labels["load_id"].text(), "RAMAL-1")
        self.assertEqual(window.equivalent_detail_labels["branch_type"].text(), "MONOFASICO")
        self.assertEqual(window.equivalent_detail_labels["removable"].text(), "NÃO (0)")
        self.assertEqual(window.equivalent_detail_labels["snom"].text(), "4")
        self.assertEqual(window.equivalent_detail_labels["sadm"].text(), "5")

        def group(load_id: str, value: str):
            return tuple(
                LoadPatternRecord(
                    load_id,
                    npat,
                    value,
                    value,
                    value,
                    value,
                    value,
                    value,
                )
                for npat in range(4)
            )

        patterns = LoadPatternModel(
            loads,
            [group("L1", "1"), group("L2", "2")],
        )
        window._set_load_pattern_model(patterns)
        self.app.processEvents()
        self.wait_for_equivalent(window)
        self.assertIs(window._equivalent_network_result.model.source_patterns, patterns)
        window._set_selection(FeatureSelection("equivalent_load", 0))
        self.assertEqual(window.equivalent_pattern_table_model.rowCount(), 4)
        # Exibição arredondada em 4 casas; o valor somado continua inteiro.
        self.assertEqual(
            window.equivalent_pattern_table_model.data(
                window.equivalent_pattern_table_model.index(0, 2)
            ),
            "3.0000",
        )
        self.assertEqual(
            window.equivalent_pattern_table_model.data(
                window.equivalent_pattern_table_model.index(0, 2),
                Qt.ItemDataRole.ToolTipRole,
            ),
            "3",
        )

        window.show_loads_action.setChecked(False)
        self.assertFalse(window.equivalent_load_virtualizer.loads_visible)
        window.show_loads_action.setChecked(True)
        self.assertTrue(window.equivalent_load_virtualizer.loads_visible)

        window.branches_window.table.selectRow(0)
        self.app.processEvents()
        self.assertTrue(window.branch_highlight_overlay.isVisible())

        window.simplified_network_action.setChecked(False)
        self.app.processEvents()
        self.assertFalse(window.equivalent_load_virtualizer.loads_visible)
        self.assertEqual(window._line_item.visible_segment_count, 4)
        self.assertIs(window._load_model, loads)

    def test_biphasic_equivalent_panel_and_highlight_include_single_phase_subtree(self) -> None:
        window, bars, _, catalog = self.make_window(
            biphasic=True,
            switch_on_branch=True,
        )
        loads = LoadModel(
            bars,
            ["L1", "L2"],
            [3, 4],
            ["", ""],
            ["", ""],
            ["1", "2"],
            ["3", "4"],
            ["", ""],
            ["", ""],
            ["", ""],
        )
        window._set_load_model(loads)
        result = analyze_branches(
            catalog,
            load_phase_configuration(self.config_path),
            loads,
        )
        window._branch_analysis_result = result
        window.branches_window.set_result(result)

        branch = result.records[0]
        self.assertEqual(branch.branch_type.value, "BIFASICO")
        self.assertEqual(set(branch.segment_indices), {2, 3})
        self.assertTrue(branch.removable)
        self.assertEqual(
            window.branch_table_model.data(window.branch_table_model.index(0, 2)),
            "BIFASICO",
        )
        self.assertEqual(
            window.branch_table_model.data(window.branch_table_model.index(0, 7)),
            "T2",
        )
        self.assertEqual(
            window.branch_table_model.data(window.branch_table_model.index(0, 9)),
            "CH1",
        )
        self.assertEqual(
            window.branch_table_model.data(window.branch_table_model.index(0, 10)),
            "CCH1",
        )

        window.branches_window.table.selectRow(0)
        self.app.processEvents()
        self.assertEqual(
            set(window.branch_highlight_overlay.segment_indices),
            {2, 3},
        )

        window.simplified_network_action.setChecked(True)
        self.wait_for_equivalent(window)
        window._set_selection(FeatureSelection("equivalent_load", 0))

        self.assertEqual(
            window.equivalent_detail_labels["branch_type"].text(),
            "BIFASICO",
        )
        self.assertEqual(
            window.equivalent_detail_labels["removable"].text(),
            "SIM (1)",
        )
        self.assertEqual(window.equivalent_detail_labels["snom"].text(), "3")
        self.assertEqual(window.equivalent_detail_labels["sadm"].text(), "7")

    def test_simplified_mode_asks_before_running_missing_analysis(self) -> None:
        window, _, _, _ = self.make_window()
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.No,
        ):
            window.simplified_network_action.setChecked(True)

        self.assertFalse(window.simplified_network_action.isChecked())
        self.assertIsNone(window._branch_thread)
        self.assertIsNone(window._equivalent_network_result)

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window.simplified_network_action.setChecked(True)
        self.wait_for_analysis(window)
        self.app.processEvents()
        self.wait_for_equivalent(window)

        self.assertIsNotNone(window._branch_analysis_result)
        self.assertIsNotNone(window._equivalent_network_result)
        self.assertTrue(window.simplified_network_action.isChecked())
        self.assertFalse(window.branches_window.isVisible())


if __name__ == "__main__":
    unittest.main()
