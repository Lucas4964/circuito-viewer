"""Sessao Qt das montagens automaticas derivadas do modelo de trechos."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from .model import LineNetworkModel
from .opendss_automatic_assembly import (
    AutomaticAssembly,
    AutomaticAssemblyResult,
    build_automatic_assemblies,
)
from .opendss_library import OpenDssLibraryCatalog
from .opendss_library_session import OpenDssLibrarySession
from .opendss_mapping_session import OpenDssMappingSession
from .phase_config import PhaseConfiguration


class OpenDssAutomaticAssemblySession(QObject):
    """Mantem somente o resultado ativo; nenhuma montagem e persistida."""

    changed = pyqtSignal()

    def __init__(
        self,
        library_session: OpenDssLibrarySession,
        mapping_session: OpenDssMappingSession,
        phase_configuration: PhaseConfiguration | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.library_session = library_session
        self.mapping_session = mapping_session
        self.phase_configuration = phase_configuration
        self.line_model: LineNetworkModel | None = None
        self.catalog: OpenDssLibraryCatalog = library_session.saved_catalog()
        self.result = AutomaticAssemblyResult()

        # Somente eventos de salvamento alimentam o modelo ativo. Os sinais de
        # rascunho (cablesChanged/geometriesChanged) nao sao observados.
        library_session.cablesSaved.connect(self._on_saved_source_changed)
        library_session.geometriesSaved.connect(self._on_saved_source_changed)
        mapping_session.mapsSaved.connect(self._on_saved_source_changed)

    def _on_saved_source_changed(self, *_args) -> None:  # noqa: ANN002
        self.rebuild()

    def set_line_model(self, model: LineNetworkModel | None) -> None:
        if model is self.line_model:
            return
        self.line_model = model
        self.rebuild()

    def rebuild(self) -> None:
        self.catalog = self.library_session.saved_catalog()
        self.result = build_automatic_assemblies(
            self.line_model,
            self.phase_configuration,
            self.catalog,
            self.mapping_session.mappings,
        )
        self.changed.emit()

    def assembly(self, assembly_id: str | None) -> AutomaticAssembly | None:
        if assembly_id is None:
            return None
        return next(
            (
                item
                for item in self.result.assemblies
                if item.assembly_id == assembly_id
            ),
            None,
        )

    def assemblies_using_cable(self, cable_id: str) -> tuple[AutomaticAssembly, ...]:
        return tuple(
            item
            for item in self.result.assemblies
            if cable_id in item.geometry.cable_ids
        )

    def assemblies_using_arrangement(
        self, arrangement_id: str
    ) -> tuple[AutomaticAssembly, ...]:
        return tuple(
            item
            for item in self.result.assemblies
            if item.key.arrangement_id == arrangement_id
        )
