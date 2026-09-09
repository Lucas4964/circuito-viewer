"""Projeto de estudos em memória: propostas, comparação de três versões e commit.

Os modelos colunares são projeções do cadastro vigente. Nenhuma operação deste
módulo abre arquivos, conhece Qt ou consulta novamente um banco de origem.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from uuid import uuid4

from .network_registry import (
    EquipmentKey, FileIdentity, NetworkRegistry, RecordOrigin, RegistryRecord,
    SourceBinding, _capture, _equal,
)
from .source_composition import (
    ENTITIES, ENTITY_BY_NAME, CompositionError, SourceWorkspace, compose, restrict_to_circuits,
)
from .project_topology import build_topology


def _freeze(values):
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class ProjectEvent:
    revision: int
    operation: str
    equipment_ids: tuple[str, ...]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True, slots=True)
class ProjectState:
    workspace: SourceWorkspace = field(default_factory=SourceWorkspace)
    project_id: str = field(default_factory=lambda: str(uuid4()))
    revision: int = 0
    baselines: object = field(default_factory=dict)
    feeder_scopes: object = field(default_factory=dict)
    pending: object = field(default_factory=dict)
    retired: object = field(default_factory=dict)
    history: tuple[ProjectEvent, ...] = ()
    previous: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        for name in ("baselines", "feeder_scopes", "pending", "retired"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))

    @property
    def records(self):
        return _freeze({key: row for dataset in self.workspace if dataset.registry
                        for key, row in dataset.registry.records.items()})

    def equipment(self, equipment_id):
        return next((row for row in self.records.values() if row.equipment_id == equipment_id), None)

    @classmethod
    def from_workspace(cls, workspace):
        """Adapta sessões anteriores e importadores isolados, sem ler disco."""
        values = []
        baselines = {}
        for dataset in workspace:
            registry = dataset.registry
            if registry is None:
                identity = FileIdentity(dataset.source_path, "session")
                network_id = "access:" + str(uuid4())
                registry = NetworkRegistry(network_id, _capture(dataset, network_id, identity, (), None),
                                           (SourceBinding(identity),))
                dataset = replace(dataset, registry=registry)
            for key, row in registry.records.items():
                for origin in row.origins:
                    baselines[(origin.file.path, key)] = row.values
            values.append(dataset)
        return cls(SourceWorkspace(values, workspace._next_ordinal), baselines=baselines)


@dataclass(frozen=True, slots=True)
class FeederChange:
    circuit_id: str
    label: str
    exists: bool
    changed_count: int
    added_count: int
    removed_keys: tuple[EquipmentKey, ...] = ()
    possible_matches: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldConflict:
    key: EquipmentKey
    field: str
    baseline: object
    current: object
    incoming: object

    @property
    def decision_key(self):
        return self.key, self.field


@dataclass(frozen=True, slots=True)
class ImportProposal:
    project_id: str
    revision: int
    file: FileIdentity
    dataset: object
    network_id: str
    binding: SourceBinding
    records: object
    scopes: object
    pending: object
    feeders: tuple[FeederChange, ...]
    requested_ids: tuple[str, ...]

    def __post_init__(self):
        for name in ("records", "scopes", "pending"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class ProjectChangeSet:
    project_id: str
    base_revision: int
    state: ProjectState
    composed: object
    topology: object
    operation_id: str = field(default_factory=lambda: str(uuid4()))

    def validate(self, current, cancelled=False):
        if cancelled:
            raise InterruptedError("Operação cancelada; projeto anterior preservado.")
        if (current.project_id, current.revision) != (self.project_id, self.base_revision):
            raise CompositionError("O projeto mudou durante a operação. Revise novamente as alterações.")


def _check(cancel_check):
    if cancel_check and cancel_check():
        raise InterruptedError("Atualização do projeto cancelada.")


def _dependencies(key, row):
    values = dict(row.values)
    entity = ENTITY_BY_NAME.get(key.entity)
    if entity:
        return tuple(EquipmentKey(key.network_id, target, values[column])
                     for column, target in entity.index_columns.items())
    if key.entity == "circuits":
        return (EquipmentKey(key.network_id, "bars", values["root_bar_id"]),)
    parent = {"patterns": "loads", "allocations": "loads", "circuit_levels": "circuits"}.get(key.entity)
    return (EquipmentKey(key.network_id, parent, key.native_id),) if parent else ()


def propose_import(project, dataset, file, *, circuit_ids=(), target_tag=None,
                   correspondences=(), cancel_check=None):
    _check(cancel_check)
    matches = [item for item in project.workspace if item.registry and item.registry.recognizes(file)]
    if len(matches) > 1:
        raise CompositionError("Origem ambígua: o arquivo corresponde a mais de uma rede do projeto.")
    known = matches[0] if matches else None
    target = project.workspace.dataset_for(target_tag) if target_tag else known
    if target_tag and target is None:
        raise CompositionError("A rede de destino não existe mais.")
    if known is not None and target is not known:
        raise CompositionError("O arquivo já pertence a outra rede; confira a correspondência.")
    if correspondences and target is None:
        raise CompositionError("Correspondências exigem uma rede de destino.")
    prior = target.registry if target else None
    if prior is None and target_tag is None:
        retired_matches = [NetworkRegistry(network_id, {}, bindings) for network_id, (bindings, _) in project.retired.items()
                           if NetworkRegistry(network_id, {}, bindings).recognizes(file)]
        if len(retired_matches) > 1:
            raise CompositionError("Origem ambígua no histórico de redes excluídas.")
        prior = retired_matches[0] if retired_matches else None
    explicit_correspondences = bool(correspondences)
    if prior and not correspondences:
        bindings = [binding for binding in prior.bindings if binding.file.path == file.path or binding.file.digest == file.digest]
        if bindings:
            correspondences = bindings[-1].correspondences
    seen = set()
    for mapping in correspondences:
        pair = mapping.entity, mapping.incoming_id
        if pair in seen or not mapping.incoming_id or not mapping.existing_id or mapping.entity not in (*ENTITY_BY_NAME, "circuits"):
            raise CompositionError("Correspondência vazia ou repetida.")
        seen.add(pair)
        known_ids = {} if prior is None else project.retired.get(prior.network_id, ((), {}))[1]
        if not prior or (EquipmentKey(prior.network_id, mapping.entity, mapping.existing_id) not in prior.records
                         and EquipmentKey(prior.network_id, mapping.entity, mapping.existing_id) not in known_ids):
            raise CompositionError("Destino da correspondência inexistente no projeto.")
        model = dataset.catalog if mapping.entity == "circuits" else getattr(dataset, mapping.entity)
        native_ids = (() if model is None else tuple(item.circuit_id for item in model.definitions)
                      if mapping.entity == "circuits" else getattr(model, ENTITY_BY_NAME[mapping.entity].id_column))
        if explicit_correspondences and mapping.incoming_id not in native_ids:
            raise CompositionError(f"ID ausente no banco: {mapping.entity} {mapping.incoming_id}.")
    network_id = prior.network_id if prior else "access:" + str(uuid4())
    tag = target.tag if target else project.workspace.next_tag()
    selected = restrict_to_circuits(dataset, circuit_ids)
    selected = replace(selected, tag=tag, name=target.name if target else selected.name)
    incoming = _capture(selected, network_id, file, correspondences, cancel_check)
    # Captura a fronteira antes da restrição; nunca cria as barras que faltam.
    all_records = _capture(dataset, network_id, file, correspondences, cancel_check)
    selected_bars = {key for key in incoming if key.entity == "bars"}
    pending = {}
    for key, row in all_records.items():
        if key.entity == "segments" and key not in incoming:
            if selected_bars.intersection(_dependencies(key, row)):
                pending[key] = row
    for key, row in all_records.items():
        if key.entity in ("switches", "regulators") and key not in incoming:
            if any(dependency in pending for dependency in _dependencies(key, row)):
                pending[key] = row
    aliases = {item.incoming_id: item.existing_id for item in correspondences if item.entity == "circuits"}
    ids = tuple(circuit_ids) if circuit_ids else tuple(item.circuit_id for item in dataset.catalog.definitions) if dataset.catalog else ()
    scopes, feeders = {}, []
    current = project.records
    for native_id in ids:
        _check(cancel_check)
        circuit_id = aliases.get(native_id, native_id)
        key = EquipmentKey(network_id, "circuits", circuit_id)
        part = restrict_to_circuits(dataset, (native_id,))
        scope = frozenset(_capture(part, network_id, file, correspondences, cancel_check)) & incoming.keys()
        scopes[circuit_id] = frozenset(scope)
        old_scope = project.feeder_scopes.get((network_id, circuit_id), frozenset())
        definition = dataset.catalog.definition(dataset.catalog.index_for_id(native_id))
        possible = tuple(f"{other.tag}: {item.code or item.circuit_id}" for other in project.workspace
                         if other.registry.network_id != network_id and other.catalog is not None
                         for item in other.catalog.definitions
                         if item.circuit_id == native_id or (definition.code and item.code.casefold() == definition.code.casefold()))
        feeders.append(FeederChange(circuit_id, definition.code or native_id, key in current,
                                    sum(k in current and not _equal(current[k].values, incoming[k].values) for k in scope),
                                    sum(k not in current for k in scope), tuple(sorted(old_scope - scope)), possible))
    if not ids:
        scopes[""] = frozenset(incoming)
        feeders.append(FeederChange("", "Banco sem seleção de alimentadores", target is not None,
                                    sum(k in current and not _equal(current[k].values, v.values) for k, v in incoming.items()),
                                    sum(k not in current for k in incoming)))
    else:
        # Interligações internas à seleção conjunta podem não pertencer a nenhum
        # alimentador isolado. Conservá-las pelo vínculo físico, não pelo dono.
        assigned = set().union(*scopes.values())
        for key, row in incoming.items():
            if key in assigned:
                continue
            dependencies = set(_dependencies(key, row))
            owners = [circuit_id for circuit_id, scope in scopes.items() if dependencies.intersection(scope)]
            for circuit_id in owners or list(scopes):
                scopes[circuit_id] = scopes[circuit_id] | {key}
    provided = selected.provided_entities if selected.provided_entities is not None else {k.entity for k in incoming}
    for i, feeder in enumerate(feeders):
        scope = scopes[feeder.circuit_id]
        owner = network_id, feeder.circuit_id
        missing = project.feeder_scopes.get(owner, frozenset()) - scope
        removed = tuple(sorted(k for k in missing if k in current and k.entity in provided
                               and current[k].origins[0].kind != "manual"
                               and not any(k in other for identity, other in project.feeder_scopes.items() if identity != owner)))
        feeders[i] = replace(feeder, removed_keys=removed,
                             changed_count=sum(k in current and not _equal(current[k].values, incoming[k].values) for k in scope),
                             added_count=sum(k not in current for k in scope))
    return ImportProposal(project.project_id, project.revision, file, selected, network_id,
                          SourceBinding(file, tuple(correspondences)), incoming, scopes, pending,
                          tuple(feeders), tuple(aliases.get(value, value) for value in ids))


class _Absent:
    def __str__(self):
        return "Sem referência anterior"


_MISSING = _Absent()


def _baseline(project, proposal, key):
    value = project.baselines.get((proposal.file.path, key))
    if value is not None:
        return value
    # Arquivo movido/copied com assinatura reconhecida usa o mesmo retrato.
    for dataset in project.workspace:
        if dataset.registry.network_id == proposal.network_id:
            for binding in reversed(dataset.registry.bindings):
                if binding.file.digest == proposal.file.digest:
                    value = project.baselines.get((binding.file.path, key))
                    if value is not None:
                        return value
    return ()


def _merged_record(project, proposal, key, incoming, current, force, conflicts, decisions):
    base = dict(_baseline(project, proposal, key))
    before, received = dict(current.values), dict(incoming.values)
    merged = dict(before)
    field_origins = dict(current.field_origins)
    for name, value in received.items():
        if (key.entity, name) in proposal.dataset.omitted_fields:
            continue
        old, baseline = before.get(name, _MISSING), base.get(name, _MISSING)
        if force or _equal(old, baseline) or _equal(old, value):
            merged[name] = value
            if not _equal(old, value):
                field_origins[name] = incoming.accepted_origin
        elif _equal(value, baseline):
            continue
        else:
            conflict = FieldConflict(key, name, baseline, old, value)
            choice = decisions.get(conflict.decision_key)
            if choice not in ("existing", "incoming"):
                conflicts.append(conflict)
            elif choice == "incoming":
                merged[name] = value
                field_origins[name] = incoming.accepted_origin
    return replace(current, values=tuple(merged.items()),
                   field_origins=tuple(field_origins.items()),
                   origins=tuple(dict.fromkeys((*current.origins, *incoming.origins))),
                   accepted_origin=incoming.accepted_origin if _equal(tuple(merged.items()), incoming.values) else current.accepted_origin)


def resolve_import(project, proposal, feeder_decisions, field_decisions=None, *, cancel_check=None):
    """Devolve os conflitos pendentes ou uma transação completamente validada."""
    if (project.project_id, project.revision) != (proposal.project_id, proposal.revision):
        raise CompositionError("A proposta pertence a uma revisão anterior.")
    _check(cancel_check)
    field_decisions = field_decisions or {}
    actions = {}
    for feeder in proposal.feeders:
        action = feeder_decisions.get(feeder.circuit_id)
        if action not in ("keep", "update", "replace"):
            raise CompositionError("Escolha Manter, Atualizar ou Substituir para cada alimentador.")
        actions[feeder.circuit_id] = action
    current = dict(project.records)
    received_keys = set().union(*(proposal.scopes[c] for c, action in actions.items() if action != "keep")) if actions else set()
    keep_keys = set().union(*(proposal.scopes[c] for c, action in actions.items() if action == "keep")) if actions else set()
    keep_keys &= current.keys()
    received_keys -= keep_keys
    scopes = dict(project.feeder_scopes)
    deletion = set()
    for circuit_id, action in actions.items():
        if action != "replace":
            continue
        old = scopes.get((proposal.network_id, circuit_id), frozenset())
        provided_entities = (proposal.dataset.provided_entities if proposal.dataset.provided_entities is not None
                             else {key.entity for key in proposal.records})
        for key in old - proposal.scopes[circuit_id]:
            if key.entity not in provided_entities or key not in current:
                continue
            shared = any(key in scope for owner, scope in scopes.items()
                         if owner != (proposal.network_id, circuit_id))
            if not shared and current[key].origins[0].kind != "manual":
                deletion.add(key)
    for key in deletion:
        current.pop(key, None)
    conflicts = []
    for i, key in enumerate(sorted(received_keys)):
        if i % 1024 == 0:
            _check(cancel_check)
        incoming = proposal.records[key]
        old = current.get(key)
        if old is None:
            retired_id = project.retired.get(key.network_id, ((), {}))[1].get(key)
            current[key] = replace(incoming, equipment_id=retired_id) if retired_id else incoming
            continue
        owners = [owner for owner, scope in scopes.items() if key in scope]
        exclusively_replaced = bool(owners) and all(
            owner[0] == proposal.network_id and actions.get(owner[1]) == "replace" for owner in owners)
        current[key] = _merged_record(project, proposal, key, incoming, old,
                                      exclusively_replaced and old.origins[0].kind != "manual", conflicts, field_decisions)
    if conflicts:
        return tuple(conflicts)
    pending = dict(project.pending)
    if any(action != "keep" for action in actions.values()):
        pending.update(proposal.pending)
    # Dependências já presentes tornam a conexão instanciável, sem consultar a origem.
    changed = True
    while changed:
        changed = False
        for key, row in tuple(pending.items()):
            if key in current:
                pending.pop(key)
            elif all(dep in current for dep in _dependencies(key, row)):
                current[key] = row
                pending.pop(key)
                changed = True
    for key, row in current.items():
        missing = [dep for dep in _dependencies(key, row) if dep not in current]
        if missing:
            raise CompositionError(f"{key.entity} {key.native_id}: exclusão quebraria a conexão com {missing[0].native_id}. Preserve ou remapeie o equipamento.")
    baselines = dict(project.baselines)
    for key, row in proposal.records.items():
        old_baseline = dict(_baseline(project, proposal, key))
        old_baseline.update((name, value) for name, value in row.values
                            if (key.entity, name) not in proposal.dataset.omitted_fields)
        baselines[(proposal.file.path, key)] = tuple(old_baseline.items())
    for circuit_id, action in actions.items():
        owner = proposal.network_id, circuit_id
        if action == "update":
            scopes[owner] = frozenset(scopes.get(owner, ())) | proposal.scopes[circuit_id]
        elif action == "replace":
            scopes[owner] = proposal.scopes[circuit_id] | frozenset(k for k in scopes.get(owner, ()) if k in current and k not in deletion)
    source = project.workspace.dataset_for(proposal.dataset.tag)
    bindings = tuple(dict.fromkeys((*(source.registry.bindings if source else project.retired.get(proposal.network_id, ((), {}))[0]), proposal.binding)))
    own = {key: row for key, row in current.items() if key.network_id == proposal.network_id}
    workspace = project.workspace
    if own:
        registry = NetworkRegistry(proposal.network_id, own, bindings)
        template = replace(proposal.dataset, registry=registry,
                           chosen_circuit_ids=tuple(sorted(key.native_id for key in own if key.entity == "circuits")))
        dataset = registry.materialize(template, cancel_check, operational=True)
        workspace = workspace.registered(dataset)
    # Conexões pendentes de outra origem podem ter sido resolvidas no mesmo domínio.
    for dataset in tuple(workspace):
        if dataset.registry.network_id == proposal.network_id:
            continue
        rows = {k: v for k, v in current.items() if k.network_id == dataset.registry.network_id}
        if rows != dataset.registry.records:
            registry = replace(dataset.registry, records=rows)
            workspace = workspace.registered(registry.materialize(dataset, cancel_check, operational=True))
    return _change(project, workspace, "import", tuple(current[k].equipment_id for k in received_keys if k in current),
                   baselines=baselines, feeder_scopes=scopes, pending=pending, cancel_check=cancel_check)


def _change(project, workspace, operation, equipment_ids=(), *, cancel_check=None, **updates):
    _check(cancel_check)
    records = {k: r for dataset in workspace for k, r in dataset.registry.records.items()}
    previous_records = project.records
    for key, record in records.items():
        if any(dependency not in records for dependency in _dependencies(key, record)):
            raise CompositionError(f"{key.entity} {key.native_id}: referência obrigatória inexistente no projeto.")
        entity = ENTITY_BY_NAME.get(key.entity)
        for column, target in (() if entity is None else entity.reference_columns.items()):
            value = dict(record.values)[column]
            reference = EquipmentKey(key.network_id, target, value)
            if reference in previous_records and reference not in records:
                raise CompositionError(f"{key.entity} {key.native_id}: excluir {target} {value} deixaria uma referência quebrada.")
    revision = project.revision + 1
    state = replace(project, workspace=workspace, revision=revision,
                    previous=replace(project, previous=None),
                    history=(*project.history, ProjectEvent(revision, operation, equipment_ids)), **updates)
    composed = compose(workspace.datasets, cancel_check=cancel_check) if len(workspace) else None
    topology = build_topology(composed, state.project_id, revision, cancel_check) if composed else None
    return ProjectChangeSet(project.project_id, project.revision, state, composed, topology)


def edit_equipment(project, equipment_id, values, *, cancel_check=None):
    record = project.equipment(equipment_id)
    if record is None:
        raise CompositionError("Equipamento não encontrado no projeto.")
    existing = dict(record.values)
    entity = ENTITY_BY_NAME.get(record.key.entity)
    protected = {entity.id_column} if entity else {"circuit_id"}
    if not set(values) <= existing.keys() or set(values) & protected:
        raise CompositionError("Campo inexistente ou identidade não editável.")
    existing.update(values)
    origin = RecordOrigin(FileIdentity("", ""), record.key.native_id, "manual", str(uuid4()), record.key.entity,
                          datetime.now(timezone.utc).isoformat())
    field_origins = dict(record.field_origins)
    field_origins.update((name, origin) for name in values)
    updated = replace(record, values=tuple(existing.items()), accepted_origin=origin, origins=(*record.origins, origin),
                      field_origins=tuple(field_origins.items()))
    workspace = project.workspace
    for dataset in workspace:
        if dataset.registry.network_id == record.key.network_id:
            rows = dict(dataset.registry.records)
            rows[record.key] = updated
            registry = replace(dataset.registry, records=rows)
            workspace = workspace.registered(registry.materialize(dataset, cancel_check, operational=True))
            break
    return _change(project, workspace, "edit", (equipment_id,), cancel_check=cancel_check)


def create_equipment(project, tag, entity, values, *, cancel_check=None):
    dataset = project.workspace.dataset_for(tag)
    if dataset is None or entity not in ENTITY_BY_NAME:
        raise CompositionError("Informe uma rede existente e uma entidade válida.")
    native_id = str(uuid4())
    columns = dict(values)
    columns[ENTITY_BY_NAME[entity].id_column] = native_id
    key = EquipmentKey(dataset.registry.network_id, entity, native_id)
    origin = RecordOrigin(FileIdentity("", ""), native_id, "manual", str(uuid4()), entity,
                          datetime.now(timezone.utc).isoformat())
    row = RegistryRecord(key, tuple(columns.items()), (origin,), origin)
    rows = dict(dataset.registry.records)
    rows[key] = row
    registry = replace(dataset.registry, records=rows)
    rebuilt = registry.materialize(dataset, cancel_check, operational=True)
    return _change(project, project.workspace.registered(rebuilt), "create", (row.equipment_id,), cancel_check=cancel_check)


def detach_origins(project, tag, *, cancel_check=None):
    dataset = project.workspace.dataset_for(tag)
    if dataset is None:
        raise CompositionError("Rede inexistente.")
    # O histórico de criação é preservado; só se interrompe o reconhecimento.
    registry = replace(dataset.registry, bindings=())
    return _change(project, project.workspace.registered(replace(dataset, registry=registry)),
                   "detach", cancel_check=cancel_check)


def remove_network(project, tag, *, cancel_check=None):
    dataset = project.workspace.dataset_for(tag)
    if dataset is None:
        raise CompositionError("Rede inexistente.")
    network_id = dataset.registry.network_id
    retired = dict(project.retired)
    ids = dict(retired.get(network_id, ((), {}))[1])
    ids.update((key, record.equipment_id) for key, record in dataset.registry.records.items())
    retired[network_id] = dataset.registry.bindings, _freeze(ids)
    return _change(project, project.workspace.without(tag), "remove", tuple(r.equipment_id for r in dataset.registry.records.values()),
                   retired=retired,
                   pending={k: v for k, v in project.pending.items() if k.network_id != network_id},
                   feeder_scopes={k: v for k, v in project.feeder_scopes.items() if k[0] != network_id},
                   cancel_check=cancel_check)


def undo(project, *, cancel_check=None):
    if project.previous is None:
        raise CompositionError("Nenhuma operação para desfazer.")
    previous = project.previous
    change = _change(project, previous.workspace, "undo", baselines=previous.baselines,
                     feeder_scopes=previous.feeder_scopes, pending=previous.pending,
                     retired=previous.retired, cancel_check=cancel_check)
    return replace(change, state=replace(change.state, previous=None))
