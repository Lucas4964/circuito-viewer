"""Montagens OpenDSS derivadas dos trechos atualmente carregados.

O resolvedor deste modulo e deliberadamente independente de Qt e de disco. Ele
recebe apenas o retrato salvo das bibliotecas/mapas e o modelo de trechos. As
montagens resultantes sao efemeras: identificam as combinacoes realmente usadas
na tela, sem alterar ``geometrias.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Literal

from .model import LineNetworkModel
from .opendss_library import (
    ArrangementDefinition,
    ConductorPosition,
    GeometryDefinition,
    OpenDssLibraryCatalog,
    normalize_library_name,
)
from .opendss_mapping_store import OpenDssLibraryMappings
from .phase_config import PhaseConfiguration


IssueSeverity = Literal["error", "warning"]
_NO_NEUTRAL_CABLE_ID = "-1"


@dataclass(frozen=True, slots=True)
class AutomaticAssemblyKey:
    """Identidade eletrica e fisica de uma montagem automatica."""

    arrangement_id: str
    phase_cable_id: str
    neutral_cable_id: str | None
    phase_letters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AutomaticAssembly:
    """Uma montagem derivada e os trechos que compartilham sua combinacao."""

    key: AutomaticAssemblyKey
    assembly_id: str
    name: str
    arrangement: ArrangementDefinition
    geometry: GeometryDefinition
    segment_indices: tuple[int, ...]
    segment_ids: tuple[str, ...]

    @property
    def phase_letters(self) -> tuple[str, ...]:
        return self.key.phase_letters

    @property
    def usage_count(self) -> int:
        return len(self.segment_indices)


@dataclass(frozen=True, slots=True)
class AutomaticAssemblyIssue:
    """Diagnostico agrupado de trechos com a mesma pendencia."""

    severity: IssueSeverity
    field: str
    value: str
    reason: str
    segment_ids: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.segment_ids)


@dataclass(frozen=True, slots=True)
class AutomaticAssemblyResult:
    assemblies: tuple[AutomaticAssembly, ...] = ()
    issues: tuple[AutomaticAssemblyIssue, ...] = ()
    total_segments: int = 0
    assembled_segments: int = 0

    @property
    def unassembled_segments(self) -> int:
        return max(0, self.total_segments - self.assembled_segments)

    @property
    def warning_count(self) -> int:
        return sum(item.count for item in self.issues if item.severity == "warning")


def _mapping_index(entries) -> dict[str, str]:  # noqa: ANN001
    return {item.source_id: item.library_name for item in entries}


def _library_indices(
    catalog: OpenDssLibraryCatalog,
) -> tuple[dict[str, object], dict[str, object]]:
    cables = {normalize_library_name(item.name): item for item in catalog.cables}
    arrangements = {
        normalize_library_name(item.name): item for item in catalog.arrangements
    }
    return cables, arrangements


def _stable_id(key: AutomaticAssemblyKey) -> str:
    payload = json.dumps(
        [
            key.arrangement_id,
            key.phase_cable_id,
            key.neutral_cable_id,
            key.phase_letters,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("ascii")).hexdigest()[:20]
    return f"auto_{digest}"


def _display_value(value: object) -> str:
    text = str(value).strip()
    return text or "<vazio>"


def _resolved_item(
    source_id: str,
    source_map: dict[str, str],
    library: dict[str, object],
) -> tuple[object | None, str]:
    normalized_source = str(source_id).strip()
    library_name = source_map.get(normalized_source)
    if library_name is None:
        return None, "sem vinculo no mapa OpenDSS"
    item = library.get(normalize_library_name(library_name))
    if item is None:
        return None, f"mapeado para '{library_name}', ausente da biblioteca salva"
    return item, ""


def _assembly_sort_key(item: AutomaticAssembly) -> tuple[object, ...]:
    key = item.key
    return (
        item.name.casefold(),
        key.arrangement_id,
        key.phase_cable_id,
        key.neutral_cable_id or "",
        key.phase_letters,
        item.assembly_id,
    )


def build_automatic_assemblies(
    lines: LineNetworkModel | None,
    phase_configuration: PhaseConfiguration | None,
    catalog: OpenDssLibraryCatalog,
    mappings: OpenDssLibraryMappings,
) -> AutomaticAssemblyResult:
    """Deriva e agrupa montagens para os trechos carregados.

    Os primeiros ``k`` condutores de fase do arranjo sao usados em sequencia.
    Posicoes entre ``k`` e o ``nphases`` do arranjo sao descartadas. Posicoes
    posteriores (neutros) so permanecem quando ``CABON_ID`` resolve um cabo.
    """

    if lines is None:
        return AutomaticAssemblyResult()

    cable_map = _mapping_index(mappings.cables)
    arrangement_map = _mapping_index(mappings.arrangements)
    cables_by_name, arrangements_by_name = _library_indices(catalog)

    grouped: dict[AutomaticAssemblyKey, list[tuple[int, str]]] = {}
    issue_segments: dict[tuple[IssueSeverity, str, str, str], list[str]] = {}
    assembled_segments = 0

    def report(
        severity: IssueSeverity,
        field: str,
        value: object,
        reason: str,
        segment_id: str,
    ) -> None:
        issue_segments.setdefault(
            (severity, field, _display_value(value), reason), []
        ).append(segment_id)

    for segment_index in range(len(lines)):
        record = lines.record(segment_index)
        fatal = False

        phase_letters = (
            None
            if phase_configuration is None
            else phase_configuration.phase_letters_for_value(record.phases)
        )
        if phase_letters is None:
            report(
                "error",
                "FASES2",
                record.phases,
                "nao resolve fases eletricas validas",
                record.segment_id,
            )
            fatal = True

        raw_arrangement = record.arrangement_id.strip()
        arrangement, arrangement_reason = _resolved_item(
            raw_arrangement,
            arrangement_map,
            arrangements_by_name,
        )
        if arrangement is None:
            report(
                "error",
                "ARRANJO_ID",
                raw_arrangement,
                arrangement_reason,
                record.segment_id,
            )
            fatal = True

        raw_phase_cable = record.phase_cable_id.strip()
        phase_cable, phase_cable_reason = _resolved_item(
            raw_phase_cable,
            cable_map,
            cables_by_name,
        )
        if phase_cable is None:
            report(
                "error",
                "CABOF_ID",
                raw_phase_cable,
                phase_cable_reason,
                record.segment_id,
            )
            fatal = True

        if (
            phase_letters is not None
            and isinstance(arrangement, ArrangementDefinition)
            and arrangement.phase_count < len(phase_letters)
        ):
            report(
                "error",
                "ARRANJO_ID",
                raw_arrangement,
                f"oferece {arrangement.phase_count} fase(s), mas a linha exige {len(phase_letters)}",
                record.segment_id,
            )
            fatal = True

        if fatal:
            continue

        assert phase_letters is not None
        assert isinstance(arrangement, ArrangementDefinition)
        # A biblioteca de cabos so contem CableDefinition; manter o acesso
        # estrutural evita um import circular apenas para satisfazer o type checker.
        phase_cable_id = str(getattr(phase_cable, "cable_id"))

        neutral_positions = arrangement.positions[arrangement.phase_count :]
        neutral_cable = None
        if neutral_positions:
            raw_neutral_cable = record.neutral_cable_id.strip()
            if raw_neutral_cable != _NO_NEUTRAL_CABLE_ID:
                neutral_cable, neutral_reason = _resolved_item(
                    raw_neutral_cable,
                    cable_map,
                    cables_by_name,
                )
                if neutral_cable is None:
                    report(
                        "warning",
                        "CABON_ID",
                        raw_neutral_cable,
                        neutral_reason + "; posicoes de neutro removidas",
                        record.segment_id,
                    )

        effective_neutral_id = (
            None
            if neutral_cable is None
            else str(getattr(neutral_cable, "cable_id"))
        )
        key = AutomaticAssemblyKey(
            arrangement.arrangement_id,
            phase_cable_id,
            effective_neutral_id,
            tuple(phase_letters),
        )
        grouped.setdefault(key, []).append((segment_index, record.segment_id))
        assembled_segments += 1

    assemblies: list[AutomaticAssembly] = []
    for key, uses in grouped.items():
        base = catalog.arrangement(key.arrangement_id)
        phase_cable = catalog.cable(key.phase_cable_id)
        neutral_cable = catalog.cable(key.neutral_cable_id)
        if base is None or phase_cable is None:
            # Todas as referencias foram verificadas durante a primeira passada.
            # Esta guarda mantem a funcao total caso receba um catalogo mutado de
            # modo concorrente por um chamador externo.
            continue
        assembly_id = _stable_id(key)
        keep_neutral = neutral_cable is not None
        phase_display = "".join(key.phase_letters) + ("N" if keep_neutral else "")
        positions = [
            ConductorPosition(item.x, item.height)
            for item in base.positions[: len(key.phase_letters)]
        ]
        if keep_neutral:
            positions.extend(
                ConductorPosition(item.x, item.height)
                for item in base.positions[base.phase_count :]
            )
        derived_arrangement = ArrangementDefinition(
            arrangement_id=f"{assembly_id}_spacing",
            name=f"{base.name} - {phase_display}",
            phase_count=len(key.phase_letters),
            units=base.units,
            positions=positions,
            description=f"Derivado automaticamente de {base.name}.",
            source=base.source,
        )
        cable_ids = [key.phase_cable_id] * len(key.phase_letters)
        if keep_neutral:
            cable_ids.extend(
                [key.neutral_cable_id]
                * len(base.positions[base.phase_count :])
            )
        name = f"{base.name} | {phase_display} | {phase_cable.name}"
        if neutral_cable is not None:
            name += f" | N:{neutral_cable.name}"
        geometry = GeometryDefinition(
            geometry_id=assembly_id,
            name=name,
            arrangement_id=derived_arrangement.arrangement_id,
            cable_ids=cable_ids,
            reduce=keep_neutral,
            description="Montagem automatica, somente leitura.",
        )
        ordered_uses = tuple(sorted(uses, key=lambda item: (item[0], item[1])))
        assemblies.append(
            AutomaticAssembly(
                key=key,
                assembly_id=assembly_id,
                name=name,
                arrangement=derived_arrangement,
                geometry=geometry,
                segment_indices=tuple(item[0] for item in ordered_uses),
                segment_ids=tuple(item[1] for item in ordered_uses),
            )
        )

    issues = tuple(
        AutomaticAssemblyIssue(
            severity,
            field,
            value,
            reason,
            tuple(sorted(segment_ids, key=str.casefold)),
        )
        for (severity, field, value, reason), segment_ids in sorted(
            issue_segments.items(),
            key=lambda item: (
                0 if item[0][0] == "error" else 1,
                item[0][1],
                item[0][2].casefold(),
                item[0][3].casefold(),
            ),
        )
    )
    return AutomaticAssemblyResult(
        assemblies=tuple(sorted(assemblies, key=_assembly_sort_key)),
        issues=issues,
        total_segments=len(lines),
        assembled_segments=assembled_segments,
    )
