from __future__ import annotations

from decimal import Decimal
import unittest

from circuit_viewer import analyze_branches, measure_branch_powers
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    UtmCrs,
)
from circuit_viewer.opendss_powerflow import (
    PowerFlowResult,
    SegmentCurrents,
    SegmentPowers,
)
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


# Ao contrário das demais suítes de ramais, esta precisa do DSS de cada fase:
# é dele que sai a correspondência nó → letra usada na leitura do fluxo.
PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("d", "D", 1, "1"),
        PhaseMappingEntry("e", "E", 1, "2"),
        PhaseMappingEntry("f", "F", 1, "3"),
        PhaseMappingEntry("de", "DE", 2, "1.2"),
        PhaseMappingEntry("def", "DEF", 3, "1.2.3"),
    )
)


def make_sources(
    *,
    branch_phases: str = "d",
    branch_start: int = 1,
    branch_end: int = 3,
    extra_segment: tuple[str, int, int] | None = None,
):
    """Tronco trifásico B0-B1-B2 e um ramal ligado à barra B1.

    ``branch_start``/``branch_end`` invertem a orientação do trecho de conexão
    sem mudar a topologia, que é o que separa os dois sinais possíveis.
    """

    bars = CircuitModel(
        ["B0", "B1", "B2", "B3", "B4"],
        ["CB0", "CB1", "CB2", "CB3", "CB4"],
        [0.0, 10.0, 20.0, 10.0, 10.0],
        [0.0, 0.0, 0.0, -10.0, -20.0],
        UtmCrs(21, northern=False),
    )
    segment_ids = ["T0", "T1", "T2", "T3"]
    phases = ["DEF", "DEF", branch_phases, branch_phases]
    starts = [0, 1, branch_start, 3]
    ends = [1, 2, branch_end, 4]
    if extra_segment is not None:
        phase, start, end = extra_segment
        segment_ids.append("T4")
        phases.append(phase)
        starts.append(start)
        ends.append(end)
    count = len(segment_ids)
    segments = LineNetworkModel(
        bars,
        segment_ids,
        [f"C{value}" for value in segment_ids],
        phases,
        starts,
        ends,
        [""] * count,
        [""] * count,
        [""] * count,
        [10.0] * count,
    )
    catalog = CircuitCatalogModel.build(
        segments,
        None,
        [CircuitDefinition("C1", "B0", "", "")],
    )
    loads = LoadModel(
        bars,
        ["L1", "L2"],
        [3, 4],
        [""] * 2,
        [""] * 2,
        ["1", "1"],
        ["1", "1"],
        [""] * 2,
        [""] * 2,
        [""] * 2,
    )
    return bars, segments, catalog, loads


def powers(nodes, active, reactive) -> SegmentPowers:
    return SegmentPowers(
        nodes=tuple(nodes),
        active=tuple(tuple(row) for row in active),
        reactive=tuple(tuple(row) for row in reactive),
    )


def currents(nodes, magnitudes, angles=()) -> SegmentCurrents:
    return SegmentCurrents(
        nodes=tuple(nodes),
        magnitudes=tuple(tuple(row) for row in magnitudes),
        angles=tuple(tuple(row) for row in angles),
    )


def make_power_flow(
    catalog,
    loads,
    segment_powers,
    segment_currents=None,
) -> PowerFlowResult:
    return PowerFlowResult(
        catalog=catalog,
        cables=None,
        phase_configuration=PHASES,
        loads=loads,
        patterns=None,
        step_count=4,
        segment_powers=segment_powers,
        segment_currents={} if segment_currents is None else segment_currents,
        solved_circuits=("C1",),
    )


class MeasureBranchCurrentsTests(unittest.TestCase):
    """A corrente do ramal vem medida, não derivada da potência.

    Derivá-la exigiria a tensão da barra; com a nominal o número sairia menor
    na proporção da queda, justamente onde a queda importa.
    """

    def _branch_and_powers(self):  # noqa: ANN202
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        return (
            catalog,
            loads,
            branches,
            branch,
            {
                branch.first_segment_index: powers(
                    (1,),
                    ((10.0,), (20.0,), (30.0,), (40.0,)),
                    ((1.0,), (2.0,), (3.0,), (4.0,)),
                )
            },
        )

    def test_the_largest_magnitude_among_the_four_patamares(self) -> None:
        catalog, loads, branches, branch, segment_powers = self._branch_and_powers()
        flow = make_power_flow(
            catalog,
            loads,
            segment_powers,
            {
                branch.first_segment_index: currents(
                    (1,),
                    ((3.5,), (9.25,), (2.0,), (7.0,)),
                )
            },
        )

        _, failures, measured = measure_branch_powers(branches, flow)

        self.assertEqual(failures, {})
        self.assertEqual(measured[branch.branch_id], Decimal("9.25"))

    def test_a_branch_without_read_current_keeps_its_power(self) -> None:
        # Corrente ausente não é medição ausente: a equivalência precisa da
        # potência, e ela veio.
        catalog, loads, branches, branch, segment_powers = self._branch_and_powers()
        flow = make_power_flow(catalog, loads, segment_powers)

        patterns, failures, measured = measure_branch_powers(branches, flow)

        self.assertEqual(failures, {})
        self.assertIn(branch.branch_id, patterns)
        self.assertEqual(measured, {})

    def test_two_connections_add_as_phasors_not_as_magnitudes(self) -> None:
        # Duas entradas em oposição de fase se cancelam; somar módulos daria 4 A
        # onde a rede vê zero.
        _, _, catalog, loads = make_sources(extra_segment=("d", 1, 4))
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        self.assertEqual(branch.trunk_connection_count, 2)
        connection_ids = ("T2", "T4")
        indices = [
            index
            for index, segment_id in enumerate(catalog.segments.segment_ids)
            if segment_id in connection_ids
        ]
        flow = make_power_flow(
            catalog,
            loads,
            {
                index: powers(
                    (1,),
                    ((1.0,), (1.0,), (1.0,), (1.0,)),
                    ((0.0,), (0.0,), (0.0,), (0.0,)),
                )
                for index in indices
            },
            {
                indices[0]: currents(
                    (1,),
                    ((2.0,), (2.0,), (2.0,), (2.0,)),
                    ((0.0,), (0.0,), (0.0,), (0.0,)),
                ),
                indices[1]: currents(
                    (1,),
                    ((2.0,), (2.0,), (2.0,), (2.0,)),
                    ((180.0,), (180.0,), (180.0,), (180.0,)),
                ),
            },
        )

        _, failures, measured = measure_branch_powers(branches, flow)

        self.assertEqual(failures, {})
        self.assertLess(float(measured[branch.branch_id]), 1e-9)

    def test_two_connections_without_angles_give_up_on_the_current(self) -> None:
        # Sem ângulo não há soma fasorial, e somar módulos inventaria um valor.
        _, _, catalog, loads = make_sources(extra_segment=("d", 1, 4))
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        indices = [
            index
            for index, segment_id in enumerate(catalog.segments.segment_ids)
            if segment_id in ("T2", "T4")
        ]
        flow = make_power_flow(
            catalog,
            loads,
            {
                index: powers(
                    (1,),
                    ((1.0,), (1.0,), (1.0,), (1.0,)),
                    ((0.0,), (0.0,), (0.0,), (0.0,)),
                )
                for index in indices
            },
            {
                index: currents((1,), ((2.0,), (2.0,), (2.0,), (2.0,)))
                for index in indices
            },
        )

        patterns, failures, measured = measure_branch_powers(branches, flow)

        self.assertEqual(failures, {})
        self.assertIn(branch.branch_id, patterns)
        self.assertEqual(measured, {})


class MeasureBranchPowersTests(unittest.TestCase):
    def test_single_phase_branch_uses_the_first_element_as_measured(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        self.assertEqual(branch.first_segment_id, "T2")
        flow = make_power_flow(
            catalog,
            loads,
            {
                branch.first_segment_index: powers(
                    (1,),
                    ((10.0,), (20.0,), (30.0,), (40.0,)),
                    ((1.0,), (2.0,), (3.0,), (4.0,)),
                )
            },
        )

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(failures, {})
        group = measured[branch.branch_id]
        self.assertEqual([record.npat for record in group], [0, 1, 2, 3])
        self.assertEqual(
            [record.pd for record in group],
            [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")],
        )
        self.assertEqual(
            [record.qd for record in group],
            [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")],
        )
        # Fora das fases do ramal tudo permanece exatamente zero: é o que o
        # exportador exige para não recusar a carga equivalente.
        self.assertEqual(
            {record.pe for record in group} | {record.pf for record in group},
            {Decimal(0)},
        )
        self.assertEqual(group[0].load_id, f"RAMAL-{branch.branch_id}")

    def test_element_pointing_away_from_the_trunk_has_its_sign_inverted(self) -> None:
        # T2 vai de B3 para B1: o terminal 1 fica a jusante, e a potência que
        # entra nele é a que volta do ramal.
        _, _, catalog, loads = make_sources(branch_start=3, branch_end=1)
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        flow = make_power_flow(
            catalog,
            loads,
            {
                branch.first_segment_index: powers(
                    (1,),
                    ((-10.0,), (-20.0,), (-30.0,), (-40.0,)),
                    ((-1.0,), (-2.0,), (-3.0,), (-4.0,)),
                )
            },
        )

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(failures, {})
        group = measured[branch.branch_id]
        self.assertEqual(
            [record.pd for record in group],
            [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")],
        )
        self.assertEqual(
            [record.qd for record in group],
            [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")],
        )

    def test_two_phase_branch_splits_the_measurement_by_node(self) -> None:
        _, _, catalog, loads = make_sources(branch_phases="de")
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        flow = make_power_flow(
            catalog,
            loads,
            {
                branch.first_segment_index: powers(
                    (1, 2),
                    ((10.0, 5.0),) * 4,
                    ((1.0, 0.5),) * 4,
                )
            },
        )

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(failures, {})
        group = measured[branch.branch_id]
        self.assertEqual(group[0].pd, Decimal("10"))
        self.assertEqual(group[0].pe, Decimal("5"))
        self.assertEqual(group[0].pf, Decimal(0))
        self.assertEqual(group[0].qd, Decimal("1"))
        self.assertEqual(group[0].qe, Decimal("0.5"))

    def test_every_trunk_connection_is_summed(self) -> None:
        # T4 liga a mesma barra B3 à barra B2 do tronco: a potência entra pelos
        # dois pontos, e medir só o primeiro subestimaria o ramal.
        _, _, catalog, loads = make_sources(extra_segment=("d", 2, 3))
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        self.assertEqual(branch.trunk_connection_count, 2)
        segments = catalog.segments
        indices = {
            segments.segment_ids[int(value)]: int(value)
            for value in branch.segment_indices
        }
        flow = make_power_flow(
            catalog,
            loads,
            {
                indices["T2"]: powers((1,), ((10.0,),) * 4, ((1.0,),) * 4),
                indices["T4"]: powers((1,), ((4.0,),) * 4, ((0.5,),) * 4),
            },
        )

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(failures, {})
        group = measured[branch.branch_id]
        self.assertEqual(group[0].pd, Decimal("14"))
        self.assertEqual(group[0].qd, Decimal("1.5"))

    def test_element_without_measurement_fails_the_branch(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        flow = make_power_flow(catalog, loads, {})

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(measured, {})
        self.assertIn("T2", failures[branch.branch_id])

    def test_unsolved_circuit_fails_the_branch(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        flow = PowerFlowResult(
            catalog=catalog,
            cables=None,
            phase_configuration=PHASES,
            loads=loads,
            patterns=None,
            step_count=4,
            segment_powers={
                branch.first_segment_index: powers(
                    (1,), ((10.0,),) * 4, ((1.0,),) * 4
                )
            },
            solved_circuits=(),
        )

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(measured, {})
        self.assertIn("não foi resolvido", failures[branch.branch_id])

    def test_unconverged_circuit_fails_the_branch(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        flow = PowerFlowResult(
            catalog=catalog,
            cables=None,
            phase_configuration=PHASES,
            loads=loads,
            patterns=None,
            step_count=4,
            segment_powers={
                branch.first_segment_index: powers(
                    (1,), ((10.0,),) * 4, ((1.0,),) * 4
                )
            },
            solved_circuits=("C1",),
            unconverged=(("C1", 2),),
        )

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(measured, {})
        self.assertIn("não convergiu", failures[branch.branch_id])

    def test_incomplete_steps_fail_the_branch(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        flow = make_power_flow(
            catalog,
            loads,
            {
                branch.first_segment_index: powers(
                    (1,), ((10.0,), (20.0,)), ((1.0,), (2.0,))
                )
            },
        )

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(measured, {})
        self.assertIn("quatro patamares", failures[branch.branch_id])

    def test_node_without_phase_letter_fails_the_branch(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        branch = branches.records[0]
        flow = make_power_flow(
            catalog,
            loads,
            {
                branch.first_segment_index: powers(
                    (7,), ((10.0,),) * 4, ((1.0,),) * 4
                )
            },
        )

        measured, failures, currents = measure_branch_powers(branches, flow)

        self.assertEqual(measured, {})
        self.assertIn("fases2.json", failures[branch.branch_id])

    def test_power_flow_from_another_catalog_is_refused(self) -> None:
        _, _, catalog, loads = make_sources()
        _, _, other_catalog, other_loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        flow = make_power_flow(other_catalog, other_loads, {})

        with self.assertRaises(ValueError):
            measure_branch_powers(branches, flow)

    def test_step_count_other_than_four_is_refused(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        flow = PowerFlowResult(
            catalog=catalog,
            cables=None,
            phase_configuration=PHASES,
            loads=loads,
            patterns=None,
            step_count=1,
            solved_circuits=("C1",),
        )

        with self.assertRaises(ValueError):
            measure_branch_powers(branches, flow)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
