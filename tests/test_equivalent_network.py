from __future__ import annotations

from decimal import Decimal
import unittest

import numpy as np

from circuit_viewer import (
    BranchPowerSource,
    BranchType,
    EquivalentLoadPatternRecord,
    EquivalentLoadRecord,
    EquivalentNetworkIssue,
    EquivalentNetworkModel,
    EquivalentNetworkResult,
    analyze_branches,
    build_equivalent_network,
)
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
    UtmCrs,
)
from circuit_viewer.calculation_levels import default_calculation_levels
from circuit_viewer.curvas import Curve
from circuit_viewer.generator_update import (
    GeneratorScheduleMode,
    calculate_generator_demands,
)
from circuit_viewer.equivalent_network import ZEROED_PATTERNS_FIELD
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("d", "D", 1),
        PhaseMappingEntry("def", "DEF", 3),
    )
)


def make_sources(
    *,
    snom: tuple[str, ...] = ("999", "1,5", "2.5e0"),
    sadm: tuple[str, ...] = ("999", "2", "3"),
):
    bars = CircuitModel(
        ["B0", "B1", "B2", "B3", "B4"],
        ["CB0", "CB1", "CB2", "CB3", "CB4"],
        [0.0, 10.0, 20.0, 10.0, 10.0],
        [0.0, 0.0, 0.0, -10.0, -20.0],
        UtmCrs(21, northern=False),
    )
    segments = LineNetworkModel(
        bars,
        ["T0", "T1", "T2", "T3"],
        ["CT0", "CT1", "CT2", "CT3"],
        ["DEF", "DEF", " D ", "d"],
        [0, 1, 1, 3],
        [1, 2, 3, 4],
        [""] * 4,
        [""] * 4,
        [""] * 4,
        [10.0] * 4,
    )
    catalog = CircuitCatalogModel.build(
        segments,
        None,
        [CircuitDefinition("C1", "B0", "", "")],
    )
    loads = LoadModel(
        bars,
        ["L0", "L1", "L2"],
        [1, 3, 4],
        [""] * 3,
        [""] * 3,
        snom,
        sadm,
        [""] * 3,
        [""] * 3,
        [""] * 3,
    )
    return bars, segments, catalog, loads


def pattern_group(load_id: str, factor: int) -> tuple[LoadPatternRecord, ...]:
    return tuple(
        LoadPatternRecord(
            load_id,
            npat,
            str(factor * (npat + 1)),
            str(factor),
            "0",
            str(factor * 2),
            "0",
            "0",
        )
        for npat in range(4)
    )


class EquivalentNetworkTests(unittest.TestCase):
    def test_generators_reduce_branch_power_and_are_hidden_with_the_branch(self) -> None:
        _, _, catalog, loads = make_sources()
        patterns = LoadPatternModel(
            loads,
            [pattern_group("L0", 9), pattern_group("L1", 1), pattern_group("L2", 2)],
        )
        generators = GeneratorModel(
            loads,
            ["G1"],
            [1],
            ["MC1"],
            ["GEN-1"],
            ["13.8"],
            ["75"],
            ["Y"],
            ["CURVA"],
            ["720"],
            ["CONS-1"],
            ["GEN-1"],
            [""],
            ["Gerador"],
            ["d"],
        )
        schedule = default_calculation_levels()
        updates = calculate_generator_demands(
            generators,
            catalog,
            PHASES,
            Curve("C", "Constante", (1.0,) * 24),
            (schedule,),
            (GeneratorScheduleMode.DEFAULT,),
        ).model
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, patterns, updates)

        record = result.model.record(0)
        self.assertEqual(record.source_generator_count, 1)
        self.assertEqual(set(record.source_generator_indices), {0})
        self.assertEqual(record.aggregation_state, "valid")
        group = result.model.records_for_load(0)
        self.assertEqual(group[0].pd, Decimal("2"))
        self.assertEqual(group[3].pd, Decimal("11"))
        self.assertEqual(record.maximum_active_demand, Decimal("11"))
        masks = result.model.visibility_masks((True,))
        self.assertTrue(np.array_equal(masks.source_generator_mask, [False]))

    def test_exact_net_zero_branch_has_no_visible_equivalent(self) -> None:
        _, _, catalog, loads = make_sources()

        def active_group(load_id: str, value: str):
            return tuple(
                LoadPatternRecord(load_id, npat, value, "0", "0", "0", "0", "0")
                for npat in range(4)
            )

        patterns = LoadPatternModel(
            loads,
            [active_group("L0", "0"), active_group("L1", "1"), active_group("L2", "0")],
        )
        generators = GeneratorModel(
            loads,
            ["G1"],
            [1],
            ["MC1"],
            ["GEN-1"],
            ["13.8"],
            ["75"],
            ["Y"],
            ["CURVA"],
            ["720"],
            ["CONS-1"],
            ["GEN-1"],
            [""],
            ["Gerador"],
            ["d"],
        )
        schedule = default_calculation_levels()
        updates = calculate_generator_demands(
            generators,
            catalog,
            PHASES,
            Curve("C", "Constante", (1.0,) * 24),
            (schedule,),
            (GeneratorScheduleMode.DEFAULT,),
        ).model
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, patterns, updates)

        self.assertTrue(result.model.record(0).is_zero)
        self.assertTrue(
            np.array_equal(
                result.model.visibility_masks((True,)).equivalent_load_mask,
                [False],
            )
        )

    def test_public_types_and_global_ids_with_original_phases2(self) -> None:
        self.assertTrue(EquivalentLoadRecord.__dataclass_fields__)
        self.assertTrue(EquivalentLoadPatternRecord.__dataclass_fields__)
        self.assertTrue(EquivalentNetworkIssue.__dataclass_fields__)
        self.assertTrue(EquivalentNetworkResult.__dataclass_fields__)
        _, segments, _, loads = make_sources()
        catalog = CircuitCatalogModel.build(
            segments,
            None,
            [
                CircuitDefinition("Z", "B0", "", ""),
                CircuitDefinition("A", "B0", "", ""),
            ],
        )

        result = analyze_branches(catalog, PHASES, loads)

        self.assertEqual([record.branch_id for record in result.records], [1, 2])
        self.assertEqual([record.circuit_id for record in result.records], ["A", "Z"])
        self.assertEqual(result.records[0].phases2, "D")
        self.assertEqual(set(result.records[0].load_indices), {1, 2})
        self.assertFalse(result.records[0].load_indices.flags.writeable)

    def test_decimal_aggregation_provenance_and_patterns(self) -> None:
        _, _, catalog, loads = make_sources()
        patterns = LoadPatternModel(
            loads,
            [pattern_group("L0", 9), pattern_group("L1", 1), pattern_group("L2", 2)],
        )
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, patterns)

        self.assertIsInstance(result.model, EquivalentNetworkModel)
        self.assertIs(result.model.source_loads, loads)
        record = result.model.record(0)
        self.assertEqual(record.branch_id, 1)
        self.assertEqual(record.branch_type, BranchType.MONOPHASIC)
        self.assertEqual(record.load_id, "RAMAL-1")
        self.assertEqual(record.origin_kind, "branch_aggregate")
        self.assertFalse(record.removable)
        self.assertEqual(record.snom, Decimal("4.0"))
        self.assertEqual(record.sadm, Decimal("5"))
        self.assertEqual(set(record.source_load_indices), {1, 2})
        group = result.model.records_for_load(0)
        self.assertEqual(len(group), 4)
        self.assertEqual(group[0].pd, Decimal("3"))
        self.assertEqual(group[3].pd, Decimal("12"))
        self.assertEqual(record.maximum_active_demand, Decimal("12"))
        self.assertEqual(result.model.index_for_id("RAMAL-1"), 0)
        self.assertEqual(result.model.index_for_branch_id(1), 0)
        self.assertEqual(result.issues, ())

    def test_biphasic_equivalent_aggregates_attached_single_phase_loads(self) -> None:
        phases = PhaseConfiguration(
            (
                PhaseMappingEntry("d", "D", 1),
                PhaseMappingEntry("e", "E", 1),
                PhaseMappingEntry("ab", "AB", 2),
                PhaseMappingEntry("def", "DEF", 3),
            )
        )
        bars = CircuitModel(
            [f"B{index}" for index in range(7)],
            [""] * 7,
            range(7),
            [0.0] * 7,
            UtmCrs(21, northern=False),
        )
        segments = LineNetworkModel(
            bars,
            [f"T{index}" for index in range(6)],
            [""] * 6,
            ["DEF", "DEF", "AB", "AB", "D", "E"],
            [0, 1, 1, 3, 4, 4],
            [1, 2, 3, 4, 5, 6],
            [""] * 6,
            [""] * 6,
            [""] * 6,
            [1.0] * 6,
        )
        catalog = CircuitCatalogModel.build(
            segments,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        loads = LoadModel(
            bars,
            ["L0", "L1", "L2"],
            [3, 5, 6],
            [""] * 3,
            [""] * 3,
            ["1", "2", "3"],
            ["4", "5", "6"],
            [""] * 3,
            [""] * 3,
            [""] * 3,
        )
        patterns = LoadPatternModel(
            loads,
            [
                pattern_group("L0", 1),
                pattern_group("L1", 2),
                pattern_group("L2", 3),
            ],
        )

        branches = analyze_branches(catalog, phases, loads)
        result = build_equivalent_network(branches, loads, patterns)

        branch = branches.records[0]
        record = result.model.record(0)
        self.assertEqual(branch.branch_type, BranchType.BIPHASIC)
        self.assertEqual(set(branch.segment_indices), {2, 3, 4, 5})
        self.assertEqual(record.branch_type, BranchType.BIPHASIC)
        self.assertEqual(record.snom, Decimal("6"))
        self.assertEqual(record.sadm, Decimal("15"))
        self.assertEqual(record.source_load_count, 3)
        self.assertEqual(result.model.records_for_load(0)[0].pd, Decimal("6"))

    def test_strict_fields_reject_a_non_numeric_snom(self) -> None:
        _, _, catalog, loads = make_sources(
            snom=("999", "", "2"),
            sadm=("999", "1", "2"),
        )
        patterns = LoadPatternModel(
            loads,
            [pattern_group("L0", 9), pattern_group("L1", 1), pattern_group("L2", 2)],
        )
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, patterns)

        record = result.model.record(0)
        self.assertIsNone(record.snom)
        self.assertEqual(record.sadm, Decimal("3"))
        self.assertTrue(any(issue.field == "SNOM" for issue in result.issues))
        # SNOM inválido não contamina a agregação elétrica: são totais distintos.
        self.assertTrue(record.electrical_complete)

    def test_load_without_a_table_counts_as_zero_without_blocking(self) -> None:
        _, _, catalog, loads = make_sources()
        # L2 não está em MODELO_CARGA; L1 está, com PD 1/2/3/4.
        patterns = LoadPatternModel(
            loads,
            [pattern_group("L0", 9), pattern_group("L1", 1), None],
        )
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, patterns)

        record = result.model.record(0)
        self.assertTrue(record.electrical_complete)
        self.assertEqual(record.aggregation_state, "valid")
        # Só a parcela de L1 entrou; a de L2 somou zero.
        self.assertEqual(
            [item.pd for item in result.model.records_for_load(0)],
            [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")],
        )
        self.assertEqual(record.maximum_active_demand, Decimal("4"))
        zeroed = [
            issue for issue in result.issues if issue.field == ZEROED_PATTERNS_FIELD
        ]
        self.assertEqual([issue.load_id for issue in zeroed], ["L2"])
        self.assertIn("considerada zero", zeroed[0].message)
        # E não sobra nenhum diagnóstico de bloqueio por patamares.
        self.assertFalse(any(issue.field == "PATAMARES" for issue in result.issues))

    def test_branch_whose_loads_have_no_table_at_all_is_zero(self) -> None:
        _, _, catalog, loads = make_sources()
        # L1 e L2 são as cargas do ramal; nenhuma tem tabela.
        patterns = LoadPatternModel(loads, [pattern_group("L0", 9), None, None])
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, patterns)

        record = result.model.record(0)
        self.assertTrue(record.electrical_complete)
        self.assertTrue(record.is_zero)
        self.assertEqual(record.maximum_active_demand, Decimal(0))

    def test_patterns_not_imported_at_all_still_blocks(self) -> None:
        # Tabela inteira ausente não é "carga sem tabela": zerar todos os ramais
        # de uma vez seria silenciosamente errado.
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, None)

        self.assertFalse(result.model.record(0).electrical_complete)
        self.assertTrue(any(issue.field == "PATAMARES" for issue in result.issues))

    def test_empty_branch_has_zero_totals_and_zero_patterns(self) -> None:
        _, _, catalog, loads = make_sources()
        loads = LoadModel(
            loads.bars,
            ["L0"],
            [1],
            [""],
            [""],
            ["10"],
            ["20"],
            [""],
            [""],
            [""],
        )
        patterns = LoadPatternModel(loads, [pattern_group("L0", 1)])
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, patterns)

        record = result.model.record(0)
        self.assertEqual(record.source_load_count, 0)
        self.assertEqual(record.snom, Decimal(0))
        self.assertEqual(record.sadm, Decimal(0))
        self.assertEqual(record.maximum_active_demand, Decimal(0))
        self.assertTrue(
            all(value == 0 for row in result.model.records_for_load(0) for value in row.values)
        )

    def test_maximum_demand_uses_only_real_active_phases_and_preserves_sign(self) -> None:
        _, _, catalog, loads = make_sources()

        def group(load_id: str, values: tuple[str, str, str, str]):
            return tuple(
                LoadPatternRecord(
                    load_id,
                    npat,
                    value,
                    "999",
                    "888",
                    "7777",
                    "6666",
                    "5555",
                )
                for npat, value in enumerate(values)
            )

        patterns = LoadPatternModel(
            loads,
            [
                group("L0", ("0", "0", "0", "0")),
                group("L1", ("-10", "-20", "-30", "-40")),
                group("L2", ("-1", "-2", "-3", "-4")),
            ],
        )
        branches = analyze_branches(catalog, PHASES, loads)

        result = build_equivalent_network(branches, loads, patterns)

        self.assertEqual(
            result.model.record(0).maximum_active_demand,
            Decimal("-11"),
        )

    def test_visibility_reduces_branch_and_restores_it_for_overlapping_owner(self) -> None:
        _, segments, _, loads = make_sources()
        catalog = CircuitCatalogModel.build(
            segments,
            None,
            [
                CircuitDefinition("C1", "B0", "", ""),
                CircuitDefinition("C2", "B0", "", ""),
            ],
        )
        branches = analyze_branches(catalog, PHASES, loads)
        model = build_equivalent_network(branches, loads).model

        both = model.visibility_masks((True, True))
        self.assertFalse(bool(both.segment_mask[2]))
        self.assertFalse(bool(both.segment_mask[3]))
        self.assertFalse(bool(both.source_load_mask[1]))
        self.assertTrue(bool(both.source_load_mask[0]))
        self.assertTrue(np.array_equal(both.equivalent_load_mask, [True, True]))

        one = model.visibility_masks((True, False))
        self.assertTrue(np.array_equal(one.equivalent_load_mask, [True, False]))
        self.assertFalse(one.segment_mask.flags.writeable)

    def test_snapshot_mismatch_and_cancellation_are_rejected(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        with self.assertRaises(InterruptedError):
            build_equivalent_network(
                branches,
                loads,
                cancel_check=lambda: True,
            )
        with self.assertRaises(ValueError):
            build_equivalent_network(branches, None)

    def test_prefixed_original_id_remains_unambiguous_by_model_and_origin(self) -> None:
        bars, _, catalog, _ = make_sources()
        loads = LoadModel(
            bars,
            ["L0", "RAMAL-1", "L2"],
            [1, 3, 4],
            [""] * 3,
            [""] * 3,
            ["1"] * 3,
            ["1"] * 3,
            [""] * 3,
            [""] * 3,
            [""] * 3,
        )
        branches = analyze_branches(catalog, PHASES, loads)
        equivalent = build_equivalent_network(branches, loads).model

        self.assertEqual(loads.index_for_id("RAMAL-1"), 1)
        self.assertEqual(equivalent.index_for_id("RAMAL-1"), 0)
        self.assertEqual(
            equivalent.record(0).origin_kind,
            "branch_aggregate",
        )


def measured_group(load_id: str, active: str, reactive: str = "0"):
    return tuple(
        EquivalentLoadPatternRecord(
            load_id,
            npat,
            Decimal(active) * (npat + 1),
            Decimal(0),
            Decimal(0),
            Decimal(reactive),
            Decimal(0),
            Decimal(0),
        )
        for npat in range(4)
    )


class MeasuredEquivalentNetworkTests(unittest.TestCase):
    """Modo em que os patamares vêm medidos do fluxo de potência."""

    def test_branch_without_load_tables_uses_the_measurement(self) -> None:
        _, _, catalog, loads = make_sources()
        # L1 e L2 estão no ramal e nenhuma delas tem patamares: pela agregação o
        # ramal vale zero; a medição no primeiro elemento lhe dá potência real.
        patterns = LoadPatternModel(loads, [pattern_group("L0", 9), (), ()])
        branches = analyze_branches(catalog, PHASES, loads)

        aggregated = build_equivalent_network(branches, loads, patterns)
        self.assertTrue(aggregated.model.record(0).is_zero)

        branch_id = branches.records[0].branch_id
        measured = build_equivalent_network(
            branches,
            loads,
            patterns,
            power_source=BranchPowerSource.POWER_FLOW,
            measured_patterns={branch_id: measured_group(f"RAMAL-{branch_id}", "7")},
        )

        record = measured.model.record(0)
        self.assertEqual(record.aggregation_state, "valid")
        self.assertEqual(measured.issues, ())
        self.assertEqual(record.maximum_active_demand, Decimal("28"))
        self.assertEqual(
            [item.pd for item in measured.model.records_for_load(0)],
            [Decimal("7"), Decimal("14"), Decimal("21"), Decimal("28")],
        )
        self.assertIs(measured.model.power_source, BranchPowerSource.POWER_FLOW)

    def test_branch_without_measurement_reports_the_reason(self) -> None:
        _, _, catalog, loads = make_sources()
        branches = analyze_branches(catalog, PHASES, loads)
        branch_id = branches.records[0].branch_id

        result = build_equivalent_network(
            branches,
            loads,
            None,
            power_source=BranchPowerSource.POWER_FLOW,
            measurement_issues={branch_id: "O elemento T2 não teve potência medida."},
        )

        self.assertFalse(result.model.record(0).electrical_complete)
        self.assertEqual(
            [(issue.branch_id, issue.field, issue.message) for issue in result.issues],
            [(branch_id, "FLUXO", "O elemento T2 não teve potência medida.")],
        )

    def test_measurement_already_contains_the_internal_generator(self) -> None:
        _, _, catalog, loads = make_sources()
        patterns = LoadPatternModel(
            loads,
            [pattern_group("L0", 9), pattern_group("L1", 1), pattern_group("L2", 2)],
        )
        generators = GeneratorModel(
            loads,
            ["G1"],
            [1],
            ["MC1"],
            ["GEN-1"],
            ["13.8"],
            ["75"],
            ["Y"],
            ["CURVA"],
            ["720"],
            ["CONS-1"],
            ["GEN-1"],
            [""],
            ["Gerador"],
            ["d"],
        )
        schedule = default_calculation_levels()
        updates = calculate_generator_demands(
            generators,
            catalog,
            PHASES,
            Curve("C", "Constante", (1.0,) * 24),
            (schedule,),
            (GeneratorScheduleMode.DEFAULT,),
        ).model
        branches = analyze_branches(catalog, PHASES, loads)
        branch_id = branches.records[0].branch_id

        result = build_equivalent_network(
            branches,
            loads,
            patterns,
            updates,
            power_source=BranchPowerSource.POWER_FLOW,
            measured_patterns={branch_id: measured_group(f"RAMAL-{branch_id}", "5")},
        )

        record = result.model.record(0)
        # A potência sai exatamente como medida: somar o gerador de novo o
        # contaria duas vezes. A proveniência, porém, é preservada, e é dela
        # que saem a redução e as máscaras de visibilidade.
        self.assertEqual(result.model.records_for_load(0)[0].pd, Decimal("5"))
        self.assertEqual(set(record.source_generator_indices), {0})
        masks = result.model.visibility_masks((True,))
        self.assertTrue(np.array_equal(masks.source_generator_mask, [False]))

    def test_ambiguous_load_still_blocks_the_measured_branch(self) -> None:
        bars = CircuitModel(
            ["B0", "B1", "B2", "B3"],
            ["CB0", "CB1", "CB2", "CB3"],
            [0.0, 10.0, 20.0, 10.0],
            [0.0, 0.0, 0.0, -10.0],
            UtmCrs(21, northern=False),
        )
        # Duas conexões monofásicas independentes chegam à mesma barra B3 por
        # fases diferentes: a carga de B3 pertence aos dois ramais.
        segments = LineNetworkModel(
            bars,
            ["T0", "T1", "T2", "T3"],
            ["CT0", "CT1", "CT2", "CT3"],
            ["DEF", "DEF", "d", "e"],
            [0, 1, 1, 2],
            [1, 2, 3, 3],
            [""] * 4,
            [""] * 4,
            [""] * 4,
            [10.0] * 4,
        )
        catalog = CircuitCatalogModel.build(
            segments,
            None,
            [CircuitDefinition("C1", "B0", "", "")],
        )
        loads = LoadModel(
            bars,
            ["L1"],
            [3],
            [""],
            [""],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        phases = PhaseConfiguration(
            (
                PhaseMappingEntry("d", "D", 1),
                PhaseMappingEntry("e", "E", 1),
                PhaseMappingEntry("def", "DEF", 3),
            )
        )
        branches = analyze_branches(catalog, phases, loads)
        self.assertEqual(len(branches.records), 2)

        result = build_equivalent_network(
            branches,
            loads,
            None,
            power_source=BranchPowerSource.POWER_FLOW,
            measured_patterns={
                record.branch_id: measured_group(f"RAMAL-{record.branch_id}", "3")
                for record in branches.records
            },
        )

        self.assertTrue(
            all(not record.electrical_complete for record in result.model.records)
        )
        self.assertTrue(
            all(issue.field == "ASSOCIACAO" for issue in result.issues)
        )

    def test_table_mode_is_untouched_by_the_new_parameters(self) -> None:
        _, _, catalog, loads = make_sources()
        patterns = LoadPatternModel(
            loads,
            [pattern_group("L0", 9), pattern_group("L1", 1), pattern_group("L2", 2)],
        )
        branches = analyze_branches(catalog, PHASES, loads)
        branch_id = branches.records[0].branch_id

        result = build_equivalent_network(
            branches,
            loads,
            patterns,
            measured_patterns={branch_id: measured_group(f"RAMAL-{branch_id}", "99")},
        )

        self.assertIs(result.model.power_source, BranchPowerSource.TABLE)
        self.assertIsNone(result.model.source_power_flow)
        self.assertEqual(result.model.records_for_load(0)[0].pd, Decimal("3"))


if __name__ == "__main__":
    unittest.main()
