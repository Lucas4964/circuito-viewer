from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path

from circuit_viewer.downstream_selection import DownstreamOrigin, select_downstream
from circuit_viewer.model import LineNetworkModel
from circuit_viewer.phase_config import load_phase_configuration
from circuit_viewer.downstream_table import (
    BARRA,
    CAPACITOR,
    CARGA,
    CHAVE,
    DOWNSTREAM_CSV_HEADERS,
    DOWNSTREAM_KINDS,
    DOWNSTREAM_TABLE_HEADERS,
    GERADOR,
    INFO_CSV_SEPARATOR,
    REGULADOR,
    TRECHO,
    build_downstream_csv_bytes,
    downstream_rows,
    downstream_summary,
    downstream_table_values,
    export_downstream_csv,
    highlight_segment_indices,
    suggested_downstream_csv_filename,
)

from test_block_analysis import make_bars, make_loads, make_network, make_switches
from test_downstream_selection import (
    make_capacitors,
    make_generators,
    make_regulators,
    orient,
)


def sample_selection(origin=("segment", 1)):  # noqa: ANN001, ANN201
    """B0 —T0— B1 —T1(chave)— B2 —T2— B3, com B1 —T3— B4.

    T2 cadastrado ao contrário (B3→B2). Carga em B2 e em B0, capacitor em B3,
    gerador na carga de B2, regulador em T2.
    """

    bars = make_bars(5)
    network = make_network(bars, [0, 1, 3, 1], [1, 2, 2, 4], [100.0, 50.0, 25.0, 10.0])
    switches = make_switches(network, [(1, "1", "1")])
    loads = make_loads(bars, [2, 0], ["75", "30"])
    return select_downstream(
        orient(network, switches),
        DownstreamOrigin(*origin),
        loads=loads,
        capacitors=make_capacitors(bars, [3]),
        generators=make_generators(loads, [0]),
        regulators=make_regulators(network, [2]),
    )


def by_kind(rows, kind):  # noqa: ANN001, ANN201
    return [row for row in rows if row.kind == kind]


class RowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = sample_selection()
        self.rows = downstream_rows(self.selection)

    def test_every_kind_in_the_region_becomes_rows(self) -> None:
        self.assertEqual(
            {row.kind for row in self.rows},
            {TRECHO, CHAVE, BARRA, CARGA, CAPACITOR, GERADOR, REGULADOR},
        )

    def test_rows_come_in_kind_order_then_level(self) -> None:
        kinds = [row.kind for row in self.rows]
        self.assertEqual(kinds, sorted(kinds, key=DOWNSTREAM_KINDS.index))
        segments = by_kind(self.rows, TRECHO)
        self.assertEqual([row.obj_id for row in segments], ["T1", "T2"])

    def test_a_switch_is_a_segment_row_and_a_switch_row(self) -> None:
        """Fiel ao banco: o trecho tem TRECHO_ID, a chave tem CHAVE_ID."""

        self.assertIn("T1", [row.obj_id for row in by_kind(self.rows, TRECHO)])
        self.assertEqual([row.obj_id for row in by_kind(self.rows, CHAVE)], ["CH0"])

    def test_bar_1_is_the_upstream_end(self) -> None:
        # T2 está cadastrado de B3 para B2, mas a energia vem de B2.
        segment = next(row for row in self.rows if row.obj_id == "T2")
        self.assertEqual((segment.bar_1, segment.bar_2), ("B2", "B3"))
        regulator = by_kind(self.rows, REGULADOR)[0]
        self.assertEqual((regulator.bar_1, regulator.bar_2), ("B2", "B3"))

    def test_the_origin_segment_starts_at_the_upstream_bar(self) -> None:
        origin = next(row for row in self.rows if row.obj_id == "T1")
        self.assertEqual((origin.bar_1, origin.bar_2), ("B1", "B2"))
        self.assertEqual(origin.level, 0)

    def test_bar_2_is_empty_for_everything_on_a_single_bar(self) -> None:
        for kind in (BARRA, CARGA, CAPACITOR, GERADOR):
            for row in by_kind(self.rows, kind):
                with self.subTest(kind=kind, obj_id=row.obj_id):
                    self.assertEqual(row.bar_2, "")
                    self.assertTrue(row.bar_1)

    def test_a_bar_row_names_itself(self) -> None:
        bar = by_kind(self.rows, BARRA)[0]
        self.assertEqual(bar.bar_1, bar.obj_id)
        self.assertEqual(bar.info, "Origem")

    def test_loads_outside_the_region_are_left_out(self) -> None:
        self.assertEqual([row.obj_id for row in by_kind(self.rows, CARGA)], ["L0"])

    def test_the_kind_and_id_pair_is_unique(self) -> None:
        keys = [(row.kind, row.obj_id) for row in self.rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_filtering_keeps_only_the_wanted_kinds(self) -> None:
        rows = downstream_rows(self.selection, (CHAVE,))
        self.assertEqual({row.kind for row in rows}, {CHAVE})

    def test_an_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            downstream_rows(self.selection, ("POSTE",))

    def test_table_values_follow_the_headers(self) -> None:
        values = downstream_table_values(self.rows[0])
        self.assertEqual(len(values), len(DOWNSTREAM_TABLE_HEADERS))

    def test_the_switch_info_spells_out_the_state(self) -> None:
        self.assertIn("Fechada", by_kind(self.rows, CHAVE)[0].info)

    def test_the_switch_info_keeps_its_two_parts(self) -> None:
        switch = by_kind(self.rows, CHAVE)[0]
        self.assertEqual(switch.info_parts, ("Fechada", "Chave"))
        # Na tela, uma célula só, como antes.
        self.assertEqual(switch.info, "Fechada · Chave")


class PhaseNameTests(unittest.TestCase):
    """O INFO do trecho mostra o NOME do FASES2, não o código."""

    def segment_info(self, rows, obj_id):  # noqa: ANN001, ANN201
        return next(row for row in rows if row.kind == TRECHO and row.obj_id == obj_id).info

    def test_the_segment_shows_the_phase_name(self) -> None:
        # make_network cadastra todos os trechos com FASES2 "13".
        rows = downstream_rows(
            sample_selection(),
            phase_configuration=load_phase_configuration(),
        )
        self.assertEqual(self.segment_info(rows, "T1"), "DEF")

    def test_without_a_configuration_the_code_stays(self) -> None:
        rows = downstream_rows(sample_selection())
        self.assertEqual(self.segment_info(rows, "T1"), "13")

    def test_an_unknown_code_is_kept_as_it_is(self) -> None:
        bars = make_bars(3)
        network = LineNetworkModel(
            bars,
            ["T0", "T1"],
            ["CT0", "CT1"],
            ["99", "2"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["", ""],
            ["", ""],
            [10.0, 10.0],
        )
        selection = select_downstream(orient(network), DownstreamOrigin("bar", 0))
        rows = downstream_rows(
            selection,
            phase_configuration=load_phase_configuration(),
        )
        self.assertEqual(self.segment_info(rows, "T0"), "99")
        self.assertEqual(self.segment_info(rows, "T1"), "E")


class SummaryTests(unittest.TestCase):
    def test_the_summary_counts_and_totals_the_region(self) -> None:
        summary = downstream_summary(sample_selection())
        self.assertEqual(summary.counts[TRECHO], 2)
        self.assertEqual(summary.counts[BARRA], 2)
        self.assertEqual(summary.counts[CARGA], 1)
        self.assertAlmostEqual(summary.total_length, 75.0)
        self.assertEqual(summary.missing_length_count, 0)
        self.assertAlmostEqual(summary.total_power, 75.0)

    def test_loads_without_a_consumer_count_are_not_zero(self) -> None:
        # make_loads não informa consumidores: a carga fica "sem resposta".
        summary = downstream_summary(sample_selection())
        self.assertEqual(summary.consumer_count, 0)
        self.assertEqual(summary.unknown_consumer_load_count, 1)


class HighlightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = sample_selection(("bar", 1))

    def test_segments_light_up_the_whole_region(self) -> None:
        self.assertEqual(
            highlight_segment_indices(self.selection, (TRECHO,)).tolist(),
            [1, 2, 3],
        )

    def test_switches_light_up_only_their_segments(self) -> None:
        self.assertEqual(
            highlight_segment_indices(self.selection, (CHAVE,)).tolist(), [1]
        )

    def test_bar_attached_kinds_have_nothing_to_light(self) -> None:
        self.assertEqual(
            highlight_segment_indices(self.selection, (CARGA, BARRA)).size, 0
        )

    def test_the_highlight_is_read_only(self) -> None:
        values = highlight_segment_indices(self.selection, (TRECHO,))
        self.assertFalse(values.flags.writeable)


class CsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = sample_selection()
        self.rows = downstream_rows(self.selection)

    def read(self, content: bytes) -> list[list[str]]:
        text = content.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))

    def test_the_csv_carries_every_table_column(self) -> None:
        rows = self.read(build_downstream_csv_bytes(self.rows))
        self.assertEqual(tuple(rows[0]), DOWNSTREAM_CSV_HEADERS)
        self.assertEqual(
            DOWNSTREAM_CSV_HEADERS,
            ("OBJ_ID", "BARRA_1", "BARRA_2", "TIPO", "CODIGO", "NIVEL", "COMPR", "INFO"),
        )
        self.assertEqual(set(DOWNSTREAM_CSV_HEADERS), set(DOWNSTREAM_TABLE_HEADERS))
        self.assertEqual(len(rows), len(self.rows) + 1)

    def test_the_original_four_columns_keep_their_positions(self) -> None:
        # Quem lia o arquivo pela posição das quatro primeiras não quebra.
        self.assertEqual(
            DOWNSTREAM_CSV_HEADERS[:4], ("OBJ_ID", "BARRA_1", "BARRA_2", "TIPO")
        )

    def test_numbers_use_the_pt_br_decimal_comma(self) -> None:
        rows = self.read(build_downstream_csv_bytes(self.rows))
        segment = next(row for row in rows if row[3] == TRECHO and row[0] == "T1")
        self.assertEqual(segment[5], "0")
        self.assertEqual(segment[6], "50,0")

    def test_the_switch_parts_are_split_by_a_pipe(self) -> None:
        rows = self.read(build_downstream_csv_bytes(self.rows))
        switch = next(row for row in rows if row[3] == CHAVE)
        self.assertEqual(switch[7], "Fechada|Chave")
        self.assertEqual(switch[7].split(INFO_CSV_SEPARATOR), ["Fechada", "Chave"])

    def test_the_pipe_never_reaches_the_screen(self) -> None:
        for row in self.rows:
            with self.subTest(kind=row.kind, obj_id=row.obj_id):
                self.assertNotIn(INFO_CSV_SEPARATOR, row.info)

    def test_the_csv_uses_the_project_convention(self) -> None:
        content = build_downstream_csv_bytes(self.rows)
        self.assertTrue(content.startswith("﻿".encode("utf-8")))
        self.assertIn(b"\r\n", content)
        self.assertIn(b"OBJ_ID;BARRA_1;BARRA_2;TIPO", content)

    def test_a_load_row_has_an_empty_second_bar(self) -> None:
        rows = self.read(build_downstream_csv_bytes(self.rows))
        load = next(row for row in rows if row[3] == CARGA)
        self.assertEqual(load[:4], ["L0", "B2", "", CARGA])
        self.assertEqual(load[7], "SNOM 75")

    def test_a_duplicated_element_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_downstream_csv_bytes(self.rows + self.rows[:1])

    def test_cancelling_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "jusante.csv"
            with self.assertRaises(InterruptedError):
                export_downstream_csv(target, self.rows, cancel_check=lambda: True)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_the_export_replaces_the_file_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "jusante.csv"
            target.write_text("antigo", encoding="utf-8")
            result = export_downstream_csv(target, self.rows)
            self.assertEqual(result.row_count, len(self.rows))
            with target.open(encoding="utf-8-sig", newline="") as stream:
                header = next(csv.reader(stream, delimiter=";"))
            self.assertEqual(tuple(header), DOWNSTREAM_CSV_HEADERS)
            self.assertEqual([item.name for item in Path(directory).iterdir()], ["jusante.csv"])

    def test_the_suggested_name_carries_the_origin(self) -> None:
        self.assertEqual(suggested_downstream_csv_filename(self.selection), "jusante_T1.csv")
        bar_selection = sample_selection(("bar", 1))
        self.assertEqual(suggested_downstream_csv_filename(bar_selection), "jusante_B1.csv")


if __name__ == "__main__":
    unittest.main()
