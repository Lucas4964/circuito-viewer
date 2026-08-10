"""Domínio das bibliotecas OpenDSS de condutores e geometrias.

Esta camada não depende de Qt nem dos modelos importados da concessionária. Um
``CableDefinition`` representa ``WireData`` ou ``CNData``; um
``ArrangementDefinition`` representa ``LineSpacing``; e uma
``GeometryDefinition`` combina um arranjo com um cabo por posição, como
``LineGeometry``. A separação explícita evita confundir estes condutores físicos
com :class:`circuit_viewer.model.CableRecord`, que contém parâmetros de
sequência usados pelo exportador atual.
"""

from __future__ import annotations

import copy
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Literal, Mapping, Sequence


LIBRARY_FILE_VERSION = 1
OPEN_DSS_UNITS: tuple[str, ...] = ("mi", "kft", "km", "m", "ft", "in", "cm")
GMR_TO_RADIUS_RATIO = 0.7788
DEFAULT_STRANDING_FILL_FACTOR = 0.75

_METERS_PER_UNIT: Mapping[str, float] = {
    "mi": 1609.344,
    "kft": 304.8,
    "km": 1000.0,
    "m": 1.0,
    "ft": 0.3048,
    "in": 0.0254,
    "cm": 0.01,
}
_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


class LibraryFormatError(ValueError):
    """Arquivo de biblioteca estruturalmente inválido."""


def normalize_library_name(value: object) -> str:
    """Nome canônico usado por bibliotecas e mapas OpenDSS."""

    return str(value).strip().upper()


@dataclass(slots=True)
class CableDefinition:
    """Condutor físico emitível como ``WireData`` ou ``CNData``."""

    cable_id: str
    name: str
    cable_type: Literal["wire", "cn"] = "wire"
    family: str = ""
    description: str = ""
    source: str = ""
    rac: float | None = None
    rdc: float | None = None
    resistance_units: str = "km"
    gmr: float | None = None
    gmr_units: str = "cm"
    diameter: float | None = None
    radius: float | None = None
    radius_units: str = "cm"
    normal_amps: float | None = None
    emergency_amps: float | None = None
    nominal_section: float | None = None
    radius_estimated: bool = False
    strand_count: int | None = None
    strand_diameter: float | None = None
    strand_resistance: float | None = None
    strand_gmr: float | None = None
    relative_permittivity: float | None = None
    insulation_layer: float | None = None
    insulation_diameter: float | None = None
    cable_diameter: float | None = None

    def __post_init__(self) -> None:
        self.name = normalize_library_name(self.name)

    @property
    def is_concentric(self) -> bool:
        return self.cable_type == "cn"


@dataclass(slots=True)
class ConductorPosition:
    x: float
    height: float


@dataclass(slots=True)
class ArrangementDefinition:
    """Posições de um ``LineSpacing``; os primeiros condutores são fases."""

    arrangement_id: str
    name: str
    phase_count: int
    units: str
    positions: list[ConductorPosition] = field(default_factory=list)
    description: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        self.name = normalize_library_name(self.name)

    @property
    def conductor_count(self) -> int:
        return len(self.positions)


@dataclass(slots=True)
class GeometryDefinition:
    """Montagem ``LineGeometry``: arranjo e cabo em cada posição."""

    geometry_id: str
    name: str
    arrangement_id: str
    cable_ids: list[str | None] = field(default_factory=list)
    reduce: bool = True
    description: str = ""


@dataclass(slots=True)
class OpenDssLibraryCatalog:
    """Catálogo editável e índices de integridade entre as três entidades."""

    cables: list[CableDefinition] = field(default_factory=list)
    arrangements: list[ArrangementDefinition] = field(default_factory=list)
    geometries: list[GeometryDefinition] = field(default_factory=list)

    def clone(self) -> "OpenDssLibraryCatalog":
        return copy.deepcopy(self)

    def cable(self, cable_id: str | None) -> CableDefinition | None:
        if cable_id is None:
            return None
        return next((item for item in self.cables if item.cable_id == cable_id), None)

    def arrangement(
        self, arrangement_id: str | None
    ) -> ArrangementDefinition | None:
        if arrangement_id is None:
            return None
        return next(
            (
                item
                for item in self.arrangements
                if item.arrangement_id == arrangement_id
            ),
            None,
        )

    def geometry(self, geometry_id: str | None) -> GeometryDefinition | None:
        if geometry_id is None:
            return None
        return next(
            (item for item in self.geometries if item.geometry_id == geometry_id),
            None,
        )

    def geometries_using_cable(self, cable_id: str) -> tuple[GeometryDefinition, ...]:
        return tuple(item for item in self.geometries if cable_id in item.cable_ids)

    def geometries_using_arrangement(
        self, arrangement_id: str
    ) -> tuple[GeometryDefinition, ...]:
        return tuple(
            item for item in self.geometries if item.arrangement_id == arrangement_id
        )

    def synchronize_geometry_slots(self, arrangement_id: str) -> None:
        arrangement = self.arrangement(arrangement_id)
        if arrangement is None:
            return
        count = arrangement.conductor_count
        for geometry in self.geometries_using_arrangement(arrangement_id):
            if len(geometry.cable_ids) < count:
                geometry.cable_ids.extend([None] * (count - len(geometry.cable_ids)))
            elif len(geometry.cable_ids) > count:
                del geometry.cable_ids[count:]


def _usable_number(value: float | int | None) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) != 0.0
    )


def cable_issues(cable: CableDefinition) -> tuple[str, ...]:
    """Problemas elétricos exibidos no cadastro; itens incompletos são válidos."""

    issues: list[str] = []
    if not _usable_number(cable.rac) and not _usable_number(cable.rdc):
        issues.append("resistência (Rac ou Rdc)")
    if cable.resistance_units not in OPEN_DSS_UNITS:
        issues.append("unidade de R")
    if not _usable_number(cable.gmr):
        issues.append("GMR")
    if cable.gmr_units not in OPEN_DSS_UNITS:
        issues.append("unidade do GMR")
    if not _usable_number(cable.radius) and not _usable_number(cable.diameter):
        issues.append("diâmetro ou raio")
    if cable.radius_units not in OPEN_DSS_UNITS:
        issues.append("unidade de diâmetro/raio")
    if cable.is_concentric:
        for value, label in (
            (cable.strand_count, "nº de fios do neutro (k)"),
            (cable.strand_diameter, "diâmetro do fio do neutro"),
            (cable.strand_resistance, "R do fio do neutro"),
            (cable.insulation_layer, "espessura da isolação"),
            (cable.insulation_diameter, "diâmetro sobre a isolação"),
            (cable.cable_diameter, "diâmetro externo do cabo"),
        ):
            if not _usable_number(value):
                issues.append(label)
    return tuple(issues)


def coincident_positions(arrangement: ArrangementDefinition) -> tuple[tuple[int, int], ...]:
    """Pares de posições idênticas, em índices baseados em um para exibição."""

    result: list[tuple[int, int]] = []
    for left, first in enumerate(arrangement.positions):
        for right in range(left + 1, len(arrangement.positions)):
            second = arrangement.positions[right]
            if first.x == second.x and first.height == second.height:
                result.append((left + 1, right + 1))
    return tuple(result)


def geometry_issues(
    geometry: GeometryDefinition,
    catalog: OpenDssLibraryCatalog,
) -> tuple[str, ...]:
    issues: list[str] = []
    arrangement = catalog.arrangement(geometry.arrangement_id)
    if arrangement is None:
        return ("arranjo ausente da biblioteca",)
    if len(geometry.cable_ids) != arrangement.conductor_count:
        issues.append("quantidade de cabos diferente da quantidade de posições")
    missing = [
        index + 1
        for index in range(arrangement.conductor_count)
        if index >= len(geometry.cable_ids)
        or catalog.cable(geometry.cable_ids[index]) is None
    ]
    if missing:
        issues.append(
            "posição sem cabo válido: " + ", ".join(str(index) for index in missing)
        )
    if not phase_cable_types_are_homogeneous(geometry, arrangement, catalog):
        issues.append("as fases misturam fio nu e cabo concêntrico")
    return tuple(issues)


def phase_cable_types_are_homogeneous(
    geometry: GeometryDefinition,
    arrangement: ArrangementDefinition,
    catalog: OpenDssLibraryCatalog,
) -> bool:
    types = {
        cable.cable_type
        for cable_id in geometry.cable_ids[: arrangement.phase_count]
        if (cable := catalog.cable(cable_id)) is not None
    }
    return len(types) <= 1


def geometry_ampacity(
    geometry: GeometryDefinition,
    catalog: OpenDssLibraryCatalog,
) -> float | None:
    """Menor ampacidade normal das fases, ou ``None`` se alguma não a declarar."""

    arrangement = catalog.arrangement(geometry.arrangement_id)
    if arrangement is None:
        return None
    values: list[float] = []
    for index in range(arrangement.phase_count):
        if index >= len(geometry.cable_ids):
            return None
        cable = catalog.cable(geometry.cable_ids[index])
        if cable is None or not _usable_number(cable.normal_amps):
            return None
        values.append(float(cable.normal_amps))
    return min(values) if values else None


def convert_length(value: float, source_unit: str, target_unit: str) -> float:
    try:
        source = _METERS_PER_UNIT[source_unit]
        target = _METERS_PER_UNIT[target_unit]
    except KeyError as exc:
        raise ValueError("Unidade OpenDSS inválida.") from exc
    return float(value) * source / target


def estimate_radius_from_gmr(cable: CableDefinition) -> float:
    if not _usable_number(cable.gmr):
        raise ValueError("Preencha o GMR antes de estimar o raio.")
    radius = convert_length(
        float(cable.gmr) / GMR_TO_RADIUS_RATIO,
        cable.gmr_units,
        cable.radius_units,
    )
    cable.radius = round(radius, 6)
    cable.diameter = None
    cable.radius_estimated = True
    return cable.radius


def estimate_diameter_from_section(
    cable: CableDefinition,
    section_mm2: float,
    fill_factor: float = DEFAULT_STRANDING_FILL_FACTOR,
) -> float:
    if not math.isfinite(section_mm2) or section_mm2 <= 0:
        raise ValueError("A seção nominal deve ser maior que zero.")
    if not math.isfinite(fill_factor) or not 0 < fill_factor <= 1:
        raise ValueError("O fator de preenchimento deve ficar entre zero e um.")
    diameter_mm = 2.0 * math.sqrt(section_mm2 / (math.pi * fill_factor))
    diameter = convert_length(diameter_mm / 1000.0, "m", cable.radius_units)
    cable.diameter = round(diameter, 6)
    cable.radius = None
    cable.nominal_section = float(section_mm2)
    cable.radius_estimated = True
    return cable.diameter


def sanitize_library_id(value: str, fallback: str = "item") -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    result = _ID_CHARS.sub("_", ascii_text.strip()).strip("_").lower()
    return result or fallback


def unique_id(base: str, current_ids: Iterable[str]) -> str:
    used = set(current_ids)
    root = sanitize_library_id(base)
    candidate = root
    suffix = 2
    while candidate in used:
        candidate = f"{root}_{suffix}"
        suffix += 1
    return candidate


def unique_name(base: str, current_names: Iterable[str]) -> str:
    names = {name.casefold() for name in current_names}
    root = str(base).strip() or "Item novo"
    if root.casefold() not in names:
        return root
    suffix = 2
    while f"{root} {suffix}".casefold() in names:
        suffix += 1
    return f"{root} {suffix}"


def validate_unique_items(
    items: Sequence[object],
    *,
    id_attribute: str,
    name_attribute: str,
    label: str,
) -> None:
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in items:
        item_id = str(getattr(item, id_attribute)).strip()
        name = str(getattr(item, name_attribute)).strip()
        if not item_id:
            raise LibraryFormatError(f"{label} com ID vazio.")
        if not name:
            raise LibraryFormatError(f"{label} com nome vazio.")
        if item_id in seen_ids:
            raise LibraryFormatError(f"ID duplicado em {label.lower()}: '{item_id}'.")
        name_key = name.casefold()
        if name_key in seen_names:
            raise LibraryFormatError(f"Nome duplicado em {label.lower()}: '{name}'.")
        seen_ids.add(item_id)
        seen_names.add(name_key)


def clone_cables(cables: Sequence[CableDefinition]) -> list[CableDefinition]:
    return copy.deepcopy(list(cables))


def clone_geometries(
    arrangements: Sequence[ArrangementDefinition],
    geometries: Sequence[GeometryDefinition],
) -> tuple[list[ArrangementDefinition], list[GeometryDefinition]]:
    return copy.deepcopy((list(arrangements), list(geometries)))
