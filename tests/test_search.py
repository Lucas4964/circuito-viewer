from __future__ import annotations

import unittest
import threading

from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.search import (
    GlobalSearchIndex,
    SearchCancelled,
    build_field_search_partition,
    normalize_code,
    query_field_snapshot,
)


class GlobalSearchIndexTests(unittest.TestCase):
    def make_models(self):
        bars = CircuitModel(
            ["B0", "B1", "B2"],
            [" AÇÃO-1 ", "acao-1", ""],
            [0.0, 100.0, 200.0],
            [0.0, 0.0, 0.0],
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
            bars,
            ["T0", "T1"],
            ["ACAO-1", "Acao-10"],
            ["ABC", "ABC"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["", ""],
            ["", ""],
            [100.0, 100.0],
        )
        switches = SwitchModel(
            segments,
            ["CH0"],
            ["TC"],
            ["C0"],
            [0],
            ["ação-1"],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        circuits = CircuitCatalogModel.build(
            segments,
            switches,
            [CircuitDefinition("C0", "B0", "AÇÃO-1", "13.8")],
        )
        return bars, segments, switches, circuits

    def test_normalization_ignores_whitespace_case_and_accents(self) -> None:
        self.assertEqual(normalize_code("  AÇÃO-1 "), "acao-1")

    def test_exact_duplicates_across_every_kind_are_not_truncated(self) -> None:
        bars, segments, switches, circuits = self.make_models()
        index = GlobalSearchIndex()
        index.set_bars(bars)
        index.set_segments(segments)
        index.set_switches(switches)
        index.set_circuits(circuits)

        result = index.query(" acao-1 ", limit=1)

        self.assertEqual(
            [item.kind for item in result.results[:5]],
            ["bar", "bar", "segment", "switch", "circuit"],
        )
        self.assertEqual(result.results[5].code, "Acao-10")
        self.assertFalse(result.truncated)
        self.assertEqual(result.results[3].related_id, "T0")
        self.assertEqual(result.results[4].related_id, "B0")

    def test_prefix_prioritizes_exact_and_reports_truncation(self) -> None:
        bars, segments, _, _ = self.make_models()
        index = GlobalSearchIndex()
        index.set_bars(bars)
        index.set_segments(segments)

        result = index.query("acao-1", limit=1)

        self.assertEqual(
            [item.code.strip() for item in result.results[:3]],
            ["AÇÃO-1", "acao-1", "ACAO-1"],
        )
        self.assertEqual(result.results[-1].code, "Acao-10")
        self.assertFalse(result.truncated)

        limited = index.query("acao", limit=1)
        self.assertEqual(len(limited.results), 1)
        self.assertTrue(limited.truncated)

    def test_partition_replacement_and_clear_remove_stale_results(self) -> None:
        bars, segments, switches, circuits = self.make_models()
        index = GlobalSearchIndex()
        index.set_bars(bars)
        index.set_segments(segments)
        index.set_switches(switches)
        index.set_circuits(circuits)
        self.assertGreater(len(index), 0)

        replacement = CircuitModel(
            ["B9"], ["NOVO"], [0.0], [0.0], UtmCrs(21, northern=False)
        )
        index.set_bars(replacement)
        index.set_segments(None)
        index.set_switches(None)
        index.set_circuits(None)

        self.assertEqual(index.query("acao").results, ())
        self.assertEqual(index.query("novo").results[0].entity_id, "B9")
        self.assertEqual(len(index), 1)

    def test_empty_codes_and_queries_are_ignored(self) -> None:
        bars, _, _, _ = self.make_models()
        index = GlobalSearchIndex()
        index.set_bars(bars)
        self.assertEqual(len(index), 2)
        self.assertEqual(index.query("   ").results, ())
        with self.assertRaises(ValueError):
            index.query("A", limit=-1)

    def test_loads_are_separate_results_and_partition_is_replaceable(self) -> None:
        bars, _, _, _ = self.make_models()
        loads = LoadModel(
            bars,
            ["L1", "L2"],
            [0, 1],
            ["", ""],
            ["ação-1", "AÇÃO-1"],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
            ["", ""],
        )
        index = GlobalSearchIndex()
        index.set_loads(loads)

        results = index.query("acao-1", limit=0).results

        self.assertEqual([result.kind for result in results], ["load", "load"])
        self.assertEqual(results[0].target.kind, "load")
        self.assertEqual(results[0].related_id, "B0")
        self.assertIn("Carga · L1 · B0", results[0].display_text)
        index.set_loads(None)
        self.assertEqual(index.query("acao-1").results, ())

    def test_any_field_search_covers_every_primary_model(self) -> None:
        bars = CircuitModel(
            ["B-ORIGEM", "B-DESTINO"],
            ["", "COD-BARRA"],
            [500_123.0, 500_456.0],
            [8_000_000.0, 8_000_100.0],
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
            bars,
            ["T-ESPECIAL"],
            [""],
            ["FASE-SEG"],
            [0],
            [1],
            ["ARR-ESPECIAL"],
            ["CABO-FASE"],
            ["CABO-NEUTRO"],
            [12.5],
        )
        switches = SwitchModel(
            segments,
            ["CH-ESPECIAL"],
            ["TIPO-CHAVE"],
            ["C-ESPECIAL"],
            [0],
            [""],
            ["FECHADA"],
            ["NORMAL-ESPECIAL"],
            ["CORN-VALOR"],
            ["ELO-VALOR"],
            ["ELO-TIPO"],
        )
        loads = LoadModel(
            bars,
            ["L-ESPECIAL"],
            [1],
            ["EXTERNO-ESPECIAL"],
            [""],
            ["15.75"],
            ["12.25"],
            ["220"],
            ["FASE-CARGA"],
            ["LIGAÇÃO-ESPECIAL"],
        )
        circuits = CircuitCatalogModel.build(
            segments,
            switches,
            [CircuitDefinition("C-ESPECIAL", "B-ORIGEM", "", "13.8-ESPECIAL")],
        )
        index = GlobalSearchIndex()
        index.set_bars(bars)
        index.set_segments(segments)
        index.set_switches(switches)
        index.set_loads(loads)
        index.set_circuits(circuits)

        cases = {
            "500123": ("bar", "X"),
            "arr-especial": ("segment", "ARRANJO_ID"),
            "normal-especial": ("switch", "ESTADO_NORMAL"),
            "externo-especial": ("load", "EXTERN_ID"),
            "ligacao-especial": ("load", "TIPO_LIG"),
            "13.8-especial": ("circuit", "VNOM"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = index.query_any_field(text)
                self.assertEqual(result.total_matches, 1)
                self.assertEqual(result.results[0].kind, expected[0])
                self.assertEqual(result.results[0].field_matches[0].column, expected[1])

        self.assertEqual(index.entity_count, 6)
        self.assertEqual(len(index), 1)

    def test_any_field_returns_each_entity_once_and_explains_all_matches(self) -> None:
        bars = CircuitModel(
            ["ALPHA-ID"],
            ["ALPHA"],
            [1.0],
            [2.0],
            UtmCrs(21, northern=False),
        )
        index = GlobalSearchIndex()
        index.set_bars(bars)

        result = index.query_any_field("alpha")

        self.assertEqual(result.total_matches, 1)
        self.assertEqual(len(result.results), 1)
        self.assertEqual(
            [match.column for match in result.results[0].field_matches],
            ["CODIGO", "BARRA_ID"],
        )
        self.assertIn("CODIGO: ALPHA", result.results[0].display_text)
        self.assertIn("BARRA_ID: ALPHA-ID", result.results[0].tooltip_text)

    def test_any_field_limit_counts_all_matches_and_orders_quality(self) -> None:
        bars = CircuitModel(
            [f"B{index:03d}" for index in range(250)],
            ["VALOR", "VALOR-PREFIXO", *("X-VALOR-Y" for _ in range(248))],
            range(250),
            range(250),
            UtmCrs(21, northern=False),
        )
        index = GlobalSearchIndex()
        index.set_bars(bars)

        result = index.query_any_field("valor", limit=200)

        self.assertEqual(result.total_matches, 250)
        self.assertEqual(len(result.results), 200)
        self.assertTrue(result.truncated)
        self.assertEqual(result.results[0].code, "VALOR")
        self.assertEqual(result.results[1].code, "VALOR-PREFIXO")

    def test_field_snapshots_are_cancelable_and_stale_partitions_are_rejected(self) -> None:
        bars = CircuitModel(
            ["B1"], ["ANTIGO"], [0.0], [0.0], UtmCrs(21, northern=False)
        )
        replacement = CircuitModel(
            ["B2"], ["NOVO"], [0.0], [0.0], UtmCrs(21, northern=False)
        )
        index = GlobalSearchIndex()
        index.set_bars(bars, build_fields=False)
        old_partition = build_field_search_partition("bar", bars)
        index.set_bars(replacement, build_fields=False)

        self.assertFalse(index.install_field_partition(old_partition))
        new_partition = build_field_search_partition("bar", replacement)
        self.assertTrue(index.install_field_partition(new_partition))
        snapshot = index.field_snapshot()
        self.assertIsNotNone(snapshot)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(SearchCancelled):
            query_field_snapshot(
                snapshot,
                "novo",
                cancel_check=cancelled.is_set,
            )


if __name__ == "__main__":
    unittest.main()
