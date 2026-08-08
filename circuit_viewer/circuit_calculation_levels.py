"""Patamares importados por circuito e suas cópias editáveis de sessão."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .calculation_levels import CalculationLevelSchedule
from .model import CircuitCatalogModel


@dataclass(frozen=True, slots=True)
class CircuitCalculationLevelsModel:
    """Fonte importada imutável, vinculada por identidade ao catálogo."""

    circuits: CircuitCatalogModel
    _schedules: tuple[CalculationLevelSchedule | None, ...]
    _available_indices: tuple[int, ...]
    source_path: str | None

    def __init__(
        self,
        circuits: CircuitCatalogModel,
        schedules: Iterable[CalculationLevelSchedule | None],
        *,
        source_path: str | None = None,
    ) -> None:
        values = tuple(schedules)
        if len(values) != len(circuits):
            raise ValueError("Cada circuito deve possuir uma posição de patamares.")
        available = tuple(index for index, value in enumerate(values) if value is not None)
        if not available:
            raise ValueError("O modelo deve conter ao menos uma agenda válida.")
        object.__setattr__(self, "circuits", circuits)
        object.__setattr__(self, "_schedules", values)
        object.__setattr__(self, "_available_indices", available)
        object.__setattr__(self, "source_path", source_path)

    def __len__(self) -> int:
        return len(self._available_indices)

    @property
    def available_indices(self) -> tuple[int, ...]:
        return self._available_indices

    @property
    def schedules(self) -> tuple[CalculationLevelSchedule | None, ...]:
        return self._schedules

    def schedule(self, circuit_index: int) -> CalculationLevelSchedule | None:
        return self._schedules[int(circuit_index)]

    def schedule_for_id(self, circuit_id: str) -> CalculationLevelSchedule | None:
        index = self.circuits.index_for_id(circuit_id)
        return None if index is None else self.schedule(index)


class CircuitCalculationLevelsController:
    """Cópia virtual da fonte; qualquer alteração vive somente na sessão."""

    __slots__ = ("model", "_schedules")

    def __init__(self, model: CircuitCalculationLevelsModel) -> None:
        self.model = model
        self._schedules = list(model.schedules)

    @property
    def circuits(self) -> CircuitCatalogModel:
        return self.model.circuits

    @property
    def available_indices(self) -> tuple[int, ...]:
        return self.model.available_indices

    def schedule(self, circuit_index: int) -> CalculationLevelSchedule | None:
        return self._schedules[int(circuit_index)]

    def schedule_for_id(self, circuit_id: str) -> CalculationLevelSchedule | None:
        index = self.circuits.index_for_id(circuit_id)
        return None if index is None else self.schedule(index)

    def set_schedule(
        self, circuit_index: int, schedule: CalculationLevelSchedule
    ) -> None:
        index = int(circuit_index)
        if self.model.schedule(index) is None:
            raise ValueError("O circuito não possui patamares importados válidos.")
        self._schedules[index] = schedule
