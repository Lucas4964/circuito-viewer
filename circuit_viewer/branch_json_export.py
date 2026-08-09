"""Exporta o snapshot já calculado dos ramais, sem reanalisar a topologia."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from .branch_analysis import BranchAnalysisResult, BranchRecord
from .equivalent_network import EquivalentNetworkResult
from .opendss_export import sanitize_dss_name


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]


class BranchJsonValidationError(ValueError):
    """Reúne todas as ausências de CODIGO antes que qualquer arquivo seja criado."""

    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(str(issue) for issue in issues)
        super().__init__(
            "A exportação JSON encontrou elementos sem CODIGO."
            if self.issues
            else "A exportação JSON é inválida."
        )


@dataclass(frozen=True, slots=True)
class BranchJsonExportResult:
    path: Path
    branch_count: int
    circuit_ids: tuple[str, ...]


def suggested_branch_json_filename(circuit_id: str | None) -> str:
    if circuit_id is None:
        return "ramais_todos.json"
    normalized = sanitize_dss_name(circuit_id) or "circuito"
    return f"ramais_{normalized}.json"


def _checked_codes(
    values: Sequence[str],
    indices,
    *,
    branch: BranchRecord,
    entity: str,
    identifiers: Sequence[str],
    issues: list[str],
    cancel_check: CancelCheck,
) -> list[str]:
    result: list[str] = []
    for raw_index in indices:
        if cancel_check():
            raise InterruptedError("Exportação JSON dos ramais cancelada.")
        index = int(raw_index)
        code = str(values[index])
        if not code.strip():
            issues.append(
                f"RAMAL-{branch.branch_id}: {entity} {identifiers[index]} sem CODIGO."
            )
        result.append(code)
    return result


def build_branch_json_payload(
    branches: BranchAnalysisResult,
    equivalent: EquivalentNetworkResult,
    branch_indices: Sequence[int],
    *,
    interest_branch_ids: Sequence[int] = (),
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    """Monta o objeto JSON somente por índices persistidos nos dois snapshots."""

    if equivalent.model.branches is not branches:
        raise ValueError("A rede equivalente não corresponde aos ramais exportados.")
    catalog = branches.source_catalog
    if catalog is None:
        raise ValueError("A análise de ramais não possui catálogo de origem.")
    loads = branches.source_loads
    if equivalent.model.source_loads is not loads:
        raise ValueError("As cargas equivalentes não correspondem aos ramais.")

    selected = tuple(int(index) for index in branch_indices)
    if len(set(selected)) != len(selected):
        raise ValueError("A seleção contém ramais duplicados.")
    if any(index < 0 or index >= len(branches.records) for index in selected):
        raise IndexError("A seleção contém um ramal inexistente.")
    selected = tuple(
        sorted(selected, key=lambda index: branches.records[index].branch_id)
    )
    interest = tuple(int(branch_id) for branch_id in interest_branch_ids)
    if len(set(interest)) != len(interest):
        raise ValueError("A lista de ramais de interesse contém IDs duplicados.")
    exported_branch_ids = {
        branches.records[index].branch_id for index in selected
    }
    if any(branch_id not in exported_branch_ids for branch_id in interest):
        raise ValueError(
            "Todo ramal de interesse deve pertencer aos ramais exportados."
        )
    interest = tuple(sorted(interest))

    cancelled = cancel_check or (lambda: False)
    bars = catalog.segments.bars
    segments = catalog.segments
    switches = catalog.switches
    switch_by_segment = (
        None if switches is None else switches.record_indices_by_segment
    )
    generator_updates = equivalent.model.source_generator_updates
    generators = (
        None if generator_updates is None else generator_updates.generators
    )
    issues: list[str] = []
    payload: dict[str, object] = {"ramais_interesse": list(interest)}
    total = len(selected)

    for position, branch_index in enumerate(selected, start=1):
        if cancelled():
            raise InterruptedError("Exportação JSON dos ramais cancelada.")
        branch = branches.records[branch_index]
        equivalent_index = equivalent.model.index_for_branch_id(branch.branch_id)
        if equivalent_index is None:
            raise ValueError(
                f"RAMAL-{branch.branch_id} não possui carga equivalente associada."
            )
        equivalent_record = equivalent.model.record(equivalent_index)

        start_code = str(branch.connection_bar_code)
        if not start_code.strip():
            issues.append(
                f"RAMAL-{branch.branch_id}: barra_inicio "
                f"{branch.connection_bar_id} sem CODIGO."
            )
        bar_codes = _checked_codes(
            bars.codes,
            branch.bar_indices,
            branch=branch,
            entity="barra",
            identifiers=bars.bar_ids,
            issues=issues,
            cancel_check=cancelled,
        )
        common_segment_indices = tuple(
            int(raw_index)
            for raw_index in branch.segment_indices
            if switch_by_segment is None
            or int(switch_by_segment[int(raw_index)]) < 0
        )
        segment_codes = _checked_codes(
            segments.codes,
            common_segment_indices,
            branch=branch,
            entity="trecho",
            identifiers=segments.segment_ids,
            issues=issues,
            cancel_check=cancelled,
        )
        load_codes = (
            []
            if loads is None
            else _checked_codes(
                loads.codes,
                equivalent_record.source_load_indices,
                branch=branch,
                entity="carga",
                identifiers=loads.load_ids,
                issues=issues,
                cancel_check=cancelled,
            )
        )
        generator_codes = (
            []
            if generators is None
            else _checked_codes(
                generators.generator_codes,
                equivalent_record.source_generator_indices,
                branch=branch,
                entity="gerador",
                identifiers=generators.generator_ids,
                issues=issues,
                cancel_check=cancelled,
            )
        )
        switch_codes = (
            []
            if switches is None
            else _checked_codes(
                switches.codes,
                branch.switch_indices,
                branch=branch,
                entity="chave",
                identifiers=switches.switch_ids,
                issues=issues,
                cancel_check=cancelled,
            )
        )
        payload[f"RAMAL-{branch.branch_id}"] = {
            "barra_inicio": start_code,
            "barras": bar_codes,
            "trechos": segment_codes,
            "cargas": load_codes,
            "geradores": generator_codes,
            "chaves": switch_codes,
            "fase": branch.phase,
            "remanejavel": bool(branch.removable),
        }
        if progress is not None:
            progress(position, total)

    if issues:
        raise BranchJsonValidationError(issues)
    return payload


def export_branches_json(
    path: str | Path,
    branches: BranchAnalysisResult,
    equivalent: EquivalentNetworkResult,
    branch_indices: Sequence[int],
    *,
    interest_branch_ids: Sequence[int] = (),
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> BranchJsonExportResult:
    """Valida, serializa e substitui o destino atomicamente."""

    cancelled = cancel_check or (lambda: False)
    selected = tuple(int(index) for index in branch_indices)
    payload = build_branch_json_payload(
        branches,
        equivalent,
        selected,
        interest_branch_ids=interest_branch_ids,
        cancel_check=cancelled,
        progress=progress,
    )
    if cancelled():
        raise InterruptedError("Exportação JSON dos ramais cancelada.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=4) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise InterruptedError("Exportação JSON dos ramais cancelada.")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    circuit_ids = tuple(
        sorted(
            {branches.records[index].circuit_id for index in selected},
            key=str.casefold,
        )
    )
    return BranchJsonExportResult(target, len(selected), circuit_ids)
