"""Ponto de entrada da aplicação."""

from __future__ import annotations

import faulthandler
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# Mantido aberto durante todo o processo: o faulthandler grava nele direto do
# sinal de falha, quando já não dá para abrir arquivo algum.
_crash_log = None

# A interface parada por este tempo já aparece como "Não está respondendo" faz
# tempo: o vigia grava então a pilha de todas as threads no crash.log.
FREEZE_DUMP_SECONDS = 20.0
# Sem pulsação da interface por este tempo, o vigia para de adiar a gravação.
FREEZE_STALE_SECONDS = 5.0


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


def _log_line(text: str) -> None:
    if _crash_log is None:
        return
    try:
        _crash_log.write(text + "\n")
        _crash_log.flush()
    except (OSError, ValueError):
        pass


def _start_freeze_watchdog(
    app,  # noqa: ANN001
    *,
    dump_seconds: float = FREEZE_DUMP_SECONDS,
    stale_seconds: float = FREEZE_STALE_SECONDS,
) -> None:
    """Grava no ``crash.log`` onde cada thread estava se a interface travar.

    Um travamento não levanta exceção nem derruba o processo, então nada iria
    para o log. Um ``QTimer`` marca a pulsação da interface, e uma thread vigia
    rearma o ``dump_traceback_later`` do faulthandler enquanto ela está em dia.
    Se a interface parar, o rearme para e, no prazo, a thread em C do
    faulthandler grava as pilhas sem precisar do GIL, que pode ser justamente o
    que prende a interface. Quem rearma é a vigia, e não a interface, porque
    cancelar o prazo anterior espera a thread do faulthandler terminar.
    """

    if _crash_log is None:
        return
    from PyQt6.QtCore import QTimer

    beat = [time.monotonic()]
    stop = threading.Event()
    pulse = QTimer(app)
    pulse.setInterval(1_000)
    pulse.timeout.connect(lambda: beat.__setitem__(0, time.monotonic()))
    pulse.start()

    def watch() -> None:
        stalled_since: float | None = None
        last = time.monotonic()
        while not stop.wait(1.0):
            now = time.monotonic()
            # A própria vigia atrasada é o processo inteiro parado (suspensão do
            # Windows) ou o GIL preso; neste caso a gravação já está armada.
            suspended, last = now - last > 1.0 + stale_seconds, now
            silence = now - beat[0]
            if silence < stale_seconds or suspended:
                if stalled_since is not None and not suspended:
                    _log_line(
                        "--- Interface voltou a responder após "
                        f"{now - stalled_since:.0f} s ---"
                    )
                stalled_since = None
                faulthandler.dump_traceback_later(dump_seconds, file=_crash_log)
            elif stalled_since is None:
                stalled_since = beat[0]
                since = datetime.now() - timedelta(seconds=silence)
                _log_line(f"--- Interface sem resposta desde {since:%H:%M:%S} ---")
        faulthandler.cancel_dump_traceback_later()

    watcher = threading.Thread(target=watch, name="vigia-da-interface", daemon=True)
    watcher.start()

    def finish() -> None:
        stop.set()
        watcher.join(2.0)

    app.aboutToQuit.connect(finish)


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
    # Depois da janela: montá-la não é travamento, e o laço começa logo abaixo.
    _start_freeze_watchdog(app)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
