"""Modelo ZIP por carga vindo do cadastro (MODELO_CARGA_SIZ do Interplan)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from circuit_viewer.load_pattern_import import (
    load_load_patterns_csv,
    parse_load_pattern_rows,
)
from circuit_viewer.opendss_export import registry_zip_weights
from circuit_viewer.opendss_settings import (
    OpenDssLoadModel,
    OpenDssLoadSettings,
    ZipvCoefficients,
)

from tests.test_opendss_export import (
    export_loads,
    load_entries,
    make_bars,
    make_loads,
    make_patterns,
)


PATTERN_VALUES = ("10", "0", "0", "5", "0", "0")


def zip_patterns(loads, *rows):  # noqa: ANN001, ANN002, ANN201
    """Patamares da carga 0 com os pesos (Z, I, P) de cada NPAT."""

    return make_patterns(
        loads,
        groups={0: tuple((*PATTERN_VALUES, *row) for row in rows)},
    )


class RegistryZipWeightsTests(unittest.TestCase):
    def test_percent_and_fraction_are_both_accepted(self) -> None:
        loads = make_loads(make_bars())
        for rows in (
            [("0", "60", "40")] * 4,
            [("0", "0.6", "0.4")] * 4,
            [("0", "0,6", "0,4")] * 4,
        ):
            with self.subTest(rows=rows[0]):
                group = zip_patterns(loads, *rows).records_for_load(0)

                weights, notes = registry_zip_weights(group)

                self.assertEqual(weights, (0.0, 0.6, 0.4))
                self.assertEqual(notes, [])

    def test_empty_registry_means_no_model(self) -> None:
        loads = make_loads(make_bars())

        weights, notes = registry_zip_weights(make_patterns(loads).records_for_load(0))

        self.assertIsNone(weights)
        self.assertEqual(notes, [])

    def test_weights_that_do_not_close_are_ignored_with_a_note(self) -> None:
        loads = make_loads(make_bars())
        group = zip_patterns(
            loads,
            ("0", "60", "30"),
            ("0", "60", "40"),
            ("0", "60", "40"),
            ("0", "60", "40"),
        ).records_for_load(0)

        weights, notes = registry_zip_weights(group)

        self.assertEqual(weights, (0.0, 0.6, 0.4))
        self.assertEqual(len(notes), 1)
        self.assertIn("NPAT 0", notes[0])

    def test_different_models_between_levels_use_the_most_frequent(self) -> None:
        loads = make_loads(make_bars())
        group = zip_patterns(
            loads,
            ("100", "0", "0"),
            ("0", "60", "40"),
            ("0", "60", "40"),
            ("100", "0", "0"),
        ).records_for_load(0)

        weights, notes = registry_zip_weights(group)

        # Empate: vence o do primeiro patamar.
        self.assertEqual(weights, (1.0, 0.0, 0.0))
        self.assertTrue(any("diferente entre patamares" in note for note in notes))


class RegistryZipExportTests(unittest.TestCase):
    def test_the_load_uses_its_own_model_over_the_settings(self) -> None:
        loads = make_loads(make_bars())
        settings = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.5,
            vmaxpu=1.5,
            load_model=OpenDssLoadModel.ZIPV,
            zipv=ZipvCoefficients(0.95, 0.0, 0.05, 1.0, 0.0, 0.0, 0.0),
        )

        result = export_loads(
            loads,
            zip_patterns(loads, *[("0", "60", "40")] * 4),
            1,
            load_settings=settings,
        )

        self.assertIn(
            " model=8 ZIPV=[0, 0.6, 0.4, 0, 0.6, 0.4, 0] ",
            load_entries(result.text)[0],
        )
        self.assertEqual(result.issues, ())
        self.assertIn("MODELO_CARGA_SIZ", result.text)

    def test_loads_without_registry_keep_the_configured_model(self) -> None:
        loads = make_loads(make_bars())

        result = export_loads(loads, make_patterns(loads), 1)

        self.assertIn(" model=1 ", load_entries(result.text)[0])
        self.assertNotIn("MODELO_CARGA_SIZ", result.text)

    def test_registry_model_without_voltage_limits_is_warned(self) -> None:
        # Com a faixa padrão (0,95–1,05) o OpenDSS ignora o ZIPV justamente
        # nas barras mais baixas: a exportação avisa, sem descartar nada.
        loads = make_loads(make_bars())

        result = export_loads(
            loads, zip_patterns(loads, *[("0", "60", "40")] * 4), 1
        )

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertTrue(
            any("limites de tensão" in issue.reason for issue in result.issues),
            result.issues,
        )


class RegistryZipImportTests(unittest.TestCase):
    def test_auxiliary_table_fills_the_records_by_load_and_level(self) -> None:
        loads = make_loads(make_bars())
        header = ("CARGA_ID", "NPAT", "PD", "PE", "PF", "QD", "QE", "QF")
        rows = [("CG1", str(npat), "1", "0", "0", "1", "0", "0") for npat in range(4)]
        zip_header = ("CARGA_ID", "NPAT", "SCTE", "ICTE", "ZCTE")
        zip_rows = [
            ("CG1", "0", "40", "60", "0"),
            ("CG1", "3", "100", "0", "0"),
            ("OUTRA", "1", "0", "0", "100"),
        ]

        result = parse_load_pattern_rows(
            header,
            rows,
            loads,
            source_label="rede.mdb",
            encoding="mdb",
            zip_header=zip_header,
            zip_rows=zip_rows,
        )

        group = result.model.records_for_load(0)
        self.assertEqual((group[0].zcte, group[0].icte, group[0].scte), ("0", "60", "40"))
        self.assertEqual((group[1].zcte, group[1].icte, group[1].scte), ("", "", ""))
        self.assertEqual(group[3].scte, "100")
        # A linha sem carga vira ocorrência, mas não invalida os patamares.
        self.assertEqual(result.valid_rows, 4)
        self.assertTrue(
            any("carga inexistente: OUTRA" in issue.reason for issue in result.issues)
        )

    def test_csv_may_carry_the_zip_columns(self) -> None:
        loads = make_loads(make_bars())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patamares.csv"
            path.write_text(
                "CARGA_ID;NPAT;PD;PE;PF;QD;QE;QF;ZCTE;ICTE;SCTE\n"
                + "".join(f"CG1;{npat};1;0;0;1;0;0;0;60;40\n" for npat in range(4)),
                encoding="utf-8",
            )

            result = load_load_patterns_csv(path, loads)

        weights, _ = registry_zip_weights(result.model.records_for_load(0))
        self.assertEqual(weights, (0.0, 0.6, 0.4))


if __name__ == "__main__":
    unittest.main()
