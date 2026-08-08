"""Persistência JSON atômica dos patamares de cálculo."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .calculation_levels import (
    CalculationLevel,
    CalculationLevelSchedule,
    default_calculation_levels,
)


CALCULATION_LEVELS_FILE_VERSION = 1
_DATA_DIRECTORY = "dados"
_FILENAME = "patamares.json"


def default_calculation_levels_path() -> Path:
    return Path(__file__).resolve().parent / _DATA_DIRECTORY / _FILENAME


def _resolve(path: str | Path | None) -> Path:
    return default_calculation_levels_path() if path is None else Path(path)


@dataclass(frozen=True, slots=True)
class CalculationLevelsLoadResult:
    schedule: CalculationLevelSchedule
    issue: str | None = None


def _schedule_from_payload(payload: object) -> CalculationLevelSchedule:
    if not isinstance(payload, dict):
        raise ValueError("a raiz não é um objeto JSON")
    entries = payload.get("patamares")
    if not isinstance(entries, list):
        raise ValueError("a lista 'patamares' não foi encontrada")
    levels: list[CalculationLevel] = []
    for row, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"o patamar {row} não é um objeto")
        try:
            levels.append(
                CalculationLevel(
                    entry["npat"],
                    entry["nome"],
                    entry["horario_ini"],
                    entry["horario_fim"],
                    entry["horario_ref"],
                )
            )
        except KeyError as exc:
            raise ValueError(
                f"falta o campo '{exc.args[0]}' no patamar {row}"
            ) from exc
    return CalculationLevelSchedule(tuple(levels))


def load_calculation_levels(
    path: str | Path | None = None,
) -> CalculationLevelsLoadResult:
    """Lê o cadastro; qualquer falha devolve a grade padrão e um aviso."""

    target = _resolve(path)
    defaults = default_calculation_levels()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CalculationLevelsLoadResult(defaults)
    except (OSError, UnicodeDecodeError) as exc:
        return CalculationLevelsLoadResult(
            defaults, f"Não foi possível ler {target.name}: {exc}"
        )
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        return CalculationLevelsLoadResult(
            defaults,
            f"{target.name} não é um JSON válido (linha {exc.lineno}). "
            "Os valores padrão foram carregados.",
        )
    notes: list[str] = []
    if isinstance(payload, dict):
        version = payload.get("version")
        if isinstance(version, int) and version > CALCULATION_LEVELS_FILE_VERSION:
            notes.append(
                f"{target.name} foi gravado por uma versão mais nova; "
                "somente os campos conhecidos foram lidos."
            )
    try:
        schedule = _schedule_from_payload(payload)
    except (TypeError, ValueError) as exc:
        return CalculationLevelsLoadResult(
            defaults,
            f"{target.name} contém uma configuração inválida: {exc}. "
            "Os valores padrão foram carregados.",
        )
    return CalculationLevelsLoadResult(schedule, " ".join(notes) or None)


def save_calculation_levels(
    schedule: CalculationLevelSchedule,
    path: str | Path | None = None,
) -> None:
    """Grava uma grade já validada usando substituição atômica."""

    if not isinstance(schedule, CalculationLevelSchedule):
        raise TypeError("schedule deve ser um CalculationLevelSchedule.")
    target = _resolve(path)
    payload = {
        "version": CALCULATION_LEVELS_FILE_VERSION,
        "patamares": [
            {
                "npat": item.npat,
                "nome": item.name,
                "horario_ini": item.start_hour,
                "horario_fim": item.end_hour,
                "horario_ref": item.reference_hour,
            }
            for item in schedule.levels
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f"{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
