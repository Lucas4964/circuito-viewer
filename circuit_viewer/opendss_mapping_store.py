"""Mapas persistentes entre IDs importados e nomes das bibliotecas OpenDSS."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .opendss_library import normalize_library_name


MAPPING_FILE_VERSION = 1
_PACKAGE_DIRECTORY = Path(__file__).resolve().parent
_CABLE_MAP_FILENAME = "mapa_cabos.json"
_ARRANGEMENT_MAP_FILENAME = "mapa_arranjos.json"


class OpenDssMappingFormatError(ValueError):
    """Arquivo de mapa estruturalmente inválido."""


@dataclass(frozen=True, slots=True)
class LibraryNameMapping:
    source_id: str
    library_name: str

    def __post_init__(self) -> None:
        source_id = str(self.source_id).strip()
        library_name = normalize_library_name(self.library_name)
        if not source_id:
            raise ValueError("O ID do mapeamento não pode ficar vazio.")
        if not library_name:
            raise ValueError("O nome da biblioteca não pode ficar vazio.")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "library_name", library_name)


@dataclass(frozen=True, slots=True)
class OpenDssLibraryMappings:
    cables: tuple[LibraryNameMapping, ...] = ()
    arrangements: tuple[LibraryNameMapping, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cables", _validated_entries(self.cables, "CABO_ID"))
        object.__setattr__(
            self,
            "arrangements",
            _validated_entries(self.arrangements, "ARRANJO_ID"),
        )


@dataclass(frozen=True, slots=True)
class LibraryMapLoadResult:
    entries: tuple[LibraryNameMapping, ...]
    issue: str | None = None


def default_cable_map_path() -> Path:
    return _PACKAGE_DIRECTORY / "dados" / _CABLE_MAP_FILENAME


def default_arrangement_map_path() -> Path:
    return _PACKAGE_DIRECTORY / "dados" / _ARRANGEMENT_MAP_FILENAME


def _validated_entries(
    entries: Sequence[LibraryNameMapping],
    id_label: str,
) -> tuple[LibraryNameMapping, ...]:
    normalized: list[LibraryNameMapping] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, LibraryNameMapping):
            raise TypeError("Cada vínculo deve ser LibraryNameMapping.")
        if entry.source_id in seen:
            raise ValueError(f"{id_label} duplicado no mapa: '{entry.source_id}'.")
        seen.add(entry.source_id)
        normalized.append(entry)
    return tuple(sorted(normalized, key=lambda item: item.source_id.casefold()))


def _read_payload(path: str | Path) -> Mapping[str, object]:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except UnicodeDecodeError as exc:
        raise OpenDssMappingFormatError(f"{target.name} não está em UTF-8.") from exc
    except json.JSONDecodeError as exc:
        raise OpenDssMappingFormatError(
            f"{target.name} não é um JSON válido (linha {exc.lineno})."
        ) from exc
    if not isinstance(payload, dict):
        raise OpenDssMappingFormatError(f"{target.name} deve conter um objeto JSON.")
    if payload.get("versao") != MAPPING_FILE_VERSION:
        raise OpenDssMappingFormatError(
            f"{target.name}: versão incompatível; esperado {MAPPING_FILE_VERSION}."
        )
    return payload


def _read_map(
    path: str | Path,
    *,
    root_key: str,
    id_key: str,
) -> tuple[LibraryNameMapping, ...]:
    raw_entries = _read_payload(path).get(root_key)
    if not isinstance(raw_entries, list):
        raise OpenDssMappingFormatError(f"Campo '{root_key}' deve ser uma lista.")
    entries: list[LibraryNameMapping] = []
    for row, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            raise OpenDssMappingFormatError(f"Relação {row} deve ser um objeto JSON.")
        source_id = raw.get(id_key)
        name = raw.get("NOME")
        if not isinstance(source_id, str) or not isinstance(name, str):
            raise OpenDssMappingFormatError(
                f"Relação {row}: '{id_key}' e 'NOME' devem ser textos."
            )
        try:
            entries.append(LibraryNameMapping(source_id, name))
        except ValueError as exc:
            raise OpenDssMappingFormatError(f"Relação {row}: {exc}") from exc
    try:
        return _validated_entries(entries, id_key)
    except ValueError as exc:
        raise OpenDssMappingFormatError(str(exc)) from exc


def read_cable_map(path: str | Path) -> tuple[LibraryNameMapping, ...]:
    return _read_map(path, root_key="mapa_cabos", id_key="CABO_ID")


def read_arrangement_map(path: str | Path) -> tuple[LibraryNameMapping, ...]:
    return _read_map(path, root_key="mapa_arranjos", id_key="ARRANJO_ID")


def _load_map(path: Path, reader) -> LibraryMapLoadResult:  # noqa: ANN001
    try:
        return LibraryMapLoadResult(reader(path))
    except FileNotFoundError:
        return LibraryMapLoadResult(())
    except (OSError, OpenDssMappingFormatError) as exc:
        return LibraryMapLoadResult((), str(exc))


def load_cable_map(path: str | Path | None = None) -> LibraryMapLoadResult:
    target = default_cable_map_path() if path is None else Path(path)
    return _load_map(target, read_cable_map)


def load_arrangement_map(path: str | Path | None = None) -> LibraryMapLoadResult:
    target = default_arrangement_map_path() if path is None else Path(path)
    return _load_map(target, read_arrangement_map)


def cable_map_payload(entries: Sequence[LibraryNameMapping]) -> dict[str, object]:
    values = _validated_entries(entries, "CABO_ID")
    return {
        "versao": MAPPING_FILE_VERSION,
        "mapa_cabos": [
            {"CABO_ID": item.source_id, "NOME": item.library_name} for item in values
        ],
    }


def arrangement_map_payload(entries: Sequence[LibraryNameMapping]) -> dict[str, object]:
    values = _validated_entries(entries, "ARRANJO_ID")
    return {
        "versao": MAPPING_FILE_VERSION,
        "mapa_arranjos": [
            {"ARRANJO_ID": item.source_id, "NOME": item.library_name}
            for item in values
        ],
    }


def write_json_files_atomically(payloads: Mapping[str | Path, Mapping[str, object]]) -> None:
    """Grava um ou mais JSONs, restaurando os anteriores se uma troca falhar."""

    prepared: list[tuple[Path, Path]] = []
    previous: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for raw_target, payload in payloads.items():
            target = Path(raw_target)
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            target.parent.mkdir(parents=True, exist_ok=True)
            previous[target] = target.read_bytes() if target.exists() else None
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent,
                prefix=f"{target.name}.",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            prepared.append((target, temporary))
        for target, temporary in prepared:
            os.replace(temporary, target)
            replaced.append(target)
    except BaseException:
        for target in reversed(replaced):
            old = previous[target]
            if old is None:
                target.unlink(missing_ok=True)
            else:
                descriptor, restore_name = tempfile.mkstemp(
                    dir=target.parent,
                    prefix=f"{target.name}.",
                    suffix=".restore",
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(old)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(restore_name, target)
        raise
    finally:
        for _target, temporary in prepared:
            temporary.unlink(missing_ok=True)


def save_cable_map(
    entries: Sequence[LibraryNameMapping],
    path: str | Path | None = None,
) -> None:
    target = default_cable_map_path() if path is None else Path(path)
    write_json_files_atomically({target: cable_map_payload(entries)})


def save_arrangement_map(
    entries: Sequence[LibraryNameMapping],
    path: str | Path | None = None,
) -> None:
    target = default_arrangement_map_path() if path is None else Path(path)
    write_json_files_atomically({target: arrangement_map_payload(entries)})
