"""Modelo Qt somente leitura para as grandezas do fluxo de potência.

Uma linha por patamar (``NPAT`` 0–3) e uma coluna por nó do elemento. O modelo
não sabe qual grandeza está sendo exibida: recebe a matriz já escolhida pelo
combobox do painel, mais os rótulos. Isso mantém a lógica de seleção de grandeza
num lugar só — o painel — e o modelo genérico para qualquer grandeza futura.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt


# Rótulo dos nós DSS; o OpenDSS numera as fases 1, 2 e 3.
NODE_LABELS = {1: "Fase 1", 2: "Fase 2", 3: "Fase 3"}


def node_label(node: int) -> str:
    return NODE_LABELS.get(node, f"Nó {node}")


class PowerFlowTableModel(QAbstractTableModel):
    """Matriz ``[patamar][nó]`` com a primeira coluna identificando o patamar."""

    PATAMAR_HEADER = "NPAT"

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._nodes: tuple[int, ...] = ()
        self._rows: tuple[tuple[float | None, ...], ...] = ()
        self._decimals = 2

    @property
    def nodes(self) -> tuple[int, ...]:
        return self._nodes

    @property
    def rows(self) -> tuple[tuple[float | None, ...], ...]:
        return self._rows

    def set_values(
        self,
        nodes: tuple[int, ...],
        rows: tuple[tuple[float | None, ...], ...],
        *,
        decimals: int = 2,
    ) -> None:
        nodes = tuple(int(node) for node in nodes)
        values = tuple(tuple(row) for row in rows)
        for row in values:
            if len(row) != len(nodes):
                raise ValueError(
                    "Cada patamar deve ter um valor por nó informado."
                )
        self.beginResetModel()
        self._nodes = nodes
        self._rows = values
        self._decimals = int(decimals)
        self.endResetModel()

    def clear(self) -> None:
        self.set_values((), ())

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        # A primeira coluna é o número do patamar.
        return 0 if parent.isValid() else len(self._nodes) + 1

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
        return f"{value:,.{self._decimals}f}".replace(",", " ")

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
        if 1 <= section <= len(self._nodes):
            return node_label(self._nodes[section - 1])
        return None

    def flags(self, index: QModelIndex):  # noqa: ANN201
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
