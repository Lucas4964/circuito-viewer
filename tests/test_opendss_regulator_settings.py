from __future__ import annotations

import unittest

from circuit_viewer.opendss_regulator_settings import (
    DEFAULT_OPENDSS_REGULATOR_SETTINGS,
    DEFAULT_REGULATOR_BAND_PU,
    DEFAULT_REGULATOR_VREG_PU,
    OpenDssRegulatorSettings,
    effective_band_pu,
    is_valid_vreg,
    regulator_settings_from_mapping,
    regulator_step_pu,
)


class RegulatorSettingsTests(unittest.TestCase):
    def test_defaults_follow_the_interplan(self) -> None:
        # 1,02 pu é o VREG que o Interplan adota sem cadastro válido; a banda
        # padrão passa por pouco um degrau de 0,625 %.
        self.assertEqual(DEFAULT_OPENDSS_REGULATOR_SETTINGS.vreg_pu, 1.02)
        self.assertGreater(
            DEFAULT_OPENDSS_REGULATOR_SETTINGS.band_pu,
            regulator_step_pu(0.1, 32),
        )

    def test_invalid_values_are_refused(self) -> None:
        for kwargs in (
            {"vreg_pu": 0.4},
            {"vreg_pu": 1.6},
            {"vreg_pu": float("nan")},
            {"band_pu": 0.0},
            {"band_pu": 0.2},
            {"band_pu": True},
        ):
            with self.subTest(**{key: repr(value) for key, value in kwargs.items()}):
                with self.assertRaises(ValueError):
                    OpenDssRegulatorSettings(**kwargs)

    def test_mapping_round_trip(self) -> None:
        chosen = OpenDssRegulatorSettings(vreg_pu=1.0125, band_pu=0.0075)

        self.assertEqual(regulator_settings_from_mapping(chosen.as_mapping()), chosen)

    def test_corrupted_mapping_never_raises(self) -> None:
        result = regulator_settings_from_mapping(
            {"vreg_pu": "não", "band_pu": "-1"}
        )

        self.assertEqual(result.vreg_pu, DEFAULT_REGULATOR_VREG_PU)
        self.assertEqual(result.band_pu, DEFAULT_REGULATOR_BAND_PU)
        self.assertEqual(
            regulator_settings_from_mapping({}), DEFAULT_OPENDSS_REGULATOR_SETTINGS
        )

    def test_the_band_is_never_narrower_than_one_step(self) -> None:
        step = regulator_step_pu(0.1, 32)

        self.assertAlmostEqual(step, 0.00625)
        self.assertEqual(effective_band_pu(0.02, 0.1, 32), 0.02)
        self.assertAlmostEqual(effective_band_pu(0.001, 0.1, 32), 1.05 * step)
        # Menos passos, degrau maior: a mesma banda deixa de bastar.
        self.assertAlmostEqual(effective_band_pu(0.0068, 0.1, 16), 1.05 * 0.0125)

    def test_vreg_validity_is_the_interplan_rule(self) -> None:
        self.assertTrue(is_valid_vreg(0.5))
        self.assertTrue(is_valid_vreg(1.5))
        self.assertFalse(is_valid_vreg(0.0))
        self.assertFalse(is_valid_vreg(102.0))
        self.assertFalse(is_valid_vreg(None))


if __name__ == "__main__":
    unittest.main()
