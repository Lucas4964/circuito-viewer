"""Leitura e gravação das bibliotecas OpenDSS em JSON compatível."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .opendss_library import (
    LIBRARY_FILE_VERSION,
    ArrangementDefinition,
    CableDefinition,
    ConductorPosition,
    GeometryDefinition,
    LibraryFormatError,
    validate_unique_items,
)


_PACKAGE_DIRECTORY = Path(__file__).resolve().parent
_CABLES_DEFAULTS = "opendss_cables_defaults.json"
_GEOMETRIES_DEFAULTS = "opendss_geometries_defaults.json"
_CABLES_FILENAME = "cabos.json"
_GEOMETRIES_FILENAME = "geometrias.json"


@dataclass(frozen=True, slots=True)
class CablesLoadResult:
    cables: tuple[CableDefinition, ...]
    issue: str | None = None
    used_defaults: bool = False


@dataclass(frozen=True, slots=True)
class GeometriesLoadResult:
    arrangements: tuple[ArrangementDefinition, ...]
    geometries: tuple[GeometryDefinition, ...]
    issue: str | None = None
    used_defaults: bool = False


def default_cables_path() -> Path:
    return _PACKAGE_DIRECTORY / "dados" / _CABLES_FILENAME


def default_geometries_path() -> Path:
    return _PACKAGE_DIRECTORY / "dados" / _GEOMETRIES_FILENAME


def packaged_cables_defaults_path() -> Path:
    return _PACKAGE_DIRECTORY / "config" / _CABLES_DEFAULTS


def packaged_geometries_defaults_path() -> Path:
    return _PACKAGE_DIRECTORY / "config" / _GEOMETRIES_DEFAULTS


def _dict(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise LibraryFormatError(f"{label} deve ser um objeto JSON.")
    return value


def _text(
    entry: Mapping[str, object], key: str, *, required: bool = False
) -> str:
    value = entry.get(key)
    if value is None:
        if required:
            raise LibraryFormatError(f"Campo obrigatório '{key}' ausente.")
        return ""
    if not isinstance(value, str):
        raise LibraryFormatError(f"Campo '{key}' deve ser texto.")
    text = value.strip()
    if required and not text:
        raise LibraryFormatError(f"Campo obrigatório '{key}' vazio.")
    return text


def _number(entry: Mapping[str, object], key: str) -> float | None:
    value = entry.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LibraryFormatError(f"Campo '{key}' deve ser numérico.")
    number = float(value)
    if not math.isfinite(number):
        raise LibraryFormatError(f"Campo '{key}' deve ser finito.")
    return number


def _integer(entry: Mapping[str, object], key: str) -> int | None:
    number = _number(entry, key)
    if number is None:
        return None
    if not number.is_integer():
        raise LibraryFormatError(f"Campo '{key}' deve ser inteiro.")
    return int(number)


def _boolean(entry: Mapping[str, object], key: str, default: bool = False) -> bool:
    value = entry.get(key, default)
    if not isinstance(value, bool):
        raise LibraryFormatError(f"Campo '{key}' deve ser booleano.")
    return value


def cable_from_mapping(raw: object) -> CableDefinition:
    entry = _dict(raw, "Cada cabo")
    cable_type = _text(entry, "tipo") or "wire"
    if cable_type not in {"wire", "cn"}:
        raise LibraryFormatError("Campo 'tipo' deve ser 'wire' ou 'cn'.")
    return CableDefinition(
        cable_id=_text(entry, "id", required=True),
        name=_text(entry, "nome", required=True),
        cable_type=cable_type,
        family=_text(entry, "familia"),
        description=_text(entry, "descricao"),
        source=_text(entry, "fonte"),
        rac=_number(entry, "rac"),
        rdc=_number(entry, "rdc"),
        resistance_units=_text(entry, "runits"),
        gmr=_number(entry, "gmrac"),
        gmr_units=_text(entry, "gmrunits"),
        diameter=_number(entry, "diam"),
        radius=_number(entry, "radius"),
        radius_units=_text(entry, "radunits"),
        normal_amps=_number(entry, "normamps"),
        emergency_amps=_number(entry, "emergamps"),
        nominal_section=_number(entry, "secao"),
        radius_estimated=_boolean(entry, "raioEstimado"),
        strand_count=_integer(entry, "k"),
        strand_diameter=_number(entry, "diastrand"),
        strand_resistance=_number(entry, "rstrand"),
        strand_gmr=_number(entry, "gmrstrand"),
        relative_permittivity=_number(entry, "epsr"),
        insulation_layer=_number(entry, "inslayer"),
        insulation_diameter=_number(entry, "diains"),
        cable_diameter=_number(entry, "diacable"),
    )


def arrangement_from_mapping(raw: object) -> ArrangementDefinition:
    entry = _dict(raw, "Cada arranjo")
    raw_positions = entry.get("pos")
    if not isinstance(raw_positions, list) or not raw_positions:
        raise LibraryFormatError("Campo 'pos' deve ser uma lista não vazia.")
    positions: list[ConductorPosition] = []
    for raw_position in raw_positions:
        position = _dict(raw_position, "Cada posição")
        x = _number(position, "x")
        height = _number(position, "h")
        if x is None or height is None:
            raise LibraryFormatError("Cada posição precisa de 'x' e 'h'.")
        positions.append(ConductorPosition(x, height))
    conductor_count = _integer(entry, "nconds")
    phase_count = _integer(entry, "nphases")
    if conductor_count != len(positions):
        raise LibraryFormatError("'nconds' deve coincidir com o tamanho de 'pos'.")
    if phase_count is None or not 1 <= phase_count <= len(positions):
        raise LibraryFormatError("'nphases' deve ficar entre 1 e 'nconds'.")
    return ArrangementDefinition(
        arrangement_id=_text(entry, "id", required=True),
        name=_text(entry, "nome", required=True),
        phase_count=phase_count,
        units=_text(entry, "unidades", required=True),
        positions=positions,
        description=_text(entry, "descricao"),
        source=_text(entry, "fonte"),
    )


def geometry_from_mapping(raw: object) -> GeometryDefinition:
    entry = _dict(raw, "Cada montagem")
    raw_cables = entry.get("cabos")
    if not isinstance(raw_cables, list):
        raise LibraryFormatError("Campo 'cabos' deve ser uma lista.")
    cable_ids: list[str | None] = []
    for cable_id in raw_cables:
        if cable_id is None or cable_id == "":
            cable_ids.append(None)
        elif isinstance(cable_id, str):
            cable_ids.append(cable_id.strip() or None)
        else:
            raise LibraryFormatError("Cada referência em 'cabos' deve ser texto ou null.")
    return GeometryDefinition(
        geometry_id=_text(entry, "id", required=True),
        name=_text(entry, "nome", required=True),
        arrangement_id=_text(entry, "arranjoId", required=True),
        cable_ids=cable_ids,
        reduce=_boolean(entry, "reduce", True),
        description=_text(entry, "descricao"),
    )


def _read_payload(path: str | Path) -> Mapping[str, object]:
    target = Path(path)
    try:
        payload: Any = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except UnicodeDecodeError as exc:
        raise LibraryFormatError(f"{target.name} não está em UTF-8.") from exc
    except json.JSONDecodeError as exc:
        raise LibraryFormatError(
            f"{target.name} não é um JSON válido (linha {exc.lineno})."
        ) from exc
    except OSError:
        raise
    if not isinstance(payload, dict):
        raise LibraryFormatError(f"{target.name} deve conter um objeto JSON.")
    version = payload.get("versao")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise LibraryFormatError("Campo 'versao' deve ser inteiro.")
    return payload


def read_cables_file(path: str | Path) -> tuple[CableDefinition, ...]:
    payload = _read_payload(path)
    raw_cables = payload.get("cabos")
    if not isinstance(raw_cables, list):
        raise LibraryFormatError("Esperado um objeto com a lista 'cabos'.")
    cables = tuple(cable_from_mapping(raw) for raw in raw_cables)
    validate_unique_items(
        cables,
        id_attribute="cable_id",
        name_attribute="name",
        label="Cabo",
    )
    return cables


def read_geometries_file(
    path: str | Path,
) -> tuple[tuple[ArrangementDefinition, ...], tuple[GeometryDefinition, ...]]:
    payload = _read_payload(path)
    raw_arrangements = payload.get("arranjos")
    raw_geometries = payload.get("montagens")
    if not isinstance(raw_arrangements, list) or not isinstance(raw_geometries, list):
        raise LibraryFormatError(
            "Esperado um objeto com as listas 'arranjos' e 'montagens'."
        )
    arrangements = tuple(arrangement_from_mapping(raw) for raw in raw_arrangements)
    geometries = tuple(geometry_from_mapping(raw) for raw in raw_geometries)
    validate_unique_items(
        arrangements,
        id_attribute="arrangement_id",
        name_attribute="name",
        label="Arranjo",
    )
    validate_unique_items(
        geometries,
        id_attribute="geometry_id",
        name_attribute="name",
        label="Montagem",
    )
    return arrangements, geometries


def load_cables(
    path: str | Path | None = None,
    *,
    defaults_path: str | Path | None = None,
) -> CablesLoadResult:
    target = default_cables_path() if path is None else Path(path)
    defaults = (
        packaged_cables_defaults_path()
        if defaults_path is None
        else Path(defaults_path)
    )
    try:
        return CablesLoadResult(read_cables_file(target))
    except FileNotFoundError:
        return CablesLoadResult(read_cables_file(defaults), used_defaults=True)
    except (OSError, LibraryFormatError) as exc:
        return CablesLoadResult(
            read_cables_file(defaults),
            f"Não foi possível carregar {target.name}; os padrões foram usados: {exc}",
            True,
        )


def load_geometries(
    path: str | Path | None = None,
    *,
    defaults_path: str | Path | None = None,
) -> GeometriesLoadResult:
    target = default_geometries_path() if path is None else Path(path)
    defaults = (
        packaged_geometries_defaults_path()
        if defaults_path is None
        else Path(defaults_path)
    )
    try:
        arrangements, geometries = read_geometries_file(target)
        return GeometriesLoadResult(arrangements, geometries)
    except FileNotFoundError:
        arrangements, geometries = read_geometries_file(defaults)
        return GeometriesLoadResult(arrangements, geometries, used_defaults=True)
    except (OSError, LibraryFormatError) as exc:
        arrangements, geometries = read_geometries_file(defaults)
        return GeometriesLoadResult(
            arrangements,
            geometries,
            f"Não foi possível carregar {target.name}; os padrões foram usados: {exc}",
            True,
        )


def cable_to_mapping(cable: CableDefinition) -> dict[str, object]:
    result: dict[str, object] = {
        "id": cable.cable_id,
        "nome": cable.name,
        "tipo": cable.cable_type,
    }
    fields = (
        ("familia", cable.family),
        ("descricao", cable.description),
        ("fonte", cable.source),
        ("rac", cable.rac),
        ("rdc", cable.rdc),
        ("runits", cable.resistance_units),
        ("gmrac", cable.gmr),
        ("gmrunits", cable.gmr_units),
        ("diam", cable.diameter),
        ("radius", cable.radius),
        ("radunits", cable.radius_units),
        ("normamps", cable.normal_amps),
        ("emergamps", cable.emergency_amps),
        ("secao", cable.nominal_section),
        ("raioEstimado", True if cable.radius_estimated else None),
        ("k", cable.strand_count),
        ("diastrand", cable.strand_diameter),
        ("rstrand", cable.strand_resistance),
        ("gmrstrand", cable.strand_gmr),
        ("epsr", cable.relative_permittivity),
        ("inslayer", cable.insulation_layer),
        ("diains", cable.insulation_diameter),
        ("diacable", cable.cable_diameter),
    )
    result.update({key: value for key, value in fields if value not in {None, ""}})
    return result


def arrangement_to_mapping(item: ArrangementDefinition) -> dict[str, object]:
    result: dict[str, object] = {
        "id": item.arrangement_id,
        "nome": item.name,
        "nconds": item.conductor_count,
        "nphases": item.phase_count,
        "unidades": item.units,
        "pos": [{"x": point.x, "h": point.height} for point in item.positions],
    }
    if item.description:
        result["descricao"] = item.description
    if item.source:
        result["fonte"] = item.source
    return result


def geometry_to_mapping(item: GeometryDefinition) -> dict[str, object]:
    result: dict[str, object] = {
        "id": item.geometry_id,
        "nome": item.name,
        "arranjoId": item.arrangement_id,
        "reduce": item.reduce,
        "cabos": item.cable_ids,
    }
    if item.description:
        result["descricao"] = item.description
    return result


def _write_atomic(path: str | Path, payload: Mapping[str, object]) -> None:
    target = Path(path)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f"{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def save_cables(cables: Sequence[CableDefinition], path: str | Path | None = None) -> None:
    _write_atomic(default_cables_path() if path is None else path, cables_payload(cables))


def cables_payload(cables: Sequence[CableDefinition]) -> dict[str, object]:
    validate_unique_items(
        cables,
        id_attribute="cable_id",
        name_attribute="name",
        label="Cabo",
    )
    return {
        "versao": LIBRARY_FILE_VERSION,
        "cabos": [cable_to_mapping(item) for item in cables],
    }


def save_geometries(
    arrangements: Sequence[ArrangementDefinition],
    geometries: Sequence[GeometryDefinition],
    path: str | Path | None = None,
) -> None:
    _write_atomic(
        default_geometries_path() if path is None else path,
        geometries_payload(arrangements, geometries),
    )


def geometries_payload(
    arrangements: Sequence[ArrangementDefinition],
    geometries: Sequence[GeometryDefinition],
) -> dict[str, object]:
    validate_unique_items(
        arrangements,
        id_attribute="arrangement_id",
        name_attribute="name",
        label="Arranjo",
    )
    validate_unique_items(
        geometries,
        id_attribute="geometry_id",
        name_attribute="name",
        label="Montagem",
    )
    return {
        "versao": LIBRARY_FILE_VERSION,
        "arranjos": [arrangement_to_mapping(item) for item in arrangements],
        "montagens": [geometry_to_mapping(item) for item in geometries],
    }
