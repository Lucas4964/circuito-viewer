"""Modelo Qt somente leitura para os quatro patamares de uma carga."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from .model import LoadPatternRecord


class LoadPatternTableModel(QAbstractTableModel):
    HEADERS = ("CARGA_ID", "NPAT", "PD", "PE", "PF", "QD", "QE", "QF")

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._records: tuple[LoadPatternRecord, ...] = ()

    @property
    def records(self) -> tuple[LoadPatternRecord, ...]:
        return self._records

    def set_records(self, records: tuple[LoadPatternRecord, ...]) -> None:
        values = tuple(records)
        if values and (
            len(values) != 4
            or tuple(record.npat for record in values) != (0, 1, 2, 3)
        ):
            raise ValueError("A tabela exige os patamares 0, 1, 2 e 3.")
        self.beginResetModel()
        self._records = values
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        record = self._records[index.row()]
        values = (
            record.load_id,
            str(record.npat),
            record.pd,
            record.pe,
            record.pf,
            record.qd,
            record.qe,
            record.qf,
        )
        value = values[index.column()]
        if role in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        }:
            return value or "—"
        if role == Qt.ItemDataRole.TextAlignmentRole:
            horizontal = (
                Qt.AlignmentFlag.AlignLeft
                if index.column() == 0
                else Qt.AlignmentFlag.AlignRight
            )
            return horizontal | Qt.AlignmentFlag.AlignVCenter
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role=Qt.ItemDataRole.DisplayRole,  # noqa: ANN001
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(
            self.HEADERS
        ):
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex):  # noqa: ANN201
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
