"""Cadastro imutável por rede/entidade/ID; índices só existem nos modelos derivados.

Nenhuma correspondência é inferida por posição, código ou coordenadas. O plano
de atualização é construído fora da UI e só substitui o workspace depois de
resolvidos os conflitos e validados todos os modelos.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4
from hashlib import sha256
import os
import math
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

import numpy as np

from .allocation import TransformerAllocationModel
from .circuit_calculation_levels import CircuitCalculationLevelsModel
from .model import CircuitCatalogModel, CircuitDefinition, LoadPatternModel, constructor_columns
from .source_composition import (
    ENTITIES, ENTITY_BY_NAME, MODEL_TYPES, CompositionError, SourceDataset,
    SourceWorkspace, restrict_to_circuits,
)


def _check(cancel_check):
    if cancel_check is not None and cancel_check():
        raise InterruptedError("Cadastro da rede cancelado.")


def _equal(left, right):
    """Valores ausentes numéricos não são alterações de cadastro."""
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return True
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if is_dataclass(left) and type(left) is type(right):
        return all(_equal(getattr(left, field.name), getattr(right, field.name))
                   for field in fields(left) if field.compare)
    return left == right


@dataclass(frozen=True, slots=True)
class FileIdentity:
    path: str
    digest: str

    @classmethod
    def read(cls, path: str, cancel_check=None) -> "FileIdentity":
        _check(cancel_check)
        resolved = Path(path).resolve()
        before = resolved.stat()
        digest = sha256()
        with resolved.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                _check(cancel_check)
                digest.update(chunk)
        after = resolved.stat()
        _check(cancel_check)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise CompositionError("O banco mudou durante a identificação. Tente novamente.")
        return cls(os.path.normcase(str(resolved)), digest.hexdigest())


@dataclass(frozen=True, slots=True, order=True)
class EquipmentKey:
    network_id: str
    entity: str
    native_id: str


@dataclass(frozen=True, slots=True)
class RecordOrigin:
    file: FileIdentity
    native_id: str
    kind: str = "import"
    batch_id: str = ""
    table: str = ""
    timestamp: str = ""
    transformation: str = ""


@dataclass(frozen=True, slots=True)
class RegistryRecord:
    key: EquipmentKey
    values: tuple[tuple[str, object], ...]
    origins: tuple[RecordOrigin, ...]
    accepted_origin: RecordOrigin
    equipment_id: str = field(default_factory=lambda: str(uuid4()))
    field_origins: tuple[tuple[str, RecordOrigin], ...] = ()


@dataclass(frozen=True, slots=True)
class RecordConflict:
    previous: RegistryRecord
    incoming: RegistryRecord

    @property
    def key(self):
        return self.previous.key

    @property
    def differences(self):
        old, new = dict(self.previous.values), dict(self.incoming.values)
        return tuple((name, old.get(name), new.get(name)) for name in sorted(old.keys() | new.keys())
                     if not _equal(old.get(name), new.get(name)))


@dataclass(frozen=True, slots=True)
class IdentityMapping:
    entity: str
    incoming_id: str
    existing_id: str


@dataclass(frozen=True, slots=True)
class SourceBinding:
    file: FileIdentity
    correspondences: tuple[IdentityMapping, ...] = ()


@dataclass(frozen=True, slots=True)
class NetworkRegistry:
    network_id: str
    records: Mapping[EquipmentKey, RegistryRecord]
    bindings: tuple[SourceBinding, ...]

    def __post_init__(self):
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))

    def recognizes(self, file: FileIdentity) -> bool:
        return any(binding.file.path == file.path or binding.file.digest == file.digest
                   for binding in self.bindings)

    def entity(self, entity: str):
        return {key.native_id: record for key, record in self.records.items() if key.entity == entity}

    def materialize(self, template: SourceDataset, cancel_check=None, *, operational=False) -> SourceDataset:
        """Recria referências por índices usando exclusivamente os IDs do cadastro."""
        diagnostics = []
        groups = {entity: self.entity(entity) for entity in (
            *(item.name for item in ENTITIES), "circuits", "patterns", "allocations", "circuit_levels")}
        indices = {entity: {native_id: i for i, native_id in enumerate(sorted(group))}
                   for entity, group in groups.items()}
        models = {}
        for entity in ENTITIES:
            _check(cancel_check)
            rows = [dict(groups[entity.name][key].values) for key in sorted(groups[entity.name])]
            if not rows:
                models[entity.name] = None
                continue
            columns = {}
            for name in rows[0]:
                values = [row[name] for row in rows]
                if name in entity.index_columns:
                    target = entity.index_columns[name]
                    try:
                        values = [indices[target][value] for value in values]
                    except KeyError as exc:
                        raise CompositionError(f"{entity.name}.{name}: referência ausente {exc.args[0]}.") from exc
                if name == "crs":
                    if any(value != values[0] for value in values):
                        raise CompositionError("Uma rede não pode misturar sistemas de coordenadas. Confira Coordenadas.")
                    columns[name] = values[0]
                else:
                    columns[name] = values
            arguments = () if entity.parent is None else (models[entity.parent],)
            models[entity.name] = MODEL_TYPES[entity.name](*arguments, **columns)
        _check(cancel_check)
        definitions = [CircuitDefinition(**dict(groups["circuits"][key].values))
                       for key in sorted(groups["circuits"])]
        catalog = None
        if definitions and models["segments"] is not None:
            catalog = CircuitCatalogModel.build(models["segments"], models["switches"], definitions,
                                                cancel_check=cancel_check, operational=operational)
        models.update(catalog=catalog, patterns=None, circuit_levels=None, allocations=None)
        loads = models["loads"]
        if loads is not None:
            patterns = [dict(groups["patterns"][key].values)["records"] if key in groups["patterns"] else None
                        for key in loads.load_ids]
            if any(patterns):
                models["patterns"] = LoadPatternModel(loads, patterns)
            allocations = [dict(groups["allocations"][key].values) if key in groups["allocations"] else None
                           for key in loads.load_ids]
            if any(row is not None for row in allocations) and any(row is None for row in allocations):
                diagnostics.append("Alocações indisponíveis na rede " + template.tag +
                                   ": há cargas sem dados de alocação. Os registros recebidos continuam preservados no projeto.")
            if allocations and all(row is not None for row in allocations):
                if any(row["phases"] != allocations[0]["phases"] for row in allocations):
                    raise CompositionError("As alocações da rede usam configurações de fases incompatíveis.")
                models["allocations"] = TransformerAllocationModel(
                    loads, allocations[0]["phases"], tuple(row["record"] for row in allocations),
                    tuple(dict.fromkeys(issue for row in allocations for issue in row["issues"])),
                )
        if catalog is not None:
            schedules = [dict(groups["circuit_levels"][item.circuit_id].values)["schedule"]
                         if item.circuit_id in groups["circuit_levels"] else None for item in catalog.definitions]
            if any(item is not None for item in schedules):
                models["circuit_levels"] = CircuitCalculationLevelsModel(catalog, schedules)
        if models["bars"] is None:
            raise CompositionError("O cadastro deve conter barras.")
        return replace(template, **models, registry=self, diagnostics=tuple(diagnostics))


def _capture(dataset, network_id, file, mappings, cancel_check):
    result = {}
    batch_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    aliases = {(item.entity, item.incoming_id): item.existing_id for item in mappings}

    def canonical(entity, native_id):
        entity = {"patterns": "loads", "allocations": "loads", "circuit_levels": "circuits"}.get(entity, entity)
        return aliases.get((entity, native_id), native_id)

    def add(entity, native_id, values):
        key = EquipmentKey(network_id, entity, canonical(entity, native_id))
        if key in result:
            raise CompositionError(f"A correspondência gera dois registros para {entity} {key.native_id}.")
        origin = RecordOrigin(file, native_id, batch_id=batch_id, table=dict(dataset.source_tables).get(entity, entity),
                              timestamp=timestamp,
                              transformation=f"{dataset.crs.label}; coordenadas / {dataset.applied_scale:g}")
        result[key] = RegistryRecord(key, tuple(values.items()), (origin,), origin)

    for entity in ENTITIES:
        _check(cancel_check)
        model = getattr(dataset, entity.name)
        if model is None:
            continue
        columns = constructor_columns(model)
        for i, native_id in enumerate(getattr(model, entity.id_column)):
            if i % 1024 == 0:
                _check(cancel_check)
            row = {}
            for name, values in columns.items():
                value = values[i] if isinstance(values, (np.ndarray, tuple)) else values
                if isinstance(value, np.generic):
                    value = value.item()
                target = entity.index_columns.get(name)
                if target is not None:
                    value = getattr(getattr(dataset, target), ENTITY_BY_NAME[target].id_column)[int(value)]
                if name == entity.id_column:
                    value = canonical(entity.name, value)
                elif target is not None or name in entity.reference_columns:
                    value = canonical(target or entity.reference_columns[name], value)
                row[name] = value
            add(entity.name, native_id, row)
    if dataset.catalog is not None:
        for i, definition in enumerate(dataset.catalog.definitions):
            row = {field.name: getattr(definition, field.name) for field in fields(definition)}
            row["circuit_id"] = canonical("circuits", definition.circuit_id)
            row["root_bar_id"] = canonical("bars", definition.root_bar_id)
            add("circuits", definition.circuit_id, row)
            if dataset.circuit_levels is not None:
                schedule = dataset.circuit_levels.schedule(i)
                if schedule is not None:
                    add("circuit_levels", definition.circuit_id, {"schedule": schedule})
    if dataset.loads is not None:
        for i, native_id in enumerate(dataset.loads.load_ids):
            load_id = canonical("loads", native_id)
            if dataset.patterns is not None:
                records = tuple(replace(record, load_id=load_id) for record in dataset.patterns.records_for_load(i))
                if records:
                    add("patterns", native_id, {"records": records})
            if dataset.allocations is not None:
                model = dataset.allocations
                add("allocations", native_id, {
                    "record": replace(model.record(i), load_id=load_id),
                    "phases": model.phase_configuration,
                    "issues": tuple(replace(issue, load_id=load_id) if issue.load_id else issue
                                    for issue in model.issues_for_loads((native_id,), include_unattributed=True)),
                })
    return result


def register_import(
    workspace: SourceWorkspace, dataset: SourceDataset, file: FileIdentity, *,
    circuit_ids: tuple[str, ...] = (), replace_workspace: bool = False,
    target_tag: str | None = None, correspondences: tuple[IdentityMapping, ...] = (),
    resolve_conflicts: Callable | None = None, cancel_check=None,
) -> tuple[SourceWorkspace, SourceDataset]:
    """Prepara uma atualização completa; jamais modifica o workspace recebido.

    O resolvedor devolve {EquipmentKey: 'existing'|'incoming'} para TODOS os
    conflitos, ou None para cancelar. Ausências no arquivo não são exclusões.
    """
    _check(cancel_check)
    matches = [item for item in workspace if item.registry and item.registry.recognizes(file)]
    if len(matches) > 1:
        raise CompositionError("O arquivo corresponde a mais de uma rede carregada.")
    known = matches[0] if matches else None
    target = workspace.dataset_for(target_tag) if target_tag else known
    if target_tag and target is None:
        raise CompositionError("A rede escolhida não está mais carregada.")
    if known is not None and target is not known:
        raise CompositionError(f"Este arquivo já pertence à rede {known.tag}; confira o vínculo escolhido.")
    if correspondences and target is None:
        raise CompositionError("Selecione a rede de destino das correspondências.")
    prior = None if target is None else target.registry
    if target is not None and prior is None:
        raise CompositionError("A fonte de destino não possui cadastro de rede. Reimporte-a primeiro.")
    explicit_correspondences = bool(correspondences)
    if prior and not correspondences:
        matching_bindings = [binding for binding in prior.bindings
                             if binding.file.path == file.path or binding.file.digest == file.digest]
        if matching_bindings:
            correspondences = matching_bindings[-1].correspondences
    seen = set()
    for mapping in correspondences:
        pair = (mapping.entity, mapping.incoming_id)
        if mapping.entity not in (*ENTITY_BY_NAME, "circuits") or not all((mapping.incoming_id, mapping.existing_id)):
            raise CompositionError("Correspondência inválida: informe entidade e ambos os IDs.")
        if pair in seen:
            raise CompositionError(f"Correspondência repetida: {pair}.")
        seen.add(pair)
        if prior is None or EquipmentKey(prior.network_id, mapping.entity, mapping.existing_id) not in prior.records:
            raise CompositionError(f"Destino inexistente: {mapping.entity} {mapping.existing_id}.")
        model = dataset.catalog if mapping.entity == "circuits" else getattr(dataset, mapping.entity)
        native_ids = (() if model is None else
                      tuple(item.circuit_id for item in model.definitions) if mapping.entity == "circuits" else
                      getattr(model, ENTITY_BY_NAME[mapping.entity].id_column))
        if explicit_correspondences and mapping.incoming_id not in native_ids:
            raise CompositionError(f"ID ausente no banco: {mapping.entity} {mapping.incoming_id}.")
    # A releitura do mesmo arquivo pode ampliar a seleção anterior. Restringir
    # a união de IDs também conserva chaves cujas duas pontas agora estão presentes.
    if known is not None and not replace_workspace:
        if not known.chosen_circuit_ids or not circuit_ids:
            circuit_ids = ()
        elif dataset.catalog is not None:
            available = {item.circuit_id for item in dataset.catalog.definitions}
            reverse = {item.existing_id: item.incoming_id for item in correspondences if item.entity == "circuits"}
            previous_ids = {reverse.get(value, value) for value in known.chosen_circuit_ids}
            circuit_ids = tuple(sorted(set(circuit_ids) | (previous_ids & available)))
    selected = restrict_to_circuits(dataset, circuit_ids)
    # Compatibilidade do adaptador legado. O projeto usa UUIDs próprios;
    # esta chave de origem não é o equipment_id canônico do equipamento.
    network_id = prior.network_id if prior else "access:" + file.digest
    incoming = _capture(selected, network_id, file, correspondences, cancel_check)
    previous = dict(prior.records) if prior and not replace_workspace else {}
    # Substituir o mapa mantém a identidade da rede, mas a seleção é só a atual.
    comparable = prior.records if prior else {}
    conflicts = tuple(RecordConflict(comparable[key], value) for key, value in incoming.items()
                      if key in comparable and not _equal(comparable[key].values, value.values))
    decisions = {}
    if conflicts:
        if resolve_conflicts is None:
            raise CompositionError(f"{len(conflicts)} registros divergentes exigem revisão.")
        decisions = resolve_conflicts(conflicts)
        if decisions is None:
            raise InterruptedError("Revisão do cadastro cancelada.")
        if any(decisions.get(conflict.key) not in ("existing", "incoming") for conflict in conflicts):
            raise CompositionError("Resolva todos os conflitos antes de importar.")
    _check(cancel_check)
    for key, record in incoming.items():
        old = comparable.get(key)
        if old is not None:
            accepted = old if decisions.get(key) == "existing" else record
            record = replace(accepted, equipment_id=old.equipment_id,
                             origins=tuple(dict.fromkeys((*old.origins, *record.origins))))
        previous[key] = record
    binding = SourceBinding(file, correspondences)
    bindings = tuple(dict.fromkeys((*(prior.bindings if prior else ()), binding)))
    registry = NetworkRegistry(network_id, previous, bindings)
    tag = target.tag if target else workspace.next_tag()
    template = replace(selected, tag=tag, name=target.name if target else selected.name)
    circuit_aliases = {item.incoming_id: item.existing_id for item in correspondences if item.entity == "circuits"}
    chosen = tuple(sorted(circuit_aliases.get(item, item) for item in circuit_ids))
    if target is not None and not replace_workspace:
        chosen = () if not target.chosen_circuit_ids or not circuit_ids else tuple(sorted(
            set(target.chosen_circuit_ids) | set(chosen)))
    template = replace(template, chosen_circuit_ids=chosen)
    candidate = registry.materialize(template, cancel_check)
    _check(cancel_check)
    updated = workspace.replaced_by(candidate) if replace_workspace else workspace.registered(candidate)
    return updated, candidate
