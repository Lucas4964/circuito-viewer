from __future__ import annotations

import unittest

from circuit_viewer.opendss_settings import (
    DEFAULT_OPENDSS_LOAD_SETTINGS,
    DEFAULT_VMAXPU,
    DEFAULT_VMINPU,
    OpenDssLoadSettings,
    settings_from_mapping,
)
from circuit_viewer.opendss_solution import (
    DEFAULT_MAX_POWER_FLOW_ITER,
    MAX_POWER_FLOW_ITER_RANGE,
    parse_max_power_flow_iterations,
)


class BatchEditCommandTests(unittest.TestCase):
    def test_disabled_emits_nothing(self) -> None:
        # É o que mantém o arquivo idêntico ao de quem nunca abriu o diálogo.
        self.assertEqual(DEFAULT_OPENDSS_LOAD_SETTINGS.batch_edit_commands(), ())
        self.assertTrue(DEFAULT_OPENDSS_LOAD_SETTINGS.is_default)

    def test_disabled_emits_nothing_even_with_custom_values(self) -> None:
        settings = OpenDssLoadSettings(
            voltage_limits_enabled=False,
            vminpu=0.8,
            vmaxpu=1.2,
        )

        self.assertEqual(settings.batch_edit_commands(), ())

    def test_enabled_emits_one_command_per_property(self) -> None:
        settings = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.8,
            vmaxpu=1.2,
        )

        self.assertEqual(
            settings.batch_edit_commands(),
            (
                "BatchEdit Load..* vminpu=0.8",
                "BatchEdit Load..* vmaxpu=1.2",
            ),
        )
        self.assertFalse(settings.is_default)

    def test_values_never_use_a_decimal_comma(self) -> None:
        # Sob locale pt-BR uma formatação descuidada geraria "0,875" e o
        # OpenDSS leria o comando errado.
        settings = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.875,
            vmaxpu=1.125,
        )

        for command in settings.batch_edit_commands():
            self.assertNotIn(",", command)
        self.assertEqual(
            settings.batch_edit_commands()[0],
            "BatchEdit Load..* vminpu=0.875",
        )

    def test_the_defaults_are_the_opendss_defaults(self) -> None:
        self.assertEqual(DEFAULT_VMINPU, 0.95)
        self.assertEqual(DEFAULT_VMAXPU, 1.05)
        self.assertEqual(DEFAULT_OPENDSS_LOAD_SETTINGS.vminpu, DEFAULT_VMINPU)
        self.assertEqual(DEFAULT_OPENDSS_LOAD_SETTINGS.vmaxpu, DEFAULT_VMAXPU)


class ValidationTests(unittest.TestCase):
    """O OpenDSS aceita valores absurdos em silêncio; a invariante é nossa."""

    def test_rejects_a_band_that_excludes_the_nominal_voltage(self) -> None:
        for vminpu, vmaxpu in ((1.1, 1.2), (0.7, 0.9)):
            with self.subTest(vminpu=vminpu, vmaxpu=vmaxpu):
                with self.assertRaises(ValueError):
                    OpenDssLoadSettings(vminpu=vminpu, vmaxpu=vmaxpu)

    def test_rejects_an_inverted_band(self) -> None:
        with self.assertRaises(ValueError):
            OpenDssLoadSettings(vminpu=1.2, vmaxpu=0.8)

    def test_rejects_non_positive_vminpu(self) -> None:
        for value in (0.0, -1.0):
            with self.subTest(vminpu=value):
                with self.assertRaises(ValueError):
                    OpenDssLoadSettings(vminpu=value)

    def test_rejects_non_finite_values(self) -> None:
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    OpenDssLoadSettings(vminpu=value)
                with self.assertRaises(ValueError):
                    OpenDssLoadSettings(vmaxpu=value)

    def test_accepts_the_band_touching_the_nominal_voltage(self) -> None:
        settings = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=1.0,
            vmaxpu=1.0,
        )

        self.assertEqual(
            settings.batch_edit_commands()[0],
            "BatchEdit Load..* vminpu=1",
        )

    def test_is_immutable(self) -> None:
        settings = OpenDssLoadSettings()

        with self.assertRaises(Exception):
            settings.vminpu = 0.8  # type: ignore[misc]


class MappingTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        settings = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.82,
            vmaxpu=1.18,
        )

        self.assertEqual(settings_from_mapping(settings.as_mapping()), settings)

    def test_round_trip_of_the_default(self) -> None:
        self.assertEqual(
            settings_from_mapping(DEFAULT_OPENDSS_LOAD_SETTINGS.as_mapping()),
            DEFAULT_OPENDSS_LOAD_SETTINGS,
        )

    def test_missing_keys_fall_back_to_the_default(self) -> None:
        self.assertEqual(settings_from_mapping({}), DEFAULT_OPENDSS_LOAD_SETTINGS)

    def test_corrupted_values_fall_back_without_raising(self) -> None:
        corrupted = (
            {"voltage_limits_enabled": "1", "vminpu": "abc", "vmaxpu": "xyz"},
            {"voltage_limits_enabled": "1", "vminpu": "1,2", "vmaxpu": "0,8"},
            {"voltage_limits_enabled": "talvez", "vminpu": "", "vmaxpu": ""},
        )
        for values in corrupted:
            with self.subTest(values=values):
                result = settings_from_mapping(values)

                self.assertEqual(result.vminpu, DEFAULT_VMINPU)
                self.assertEqual(result.vmaxpu, DEFAULT_VMAXPU)

    def test_an_incoherent_stored_band_falls_back_entirely(self) -> None:
        # Gravado por uma versão anterior, ou editado à mão no registro.
        result = settings_from_mapping(
            {"voltage_limits_enabled": "1", "vminpu": "1.3", "vmaxpu": "1.4"}
        )

        self.assertEqual(result, DEFAULT_OPENDSS_LOAD_SETTINGS)

    def test_accepts_a_decimal_comma_when_reading(self) -> None:
        # parse_number aceita os dois separadores; só a escrita é padronizada.
        result = settings_from_mapping(
            {"voltage_limits_enabled": "1", "vminpu": "0,8", "vmaxpu": "1,2"}
        )

        self.assertTrue(result.voltage_limits_enabled)
        self.assertAlmostEqual(result.vminpu, 0.8)
        self.assertAlmostEqual(result.vmaxpu, 1.2)


class MaxPowerFlowIterationsTests(unittest.TestCase):
    """O teto de iterações é preferência, então o parser nunca pode levantar."""

    def test_accepts_a_value_inside_the_range(self) -> None:
        low, high = MAX_POWER_FLOW_ITER_RANGE

        self.assertEqual(parse_max_power_flow_iterations(200), 200)
        self.assertEqual(parse_max_power_flow_iterations(" 750 "), 750)
        self.assertEqual(parse_max_power_flow_iterations(low), low)
        self.assertEqual(parse_max_power_flow_iterations(high), high)

    def test_missing_or_unreadable_falls_back_to_the_default(self) -> None:
        for value in (None, "", "muitas", "1e3", 12.5, object()):
            with self.subTest(value=value):
                self.assertEqual(
                    parse_max_power_flow_iterations(value),
                    DEFAULT_MAX_POWER_FLOW_ITER,
                )

    def test_outside_the_range_falls_back_to_the_default(self) -> None:
        low, high = MAX_POWER_FLOW_ITER_RANGE
        # Abaixo do piso a configuração só pioraria o padrão do próprio OpenDSS.
        for value in (0, -1, low - 1, high + 1):
            with self.subTest(value=value):
                self.assertEqual(
                    parse_max_power_flow_iterations(value),
                    DEFAULT_MAX_POWER_FLOW_ITER,
                )

    def test_the_default_is_generous_and_inside_its_own_range(self) -> None:
        low, high = MAX_POWER_FLOW_ITER_RANGE

        self.assertEqual(DEFAULT_MAX_POWER_FLOW_ITER, 500)
        self.assertLessEqual(low, DEFAULT_MAX_POWER_FLOW_ITER)
        self.assertLessEqual(DEFAULT_MAX_POWER_FLOW_ITER, high)
        # O piso é o padrão do próprio OpenDSS.
        self.assertEqual(low, 15)


if __name__ == "__main__":
    unittest.main()
