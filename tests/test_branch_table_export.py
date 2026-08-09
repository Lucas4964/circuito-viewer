from __future__ import annotations

import codecs
import csv
from decimal import Decimal
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from circuit_viewer import (
    BRANCH_TABLE_HEADERS,
    analyze_branches,
    build_branches_csv_bytes,
    export_branches_csv,
    suggested_branch_csv_filename,
)
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    UtmCrs,
)
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("d", "D", 1),
        PhaseMappingEntry("def", "DEF", 3),
    )
)


def make_snapshot(*, two_branches: bool = False):
    bar_count = 5 if two_branches else 4
    bars = CircuitModel(
        [f"B{index}" for index in range(bar_count)],
        [f"CB{index}" for index in range(bar_count)],
        [500_000.0 + index * 10.0 for index in range(bar_count)],
        [8_000_000.0] * bar_count,
        UtmCrs(21, northern=False),
    )
    if two_branches:
        starts = [0, 1, 1, 2]
        ends = [1, 2, 3, 4]
        phases = ["DEF", "DEF", "D", "D"]
        codes = ["CT0", "CT1", "TRECHO;Á", "CT3"]
        lengths = [10.0, 10.0, 12.3456789012345, 2.5]
    else:
        starts = [0, 1, 1]
        ends = [1, 2, 3]
        phases = ["DEF", "DEF", "D"]
        codes = ["CT0", "CT1", "TRECHO;Á"]
        lengths = [10.0, 10.0, 12.3456789012345]
    segments = LineNetworkModel(
        bars,
        [f"T{index}" for index in range(len(starts))],
        codes,
        phases,
        starts,
        ends,
        [""] * len(starts),
        [""] * len(starts),
        [""] * len(starts),
        lengths,
    )
    catalog = CircuitCatalogModel.build(
        segments,
        None,
        [CircuitDefinition("C;Á", "B0", "", "")],
    )
    return analyze_branches(catalog, PHASES)


def equivalent_with_demand(branches, value: Decimal):  # noqa: ANN001
    record = SimpleNamespace(branch_id=1, maximum_active_demand=value)
    model = SimpleNamespace(branches=branches, records=(record,))
    return SimpleNamespace(model=model)


def parse_csv(content: bytes) -> list[list[str]]:
    stream = io.StringIO(content.decode("utf-8-sig"), newline="")
    return list(csv.reader(stream, delimiter=";"))


class BranchTableExportTests(unittest.TestCase):
    def test_pt_br_csv_has_bom_crlf_quotes_and_full_precision(self) -> None:
        branches = make_snapshot()
        demand = Decimal("40.905912345678901234")

        content = build_branches_csv_bytes(
            branches,
            equivalent_with_demand(branches, demand),
            (0,),
        )

        self.assertTrue(content.startswith(codecs.BOM_UTF8))
        self.assertEqual(content.count(b"\r\n"), 2)
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
        rows = parse_csv(content)
        self.assertEqual(tuple(rows[0]), BRANCH_TABLE_HEADERS)
        self.assertEqual(len(rows[1]), 21)
        self.assertEqual(rows[1][2], "C;Á")
        self.assertEqual(rows[1][5], "1")
        self.assertEqual(rows[1][7], "TRECHO;Á")
        self.assertEqual(
            rows[1][9],
            repr(branches.records[0].total_length).replace(".", ","),
        )
        self.assertEqual(rows[1][11], "40,905912345678901234")

    def test_missing_maximum_demand_is_an_empty_cell(self) -> None:
        branches = make_snapshot()

        rows = parse_csv(build_branches_csv_bytes(branches, None, (0,)))

        self.assertEqual(rows[1][11], "")

    def test_received_order_is_preserved_exactly(self) -> None:
        branches = make_snapshot(two_branches=True)

        rows = parse_csv(build_branches_csv_bytes(branches, None, (1, 0)))

        self.assertEqual([row[0] for row in rows[1:]], ["2", "1"])
        self.assertEqual([row[5] for row in rows[1:]], ["2", "1"])

    def test_atomic_round_trip_and_no_temporary_file(self) -> None:
        branches = make_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ramais.csv"

            result = export_branches_csv(target, branches, None, (0,))

            self.assertEqual(result.path, target)
            self.assertEqual(result.branch_count, 1)
            self.assertEqual(result.circuit_ids, ("C;Á",))
            self.assertTrue(target.read_bytes().startswith(codecs.BOM_UTF8))
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_cancellation_preserves_previous_file(self) -> None:
        branches = make_snapshot()
        cancelled = False

        def progress(_current: int, _total: int) -> None:
            nonlocal cancelled
            cancelled = True

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ramais.csv"
            target.write_bytes(b"anterior")

            with self.assertRaises(InterruptedError):
                export_branches_csv(
                    target,
                    branches,
                    None,
                    (0,),
                    cancel_check=lambda: cancelled,
                    progress=progress,
                )

            self.assertEqual(target.read_bytes(), b"anterior")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_replace_failure_preserves_previous_file(self) -> None:
        branches = make_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ramais.csv"
            target.write_bytes(b"anterior")

            with patch(
                "circuit_viewer.branch_table_export.os.replace",
                side_effect=OSError("falha simulada"),
            ), self.assertRaisesRegex(OSError, "falha simulada"):
                export_branches_csv(target, branches, None, (0,))

            self.assertEqual(target.read_bytes(), b"anterior")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_suggested_names_cover_specific_and_all_circuits(self) -> None:
        self.assertEqual(suggested_branch_csv_filename(None), "ramais_todos.csv")
        self.assertEqual(
            suggested_branch_csv_filename("Circuito 3/MT"),
            "ramais_Circuito_3_MT.csv",
        )


if __name__ == "__main__":
    unittest.main()
