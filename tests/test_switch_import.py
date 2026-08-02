from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.model import CircuitModel, LineNetworkModel, UtmCrs
from circuit_viewer.switch_import import load_switches_csv


def make_segments() -> LineNetworkModel:
    bars = CircuitModel(
        ["B1", "B2", "B3"],
        ["", "", ""],
        [0.0, 10.0, 20.0],
        [0.0, 0.0, 0.0],
        UtmCrs(21, northern=False),
    )
    return LineNetworkModel(
        bars,
        ["T1", "T2"],
        ["", ""],
        ["ABC", "ABC"],
        [0, 1],
        [1, 2],
        ["", ""],
        ["", ""],
        ["", ""],
        [10.0, 10.0],
    )


class SwitchImportTests(unittest.TestCase):
    def _write(self, content: str, encoding: str = "utf-8") -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "chaves.csv"
        path.write_text(content, encoding=encoding)
        return path

    def test_imports_by_column_name_and_ignores_extra_columns(self) -> None:
        path = self._write(
            "EXTRA;TRECHO_ID;CHAVE_ID;CODIGO;TIPOCHV_ID;CIRC_ID;ESTADO;"
            "ESTADO_NORMAL;CORN;ELO;ELO_TIPO\n"
            "ignorar;T2;CH1;C-1;TC;CIR-1;A;F;N;E-1;FUSIVEL\n"
        )

        result = load_switches_csv(path, make_segments())

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 0)
        record = result.model.record_for_segment_id("T2")
        self.assertIsNotNone(record)
        self.assertEqual(record.switch_id, "CH1")
        self.assertEqual(record.switch_type_id, "TC")
        self.assertEqual(record.circuit_id, "CIR-1")
        self.assertEqual(record.code, "C-1")
        self.assertEqual(record.state, "A")
        self.assertEqual(record.normal_state, "F")
        self.assertEqual(record.corn, "N")
        self.assertEqual(record.elo, "E-1")
        self.assertEqual(record.elo_type, "FUSIVEL")

    def test_reports_missing_segments_and_duplicate_ids_or_associations(self) -> None:
        header = (
            "CHAVE_ID;TIPOCHV_ID;CIRC_ID;TRECHO_ID;CODIGO;ESTADO;"
            "ESTADO_NORMAL;CORN;ELO;ELO_TIPO\n"
        )
        path = self._write(
            header
            + "CH1;;;T1;;;;;;\n"
            + "CH1;;;T2;;;;;;\n"
            + "CH2;;;T1;;;;;;\n"
            + "CH3;;;INEXISTENTE;;;;;;\n"
            + ";;;T2;;;;;;\n"
            + "CH4;;;T2;;;;;;\n"
        )

        result = load_switches_csv(path, make_segments())

        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 4)
        self.assertEqual(result.model.record_for_segment_id("T1").switch_id, "CH1")
        self.assertEqual(result.model.record_for_segment_id("T2").switch_id, "CH4")
        reasons = "\n".join(issue.reason for issue in result.issues)
        self.assertIn("CHAVE_ID duplicado", reasons)
        self.assertIn("mais de uma chave", reasons)
        self.assertIn("trecho inexistente", reasons)
        self.assertIn("CHAVE_ID vazio", reasons)

    def test_falls_back_to_cp1252(self) -> None:
        path = self._write(
            "CHAVE_ID;TIPOCHV_ID;CIRC_ID;TRECHO_ID;CODIGO;ESTADO;"
            "ESTADO_NORMAL;CORN;ELO;ELO_TIPO\n"
            "CH1;;;T1;AÇÃO;;;;;\n",
            encoding="cp1252",
        )

        result = load_switches_csv(path, make_segments())

        self.assertEqual(result.encoding, "cp1252")
        self.assertEqual(result.model.record(0).code, "AÇÃO")

    def test_rejects_missing_header_and_files_without_valid_rows(self) -> None:
        missing_header = self._write("CHAVE_ID;TRECHO_ID\nCH1;T1\n")
        with self.assertRaisesRegex(CsvImportError, "ausentes"):
            load_switches_csv(missing_header, make_segments())

        invalid = self._write(
            "CHAVE_ID;TIPOCHV_ID;CIRC_ID;TRECHO_ID;CODIGO;ESTADO;"
            "ESTADO_NORMAL;CORN;ELO;ELO_TIPO\n"
            "CH1;;;INEXISTENTE;;;;;;\n"
        )
        with self.assertRaisesRegex(CsvImportError, "Nenhuma chave válida"):
            load_switches_csv(invalid, make_segments())

    def test_pre_cancelled_import_stops(self) -> None:
        path = self._write(
            "CHAVE_ID;TIPOCHV_ID;CIRC_ID;TRECHO_ID;CODIGO;ESTADO;"
            "ESTADO_NORMAL;CORN;ELO;ELO_TIPO\n"
            "CH1;;;T1;;;;;;\n"
        )
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(CsvImportCancelled):
            load_switches_csv(path, make_segments(), cancel_event=cancelled)


if __name__ == "__main__":
    unittest.main()
