from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from circuit_viewer.allocation import (
    AllocationTable,
    build_transformer_allocations,
)
from circuit_viewer.allocation_measurements import (
    parse_allocation_measurement_rows,
)
from circuit_viewer.csv_import import CsvImportError
from circuit_viewer.calculation_levels import default_calculation_levels
from circuit_viewer.curvas import Curve
from circuit_viewer.model import (
    CableModel,
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitMembership,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    UtmCrs,
)
from circuit_viewer.opendss_allocation_export import (
    BT_GENERATION_FILENAME,
    ENERGY_LOADS_FILENAME,
    MT_GENERATION_FILENAME,
    OpenDssAllocationExportError,
    build_allocation_export,
    write_allocation_export,
)
from circuit_viewer.opendss_allocation_settings import OpenDssAllocationSettings
from circuit_viewer.opendss_engine import (
    acquire_engine,
    ascii_workspace,
    power_flow_available,
)
from circuit_viewer.phase_config import load_phase_configuration


def make_models():  # noqa: ANN201
    bars = CircuitModel(
        ["B0", "B1", "B2"],
        ["ROOT", "MID", "6401_AUX"],
        [500_000.0, 500_100.0, 500_200.0],
        [8_000_000.0] * 3,
        UtmCrs(21, northern=False),
    )
    lines = LineNetworkModel(
        bars,
        ["S0", "S1"],
        ["L0", "L1"],
        ["13", "13"],
        [0, 1],
        [1, 2],
        ["", ""],
        ["CB1", "CB1"],
        ["", ""],
        [100.0, 100.0],
    )
    catalog = CircuitCatalogModel.build(
        lines,
        None,
        [CircuitDefinition("C1", "B0", "ALIM-01", "34,5")],
    )
    loads = LoadModel(
        bars,
        ["L1"],
        [2],
        ["EXT-1"],
        ["57169589ME"],
        ["100"],
        ["100"],
        ["220"],
        ["13"],
        ["Y"],
    )
    cables = CableModel(
        ["CB1"],
        ["1"],
        ["4/0"],
        ["340"],
        ["0.00824"],
        ["0.367"],
        ["0.42"],
        ["1.2"],
        ["0.551"],
        ["1.232"],
        ["0.367"],
        ["0.42"],
        ["CABO"],
        ["EXT-CB"],
    )
    return bars, catalog, loads, cables


def table(header, rows, name):  # noqa: ANN001, ANN201
    return AllocationTable(header, rows, name)


def make_allocations(loads):  # noqa: ANN001, ANN201
    phases = load_phase_configuration()
    model = build_transformer_allocations(
        loads,
        phases,
        bt_et=table(("ID", "MT_CAR_ID"), [("ET1", "L1")], "BT_ET"),
        bt_consumers=table(
            ("ET_ID", "FASES2", "CONSUMO"),
            [("ET1", "1", "3000"), ("ET1", "7", "1000")],
            "BT_CONS",
        ),
        bt_generators=table(
            ("ET_ID", "GERACAO_KWH"), [("ET1", "720")], "BT_GERADOR_CONS"
        ),
        mt_consumers=table(
            ("ID", "CARGA_ID", "FASES2", "CONSUMO"),
            [("MT1", "L1", "13", "300")],
            "MT_CONS",
        ),
        mt_generators=table(
            ("MT_CONS_ID", "GERACAO_KWH"),
            [("MT1", "1440")],
            "MT_GERADOR_CONS",
        ),
    )
    return phases, model


class AllocationAggregationTests(unittest.TestCase):
    def test_aggregates_consumers_by_their_phases_and_generators_by_transformer(self):
        _bars, _catalog, loads, _cables = make_models()
        _phases, allocations = make_allocations(loads)

        record = allocations.record(0)
        self.assertEqual((record.energy_bt.d, record.energy_bt.e, record.energy_bt.f), (3500, 500, 0))
        self.assertEqual((record.energy_mt.d, record.energy_mt.e, record.energy_mt.f), (100, 100, 100))
        self.assertEqual((record.total_energy.d, record.total_energy.e, record.total_energy.f), (3600, 600, 100))
        self.assertEqual(record.generation_bt_kwh, 720)
        self.assertEqual(record.generation_mt_kwh, 1440)
        self.assertEqual(allocations.issues, ())

    def test_unmapped_transformer_phase_is_reported(self):
        bars, _catalog, _loads, _cables = make_models()
        loads = LoadModel(
            bars,
            ["L404"],
            [2],
            [""],
            ["T404"],
            ["1"],
            ["1"],
            ["220"],
            ["404"],
            ["Y"],
        )
        phases = load_phase_configuration()
        allocation = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [], "BT_ET"),
            bt_consumers=table(("ET_ID", "FASES2", "CONSUMO"), [], "BT_CONS"),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        self.assertIn("404", allocation.issues[0].reason)

    def test_biphase_bt_client_on_monophase_transformer_uses_full_primary_phase(self):
        bars, _catalog, _loads, _cables = make_models()
        phases = load_phase_configuration()

        for transformer_phase, expected in (
            ("1", (600, 0, 0)),
            ("2", (0, 600, 0)),
            ("3", (0, 0, 600)),
        ):
            with self.subTest(transformer_phase=transformer_phase):
                loads = LoadModel(
                    bars,
                    ["LM"],
                    [2],
                    [""],
                    [f"MONO-{transformer_phase}"],
                    ["1"],
                    ["1"],
                    ["220"],
                    [transformer_phase],
                    ["Y"],
                )
                allocation = build_transformer_allocations(
                    loads,
                    phases,
                    bt_et=table(("ID", "MT_CAR_ID"), [("ET1", "LM")], "BT_ET"),
                    bt_consumers=table(
                        ("ET_ID", "FASES2", "CONSUMO"),
                        [("ET1", "7", "600")],
                        "BT_CONS",
                    ),
                    bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
                    mt_consumers=table(
                        ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
                    ),
                    mt_generators=table(
                        ("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"
                    ),
                )

                record = allocation.record(0)
                self.assertEqual(
                    (record.energy_bt.d, record.energy_bt.e, record.energy_bt.f),
                    expected,
                )
                self.assertEqual(allocation.issues, ())

    def test_monophase_transformer_sums_mono_and_biphase_bt_clients_integrally(self):
        bars, _catalog, _loads, _cables = make_models()
        loads = LoadModel(
            bars,
            ["LM"],
            [2],
            [""],
            ["MONO-E"],
            ["1"],
            ["1"],
            ["220"],
            ["2"],
            ["Y"],
        )
        phases = load_phase_configuration()
        allocation = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [("ET1", "LM")], "BT_ET"),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO"),
                [("ET1", "2", "100"), ("ET1", "7", "600")],
                "BT_CONS",
            ),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )

        record = allocation.record(0)
        self.assertEqual(
            (record.energy_bt.d, record.energy_bt.e, record.energy_bt.f),
            (0, 700, 0),
        )
        self.assertEqual(allocation.issues, ())

    def test_phase_incompatibility_remains_strict_for_multiphase_bt_and_all_mt(self):
        bars, _catalog, _loads, _cables = make_models()
        loads = LoadModel(
            bars,
            ["LBI", "LMONO"],
            [2, 2],
            ["", ""],
            ["BI-DE", "MONO-D"],
            ["1", "1"],
            ["1", "1"],
            ["220", "220"],
            ["7", "1"],
            ["Y", "Y"],
        )
        phases = load_phase_configuration()
        allocation = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [("ET-BI", "LBI")], "BT_ET"),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO", "ID", "CODIGO"),
                [("ET-BI", "8", "600", "913", "396000")],
                "BT_CONS",
            ),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"),
                [("MT1", "LMONO", "7", "300")],
                "MT_CONS",
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )

        self.assertEqual(
            (allocation.record(0).energy_bt.d, allocation.record(0).energy_bt.e,
             allocation.record(0).energy_bt.f),
            (0, 0, 0),
        )
        self.assertEqual(
            (allocation.record(1).energy_mt.d, allocation.record(1).energy_mt.e,
             allocation.record(1).energy_mt.f),
            (0, 0, 0),
        )
        self.assertEqual(len(allocation.issues), 2)
        self.assertTrue(
            all("incompatíveis" in issue.reason for issue in allocation.issues)
        )
        bt_reason = allocation.issues[0].reason
        self.assertIn("ID=913", bt_reason)
        self.assertIn("CODIGO=396000", bt_reason)
        self.assertIn("ET_ID=ET-BI", bt_reason)
        self.assertIn("FASES2=8 (E-F)", bt_reason)
        self.assertIn("CARGA_ID=LBI", bt_reason)
        self.assertIn("CODIGO=BI-DE", bt_reason)
        self.assertIn("FASES2=7 (D-E)", bt_reason)

    def test_monophase_exception_still_requires_valid_bt_consumer_phase(self):
        bars, _catalog, _loads, _cables = make_models()
        loads = LoadModel(
            bars,
            ["LM"],
            [2],
            [""],
            ["MONO-E"],
            ["1"],
            ["1"],
            ["220"],
            ["2"],
            ["Y"],
        )
        allocation = build_transformer_allocations(
            loads,
            load_phase_configuration(),
            bt_et=table(("ID", "MT_CAR_ID"), [("ET1", "LM")], "BT_ET"),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO"),
                [("ET1", "404", "600")],
                "BT_CONS",
            ),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )

        record = allocation.record(0)
        self.assertEqual(
            (record.energy_bt.d, record.energy_bt.e, record.energy_bt.f),
            (0, 0, 0),
        )
        self.assertEqual(len(allocation.issues), 1)
        self.assertIn("FASES2 sem relação válida", allocation.issues[0].reason)

    def test_reports_negative_values_and_unidentifiable_orphans(self):
        _bars, _catalog, loads, _cables = make_models()
        phases = load_phase_configuration()
        allocation = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [("ET1", "L1")], "BT_ET"),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO"),
                [("SEM-ET", "1", "10")],
                "BT_CONS",
            ),
            bt_generators=table(
                ("ET_ID", "GERACAO_KWH"), [("ET1", "-1")], "BT_GD"
            ),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(
                ("MT_CONS_ID", "GERACAO_KWH"), [("SEM-MT", "10")], "MT_GD"
            ),
        )
        reasons = "\n".join(issue.reason for issue in allocation.issues)
        self.assertIn("SEM-ET", reasons)
        self.assertIn("não negativo", reasons)
        self.assertIn("SEM-MT", reasons)


class MeasurementTests(unittest.TestCase):
    def test_uses_circuit_code_and_preserves_leading_zero(self):
        _bars, original, _loads, _cables = make_models()
        catalog = CircuitCatalogModel.build(
            original.segments,
            None,
            [CircuitDefinition("2", "B0", "004011", "34,5")],
        )

        result = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("004011", str(npat), "1", "2", "3") for npat in range(4)],
            catalog,
            source_label="medicoes.csv",
            encoding="utf-8",
        )

        records = result.model.records_for_circuit(0)
        self.assertEqual(tuple(record.circuit_id for record in records), ("2",) * 4)

    def test_rejects_old_header_and_shows_the_exact_expected_header(self):
        _bars, catalog, _loads, _cables = make_models()

        with self.assertRaises(CsvImportError) as raised:
            parse_allocation_measurement_rows(
                ("CIRC_ID", "NPAT", "ID", "IE", "IF"),
                (),
                catalog,
                source_label="medicoes.csv",
                encoding="utf-8",
            )

        self.assertIn("CODIGO;NPAT;ID;IE;IF", str(raised.exception))
        self.assertIn("ponto e vírgula", str(raised.exception))

    def test_rejects_ambiguous_circuit_code(self):
        _bars, original, _loads, _cables = make_models()
        catalog = CircuitCatalogModel.build(
            original.segments,
            None,
            [
                CircuitDefinition("C1", "B0", "004011", "34,5"),
                CircuitDefinition("C2", "B2", "004011", "34,5"),
            ],
        )

        with self.assertRaisesRegex(CsvImportError, "CODIGO de circuito ambíguo"):
            parse_allocation_measurement_rows(
                ("CODIGO", "NPAT", "ID", "IE", "IF"),
                [("004011", str(npat), "1", "2", "3") for npat in range(4)],
                catalog,
                source_label="medicoes.csv",
                encoding="utf-8",
            )

    def test_accepts_dot_and_comma_and_requires_four_npat(self):
        _bars, catalog, _loads, _cables = make_models()
        result = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [
                ("ALIM-01", "0", "10,5", "20", "30.25"),
                ("ALIM-01", "1", "11", "21", "31"),
                ("ALIM-01", "2", "12", "22", "32"),
                ("ALIM-01", "3", "13", "23", "33"),
            ],
            catalog,
            source_label="medicoes.csv",
            encoding="utf-8",
        )
        self.assertEqual(result.model.records_for_circuit(0)[0].currents, (10.5, 20.0, 30.25))

    def test_invalid_other_circuit_does_not_discard_a_complete_group(self):
        _bars, catalog, _loads, _cables = make_models()
        catalog = CircuitCatalogModel.build(
            catalog.segments,
            None,
            [
                CircuitDefinition("C1", "B0", "ALIM-01", "34,5"),
                CircuitDefinition("C2", "B2", "ALIM-02", "34,5"),
            ],
        )
        rows = [
            *(("ALIM-01", str(npat), "1", "2", "3") for npat in range(4)),
            ("ALIM-02", "0", "1", "2", "3"),
        ]

        result = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            rows,
            catalog,
            source_label="medicoes.csv",
            encoding="utf-8",
        )

        self.assertEqual(result.model.available_indices, (0,))
        self.assertTrue(result.has_warnings)
        self.assertTrue(any("ALIM-02" in issue for issue in result.issues))

    def test_duplicate_negative_and_unknown_circuit_are_rejected(self):
        _bars, catalog, _loads, _cables = make_models()
        complete = [
            ("ALIM-01", str(npat), "1", "2", "3") for npat in range(4)
        ]
        cases = (
            [*complete, complete[0]],
            [complete[0], ("ALIM-01", "1", "-1", "2", "3"), *complete[2:]],
            [*complete, ("DESCONHECIDO", "0", "1", "2", "3")],
        )
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(CsvImportError):
                parse_allocation_measurement_rows(
                    ("CODIGO", "NPAT", "ID", "IE", "IF"),
                    rows,
                    catalog,
                    source_label="medicoes.csv",
                    encoding="utf-8",
                )


class AllocationExportTests(unittest.TestCase):
    def make_bundle(self, curve: Curve | None = None):  # noqa: ANN201
        _bars, catalog, loads, cables = make_models()
        phases, allocations = make_allocations(loads)
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), str(10 + npat), str(20 + npat), str(30 + npat)) for npat in range(4)],
            catalog,
            source_label="medicoes.csv",
            encoding="utf-8",
        ).model
        curve = curve or Curve("CURVA", "GD teste", (2.0,) * 24)
        return build_allocation_export(
            catalog,
            cables,
            phases,
            0,
            allocations,
            measurements,
            curve,
            default_calculation_levels(),
            OpenDssAllocationSettings(30, 4, 0.92, 2),
        )

    def make_monophase_bundle(self):  # noqa: ANN201
        bars, catalog, _loads, cables = make_models()
        loads = LoadModel(
            bars,
            ["L1"],
            [2],
            ["EXT-1"],
            ["MONO-E"],
            ["100"],
            ["100"],
            ["220"],
            ["2"],
            ["Y"],
        )
        phases = load_phase_configuration()
        allocations = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [("ET1", "L1")], "BT_ET"),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO"),
                [("ET1", "7", "600")],
                "BT_CONS",
            ),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), "0", "1", "0") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model
        return build_allocation_export(
            catalog,
            cables,
            phases,
            0,
            allocations,
            measurements,
            Curve("C", "C", (1.0,) * 24),
            default_calculation_levels(),
            OpenDssAllocationSettings(),
        )

    def test_emits_only_energy_as_allocatable_and_fixed_generation_per_phase(self):
        bundle = self.make_bundle()
        self.assertEqual(len(bundle.levels), 4)
        level = bundle.levels[0]
        files = dict(level.files)
        energy = files[ENERGY_LOADS_FILENAME]
        gd_bt = files[BT_GENERATION_FILENAME]
        gd_mt = files[MT_GENERATION_FILENAME]

        self.assertEqual(energy.count("New Load."), 3)
        self.assertIn("Load.57169589ME-3F-D", energy)
        self.assertIn("kWh=3600 kWhDays=30 CFactor=4 PF=0.92", energy)
        self.assertIn("kWh=600", energy)
        self.assertIn("kWh=100", energy)
        self.assertIn("kV=19.9186", energy)
        self.assertIn(".1 conn=wye", energy)
        self.assertIn(".2 conn=wye", energy)
        self.assertIn(".3 conn=wye", energy)

        self.assertEqual(gd_bt.count("New Load."), 3)
        self.assertIn("kW=-0.666667 kvar=0 status=fixed", gd_bt)
        self.assertEqual(gd_mt.count("New Load."), 3)
        self.assertIn("kW=-1.33333 kvar=0 status=fixed", gd_mt)
        for fixed in (gd_bt, gd_mt):
            self.assertNotIn("kWh=", fixed)
            self.assertNotIn("CFactor=", fixed)
        self.assertEqual(level.load_count, 9)

    def test_master_installs_meter_and_runs_snapshot_allocation(self):
        bundle = self.make_bundle()
        level = bundle.levels[2]
        master = dict(level.files)[level.master_filename]
        self.assertIn("r1=0.0001 x1=0.0001 r0=0.0003 x0=0.0003", master)
        self.assertIn("c1=0 c0=0 length=1 units=km", master)
        self.assertIn("New EnergyMeter.", master)
        self.assertIn("PeakCurrent=[12 22 32]", master)
        self.assertIn("Set mode=snapshot", master)
        self.assertIn("Set NumAllocIterations=2", master)
        self.assertEqual(master.count("Solve"), 2)
        self.assertIn("AllocateLoads", master)
        self.assertIn("fluxo reverso", master)
        self.assertNotIn("LoadShape", "\n".join(dict(level.files)))

    def test_uses_each_level_reference_hour_for_generation(self):
        curve = Curve(
            "CURVA-HORAS",
            "Horas",
            tuple(float(hour) for hour in range(1, 25)),
        )
        bundle = self.make_bundle(curve)
        expected = (23.0 / 3.0, 11.0 / 3.0, 12.0 / 3.0, 22.0 / 3.0)
        for level, kw in zip(bundle.levels, expected, strict=True):
            text = dict(level.files)[BT_GENERATION_FILENAME]
            self.assertIn(f"kW=-{kw:.6g}", text)

    def test_one_two_and_three_phase_transformers_emit_three_objects_per_phase(self):
        bars, catalog, _loads, cables = make_models()
        loads = LoadModel(
            bars,
            ["M", "B", "T"],
            [2, 2, 2],
            ["", "", ""],
            ["MONO", "BI", "TRI"],
            ["1", "1", "1"],
            ["1", "1", "1"],
            ["220", "220", "220"],
            ["1", "7", "13"],
            ["Y", "Y", "Y"],
        )
        phases = load_phase_configuration()
        allocations = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(
                ("ID", "MT_CAR_ID"),
                [("EM", "M"), ("EB", "B"), ("ET", "T")],
                "BT_ET",
            ),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO"),
                [("EM", "1", "10"), ("EB", "7", "20"), ("ET", "13", "30")],
                "BT_CONS",
            ),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), "1", "1", "1") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model
        bundle = build_allocation_export(
            catalog,
            cables,
            phases,
            0,
            allocations,
            measurements,
            Curve("C", "C", (1.0,) * 24),
            default_calculation_levels(),
            OpenDssAllocationSettings(),
        )
        level = bundle.levels[0]
        self.assertEqual(level.load_count, 18)
        files = dict(level.files)
        self.assertEqual(files[ENERGY_LOADS_FILENAME].count("New Load."), 6)
        self.assertIn("Load.MONO-1F-D", files[ENERGY_LOADS_FILENAME])
        self.assertIn("Load.BI-2F-E", files[ENERGY_LOADS_FILENAME])
        self.assertIn("Load.TRI-3F-F", files[ENERGY_LOADS_FILENAME])
        self.assertNotIn("kW=-0", files[BT_GENERATION_FILENAME])
        self.assertIn("kW=0", files[BT_GENERATION_FILENAME])

    def test_biphase_bt_client_on_monophase_transformer_emits_only_three_objects(self):
        level = self.make_monophase_bundle().levels[0]
        files = dict(level.files)
        energy = files[ENERGY_LOADS_FILENAME]

        self.assertEqual(level.load_count, 3)
        self.assertEqual(energy.count("New Load."), 1)
        self.assertEqual(files[BT_GENERATION_FILENAME].count("New Load."), 1)
        self.assertEqual(files[MT_GENERATION_FILENAME].count("New Load."), 1)
        self.assertIn("Load.MONO-E-1F-E", energy)
        self.assertIn("bus1=6401_AUX.2", energy)
        self.assertIn("kWh=600", energy)
        self.assertNotIn("Load.MONO-E-1F-D", energy)
        self.assertNotIn("Load.MONO-E-1F-F", energy)

    def test_writes_four_independent_directories(self):
        bundle = self.make_bundle()
        with tempfile.TemporaryDirectory() as temp:
            paths = write_allocation_export(temp, bundle)
            self.assertEqual(len(paths), 4)
            for level, path in zip(bundle.levels, paths, strict=True):
                self.assertTrue(path.is_dir())
                self.assertTrue((path / level.master_filename).is_file())
                self.assertTrue((path / ENERGY_LOADS_FILENAME).is_file())

    def test_positive_current_without_energy_exports_with_detailed_warnings(self):
        _bars, catalog, loads, cables = make_models()
        phases = load_phase_configuration()
        allocations = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [], "BT_ET"),
            bt_consumers=table(("ET_ID", "FASES2", "CONSUMO"), [], "BT_CONS"),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), "1", "1", "1") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model
        bundle = build_allocation_export(
            catalog,
            cables,
            phases,
            0,
            allocations,
            measurements,
            Curve("C", "C", (1.0,) * 24),
            default_calculation_levels(),
            OpenDssAllocationSettings(),
        )

        self.assertEqual(len(bundle.levels), 4)
        self.assertTrue(bundle.has_warnings)
        self.assertEqual(len(bundle.warnings), 12)
        self.assertTrue(
            any(
                "NPAT 0, fase D" in warning and "PeakCurrent=1 A" in warning
                for warning in bundle.warnings
            )
        )

    def test_invalid_consumer_and_generator_are_ignored_without_removing_transformer(self):
        bars, catalog, _loads, cables = make_models()
        loads = LoadModel(
            bars,
            ["L1"],
            [2],
            [""],
            ["BI-DE"],
            ["1"],
            ["1"],
            ["220"],
            ["7"],
            ["Y"],
        )
        phases = load_phase_configuration()
        allocations = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [("733", "L1")], "BT_ET"),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO", "ID", "CODIGO"),
                [
                    ("733", "7", "600", "912", "VALIDO"),
                    ("733", "8", "0", "913", "396000"),
                ],
                "BT_CONS",
            ),
            bt_generators=table(
                ("ET_ID", "GERACAO_KWH"), [("733", "-1")], "BT_GD"
            ),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), "1", "1", "0") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model

        bundle = build_allocation_export(
            catalog,
            cables,
            phases,
            0,
            allocations,
            measurements,
            Curve("C", "C", (1.0,) * 24),
            default_calculation_levels(),
            OpenDssAllocationSettings(),
        )

        self.assertEqual(bundle.levels[0].transformer_count, 1)
        self.assertEqual(bundle.skipped_transformer_count, 0)
        warnings = "\n".join(bundle.warnings)
        self.assertIn("ID=913", warnings)
        self.assertIn("CODIGO=396000", warnings)
        self.assertIn("ET_ID=733", warnings)
        self.assertIn("GERACAO_KWH deve ser numérico e não negativo", warnings)
        files = dict(bundle.levels[0].files)
        energy = files[ENERGY_LOADS_FILENAME]
        self.assertIn("Load.BI-DE-2F-D", energy)
        self.assertIn("Load.BI-DE-2F-E", energy)
        self.assertEqual(energy.count("kWh=300"), 2)
        self.assertIn("kW=0", files[BT_GENERATION_FILENAME])

    def test_invalid_transformer_is_skipped_while_valid_transformer_exports(self):
        bars, catalog, _loads, cables = make_models()
        loads = LoadModel(
            bars,
            ["VALID", "INVALID"],
            [2, 2],
            ["", ""],
            ["VALIDO", "INVALIDO"],
            ["1", "1"],
            ["1", "1"],
            ["220", "220"],
            ["1", "404"],
            ["Y", "Y"],
        )
        phases = load_phase_configuration()
        allocations = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [("ET1", "VALID")], "BT_ET"),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO"), [("ET1", "1", "600")], "BT_CONS"
            ),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), "1", "0", "0") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model

        bundle = build_allocation_export(
            catalog,
            cables,
            phases,
            0,
            allocations,
            measurements,
            Curve("C", "C", (1.0,) * 24),
            default_calculation_levels(),
            OpenDssAllocationSettings(),
        )

        self.assertEqual(bundle.levels[0].transformer_count, 1)
        self.assertEqual(bundle.skipped_transformer_count, 1)
        self.assertTrue(any("CARGA_ID=INVALID" in item for item in bundle.warnings))
        energy = dict(bundle.levels[0].files)[ENERGY_LOADS_FILENAME]
        self.assertIn("Load.VALIDO-1F-D", energy)
        self.assertNotIn("INVALIDO", energy)

    def test_all_invalid_transformers_still_block_export_with_report(self):
        bars, catalog, _loads, cables = make_models()
        loads = LoadModel(
            bars,
            ["INVALID"],
            [2],
            [""],
            ["INVALIDO"],
            ["1"],
            ["1"],
            ["220"],
            ["404"],
            ["Y"],
        )
        phases = load_phase_configuration()
        allocations = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(("ID", "MT_CAR_ID"), [], "BT_ET"),
            bt_consumers=table(("ET_ID", "FASES2", "CONSUMO"), [], "BT_CONS"),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), "0", "0", "0") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model

        with self.assertRaisesRegex(
            OpenDssAllocationExportError,
            "Nenhum transformador válido",
        ) as raised:
            build_allocation_export(
                catalog,
                cables,
                phases,
                0,
                allocations,
                measurements,
                Curve("C", "C", (1.0,) * 24),
                default_calculation_levels(),
                OpenDssAllocationSettings(),
            )
        self.assertIn("CARGA_ID=INVALID", str(raised.exception))

    def test_all_transformers_in_a_load_name_collision_are_skipped(self):
        bars, catalog, _loads, cables = make_models()
        loads = LoadModel(
            bars,
            ["A", "B", "C"],
            [2, 2, 2],
            ["", "", ""],
            ["DUP", "DUP", "VALIDO"],
            ["1", "1", "1"],
            ["1", "1", "1"],
            ["220", "220", "220"],
            ["1", "1", "1"],
            ["Y", "Y", "Y"],
        )
        phases = load_phase_configuration()
        allocations = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(
                ("ID", "MT_CAR_ID"), [("EA", "A"), ("EB", "B"), ("EC", "C")], "BT_ET"
            ),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO"),
                [("EA", "1", "100"), ("EB", "1", "200"), ("EC", "1", "300")],
                "BT_CONS",
            ),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), "1", "0", "0") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model

        bundle = build_allocation_export(
            catalog,
            cables,
            phases,
            0,
            allocations,
            measurements,
            Curve("C", "C", (1.0,) * 24),
            default_calculation_levels(),
            OpenDssAllocationSettings(),
        )

        self.assertEqual(bundle.skipped_transformer_count, 2)
        self.assertEqual(bundle.levels[0].transformer_count, 1)
        warnings = "\n".join(bundle.warnings)
        self.assertIn("CARGA_ID=A", warnings)
        self.assertIn("CARGA_ID=B", warnings)
        energy = dict(bundle.levels[0].files)[ENERGY_LOADS_FILENAME]
        self.assertIn("Load.VALIDO-1F-D", energy)
        self.assertNotIn("Load.DUP-1F-D", energy)

    def test_transformer_on_shared_bar_is_skipped_without_blocking_valid_one(self):
        bars, original_catalog, _loads, cables = make_models()

        def indices(values):  # noqa: ANN001, ANN202
            result = np.asarray(values, dtype=np.intp)
            result.setflags(write=False)
            return result

        catalog = CircuitCatalogModel(
            original_catalog.segments,
            None,
            (
                CircuitDefinition("C1", "B0", "ALIM-01", "34,5"),
                CircuitDefinition("C2", "B2", "ALIM-02", "34,5"),
            ),
            (
                CircuitMembership(
                    indices([0, 1, 2]),
                    indices([0, 1]),
                    indices([]),
                    indices([0, 1]),
                ),
                CircuitMembership(
                    indices([2]),
                    indices([]),
                    indices([]),
                    indices([]),
                ),
            ),
        )
        loads = LoadModel(
            bars,
            ["VALID", "SHARED"],
            [1, 2],
            ["", ""],
            ["VALIDO", "COMPARTILHADO"],
            ["1", "1"],
            ["1", "1"],
            ["220", "220"],
            ["1", "1"],
            ["Y", "Y"],
        )
        phases = load_phase_configuration()
        allocations = build_transformer_allocations(
            loads,
            phases,
            bt_et=table(
                ("ID", "MT_CAR_ID"),
                [("EV", "VALID"), ("ES", "SHARED")],
                "BT_ET",
            ),
            bt_consumers=table(
                ("ET_ID", "FASES2", "CONSUMO"),
                [("EV", "1", "300"), ("ES", "1", "300")],
                "BT_CONS",
            ),
            bt_generators=table(("ET_ID", "GERACAO_KWH"), [], "BT_GD"),
            mt_consumers=table(
                ("ID", "CARGA_ID", "FASES2", "CONSUMO"), [], "MT_CONS"
            ),
            mt_generators=table(("MT_CONS_ID", "GERACAO_KWH"), [], "MT_GD"),
        )
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [
                *(("ALIM-01", str(npat), "1", "0", "0") for npat in range(4)),
                *(("ALIM-02", str(npat), "0", "0", "0") for npat in range(4)),
            ],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model

        bundle = build_allocation_export(
            catalog,
            cables,
            phases,
            0,
            allocations,
            measurements,
            Curve("C", "C", (1.0,) * 24),
            default_calculation_levels(),
            OpenDssAllocationSettings(),
        )

        self.assertEqual(bundle.levels[0].transformer_count, 1)
        self.assertEqual(bundle.skipped_transformer_count, 1)
        self.assertTrue(
            any(
                "CARGA_ID=SHARED" in warning
                and "múltiplos circuitos" in warning
                for warning in bundle.warnings
            )
        )

    def test_synthetic_head_name_collision_blocks_export(self):
        bars, _catalog, loads, cables = make_models()
        lines = LineNetworkModel(
            bars,
            ["S0", "S1"],
            ["ALLOC-HEAD-ALIM-01_NPAT0", "L1"],
            ["13", "13"],
            [0, 1],
            [1, 2],
            ["", ""],
            ["CB1", "CB1"],
            ["", ""],
            [100.0, 100.0],
        )
        catalog = CircuitCatalogModel.build(
            lines,
            None,
            [CircuitDefinition("C1", "B0", "ALIM-01", "34,5")],
        )
        phases, allocations = make_allocations(loads)
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM-01", str(npat), "1", "1", "1") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model

        with self.assertRaisesRegex(OpenDssAllocationExportError, "sintética"):
            build_allocation_export(
                catalog,
                cables,
                phases,
                0,
                allocations,
                measurements,
                Curve("C", "C", (1.0,) * 24),
                default_calculation_levels(),
                OpenDssAllocationSettings(),
            )

    @unittest.skipUnless(power_flow_available(), "DLL OpenDSS não disponível")
    def test_real_engine_changes_energy_cfactor_but_keeps_fixed_generation_kw(self):
        bundle = self.make_bundle()
        with ascii_workspace() as workspace:
            path = write_allocation_export(workspace, bundle)[0]
            master = path / bundle.levels[0].master_filename
            with acquire_engine() as engine:
                engine.text(f"Compile [{master}]")
                self.assertTrue(engine.solution.converged)
                values: dict[str, tuple[float, float]] = {}
                cursor = engine.loads.first()
                while cursor:
                    values[engine.loads.name.casefold()] = (
                        float(engine.loads.kw),
                        float(engine.loads.c_factor),
                    )
                    cursor = engine.loads.next()

        energy_name = "57169589ME-3F-D".casefold()
        bt_name = "GD-BT-57169589ME-3F-D".casefold()
        mt_name = "GD-MT-57169589ME-3F-D".casefold()
        self.assertNotAlmostEqual(values[energy_name][1], 4.0)
        self.assertAlmostEqual(values[bt_name][0], -0.666667, places=5)
        self.assertAlmostEqual(values[mt_name][0], -1.33333, places=5)

    @unittest.skipUnless(power_flow_available(), "DLL OpenDSS não disponível")
    def test_real_engine_compiles_monophase_transformer_with_biphase_bt_client(self):
        bundle = self.make_monophase_bundle()
        with ascii_workspace() as workspace:
            path = write_allocation_export(workspace, bundle)[0]
            master = path / bundle.levels[0].master_filename
            with acquire_engine() as engine:
                engine.text(f"Compile [{master}]")
                self.assertTrue(engine.solution.converged)
                names: set[str] = set()
                cursor = engine.loads.first()
                while cursor:
                    names.add(engine.loads.name.casefold())
                    cursor = engine.loads.next()

        self.assertEqual(
            names,
            {
                "mono-e-1f-e",
                "gd-bt-mono-e-1f-e",
                "gd-mt-mono-e-1f-e",
            },
        )


if __name__ == "__main__":
    unittest.main()
