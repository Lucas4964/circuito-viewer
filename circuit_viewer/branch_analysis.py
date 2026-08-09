"""Análise topológica de ramais monofásicos e bifásicos por circuito."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

import numpy as np

from .model import (
    CircuitCatalogModel,
    IndexArray,
    LoadModel,
    NetworkTopology,
)
from .phase_config import PhaseConfiguration


MAX_BRANCH_ISSUES = 500
CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]


class BranchType(StrEnum):
    """Classificações públicas dos ramais encontrados."""

    MONOPHASIC = "MONOFASICO"
    BIPHASIC = "BIFASICO"


@dataclass(frozen=True, slots=True)
class _BranchPolicy:
    branch_type: BranchType
    primary_phase_count: int
    incorporates_single_phase_subtrees: bool


_BRANCH_POLICIES = {
    1: _BranchPolicy(BranchType.MONOPHASIC, 1, False),
    2: _BranchPolicy(BranchType.BIPHASIC, 2, True),
}


def _readonly_indices(values) -> IndexArray:  # noqa: ANN001
    result = np.ascontiguousarray(values, dtype=np.intp)
    if result.ndim != 1:
        raise ValueError("Os índices devem formar um vetor unidimensional.")
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class BranchIssue:
    """Ocorrência que limita ou qualifica um resultado de ramal."""

    circuit_id: str
    kind: str
    message: str
    segment_id: str | None = None


@dataclass(frozen=True, slots=True)
class BranchRecord:
    """Resumo imutável de um ramal conectado ao tronco trifásico."""

    branch_id: int
    branch_type: BranchType
    circuit_index: int
    circuit_id: str
    connection_bar_index: int
    connection_bar_id: str
    connection_bar_code: str
    topological_level: int
    first_segment_index: int
    first_segment_id: str
    first_segment_code: str
    first_common_segment_index: int | None
    first_common_segment_id: str
    first_common_segment_code: str
    segment_indices: IndexArray
    bar_indices: IndexArray
    load_indices: IndexArray
    switch_indices: IndexArray
    first_switch_record_index: int | None
    first_switch_id: str
    first_switch_code: str
    total_length: float | None
    load_count: int
    phases2: str
    phase: str
    phase_key: str
    removable: bool
    switch_count: int
    first_switch_position: int | None
    trunk_connection_count: int
    missing_length_count: int
    topology: str

    def __post_init__(self) -> None:
        if not isinstance(self.branch_type, BranchType):
            raise ValueError("TIPO_RAMAL deve ser uma classificação conhecida.")
        for values in (
            self.segment_indices,
            self.bar_indices,
            self.load_indices,
            self.switch_indices,
        ):
            if values.dtype != np.dtype(np.intp) or values.ndim != 1:
                raise ValueError("Os índices do ramal devem ser vetores inteiros.")
            if values.flags.writeable:
                raise ValueError("Os índices do ramal devem ser imutáveis.")
        if self.branch_id <= 0:
            raise ValueError("RAMAL_ID deve ser um inteiro positivo.")
        if self.circuit_index < 0:
            raise ValueError("O índice do circuito não pode ser negativo.")
        if self.connection_bar_index < 0 or self.first_segment_index < 0:
            raise ValueError("A conexão do ramal deve possuir índices válidos.")
        if self.first_common_segment_index is None:
            if self.first_common_segment_id or self.first_common_segment_code:
                raise ValueError("Ramal sem trecho convencional não pode identificá-lo.")
        elif self.first_common_segment_index < 0 or not self.first_common_segment_id:
            raise ValueError("O primeiro trecho convencional deve ser válido.")
        if self.first_switch_record_index is None:
            if self.first_switch_id or self.first_switch_code:
                raise ValueError("Ramal sem chave não pode identificá-la.")
        elif self.first_switch_record_index < 0 or not self.first_switch_id:
            raise ValueError("A primeira chave deve ser válida.")
        if self.topological_level < 0:
            raise ValueError("O nível topológico não pode ser negativo.")
        for count in (
            self.load_count,
            self.switch_count,
            self.trunk_connection_count,
            self.missing_length_count,
        ):
            if count < 0:
                raise ValueError("As contagens do ramal não podem ser negativas.")
        if self.load_count != int(self.load_indices.size):
            raise ValueError("NUM_CARGAS deve corresponder aos índices das cargas.")
        if self.switch_count != int(self.switch_indices.size):
            raise ValueError("NUM_CHAVES deve corresponder aos índices das chaves.")
        if (self.first_switch_position is None) != (
            self.first_switch_record_index is None
        ):
            raise ValueError("A posição e a identificação da primeira chave divergem.")

    @property
    def segment_count(self) -> int:
        return int(self.segment_indices.size)

    @property
    def bar_count(self) -> int:
        return int(self.bar_indices.size)


@dataclass(frozen=True, slots=True)
class BranchAnalysisResult:
    records: tuple[BranchRecord, ...]
    issues: tuple[BranchIssue, ...]
    analyzed_circuit_count: int
    omitted_issue_count: int = 0
    source_catalog: CircuitCatalogModel | None = None
    source_loads: LoadModel | None = None
    phase_configuration: PhaseConfiguration | None = None

    def __post_init__(self) -> None:
        if self.analyzed_circuit_count < 0 or self.omitted_issue_count < 0:
            raise ValueError("As contagens da análise não podem ser negativas.")
        if tuple(record.branch_id for record in self.records) != tuple(
            range(1, len(self.records) + 1)
        ):
            raise ValueError("RAMAL_ID deve formar uma sequência global de 1 até N.")
        if (
            self.source_catalog is not None
            and self.source_loads is not None
            and self.source_loads.bars is not self.source_catalog.segments.bars
        ):
            raise ValueError("As fontes da análise de ramais são incompatíveis.")


@dataclass(slots=True)
class _TwoPhaseCore:
    phase_key: str
    first_segment: int
    primary_trunk_bar: int
    segment_indices: list[int]
    connections: dict[int, int]


def analyze_branches(
    catalog: CircuitCatalogModel,
    phase_configuration: PhaseConfiguration,
    loads: LoadModel | None = None,
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> BranchAnalysisResult:
    """Identifica ramais monofásicos e bifásicos energizados por circuito."""

    segments = catalog.segments
    bars = segments.bars
    switches = catalog.switches
    if loads is not None and loads.bars is not bars:
        raise ValueError("As cargas devem pertencer às barras dos circuitos.")

    segment_count = len(segments)
    bar_count = len(bars)
    topology = NetworkTopology(segments, switches)
    offsets = topology.incidence_offsets
    incidence_segments = topology.incidence_segments
    incidence_neighbors = topology.incidence_neighbors

    phase_counts = np.zeros(segment_count, dtype=np.int8)
    phase_keys: list[str] = []
    phase_labels: dict[str, str] = {}
    entry_names = {
        entry.fases2: entry.name
        for entry in phase_configuration.entries
        if entry.name
    }
    for index, raw_value in enumerate(segments.phases):
        display = raw_value.strip()
        key = display.casefold()
        phase_keys.append(key)
        phase_counts[index] = phase_configuration.phase_count_by_value.get(key, 0)
        if key and key not in phase_labels:
            phase_labels[key] = entry_names.get(key) or display

    load_counts_by_bar = np.zeros(bar_count, dtype=np.intp)
    load_offsets_by_bar = np.zeros(bar_count + 1, dtype=np.intp)
    load_order_by_bar = np.empty(0, dtype=np.intp)
    if loads is not None:
        load_counts_by_bar = np.bincount(
            loads.bar_indices,
            minlength=bar_count,
        ).astype(np.intp, copy=False)
        np.cumsum(load_counts_by_bar, out=load_offsets_by_bar[1:])
        load_order_by_bar = np.argsort(loads.bar_indices, kind="stable").astype(
            np.intp,
            copy=False,
        )

    switch_by_segment = (
        None if switches is None else switches.record_indices_by_segment
    )
    allowed_marks = np.zeros(segment_count, dtype=np.int64)
    circuit_switch_marks = np.zeros(segment_count, dtype=np.int64)
    trunk_bar_marks = np.zeros(bar_count, dtype=np.int64)
    trunk_segment_marks = np.zeros(segment_count, dtype=np.int64)
    processed_two_phase_marks = np.zeros(segment_count, dtype=np.int64)
    processed_single_phase_marks = np.zeros(segment_count, dtype=np.int64)
    single_component_marks = np.zeros(segment_count, dtype=np.int64)
    metric_segment_marks = np.zeros(segment_count, dtype=np.int64)
    distance_segment_marks = np.zeros(segment_count, dtype=np.int64)
    distance_bar_marks = np.zeros(bar_count, dtype=np.int64)
    trunk_depths = np.zeros(bar_count, dtype=np.intp)
    segment_distances = np.zeros(segment_count, dtype=np.intp)
    bar_distances = np.zeros(bar_count, dtype=np.intp)

    records: list[BranchRecord] = []
    issues: list[BranchIssue] = []
    omitted_issues = 0
    inspected = 0
    issue_keys: set[tuple[object, ...]] = set()
    metric_generation = 0
    distance_generation = 0

    def cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    def inspect() -> None:
        nonlocal inspected
        inspected += 1
        if inspected % 4_096 == 0 and cancelled():
            raise InterruptedError("Análise de ramais cancelada.")

    def add_issue(
        circuit_id: str,
        kind: str,
        message: str,
        segment_index: int | None = None,
        *,
        dedupe: tuple[object, ...] | None = None,
    ) -> None:
        nonlocal omitted_issues
        key = dedupe or (circuit_id, kind, segment_index, message)
        if key in issue_keys:
            return
        issue_keys.add(key)
        if len(issues) >= MAX_BRANCH_ISSUES:
            omitted_issues += 1
            return
        issues.append(
            BranchIssue(
                circuit_id,
                kind,
                message,
                None
                if segment_index is None
                else segments.segment_ids[segment_index],
            )
        )

    total_circuits = len(catalog)
    for circuit_index in range(total_circuits):
        if cancelled():
            raise InterruptedError("Análise de ramais cancelada.")
        generation = circuit_index + 1
        definition = catalog.definition(circuit_index)
        membership = catalog.membership(circuit_index)
        circuit_id = definition.circuit_id

        allowed_marks[membership.common_segment_indices] = generation
        circuit_switch_marks[membership.switch_segment_indices] = generation
        if switches is not None:
            for segment_value in membership.switch_segment_indices:
                segment_index = int(segment_value)
                record_index = int(switch_by_segment[segment_index])
                if record_index < 0:
                    continue
                if (
                    switches.states[record_index].strip() == "1"
                    and switches.circuit_ids[record_index].strip() == circuit_id
                ):
                    allowed_marks[segment_index] = generation

        root_index = bars.index_for_id(definition.root_bar_id)
        if root_index is None:
            add_issue(
                circuit_id,
                "missing-root",
                f"Barra inicial inexistente: {definition.root_bar_id}.",
            )
            if progress is not None:
                progress(circuit_index + 1, total_circuits)
            continue

        trunk_queue: deque[int] = deque((root_index,))
        trunk_bar_marks[root_index] = generation
        trunk_depths[root_index] = 0
        trunk_bars: list[int] = [root_index]
        trunk_segment_count = 0
        while trunk_queue:
            bar_index = trunk_queue.popleft()
            start = int(offsets[bar_index])
            stop = int(offsets[bar_index + 1])
            for position in range(start, stop):
                inspect()
                segment_index = int(incidence_segments[position])
                if (
                    allowed_marks[segment_index] != generation
                    or phase_counts[segment_index] != 3
                ):
                    continue
                if trunk_segment_marks[segment_index] != generation:
                    trunk_segment_marks[segment_index] = generation
                    trunk_segment_count += 1
                neighbor = int(incidence_neighbors[position])
                if trunk_bar_marks[neighbor] == generation:
                    continue
                trunk_bar_marks[neighbor] = generation
                trunk_depths[neighbor] = trunk_depths[bar_index] + 1
                trunk_bars.append(neighbor)
                trunk_queue.append(neighbor)

        if trunk_segment_count == 0:
            add_issue(
                circuit_id,
                "missing-three-phase-trunk",
                "O circuito não possui tronco trifásico alcançável desde a origem.",
            )
            if progress is not None:
                progress(circuit_index + 1, total_circuits)
            continue

        two_phase_candidates: list[tuple[int, str, int, int, int]] = []
        single_phase_candidates: list[tuple[int, str, int, int, int]] = []
        for trunk_bar in trunk_bars:
            start = int(offsets[trunk_bar])
            stop = int(offsets[trunk_bar + 1])
            for position in range(start, stop):
                inspect()
                segment_index = int(incidence_segments[position])
                neighbor = int(incidence_neighbors[position])
                if allowed_marks[segment_index] != generation:
                    if (
                        switch_by_segment is not None
                        and circuit_switch_marks[segment_index] == generation
                    ):
                        add_issue(
                            circuit_id,
                            "open-switch-boundary",
                            "Uma chave aberta interrompe a rede energizada.",
                            segment_index,
                            dedupe=(circuit_id, "open", segment_index),
                        )
                    continue
                phase_count = int(phase_counts[segment_index])
                policy = _BRANCH_POLICIES.get(phase_count)
                if policy is None:
                    continue
                if trunk_bar_marks[neighbor] == generation:
                    kind = (
                        "single-phase-trunk-chord"
                        if phase_count == 1
                        else "two-phase-trunk-chord"
                    )
                    label = "monofásico" if phase_count == 1 else "bifásico"
                    add_issue(
                        circuit_id,
                        kind,
                        f"Trecho {label} conecta duas barras do tronco e não foi "
                        "classificado como ramal.",
                        segment_index,
                        dedupe=(circuit_id, "trunk-chord", segment_index),
                    )
                    continue
                candidate = (
                    int(trunk_depths[trunk_bar]),
                    segments.segment_ids[segment_index].casefold(),
                    segment_index,
                    trunk_bar,
                    neighbor,
                )
                if policy.branch_type is BranchType.BIPHASIC:
                    two_phase_candidates.append(candidate)
                else:
                    single_phase_candidates.append(candidate)
        two_phase_candidates.sort()
        single_phase_candidates.sort()

        cores: list[_TwoPhaseCore] = []
        core_ids_by_bar: dict[int, set[int]] = {}
        for _, _, first_segment, primary_trunk_bar, first_downstream_bar in two_phase_candidates:
            if processed_two_phase_marks[first_segment] == generation:
                continue
            phase_key = phase_keys[first_segment]
            if not phase_key:
                continue
            core_segments = [first_segment]
            connections = {first_segment: primary_trunk_bar}
            core_vertices = {primary_trunk_bar, first_downstream_bar}
            downstream_seen = {first_downstream_bar}
            core_queue: deque[int] = deque((first_downstream_bar,))
            processed_two_phase_marks[first_segment] = generation

            while core_queue:
                bar_index = core_queue.popleft()
                start = int(offsets[bar_index])
                stop = int(offsets[bar_index + 1])
                for position in range(start, stop):
                    inspect()
                    segment_index = int(incidence_segments[position])
                    neighbor = int(incidence_neighbors[position])
                    if allowed_marks[segment_index] != generation:
                        if (
                            switch_by_segment is not None
                            and circuit_switch_marks[segment_index] == generation
                        ):
                            add_issue(
                                circuit_id,
                                "open-switch-boundary",
                                "Uma chave aberta interrompeu um ramal.",
                                segment_index,
                                dedupe=(circuit_id, "open", segment_index),
                            )
                        continue
                    if int(phase_counts[segment_index]) != 2:
                        continue
                    if phase_keys[segment_index] != phase_key:
                        add_issue(
                            circuit_id,
                            "two-phase-transition",
                            "Uma mudança de FASES2 bifásico interrompeu o ramal.",
                            segment_index,
                            dedupe=(
                                circuit_id,
                                "two-phase-transition",
                                min(first_segment, segment_index),
                                max(first_segment, segment_index),
                            ),
                        )
                        continue
                    if processed_two_phase_marks[segment_index] != generation:
                        processed_two_phase_marks[segment_index] = generation
                        core_segments.append(segment_index)
                    core_vertices.add(neighbor)
                    if trunk_bar_marks[neighbor] == generation:
                        connections.setdefault(segment_index, neighbor)
                        continue
                    if neighbor not in downstream_seen:
                        downstream_seen.add(neighbor)
                        core_queue.append(neighbor)

            core_id = len(cores)
            cores.append(
                _TwoPhaseCore(
                    phase_key,
                    first_segment,
                    primary_trunk_bar,
                    core_segments,
                    connections,
                )
            )
            for bar_index in core_vertices:
                if trunk_bar_marks[bar_index] != generation:
                    core_ids_by_bar.setdefault(bar_index, set()).add(core_id)

        # Estado por trecho monofásico: 1=ramal próprio, 2=anexado, 3=excluído.
        single_status = np.zeros(segment_count, dtype=np.int8)
        for raw_segment in membership.segment_indices:
            first_segment = int(raw_segment)
            if (
                allowed_marks[first_segment] != generation
                or int(phase_counts[first_segment]) != 1
                or single_component_marks[first_segment] == generation
            ):
                continue
            component_segments: list[int] = [first_segment]
            component_vertices: set[int] = set()
            component_bars: deque[int] = deque()
            queued_bars: set[int] = set()
            single_component_marks[first_segment] = generation

            for endpoint in (
                int(segments.start_indices[first_segment]),
                int(segments.end_indices[first_segment]),
            ):
                component_vertices.add(endpoint)
                if trunk_bar_marks[endpoint] != generation and endpoint not in queued_bars:
                    queued_bars.add(endpoint)
                    component_bars.append(endpoint)

            while component_bars:
                bar_index = component_bars.popleft()
                start = int(offsets[bar_index])
                stop = int(offsets[bar_index + 1])
                for position in range(start, stop):
                    inspect()
                    segment_index = int(incidence_segments[position])
                    neighbor = int(incidence_neighbors[position])
                    if allowed_marks[segment_index] != generation:
                        if (
                            switch_by_segment is not None
                            and circuit_switch_marks[segment_index] == generation
                        ):
                            add_issue(
                                circuit_id,
                                "open-switch-boundary",
                                "Uma chave aberta interrompeu uma subárvore monofásica.",
                                segment_index,
                                dedupe=(circuit_id, "open", segment_index),
                            )
                        continue
                    if int(phase_counts[segment_index]) != 1:
                        continue
                    if single_component_marks[segment_index] != generation:
                        single_component_marks[segment_index] = generation
                        component_segments.append(segment_index)
                    component_vertices.add(neighbor)
                    if (
                        trunk_bar_marks[neighbor] != generation
                        and neighbor not in queued_bars
                    ):
                        queued_bars.add(neighbor)
                        component_bars.append(neighbor)

            touches_trunk = any(
                trunk_bar_marks[bar_index] == generation
                for bar_index in component_vertices
            )
            adjacent_cores: set[int] = set()
            for bar_index in component_vertices:
                if trunk_bar_marks[bar_index] != generation:
                    adjacent_cores.update(core_ids_by_bar.get(bar_index, ()))
            representative = min(
                component_segments,
                key=lambda value: (
                    segments.segment_ids[value].casefold(),
                    segments.segment_ids[value],
                    value,
                ),
            )

            if len(adjacent_cores) > 1:
                single_status[component_segments] = 3
                add_issue(
                    circuit_id,
                    "ambiguous-single-phase-subtree",
                    "Componente monofásica ligada a mais de um ramal bifásico; "
                    "seus trechos e cargas foram excluídos da agregação.",
                    representative,
                    dedupe=(
                        circuit_id,
                        "ambiguous-single-phase-subtree",
                        tuple(sorted(component_segments)),
                    ),
                )
            elif touches_trunk and adjacent_cores:
                single_status[component_segments] = 3
                add_issue(
                    circuit_id,
                    "single-phase-trunk-bridge",
                    "Componente monofásica ligada simultaneamente ao tronco e a um "
                    "ramal bifásico; seus trechos e cargas foram excluídos.",
                    representative,
                    dedupe=(
                        circuit_id,
                        "single-phase-trunk-bridge",
                        tuple(sorted(component_segments)),
                    ),
                )
            elif len(adjacent_cores) == 1:
                single_status[component_segments] = 2
                core = cores[next(iter(adjacent_cores))]
                core.segment_indices.extend(component_segments)
            elif touches_trunk:
                single_status[component_segments] = 1

        def append_record(
            branch_type: BranchType,
            branch_segments: list[int],
            connections: dict[int, int],
            primary_trunk_bar: int,
            first_segment: int,
            phase_key: str,
        ) -> None:
            nonlocal metric_generation, distance_generation
            unique_segments = sorted(set(branch_segments))
            branch_array = np.asarray(unique_segments, dtype=np.intp)
            metric_generation += 1
            metric_segment_marks[branch_array] = metric_generation

            distance_generation += 1
            distance_queue: deque[int] = deque()
            for segment_index, trunk_bar in connections.items():
                distance_segment_marks[segment_index] = distance_generation
                segment_distances[segment_index] = 1
                start_bar = int(segments.start_indices[segment_index])
                end_bar = int(segments.end_indices[segment_index])
                downstream = end_bar if start_bar == trunk_bar else start_bar
                if trunk_bar_marks[downstream] == generation:
                    continue
                if distance_bar_marks[downstream] != distance_generation:
                    distance_bar_marks[downstream] = distance_generation
                    bar_distances[downstream] = 1
                    distance_queue.append(downstream)
            while distance_queue:
                bar_index = distance_queue.popleft()
                start = int(offsets[bar_index])
                stop = int(offsets[bar_index + 1])
                for position in range(start, stop):
                    inspect()
                    segment_index = int(incidence_segments[position])
                    if metric_segment_marks[segment_index] != metric_generation:
                        continue
                    next_distance = int(bar_distances[bar_index]) + 1
                    if distance_segment_marks[segment_index] != distance_generation:
                        distance_segment_marks[segment_index] = distance_generation
                        segment_distances[segment_index] = next_distance
                    neighbor = int(incidence_neighbors[position])
                    if trunk_bar_marks[neighbor] == generation:
                        continue
                    if distance_bar_marks[neighbor] != distance_generation:
                        distance_bar_marks[neighbor] = distance_generation
                        bar_distances[neighbor] = int(segment_distances[segment_index])
                        distance_queue.append(neighbor)

            switch_segment_indices = (
                np.empty(0, dtype=np.intp)
                if switch_by_segment is None
                else branch_array[switch_by_segment[branch_array] >= 0]
            )
            common_segment_indices = (
                branch_array
                if switch_by_segment is None
                else branch_array[switch_by_segment[branch_array] < 0]
            )
            first_common_segment_index = (
                None
                if common_segment_indices.size == 0
                else min(
                    (int(value) for value in common_segment_indices),
                    key=lambda index: (int(segment_distances[index]), index),
                )
            )
            first_switch_segment_index = (
                None
                if switch_segment_indices.size == 0
                else min(
                    (int(value) for value in switch_segment_indices),
                    key=lambda index: (int(segment_distances[index]), index),
                )
            )
            first_switch_position = (
                None
                if first_switch_segment_index is None
                else int(segment_distances[first_switch_segment_index])
            )
            switch_record_indices = (
                np.empty(0, dtype=np.intp)
                if switch_by_segment is None
                else switch_by_segment[switch_segment_indices]
            )
            first_switch_record_index = (
                None
                if first_switch_segment_index is None
                else int(switch_by_segment[first_switch_segment_index])
            )
            lengths = segments.lengths[branch_array]
            missing_length_count = int(np.count_nonzero(np.isnan(lengths)))
            total_length = (
                None if missing_length_count else float(lengths.sum(dtype=np.float64))
            )

            degrees: dict[int, int] = {}
            vertices: set[int] = set()
            for segment_index in unique_segments:
                inspect()
                start_bar = int(segments.start_indices[segment_index])
                end_bar = int(segments.end_indices[segment_index])
                vertices.add(start_bar)
                vertices.add(end_bar)
                degrees[start_bar] = degrees.get(start_bar, 0) + 1
                degrees[end_bar] = degrees.get(end_bar, 0) + 1
            downstream_bars = sorted(
                bar_index
                for bar_index in vertices
                if trunk_bar_marks[bar_index] != generation
            )
            downstream_array = np.asarray(downstream_bars, dtype=np.intp)
            load_count = int(load_counts_by_bar[downstream_array].sum())
            if load_count:
                load_array = np.empty(load_count, dtype=np.intp)
                cursor = 0
                for bar_value in downstream_array:
                    bar_index = int(bar_value)
                    start = int(load_offsets_by_bar[bar_index])
                    stop = int(load_offsets_by_bar[bar_index + 1])
                    size = stop - start
                    if size:
                        load_array[cursor : cursor + size] = load_order_by_bar[
                            start:stop
                        ]
                        cursor += size
                load_array.sort()
            else:
                load_array = np.empty(0, dtype=np.intp)

            cyclic = len(unique_segments) >= len(vertices)
            bifurcated = any(
                degrees.get(bar_index, 0) > 2 for bar_index in downstream_bars
            )
            shape = "Cíclico" if cyclic else "Bifurcado" if bifurcated else "Linear"
            if len(connections) > 1:
                shape += " + Múltiplas conexões"
                add_issue(
                    circuit_id,
                    "multiple-trunk-connections",
                    f"O ramal possui {len(connections):n} conexões com o tronco.",
                    first_segment,
                    dedupe=(circuit_id, "multiple", tuple(sorted(connections))),
                )

            records.append(
                BranchRecord(
                    branch_id=1,
                    branch_type=branch_type,
                    circuit_index=circuit_index,
                    circuit_id=circuit_id,
                    connection_bar_index=primary_trunk_bar,
                    connection_bar_id=bars.bar_ids[primary_trunk_bar],
                    connection_bar_code=bars.codes[primary_trunk_bar],
                    topological_level=int(trunk_depths[primary_trunk_bar]),
                    first_segment_index=first_segment,
                    first_segment_id=segments.segment_ids[first_segment],
                    first_segment_code=segments.codes[first_segment],
                    first_common_segment_index=first_common_segment_index,
                    first_common_segment_id=(
                        ""
                        if first_common_segment_index is None
                        else segments.segment_ids[first_common_segment_index]
                    ),
                    first_common_segment_code=(
                        ""
                        if first_common_segment_index is None
                        else segments.codes[first_common_segment_index]
                    ),
                    segment_indices=_readonly_indices(branch_array),
                    bar_indices=_readonly_indices(downstream_array),
                    load_indices=_readonly_indices(load_array),
                    switch_indices=_readonly_indices(switch_record_indices),
                    first_switch_record_index=first_switch_record_index,
                    first_switch_id=(
                        ""
                        if first_switch_record_index is None
                        else switches.switch_ids[first_switch_record_index]
                    ),
                    first_switch_code=(
                        ""
                        if first_switch_record_index is None
                        else switches.codes[first_switch_record_index]
                    ),
                    total_length=total_length,
                    load_count=load_count,
                    phases2=segments.phases[first_segment].strip(),
                    phase=phase_labels.get(phase_key, phase_key),
                    phase_key=phase_key,
                    removable=(
                        first_switch_position is not None
                        and first_switch_position <= 5
                    ),
                    switch_count=int(switch_record_indices.size),
                    first_switch_position=first_switch_position,
                    trunk_connection_count=len(connections),
                    missing_length_count=missing_length_count,
                    topology=shape,
                )
            )

        for core in cores:
            append_record(
                _BRANCH_POLICIES[2].branch_type,
                core.segment_indices,
                core.connections,
                core.primary_trunk_bar,
                core.first_segment,
                core.phase_key,
            )

        for _, _, first_segment, primary_trunk_bar, first_downstream_bar in single_phase_candidates:
            if (
                single_status[first_segment] != 1
                or processed_single_phase_marks[first_segment] == generation
            ):
                continue
            phase_key = phase_keys[first_segment]
            if not phase_key:
                continue
            branch_segments: list[int] = [first_segment]
            connections: dict[int, int] = {first_segment: primary_trunk_bar}
            processed_single_phase_marks[first_segment] = generation
            downstream_seen = {first_downstream_bar}
            branch_queue: deque[int] = deque((first_downstream_bar,))

            while branch_queue:
                bar_index = branch_queue.popleft()
                start = int(offsets[bar_index])
                stop = int(offsets[bar_index + 1])
                for position in range(start, stop):
                    inspect()
                    segment_index = int(incidence_segments[position])
                    neighbor = int(incidence_neighbors[position])
                    if allowed_marks[segment_index] != generation:
                        if (
                            switch_by_segment is not None
                            and circuit_switch_marks[segment_index] == generation
                        ):
                            add_issue(
                                circuit_id,
                                "open-switch-boundary",
                                "Uma chave aberta interrompeu um ramal.",
                                segment_index,
                                dedupe=(circuit_id, "open", segment_index),
                            )
                        continue
                    phase_count = int(phase_counts[segment_index])
                    if phase_count != 1:
                        if phase_count in {0, 2, 3}:
                            kind = (
                                "unknown-phase-boundary"
                                if phase_count == 0
                                else "phase-count-boundary"
                            )
                            add_issue(
                                circuit_id,
                                kind,
                                "Uma mudança de número de fases interrompeu o ramal.",
                                segment_index,
                                dedupe=(
                                    circuit_id,
                                    kind,
                                    min(first_segment, segment_index),
                                    max(first_segment, segment_index),
                                ),
                            )
                        continue
                    if single_status[segment_index] != 1:
                        continue
                    if phase_keys[segment_index] != phase_key:
                        add_issue(
                            circuit_id,
                            "single-phase-transition",
                            "Uma mudança de fase monofásica interrompeu o ramal.",
                            segment_index,
                            dedupe=(
                                circuit_id,
                                "transition",
                                min(first_segment, segment_index),
                                max(first_segment, segment_index),
                            ),
                        )
                        continue
                    if processed_single_phase_marks[segment_index] != generation:
                        processed_single_phase_marks[segment_index] = generation
                        branch_segments.append(segment_index)
                    if trunk_bar_marks[neighbor] == generation:
                        connections.setdefault(segment_index, neighbor)
                        continue
                    if neighbor not in downstream_seen:
                        downstream_seen.add(neighbor)
                        branch_queue.append(neighbor)

            append_record(
                _BRANCH_POLICIES[1].branch_type,
                branch_segments,
                connections,
                primary_trunk_bar,
                first_segment,
                phase_key,
            )

        if progress is not None:
            progress(circuit_index + 1, total_circuits)

    if cancelled():
        raise InterruptedError("Análise de ramais cancelada.")
    records.sort(
        key=lambda record: (
            record.circuit_id.casefold(),
            record.circuit_id,
            record.first_segment_id.casefold(),
            record.first_segment_id,
            record.first_segment_index,
        )
    )
    records = [
        replace(record, branch_id=branch_id)
        for branch_id, record in enumerate(records, start=1)
    ]
    return BranchAnalysisResult(
        records=tuple(records),
        issues=tuple(issues),
        analyzed_circuit_count=total_circuits,
        omitted_issue_count=omitted_issues,
        source_catalog=catalog,
        source_loads=loads,
        phase_configuration=phase_configuration,
    )
