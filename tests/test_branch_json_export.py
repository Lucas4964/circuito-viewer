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
    build_equivalent_network,
    export_branches_json,
    suggested_branch_json_filename,
)
from circuit_viewer.branch_analysis import analyze_branches
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
                    "barras": ["CB3", "CB4"],
                    "trechos": ["CT2"],
                    "cargas": ["CARGA-1", "CARGA-2"],
                    "geradores": ["GERAÇÃO-1"],
                    "chaves": ["CHAVE-Á"],
                    "fase": "D",
                    "remanejavel": True,
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
