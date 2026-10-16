"""Vínculo de UCs, IP e GD às CARGAs, sem banco: as linhas chegam prontas."""

from __future__ import annotations

import unittest

from circuit_viewer.transformer_consumers import (
    CONSUMER_KIND_BT,
    CONSUMER_KIND_IP,
    CONSUMER_KIND_MT,
    ORIGIN_EP,
    ORIGIN_ET,
    ORIGIN_ET_EP,
    TransformerConsumersModel,
    TransformerConsumersRecord,
    build_transformer_consumers,
)
from circuit_viewer.mdb_import import dataset_from_result
from circuit_viewer.mdb_import_report import issue_lines
from circuit_viewer.network_registry import FileIdentity, register_import
from circuit_viewer.source_composition import (
    SourceWorkspace,
    compose,
    restrict_to_circuits,
)
from tests.test_mdb_import import network_database, run
from tests.test_source_composition import dataset


CARGA_COLUMNS = [
    "CARGA_ID", "BARRA_ID", "EXTERN_ID", "CODIGO", "SNOM", "SADM",
    "VLINHASEC", "FASES2", "TIPO_LIG", "FATDEM",
]


def loads_model():  # noqa: ANN201
    """Quatro CARGAs: só ET, só EP, ET e EP juntos e uma sem nada pendurado."""

    database = network_database(
        CARGA=(
            CARGA_COLUMNS,
            [
                (2, 9, 1, "ET-2", 25.0, 25.0, 254.0, 1, 0, None),
                (3, 8, 2, "EP-3", 2500.0, 2500.0, 220.0, 13, 2, None),
                (4, 9, 3, "MISTO-4", 225.0, 225.0, 380.0, 13, 2, None),
                (5, 7, 4, "VAZIA-5", 15.0, 15.0, 220.0, 1, 0, None),
            ],
        ),
    )
    return run(database).loads.model


def et(et_id, load_id, code=""):  # noqa: ANN001, ANN201
    return {"ID": str(et_id), "MT_CAR_ID": str(load_id), "CODIGO": code}


def utc(et_id, snom, vpri, vsec, fases_bt2):  # noqa: ANN001, ANN201
    return {
        "ET_ID": str(et_id), "SNOM": str(snom), "VPRIMARIO": str(vpri),
        "VSECUNDA": str(vsec), "FASES_BT2": str(fases_bt2),
    }


def bt(consumer_id, et_id, consumo, fases2="1", classe="0", ps="900"):  # noqa: ANN001, ANN201
    return {
        "ID": str(consumer_id), "ET_ID": str(et_id), "CONSUMO": str(consumo),
        "FASES2": fases2, "CLASSE": classe, "PS_ID": ps,
        "CODIGO": f"UC-{consumer_id}", "NOME": f"Cliente  {consumer_id}",
    }


def mt(consumer_id, load_id, consumo, ativo="1"):  # noqa: ANN001, ANN201
    return {
        "ID": str(consumer_id), "CARGA_ID": str(load_id), "CONSUMO": str(consumo),
        "FASES2": "13", "CLASSE": "2", "CODIGO": f"MT-{consumer_id}", "NOME": "Fábrica",
        "ATIVO": ativo,
    }


def standard_rows() -> dict:
    return dict(
        transformers=[et(17, 2, "5607484166"), et(18, 4, "MISTO-4")],
        transformer_units=[
            utc(17, 25, 7.967430114746094, 254, 10),
            utc(18, 225, 13.800000190734863, 380, 14),
        ],
        bt_consumers=[bt(1001, 17, 120.5), bt(1002, 17, "", fases2="7"), bt(1003, 18, 300)],
        mt_consumers=[mt(556, 4, 1929.5), mt(557, 3, 12192.17), mt(558, 3, 0, ativo="0")],
        public_lighting=[
            {"ID": "168662", "ET_ID": "17", "DEMANDA": "0.15000000596046448",
             "FASES2": "1", "PS_ID": "901", "CODIGO": "35595273", "NLAMP": "1"},
        ],
        bt_generators=[
            {"GERADOR_ID": "71", "BT_CONS_ID": "1001", "GERACAO_KWH": "250", "TIPOGD": "0",
             "CURVA_ID": "0", "SNOM": "5", "CODIGO": "GD-71"},
        ],
        mt_generators=[
            {"GERADOR_ID": "132", "MT_CONS_ID": "556", "GERACAO_KWH": "22713",
             "TIPOGD": "0", "CURVA_ID": "387", "SNOM": "0", "CODIGO": "GD-132"},
        ],
    )


class LinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loads = loads_model()
        self.model = build_transformer_consumers(self.loads, **standard_rows())

    def record(self, load_id: str) -> TransformerConsumersRecord:
        return self.model.record(self.loads.index_for_id(load_id))

    def test_bt_consumers_and_lighting_reach_the_carga_through_the_et(self) -> None:
        record = self.record("2")
        self.assertEqual(
            [(item.kind, item.consumer_id) for item in record.consumers],
            [(CONSUMER_KIND_BT, "1001"), (CONSUMER_KIND_BT, "1002"), (CONSUMER_KIND_IP, "168662")],
        )
        first = record.consumers[0]
        self.assertEqual((first.code, first.name, first.consumer_class), ("UC-1001", "Cliente 1001", 0))
        self.assertEqual((first.consumption_kwh, first.service_point_id), (120.5, "900"))
        self.assertIsNone(record.consumers[1].consumption_kwh)
        lighting = record.consumers[2]
        self.assertAlmostEqual(lighting.demand_kw, 0.15000000596046448)
        self.assertEqual(lighting.lamp_count, 1)

    def test_the_et_transformer_comes_from_the_utc(self) -> None:
        spec = self.record("2").transformer
        self.assertEqual(spec.origin, ORIGIN_ET)
        self.assertEqual(spec.codes, ("5607484166",))
        self.assertEqual(spec.kva, 25.0)
        self.assertAlmostEqual(spec.primary_kv, 7.967430114746094)
        self.assertEqual((spec.secondary_v, spec.secondary_phases), (254.0, "10"))

    def test_an_ep_alone_uses_the_carga_data(self) -> None:
        record = self.record("3")
        spec = record.transformer
        self.assertEqual(spec.origin, ORIGIN_EP)
        self.assertEqual((spec.kva, spec.secondary_v, spec.primary_kv), (2500.0, 220.0, None))
        self.assertEqual([item.kind for item in record.consumers], [CONSUMER_KIND_MT] * 2)
        self.assertEqual([item.active for item in record.consumers], [True, False])

    def test_et_and_ep_on_the_same_carga_are_one_transformer(self) -> None:
        record = self.record("4")
        self.assertEqual(record.transformer.origin, ORIGIN_ET_EP)
        self.assertEqual(record.transformer.kva, 225.0)
        self.assertEqual(
            sorted(item.kind for item in record.consumers),
            [CONSUMER_KIND_BT, CONSUMER_KIND_MT],
        )

    def test_generation_follows_its_consumer(self) -> None:
        self.assertEqual([item.generator_id for item in self.record("2").generators], ["71"])
        (generator,) = self.record("4").generators
        self.assertEqual((generator.kind, generator.consumer_id), (CONSUMER_KIND_MT, "556"))
        self.assertEqual((generator.curve_id, generator.generation_kwh), ("387", 22713.0))
        self.assertEqual(generator.generation_type, 0)

    def test_a_carga_without_et_or_ep_keeps_no_transformer(self) -> None:
        record = self.record("5")
        self.assertIsNone(record.transformer)
        self.assertEqual((record.consumers, record.generators), ((), ()))

    def test_counts(self) -> None:
        self.assertEqual(self.model.consumer_count, 7)
        self.assertEqual(self.model.generator_count, 2)
        self.assertEqual(self.model.issues, ())


class IssueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loads = loads_model()

    def build(self, **changes) -> TransformerConsumersModel:  # noqa: ANN003
        rows = standard_rows()
        rows.update(changes)
        return build_transformer_consumers(
            self.loads, sources={"BT_CONS": "rede.mdb/BT_CONS"}, **rows
        )

    def test_a_consumer_of_an_unknown_et_is_reported(self) -> None:
        model = self.build(bt_consumers=[bt(1001, 99, 10)], bt_generators=[])
        (issue,) = model.issues
        self.assertEqual(issue.source, "rede.mdb/BT_CONS")
        self.assertIn("ET 99", issue.reason)

    def test_a_repeated_consumer_keeps_the_first(self) -> None:
        model = self.build(bt_consumers=[bt(1001, 17, 10), bt(1001, 17, 20)])
        record = model.record(self.loads.index_for_id("2"))
        self.assertEqual([item.consumption_kwh for item in record.consumers if item.kind == "BT"], [10.0])
        self.assertTrue(any("repetida" in issue.reason for issue in model.issues))

    def test_an_orphan_generator_is_reported(self) -> None:
        model = self.build(bt_generators=[{"GERADOR_ID": "9", "BT_CONS_ID": "4040", "GERACAO_KWH": "1"}])
        self.assertTrue(any("GD 9" in issue.reason for issue in model.issues))
        self.assertEqual(model.generator_count, 1)

    def test_et_and_ep_disagreeing_is_reported_and_the_et_wins(self) -> None:
        model = self.build(transformer_units=[
            utc(17, 25, 7.967, 254, 10), utc(18, 150, 13.8, 220, 14),
        ])
        record = model.record(self.loads.index_for_id("4"))
        self.assertEqual((record.transformer.kva, record.transformer.secondary_v), (150.0, 220.0))
        (issue,) = model.issues_for_loads(("4",))
        self.assertIn("kVA e tensão do secundário", issue.reason)

    def test_two_ets_on_one_carga_add_their_kva(self) -> None:
        model = self.build(
            transformers=[et(17, 2), et(19, 2), et(18, 4, "MISTO-4")],
            transformer_units=[
                utc(17, 25, 7.967, 254, 10), utc(19, 15, 7.967, 254, 10),
                utc(18, 225, 13.8, 380, 14),
            ],
        )
        spec = model.record(self.loads.index_for_id("2")).transformer
        self.assertEqual(spec.kva, 40.0)
        self.assertTrue(any("2 ETs" in issue.reason for issue in model.issues))

    def test_an_et_without_utc_has_no_ratings(self) -> None:
        model = self.build(transformer_units=[utc(18, 225, 13.8, 380, 14)])
        spec = model.record(self.loads.index_for_id("2")).transformer
        self.assertEqual((spec.origin, spec.kva, spec.secondary_v), (ORIGIN_ET, None, None))
        self.assertTrue(any("sem UTC" in issue.reason for issue in model.issues_for_loads(("2",))))

    def test_decimal_ids_from_the_driver_still_link(self) -> None:
        model = self.build(
            transformers=[et("17.0", "2.0")],
            bt_consumers=[bt("1001.0", "17.0", 10)],
            bt_generators=[], mt_generators=[], mt_consumers=[], public_lighting=[],
        )
        record = model.record(self.loads.index_for_id("2"))
        self.assertEqual([item.consumer_id for item in record.consumers], ["1001"])

    def test_cancellation(self) -> None:
        rows = standard_rows()
        rows["bt_consumers"] = [bt(index, 17, 1) for index in range(10_000)]
        with self.assertRaises(InterruptedError):
            build_transformer_consumers(self.loads, cancel_check=lambda: True, **rows)


class ModelTests(unittest.TestCase):
    def test_records_must_follow_the_loads(self) -> None:
        loads = loads_model()
        with self.assertRaises(ValueError):
            TransformerConsumersModel(loads, (TransformerConsumersRecord("2"),))
        records = tuple(TransformerConsumersRecord(load_id) for load_id in reversed(loads.load_ids))
        with self.assertRaises(ValueError):
            TransformerConsumersModel(loads, records)


def extended_tables(offset: int = 0) -> dict:
    """As tabelas de UCs de uma CARGA com ET e EP, como a 17495 da base real."""

    o = offset
    return {
        "BT_ET": (
            ["ID", "BARINI_ID", "MT_CIRC_ID", "MT_CAR_ID", "CODIGO", "EXTERN_ID"],
            [(17 + o, 0, 2 + o, 2 + o, f"ET-17{o}", "")],
        ),
        "BT_UTC": (
            ["ID", "CODIGO", "ET_ID", "SNOM", "SADM", "VPRIMARIO", "VSECUNDA",
             "FASES_MT2", "FASES_BT2"],
            [(1 + o, "UTC", 17 + o, 30.0, 30.0, 13.800000190734863, 220.0, 13, 14)],
        ),
        "BT_CONS": (
            ["ID", "PS_ID", "CODIGO", "NOME", "EXTERN_ID", "CONSUMO", "ET_ID", "FASES2", "CLASSE"],
            [
                (1001 + o, 900, "UC-1", "Ana", "", 150.0, 17 + o, 1, 0),
                (1002 + o, 900, "UC-2", "Beto", "", 320.5, 17 + o, 13, 1),
            ],
        ),
        "BT_IP": (
            ["ID", "PS_ID", "CODIGO", "EXTERN_ID", "ET_ID", "DEMANDA", "NLAMP",
             "LAMPCONST", "FASES2"],
            [(168662 + o, 901, "IP-1", "", 17 + o, 0.15000000596046448, 1, 0, 1)],
        ),
        "BT_GERADOR_CONS": (
            ["GERADOR_ID", "BT_CONS_ID", "CODIGO", "SNOM", "TIPOGD", "CURVA_ID", "GERACAO_KWH"],
            [(71 + o, 1001 + o, "GD-71", 5.0, 0, 0, 250.0)],
        ),
        "MT_CONS": (
            ["ID", "CARGA_ID", "CODIGO", "EXTERN_ID", "NOME", "FASES2", "CONSUMO",
             "CLASSE", "ATIVO"],
            [(101 + o, 2 + o, f"GEN-COD{o}", "EXT-GEN", "Usina", 13, 1929.5, 3, 1)],
        ),
    }


class MdbImportTests(unittest.TestCase):
    def test_the_extended_database_brings_the_consumers(self) -> None:
        result = run(network_database(**extended_tables()))
        self.assertIsNone(result.consumers_error)
        model = result.consumers
        self.assertIs(model.loads, result.loads.model)
        (record,) = model.records
        self.assertEqual(record.transformer.origin, ORIGIN_ET_EP)
        self.assertEqual((record.transformer.kva, record.transformer.secondary_v), (30.0, 220.0))
        self.assertEqual(
            [(item.kind, item.consumer_id) for item in record.consumers],
            [("BT", "1001"), ("BT", "1002"), ("MT", "101"), ("IP", "168662")],
        )
        self.assertEqual(
            sorted((item.kind, item.generator_id) for item in record.generators),
            [("BT", "71"), ("MT", "201")],
        )
        self.assertEqual(model.issues, ())

    def test_a_database_without_bt_et_stays_silent(self) -> None:
        result = run(network_database())
        self.assertIsNone(result.consumers)
        self.assertIsNone(result.consumers_error)
        # O único aviso é o que já existia: a contagem de UC sem BT_ET.
        self.assertFalse(any("Unidades consumidoras" in line for line in issue_lines(result)))

    def test_a_table_missing_a_required_column_is_reported(self) -> None:
        tables = extended_tables()
        tables["BT_CONS"] = (
            ["ID", "PS_ID", "CODIGO", "ET_ID", "FASES2", "CLASSE"],
            [(1001, 900, "UC-1", 17, 1, 0)],
        )
        result = run(network_database(**tables))
        self.assertIsNone(result.consumers)
        self.assertIn("CONSUMO", result.consumers_error)

    def test_consumer_problems_stay_out_of_the_import_report(self) -> None:
        """Só interessam ao modo Energia; o modo Interplan não ganha ruído."""

        database = network_database(
            BT_ET=(["ID", "MT_CAR_ID"], []), BT_CONS=(["ID", "ET_ID"], [])
        )
        result = run(database)
        self.assertIsNotNone(result.consumers_error)
        self.assertEqual(issue_lines(result), ())
        self.assertFalse(result.has_warnings)

    def test_a_missing_optional_table_is_a_note(self) -> None:
        tables = extended_tables()
        database = network_database(**tables)
        del database._tables["BT_IP"]
        model = run(database).consumers
        self.assertEqual([item.kind for item in model.records[0].consumers], ["BT", "BT", "MT"])
        self.assertTrue(any("BT_IP" in issue.source and "ausente" in issue.reason for issue in model.issues))


class CompositionTests(unittest.TestCase):
    def test_a_single_source_keeps_its_model(self) -> None:
        source = dataset("F1", **extended_tables())
        self.assertIsNotNone(source.consumers)
        composed = compose((source,))
        self.assertIs(composed.consumers, source.consumers)

    def test_restriction_keeps_the_records_of_the_kept_loads(self) -> None:
        source = dataset("F1", **extended_tables())
        restricted = restrict_to_circuits(source, ("2",))
        self.assertIs(restricted.consumers.loads, restricted.loads)
        self.assertEqual(restricted.consumers.records, source.consumers.records)

    def test_two_sources_with_the_same_ids_are_aliased(self) -> None:
        first = dataset("F1", **extended_tables())
        second = dataset("F2", **extended_tables())
        composed = compose((first, second))
        model = composed.consumers
        self.assertIs(model.loads, composed.loads)
        self.assertEqual(len(model.records), 2)
        self.assertEqual([record.load_id for record in model.records], list(composed.loads.load_ids))
        self.assertTrue(all(record.transformer.origin == ORIGIN_ET_EP for record in model.records))

    def test_a_source_without_consumers_falls_back_to_interplan(self) -> None:
        first = dataset("F1", **extended_tables())
        second = dataset("F2", offset=1000)
        model = compose((first, second)).consumers
        self.assertEqual(len(model.records), 2)
        self.assertIsNotNone(model.records[0].transformer)
        self.assertIsNone(model.records[1].transformer)
        self.assertTrue(any("sem dados de unidades consumidoras" in issue.reason for issue in model.issues))


class RegistryTests(unittest.TestCase):
    def test_the_project_registry_keeps_the_consumers(self) -> None:
        source = dataset("F1", **extended_tables())
        _, rebuilt = register_import(
            SourceWorkspace(), source, FileIdentity("rede.mdb", "assinatura")
        )
        self.assertIsNotNone(rebuilt.consumers)
        self.assertIs(rebuilt.consumers.loads, rebuilt.loads)
        self.assertEqual(rebuilt.consumers.records, source.consumers.records)


if __name__ == "__main__":
    unittest.main()
