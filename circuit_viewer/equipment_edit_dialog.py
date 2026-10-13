"""Janela de edição dos dados de um equipamento, no estilo do Interplan.

O painel de propriedades é só de visualização; editar é abrir esta janela
pequena, alterar e salvar. Ela não conhece o projeto nem Qt de outras janelas:
recebe as **colunas dos registros** do projeto (``RegistryRecord.values``) e
devolve só o que mudou, no formato de :func:`project_state.edit_equipments` —
``{equipment_id: {coluna: valor}}``. Quem aplica é a janela principal.

Os formulários de cada tipo de equipamento (:func:`segment_form`,
:func:`load_form`, :func:`capacitor_form`, :func:`generator_form`) são funções
puras que montam as abas a partir desses registros; a janela só desenha e
valida o que elas descrevem.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable, Mapping, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .model import LoadPatternRecord
from .opendss_export import parse_number


TEXT = "text"
NUMBER = "number"
INTEGER = "integer"
CHOICE = "choice"

# Destaque de campo inválido: só a borda, para valer nos dois temas.
INVALID_STYLE = "border: 1px solid #d9534f;"

PATTERN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pd", "PD"),
    ("pe", "PE"),
    ("pf", "PF"),
    ("qd", "QD"),
    ("qe", "QE"),
    ("qf", "QF"),
    ("zcte", "ZCTE"),
    ("icte", "ICTE"),
    ("scte", "SCTE"),
)
# O modelo ZIP é opcional no cadastro; as potências do patamar, não.
OPTIONAL_PATTERN_COLUMNS = frozenset({"zcte", "icte", "scte"})


@dataclass(frozen=True, slots=True)
class EditField:
    """Um campo da janela, ligado a uma coluna de um registro do projeto.

    ``item`` aponta uma posição dentro de uma coluna-tupla, como os P1..P4 do
    gerador. ``cast`` converte o texto digitado no valor da coluna (o
    comprimento do trecho é ``float``; o resto é texto, como veio do banco).
    ``enabled_when`` liga o campo a outro: ``(chave do outro campo, valor)``.
    """

    equipment_id: str
    column: str
    label: str
    kind: str = TEXT
    item: int | None = None
    choices: tuple[tuple[str, str], ...] = ()
    editable_choice: bool = False
    allow_empty: bool = True
    cast: Callable[[str], object] | None = None
    read_only: bool = False
    enabled_when: tuple[str, str] | None = None
    tooltip: str = ""

    @property
    def key(self) -> str:
        return f"{self.equipment_id}:{self.column}:{'' if self.item is None else self.item}"


@dataclass(frozen=True, slots=True)
class PatternTable:
    """Os quatro patamares de uma carga, editáveis numa tabela 4×9."""

    equipment_id: str
    records: tuple[LoadPatternRecord, ...]


@dataclass(frozen=True, slots=True)
class EditTab:
    title: str
    fields: tuple[EditField, ...] = ()
    patterns: PatternTable | None = None


@dataclass(frozen=True, slots=True)
class EditForm:
    """O que a janela desenha: título, abas e os valores atuais dos registros."""

    title: str
    tabs: tuple[EditTab, ...]
    values: Mapping[str, Mapping[str, object]]


def _display(value: object) -> str:
    """Texto do valor de uma coluna, como o usuário o espera ver."""

    if value is None:
        return ""
    if isinstance(value, float):
        return "" if math.isnan(value) else f"{value:.6g}"
    return str(value)


def _float_or_nan(text: str) -> float:
    number = parse_number(text)
    return math.nan if number is None else number


class EquipmentEditDialog(QDialog):
    """Janela modal com abas; :meth:`changes` devolve só o que mudou."""

    def __init__(self, form: EditForm, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle(form.title)
        self.setModal(True)
        self._form = form
        self._fields: dict[str, EditField] = {}
        self._widgets: dict[str, QLineEdit | QComboBox] = {}
        self._initial: dict[str, str] = {}
        self._pattern_tables: list[tuple[PatternTable, QTableWidget]] = []

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("equipment_edit_tabs")
        for tab in form.tabs:
            self.tabs.addTab(self._build_tab(tab), tab.title)
        if self.tabs.count() == 1:
            self.tabs.tabBar().hide()
        layout.addWidget(self.tabs)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("equipment_edit_status")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Salvar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._sync_dependencies()
        self._validate()
        self.resize(max(380, self.sizeHint().width()), self.sizeHint().height())

    # -- construção -------------------------------------------------------

    def _build_tab(self, tab: EditTab) -> QWidget:
        page = QWidget(self.tabs)
        layout = QVBoxLayout(page)
        if tab.fields:
            form = QFormLayout()
            for field in tab.fields:
                form.addRow(f"{field.label}:", self._build_field(field, page))
            layout.addLayout(form)
        if tab.patterns is not None:
            layout.addWidget(self._build_patterns(tab.patterns, page))
        layout.addStretch(1)
        return page

    def _initial_text(self, field: EditField) -> str:
        value = self._form.values.get(field.equipment_id, {}).get(field.column)
        if field.item is not None:
            value = value[field.item] if isinstance(value, (tuple, list)) else ""
        text = _display(value)
        if field.kind == CHOICE and not field.editable_choice:
            known = [choice for choice, _ in field.choices]
            if text not in known and known:
                # Valor fora da lista (vazio no cadastro, por exemplo): a
                # combo mostra a primeira opção, e só mudá-la é alteração.
                text = known[0]
        return text

    def _build_field(self, field: EditField, parent: QWidget) -> QWidget:
        text = self._initial_text(field)
        self._fields[field.key] = field
        self._initial[field.key] = text
        if field.kind == CHOICE:
            widget = QComboBox(parent)
            widget.setEditable(field.editable_choice)
            for value, caption in field.choices:
                widget.addItem(caption, value)
            position = widget.findData(text)
            if position >= 0:
                widget.setCurrentIndex(position)
            elif field.editable_choice:
                widget.setEditText(text)
            if field.editable_choice and widget.completer() is not None:
                widget.completer().setFilterMode(Qt.MatchFlag.MatchContains)
                widget.completer().setCompletionMode(
                    widget.completer().CompletionMode.PopupCompletion
                )
            widget.currentIndexChanged.connect(self._on_change)
            widget.editTextChanged.connect(self._on_change)
        else:
            widget = QLineEdit(text, parent)
            widget.textChanged.connect(self._on_change)
            widget.setReadOnly(field.read_only)
        if field.read_only:
            widget.setEnabled(False)
        widget.setObjectName(f"edit_{field.column}{'' if field.item is None else field.item}")
        if field.tooltip:
            widget.setToolTip(field.tooltip)
        self._widgets[field.key] = widget
        return widget

    def _build_patterns(self, patterns: PatternTable, parent: QWidget) -> QTableWidget:
        table = QTableWidget(len(patterns.records), len(PATTERN_COLUMNS), parent)
        table.setObjectName("edit_patterns_table")
        table.setHorizontalHeaderLabels([caption for _, caption in PATTERN_COLUMNS])
        table.setVerticalHeaderLabels([f"NPAT {record.npat}" for record in patterns.records])
        for row, record in enumerate(patterns.records):
            for column, (name, _) in enumerate(PATTERN_COLUMNS):
                table.setItem(row, column, QTableWidgetItem(getattr(record, name)))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.setMinimumWidth(560)
        table.itemChanged.connect(self._on_change)
        self._pattern_tables.append((patterns, table))
        return table

    # -- estado -----------------------------------------------------------

    def _text(self, key: str) -> str:
        widget = self._widgets[key]
        field = self._fields[key]
        if isinstance(widget, QComboBox):
            if field.editable_choice:
                position = widget.findText(widget.currentText())
                data = widget.itemData(position) if position >= 0 else None
                return (data if data is not None else widget.currentText()).strip()
            data = widget.currentData()
            return "" if data is None else str(data)
        return widget.text().strip()

    def _on_change(self, *_args) -> None:  # noqa: ANN002
        self._sync_dependencies()
        self._validate()

    def _sync_dependencies(self) -> None:
        for key, field in self._fields.items():
            if field.enabled_when is None:
                continue
            other, expected = field.enabled_when
            self._widgets[key].setEnabled(
                other in self._widgets and self._text(other) == expected
            )

    def _field_error(self, key: str) -> str | None:
        field = self._fields[key]
        if field.read_only or not self._widgets[key].isEnabled():
            return None
        text = self._text(key)
        if not text:
            return None if field.allow_empty else f"{field.label} é obrigatório."
        if field.kind in (NUMBER, INTEGER):
            number = parse_number(text)
            if number is None:
                return f"{field.label} deve ser um número (vírgula ou ponto decimal)."
            if field.kind == INTEGER and number != int(number):
                return f"{field.label} deve ser um número inteiro."
        return None

    def _pattern_errors(self) -> list[str]:
        errors: list[str] = []
        for _, table in self._pattern_tables:
            for row in range(table.rowCount()):
                for column, (name, caption) in enumerate(PATTERN_COLUMNS):
                    item = table.item(row, column)
                    text = "" if item is None else item.text().strip()
                    valid = (
                        parse_number(text) is not None
                        or (not text and name in OPTIONAL_PATTERN_COLUMNS)
                    )
                    if item is not None:
                        item.setBackground(
                            Qt.GlobalColor.transparent if valid else Qt.GlobalColor.darkRed
                        )
                    if not valid:
                        errors.append(f"{caption} do NPAT {row} deve ser um número.")
        return errors

    def _validate(self) -> list[str]:
        errors: list[str] = []
        for key, widget in self._widgets.items():
            error = self._field_error(key)
            widget.setStyleSheet(INVALID_STYLE if error else "")
            if error:
                errors.append(error)
        errors.extend(self._pattern_errors())
        self.status_label.setText(errors[0] if errors else "")
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setEnabled(not errors)
        return errors

    @property
    def is_valid(self) -> bool:
        return not self._validate()

    def changes(self) -> dict[str, dict[str, object]]:
        """``{equipment_id: {coluna: valor}}`` só com o que mudou."""

        result: dict[str, dict[str, object]] = {}
        for key, field in self._fields.items():
            if field.read_only:
                continue
            text = self._text(key)
            if text == self._initial[key]:
                continue
            value: object = field.cast(text) if field.cast is not None else text
            columns = result.setdefault(field.equipment_id, {})
            if field.item is not None:
                base = columns.get(field.column)
                if base is None:
                    base = self._form.values[field.equipment_id][field.column]
                items = list(base)
                items[field.item] = value
                value = tuple(items)
            columns[field.column] = value
        for patterns, table in self._pattern_tables:
            records = []
            changed = False
            for row, record in enumerate(patterns.records):
                updates = {}
                for column, (name, _) in enumerate(PATTERN_COLUMNS):
                    item = table.item(row, column)
                    text = "" if item is None else item.text().strip()
                    if text != getattr(record, name):
                        updates[name] = text
                changed = changed or bool(updates)
                records.append(replace(record, **updates) if updates else record)
            if changed:
                result.setdefault(patterns.equipment_id, {})["records"] = tuple(records)
        return result


# -- formulários -----------------------------------------------------------


def _choices(values: Sequence[str], labels: Mapping[str, str] | None = None) -> tuple[tuple[str, str], ...]:
    labels = labels or {}
    return tuple((value, labels.get(value, value)) for value in values)


def segment_form(
    segment_id: str,
    segment: Mapping[str, object],
    *,
    regulator_id: str | None = None,
    regulator: Mapping[str, object] | None = None,
    phase_choices: Sequence[tuple[str, str]] = (),
    cable_choices: Sequence[tuple[str, str]] = (),
    arrangement_choices: Sequence[str] = (),
    segment_editable: bool = True,
    regulator_columns: frozenset[str] | None = None,
) -> EditForm:
    """Trecho e, se houver, o regulador dele, em abas.

    ``segment_editable`` desligado mostra o trecho só para leitura — o caso de
    uma rede importada sem projeto, em que só o regulador tem onde guardar a
    edição. ``regulator_columns`` restringe as colunas editáveis do regulador
    (as demais aparecem só para leitura); ``None`` libera todas.
    """

    cables = tuple(cable_choices)
    neutral = (("", "[não usado]"), *cables)
    tabs = [
        EditTab(
            "Trecho",
            (
                EditField(segment_id, "segment_ids", "TRECHO_ID", read_only=True),
                EditField(segment_id, "codes", "Código"),
                EditField(segment_id, "phases", "Fases (FASES2)", CHOICE, choices=tuple(phase_choices),
                          editable_choice=True, allow_empty=False),
                EditField(segment_id, "phase_cable_ids", "Cabo fase", CHOICE, choices=cables,
                          editable_choice=True,
                          tooltip="Digite parte do nome ou do CABO_ID para filtrar."),
                EditField(segment_id, "neutral_cable_ids", "Cabo neutro", CHOICE, choices=neutral,
                          editable_choice=True),
                EditField(segment_id, "arrangement_ids", "Arranjo", CHOICE,
                          choices=_choices(arrangement_choices), editable_choice=True),
                EditField(segment_id, "lengths", "Comprimento (m)", NUMBER, allow_empty=False,
                          cast=_float_or_nan),
                EditField(segment_id, "start_indices", "Barra 1", read_only=True),
                EditField(segment_id, "end_indices", "Barra 2", read_only=True),
            ),
        )
    ]
    if not segment_editable:
        tabs[0] = replace(
            tabs[0], fields=tuple(replace(field, read_only=True) for field in tabs[0].fields)
        )
    values: dict[str, Mapping[str, object]] = {segment_id: segment}
    if regulator_id is not None and regulator is not None:
        values[regulator_id] = regulator
        control_key = EditField(regulator_id, "fixed_values", "").key
        tabs.extend(
            (
                EditTab(
                    "Regulador",
                    (
                        EditField(regulator_id, "codes", "Código", read_only=True),
                        EditField(regulator_id, "snom_values", "SNOM (kVA)", NUMBER),
                        EditField(regulator_id, "vnom_values", "VNOM (kV)", NUMBER,
                                  tooltip="Vazio ou zero herda a VNOM do circuito."),
                        EditField(regulator_id, "inom_values", "INOM (A)", NUMBER,
                                  tooltip="Usada para relatar sobrecarga no fluxo de potência."),
                    ),
                ),
                EditTab(
                    "Tap",
                    (
                        EditField(regulator_id, "regulation_ranges", "Faixa (FAIXA)", NUMBER,
                                  tooltip="Fração (0,1) ou porcentagem (10) de regulação."),
                        EditField(regulator_id, "step_counts", "Passos (NPASSOS)", INTEGER),
                        EditField(regulator_id, "fixed_values", "Controle", CHOICE,
                                  choices=(("0", "Automático"), ("1", "Tap fixo"))),
                        EditField(regulator_id, "tap_values", "Tap fixo (TAP)", NUMBER,
                                  enabled_when=(control_key, "1"),
                                  tooltip="Fração (0,05 = +5 %) ou porcentagem."),
                    ),
                ),
                EditTab(
                    "Tensão",
                    tuple(
                        EditField(regulator_id, f"vreg_p{level}_values", f"VREG patamar {level} (pu)",
                                  NUMBER,
                                  tooltip="Vazio ou fora de 0,5–1,5 pu usa o padrão das configurações.")
                        for level in range(1, 5)
                    ),
                ),
            )
        )
        if regulator_columns is not None:
            tabs = [
                tab if index == 0 else replace(
                    tab,
                    fields=tuple(
                        field if field.column in regulator_columns or field.read_only
                        else replace(field, read_only=True)
                        for field in tab.fields
                    ),
                )
                for index, tab in enumerate(tabs)
            ]
    return EditForm("Trecho", tuple(tabs), values)


def load_form(
    load_id: str,
    load: Mapping[str, object],
    *,
    patterns_id: str | None = None,
    patterns: Sequence[LoadPatternRecord] = (),
    phase_choices: Sequence[tuple[str, str]] = (),
) -> EditForm:
    tabs = [
        EditTab(
            "Dados",
            (
                EditField(load_id, "load_ids", "CARGA_ID", read_only=True),
                EditField(load_id, "codes", "Código"),
                EditField(load_id, "snom_values", "SNOM (kVA)", NUMBER),
                EditField(load_id, "sadm_values", "SADM (kVA)", NUMBER),
                EditField(load_id, "phases", "Fases (FASES2)", CHOICE, choices=tuple(phase_choices),
                          editable_choice=True, allow_empty=False),
                EditField(load_id, "connection_types", "Ligação (TIPO_LIG)", CHOICE,
                          choices=(("1", "1 — fase-neutro"), ("2", "2 — entre fases (ET/EP)")),
                          editable_choice=True),
            ),
        )
    ]
    values: dict[str, Mapping[str, object]] = {load_id: load}
    if patterns_id is not None and patterns:
        tabs.append(EditTab("Patamares", patterns=PatternTable(patterns_id, tuple(patterns))))
    return EditForm("Carga", tuple(tabs), values)


def capacitor_form(capacitor_id: str, capacitor: Mapping[str, object]) -> EditForm:
    return EditForm(
        "Capacitor",
        (
            EditTab(
                "Dados",
                (
                    EditField(capacitor_id, "capacitor_ids", "CAPAC_ID", read_only=True),
                    EditField(capacitor_id, "codes", "Código"),
                    EditField(capacitor_id, "nominal_voltages", "VNOM (kV)", NUMBER),
                    *(
                        EditField(capacitor_id, f"q{level}_values", f"Q{level} (kvar)", NUMBER)
                        for level in range(1, 5)
                    ),
                    EditField(capacitor_id, "phases", "Fases (FASES)",
                              tooltip="Letras das fases, como DEF ou DEFN."),
                    EditField(capacitor_id, "connection_types", "Ligação"),
                ),
            ),
        ),
        {capacitor_id: capacitor},
    )


def generator_form(generator_id: str, generator: Mapping[str, object]) -> EditForm:
    level_fields = tuple(
        EditField(generator_id, column, f"{prefix}{level + 1} ({unit})", NUMBER, item=level,
                  tooltip="Potência cadastrada do patamar; com algum valor, vale no lugar da curva.")
        for column, prefix, unit in (
            ("level_active_powers", "P", "kW"),
            ("level_reactive_powers", "Q", "kvar"),
        )
        for level in range(4)
    )
    return EditForm(
        "Gerador",
        (
            EditTab(
                "Dados",
                (
                    EditField(generator_id, "generator_ids", "GERADOR_ID", read_only=True),
                    EditField(generator_id, "generator_codes", "Código"),
                    EditField(generator_id, "nominal_powers", "SNOM (kVA)", NUMBER),
                    EditField(generator_id, "nominal_voltages", "VNOM (kV)", NUMBER),
                    EditField(generator_id, "connections", "Ligação"),
                    EditField(generator_id, "curve_ids", "CURVA_ID"),
                    EditField(generator_id, "generation_kwh", "Geração mensal (kWh)", NUMBER),
                ),
            ),
            EditTab("Potência por patamar", level_fields),
        ),
        {generator_id: generator},
    )


__all__ = [
    "EditField",
    "EditForm",
    "EditTab",
    "EquipmentEditDialog",
    "PatternTable",
    "capacitor_form",
    "generator_form",
    "load_form",
    "segment_form",
]
