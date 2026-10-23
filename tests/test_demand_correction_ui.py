"""Interface da correção de demanda: diálogos, sessão e integração com o fluxo.

Nenhum teste toca a DLL: o worker é substituído e o resultado é montado à mão,
como em ``test_powerflow_ui``.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

    from circuit_viewer.demand_correction import (
        CorrectionSettings,
        DemandCorrectionResult,
        DemandScope,
        HeadReading,
        PatamarCorrection,
        PatamarStatus,
        PatamarTarget,
    )
    from circuit_viewer.demand_correction_dialog import (
        DemandCorrectionDialog,
        DemandCorrectionReportDialog,
        load_demand_correction_settings,
        save_demand_correction_settings,
    )
    from circuit_viewer.load_import import LoadCsvResult
    from circuit_viewer.model import LoadModel
    from circuit_viewer.opendss_powerflow import PowerFlowResult
    # O módulo, e não a classe: importada, ela seria coletada de novo aqui.
    from tests import test_powerflow_ui

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


class SignalStub:
    def connect(self, _callback) -> None:  # noqa: ANN001
        pass


class RecordingWorker:
    created: list["RecordingWorker"] = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.args = args
        self.kwargs = kwargs
        self.progress = SignalStub()
        self.finished = SignalStub()
        self.failed = SignalStub()
        self.cancelled = SignalStub()
        RecordingWorker.created.append(self)

    def moveToThread(self, _thread) -> None:  # noqa: N802, ANN001
        pass

    def run(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def deleteLater(self) -> None:  # noqa: N802
        pass


def make_result(*statuses: PatamarStatus) -> DemandCorrectionResult:
    patamares = tuple(
        PatamarCorrection(
            step,
            PatamarTarget(10.0, 4.0),
            status,
            1.0 + 0.1 * (step + 1),
            0.9,
            iterations=3,
            initial=HeadReading(8.0, 5.0),
            final=HeadReading(10.0, 4.0),
        )
        for step, status in enumerate(statuses)
    )
    return DemandCorrectionResult(
        0, "C1", frozenset({DemandScope.CARGA}), CorrectionSettings(), patamares,
        scoped_load_count=1,
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class DemandCorrectionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        # O auxiliar de montagem da janela usa o app da própria classe.
        test_powerflow_ui.PowerFlowUiTests.app = cls.app

    def _dialog(self, *, energy_mode: bool = False, remembered=None) -> DemandCorrectionDialog:  # noqa: ANN001
        helper = test_powerflow_ui.PowerFlowUiTests()
        window = helper._window()
        self.addCleanup(window.close)
        helper._load_everything(window)
        dialog = DemandCorrectionDialog(
            window._circuit_catalog,
            (0,),
            energy_mode=energy_mode,
            settings=CorrectionSettings(),
            scope=frozenset({DemandScope.CARGA, DemandScope.UC_BT}),
            patamar_labels=lambda _index: ("Madrugada", "Manhã", "Tarde", "Noite"),
            remembered_targets=remembered,
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_typed_targets_become_one_pair_per_patamar(self) -> None:
        dialog = self._dialog()
        dialog.table.item(0, 0).setText("1.234,5")
        dialog.table.item(0, 1).setText("300")
        dialog.table.item(2, 1).setText("-12,5")

        targets = dialog.selected_targets()

        self.assertEqual(targets[0], PatamarTarget(1234.5, 300.0))
        self.assertEqual(targets[1], PatamarTarget())
        self.assertEqual(targets[2], PatamarTarget(None, -12.5))
        self.assertIn("Madrugada", dialog.table.verticalHeaderItem(0).text())

    def test_invalid_or_empty_input_keeps_the_dialog_open(self) -> None:
        dialog = self._dialog()

        dialog._accept_if_valid()
        self.assertFalse(dialog.error_label.isHidden())
        self.assertIn("ao menos um patamar", dialog.error_label.text())

        dialog.table.item(1, 0).setText("abc")
        dialog._accept_if_valid()
        self.assertIn("NPAT 1", dialog.error_label.text())
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_energy_groups_only_exist_in_energy_mode(self) -> None:
        interplan = self._dialog()
        self.assertFalse(interplan.scope_checks[DemandScope.UC_BT].isEnabled())
        self.assertEqual(interplan.selected_scope(), frozenset({DemandScope.CARGA}))

        energy = self._dialog(energy_mode=True)
        self.assertTrue(energy.scope_checks[DemandScope.UC_BT].isChecked())
        self.assertFalse(energy.scope_checks[DemandScope.IP].isChecked())

    def test_remembered_targets_come_back(self) -> None:
        texts = (("10", "4"), ("", ""), ("", ""), ("", ""))
        dialog = self._dialog(remembered={0: texts})

        self.assertEqual(dialog.target_texts(), texts)
        dialog.table.item(1, 0).setText("7")
        self.assertEqual(dialog.remembered_targets()[0][1], ("7", ""))

    def test_settings_round_trip(self) -> None:
        from PyQt6.QtCore import QSettings
        import tempfile

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = QSettings(os.path.join(directory.name, "s.ini"), QSettings.Format.IniFormat)
        value = CorrectionSettings(0.002, 1.5, 30)
        scope = frozenset({DemandScope.UC_BT, DemandScope.IP})

        save_demand_correction_settings(settings, value, scope)

        self.assertEqual(load_demand_correction_settings(settings), (value, scope))

    def test_report_enables_apply_only_with_something_to_apply(self) -> None:
        good = DemandCorrectionReportDialog(
            make_result(PatamarStatus.CONVERGED, PatamarStatus.NOT_CONVERGED,
                        PatamarStatus.SKIPPED, PatamarStatus.CONVERGED),
            circuit_label="ALIMENTADOR",
        )
        bad = DemandCorrectionReportDialog(
            make_result(*(PatamarStatus.FLOW_DIVERGED,) * 4), circuit_label="ALIMENTADOR"
        )
        self.addCleanup(good.deleteLater)
        self.addCleanup(bad.deleteLater)

        self.assertTrue(good.apply_button.isEnabled())
        self.assertIn("NPAT 1", good.notes_label.text())
        self.assertEqual(good.table.item(0, 7).text(), "1,100000")
        self.assertFalse(bad.apply_button.isEnabled())


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class DemandCorrectionWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        # O auxiliar de montagem da janela usa o app da própria classe.
        test_powerflow_ui.PowerFlowUiTests.app = cls.app

    def setUp(self) -> None:
        self.helper = test_powerflow_ui.PowerFlowUiTests()
        RecordingWorker.created = []

    def _window(self, *, with_loads: bool = True):  # noqa: ANN202
        window = self.helper._window()
        self.addCleanup(window.close)
        self.helper._load_everything(window, with_loads=with_loads)
        return window

    def _deliver(self, window, result) -> None:  # noqa: ANN001
        """Entrega o resultado como o worker, com o retrato da partida."""

        window._demand_correction_snapshot = (
            *window._demand_correction_base_now(),
            window._cable_model,
            window._phase_configuration,
            window._exportable_generators(),
            window._opendss_line_parameter_mode,
        )

        class AcceptingReport:
            def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            def exec(self) -> int:
                return QDialog.DialogCode.Accepted

        with patch("circuit_viewer.main_window.DemandCorrectionReportDialog", AcceptingReport):
            window._on_demand_correction_finished(result)

    def test_the_action_lives_next_to_the_power_flow_and_explains_missing_loads(self) -> None:
        window = self._window(with_loads=False)
        tools = next(
            entry.menu() for entry in window.menuBar().actions() if entry.text() == "Ferramentas"
        )
        self.assertIn(window.demand_correction_action, tools.actions())
        self.assertTrue(window.demand_correction_action.isEnabled())
        self.assertFalse(window.clear_demand_correction_action.isEnabled())

        with patch("circuit_viewer.main_window.QMessageBox.warning") as warning, patch(
            "circuit_viewer.main_window.DemandCorrectionDialog"
        ) as dialog:
            window._run_demand_correction()

        warning.assert_called_once()
        self.assertIn("cargas", warning.call_args.args[2])
        dialog.assert_not_called()

    def test_running_hands_the_typed_targets_to_the_worker(self) -> None:
        window = self._window()
        targets = (PatamarTarget(10.0, 4.0),) + (PatamarTarget(),) * 3

        class AcceptingDialog:
            def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
                pass

            def exec(self) -> int:
                return QDialog.DialogCode.Accepted

            def remembered_targets(self):  # noqa: ANN202
                return {0: (("10", "4"),) + (("", ""),) * 3}

            def selected_circuit_index(self) -> int:
                return 0

            def selected_targets(self):  # noqa: ANN202
                return targets

            def selected_scope(self):  # noqa: ANN202
                return frozenset({DemandScope.CARGA})

            def selected_settings(self) -> CorrectionSettings:
                return CorrectionSettings()

        with patch("circuit_viewer.main_window.DemandCorrectionDialog", AcceptingDialog), patch(
            "circuit_viewer.main_window.DemandCorrectionWorker", RecordingWorker
        ), patch("circuit_viewer.main_window.QThread.start"), patch(
            "circuit_viewer.main_window.save_demand_correction_settings"
        ):
            window._run_demand_correction()

        (worker,) = RecordingWorker.created
        self.assertEqual(worker.args[3], 0)
        self.assertEqual(worker.args[4], targets)
        self.assertIs(worker.kwargs["loads"], window._load_model)
        self.assertIs(window._power_flow_worker, worker)
        # O slot do fluxo está ocupado: as operações pesadas ficam bloqueadas.
        self.assertFalse(window.power_flow_action.isEnabled())
        self.assertFalse(window.demand_correction_action.isEnabled())
        self.assertEqual(window._demand_correction_targets[0][0], ("10", "4"))

        thread = window._power_flow_thread
        window._on_demand_correction_thread_finished()
        self.assertIsNone(window._power_flow_thread)
        self.assertTrue(window.power_flow_action.isEnabled())
        if thread is not None:
            thread.deleteLater()

    def test_applying_keeps_the_factors_for_the_power_flow(self) -> None:
        window = self._window()
        self.helper._install_result(window, self.helper._result(window))
        self.assertIsNotNone(window._power_flow_result)

        self._deliver(window, make_result(*(PatamarStatus.CONVERGED,) * 4))

        corrections = window._active_demand_corrections()
        self.assertEqual(set(corrections), {"C1"})
        self.assertEqual(corrections["C1"].factors[0], (1.1, 0.9))
        # O fluxo na tela era da carga anterior.
        self.assertIsNone(window._power_flow_result)
        self.assertFalse(window.demand_correction_status.isHidden())
        self.assertIn("C1", window.demand_correction_status.text())
        self.assertTrue(window.clear_demand_correction_action.isEnabled())

        with patch("circuit_viewer.main_window.PowerFlowWorker", RecordingWorker), patch(
            "circuit_viewer.main_window.QThread.start"
        ):
            window._run_power_flow()
        (worker,) = RecordingWorker.created
        self.assertEqual(set(worker.kwargs["demand_correction"]), {"C1"})
        thread = window._power_flow_thread
        window._on_power_flow_thread_finished()
        if thread is not None:
            thread.deleteLater()

    def test_new_loads_discard_the_factors(self) -> None:
        window = self._window()
        self._deliver(window, make_result(*(PatamarStatus.CONVERGED,) * 4))
        self.assertTrue(window._demand_corrections)

        loads = LoadModel(
            window._model, ["CG2"], [2], [""], ["CARGA-2"], [""], [""], [""], ["1"], [""]
        )
        window._on_load_import_finished(LoadCsvResult(loads, "utf-8-sig", 1, 1, 0, (), 0))

        self.assertIsNone(window._active_demand_corrections())
        self.assertFalse(window._demand_corrections)
        self.assertTrue(window.demand_correction_status.isHidden())

    def test_clearing_returns_to_the_base_load(self) -> None:
        window = self._window()
        self._deliver(window, make_result(*(PatamarStatus.CONVERGED,) * 4))

        window.clear_demand_correction_action.trigger()

        self.assertIsNone(window._active_demand_corrections())
        self.assertFalse(window.clear_demand_correction_action.isEnabled())

    def test_a_stale_result_is_not_offered(self) -> None:
        window = self._window()
        window._demand_correction_snapshot = None

        with patch("circuit_viewer.main_window.DemandCorrectionReportDialog") as report:
            window._on_demand_correction_finished(make_result(*(PatamarStatus.CONVERGED,) * 4))

        report.assert_not_called()
        self.assertFalse(window._demand_corrections)

    def test_the_power_flow_report_shows_the_head_next_to_the_factors(self) -> None:
        window = self._window()
        self._deliver(window, make_result(*(PatamarStatus.CONVERGED,) * 4))
        base = self.helper._result(window)
        result = PowerFlowResult(
            catalog=base.catalog,
            cables=base.cables,
            phase_configuration=base.phase_configuration,
            loads=base.loads,
            patterns=base.patterns,
            step_count=4,
            segment_currents=base.segment_currents,
            bar_voltages=base.bar_voltages,
            solved_circuits=("C1",),
            head_powers={"C1": ((10.0, 4.0),) * 4},
            demand_correction=window._active_demand_corrections(),
        )
        shown: list[str] = []

        def capture(message) -> int:  # noqa: ANN001
            shown.append(message.informativeText())
            return 0

        with patch.object(QMessageBox, "exec", capture):
            window._show_power_flow_report(result)

        self.assertEqual(len(shown), 1)
        self.assertIn("NPAT 0: P 10.00 kW, Q 4.00 kvar (fP 1.1, fQ 0.9)", shown[0])


if __name__ == "__main__":
    unittest.main()
