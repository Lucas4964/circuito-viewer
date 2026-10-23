"""Diálogos da correção de demanda: os alvos de entrada e o relatório de saída."""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .demand_correction import (
    DEFAULT_ABSOLUTE_TOLERANCE,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_RELATIVE_TOLERANCE,
    DEFAULT_SCOPE,
    PATAMAR_COUNT,
    CorrectionSettings,
    DemandCorrectionResult,
    DemandScope,
    PatamarStatus,
    PatamarTarget,
    parse_target,
)
from .model import CircuitCatalogModel
from .table_columns import EXCEL_LIKE_TABLE_STYLE


SETTINGS_PREFIX = "opendss/demand_correction_"

# Os grupos que só existem quando as UCs estão no secundário dos trafos.
ENERGY_ONLY_SCOPES = frozenset({DemandScope.UC_BT, DemandScope.UC_MT, DemandScope.IP})

TargetTexts = tuple[tuple[str, str], ...]


def load_demand_correction_settings(
    settings: QSettings,
) -> tuple[CorrectionSettings, frozenset[DemandScope]]:
    """Tolerâncias e escopo salvos; o padrão onde nada valer."""

    def number(key: str, default: float) -> float:
        try:
            return float(settings.value(f"{SETTINGS_PREFIX}{key}", default))
        except (TypeError, ValueError):
            return default

    try:
        iterations = int(
            settings.value(f"{SETTINGS_PREFIX}max_iterations", DEFAULT_MAX_ITERATIONS)
        )
        value = CorrectionSettings(
            number("relative_tolerance", DEFAULT_RELATIVE_TOLERANCE),
            number("absolute_tolerance", DEFAULT_ABSOLUTE_TOLERANCE),
            iterations,
        )
    except (TypeError, ValueError):
        value = CorrectionSettings()
    raw_scope = settings.value(f"{SETTINGS_PREFIX}scope")
    scope = DEFAULT_SCOPE
    if isinstance(raw_scope, str) and raw_scope.strip():
        parsed = frozenset(
            item
            for item in DemandScope
            if item.value in {part.strip() for part in raw_scope.split(",")}
        )
        scope = parsed or DEFAULT_SCOPE
    return value, scope


def save_demand_correction_settings(
    settings: QSettings,
    value: CorrectionSettings,
    scope: frozenset[DemandScope],
) -> None:
    settings.setValue(f"{SETTINGS_PREFIX}relative_tolerance", value.relative_tolerance)
    settings.setValue(f"{SETTINGS_PREFIX}absolute_tolerance", value.absolute_tolerance)
    settings.setValue(f"{SETTINGS_PREFIX}max_iterations", value.max_iterations)
    settings.setValue(
        f"{SETTINGS_PREFIX}scope",
        ",".join(item.value for item in DemandScope if item in scope),
    )
    settings.sync()


def _format_number(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


class DemandCorrectionDialog(QDialog):
    """Circuito, P e Q medidos na saída por patamar, escopo e tolerâncias."""

    def __init__(
        self,
        circuits: CircuitCatalogModel,
        circuit_indices: Sequence[int],
        *,
        energy_mode: bool,
        settings: CorrectionSettings,
        scope: frozenset[DemandScope],
        patamar_labels: Callable[[int], Sequence[str]] | None = None,
        remembered_targets: Mapping[int, TargetTexts] | None = None,
        initial_circuit: int | None = None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Correção de demanda")
        self.setModal(True)
        self._circuits = circuits
        self._patamar_labels = patamar_labels
        self._remembered = dict(remembered_targets or {})
        self._energy_mode = energy_mode
        self._shown_circuit: int | None = None

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Informe P e Q medidos na saída do alimentador. Para cada patamar, "
            "o OpenDSS acha um fator para o P e outro para o Q das cargas "
            "escolhidas, de modo que a saída calculada bata com a medida. "
            "Deixe vazio o que não deve ser corrigido.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.circuit_combo = QComboBox(self)
        self.circuit_combo.setObjectName("demand_correction_circuit_combo")
        for index in circuit_indices:
            definition = circuits.definition(int(index))
            label = definition.code or definition.circuit_id
            self.circuit_combo.addItem(
                f"{label} (CIRC_ID {definition.circuit_id})", int(index)
            )
        if initial_circuit is not None:
            position = self.circuit_combo.findData(int(initial_circuit))
            if position >= 0:
                self.circuit_combo.setCurrentIndex(position)
        form.addRow("Circuito:", self.circuit_combo)
        layout.addLayout(form)

        self.table = QTableWidget(PATAMAR_COUNT, 2, self)
        self.table.setObjectName("demand_correction_targets")
        self.table.setHorizontalHeaderLabels(["P medido (kW)", "Q medido (kvar)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Linhas com folga acima e abaixo do texto: a altura do conteúdo
        # sozinha deixa os patamares colados uns nos outros.
        rows = self.table.verticalHeader()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows.setDefaultSectionSize(self.table.fontMetrics().height() + 10)
        for row in range(PATAMAR_COUNT):
            for column in range(2):
                self.table.setItem(row, column, QTableWidgetItem(""))
        # Os patamares são poucos e fixos: a tabela já nasce do tamanho de
        # todos eles, sem barra de rolagem.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._fit_table_height()
        layout.addWidget(self.table)

        scope_box = QGroupBox("Cargas corrigidas pelos fatores", self)
        scope_layout = QVBoxLayout(scope_box)
        self.scope_checks: dict[DemandScope, QCheckBox] = {}
        for item in DemandScope:
            check = QCheckBox(item.label, scope_box)
            check.setObjectName(f"demand_correction_scope_{item.value}")
            available = energy_mode or item not in ENERGY_ONLY_SCOPES
            check.setEnabled(available)
            check.setChecked(available and item in scope)
            if not available:
                check.setToolTip("Só existe no modo de cargas Energia.")
            self.scope_checks[item] = check
            scope_layout.addWidget(check)
        fixed = QLabel(
            "Geradores, GD e capacitores nunca são escalados; as cargas não "
            "marcadas também ficam fixas no fluxo.",
            scope_box,
        )
        fixed.setWordWrap(True)
        scope_layout.addWidget(fixed)
        layout.addWidget(scope_box)

        tolerance_form = QFormLayout()
        self.relative_tolerance = QDoubleSpinBox(self)
        self.relative_tolerance.setObjectName("demand_correction_relative_tolerance")
        self.relative_tolerance.setDecimals(4)
        self.relative_tolerance.setRange(0.0, 10.0)
        self.relative_tolerance.setSingleStep(0.01)
        self.relative_tolerance.setSuffix(" %")
        self.relative_tolerance.setValue(settings.relative_tolerance * 100.0)
        tolerance_form.addRow("Tolerância relativa:", self.relative_tolerance)
        self.absolute_tolerance = QDoubleSpinBox(self)
        self.absolute_tolerance.setObjectName("demand_correction_absolute_tolerance")
        self.absolute_tolerance.setDecimals(3)
        self.absolute_tolerance.setRange(0.0, 1_000_000.0)
        self.absolute_tolerance.setSuffix(" kW / kvar")
        self.absolute_tolerance.setValue(settings.absolute_tolerance)
        tolerance_form.addRow("Tolerância absoluta:", self.absolute_tolerance)
        self.max_iterations = QSpinBox(self)
        self.max_iterations.setObjectName("demand_correction_max_iterations")
        self.max_iterations.setRange(1, 500)
        self.max_iterations.setValue(settings.max_iterations)
        tolerance_form.addRow("Máximo de iterações:", self.max_iterations)
        layout.addLayout(tolerance_form)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("demand_correction_error")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Corrigir")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.circuit_combo.count() > 0
        )

        self.circuit_combo.currentIndexChanged.connect(self._show_circuit)
        self._show_circuit()
        self.resize(560, self.sizeHint().height())

    # -- leitura -----------------------------------------------------------

    def selected_circuit_index(self) -> int:
        value = self.circuit_combo.currentData()
        if value is None:
            raise ValueError("Selecione um circuito.")
        return int(value)

    def target_texts(self) -> TargetTexts:
        return tuple(
            (
                self.table.item(row, 0).text().strip(),
                self.table.item(row, 1).text().strip(),
            )
            for row in range(PATAMAR_COUNT)
        )

    def selected_targets(self) -> tuple[PatamarTarget, ...]:
        targets: list[PatamarTarget] = []
        for row, (active_text, reactive_text) in enumerate(self.target_texts()):
            values: list[float | None] = []
            for column, text in ((0, active_text), (1, reactive_text)):
                try:
                    values.append(parse_target(text))
                except ValueError as exc:
                    header = self.table.horizontalHeaderItem(column).text()
                    raise ValueError(
                        f"{self._row_label(row)}, {header}: '{text}' não é um número."
                    ) from exc
            targets.append(PatamarTarget(values[0], values[1]))
        if all(target.is_empty for target in targets):
            raise ValueError("Informe P ou Q de ao menos um patamar.")
        return tuple(targets)

    def selected_scope(self) -> frozenset[DemandScope]:
        scope = frozenset(
            item
            for item, check in self.scope_checks.items()
            if check.isEnabled() and check.isChecked()
        )
        if not scope:
            raise ValueError("Marque ao menos um grupo de cargas a corrigir.")
        return scope

    def selected_settings(self) -> CorrectionSettings:
        return CorrectionSettings(
            self.relative_tolerance.value() / 100.0,
            self.absolute_tolerance.value(),
            self.max_iterations.value(),
        )

    def remembered_targets(self) -> dict[int, TargetTexts]:
        """Os alvos digitados, por circuito, inclusive o do circuito na tela."""

        self._remember_shown()
        return dict(self._remembered)

    # -- comportamento -----------------------------------------------------

    def _fit_table_height(self) -> None:
        self.table.setFixedHeight(
            self.table.horizontalHeader().sizeHint().height()
            + self.table.verticalHeader().defaultSectionSize() * PATAMAR_COUNT
            + 2 * self.table.frameWidth()
        )

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        # O estilo só termina de valer na primeira exibição; o cabeçalho pode
        # crescer e esconder a última linha.
        self._fit_table_height()
        super().showEvent(event)

    def _row_label(self, row: int) -> str:
        labels: Sequence[str] = ()
        if self._patamar_labels is not None and self._shown_circuit is not None:
            labels = tuple(self._patamar_labels(self._shown_circuit))
        name = labels[row] if row < len(labels) and labels[row] else ""
        return f"NPAT {row}" + (f" — {name}" if name else "")

    def _remember_shown(self) -> None:
        if self._shown_circuit is not None:
            self._remembered[self._shown_circuit] = self.target_texts()

    def _show_circuit(self) -> None:
        self._remember_shown()
        try:
            index = self.selected_circuit_index()
        except ValueError:
            self._shown_circuit = None
            return
        self._shown_circuit = index
        texts = self._remembered.get(index, (("", ""),) * PATAMAR_COUNT)
        self.table.setVerticalHeaderLabels(
            [self._row_label(row) for row in range(PATAMAR_COUNT)]
        )
        for row, pair in enumerate(texts):
            for column, text in enumerate(pair):
                self.table.item(row, column).setText(text)

    def _accept_if_valid(self) -> None:
        # Fecha um editor de célula aberto: sem isso o último valor digitado
        # ainda não está no item.
        self.table.setCurrentCell(-1, -1)
        try:
            self.selected_circuit_index()
            self.selected_targets()
            self.selected_scope()
            self.selected_settings()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            self.error_label.setVisible(True)
            return
        self.error_label.setVisible(False)
        self.accept()


class DemandCorrectionReportDialog(QDialog):
    """Desfecho por patamar, com o botão que aplica os fatores na sessão."""

    COLUMNS = (
        "Patamar",
        "P medido",
        "Q medido",
        "P inicial",
        "Q inicial",
        "P final",
        "Q final",
        "fP",
        "fQ",
        "Iterações",
        "Situação",
    )
    COLUMN_SLACK = 12

    def __init__(
        self,
        result: DemandCorrectionResult,
        *,
        circuit_label: str,
        patamar_labels: Sequence[str] = (),
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Correção de demanda — resultado")
        self.setModal(True)
        self.resize(980, 420)
        self.result = result

        layout = QVBoxLayout(self)
        scope = ", ".join(
            item.label.lower() for item in DemandScope if item in result.scope
        )
        header = QLabel(
            f"Circuito {circuit_label}. Cargas corrigidas: {scope} "
            f"({result.scoped_load_count:n} Load no OpenDSS; "
            f"{result.fixed_load_count:n} fixas). Potências em kW e kvar, na "
            "saída do alimentador.",
            self,
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self.table = QTableWidget(len(result.patamares), len(self.COLUMNS), self)
        self.table.setObjectName("demand_correction_report")
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        self.table.verticalHeader().setDefaultSectionSize(30)
        columns = self.table.horizontalHeader()
        columns.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        columns.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        columns.setStretchLastSection(True)
        notes: list[str] = []
        for row, item in enumerate(result.patamares):
            name = (
                patamar_labels[item.step]
                if item.step < len(patamar_labels) and patamar_labels[item.step]
                else ""
            )
            initial = item.initial
            final = item.final
            values = (
                f"NPAT {item.step}" + (f" — {name}" if name else ""),
                _format_number(item.target.active),
                _format_number(item.target.reactive),
                _format_number(None if initial is None else initial.active),
                _format_number(None if initial is None else initial.reactive),
                _format_number(None if final is None else final.active),
                _format_number(None if final is None else final.reactive),
                _format_number(item.active_factor, 6),
                _format_number(item.reactive_factor, 6),
                f"{item.iterations:n}",
                item.status.label,
            )
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self.table.setItem(row, column, cell)
            if item.message:
                notes.append(f"NPAT {item.step}: {item.message}.")
            notes.extend(f"NPAT {item.step}: {warning}." for warning in item.warnings)
        layout.addWidget(self.table)

        failed = result.failed_steps
        correction = result.correction()
        if failed:
            notes.append(
                "Patamares que não convergiram ficam com fator 1 ao aplicar: "
                + ", ".join(f"NPAT {step}" for step in failed)
                + "."
            )
        if correction is not None:
            notes.append(
                "Aplicar mantém os fatores ativos nesta sessão: o Fluxo de "
                "Potência e a exportação OpenDSS passam a multiplicar o perfil "
                "de cada patamar das cargas corrigidas pelo seu fator."
            )
        else:
            notes.append("Nenhum patamar convergiu; não há fatores a aplicar.")
        self.notes_label = QLabel("\n".join(notes), self)
        self.notes_label.setObjectName("demand_correction_report_notes")
        self.notes_label.setWordWrap(True)
        layout.addWidget(self.notes_label)

        self.buttons = QDialogButtonBox(parent=self)
        self.apply_button = self.buttons.addButton(
            "Aplicar", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.apply_button.setObjectName("demand_correction_apply")
        self.apply_button.setEnabled(correction is not None)
        close = self.buttons.addButton("Fechar", QDialogButtonBox.ButtonRole.RejectRole)
        close.setObjectName("demand_correction_close")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._columns_fitted = False

    def _fit_columns(self) -> None:
        # O padding do estilo só entra na conta depois que o estilo é aplicado,
        # por isso o ajuste espera a primeira exibição. A folga extra separa as
        # colunas em que o cabeçalho é mais largo que os valores.
        self.table.resizeColumnsToContents()
        for column in range(self.table.columnCount()):
            self.table.setColumnWidth(
                column, self.table.columnWidth(column) + self.COLUMN_SLACK
            )
        needed = (
            sum(self.table.columnWidth(c) for c in range(self.table.columnCount()))
            + 2 * self.table.frameWidth()
            + self.layout().contentsMargins().left()
            + self.layout().contentsMargins().right()
        )
        screen = self.screen()
        if screen is not None:
            needed = min(needed, screen.availableGeometry().width() - 40)
        if needed > self.width():
            self.resize(needed, self.height())

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._columns_fitted:
            self._columns_fitted = True
            self._fit_columns()
        super().showEvent(event)


def status_summary(result: DemandCorrectionResult) -> str:
    """Uma linha para a barra de status."""

    converged = sum(1 for item in result.patamares if item.converged)
    corrected = sum(
        1 for item in result.patamares if item.status is not PatamarStatus.SKIPPED
    )
    return (
        f"Correção de demanda de {result.circuit_id}: {converged:n} de "
        f"{corrected:n} patamar(es) convergiram"
    )


__all__ = [
    "DemandCorrectionDialog",
    "DemandCorrectionReportDialog",
    "load_demand_correction_settings",
    "save_demand_correction_settings",
    "status_summary",
]
