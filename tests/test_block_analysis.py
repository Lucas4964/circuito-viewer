from __future__ import annotations

import unittest

import numpy as np

from circuit_viewer.block_analysis import (
    BlockRecord,
    analyze_blocks,
    boundary_segment_mask,
)
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    SwitchModel,
    UtmCrs,
)


def make_bars(count: int) -> CircuitModel:
    return CircuitModel(
        [f"B{index}" for index in range(count)],
        [f"CB{index}" for index in range(count)],
        [500_000.0 + index * 10.0 for index in range(count)],
        [8_000_000.0] * count,
        UtmCrs(21, northern=False),
    )


def make_network(
    bars: CircuitModel,
    starts: list[int],
    ends: list[int],
    lengths: list[float] | None = None,
) -> LineNetworkModel:
    count = len(starts)
    return LineNetworkModel(
        bars,
        [f"T{index}" for index in range(count)],
        [f"CT{index}" for index in range(count)],
        ["13"] * count,
        starts,
        ends,
        [""] * count,
        [""] * count,
        [""] * count,
        [100.0] * count if lengths is None else lengths,
    )


def make_switches(
    network: LineNetworkModel,
    entries: list[tuple[int, str, str]],
) -> SwitchModel:
    """``entries`` são triplas ``(índice do trecho, MANOBRAVEL, ESTADO)``."""

    size = len(entries)
    return SwitchModel(
        network,
        [f"CH{index}" for index in range(size)],
        ["4"] * size,
        ["C1"] * size,
        [segment for segment, _, _ in entries],
        [f"COD-CH{index}" for index in range(size)],
        [state for _, _, state in entries],
        [state for _, _, state in entries],
        ["400"] * size,
        [""] * size,
        [""] * size,
        type_names=["Chave"] * size,
        switchable_values=[switchable for _, switchable, _ in entries],
    )


def make_loads(bars: CircuitModel, bar_indices: list[int], snom: list[str]) -> LoadModel:
    size = len(bar_indices)
    return LoadModel(
        bars,
        [f"L{index}" for index in range(size)],
        bar_indices,
        [""] * size,
        [f"CL{index}" for index in range(size)],
        snom,
        snom,
        ["220"] * size,
        ["13"] * size,
        ["Y"] * size,
    )


def make_catalog(network: LineNetworkModel, switches: SwitchModel | None = None):  # noqa: ANN201
    return CircuitCatalogModel.build(
        network,
        switches,
        [CircuitDefinition("C1", "B0", "ALIM-1", "13.8")],
    )


class BoundaryTests(unittest.TestCase):
    """Só chave manobrável delimita, e o estado dela não entra na conta."""

    def setUp(self) -> None:
        # B0 —T0— B1 —T1(chave)— B2 —T2— B3
        self.bars = make_bars(4)
        self.network = make_network(self.bars, [0, 1, 2], [1, 2, 3])

    def _blocks(self, switchable: str, state: str):  # noqa: ANN202
        switches = make_switches(self.network, [(1, switchable, state)])
        return analyze_blocks(make_catalog(self.network, switches), switches)

    def test_a_switchable_switch_splits_the_network(self) -> None:
        result = self._blocks("1", "1")

        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.switchable_switch_count, 1)
        self.assertEqual(
            sorted(record.segment_count for record in result.records),
            [1, 1],
        )

    def test_an_open_switchable_switch_splits_just_the_same(self) -> None:
        # O estado não entra: o bloco é o que se isola operando a chave, e ela
        # é fronteira aberta ou fechada.
        aberta = self._blocks("1", "0")
        fechada = self._blocks("1", "1")

        self.assertEqual(len(aberta.records), len(fechada.records))
        self.assertEqual(
            [record.bar_count for record in aberta.records],
            [record.bar_count for record in fechada.records],
        )

    def test_a_fuse_does_not_split(self) -> None:
        result = self._blocks("0", "1")

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.switchable_switch_count, 0)
        self.assertEqual(result.records[0].bar_count, 4)

    def test_an_open_fuse_does_not_split_either(self) -> None:
        # É a decisão central da ferramenta: a região além do fusível aberto
        # continua no mesmo bloco, porque nenhuma manobra a separa.
        result = self._blocks("0", "0")

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].bar_count, 4)

    def test_a_type_without_a_declared_answer_does_not_split(self) -> None:
        # switchable vazio é "não declarado", que não é "manobrável".
        result = self._blocks("", "1")

        self.assertEqual(len(result.records), 1)

    def test_the_mask_marks_only_the_switchable_segment(self) -> None:
        switches = make_switches(self.network, [(1, "1", "1"), (2, "0", "1")])

        mask = boundary_segment_mask(self.network, switches)

        self.assertEqual(list(mask), [False, True, False])

    def test_without_switches_the_whole_network_is_one_block(self) -> None:
        result = analyze_blocks(make_catalog(self.network))

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].segment_count, 3)
        # A situação é relatada em vez de passar por resultado normal.
        self.assertEqual([issue.kind for issue in result.issues], ["sem-fronteira"])


class BlockContentTests(unittest.TestCase):
    def setUp(self) -> None:
        # B0 —T0— B1 —T1(manobrável)— B2 —T2— B3, cargas em B1 e B3.
        self.bars = make_bars(4)
        self.network = make_network(
            self.bars, [0, 1, 2], [1, 2, 3], lengths=[30.0, 5.0, 70.0]
        )
        self.switches = make_switches(self.network, [(1, "1", "1")])
        self.loads = make_loads(self.bars, [1, 3], ["10", "25,5"])
        self.result = analyze_blocks(
            make_catalog(self.network, self.switches), self.switches, self.loads
        )

    def _block_with(self, bar_index: int) -> BlockRecord:
        return next(
            record
            for record in self.result.records
            if bar_index in set(record.bar_indices.tolist())
        )

    def test_the_counts_describe_each_side(self) -> None:
        upstream = self._block_with(0)
        downstream = self._block_with(3)

        self.assertEqual((upstream.bar_count, upstream.segment_count), (2, 1))
        self.assertEqual((downstream.bar_count, downstream.segment_count), (2, 1))
        self.assertEqual(upstream.load_count, 1)
        self.assertEqual(downstream.load_count, 1)

    def test_the_boundary_switch_belongs_to_both_blocks(self) -> None:
        # É a mesma manobra vista dos dois lados; cada bloco precisa vê-la.
        for record in self.result.records:
            self.assertEqual(record.boundary_switch_codes, ("COD-CH0",))
            self.assertTrue(record.is_dead_end)

    def test_the_boundary_segment_belongs_to_no_block(self) -> None:
        # A chave é fronteira, não conteúdo: T1 não entra em bloco nenhum.
        owned = {
            index
            for record in self.result.records
            for index in record.segment_indices.tolist()
        }
        self.assertEqual(owned, {0, 2})

    def test_power_and_length_are_summed(self) -> None:
        upstream = self._block_with(0)
        downstream = self._block_with(3)

        self.assertAlmostEqual(upstream.total_power, 10.0)
        # Vírgula decimal do cadastro é aceita.
        self.assertAlmostEqual(downstream.total_power, 25.5)
        # COMPR em metros, como na tabela de ramais; o trecho de chave fica fora.
        self.assertAlmostEqual(upstream.total_length, 30.0)
        self.assertAlmostEqual(downstream.total_length, 70.0)

    def test_only_the_block_with_the_root_bar_is_the_source(self) -> None:
        self.assertTrue(self._block_with(0).contains_source)
        self.assertFalse(self._block_with(3).contains_source)

    def test_the_ids_form_a_sequence(self) -> None:
        self.assertEqual(
            [record.block_id for record in self.result.records],
            list(range(1, len(self.result.records) + 1)),
        )


class DegenerateBlockTests(unittest.TestCase):
    def test_a_block_between_two_switches_has_no_segment(self) -> None:
        # B0 —T0(manobrável)— B1 —T1(manobrável)— B2: B1 fica sozinha, num bloco
        # sem trecho nenhum. Não pode quebrar nem sumir.
        bars = make_bars(3)
        network = make_network(bars, [0, 1], [1, 2])
        switches = make_switches(network, [(0, "1", "1"), (1, "1", "1")])

        result = analyze_blocks(make_catalog(network, switches), switches)

        self.assertEqual(len(result.records), 3)
        middle = next(
            record
            for record in result.records
            if 1 in set(record.bar_indices.tolist())
        )
        self.assertEqual(middle.segment_count, 0)
        self.assertEqual(middle.bar_count, 1)
        self.assertIsNone(middle.total_length)
        # Duas fronteiras: dá para isolar por qualquer um dos dois lados.
        self.assertEqual(middle.boundary_count, 2)
        self.assertFalse(middle.is_dead_end)

    def test_a_block_without_load_reports_no_power(self) -> None:
        # Dois blocos, carga só num deles: o outro precisa sair com 0 cargas e
        # sem kVA, sem herdar nada do vizinho.
        bars = make_bars(4)
        network = make_network(bars, [0, 1, 2], [1, 2, 3])
        switches = make_switches(network, [(1, "1", "1")])
        loads = make_loads(bars, [0], ["10"])

        result = analyze_blocks(
            make_catalog(network, switches), switches, loads
        )

        vazio = next(
            record
            for record in result.records
            if 3 in set(record.bar_indices.tolist())
        )
        self.assertEqual(vazio.load_count, 0)
        # None, não zero: não há carga, então não há kVA a afirmar.
        self.assertIsNone(vazio.total_power)

    def test_a_non_numeric_snom_is_skipped_without_losing_the_others(self) -> None:
        bars = make_bars(2)
        network = make_network(bars, [0], [1])
        loads = make_loads(bars, [0, 1], ["", "12"])

        result = analyze_blocks(make_catalog(network), None, loads)

        self.assertEqual(result.records[0].load_count, 2)
        self.assertAlmostEqual(result.records[0].total_power, 12.0)

    def test_mismatched_sources_are_refused(self) -> None:
        network = make_network(make_bars(2), [0], [1])
        other = make_network(make_bars(2), [0], [1])
        switches = make_switches(other, [(0, "1", "1")])

        with self.assertRaises(ValueError):
            analyze_blocks(make_catalog(network), switches)


class BlockRecordTests(unittest.TestCase):
    def _record(self, **overrides):  # noqa: ANN202
        empty = np.zeros(0, dtype=np.intp)
        empty.setflags(write=False)
        bars = np.asarray([0], dtype=np.intp)
        bars.setflags(write=False)
        values = {
            "block_id": 1,
            "bar_indices": bars,
            "segment_indices": empty,
            "load_indices": empty,
            "boundary_switch_indices": empty,
            "boundary_switch_codes": (),
            "total_power": None,
            "total_length": None,
            "contains_source": False,
        }
        values.update(overrides)
        return BlockRecord(**values)

    def test_a_writable_vector_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._record(segment_indices=np.zeros(2, dtype=np.intp))

    def test_a_block_without_bars_is_refused(self) -> None:
        empty = np.zeros(0, dtype=np.intp)
        empty.setflags(write=False)
        with self.assertRaises(ValueError):
            self._record(bar_indices=empty)

    def test_codes_must_match_the_boundary_count(self) -> None:
        with self.assertRaises(ValueError):
            self._record(boundary_switch_codes=("CH1",))


if __name__ == "__main__":
    unittest.main()
