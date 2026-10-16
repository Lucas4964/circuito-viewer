from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from circuit_viewer.curvas import HOURLY_CURVE_POINT_COUNT, Curve, new_curve_id
from circuit_viewer.curvas_categorias import CurveCategory
from circuit_viewer.curvas_store import (
    CURVES_FILE_VERSION,
    categories_path_for,
    default_categories_path,
    default_curves_path,
    load_curve_categories,
    load_curves,
    save_curve_categories,
    save_curves,
)


def _values(start: float = 0.0) -> tuple[float, ...]:
    return tuple(start + hour for hour in range(HOURLY_CURVE_POINT_COUNT))


class CurvesPathTests(unittest.TestCase):
    def test_default_path_lives_beside_the_package(self) -> None:
        path = default_curves_path()
        self.assertEqual(path.name, "curvas.json")
        self.assertEqual(path.parent.name, "dados")
        self.assertEqual(path.parent.parent.name, "circuit_viewer")


class CurvesRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "curvas.json"

    def test_round_trip_preserves_id_name_and_values(self) -> None:
        original = (
            Curve(new_curve_id(), "Residencial típica", _values()),
            Curve(new_curve_id(), "Geração", _values(-12.0)),
        )
        save_curves(original, self.path)
        result = load_curves(self.path)
        self.assertIsNone(result.issue)
        self.assertEqual(result.curves, original)

    def test_zero_and_negative_survive(self) -> None:
        values = tuple(float(v) for v in range(-12, 12))
        save_curves((Curve("id", "X", values),), self.path)
        self.assertEqual(load_curves(self.path).curves[0].values, values)

    def test_creates_missing_directory(self) -> None:
        nested = Path(self._directory.name) / "a" / "b" / "curvas.json"
        save_curves((Curve("id", "X", _values()),), nested)
        self.assertTrue(nested.is_file())

    def test_overwrites_existing_file(self) -> None:
        """Regressão: no Windows, os.rename falha quando o destino existe."""

        save_curves((Curve("id", "Primeira", _values()),), self.path)
        save_curves((Curve("id", "Segunda", _values()),), self.path)
        self.assertEqual(load_curves(self.path).curves[0].name, "Segunda")

    def test_no_temporary_file_is_left_behind(self) -> None:
        save_curves((Curve("id", "X", _values()),), self.path)
        leftovers = list(self.path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_saved_file_is_readable_json_with_version(self) -> None:
        save_curves((Curve("id", "Ação", _values()),), self.path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], CURVES_FILE_VERSION)
        self.assertEqual(payload["curvas"][0]["nome"], "Ação")
        # ensure_ascii=False: acentos legíveis a olho nu no arquivo.
        self.assertIn("Ação", self.path.read_text(encoding="utf-8"))

    def test_empty_catalog_round_trip(self) -> None:
        save_curves((), self.path)
        result = load_curves(self.path)
        self.assertEqual(result.curves, ())
        self.assertIsNone(result.issue)


class CurvesLoadToleranceTests(unittest.TestCase):
    """Ler nunca levanta: um arquivo ruim não pode impedir o programa de abrir."""

    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "curvas.json"

    def _write(self, payload: object) -> None:
        if isinstance(payload, str):
            self.path.write_text(payload, encoding="utf-8")
        else:
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

    def _entry(self, name: str = "X", curve_id: str | None = None) -> dict:
        return {
            "id": curve_id or new_curve_id(),
            "nome": name,
            "valores": list(_values()),
        }

    def test_missing_file_is_not_a_problem(self) -> None:
        result = load_curves(self.path)
        self.assertEqual(result.curves, ())
        self.assertIsNone(result.issue)

    def test_corrupted_json_does_not_raise(self) -> None:
        self._write("{ isto nao e json")
        result = load_curves(self.path)
        self.assertEqual(result.curves, ())
        self.assertIsNotNone(result.issue)

    def test_root_is_not_an_object(self) -> None:
        self._write([1, 2, 3])
        result = load_curves(self.path)
        self.assertEqual(result.curves, ())
        self.assertIsNotNone(result.issue)

    def test_missing_curve_list(self) -> None:
        self._write({"version": 1})
        result = load_curves(self.path)
        self.assertEqual(result.curves, ())
        self.assertIsNotNone(result.issue)

    def test_invalid_entry_among_valid_ones(self) -> None:
        self._write(
            {
                "version": 1,
                "curvas": [
                    self._entry("Boa 1"),
                    {"nome": "Curta", "valores": [1, 2, 3]},
                    self._entry("Boa 2"),
                ],
            }
        )
        result = load_curves(self.path)
        self.assertEqual(
            [curve.name for curve in result.curves], ["Boa 1", "Boa 2"]
        )
        self.assertIn("1", result.issue)

    def test_newer_version_still_reads(self) -> None:
        self._write(
            {"version": CURVES_FILE_VERSION + 1, "curvas": [self._entry("Nova")]}
        )
        result = load_curves(self.path)
        self.assertEqual(len(result.curves), 1)
        self.assertIsNotNone(result.issue)

    def test_duplicate_ids_keep_the_first(self) -> None:
        self._write(
            {
                "version": 1,
                "curvas": [
                    self._entry("Primeira", curve_id="mesmo"),
                    self._entry("Segunda", curve_id="mesmo"),
                ],
            }
        )
        result = load_curves(self.path)
        self.assertEqual([c.name for c in result.curves], ["Primeira"])
        self.assertIsNotNone(result.issue)

    def test_duplicate_names_keep_the_first(self) -> None:
        self._write(
            {
                "version": 1,
                "curvas": [self._entry("Igual"), self._entry("igual")],
            }
        )
        result = load_curves(self.path)
        self.assertEqual(len(result.curves), 1)

    def test_missing_id_is_generated(self) -> None:
        """Um arquivo escrito à mão não tem por que inventar um uuid."""

        self._write(
            {"version": 1, "curvas": [{"nome": "X", "valores": list(_values())}]}
        )
        result = load_curves(self.path)
        self.assertEqual(len(result.curves), 1)
        self.assertTrue(result.curves[0].curve_id)

    def test_unknown_keys_are_ignored(self) -> None:
        """É o que permitirá acrescentar "tipo" no futuro sem virar a versão."""

        entry = self._entry("Com extras")
        entry["tipo"] = "geracao"
        entry["observacao"] = "qualquer coisa"
        self._write({"version": 1, "curvas": [entry]})
        result = load_curves(self.path)
        self.assertEqual(len(result.curves), 1)
        self.assertIsNone(result.issue)

    def test_text_values_with_comma_are_accepted(self) -> None:
        entry = {
            "id": "abc",
            "nome": "Texto",
            "valores": ["0,5"] * HOURLY_CURVE_POINT_COUNT,
        }
        self._write({"version": 1, "curvas": [entry]})
        result = load_curves(self.path)
        self.assertEqual(result.curves[0].values[0], 0.5)

    def test_non_finite_value_discards_the_entry(self) -> None:
        entry = {
            "id": "abc",
            "nome": "Ruim",
            "valores": ["nan"] * HOURLY_CURVE_POINT_COUNT,
        }
        self._write({"version": 1, "curvas": [entry]})
        result = load_curves(self.path)
        self.assertEqual(result.curves, ())
        self.assertIsNotNone(result.issue)

    def test_boolean_is_not_a_number(self) -> None:
        entry = {
            "id": "abc",
            "nome": "Booleano",
            "valores": [True] * HOURLY_CURVE_POINT_COUNT,
        }
        self._write({"version": 1, "curvas": [entry]})
        self.assertEqual(load_curves(self.path).curves, ())


class CurveLinkIdTests(unittest.TestCase):
    """O ID de vínculo (CAT_ID nas curvas do Interplan) é opcional e textual."""

    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "curvas.json"

    def _write(self, entry: dict) -> None:
        self.path.write_text(
            json.dumps({"version": 1, "curvas": [entry]}), encoding="utf-8"
        )

    def test_round_trip_preserves_the_link_id(self) -> None:
        original = (Curve("a", "Residencial - Dia útil", _values(), "2"),)
        save_curves(original, self.path)
        self.assertEqual(load_curves(self.path).curves, original)

    def test_curves_without_link_id_keep_the_old_format(self) -> None:
        save_curves((Curve("a", "X", _values()),), self.path)
        entry = json.loads(self.path.read_text(encoding="utf-8"))["curvas"][0]
        self.assertNotIn("id_vinculo", entry)
        self.assertEqual(load_curves(self.path).curves[0].link_id, "")

    def test_numeric_link_id_is_read_as_text(self) -> None:
        self._write({"id": "a", "id_vinculo": 387, "nome": "X", "valores": list(_values())})
        self.assertEqual(load_curves(self.path).curves[0].link_id, "387")

    def test_invalid_link_id_is_dropped_but_keeps_the_curve(self) -> None:
        self._write({"id": "a", "id_vinculo": [1], "nome": "X", "valores": list(_values())})
        curves = load_curves(self.path).curves
        self.assertEqual(len(curves), 1)
        self.assertEqual(curves[0].link_id, "")

    def test_link_id_does_not_need_to_be_unique(self) -> None:
        original = (
            Curve("a", "Categoria - Dia útil", _values(), "2"),
            Curve("b", "Categoria - Sábado", _values(1.0), "2"),
        )
        save_curves(original, self.path)
        result = load_curves(self.path)
        self.assertIsNone(result.issue)
        self.assertEqual(result.curves, original)


class CurvePeriodTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "curvas.json"

    def _write(self, entry: dict) -> None:
        self.path.write_text(
            json.dumps({"version": 1, "curvas": [entry]}), encoding="utf-8"
        )

    def test_round_trip_preserves_the_period(self) -> None:
        original = (Curve("a", "Residencial - Sábado", _values(), "2", "SA"),)
        save_curves(original, self.path)
        self.assertEqual(load_curves(self.path).curves, original)

    def test_curves_without_period_keep_the_old_format(self) -> None:
        save_curves((Curve("a", "X", _values()),), self.path)
        entry = json.loads(self.path.read_text(encoding="utf-8"))["curvas"][0]
        self.assertNotIn("periodo", entry)

    def test_lowercase_period_is_accepted(self) -> None:
        self._write({"id": "a", "periodo": " du ", "nome": "X", "valores": list(_values())})
        self.assertEqual(load_curves(self.path).curves[0].period, "DU")

    def test_unknown_period_is_dropped_but_keeps_the_curve(self) -> None:
        self._write({"id": "a", "periodo": "FERIADO", "nome": "X", "valores": list(_values())})
        curves = load_curves(self.path).curves
        self.assertEqual(len(curves), 1)
        self.assertEqual(curves[0].period, "")


def _category(category_id: str = "2", **changes) -> CurveCategory:
    fields = dict(
        category_id=category_id,
        description="Clientes BT res_0_100 kWh - Tipologia 1",
        consumer_class=0,
        minimum=-1.0,
        maximum=100.0,
        market_share=25.527344128043676,
        power_factor=0.92,
    )
    fields.update(changes)
    return CurveCategory(**fields)


class CurveCategoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "categorias_curvas.json"

    def test_the_file_lives_beside_the_curves(self) -> None:
        self.assertEqual(default_categories_path().parent, default_curves_path().parent)
        curves = Path(self._directory.name) / "curvas.json"
        self.assertEqual(categories_path_for(curves), self.path)
        self.assertEqual(categories_path_for(None), default_categories_path())

    def test_round_trip_preserves_every_field(self) -> None:
        original = (
            _category(),
            _category("387", description="Geração Solar", kind="F", power_factor=1.0),
            _category("1", description="Clientes BT IP", consumer_class=5, pu_max=1,
                      power_factor=None),
        )
        save_curve_categories(original, self.path)
        result = load_curve_categories(self.path)
        self.assertIsNone(result.issue)
        self.assertEqual(
            result.categories,
            tuple(sorted(original, key=lambda item: int(item.category_id))),
        )

    def test_missing_file_is_not_a_problem(self) -> None:
        result = load_curve_categories(self.path)
        self.assertEqual(result.categories, ())
        self.assertIsNone(result.issue)

    def test_invalid_and_repeated_entries_are_reported(self) -> None:
        valid = {
            "cat_id": 2, "descricao": "Residencial", "classe": "0", "min": -1,
            "max": "100", "part_merc": "25,5", "tipo": "c", "pu_max": 0, "tipodem": 0,
        }
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "categorias": [valid, dict(valid), dict(valid, cat_id=3, classe=0.5)],
                }
            ),
            encoding="utf-8",
        )
        result = load_curve_categories(self.path)
        (category,) = result.categories
        self.assertEqual(category.category_id, "2")
        self.assertEqual(category.kind, "C")
        self.assertEqual(category.market_share, 25.5)
        self.assertIn("ignoradas", result.issue)
        self.assertIn("repetidas", result.issue)

    def test_corrupted_file_does_not_raise(self) -> None:
        self.path.write_text("{", encoding="utf-8")
        result = load_curve_categories(self.path)
        self.assertEqual(result.categories, ())
        self.assertIn("JSON", result.issue)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
