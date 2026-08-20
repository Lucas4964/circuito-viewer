"""Diálogo em abas e persistência das configurações globais do OpenDSS."""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .opendss_line_mode import (
    DEFAULT_OPENDSS_LINE_PARAMETER_MODE,
    OpenDssLineParameterMode,
    parse_opendss_line_parameter_mode,
)
from .opendss_mapping_store import (
    LibraryNameMapping,
    OpenDssLibraryMappings,
)
from .opendss_settings import (
    DEFAULT_OPENDSS_LOAD_SETTINGS,
    DEFAULT_ZIPV_COEFFICIENTS,
    OpenDssLoadModel,
    OpenDssLoadSettings,
    VMAXPU_RANGE,
    VMINPU_RANGE,
    ZIPV_CUTOFF_RANGE,
    ZIPV_WEIGHT_RANGE,
    ZipvCoefficients,
    settings_from_mapping,
    zipv_sum_error,
)
from .table_columns import EXCEL_LIKE_TABLE_STYLE


SETTINGS_PREFIX = "opendss/load_"
LINE_PARAMETER_MODE_SETTINGS_KEY = "opendss/line_parameter_mode"


def load_opendss_settings(settings: QSettings) -> OpenDssLoadSettings:
    """Lê a configuração salva; ausência ou corrupção caem no padrão."""

    stored = {
        key: settings.value(f"{SETTINGS_PREFIX}{key}")
        for key in DEFAULT_OPENDSS_LOAD_SETTINGS.as_mapping()
    }
    return settings_from_mapping(
        {key: value for key, value in stored.items() if value is not None}
    )


def save_opendss_settings(
    settings: QSettings,
    value: OpenDssLoadSettings,
) -> None:
    for key, text in value.as_mapping().items():
        settings.setValue(f"{SETTINGS_PREFIX}{key}", text)
    settings.sync()


def load_opendss_line_parameter_mode(
    settings: QSettings,
) -> OpenDssLineParameterMode:
    """Lê o modo das linhas; ausência ou corrupção usam o modo original."""

    return parse_opendss_line_parameter_mode(
        settings.value(LINE_PARAMETER_MODE_SETTINGS_KEY)
    )


def save_opendss_line_parameter_mode(
    settings: QSettings,
    value: OpenDssLineParameterMode,
) -> None:
    """Persiste separadamente a fonte dos parâmetros elétricos das linhas."""

    mode = OpenDssLineParameterMode(value)
    settings.setValue(LINE_PARAMETER_MODE_SETTINGS_KEY, mode.value)
    settings.sync()


class MappingTableEditor(QWidget):
    """Editor manual de um mapa ``ID → nome canônico``."""

    changed = pyqtSignal()
    saveRequested = pyqtSignal(object)

    def __init__(
        self,
        id_header: str,
        names: Sequence[str],
        entries: Sequence[LibraryNameMapping],
        *,
        load_issue: str | None = None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.id_header = id_header
        self.available_names = tuple(sorted(set(names), key=str.casefold))
        self._available_name_set = set(self.available_names)
        self._load_issue = load_issue
        self._saved_load_issue = load_issue
        self._saved_entries = tuple(
            sorted(entries, key=lambda entry: entry.source_id.casefold())
        )
        self._save_confirmed = False
        self._save_error: str | None = None
        self._loading = False

        layout = QVBoxLayout(self)
        explanation = QLabel(
            f"Cadastre manualmente cada {id_header} e escolha o nome correspondente "
            "na biblioteca OpenDSS salva.",
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.table = QTableWidget(0, 2, self)
        self.table.setObjectName(f"opendss_{id_header.lower()}_map_table")
        self.table.setHorizontalHeaderLabels((id_header, "Nome na biblioteca"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setMinimumHeight(260)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.add_button = QPushButton("Adicionar", self)
        self.remove_button = QPushButton("Remover selecionado", self)
        self.save_button = QPushButton("Salvar", self)
        self.save_button.setObjectName(
            f"opendss_{id_header.lower()}_map_save_button"
        )
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.save_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.issue_label = QLabel(self)
        self.issue_label.setObjectName(f"opendss_{id_header.lower()}_map_issue")
        self.issue_label.setWordWrap(True)
        layout.addWidget(self.issue_label)

        self.add_button.clicked.connect(self.add_empty_row)
        self.remove_button.clicked.connect(self.remove_selected_rows)
        self.save_button.clicked.connect(self._request_save)
        self.table.itemChanged.connect(self._on_item_changed)
        self._loading = True
        try:
            for entry in entries:
                self._append_row(entry.source_id, entry.library_name)
        finally:
            self._loading = False
        self._sync_validation()

    def _combo(self, current_name: str = "") -> QComboBox:
        combo = QComboBox(self.table)
        combo.addItem("— selecione —", "")
        for name in self.available_names:
            combo.addItem(name, name)
        if current_name and current_name not in self._available_name_set:
            combo.addItem(f"{current_name} (ausente)", current_name)
        combo.setCurrentIndex(max(combo.findData(current_name), 0))
        combo.currentIndexChanged.connect(self._on_combo_changed)
        return combo

    def _append_row(self, source_id: str = "", library_name: str = "") -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(source_id))
        self.table.setCellWidget(row, 1, self._combo(library_name))
        return row

    def add_empty_row(self) -> None:
        self._load_issue = None
        self._loading = True
        try:
            row = self._append_row()
        finally:
            self._loading = False
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))
        self._notify_changed()

    def remove_selected_rows(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        if not rows:
            return
        self._load_issue = None
        self._loading = True
        try:
            for row in rows:
                self.table.removeRow(row)
        finally:
            self._loading = False
        self._notify_changed()

    def clear_map(self) -> None:
        self._load_issue = None
        self._loading = True
        try:
            self.table.setRowCount(0)
        finally:
            self._loading = False
        self._notify_changed()

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        if not self._loading:
            self._load_issue = None
            self._notify_changed()

    def _on_combo_changed(self, _index: int) -> None:
        if not self._loading:
            self._load_issue = None
            self._notify_changed()

    def _notify_changed(self) -> None:
        self._save_confirmed = False
        self._save_error = None
        self._sync_validation()
        self.changed.emit()

    def _request_save(self) -> None:
        if self.validation_error() is not None or not self.has_unsaved_changes():
            return
        self.saveRequested.emit(self.entries())

    def validation_error(self) -> str | None:
        if self._load_issue:
            return "Arquivo inválido: " + self._load_issue
        seen: set[str] = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            source_id = "" if item is None else item.text().strip()
            combo = self.table.cellWidget(row, 1)
            name = "" if not isinstance(combo, QComboBox) else str(combo.currentData() or "")
            if not source_id:
                return f"Linha {row + 1}: informe {self.id_header}."
            if source_id in seen:
                return f"Linha {row + 1}: {self.id_header} '{source_id}' está duplicado."
            seen.add(source_id)
            if not name:
                return f"Linha {row + 1}: selecione um nome da biblioteca."
            if name not in self._available_name_set:
                return f"Linha {row + 1}: o nome '{name}' não existe na biblioteca salva."
        return None

    def _sync_validation(self) -> None:
        error = self.validation_error()
        dirty = self.has_unsaved_changes() if error is None else False
        if error is not None:
            message = "Atenção: " + error
        elif self._save_error is not None:
            message = "Falha ao salvar: " + self._save_error
        elif dirty:
            message = "Alterações não salvas."
        elif self._save_confirmed:
            message = "Mapa salvo."
        else:
            message = ""
        self.issue_label.setText(message)
        self.remove_button.setEnabled(self.table.rowCount() > 0)
        self.save_button.setEnabled(error is None and dirty)

    def has_unsaved_changes(self) -> bool:
        if self._load_issue is not None:
            return False
        try:
            current = self.entries()
        except ValueError:
            return True
        return self._saved_load_issue is not None or current != self._saved_entries

    def mark_saved(
        self,
        entries: Sequence[LibraryNameMapping] | None = None,
    ) -> None:
        saved = self.entries() if entries is None else tuple(entries)
        self._saved_entries = tuple(
            sorted(saved, key=lambda entry: entry.source_id.casefold())
        )
        self._saved_load_issue = None
        self._load_issue = None
        self._save_error = None
        self._save_confirmed = True
        self._sync_validation()

    def mark_save_failed(self, message: str) -> None:
        self._save_confirmed = False
        self._save_error = str(message).strip() or "erro desconhecido"
        self._sync_validation()

    def entries(self) -> tuple[LibraryNameMapping, ...]:
        error = self.validation_error()
        if error is not None:
            raise ValueError(error)
        result: list[LibraryNameMapping] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            combo = self.table.cellWidget(row, 1)
            result.append(
                LibraryNameMapping(
                    "" if item is None else item.text(),
                    "" if not isinstance(combo, QComboBox) else str(combo.currentData()),
                )
            )
        return tuple(sorted(result, key=lambda entry: entry.source_id.casefold()))


class OpenDssSettingsDialog(QDialog):
    """Configura limites de carga e mapas globais das bibliotecas OpenDSS."""

    def __init__(
        self,
        settings: OpenDssLoadSettings | None = None,
        parent=None,  # noqa: ANN001
        *,
        mappings: OpenDssLibraryMappings | None = None,
        cable_names: Sequence[str] = (),
        arrangement_names: Sequence[str] = (),
        cable_map_issue: str | None = None,
        arrangement_map_issue: str | None = None,
        line_parameter_mode: OpenDssLineParameterMode = (
            DEFAULT_OPENDSS_LINE_PARAMETER_MODE
        ),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configurações do OpenDSS")
        self.setModal(True)
        self.resize(760, 540)

        current = settings or DEFAULT_OPENDSS_LOAD_SETTINGS
        current_maps = mappings or OpenDssLibraryMappings()
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("opendss_settings_tabs")
        self.voltage_tab = self._build_voltage_tab(current)
        self.tabs.addTab(self.voltage_tab, "Cargas")
        self.line_parameters_tab = self._build_line_parameters_tab(
            parse_opendss_line_parameter_mode(line_parameter_mode)
        )
        self.tabs.addTab(self.line_parameters_tab, "Parâmetros das linhas")
        self.cable_map_editor = MappingTableEditor(
            "CABO_ID",
            cable_names,
            current_maps.cables,
            load_issue=cable_map_issue,
            parent=self.tabs,
        )
        self.tabs.addTab(self.cable_map_editor, "Mapa de Cabos")
        self.arrangement_map_editor = MappingTableEditor(
            "ARRANJO_ID",
            arrangement_names,
            current_maps.arrangements,
            load_issue=arrangement_map_issue,
            parent=self.tabs,
        )
        self.tabs.addTab(self.arrangement_map_editor, "Mapa de Arranjos")
        layout.addWidget(self.tabs, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        restore = self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        if restore is not None:
            restore.clicked.connect(self.restore_defaults)
        layout.addWidget(self.buttons)

        self.apply_limits_check.toggled.connect(self._sync_fields)
        self.vminpu_input.valueChanged.connect(self._sync_preview)
        self.vmaxpu_input.valueChanged.connect(self._sync_preview)
        self.constant_power_radio.toggled.connect(self._sync_load_model)
        for box in self.zipv_inputs.values():
            box.valueChanged.connect(self._sync_zipv)
        self.cable_map_editor.changed.connect(self._sync_accept_enabled)
        self.arrangement_map_editor.changed.connect(self._sync_accept_enabled)
        self._sync_fields(self.apply_limits_check.isChecked())
        self._sync_load_model()
        self._sync_accept_enabled()

    def _build_line_parameters_tab(
        self,
        current: OpenDssLineParameterMode,
    ) -> QWidget:
        tab = QWidget(self.tabs)
        tab.setObjectName("opendss_line_parameters_tab")
        layout = QVBoxLayout(tab)

        explanation = QLabel(
            "Escolha a fonte dos parâmetros elétricos e físicos usados nas "
            "linhas exportadas para o OpenDSS.",
            tab,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.line_parameter_mode_group = QButtonGroup(tab)
        self.line_parameter_mode_group.setObjectName(
            "opendss_line_parameter_mode_group"
        )
        self.line_parameter_mode_group.setExclusive(True)

        self.original_line_parameters_radio = QRadioButton(
            "Usar parâmetros elétricos importados",
            tab,
        )
        self.original_line_parameters_radio.setObjectName(
            "opendss_line_parameters_original"
        )
        self.original_line_parameters_radio.setToolTip(
            "Mantém a exportação atual baseada nos parâmetros de sequência "
            "dos cabos importados."
        )
        self.line_parameter_mode_group.addButton(
            self.original_line_parameters_radio
        )
        layout.addWidget(self.original_line_parameters_radio)

        self.library_line_parameters_radio = QRadioButton(
            "Usar bibliotecas de cabos e arranjos",
            tab,
        )
        self.library_line_parameters_radio.setObjectName(
            "opendss_line_parameters_library"
        )
        self.library_line_parameters_radio.setToolTip(
            "Usa as bibliotecas e os mapas salvos para gerar WireData, "
            "LineSpacing e LineGeometry."
        )
        self.line_parameter_mode_group.addButton(
            self.library_line_parameters_radio
        )
        layout.addWidget(self.library_line_parameters_radio)

        if current is OpenDssLineParameterMode.LIBRARY:
            self.library_line_parameters_radio.setChecked(True)
        else:
            self.original_line_parameters_radio.setChecked(True)
        layout.addStretch(1)
        return tab

    def _build_voltage_tab(self, current: OpenDssLoadSettings) -> QWidget:
        """Só o essencial na tela.

        A explicação de cada campo vive nos tooltips até existir uma janela de
        ajuda própria: prosa permanente aqui atrapalha mais do que ajuda.
        """

        tab = QWidget(self.tabs)
        tab.setObjectName("opendss_loads_tab")
        layout = QVBoxLayout(tab)

        self.load_model_group = QButtonGroup(tab)
        self.load_model_group.setObjectName("opendss_load_model_group")
        self.load_model_group.setExclusive(True)

        self.constant_power_radio = QRadioButton(
            "Potência constante (model=1)",
            tab,
        )
        self.constant_power_radio.setObjectName("opendss_load_model_constant_power")
        self.constant_power_radio.setToolTip(
            "A carga entrega a potência do patamar independentemente da tensão."
        )
        self.load_model_group.addButton(self.constant_power_radio)
        layout.addWidget(self.constant_power_radio)

        self.zipv_radio = QRadioButton("ZIPV (model=8)", tab)
        self.zipv_radio.setObjectName("opendss_load_model_zipv")
        self.zipv_radio.setToolTip(
            "Compõe a carga em parcelas de impedância, corrente e potência "
            "constantes. Vale só para as cargas de consumo."
        )
        self.load_model_group.addButton(self.zipv_radio)
        layout.addWidget(self.zipv_radio)

        self.zipv_fields = self._build_zipv_fields(tab, current.zipv)
        layout.addWidget(self.zipv_fields)

        self.zipv_sum_label = QLabel(tab)
        self.zipv_sum_label.setObjectName("opendss_zipv_sum")
        self.zipv_sum_label.setWordWrap(True)
        layout.addWidget(self.zipv_sum_label)

        self.apply_limits_check = QCheckBox("Aplicar limites de tensão às cargas", tab)
        self.apply_limits_check.setObjectName("opendss_apply_voltage_limits")
        self.apply_limits_check.setToolTip(
            "A faixa delimita onde o modelo escolhido vale, inclusive no ZIPV. "
            "Desmarcado, o OpenDSS usa 0,95 e 1,05."
        )
        self.apply_limits_check.setChecked(current.voltage_limits_enabled)
        layout.addWidget(self.apply_limits_check)
        self.fields = QWidget(tab)
        form = QFormLayout(self.fields)
        form.setContentsMargins(0, 0, 0, 0)
        self.vminpu_input = self._spin_box(
            "opendss_vminpu",
            VMINPU_RANGE,
            current.vminpu,
            "Abaixo desta tensão a carga deixa de seguir o modelo escolhido.",
        )
        form.addRow("vminpu:", self.vminpu_input)
        self.vmaxpu_input = self._spin_box(
            "opendss_vmaxpu",
            VMAXPU_RANGE,
            current.vmaxpu,
            "Acima desta tensão a carga deixa de seguir o modelo escolhido.",
        )
        form.addRow("vmaxpu:", self.vmaxpu_input)
        layout.addWidget(self.fields)
        layout.addWidget(QLabel("Efeito nos arquivos gerados:", tab))
        self.preview_label = QLabel(tab)
        self.preview_label.setObjectName("opendss_settings_preview")
        self.preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview_label.setStyleSheet(
            "font-family: monospace; padding: 6px;border: 1px solid palette(mid);"
        )
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)
        layout.addStretch(1)

        if current.load_model is OpenDssLoadModel.ZIPV:
            self.zipv_radio.setChecked(True)
        else:
            self.constant_power_radio.setChecked(True)
        return tab

    def _build_zipv_fields(
        self,
        parent: QWidget,
        current: ZipvCoefficients,
    ) -> QWidget:
        """Os pesos numa tabela 3x2: linhas Z/I/P, colunas P e Q.

        A forma tabular é a do próprio modelo ZIP e dispensa rótulo por campo.
        ``P`` aparece como coluna (potência ativa) e como linha (parcela de
        potência constante) — é a nomenclatura padrão, e o tooltip do cabeçalho
        desfaz a ambiguidade sem ocupar espaço.
        """

        container = QWidget(parent)
        container.setObjectName("opendss_zipv_fields")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        table = QWidget(container)
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(table)
        self.zipv_inputs: dict[str, QDoubleSpinBox] = {}

        for column, (header, tooltip) in enumerate(
            (("P", "Potência ativa"), ("Q", "Potência reativa")),
            start=1,
        ):
            label = QLabel(header, table)
            label.setToolTip(tooltip)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(label, 0, column)

        components = (
            ("Z", "z", "Parcela de impedância constante"),
            ("I", "i", "Parcela de corrente constante"),
            ("P", "p", "Parcela de potência constante"),
        )
        for row, (header, prefix, tooltip) in enumerate(components, start=1):
            label = QLabel(header, table)
            label.setToolTip(tooltip)
            grid.addWidget(label, row, 0)
            for column, suffix in enumerate(("p", "q"), start=1):
                name = f"{prefix}_{suffix}"
                box = self._spin_box(
                    f"opendss_zipv_{name}",
                    ZIPV_WEIGHT_RANGE,
                    getattr(current, name),
                    "Os três pesos de cada potência devem somar 1.",
                    decimals=4,
                )
                box.setMaximumWidth(110)
                self.zipv_inputs[name] = box
                grid.addWidget(box, row, column)

        cutoff = self._spin_box(
            "opendss_zipv_cutoff",
            ZIPV_CUTOFF_RANGE,
            current.cutoff,
            "Abaixo desta tensão a carga vai a zero. Zero desliga o corte.",
            decimals=4,
        )
        cutoff.setMaximumWidth(110)
        self.zipv_inputs["cutoff"] = cutoff
        # Fora da grade de propósito: dentro dela o rótulo alargaria a coluna
        # das letras e afastaria os campos das linhas Z/I/P.
        cutoff_form = QFormLayout()
        cutoff_form.setContentsMargins(0, 0, 0, 0)
        cutoff_form.addRow("Corte (pu):", cutoff)
        outer.addLayout(cutoff_form)
        # A última coluna absorve a sobra para a tabela não esticar os campos.
        grid.setColumnStretch(3, 1)
        return container

    def _spin_box(
        self,
        object_name: str,
        limits: tuple[float, float],
        value: float,
        tooltip: str,
        *,
        decimals: int = 3,
    ) -> QDoubleSpinBox:
        box = QDoubleSpinBox(self)
        box.setObjectName(object_name)
        box.setDecimals(decimals)
        box.setSingleStep(0.01)
        box.setRange(*limits)
        box.setValue(value)
        box.setToolTip(tooltip)
        return box

    def _sync_fields(self, enabled: bool) -> None:
        self.fields.setEnabled(bool(enabled))
        self._sync_preview()

    def _sync_load_model(self) -> None:
        """Os campos do ZIPV só existem para quem escolheu o ZIPV."""

        self.zipv_fields.setVisible(self.zipv_radio.isChecked())
        # A visibilidade do rótulo de somas é decidida em _sync_zipv, que
        # só o mostra quando há erro.
        self._sync_zipv()

    def _sync_zipv(self) -> None:
        # Com as somas corretas o rótulo seria só informação, e some. Com
        # erro ele é a justificativa de o OK estar bloqueado, e fica.
        error = self.zipv_validation_error()
        self.zipv_sum_label.setText(error or "")
        self.zipv_sum_label.setVisible(error is not None)
        self._sync_preview()
        self._sync_accept_enabled()

    def zipv_validation_error(self) -> str | None:
        """Erro que impede aceitar o diálogo, ou ``None``.

        Só vale no modo ZIPV: em potência constante os coeficientes ficam
        guardados sem efeito, e uma soma incoerente ali não impede nada.
        """

        if not self.zipv_radio.isChecked():
            return None
        return zipv_sum_error(self._zipv_coefficients())

    def _zipv_coefficients(self) -> ZipvCoefficients:
        try:
            return ZipvCoefficients(
                **{name: box.value() for name, box in self.zipv_inputs.items()}
            )
        except ValueError:
            return DEFAULT_ZIPV_COEFFICIENTS

    def _sync_preview(self) -> None:
        settings = self.settings()
        blocks: list[str] = []
        commands = settings.batch_edit_commands()
        blocks.append(
            "\n".join(commands)
            if commands
            else "! sem limites de tensão; o OpenDSS usará 0,95 e 1,05"
        )
        # O modelo não é comando de master: ele vive em cada linha New Load dos
        # arquivos de carga. Sem mostrá-lo aqui, o diálogo mentiria sobre o
        # efeito da configuração.
        blocks.append(
            f"New Load.<carga> ... {settings.load_model_directive()} kW=1 kvar=1 ..."
        )
        self.preview_label.setText("\n".join(blocks))

    def _sync_accept_enabled(self) -> None:
        valid = (
            self.cable_map_editor.validation_error() is None
            and self.arrangement_map_editor.validation_error() is None
            and self.zipv_validation_error() is None
        )
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(valid)

    def restore_defaults(self) -> None:
        current_tab = self.tabs.currentWidget()
        if current_tab is self.voltage_tab:
            self.apply_limits_check.setChecked(
                DEFAULT_OPENDSS_LOAD_SETTINGS.voltage_limits_enabled
            )
            self.vminpu_input.setValue(DEFAULT_OPENDSS_LOAD_SETTINGS.vminpu)
            self.vmaxpu_input.setValue(DEFAULT_OPENDSS_LOAD_SETTINGS.vmaxpu)
            self.constant_power_radio.setChecked(True)
            for name, value in zip(
                ("z_p", "i_p", "p_p", "z_q", "i_q", "p_q", "cutoff"),
                DEFAULT_ZIPV_COEFFICIENTS.as_tuple(),
                strict=True,
            ):
                self.zipv_inputs[name].setValue(value)
        elif current_tab is self.line_parameters_tab:
            self.original_line_parameters_radio.setChecked(True)
        elif current_tab is self.cable_map_editor:
            self.cable_map_editor.clear_map()
        elif current_tab is self.arrangement_map_editor:
            self.arrangement_map_editor.clear_map()
        self._sync_accept_enabled()

    def settings(self) -> OpenDssLoadSettings:
        try:
            return OpenDssLoadSettings(
                voltage_limits_enabled=self.apply_limits_check.isChecked(),
                vminpu=self.vminpu_input.value(),
                vmaxpu=self.vmaxpu_input.value(),
                load_model=(
                    OpenDssLoadModel.ZIPV
                    if self.zipv_radio.isChecked()
                    else OpenDssLoadModel.CONSTANT_POWER
                ),
                zipv=self._zipv_coefficients(),
            )
        except ValueError:
            return DEFAULT_OPENDSS_LOAD_SETTINGS

    def line_parameter_mode(self) -> OpenDssLineParameterMode:
        if self.library_line_parameters_radio.isChecked():
            return OpenDssLineParameterMode.LIBRARY
        return OpenDssLineParameterMode.ORIGINAL

    def mappings(self) -> OpenDssLibraryMappings:
        return OpenDssLibraryMappings(
            self.cable_map_editor.entries(),
            self.arrangement_map_editor.entries(),
        )
