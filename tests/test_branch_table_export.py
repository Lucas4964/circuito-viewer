from __future__ import annotations

import codecs
import csv
from dataclasses import replace
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
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.branch_table_export import branch_table_values
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


def make_switch_first_snapshot():
    bars = CircuitModel(
        ["B0", "B1", "B2", "B3"],
        ["CB0", "CB1", "CB2", "CB3"],
        [500_000.0, 500_010.0, 500_020.0, 500_030.0],
        [8_000_000.0] * 4,
        UtmCrs(21, northern=False),
    )
    segments = LineNetworkModel(
        bars,
        ["T0", "T1", "T2"],
        ["CT0", "TRECHO-CHAVE", "TRECHO-COMUM"],
        ["DEF", "D", "D"],
        [0, 1, 2],
        [1, 2, 3],
        ["", "", ""],
        ["", "", ""],
        ["", "", ""],
        [10.0, 10.0, 10.0],
    )
    switches = SwitchModel(
        segments,
        ["CH1"],
        ["TIPO"],
        ["C1"],
        [1],
        ["COD-CH1"],
        ["1"],
        ["1"],
        [""],
        [""],
        [""],
    )
    catalog = CircuitCatalogModel.build(
        segments,
        switches,
        [CircuitDefinition("C1", "B0", "", "")],
    )
    return analyze_branches(catalog, PHASES)


def equivalent_with_demand(
    branches,  # noqa: ANN001
    value: Decimal,
    current: Decimal | None = None,
):
    record = SimpleNamespace(
        branch_id=1,
        maximum_active_demand=value,
        maximum_current=current,
    )
    model = SimpleNamespace(branches=branches, records=(record,))
    return SimpleNamespace(model=model)


def column(name: str) -> int:
    """Posicao da coluna pelo nome, para o teste sobreviver a reordenacoes."""

    return BRANCH_TABLE_HEADERS.index(name)


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
        self.assertEqual(len(rows[1]), len(BRANCH_TABLE_HEADERS))
        self.assertEqual(rows[1][column("CIRC_ID")], "C;Á")
        self.assertEqual(rows[1][column("NIVEL_TOPOLOGICO")], "1")
        self.assertEqual(rows[1][column("TRECHO_CODIGO")], "TRECHO;Á")
        self.assertEqual(rows[1][column("CHAVE_ID")], "")
        self.assertEqual(rows[1][column("CHAVE_CODIGO")], "")
        self.assertEqual(
            rows[1][column("COMPR")],
            repr(branches.records[0].total_length).replace(".", ","),
        )
        self.assertEqual(
            rows[1][column("DEMANDA_MAXIMA")],
            "40,905912345678901234",
        )
        self.assertEqual(rows[1][column("CORRENTE_MAXIMA")], "")

    def test_maximum_current_reaches_the_csv_with_full_precision(self) -> None:
        branches = make_snapshot()
        current = Decimal("1.234567890123456789")

        rows = parse_csv(
            build_branches_csv_bytes(
                branches,
                equivalent_with_demand(branches, Decimal("1"), current),
                (0,),
            )
        )

        self.assertEqual(
            rows[1][column("CORRENTE_MAXIMA")],
            "1,234567890123456789",
        )

    def test_missing_maximum_demand_is_an_empty_cell(self) -> None:
        branches = make_snapshot()

        rows = parse_csv(build_branches_csv_bytes(branches, None, (0,)))

        self.assertEqual(rows[1][column("DEMANDA_MAXIMA")], "")

    def test_csv_separates_first_common_segment_from_first_switch(self) -> None:
        branches = make_switch_first_snapshot()

        rows = parse_csv(build_branches_csv_bytes(branches, None, (0,)))

        self.assertEqual(
            [
                rows[1][column(name)]
                for name in ("TRECHO_ID", "TRECHO_CODIGO", "CHAVE_ID", "CHAVE_CODIGO")
            ],
            ["T2", "TRECHO-COMUM", "CH1", "COD-CH1"],
        )

    def test_non_removable_branch_hides_switch_columns(self) -> None:
        record = make_switch_first_snapshot().records[0]
        record = replace(
            record,
            removable=False,
            first_switch_position=6,
        )

        values = branch_table_values(record, None, None)

        self.assertEqual(values[BRANCH_TABLE_HEADERS.index("CHAVE_ID")], "")
        self.assertEqual(values[BRANCH_TABLE_HEADERS.index("CHAVE_CODIGO")], "")

    def test_every_header_names_the_value_below_it(self) -> None:
        """Prende as duas tuplas posicionais uma a outra.

        ``BRANCH_TABLE_HEADERS`` e ``branch_table_values`` sao listas paralelas
        escritas a mao. Reordenar so uma delas nao quebra nada que se veja: a
        tabela e o CSV seguem saindo, com cada numero sob o cabecalho errado.
        """

        record = make_switch_first_snapshot().records[0]
        demand = Decimal("12.5")

        current = Decimal("3.25")

        row = dict(
            zip(
                BRANCH_TABLE_HEADERS,
                branch_table_values(record, demand, current),
            )
        )

        self.assertEqual(len(row), len(BRANCH_TABLE_HEADERS))
        self.assertEqual(row["RAMAL_ID"], record.branch_id)
        self.assertEqual(row["DEMANDA_MAXIMA"], demand)
        self.assertEqual(row["CORRENTE_MAXIMA"], current)
        self.assertEqual(row["NUM_CARGAS"], record.load_count)
        self.assertEqual(row["TIPO_RAMAL"], record.branch_type.value)
        self.assertEqual(row["CIRC_ID"], record.circuit_id)
        self.assertEqual(row["BARRA_ID"], record.connection_bar_id)
        self.assertEqual(row["BARRA_CODIGO"], record.connection_bar_code)
        self.assertEqual(row["NIVEL_TOPOLOGICO"], record.topological_level)
        self.assertEqual(row["TRECHO_ID"], record.first_common_segment_id)
        self.assertEqual(row["TRECHO_CODIGO"], record.first_common_segment_code)
        self.assertEqual(row["CHAVE_ID"], record.first_switch_id)
        self.assertEqual(row["CHAVE_CODIGO"], record.first_switch_code)
        self.assertEqual(row["NUM_TRECHOS"], record.segment_count)
        self.assertEqual(row["COMPR"], record.total_length)
        self.assertEqual(row["FASES2"], record.phases2)
        self.assertEqual(row["FASE"], record.phase)
        self.assertEqual(row["REMANEJAVEL"], int(record.removable))
        self.assertEqual(row["NUM_BARRAS"], record.bar_count)
        self.assertEqual(row["NUM_CHAVES"], record.switch_count)
        self.assertEqual(row["POS_PRIMEIRA_CHAVE"], record.first_switch_position)
        self.assertEqual(row["NUM_CONEXOES_TRONCO"], record.trunk_connection_count)
        self.assertEqual(row["NUM_COMPR_AUSENTE"], record.missing_length_count)
        self.assertEqual(row["TOPOLOGIA"], record.topology)

    def test_demand_and_load_count_lead_the_row(self) -> None:
        # A ordem existe por um motivo de uso: sao as duas colunas que se olha
        # para decidir marcar um ramal, e a caixa de marcacao abre a tabela.
        self.assertEqual(
            BRANCH_TABLE_HEADERS[:4],
            ("RAMAL_ID", "DEMANDA_MAXIMA", "CORRENTE_MAXIMA", "NUM_CARGAS"),
        )

    def test_received_order_is_preserved_exactly(self) -> None:
        branches = make_snapshot(two_branches=True)

        rows = parse_csv(build_branches_csv_bytes(branches, None, (1, 0)))

        self.assertEqual([row[0] for row in rows[1:]], ["2", "1"])
        self.assertEqual(
            [row[column("NIVEL_TOPOLOGICO")] for row in rows[1:]],
            ["2", "1"],
        )

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
