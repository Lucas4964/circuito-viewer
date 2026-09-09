"""O espaço de nomes do OpenDSS quando o modelo vem de várias fontes.

``CODIGO`` é rótulo, não chave: ele nunca é alterado no modelo. Quem desempata é
``name_suffixes``, e só o exportador o consulta. Com uma fonte, o campo é
``None`` e a saída é byte a byte a de sempre.
"""

from __future__ import annotations

import unittest

from circuit_viewer.dss_names import sanitize_dss_name
from circuit_viewer.model import UtmCrs
from circuit_viewer.opendss_export import bus_namer
from circuit_viewer.source_composition import ID_SEPARATOR, compose

from tests.test_source_composition import dataset


class NameSuffixTests(unittest.TestCase):
    def test_a_single_source_has_no_suffixes_at_all(self) -> None:
        composed = compose([dataset("F1")])
        self.assertIsNone(composed.bars.name_suffixes)
        self.assertIsNone(composed.loads.name_suffixes)

    def test_disjoint_codes_produce_no_suffixes(self) -> None:
        composed = compose([dataset("F1"), dataset("F2", offset=1000)])
        self.assertIsNone(composed.bars.name_suffixes)
        self.assertEqual(composed.report.code_collisions, ())

    def test_only_the_later_source_is_suffixed(self) -> None:
        first = dataset("F1")
        composed = compose([first, dataset("F2")])
        suffixes = composed.bars.name_suffixes
        self.assertEqual(suffixes[: len(first.bars)], ("",) * len(first.bars))
        self.assertEqual(
            suffixes[len(first.bars) :], ("F2",) * len(first.bars)
        )

    def test_the_codigo_itself_is_never_touched(self) -> None:
        first = dataset("F1")
        composed = compose([first, dataset("F2")])
        self.assertEqual(
            composed.bars.codes[len(first.bars) :], first.bars.codes
        )


class BusNameTests(unittest.TestCase):
    def bus_names(self, composed) -> list[str]:  # noqa: ANN001
        namer = bus_namer(composed.catalog)
        return [namer(index) for index in range(len(composed.bars))]

    def test_one_source_names_buses_exactly_as_before(self) -> None:
        source = dataset("F1")
        composed = compose([source])
        self.assertEqual(
            self.bus_names(composed),
            [sanitize_dss_name(code) for code in source.bars.codes],
        )

    def test_repeated_codes_become_distinct_buses(self) -> None:
        """Sem o sufixo, as duas barras virariam **uma** no OpenDSS."""

        composed = compose([dataset("F1"), dataset("F2")])
        names = self.bus_names(composed)
        self.assertEqual(len(set(names)), len(names))
        self.assertIn("COD-A", names)
        self.assertIn("COD-A_F2", names)

    def test_the_suffix_survives_sanitize_dss_name(self) -> None:
        composed = compose([dataset("F1"), dataset("F2")])
        for name in self.bus_names(composed):
            with self.subTest(name):
                self.assertEqual(sanitize_dss_name(name), name)

    def test_the_qualified_identifier_also_survives(self) -> None:
        # O separador "__" fica no meio, então o strip("_") final não o come.
        self.assertEqual(
            sanitize_dss_name(f"12345{ID_SEPARATOR}F2"), f"12345{ID_SEPARATOR}F2"
        )

    def test_building_the_namer_does_no_work_per_bar(self) -> None:
        """``bus_namer`` é construído dentro de laços sobre todas as barras.

        Um pré-cálculo por barra aqui dentro viraria custo quadrático — 10¹⁰
        operações num modelo de 100 mil barras.
        """

        composed = compose([dataset("F1"), dataset("F2")])
        calls = 0
        original = type(composed.bars).name_suffixes.fget

        def counting(self):  # noqa: ANN001, ANN202
            nonlocal calls
            calls += 1
            return original(self)

        type(composed.bars).name_suffixes = property(counting)
        try:
            for _ in range(50):
                bus_namer(composed.catalog)
        finally:
            type(composed.bars).name_suffixes = property(original)
        # Uma leitura por construção, e nenhuma por barra.
        self.assertEqual(calls, 50)


class LoadNameTests(unittest.TestCase):
    def test_repeated_load_codes_are_disambiguated(self) -> None:
        first = dataset("F1")
        composed = compose([first, dataset("F2")])
        suffixes = composed.loads.name_suffixes
        self.assertIsNotNone(suffixes)
        self.assertEqual(suffixes[0], "")
        self.assertEqual(suffixes[len(first.loads)], "F2")
        # O cadastro segue intacto nas duas.
        self.assertEqual(
            composed.loads.codes[0], composed.loads.codes[len(first.loads)]
        )


class ValidationTests(unittest.TestCase):
    def test_a_suffix_vector_of_the_wrong_size_is_refused(self) -> None:
        from circuit_viewer.model import CircuitModel

        with self.assertRaises(ValueError):
            CircuitModel(
                ("1", "2"),
                ("A", "B"),
                [0.0, 1.0],
                [0.0, 1.0],
                UtmCrs(zone=21, northern=False),
                name_suffixes=("F1",),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
