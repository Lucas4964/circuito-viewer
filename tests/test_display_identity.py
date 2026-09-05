from __future__ import annotations

from types import SimpleNamespace
import unittest

import numpy as np

from circuit_viewer.display_identity import (
    UNRESOLVED_CIRCUIT_LABEL,
    build_block_display_identities,
    circuit_display_labels,
)


def _indices(*values: int) -> np.ndarray:
    result = np.asarray(values, dtype=np.intp)
    result.setflags(write=False)
    return result


class _Catalog:
    def __init__(self) -> None:
        self.definitions = (
            SimpleNamespace(circuit_id="364", code="001001"),
            SimpleNamespace(circuit_id="365", code=""),
            SimpleNamespace(circuit_id="366", code="REPETIDO"),
            SimpleNamespace(circuit_id="367", code="REPETIDO"),
        )
        self.memberships = (
            SimpleNamespace(bar_indices=_indices(0, 1)),
            SimpleNamespace(bar_indices=_indices(2)),
            SimpleNamespace(bar_indices=_indices(3)),
            SimpleNamespace(bar_indices=_indices(4)),
        )
        self._by_id = {
            definition.circuit_id: index
            for index, definition in enumerate(self.definitions)
        }

    def index_for_id(self, circuit_id: str) -> int | None:
        return self._by_id.get(circuit_id)

    def definition(self, index: int):  # noqa: ANN201
        return self.definitions[index]


class CircuitDisplayLabelTests(unittest.TestCase):
    def test_code_is_primary_and_bad_codes_fall_back_to_circuit_id(self) -> None:
        labels = circuit_display_labels(_Catalog())

        self.assertEqual(labels[0], "001001")
        self.assertEqual(labels[1], "Sem código (CIRC_ID: 365)")
        self.assertEqual(labels[2], "REPETIDO (CIRC_ID: 366)")
        self.assertEqual(labels[3], "REPETIDO (CIRC_ID: 367)")


class BlockDisplayIdentityTests(unittest.TestCase):
    def test_numbers_restart_per_circuit_and_internal_ids_stay_global(self) -> None:
        records = (
            SimpleNamespace(block_id=1, bar_indices=_indices(0)),
            SimpleNamespace(block_id=2, bar_indices=_indices(1)),
            SimpleNamespace(block_id=3, bar_indices=_indices(2)),
            SimpleNamespace(block_id=4, bar_indices=_indices(3)),
            SimpleNamespace(block_id=5, bar_indices=_indices(4)),
            SimpleNamespace(block_id=6, bar_indices=_indices(9)),
        )
        result = SimpleNamespace(records=records, source_switches=None)

        identities = build_block_display_identities(result, _Catalog())

        self.assertEqual(tuple(identities), (1, 2, 3, 4, 5, 6))
        self.assertEqual(identities[1].graph_label, "001001-1")
        self.assertEqual(identities[2].graph_label, "001001-2")
        self.assertEqual(identities[3].local_number, 1)
        self.assertEqual(
            identities[3].graph_label,
            "365-1",
        )
        self.assertEqual(identities[4].graph_label, "REPETIDO[366]-1")
        self.assertEqual(identities[5].graph_label, "REPETIDO[367]-1")
        self.assertEqual(identities[6].circuit_label, UNRESOLVED_CIRCUIT_LABEL)
        self.assertEqual(identities[6].graph_label, "SEM-CIRCUITO-1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
