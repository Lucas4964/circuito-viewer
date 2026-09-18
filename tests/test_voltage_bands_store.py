from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from circuit_viewer.voltage_bands import (
    ReservedColors,
    VoltageBand,
    VoltageBandTable,
    default_voltage_band_table,
)
from circuit_viewer.voltage_bands_store import (
    VOLTAGE_BANDS_FILE_VERSION,
    default_voltage_bands_path,
    load_voltage_bands,
    save_voltage_bands,
)


class VoltageBandsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "faixas_tensao.json"

    def test_a_missing_file_loads_the_defaults_without_complaining(self) -> None:
        result = load_voltage_bands(self.path)
        self.assertEqual(result.table, default_voltage_band_table())
        self.assertIsNone(result.issue)

    def test_a_round_trip_preserves_the_table(self) -> None:
        original = default_voltage_band_table()
        save_voltage_bands(original, self.path)
        result = load_voltage_bands(self.path)
        self.assertIsNone(result.issue)
        self.assertEqual(result.table, original)

    def test_infinity_is_written_as_null_and_read_back(self) -> None:
        """``Infinity`` não é JSON padrão; outro leitor recusaria o arquivo."""

        save_voltage_bands(default_voltage_band_table(), self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("Infinity", text)
        payload = json.loads(text)
        highest = max(payload["faixas"], key=lambda entry: entry["minimo_pu"])
        self.assertIsNone(highest["maximo_pu"])
        table = load_voltage_bands(self.path).table
        self.assertTrue(
            any(math.isinf(band.maximum_pu) for band in table.bands)
        )

    def test_the_custom_colors_survive(self) -> None:
        original = VoltageBandTable(
            (
                VoltageBand("Boa", "#123456", 0.9, 1.1, 0),
                VoltageBand("Baixa", "#ABCDEF", 0.0, 0.9, 2),
                VoltageBand("Alta", "#FEDCBA", 1.1, math.inf, 2),
            ),
            ReservedColors(
                no_line_voltage="#010203",
                de_energized="#040506",
                no_data="#070809",
            ),
        )
        save_voltage_bands(original, self.path)
        table = load_voltage_bands(self.path).table
        self.assertEqual(table, original)
        self.assertEqual(table.reserved.no_line_voltage, "#010203")

    def test_a_broken_json_falls_back_with_a_readable_reason(self) -> None:
        self.path.write_text("{ isso não é json", encoding="utf-8")
        result = load_voltage_bands(self.path)
        self.assertEqual(result.table, default_voltage_band_table())
        self.assertIsNotNone(result.issue)
        self.assertIn("faixas_tensao.json", result.issue)

    def test_a_table_with_a_gap_falls_back_instead_of_raising(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "faixas": [
                        {
                            "nome": "Baixa",
                            "cor": "#C62828",
                            "minimo_pu": 0.0,
                            "maximo_pu": 0.9,
                            "gravidade": 2,
                        },
                        {
                            "nome": "Alta",
                            "cor": "#2E7D32",
                            "minimo_pu": 0.95,
                            "maximo_pu": None,
                            "gravidade": 0,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = load_voltage_bands(self.path)
        self.assertEqual(result.table, default_voltage_band_table())
        self.assertIn("lacuna", result.issue)

    def test_an_invalid_color_falls_back_with_the_band_name(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "faixas": [
                        {
                            "nome": "Tudo",
                            "cor": "vermelho",
                            "minimo_pu": 0.0,
                            "maximo_pu": None,
                            "gravidade": 0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = load_voltage_bands(self.path)
        self.assertIn("Tudo", result.issue)

    def test_a_future_version_is_read_with_a_note(self) -> None:
        save_voltage_bands(default_voltage_band_table(), self.path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["version"] = VOLTAGE_BANDS_FILE_VERSION + 1
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        result = load_voltage_bands(self.path)
        self.assertEqual(result.table, default_voltage_band_table())
        self.assertIn("versão mais nova", result.issue)

    def test_missing_reserved_colors_fall_back_to_the_defaults(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "faixas": [
                        {
                            "nome": "Tudo",
                            "cor": "#2E7D32",
                            "minimo_pu": 0.0,
                            "maximo_pu": None,
                            "gravidade": 0,
                        }
                    ],
                    "reservadas": {"sem_tensao": "#111111"},
                }
            ),
            encoding="utf-8",
        )
        table = load_voltage_bands(self.path).table
        self.assertEqual(table.reserved.de_energized, "#111111")
        self.assertEqual(table.reserved.no_line_voltage, "#000000")

    def test_saving_leaves_no_temporary_file_behind(self) -> None:
        save_voltage_bands(default_voltage_band_table(), self.path)
        leftovers = [
            item.name
            for item in self.path.parent.iterdir()
            if item.name != self.path.name
        ]
        self.assertEqual(leftovers, [])

    def test_saving_rejects_anything_that_is_not_a_table(self) -> None:
        with self.assertRaises(TypeError):
            save_voltage_bands({"faixas": []}, self.path)

    def test_the_default_path_lives_beside_the_other_user_data(self) -> None:
        path = default_voltage_bands_path()
        self.assertEqual(path.name, "faixas_tensao.json")
        self.assertEqual(path.parent.name, "dados")


if __name__ == "__main__":
    unittest.main()
