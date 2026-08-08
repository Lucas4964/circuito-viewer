"""Cálculo em memória das demandas dos geradores por patamar."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence

from .calculation_levels import CalculationLevelSchedule
from .curvas import Curve
from .model import CircuitCatalogModel, GeneratorModel
from .opendss_export import parse_number
from .phase_config import PhaseConfiguration


MAX_REPORTED_ISSUES = 200
ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


class GeneratorUpdateError(ValueError):
    """Falha fatal que impede instalar um novo resultado."""


class GeneratorScheduleMode(str, Enum):
    DEFAULT = "default"
    CIRCUIT = "circuit"


@dataclass(frozen=True, slots=True)
class GeneratorDemandRecord:
    generator_id: str
    npat: int
    demand: float


@dataclass(frozen=True, slots=True)
class GeneratorPhasePowerRecord:
    generator_id: str
    npat: int
    pd: float
    pe: float
    pf: float
    qd: float = 0.0
    qe: float = 0.0
    qf: float = 0.0


@dataclass(frozen=True, slots=True)
class GeneratorUpdateIssue:
    generator_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class GeneratorUpdateModel:
    """Retrato imutável do cálculo e de todas as entradas utilizadas."""

    generators: GeneratorModel
    circuits: CircuitCatalogModel
    phase_configuration: PhaseConfiguration
    curve: Curve
    effective_schedules: tuple[CalculationLevelSchedule, ...]
    schedule_modes: tuple[GeneratorScheduleMode, ...]
    mean_demands: tuple[float | None, ...]
    circuit_indices: tuple[int | None, ...]
    _demands_by_generator: tuple[tuple[GeneratorDemandRecord, ...] | None, ...]
    _phase_powers_by_generator: (
        tuple[tuple[GeneratorPhasePowerRecord, ...] | None, ...]
    )

    def __post_init__(self) -> None:
        if self.circuits.segments.bars is not self.generators.bars:
            raise ValueError("Os geradores e circuitos devem usar as mesmas barras.")
        circuit_count = len(self.circuits)
        if len(self.effective_schedules) != circuit_count:
            raise ValueError("Cada circuito deve possuir um patamar efetivo.")
        if len(self.schedule_modes) != circuit_count:
            raise ValueError("Cada circuito deve possuir uma origem de patamares.")
        generator_count = len(self.generators)
        dense = (
            self.mean_demands,
            self.circuit_indices,
            self._demands_by_generator,
            self._phase_powers_by_generator,
        )
        if any(len(values) != generator_count for values in dense):
            raise ValueError("Os resultados devem corresponder aos geradores.")
        valid_count = 0
        for index, (demands, powers) in enumerate(
            zip(
                self._demands_by_generator,
                self._phase_powers_by_generator,
                strict=True,
            )
        ):
            if demands is None or powers is None:
                if demands is not None or powers is not None:
                    raise ValueError("Um resultado parcial de gerador é inválido.")
                continue
            generator_id = self.generators.generator_ids[index]
            if (
                len(demands) != 4
                or len(powers) != 4
                or tuple(item.npat for item in demands) != (0, 1, 2, 3)
                or tuple(item.npat for item in powers) != (0, 1, 2, 3)
                or any(item.generator_id != generator_id for item in demands)
                or any(item.generator_id != generator_id for item in powers)
            ):
                raise ValueError("Cada gerador calculado deve possuir quatro patamares.")
            valid_count += 1
        if valid_count == 0:
            raise ValueError("O modelo deve conter ao menos um gerador calculado.")

    def demand_records_for_generator(
        self, generator_index: int
    ) -> tuple[GeneratorDemandRecord, ...]:
        if not 0 <= int(generator_index) < len(self.generators):
            raise IndexError(generator_index)
        values = self._demands_by_generator[int(generator_index)]
        return () if values is None else values

    def phase_power_records_for_generator(
        self, generator_index: int
    ) -> tuple[GeneratorPhasePowerRecord, ...]:
        if not 0 <= int(generator_index) < len(self.generators):
            raise IndexError(generator_index)
        values = self._phase_powers_by_generator[int(generator_index)]
        return () if values is None else values


@dataclass(frozen=True, slots=True)
class GeneratorUpdateResult:
    model: GeneratorUpdateModel
    total_generators: int
    valid_generators: int
    invalid_generators: int
    issues: tuple[GeneratorUpdateIssue, ...]
    omitted_issues: int

    @property
    def has_warnings(self) -> bool:
        return self.invalid_generators > 0


def curve_value_at_reference(curve: Curve, reference_hour: int) -> float:
    """Converte a hora 0–23 para a convenção visual 1–24 das curvas."""

    if type(reference_hour) is not int or not 0 <= reference_hour <= 23:
        raise ValueError("HORARIO_REF deve estar entre 0 e 23.")
    visual_hour = 24 if reference_hour == 0 else reference_hour
    return curve.values[visual_hour - 1]


def calculate_generator_demands(
    generators: GeneratorModel,
    circuits: CircuitCatalogModel,
    phase_configuration: PhaseConfiguration,
    curve: Curve,
    effective_schedules: Sequence[CalculationLevelSchedule],
    schedule_modes: Sequence[GeneratorScheduleMode | str],
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> GeneratorUpdateResult:
    """Calcula demandas totais e potências ativas por fase para quatro NPAT."""

    if circuits.segments.bars is not generators.bars:
        raise GeneratorUpdateError(
            "Os geradores e circuitos devem usar o mesmo modelo de barras."
        )
    schedules = tuple(effective_schedules)
    try:
        modes = tuple(GeneratorScheduleMode(value) for value in schedule_modes)
    except ValueError as exc:
        raise GeneratorUpdateError("Origem de patamares inválida.") from exc
    if len(schedules) != len(circuits) or len(modes) != len(circuits):
        raise GeneratorUpdateError(
            "Informe uma agenda e uma origem para cada circuito."
        )

    total_work = len(circuits) + len(generators)
    completed = 0
    owners_by_bar: list[list[int]] = [[] for _ in range(len(generators.bars))]
    for circuit_index, membership in enumerate(circuits.memberships):
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Atualização cancelada.")
        for bar_index in membership.bar_indices:
            owners_by_bar[int(bar_index)].append(circuit_index)
        completed += 1
        if progress is not None:
            progress(completed, total_work)

    mean_demands: list[float | None] = [None] * len(generators)
    circuit_indices: list[int | None] = [None] * len(generators)
    demand_groups: list[tuple[GeneratorDemandRecord, ...] | None] = [
        None
    ] * len(generators)
    power_groups: list[tuple[GeneratorPhasePowerRecord, ...] | None] = [
        None
    ] * len(generators)
    issues: list[GeneratorUpdateIssue] = []
    omitted_issues = 0

    def report(generator_id: str, reason: str) -> None:
        nonlocal omitted_issues
        if len(issues) < MAX_REPORTED_ISSUES:
            issues.append(GeneratorUpdateIssue(generator_id, reason))
        else:
            omitted_issues += 1

    valid_count = 0
    for generator_index in range(len(generators)):
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Atualização cancelada.")
        record = generators.record(generator_index)
        energy = parse_number(record.generation_kwh)
        if energy is None:
            report(record.generator_id, "GERACAO_KWH não é um número válido")
        elif energy < 0.0:
            report(record.generator_id, "GERACAO_KWH não pode ser negativo")
        else:
            owners = owners_by_bar[int(generators.bar_indices[generator_index])]
            if not owners:
                report(record.generator_id, "a barra do gerador não pertence a circuito")
            elif len(owners) > 1:
                circuit_ids = ", ".join(
                    circuits.definition(index).circuit_id for index in owners
                )
                report(
                    record.generator_id,
                    f"a barra pertence a múltiplos circuitos: {circuit_ids}",
                )
            else:
                letters = phase_configuration.phase_letters_for_value(record.phases)
                if letters is None:
                    report(
                        record.generator_id,
                        f"FASES2 sem relação válida: {record.phases or '<vazio>'}",
                    )
                else:
                    circuit_index = owners[0]
                    schedule = schedules[circuit_index]
                    mean_demand = energy / (30.0 * 24.0)
                    demands: list[GeneratorDemandRecord] = []
                    powers: list[GeneratorPhasePowerRecord] = []
                    for level in schedule.levels:
                        demand = mean_demand * curve_value_at_reference(
                            curve, level.reference_hour
                        )
                        # Convenção elétrica da aplicação: consumo é positivo e
                        # geração é negativa. A demanda total permanece sem
                        # inversão; somente a potência ativa injetada por fase
                        # recebe o sinal de geração.
                        per_phase = (
                            0.0 if demand == 0.0 else -(demand / len(letters))
                        )
                        active = {
                            "D": per_phase if "D" in letters else 0.0,
                            "E": per_phase if "E" in letters else 0.0,
                            "F": per_phase if "F" in letters else 0.0,
                        }
                        demands.append(
                            GeneratorDemandRecord(
                                record.generator_id, level.npat, demand
                            )
                        )
                        powers.append(
                            GeneratorPhasePowerRecord(
                                record.generator_id,
                                level.npat,
                                active["D"],
                                active["E"],
                                active["F"],
                            )
                        )
                    mean_demands[generator_index] = mean_demand
                    circuit_indices[generator_index] = circuit_index
                    demand_groups[generator_index] = tuple(demands)
                    power_groups[generator_index] = tuple(powers)
                    valid_count += 1
        completed += 1
        if progress is not None:
            progress(completed, total_work)

    if cancel_check is not None and cancel_check():
        raise InterruptedError("Atualização cancelada.")
    if valid_count == 0:
        details = issues[0].reason if issues else "nenhum registro processável"
        raise GeneratorUpdateError(
            f"Nenhum gerador pôde ser atualizado: {details}."
        )
    model = GeneratorUpdateModel(
        generators=generators,
        circuits=circuits,
        phase_configuration=phase_configuration,
        curve=curve,
        effective_schedules=schedules,
        schedule_modes=modes,
        mean_demands=tuple(mean_demands),
        circuit_indices=tuple(circuit_indices),
        _demands_by_generator=tuple(demand_groups),
        _phase_powers_by_generator=tuple(power_groups),
    )
    return GeneratorUpdateResult(
        model=model,
        total_generators=len(generators),
        valid_generators=valid_count,
        invalid_generators=len(generators) - valid_count,
        issues=tuple(issues),
        omitted_issues=omitted_issues,
    )
