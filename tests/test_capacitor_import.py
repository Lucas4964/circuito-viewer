"""Importação de bancos de capacitores e paridade CSV/MDB."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from circuit_viewer.capacitor_import import (
    EXPECTED_CAPACITOR_HEADER,
    load_capacitors_csv,
    parse_capacitor_rows,
)
from circuit_viewer.csv_import import CsvImportError
from circuit_viewer.model import CircuitModel, UtmCrs


HEADER = ";".join(EXPECTED_CAPACITOR_HEADER)


class CapacitorImportTests(unittest.TestCase):
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

    def write(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.root / name
        path.write_text(text, encoding=encoding)
        return path

    def test_a_valid_row_binds_the_bank_to_its_bar(self) -> None:
        path = self.write(
            "cap.csv",
            f"{HEADER}\n239;B2;34559653;CAP-1;13.8;600;600;600;600;DEFN;0\n",
        )

        result = load_capacitors_csv(path, self.bars)

        self.assertEqual(result.total_rows, 1)
        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 0)
        self.assertFalse(result.has_warnings)
        record = result.model.record(0)
        self.assertEqual(record.capacitor_id, "239")
        self.assertEqual(record.bar_id, "B2")
        self.assertEqual(record.code, "CAP-1")
        self.assertEqual(record.nominal_voltage, "13.8")
        self.assertEqual(record.phases, "DEFN")
        self.assertEqual(record.reactive_powers, ("600", "600", "600", "600"))
        self.assertEqual(tuple(result.model.bar_indices), (1,))

    def test_every_kind_of_invalid_row_is_reported(self) -> None:
        path = self.write(
            "cap.csv",
            f"{HEADER}\n"
            "239;B1;;CAP-1;13.8;600;600;600;600;DEFN;0\n"
            ";B1;;CAP-2;13.8;600;600;600;600;DEFN;0\n"
            "239;B1;;CAP-3;13.8;600;600;600;600;DEFN;0\n"
            "240;B9;;CAP-4;13.8;600;600;600;600;DEFN;0\n"
            "241;;;CAP-5;13.8;600;600;600;600;DEFN;0\n"
            "242;B1\n",
        )

        result = load_capacitors_csv(path, self.bars)

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 5)
        reasons = [issue.reason for issue in result.issues]
        self.assertIn("CAPAC_ID vazio", reasons)
        self.assertIn("CAPAC_ID duplicado: 239", reasons)
        self.assertIn("barra inexistente: B9", reasons)
        self.assertIn("barra inexistente: <vazio>", reasons)
        self.assertIn("faltam valores em colunas obrigatórias", reasons)

    def test_a_missing_column_rejects_the_whole_file(self) -> None:
        path = self.write("cap.csv", "CAPAC_ID;BARRA_ID\n239;B1\n")

        with self.assertRaisesRegex(CsvImportError, "Cabeçalho inválido"):
            load_capacitors_csv(path, self.bars)

    def test_a_file_without_valid_rows_is_refused(self) -> None:
        path = self.write(
            "cap.csv",
            f"{HEADER}\n;B1;;CAP-1;13.8;600;600;600;600;DEFN;0\n",
        )

        with self.assertRaisesRegex(CsvImportError, "Nenhum capacitor válido"):
            load_capacitors_csv(path, self.bars)

    def test_the_csv_and_the_database_produce_the_same_record(self) -> None:
        """O seam é ``parse_capacitor_rows``: as duas fontes só entregam texto."""

        path = self.write(
            "cap.csv",
            f"{HEADER}\n239;B2;34559653;CAP-1;13.8;600;600;600;600;DEFN;0\n",
        )
        from_csv = load_capacitors_csv(path, self.bars)

        from_database = parse_capacitor_rows(
            EXPECTED_CAPACITOR_HEADER,
            [("239", "B2", "34559653", "CAP-1", "13.8", "600", "600", "600",
              "600", "DEFN", "0")],
            self.bars,
            source_label="CAPACITOR",
            encoding="ODBC",
            first_line_number=1,
        )

        self.assertEqual(from_csv.model.record(0), from_database.model.record(0))
        self.assertFalse(from_database.has_warnings)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
