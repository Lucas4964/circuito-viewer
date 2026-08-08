from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.generator_import import (
    CONSUMER_HEADER,
    GENERATOR_HEADER,
    load_generators_csv,
    parse_generator_rows,
)
from circuit_viewer.model import CircuitModel, LoadModel, UtmCrs


class GeneratorImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        bars = CircuitModel(
            ["B1", "B2"],
            ["", ""],
            [500_000.0, 500_100.0],
            [8_000_000.0, 8_000_000.0],
            UtmCrs(21, northern=False),
        )
        self.loads = LoadModel(
            bars,
            ["L1", "L2"],
            [0, 1],
            ["", ""],
            ["LOAD-1", "LOAD-2"],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
        )

    def write(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.root / name
        path.write_text(text, encoding=encoding)
        return path

    def test_associates_by_code_and_resolves_bar_through_load(self) -> None:
        generators = self.write(
            "MT_GERADOR_CONS.csv",
            "EXTRA;CODIGO;GERADOR_ID;MT_CONS_ID;VNOM;SNOM;LIGACAO;CURVA_ID;GERACAO_KWH\n"
            "x; COD-2 ;G1;IGNORADO;13,8;75;Y;CUR-1;1000,5\n",
        )
        consumers = self.write(
            "MT_CONS.csv",
            "NOME;CODIGO;ID;CARGA_ID;EXTERN_ID;FASES2\n"
            "Usina;COD-2;MC-2;L2;EXT-2;ABC\n",
        )

        result = load_generators_csv(generators, consumers, self.loads)

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.model.load_indices.tolist(), [1])
        self.assertEqual(result.model.bar_indices.tolist(), [1])
        self.assertFalse(result.model.load_indices.flags.writeable)
        record = result.model.record(0)
        self.assertEqual(record.generator_id, "G1")
        self.assertEqual(record.mt_cons_id, "IGNORADO")
        self.assertEqual(record.consumer_id, "MC-2")
        self.assertEqual(record.load_id, "L2")
        self.assertEqual(record.bar_id, "B2")
        self.assertEqual(record.generation_kwh, "1000,5")

    def test_skips_duplicate_ids_ambiguous_codes_and_unknown_loads(self) -> None:
        generators = self.write(
            "geradores.csv",
            ";".join(GENERATOR_HEADER)
            + "\nG1;;OK;;;;;\nG1;;OK;;;;;\nG2;;DUP;;;;;\nG3;;MISS;;;;;\nG4;;BADLOAD;;;;;\n",
        )
        consumers = self.write(
            "consumidores.csv",
            ";".join(CONSUMER_HEADER)
            + "\nC1;L1;OK;;;\nC2;L1;DUP;;;\nC3;L2;DUP;;;\nC4;L9;BADLOAD;;;\n",
        )

        result = load_generators_csv(generators, consumers, self.loads)

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 4)
        reasons = "\n".join(issue.reason for issue in result.issues)
        self.assertIn("GERADOR_ID duplicado", reasons)
        self.assertIn("CODIGO ambíguo", reasons)
        self.assertIn("CODIGO inexistente", reasons)
        self.assertIn("carga inexistente", reasons)

    def test_validates_both_headers_and_requires_a_valid_generator(self) -> None:
        valid_generator = self.write(
            "geradores.csv", ";".join(GENERATOR_HEADER) + "\nG1;;X;;;;;\n"
        )
        invalid_consumer = self.write("cons.csv", "ID;CARGA_ID\nC1;L1\n")
        with self.assertRaisesRegex(CsvImportError, "Cabeçalho inválido"):
            load_generators_csv(valid_generator, invalid_consumer, self.loads)

        valid_consumer = self.write(
            "cons.csv", ";".join(CONSUMER_HEADER) + "\nC1;L1;Y;;;\n"
        )
        with self.assertRaisesRegex(CsvImportError, "Nenhum gerador válido"):
            load_generators_csv(valid_generator, valid_consumer, self.loads)

    def test_independent_cp1252_fallback_progress_and_cancellation(self) -> None:
        generators = self.write(
            "geradores.csv",
            ";".join(GENERATOR_HEADER) + "\nG1;;AÇÃO;;;;;\n",
            "cp1252",
        )
        consumers = self.write(
            "cons.csv",
            ";".join(CONSUMER_HEADER) + "\nC1;L1;AÇÃO;;;Geração\n",
            "cp1252",
        )
        progress: list[tuple[int, int, int]] = []
        stages: list[str] = []
        result = load_generators_csv(
            generators,
            consumers,
            self.loads,
            progress=lambda *args: progress.append(args),
            stage=stages.append,
        )
        self.assertEqual(result.generator_encoding, "cp1252")
        self.assertEqual(result.consumer_encoding, "cp1252")
        self.assertEqual(stages, ["MT_CONS", "MT_GERADOR_CONS"])
        self.assertEqual(progress[-1][1], progress[-1][2])

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(CsvImportCancelled):
            load_generators_csv(
                generators, consumers, self.loads, cancel_event=cancelled
            )

    def test_source_independent_parser_has_csv_parity(self) -> None:
        generators = self.write(
            "geradores.csv",
            ";".join(GENERATOR_HEADER) + "\nG1;MC1;COD;13.8;75;Y;CUR;1000\n",
        )
        consumers = self.write(
            "cons.csv",
            ";".join(CONSUMER_HEADER) + "\nMC1;L2;COD;EXT;Usina;ABC\n",
        )
        csv_result = load_generators_csv(generators, consumers, self.loads)
        rows_result = parse_generator_rows(
            GENERATOR_HEADER,
            [("G1", "MC1", "COD", "13.8", "75", "Y", "CUR", "1000")],
            CONSUMER_HEADER,
            [("MC1", "L2", "COD", "EXT", "Usina", "ABC")],
            self.loads,
            generator_source_label="banco::MT_GERADOR_CONS",
            consumer_source_label="banco::MT_CONS",
            generator_encoding="ODBC",
            consumer_encoding="ODBC",
            generator_first_line_number=1,
            consumer_first_line_number=1,
        )
        self.assertEqual(csv_result.model.record(0), rows_result.model.record(0))
        self.assertEqual(csv_result.model.bar_indices.tolist(), [1])
        self.assertEqual(rows_result.model.bar_indices.tolist(), [1])
        self.assertIs(rows_result.model.loads, self.loads)


if __name__ == "__main__":
    unittest.main()
