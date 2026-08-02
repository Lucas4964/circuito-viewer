"""Workers Qt para operações que não devem bloquear a interface."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from .circuit_import import load_circuits_csv
from .csv_import import CsvImportCancelled, load_csv
from .model import CircuitModel, LineNetworkModel, SwitchModel, UtmCrs
from .segment_import import load_segments_csv
from .switch_import import load_switches_csv


class CsvImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str, crs: UtmCrs) -> None:
        super().__init__()
        self.path = path
        self.crs = crs
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Pode ser chamado diretamente pela thread da interface."""

        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_csv(
                self.path,
                self.crs,
                cancel_event=self._cancel_event,
                progress=lambda rows, current, total: self.progress.emit(
                    rows, current, total
                ),
            )
        except CsvImportCancelled:
            self.cancelled.emit()
        except Exception as exc:  # a mensagem será apresentada na UI
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class SegmentImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str, bars: CircuitModel) -> None:
        super().__init__()
        self.path = path
        self.bars = bars
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_segments_csv(
                self.path,
                self.bars,
                cancel_event=self._cancel_event,
                progress=lambda rows, current, total: self.progress.emit(
                    rows, current, total
                ),
            )
        except CsvImportCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class SwitchImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str, segments: LineNetworkModel) -> None:
        super().__init__()
        self.path = path
        self.segments = segments
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_switches_csv(
                self.path,
                self.segments,
                cancel_event=self._cancel_event,
                progress=lambda rows, current, total: self.progress.emit(
                    rows, current, total
                ),
            )
        except CsvImportCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class CircuitImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        path: str,
        segments: LineNetworkModel,
        switches: SwitchModel | None,
    ) -> None:
        super().__init__()
        self.path = path
        self.segments = segments
        self.switches = switches
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_circuits_csv(
                self.path,
                self.segments,
                self.switches,
                cancel_event=self._cancel_event,
                progress=lambda rows, current, total: self.progress.emit(
                    rows, current, total
                ),
            )
        except CsvImportCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)
