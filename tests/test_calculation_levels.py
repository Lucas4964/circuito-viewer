from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from circuit_viewer.calculation_levels import (
    CalculationLevel,
    CalculationLevelCatalog,
    CalculationLevelSchedule,
    default_calculation_levels,
)


def level(npat: int, name: str, start: int, end: int, reference: int):
    return CalculationLevel(npat, name, start, end, reference)


class CalculationLevelDefaultsTests(unittest.TestCase):
    def test_defaults_match_the_reference_table(self) -> None:
        schedule = default_calculation_levels()
        self.assertEqual(
            [
                (item.npat, item.name, item.start_hour, item.end_hour, item.reference_hour)
                for item in schedule.levels
            ],
            [
                (0, "Madrugada", 22, 5, 23),
                (1, "Manhã", 5, 11, 11),
                (2, "Tarde", 11, 18, 12),
                (3, "Noite", 18, 22, 22),
            ],
        )
        self.assertEqual(sum(item.duration for item in schedule.levels), 24)

    def test_saved_levels_are_immutable(self) -> None:
        item = default_calculation_levels().level(0)
        with self.assertRaises(FrozenInstanceError):
            item.name = "Outro"  # type: ignore[misc]

    def test_schedule_sorts_an_unsorted_input_by_npat(self) -> None:
        original = default_calculation_levels().levels
        schedule = CalculationLevelSchedule(tuple(reversed(original)))
        self.assertEqual([item.npat for item in schedule.levels], [0, 1, 2, 3])


class CalculationLevelValidationTests(unittest.TestCase):
    def test_reference_may_equal_start_or_end_in_a_midnight_interval(self) -> None:
        self.assertEqual(level(0, "A", 22, 5, 22).reference_hour, 22)
        self.assertEqual(level(0, "A", 22, 5, 5).reference_hour, 5)

    def test_reference_outside_the_circular_interval_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "HORARIO_REF"):
            level(0, "A", 22, 5, 12)

    def test_hours_must_be_in_the_day_and_start_must_differ_from_end(self) -> None:
        with self.assertRaisesRegex(ValueError, "entre 0 e 23"):
            level(0, "A", 24, 5, 1)
        with self.assertRaisesRegex(ValueError, "diferentes"):
            level(0, "A", 5, 5, 5)

    def test_npat_must_be_an_integer_from_zero_to_three(self) -> None:
        with self.assertRaises(ValueError):
            level(True, "A", 0, 1, 1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "entre 0 e 3"):
            level(4, "A", 0, 1, 1)

    def test_names_are_trimmed_required_and_unique_without_case(self) -> None:
        self.assertEqual(level(0, "  Nome   composto ", 0, 1, 1).name, "Nome composto")
        with self.assertRaisesRegex(ValueError, "NOME"):
            level(0, "   ", 0, 1, 1)
        items = list(default_calculation_levels().levels)
        items[1] = level(1, "madrugada", 5, 11, 11)
        with self.assertRaisesRegex(ValueError, "nomes"):
            CalculationLevelSchedule(tuple(items))

    def test_npat_must_be_exactly_the_zero_to_three_permutation(self) -> None:
        items = list(default_calculation_levels().levels)
        items[1] = level(0, "Outra", 5, 11, 11)
        with self.assertRaisesRegex(ValueError, "0, 1, 2 e 3"):
            CalculationLevelSchedule(tuple(items))

    def test_gap_or_overlap_between_consecutive_levels_is_refused(self) -> None:
        items = list(default_calculation_levels().levels)
        items[1] = level(1, "Manhã", 6, 11, 11)
        with self.assertRaisesRegex(ValueError, "lacuna ou sobreposição"):
            CalculationLevelSchedule(tuple(items))

    def test_a_contiguous_schedule_that_loops_twice_is_refused(self) -> None:
        items = (
            level(0, "A", 0, 6, 6),
            level(1, "B", 6, 0, 0),
            level(2, "C", 0, 6, 6),
            level(3, "D", 6, 0, 0),
        )
        with self.assertRaisesRegex(ValueError, "24 horas"):
            CalculationLevelSchedule(items)

    def test_drafts_do_not_mutate_the_saved_schedule(self) -> None:
        schedule = default_calculation_levels()
        catalog = CalculationLevelCatalog.from_schedule(schedule)
        catalog.draft(0).name = "Editado"
        self.assertEqual(schedule.level(0).name, "Madrugada")


if __name__ == "__main__":
    unittest.main()
