"""Persistência JSON atômica das faixas de níveis de tensão.

**Onde o arquivo mora.** Em ``circuit_viewer/dados/faixas_tensao.json``, ao lado
das curvas e dos patamares: é dado do usuário, editável pela própria aplicação,
e não dado empacotado como o que vive em ``config/``.

**Ler nunca levanta.** Arquivo ausente é o caso comum — a primeira execução —, e
devolve os padrões do PRODIST em silêncio. Qualquer outra falha devolve os mesmos
padrões e o motivo em :attr:`VoltageBandsLoadResult.issue`, para a janela
mostrar; o que não pode acontecer é a aplicação não abrir por causa de um arquivo
de cores.

**Infinito vira ``null``.** ``json.dumps`` escreveria ``Infinity``, que o padrão
JSON não define e outro leitor recusaria. O máximo ausente é o infinito, e é só
a faixa mais alta que o usa.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .voltage_bands import (
    ReservedColors,
    VoltageBand,
    VoltageBandTable,
    VoltageBandsError,
    default_voltage_band_table,
)


VOLTAGE_BANDS_FILE_VERSION = 1
_DATA_DIRECTORY = "dados"
_FILENAME = "faixas_tensao.json"

#: Nome de cada categoria reservada no arquivo. Separado do rótulo exibido para
#: que renomear um rótulo na tela não invalide os arquivos já gravados.
_RESERVED_KEYS = {
    "no_line_voltage": "sem_tensao_de_linha",
    "de_energized": "sem_tensao",
    "no_data": "sem_resultado",
}


def default_voltage_bands_path() -> Path:
    """Arquivo de faixas do usuário, ao lado do pacote."""

    return Path(__file__).resolve().parent / _DATA_DIRECTORY / _FILENAME


def _resolve(path: str | Path | None) -> Path:
    return default_voltage_bands_path() if path is None else Path(path)


@dataclass(frozen=True, slots=True)
class VoltageBandsLoadResult:
    """O que sobreviveu à leitura e, se algo caiu, o motivo."""

    table: VoltageBandTable
    issue: str | None = None


def _number(entry: dict[str, Any], key: str, row: int) -> float:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{key}' da faixa {row} deve ser um número")
    return float(value)


def _band_from_payload(entry: object, row: int) -> VoltageBand:
    if not isinstance(entry, dict):
        raise ValueError(f"a faixa {row} não é um objeto")
    label = entry.get("nome")
    if not isinstance(label, str):
        raise ValueError(f"'nome' da faixa {row} deve ser texto")
    color = entry.get("cor")
    if not isinstance(color, str):
        raise ValueError(f"'cor' da faixa {row} deve ser texto")
    severity = entry.get("gravidade")
    if isinstance(severity, bool) or not isinstance(severity, int):
        raise ValueError(f"'gravidade' da faixa {row} deve ser um inteiro")
    # Ausente ou nulo é o infinito: é como a faixa mais alta se escreve.
    raw_maximum = entry.get("maximo_pu")
    maximum = math.inf if raw_maximum is None else _number(entry, "maximo_pu", row)
    return VoltageBand(
        label,
        color,
        _number(entry, "minimo_pu", row),
        maximum,
        severity,
    )


def _reserved_from_payload(payload: object) -> ReservedColors:
    if payload is None:
        return ReservedColors()
    if not isinstance(payload, dict):
        raise ValueError("'reservadas' deve ser um objeto")
    values: dict[str, str] = {}
    for field_name, key in _RESERVED_KEYS.items():
        color = payload.get(key)
        if color is None:
            continue
        if not isinstance(color, str):
            raise ValueError(f"'{key}' deve ser texto")
        values[field_name] = color
    return ReservedColors(**values)


def _table_from_payload(payload: object) -> VoltageBandTable:
    if not isinstance(payload, dict):
        raise ValueError("a raiz não é um objeto JSON")
    entries = payload.get("faixas")
    if not isinstance(entries, list):
        raise ValueError("a lista 'faixas' não foi encontrada")
    bands = tuple(
        _band_from_payload(entry, row) for row, entry in enumerate(entries, start=1)
    )
    return VoltageBandTable(bands, _reserved_from_payload(payload.get("reservadas")))


def load_voltage_bands(path: str | Path | None = None) -> VoltageBandsLoadResult:
    """Lê o cadastro; qualquer falha devolve as faixas padrão e um aviso."""

    target = _resolve(path)
    defaults = default_voltage_band_table()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return VoltageBandsLoadResult(defaults)
    except (OSError, UnicodeDecodeError) as exc:
        return VoltageBandsLoadResult(
            defaults, f"Não foi possível ler {target.name}: {exc}"
        )
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        return VoltageBandsLoadResult(
            defaults,
            f"{target.name} não é um JSON válido (linha {exc.lineno}). "
            "As faixas padrão foram carregadas.",
        )
    notes: list[str] = []
    if isinstance(payload, dict):
        version = payload.get("version")
        if isinstance(version, int) and version > VOLTAGE_BANDS_FILE_VERSION:
            notes.append(
                f"{target.name} foi gravado por uma versão mais nova; "
                "somente os campos conhecidos foram lidos."
            )
    try:
        table = _table_from_payload(payload)
    except (TypeError, ValueError, VoltageBandsError) as exc:
        return VoltageBandsLoadResult(
            defaults,
            f"{target.name} contém uma configuração inválida: {exc}. "
            "As faixas padrão foram carregadas.",
        )
    return VoltageBandsLoadResult(table, " ".join(notes) or None)


def save_voltage_bands(
    table: VoltageBandTable,
    path: str | Path | None = None,
) -> None:
    """Grava uma tabela já validada usando substituição atômica."""

    if not isinstance(table, VoltageBandTable):
        raise TypeError("table deve ser um VoltageBandTable.")
    target = _resolve(path)
    payload = {
        "version": VOLTAGE_BANDS_FILE_VERSION,
        "faixas": [
            {
                "nome": band.label,
                "cor": band.color,
                "minimo_pu": band.minimum_pu,
                "maximo_pu": None if math.isinf(band.maximum_pu) else band.maximum_pu,
                "gravidade": band.severity,
            }
            for band in table.bands
        ],
        "reservadas": {
            key: getattr(table.reserved, field_name)
            for field_name, key in _RESERVED_KEYS.items()
        },
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
