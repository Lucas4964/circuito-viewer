"""D e G pela energia do mês, como o calcular_dg, com curvas sintéticas."""

from __future__ import annotations

import math
import unittest

from circuit_viewer.calculation_levels import default_calculation_levels
from circuit_viewer.curvas import Curve
from circuit_viewer.curvas_categorias import CurveCategory
from circuit_viewer.energy_demand import (
    DAYS,
    STATUS_ADJUSTED,
    STATUS_FULL_SELF_CONSUMPTION,
    STATUS_GENERATION_ONLY,
    STATUS_INACTIVE,
    STATUS_LIGHTING,
    STATUS_NO_CURVE,
    STATUS_NO_ENERGY,
    STATUS_NO_GD,
    STATUS_ZERO_CONSUMPTION,
    CurveSelector,
    Sorteador,
    TypicalCurve,
    calculate_energy_demands,
    energies,
    resolve_dg,
    schedules_for_loads,
    select,
)
from circuit_viewer.transformer_consumers import build_transformer_consumers
from tests.test_transformer_consumers import bt, et, loads_model, mt, standard_rows, utc


FLAT = tuple(1.0 for _ in range(24))
# Sol das 7 às 17 h (pontos 8 a 17), pico de 1 pu ao meio-dia.
SOLAR = tuple(
    max(0.0, 1.0 - abs(hour - 12) / 5.0) if 7 <= hour <= 17 else 0.0
    for hour in range(1, 25)
)
EVENING = tuple(0.5 if hour < 18 else 1.0 for hour in range(1, 25))


def category(cat_id, classe, minimum=-1.0, maximum=-1.0, share=100.0, kind="C", **extra):  # noqa: ANN001, ANN003, ANN201
    return CurveCategory(str(cat_id), f"Categoria {cat_id}", classe, minimum, maximum, share,
                         kind=kind, **extra)


def average(cat_id, values):  # noqa: ANN001, ANN201
    # Em pu da média, como o MEDIA_P do banco: o seletor normaliza pelo máximo.
    return Curve(f"interplan-cat{cat_id}-p4", f"Curva {cat_id} - Média",
                 tuple(2.0 * value for value in values), str(cat_id), "MED")


CATEGORIES = (
    category(1, 5, pu_max=1),                               # IP
    category(2, 0, -1.0, 100.0, share=60.0),                # residencial < 100 kWh, tipologia 1
    category(3, 0, -1.0, 100.0, share=40.0),                # residencial < 100 kWh, tipologia 2
    category(4, 0, 100.0, -1.0),                            # residencial >= 100 kWh
    category(5, -1, -1.0, 50.0, power_factor=0.95),         # A4 até 50 kW
    category(6, -1, 50.0, -1.0, power_factor=0.95),         # A4 acima de 50 kW
    category(7, 2, -1.0, -1.0),                             # industrial BT
    category(387, 0, kind="F", power_factor=1.0),           # GD solar
)
CURVES = (
    average(1, tuple(1.0 if hour < 7 or hour > 18 else 0.0 for hour in range(1, 25))),
    average(2, EVENING),
    average(3, FLAT),
    average(4, EVENING),
    average(5, FLAT),
    average(6, EVENING),
    average(7, FLAT),
    average(387, SOLAR),
    # Curvas de outros períodos não entram no cálculo.
    Curve("interplan-cat4-p1", "Curva 4 - Dia útil", FLAT, "4", "DU"),
)


class SorteadorTests(unittest.TestCase):
    def test_reproduces_the_delphi_generator(self) -> None:
        drawer = Sorteador()
        values = [drawer.percent() for _ in range(3)]
        self.assertEqual(values[0], 0.0)
        seed = 0
        expected = []
        for _ in range(3):
            seed = (seed * 0x08088405 + 1) & 0xFFFFFFFF
            expected.append(((seed * 10000) >> 32) / 100.0)
        self.assertEqual(values, expected)

    def test_a_single_candidate_does_not_draw(self) -> None:
        selector = CurveSelector(CURVES, CATEGORIES)
        drawer = Sorteador()
        (only,) = selector.bt_range(0, 150.0)
        self.assertIs(select([only], drawer), only)
        self.assertEqual(drawer.seed, 0)

    def test_the_draw_follows_the_accumulated_market_share(self) -> None:
        selector = CurveSelector(CURVES, CATEGORIES)
        candidates = selector.bt_range(0, 50.0)
        self.assertEqual([item.category_id for item in candidates], ["2", "3"])
        drawer = Sorteador()
        # O primeiro sorteio é 0,0: cai na primeira tipologia (até 60 %).
        self.assertEqual(select(candidates, drawer).category_id, "2")


class SelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = CurveSelector(CURVES, CATEGORIES)

    def test_only_average_curves_are_used_and_normalized_by_the_peak(self) -> None:
        self.assertEqual(len(self.selector), 8)
        self.assertEqual(max(self.selector.by_category["4"].pu), 1.0)
        self.assertEqual(self.selector.by_category["4"].pu[0], 0.5)

    def test_a4_rounds_the_demand_like_the_interplan(self) -> None:
        self.assertEqual(self.selector.a4(49.4).category_id, "5")
        self.assertEqual(self.selector.a4(49.6).category_id, "6")

    def test_generation_by_curve_id_or_by_type(self) -> None:
        self.assertEqual(self.selector.generation(0, "0").category_id, "387")
        self.assertEqual(self.selector.generation(None, "4").category_id, "4")
        self.assertIsNone(self.selector.generation(9, ""))

    def test_lighting(self) -> None:
        self.assertEqual(self.selector.lighting().category_id, "1")


class ResolveTests(unittest.TestCase):
    def test_adjusted_closes_both_energies(self) -> None:
        solution = resolve_dg(FLAT, SOLAR, 300.0, 120.0)
        self.assertEqual(solution.status, STATUS_ADJUSTED)
        imported, injected = energies(FLAT, SOLAR, solution.d, solution.g)
        self.assertAlmostEqual(DAYS * imported, 300.0, places=6)
        self.assertAlmostEqual(DAYS * injected, 120.0, places=6)

    def test_generation_only(self) -> None:
        solution = resolve_dg(FLAT, SOLAR, 0.0, 90.0)
        self.assertEqual((solution.status, solution.d), (STATUS_GENERATION_ONLY, 0.0))
        self.assertAlmostEqual(solution.g * DAYS * sum(SOLAR), 90.0)

    def test_full_self_consumption_reports_the_largest_silent_gd(self) -> None:
        solution = resolve_dg(FLAT, SOLAR, 240.0, 0.0)
        self.assertEqual((solution.status, solution.g), (STATUS_FULL_SELF_CONSUMPTION, 0.0))
        self.assertAlmostEqual(solution.d, 240.0 / (DAYS * 24))
        self.assertIsNotNone(solution.g_max)

    def test_no_energy(self) -> None:
        self.assertEqual(resolve_dg(FLAT, SOLAR, 0.0, 0.0).status, STATUS_NO_ENERGY)


class CalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loads = loads_model()
        rows = standard_rows()
        rows["bt_consumers"] = [
            bt(1001, 17, 120.5),                      # >= 100 kWh: tipologia única
            bt(1002, 17, 0, classe="0"),              # sem consumo e sem GD
            bt(1004, 17, 40, classe="0", ps="905"),   # < 100 kWh: sorteio
            bt(1005, 17, 80, classe="9"),             # classe sem curva
            bt(1003, 18, 300, classe="2"),            # industrial BT
        ]
        self.consumers = build_transformer_consumers(self.loads, **rows)
        self.schedule = default_calculation_levels()
        self.model = calculate_energy_demands(
            self.consumers, CURVES, CATEGORIES, (self.schedule,) * len(self.loads)
        )

    def demand(self, load_id: str, consumer_id: str):  # noqa: ANN201
        transformer = self.model.transformer(self.loads.index_for_id(load_id))
        return next(item for item in transformer.consumers if item.consumer.consumer_id == consumer_id)

    def test_a_bt_consumer_with_gd_is_adjusted(self) -> None:
        demand = self.demand("2", "1001")
        self.assertEqual(demand.status, STATUS_ADJUSTED)
        self.assertEqual(demand.load_curve.category_id, "4")
        self.assertEqual(demand.generation_curve.category_id, "387")
        self.assertAlmostEqual(demand.consumed_kwh, 120.5, places=6)
        self.assertAlmostEqual(demand.injected_kwh, 250.0, places=6)
        self.assertLess(demand.error_kwh, 1e-6)

    def test_levels_use_the_reference_hour_of_each_level(self) -> None:
        demand = self.demand("2", "1001")
        pu = demand.load_curve.pu
        expected = tuple(
            demand.demand_kw * pu[level.reference_hour - 1 if level.reference_hour else 23]
            for level in self.schedule.levels
        )
        for got, want in zip(demand.load_levels, expected):
            self.assertAlmostEqual(got, want)
        factor = math.tan(math.acos(0.92))
        for p, q in zip(demand.load_levels, demand.reactive_levels):
            self.assertAlmostEqual(q, p * factor)
        # Ao meio-dia (Tarde) a GD está no pico; à noite, zerada.
        self.assertAlmostEqual(demand.generation_levels[2], demand.generation_kw)
        self.assertEqual(demand.generation_levels[3], 0.0)

    def test_the_bt_draw_and_the_statuses(self) -> None:
        self.assertEqual(self.demand("2", "1004").load_curve.category_id, "2")
        self.assertEqual(self.demand("2", "1004").draw_percent, 0.0)
        self.assertEqual(self.demand("2", "1002").status, STATUS_ZERO_CONSUMPTION)
        self.assertEqual(self.demand("2", "1005").status, STATUS_NO_CURVE)
        industrial = self.demand("4", "1003")
        self.assertEqual(industrial.status, STATUS_NO_GD)
        self.assertAlmostEqual(industrial.demand_kw, 300.0 / (DAYS * 24))

    def test_public_lighting_uses_demand_times_lamps(self) -> None:
        demand = self.demand("2", "168662")
        self.assertEqual(demand.status, STATUS_LIGHTING)
        self.assertAlmostEqual(demand.demand_kw, 0.15000000596046448)
        # Madrugada (23 h) com a luz acesa; Tarde (12 h) apagada.
        self.assertAlmostEqual(demand.load_levels[0], demand.demand_kw)
        self.assertEqual(demand.load_levels[2], 0.0)

    def test_a4_consumers_and_their_gd(self) -> None:
        ep = self.demand("4", "556")
        self.assertEqual(ep.status, STATUS_ADJUSTED)
        self.assertIn(ep.load_curve.category_id, {"5", "6"})
        self.assertAlmostEqual(ep.power_factor, 0.95)
        transformer = self.model.transformer(self.loads.index_for_id("4"))
        (share,) = transformer.generators
        self.assertEqual(share.generator.generator_id, "132")
        self.assertAlmostEqual(share.generation_kw, ep.generation_kw)

    def test_an_inactive_consumer_is_not_computed(self) -> None:
        self.assertEqual(self.demand("3", "558").status, STATUS_INACTIVE)
        self.assertEqual(self.demand("3", "558").load_levels, ())

    def test_cargas_without_transformer_have_no_result(self) -> None:
        self.assertIsNone(self.model.transformer(self.loads.index_for_id("5")))
        self.assertTrue(self.model.transformer(self.loads.index_for_id("2")).modeled)

    def test_status_counts(self) -> None:
        counts = self.model.status_counts()
        self.assertEqual(counts[STATUS_LIGHTING], 1)
        self.assertEqual(counts[STATUS_INACTIVE], 1)

    def test_without_curves_the_issue_says_how_to_fix_it(self) -> None:
        model = calculate_energy_demands(self.consumers, (), (), (None,) * len(self.loads))
        self.assertIn("Importe o banco de curvas".casefold(), model.issues[0].casefold())


class ScheduleTests(unittest.TestCase):
    def test_without_catalog_every_load_gets_the_default(self) -> None:
        loads = loads_model()
        consumers = build_transformer_consumers(loads, transformers=[et(17, 2)],
                                                transformer_units=[utc(17, 25, 7.967, 254, 10)])
        schedule = default_calculation_levels()
        self.assertEqual(
            schedules_for_loads(consumers, None, (), schedule), (schedule,) * len(loads)
        )


class TypicalCurveTests(unittest.TestCase):
    def test_power_factor_defaults_to_0_92(self) -> None:
        typical = TypicalCurve(category(9, 0), FLAT)
        self.assertEqual(typical.power_factor, 0.92)


if __name__ == "__main__":
    unittest.main()
