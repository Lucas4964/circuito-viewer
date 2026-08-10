"""Sessão Qt compartilhada pelas janelas das bibliotecas OpenDSS."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .opendss_library import (
    OpenDssLibraryCatalog,
    clone_cables,
    clone_geometries,
)
from .opendss_library_store import (
    CablesLoadResult,
    GeometriesLoadResult,
    load_cables,
    load_geometries,
    packaged_cables_defaults_path,
    packaged_geometries_defaults_path,
    read_cables_file,
    read_geometries_file,
    save_cables,
    save_geometries,
)
from .opendss_mapping_session import OpenDssMappingSession


class OpenDssLibrarySession(QObject):
    """Rascunhos compartilhados, persistência e sinais de atualização cruzada."""

    cablesChanged = pyqtSignal()
    geometriesChanged = pyqtSignal()
    cablesDirtyChanged = pyqtSignal(bool)
    geometriesDirtyChanged = pyqtSignal(bool)
    cablesSaved = pyqtSignal(int)
    geometriesSaved = pyqtSignal(int, int)

    def __init__(
        self,
        *,
        cables_path: str | Path | None = None,
        geometries_path: str | Path | None = None,
        mapping_session: OpenDssMappingSession | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.cables_path = cables_path
        self.geometries_path = geometries_path
        self.mapping_session = mapping_session
        self.cables_load: CablesLoadResult = load_cables(cables_path)
        self.geometries_load: GeometriesLoadResult = load_geometries(geometries_path)
        self._saved_cables = clone_cables(self.cables_load.cables)
        self._saved_arrangements, self._saved_geometries = clone_geometries(
            self.geometries_load.arrangements,
            self.geometries_load.geometries,
        )
        self.catalog = OpenDssLibraryCatalog(
            clone_cables(self._saved_cables),
            *clone_geometries(self._saved_arrangements, self._saved_geometries),
        )
        self._cables_dirty = False
        self._geometries_dirty = False

    @property
    def cables_dirty(self) -> bool:
        return self._cables_dirty

    @property
    def geometries_dirty(self) -> bool:
        return self._geometries_dirty

    @property
    def saved_cable_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self._saved_cables)

    @property
    def saved_arrangement_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self._saved_arrangements)

    def mark_cables_changed(self) -> None:
        self._set_cables_dirty(True)
        self.cablesChanged.emit()
        # Seletores, ampacidade e diagnósticos das montagens dependem dos cabos.
        self.geometriesChanged.emit()

    def mark_geometries_changed(self) -> None:
        self._set_geometries_dirty(True)
        self.geometriesChanged.emit()
        # A coluna de usos da janela de cabos depende das montagens.
        self.cablesChanged.emit()

    def _set_cables_dirty(self, dirty: bool) -> None:
        dirty = bool(dirty)
        if dirty != self._cables_dirty:
            self._cables_dirty = dirty
            self.cablesDirtyChanged.emit(dirty)

    def _set_geometries_dirty(self, dirty: bool) -> None:
        dirty = bool(dirty)
        if dirty != self._geometries_dirty:
            self._geometries_dirty = dirty
            self.geometriesDirtyChanged.emit(dirty)

    def replace_cables_from_file(self, path: str | Path) -> int:
        cables = read_cables_file(path)
        if self.mapping_session is not None:
            self.mapping_session.validate_cable_replacement(self._saved_cables, cables)
        self.catalog.cables = clone_cables(cables)
        self.mark_cables_changed()
        return len(cables)

    def replace_geometries_from_file(self, path: str | Path) -> tuple[int, int]:
        arrangements, geometries = read_geometries_file(path)
        if self.mapping_session is not None:
            self.mapping_session.validate_arrangement_replacement(
                self._saved_arrangements, arrangements
            )
        (
            self.catalog.arrangements,
            self.catalog.geometries,
        ) = clone_geometries(arrangements, geometries)
        self.mark_geometries_changed()
        return len(arrangements), len(geometries)

    def restore_default_cables(self) -> int:
        cables = read_cables_file(packaged_cables_defaults_path())
        if self.mapping_session is not None:
            self.mapping_session.validate_cable_replacement(self._saved_cables, cables)
        self.catalog.cables = clone_cables(cables)
        self.mark_cables_changed()
        return len(cables)

    def restore_default_geometries(self) -> tuple[int, int]:
        arrangements, geometries = read_geometries_file(
            packaged_geometries_defaults_path()
        )
        if self.mapping_session is not None:
            self.mapping_session.validate_arrangement_replacement(
                self._saved_arrangements, arrangements
            )
        (
            self.catalog.arrangements,
            self.catalog.geometries,
        ) = clone_geometries(arrangements, geometries)
        self.mark_geometries_changed()
        return len(arrangements), len(geometries)

    def save_cable_drafts(self) -> None:
        if self.mapping_session is None:
            save_cables(self.catalog.cables, self.cables_path)
        else:
            self.mapping_session.save_cable_library(
                self._saved_cables,
                self.catalog.cables,
                self.cables_path,
            )
        self._saved_cables = clone_cables(self.catalog.cables)
        self._set_cables_dirty(False)
        self.cablesSaved.emit(len(self.catalog.cables))

    def save_geometry_drafts(self) -> None:
        if self.mapping_session is None:
            save_geometries(
                self.catalog.arrangements,
                self.catalog.geometries,
                self.geometries_path,
            )
        else:
            self.mapping_session.save_geometry_library(
                self._saved_arrangements,
                self.catalog.arrangements,
                self.catalog.geometries,
                self.geometries_path,
            )
        self._saved_arrangements, self._saved_geometries = clone_geometries(
            self.catalog.arrangements,
            self.catalog.geometries,
        )
        self._set_geometries_dirty(False)
        self.geometriesSaved.emit(
            len(self.catalog.arrangements), len(self.catalog.geometries)
        )

    def discard_cable_drafts(self) -> None:
        self.catalog.cables = clone_cables(self._saved_cables)
        self._set_cables_dirty(False)
        self.cablesChanged.emit()
        self.geometriesChanged.emit()

    def discard_geometry_drafts(self) -> None:
        (
            self.catalog.arrangements,
            self.catalog.geometries,
        ) = clone_geometries(self._saved_arrangements, self._saved_geometries)
        self._set_geometries_dirty(False)
        self.geometriesChanged.emit()
        self.cablesChanged.emit()

    def export_cables(self, path: str | Path) -> None:
        save_cables(self.catalog.cables, path)

    def export_geometries(self, path: str | Path) -> None:
        save_geometries(self.catalog.arrangements, self.catalog.geometries, path)
