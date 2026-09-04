"""Janela não modal com os blocos elétricos identificados."""

from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QPalette,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableView,
    QVBoxLayout,
)

from .block_analysis import BlockAnalysisResult, BlockRecord
from .block_table import (
    BLOCK_LENGTH_COLUMN,
    BLOCK_NUMERIC_COLUMNS,
    BLOCK_POWER_COLUMN,
    BLOCK_SWITCHES_COLUMN,
    BLOCK_TABLE_HEADERS,
    block_table_values,
    switch_list_summary,
    switch_list_text,
)


# Mesmo alfa da tabela de ramais: forte o bastante para guiar o olho ao rolar
# as colunas, fraco o bastante para não competir com a célula selecionada.
BLOCK_HIGHLIGHT_ALPHA = 50


class BlockTableModel(QAbstractTableModel):
    HEADERS = BLOCK_TABLE_HEADERS
    NUMERIC_COLUMNS = BLOCK_NUMERIC_COLUMNS

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.result: BlockAnalysisResult | None = None
        self._highlight_row = -1

    def set_result(self, result: BlockAnalysisResult | None) -> None:
        self.beginResetModel()
        self.result = result
        self._highlight_row = -1
        self.endResetModel()

    def record(self, row: int) -> BlockRecord:
        if self.result is None:
            raise IndexError(row)
        return self.result.records[row]

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        if parent.isValid() or self.result is None:
            return 0
        return len(self.result.records)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, N802
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= int(section) < len(self.HEADERS)
        ):
            return self.HEADERS[int(section)]
        return None

    @property
    def highlight_row(self) -> int:
        return self._highlight_row

    def set_highlight_row(self, row: int) -> None:
        normalized = int(row)
        if not 0 <= normalized < self.rowCount():
            normalized = -1
        if normalized == self._highlight_row:
            return
        previous = self._highlight_row
        self._highlight_row = normalized
        for changed in (previous, normalized):
            if changed < 0:
                continue
            self.dataChanged.emit(
                self.index(changed, 0),
                self.index(changed, len(self.HEADERS) - 1),
                [Qt.ItemDataRole.BackgroundRole],
            )

    @staticmethod
    def _highlight_brush() -> QBrush:
        color = QPalette().color(QPalette.ColorRole.Highlight)
        color.setAlpha(BLOCK_HIGHLIGHT_ALPHA)
        return QBrush(color)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201
        if not index.isValid() or self.result is None:
            return None
        if role == Qt.ItemDataRole.BackgroundRole:
            if index.row() != self._highlight_row:
                return None
            return self._highlight_brush()
        record = self.record(index.row())
        column = index.column()
        value = block_table_values(record)[column]

        # UserRole é a chave de ordenação: valor cru, para o proxy comparar
        # grandeza e não texto formatado.
        if role == Qt.ItemDataRole.UserRole:
            if column == BLOCK_SWITCHES_COLUMN:
                return switch_list_text(record)
            if value is None:
                return float("inf") if column in self.NUMERIC_COLUMNS else ""
            return value
        # EditRole é o que o Ctrl+C leva. Só a coluna de chaves difere do que se
        # vê: ela é abreviada na tela e precisa ser copiada inteira.
        if role == Qt.ItemDataRole.EditRole:
            if column == BLOCK_SWITCHES_COLUMN:
                return switch_list_text(record)
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in self.NUMERIC_COLUMNS:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return None
        if role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        }:
            return None

        if column == BLOCK_SWITCHES_COLUMN:
            # Abreviada na tela, inteira no tooltip: o mesmo par que a coluna de
            # demanda máxima dos ramais já usa. NUM_CHAVES, ao lado, diz quantas
            # são, então a reticência só precisa avisar que há continuação.
            full = switch_list_text(record)
            if role == Qt.ItemDataRole.ToolTipRole:
                return full or "—"
            return switch_list_summary(record) or "—"
        if value is None or value == "":
            return "—"
        if column in {BLOCK_POWER_COLUMN, BLOCK_LENGTH_COLUMN}:
            return f"{float(value):n}" if role == Qt.ItemDataRole.ToolTipRole else (
                f"{float(value):.2f}"
            )
        if isinstance(value, int):
            return f"{value:n}"
        return str(value)


class BlockSortProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setDynamicSortFilter(True)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        source = self.sourceModel()
        if (
            isinstance(source, BlockTableModel)
            and left.column() == right.column()
            and left.column() in source.NUMERIC_COLUMNS
        ):
            left_value = source.data(left, Qt.ItemDataRole.UserRole)
            right_value = source.data(right, Qt.ItemDataRole.UserRole)
            return bool(left_value < right_value)
        return super().lessThan(left, right)


class BlockTableView(QTableView):
    """Tabela somente leitura com cópia tabular compatível com planilhas.

    A cópia prefere o ``EditRole`` ao ``DisplayRole``: é o que faz o ``Ctrl+C``
    na coluna de chaves levar a lista inteira em vez da reticência que aparece
    na tela. Nas demais colunas o ``EditRole`` é nulo e o texto exibido vale.
    """

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_selection(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:  # pragma: no cover - depende da plataforma
            return
        indexes = tuple(self.selectedIndexes())
        if not indexes:
            return
        selected = {(index.row(), index.column()) for index in indexes}
        first_row = min(index.row() for index in indexes)
        last_row = max(index.row() for index in indexes)
        first_column = min(index.column() for index in indexes)
        last_column = max(index.column() for index in indexes)
        model = self.model()
        if model is None:
            return
        lines: list[str] = []
        for row in range(first_row, last_row + 1):
            cells: list[str] = []
            for column in range(first_column, last_column + 1):
                if (row, column) not in selected:
                    cells.append("")
                    continue
                index = model.index(row, column)
                value = model.data(index, Qt.ItemDataRole.EditRole)
                if value is None:
                    value = model.data(index, Qt.ItemDataRole.DisplayRole)
                cells.append("" if value is None else str(value))
            lines.append("\t".join(cells))
        clipboard.setText("\n".join(lines))


class BlocksWindow(QDialog):
    """Os blocos da rede: as regiões que uma manobra isola."""

    blockSelected = pyqtSignal(object)
    blockActivated = pyqtSignal(object)
    selectionCleared = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, table_model: BlockTableModel, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Blocos")
        self.setModal(False)
        self.resize(900, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel(
            "Importe uma rede com chaves para identificar os blocos."
        )
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.proxy_model = BlockSortProxyModel(self)
        self.proxy_model.setSourceModel(table_model)
        self.table = BlockTableView(self)
        self.table.setObjectName("blocks_table")
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
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
            len(BlockTableModel.HEADERS) - 1,
            QHeaderView.ResizeMode.Stretch,
        )
        layout.addWidget(self.table, 1)

        self.issues_label = QLabel()
        self.issues_label.setWordWrap(True)
        self.issues_text = QPlainTextEdit(self)
        self.issues_text.setReadOnly(True)
        self.issues_text.setMaximumHeight(90)
        layout.addWidget(self.issues_label)
        layout.addWidget(self.issues_text)

        selection_model = self.table.selectionModel()
        selection_model.currentRowChanged.connect(self._current_row_changed)
        self.table.activated.connect(self._activate_index)

    def set_result(self, result: BlockAnalysisResult | None) -> None:
        source = self.proxy_model.sourceModel()
        assert isinstance(source, BlockTableModel)
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        source.set_result(result)

        if result is None:
            self.summary_label.setText(
                "Importe uma rede com chaves para identificar os blocos."
            )
            self.issues_label.setText("")
            self.issues_text.setPlainText("")
            self.issues_text.setVisible(False)
            return

        dead_ends = sum(1 for record in result.records if record.is_dead_end)
        self.summary_label.setText(
            f"{len(result.records):n} bloco(s) delimitados por "
            f"{result.switchable_switch_count:n} chave(s) manobrável(is). "
            f"{dead_ends:n} bloco(s) com fronteira única — isoláveis, mas sem "
            "alternativa de transferência."
        )
        issue_lines = [
            f"{issue.kind}: {issue.message}" for issue in result.issues
        ]
        if result.omitted_issue_count:
            issue_lines.append(
                f"… e mais {result.omitted_issue_count:n} ocorrência(s)."
            )
        self.issues_label.setText(
            "Nenhuma ocorrência de diagnóstico."
            if not issue_lines
            else f"Ocorrências de diagnóstico: {len(result.issues):n}"
        )
        self.issues_text.setPlainText("\n".join(issue_lines))
        self.issues_text.setVisible(bool(issue_lines))

    def select_block(self, block_id: int) -> bool:
        """Põe a linha do bloco em foco, seguindo a ordenação corrente.

        A tradução fonte → proxy é obrigatória pelo mesmo motivo do realce: a
        tabela ordena, e a linha do modelo não é a que está na tela.
        """

        source = self.proxy_model.sourceModel()
        if not isinstance(source, BlockTableModel) or source.result is None:
            return False
        row = next(
            (
                index
                for index, record in enumerate(source.result.records)
                if record.block_id == int(block_id)
            ),
            None,
        )
        if row is None:
            return False
        proxy_index = self.proxy_model.mapFromSource(source.index(row, 0))
        if not proxy_index.isValid():
            return False
        self.table.setCurrentIndex(proxy_index)
        self.table.selectRow(proxy_index.row())
        self.table.scrollTo(proxy_index)
        return True

    def clear_selection(self) -> None:
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        self._highlight_source_row(QModelIndex())
        self.selectionCleared.emit()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        """Esc desfaz um nível por vez, como no mapa.

        Havendo linha selecionada, o Esc a desmarca — e o destaque some pelo
        caminho que já existe. Não havendo, o evento segue para o ``QDialog``,
        que fecha a janela como sempre fez. O fechar-com-Esc não se perde; ele
        só passa a ser o segundo passo.
        """

        if (
            event.key() == Qt.Key.Key_Escape
            and self.table.currentIndex().isValid()
        ):
            self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def _record_for_index(self, index: QModelIndex) -> BlockRecord | None:
        if not index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(index)
        source = self.proxy_model.sourceModel()
        if not isinstance(source, BlockTableModel) or not source_index.isValid():
            return None
        return source.record(source_index.row())

    def _highlight_source_row(self, index: QModelIndex) -> None:
        """Traduz a linha visível para a linha fonte antes de destacá-la.

        Obrigatório porque a tabela ordena: a linha do proxy não corresponde à
        do modelo fonte.
        """

        source = self.proxy_model.sourceModel()
        if not isinstance(source, BlockTableModel):
            return
        source_index = self.proxy_model.mapToSource(index)
        source.set_highlight_row(
            source_index.row() if source_index.isValid() else -1
        )

    def _current_row_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        del previous
        record = self._record_for_index(current)
        self._highlight_source_row(current)
        if record is None:
            self.selectionCleared.emit()
        else:
            self.blockSelected.emit(record)

    def _activate_index(self, index: QModelIndex) -> None:
        record = self._record_for_index(index)
        if record is not None:
            self.blockActivated.emit(record)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.clear_selection()
        self.closed.emit()
        super().closeEvent(event)


__all__ = [
    "BLOCK_HIGHLIGHT_ALPHA",
    "BlockSortProxyModel",
    "BlockTableModel",
    "BlockTableView",
    "BlocksWindow",
]
