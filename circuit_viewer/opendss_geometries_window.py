"""Cadastro nativo de arranjos ``LineSpacing`` e montagens ``LineGeometry``."""

from __future__ import annotations

import copy
import math
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSignalBlocker, QTimer, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QStyledItemDelegate,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .opendss_geometry_preview import CartesianGeometryView, GeometryCutDialog
from .opendss_library import (
    OPEN_DSS_UNITS,
    ArrangementDefinition,
    ConductorPosition,
    GeometryDefinition,
    LibraryFormatError,
    coincident_positions,
    geometry_ampacity,
    geometry_issues,
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


class GeometryCableTableModel(QAbstractTableModel):
    HEADERS = ("#", "Papel", "x / h", "Cabo")

    def __init__(self, session: OpenDssLibrarySession, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.session = session
        self.geometry_id: str | None = None

    def set_geometry(self, geometry_id: str | None) -> None:
        self.beginResetModel()
        self.geometry_id = geometry_id
        self.endResetModel()

    def context(self) -> tuple[GeometryDefinition | None, ArrangementDefinition | None]:
        geometry = self.session.catalog.geometry(self.geometry_id)
        arrangement = None if geometry is None else self.session.catalog.arrangement(geometry.arrangement_id)
        return geometry, arrangement

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        _geometry, arrangement = self.context()
        return 0 if parent.isValid() or arrangement is None else arrangement.conductor_count

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= int(section) < len(self.HEADERS):
                return self.HEADERS[int(section)]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        geometry, arrangement = self.context()
        if not index.isValid() or geometry is None or arrangement is None:
            return None
        row = index.row()
        position = arrangement.positions[row]
        cable_id = geometry.cable_ids[row] if row < len(geometry.cable_ids) else None
        cable = self.session.catalog.cable(cable_id)
        values = (
            row + 1,
            f"F{row + 1}" if row < arrangement.phase_count else "N",
            f"{position.x:g} / {position.height:g} {arrangement.units}",
            "— selecione —" if cable is None else cable.name,
        )
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            return values[index.column()]
        if role == Qt.ItemDataRole.UserRole and index.column() == 3:
            return cable_id
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 3 and cable_id and cable is None:
            return f"Cabo '{cable_id}' ausente da biblioteca."
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == 0:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def flags(self, index: QModelIndex):  # noqa: ANN201
        flags = super().flags(index)
        if index.isValid() and index.column() == 3:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: ANN001, N802
        geometry, arrangement = self.context()
        if geometry is None or arrangement is None or index.column() != 3 or role not in {Qt.ItemDataRole.EditRole, Qt.ItemDataRole.UserRole}:
            return False
        while len(geometry.cable_ids) < arrangement.conductor_count:
            geometry.cable_ids.append(None)
        cable_id = str(value).strip() if value else None
        cable_id = cable_id if cable_id and self.session.catalog.cable(cable_id) is not None else None
        if geometry.cable_ids[index.row()] == cable_id:
            return True
        geometry.cable_ids[index.row()] = cable_id
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])
        self.session.mark_geometries_changed()
        return True


class CableComboDelegate(QStyledItemDelegate):
    def __init__(self, session: OpenDssLibrarySession, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.session = session

    def createEditor(self, parent, option, index):  # noqa: ANN001, ANN201, N802
        combo = QComboBox(parent)
        combo.addItem("— selecione —", None)
        for cable in sorted(self.session.catalog.cables, key=lambda item: item.name.casefold()):
            suffix = " (CN)" if cable.is_concentric else ""
            combo.addItem(cable.name + suffix, cable.cable_id)
        return combo

    def setEditorData(self, editor: QComboBox, index: QModelIndex) -> None:  # noqa: N802
        cable_id = index.data(Qt.ItemDataRole.UserRole)
        editor.setCurrentIndex(max(editor.findData(cable_id), 0))

    def setModelData(self, editor: QComboBox, model, index: QModelIndex) -> None:  # noqa: ANN001, N802
        model.setData(index, editor.currentData(), Qt.ItemDataRole.UserRole)


class OpenDssGeometriesWindow(QDialog):
    def __init__(
        self,
        session: OpenDssLibrarySession,
        help_dialog: OpenDssLibraryHelpDialog,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.help_dialog = help_dialog
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
        self.tabs.addTab(self._build_geometries_tab(), "Montagens")
        root.addWidget(self.tabs, 1)
        self.status_label = QLabel(self)
        self.status_label.setObjectName("opendss_geometries_status")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        root.addLayout(self._build_actions())

        self.cut_dialog = GeometryCutDialog(session.catalog, self)
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
        layout = QHBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal, tab)
        side = QWidget(splitter)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.geometries_list = QListWidget(side)
        self.geometries_list.setObjectName("opendss_montages_list")
        side_layout.addWidget(self.geometries_list, 1)
        geometry_buttons = QHBoxLayout()
        self.new_geometry_button = QPushButton("Nova", side)
        self.duplicate_geometry_button = QPushButton("Duplicar", side)
        self.delete_geometry_button = QPushButton("Excluir…", side)
        for button in (self.new_geometry_button, self.duplicate_geometry_button, self.delete_geometry_button):
            geometry_buttons.addWidget(button)
        side_layout.addLayout(geometry_buttons)

        scroll = QScrollArea(splitter)
        scroll.setWidgetResizable(True)
        editor = QWidget(scroll)
        column = QVBoxLayout(editor)
        self.geometry_empty_label = QLabel("Selecione uma montagem ou crie uma nova.", editor)
        column.addWidget(self.geometry_empty_label)
        group = QGroupBox("Montagem (LineGeometry)", editor)
        form = QFormLayout(group)
        self.geometry_name_edit = QLineEdit(group)
        form.addRow("Nome:", self.geometry_name_edit)
        self.geometry_arrangement_combo = QComboBox(group)
        form.addRow("Arranjo:", self.geometry_arrangement_combo)
        self.geometry_reduce_check = QCheckBox("Aplicar redução de Kron", group)
        form.addRow("Redução:", self.geometry_reduce_check)
        self.geometry_description_edit = QLineEdit(group)
        form.addRow("Descrição:", self.geometry_description_edit)
        self.geometry_ampacity_label = QLabel(group)
        form.addRow("Ampacidade:", self.geometry_ampacity_label)
        column.addWidget(group)
        self.assignment_model = GeometryCableTableModel(self.session, self)
        self.assignments_table = AllRowsTableView(editor)
        self.assignments_table.setObjectName("opendss_geometry_cables_table")
        self.assignments_table.setModel(self.assignment_model)
        self.assignments_table.setItemDelegateForColumn(3, CableComboDelegate(self.session, self.assignments_table))
        self.assignments_table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        enable_interactive_columns(self.assignments_table)
        column.addWidget(self.assignments_table)
        self.geometry_issue_label = QLabel(editor)
        self.geometry_issue_label.setWordWrap(True)
        column.addWidget(self.geometry_issue_label)
        self.geometry_preview = CartesianGeometryView(editor)
        self.geometry_preview.setObjectName("opendss_geometry_preview")
        column.addWidget(self.geometry_preview, 1)
        self.cut_button = QPushButton("Ampliar gráfico…", editor)
        column.addWidget(self.cut_button)
        scroll.setWidget(editor)
        splitter.addWidget(side)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)
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
        self.geometry_name_edit.editingFinished.connect(self._edit_geometry_name)
        self.geometry_arrangement_combo.currentIndexChanged.connect(self._edit_geometry_arrangement)
        self.geometry_reduce_check.toggled.connect(self._edit_geometry_reduce)
        self.geometry_description_edit.editingFinished.connect(self._edit_geometry_description)
        self.new_geometry_button.clicked.connect(self._new_geometry)
        self.duplicate_geometry_button.clicked.connect(self._duplicate_geometry)
        self.delete_geometry_button.clicked.connect(self._delete_geometry)
        self.cut_button.clicked.connect(self._show_cut)
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
            self.summary_label.setText(
                f"{len(self.session.catalog.arrangements):n} arranjo(s) e {len(self.session.catalog.geometries):n} montagem(ns)"
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
            for geometry in self.session.catalog.geometries:
                issues = geometry_issues(geometry, self.session.catalog)
                suffix = "  ●" if issues else ""
                item = QListWidgetItem(geometry.name + suffix)
                item.setData(Qt.ItemDataRole.UserRole, geometry.geometry_id)
                if issues:
                    item.setToolTip("; ".join(issues))
                self.geometries_list.addItem(item)
                if geometry.geometry_id == self._selected_geometry_id:
                    self.geometries_list.setCurrentItem(item)
            if self.geometries_list.currentRow() < 0 and self.geometries_list.count():
                self.geometries_list.setCurrentRow(0)
            current = self.geometries_list.currentItem()
            self._selected_geometry_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)

    def selected_arrangement(self) -> ArrangementDefinition | None:
        return self.session.catalog.arrangement(self._selected_arrangement_id)

    def selected_geometry(self) -> GeometryDefinition | None:
        return self.session.catalog.geometry(self._selected_geometry_id)

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
        geometry = self.selected_geometry()
        has_item = geometry is not None
        self.geometry_empty_label.setVisible(not has_item)
        self.duplicate_geometry_button.setEnabled(has_item)
        self.delete_geometry_button.setEnabled(has_item)
        self.cut_button.setEnabled(has_item)
        self.assignment_model.set_geometry(self._selected_geometry_id)
        with QSignalBlocker(self.geometry_arrangement_combo):
            self.geometry_arrangement_combo.clear()
            for arrangement in self.session.catalog.arrangements:
                self.geometry_arrangement_combo.addItem(
                    f"{arrangement.name} ({arrangement.conductor_count} cond.)",
                    arrangement.arrangement_id,
                )
        if geometry is None:
            self.geometry_issue_label.clear()
            self.geometry_ampacity_label.setText("—")
            self.geometry_preview.set_content(self.session.catalog, None)
            return
        self.geometry_name_edit.setText(geometry.name)
        self.geometry_arrangement_combo.setCurrentIndex(max(self.geometry_arrangement_combo.findData(geometry.arrangement_id), 0))
        self.geometry_reduce_check.setChecked(geometry.reduce)
        self.geometry_description_edit.setText(geometry.description)
        arrangement = self.session.catalog.arrangement(geometry.arrangement_id)
        ampacity = geometry_ampacity(geometry, self.session.catalog)
        self.geometry_ampacity_label.setText("—" if ampacity is None else f"{ampacity:g} A")
        issues = geometry_issues(geometry, self.session.catalog)
        self.geometry_issue_label.setText("Atenção: " + "; ".join(issues) + "." if issues else "")
        self.geometry_preview.set_content(self.session.catalog, arrangement, geometry)

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
        uses = self.session.catalog.geometries_using_arrangement(arrangement.arrangement_id)
        if uses:
            QMessageBox.warning(self, "Arranjo em uso", f'"{arrangement.name}" é usado por {len(uses)} montagem(ns):\n\n• ' + "\n• ".join(item.name for item in uses))
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

    def _edit_geometry_name(self) -> None:
        if self._loading or (geometry := self.selected_geometry()) is None:
            return
        name = self.geometry_name_edit.text().strip()
        if not name or any(item.geometry_id != geometry.geometry_id and item.name.casefold() == name.casefold() for item in self.session.catalog.geometries):
            QMessageBox.warning(self, "Nome inválido", "Informe um nome não vazio e exclusivo para a montagem.")
            self._load_geometry_editor()
            return
        if geometry.name != name:
            geometry.name = name
            self.session.mark_geometries_changed()

    def _edit_geometry_arrangement(self) -> None:
        if self._loading or (geometry := self.selected_geometry()) is None:
            return
        arrangement_id = self.geometry_arrangement_combo.currentData()
        if arrangement_id and geometry.arrangement_id != arrangement_id:
            geometry.arrangement_id = str(arrangement_id)
            self.session.catalog.synchronize_geometry_slots(geometry.arrangement_id)
            self.session.mark_geometries_changed()

    def _edit_geometry_reduce(self, checked: bool) -> None:
        if self._loading or (geometry := self.selected_geometry()) is None:
            return
        if geometry.reduce != checked:
            geometry.reduce = checked
            self.session.mark_geometries_changed()

    def _edit_geometry_description(self) -> None:
        if self._loading or (geometry := self.selected_geometry()) is None:
            return
        value = self.geometry_description_edit.text().strip()
        if geometry.description != value:
            geometry.description = value
            self.session.mark_geometries_changed()

    def _new_geometry(self) -> None:
        if not self.session.catalog.arrangements:
            QMessageBox.warning(self, "Sem arranjos", "Crie um arranjo antes da montagem.")
            return
        arrangement = self.selected_arrangement() or self.session.catalog.arrangements[0]
        name = unique_name("Montagem nova", (item.name for item in self.session.catalog.geometries))
        geometry = GeometryDefinition(
            unique_id(name, (item.geometry_id for item in self.session.catalog.geometries)),
            name,
            arrangement.arrangement_id,
            [None] * arrangement.conductor_count,
            arrangement.conductor_count > arrangement.phase_count,
        )
        self.session.catalog.geometries.append(geometry)
        self._selected_geometry_id = geometry.geometry_id
        self.session.mark_geometries_changed()

    def _duplicate_geometry(self) -> None:
        geometry = self.selected_geometry()
        if geometry is None:
            return
        duplicate = copy.deepcopy(geometry)
        duplicate.name = unique_name(geometry.name, (item.name for item in self.session.catalog.geometries))
        duplicate.geometry_id = unique_id(duplicate.name, (item.geometry_id for item in self.session.catalog.geometries))
        self.session.catalog.geometries.append(duplicate)
        self._selected_geometry_id = duplicate.geometry_id
        self.session.mark_geometries_changed()

    def _delete_geometry(self) -> None:
        geometry = self.selected_geometry()
        if geometry is None:
            return
        if QMessageBox.question(self, "Excluir montagem", f'Excluir a montagem "{geometry.name}"?') != QMessageBox.StandardButton.Yes:
            return
        self.session.catalog.geometries.remove(geometry)
        self._selected_geometry_id = None
        self.session.mark_geometries_changed()

    def _show_cut(self) -> None:
        if self._selected_geometry_id:
            self.cut_dialog.show_geometry(self._selected_geometry_id)

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
            f"Substituir os {len(self.session.catalog.arrangements):n} arranjos e {len(self.session.catalog.geometries):n} montagens atuais pelos {len(arrangements):n} arranjos e {len(geometries):n} montagens do arquivo?\n\nA alteração ficará pendente até Salvar.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.replace_geometries_from_file(path)
        except MappedLibraryItemError as exc:
            QMessageBox.warning(self, "Arranjos mapeados", str(exc))
            return
        self._selected_arrangement_id = self.session.catalog.arrangements[0].arrangement_id if self.session.catalog.arrangements else None
        self._selected_geometry_id = self.session.catalog.geometries[0].geometry_id if self.session.catalog.geometries else None
        self._warn_missing_references()

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
            "Restaurar os 9 arranjos e 12 montagens de fábrica?\n\nOs itens criados ou editados serão substituídos no rascunho.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.restore_default_geometries()
        except MappedLibraryItemError as exc:
            QMessageBox.warning(self, "Arranjos mapeados", str(exc))
            return
        self._selected_arrangement_id = self.session.catalog.arrangements[0].arrangement_id
        self._selected_geometry_id = self.session.catalog.geometries[0].geometry_id

    def _warn_missing_references(self) -> None:
        issues = sum(bool(geometry_issues(item, self.session.catalog)) for item in self.session.catalog.geometries)
        if issues:
            message = f"{issues:n} montagem(ns) ficaram incompletas ou com referências ausentes. Importe a biblioteca de cabos correspondente ou corrija os campos destacados."
            self.status_label.setText(message)
            QMessageBox.warning(self, "Montagens incompletas", message)

    def _save(self) -> bool:
        try:
            self.session.save_geometry_drafts()
        except (OSError, LibraryFormatError, MappedLibraryItemError) as exc:
            QMessageBox.warning(self, "Falha ao salvar", str(exc))
            return False
        self.status_label.setText(
            f"{len(self.session.catalog.arrangements):n} arranjo(s) e {len(self.session.catalog.geometries):n} montagem(ns) salvos."
        )
        return True

    def _sync_dirty_state(self, dirty: bool) -> None:
        self.save_button.setEnabled(dirty)
        self.setWindowTitle("Biblioteca de Geometrias OpenDSS" + (" *" if dirty else ""))

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
