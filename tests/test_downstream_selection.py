from __future__ import annotations

import unittest

from circuit_viewer.downstream_selection import (
    DownstreamOrigin,
    select_downstream,
)
from circuit_viewer.model import (
    CapacitorModel,
    CircuitCatalogModel,
    CircuitDefinition,
    GeneratorModel,
    NetworkTopology,
    RegulatorModel,
)
from circuit_viewer.network_orientation import build_orientation

from test_block_analysis import make_bars, make_loads, make_network, make_switches


def orient(network, switches=None, roots=("B0",)):  # noqa: ANN001, ANN201
    catalog = CircuitCatalogModel.build(
        network,
        switches,
        [
            CircuitDefinition(f"C{index}", root, f"ALIM-{index}", "13.8")
            for index, root in enumerate(roots)
        ],
    )
    return build_orientation(NetworkTopology(network, switches), catalog)


def make_capacitors(bars, bar_indices):  # noqa: ANN001, ANN201
    size = len(bar_indices)
    return CapacitorModel(
        bars,
        [f"CP{index}" for index in range(size)],
        bar_indices,
        [""] * size,
        [f"CCP{index}" for index in range(size)],
        ["13.8"] * size,
        ["300"] * size,
        ["300"] * size,
        ["300"] * size,
        ["300"] * size,
        ["ABC"] * size,
        ["Y"] * size,
    )


def make_generators(loads, load_indices):  # noqa: ANN001, ANN201
    size = len(load_indices)
    blank = [""] * size
    return GeneratorModel(
        loads,
        [f"G{index}" for index in range(size)],
        load_indices,
        blank, blank, blank, blank, blank, blank, blank, blank, blank, blank, blank,
        ["13"] * size,
    )


def make_regulators(network, segment_indices):  # noqa: ANN001, ANN201
    size = len(segment_indices)
    blank = [""] * size
    return RegulatorModel(
        network,
        [f"R{index}" for index in range(size)],
        segment_indices,
        blank, blank, blank, blank, blank, blank, blank, blank, blank,
    )


def issue_kinds(selection) -> set[str]:  # noqa: ANN001
    return {issue.kind for issue in selection.issues}


class RadialSelectionTests(unittest.TestCase):
    """B0 —T0— B1 —T1(chave)— B2 —T2— B3, com B1 —T3— B4 em derivação.

    T2 escrito ao contrário (B3→B2) de propósito.
    """

    def setUp(self) -> None:
        self.bars = make_bars(5)
        self.network = make_network(self.bars, [0, 1, 3, 1], [1, 2, 2, 4])
        self.switches = make_switches(self.network, [(1, "1", "1")])
        self.orientation = orient(self.network, self.switches)

    def select(self, kind, index, **options):  # noqa: ANN001, ANN201
        return select_downstream(
            self.orientation, DownstreamOrigin(kind, index), **options
        )

    def test_from_a_bar_the_region_is_the_bar_and_everything_below(self) -> None:
        selection = self.select("bar", 1)
        self.assertEqual(sorted(selection.bar_indices.tolist()), [1, 2, 3, 4])
        self.assertEqual(sorted(selection.segment_indices.tolist()), [1, 2, 3])
        self.assertEqual(selection.start_bar_index, 1)
        self.assertEqual(selection.issues, ())

    def test_from_a_segment_the_segment_itself_is_selected(self) -> None:
        selection = self.select("segment", 1)
        self.assertEqual(sorted(selection.bar_indices.tolist()), [2, 3])
        self.assertEqual(sorted(selection.segment_indices.tolist()), [1, 2])
        self.assertIn(1, selection.segment_indices.tolist())

    def test_the_downstream_end_ignores_how_the_segment_was_written(self) -> None:
        # T2 vai de B3 para B2 no cadastro; a jusante dele está B3, não B2.
        selection = self.select("segment", 2)
        self.assertEqual(selection.bar_indices.tolist(), [3])
        self.assertEqual(selection.start_bar_index, 3)

    def test_the_switch_is_reported_by_its_record_index(self) -> None:
        selection = self.select("segment", 1)
        self.assertEqual(selection.switch_indices.tolist(), [0])

    def test_bars_come_out_in_level_order(self) -> None:
        selection = self.select("bar", 0)
        self.assertEqual(selection.bar_indices.tolist(), [0, 1, 2, 4, 3])
        self.assertEqual(selection.bar_levels.tolist(), [0, 1, 2, 2, 3])

    def test_a_segment_level_is_the_level_of_the_bar_it_feeds(self) -> None:
        selection = self.select("segment", 1)
        levels = dict(
            zip(
                selection.segment_indices.tolist(),
                selection.segment_levels.tolist(),
                strict=True,
            )
        )
        self.assertEqual(levels, {1: 0, 2: 1})

    def test_the_root_selects_the_whole_feeder(self) -> None:
        selection = self.select("bar", 0)
        self.assertEqual(len(selection.bar_indices), 5)
        self.assertEqual(len(selection.segment_indices), 4)

    def test_attached_elements_follow_their_bars(self) -> None:
        loads = make_loads(self.bars, [2, 4, 0], ["10", "20", "30"])
        capacitors = make_capacitors(self.bars, [3, 0])
        generators = make_generators(loads, [0, 2])
        regulators = make_regulators(self.network, [2, 0])
        selection = self.select(
            "segment",
            1,
            loads=loads,
            capacitors=capacitors,
            generators=generators,
            regulators=regulators,
        )
        self.assertEqual(selection.load_indices.tolist(), [0])
        self.assertEqual(selection.capacitor_indices.tolist(), [0])
        self.assertEqual(selection.generator_indices.tolist(), [0])
        self.assertEqual(selection.regulator_indices.tolist(), [0])

    def test_missing_models_give_empty_vectors(self) -> None:
        selection = self.select("bar", 1)
        for name in (
            "load_indices",
            "capacitor_indices",
            "generator_indices",
            "regulator_indices",
        ):
            with self.subTest(name):
                self.assertEqual(getattr(selection, name).size, 0)

    def test_every_vector_is_read_only(self) -> None:
        selection = self.select("bar", 1)
        for name in (
            "bar_indices",
            "bar_levels",
            "segment_indices",
            "segment_levels",
            "switch_indices",
            "boundary_switch_indices",
        ):
            with self.subTest(name):
                self.assertFalse(getattr(selection, name).flags.writeable)

    def test_a_foreign_model_is_refused(self) -> None:
        other_loads = make_loads(make_bars(5), [0], ["1"])
        with self.assertRaises(ValueError):
            self.select("bar", 1, loads=other_loads)

    def test_an_out_of_range_origin_is_refused(self) -> None:
        with self.assertRaises(IndexError):
            self.select("bar", 99)


class OpenSwitchSelectionTests(unittest.TestCase):
    """B0 —T0— B1 —T1(aberta)— B2 —T2— B3."""

    def setUp(self) -> None:
        self.network = make_network(make_bars(4), [0, 1, 2], [1, 2, 3])
        self.switches = make_switches(self.network, [(1, "1", "0")])
        self.orientation = orient(self.network, self.switches)

    def select(self, kind, index, **options):  # noqa: ANN001, ANN201
        return select_downstream(
            self.orientation, DownstreamOrigin(kind, index), **options
        )

    def test_the_region_stops_at_the_open_switch(self) -> None:
        selection = self.select("bar", 0)
        self.assertEqual(sorted(selection.bar_indices.tolist()), [0, 1])
        self.assertEqual(selection.boundary_switch_indices.tolist(), [0])

    def test_an_open_switch_feeds_nothing(self) -> None:
        selection = self.select("segment", 1)
        self.assertEqual(selection.bar_indices.size, 0)
        # O clicado continua na seleção, com a ocorrência explicando o vazio.
        self.assertEqual(selection.segment_indices.tolist(), [1])
        self.assertIn("chave-aberta", issue_kinds(selection))

    def test_beyond_mode_crosses_the_open_switch(self) -> None:
        selection = self.select("bar", 0, beyond_open_switches=True)
        self.assertEqual(sorted(selection.bar_indices.tolist()), [0, 1, 2, 3])
        self.assertEqual(sorted(selection.segment_indices.tolist()), [0, 1, 2])
        self.assertEqual(selection.inner_open_switch_indices.tolist(), [0])
        self.assertEqual(selection.boundary_switch_indices.size, 0)

    def test_beyond_mode_from_the_open_switch_selects_what_it_would_feed(self) -> None:
        selection = self.select("segment", 1, beyond_open_switches=True)
        self.assertEqual(sorted(selection.bar_indices.tolist()), [2, 3])
        self.assertEqual(sorted(selection.segment_indices.tolist()), [1, 2])
        self.assertEqual(selection.issues, ())

    def test_absorbed_bars_keep_the_level_order(self) -> None:
        selection = self.select("bar", 0, beyond_open_switches=True)
        self.assertEqual(selection.bar_levels.tolist(), [0, 1, 2, 3])

    def test_a_bar_beyond_the_open_switch_is_not_fed(self) -> None:
        selection = self.select("bar", 3)
        self.assertEqual(selection.bar_indices.size, 0)
        self.assertIn("barra-nao-alcancada", issue_kinds(selection))

    def test_beyond_mode_from_a_dead_bar_selects_its_island(self) -> None:
        selection = self.select("bar", 2, beyond_open_switches=True)
        self.assertEqual(sorted(selection.bar_indices.tolist()), [2, 3])


class NeighbourFeederTests(unittest.TestCase):
    """Dois alimentadores unidos por uma chave NA, e uma ilha morta de lado.

    Alimentador 1: B0 —T0— B1 —T1— B2
    Alimentador 2: B5 —T4— B4 —T3— B3
    Chave NA (aberta) T2 entre B2 e B3.
    Ilha morta: B2 —T5(aberta)— B6 —T6— B7
    """

    def setUp(self) -> None:
        self.network = make_network(
            make_bars(8),
            [0, 1, 2, 3, 4, 2, 6],
            [1, 2, 3, 4, 5, 6, 7],
        )
        self.switches = make_switches(self.network, [(2, "1", "0"), (5, "1", "0")])
        self.orientation = orient(self.network, self.switches, roots=("B0", "B5"))

    def test_beyond_mode_never_leaks_into_the_neighbour_feeder(self) -> None:
        """O teste que prova a regra ``depth < 0``.

        A ponta de fora da NA (B3) é alimentada pelo alimentador 2; a região do
        alimentador 1 para nela, mesmo no modo que atravessa chaves abertas.
        """

        selection = select_downstream(
            self.orientation,
            DownstreamOrigin("bar", 1),
            beyond_open_switches=True,
        )
        bars = set(selection.bar_indices.tolist())
        self.assertTrue(bars.isdisjoint({3, 4, 5}))
        self.assertEqual(bars, {1, 2, 6, 7})
        # A NA fica na fronteira; a chave da ilha morta foi atravessada.
        self.assertEqual(selection.boundary_switch_indices.tolist(), [0])
        self.assertEqual(selection.inner_open_switch_indices.tolist(), [1])

    def test_without_beyond_both_open_switches_bound_the_region(self) -> None:
        selection = select_downstream(self.orientation, DownstreamOrigin("bar", 1))
        self.assertEqual(sorted(selection.bar_indices.tolist()), [1, 2])
        self.assertEqual(sorted(selection.boundary_switch_indices.tolist()), [0, 1])

    def test_an_open_switch_between_two_fed_sides_feeds_nothing(self) -> None:
        selection = select_downstream(
            self.orientation,
            DownstreamOrigin("segment", 2),
            beyond_open_switches=True,
        )
        self.assertEqual(selection.bar_indices.size, 0)
        self.assertIn("chave-aberta-entre-alimentadas", issue_kinds(selection))


class LoopSelectionTests(unittest.TestCase):
    def test_a_chord_has_nothing_downstream(self) -> None:
        """Abrir uma corda não desconecta ninguém: a resposta certa é 'nada'."""

        # Anel B0 —T0— B1 —T1— B2 —T2— B3 —T3— B0; T2 fica corda.
        network = make_network(make_bars(4), [0, 1, 2, 3], [1, 2, 3, 0])
        orientation = orient(network)
        chord = int(orientation.chord_segment_indices[0])
        selection = select_downstream(orientation, DownstreamOrigin("segment", chord))
        self.assertEqual(selection.bar_indices.size, 0)
        self.assertEqual(selection.segment_indices.tolist(), [chord])
        self.assertIn("corda", issue_kinds(selection))

    def test_a_boundary_chord_is_reported_and_left_out(self) -> None:
        # Mesmo anel: a jusante de B1 é {B1, B2}, e a corda T2 liga B2 a B3, que
        # está fora — a região é alimentada também por ali.
        network = make_network(make_bars(4), [0, 1, 2, 3], [1, 2, 3, 0])
        orientation = orient(network)
        selection = select_downstream(orientation, DownstreamOrigin("bar", 1))
        self.assertEqual(sorted(selection.bar_indices.tolist()), [1, 2])
        self.assertEqual(selection.boundary_chord_segment_indices.tolist(), [2])
        self.assertNotIn(2, selection.segment_indices.tolist())
        self.assertIn("corda-de-fronteira", issue_kinds(selection))

    def test_an_internal_chord_is_part_of_the_region(self) -> None:
        # B0 —T0— B1; B1 —T1— B2; B1 —T2— B3; B2 —T3— B3 (corda interna).
        network = make_network(make_bars(4), [0, 1, 1, 2], [1, 2, 3, 3])
        orientation = orient(network)
        selection = select_downstream(orientation, DownstreamOrigin("bar", 1))
        self.assertEqual(selection.internal_chord_segment_indices.tolist(), [3])
        self.assertIn(3, selection.segment_indices.tolist())
        self.assertEqual(selection.issues, ())


class CancellationTests(unittest.TestCase):
    def test_cancelling_interrupts(self) -> None:
        count = 5_000
        network = make_network(
            make_bars(count), list(range(count - 1)), list(range(1, count))
        )
        orientation = orient(network)
        with self.assertRaises(InterruptedError):
            select_downstream(
                orientation,
                DownstreamOrigin("bar", 0),
                cancel_check=lambda: True,
            )


if __name__ == "__main__":
    unittest.main()
