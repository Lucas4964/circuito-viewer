"""Leitura e gravação das curvas do usuário e das categorias em JSON.

Camada de núcleo: toca o disco, mas não importa Qt. O caminho é injetável em
todas as funções pela mesma razão que ``load_opendss_settings`` recebe o
``QSettings`` — sem isso, um teste gravaria por cima do arquivo real de quem
está desenvolvendo.

**Onde os arquivos moram.** Em ``circuit_viewer/dados/curvas.json`` e, ao lado,
``categorias_curvas.json``, localizados do mesmo jeito que ``phase_config`` e
``mdb_mapping`` localizam a pasta ``config``: relativo ao próprio pacote. São
dados do usuário, não dados empacotados, e por isso ``dados/`` fica fora do
``package-data``. A contrapartida conhecida: numa instalação por wheel a pasta
cai em ``site-packages``, que pode ser somente leitura — nesse caso a gravação
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
    CURVE_PERIODS,
    HOURLY_CURVE_POINT_COUNT,
    Curve,
    new_curve_id,
)
from .curvas_categorias import CurveCategory, category_sort_key
from .opendss_export import parse_number


# Versão do formato em disco. O leitor aceita um arquivo de versão maior e apenas
# avisa — o que ele não entender já é descartado registro a registro, e recusar o
# arquivo inteiro só faria o usuário perder o que ainda era legível.
CURVES_FILE_VERSION = 1
CATEGORIES_FILE_VERSION = 1

_DATA_DIRECTORY = "dados"
_CURVES_FILENAME = "curvas.json"
_CATEGORIES_FILENAME = "categorias_curvas.json"


def default_curves_path() -> Path:
    """Arquivo de curvas do usuário, ao lado do pacote."""

    return Path(__file__).resolve().parent / _DATA_DIRECTORY / _CURVES_FILENAME


def default_categories_path() -> Path:
    """Arquivo das categorias de curva, ao lado do de curvas."""

    return default_curves_path().with_name(_CATEGORIES_FILENAME)


def categories_path_for(curves_path: str | Path | None) -> Path:
    """O arquivo de categorias que acompanha um arquivo de curvas.

    Ligar um ao outro é o que mantém os testes isolados: quem injeta um
    ``curvas.json`` temporário ganha, sem pedir, as categorias no mesmo
    diretório, em vez de gravar por cima das categorias reais.
    """

    if curves_path is None:
        return default_categories_path()
    return Path(curves_path).with_name(_CATEGORIES_FILENAME)


def _resolve(path: str | Path | None) -> Path:
    return default_curves_path() if path is None else Path(path)


@dataclass(frozen=True, slots=True)
class CurvesLoadResult:
    """Resultado da leitura: o que sobreviveu e, se algo caiu, o motivo."""

    curves: tuple[Curve, ...]
    issue: str | None = None


@dataclass(frozen=True, slots=True)
class CategoriesLoadResult:
    """Resultado da leitura das categorias, com a mesma tolerância das curvas."""

    categories: tuple[CurveCategory, ...]
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


def _coerce_integer(raw: object) -> int | None:
    """Inteiro do arquivo, aceitando ``"3"`` e ``3.0``; ``None`` se não for."""

    number = _coerce_value(raw)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _identifier(raw: object) -> str:
    """Identificador textual; número também vale, como no banco de origem."""

    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return ""
    return " ".join(str(raw).split())


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
    # O ID de vínculo é opcional; número também vale, porque no banco de origem
    # ele é inteiro e um arquivo editado à mão pode trazê-lo sem aspas.
    link_id = _identifier(entry.get("id_vinculo"))
    # Período desconhecido some sem levar a curva junto: os 24 valores continuam
    # válidos, só o rótulo do tipo de dia não.
    raw_period = entry.get("periodo")
    period = raw_period.strip().upper() if isinstance(raw_period, str) else ""
    if period not in CURVE_PERIODS:
        period = ""
    try:
        return Curve(curve_id, name.strip(), tuple(values), link_id, period)
    except ValueError:
        return None


def _read_entries(
    target: Path,
    *,
    key: str,
    version_limit: int,
    missing_list: str,
) -> tuple[list | None, list[str], str | None]:
    """Lê a lista ``key`` do arquivo. **Nunca levanta.**

    Devolve ``(entradas, avisos, problema)``. Arquivo inexistente volta como
    ``([], [], None)``: primeira execução não é problema, e avisar sobre ela
    seria ruído.
    """

    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], [], None
    except (OSError, UnicodeDecodeError) as exc:
        return None, [], f"Não foi possível ler {target.name}: {exc}"

    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [], f"{target.name} não é um JSON válido (linha {exc.lineno})."

    if not isinstance(payload, dict):
        return None, [], f"{target.name} não tem o formato esperado."

    notes: list[str] = []
    version = payload.get("version")
    if isinstance(version, int) and version > version_limit:
        notes.append(
            f"{target.name} foi gravado por uma versão mais nova do programa; "
            "o que não for reconhecido será ignorado."
        )

    entries = payload.get(key)
    if not isinstance(entries, list):
        return None, [], f"{target.name} {missing_list}"
    return entries, notes, None


def load_curves(path: str | Path | None = None) -> CurvesLoadResult:
    """Lê as curvas gravadas. **Nunca levanta.**"""

    entries, notes, problem = _read_entries(
        _resolve(path),
        key="curvas",
        version_limit=CURVES_FILE_VERSION,
        missing_list="não tem a lista de curvas.",
    )
    if entries is None:
        return CurvesLoadResult((), problem)

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


def _write_json_atomically(target: Path, payload: dict) -> None:
    """Grava ``payload`` de forma atômica. Levanta ``OSError`` se o disco recusar.

    A gravação passa por um arquivo temporário seguido de ``os.replace`` para
    que uma queda no meio do caminho não deixe o arquivo truncado — o usuário
    perderia todo o cadastro por causa de um salvamento interrompido.
    """

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


def save_curves(
    curves: Sequence[Curve],
    path: str | Path | None = None,
) -> None:
    """Grava as curvas de forma atômica. Levanta ``OSError`` se o disco recusar."""

    _write_json_atomically(
        _resolve(path),
        {
            "version": CURVES_FILE_VERSION,
            "curvas": [
                {
                    "id": curve.curve_id,
                    **({"id_vinculo": curve.link_id} if curve.link_id else {}),
                    **({"periodo": curve.period} if curve.period else {}),
                    "nome": curve.name,
                    "valores": list(curve.values),
                }
                for curve in curves
            ],
        },
    )


def _category_from_entry(entry: object) -> CurveCategory | None:
    """Interpreta uma categoria do arquivo, ou devolve ``None`` se não der."""

    if not isinstance(entry, dict):
        return None
    description = entry.get("descricao")
    kind = entry.get("tipo")
    numbers = {
        key: _coerce_value(entry.get(key)) for key in ("min", "max", "part_merc")
    }
    integers = {
        key: _coerce_integer(entry.get(key)) for key in ("classe", "pu_max", "tipodem")
    }
    if (
        not isinstance(description, str)
        or not isinstance(kind, str)
        or any(value is None for value in numbers.values())
        or any(value is None for value in integers.values())
    ):
        return None
    raw_factor = entry.get("fat_pot")
    power_factor = None if raw_factor is None else _coerce_value(raw_factor)
    if raw_factor is not None and power_factor is None:
        return None
    try:
        return CurveCategory(
            category_id=_identifier(entry.get("cat_id")),
            description=" ".join(description.split()),
            consumer_class=integers["classe"],
            minimum=numbers["min"],
            maximum=numbers["max"],
            market_share=numbers["part_merc"],
            kind=kind.strip().upper(),
            pu_max=integers["pu_max"],
            demand_type=integers["tipodem"],
            power_factor=power_factor,
        )
    except ValueError:
        return None


def load_curve_categories(
    path: str | Path | None = None,
) -> CategoriesLoadResult:
    """Lê as categorias gravadas, em ordem de ``CAT_ID``. **Nunca levanta.**"""

    target = default_categories_path() if path is None else Path(path)
    entries, notes, problem = _read_entries(
        target,
        key="categorias",
        version_limit=CATEGORIES_FILE_VERSION,
        missing_list="não tem a lista de categorias.",
    )
    if entries is None:
        return CategoriesLoadResult((), problem)

    categories: dict[str, CurveCategory] = {}
    discarded = 0
    duplicated = 0
    for entry in entries:
        category = _category_from_entry(entry)
        if category is None:
            discarded += 1
        elif category.category_id in categories:
            duplicated += 1
        else:
            categories[category.category_id] = category
    if discarded:
        notes.append(
            f"{discarded} categoria(s) foram ignoradas por estarem incompletas "
            "ou inválidas."
        )
    if duplicated:
        notes.append(f"{duplicated} categoria(s) repetidas foram ignoradas.")
    ordered = tuple(sorted(categories.values(), key=category_sort_key))
    return CategoriesLoadResult(ordered, " ".join(notes) or None)


def save_curve_categories(
    categories: Sequence[CurveCategory],
    path: str | Path | None = None,
) -> None:
    """Grava as categorias de forma atômica. Levanta ``OSError`` se o disco recusar."""

    _write_json_atomically(
        default_categories_path() if path is None else Path(path),
        {
            "version": CATEGORIES_FILE_VERSION,
            "categorias": [
                {
                    "cat_id": category.category_id,
                    "descricao": category.description,
                    "classe": category.consumer_class,
                    "min": category.minimum,
                    "max": category.maximum,
                    "part_merc": category.market_share,
                    "tipo": category.kind,
                    "pu_max": category.pu_max,
                    "tipodem": category.demand_type,
                    **(
                        {}
                        if category.power_factor is None
                        else {"fat_pot": category.power_factor}
                    ),
                }
                for category in categories
            ],
        },
    )
