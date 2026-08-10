"""Cadastro nativo de condutores ``WireData`` e ``CNData``."""

from __future__ import annotations

import copy
import math
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QColor, QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .opendss_library import (
    DEFAULT_STRANDING_FILL_FACTOR,
    OPEN_DSS_UNITS,
    CableDefinition,
    LibraryFormatError,
    cable_issues,
    estimate_diameter_from_section,
    estimate_radius_from_gmr,
    normalize_library_name,
    unique_id,
    unique_name,
)
from .opendss_library_help import OpenDssLibraryHelpDialog
from .opendss_library_session import OpenDssLibrarySession
from .opendss_mapping_session import MappedLibraryItemError
from .opendss_library_store import read_cables_file
from .table_columns import EXCEL_LIKE_TABLE_STYLE, enable_interactive_columns


def _format_number(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


def _parse_number(text: str, *, integer: bool = False) -> float | int | None:
    value = str(text).strip()
    if not value:
        return None
    if "," in value and "." in value:
        raise ValueError("Use ponto ou vírgula como separador decimal, não ambos.")
    try:
        number = float(value.replace(",", "."))
    except ValueError as exc:
        raise ValueError("Informe um número válido.") from exc
    if not math.isfinite(number):
        raise ValueError("Informe um número finito.")
    if integer:
        if not number.is_integer():
            raise ValueError("Informe um número inteiro.")
        return int(number)
    return number


class OpenDssCableTableModel(QAbstractTableModel):
    HEADERS = ("Nome", "Tipo", "Família", "R", "GMR", "Dimensão", "Ampac.", "Usos", "Estado")

    def __init__(self, session: OpenDssLibrarySession, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.session = session

    def refresh(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.session.catalog.cables)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= int(section) < len(self.HEADERS):
                return self.HEADERS[int(section)]
        return None

    def cable(self, row: int) -> CableDefinition:
        return self.session.catalog.cables[int(row)]

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid():
            return None
        cable = self.cable(index.row())
        uses = len(self.session.catalog.geometries_using_cable(cable.cable_id))
        resistance = cable.rac if cable.rac is not None else cable.rdc
        resistance_kind = "ac" if cable.rac is not None else "dc"
        dimension = (
            f"r={_format_number(cable.radius)} {cable.radius_units}"
            if cable.radius is not None
            else f"Ø={_format_number(cable.diameter)} {cable.radius_units}"
            if cable.diameter is not None
            else "—"
        )
        issues = cable_issues(cable)
        state = "Incompleto" if issues else "Estimado" if cable.radius_estimated else "Completo"
        values = (
            cable.name,
            "CN" if cable.is_concentric else "Nu",
            cable.family or "—",
            f"{_format_number(resistance)} ({resistance_kind}) {cable.resistance_units}" if resistance is not None else "—",
            f"{_format_number(cable.gmr)} {cable.gmr_units}" if cable.gmr is not None else "—",
            dimension,
            f"{_format_number(cable.normal_amps)} A" if cable.normal_amps is not None else "—",
            uses or "—",
            state,
        )
        if role == Qt.ItemDataRole.DisplayRole:
            return values[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            sort_values = (
                cable.name.casefold(),
                cable.cable_type,
                cable.family.casefold(),
                float("inf") if resistance is None else resistance,
                float("inf") if cable.gmr is None else cable.gmr,
                float("inf") if cable.radius is None and cable.diameter is None else (cable.radius if cable.radius is not None else cable.diameter),
                float("inf") if cable.normal_amps is None else cable.normal_amps,
                uses,
                state,
            )
            return sort_values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            if issues:
                return "Incompleto: falta " + ", ".join(issues)
            if cable.radius_estimated:
                return "A dimensão foi estimada e deve ser conferida."
            if index.column() == 0 and cable.description:
                return cable.description
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 8:
            if issues:
                return QColor("#C62828")
            if cable.radius_estimated:
                return QColor("#1976D2")
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in {3, 4, 5, 6, 7}:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None


class CableFilterProxyModel(QSortFilterProxyModel):
    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        expression = self.filterRegularExpression()
        if not expression.pattern():
            return True
        model = self.sourceModel()
        if not isinstance(model, OpenDssCableTableModel):
            return True
        cable = model.cable(source_row)
        searchable = " ".join(
            (cable.cable_id, cable.name, cable.family, cable.description, cable.source)
        )
        return expression.match(searchable).hasMatch()


class OpenDssCablesWindow(QDialog):
    """Janela não modal, com alterações pendentes até ``Salvar``."""

    _NUMERIC_FIELDS = {
        "rac": False,
        "rdc": False,
        "gmr": False,
        "diameter": False,
        "radius": False,
        "normal_amps": False,
        "emergency_amps": False,
        "nominal_section": False,
        "strand_count": True,
        "strand_diameter": False,
        "strand_resistance": False,
        "strand_gmr": False,
        "relative_permittivity": False,
        "insulation_layer": False,
        "insulation_diameter": False,
        "cable_diameter": False,
    }

    def __init__(
        self,
        session: OpenDssLibrarySession,
        help_dialog: OpenDssLibraryHelpDialog,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.help_dialog = help_dialog
        self._selected_id: str | None = None
        self._loading = False
        self.setWindowTitle("Biblioteca de Cabos OpenDSS")
        self.setModal(False)
        self.resize(1240, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Buscar:", self))
        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("opendss_cables_search")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText("Nome, família, descrição ou origem")
        header.addWidget(self.search_edit, 1)
        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("opendss_cables_summary")
        header.addWidget(self.summary_label)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.table_model = OpenDssCableTableModel(session, self)
        self.proxy_model = CableFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)
        self.proxy_model.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy_model.setDynamicSortFilter(True)
        self.table = QTableView(splitter)
        self.table.setObjectName("opendss_cables_table")
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        enable_interactive_columns(self.table)
        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self.editor_scroll = QScrollArea(splitter)
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setMinimumWidth(420)
        self.editor = QWidget(self.editor_scroll)
        self.editor_layout = QVBoxLayout(self.editor)
        self.editor_scroll.setWidget(self.editor)
        self._build_editor()
        splitter.addWidget(self.table)
        splitter.addWidget(self.editor_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self.reference_label = QLabel(self)
        self.reference_label.setObjectName("opendss_cables_reference_warning")
        self.reference_label.setWordWrap(True)
        root.addWidget(self.reference_label)

        root.addLayout(self._build_actions())
        self._connect_signals()
        self.refresh()
        self._sync_dirty_state(session.cables_dirty)
        if session.cables_load.issue:
            self.reference_label.setText(session.cables_load.issue)

    def _build_editor(self) -> None:
        self.empty_label = QLabel("Selecione um cabo ou crie um novo cadastro.", self.editor)
        self.empty_label.setWordWrap(True)
        self.editor_layout.addWidget(self.empty_label)
        self.issue_label = QLabel(self.editor)
        self.issue_label.setObjectName("opendss_cable_issues")
        self.issue_label.setWordWrap(True)
        self.editor_layout.addWidget(self.issue_label)

        identity = QGroupBox("Identificação", self.editor)
        identity_form = QFormLayout(identity)
        self.name_edit = QLineEdit(identity)
        self.name_edit.setObjectName("opendss_cable_name")
        identity_form.addRow("Nome:", self.name_edit)
        self.type_combo = QComboBox(identity)
        self.type_combo.setObjectName("opendss_cable_type")
        self.type_combo.addItem("Fio nu (WireData)", "wire")
        self.type_combo.addItem("Concêntrico (CNData)", "cn")
        identity_form.addRow("Tipo:", self.type_combo)
        self.family_edit = QLineEdit(identity)
        identity_form.addRow("Família:", self.family_edit)
        self.description_edit = QLineEdit(identity)
        identity_form.addRow("Descrição:", self.description_edit)
        self.editor_layout.addWidget(identity)

        self.numeric_edits: dict[str, QLineEdit] = {}
        series = QGroupBox("Série — impedância", self.editor)
        series_form = QFormLayout(series)
        self._add_numeric_row(series_form, "Rac (Ω/unid.):", "rac")
        self._add_numeric_row(series_form, "Rdc (Ω/unid.):", "rdc")
        self.resistance_units_combo = self._units_combo(series)
        series_form.addRow("Unidade de R*:", self.resistance_units_combo)
        self._add_numeric_row(series_form, "GMR*:", "gmr")
        self.gmr_units_combo = self._units_combo(series)
        series_form.addRow("Unidade do GMR*:", self.gmr_units_combo)
        self.editor_layout.addWidget(series)

        shunt = QGroupBox("Shunt — dimensão física", self.editor)
        shunt_form = QFormLayout(shunt)
        self._add_numeric_row(shunt_form, "Diâmetro:", "diameter")
        self._add_numeric_row(shunt_form, "Raio:", "radius")
        self.radius_units_combo = self._units_combo(shunt)
        shunt_form.addRow("Unidade da dimensão*:", self.radius_units_combo)
        self.estimate_gmr_button = QPushButton("Estimar raio pelo GMR", shunt)
        shunt_form.addRow("Sem raio medido:", self.estimate_gmr_button)
        estimate_row = QWidget(shunt)
        estimate_layout = QHBoxLayout(estimate_row)
        estimate_layout.setContentsMargins(0, 0, 0, 0)
        self.section_estimate_edit = QLineEdit(estimate_row)
        self.section_estimate_edit.setPlaceholderText("Seção mm²")
        self.fill_factor_edit = QLineEdit(_format_number(DEFAULT_STRANDING_FILL_FACTOR), estimate_row)
        self.fill_factor_edit.setPlaceholderText("Fator k")
        self.estimate_section_button = QPushButton("Estimar diâmetro", estimate_row)
        estimate_layout.addWidget(self.section_estimate_edit)
        estimate_layout.addWidget(self.fill_factor_edit)
        estimate_layout.addWidget(self.estimate_section_button)
        shunt_form.addRow("Pela seção:", estimate_row)
        self.editor_layout.addWidget(shunt)

        optional = QGroupBox("Opcional", self.editor)
        optional_form = QFormLayout(optional)
        self._add_numeric_row(optional_form, "Ampacidade normal (A):", "normal_amps")
        self._add_numeric_row(optional_form, "Ampacidade emergência (A):", "emergency_amps")
        self._add_numeric_row(optional_form, "Seção nominal (mm²):", "nominal_section")
        self.source_edit = QLineEdit(optional)
        optional_form.addRow("Origem:", self.source_edit)
        self.editor_layout.addWidget(optional)

        self.cn_group = QGroupBox("Neutro concêntrico", self.editor)
        cn_form = QFormLayout(self.cn_group)
        for caption, attribute in (
            ("Número de fios (k)*:", "strand_count"),
            ("Diâmetro do fio*:", "strand_diameter"),
            ("Resistência do fio*:", "strand_resistance"),
            ("GMR do fio:", "strand_gmr"),
            ("Permissividade relativa:", "relative_permittivity"),
            ("Espessura da isolação*:", "insulation_layer"),
            ("Diâmetro sobre isolação*:", "insulation_diameter"),
            ("Diâmetro externo*:", "cable_diameter"),
        ):
            self._add_numeric_row(cn_form, caption, attribute)
        self.editor_layout.addWidget(self.cn_group)
        self.editor_layout.addStretch(1)

    def _add_numeric_row(self, form: QFormLayout, caption: str, attribute: str) -> None:
        edit = QLineEdit(form.parentWidget())
        edit.setObjectName(f"opendss_cable_{attribute}")
        self.numeric_edits[attribute] = edit
        form.addRow(caption, edit)

    @staticmethod
    def _units_combo(parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItems(OPEN_DSS_UNITS)
        return combo

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.new_button = QPushButton("Novo", self)
        self.duplicate_button = QPushButton("Duplicar", self)
        self.delete_button = QPushButton("Excluir…", self)
        self.import_button = QPushButton("Importar…", self)
        self.export_button = QPushButton("Exportar…", self)
        self.restore_button = QPushButton("Restaurar padrões…", self)
        self.help_button = QPushButton("Ajuda", self)
        for button in (
            self.new_button,
            self.duplicate_button,
            self.delete_button,
            self.import_button,
            self.export_button,
            self.restore_button,
            self.help_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        self.save_button = QPushButton("Salvar", self)
        self.close_button = QPushButton("Fechar", self)
        row.addWidget(self.save_button)
        row.addWidget(self.close_button)
        return row

    def _connect_signals(self) -> None:
        self.search_edit.textChanged.connect(self.proxy_model.setFilterFixedString)
        self.proxy_model.modelReset.connect(self._restore_selection)
        self.table.selectionModel().currentRowChanged.connect(self._on_current_row_changed)
        self.session.cablesChanged.connect(self.refresh)
        self.session.geometriesChanged.connect(self._refresh_usage_only)
        self.session.cablesDirtyChanged.connect(self._sync_dirty_state)
        self.name_edit.editingFinished.connect(self._edit_name)
        self.family_edit.editingFinished.connect(lambda: self._edit_text("family", self.family_edit))
        self.description_edit.editingFinished.connect(lambda: self._edit_text("description", self.description_edit))
        self.source_edit.editingFinished.connect(lambda: self._edit_text("source", self.source_edit))
        self.type_combo.currentIndexChanged.connect(self._edit_type)
        for attribute, edit in self.numeric_edits.items():
            edit.editingFinished.connect(
                lambda attribute=attribute, edit=edit: self._edit_number(attribute, edit)
            )
        self.resistance_units_combo.currentTextChanged.connect(
            lambda value: self._edit_unit("resistance_units", value)
        )
        self.gmr_units_combo.currentTextChanged.connect(
            lambda value: self._edit_unit("gmr_units", value)
        )
        self.radius_units_combo.currentTextChanged.connect(
            lambda value: self._edit_unit("radius_units", value)
        )
        self.estimate_gmr_button.clicked.connect(self._estimate_from_gmr)
        self.estimate_section_button.clicked.connect(self._estimate_from_section)
        self.new_button.clicked.connect(self._new_cable)
        self.duplicate_button.clicked.connect(self._duplicate_cable)
        self.delete_button.clicked.connect(self._delete_cable)
        self.import_button.clicked.connect(self._import_cables)
        self.export_button.clicked.connect(self._export_cables)
        self.restore_button.clicked.connect(self._restore_defaults)
        self.help_button.clicked.connect(lambda: self.help_dialog.show_section("cables"))
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.close)

    def selected_cable(self) -> CableDefinition | None:
        return self.session.catalog.cable(self._selected_id)

    def refresh(self) -> None:
        self.table_model.refresh()
        self.summary_label.setText(f"{len(self.session.catalog.cables):n} cabo(s)")
        self._restore_selection()
        self._sync_reference_warning()

    def _refresh_usage_only(self) -> None:
        self.table_model.refresh()
        self._restore_selection()
        self._sync_reference_warning()

    def _restore_selection(self) -> None:
        if self.proxy_model.rowCount() == 0:
            self._selected_id = None
            self._load_editor()
            return
        source_row = 0
        if self._selected_id is not None:
            for row, cable in enumerate(self.session.catalog.cables):
                if cable.cable_id == self._selected_id:
                    source_row = row
                    break
        proxy_index = self.proxy_model.mapFromSource(self.table_model.index(source_row, 0))
        if not proxy_index.isValid():
            proxy_index = self.proxy_model.index(0, 0)
        self.table.setCurrentIndex(proxy_index)
        source = self.proxy_model.mapToSource(proxy_index)
        if source.isValid():
            self._selected_id = self.table_model.cable(source.row()).cable_id
        self._load_editor()

    def _on_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        source = self.proxy_model.mapToSource(current)
        if source.isValid():
            self._selected_id = self.table_model.cable(source.row()).cable_id
        else:
            self._selected_id = None
        self._load_editor()

    def _load_editor(self) -> None:
        cable = self.selected_cable()
        self._loading = True
        try:
            has_cable = cable is not None
            self.empty_label.setVisible(not has_cable)
            for widget in self.editor.findChildren(QWidget):
                if widget is not self.empty_label:
                    widget.setEnabled(has_cable)
            self.duplicate_button.setEnabled(has_cable)
            self.delete_button.setEnabled(has_cable)
            if cable is None:
                self.issue_label.clear()
                return
            self.name_edit.setText(cable.name)
            self.type_combo.setCurrentIndex(1 if cable.is_concentric else 0)
            self.family_edit.setText(cable.family)
            self.description_edit.setText(cable.description)
            self.source_edit.setText(cable.source)
            for attribute, edit in self.numeric_edits.items():
                edit.setText(_format_number(getattr(cable, attribute)))
            self.section_estimate_edit.setText(_format_number(cable.nominal_section))
            self._set_combo_value(self.resistance_units_combo, cable.resistance_units)
            self._set_combo_value(self.gmr_units_combo, cable.gmr_units)
            self._set_combo_value(self.radius_units_combo, cable.radius_units)
            self.cn_group.setVisible(cable.is_concentric)
            issues = cable_issues(cable)
            if issues:
                self.issue_label.setText("Incompleto: falta " + ", ".join(issues) + ".")
            elif cable.radius_estimated:
                self.issue_label.setText("≈ Dimensão estimada; confira antes do uso elétrico.")
            else:
                self.issue_label.clear()
            self._sync_paired_fields(cable)
        finally:
            self._loading = False

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index < 0 and value:
            combo.addItem(value)
            index = combo.count() - 1
        combo.setCurrentIndex(max(index, 0))

    def _sync_paired_fields(self, cable: CableDefinition) -> None:
        self.numeric_edits["rac"].setEnabled(not (cable.rdc is not None and cable.rac is None))
        self.numeric_edits["rdc"].setEnabled(not (cable.rac is not None and cable.rdc is None))
        self.numeric_edits["diameter"].setEnabled(not (cable.radius is not None and cable.diameter is None))
        self.numeric_edits["radius"].setEnabled(not (cable.diameter is not None and cable.radius is None))

    def _edit_name(self) -> None:
        if self._loading or (cable := self.selected_cable()) is None:
            return
        name = normalize_library_name(self.name_edit.text())
        if not name:
            self._show_error("O nome não pode ficar vazio.")
            self._load_editor()
            return
        if any(other.cable_id != cable.cable_id and other.name.casefold() == name.casefold() for other in self.session.catalog.cables):
            self._show_error(f'Já existe um cabo chamado "{name}".')
            self._load_editor()
            return
        if name != cable.name:
            cable.name = name
            self.session.mark_cables_changed()
        if self.name_edit.text() != name:
            self.name_edit.setText(name)

    def _edit_text(self, attribute: str, edit: QLineEdit) -> None:
        if self._loading or (cable := self.selected_cable()) is None:
            return
        value = edit.text().strip()
        if getattr(cable, attribute) != value:
            setattr(cable, attribute, value)
            self.session.mark_cables_changed()

    def _edit_type(self) -> None:
        if self._loading or (cable := self.selected_cable()) is None:
            return
        cable_type = str(self.type_combo.currentData())
        if cable_type == cable.cable_type:
            return
        cable.cable_type = "cn" if cable_type == "cn" else "wire"
        if cable.cable_type == "wire":
            for attribute in (
                "strand_count",
                "strand_diameter",
                "strand_resistance",
                "strand_gmr",
                "relative_permittivity",
                "insulation_layer",
                "insulation_diameter",
                "cable_diameter",
            ):
                setattr(cable, attribute, None)
        self.session.mark_cables_changed()

    def _edit_number(self, attribute: str, edit: QLineEdit) -> None:
        if self._loading or (cable := self.selected_cable()) is None:
            return
        try:
            value = _parse_number(edit.text(), integer=self._NUMERIC_FIELDS[attribute])
        except ValueError as exc:
            self._show_error(str(exc))
            self._load_editor()
            return
        if getattr(cable, attribute) != value:
            setattr(cable, attribute, value)
            if attribute in {"radius", "diameter"}:
                cable.radius_estimated = False
            self.session.mark_cables_changed()

    def _edit_unit(self, attribute: str, value: str) -> None:
        if self._loading or (cable := self.selected_cable()) is None:
            return
        if getattr(cable, attribute) != value:
            setattr(cable, attribute, value)
            self.session.mark_cables_changed()

    def _estimate_from_gmr(self) -> None:
        cable = self.selected_cable()
        if cable is None:
            return
        try:
            value = estimate_radius_from_gmr(cable)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.session.mark_cables_changed()
        self.reference_label.setText(f"Raio estimado: {_format_number(value)} {cable.radius_units}.")

    def _estimate_from_section(self) -> None:
        cable = self.selected_cable()
        if cable is None:
            return
        try:
            section = _parse_number(self.section_estimate_edit.text())
            factor = _parse_number(self.fill_factor_edit.text())
            if section is None or factor is None:
                raise ValueError("Informe a seção nominal e o fator de preenchimento.")
            value = estimate_diameter_from_section(cable, float(section), float(factor))
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.session.mark_cables_changed()
        self.reference_label.setText(f"Diâmetro estimado: {_format_number(value)} {cable.radius_units}.")

    def _new_cable(self) -> None:
        names = [item.name for item in self.session.catalog.cables]
        name = unique_name("Cabo novo", names)
        cable = CableDefinition(
            cable_id=unique_id(name, (item.cable_id for item in self.session.catalog.cables)),
            name=name,
            family="Personalizado",
            rac=0.1,
            resistance_units="km",
            gmr=0.5,
            gmr_units="cm",
            radius=0.7,
            radius_units="cm",
        )
        self.session.catalog.cables.append(cable)
        self._selected_id = cable.cable_id
        self.session.mark_cables_changed()

    def _duplicate_cable(self) -> None:
        cable = self.selected_cable()
        if cable is None:
            return
        duplicate = copy.deepcopy(cable)
        duplicate.name = unique_name(cable.name, (item.name for item in self.session.catalog.cables))
        duplicate.cable_id = unique_id(duplicate.name, (item.cable_id for item in self.session.catalog.cables))
        duplicate.source = ""
        self.session.catalog.cables.append(duplicate)
        self._selected_id = duplicate.cable_id
        self.session.mark_cables_changed()

    def _delete_cable(self) -> None:
        cable = self.selected_cable()
        if cable is None:
            return
        uses = self.session.catalog.geometries_using_cable(cable.cable_id)
        if uses:
            QMessageBox.warning(
                self,
                "Cabo em uso",
                f'"{cable.name}" é usado por {len(uses)} montagem(ns):\n\n• '
                + "\n• ".join(item.name for item in uses)
                + "\n\nTroque o cabo nessas montagens antes de excluí-lo.",
            )
            return
        mapping_session = self.session.mapping_session
        mapped_ids = (
            ()
            if mapping_session is None
            else mapping_session.mapped_cable_source_ids(cable.name)
        )
        if mapped_ids:
            QMessageBox.warning(
                self,
                "Cabo mapeado",
                f'"{cable.name}" é usado pelos CABO_ID: {", ".join(mapped_ids)}.\n\n'
                "Remova esses vínculos em Configurações > OpenDSS antes de excluir.",
            )
            return
        if QMessageBox.question(self, "Excluir cabo", f'Excluir o cabo "{cable.name}"?') != QMessageBox.StandardButton.Yes:
            return
        self.session.catalog.cables.remove(cable)
        self._selected_id = None
        self.session.mark_cables_changed()

    def _import_cables(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar biblioteca de cabos", "", "JSON (*.json)")
        if not path:
            return
        try:
            incoming = read_cables_file(path)
        except (OSError, LibraryFormatError) as exc:
            self._show_error(str(exc), title="Arquivo inválido")
            return
        if QMessageBox.question(
            self,
            "Substituir biblioteca de cabos",
            f"Substituir os {len(self.session.catalog.cables):n} cabos atuais pelos {len(incoming):n} do arquivo?\n\nA alteração ficará pendente até Salvar.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.replace_cables_from_file(path)
        except MappedLibraryItemError as exc:
            self._show_error(str(exc), title="Cabos mapeados")
            return
        self._selected_id = self.session.catalog.cables[0].cable_id if self.session.catalog.cables else None
        self._sync_reference_warning(show_dialog=True)

    def _export_cables(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Exportar biblioteca de cabos", "cabos.json", "JSON (*.json)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            self.session.export_cables(target)
        except (OSError, LibraryFormatError) as exc:
            self._show_error(str(exc), title="Falha ao exportar")
            return
        self.reference_label.setText(f"Biblioteca exportada para {target.name}.")

    def _restore_defaults(self) -> None:
        if QMessageBox.question(
            self,
            "Restaurar cabos padrão",
            "Restaurar os 58 cabos de fábrica?\n\nOs cabos criados ou editados serão substituídos no rascunho.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.restore_default_cables()
        except MappedLibraryItemError as exc:
            self._show_error(str(exc), title="Cabos mapeados")
            return
        self._selected_id = self.session.catalog.cables[0].cable_id

    def _save(self) -> bool:
        try:
            self.session.save_cable_drafts()
        except (OSError, LibraryFormatError, MappedLibraryItemError) as exc:
            self._show_error(str(exc), title="Falha ao salvar")
            return False
        self.reference_label.setText(f"{len(self.session.catalog.cables):n} cabo(s) salvos.")
        return True

    def _sync_dirty_state(self, dirty: bool) -> None:
        self.save_button.setEnabled(dirty)
        self.setWindowTitle("Biblioteca de Cabos OpenDSS" + (" *" if dirty else ""))

    def _sync_reference_warning(self, *, show_dialog: bool = False) -> None:
        missing = sorted(
            {
                cable_id
                for geometry in self.session.catalog.geometries
                for cable_id in geometry.cable_ids
                if cable_id and self.session.catalog.cable(cable_id) is None
            }
        )
        if not missing:
            if self.reference_label.text().startswith("Atenção:"):
                self.reference_label.clear()
            return
        message = (
            f"Atenção: {len(missing):n} referência(s) de cabo das montagens não existem nesta biblioteca. "
            "Importe o arquivo de geometrias correspondente ou corrija as montagens."
        )
        self.reference_label.setText(message)
        if show_dialog:
            QMessageBox.warning(self, "Referências ausentes", message)

    def confirm_pending_changes(self) -> bool:
        if not self.session.cables_dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Alterações não salvas",
            "Há alterações não salvas na biblioteca de cabos. Salvar agora?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        self.session.discard_cable_drafts()
        return True

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.confirm_pending_changes():
            event.accept()
        else:
            event.ignore()

    def _show_error(self, message: str, *, title: str = "Valor inválido") -> None:
        QMessageBox.warning(self, title, message)
