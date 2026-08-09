"""Janela não modal com os resultados da análise de ramais."""

from __future__ import annotations

from decimal import Decimal

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from .branch_analysis import BranchAnalysisResult, BranchRecord
from .branch_table_export import (
    BRANCH_LENGTH_COLUMN,
    BRANCH_MAXIMUM_DEMAND_COLUMN,
    BRANCH_NUMERIC_COLUMNS,
    BRANCH_REMOVABLE_COLUMN,
    BRANCH_TABLE_HEADERS,
    branch_table_values,
)
from .equivalent_network import EquivalentNetworkResult


class BranchTableModel(QAbstractTableModel):
    HEADERS = BRANCH_TABLE_HEADERS
    NUMERIC_COLUMNS = BRANCH_NUMERIC_COLUMNS

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.result: BranchAnalysisResult | None = None
        self._maximum_demand_by_branch: dict[int, Decimal | None] = {}

    def set_result(self, result: BranchAnalysisResult | None) -> None:
        self.beginResetModel()
        self.result = result
        self._maximum_demand_by_branch = {}
        self.endResetModel()

    def set_equivalent_result(
        self,
        result: EquivalentNetworkResult | None,
    ) -> None:
        if result is not None and result.model.branches is not self.result:
            raise ValueError("A rede equivalente deve pertencer aos ramais exibidos.")
        self._maximum_demand_by_branch = (
            {}
            if result is None
            else {
                record.branch_id: record.maximum_active_demand
                for record in result.model.records
            }
        )
        if self.rowCount() > 0:
            self.dataChanged.emit(
                self.index(0, BRANCH_MAXIMUM_DEMAND_COLUMN),
                self.index(
                    self.rowCount() - 1,
                    BRANCH_MAXIMUM_DEMAND_COLUMN,
                ),
                (
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.UserRole,
                ),
            )

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        if parent.isValid() or self.result is None:
            return 0
        return len(self.result.records)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(  # noqa: ANN001, ANN201, N802
        self,
        section,
        orientation,
        role=Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= int(section) < len(self.HEADERS)
        ):
            return self.HEADERS[int(section)]
        return None

    def record(self, row: int) -> BranchRecord:
        if self.result is None or not 0 <= int(row) < len(self.result.records):
            raise IndexError(row)
        return self.result.records[int(row)]

    def _raw_values(self, record: BranchRecord) -> tuple[object, ...]:
        return branch_table_values(
            record,
            self._maximum_demand_by_branch.get(record.branch_id),
        )

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid() or self.result is None:
            return None
        record = self.record(index.row())
        value = self._raw_values(record)[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            if value is None:
                return float("inf") if index.column() in self.NUMERIC_COLUMNS else ""
            return value
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() == BRANCH_REMOVABLE_COLUMN:
                return Qt.AlignmentFlag.AlignCenter
            if index.column() in self.NUMERIC_COLUMNS:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return None
        if role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        }:
            return None
        if value is None or value == "":
            display = "—"
        elif index.column() == BRANCH_LENGTH_COLUMN:
            display = f"{float(value):.3f}"
        elif index.column() == BRANCH_MAXIMUM_DEMAND_COLUMN:
            display = (
                str(value)
                if role == Qt.ItemDataRole.ToolTipRole
                else f"{value:.4f}"
            )
        elif isinstance(value, int):
            display = f"{value:n}"
        else:
            display = str(value)
        return display


class BranchFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._circuit_id: str | None = None
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setDynamicSortFilter(True)

    def set_circuit_id(self, circuit_id: str | None) -> None:
        normalized = None if not circuit_id else str(circuit_id)
        if normalized == self._circuit_id:
            return
        self._circuit_id = normalized
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        del source_parent
        if self._circuit_id is None:
            return True
        source = self.sourceModel()
        return (
            isinstance(source, BranchTableModel)
            and source.record(source_row).circuit_id == self._circuit_id
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        source = self.sourceModel()
        if (
            isinstance(source, BranchTableModel)
            and left.column() == right.column()
            and left.column() in source.NUMERIC_COLUMNS
        ):
            left_value = source.data(left, Qt.ItemDataRole.UserRole)
            right_value = source.data(right, Qt.ItemDataRole.UserRole)
            return bool(left_value < right_value)
        return super().lessThan(left, right)


class BranchesWindow(QDialog):
    branchSelected = pyqtSignal(object)
    branchActivated = pyqtSignal(object)
    selectionCleared = pyqtSignal()
    closed = pyqtSignal()
    exportJsonRequested = pyqtSignal()
    exportCsvRequested = pyqtSignal()

    def __init__(self, table_model: BranchTableModel, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Ramais")
        self.setModal(False)
        self.resize(1_180, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._equivalent_result: EquivalentNetworkResult | None = None
        self._equivalent_pending = False
        self._json_export_pending = False
        self._csv_export_pending = False

        layout = QVBoxLayout(self)
        self.summary_label = QLabel("Execute a análise para identificar os ramais.")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Circuito:"))
        self.circuit_filter = QComboBox(self)
        self.circuit_filter.setMinimumWidth(180)
        filters.addWidget(self.circuit_filter)
        filters.addStretch(1)
        self.export_csv_button = QPushButton("Exportar CSV (Excel)", self)
        self.export_csv_button.setEnabled(False)
        self.export_csv_button.setToolTip(
            "Exportar os ramais exibidos em formato pt-BR."
        )
        filters.addWidget(self.export_csv_button)
        self.export_json_button = QPushButton("Exportar JSON", self)
        self.export_json_button.setEnabled(False)
        self.export_json_button.setToolTip(
            "Aguarde o cálculo das cargas equivalentes dos ramais."
        )
        filters.addWidget(self.export_json_button)
        layout.addLayout(filters)

        self.proxy_model = BranchFilterProxyModel(self)
        self.proxy_model.setSourceModel(table_model)
        self.table = QTableView(self)
        self.table.setObjectName("branches_table")
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.setStyleSheet("QTableView { gridline-color: palette(mid); }")
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            len(BranchTableModel.HEADERS) - 1,
            QHeaderView.ResizeMode.Stretch,
        )
        layout.addWidget(self.table, 1)

        self.issues_label = QLabel()
        self.issues_label.setWordWrap(True)
        self.issues_text = QPlainTextEdit(self)
        self.issues_text.setReadOnly(True)
        self.issues_text.setMaximumHeight(120)
        layout.addWidget(self.issues_label)
        layout.addWidget(self.issues_text)

        self.circuit_filter.currentIndexChanged.connect(self._apply_filter)
        self.export_csv_button.clicked.connect(self.exportCsvRequested)
        self.export_json_button.clicked.connect(self.exportJsonRequested)
        selection_model = self.table.selectionModel()
        selection_model.currentRowChanged.connect(self._current_row_changed)
        self.table.activated.connect(self._activate_index)

    def set_result(self, result: BranchAnalysisResult | None) -> None:
        self._equivalent_result = None
        source = self.proxy_model.sourceModel()
        assert isinstance(source, BranchTableModel)
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        source.set_result(result)
        self.circuit_filter.blockSignals(True)
        self.circuit_filter.clear()
        self.circuit_filter.addItem("Todos os circuitos", None)
        if result is not None:
            circuit_ids = sorted(
                {record.circuit_id for record in result.records},
                key=str.casefold,
            )
            for circuit_id in circuit_ids:
                self.circuit_filter.addItem(circuit_id, circuit_id)
        self.circuit_filter.blockSignals(False)
        self.proxy_model.set_circuit_id(None)

        if result is None:
            self.summary_label.setText(
                "Execute a análise para identificar os ramais."
            )
        else:
            circuit_count = len({record.circuit_id for record in result.records})
            self.summary_label.setText(
                f"{len(result.records):n} ramal(is) em {circuit_count:n} circuito(s); "
                f"{result.analyzed_circuit_count:n} circuito(s) analisado(s)."
            )
        self._refresh_issues()
        self._sync_export_availability()
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    def set_equivalent_result(
        self,
        result: EquivalentNetworkResult | None,
    ) -> None:
        self._equivalent_result = result
        source = self.proxy_model.sourceModel()
        if isinstance(source, BranchTableModel):
            source.set_equivalent_result(result)
        self._refresh_issues()
        self._sync_export_availability()

    def set_equivalent_pending(self, pending: bool) -> None:
        self._equivalent_pending = bool(pending)
        self._sync_export_availability()

    def set_json_export_pending(self, pending: bool) -> None:
        self._json_export_pending = bool(pending)
        self._sync_export_availability()

    def set_csv_export_pending(self, pending: bool) -> None:
        self._csv_export_pending = bool(pending)
        self._sync_export_availability()

    def visible_source_rows(self) -> tuple[int, ...]:
        rows: list[int] = []
        for proxy_row in range(self.proxy_model.rowCount()):
            source_index = self.proxy_model.mapToSource(
                self.proxy_model.index(proxy_row, 0)
            )
            if source_index.isValid():
                rows.append(source_index.row())
        return tuple(sorted(rows))

    def visible_source_rows_in_display_order(self) -> tuple[int, ...]:
        rows: list[int] = []
        for proxy_row in range(self.proxy_model.rowCount()):
            source_index = self.proxy_model.mapToSource(
                self.proxy_model.index(proxy_row, 0)
            )
            if source_index.isValid():
                rows.append(source_index.row())
        return tuple(rows)

    def selected_circuit_id(self) -> str | None:
        value = self.circuit_filter.currentData()
        return None if value is None else str(value)

    def _sync_export_availability(self) -> None:
        has_rows = self.proxy_model.rowCount() > 0
        export_pending = self._json_export_pending or self._csv_export_pending
        json_available = (
            self._equivalent_result is not None
            and has_rows
            and not self._equivalent_pending
            and not export_pending
        )
        csv_available = (
            has_rows
            and not self._equivalent_pending
            and not export_pending
        )
        self.export_json_button.setEnabled(json_available)
        self.export_csv_button.setEnabled(csv_available)
        if self._equivalent_pending:
            tooltip = "Calculando demandas e associações dos ramais…"
        elif export_pending:
            tooltip = "Uma exportação de ramais está em andamento."
        elif json_available:
            tooltip = "Exportar os ramais exibidos para JSON."
        else:
            tooltip = "A exportação exige ramais e cargas equivalentes calculadas."
        self.export_json_button.setToolTip(tooltip)
        if self._equivalent_pending:
            csv_tooltip = "Aguarde o cálculo das demandas dos ramais."
        elif export_pending:
            csv_tooltip = "Uma exportação de ramais está em andamento."
        elif csv_available:
            csv_tooltip = (
                "Exportar exatamente as linhas exibidas, na ordem atual, "
                "em CSV pt-BR."
            )
        else:
            csv_tooltip = "A exportação exige ao menos um ramal exibido."
        self.export_csv_button.setToolTip(csv_tooltip)

    def _refresh_issues(self) -> None:
        source = self.proxy_model.sourceModel()
        branch_result = (
            None if not isinstance(source, BranchTableModel) else source.result
        )
        issue_lines: list[str] = []
        issue_count = 0
        if branch_result is not None:
            issue_count += len(branch_result.issues) + branch_result.omitted_issue_count
            issue_lines.extend(
                f"[{issue.circuit_id}] {issue.message}"
                + (f" ({issue.segment_id})" if issue.segment_id else "")
                for issue in branch_result.issues
            )
            if branch_result.omitted_issue_count:
                issue_lines.append(
                    f"… e mais {branch_result.omitted_issue_count:n} ocorrência(s) topológica(s)."
                )
        equivalent = self._equivalent_result
        if equivalent is not None:
            issue_count += len(equivalent.issues) + equivalent.omitted_issue_count
            issue_lines.extend(
                f"[RAMAL-{issue.branch_id}] {issue.message}"
                + (f" (carga {issue.load_id})" if issue.load_id else "")
                + (
                    f" (gerador {issue.generator_id})"
                    if issue.generator_id
                    else ""
                )
                for issue in equivalent.issues
            )
            if equivalent.omitted_issue_count:
                issue_lines.append(
                    f"… e mais {equivalent.omitted_issue_count:n} ocorrência(s) de agregação."
                )
        self.issues_label.setText(
            "Nenhuma ocorrência de diagnóstico."
            if issue_count == 0
            else f"Ocorrências de diagnóstico: {issue_count:n}"
        )
        self.issues_text.setPlainText("\n".join(issue_lines))
        self.issues_text.setVisible(issue_count > 0)

    def clear_selection(self) -> None:
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        self.selectionCleared.emit()

    def _apply_filter(self) -> None:
        self.proxy_model.set_circuit_id(self.circuit_filter.currentData())
        self.clear_selection()
        self._sync_export_availability()

    def _record_for_index(self, index: QModelIndex) -> BranchRecord | None:
        if not index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(index)
        source = self.proxy_model.sourceModel()
        if not isinstance(source, BranchTableModel) or not source_index.isValid():
            return None
        return source.record(source_index.row())

    def _current_row_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        del previous
        record = self._record_for_index(current)
        if record is None:
            self.selectionCleared.emit()
        else:
            self.branchSelected.emit(record)

    def _activate_index(self, index: QModelIndex) -> None:
        record = self._record_for_index(index)
        if record is not None:
            self.branchActivated.emit(record)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.clear_selection()
        self.closed.emit()
        super().closeEvent(event)
