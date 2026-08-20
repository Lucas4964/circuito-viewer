"""Modelo de carga configurável: potência constante ou ZIPV.

A fidelidade ao OpenDSS é o que estes testes travam. A definição oficial da
propriedade é: *"First 3 are ZIP weighting factors for real power (should sum to
1). Next 3 are ZIP weighting factors for reactive power (should sum to 1). Last 1
is cut-off voltage in p.u. of base kV"*, e o modelo correspondente é o 8.
"""

from __future__ import annotations

import unittest

from circuit_viewer.opendss_settings import (
    DEFAULT_OPENDSS_LOAD_MODEL,
    DEFAULT_OPENDSS_LOAD_SETTINGS,
    DEFAULT_ZIPV_COEFFICIENTS,
    OpenDssLoadModel,
    OpenDssLoadSettings,
    ZipvCoefficients,
    parse_opendss_load_model,
    settings_from_mapping,
    zipv_sum_error,
)


def zipv(**overrides) -> ZipvCoefficients:  # noqa: ANN003
    values = {
        "z_p": 0.5,
        "i_p": 0.2,
        "p_p": 0.3,
        "z_q": 0.4,
        "i_q": 0.3,
        "p_q": 0.3,
        "cutoff": 0.7,
    }
    values.update(overrides)
    return ZipvCoefficients(**values)


class LoadModelTests(unittest.TestCase):
    def test_the_dss_numbers_follow_the_opendss_manual(self) -> None:
        self.assertEqual(OpenDssLoadModel.CONSTANT_POWER.dss_model, 1)
        self.assertEqual(OpenDssLoadModel.ZIPV.dss_model, 8)
        self.assertIs(DEFAULT_OPENDSS_LOAD_MODEL, OpenDssLoadModel.CONSTANT_POWER)

    def test_a_corrupted_value_falls_back_to_the_default(self) -> None:
        self.assertIs(parse_opendss_load_model("zipv"), OpenDssLoadModel.ZIPV)
        for value in ("", None, "model=8", 42):
            with self.subTest(value=value):
                self.assertIs(
                    parse_opendss_load_model(value), DEFAULT_OPENDSS_LOAD_MODEL
                )


class ZipvCoefficientsTests(unittest.TestCase):
    def test_the_vector_order_matches_the_opendss_definition(self) -> None:
        """Três pesos da ativa, três da reativa, e o corte por último."""

        self.assertEqual(
            zipv().as_tuple(), (0.5, 0.2, 0.3, 0.4, 0.3, 0.3, 0.7)
        )
        self.assertEqual(
            zipv().as_dss_vector(), "[0.5, 0.2, 0.3, 0.4, 0.3, 0.3, 0.7]"
        )

    def test_the_factory_default_is_pure_constant_power(self) -> None:
        """Trocar para ZIPV sem editar nada não muda o resultado físico."""

        self.assertEqual(
            DEFAULT_ZIPV_COEFFICIENTS.as_tuple(), (0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0)
        )
        self.assertIsNone(zipv_sum_error(DEFAULT_ZIPV_COEFFICIENTS))
        # Corte zero desliga o mecanismo: o Load.pas só o aplica quando > 0.
        self.assertEqual(DEFAULT_ZIPV_COEFFICIENTS.cutoff, 0.0)

    def test_a_cutoff_outside_the_range_is_refused(self) -> None:
        for value in (-0.1, 1.5):
            with self.subTest(cutoff=value):
                with self.assertRaisesRegex(ValueError, "corte"):
                    zipv(cutoff=value)

    def test_a_non_finite_coefficient_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "finito"):
            zipv(z_p=float("nan"))

    def test_the_sums_are_reported_and_checked_separately(self) -> None:
        self.assertIsNone(zipv_sum_error(zipv()))

        error = zipv_sum_error(zipv(p_p=0.9))
        self.assertIsNotNone(error)
        self.assertIn("ativa", error)
        self.assertNotIn("reativa", error)

        both = zipv_sum_error(zipv(p_p=0.9, p_q=0.9))
        self.assertIn("ativa", both)
        self.assertIn("reativa", both)

    def test_the_sum_is_not_an_invariant_of_the_dataclass(self) -> None:
        """O diálogo reconstrói o valor a cada tecla; levantar quebraria o preview."""

        coefficients = zipv(p_p=0.9)

        self.assertAlmostEqual(coefficients.active_sum, 1.6)
        self.assertIsNotNone(zipv_sum_error(coefficients))


class LoadModelDirectiveTests(unittest.TestCase):
    def test_constant_power_emits_exactly_what_it_always_did(self) -> None:
        self.assertEqual(
            DEFAULT_OPENDSS_LOAD_SETTINGS.load_model_directive(), "model=1"
        )
        self.assertTrue(DEFAULT_OPENDSS_LOAD_SETTINGS.is_default)

    def test_zipv_emits_model_eight_with_the_vector(self) -> None:
        settings = OpenDssLoadSettings(
            load_model=OpenDssLoadModel.ZIPV, zipv=zipv()
        )

        self.assertEqual(
            settings.load_model_directive(),
            "model=8 ZIPV=[0.5, 0.2, 0.3, 0.4, 0.3, 0.3, 0.7]",
        )

    def test_zipv_alone_makes_the_settings_non_default(self) -> None:
        """O arquivo muda mesmo sem os limites de tensão ligados."""

        settings = OpenDssLoadSettings(load_model=OpenDssLoadModel.ZIPV)

        self.assertFalse(settings.is_default)
        self.assertEqual(settings.batch_edit_commands(), ())

    def test_the_decimal_separator_is_never_the_locale_one(self) -> None:
        settings = OpenDssLoadSettings(
            load_model=OpenDssLoadModel.ZIPV,
            zipv=zipv(z_p=0.3333, i_p=0.3333, p_p=0.3334),
        )

        self.assertNotIn(",", settings.load_model_directive().replace(", ", ""))
        self.assertIn("0.3333", settings.load_model_directive())


class PersistenceTests(unittest.TestCase):
    def test_the_round_trip_preserves_the_seven_values(self) -> None:
        settings = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.8,
            vmaxpu=1.2,
            load_model=OpenDssLoadModel.ZIPV,
            zipv=zipv(),
        )

        self.assertEqual(settings_from_mapping(settings.as_mapping()), settings)

    def test_a_mapping_without_the_new_keys_reads_as_constant_power(self) -> None:
        """Preferência gravada por uma versão anterior continua válida."""

        legacy = {
            "voltage_limits_enabled": "1",
            "vminpu": "0.8",
            "vmaxpu": "1.2",
        }

        restored = settings_from_mapping(legacy)

        self.assertIs(restored.load_model, OpenDssLoadModel.CONSTANT_POWER)
        self.assertEqual(restored.zipv, DEFAULT_ZIPV_COEFFICIENTS)
        self.assertEqual(restored.vminpu, 0.8)

    def test_an_incoherent_stored_vector_is_discarded(self) -> None:
        """Exportá-lo mudaria a potência de todo o circuito em silêncio."""

        values = OpenDssLoadSettings(
            load_model=OpenDssLoadModel.ZIPV, zipv=zipv()
        ).as_mapping()
        values["zipv_p_p"] = "0.9"

        restored = settings_from_mapping(values)

        self.assertIs(restored.load_model, OpenDssLoadModel.CONSTANT_POWER)
        self.assertEqual(restored.zipv, DEFAULT_ZIPV_COEFFICIENTS)

    def test_a_non_numeric_coefficient_falls_back_to_its_default(self) -> None:
        values = DEFAULT_OPENDSS_LOAD_SETTINGS.as_mapping()
        values["zipv_cutoff"] = "abacaxi"

        self.assertEqual(
            settings_from_mapping(values).zipv, DEFAULT_ZIPV_COEFFICIENTS
        )

    def test_a_comma_decimal_is_accepted(self) -> None:
        values = OpenDssLoadSettings(
            load_model=OpenDssLoadModel.ZIPV, zipv=zipv()
        ).as_mapping()
        values["zipv_cutoff"] = "0,65"

        self.assertAlmostEqual(settings_from_mapping(values).zipv.cutoff, 0.65)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
