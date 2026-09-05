from __future__ import annotations

import re
import unittest

from circuit_viewer.branch_analysis import analyze_branches
from circuit_viewer.branch_power_flow import measure_branch_powers
from circuit_viewer.branch_power_source import BranchPowerSource
from circuit_viewer.calculation_levels import default_calculation_levels
from circuit_viewer.curvas import Curve
from circuit_viewer.equivalent_network import build_equivalent_network
from circuit_viewer.generator_update import (
    GeneratorScheduleMode,
    calculate_generator_demands,
)
from circuit_viewer.model import (
    CableModel,
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
    UtmCrs,
)
from circuit_viewer.opendss_powerflow import PowerFlowResult, SegmentPowers
from circuit_viewer.branch_json_export import build_branch_json_payload
from circuit_viewer.opendss_simplified_export import (
    SINGLE_PHASE_BRANCHES_FILENAME,
    TWO_PHASE_BRANCHES_FILENAME,
    SimplifiedOpenDssExportError,
    build_simplified_export,
    simplified_export_directory_name,
)
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("d", "D", 1, "1"),
        PhaseMappingEntry("e", "E", 1, "2"),
        PhaseMappingEntry("f", "F", 1, "3"),
        PhaseMappingEntry("def", "DEF", 3, "1.2.3"),
    )
)


def make_cables() -> CableModel:
    return CableModel(
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
        ["ALUMINIO"],
        ["EXT"],
    )


def active_group(load_id: str, value: str):
    return tuple(
        LoadPatternRecord(load_id, npat, value, "0", "0", "0", "0", "0")
        for npat in range(4)
    )


def make_system(
    *,
    branch_consumption: str = "2",
    outside_code: str = "CARGA-EXTERNA",
    incomplete_branch: bool = False,
):
    bars = CircuitModel(
        ["B0", "B1", "B2", "B3", "B4"],
        ["BAR-0", "BAR-1", "BAR-2", "BAR-3", "BAR-4"],
        [0.0, 10.0, 20.0, 10.0, 10.0],
        [0.0, 0.0, 0.0, -10.0, -20.0],
        UtmCrs(21, False),
    )
    segments = LineNetworkModel(
        bars,
        ["T0", "T1", "T2", "T3"],
        ["TR-0", "TR-1", "TR-2", "TR-3"],
        ["def", "def", "d", "d"],
        [0, 1, 1, 3],
        [1, 2, 3, 4],
        [""] * 4,
        ["CB1"] * 4,
        [""] * 4,
        [10.0] * 4,
    )
    catalog = CircuitCatalogModel.build(
        segments,
        None,
        [CircuitDefinition("C1", "B0", "ALIM-1", "13.8")],
    )
    loads = LoadModel(
        bars,
        ["L0", "L1", "L2"],
        [2, 3, 4],
        [""] * 3,
        [outside_code, "CARGA-RAMAL", "BASE-GERADOR"],
        [""] * 3,
        [""] * 3,
        [""] * 3,
        ["d"] * 3,
        [""] * 3,
    )
    patterns = LoadPatternModel(
        loads,
        (
            active_group("L0", "5"),
            active_group("L1", branch_consumption),
            None if incomplete_branch else active_group("L2", "0"),
        ),
    )
    generators = GeneratorModel(
        loads,
        ["G1"],
        [2],
        ["MC1"],
        ["GEN-RAMAL"],
        ["13.8"],
        ["75"],
        ["Y"],
        ["CURVA"],
        ["720"],
        ["CONS-1"],
        ["GEN-RAMAL"],
        [""],
        ["Gerador do ramal"],
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
    return catalog, loads, patterns, updates, equivalent


def build_system_export(**kwargs):
    catalog, loads, patterns, updates, equivalent = make_system(**kwargs)
    result = build_simplified_export(
        catalog,
        make_cables(),
        PHASES,
        (0,),
        equivalent=equivalent,
        loads=loads,
        patterns=patterns,
        generator_updates=updates,
    )
    return catalog, equivalent, result


def build_measured_system_export(active: float, **kwargs):
    """Refaz o mesmo sistema com a potência do ramal medida no primeiro trecho."""

    catalog, loads, patterns, updates, aggregated = make_system(**kwargs)
    branches = aggregated.model.branches
    flow = PowerFlowResult(
        catalog=catalog,
        cables=None,
        phase_configuration=PHASES,
        loads=loads,
        patterns=patterns,
        step_count=4,
        segment_powers={
            branches.records[0].first_segment_index: SegmentPowers(
                nodes=(1,),
                active=((active,),) * 4,
                reactive=((0.0,),) * 4,
            )
        },
        solved_circuits=("C1",),
    )
    measured, failures, currents = measure_branch_powers(branches, flow)
    equivalent = build_equivalent_network(
        branches,
        loads,
        patterns,
        updates,
        power_source=BranchPowerSource.POWER_FLOW,
        measured_patterns=measured,
        measurement_issues=failures,
        source_power_flow=flow,
    )
    result = build_simplified_export(
        catalog,
        make_cables(),
        PHASES,
        (0,),
        equivalent=equivalent,
        loads=loads,
        patterns=patterns,
        generator_updates=updates,
    )
    return catalog, equivalent, result


class SimplifiedOpenDssExportTests(unittest.TestCase):
    def test_exports_retained_trunk_external_sources_and_net_branch(self) -> None:
        catalog, _, result = build_system_export()

        self.assertEqual(result.lines.exported_count, 2)
        self.assertIn("TR-0", result.lines.text)
        self.assertIn("TR-1", result.lines.text)
        self.assertNotIn("TR-2", result.lines.text)
        self.assertNotIn("TR-3", result.lines.text)
        self.assertEqual(result.single_phase_loads.exported_count, 1)
        self.assertIn("CARGA-EXTERNA", result.single_phase_loads.text)
        self.assertNotIn("CARGA-RAMAL", result.single_phase_loads.text)
        self.assertEqual(result.single_phase_generators.exported_count, 0)
        self.assertNotIn("GEN-RAMAL", result.single_phase_generators.text)
        self.assertEqual(result.single_phase_branches.exported_count, 1)
        self.assertIn(
            "mult=[1.000000 1.000000 1.000000 1.000000]",
            result.single_phase_branches.text,
        )
        self.assertIn("class=1", result.single_phase_branches.text)
        filenames = [name for name, _ in result.files]
        self.assertIn(SINGLE_PHASE_BRANCHES_FILENAME, filenames)
        self.assertIn(TWO_PHASE_BRANCHES_FILENAME, filenames)
        redirects = [
            line.removeprefix("Redirect ")
            for line in result.master.text.splitlines()
            if line.startswith("Redirect ")
        ]
        self.assertEqual(redirects, [name for name, _ in result.element_files])
        self.assertIn("BAR-0", result.master.buscoords_text)
        self.assertIn("BAR-2", result.master.buscoords_text)
        self.assertNotIn("BAR-3", result.master.buscoords_text)
        self.assertNotIn("BAR-4", result.master.buscoords_text)
        self.assertEqual(
            simplified_export_directory_name(catalog, 0),
            "ALIM-1_Rede_Simplificada",
        )

    def test_generator_only_net_is_exported_with_negative_sign(self) -> None:
        _, _, result = build_system_export(branch_consumption="0")

        self.assertIn(
            "mult=[-1.000000 -1.000000 -1.000000 -1.000000]",
            result.single_phase_branches.text,
        )

    def test_zero_net_branch_is_collapsed_but_not_emitted(self) -> None:
        _, equivalent, result = build_system_export(branch_consumption="1")

        self.assertTrue(equivalent.model.record(0).is_zero)
        self.assertEqual(result.single_phase_branches.exported_count, 0)
        self.assertEqual(result.single_phase_branches.zero_count, 1)
        self.assertNotIn("New Load.", result.single_phase_branches.text)

    def test_load_without_a_table_is_zeroed_and_no_longer_blocks(self) -> None:
        # L2 não está em MODELO_CARGA. Antes isso deixava o ramal incompleto e
        # bloqueava a exportação inteira; agora ela vale zero e o ramal sai.
        catalog, loads, patterns, updates, equivalent = make_system(
            incomplete_branch=True
        )

        self.assertTrue(equivalent.model.record(0).electrical_complete)
        result = build_simplified_export(
            catalog,
            make_cables(),
            PHASES,
            (0,),
            equivalent=equivalent,
            loads=loads,
            patterns=patterns,
            generator_updates=updates,
        )

        # L2 é a base do gerador do ramal e valia zero de consumo; o líquido do
        # ramal continua sendo o de L1 menos a geração.
        self.assertEqual(result.single_phase_branches.exported_count, 1)
        self.assertEqual(result.single_phase_branches.discarded_count, 0)

    def test_patterns_not_imported_at_all_still_block(self) -> None:
        # Tabela inteira ausente não é "carga sem tabela": o ramal fica
        # incompleto na agregação e a exportação é recusada — aqui pela guarda
        # de entrada, que cobre o caso antes de chegar aos ramais.
        catalog, loads, _patterns, updates, _equivalent = make_system()
        branches = analyze_branches(catalog, PHASES, loads)
        equivalent = build_equivalent_network(branches, loads, None, updates)

        self.assertFalse(equivalent.model.record(0).electrical_complete)
        with self.assertRaisesRegex(
            SimplifiedOpenDssExportError,
            "Importe os quatro patamares",
        ):
            build_simplified_export(
                catalog,
                make_cables(),
                PHASES,
                (0,),
                equivalent=equivalent,
                loads=loads,
                generator_updates=updates,
            )

    def test_measured_branch_exports_even_without_load_tables(self) -> None:
        # Exatamente o sistema que o teste anterior mostra bloqueado: L2 não tem
        # patamares. Medindo no primeiro elemento do ramal, a exportação sai.
        _, equivalent, result = build_measured_system_export(
            4.0,
            incomplete_branch=True,
        )

        self.assertTrue(equivalent.model.record(0).electrical_complete)
        self.assertEqual(result.single_phase_branches.exported_count, 1)
        self.assertIn(
            "mult=[4.000000 4.000000 4.000000 4.000000]",
            result.single_phase_branches.text,
        )
        self.assertIn(
            "qmult=[0.000000 0.000000 0.000000 0.000000]",
            result.single_phase_branches.text,
        )
        # A redução da rede não muda com o método: o ramal continua ausente das
        # linhas e das cargas de consumo.
        self.assertNotIn("TR-2", result.lines.text)
        self.assertNotIn("CARGA-RAMAL", result.single_phase_loads.text)

    def test_shared_load_namespace_collision_blocks_branch_export(self) -> None:
        catalog, loads, patterns, updates, equivalent = make_system(
            outside_code="RAMAL-1"
        )

        with self.assertRaisesRegex(SimplifiedOpenDssExportError, "já utilizado"):
            build_simplified_export(
                catalog,
                make_cables(),
                PHASES,
                (0,),
                equivalent=equivalent,
                loads=loads,
                patterns=patterns,
                generator_updates=updates,
            )



class JsonMatchesLoadShapeTests(unittest.TestCase):
    """O P0..Q3 do JSON e o LoadShape do ramal têm de dizer a mesma coisa.

    São duas saídas do mesmo dado, e um consumidor que compare as duas tem de
    encontrar os mesmos números. Elas só coincidem porque ambas leem a coluna da
    fase do ramal, pelo mesmo ``PHASE_COLUMNS`` — somar as três colunas numa
    delas quebraria a igualdade sem quebrar teste nenhum, se este não existisse.
    """

    def test_the_json_powers_are_the_shape_multipliers(self) -> None:
        catalog, loads, patterns, updates, equivalent = make_system()
        branches = equivalent.model.branches

        payload = build_branch_json_payload(
            branches,
            equivalent,
            tuple(range(len(branches.records))),
        )
        bundle = build_simplified_export(
            catalog,
            make_cables(),
            PHASES,
            (0,),
            equivalent=equivalent,
            loads=loads,
            patterns=patterns,
            generator_updates=updates,
        )
        text = "\n".join(content for _, content in bundle.files)

        checked = 0
        for record in branches.records:
            entry = payload[f"RAMAL-{record.branch_id}"]
            if entry["P0"] is None:
                continue
            shapes = re.findall(
                rf"New LoadShape\.PERFIL-RAMAL-{record.branch_id}-\dF-\w+ "
                r"npts=\d+ interval=\d+ mult=\[([^\]]+)\] qmult=\[([^\]]+)\]",
                text,
            )
            if not shapes:
                continue
            self.assertEqual(
                len(shapes),
                1,
                "um ramal com potência no JSON é monofásico e tem um LoadShape",
            )
            mult, qmult = shapes[0]
            self.assertEqual(
                [float(value) for value in mult.split()],
                [entry[f"P{npat}"] for npat in range(4)],
            )
            self.assertEqual(
                [float(value) for value in qmult.split()],
                [entry[f"Q{npat}"] for npat in range(4)],
            )
            checked += 1
        self.assertGreater(checked, 0, "nenhum ramal com LoadShape para comparar")


if __name__ == "__main__":
    unittest.main()
