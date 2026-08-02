"""Janela principal do visualizador."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PyQt6.QtWidgets import (
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
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .circuit_import import CircuitLoadResult
from .circuits_window import CircuitTableModel, CircuitsWindow
from .csv_import import CsvLoadResult
from .graphics import (
    DiagramView,
    ItemVirtualizer,
    LineNetworkItem,
    SegmentSelectionOverlayItem,
    SwitchNetworkItem,
)
from .model import (
    CircuitCatalogModel,
    CircuitModel,
    CircuitVisibilityController,
    FeatureSelection,
    LineNetworkModel,
    SwitchModel,
    UtmCrs,
)
from .overlap_report import CircuitOverlapReportWindow, OverlapReportTableModel
from .segment_import import SegmentLoadResult
from .switch_import import SwitchLoadResult
from .workers import (
    CircuitImportWorker,
    CsvImportWorker,
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
                "Importe as barras antes de importar os trechos da rede."
            )
            dependency.setWordWrap(True)
            layout.addWidget(dependency)
        elif not has_segments:
            dependency = QLabel(
                "Importe os trechos antes de importar as chaves ou os circuitos."
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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Visualizador de Circuitos Elétricos")
        self.resize(1280, 800)

        self._model: CircuitModel | None = None
        self._line_model: LineNetworkModel | None = None
        self._line_item: LineNetworkItem | None = None
        self._switch_model: SwitchModel | None = None
        self._switch_item: SwitchNetworkItem | None = None
        self._circuit_catalog: CircuitCatalogModel | None = None
        self._circuit_visibility: CircuitVisibilityController | None = None
        self._selected_feature: FeatureSelection | None = None
        self._import_thread: QThread | None = None
        self._import_worker: (
            CsvImportWorker
            | SegmentImportWorker
            | SwitchImportWorker
            | CircuitImportWorker
            | None
        ) = None
        self._progress_dialog: QProgressDialog | None = None
        self._progress_entity = "registros"
        self._close_after_import = False

        self.scene = QGraphicsScene(self)
        self.scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.view = DiagramView(self.scene, self)
        self.setCentralWidget(self.view)
        self.virtualizer = ItemVirtualizer(self.scene, self.view, parent=self)
        self.segment_selection_overlay = SegmentSelectionOverlayItem()
        self.scene.addItem(self.segment_selection_overlay)

        self.circuit_table_model = CircuitTableModel(self)
        self.circuits_window = CircuitsWindow(self.circuit_table_model, self)
        self.overlap_table_model = OverlapReportTableModel(self)
        self.overlap_report_window = CircuitOverlapReportWindow(
            self.overlap_table_model,
            self,
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

    def _create_actions(self) -> None:
        self.import_action = QAction("Importar…", self)
        self.import_action.setShortcut(QKeySequence.StandardKey.Open)
        self.import_action.setToolTip(
            "Importar barras, trechos, chaves ou circuitos de arquivos CSV"
        )
        self.import_action.triggered.connect(self._choose_import)

        self.fit_action = QAction("Enquadrar tudo", self)
        self.fit_action.setShortcut(QKeySequence("F"))
        self.fit_action.setEnabled(False)
        self.fit_action.triggered.connect(self._fit_all)

        self.show_bars_action = QAction("Mostrar barras", self)
        self.show_bars_action.setCheckable(True)
        self.show_bars_action.setChecked(True)
        self.show_bars_action.setEnabled(False)
        self.show_bars_action.toggled.connect(self._set_bars_visible)

        self.circuits_action = QAction("Circuitos…", self)
        self.circuits_action.setEnabled(False)
        self.circuits_action.triggered.connect(self._show_circuits_window)

        self.overlaps_action = QAction("Sobreposições…", self)
        self.overlaps_action.setEnabled(False)
        self.overlaps_action.triggered.connect(self._show_overlap_report)

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
        view_menu.addAction(self.circuits_action)
        view_menu.addAction(self.overlaps_action)
        view_menu.addSeparator()
        view_menu.addAction(self.fit_action)

        toolbar = QToolBar("Ferramentas principais", self)
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.addAction(self.import_action)
        toolbar.addSeparator()
        toolbar.addAction(self.select_action)
        toolbar.addAction(self.pan_action)
        toolbar.addAction(self.fit_action)
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
        empty_message = QLabel("Clique em uma barra ou trecho para ver seus dados.")
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
        self.active_status = QLabel("Itens ativos: 0")
        self.mode_status = QLabel("Visão geral")
        self.overlap_status = QLabel("Sobreposições: 0")
        status = self.statusBar()
        status.addWidget(self.coordinate_status, 1)
        status.addPermanentWidget(self.total_status)
        status.addPermanentWidget(self.segment_status)
        status.addPermanentWidget(self.overlap_status)
        status.addPermanentWidget(self.active_status)
        status.addPermanentWidget(self.mode_status)

    def _connect_signals(self) -> None:
        self.view.selectionRequested.connect(self._set_selection)
        self.view.mouseCoordinateChanged.connect(self._show_coordinates)
        self.virtualizer.countsChanged.connect(self._update_status_counts)
        self.virtualizer.modeChanged.connect(self.mode_status.setText)
        self.circuit_table_model.visibilityChanged.connect(
            self._schedule_circuit_visibility_update
        )
        self.circuit_table_model.colorChanged.connect(
            self._schedule_circuit_visibility_update
        )

    def _choose_import(self) -> None:
        if self._import_thread is not None:
            return
        dialog = ImportChoiceDialog(
            self._model is not None,
            self._line_model is not None,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.selected_kind == "bars":
            self._choose_csv()
        elif dialog.selected_kind == "segments":
            self._choose_segments_csv()
        elif dialog.selected_kind == "switches":
            self._choose_switches_csv()
        elif dialog.selected_kind == "circuits":
            self._choose_circuits_csv()

    def _choose_csv(self) -> None:
        if self._import_thread is not None:
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
        if self._import_thread is not None or self._model is None:
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
        if self._import_thread is not None or self._line_model is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar chaves",
            "",
            "Arquivos CSV (*.csv);;Todos os arquivos (*)",
        )
        if path:
            self._start_switch_import(path)

    def _choose_circuits_csv(self) -> None:
        if self._import_thread is not None or self._line_model is None:
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

        # Os trechos existentes referenciam o modelo anterior e só são removidos
        # depois que a nova importação de barras foi concluída com sucesso.
        self._set_line_model(None)
        self._model = result.model
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
        self.view.set_line_model(model)
        if model is not None:
            self._line_item = LineNetworkItem(model)
            self.scene.addItem(self._line_item)
        self.segment_status.setText(f"Trechos: {len(model) if model is not None else 0:n}")
        self.view.viewport().update()

    def _set_switch_model(self, model: SwitchModel | None) -> None:
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
        self.view.viewport().update()

    def _set_circuit_catalog(
        self,
        catalog: CircuitCatalogModel | None,
        checked: tuple[bool, ...] | None = None,
        colors: tuple[str, ...] | None = None,
    ) -> None:
        if catalog is not None:
            if catalog.segments is not self._line_model:
                raise ValueError("Os circuitos devem pertencer aos trechos exibidos.")
            if catalog.switches is not self._switch_model:
                raise ValueError("Os circuitos devem usar as chaves exibidas.")
        self._circuit_visibility_timer.stop()
        self._circuit_catalog = catalog
        self._circuit_visibility = (
            None
            if catalog is None
            else CircuitVisibilityController(catalog, checked, colors)
        )
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
        colors = () if controller is None else controller.colors
        self.view.set_feature_visibility_masks(bar_mask, segment_mask)
        self.virtualizer.set_visibility_mask(bar_mask)
        if self._line_item is not None:
            self._line_item.set_circuit_rendering(
                segment_mask,
                segment_styles,
                colors,
            )
        if self._switch_item is not None:
            self._switch_item.set_circuit_rendering(
                segment_mask,
                segment_styles,
                colors,
            )

        selection = self._selected_feature
        if selection is not None and controller is not None:
            hidden = (
                selection.kind == "bar"
                and not bool(controller.bar_visible_mask[selection.index])
            ) or (
                selection.kind == "segment"
                and not bool(controller.segment_visible_mask[selection.index])
            )
            if hidden:
                self._set_selection(None)
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
        if self._close_after_import:
            self._close_after_import = False
            self.close()

    def _fit_all(self) -> None:
        if self._model is None:
            return
        self.view.fit_model()
        self.virtualizer.refresh(force=True)

    def _set_bars_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if (
            not visible
            and self._selected_feature is not None
            and self._selected_feature.kind == "bar"
        ):
            self._set_selection(None)
        self.view.set_bars_visible(visible)
        self.virtualizer.set_bars_visible(visible)
        state = "visíveis" if visible else "ocultas"
        self.statusBar().showMessage(f"Barras {state}.", 3_000)

    def _set_selection(self, selection: FeatureSelection | None) -> None:
        if self._model is None or selection is None:
            self._selected_feature = None
            self.virtualizer.set_selected_index(None)
            self.segment_selection_overlay.clear()
            for label in (
                *self.bar_detail_labels.values(),
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
            self.virtualizer.set_selected_index(selection.index)
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

        if self._line_model is None or not 0 <= selection.index < len(self._line_model):
            return
        self._selected_feature = selection
        self.virtualizer.set_selected_index(None)
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
        self.active_status.setText(f"Itens ativos: {active:n}")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._import_thread is not None and self._import_thread.isRunning():
            if self._import_worker is not None:
                self._import_worker.cancel()
            self._close_after_import = True
            event.ignore()
            return
        super().closeEvent(event)
