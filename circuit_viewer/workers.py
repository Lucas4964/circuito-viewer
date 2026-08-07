"""Workers Qt para operações que não devem bloquear a interface."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from .branch_analysis import BranchAnalysisResult, analyze_branches
from .cable_import import load_cables_csv
from .circuit_import import load_circuits_csv
from .csv_import import CsvImportCancelled, load_csv
from .load_import import load_loads_csv
from .load_pattern_import import load_load_patterns_csv
from .equivalent_network import build_equivalent_network
from .mdb_engine import open_database
from .mdb_import import load_database
from .model import (
    CableModel,
    CircuitCatalogModel,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
    SwitchModel,
    UtmCrs,
)
from .opendss_engine import acquire_engine, ascii_workspace
from .opendss_export import build_export
from .opendss_powerflow import run_power_flow
from .opendss_settings import OpenDssLoadSettings
from .phase_config import PhaseConfiguration
from .regulator_import import load_regulators_csv
from .segment_import import load_segments_csv
from .switch_import import load_switches_csv


class CsvImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str, crs: UtmCrs, scale: float = 1.0) -> None:
        super().__init__()
        self.path = path
        self.crs = crs
        self.scale = float(scale)
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
                scale=self.scale,
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


class LoadImportWorker(QObject):
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
            result = load_loads_csv(
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


class LoadPatternImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str, loads: LoadModel) -> None:
        super().__init__()
        self.path = path
        self.loads = loads
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_load_patterns_csv(
                self.path,
                self.loads,
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


class RegulatorImportWorker(QObject):
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
            result = load_regulators_csv(
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


class CableImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_cables_csv(
                self.path,
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


class MdbImportWorker(QObject):
    """Importa as oito entidades de um banco Access numa única execução.

    A cadeia inteira roda num worker só porque cada importador recebe o modelo
    do anterior: dividir em oito threads exigiria sequenciá-las de qualquer
    forma, e ainda multiplicaria as revalidações de identidade na chegada.

    A conexão é aberta **aqui dentro**, na thread secundária, e fechada antes de
    o sinal ser emitido: uma conexão ODBC não é segura para atravessar threads.
    """

    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        path: str,
        crs: UtmCrs,
        *,
        password: str | None = None,
        entities: tuple[str, ...] | None = None,
        overrides: dict[str, str] | None = None,
        scale: float = 1.0,
    ) -> None:
        super().__init__()
        self.path = path
        self.crs = crs
        # A senha vive só na memória do worker e nunca é gravada nem registrada.
        self._password = password
        self.entities = entities
        self.overrides = dict(overrides or {})
        self.scale = float(scale)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            with open_database(self.path, self._password) as database:
                result = load_database(
                    database,
                    self.crs,
                    source_path=self.path,
                    overrides=self.overrides,
                    entities=self.entities,
                    scale=self.scale,
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


class OpenDssExportWorker(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        catalog: CircuitCatalogModel,
        cables: CableModel,
        phase_configuration: PhaseConfiguration,
        circuit_indices: tuple[int, ...],
        loads: LoadModel | None = None,
        patterns: LoadPatternModel | None = None,
        regulators: RegulatorModel | None = None,
        load_settings: OpenDssLoadSettings | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.cables = cables
        self.phase_configuration = phase_configuration
        self.circuit_indices = tuple(circuit_indices)
        self.loads = loads
        self.patterns = patterns
        self.regulators = regulators
        self.load_settings = load_settings
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = build_export(
                self.catalog,
                self.cables,
                self.phase_configuration,
                self.circuit_indices,
                loads=self.loads,
                patterns=self.patterns,
                regulators=self.regulators,
                load_settings=self.load_settings,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class PowerFlowWorker(QObject):
    """Gera o modelo OpenDSS de cada circuito, resolve e devolve as grandezas.

    Segue o mesmo contrato dos demais workers, com uma diferença obrigatória: o
    ``except`` cobre ``BaseException``. A biblioteca do OpenDSS chama ``exit()``
    quando a DLL não inicia, e ``SystemExit`` não deriva de ``Exception`` —
    escaparia daqui e derrubaria a thread sem mensagem alguma para o usuário.
    """

    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        catalog: CircuitCatalogModel,
        cables: CableModel,
        phase_configuration: PhaseConfiguration,
        circuit_indices: tuple[int, ...],
        loads: LoadModel | None = None,
        patterns: LoadPatternModel | None = None,
        regulators: RegulatorModel | None = None,
        load_settings: OpenDssLoadSettings | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.cables = cables
        self.phase_configuration = phase_configuration
        self.circuit_indices = tuple(circuit_indices)
        self.loads = loads
        self.patterns = patterns
        self.regulators = regulators
        self.load_settings = load_settings
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            # A pasta de trabalho é descartada ao sair: os .dss aqui são meio, e
            # não produto — quem quer os arquivos usa a exportação.
            with ascii_workspace() as workspace, acquire_engine() as engine:
                result = run_power_flow(
                    engine,
                    self.catalog,
                    self.cables,
                    self.phase_configuration,
                    self.circuit_indices,
                    workspace=workspace,
                    loads=self.loads,
                    patterns=self.patterns,
                    regulators=self.regulators,
                    load_settings=self.load_settings,
                    cancel_check=self._cancel_event.is_set,
                    progress=lambda current, total: self.progress.emit(
                        current, total
                    ),
                )
        except InterruptedError:
            self.cancelled.emit()
        except BaseException as exc:  # noqa: BLE001 — inclui o exit() da DLL
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.finished.emit(result)


class BranchAnalysisWorker(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        catalog: CircuitCatalogModel,
        phase_configuration: PhaseConfiguration,
        loads: LoadModel | None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.phase_configuration = phase_configuration
        self.loads = loads
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = analyze_branches(
                self.catalog,
                self.phase_configuration,
                self.loads,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class EquivalentNetworkWorker(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        branches: BranchAnalysisResult,
        loads: LoadModel | None,
        patterns: LoadPatternModel | None,
    ) -> None:
        super().__init__()
        self.branches = branches
        self.loads = loads
        self.patterns = patterns
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = build_equivalent_network(
                self.branches,
                self.loads,
                self.patterns,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)
