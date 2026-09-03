from __future__ import annotations

import unittest

import numpy as np

from circuit_viewer.model import (
    CLOSED_SWITCH_STATE,
    OPEN_SWITCH_STATE,
    Bounds,
    CircuitModel,
    FeatureSelection,
    LineNetworkModel,
    LoadModel,
    StaticPointIndex,
    StaticSegmentIndex,
    SwitchModel,
    UtmCrs,
    switch_state_label,
)


class StaticPointIndexTests(unittest.TestCase):
    def test_query_rect_is_inclusive(self) -> None:
        x = np.array([0.0, 1.0, 2.0, 3.0])
        y = np.array([0.0, 2.0, 1.0, 3.0])
        index = StaticPointIndex(x, y)

        found = set(index.query_rect(Bounds(1.0, 1.0, 2.0, 2.0)).tolist())

        self.assertEqual(found, {1, 2})

    def test_nearest_respects_radius_and_tie_breaks_by_index(self) -> None:
        x = np.array([-1.0, 1.0, 5.0])
        y = np.array([0.0, 0.0, 5.0])
        index = StaticPointIndex(x, y)

        self.assertEqual(index.nearest(0.0, 0.0, 1.0), 0)
        self.assertIsNone(index.nearest(0.0, 0.0, 0.9))

    def test_random_queries_match_brute_force(self) -> None:
        rng = np.random.default_rng(20260801)
        x = rng.uniform(-1_000.0, 1_000.0, 2_000)
        y = rng.uniform(-2_000.0, 2_000.0, 2_000)
        index = StaticPointIndex(x, y)

        for _ in range(50):
            left, right = sorted(rng.uniform(-1_000.0, 1_000.0, 2))
            top, bottom = sorted(rng.uniform(-2_000.0, 2_000.0, 2))
            expected = set(
                np.flatnonzero(
                    (x >= left) & (x <= right) & (y >= top) & (y <= bottom)
                ).tolist()
            )
            actual = set(index.query_rect(Bounds(left, top, right, bottom)).tolist())
            self.assertEqual(actual, expected)


class StaticSegmentIndexTests(unittest.TestCase):
    def test_nearest_handles_orientations_degenerate_segments_and_ties(self) -> None:
        index = StaticSegmentIndex(
            np.array([0.0, 0.0, 0.0, 20.0, 0.0]),
            np.array([0.0, 0.0, 0.0, 5.0, 0.0]),
            np.array([10.0, 0.0, 10.0, 20.0, 10.0]),
            np.array([0.0, 10.0, 10.0, 5.0, 0.0]),
        )

        self.assertEqual(index.nearest(5.0, 1.0, 1.0), 0)  # horizontal
        self.assertEqual(index.nearest(1.0, 5.0, 1.0), 1)  # vertical
        self.assertEqual(index.nearest(6.0, 5.0, 1.0), 2)  # diagonal
        self.assertEqual(index.nearest(20.5, 5.0, 0.5), 3)  # ponto
        self.assertEqual(index.nearest(5.0, 0.0, 0.0), 0)  # empate por índice
        self.assertIsNone(index.nearest(20.0, 20.0, 1.0))

    def test_random_nearest_queries_match_brute_force(self) -> None:
        rng = np.random.default_rng(20260802)
        x1 = rng.uniform(-1_000.0, 1_000.0, 2_000)
        y1 = rng.uniform(-1_000.0, 1_000.0, 2_000)
        x2 = x1 + rng.uniform(-50.0, 50.0, 2_000)
        y2 = y1 + rng.uniform(-50.0, 50.0, 2_000)
        x2[:5] = x1[:5]
        y2[:5] = y1[:5]
        index = StaticSegmentIndex(x1, y1, x2, y2)

        vx = x2 - x1
        vy = y2 - y1
        squared_lengths = vx * vx + vy * vy
        for _ in range(100):
            x, y = rng.uniform(-1_000.0, 1_000.0, 2)
            tolerance = float(rng.uniform(1.0, 100.0))
            projection = np.zeros(x1.size, dtype=np.float64)
            np.divide(
                (x - x1) * vx + (y - y1) * vy,
                squared_lengths,
                out=projection,
                where=squared_lengths > 0.0,
            )
            projection = np.clip(projection, 0.0, 1.0)
            distances = (
                (x - (x1 + projection * vx)) ** 2
                + (y - (y1 + projection * vy)) ** 2
            )
            candidates = np.flatnonzero(distances <= tolerance * tolerance)
            expected = (
                None
                if candidates.size == 0
                else int(candidates[np.lexsort((candidates, distances[candidates]))[0]])
            )
            self.assertEqual(index.nearest(float(x), float(y), tolerance), expected)

    def test_feature_selection_validates_kind_and_index(self) -> None:
        self.assertEqual(FeatureSelection("segment", 3).index, 3)
        with self.assertRaises(ValueError):
            FeatureSelection("invalid", 0)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            FeatureSelection("bar", -1)


class CircuitModelTests(unittest.TestCase):
    def test_records_are_retrieved_without_qt(self) -> None:
        model = CircuitModel(
            ["B1", "B2"],
            ["C1", "C2"],
            [500_000.0, 500_100.0],
            [8_000_000.0, 8_000_100.0],
            UtmCrs(21, northern=False),
        )

        self.assertEqual(model.index_for_id("B2"), 1)
        self.assertEqual(model.record(1).code, "C2")
        self.assertEqual(model.crs.epsg, 32721)
        self.assertFalse(model.x.flags.writeable)

    def test_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicado"):
            CircuitModel(
                ["B1", "B1"],
                ["", ""],
                [1.0, 2.0],
                [1.0, 2.0],
                UtmCrs(21, northern=False),
            )

    def test_line_network_reuses_bar_coordinates_and_indexes_extents(self) -> None:
        bars = CircuitModel(
            ["B1", "B2", "B3", "B4"],
            ["", "", "", ""],
            [0.0, 10.0, 20.0, 30.0],
            [0.0, 10.0, 0.0, 30.0],
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            ["T1", "T2", "T3"],
            ["", "", ""],
            ["ABC", "ABC", "ABC"],
            [0, 1, 2],
            [1, 2, 3],
            ["", "", ""],
            ["", "", ""],
            ["", "", ""],
            [10.0, 10.0, float("nan")],
        )

        self.assertIs(network.bars, bars)
        self.assertEqual(network.phases, ("ABC", "ABC", "ABC"))
        self.assertFalse(network.start_indices.flags.writeable)
        self.assertEqual(network.record(0).start_bar_id, "B1")
        found = set(network.spatial_index.query_rect(Bounds(9.0, 0.0, 21.0, 11.0)))
        self.assertEqual(found, {0, 1, 2})

    def test_switch_model_reuses_segments_and_looks_up_by_segment(self) -> None:
        bars = CircuitModel(
            ["B1", "B2", "B3"],
            ["", "", ""],
            [0.0, 10.0, 20.0],
            [0.0, 0.0, 0.0],
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
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
        switches = SwitchModel(
            segments,
            ["CH1"],
            ["TC"],
            ["CIR1"],
            [1],
            ["COD1"],
            ["A"],
            ["F"],
            ["N"],
            ["E1"],
            ["FUSIVEL"],
        )

        self.assertIs(switches.segments, segments)
        self.assertFalse(switches.segment_indices.flags.writeable)
        self.assertIsNone(switches.record_for_segment(0))
        record = switches.record_for_segment_id("T2")
        self.assertEqual(record.switch_id, "CH1")
        self.assertEqual(record.segment_id, "T2")
        self.assertEqual(switches.index_for_id("CH1"), 0)

    def test_load_model_reuses_bars_and_exposes_immutable_records(self) -> None:
        bars = CircuitModel(
            ["B1", "B2"],
            ["", ""],
            [0.0, 10.0],
            [0.0, 5.0],
            UtmCrs(21, northern=False),
        )
        loads = LoadModel(
            bars,
            ["L2", "L1"],
            [1, 1],
            ["E2", "E1"],
            ["C2", "C1"],
            ["20", "10"],
            ["18", "9"],
            ["220", "127"],
            ["ABC", "A"],
            ["Y", "D"],
        )

        self.assertIs(loads.bars, bars)
        self.assertEqual(loads.index_for_id("L1"), 1)
        self.assertEqual(loads.record(1).bar_id, "B2")
        self.assertEqual(loads.record(1).secondary_line_voltage, "127")
        self.assertFalse(loads.bar_indices.flags.writeable)
        self.assertEqual(loads.spatial_index.nearest(10.0, 5.0, 0.0), 0)
        self.assertEqual(FeatureSelection("load", 0).kind, "load")
        self.assertEqual(
            FeatureSelection("equivalent_load", 1).kind,
            "equivalent_load",
        )

        with self.assertRaisesRegex(ValueError, "duplicado"):
            LoadModel(
                bars,
                ["L1", "L1"],
                [0, 1],
                ["", ""],
                ["", ""],
                ["", ""],
                ["", ""],
                ["", ""],
                ["", ""],
                ["", ""],
            )


class SwitchStateLabelTests(unittest.TestCase):
    """0 e 1 por extenso: o painel mostra a palavra, não o código."""

    def test_the_two_known_states(self) -> None:
        self.assertEqual(switch_state_label(CLOSED_SWITCH_STATE), "FECHADA")
        self.assertEqual(switch_state_label(OPEN_SWITCH_STATE), "ABERTA")

    def test_surrounding_spaces_do_not_lose_the_answer(self) -> None:
        # O cadastro chega como texto; o resto do código já faz .strip().
        self.assertEqual(switch_state_label(" 1 "), "FECHADA")
        self.assertEqual(switch_state_label("\t0\n"), "ABERTA")

    def test_an_empty_state_has_no_label(self) -> None:
        # Vazio devolve vazio, e a interface converte em travessão.
        self.assertEqual(switch_state_label(""), "")
        self.assertEqual(switch_state_label("   "), "")

    def test_an_unrecognised_value_says_what_the_program_does(self) -> None:
        # O programa trata o valor inválido como aberta — o exportador registra
        # "exportada como aberta" e a travessia bloqueia. Dizer só ABERTA
        # esconderia o defeito de cadastro; dizer só travessão esconderia o
        # comportamento.
        for value in ("2", "x", "1.0"):
            with self.subTest(value=value):
                label = switch_state_label(value)
                self.assertTrue(label.startswith("ABERTA"), label)
                self.assertIn("não reconhecido", label)

    def test_the_exporter_shares_the_constant_instead_of_copying_it(self) -> None:
        # Um rótulo errado não quebra teste de comportamento: só mente na tela.
        # Enquanto os dois forem o mesmo objeto, não há como divergirem.
        from circuit_viewer.opendss_export import (
            CLOSED_SWITCH_STATE as exported,
        )

        self.assertIs(exported, CLOSED_SWITCH_STATE)


if __name__ == "__main__":
    unittest.main()
