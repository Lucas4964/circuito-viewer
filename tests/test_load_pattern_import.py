from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.load_pattern_import import (
    EXPECTED_LOAD_PATTERN_HEADER,
    load_load_patterns_csv,
)
from circuit_viewer.model import (
    CircuitModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
    UtmCrs,
)


class LoadPatternImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        bars = CircuitModel(
            ["B1", "B2", "B3"],
            ["", "", ""],
            [0.0, 10.0, 20.0],
            [0.0, 0.0, 0.0],
            UtmCrs(21, northern=False),
        )
        self.loads = LoadModel(
            bars,
            ["L1", "L2", "L3"],
            [0, 1, 2],
            ["", "", ""],
            ["", "", ""],
            ["", "", ""],
            ["", "", ""],
            ["", "", ""],
            ["", "", ""],
            ["", "", ""],
        )

    def write(self, text: str, *, encoding: str = "utf-8") -> Path:
        path = self.root / "patamares.csv"
        path.write_text(text, encoding=encoding)
        return path

    @staticmethod
    def row(load_id: str, npat: str, value: str = "1.25") -> str:
        return f"{load_id};{npat};{value};;;;;\n"

    def test_imports_complete_groups_sorted_and_preserves_text(self) -> None:
        path = self.write(
            "EXTRA;QF;CARGA_ID;NPAT;PD;PE;PF;QD;QE\n"
            "x;;L2;3;3,75;;;;\n"
            "x;0;L2;1; 1.234567890123456789 ;;;;;\n"
            "x;;L2;0;;;;;;\n"
            "x;;L2;2;2;;;;\n"
        )
        progress: list[tuple[int, int, int]] = []

        result = load_load_patterns_csv(
            path,
            self.loads,
            progress=lambda *args: progress.append(args),
        )

        self.assertEqual(len(result.model), 1)
        self.assertEqual(result.model.record_count, 4)
        self.assertEqual(result.valid_rows, 4)
        self.assertEqual(result.invalid_rows, 0)
        self.assertEqual(result.model.records_for_load(0), ())
        records = result.model.records_for_load(1)
        self.assertEqual([record.npat for record in records], [0, 1, 2, 3])
        self.assertEqual(records[0].pd, "")
        self.assertEqual(records[1].pd, "1.234567890123456789")
        self.assertEqual(records[3].pd, "3,75")
        self.assertEqual(progress[-1][1], progress[-1][2])

    def test_discards_incomplete_duplicate_and_invalid_groups_atomically(self) -> None:
        header = ";".join(EXPECTED_LOAD_PATTERN_HEADER) + "\n"
        text = header
        for npat in range(4):
            text += self.row("L1", str(npat))
        for npat in range(3):
            text += self.row("L2", str(npat))
        for npat in range(4):
            text += self.row("L3", str(npat))
        text += self.row("L3", "2", "duplicado")
        text += self.row("DESCONHECIDA", "0")
        path = self.write(text)

        result = load_load_patterns_csv(path, self.loads)

        self.assertEqual(result.valid_rows, 4)
        self.assertEqual(result.invalid_rows, 9)
        self.assertEqual(result.model.records_for_load(1), ())
        self.assertEqual(result.model.records_for_load(2), ())
        reasons = "\n".join(issue.reason for issue in result.issues)
        self.assertIn("grupo incompleto", reasons)
        self.assertIn("NPAT duplicado", reasons)
        self.assertIn("carga inexistente", reasons)

    def test_npat_out_of_range_invalidates_even_an_otherwise_complete_group(self) -> None:
        header = ";".join(EXPECTED_LOAD_PATTERN_HEADER) + "\n"
        text = header
        for npat in range(4):
            text += self.row("L1", str(npat))
        for npat in range(4):
            text += self.row("L2", str(npat))
        text += self.row("L2", "4")
        path = self.write(text)

        result = load_load_patterns_csv(path, self.loads)

        self.assertEqual(len(result.model), 1)
        self.assertEqual(result.invalid_rows, 5)
        self.assertIn("NPAT inválido", result.issues[0].reason)

    def test_rejects_headers_and_files_without_complete_groups(self) -> None:
        missing = self.write("CARGA_ID;NPAT\nL1;0\n")
        with self.assertRaisesRegex(CsvImportError, "Cabeçalho inválido"):
            load_load_patterns_csv(missing, self.loads)

        duplicated_header = list(EXPECTED_LOAD_PATTERN_HEADER) + ["PD"]
        duplicated = self.write(";".join(duplicated_header) + "\n")
        with self.assertRaisesRegex(CsvImportError, "duplicadas: PD"):
            load_load_patterns_csv(duplicated, self.loads)

        header = ";".join(EXPECTED_LOAD_PATTERN_HEADER) + "\n"
        incomplete = self.write(header + self.row("L1", "0"))
        with self.assertRaisesRegex(CsvImportError, "Nenhum grupo completo"):
            load_load_patterns_csv(incomplete, self.loads)

    def test_cp1252_cancellation_and_public_model_validation(self) -> None:
        accented_loads = LoadModel(
            self.loads.bars,
            ["AÇÃO"],
            [0],
            [""],
            [""],
            [""],
            [""],
            [""],
            [""],
            [""],
        )
        header = ";".join(EXPECTED_LOAD_PATTERN_HEADER) + "\n"
        text = header + "".join(self.row("AÇÃO", str(npat)) for npat in range(4))
        path = self.write(text, encoding="cp1252")

        result = load_load_patterns_csv(path, accented_loads)

        self.assertEqual(result.encoding, "cp1252")
        self.assertEqual(result.model.records_for_load(0)[0].load_id, "AÇÃO")

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(CsvImportCancelled):
            load_load_patterns_csv(path, accented_loads, cancel_event=cancelled)

        record = LoadPatternRecord("AÇÃO", 0, "", "", "", "", "", "")
        with self.assertRaises(FrozenInstanceError):
            record.pd = "alterado"  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "exatamente os patamares"):
            LoadPatternModel(accented_loads, [(record,)])
        with self.assertRaisesRegex(ValueError, "NPAT"):
            LoadPatternRecord("AÇÃO", 4, "", "", "", "", "", "")


if __name__ == "__main__":
    unittest.main()
