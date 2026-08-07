"""Leitura e gravação das curvas do usuário em JSON.

Camada de núcleo: toca o disco, mas não importa Qt. O caminho é injetável em
todas as funções pela mesma razão que ``load_opendss_settings`` recebe o
``QSettings`` — sem isso, um teste gravaria por cima do arquivo real de quem
está desenvolvendo.

**Onde o arquivo mora.** Em ``circuit_viewer/dados/curvas.json``, localizado do
mesmo jeito que ``phase_config`` e ``mdb_mapping`` localizam a pasta ``config``:
relativo ao próprio pacote. É dado do usuário, não dado empacotado, e por isso
``dados/`` fica fora do ``package-data`` e dentro do ``.gitignore``. A
contrapartida conhecida: numa instalação por wheel a pasta cai em
``site-packages``, que pode ser somente leitura — nesse caso :func:`save_curves`
levanta ``OSError`` e a janela mostra a mensagem, em vez de quebrar.

**Ler nunca levanta.** É a mesma regra dura de ``settings_from_mapping``: um
arquivo corrompido, editado à mão ou gravado por uma versão anterior não pode
impedir a aplicação de abrir. O que não puder ser lido é descartado e o motivo
volta em :attr:`CurvesLoadResult.issue`, para a janela avisar sem travar nada.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .curvas import (
    HOURLY_CURVE_POINT_COUNT,
    Curve,
    new_curve_id,
)
from .opendss_export import parse_number


# Versão do formato em disco. O leitor aceita um arquivo de versão maior e apenas
# avisa — o que ele não entender já é descartado registro a registro, e recusar o
# arquivo inteiro só faria o usuário perder o que ainda era legível.
CURVES_FILE_VERSION = 1

_DATA_DIRECTORY = "dados"
_CURVES_FILENAME = "curvas.json"


def default_curves_path() -> Path:
    """Arquivo de curvas do usuário, ao lado do pacote."""

    return Path(__file__).resolve().parent / _DATA_DIRECTORY / _CURVES_FILENAME


def _resolve(path: str | Path | None) -> Path:
    return default_curves_path() if path is None else Path(path)


@dataclass(frozen=True, slots=True)
class CurvesLoadResult:
    """Resultado da leitura: o que sobreviveu e, se algo caiu, o motivo."""

    curves: tuple[Curve, ...]
    issue: str | None = None


def _coerce_value(raw: object) -> float | None:
    """Converte um valor do arquivo em ``float`` finito, ou ``None``.

    Aceita número e texto: um arquivo editado à mão pode trazer ``"0,42"``, e
    recusá-lo obrigaria o usuário a conhecer a regra de separador do JSON. Quem
    interpreta o texto é ``parse_number``, a regra única do projeto.
    """

    if isinstance(raw, bool):
        # ``bool`` é subclasse de ``int``: sem esta guarda, ``true`` viraria 1,0.
        return None
    if isinstance(raw, (int, float)):
        number = float(raw)
    elif isinstance(raw, str):
        number = parse_number(raw)
        if number is None:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _curve_from_entry(entry: object) -> Curve | None:
    """Interpreta um objeto do arquivo, ou devolve ``None`` se não der.

    Chaves desconhecidas são ignoradas de propósito: é o que permitirá
    acrescentar ``"tipo"`` ou ``"unidade"`` no futuro sem virar a versão do
    formato e sem quebrar um arquivo lido por uma build anterior.
    """

    if not isinstance(entry, dict):
        return None
    name = entry.get("nome")
    if not isinstance(name, str) or not name.strip():
        return None
    raw_values = entry.get("valores")
    if (
        not isinstance(raw_values, list)
        or len(raw_values) != HOURLY_CURVE_POINT_COUNT
    ):
        return None
    values: list[float] = []
    for raw in raw_values:
        value = _coerce_value(raw)
        if value is None:
            return None
        values.append(value)
    raw_id = entry.get("id")
    # Identificador ausente é migração silenciosa, não erro: um arquivo escrito
    # à mão não tem por que inventar um uuid.
    curve_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else new_curve_id()
    try:
        return Curve(curve_id, name.strip(), tuple(values))
    except ValueError:
        return None


def load_curves(path: str | Path | None = None) -> CurvesLoadResult:
    """Lê as curvas gravadas. **Nunca levanta.**"""

    target = _resolve(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Primeira execução não é problema, e avisar sobre ela seria ruído.
        return CurvesLoadResult(())
    except (OSError, UnicodeDecodeError) as exc:
        return CurvesLoadResult(
            (),
            f"Não foi possível ler {target.name}: {exc}",
        )

    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        return CurvesLoadResult(
            (),
            f"{target.name} não é um JSON válido (linha {exc.lineno}).",
        )

    if not isinstance(payload, dict):
        return CurvesLoadResult(
            (),
            f"{target.name} não tem o formato esperado.",
        )

    notes: list[str] = []
    version = payload.get("version")
    if isinstance(version, int) and version > CURVES_FILE_VERSION:
        notes.append(
            f"{target.name} foi gravado por uma versão mais nova do programa; "
            "o que não for reconhecido será ignorado."
        )

    entries = payload.get("curvas")
    if not isinstance(entries, list):
        return CurvesLoadResult(
            (),
            f"{target.name} não tem a lista de curvas.",
        )

    curves: list[Curve] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    discarded = 0
    duplicated = 0
    for entry in entries:
        curve = _curve_from_entry(entry)
        if curve is None:
            discarded += 1
            continue
        name_key = curve.name.casefold()
        if curve.curve_id in seen_ids or name_key in seen_names:
            duplicated += 1
            continue
        seen_ids.add(curve.curve_id)
        seen_names.add(name_key)
        curves.append(curve)

    if discarded:
        notes.append(
            f"{discarded} curva(s) foram ignoradas por estarem incompletas ou "
            "inválidas."
        )
    if duplicated:
        notes.append(f"{duplicated} curva(s) repetidas foram ignoradas.")

    return CurvesLoadResult(tuple(curves), " ".join(notes) or None)


def save_curves(
    curves: Sequence[Curve],
    path: str | Path | None = None,
) -> None:
    """Grava as curvas de forma atômica. Levanta ``OSError`` se o disco recusar.

    A gravação passa por um arquivo temporário seguido de ``os.replace`` para
    que uma queda no meio do caminho não deixe um ``curvas.json`` truncado — o
    usuário perderia todo o cadastro por causa de um salvamento interrompido.
    """

    target = _resolve(path)
    payload = {
        "version": CURVES_FILE_VERSION,
        "curvas": [
            {
                "id": curve.curve_id,
                "nome": curve.name,
                "valores": list(curve.values),
            }
            for curve in curves
        ],
    }
    # ``ensure_ascii=False`` mantém os acentos legíveis e ``indent`` deixa o
    # arquivo diferenciável à mão, como o ``fases2.json``.
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    target.parent.mkdir(parents=True, exist_ok=True)
    # O temporário nasce **no mesmo diretório** do alvo: ``os.replace`` só é
    # atômico dentro do mesmo sistema de arquivos, e o %TEMP% do Windows
    # frequentemente está em outro volume.
    handle_fd, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f"{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(
            handle_fd, "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(text)
            handle.flush()
            # Sem o fsync, uma queda de energia logo após o replace pode deixar
            # o nome definitivo apontando para um conteúdo ainda não gravado.
            os.fsync(handle.fileno())
        # ``os.replace`` e não ``os.rename``: no Windows o rename falha quando o
        # destino existe, que é o caso do segundo salvamento em diante.
        os.replace(temp_name, target)
    except BaseException:
        # BaseException também cobre KeyboardInterrupt, para não deixar lixo ao
        # lado do arquivo do usuário.
        Path(temp_name).unlink(missing_ok=True)
        raise
