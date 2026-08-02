"""Janela principal do visualizador."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsScene,
    QGridLayout,
    QHeaderView,
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
from .branch_window import BranchesWindow, BranchTableModel
from .circuit_import import CircuitLoadResult
from .circuits_window import CircuitTableModel, CircuitsWindow
from .csv_import import CsvLoadResult
from .graphics import (
    BranchHighlightOverlayItem,
    DiagramView,
    ItemVirtualizer,
    LineNetworkItem,
    LoadVirtualizer,
    SegmentSelectionOverlayItem,
    SwitchNetworkItem,
)
from .load_import import LoadCsvResult
from .load_pattern_import import LoadPatternCsvResult
from .load_pattern_table import LoadPatternTableModel
from .model import (
    CircuitCatalogModel,
    CircuitModel,
    CircuitVisibilityController,
    FeatureSelection,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    SwitchModel,
    UtmCrs,
)
from .overlap_report import CircuitOverlapReportWindow, OverlapReportTableModel
from .phase_config import (
    PHASE_COLORS,
    PhaseClassification,
    PhaseConfiguration,
    PhaseConfigurationError,
    default_phase_configuration_path,
    load_phase_configuration,
)
from .phase_legend import PhaseLegend
from .search import GlobalSearchIndex, SearchResult
from .search_palette import SearchPalette
from .segment_import import SegmentLoadResult
from .switch_import import SwitchLoadResult
from .workers import (
    BranchAnalysisWorker,
    CircuitImportWorker,
    CsvImportWorker,
    LoadImportWorker,
    LoadPatternImportWorker,
    SegmentImportWorker,
    SwitchImportWorker,
)


class UtmImportDialog(QDialog):
    """Coleta os metadados UTM ausentes do CSV."""

    def __init__(self, file_name: str, parent=None) -> None:  # noqa: ANN001
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

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

        self.switches_button = QPushButton("Importar chaves…")
        self.switches_button.setToolTip(
            "Carregar atributos de chaves vinculados aos trechos importados"
        )
        self.switches_button.setEnabled(has_bars and has_segments)
        self.switches_button.clicked.connect(lambda: self._select("switches"))
        layout.addWidget(self.switches_button)

        self.circuits_button = QPushButton("Importar circuitos…")
        self.circuits_button.setToolTip(
            "Carregar circuitos e descobrir seus elementos na rede"
        )
        self.circuits_button.setEnabled(has_bars and has_segments)
        self.circuits_button.clicked.connect(lambda: self._select("circuits"))
        layout.addWidget(self.circuits_button)

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


class MainWindow(QMainWindow):
    def __init__(self, phase_configuration_path: str | Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Visualizador de Circuitos Elétricos")
        self.resize(1280, 800)

        self._model: CircuitModel | None = None
        self._line_model: LineNetworkModel | None = None
        self._line_item: LineNetworkItem | None = None
        self._load_model: LoadModel | None = None
        self._load_pattern_model: LoadPatternModel | None = None
        self._switch_model: SwitchModel | None = None
        self._switch_item: SwitchNetworkItem | None = None
        self._circuit_catalog: CircuitCatalogModel | None = None
        self._circuit_visibility: CircuitVisibilityController | None = None
        self._branch_analysis_result: BranchAnalysisResult | None = None
        self._selected_branch: BranchRecord | None = None
        self._selected_feature: FeatureSelection | None = None
        self._search_focus_active = False
        self._active_bar_count = 0
        self._active_load_count = 0
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
        self._import_thread: QThread | None = None
        self._import_worker: (
            CsvImportWorker
            | SegmentImportWorker
            | LoadImportWorker
            | LoadPatternImportWorker
            | SwitchImportWorker
            | CircuitImportWorker
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

        self.scene = QGraphicsScene(self)
        self.scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.view = DiagramView(self.scene, self)
        self.setCentralWidget(self.view)
        self.virtualizer = ItemVirtualizer(self.scene, self.view, parent=self)
        self.load_virtualizer = LoadVirtualizer(
            self.scene,
            self.view,
            parent=self,
        )
        self.view.set_load_layer(self.load_virtualizer)
        self.segment_selection_overlay = SegmentSelectionOverlayItem()
        self.scene.addItem(self.segment_selection_overlay)
        self.branch_highlight_overlay = BranchHighlightOverlayItem()
        self.scene.addItem(self.branch_highlight_overlay)
        self.search_index = GlobalSearchIndex()
        self.search_palette = SearchPalette(
            self.search_index,
            self._is_search_result_hidden,
            self.view.viewport(),
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
        if self._phase_configuration_error is not None:
            QTimer.singleShot(0, self._show_phase_configuration_error)

    def _create_actions(self) -> None:
        self.import_action = QAction("Importar…", self)
        self.import_action.setShortcut(QKeySequence.StandardKey.Open)
        self.import_action.setToolTip(
            "Importar barras, trechos, cargas, chaves ou circuitos de arquivos CSV"
        )
        self.import_action.triggered.connect(self._choose_import)

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

        self.circuits_action = QAction("Circuitos…", self)
        self.circuits_action.setEnabled(False)
        self.circuits_action.triggered.connect(self._show_circuits_window)

        self.overlaps_action = QAction("Sobreposições…", self)
        self.overlaps_action.setEnabled(False)
        self.overlaps_action.triggered.connect(self._show_overlap_report)

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
        file_menu = self.menuBar().addMenu("Arquivo")
        file_menu.addAction(self.import_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        view_menu = self.menuBar().addMenu("Visualizar")
        view_menu.addAction(self.show_bars_action)
        view_menu.addAction(self.show_loads_action)
        view_menu.addAction(self.phase_coloring_action)
        view_menu.addAction(self.circuits_action)
        view_menu.addAction(self.overlaps_action)
        view_menu.addSeparator()
        view_menu.addAction(self.fit_action)

        self.tools_menu = self.menuBar().addMenu("Ferramentas")
        self.tools_menu.addAction(self.branches_action)

        toolbar = QToolBar("Ferramentas principais", self)
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.addAction(self.import_action)
        toolbar.addSeparator()
        toolbar.addAction(self.select_action)
        toolbar.addAction(self.pan_action)
        toolbar.addAction(self.fit_action)
        toolbar.addSeparator()
        toolbar.addAction(self.search_action)
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
            "Clique em uma barra, trecho ou carga para ver seus dados."
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

        def create_table(fields: tuple[tuple[str, str], ...], parent: QWidget):
            table = QWidget(parent)
            table.setObjectName("details_table")
            grid = QGridLayout(table)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(0)
            grid.setVerticalSpacing(0)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 1)
            labels: dict[str, QLabel] = {}
            caption_labels: dict[str, QLabel] = {}
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
            return table, labels, caption_labels, grid

        def create_table_page(fields: tuple[tuple[str, str], ...]):
            page = QWidget(self.details_stack)
            page_layout = QVBoxLayout(page)
            table, labels, caption_labels, grid = create_table(fields, page)
            page_layout.addWidget(table)
            page_layout.addStretch(1)
            return page, table, labels, caption_labels, grid

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
        ) = create_table(load_fields, self.load_details_body)
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
        self.load_patterns_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.load_patterns_table.horizontalHeader().setStretchLastSection(False)
        self.load_patterns_table.setStyleSheet(
            "QTableView { gridline-color: palette(mid); }"
        )
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
        ) = create_table(segment_fields, self.segment_details_body)
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
        ) = create_table(switch_fields, self.switch_details_section)
        switch_layout.addWidget(self.switch_details_table)
        self.switch_details_section.setVisible(False)
        segment_layout.addWidget(self.switch_details_section)
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
        self.active_status = QLabel("Itens ativos: 0")
        self.mode_status = QLabel("Visão geral")
        self.overlap_status = QLabel("Sobreposições: 0")
        status = self.statusBar()
        status.addWidget(self.coordinate_status, 1)
        status.addPermanentWidget(self.total_status)
        status.addPermanentWidget(self.segment_status)
        status.addPermanentWidget(self.load_status)
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
        self.virtualizer.countsChanged.connect(self._update_status_counts)
        self.load_virtualizer.countsChanged.connect(
            self._update_load_status_counts
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

    def _choose_import(self) -> None:
        if self._import_thread is not None or self._branch_thread is not None:
            return
        dialog = ImportChoiceDialog(
            self._model is not None,
            self._line_model is not None,
            self,
            has_loads=self._load_model is not None,
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
        elif dialog.selected_kind == "switches":
            self._choose_switches_csv()
        elif dialog.selected_kind == "circuits":
            self._choose_circuits_csv()

    def _choose_csv(self) -> None:
        if self._import_thread is not None or self._branch_thread is not None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar barras",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if not path:
            return

        crs_dialog = UtmImportDialog(Path(path).name, self)
        if crs_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._start_import(path, crs_dialog.crs())

    def _choose_segments_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._model is None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar trechos",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self._start_segment_import(path)

    def _choose_switches_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
            or self._line_model is None
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar chaves",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self._start_switch_import(path)

    def _choose_loads_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
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

    def _choose_circuits_csv(self) -> None:
        if (
            self._import_thread is not None
            or self._branch_thread is not None
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
            self._start_circuit_import(path)

    def _start_import(self, path: str, crs: UtmCrs) -> None:
        thread = QThread(self)
        worker = CsvImportWorker(path, crs)
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

    def _on_import_progress(self, rows: int, current: int, total: int) -> None:
        if self._progress_dialog is None:
            return
        percent = min(99, int(current * 100 / max(total, 1)))
        self._progress_dialog.setLabelText(
            f"Lendo {self._progress_entity}… {rows:n} linhas"
        )
        self._progress_dialog.setValue(percent)

    def _on_import_finished(self, result: CsvLoadResult) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setValue(100)
            self._progress_dialog.close()

        # Trechos e cargas referenciam o modelo anterior e só são removidos
        # depois que a nova importação de barras foi concluída com sucesso.
        self._set_load_model(None)
        self._set_line_model(None)
        self._model = result.model
        self.search_index.set_bars(result.model)
        self._sync_search_availability()
        self._selected_feature = None
        self.view.set_model(result.model)
        self.virtualizer.reset_model(result.model)
        self._set_selection(None)
        self.total_status.setText(f"Barras: {len(result.model):n}")
        self.show_bars_action.setEnabled(True)
        self.fit_action.setEnabled(True)
        self._fit_all()
        self._show_import_report(result)

    def _on_segment_import_finished(self, result: SegmentLoadResult) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setValue(100)
            self._progress_dialog.close()
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
        if self._progress_dialog is not None:
            self._progress_dialog.setValue(100)
            self._progress_dialog.close()
        if self._model is None or result.model.bars is not self._model:
            QMessageBox.critical(
                self,
                "Falha na importação",
                "As barras foram alteradas durante a importação das cargas.",
            )
            return
        self._set_load_model(result.model)
        self._show_load_import_report(result)

    def _on_load_pattern_import_finished(
        self,
        result: LoadPatternCsvResult,
    ) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setValue(100)
            self._progress_dialog.close()
        if self._load_model is None or result.model.loads is not self._load_model:
            QMessageBox.critical(
                self,
                "Falha na importação",
                "As cargas foram alteradas durante a importação dos patamares.",
            )
            return
        self._set_load_pattern_model(result.model)
        self._show_load_pattern_import_report(result)

    def _on_switch_import_finished(self, result: SwitchLoadResult) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setValue(100)
            self._progress_dialog.close()
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

    def _on_circuit_import_finished(self, result: CircuitLoadResult) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setValue(100)
            self._progress_dialog.close()
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

    def _set_line_model(self, model: LineNetworkModel | None) -> None:
        self._invalidate_branch_analysis()
        if self._search_focus_active:
            self._set_selection(None)
        self._set_circuit_catalog(None)
        self._set_switch_model(None)
        if (
            self._selected_feature is not None
            and self._selected_feature.kind == "segment"
        ):
            self._set_selection(None)
        if self._line_item is not None:
            self.scene.removeItem(self._line_item)
            self._line_item = None
        self._line_model = model
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
        self.search_index.set_segments(model)
        self._sync_search_availability()
        self.view.set_line_model(model)
        if model is not None:
            self._line_item = LineNetworkItem(model)
            self.scene.addItem(self._line_item)
        self.segment_status.setText(f"Trechos: {len(model) if model is not None else 0:n}")
        self._apply_circuit_visibility()
        self.view.viewport().update()

    def _set_load_model(self, model: LoadModel | None) -> None:
        self._invalidate_branch_analysis()
        if model is not None and model.bars is not self._model:
            raise ValueError("As cargas devem referenciar as barras exibidas.")
        if (
            self._selected_feature is not None
            and self._selected_feature.kind == "load"
        ):
            self._set_selection(None)
        self._set_load_pattern_model(None)
        self._load_model = model
        self.search_index.set_loads(model)
        self._sync_search_availability()
        self.view.set_load_model(model)
        self.load_virtualizer.reset_model(model)
        self.show_loads_action.setEnabled(model is not None)
        self.load_status.setText(f"Cargas: {len(model) if model is not None else 0:n}")
        self._apply_circuit_visibility()
        if model is not None and self.show_loads_action.isChecked():
            self.load_virtualizer.refresh(force=True)
        self.view.viewport().update()

    def _set_load_pattern_model(self, model: LoadPatternModel | None) -> None:
        if model is not None and model.loads is not self._load_model:
            raise ValueError("Os patamares devem pertencer às cargas exibidas.")
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

    def _set_switch_model(self, model: SwitchModel | None) -> None:
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
        self.search_index.set_switches(model)
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
        self.view.viewport().update()

    def _set_circuit_catalog(
        self,
        catalog: CircuitCatalogModel | None,
        checked: tuple[bool, ...] | None = None,
        colors: tuple[str, ...] | None = None,
    ) -> None:
        self._invalidate_branch_analysis()
        if self._search_focus_active:
            self._set_selection(None)
        if catalog is not None:
            if catalog.segments is not self._line_model:
                raise ValueError("Os circuitos devem pertencer aos trechos exibidos.")
            if catalog.switches is not self._switch_model:
                raise ValueError("Os circuitos devem usar as chaves exibidas.")
        self._circuit_visibility_timer.stop()
        self._circuit_catalog = catalog
        self.search_index.set_circuits(catalog)
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
        self._apply_circuit_visibility()

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
        colors = () if controller is None else controller.colors
        phase_mode = (
            self.phase_coloring_action.isChecked()
            and self._phase_classification is not None
        )
        self.view.set_feature_visibility_masks(bar_mask, segment_mask)
        self.virtualizer.set_visibility_mask(bar_mask)
        self.load_virtualizer.set_visibility_mask(load_mask)
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
        self.phase_legend.setVisible(phase_mode and self._line_item is not None)
        if self.phase_legend.isVisible():
            self._position_phase_legend()

        selection = self._selected_feature
        if selection is not None and controller is not None:
            hidden = (
                selection.kind == "bar"
                and not bool(controller.bar_visible_mask[selection.index])
            ) or (
                selection.kind == "segment"
                and not bool(controller.segment_visible_mask[selection.index])
            ) or (
                selection.kind == "load"
                and self._load_model is not None
                and not bool(
                    controller.bar_visible_mask[
                        int(self._load_model.bar_indices[selection.index])
                    ]
                )
            )
            if hidden and not self._search_focus_active:
                self._set_selection(None)
        if self.search_palette.isVisible():
            self.search_palette.refresh_results()
        self.view.viewport().update()

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

    def _sync_branches_availability(self) -> None:
        available = (
            self._circuit_catalog is not None
            and self._line_model is not None
            and self._phase_configuration is not None
            and self._import_thread is None
            and self._branch_thread is None
        )
        self.branches_action.setEnabled(available)

    def _invalidate_branch_analysis(self) -> None:
        if self._branch_worker is not None:
            self._branch_worker.cancel()
        self._branch_analysis_snapshot = None
        self._branch_analysis_result = None
        self._selected_branch = None
        self.branch_highlight_overlay.clear()
        self.branches_window.set_result(None)
        self.branches_window.hide()
        self._sync_branches_availability()

    def _show_or_analyze_branches(self) -> None:
        if self._branch_analysis_result is not None:
            self._show_branches_window()
            return
        if (
            self._circuit_catalog is None
            or self._line_model is None
            or self._phase_configuration is None
            or self._import_thread is not None
            or self._branch_thread is not None
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
        if self._branch_progress_dialog is None:
            return
        self._branch_progress_dialog.setMaximum(max(1, int(total)))
        self._branch_progress_dialog.setValue(min(int(current), max(1, int(total))))
        self._branch_progress_dialog.setLabelText(
            f"Analisando circuitos… {current:n}/{total:n}"
        )

    def _close_branch_progress(self) -> None:
        if self._branch_progress_dialog is not None:
            self._branch_progress_dialog.close()

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
        if not self._close_after_branch_analysis:
            self._show_branches_window()
            self.statusBar().showMessage(
                f"Análise concluída: {len(result.records):n} ramal(is).",
                5_000,
            )

    def _on_branch_analysis_failed(self, message: str) -> None:
        self._close_branch_progress()
        if not self._close_after_branch_analysis:
            QMessageBox.critical(
                self,
                "Falha na análise de ramais",
                message,
            )

    def _on_branch_analysis_cancelled(self) -> None:
        self._close_branch_progress()
        if not self._close_after_branch_analysis:
            self.statusBar().showMessage("Análise de ramais cancelada.", 5_000)

    def _on_branch_analysis_thread_finished(self) -> None:
        self._branch_thread = None
        self._branch_worker = None
        self._branch_progress_dialog = None
        self._branch_analysis_snapshot = None
        self.import_action.setEnabled(True)
        self._sync_branches_availability()
        if self._close_after_branch_analysis:
            self._close_after_branch_analysis = False
            self.close()

    def _show_branches_window(self) -> None:
        if self._branch_analysis_result is None:
            return
        self.branches_window.show()
        self.branches_window.raise_()
        self.branches_window.activateWindow()

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

    def _on_import_failed(self, reason: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
        QMessageBox.critical(self, "Falha na importação", reason)

    def _on_import_cancelled(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
        self.statusBar().showMessage("Importação cancelada; os dados anteriores foram mantidos.", 5_000)

    def _on_import_thread_finished(self) -> None:
        self._import_thread = None
        self._import_worker = None
        self._progress_dialog = None
        self.import_action.setEnabled(True)
        self._sync_branches_availability()
        if self._close_after_import:
            self._close_after_import = False
            self.close()

    def _fit_all(self) -> None:
        if self._model is None:
            return
        self.view.fit_model()
        self.virtualizer.refresh(force=True)
        self.load_virtualizer.refresh(force=True)

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
        if self.search_palette.isVisible():
            self._position_search_palette()
        self._position_phase_legend()

    def _show_zoom_limit_reached(self) -> None:
        self.statusBar().showMessage("Limite máximo de zoom atingido.", 3_000)

    def _sync_search_availability(self) -> None:
        available = len(self.search_index) > 0
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
        self._position_search_palette()
        self.search_palette.open()

    def _position_search_palette(self) -> None:
        viewport = self.view.viewport()
        available_width = max(1, viewport.width())
        available_height = max(1, viewport.height())
        width = min(520, max(220, available_width - 32))
        height = min(360, max(180, available_height - 32))
        left = max(0, (available_width - width) // 2)
        top = min(16, max(0, available_height - height))
        self.search_palette.setGeometry(left, top, width, height)
        self.search_palette.set_height_limit(height)

    def _is_search_result_hidden(self, result: SearchResult) -> bool:
        target = result.target
        controller = self._circuit_visibility
        if target.kind == "bar":
            if not self.view.bars_visible:
                return True
            return (
                controller is not None
                and not bool(controller.bar_visible_mask[target.index])
            )
        if target.kind == "load":
            if not self.load_virtualizer.loads_visible or self._load_model is None:
                return True
            return (
                controller is not None
                and not bool(
                    controller.bar_visible_mask[
                        int(self._load_model.bar_indices[target.index])
                    ]
                )
            )
        return (
            controller is not None
            and not bool(controller.segment_visible_mask[target.index])
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
            and self._selected_feature.kind == "load"
            and not self._search_focus_active
        ):
            self._set_selection(None)
        self.load_virtualizer.set_loads_visible(visible)
        if self.search_palette.isVisible():
            self.search_palette.refresh_results()
        state = "visíveis" if visible else "ocultas"
        self.statusBar().showMessage(f"Cargas {state}.", 3_000)

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
        if self._model is None or selection is None:
            self._selected_feature = None
            self.virtualizer.set_selected_index(None)
            self.load_virtualizer.set_selected_index(None)
            self.segment_selection_overlay.clear()
            for label in (
                *self.bar_detail_labels.values(),
                *self.load_detail_labels.values(),
                *self.segment_detail_labels.values(),
                *self.switch_detail_labels.values(),
            ):
                label.setText("—")
            self.switch_details_section.setVisible(False)
            self.details_dock.setWindowTitle("Elemento selecionado")
            self.details_stack.setCurrentWidget(self.empty_details_page)
            return

        if selection.kind == "bar":
            if not 0 <= selection.index < len(self._model):
                return
            self._selected_feature = selection
            self.segment_selection_overlay.clear()
            self.load_virtualizer.set_selected_index(None)
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

        if self._line_model is None or not 0 <= selection.index < len(self._line_model):
            return
        self._selected_feature = selection
        self.virtualizer.set_selected_index(None)
        self.load_virtualizer.set_selected_index(None)
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
        self.details_dock.setWindowTitle("Trecho selecionado")
        self.details_stack.setCurrentWidget(self.segment_details_page)
        self.segment_details_page.verticalScrollBar().setValue(0)

    def _show_coordinates(self, x: float, y: float) -> None:
        self.coordinate_status.setText(f"X: {x:.3f}   Y: {y:.3f}")

    def _update_status_counts(self, active: int) -> None:
        self._active_bar_count = int(active)
        self._refresh_active_status()

    def _update_load_status_counts(self, active: int) -> None:
        self._active_load_count = int(active)
        self._refresh_active_status()

    def _refresh_active_status(self) -> None:
        self.active_status.setText(
            f"Itens ativos: {self._active_bar_count + self._active_load_count:n}"
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
        super().closeEvent(event)
