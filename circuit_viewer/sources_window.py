"""Tabela modeless das fontes carregadas no espaço de trabalho.

Um ``QDialog``, e não um segundo ``QDockWidget``, por três razões. A aplicação
tem **um** dock ("Elemento selecionado") contra uma dúzia de diálogos modeless,
então um segundo dock seria a exceção. Ele disputaria a área direita com o
painel de detalhes e forçaria abas ali — escondendo justamente o painel que diz
de que fonte veio o elemento clicado. E, principalmente, a ``CircuitsWindow`` já
é o painel de camadas: visibilidade e cor por circuito moram lá. Partir o
controle em dois idiomas quebraria o modelo mental.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from .source_composition import SourceWorkspace


class SourceTableModel(QAbstractTableModel):
    """Adaptador fino: o espaço de trabalho continua sendo a verdade."""

    HEADERS = (
        "Fonte",
        "Nome",
        "Circuitos",
        "Barras",
        "Trechos",
        "Cargas",
        "CRS",
        "Unidade",
        "Arquivo",
    )

    NAME_COLUMN = 1

    nameChanged = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._workspace = SourceWorkspace()

    @property
    def workspace(self) -> SourceWorkspace:
        return self._workspace

    def set_workspace(self, workspace: SourceWorkspace) -> None:
        self.beginResetModel()
        self._workspace = workspace
        self.endResetModel()

    def dataset_at(self, row: int):  # noqa: ANN201
        datasets = self._workspace.datasets
        if not 0 <= row < len(datasets):
            return None
        return datasets[row]

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self._workspace)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid():
            return None
        dataset = self.dataset_at(index.row())
        if dataset is None:
            return None
        column = index.column()
        if role == Qt.ItemDataRole.ToolTipRole:
            if dataset.registry is not None:
                paths = tuple(dict.fromkeys(binding.file.path for binding in dataset.registry.bindings))
                return f"Rede {dataset.tag} — {dataset.name}\nArquivos de origem:\n" + "\n".join(paths)
            return dataset.source_path
        if role not in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.EditRole,
        ):
            return None
        if role == Qt.ItemDataRole.EditRole and column != self.NAME_COLUMN:
            return None
        values = (
            dataset.tag,
            dataset.name,
            f"{dataset.circuit_count:n}",
            f"{dataset.count('bars'):n}",
            f"{dataset.count('segments'):n}",
            f"{dataset.count('loads'):n}",
            dataset.crs.label,
            _scale_label(dataset.applied_scale),
            dataset.source_path,
        )
        return values[column]

    def flags(self, index: QModelIndex):  # noqa: ANN201
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == self.NAME_COLUMN:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: ANN001, N802
        if (
            not index.isValid()
            or index.column() != self.NAME_COLUMN
            or role != Qt.ItemDataRole.EditRole
        ):
            return False
        dataset = self.dataset_at(index.row())
        name = str(value).strip()
        if dataset is None or not name or name == dataset.name:
            return False
        # A janela é quem guarda o espaço de trabalho; a tabela só avisa.
        self.nameChanged.emit(dataset.tag, name)
        return True


def _scale_label(scale: float) -> str:
    """O divisor aplicado às coordenadas, do jeito que o diálogo o ofereceu.

    Aparece aqui para que um engano na importação — o divisor errado joga a
    fonte a milhões de metros das outras — fique visível ao lado das demais.
    """

    if scale == 1.0:
        return "metros"
    return f"÷ {scale:g}"


class SourcesWindow(QDialog):
    """Lista as fontes carregadas e permite remover ou enquadrar uma delas."""

    removeRequested = pyqtSignal(str)
    detachRequested = pyqtSignal(str)
    provenanceRequested = pyqtSignal()
    fitRequested = pyqtSignal(str)
    addRequested = pyqtSignal()

    def __init__(self, table_model: SourceTableModel, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Fontes carregadas")
        self.setModal(False)
        self.resize(760, 280)

        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.table_model = table_model
        self.view = QTableView(self)
        self.view.setModel(table_model)
        self.view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.view.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.view)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("Importar banco de dados…")
        self.add_button.clicked.connect(self.addRequested)
        self.fit_button = QPushButton("Enquadrar fonte")
        self.fit_button.clicked.connect(self._fit_selected)
        self.remove_button = QPushButton("Excluir rede do projeto")
        self.remove_button.clicked.connect(self._remove_selected)
        self.detach_button = QPushButton("Desvincular arquivos")
        self.detach_button.setToolTip("Interrompe o reconhecimento automático dos arquivos; preserva os equipamentos e sua proveniência.")
        self.detach_button.clicked.connect(self._detach_selected)
        self.provenance_button = QPushButton("Histórico e pendências…")
        self.provenance_button.clicked.connect(self.provenanceRequested)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.fit_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.detach_button)
        buttons.addWidget(self.provenance_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.hide)
        layout.addWidget(close_box)

        table_model.modelReset.connect(self._sync)
        self.view.selectionModel().selectionChanged.connect(self._sync_buttons)
        self._sync()

    def _selected_tag(self) -> str | None:
        rows = self.view.selectionModel().selectedRows()
        if not rows:
            return None
        dataset = self.table_model.dataset_at(rows[0].row())
        return None if dataset is None else dataset.tag

    def _remove_selected(self) -> None:
        tag = self._selected_tag()
        if tag is not None:
            self.removeRequested.emit(tag)

    def _fit_selected(self) -> None:
        tag = self._selected_tag()
        if tag is not None:
            self.fitRequested.emit(tag)

    def _detach_selected(self) -> None:
        tag = self._selected_tag()
        if tag is not None:
            self.detachRequested.emit(tag)

    def _sync(self) -> None:
        workspace = self.table_model.workspace
        total = len(workspace)
        circuits = sum(item.circuit_count for item in workspace.datasets)
        bars = sum(item.count("bars") for item in workspace.datasets)
        if total == 0:
            self.summary.setText("Nenhuma fonte carregada.")
        else:
            self.summary.setText(
                f"{total:n} fonte(s), {circuits:n} circuito(s) e {bars:n} barra(s) "
                "no mapa. Editar o Nome muda só o rótulo exibido."
            )
        self._sync_buttons()

    def _sync_buttons(self, *_args: object) -> None:
        has_selection = self._selected_tag() is not None
        self.fit_button.setEnabled(has_selection)
        self.remove_button.setEnabled(has_selection)
        self.detach_button.setEnabled(has_selection)

    def set_busy(self, busy: bool) -> None:
        """Desabilita o que mexe no espaço de trabalho durante uma operação."""

        self.add_button.setEnabled(not busy)
        self.remove_button.setEnabled(not busy and self._selected_tag() is not None)
        self.detach_button.setEnabled(not busy and self._selected_tag() is not None)
