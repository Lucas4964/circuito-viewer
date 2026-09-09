"""Interface do espaço de trabalho com várias fontes.

A leitura do banco é testada em ``test_mdb_engine``, a composição em
``test_source_composition``; aqui o ``MdbSourceLoad`` é montado pelo mesmo
caminho que o worker usa e injetado direto na janela, sem ``pyodbc`` nem driver
ODBC.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.mdb_import import (
        MdbSourceLoad,
        dataset_from_result,
        list_circuits,
        load_database,
    )
    from circuit_viewer.mdb_import_dialog import MdbImportDialog
    from circuit_viewer.mdb_mapping import load_table_mapping, resolve_mapping
    from circuit_viewer.model import UtmCrs
    from circuit_viewer.source_composition import (
        ID_SEPARATOR,
        SourceWorkspace,
        compose,
        restrict_to_circuits,
    )

    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt6
    PYQT_AVAILABLE = False

from tests.test_mdb_import import network_database

try:
    from tests.test_source_composition import CRS, _shifted_tables
except ModuleNotFoundError:  # pragma: no cover
    CRS = None


def ensure_app():  # noqa: ANN201
    return QApplication.instance() or QApplication([])


def source_load(
    workspace: SourceWorkspace,
    *,
    offset: int = 0,
    circuit_ids: tuple[str, ...] = (),
    replace: bool = False,
) -> MdbSourceLoad:
    """O mesmo encadeamento que ``MdbImportWorker.run`` executa."""

    tag = workspace.next_tag()
    result = load_database(
        network_database(**_shifted_tables(offset)),
        CRS,
        source_path=f"C:/dados/rede-{tag}.mdb",
        scale=10.0,
    )
    dataset = restrict_to_circuits(
        dataset_from_result(result, tag=tag), circuit_ids
    )
    updated = (
        workspace.replaced_by(dataset) if replace else workspace.added(dataset)
    )
    return MdbSourceLoad(
        dataset=dataset,
        workspace=updated,
        composed=compose(updated.datasets),
        result=result,
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class AddDatabaseActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_the_action_lives_in_the_file_menu(self) -> None:
        file_menu = next(
            action.menu()
            for action in self.window.menuBar().actions()
            if action.text() == "Arquivo"
        )
        self.assertNotIn(self.window.mdb_add_action, file_menu.actions())
        self.assertIn(self.window.mdb_import_action, file_menu.actions())
        self.assertIn(self.window.new_project_action, file_menu.actions())

    def test_it_starts_disabled_saying_why(self) -> None:
        # Não há nada a que acrescentar antes do primeiro banco, e a ação diz
        # isso na dica em vez de sumir.
        self.window._sync_mdb_actions()
        if self.window._mdb_error is not None:
            self.skipTest("driver ODBC ausente neste ambiente")
        self.assertFalse(self.window.mdb_add_action.isEnabled())
        self.assertIn("Importar banco de dados", self.window.mdb_add_action.toolTip())

    def test_it_becomes_available_once_a_map_exists(self) -> None:
        self.window._mdb_error = None
        self.window._on_mdb_import_finished(source_load(SourceWorkspace()))
        self.window._sync_mdb_actions()
        self.assertTrue(self.window.mdb_add_action.isEnabled())
        self.assertIn("mantendo as fontes", self.window.mdb_add_action.toolTip())

    def test_it_does_nothing_without_a_model(self) -> None:
        self.window._mdb_error = None
        # Não deve abrir diálogo algum nem levantar.
        self.window._choose_mdb_import(replace=False)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class InstallComposedChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_the_first_import_installs_a_single_source_workspace(self) -> None:
        load = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(load)

        self.assertEqual(len(self.window._workspace), 1)
        self.assertIs(self.window._model, load.composed.bars)
        # Com uma fonte só, a composição não copia nada.
        self.assertIs(self.window._model, load.dataset.bars)
        self.assertIsNotNone(self.window._circuit_catalog)

    def test_adding_a_second_source_keeps_the_first(self) -> None:
        first = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(first)
        second = source_load(first.workspace, offset=1000)
        self.window._on_mdb_import_finished(second)

        self.assertEqual(
            [item.tag for item in self.window._workspace.datasets], ["F1", "F2"]
        )
        self.assertEqual(
            len(self.window._model),
            len(first.dataset.bars) + len(second.dataset.bars),
        )
        self.assertEqual(len(self.window._circuit_catalog), 2)

    def test_the_installed_chain_keeps_the_identity_rule(self) -> None:
        first = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(first)
        self.window._on_mdb_import_finished(source_load(first.workspace, offset=1000))

        window = self.window
        self.assertIs(window._line_model.bars, window._model)
        self.assertIs(window._load_model.bars, window._model)
        self.assertIs(window._switch_model.segments, window._line_model)
        self.assertIs(window._circuit_catalog.segments, window._line_model)
        self.assertIs(window._circuit_visibility.catalog, window._circuit_catalog)

    def test_replacing_drops_the_other_sources(self) -> None:
        first = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(first)
        second = source_load(first.workspace, offset=1000)
        self.window._on_mdb_import_finished(second)
        third = source_load(second.workspace, offset=2000, replace=True)
        self.window._on_mdb_import_finished(third)

        self.assertEqual(
            [item.tag for item in self.window._workspace.datasets], ["F3"]
        )
        self.assertEqual(len(self.window._circuit_catalog), 1)

    def test_a_raw_import_result_still_installs(self) -> None:
        """A costura que mantém ``test_mdb_import_ui`` passando sem edição."""

        result = load_database(
            network_database(),
            CRS,
            source_path="C:/dados/rede.mdb",
            scale=10.0,
        )
        self.window._on_mdb_import_finished(result)

        self.assertEqual(len(self.window._workspace), 1)
        self.assertIs(self.window._model, result.bars.model)

    def test_two_sources_with_the_same_ids_are_qualified(self) -> None:
        first = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(first)
        # offset zero: a segunda fonte repete todos os ids da primeira.
        second = source_load(first.workspace, offset=0)
        self.window._on_mdb_import_finished(second)

        self.assertIn(f"7{ID_SEPARATOR}F2", self.window._model.bar_ids)
        self.assertTrue(second.composed.report.collisions)
        # O CODIGO é rótulo: continua o do cadastro nas duas fontes.
        codes = self.window._model.codes
        self.assertEqual(codes[0], codes[3])


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class CircuitChoiceDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.database = network_database()
        self.plan = resolve_mapping(self.database, load_table_mapping())
        self.choices = list_circuits(self.database, self.plan.get("circuitos"))

    def dialog(self, choices=None) -> MdbImportDialog:  # noqa: ANN001
        return MdbImportDialog(
            "C:/dados/rede.mdb",
            self.plan,
            self.database.tables(),
            circuits=self.choices if choices is None else choices,
        )

    def test_without_circuits_requires_explicit_whole_database_mode(self) -> None:
        dialog = self.dialog(choices=())
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.load_button.isEnabled())
        dialog.whole_database_check.setChecked(True)
        self.assertTrue(dialog.load_button.isEnabled())
        self.assertEqual(dialog.selection().import_circuit_ids(), ())

    def test_nothing_is_selected_by_default(self) -> None:
        dialog = self.dialog()
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.selected_circuit_ids(), ())
        self.assertFalse(dialog.load_button.isEnabled())
        with self.assertRaises(ValueError):
            dialog.selection().import_circuit_ids()

    def test_all_chosen_ids_are_explicit_even_when_all_are_selected(self) -> None:
        dialog = self.dialog()
        self.addCleanup(dialog.close)
        dialog.substation_view.setCurrentIndex(dialog.substation_model.index(0, 0))
        dialog.transfer_buttons[">>"].click()
        self.assertEqual(dialog.selection().import_circuit_ids(), ("2",))
        self.assertTrue(dialog.load_button.isEnabled())

    def test_removing_everything_blocks_import(self) -> None:
        dialog = self.dialog()
        self.addCleanup(dialog.close)
        dialog.substation_view.setCurrentIndex(dialog.substation_model.index(0, 0))
        dialog.transfer_buttons[">>"].click()
        dialog.transfer_buttons["<<"].click()
        self.assertFalse(dialog.load_button.isEnabled())

    def test_filter_limits_the_available_list(self) -> None:
        dialog = self.dialog()
        self.addCleanup(dialog.close)
        dialog.substation_view.setCurrentIndex(dialog.substation_model.index(0, 0))
        dialog.circuit_filter.setText("nao-existe")
        self.assertEqual(dialog.available_model.rowCount(), 0)
        dialog.circuit_filter.setText("004001")
        self.assertEqual(dialog.available_model.rowCount(), 1)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class RestrictedImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_only_the_chosen_circuit_reaches_the_map(self) -> None:
        load = source_load(SourceWorkspace(), circuit_ids=("2",))
        self.window._on_mdb_import_finished(load)

        self.assertEqual(len(self.window._circuit_catalog), 1)
        self.assertEqual(
            self.window._circuit_catalog.definition(0).circuit_id, "2"
        )
        self.assertEqual(load.dataset.chosen_circuit_ids, ("2",))



@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class SourcesWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        first = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(first)
        self.second = source_load(first.workspace, offset=1000)
        self.window._on_mdb_import_finished(self.second)

    def accept_removal(self) -> None:
        """A confirmação é modal; o teste responde por ela."""

        from PyQt6.QtWidgets import QMessageBox

        original = QMessageBox.question
        QMessageBox.question = staticmethod(
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes
        )
        self.addCleanup(lambda: setattr(QMessageBox, "question", original))

    def test_the_panel_lists_every_loaded_source(self) -> None:
        model = self.window.sources_table_model
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.data(model.index(0, 0)), "F1")
        self.assertEqual(model.data(model.index(1, 0)), "F2")
        self.assertEqual(model.data(model.index(0, 1)), "rede-F1.mdb")
        # Circuitos, barras, trechos e cargas da fonte, não do mapa inteiro.
        self.assertEqual(model.data(model.index(0, 2)), "1")
        self.assertEqual(model.data(model.index(0, 3)), "3")

    def test_the_action_is_enabled_once_there_are_sources(self) -> None:
        self.assertTrue(self.window.sources_action.isEnabled())

    def test_renaming_changes_only_the_label(self) -> None:
        model = self.window.sources_table_model
        bars_before = self.window._model
        model.setData(model.index(0, 1), "Rede Norte", 2)  # EditRole
        self.assertEqual(model.data(model.index(0, 1)), "Rede Norte")
        self.assertEqual(self.window._workspace.datasets[0].name, "Rede Norte")
        # Renomear não recompõe nada.
        self.assertIs(self.window._model, bars_before)

    def test_removing_a_source_keeps_the_other(self) -> None:
        self.accept_removal()
        self.window._remove_source("F1")

        self.assertEqual(
            [item.tag for item in self.window._workspace.datasets], ["F2"]
        )
        self.assertEqual(len(self.window._circuit_catalog), 1)
        self.assertEqual(len(self.window._model), len(self.second.dataset.bars))
        # Com uma fonte só, a composição volta a não copiar nada.
        self.assertIs(self.window._model, self.second.dataset.bars)

    def test_removing_the_last_source_empties_the_map(self) -> None:
        self.accept_removal()
        self.window._remove_source("F1")
        self.window._remove_source("F2")

        self.assertEqual(len(self.window._workspace), 0)
        self.assertIsNone(self.window._model)
        self.assertIsNone(self.window._circuit_catalog)
        self.assertFalse(self.window.sources_action.isEnabled())
        self.assertFalse(self.window.fit_action.isEnabled())
        # O contador de etiquetas não rebobina: F1 não volta a ser oferecido.
        self.assertEqual(self.window._workspace.next_tag(), "F3")

    def test_colour_and_visibility_survive_adding_and_removing(self) -> None:
        window = self.window
        controller = window._circuit_visibility
        # O usuário desliga o circuito da segunda fonte e recolore o da primeira.
        controller.set_visible(1, False)
        window._circuit_visibility.set_color(0, "#123456")
        chosen = controller.colors[0]

        third = source_load(window._workspace, offset=2000)
        window._on_mdb_import_finished(third)

        self.assertEqual(window._circuit_visibility.colors[0], chosen)
        self.assertFalse(window._circuit_visibility.is_visible(1))
        # O circuito novo entra visível, sem roubar a cor de ninguém.
        self.assertTrue(window._circuit_visibility.is_visible(2))
        self.assertNotEqual(window._circuit_visibility.colors[2], chosen)

        self.accept_removal()
        window._remove_source("F3")
        self.assertEqual(window._circuit_visibility.colors[0], chosen)
        self.assertFalse(window._circuit_visibility.is_visible(1))

    def test_the_source_column_appears_only_with_more_than_one(self) -> None:
        window = self.window
        self.assertTrue(window.circuit_table_model.multi_source)
        self.assertFalse(
            window.circuits_window.table.isColumnHidden(
                window.circuit_table_model.SOURCE_COLUMN
            )
        )
        self.accept_removal()
        window._remove_source("F2")
        self.assertFalse(window.circuit_table_model.multi_source)
        self.assertTrue(
            window.circuits_window.table.isColumnHidden(
                window.circuit_table_model.SOURCE_COLUMN
            )
        )

    def test_the_source_column_says_where_the_circuit_came_from(self) -> None:
        model = self.window.circuit_table_model
        column = model.SOURCE_COLUMN
        self.assertIn("F1", model.data(model.index(0, column)))
        self.assertIn("F2", model.data(model.index(1, column)))


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class CsvRestrictionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_csv_stays_available_with_a_single_source(self) -> None:
        self.window._on_mdb_import_finished(source_load(SourceWorkspace()))
        self.window._sync_import_availability()
        self.assertFalse(self.window._multi_source())
        self.assertTrue(self.window.import_action.isEnabled())

    def test_csv_is_disabled_with_more_than_one_source(self) -> None:
        first = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(first)
        self.window._on_mdb_import_finished(source_load(first.workspace, offset=1000))

        self.assertTrue(self.window._multi_source())
        self.assertFalse(self.window.import_action.isEnabled())
        self.assertIn("um só banco", self.window.import_action.toolTip())

    def test_the_entry_point_refuses_even_if_the_action_is_forced(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

        first = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(first)
        self.window._on_mdb_import_finished(source_load(first.workspace, offset=1000))

        shown: list[str] = []
        original = QMessageBox.information
        QMessageBox.information = staticmethod(
            lambda parent, title, text, *a, **k: shown.append(text)
        )
        self.addCleanup(lambda: setattr(QMessageBox, "information", original))

        # Não deve abrir o ImportChoiceDialog, que é modal e travaria o teste.
        self.window._choose_import()
        self.assertEqual(len(shown), 1)
        self.assertIn("um só banco", shown[0])


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class BlocksFollowTheWorkspaceTests(unittest.TestCase):
    """Blocos e grafo de blocos valem para o mapa inteiro, não para a 1ª fonte.

    O defeito relatado: analisar os blocos, acrescentar uma fonte e reabrir a
    janela devolvia o resultado antigo — calculado sobre a rede anterior —, então
    a fonte acrescentada simplesmente não existia para as duas ferramentas.
    """

    def setUp(self) -> None:
        self.app = ensure_app()
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.first = source_load(SourceWorkspace())
        self.window._on_mdb_import_finished(self.first)

    def covered_bars(self, result) -> int:  # noqa: ANN001
        return sum(len(record.bar_indices) for record in result.records)

    def test_the_blocks_cover_the_whole_map_after_adding(self) -> None:
        before = self.window._ensure_blocks_result()
        self.assertEqual(self.covered_bars(before), len(self.window._model))

        second = source_load(self.first.workspace, offset=1000)
        self.window._on_mdb_import_finished(second)

        after = self.window._ensure_blocks_result()
        self.assertIsNot(after, before)
        self.assertEqual(self.covered_bars(after), len(self.window._model))
        # As redes das duas fontes são disjuntas, então cada uma traz um bloco.
        self.assertEqual(len(after.records), 2 * len(before.records))

    def test_the_result_always_belongs_to_the_current_network(self) -> None:
        self.window._ensure_blocks_result()
        self.window._on_mdb_import_finished(
            source_load(self.first.workspace, offset=1000)
        )
        result = self.window._ensure_blocks_result()

        self.assertIs(result.source_segments, self.window._line_model)
        self.assertIs(result.source_switches, self.window._switch_model)
        self.assertIs(result.source_loads, self.window._load_model)

    def test_the_block_graph_gets_the_new_result_too(self) -> None:
        """O grafo desenha a partir de ``source_segments``/``source_switches``.

        Um resultado velho ali não erra só a contagem: desenha com os modelos
        que já foram substituídos.
        """

        self.window._ensure_blocks_result()
        self.window._on_mdb_import_finished(
            source_load(self.first.workspace, offset=1000)
        )
        result = self.window._ensure_blocks_result()

        self.assertIs(self.window.block_graph_window.result, result)
        self.assertIs(result.source_segments, self.window._line_model)

    def test_removing_a_source_also_refreshes_the_blocks(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

        second = source_load(self.first.workspace, offset=1000)
        self.window._on_mdb_import_finished(second)
        self.window._ensure_blocks_result()

        original = QMessageBox.question
        QMessageBox.question = staticmethod(
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes
        )
        self.addCleanup(lambda: setattr(QMessageBox, "question", original))
        self.window._remove_source("F2")

        result = self.window._ensure_blocks_result()
        self.assertEqual(self.covered_bars(result), len(self.window._model))
        self.assertIs(result.source_segments, self.window._line_model)

    def test_changing_the_network_discards_the_cached_blocks(self) -> None:
        self.assertIsNotNone(self.window._ensure_blocks_result())
        self.window._on_mdb_import_finished(
            source_load(self.first.workspace, offset=1000)
        )
        # A cascata já limpou o cache; a janela não guarda nada de antes.
        self.assertIsNone(self.window.block_table_model.result)
        self.assertIsNone(self.window.block_graph_window.result)
        self.assertEqual(self.window._block_display_identities, {})

    def test_a_result_from_another_network_is_never_reused(self) -> None:
        """A guarda de identidade, exercitada sem passar pela cascata."""

        stale = self.window._ensure_blocks_result()
        self.window._on_mdb_import_finished(
            source_load(self.first.workspace, offset=1000)
        )
        # Reinjeta à força o resultado antigo, como faria um caminho novo que
        # trocasse um modelo sem avisar ninguém.
        self.window.block_table_model.set_result(stale)
        self.assertFalse(self.window._blocks_match_current_network(stale))

        result = self.window._ensure_blocks_result()
        self.assertIsNot(result, stale)
        self.assertEqual(self.covered_bars(result), len(self.window._model))

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
