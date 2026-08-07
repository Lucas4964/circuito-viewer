"""Modelo Qt somente leitura para as grandezas do fluxo de potência.

Uma linha por patamar (``NPAT`` 0–3) e uma coluna por grandeza do elemento. O
modelo não sabe qual grandeza está sendo exibida: recebe a matriz já escolhida
pelo combobox do painel, mais os rótulos. Isso mantém a lógica de seleção de
grandeza num lugar só — o painel — e o modelo genérico para qualquer grandeza
futura.

Os rótulos chegam **prontos**, e não como número de nó, porque nem toda coluna
corresponde a um nó: a tensão de linha é indexada por par de fases (``VDE``) e o
ângulo por fase (``θD``). Traduzir nó em letra exige a configuração de fases, que
é assunto do painel.
"""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt


class PowerFlowTableModel(QAbstractTableModel):
    """Matriz ``[patamar][coluna]`` com a primeira coluna identificando o patamar."""

    PATAMAR_HEADER = "NPAT"

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._labels: tuple[str, ...] = ()
        self._rows: tuple[tuple[float | None, ...], ...] = ()
        self._decimals: tuple[int, ...] = ()

    @property
    def labels(self) -> tuple[str, ...]:
        return self._labels

    @property
    def rows(self) -> tuple[tuple[float | None, ...], ...]:
        return self._rows

    def set_values(
        self,
        labels: Sequence[str],
        rows: Sequence[Sequence[float | None]],
        *,
        decimals: int | Sequence[int] = 4,
    ) -> None:
        """Instala rótulos, valores e casas decimais.

        ``decimals`` aceita um número — aplicado a todas as colunas — ou um por
        coluna, porque módulo e ângulo dividem a mesma tabela e não têm a mesma
        precisão útil.
        """

        headers = tuple(str(label) for label in labels)
        values = tuple(tuple(row) for row in rows)
        for row in values:
            if len(row) != len(headers):
                raise ValueError(
                    "Cada patamar deve ter um valor por coluna informada."
                )
        if isinstance(decimals, int):
            places = (int(decimals),) * len(headers)
        else:
            places = tuple(int(value) for value in decimals)
            if len(places) != len(headers):
                raise ValueError(
                    "As casas decimais devem ter um valor por coluna informada."
                )
        self.beginResetModel()
        self._labels = headers
        self._rows = values
        self._decimals = places
        self.endResetModel()

    def clear(self) -> None:
        self.set_values((), ())

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        # A primeira coluna é o número do patamar.
        return 0 if parent.isValid() else len(self._labels) + 1

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        column = index.column()
        if role == Qt.ItemDataRole.TextAlignmentRole:
            horizontal = (
                Qt.AlignmentFlag.AlignLeft
                if column == 0
                else Qt.AlignmentFlag.AlignRight
            )
            return horizontal | Qt.AlignmentFlag.AlignVCenter
        if role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        }:
            return None
        if column == 0:
            return str(index.row())
        value = self._rows[index.row()][column - 1]
        if value is None:
            return "—"
        return f"{value:,.{self._decimals[column - 1]}f}".replace(",", " ")

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role=Qt.ItemDataRole.DisplayRole,  # noqa: ANN001
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation != Qt.Orientation.Horizontal:
            return None
        if section == 0:
            return self.PATAMAR_HEADER
        if 1 <= section <= len(self._labels):
            return self._labels[section - 1]
        return None

    def flags(self, index: QModelIndex):  # noqa: ANN201
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
