from __future__ import annotations

import itertools
import math
import re
import unittest

from circuit_viewer.circuit_colors import (
    contrast_ratio_with_white,
    generate_circuit_palette,
    normalize_hex_color,
)


class CircuitPaletteTests(unittest.TestCase):
    def test_palette_is_unique_contrasting_and_visually_separated(self) -> None:
        palette = generate_circuit_palette(16, seed=2026)
        self.assertEqual(len(palette), 16)
        self.assertEqual(len(set(palette)), 16)
        for color in palette:
            self.assertRegex(color, re.compile(r"^#[0-9A-F]{6}$"))
            self.assertGreaterEqual(contrast_ratio_with_white(color), 3.0)

        def rgb(color: str) -> tuple[int, int, int]:
            return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))

        minimum_distance = min(
            math.dist(rgb(first), rgb(second))
            for first, second in itertools.combinations(palette, 2)
        )
        self.assertGreater(minimum_distance, 30.0)

    def test_seed_controls_random_phase_and_large_palette_does_not_repeat(self) -> None:
        first = generate_circuit_palette(80, seed=1)
        repeated = generate_circuit_palette(80, seed=1)
        second = generate_circuit_palette(80, seed=2)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertEqual(len(set(first)), len(first))

    def test_color_normalization_rejects_invalid_values(self) -> None:
        self.assertEqual(normalize_hex_color("#a1b2c3"), "#A1B2C3")
        with self.assertRaises(ValueError):
            normalize_hex_color("red")
        with self.assertRaises(ValueError):
            normalize_hex_color("#1234")


if __name__ == "__main__":
    unittest.main()
