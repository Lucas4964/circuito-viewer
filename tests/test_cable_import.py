from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from circuit_viewer.cable_import import (
    EXPECTED_CABLE_HEADER,
    MAX_REPORTED_ISSUES,
    REQUIRED_CABLE_TYPE,
    load_cables_csv,
)
from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.model import CableModel


HEADER = ";".join(EXPECTED_CABLE_HEADER) + "\n"


def make_row(**values: str) -> str:
    """Monta uma linha de dados na ordem de EXPECTED_CABLE_HEADER.

    Campos não informados ficam vazios; TIPO por padrão é "1" (o único valor
    que sobrevive ao filtro de importação), então testes que não são sobre o
    filtro em si não precisam repeti-lo.
    """

    values.setdefault("TIPO", REQUIRED_CABLE_TYPE)
    return ";".join(values.get(name, "") for name in EXPECTED_CABLE_HEADER) + "\n"


class CableImportTests(unittest.TestCase):
    def _write(self, content: str, encoding: str = "utf-8") -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "cabos.csv"
        path.write_text(content, encoding=encoding)
        return path

    def test_imports_by_column_name_and_ignores_extra_columns(self) -> None:
        path = self._write(
            "EXTRA;NOME;CABO_ID;TIPO;CODIGO;IADM;GMR;R;X;QCAP;R0;X0;R1;X1;"
            "EXTERN_ID\n"
            "ignorar;Alumínio nu 4/0;C1;1;4/0;340;0,00824;0,367;0,42;1,2;"
            "0,5;1,1;0,367;0,42;EXT-1\n"
        )

        result = load_cables_csv(path)

        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 0)
        self.assertEqual(result.ignored_type_rows, 0)
        self.assertEqual(len(result.model), 1)
        record = result.model.record(0)
        self.assertEqual(record.cable_id, "C1")
        self.assertEqual(record.cable_type, "1")
        self.assertEqual(record.code, "4/0")
        self.assertEqual(record.name, "Alumínio nu 4/0")
        self.assertEqual(record.external_id, "EXT-1")
        # Valores numéricos permanecem texto, com a vírgula decimal intacta.
        self.assertEqual(record.iadm, "340")
        self.assertEqual(record.gmr, "0,00824")
        self.assertEqual(record.r, "0,367")
        self.assertEqual(record.x, "0,42")
        self.assertEqual(record.qcap, "1,2")
        self.assertEqual(record.r0, "0,5")
        self.assertEqual(record.x0, "1,1")
        self.assertEqual(record.r1, "0,367")
        self.assertEqual(record.x1, "0,42")

    def test_only_type_one_rows_are_kept(self) -> None:
        # O arquivo real traz um registro por TIPO para o mesmo CABO_ID; só o
        # TIPO=1 deve sobreviver, e o TIPO=2 nunca pode virar "CABO_ID
        # duplicado" porque a filtragem roda antes da checagem de duplicidade.
        path = self._write(
            HEADER
            + make_row(CABO_ID="C1", TIPO="2", NOME="não deve entrar")
            + make_row(CABO_ID="C1", TIPO="1", NOME="cabo válido")
            + make_row(CABO_ID="C2", TIPO="3")
            + make_row(CABO_ID="C3", TIPO="")
        )

        result = load_cables_csv(path)

        self.assertEqual(result.model.cable_ids, ("C1",))
        self.assertEqual(result.model.record(0).name, "cabo válido")
        self.assertEqual(result.valid_rows, 1)
        self.assertEqual(result.invalid_rows, 0)
        self.assertEqual(result.ignored_type_rows, 3)
        self.assertEqual(result.issues, ())

    def test_row_counts_reconcile_across_the_three_categories(self) -> None:
        path = self._write(
            HEADER
            + make_row(CABO_ID="C1", TIPO="1")  # válido
            + make_row(CABO_ID="C1", TIPO="1")  # duplicado -> inválido
            + make_row(CABO_ID="", TIPO="1")  # CABO_ID vazio -> inválido
            + make_row(CABO_ID="C2", TIPO="2")  # ignorado por tipo
            + make_row(CABO_ID="C3", TIPO="1")  # válido
        )

        result = load_cables_csv(path)

        self.assertEqual(result.total_rows, 5)
        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 2)
        self.assertEqual(result.ignored_type_rows, 1)
        self.assertEqual(
            result.total_rows,
            result.valid_rows + result.invalid_rows + result.ignored_type_rows,
        )
        self.assertEqual(result.model.cable_ids, ("C1", "C3"))

    def test_reports_empty_duplicate_ids_and_short_rows(self) -> None:
        path = self._write(
            HEADER
            + make_row(CABO_ID="C1")
            + make_row(CABO_ID="C1")
            + make_row(CABO_ID="", CODIGO="preenchido")
            + "C2;1\n"  # linha curta demais
            + "\n"  # linha vazia: ignorada sem contar
            + make_row(CABO_ID="C3")
        )

        result = load_cables_csv(path)

        self.assertEqual(result.total_rows, 5)
        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 3)
        self.assertEqual(result.ignored_type_rows, 0)
        self.assertEqual(result.model.cable_ids, ("C1", "C3"))
        reasons = [issue.reason for issue in result.issues]
        self.assertIn("CABO_ID duplicado: C1", reasons)
        self.assertIn("CABO_ID vazio", reasons)
        self.assertIn("faltam valores em colunas obrigatórias", reasons)

    def test_missing_or_duplicated_required_columns_are_fatal(self) -> None:
        without_column = self._write(
            "CABO_ID;TIPO;CODIGO;IADM;GMR;R;X;QCAP;R0;X0;R1;NOME;EXTERN_ID\n"
            "C1;;;;;;;;;;;;\n"
        )
        with self.assertRaises(CsvImportError) as missing:
            load_cables_csv(without_column)
        self.assertIn("X1", str(missing.exception))

        duplicated = self._write(
            "CABO_ID;TIPO;CODIGO;IADM;GMR;R;X;QCAP;R0;X0;R1;X1;NOME;EXTERN_ID;R\n"
            "C1;;;;;;;;;;;;;;\n"
        )
        with self.assertRaises(CsvImportError) as repeated:
            load_cables_csv(duplicated)
        self.assertIn("duplicadas", str(repeated.exception))

    def test_empty_file_and_no_valid_rows_fail(self) -> None:
        with self.assertRaises(CsvImportError):
            load_cables_csv(self._write(""))
        with self.assertRaises(CsvImportError):
            load_cables_csv(self._write(HEADER + make_row(CABO_ID="")))

    def test_no_valid_rows_when_every_row_is_filtered_by_type(self) -> None:
        path = self._write(HEADER + make_row(CABO_ID="C1", TIPO="2"))

        with self.assertRaises(CsvImportError):
            load_cables_csv(path)

    def test_missing_file_is_reported(self) -> None:
        with self.assertRaises(CsvImportError):
            load_cables_csv(Path(tempfile.gettempdir()) / "cabo_inexistente.csv")

    def test_falls_back_to_cp1252(self) -> None:
        path = self._write(
            HEADER + make_row(CABO_ID="C1", NOME="Alumínio"),
            encoding="cp1252",
        )

        result = load_cables_csv(path)

        self.assertEqual(result.encoding, "cp1252")
        self.assertTrue(result.has_warnings)
        self.assertEqual(result.model.record(0).name, "Alumínio")

    def test_cancellation_stops_the_import(self) -> None:
        path = self._write(HEADER + make_row(CABO_ID="C1"))
        cancel_event = threading.Event()
        cancel_event.set()

        with self.assertRaises(CsvImportCancelled):
            load_cables_csv(path, cancel_event=cancel_event)

    def test_issue_reporting_is_capped(self) -> None:
        rows = "".join(
            make_row() for _ in range(MAX_REPORTED_ISSUES + 25)
        )  # CABO_ID vazio em todas, TIPO=1 por padrão do helper
        path = self._write(HEADER + make_row(CABO_ID="C1") + rows)

        result = load_cables_csv(path)

        self.assertEqual(len(result.issues), MAX_REPORTED_ISSUES)
        self.assertEqual(result.omitted_issues, 25)

    def test_progress_reports_bytes(self) -> None:
        path = self._write(HEADER + make_row(CABO_ID="C1"))
        events: list[tuple[int, int, int]] = []

        load_cables_csv(path, progress=lambda *values: events.append(values))

        self.assertTrue(events)
        rows, current, total = events[-1]
        self.assertEqual(rows, 1)
        self.assertEqual(current, total)


class CableModelTests(unittest.TestCase):
    def _model(self) -> CableModel:
        return CableModel(
            ["C1", "C2"],
            ["1", "1"],
            ["4/0", "336"],
            ["340", "500"],
            *(["", ""] for _ in range(8)),
            ["Alumínio nu 4/0", "Alumínio com alma de aço"],
            ["EXT-1", "EXT-2"],
        )

    def test_lookup_by_id(self) -> None:
        model = self._model()

        self.assertEqual(len(model), 2)
        self.assertEqual(model.index_for_id("C2"), 1)
        self.assertIsNone(model.index_for_id("C9"))
        self.assertEqual(model.record_for_id("C2").code, "336")
        self.assertIsNone(model.record_for_id("C9"))

    def test_rejects_empty_duplicate_ids_and_ragged_columns(self) -> None:
        with self.assertRaises(ValueError):
            CableModel([""], *(["" ] for _ in range(13)))
        with self.assertRaises(ValueError):
            CableModel(["C1", "C1"], *(["", ""] for _ in range(13)))
        with self.assertRaises(ValueError):
            CableModel(["C1", "C2"], ["1"], *(["", ""] for _ in range(12)))
        with self.assertRaises(ValueError):
            CableModel([], *([] for _ in range(13)))

    def test_record_index_is_validated(self) -> None:
        model = self._model()

        with self.assertRaises(IndexError):
            model.record(2)


if __name__ == "__main__":
    unittest.main()
