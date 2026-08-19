"""Exportação OpenDSS isolada da projeção simplificada por ramais."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

from .branch_analysis import BranchType
from .equivalent_network import EquivalentNetworkResult
from .model import (
    CableModel,
    CapacitorModel,
    CircuitCatalogModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
)
from .opendss_export import (
    CAPACITORS_FILENAME,
    GENERATOR_PHASE_COUNTS,
    LINES_FILENAME,
    LOAD_PATTERN_COUNT,
    LOAD_PHASE_COUNTS,
    LOAD_SHAPE_PREFIX,
    REGULATORS_FILENAME,
    SINGLE_PHASE_GENERATORS_FILENAME,
    SINGLE_PHASE_LOADS_FILENAME,
    SWITCHES_FILENAME,
    THREE_PHASE_GENERATORS_FILENAME,
    THREE_PHASE_LOADS_FILENAME,
    TWO_PHASE_GENERATORS_FILENAME,
    TWO_PHASE_LOADS_FILENAME,
    OpenDssCapacitorExportResult,
    OpenDssExportIssue,
    OpenDssGeneratorExportResult,
    OpenDssLineExportResult,
    OpenDssLoadExportResult,
    OpenDssMasterExportResult,
    OpenDssRegulatorExportResult,
    OpenDssSwitchExportResult,
    _entries_by_value,
    _format,
    _format_pattern,
    _phase_letters,
    _phase_nodes,
    _terminals_by_phase_letter,
    build_capacitor_export,
    build_generator_export,
    build_line_export,
    build_load_export,
    build_master_export,
    build_regulator_export,
    build_switch_export,
    bus_namer,
    parse_number,
    phase_voltage_kv,
    sanitize_dss_name,
)
from .phase_config import PhaseConfiguration

if TYPE_CHECKING:
    from .generator_update import GeneratorUpdateModel
    from .opendss_settings import OpenDssLoadSettings


SINGLE_PHASE_BRANCHES_FILENAME = "ramalmonofasico.dss"
TWO_PHASE_BRANCHES_FILENAME = "ramalbifasico.dss"
_BRANCH_FILES = {
    1: (SINGLE_PHASE_BRANCHES_FILENAME, "monofásicos"),
    2: (TWO_PHASE_BRANCHES_FILENAME, "bifásicos"),
}
_LOAD_FILES = {
    1: SINGLE_PHASE_LOADS_FILENAME,
    2: TWO_PHASE_LOADS_FILENAME,
    3: THREE_PHASE_LOADS_FILENAME,
}
_GENERATOR_FILES = {
    1: SINGLE_PHASE_GENERATORS_FILENAME,
    2: TWO_PHASE_GENERATORS_FILENAME,
    3: THREE_PHASE_GENERATORS_FILENAME,
}
_FIELD_BY_PHASE = {
    "D": ("pd", "qd"),
    "E": ("pe", "qe"),
    "F": ("pf", "qf"),
}


class SimplifiedOpenDssExportError(ValueError):
    """Falha de pré-validação que impede qualquer gravação simplificada."""


@dataclass(frozen=True, slots=True)
class OpenDssBranchExportResult:
    text: str
    exported_count: int
    skipped_other_phase_count: int
    zero_count: int
    discarded_count: int
    issues: tuple[OpenDssExportIssue, ...]
    omitted_issues: int
    used_names: frozenset[str] = frozenset()

    @property
    def has_warnings(self) -> bool:
        return self.discarded_count > 0 or bool(self.issues)


@dataclass(frozen=True, slots=True)
class SimplifiedOpenDssExportBundle:
    lines: OpenDssLineExportResult
    switches: OpenDssSwitchExportResult
    single_phase_branches: OpenDssBranchExportResult
    two_phase_branches: OpenDssBranchExportResult
    regulators: OpenDssRegulatorExportResult | None = None
    single_phase_loads: OpenDssLoadExportResult | None = None
    two_phase_loads: OpenDssLoadExportResult | None = None
    three_phase_loads: OpenDssLoadExportResult | None = None
    single_phase_generators: OpenDssGeneratorExportResult | None = None
    two_phase_generators: OpenDssGeneratorExportResult | None = None
    three_phase_generators: OpenDssGeneratorExportResult | None = None
    capacitors: OpenDssCapacitorExportResult | None = None
    master: OpenDssMasterExportResult | None = None

    @property
    def loads_by_phase_count(self) -> tuple[tuple[int, OpenDssLoadExportResult], ...]:
        return tuple(
            (count, result)
            for count, result in (
                (1, self.single_phase_loads),
                (2, self.two_phase_loads),
                (3, self.three_phase_loads),
            )
            if result is not None
        )

    @property
    def generators_by_phase_count(
        self,
    ) -> tuple[tuple[int, OpenDssGeneratorExportResult], ...]:
        return tuple(
            (count, result)
            for count, result in (
                (1, self.single_phase_generators),
                (2, self.two_phase_generators),
                (3, self.three_phase_generators),
            )
            if result is not None
        )

    @property
    def branches_by_phase_count(self) -> tuple[tuple[int, OpenDssBranchExportResult], ...]:
        return (
            (1, self.single_phase_branches),
            (2, self.two_phase_branches),
        )

    @property
    def element_files(self) -> tuple[tuple[str, str], ...]:
        regulators = self.regulators
        capacitors = self.capacitors
        return (
            (LINES_FILENAME, self.lines.text),
            (SWITCHES_FILENAME, self.switches.text),
            *(
                ()
                if regulators is None or not regulators.exported_count
                else ((REGULATORS_FILENAME, regulators.text),)
            ),
            *(
                (_LOAD_FILES[count], result.text)
                for count, result in self.loads_by_phase_count
            ),
            *(
                (_GENERATOR_FILES[count], result.text)
                for count, result in self.generators_by_phase_count
            ),
            *(
                ()
                if capacitors is None or not capacitors.exported_count
                else ((CAPACITORS_FILENAME, capacitors.text),)
            ),
            (SINGLE_PHASE_BRANCHES_FILENAME, self.single_phase_branches.text),
            (TWO_PHASE_BRANCHES_FILENAME, self.two_phase_branches.text),
        )

    @property
    def files(self) -> tuple[tuple[str, str], ...]:
        if self.master is None or not self.master.text:
            return self.element_files
        return (
            *self.element_files,
            (self.master.master_filename, self.master.text),
            (self.master.buscoords_filename, self.master.buscoords_text),
        )

    @property
    def issues(self) -> tuple[OpenDssExportIssue, ...]:
        groups = (
            self.lines,
            self.switches,
            *(() if self.regulators is None else (self.regulators,)),
            *(result for _, result in self.loads_by_phase_count),
            *(result for _, result in self.generators_by_phase_count),
            *(() if self.capacitors is None else (self.capacitors,)),
            self.single_phase_branches,
            self.two_phase_branches,
            *((self.master,) if self.master is not None else ()),
        )
        return tuple(issue for group in groups for issue in group.issues)

    @property
    def omitted_issues(self) -> int:
        groups = (
            self.lines,
            self.switches,
            *(() if self.regulators is None else (self.regulators,)),
            *(result for _, result in self.loads_by_phase_count),
            *(result for _, result in self.generators_by_phase_count),
            *(() if self.capacitors is None else (self.capacitors,)),
            self.single_phase_branches,
            self.two_phase_branches,
            *((self.master,) if self.master is not None else ()),
        )
        return sum(group.omitted_issues for group in groups)

    @property
    def has_warnings(self) -> bool:
        return bool(self.issues) or self.omitted_issues > 0


def simplified_export_directory_name(
    catalog: CircuitCatalogModel,
    circuit_index: int,
) -> str:
    definition = catalog.definition(int(circuit_index))
    base = sanitize_dss_name(definition.code) or sanitize_dss_name(
        definition.circuit_id
    )
    return f"{base}_Rede_Simplificada"


def _format_incomplete_error(records) -> SimplifiedOpenDssExportError:  # noqa: ANN001
    identifiers = ", ".join(f"RAMAL-{record.branch_id}" for record in records)
    return SimplifiedOpenDssExportError(
        "A exportação simplificada foi bloqueada porque há ramais com "
        f"equivalência elétrica incompleta: {identifiers}. "
        "Consulte os diagnósticos da ferramenta Ramais."
    )


def build_branch_export(
    catalog: CircuitCatalogModel,
    equivalent: EquivalentNetworkResult,
    phase_configuration: PhaseConfiguration,
    circuit_index: int,
    *,
    phase_count: int,
    reserved_names: frozenset[str] = frozenset(),
    cancel_check: Callable[[], bool] | None = None,
) -> OpenDssBranchExportResult:
    if phase_count not in _BRANCH_FILES:
        raise ValueError(f"Contagem de fases sem arquivo de ramal: {phase_count}")
    model = equivalent.model
    entries = _entries_by_value(phase_configuration)
    terminals = _terminals_by_phase_letter(phase_configuration)
    bus_name = bus_namer(catalog)
    definition = catalog.definition(circuit_index)
    nominal_voltage = parse_number(definition.nominal_voltage)
    issues: list[OpenDssExportIssue] = []
    omitted = 0
    discarded = 0
    shapes: list[str] = []
    loads: list[str] = []
    used: set[str] = set()
    used_folded: set[str] = set()
    reserved_folded = {name.casefold() for name in reserved_names}
    exported = 0
    skipped = 0
    zero = 0

    def report(element_id: str, reason: str) -> None:
        nonlocal omitted, discarded
        discarded += 1
        if len(issues) < 200:
            issues.append(OpenDssExportIssue(element_id, reason))
        else:
            omitted += 1

    for equivalent_index, record in enumerate(model.records):
        if record.circuit_index != circuit_index:
            continue
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Exportação simplificada cancelada.")
        record_phase_count = (
            1 if record.branch_type is BranchType.MONOPHASIC else 2
        )
        if record_phase_count != phase_count:
            skipped += 1
            continue
        if record.is_zero:
            zero += 1
            continue
        if not record.electrical_complete:
            report(record.load_id, "equivalência elétrica incompleta")
            continue
        patterns = model.records_for_load(equivalent_index)
        if len(patterns) != LOAD_PATTERN_COUNT:
            report(record.load_id, "sem os quatro patamares equivalentes")
            continue
        entry = entries.get(record.phases2.strip().casefold())
        if entry is None or entry.phase_count != phase_count:
            report(record.load_id, f"FASES2 '{record.phases2}' incompatível")
            continue
        letters = _phase_letters(entry.name, phase_count)
        if letters is None:
            report(record.load_id, f"FASES2 '{record.phases2}' não resolve as fases")
            continue
        nodes, node_error = _phase_nodes(entry, letters, terminals)
        if node_error is not None or nodes is None:
            report(record.load_id, node_error or "terminais DSS indisponíveis")
            continue
        absent_fields = [
            field
            for letter, fields in _FIELD_BY_PHASE.items()
            if letter not in letters
            for field in fields
        ]
        incompatible = next(
            (
                (item.npat, field.upper())
                for item in patterns
                for field in absent_fields
                if Decimal(getattr(item, field)) != 0
            ),
            None,
        )
        if incompatible is not None:
            npat, field = incompatible
            report(
                record.load_id,
                f"{field} não nulo no NPAT {npat}, fora das fases do ramal",
            )
            continue
        if nominal_voltage is None or nominal_voltage <= 0:
            report(record.load_id, f"circuito {record.circuit_id} sem VNOM positiva")
            continue

        phase_names = [
            f"RAMAL-{record.branch_id}-{phase_count}F-{letter}"
            for letter in letters
        ]
        collision = next(
            (
                name
                for name in phase_names
                if name.casefold() in reserved_folded
                or name.casefold() in used_folded
            ),
            None,
        )
        if collision is not None:
            report(record.load_id, f"nome OpenDSS '{collision}' já utilizado")
            continue

        voltage = _format(phase_voltage_kv(nominal_voltage))
        bus = bus_name(record.bar_index)
        for name, node, letter in zip(phase_names, nodes, letters, strict=True):
            active_field, reactive_field = _FIELD_BY_PHASE[letter]
            active = [Decimal(getattr(item, active_field)) for item in patterns]
            reactive = [Decimal(getattr(item, reactive_field)) for item in patterns]
            shape_name = f"{LOAD_SHAPE_PREFIX}{name}"
            shapes.append(
                f"New LoadShape.{shape_name} npts={LOAD_PATTERN_COUNT} interval=1"
                f" mult=[{' '.join(_format_pattern(float(value)) for value in active)}]"
                f" qmult=[{' '.join(_format_pattern(float(value)) for value in reactive)}]"
            )
            loads.append(
                f"New Load.{name} phases=1 bus1={bus}.{node} conn=wye"
                f" kV={voltage} model=1 kW=1 kvar=1 daily={shape_name}"
                f" class={phase_count}"
            )
            used.add(name)
            used_folded.add(name.casefold())
        exported += 1

    label = _BRANCH_FILES[phase_count][1]
    header = (
        f"! Cargas equivalentes de ramais {label}",
        "! Potencias liquidas: consumo positivo e geracao negativa",
        "! Cada fase usa um LoadShape de quatro patamares",
        "",
    )
    body = (*shapes, *(('',) if shapes and loads else ()), *loads)
    text = "\n".join((*header, *body))
    if body:
        text += "\n"
    return OpenDssBranchExportResult(
        text,
        exported,
        skipped,
        zero,
        discarded,
        tuple(issues),
        omitted,
        frozenset(used),
    )


def build_simplified_export(
    catalog: CircuitCatalogModel,
    cables: CableModel,
    phase_configuration: PhaseConfiguration,
    circuit_indices: Sequence[int] | Iterable[int],
    *,
    equivalent: EquivalentNetworkResult,
    loads: LoadModel | None = None,
    patterns: LoadPatternModel | None = None,
    generator_updates: GeneratorUpdateModel | None = None,
    regulators: RegulatorModel | None = None,
    capacitors: CapacitorModel | None = None,
    load_settings: OpenDssLoadSettings | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> SimplifiedOpenDssExportBundle:
    """Monta a exportação simplificada sem passar pelo bundle convencional."""

    selected = tuple(int(index) for index in circuit_indices)
    if len(selected) != 1:
        raise SimplifiedOpenDssExportError(
            "A exportação simplificada exige exatamente um circuito."
        )
    circuit_index = selected[0]
    if not 0 <= circuit_index < len(catalog):
        raise IndexError(circuit_index)
    model = equivalent.model
    if model.catalog is not catalog:
        raise SimplifiedOpenDssExportError(
            "A rede simplificada pertence a outro catálogo de circuitos."
        )
    if model.source_loads is not loads:
        raise SimplifiedOpenDssExportError(
            "A rede simplificada pertence a outro modelo de cargas."
        )
    if loads is not None and (patterns is None or patterns.loads is not loads):
        raise SimplifiedOpenDssExportError(
            "Importe os quatro patamares das cargas antes da exportação simplificada."
        )
    if generator_updates is not model.source_generator_updates:
        raise SimplifiedOpenDssExportError(
            "Atualize novamente os geradores antes da exportação simplificada."
        )

    selected_records = [
        record for record in model.records if record.circuit_index == circuit_index
    ]
    incomplete = [
        record for record in selected_records if not record.electrical_complete
    ]
    if incomplete:
        raise _format_incomplete_error(incomplete)

    retained_segments = frozenset(
        int(value) for value in model.retained_segment_indices(circuit_index)
    )
    retained_bars = frozenset(
        int(value) for value in model.retained_bar_indices(circuit_index)
    )
    segments = catalog.segments
    dangling = [
        segments.segment_ids[index]
        for index in retained_segments
        if int(segments.start_indices[index]) not in retained_bars
        or int(segments.end_indices[index]) not in retained_bars
    ]
    if dangling:
        raise SimplifiedOpenDssExportError(
            "A projeção simplificada contém trechos ligados a barras removidas: "
            + ", ".join(dangling[:20])
        )

    regulator_result = (
        None
        if regulators is None
        else build_regulator_export(
            catalog,
            regulators,
            phase_configuration,
            selected,
            include_segments=retained_segments,
            cancel_check=cancel_check,
            progress=progress,
        )
    )
    line_result = build_line_export(
        catalog,
        cables,
        phase_configuration,
        selected,
        include_segments=retained_segments,
        skip_segments=(
            frozenset()
            if regulator_result is None
            else regulator_result.replaced_segments
        ),
        cancel_check=cancel_check,
        progress=progress,
    )
    switch_result = build_switch_export(
        catalog,
        phase_configuration,
        selected,
        include_segments=retained_segments,
        reserved_names=line_result.used_names,
        cancel_check=cancel_check,
        progress=progress,
    )

    load_results: dict[int, OpenDssLoadExportResult] = {}
    reserved_load_names: frozenset[str] = frozenset()
    if loads is not None and patterns is not None:
        reduced = set(int(value) for value in model.reduced_load_indices(circuit_index))
        outside_loads = frozenset(index for index in range(len(loads)) if index not in reduced)
        for count in LOAD_PHASE_COUNTS:
            result = build_load_export(
                catalog,
                loads,
                patterns,
                phase_configuration,
                selected,
                phase_count=count,
                reserved_names=reserved_load_names,
                include_load_indices=outside_loads,
                cancel_check=cancel_check,
                progress=progress,
            )
            reserved_load_names |= result.used_names
            load_results[count] = result

    generator_results: dict[int, OpenDssGeneratorExportResult] = {}
    reserved = reserved_load_names
    if generator_updates is not None:
        reduced = set(
            int(value) for value in model.reduced_generator_indices(circuit_index)
        )
        outside_generators = frozenset(
            index
            for index in range(len(generator_updates.generators))
            if index not in reduced
        )
        for count in GENERATOR_PHASE_COUNTS:
            result = build_generator_export(
                catalog,
                generator_updates,
                selected,
                phase_count=count,
                reserved_names=reserved,
                include_generator_indices=outside_generators,
                cancel_check=cancel_check,
                progress=progress,
            )
            reserved |= result.used_names
            generator_results[count] = result

    source_names = [
        name
        for result in (*load_results.values(), *generator_results.values())
        for name in result.used_names
    ]
    seen_folded: dict[str, str] = {}
    case_collision: tuple[str, str] | None = None
    for name in source_names:
        previous = seen_folded.setdefault(name.casefold(), name)
        if previous != name:
            case_collision = (previous, name)
            break
    if case_collision is not None:
        first, second = case_collision
        raise SimplifiedOpenDssExportError(
            "A exportação simplificada foi bloqueada porque os nomes OpenDSS "
            f"'{first}' e '{second}' diferem apenas em maiúsculas/minúsculas."
        )

    branch_results: dict[int, OpenDssBranchExportResult] = {}
    for count in (1, 2):
        result = build_branch_export(
            catalog,
            equivalent,
            phase_configuration,
            circuit_index,
            phase_count=count,
            reserved_names=reserved,
            cancel_check=cancel_check,
        )
        if result.discarded_count:
            raise SimplifiedOpenDssExportError(
                "A exportação simplificada foi bloqueada: "
                + "; ".join(
                    f"{issue.segment_id}: {issue.reason}" for issue in result.issues
                )
            )
        reserved |= result.used_names
        branch_results[count] = result

    capacitor_result: OpenDssCapacitorExportResult | None = None
    if capacitors is not None:
        # Só os bancos que sobreviveram à redução, isto é, os do tronco.
        # Um banco dentro de um ramal foi absorvido pela carga equivalente,
        # que não representa compensação reativa — ele sai da exportação
        # com ocorrência registrada, nunca em silêncio.
        capacitor_result = build_capacitor_export(
            catalog,
            capacitors,
            phase_configuration,
            selected,
            reserved_names=reserved,
            include_bar_indices=retained_bars,
            cancel_check=cancel_check,
            progress=progress,
        )
        reserved |= capacitor_result.used_names

    partial = SimplifiedOpenDssExportBundle(
        line_result,
        switch_result,
        branch_results[1],
        branch_results[2],
        regulator_result,
        load_results.get(1),
        load_results.get(2),
        load_results.get(3),
        generator_results.get(1),
        generator_results.get(2),
        generator_results.get(3),
        capacitor_result,
    )
    master = build_master_export(
        catalog,
        selected,
        redirects=[name for name, _ in partial.element_files],
        load_settings=(
            load_settings
            if load_results
            or generator_results
            or capacitor_result
            or any(
                result.exported_count for result in branch_results.values()
            )
            else None
        ),
        include_bar_indices=retained_bars,
    )
    if not master.text:
        raise SimplifiedOpenDssExportError(
            "O master da rede simplificada não pôde ser gerado: "
            + "; ".join(issue.reason for issue in master.issues)
        )
    return SimplifiedOpenDssExportBundle(
        line_result,
        switch_result,
        branch_results[1],
        branch_results[2],
        regulator_result,
        load_results.get(1),
        load_results.get(2),
        load_results.get(3),
        generator_results.get(1),
        generator_results.get(2),
        generator_results.get(3),
        capacitor_result,
        master,
    )
