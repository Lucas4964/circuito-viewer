"""Ponto de entrada do MDB Viewer em PyQt6."""

from __future__ import annotations

import os
import sys

from PyQt6.QtWidgets import QApplication

from mdb_viewer_app.main_window import MainWindow


def main() -> int:
    # Os testes usam o plugin sem tela. A variável fica persistente quando é
    # definida numa sessão PowerShell e não deve afetar a aplicação interativa.
    if os.environ.get("QT_QPA_PLATFORM", "").casefold() == "offscreen":
        os.environ.pop("QT_QPA_PLATFORM", None)
        print("QT_QPA_PLATFORM=offscreen removido para iniciar a interface normal.")
    app = QApplication(sys.argv)
    app.setApplicationName("MDB Viewer")
    app.setOrganizationName("MDB Viewer")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
