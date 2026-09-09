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
from .source_composition import ComposedProvenance


class CircuitTableModel(QAbstractTableModel):
    """Adaptador Qt fino; o estado real permanece no controlador lógico."""

    visibilityChanged = pyqtSignal(int, bool)
    colorChanged = pyqtSignal(int, str)
    # Em blocos por entidade: o circuito, a subestação que o alimenta e o
    # transformador de onde ele sai. O bloco do transformador tem a mesma forma
    # do bloco do circuito — id, código, atributo —, que é a razão de o S_NOM
    # fechar a fila em vez de se meter entre a SE e o trafo.
    HEADERS = (
        "Visível",
        "Cor",
        "CIRC_ID",
        "BARRA_ID",
        "CODIGO",
        "VNOM",
        "CODIGO_SE",
        "NOME_SE",
        "TRAFO_ID",
        "CODIGO_TRAFO",
        "S_NOM",
        # Acrescentada no fim, e não no meio: assim ROOT_BAR_COLUMN e o
        # ``values[column - 2]`` de ``data`` seguem valendo sem tocar em nada.
        # Com uma fonte só ela fica escondida, e a janela é a de sempre.
        "Fonte",
    )

    #: Índice da coluna que diz de que banco o circuito veio.
    SOURCE_COLUMN = len(HEADERS) - 1

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.catalog: CircuitCatalogModel | None = None
        self.controller: CircuitVisibilityController | None = None
        self.provenance: ComposedProvenance | None = None

    def set_source(
        self,
        catalog: CircuitCatalogModel | None,
        controller: CircuitVisibilityController | None,
        provenance: ComposedProvenance | None = None,
    ) -> None:
        if (catalog is None) != (controller is None):
            raise ValueError("Catálogo e controlador devem ser definidos juntos.")
        if controller is not None and controller.catalog is not catalog:
            raise ValueError("O controlador deve pertencer ao catálogo informado.")
        self.beginResetModel()
        self.catalog = catalog
        self.controller = controller
        self.provenance = provenance
        self.endResetModel()

    @property
    def multi_source(self) -> bool:
        return self.provenance is not None and not self.provenance.single_source

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        if parent.isValid() or self.catalog is None:
            return 0
        return len(self.catalog)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if orientation != Qt.Orientation.Horizontal or not (
            0 <= int(section) < len(self.HEADERS)
        ):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[int(section)]
        # Sem o aviso no cabeçalho o duplo clique seria indescobrível: nada na
        # célula sugere que ela é interativa.
        if (
            role == Qt.ItemDataRole.ToolTipRole
            and int(section) == ROOT_BAR_COLUMN
        ):
            return "Barra inicial do circuito. Duplo clique enquadra a barra no mapa."
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
        if column == self.SOURCE_COLUMN:
            provenance = self.provenance
            if provenance is None:
                return "—"
            tag = provenance.tag_of("circuits", row)
            name = provenance.name_of("circuits", row)
            if role == Qt.ItemDataRole.ToolTipRole:
                native = provenance.native_id("circuits", row)
                return f"{name} — CIRC_ID no banco: {native}"
            return f"{tag} · {name}" if name else tag
        definition = self.catalog.definition(row)
        values = (
            definition.circuit_id,
            definition.root_bar_id,
            definition.code or "—",
            definition.nominal_voltage or "—",
            definition.substation_code or "—",
            definition.substation_name or "—",
            definition.transformer_id or "—",
            definition.transformer_code or "—",
            definition.transformer_power or "—",
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


# Derivada do cabeçalho para as duas definições não divergirem.
ROOT_BAR_COLUMN = CircuitTableModel.HEADERS.index("BARRA_ID")


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

    #: Índice do circuito cuja barra inicial deve ser enquadrada no mapa.
    rootBarActivated = pyqtSignal(int)

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
        # `doubleClicked`, e não `activated`: o alvo é uma célula específica, e
        # a ativação do `activated` depende da plataforma. `DoubleClicked` está
        # fora de `setEditTriggers`, então não disputa com nenhum editor.
        self.table.doubleClicked.connect(self._double_clicked)
        layout.addWidget(self.table)

        self.table_model = table_model
        table_model.modelReset.connect(self._sync_source_column)
        self._sync_source_column()

    def _sync_source_column(self) -> None:
        """A coluna "Fonte" só faz sentido quando há mais de uma."""

        self.table.setColumnHidden(
            CircuitTableModel.SOURCE_COLUMN, not self.table_model.multi_source
        )

    def _double_clicked(self, index: QModelIndex) -> None:
        """Só a coluna da barra inicial enquadra.

        O filtro de coluna é obrigatório: `CircuitColorDelegate` responde ao
        clique na coluna "Cor", e sem ele um duplo clique ali abriria o seletor
        de cores duas vezes.
        """

        if index.isValid() and index.column() == ROOT_BAR_COLUMN:
            self.rootBarActivated.emit(index.row())
