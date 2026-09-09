from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.main_window import MainWindow, _close_progress_dialog

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


if PYQT_AVAILABLE:
    class SignalProbe(QObject):
        triggered = pyqtSignal()


class ReentrantProgressDialog:
    """Dublê que executa a limpeza da operação dentro de ``setValue``."""

    def __init__(self, on_value=None) -> None:  # noqa: ANN001
        self.on_value = on_value
        self.calls: list[tuple[object, ...]] = []
        self.value_was_set = False

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self._before_value("range", minimum, maximum)

    def setMinimum(self, minimum: int) -> None:  # noqa: N802
        self._before_value("minimum", minimum)

    def setMaximum(self, maximum: int) -> None:  # noqa: N802
        self._before_value("maximum", maximum)

    def setLabelText(self, label: str) -> None:  # noqa: N802
        self._before_value("label", label)

    def setValue(self, value: int) -> None:  # noqa: N802
        self.calls.append(("value", value))
        self.value_was_set = True
        if self.on_value is not None:
            self.on_value()

    def close(self) -> None:
        self.calls.append(("close",))

    def _before_value(self, *call: object) -> None:
        if self.value_was_set:
            raise AssertionError("o diálogo foi acessado depois de setValue()")
        self.calls.append(call)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class ProgressDialogLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_every_progress_callback_tolerates_reentrant_cleanup(self) -> None:
        cases = (
            (
                "_import_worker",
                "_progress_dialog",
                lambda: self.window._on_import_progress(4, 2, 10),
            ),
            (
                "_generator_update_worker",
                "_generator_update_progress_dialog",
                lambda: self.window._on_generator_update_progress(2, 10),
            ),
            (
                "_export_worker",
                "_export_progress_dialog",
                lambda: self.window._on_export_progress(2, 10),
            ),
            (
                "_power_flow_worker",
                "_power_flow_progress_dialog",
                lambda: self.window._on_power_flow_progress(2, 10),
            ),
            (
                "_branch_worker",
                "_branch_progress_dialog",
                lambda: self.window._on_branch_analysis_progress(2, 10),
            ),
            (
                "_equivalent_worker",
                "_equivalent_progress_dialog",
                lambda: self.window._on_equivalent_progress(2, 10),
            ),
            (
                "_branch_json_worker",
                "_branch_json_progress_dialog",
                lambda: self.window._on_branch_json_progress(2, 10),
            ),
            (
                "_branch_csv_worker",
                "_branch_csv_progress_dialog",
                lambda: self.window._on_branch_csv_progress(2, 10),
            ),
        )

        for worker_attr, dialog_attr, callback in cases:
            with self.subTest(dialog=dialog_attr):
                setattr(self.window, worker_attr, object())
                dialog = ReentrantProgressDialog(
                    lambda attr=dialog_attr: setattr(self.window, attr, None)
                )
                setattr(self.window, dialog_attr, dialog)

                callback()

                self.assertEqual(dialog.calls[-1][0], "value")
                self.assertIsNone(getattr(self.window, dialog_attr))
                callback()

    def test_export_progress_survives_thread_cleanup_inside_set_value(self) -> None:
        self.window._export_thread = object()  # type: ignore[assignment]
        self.window._export_worker = object()  # type: ignore[assignment]
        dialog = ReentrantProgressDialog(
            self.window._on_export_thread_finished
        )
        self.window._export_progress_dialog = dialog  # type: ignore[assignment]

        self.window._on_export_progress(10, 10)

        self.assertIsNone(self.window._export_progress_dialog)
        self.assertIsNone(self.window._export_thread)
        self.assertIn(("close",), dialog.calls)
        self.assertEqual(dialog.calls[-2][0], "value")

    def test_stale_worker_progress_does_not_touch_current_dialog(self) -> None:
        cases = (
            (
                "_import_worker",
                "_progress_dialog",
                lambda: self.window._on_import_progress(4, 5, 10),
            ),
            (
                "_generator_update_worker",
                "_generator_update_progress_dialog",
                lambda: self.window._on_generator_update_progress(5, 10),
            ),
            (
                "_export_worker",
                "_export_progress_dialog",
                lambda: self.window._on_export_progress(5, 10),
            ),
            (
                "_power_flow_worker",
                "_power_flow_progress_dialog",
                lambda: self.window._on_power_flow_progress(5, 10),
            ),
            (
                "_branch_worker",
                "_branch_progress_dialog",
                lambda: self.window._on_branch_analysis_progress(5, 10),
            ),
            (
                "_equivalent_worker",
                "_equivalent_progress_dialog",
                lambda: self.window._on_equivalent_progress(5, 10),
            ),
            (
                "_branch_json_worker",
                "_branch_json_progress_dialog",
                lambda: self.window._on_branch_json_progress(5, 10),
            ),
            (
                "_branch_csv_worker",
                "_branch_csv_progress_dialog",
                lambda: self.window._on_branch_csv_progress(5, 10),
            ),
        )

        for worker_attr, dialog_attr, callback in cases:
            with self.subTest(dialog=dialog_attr):
                current_worker = object()
                stale_worker = SignalProbe()
                dialog = ReentrantProgressDialog()
                setattr(self.window, worker_attr, stale_worker)
                self.window._connect_operation_signal(stale_worker.triggered, stale_worker, callback)
                setattr(self.window, worker_attr, current_worker)
                setattr(self.window, dialog_attr, dialog)

                with patch.object(MainWindow, "sender", side_effect=AssertionError("sender não deve ser consultado")):
                    stale_worker.triggered.emit()

                self.assertEqual(dialog.calls, [])
                setattr(self.window, worker_attr, None)
                setattr(self.window, dialog_attr, None)

    def test_stale_thread_cannot_clear_a_new_operation(self) -> None:
        cases = (
            (
                "_import_thread",
                "_import_worker",
                "_progress_dialog",
                self.window._on_import_thread_finished,
            ),
            (
                "_generator_update_thread",
                "_generator_update_worker",
                "_generator_update_progress_dialog",
                self.window._on_generator_update_thread_finished,
            ),
            (
                "_export_thread",
                "_export_worker",
                "_export_progress_dialog",
                self.window._on_export_thread_finished,
            ),
            (
                "_power_flow_thread",
                "_power_flow_worker",
                "_power_flow_progress_dialog",
                self.window._on_power_flow_thread_finished,
            ),
            (
                "_branch_thread",
                "_branch_worker",
                "_branch_progress_dialog",
                self.window._on_branch_analysis_thread_finished,
            ),
            (
                "_equivalent_thread",
                "_equivalent_worker",
                "_equivalent_progress_dialog",
                self.window._on_equivalent_thread_finished,
            ),
            (
                "_branch_json_thread",
                "_branch_json_worker",
                "_branch_json_progress_dialog",
                self.window._on_branch_json_thread_finished,
            ),
            (
                "_branch_csv_thread",
                "_branch_csv_worker",
                "_branch_csv_progress_dialog",
                self.window._on_branch_csv_thread_finished,
            ),
        )

        for thread_attr, worker_attr, dialog_attr, callback in cases:
            with self.subTest(dialog=dialog_attr):
                current_thread = object()
                stale_thread = SignalProbe()
                current_worker = object()
                dialog = ReentrantProgressDialog()
                setattr(self.window, thread_attr, stale_thread)
                self.window._connect_operation_signal(stale_thread.triggered, stale_thread, callback)
                setattr(self.window, thread_attr, current_thread)
                setattr(self.window, worker_attr, current_worker)
                setattr(self.window, dialog_attr, dialog)

                with patch.object(MainWindow, "sender", side_effect=AssertionError("sender não deve ser consultado")):
                    stale_thread.triggered.emit()

                self.assertIs(getattr(self.window, thread_attr), current_thread)
                self.assertIs(getattr(self.window, worker_attr), current_worker)
                self.assertIs(getattr(self.window, dialog_attr), dialog)
                self.assertEqual(dialog.calls, [])
                setattr(self.window, thread_attr, None)
                setattr(self.window, worker_attr, None)
                setattr(self.window, dialog_attr, None)

    def test_close_helper_does_not_require_a_live_dialog(self) -> None:
        _close_progress_dialog(None)
        dialog = ReentrantProgressDialog()

        _close_progress_dialog(dialog)  # type: ignore[arg-type]

        self.assertEqual(dialog.calls, [("close",)])


if __name__ == "__main__":
    unittest.main()
