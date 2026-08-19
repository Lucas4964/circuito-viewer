"""Exporta quatro circuitos snapshot com alocação nativa do OpenDSS."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .allocation import (
    PHASE_LETTERS,
    TransformerAllocationModel,
    TransformerAllocationRecord,
)
from .allocation_measurements import AllocationMeasurementModel
from .calculation_levels import CalculationLevelSchedule
from .curvas import Curve
from .generator_update import curve_value_at_reference
from .model import CableModel, CircuitCatalogModel, RegulatorModel
from .opendss_export import (
    CONTROL_MODE,
    FREQUENCY_HZ,
    MAX_CONTROL_ITER,
    SOURCE_SHORT_CIRCUIT_MVA,
    OpenDssExportBundle,
    build_export,
    bus_namer,
    parse_number,
    phase_voltage_kv,
    sanitize_dss_name,
)
from .opendss_library import OpenDssLibraryCatalog
from .opendss_line_mode import OpenDssLineParameterMode
from .opendss_mapping_store import OpenDssLibraryMappings
from .opendss_settings import OpenDssLoadSettings
from .opendss_allocation_settings import OpenDssAllocationSettings
from .phase_config import PhaseConfiguration


ENERGY_LOADS_FILENAME = "cargas_energia.dss"
BT_GENERATION_FILENAME = "geracao_bt.dss"
MT_GENERATION_FILENAME = "geracao_mt.dss"
HEAD_LINE_R1 = 0.0001
HEAD_LINE_X1 = 0.0001
HEAD_LINE_R0 = 0.0003
HEAD_LINE_X0 = 0.0003

CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]


class OpenDssAllocationExportError(ValueError):
    """Insumos incompatíveis ou incompletos para a exportação."""


@dataclass(frozen=True, slots=True)
class AllocationLevelExport:
    npat: int
    level_name: str
    directory_name: str
    master_filename: str
    files: tuple[tuple[str, str], ...]
    transformer_count: int
    load_count: int


@dataclass(frozen=True, slots=True)
class _ResolvedAllocationTransformer:
    load_index: int
    base: str
    bus: str
    letters: tuple[str, ...]
    nodes: tuple[str, ...]
    record: TransformerAllocationRecord


@dataclass(frozen=True, slots=True)
class OpenDssAllocationExportBundle:
    circuit_id: str
    levels: tuple[AllocationLevelExport, ...]
    network: OpenDssExportBundle
    warnings: tuple[str, ...] = ()
    skipped_transformer_count: int = 0

    def __post_init__(self) -> None:
        if tuple(level.npat for level in self.levels) != (0, 1, 2, 3):
            raise ValueError("A exportação deve conter NPAT 0 a 3.")

    @property
    def relative_files(self) -> tuple[tuple[Path, str], ...]:
        return tuple(
            (Path(level.directory_name) / filename, text)
            for level in self.levels
            for filename, text in level.files
        )

    @property
    def directory_names(self) -> tuple[str, ...]:
        return tuple(level.directory_name for level in self.levels)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings) or self.network.has_warnings


def allocation_export_directory_names(
    catalog: CircuitCatalogModel,
    circuit_index: int,
    schedule: CalculationLevelSchedule,
) -> tuple[str, ...]:
    """Nomes dos quatro destinos, disponíveis antes de montar os arquivos."""

    definition = catalog.definition(circuit_index)
    base = sanitize_dss_name(definition.code) or sanitize_dss_name(
        definition.circuit_id
    )
    return tuple(
        f"OpenDSS_{base}_NPAT{level.npat}_"
        f"{sanitize_dss_name(level.name) or f'NPAT{level.npat}'}"
        for level in schedule.levels
    )


def _format(value: float) -> str:
    if value == 0.0:
        return "0"
    return f"{value:.6g}"


def _validate_synthetic_names(
    catalog: CircuitCatalogModel,
    circuit_index: int,
    schedule: CalculationLevelSchedule,
    network: OpenDssExportBundle,
) -> None:
    """Impede a cabeça sintética de sobrescrever uma linha ou barra real."""

    definition = catalog.definition(circuit_index)
    base = sanitize_dss_name(definition.code) or sanitize_dss_name(
        definition.circuit_id
    )
    root_index = catalog.segments.bars.index_for_id(definition.root_bar_id)
    assert root_index is not None
    root_bus = bus_namer(catalog)(root_index)
    line_names = {
        name.casefold()
        for name in (*network.lines.used_names, *network.switches.used_names)
    }
    all_buses = {
        bus_namer(catalog)(index).casefold()
        for index in range(len(catalog.segments.bars))
    }
    for level in schedule.levels:
        circuit_name = f"{base}_NPAT{level.npat}"
        head_name = f"ALLOC-HEAD-{circuit_name}"
        if head_name.casefold() in line_names:
            raise OpenDssAllocationExportError(
                f"A linha sintética '{head_name}' colide com uma Line da rede."
            )
        source_bus = f"{root_bus}_SOURCE_NPAT{level.npat}"
        if source_bus.casefold() in all_buses:
            raise OpenDssAllocationExportError(
                f"A barra sintética '{source_bus}' colide com uma barra da rede."
            )


def _selected_load_indices(
    catalog: CircuitCatalogModel,
    allocations: TransformerAllocationModel,
    circuit_index: int,
) -> tuple[int, ...]:
    membership = catalog.membership(circuit_index)
    selected_bars = frozenset(int(value) for value in membership.bar_indices)
    selected: list[int] = []
    for load_index, raw_bar in enumerate(allocations.loads.bar_indices):
        bar_index = int(raw_bar)
        if bar_index not in selected_bars:
            continue
        selected.append(load_index)
    if not selected:
        raise OpenDssAllocationExportError(
            "O circuito selecionado não possui transformadores para alocação."
        )
    return tuple(selected)


def _phase_nodes(
    configuration: PhaseConfiguration,
    raw_phases: str,
    letters: tuple[str, ...],
) -> tuple[str, ...]:
    by_value = {entry.fases2: entry for entry in configuration.entries}
    entry = by_value.get(str(raw_phases).strip().casefold())
    if entry is None:
        raise OpenDssAllocationExportError(
            f"FASES2 sem relação válida: {raw_phases or '<vazio>'}"
        )
    if len(letters) == 1:
        if not entry.dss:
            raise OpenDssAllocationExportError(
                f"FASES2 {entry.fases2} não possui terminal DSS."
            )
        return (entry.dss,)
    terminals: dict[str, str] = {}
    for candidate in configuration.entries:
        if candidate.phase_count != 1 or not candidate.dss:
            continue
        name = (candidate.name or "").strip().upper()
        if name and name[0] in PHASE_LETTERS:
            terminals.setdefault(name[0], candidate.dss.split(".", 1)[0])
    missing = [letter for letter in letters if letter not in terminals]
    if missing:
        raise OpenDssAllocationExportError(
            "Fases sem terminal DSS monofásico: " + ", ".join(missing)
        )
    return tuple(terminals[letter] for letter in letters)


def _base_name(code: str, load_id: str) -> str:
    value = sanitize_dss_name(code) or sanitize_dss_name(load_id)
    if not value:
        raise OpenDssAllocationExportError(
            f"CARGA_ID {load_id} não produz um nome OpenDSS válido."
        )
    return value


def _unique_messages(messages: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(message.strip() for message in messages if message.strip())
    )


def _resolve_transformers(
    catalog: CircuitCatalogModel,
    allocations: TransformerAllocationModel,
    phase_configuration: PhaseConfiguration,
    load_indices: tuple[int, ...],
    *,
    cancel_check: CancelCheck | None,
) -> tuple[
    tuple[_ResolvedAllocationTransformer, ...],
    tuple[str, ...],
    int,
]:
    """Resolve transformadores isoladamente e omite somente os inválidos."""

    owners = [0] * len(catalog.segments.bars)
    for membership in catalog.memberships:
        for raw_bar in membership.bar_indices:
            owners[int(raw_bar)] += 1

    bus_name = bus_namer(catalog)
    candidates: dict[int, _ResolvedAllocationTransformer] = {}
    problems: dict[int, list[str]] = {}

    def reject(load_index: int, reason: str) -> None:
        problems.setdefault(load_index, []).append(reason)

    for load_index in load_indices:
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Exportação de alocação cancelada.")
        load_id = allocations.loads.load_ids[load_index]
        bar_index = int(allocations.loads.bar_indices[load_index])
        if owners[bar_index] > 1:
            reject(
                load_index,
                "a barra pertence a múltiplos circuitos",
            )
            continue
        raw_phases = allocations.loads.phases[load_index]
        letters = phase_configuration.phase_letters_for_value(raw_phases)
        if letters is None:
            reject(
                load_index,
                f"FASES2={raw_phases or '<vazio>'} não possui relação válida",
            )
            continue
        try:
            nodes = _phase_nodes(phase_configuration, raw_phases, letters)
            base = _base_name(allocations.loads.codes[load_index], load_id)
        except OpenDssAllocationExportError as exc:
            reject(load_index, str(exc))
            continue
        candidates[load_index] = _ResolvedAllocationTransformer(
            load_index,
            base,
            bus_name(bar_index),
            letters,
            nodes,
            allocations.record(load_index),
        )

    name_owners: dict[str, list[tuple[int, str]]] = {}
    for load_index, candidate in candidates.items():
        phase_count = len(candidate.letters)
        for prefix in ("", "GD-BT-", "GD-MT-"):
            for letter in candidate.letters:
                name = f"{prefix}{candidate.base}-{phase_count}F-{letter}"
                name_owners.setdefault(name.casefold(), []).append(
                    (load_index, name)
                )
    collisions: dict[int, list[str]] = {}
    for entries in name_owners.values():
        indices = tuple(dict.fromkeys(index for index, _name in entries))
        if len(indices) <= 1:
            continue
        name = entries[0][1]
        ids = ", ".join(allocations.loads.load_ids[index] for index in indices)
        for load_index in indices:
            collisions.setdefault(load_index, []).append(
                f"'{name}' entre CARGA_ID {ids}"
            )
    for load_index, details in collisions.items():
        reject(
            load_index,
            "nomes OpenDSS colidem: " + "; ".join(details),
        )

    rejected = frozenset(problems)
    resolved = tuple(
        candidate
        for load_index, candidate in candidates.items()
        if load_index not in rejected
    )
    warnings: list[str] = []
    for load_index in load_indices:
        reasons = problems.get(load_index)
        if not reasons:
            continue
        load_id = allocations.loads.load_ids[load_index]
        code = allocations.loads.codes[load_index] or "<vazio>"
        phases = allocations.loads.phases[load_index] or "<vazio>"
        for reason in _unique_messages(reasons):
            warnings.append(
                "Transformador ignorado: "
                f"CARGA_ID={load_id}, CODIGO={code}, FASES2={phases}: {reason}."
            )
    return resolved, _unique_messages(warnings), len(rejected)


def _allocation_files(
    catalog: CircuitCatalogModel,
    circuit_index: int,
    transformers: tuple[_ResolvedAllocationTransformer, ...],
    curve: Curve,
    schedule: CalculationLevelSchedule,
    settings: OpenDssAllocationSettings,
    *,
    cancel_check: CancelCheck | None,
    progress: ProgressCallback | None,
) -> tuple[tuple[int, str, str, str, str, int], ...]:
    """Devolve NPAT, nome, energia, GD BT, GD MT e contagem de objetos."""

    definition = catalog.definition(circuit_index)
    nominal_voltage = parse_number(definition.nominal_voltage)
    if nominal_voltage is None or nominal_voltage <= 0.0:
        raise OpenDssAllocationExportError(
            f"Circuito {definition.circuit_id} sem VNOM numérica positiva."
        )
    voltage = _format(phase_voltage_kv(nominal_voltage))
    level_files: list[tuple[int, str, str, str, str, int]] = []
    for level_position, level in enumerate(schedule.levels):
        multiplier = curve_value_at_reference(curve, level.reference_hour)
        if multiplier < 0.0:
            raise OpenDssAllocationExportError(
                f"A curva {curve.name} é negativa no HORARIO_REF "
                f"do NPAT {level.npat}."
            )
        energy_entries: list[str] = []
        bt_entries: list[str] = []
        mt_entries: list[str] = []
        for transformer in transformers:
            base = transformer.base
            bus = transformer.bus
            letters = transformer.letters
            nodes = transformer.nodes
            record = transformer.record
            phase_count = len(letters)
            bt_kw = (
                record.generation_bt_kwh
                / (24.0 * settings.kwh_days)
                * multiplier
                / phase_count
            )
            mt_kw = (
                record.generation_mt_kwh
                / (24.0 * settings.kwh_days)
                * multiplier
                / phase_count
            )
            for letter, node in zip(letters, nodes, strict=True):
                energy = record.total_energy.for_phase(letter)
                energy_entries.append(
                    f"New Load.{base}-{phase_count}F-{letter}"
                    " phases=1"
                    f" bus1={bus}.{node}"
                    " conn=wye"
                    f" kV={voltage}"
                    " model=1"
                    f" kWh={_format(energy)}"
                    f" kWhDays={_format(settings.kwh_days)}"
                    f" CFactor={_format(settings.initial_cfactor)}"
                    f" PF={_format(settings.load_pf)}"
                    " class=1"
                )
                bt_entries.append(
                    f"New Load.GD-BT-{base}-{phase_count}F-{letter}"
                    " phases=1"
                    f" bus1={bus}.{node}"
                    " conn=wye"
                    f" kV={voltage}"
                    " model=1"
                    f" kW={_format(-bt_kw)} kvar=0 status=fixed class=-1"
                )
                mt_entries.append(
                    f"New Load.GD-MT-{base}-{phase_count}F-{letter}"
                    " phases=1"
                    f" bus1={bus}.{node}"
                    " conn=wye"
                    f" kV={voltage}"
                    " model=1"
                    f" kW={_format(-mt_kw)} kvar=0 status=fixed class=-2"
                )

        header = (
            "! Gerado pelo modo de alocacao nativa por energia",
            f"! Circuito {definition.circuit_id}; NPAT {level.npat} - {level.name}",
            "",
        )
        energy_text = "\n".join((*header, *energy_entries, ""))
        bt_text = "\n".join(
            (
                "! Equivalentes fixos de geracao BT; nao participam de AllocateLoads",
                f"! Curva {curve.name}; HORARIO_REF={level.reference_hour}",
                "",
                *bt_entries,
                "",
            )
        )
        mt_text = "\n".join(
            (
                "! Equivalentes fixos de geracao MT; nao participam de AllocateLoads",
                f"! Curva {curve.name}; HORARIO_REF={level.reference_hour}",
                "",
                *mt_entries,
                "",
            )
        )
        level_files.append(
            (level.npat, level.name, energy_text, bt_text, mt_text, len(energy_entries) * 3)
        )
        if progress is not None:
            progress(
                len(transformers) * (level_position + 1),
                max(1, len(transformers) * 4),
            )
    return tuple(level_files)


def _master_text(
    catalog: CircuitCatalogModel,
    circuit_index: int,
    npat: int,
    level_name: str,
    currents: tuple[float, float, float],
    settings: OpenDssAllocationSettings,
    load_settings: OpenDssLoadSettings | None,
    redirects: Iterable[str],
    buscoords_filename: str,
) -> str:
    definition = catalog.definition(circuit_index)
    base = sanitize_dss_name(definition.code) or sanitize_dss_name(
        definition.circuit_id
    )
    circuit_name = f"{base}_NPAT{npat}"
    root_index = catalog.segments.bars.index_for_id(definition.root_bar_id)
    assert root_index is not None
    root_bus = bus_namer(catalog)(root_index)
    source_bus = f"{root_bus}_SOURCE_NPAT{npat}"
    head_name = f"ALLOC-HEAD-{circuit_name}"
    meter_name = f"ALLOC-{circuit_name}"
    voltage = parse_number(definition.nominal_voltage)
    assert voltage is not None
    lines = [
        "! ATENCAO: PeakCurrent usa apenas modulos de corrente.",
        "! Com fluxo reverso por GD, a alocacao pode convergir para a solucao de sentido oposto.",
        f"! NPAT {npat} - {level_name}",
        "Clear",
        f"Set DefaultBaseFrequency={_format(FREQUENCY_HZ)}",
        "",
        f"New Circuit.{circuit_name}",
        f"~ bus1={source_bus}.1.2.3 phases=3 basekv={_format(voltage)}"
        f" pu=1 angle=0 frequency={_format(FREQUENCY_HZ)}",
        f"~ MVAsc3={SOURCE_SHORT_CIRCUIT_MVA} MVAsc1={SOURCE_SHORT_CIRCUIT_MVA}",
        "",
        f"New Line.{head_name} phases=3",
        f"~ bus1={source_bus}.1.2.3 bus2={root_bus}.1.2.3",
        f"~ r1={_format(HEAD_LINE_R1)} x1={_format(HEAD_LINE_X1)}"
        f" r0={_format(HEAD_LINE_R0)} x0={_format(HEAD_LINE_X0)}",
        "~ c1=0 c0=0 length=1 units=km",
        "",
        *(f"Redirect {name}" for name in redirects),
        "",
        f"New EnergyMeter.{meter_name} Element=Line.{head_name} Terminal=1",
        f"~ PeakCurrent=[{' '.join(_format(value) for value in currents)}]",
        "",
    ]
    if load_settings is not None:
        lines.extend(load_settings.batch_edit_commands())
        if load_settings.batch_edit_commands():
            lines.append("")
    lines.extend(
        [
            f"Set Voltagebases=[{_format(voltage)}]",
            "calcvoltagebases",
            f"Set ControlMode={CONTROL_MODE}",
            f"Set MaxControlIter={MAX_CONTROL_ITER}",
            "Set mode=snapshot",
            "Set number=1",
            "Solve",
            f"Set NumAllocIterations={settings.num_iterations}",
            "AllocateLoads",
            "Solve",
            "",
            f"Buscoords {buscoords_filename}",
            "",
        ]
    )
    return "\n".join(lines)


def build_allocation_export(
    catalog: CircuitCatalogModel,
    cables: CableModel | None,
    phase_configuration: PhaseConfiguration,
    circuit_index: int,
    allocations: TransformerAllocationModel,
    measurements: AllocationMeasurementModel,
    curve: Curve,
    schedule: CalculationLevelSchedule,
    settings: OpenDssAllocationSettings,
    *,
    regulators: RegulatorModel | None = None,
    load_settings: OpenDssLoadSettings | None = None,
    line_parameter_mode: OpenDssLineParameterMode = OpenDssLineParameterMode.ORIGINAL,
    library_catalog: OpenDssLibraryCatalog | None = None,
    library_mappings: OpenDssLibraryMappings | None = None,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> OpenDssAllocationExportBundle:
    """Monta os quatro diretórios sem iniciar o motor OpenDSS."""

    if not 0 <= int(circuit_index) < len(catalog):
        raise IndexError(circuit_index)
    if allocations.loads.bars is not catalog.segments.bars:
        raise OpenDssAllocationExportError(
            "Os agregados pertencem a outro modelo de barras."
        )
    if allocations.phase_configuration is not phase_configuration:
        raise OpenDssAllocationExportError(
            "Os agregados usam outra configuração de fases."
        )
    if measurements.circuits is not catalog:
        raise OpenDssAllocationExportError(
            "As correntes pertencem a outro catálogo de circuitos."
        )
    measurement_group = measurements.records_for_circuit(circuit_index)
    if len(measurement_group) != 4:
        raise OpenDssAllocationExportError(
            "O circuito selecionado não possui as quatro medições NPAT."
        )
    load_indices = _selected_load_indices(catalog, allocations, circuit_index)
    selected_ids = frozenset(
        allocations.loads.load_ids[index] for index in load_indices
    )
    selected_issues = allocations.issues_for_loads(selected_ids)
    warnings: list[str] = [
        "Elemento ignorado na agregação: " + issue.description
        for issue in selected_issues
    ]
    transformers, transformer_warnings, skipped_transformer_count = (
        _resolve_transformers(
            catalog,
            allocations,
            phase_configuration,
            load_indices,
            cancel_check=cancel_check,
        )
    )
    warnings.extend(transformer_warnings)
    if not transformers:
        unique = _unique_messages(warnings)
        details = "\n• ".join(unique[:30])
        suffix = "" if len(unique) <= 30 else f"\n… e mais {len(unique) - 30}."
        raise OpenDssAllocationExportError(
            "Nenhum transformador válido restou no circuito selecionado."
            + ("" if not details else "\n• " + details + suffix)
        )

    energy_by_phase = {letter: 0.0 for letter in PHASE_LETTERS}
    for transformer in transformers:
        for letter in transformer.letters:
            energy_by_phase[letter] += transformer.record.total_energy.for_phase(
                letter
            )
    for measurement in measurement_group:
        for letter, current in zip(
            PHASE_LETTERS,
            measurement.currents,
            strict=True,
        ):
            if current > 0.0 and energy_by_phase[letter] <= 0.0:
                warnings.append(
                    f"NPAT {measurement.npat}, fase {letter}: "
                    f"PeakCurrent={_format(current)} A, mas não há kWh alocável; "
                    "o circuito foi exportado e essa medição pode não ser atendida."
                )

    network = build_export(
        catalog,
        cables,
        phase_configuration,
        (circuit_index,),
        regulators=regulators,
        line_parameter_mode=line_parameter_mode,
        library_catalog=library_catalog,
        library_mappings=library_mappings,
        cancel_check=cancel_check,
        progress=progress,
    )
    _validate_synthetic_names(catalog, circuit_index, schedule, network)
    level_payloads = _allocation_files(
        catalog,
        circuit_index,
        transformers,
        curve,
        schedule,
        settings,
        cancel_check=cancel_check,
        progress=progress,
    )
    definition = catalog.definition(circuit_index)
    base = sanitize_dss_name(definition.code) or sanitize_dss_name(
        definition.circuit_id
    )
    buscoords_text = "" if network.master is None else network.master.buscoords_text
    levels: list[AllocationLevelExport] = []
    directory_names = allocation_export_directory_names(
        catalog, circuit_index, schedule
    )
    for (
        npat,
        level_name,
        energy_text,
        bt_text,
        mt_text,
        load_count,
    ), directory_name in zip(level_payloads, directory_names, strict=True):
        master_filename = f"{base}_NPAT{npat}_Master.dss"
        buscoords_filename = f"{base}_NPAT{npat}_Buscoords.csv"
        redirects = (
            *(name for name, _text in network.element_files),
            ENERGY_LOADS_FILENAME,
            BT_GENERATION_FILENAME,
            MT_GENERATION_FILENAME,
        )
        master_text = _master_text(
            catalog,
            circuit_index,
            npat,
            level_name,
            measurement_group[npat].currents,
            settings,
            load_settings,
            redirects,
            buscoords_filename,
        )
        files = (
            *network.element_files,
            (ENERGY_LOADS_FILENAME, energy_text),
            (BT_GENERATION_FILENAME, bt_text),
            (MT_GENERATION_FILENAME, mt_text),
            (master_filename, master_text),
            (buscoords_filename, buscoords_text),
        )
        levels.append(
            AllocationLevelExport(
                npat,
                level_name,
                directory_name,
                master_filename,
                files,
                len(transformers),
                load_count,
            )
        )
    return OpenDssAllocationExportBundle(
        definition.circuit_id,
        tuple(levels),
        network,
        _unique_messages(warnings),
        skipped_transformer_count,
    )


def write_allocation_export(
    destination: str | os.PathLike[str],
    bundle: OpenDssAllocationExportBundle,
) -> tuple[Path, ...]:
    """Grava tudo em staging e substitui os quatro diretórios com rollback."""

    base = Path(destination)
    base.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".allocation-export-", dir=base))
    token = uuid.uuid4().hex
    backups: list[tuple[Path, Path]] = []
    installed: list[tuple[Path, bool]] = []
    try:
        for relative_path, text in bundle.relative_files:
            target = staging / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        for directory_name in bundle.directory_names:
            staged = staging / directory_name
            final = base / directory_name
            existed = final.exists()
            if existed:
                backup = base / f".{directory_name}.backup-{token}"
                os.replace(final, backup)
                backups.append((backup, final))
            os.replace(staged, final)
            installed.append((final, existed))
    except Exception:
        for final, _existed in reversed(installed):
            if final.is_dir():
                shutil.rmtree(final, ignore_errors=True)
            elif final.exists():
                final.unlink(missing_ok=True)
        for backup, final in reversed(backups):
            if backup.exists():
                os.replace(backup, final)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    for backup, _final in backups:
        if backup.is_dir():
            shutil.rmtree(backup, ignore_errors=True)
        elif backup.exists():
            backup.unlink(missing_ok=True)
    return tuple(base / name for name in bundle.directory_names)


__all__ = [
    "BT_GENERATION_FILENAME",
    "ENERGY_LOADS_FILENAME",
    "MT_GENERATION_FILENAME",
    "AllocationLevelExport",
    "OpenDssAllocationExportBundle",
    "OpenDssAllocationExportError",
    "allocation_export_directory_names",
    "build_allocation_export",
    "write_allocation_export",
]
