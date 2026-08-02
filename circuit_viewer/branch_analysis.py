"""Análise topológica de ramais monofásicos por circuito."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

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
    """Resumo imutável de um componente monofásico conectado ao tronco."""

    circuit_index: int
    circuit_id: str
    connection_bar_index: int
    connection_bar_id: str
    connection_bar_code: str
    first_segment_index: int
    first_segment_id: str
    first_segment_code: str
    segment_indices: IndexArray
    bar_indices: IndexArray
    total_length: float | None
    load_count: int
    phase: str
    phase_key: str
    removable: bool
    switch_count: int
    first_switch_position: int | None
    trunk_connection_count: int
    missing_length_count: int
    topology: str

    def __post_init__(self) -> None:
        for values in (self.segment_indices, self.bar_indices):
            if values.dtype != np.dtype(np.intp) or values.ndim != 1:
                raise ValueError("Os índices do ramal devem ser vetores inteiros.")
            if values.flags.writeable:
                raise ValueError("Os índices do ramal devem ser imutáveis.")
        if self.circuit_index < 0:
            raise ValueError("O índice do circuito não pode ser negativo.")
        if self.connection_bar_index < 0 or self.first_segment_index < 0:
            raise ValueError("A conexão do ramal deve possuir índices válidos.")
        for count in (
            self.load_count,
            self.switch_count,
            self.trunk_connection_count,
            self.missing_length_count,
        ):
            if count < 0:
                raise ValueError("As contagens do ramal não podem ser negativas.")

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

    def __post_init__(self) -> None:
        if self.analyzed_circuit_count < 0 or self.omitted_issue_count < 0:
            raise ValueError("As contagens da análise não podem ser negativas.")


def analyze_branches(
    catalog: CircuitCatalogModel,
    phase_configuration: PhaseConfiguration,
    loads: LoadModel | None = None,
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> BranchAnalysisResult:
    """Identifica ramais monofásicos energizados em todos os circuitos."""

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
    if loads is not None:
        load_counts_by_bar = np.bincount(
            loads.bar_indices,
            minlength=bar_count,
        ).astype(np.intp, copy=False)

    switch_by_segment = (
        None if switches is None else switches.record_indices_by_segment
    )
    allowed_marks = np.zeros(segment_count, dtype=np.int64)
    circuit_switch_marks = np.zeros(segment_count, dtype=np.int64)
    trunk_bar_marks = np.zeros(bar_count, dtype=np.int64)
    trunk_segment_marks = np.zeros(segment_count, dtype=np.int64)
    processed_branch_marks = np.zeros(segment_count, dtype=np.int64)
    component_marks = np.zeros(segment_count, dtype=np.int64)
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
    component_generation = 0
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

        candidates: list[tuple[int, str, int, int, int]] = []
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
                if phase_counts[segment_index] != 1:
                    continue
                if trunk_bar_marks[neighbor] == generation:
                    add_issue(
                        circuit_id,
                        "single-phase-trunk-chord",
                        "Trecho monofásico conecta duas barras do tronco e não foi "
                        "classificado como ramal.",
                        segment_index,
                        dedupe=(circuit_id, "trunk-chord", segment_index),
                    )
                    continue
                candidates.append(
                    (
                        int(trunk_depths[trunk_bar]),
                        segments.segment_ids[segment_index].casefold(),
                        segment_index,
                        trunk_bar,
                        neighbor,
                    )
                )
        candidates.sort()

        for _, _, first_segment, primary_trunk_bar, first_downstream_bar in candidates:
            if processed_branch_marks[first_segment] == generation:
                continue
            phase_key = phase_keys[first_segment]
            if not phase_key:
                continue

            component_generation += 1
            branch_segments: list[int] = [first_segment]
            downstream_bars: list[int] = [first_downstream_bar]
            connections: dict[int, int] = {first_segment: primary_trunk_bar}
            component_marks[first_segment] = component_generation
            processed_branch_marks[first_segment] = generation
            downstream_seen: set[int] = {first_downstream_bar}
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
                    if component_marks[segment_index] != component_generation:
                        component_marks[segment_index] = component_generation
                        processed_branch_marks[segment_index] = generation
                        branch_segments.append(segment_index)
                    if trunk_bar_marks[neighbor] == generation:
                        connections.setdefault(segment_index, neighbor)
                        continue
                    if neighbor not in downstream_seen:
                        downstream_seen.add(neighbor)
                        downstream_bars.append(neighbor)
                        branch_queue.append(neighbor)

            # Distância multi-origem: qualquer conexão com o tronco inicia no nível 1.
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
                    if component_marks[segment_index] != component_generation:
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
                        bar_distances[neighbor] = int(
                            segment_distances[segment_index]
                        )
                        distance_queue.append(neighbor)

            branch_array = np.asarray(branch_segments, dtype=np.intp)
            switch_indices = (
                np.empty(0, dtype=np.intp)
                if switch_by_segment is None
                else branch_array[switch_by_segment[branch_array] >= 0]
            )
            first_switch_position = (
                None
                if switch_indices.size == 0
                else int(segment_distances[switch_indices].min())
            )
            lengths = segments.lengths[branch_array]
            missing_length_count = int(np.count_nonzero(np.isnan(lengths)))
            total_length = (
                None if missing_length_count else float(lengths.sum(dtype=np.float64))
            )
            downstream_array = np.asarray(downstream_bars, dtype=np.intp)
            load_count = int(load_counts_by_bar[downstream_array].sum())

            degrees: dict[int, int] = {}
            vertices: set[int] = set()
            for segment_index in branch_segments:
                inspect()
                start_bar = int(segments.start_indices[segment_index])
                end_bar = int(segments.end_indices[segment_index])
                vertices.add(start_bar)
                vertices.add(end_bar)
                degrees[start_bar] = degrees.get(start_bar, 0) + 1
                degrees[end_bar] = degrees.get(end_bar, 0) + 1
            cyclic = len(branch_segments) >= len(vertices)
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

            readonly_segments = _readonly_indices(sorted(branch_segments))
            readonly_bars = _readonly_indices(sorted(downstream_bars))
            records.append(
                BranchRecord(
                    circuit_index=circuit_index,
                    circuit_id=circuit_id,
                    connection_bar_index=primary_trunk_bar,
                    connection_bar_id=bars.bar_ids[primary_trunk_bar],
                    connection_bar_code=bars.codes[primary_trunk_bar],
                    first_segment_index=first_segment,
                    first_segment_id=segments.segment_ids[first_segment],
                    first_segment_code=segments.codes[first_segment],
                    segment_indices=readonly_segments,
                    bar_indices=readonly_bars,
                    total_length=total_length,
                    load_count=load_count,
                    phase=phase_labels.get(phase_key, phase_key),
                    phase_key=phase_key,
                    removable=(
                        first_switch_position is not None
                        and first_switch_position <= 5
                    ),
                    switch_count=int(switch_indices.size),
                    first_switch_position=first_switch_position,
                    trunk_connection_count=len(connections),
                    missing_length_count=missing_length_count,
                    topology=shape,
                )
            )

        if progress is not None:
            progress(circuit_index + 1, total_circuits)

    if cancelled():
        raise InterruptedError("Análise de ramais cancelada.")
    records.sort(
        key=lambda record: (
            record.circuit_index,
            record.first_segment_id.casefold(),
            record.first_segment_index,
        )
    )
    return BranchAnalysisResult(
        tuple(records),
        tuple(issues),
        total_circuits,
        omitted_issues,
    )
