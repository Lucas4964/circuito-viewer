"""Janela não modal com os resultados da análise de ramais."""

from __future__ import annotations

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
    QTableView,
    QVBoxLayout,
)

from .branch_analysis import BranchAnalysisResult, BranchRecord


class BranchTableModel(QAbstractTableModel):
    HEADERS = (
        "CIRC_ID",
        "BARRA_ID",
        "BARRA_CODIGO",
        "TRECHO_ID",
        "TRECHO_CODIGO",
        "NUM_TRECHOS",
        "COMPR",
        "NUM_CARGAS",
        "FASE",
        "REMANEJAVEL",
        "NUM_BARRAS",
        "NUM_CHAVES",
        "POS_PRIMEIRA_CHAVE",
        "NUM_CONEXOES_TRONCO",
        "NUM_COMPR_AUSENTE",
        "TOPOLOGIA",
    )
    NUMERIC_COLUMNS = frozenset({5, 6, 7, 9, 10, 11, 12, 13, 14})

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.result: BranchAnalysisResult | None = None

    def set_result(self, result: BranchAnalysisResult | None) -> None:
        self.beginResetModel()
        self.result = result
        self.endResetModel()

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

    @staticmethod
    def _raw_values(record: BranchRecord) -> tuple[object, ...]:
        return (
            record.circuit_id,
            record.connection_bar_id,
            record.connection_bar_code,
            record.first_segment_id,
            record.first_segment_code,
            record.segment_count,
            record.total_length,
            record.load_count,
            record.phase,
            int(record.removable),
            record.bar_count,
            record.switch_count,
            record.first_switch_position,
            record.trunk_connection_count,
            record.missing_length_count,
            record.topology,
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
            if index.column() == 9:
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
        elif index.column() == 6:
            display = f"{float(value):.3f}"
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


class BranchesWindow(QDialog):
    branchSelected = pyqtSignal(object)
    branchActivated = pyqtSignal(object)
    selectionCleared = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, table_model: BranchTableModel, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Ramais")
        self.setModal(False)
        self.resize(1_180, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

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
        selection_model = self.table.selectionModel()
        selection_model.currentRowChanged.connect(self._current_row_changed)
        self.table.activated.connect(self._activate_index)

    def set_result(self, result: BranchAnalysisResult | None) -> None:
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
            issue_count = 0
            issue_lines: list[str] = []
        else:
            circuit_count = len({record.circuit_id for record in result.records})
            self.summary_label.setText(
                f"{len(result.records):n} ramal(is) em {circuit_count:n} circuito(s); "
                f"{result.analyzed_circuit_count:n} circuito(s) analisado(s)."
            )
            issue_count = len(result.issues) + result.omitted_issue_count
            issue_lines = [
                f"[{issue.circuit_id}] {issue.message}"
                + (f" ({issue.segment_id})" if issue.segment_id else "")
                for issue in result.issues
            ]
            if result.omitted_issue_count:
                issue_lines.append(
                    f"… e mais {result.omitted_issue_count:n} ocorrência(s)."
                )
        self.issues_label.setText(
            "Nenhuma ocorrência de diagnóstico."
            if issue_count == 0
            else f"Ocorrências de diagnóstico: {issue_count:n}"
        )
        self.issues_text.setPlainText("\n".join(issue_lines))
        self.issues_text.setVisible(issue_count > 0)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    def clear_selection(self) -> None:
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        self.selectionCleared.emit()

    def _apply_filter(self) -> None:
        self.proxy_model.set_circuit_id(self.circuit_filter.currentData())
        self.clear_selection()

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
