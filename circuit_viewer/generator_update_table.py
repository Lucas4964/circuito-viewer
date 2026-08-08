"""Modelos Qt somente leitura dos resultados calculados dos geradores."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from .generator_update import GeneratorDemandRecord, GeneratorPhasePowerRecord


DISPLAY_DECIMALS = 4


def _number_text(value: float, *, full: bool) -> str:
    return f"{value:.12g}" if full else f"{value:.{DISPLAY_DECIMALS}f}"


class _GeneratorResultTableModel(QAbstractTableModel):
    HEADERS: tuple[str, ...] = ()

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._records: tuple[object, ...] = ()

    @property
    def records(self) -> tuple[object, ...]:
        return self._records

    def set_records(self, records: tuple[object, ...]) -> None:
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

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role=Qt.ItemDataRole.DisplayRole,  # noqa: ANN001
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex):  # noqa: ANN201
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class GeneratorDemandTableModel(_GeneratorResultTableModel):
    HEADERS = ("NPAT", "DEMANDA")

    def set_records(self, records: tuple[GeneratorDemandRecord, ...]) -> None:
        super().set_records(records)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        record = self._records[index.row()]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        if index.column() == 0:
            return str(record.npat)
        return _number_text(
            record.demand,
            full=role == Qt.ItemDataRole.ToolTipRole,
        )


class GeneratorPhasePowerTableModel(_GeneratorResultTableModel):
    HEADERS = ("GERADOR_ID", "NPAT", "PD", "PE", "PF", "QD", "QE", "QF")

    def set_records(self, records: tuple[GeneratorPhasePowerRecord, ...]) -> None:
        super().set_records(records)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        record = self._records[index.row()]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            horizontal = (
                Qt.AlignmentFlag.AlignLeft
                if index.column() == 0
                else Qt.AlignmentFlag.AlignRight
            )
            return horizontal | Qt.AlignmentFlag.AlignVCenter
        if role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        values = (
            record.generator_id,
            record.npat,
            record.pd,
            record.pe,
            record.pf,
            record.qd,
            record.qe,
            record.qf,
        )
        value = values[index.column()]
        if index.column() <= 1:
            return str(value)
        return _number_text(
            value,
            full=role == Qt.ItemDataRole.ToolTipRole,
        )
