"""O vigia grava no crash.log as pilhas de quando a interface travou.

Roda num processo à parte: o ``dump_traceback_later`` do faulthandler é um só
por processo e não pode disputar com o do pytest.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

try:
    import PyQt6  # noqa: F401

    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - ambiente sem PyQt6
    PYQT_AVAILABLE = False

REPOSITORY = Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent(
    """
    import ctypes, os, sys, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    import circuit_viewer.__main__ as entry

    app = QApplication([])
    entry._crash_log = open(sys.argv[1], "a", encoding="utf-8")
    # Folga bem acima do pulso de 1 s, como nos 5 s do app.
    entry._start_freeze_watchdog(app, dump_seconds=2.0, stale_seconds=2.5)

    def interface_parada():
        if sys.argv[2] == "gil":
            # Uma chamada nativa que não solta o GIL: nem a vigia roda.
            ctypes.PyDLL("kernel32").Sleep(5000)
        else:
            time.sleep(5.0)
        QTimer.singleShot(2500, app.quit)

    QTimer.singleShot(1500, interface_parada)
    sys.exit(app.exec())
    """
)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está disponível")
class FreezeWatchdogTests(unittest.TestCase):
    def _run(self, mode: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "crash.log"
            script = Path(directory) / "travar.py"
            script.write_text(SCRIPT, encoding="utf-8")
            environment = dict(os.environ, PYTHONPATH=str(REPOSITORY))
            completed = subprocess.run(
                [sys.executable, str(script), str(log), mode],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return log.read_text(encoding="utf-8")

    def test_a_stalled_interface_leaves_the_stacks(self) -> None:
        text = self._run("espera")
        self.assertIn("--- Interface sem resposta desde", text)
        self.assertIn("Timeout (0:00:02)!", text)
        # A pilha mostra onde a interface estava parada.
        self.assertIn("interface_parada", text)
        self.assertIn("--- Interface voltou a responder após", text)

    @unittest.skipUnless(os.name == "nt", "a chamada que prende o GIL usa o kernel32")
    def test_a_held_gil_still_leaves_the_stacks(self) -> None:
        text = self._run("gil")
        self.assertIn("Timeout (0:00:02)!", text)
        self.assertIn("interface_parada", text)

    def test_a_healthy_interface_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "crash.log"
            script = Path(directory) / "normal.py"
            script.write_text(
                SCRIPT.replace(
                    "QTimer.singleShot(1500, interface_parada)",
                    "QTimer.singleShot(4000, app.quit)",
                ),
                encoding="utf-8",
            )
            environment = dict(os.environ, PYTHONPATH=str(REPOSITORY))
            completed = subprocess.run(
                [sys.executable, str(script), str(log), "espera"],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(log.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
