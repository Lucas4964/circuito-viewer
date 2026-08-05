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
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.opendss_export import (
    FREQUENCY_HZ,
    LINES_FILENAME,
    MAX_REPORTED_ISSUES,
    SINGLE_PHASE_LOADS_FILENAME,
    SWITCHES_FILENAME,
    TWO_PHASE_LOADS_FILENAME,
    build_export,
    build_line_export,
    build_single_phase_load_export,
    build_switch_export,
    build_two_phase_load_export,
    parse_number,
    phase_voltage_kv,
    positive_sequence_capacitance_nf,
    sanitize_dss_name,
)
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


class LoadExportTests(unittest.TestCase):
    def _export(self, loads: LoadModel, patterns: LoadPatternModel, **kwargs):  # noqa: ANN003, ANN202
        catalog = kwargs.pop("catalog", None)
        if catalog is None:
            catalog = make_catalog(make_network(make_bars()))
        return build_single_phase_load_export(catalog, loads, patterns, PHASES, [0], **kwargs)

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
                "New LoadShape.PERFIL-CARGA-1 npts=4 interval=1"
                " mult=[1.500000 2.500000 3.500000 4.500000]"
                " qmult=[0.250000 1.250000 2.250000 3.250000]",
                f"New Load.CARGA-1 phases=1 bus1=BARRA_B.1 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1"
                " class=1",
            ],
        )

    def test_every_shape_precedes_every_load(self) -> None:
        # O daily= referencia um perfil que o OpenDSS precisa já ter definido.
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
        self.assertEqual(
            kinds,
            ["New LoadShape", "New LoadShape", "New Load", "New Load"],
        )

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

        shapes = [
            line for line in data_lines(result.text) if "LoadShape" in line
        ]
        # D consome PD/QD; E consome PE/QE.
        self.assertIn("mult=[1.500000 2.500000 3.500000 4.500000]", shapes[0])
        self.assertIn("qmult=[0.250000 1.250000 2.250000 3.250000]", shapes[0])
        self.assertIn("mult=[2.500000 3.500000 4.500000 5.500000]", shapes[1])
        self.assertIn("qmult=[0.350000 1.350000 2.350000 3.350000]", shapes[1])

    def test_neutral_phase_uses_the_same_column_and_keeps_the_node(self) -> None:
        bars = make_bars()
        loads = make_loads(bars, codes=("COM-NEUTRO",), phases=("4",))

        result = self._export(loads, make_patterns(loads))

        entry = data_lines(result.text)[1]
        # DN é monofásica: nó de neutro preservado e as colunas de D.
        self.assertIn("bus1=BARRA_B.1.0", entry)
        self.assertIn("phases=1", entry)
        self.assertIn(
            "mult=[1.500000 2.500000 3.500000 4.500000]", data_lines(result.text)[0]
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

        shape = data_lines(result.text)[0]
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

    def test_polyphase_loads_are_counted_without_diagnostics(self) -> None:
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
            ["patamar com PD não numérico"],
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
        self.assertIn(
            "FASES2 '5' com NOME 'SEM_LETRA' fora de D, E ou F", reasons
        )

    def test_empty_code_falls_back_to_the_load_id(self) -> None:
        bars = make_bars()
        loads = make_loads(bars, codes=("",))

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn("New Load.CG1 ", result.text)
        self.assertIn("daily=PERFIL-CG1", result.text)
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
            "nome 'IGUAL' já usado pela carga CG1",
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

        result = build_single_phase_load_export(
            catalog, loads, make_patterns(loads), PHASES, [1]
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

        result = build_single_phase_load_export(
            catalog, loads, make_patterns(loads), PHASES, [0, 1]
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

        result = build_single_phase_load_export(
            catalog, loads, make_patterns(loads), PHASES, [0]
        )

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
    def _export(self, loads: LoadModel, patterns: LoadPatternModel, **kwargs):  # noqa: ANN003, ANN202
        catalog = kwargs.pop("catalog", None)
        configuration = kwargs.pop("configuration", PHASES)
        if catalog is None:
            catalog = make_catalog(make_network(make_bars()))
        return build_two_phase_load_export(
            catalog, loads, patterns, configuration, [0], **kwargs
        )

    def _two_phase_loads(self, bars: CircuitModel, phases: str) -> LoadModel:
        return make_loads(bars, phases=(phases,))

    def test_one_load_becomes_two_independent_single_phase_loads(self) -> None:
        bars = make_bars()
        loads = self._two_phase_loads(bars, "7")  # DE

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertFalse(result.has_warnings)
        kv = phase_voltage_kv(13.8)
        self.assertEqual(
            data_lines(result.text),
            [
                "New LoadShape.PERFIL-CARGA-1-D npts=4 interval=1"
                " mult=[1.500000 2.500000 3.500000 4.500000]"
                " qmult=[0.250000 1.250000 2.250000 3.250000]",
                "New LoadShape.PERFIL-CARGA-1-E npts=4 interval=1"
                " mult=[2.500000 3.500000 4.500000 5.500000]"
                " qmult=[0.350000 1.350000 2.350000 3.350000]",
                f"New Load.CARGA-1-D phases=1 bus1=BARRA_B.1 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-D"
                " class=2",
                f"New Load.CARGA-1-E phases=1 bus1=BARRA_B.2 conn=wye"
                f" kV={kv:.6g} model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-E"
                " class=2",
            ],
        )

    def test_terminals_come_from_the_single_phase_entries(self) -> None:
        # "FD" tem DSS "1.3": parear posicionalmente daria F=1 e D=3. O terminal
        # de cada letra vem da entrada monofásica, então F=3 e D=1.
        bars = make_bars()
        loads = self._two_phase_loads(bars, "9")  # FD

        result = self._export(loads, make_patterns(loads))

        entries = [line for line in data_lines(result.text) if " Load." in line]
        self.assertEqual(
            [line.split(" bus1=")[1].split(" ")[0] for line in entries],
            ["BARRA_B.3", "BARRA_B.1"],
        )
        # A ordem das fases segue as letras do NOME, não a ordem dos nós.
        self.assertIn("New Load.CARGA-1-F ", entries[0])
        self.assertIn("New Load.CARGA-1-D ", entries[1])

    def test_each_phase_reads_its_own_pattern_columns(self) -> None:
        bars = make_bars()
        loads = self._two_phase_loads(bars, "8")  # EF

        result = self._export(loads, make_patterns(loads))

        shapes = [
            line for line in data_lines(result.text) if "LoadShape" in line
        ]
        # E consome PE/QE; F consome PF/QF.
        self.assertIn("PERFIL-CARGA-1-E", shapes[0])
        self.assertIn("mult=[2.500000 3.500000 4.500000 5.500000]", shapes[0])
        self.assertIn("qmult=[0.350000 1.350000 2.350000 3.350000]", shapes[0])
        self.assertIn("PERFIL-CARGA-1-F", shapes[1])
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
        loads = self._two_phase_loads(bars, "7")
        groups = {0: tuple(("0",) * 6 for _ in range(4))}

        result = self._export(loads, make_patterns(loads, groups=groups))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn(
            "mult=[0.000000 0.000000 0.000000 0.000000]", result.text
        )

    def test_one_invalid_phase_discards_the_whole_load(self) -> None:
        bars = make_bars()
        loads = self._two_phase_loads(bars, "7")  # DE
        # PD válido, PE vazio: nenhuma das duas fases pode sair.
        groups = {0: tuple(("1", "", "0", "1", "1", "1") for _ in range(4))}

        result = self._export(loads, make_patterns(loads, groups=groups))

        self.assertEqual(result.exported_count, 0)
        self.assertEqual(data_lines(result.text), [])
        self.assertEqual(
            [issue.reason for issue in result.issues],
            [
                "patamar com PE não numérico; a carga bifásica inteira foi "
                "descartada"
            ],
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
        loads = self._two_phase_loads(bars, "10")  # NOME "XY"

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 0)
        self.assertIn(
            "FASES2 '10' com NOME 'XY' não resolve duas fases distintas "
            "entre D, E e F",
            [issue.reason for issue in result.issues],
        )

    def test_letter_without_a_terminal_is_reported(self) -> None:
        bars = make_bars()
        loads = self._two_phase_loads(bars, "8")  # EF, sem entrada de F

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

    def test_empty_code_falls_back_to_the_load_id(self) -> None:
        bars = make_bars()
        loads = make_loads(bars, codes=("",), phases=("7",))

        result = self._export(loads, make_patterns(loads))

        self.assertEqual(result.exported_count, 1)
        self.assertEqual(result.discarded_count, 0)
        self.assertIn("New Load.CG1-D ", result.text)
        self.assertIn("New Load.CG1-E ", result.text)
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
            "nome 'IGUAL-D' já usado pela carga CG1; a carga bifásica inteira "
            "foi descartada",
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
        loads = self._two_phase_loads(network.bars, "7")

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
        loads = self._two_phase_loads(bars, "7")

        result = build_two_phase_load_export(
            catalog, loads, make_patterns(loads), PHASES, [0, 1]
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
        loads = self._two_phase_loads(bars, "7")
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
            [LINES_FILENAME, SWITCHES_FILENAME],
        )
        self.assertIsNone(bundle.single_phase_loads)
        self.assertIsNone(bundle.two_phase_loads)
        self.assertEqual(bundle.lines.exported_count, 1)
        self.assertEqual(bundle.switches.exported_count, 1)
        self.assertFalse(bundle.has_warnings)

    def test_bundle_carries_both_load_files_when_patterns_exist(self) -> None:
        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network, switches=make_switches(network))
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("MONO", "BI"),
            phases=("1", "7"),
        )

        bundle = build_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            loads=loads,
            patterns=make_patterns(loads),
        )

        self.assertEqual(
            [name for name, _ in bundle.files],
            [
                LINES_FILENAME,
                SWITCHES_FILENAME,
                SINGLE_PHASE_LOADS_FILENAME,
                TWO_PHASE_LOADS_FILENAME,
            ],
        )
        self.assertEqual(bundle.single_phase_loads.exported_count, 1)
        self.assertEqual(bundle.two_phase_loads.exported_count, 1)
        self.assertFalse(bundle.has_warnings)

    def test_both_load_files_are_written_even_when_one_is_empty(self) -> None:
        # A lista de arquivos gerados não pode depender do conteúdo do CSV.
        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network, switches=make_switches(network))
        loads = make_loads(bars)  # só uma carga monofásica

        bundle = build_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            loads=loads,
            patterns=make_patterns(loads),
        )

        self.assertEqual(len(bundle.files), 4)
        self.assertEqual(bundle.two_phase_loads.exported_count, 0)
        self.assertEqual(bundle.two_phase_loads.skipped_other_phase_count, 1)
        self.assertEqual(data_lines(bundle.two_phase_loads.text), [])

    def test_two_phase_name_colliding_with_a_single_phase_is_discarded(self) -> None:
        bars = make_bars()
        network = make_network(bars)
        catalog = make_catalog(network, switches=make_switches(network))
        # A monofásica se chama como a primeira fase da bifásica.
        loads = make_loads(
            bars,
            load_ids=("CG1", "CG2"),
            bar_indices=(1, 2),
            codes=("BI-D", "BI"),
            phases=("1", "7"),
        )

        bundle = build_export(
            catalog,
            make_cables(),
            PHASES,
            [0],
            loads=loads,
            patterns=make_patterns(loads),
        )

        self.assertEqual(bundle.single_phase_loads.exported_count, 1)
        self.assertEqual(bundle.two_phase_loads.exported_count, 0)
        self.assertIn(
            f"nome 'BI-D' já usado por uma carga em "
            f"{SINGLE_PHASE_LOADS_FILENAME}; a carga bifásica inteira foi "
            "descartada",
            [issue.reason for issue in bundle.issues],
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
        self.assertIsNone(bundle.single_phase_loads)
        self.assertIsNone(bundle.two_phase_loads)

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
