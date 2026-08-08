from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from circuit_viewer.calculation_levels import (
    CalculationLevel,
    CalculationLevelSchedule,
    default_calculation_levels,
)
from circuit_viewer.calculation_levels_store import (
    CALCULATION_LEVELS_FILE_VERSION,
    default_calculation_levels_path,
    load_calculation_levels,
    save_calculation_levels,
)


class CalculationLevelsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "dados" / "patamares.json"

    def test_default_path_is_the_project_data_directory(self) -> None:
        path = default_calculation_levels_path()
        self.assertEqual(path.name, "patamares.json")
        self.assertEqual(path.parent.name, "dados")
        self.assertEqual(path.parent.parent.name, "circuit_viewer")

    def test_missing_file_returns_defaults_without_warning(self) -> None:
        result = load_calculation_levels(self.path)
        self.assertEqual(result.schedule, default_calculation_levels())
        self.assertIsNone(result.issue)
        self.assertFalse(self.path.exists())

    def test_round_trip_preserves_all_fields_and_utf8(self) -> None:
        schedule = default_calculation_levels()
        save_calculation_levels(schedule, self.path)
        result = load_calculation_levels(self.path)
        self.assertEqual(result.schedule, schedule)
        self.assertIsNone(result.issue)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Manhã", text)
        payload = json.loads(text)
        self.assertEqual(payload["version"], CALCULATION_LEVELS_FILE_VERSION)

    def test_creates_the_directory_overwrites_and_leaves_no_temp_file(self) -> None:
        first = default_calculation_levels()
        save_calculation_levels(first, self.path)
        levels = list(first.levels)
        levels[0] = CalculationLevel(0, "Madrugada nova", 22, 5, 23)
        second = CalculationLevelSchedule(tuple(levels))
        save_calculation_levels(second, self.path)
        self.assertEqual(load_calculation_levels(self.path).schedule, second)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_corrupted_json_falls_back_to_defaults_with_warning(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ isto não é json", encoding="utf-8")
        result = load_calculation_levels(self.path)
        self.assertEqual(result.schedule, default_calculation_levels())
        self.assertIn("valores padrão", result.issue)

    def test_semantically_invalid_file_falls_back_as_a_whole(self) -> None:
        self.path.parent.mkdir(parents=True)
        payload = {
            "version": 1,
            "patamares": [
                {"npat": 0, "nome": "Único", "horario_ini": 0, "horario_fim": 1, "horario_ref": 1}
            ],
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        result = load_calculation_levels(self.path)
        self.assertEqual(result.schedule, default_calculation_levels())
        self.assertIsNotNone(result.issue)

    def test_unknown_fields_are_ignored(self) -> None:
        save_calculation_levels(default_calculation_levels(), self.path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["patamares"][0]["futuro"] = "ignorar"
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(load_calculation_levels(self.path).issue)

    def test_newer_version_reads_known_fields_and_warns(self) -> None:
        save_calculation_levels(default_calculation_levels(), self.path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["version"] = CALCULATION_LEVELS_FILE_VERSION + 1
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        result = load_calculation_levels(self.path)
        self.assertEqual(result.schedule, default_calculation_levels())
        self.assertIn("versão mais nova", result.issue)

    def test_replace_failure_removes_the_temporary_file(self) -> None:
        with patch(
            "circuit_viewer.calculation_levels_store.os.replace",
            side_effect=OSError("bloqueado"),
        ):
            with self.assertRaises(OSError):
                save_calculation_levels(default_calculation_levels(), self.path)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertFalse(self.path.exists())

    def test_replace_failure_preserves_the_previous_complete_file(self) -> None:
        original = default_calculation_levels()
        save_calculation_levels(original, self.path)
        levels = list(original.levels)
        levels[0] = CalculationLevel(0, "Nova", 22, 5, 23)
        replacement = CalculationLevelSchedule(tuple(levels))
        with patch(
            "circuit_viewer.calculation_levels_store.os.replace",
            side_effect=OSError("bloqueado"),
        ):
            with self.assertRaises(OSError):
                save_calculation_levels(replacement, self.path)
        self.assertEqual(load_calculation_levels(self.path).schedule, original)


if __name__ == "__main__":
    unittest.main()
