"""Correção de demanda resolvida pelo próprio motor do OpenDSS.

Camada de núcleo: não importa Qt nem ``py_dss_interface``; o motor entra por
parâmetro, como em :mod:`circuit_viewer.opendss_powerflow`. O método está em
:mod:`circuit_viewer.demand_correction`; aqui está só o que liga o método ao
motor.

**Sem recompilar a cada iteração.** Toda carga de consumo é exportada com
``kW=1 kvar=1`` e a potência de cada patamar mora no ``LoadShape``. Gravar
``kW=fP kvar=fQ`` na carga compilada multiplica o ponto do perfil que o solve
usa — e cada solve usa um ponto só (``Set time=(k, 0)`` resolve o NPAT ``k``).
Por isso o par do patamar ``k`` é regravado antes de cada solve daquele
patamar e nunca contamina os outros. O circuito em memória é descartável: os
fatores saem daqui como números, e quem os aplica de fato é a exportação, nos
perfis (``build_export(demand_correction=...)``).

**A ordem kW → kvar é obrigatória.** Medido na DLL: gravar ``kW`` recalcula o
``kvar`` pelo FP corrente (kW=2 numa carga 1+j1 vira 2+j2); só o ``kvar``
gravado em seguida o torna independente.

**O fixo é medido.** Geração, capacitores e cargas fora do escopo não escalam;
a parcela deles na saída vem do mesmo solve. Ler as potências de todas as
``Load`` custaria uma chamada por carga, então lê-se o **menor** dos dois
conjuntos e o outro sai do balanço ``saída = escopo + fixo + perdas``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from .demand_correction import (
    PATAMAR_COUNT,
    CorrectionSettings,
    DemandCorrectionResult,
    DemandScope,
    HeadReading,
    PatamarTarget,
    energy_scope_for_name,
    solve_patamar,
)
from .model import (
    CableModel,
    CapacitorModel,
    CircuitCatalogModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
)
from .opendss_engine import DssEngine
from .opendss_export import (
    LOAD_PATTERN_COUNT,
    OpenDssExportBundle,
    build_export,
)
from .opendss_library import OpenDssLibraryCatalog
from .opendss_line_mode import OpenDssLineParameterMode
from .opendss_mapping_store import OpenDssLibraryMappings
from .opendss_powerflow import (
    _step_mode_commands,
    _write_circuit_files,
    read_head_power,
)
from .opendss_regulator_settings import OpenDssRegulatorSettings
from .opendss_settings import OpenDssLoadSettings
from .opendss_solution import DEFAULT_MAX_POWER_FLOW_ITER
from .phase_config import PhaseConfiguration

if TYPE_CHECKING:
    from .energy_demand import EnergyDemandModel
    from .generator_update import GeneratorUpdateModel


ProgressCallback = Callable[[int, int], None]

assert LOAD_PATTERN_COUNT == PATAMAR_COUNT


def scoped_load_names(
    bundle: OpenDssExportBundle,
    scope: frozenset[DemandScope],
) -> frozenset[str]:
    """Nomes (minúsculos, como o OpenDSS) das ``Load`` que os fatores escalam.

    Vem do que o exportador **emitiu**, e não de uma regra de nome: a carga do
    Interplan não tem prefixo, então só os resultados dos arquivos de carga
    sabem quais nomes são dela. No modo Energia, o prefixo é do próprio
    exportador (``UC-BT-``, ``UC-MT-``, ``IP-``), e a GD fica de fora.
    """

    names: set[str] = set()
    if DemandScope.CARGA in scope:
        for result in (
            bundle.single_phase_loads,
            bundle.two_phase_loads,
            bundle.three_phase_loads,
        ):
            if result is not None:
                names.update(result.used_names)
    if bundle.energy is not None:
        names.update(
            name
            for name in bundle.energy.used_names
            if energy_scope_for_name(name) in scope
        )
    return frozenset(name.casefold() for name in names)


def _load_indices(
    engine: DssEngine,
    scoped: frozenset[str],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Índices das cargas do escopo e das demais, numa única passada."""

    loads = engine.loads
    inside: list[int] = []
    outside: list[int] = []
    cursor = loads.first()
    while cursor:
        target = inside if loads.name.casefold() in scoped else outside
        target.append(int(loads.idx))
        cursor = loads.next()
    return tuple(inside), tuple(outside)


def _write_factors(
    engine: DssEngine,
    indices: Sequence[int],
    active_factor: float,
    reactive_factor: float,
) -> None:
    loads = engine.loads
    for index in indices:
        loads.idx = index
        # kW antes de kvar: ver o docstring do módulo.
        loads.kw = active_factor
        loads.kvar = reactive_factor


def _sum_load_powers(engine: DssEngine, indices: Sequence[int]) -> tuple[float, float]:
    """Potência consumida pelas cargas indicadas (gerador sai negativo)."""

    loads = engine.loads
    element = engine.cktelement
    active: list[float] = []
    reactive: list[float] = []
    for index in indices:
        loads.idx = index
        powers = element.powers
        active.append(math.fsum(powers[0::2]))
        reactive.append(math.fsum(powers[1::2]))
    return math.fsum(active), math.fsum(reactive)


def run_demand_correction(
    engine: DssEngine,
    catalog: CircuitCatalogModel,
    cables: CableModel | None,
    phase_configuration: PhaseConfiguration,
    circuit_index: int,
    targets: Sequence[PatamarTarget],
    scope: frozenset[DemandScope],
    *,
    workspace: Path,
    settings: CorrectionSettings = CorrectionSettings(),
    loads: LoadModel | None = None,
    patterns: LoadPatternModel | None = None,
    generator_updates: GeneratorUpdateModel | None = None,
    regulators: RegulatorModel | None = None,
    capacitors: CapacitorModel | None = None,
    load_settings: OpenDssLoadSettings | None = None,
    regulator_settings: OpenDssRegulatorSettings | None = None,
    max_power_flow_iterations: int = DEFAULT_MAX_POWER_FLOW_ITER,
    line_parameter_mode: OpenDssLineParameterMode = OpenDssLineParameterMode.ORIGINAL,
    library_catalog: OpenDssLibraryCatalog | None = None,
    library_mappings: OpenDssLibraryMappings | None = None,
    energy: EnergyDemandModel | None = None,
    cancel_check: Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
) -> DemandCorrectionResult:
    """Acha ``(fP, fQ)`` de cada patamar com alvo, para um circuito.

    O modelo é gerado **sem** correção alguma: os fatores são sempre sobre a
    base do modo de cargas corrente, nunca sobre uma correção anterior.
    """

    circuit_index = int(circuit_index)
    if not 0 <= circuit_index < len(catalog):
        raise IndexError(circuit_index)
    targets = tuple(targets)
    if len(targets) != PATAMAR_COUNT:
        raise ValueError(f"São necessários {PATAMAR_COUNT} patamares de alvo.")
    if all(target.is_empty for target in targets):
        raise ValueError("Nenhum patamar tem P ou Q a corrigir.")
    scope = frozenset(scope)
    if not scope:
        raise ValueError("Escolha ao menos um grupo de cargas a corrigir.")
    from .project_topology import coupled_study_reason

    reason = coupled_study_reason(catalog, (circuit_index,))
    if reason:
        raise ValueError(reason)

    definition = catalog.definition(circuit_index)
    bundle = build_export(
        catalog,
        cables,
        phase_configuration,
        (circuit_index,),
        loads=loads,
        patterns=patterns,
        generator_updates=generator_updates,
        regulators=regulators,
        capacitors=capacitors,
        load_settings=load_settings,
        regulator_settings=regulator_settings,
        max_power_flow_iterations=max_power_flow_iterations,
        line_parameter_mode=line_parameter_mode,
        library_catalog=library_catalog,
        library_mappings=library_mappings,
        include_solve=False,
        energy=energy,
        cancel_check=cancel_check,
    )
    master = bundle.master
    if master is None or not master.text:
        detail = "; ".join(issue.reason for issue in (master.issues if master else ()))
        raise ValueError(
            f"Circuito {definition.circuit_id}: o arquivo master não pôde ser "
            f"gerado{f': {detail}' if detail else ''}."
        )
    scoped_names = scoped_load_names(bundle, scope)
    if not scoped_names:
        groups = ", ".join(item.label.lower() for item in sorted(scope, key=list(DemandScope).index))
        raise ValueError(
            f"Circuito {definition.circuit_id}: nenhuma carga exportada nos grupos "
            f"escolhidos ({groups})."
        )

    master_path = _write_circuit_files(workspace, circuit_index, definition, bundle)
    if cancel_check is not None and cancel_check():
        raise InterruptedError("Correção de demanda cancelada.")
    engine.text("Clear")
    engine.text(f"Compile [{master_path}]")
    for command in _step_mode_commands(max_power_flow_iterations):
        engine.text(command)

    inside, outside = _load_indices(engine, scoped_names)
    if not inside:
        raise ValueError(
            f"Circuito {definition.circuit_id}: as cargas do escopo não foram "
            "encontradas no circuito compilado."
        )
    measure_outside = len(outside) <= len(inside)
    written: list[tuple[float, float] | None] = [None]

    def evaluator(step: int) -> Callable[[float, float], HeadReading]:
        def evaluate(active_factor: float, reactive_factor: float) -> HeadReading:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Correção de demanda cancelada.")
            pair = (active_factor, reactive_factor)
            if written[0] != pair:
                _write_factors(engine, inside, active_factor, reactive_factor)
                written[0] = pair
            # O solve avança o relógio: o patamar é reposicionado toda vez.
            engine.text(f"Set time=({step}, 0)")
            engine.solution.solve()
            converged = bool(engine.solution.converged)
            head_active, head_reactive = read_head_power(engine)
            losses = engine.circuit.losses
            active_losses = float(losses[0]) / 1000.0
            reactive_losses = float(losses[1]) / 1000.0
            if measure_outside:
                fixed_active, fixed_reactive = _sum_load_powers(engine, outside)
            else:
                scoped_active, scoped_reactive = _sum_load_powers(engine, inside)
                fixed_active = head_active - scoped_active - active_losses
                fixed_reactive = head_reactive - scoped_reactive - reactive_losses
            return HeadReading(
                head_active,
                head_reactive,
                fixed_active,
                fixed_reactive,
                converged,
                active_losses,
                reactive_losses,
            )

        return evaluate

    patamares = []
    for step, target in enumerate(targets):
        patamares.append(
            solve_patamar(
                evaluator(step),
                target,
                settings,
                step=step,
                cancel_check=cancel_check,
            )
        )
        if progress is not None:
            progress(step + 1, PATAMAR_COUNT)

    return DemandCorrectionResult(
        circuit_index=circuit_index,
        circuit_id=definition.circuit_id,
        scope=scope,
        settings=settings,
        patamares=tuple(patamares),
        energy_mode=energy is not None,
        scoped_load_count=len(inside),
        fixed_load_count=len(outside),
        models=(
            catalog,
            cables,
            phase_configuration,
            loads,
            patterns,
            generator_updates,
            regulators,
            capacitors,
            energy,
        ),
    )
