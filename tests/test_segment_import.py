from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.model import CircuitModel, UtmCrs
from circuit_viewer.segment_import import load_segments_csv


class SegmentImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.bars = CircuitModel(
            ["B1", "B2", "B3"],
            ["", "", ""],
            [500_000.0, 500_100.0, 500_200.0],
            [8_000_000.0, 8_000_100.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )

    def _write(self, content: str, *, encoding: str = "utf-8") -> Path:
        path = self.root / "trechos.csv"
        path.write_bytes(content.encode(encoding))
        return path

    def test_imports_by_column_name_and_ignores_extra_columns(self) -> None:
        path = self._write(
            "EXTRA;BARRA2_ID;TRECHO_ID;COMPR;CODIGO;BARRA1_ID;"
            "FASES2;ARRANJO_ID;CABOF_ID;CABON_ID;OUTRA\n"
            "x;B2;T1;100,5;C1;B1;ABC;A1;CF1;CN1;y\n"
        )

        result = load_segments_csv(path, self.bars)

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 0)
        record = result.model.record(0)
        self.assertEqual(record.segment_id, "T1")
        self.assertEqual(record.start_bar_id, "B1")
        self.assertEqual(record.end_bar_id, "B2")
        self.assertEqual(record.phases, "ABC")
        self.assertAlmostEqual(record.length, 100.5)

    def test_reports_missing_bars_duplicates_and_invalid_length(self) -> None:
        header = "TRECHO_ID;CODIGO;FASES2;BARRA1_ID;BARRA2_ID;ARRANJO_ID;CABOF_ID;CABON_ID;COMPR\n"
        path = self._write(
            header
            + "T1;C1;ABC;B1;B2;A;CF;CN;10\n"
            + "T1;duplicado;ABC;B2;B3;A;CF;CN;10\n"
            + "T2;sem-barra;ABC;B1;BX;A;CF;CN;10\n"
            + "T3;compr-ruim;ABC;B1;B2;A;CF;CN;-1\n"
            + "T4;sem-compr;ABC;B2;B3;A;CF;CN;\n"
        )

        result = load_segments_csv(path, self.bars)

        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 3)
        self.assertEqual(result.model.segment_ids, ("T1", "T4"))
        self.assertIsNone(result.model.record(1).length)
        reasons = "\n".join(issue.reason for issue in result.issues)
        self.assertIn("duplicado", reasons)
        self.assertIn("BX", reasons)
        self.assertIn("não negativo", reasons)

    def test_rejects_missing_required_column(self) -> None:
        path = self._write(
            "TRECHO_ID;CODIGO;FASES2;BARRA1_ID;BARRA2_ID;ARRANJO_ID;CABOF_ID;COMPR\n"
        )
        with self.assertRaisesRegex(CsvImportError, "CABON_ID"):
            load_segments_csv(path, self.bars)

    def test_falls_back_to_cp1252(self) -> None:
        header = "TRECHO_ID;CODIGO;FASES2;BARRA1_ID;BARRA2_ID;ARRANJO_ID;CABOF_ID;CABON_ID;COMPR\n"
        path = self._write(
            header + "T1;AÇÃO;ABC;B1;B2;A;CF;CN;10\n",
            encoding="cp1252",
        )
        result = load_segments_csv(path, self.bars)
        self.assertEqual(result.encoding, "cp1252")
        self.assertEqual(result.model.record(0).code, "AÇÃO")

    def test_pre_cancelled_import_stops(self) -> None:
        header = "TRECHO_ID;CODIGO;FASES2;BARRA1_ID;BARRA2_ID;ARRANJO_ID;CABOF_ID;CABON_ID;COMPR\n"
        path = self._write(header + "T1;C1;ABC;B1;B2;A;CF;CN;10\n")
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CsvImportCancelled):
            load_segments_csv(path, self.bars, cancel_event=cancel)


if __name__ == "__main__":
    unittest.main()

