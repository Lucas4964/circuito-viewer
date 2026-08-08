from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from circuit_viewer.mdb_mapping import (
    ENTITY_ORDER,
    GENERATOR_CONSUMER_ENTITY,
    MAPPING_ORDER,
    MANDATORY_ENTITIES,
    REQUIRED_COLUMNS,
    EntityMapping,
    MdbMappingError,
    default_mapping_path,
    load_table_mapping,
    resolve_mapping,
)


class FakeDatabase:
    """Banco falso: só um dicionário de tabela → colunas."""

    def __init__(self, tables: dict[str, list[str]], *, unreadable=()) -> None:
        self._tables = tables
        self._unreadable = set(unreadable)

    def tables(self) -> tuple[str, ...]:
        return tuple(sorted(self._tables))

    def columns(self, table: str) -> tuple[str, ...]:
        if table in self._unreadable:
            raise RuntimeError("catálogo indisponível")
        return tuple(self._tables[table])

    def row_count(self, table: str) -> int:
        return 0

    def iter_rows(self, table, columns, *, batch_size=500):  # noqa: ANN001
        return iter(())


def full_database(**extra: list[str]) -> FakeDatabase:
    """Banco com as fontes das dez entidades lógicas, mais o que for pedido."""

    tables = {
        "BARRA": [*REQUIRED_COLUMNS["barras"], "BLOCO_ID", "PL_ANO"],
        "CABOS": [*REQUIRED_COLUMNS["cabos"], "K", "COLOR"],
        "TRECHO": [*REQUIRED_COLUMNS["trechos"], "POSBAR1", "INFO"],
        "CARGA": [*REQUIRED_COLUMNS["cargas"], "MC1_TIPO", "FATDEM"],
        "MT_GERADOR_CONS": [*REQUIRED_COLUMNS["geradores"], "OBS"],
        "MT_CONS": [*REQUIRED_COLUMNS[GENERATOR_CONSUMER_ENTITY], "TIPO"],
        "MODELO_CARGA": ["CENARIO_ID", *REQUIRED_COLUMNS["patamares"]],
        "CHAVE": [*REQUIRED_COLUMNS["chaves"], "BLOCO1_ID"],
        "REGULADOR": [*REQUIRED_COLUMNS["reguladores"], "FIXO", "VREG_P1"],
        "CIRCUITO": [*REQUIRED_COLUMNS["circuitos"], "SE_ID", "NOME"],
        "CIRCUITO_PATAMARES": [
            *REQUIRED_COLUMNS["patamares_circuitos"], "PONTA", "HORARIO_OPC"
        ],
        "MSysObjects": ["Id", "Name"],
    }
    tables.update(extra)
    return FakeDatabase(tables)


class LoadTableMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def write(self, payload) -> Path:  # noqa: ANN001
        path = self.root / "mdb_tabelas.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_the_shipped_mapping_is_valid_and_complete(self) -> None:
        entries = load_table_mapping()
        self.assertEqual(
            {entry.entity for entry in entries}, set(MAPPING_ORDER)
        )

    def test_the_shipped_mapping_matches_the_real_base(self) -> None:
        # Nomes conferidos contra a exportação de Downloads\CSV\UTIL.
        by_entity = {entry.entity: entry for entry in load_table_mapping()}
        self.assertIn("BARRA", by_entity["barras"].tables)
        self.assertIn("CABOS", by_entity["cabos"].tables)
        self.assertIn("MODELO_CARGA", by_entity["patamares"].tables)
        self.assertIn("MT_GERADOR_CONS", by_entity["geradores"].tables)
        self.assertIn("MT_CONS", by_entity[GENERATOR_CONSUMER_ENTITY].tables)
        self.assertIn(
            "CIRCUITO_PATAMARES", by_entity["patamares_circuitos"].tables
        )

    def test_empty_alias_list_means_the_csv_name(self) -> None:
        path = self.write(
            [{"entidade": "barras", "tabelas": ["BARRA"], "colunas": {"X": []}}]
        )
        entry = load_table_mapping(path)[0]
        self.assertEqual(entry.candidates_for("X"), ("X",))

    def test_aliases_come_after_the_canonical_name(self) -> None:
        path = self.write(
            [
                {
                    "entidade": "barras",
                    "tabelas": ["BARRA"],
                    "colunas": {"X": ["COORD_X", "LESTE"]},
                }
            ]
        )
        entry = load_table_mapping(path)[0]
        self.assertEqual(entry.candidates_for("X"), ("X", "COORD_X", "LESTE"))

    def test_unknown_entity_is_refused(self) -> None:
        path = self.write([{"entidade": "postes", "tabelas": ["POSTE"]}])
        with self.assertRaises(MdbMappingError) as caught:
            load_table_mapping(path)
        self.assertIn("postes", str(caught.exception))

    def test_duplicated_entity_is_refused(self) -> None:
        path = self.write(
            [
                {"entidade": "barras", "tabelas": ["BARRA"]},
                {"entidade": "barras", "tabelas": ["BARRA2"]},
            ]
        )
        with self.assertRaises(MdbMappingError):
            load_table_mapping(path)

    def test_column_outside_the_entity_is_refused(self) -> None:
        path = self.write(
            [
                {
                    "entidade": "barras",
                    "tabelas": ["BARRA"],
                    "colunas": {"COMPR": []},
                }
            ]
        )
        with self.assertRaises(MdbMappingError) as caught:
            load_table_mapping(path)
        self.assertIn("COMPR", str(caught.exception))

    def test_empty_table_list_is_refused(self) -> None:
        path = self.write([{"entidade": "barras", "tabelas": []}])
        with self.assertRaises(MdbMappingError):
            load_table_mapping(path)

    def test_invalid_json_names_the_position(self) -> None:
        path = self.root / "mdb_tabelas.json"
        path.write_text("[{", encoding="utf-8")
        with self.assertRaises(MdbMappingError) as caught:
            load_table_mapping(path)
        self.assertIn("linha", str(caught.exception))

    def test_missing_file_is_refused(self) -> None:
        with self.assertRaises(MdbMappingError):
            load_table_mapping(self.root / "inexistente.json")

    def test_the_shipped_path_exists(self) -> None:
        self.assertTrue(default_mapping_path().is_file())


class ResolveMappingTests(unittest.TestCase):
    def test_resolves_every_entity_of_the_real_base(self) -> None:
        result = resolve_mapping(full_database())
        self.assertEqual(
            {item.entity for item in result.resolved}, set(MAPPING_ORDER)
        )
        self.assertEqual(result.unavailable, ())
        self.assertTrue(result.has_mandatory)

    def test_keeps_the_import_order(self) -> None:
        result = resolve_mapping(full_database())
        self.assertEqual(
            tuple(item.entity for item in result.resolved), MAPPING_ORDER
        )

    def test_selects_only_the_required_columns(self) -> None:
        # A tabela CARGA tem 43 colunas na base real; 9 interessam.
        entity = resolve_mapping(full_database()).get("cargas")
        self.assertIsNotNone(entity)
        self.assertEqual(entity.columns, REQUIRED_COLUMNS["cargas"])
        self.assertNotIn("FATDEM", entity.columns)

    def test_ignores_the_extra_scenario_column_of_the_patterns_table(self) -> None:
        entity = resolve_mapping(full_database()).get("patamares")
        self.assertNotIn("CENARIO_ID", entity.columns)
        self.assertEqual(entity.header, REQUIRED_COLUMNS["patamares"])

    def test_table_names_match_without_case(self) -> None:
        tables = {
            "Barra": list(REQUIRED_COLUMNS["barras"]),
        }
        result = resolve_mapping(FakeDatabase(tables))
        entity = result.get("barras")
        # O Access não diferencia caixa; a resolução acompanha.
        self.assertEqual(entity.table, "Barra")

    def test_column_names_match_without_case(self) -> None:
        tables = {"BARRA": ["barra_id", "Codigo", "x", "Y"]}
        entity = resolve_mapping(FakeDatabase(tables)).get("barras")
        self.assertEqual(entity.columns, ("barra_id", "Codigo", "x", "Y"))
        # O cabeçalho entregue ao importador continua canônico.
        self.assertEqual(entity.header, REQUIRED_COLUMNS["barras"])

    def test_falls_back_to_the_second_candidate_table(self) -> None:
        tables = {
            "BARRA": list(REQUIRED_COLUMNS["barras"]),
            "CABO": list(REQUIRED_COLUMNS["cabos"]),
        }
        entity = resolve_mapping(FakeDatabase(tables)).get("cabos")
        self.assertEqual(entity.table, "CABO")

    def test_aliases_resolve_a_renamed_column(self) -> None:
        tables = {"BARRA": ["BARRA_ID", "CODIGO", "COORD_X", "COORD_Y"]}
        mapping = (
            EntityMapping(
                "barras",
                ("BARRA",),
                {"X": ("COORD_X",), "Y": ("COORD_Y",)},
            ),
        )
        entity = resolve_mapping(FakeDatabase(tables), mapping).get("barras")
        self.assertEqual(entity.columns, ("BARRA_ID", "CODIGO", "COORD_X", "COORD_Y"))

    def test_a_missing_table_only_disables_its_own_entity(self) -> None:
        tables = {
            "BARRA": list(REQUIRED_COLUMNS["barras"]),
            "TRECHO": list(REQUIRED_COLUMNS["trechos"]),
        }
        result = resolve_mapping(FakeDatabase(tables))
        self.assertIsNotNone(result.get("barras"))
        self.assertIsNotNone(result.get("trechos"))
        self.assertIn("CHAVE", result.reason_for("chaves"))
        # Barras existem, então o essencial está atendido.
        self.assertTrue(result.has_mandatory)

    def test_a_missing_column_disables_the_entity_with_its_name(self) -> None:
        tables = {"BARRA": ["BARRA_ID", "CODIGO", "X"]}
        result = resolve_mapping(FakeDatabase(tables))
        self.assertIsNone(result.get("barras"))
        self.assertIn("Y", result.reason_for("barras"))
        self.assertFalse(result.has_mandatory)

    def test_an_unreadable_table_is_reported_not_raised(self) -> None:
        database = full_database()
        database._unreadable.add("CHAVE")
        result = resolve_mapping(database)
        self.assertIsNone(result.get("chaves"))
        self.assertIn("colunas", result.reason_for("chaves"))
        # As demais entidades continuam resolvidas.
        self.assertIsNotNone(result.get("barras"))

    def test_system_tables_are_never_chosen(self) -> None:
        result = resolve_mapping(full_database())
        for item in result.resolved:
            with self.subTest(entity=item.entity):
                self.assertFalse(item.table.upper().startswith("MSYS"))

    def test_override_forces_the_table(self) -> None:
        tables = {
            "BARRA": list(REQUIRED_COLUMNS["barras"]),
            "SE_BARRA": list(REQUIRED_COLUMNS["barras"]),
        }
        result = resolve_mapping(
            FakeDatabase(tables), overrides={"barras": "SE_BARRA"}
        )
        self.assertEqual(result.get("barras").table, "SE_BARRA")

    def test_an_impossible_override_is_reported_instead_of_ignored(self) -> None:
        result = resolve_mapping(
            full_database(), overrides={"barras": "NAO_EXISTE"}
        )
        self.assertIsNone(result.get("barras"))
        # Cair de volta na detecção passaria por cima da escolha do usuário.
        self.assertIn("NAO_EXISTE", result.reason_for("barras"))

    def test_an_override_missing_a_column_does_not_fall_back(self) -> None:
        tables = {
            "BARRA": list(REQUIRED_COLUMNS["barras"]),
            "SE_BARRA": ["BARRA_ID", "CODIGO"],
        }
        result = resolve_mapping(
            FakeDatabase(tables), overrides={"barras": "SE_BARRA"}
        )
        self.assertIsNone(result.get("barras"))
        self.assertIn("SE_BARRA", result.reason_for("barras"))

    def test_bars_are_the_mandatory_entity(self) -> None:
        self.assertEqual(MANDATORY_ENTITIES, frozenset({"barras"}))


if __name__ == "__main__":
    unittest.main()
