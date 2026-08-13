"""Escolhas do modo OpenDSS com alocação nativa por energia."""

from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from .allocation_measurements import AllocationMeasurementModel
from .calculation_levels import CalculationLevelSchedule
from .circuit_calculation_levels import CircuitCalculationLevelsController
from .curvas import Curve
from .model import CircuitCatalogModel
from .opendss_allocation_settings import (
    DEFAULT_OPENDSS_ALLOCATION_SETTINGS,
    OpenDssAllocationSettings,
    allocation_settings_from_mapping,
)


SETTINGS_PREFIX = "opendss/allocation_"


def load_opendss_allocation_settings(settings: QSettings) -> OpenDssAllocationSettings:
    stored = {
        key: settings.value(f"{SETTINGS_PREFIX}{key}")
        for key in DEFAULT_OPENDSS_ALLOCATION_SETTINGS.as_mapping()
    }
    return allocation_settings_from_mapping(
        {key: value for key, value in stored.items() if value is not None}
    )


def save_opendss_allocation_settings(
    settings: QSettings,
    value: OpenDssAllocationSettings,
) -> None:
    for key, text in value.as_mapping().items():
        settings.setValue(f"{SETTINGS_PREFIX}{key}", text)
    settings.sync()


class OpenDssAllocationDialog(QDialog):
    """Seleciona um circuito medido e produz um retrato validado da execução."""

    def __init__(
        self,
        circuits: CircuitCatalogModel,
        measurements: AllocationMeasurementModel,
        curves: tuple[Curve, ...],
        default_schedule: CalculationLevelSchedule,
        circuit_levels: CircuitCalculationLevelsController | None,
        settings: OpenDssAllocationSettings,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        if measurements.circuits is not circuits:
            raise ValueError("As correntes devem pertencer aos circuitos informados.")
        if circuit_levels is not None and circuit_levels.circuits is not circuits:
            raise ValueError("Os patamares devem pertencer aos circuitos informados.")
        self.setWindowTitle("OpenDSS — Alocação por energia")
        self.setModal(True)
        self.resize(600, 430)
        self._circuits = circuits
        self._measurements = measurements
        self._curves = tuple(curves)
        self._default_schedule = default_schedule
        self._circuit_levels = circuit_levels

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Gera quatro circuitos snapshot autocontidos. Somente as cargas "
            "definidas por kWh participam de AllocateLoads; os equivalentes de "
            "geração são cargas negativas fixas.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        warning = QLabel(
            "Atenção: PeakCurrent usa módulos. Se a GD provocar fluxo "
            "reverso, o sentido da solução alocada pode ser ambíguo.",
            self,
        )
        warning.setObjectName("opendss_allocation_reverse_flow_warning")
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b36b00; font-weight: 600;")
        layout.addWidget(warning)

        form = QFormLayout()
        self.circuit_combo = QComboBox(self)
        self.circuit_combo.setObjectName("opendss_allocation_circuit_combo")
        for index in measurements.available_indices:
            definition = circuits.definition(index)
            label = definition.code or definition.circuit_id
            self.circuit_combo.addItem(
                f"{label} (CIRC_ID {definition.circuit_id})", index
            )
        form.addRow("Circuito:", self.circuit_combo)

        self.curve_combo = QComboBox(self)
        self.curve_combo.setObjectName("opendss_allocation_curve_combo")
        for curve in self._curves:
            self.curve_combo.addItem(curve.name, curve.curve_id)
        form.addRow("Curva horária:", self.curve_combo)

        self.schedule_combo = QComboBox(self)
        self.schedule_combo.setObjectName("opendss_allocation_schedule_combo")
        self.schedule_combo.addItem("DEFAULT", "default")
        form.addRow("Agenda:", self.schedule_combo)
        self.circuit_combo.currentIndexChanged.connect(self._sync_schedule_choices)

        self.kwh_days = QDoubleSpinBox(self)
        self.kwh_days.setObjectName("opendss_allocation_kwh_days")
        self.kwh_days.setDecimals(3)
        self.kwh_days.setRange(0.001, 1_000_000.0)
        self.kwh_days.setValue(settings.kwh_days)
        self.kwh_days.setSuffix(" dias")
        form.addRow("kWhDays:", self.kwh_days)

        self.cfactor = QDoubleSpinBox(self)
        self.cfactor.setObjectName("opendss_allocation_cfactor")
        self.cfactor.setDecimals(6)
        self.cfactor.setRange(0.000001, 1_000_000.0)
        self.cfactor.setValue(settings.initial_cfactor)
        form.addRow("CFactor inicial:", self.cfactor)

        self.pf = QDoubleSpinBox(self)
        self.pf.setObjectName("opendss_allocation_pf")
        self.pf.setDecimals(6)
        self.pf.setRange(0.000001, 1.0)
        self.pf.setSingleStep(0.01)
        self.pf.setValue(settings.load_pf)
        form.addRow("PF das cargas:", self.pf)

        self.iterations = QSpinBox(self)
        self.iterations.setObjectName("opendss_allocation_iterations")
        self.iterations.setRange(1, 100)
        self.iterations.setValue(settings.num_iterations)
        form.addRow("Iterações:", self.iterations)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._sync_schedule_choices()
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(bool(measurements.available_indices and self._curves))

    def selected_circuit_index(self) -> int:
        value = self.circuit_combo.currentData()
        if value is None:
            raise ValueError("Selecione um circuito com quatro medições.")
        return int(value)

    def selected_curve(self) -> Curve:
        curve_id = self.curve_combo.currentData()
        for curve in self._curves:
            if curve.curve_id == curve_id:
                return curve
        raise ValueError("Selecione uma curva horária válida.")

    def selected_schedule(self) -> CalculationLevelSchedule:
        if self.schedule_combo.currentData() == "circuit":
            own = (
                None
                if self._circuit_levels is None
                else self._circuit_levels.schedule(self.selected_circuit_index())
            )
            if own is None:
                raise ValueError("O circuito não possui quatro patamares próprios.")
            return own
        return self._default_schedule

    def selected_settings(self) -> OpenDssAllocationSettings:
        return OpenDssAllocationSettings(
            self.kwh_days.value(),
            self.cfactor.value(),
            self.pf.value(),
            self.iterations.value(),
        )

    def _sync_schedule_choices(self) -> None:
        previous = self.schedule_combo.currentData()
        self.schedule_combo.blockSignals(True)
        self.schedule_combo.clear()
        self.schedule_combo.addItem("DEFAULT", "default")
        try:
            index = self.selected_circuit_index()
        except ValueError:
            index = -1
        own = (
            None
            if self._circuit_levels is None or index < 0
            else self._circuit_levels.schedule(index)
        )
        if own is not None:
            self.schedule_combo.addItem("Próprios", "circuit")
        wanted = self.schedule_combo.findData(previous)
        self.schedule_combo.setCurrentIndex(max(wanted, 0))
        self.schedule_combo.blockSignals(False)


__all__ = [
    "OpenDssAllocationDialog",
    "load_opendss_allocation_settings",
    "save_opendss_allocation_settings",
]
