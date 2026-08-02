"""Configuração externa da correspondência entre FASES2 e número de fases."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

import numpy as np

from .model import IndexArray


PHASE_COLORS = ("#0000FF", "#00FF00", "#FF0000")
UNMAPPED_PHASE_COLOR = "#555555"


class PhaseConfigurationError(ValueError):
    """Erro legível ao carregar ou validar ``fases2.json``."""


@dataclass(frozen=True, slots=True)
class PhaseMappingEntry:
    fases2: str
    name: str | None
    phase_count: int


@dataclass(frozen=True, slots=True)
class PhaseClassification:
    """Categorias de renderização e resumo dos valores sem relação."""

    style_indices: IndexArray
    unmapped_count: int
    unmapped_values: tuple[str, ...]

    def __post_init__(self) -> None:
        styles = self.style_indices
        if styles.dtype != np.dtype(np.intp) or styles.ndim != 1:
            raise ValueError("As categorias de fases devem ser um vetor de índices.")
        if styles.flags.writeable:
            raise ValueError("As categorias de fases devem ser imutáveis.")
        if self.unmapped_count < 0:
            raise ValueError("A contagem sem relação não pode ser negativa.")


@dataclass(frozen=True, slots=True)
class PhaseConfiguration:
    entries: tuple[PhaseMappingEntry, ...]
    _phase_count_by_value: Mapping[str, int] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("A configuração deve conter ao menos uma relação.")
        for entry in self.entries:
            if normalize_phase_value(entry.fases2) != entry.fases2:
                raise ValueError("Os valores FASES2 devem estar normalizados.")
            if entry.phase_count not in {1, 2, 3}:
                raise ValueError("O número de fases deve estar entre 1 e 3.")
        mapping = {entry.fases2: entry.phase_count for entry in self.entries}
        if len(mapping) != len(self.entries):
            raise ValueError("A configuração contém valores FASES2 duplicados.")
        object.__setattr__(
            self,
            "_phase_count_by_value",
            MappingProxyType(mapping),
        )

    @property
    def phase_count_by_value(self) -> Mapping[str, int]:
        return self._phase_count_by_value

    def classify(self, values: Iterable[str]) -> PhaseClassification:
        source_values = tuple(str(value) for value in values)
        styles = np.full(len(source_values), -1, dtype=np.intp)
        unmapped: dict[str, str] = {}
        for index, original in enumerate(source_values):
            key = original.strip().casefold()
            phase_count = self._phase_count_by_value.get(key)
            if phase_count is None:
                display = original.strip() or "<vazio>"
                unmapped.setdefault(key, display)
                continue
            styles[index] = phase_count - 1
        styles.setflags(write=False)
        unknown_values = tuple(
            sorted(unmapped.values(), key=lambda value: value.casefold())
        )
        return PhaseClassification(
            styles,
            int(np.count_nonzero(styles < 0)),
            unknown_values,
        )


def normalize_phase_value(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("FASES2 deve ser texto ou número.")
    if isinstance(value, int):
        normalized = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("FASES2 numérico deve ser finito.")
        normalized = format(value, ".15g")
    elif isinstance(value, str):
        normalized = value.strip().casefold()
    else:
        raise ValueError("FASES2 deve ser texto ou número.")
    if not normalized:
        raise ValueError("FASES2 não pode ser vazio.")
    return normalized


def default_phase_configuration_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "fases2.json"


def load_phase_configuration(path: str | Path | None = None) -> PhaseConfiguration:
    source = default_phase_configuration_path() if path is None else Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PhaseConfigurationError(
            f"O arquivo {source} não está codificado em UTF-8."
        ) from exc
    except OSError as exc:
        raise PhaseConfigurationError(
            f"Não foi possível ler {source}: {exc.strerror or exc}"
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PhaseConfigurationError(
            f"JSON inválido em {source}, linha {exc.lineno}, coluna {exc.colno}."
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise PhaseConfigurationError(
            "A raiz de fases2.json deve ser uma lista não vazia."
        )

    entries: list[PhaseMappingEntry] = []
    seen: dict[str, int] = {}
    for row_number, raw_entry in enumerate(payload, start=1):
        if not isinstance(raw_entry, dict):
            raise PhaseConfigurationError(
                f"Relação {row_number}: cada item deve ser um objeto JSON."
            )
        if "FASES2" not in raw_entry or "NUMERO_FASES" not in raw_entry:
            raise PhaseConfigurationError(
                f"Relação {row_number}: FASES2 e NUMERO_FASES são obrigatórios."
            )
        try:
            fases2 = normalize_phase_value(raw_entry["FASES2"])
        except ValueError as exc:
            raise PhaseConfigurationError(f"Relação {row_number}: {exc}") from exc
        if fases2 in seen:
            raise PhaseConfigurationError(
                f"Relação {row_number}: FASES2 duplicado; já aparece na relação "
                f"{seen[fases2]}."
            )
        phase_count = raw_entry["NUMERO_FASES"]
        if (
            isinstance(phase_count, bool)
            or not isinstance(phase_count, int)
            or phase_count not in {1, 2, 3}
        ):
            raise PhaseConfigurationError(
                f"Relação {row_number}: NUMERO_FASES deve ser um inteiro entre 1 e 3."
            )
        raw_name = raw_entry.get("NOME")
        if raw_name is not None and not isinstance(raw_name, str):
            raise PhaseConfigurationError(
                f"Relação {row_number}: NOME deve ser texto quando informado."
            )
        name = None if raw_name is None else raw_name.strip() or None
        seen[fases2] = row_number
        entries.append(PhaseMappingEntry(fases2, name, phase_count))
    return PhaseConfiguration(tuple(entries))
