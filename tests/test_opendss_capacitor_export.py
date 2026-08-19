"""Bancos de capacitores exportados como ``Load`` de reativo negativo."""

from __future__ import annotations

import unittest

from circuit_viewer.model import CapacitorModel
from circuit_viewer.opendss_export import (
    CAPACITOR_NAME_PREFIX,
    build_capacitor_export,
    phase_voltage_kv,
)

from test_opendss_export import (
    PHASES,
    data_lines,
    make_bars,
    make_catalog,
    make_network,
)


def load_entries(text: str) -> list[str]:
    return [line for line in data_lines(text) if line.startswith("New Load.")]


def shape_entries(text: str) -> list[str]:
    return [line for line in data_lines(text) if line.startswith("New LoadShape.")]


def make_capacitors(
    bars,  # noqa: ANN001
    *,
    capacitor_ids: tuple[str, ...] = ("239",),
    bar_indices: tuple[int, ...] = (1,),
    codes: tuple[str, ...] = ("CAP-1",),
    reactive: tuple[tuple[str, str, str, str], ...] = (("600", "600", "600", "600"),),
    phases: tuple[str, ...] = ("DEFN",),
) -> CapacitorModel:
    size = len(capacitor_ids)
    return CapacitorModel(
        bars,
        list(capacitor_ids),
        list(bar_indices),
        [""] * size,
        list(codes),
        ["13,8"] * size,
        [values[0] for values in reactive],
        [values[1] for values in reactive],
        [values[2] for values in reactive],
        [values[3] for values in reactive],
        list(phases),
        ["0"] * size,
    )


class CapacitorExportTests(unittest.TestCase):
    def _export(self, capacitors=None, **kwargs):  # noqa: ANN001, ANN202
        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network, **kwargs)
        model = make_capacitors(bars) if capacitors is None else capacitors
        return build_capacitor_export(catalog, model, PHASES, [0])

    def test_a_three_phase_bank_becomes_three_single_phase_loads(self) -> None:
        result = self._export()

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertEqual(result.issues, ())
        kv = phase_voltage_kv(13.8)
        self.assertEqual(
            load_entries(result.text),
            [
                f"New Load.CAP-CAP-1-3F-D phases=1 bus1=BARRA_B.1 conn=wye"
                f" kV={kv:.6g} model=1 kW=0 kvar=1"
                " daily=PERFIL-CAP-CAP-1-3F-D class=3",
                f"New Load.CAP-CAP-1-3F-E phases=1 bus1=BARRA_B.2 conn=wye"
                f" kV={kv:.6g} model=1 kW=0 kvar=1"
                " daily=PERFIL-CAP-CAP-1-3F-E class=3",
                f"New Load.CAP-CAP-1-3F-F phases=1 bus1=BARRA_B.3 conn=wye"
                f" kV={kv:.6g} model=1 kW=0 kvar=1"
                " daily=PERFIL-CAP-CAP-1-3F-F class=3",
            ],
        )

    def test_the_reactive_profile_is_negative_and_split_by_phase(self) -> None:
        """600 kvar trifásicos viram 200 por fase; sem a divisão seriam 1800."""

        result = self._export(
            capacitors=make_capacitors(
                make_bars(), reactive=(("600", "300", "0", "1200"),)
            )
        )

        self.assertEqual(
            shape_entries(result.text)[0],
            "New LoadShape.PERFIL-CAP-CAP-1-3F-D npts=4 interval=1"
            " mult=[0.000000 0.000000 0.000000 0.000000]"
            " qmult=[-200.000000 -100.000000 0.000000 -400.000000]",
        )

    def test_the_active_profile_stays_zero(self) -> None:
        result = self._export()

        for entry in shape_entries(result.text):
            self.assertIn(
                "mult=[0.000000 0.000000 0.000000 0.000000]", entry
            )
        for entry in load_entries(result.text):
            self.assertIn(" kW=0 kvar=1", entry)

    def test_every_shape_precedes_every_load(self) -> None:
        result = self._export()

        kinds = [line.split(".", 1)[0] for line in data_lines(result.text)]
        self.assertEqual(
            kinds,
            ["New LoadShape"] * 3 + ["New Load"] * 3,
        )

    def test_a_single_phase_bank_keeps_the_whole_reactive_power(self) -> None:
        result = self._export(
            capacitors=make_capacitors(make_bars(), phases=("D",))
        )

        entries = load_entries(result.text)
        self.assertEqual(len(entries), 1)
        self.assertIn("CAP-CAP-1-1F-D", entries[0])
        self.assertIn("bus1=BARRA_B.1", entries[0])
        self.assertIn("qmult=[-600.000000", shape_entries(result.text)[0])

    def test_the_neutral_letter_is_ignored(self) -> None:
        with_neutral = self._export(
            capacitors=make_capacitors(make_bars(), phases=("DEFN",))
        )
        without_neutral = self._export(
            capacitors=make_capacitors(make_bars(), phases=("DEF",))
        )

        self.assertEqual(with_neutral.text, without_neutral.text)

    def test_phases_that_do_not_resolve_discard_the_bank(self) -> None:
        for label in ("", "N", "DD", "XYZ"):
            with self.subTest(phases=label):
                result = self._export(
                    capacitors=make_capacitors(make_bars(), phases=(label,))
                )

                self.assertEqual(result.exported_count, 0)
                self.assertEqual(result.discarded_count, 1)
                self.assertTrue(
                    any("FASES" in issue.reason for issue in result.issues),
                    result.issues,
                )

    def test_a_non_numeric_reactive_power_discards_the_whole_bank(self) -> None:
        result = self._export(
            capacitors=make_capacitors(
                make_bars(), reactive=(("600", "zero", "600", "600"),)
            )
        )

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertTrue(
            any("Q2" in issue.reason for issue in result.issues), result.issues
        )
        self.assertEqual(load_entries(result.text), [])

    def test_an_empty_code_falls_back_to_the_capacitor_id(self) -> None:
        result = self._export(
            capacitors=make_capacitors(make_bars(), codes=("",))
        )

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn(f"New Load.{CAPACITOR_NAME_PREFIX}239-3F-D", result.text)
        self.assertTrue(
            any("CAPAC_ID" in issue.reason for issue in result.issues),
            result.issues,
        )

    def test_a_reserved_name_discards_the_bank(self) -> None:
        """``Load.*`` é namespace único: cargas e geradores têm prioridade."""

        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network)

        result = build_capacitor_export(
            catalog,
            make_capacitors(bars),
            PHASES,
            [0],
            reserved_names=frozenset({"CAP-CAP-1-3F-E"}),
        )

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertTrue(
            any("já usado" in issue.reason for issue in result.issues),
            result.issues,
        )

    def test_a_bank_inside_a_branch_is_omitted_with_a_diagnosis(self) -> None:
        """Na rede simplificada o banco de um ramal não é representado."""

        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network)

        result = build_capacitor_export(
            catalog,
            make_capacitors(bars),
            PHASES,
            [0],
            include_bar_indices=frozenset({0}),
        )

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertEqual(load_entries(result.text), [])
        self.assertTrue(
            any("dentro de um ramal" in issue.reason for issue in result.issues),
            result.issues,
        )

    def test_a_circuit_without_voltage_discards_the_bank(self) -> None:
        result = self._export(voltage="")

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertTrue(
            any("VNOM" in issue.reason for issue in result.issues), result.issues
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
