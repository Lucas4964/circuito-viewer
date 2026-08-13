"""Parâmetros persistíveis do modo de alocação nativa por energia."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from .opendss_export import parse_number


@dataclass(frozen=True, slots=True)
class OpenDssAllocationSettings:
    kwh_days: float = 30.0
    initial_cfactor: float = 4.0
    load_pf: float = 0.92
    num_iterations: int = 2

    def __post_init__(self) -> None:
        for name in ("kwh_days", "initial_cfactor", "load_pf"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} deve ser um número finito.")
        if self.kwh_days <= 0.0:
            raise ValueError("kwh_days deve ser positivo.")
        if self.initial_cfactor <= 0.0:
            raise ValueError("initial_cfactor deve ser positivo.")
        if not 0.0 < self.load_pf <= 1.0:
            raise ValueError("load_pf deve estar no intervalo (0, 1].")
        if type(self.num_iterations) is not int or not 1 <= self.num_iterations <= 100:
            raise ValueError("num_iterations deve estar entre 1 e 100.")

    def as_mapping(self) -> dict[str, str]:
        return {
            "kwh_days": format(self.kwh_days, ".12g"),
            "initial_cfactor": format(self.initial_cfactor, ".12g"),
            "load_pf": format(self.load_pf, ".12g"),
            "num_iterations": str(self.num_iterations),
        }


DEFAULT_OPENDSS_ALLOCATION_SETTINGS = OpenDssAllocationSettings()


def allocation_settings_from_mapping(
    values: Mapping[str, object],
) -> OpenDssAllocationSettings:
    defaults = DEFAULT_OPENDSS_ALLOCATION_SETTINGS
    days = parse_number(str(values.get("kwh_days", "")))
    cfactor = parse_number(str(values.get("initial_cfactor", "")))
    pf = parse_number(str(values.get("load_pf", "")))
    try:
        iterations = int(str(values.get("num_iterations", "")))
    except ValueError:
        iterations = defaults.num_iterations
    try:
        return OpenDssAllocationSettings(
            defaults.kwh_days if days is None else days,
            defaults.initial_cfactor if cfactor is None else cfactor,
            defaults.load_pf if pf is None else pf,
            iterations,
        )
    except ValueError:
        return defaults


__all__ = [
    "DEFAULT_OPENDSS_ALLOCATION_SETTINGS",
    "OpenDssAllocationSettings",
    "allocation_settings_from_mapping",
]
