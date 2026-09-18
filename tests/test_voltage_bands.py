from __future__ import annotations

import cmath
import math
import unittest

import numpy as np

from circuit_viewer.opendss_powerflow import (
    DEAD_NODE_PU,
    LINE_VOLTAGE_PU_BASE,
    BarVoltages,
)
from circuit_viewer.voltage_bands import (
    ReservedColors,
    VoltageBand,
    VoltageBandTable,
    VoltageBandsError,
    VoltageQuantity,
    default_voltage_band_table,
)


def balanced(magnitude: float, nodes: tuple[int, ...] = (1, 2, 3)) -> BarVoltages:
    """Barra equilibrada: o mesmo módulo nas fases presentes, 120° entre elas."""

    angles = {1: 0.0, 2: -120.0, 3: 120.0}
    return BarVoltages(
        nodes=nodes,
        magnitudes=((13_800.0,) * len(nodes),),
        per_unit=((magnitude,) * len(nodes),),
        angles=(tuple(angles[node] for node in nodes),),
    )


class VoltageBandTests(unittest.TestCase):
    def test_a_band_normalizes_its_label_and_color(self) -> None:
        band = VoltageBand("  Adequada  ", "#2e7d32", 0.93, 1.05, 0)
        self.assertEqual(band.label, "Adequada")
        self.assertEqual(band.color, "#2E7D32")

    def test_an_invalid_color_names_the_band(self) -> None:
        with self.assertRaises(VoltageBandsError) as error:
            VoltageBand("Adequada", "verde", 0.93, 1.05, 0)
        self.assertIn("Adequada", str(error.exception))

    def test_an_empty_interval_is_rejected(self) -> None:
        with self.assertRaises(VoltageBandsError):
            VoltageBand("Vazia", "#FFFFFF", 1.0, 1.0, 0)

    def test_the_interval_is_closed_at_both_ends(self) -> None:
        band = VoltageBand("Adequada", "#2E7D32", 0.93, 1.05, 0)
        self.assertTrue(band.contains(0.93))
        self.assertTrue(band.contains(1.05))
        self.assertFalse(band.contains(0.9299))


class DefaultTableTests(unittest.TestCase):
    """As bordas do PRODIST Módulo 8 para média tensão."""

    def setUp(self) -> None:
        self.table = default_voltage_band_table()

    def label_of(self, value: float) -> str:
        return self.table.bands[self.table.band_index_of(value)].label

    def test_the_prodist_boundaries_fall_where_the_module_says(self) -> None:
        # 0,93 e 1,05 pertencem a "adequada": ela é fechada nos dois extremos, e
        # é por isso que a primeira faixa que contém o valor é que vence.
        self.assertEqual(self.label_of(0.93), "Adequada")
        self.assertEqual(self.label_of(1.05), "Adequada")
        self.assertEqual(self.label_of(1.00), "Adequada")
        self.assertEqual(self.label_of(0.90), "Precária (subtensão)")
        self.assertEqual(self.label_of(0.92), "Precária (subtensão)")
        self.assertEqual(self.label_of(0.8999), "Crítica (subtensão)")
        self.assertEqual(self.label_of(0.0), "Crítica (subtensão)")
        self.assertEqual(self.label_of(1.0501), "Crítica (sobretensão)")
        self.assertEqual(self.label_of(2.0), "Crítica (sobretensão)")

    def test_under_and_overvoltage_share_severity_but_not_color(self) -> None:
        """É o ponto do pedido: a gravidade empata, a cor não pode empatar.

        Pintar as duas pontas de vermelho tornaria o mapa ambíguo justamente
        onde ele precisa ser imediato — alta demais ou baixa demais.
        """

        low = self.table.bands[self.table.band_index_of(0.85)]
        high = self.table.bands[self.table.band_index_of(1.10)]
        self.assertEqual(low.severity, high.severity)
        self.assertNotEqual(low.color, high.color)
        self.assertNotEqual(low.label, high.label)

    def test_every_color_in_the_palette_is_distinct(self) -> None:
        palette = self.table.colors
        self.assertEqual(len(palette), len(set(palette)))

    def test_the_palette_carries_the_bands_then_the_reserved(self) -> None:
        self.assertEqual(
            self.table.colors,
            tuple(band.color for band in self.table.bands)
            + ("#000000", "#9E9E9E", "#E0E0E0"),
        )
        self.assertEqual(self.table.no_line_voltage_index, len(self.table.bands))
        self.assertEqual(
            self.table.labels[self.table.no_line_voltage_index],
            "Sem tensão de linha",
        )

    def test_the_worst_band_is_the_one_with_the_highest_severity(self) -> None:
        index, value = self.table.worst_index([1.00, 0.91, 0.99])
        self.assertEqual(self.table.bands[index].label, "Precária (subtensão)")
        self.assertAlmostEqual(value, 0.91)

    def test_a_tie_between_the_two_criticals_goes_to_the_farther_value(self) -> None:
        # 0,15 pu de afundamento é pior que 0,10 pu de elevação, e a cor tem de
        # corresponder ao número que o tooltip mostra.
        index, value = self.table.worst_index([0.85, 1.10])
        self.assertEqual(self.table.bands[index].label, "Crítica (subtensão)")
        self.assertAlmostEqual(value, 0.85)

        index, value = self.table.worst_index([0.95, 1.30])
        self.assertEqual(self.table.bands[index].label, "Crítica (sobretensão)")
        self.assertAlmostEqual(value, 1.30)


class TableValidationTests(unittest.TestCase):
    def test_a_gap_between_bands_is_rejected(self) -> None:
        with self.assertRaises(VoltageBandsError) as error:
            VoltageBandTable(
                (
                    VoltageBand("Baixa", "#C62828", 0.0, 0.90, 2),
                    VoltageBand("Alta", "#2E7D32", 0.95, math.inf, 0),
                )
            )
        self.assertIn("lacuna", str(error.exception))

    def test_the_table_must_start_at_zero(self) -> None:
        with self.assertRaises(VoltageBandsError):
            VoltageBandTable((VoltageBand("Alta", "#2E7D32", 0.5, math.inf, 0),))

    def test_the_table_must_reach_infinity(self) -> None:
        with self.assertRaises(VoltageBandsError) as error:
            VoltageBandTable((VoltageBand("Tudo", "#2E7D32", 0.0, 2.0, 0),))
        self.assertIn("infinito", str(error.exception))

    def test_an_empty_table_is_rejected(self) -> None:
        with self.assertRaises(VoltageBandsError):
            VoltageBandTable(())

    def test_overlap_is_allowed_and_resolved_by_order(self) -> None:
        # É o que deixa "adequada" fechada nos dois extremos sem inventar uma
        # inclusividade por ponta.
        table = default_voltage_band_table()
        overlapping = [
            band
            for band in table.bands
            if band.contains(0.93)
        ]
        self.assertEqual(len(overlapping), 2)
        self.assertEqual(table.bands[table.band_index_of(0.93)].label, "Adequada")

    def test_an_extra_high_precarious_band_is_accepted(self) -> None:
        """O PRODIST MT não define uma, mas a tabela é do usuário."""

        table = VoltageBandTable(
            (
                VoltageBand("Adequada", "#2E7D32", 0.93, 1.05, 0),
                VoltageBand("Precária alta", "#EF6C00", 1.05, 1.08, 1),
                VoltageBand("Precária baixa", "#F9A825", 0.90, 0.93, 1),
                VoltageBand("Crítica baixa", "#C62828", 0.0, 0.90, 2),
                VoltageBand("Crítica alta", "#6A1B9A", 1.08, math.inf, 2),
            )
        )
        self.assertEqual(table.bands[table.band_index_of(1.06)].label, "Precária alta")
        self.assertEqual(table.bands[table.band_index_of(1.09)].label, "Crítica alta")

    def test_reserved_colors_are_normalized(self) -> None:
        reserved = ReservedColors(no_line_voltage="#000000", de_energized="#9e9e9e")
        self.assertEqual(reserved.de_energized, "#9E9E9E")

    def test_an_invalid_reserved_color_names_the_category(self) -> None:
        with self.assertRaises(VoltageBandsError) as error:
            ReservedColors(no_line_voltage="preto")
        self.assertIn("Sem tensão de linha", str(error.exception))


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = default_voltage_band_table()

    def classify(self, voltages, quantity=VoltageQuantity.PHASE, count=1, step=0):
        return self.table.classify(count, voltages, quantity, step)

    def label_of(self, result, bar_index: int = 0) -> str:
        return result.labels[int(result.style_indices[bar_index])]

    def test_a_bar_without_result_is_not_a_bar_without_voltage(self) -> None:
        result = self.classify({}, count=2)
        self.assertEqual(self.label_of(result, 0), "Sem resultado")
        self.assertEqual(self.label_of(result, 1), "Sem resultado")
        self.assertTrue(math.isnan(float(result.values_pu[0])))

    def test_a_de_energized_bar_is_not_critical(self) -> None:
        """Zero pu é ausência de tensão, não uma tensão que afundou."""

        dead = BarVoltages(
            nodes=(1, 2, 3),
            magnitudes=((0.0, 0.0, 0.0),),
            per_unit=((0.0, DEAD_NODE_PU / 2.0, 0.0),),
            angles=((0.0, 0.0, 0.0),),
        )
        for quantity in VoltageQuantity:
            with self.subTest(quantity.name):
                result = self.classify({0: dead}, quantity)
                self.assertEqual(self.label_of(result), "Sem tensão")

    def test_phase_mode_uses_the_per_unit_of_the_step(self) -> None:
        result = self.classify({0: balanced(0.91)})
        self.assertEqual(self.label_of(result), "Precária (subtensão)")
        self.assertAlmostEqual(float(result.values_pu[0]), 0.91)

    def test_phase_mode_takes_the_worst_phase(self) -> None:
        uneven = BarVoltages(
            nodes=(1, 2, 3),
            magnitudes=((13_800.0,) * 3,),
            per_unit=((1.00, 0.88, 0.97),),
            angles=((0.0, -120.0, 120.0),),
        )
        result = self.classify({0: uneven})
        self.assertEqual(self.label_of(result), "Crítica (subtensão)")
        self.assertAlmostEqual(float(result.values_pu[0]), 0.88)

    # -- modo de linha ----------------------------------------------------

    def test_a_single_phase_bar_has_no_line_voltage(self) -> None:
        """O pedido: no modo de linha, o ramal monofásico fica preto."""

        result = self.classify({0: balanced(0.99, nodes=(3,))}, VoltageQuantity.LINE)
        self.assertEqual(self.label_of(result), "Sem tensão de linha")
        self.assertEqual(
            result.colors[int(result.style_indices[0])],
            "#000000",
        )
        self.assertTrue(math.isnan(float(result.values_pu[0])))

    def test_the_same_single_phase_bar_is_classified_in_phase_mode(self) -> None:
        # A barra não é "sem dado": ela só não participa daquela medida.
        result = self.classify({0: balanced(0.99, nodes=(3,))}, VoltageQuantity.PHASE)
        self.assertEqual(self.label_of(result), "Adequada")

    def test_line_mode_renormalizes_by_the_square_root_of_three(self) -> None:
        # Trifásica equilibrada em 1,0 pu de fase dá √3 pu de linha na base de
        # fase, ou seja 1,0 pu depois da renormalização.
        result = self.classify({0: balanced(1.0)}, VoltageQuantity.LINE)
        self.assertEqual(self.label_of(result), "Adequada")
        self.assertAlmostEqual(float(result.values_pu[0]), 1.0, places=6)

    def test_line_mode_subtracts_phasors_and_not_magnitudes(self) -> None:
        """Com módulos apenas, 1,0 − 1,0 daria zero e a barra sairia crítica."""

        result = self.classify({0: balanced(1.0)}, VoltageQuantity.LINE)
        expected = abs(
            cmath.rect(1.0, 0.0) - cmath.rect(1.0, math.radians(-120.0))
        ) / LINE_VOLTAGE_PU_BASE
        self.assertAlmostEqual(float(result.values_pu[0]), expected, places=9)
        self.assertNotEqual(self.label_of(result), "Crítica (subtensão)")

    def test_a_two_phase_bar_has_one_line_voltage(self) -> None:
        result = self.classify({0: balanced(1.0, nodes=(1, 2))}, VoltageQuantity.LINE)
        self.assertEqual(self.label_of(result), "Adequada")

    def test_a_dead_phase_is_left_out_of_the_pairs(self) -> None:
        """Trifásica com uma fase aberta tem **uma** tensão de linha, não três.

        Sem o filtro, as outras duas seriam medidas contra a fase morta e a
        barra sairia crítica por um par que não existe.
        """

        one_open = BarVoltages(
            nodes=(1, 2, 3),
            magnitudes=((13_800.0, 13_800.0, 0.0),),
            per_unit=((1.0, 1.0, 0.0),),
            angles=((0.0, -120.0, 0.0),),
        )
        result = self.classify({0: one_open}, VoltageQuantity.LINE)
        self.assertEqual(self.label_of(result), "Adequada")
        self.assertAlmostEqual(float(result.values_pu[0]), 1.0, places=6)

    def test_two_dead_phases_leave_no_line_voltage(self) -> None:
        two_open = BarVoltages(
            nodes=(1, 2, 3),
            magnitudes=((13_800.0, 0.0, 0.0),),
            per_unit=((1.0, 0.0, 0.0),),
            angles=((0.0, 0.0, 0.0),),
        )
        result = self.classify({0: two_open}, VoltageQuantity.LINE)
        self.assertEqual(self.label_of(result), "Sem tensão de linha")

    def test_missing_angles_cannot_be_passed_off_as_line_voltage(self) -> None:
        without_angles = BarVoltages(
            nodes=(1, 2, 3),
            magnitudes=((13_800.0,) * 3,),
            per_unit=((1.0,) * 3,),
        )
        result = self.classify({0: without_angles}, VoltageQuantity.LINE)
        self.assertEqual(self.label_of(result), "Sem tensão de linha")

    # -- forma do resultado -----------------------------------------------

    def test_the_result_mirrors_the_phase_classification_contract(self) -> None:
        result = self.classify({0: balanced(1.0)}, count=3)
        self.assertEqual(result.style_indices.dtype, np.dtype(np.intp))
        self.assertEqual(result.style_indices.ndim, 1)
        self.assertFalse(result.style_indices.flags.writeable)
        self.assertFalse(result.values_pu.flags.writeable)
        self.assertEqual(result.style_indices.size, 3)
        self.assertEqual(result.values_pu.size, 3)

    def test_every_index_addresses_a_color(self) -> None:
        result = self.classify({0: balanced(1.0)}, count=4)
        self.assertEqual(len(result.colors), len(result.labels))
        self.assertLess(int(result.style_indices.max()), len(result.colors))
        self.assertGreaterEqual(int(result.style_indices.min()), 0)

    def test_the_counts_add_up_to_the_number_of_bars(self) -> None:
        voltages = {0: balanced(1.0), 1: balanced(0.85), 2: balanced(0.91)}
        result = self.classify(voltages, count=5)
        self.assertEqual(sum(result.counts), 5)
        self.assertEqual(result.counts[result.labels.index("Sem resultado")], 2)

    def test_the_tooltip_names_the_quantity_actually_used(self) -> None:
        result = self.classify({0: balanced(0.91)}, VoltageQuantity.PHASE)
        self.assertIn("tensão de fase", result.describe(0))
        self.assertIn("0.910 pu", result.describe(0))

        result = self.classify({0: balanced(0.99, nodes=(3,))}, VoltageQuantity.LINE)
        self.assertEqual(result.describe(0), "Sem tensão de linha")

    def test_a_step_beyond_the_result_is_reported_as_missing(self) -> None:
        result = self.classify({0: balanced(1.0)}, step=3)
        self.assertEqual(self.label_of(result), "Sem resultado")

    def test_bars_outside_the_model_are_ignored(self) -> None:
        # A composição de fontes pode deixar um índice órfão; isso não pode
        # derrubar a classificação inteira.
        result = self.classify({0: balanced(1.0), 99: balanced(0.5)}, count=1)
        self.assertEqual(result.style_indices.size, 1)
        self.assertEqual(self.label_of(result), "Adequada")


if __name__ == "__main__":
    unittest.main()
