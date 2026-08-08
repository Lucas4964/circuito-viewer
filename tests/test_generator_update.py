from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

import numpy as np

from circuit_viewer.calculation_levels import (
    CalculationLevel,
    CalculationLevelSchedule,
    default_calculation_levels,
)
from circuit_viewer.curvas import Curve
from circuit_viewer.generator_update import (
    GeneratorScheduleMode,
    GeneratorUpdateError,
    calculate_generator_demands,
    curve_value_at_reference,
)
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitMembership,
    CircuitModel,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    UtmCrs,
)
from circuit_viewer.phase_config import load_phase_configuration


def readonly_indices(values: list[int]) -> np.ndarray:
    result = np.asarray(values, dtype=np.intp)
    result.setflags(write=False)
    return result


def membership(bars: list[int], segments: list[int]) -> CircuitMembership:
    segment_indices = readonly_indices(segments)
    return CircuitMembership(
        bar_indices=readonly_indices(bars),
        common_segment_indices=segment_indices,
        switch_segment_indices=readonly_indices([]),
        segment_indices=segment_indices,
    )


def make_system(
    *,
    energies: tuple[str, ...] = ("720",),
    phases: tuple[str, ...] = ("13",),
    load_bar_indices: tuple[int, ...] | None = None,
    circuit_bars: tuple[tuple[int, ...], ...] = ((0, 1), (2, 3)),
) -> tuple[GeneratorModel, CircuitCatalogModel]:
    bar_count = 6
    bars = CircuitModel(
        [f"B{index}" for index in range(bar_count)],
        [f"CB{index}" for index in range(bar_count)],
        [500_000.0 + index * 10.0 for index in range(bar_count)],
        [8_000_000.0] * bar_count,
        UtmCrs(21, False),
    )
    segments = LineNetworkModel(
        bars,
        [f"T{index}" for index in range(bar_count - 1)],
        [f"TR{index}" for index in range(bar_count - 1)],
        ["13"] * (bar_count - 1),
        list(range(bar_count - 1)),
        list(range(1, bar_count)),
        [""] * (bar_count - 1),
        [""] * (bar_count - 1),
        [""] * (bar_count - 1),
        [10.0] * (bar_count - 1),
    )
    definitions = tuple(
        CircuitDefinition(
            f"C{index}",
            f"B{owned_bars[0]}",
            f"COD-{index}",
            "13.8",
        )
        for index, owned_bars in enumerate(circuit_bars)
    )
    memberships = tuple(
        membership(list(owned_bars), [min(index, bar_count - 2)])
        for index, owned_bars in enumerate(circuit_bars)
    )
    circuits = CircuitCatalogModel(
        segments,
        None,
        definitions,
        memberships,
    )

    generator_count = len(energies)
    if load_bar_indices is None:
        load_bar_indices = tuple(0 for _ in range(generator_count))
    loads = LoadModel(
        bars,
        [f"L{index}" for index in range(generator_count)],
        load_bar_indices,
        [""] * generator_count,
        [f"LOAD-{index}" for index in range(generator_count)],
        [""] * generator_count,
        [""] * generator_count,
        [""] * generator_count,
        phases,
        [""] * generator_count,
    )
    generators = GeneratorModel(
        loads,
        [f"G{index}" for index in range(generator_count)],
        list(range(generator_count)),
        [f"MC{index}" for index in range(generator_count)],
        [f"GEN-{index}" for index in range(generator_count)],
        ["13.8"] * generator_count,
        ["75"] * generator_count,
        ["Y"] * generator_count,
        ["CURVA-IMPORTADA"] * generator_count,
        energies,
        [f"CONS-{index}" for index in range(generator_count)],
        [f"GEN-{index}" for index in range(generator_count)],
        [""] * generator_count,
        [f"Gerador {index}" for index in range(generator_count)],
        phases,
    )
    return generators, circuits


def curve(values: tuple[float, ...] | None = None) -> Curve:
    return Curve(
        "CURVA-CALCULO",
        "Curva de cálculo",
        values or tuple(float(hour) for hour in range(1, 25)),
    )


def calculate(
    generators: GeneratorModel,
    circuits: CircuitCatalogModel,
    *,
    selected_curve: Curve | None = None,
    schedules: tuple[CalculationLevelSchedule, ...] | None = None,
    modes: tuple[GeneratorScheduleMode, ...] | None = None,
    **kwargs,
):
    default = default_calculation_levels()
    return calculate_generator_demands(
        generators,
        circuits,
        load_phase_configuration(),
        selected_curve or curve(),
        schedules or tuple(default for _ in range(len(circuits))),
        modes
        or tuple(GeneratorScheduleMode.DEFAULT for _ in range(len(circuits))),
        **kwargs,
    )


class GeneratorDemandCalculationTests(unittest.TestCase):
    def test_formula_decimal_comma_hour_convention_and_imported_curve_id(self) -> None:
        generators, circuits = make_system(energies=("720,0",), phases=("13",))

        result = calculate(generators, circuits)

        records = result.model.demand_records_for_generator(0)
        self.assertEqual([item.demand for item in records], [23.0, 11.0, 12.0, 22.0])
        self.assertEqual(result.model.mean_demands, (1.0,))
        self.assertEqual(result.model.circuit_indices, (0,))
        self.assertEqual(generators.record(0).curve_id, "CURVA-IMPORTADA")
        self.assertEqual(result.model.curve.curve_id, "CURVA-CALCULO")
        self.assertEqual(curve_value_at_reference(curve(), 0), 24.0)
        self.assertEqual(curve_value_at_reference(curve(), 23), 23.0)

    def test_default_and_own_schedule_are_applied_per_circuit(self) -> None:
        generators, circuits = make_system(
            energies=("720", "720"),
            phases=("1", "1"),
            load_bar_indices=(0, 2),
        )
        own = CalculationLevelSchedule(
            (
                CalculationLevel(0, "A", 22, 5, 0),
                CalculationLevel(1, "B", 5, 11, 5),
                CalculationLevel(2, "C", 11, 18, 18),
                CalculationLevel(3, "D", 18, 22, 20),
            )
        )
        default = default_calculation_levels()

        result = calculate(
            generators,
            circuits,
            schedules=(default, own),
            modes=(GeneratorScheduleMode.DEFAULT, GeneratorScheduleMode.CIRCUIT),
        )

        self.assertEqual(
            [item.demand for item in result.model.demand_records_for_generator(0)],
            [23.0, 11.0, 12.0, 22.0],
        )
        self.assertEqual(
            [item.demand for item in result.model.demand_records_for_generator(1)],
            [24.0, 5.0, 18.0, 20.0],
        )
        self.assertEqual(
            result.model.schedule_modes,
            (GeneratorScheduleMode.DEFAULT, GeneratorScheduleMode.CIRCUIT),
        )

    def test_active_power_is_distributed_only_over_the_actual_phase_letters(self) -> None:
        generators, circuits = make_system(
            energies=("720", "720", "720"),
            phases=("2", "7", "14"),
            load_bar_indices=(0, 0, 0),
        )

        result = calculate(generators, circuits)
        mono = result.model.phase_power_records_for_generator(0)[0]
        two = result.model.phase_power_records_for_generator(1)[0]
        three = result.model.phase_power_records_for_generator(2)[0]

        self.assertEqual((mono.pd, mono.pe, mono.pf), (0.0, -23.0, 0.0))
        self.assertEqual((two.pd, two.pe, two.pf), (-11.5, -11.5, 0.0))
        self.assertAlmostEqual(three.pd, -(23.0 / 3.0))
        self.assertAlmostEqual(three.pe, -(23.0 / 3.0))
        self.assertAlmostEqual(three.pf, -(23.0 / 3.0))
        self.assertEqual(
            (three.qd, three.qe, three.qf),
            (0.0, 0.0, 0.0),
        )

    def test_zero_energy_and_negative_curve_values_are_valid(self) -> None:
        generators, circuits = make_system(
            energies=("0", "720"),
            phases=("1", "1"),
            load_bar_indices=(0, 0),
        )
        values = [1.0] * 24
        values[22] = -2.5

        result = calculate(
            generators,
            circuits,
            selected_curve=curve(tuple(values)),
        )

        self.assertEqual(
            [item.demand for item in result.model.demand_records_for_generator(0)],
            [0.0, 0.0, 0.0, 0.0],
        )
        self.assertEqual(
            [
                item.pd
                for item in result.model.phase_power_records_for_generator(0)
            ],
            [0.0, 0.0, 0.0, 0.0],
        )
        self.assertEqual(
            result.model.demand_records_for_generator(1)[0].demand,
            -2.5,
        )
        self.assertEqual(
            result.model.phase_power_records_for_generator(1)[0].pd,
            2.5,
        )

    def test_invalid_generators_are_omitted_with_specific_reasons(self) -> None:
        generators, circuits = make_system(
            energies=("720", "inválida", "-1", "720", "720"),
            phases=("1", "1", "1", "999", "1"),
            load_bar_indices=(0, 0, 0, 0, 5),
        )

        result = calculate(generators, circuits)

        self.assertEqual((result.valid_generators, result.invalid_generators), (1, 4))
        reasons = {item.generator_id: item.reason for item in result.issues}
        self.assertIn("número válido", reasons["G1"])
        self.assertIn("negativo", reasons["G2"])
        self.assertIn("FASES2", reasons["G3"])
        self.assertIn("não pertence", reasons["G4"])
        for index in range(1, 5):
            self.assertEqual(result.model.demand_records_for_generator(index), ())

    def test_bar_owned_by_multiple_circuits_is_omitted(self) -> None:
        generators, circuits = make_system(
            energies=("720", "720"),
            phases=("1", "1"),
            load_bar_indices=(0, 1),
            circuit_bars=((0, 1), (1, 2)),
        )

        result = calculate(generators, circuits)

        self.assertEqual((result.valid_generators, result.invalid_generators), (1, 1))
        self.assertIn("múltiplos circuitos", result.issues[0].reason)
        self.assertIn("C0", result.issues[0].reason)
        self.assertIn("C1", result.issues[0].reason)

    def test_all_invalid_is_fatal_and_inputs_must_share_identity(self) -> None:
        generators, circuits = make_system(
            energies=("-1",),
            phases=("1",),
        )
        with self.assertRaisesRegex(GeneratorUpdateError, "Nenhum gerador"):
            calculate(generators, circuits)

        other_generators, _ = make_system()
        with self.assertRaisesRegex(GeneratorUpdateError, "mesmo modelo"):
            calculate(other_generators, circuits)

    def test_progress_is_monotonic_and_cancellation_is_observed(self) -> None:
        generators, circuits = make_system(
            energies=("720", "720"),
            phases=("1", "1"),
            load_bar_indices=(0, 2),
        )
        progress: list[tuple[int, int]] = []

        result = calculate(
            generators,
            circuits,
            progress=lambda current, total: progress.append((current, total)),
        )

        self.assertEqual(result.valid_generators, 2)
        self.assertEqual(progress, [(1, 4), (2, 4), (3, 4), (4, 4)])
        with self.assertRaises(InterruptedError):
            calculate(generators, circuits, cancel_check=lambda: True)

    def test_derived_model_is_frozen_and_keeps_input_identities(self) -> None:
        generators, circuits = make_system()
        configuration = load_phase_configuration()
        schedule = default_calculation_levels()
        selected_curve = curve()

        result = calculate_generator_demands(
            generators,
            circuits,
            configuration,
            selected_curve,
            (schedule, schedule),
            (GeneratorScheduleMode.DEFAULT, GeneratorScheduleMode.DEFAULT),
        )

        self.assertIs(result.model.generators, generators)
        self.assertIs(result.model.circuits, circuits)
        self.assertIs(result.model.phase_configuration, configuration)
        self.assertIs(result.model.curve, selected_curve)
        with self.assertRaises(FrozenInstanceError):
            result.model.curve = curve()  # type: ignore[misc]

    def test_reference_hour_rejects_values_outside_zero_to_twenty_three(self) -> None:
        with self.assertRaisesRegex(ValueError, "entre 0 e 23"):
            curve_value_at_reference(curve(), 24)
        with self.assertRaisesRegex(ValueError, "entre 0 e 23"):
            curve_value_at_reference(curve(), True)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
