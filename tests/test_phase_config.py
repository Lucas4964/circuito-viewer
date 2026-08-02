from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from circuit_viewer.phase_config import (
    PHASE_COLORS,
    UNMAPPED_PHASE_COLOR,
    PhaseConfigurationError,
    default_phase_configuration_path,
    load_phase_configuration,
)


class PhaseConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def write_json(self, payload) -> Path:  # noqa: ANN001
        path = self.root / "fases2.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_default_configuration_and_exact_colors(self) -> None:
        path = default_phase_configuration_path()
        config = load_phase_configuration()

        self.assertTrue(path.is_file())
        self.assertEqual(PHASE_COLORS, ("#0000FF", "#00FF00", "#FF0000"))
        self.assertEqual(UNMAPPED_PHASE_COLOR, "#555555")
        self.assertEqual(
            [(entry.fases2, entry.name, entry.phase_count) for entry in config.entries],
            [
                ("1", "D", 1),
                ("2", "E", 1),
                ("3", "F", 1),
                ("9", "DF", 2),
                ("13", "DEF", 3),
            ],
        )

    def test_accepts_numeric_values_normalizes_text_and_ignores_extras(self) -> None:
        path = self.write_json(
            [
                {
                    "FASES2": 1.0,
                    "NOME": " D ",
                    "NUMERO_FASES": 1,
                    "EXTRA": "ignorado",
                },
                {"FASES2": " Ab ", "NUMERO_FASES": 2},
            ]
        )

        config = load_phase_configuration(path)

        self.assertEqual(config.entries[0].fases2, "1")
        self.assertEqual(config.entries[0].name, "D")
        self.assertEqual(config.entries[1].fases2, "ab")
        with self.assertRaises(TypeError):
            config.phase_count_by_value["novo"] = 1  # type: ignore[index]

    def test_classifies_once_and_reports_distinct_unmapped_values(self) -> None:
        config = load_phase_configuration(
            self.write_json(
                [
                    {"FASES2": "A", "NUMERO_FASES": 1},
                    {"FASES2": "AB", "NUMERO_FASES": 2},
                    {"FASES2": "ABC", "NUMERO_FASES": 3},
                ]
            )
        )

        result = config.classify([" a ", "AB", "abc", "X", " x ", ""])

        np.testing.assert_array_equal(result.style_indices, [0, 1, 2, -1, -1, -1])
        self.assertFalse(result.style_indices.flags.writeable)
        self.assertEqual(result.unmapped_count, 3)
        self.assertEqual(result.unmapped_values, ("<vazio>", "X"))

    def test_missing_and_malformed_files_raise_readable_errors(self) -> None:
        with self.assertRaisesRegex(PhaseConfigurationError, "Não foi possível ler"):
            load_phase_configuration(self.root / "ausente.json")

        malformed = self.root / "malformado.json"
        malformed.write_text("[{", encoding="utf-8")
        with self.assertRaisesRegex(PhaseConfigurationError, "linha 1, coluna"):
            load_phase_configuration(malformed)

        invalid_encoding = self.root / "invalido.json"
        invalid_encoding.write_bytes(b"[\x96]")
        with self.assertRaisesRegex(PhaseConfigurationError, "UTF-8"):
            load_phase_configuration(invalid_encoding)

    def test_rejects_invalid_shapes_fields_duplicates_and_types(self) -> None:
        invalid_payloads = (
            ([], "lista não vazia"),
            ({}, "lista não vazia"),
            (["x"], "objeto JSON"),
            ([{"FASES2": "1"}], "obrigatórios"),
            ([{"FASES2": "", "NUMERO_FASES": 1}], "não pode ser vazio"),
            (
                [
                    {"FASES2": " A ", "NUMERO_FASES": 1},
                    {"FASES2": "a", "NUMERO_FASES": 2},
                ],
                "duplicado",
            ),
            ([{"FASES2": True, "NUMERO_FASES": 1}], "texto ou número"),
            ([{"FASES2": "1", "NUMERO_FASES": True}], "inteiro entre 1 e 3"),
            ([{"FASES2": "1", "NUMERO_FASES": 4}], "inteiro entre 1 e 3"),
            (
                [{"FASES2": "1", "NUMERO_FASES": 1, "NOME": 123}],
                "NOME deve ser texto",
            ),
        )
        for payload, message in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(PhaseConfigurationError, message):
                    load_phase_configuration(self.write_json(payload))


if __name__ == "__main__":
    unittest.main()
