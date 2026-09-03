from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from circuit_viewer.circuit_import import (
    SubstationLink,
    load_circuits_csv,
    parse_circuit_rows,
)
from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    CircuitVisibilityController,
    LineNetworkModel,
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
    ids: list[str] | None = None,
) -> LineNetworkModel:
    count = len(starts)
    return LineNetworkModel(
        bars,
        ids or [f"T{index}" for index in range(count)],
        [""] * count,
        ["ABC"] * count,
        starts,
        ends,
        [""] * count,
        [""] * count,
        [""] * count,
        [10.0] * count,
    )


def make_switches(
    network: LineNetworkModel,
    segment_indices: list[int],
    circuit_ids: list[str],
    states: list[str],
) -> SwitchModel:
    count = len(segment_indices)
    return SwitchModel(
        network,
        [f"CH{index}" for index in range(count)],
        ["TIPO"] * count,
        circuit_ids,
        segment_indices,
        [""] * count,
        states,
        ["1"] * count,
        [""] * count,
        [""] * count,
        [""] * count,
    )


class CircuitTopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = make_bars(6)
        self.network = make_network(
            self.bars,
            [0, 1, 2, 1, 4],
            [1, 2, 3, 4, 5],
            ["T0", "K1", "T2", "K2", "T4"],
        )

    def test_bfs_discovers_only_common_segments_and_respects_circuit_id(self) -> None:
        switches = make_switches(self.network, [1, 3], ["C1", "C2"], ["1", "1"])
        catalog = CircuitCatalogModel.build(
            self.network,
            switches,
            [
                CircuitDefinition("C1", "B0", "Circuito 1", "13.8"),
                CircuitDefinition("C2", "B4", "Circuito 2", "34.5"),
            ],
        )

        first = catalog.membership(0)
        self.assertEqual(set(first.bar_indices), {0, 1, 2, 3})
        self.assertEqual(set(first.common_segment_indices), {0, 2})
        self.assertEqual(set(first.switch_segment_indices), {1})
        self.assertEqual(set(first.segment_indices), {0, 1, 2})

        second = catalog.membership(1)
        self.assertEqual(set(second.bar_indices), {0, 1, 4, 5})
        self.assertEqual(set(second.common_segment_indices), {0, 4})
        self.assertEqual(set(second.switch_segment_indices), {3})
        self.assertEqual(set(second.segment_indices), {0, 3, 4})

    def test_open_switch_is_directly_associated_but_blocks_traversal(self) -> None:
        switches = make_switches(self.network, [1], ["C1"], ["0"])
        catalog = CircuitCatalogModel.build(
            self.network,
            switches,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        membership = catalog.membership(0)
        self.assertEqual(set(membership.bar_indices), {0, 1, 4, 5})
        self.assertEqual(set(membership.common_segment_indices), {0, 3, 4})
        self.assertEqual(set(membership.switch_segment_indices), {1})

    def test_unknown_switch_circuit_and_invalid_state_warn_and_block(self) -> None:
        switches = make_switches(self.network, [1, 3], ["C1", "INEXISTENTE"], ["x", "1"])
        catalog = CircuitCatalogModel.build(
            self.network,
            switches,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        membership = catalog.membership(0)
        self.assertEqual(set(membership.bar_indices), {0, 1})
        self.assertEqual(set(membership.switch_segment_indices), {1})
        self.assertEqual(len(catalog.topology_warnings), 2)
        self.assertTrue(any("ESTADO" in value for value in catalog.topology_warnings))
        self.assertTrue(any("INEXISTENTE" in value for value in catalog.topology_warnings))

    def test_switch_is_directly_associated_even_outside_reachable_component(self) -> None:
        switches = make_switches(
            self.network,
            [3, 4],
            ["C2", "C1"],
            ["1", "0"],
        )
        catalog = CircuitCatalogModel.build(
            self.network,
            switches,
            [
                CircuitDefinition("C1", "B0", "", ""),
                CircuitDefinition("C2", "B4", "", ""),
            ],
        )
        first = catalog.membership(0)
        self.assertNotIn(4, first.bar_indices)
        self.assertNotIn(5, first.bar_indices)
        self.assertEqual(set(first.switch_segment_indices), {4})
        self.assertIn(4, first.segment_indices)

    def test_self_loop_and_cycle_are_visited_once(self) -> None:
        bars = make_bars(3)
        network = make_network(bars, [0, 0, 1, 2], [0, 1, 2, 0])
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C", "B0", "", "")],
        )
        membership = catalog.membership(0)
        self.assertEqual(set(membership.bar_indices), {0, 1, 2})
        self.assertEqual(set(membership.common_segment_indices), {0, 1, 2, 3})


class CircuitImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.network = make_network(make_bars(4), [0, 1], [1, 2])

    def write(self, name: str, content: str, encoding: str = "utf-8") -> Path:
        path = Path(self.temp.name) / name
        path.write_text(content, encoding=encoding)
        return path

    def test_flexible_header_extra_columns_duplicates_and_invalid_roots(self) -> None:
        path = self.write(
            "circuitos.csv",
            "EXTRA;VNOM;CIRC_ID;CODIGO;BARRA_ID\n"
            "x;13,8;C1;ALIMENTADOR;B0\n"
            "x;99;C1;DUPLICADO;B1\n"
            "x;34.5;C2;;B2\n"
            "x;1;C3;INVALIDO;BX\n",
        )
        result = load_circuits_csv(path, self.network)
        self.assertEqual(result.valid_rows, 2)
        self.assertEqual(result.invalid_rows, 2)
        self.assertEqual(result.model.definition(0).nominal_voltage, "13,8")
        self.assertEqual(result.model.definition(1).code, "")
        self.assertEqual(result.model.index_for_id("C1"), 0)
        self.assertEqual(result.model.index_for_id("C2"), 1)

    def test_cp1252_and_cancellation(self) -> None:
        path = self.write(
            "cp1252.csv",
            "CIRC_ID;BARRA_ID;CODIGO;VNOM\nC1;B0;Ação;13.8\n",
            "cp1252",
        )
        result = load_circuits_csv(path, self.network)
        self.assertEqual(result.encoding, "cp1252")
        self.assertEqual(result.model.definition(0).code, "Ação")

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(CsvImportCancelled):
            load_circuits_csv(path, self.network, cancel_event=cancelled)

    def test_invalid_header_and_no_valid_rows_are_errors(self) -> None:
        missing = self.write("missing.csv", "CIRC_ID;BARRA_ID;CODIGO\nC;B0;X\n")
        with self.assertRaises(CsvImportError):
            load_circuits_csv(missing, self.network)
        invalid = self.write(
            "invalid.csv",
            "CIRC_ID;BARRA_ID;CODIGO;VNOM\nC;INEXISTENTE;X;1\n",
        )
        with self.assertRaises(CsvImportError):
            load_circuits_csv(invalid, self.network)


class CircuitVisibilityTests(unittest.TestCase):
    def test_overlap_uses_union_and_unassigned_elements_remain_visible(self) -> None:
        bars = make_bars(4)
        network = make_network(bars, [0, 2], [1, 3])
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "", ""),
                CircuitDefinition("C2", "B1", "", ""),
            ],
        )
        controller = CircuitVisibilityController(
            catalog, colors=("#112233", "#445566")
        )
        memberships_before = catalog.memberships
        np.testing.assert_array_equal(catalog.overlapping_segment_indices, [0])
        np.testing.assert_array_equal(catalog.circuit_indices_for_segment(0), [0, 1])
        np.testing.assert_array_equal(controller.segment_style_indices, [0, -1])
        with self.assertRaises(ValueError):
            catalog.overlapping_segment_indices[0] = 1
        self.assertTrue(controller.bar_visible_mask.all())
        self.assertTrue(controller.segment_visible_mask.all())

        controller.set_visible(0, False)
        self.assertTrue(controller.bar_visible_mask.all())
        self.assertTrue(controller.segment_visible_mask.all())
        np.testing.assert_array_equal(controller.segment_style_indices, [1, -1])

        controller.set_visible(1, False)
        np.testing.assert_array_equal(
            controller.bar_visible_mask, [False, False, True, True]
        )
        np.testing.assert_array_equal(controller.segment_visible_mask, [False, True])
        np.testing.assert_array_equal(controller.segment_style_indices, [-2, -1])
        self.assertIs(catalog.memberships, memberships_before)

        controller.set_visible(0, True)
        self.assertTrue(controller.bar_visible_mask.all())
        self.assertTrue(controller.segment_visible_mask.all())

    def test_spatial_nearest_ignores_hidden_candidates(self) -> None:
        bars = make_bars(3)
        point_mask = np.array([False, True, True], dtype=np.bool_)
        self.assertIsNone(
            bars.spatial_index.nearest(500_000.0, 8_000_000.0, 1.0, point_mask)
        )
        network = make_network(bars, [0, 1], [1, 2])
        line_mask = np.array([False, True], dtype=np.bool_)
        self.assertIsNone(
            network.spatial_index.nearest(
                500_005.0, 8_000_000.0, 1.0, line_mask
            )
        )


class SubstationLinkTests(unittest.TestCase):
    """A origem do circuito chega pronta; o parser só a repassa."""

    HEADER = ("CIRC_ID", "BARRA_ID", "CODIGO", "VNOM")

    def setUp(self) -> None:
        self.bars = make_bars(3)
        self.network = make_network(self.bars, [0, 1], [1, 2])

    def _parse(self, links=None):  # noqa: ANN001, ANN202
        return parse_circuit_rows(
            self.HEADER,
            [("C1", "B0", "Circuito 1", "13.8")],
            self.network,
            None,
            source_label="teste",
            encoding="utf-8",
            substation_links=links,
        )

    def test_without_links_the_fields_stay_empty(self) -> None:
        # É o caso de qualquer fonte que não seja o banco Access.
        definition = self._parse().model.definition(0)

        self.assertEqual(definition.substation_code, "")
        self.assertEqual(definition.substation_name, "")
        self.assertEqual(definition.transformer_id, "")
        self.assertEqual(definition.transformer_code, "")
        self.assertEqual(definition.transformer_power, "")

    def test_a_link_reaches_the_definition(self) -> None:
        result = self._parse(
            {
                "C1": SubstationLink(
                    substation_code="032",
                    substation_name="SE AGUA BOA",
                    transformer_id="3",
                    transformer_code="53244TRAFO_032",
                    transformer_power="25",
                )
            }
        )

        definition = result.model.definition(0)
        self.assertEqual(definition.substation_code, "032")
        self.assertEqual(definition.substation_name, "SE AGUA BOA")
        self.assertEqual(definition.transformer_id, "3")
        self.assertEqual(definition.transformer_code, "53244TRAFO_032")
        self.assertEqual(definition.transformer_power, "25")
        self.assertEqual(result.issues, ())

    def test_a_reason_is_reported_without_rejecting_the_circuit(self) -> None:
        # Contar como linha inválida mentiria no relatório: o circuito entrou
        # no catálogo, só não trouxe a referência.
        result = self._parse({"C1": SubstationLink(reason="SE_ID '9' sem par")})

        self.assertEqual(len(result.model), 1)
        self.assertEqual(result.invalid_rows, 0)
        self.assertEqual(result.valid_rows, 1)
        self.assertEqual([issue.reason for issue in result.issues], ["SE_ID '9' sem par"])

    def test_a_circuit_absent_from_the_links_is_left_alone(self) -> None:
        result = self._parse({"OUTRO": SubstationLink(substation_code="999")})

        self.assertEqual(result.model.definition(0).substation_code, "")
        self.assertEqual(result.issues, ())


if __name__ == "__main__":
    unittest.main()
