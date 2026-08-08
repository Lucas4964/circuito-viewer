from __future__ import annotations

import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory

from circuit_viewer.calculation_levels import (
    CalculationLevel,
    CalculationLevelSchedule,
)
from circuit_viewer.circuit_calculation_levels import (
    CircuitCalculationLevelsController,
)
from circuit_viewer.circuit_level_import import (
    EXPECTED_CIRCUIT_LEVEL_HEADER,
    load_circuit_levels_csv,
    parse_circuit_level_rows,
)
from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    UtmCrs,
)


def make_catalog() -> CircuitCatalogModel:
    bars = CircuitModel(
        ["B1", "B2"],
        ["CB1", "CB2"],
        [500_000.0, 500_010.0],
        [8_000_000.0, 8_000_000.0],
        UtmCrs(21, False),
    )
    segments = LineNetworkModel(
        bars,
        ["T1"],
        ["TR1"],
        ["ABC"],
        [0],
        [1],
        [""],
        [""],
        [""],
        [10.0],
    )
    return CircuitCatalogModel.build(
        segments,
        None,
        [
            CircuitDefinition("2", "B1", "004001", "13.8"),
            CircuitDefinition("3", "B2", "004002", "13.8"),
        ],
    )


def rows(circuit_id: str, *, name_suffix: str = "") -> list[tuple[str, ...]]:
    return [
        (circuit_id, "0", f"Madrugada{name_suffix}", "22", "5", "23"),
        (circuit_id, "1", f"Manhã{name_suffix}", "5", "11", "11"),
        (circuit_id, "2", f"Tarde{name_suffix}", "11", "18", "12"),
        (circuit_id, "3", f"Noite{name_suffix}", "18", "22", "22"),
    ]


class CircuitLevelParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = make_catalog()

    def test_valid_groups_are_associated_exactly_and_extra_columns_are_ignored(self) -> None:
        header = ("PONTA", "NOME", "CIRC_ID", "HORARIO_REF", "NPAT", "HORARIO_FIM", "HORARIO_INI")
        data = []
        for source in rows("2") + rows("3", name_suffix=" 3"):
            circ_id, npat, name, start, end, reference = source
            data.append(("0", name, circ_id, reference, npat, end, start))
        result = parse_circuit_level_rows(
            header, data, self.catalog, source_label="mem", encoding="ODBC"
        )
        self.assertEqual((result.valid_rows, result.invalid_rows), (2, 0))
        self.assertIs(result.model.circuits, self.catalog)
        self.assertEqual(result.model.schedule_for_id("2").level(0).start_hour, 22)
        self.assertEqual(result.model.schedule_for_id("3").level(0).name, "Madrugada 3")

    def test_incomplete_unknown_and_duplicate_groups_are_omitted(self) -> None:
        invalid_duplicate = rows("3")
        invalid_duplicate[-1] = invalid_duplicate[0]
        result = parse_circuit_level_rows(
            EXPECTED_CIRCUIT_LEVEL_HEADER,
            rows("2") + invalid_duplicate + rows("999"),
            self.catalog,
            source_label="mem",
            encoding="ODBC",
        )
        self.assertEqual((result.valid_rows, result.invalid_rows), (1, 2))
        self.assertIsNone(result.model.schedule_for_id("3"))
        self.assertTrue(any("duplicado" in issue.reason for issue in result.issues))
        self.assertTrue(any("inexistente" in issue.reason for issue in result.issues))

    def test_no_valid_group_is_fatal(self) -> None:
        with self.assertRaises(CsvImportError):
            parse_circuit_level_rows(
                EXPECTED_CIRCUIT_LEVEL_HEADER,
                rows("2")[:3],
                self.catalog,
                source_label="mem",
                encoding="ODBC",
            )

    def test_duplicate_or_missing_required_header_is_fatal(self) -> None:
        with self.assertRaises(CsvImportError):
            parse_circuit_level_rows(
                ("CIRC_ID", "NPAT", "NPAT", "NOME", "HORARIO_INI", "HORARIO_FIM"),
                (),
                self.catalog,
                source_label="mem",
                encoding="ODBC",
            )

    def test_session_controller_does_not_mutate_imported_source(self) -> None:
        result = parse_circuit_level_rows(
            EXPECTED_CIRCUIT_LEVEL_HEADER,
            rows("2"),
            self.catalog,
            source_label="mem",
            encoding="ODBC",
        )
        controller = CircuitCalculationLevelsController(result.model)
        edited = CalculationLevelSchedule(
            (
                CalculationLevel(0, "A", 22, 5, 23),
                CalculationLevel(1, "B", 5, 11, 11),
                CalculationLevel(2, "C", 11, 18, 12),
                CalculationLevel(3, "D", 18, 22, 22),
            )
        )
        controller.set_schedule(0, edited)
        self.assertEqual(controller.schedule(0), edited)
        self.assertEqual(result.model.schedule(0).level(0).name, "Madrugada")
        self.assertEqual(
            CircuitCalculationLevelsController(result.model).schedule(0).level(0).name,
            "Madrugada",
        )
        with self.assertRaises(FrozenInstanceError):
            result.model.source_path = "alterado"  # type: ignore[misc]

    def test_csv_falls_back_to_cp1252_and_cancel_is_transactional(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "CIRCUITO_PATAMARES.csv"
            text = ";".join(EXPECTED_CIRCUIT_LEVEL_HEADER) + "\n"
            text += "\n".join(";".join(row) for row in rows("2"))
            path.write_bytes(text.encode("cp1252"))
            result = load_circuit_levels_csv(path, self.catalog)
            self.assertEqual(result.encoding, "cp1252")
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(CsvImportCancelled):
                load_circuit_levels_csv(path, self.catalog, cancel_event=cancelled)


if __name__ == "__main__":
    unittest.main()
