"""Conectividade física e operacional derivada exclusivamente do projeto."""
from dataclasses import dataclass

import numpy as np
from types import SimpleNamespace


@dataclass(frozen=True, slots=True)
class TopologySnapshot:
    project_id: str
    revision: int
    physical_components: tuple[int, ...]
    operational_components: tuple[int, ...]
    coupled_circuits: tuple[tuple[int, ...], ...]
    issues: tuple[str, ...] = ()

    def assert_current(self, project):
        if (self.project_id, self.revision) != (project.project_id, project.revision):
            raise ValueError("A topologia pertence a outra revisão do projeto.")


def build_topology(composed, project_id, revision, cancel_check=None):
    bars = composed.bars
    physical = list(range(len(bars)))
    operational = physical.copy()

    def root(parent, index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(parent, left, right):
        a, b = root(parent, left), root(parent, right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    issues = []
    segments, switches = composed.segments, composed.switches
    if segments is not None:
        states = {} if switches is None else {
            int(segment): switches.states[i].strip()
            for i, segment in enumerate(switches.segment_indices)
        }
        for i, (left, right) in enumerate(zip(segments.start_indices, segments.end_indices)):
            if i % 1024 == 0 and cancel_check and cancel_check():
                raise InterruptedError("Atualização da topologia cancelada.")
            left, right = int(left), int(right)
            join(physical, left, right)
            state = states.get(i, "1")
            if state == "1":
                join(operational, left, right)
            elif state != "0":
                issues.append(f"Trecho {segments.segment_ids[i]}: estado de chave desconhecido; condução indeterminada.")
    physical = tuple(root(physical, i) for i in range(len(bars)))
    operational = tuple(root(operational, i) for i in range(len(bars)))
    groups = {}
    if composed.catalog is not None:
        for i, definition in enumerate(composed.catalog.definitions):
            bar = bars.index_for_id(definition.root_bar_id)
            if bar is not None:
                groups.setdefault(operational[bar], []).append(i)
    return TopologySnapshot(project_id, revision, physical, operational,
                            tuple(tuple(indices) for indices in groups.values() if len(indices) > 1),
                            tuple(issues))


def physical_switch_scope(catalog, selected):
    """Chaves internas ao escopo e fronteiras que ficaram fora da exportação."""
    if catalog.switches is None or not selected:
        return (), ()
    bars = np.zeros(len(catalog.segments.bars), dtype=bool)
    if len(set(selected)) == len(catalog):
        bars[:] = True
    else:
        for index in selected:
            bars[catalog.membership(index).bar_indices] = True
    internal, boundary = [], []
    for segment in catalog.switches.segment_indices:
        segment = int(segment)
        left = bool(bars[catalog.segments.start_indices[segment]])
        right = bool(bars[catalog.segments.end_indices[segment]])
        if left and right:
            internal.append(segment)
        elif left or right:
            boundary.append(segment)
    return tuple(dict.fromkeys(internal)), tuple(dict.fromkeys(boundary))


def coupled_study_reason(catalog, selected):
    """Mesmo bloqueio para UI, API do solver e geração de master."""
    snapshot = build_topology(SimpleNamespace(bars=catalog.segments.bars,
                              segments=catalog.segments, switches=catalog.switches,
                              catalog=catalog), "", 0)
    groups = [group for group in snapshot.coupled_circuits if set(group).intersection(selected)]
    if not groups:
        return ""
    labels = [", ".join(catalog.definition(i).code or catalog.definition(i).circuit_id for i in group)
              for group in groups]
    return ("Alimentadores unidos por conexões condutoras: " + "; ".join(labels) +
            ". O estudo exige solução elétrica multifonte, ainda não suportada; "
            "não é válido resolvê-los como alimentadores independentes.")
