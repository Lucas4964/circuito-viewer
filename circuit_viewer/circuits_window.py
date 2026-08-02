"""Tabela modeless para controle da visibilidade dos circuitos."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QHeaderView,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
)

from .model import CircuitCatalogModel, CircuitVisibilityController


class CircuitTableModel(QAbstractTableModel):
    """Adaptador Qt fino; o estado real permanece no controlador lógico."""

    visibilityChanged = pyqtSignal(int, bool)
    colorChanged = pyqtSignal(int, str)
    HEADERS = ("Visível", "Cor", "CIRC_ID", "BARRA_ID", "CODIGO", "VNOM")

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.catalog: CircuitCatalogModel | None = None
        self.controller: CircuitVisibilityController | None = None

    def set_source(
        self,
        catalog: CircuitCatalogModel | None,
        controller: CircuitVisibilityController | None,
    ) -> None:
        if (catalog is None) != (controller is None):
            raise ValueError("Catálogo e controlador devem ser definidos juntos.")
        if controller is not None and controller.catalog is not catalog:
            raise ValueError("O controlador deve pertencer ao catálogo informado.")
        self.beginResetModel()
        self.catalog = catalog
        self.controller = controller
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        if parent.isValid() or self.catalog is None:
            return 0
        return len(self.catalog)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= int(section) < len(self.HEADERS)
        ):
            return self.HEADERS[int(section)]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid() or self.catalog is None or self.controller is None:
            return None
        row = index.row()
        column = index.column()
        if column == 0:
            if role == Qt.ItemDataRole.CheckStateRole:
                return (
                    Qt.CheckState.Checked
                    if self.controller.is_visible(row)
                    else Qt.CheckState.Unchecked
                )
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter
            return None
        if column == 1:
            color = self.controller.color(row)
            if role in {
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.ToolTipRole,
                Qt.ItemDataRole.UserRole,
            }:
                return color
            if role == Qt.ItemDataRole.DecorationRole:
                sample = QPixmap(32, 14)
                sample.fill(QColor(color))
                return sample
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter
            return None
        if role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        }:
            return None
        definition = self.catalog.definition(row)
        values = (
            definition.circuit_id,
            definition.root_bar_id,
            definition.code or "—",
            definition.nominal_voltage or "—",
        )
        return values[column - 2]

    def flags(self, index: QModelIndex):  # noqa: ANN201
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        elif index.column() == 1:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: ANN001, N802
        if not index.isValid() or self.controller is None:
            return False
        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            checked = value in {Qt.CheckState.Checked, Qt.CheckState.Checked.value}
            if not self.controller.set_visible(index.row(), checked):
                return False
            self.dataChanged.emit(
                index,
                index,
                [Qt.ItemDataRole.CheckStateRole],
            )
            self.visibilityChanged.emit(index.row(), checked)
            return True
        if index.column() == 1 and role == Qt.ItemDataRole.EditRole:
            try:
                changed = self.controller.set_color(index.row(), str(value))
            except ValueError:
                return False
            if not changed:
                return False
            color = self.controller.color(index.row())
            self.dataChanged.emit(
                index,
                index,
                [
                    Qt.ItemDataRole.DisplayRole,
                    Qt.ItemDataRole.DecorationRole,
                    Qt.ItemDataRole.UserRole,
                ],
            )
            self.colorChanged.emit(index.row(), color)
            return True
        return False


class CircuitColorDelegate(QStyledItemDelegate):
    """Amostra a cor e abre o seletor nativo diretamente na célula."""

    def choose_color(self, initial: QColor, parent) -> QColor:  # noqa: ANN001
        return QColorDialog.getColor(
            initial,
            parent,
            "Escolher cor do circuito",
        )

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: ANN001, N802
        del option
        activate = False
        if event.type() == QEvent.Type.MouseButtonRelease:
            mouse_event = event
            if isinstance(mouse_event, QMouseEvent):
                activate = mouse_event.button() == Qt.MouseButton.LeftButton
        elif event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent):
                activate = key_event.key() in {
                    Qt.Key.Key_Return,
                    Qt.Key.Key_Enter,
                    Qt.Key.Key_Space,
                }
        if not activate:
            return False
        initial = QColor(str(index.data(Qt.ItemDataRole.UserRole)))
        parent = self.parent()
        selected = self.choose_color(initial, parent)
        if not selected.isValid():
            return True
        model.setData(index, selected.name().upper(), Qt.ItemDataRole.EditRole)
        return True


class CircuitsWindow(QDialog):
    """Janela não modal e reutilizável para a tabela de circuitos."""

    def __init__(self, table_model: CircuitTableModel, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Circuitos")
        self.setModal(False)
        self.resize(820, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        self.table = QTableView(self)
        self.table.setObjectName("circuits_table")
        self.table.setModel(table_model)
        self.color_delegate = CircuitColorDelegate(self.table)
        self.table.setItemDelegateForColumn(1, self.color_delegate)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.CurrentChanged
        )
        self.table.setStyleSheet("QTableView { gridline-color: palette(mid); }")
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
