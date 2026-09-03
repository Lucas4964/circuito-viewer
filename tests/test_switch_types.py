from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from circuit_viewer.switch_types import (
    SwitchTypeConfiguration,
    SwitchTypeConfigurationError,
    SwitchTypeEntry,
    default_switch_type_path,
    load_switch_types,
    normalize_type_code,
)


class ConfigFileMixin:
    """Escreve um ``tipos_chave.json`` temporário para o teste corrente."""

    def write(self, payload: object) -> Path:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "tipos_chave.json"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        path.write_text(text, encoding="utf-8")
        return path


class ShippedFileTests(unittest.TestCase):
    """O arquivo que acompanha o programa precisa cobrir o cadastro real."""

    def test_the_shipped_file_loads(self) -> None:
        configuration = load_switch_types()

        self.assertTrue(default_switch_type_path().is_file())
        self.assertGreater(len(configuration.entries), 0)

    def test_a_fuse_is_not_switchable_and_a_knife_switch_is(self) -> None:
        # A regra que originou o campo, afirmada sobre o arquivo entregue.
        configuration = load_switch_types()

        self.assertEqual(configuration.switchable_text("CHFUSIVEL"), "0")
        self.assertEqual(configuration.switchable_text("CFT"), "0")
        self.assertEqual(configuration.switchable_text("CFUSIVEL"), "0")
        self.assertEqual(configuration.switchable_text("CF"), "1")
        self.assertEqual(configuration.switchable_text("DJ"), "1")

    def test_permanent_connections_are_not_switchable(self) -> None:
        # Jumper e Fly Tap não são fusível, mas também não são dispositivo de
        # manobra: é justamente o que a coluna ELO do banco não distingue, e a
        # razão de esta decisão morar num arquivo.
        configuration = load_switch_types()

        self.assertEqual(configuration.switchable_text("JUMPER"), "0")
        self.assertEqual(configuration.switchable_text("FLYTAP"), "0")


class LoadSwitchTypesTests(ConfigFileMixin, unittest.TestCase):
    def test_an_unknown_code_has_no_answer(self) -> None:
        # Vazio, não zero: a interface mostra traço em vez de afirmar que um
        # tipo não declarado é imanobrável.
        configuration = load_switch_types()

        self.assertIsNone(configuration.entry_for("NAO_EXISTE"))
        self.assertEqual(configuration.switchable_text("NAO_EXISTE"), "")

    def test_the_code_is_matched_without_case_or_padding(self) -> None:
        configuration = load_switch_types()

        self.assertEqual(configuration.switchable_text(" cf "), "1")
        self.assertEqual(normalize_type_code(" chFusivel "), "CHFUSIVEL")

    def test_a_missing_field_names_the_row(self) -> None:
        path = self.write([{"CODIGO": "CF"}])

        with self.assertRaisesRegex(SwitchTypeConfigurationError, "Relação 1"):
            load_switch_types(path)

    def test_manobravel_only_accepts_zero_or_one(self) -> None:
        for value in (2, -1, "1", None):
            with self.subTest(value=value):
                path = self.write([{"CODIGO": "CF", "MANOBRAVEL": value}])
                with self.assertRaisesRegex(
                    SwitchTypeConfigurationError, "MANOBRAVEL"
                ):
                    load_switch_types(path)

    def test_a_boolean_is_refused_even_though_python_calls_it_an_int(self) -> None:
        # bool é subclasse de int: sem a checagem o arquivo teria duas grafias
        # para a mesma coisa, e nenhuma delas documentada.
        path = self.write([{"CODIGO": "CF", "MANOBRAVEL": True}])

        with self.assertRaisesRegex(SwitchTypeConfigurationError, "MANOBRAVEL"):
            load_switch_types(path)

    def test_a_duplicated_code_points_at_the_first_one(self) -> None:
        path = self.write(
            [
                {"CODIGO": "CF", "MANOBRAVEL": 1},
                {"CODIGO": "cf", "MANOBRAVEL": 0},
            ]
        )

        with self.assertRaisesRegex(SwitchTypeConfigurationError, "relação 1"):
            load_switch_types(path)

    def test_an_empty_list_is_refused(self) -> None:
        with self.assertRaisesRegex(SwitchTypeConfigurationError, "lista não vazia"):
            load_switch_types(self.write([]))

    def test_broken_json_reports_where(self) -> None:
        with self.assertRaisesRegex(SwitchTypeConfigurationError, "linha"):
            load_switch_types(self.write("[{"))

    def test_a_missing_file_is_readable_in_the_message(self) -> None:
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "ausente.json"
            with self.assertRaisesRegex(
                SwitchTypeConfigurationError, "Não foi possível ler"
            ):
                load_switch_types(missing)


class SwitchTypeConfigurationTests(unittest.TestCase):
    def test_duplicated_codes_are_refused_by_the_invariant(self) -> None:
        entry = SwitchTypeEntry("CF", "Chave Faca", True)

        with self.assertRaises(ValueError):
            SwitchTypeConfiguration((entry, entry))


if __name__ == "__main__":
    unittest.main()
