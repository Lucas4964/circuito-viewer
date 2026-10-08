"""Ponto de entrada da aplicação."""

from __future__ import annotations

import faulthandler
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Mantido aberto durante todo o processo: o faulthandler grava nele direto do
# sinal de falha, quando já não dá para abrir arquivo algum.
_crash_log = None


def _open_crash_log() -> None:
    """Liga o registro de falhas em ``crash.log`` na pasta de dados do app.

    Sem console (``pythonw``), uma exceção num slot ou uma falha nativa do Qt
    fecharia a janela sem deixar rastro. O faulthandler grava a pilha Python de
    todas as threads mesmo numa violação de acesso.
    """

    global _crash_log
    from PyQt6.QtCore import QStandardPaths

    try:
        directory = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
        )
        directory.mkdir(parents=True, exist_ok=True)
        _crash_log = open(directory / "crash.log", "a", encoding="utf-8")  # noqa: SIM115
        _crash_log.write(f"\n=== Início {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        _crash_log.flush()
        faulthandler.enable(file=_crash_log, all_threads=True)
    except OSError:
        _crash_log = None


def _exception_hook(exc_type, exc_value, exc_traceback) -> None:  # noqa: ANN001
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    if _crash_log is not None:
        try:
            _crash_log.write(f"--- Exceção {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
            traceback.print_exception(
                exc_type, exc_value, exc_traceback, file=_crash_log
            )
            _crash_log.flush()
        except (OSError, ValueError):
            pass


def main() -> int:
    try:
        from PyQt6.QtCore import QSettings
        from PyQt6.QtWidgets import QApplication
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "PyQt6 não está instalado. Execute 'python -m pip install -e .' "
            "no ambiente virtual do projeto."
        ) from exc

    from .main_window import MainWindow
    from .theme import apply_theme, load_theme_preference

    sys.excepthook = _exception_hook
    app = QApplication(sys.argv)
    app.setApplicationName("Visualizador de Circuitos Elétricos")
    app.setOrganizationName("Circuit Viewer")
    # Depois dos nomes: são eles que definem a pasta de dados do aplicativo.
    _open_crash_log()
    # Antes da janela: aplicar depois faria a interface piscar no tema anterior.
    apply_theme(app, load_theme_preference(QSettings()))
    window = MainWindow()
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
