from __future__ import annotations

import unittest

from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.search import GlobalSearchIndex, normalize_code


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


if __name__ == "__main__":
    unittest.main()
