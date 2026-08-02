from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from circuit_viewer.csv_import import (
    CsvImportCancelled,
    CsvImportError,
    load_csv,
)
from circuit_viewer.model import UtmCrs


class CsvImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.crs = UtmCrs(21, northern=False)

    def _write(self, content: str, *, encoding: str = "utf-8") -> Path:
        path = self.root / "barras.csv"
        path.write_bytes(content.encode(encoding))
        return path

    def test_accepts_bom_and_both_decimal_separators(self) -> None:
        path = self._write(
            "\ufeffBARRA_ID;CODIGO;X;Y\n"
            "B1;001;500000.25;8000000,50\n"
            "B2;002;500100,75;8000100.25\n"
        )

        result = load_csv(path, self.crs)

        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 0)
        self.assertAlmostEqual(result.model.record(0).y, 8_000_000.5)

    def test_invalid_and_duplicate_rows_are_reported(self) -> None:
        path = self._write(
            "BARRA_ID;CODIGO;X;Y\n"
            "B1;001;1;2\n"
            "B1;duplicada;3;4\n"
            ";sem-id;3;4\n"
            "B2;nan;NaN;4\n"
            "B3;mista;1.000,5;4\n"
            "B4;ok;5;6\n"
        )

        result = load_csv(path, self.crs)

        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 4)
        self.assertEqual(result.model.bar_ids, ("B1", "B4"))
        self.assertEqual([issue.line_number for issue in result.issues], [3, 4, 5, 6])

    def test_accepts_extra_columns_and_required_columns_in_any_order(self) -> None:
        path = self._write(
            "DESCRICAO;Y;BARRA_ID;CAMADA;X;CODIGO;IGNORAR\n"
            "Barra principal;8000000,5;B1;MT;500000.25;C-001;qualquer valor\n"
            "Outra barra;8000100;B2;BT;500100;C-002;outro valor\n"
        )

        result = load_csv(path, self.crs)

        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.model.bar_ids, ("B1", "B2"))
        self.assertEqual(result.model.record(0).code, "C-001")
        self.assertAlmostEqual(result.model.record(0).x, 500_000.25)
        self.assertAlmostEqual(result.model.record(0).y, 8_000_000.5)

    def test_rejects_duplicated_required_column(self) -> None:
        path = self._write("BARRA_ID;CODIGO;X;Y;X\nB1;C1;1;2;3\n")
        with self.assertRaisesRegex(CsvImportError, "duplicadas: X"):
            load_csv(path, self.crs)

    def test_falls_back_to_cp1252(self) -> None:
        path = self._write(
            "BARRA_ID;CODIGO;X;Y\nB1;AÇÃO;1;2\n",
            encoding="cp1252",
        )

        result = load_csv(path, self.crs)

        self.assertEqual(result.encoding, "cp1252")
        self.assertEqual(result.model.record(0).code, "AÇÃO")

    def test_rejects_wrong_header(self) -> None:
        path = self._write("ID;CODIGO;X;Y\nB1;C1;1;2\n")
        with self.assertRaisesRegex(CsvImportError, "Cabeçalho inválido"):
            load_csv(path, self.crs)

    def test_pre_cancelled_import_stops_without_model(self) -> None:
        path = self._write("BARRA_ID;CODIGO;X;Y\nB1;C1;1;2\n")
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CsvImportCancelled):
            load_csv(path, self.crs, cancel_event=cancel)


if __name__ == "__main__":
    unittest.main()
