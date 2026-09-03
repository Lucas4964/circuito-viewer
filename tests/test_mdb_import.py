from __future__ import annotations

import threading
import unittest

from circuit_viewer.csv_import import CsvImportCancelled, CsvImportError
from circuit_viewer.mdb_engine import cell_to_text
from circuit_viewer.mdb_import import (
    ENTITY_DEPENDENCIES,
    FIRST_ROW_NUMBER,
    MDB_ENCODING,
    detect_database_scale,
    load_database,
    source_label,
)
from circuit_viewer.mdb_mapping import ENTITY_ORDER, resolve_mapping
from circuit_viewer.model import UtmCrs
from circuit_viewer.phase_config import load_phase_configuration
from circuit_viewer.switch_types import load_switch_types


CRS = UtmCrs(zone=21, northern=False)


class FakeDatabase:
    """Banco falso: tabela → (colunas, linhas de valores nativos).

    Espelha o motor falso de ``test_opendss_powerflow``: a orquestração recebe o
    banco por parâmetro, então a suíte roda sem pyodbc nem driver ODBC.
    """

    def __init__(self, tables: dict[str, tuple[list[str], list[tuple]]]) -> None:
        self._tables = tables
        self.statements: list[tuple[str, tuple[str, ...]]] = []

    def tables(self) -> tuple[str, ...]:
        return tuple(sorted(self._tables))

    def columns(self, table: str) -> tuple[str, ...]:
        return tuple(self._tables[table][0])

    def row_count(self, table: str) -> int:
        return len(self._tables[table][1])

    def iter_rows(self, table, columns, *, batch_size=500):  # noqa: ANN001
        self.statements.append((table, tuple(columns)))
        names, rows = self._tables[table]
        positions = [names.index(name) for name in columns]
        for row in rows:
            # A conversão é a mesma do motor real: é o que este teste exercita.
            yield tuple(cell_to_text(row[position]) for position in positions)


def network_database(**overrides) -> FakeDatabase:
    """Uma rede mínima com todas as entidades, em tipos nativos do Access.

    Os tipos imitam a base real: identificadores inteiros, FASES2 e ESTADO
    inteiros, COMPR e VNOM em ponto flutuante.
    """

    tables: dict[str, tuple[list[str], list[tuple]]] = {
        "BARRA": (
            ["BARRA_ID", "BLOCO_ID", "CODIGO", "X", "Y", "PL_ANO"],
            [
                (7, 2, "COD-A", 5989944, 82487703, 0),
                (8, 2, "COD-B", 5990044, 82487803, 0),
                (9, 2, "COD-C", 5990144, 82487903, 0),
            ],
        ),
        "CABOS": (
            [
                "CABO_ID", "TIPO", "CODIGO", "IADM", "GMR", "R", "X", "QCAP",
                "R0", "X0", "R1", "X1", "NOME", "EXTERN_ID", "COLOR",
            ],
            [
                (115, 1, "AA47", 365.0, 0.0674, 0.18, 0.0, 0.9764,
                 0.3658, 2.0714, 0.1882, 0.3836, "", "", 10872956),
                # TIPO=2 do mesmo cabo: descartado, como no CSV.
                (115, 2, "AA47", 365.0, 0.0674, 0.18, 0.0, 0.9764,
                 0.3658, 2.0714, 0.1882, 0.3836, "", "", 10872956),
            ],
        ),
        "TRECHO": (
            [
                "TRECHO_ID", "CODIGO", "FASES2", "BLOCO_ID", "BARRA1_ID",
                "BARRA2_ID", "ARRANJO_ID", "CABOF_ID", "CABON_ID", "COMPR",
            ],
            [
                (2, "TR-1", 13, -1, 7, 8, 1, 115, -1, 41.297000885009766),
                (3, "TR-2", 13, -1, 8, 9, 1, 115, -1, 50.0),
            ],
        ),
        "CARGA": (
            [
                "CARGA_ID", "BARRA_ID", "EXTERN_ID", "CODIGO", "SNOM", "SADM",
                "VLINHASEC", "FASES2", "TIPO_LIG", "FATDEM",
            ],
            [(2, 9, 34722450, "CARGA-1", 30.0, 30.0, 220.0, 13, 2, None)],
        ),
        "CAPACITOR": (
            [
                "CAPAC_ID", "BARRA_ID", "EXTERN_ID", "CODIGO", "VNOM",
                "Q1", "Q2", "Q3", "Q4", "FASES", "LIGACAO",
            ],
            # FASES vem em letras no cadastro, e nao no codigo numerico das
            # cargas; o N e o neutro, ignorado pela exportacao.
            [(239, 9, 34559653, "CAP-1", 13.8, 600.0, 600.0, 600.0, 600.0,
              "DEFN", 0)],
        ),
        "MT_CONS": (
            ["ID", "CARGA_ID", "CODIGO", "EXTERN_ID", "NOME", "FASES2"],
            [(101, 2, "GEN-COD", "EXT-GEN", "Usina", 13)],
        ),
        "MT_GERADOR_CONS": (
            [
                "GERADOR_ID", "MT_CONS_ID", "CODIGO", "VNOM", "SNOM",
                "LIGACAO", "CURVA_ID", "GERACAO_KWH",
            ],
            [(201, 101, "GEN-COD", 13.8, 75.0, "Y", "CUR-1", 1000.5)],
        ),
        "MODELO_CARGA": (
            ["CENARIO_ID", "CARGA_ID", "NPAT", "PD", "PE", "PF", "QD", "QE", "QF"],
            [
                (1, 2, 0, 0.9876, 0.0, 0.0, 0.1015, 0.0, 0.0),
                (1, 2, 1, 1.0, 0.0, 0.0, 0.2, 0.0, 0.0),
                (1, 2, 2, 1.5, 0.0, 0.0, 0.3, 0.0, 0.0),
                (1, 2, 3, 2.0, 0.0, 0.0, 0.4, 0.0, 0.0),
            ],
        ),
        "CHAVE": (
            [
                "CHAVE_ID", "TIPOCHV_ID", "CIRC_ID", "TRECHO_ID", "CODIGO",
                "ESTADO", "ESTADO_NORMAL", "CORN", "ELO", "ELO_TIPO",
            ],
            [(4, 2, 2, 3, "CHV-1", 1, 1, 400.0, 0.0, None)],
        ),
        "REGULADOR": (
            [
                "REGU_ID", "TRECHO_ID", "EXTERN_ID", "CODIGO", "LIGACAO",
                "SNOM", "FAIXA", "NPASSOS", "TAP", "INOM", "VNOM",
            ],
            [(2, 2, 34691885, "REG-1", 0, 333.0, 0.1, 32, 0.025, 200.0, 13.8)],
        ),
        "CIRCUITO": (
            ["CIRC_ID", "SE_ID", "BARRA_ID", "CODIGO", "VNOM", "NOME"],
            [(2, 2, 7, "004001", 13.800000190734863, "")],
        ),
        "CIRCUITO_PATAMARES": (
            [
                "CIRC_ID", "NPAT", "NOME", "HORARIO_INI", "HORARIO_FIM",
                "HORARIO_REF", "PONTA", "HORARIO_OPC",
            ],
            [
                (2, 0, "Madrugada", 22, 5, 23, 0, 0),
                (2, 1, "Manhã", 5, 11, 11, 0, 0),
                (2, 2, "Tarde", 11, 18, 12, 0, 0),
                (2, 3, "Noite", 18, 22, 22, 1, 0),
            ],
        ),
        "MSysObjects": (["Id", "Name"], []),
    }
    tables.update(overrides)
    return FakeDatabase(tables)


def run(database: FakeDatabase, **kwargs):  # noqa: ANN001, ANN201
    kwargs.setdefault("source_path", r"C:\dados\rede.mdb")
    kwargs.setdefault("scale", 10.0)
    return load_database(database, CRS, **kwargs)


class LoadDatabaseTests(unittest.TestCase):
    def test_imports_optional_transformer_allocation_tables(self) -> None:
        database = network_database(
            BT_ET=(["ID", "MT_CAR_ID"], [(501, 2)]),
            BT_CONS=(
                ["ID", "CODIGO", "ET_ID", "FASES2", "CONSUMO"],
                [(901, "UC-901", 501, 7, 600.0)],
            ),
            BT_GERADOR_CONS=(
                ["ET_ID", "GERACAO_KWH"],
                [(501, 720.0)],
            ),
            MT_CONS=(
                ["ID", "CARGA_ID", "CODIGO", "EXTERN_ID", "NOME", "FASES2", "CONSUMO"],
                [(101, 2, "GEN-COD", "EXT-GEN", "Usina", 13, 300.0)],
            ),
        )

        result = run(
            database,
            phase_configuration=load_phase_configuration(),
        )

        self.assertIsNotNone(result.allocations)
        self.assertIsNone(result.allocation_error)
        record = result.allocations.record(0)
        self.assertEqual(record.total_energy.d, 400.0)
        self.assertEqual(record.total_energy.e, 400.0)
        self.assertEqual(record.total_energy.f, 100.0)
        self.assertEqual(record.generation_bt_kwh, 720.0)
        self.assertEqual(record.generation_mt_kwh, 1000.5)
        by_table = dict(database.statements)
        self.assertEqual(
            by_table["BT_CONS"],
            ("ET_ID", "FASES2", "CONSUMO", "ID", "CODIGO"),
        )

    SUBSTATION_TABLES = {
        "CIRCUITO": (
            ["CIRC_ID", "SE_ID", "TRAFO_ID", "BARRA_ID", "CODIGO", "VNOM", "NOME"],
            [(2, 2, 3, 7, "004001", 13.800000190734863, "")],
        ),
        "SE": (
            ["SE_ID", "CODIGO", "NOME", "VALTA"],
            [(2, "032", "SE AGUA BOA", 69)],
        ),
        "SE_TRAFO": (
            ["TRAFO_ID", "SE_ID", "CODIGO", "SNOM", "VMEDIA"],
            [
                (2, 2, "TRAFO_13KV", 50.0, 13.8),
                (3, 2, "53244TRAFO_032", 25.0, 34.5),
            ],
        ),
    }

    def test_the_circuit_carries_its_substation_and_transformer(self) -> None:
        # SE_ID e TRAFO_ID sao chaves de CIRCUITO; sem resolve-las a janela nao
        # sabe dizer de onde o alimentador sai.
        result = run(network_database(**self.SUBSTATION_TABLES))

        definition = result.circuits.model.definition(0)
        self.assertEqual(definition.substation_code, "032")
        self.assertEqual(definition.substation_name, "SE AGUA BOA")
        self.assertEqual(definition.transformer_id, "3")
        self.assertEqual(definition.transformer_code, "53244TRAFO_032")
        self.assertEqual(definition.transformer_power, "25")
        self.assertEqual(result.circuits.issues, ())
        self.assertEqual(result.circuits.invalid_rows, 0)

    def test_a_key_that_does_not_resolve_empties_without_rejecting(self) -> None:
        # Uma coluna informativa nao pode custar o alimentador: o circuito entra
        # no catalogo, as celulas ficam vazias e o relatorio diz o motivo.
        tables = dict(self.SUBSTATION_TABLES)
        tables["CIRCUITO"] = (
            tables["CIRCUITO"][0],
            [(2, 99, 98, 7, "004001", 13.800000190734863, "")],
        )
        result = run(network_database(**tables))

        definition = result.circuits.model.definition(0)
        self.assertEqual(definition.substation_code, "")
        self.assertEqual(definition.substation_name, "")
        self.assertEqual(definition.transformer_code, "")
        self.assertEqual(definition.transformer_power, "")
        # O id cru sobrevive: e ele que permite achar o erro no cadastro.
        self.assertEqual(definition.transformer_id, "98")
        # Relatado, mas nao contado como linha invalida.
        self.assertEqual(len(result.circuits.issues), 1)
        reason = result.circuits.issues[0].reason
        self.assertIn("SE_ID '99'", reason)
        self.assertIn("TRAFO_ID '98'", reason)
        self.assertEqual(result.circuits.invalid_rows, 0)
        self.assertEqual(len(result.circuits.model), 1)

    def test_an_empty_key_is_reported_too(self) -> None:
        tables = dict(self.SUBSTATION_TABLES)
        tables["CIRCUITO"] = (
            tables["CIRCUITO"][0],
            [(2, None, 3, 7, "004001", 13.800000190734863, "")],
        )
        result = run(network_database(**tables))

        definition = result.circuits.model.definition(0)
        self.assertEqual(definition.substation_code, "")
        self.assertEqual(definition.transformer_code, "53244TRAFO_032")
        self.assertIn("sem SE_ID", result.circuits.issues[0].reason)

    def test_a_database_without_the_substation_tables_still_imports(self) -> None:
        # O banco padrao do fixture nao tem SE nem SE_TRAFO nem TRAFO_ID: a
        # importacao segue e as colunas de origem ficam em branco.
        result = run(network_database())

        definition = result.circuits.model.definition(0)
        self.assertEqual(definition.substation_code, "")
        self.assertEqual(definition.transformer_id, "")
        self.assertEqual(result.circuits.issues, ())
        self.assertEqual(result.failures, ())

    def test_the_circuit_table_comes_from_the_mapping_not_a_literal(self) -> None:
        # Quem decide de onde os circuitos vem e o mdb_tabelas.json. Com a
        # entidade forcada para outra tabela, a leitura auxiliar tem de seguir
        # junto — fixar "CIRCUITO" no codigo passaria neste cenario lendo a
        # tabela errada.
        tables = dict(self.SUBSTATION_TABLES)
        columns, rows = tables.pop("CIRCUITO")
        database = network_database(OUTRO_CIRCUITO=(columns, rows), **tables)

        result = run(database, overrides={"circuitos": "OUTRO_CIRCUITO"})

        self.assertEqual(
            result.circuits.model.definition(0).transformer_code,
            "53244TRAFO_032",
        )
        self.assertIn(
            ("OUTRO_CIRCUITO", ("CIRC_ID", "SE_ID", "TRAFO_ID")),
            database.statements,
        )

    SWITCH_TYPE_TABLES = {
        "TIPOCHAVE": (
            ["ID", "TIPO", "CODIGO", "ELO", "OPERACAO"],
            [
                (2, "Disjuntor", "DJ", 0, 0),
                (4, "Chave Faca", "CF", 0, 0),
                (5, "Chave Fusível", "CHFUSIVEL", 1, 0),
                (99, "Tipo Novo", "INEDITO", 0, 0),
            ],
        ),
    }

    def _switch_database(self, type_id: int):  # noqa: ANN202
        tables = dict(self.SWITCH_TYPE_TABLES)
        tables["CHAVE"] = (
            [
                "CHAVE_ID", "TIPOCHV_ID", "CIRC_ID", "TRECHO_ID", "CODIGO",
                "ESTADO", "ESTADO_NORMAL", "CORN", "ELO", "ELO_TIPO",
            ],
            [(4, type_id, 2, 3, "CHV-1", 1, 1, 400.0, 0.0, None)],
        )
        return network_database(**tables)

    def test_the_switch_carries_its_type_and_whether_it_can_be_operated(self) -> None:
        result = run(
            self._switch_database(4), switch_types=load_switch_types()
        )

        record = result.switches.model.record(0)
        self.assertEqual(record.type_name, "Chave Faca")
        self.assertEqual(record.switchable, "1")

    def test_a_fuse_is_not_switchable(self) -> None:
        result = run(
            self._switch_database(5), switch_types=load_switch_types()
        )

        record = result.switches.model.record(0)
        self.assertEqual(record.type_name, "Chave Fusível")
        self.assertEqual(record.switchable, "0")

    def test_a_type_absent_from_the_file_keeps_its_name(self) -> None:
        # O banco sabe o que a chave é; só a política está faltando. Afirmar 0
        # ali seria inventar uma resposta que ninguém declarou.
        result = run(
            self._switch_database(99), switch_types=load_switch_types()
        )

        record = result.switches.model.record(0)
        self.assertEqual(record.type_name, "Tipo Novo")
        self.assertEqual(record.switchable, "")

    def test_without_the_configuration_only_the_name_survives(self) -> None:
        result = run(self._switch_database(4))

        record = result.switches.model.record(0)
        self.assertEqual(record.type_name, "Chave Faca")
        self.assertEqual(record.switchable, "")

    def test_a_database_without_tipochave_still_imports(self) -> None:
        result = run(network_database(), switch_types=load_switch_types())

        record = result.switches.model.record(0)
        self.assertEqual(record.type_name, "")
        self.assertEqual(record.switchable, "")
        self.assertEqual(result.failures, ())

    def test_imports_every_entity(self) -> None:
        result = run(network_database())
        self.assertEqual(result.imported_entities, ENTITY_ORDER)
        self.assertEqual(result.failures, ())

    def test_chains_the_models_by_identity(self) -> None:
        # É a regra de consistência do projeto: os modelos precisam ser os
        # mesmos objetos, não cópias equivalentes.
        result = run(network_database())
        self.assertIs(result.segments.model.bars, result.bars.model)
        self.assertIs(result.loads.model.bars, result.bars.model)
        self.assertIs(result.patterns.model.loads, result.loads.model)
        self.assertIs(result.generators.model.loads, result.loads.model)
        self.assertIs(result.switches.model.segments, result.segments.model)
        self.assertIs(result.regulators.model.segments, result.segments.model)
        self.assertIs(result.circuits.model.segments, result.segments.model)
        self.assertIs(result.circuits.model.switches, result.switches.model)
        self.assertIs(result.circuit_levels.model.circuits, result.circuits.model)

    def test_entities_are_imported_in_dependency_order(self) -> None:
        database = network_database()
        run(database)
        visited = [table for table, _columns in database.statements]
        self.assertLess(visited.index("BARRA"), visited.index("TRECHO"))
        self.assertLess(visited.index("TRECHO"), visited.index("CHAVE"))
        # As chaves precisam existir antes dos circuitos: a topologia
        # energizada depende delas.
        self.assertLess(visited.index("CHAVE"), visited.index("CIRCUITO"))
        self.assertLess(
            visited.index("CIRCUITO"), visited.index("CIRCUITO_PATAMARES")
        )
        self.assertLess(visited.index("CARGA"), visited.index("MODELO_CARGA"))
        self.assertLess(visited.index("CARGA"), visited.index("MT_CONS"))
        self.assertLess(visited.index("MT_CONS"), visited.index("MT_GERADOR_CONS"))

    def test_only_the_required_columns_are_selected(self) -> None:
        database = network_database()
        run(database)
        by_table = dict(database.statements)
        self.assertNotIn("FATDEM", by_table["CARGA"])
        self.assertNotIn("CENARIO_ID", by_table["MODELO_CARGA"])
        self.assertNotIn("PONTA", by_table["CIRCUITO_PATAMARES"])
        self.assertNotIn("HORARIO_OPC", by_table["CIRCUITO_PATAMARES"])
        self.assertNotIn("BLOCO_ID", by_table["BARRA"])
        self.assertEqual(
            by_table["MT_GERADOR_CONS"],
            (
                "GERADOR_ID", "MT_CONS_ID", "CODIGO", "VNOM", "SNOM",
                "LIGACAO", "CURVA_ID", "GERACAO_KWH",
            ),
        )

    def test_source_path_records_the_table(self) -> None:
        result = run(network_database())
        self.assertEqual(
            result.bars.model.source_path, r"C:\dados\rede.mdb::BARRA"
        )
        self.assertEqual(
            result.segments.model.source_path, r"C:\dados\rede.mdb::TRECHO"
        )
        self.assertEqual(
            result.generators.model.source_paths,
            (
                r"C:\dados\rede.mdb::MT_GERADOR_CONS",
                r"C:\dados\rede.mdb::MT_CONS",
            ),
        )

    def test_generator_association_uses_code_and_load_to_find_the_bar(self) -> None:
        result = run(network_database())
        record = result.generators.model.record(0)
        self.assertEqual(record.generator_id, "201")
        self.assertEqual(record.mt_cons_id, "101")
        self.assertEqual(record.consumer_id, "101")
        self.assertEqual(record.load_id, "2")
        self.assertEqual(record.bar_id, "9")
        self.assertEqual(result.generators.model.load_indices.tolist(), [0])
        self.assertEqual(result.generators.model.bar_indices.tolist(), [2])

    def test_encoding_is_not_reported_as_a_legacy_codepage(self) -> None:
        result = run(network_database())
        self.assertEqual(result.bars.encoding, MDB_ENCODING)
        # "cp1252" dispararia has_warnings sem haver aviso nenhum.
        self.assertFalse(result.bars.has_warnings)

    def test_rows_are_numbered_from_one(self) -> None:
        # Não há cabeçalho ocupando a linha 1 como no CSV.
        self.assertEqual(FIRST_ROW_NUMBER, 1)
        broken = network_database(
            BARRA=(
                ["BARRA_ID", "CODIGO", "X", "Y"],
                [(None, "COD-A", 5989944, 82487703), (8, "COD-B", 5990044, 82487803)],
            )
        )
        result = run(broken)
        self.assertEqual(result.bars.issues[0].line_number, 1)


class TypeConversionRegressionTests(unittest.TestCase):
    """Os casos em que um ".0" a mais quebraria a aplicação em silêncio."""

    def test_integer_estado_keeps_the_switch_closed(self) -> None:
        result = run(network_database())
        record = result.switches.model.record(0)
        # trace() e o exportador testam ESTADO == "1"; "1.0" abriria a chave.
        self.assertEqual(record.state, "1")

    def test_float_estado_also_keeps_the_switch_closed(self) -> None:
        database = network_database(
            CHAVE=(
                [
                    "CHAVE_ID", "TIPOCHV_ID", "CIRC_ID", "TRECHO_ID", "CODIGO",
                    "ESTADO", "ESTADO_NORMAL", "CORN", "ELO", "ELO_TIPO",
                ],
                [(4, 2, 2, 3, "CHV-1", 1.0, 1.0, 400.0, 0.0, None)],
            )
        )
        result = run(database)
        self.assertEqual(result.switches.model.record(0).state, "1")

    def test_the_closed_switch_is_traversed_by_the_topology(self) -> None:
        # A prova de ponta a ponta: o circuito precisa alcançar as três barras
        # atravessando a chave fechada do trecho 3.
        result = run(network_database())
        membership = result.circuits.model.membership(0)
        self.assertEqual(len(membership.bar_indices), 3)

    def test_integer_fases2_matches_the_phase_configuration(self) -> None:
        result = run(network_database())
        # "13.0" cairia em "sem relação" e apagaria a cor da rede inteira.
        self.assertEqual(result.segments.model.record(0).phases, "13")

    def test_identifiers_match_across_tables(self) -> None:
        result = run(network_database())
        segment = result.segments.model.record(0)
        self.assertEqual(segment.start_bar_id, "7")
        self.assertEqual(result.bars.model.record(0).bar_id, "7")

    def test_fractional_length_keeps_full_precision(self) -> None:
        result = run(network_database())
        self.assertAlmostEqual(
            result.segments.model.record(0).length, 41.297000885009766
        )

    def test_integral_npat_is_accepted(self) -> None:
        result = run(network_database())
        group = result.patterns.model.records_for_load(0)
        self.assertEqual(tuple(item.npat for item in group), (0, 1, 2, 3))

    def test_null_becomes_an_empty_field(self) -> None:
        result = run(network_database())
        self.assertEqual(result.switches.model.record(0).elo_type, "")

    def test_cable_type_two_is_filtered_out(self) -> None:
        result = run(network_database())
        self.assertEqual(len(result.cables.model), 1)
        self.assertEqual(result.cables.ignored_type_rows, 1)


class CoordinateScaleTests(unittest.TestCase):
    def test_detects_decimetres_like_the_real_base(self) -> None:
        database = network_database()
        plan = resolve_mapping(database)
        self.assertEqual(detect_database_scale(database, plan), 10.0)

    def test_applied_scale_brings_the_model_to_metres(self) -> None:
        result = run(network_database(), scale=10.0)
        self.assertAlmostEqual(result.bars.model.x[0], 598994.4)
        self.assertAlmostEqual(result.bars.model.y[0], 8248770.3)
        self.assertIsNone(result.bars.crs_warning)

    def test_without_the_scale_the_warning_fires(self) -> None:
        result = run(network_database(), scale=1.0)
        self.assertIsNotNone(result.bars.crs_warning)

    def test_missing_bars_mapping_falls_back_to_metres(self) -> None:
        database = FakeDatabase({"TRECHO": (["TRECHO_ID"], [])})
        plan = resolve_mapping(database)
        self.assertEqual(detect_database_scale(database, plan), 1.0)


class PartialDatabaseTests(unittest.TestCase):
    def test_bars_only_is_a_valid_import(self) -> None:
        database = FakeDatabase(
            {
                "BARRA": (
                    ["BARRA_ID", "CODIGO", "X", "Y"],
                    [(7, "COD-A", 5989944, 82487703)],
                )
            }
        )
        result = run(database)
        self.assertEqual(len(result.bars.model), 1)
        self.assertIsNone(result.segments)

    def test_a_missing_table_does_not_stop_the_others(self) -> None:
        database = network_database()
        del database._tables["REGULADOR"]
        result = run(database)
        self.assertIsNone(result.regulators)
        self.assertIsNotNone(result.circuits)
        self.assertIn("REGULADOR", result.outcome_for("reguladores").error)

    def test_entities_depending_on_a_failure_are_skipped_with_the_reason(self) -> None:
        database = network_database()
        del database._tables["TRECHO"]
        result = run(database)
        for entity in ("chaves", "reguladores", "circuitos"):
            with self.subTest(entity=entity):
                outcome = result.outcome_for(entity)
                self.assertIn("Depende de", outcome.error)
        # As cargas dependem só das barras e continuam vindo.
        self.assertIsNotNone(result.loads)

    def test_patterns_are_skipped_without_loads(self) -> None:
        database = network_database()
        del database._tables["CARGA"]
        result = run(database)
        self.assertIsNone(result.patterns)
        self.assertIn("Depende de", result.outcome_for("patamares").error)
        self.assertIsNone(result.generators)
        self.assertIn("Depende de", result.outcome_for("geradores").error)

    def test_missing_either_generator_table_only_disables_generators(self) -> None:
        for missing in ("MT_CONS", "MT_GERADOR_CONS"):
            with self.subTest(missing=missing):
                database = network_database()
                del database._tables[missing]
                result = run(database)
                self.assertIsNone(result.generators)
                self.assertIsNotNone(result.patterns)
                self.assertIsNotNone(result.circuits)
                outcome = result.outcome_for("geradores")
                self.assertFalse(outcome.imported)
                self.assertIsNotNone(outcome.error)

    def test_circuits_are_built_without_switches(self) -> None:
        database = network_database()
        del database._tables["CHAVE"]
        result = run(database)
        self.assertIsNotNone(result.circuits)
        self.assertIsNone(result.circuits.model.switches)

    def test_an_entity_without_valid_rows_is_reported_not_raised(self) -> None:
        database = network_database(
            REGULADOR=(
                [
                    "REGU_ID", "TRECHO_ID", "EXTERN_ID", "CODIGO", "LIGACAO",
                    "SNOM", "FAIXA", "NPASSOS", "TAP", "INOM", "VNOM",
                ],
                [(2, 999, 0, "REG-1", 0, 333.0, 0.1, 32, 0.025, 200.0, 13.8)],
            )
        )
        result = run(database)
        self.assertIsNone(result.regulators)
        self.assertIsNotNone(result.outcome_for("reguladores").error)
        self.assertIsNotNone(result.circuits)

    def test_missing_bars_is_fatal(self) -> None:
        database = network_database()
        del database._tables["BARRA"]
        with self.assertRaises(CsvImportError):
            run(database)

    def test_unusable_bars_are_fatal(self) -> None:
        database = network_database(
            BARRA=(["BARRA_ID", "CODIGO", "X", "Y"], [(None, "COD-A", "", "")])
        )
        with self.assertRaises(CsvImportError):
            run(database)

    def test_every_entity_has_an_outcome(self) -> None:
        database = network_database()
        del database._tables["CHAVE"]
        result = run(database)
        self.assertEqual(
            tuple(item.entity for item in result.outcomes), ENTITY_ORDER
        )

    def test_failures_make_the_report_appear(self) -> None:
        database = network_database()
        del database._tables["REGULADOR"]
        self.assertTrue(run(database).has_warnings)

    def test_a_complete_import_needs_no_report(self) -> None:
        self.assertFalse(run(network_database()).has_warnings)


class OverrideTests(unittest.TestCase):
    def test_override_picks_another_table(self) -> None:
        database = network_database()
        database._tables["SE_BARRA"] = (
            ["BARRA_ID", "CODIGO", "X", "Y"],
            [(70, "SE-A", 5989944, 82487703)],
        )
        result = run(database, overrides={"barras": "SE_BARRA"})
        self.assertEqual(result.bars.model.record(0).code, "SE-A")
        # Os trechos apontam para as barras antigas e não resolvem mais.
        self.assertIsNone(result.segments)

    def test_generator_sources_can_be_overridden_independently(self) -> None:
        database = network_database()
        database._tables["GEN_ALT"] = database._tables.pop("MT_GERADOR_CONS")
        database._tables["CONS_ALT"] = database._tables.pop("MT_CONS")
        result = run(
            database,
            overrides={
                "geradores": "GEN_ALT",
                "geradores_mt_cons": "CONS_ALT",
            },
        )
        self.assertEqual(
            result.generators.model.source_paths,
            (r"C:\dados\rede.mdb::GEN_ALT", r"C:\dados\rede.mdb::CONS_ALT"),
        )
        self.assertEqual(result.generators.model.record(0).bar_id, "9")


class CancellationTests(unittest.TestCase):
    def test_cancelling_before_the_first_entity(self) -> None:
        event = threading.Event()
        event.set()
        with self.assertRaises(CsvImportCancelled):
            run(network_database(), cancel_event=event)

    def test_cancellation_is_never_swallowed_as_an_entity_failure(self) -> None:
        class CancellingDatabase(FakeDatabase):
            def __init__(self, event) -> None:  # noqa: ANN001
                super().__init__(network_database()._tables)
                self._event = event

            def iter_rows(self, table, columns, *, batch_size=500):  # noqa: ANN001
                if table == "TRECHO":
                    self._event.set()
                return super().iter_rows(table, columns, batch_size=batch_size)

        event = threading.Event()
        with self.assertRaises(CsvImportCancelled):
            run(CancellingDatabase(event), cancel_event=event)


class ProgressTests(unittest.TestCase):
    def test_progress_never_regresses_and_ends_at_the_total(self) -> None:
        seen: list[tuple[int, int, int]] = []
        run(network_database(), progress=lambda *args: seen.append(args))
        self.assertTrue(seen)
        currents = [current for _rows, current, _total in seen]
        self.assertEqual(currents, sorted(currents))
        total = seen[-1][2]
        self.assertLessEqual(currents[-1], total)

    def test_progress_spans_the_whole_chain(self) -> None:
        seen: list[tuple[int, int, int]] = []
        run(network_database(), progress=lambda *args: seen.append(args))
        # Um total só para as oito entidades, em vez de reiniciar oito vezes.
        self.assertEqual(len({total for _rows, _current, total in seen}), 1)


class HelpersTests(unittest.TestCase):
    def test_source_label_joins_file_and_table(self) -> None:
        self.assertEqual(source_label(r"C:\a\rede.mdb", "BARRA"), r"C:\a\rede.mdb::BARRA")

    def test_dependencies_cover_every_entity(self) -> None:
        self.assertEqual(set(ENTITY_DEPENDENCIES), set(ENTITY_ORDER))

    def test_dependencies_come_before_their_dependents(self) -> None:
        position = {entity: index for index, entity in enumerate(ENTITY_ORDER)}
        for entity, sources in ENTITY_DEPENDENCIES.items():
            for source in sources:
                with self.subTest(entity=entity, source=source):
                    self.assertLess(position[source], position[entity])


if __name__ == "__main__":
    unittest.main()
