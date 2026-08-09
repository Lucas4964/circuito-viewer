from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QMessageBox,
    )

    from circuit_viewer.cable_import import CableCsvResult
    from circuit_viewer.circuit_import import CircuitLoadResult
    from circuit_viewer.csv_import import CsvLoadResult
    from circuit_viewer.load_import import LoadCsvResult
    from circuit_viewer.load_pattern_import import LoadPatternCsvResult
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.model import (
        CableModel,
        CircuitCatalogModel,
        CircuitDefinition,
        CircuitModel,
        LineNetworkModel,
        LoadModel,
        LoadPatternModel,
        LoadPatternRecord,
        RegulatorModel,
        SwitchModel,
        UtmCrs,
    )
    from circuit_viewer.opendss_export import (
        LINES_FILENAME,
        REGULATORS_FILENAME,
        SINGLE_PHASE_LOADS_FILENAME,
        SWITCHES_FILENAME,
        THREE_PHASE_LOADS_FILENAME,
        TWO_PHASE_LOADS_FILENAME,
        build_export,
    )
    from circuit_viewer.opendss_export_dialog import OpenDssExportDialog
    from circuit_viewer.regulator_import import RegulatorLoadResult
    from circuit_viewer.segment_import import SegmentLoadResult
    from circuit_viewer.switch_import import SwitchLoadResult

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


def accept_dialog(dialog) -> int:  # noqa: ANN001
    """Substitui o exec() modal do diálogo por um aceite imediato."""

    dialog.accept()
    return int(QDialog.DialogCode.Accepted)


def make_cables() -> CableModel:
    return CableModel(
        ["CB1"],
        ["1"],
        ["4/0"],
        ["340"],
        ["0,00824"],
        ["0,367"],
        ["0,42"],
        ["1,2"],
        ["0,551"],
        ["1,232"],
        ["0,367"],
        ["0,42"],
        ["ALUMINIO 4/0"],
        ["EXT-1"],
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class OpenDssExportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.destination = Path(directory.name)

    def _window(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        return window

    def _load_everything(
        self,
        window: MainWindow,
        *,
        with_loads: bool = True,
    ) -> None:
        """Importa, na ordem, tudo que a exportação consome.

        ``with_loads=False`` para o caso em que só os dois arquivos de rede são
        gerados: cargas e patamares não fazem parte das precondições do menu.
        """

        bars = CircuitModel(
            ["B0", "B1", "B2"],
            ["COD-A", "COD-B", "COD-C"],
            [500_000.0, 500_100.0, 500_200.0],
            [8_000_000.0, 8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )
        window._on_import_finished(CsvLoadResult(bars, "utf-8-sig", 3, 3, 0, (), 0))
        network = LineNetworkModel(
            bars,
            ["T0", "T1"],
            ["TR-1", "TR-2"],
            ["13", "13"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["CB1", "CB1"],
            ["", ""],
            [250.0, 400.0],
        )
        window._on_segment_import_finished(
            SegmentLoadResult(network, "utf-8-sig", 2, 2, 0, (), 0)
        )
        switches = SwitchModel(
            network,
            ["CH1"],
            ["TC"],
            ["C1"],
            [1],
            ["CHV-1"],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        window._on_switch_import_finished(
            SwitchLoadResult(switches, "utf-8-sig", 1, 1, 0, (), 0)
        )
        catalog = CircuitCatalogModel.build(
            network,
            switches,
            [CircuitDefinition("C1", "B0", "ALIMENTADOR", "13,8")],
        )
        window._on_circuit_import_finished(
            CircuitLoadResult(catalog, "utf-8-sig", 1, 1, 0, (), 0)
        )
        window._on_cable_import_finished(
            CableCsvResult(make_cables(), "utf-8-sig", 1, 1, 0, 0, (), 0)
        )
        if with_loads:
            loads = LoadModel(
                bars,
                ["CG1", "CG2", "CG3"],
                [1, 2, 0],
                ["EXT-1", "EXT-2", "EXT-3"],
                ["CARGA-1", "CARGA-2", "CARGA-3"],
                ["10", "20", "30"],
                ["12", "22", "32"],
                ["220", "220", "220"],
                # FASES2 do fases2.json real: "1" é D, "7" é DE, "13" é DEF.
                ["1", "7", "13"],
                ["Y", "Y", "Y"],
            )
            window._on_load_import_finished(
                LoadCsvResult(loads, "utf-8-sig", 3, 3, 0, (), 0)
            )
            patterns = LoadPatternModel(
                loads,
                [
                    tuple(
                        LoadPatternRecord(
                            load_id, npat, f"{1.5 + npat}", f"{2.5 + npat}",
                            f"{3.5 + npat}", f"{0.25 + npat}",
                            f"{0.35 + npat}", f"{0.45 + npat}",
                        )
                        for npat in range(4)
                    )
                    for load_id in ("CG1", "CG2", "CG3")
                ],
            )
            window._on_load_pattern_import_finished(
                LoadPatternCsvResult(patterns, "utf-8-sig", 12, 12, 0, (), 0)
            )
        self.app.processEvents()

    def test_menu_entry_requires_every_source(self) -> None:
        window = self._window()

        export_menu = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Exportar"
        )
        self.assertIn(window.opendss_export_action, export_menu.actions())
        self.assertIn(window.simplified_opendss_export_action, export_menu.actions())
        self.assertFalse(window.opendss_export_action.isEnabled())
        self.assertFalse(window.simplified_opendss_export_action.isEnabled())

        self._load_everything(window)
        self.assertTrue(window.opendss_export_action.isEnabled())
        self.assertTrue(window.simplified_opendss_export_action.isEnabled())

        # Sem o catálogo de cabos a exportação volta a ficar indisponível.
        window._set_cable_model(None)
        self.assertFalse(window.opendss_export_action.isEnabled())
        self.assertFalse(window.simplified_opendss_export_action.isEnabled())

    def test_simplified_export_requires_processed_branch_projection(self) -> None:
        window = self._window()
        self._load_everything(window)

        with patch(
            "circuit_viewer.main_window.QMessageBox.information"
        ) as information, patch.object(OpenDssExportDialog, "exec") as dialog:
            window._export_simplified_opendss()

        dialog.assert_not_called()
        self.assertIn("Ramais", information.call_args.args[2])
        self.assertIsNone(window._export_thread)

    def test_simplified_export_builds_projection_without_activating_view(self) -> None:
        window = self._window()
        self._load_everything(window)
        window._branch_analysis_result = object()

        with patch.object(window, "_start_equivalent_build") as start, patch.object(
            OpenDssExportDialog,
            "exec",
        ) as dialog:
            window._export_simplified_opendss()

        start.assert_called_once_with()
        dialog.assert_not_called()
        self.assertTrue(window._pending_simplified_export)
        self.assertTrue(window._pending_simplified_activation)
        self.assertFalse(window.simplified_network_action.isChecked())

    def test_dialog_lists_circuits_and_returns_the_selection(self) -> None:
        window = self._window()
        self._load_everything(window)

        dialog = OpenDssExportDialog(window._circuit_catalog, window)
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.circuit_list.count(), 1)
        self.assertIn("C1", dialog.circuit_list.item(0).text())
        self.assertIn("13,8 kV", dialog.circuit_list.item(0).text())
        self.assertEqual(dialog.selected_circuit_indices(), (0,))

        ok_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        dialog.circuit_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.selected_circuit_indices(), ())
        self.assertFalse(ok_button.isEnabled())

        dialog.circuit_list.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.selected_circuit_indices(), (0,))
        self.assertTrue(ok_button.isEnabled())

    def test_dialog_keeps_only_one_circuit_checked(self) -> None:
        window = self._window()
        self._load_everything(window)
        # Um segundo circuito, para exercitar a exclusividade.
        catalog = CircuitCatalogModel.build(
            window._line_model,
            window._switch_model,
            [
                CircuitDefinition("C1", "B0", "ALIMENTADOR", "13,8"),
                CircuitDefinition("C2", "B2", "OUTRO", "13,8"),
            ],
        )
        dialog = OpenDssExportDialog(catalog, window)
        self.addCleanup(dialog.close)

        # Só o primeiro nasce marcado.
        self.assertEqual(dialog.selected_circuit_indices(), (0,))

        dialog.circuit_list.item(1).setCheckState(Qt.CheckState.Checked)

        # Marcar o segundo desmarca o primeiro: o master energiza um só.
        self.assertEqual(dialog.selected_circuit_indices(), (1,))
        self.assertEqual(
            dialog.circuit_list.item(0).checkState(), Qt.CheckState.Unchecked
        )

    def _load_regulators(self, window: MainWindow) -> RegulatorModel:
        """Um regulador no trecho 0, na tensão do circuito da fixture."""

        model = RegulatorModel(
            window._line_model,
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
        window._on_regulator_import_finished(
            RegulatorLoadResult(model, "utf-8-sig", 1, 1, 0, (), 0)
        )
        self.app.processEvents()
        return model

    def test_imported_generators_without_update_require_confirmation(self) -> None:
        window = self._window()
        self._load_everything(window)
        # Para esta confirmacao basta existir uma fonte importada; o resultado
        # derivado continua ausente, que e justamente o estado exercitado.
        window._generator_model = object()

        with patch(
            "circuit_viewer.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as question, patch.object(OpenDssExportDialog, "exec") as dialog:
            window._export_opendss()

        dialog.assert_not_called()
        self.assertIn("Atualizar Geradores", question.call_args.args[2])
        self.assertIsNone(window._export_thread)

    def test_export_includes_the_regulators_of_the_window(self) -> None:
        window = self._window()
        self._load_everything(window)
        self._load_regulators(window)

        with patch.object(
            OpenDssExportDialog, "exec", accept_dialog
        ), patch(
            "circuit_viewer.main_window.QFileDialog.getExistingDirectory",
            return_value=str(self.destination),
        ), patch(
            "circuit_viewer.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(QMessageBox, "exec", return_value=0):
            # O trecho da fixture tem 250 m: substituí-lo pelo regulador
            # descarta a impedância dele e o relatório sai com aviso, modal.
            window.opendss_export_action.trigger()
            self._wait_for_export(window)

        target = self.destination / REGULATORS_FILENAME
        self.assertTrue(target.is_file())
        emitted = target.read_text(encoding="utf-8")
        self.assertIn("New Transformer.REG-X-D ", emitted)
        self.assertIn("New RegControl.CTRL-X-F ", emitted)
        # O trecho regulado saiu do arquivo de trechos: ele virou o regulador.
        # Nesta fixture o outro trecho carrega a chave, então trechos.dss fica
        # sem nenhuma Line — e a chave continua intacta no arquivo dela.
        lines = (self.destination / LINES_FILENAME).read_text(encoding="utf-8")
        self.assertNotIn("New Line.", lines)
        switches = (self.destination / SWITCHES_FILENAME).read_text(encoding="utf-8")
        self.assertIn("New Line.CHV-1 ", switches)

    def test_export_writes_every_file_in_the_chosen_folder(self) -> None:
        window = self._window()
        self._load_everything(window)

        with patch.object(
            OpenDssExportDialog, "exec", accept_dialog
        ), patch(
            "circuit_viewer.main_window.QFileDialog.getExistingDirectory",
            return_value=str(self.destination),
        ) as folder_dialog:
            window.opendss_export_action.trigger()
            self._wait_for_export(window)

        # A pasta é pedida uma única vez para todos os arquivos.
        folder_dialog.assert_called_once()

        expected = build_export(
            window._circuit_catalog,
            window._cable_model,
            window._phase_configuration,
            (0,),
            loads=window._load_model,
            patterns=window._load_pattern_model,
        )
        self.assertEqual(
            [name for name, _ in expected.files],
            [
                LINES_FILENAME,
                SWITCHES_FILENAME,
                SINGLE_PHASE_LOADS_FILENAME,
                TWO_PHASE_LOADS_FILENAME,
                THREE_PHASE_LOADS_FILENAME,
                "ALIMENTADOR_Master.dss",
                "ALIMENTADOR_Buscoords.csv",
            ],
        )
        for filename, text in expected.files:
            target = self.destination / filename
            self.assertTrue(target.is_file(), filename)
            self.assertEqual(target.read_text(encoding="utf-8"), text)

        lines = (self.destination / LINES_FILENAME).read_text(encoding="utf-8")
        switches = (self.destination / SWITCHES_FILENAME).read_text(
            encoding="utf-8"
        )
        single_phase = (
            self.destination / SINGLE_PHASE_LOADS_FILENAME
        ).read_text(encoding="utf-8")
        two_phase = (self.destination / TWO_PHASE_LOADS_FILENAME).read_text(
            encoding="utf-8"
        )
        three_phase = (
            self.destination / THREE_PHASE_LOADS_FILENAME
        ).read_text(encoding="utf-8")
        # O trecho T1 é chave: sai de chaves.dss, nunca de trechos.dss.
        self.assertIn("New Line.TR-1 ", lines)
        self.assertNotIn("TR-2", lines)
        self.assertIn("New Line.CHV-1 Bus1=COD-B", switches)
        self.assertTrue(switches.rstrip().endswith("Switch=Yes"))
        # Uma carga de cada contagem de fases, cada uma no seu arquivo.
        self.assertIn(
            "New LoadShape.PERFIL-CARGA-1-1F-D npts=4", single_phase
        )
        self.assertIn(
            "New Load.CARGA-1-1F-D phases=1 bus1=COD-B.1 conn=wye",
            single_phase,
        )
        self.assertTrue(single_phase.rstrip().endswith("class=1"))
        self.assertIn(
            "New Load.CARGA-2-2F-D phases=1 bus1=COD-C.1 conn=wye", two_phase
        )
        self.assertIn(
            "New Load.CARGA-2-2F-E phases=1 bus1=COD-C.2 conn=wye", two_phase
        )
        self.assertTrue(two_phase.rstrip().endswith("class=2"))
        for letter, node in (("D", "1"), ("E", "2"), ("F", "3")):
            self.assertIn(
                f"New Load.CARGA-3-3F-{letter} phases=1 bus1=COD-A.{node}"
                " conn=wye",
                three_phase,
            )
        self.assertTrue(three_phase.rstrip().endswith("class=3"))
        # Cada carga aparece só no arquivo da sua contagem de fases.
        self.assertNotIn("CARGA-2", single_phase)
        self.assertNotIn("CARGA-3", single_phase)
        self.assertNotIn("CARGA-1", two_phase)
        self.assertNotIn("CARGA-3", two_phase)
        self.assertNotIn("CARGA-1", three_phase)
        self.assertNotIn("CARGA-2", three_phase)
        # O master chama todos os arquivos de elementos e aponta as coordenadas.
        master = (self.destination / "ALIMENTADOR_Master.dss").read_text(
            encoding="utf-8"
        )
        self.assertIn("New Circuit.ALIMENTADOR", master)
        self.assertIn("~ bus1=COD-A.1.2.3 phases=3 basekv=13.8", master)
        for filename, _ in expected.element_files:
            self.assertIn(f"Redirect {filename}", master)
        self.assertIn("Buscoords ALIMENTADOR_Buscoords.csv", master)
        buscoords = (self.destination / "ALIMENTADOR_Buscoords.csv").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            buscoords.splitlines(),
            [
                "COD-A,500000.000,8000000.000",
                "COD-B,500100.000,8000000.000",
                "COD-C,500200.000,8000000.000",
            ],
        )
        self.assertIn(str(self.destination), window.statusBar().currentMessage())

    def test_load_files_are_skipped_without_loads_and_patterns(self) -> None:
        window = self._window()
        self._load_everything(window, with_loads=False)

        with patch.object(
            OpenDssExportDialog, "exec", accept_dialog
        ), patch(
            "circuit_viewer.main_window.QFileDialog.getExistingDirectory",
            return_value=str(self.destination),
        ):
            window.opendss_export_action.trigger()
            self._wait_for_export(window)

        # O menu não depende de cargas: os dois arquivos de rede saem normalmente.
        self.assertTrue((self.destination / LINES_FILENAME).is_file())
        self.assertTrue((self.destination / SWITCHES_FILENAME).is_file())
        for filename in (
            SINGLE_PHASE_LOADS_FILENAME,
            TWO_PHASE_LOADS_FILENAME,
            THREE_PHASE_LOADS_FILENAME,
        ):
            self.assertFalse((self.destination / filename).exists(), filename)

    def test_existing_load_file_is_ignored_when_it_will_not_be_written(self) -> None:
        window = self._window()
        self._load_everything(window, with_loads=False)
        # Sem cargas o arquivo não será gravado, então não faz sentido pedir
        # confirmação para substituí-lo.
        target = self.destination / TWO_PHASE_LOADS_FILENAME
        target.write_text("conteudo anterior", encoding="utf-8")

        with patch.object(
            OpenDssExportDialog, "exec", accept_dialog
        ), patch(
            "circuit_viewer.main_window.QFileDialog.getExistingDirectory",
            return_value=str(self.destination),
        ), patch(
            "circuit_viewer.main_window.QMessageBox.question",
        ) as question:
            window.opendss_export_action.trigger()
            self._wait_for_export(window)

        question.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "conteudo anterior")

    def test_export_asks_before_replacing_any_existing_file(self) -> None:
        window = self._window()
        self._load_everything(window)
        # Basta um dos arquivos existir para a confirmação aparecer.
        target = self.destination / SWITCHES_FILENAME
        target.write_text("conteudo anterior", encoding="utf-8")

        with patch.object(
            OpenDssExportDialog, "exec", accept_dialog
        ), patch(
            "circuit_viewer.main_window.QFileDialog.getExistingDirectory",
            return_value=str(self.destination),
        ), patch(
            "circuit_viewer.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as question:
            window.opendss_export_action.trigger()

        question.assert_called_once()
        self.assertIn(SWITCHES_FILENAME, question.call_args.args[2])
        self.assertIsNone(window._export_thread)
        self.assertEqual(target.read_text(encoding="utf-8"), "conteudo anterior")
        self.assertFalse((self.destination / LINES_FILENAME).exists())

    def _wait_for_export(self, window: MainWindow) -> None:
        thread = window._export_thread
        if thread is not None:
            thread.wait(10_000)
        for _ in range(50):
            self.app.processEvents()
            if window._export_thread is None:
                return


if __name__ == "__main__":
    unittest.main()
