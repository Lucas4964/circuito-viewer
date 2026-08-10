"""Sessão compartilhada dos mapas de IDs para nomes das bibliotecas OpenDSS."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PyQt6.QtCore import QObject, pyqtSignal

from .opendss_library import ArrangementDefinition, CableDefinition, normalize_library_name
from .opendss_library_store import (
    cables_payload,
    default_cables_path,
    default_geometries_path,
    geometries_payload,
)
from .opendss_mapping_store import (
    LibraryNameMapping,
    OpenDssLibraryMappings,
    arrangement_map_payload,
    cable_map_payload,
    default_arrangement_map_path,
    default_cable_map_path,
    load_arrangement_map,
    load_cable_map,
    write_json_files_atomically,
)


class MappedLibraryItemError(ValueError):
    def __init__(self, label: str, source_ids: Sequence[str]) -> None:
        self.label = label
        self.source_ids = tuple(source_ids)
        joined = ", ".join(self.source_ids)
        super().__init__(
            f"A alteração removeria {label.lower()} usado(s) pelos vínculos: {joined}. "
            "Remova ou altere esses vínculos em Configurações > OpenDSS."
        )


class OpenDssMappingSession(QObject):
    mapsSaved = pyqtSignal(int, int)

    def __init__(
        self,
        *,
        cable_map_path: str | Path | None = None,
        arrangement_map_path: str | Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.cable_map_path = (
            default_cable_map_path() if cable_map_path is None else Path(cable_map_path)
        )
        self.arrangement_map_path = (
            default_arrangement_map_path()
            if arrangement_map_path is None
            else Path(arrangement_map_path)
        )
        cable_load = load_cable_map(self.cable_map_path)
        arrangement_load = load_arrangement_map(self.arrangement_map_path)
        self.cable_issue = cable_load.issue
        self.arrangement_issue = arrangement_load.issue
        self._cables = cable_load.entries
        self._arrangements = arrangement_load.entries

    @property
    def mappings(self) -> OpenDssLibraryMappings:
        return OpenDssLibraryMappings(self._cables, self._arrangements)

    def save_maps(self, mappings: OpenDssLibraryMappings) -> bool:
        payloads: dict[Path, dict[str, object]] = {}
        if mappings.cables != self._cables or self.cable_issue:
            payloads[self.cable_map_path] = cable_map_payload(mappings.cables)
        if mappings.arrangements != self._arrangements or self.arrangement_issue:
            payloads[self.arrangement_map_path] = arrangement_map_payload(
                mappings.arrangements
            )
        if not payloads:
            return False
        write_json_files_atomically(payloads)
        self._cables = mappings.cables
        self._arrangements = mappings.arrangements
        self.cable_issue = None
        self.arrangement_issue = None
        self.mapsSaved.emit(len(self._cables), len(self._arrangements))
        return True

    def save_cable_map(
        self,
        entries: Sequence[LibraryNameMapping],
    ) -> bool:
        """Salva somente o mapa de cabos, sem tocar no mapa de arranjos."""

        cables = OpenDssLibraryMappings(cables=tuple(entries)).cables
        if cables == self._cables and self.cable_issue is None:
            return False
        write_json_files_atomically(
            {self.cable_map_path: cable_map_payload(cables)}
        )
        self._cables = cables
        self.cable_issue = None
        self.mapsSaved.emit(len(self._cables), len(self._arrangements))
        return True

    def save_arrangement_map(
        self,
        entries: Sequence[LibraryNameMapping],
    ) -> bool:
        """Salva somente o mapa de arranjos, sem tocar no mapa de cabos."""

        arrangements = OpenDssLibraryMappings(
            arrangements=tuple(entries)
        ).arrangements
        if arrangements == self._arrangements and self.arrangement_issue is None:
            return False
        write_json_files_atomically(
            {self.arrangement_map_path: arrangement_map_payload(arrangements)}
        )
        self._arrangements = arrangements
        self.arrangement_issue = None
        self.mapsSaved.emit(len(self._cables), len(self._arrangements))
        return True

    def mapped_cable_source_ids(self, name: str) -> tuple[str, ...]:
        target = normalize_library_name(name)
        return tuple(item.source_id for item in self._cables if item.library_name == target)

    def mapped_arrangement_source_ids(self, name: str) -> tuple[str, ...]:
        target = normalize_library_name(name)
        return tuple(
            item.source_id for item in self._arrangements if item.library_name == target
        )

    def _migrated(
        self,
        entries: Sequence[LibraryNameMapping],
        old_items: Sequence[object],
        new_items: Sequence[object],
        *,
        id_attribute: str,
        label: str,
    ) -> tuple[LibraryNameMapping, ...]:
        old_id_by_name = {
            normalize_library_name(getattr(item, "name")): str(getattr(item, id_attribute))
            for item in old_items
        }
        new_name_by_id = {
            str(getattr(item, id_attribute)): normalize_library_name(getattr(item, "name"))
            for item in new_items
        }
        new_names = set(new_name_by_id.values())
        migrated: list[LibraryNameMapping] = []
        broken: list[str] = []
        for entry in entries:
            old_id = old_id_by_name.get(entry.library_name)
            if old_id is None:
                replacement = entry.library_name if entry.library_name in new_names else None
            else:
                # O vínculo acompanha a identidade estável do item. Isso também
                # cobre uma troca de nomes entre dois itens, na qual o nome
                # antigo ainda existe, mas passou a identificar outro objeto.
                replacement = new_name_by_id.get(old_id)
                # Uma importação pode recriar o catálogo com IDs internos
                # diferentes. Como o contrato público do mapa é por nome, a
                # referência continua válida se o mesmo nome canônico existir.
                if replacement is None and entry.library_name in new_names:
                    replacement = entry.library_name
            if replacement is None:
                broken.append(entry.source_id)
            else:
                migrated.append(LibraryNameMapping(entry.source_id, replacement))
        if broken:
            raise MappedLibraryItemError(label, broken)
        return tuple(migrated)

    def validate_cable_replacement(
        self,
        old_items: Sequence[CableDefinition],
        new_items: Sequence[CableDefinition],
    ) -> tuple[LibraryNameMapping, ...]:
        return self._migrated(
            self._cables,
            old_items,
            new_items,
            id_attribute="cable_id",
            label="Cabo",
        )

    def validate_arrangement_replacement(
        self,
        old_items: Sequence[ArrangementDefinition],
        new_items: Sequence[ArrangementDefinition],
    ) -> tuple[LibraryNameMapping, ...]:
        return self._migrated(
            self._arrangements,
            old_items,
            new_items,
            id_attribute="arrangement_id",
            label="Arranjo",
        )

    def save_cable_library(
        self,
        old_items: Sequence[CableDefinition],
        new_items: Sequence[CableDefinition],
        library_path: str | Path | None,
    ) -> None:
        migrated = self.validate_cable_replacement(old_items, new_items)
        target = default_cables_path() if library_path is None else Path(library_path)
        payloads: dict[Path, dict[str, object]] = {target: cables_payload(new_items)}
        if migrated != self._cables:
            payloads[self.cable_map_path] = cable_map_payload(migrated)
        write_json_files_atomically(payloads)
        self._cables = migrated
        if self.cable_map_path in payloads:
            self.cable_issue = None

    def save_geometry_library(
        self,
        old_arrangements: Sequence[ArrangementDefinition],
        new_arrangements: Sequence[ArrangementDefinition],
        geometries: Sequence[object],
        library_path: str | Path | None,
    ) -> None:
        migrated = self.validate_arrangement_replacement(
            old_arrangements, new_arrangements
        )
        target = default_geometries_path() if library_path is None else Path(library_path)
        payloads: dict[Path, dict[str, object]] = {
            target: geometries_payload(new_arrangements, geometries)
        }
        if migrated != self._arrangements:
            payloads[self.arrangement_map_path] = arrangement_map_payload(migrated)
        write_json_files_atomically(payloads)
        self._arrangements = migrated
        if self.arrangement_map_path in payloads:
            self.arrangement_issue = None
