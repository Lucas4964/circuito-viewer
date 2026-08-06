from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.model import (
    CircuitModel,
    LineNetworkModel,
    RegulatorModel,
    UtmCrs,
)
from circuit_viewer.regulator_import import load_regulators_csv


HEADER = (
    "REGU_ID;TRECHO_ID;EXTERN_ID;CODIGO;LIGACAO;SNOM;FAIXA;NPASSOS;TAP;"
    "INOM;VNOM\n"
)


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


class RegulatorImportTests(unittest.TestCase):
    def _write(self, content: str, encoding: str = "utf-8") -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "reguladores.csv"
        path.write_text(content, encoding=encoding)
        return path

    def test_imports_by_column_name_and_ignores_extra_columns(self) -> None:
        path = self._write(
            "EXTRA;VNOM;INOM;TAP;NPASSOS;FAIXA;SNOM;LIGACAO;CODIGO;EXTERN_ID;"
            "TRECHO_ID;REGU_ID\n"
            "ignorar;13800;100;3;32;10;1000;Y;REG-01;EXT-9;T2;RG1\n"
        )

        result = load_regulators_csv(path, make_segments())

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 0)
        record = result.model.record_for_segment_id("T2")
        self.assertIsNotNone(record)
        self.assertEqual(record.regulator_id, "RG1")
        self.assertEqual(record.segment_id, "T2")
        self.assertEqual(record.external_id, "EXT-9")
        self.assertEqual(record.code, "REG-01")
        self.assertEqual(record.connection, "Y")
        self.assertEqual(record.snom, "1000")
        self.assertEqual(record.regulation_range, "10")
        self.assertEqual(record.step_count, "32")
        self.assertEqual(record.tap, "3")
        self.assertEqual(record.inom, "100")
        self.assertEqual(record.vnom, "13800")

    def test_numeric_columns_stay_as_text(self) -> None:
        # A regra do projeto: converter só onde há consumidor numérico. Zeros à
        # esquerda e vírgula decimal chegam intactos ao painel.
        path = self._write(HEADER + "RG1;T1;;;;0500;7,5;016;-2;0100;13,8\n")

        record = load_regulators_csv(path, make_segments()).model.record(0)

        self.assertEqual(record.snom, "0500")
        self.assertEqual(record.regulation_range, "7,5")
        self.assertEqual(record.step_count, "016")
        self.assertEqual(record.tap, "-2")
        self.assertEqual(record.vnom, "13,8")

    def test_reports_missing_segments_and_duplicate_ids_or_associations(self) -> None:
        path = self._write(
            HEADER
            + "RG1;T1;;;;;;;;;\n"
            + "RG1;T2;;;;;;;;;\n"
            + "RG2;T1;;;;;;;;;\n"
            + "RG3;INEXISTENTE;;;;;;;;;\n"
            + ";T2;;;;;;;;;\n"
            + "RG4;T2;;;;;;;;;\n"
        )

        result = load_regulators_csv(path, make_segments())

        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 4)
        self.assertEqual(
            result.model.record_for_segment_id("T1").regulator_id, "RG1"
        )
        self.assertEqual(
            result.model.record_for_segment_id("T2").regulator_id, "RG4"
        )
        reasons = "\n".join(issue.reason for issue in result.issues)
        self.assertIn("REGU_ID duplicado", reasons)
        self.assertIn("mais de um regulador", reasons)
        self.assertIn("trecho inexistente", reasons)
        self.assertIn("REGU_ID vazio", reasons)

    def test_short_rows_are_reported(self) -> None:
        # Acompanhada de uma linha válida: sem nenhuma, o importador falharia
        # antes, e o que se testa aqui é o diagnóstico da linha curta.
        path = self._write(HEADER + "RG1;T1\n" + "RG2;T2;;;;;;;;;\n")

        result = load_regulators_csv(path, make_segments())

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 1)
        self.assertIn("colunas obrigatórias", result.issues[0].reason)

    def test_blank_lines_are_skipped_without_counting(self) -> None:
        path = self._write(HEADER + "RG1;T1;;;;;;;;;\n\n;;;;;;;;;;\n")

        result = load_regulators_csv(path, make_segments())

        self.assertEqual(result.total_rows, 1)
        self.assertEqual(result.valid_rows, 1)

    def test_falls_back_to_cp1252(self) -> None:
        path = self._write(
            HEADER + "RG1;T1;;REGULAÇÃO;;;;;;;\n",
            encoding="cp1252",
        )

        result = load_regulators_csv(path, make_segments())

        self.assertEqual(result.encoding, "cp1252")
        self.assertEqual(result.model.record(0).code, "REGULAÇÃO")
        self.assertTrue(result.has_warnings)

    def test_rejects_missing_and_duplicated_columns(self) -> None:
        missing = self._write("REGU_ID;TRECHO_ID\nRG1;T1\n")
        with self.assertRaisesRegex(CsvImportError, "ausentes"):
            load_regulators_csv(missing, make_segments())

        duplicated = self._write(
            "REGU_ID;REGU_ID;TRECHO_ID;EXTERN_ID;CODIGO;LIGACAO;SNOM;FAIXA;"
            "NPASSOS;TAP;INOM;VNOM\n"
        )
        with self.assertRaisesRegex(CsvImportError, "duplicadas"):
            load_regulators_csv(duplicated, make_segments())

    def test_rejects_empty_files_and_files_without_valid_rows(self) -> None:
        empty = self._write("")
        with self.assertRaisesRegex(CsvImportError, "vazio"):
            load_regulators_csv(empty, make_segments())

        invalid = self._write(HEADER + "RG1;INEXISTENTE;;;;;;;;;\n")
        with self.assertRaisesRegex(CsvImportError, "Nenhum regulador válido"):
            load_regulators_csv(invalid, make_segments())

    def test_rejects_a_missing_file(self) -> None:
        with self.assertRaisesRegex(CsvImportError, "não encontrado"):
            load_regulators_csv("nao_existe.csv", make_segments())

    def test_pre_cancelled_import_stops(self) -> None:
        path = self._write(HEADER + "RG1;T1;;;;;;;;;\n")
        cancelled = threading.Event()
        cancelled.set()

        with self.assertRaises(CsvImportCancelled):
            load_regulators_csv(path, make_segments(), cancel_event=cancelled)

    def test_progress_ends_at_the_total(self) -> None:
        path = self._write(HEADER + "RG1;T1;;;;;;;;;\n")
        seen: list[tuple[int, int, int]] = []

        load_regulators_csv(
            path,
            make_segments(),
            progress=lambda rows, current, total: seen.append(
                (rows, current, total)
            ),
        )

        self.assertTrue(seen)
        rows, current, total = seen[-1]
        self.assertEqual(rows, 1)
        self.assertEqual(current, total)


class RegulatorModelTests(unittest.TestCase):
    """As invariantes valem no modelo, independentemente do importador."""

    def _model(self, **overrides):  # noqa: ANN003, ANN202
        segments = overrides.pop("segments", make_segments())
        values = {
            "regulator_ids": ["RG1"],
            "segment_indices": [0],
            "external_ids": [""],
            "codes": ["REG-01"],
            "connections": ["Y"],
            "snom_values": ["1000"],
            "regulation_ranges": ["10"],
            "step_counts": ["32"],
            "tap_values": ["0"],
            "inom_values": ["100"],
            "vnom_values": ["13800"],
        }
        values.update(overrides)
        return RegulatorModel(segments, **values)

    def test_rejects_an_empty_model(self) -> None:
        with self.assertRaisesRegex(ValueError, "ao menos um regulador"):
            self._model(
                regulator_ids=[],
                segment_indices=[],
                external_ids=[],
                codes=[],
                connections=[],
                snom_values=[],
                regulation_ranges=[],
                step_counts=[],
                tap_values=[],
                inom_values=[],
                vnom_values=[],
            )

    def test_rejects_an_empty_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "REGU_ID não pode ser vazio"):
            self._model(regulator_ids=[""])

    def test_rejects_duplicate_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "REGU_ID duplicado"):
            self._model(
                regulator_ids=["RG1", "RG1"],
                segment_indices=[0, 1],
                external_ids=["", ""],
                codes=["", ""],
                connections=["", ""],
                snom_values=["", ""],
                regulation_ranges=["", ""],
                step_counts=["", ""],
                tap_values=["", ""],
                inom_values=["", ""],
                vnom_values=["", ""],
            )

    def test_rejects_two_regulators_on_the_same_segment(self) -> None:
        with self.assertRaisesRegex(ValueError, "mais de um regulador"):
            self._model(
                regulator_ids=["RG1", "RG2"],
                segment_indices=[0, 0],
                external_ids=["", ""],
                codes=["", ""],
                connections=["", ""],
                snom_values=["", ""],
                regulation_ranges=["", ""],
                step_counts=["", ""],
                tap_values=["", ""],
                inom_values=["", ""],
                vnom_values=["", ""],
            )

    def test_rejects_an_unknown_segment(self) -> None:
        with self.assertRaisesRegex(ValueError, "trecho inexistente"):
            self._model(segment_indices=[7])

    def test_rejects_mismatched_column_lengths(self) -> None:
        with self.assertRaisesRegex(ValueError, "mesmo tamanho"):
            self._model(codes=["A", "B"])

    def test_lookup_by_segment_is_none_where_there_is_no_regulator(self) -> None:
        model = self._model()

        self.assertIsNotNone(model.record_for_segment(0))
        self.assertIsNone(model.record_for_segment(1))
        self.assertIsNone(model.record_for_segment_id("INEXISTENTE"))

    def test_record_indices_by_segment_marks_absence_with_minus_one(self) -> None:
        model = self._model()

        indices = model.record_indices_by_segment
        self.assertEqual(len(indices), len(model.segments))
        self.assertEqual(int(indices[0]), 0)
        self.assertEqual(int(indices[1]), -1)

    def test_arrays_are_read_only(self) -> None:
        model = self._model()

        with self.assertRaises(ValueError):
            model.segment_indices[0] = 1

    def test_index_for_id(self) -> None:
        model = self._model()

        self.assertEqual(model.index_for_id("RG1"), 0)
        self.assertIsNone(model.index_for_id("RG9"))

    def test_record_rejects_an_out_of_range_index(self) -> None:
        model = self._model()

        with self.assertRaises(IndexError):
            model.record(1)


if __name__ == "__main__":
    unittest.main()
