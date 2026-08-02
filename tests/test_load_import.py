from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.load_import import EXPECTED_LOAD_HEADER, load_loads_csv
from circuit_viewer.model import CircuitModel, UtmCrs


class LoadImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bars = CircuitModel(
            ["B1", "B2"],
            ["", ""],
            [500_000.0, 500_100.0],
            [8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )

    def write(self, text: str, *, encoding: str = "utf-8") -> Path:
        path = self.root / "cargas.csv"
        path.write_text(text, encoding=encoding)
        return path

    def test_imports_by_name_preserves_text_and_ignores_extras(self) -> None:
        path = self.write(
            "EXTRA;CODIGO;BARRA_ID;CARGA_ID;SNOM;SADM;VLINHASEC;FASES2;"
            "TIPO_LIG;EXTERN_ID\n"
            "x; COD-1 ;B2;L1;10,5;8;220;AN;Y;EXT-1\n"
            "x;;B2;L2;;;;;;\n"
        )

        result = load_loads_csv(path, self.bars)

        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 0)
        self.assertEqual(result.model.bar_indices.tolist(), [1, 1])
        record = result.model.record(0)
        self.assertEqual(record.load_id, "L1")
        self.assertEqual(record.bar_id, "B2")
        self.assertEqual(record.code, "COD-1")
        self.assertEqual(record.snom, "10,5")
        self.assertEqual(record.secondary_line_voltage, "220")
        self.assertEqual(record.connection_type, "Y")
        self.assertFalse(result.model.bar_indices.flags.writeable)
        self.assertEqual(result.model.spatial_index.nearest(500_100, 8_000_000, 0), 0)

    def test_reports_empty_duplicate_and_unknown_references(self) -> None:
        header = ";".join(EXPECTED_LOAD_HEADER)
        path = self.write(
            header
            + "\n;B1;;;;;;;\n"
            + "L1;B9;;A;;;;;\n"
            + "L2;B1;;B;;;;;\n"
            + "L2;B2;;C;;;;;\n"
        )

        result = load_loads_csv(path, self.bars)

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 3)
        self.assertIn("CARGA_ID vazio", result.issues[0].reason)
        self.assertIn("barra inexistente", result.issues[1].reason)
        self.assertIn("duplicado", result.issues[2].reason)

    def test_rejects_invalid_headers_and_files_without_valid_rows(self) -> None:
        missing = self.write("CARGA_ID;BARRA_ID\nL1;B1\n")
        with self.assertRaisesRegex(CsvImportError, "Cabeçalho inválido"):
            load_loads_csv(missing, self.bars)

        duplicate_header = list(EXPECTED_LOAD_HEADER) + ["CODIGO"]
        duplicated = self.write(";".join(duplicate_header) + "\n")
        with self.assertRaisesRegex(CsvImportError, "duplicadas: CODIGO"):
            load_loads_csv(duplicated, self.bars)

        header = ";".join(EXPECTED_LOAD_HEADER)
        invalid = self.write(header + "\nL1;B9;;;;;;;\n")
        with self.assertRaisesRegex(CsvImportError, "Nenhuma carga válida"):
            load_loads_csv(invalid, self.bars)

    def test_falls_back_to_cp1252_and_reports_progress(self) -> None:
        header = ";".join(EXPECTED_LOAD_HEADER)
        path = self.write(
            header + "\nL1;B1;;CARGA-AÇÃO;1;2;220;A;Y\n",
            encoding="cp1252",
        )
        progress: list[tuple[int, int, int]] = []

        result = load_loads_csv(path, self.bars, progress=lambda *args: progress.append(args))

        self.assertEqual(result.encoding, "cp1252")
        self.assertEqual(result.model.codes, ("CARGA-AÇÃO",))
        self.assertTrue(progress)
        self.assertEqual(progress[-1][1], progress[-1][2])

    def test_pre_cancelled_import_stops(self) -> None:
        header = ";".join(EXPECTED_LOAD_HEADER)
        path = self.write(header + "\nL1;B1;;;;;;;\n")
        cancelled = threading.Event()
        cancelled.set()

        with self.assertRaises(CsvImportCancelled):
            load_loads_csv(path, self.bars, cancel_event=cancelled)


if __name__ == "__main__":
    unittest.main()
