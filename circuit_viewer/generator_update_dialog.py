"""Diálogo de escolha da curva e dos patamares efetivos por circuito."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .calculation_levels import CalculationLevelSchedule
from .circuit_calculation_levels import CircuitCalculationLevelsController
from .curvas import Curve
from .generator_update import GeneratorScheduleMode
from .model import CircuitCatalogModel
from .table_columns import EXCEL_LIKE_TABLE_STYLE


class UpdateGeneratorsDialog(QDialog):
    """Produz um retrato completo das escolhas antes de iniciar o worker."""

    HEADERS = ("CIRC_ID", "CODIGO", "PATAMARES")

    def __init__(
        self,
        curves: tuple[Curve, ...],
        circuits: CircuitCatalogModel,
        default_schedule: CalculationLevelSchedule,
        circuit_levels: CircuitCalculationLevelsController | None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        if circuit_levels is not None and circuit_levels.circuits is not circuits:
            raise ValueError("Os patamares devem pertencer aos circuitos informados.")
        self.setWindowTitle("Atualizar Geradores")
        self.setModal(True)
        self.resize(620, 430)
        self._curves = tuple(curves)
        self._circuits = circuits
        self._default_schedule = default_schedule
        self._circuit_levels = circuit_levels
        self._mode_combos: list[QComboBox] = []

        layout = QVBoxLayout(self)
        message = QLabel(
            "Selecione uma curva para todos os geradores e a origem dos "
            "patamares de cada circuito.",
            self,
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        form = QFormLayout()
        self.curve_combo = QComboBox(self)
        self.curve_combo.setObjectName("generator_update_curve_combo")
        for curve in self._curves:
            self.curve_combo.addItem(curve.name, curve.curve_id)
        form.addRow("Curva:", self.curve_combo)
        layout.addLayout(form)

        self.circuit_table = QTableWidget(len(circuits), len(self.HEADERS), self)
        self.circuit_table.setObjectName("generator_update_circuit_table")
        self.circuit_table.setHorizontalHeaderLabels(self.HEADERS)
        self.circuit_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.circuit_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.circuit_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.circuit_table.verticalHeader().hide()
        self.circuit_table.verticalHeader().setDefaultSectionSize(30)
        self.circuit_table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        self.circuit_table.horizontalHeader().setStretchLastSection(True)
        for row, definition in enumerate(circuits.definitions):
            id_item = QTableWidgetItem(definition.circuit_id)
            code_item = QTableWidgetItem(definition.code or "—")
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            code_item.setFlags(code_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.circuit_table.setItem(row, 0, id_item)
            self.circuit_table.setItem(row, 1, code_item)

            combo = QComboBox(self.circuit_table)
            combo.addItem("DEFAULT", GeneratorScheduleMode.DEFAULT.value)
            own_schedule = (
                None if circuit_levels is None else circuit_levels.schedule(row)
            )
            if own_schedule is not None:
                combo.addItem("Próprios", GeneratorScheduleMode.CIRCUIT.value)
            else:
                combo.setToolTip(
                    "Este circuito não possui quatro patamares próprios válidos."
                )
            combo.setCurrentIndex(0)
            combo.setObjectName(f"generator_schedule_mode_{row}")
            self._mode_combos.append(combo)
            self.circuit_table.setCellWidget(row, 2, combo)
        self.circuit_table.resizeColumnsToContents()
        layout.addWidget(self.circuit_table, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.update_button = self.buttons.addButton(
            "Atualizar", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.update_button.setObjectName("generator_update_confirm_button")
        self.update_button.setEnabled(bool(self._curves))
        self.update_button.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def selected_curve(self) -> Curve:
        curve_id = self.curve_combo.currentData()
        for curve in self._curves:
            if curve.curve_id == curve_id:
                return curve
        raise ValueError("Selecione uma curva válida.")

    def schedule_modes(self) -> tuple[GeneratorScheduleMode, ...]:
        return tuple(
            GeneratorScheduleMode(combo.currentData())
            for combo in self._mode_combos
        )

    def effective_schedules(self) -> tuple[CalculationLevelSchedule, ...]:
        schedules: list[CalculationLevelSchedule] = []
        for circuit_index, mode in enumerate(self.schedule_modes()):
            if mode is GeneratorScheduleMode.CIRCUIT:
                own = (
                    None
                    if self._circuit_levels is None
                    else self._circuit_levels.schedule(circuit_index)
                )
                if own is None:
                    raise ValueError(
                        "Um circuito selecionado não possui patamares próprios."
                    )
                schedules.append(own)
            else:
                schedules.append(self._default_schedule)
        return tuple(schedules)
