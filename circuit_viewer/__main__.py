"""Ponto de entrada da aplicação."""

from __future__ import annotations

import sys
import traceback


def _exception_hook(exc_type, exc_value, exc_traceback) -> None:  # noqa: ANN001
    traceback.print_exception(exc_type, exc_value, exc_traceback)


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
    # Antes da janela: aplicar depois faria a interface piscar no tema anterior.
    apply_theme(app, load_theme_preference(QSettings()))
    window = MainWindow()
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

