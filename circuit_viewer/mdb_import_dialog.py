"""Importação Access em três abas, com inspeção assíncrona e seleção por SE_ID.

As conexões pertencem aos workers. O diálogo mantém apenas metadados, seleção
e a senha temporária, descartando resultados de inspeções substituídas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections import Counter
from enum import StrEnum
import unicodedata

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QThread, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFileDialog,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .csv_import import COORDINATE_UNITS, DEFAULT_COORDINATE_SCALE
from .mdb_mapping import (
    ENTITY_LABELS,
    ENTITY_ORDER,
    GENERATOR_CONSUMER_ENTITY,
    ResolvedMapping,
    load_table_mapping,
    resolve_mapping,
)
from .mdb_import import CircuitChoice, SubstationChoice
from .mdb_credentials import load_default_password
from .mdb_inspection import DatabaseSchema, MdbInspection
from .workers import MdbInspectionWorker
from .model import UtmCrs
from .network_registry import IdentityMapping
from .network_registry_dialog import NetworkMappingDialog


# Item do combo de tabelas que devolve o controle à detecção automática.
AUTOMATIC_LABEL = "Detectar automaticamente"


class MdbSelectionMode(StrEnum):
    CIRCUITS = "circuits"
    DATABASE = "database"


@dataclass(frozen=True, slots=True)
class MdbImportSelection:
    """O que o usuário escolheu no diálogo."""

    crs: UtmCrs
    scale: float
    entities: tuple[str, ...]
    overrides: dict[str, str] = field(default_factory=dict)
    circuit_ids: tuple[str, ...] = ()
    mode: MdbSelectionMode = MdbSelectionMode.CIRCUITS
    target_tag: str | None = None
    correspondences: tuple[IdentityMapping, ...] = ()

    def import_circuit_ids(self) -> tuple[str, ...]:
        if self.mode == MdbSelectionMode.DATABASE:
            return ()
        if not self.circuit_ids:
            raise ValueError("Selecione ao menos um alimentador.")
        return self.circuit_ids

    def __repr__(self) -> str:  # pragma: no cover - conveniência de depuração
        return (
            f"MdbImportSelection(crs={self.crs!r}, scale={self.scale!r}, "
            f"entities={self.entities!r})"
        )


class MdbPasswordDialog(QDialog):
    """Pede a senha de um banco protegido.

    Existe separado do diálogo principal porque a senha só é conhecida como
    necessária **depois** da primeira tentativa de conexão: o driver responde
    ``-1905`` e a aplicação repergunta, em vez de repassar o erro ODBC cru.
    """

    def __init__(
        self,
        file_name: str,
        parent=None,  # noqa: ANN001
        *,
        retry: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Banco protegido")
        self.setModal(True)

        layout = QVBoxLayout(self)
        message = QLabel(
            f"A senha informada para {file_name} não confere."
            if retry
            else f"O banco {file_name} é protegido por senha."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        form = QFormLayout()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setToolTip(
            "A senha é usada apenas para abrir o banco e não é gravada."
        )
        form.addRow("Senha:", self.password_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def password(self) -> str:
        return self.password_input.text()


def search_key(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.casefold())
                   if not unicodedata.combining(c)).strip()


class ChoiceTableModel(QAbstractTableModel):
    """Linhas (identificador, células, diagnóstico), sem widgets por registro."""

    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self.headers = headers
        self.rows = ()

    def replace_rows(self, rows):
        self.beginResetModel()
        self.rows = tuple(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section] if orientation == Qt.Orientation.Horizontal else section + 1
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        key, cells, reason = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return cells[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return key
        if role == Qt.ItemDataRole.ToolTipRole:
            return reason or " · ".join(str(value) for value in cells)
        return None


class MdbImportDialog(QDialog):
    def __init__(self, path: str = "", mapping: ResolvedMapping | None = None,
                 table_names: tuple[str, ...] = (), parent=None, *,
                 suggested_scale: float = DEFAULT_COORDINATE_SCALE,
                 row_counts: dict[str, int] | None = None,
                 circuits: tuple[CircuitChoice, ...] = (),
                 substations: tuple[SubstationChoice, ...] = (),
                 schema: DatabaseSchema | None = None, table_mapping=None,
                 title: str = "Importar banco de dados", workspace=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(820, 700)
        self.setMinimumSize(640, 540)
        self._table_mapping = tuple(table_mapping) if table_mapping is not None else load_table_mapping()
        self._mapping = mapping or ResolvedMapping((), ())
        self._automatic_mapping = self._mapping
        self._schema = schema
        self._table_names = tuple(table_names)
        self._row_counts = dict(row_counts or {})
        self._circuits = tuple(circuits)
        self._substations = tuple(substations)
        self._chosen: set[str] = set()
        self._active_substation = None
        self._known_substations = {}
        self._ready = mapping is not None
        self._inspecting = False
        self._building = True
        self._closing = False
        self._request_id = 0
        self._jobs: dict[int, tuple[QThread, MdbInspectionWorker]] = {}
        self._password: str | None = None
        self._default_password_tried = False
        self._password_is_default = False
        self._error = ""
        self._diagnostics = ()
        self._accept_when_idle = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("Arquivo:"))
        self.path_input = QLineEdit(path)
        self.path_input.setReadOnly(True)
        self.path_input.setPlaceholderText("Selecione um banco Access (.mdb ou .accdb)")
        file_row.addWidget(self.path_input, 1)
        self.browse_button = QPushButton("Procurar…")
        self.browse_button.setAutoDefault(False)
        self.browse_button.clicked.connect(self._browse)
        file_row.addWidget(self.browse_button)
        layout.addLayout(file_row)
        self._identity_mappings = ()
        self.network_combo = QComboBox()
        self.network_combo.addItem("Reconhecer banco; arquivos diferentes criam outra rede", None)
        for source in workspace or ():
            if source.registry is not None:
                self.network_combo.addItem(f"Vincular à rede {source.tag} — {source.name}", source.tag)
        self.mapping_button = QPushButton("Correspondências…")
        self.mapping_button.setAutoDefault(False)
        self.mapping_button.setEnabled(False)
        self.mapping_button.clicked.connect(self._edit_identity_mappings)
        self.network_combo.currentIndexChanged.connect(self._network_changed)
        self.network_combo.setToolTip("O mesmo caminho ou uma cópia idêntica reconhece a rede automaticamente. "
                                     "Vincule outro banco somente se ele representar a mesma rede física.")
        network_row = QHBoxLayout()
        network_row.addWidget(QLabel("Rede:"))
        network_row.addWidget(self.network_combo, 1)
        network_row.addWidget(self.mapping_button)
        layout.addLayout(network_row)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_feeders_tab(), "Alimentadores")
        tables_page = QWidget()
        self.tables_layout = QVBoxLayout(tables_page)
        self.entities_group = self._build_entities_group()
        self.tables_layout.addWidget(self.entities_group)
        self.table_status = QLabel()
        self.table_status.setWordWrap(True)
        self.table_status.setTextFormat(Qt.TextFormat.PlainText)
        self.tables_layout.addWidget(self.table_status)
        self.auxiliary_label = QLabel("As tabelas auxiliares serão verificadas ao abrir o banco.")
        self.auxiliary_label.setWordWrap(True)
        self.auxiliary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.tables_layout.addWidget(self.auxiliary_label)
        self.tables_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(tables_page)
        self.tabs.addTab(scroll, "Tabelas")
        coordinate_page = QWidget()
        coordinate_layout = QVBoxLayout(coordinate_page)
        coordinate_layout.addWidget(self._build_coordinates_group(suggested_scale))
        coordinate_layout.addStretch()
        self.tabs.addTab(coordinate_page, "Coordenadas")
        layout.addWidget(self.tabs, 1)
        self.circuit_summary = QLabel()
        self.circuit_summary.setWordWrap(True)
        layout.addWidget(self.circuit_summary)
        self.warning_label = QLabel()
        self.warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.load_button = QPushButton("Carregar alimentadores na memória")
        self.load_button.setMinimumHeight(32)
        self.load_button.setAutoDefault(False)
        button_row = QHBoxLayout()
        button_row.addWidget(self.load_button, 1)
        button_row.addWidget(self.buttons)
        self.load_button.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addLayout(button_row)
        self._building = False
        self._update_entity_validity()
        self._refresh_substations()
        self._refresh_circuits()

    def _network_changed(self):
        self._identity_mappings = ()
        self.mapping_button.setEnabled(self.network_combo.currentData() is not None)

    def _edit_identity_mappings(self):
        dialog = NetworkMappingDialog(self._identity_mappings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._identity_mappings = dialog.mappings()
        dialog.deleteLater()

    def _table(self, model, *, single=False):
        view = QTableView()
        view.setModel(model)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection if single
                              else QAbstractItemView.SelectionMode.ExtendedSelection)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.verticalHeader().hide()
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        view.setShowGrid(False)
        view.setMinimumHeight(90)
        return view

    def _build_feeders_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.circuits_group = QWidget()
        hierarchy = QVBoxLayout(self.circuits_group)
        hierarchy.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        upper = QVBoxLayout(top)
        upper.setContentsMargins(0, 0, 0, 0)
        upper.addWidget(QLabel("1. Selecione a subestação"))
        self.substation_filter = QLineEdit()
        self.substation_filter.setPlaceholderText("Buscar subestação por código, nome ou ID…")
        self.substation_filter.setClearButtonEnabled(True)
        self.substation_filter.textChanged.connect(self._filter_substations)
        upper.addWidget(self.substation_filter)
        self.substation_model = ChoiceTableModel(("Código", "Nome", "Alimentadores"), self)
        self.substation_view = self._table(self.substation_model, single=True)
        self.substation_view.selectionModel().currentRowChanged.connect(self._substation_changed)
        upper.addWidget(self.substation_view, 1)
        splitter.addWidget(top)
        bottom = QWidget()
        lower = QVBoxLayout(bottom)
        lower.setContentsMargins(0, 0, 0, 0)
        lower.addWidget(QLabel("2. Selecione os alimentadores"))
        self.circuit_filter = QLineEdit()
        self.circuit_filter.setPlaceholderText("Buscar alimentador por código, ID ou tensão…")
        self.circuit_filter.setClearButtonEnabled(True)
        self.circuit_filter.textChanged.connect(self._refresh_circuits)
        lower.addWidget(self.circuit_filter)
        self.available_model = ChoiceTableModel(("Código", "ID", "Tensão (kV)"), self)
        self.selected_model = ChoiceTableModel(("Código", "ID", "Tensão (kV)", "Subestação"), self)
        self.available_view = self._table(self.available_model)
        self.selected_view = self._table(self.selected_model)
        self.available_view.setColumnHidden(1, True)
        self.selected_view.setColumnHidden(1, True)
        transfer = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(QLabel("Disponíveis"))
        left.addWidget(self.available_view, 1)
        right.addWidget(QLabel("Selecionados"))
        right.addWidget(self.selected_view, 1)
        transfer.addLayout(left, 1)
        arrows = QVBoxLayout()
        arrows.addStretch()
        self.transfer_buttons = {}
        for text, tip, callback in (
            (">", "Adicionar os alimentadores destacados", self._add_highlighted),
            (">>", "Adicionar todos os disponíveis após o filtro", self._add_visible),
            ("<", "Remover os alimentadores destacados", self._remove_highlighted),
            ("<<", "Limpar toda a seleção, de todas as subestações", self._clear_chosen),
        ):
            button = QPushButton(text)
            button.setFixedWidth(36)
            button.setAutoDefault(False)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.clicked.connect(callback)
            self.transfer_buttons[text] = button
            arrows.addWidget(button)
        arrows.addStretch()
        transfer.addLayout(arrows)
        transfer.addLayout(right, 1)
        lower.addLayout(transfer, 1)
        self.available_view.doubleClicked.connect(self._add_highlighted)
        self.selected_view.doubleClicked.connect(self._remove_highlighted)
        self.available_view.selectionModel().selectionChanged.connect(self._sync_transfer_buttons)
        self.selected_view.selectionModel().selectionChanged.connect(self._sync_transfer_buttons)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        hierarchy.addWidget(splitter)
        layout.addWidget(self.circuits_group, 1)
        self.whole_database_check = QCheckBox("Importar banco sem seleção de alimentadores")
        self.whole_database_check.toggled.connect(self._sync_ok_enabled)
        layout.addWidget(self.whole_database_check)
        return page

    def _substation_key(self, choice):
        return choice.substation_id if choice.substation_id in self._known_substations else ""

    def _refresh_substations(self):
        self._known_substations = {item.substation_id: item for item in self._substations}
        counts = Counter(self._substation_key(choice) for choice in self._circuits)
        rows = [(item.substation_id, (item.code or item.substation_id, item.name, counts[item.substation_id]),
                 f"ID: {item.substation_id}") for item in self._substations]
        rows.sort(key=lambda row: search_key(" ".join(str(value) for value in row[1][:2])))
        if counts[""]:
            rows.append(("", ("—", "Sem subestação", counts[""]), "Alimentadores sem vínculo válido com SE."))
        self.substation_model.replace_rows(rows)
        self._active_substation = None
        self._filter_substations()

    def _filter_substations(self, *_args):
        needle = search_key(self.substation_filter.text())
        for index, (key, cells, _) in enumerate(self.substation_model.rows):
            hidden = needle not in search_key(" ".join((key, *(str(cell) for cell in cells[:2]))))
            self.substation_view.setRowHidden(index, hidden)
            if hidden and self._active_substation == key:
                self.substation_view.setCurrentIndex(QModelIndex())
                self._active_substation = None
                self._refresh_circuits()

    def _substation_changed(self, current, _previous):
        self._active_substation = current.data(Qt.ItemDataRole.UserRole) if current.isValid() else None
        self._refresh_circuits()

    def _refresh_circuits(self, *_args):
        if self._building:
            return
        needle = search_key(self.circuit_filter.text())
        available, selected = [], []
        for choice in self._circuits:
            cells = (choice.code, choice.circuit_id, choice.nominal_voltage)
            if choice.circuit_id in self._chosen:
                substation = self._known_substations.get(choice.substation_id)
                label = "Sem subestação" if substation is None else (
                    " · ".join(filter(None, (substation.code, substation.name))) or substation.substation_id)
                selected.append((choice.circuit_id, (*cells, label), choice.reason))
            elif self._active_substation == self._substation_key(choice) and needle in search_key(" ".join(cells)):
                available.append((choice.circuit_id, cells, choice.reason))
        self.available_model.replace_rows(available)
        self.selected_model.replace_rows(selected)
        self._sync_ok_enabled()
        self._sync_transfer_buttons()

    @staticmethod
    def _highlighted(view):
        return {index.data(Qt.ItemDataRole.UserRole) for index in view.selectionModel().selectedRows()}

    def _add_highlighted(self, *_args):
        self._chosen.update(self._highlighted(self.available_view))
        self._refresh_circuits()

    def _add_visible(self):
        self._chosen.update(row[0] for row in self.available_model.rows)
        self._refresh_circuits()

    def _remove_highlighted(self, *_args):
        self._chosen.difference_update(self._highlighted(self.selected_view))
        self._refresh_circuits()

    def _clear_chosen(self):
        self._chosen.clear()
        self._refresh_circuits()

    def _sync_transfer_buttons(self, *_args):
        if self._building:
            return
        self.transfer_buttons[">"].setEnabled(bool(self._highlighted(self.available_view)))
        self.transfer_buttons[">>"].setEnabled(bool(self.available_model.rows))
        self.transfer_buttons["<"].setEnabled(bool(self._highlighted(self.selected_view)))
        self.transfer_buttons["<<"].setEnabled(bool(self._chosen))

    def selected_entities(self):
        return tuple(entity for entity in ENTITY_ORDER if self.entity_checks[entity].isChecked())

    def _all_overrides(self):
        chosen = {}
        for source, combo in self.entity_combos.items():
            table = combo.currentData()
            resolved = self._automatic_mapping.get(source)
            if table is not None and (resolved is None or resolved.table != table):
                chosen[source] = table
        return chosen

    def overrides(self):
        sources = set(self.selected_entities())
        if "geradores" in sources:
            sources.add(GENERATOR_CONSUMER_ENTITY)
        return {key: value for key, value in self._all_overrides().items() if key in sources}

    def _on_table_changed(self, entity):
        if self._building:
            return
        if self._schema is None:
            self._start_inspection(reset=False)
            return
        self._mapping = resolve_mapping(self._schema, self._table_mapping, overrides=self._all_overrides())
        self._update_entity_validity(entity)
        if entity == "circuitos":
            self._chosen.clear()
            self._circuits = ()
            self._refresh_substations()
            self._refresh_circuits()
            self._start_inspection(reset=False)
        self._sync_ok_enabled()

    def _update_entity_validity(self, changed=None):
        self._building = True
        messages = []
        for entity, check in self.entity_checks.items():
            sources = (entity, GENERATOR_CONSUMER_ENTITY) if entity == "geradores" else (entity,)
            valid = all(self._mapping.get(source) is not None for source in sources)
            check.setEnabled(valid)
            if not valid or entity == changed:
                check.setChecked(valid)
            reason = "; ".join(filter(None, (self._mapping.reason_for(source) for source in sources)))
            check.setToolTip(reason)
            for source in sources:
                self.entity_combos[source].setToolTip(reason)
            if reason:
                messages.append(f"{ENTITY_LABELS[entity]}: {reason}")
        self.table_status.setText("\n".join(messages))
        self._building = False

    def _sync_ok_enabled(self, *_args):
        if self._building:
            return
        whole = self.whole_database_check.isChecked()
        self.circuits_group.setEnabled(self._ready and not self._inspecting and not whole)
        self.whole_database_check.setEnabled(self._ready and not self._inspecting)
        self.entities_group.setEnabled(self._ready and not self._inspecting)
        self.load_button.setText("Carregar banco na memória" if whole else "Carregar alimentadores na memória")
        selected = self.selected_entities()
        required = ("barras",) if whole else ("barras", "trechos", "circuitos")
        missing = [ENTITY_LABELS[entity] for entity in required
                   if entity not in selected or self._mapping.get(entity) is None]
        invalid = [ENTITY_LABELS[entity] for entity in selected if self._mapping.get(entity) is None]
        enabled = self._ready and not self._inspecting and not self._closing and not missing and not invalid
        self.load_button.setEnabled(enabled and (whole or bool(self._chosen)))
        groups = {self._substation_key(choice) for choice in self._circuits if choice.circuit_id in self._chosen}
        count = len(self._chosen)
        feeders = "alimentador selecionado" if count == 1 else "alimentadores selecionados"
        stations = "subestação" if len(groups) == 1 else "subestações"
        self.circuit_summary.setText("Banco sem seleção de alimentadores; serão lidas as tabelas marcadas."
                                    if whole else f"{count} {feeders} em {len(groups)} {stations}")
        if self._closing:
            message = "Cancelando a inspeção e fechando a conexão…"
        elif self._inspecting:
            message = "Lendo tabelas, subestações e alimentadores…"
        elif self._error:
            message = self._error
        elif not self._ready:
            message = "Selecione um arquivo para começar."
        elif missing:
            message = "Tabelas obrigatórias: " + ", ".join(missing) + ". Verifique a aba Tabelas."
        elif invalid:
            message = "Tabelas incompatíveis: " + ", ".join(invalid)
        elif not whole and not self._chosen:
            message = ("Selecione ao menos um alimentador." if self._circuits else
                       "Nenhum alimentador disponível. Verifique Tabelas ou escolha importar o banco sem seleção.")
        else:
            skipped = [ENTITY_LABELS[entity] for entity in ENTITY_ORDER if entity not in selected]
            message = "Não serão importadas: " + ", ".join(skipped) if skipped else ""
        if self._diagnostics and not self._inspecting:
            message += "\n" + "\n".join(self._diagnostics)
        self.warning_label.setText(message)
        self.warning_label.setVisible(bool(message))

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar banco Access", self.path_input.text(),
                                             "Bancos Access (*.mdb *.accdb)")
        if path:
            self.set_path(path)

    def set_path(self, path):
        self.network_combo.setCurrentIndex(0)
        self._identity_mappings = ()
        self.path_input.setText(str(Path(path).absolute()))
        self.path_input.setToolTip(self.path_input.text())
        self._password = None
        self._default_password_tried = False
        self._password_is_default = False
        self._chosen.clear()
        self._circuits = ()
        self._substations = ()
        self._schema = None
        self._mapping = ResolvedMapping((), ())
        self._automatic_mapping = self._mapping
        self._table_names = ()
        self._row_counts = {}
        self._ready = False
        self._diagnostics = ()
        self._building = True
        self.whole_database_check.setChecked(False)
        self.substation_filter.clear()
        self.circuit_filter.clear()
        self.zone_input.setValue(21)
        self.hemisphere_input.setCurrentIndex(0)
        self.unit_input.setCurrentIndex(self.unit_input.findData(DEFAULT_COORDINATE_SCALE))
        self._replace_entities_group()
        self.auxiliary_label.setText("As tabelas auxiliares serão verificadas ao abrir o banco.")
        self.table_status.clear()
        self._building = False
        self._refresh_substations()
        self._refresh_circuits()
        self._start_inspection(reset=True)

    def _replace_entities_group(self):
        old = self.entities_group
        self.entities_group = self._build_entities_group()
        self.tables_layout.replaceWidget(old, self.entities_group)
        old.hide()
        old.deleteLater()

    def _start_inspection(self, *, reset):
        if not self.path_input.text() or self._closing:
            return
        for _thread, worker in self._jobs.values():
            worker.cancel()
        self._request_id += 1
        request_id = self._request_id
        self._inspecting = True
        self._error = ""
        self._inspection_reset = reset
        self._accept_when_idle = False
        self._sync_ok_enabled()
        thread = QThread(self)
        worker = MdbInspectionWorker(request_id, self.path_input.text(), password=self._password,
                                     table_mapping=self._table_mapping, overrides=self._all_overrides())
        worker.moveToThread(thread)
        self._jobs[request_id] = (thread, worker)
        thread.started.connect(worker.run)
        worker.finished.connect(self._inspection_finished)
        worker.failed.connect(self._inspection_failed)
        worker.password_required.connect(self._request_password)
        worker.completed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        thread.finished.connect(lambda token=request_id: self._inspection_thread_finished(token))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _inspection_finished(self, request_id: int, result: MdbInspection) -> None:
        if request_id != self._request_id or self._closing:
            return
        self._schema = result.schema
        self._table_names = result.schema.table_names
        self._mapping = result.mapping
        self._automatic_mapping = result.automatic_mapping
        self._row_counts = result.row_counts
        self._circuits = result.circuits
        self._substations = result.substations
        self._diagnostics = result.diagnostics
        if self._inspection_reset:
            self._building = True
            self._replace_entities_group()
            self._building = False
        self._update_entity_validity()
        self.auxiliary_label.setText("Tabelas auxiliares consultadas conforme as entidades importadas:\n\n" +
                                    "\n".join(f"{item.table} — {item.purpose}: {item.status}" for item in result.auxiliaries))
        self._ready = True
        self._inspecting = False
        self._refresh_substations()
        self._refresh_circuits()

    def _inspection_failed(self, request_id, message):
        if request_id != self._request_id or self._closing:
            return
        self._ready = False
        self._inspecting = False
        self._error = message
        self._sync_ok_enabled()

    def _request_password(self, request_id):
        if request_id != self._request_id or self._closing:
            return
        if not self._default_password_tried:
            self._default_password_tried = True
            password = load_default_password()
            if password:
                self._password = password
                self._password_is_default = True
                self._start_inspection(reset=self._inspection_reset)
                return
        dialog = MdbPasswordDialog(Path(self.path_input.text()).name, self,
                                   retry=self._password is not None and not self._password_is_default)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if request_id != self._request_id or self._closing:
            return
        if accepted:
            self._password = dialog.password()
            self._password_is_default = False
            self._start_inspection(reset=self._inspection_reset)
        else:
            self._password = None
            self._inspection_failed(request_id, "A abertura do banco protegido foi cancelada. Selecione o arquivo para tentar novamente.")

    def _inspection_thread_finished(self, request_id: int) -> None:
        self._jobs.pop(request_id, None)
        if not self._jobs:
            if self._closing:
                super().done(QDialog.DialogCode.Rejected)
            elif self._accept_when_idle:
                self._accept_when_idle = False
                self.accept()

    def done(self, result):
        if result == QDialog.DialogCode.Accepted:
            self._sync_ok_enabled()
            if not self.load_button.isEnabled():
                return
            self.selection().import_circuit_ids()
        if self._jobs:
            if result == QDialog.DialogCode.Accepted:
                self._accept_when_idle = True
                return
            self._closing = True
            self._request_id += 1
            for _thread, worker in self._jobs.values():
                worker.cancel()
            self.browse_button.setEnabled(False)
            self._password = None
            self._sync_ok_enabled()
            return
        if result != QDialog.DialogCode.Accepted:
            self._password = None
        super().done(result)

    def closeEvent(self, event):
        if self._jobs:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)

    def password(self) -> str | None:
        return self._password

    def clear_password(self) -> None:
        self._password = None

    def selected_circuit_ids(self) -> tuple[str, ...]:
        return tuple(choice.circuit_id for choice in self._circuits if choice.circuit_id in self._chosen)

    def coordinate_scale(self) -> float:
        return float(self.unit_input.currentData())

    def crs(self) -> UtmCrs:
        return UtmCrs(zone=self.zone_input.value(), northern=bool(self.hemisphere_input.currentData()))

    def selection(self) -> MdbImportSelection:
        whole = self.whole_database_check.isChecked()
        return MdbImportSelection(crs=self.crs(), scale=self.coordinate_scale(),
                                  entities=self.selected_entities(), overrides=self.overrides(),
                                  circuit_ids=() if whole else self.selected_circuit_ids(),
                                  mode=MdbSelectionMode.DATABASE if whole else MdbSelectionMode.CIRCUITS,
                                  target_tag=self.network_combo.currentData(),
                                  correspondences=self._identity_mappings)



    def _build_entities_group(self) -> QWidget:
        group = QGroupBox("Tabelas")
        form = QFormLayout(group)
        self.entity_checks: dict[str, QCheckBox] = {}
        self.entity_combos: dict[str, QComboBox] = {}

        def create_combo(entity: str) -> QComboBox:
            resolved = self._mapping.get(entity)
            combo = QComboBox()
            combo.addItem(AUTOMATIC_LABEL, None)
            for name in self._table_names:
                count = self._row_counts.get(name)
                label = name if count is None else f"{name}  ({count:n})"
                combo.addItem(label, name)
            if resolved is not None:
                position = combo.findData(resolved.table)
                if position >= 0:
                    combo.setCurrentIndex(position)
            else:
                combo.setToolTip(
                    self._mapping.reason_for(entity) or "Não encontrada."
                )
            self.entity_combos[entity] = combo
            return combo

        for entity in ENTITY_ORDER:
            resolved = self._mapping.get(entity)
            consumer_resolved = (
                self._mapping.get(GENERATOR_CONSUMER_ENTITY)
                if entity == "geradores"
                else None
            )
            fully_resolved = resolved is not None and (
                entity != "geradores" or consumer_resolved is not None
            )
            check = QCheckBox(ENTITY_LABELS[entity])
            check.setChecked(fully_resolved)
            check.toggled.connect(self._sync_ok_enabled)

            combo = create_combo(entity)
            if fully_resolved:
                tooltip = f"Tabela detectada: {resolved.table}"
                if entity == "geradores":
                    tooltip += f"; MT_CONS: {consumer_resolved.table}"
                check.setToolTip(tooltip)
            else:
                reasons = [self._mapping.reason_for(entity)]
                if entity == "geradores":
                    reasons.append(
                        self._mapping.reason_for(GENERATOR_CONSUMER_ENTITY)
                    )
                reason = "; ".join(item for item in reasons if item) or "Não encontrada."
                check.setEnabled(False)
                check.setToolTip(reason)
                combo.setToolTip(reason)
            combo.currentIndexChanged.connect(
                lambda _index, name=entity: self._on_table_changed(name)
            )

            self.entity_checks[entity] = check
            if entity != "geradores":
                form.addRow(check, combo)
                continue

            consumer_combo = create_combo(GENERATOR_CONSUMER_ENTITY)
            consumer_combo.currentIndexChanged.connect(
                lambda _index: self._on_table_changed("geradores")
            )
            compound = QWidget(group)
            compound_form = QFormLayout(compound)
            compound_form.setContentsMargins(0, 0, 0, 0)
            compound_form.addRow("MT_GERADOR_CONS:", combo)
            compound_form.addRow("MT_CONS:", consumer_combo)
            form.addRow(check, compound)

        return group


    def _build_coordinates_group(self, suggested_scale: float) -> QWidget:
        group = QGroupBox("Coordenadas")
        form = QFormLayout(group)

        self.zone_input = QSpinBox()
        self.zone_input.setRange(1, 60)
        self.zone_input.setValue(21)
        self.zone_input.setToolTip("Zona longitudinal do sistema UTM")
        form.addRow("Zona UTM:", self.zone_input)

        self.hemisphere_input = QComboBox()
        self.hemisphere_input.addItem("Sul", False)
        self.hemisphere_input.addItem("Norte", True)
        form.addRow("Hemisfério:", self.hemisphere_input)

        self.unit_input = QComboBox()
        for factor, label in COORDINATE_UNITS:
            self.unit_input.addItem(label, factor)
        self.unit_input.setToolTip(
            "Divisor que converte X e Y do banco para metros. O modelo trabalha "
            "em metros, como o COMPR dos trechos."
        )
        position = self.unit_input.findData(suggested_scale)
        if position >= 0:
            self.unit_input.setCurrentIndex(position)
        form.addRow("Unidade das coordenadas:", self.unit_input)
        return group
