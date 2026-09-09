"""Revisão de conflitos e correspondências explícitas entre bancos."""

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QStyledItemDelegate, QTableView, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from .network_registry import IdentityMapping


ENTITY_NAMES = {
    "bars": "Barras", "segments": "Trechos", "switches": "Chaves", "circuits": "Circuitos",
    "loads": "Cargas", "capacitors": "Capacitores", "generators": "Geradores",
    "regulators": "Reguladores", "cables": "Cabos", "patterns": "Patamares de cargas",
    "allocations": "Alocações", "circuit_levels": "Patamares de circuitos",
}
DECISIONS = (("Escolha…", None), ("Manter atual", "existing"), ("Usar recebido", "incoming"))


class ConflictModel(QAbstractTableModel):
    headers = ("Entidade / ID", "Campos divergentes", "Atual", "Recebido", "Decisão")

    def __init__(self, conflicts, parent=None):
        super().__init__(parent)
        self.conflicts = tuple(conflicts)
        self.decisions = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.conflicts)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        conflict = self.conflicts[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.EditRole and column == 4:
            return self.decisions.get(conflict.key)
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        changes = conflict.differences
        values = (
            f"{ENTITY_NAMES.get(conflict.key.entity, conflict.key.entity)} / {conflict.key.native_id}",
            ", ".join(name for name, _, _ in changes),
            "\n".join(f"{name}: {old}" for name, old, _ in changes),
            "\n".join(f"{name}: {new}" for name, _, new in changes),
            next(label for label, decision in DECISIONS if decision == self.decisions.get(conflict.key)),
        )
        value = values[column]
        if role == Qt.ItemDataRole.ToolTipRole:
            if column in (2, 3):
                record = conflict.previous if column == 2 else conflict.incoming
                value += f"\nOrigem: {record.accepted_origin.file.path}\nID de origem: {record.accepted_origin.native_id}"
            return value
        return value.replace("\n", "; ")[:220]

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsEditable if index.column() == 4 else flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or index.column() != 4 or value not in (None, "existing", "incoming"):
            return False
        self.decisions[self.conflicts[index.row()].key] = value
        self.dataChanged.emit(index, index)
        return True

    def choose_all(self, decision):
        self.decisions = {conflict.key: decision for conflict in self.conflicts}
        if self.conflicts:
            self.dataChanged.emit(self.index(0, 4), self.index(len(self.conflicts) - 1, 4))


class DecisionDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        for label, value in DECISIONS:
            editor.addItem(label, value)
        editor.activated.connect(lambda: self.commitData.emit(editor))
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentIndex(editor.findData(index.data(Qt.ItemDataRole.EditRole)))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData())


class NetworkConflictDialog(QDialog):
    def __init__(self, conflicts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Revisar alterações da rede")
        self.resize(1000, 560)
        layout = QVBoxLayout(self)
        count = len(conflicts)
        message = QLabel(("1 registro possui dados diferentes. " if count == 1 else
                          f"{count} registros possuem dados diferentes. ") +
                         "Clique duas vezes em Decisão para escolher a versão do registro completo. "
                         "Passe o mouse sobre os valores para ver os detalhes.")
        message.setWordWrap(True)
        layout.addWidget(message)
        self.model = ConflictModel(conflicts, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(4, DecisionDelegate(self.table))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        for label, decision in DECISIONS[1:]:
            button = QPushButton(label + " em todos")
            button.clicked.connect(lambda checked=False, value=decision: self.model.choose_all(value))
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar decisões e importar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.model.dataChanged.connect(self._update_validity)
        layout.addWidget(self.buttons)
        self._update_validity()

    def _update_validity(self, *args):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            all(self.model.decisions.get(conflict.key) in ("existing", "incoming") for conflict in self.model.conflicts))

    def accept(self):
        if self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            super().accept()


class NetworkMappingDialog(QDialog):
    def __init__(self, mappings=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Correspondências de equipamentos")
        self.resize(650, 420)
        layout = QVBoxLayout(self)
        message = QLabel("Na rede escolhida, IDs iguais representam o mesmo equipamento. "
                         "Informe abaixo os IDs diferentes que também representam o mesmo equipamento. "
                         "Os demais IDs serão novos registros; divergências serão revisadas após a leitura.")
        message.setWordWrap(True)
        layout.addWidget(message)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Entidade", "ID no banco recebido", "ID na rede carregada"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Adicionar correspondência")
        add.clicked.connect(lambda: self.add_mapping())
        remove = QPushButton("Remover linha")
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Confirmar correspondências")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for mapping in mappings:
            self.add_mapping(mapping)

    def add_mapping(self, mapping=None):
        row = self.table.rowCount()
        self.table.insertRow(row)
        combo = QComboBox()
        for entity, label in ENTITY_NAMES.items():
            if entity not in ("patterns", "allocations", "circuit_levels"):
                combo.addItem(label, entity)
        if mapping:
            combo.setCurrentIndex(combo.findData(mapping.entity))
        self.table.setCellWidget(row, 0, combo)
        self.table.setItem(row, 1, QTableWidgetItem(mapping.incoming_id if mapping else ""))
        self.table.setItem(row, 2, QTableWidgetItem(mapping.existing_id if mapping else ""))

    def mappings(self):
        return tuple(IdentityMapping(self.table.cellWidget(row, 0).currentData(),
                                     self.table.item(row, 1).text().strip(), self.table.item(row, 2).text().strip())
                     for row in range(self.table.rowCount()))

    def accept(self):
        mappings = self.mappings()
        if any(not item.incoming_id or not item.existing_id for item in mappings):
            QMessageBox.information(self, "Correspondências", "Preencha ambos os IDs ou remova a linha incompleta.")
            return
        super().accept()
