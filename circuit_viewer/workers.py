"""Workers Qt para operações que não devem bloquear a interface."""

from __future__ import annotations

from decimal import Decimal
import threading
import re

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from .branch_analysis import BranchAnalysisResult, analyze_branches
from .branch_json_export import export_branches_json
from .branch_power_flow import measure_branch_powers
from .branch_power_source import (
    DEFAULT_BRANCH_POWER_SOURCE,
    BranchPowerSource,
)
from .branch_table_export import export_branches_csv
from .allocation import TransformerAllocationModel
from .allocation_measurements import (
    AllocationMeasurementModel,
    load_allocation_measurements_csv,
)
from .cable_import import load_cables_csv
from .circuit_import import load_circuits_csv
from .circuit_level_import import load_circuit_levels_csv
from .csv_import import CsvImportCancelled, load_csv
from .generator_import import load_generators_csv
from .generator_update import (
    GeneratorScheduleMode,
    GeneratorUpdateModel,
    calculate_generator_demands,
)
from .curvas import Curve
from .calculation_levels import CalculationLevelSchedule
from .capacitor_import import load_capacitors_csv
from .load_import import load_loads_csv
from .load_pattern_import import load_load_patterns_csv
from .equivalent_network import EquivalentNetworkResult, build_equivalent_network
from .mdb_engine import MdbEngineError, MdbPasswordError, open_database
from .mdb_inspection import inspect_database
from .network_registry import FileIdentity, register_import
from .mdb_import import (
    MdbSourceLoad,
    dataset_from_result,
    load_database,
)
from .model import (
    CapacitorModel,
    CableModel,
    CircuitCatalogModel,
    CircuitModel,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
    SwitchModel,
    UtmCrs,
)
from .source_composition import (
    SourceWorkspace,
    compose,
)
from .opendss_engine import acquire_engine, ascii_workspace
from .opendss_allocation_export import build_allocation_export
from .opendss_allocation_settings import OpenDssAllocationSettings
from .opendss_export import build_export
from .opendss_library import OpenDssLibraryCatalog
from .opendss_line_mode import OpenDssLineParameterMode
from .opendss_mapping_store import OpenDssLibraryMappings
from .opendss_simplified_export import build_simplified_export
from .opendss_powerflow import run_power_flow
from .opendss_settings import OpenDssLoadSettings
from .opendss_solution import DEFAULT_MAX_POWER_FLOW_ITER
from .phase_config import PhaseConfiguration
from .switch_types import SwitchTypeConfiguration
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


class CapacitorImportWorker(QObject):
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
            result = load_capacitors_csv(
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


class GeneratorImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    stageChanged = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        generator_path: str,
        consumer_path: str,
        loads: LoadModel,
    ) -> None:
        super().__init__()
        self.generator_path = generator_path
        self.consumer_path = consumer_path
        self.loads = loads
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_generators_csv(
                self.generator_path,
                self.consumer_path,
                self.loads,
                cancel_event=self._cancel_event,
                progress=lambda rows, current, total: self.progress.emit(
                    rows, current, total
                ),
                stage=self.stageChanged.emit,
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


class CircuitLevelImportWorker(QObject):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str, circuits: CircuitCatalogModel) -> None:
        super().__init__()
        self.path = path
        self.circuits = circuits
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_circuit_levels_csv(
                self.path,
                self.circuits,
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


class AllocationMeasurementImportWorker(QObject):
    """Importa as quatro correntes NPAT de cada circuito informado no CSV."""

    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, path: str, circuits: CircuitCatalogModel) -> None:
        super().__init__()
        self.path = path
        self.circuits = circuits
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = load_allocation_measurements_csv(
                self.path,
                self.circuits,
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


class MdbInspectionWorker(QObject):
    """Abre e fecha a conexão na própria thread antes de entregar o retrato."""

    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    password_required = pyqtSignal(int)
    completed = pyqtSignal()

    def __init__(self, request_id, path, *, password=None, table_mapping=None, overrides=None):
        super().__init__()
        self.request_id = request_id
        self.path = path
        self._password = password
        self.table_mapping = table_mapping
        self.overrides = dict(overrides or {})
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self._cancel_event.is_set():
                return
            with open_database(self.path, self._password) as database:
                result = inspect_database(database, self.table_mapping,
                                          overrides=self.overrides,
                                          cancel_event=self._cancel_event)
            if not self._cancel_event.is_set():
                self.finished.emit(self.request_id, result)
        except MdbPasswordError:
            if not self._cancel_event.is_set():
                self.password_required.emit(self.request_id)
        except InterruptedError:
            pass
        except MdbEngineError as exc:
            if not self._cancel_event.is_set():
                message = str(exc)
                if self._password:
                    message = message.replace(self._password, "[senha omitida]")
                message = re.sub(r"(?i)\bPWD\s*=.*", "PWD=[omitida]", message)
                self.failed.emit(self.request_id, message)
        except Exception:
            if not self._cancel_event.is_set():
                # Exceções ODBC podem conter a conexão, incluindo PWD.
                self.failed.emit(self.request_id,
                                 "Não foi possível ler o banco. Verifique o arquivo e o driver Access.")
        finally:
            self._password = None
            self.completed.emit()


class MdbImportWorker(QObject):
    """Importa as dez entidades lógicas de um banco Access numa única execução.

    A cadeia inteira roda num worker só porque cada importador recebe o modelo
    do anterior: dividir em dez threads exigiria sequenciá-las de qualquer
    forma, e ainda multiplicaria as revalidações de identidade na chegada.

    A conexão é aberta **aqui dentro**, na thread secundária, e fechada antes de
    o sinal ser emitido: uma conexão ODBC não é segura para atravessar threads.
    """

    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()
    review_required = pyqtSignal(object)
    project_review_required = pyqtSignal(object)
    field_review_required = pyqtSignal(object)

    def __init__(
        self,
        path: str,
        crs: UtmCrs,
        *,
        password: str | None = None,
        entities: tuple[str, ...] | None = None,
        overrides: dict[str, str] | None = None,
        scale: float = 1.0,
        phase_configuration: PhaseConfiguration | None = None,
        switch_types: SwitchTypeConfiguration | None = None,
        circuit_ids: tuple[str, ...] = (),
        workspace: SourceWorkspace | None = None,
        replace_workspace: bool = True,
        target_tag: str | None = None,
        correspondences: tuple = (),
        project=None,
    ) -> None:
        super().__init__()
        self.path = path
        self.crs = crs
        # A senha vive só na memória do worker e nunca é gravada nem registrada.
        self._password = password
        self.entities = entities
        self.overrides = dict(overrides or {})
        self.scale = float(scale)
        self.phase_configuration = phase_configuration
        self.switch_types = switch_types
        # Vazio quer dizer "o banco inteiro", nunca "nenhum circuito".
        self.circuit_ids = tuple(circuit_ids)
        # As fontes já carregadas são imutáveis, então atravessam a fronteira de
        # thread sem cuidado nenhum — e compor fora da thread da UI é o que
        # impede a janela de congelar com vários bancos grandes.
        self.workspace = workspace if workspace is not None else SourceWorkspace()
        self.replace_workspace = bool(replace_workspace)
        self.target_tag = target_tag
        self.correspondences = tuple(correspondences)
        self.project = project
        self._cancel_event = threading.Event()
        self._review_event = threading.Event()
        self._review_decisions = None

    def cancel(self) -> None:
        self._cancel_event.set()
        self._review_event.set()

    def resolve_review(self, decisions) -> None:
        """Chamado pela UI; só publica dados imutáveis e desperta o worker."""
        self._review_decisions = None if decisions is None else dict(decisions)
        self._review_event.set()

    def _review(self, conflicts):
        return self._request_review(self.review_required, conflicts)

    def _request_review(self, signal, payload):
        self._review_decisions = None
        self._review_event.clear()
        if self._cancel_event.is_set():
            raise InterruptedError()
        signal.emit(payload)
        while not self._review_event.wait(0.1):
            if self._cancel_event.is_set():
                raise InterruptedError()
        if self._cancel_event.is_set():
            raise InterruptedError()
        return self._review_decisions

    @pyqtSlot()
    def run(self) -> None:
        try:
            identity = FileIdentity.read(self.path, self._cancel_event.is_set)
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
                    phase_configuration=self.phase_configuration,
                    switch_types=self.switch_types,
                )
            # A leitura acabou e a conexão já fechou: o que vem a seguir é só
            # numpy, e pode ser cancelado do mesmo jeito.
            if FileIdentity.read(self.path, self._cancel_event.is_set) != identity:
                raise ValueError("O banco mudou durante a leitura. Tente novamente.")
            project_change = None
            if self.project is not None:
                from .project_state import propose_import, resolve_import, ProjectChangeSet
                proposal = propose_import(self.project, dataset_from_result(result, tag=self.workspace.next_tag()), identity,
                                          circuit_ids=self.circuit_ids, target_tag=self.target_tag,
                                          correspondences=self.correspondences, cancel_check=self._cancel_event.is_set)
                choices = self._request_review(self.project_review_required, proposal)
                if choices is None:
                    raise InterruptedError()
                project_change = resolve_import(self.project, proposal, choices, cancel_check=self._cancel_event.is_set)
                if not isinstance(project_change, ProjectChangeSet):
                    decisions = self._request_review(self.field_review_required, project_change)
                    if decisions is None:
                        raise InterruptedError()
                    project_change = resolve_import(self.project, proposal, choices, decisions,
                                                    cancel_check=self._cancel_event.is_set)
                if not isinstance(project_change, ProjectChangeSet):
                    raise ValueError("Ainda existem conflitos não resolvidos.")
                workspace, composed = project_change.state.workspace, project_change.composed
                dataset = workspace.dataset_for(proposal.dataset.tag) or proposal.dataset
            else:
                workspace, dataset = register_import(
                    self.workspace, dataset_from_result(result, tag=self.workspace.next_tag()), identity,
                    circuit_ids=self.circuit_ids, replace_workspace=self.replace_workspace,
                    target_tag=self.target_tag, correspondences=self.correspondences,
                    resolve_conflicts=self._review, cancel_check=self._cancel_event.is_set,
                )
                composed = compose(workspace.datasets, cancel_check=self._cancel_event.is_set)
        except CsvImportCancelled:
            self.cancelled.emit()
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            message = str(exc)
            if self._password:
                message = message.replace(self._password, "***")
            self.failed.emit(message)
        else:
            if self._cancel_event.is_set():
                self.cancelled.emit()
                return
            self.finished.emit(
                MdbSourceLoad(
                    dataset=dataset,
                    workspace=workspace,
                    composed=composed,
                    result=result,
                    project_change=project_change,
                )
            )
        finally:
            self._password = None


class ProjectOperationWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    @pyqtSlot()
    def run(self):
        try:
            result = self.operation(self._cancel_event.is_set)
            if self._cancel_event.is_set():
                raise InterruptedError()
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class GeneratorUpdateWorker(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        generators: GeneratorModel,
        circuits: CircuitCatalogModel,
        phase_configuration: PhaseConfiguration,
        curve: Curve,
        effective_schedules: tuple[CalculationLevelSchedule, ...],
        schedule_modes: tuple[GeneratorScheduleMode, ...],
    ) -> None:
        super().__init__()
        self.generators = generators
        self.circuits = circuits
        self.phase_configuration = phase_configuration
        self.curve = curve
        self.effective_schedules = effective_schedules
        self.schedule_modes = schedule_modes
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = calculate_generator_demands(
                self.generators,
                self.circuits,
                self.phase_configuration,
                self.curve,
                self.effective_schedules,
                self.schedule_modes,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
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
        cables: CableModel | None,
        phase_configuration: PhaseConfiguration,
        circuit_indices: tuple[int, ...],
        loads: LoadModel | None = None,
        patterns: LoadPatternModel | None = None,
        generator_updates: GeneratorUpdateModel | None = None,
        regulators: RegulatorModel | None = None,
        capacitors: CapacitorModel | None = None,
        load_settings: OpenDssLoadSettings | None = None,
        max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER,
        line_parameter_mode: OpenDssLineParameterMode = (
            OpenDssLineParameterMode.ORIGINAL
        ),
        library_catalog: OpenDssLibraryCatalog | None = None,
        library_mappings: OpenDssLibraryMappings | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.cables = cables
        self.phase_configuration = phase_configuration
        self.circuit_indices = tuple(circuit_indices)
        self.loads = loads
        self.patterns = patterns
        self.generator_updates = generator_updates
        self.regulators = regulators
        self.capacitors = capacitors
        self.load_settings = load_settings
        self.max_power_flow_iterations = int(max_power_flow_iterations)
        self.line_parameter_mode = line_parameter_mode
        self.library_catalog = library_catalog
        self.library_mappings = library_mappings
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
                generator_updates=self.generator_updates,
                regulators=self.regulators,
                capacitors=self.capacitors,
                load_settings=self.load_settings,
                max_power_flow_iterations=self.max_power_flow_iterations,
                line_parameter_mode=self.line_parameter_mode,
                library_catalog=self.library_catalog,
                library_mappings=self.library_mappings,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class OpenDssAllocationExportWorker(QObject):
    """Gera os quatro circuitos de alocação sem executar o OpenDSS."""

    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        catalog: CircuitCatalogModel,
        cables: CableModel | None,
        phase_configuration: PhaseConfiguration,
        circuit_index: int,
        allocations: TransformerAllocationModel,
        measurements: AllocationMeasurementModel,
        curve: Curve,
        schedule: CalculationLevelSchedule,
        settings: OpenDssAllocationSettings,
        *,
        regulators: RegulatorModel | None = None,
        load_settings: OpenDssLoadSettings | None = None,
        max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER,
        line_parameter_mode: OpenDssLineParameterMode = (
            OpenDssLineParameterMode.ORIGINAL
        ),
        library_catalog: OpenDssLibraryCatalog | None = None,
        library_mappings: OpenDssLibraryMappings | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.cables = cables
        self.phase_configuration = phase_configuration
        self.circuit_index = int(circuit_index)
        self.allocations = allocations
        self.measurements = measurements
        self.curve = curve
        self.schedule = schedule
        self.settings = settings
        self.regulators = regulators
        self.load_settings = load_settings
        self.max_power_flow_iterations = int(max_power_flow_iterations)
        self.line_parameter_mode = line_parameter_mode
        self.library_catalog = library_catalog
        self.library_mappings = library_mappings
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = build_allocation_export(
                self.catalog,
                self.cables,
                self.phase_configuration,
                self.circuit_index,
                self.allocations,
                self.measurements,
                self.curve,
                self.schedule,
                self.settings,
                regulators=self.regulators,
                load_settings=self.load_settings,
                max_power_flow_iterations=self.max_power_flow_iterations,
                line_parameter_mode=self.line_parameter_mode,
                library_catalog=self.library_catalog,
                library_mappings=self.library_mappings,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class SimplifiedOpenDssExportWorker(QObject):
    """Worker exclusivo da exportação da projeção simplificada."""

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
        equivalent: EquivalentNetworkResult,
        *,
        loads: LoadModel | None = None,
        patterns: LoadPatternModel | None = None,
        generator_updates: GeneratorUpdateModel | None = None,
        regulators: RegulatorModel | None = None,
        capacitors: CapacitorModel | None = None,
        load_settings: OpenDssLoadSettings | None = None,
        max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.cables = cables
        self.phase_configuration = phase_configuration
        self.circuit_indices = tuple(circuit_indices)
        self.equivalent = equivalent
        self.loads = loads
        self.patterns = patterns
        self.generator_updates = generator_updates
        self.regulators = regulators
        self.capacitors = capacitors
        self.load_settings = load_settings
        self.max_power_flow_iterations = int(max_power_flow_iterations)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = build_simplified_export(
                self.catalog,
                self.cables,
                self.phase_configuration,
                self.circuit_indices,
                equivalent=self.equivalent,
                loads=self.loads,
                patterns=self.patterns,
                generator_updates=self.generator_updates,
                regulators=self.regulators,
                capacitors=self.capacitors,
                load_settings=self.load_settings,
                max_power_flow_iterations=self.max_power_flow_iterations,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class BranchJsonExportWorker(QObject):
    """Serializa e grava atomicamente os ramais sem bloquear a interface."""

    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(
        self,
        path: str,
        branches: BranchAnalysisResult,
        equivalent: EquivalentNetworkResult,
        branch_indices: tuple[int, ...],
        interest_branch_ids: tuple[int, ...] = (),
    ) -> None:
        super().__init__()
        self.path = path
        self.branches = branches
        self.equivalent = equivalent
        self.branch_indices = tuple(branch_indices)
        self.interest_branch_ids = tuple(interest_branch_ids)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = export_branches_json(
                self.path,
                self.branches,
                self.equivalent,
                self.branch_indices,
                interest_branch_ids=self.interest_branch_ids,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(exc)
        else:
            self.finished.emit(result)


class BranchCsvExportWorker(QObject):
    """Grava a tabela de ramais em CSV pt-BR fora da thread da interface."""

    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(
        self,
        path: str,
        branches: BranchAnalysisResult,
        equivalent: EquivalentNetworkResult | None,
        branch_indices: tuple[int, ...],
    ) -> None:
        super().__init__()
        self.path = path
        self.branches = branches
        self.equivalent = equivalent
        self.branch_indices = tuple(branch_indices)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = export_branches_csv(
                self.path,
                self.branches,
                self.equivalent,
                self.branch_indices,
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(exc)
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
        cables: CableModel | None,
        phase_configuration: PhaseConfiguration,
        circuit_indices: tuple[int, ...],
        loads: LoadModel | None = None,
        patterns: LoadPatternModel | None = None,
        generator_updates: GeneratorUpdateModel | None = None,
        regulators: RegulatorModel | None = None,
        capacitors: CapacitorModel | None = None,
        load_settings: OpenDssLoadSettings | None = None,
        max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER,
        line_parameter_mode: OpenDssLineParameterMode = (
            OpenDssLineParameterMode.ORIGINAL
        ),
        library_catalog: OpenDssLibraryCatalog | None = None,
        library_mappings: OpenDssLibraryMappings | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.cables = cables
        self.phase_configuration = phase_configuration
        self.circuit_indices = tuple(circuit_indices)
        self.loads = loads
        self.patterns = patterns
        self.generator_updates = generator_updates
        self.regulators = regulators
        self.capacitors = capacitors
        self.load_settings = load_settings
        self.max_power_flow_iterations = int(max_power_flow_iterations)
        self.line_parameter_mode = line_parameter_mode
        self.library_catalog = library_catalog
        self.library_mappings = library_mappings
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
                    generator_updates=self.generator_updates,
                    regulators=self.regulators,
                    capacitors=self.capacitors,
                    load_settings=self.load_settings,
                    max_power_flow_iterations=self.max_power_flow_iterations,
                    line_parameter_mode=self.line_parameter_mode,
                    library_catalog=self.library_catalog,
                    library_mappings=self.library_mappings,
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
        generator_updates: GeneratorUpdateModel | None = None,
        *,
        power_source: BranchPowerSource = DEFAULT_BRANCH_POWER_SOURCE,
        power_flow: object | None = None,
    ) -> None:
        super().__init__()
        self.branches = branches
        self.loads = loads
        self.patterns = patterns
        self.generator_updates = generator_updates
        self.power_source = BranchPowerSource(power_source)
        self.power_flow = power_flow
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            measured: dict[int, tuple] = {}
            measurement_issues: dict[int, str] = {}
            measured_currents: dict[int, Decimal] = {}
            if self.power_source is BranchPowerSource.POWER_FLOW:
                if self.power_flow is None:
                    raise ValueError(
                        "O cálculo das potências dos ramais por fluxo de "
                        "potência exige um resultado de fluxo vigente."
                    )
                # A medição é a primeira metade do trabalho e usa a mesma barra
                # de progresso: os dois laços percorrem os mesmos ramais.
                (
                    measured,
                    measurement_issues,
                    measured_currents,
                ) = measure_branch_powers(
                    self.branches,
                    self.power_flow,
                    cancel_check=self._cancel_event.is_set,
                    progress=lambda current, total: self.progress.emit(
                        current,
                        total,
                    ),
                )
            result = build_equivalent_network(
                self.branches,
                self.loads,
                self.patterns,
                self.generator_updates,
                power_source=self.power_source,
                measured_patterns=measured,
                measurement_issues=measurement_issues,
                measured_currents=measured_currents,
                source_power_flow=(
                    self.power_flow
                    if self.power_source is BranchPowerSource.POWER_FLOW
                    else None
                ),
                cancel_check=self._cancel_event.is_set,
                progress=lambda current, total: self.progress.emit(current, total),
            )
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)
