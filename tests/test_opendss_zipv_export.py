"""ZIPV na exportação: só as cargas de consumo mudam de modelo."""

from __future__ import annotations

import unittest

from circuit_viewer.opendss_export import build_export, build_load_export
from circuit_viewer.opendss_settings import (
    OpenDssLoadModel,
    OpenDssLoadSettings,
    ZipvCoefficients,
)

from test_opendss_export import (
    PHASES,
    make_bars,
    make_cables,
    make_catalog,
    make_loads,
    make_network,
    make_patterns,
)


ZIPV = ZipvCoefficients(0.5, 0.2, 0.3, 0.4, 0.3, 0.3, 0.7)
VECTOR = "model=8 ZIPV=[0.5, 0.2, 0.3, 0.4, 0.3, 0.3, 0.7]"


def zipv_settings(**overrides) -> OpenDssLoadSettings:  # noqa: ANN003
    return OpenDssLoadSettings(
        load_model=OpenDssLoadModel.ZIPV, zipv=ZIPV, **overrides
    )


def load_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("New Load.")]


class LoadFileTests(unittest.TestCase):
    def _export(self, settings):  # noqa: ANN001, ANN202
        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network)
        loads = make_loads(bars, phases=("13",))
        patterns = make_patterns(loads)
        return build_load_export(
            catalog,
            loads,
            patterns,
            PHASES,
            [0],
            phase_count=3,
            load_settings=settings,
        )

    def test_constant_power_is_byte_identical_to_the_previous_behaviour(self) -> None:
        """A regressão que garante não quebrar quem já exportava."""

        without = self._export(None)
        explicit = self._export(OpenDssLoadSettings())

        self.assertEqual(without.text, explicit.text)
        for line in load_lines(without.text):
            self.assertIn(" model=1 kW=1 kvar=1 ", line)
            self.assertNotIn("ZIPV", line)

    def test_zipv_replaces_the_model_and_keeps_class_last(self) -> None:
        result = self._export(zipv_settings())

        entries = load_lines(result.text)
        self.assertEqual(len(entries), 3)
        for entry in entries:
            self.assertIn(f" {VECTOR} kW=1 kvar=1 ", entry)
            self.assertNotIn(" model=1 ", entry)
            # `class` fecha a linha, como o resto do exportador documenta.
            self.assertRegex(entry, r"class=3$")

    def test_the_header_explains_the_vector_only_in_zipv(self) -> None:
        self.assertNotIn("! ZIPV", self._export(None).text)
        self.assertIn("! ZIPV", self._export(zipv_settings()).text)


class OtherFamiliesTests(unittest.TestCase):
    """Geradores, capacitores e ramais são ``Load`` por dialeto, não por natureza."""

    def _bundle(self, settings):  # noqa: ANN001, ANN202
        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network)
        loads = make_loads(bars, phases=("13",))
        patterns = make_patterns(loads)
        return build_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            loads=loads,
            patterns=patterns,
            load_settings=settings,
        )

    def test_only_the_load_files_carry_the_vector(self) -> None:
        bundle = self._bundle(zipv_settings())

        carrying = {
            name
            for name, text in bundle.element_files
            if any("ZIPV" in line for line in load_lines(text))
        }
        self.assertEqual(carrying, {"cargastrifasicas.dss"})
        # Os arquivos das outras famílias continuam em potência constante.
        for name, text in bundle.element_files:
            if name == "cargastrifasicas.dss":
                continue
            for line in load_lines(text):
                with self.subTest(file=name):
                    self.assertIn(" model=1 ", line)

    def test_no_file_gains_the_vector_in_constant_power(self) -> None:
        bundle = self._bundle(OpenDssLoadSettings())

        for name, text in bundle.element_files:
            with self.subTest(file=name):
                self.assertNotIn("ZIPV", text)

    def test_the_vector_never_reaches_the_master(self) -> None:
        """O modelo vive na linha da carga; o master só leva os BatchEdit."""

        bundle = self._bundle(zipv_settings(voltage_limits_enabled=True))

        self.assertNotIn("ZIPV", bundle.master.text)
        self.assertIn("BatchEdit Load..* vminpu=", bundle.master.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
