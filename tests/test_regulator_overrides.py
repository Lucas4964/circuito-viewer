"""Edições voláteis de regulador: sobrepõem o MDB sem nunca gravá-lo."""

from __future__ import annotations

import unittest

from circuit_viewer.model import (
    CircuitModel,
    LineNetworkModel,
    RegulatorModel,
    UtmCrs,
)
from circuit_viewer.regulator_overrides import (
    EDITABLE_FIELDS,
    RegulatorOverrides,
    apply_overrides,
)


def make_network() -> LineNetworkModel:
    bars = CircuitModel(
        ["B0", "B1", "B2"],
        ["COD-A", "COD-B", "COD-C"],
        [0.0, 100.0, 200.0],
        [0.0, 0.0, 0.0],
        UtmCrs(21, northern=False),
    )
    return LineNetworkModel(
        bars,
        ["T0", "T1"],
        ["TR-1", "TR-2"],
        ["13", "13"],
        [0, 1],
        [1, 2],
        ["", ""],
        ["CB1", "CB1"],
        ["", ""],
        [250.0, 400.0],
    )


def make_regulators(network: LineNetworkModel) -> RegulatorModel:
    """Dois reguladores com o cadastro zerado, como nos MDBs reais."""

    return RegulatorModel(
        network,
        ["RG1", "RG2"],
        [0, 1],
        ["EXT-1", "EXT-2"],
        ["REG-01", "REG-02"],
        ["Y", "Y"],
        ["0", "276"],
        ["0", "0"],
        ["32", "32"],
        ["0", "0"],
        ["200", "100"],
        ["0", "0"],
    )


class RegulatorOverridesTests(unittest.TestCase):
    def test_only_fields_consumed_by_the_export_are_editable(self) -> None:
        overrides = RegulatorOverrides()

        self.assertEqual(EDITABLE_FIELDS, ("vnom", "snom"))
        for field in EDITABLE_FIELDS:
            self.assertTrue(overrides.set("RG1", field, "1"))
        for field in ("faixa", "tap", "npassos", "codigo"):
            with self.assertRaises(ValueError):
                overrides.set("RG1", field, "1")

    def test_setting_the_same_value_twice_reports_no_change(self) -> None:
        overrides = RegulatorOverrides()

        self.assertTrue(overrides.set("RG1", "snom", "276"))
        self.assertFalse(overrides.set("RG1", "snom", "276"))
        self.assertFalse(overrides.set("RG1", "snom", "  276  "))

    def test_clearing_removes_the_regulator_when_the_last_field_goes(self) -> None:
        overrides = RegulatorOverrides()
        overrides.set("RG1", "snom", "276")
        overrides.set("RG1", "vnom", "13,8")

        self.assertTrue(overrides.clear("RG1", "snom"))
        self.assertFalse(overrides.is_empty)
        self.assertTrue(overrides.clear("RG1", "vnom"))
        self.assertTrue(overrides.is_empty)
        self.assertFalse(overrides.clear("RG1", "vnom"))

    def test_retain_drops_regulators_absent_from_the_model(self) -> None:
        overrides = RegulatorOverrides()
        overrides.set("RG1", "snom", "276")
        overrides.set("RG9", "snom", "111")

        self.assertTrue(overrides.retain(["RG1", "RG2"]))

        self.assertEqual(overrides.fields_for("RG1"), {"snom": "276"})
        self.assertEqual(overrides.fields_for("RG9"), {})

    # --------------------------------------------------------------- aplicação

    def test_no_override_returns_the_very_same_model(self) -> None:
        """A identidade importa: reconstruir invalidaria o fluxo de potência."""

        model = make_regulators(make_network())

        self.assertIs(apply_overrides(model, RegulatorOverrides()), model)
        self.assertIsNone(apply_overrides(None, RegulatorOverrides()))

    def test_an_override_equal_to_the_registry_returns_the_same_model(self) -> None:
        model = make_regulators(make_network())
        overrides = RegulatorOverrides()
        overrides.set("RG2", "snom", "276")

        self.assertIs(apply_overrides(model, overrides), model)

    def test_the_effective_model_carries_the_typed_values(self) -> None:
        model = make_regulators(make_network())
        overrides = RegulatorOverrides()
        overrides.set("RG1", "snom", "276")
        overrides.set("RG1", "vnom", "13,8")

        effective = apply_overrides(model, overrides)

        self.assertIsNot(effective, model)
        record = effective.record(0)
        self.assertEqual(record.snom, "276")
        self.assertEqual(record.vnom, "13,8")
        # O outro regulador e as demais colunas ficam intactos.
        self.assertEqual(effective.record(1).snom, "276")
        self.assertEqual(effective.record(0).inom, "200")
        self.assertEqual(effective.record(0).code, "REG-01")
        self.assertEqual(effective.segments, model.segments)

    def test_the_imported_model_is_never_mutated(self) -> None:
        """O MDB é retrato somente leitura: a edição não pode vazar para ele."""

        model = make_regulators(make_network())
        overrides = RegulatorOverrides()
        overrides.set("RG1", "snom", "276")

        apply_overrides(model, overrides)

        self.assertEqual(model.snom_values, ("0", "276"))
        self.assertEqual(model.vnom_values, ("0", "0"))
        self.assertEqual(model.record(0).snom, "0")

    def test_an_override_for_an_unknown_regulator_is_ignored(self) -> None:
        model = make_regulators(make_network())
        overrides = RegulatorOverrides()
        overrides.set("INEXISTENTE", "snom", "999")

        self.assertIs(apply_overrides(model, overrides), model)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
