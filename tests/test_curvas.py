from __future__ import annotations

import unittest

from circuit_viewer.curvas import (
    HOURLY_CURVE_POINT_COUNT,
    MAX_CURVE_NAME_LENGTH,
    Curve,
    CurveCatalog,
    CurveDraft,
    clipboard_column,
    new_curve_id,
    parse_clipboard_values,
    split_clipboard_block,
    validate_catalog,
    validate_curve_name,
    validate_draft,
)


def _values(start: float = 0.0) -> tuple[float, ...]:
    return tuple(start + hour for hour in range(HOURLY_CURVE_POINT_COUNT))


class CurveInvariantTests(unittest.TestCase):
    """A curva gravada nunca existe incompleta ou com valor não finito."""

    def test_accepts_twenty_four_finite_values(self) -> None:
        curve = Curve("abc", "Residencial", _values())
        self.assertEqual(len(curve.values), 24)

    def test_rejects_wrong_point_count(self) -> None:
        for count in (0, 23, 25):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    Curve("abc", "X", tuple(float(i) for i in range(count)))

    def test_rejects_non_finite_values(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                values = list(_values())
                values[5] = bad
                with self.assertRaises(ValueError):
                    Curve("abc", "X", tuple(values))

    def test_accepts_negative_and_zero(self) -> None:
        """Negativos são legítimos: uma curva de geração injeta potência."""

        values = tuple(float(v) for v in range(-12, 12))
        curve = Curve("abc", "Geração", values)
        self.assertIn(0.0, curve.values)
        self.assertIn(-12.0, curve.values)

    def test_rejects_empty_id_and_name(self) -> None:
        with self.assertRaises(ValueError):
            Curve("", "X", _values())
        with self.assertRaises(ValueError):
            Curve("abc", "   ", _values())


class CurveDraftTests(unittest.TestCase):
    def test_new_draft_starts_empty(self) -> None:
        draft = CurveDraft.new("Nova")
        self.assertEqual(len(draft.values), 24)
        self.assertTrue(all(value is None for value in draft.values))
        self.assertEqual(draft.missing_hours(), tuple(range(1, 25)))

    def test_missing_hours_are_one_based(self) -> None:
        draft = CurveDraft.new("X")
        for hour_index in range(24):
            draft.set_value(hour_index, 1.0)
        draft.set_value(0, None)
        draft.set_value(23, None)
        self.assertEqual(draft.missing_hours(), (1, 24))

    def test_set_value_reports_no_op(self) -> None:
        draft = CurveDraft.new("X")
        self.assertFalse(draft.set_value(0, None))
        self.assertTrue(draft.set_value(0, 1.5))
        self.assertFalse(draft.set_value(0, 1.5))
        self.assertTrue(draft.set_value(0, None))

    def test_set_value_rejects_non_finite(self) -> None:
        draft = CurveDraft.new("X")
        with self.assertRaises(ValueError):
            draft.set_value(0, float("nan"))

    def test_set_value_rejects_out_of_range_hour(self) -> None:
        draft = CurveDraft.new("X")
        with self.assertRaises(IndexError):
            draft.set_value(24, 1.0)

    def test_to_curve_requires_every_hour(self) -> None:
        draft = CurveDraft.new("X")
        with self.assertRaises(ValueError):
            draft.to_curve()

    def test_curve_id_survives_rename(self) -> None:
        """Renomear não pode quebrar um vínculo futuro carga→curva."""

        curve = Curve(new_curve_id(), "Antigo", _values())
        draft = CurveDraft.from_curve(curve)
        catalog = CurveCatalog([draft])
        catalog.rename(0, "Novo")
        self.assertEqual(draft.name, "Novo")
        self.assertEqual(draft.curve_id, curve.curve_id)
        self.assertEqual(draft.to_curve().curve_id, curve.curve_id)


class CurveCatalogTests(unittest.TestCase):
    def _catalog(self, *names: str) -> CurveCatalog:
        return CurveCatalog(CurveDraft.new(name) for name in names)

    def test_index_for_id(self) -> None:
        catalog = self._catalog("A", "B")
        target = catalog.draft(1)
        self.assertEqual(catalog.index_for_id(target.curve_id), 1)
        self.assertIsNone(catalog.index_for_id("inexistente"))

    def test_name_available_is_case_insensitive(self) -> None:
        catalog = self._catalog("Residencial")
        self.assertFalse(catalog.name_available("residencial"))
        self.assertFalse(catalog.name_available("  RESIDENCIAL  "))
        self.assertTrue(catalog.name_available("Comercial"))

    def test_name_available_ignores_itself(self) -> None:
        catalog = self._catalog("Residencial")
        own = catalog.draft(0).curve_id
        self.assertTrue(catalog.name_available("Residencial", ignoring=own))

    def test_rename_reports_no_op(self) -> None:
        catalog = self._catalog("A")
        self.assertFalse(catalog.rename(0, "A"))
        self.assertTrue(catalog.rename(0, "B"))

    def test_remove(self) -> None:
        catalog = self._catalog("A", "B")
        catalog.remove(0)
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog.draft(0).name, "B")


class ValidationTests(unittest.TestCase):
    def test_empty_name(self) -> None:
        catalog = CurveCatalog()
        self.assertIsNotNone(validate_curve_name("", catalog))
        self.assertIsNotNone(validate_curve_name("   ", catalog))

    def test_name_too_long(self) -> None:
        catalog = CurveCatalog()
        long_name = "a" * (MAX_CURVE_NAME_LENGTH + 1)
        problem = validate_curve_name(long_name, catalog)
        self.assertIsNotNone(problem)
        self.assertIn(str(MAX_CURVE_NAME_LENGTH), problem)

    def test_duplicate_name(self) -> None:
        """A colisão ignora caixa; a mensagem devolve o que o usuário digitou."""

        catalog = CurveCatalog([CurveDraft.new("Residencial")])
        problem = validate_curve_name("residencial", catalog)
        self.assertIsNotNone(problem)
        self.assertIn("residencial", problem)

    def test_valid_name(self) -> None:
        self.assertIsNone(
            validate_curve_name("Residencial típica", CurveCatalog())
        )

    def test_validate_draft_lists_missing_hours(self) -> None:
        draft = CurveDraft.new("X")
        for hour_index in range(24):
            draft.set_value(hour_index, 1.0)
        draft.set_value(2, None)
        draft.set_value(6, None)
        draft.set_value(18, None)
        catalog = CurveCatalog([draft])
        problem = validate_draft(draft, catalog)
        self.assertEqual(problem, "Faltam valores nas horas 3, 7 e 19.")

    def test_validate_catalog_empty_when_all_valid(self) -> None:
        draft = CurveDraft.new("Boa")
        for hour_index in range(24):
            draft.set_value(hour_index, 1.0)
        self.assertEqual(validate_catalog(CurveCatalog([draft])), ())

    def test_validate_catalog_names_the_offending_curve(self) -> None:
        catalog = CurveCatalog([CurveDraft.new("Incompleta")])
        problems = validate_catalog(catalog)
        self.assertEqual(len(problems), 1)
        self.assertIn("Incompleta", problems[0])


class ClipboardSplitTests(unittest.TestCase):
    def test_plain_lines(self) -> None:
        block = split_clipboard_block("1\n2\n3")
        self.assertEqual(block, [["1"], ["2"], ["3"]])

    def test_windows_line_endings(self) -> None:
        block = split_clipboard_block("1\r\n2\r\n3")
        self.assertEqual(block, [["1"], ["2"], ["3"]])

    def test_lone_carriage_returns(self) -> None:
        block = split_clipboard_block("1\r2\r3")
        self.assertEqual(block, [["1"], ["2"], ["3"]])

    def test_trailing_blank_line_is_dropped(self) -> None:
        """O Excel sempre acrescenta uma linha vazia ao copiar um intervalo."""

        self.assertEqual(split_clipboard_block("1\n2\n"), [["1"], ["2"]])
        self.assertEqual(split_clipboard_block("1\n2\n\n\n"), [["1"], ["2"]])

    def test_blank_line_in_the_middle_is_kept(self) -> None:
        """Descartá-la deslocaria em uma hora todos os valores seguintes."""

        block = split_clipboard_block("1\n\n3")
        self.assertEqual(block, [["1"], [""], ["3"]])

    def test_tab_separated_columns(self) -> None:
        block = split_clipboard_block("1\t0,5\n2\t0,7")
        self.assertEqual(block, [["1", "0,5"], ["2", "0,7"]])

    def test_empty_text(self) -> None:
        self.assertEqual(split_clipboard_block(""), [])


class ClipboardColumnTests(unittest.TestCase):
    def test_single_column(self) -> None:
        texts, width = clipboard_column([["1"], ["2"]])
        self.assertEqual(texts, ["1", "2"])
        self.assertEqual(width, 1)

    def test_two_columns_takes_the_last(self) -> None:
        """Quem copia um par do Excel copia "Hora, Valor" nessa ordem."""

        texts, width = clipboard_column([["1", "0,5"], ["2", "0,7"]])
        self.assertEqual(texts, ["0,5", "0,7"])
        self.assertEqual(width, 2)

    def test_ragged_rows_yield_empty(self) -> None:
        texts, width = clipboard_column([["1", "0,5"], ["2"]])
        self.assertEqual(width, 2)
        self.assertEqual(texts, ["0,5", ""])

    def test_empty_block(self) -> None:
        texts, width = clipboard_column([])
        self.assertEqual(texts, [])
        self.assertEqual(width, 0)


class ClipboardParseTests(unittest.TestCase):
    def test_accepts_both_decimal_separators(self) -> None:
        parsed = parse_clipboard_values(["12,5", "12.5", " 3 "])
        self.assertEqual(parsed, [(12.5, True), (12.5, True), (3.0, True)])

    def test_rejects_ambiguous_thousands_separator(self) -> None:
        """Regra única do projeto: ponto e vírgula juntos são ambíguos."""

        self.assertEqual(parse_clipboard_values(["1.234,56"]), [(None, False)])

    def test_empty_text_clears_the_cell(self) -> None:
        self.assertEqual(parse_clipboard_values(["", "   "]), [(None, True)] * 2)

    def test_non_numeric_is_reported_not_raised(self) -> None:
        parsed = parse_clipboard_values(["Valor", "1"])
        self.assertEqual(parsed, [(None, False), (1.0, True)])

    def test_negative_and_scientific_notation(self) -> None:
        parsed = parse_clipboard_values(["-2,5", "1e3"])
        self.assertEqual(parsed, [(-2.5, True), (1000.0, True)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
