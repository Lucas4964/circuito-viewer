from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from circuit_viewer import (
    BranchJsonValidationError,
    build_branch_json_payload,
    EquivalentLoadPatternRecord,
    build_equivalent_network,
    export_branches_json,
    suggested_branch_json_filename,
)
from circuit_viewer.branch_analysis import analyze_branches
from circuit_viewer.branch_json_export import BRANCH_POWER_FIELDS
from circuit_viewer.branch_power_source import BranchPowerSource
from circuit_viewer.calculation_levels import default_calculation_levels
from circuit_viewer.curvas import Curve
from circuit_viewer.generator_update import (
    GeneratorScheduleMode,
    calculate_generator_demands,
)
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("d", "D", 1),
        PhaseMappingEntry("def", "DEF", 3),
    )
)


def make_snapshot(
    *,
    empty_load_code: bool = False,
    empty_switch_segment_code: bool = False,
    empty_switch_code: bool = False,
):
    bars = CircuitModel(
        ["B0", "B1", "B2", "B3", "B4"],
        ["CB0", "CB1", "CB2", "CB3", "CB4"],
        [0.0, 10.0, 20.0, 10.0, 10.0],
        [0.0, 0.0, 0.0, -10.0, -20.0],
        UtmCrs(21, northern=False),
    )
    segments = LineNetworkModel(
        bars,
        ["T0", "T1", "T2", "T3"],
        ["CT0", "CT1", "CT2", "" if empty_switch_segment_code else "CT3"],
        ["DEF", "DEF", "D", "D"],
        [0, 1, 1, 3],
        [1, 2, 3, 4],
        [""] * 4,
        [""] * 4,
        [""] * 4,
        [10.0] * 4,
    )
    switches = SwitchModel(
        segments,
        ["CH1"],
        ["TIPO"],
        ["C1"],
        [3],
        ["" if empty_switch_code else "CHAVE-Á"],
        ["1"],
        ["1"],
        [""],
        [""],
        [""],
    )
    catalog = CircuitCatalogModel.build(
        segments,
        switches,
        [CircuitDefinition("C1", "B0", "", "")],
    )
    loads = LoadModel(
        bars,
        ["L1", "L2"],
        [3, 4],
        ["", ""],
        ["CARGA-1", "" if empty_load_code else "CARGA-2"],
        ["1", "2"],
        ["1", "2"],
        ["", ""],
        ["d", "d"],
        ["", ""],
    )

    def group(load_id: str, values: tuple[str, str, str, str]):
        return tuple(
            LoadPatternRecord(
                load_id,
                npat,
                value,
                "0",
                "0",
                "9999",
                "9999",
                "9999",
            )
            for npat, value in enumerate(values)
        )

    patterns = LoadPatternModel(
        loads,
        [
            group("L1", ("1", "2", "3", "4")),
            group("L2", ("10", "20", "30", "40")),
        ],
    )
    generators = GeneratorModel(
        loads,
        ["G1"],
        [0],
        ["MC1"],
        ["GERAÇÃO-1"],
        ["13.8"],
        ["75"],
        ["Y"],
        ["CURVA"],
        ["720"],
        ["CONS-1"],
        ["CONS-1"],
        [""],
        ["Gerador"],
        ["d"],
    )
    updates = calculate_generator_demands(
        generators,
        catalog,
        PHASES,
        Curve("C", "Constante", (1.0,) * 24),
        (default_calculation_levels(),),
        (GeneratorScheduleMode.DEFAULT,),
    ).model
    branches = analyze_branches(catalog, PHASES, loads)
    equivalent = build_equivalent_network(branches, loads, patterns, updates)
    return branches, equivalent


def make_linear_branch_snapshot(
    *,
    branch_segment_count: int,
    switch_at: int | None,
    switch_code: str = "CHAVE-1",
):
    """Tronco ``DEF`` B0-B1-B2 com um ramal ``D`` linear pendurado em B1.

    ``switch_at`` é a posição (1-based, igual à distância em saltos do tronco)
    do trecho do ramal que recebe a chave, ou ``None`` para um ramal sem chave.
    """

    bar_count = branch_segment_count + 3
    bars = CircuitModel(
        [f"B{index}" for index in range(bar_count)],
        [f"CB{index}" for index in range(bar_count)],
        [float(index) for index in range(bar_count)],
        [0.0] * bar_count,
        UtmCrs(21, northern=False),
    )
    starts = [0, 1]
    ends = [1, 2]
    for position in range(1, branch_segment_count + 1):
        starts.append(1 if position == 1 else position + 1)
        ends.append(position + 2)
    segment_count = len(starts)
    segments = LineNetworkModel(
        bars,
        [f"T{index}" for index in range(segment_count)],
        [f"CT{index}" for index in range(segment_count)],
        ["DEF", "DEF", *["D"] * branch_segment_count],
        starts,
        ends,
        [""] * segment_count,
        [""] * segment_count,
        [""] * segment_count,
        [10.0] * segment_count,
    )
    switches = (
        None
        if switch_at is None
        else SwitchModel(
            segments,
            ["CH1"],
            ["TIPO"],
            ["C1"],
            [switch_at + 1],
            [switch_code],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
    )
    catalog = CircuitCatalogModel.build(
        segments,
        switches,
        [CircuitDefinition("C1", "B0", "", "")],
    )
    loads = LoadModel(
        bars,
        ["L1"],
        [bar_count - 1],
        [""],
        ["CARGA-1"],
        ["1"],
        ["1"],
        [""],
        ["d"],
        [""],
    )
    branches = analyze_branches(catalog, PHASES, loads)
    equivalent = build_equivalent_network(branches, loads)
    return branches, equivalent


class BranchJsonExportTests(unittest.TestCase):
    def test_payload_uses_only_cached_indices_and_codes(self) -> None:
        branches, equivalent = make_snapshot()

        payload = build_branch_json_payload(branches, equivalent, (0,))

        self.assertEqual(
            payload,
            {
                "ramais_interesse": [],
                "RAMAL-1": {
                    "barra_inicio": "CB1",
                    "nivel_topologico": 1,
                    "barras": ["CB3", "CB4"],
                    "trechos": ["CT2"],
                    "trecho_ini": "CT2",
                    "cargas": ["CARGA-1", "CARGA-2"],
                    "geradores": ["GERAÇÃO-1"],
                    "chaves": ["CHAVE-Á"],
                    "chave_ini": "CHAVE-Á",
                    "fase": "D",
                    "remanejavel": True,
                    "P0": 10.0,
                    "P1": 21.0,
                    "P2": 32.0,
                    "P3": 43.0,
                    "Q0": 19998.0,
                    "Q1": 19998.0,
                    "Q2": 19998.0,
                    "Q3": 19998.0,
                }
            },
        )
        branch = branches.records[0]
        self.assertEqual(tuple(branch.switch_indices), (0,))
        self.assertFalse(branch.switch_indices.flags.writeable)
        switch_segment_code = branches.source_catalog.segments.codes[3]
        self.assertNotIn(switch_segment_code, payload["RAMAL-1"]["trechos"])
        self.assertIn("CHAVE-Á", payload["RAMAL-1"]["chaves"])
        self.assertEqual(
            equivalent.model.record(0).maximum_active_demand,
            Decimal("43"),
        )

    def test_every_branch_carries_the_eight_power_fields(self) -> None:
        # A estrutura não pode depender do método nem do estado do cadastro:
        # quem consome não deveria testar a presença de cada chave.
        branches, equivalent = make_snapshot()

        payload = build_branch_json_payload(branches, equivalent, (0,))

        for name in BRANCH_POWER_FIELDS:
            with self.subTest(field=name):
                self.assertIn(name, payload["RAMAL-1"])

    def test_the_power_comes_from_the_selected_method(self) -> None:
        """A exigência central: mesmas chaves, valores da origem escolhida."""

        branches, aggregated = make_snapshot()
        branch_id = branches.records[0].branch_id
        # A mesma análise, agora com a potência medida na cabeceira em vez de
        # agregada das cargas. Só a origem muda.
        measured = build_equivalent_network(
            branches,
            branches.source_loads,
            power_source=BranchPowerSource.POWER_FLOW,
            measured_patterns={
                branch_id: tuple(
                    EquivalentLoadPatternRecord(
                        f"RAMAL-{branch_id}",
                        npat,
                        Decimal(f"{100 + npat}"),
                        Decimal(0),
                        Decimal(0),
                        Decimal(f"{10 + npat}"),
                        Decimal(0),
                        Decimal(0),
                    )
                    for npat in range(4)
                )
            },
        )

        from_table = build_branch_json_payload(branches, aggregated, (0,))
        from_flow = build_branch_json_payload(branches, measured, (0,))

        self.assertEqual(
            set(from_table["RAMAL-1"]), set(from_flow["RAMAL-1"])
        )
        # A medição preenche a coluna D, que é a fase deste ramal.
        self.assertEqual(from_flow["RAMAL-1"]["fase"], "D")
        self.assertEqual(from_flow["RAMAL-1"]["P0"], 100.0)
        self.assertEqual(from_flow["RAMAL-1"]["P3"], 103.0)
        self.assertEqual(from_flow["RAMAL-1"]["Q0"], 10.0)
        self.assertNotEqual(
            from_table["RAMAL-1"]["P0"], from_flow["RAMAL-1"]["P0"]
        )

    def test_only_the_column_of_the_branch_phase_is_read(self) -> None:
        # A agregação soma as seis colunas das cargas sem filtrar por fase, então
        # um cadastro pode pôr potência numa fase que não é a do ramal. Somar as
        # três faria "P0 é a potência deste ramal na sua fase" deixar de valer.
        branches, equivalent = make_snapshot()
        record = equivalent.model.records_for_load(0)[0]
        self.assertEqual(branches.records[0].phase, "D")
        # O fixture tem reativo nas três colunas, então a soma difere da coluna
        # da fase — é exatamente o caso que distingue as duas leituras.
        self.assertNotEqual(record.qe, 0)

        payload = build_branch_json_payload(branches, equivalent, (0,))

        self.assertEqual(payload["RAMAL-1"]["Q0"], float(record.qd))
        self.assertNotEqual(
            payload["RAMAL-1"]["Q0"], float(record.qd + record.qe + record.qf)
        )

    def test_a_branch_without_the_four_patamares_reports_no_power(self) -> None:
        # None, e não zero: não ter resposta não é ter potência nula.
        branches, _ = make_snapshot()
        # Sem tabela de patamares a agregação não produz os quatro NPAT.
        incomplete = build_equivalent_network(branches, branches.source_loads)
        self.assertEqual(
            incomplete.model.records_for_load(0),
            (),
            "o fixture precisa de um ramal sem patamares para este caso",
        )

        payload = build_branch_json_payload(branches, incomplete, (0,))

        for name in BRANCH_POWER_FIELDS:
            with self.subTest(field=name):
                self.assertIsNone(payload["RAMAL-1"][name])

    def test_chave_ini_ignores_removable_gate(self) -> None:
        """``chave_ini`` é sempre a primeira chave, mesmo em ramal não remanejável."""

        branches, equivalent = make_linear_branch_snapshot(
            branch_segment_count=6,
            switch_at=6,
            switch_code="CHAVE-LONGE",
        )
        record = branches.records[0]
        self.assertEqual(record.first_switch_position, 6)
        self.assertFalse(record.removable)

        payload = build_branch_json_payload(branches, equivalent, (0,))["RAMAL-1"]

        self.assertEqual(payload["chave_ini"], "CHAVE-LONGE")
        self.assertEqual(payload["chaves"], ["CHAVE-LONGE"])
        self.assertFalse(payload["remanejavel"])
        self.assertEqual(payload["nivel_topologico"], 1)
        self.assertEqual(payload["trecho_ini"], "CT2")

    def test_first_codes_are_empty_without_switch(self) -> None:
        branches, equivalent = make_linear_branch_snapshot(
            branch_segment_count=2,
            switch_at=None,
        )

        payload = build_branch_json_payload(branches, equivalent, (0,))["RAMAL-1"]

        self.assertEqual(payload["chaves"], [])
        self.assertEqual(payload["chave_ini"], "")
        self.assertEqual(payload["trecho_ini"], "CT2")
        self.assertEqual(payload["trechos"][0], "CT2")

    def test_trecho_ini_is_the_topological_first_of_trechos(self) -> None:
        """O trecho com chave é pulado, igual ao filtro da lista ``trechos``."""

        branches, equivalent = make_linear_branch_snapshot(
            branch_segment_count=3,
            switch_at=1,
        )

        payload = build_branch_json_payload(branches, equivalent, (0,))["RAMAL-1"]

        self.assertNotIn("CT2", payload["trechos"])
        self.assertEqual(payload["trecho_ini"], "CT3")
        self.assertEqual(payload["chave_ini"], "CHAVE-1")

    def test_interest_ids_are_first_sorted_and_limited_to_exported_branches(self) -> None:
        branches, equivalent = make_snapshot()

        payload = build_branch_json_payload(
            branches,
            equivalent,
            (0,),
            interest_branch_ids=(1,),
        )

        self.assertEqual(list(payload), ["ramais_interesse", "RAMAL-1"])
        self.assertEqual(payload["ramais_interesse"], [1])
        with self.assertRaisesRegex(ValueError, "duplicados"):
            build_branch_json_payload(
                branches,
                equivalent,
                (0,),
                interest_branch_ids=(1, 1),
            )
        with self.assertRaisesRegex(ValueError, "pertencer"):
            build_branch_json_payload(
                branches,
                equivalent,
                (0,),
                interest_branch_ids=(2,),
            )

    def test_empty_code_blocks_export_and_preserves_existing_file(self) -> None:
        branches, equivalent = make_snapshot(empty_load_code=True)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ramais.json"
            target.write_text("anterior", encoding="utf-8")

            with self.assertRaises(BranchJsonValidationError) as raised:
                export_branches_json(target, branches, equivalent, (0,))

            self.assertIn("carga L2 sem CODIGO", "\n".join(raised.exception.issues))
            self.assertEqual(target.read_text(encoding="utf-8"), "anterior")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_switch_segment_code_is_irrelevant_but_switch_code_is_required(self) -> None:
        branches, equivalent = make_snapshot(empty_switch_segment_code=True)

        payload = build_branch_json_payload(branches, equivalent, (0,))

        self.assertEqual(payload["RAMAL-1"]["trechos"], ["CT2"])
        self.assertEqual(payload["RAMAL-1"]["chaves"], ["CHAVE-Á"])

        branches, equivalent = make_snapshot(empty_switch_code=True)
        with self.assertRaises(BranchJsonValidationError) as raised:
            build_branch_json_payload(branches, equivalent, (0,))
        self.assertIn("chave CH1 sem CODIGO", "\n".join(raised.exception.issues))

    def test_atomic_utf8_round_trip_and_no_temporary_file(self) -> None:
        branches, equivalent = make_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ramais.json"

            result = export_branches_json(
                target,
                branches,
                equivalent,
                (0,),
                interest_branch_ids=(1,),
            )

            self.assertEqual(result.path, target)
            self.assertEqual(result.branch_count, 1)
            self.assertEqual(result.circuit_ids, ("C1",))
            text = target.read_text(encoding="utf-8")
            self.assertIn("GERAÇÃO-1", text)
            self.assertEqual(list(json.loads(text))[0], "ramais_interesse")
            self.assertEqual(json.loads(text)["ramais_interesse"], [1])
            self.assertEqual(json.loads(text)["RAMAL-1"]["chaves"], ["CHAVE-Á"])
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_cancellation_before_replace_preserves_existing_file(self) -> None:
        branches, equivalent = make_snapshot()
        cancelled = False

        def progress(_current: int, _total: int) -> None:
            nonlocal cancelled
            cancelled = True

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ramais.json"
            target.write_text("anterior", encoding="utf-8")

            with self.assertRaises(InterruptedError):
                export_branches_json(
                    target,
                    branches,
                    equivalent,
                    (0,),
                    cancel_check=lambda: cancelled,
                    progress=progress,
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "anterior")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_replace_failure_preserves_existing_file_and_removes_temporary(self) -> None:
        branches, equivalent = make_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ramais.json"
            target.write_text("anterior", encoding="utf-8")

            with patch(
                "circuit_viewer.branch_json_export.os.replace",
                side_effect=OSError("falha simulada"),
            ), self.assertRaisesRegex(OSError, "falha simulada"):
                export_branches_json(target, branches, equivalent, (0,))

            self.assertEqual(target.read_text(encoding="utf-8"), "anterior")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_suggested_names_cover_specific_and_all_circuits(self) -> None:
        self.assertEqual(suggested_branch_json_filename(None), "ramais_todos.json")
        self.assertEqual(
            suggested_branch_json_filename("Circuito 3/MT"),
            "ramais_Circuito_3_MT.json",
        )


if __name__ == "__main__":
    unittest.main()
