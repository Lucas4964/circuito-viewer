"""Janela de edição e salvamento dos patamares de cálculo."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from .calculation_levels import CalculationLevelCatalog, CalculationLevelSchedule
from .circuit_calculation_levels import CircuitCalculationLevelsController
from .calculation_levels_store import (
    load_calculation_levels,
    save_calculation_levels,
)
from .patamares_table import PatamarNumberDelegate, PatamaresTableModel
from .table_columns import EXCEL_LIKE_TABLE_STYLE, enable_interactive_columns


class PatamaresWindow(QDialog):
    scheduleSaved = pyqtSignal(object)
    scheduleReloaded = pyqtSignal(object)
    circuitScheduleSaved = pyqtSignal(str, object)

    def __init__(
        self,
        schedule: CalculationLevelSchedule,
        *,
        storage_path: str | Path | None = None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Patamares")
        self.setModal(False)
        self.resize(820, 300)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._storage_path = storage_path
        self._saved_schedule = schedule
        self._circuit_levels: CircuitCalculationLevelsController | None = None
        self._selected_circuit_index: int | None = None
        self.catalog = CalculationLevelCatalog.from_schedule(schedule)
        self._dirty = False

        layout = QVBoxLayout(self)
        description = QLabel(
            "Defina os períodos horários que serão usados nos cálculos elétricos.",
            self,
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("Configuração:", self))
        self.schedule_selector = QComboBox(self)
        self.schedule_selector.setObjectName("patamares_schedule_selector")
        self.schedule_selector.addItem("DEFAULT", None)
        selector_layout.addWidget(self.schedule_selector, 1)
        layout.addLayout(selector_layout)

        self.table_model = PatamaresTableModel(self.catalog, self)
        self.table = QTableView(self)
        self.table.setObjectName("patamares_table")
        self.table.setModel(self.table_model)
        self.number_delegate = PatamarNumberDelegate(self.table)
        for column in (0, 2, 3, 4):
            self.table.setItemDelegateForColumn(column, self.number_delegate)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        enable_interactive_columns(self.table, always_refit=True)
        layout.addWidget(self.table)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("patamares_status_label")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save_button = QPushButton("Salvar", self)
        self.save_button.setObjectName("patamares_save_button")
        buttons.addWidget(self.save_button)
        self.close_button = QPushButton("Fechar", self)
        self.close_button.setObjectName("patamares_close_button")
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.table_model.contentChanged.connect(self._mark_dirty)
        self.table_model.validationFailed.connect(self._show_error)
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.close)
        self.schedule_selector.currentIndexChanged.connect(
            self._on_schedule_selection_changed
        )

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def saved_schedule(self) -> CalculationLevelSchedule:
        return self._saved_schedule

    def refresh(self) -> None:
        self.table.viewport().update()

    @property
    def selected_circuit_index(self) -> int | None:
        return self._selected_circuit_index

    @property
    def circuit_levels(self) -> CircuitCalculationLevelsController | None:
        return self._circuit_levels

    def set_circuit_levels(
        self, controller: CircuitCalculationLevelsController | None
    ) -> bool:
        """Instala uma nova fonte de sessão e recompõe as opções da combo."""

        if not self.confirm_pending_changes():
            return False
        previous_id: str | None = None
        if self._selected_circuit_index is not None and self._circuit_levels is not None:
            previous_id = self._circuit_levels.circuits.definition(
                self._selected_circuit_index
            ).circuit_id
        self._circuit_levels = controller
        with QSignalBlocker(self.schedule_selector):
            self.schedule_selector.clear()
            self.schedule_selector.addItem("DEFAULT", None)
            selected_combo_index = 0
            if controller is not None:
                for circuit_index in controller.available_indices:
                    definition = controller.circuits.definition(circuit_index)
                    label = definition.circuit_id
                    if definition.code:
                        label = f"{definition.circuit_id} — {definition.code}"
                    self.schedule_selector.addItem(label, circuit_index)
                    if definition.circuit_id == previous_id:
                        selected_combo_index = self.schedule_selector.count() - 1
                self.schedule_selector.setCurrentIndex(selected_combo_index)
        self._selected_circuit_index = self.schedule_selector.currentData()
        self._load_selected_schedule()
        return True

    def confirm_pending_changes(self) -> bool:
        """Resolve a edição atual antes de trocar ou substituir sua fonte."""

        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Alterações não salvas",
            "Há alterações não salvas nos patamares. Salvar agora?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        self._restore_selected_schedule()
        return True

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._clear_status()

    def _save(self) -> bool:
        try:
            schedule = self.catalog.to_schedule()
        except (TypeError, ValueError) as exc:
            self._show_error(str(exc))
            return False
        if self._selected_circuit_index is None:
            try:
                save_calculation_levels(schedule, self._storage_path)
            except OSError as exc:
                self._show_error(
                    f"Não foi possível gravar os patamares: {exc.strerror or exc}"
                )
                return False
            self._saved_schedule = schedule
        else:
            controller = self._circuit_levels
            if controller is None:
                self._show_error("A fonte de patamares do circuito não está disponível.")
                return False
            controller.set_schedule(self._selected_circuit_index, schedule)
        self.catalog = CalculationLevelCatalog.from_schedule(schedule)
        self.table_model.set_catalog(self.catalog)
        self._dirty = False
        if self._selected_circuit_index is None:
            self._show_info("4 patamares DEFAULT salvos permanentemente.")
            self.scheduleSaved.emit(schedule)
        else:
            circuit_id = self._circuit_levels.circuits.definition(  # type: ignore[union-attr]
                self._selected_circuit_index
            ).circuit_id
            self._show_info(
                f"Patamares do circuito {circuit_id} salvos apenas nesta sessão."
            )
            self.circuitScheduleSaved.emit(circuit_id, schedule)
        return True

    def _reload_from_disk(self) -> None:
        result = load_calculation_levels(self._storage_path)
        self._saved_schedule = result.schedule
        self.catalog = CalculationLevelCatalog.from_schedule(result.schedule)
        self.table_model.set_catalog(self.catalog)
        self._dirty = False
        self.scheduleReloaded.emit(result.schedule)
        if result.issue:
            self._show_error(result.issue)
        else:
            self._clear_status()

    def _restore_selected_schedule(self) -> None:
        if self._selected_circuit_index is None:
            self._reload_from_disk()
            return
        self._load_selected_schedule()

    def _load_selected_schedule(self) -> None:
        if self._selected_circuit_index is None:
            schedule = self._saved_schedule
        else:
            if self._circuit_levels is None:
                return
            schedule = self._circuit_levels.schedule(self._selected_circuit_index)
            if schedule is None:
                return
        self.catalog = CalculationLevelCatalog.from_schedule(schedule)
        self.table_model.set_catalog(self.catalog)
        self._dirty = False
        self._clear_status()

    def _on_schedule_selection_changed(self, _combo_index: int) -> None:
        new_circuit_index = self.schedule_selector.currentData()
        old_circuit_index = self._selected_circuit_index
        if new_circuit_index == old_circuit_index:
            return
        if not self.confirm_pending_changes():
            with QSignalBlocker(self.schedule_selector):
                old_combo_index = self.schedule_selector.findData(old_circuit_index)
                self.schedule_selector.setCurrentIndex(max(0, old_combo_index))
            return
        self._selected_circuit_index = new_circuit_index
        self._load_selected_schedule()

    def _show_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: palette(bright-text);")
        self.status_label.setText(message)

    def _show_info(self, message: str) -> None:
        self.status_label.setStyleSheet("")
        self.status_label.setText(message)

    def _clear_status(self) -> None:
        self.status_label.setStyleSheet("")
        self.status_label.clear()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.confirm_pending_changes():
            event.accept()
        else:
            event.ignore()
