"""Cadastro de arranjos e consulta das montagens OpenDSS automáticas."""

from __future__ import annotations

import copy
import math
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSignalBlocker, QTimer, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .opendss_geometry_preview import CartesianGeometryView
from .opendss_automatic_assembly_session import OpenDssAutomaticAssemblySession
from .opendss_library import (
    OPEN_DSS_UNITS,
    ArrangementDefinition,
    ConductorPosition,
    LibraryFormatError,
    coincident_positions,
    normalize_library_name,
    unique_id,
    unique_name,
)
from .opendss_library_help import OpenDssLibraryHelpDialog
from .opendss_library_session import OpenDssLibrarySession
from .opendss_mapping_session import MappedLibraryItemError
from .opendss_library_store import read_geometries_file
from .table_columns import (
    EXCEL_LIKE_TABLE_STYLE,
    AllRowsTableView,
    enable_interactive_columns,
)


def _number(text: str) -> float:
    value = str(text).strip()
    if not value or ("," in value and "." in value):
        raise ValueError("Informe um número usando ponto ou vírgula decimal.")
    try:
        number = float(value.replace(",", "."))
    except ValueError as exc:
        raise ValueError("Informe um número válido.") from exc
    if not math.isfinite(number):
        raise ValueError("Informe um número finito.")
    return number


class PositionTableModel(QAbstractTableModel):
    HEADERS = ("#", "Papel", "x", "h")

    def __init__(self, session: OpenDssLibrarySession, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.session = session
        self.arrangement_id: str | None = None

    def set_arrangement(self, arrangement_id: str | None) -> None:
        self.beginResetModel()
        self.arrangement_id = arrangement_id
        self.endResetModel()

    def arrangement(self) -> ArrangementDefinition | None:
        return self.session.catalog.arrangement(self.arrangement_id)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        arrangement = self.arrangement()
        return 0 if parent.isValid() or arrangement is None else arrangement.conductor_count

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= int(section) < len(self.HEADERS):
                return self.HEADERS[int(section)]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        arrangement = self.arrangement()
        if not index.isValid() or arrangement is None:
            return None
        position = arrangement.positions[index.row()]
        values = (
            index.row() + 1,
            f"F{index.row() + 1}" if index.row() < arrangement.phase_count else "N",
            f"{position.x:g}",
            f"{position.height:g}",
        )
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            return values[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in {0, 2, 3}:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def flags(self, index: QModelIndex):  # noqa: ANN201
        flags = super().flags(index)
        if index.isValid() and index.column() in {2, 3}:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: ANN001, N802
        arrangement = self.arrangement()
        if role != Qt.ItemDataRole.EditRole or arrangement is None or index.column() not in {2, 3}:
            return False
        try:
            parsed = _number(str(value))
        except ValueError:
            return False
        position = arrangement.positions[index.row()]
        attribute = "x" if index.column() == 2 else "height"
        if getattr(position, attribute) == parsed:
            return True
        setattr(position, attribute, parsed)
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
        self.session.mark_geometries_changed()
        return True


class AutomaticAssemblyTableModel(QAbstractTableModel):
    """Posições e vínculos de uma montagem automática, sempre somente leitura."""

    HEADERS = ("#", "Posição", "Fase real", "x / h", "Cabo")

    def __init__(
        self,
        assembly_session: OpenDssAutomaticAssemblySession | None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.assembly_session = assembly_session
        self.assembly_id: str | None = None

    def set_assembly(self, assembly_id: str | None) -> None:
        self.beginResetModel()
        self.assembly_id = assembly_id
        self.endResetModel()

    def assembly(self):  # noqa: ANN201
        return (
            None
            if self.assembly_session is None
            else self.assembly_session.assembly(self.assembly_id)
        )

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        assembly = self.assembly()
        return (
            0
            if parent.isValid() or assembly is None
            else assembly.arrangement.conductor_count
        )

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

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        assembly = self.assembly()
        if not index.isValid() or assembly is None:
            return None
        row = index.row()
        arrangement = assembly.arrangement
        position = arrangement.positions[row]
        is_phase = row < arrangement.phase_count
        physical_role = f"F{row + 1}" if is_phase else f"N{row - arrangement.phase_count + 1}"
        electrical_role = assembly.phase_letters[row] if is_phase else "Neutro"
        cable_id = assembly.geometry.cable_ids[row]
        cable = (
            None
            if self.assembly_session is None
            else self.assembly_session.catalog.cable(cable_id)
        )
        values = (
            row + 1,
            physical_role,
            electrical_role,
            f"{position.x:g} / {position.height:g} {arrangement.units}",
            "—" if cable is None else cable.name,
        )
        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            if is_phase:
                return f"{physical_role} → fase {electrical_role}"
            return f"{physical_role} → condutor neutro"
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in {0, 3}:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None


class AutomaticAssemblyIssueTableModel(QAbstractTableModel):
    HEADERS = ("Nível", "Campo", "Valor", "Qtd.", "Motivo", "Trechos")

    def __init__(
        self,
        assembly_session: OpenDssAutomaticAssemblySession | None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.assembly_session = assembly_session

    def refresh(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def issues(self):  # noqa: ANN201
        return (
            ()
            if self.assembly_session is None
            else self.assembly_session.result.issues
        )

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.issues())

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

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid():
            return None
        issue = self.issues()[index.row()]
        shown = issue.segment_ids[:12]
        segment_text = ", ".join(shown)
        if len(issue.segment_ids) > len(shown):
            segment_text += f" … e mais {len(issue.segment_ids) - len(shown):n}"
        values = (
            "Erro" if issue.severity == "error" else "Aviso",
            issue.field,
            issue.value,
            issue.count,
            issue.reason,
            segment_text,
        )
        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return ", ".join(issue.segment_ids)
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == 3:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None


class OpenDssGeometriesWindow(QDialog):
    def __init__(
        self,
        session: OpenDssLibrarySession,
        help_dialog: OpenDssLibraryHelpDialog,
        parent=None,  # noqa: ANN001
        *,
        assembly_session: OpenDssAutomaticAssemblySession | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.help_dialog = help_dialog
        self.assembly_session = assembly_session
        self._selected_arrangement_id: str | None = None
        self._selected_geometry_id: str | None = None
        self._loading = False
        self._refresh_pending = False
        self.setWindowTitle("Biblioteca de Geometrias OpenDSS")
        self.setModal(False)
        self.resize(1120, 730)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("opendss_geometries_summary")
        root.addWidget(self.summary_label)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("opendss_geometries_tabs")
        self.tabs.addTab(self._build_arrangements_tab(), "Arranjos")
        self.tabs.addTab(self._build_geometries_tab(), "Montagens automáticas")
        root.addWidget(self.tabs, 1)
        self.status_label = QLabel(self)
        self.status_label.setObjectName("opendss_geometries_status")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        root.addLayout(self._build_actions())

        self._connect_signals()
        self.refresh()
        self._sync_dirty_state(session.geometries_dirty)
        if session.geometries_load.issue:
            self.status_label.setText(session.geometries_load.issue)

    def _build_arrangements_tab(self) -> QWidget:
        tab = QWidget(self.tabs)
        layout = QHBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal, tab)
        side = QWidget(splitter)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.arrangements_list = QListWidget(side)
        self.arrangements_list.setObjectName("opendss_arrangements_list")
        side_layout.addWidget(self.arrangements_list, 1)
        arrangement_buttons = QHBoxLayout()
        self.new_arrangement_button = QPushButton("Novo", side)
        self.duplicate_arrangement_button = QPushButton("Duplicar", side)
        self.delete_arrangement_button = QPushButton("Excluir…", side)
        for button in (self.new_arrangement_button, self.duplicate_arrangement_button, self.delete_arrangement_button):
            arrangement_buttons.addWidget(button)
        side_layout.addLayout(arrangement_buttons)

        scroll = QScrollArea(splitter)
        scroll.setWidgetResizable(True)
        editor = QWidget(scroll)
        column = QVBoxLayout(editor)
        self.arrangement_empty_label = QLabel("Selecione um arranjo ou crie um novo.", editor)
        column.addWidget(self.arrangement_empty_label)
        group = QGroupBox("Arranjo (LineSpacing)", editor)
        form = QFormLayout(group)
        self.arrangement_name_edit = QLineEdit(group)
        form.addRow("Nome:", self.arrangement_name_edit)
        self.arrangement_units_combo = QComboBox(group)
        self.arrangement_units_combo.addItems(OPEN_DSS_UNITS)
        form.addRow("Unidades de x/h:", self.arrangement_units_combo)
        self.arrangement_phases_spin = QSpinBox(group)
        self.arrangement_phases_spin.setMinimum(1)
        form.addRow("Fases:", self.arrangement_phases_spin)
        self.arrangement_description_edit = QLineEdit(group)
        form.addRow("Descrição:", self.arrangement_description_edit)
        self.arrangement_source_edit = QLineEdit(group)
        form.addRow("Origem:", self.arrangement_source_edit)
        column.addWidget(group)
        self.position_model = PositionTableModel(self.session, self)
        self.positions_table = AllRowsTableView(editor)
        self.positions_table.setObjectName("opendss_positions_table")
        self.positions_table.setModel(self.position_model)
        self.positions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.positions_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.positions_table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        enable_interactive_columns(self.positions_table)
        column.addWidget(self.positions_table)
        positions_row = QHBoxLayout()
        self.add_position_button = QPushButton("Adicionar condutor", editor)
        self.remove_position_button = QPushButton("Remover selecionado", editor)
        positions_row.addWidget(self.add_position_button)
        positions_row.addWidget(self.remove_position_button)
        positions_row.addStretch(1)
        column.addLayout(positions_row)
        self.arrangement_issue_label = QLabel(editor)
        self.arrangement_issue_label.setWordWrap(True)
        column.addWidget(self.arrangement_issue_label)
        self.arrangement_preview = CartesianGeometryView(editor)
        self.arrangement_preview.setObjectName("opendss_arrangement_preview")
        column.addWidget(self.arrangement_preview, 1)
        scroll.setWidget(editor)
        splitter.addWidget(side)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)
        return tab

    def _build_geometries_tab(self) -> QWidget:
        tab = QWidget(self.tabs)
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal, tab)
        side = QWidget(splitter)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.geometries_list = QListWidget(side)
        self.geometries_list.setObjectName("opendss_montages_list")
        side_layout.addWidget(self.geometries_list, 1)
        self.automatic_list_hint = QLabel(
            "As combinações são agrupadas por arranjo, cabos e fases reais.", side
        )
        self.automatic_list_hint.setWordWrap(True)
        side_layout.addWidget(self.automatic_list_hint)

        scroll = QScrollArea(splitter)
        scroll.setWidgetResizable(True)
        editor = QWidget(scroll)
        column = QVBoxLayout(editor)
        self.geometry_empty_label = QLabel(
            "Importe trechos para gerar as montagens automáticas.", editor
        )
        self.geometry_empty_label.setWordWrap(True)
        column.addWidget(self.geometry_empty_label)
        group = QGroupBox("Montagem automática (somente leitura)", editor)
        form = QFormLayout(group)
        self.automatic_name_label = QLabel(group)
        self.automatic_name_label.setWordWrap(True)
        form.addRow("Nome:", self.automatic_name_label)
        self.automatic_arrangement_label = QLabel(group)
        form.addRow("Arranjo base:", self.automatic_arrangement_label)
        self.automatic_phases_label = QLabel(group)
        form.addRow("Fases:", self.automatic_phases_label)
        self.automatic_phase_cable_label = QLabel(group)
        form.addRow("Cabo de fase:", self.automatic_phase_cable_label)
        self.automatic_neutral_cable_label = QLabel(group)
        form.addRow("Cabo neutro:", self.automatic_neutral_cable_label)
        self.automatic_usage_label = QLabel(group)
        self.automatic_usage_label.setWordWrap(True)
        form.addRow("Trechos:", self.automatic_usage_label)
        self.automatic_reduce_label = QLabel(group)
        form.addRow("Redução de Kron:", self.automatic_reduce_label)
        column.addWidget(group)
        self.assignment_model = AutomaticAssemblyTableModel(
            self.assembly_session, self
        )
        self.assignments_table = AllRowsTableView(editor)
        self.assignments_table.setObjectName("opendss_geometry_cables_table")
        self.assignments_table.setModel(self.assignment_model)
        self.assignments_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.assignments_table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        enable_interactive_columns(self.assignments_table)
        column.addWidget(self.assignments_table)
        self.geometry_issue_label = QLabel(editor)
        self.geometry_issue_label.setWordWrap(True)
        column.addWidget(self.geometry_issue_label)
        self.geometry_preview = CartesianGeometryView(editor)
        self.geometry_preview.setObjectName("opendss_geometry_preview")
        column.addWidget(self.geometry_preview, 1)
        scroll.setWidget(editor)
        splitter.addWidget(side)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self.automatic_draft_label = QLabel(tab)
        self.automatic_draft_label.setWordWrap(True)
        layout.addWidget(self.automatic_draft_label)
        self.automatic_issue_summary = QLabel(tab)
        self.automatic_issue_summary.setWordWrap(True)
        layout.addWidget(self.automatic_issue_summary)
        self.automatic_issue_model = AutomaticAssemblyIssueTableModel(
            self.assembly_session, self
        )
        self.automatic_issue_table = QTableView(tab)
        self.automatic_issue_table.setObjectName("opendss_automatic_issues_table")
        self.automatic_issue_table.setModel(self.automatic_issue_model)
        self.automatic_issue_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.automatic_issue_table.setAlternatingRowColors(True)
        self.automatic_issue_table.verticalHeader().hide()
        self.automatic_issue_table.setMaximumHeight(190)
        self.automatic_issue_table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        enable_interactive_columns(self.automatic_issue_table)
        layout.addWidget(self.automatic_issue_table)
        return tab

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.import_button = QPushButton("Importar…", self)
        self.export_button = QPushButton("Exportar…", self)
        self.restore_button = QPushButton("Restaurar padrões…", self)
        self.help_button = QPushButton("Ajuda", self)
        for button in (self.import_button, self.export_button, self.restore_button, self.help_button):
            row.addWidget(button)
        row.addStretch(1)
        self.save_button = QPushButton("Salvar", self)
        self.close_button = QPushButton("Fechar", self)
        row.addWidget(self.save_button)
        row.addWidget(self.close_button)
        return row

    def _connect_signals(self) -> None:
        self.session.geometriesChanged.connect(self._schedule_refresh)
        self.session.cablesChanged.connect(self._schedule_refresh)
        self.session.geometriesDirtyChanged.connect(self._sync_dirty_state)
        self.arrangements_list.currentItemChanged.connect(self._select_arrangement)
        self.geometries_list.currentItemChanged.connect(self._select_geometry)
        self.arrangement_name_edit.editingFinished.connect(self._edit_arrangement_name)
        self.arrangement_units_combo.currentTextChanged.connect(self._edit_arrangement_units)
        self.arrangement_phases_spin.valueChanged.connect(self._edit_arrangement_phases)
        self.arrangement_description_edit.editingFinished.connect(lambda: self._edit_arrangement_text("description", self.arrangement_description_edit))
        self.arrangement_source_edit.editingFinished.connect(lambda: self._edit_arrangement_text("source", self.arrangement_source_edit))
        self.add_position_button.clicked.connect(self._add_position)
        self.remove_position_button.clicked.connect(self._remove_position)
        self.new_arrangement_button.clicked.connect(self._new_arrangement)
        self.duplicate_arrangement_button.clicked.connect(self._duplicate_arrangement)
        self.delete_arrangement_button.clicked.connect(self._delete_arrangement)
        if self.assembly_session is not None:
            self.assembly_session.changed.connect(self._schedule_refresh)
        self.import_button.clicked.connect(self._import_geometries)
        self.export_button.clicked.connect(self._export_geometries)
        self.restore_button.clicked.connect(self._restore_defaults)
        self.help_button.clicked.connect(lambda: self.help_dialog.show_section("geometries"))
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.close)

    def _schedule_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self) -> None:
        self._refresh_pending = False
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        try:
            automatic = (
                None if self.assembly_session is None else self.assembly_session.result
            )
            automatic_count = 0 if automatic is None else len(automatic.assemblies)
            coverage = (
                "sem trechos carregados"
                if automatic is None or automatic.total_segments == 0
                else f"{automatic.assembled_segments:n}/{automatic.total_segments:n} trecho(s) atendido(s)"
            )
            self.summary_label.setText(
                f"{len(self.session.catalog.arrangements):n} arranjo(s), "
                f"{automatic_count:n} montagem(ns) automática(s) — {coverage}"
            )
            self._populate_arrangements()
            self._populate_geometries()
            self._load_arrangement_editor()
            self._load_geometry_editor()
        finally:
            self._loading = False

    def _populate_arrangements(self) -> None:
        with QSignalBlocker(self.arrangements_list):
            self.arrangements_list.clear()
            for arrangement in self.session.catalog.arrangements:
                pairs = coincident_positions(arrangement)
                suffix = "  ●" if pairs else ""
                item = QListWidgetItem(arrangement.name + suffix)
                item.setData(Qt.ItemDataRole.UserRole, arrangement.arrangement_id)
                if pairs:
                    item.setToolTip("Há posições coincidentes.")
                self.arrangements_list.addItem(item)
                if arrangement.arrangement_id == self._selected_arrangement_id:
                    self.arrangements_list.setCurrentItem(item)
            if self.arrangements_list.currentRow() < 0 and self.arrangements_list.count():
                self.arrangements_list.setCurrentRow(0)
            current = self.arrangements_list.currentItem()
            self._selected_arrangement_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)

    def _populate_geometries(self) -> None:
        with QSignalBlocker(self.geometries_list):
            self.geometries_list.clear()
            assemblies = (
                ()
                if self.assembly_session is None
                else self.assembly_session.result.assemblies
            )
            for assembly in assemblies:
                item = QListWidgetItem(
                    f"{assembly.name}  ({assembly.usage_count:n})"
                )
                item.setData(Qt.ItemDataRole.UserRole, assembly.assembly_id)
                item.setToolTip(
                    f"Trechos: {', '.join(assembly.segment_ids)}"
                )
                self.geometries_list.addItem(item)
                if assembly.assembly_id == self._selected_geometry_id:
                    self.geometries_list.setCurrentItem(item)
            if self.geometries_list.currentRow() < 0 and self.geometries_list.count():
                self.geometries_list.setCurrentRow(0)
            current = self.geometries_list.currentItem()
            self._selected_geometry_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)

    def selected_arrangement(self) -> ArrangementDefinition | None:
        return self.session.catalog.arrangement(self._selected_arrangement_id)

    def selected_geometry(self):  # noqa: ANN201
        return (
            None
            if self.assembly_session is None
            else self.assembly_session.assembly(self._selected_geometry_id)
        )

    def _select_arrangement(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._loading:
            return
        self._selected_arrangement_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)
        self._load_arrangement_editor()

    def _select_geometry(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._loading:
            return
        self._selected_geometry_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)
        self._load_geometry_editor()

    def _load_arrangement_editor(self) -> None:
        arrangement = self.selected_arrangement()
        has_item = arrangement is not None
        self.arrangement_empty_label.setVisible(not has_item)
        self.duplicate_arrangement_button.setEnabled(has_item)
        self.delete_arrangement_button.setEnabled(has_item)
        self.add_position_button.setEnabled(has_item)
        self.remove_position_button.setEnabled(has_item and arrangement.conductor_count > 1 if arrangement else False)
        self.position_model.set_arrangement(self._selected_arrangement_id)
        self.arrangement_preview.set_content(self.session.catalog, arrangement)
        if arrangement is None:
            self.arrangement_issue_label.clear()
            return
        self.arrangement_name_edit.setText(arrangement.name)
        self.arrangement_units_combo.setCurrentText(arrangement.units)
        self.arrangement_phases_spin.setMaximum(arrangement.conductor_count)
        self.arrangement_phases_spin.setValue(arrangement.phase_count)
        self.arrangement_description_edit.setText(arrangement.description)
        self.arrangement_source_edit.setText(arrangement.source)
        pairs = coincident_positions(arrangement)
        self.arrangement_issue_label.setText(
            "Posições coincidentes: " + ", ".join(f"{left} e {right}" for left, right in pairs) + "."
            if pairs
            else ""
        )

    def _load_geometry_editor(self) -> None:
        assembly = self.selected_geometry()
        has_item = assembly is not None
        automatic = (
            None if self.assembly_session is None else self.assembly_session.result
        )
        has_lines = automatic is not None and automatic.total_segments > 0
        if not has_lines:
            self.geometry_empty_label.setText(
                "Importe trechos para gerar as montagens automáticas."
            )
        elif not has_item:
            self.geometry_empty_label.setText(
                "Nenhuma montagem completa foi gerada. Consulte os diagnósticos abaixo."
            )
        self.geometry_empty_label.setVisible(not has_item)
        self.assignment_model.set_assembly(self._selected_geometry_id)

        self.automatic_issue_model.refresh()
        issue_count = 0 if automatic is None else len(automatic.issues)
        self.automatic_issue_table.setVisible(issue_count > 0)
        if automatic is None or automatic.total_segments == 0:
            self.automatic_issue_summary.setText("Sem diagnósticos: não há trechos carregados.")
        elif issue_count:
            self.automatic_issue_summary.setText(
                f"Diagnósticos agrupados: {issue_count:n}. "
                f"{automatic.unassembled_segments:n} trecho(s) sem montagem completa."
            )
        else:
            self.automatic_issue_summary.setText(
                "Todos os trechos receberam uma montagem automática."
            )
        self.automatic_draft_label.setText(
            "Há rascunhos de arranjos. As montagens automáticas continuam usando "
            "a última versão salva."
            if self.session.geometries_dirty
            else "As montagens usam exclusivamente bibliotecas e mapas salvos."
        )

        if assembly is None:
            self.geometry_issue_label.clear()
            for label in (
                self.automatic_name_label,
                self.automatic_arrangement_label,
                self.automatic_phases_label,
                self.automatic_phase_cable_label,
                self.automatic_neutral_cable_label,
                self.automatic_usage_label,
                self.automatic_reduce_label,
            ):
                label.setText("—")
            catalog = (
                self.session.saved_catalog()
                if self.assembly_session is None
                else self.assembly_session.catalog
            )
            self.geometry_preview.set_content(catalog, None)
            return

        assert self.assembly_session is not None
        base = self.assembly_session.catalog.arrangement(
            assembly.key.arrangement_id
        )
        phase_cable = self.assembly_session.catalog.cable(
            assembly.key.phase_cable_id
        )
        neutral_cable = self.assembly_session.catalog.cable(
            assembly.key.neutral_cable_id
        )
        bindings = ", ".join(
            f"F{index + 1} → {letter}"
            for index, letter in enumerate(assembly.phase_letters)
        )
        shown_segments = assembly.segment_ids[:20]
        segment_text = ", ".join(shown_segments)
        if len(assembly.segment_ids) > len(shown_segments):
            segment_text += (
                f" … e mais {len(assembly.segment_ids) - len(shown_segments):n}"
            )
        self.automatic_name_label.setText(assembly.name)
        self.automatic_arrangement_label.setText("—" if base is None else base.name)
        self.automatic_phases_label.setText(bindings)
        self.automatic_phase_cable_label.setText(
            "—" if phase_cable is None else phase_cable.name
        )
        self.automatic_neutral_cable_label.setText(
            "Removido/não aplicável" if neutral_cable is None else neutral_cable.name
        )
        self.automatic_usage_label.setText(
            f"{assembly.usage_count:n} — {segment_text}"
        )
        self.automatic_reduce_label.setText(
            "Sim" if assembly.geometry.reduce else "Não"
        )
        self.geometry_issue_label.setText(
            "Posições de fase usadas em sequência; posições excedentes do arranjo base foram descartadas."
        )
        self.geometry_preview.set_content(
            self.assembly_session.catalog,
            assembly.arrangement,
            assembly.geometry,
            assembly.phase_letters,
        )

    def _edit_arrangement_name(self) -> None:
        if self._loading or (arrangement := self.selected_arrangement()) is None:
            return
        name = normalize_library_name(self.arrangement_name_edit.text())
        if not name or any(item.arrangement_id != arrangement.arrangement_id and item.name.casefold() == name.casefold() for item in self.session.catalog.arrangements):
            QMessageBox.warning(self, "Nome inválido", "Informe um nome não vazio e exclusivo para o arranjo.")
            self._load_arrangement_editor()
            return
        if name != arrangement.name:
            arrangement.name = name
            self.session.mark_geometries_changed()
        if self.arrangement_name_edit.text() != name:
            self.arrangement_name_edit.setText(name)

    def _edit_arrangement_units(self, units: str) -> None:
        if self._loading or (arrangement := self.selected_arrangement()) is None:
            return
        if arrangement.units != units:
            arrangement.units = units
            self.session.mark_geometries_changed()

    def _edit_arrangement_phases(self, phases: int) -> None:
        if self._loading or (arrangement := self.selected_arrangement()) is None:
            return
        if arrangement.phase_count != phases:
            arrangement.phase_count = phases
            self.session.mark_geometries_changed()

    def _edit_arrangement_text(self, attribute: str, edit: QLineEdit) -> None:
        if self._loading or (arrangement := self.selected_arrangement()) is None:
            return
        value = edit.text().strip()
        if getattr(arrangement, attribute) != value:
            setattr(arrangement, attribute, value)
            self.session.mark_geometries_changed()

    def _add_position(self) -> None:
        arrangement = self.selected_arrangement()
        if arrangement is None:
            return
        last = arrangement.positions[-1] if arrangement.positions else ConductorPosition(0.0, 0.0)
        arrangement.positions.append(ConductorPosition(last.x + 1.0, last.height))
        self.session.catalog.synchronize_geometry_slots(arrangement.arrangement_id)
        self.session.mark_geometries_changed()

    def _remove_position(self) -> None:
        arrangement = self.selected_arrangement()
        current = self.positions_table.currentIndex()
        if arrangement is None or arrangement.conductor_count <= 1 or not current.isValid():
            return
        del arrangement.positions[current.row()]
        arrangement.phase_count = min(arrangement.phase_count, arrangement.conductor_count)
        self.session.catalog.synchronize_geometry_slots(arrangement.arrangement_id)
        self.session.mark_geometries_changed()

    def _new_arrangement(self) -> None:
        name = unique_name("Arranjo novo", (item.name for item in self.session.catalog.arrangements))
        arrangement = ArrangementDefinition(
            unique_id(name, (item.arrangement_id for item in self.session.catalog.arrangements)),
            name,
            1,
            "m",
            [ConductorPosition(0.0, 9.0), ConductorPosition(0.3, 8.0)],
        )
        self.session.catalog.arrangements.append(arrangement)
        self._selected_arrangement_id = arrangement.arrangement_id
        self.session.mark_geometries_changed()

    def _duplicate_arrangement(self) -> None:
        arrangement = self.selected_arrangement()
        if arrangement is None:
            return
        duplicate = copy.deepcopy(arrangement)
        duplicate.name = unique_name(arrangement.name, (item.name for item in self.session.catalog.arrangements))
        duplicate.arrangement_id = unique_id(duplicate.name, (item.arrangement_id for item in self.session.catalog.arrangements))
        duplicate.source = ""
        self.session.catalog.arrangements.append(duplicate)
        self._selected_arrangement_id = duplicate.arrangement_id
        self.session.mark_geometries_changed()

    def _delete_arrangement(self) -> None:
        arrangement = self.selected_arrangement()
        if arrangement is None:
            return
        uses = (
            ()
            if self.assembly_session is None
            else self.assembly_session.assemblies_using_arrangement(
                arrangement.arrangement_id
            )
        )
        if uses:
            QMessageBox.warning(
                self,
                "Arranjo em uso",
                f'"{arrangement.name}" é usado por {len(uses)} montagem(ns) automática(s).\n\n'
                "Altere os vínculos de ARRANJO_ID antes de excluí-lo.",
            )
            return
        mapping_session = self.session.mapping_session
        mapped_ids = (
            ()
            if mapping_session is None
            else mapping_session.mapped_arrangement_source_ids(arrangement.name)
        )
        if mapped_ids:
            QMessageBox.warning(
                self,
                "Arranjo mapeado",
                f'"{arrangement.name}" é usado pelos ARRANJO_ID: {", ".join(mapped_ids)}.\n\n'
                "Remova esses vínculos em Configurações > OpenDSS antes de excluir.",
            )
            return
        if QMessageBox.question(self, "Excluir arranjo", f'Excluir o arranjo "{arrangement.name}"?') != QMessageBox.StandardButton.Yes:
            return
        self.session.catalog.arrangements.remove(arrangement)
        self._selected_arrangement_id = None
        self.session.mark_geometries_changed()

    def _import_geometries(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar geometrias", "", "JSON (*.json)")
        if not path:
            return
        try:
            arrangements, geometries = read_geometries_file(path)
        except (OSError, LibraryFormatError) as exc:
            QMessageBox.warning(self, "Arquivo inválido", str(exc))
            return
        if QMessageBox.question(
            self,
            "Substituir geometrias",
            f"Substituir os {len(self.session.catalog.arrangements):n} arranjos atuais pelos {len(arrangements):n} do arquivo?\n\n"
            f"As {len(geometries):n} montagem(ns) manuais do arquivo serão preservadas apenas como legado. "
            "A alteração ficará pendente até Salvar.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.replace_geometries_from_file(path)
        except MappedLibraryItemError as exc:
            QMessageBox.warning(self, "Arranjos mapeados", str(exc))
            return
        self._selected_arrangement_id = self.session.catalog.arrangements[0].arrangement_id if self.session.catalog.arrangements else None
        self._selected_geometry_id = None

    def _export_geometries(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Exportar geometrias", "geometrias.json", "JSON (*.json)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            self.session.export_geometries(target)
        except (OSError, LibraryFormatError) as exc:
            QMessageBox.warning(self, "Falha ao exportar", str(exc))
            return
        self.status_label.setText(f"Geometrias exportadas para {target.name}.")

    def _restore_defaults(self) -> None:
        if QMessageBox.question(
            self,
            "Restaurar geometrias padrão",
            "Restaurar os 9 arranjos de fábrica?\n\nAs 12 montagens manuais do pacote serão mantidas somente no campo legado. "
            "Os arranjos criados ou editados serão substituídos no rascunho.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.restore_default_geometries()
        except MappedLibraryItemError as exc:
            QMessageBox.warning(self, "Arranjos mapeados", str(exc))
            return
        self._selected_arrangement_id = self.session.catalog.arrangements[0].arrangement_id
        self._selected_geometry_id = None

    def _save(self) -> bool:
        try:
            self.session.save_geometry_drafts()
        except (OSError, LibraryFormatError, MappedLibraryItemError) as exc:
            QMessageBox.warning(self, "Falha ao salvar", str(exc))
            return False
        self.status_label.setText(
            f"{len(self.session.catalog.arrangements):n} arranjo(s) salvo(s). "
            "As montagens automáticas não são persistidas."
        )
        return True

    def _sync_dirty_state(self, dirty: bool) -> None:
        self.save_button.setEnabled(dirty)
        self.setWindowTitle("Biblioteca de Geometrias OpenDSS" + (" *" if dirty else ""))
        if hasattr(self, "automatic_draft_label"):
            self.automatic_draft_label.setText(
                "Há rascunhos de arranjos. As montagens automáticas continuam usando "
                "a última versão salva."
                if dirty
                else "As montagens usam exclusivamente bibliotecas e mapas salvos."
            )

    def confirm_pending_changes(self) -> bool:
        if not self.session.geometries_dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Alterações não salvas",
            "Há alterações não salvas na biblioteca de geometrias. Salvar agora?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        self.session.discard_geometry_drafts()
        return True

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.confirm_pending_changes():
            event.accept()
        else:
            event.ignore()
