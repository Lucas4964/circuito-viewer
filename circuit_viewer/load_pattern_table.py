"""Modelo Qt somente leitura para os quatro patamares de uma carga."""

from __future__ import annotations

from decimal import Decimal

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from .opendss_export import parse_number

# Arredondamento **só de exibição**: o valor guardado no registro continua
# intacto, e o tooltip devolve a precisão cheia para conferência.
DISPLAY_DECIMALS = 4

# CARGA_ID e NPAT são identificadores, não grandezas — nunca são formatados.
_FIRST_VALUE_COLUMN = 2


def _display_text(value: object) -> str:
    """Formata uma grandeza de patamar com casas decimais fixas.

    O modelo atende dois registros diferentes: os patamares importados guardam
    o texto cru do CSV (que pode vir com vírgula decimal, ou nem ser número), e
    os patamares equivalentes guardam ``Decimal`` somado em precisão alta. Só o
    que for reconhecido como número é arredondado; o resto aparece como está,
    porque esconder um valor que o usuário digitou seria pior que exibi-lo.
    """

    if isinstance(value, Decimal):
        return f"{value:.{DISPLAY_DECIMALS}f}"
    # ``parse_number`` é a regra única de separador decimal do projeto; duplicá-la
    # aqui abriria espaço para a tabela e a exportação discordarem.
    number = parse_number(value) if isinstance(value, str) else None
    if number is None:
        return str(value)
    return f"{number:.{DISPLAY_DECIMALS}f}"


class LoadPatternTableModel(QAbstractTableModel):
    HEADERS = ("CARGA_ID", "NPAT", "PD", "PE", "PF", "QD", "QE", "QF")

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
            if value is None or value == "":
                return "—"
            if (
                role == Qt.ItemDataRole.DisplayRole
                and index.column() >= _FIRST_VALUE_COLUMN
            ):
                return _display_text(value)
            return str(value)
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
