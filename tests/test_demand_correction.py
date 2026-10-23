"""Núcleo da correção de demanda: o ponto fixo, sem motor e sem Qt.

``evaluate`` sintético faz o papel do OpenDSS: saída = fator × parcela do
escopo + fixo (+ perdas, quando o teste quer não linearidade).
"""

from __future__ import annotations

import math
import unittest

from circuit_viewer.demand_correction import (
    DemandCorrection,
    DemandCorrectionResult,
    DemandScope,
    CorrectionSettings,
    HeadReading,
    PatamarCorrection,
    PatamarStatus,
    PatamarTarget,
    correction_for,
    energy_scope_for_kind,
    energy_scope_for_name,
    parse_target,
    solve_patamar,
    targets_from_pairs,
)


def linear(
    active: float,
    reactive: float,
    *,
    fixed_active: float = 0.0,
    fixed_reactive: float = 0.0,
    loss: float = 0.0,
    calls: list[tuple[float, float]] | None = None,
):  # noqa: ANN201
    """Saída de um alimentador com perdas quadráticas na corrente."""

    def evaluate(fp: float, fq: float) -> HeadReading:
        if calls is not None:
            calls.append((fp, fq))
        p_load = fp * active
        q_load = fq * reactive
        current = math.hypot(p_load + fixed_active, q_load + fixed_reactive)
        p_loss = loss * current**2
        q_loss = 2.0 * loss * current**2
        return HeadReading(
            p_load + fixed_active + p_loss,
            q_load + fixed_reactive + q_loss,
            fixed_active,
            fixed_reactive,
            True,
            p_loss,
            q_loss,
        )

    return evaluate


class SolvePatamarTests(unittest.TestCase):
    def test_the_reference_case_converges_to_the_exact_factors(self) -> None:
        # O caso de tests/test_correcao.py do CORRECAO_DEMANDA: carga 100+j50,
        # GD −20 kW e 7 kvar fixos (A + B) → fP = 2,6 e fQ = 2,0.
        calls: list[tuple[float, float]] = []
        outcome = solve_patamar(
            linear(100.0, 50.0, fixed_active=-20.0, fixed_reactive=7.0, calls=calls),
            PatamarTarget(240.0, 107.0),
        )

        self.assertIs(outcome.status, PatamarStatus.CONVERGED)
        self.assertAlmostEqual(outcome.active_factor, 2.6)
        self.assertAlmostEqual(outcome.reactive_factor, 2.0)
        # Um solve de base e um que já basta: o incremento seguinte não é aplicado.
        self.assertEqual(calls, [(1.0, 1.0), (outcome.active_factor, outcome.reactive_factor)])
        self.assertEqual(outcome.iterations, 2)
        self.assertAlmostEqual(outcome.initial.active, 80.0)
        self.assertAlmostEqual(outcome.final.active, 240.0)

    def test_losses_make_it_iterate_and_the_factors_stay_independent(self) -> None:
        settings = CorrectionSettings(relative_tolerance=1e-6, absolute_tolerance=1e-6)
        outcome = solve_patamar(
            linear(1_000.0, 400.0, loss=2e-5),
            PatamarTarget(1_300.0, 300.0),
            settings,
        )

        self.assertTrue(outcome.converged)
        self.assertGreater(outcome.iterations, 2)
        self.assertGreater(outcome.active_factor, 1.0)
        self.assertLess(outcome.reactive_factor, 1.0)
        self.assertAlmostEqual(outcome.final.active, 1_300.0, places=3)
        self.assertAlmostEqual(outcome.final.reactive, 300.0, places=3)
        self.assertLessEqual(abs(outcome.active_residual), settings.tolerance(1_300.0))

    def test_a_missing_q_target_keeps_its_factor_at_one(self) -> None:
        outcome = solve_patamar(linear(100.0, 50.0), PatamarTarget(150.0, None))

        self.assertTrue(outcome.converged)
        self.assertAlmostEqual(outcome.active_factor, 1.5)
        self.assertEqual(outcome.reactive_factor, 1.0)
        self.assertIsNone(outcome.reactive_residual)

    def test_a_patamar_without_target_is_read_once_at_the_base(self) -> None:
        calls: list[tuple[float, float]] = []
        outcome = solve_patamar(linear(100.0, 50.0, calls=calls), PatamarTarget())

        self.assertIs(outcome.status, PatamarStatus.SKIPPED)
        self.assertEqual(calls, [(1.0, 1.0)])
        self.assertEqual((outcome.active_factor, outcome.reactive_factor), (1.0, 1.0))
        self.assertAlmostEqual(outcome.final.active, 100.0)

    def test_a_capacitive_target_gives_a_negative_q_factor_with_a_warning(self) -> None:
        outcome = solve_patamar(
            linear(100.0, 50.0, fixed_reactive=-30.0), PatamarTarget(100.0, -60.0)
        )

        self.assertTrue(outcome.converged)
        self.assertAlmostEqual(outcome.reactive_factor, -0.6)
        self.assertTrue(outcome.warnings)

    def test_fixed_generation_above_the_target_is_infeasible(self) -> None:
        outcome = solve_patamar(
            linear(100.0, 50.0, fixed_active=-50.0), PatamarTarget(-80.0, 50.0)
        )

        self.assertIs(outcome.status, PatamarStatus.INVALID)
        self.assertIn("carga negativa", outcome.message)
        self.assertEqual(outcome.active_factor, 1.0)

    def test_without_scalable_power_there_is_no_factor(self) -> None:
        outcome = solve_patamar(linear(0.0, 0.0), PatamarTarget(10.0, 5.0))

        self.assertIs(outcome.status, PatamarStatus.INVALID)

    def test_a_diverged_flow_stops_the_patamar(self) -> None:
        def evaluate(fp: float, fq: float) -> HeadReading:
            return HeadReading(100.0 * fp, 50.0 * fq, converged=fp < 1.2)

        outcome = solve_patamar(evaluate, PatamarTarget(150.0, 50.0))

        self.assertIs(outcome.status, PatamarStatus.FLOW_DIVERGED)
        self.assertAlmostEqual(outcome.active_factor, 1.5)
        self.assertFalse(outcome.final.converged)

    def test_the_status_comes_from_the_real_residual(self) -> None:
        # Saída saturada: nenhum fator passa de 90 kW. A referência relaxava a
        # tolerância e "aceitava"; aqui o patamar não converge.
        def evaluate(fp: float, fq: float) -> HeadReading:
            return HeadReading(90.0 * fp / (1.0 + fp), 50.0 * fq)

        settings = CorrectionSettings(max_iterations=30)
        outcome = solve_patamar(evaluate, PatamarTarget(120.0, 50.0), settings)

        self.assertIs(outcome.status, PatamarStatus.NOT_CONVERGED)
        self.assertEqual(outcome.iterations, 30)
        self.assertGreater(abs(outcome.active_residual), 30.0)

    def test_the_tolerance_is_inclusive_and_has_a_floor(self) -> None:
        settings = CorrectionSettings(relative_tolerance=0.001, absolute_tolerance=0.5)
        self.assertEqual(settings.tolerance(100.0), 0.5)
        self.assertAlmostEqual(settings.tolerance(10_000.0), 10.0)

        outcome = solve_patamar(linear(100.1, 0.4), PatamarTarget(100.0, 0.0), settings)
        self.assertTrue(outcome.converged)
        self.assertEqual(outcome.iterations, 1)

    def test_cancelling_interrupts(self) -> None:
        with self.assertRaises(InterruptedError):
            solve_patamar(linear(100.0, 50.0), PatamarTarget(150.0, 50.0), cancel_check=lambda: True)


class SettingsAndTargetsTests(unittest.TestCase):
    def test_settings_are_validated(self) -> None:
        for kwargs in (
            {"relative_tolerance": -1.0},
            {"relative_tolerance": 0.0, "absolute_tolerance": 0.0},
            {"max_iterations": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CorrectionSettings(**kwargs)

    def test_typed_targets_accept_the_decimal_comma(self) -> None:
        self.assertEqual(parse_target("1.234,5"), 1234.5)
        self.assertEqual(parse_target(" 12,5 "), 12.5)
        self.assertEqual(parse_target("-3.5"), -3.5)
        self.assertIsNone(parse_target("  "))
        with self.assertRaises(ValueError):
            parse_target("abc")
        with self.assertRaises(ValueError):
            parse_target("inf")

    def test_four_patamares_are_required(self) -> None:
        self.assertEqual(len(targets_from_pairs([(1.0, None)] * 4)), 4)
        with self.assertRaises(ValueError):
            targets_from_pairs([(1.0, 2.0)] * 3)


class DemandCorrectionTests(unittest.TestCase):
    def test_each_patamar_is_scaled_by_its_own_factor(self) -> None:
        correction = DemandCorrection(
            "C1",
            frozenset({DemandScope.CARGA}),
            ((1.0, 1.0), (2.0, 0.5), (3.0, 1.0), (1.0, -1.0)),
        )

        active, reactive = correction.scale((1.0, 1.0, 1.0, 2.0), (4.0, 4.0, 4.0, 4.0))

        self.assertEqual(active, [1.0, 2.0, 3.0, 2.0])
        self.assertEqual(reactive, [4.0, 2.0, 4.0, -4.0])

    def test_invalid_corrections_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            DemandCorrection("C1", frozenset({DemandScope.CARGA}), ((1.0, 1.0),) * 3)
        with self.assertRaises(ValueError):
            DemandCorrection("C1", frozenset(), ((1.0, 1.0),) * 4)
        with self.assertRaises(ValueError):
            DemandCorrection("C1", frozenset({DemandScope.CARGA}), ((math.nan, 1.0),) * 4)

    def test_lookup_respects_circuit_and_group(self) -> None:
        correction = DemandCorrection("C1", frozenset({DemandScope.UC_BT}), ((2.0, 2.0),) * 4)
        corrections = {"C1": correction}

        self.assertIs(correction_for(corrections, "C1", DemandScope.UC_BT), correction)
        self.assertIsNone(correction_for(corrections, "C1", DemandScope.IP))
        self.assertIsNone(correction_for(corrections, "C2", DemandScope.UC_BT))
        self.assertIsNone(correction_for(None, "C1", DemandScope.UC_BT))

    def test_energy_names_and_kinds_map_to_groups(self) -> None:
        self.assertIs(energy_scope_for_name("uc-bt-1001"), DemandScope.UC_BT)
        self.assertIs(energy_scope_for_name("UC-MT-101_2"), DemandScope.UC_MT)
        self.assertIs(energy_scope_for_name("IP-168662"), DemandScope.IP)
        self.assertIsNone(energy_scope_for_name("GD-BT-71"))
        self.assertIs(energy_scope_for_kind("BT"), DemandScope.UC_BT)
        self.assertIs(energy_scope_for_kind("ip"), DemandScope.IP)
        self.assertIsNone(energy_scope_for_kind("GD"))


class ResultTests(unittest.TestCase):
    def _result(self, *statuses: PatamarStatus) -> DemandCorrectionResult:
        patamares = tuple(
            PatamarCorrection(step, PatamarTarget(1.0, 1.0), status, 1.0 + step, 0.5)
            for step, status in enumerate(statuses)
        )
        return DemandCorrectionResult(
            0, "C1", frozenset({DemandScope.CARGA}), CorrectionSettings(), patamares
        )

    def test_only_converged_patamares_are_applied(self) -> None:
        result = self._result(
            PatamarStatus.CONVERGED,
            PatamarStatus.NOT_CONVERGED,
            PatamarStatus.CONVERGED,
            PatamarStatus.SKIPPED,
        )

        correction = result.correction()

        self.assertEqual(correction.factors, ((1.0, 0.5), (1.0, 1.0), (3.0, 0.5), (1.0, 1.0)))
        self.assertEqual(result.converged_steps, (0, 2))
        self.assertEqual(result.failed_steps, (1,))

    def test_nothing_to_apply_without_convergence(self) -> None:
        result = self._result(*(PatamarStatus.FLOW_DIVERGED,) * 4)

        self.assertIsNone(result.correction())


if __name__ == "__main__":
    unittest.main()
