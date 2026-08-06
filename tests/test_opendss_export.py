from __future__ import annotations

import math
import unittest

from circuit_viewer.model import (
    CableModel,
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    LoadPatternRecord,
    RegulatorModel,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.opendss_export import (
    FREQUENCY_HZ,
    LINES_FILENAME,
    MAX_REPORTED_ISSUES,
    REGULATORS_FILENAME,
    SINGLE_PHASE_LOADS_FILENAME,
    SWITCHES_FILENAME,
    THREE_PHASE_LOADS_FILENAME,
    TWO_PHASE_LOADS_FILENAME,
    build_export,
    build_line_export,
    build_load_export,
    build_master_export,
    build_regulator_export,
    build_switch_export,
    bus_namer,
    master_filenames,
    parse_number,
    phase_voltage_kv,
    positive_sequence_capacitance_nf,
    sanitize_dss_name,
)
from circuit_viewer.opendss_settings import OpenDssLoadSettings
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


PHASES = PhaseConfiguration(
    (
        PhaseMappingEntry("1", "D", 1, "1"),
        PhaseMappingEntry("2", "E", 1, "2"),
        PhaseMappingEntry("3", "F", 1, "3"),
        PhaseMappingEntry("4", "DN", 1, "1.0"),
        PhaseMappingEntry("5", "SEM_LETRA", 1, "2.0"),
        PhaseMappingEntry("6", "FN", 1, None),
        PhaseMappingEntry("7", "DE", 2, "1.2"),
        PhaseMappingEntry("8", "EF", 2, "2.3"),
        # Nós em ordem crescente, não na ordem das letras: parear o DSS
        # posicionalmente com "FD" inverteria as duas fases.
        PhaseMappingEntry("9", "FD", 2, "1.3"),
        PhaseMappingEntry("10", "XY", 2, "1.2"),
        PhaseMappingEntry("11", "DE", 2, None),
        PhaseMappingEntry("13", "DEF", 3, "1.2.3"),
        PhaseMappingEntry("14", "DEFN", 3, "1.2.3.0"),
        PhaseMappingEntry("15", "DEX", 3, "1.2.3"),
        PhaseMappingEntry("99", "SEM_DSS", 3, None),
    )
)

# Sem nenhuma entrada monofásica para F, uma carga "EF" não consegue resolver o
# terminal daquela fase.
PHASES_WITHOUT_F = PhaseConfiguration(
    (
        PhaseMappingEntry("1", "D", 1, "1"),
        PhaseMappingEntry("2", "E", 1, "2"),
        PhaseMappingEntry("8", "EF", 2, "2.3"),
    )
)


def make_cables(**overrides: dict[str, str]) -> CableModel:
    """Catálogo com um cabo bom (CB1) e um sem R1 (CB2)."""

    return CableModel(
        ["CB1", "CB2"],
        ["1", "1"],
        ["4/0", "336"],
        ["340", "500"],
        ["0,00824", ""],
        ["0,367", ""],
        ["0,42", ""],
        ["1,2", "1,2"],  # QCAP
        ["0,551", "0,5"],  # R0
        ["1,232", "1,1"],  # X0
        ["0,367", ""],  # R1 vazio no CB2
        ["0,42", "0,4"],  # X1
        ["ALUMINIO 4/0", "ALUMINIO 336"],
        ["EXT-1", "EXT-2"],
    )


def make_bars(codes: tuple[str, ...] = ("BARRA_A", "BARRA_B", "BARRA_C")) -> CircuitModel:
    return CircuitModel(
        ["B0", "B1", "B2"],
        list(codes),
        [500_000.0, 500_100.0, 500_200.0],
        [8_000_000.0, 8_000_000.0, 8_000_000.0],
        UtmCrs(21, northern=False),
    )


def make_network(
    bars: CircuitModel,
    *,
    codes: tuple[str, str] = ("TR-1", "TR-2"),
    phases: tuple[str, str] = ("13", "13"),
    cables: tuple[str, str] = ("CB1", "CB1"),
    lengths: tuple[float, float] = (250.0, 400.0),
) -> LineNetworkModel:
    return LineNetworkModel(
        bars,
        ["T0", "T1"],
        list(codes),
        list(phases),
        [0, 1],
        [1, 2],
        ["", ""],
        list(cables),
        ["", ""],
        list(lengths),
    )


def make_catalog(
    network: LineNetworkModel,
    *,
    switches: SwitchModel | None = None,
    voltage: str = "13,8",
) -> CircuitCatalogModel:
    return CircuitCatalogModel.build(
        network,
        switches,
        [CircuitDefinition("C1", "B0", "ALIMENTADOR", voltage)],
    )


def data_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("New ")]


def open_commands(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("Open ")]


def make_switches(
    network: LineNetworkModel,
    *,
    segment_indices: tuple[int, ...] = (1,),
    codes: tuple[str, ...] = ("CHV-001",),
    states: tuple[str, ...] = ("1",),
    switch_ids: tuple[str, ...] = ("CH1",),
    circuit_ids: tuple[str, ...] = ("C1",),
) -> SwitchModel:
    size = len(segment_indices)
    return SwitchModel(
        network,
        list(switch_ids),
        ["TC"] * size,
        list(circuit_ids),
        list(segment_indices),
        list(codes),
        list(states),
        ["1"] * size,
        [""] * size,
        [""] * size,
        [""] * size,
    )


def make_regulators(
    network: LineNetworkModel,
    *,
    segment_indices: tuple[int, ...] = (0,),
    codes: tuple[str, ...] = ("X",),
    vnom_values: tuple[str, ...] = ("34,5",),
    snom_values: tuple[str, ...] = ("333",),
    regulator_ids: tuple[str, ...] = ("RG1",),
) -> RegulatorModel:
    """Um regulador no trecho 0, com os valores do exemplo da especificação."""

    size = len(segment_indices)
    return RegulatorModel(
        network,
        list(regulator_ids),
        list(segment_indices),
        [""] * size,
        list(codes),
        ["Y"] * size,
        list(snom_values),
        ["10"] * size,
        ["32"] * size,
        ["0"] * size,
        ["100"] * size,
        list(vnom_values),
    )


def transformer_entries(text: str) -> list[str]:
    return [
        line for line in data_lines(text) if line.startswith("New Transformer.")
    ]


def control_entries(text: str) -> list[str]:
    return [
        line for line in data_lines(text) if line.startswith("New RegControl.")
    ]


def make_loads(
    bars: CircuitModel,
    *,
    load_ids: tuple[str, ...] = ("CG1",),
    bar_indices: tuple[int, ...] = (1,),
    codes: tuple[str, ...] = ("CARGA-1",),
    phases: tuple[str, ...] = ("1",),
) -> LoadModel:
    size = len(load_ids)
    return LoadModel(
        bars,
        list(load_ids),
        list(bar_indices),
        [f"EXT-{index}" for index in range(size)],
        list(codes),
        ["10"] * size,
        ["12"] * size,
        ["220"] * size,
        list(phases),
        ["Y"] * size,
    )


def make_patterns(
    loads: LoadModel,
    *,
    without: tuple[int, ...] = (),
    groups: dict[int, tuple[tuple[str, ...], ...]] | None = None,
) -> LoadPatternModel:
    """Patamares densos por carga, com valores previsíveis por NPAT.

    ``without`` lista as cargas sem patamar algum — o importador descarta
    grupos incompletos, então essa é a única forma de ausência. ``groups``
    substitui os quatro NPAT de uma carga, cada um com os seis valores na ordem
    ``PD, PE, PF, QD, QE, QF``.
    """

    default = tuple(
        (
            f"{1.5 + npat}",
            f"{2.5 + npat}",
            f"{3.5 + npat}",
            f"{0.25 + npat}",
            f"{0.35 + npat}",
            f"{0.45 + npat}",
        )
        for npat in range(4)
    )
    overrides = groups or {}
    records_by_load: list[tuple[LoadPatternRecord, ...] | None] = []
    for load_index in range(len(loads)):
        if load_index in without:
            records_by_load.append(None)
            continue
        values = overrides.get(load_index, default)
        records_by_load.append(
            tuple(
                LoadPatternRecord(loads.load_ids[load_index], npat, *row)
                for npat, row in enumerate(values)
            )
        )
    return LoadPatternModel(loads, records_by_load)


def export_loads(
    loads: LoadModel,
    patterns: LoadPatternModel,
    phase_count: int,
    **kwargs,  # noqa: ANN003
):  # noqa: ANN201
    """Exporta as cargas de uma contagem de fases com o catálogo padrão."""

    catalog = kwargs.pop("catalog", None)
    configuration = kwargs.pop("configuration", PHASES)
    if catalog is None:
        catalog = make_catalog(make_network(make_bars()))
    return build_load_export(
        catalog,
        loads,
        patterns,
        configuration,
        [0],
        phase_count=phase_count,
        **kwargs,
    )


def load_entries(text: str) -> list[str]:
    return [line for line in data_lines(text) if line.startswith("New Load.")]


def shape_entries(text: str) -> list[str]:
    return [
        line for line in data_lines(text) if line.startswith("New LoadShape.")
    ]


class CapacitanceTests(unittest.TestCase):
    def test_uses_phase_voltage_not_line_voltage(self) -> None:
        # 1,2 kvar/km por fase em um circuito de 13,8 kV de linha:
        # V_f = 13,8/raiz(3) = 7,967 kV  ->  C1 = 50,1 nF/km.
        value = positive_sequence_capacitance_nf(1.2, 13.8)

        self.assertAlmostEqual(value, 50.14, places=2)

        # Trava a conversão linha->fase: usar a tensão de linha daria 1/3 disso.
        line_voltage_result = (1.2 * 1_000.0) / (
            2.0 * math.pi * FREQUENCY_HZ * (13.8 * 1_000.0) ** 2
        ) * 1e9
        self.assertAlmostEqual(value / line_voltage_result, 3.0, places=9)

    def test_scales_with_qcap_and_inverse_square_of_voltage(self) -> None:
        base = positive_sequence_capacitance_nf(1.0, 13.8)

        self.assertAlmostEqual(
            positive_sequence_capacitance_nf(2.0, 13.8),
            2.0 * base,
        )
        self.assertAlmostEqual(
            positive_sequence_capacitance_nf(1.0, 27.6),
            base / 4.0,
        )

    def test_rejects_non_positive_voltage_and_frequency(self) -> None:
        with self.assertRaises(ValueError):
            positive_sequence_capacitance_nf(1.0, 0.0)
        with self.assertRaises(ValueError):
            positive_sequence_capacitance_nf(1.0, -13.8)
        with self.assertRaises(ValueError):
            positive_sequence_capacitance_nf(1.0, 13.8, frequency_hz=0.0)


class SanitizeTests(unittest.TestCase):
    def test_removes_delimiters_and_accents(self) -> None:
        # Ponto separa nós de barra e espaço separa propriedades no OpenDSS.
        self.assertEqual(sanitize_dss_name("Ação 1.2"), "Acao_1_2")
        self.assertEqual(sanitize_dss_name("  TR-1  "), "TR-1")
        self.assertEqual(sanitize_dss_name("__A__"), "A")
        self.assertEqual(sanitize_dss_name(""), "")
        self.assertEqual(sanitize_dss_name("///"), "")

    def test_parse_number_accepts_one_decimal_separator(self) -> None:
        self.assertEqual(parse_number("0,42"), 0.42)
        self.assertEqual(parse_number("0.42"), 0.42)
        self.assertEqual(parse_number(" 13 "), 13.0)
        self.assertIsNone(parse_number("1.234,5"))
        self.assertIsNone(parse_number(""))
        self.assertIsNone(parse_number("n/a"))


class LineExportTests(unittest.TestCase):
    def test_full_line_for_a_three_phase_segment(self) -> None:
        network = make_network(make_bars(), lengths=(250.0, 400.0))
        catalog = make_catalog(network)

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        self.assertEqual(result.exported_count, 2)
        self.assertEqual(result.discarded_count, 0)
        self.assertEqual(result.issues, ())
        self.assertEqual(
            data_lines(result.text)[0],
            "New Line.TR-1 Bus1=BARRA_A.1.2.3 Bus2=BARRA_B.1.2.3 Phases=3 "
            "R1=0.367 X1=0.42 R0=0.551 X0=1.232 C1=50.1433 C0=50.1433 "
            "Length=0.25 units=km",
        )
        self.assertTrue(result.text.startswith("!"))
        self.assertTrue(result.text.endswith("\n"))

    def test_buses_use_bar_code_not_bar_id(self) -> None:
        bars = make_bars(("COD-A", "COD-B", "COD-C"))
        catalog = make_catalog(make_network(bars))

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        first = data_lines(result.text)[0]
        self.assertIn("Bus1=COD-A.1.2.3", first)
        self.assertIn("Bus2=COD-B.1.2.3", first)
        self.assertNotIn("B0", first)

    def test_bar_without_code_falls_back_to_bar_id(self) -> None:
        catalog = make_catalog(make_network(make_bars(("", "COD-B", "COD-C"))))

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        self.assertIn("Bus1=B0.1.2.3", data_lines(result.text)[0])

    def test_length_in_meters_becomes_kilometres(self) -> None:
        catalog = make_catalog(make_network(make_bars(), lengths=(1_500.0, 20.0)))

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        exported = data_lines(result.text)
        self.assertIn("Length=1.5 units=km", exported[0])
        self.assertIn("Length=0.02 units=km", exported[1])

    def test_phase_count_and_dss_code_come_from_the_configuration(self) -> None:
        catalog = make_catalog(make_network(make_bars(), phases=("1", "7")))

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        exported = data_lines(result.text)
        self.assertIn("Bus1=BARRA_A.1 Bus2=BARRA_B.1 Phases=1", exported[0])
        self.assertIn("Bus1=BARRA_B.1.2 Bus2=BARRA_C.1.2 Phases=2", exported[1])

    def test_switch_segments_are_excluded_and_counted(self) -> None:
        network = make_network(make_bars())
        switches = SwitchModel(
            network,
            ["CH1"],
            ["TC"],
            ["C1"],
            [1],  # o trecho T1 é uma chave
            ["CHV-1"],
            ["1"],
            ["1"],
            [""],
            [""],
            [""],
        )
        catalog = make_catalog(network, switches=switches)

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        exported = data_lines(result.text)
        self.assertEqual(len(exported), 1)
        self.assertIn("New Line.TR-1 ", exported[0])
        self.assertNotIn("TR-2", result.text)
        self.assertEqual(result.skipped_switch_count, 1)

    def test_shared_segment_is_written_once(self) -> None:
        network = make_network(make_bars())
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "A", "13,8"),
                CircuitDefinition("C2", "B2", "B", "13,8"),
            ],
        )

        result = build_line_export(catalog, make_cables(), PHASES, [0, 1])

        self.assertEqual(result.exported_count, 2)
        self.assertEqual(len(data_lines(result.text)), 2)

    def test_shared_segment_with_diverging_voltage_is_reported(self) -> None:
        network = make_network(make_bars())
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "A", "13,8"),
                CircuitDefinition("C2", "B2", "B", "34,5"),
            ],
        )

        result = build_line_export(catalog, make_cables(), PHASES, [0, 1])

        self.assertEqual(result.exported_count, 2)
        self.assertEqual(result.discarded_count, 0)
        self.assertTrue(result.has_warnings)
        self.assertTrue(
            any("VNOM diferente" in issue.reason for issue in result.issues)
        )
        # A tensão do primeiro circuito selecionado prevalece.
        self.assertIn("C1=50.1433", data_lines(result.text)[0])

    def test_only_the_selected_circuits_are_exported(self) -> None:
        network = make_network(make_bars())
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "A", "13,8"),
                CircuitDefinition("C2", "B2", "B", "13,8"),
            ],
        )

        result = build_line_export(catalog, make_cables(), PHASES, [1])

        self.assertIn("Circuitos: C2", result.text)
        self.assertEqual(result.exported_count, 2)

    def test_rejects_circuit_index_out_of_range(self) -> None:
        catalog = make_catalog(make_network(make_bars()))

        with self.assertRaises(IndexError):
            build_line_export(catalog, make_cables(), PHASES, [5])

    def test_output_is_deterministic(self) -> None:
        catalog = make_catalog(make_network(make_bars()))
        cables = make_cables()

        first = build_line_export(catalog, cables, PHASES, [0])
        second = build_line_export(catalog, cables, PHASES, [0])

        self.assertEqual(first.text, second.text)


class LineExportDiagnosticsTests(unittest.TestCase):
    def _export(self, **network_kwargs) -> tuple:  # noqa: ANN003
        voltage = network_kwargs.pop("voltage", "13,8")
        network = make_network(make_bars(), **network_kwargs)
        catalog = make_catalog(network, voltage=voltage)
        result = build_line_export(catalog, make_cables(), PHASES, [0])
        return result, [issue.reason for issue in result.issues]

    def test_unmapped_phases_are_discarded(self) -> None:
        result, reasons = self._export(phases=("13", "404"))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 1)
        self.assertIn("FASES2 '404' sem relação em fases2.json", reasons)

    def test_phases_without_dss_code_are_discarded(self) -> None:
        result, reasons = self._export(phases=("13", "99"))

        self.assertEqual(result.exported_count, 1)
        self.assertIn("FASES2 '99' sem código DSS em fases2.json", reasons)

    def test_missing_cable_is_discarded(self) -> None:
        result, reasons = self._export(cables=("CB1", "CB404"))

        self.assertEqual(result.exported_count, 1)
        self.assertIn(
            "CABOF_ID 'CB404' ausente do catálogo de cabos",
            reasons,
        )

    def test_cable_without_numeric_field_is_discarded(self) -> None:
        result, reasons = self._export(cables=("CB1", "CB2"))

        self.assertEqual(result.exported_count, 1)
        self.assertIn("cabo CB2 sem R1 numérico", reasons)

    def test_missing_length_is_discarded(self) -> None:
        result, reasons = self._export(lengths=(250.0, float("nan")))

        self.assertEqual(result.exported_count, 1)
        self.assertIn("COMPR ausente", reasons)

    def test_invalid_nominal_voltage_discards_the_whole_circuit(self) -> None:
        result, reasons = self._export(voltage="")

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 2)
        self.assertTrue(all("sem VNOM numérica positiva" in reason for reason in reasons))

    def test_duplicate_code_keeps_only_the_first_line(self) -> None:
        result, reasons = self._export(codes=("TR-1", "TR-1"))

        self.assertEqual(result.exported_count, 1)
        self.assertIn("nome 'TR-1' já usado pelo trecho T0", reasons)

    def test_empty_code_falls_back_to_segment_id_and_warns(self) -> None:
        result, reasons = self._export(codes=("", "TR-2"))

        self.assertEqual(result.exported_count, 2)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn("New Line.T0 ", result.text)
        self.assertTrue(any("CODIGO vazio" in reason for reason in reasons))


class SwitchExportTests(unittest.TestCase):
    def _catalog(self, **switch_kwargs) -> CircuitCatalogModel:  # noqa: ANN003
        network = make_network(make_bars())
        switches = make_switches(network, **switch_kwargs)
        return make_catalog(network, switches=switches)

    def test_full_line_for_a_closed_switch(self) -> None:
        result = build_switch_export(self._catalog(), PHASES, [0])

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.open_count, 0)
        self.assertEqual(result.discarded_count, 0)
        self.assertEqual(
            data_lines(result.text)[0],
            "New Line.CHV-001 Bus1=BARRA_B.1.2.3 Bus2=BARRA_C.1.2.3 Phases=3 "
            "Switch=Yes",
        )
        # Switch=Yes redefine r1/x1/r0/x0/c1/c0/length: nada elétrico depois dele.
        self.assertTrue(data_lines(result.text)[0].endswith("Switch=Yes"))
        self.assertEqual(open_commands(result.text), [])

    def test_open_switch_gets_an_open_command(self) -> None:
        result = build_switch_export(self._catalog(states=("0",)), PHASES, [0])

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.open_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertEqual(open_commands(result.text), ["Open Line.CHV-001 1"])

    def test_every_definition_comes_before_every_open_command(self) -> None:
        network = make_network(make_bars())
        switches = make_switches(
            network,
            segment_indices=(0, 1),
            codes=("CHV-A", "CHV-B"),
            states=("0", "1"),  # a primeira, aberta, é definida antes
            switch_ids=("CH1", "CH2"),
            circuit_ids=("C1", "C1"),
        )
        catalog = make_catalog(network, switches=switches)

        result = build_switch_export(catalog, PHASES, [0])

        body = [
            line
            for line in result.text.splitlines()
            if line.startswith(("New ", "Open "))
        ]
        self.assertEqual(len(body), 3)
        self.assertTrue(all(line.startswith("New ") for line in body[:2]))
        self.assertEqual(body[2], "Open Line.CHV-A 1")

    def test_invalid_state_is_exported_as_open_with_a_warning(self) -> None:
        result = build_switch_export(self._catalog(states=("X",)), PHASES, [0])

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.open_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertTrue(
            any("ESTADO 'X' inválido" in issue.reason for issue in result.issues)
        )

    def test_name_comes_from_the_switch_code_not_the_segment_code(self) -> None:
        # O trecho 1 tem CODIGO "TR-2"; a chave tem "CHV-001".
        result = build_switch_export(self._catalog(), PHASES, [0])

        self.assertIn("New Line.CHV-001 ", result.text)
        self.assertNotIn("TR-2", result.text)

    def test_empty_switch_code_falls_back_to_switch_id(self) -> None:
        result = build_switch_export(self._catalog(codes=("",)), PHASES, [0])

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn("New Line.CH1 ", result.text)
        self.assertTrue(
            any("CODIGO da chave vazio" in issue.reason for issue in result.issues)
        )

    def test_name_reserved_by_a_segment_is_discarded(self) -> None:
        catalog = self._catalog()

        result = build_switch_export(
            catalog,
            PHASES,
            [0],
            reserved_names=frozenset({"CHV-001"}),
        )

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertIn(
            f"nome 'CHV-001' já usado por um trecho em {LINES_FILENAME}",
            [issue.reason for issue in result.issues],
        )

    def test_duplicate_switch_codes_keep_only_the_first(self) -> None:
        network = make_network(make_bars())
        switches = make_switches(
            network,
            segment_indices=(0, 1),
            codes=("CHV-A", "CHV-A"),
            states=("1", "1"),
            switch_ids=("CH1", "CH2"),
            circuit_ids=("C1", "C1"),
        )

        result = build_switch_export(
            make_catalog(network, switches=switches),
            PHASES,
            [0],
        )

        self.assertEqual(result.exported_count, 1)
        self.assertIn(
            "nome 'CHV-A' já usado pela chave CH1",
            [issue.reason for issue in result.issues],
        )

    def test_phases_and_dss_come_from_the_segment(self) -> None:
        network = make_network(make_bars(), phases=("13", "7"))
        switches = make_switches(network)

        result = build_switch_export(
            make_catalog(network, switches=switches),
            PHASES,
            [0],
        )

        self.assertIn(
            "Bus1=BARRA_B.1.2 Bus2=BARRA_C.1.2 Phases=2 Switch=Yes",
            result.text,
        )

    def test_unmapped_phases_discard_the_switch(self) -> None:
        network = make_network(make_bars(), phases=("13", "404"))
        switches = make_switches(network)

        result = build_switch_export(
            make_catalog(network, switches=switches),
            PHASES,
            [0],
        )

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertIn(
            "FASES2 '404' sem relação em fases2.json",
            [issue.reason for issue in result.issues],
        )

    def test_catalog_without_switches_produces_an_empty_file(self) -> None:
        catalog = make_catalog(make_network(make_bars()))

        result = build_switch_export(catalog, PHASES, [0])

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.open_count, 0)
        self.assertEqual(data_lines(result.text), [])
        self.assertTrue(result.text.startswith("!"))

    def test_output_is_deterministic(self) -> None:
        catalog = self._catalog()

        first = build_switch_export(catalog, PHASES, [0])
        second = build_switch_export(catalog, PHASES, [0])

        self.assertEqual(first.text, second.text)

    def test_cancellation(self) -> None:
        with self.assertRaises(InterruptedError):
            build_switch_export(
                self._catalog(),
                PHASES,
                [0],
                cancel_check=lambda: True,
            )


class RegulatorExportTests(unittest.TestCase):
    """Um regulador trifásico vira três monofásicos e ocupa o lugar do trecho."""

    def _network(self, **overrides):  # noqa: ANN003, ANN202
        overrides.setdefault("lengths", (5.0, 400.0))
        return make_network(make_bars(), **overrides)

    def _export(self, network=None, regulators=None, voltage="34,5", **kwargs):  # noqa: ANN001, ANN003, ANN202
        network = self._network() if network is None else network
        catalog = make_catalog(network, voltage=voltage, **kwargs)
        model = make_regulators(network) if regulators is None else regulators
        return build_regulator_export(catalog, model, PHASES, [0])

    def test_three_single_phase_transformers_follow_the_specification(self) -> None:
        result = self._export()

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertEqual(result.issues, ())
        self.assertEqual(
            transformer_entries(result.text),
            [
                "New Transformer.REG-X-D phases=1 windings=2 XHL=0.01 "
                "%LoadLoss=0.01 Buses=[BARRA_A.1.0, BARRA_B.1.0] "
                "conns=[wye, wye] kVs=[19.9186, 19.9186] kVAs=[111, 111]",
                "New Transformer.REG-X-E phases=1 windings=2 XHL=0.01 "
                "%LoadLoss=0.01 Buses=[BARRA_A.2.0, BARRA_B.2.0] "
                "conns=[wye, wye] kVs=[19.9186, 19.9186] kVAs=[111, 111]",
                "New Transformer.REG-X-F phases=1 windings=2 XHL=0.01 "
                "%LoadLoss=0.01 Buses=[BARRA_A.3.0, BARRA_B.3.0] "
                "conns=[wye, wye] kVs=[19.9186, 19.9186] kVAs=[111, 111]",
            ],
        )

    def test_each_transformer_gets_its_own_control(self) -> None:
        result = self._export()

        self.assertEqual(
            control_entries(result.text),
            [
                "New RegControl.CTRL-X-D transformer=REG-X-D winding=2 "
                "vreg=66.3953 band=1.32791 ptratio=300",
                "New RegControl.CTRL-X-E transformer=REG-X-E winding=2 "
                "vreg=66.3953 band=1.32791 ptratio=300",
                "New RegControl.CTRL-X-F transformer=REG-X-F winding=2 "
                "vreg=66.3953 band=1.32791 ptratio=300",
            ],
        )

    def test_every_control_comes_after_every_transformer(self) -> None:
        # O RegControl referencia o transformador pelo nome, então nenhuma
        # definição de controle pode preceder a do seu transformador.
        kinds = [line.split(".", 1)[0] for line in data_lines(self._export().text)]

        self.assertEqual(kinds, ["New Transformer"] * 3 + ["New RegControl"] * 3)

    def test_the_regulated_segment_stops_being_a_line(self) -> None:
        network = self._network()
        catalog = make_catalog(network, voltage="34,5")
        regulators = make_regulators(network)

        exported = build_regulator_export(catalog, regulators, PHASES, [0])
        lines = build_line_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            skip_segments=exported.replaced_segments,
        )

        self.assertEqual(exported.replaced_segments, frozenset({0}))
        # TR-1 é o trecho 0: ele virou regulador e não pode sair também como
        # Line, senão ficaria em paralelo com os transformadores.
        self.assertEqual(lines.exported_segments, (("TR-2", 1),))
        self.assertNotIn("New Line.TR-1 ", lines.text)

    def test_a_discarded_regulator_keeps_the_line_of_its_segment(self) -> None:
        network = self._network(phases=("7", "13"))
        catalog = make_catalog(network, voltage="34,5")

        exported = build_regulator_export(
            catalog, make_regulators(network), PHASES, [0]
        )
        lines = build_line_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            skip_segments=exported.replaced_segments,
        )

        # Sem regulador emitido não há substituição: apagar a linha aqui
        # removeria um ramo inteiro da rede em silêncio.
        self.assertEqual(exported.exported_count, 0)
        self.assertEqual(exported.replaced_segments, frozenset())
        self.assertIn("New Line.TR-1 ", lines.text)

    def test_only_three_phase_segments_are_exported(self) -> None:
        result = self._export(network=self._network(phases=("7", "13")))

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertTrue(
            any("não é trifásica" in issue.reason for issue in result.issues),
            result.issues,
        )

    def test_voltage_in_volts_is_refused_against_the_circuit_vnom(self) -> None:
        network = self._network()
        result = self._export(
            network=network,
            regulators=make_regulators(network, vnom_values=("34500",)),
        )

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertTrue(
            any("confira se a unidade é kV" in issue.reason for issue in result.issues),
            result.issues,
        )

    def test_non_numeric_vnom_and_snom_are_discarded(self) -> None:
        network = self._network()
        for field, regulators in (
            ("VNOM", make_regulators(network, vnom_values=("",))),
            ("SNOM", make_regulators(network, snom_values=("zero",))),
        ):
            with self.subTest(field=field):
                result = self._export(network=network, regulators=regulators)

                self.assertEqual(result.exported_count, 0)
                self.assertEqual(result.discarded_count, 1)
                self.assertTrue(
                    any(field in issue.reason for issue in result.issues),
                    result.issues,
                )

    def test_empty_code_falls_back_to_the_regulator_id(self) -> None:
        network = self._network()
        result = self._export(
            network=network,
            regulators=make_regulators(network, codes=("",)),
        )

        self.assertEqual(result.exported_count, 1)
        # Aviso, não descarte: o regulador saiu, só com outro nome.
        self.assertEqual(result.discarded_count, 0)
        self.assertTrue(
            transformer_entries(result.text)[0].startswith(
                "New Transformer.REG-RG1-D "
            ),
            result.text,
        )

    def test_replacing_a_long_segment_warns_without_discarding(self) -> None:
        result = self._export(network=self._network(lengths=(250.0, 400.0)))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertTrue(
            any("impedância dele saiu do modelo" in i.reason for i in result.issues),
            result.issues,
        )

    def test_a_switch_segment_refuses_the_regulator(self) -> None:
        network = self._network()
        switches = make_switches(network, segment_indices=(0,))
        result = self._export(network=network, switches=switches)

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.discarded_count, 1)
        self.assertTrue(
            any("já representa a chave" in issue.reason for issue in result.issues),
            result.issues,
        )

    def test_homonym_regulators_are_discarded(self) -> None:
        network = self._network()
        result = self._export(
            network=network,
            regulators=make_regulators(
                network,
                segment_indices=(0, 1),
                regulator_ids=("RG1", "RG2"),
                codes=("X", "X"),
                vnom_values=("34,5", "34,5"),
                snom_values=("333", "333"),
            ),
        )

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 1)
        self.assertTrue(
            any("já usado pelo regulador RG1" in i.reason for i in result.issues),
            result.issues,
        )


class RegulatorBundleTests(unittest.TestCase):
    """O arquivo de reguladores só existe quando há regulador exportado."""

    def _bundle(self, **overrides):  # noqa: ANN003, ANN202
        network = make_network(make_bars(), lengths=(5.0, 400.0), **overrides)
        catalog = make_catalog(network, voltage="34,5")
        return build_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            regulators=make_regulators(network),
        )

    def test_bundle_adds_the_file_and_the_master_redirect(self) -> None:
        bundle = self._bundle()

        self.assertEqual(
            [name for name, _ in bundle.element_files],
            [LINES_FILENAME, SWITCHES_FILENAME, REGULATORS_FILENAME],
        )
        self.assertIn(f"Redirect {REGULATORS_FILENAME}", bundle.master.text)

    def test_an_export_without_regulators_is_unchanged(self) -> None:
        network = make_network(make_bars())
        catalog = make_catalog(network)
        cables = make_cables()

        without = build_export(catalog, cables, PHASES, [0])
        explicit_none = build_export(catalog, cables, PHASES, [0], regulators=None)

        self.assertEqual(
            [name for name, _ in without.element_files],
            [LINES_FILENAME, SWITCHES_FILENAME],
        )
        self.assertEqual(without.files, explicit_none.files)
        self.assertNotIn(REGULATORS_FILENAME, without.master.text)

    def test_a_fully_discarded_regulator_model_writes_no_file(self) -> None:
        # Modelo presente, nenhum regulador exportável: o arquivo não sai, e o
        # master de quem não tem regulador continua idêntico.
        bundle = self._bundle(phases=("7", "13"))

        self.assertEqual(bundle.regulators.exported_count, 0)
        self.assertEqual(
            [name for name, _ in bundle.element_files],
            [LINES_FILENAME, SWITCHES_FILENAME],
        )
        self.assertTrue(bundle.has_warnings)
        self.assertTrue(
            any("não é trifásica" in issue.reason for issue in bundle.issues),
            bundle.issues,
        )


class ExportedSegmentIndexTests(unittest.TestCase):
    """O índice reverso precisa descrever exatamente o arquivo emitido.

    Ele é o que permite devolver um resultado de fluxo de potência ao trecho
    certo, então qualquer divergência entre ``exported_segments`` e as linhas
    ``New Line.<nome>`` atribuiria corrente ao elemento errado — em silêncio.
    """

    def _assert_matches_text(self, result, text: str) -> None:  # noqa: ANN001
        emitted = data_lines(text)
        self.assertEqual(len(result.exported_segments), len(emitted))
        for (name, _), line in zip(result.exported_segments, emitted):
            self.assertTrue(line.startswith(f"New Line.{name} "), line)

    def test_lines_pair_every_name_with_its_segment(self) -> None:
        network = make_network(make_bars())
        catalog = make_catalog(network)

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        self.assertEqual(result.exported_segments, (("TR-1", 0), ("TR-2", 1)))
        self._assert_matches_text(result, result.text)

    def test_line_fallback_name_is_the_one_indexed(self) -> None:
        network = make_network(make_bars(), codes=("", "TR-2"))
        catalog = make_catalog(network)

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        # O CODIGO vazio faz o nome cair no TRECHO_ID; o índice acompanha.
        self.assertEqual(result.exported_segments, (("T0", 0), ("TR-2", 1)))
        self._assert_matches_text(result, result.text)

    def test_discarded_line_stays_out_of_the_index(self) -> None:
        network = make_network(make_bars(), codes=("TR-1", "TR-1"))
        catalog = make_catalog(network)

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        # O homônimo é descartado, então só o primeiro trecho é indexado.
        self.assertEqual(result.exported_segments, (("TR-1", 0),))
        self._assert_matches_text(result, result.text)

    def test_shared_segment_is_indexed_once(self) -> None:
        network = make_network(make_bars())
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "A", "13,8"),
                CircuitDefinition("C2", "B2", "B", "13,8"),
            ],
        )

        result = build_line_export(catalog, make_cables(), PHASES, [0, 1])

        self.assertEqual(
            [index for _, index in result.exported_segments],
            [0, 1],
        )
        self._assert_matches_text(result, result.text)

    def test_switches_index_the_segment_the_switch_sits_on(self) -> None:
        network = make_network(make_bars())
        switches = make_switches(network)
        catalog = make_catalog(network, switches=switches)

        result = build_switch_export(catalog, PHASES, [0])

        # O nome vem do CODIGO da chave, mas o índice é o do trecho.
        self.assertEqual(result.exported_segments, (("CHV-001", 1),))
        self._assert_matches_text(result, result.text)

    def test_switch_discarded_by_a_reserved_name_stays_out(self) -> None:
        network = make_network(make_bars())
        switches = make_switches(network, codes=("TR-1",))
        catalog = make_catalog(network, switches=switches)

        result = build_switch_export(
            catalog,
            PHASES,
            [0],
            reserved_names=frozenset({"TR-1"}),
        )

        self.assertEqual(result.exported_segments, ())

    def test_bundle_indexes_cover_both_network_files(self) -> None:
        network = make_network(make_bars())
        switches = make_switches(network)
        catalog = make_catalog(network, switches=switches)

        bundle = build_export(catalog, make_cables(), PHASES, [0])

        self._assert_matches_text(bundle.lines, bundle.lines.text)
        self._assert_matches_text(bundle.switches, bundle.switches.text)
        indexed = {
            index
            for result in (bundle.lines, bundle.switches)
            for _, index in result.exported_segments
        }
        self.assertEqual(indexed, {0, 1})


class BusNamerTests(unittest.TestCase):
    """``bus_namer`` é a única definição de nome de barra; amarra-se ao arquivo."""

    def test_uses_the_code_with_the_bar_id_as_fallback(self) -> None:
        network = make_network(make_bars(codes=("BARRA_A", "", "BARRA_C")))
        catalog = make_catalog(network)

        name = bus_namer(catalog)

        self.assertEqual(name(0), "BARRA_A")
        self.assertEqual(name(1), "B1")
        self.assertEqual(name(2), "BARRA_C")

    def test_matches_the_terminals_written_in_the_lines(self) -> None:
        network = make_network(make_bars(codes=("BARRA A", "B.2", "BARRA_C")))
        catalog = make_catalog(network)
        name = bus_namer(catalog)

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        first = data_lines(result.text)[0]
        self.assertIn(f" Bus1={name(0)}.", first)
        self.assertIn(f" Bus2={name(1)}.", first)


class LoadExportTests(unittest.TestCase):
    """Cargas monofásicas: uma Load por carga, com o nó do próprio FASES2."""

    def _export(self, loads: LoadModel, patterns: LoadPatternModel, **kwargs):  # noqa: ANN003, ANN202
        return export_loads(loads, patterns, 1, **kwargs)

    def test_exports_a_load_with_its_daily_shape(self) -> None:
        bars = make_bars()
        loads = make_loads(bars)

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertFalse(result.has_warnings)
        kv = phase_voltage_kv(13.8)
        self.assertEqual(
            data_lines(result.text),
            [
                "New LoadShape.PERFIL-CARGA-1-1F-D npts=4 interval=1"
                " mult=[1.500000 2.500000 3.500000 4.500000]"
                " qmult=[0.250000 1.250000 2.250000 3.250000]",
                f"New Load.CARGA-1-1F-D phases=1 bus1=BARRA_B.1 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-1F-D"
                " class=1",
            ],
        )

    def test_every_shape_precedes_every_load(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("CARGA-1", "CARGA-2"),
            phases=("1", "1"),
        )

        result = self._export(loads, make_patterns(loads))

        kinds = [line.split(".", 1)[0] for line in data_lines(result.text)]
        self.assertEqual(kinds, ["New LoadShape"] * 2 + ["New Load"] * 2)

    def test_phase_letter_chooses_the_pattern_columns(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("EM-D", "EM-E"),
            phases=("1", "2"),
        )

        result = self._export(loads, make_patterns(loads))

        shapes = shape_entries(result.text)
        # D consome PD/QD; E consome PE/QE.
        self.assertIn("mult=[1.500000 2.500000 3.500000 4.500000]", shapes[0])
        self.assertIn("qmult=[0.250000 1.250000 2.250000 3.250000]", shapes[0])
        self.assertIn("mult=[2.500000 3.500000 4.500000 5.500000]", shapes[1])
        self.assertIn("qmult=[0.350000 1.350000 2.350000 3.350000]", shapes[1])

    def test_neutral_phase_uses_the_same_column_and_keeps_the_node(self) -> None:
        bars = make_bars()
        loads = make_loads(bars, codes=("COM-NEUTRO",), phases=("4",))

        result = self._export(loads, make_patterns(loads))

        entry = load_entries(result.text)[0]
        # DN é a fase D com neutro: o nó explícito do próprio DSS é preservado,
        # mas o nome e as colunas de patamar são os de D.
        self.assertIn("bus1=BARRA_B.1.0", entry)
        self.assertIn("New Load.COM-NEUTRO-1F-D ", entry)
        self.assertIn(
            "mult=[1.500000 2.500000 3.500000 4.500000]",
            shape_entries(result.text)[0],
        )

    def test_pattern_values_are_rounded_to_six_decimals(self) -> None:
        bars = make_bars()
        loads = make_loads(bars)
        groups = {
            0: tuple(
                ("1,23456789", "0", "0", "9,87654321", "0", "0")
                for _ in range(4)
            )
        }

        result = self._export(loads, make_patterns(loads, groups=groups))

        shape = shape_entries(result.text)[0]
        self.assertIn("mult=[1.234568 1.234568 1.234568 1.234568]", shape)
        self.assertIn("qmult=[9.876543 9.876543 9.876543 9.876543]", shape)

    def test_zeroed_pattern_is_valid(self) -> None:
        # Zero é diferente de vazio: uma carga sem consumo ainda é exportável.
        bars = make_bars()
        loads = make_loads(bars)
        groups = {0: tuple(("0",) * 6 for _ in range(4))}

        result = self._export(loads, make_patterns(loads, groups=groups))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn(
            "mult=[0.000000 0.000000 0.000000 0.000000]", result.text
        )

    def test_other_phase_counts_are_counted_without_diagnostics(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("MONO", "TRI"),
            phases=("1", "13"),
        )

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.skipped_other_phase_count, 1)
        self.assertEqual(result.issues, ())
        self.assertNotIn("TRI", result.text)

    def test_load_without_patterns_is_discarded(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("SEM-PATAMAR", "COM-PATAMAR"),
            phases=("1", "1"),
        )
        # O importador descarta grupos incompletos, então a primeira carga
        # simplesmente não tem patamar algum.
        patterns = make_patterns(loads, without=(0,))

        result = self._export(loads, patterns)

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 1)
        self.assertEqual(
            [(issue.segment_id, issue.reason) for issue in result.issues],
            [("CG1", "sem os quatro patamares (NPAT 0 a 3)")],
        )

    def test_non_numeric_pattern_is_discarded(self) -> None:
        bars = make_bars()
        loads = make_loads(bars)
        groups = {
            0: tuple(("n/a", "0", "0", "1", "0", "0") for _ in range(4))
        }

        result = self._export(loads, make_patterns(loads, groups=groups))

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(
            [issue.reason for issue in result.issues],
            ["patamar com PD não numérico; a carga inteira foi descartada"],
        )

    def test_unmapped_phase_is_reported(self) -> None:
        bars = make_bars()
        loads = make_loads(bars, phases=("404",))

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(result.skipped_other_phase_count, 0)
        self.assertIn(
            "FASES2 '404' sem relação em fases2.json",
            [issue.reason for issue in result.issues],
        )

    def test_single_phase_without_dss_or_letter_is_reported(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("SEM-DSS", "SEM-LETRA"),
            phases=("6", "5"),
        )

        result = self._export(loads, make_patterns(loads))

        reasons = [issue.reason for issue in result.issues]
        self.assertEqual(result.exported_count, 0)
        self.assertIn("FASES2 '6' sem código DSS em fases2.json", reasons)
        # "SEM_LETRA" tem dois E: não resolve uma fase única.
        self.assertIn(
            "FASES2 '5' com NOME 'SEM_LETRA' não resolve 1 fase(s) "
            "distinta(s) entre D, E e F",
            reasons,
        )

    def test_empty_code_falls_back_to_the_load_id(self) -> None:
        bars = make_bars()
        loads = make_loads(bars, codes=("",))

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn("New Load.CG1-1F-D ", result.text)
        self.assertIn("daily=PERFIL-CG1-1F-D", result.text)
        self.assertTrue(result.has_warnings)

    def test_duplicate_names_are_discarded(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("IGUAL", "IGUAL"),
            phases=("1", "1"),
        )

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertIn(
            "nome 'IGUAL-1F-D' já usado pela carga CG1; a carga inteira foi "
            "descartada",
            [issue.reason for issue in result.issues],
        )

    def test_load_outside_the_selected_circuits_is_ignored(self) -> None:
        bars = make_bars()
        network = make_network(bars)
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "ALIMENTADOR", "13,8"),
                CircuitDefinition("C2", "B2", "OUTRO", "13,8"),
            ],
        )
        loads = make_loads(bars)

        result = build_load_export(
            catalog, loads, make_patterns(loads), PHASES, [1], phase_count=1
        )

        # O circuito 2 parte de B2 e alcança as mesmas barras; ainda assim a
        # seleção é respeitada e a carga sai uma vez só.
        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.issues, ())

    def test_shared_bar_exports_once_and_warns_about_the_voltage(self) -> None:
        bars = make_bars()
        network = make_network(bars)
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "ALIMENTADOR", "13,8"),
                CircuitDefinition("C2", "B2", "OUTRO", "34,5"),
            ],
        )
        loads = make_loads(bars)

        result = build_load_export(
            catalog, loads, make_patterns(loads), PHASES, [0, 1], phase_count=1
        )

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn(
            "barra compartilhada com o circuito C2, de VNOM diferente; "
            "foi usada a do circuito C1",
            [issue.reason for issue in result.issues],
        )
        self.assertIn(f"kV={phase_voltage_kv(13.8):.6g}", result.text)

    def test_circuit_without_voltage_discards_the_load(self) -> None:
        network = make_network(make_bars())
        catalog = make_catalog(network, voltage="")
        loads = make_loads(network.bars)

        result = self._export(loads, make_patterns(loads), catalog=catalog)

        self.assertEqual(result.exported_count, 0)
        self.assertIn(
            "circuito C1 sem VNOM numérica positiva",
            [issue.reason for issue in result.issues],
        )

    def test_progress_and_cancellation(self) -> None:
        bars = make_bars()
        loads = make_loads(bars)
        patterns = make_patterns(loads)
        events: list[tuple[int, int]] = []

        self._export(
            loads,
            patterns,
            progress=lambda current, total: events.append((current, total)),
        )

        self.assertEqual(events[-1], (1, 1))
        with self.assertRaises(InterruptedError):
            self._export(loads, patterns, cancel_check=lambda: True)


class TwoPhaseLoadExportTests(unittest.TestCase):
    """Bifásicas: duas Load independentes, uma por fase."""

    def _export(self, loads: LoadModel, patterns: LoadPatternModel, **kwargs):  # noqa: ANN003, ANN202
        return export_loads(loads, patterns, 2, **kwargs)

    def _loads(self, bars: CircuitModel, phases: str) -> LoadModel:
        return make_loads(bars, phases=(phases,))

    def test_one_load_becomes_two_independent_single_phase_loads(self) -> None:
        bars = make_bars()
        loads = self._loads(bars, "7")  # DE

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertFalse(result.has_warnings)
        kv = phase_voltage_kv(13.8)
        self.assertEqual(
            data_lines(result.text),
            [
                "New LoadShape.PERFIL-CARGA-1-2F-D npts=4 interval=1"
                " mult=[1.500000 2.500000 3.500000 4.500000]"
                " qmult=[0.250000 1.250000 2.250000 3.250000]",
                "New LoadShape.PERFIL-CARGA-1-2F-E npts=4 interval=1"
                " mult=[2.500000 3.500000 4.500000 5.500000]"
                " qmult=[0.350000 1.350000 2.350000 3.350000]",
                f"New Load.CARGA-1-2F-D phases=1 bus1=BARRA_B.1 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-2F-D"
                " class=2",
                f"New Load.CARGA-1-2F-E phases=1 bus1=BARRA_B.2 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-2F-E"
                " class=2",
            ],
        )

    def test_terminals_come_from_the_single_phase_entries(self) -> None:
        # "FD" tem DSS "1.3": parear posicionalmente daria F=1 e D=3. O terminal
        # de cada letra vem da entrada monofásica, então F=3 e D=1.
        bars = make_bars()
        loads = self._loads(bars, "9")  # FD

        result = self._export(loads, make_patterns(loads))

        entries = load_entries(result.text)
        self.assertEqual(
            [line.split(" bus1=")[1].split(" ")[0] for line in entries],
            ["BARRA_B.3", "BARRA_B.1"],
        )
        # A ordem das fases segue as letras do NOME, não a ordem dos nós.
        self.assertIn("New Load.CARGA-1-2F-F ", entries[0])
        self.assertIn("New Load.CARGA-1-2F-D ", entries[1])

    def test_each_phase_reads_its_own_pattern_columns(self) -> None:
        bars = make_bars()
        loads = self._loads(bars, "8")  # EF

        result = self._export(loads, make_patterns(loads))

        shapes = shape_entries(result.text)
        # E consome PE/QE; F consome PF/QF.
        self.assertIn("PERFIL-CARGA-1-2F-E", shapes[0])
        self.assertIn("mult=[2.500000 3.500000 4.500000 5.500000]", shapes[0])
        self.assertIn("qmult=[0.350000 1.350000 2.350000 3.350000]", shapes[0])
        self.assertIn("PERFIL-CARGA-1-2F-F", shapes[1])
        self.assertIn("mult=[3.500000 4.500000 5.500000 6.500000]", shapes[1])
        self.assertIn("qmult=[0.450000 1.450000 2.450000 3.450000]", shapes[1])

    def test_every_shape_precedes_every_load(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("CARGA-1", "CARGA-2"),
            phases=("7", "8"),
        )

        result = self._export(loads, make_patterns(loads))

        kinds = [line.split(".", 1)[0] for line in data_lines(result.text)]
        self.assertEqual(kinds, ["New LoadShape"] * 4 + ["New Load"] * 4)

    def test_zeroed_pattern_is_valid(self) -> None:
        # Zero é diferente de vazio: uma fase sem consumo ainda é exportável.
        bars = make_bars()
        loads = self._loads(bars, "7")
        groups = {0: tuple(("0",) * 6 for _ in range(4))}

        result = self._export(loads, make_patterns(loads, groups=groups))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn(
            "mult=[0.000000 0.000000 0.000000 0.000000]", result.text
        )

    def test_one_invalid_phase_discards_the_whole_load(self) -> None:
        bars = make_bars()
        loads = self._loads(bars, "7")  # DE
        # PD válido, PE vazio: nenhuma das duas fases pode sair.
        groups = {0: tuple(("1", "", "0", "1", "1", "1") for _ in range(4))}

        result = self._export(loads, make_patterns(loads, groups=groups))

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(data_lines(result.text), [])
        self.assertEqual(
            [issue.reason for issue in result.issues],
            ["patamar com PE não numérico; a carga inteira foi descartada"],
        )

    def test_other_phase_counts_are_counted_without_diagnostics(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2", "CG3"),
            bar_indices=(1, 2, 1),
            codes=("MONO", "TRI", "BI"),
            phases=("1", "13", "7"),
        )

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.skipped_other_phase_count, 2)
        self.assertEqual(result.issues, ())

    def test_name_without_two_phases_is_reported(self) -> None:
        bars = make_bars()
        loads = self._loads(bars, "10")  # NOME "XY"

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 0)
        self.assertIn(
            "FASES2 '10' com NOME 'XY' não resolve 2 fase(s) distinta(s) "
            "entre D, E e F",
            [issue.reason for issue in result.issues],
        )

    def test_letter_without_a_terminal_is_reported(self) -> None:
        bars = make_bars()
        loads = self._loads(bars, "8")  # EF, sem entrada de F

        result = self._export(
            loads, make_patterns(loads), configuration=PHASES_WITHOUT_F
        )

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(
            [issue.reason for issue in result.issues],
            [
                "fase 'F' sem terminal DSS: nenhuma entrada monofásica de "
                "fases2.json a define"
            ],
        )

    def test_missing_dss_does_not_matter_for_multi_phase(self) -> None:
        # O DSS da entrada bifásica não é usado: os nós vêm das monofásicas.
        bars = make_bars()
        loads = self._loads(bars, "11")  # DE sem DSS

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.issues, ())

    def test_empty_code_falls_back_to_the_load_id(self) -> None:
        bars = make_bars()
        loads = make_loads(bars, codes=("",), phases=("7",))

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn("New Load.CG1-2F-D ", result.text)
        self.assertIn("New Load.CG1-2F-E ", result.text)
        self.assertTrue(result.has_warnings)

    def test_duplicate_names_discard_the_second_load_entirely(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("IGUAL", "IGUAL"),
            phases=("7", "7"),
        )

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertIn(
            "nome 'IGUAL-2F-D' já usado pela carga CG1; a carga inteira foi "
            "descartada",
            [issue.reason for issue in result.issues],
        )

    def test_name_reserved_by_another_file_discards_the_load(self) -> None:
        # O infixo -NF- torna a colisão entre arquivos impossível na prática,
        # então a reserva só se exercita passando os nomes diretamente.
        bars = make_bars()
        loads = self._loads(bars, "7")

        result = self._export(
            loads,
            make_patterns(loads),
            reserved_names=frozenset({"CARGA-1-2F-E"}),
        )

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(data_lines(result.text), [])
        self.assertIn(
            "nome 'CARGA-1-2F-E' já usado por uma carga de outra contagem de "
            "fases; a carga inteira foi descartada",
            [issue.reason for issue in result.issues],
        )

    def test_load_without_patterns_is_discarded(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("SEM-PATAMAR", "COM-PATAMAR"),
            phases=("7", "7"),
        )

        result = self._export(loads, make_patterns(loads, without=(0,)))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(
            [(issue.segment_id, issue.reason) for issue in result.issues],
            [("CG1", "sem os quatro patamares (NPAT 0 a 3)")],
        )

    def test_circuit_without_voltage_discards_the_load(self) -> None:
        network = make_network(make_bars())
        catalog = make_catalog(network, voltage="")
        loads = self._loads(network.bars, "7")

        result = self._export(loads, make_patterns(loads), catalog=catalog)

        self.assertEqual(result.exported_count, 0)
        self.assertIn(
            "circuito C1 sem VNOM numérica positiva",
            [issue.reason for issue in result.issues],
        )

    def test_shared_bar_exports_once_and_warns_about_the_voltage(self) -> None:
        bars = make_bars()
        network = make_network(bars)
        catalog = CircuitCatalogModel.build(
            network,
            None,
            [
                CircuitDefinition("C1", "B0", "ALIMENTADOR", "13,8"),
                CircuitDefinition("C2", "B2", "OUTRO", "34,5"),
            ],
        )
        loads = self._loads(bars, "7")

        result = build_load_export(
            catalog, loads, make_patterns(loads), PHASES, [0, 1], phase_count=2
        )

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn(
            "barra compartilhada com o circuito C2, de VNOM diferente; "
            "foi usada a do circuito C1",
            [issue.reason for issue in result.issues],
        )

    def test_progress_and_cancellation(self) -> None:
        bars = make_bars()
        loads = self._loads(bars, "7")
        patterns = make_patterns(loads)
        events: list[tuple[int, int]] = []

        self._export(
            loads,
            patterns,
            progress=lambda current, total: events.append((current, total)),
        )

        self.assertEqual(events[-1], (1, 1))
        with self.assertRaises(InterruptedError):
            self._export(loads, patterns, cancel_check=lambda: True)


class ThreePhaseLoadExportTests(unittest.TestCase):
    """Trifásicas: três Load independentes, uma por fase."""

    def _export(self, loads: LoadModel, patterns: LoadPatternModel, **kwargs):  # noqa: ANN003, ANN202
        return export_loads(loads, patterns, 3, **kwargs)

    def _loads(self, bars: CircuitModel, phases: str = "13") -> LoadModel:
        return make_loads(bars, phases=(phases,))

    def test_one_load_becomes_three_independent_single_phase_loads(self) -> None:
        bars = make_bars()
        loads = self._loads(bars)  # DEF

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertFalse(result.has_warnings)
        kv = phase_voltage_kv(13.8)
        self.assertEqual(
            data_lines(result.text),
            [
                "New LoadShape.PERFIL-CARGA-1-3F-D npts=4 interval=1"
                " mult=[1.500000 2.500000 3.500000 4.500000]"
                " qmult=[0.250000 1.250000 2.250000 3.250000]",
                "New LoadShape.PERFIL-CARGA-1-3F-E npts=4 interval=1"
                " mult=[2.500000 3.500000 4.500000 5.500000]"
                " qmult=[0.350000 1.350000 2.350000 3.350000]",
                "New LoadShape.PERFIL-CARGA-1-3F-F npts=4 interval=1"
                " mult=[3.500000 4.500000 5.500000 6.500000]"
                " qmult=[0.450000 1.450000 2.450000 3.450000]",
                f"New Load.CARGA-1-3F-D phases=1 bus1=BARRA_B.1 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-3F-D"
                " class=3",
                f"New Load.CARGA-1-3F-E phases=1 bus1=BARRA_B.2 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-3F-E"
                " class=3",
                f"New Load.CARGA-1-3F-F phases=1 bus1=BARRA_B.3 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-3F-F"
                " class=3",
            ],
        )

    def test_neutral_in_the_name_is_ignored(self) -> None:
        # "DEFN" resolve as mesmas três fases de "DEF": o N não é fase.
        bars = make_bars()
        loads = self._loads(bars, "14")

        result = self._export(loads, make_patterns(loads))

        entries = load_entries(result.text)
        self.assertEqual(len(entries), 3)
        self.assertEqual(
            [line.split(" bus1=")[1].split(" ")[0] for line in entries],
            ["BARRA_B.1", "BARRA_B.2", "BARRA_B.3"],
        )
        self.assertIn("New Load.CARGA-1-3F-D ", entries[0])

    def test_every_shape_precedes_every_load(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("CARGA-1", "CARGA-2"),
            phases=("13", "13"),
        )

        result = self._export(loads, make_patterns(loads))

        kinds = [line.split(".", 1)[0] for line in data_lines(result.text)]
        self.assertEqual(kinds, ["New LoadShape"] * 6 + ["New Load"] * 6)

    def test_zeroed_pattern_is_valid(self) -> None:
        bars = make_bars()
        loads = self._loads(bars)
        groups = {0: tuple(("0",) * 6 for _ in range(4))}

        result = self._export(loads, make_patterns(loads, groups=groups))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertEqual(len(load_entries(result.text)), 3)

    def test_one_invalid_phase_discards_the_whole_load(self) -> None:
        bars = make_bars()
        loads = self._loads(bars)
        # PD e PE válidos, PF vazio: nenhuma das três fases pode sair.
        groups = {0: tuple(("1", "1", "", "1", "1", "1") for _ in range(4))}

        result = self._export(loads, make_patterns(loads, groups=groups))

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(data_lines(result.text), [])
        self.assertEqual(
            [issue.reason for issue in result.issues],
            ["patamar com PF não numérico; a carga inteira foi descartada"],
        )

    def test_other_phase_counts_are_counted_without_diagnostics(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2", "CG3"),
            bar_indices=(1, 2, 1),
            codes=("MONO", "BI", "TRI"),
            phases=("1", "7", "13"),
        )

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.skipped_other_phase_count, 2)
        self.assertEqual(result.issues, ())

    def test_name_without_three_phases_is_reported(self) -> None:
        bars = make_bars()
        loads = self._loads(bars, "15")  # NOME "DEX"

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 0)
        self.assertIn(
            "FASES2 '15' com NOME 'DEX' não resolve 3 fase(s) distinta(s) "
            "entre D, E e F",
            [issue.reason for issue in result.issues],
        )

    def test_empty_code_falls_back_to_the_load_id(self) -> None:
        bars = make_bars()
        loads = make_loads(bars, codes=("",), phases=("13",))

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        for letter in ("D", "E", "F"):
            self.assertIn(f"New Load.CG1-3F-{letter} ", result.text)
        self.assertTrue(result.has_warnings)

    def test_circuit_without_voltage_discards_the_load(self) -> None:
        network = make_network(make_bars())
        catalog = make_catalog(network, voltage="")
        loads = self._loads(network.bars)

        result = self._export(loads, make_patterns(loads), catalog=catalog)

        self.assertEqual(result.exported_count, 0)
        self.assertIn(
            "circuito C1 sem VNOM numérica positiva",
            [issue.reason for issue in result.issues],
        )

    def test_progress_and_cancellation(self) -> None:
        bars = make_bars()
        loads = self._loads(bars)
        patterns = make_patterns(loads)
        events: list[tuple[int, int]] = []

        self._export(
            loads,
            patterns,
            progress=lambda current, total: events.append((current, total)),
        )

        self.assertEqual(events[-1], (1, 1))
        with self.assertRaises(InterruptedError):
            self._export(loads, patterns, cancel_check=lambda: True)


def make_two_circuit_catalog(network: LineNetworkModel) -> CircuitCatalogModel:
    return CircuitCatalogModel.build(
        network,
        None,
        [
            CircuitDefinition("C1", "B0", "ALIMENTADOR", "13,8"),
            CircuitDefinition("C2", "B2", "OUTRO", "13,8"),
        ],
    )


class MasterExportTests(unittest.TestCase):
    def _catalog(self, **kwargs):  # noqa: ANN003, ANN202
        return make_catalog(make_network(make_bars()), **kwargs)

    def test_master_follows_the_opendss_template(self) -> None:
        result = build_master_export(
            self._catalog(),
            [0],
            redirects=[LINES_FILENAME, SWITCHES_FILENAME],
        )

        self.assertFalse(result.has_warnings)
        self.assertEqual(result.master_filename, "ALIMENTADOR_Master.dss")
        self.assertEqual(
            result.text.splitlines(),
            [
                "Clear",
                "Set DefaultBaseFrequency=60",
                "",
                "New Circuit.ALIMENTADOR",
                "~ bus1=BARRA_A.1.2.3 phases=3 basekv=13.8 pu=1 angle=0"
                " frequency=60",
                "~ MVAsc3=999999 MVAsc1=999999",
                "",
                "Redirect trechos.dss",
                "Redirect chaves.dss",
                "",
                "Set Voltagebases=[13.8]",
                "calcvoltagebases",
                "Set mode=daily",
                "Set stepsize=1h",
                "Set number=4",
                "Set time=(0, 0)",
                "Solve",
                "",
                "Buscoords ALIMENTADOR_Buscoords.csv",
            ],
        )

    def test_redirects_follow_the_files_that_were_generated(self) -> None:
        catalog = self._catalog()

        without = build_master_export(catalog, [0], redirects=[])
        with_loads = build_master_export(
            catalog,
            [0],
            redirects=[LINES_FILENAME, SINGLE_PHASE_LOADS_FILENAME],
        )

        self.assertNotIn("Redirect", without.text)
        self.assertEqual(
            [
                line
                for line in with_loads.text.splitlines()
                if line.startswith("Redirect")
            ],
            [f"Redirect {LINES_FILENAME}", f"Redirect {SINGLE_PHASE_LOADS_FILENAME}"],
        )

    def test_buscoords_keeps_full_utm_precision(self) -> None:
        # O _format do módulo (.6g) viraria 8000000.0 em "8e+06".
        result = build_master_export(self._catalog(), [0])

        self.assertEqual(result.bus_count, 3)
        self.assertEqual(
            result.buscoords_text.splitlines(),
            [
                "BARRA_A,500000.000,8000000.000",
                "BARRA_B,500100.000,8000000.000",
                "BARRA_C,500200.000,8000000.000",
            ],
        )

    def test_bus_names_match_the_line_terminals(self) -> None:
        catalog = self._catalog()
        lines = build_line_export(catalog, make_cables(), PHASES, [0])
        master = build_master_export(catalog, [0])

        coordinate_names = {
            line.split(",", 1)[0]
            for line in master.buscoords_text.splitlines()
            if line
        }
        # Toda barra citada em Bus1/Bus2 precisa ter coordenada.
        for entry in data_lines(lines.text):
            for part in entry.split():
                if part.startswith(("Bus1=", "Bus2=")):
                    bus = part.split("=", 1)[1].split(".", 1)[0]
                    self.assertIn(bus, coordinate_names)

    def test_empty_code_falls_back_to_the_circuit_id(self) -> None:
        network = make_network(make_bars())
        catalog = CircuitCatalogModel.build(
            network, None, [CircuitDefinition("C1", "B0", "", "13,8")]
        )

        result = build_master_export(catalog, [0])

        self.assertEqual(result.master_filename, "C1_Master.dss")
        self.assertIn("New Circuit.C1", result.text)
        self.assertEqual(result.discarded_count, 0)
        self.assertTrue(result.has_warnings)

    def test_circuit_without_voltage_produces_no_master(self) -> None:
        result = build_master_export(self._catalog(voltage=""), [0])

        self.assertEqual(result.text, "")
        self.assertEqual(result.buscoords_text, "")
        self.assertIn(
            "circuito sem VNOM numérica positiva (<vazio>); o master não foi "
            "gerado",
            [issue.reason for issue in result.issues],
        )

    def test_more_than_one_circuit_produces_no_master(self) -> None:
        catalog = make_two_circuit_catalog(make_network(make_bars()))

        result = build_master_export(catalog, [0, 1])

        self.assertEqual(result.text, "")
        self.assertIn(
            "2 circuitos selecionados; o master exige exatamente um, porque "
            "um New Circuit energiza um alimentador só",
            [issue.reason for issue in result.issues],
        )

    def test_repeated_bus_name_is_reported_once(self) -> None:
        bars = make_bars(codes=("IGUAL", "IGUAL", "BARRA_C"))
        catalog = make_catalog(make_network(bars))

        result = build_master_export(catalog, [0])

        self.assertEqual(result.bus_count, 2)
        self.assertIn(
            "nome de barra 'IGUAL' já usado pela barra B0; a coordenada foi "
            "descartada",
            [issue.reason for issue in result.issues],
        )

    def test_master_filenames_matches_the_result(self) -> None:
        catalog = self._catalog()

        names = master_filenames(catalog, [0])

        self.assertEqual(
            names, ("ALIMENTADOR_Master.dss", "ALIMENTADOR_Buscoords.csv")
        )
        result = build_master_export(catalog, [0])
        self.assertEqual(
            names, (result.master_filename, result.buscoords_filename)
        )

    def test_master_filenames_is_none_without_a_single_circuit(self) -> None:
        catalog = make_two_circuit_catalog(make_network(make_bars()))

        self.assertIsNone(master_filenames(catalog, [0, 1]))
        self.assertIsNone(master_filenames(catalog, []))


class LoadSettingsInMasterTests(unittest.TestCase):
    """Os BatchEdit dos limites de tensão, e a compatibilidade sem eles.

    ``MasterExportTests.test_master_follows_the_opendss_template`` trava o
    arquivo linha a linha para o caso sem configuração; o que se verifica aqui é
    o caso configurado e a posição exata dos comandos.
    """

    def _catalog(self):  # noqa: ANN202
        return make_catalog(make_network(make_bars()))

    def _enabled(self, **kwargs):  # noqa: ANN003, ANN202
        return OpenDssLoadSettings(voltage_limits_enabled=True, **kwargs)

    def test_no_settings_changes_nothing(self) -> None:
        catalog = self._catalog()

        without = build_master_export(catalog, [0], redirects=[LINES_FILENAME])
        explicit_none = build_master_export(
            catalog,
            [0],
            redirects=[LINES_FILENAME],
            load_settings=None,
        )

        self.assertNotIn("BatchEdit", without.text)
        self.assertEqual(without.text, explicit_none.text)

    def test_disabled_settings_change_nothing(self) -> None:
        catalog = self._catalog()

        result = build_master_export(
            catalog,
            [0],
            redirects=[LINES_FILENAME],
            load_settings=OpenDssLoadSettings(vminpu=0.8, vmaxpu=1.2),
        )

        self.assertNotIn("BatchEdit", result.text)

    def test_commands_sit_between_the_redirects_and_the_voltage_bases(self) -> None:
        result = build_master_export(
            self._catalog(),
            [0],
            redirects=[LINES_FILENAME, SINGLE_PHASE_LOADS_FILENAME],
            load_settings=self._enabled(vminpu=0.8, vmaxpu=1.2),
        )

        lines = [line for line in result.text.splitlines() if line]
        last_redirect = max(
            index for index, line in enumerate(lines) if line.startswith("Redirect")
        )
        first_batch = min(
            index for index, line in enumerate(lines) if line.startswith("BatchEdit")
        )
        voltage_bases = next(
            index
            for index, line in enumerate(lines)
            if line.startswith("Set Voltagebases")
        )

        # BatchEdit é executivo: exige as Load já definidas pelos Redirect, e
        # precisa preceder o Solve.
        self.assertLess(last_redirect, first_batch)
        self.assertLess(first_batch, voltage_bases)

    def test_both_properties_are_emitted_in_order(self) -> None:
        result = build_master_export(
            self._catalog(),
            [0],
            redirects=[LINES_FILENAME],
            load_settings=self._enabled(vminpu=0.8, vmaxpu=1.2),
        )

        self.assertEqual(
            [
                line
                for line in result.text.splitlines()
                if line.startswith("BatchEdit")
            ],
            [
                "BatchEdit Load..* vminpu=0.8",
                "BatchEdit Load..* vmaxpu=1.2",
            ],
        )

    def test_values_never_reach_the_file_with_a_decimal_comma(self) -> None:
        result = build_master_export(
            self._catalog(),
            [0],
            redirects=[LINES_FILENAME],
            load_settings=self._enabled(vminpu=0.875, vmaxpu=1.125),
        )

        for line in result.text.splitlines():
            if line.startswith("BatchEdit"):
                self.assertNotIn(",", line)

    def test_the_master_is_still_valid_without_redirects(self) -> None:
        result = build_master_export(
            self._catalog(),
            [0],
            load_settings=self._enabled(vminpu=0.9, vmaxpu=1.1),
        )

        lines = result.text.splitlines()
        self.assertIn("BatchEdit Load..* vminpu=0.9", lines)
        self.assertLess(
            lines.index("BatchEdit Load..* vminpu=0.9"),
            lines.index("Set Voltagebases=[13.8]"),
        )


class LoadSettingsInBundleTests(unittest.TestCase):
    """``build_export`` só leva a configuração ao master quando há cargas."""

    def setUp(self) -> None:
        self.network = make_network(make_bars())
        self.catalog = make_catalog(self.network)
        self.settings = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.8,
            vmaxpu=1.2,
        )

    def test_bundle_with_loads_carries_the_commands(self) -> None:
        # As cargas precisam pendurar nas barras do catálogo em uso, e não em
        # outras equivalentes: a regra de identidade do projeto é por objeto.
        loads = LoadModel(
            self.network.bars,
            ["CG1"],
            [1],
            ["EXT-1"],
            ["CARGA-1"],
            ["10"],
            ["12"],
            ["220"],
            ["1"],
            ["Y"],
        )
        patterns = LoadPatternModel(
            loads,
            [
                tuple(
                    LoadPatternRecord("CG1", npat, "1", "2", "3", "4", "5", "6")
                    for npat in range(4)
                )
            ],
        )

        bundle = build_export(
            self.catalog,
            make_cables(),
            PHASES,
            [0],
            loads=loads,
            patterns=patterns,
            load_settings=self.settings,
        )

        self.assertIn("BatchEdit Load..* vminpu=0.8", bundle.master.text)

    def test_bundle_without_loads_omits_the_commands(self) -> None:
        # Sem arquivo de carga o comando editaria zero objetos; a DLL tolera,
        # mas o arquivo ficaria enganoso.
        bundle = build_export(
            self.catalog,
            make_cables(),
            PHASES,
            [0],
            load_settings=self.settings,
        )

        self.assertNotIn("BatchEdit", bundle.master.text)

    def test_bundle_without_settings_is_unchanged(self) -> None:
        bundle = build_export(self.catalog, make_cables(), PHASES, [0])

        self.assertNotIn("BatchEdit", bundle.master.text)


class ExportBundleTests(unittest.TestCase):
    def _bundle(self, **switch_kwargs):  # noqa: ANN003, ANN202
        network = make_network(make_bars())
        switches = make_switches(network, **switch_kwargs)
        catalog = make_catalog(network, switches=switches)
        return build_export(catalog, make_cables(), PHASES, [0])

    def test_bundle_carries_only_the_network_files_without_loads(self) -> None:
        bundle = self._bundle()

        self.assertEqual(
            [name for name, _ in bundle.files],
            [
                LINES_FILENAME,
                SWITCHES_FILENAME,
                "ALIMENTADOR_Master.dss",
                "ALIMENTADOR_Buscoords.csv",
            ],
        )
        self.assertEqual(bundle.loads_by_phase_count, ())
        self.assertEqual(bundle.lines.exported_count, 1)
        self.assertEqual(bundle.switches.exported_count, 1)
        self.assertFalse(bundle.has_warnings)

    def _bundle_with_loads(self, loads: LoadModel):  # noqa: ANN202
        network = make_network(loads.bars)
        catalog = make_catalog(network, switches=make_switches(network))
        return build_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            loads=loads,
            patterns=make_patterns(loads),
        )

    def test_bundle_carries_every_load_file_when_patterns_exist(self) -> None:
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2", "CG3"),
            bar_indices=(1, 2, 1),
            codes=("MONO", "BI", "TRI"),
            phases=("1", "7", "13"),
        )

        bundle = self._bundle_with_loads(loads)

        self.assertEqual(
            [name for name, _ in bundle.files],
            [
                LINES_FILENAME,
                SWITCHES_FILENAME,
                SINGLE_PHASE_LOADS_FILENAME,
                TWO_PHASE_LOADS_FILENAME,
                THREE_PHASE_LOADS_FILENAME,
                "ALIMENTADOR_Master.dss",
                "ALIMENTADOR_Buscoords.csv",
            ],
        )
        self.assertEqual(
            [(count, result.exported_count) for count, result in bundle.loads_by_phase_count],
            [(1, 1), (2, 1), (3, 1)],
        )
        self.assertFalse(bundle.has_warnings)

    def test_every_load_file_is_written_even_when_empty(self) -> None:
        # A lista de arquivos gerados não pode depender do conteúdo do CSV.
        loads = make_loads(make_bars())  # só uma carga monofásica

        bundle = self._bundle_with_loads(loads)

        self.assertEqual(len(bundle.files), 7)
        for result in (bundle.two_phase_loads, bundle.three_phase_loads):
            self.assertEqual(result.exported_count, 0)
            self.assertEqual(result.skipped_other_phase_count, 1)
            self.assertEqual(data_lines(result.text), [])

    def test_phase_count_infix_keeps_the_files_from_colliding(self) -> None:
        # Mesmo CODIGO em três contagens de fases: o infixo -NF- separa os
        # nomes, então nenhuma carga é descartada por colisão.
        bars = make_bars()
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2", "CG3"),
            bar_indices=(1, 2, 1),
            codes=("MESMO", "MESMO", "MESMO"),
            phases=("1", "7", "13"),
        )

        bundle = self._bundle_with_loads(loads)

        self.assertFalse(bundle.has_warnings)
        names = sorted(
            name
            for _, result in bundle.loads_by_phase_count
            for name in result.used_names
        )
        self.assertEqual(
            names,
            [
                "MESMO-1F-D",
                "MESMO-2F-D",
                "MESMO-2F-E",
                "MESMO-3F-D",
                "MESMO-3F-E",
                "MESMO-3F-F",
            ],
        )

    def test_patterns_from_another_import_are_refused(self) -> None:
        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network, switches=make_switches(network))
        loads = make_loads(bars)
        other = make_loads(bars)

        bundle = build_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            loads=loads,
            patterns=make_patterns(other),
        )

        # Patamares de outra importação nunca se combinam com estas cargas.
        self.assertEqual(bundle.loads_by_phase_count, ())
        # Rede e master saem mesmo assim: só os arquivos de carga somem.
        self.assertEqual(len(bundle.files), 4)

    def test_switch_name_colliding_with_a_segment_is_discarded(self) -> None:
        # A chave passa a se chamar como o trecho exportado em trechos.dss.
        bundle = self._bundle(codes=("TR-1",))

        self.assertEqual(bundle.lines.exported_count, 1)
        self.assertEqual(bundle.switches.exported_count, 0)
        self.assertTrue(bundle.has_warnings)
        self.assertIn(
            f"nome 'TR-1' já usado por um trecho em {LINES_FILENAME}",
            [issue.reason for issue in bundle.issues],
        )

    def test_bundle_aggregates_diagnostics(self) -> None:
        bundle = self._bundle(states=("X",))

        self.assertEqual(bundle.discarded_count, 0)
        self.assertTrue(bundle.has_warnings)
        self.assertTrue(
            any("ESTADO 'X'" in issue.reason for issue in bundle.issues)
        )


class LineExportControlTests(unittest.TestCase):
    def _wide_network(self, size: int) -> CircuitCatalogModel:
        bars = CircuitModel(
            [f"B{index}" for index in range(size + 1)],
            [f"COD{index}" for index in range(size + 1)],
            [500_000.0 + index for index in range(size + 1)],
            [8_000_000.0] * (size + 1),
            UtmCrs(21, northern=False),
        )
        network = LineNetworkModel(
            bars,
            [f"T{index}" for index in range(size)],
            [""] * size,  # sem CODIGO: cai no TRECHO_ID, gerando um aviso cada
            ["404"] * size,  # FASES2 sem relação: descarta todos
            list(range(size)),
            list(range(1, size + 1)),
            [""] * size,
            ["CB1"] * size,
            [""] * size,
            [10.0] * size,
        )
        return CircuitCatalogModel.build(
            network,
            None,
            [CircuitDefinition("C1", "B0", "A", "13,8")],
        )

    def test_issue_reporting_is_capped(self) -> None:
        catalog = self._wide_network(MAX_REPORTED_ISSUES + 20)

        result = build_line_export(catalog, make_cables(), PHASES, [0])

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(len(result.issues), MAX_REPORTED_ISSUES)
        self.assertEqual(result.omitted_issues, 20)

    def test_progress_and_cancellation(self) -> None:
        catalog = make_catalog(make_network(make_bars()))
        events: list[tuple[int, int]] = []

        build_line_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            progress=lambda current, total: events.append((current, total)),
        )

        self.assertEqual(events[-1], (2, 2))

        with self.assertRaises(InterruptedError):
            build_line_export(
                catalog,
                make_cables(),
                PHASES,
                [0],
                cancel_check=lambda: True,
            )


if __name__ == "__main__":
    unittest.main()
