"""Relatório modeless de trechos associados a vários circuitos."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
)

from .model import CircuitCatalogModel


class OverlapReportTableModel(QAbstractTableModel):
    HEADERS = ("TRECHO_ID", "Quantidade de circuitos", "CIRC_IDs")

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.catalog: CircuitCatalogModel | None = None

    def set_catalog(self, catalog: CircuitCatalogModel | None) -> None:
        self.beginResetModel()
        self.catalog = catalog
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        if parent.isValid() or self.catalog is None:
            return 0
        return int(self.catalog.overlapping_segment_indices.size)

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

    def segment_index(self, row: int) -> int:
        if self.catalog is None or not 0 <= int(row) < self.rowCount():
            raise IndexError(row)
        return int(self.catalog.overlapping_segment_indices[int(row)])

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if (
            not index.isValid()
            or self.catalog is None
            or role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}
        ):
            return None
        segment_index = self.segment_index(index.row())
        owners = self.catalog.circuit_indices_for_segment(segment_index)
        circuit_ids = tuple(
            self.catalog.definition(int(owner)).circuit_id for owner in owners
        )
        values = (
            self.catalog.segments.segment_ids[segment_index],
            str(len(circuit_ids)),
            ", ".join(circuit_ids),
        )
        return values[index.column()]


class CircuitOverlapReportWindow(QDialog):
    """Janela reutilizável com os casos que demandam atenção."""

    def __init__(
        self,
        table_model: OverlapReportTableModel,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sobreposições de circuitos")
        self.setModal(False)
        self.resize(760, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel(
            "Trechos associados a mais de um circuito. Verifique estes casos."
        )
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.table = QTableView(self)
        self.table.setObjectName("circuit_overlap_table")
        self.table.setModel(table_model)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setStyleSheet("QTableView { gridline-color: palette(mid); }")
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

    def update_summary(self, count: int) -> None:
        self.summary_label.setText(
            f"{count:n} trecho(s) pertencem a mais de um circuito e demandam atenção."
        )
