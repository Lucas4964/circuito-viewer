"""Janela principal do visualizador."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PyQt6.QtCore import QSettings, QSignalBlocker, QThread, QTimer, Qt
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsScene,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .branch_analysis import BranchAnalysisResult, BranchRecord
from .allocation import TransformerAllocationModel
from .allocation_measurements import (
    AllocationMeasurementCsvResult,
    AllocationMeasurementModel,
)
from .branch_json_export import (
    BranchJsonExportResult,
    BranchJsonValidationError,
    suggested_branch_json_filename,
)
from .branch_table_export import (
    BranchCsvExportResult,
    suggested_branch_csv_filename,
)
from .branch_window import BranchesWindow, BranchTableModel
from .calculation_levels import CalculationLevelSchedule
from .calculation_levels_store import load_calculation_levels
from .circuit_calculation_levels import (
    CircuitCalculationLevelsController,
    CircuitCalculationLevelsModel,
)
from .cable_import import CableCsvResult
from .cables_window import (
    CablesWindow,
    CableTableModel,
    cable_summary,
    cable_tooltip,
)
from .equivalent_network import EquivalentNetworkResult
from .circuit_import import CircuitLoadResult
from .circuit_level_import import CircuitLevelCsvResult
from .circuits_window import CircuitTableModel, CircuitsWindow
from .csv_import import (
    COORDINATE_UNITS,
    CsvLoadResult,
    detect_coordinate_scale,
)
from .curvas import Curve, CurveCatalog
from .curvas_store import load_curves
from .curvas_window import CurvesWindow
from .generator_update import (
    GeneratorScheduleMode,
    GeneratorUpdateModel,
    GeneratorUpdateResult,
)
from .generator_update_dialog import UpdateGeneratorsDialog
from .generator_update_table import (
    GeneratorDemandTableModel,
    GeneratorPhasePowerTableModel,
)
from .graphics import (
    BranchHighlightOverlayItem,
    DiagramView,
    ItemVirtualizer,
    LineNetworkItem,
    LoadLodCoordinator,
    LoadVirtualizer,
    load_layout_offsets_for_models,
    RegulatorNetworkItem,
    SegmentSelectionOverlayItem,
    SwitchNetworkItem,
)
from .generator_import import GeneratorCsvResult
from .load_import import LoadCsvResult
from .load_pattern_import import LoadPatternCsvResult
from .load_pattern_table import LoadPatternTableModel
from .mdb_engine import (
    MdbEngineError,
    MdbPasswordError,
    mdb_import_error,
    open_database,
)
from .mdb_import import MdbImportResult, detect_database_scale
from .mdb_import_dialog import MdbImportDialog, MdbPasswordDialog
from .mdb_import_report import MdbImportReportWindow
from .mdb_mapping import (
    ENTITY_ORDER,
    MdbMappingError,
    load_table_mapping,
    resolve_mapping,
)
from .mapa_tiles import (
    PROVEDORES,
    PROVEDOR_ESRI,
    PROVEDOR_GOOGLE_HIBRIDO,
    PROVEDOR_GOOGLE_SAT,
    GerenciadorTiles,
    Provedor,
)
from .model import (
    CableModel,
    CircuitCatalogModel,
    CircuitModel,
    CircuitVisibilityController,
    FeatureSelection,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
    SwitchModel,
    UtmCrs,
)
from .opendss_export import (
    ARRANGEMENTS_FILENAME,
    CABOS_FILENAME,
    LINE_GEOMETRIES_FILENAME,
    LINES_FILENAME,
    REGULATORS_FILENAME,
    SINGLE_PHASE_GENERATORS_FILENAME,
    SINGLE_PHASE_LOADS_FILENAME,
    SWITCHES_FILENAME,
    THREE_PHASE_LOADS_FILENAME,
    THREE_PHASE_GENERATORS_FILENAME,
    TWO_PHASE_GENERATORS_FILENAME,
    TWO_PHASE_LOADS_FILENAME,
    OpenDssExportBundle,
    master_filenames,
    phase_letters_by_node,
)
from .opendss_export_dialog import OpenDssExportDialog
from .opendss_allocation_dialog import (
    OpenDssAllocationDialog,
    load_opendss_allocation_settings,
    save_opendss_allocation_settings,
)
from .opendss_allocation_export import (
    OpenDssAllocationExportBundle,
    allocation_export_directory_names,
    write_allocation_export,
)
from .opendss_cables_window import OpenDssCablesWindow
from .opendss_automatic_assembly_session import OpenDssAutomaticAssemblySession
from .opendss_geometries_window import OpenDssGeometriesWindow
from .opendss_library_help import OpenDssLibraryHelpDialog
from .opendss_library_session import OpenDssLibrarySession
from .opendss_line_mode import OpenDssLineParameterMode
from .opendss_mapping_session import OpenDssMappingSession
from .opendss_simplified_export import (
    SINGLE_PHASE_BRANCHES_FILENAME,
    TWO_PHASE_BRANCHES_FILENAME,
    SimplifiedOpenDssExportBundle,
    simplified_export_directory_name,
)
from .opendss_engine import power_flow_import_error
from .opendss_powerflow import (
    LINE_VOLTAGE_PU_BASE,
    PowerFlowResult,
    SegmentPowers,
    apparent_power,
    line_voltages,
    power_factor,
    three_phase_power,
    voltage_unbalance,
)
from .opendss_settings import OpenDssLoadSettings
from .opendss_settings_dialog import (
    OpenDssSettingsDialog,
    load_opendss_line_parameter_mode,
    load_opendss_settings,
    save_opendss_line_parameter_mode,
    save_opendss_settings,
)
from .overlap_report import CircuitOverlapReportWindow, OverlapReportTableModel
from .patamares_window import PatamaresWindow
from .power_flow_table import PowerFlowTableModel
from .phase_config import (
    PHASE_COLORS,
    PhaseClassification,
    PhaseConfiguration,
    PhaseConfigurationError,
    default_phase_configuration_path,
    load_phase_configuration,
)
from .phase_legend import PhaseLegend
from .regulator_import import RegulatorLoadResult
from .search import GlobalSearchIndex, SearchResult
from .search_palette import SearchPalette
from .segment_import import SegmentLoadResult
from .switch_import import SwitchLoadResult
from .table_columns import EXCEL_LIKE_TABLE_STYLE, enable_interactive_columns
from .theme import (
    THEME_LABELS,
    AppTheme,
    apply_theme,
    load_theme_preference,
    save_theme_preference,
)
from .workers import (
    AllocationMeasurementImportWorker,
    BranchAnalysisWorker,
    BranchCsvExportWorker,
    BranchJsonExportWorker,
    CableImportWorker,
    CircuitImportWorker,
    CircuitLevelImportWorker,
    CsvImportWorker,
    GeneratorImportWorker,
    GeneratorUpdateWorker,
    LoadImportWorker,
    LoadPatternImportWorker,
    MdbImportWorker,
    OpenDssExportWorker,
    OpenDssAllocationExportWorker,
    SimplifiedOpenDssExportWorker,
    PowerFlowWorker,
    RegulatorImportWorker,
    SegmentImportWorker,
    SwitchImportWorker,
    EquivalentNetworkWorker,
)


# Arquivo e rótulo de cada contagem de fases, para a confirmação de
# substituição e para o relatório final da exportação.
_LOAD_EXPORT_FILES: tuple[tuple[int, str, str], ...] = (
    (1, SINGLE_PHASE_LOADS_FILENAME, "monofásicas"),
    (2, TWO_PHASE_LOADS_FILENAME, "bifásicas"),
    (3, THREE_PHASE_LOADS_FILENAME, "trifásicas"),
)
_GENERATOR_EXPORT_FILES: tuple[tuple[int, str, str], ...] = (
    (1, SINGLE_PHASE_GENERATORS_FILENAME, "monofásicos"),
    (2, TWO_PHASE_GENERATORS_FILENAME, "bifásicos"),
    (3, THREE_PHASE_GENERATORS_FILENAME, "trifásicos"),
)


def _update_progress_dialog(
    dialog: QProgressDialog | None,
    *,
    label: str | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    value: int | None = None,
) -> None:
    """Atualiza um progresso modal sem acessar o objeto depois de ``setValue``.

    ``QProgressDialog.setValue`` pode processar eventos quando o diálogo é
    modal. Isso permite que o término da thread limpe as referências da janela
    durante a própria chamada. Por isso o valor é sempre a última operação.
    """

    if dialog is None:
        return
    if minimum is not None and maximum is not None:
        dialog.setRange(minimum, maximum)
    elif minimum is not None:
        dialog.setMinimum(minimum)
    elif maximum is not None:
        dialog.setMaximum(maximum)
    if label is not None:
        dialog.setLabelText(label)
    if value is not None:
        dialog.setValue(value)


def _close_progress_dialog(dialog: QProgressDialog | None) -> None:
    """Fecha uma referência local estável, sem reler o estado da janela."""

    if dialog is not None:
        dialog.close()


# Grandezas do fluxo de potência oferecidas em cada página de detalhes, como
# (chave, rótulo, casas decimais, tem fasor). Uma tabela por página em vez de
# várias: o painel já é denso, e o combobox troca a leitura sem empilhar mais
# uma tabela de quatro linhas.
#
# "tem fasor" acrescenta as colunas de ângulo ao lado dos módulos. O pu fica de
# fora de propósito: o ângulo dele é o mesmo da tensão de fase, só o módulo muda
# de escala, então repeti-lo seria ruído. O carregamento é uma razão de módulos
# e não tem fase alguma.
#
# Todas as grandezas usam 4 casas decimais fixas — inclusive ângulo,
# carregamento e desequilíbrio —, para que a tabela inteira tenha a mesma
# precisão visual em vez de variar coluna a coluna.
_SEGMENT_QUANTITIES: tuple[tuple[str, str, int, bool], ...] = (
    ("current", "Corrente por fase (A)", 4, True),
    ("loading", "Carregamento (%)", 4, False),
    ("active_power", "Potência ativa (kW)", 4, False),
    ("reactive_power", "Potência reativa (kvar)", 4, False),
    ("apparent_power", "Potência aparente (kVA)", 4, True),
    ("three_phase_power", "Potência trifásica", 4, False),
    ("power_factor", "Fator de potência", 4, False),
    ("losses", "Perdas", 4, False),
)
# "voltage" precisa continuar em primeiro: é o padrão do combobox e o recuo de
# _quantity_of quando a chave ainda não foi escolhida.
_BAR_QUANTITIES: tuple[tuple[str, str, int, bool], ...] = (
    ("voltage", "Tensão de fase (V)", 4, True),
    ("line_voltage", "Tensão de linha (V)", 4, True),
    ("per_unit", "Tensão de fase (pu)", 4, False),
    ("line_per_unit", "Tensão de linha (pu)", 4, False),
    ("unbalance", "Desequilíbrio de tensão (%)", 4, False),
)
# Casas decimais das colunas de ângulo — mesma precisão fixa das demais
# colunas de fluxo de potência, para a tabela não variar de precisão por
# coluna.
_ANGLE_DECIMALS = 4
# Grandezas que saem de SegmentPowers, e não de SegmentCurrents.
_POWER_QUANTITY_KEYS = frozenset(
    {
        "active_power",
        "reactive_power",
        "apparent_power",
        "three_phase_power",
        "power_factor",
        "losses",
    }
)


class UtmImportDialog(QDialog):
    """Coleta os metadados UTM ausentes do CSV e a unidade das coordenadas."""

    def __init__(
        self,
        file_name: str,
        parent=None,  # noqa: ANN001
        *,
        suggested_scale: float = 1.0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sistema de coordenadas UTM")
        self.setModal(True)

        form = QFormLayout(self)
        file_label = QLabel(file_name)
        file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Arquivo:", file_label)

        self.zone_input = QSpinBox()
        self.zone_input.setRange(1, 60)
        self.zone_input.setValue(21)
        self.zone_input.setToolTip("Zona longitudinal do sistema UTM")
        form.addRow("Zona UTM:", self.zone_input)

        self.hemisphere_input = QComboBox()
        self.hemisphere_input.addItem("Sul", False)
        self.hemisphere_input.addItem("Norte", True)
        form.addRow("Hemisfério:", self.hemisphere_input)

        self.unit_input = QComboBox()
        for factor, label in COORDINATE_UNITS:
            self.unit_input.addItem(label, factor)
        self.unit_input.setToolTip(
            "Divisor que converte X e Y do arquivo para metros. O modelo "
            "trabalha em metros, como o COMPR dos trechos."
        )
        form.addRow("Unidade das coordenadas:", self.unit_input)

        # A dedução só devolve fatores de COORDINATE_UNITS, então a entrada
        # correspondente sempre existe no combo.
        position = self.unit_input.findData(suggested_scale)
        if position >= 0:
            self.unit_input.setCurrentIndex(position)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def coordinate_scale(self) -> float:
        return float(self.unit_input.currentData())

    def crs(self) -> UtmCrs:
        return UtmCrs(
            zone=self.zone_input.value(),
            northern=bool(self.hemisphere_input.currentData()),
        )


class ImportChoiceDialog(QDialog):
    """Escolhe o tipo de dado sem multiplicar ações na interface principal."""

    def __init__(
        self,
        has_bars: bool,
        has_segments: bool,
        parent=None,  # noqa: ANN001
        *,
        has_loads: bool = False,
        has_circuits: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Importar dados")
        self.setModal(True)
        self.selected_kind: str | None = None

        layout = QVBoxLayout(self)
        instruction = QLabel("O que você deseja importar?")
        layout.addWidget(instruction)

        self.bars_button = QPushButton("Importar barras…")
        self.bars_button.setToolTip("Carregar barras e suas coordenadas UTM")
        self.bars_button.clicked.connect(lambda: self._select("bars"))
        layout.addWidget(self.bars_button)

        self.segments_button = QPushButton("Importar trechos…")
        self.segments_button.setToolTip(
            "Carregar trechos vinculados às barras já importadas"
        )
        self.segments_button.setEnabled(has_bars)
        self.segments_button.clicked.connect(lambda: self._select("segments"))
        layout.addWidget(self.segments_button)

        self.loads_button = QPushButton("Importar cargas…")
        self.loads_button.setToolTip(
            "Carregar cargas vinculadas às barras já importadas"
        )
        self.loads_button.setEnabled(has_bars)
        self.loads_button.clicked.connect(lambda: self._select("loads"))
        layout.addWidget(self.loads_button)

        self.load_patterns_button = QPushButton("Importar patamares de carga…")
        self.load_patterns_button.setToolTip(
            "Carregar os quatro patamares NPAT das cargas importadas"
        )
        self.load_patterns_button.setEnabled(has_loads)
        self.load_patterns_button.clicked.connect(
            lambda: self._select("load_patterns")
        )
        layout.addWidget(self.load_patterns_button)

        self.generators_button = QPushButton("Importar geradores…")
        self.generators_button.setToolTip(
            "Associar MT_GERADOR_CONS e MT_CONS às cargas importadas"
        )
        self.generators_button.setEnabled(has_loads)
        self.generators_button.clicked.connect(lambda: self._select("generators"))
        layout.addWidget(self.generators_button)

        self.switches_button = QPushButton("Importar chaves…")
        self.switches_button.setToolTip(
            "Carregar atributos de chaves vinculados aos trechos importados"
        )
        self.switches_button.setEnabled(has_bars and has_segments)
        self.switches_button.clicked.connect(lambda: self._select("switches"))
        layout.addWidget(self.switches_button)

        self.regulators_button = QPushButton("Importar reguladores…")
        self.regulators_button.setToolTip(
            "Carregar reguladores de tensão vinculados aos trechos importados"
        )
        self.regulators_button.setEnabled(has_bars and has_segments)
        self.regulators_button.clicked.connect(lambda: self._select("regulators"))
        layout.addWidget(self.regulators_button)

        self.circuits_button = QPushButton("Importar circuitos…")
        self.circuits_button.setToolTip(
            "Carregar circuitos e descobrir seus elementos na rede"
        )
        self.circuits_button.setEnabled(has_bars and has_segments)
        self.circuits_button.clicked.connect(lambda: self._select("circuits"))
        layout.addWidget(self.circuits_button)

        self.circuit_levels_button = QPushButton(
            "Importar patamares de circuitos…"
        )
        self.circuit_levels_button.setToolTip(
            "Carregar CIRCUITO_PATAMARES para os circuitos importados"
        )
        self.circuit_levels_button.setEnabled(has_circuits)
        self.circuit_levels_button.clicked.connect(
            lambda: self._select("circuit_levels")
        )
        layout.addWidget(self.circuit_levels_button)

        self.allocation_measurements_button = QPushButton(
            "Importar correntes para alocação OpenDSS…"
        )
        self.allocation_measurements_button.setToolTip(
            "CSV separado por ponto e vírgula. Cabeçalho obrigatório: "
            "CODIGO;NPAT;ID;IE;IF (ex.: 004011;0;120.5;98.2;101.7)"
        )
        self.allocation_measurements_button.setEnabled(has_circuits)
        self.allocation_measurements_button.clicked.connect(
            lambda: self._select("allocation_measurements")
        )
        layout.addWidget(self.allocation_measurements_button)

        # O catálogo de cabos é uma raiz independente: não depende de barras nem
        # de trechos, então o botão nunca fica desabilitado.
        self.cables_button = QPushButton("Importar cabos…")
        self.cables_button.setToolTip(
            "Carregar o catálogo de cabos consultado em Tabelas > Cabos…"
        )
        self.cables_button.clicked.connect(lambda: self._select("cables"))
        layout.addWidget(self.cables_button)

        if not has_bars:
            dependency = QLabel(
                "Importe as barras antes de importar trechos ou cargas."
            )
            dependency.setWordWrap(True)
            layout.addWidget(dependency)
        elif not has_segments:
            dependency = QLabel(
                "Importe os trechos antes de importar as chaves ou os circuitos."
            )
            dependency.setWordWrap(True)
            layout.addWidget(dependency)
        if has_bars and not has_loads:
            dependency = QLabel(
                "Importe as cargas antes de importar seus patamares."
            )
            dependency.setWordWrap(True)
            layout.addWidget(dependency)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select(self, kind: str) -> None:
        self.selected_kind = kind
        self.accept()


class GeneratorCsvImportDialog(QDialog):
    """Escolhe explicitamente as duas fontes CSV antes de iniciar o worker."""

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Importar geradores")
        self.setModal(True)
        self._generator_path = ""
        self._consumer_path = ""

        layout = QVBoxLayout(self)
        message = QLabel(
            "Selecione os dois arquivos necessários para importar os geradores."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        grid = QGridLayout()
        self.generator_file_button = QPushButton("Importar MT_GERADOR_CONS…")
        self.generator_file_button.clicked.connect(self._choose_generator_file)
        self.generator_file_label = QLabel("Nenhum arquivo selecionado")
        self.generator_file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        grid.addWidget(self.generator_file_button, 0, 0)
        grid.addWidget(self.generator_file_label, 0, 1)

        self.consumer_file_button = QPushButton("Importar MT_CONS…")
        self.consumer_file_button.clicked.connect(self._choose_consumer_file)
        self.consumer_file_label = QLabel("Nenhum arquivo selecionado")
        self.consumer_file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        grid.addWidget(self.consumer_file_button, 1, 0)
        grid.addWidget(self.consumer_file_label, 1, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.import_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText("Importar")
        self.import_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _initial_directory(self, other_path: str) -> str:
        return "" if not other_path else str(Path(other_path).parent)

    def _choose_generator_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar MT_GERADOR_CONS.csv",
            self._initial_directory(self._consumer_path),
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self.set_generator_path(path)

    def _choose_consumer_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar MT_CONS.csv",
            self._initial_directory(self._generator_path),
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self.set_consumer_path(path)

    def set_generator_path(self, path: str) -> None:
        self._generator_path = str(path)
        self.generator_file_label.setText(Path(path).name)
        self.generator_file_label.setToolTip(str(path))
        self._sync_import_enabled()

    def set_consumer_path(self, path: str) -> None:
        self._consumer_path = str(path)
        self.consumer_file_label.setText(Path(path).name)
        self.consumer_file_label.setToolTip(str(path))
        self._sync_import_enabled()

    def _sync_import_enabled(self) -> None:
        self.import_button.setEnabled(
            bool(self._generator_path and self._consumer_path)
        )

    def generator_path(self) -> str:
        return self._generator_path

    def consumer_path(self) -> str:
        return self._consumer_path


class MainWindow(QMainWindow):
    def __init__(
        self,
        phase_configuration_path: str | Path | None = None,
        settings: QSettings | None = None,
        curves_path: str | Path | None = None,
        patamares_path: str | Path | None = None,
        library_cables_path: str | Path | None = None,
        library_geometries_path: str | Path | None = None,
        cable_map_path: str | Path | None = None,
        arrangement_map_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Visualizador de Circuitos Elétricos")
        self.resize(1280, 800)

        # O tema já foi aplicado pelo ponto de entrada; aqui a preferência serve
        # apenas para marcar a ação correta do menu.
        self._settings = QSettings() if settings is None else settings
        self._theme = load_theme_preference(self._settings)
        # Preferência de sessão para sessão, como o tema: os limites de tensão
        # das cargas valem para a exportação e para o fluxo de potência.
        self._opendss_load_settings = load_opendss_settings(self._settings)
        self._opendss_allocation_settings = load_opendss_allocation_settings(
            self._settings
        )
        self._opendss_line_parameter_mode = load_opendss_line_parameter_mode(
            self._settings
        )

        self._model: CircuitModel | None = None
        self._line_model: LineNetworkModel | None = None
        self._line_item: LineNetworkItem | None = None
        self._load_model: LoadModel | None = None
        self._allocation_model: TransformerAllocationModel | None = None
        self._load_pattern_model: LoadPatternModel | None = None
        self._generator_model: GeneratorModel | None = None
        self._generator_update_result: GeneratorUpdateResult | None = None
        self._switch_model: SwitchModel | None = None
        self._switch_item: SwitchNetworkItem | None = None
        self._regulator_model: RegulatorModel | None = None
        self._regulator_item: RegulatorNetworkItem | None = None
        self._cable_model: CableModel | None = None
        self._circuit_catalog: CircuitCatalogModel | None = None
        self._allocation_measurements: AllocationMeasurementModel | None = None
        self._circuit_level_model: CircuitCalculationLevelsModel | None = None
        self._circuit_level_controller: (
            CircuitCalculationLevelsController | None
        ) = None
        self._circuit_visibility: CircuitVisibilityController | None = None
        self._branch_analysis_result: BranchAnalysisResult | None = None
        self._equivalent_network_result: EquivalentNetworkResult | None = None
        self._selected_branch: BranchRecord | None = None
        self._selected_feature: FeatureSelection | None = None
        self._effective_bar_mask = None
        self._effective_segment_mask = None
        self._effective_load_mask = None
        self._search_focus_active = False
        self._active_bar_count = 0
        self._active_load_count = 0
        self._active_equivalent_load_count = 0
        self._active_generator_count = 0
        self._satellite_provider = PROVEDOR_ESRI
        self._google_satellite_authorized = False
        self.phase_configuration_path = (
            default_phase_configuration_path()
            if phase_configuration_path is None
            else Path(phase_configuration_path)
        )
        self._phase_configuration: PhaseConfiguration | None = None
        self._phase_configuration_error: str | None = None
        self._phase_classification: PhaseClassification | None = None
        try:
            self._phase_configuration = load_phase_configuration(
                self.phase_configuration_path
            )
        except PhaseConfigurationError as exc:
            self._phase_configuration_error = str(exc)
        # Como o fases2.json, um mapeamento inválido desabilita apenas o recurso
        # que depende dele — aqui, a importação por banco.
        self._mdb_table_mapping = None
        self._mdb_mapping_error: str | None = None
        self._mdb_report_window: MdbImportReportWindow | None = None
        try:
            self._mdb_table_mapping = load_table_mapping()
        except MdbMappingError as exc:
            self._mdb_mapping_error = str(exc)
        self._import_thread: QThread | None = None
        self._import_worker: (
            CsvImportWorker
            | SegmentImportWorker
            | LoadImportWorker
            | GeneratorImportWorker
            | LoadPatternImportWorker
            | SwitchImportWorker
            | RegulatorImportWorker
            | CircuitImportWorker
            | CircuitLevelImportWorker
            | MdbImportWorker
            | AllocationMeasurementImportWorker
            | None
        ) = None
        self._progress_dialog: QProgressDialog | None = None
        self._progress_entity = "registros"
        self._close_after_import = False
        self._branch_thread: QThread | None = None
        self._branch_worker: BranchAnalysisWorker | None = None
        self._branch_progress_dialog: QProgressDialog | None = None
        self._branch_analysis_snapshot: tuple[object, ...] | None = None
        self._close_after_branch_analysis = False
        self._show_branches_after_analysis = True
        self._pending_branch_metrics = False
        self._branch_json_thread: QThread | None = None
        self._branch_json_worker: BranchJsonExportWorker | None = None
        self._branch_json_progress_dialog: QProgressDialog | None = None
        self._close_after_branch_json_export = False
        self._branch_csv_thread: QThread | None = None
        self._branch_csv_worker: BranchCsvExportWorker | None = None
        self._branch_csv_progress_dialog: QProgressDialog | None = None
        self._close_after_branch_csv_export = False
        self._pending_simplified_activation = False
        self._pending_simplified_export = False
        self._restart_equivalent_after_finish = False
        self._equivalent_thread: QThread | None = None
        self._equivalent_worker: EquivalentNetworkWorker | None = None
        self._equivalent_progress_dialog: QProgressDialog | None = None
        self._equivalent_snapshot: tuple[object, ...] | None = None
        self._close_after_equivalent_build = False
        self._export_thread: QThread | None = None
        self._export_worker: (
            OpenDssExportWorker
            | OpenDssAllocationExportWorker
            | SimplifiedOpenDssExportWorker
            | None
        ) = None
        self._export_progress_dialog: QProgressDialog | None = None
        self._export_directory: Path | None = None
        self._close_after_export = False
        self._power_flow_thread: QThread | None = None
        self._power_flow_worker: PowerFlowWorker | None = None
        self._power_flow_progress_dialog: QProgressDialog | None = None
        self._power_flow_snapshot: tuple[object, ...] | None = None
        self._power_flow_result: PowerFlowResult | None = None
        self._close_after_power_flow = False
        self._generator_update_thread: QThread | None = None
        self._generator_update_worker: GeneratorUpdateWorker | None = None
        self._generator_update_progress_dialog: QProgressDialog | None = None
        self._close_after_generator_update = False
        # Grandezas do elemento selecionado, guardadas para o combobox poder
        # trocar a leitura sem reconsultar o resultado.
        self._segment_power_flow_currents = None
        self._segment_power_flow_powers = None
        self._bar_power_flow_voltages = None

        self.scene = QGraphicsScene(self)
        self.scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.view = DiagramView(self.scene, self)
        self.setCentralWidget(self.view)
        self.virtualizer = ItemVirtualizer(self.scene, self.view, parent=self)
        self.load_lod_coordinator = LoadLodCoordinator(self.view, parent=self)
        self.load_virtualizer = LoadVirtualizer(
            self.scene,
            self.view,
            lod_coordinator=self.load_lod_coordinator,
            parent=self,
        )
        self.view.set_load_layer(self.load_virtualizer)
        self.generator_virtualizer = LoadVirtualizer(
            self.scene,
            self.view,
            symbol_kind="generator",
            lod_coordinator=self.load_lod_coordinator,
            parent=self,
        )
        self.view.set_generator_layer(self.generator_virtualizer)
        self.equivalent_load_virtualizer = LoadVirtualizer(
            self.scene,
            self.view,
            lod_coordinator=self.load_lod_coordinator,
            parent=self,
        )
        self.equivalent_load_virtualizer.set_loads_visible(False)
        self.view.set_equivalent_load_layer(self.equivalent_load_virtualizer)
        self.segment_selection_overlay = SegmentSelectionOverlayItem()
        self.scene.addItem(self.segment_selection_overlay)
        self.branch_highlight_overlay = BranchHighlightOverlayItem()
        self.scene.addItem(self.branch_highlight_overlay)
        self.search_index = GlobalSearchIndex()
        self.search_palette = SearchPalette(
            self.search_index,
            self._is_search_result_hidden,
            self,
        )
        self.phase_legend = PhaseLegend(self.view.viewport())
        self._overlay_position_timer = QTimer(self)
        self._overlay_position_timer.setSingleShot(True)
        self._overlay_position_timer.setInterval(0)
        self._overlay_position_timer.timeout.connect(
            self._position_viewport_overlays
        )

        self.circuit_table_model = CircuitTableModel(self)
        self.circuits_window = CircuitsWindow(self.circuit_table_model, self)
        self.overlap_table_model = OverlapReportTableModel(self)
        self.overlap_report_window = CircuitOverlapReportWindow(
            self.overlap_table_model,
            self,
        )
        self.branch_table_model = BranchTableModel(self)
        self.branches_window = BranchesWindow(self.branch_table_model, self)
        self.cable_table_model = CableTableModel(self)
        self.cables_window = CablesWindow(self.cable_table_model, self)
        # WireData/CNData e LineSpacing são bibliotecas globais. As montagens
        # transitórias serão combinadas com os trechos assim que eles existirem.
        self.opendss_mapping_session = OpenDssMappingSession(
            cable_map_path=cable_map_path,
            arrangement_map_path=arrangement_map_path,
            parent=self,
        )
        self.opendss_library_session = OpenDssLibrarySession(
            cables_path=library_cables_path,
            geometries_path=library_geometries_path,
            mapping_session=self.opendss_mapping_session,
            parent=self,
        )
        self.opendss_automatic_assembly_session = OpenDssAutomaticAssemblySession(
            self.opendss_library_session,
            self.opendss_mapping_session,
            self._phase_configuration,
            self,
        )
        self.opendss_library_help = OpenDssLibraryHelpDialog(self)
        self.opendss_cables_window = OpenDssCablesWindow(
            self.opendss_library_session,
            self.opendss_library_help,
            self,
            assembly_session=self.opendss_automatic_assembly_session,
        )
        self.opendss_geometries_window = OpenDssGeometriesWindow(
            self.opendss_library_session,
            self.opendss_library_help,
            self,
            assembly_session=self.opendss_automatic_assembly_session,
        )
        self.opendss_library_session.cablesSaved.connect(
            lambda count: self.statusBar().showMessage(
                f"Biblioteca OpenDSS: {count:n} cabo(s) salvos.", 6_000
            )
        )
        self.opendss_library_session.cablesSaved.connect(
            self._on_opendss_library_inputs_saved
        )
        self.opendss_library_session.geometriesSaved.connect(
            lambda arrangements, _legacy: self.statusBar().showMessage(
                "Biblioteca OpenDSS: "
                f"{arrangements:n} arranjo(s) salvo(s); montagens automáticas atualizadas.",
                6_000,
            )
        )
        self.opendss_library_session.geometriesSaved.connect(
            self._on_opendss_library_inputs_saved
        )
        self.opendss_mapping_session.mapsSaved.connect(
            self._on_opendss_library_inputs_saved
        )
        # As curvas são cadastro do usuário, não dado importado: carregam junto
        # com a janela e sobrevivem a qualquer importação.
        self._curves_path = curves_path
        self._curves_load = load_curves(curves_path)
        self._saved_curves: tuple[Curve, ...] = self._curves_load.curves
        self.curve_catalog = CurveCatalog.from_curves(self._curves_load.curves)
        self.curves_window = CurvesWindow(
            self.curve_catalog,
            storage_path=curves_path,
            parent=self,
        )
        self.curves_window.curvesSaved.connect(self._on_curves_saved)
        # A grade de horários é configuração global e independente dos
        # patamares de potência importados para cada carga.
        self._patamares_path = patamares_path
        self._calculation_levels_load = load_calculation_levels(patamares_path)
        self.calculation_level_schedule = self._calculation_levels_load.schedule
        self.patamares_window = PatamaresWindow(
            self.calculation_level_schedule,
            storage_path=patamares_path,
            parent=self,
        )
        self.patamares_window.scheduleSaved.connect(
            self._on_calculation_levels_saved
        )
        self.patamares_window.scheduleReloaded.connect(
            self._on_calculation_levels_reloaded
        )
        self.patamares_window.circuitScheduleSaved.connect(
            self._on_circuit_calculation_levels_saved
        )
        self._circuit_visibility_timer = QTimer(self)
        self._circuit_visibility_timer.setSingleShot(True)
        self._circuit_visibility_timer.setInterval(50)
        self._circuit_visibility_timer.timeout.connect(
            self._apply_circuit_visibility
        )

        self._create_actions()
        self._create_menus_and_toolbar()
        self._create_details_dock()
        self._create_status_bar()
        self._connect_signals()
        self._set_selection(None)
        self._update_status_counts(0)
        self._sync_export_availability()
        self._sync_power_flow_availability()
        if self._phase_configuration_error is not None:
            QTimer.singleShot(0, self._show_phase_configuration_error)
        if self._curves_load.issue is not None:
            QTimer.singleShot(0, self._show_curves_load_warning)
        if self._calculation_levels_load.issue is not None:
            QTimer.singleShot(0, self._show_calculation_levels_load_warning)

    def _is_current_signal_source(self, expected: object | None) -> bool:
        """Aceita chamadas diretas e sinais apenas da operação ainda vigente."""

        source = self.sender()
        return source is None or source is expected

    def _create_actions(self) -> None:
        self.import_action = QAction("Importar CSV…", self)
        self.import_action.setShortcut(QKeySequence.StandardKey.Open)
        self.import_action.setToolTip(
            "Importar barras, trechos, cargas, geradores, chaves ou circuitos de arquivos CSV"
        )
        self.import_action.triggered.connect(self._choose_import)

        self.mdb_import_action = QAction("Importar banco de dados…", self)
        self.mdb_import_action.setToolTip(
            "Importar barras, trechos, cargas, patamares, chaves, reguladores, "
            "circuitos e cabos de um banco Access (.mdb/.accdb), em modo "
            "somente leitura"
        )
        # Sem pyodbc ou sem driver ODBC a ação nunca habilita, e o motivo vira a
        # dica — o mesmo padrão de power_flow_action com o py-dss-interface.
        self._mdb_error = mdb_import_error()
        if self._mdb_error is None and self._mdb_mapping_error is not None:
            self._mdb_error = self._mdb_mapping_error
        if self._mdb_error is not None:
            self.mdb_import_action.setEnabled(False)
            self.mdb_import_action.setToolTip(self._mdb_error)
        self.mdb_import_action.triggered.connect(self._choose_mdb_import)

        self.fit_action = QAction("Enquadrar tudo", self)
        self.fit_action.setShortcut(QKeySequence("F"))
        self.fit_action.setEnabled(False)
        self.fit_action.triggered.connect(self._fit_all)

        self.search_action = QAction("Buscar", self)
        self.search_action.setShortcut(QKeySequence.StandardKey.Find)
        self.search_action.setToolTip(
            "Buscar barras, trechos, chaves, cargas e circuitos por código"
        )
        self.search_action.setEnabled(False)
        self.search_action.triggered.connect(self._show_search_palette)

        self.show_bars_action = QAction("Mostrar barras", self)
        self.show_bars_action.setCheckable(True)
        self.show_bars_action.setChecked(True)
        self.show_bars_action.setEnabled(False)
        self.show_bars_action.toggled.connect(self._set_bars_visible)

        self.show_loads_action = QAction("Mostrar cargas", self)
        self.show_loads_action.setCheckable(True)
        self.show_loads_action.setChecked(True)
        self.show_loads_action.setEnabled(False)
        self.show_loads_action.toggled.connect(self._set_loads_visible)

        self.show_generators_action = QAction("Mostrar geradores", self)
        self.show_generators_action.setCheckable(True)
        self.show_generators_action.setChecked(True)
        self.show_generators_action.setEnabled(False)
        self.show_generators_action.toggled.connect(self._set_generators_visible)

        self.phase_coloring_action = QAction("Colorir trechos por fases", self)
        self.phase_coloring_action.setCheckable(True)
        self.phase_coloring_action.setEnabled(False)
        if self._phase_configuration_error is not None:
            self.phase_coloring_action.setToolTip(self._phase_configuration_error)
        else:
            self.phase_coloring_action.setToolTip(
                "Substituir as cores dos circuitos pelas cores do número de fases"
            )
        self.phase_coloring_action.toggled.connect(
            self._set_phase_coloring_enabled
        )

        self.satellite_action = QAction("Exibir imagem de satélite", self)
        self.satellite_action.setCheckable(True)
        self.satellite_action.setChecked(False)
        self.satellite_action.setToolTip(
            "Exibir tiles de satélite georreferenciados sob a rede"
        )
        self.satellite_action.toggled.connect(self._set_satellite_enabled)

        self.satellite_provider_group = QActionGroup(self)
        self.satellite_provider_group.setExclusive(True)
        self.satellite_provider_actions: dict[Provedor, QAction] = {}
        for provider in PROVEDORES:
            action = QAction(provider.nome, self)
            action.setCheckable(True)
            action.setChecked(provider is self._satellite_provider)
            action.triggered.connect(
                lambda _checked=False, selected=provider: (
                    self._select_satellite_provider(selected)
                )
            )
            self.satellite_provider_group.addAction(action)
            self.satellite_provider_actions[provider] = action

        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions: dict[AppTheme, QAction] = {}
        for theme, label in THEME_LABELS.items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(theme is self._theme)
            action.setToolTip(
                "Definir manualmente a aparência da interface, "
                "independentemente do tema do sistema operacional"
            )
            action.triggered.connect(
                lambda _checked=False, selected=theme: self._set_theme(selected)
            )
            self.theme_group.addAction(action)
            self.theme_actions[theme] = action

        self.simplified_network_action = QAction(
            "Rede simplificada por ramais",
            self,
        )
        self.simplified_network_action.setCheckable(True)
        self.simplified_network_action.setEnabled(False)
        self.simplified_network_action.setToolTip(
            "Representar cada ramal como uma carga equivalente derivada"
        )
        if self._phase_configuration_error is not None:
            self.simplified_network_action.setToolTip(
                self._phase_configuration_error
            )
        self.simplified_network_action.toggled.connect(
            self._set_simplified_network_enabled
        )

        self.circuits_action = QAction("Circuitos…", self)
        self.circuits_action.setEnabled(False)
        self.circuits_action.triggered.connect(self._show_circuits_window)

        self.overlaps_action = QAction("Sobreposições…", self)
        self.overlaps_action.setEnabled(False)
        self.overlaps_action.triggered.connect(self._show_overlap_report)

        self.cables_action = QAction("Cabos importados…", self)
        self.cables_action.setToolTip(
            "Consultar o catálogo de cabos importado"
        )
        self.cables_action.triggered.connect(self._show_cables_window)

        self.opendss_cables_action = QAction("Cabos…", self)
        self.opendss_cables_action.setToolTip(
            "Cadastrar condutores WireData e CNData do OpenDSS"
        )
        self.opendss_cables_action.triggered.connect(
            self._show_opendss_cables_window
        )

        self.opendss_geometries_action = QAction("Geometrias…", self)
        self.opendss_geometries_action.setToolTip(
            "Cadastrar arranjos LineSpacing e montagens LineGeometry"
        )
        self.opendss_geometries_action.triggered.connect(
            self._show_opendss_geometries_window
        )

        self.opendss_export_action = QAction("OpenDSS…", self)
        self.opendss_export_action.setEnabled(False)
        self.opendss_export_action.setToolTip(
            "Exportar trechos, chaves e cargas mono, bi e trifásicas como "
            "elementos do OpenDSS"
        )
        self.opendss_export_action.triggered.connect(self._export_opendss)

        self.simplified_opendss_export_action = QAction(
            "OpenDSS — Rede simplificada por ramais…",
            self,
        )
        self.simplified_opendss_export_action.setEnabled(False)
        self.simplified_opendss_export_action.setToolTip(
            "Exportar uma projeção independente em que cada ramal é substituído "
            "por sua carga equivalente"
        )
        self.simplified_opendss_export_action.triggered.connect(
            self._export_simplified_opendss
        )

        self.opendss_allocation_export_action = QAction(
            "OpenDSS — Alocação por energia…",
            self,
        )
        self.opendss_allocation_export_action.setEnabled(False)
        self.opendss_allocation_export_action.setToolTip(
            "Gerar quatro circuitos snapshot e alocar somente as cargas "
            "definidas pela energia agregada"
        )
        self.opendss_allocation_export_action.triggered.connect(
            self._export_opendss_allocation
        )

        self.opendss_settings_action = QAction("OpenDSS…", self)
        self.opendss_settings_action.setToolTip(
            "Definir parâmetros globais aplicados a todas as cargas do modelo "
            "exportado e do fluxo de potência"
        )
        self.opendss_settings_action.triggered.connect(self._show_opendss_settings)

        # Sem setEnabled(False): é cadastro do usuário, nunca depende de dado
        # importado — a mesma razão de opendss_settings_action.
        self.curves_action = QAction("Curvas…", self)
        self.curves_action.setToolTip(
            "Criar e editar curvas horárias de 24 pontos para associar a "
            "cargas e geradores"
        )
        self.curves_action.triggered.connect(self._show_curves_window)

        self.patamares_action = QAction("Patamares…", self)
        self.patamares_action.setToolTip(
            "Definir os períodos horários dos quatro patamares de cálculo"
        )
        self.patamares_action.triggered.connect(self._show_patamares_window)

        self.update_generators_action = QAction("Atualizar Geradores…", self)
        self.update_generators_action.setEnabled(False)
        self.update_generators_action.setToolTip(
            "Calcular as demandas dos geradores usando uma curva e os "
            "patamares efetivos dos circuitos"
        )
        self.update_generators_action.triggered.connect(self._update_generators)

        self.power_flow_action = QAction("Executar Fluxo de Potência", self)
        self.power_flow_action.setEnabled(False)
        self.power_flow_action.setToolTip(
            "Converter os circuitos visíveis para o OpenDSS, resolver o fluxo "
            "de potência e trazer correntes e tensões para o painel"
        )
        # Sem a biblioteca opcional o botão nunca habilita; o motivo vira a
        # dica, no mesmo padrão de branches_action com a configuração de fases.
        engine_error = power_flow_import_error()
        if engine_error is not None:
            self.power_flow_action.setToolTip(engine_error)
        self.power_flow_action.triggered.connect(self._run_power_flow)

        self.branches_action = QAction("Ramais…", self)
        self.branches_action.setEnabled(False)
        self.branches_action.setToolTip(
            "Identificar ramais monofásicos conectados aos troncos trifásicos"
        )
        if self._phase_configuration_error is not None:
            self.branches_action.setToolTip(self._phase_configuration_error)
        self.branches_action.triggered.connect(self._show_or_analyze_branches)

        self.select_action = QAction("Selecionar", self)
        self.select_action.setCheckable(True)
        self.select_action.setChecked(True)
        self.select_action.setShortcut(QKeySequence("S"))

        self.pan_action = QAction("Mover", self)
        self.pan_action.setCheckable(True)
        self.pan_action.setShortcut(QKeySequence("M"))

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_group.addAction(self.select_action)
        self.tool_group.addAction(self.pan_action)
        self.select_action.triggered.connect(
            lambda checked: checked and self.view.set_interaction_mode("select")
        )
        self.pan_action.triggered.connect(
            lambda checked: checked and self.view.set_interaction_mode("pan")
        )

        self.exit_action = QAction("Sair", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.triggered.connect(self.close)

    def _create_menus_and_toolbar(self) -> None:
        self.file_menu = self.menuBar().addMenu("Arquivo")
        self.file_menu.addAction(self.import_action)
        self.file_menu.addAction(self.mdb_import_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.exit_action)

        view_menu = self.menuBar().addMenu("Visualizar")
        view_menu.addAction(self.show_bars_action)
        view_menu.addAction(self.show_loads_action)
        view_menu.addAction(self.show_generators_action)
        view_menu.addAction(self.phase_coloring_action)
        view_menu.addAction(self.simplified_network_action)
        view_menu.addSeparator()
        view_menu.addAction(self.satellite_action)
        provider_menu = view_menu.addMenu("Provedor de satélite")
        for provider in PROVEDORES:
            provider_menu.addAction(self.satellite_provider_actions[provider])
        theme_menu = view_menu.addMenu("Tema")
        for theme in THEME_LABELS:
            theme_menu.addAction(self.theme_actions[theme])
        view_menu.addSeparator()
        view_menu.addAction(self.circuits_action)
        view_menu.addAction(self.overlaps_action)
        view_menu.addSeparator()
        view_menu.addAction(self.fit_action)

        self.tables_menu = self.menuBar().addMenu("Tabelas")
        self.tables_menu.addAction(self.cables_action)

        self.libraries_menu = self.menuBar().addMenu("Bibliotecas")
        self.libraries_menu.addAction(self.opendss_cables_action)
        self.libraries_menu.addAction(self.opendss_geometries_action)

        self.export_menu = self.menuBar().addMenu("Exportar")
        self.export_menu.addAction(self.opendss_export_action)
        self.export_menu.addAction(self.simplified_opendss_export_action)
        self.export_menu.addAction(self.opendss_allocation_export_action)

        self.tools_menu = self.menuBar().addMenu("Ferramentas")
        self.tools_menu.addAction(self.branches_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.update_generators_action)
        self.tools_menu.addAction(self.power_flow_action)

        # Menu próprio: é estado global da aplicação, e não um passo de uma
        # exportação ou de uma execução.
        self.settings_menu = self.menuBar().addMenu("Configurações")
        self.settings_menu.addAction(self.opendss_settings_action)
        self.settings_menu.addAction(self.patamares_action)
        self.settings_menu.addAction(self.curves_action)

        toolbar = QToolBar("Ferramentas principais", self)
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.addAction(self.select_action)
        toolbar.addAction(self.pan_action)
        toolbar.addAction(self.fit_action)
        toolbar.addSeparator()
        toolbar.addAction(self.search_action)
        toolbar.addSeparator()
        toolbar.addAction(self.power_flow_action)
        self.addToolBar(toolbar)

    def _create_details_dock(self) -> None:
        self.details_dock = QDockWidget("Elemento selecionado", self)
        self.details_dock.setObjectName("element_details_dock")
        self.details_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.details_dock.setMinimumWidth(330)

        self.details_stack = QStackedWidget(self.details_dock)
        self.empty_details_page = QWidget(self.details_stack)
        empty_layout = QVBoxLayout(self.empty_details_page)
        empty_message = QLabel(
            "Clique em uma barra, trecho, carga, gerador ou carga equivalente para ver seus dados."
        )
        empty_message.setWordWrap(True)
        empty_layout.addWidget(empty_message)
        empty_layout.addStretch(1)
        self.details_stack.addWidget(self.empty_details_page)

        def cell_style(row: int, column: int) -> str:
            # Cada fronteira compartilhada pertence somente à célula de cima
            # ou da esquerda, evitando linhas internas com dois pixels.
            top = "1px solid palette(mid)" if row == 0 else "0px"
            left = "1px solid palette(mid)" if column == 0 else "0px"
            return (
                f"border-top: {top};"
                "border-right: 1px solid palette(mid);"
                "border-bottom: 1px solid palette(mid);"
                f"border-left: {left};"
                "padding: 4px 6px;"
            )

        def create_table(
            fields: tuple[tuple[str, str], ...],
            parent: QWidget,
            *,
            with_companion: bool = False,
        ):
            # A terceira coluna existe para valores derivados de outro catálogo
            # (hoje, o cabo de CABOF_ID/CABON_ID). Os rótulos nascem ocultos e o
            # QGridLayout colapsa a coluna, então sem dados complementares a
            # tabela fica idêntica à de duas colunas.
            table = QWidget(parent)
            table.setObjectName("details_table")
            grid = QGridLayout(table)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(0)
            grid.setVerticalSpacing(0)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 1)
            if with_companion:
                grid.setColumnStretch(2, 1)
            labels: dict[str, QLabel] = {}
            caption_labels: dict[str, QLabel] = {}
            companion_labels: dict[str, QLabel] = {}
            for row, (key, caption) in enumerate(fields):
                caption_value = QLabel(caption)
                caption_value.setProperty("detailCell", True)
                caption_value.setProperty("detailColumn", "caption")
                caption_value.setStyleSheet(cell_style(row, 0))
                caption_value.setWordWrap(True)
                value = QLabel("—")
                value.setProperty("detailCell", True)
                value.setProperty("detailColumn", "value")
                value.setStyleSheet(cell_style(row, 1))
                value.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                value.setWordWrap(True)
                caption_labels[key] = caption_value
                labels[key] = value
                grid.addWidget(caption_value, row, 0)
                grid.addWidget(value, row, 1)
                if not with_companion:
                    continue
                companion = QLabel("—")
                companion.setProperty("detailCell", True)
                companion.setProperty("detailColumn", "companion")
                companion.setStyleSheet(cell_style(row, 2))
                companion.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                companion.setWordWrap(True)
                companion.setVisible(False)
                grid.addWidget(companion, row, 2)
                companion_labels[key] = companion
            return table, labels, caption_labels, grid, companion_labels

        def create_table_page(fields: tuple[tuple[str, str], ...]):
            page = QWidget(self.details_stack)
            page_layout = QVBoxLayout(page)
            table, labels, caption_labels, grid, _ = create_table(fields, page)
            page_layout.addWidget(table)
            page_layout.addStretch(1)
            return page, table, labels, caption_labels, grid

        def create_power_flow_section(
            parent: QWidget,
            quantities: tuple[tuple[str, str, int], ...],
            object_name: str,
        ):
            """Seção de resultados: um combobox de grandeza e a tabela dela.

            Nasce invisível — só aparece quando o elemento selecionado tem
            resultado — e o combobox carrega a chave da grandeza no ``UserRole``
            para o painel não depender da ordem dos itens.
            """

            section = QWidget(parent)
            layout = QVBoxLayout(section)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(QLabel("Resultados do fluxo de potência"))

            combo = QComboBox(section)
            combo.setObjectName(object_name)
            for key, caption, *_ in quantities:
                combo.addItem(caption, key)
            layout.addWidget(combo)

            model = PowerFlowTableModel(self)
            table = QTableView(section)
            table.setObjectName(f"{object_name}_table")
            table.setModel(model)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectItems
            )
            table.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection
            )
            table.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            table.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            table.verticalHeader().hide()
            table.verticalHeader().setDefaultSectionSize(28)
            enable_interactive_columns(table, always_refit=True)
            table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
            # Quatro patamares sempre; altura fixa evita a barra de rolagem
            # vertical dentro de uma tabela que nunca cresce.
            table.setFixedHeight(
                table.horizontalHeader().sizeHint().height()
                + 4 * table.verticalHeader().defaultSectionSize()
                + 2 * table.frameWidth()
                + 4
            )
            layout.addWidget(table)

            note = QLabel("")
            note.setWordWrap(True)
            note.setVisible(False)
            layout.addWidget(note)

            section.setVisible(False)
            return section, combo, model, note

        bar_fields = (
            ("bar_id", "BARRA_ID:"),
            ("code", "CODIGO:"),
            ("x", "X:"),
            ("y", "Y:"),
            ("zone", "Zona UTM:"),
            ("hemisphere", "Hemisfério:"),
            ("epsg", "EPSG:"),
        )
        (
            self.bar_details_page,
            self.bar_details_table,
            self.bar_detail_labels,
            self.bar_caption_labels,
            self.bar_details_grid,
        ) = create_table_page(bar_fields)
        (
            self.bar_power_flow_section,
            self.bar_power_flow_combo,
            self.bar_power_flow_model,
            self.bar_power_flow_note,
        ) = create_power_flow_section(
            self.bar_details_page,
            _BAR_QUANTITIES,
            "bar_power_flow_quantity",
        )
        # Entra antes do addStretch(1) que create_table_page deixou no fim.
        bar_layout = self.bar_details_page.layout()
        bar_layout.insertWidget(
            bar_layout.count() - 1,
            self.bar_power_flow_section,
        )
        self.bar_power_flow_combo.currentIndexChanged.connect(
            self._refresh_bar_power_flow_values
        )
        self.details_stack.addWidget(self.bar_details_page)

        load_fields = (
            ("load_id", "CARGA_ID:"),
            ("bar_id", "BARRA_ID:"),
            ("external_id", "EXTERN_ID:"),
            ("code", "CODIGO:"),
            ("snom", "SNOM:"),
            ("sadm", "SADM:"),
            ("secondary_line_voltage", "VLINHASEC:"),
            ("phases", "FASES2:"),
            ("connection_type", "TIPO_LIG:"),
        )
        self.load_details_page = QScrollArea(self.details_stack)
        self.load_details_page.setFrameShape(QFrame.Shape.NoFrame)
        self.load_details_page.setWidgetResizable(True)
        self.load_details_page.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.load_details_body = QWidget(self.load_details_page)
        load_layout = QVBoxLayout(self.load_details_body)
        self.load_table_title = QLabel("Dados da carga")
        load_layout.addWidget(self.load_table_title)
        (
            self.load_details_table,
            self.load_detail_labels,
            self.load_caption_labels,
            self.load_details_grid,
            self.load_companion_labels,
        ) = create_table(
            load_fields,
            self.load_details_body,
            with_companion=True,
        )
        load_layout.addWidget(self.load_details_table)

        self.load_patterns_section = QWidget(self.load_details_body)
        pattern_layout = QVBoxLayout(self.load_patterns_section)
        pattern_layout.setContentsMargins(0, 0, 0, 0)
        self.load_patterns_title = QLabel("Patamares da carga")
        pattern_layout.addWidget(self.load_patterns_title)
        self.load_pattern_table_model = LoadPatternTableModel(self)
        self.load_patterns_table = QTableView(self.load_patterns_section)
        self.load_patterns_table.setModel(self.load_pattern_table_model)
        self.load_patterns_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.load_patterns_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.load_patterns_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.load_patterns_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.load_patterns_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.load_patterns_table.verticalHeader().hide()
        self.load_patterns_table.verticalHeader().setDefaultSectionSize(28)
        enable_interactive_columns(self.load_patterns_table, always_refit=True)
        self.load_patterns_table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        table_height = (
            self.load_patterns_table.horizontalHeader().sizeHint().height()
            + 4 * self.load_patterns_table.verticalHeader().defaultSectionSize()
            + 2 * self.load_patterns_table.frameWidth()
            + 4
        )
        self.load_patterns_table.setFixedHeight(table_height)
        pattern_layout.addWidget(self.load_patterns_table)
        self.load_patterns_section.setVisible(False)
        load_layout.addWidget(self.load_patterns_section)
        load_layout.addStretch(1)
        self.load_details_page.setWidget(self.load_details_body)
        self.details_stack.addWidget(self.load_details_page)

        self.generator_details_page = QScrollArea(self.details_stack)
        self.generator_details_page.setFrameShape(QFrame.Shape.NoFrame)
        self.generator_details_page.setWidgetResizable(True)
        self.generator_details_page.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.generator_details_body = QWidget(self.generator_details_page)
        generator_layout = QVBoxLayout(self.generator_details_body)
        generator_layout.addWidget(QLabel("MT_GERADOR_CONS"))
        generator_fields = (
            ("generator_id", "GERADOR_ID:"),
            ("mt_cons_id", "MT_CONS_ID:"),
            ("generator_code", "CODIGO:"),
            ("nominal_voltage", "VNOM:"),
            ("nominal_power", "SNOM:"),
            ("connection", "LIGACAO:"),
            ("curve_id", "CURVA_ID:"),
            ("generation_kwh", "GERACAO_KWH:"),
        )
        (
            self.generator_details_table,
            self.generator_detail_labels,
            self.generator_caption_labels,
            self.generator_details_grid,
            _,
        ) = create_table(generator_fields, self.generator_details_body)
        generator_layout.addWidget(self.generator_details_table)
        generator_layout.addWidget(QLabel("MT_CONS"))
        generator_consumer_fields = (
            ("consumer_id", "ID:"),
            ("load_id", "CARGA_ID:"),
            ("consumer_code", "CODIGO:"),
            ("external_id", "EXTERN_ID:"),
            ("name", "NOME:"),
            ("phases", "FASES2:"),
        )
        (
            self.generator_consumer_details_table,
            self.generator_consumer_detail_labels,
            self.generator_consumer_caption_labels,
            self.generator_consumer_details_grid,
            _,
        ) = create_table(generator_consumer_fields, self.generator_details_body)
        generator_layout.addWidget(self.generator_consumer_details_table)

        def configure_generator_result_table(table: QTableView) -> None:
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectItems
            )
            table.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection
            )
            table.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            table.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            table.verticalHeader().hide()
            table.verticalHeader().setDefaultSectionSize(28)
            enable_interactive_columns(table, always_refit=True)
            table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
            table.setFixedHeight(table_height)

        self.generator_demand_section = QWidget(self.generator_details_body)
        generator_demand_layout = QVBoxLayout(self.generator_demand_section)
        generator_demand_layout.setContentsMargins(0, 0, 0, 0)
        generator_demand_layout.addWidget(QLabel("Demanda por Patamar"))
        self.generator_demand_table_model = GeneratorDemandTableModel(self)
        self.generator_demand_table = QTableView(self.generator_demand_section)
        self.generator_demand_table.setObjectName("generator_demand_table")
        self.generator_demand_table.setModel(self.generator_demand_table_model)
        configure_generator_result_table(self.generator_demand_table)
        generator_demand_layout.addWidget(self.generator_demand_table)
        self.generator_demand_section.setVisible(False)
        generator_layout.addWidget(self.generator_demand_section)

        self.generator_phase_power_section = QWidget(self.generator_details_body)
        generator_phase_layout = QVBoxLayout(self.generator_phase_power_section)
        generator_phase_layout.setContentsMargins(0, 0, 0, 0)
        generator_phase_layout.addWidget(QLabel("Potência por Fase"))
        self.generator_phase_power_table_model = GeneratorPhasePowerTableModel(
            self
        )
        self.generator_phase_power_table = QTableView(
            self.generator_phase_power_section
        )
        self.generator_phase_power_table.setObjectName(
            "generator_phase_power_table"
        )
        self.generator_phase_power_table.setModel(
            self.generator_phase_power_table_model
        )
        configure_generator_result_table(self.generator_phase_power_table)
        generator_phase_layout.addWidget(self.generator_phase_power_table)
        self.generator_phase_power_section.setVisible(False)
        generator_layout.addWidget(self.generator_phase_power_section)

        self.generator_update_note = QLabel(self.generator_details_body)
        self.generator_update_note.setObjectName("generator_update_note")
        self.generator_update_note.setWordWrap(True)
        self.generator_update_note.setVisible(False)
        generator_layout.addWidget(self.generator_update_note)
        generator_layout.addStretch(1)
        self.generator_details_page.setWidget(self.generator_details_body)
        self.details_stack.addWidget(self.generator_details_page)

        equivalent_fields = (
            ("origin", "ORIGEM:"),
            ("load_id", "CARGA_ID:"),
            ("branch_id", "RAMAL_ID:"),
            ("branch_type", "TIPO_RAMAL:"),
            ("removable", "REMANEJAVEL:"),
            ("circuit_id", "CIRC_ID:"),
            ("bar_id", "BARRA_ID:"),
            ("first_segment_id", "TRECHO_ID:"),
            ("phases2", "FASES2:"),
            ("phase", "FASE:"),
            ("source_load_count", "NUM_CARGAS:"),
            ("source_generator_count", "NUM_GERADORES:"),
            ("snom", "SNOM:"),
            ("sadm", "SADM:"),
            ("source_load_ids", "CARGAS_ORIGEM:"),
            ("source_generator_ids", "GERADORES_ORIGEM:"),
        )
        self.equivalent_details_page = QScrollArea(self.details_stack)
        self.equivalent_details_page.setFrameShape(QFrame.Shape.NoFrame)
        self.equivalent_details_page.setWidgetResizable(True)
        self.equivalent_details_body = QWidget(self.equivalent_details_page)
        equivalent_layout = QVBoxLayout(self.equivalent_details_body)
        self.equivalent_table_title = QLabel("Carga equivalente de ramal")
        equivalent_layout.addWidget(self.equivalent_table_title)
        (
            self.equivalent_details_table,
            self.equivalent_detail_labels,
            self.equivalent_caption_labels,
            self.equivalent_details_grid,
            _,
        ) = create_table(equivalent_fields, self.equivalent_details_body)
        equivalent_layout.addWidget(self.equivalent_details_table)

        self.equivalent_patterns_section = QWidget(self.equivalent_details_body)
        equivalent_pattern_layout = QVBoxLayout(self.equivalent_patterns_section)
        equivalent_pattern_layout.setContentsMargins(0, 0, 0, 0)
        equivalent_pattern_layout.addWidget(QLabel("Patamares equivalentes"))
        self.equivalent_pattern_table_model = LoadPatternTableModel(self)
        self.equivalent_patterns_table = QTableView(
            self.equivalent_patterns_section
        )
        self.equivalent_patterns_table.setModel(
            self.equivalent_pattern_table_model
        )
        self.equivalent_patterns_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.equivalent_patterns_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.equivalent_patterns_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.equivalent_patterns_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.equivalent_patterns_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.equivalent_patterns_table.verticalHeader().hide()
        self.equivalent_patterns_table.verticalHeader().setDefaultSectionSize(28)
        enable_interactive_columns(
            self.equivalent_patterns_table, always_refit=True
        )
        self.equivalent_patterns_table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        self.equivalent_patterns_table.setFixedHeight(table_height)
        equivalent_pattern_layout.addWidget(self.equivalent_patterns_table)
        self.equivalent_patterns_section.setVisible(False)
        equivalent_layout.addWidget(self.equivalent_patterns_section)
        equivalent_layout.addStretch(1)
        self.equivalent_details_page.setWidget(self.equivalent_details_body)
        self.details_stack.addWidget(self.equivalent_details_page)

        segment_fields = (
            ("segment_id", "TRECHO_ID:"),
            ("code", "CODIGO:"),
            ("phases", "FASES2:"),
            ("start_bar_id", "BARRA1_ID:"),
            ("end_bar_id", "BARRA2_ID:"),
            ("arrangement_id", "ARRANJO_ID:"),
            ("phase_cable_id", "CABOF_ID:"),
            ("neutral_cable_id", "CABON_ID:"),
            ("length", "COMPR:"),
        )
        self.segment_details_page = QScrollArea(self.details_stack)
        self.segment_details_page.setFrameShape(QFrame.Shape.NoFrame)
        self.segment_details_page.setWidgetResizable(True)
        self.segment_details_body = QWidget(self.segment_details_page)
        segment_layout = QVBoxLayout(self.segment_details_body)
        self.segment_table_title = QLabel("Dados do trecho")
        segment_layout.addWidget(self.segment_table_title)
        (
            self.segment_details_table,
            self.segment_detail_labels,
            self.segment_caption_labels,
            self.segment_details_grid,
            self.segment_companion_labels,
        ) = create_table(
            segment_fields,
            self.segment_details_body,
            with_companion=True,
        )
        segment_layout.addWidget(self.segment_details_table)

        switch_fields = (
            ("switch_id", "CHAVE_ID:"),
            ("switch_type_id", "TIPOCHV_ID:"),
            ("circuit_id", "CIRC_ID:"),
            ("segment_id", "TRECHO_ID:"),
            ("code", "CODIGO:"),
            ("state", "ESTADO:"),
            ("normal_state", "ESTADO_NORMAL:"),
            ("corn", "CORN:"),
            ("elo", "ELO:"),
            ("elo_type", "ELO_TIPO:"),
        )
        self.switch_details_section = QWidget(self.segment_details_body)
        switch_layout = QVBoxLayout(self.switch_details_section)
        switch_layout.setContentsMargins(0, 0, 0, 0)
        self.switch_table_title = QLabel("Dados da chave")
        switch_layout.addWidget(self.switch_table_title)
        (
            self.switch_details_table,
            self.switch_detail_labels,
            self.switch_caption_labels,
            self.switch_details_grid,
            _,
        ) = create_table(switch_fields, self.switch_details_section)
        switch_layout.addWidget(self.switch_details_table)
        self.switch_details_section.setVisible(False)
        segment_layout.addWidget(self.switch_details_section)

        regulator_fields = (
            ("regulator_id", "REGU_ID:"),
            ("segment_id", "TRECHO_ID:"),
            ("external_id", "EXTERN_ID:"),
            ("code", "CODIGO:"),
            ("connection", "LIGACAO:"),
            ("snom", "SNOM:"),
            ("regulation_range", "FAIXA:"),
            ("step_count", "NPASSOS:"),
            ("tap", "TAP:"),
            ("inom", "INOM:"),
            ("vnom", "VNOM:"),
        )
        self.regulator_details_section = QWidget(self.segment_details_body)
        regulator_layout = QVBoxLayout(self.regulator_details_section)
        regulator_layout.setContentsMargins(0, 0, 0, 0)
        self.regulator_table_title = QLabel("Dados do regulador")
        regulator_layout.addWidget(self.regulator_table_title)
        (
            self.regulator_details_table,
            self.regulator_detail_labels,
            self.regulator_caption_labels,
            self.regulator_details_grid,
            _,
        ) = create_table(regulator_fields, self.regulator_details_section)
        regulator_layout.addWidget(self.regulator_details_table)
        # Tap resolvido pelo fluxo de potência; só aparece quando há resultado.
        self.regulator_tap_label = QLabel("")
        self.regulator_tap_label.setObjectName("regulator_tap")
        self.regulator_tap_label.setWordWrap(True)
        self.regulator_tap_label.setVisible(False)
        regulator_layout.addWidget(self.regulator_tap_label)

        # Passos de tap por patamar: um retrato por NPAT, e não só o final.
        self.regulator_tap_table_title = QLabel("Passos de tap por patamar")
        self.regulator_tap_table_title.setVisible(False)
        regulator_layout.addWidget(self.regulator_tap_table_title)
        self.regulator_tap_table_model = PowerFlowTableModel(self)
        self.regulator_tap_table = QTableView(self.regulator_details_section)
        self.regulator_tap_table.setObjectName("regulator_tap_table")
        self.regulator_tap_table.setModel(self.regulator_tap_table_model)
        self.regulator_tap_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.regulator_tap_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.regulator_tap_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.regulator_tap_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.regulator_tap_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.regulator_tap_table.verticalHeader().hide()
        self.regulator_tap_table.verticalHeader().setDefaultSectionSize(28)
        enable_interactive_columns(self.regulator_tap_table, always_refit=True)
        self.regulator_tap_table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        self.regulator_tap_table.setFixedHeight(
            self.regulator_tap_table.horizontalHeader().sizeHint().height()
            + 4 * self.regulator_tap_table.verticalHeader().defaultSectionSize()
            + 2 * self.regulator_tap_table.frameWidth()
            + 4
        )
        self.regulator_tap_table.setVisible(False)
        regulator_layout.addWidget(self.regulator_tap_table)

        self.regulator_details_section.setVisible(False)
        segment_layout.addWidget(self.regulator_details_section)

        (
            self.segment_power_flow_section,
            self.segment_power_flow_combo,
            self.segment_power_flow_model,
            self.segment_power_flow_note,
        ) = create_power_flow_section(
            self.segment_details_body,
            _SEGMENT_QUANTITIES,
            "segment_power_flow_quantity",
        )
        self.segment_power_flow_combo.currentIndexChanged.connect(
            self._refresh_segment_power_flow_values
        )
        segment_layout.addWidget(self.segment_power_flow_section)
        segment_layout.addStretch(1)
        self.segment_details_page.setWidget(self.segment_details_body)
        self.details_stack.addWidget(self.segment_details_page)

        # Mantém o nome usado pela primeira versão para integrações existentes.
        self.detail_labels = self.bar_detail_labels
        self.details_dock.setWidget(self.details_stack)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.details_dock)

    def _create_status_bar(self) -> None:
        self.coordinate_status = QLabel("X: —   Y: —")
        self.total_status = QLabel("Barras: 0")
        self.segment_status = QLabel("Trechos: 0")
        self.load_status = QLabel("Cargas: 0")
        self.generator_status = QLabel("Geradores: 0")
        self.active_status = QLabel("Itens ativos: 0")
        self.mode_status = QLabel("Visão geral")
        self.overlap_status = QLabel("Sobreposições: 0")
        status = self.statusBar()
        status.addWidget(self.coordinate_status, 1)
        status.addPermanentWidget(self.total_status)
        status.addPermanentWidget(self.segment_status)
        status.addPermanentWidget(self.load_status)
        status.addPermanentWidget(self.generator_status)
        status.addPermanentWidget(self.overlap_status)
        status.addPermanentWidget(self.active_status)
        status.addPermanentWidget(self.mode_status)

    def _connect_signals(self) -> None:
        self.view.selectionRequested.connect(self._set_selection)
        self.view.mouseCoordinateChanged.connect(self._show_coordinates)
        self.view.viewportChanged.connect(
            self._schedule_viewport_overlay_update
        )
        self.view.zoomLimitReached.connect(self._show_zoom_limit_reached)
        self.view.satelliteUnavailable.connect(self._show_satellite_failure)
        self.virtualizer.countsChanged.connect(self._update_status_counts)
        self.load_virtualizer.countsChanged.connect(
            self._update_load_status_counts
        )
        self.equivalent_load_virtualizer.countsChanged.connect(
            self._update_equivalent_load_status_counts
        )
        self.generator_virtualizer.countsChanged.connect(
            self._update_generator_status_counts
        )
        self.virtualizer.modeChanged.connect(self.mode_status.setText)
        self.circuit_table_model.visibilityChanged.connect(
            self._schedule_circuit_visibility_update
        )
        self.circuit_table_model.colorChanged.connect(
            self._schedule_circuit_visibility_update
        )
        self.search_palette.resultActivated.connect(self._activate_search_result)
        self.search_palette.closed.connect(self.view.setFocus)
        self.branches_window.branchSelected.connect(self._select_branch)
        self.branches_window.branchActivated.connect(self._activate_branch)
        self.branches_window.selectionCleared.connect(
            self._clear_branch_highlight
        )
        self.branches_window.closed.connect(self._clear_branch_highlight)
        self.branches_window.exportJsonRequested.connect(
            self._export_visible_branches_json
        )
        self.branches_window.exportCsvRequested.connect(
            self._export_visible_branches_csv
        )
        self.cables_window.importRequested.connect(self._choose_cables_csv)

    def _choose_import(self) -> None:
        if self._busy():
            return
        dialog = ImportChoiceDialog(
            self._model is not None,
            self._line_model is not None,
            self,
            has_loads=self._load_model is not None,
            has_circuits=self._circuit_catalog is not None,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.selected_kind == "bars":
            self._choose_csv()
        elif dialog.selected_kind == "segments":
            self._choose_segments_csv()
        elif dialog.selected_kind == "loads":
            self._choose_loads_csv()
        elif dialog.selected_kind == "load_patterns":
            self._choose_load_patterns_csv()
        elif dialog.selected_kind == "generators":
            self._choose_generators_csv()
        elif dialog.selected_kind == "switches":
            self._choose_switches_csv()
        elif dialog.selected_kind == "regulators":
            self._choose_regulators_csv()
        elif dialog.selected_kind == "circuits":
            self._choose_circuits_csv()
        elif dialog.selected_kind == "circuit_levels":
            self._choose_circuit_levels_csv()
        elif dialog.selected_kind == "allocation_measurements":
            self._choose_allocation_measurements_csv()
        elif dialog.selected_kind == "cables":
            self._choose_cables_csv()

    def _choose_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar barras",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if not path:
            return

        try:
            suggested_scale = detect_coordinate_scale(path)
        except Exception:
            # A dedução é uma conveniência: se falhar, o diálogo abre em metros.
            suggested_scale = 1.0
        crs_dialog = UtmImportDialog(
            Path(path).name,
            self,
            suggested_scale=suggested_scale,
        )
        if crs_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not self.patamares_window.confirm_pending_changes():
            return
        self._start_import(path, crs_dialog.crs(), crs_dialog.coordinate_scale())

    def _choose_segments_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._model is None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar trechos",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path and self.patamares_window.confirm_pending_changes():
            self._start_segment_import(path)

    def _choose_switches_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._line_model is None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar chaves",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path and self.patamares_window.confirm_pending_changes():
            self._start_switch_import(path)

    def _choose_regulators_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._line_model is None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar reguladores",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self._start_regulator_import(path)

    def _choose_cables_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar cabos",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self._start_cable_import(path)

    def _choose_loads_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._model is None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar cargas",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self._start_load_import(path)

    def _choose_load_patterns_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._load_model is None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar patamares de carga",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self._start_load_pattern_import(path)

    def _choose_generators_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._load_model is None
        ):
            return
        dialog = GeneratorCsvImportDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._start_generator_import(
                dialog.generator_path(), dialog.consumer_path()
            )

    def _choose_circuits_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._line_model is None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar circuitos",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            if not self.patamares_window.confirm_pending_changes():
                return
            self._start_circuit_import(path)

    def _choose_circuit_levels_csv(self) -> None:
        if self._busy() or self._circuit_catalog is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar patamares dos circuitos",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path and self.patamares_window.confirm_pending_changes():
            self._start_circuit_level_import(path)

    def _choose_allocation_measurements_csv(self) -> None:
        if self._busy() or self._circuit_catalog is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Correntes OpenDSS — cabeçalho: CODIGO;NPAT;ID;IE;IF",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self._start_allocation_measurement_import(path)

    def _busy(self) -> bool:
        """``True`` quando alguma operação pesada já ocupa um dos slots."""

        return (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._branch_json_thread is not None
            or self._branch_csv_thread is not None
            or self._power_flow_thread is not None
            or self._generator_update_thread is not None
        )

    def _open_mdb(self, path: str):  # noqa: ANN201
        """Abre o banco pedindo a senha quando o driver reclamar dela.

        Devolve ``(gerenciador, senha)`` ou ``None`` se o usuário desistir. O
        contexto volta aberto de propósito: o diálogo precisa consultar tabelas
        e amostrar coordenadas antes de a importação começar.
        """

        password: str | None = None
        retry = False
        while True:
            manager = open_database(path, password)
            try:
                database = manager.__enter__()
            except MdbPasswordError:
                dialog = MdbPasswordDialog(Path(path).name, self, retry=retry)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return None
                password = dialog.password()
                retry = True
                continue
            return manager, database, password

    def _choose_mdb_import(self) -> None:
        if self._busy() or self._mdb_error is not None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar banco de dados",
            "",
            "Bancos Access (*.mdb *.accdb);;Todos os arquivos (*)",
        )
        if not path:
            return

        try:
            opened = self._open_mdb(path)
        except MdbEngineError as exc:
            QMessageBox.critical(self, "Falha ao abrir o banco", str(exc))
            return
        if opened is None:
            return
        manager, database, password = opened

        try:
            plan = resolve_mapping(database, self._mdb_table_mapping)
            table_names = database.tables()
            row_counts: dict[str, int] = {}
            for entity in plan.resolved:
                try:
                    row_counts[entity.table] = database.row_count(entity.table)
                except Exception:  # noqa: BLE001 — a contagem é informativa
                    continue
            suggested_scale = detect_database_scale(database, plan)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Falha ao ler o banco", str(exc))
            return
        finally:
            # A conexão de inspeção é fechada antes de o worker começar: ele
            # abre a sua própria, porque uma conexão ODBC não é segura para
            # atravessar threads.
            manager.__exit__(None, None, None)

        if not plan.has_mandatory:
            QMessageBox.critical(
                self,
                "Banco incompatível",
                "A tabela de barras não foi encontrada no banco.\n\n"
                + (plan.reason_for("barras") or ""),
            )
            return

        dialog = MdbImportDialog(
            path,
            plan,
            table_names,
            self,
            suggested_scale=suggested_scale,
            row_counts=row_counts,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selection = dialog.selection()
        # Toda importação MDB substitui as barras e, por consequência, o
        # catálogo de circuitos e suas agendas de sessão atuais.
        if not self.patamares_window.confirm_pending_changes():
            return
        self._start_mdb_import(path, selection, password)

    def _start_mdb_import(self, path: str, selection, password) -> None:  # noqa: ANN001
        thread = QThread(self)
        worker = MdbImportWorker(
            path,
            selection.crs,
            password=password,
            entities=selection.entities,
            overrides=selection.overrides,
            scale=selection.scale,
            phase_configuration=self._phase_configuration,
        )
        worker.moveToThread(thread)

        progress = QProgressDialog("Lendo o banco…", "Cancelar", 0, 100, self)
        progress.setWindowTitle("Importando banco de dados")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "registros"
        self.import_action.setEnabled(False)
        self.mdb_import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.patamares_window.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_mdb_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_mdb_import_finished(self, result: MdbImportResult) -> None:
        """Instala os dez modelos pelos setters existentes, na mesma ordem.

        Não há cascata nova aqui: reusar ``_set_*_model`` é o que mantém as
        invalidações exatamente como estão documentadas na seção 6 da
        arquitetura. A ordem importa — as chaves precisam entrar antes do
        catálogo de circuitos, que é reconstruído por ``_set_switch_model``.
        """

        _close_progress_dialog(self._progress_dialog)

        # As barras substituem tudo: trechos e cargas antigos referenciam o
        # modelo anterior e precisam sair antes.
        self._set_load_model(None)
        self._set_line_model(None)
        self._model = result.bars.model
        self.search_index.set_bars(result.bars.model, build_fields=False)
        self.search_palette.schedule_field_index("bar", result.bars.model)
        self._selected_feature = None
        self.view.set_model(result.bars.model)
        self.virtualizer.reset_model(result.bars.model)
        self._set_selection(None)
        self.total_status.setText(f"Barras: {len(result.bars.model):n}")
        self.show_bars_action.setEnabled(True)
        self.fit_action.setEnabled(True)

        if result.cables is not None:
            self._set_cable_model(result.cables.model)
        if result.segments is not None:
            self._set_line_model(result.segments.model)
        if result.loads is not None:
            self._set_load_model(result.loads.model)
        if result.allocations is not None:
            self._set_allocation_model(result.allocations)
        if result.generators is not None:
            self._set_generator_model(result.generators.model)
        if result.patterns is not None:
            self._set_load_pattern_model(result.patterns.model)
        if result.switches is not None:
            self._set_switch_model(result.switches.model)
        if result.regulators is not None:
            self._set_regulator_model(result.regulators.model)
        if result.circuits is not None:
            self._set_circuit_catalog(result.circuits.model)
        if result.circuit_levels is not None:
            self._set_circuit_level_model(result.circuit_levels.model)

        self._sync_search_availability()
        self._sync_export_availability()
        self._fit_all()
        if result.circuits is not None:
            self._show_circuits_window()
        self._show_mdb_import_report(result)

    def _show_mdb_import_report(self, result: MdbImportResult) -> None:
        imported = len(result.imported_entities)
        summary = (
            f"Banco importado: {imported} de {len(ENTITY_ORDER)} entidades, "
            f"{len(result.bars.model):n} barras."
        )
        self.statusBar().showMessage(summary, 10_000)
        if not result.has_warnings:
            return
        # A referência fica na janela: sem ela o diálogo não modal seria
        # coletado assim que o método retornasse.
        if self._mdb_report_window is not None:
            self._mdb_report_window.close()
        self._mdb_report_window = MdbImportReportWindow(result, self)
        self._mdb_report_window.show()
        self._mdb_report_window.raise_()

    def _start_import(self, path: str, crs: UtmCrs, scale: float = 1.0) -> None:
        thread = QThread(self)
        worker = CsvImportWorker(path, crs, scale)
        worker.moveToThread(thread)

        progress = QProgressDialog("Lendo barras…", "Cancelar", 0, 100, self)
        progress.setWindowTitle("Importando CSV")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "barras"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.patamares_window.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_segment_import(self, path: str) -> None:
        if self._model is None:
            return
        thread = QThread(self)
        worker = SegmentImportWorker(path, self._model)
        worker.moveToThread(thread)

        progress = QProgressDialog("Lendo trechos…", "Cancelar", 0, 100, self)
        progress.setWindowTitle("Importando trechos")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "trechos"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.patamares_window.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_segment_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_switch_import(self, path: str) -> None:
        if self._line_model is None:
            return
        thread = QThread(self)
        worker = SwitchImportWorker(path, self._line_model)
        worker.moveToThread(thread)

        progress = QProgressDialog("Lendo chaves…", "Cancelar", 0, 100, self)
        progress.setWindowTitle("Importando chaves")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "chaves"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.patamares_window.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_switch_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_regulator_import(self, path: str) -> None:
        if self._line_model is None:
            return
        thread = QThread(self)
        worker = RegulatorImportWorker(path, self._line_model)
        worker.moveToThread(thread)

        progress = QProgressDialog("Lendo reguladores…", "Cancelar", 0, 100, self)
        progress.setWindowTitle("Importando reguladores")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "reguladores"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_regulator_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_cable_import(self, path: str) -> None:
        thread = QThread(self)
        worker = CableImportWorker(path)
        worker.moveToThread(thread)

        progress = QProgressDialog("Lendo cabos…", "Cancelar", 0, 100, self)
        progress.setWindowTitle("Importando cabos")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "cabos"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_cable_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_load_import(self, path: str) -> None:
        if self._model is None:
            return
        thread = QThread(self)
        worker = LoadImportWorker(path, self._model)
        worker.moveToThread(thread)

        progress = QProgressDialog("Lendo cargas…", "Cancelar", 0, 100, self)
        progress.setWindowTitle("Importando cargas")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "cargas"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_load_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_load_pattern_import(self, path: str) -> None:
        if self._load_model is None:
            return
        thread = QThread(self)
        worker = LoadPatternImportWorker(path, self._load_model)
        worker.moveToThread(thread)

        progress = QProgressDialog(
            "Lendo patamares de carga…",
            "Cancelar",
            0,
            100,
            self,
        )
        progress.setWindowTitle("Importando patamares de carga")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "patamares"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_load_pattern_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_generator_import(
        self, generator_path: str, consumer_path: str
    ) -> None:
        if self._load_model is None:
            return
        thread = QThread(self)
        worker = GeneratorImportWorker(
            generator_path, consumer_path, self._load_model
        )
        worker.moveToThread(thread)

        progress = QProgressDialog(
            "Lendo MT_CONS…", "Cancelar", 0, 100, self
        )
        progress.setWindowTitle("Importando geradores")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "MT_CONS"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)

        thread.started.connect(worker.run)
        worker.stageChanged.connect(self._on_generator_stage_changed)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_generator_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_generator_stage_changed(self, stage: str) -> None:
        self._progress_entity = stage

    def _start_circuit_import(self, path: str) -> None:
        if self._line_model is None:
            return
        thread = QThread(self)
        worker = CircuitImportWorker(
            path,
            self._line_model,
            self._switch_model,
        )
        worker.moveToThread(thread)

        progress = QProgressDialog(
            "Lendo circuitos e construindo a topologia…",
            "Cancelar",
            0,
            100,
            self,
        )
        progress.setWindowTitle("Importando circuitos")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "circuitos"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.patamares_window.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_circuit_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_circuit_level_import(self, path: str) -> None:
        if self._circuit_catalog is None:
            return
        thread = QThread(self)
        worker = CircuitLevelImportWorker(path, self._circuit_catalog)
        worker.moveToThread(thread)

        progress = QProgressDialog(
            "Lendo patamares dos circuitos…", "Cancelar", 0, 100, self
        )
        progress.setWindowTitle("Importando patamares dos circuitos")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "patamares dos circuitos"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.patamares_window.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_circuit_level_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_allocation_measurement_import(self, path: str) -> None:
        if self._circuit_catalog is None:
            return
        thread = QThread(self)
        worker = AllocationMeasurementImportWorker(path, self._circuit_catalog)
        worker.moveToThread(thread)
        progress = QProgressDialog(
            "Lendo correntes de alocação…", "Cancelar", 0, 100, self
        )
        progress.setWindowTitle("Importando correntes de alocação")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._import_thread = thread
        self._import_worker = worker
        self._progress_dialog = progress
        self._progress_entity = "correntes de alocação"
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.patamares_window.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_import_progress)
        worker.finished.connect(self._on_allocation_measurement_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.cancelled.connect(self._on_import_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_import_progress(self, rows: int, current: int, total: int) -> None:
        if not self._is_current_signal_source(self._import_worker):
            return
        progress = self._progress_dialog
        percent = min(99, int(current * 100 / max(total, 1)))
        _update_progress_dialog(
            progress,
            label=f"Lendo {self._progress_entity}… {rows:n} linhas",
            value=percent,
        )

    def _on_import_finished(self, result: CsvLoadResult) -> None:
        _close_progress_dialog(self._progress_dialog)

        # Trechos e cargas referenciam o modelo anterior e só são removidos
        # depois que a nova importação de barras foi concluída com sucesso.
        self._set_load_model(None)
        self._set_line_model(None)
        self._model = result.model
        self.search_index.set_bars(result.model, build_fields=False)
        self.search_palette.schedule_field_index("bar", result.model)
        self._sync_search_availability()
        self._selected_feature = None
        self.view.set_model(result.model)
        self.virtualizer.reset_model(result.model)
        self._set_selection(None)
        self.total_status.setText(f"Barras: {len(result.model):n}")
        self.show_bars_action.setEnabled(True)
        self.fit_action.setEnabled(True)
        self._sync_export_availability()
        self._fit_all()
        self._show_import_report(result)

    def _on_segment_import_finished(self, result: SegmentLoadResult) -> None:
        _close_progress_dialog(self._progress_dialog)
        if self._model is None or result.model.bars is not self._model:
            QMessageBox.critical(
                self,
                "Falha na importação",
                "As barras foram alteradas durante a importação dos trechos.",
            )
            return
        self._set_line_model(result.model)
        self._show_segment_import_report(result)

    def _on_load_import_finished(self, result: LoadCsvResult) -> None:
        _close_progress_dialog(self._progress_dialog)
        if self._model is None or result.model.bars is not self._model:
            QMessageBox.critical(
                self,
                "Falha na importação",
                "As barras foram alteradas durante a importação das cargas.",
            )
            return
        self._set_load_model(result.model)
        self._show_load_import_report(result)

    def _on_generator_import_finished(self, result: GeneratorCsvResult) -> None:
        _close_progress_dialog(self._progress_dialog)
        if self._load_model is None or result.model.loads is not self._load_model:
            QMessageBox.critical(
                self,
                "Falha na importação",
                "As cargas foram alteradas durante a importação dos geradores.",
            )
            return
        self._set_generator_model(result.model)
        self._show_generator_import_report(result)

    def _on_load_pattern_import_finished(
        self,
        result: LoadPatternCsvResult,
    ) -> None:
        _close_progress_dialog(self._progress_dialog)
        if self._load_model is None or result.model.loads is not self._load_model:
            QMessageBox.critical(
                self,
                "Falha na importação",
                "As cargas foram alteradas durante a importação dos patamares.",
            )
            return
        self._set_load_pattern_model(result.model)
        self._show_load_pattern_import_report(result)

    def _on_cable_import_finished(self, result: CableCsvResult) -> None:
        _close_progress_dialog(self._progress_dialog)
        # Catálogo raiz: não há modelo-pai cuja identidade precise ser validada.
        self._set_cable_model(result.model)
        self._show_cable_import_report(result)

    def _on_switch_import_finished(self, result: SwitchLoadResult) -> None:
        _close_progress_dialog(self._progress_dialog)
        if self._line_model is None or result.model.segments is not self._line_model:
            QMessageBox.critical(
                self,
                "Falha na importação",
                "Os trechos foram alterados durante a importação das chaves.",
            )
            return
        self._set_switch_model(result.model)
        topology_warnings = (
            ()
            if self._circuit_catalog is None
            else self._circuit_catalog.topology_warnings
        )
        self._show_switch_import_report(result, topology_warnings)

    def _on_regulator_import_finished(self, result: RegulatorLoadResult) -> None:
        _close_progress_dialog(self._progress_dialog)
        if self._line_model is None or result.model.segments is not self._line_model:
            QMessageBox.critical(
                self,
                "Falha na importação",
                "Os trechos foram alterados durante a importação dos reguladores.",
            )
            return
        self._set_regulator_model(result.model)
        self._show_regulator_import_report(result)

    def _on_circuit_import_finished(self, result: CircuitLoadResult) -> None:
        _close_progress_dialog(self._progress_dialog)
        if (
            self._line_model is None
            or result.model.segments is not self._line_model
            or result.model.switches is not self._switch_model
        ):
            QMessageBox.critical(
                self,
                "Falha na importação",
                "Os trechos ou as chaves foram alterados durante a importação "
                "dos circuitos.",
            )
            return
        self._set_circuit_catalog(result.model)
        self._show_circuits_window()
        self._show_circuit_import_report(result)

    def _on_circuit_level_import_finished(
        self, result: CircuitLevelCsvResult
    ) -> None:
        _close_progress_dialog(self._progress_dialog)
        if (
            self._circuit_catalog is None
            or result.model.circuits is not self._circuit_catalog
        ):
            QMessageBox.critical(
                self,
                "Falha na importação",
                "Os circuitos foram alterados durante a importação dos patamares.",
            )
            return
        self._set_circuit_level_model(result.model)
        self._show_circuit_level_import_report(result)

    def _on_allocation_measurement_import_finished(
        self,
        result: AllocationMeasurementCsvResult,
    ) -> None:
        _close_progress_dialog(self._progress_dialog)
        if (
            self._circuit_catalog is None
            or result.model.circuits is not self._circuit_catalog
        ):
            QMessageBox.critical(
                self,
                "Falha na importação",
                "Os circuitos foram alterados durante a importação das correntes.",
            )
            return
        self._set_allocation_measurements(result.model)
        imported = len(result.model.available_indices)
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"Correntes de alocação importadas para "
                f"{imported:n} circuito(s).",
                6_000,
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Correntes importadas com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(
            f"As quatro medições foram importadas para {imported:n} circuito(s)."
        )
        message.setInformativeText(
            f"Linhas lidas: {result.total_rows:n}\n"
            f"Linhas usadas: {result.valid_rows:n}\n"
            f"Linhas de grupos recusados: {result.invalid_rows:n}"
        )
        if result.issues:
            message.setDetailedText("\n".join(result.issues))
        message.exec()

    def _set_line_model(self, model: LineNetworkModel | None) -> None:
        self._invalidate_power_flow()
        self._invalidate_branch_analysis()
        if self._search_focus_active:
            self._set_selection(None)
        self._set_circuit_catalog(None)
        self._set_switch_model(None)
        # Reguladores referenciam os trechos antigos por índice; trechos novos
        # os invalidam. É a única cascata que eles têm.
        self._set_regulator_model(None)
        if (
            self._selected_feature is not None
            and self._selected_feature.kind == "segment"
        ):
            self._set_selection(None)
        if self._line_item is not None:
            self.scene.removeItem(self._line_item)
            self._line_item = None
        self._line_model = model
        self.opendss_automatic_assembly_session.set_line_model(model)
        self._phase_classification = (
            None
            if model is None or self._phase_configuration is None
            else self._phase_configuration.classify(model.phases)
        )
        self.phase_coloring_action.setEnabled(
            model is not None and self._phase_configuration is not None
        )
        if self._phase_classification is not None:
            self.phase_legend.set_unmapped_count(
                self._phase_classification.unmapped_count
            )
        else:
            self.phase_legend.hide()
        self.search_index.set_segments(model, build_fields=False)
        self.search_palette.schedule_field_index("segment", model)
        self._sync_search_availability()
        self.view.set_line_model(model)
        if model is not None:
            self._line_item = LineNetworkItem(model)
            self.scene.addItem(self._line_item)
        self.segment_status.setText(f"Trechos: {len(model) if model is not None else 0:n}")
        self._apply_circuit_visibility()
        self._sync_export_availability()
        self.view.viewport().update()

    def _set_load_model(self, model: LoadModel | None) -> None:
        self._invalidate_power_flow()
        self._invalidate_branch_analysis()
        if model is not None and model.bars is not self._model:
            raise ValueError("As cargas devem referenciar as barras exibidas.")
        if model is not self._load_model:
            self._set_generator_model(None)
            self._set_allocation_model(None)
        if (
            self._selected_feature is not None
            and self._selected_feature.kind == "load"
        ):
            self._set_selection(None)
        self._set_load_pattern_model(None)
        self._load_model = model
        self.search_index.set_loads(model, build_fields=False)
        self.search_palette.schedule_field_index("load", model)
        self._sync_search_availability()
        self.view.set_load_model(model)
        self.load_virtualizer.reset_model(model)
        self._sync_load_layout()
        self.show_loads_action.setEnabled(model is not None)
        self.load_status.setText(f"Cargas: {len(model) if model is not None else 0:n}")
        self._apply_circuit_visibility()
        if model is not None and self.show_loads_action.isChecked():
            self.load_virtualizer.refresh(force=True)
        self.view.viewport().update()

    def _set_allocation_model(
        self,
        model: TransformerAllocationModel | None,
    ) -> None:
        if model is not None:
            if model.loads is not self._load_model:
                raise ValueError("Os agregados devem pertencer às cargas exibidas.")
            if model.phase_configuration is not self._phase_configuration:
                raise ValueError(
                    "Os agregados devem usar a configuração de fases atual."
                )
        self._allocation_model = model
        self._sync_export_availability()

    def _set_allocation_measurements(
        self,
        model: AllocationMeasurementModel | None,
    ) -> None:
        if model is not None and model.circuits is not self._circuit_catalog:
            raise ValueError("As correntes devem pertencer aos circuitos exibidos.")
        self._allocation_measurements = model
        self._sync_export_availability()

    def _set_generator_model(self, model: GeneratorModel | None) -> None:
        if model is not None and model.loads is not self._load_model:
            raise ValueError("Os geradores devem pertencer às cargas exibidas.")
        if model is not self._generator_model:
            self._invalidate_equivalent_network()
            self._invalidate_generator_update()
        if (
            self._selected_feature is not None
            and self._selected_feature.kind == "generator"
        ):
            self._set_selection(None)
        self._generator_model = model
        self.view.set_generator_model(model)
        self.generator_virtualizer.reset_model(model)
        self._sync_load_layout()
        self.show_generators_action.setEnabled(model is not None)
        self.generator_status.setText(
            f"Geradores: {len(model) if model is not None else 0:n}"
        )
        self._apply_circuit_visibility()
        if model is not None and self.show_generators_action.isChecked():
            self.generator_virtualizer.refresh(force=True)
        self.view.viewport().update()

    def _set_load_pattern_model(self, model: LoadPatternModel | None) -> None:
        if model is not None and model.loads is not self._load_model:
            raise ValueError("Os patamares devem pertencer às cargas exibidas.")
        # Os patamares são a potência de cada Load exportada: trocá-los muda o
        # fluxo inteiro.
        self._invalidate_power_flow()
        rebuild_visual = (
            self.simplified_network_action.isChecked()
            and self._branch_analysis_result is not None
        )
        rebuild_table = (
            self.branches_window.isVisible()
            and self._branch_analysis_result is not None
        )
        self._invalidate_equivalent_network(
            keep_requested=rebuild_visual
        )
        self._load_pattern_model = model
        selection = self._selected_feature
        if selection is not None and selection.kind == "load":
            self._set_selection(
                selection,
                reveal_hidden=self._search_focus_active,
            )
        elif model is None:
            self.load_pattern_table_model.set_records(())
            self.load_patterns_section.setVisible(False)
        if (rebuild_visual or rebuild_table) and self._import_thread is None:
            self._start_equivalent_build()

    def _set_cable_model(self, model: CableModel | None) -> None:
        """Instala o catálogo de cabos.

        Não há cascata de invalidação: cabos são raiz e ninguém deriva deles —
        os trechos apenas exibem o cabo correspondente quando ele existe. O
        fluxo de potência é a exceção, porque consome R/X/QCAP e IADM do cabo.
        """

        self._invalidate_power_flow()
        self._cable_model = model
        self.cable_table_model.set_catalog(model)
        self.cables_window.refresh()
        self._sync_export_availability()
        self._sync_power_flow_availability()
        selection = self._selected_feature
        if selection is not None and selection.kind == "segment":
            self._set_selection(
                selection,
                reveal_hidden=self._search_focus_active,
            )

    def _set_switch_model(self, model: SwitchModel | None) -> None:
        self._invalidate_power_flow()
        self._invalidate_branch_analysis()
        if self._search_focus_active:
            self._set_selection(None)
        if model is not None and model.segments is not self._line_model:
            raise ValueError("As chaves devem referenciar os trechos exibidos.")
        previous_catalog = self._circuit_catalog
        previous_checked = (
            None
            if self._circuit_visibility is None
            else self._circuit_visibility.checked_states
        )
        previous_colors = (
            None
            if self._circuit_visibility is None
            else self._circuit_visibility.colors
        )
        if self._switch_item is not None:
            self.scene.removeItem(self._switch_item)
            self._switch_item = None
        self._switch_model = model
        self.search_index.set_switches(model, build_fields=False)
        self.search_palette.schedule_field_index("switch", model)
        self._sync_search_availability()
        if self._line_item is not None:
            self._line_item.set_switch_segment_indices(
                None if model is None else model.segment_indices
            )
        if model is not None:
            self._switch_item = SwitchNetworkItem(model)
            self.scene.addItem(self._switch_item)
        if previous_catalog is not None and self._line_model is not None:
            rebuilt_catalog = CircuitCatalogModel.build(
                self._line_model,
                model,
                previous_catalog.definitions,
                source_path=previous_catalog.source_path,
            )
            display_by_id = {
                definition.circuit_id: (previous_checked[index], previous_colors[index])
                for index, definition in enumerate(previous_catalog.definitions)
            }
            rebuilt_checked = tuple(
                display_by_id[definition.circuit_id][0]
                for definition in rebuilt_catalog.definitions
            )
            rebuilt_colors = tuple(
                display_by_id[definition.circuit_id][1]
                for definition in rebuilt_catalog.definitions
            )
            self._set_circuit_catalog(
                rebuilt_catalog,
                rebuilt_checked,
                rebuilt_colors,
            )
            if rebuilt_catalog.topology_warnings:
                self.statusBar().showMessage(
                    "Circuitos recalculados com avisos de topologia.", 5_000
                )
        if (
            self._selected_feature is not None
            and self._selected_feature.kind == "segment"
            and self._line_model is not None
        ):
            self._set_selection(self._selected_feature)
        self._apply_circuit_visibility()
        self._sync_export_availability()
        self.view.viewport().update()

    def _set_regulator_model(self, model: RegulatorModel | None) -> None:
        """Instala os reguladores do trecho selecionável.

        Cascata mínima: reguladores continuam fora da topologia — não
        interrompem nem energizam nada, então não reconstroem o catálogo de
        circuitos e não invalidam ramais nem rede simplificada. Mas eles **são
        exportados** e regulam a tensão, então um resultado de fluxo de potência
        calculado com outro conjunto de reguladores deixa de valer.
        """

        if model is not None and model.segments is not self._line_model:
            raise ValueError("Os reguladores devem referenciar os trechos exibidos.")
        if model is not self._regulator_model:
            self._invalidate_power_flow()
        if self._regulator_item is not None:
            self.scene.removeItem(self._regulator_item)
            self._regulator_item = None
        self._regulator_model = model
        if model is not None:
            self._regulator_item = RegulatorNetworkItem(model)
            self.scene.addItem(self._regulator_item)
        self.search_index.set_regulators(model, build_fields=False)
        self.search_palette.schedule_field_index("regulator", model)
        self._sync_search_availability()
        selection = self._selected_feature
        if selection is not None and selection.kind == "segment":
            self._set_selection(
                selection,
                reveal_hidden=self._search_focus_active,
            )
        elif model is None:
            self.regulator_details_section.setVisible(False)
        # O anel entra na cena agora, então precisa da máscara de circuitos e de
        # um repaint — nenhum dos dois existia enquanto reguladores não eram
        # desenhados.
        self._apply_circuit_visibility()
        self.view.viewport().update()

    def _set_circuit_catalog(
        self,
        catalog: CircuitCatalogModel | None,
        checked: tuple[bool, ...] | None = None,
        colors: tuple[str, ...] | None = None,
    ) -> None:
        self._invalidate_power_flow()
        self._invalidate_branch_analysis()
        if self._search_focus_active:
            self._set_selection(None)
        if catalog is not None:
            if catalog.segments is not self._line_model:
                raise ValueError("Os circuitos devem pertencer aos trechos exibidos.")
            if catalog.switches is not self._switch_model:
                raise ValueError("Os circuitos devem usar as chaves exibidas.")
        if catalog is not self._circuit_catalog:
            self._invalidate_generator_update()
            self._set_circuit_level_model(None)
            self._set_allocation_measurements(None)
        self._circuit_visibility_timer.stop()
        self._circuit_catalog = catalog
        self.search_index.set_circuits(catalog, build_fields=False)
        self.search_palette.schedule_field_index("circuit", catalog)
        self._circuit_visibility = (
            None
            if catalog is None
            else CircuitVisibilityController(catalog, checked, colors)
        )
        self._sync_search_availability()
        self.circuit_table_model.set_source(
            self._circuit_catalog,
            self._circuit_visibility,
        )
        self.circuits_action.setEnabled(catalog is not None)
        overlap_count = (
            0 if catalog is None else int(catalog.overlapping_segment_indices.size)
        )
        self.overlap_table_model.set_catalog(catalog)
        self.overlap_report_window.update_summary(overlap_count)
        self.overlaps_action.setEnabled(overlap_count > 0)
        self.overlap_status.setText(f"Sobreposições: {overlap_count:n}")
        if catalog is None:
            self.circuits_window.hide()
        if overlap_count:
            self._show_overlap_report()
        else:
            self.overlap_report_window.hide()
        self._sync_branches_availability()
        self._sync_export_availability()
        self._sync_power_flow_availability()
        self._apply_circuit_visibility()

    def _set_circuit_level_model(
        self, model: CircuitCalculationLevelsModel | None
    ) -> None:
        if model is not None and model.circuits is not self._circuit_catalog:
            raise ValueError(
                "Os patamares devem pertencer ao catálogo de circuitos exibido."
            )
        if model is not self._circuit_level_model:
            self._invalidate_generator_update()
        self._circuit_level_model = model
        self._circuit_level_controller = (
            None if model is None else CircuitCalculationLevelsController(model)
        )
        self.patamares_window.set_circuit_levels(self._circuit_level_controller)

    def _set_generator_update_result(
        self, result: GeneratorUpdateResult | None
    ) -> None:
        if result is not None:
            if result.model.generators is not self._generator_model:
                raise ValueError("O resultado deve pertencer aos geradores exibidos.")
            if result.model.circuits is not self._circuit_catalog:
                raise ValueError("O resultado deve pertencer aos circuitos exibidos.")
            if result.model.phase_configuration is not self._phase_configuration:
                raise ValueError(
                    "O resultado deve usar a configuração de fases atual."
                )
        changed = result is not self._generator_update_result
        rebuild_visual = bool(
            changed
            and result is not None
            and self.simplified_network_action.isChecked()
            and self._branch_analysis_result is not None
        )
        rebuild_table = bool(
            changed
            and result is not None
            and self.branches_window.isVisible()
            and self._branch_analysis_result is not None
        )
        if changed:
            self._invalidate_power_flow()
            self._invalidate_equivalent_network(
                keep_requested=rebuild_visual
            )
        self._generator_update_result = result
        selection = self._selected_feature
        if selection is not None and selection.kind == "generator":
            self._set_selection(
                selection,
                reveal_hidden=self._search_focus_active,
            )
        if (rebuild_visual or rebuild_table) and self._generator_update_thread is None:
            self._start_equivalent_build()

    def _invalidate_generator_update(self) -> None:
        if self._generator_update_worker is not None:
            self._generator_update_worker.cancel()
        had_result = self._generator_update_result is not None
        if had_result:
            self._invalidate_power_flow()
            self._invalidate_equivalent_network()
        self._generator_update_result = None
        if hasattr(self, "generator_demand_table_model"):
            self.generator_demand_table_model.set_records(())
            self.generator_phase_power_table_model.set_records(())
            self.generator_demand_section.setVisible(False)
            self.generator_phase_power_section.setVisible(False)
            self.generator_update_note.clear()
            self.generator_update_note.setVisible(False)
        if (
            had_result
            and self._selected_feature is not None
            and self._selected_feature.kind == "generator"
            and self._generator_model is not None
        ):
            self._set_selection(self._selected_feature)

    def _schedule_circuit_visibility_update(self, *args) -> None:  # noqa: ANN002
        del args
        self._circuit_visibility_timer.start()

    def _apply_circuit_visibility(self) -> None:
        controller = self._circuit_visibility
        bar_mask = None if controller is None else controller.bar_visible_mask
        segment_mask = (
            None if controller is None else controller.segment_visible_mask
        )
        segment_styles = (
            None if controller is None else controller.segment_style_indices
        )
        load_mask = (
            None
            if controller is None or self._load_model is None
            else controller.bar_visible_mask[self._load_model.bar_indices]
        )
        generator_mask = (
            None
            if controller is None or self._generator_model is None
            else controller.bar_visible_mask[self._generator_model.bar_indices]
        )
        equivalent_mask = None
        simplified = (
            self.simplified_network_action.isChecked()
            and self._equivalent_network_result is not None
            and controller is not None
        )
        if simplified:
            masks = self._equivalent_network_result.model.visibility_masks(
                controller.checked_states
            )
            bar_mask = masks.bar_mask
            segment_mask = masks.segment_mask
            load_mask = masks.source_load_mask
            generator_mask = masks.source_generator_mask
            equivalent_mask = masks.equivalent_load_mask
        colors = () if controller is None else controller.colors
        phase_mode = (
            self.phase_coloring_action.isChecked()
            and self._phase_classification is not None
        )
        self.view.set_feature_visibility_masks(bar_mask, segment_mask)
        self._effective_bar_mask = bar_mask
        self._effective_segment_mask = segment_mask
        self._effective_load_mask = load_mask
        self.virtualizer.set_visibility_mask(bar_mask)
        self.load_virtualizer.set_visibility_mask(load_mask)
        self.generator_virtualizer.set_visibility_mask(generator_mask)
        if self._equivalent_network_result is not None:
            self.equivalent_load_virtualizer.set_visibility_mask(equivalent_mask)
        if self._line_item is not None:
            if phase_mode:
                self._line_item.set_phase_rendering(
                    segment_mask,
                    self._phase_classification.style_indices,
                    PHASE_COLORS,
                )
            else:
                self._line_item.set_circuit_rendering(
                    segment_mask,
                    segment_styles,
                    colors,
                )
        if self._switch_item is not None:
            if phase_mode:
                self._switch_item.set_phase_rendering(
                    segment_mask,
                    self._phase_classification.style_indices,
                    PHASE_COLORS,
                )
            else:
                self._switch_item.set_circuit_rendering(
                    segment_mask,
                    segment_styles,
                    colors,
                )
        if self._regulator_item is not None:
            # O anel não tem cor por circuito nem por fase: só some junto com o
            # trecho que o hospeda.
            self._regulator_item.set_visibility_mask(segment_mask)
        self.phase_legend.setVisible(phase_mode and self._line_item is not None)
        if self.phase_legend.isVisible():
            self._position_phase_legend()

        selection = self._selected_feature
        if selection is not None and controller is not None:
            hidden = (
                selection.kind == "bar"
                and bar_mask is not None
                and not bool(bar_mask[selection.index])
            ) or (
                selection.kind == "segment"
                and segment_mask is not None
                and not bool(segment_mask[selection.index])
            ) or (
                selection.kind == "load"
                and load_mask is not None
                and not bool(load_mask[selection.index])
            ) or (
                selection.kind == "equivalent_load"
                and equivalent_mask is not None
                and not bool(equivalent_mask[selection.index])
            ) or (
                selection.kind == "generator"
                and generator_mask is not None
                and not bool(generator_mask[selection.index])
            )
            if hidden and not self._search_focus_active:
                self._set_selection(None)
        if self.search_palette.isVisible():
            self.search_palette.refresh_results()
        # O escopo do fluxo de potência são os circuitos visíveis; desmarcar
        # todos desabilita o botão.
        self._sync_power_flow_availability()
        self.view.viewport().update()

    def _show_curves_window(self) -> None:
        # Sempre disponível: sem curvas, a janela oferece a criação.
        self.curves_window.refresh()
        self.curves_window.show()
        self.curves_window.raise_()
        self.curves_window.activateWindow()

    def _show_patamares_window(self) -> None:
        self.patamares_window.refresh()
        self.patamares_window.show()
        self.patamares_window.raise_()
        self.patamares_window.activateWindow()

    def _on_calculation_levels_saved(
        self, schedule: CalculationLevelSchedule
    ) -> None:
        # Somente o sinal posterior ao replace atômico atualiza o retrato que
        # consumidores futuros consultarão.
        self._invalidate_generator_update()
        self.calculation_level_schedule = schedule
        self.statusBar().showMessage("4 patamares salvos.", 6_000)

    def _on_calculation_levels_reloaded(
        self, schedule: CalculationLevelSchedule
    ) -> None:
        if schedule != self.calculation_level_schedule:
            self._invalidate_generator_update()
        self.calculation_level_schedule = schedule

    def _on_circuit_calculation_levels_saved(
        self, _circuit_id: str, _schedule: CalculationLevelSchedule
    ) -> None:
        self._invalidate_generator_update()
        self.statusBar().showMessage(
            "Patamares do circuito salvos; atualize os geradores novamente.",
            6_000,
        )

    def _show_calculation_levels_load_warning(self) -> None:
        issue = self._calculation_levels_load.issue
        if issue is not None:
            QMessageBox.warning(self, "Patamares", issue)

    def _on_curves_saved(self, count: int) -> None:
        loaded = load_curves(self._curves_path)
        previous_result = self._generator_update_result
        self._saved_curves = loaded.curves
        if previous_result is not None:
            used = previous_result.model.curve
            replacement = next(
                (
                    curve
                    for curve in self._saved_curves
                    if curve.curve_id == used.curve_id
                ),
                None,
            )
            if replacement != used:
                self._invalidate_generator_update()
        self._sync_generator_update_availability()
        self._sync_export_availability()
        self.statusBar().showMessage(f"{count:n} curva(s) salva(s).", 6_000)

    def _show_curves_load_warning(self) -> None:
        issue = self._curves_load.issue
        if issue is None:
            return
        QMessageBox.warning(self, "Curvas", issue)

    def _show_circuits_window(self) -> None:
        if self._circuit_catalog is None:
            return
        self.circuits_window.show()
        self.circuits_window.raise_()
        self.circuits_window.activateWindow()

    def _show_overlap_report(self) -> None:
        if (
            self._circuit_catalog is None
            or self._circuit_catalog.overlapping_segment_indices.size == 0
        ):
            return
        self.overlap_report_window.show()
        self.overlap_report_window.raise_()
        self.overlap_report_window.activateWindow()

    def _show_cables_window(self) -> None:
        # Sempre disponível: sem catálogo, a janela oferece a importação.
        self.cables_window.refresh()
        self.cables_window.show()
        self.cables_window.raise_()
        self.cables_window.activateWindow()

    def _show_opendss_cables_window(self) -> None:
        self.opendss_cables_window.refresh()
        self.opendss_cables_window.show()
        self.opendss_cables_window.raise_()
        self.opendss_cables_window.activateWindow()

    def _show_opendss_geometries_window(self) -> None:
        self.opendss_geometries_window.refresh()
        self.opendss_geometries_window.show()
        self.opendss_geometries_window.raise_()
        self.opendss_geometries_window.activateWindow()

    def _sync_branches_availability(self) -> None:
        available = (
            self._circuit_catalog is not None
            and self._line_model is not None
            and self._phase_configuration is not None
            and self._import_thread is None
            and self._branch_thread is None
            and self._equivalent_thread is None
            and self._branch_json_thread is None
            and self._branch_csv_thread is None
            and self._power_flow_thread is None
            and self._generator_update_thread is None
        )
        self.branches_action.setEnabled(available)
        self.simplified_network_action.setEnabled(available)

    def _sync_export_availability(self) -> None:
        """Habilita cada exportação com os insumos exigidos por seu modo."""

        common_available = (
            self._model is not None
            and self._line_model is not None
            and self._switch_model is not None
            and self._circuit_catalog is not None
            and self._phase_configuration is not None
            and self._import_thread is None
            and self._export_thread is None
            and self._branch_json_thread is None
            and self._branch_csv_thread is None
            and self._generator_update_thread is None
        )
        self.opendss_export_action.setEnabled(
            common_available
            and (
                self._cable_model is not None
                or self._uses_opendss_library_parameters()
            )
        )
        # A projeção simplificada mantém integralmente o exportador original.
        self.simplified_opendss_export_action.setEnabled(
            common_available and self._cable_model is not None
        )
        allocation_base_available = (
            common_available
            and (
                self._cable_model is not None
                or self._uses_opendss_library_parameters()
            )
        )
        # Não deixe a ação cinza por insumos complementares: o clique explica
        # exatamente o que falta e onde obter, em vez de falhar em silêncio.
        self.opendss_allocation_export_action.setEnabled(allocation_base_available)
        missing = self._allocation_export_missing_requirements()
        if allocation_base_available and missing:
            self.opendss_allocation_export_action.setToolTip(
                "Clique para ver os dados pendentes: " + "; ".join(missing)
            )
        else:
            self.opendss_allocation_export_action.setToolTip(
                "Gerar quatro circuitos snapshot e alocar somente as cargas "
                "definidas pela energia agregada"
            )

    def _allocation_export_missing_requirements(self) -> tuple[str, ...]:
        """Insumos complementares ausentes, com instruções acionáveis."""

        missing: list[str] = []
        if self._allocation_model is None:
            missing.append(
                "agregados de energia do MDB (reimporte o banco com BT_ET, "
                "BT_CONS, BT_GERADOR_CONS, MT_CONS e MT_GERADOR_CONS)"
            )
        if (
            self._allocation_measurements is None
            or not self._allocation_measurements.available_indices
        ):
            missing.append(
                "correntes CODIGO/NPAT/ID/IE/IF (Arquivo > Importar CSV… > "
                "Importar correntes para alocação OpenDSS…)"
            )
        if not self._saved_curves:
            missing.append(
                "uma curva horária salva (Configurações > Curvas…)"
            )
        return tuple(missing)

    def _visible_circuit_indices(self) -> tuple[int, ...]:
        """Circuitos marcados na janela de circuitos, na ordem do catálogo.

        É o escopo do fluxo de potência: o que o usuário está vendo na tela é o
        que ele espera ver resolvido.
        """

        controller = self._circuit_visibility
        if controller is None:
            return ()
        return tuple(
            index
            for index, checked in enumerate(controller.checked_states)
            if checked
        )

    def _sync_power_flow_availability(self) -> None:
        """Exige tudo que a exportação exige, mais a lib e um circuito visível."""

        self.power_flow_action.setEnabled(
            power_flow_import_error() is None
            and self._model is not None
            and self._line_model is not None
            and self._switch_model is not None
            and self._circuit_catalog is not None
            and (
                self._cable_model is not None
                or self._uses_opendss_library_parameters()
            )
            and self._phase_configuration is not None
            and bool(self._visible_circuit_indices())
            and self._import_thread is None
            and self._branch_thread is None
            and self._equivalent_thread is None
            and self._branch_json_thread is None
            and self._branch_csv_thread is None
            and self._power_flow_thread is None
            and self._generator_update_thread is None
        )
        self._sync_generator_update_availability()

    def _sync_generator_update_availability(self) -> None:
        self.update_generators_action.setEnabled(
            self._generator_model is not None
            and self._circuit_catalog is not None
            and self._phase_configuration is not None
            and bool(self._saved_curves)
            and self._import_thread is None
            and self._branch_thread is None
            and self._equivalent_thread is None
            and self._branch_json_thread is None
            and self._branch_csv_thread is None
            and self._power_flow_thread is None
            and self._generator_update_thread is None
        )

    def _update_generators(self) -> None:
        generators = self._generator_model
        circuits = self._circuit_catalog
        configuration = self._phase_configuration
        if (
            self._busy()
            or generators is None
            or circuits is None
            or configuration is None
        ):
            return
        if not self.curves_window.confirm_pending_changes():
            return
        if not self.patamares_window.confirm_pending_changes():
            return
        if not self._saved_curves:
            QMessageBox.information(
                self,
                "Atualizar Geradores",
                "Cadastre e salve ao menos uma curva antes de atualizar os geradores.",
            )
            self._sync_generator_update_availability()
            return
        dialog = UpdateGeneratorsDialog(
            self._saved_curves,
            circuits,
            self.calculation_level_schedule,
            self._circuit_level_controller,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            curve = dialog.selected_curve()
            schedules = dialog.effective_schedules()
            modes = dialog.schedule_modes()
        except ValueError as exc:
            QMessageBox.warning(self, "Atualizar Geradores", str(exc))
            return
        self._start_generator_update(curve, schedules, modes)

    def _start_generator_update(
        self,
        curve: Curve,
        effective_schedules: tuple[CalculationLevelSchedule, ...],
        schedule_modes: tuple[GeneratorScheduleMode, ...],
    ) -> None:
        generators = self._generator_model
        circuits = self._circuit_catalog
        configuration = self._phase_configuration
        if generators is None or circuits is None or configuration is None:
            return
        thread = QThread(self)
        worker = GeneratorUpdateWorker(
            generators,
            circuits,
            configuration,
            curve,
            effective_schedules,
            tuple(schedule_modes),
        )
        worker.moveToThread(thread)

        progress = QProgressDialog(
            "Calculando demandas dos geradores…",
            "Cancelar",
            0,
            len(circuits) + len(generators),
            self,
        )
        progress.setWindowTitle("Atualizando geradores")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._generator_update_thread = thread
        self._generator_update_worker = worker
        self._generator_update_progress_dialog = progress
        self.import_action.setEnabled(False)
        self.mdb_import_action.setEnabled(False)
        self.cables_window.setEnabled(False)
        self.curves_window.setEnabled(False)
        self.patamares_window.setEnabled(False)
        self._sync_branches_availability()
        self._sync_export_availability()
        self._sync_power_flow_availability()

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_generator_update_progress)
        worker.finished.connect(self._on_generator_update_finished)
        worker.failed.connect(self._on_generator_update_failed)
        worker.cancelled.connect(self._on_generator_update_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_generator_update_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_generator_update_progress(self, current: int, total: int) -> None:
        if not self._is_current_signal_source(self._generator_update_worker):
            return
        progress = self._generator_update_progress_dialog
        maximum = max(total, 1)
        _update_progress_dialog(
            progress,
            minimum=0,
            maximum=maximum,
            label=f"Calculando demandas dos geradores… ({current:n}/{total:n})",
            value=min(current, maximum),
        )

    def _close_generator_update_progress(self) -> None:
        _close_progress_dialog(self._generator_update_progress_dialog)

    def _on_generator_update_finished(self, result: GeneratorUpdateResult) -> None:
        self._close_generator_update_progress()
        if (
            result.model.generators is not self._generator_model
            or result.model.circuits is not self._circuit_catalog
            or result.model.phase_configuration is not self._phase_configuration
        ):
            QMessageBox.critical(
                self,
                "Falha ao atualizar geradores",
                "Os geradores, circuitos ou a configuração de fases foram "
                "alterados durante o cálculo. O resultado anterior foi mantido.",
            )
            return
        self._set_generator_update_result(result)
        self._show_generator_update_report(result)

    def _on_generator_update_failed(self, reason: str) -> None:
        self._close_generator_update_progress()
        QMessageBox.critical(self, "Falha ao atualizar geradores", reason)

    def _on_generator_update_cancelled(self) -> None:
        self._close_generator_update_progress()
        self.statusBar().showMessage(
            "Atualização cancelada; o resultado anterior foi mantido.",
            5_000,
        )

    def _on_generator_update_thread_finished(self) -> None:
        if not self._is_current_signal_source(self._generator_update_thread):
            return
        progress = self._generator_update_progress_dialog
        self._generator_update_thread = None
        self._generator_update_worker = None
        self._generator_update_progress_dialog = None
        _close_progress_dialog(progress)
        self.cables_window.setEnabled(True)
        self.curves_window.setEnabled(True)
        self.patamares_window.setEnabled(True)
        self.import_action.setEnabled(True)
        self.mdb_import_action.setEnabled(self._mdb_error is None)
        self._sync_branches_availability()
        self._sync_export_availability()
        self._sync_power_flow_availability()
        if (
            self._pending_simplified_activation
            and self._generator_update_result is not None
            and not self._close_after_generator_update
        ):
            self._start_equivalent_build()
        if self._close_after_generator_update:
            self._close_after_generator_update = False
            self.close()

    def _show_generator_update_report(self, result: GeneratorUpdateResult) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{result.valid_generators:n} geradores atualizados com a curva "
                f'"{result.model.curve.name}".',
                6_000,
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Geradores atualizados com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(
            f"{result.valid_generators:n} de {result.total_generators:n} "
            "geradores foram atualizados."
        )
        message.setInformativeText(
            f"Curva: {result.model.curve.name}\n"
            f"Calculados: {result.valid_generators:n}\n"
            f"Omitidos: {result.invalid_generators:n}"
        )
        details = [
            f"Gerador {issue.generator_id}: {issue.reason}"
            for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _uses_opendss_library_parameters(self) -> bool:
        return (
            self._opendss_line_parameter_mode
            is OpenDssLineParameterMode.LIBRARY
        )

    def _on_opendss_library_inputs_saved(self, *_counts: int) -> None:
        """Invalida somente estudos que dependem do retrato salvo alterado."""

        if self._uses_opendss_library_parameters():
            self._invalidate_power_flow()

    def _invalidate_power_flow(self) -> None:
        """Descarta o resultado: ele deriva de todos os modelos importados."""

        if self._power_flow_worker is not None:
            self._power_flow_worker.cancel()
        self._power_flow_snapshot = None
        self._power_flow_result = None
        self._segment_power_flow_currents = None
        self._segment_power_flow_powers = None
        self._bar_power_flow_voltages = None
        self.segment_power_flow_model.clear()
        self.bar_power_flow_model.clear()
        self.segment_power_flow_section.setVisible(False)
        self.bar_power_flow_section.setVisible(False)
        self.regulator_tap_label.setVisible(False)
        self.regulator_tap_label.setText("")
        self.regulator_tap_table_model.clear()
        self.regulator_tap_table_title.setVisible(False)
        self.regulator_tap_table.setVisible(False)
        self._sync_power_flow_availability()

    def _exportable_loads(self) -> tuple[LoadModel, LoadPatternModel] | None:
        """Cargas e patamares aptos a gerar os arquivos de carga.

        Os dois são necessários: sem os quatro NPAT não há ``LoadShape`` para o
        ``daily`` das cargas apontar. A identidade garante que os patamares
        pertencem às cargas exibidas.
        """

        loads = self._load_model
        patterns = self._load_pattern_model
        if loads is None or patterns is None or patterns.loads is not loads:
            return None
        return loads, patterns

    def _exportable_generators(self) -> GeneratorUpdateModel | None:
        """Resultado vigente e coerente de ``Atualizar Geradores``."""

        result = self._generator_update_result
        if result is None:
            return None
        model = result.model
        if (
            model.generators is not self._generator_model
            or model.circuits is not self._circuit_catalog
            or model.phase_configuration is not self._phase_configuration
        ):
            return None
        return model

    def _expected_export_filenames(
        self,
        circuit_indices: tuple[int, ...],
    ) -> tuple[str, ...]:
        """Arquivos que a exportação vai gravar para esta seleção.

        Arquivos de carga exigem cargas e patamares; os de geradores exigem um
        resultado vigente de sua atualização. Só os grupos que serão gravados
        podem aparecer na confirmação de substituição. O master e as coordenadas
        dependem do circuito escolhido, por isso vêm de ``master_filenames``.
        """

        names = [LINES_FILENAME, SWITCHES_FILENAME]
        if self._uses_opendss_library_parameters():
            names[:0] = [
                CABOS_FILENAME,
                ARRANGEMENTS_FILENAME,
                LINE_GEOMETRIES_FILENAME,
            ]
        # Aproximação deliberada: só a exportação sabe quantos reguladores são
        # de fato exportáveis, e um modelo cujos reguladores fossem todos
        # descartados não geraria o arquivo. Perguntar por um arquivo que não
        # será tocado é inofensivo; deixar de perguntar por um que será, não.
        if self._regulator_model is not None:
            names.append(REGULATORS_FILENAME)
        if self._exportable_loads() is not None:
            names.extend(filename for _, filename, _ in _LOAD_EXPORT_FILES)
        if self._exportable_generators() is not None:
            names.extend(filename for _, filename, _ in _GENERATOR_EXPORT_FILES)
        catalog = self._circuit_catalog
        if catalog is not None:
            master = master_filenames(catalog, circuit_indices)
            if master is not None:
                names.extend(master)
        return tuple(names)

    def _export_opendss(self) -> None:
        catalog = self._circuit_catalog
        cables = self._cable_model
        configuration = self._phase_configuration
        if (
            catalog is None
            or (cables is None and not self._uses_opendss_library_parameters())
            or configuration is None
            or self._export_thread is not None
        ):
            return
        if (
            self._generator_model is not None
            and self._exportable_generators() is None
        ):
            answer = QMessageBox.question(
                self,
                "Exportar sem geradores",
                "Há geradores importados, mas eles ainda não possuem um "
                "resultado válido de ‘Atualizar Geradores’. Eles serão "
                "omitidos da exportação.\nDeseja continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        dialog = OpenDssExportDialog(catalog, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        circuit_indices = dialog.selected_circuit_indices()
        if not circuit_indices:
            return
        directory = QFileDialog.getExistingDirectory(
            self,
            "Escolher a pasta de destino da exportação",
        )
        if not directory:
            return
        # A pasta é escolhida uma vez só e recebe todos os arquivos gerados.
        destination = Path(directory)
        existing = [
            name
            for name in self._expected_export_filenames(circuit_indices)
            if (destination / name).exists()
        ]
        if existing:
            answer = QMessageBox.question(
                self,
                "Substituir arquivos",
                f"Já existem em {destination}: {', '.join(existing)}.\n"
                "Deseja substituí-los?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._start_opendss_export(destination, circuit_indices)

    def _export_opendss_allocation(self) -> None:
        catalog = self._circuit_catalog
        cables = self._cable_model
        configuration = self._phase_configuration
        allocations = self._allocation_model
        measurements = self._allocation_measurements
        if (
            catalog is None
            or configuration is None
            or self._export_thread is not None
            or (cables is None and not self._uses_opendss_library_parameters())
        ):
            return
        missing = self._allocation_export_missing_requirements()
        if missing:
            QMessageBox.warning(
                self,
                "Alocação por energia — dados pendentes",
                "A exportação não foi iniciada porque ainda faltam:\n\n• "
                + "\n• ".join(missing),
            )
            return
        assert allocations is not None
        assert measurements is not None
        dialog = OpenDssAllocationDialog(
            catalog,
            measurements,
            self._saved_curves,
            self.calculation_level_schedule,
            self._circuit_level_controller,
            self._opendss_allocation_settings,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            circuit_index = dialog.selected_circuit_index()
            curve = dialog.selected_curve()
            schedule = dialog.selected_schedule()
            settings = dialog.selected_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Escolhas inválidas", str(exc))
            return
        directory = QFileDialog.getExistingDirectory(
            self,
            "Escolher a pasta base da alocação OpenDSS",
        )
        if not directory:
            return
        destination = Path(directory)
        names = allocation_export_directory_names(
            catalog,
            circuit_index,
            schedule,
        )
        existing = [name for name in names if (destination / name).exists()]
        if existing:
            answer = QMessageBox.question(
                self,
                "Substituir circuitos de alocação",
                "As seguintes pastas já existem e serão substituídas "
                "somente após os quatro circuitos estarem prontos:\n"
                + "\n".join(existing)
                + "\n\nDeseja continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._opendss_allocation_settings = settings
        save_opendss_allocation_settings(self._settings, settings)
        self._start_opendss_allocation_export(
            destination,
            circuit_index,
            curve,
            schedule,
            settings,
        )

    def _start_opendss_allocation_export(
        self,
        destination: Path,
        circuit_index: int,
        curve: Curve,
        schedule: CalculationLevelSchedule,
        settings,  # noqa: ANN001 — validado pelo diálogo
    ) -> None:
        catalog = self._circuit_catalog
        configuration = self._phase_configuration
        allocations = self._allocation_model
        measurements = self._allocation_measurements
        if (
            catalog is None
            or configuration is None
            or allocations is None
            or measurements is None
        ):
            return
        use_library = self._uses_opendss_library_parameters()
        worker = OpenDssAllocationExportWorker(
            catalog,
            self._cable_model,
            configuration,
            circuit_index,
            allocations,
            measurements,
            curve,
            schedule,
            settings,
            regulators=self._regulator_model,
            load_settings=self._opendss_load_settings,
            line_parameter_mode=self._opendss_line_parameter_mode,
            library_catalog=(
                self.opendss_library_session.saved_catalog() if use_library else None
            ),
            library_mappings=(
                self.opendss_mapping_session.mappings if use_library else None
            ),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        progress = QProgressDialog(
            "Preparando quatro circuitos de alocação…",
            "Cancelar",
            0,
            100,
            self,
        )
        progress.setWindowTitle("Exportando alocação OpenDSS")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._export_thread = thread
        self._export_worker = worker
        self._export_progress_dialog = progress
        self._export_directory = destination
        self._sync_export_availability()

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_export_progress)
        worker.finished.connect(self._on_opendss_allocation_export_finished)
        worker.failed.connect(self._on_export_failed)
        worker.cancelled.connect(self._on_export_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_export_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_opendss_allocation_export_finished(
        self,
        result: OpenDssAllocationExportBundle,
    ) -> None:
        self._close_export_progress()
        destination = self._export_directory
        if destination is None:
            return
        try:
            paths = write_allocation_export(destination, result)
        except Exception as exc:  # noqa: BLE001 — rollback já ocorreu no núcleo
            QMessageBox.critical(
                self,
                "Falha na exportação de alocação",
                f"Nenhum conjunto parcial foi mantido em {destination}: {exc}",
            )
            return
        transformer_count = (
            result.levels[0].transformer_count if result.levels else 0
        )
        object_count = result.levels[0].load_count if result.levels else 0
        warning_count = (
            len(result.warnings)
            + len(result.network.issues)
            + result.network.omitted_issues
        )
        if result.has_warnings and warning_count == 0:
            warning_count = result.network.discarded_count
        warning_suffix = (
            ""
            if not result.has_warnings
            else f"; {warning_count:n} aviso(s)"
        )
        self.statusBar().showMessage(
            f"Quatro circuitos de alocação exportados para {destination}: "
            f"{transformer_count:n} transformador(es) e {object_count:n} "
            f"objetos Load por patamar{warning_suffix}.",
            10_000,
        )
        summary = (
            "Foram geradas quatro pastas autocontidas, sem executar o motor "
            "OpenDSS. Ao compilar cada Master, ele executará Solve, "
            "AllocateLoads e o Solve final."
        )
        if not result.has_warnings:
            QMessageBox.information(
                self,
                "Alocação OpenDSS exportada",
                summary + "\n\n" + "\n".join(str(path) for path in paths),
            )
            return

        message = QMessageBox(self)
        message.setWindowTitle("Alocação OpenDSS exportada com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(summary)
        message.setInformativeText(
            f"Transformadores exportados: {transformer_count:n}\n"
            f"Transformadores ignorados: {result.skipped_transformer_count:n}\n"
            f"Objetos Load por patamar: {object_count:n}\n\n"
            + "\n".join(str(path) for path in paths)
        )
        details = [*result.warnings]
        details.extend(
            f"Rede, elemento {issue.segment_id}: {issue.reason}"
            for issue in result.network.issues
        )
        if result.network.omitted_issues:
            details.append(
                f"… e mais {result.network.omitted_issues:n} ocorrências da rede."
            )
        if result.network.discarded_count and not result.network.issues:
            details.append(
                f"Rede: {result.network.discarded_count:n} elemento(s) descartado(s)."
            )
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _expected_simplified_export_filenames(
        self,
        circuit_indices: tuple[int, ...],
    ) -> tuple[str, ...]:
        names = [
            LINES_FILENAME,
            SWITCHES_FILENAME,
            SINGLE_PHASE_BRANCHES_FILENAME,
            TWO_PHASE_BRANCHES_FILENAME,
        ]
        if self._regulator_model is not None:
            names.append(REGULATORS_FILENAME)
        if self._exportable_loads() is not None:
            names.extend(filename for _, filename, _ in _LOAD_EXPORT_FILES)
        if self._exportable_generators() is not None:
            names.extend(filename for _, filename, _ in _GENERATOR_EXPORT_FILES)
        if self._circuit_catalog is not None:
            master = master_filenames(self._circuit_catalog, circuit_indices)
            if master is not None:
                names.extend(master)
        return tuple(names)

    def _export_simplified_opendss(self) -> None:
        catalog = self._circuit_catalog
        cables = self._cable_model
        configuration = self._phase_configuration
        branches = self._branch_analysis_result
        equivalent = self._equivalent_network_result
        if (
            catalog is None
            or cables is None
            or configuration is None
            or self._export_thread is not None
        ):
            return
        if branches is None:
            QMessageBox.information(
                self,
                "Rede simplificada não processada",
                "Execute primeiro Ferramentas → Ramais… para identificar os "
                "ramais que serão simplificados.",
            )
            return
        generator_updates = self._exportable_generators()
        if self._generator_model is not None and generator_updates is None:
            QMessageBox.information(
                self,
                "Atualize os geradores",
                "Há geradores importados. Execute primeiro Ferramentas → "
                "Atualizar Geradores… para incluí-los com segurança nos ramais.",
            )
            return
        if equivalent is None:
            self._pending_simplified_export = True
            self._pending_simplified_activation = True
            self._start_equivalent_build()
            return
        if (
            equivalent.model.branches is not branches
            or equivalent.model.catalog is not catalog
            or equivalent.model.source_loads is not self._load_model
            or equivalent.model.source_patterns is not self._load_pattern_model
            or equivalent.model.source_generator_updates is not generator_updates
        ):
            QMessageBox.information(
                self,
                "Rede simplificada desatualizada",
                "Os dados mudaram depois da simplificação. Execute novamente a "
                "ferramenta de ramais antes de exportar.",
            )
            return

        dialog = OpenDssExportDialog(catalog, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        circuit_indices = dialog.selected_circuit_indices()
        if len(circuit_indices) != 1:
            return
        directory = QFileDialog.getExistingDirectory(
            self,
            "Escolher a pasta base da exportação simplificada",
        )
        if not directory:
            return
        destination = Path(directory) / simplified_export_directory_name(
            catalog,
            circuit_indices[0],
        )
        existing = [
            name
            for name in self._expected_simplified_export_filenames(circuit_indices)
            if (destination / name).exists()
        ]
        if existing:
            answer = QMessageBox.question(
                self,
                "Substituir arquivos simplificados",
                f"Já existem em {destination}: {', '.join(existing)}.\n"
                "Deseja substituí-los?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._start_simplified_opendss_export(
            destination,
            circuit_indices,
            equivalent,
        )

    def _start_simplified_opendss_export(
        self,
        destination: Path,
        circuit_indices: tuple[int, ...],
        equivalent: EquivalentNetworkResult,
    ) -> None:
        catalog = self._circuit_catalog
        cables = self._cable_model
        configuration = self._phase_configuration
        if catalog is None or cables is None or configuration is None:
            return
        exportable_loads = self._exportable_loads()
        loads, patterns = exportable_loads or (self._load_model, None)
        worker = SimplifiedOpenDssExportWorker(
            catalog,
            cables,
            configuration,
            circuit_indices,
            equivalent,
            loads=loads,
            patterns=patterns,
            generator_updates=self._exportable_generators(),
            regulators=self._regulator_model,
            load_settings=self._opendss_load_settings,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        progress = QProgressDialog(
            "Gerando a rede simplificada em OpenDSS…",
            "Cancelar",
            0,
            100,
            self,
        )
        progress.setWindowTitle("Exportando rede simplificada")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._export_thread = thread
        self._export_worker = worker
        self._export_progress_dialog = progress
        self._export_directory = destination
        self._sync_export_availability()

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_export_progress)
        worker.finished.connect(self._on_simplified_opendss_export_finished)
        worker.failed.connect(self._on_export_failed)
        worker.cancelled.connect(self._on_export_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_export_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_simplified_opendss_export_finished(
        self,
        result: SimplifiedOpenDssExportBundle,
    ) -> None:
        self._close_export_progress()
        destination = self._export_directory
        if destination is None:
            return
        try:
            destination.mkdir(parents=True, exist_ok=True)
            for filename, content in result.files:
                (destination / filename).write_text(content, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Falha na exportação simplificada",
                f"Não foi possível gravar em {destination}: {exc.strerror or exc}",
            )
            return
        self._show_simplified_export_report(result, destination)

    def _show_simplified_export_report(
        self,
        result: SimplifiedOpenDssExportBundle,
        destination: Path,
    ) -> None:
        equivalent_count = sum(
            branch_result.exported_count
            for _, branch_result in result.branches_by_phase_count
        )
        zero_count = sum(
            branch_result.zero_count
            for _, branch_result in result.branches_by_phase_count
        )
        outside_loads = sum(
            load_result.exported_count
            for _, load_result in result.loads_by_phase_count
        )
        outside_generators = sum(
            generator_result.exported_count
            for _, generator_result in result.generators_by_phase_count
        )
        summary = (
            f"Rede simplificada exportada: {result.lines.exported_count:n} "
            f"trechos, {result.switches.exported_count:n} chaves, "
            f"{outside_loads:n} cargas externas, {outside_generators:n} "
            f"geradores externos e {equivalent_count:n} ramais equivalentes"
        )
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{summary} em {destination}; {zero_count:n} ramal(is) zerado(s) omitido(s).",
                8_000,
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Exportação simplificada concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(summary + ".")
        message.setInformativeText(
            f"Pasta: {destination}\nRamais zerados omitidos: {zero_count:n}"
        )
        details = [
            f"{issue.segment_id}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _start_opendss_export(
        self,
        destination: Path,
        circuit_indices: tuple[int, ...],
    ) -> None:
        catalog = self._circuit_catalog
        cables = self._cable_model
        configuration = self._phase_configuration
        if (
            catalog is None
            or (cables is None and not self._uses_opendss_library_parameters())
            or configuration is None
        ):
            return
        exportable_loads = self._exportable_loads()
        generator_updates = self._exportable_generators()
        thread = QThread(self)
        # Os modelos são imutáveis: o worker guarda as próprias referências e o
        # arquivo sai como um retrato consistente do estado atual, mesmo que uma
        # importação substitua os modelos enquanto a exportação corre.
        loads, patterns = exportable_loads or (None, None)
        library_catalog = (
            self.opendss_library_session.saved_catalog()
            if self._uses_opendss_library_parameters()
            else None
        )
        library_mappings = (
            self.opendss_mapping_session.mappings
            if self._uses_opendss_library_parameters()
            else None
        )
        worker = OpenDssExportWorker(
            catalog,
            cables,
            configuration,
            circuit_indices,
            loads=loads,
            patterns=patterns,
            generator_updates=generator_updates,
            regulators=self._regulator_model,
            load_settings=self._opendss_load_settings,
            line_parameter_mode=self._opendss_line_parameter_mode,
            library_catalog=library_catalog,
            library_mappings=library_mappings,
        )
        worker.moveToThread(thread)

        progress = QProgressDialog(
            "Gerando os arquivos .dss…",
            "Cancelar",
            0,
            100,
            self,
        )
        progress.setWindowTitle("Exportando para OpenDSS")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._export_thread = thread
        self._export_worker = worker
        self._export_progress_dialog = progress
        self._export_directory = destination
        self._sync_export_availability()

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_export_progress)
        worker.finished.connect(self._on_opendss_export_finished)
        worker.failed.connect(self._on_export_failed)
        worker.cancelled.connect(self._on_export_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_export_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_export_progress(self, current: int, total: int) -> None:
        if not self._is_current_signal_source(self._export_worker):
            return
        progress = self._export_progress_dialog
        if total <= 0:
            _update_progress_dialog(progress, minimum=0, maximum=0)
            return
        _update_progress_dialog(
            progress,
            minimum=0,
            maximum=total,
            label=f"Gerando os arquivos .dss… ({current:n}/{total:n})",
            value=min(current, total),
        )

    def _close_export_progress(self) -> None:
        _close_progress_dialog(self._export_progress_dialog)

    def _on_opendss_export_finished(self, result: OpenDssExportBundle) -> None:
        self._close_export_progress()
        destination = self._export_directory
        if destination is None:
            return
        for filename, text in result.files:
            target = destination / filename
            try:
                target.write_text(text, encoding="utf-8")
            except OSError as exc:
                QMessageBox.critical(
                    self,
                    "Falha na exportação",
                    f"Não foi possível gravar {target}: {exc.strerror or exc}",
                )
                return
        self._show_opendss_export_report(result, destination)

    def _on_export_failed(self, reason: str) -> None:
        self._close_export_progress()
        QMessageBox.critical(self, "Falha na exportação", reason)

    def _on_export_cancelled(self) -> None:
        self._close_export_progress()
        self.statusBar().showMessage(
            "Exportação cancelada; nenhum arquivo foi gravado.",
            5_000,
        )

    def _on_export_thread_finished(self) -> None:
        if not self._is_current_signal_source(self._export_thread):
            return
        progress = self._export_progress_dialog
        self._export_thread = None
        self._export_worker = None
        self._export_progress_dialog = None
        self._export_directory = None
        _close_progress_dialog(progress)
        self._sync_export_availability()
        if self._close_after_export:
            self._close_after_export = False
            self.close()

    def _show_opendss_export_report(
        self,
        result: OpenDssExportBundle,
        destination: Path,
    ) -> None:
        captions = {count: caption for count, _, caption in _LOAD_EXPORT_FILES}
        generator_captions = {
            count: caption for count, _, caption in _GENERATOR_EXPORT_FILES
        }
        regulators = result.regulators
        parts = [
            f"{result.lines.exported_count:n} trechos",
            f"{result.switches.exported_count:n} chaves",
            *(
                ()
                if regulators is None or not regulators.exported_count
                else (f"{regulators.exported_count:n} reguladores",)
            ),
            *(
                f"{load_result.exported_count:n} cargas {captions[count]}"
                for count, load_result in result.loads_by_phase_count
            ),
            *(
                f"{generator_result.exported_count:n} geradores "
                f"{generator_captions[count]}"
                for count, generator_result in result.generators_by_phase_count
            ),
        ]
        summary = ", ".join(parts[:-1]) + f" e {parts[-1]}"
        # O número na tela precisa ser rastreável até a configuração que o
        # produziu; sem os limites ativos a menção só faria ruído.
        limits = (
            ""
            if self._opendss_load_settings.is_default
            else f" ({self._opendss_settings_summary(self._opendss_load_settings)})"
        )
        library_result = result.library
        library_file_summaries = (
            ()
            if library_result is None
            else (
                f"{CABOS_FILENAME} ({library_result.cable_count:n} cabo(s))",
                f"{ARRANGEMENTS_FILENAME} "
                f"({library_result.arrangement_count:n} arranjo(s))",
                f"{LINE_GEOMETRIES_FILENAME} "
                f"({library_result.line_geometry_count:n} geometria(s))",
            )
        )
        library_notice = (
            ""
            if not library_file_summaries
            else " Arquivos de biblioteca: "
            + ", ".join(library_file_summaries)
            + "."
        )
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{summary} exportados para {destination}.{limits}{library_notice}",
                5_000,
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Exportação concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{summary} foram exportados.")
        lines = [
            f"Pasta: {destination}",
            *(
                ()
                if self._opendss_load_settings.is_default
                else (self._opendss_settings_summary(self._opendss_load_settings),)
            ),
            *(
                ()
                if library_result is None
                else (
                    f"{CABOS_FILENAME}: {library_result.cable_count:n} cabo(s)",
                    f"{ARRANGEMENTS_FILENAME}: "
                    f"{library_result.arrangement_count:n} arranjo(s)",
                    f"{LINE_GEOMETRIES_FILENAME}: "
                    f"{library_result.line_geometry_count:n} geometria(s)",
                )
            ),
            f"{LINES_FILENAME}: {result.lines.exported_count:n} trechos, "
            f"{result.lines.discarded_count:n} descartados",
            f"{SWITCHES_FILENAME}: {result.switches.exported_count:n} chaves "
            f"({result.switches.open_count:n} abertas), "
            f"{result.switches.discarded_count:n} descartadas",
            *(
                ()
                if regulators is None
                else (
                    f"{REGULATORS_FILENAME}: "
                    f"{regulators.exported_count:n} reguladores "
                    f"({regulators.exported_count * 3:n} transformadores), "
                    f"{regulators.discarded_count:n} descartados",
                )
            ),
        ]
        filenames = {count: filename for count, filename, _ in _LOAD_EXPORT_FILES}
        for count, load_result in result.loads_by_phase_count:
            lines.append(
                f"{filenames[count]}: {load_result.exported_count:n} cargas "
                f"{captions[count]} "
                f"({load_result.skipped_other_phase_count:n} de outras fases "
                f"ignoradas), "
                f"{load_result.discarded_count:n} descartadas"
            )
        generator_filenames = {
            count: filename for count, filename, _ in _GENERATOR_EXPORT_FILES
        }
        for count, generator_result in result.generators_by_phase_count:
            lines.append(
                f"{generator_filenames[count]}: "
                f"{generator_result.exported_count:n} geradores "
                f"{generator_captions[count]} "
                f"({generator_result.skipped_other_phase_count:n} de outras "
                "fases ignorados), "
                f"{generator_result.discarded_count:n} descartados"
            )
        master = result.master
        if master is not None and master.text:
            lines.append(
                f"{master.master_filename}: circuito, chamadas e solve; "
                f"{master.buscoords_filename} com {master.bus_count:n} barras"
            )
        elif master is not None:
            lines.append("Arquivo master não gerado; veja os detalhes.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"{issue.segment_id}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _run_power_flow(self) -> None:
        """Resolve o fluxo de potência dos circuitos visíveis.

        Ao contrário da exportação, esta operação entra na exclusão mútua com
        importações e análises: o resultado volta para o estado da aplicação, e
        não para o disco, então precisa dos mesmos modelos do início ao fim.
        """

        catalog = self._circuit_catalog
        cables = self._cable_model
        configuration = self._phase_configuration
        if (
            catalog is None
            or (cables is None and not self._uses_opendss_library_parameters())
            or configuration is None
            or self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
            or self._power_flow_thread is not None
            or self._generator_update_thread is not None
        ):
            return
        circuit_indices = self._visible_circuit_indices()
        if not circuit_indices:
            self.statusBar().showMessage(
                "Marque ao menos um circuito para executar o fluxo de potência.",
                5_000,
            )
            return
        exportable_loads = self._exportable_loads()
        loads, patterns = exportable_loads or (None, None)
        generator_updates = self._exportable_generators()
        missing_parts: list[str] = []
        if exportable_loads is None:
            missing_parts.append(
                "cargas e patamares não estão ambos importados; as cargas "
                "de consumo serão omitidas"
            )
        if self._generator_model is not None and generator_updates is None:
            missing_parts.append(
                "há geradores importados sem resultado válido de ‘Atualizar "
                "Geradores’; eles serão omitidos"
            )
        if missing_parts:
            answer = QMessageBox.question(
                self,
                "Executar com dados incompletos",
                "O fluxo será executado com as seguintes limitações:\n\n- "
                + "\n- ".join(missing_parts)
                + "\n\nDeseja continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        library_catalog = (
            self.opendss_library_session.saved_catalog()
            if self._uses_opendss_library_parameters()
            else None
        )
        library_mappings = (
            self.opendss_mapping_session.mappings
            if self._uses_opendss_library_parameters()
            else None
        )
        thread = QThread(self)
        worker = PowerFlowWorker(
            catalog,
            cables,
            configuration,
            circuit_indices,
            loads=loads,
            patterns=patterns,
            generator_updates=generator_updates,
            regulators=self._regulator_model,
            load_settings=self._opendss_load_settings,
            line_parameter_mode=self._opendss_line_parameter_mode,
            library_catalog=library_catalog,
            library_mappings=library_mappings,
        )
        worker.moveToThread(thread)

        progress = QProgressDialog(
            "Resolvendo o fluxo de potência…",
            "Cancelar",
            0,
            len(circuit_indices),
            self,
        )
        progress.setWindowTitle("Fluxo de potência")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._power_flow_thread = thread
        self._power_flow_worker = worker
        self._power_flow_progress_dialog = progress
        self._power_flow_snapshot = (
            catalog,
            cables,
            configuration,
            loads,
            patterns,
            generator_updates,
            self._opendss_line_parameter_mode,
        )
        self.import_action.setEnabled(False)
        self._sync_power_flow_availability()
        self._sync_branches_availability()

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_power_flow_progress)
        worker.finished.connect(self._on_power_flow_finished)
        worker.failed.connect(self._on_power_flow_failed)
        worker.cancelled.connect(self._on_power_flow_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_power_flow_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_power_flow_progress(self, current: int, total: int) -> None:
        if not self._is_current_signal_source(self._power_flow_worker):
            return
        progress = self._power_flow_progress_dialog
        maximum = max(1, int(total))
        _update_progress_dialog(
            progress,
            maximum=maximum,
            label=f"Resolvendo o fluxo de potência… {current:n}/{total:n} patamares",
            value=min(int(current), maximum),
        )

    def _close_power_flow_progress(self) -> None:
        _close_progress_dialog(self._power_flow_progress_dialog)

    def _on_power_flow_finished(self, result: PowerFlowResult) -> None:
        self._close_power_flow_progress()
        # Revalidação por identidade, como nas demais análises: uma reimportação
        # durante a execução torna o resultado um retrato de dados que já não
        # estão na tela.
        loads, patterns = self._exportable_loads() or (None, None)
        generator_updates = self._exportable_generators()
        current = (
            self._circuit_catalog,
            self._cable_model,
            self._phase_configuration,
            loads,
            patterns,
            generator_updates,
            self._opendss_line_parameter_mode,
        )
        snapshot = self._power_flow_snapshot
        # Resultados produzidos antes da inclusão dos geradores (e alguns
        # doubles de teste) possuem o retrato histórico de cinco modelos. Eles
        # continuam válidos somente quando não há resultado de geradores.
        if snapshot is not None and len(snapshot) == 5:
            snapshot = (*snapshot, None)
        if snapshot is not None and len(snapshot) == 6:
            snapshot = (*snapshot, OpenDssLineParameterMode.ORIGINAL)
        if snapshot is None or any(
            expected is not actual
            for expected, actual in zip(snapshot, current, strict=True)
        ):
            self.statusBar().showMessage(
                "Os dados mudaram durante a execução; o resultado do fluxo de "
                "potência foi descartado.",
                5_000,
            )
            return
        self._power_flow_result = result
        # A seleção corrente precisa ser reaplicada para o painel refletir o
        # resultado que acabou de chegar.
        if self._selected_feature is not None:
            self._set_selection(
                self._selected_feature,
                reveal_hidden=self._search_focus_active,
                preserve_branch=True,
            )
        self._show_power_flow_report(result)

    def _on_power_flow_failed(self, reason: str) -> None:
        self._close_power_flow_progress()
        QMessageBox.critical(self, "Falha no fluxo de potência", reason)

    def _on_power_flow_cancelled(self) -> None:
        self._close_power_flow_progress()
        self.statusBar().showMessage(
            "Fluxo de potência cancelado; nenhum resultado foi alterado.",
            5_000,
        )

    def _on_power_flow_thread_finished(self) -> None:
        if not self._is_current_signal_source(self._power_flow_thread):
            return
        progress = self._power_flow_progress_dialog
        self._power_flow_thread = None
        self._power_flow_worker = None
        self._power_flow_progress_dialog = None
        _close_progress_dialog(progress)
        self.import_action.setEnabled(True)
        self._sync_power_flow_availability()
        self._sync_branches_availability()
        if self._close_after_power_flow:
            self._close_after_power_flow = False
            self.close()

    def _show_power_flow_report(self, result: PowerFlowResult) -> None:
        summary = (
            f"{len(result.solved_circuits):n} circuito(s) resolvido(s): "
            f"{len(result.segment_currents):n} trechos com corrente e "
            f"{len(result.bar_voltages):n} barras com tensão"
            + (
                ""
                if result.generator_updates is None
                else f"; {result.exported_generators:n} gerador(es) no modelo"
            )
        )
        if not result.has_warnings:
            limits = (
                ""
                if self._opendss_load_settings.is_default
                else f" {self._opendss_settings_summary(self._opendss_load_settings)}"
            )
            self.statusBar().showMessage(f"{summary}.{limits}", 6_000)
            return
        message = QMessageBox(self)
        message.setWindowTitle("Fluxo de potência concluído com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{summary}.")
        lines = [
            f"Patamares por circuito: {result.step_count:n}",
            *(
                ()
                if result.generator_updates is None
                else (
                    f"Geradores exportados: {result.exported_generators:n}; "
                    f"descartados: {result.discarded_generators:n}",
                )
            ),
            *(
                ()
                if self._opendss_load_settings.is_default
                else (self._opendss_settings_summary(self._opendss_load_settings),)
            ),
        ]
        if result.skipped_circuits:
            lines.append(
                "Circuitos não resolvidos: "
                + ", ".join(result.skipped_circuits)
            )
        if result.unconverged:
            lines.append(
                f"{len(result.unconverged):n} patamar(es) não convergiram: "
                + ", ".join(
                    f"{circuit_id} (NPAT {step})"
                    for circuit_id, step in result.unconverged[:10]
                )
            )
        message.setInformativeText("\n".join(lines))
        details = [
            f"{issue.element_id}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _sync_load_layout(self) -> None:
        equivalent_model = (
            None
            if self._equivalent_network_result is None
            else self._equivalent_network_result.model
        )
        show_equivalent = (
            self.simplified_network_action.isChecked()
            and equivalent_model is not None
        )
        candidates = (
            (self._load_model, None),
            (self._generator_model, None),
            (
                equivalent_model if show_equivalent else None,
                (
                    None
                    if not show_equivalent or equivalent_model is None
                    else tuple(
                        not record.is_zero for record in equivalent_model.records
                    )
                ),
            ),
        )
        models = tuple(model for model, _ in candidates if model is not None)
        layout_masks = tuple(mask for model, mask in candidates if model is not None)
        if not models:
            return
        layouts = load_layout_offsets_for_models(models, layout_masks)
        layout_index = 0
        if self._load_model is not None:
            self.load_virtualizer.set_layout_offsets(*layouts[layout_index])
            layout_index += 1
        if self._generator_model is not None:
            self.generator_virtualizer.set_layout_offsets(*layouts[layout_index])
            layout_index += 1
        if show_equivalent:
            self.equivalent_load_virtualizer.set_layout_offsets(
                *layouts[layout_index]
            )

    def _invalidate_equivalent_network(
        self,
        *,
        keep_requested: bool = False,
    ) -> None:
        rebuild_after_cancel = bool(
            self._equivalent_worker is not None
            and self._branch_analysis_result is not None
            and (keep_requested or self.branches_window.isVisible())
        )
        self._restart_equivalent_after_finish = rebuild_after_cancel
        if self._equivalent_worker is not None:
            self._equivalent_worker.cancel()
        self._pending_branch_metrics = bool(
            self._branch_analysis_result is not None
            and self.branches_window.isVisible()
        )
        self._pending_simplified_export = False
        if (
            self._selected_feature is not None
            and self._selected_feature.kind == "equivalent_load"
        ):
            self._set_selection(None)
        self._equivalent_snapshot = None
        self._equivalent_network_result = None
        self.branches_window.set_equivalent_result(None)
        self.branches_window.set_equivalent_pending(
            self._pending_branch_metrics
        )
        self.view.set_equivalent_load_model(None)
        self.equivalent_load_virtualizer.reset_model(None)
        self.equivalent_load_virtualizer.set_loads_visible(False)
        self.equivalent_pattern_table_model.set_records(())
        self.equivalent_patterns_section.setVisible(False)
        self._pending_simplified_activation = bool(keep_requested)
        if not keep_requested:
            blocker = QSignalBlocker(self.simplified_network_action)
            self.simplified_network_action.setChecked(False)
            del blocker
        self._sync_load_layout()
        self._apply_circuit_visibility()

    def _invalidate_branch_analysis(self) -> None:
        self._invalidate_equivalent_network()
        self._restart_equivalent_after_finish = False
        if self._branch_worker is not None:
            self._branch_worker.cancel()
        self._branch_analysis_snapshot = None
        self._branch_analysis_result = None
        self._selected_branch = None
        self.branch_highlight_overlay.clear()
        self.branches_window.set_result(None)
        self.branches_window.hide()
        self._pending_branch_metrics = False
        self.branches_window.set_equivalent_pending(False)
        self._sync_branches_availability()

    def _show_or_analyze_branches(self) -> None:
        self._show_branches_after_analysis = True
        self._pending_branch_metrics = True
        self._start_branch_analysis()

    def _start_branch_analysis(self) -> None:
        if self._branch_analysis_result is not None:
            if self._show_branches_after_analysis:
                self._show_branches_window()
            if self._equivalent_network_result is None:
                self._start_equivalent_build()
            return
        if (
            self._circuit_catalog is None
            or self._line_model is None
            or self._phase_configuration is None
            or self._import_thread is not None
            or self._branch_thread is not None
            or self._equivalent_thread is not None
        ):
            return

        catalog = self._circuit_catalog
        phase_configuration = self._phase_configuration
        loads = self._load_model
        thread = QThread(self)
        worker = BranchAnalysisWorker(catalog, phase_configuration, loads)
        worker.moveToThread(thread)
        progress = QProgressDialog(
            "Analisando circuitos…",
            "Cancelar",
            0,
            len(catalog),
            self,
        )
        progress.setWindowTitle("Análise de ramais")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._branch_thread = thread
        self._branch_worker = worker
        self._branch_progress_dialog = progress
        self._branch_analysis_snapshot = (
            catalog,
            phase_configuration,
            loads,
        )
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.simplified_network_action.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_branch_analysis_progress)
        worker.finished.connect(self._on_branch_analysis_finished)
        worker.failed.connect(self._on_branch_analysis_failed)
        worker.cancelled.connect(self._on_branch_analysis_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(lambda: worker.cancel())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_branch_analysis_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_branch_analysis_progress(self, current: int, total: int) -> None:
        if not self._is_current_signal_source(self._branch_worker):
            return
        progress = self._branch_progress_dialog
        maximum = max(1, int(total))
        _update_progress_dialog(
            progress,
            maximum=maximum,
            label=f"Analisando circuitos… {current:n}/{total:n}",
            value=min(int(current), maximum),
        )

    def _close_branch_progress(self) -> None:
        _close_progress_dialog(self._branch_progress_dialog)

    def _on_branch_analysis_finished(self, result: BranchAnalysisResult) -> None:
        self._close_branch_progress()
        snapshot = self._branch_analysis_snapshot
        current_snapshot = (
            self._circuit_catalog,
            self._phase_configuration,
            self._load_model,
        )
        if snapshot is None or any(
            expected is not current
            for expected, current in zip(snapshot, current_snapshot, strict=True)
        ):
            self.statusBar().showMessage(
                "A análise foi descartada porque os dados foram substituídos.",
                5_000,
            )
            return
        self._branch_analysis_result = result
        self.branches_window.set_result(result)
        if (
            not self._close_after_branch_analysis
            and self._show_branches_after_analysis
        ):
            self._show_branches_window()
            self.statusBar().showMessage(
                f"Análise concluída: {len(result.records):n} ramal(is).",
                5_000,
            )

    def _on_branch_analysis_failed(self, message: str) -> None:
        self._close_branch_progress()
        if self._pending_simplified_activation:
            self._cancel_simplified_request()
        if not self._close_after_branch_analysis:
            QMessageBox.critical(
                self,
                "Falha na análise de ramais",
                message,
            )

    def _on_branch_analysis_cancelled(self) -> None:
        self._close_branch_progress()
        if self._pending_simplified_activation:
            self._cancel_simplified_request()
        if not self._close_after_branch_analysis:
            self.statusBar().showMessage("Análise de ramais cancelada.", 5_000)

    def _on_branch_analysis_thread_finished(self) -> None:
        if not self._is_current_signal_source(self._branch_thread):
            return
        progress = self._branch_progress_dialog
        self._branch_thread = None
        self._branch_worker = None
        self._branch_progress_dialog = None
        self._branch_analysis_snapshot = None
        _close_progress_dialog(progress)
        self.import_action.setEnabled(True)
        self._sync_branches_availability()
        self._sync_power_flow_availability()
        if (
            (
                self._pending_simplified_activation
                or self._pending_simplified_export
                or self._pending_branch_metrics
            )
            and self._branch_analysis_result is not None
            and not self._close_after_branch_analysis
        ):
            self._start_equivalent_build()
        if self._close_after_branch_analysis:
            self._close_after_branch_analysis = False
            self.close()

    def _cancel_simplified_request(self) -> None:
        self._pending_simplified_activation = False
        blocker = QSignalBlocker(self.simplified_network_action)
        self.simplified_network_action.setChecked(False)
        del blocker

    def _set_simplified_network_enabled(self, enabled: bool) -> None:
        if not enabled:
            self._pending_simplified_activation = False
            if (
                self._equivalent_worker is not None
                and not self._pending_branch_metrics
                and not self._pending_simplified_export
            ):
                self._equivalent_worker.cancel()
            if (
                self._selected_feature is not None
                and self._selected_feature.kind == "equivalent_load"
            ):
                self._set_selection(None)
            self.equivalent_load_virtualizer.set_loads_visible(False)
            self._sync_load_layout()
            self._apply_circuit_visibility()
            self.statusBar().showMessage("Rede original restaurada.", 4_000)
            return
        if (
            self._circuit_catalog is None
            or self._line_model is None
            or self._phase_configuration is None
        ):
            self._cancel_simplified_request()
            return
        if self._equivalent_network_result is not None:
            self._activate_simplified_network()
            return
        self._pending_simplified_activation = True
        if self._branch_analysis_result is None:
            answer = QMessageBox.question(
                self,
                "Analisar ramais",
                "A rede simplificada precisa identificar os ramais. "
                "Deseja iniciar a análise agora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._cancel_simplified_request()
                return
            self._show_branches_after_analysis = False
            self._start_branch_analysis()
            return
        self._start_equivalent_build()

    def _start_equivalent_build(self) -> None:
        if (
            not (
                self._pending_simplified_activation
                or self._pending_simplified_export
                or self._pending_branch_metrics
            )
            or self._branch_analysis_result is None
            or self._equivalent_thread is not None
            or self._import_thread is not None
            or self._branch_thread is not None
        ):
            return
        if not self._branch_analysis_result.records:
            self._pending_branch_metrics = False
            self.branches_window.set_equivalent_pending(False)
            self._pending_simplified_export = False
            self._cancel_simplified_request()
            self.statusBar().showMessage(
                "Nenhum ramal foi identificado para simplificar.",
                5_000,
            )
            return
        branches = self._branch_analysis_result
        loads = self._load_model
        patterns = self._load_pattern_model
        generator_updates = self._exportable_generators()
        if self._generator_model is not None and generator_updates is None:
            self._pending_branch_metrics = False
            self.branches_window.set_equivalent_pending(False)
            self._pending_simplified_export = False
            self._cancel_simplified_request()
            QMessageBox.information(
                self,
                "Atualize os geradores",
                "A rede simplificada precisa das potências vigentes dos "
                "geradores. Execute primeiro Ferramentas → Atualizar Geradores…",
            )
            return
        thread = QThread(self)
        worker = EquivalentNetworkWorker(
            branches,
            loads,
            patterns,
            generator_updates,
        )
        worker.moveToThread(thread)
        progress = QProgressDialog(
            "Construindo cargas equivalentes…",
            "Cancelar",
            0,
            len(branches.records),
            self,
        )
        progress.setWindowTitle("Rede simplificada")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._equivalent_thread = thread
        self._equivalent_worker = worker
        self._equivalent_progress_dialog = progress
        self._equivalent_snapshot = (
            branches,
            loads,
            patterns,
            generator_updates,
        )
        self.branches_window.set_equivalent_pending(True)
        self.import_action.setEnabled(False)
        self.branches_action.setEnabled(False)
        self.simplified_network_action.setEnabled(False)

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_equivalent_progress)
        worker.finished.connect(self._on_equivalent_finished)
        worker.failed.connect(self._on_equivalent_failed)
        worker.cancelled.connect(self._on_equivalent_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_equivalent_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_equivalent_progress(self, current: int, total: int) -> None:
        if not self._is_current_signal_source(self._equivalent_worker):
            return
        progress = self._equivalent_progress_dialog
        maximum = max(1, int(total))
        _update_progress_dialog(
            progress,
            maximum=maximum,
            label=f"Construindo cargas equivalentes… {current:n}/{total:n}",
            value=min(int(current), maximum),
        )

    def _close_equivalent_progress(self) -> None:
        _close_progress_dialog(self._equivalent_progress_dialog)

    def _on_equivalent_finished(self, result: EquivalentNetworkResult) -> None:
        self._close_equivalent_progress()
        current_snapshot = (
            self._branch_analysis_result,
            self._load_model,
            self._load_pattern_model,
            self._exportable_generators(),
        )
        snapshot = self._equivalent_snapshot
        if snapshot is None or any(
            expected is not current
            for expected, current in zip(snapshot, current_snapshot, strict=True)
        ):
            self.statusBar().showMessage(
                "A rede equivalente foi descartada porque os dados mudaram.",
                5_000,
            )
            return
        self._equivalent_network_result = result
        self._restart_equivalent_after_finish = False
        self._pending_branch_metrics = False
        self.branches_window.set_equivalent_pending(False)
        self.branches_window.set_equivalent_result(result)
        self.view.set_equivalent_load_model(result.model)
        self.equivalent_load_virtualizer.reset_model(result.model)
        self._activate_simplified_network()

    def _on_equivalent_failed(self, message: str) -> None:
        self._close_equivalent_progress()
        self._restart_equivalent_after_finish = False
        self._pending_branch_metrics = False
        self.branches_window.set_equivalent_pending(False)
        self._pending_simplified_export = False
        self._cancel_simplified_request()
        if not self._close_after_equivalent_build:
            QMessageBox.critical(
                self,
                "Falha na rede simplificada",
                message,
            )

    def _on_equivalent_cancelled(self) -> None:
        self._close_equivalent_progress()
        if self._restart_equivalent_after_finish:
            self.statusBar().showMessage(
                "Reiniciando o cálculo das demandas dos ramais…",
                3_000,
            )
            return
        self._pending_branch_metrics = False
        self.branches_window.set_equivalent_pending(False)
        self._pending_simplified_export = False
        self._cancel_simplified_request()
        if not self._close_after_equivalent_build:
            self.statusBar().showMessage(
                "Construção da rede simplificada cancelada.",
                5_000,
            )

    def _on_equivalent_thread_finished(self) -> None:
        if not self._is_current_signal_source(self._equivalent_thread):
            return
        progress = self._equivalent_progress_dialog
        self._equivalent_thread = None
        self._equivalent_worker = None
        self._equivalent_progress_dialog = None
        self._equivalent_snapshot = None
        _close_progress_dialog(progress)
        restart = bool(
            self._restart_equivalent_after_finish
            and self._branch_analysis_result is not None
            and not self._close_after_equivalent_build
        )
        self._restart_equivalent_after_finish = False
        self.branches_window.set_equivalent_pending(
            restart and self._pending_branch_metrics
        )
        self.import_action.setEnabled(True)
        self._sync_branches_availability()
        self._sync_power_flow_availability()
        pending_export = self._pending_simplified_export
        resume_export = (
            pending_export
            and self._equivalent_network_result is not None
            and not self._close_after_equivalent_build
        )
        self._pending_simplified_export = False
        if pending_export:
            self._pending_simplified_activation = False
        if resume_export:
            QTimer.singleShot(0, self._export_simplified_opendss)
        elif restart:
            QTimer.singleShot(0, self._start_equivalent_build)
        if self._close_after_equivalent_build:
            self._close_after_equivalent_build = False
            self.close()

    def _activate_simplified_network(self) -> None:
        result = self._equivalent_network_result
        if result is None or not self.simplified_network_action.isChecked():
            return
        self._pending_simplified_activation = False
        self._sync_load_layout()
        self.equivalent_load_virtualizer.set_loads_visible(
            self.show_loads_action.isChecked()
        )
        self._apply_circuit_visibility()
        self.load_virtualizer.refresh(force=True)
        self.equivalent_load_virtualizer.refresh(force=True)
        issue_count = len(result.issues) + result.omitted_issue_count
        equivalent_count = sum(
            not record.is_zero for record in result.model.records
        )
        zero_count = len(result.model) - equivalent_count
        suffix = (
            ""
            if issue_count == 0
            else f"; {issue_count:n} diagnóstico(s) de agregação"
        )
        if zero_count:
            suffix += f"; {zero_count:n} ramal(is) zerado(s) omitido(s)"
        self.statusBar().showMessage(
            f"Rede simplificada ativa: {equivalent_count:n} carga(s) "
            f"equivalente(s){suffix}.",
            8_000,
        )

    def _show_branches_window(self) -> None:
        if self._branch_analysis_result is None:
            return
        self.branches_window.show()
        self.branches_window.raise_()
        self.branches_window.activateWindow()
        if self._equivalent_network_result is None:
            self._pending_branch_metrics = True
            self._start_equivalent_build()

    def _export_visible_branches_csv(self) -> None:
        branches = self._branch_analysis_result
        equivalent = self._equivalent_network_result
        if (
            branches is None
            or self._equivalent_thread is not None
            or self._branch_csv_thread is not None
            or self._branch_json_thread is not None
        ):
            return
        if equivalent is not None and equivalent.model.branches is not branches:
            equivalent = None
        branch_indices = (
            self.branches_window.visible_source_rows_in_display_order()
        )
        if not branch_indices:
            return
        suggested = suggested_branch_csv_filename(
            self.branches_window.selected_circuit_id()
        )
        path, _ = QFileDialog.getSaveFileName(
            self.branches_window,
            "Exportar tabela de ramais para CSV",
            str(Path.cwd() / suggested),
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".csv")
        self._start_branch_csv_export(target, branch_indices)

    def _start_branch_csv_export(
        self,
        target: Path,
        branch_indices: tuple[int, ...],
    ) -> None:
        branches = self._branch_analysis_result
        equivalent = self._equivalent_network_result
        if (
            branches is None
            or self._branch_csv_thread is not None
            or self._branch_json_thread is not None
        ):
            return
        if equivalent is not None and equivalent.model.branches is not branches:
            equivalent = None
        thread = QThread(self)
        worker = BranchCsvExportWorker(
            str(target),
            branches,
            equivalent,
            branch_indices,
        )
        worker.moveToThread(thread)
        progress = QProgressDialog(
            "Exportando tabela de ramais para CSV…",
            "Cancelar",
            0,
            len(branch_indices),
            self,
        )
        progress.setWindowTitle("Exportar ramais")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._branch_csv_thread = thread
        self._branch_csv_worker = worker
        self._branch_csv_progress_dialog = progress
        self.branches_window.set_csv_export_pending(True)
        self.import_action.setEnabled(False)
        self.mdb_import_action.setEnabled(False)
        self._sync_branches_availability()
        self._sync_export_availability()
        self._sync_power_flow_availability()

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_branch_csv_progress)
        worker.finished.connect(self._on_branch_csv_finished)
        worker.failed.connect(self._on_branch_csv_failed)
        worker.cancelled.connect(self._on_branch_csv_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_branch_csv_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_branch_csv_progress(self, current: int, total: int) -> None:
        if not self._is_current_signal_source(self._branch_csv_worker):
            return
        progress = self._branch_csv_progress_dialog
        maximum = max(1, int(total))
        _update_progress_dialog(
            progress,
            minimum=0,
            maximum=maximum,
            label=f"Exportando ramais para CSV… {current:n}/{total:n}",
            value=min(int(current), maximum),
        )

    def _close_branch_csv_progress(self) -> None:
        _close_progress_dialog(self._branch_csv_progress_dialog)

    def _on_branch_csv_finished(self, result: BranchCsvExportResult) -> None:
        self._close_branch_csv_progress()
        self.statusBar().showMessage(
            f"{result.branch_count:n} ramal(is) exportado(s) para {result.path}.",
            8_000,
        )

    def _on_branch_csv_failed(self, error: object) -> None:
        self._close_branch_csv_progress()
        QMessageBox.critical(
            self,
            "Falha na exportação CSV",
            str(error) or type(error).__name__,
        )

    def _on_branch_csv_cancelled(self) -> None:
        self._close_branch_csv_progress()
        self.statusBar().showMessage(
            "Exportação CSV cancelada; nenhum arquivo foi alterado.",
            5_000,
        )

    def _on_branch_csv_thread_finished(self) -> None:
        if not self._is_current_signal_source(self._branch_csv_thread):
            return
        progress = self._branch_csv_progress_dialog
        self._branch_csv_thread = None
        self._branch_csv_worker = None
        self._branch_csv_progress_dialog = None
        _close_progress_dialog(progress)
        self.branches_window.set_csv_export_pending(False)
        self.import_action.setEnabled(True)
        self.mdb_import_action.setEnabled(self._mdb_error is None)
        self._sync_branches_availability()
        self._sync_export_availability()
        self._sync_power_flow_availability()
        if self._close_after_branch_csv_export:
            self._close_after_branch_csv_export = False
            self.close()

    def _export_visible_branches_json(self) -> None:
        branches = self._branch_analysis_result
        equivalent = self._equivalent_network_result
        if (
            branches is None
            or equivalent is None
            or equivalent.model.branches is not branches
            or self._branch_json_thread is not None
            or self._branch_csv_thread is not None
        ):
            return
        branch_indices = self.branches_window.visible_source_rows()
        if not branch_indices:
            return
        interest_branch_ids = (
            self.branches_window.interest_branch_ids_for_source_rows(
                branch_indices
            )
        )
        suggested = suggested_branch_json_filename(
            self.branches_window.selected_circuit_id()
        )
        path, _ = QFileDialog.getSaveFileName(
            self.branches_window,
            "Exportar ramais para JSON",
            str(Path.cwd() / suggested),
            "Arquivos JSON (*.json);;Todos os arquivos (*)",
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".json")
        self._start_branch_json_export(
            target,
            branch_indices,
            interest_branch_ids,
        )

    def _start_branch_json_export(
        self,
        target: Path,
        branch_indices: tuple[int, ...],
        interest_branch_ids: tuple[int, ...] = (),
    ) -> None:
        branches = self._branch_analysis_result
        equivalent = self._equivalent_network_result
        if (
            branches is None
            or equivalent is None
            or self._branch_json_thread is not None
            or self._branch_csv_thread is not None
        ):
            return
        thread = QThread(self)
        worker = BranchJsonExportWorker(
            str(target),
            branches,
            equivalent,
            branch_indices,
            interest_branch_ids,
        )
        worker.moveToThread(thread)
        progress = QProgressDialog(
            "Exportando ramais para JSON…",
            "Cancelar",
            0,
            len(branch_indices),
            self,
        )
        progress.setWindowTitle("Exportar ramais")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        self._branch_json_thread = thread
        self._branch_json_worker = worker
        self._branch_json_progress_dialog = progress
        self.branches_window.set_json_export_pending(True)
        self.import_action.setEnabled(False)
        self.mdb_import_action.setEnabled(False)
        self._sync_branches_availability()
        self._sync_export_availability()
        self._sync_power_flow_availability()

        thread.started.connect(worker.run)
        worker.progress.connect(self._on_branch_json_progress)
        worker.finished.connect(self._on_branch_json_finished)
        worker.failed.connect(self._on_branch_json_failed)
        worker.cancelled.connect(self._on_branch_json_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        progress.canceled.connect(worker.cancel)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_branch_json_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_branch_json_progress(self, current: int, total: int) -> None:
        if not self._is_current_signal_source(self._branch_json_worker):
            return
        progress = self._branch_json_progress_dialog
        maximum = max(1, int(total))
        _update_progress_dialog(
            progress,
            minimum=0,
            maximum=maximum,
            label=f"Exportando ramais para JSON… {current:n}/{total:n}",
            value=min(int(current), maximum),
        )

    def _close_branch_json_progress(self) -> None:
        _close_progress_dialog(self._branch_json_progress_dialog)

    def _on_branch_json_finished(self, result: BranchJsonExportResult) -> None:
        self._close_branch_json_progress()
        self.statusBar().showMessage(
            f"{result.branch_count:n} ramal(is) exportado(s) para {result.path}.",
            8_000,
        )

    def _on_branch_json_failed(self, error: object) -> None:
        self._close_branch_json_progress()
        if isinstance(error, BranchJsonValidationError):
            message = QMessageBox(self)
            message.setWindowTitle("Não foi possível exportar os ramais")
            message.setIcon(QMessageBox.Icon.Warning)
            message.setText(
                f"Foram encontrados {len(error.issues):n} elemento(s) sem CODIGO."
            )
            message.setInformativeText(
                "Nenhum arquivo foi criado. Corrija os dados de origem e tente novamente."
            )
            message.setDetailedText("\n".join(error.issues))
            message.exec()
            return
        QMessageBox.critical(
            self,
            "Falha na exportação JSON",
            str(error) or type(error).__name__,
        )

    def _on_branch_json_cancelled(self) -> None:
        self._close_branch_json_progress()
        self.statusBar().showMessage(
            "Exportação JSON cancelada; nenhum arquivo foi alterado.",
            5_000,
        )

    def _on_branch_json_thread_finished(self) -> None:
        if not self._is_current_signal_source(self._branch_json_thread):
            return
        progress = self._branch_json_progress_dialog
        self._branch_json_thread = None
        self._branch_json_worker = None
        self._branch_json_progress_dialog = None
        _close_progress_dialog(progress)
        self.branches_window.set_json_export_pending(False)
        self.import_action.setEnabled(True)
        self.mdb_import_action.setEnabled(self._mdb_error is None)
        self._sync_branches_availability()
        self._sync_export_availability()
        self._sync_power_flow_availability()
        if self._close_after_branch_json_export:
            self._close_after_branch_json_export = False
            self.close()

    def _clear_branch_highlight(self, *, clear_table: bool = False) -> None:
        self._selected_branch = None
        self.branch_highlight_overlay.clear()
        if clear_table:
            self.branches_window.clear_selection()
        self.view.viewport().update()

    def _select_branch(self, record: BranchRecord) -> None:
        if (
            self._branch_analysis_result is None
            or self._line_model is None
            or self._circuit_catalog is None
            or not any(
                candidate is record
                for candidate in self._branch_analysis_result.records
            )
        ):
            return
        self._set_selection(None, preserve_branch=True)
        if self.search_palette.isVisible():
            self.search_palette.close_palette()
        reactivated = False
        if (
            self._circuit_visibility is not None
            and not self._circuit_visibility.is_visible(record.circuit_index)
        ):
            reactivated = self.circuit_table_model.setData(
                self.circuit_table_model.index(record.circuit_index, 0),
                Qt.CheckState.Checked,
                Qt.ItemDataRole.CheckStateRole,
            )
            self._circuit_visibility_timer.stop()
            self._apply_circuit_visibility()
        self._selected_branch = record
        self.branch_highlight_overlay.bind(
            self._line_model,
            record.segment_indices,
        )
        if reactivated:
            self.statusBar().showMessage(
                f"Circuito {record.circuit_id} reativado para exibir o ramal.",
                5_000,
            )
        self.view.viewport().update()

    def _activate_branch(self, record: BranchRecord) -> None:
        self._select_branch(record)
        if self._selected_branch is not record:
            return
        try:
            self.view.focus_segments(record.segment_indices)
        except (IndexError, ValueError):
            self._invalidate_branch_analysis()
            self.statusBar().showMessage(
                "O ramal não está mais disponível após a substituição dos dados.",
                5_000,
            )
            return
        self.virtualizer.refresh(force=True)
        self.load_virtualizer.refresh(force=True)
        self.equivalent_load_virtualizer.refresh(force=True)
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_import_report(self, result: CsvLoadResult) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{result.valid_rows:n} barras importadas com sucesso.", 5_000
            )
            return

        message = QMessageBox(self)
        message.setWindowTitle("Importação concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{result.valid_rows:n} barras foram importadas.")
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Linhas válidas: {result.valid_rows:n}",
            f"Linhas ignoradas: {result.invalid_rows:n}",
        ]
        if result.applied_scale != 1.0:
            lines.append(
                f"Coordenadas divididas por {result.applied_scale:n} para metros."
            )
        if result.crs_warning is not None:
            lines.append(result.crs_warning)
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_segment_import_report(self, result: SegmentLoadResult) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{result.valid_rows:n} trechos importados com sucesso.", 5_000
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Importação de trechos concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{result.valid_rows:n} trechos foram importados.")
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Linhas válidas: {result.valid_rows:n}",
            f"Linhas ignoradas: {result.invalid_rows:n}",
        ]
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_load_import_report(self, result: LoadCsvResult) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{result.valid_rows:n} cargas importadas com sucesso.", 5_000
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Importação de cargas concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{result.valid_rows:n} cargas foram importadas.")
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Linhas válidas: {result.valid_rows:n}",
            f"Linhas ignoradas: {result.invalid_rows:n}",
        ]
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_load_pattern_import_report(
        self,
        result: LoadPatternCsvResult,
    ) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"Patamares importados para {len(result.model):n} cargas "
                f"({result.valid_rows:n} registros).",
                5_000,
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Importação de patamares concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(
            f"Patamares de {len(result.model):n} cargas foram importados."
        )
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Linhas válidas: {result.valid_rows:n}",
            f"Linhas ignoradas: {result.invalid_rows:n}",
        ]
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_generator_import_report(self, result: GeneratorCsvResult) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{result.valid_rows:n} geradores importados com sucesso.", 5_000
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Importação de geradores concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{result.valid_rows:n} geradores foram importados.")
        lines = [
            f"MT_GERADOR_CONS: {result.generator_encoding}; "
            f"{result.generator_total_rows:n} linhas",
            f"MT_CONS: {result.consumer_encoding}; "
            f"{result.consumer_total_rows:n} linhas",
            f"Geradores válidos: {result.valid_rows:n}",
            f"Geradores ignorados: {result.invalid_rows:n}",
        ]
        if (
            result.generator_encoding.lower() == "cp1252"
            or result.consumer_encoding.lower() == "cp1252"
        ):
            lines.append("Ao menos um arquivo foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"{issue.source}, linha {issue.line_number}: {issue.reason}"
            for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_switch_import_report(
        self,
        result: SwitchLoadResult,
        topology_warnings: tuple[str, ...] = (),
    ) -> None:
        if not result.has_warnings and not topology_warnings:
            self.statusBar().showMessage(
                f"{result.valid_rows:n} chaves importadas com sucesso.", 5_000
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Importação de chaves concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{result.valid_rows:n} chaves foram importadas.")
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Linhas válidas: {result.valid_rows:n}",
            f"Linhas ignoradas: {result.invalid_rows:n}",
        ]
        if topology_warnings:
            lines.append(f"Avisos de topologia: {len(topology_warnings):n}")
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        details.extend(
            f"Topologia: {warning}" for warning in topology_warnings[:200]
        )
        if len(topology_warnings) > 200:
            details.append(
                f"… e mais {len(topology_warnings) - 200:n} avisos de topologia."
            )
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_regulator_import_report(self, result: RegulatorLoadResult) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{result.valid_rows:n} reguladores importados com sucesso.",
                5_000,
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Importação de reguladores concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{result.valid_rows:n} reguladores foram importados.")
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Linhas válidas: {result.valid_rows:n}",
            f"Linhas ignoradas: {result.invalid_rows:n}",
        ]
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_cable_import_report(self, result: CableCsvResult) -> None:
        if not result.has_warnings:
            message = f"{result.valid_rows:n} cabos importados com sucesso."
            if result.ignored_type_rows:
                message += f" {result.ignored_type_rows:n} ignorados por TIPO ≠ 1."
            self.statusBar().showMessage(message, 5_000)
            return
        message = QMessageBox(self)
        message.setWindowTitle("Importação de cabos concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{result.valid_rows:n} cabos foram importados.")
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Linhas válidas: {result.valid_rows:n}",
            f"Linhas ignoradas: {result.invalid_rows:n}",
        ]
        if result.ignored_type_rows:
            lines.append(f"Ignorados por TIPO ≠ 1: {result.ignored_type_rows:n}")
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_circuit_import_report(self, result: CircuitLoadResult) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"{result.valid_rows:n} circuitos importados com sucesso.", 5_000
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Importação de circuitos concluída com avisos")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"{result.valid_rows:n} circuitos foram importados.")
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Linhas válidas: {result.valid_rows:n}",
            f"Linhas ignoradas: {result.invalid_rows:n}",
            f"Avisos de topologia: {len(result.model.topology_warnings):n}",
        ]
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências no CSV.")
        topology_warnings = result.model.topology_warnings
        details.extend(
            f"Topologia: {warning}" for warning in topology_warnings[:200]
        )
        if len(topology_warnings) > 200:
            details.append(
                f"… e mais {len(topology_warnings) - 200:n} avisos de topologia."
            )
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _show_circuit_level_import_report(
        self, result: CircuitLevelCsvResult
    ) -> None:
        if not result.has_warnings:
            self.statusBar().showMessage(
                f"Patamares importados para {result.valid_rows:n} circuitos.",
                5_000,
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle(
            "Importação de patamares dos circuitos concluída com avisos"
        )
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(
            f"Patamares de {result.valid_rows:n} circuitos foram importados."
        )
        lines = [
            f"Codificação: {result.encoding}",
            f"Linhas de dados: {result.total_rows:n}",
            f"Circuitos válidos: {result.valid_rows:n}",
            f"Circuitos ignorados: {result.invalid_rows:n}",
        ]
        if result.encoding.lower() == "cp1252":
            lines.append("O arquivo não era UTF-8 e foi lido como CP-1252.")
        message.setInformativeText("\n".join(lines))
        details = [
            f"Linha {issue.line_number}: {issue.reason}" for issue in result.issues
        ]
        if result.omitted_issues:
            details.append(f"… e mais {result.omitted_issues:n} ocorrências.")
        if details:
            message.setDetailedText("\n".join(details))
        message.exec()

    def _on_import_failed(self, reason: str) -> None:
        _close_progress_dialog(self._progress_dialog)
        QMessageBox.critical(self, "Falha na importação", reason)

    def _on_import_cancelled(self) -> None:
        _close_progress_dialog(self._progress_dialog)
        self.statusBar().showMessage("Importação cancelada; os dados anteriores foram mantidos.", 5_000)

    def _on_import_thread_finished(self) -> None:
        if not self._is_current_signal_source(self._import_thread):
            return
        progress = self._progress_dialog
        self._import_thread = None
        self._import_worker = None
        self._progress_dialog = None
        _close_progress_dialog(progress)
        self.import_action.setEnabled(True)
        self.mdb_import_action.setEnabled(self._mdb_error is None)
        self.patamares_window.setEnabled(True)
        self._sync_branches_availability()
        self._sync_export_availability()
        self._sync_power_flow_availability()
        if (
            self._pending_simplified_activation
            and self._branch_analysis_result is not None
            and not self._close_after_import
        ):
            self._start_equivalent_build()
        if self._close_after_import:
            self._close_after_import = False
            self.close()

    def _fit_all(self) -> None:
        if self._model is None:
            return
        if (
            self.simplified_network_action.isChecked()
            and self._equivalent_network_result is not None
            and self._circuit_visibility is not None
        ):
            masks = self._equivalent_network_result.model.visibility_masks(
                self._circuit_visibility.checked_states
            )
            self.view.fit_visible_features(masks.bar_mask, masks.segment_mask)
        else:
            self.view.fit_model()
        self.virtualizer.refresh(force=True)
        self.load_virtualizer.refresh(force=True)
        self.equivalent_load_virtualizer.refresh(force=True)

    def _show_phase_configuration_error(self) -> None:
        if self._phase_configuration_error is None:
            return
        QMessageBox.warning(
            self,
            "Configuração de fases indisponível",
            f"O modo de coloração por fases foi desabilitado.\n\n"
            f"Arquivo: {self.phase_configuration_path}\n\n"
            f"{self._phase_configuration_error}",
        )

    def _set_phase_coloring_enabled(self, enabled: bool) -> None:
        if enabled and self._phase_classification is None:
            self.phase_coloring_action.setChecked(False)
            return
        self._apply_circuit_visibility()
        if not enabled or self._phase_classification is None:
            self.statusBar().showMessage(
                "Coloração dos trechos por circuito restaurada.",
                4_000,
            )
            return
        classification = self._phase_classification
        if classification.unmapped_count:
            values = ", ".join(classification.unmapped_values[:5])
            remaining = len(classification.unmapped_values) - 5
            suffix = f" e mais {remaining:n}" if remaining > 0 else ""
            self.statusBar().showMessage(
                f"Modo por fases ativo: {classification.unmapped_count:n} trechos "
                f"sem relação (FASES2: {values}{suffix}).",
                8_000,
            )
        else:
            self.statusBar().showMessage(
                "Modo por fases ativo: todos os trechos foram reconhecidos.",
                5_000,
            )

    @staticmethod
    def _is_google_satellite_provider(provider: Provedor) -> bool:
        return provider in (PROVEDOR_GOOGLE_SAT, PROVEDOR_GOOGLE_HIBRIDO)

    def _authorize_google_satellite(self) -> bool:
        if self._google_satellite_authorized:
            return True
        answer = QMessageBox.warning(
            self,
            "Provedor Google não oficial",
            "Este provedor utiliza um endpoint de tiles Google não oficial. "
            "O uso pode não ser adequado para distribuição comercial e deve "
            "respeitar os termos do provedor.\n\n"
            "Deseja autorizar os provedores Google durante esta sessão?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._google_satellite_authorized = True
        return True

    def _select_satellite_provider(self, provider: Provedor) -> None:
        previous = self._satellite_provider
        if provider is previous:
            return
        if (
            self._is_google_satellite_provider(provider)
            and not self._authorize_google_satellite()
        ):
            self.satellite_provider_actions[previous].setChecked(True)
            return
        self._satellite_provider = provider
        if self.satellite_action.isChecked():
            self._create_satellite_manager(provider)
        self.statusBar().showMessage(
            f"Provedor de satélite: {provider.nome}.", 4_000
        )

    def _set_theme(self, theme: AppTheme) -> None:
        """Aplica e memoriza o tema escolhido manualmente pelo usuário."""

        if theme is self._theme:
            return
        self._theme = theme
        save_theme_preference(self._settings, theme)
        action = self.theme_actions.get(theme)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme)
        self.statusBar().showMessage(
            f"Tema {THEME_LABELS[theme].lower()} aplicado.", 4_000
        )

    def _show_opendss_settings(self) -> None:
        """Coleta e memoriza os parâmetros globais aplicados às cargas.

        Um resultado de fluxo calculado com a faixa anterior é descartado: ele
        continuaria no painel como se descrevesse o modelo novo, que é o modo de
        falha mais difícil de perceber neste recurso.
        """

        dialog = OpenDssSettingsDialog(
            self._opendss_load_settings,
            self,
            mappings=self.opendss_mapping_session.mappings,
            cable_names=self.opendss_library_session.saved_cable_names,
            arrangement_names=self.opendss_library_session.saved_arrangement_names,
            cable_map_issue=self.opendss_mapping_session.cable_issue,
            arrangement_map_issue=self.opendss_mapping_session.arrangement_issue,
            line_parameter_mode=self._opendss_line_parameter_mode,
        )
        dialog.cable_map_editor.saveRequested.connect(
            lambda entries: self._save_single_opendss_map(
                dialog.cable_map_editor,
                entries,
                map_kind="cables",
            )
        )
        dialog.arrangement_map_editor.saveRequested.connect(
            lambda entries: self._save_single_opendss_map(
                dialog.arrangement_map_editor,
                entries,
                map_kind="arrangements",
            )
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.settings()
        chosen_mode = dialog.line_parameter_mode()
        chosen_maps = dialog.mappings()
        load_changed = chosen != self._opendss_load_settings
        mode_changed = chosen_mode is not self._opendss_line_parameter_mode
        maps_changed = (
            chosen_maps != self.opendss_mapping_session.mappings
            or self.opendss_mapping_session.cable_issue is not None
            or self.opendss_mapping_session.arrangement_issue is not None
        )
        if not load_changed and not mode_changed and not maps_changed:
            return
        try:
            if maps_changed:
                self.opendss_mapping_session.save_maps(chosen_maps)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Falha ao salvar mapas OpenDSS", str(exc))
            return
        if load_changed:
            self._opendss_load_settings = chosen
            save_opendss_settings(self._settings, chosen)
        if mode_changed:
            self._opendss_line_parameter_mode = chosen_mode
            save_opendss_line_parameter_mode(self._settings, chosen_mode)
            self._sync_export_availability()
        if load_changed or mode_changed:
            self._invalidate_power_flow()
        messages: list[str] = []
        if load_changed:
            messages.append(self._opendss_settings_summary(chosen))
        if mode_changed:
            mode_label = (
                "biblioteca de cabos e arranjos"
                if chosen_mode is OpenDssLineParameterMode.LIBRARY
                else "parâmetros originais"
            )
            messages.append(f"Parâmetros das linhas: {mode_label}.")
        if maps_changed:
            messages.append(
                "Mapas OpenDSS: "
                f"{len(chosen_maps.cables):n} cabo(s) e "
                f"{len(chosen_maps.arrangements):n} arranjo(s)."
            )
        self.statusBar().showMessage(" ".join(messages), 6_000)

    def _save_single_opendss_map(
        self,
        editor,  # noqa: ANN001
        entries,  # noqa: ANN001
        *,
        map_kind: str,
    ) -> None:
        try:
            if map_kind == "cables":
                self.opendss_mapping_session.save_cable_map(entries)
                label = "cabos"
            else:
                self.opendss_mapping_session.save_arrangement_map(entries)
                label = "arranjos"
        except (OSError, ValueError) as exc:
            editor.mark_save_failed(str(exc))
            QMessageBox.warning(self, "Falha ao salvar mapa OpenDSS", str(exc))
            return
        editor.mark_saved(entries)
        self.statusBar().showMessage(
            f"Mapa OpenDSS de {label} salvo: {len(entries):n} vínculo(s).",
            6_000,
        )

    @staticmethod
    def _opendss_settings_summary(settings: OpenDssLoadSettings) -> str:
        if settings.is_default:
            return (
                "Limites de tensão das cargas desativados; o OpenDSS usará "
                "0,95 e 1,05."
            )
        return (
            "Limites de tensão das cargas: "
            f"vminpu={settings.vminpu:n} e vmaxpu={settings.vmaxpu:n}."
        )

    def _create_satellite_manager(self, provider: Provedor) -> None:
        manager = GerenciadorTiles(provider, parent=self.view)
        manager.tile_pronto.connect(self.view.viewport().update)
        manager.falha_tiles.connect(self._show_satellite_download_failure)
        self.view.set_tile_manager(manager)

    def _show_satellite_download_failure(self, reason: str) -> None:
        self._show_satellite_failure(
            f"não foi possível baixar os tiles ({reason}); verifique a conexão"
        )

    def _show_satellite_failure(self, reason: str) -> None:
        self.statusBar().showMessage(
            f"Imagem de satélite indisponível: {reason}.",
            8_000,
        )

    def _set_satellite_enabled(self, enabled: bool) -> None:
        if enabled:
            if (
                self._is_google_satellite_provider(self._satellite_provider)
                and not self._authorize_google_satellite()
            ):
                blocker = QSignalBlocker(self.satellite_action)
                self.satellite_action.setChecked(False)
                del blocker
                return
            manager = self.view.tile_manager
            if (
                manager is None
                or manager.provedor is not self._satellite_provider
            ):
                self._create_satellite_manager(self._satellite_provider)
        if not enabled:
            message = "Imagem de satélite desativada."
        elif self._model is None:
            # Sem barras não há zona/hemisfério UTM para georreferenciar os
            # tiles; a camada só passa a desenhar depois da importação.
            message = (
                "Imagem de satélite ativada; o fundo aparecerá após importar "
                "as barras (referência UTM)."
            )
        else:
            message = "Imagem de satélite ativada."
        self.view.set_satellite_enabled(enabled)
        self.statusBar().showMessage(message, 4_000)

    def _position_phase_legend(self) -> None:
        if not self.phase_legend.isVisible():
            return
        self.phase_legend.adjustSize()
        viewport = self.view.viewport()
        self.phase_legend.move(
            12,
            max(12, viewport.height() - self.phase_legend.height() - 12),
        )
        self.phase_legend.raise_()

    def _schedule_viewport_overlay_update(self) -> None:
        self._overlay_position_timer.start()

    def _position_viewport_overlays(self) -> None:
        self._position_phase_legend()

    def _show_zoom_limit_reached(self) -> None:
        self.statusBar().showMessage("Limite máximo de zoom atingido.", 3_000)

    def _sync_search_availability(self) -> None:
        available = self.search_index.entity_count > 0
        self.search_action.setEnabled(available)
        if self.search_palette.isVisible():
            if available:
                self.search_palette.refresh_results()
            else:
                self.search_palette.close_palette()

    def _show_search_palette(self) -> None:
        if not self.search_action.isEnabled():
            return
        if self._search_focus_active:
            self._set_selection(None)
        self.search_palette.open()

    def _is_search_result_hidden(self, result: SearchResult) -> bool:
        target = result.target
        controller = self._circuit_visibility
        if target.kind == "bar":
            if not self.view.bars_visible:
                return True
            return self._effective_bar_mask is not None and not bool(
                self._effective_bar_mask[target.index]
            )
        if target.kind == "load":
            if not self.load_virtualizer.loads_visible or self._load_model is None:
                return True
            return self._effective_load_mask is not None and not bool(
                self._effective_load_mask[target.index]
            )
        return self._effective_segment_mask is not None and not bool(
            self._effective_segment_mask[target.index]
        )

    def _activate_search_result(self, result: SearchResult) -> None:
        target = result.target
        try:
            if target.kind == "bar":
                self.view.focus_bar(target.index)
            elif target.kind == "load":
                self.view.focus_load(target.index)
            else:
                self.view.focus_segment(target.index)
        except IndexError:
            self.statusBar().showMessage(
                "O elemento não está mais disponível após a última importação.",
                5_000,
            )
            self.search_palette.refresh_results()
            return

        self._set_selection(target, reveal_hidden=True)
        self.virtualizer.refresh(force=True)
        self.load_virtualizer.refresh(force=True)
        self.details_dock.show()
        if result.kind == "switch":
            self.details_dock.setWindowTitle("Chave selecionada")
            QTimer.singleShot(
                0,
                lambda: self.segment_details_page.ensureWidgetVisible(
                    self.switch_details_section
                ),
            )
        elif result.kind == "regulator":
            self.details_dock.setWindowTitle("Regulador selecionado")
            QTimer.singleShot(
                0,
                lambda: self.segment_details_page.ensureWidgetVisible(
                    self.regulator_details_section
                ),
            )
        elif result.kind == "circuit":
            self.statusBar().showMessage(
                f"Circuito {result.entity_id}: origem {result.related_id}.",
                5_000,
            )
        elif self._is_search_result_hidden(result):
            self.statusBar().showMessage(
                "Elemento oculto pelos filtros; destaque de busca mantido.",
                5_000,
            )
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_bars_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if (
            not visible
            and self._selected_feature is not None
            and self._selected_feature.kind == "bar"
            and not self._search_focus_active
        ):
            self._set_selection(None)
        self.view.set_bars_visible(visible)
        self.virtualizer.set_bars_visible(visible)
        if self.search_palette.isVisible():
            self.search_palette.refresh_results()
        state = "visíveis" if visible else "ocultas"
        self.statusBar().showMessage(f"Barras {state}.", 3_000)

    def _set_loads_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if (
            not visible
            and self._selected_feature is not None
            and self._selected_feature.kind in {"load", "equivalent_load"}
            and not self._search_focus_active
        ):
            self._set_selection(None)
        self.load_virtualizer.set_loads_visible(visible)
        self.equivalent_load_virtualizer.set_loads_visible(
            visible
            and self.simplified_network_action.isChecked()
            and self._equivalent_network_result is not None
        )
        if self.search_palette.isVisible():
            self.search_palette.refresh_results()
        state = "visíveis" if visible else "ocultas"
        self.statusBar().showMessage(f"Cargas {state}.", 3_000)

    def _set_generators_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if (
            not visible
            and self._selected_feature is not None
            and self._selected_feature.kind == "generator"
            and not self._search_focus_active
        ):
            self._set_selection(None)
        self.generator_virtualizer.set_loads_visible(visible)
        state = "visíveis" if visible else "ocultos"
        self.statusBar().showMessage(f"Geradores {state}.", 3_000)

    def _set_selection(
        self,
        selection: FeatureSelection | None,
        *,
        reveal_hidden: bool = False,
        preserve_branch: bool = False,
    ) -> None:
        if not preserve_branch:
            self._clear_branch_highlight(clear_table=True)
        self._search_focus_active = selection is not None and bool(reveal_hidden)
        if selection is None or selection.kind != "load":
            self.load_pattern_table_model.set_records(())
            self.load_patterns_section.setVisible(False)
        if selection is None or selection.kind != "equivalent_load":
            self.equivalent_pattern_table_model.set_records(())
            self.equivalent_patterns_section.setVisible(False)
        if selection is None or selection.kind != "segment":
            self.segment_power_flow_section.setVisible(False)
            # Só o trecho tem regulador; as demais páginas nunca o mostram.
            self.regulator_details_section.setVisible(False)
        if selection is None or selection.kind != "bar":
            self.bar_power_flow_section.setVisible(False)
        if self._model is None or selection is None:
            self._selected_feature = None
            self.virtualizer.set_selected_index(None)
            self.load_virtualizer.set_selected_index(None)
            self.equivalent_load_virtualizer.set_selected_index(None)
            self.generator_virtualizer.set_selected_index(None)
            self.segment_selection_overlay.clear()
            for label in (
                *self.bar_detail_labels.values(),
                *self.load_detail_labels.values(),
                *self.generator_detail_labels.values(),
                *self.generator_consumer_detail_labels.values(),
                *self.equivalent_detail_labels.values(),
                *self.segment_detail_labels.values(),
                *self.switch_detail_labels.values(),
                *self.regulator_detail_labels.values(),
            ):
                label.setText("—")
            self.switch_details_section.setVisible(False)
            self.details_dock.setWindowTitle("Elemento selecionado")
            self.details_stack.setCurrentWidget(self.empty_details_page)
            return

        if selection.kind != "generator":
            self.generator_virtualizer.set_selected_index(None)

        if selection.kind == "bar":
            if not 0 <= selection.index < len(self._model):
                return
            self._selected_feature = selection
            self.segment_selection_overlay.clear()
            self.load_virtualizer.set_selected_index(None)
            self.equivalent_load_virtualizer.set_selected_index(None)
            self.virtualizer.set_selected_index(
                selection.index,
                reveal_hidden=reveal_hidden,
            )
            self.switch_details_section.setVisible(False)
            record = self._model.record(selection.index)
            crs = self._model.crs
            self.bar_detail_labels["bar_id"].setText(record.bar_id)
            self.bar_detail_labels["code"].setText(record.code or "—")
            self.bar_detail_labels["x"].setText(f"{record.x:.3f}")
            self.bar_detail_labels["y"].setText(f"{record.y:.3f}")
            self.bar_detail_labels["zone"].setText(str(crs.zone))
            self.bar_detail_labels["hemisphere"].setText(crs.hemisphere)
            self.bar_detail_labels["epsg"].setText(str(crs.epsg))
            self._sync_bar_power_flow_section(selection.index)
            self.details_dock.setWindowTitle("Barra selecionada")
            self.details_stack.setCurrentWidget(self.bar_details_page)
            return

        if selection.kind == "load":
            if self._load_model is None or not 0 <= selection.index < len(
                self._load_model
            ):
                return
            self._selected_feature = selection
            self.virtualizer.set_selected_index(None)
            self.segment_selection_overlay.clear()
            self.equivalent_load_virtualizer.set_selected_index(None)
            self.load_virtualizer.set_selected_index(
                selection.index,
                reveal_hidden=reveal_hidden,
            )
            self.switch_details_section.setVisible(False)
            record = self._load_model.record(selection.index)
            values = {
                "load_id": record.load_id,
                "bar_id": record.bar_id,
                "external_id": record.external_id,
                "code": record.code,
                "snom": record.snom,
                "sadm": record.sadm,
                "secondary_line_voltage": record.secondary_line_voltage,
                "phases": record.phases,
                "connection_type": record.connection_type,
            }
            for key, value in values.items():
                self.load_detail_labels[key].setText(value or "—")
            self._sync_load_companion_columns(selection.index, record)
            pattern_records = (
                ()
                if self._load_pattern_model is None
                else self._load_pattern_model.records_for_load(selection.index)
            )
            self.load_pattern_table_model.set_records(pattern_records)
            self.load_patterns_section.setVisible(bool(pattern_records))
            self.details_dock.setWindowTitle("Carga selecionada")
            self.details_stack.setCurrentWidget(self.load_details_page)
            self.load_details_page.verticalScrollBar().setValue(0)
            return

        if selection.kind == "equivalent_load":
            result = self._equivalent_network_result
            if result is None or not 0 <= selection.index < len(result.model):
                return
            self._selected_feature = selection
            self.virtualizer.set_selected_index(None)
            self.load_virtualizer.set_selected_index(None)
            self.segment_selection_overlay.clear()
            self.equivalent_load_virtualizer.set_selected_index(selection.index)
            self.switch_details_section.setVisible(False)
            record = result.model.record(selection.index)

            def decimal_text(value) -> str:  # noqa: ANN001
                if value is None:
                    return "—"
                text = format(value, "f")
                if "." in text:
                    text = text.rstrip("0").rstrip(".")
                return text or "0"

            source_ids = (
                ()
                if self._load_model is None
                else tuple(
                    self._load_model.load_ids[int(index)]
                    for index in record.source_load_indices
                )
            )
            shown_ids = source_ids[:50]
            source_text = ", ".join(shown_ids) or "—"
            if len(source_ids) > len(shown_ids):
                source_text += f" … e mais {len(source_ids) - len(shown_ids):n}"
            generator_ids = (
                ()
                if self._generator_model is None
                else tuple(
                    self._generator_model.generator_ids[int(index)]
                    for index in record.source_generator_indices
                )
            )
            shown_generator_ids = generator_ids[:50]
            generator_text = ", ".join(shown_generator_ids) or "—"
            if len(generator_ids) > len(shown_generator_ids):
                generator_text += (
                    f" … e mais {len(generator_ids) - len(shown_generator_ids):n}"
                )
            values = {
                "origin": "Ramal agregado",
                "load_id": record.load_id,
                "branch_id": str(record.branch_id),
                "branch_type": record.branch_type.value,
                "removable": "SIM (1)" if record.removable else "NÃO (0)",
                "circuit_id": record.circuit_id,
                "bar_id": record.bar_id,
                "first_segment_id": record.first_segment_id,
                "phases2": record.phases2,
                "phase": record.phase,
                "source_load_count": str(record.source_load_count),
                "source_generator_count": str(record.source_generator_count),
                "snom": decimal_text(record.snom),
                "sadm": decimal_text(record.sadm),
                "source_load_ids": source_text,
                "source_generator_ids": generator_text,
            }
            for key, value in values.items():
                self.equivalent_detail_labels[key].setText(value or "—")
            pattern_records = result.model.records_for_load(selection.index)
            self.equivalent_pattern_table_model.set_records(pattern_records)
            self.equivalent_patterns_section.setVisible(bool(pattern_records))
            self.details_dock.setWindowTitle("Carga equivalente de ramal")
            self.details_stack.setCurrentWidget(self.equivalent_details_page)
            self.equivalent_details_page.verticalScrollBar().setValue(0)
            return

        if selection.kind == "generator":
            if self._generator_model is None or not 0 <= selection.index < len(
                self._generator_model
            ):
                return
            self._selected_feature = selection
            self.virtualizer.set_selected_index(None)
            self.load_virtualizer.set_selected_index(None)
            self.equivalent_load_virtualizer.set_selected_index(None)
            self.segment_selection_overlay.clear()
            self.generator_virtualizer.set_selected_index(
                selection.index, reveal_hidden=reveal_hidden
            )
            record = self._generator_model.record(selection.index)
            generator_values = {
                "generator_id": record.generator_id,
                "mt_cons_id": record.mt_cons_id,
                "generator_code": record.generator_code,
                "nominal_voltage": record.nominal_voltage,
                "nominal_power": record.nominal_power,
                "connection": record.connection,
                "curve_id": record.curve_id,
                "generation_kwh": record.generation_kwh,
            }
            consumer_values = {
                "consumer_id": record.consumer_id,
                "load_id": record.load_id,
                "consumer_code": record.consumer_code,
                "external_id": record.external_id,
                "name": record.name,
                "phases": record.phases,
            }
            for key, value in generator_values.items():
                self.generator_detail_labels[key].setText(value or "—")
            for key, value in consumer_values.items():
                self.generator_consumer_detail_labels[key].setText(value or "—")
            update_model = (
                None
                if self._generator_update_result is None
                else self._generator_update_result.model
            )
            demand_records = (
                ()
                if update_model is None
                or update_model.generators is not self._generator_model
                else update_model.demand_records_for_generator(selection.index)
            )
            phase_records = (
                ()
                if update_model is None
                or update_model.generators is not self._generator_model
                else update_model.phase_power_records_for_generator(
                    selection.index
                )
            )
            self.generator_demand_table_model.set_records(demand_records)
            self.generator_phase_power_table_model.set_records(phase_records)
            has_update = bool(demand_records and phase_records)
            self.generator_demand_section.setVisible(has_update)
            self.generator_phase_power_section.setVisible(has_update)
            issue = (
                None
                if self._generator_update_result is None
                else next(
                    (
                        item.reason
                        for item in self._generator_update_result.issues
                        if item.generator_id == record.generator_id
                    ),
                    None,
                )
            )
            if (
                issue is None
                and self._generator_update_result is not None
                and not has_update
            ):
                issue = "não foi calculado; consulte o relatório da atualização"
            self.generator_update_note.setText(
                "Gerador não calculado: " + issue if issue else ""
            )
            self.generator_update_note.setVisible(bool(issue and not has_update))
            self.details_dock.setWindowTitle("Gerador selecionado")
            self.details_stack.setCurrentWidget(self.generator_details_page)
            self.generator_details_page.verticalScrollBar().setValue(0)
            return

        if self._line_model is None or not 0 <= selection.index < len(self._line_model):
            return
        self._selected_feature = selection
        self.virtualizer.set_selected_index(None)
        self.load_virtualizer.set_selected_index(None)
        self.equivalent_load_virtualizer.set_selected_index(None)
        self.generator_virtualizer.set_selected_index(None)
        self.segment_selection_overlay.bind(self._line_model, selection.index)
        record = self._line_model.record(selection.index)
        values = {
            "segment_id": record.segment_id,
            "code": record.code,
            "phases": record.phases,
            "start_bar_id": record.start_bar_id,
            "end_bar_id": record.end_bar_id,
            "arrangement_id": record.arrangement_id,
            "phase_cable_id": record.phase_cable_id,
            "neutral_cable_id": record.neutral_cable_id,
            "length": "—" if record.length is None else f"{record.length:.3f}",
        }
        for key, value in values.items():
            self.segment_detail_labels[key].setText(value or "—")
        self._sync_segment_cable_columns(record, selection.index)

        switch_record = (
            None
            if self._switch_model is None
            else self._switch_model.record_for_segment(selection.index)
        )
        if switch_record is None:
            for label in self.switch_detail_labels.values():
                label.setText("—")
            self.switch_details_section.setVisible(False)
        else:
            switch_values = {
                "switch_id": switch_record.switch_id,
                "switch_type_id": switch_record.switch_type_id,
                "circuit_id": switch_record.circuit_id,
                "segment_id": switch_record.segment_id,
                "code": switch_record.code,
                "state": switch_record.state,
                "normal_state": switch_record.normal_state,
                "corn": switch_record.corn,
                "elo": switch_record.elo,
                "elo_type": switch_record.elo_type,
            }
            for key, value in switch_values.items():
                self.switch_detail_labels[key].setText(value or "—")
            self.switch_details_section.setVisible(True)

        regulator_record = (
            None
            if self._regulator_model is None
            else self._regulator_model.record_for_segment(selection.index)
        )
        if regulator_record is None:
            for label in self.regulator_detail_labels.values():
                label.setText("—")
            self.regulator_tap_label.setVisible(False)
            self.regulator_tap_label.setText("")
            self.regulator_tap_table_model.clear()
            self.regulator_tap_table_title.setVisible(False)
            self.regulator_tap_table.setVisible(False)
            self.regulator_details_section.setVisible(False)
        else:
            regulator_values = {
                "regulator_id": regulator_record.regulator_id,
                "segment_id": regulator_record.segment_id,
                "external_id": regulator_record.external_id,
                "code": regulator_record.code,
                "connection": regulator_record.connection,
                "snom": regulator_record.snom,
                "regulation_range": regulator_record.regulation_range,
                "step_count": regulator_record.step_count,
                "tap": regulator_record.tap,
                "inom": regulator_record.inom,
                "vnom": regulator_record.vnom,
            }
            for key, value in regulator_values.items():
                self.regulator_detail_labels[key].setText(value or "—")
            self._apply_regulator_tap(selection.index)
            self.regulator_details_section.setVisible(True)

        self._sync_segment_power_flow_section(selection.index)
        self.details_dock.setWindowTitle("Trecho selecionado")
        self.details_stack.setCurrentWidget(self.segment_details_page)
        self.segment_details_page.verticalScrollBar().setValue(0)

    def _phase_entry(self, value: str):  # noqa: ANN201
        """Resolve um FASES2 na configuração atual, ou ``None``."""

        configuration = self._phase_configuration
        if configuration is None:
            return None
        key = str(value).strip().casefold()
        return next(
            (entry for entry in configuration.entries if entry.fases2 == key),
            None,
        )

    def _apply_phase_companion(self, label: QLabel, value: str) -> None:
        """Escreve o NOME do FASES2 e o número de fases no tooltip."""

        entry = self._phase_entry(value)
        if entry is None:
            label.setText("—")
            label.setToolTip("")
            return
        label.setText(entry.name or "—")
        label.setToolTip(f"NUMERO_FASES: {entry.phase_count}")

    def _sync_segment_cable_columns(self, record, segment_index: int) -> None:  # noqa: ANN001
        """Preenche a coluna à direita de FASES2, BARRA1_ID/BARRA2_ID e CABOF_ID/CABON_ID.

        BARRA1_ID/BARRA2_ID sempre mostram o código da barra correspondente,
        pois ele está sempre disponível a partir do modelo de trechos — igual
        ao painel de cargas. FASES2 e os cabos continuam dependendo da
        configuração de fases e do catálogo de cabos.
        """

        catalog = self._cable_model
        visible = catalog is not None or self._phase_configuration is not None
        cable_ids = {
            "phase_cable_id": record.phase_cable_id,
            "neutral_cable_id": record.neutral_cable_id,
        }
        start_index = int(self._line_model.start_indices[segment_index])
        end_index = int(self._line_model.end_indices[segment_index])
        bar_codes = {
            "start_bar_id": self._line_model.bars.codes[start_index],
            "end_bar_id": self._line_model.bars.codes[end_index],
        }
        for key, label in self.segment_companion_labels.items():
            if key == "phases":
                self._apply_phase_companion(label, record.phases)
                label.setVisible(visible)
                continue
            if key in bar_codes:
                label.setText(bar_codes[key] or "—")
                label.setToolTip("")
                label.setVisible(True)
                continue
            cable_id = cable_ids.get(key)
            if catalog is None or cable_id is None:
                label.setText("—")
                label.setToolTip("")
            else:
                cable = catalog.record_for_id(cable_id) if cable_id else None
                label.setText("—" if cable is None else cable_summary(cable))
                label.setToolTip("" if cable is None else cable_tooltip(cable))
            label.setVisible(visible)

    def _sync_load_companion_columns(self, load_index: int, record) -> None:  # noqa: ANN001
        """Preenche a coluna à direita de BARRA_ID e de FASES2.

        A coluna fica sempre visível nesta página: o código da barra existe
        sempre que há cargas, ao contrário do painel de trechos, que depende do
        catálogo de cabos.
        """

        model = self._load_model
        bar_code = ""
        if model is not None and 0 <= load_index < len(model):
            bar_index = int(model.bar_indices[load_index])
            bar_code = model.bars.codes[bar_index]
        for key, label in self.load_companion_labels.items():
            if key == "phases":
                self._apply_phase_companion(label, record.phases)
            elif key == "bar_id":
                label.setText(bar_code or "—")
                label.setToolTip("")
            else:
                label.setText("—")
                label.setToolTip("")
            label.setVisible(True)

    @staticmethod
    def _quantity_of(combo: QComboBox, quantities) -> tuple[str, int, bool]:  # noqa: ANN001
        """Chave, casas decimais e presença de fasor da grandeza escolhida.

        A chave viaja no ``UserRole`` para o painel não depender da posição do
        item, e o ``next`` com padrão cobre o combobox ainda vazio.
        """

        key = combo.currentData()
        return next(
            (
                (name, decimals, phasor)
                for name, _, decimals, phasor in quantities
                if name == key
            ),
            (quantities[0][0], quantities[0][2], quantities[0][3]),
        )

    def _apply_regulator_tap(self, segment_index: int) -> None:
        """Escreve o tap final do regulador e a tabela de passos por patamar.

        ``result.regulator_taps`` traz um retrato por patamar (o mesmo formato
        de ``segment_currents``/``bar_voltages``); o rótulo "Tap resolvido"
        continua mostrando só o **último** patamar — o aviso de fim de curso é
        o motivo de a linha existir: um regulador que esgotou o tap **parou de
        regular**, e sem dizê-lo o painel mostraria uma posição de aparência
        normal.
        """

        result = self._power_flow_result
        taps_by_step = (
            () if result is None else result.regulator_taps.get(segment_index, ())
        )
        if not taps_by_step:
            self.regulator_tap_label.setVisible(False)
            self.regulator_tap_label.setText("")
            self.regulator_tap_table_model.clear()
            self.regulator_tap_table_title.setVisible(False)
            self.regulator_tap_table.setVisible(False)
            return

        final_taps = taps_by_step[-1]
        parts = ", ".join(f"{tap.phase}: {tap.tap:.4f}" for tap in final_taps)
        saturated = [tap.phase for tap in final_taps if tap.at_limit]
        text = f"Tap resolvido — {parts}"
        if saturated:
            text += (
                f". Fase(s) {', '.join(saturated)} no fim do curso: "
                "o regulador não consegue corrigir mais."
            )
        self.regulator_tap_label.setText(text)
        self.regulator_tap_label.setVisible(True)

        # A ordem das fases segue a que já sai do exportador (não é sempre
        # D, E, F — depende da ordem das letras em NOME no fases2.json), então
        # a lê da própria primeira linha em vez de fixar uma ordem própria.
        labels = tuple(f"Fase {tap.phase}" for tap in taps_by_step[0])
        rows = tuple(
            tuple(float(tap.step) for tap in step_taps)
            for step_taps in taps_by_step
        )
        self.regulator_tap_table_model.set_values(labels, rows, decimals=0)
        self.regulator_tap_table_title.setVisible(True)
        self.regulator_tap_table.setVisible(True)

    def _phase_letter_of_node(self, node: int) -> str | None:
        """Letra da fase de um nó DSS, segundo o ``fases2.json`` carregado."""

        configuration = self._phase_configuration
        if configuration is None:
            return None
        return phase_letters_by_node(configuration).get(int(node))

    def _node_labels(self, nodes: Sequence[int]) -> tuple[str, ...]:
        """Rótulo de cada coluna de fase, com o número do nó como reserva."""

        return tuple(
            f"Fase {self._phase_letter_of_node(node) or node}" for node in nodes
        )

    def _pair_labels(self, pairs: Sequence[tuple[int, int]]) -> tuple[str, ...]:
        """Rótulo de cada coluna fase-fase, no formato ``VDE``."""

        return tuple(
            "V"
            + (self._phase_letter_of_node(first) or str(first))
            + (self._phase_letter_of_node(second) or str(second))
            for first, second in pairs
        )

    @staticmethod
    def _angle_labels(labels: Sequence[str]) -> tuple[str, ...]:
        """Rótulo do ângulo a partir do rótulo do módulo: ``Fase D`` → ``θD``."""

        return tuple(
            "θ" + label.removeprefix("Fase ").removeprefix("V")
            for label in labels
        )

    def _with_angles(
        self,
        labels: Sequence[str],
        magnitudes: Sequence[Sequence[float | None]],
        angles: Sequence[Sequence[float]],
        decimals: int,
    ) -> tuple[tuple[str, ...], tuple[tuple[float | None, ...], ...], tuple[int, ...]]:
        """Junta módulo e ângulo na mesma tabela, lado a lado.

        Sem ângulo colhido — resultado de uma execução anterior à colheita de
        fasores — a tabela volta a ser só de módulos, em vez de exibir colunas
        de traço.
        """

        columns = tuple(labels)
        if not angles or len(angles) != len(magnitudes):
            return columns, tuple(tuple(row) for row in magnitudes), (decimals,) * len(columns)
        rows = tuple(
            (*magnitude_row, *angle_row)
            for magnitude_row, angle_row in zip(magnitudes, angles, strict=True)
        )
        return (
            (*columns, *self._angle_labels(columns)),
            rows,
            (decimals,) * len(columns) + (_ANGLE_DECIMALS,) * len(columns),
        )

    def _set_combo_item_enabled(
        self,
        combo: QComboBox,
        key: str,
        enabled: bool,
    ) -> None:
        model = combo.model()
        for row in range(combo.count()):
            if combo.itemData(row) != key:
                continue
            item = model.item(row) if hasattr(model, "item") else None
            if item is not None:
                item.setEnabled(enabled)
            return

    def _sync_segment_power_flow_section(self, segment_index: int) -> None:
        """Mostra (ou esconde) os resultados do trecho selecionado."""

        result = self._power_flow_result
        currents = (
            None
            if result is None
            else result.segment_currents.get(segment_index)
        )
        self._segment_power_flow_currents = currents
        self._segment_power_flow_powers = (
            None if result is None else result.segment_powers.get(segment_index)
        )
        if currents is None:
            self.segment_power_flow_model.clear()
            self.segment_power_flow_note.setVisible(False)
            self.segment_power_flow_section.setVisible(False)
            return
        # Sem IADM não há base para o percentual; a grandeza fica desabilitada
        # em vez de exibir uma coluna de traços sem explicação.
        self._set_combo_item_enabled(
            self.segment_power_flow_combo,
            "loading",
            currents.ampacity is not None,
        )
        powers = self._segment_power_flow_powers
        for key in _POWER_QUANTITY_KEYS:
            self._set_combo_item_enabled(
                self.segment_power_flow_combo,
                key,
                powers is not None
                and (key != "losses" or bool(powers.active_losses)),
            )
        self.segment_power_flow_section.setVisible(True)
        self._refresh_segment_power_flow_values()

    def _show_segment_power(
        self,
        key: str,
        powers: SegmentPowers,
        decimals: int,
        with_phasor: bool,
    ) -> None:
        """Monta a tabela de uma das grandezas de potência do trecho.

        Todas nascem de ``P`` e ``Q`` do terminal 1 e **preservam o sinal do
        OpenDSS**: positivo entra pelo terminal, negativo sai. É o sinal que diz
        o sentido do fluxo.
        """

        phases = self._node_labels(powers.nodes)
        if key == "active_power":
            self.segment_power_flow_model.set_values(
                phases, powers.active, decimals=decimals
            )
            return
        if key == "reactive_power":
            self.segment_power_flow_model.set_values(
                phases, powers.reactive, decimals=decimals
            )
            return
        if key == "apparent_power":
            magnitudes, angles = apparent_power(powers.active, powers.reactive)
            labels, rows, places = self._with_angles(
                phases, magnitudes, angles if with_phasor else (), decimals
            )
            self.segment_power_flow_model.set_values(
                labels, rows, decimals=places
            )
            return
        if key == "three_phase_power":
            self.segment_power_flow_model.set_values(
                ("P (kW)", "Q (kvar)", "S (kVA)", "θS"),
                three_phase_power(powers.active, powers.reactive),
                decimals=(decimals, decimals, decimals, _ANGLE_DECIMALS),
            )
            return
        if key == "power_factor":
            self.segment_power_flow_model.set_values(
                (*phases, "3φ"),
                power_factor(powers.active, powers.reactive),
                decimals=decimals,
            )
            return
        # Perdas: o OpenDSS as devolve somadas no elemento, não por fase.
        rows = tuple(
            (active, reactive)
            for active, reactive in zip(
                powers.active_losses, powers.reactive_losses, strict=True
            )
        )
        self.segment_power_flow_model.set_values(
            ("ΔP (kW)", "ΔQ (kvar)"), rows, decimals=decimals
        )

    def _refresh_segment_power_flow_values(self) -> None:
        currents = self._segment_power_flow_currents
        if currents is None:
            return
        key, decimals, with_phasor = self._quantity_of(
            self.segment_power_flow_combo,
            _SEGMENT_QUANTITIES,
        )
        labels = self._node_labels(currents.nodes)
        powers = self._segment_power_flow_powers
        if key in _POWER_QUANTITY_KEYS:
            self.segment_power_flow_note.setVisible(False)
            if powers is None:
                self.segment_power_flow_model.set_values((), ())
                return
            self._show_segment_power(key, powers, decimals, with_phasor)
            return
        if key == "loading":
            ampacity = currents.ampacity
            rows = tuple(
                tuple(
                    None if ampacity is None else value / ampacity * 100.0
                    for value in row
                )
                for row in currents.magnitudes
            )
            self.segment_power_flow_note.setText(
                "O cabo de fase do trecho não tem IADM numérico, então o "
                "carregamento não pode ser calculado."
                if ampacity is None
                else f"Base: IADM de {ampacity:n} A."
            )
            self.segment_power_flow_note.setVisible(True)
            places: int | tuple[int, ...] = decimals
        else:
            rows = currents.magnitudes
            self.segment_power_flow_note.setVisible(False)
            labels, rows, places = self._with_angles(
                labels,
                rows,
                currents.angles if with_phasor else (),
                decimals,
            )
        self.segment_power_flow_model.set_values(
            labels,
            rows,
            decimals=places,
        )

    def _sync_bar_power_flow_section(self, bar_index: int) -> None:
        """Mostra (ou esconde) os resultados da barra selecionada."""

        result = self._power_flow_result
        voltages = (
            None if result is None else result.bar_voltages.get(bar_index)
        )
        self._bar_power_flow_voltages = voltages
        if voltages is None:
            self.bar_power_flow_model.clear()
            self.bar_power_flow_note.setVisible(False)
            self.bar_power_flow_section.setVisible(False)
            return
        # Barra monofásica não tem par de fases, e o desequilíbrio exige as
        # três; as grandezas ficam desabilitadas em vez de abrir uma tabela
        # vazia, como o "Carregamento" já faz.
        pairs, _, _ = line_voltages(
            voltages.nodes,
            voltages.magnitudes,
            voltages.angles,
        )
        for key in ("line_voltage", "line_per_unit"):
            self._set_combo_item_enabled(
                self.bar_power_flow_combo, key, bool(pairs)
            )
        self._set_combo_item_enabled(
            self.bar_power_flow_combo,
            "unbalance",
            bool(
                voltage_unbalance(
                    voltages.nodes, voltages.magnitudes, voltages.angles
                )
            ),
        )
        self.bar_power_flow_section.setVisible(True)
        self._refresh_bar_power_flow_values()

    def _refresh_bar_power_flow_values(self) -> None:
        voltages = self._bar_power_flow_voltages
        if voltages is None:
            return
        key, decimals, with_phasor = self._quantity_of(
            self.bar_power_flow_combo,
            _BAR_QUANTITIES,
        )
        # Cadeia explícita por grandeza: um else que engolisse a chave
        # desconhecida faria a tensão de linha exibir a de fase em silêncio.
        note = ""
        if key == "per_unit":
            labels = self._node_labels(voltages.nodes)
            rows: tuple[tuple[float | None, ...], ...] = voltages.per_unit
            places: int | tuple[int, ...] = decimals
        elif key == "unbalance":
            values = voltage_unbalance(
                voltages.nodes,
                voltages.magnitudes,
                voltages.angles,
            )
            if not values:
                note = (
                    "O desequilíbrio exige as três fases; esta barra não as "
                    "tem."
                )
            labels = ("FD (%)",) if values else ()
            rows = tuple((value,) for value in values)
            places = decimals
        elif key in {"line_voltage", "line_per_unit"}:
            per_unit = key == "line_per_unit"
            pairs, magnitudes, angles = line_voltages(
                voltages.nodes,
                voltages.per_unit if per_unit else voltages.magnitudes,
                voltages.angles,
            )
            if per_unit:
                # A pu do OpenDSS é na base de fase; a de linha é √3 maior, e é
                # essa renormalização que faz o nominal dar 1,0.
                magnitudes = tuple(
                    tuple(value / LINE_VOLTAGE_PU_BASE for value in row)
                    for row in magnitudes
                )
            if not pairs:
                note = (
                    "A barra tem uma fase só, então não há tensão entre fases "
                    "para calcular."
                )
            labels, rows, places = self._with_angles(
                self._pair_labels(pairs),
                magnitudes,
                angles if with_phasor else (),
                decimals,
            )
        else:
            labels, rows, places = self._with_angles(
                self._node_labels(voltages.nodes),
                voltages.magnitudes,
                voltages.angles if with_phasor else (),
                decimals,
            )
        self.bar_power_flow_note.setText(note)
        self.bar_power_flow_note.setVisible(bool(note))
        self.bar_power_flow_model.set_values(
            labels,
            rows,
            decimals=places,
        )

    def _show_coordinates(self, x: float, y: float) -> None:
        self.coordinate_status.setText(f"X: {x:.3f}   Y: {y:.3f}")

    def _update_status_counts(self, active: int) -> None:
        self._active_bar_count = int(active)
        self._refresh_active_status()

    def _update_load_status_counts(self, active: int) -> None:
        self._active_load_count = int(active)
        self._refresh_active_status()

    def _update_equivalent_load_status_counts(self, active: int) -> None:
        self._active_equivalent_load_count = int(active)
        self._refresh_active_status()

    def _update_generator_status_counts(self, active: int) -> None:
        self._active_generator_count = int(active)
        self._refresh_active_status()

    def _refresh_active_status(self) -> None:
        self.active_status.setText(
            "Itens ativos: "
            f"{self._active_bar_count + self._active_load_count + self._active_equivalent_load_count + self._active_generator_count:n}"
        )

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._schedule_viewport_overlay_update()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._import_thread is not None and self._import_thread.isRunning():
            if self._import_worker is not None:
                self._import_worker.cancel()
            self._close_after_import = True
            event.ignore()
            return
        if self._branch_thread is not None and self._branch_thread.isRunning():
            if self._branch_worker is not None:
                self._branch_worker.cancel()
            self._close_after_branch_analysis = True
            event.ignore()
            return
        if (
            self._equivalent_thread is not None
            and self._equivalent_thread.isRunning()
        ):
            if self._equivalent_worker is not None:
                self._equivalent_worker.cancel()
            self._close_after_equivalent_build = True
            event.ignore()
            return
        if (
            self._branch_json_thread is not None
            and self._branch_json_thread.isRunning()
        ):
            if self._branch_json_worker is not None:
                self._branch_json_worker.cancel()
            self._close_after_branch_json_export = True
            event.ignore()
            return
        if (
            self._branch_csv_thread is not None
            and self._branch_csv_thread.isRunning()
        ):
            if self._branch_csv_worker is not None:
                self._branch_csv_worker.cancel()
            self._close_after_branch_csv_export = True
            event.ignore()
            return
        if self._export_thread is not None and self._export_thread.isRunning():
            if self._export_worker is not None:
                self._export_worker.cancel()
            self._close_after_export = True
            event.ignore()
            return
        if (
            self._power_flow_thread is not None
            and self._power_flow_thread.isRunning()
        ):
            if self._power_flow_worker is not None:
                self._power_flow_worker.cancel()
            self._close_after_power_flow = True
            event.ignore()
            return
        if (
            self._generator_update_thread is not None
            and self._generator_update_thread.isRunning()
        ):
            if self._generator_update_worker is not None:
                self._generator_update_worker.cancel()
            self._close_after_generator_update = True
            event.ignore()
            return
        # Última guarda, e depois das de thread: fechar a janela de curvas
        # dispara o próprio aviso de alterações pendentes, e close() devolve
        # False quando o usuário cancela. Sem pendências é inerte.
        if not self.curves_window.close():
            event.ignore()
            return
        if not self.patamares_window.close():
            event.ignore()
            return
        if not self.opendss_cables_window.close():
            event.ignore()
            return
        if not self.opendss_geometries_window.close():
            event.ignore()
            return
        self.opendss_library_help.close()
        self.search_palette.shutdown()
        self.view.shutdown_satellite()
        super().closeEvent(event)
