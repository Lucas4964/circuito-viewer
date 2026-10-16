from __future__ import annotations

import unittest

from circuit_viewer.curvas_categorias import CurveCategory, category_sort_key


def _category(**changes) -> CurveCategory:
    fields = dict(
        category_id="2",
        description="Clientes BT res_0_100 kWh - Tipologia 1",
        consumer_class=0,
        minimum=-1.0,
        maximum=100.0,
        market_share=25.5,
    )
    fields.update(changes)
    return CurveCategory(**fields)


class ContainsTests(unittest.TestCase):
    def test_the_range_includes_the_minimum_and_excludes_the_maximum(self) -> None:
        category = _category(minimum=100.0, maximum=200.0)
        self.assertTrue(category.contains(100.0))
        self.assertTrue(category.contains(199.99))
        self.assertFalse(category.contains(200.0))
        self.assertFalse(category.contains(99.99))

    def test_a_negative_limit_is_open(self) -> None:
        self.assertTrue(_category(minimum=-1.0, maximum=100.0).contains(0.0))
        self.assertTrue(_category(minimum=500.0, maximum=-1.0).contains(1e9))
        self.assertTrue(_category(minimum=-1.0, maximum=-1.0).contains(-5.0))


class ValidationTests(unittest.TestCase):
    def test_rejects_an_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            _category(kind="X")

    def test_rejects_a_non_integer_class(self) -> None:
        with self.assertRaises(ValueError):
            _category(consumer_class=1.5)

    def test_rejects_a_power_factor_outside_zero_one(self) -> None:
        with self.assertRaises(ValueError):
            _category(power_factor=1.2)
        with self.assertRaises(ValueError):
            _category(power_factor=0.0)

    def test_rejects_non_finite_limits(self) -> None:
        with self.assertRaises(ValueError):
            _category(maximum=float("inf"))

    def test_rejects_an_empty_id(self) -> None:
        with self.assertRaises(ValueError):
            _category(category_id=" ")


class SortKeyTests(unittest.TestCase):
    def test_numeric_ids_sort_as_numbers_and_text_goes_last(self) -> None:
        categories = [_category(category_id=value) for value in ("10", "x", "9")]
        self.assertEqual(
            [item.category_id for item in sorted(categories, key=category_sort_key)],
            ["9", "10", "x"],
        )


if __name__ == "__main__":
    unittest.main()
