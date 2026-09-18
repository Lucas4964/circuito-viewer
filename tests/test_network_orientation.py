from __future__ import annotations

import unittest

import numpy as np

from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    NetworkTopology,
)
from circuit_viewer.network_orientation import (
    SEGMENT_CHORD,
    SEGMENT_OPEN,
    SEGMENT_TREE,
    SEGMENT_UNREACHED,
    build_orientation,
    conducting_segments,
)

from test_block_analysis import make_bars, make_network, make_switches


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


def kinds(orientation) -> set[str]:  # noqa: ANN001
    return {issue.kind for issue in orientation.issues}


class RadialTests(unittest.TestCase):
    """B0 —T0— B1 —T1— B2 —T2— B3, com B1 —T3— B4 em derivação."""

    def setUp(self) -> None:
        bars = make_bars(5)
        # T2 escrito ao contrário de propósito: a orientação não pode depender
        # de start/end dizerem montante→jusante.
        self.network = make_network(bars, [0, 1, 3, 1], [1, 2, 2, 4])
        self.orientation = orient(self.network)

    def test_parents_and_depths_follow_the_root(self) -> None:
        self.assertEqual(self.orientation.parent_bar.tolist(), [-1, 0, 1, 2, 1])
        self.assertEqual(self.orientation.parent_segment.tolist(), [-1, 0, 1, 2, 3])
        self.assertEqual(self.orientation.depth.tolist(), [0, 1, 2, 3, 2])

    def test_the_child_end_ignores_how_the_segment_was_written(self) -> None:
        # T2 vai de B3 para B2 no cadastro, mas B3 é a filha.
        self.assertEqual(self.orientation.tree_child_bar.tolist(), [1, 2, 3, 4])

    def test_every_segment_is_a_tree_edge(self) -> None:
        self.assertTrue((self.orientation.segment_role == SEGMENT_TREE).all())
        self.assertEqual(self.orientation.chord_segment_indices.size, 0)
        self.assertEqual(self.orientation.issues, ())

    def test_children_come_out_in_bar_order(self) -> None:
        bars, feeding = self.orientation.children_of(1)
        self.assertEqual(bars.tolist(), [2, 4])
        self.assertEqual(feeding.tolist(), [1, 3])
        bars, _ = self.orientation.children_of(3)
        self.assertEqual(bars.tolist(), [])

    def test_every_bar_belongs_to_the_only_circuit(self) -> None:
        self.assertEqual(self.orientation.root_of.tolist(), [0] * 5)
        self.assertEqual(self.orientation.circuit_of_bar.tolist(), [0] * 5)
        self.assertEqual(self.orientation.root_bar_indices.tolist(), [0])

    def test_every_vector_is_read_only(self) -> None:
        for name in (
            "parent_bar",
            "parent_segment",
            "depth",
            "root_of",
            "circuit_of_bar",
            "children_offsets",
            "children_bars",
            "children_segments",
            "segment_role",
            "tree_child_bar",
            "chord_segment_indices",
            "root_bar_indices",
        ):
            with self.subTest(name):
                self.assertFalse(getattr(self.orientation, name).flags.writeable)

    def test_the_orientation_refuses_a_foreign_catalog(self) -> None:
        other = make_network(make_bars(5), [0, 1, 3, 1], [1, 2, 2, 4])
        catalog = CircuitCatalogModel.build(
            other, None, [CircuitDefinition("C0", "B0", "", "")]
        )
        with self.assertRaises(ValueError):
            build_orientation(NetworkTopology(self.network), catalog)


class OpenSwitchTests(unittest.TestCase):
    """B0 —T0— B1 —T1(chave)— B2 —T2— B3."""

    def setUp(self) -> None:
        self.network = make_network(make_bars(4), [0, 1, 2], [1, 2, 3])

    def test_an_open_switch_leaves_the_far_side_unreached(self) -> None:
        switches = make_switches(self.network, [(1, "1", "0")])
        orientation = orient(self.network, switches)
        self.assertEqual(orientation.depth.tolist(), [0, 1, -1, -1])
        self.assertEqual(orientation.segment_role[1], SEGMENT_OPEN)
        self.assertEqual(orientation.segment_role[2], SEGMENT_UNREACHED)
        self.assertEqual(orientation.unreachable_bar_count, 2)
        self.assertIn("ilha-sem-fonte", kinds(orientation))

    def test_a_closed_switch_conducts(self) -> None:
        switches = make_switches(self.network, [(1, "1", "1")])
        orientation = orient(self.network, switches)
        self.assertEqual(orientation.depth.tolist(), [0, 1, 2, 3])
        self.assertEqual(orientation.segment_role[1], SEGMENT_TREE)

    def test_an_unknown_state_does_not_conduct_and_is_reported(self) -> None:
        # O mesmo predicado do trace: só "1" conduz.
        switches = make_switches(self.network, [(1, "1", "1.0")])
        orientation = orient(self.network, switches)
        self.assertEqual(orientation.segment_role[1], SEGMENT_OPEN)
        self.assertIn("estado-invalido", kinds(orientation))

    def test_the_conduction_mask_is_the_trace_predicate(self) -> None:
        switches = make_switches(self.network, [(1, "1", " 1 ")])
        mask = conducting_segments(NetworkTopology(self.network, switches))
        self.assertEqual(mask.tolist(), [True, True, True])

    def test_the_orientation_ignores_the_switch_owner(self) -> None:
        """Chave fechada conduz, seja de quem for — diferente do ``trace``."""

        switches = make_switches(self.network, [(1, "1", "1")])
        # make_switches marca as chaves como do circuito "C1"; o único circuito
        # aqui é "C0", e mesmo assim a chave conduz.
        orientation = orient(self.network, switches)
        self.assertEqual(orientation.depth[3], 3)


class LoopTests(unittest.TestCase):
    def test_a_ring_leaves_exactly_one_chord(self) -> None:
        # B0 —T0— B1 —T1— B2 —T2— B3 —T3— B0
        network = make_network(make_bars(4), [0, 1, 2, 3], [1, 2, 3, 0])
        orientation = orient(network)
        self.assertEqual(orientation.chord_segment_indices.size, 1)
        tree = np.count_nonzero(orientation.segment_role == SEGMENT_TREE)
        self.assertEqual(tree, 3)
        self.assertIn("corda", kinds(orientation))

    def test_a_tree_edge_is_never_demoted_to_chord(self) -> None:
        # A volta pela incidência reversa não pode transformar a aresta que
        # descobriu a barra em corda; sem a guarda, toda aresta viraria corda.
        network = make_network(make_bars(3), [0, 1], [1, 2])
        orientation = orient(network)
        self.assertEqual(orientation.chord_segment_indices.size, 0)

    def test_parallel_segments_make_one_tree_edge_and_one_chord(self) -> None:
        network = make_network(make_bars(2), [0, 0], [1, 1])
        orientation = orient(network)
        self.assertEqual(orientation.segment_role.tolist(), [SEGMENT_TREE, SEGMENT_CHORD])
        self.assertEqual(orientation.parent_segment[1], 0)

    def test_a_self_loop_is_a_chord(self) -> None:
        network = make_network(make_bars(2), [0, 1], [1, 1])
        orientation = orient(network)
        self.assertEqual(orientation.segment_role[1], SEGMENT_CHORD)


class MultiSourceTests(unittest.TestCase):
    def test_two_roots_in_one_island_split_by_distance(self) -> None:
        # B0 —T0— B1 —T1— B2 —T2— B3 —T3— B4, raízes em B0 e B4.
        network = make_network(make_bars(5), [0, 1, 2, 3], [1, 2, 3, 4])
        orientation = orient(network, roots=("B0", "B4"))
        self.assertEqual(orientation.root_of.tolist(), [0, 0, 0, 4, 4])
        self.assertEqual(orientation.circuit_of_bar.tolist(), [0, 0, 0, 1, 1])
        self.assertEqual(len(orientation.multi_source_contacts), 1)
        self.assertIn("fonte-multipla", kinds(orientation))

    def test_the_contact_speaks_the_coupled_feeder_vocabulary(self) -> None:
        network = make_network(make_bars(3), [0, 1], [1, 2])
        orientation = orient(network, roots=("B0", "B2"))
        message = next(
            issue.message
            for issue in orientation.issues
            if issue.kind == "fonte-multipla"
        )
        self.assertIn("Alimentadores unidos por conexões condutoras", message)

    def test_a_tie_goes_to_the_first_circuit_of_the_catalog(self) -> None:
        # B1 fica a um salto das duas raízes.
        network = make_network(make_bars(3), [0, 1], [1, 2])
        orientation = orient(network, roots=("B0", "B2"))
        self.assertEqual(orientation.circuit_of_bar[1], 0)

    def test_a_shared_root_is_reported_once(self) -> None:
        network = make_network(make_bars(2), [0], [1])
        orientation = orient(network, roots=("B0", "B0"))
        self.assertEqual(orientation.root_bar_indices.tolist(), [0])
        self.assertIn("raiz-duplicada", kinds(orientation))

    def test_separate_feeders_have_no_contact(self) -> None:
        network = make_network(make_bars(4), [0, 2], [1, 3])
        orientation = orient(network, roots=("B0", "B2"))
        self.assertEqual(orientation.multi_source_contacts, ())
        self.assertEqual(orientation.root_of.tolist(), [0, 0, 2, 2])


class IsolatedBarTests(unittest.TestCase):
    def test_a_bar_without_segments_is_not_a_failure(self) -> None:
        network = make_network(make_bars(3), [0], [1])
        orientation = orient(network)
        self.assertEqual(orientation.depth[2], -1)
        self.assertEqual(orientation.unreachable_bar_count, 0)
        self.assertEqual(orientation.issues, ())


class CancellationTests(unittest.TestCase):
    def test_cancelling_interrupts(self) -> None:
        count = 5_000
        network = make_network(
            make_bars(count), list(range(count - 1)), list(range(1, count))
        )
        catalog = CircuitCatalogModel.build(
            network, None, [CircuitDefinition("C0", "B0", "", "")]
        )
        with self.assertRaises(InterruptedError):
            build_orientation(
                NetworkTopology(network), catalog, cancel_check=lambda: True
            )


if __name__ == "__main__":
    unittest.main()
