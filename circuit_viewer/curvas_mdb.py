"""Leitura do banco de curvas típicas do Interplan (``Curvas.mdb``).

Camada de núcleo: recebe o banco pelo protocolo ``AccessDatabase`` e não importa
Qt, então os testes rodam com um banco falso, sem ``pyodbc`` nem driver ODBC.

Três tabelas formam as curvas típicas:

- ``CURVAS_CATEGORIA``: a categoria (classe, faixa de consumo, participação de
  mercado), que vira :class:`CurveCategory`;
- ``CURVAS_CURVATIPICA``: uma curva por categoria e ``PERIODO``, com o
  ``FAT_POT``;
- ``CURVAS_PONTOS``: os 24 pontos de cada curva (``MEDIA_P``, em pu da média).

Cada curva típica vira uma :class:`Curve` de id ``interplan-cat<CAT_ID>-p<PERIODO>``,
nome ``<CAT_DESC> - <período>``, ``link_id`` igual ao ``CAT_ID`` e ``period``
igual ao tipo de dia. O id fixo é o que torna a importação repetível: importar
de novo atualiza as mesmas curvas em vez de duplicá-las.

**PERIODO.** O banco só guarda o número, sem tabela que o descreva. O
significado foi deduzido dos dados: o ``FAT_NORM`` do 4 é a soma dos do 1, 2 e
3, e os pontos do 4 são a média dos três ponderada por esses fatores — o 4 é a
média do mês; o 1 tem de cinco a dez vezes o fator do 2 e do 3, como 22 dias
úteis contra cerca de 4 sábados ou domingos; e nas categorias comerciais e
industriais o 2 tem mais carga que o 3, o que separa o sábado do domingo. Sem a
coluna ``PERIODO``, a curva é tratada como a média, como faz o ``calcular_dg``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .curvas import (
    CURVE_PERIOD_LABELS,
    HOURLY_CURVE_POINT_COUNT,
    MAX_CURVE_NAME_LENGTH,
    Curve,
)
from .curvas_categorias import (
    CATEGORY_KIND_LOAD,
    CurveCategory,
    category_sort_key,
)
from .mdb_engine import AccessDatabase
from .opendss_export import parse_number


CATEGORY_TABLE = "CURVAS_CATEGORIA"
TYPICAL_CURVE_TABLE = "CURVAS_CURVATIPICA"
POINTS_TABLE = "CURVAS_PONTOS"

# PERIODO do banco → tipo de dia da curva.
PERIOD_BY_NUMBER = {"1": "DU", "2": "SA", "3": "DO", "4": "MED"}
AGGREGATED_PERIOD_NUMBER = "4"

INTERPLAN_CURVE_ID_PREFIX = "interplan-cat"

# Quantas ocorrências o relatório lista antes de resumir o resto.
MAX_REPORTED_ISSUES = 50

_REQUIRED_COLUMNS = {
    CATEGORY_TABLE: ("CAT_ID", "CAT_DESC", "CLASSE", "MIN", "MAX", "N_PONTOS", "PART_MERC"),
    TYPICAL_CURVE_TABLE: ("CUR_ID", "CAT_ID"),
    POINTS_TABLE: ("CUR_ID", "PTONUM", "MEDIA_P"),
}
# Bancos antigos não têm estas colunas; o padrão de cada uma é o do calcular_dg.
_OPTIONAL_COLUMNS = {
    CATEGORY_TABLE: ("TIPO", "PU_MAX", "TIPODEM"),
    TYPICAL_CURVE_TABLE: ("PERIODO", "FAT_POT"),
    POINTS_TABLE: (),
}

CancelCheck = Callable[[], bool]


class CurveDatabaseError(ValueError):
    """O arquivo não tem as tabelas ou colunas de um banco de curvas."""


@dataclass(frozen=True, slots=True)
class CurveDatabaseContents:
    """O que o banco de curvas tinha de aproveitável, e o que ficou de fora."""

    curves: tuple[Curve, ...]
    categories: tuple[CurveCategory, ...]
    issues: tuple[str, ...] = ()
    omitted_issues: int = 0


@dataclass(frozen=True, slots=True)
class CurveDatabaseMerge:
    """Curvas e categorias depois de juntar o banco ao que já estava gravado."""

    curves: tuple[Curve, ...]
    categories: tuple[CurveCategory, ...]
    added: int
    replaced: int
    renamed: tuple[str, ...] = ()


def interplan_curve_id(category_id: str, period_number: str) -> str:
    """Id fixo da curva típica de uma categoria e período."""

    return f"{INTERPLAN_CURVE_ID_PREFIX}{category_id}-p{period_number}"


class _Issues:
    """Acumula ocorrências com teto, como os relatórios dos importadores."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self.omitted = 0

    def add(self, message: str) -> None:
        if len(self.items) < MAX_REPORTED_ISSUES:
            self.items.append(message)
        else:
            self.omitted += 1


def _resolve_columns(
    database: AccessDatabase,
    tables_by_key: dict[str, str],
    table: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Nome real da tabela, colunas lógicas e colunas reais a projetar."""

    actual = tables_by_key.get(table.casefold())
    if actual is None:
        raise CurveDatabaseError(
            f"O arquivo não parece um banco de curvas: falta a tabela {table}."
        )
    by_key = {column.casefold(): column for column in database.columns(actual)}
    missing = [name for name in _REQUIRED_COLUMNS[table] if name.casefold() not in by_key]
    if missing:
        raise CurveDatabaseError(
            f"{table} sem as colunas obrigatórias: {', '.join(missing)}."
        )
    logical = (
        *_REQUIRED_COLUMNS[table],
        *(name for name in _OPTIONAL_COLUMNS[table] if name.casefold() in by_key),
    )
    return actual, logical, tuple(by_key[name.casefold()] for name in logical)


def _rows(
    database: AccessDatabase,
    tables_by_key: dict[str, str],
    table: str,
    cancel_check: CancelCheck | None,
) -> Iterable[dict[str, str]]:
    actual, logical, columns = _resolve_columns(database, tables_by_key, table)
    for count, row in enumerate(database.iter_rows(actual, columns)):
        if cancel_check is not None and count % 4_096 == 0 and cancel_check():
            raise InterruptedError("Leitura do banco de curvas cancelada.")
        yield {name: str(value).strip() for name, value in zip(logical, row)}


def _integer(text: str, default: int | None = None) -> int | None:
    if not text:
        return default
    number = parse_number(text)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _number(text: str, default: float | None = None) -> float | None:
    if not text:
        return default
    return parse_number(text)


def _identifier(text: str) -> str:
    """``CAT_ID``/``CUR_ID`` sem casa decimal, como ``index_for_id`` compara."""

    number = parse_number(text) if text else None
    if number is not None and number.is_integer():
        return str(int(number))
    return text


def _curve_name(description: str, period: str, suffix: str = "") -> str:
    """``<descrição>[ suffix] - <período>``, cortando a descrição se passar do limite."""

    tail = f"{suffix} - {CURVE_PERIOD_LABELS[period]}"
    room = MAX_CURVE_NAME_LENGTH - len(tail)
    if len(description) > room:
        description = description[: room - 1].rstrip() + "…"
    return f"{description}{tail}"


def read_curve_database(
    database: AccessDatabase,
    *,
    cancel_check: CancelCheck | None = None,
) -> CurveDatabaseContents:
    """Lê categorias e curvas típicas.

    Levanta :class:`CurveDatabaseError` quando faltam as tabelas ou colunas
    obrigatórias. Uma categoria ou curva com dado ruim fica de fora com o
    motivo no relatório, sem derrubar as demais.
    """

    issues = _Issues()
    tables_by_key = {name.casefold(): name for name in database.tables()}
    for table in _REQUIRED_COLUMNS:
        _resolve_columns(database, tables_by_key, table)

    raw_categories: dict[str, dict[str, object]] = {}
    for row in _rows(database, tables_by_key, CATEGORY_TABLE, cancel_check):
        category_id = _identifier(row["CAT_ID"])
        if not category_id:
            issues.add(f"{CATEGORY_TABLE}: categoria sem CAT_ID ignorada.")
            continue
        if category_id in raw_categories:
            issues.add(f"{CATEGORY_TABLE}: CAT_ID {category_id} repetido; vale o primeiro.")
            continue
        points = _integer(row["N_PONTOS"])
        if points != HOURLY_CURVE_POINT_COUNT:
            issues.add(
                f"Categoria {category_id}: N_PONTOS {row['N_PONTOS'] or '<vazio>'} "
                f"diferente de {HOURLY_CURVE_POINT_COUNT}; ignorada."
            )
            continue
        fields = {
            "description": " ".join(row["CAT_DESC"].split()),
            "consumer_class": _integer(row["CLASSE"]),
            # Limite vazio é aberto, como o -1 do próprio banco.
            "minimum": _number(row["MIN"], -1.0),
            "maximum": _number(row["MAX"], -1.0),
            "market_share": _number(row["PART_MERC"], 0.0),
            "kind": (row.get("TIPO") or CATEGORY_KIND_LOAD).upper(),
            "pu_max": _integer(row.get("PU_MAX", ""), 0),
            "demand_type": _integer(row.get("TIPODEM", ""), 0),
        }
        invalid = [name for name, value in fields.items() if value is None or value == ""]
        if invalid:
            issues.add(f"Categoria {category_id}: valores inválidos em {CATEGORY_TABLE}; ignorada.")
            continue
        raw_categories[category_id] = fields

    # (CAT_ID, PERIODO) → (CUR_ID, FAT_POT)
    typical: dict[tuple[str, str], tuple[str, str]] = {}
    for row in _rows(database, tables_by_key, TYPICAL_CURVE_TABLE, cancel_check):
        curve_id = _identifier(row["CUR_ID"])
        category_id = _identifier(row["CAT_ID"])
        period_number = _identifier(row.get("PERIODO", "")) or AGGREGATED_PERIOD_NUMBER
        if category_id not in raw_categories:
            issues.add(f"Curva {curve_id}: categoria {category_id or '<vazia>'} inexistente ou ignorada.")
            continue
        if period_number not in PERIOD_BY_NUMBER:
            issues.add(f"Curva {curve_id}: PERIODO {period_number} desconhecido; ignorada.")
            continue
        key = (category_id, period_number)
        if key in typical:
            issues.add(
                f"Categoria {category_id}: mais de uma curva no PERIODO {period_number}; "
                "vale a primeira."
            )
            continue
        typical[key] = (curve_id, row.get("FAT_POT", ""))

    wanted = {curve_id for curve_id, _ in typical.values()}
    points: dict[str, dict[int, float]] = defaultdict(dict)
    unreadable: set[str] = set()
    for row in _rows(database, tables_by_key, POINTS_TABLE, cancel_check):
        curve_id = _identifier(row["CUR_ID"])
        if curve_id not in wanted:
            continue
        number = _integer(row["PTONUM"])
        # MEDIA_P nulo vale zero, como no calcular_dg.
        value = _number(row["MEDIA_P"], 0.0)
        if number is None or value is None:
            unreadable.add(curve_id)
            continue
        points[curve_id][number] = value

    categories: list[CurveCategory] = []
    factors_by_category: dict[str, dict[str, str]] = defaultdict(dict)
    for (category_id, period_number), (_, factor) in typical.items():
        if factor:
            factors_by_category[category_id][period_number] = factor
    for category_id, fields in raw_categories.items():
        factors = factors_by_category.get(category_id, {})
        distinct = {parse_number(value) for value in factors.values()}
        if len(distinct) > 1:
            issues.add(
                f"Categoria {category_id}: FAT_POT diferente entre os períodos; "
                "vale o da média."
            )
        chosen = factors.get(AGGREGATED_PERIOD_NUMBER) or next(iter(factors.values()), "")
        power_factor = parse_number(chosen) if chosen else None
        if power_factor is not None and not 0 < power_factor <= 1:
            issues.add(f"Categoria {category_id}: FAT_POT {chosen} fora de (0, 1]; ignorado.")
            power_factor = None
        try:
            categories.append(
                CurveCategory(category_id=category_id, power_factor=power_factor, **fields)
            )
        except ValueError as exc:
            issues.add(f"Categoria {category_id}: {exc} Ignorada.")

    valid_categories = {category.category_id: category for category in categories}
    curves: list[Curve] = []
    used_names: set[str] = set()
    # Ordem do Interplan: CAT_ID numérico e, dentro dele, o período.
    ordered_keys = sorted(
        (key for key in typical if key[0] in valid_categories),
        key=lambda key: (category_sort_key(valid_categories[key[0]]), key[1]),
    )
    for category_id, period_number in ordered_keys:
        category = valid_categories[category_id]
        curve_id, _ = typical[(category_id, period_number)]
        series = points.get(curve_id, {})
        if curve_id in unreadable:
            issues.add(f"Curva {curve_id} (categoria {category_id}): ponto não numérico; ignorada.")
            continue
        if sorted(series) != list(range(1, HOURLY_CURVE_POINT_COUNT + 1)):
            issues.add(
                f"Curva {curve_id} (categoria {category_id}): {len(series)} ponto(s) "
                f"em vez dos {HOURLY_CURVE_POINT_COUNT} (1 a 24); ignorada."
            )
            continue
        period = PERIOD_BY_NUMBER[period_number]
        name = _curve_name(category.description, period)
        if name.casefold() in used_names:
            name = _curve_name(category.description, period, f" #{category_id}")
            issues.add(f"Categoria {category_id}: descrição repetida; o nome levou o CAT_ID.")
        used_names.add(name.casefold())
        curves.append(
            Curve(
                interplan_curve_id(category_id, period_number),
                name,
                tuple(series[hour] for hour in range(1, HOURLY_CURVE_POINT_COUNT + 1)),
                category_id,
                period,
            )
        )

    return CurveDatabaseContents(
        curves=tuple(curves),
        categories=tuple(sorted(categories, key=category_sort_key)),
        issues=tuple(issues.items),
        omitted_issues=issues.omitted,
    )


def _unique_name(name: str, taken: set[str]) -> str:
    """O nome, ou o nome com ``(2)``, ``(3)``… se já estiver em uso."""

    if name.casefold() not in taken:
        return name
    suffix = 2
    while True:
        tail = f" ({suffix})"
        candidate = name[: MAX_CURVE_NAME_LENGTH - len(tail)].rstrip() + tail
        if candidate.casefold() not in taken:
            return candidate
        suffix += 1


def merge_curve_database(
    existing_curves: Sequence[Curve],
    existing_categories: Sequence[CurveCategory],
    contents: CurveDatabaseContents,
) -> CurveDatabaseMerge:
    """Junta o banco lido ao que já estava gravado.

    Curvas e categorias do banco substituem as de mesmo id, na mesma posição;
    as novas entram no fim. As do usuário ficam intactas — e, se uma delas já
    usa o nome de uma curva do banco, é a do banco que ganha um sufixo, porque
    a do usuário pode estar vinculada a algo pelo nome que ele escolheu.
    """

    imported = {curve.curve_id: curve for curve in contents.curves}
    kept_names = {
        curve.name.casefold() for curve in existing_curves if curve.curve_id not in imported
    }
    renamed: list[str] = []
    taken = set(kept_names)
    resolved: dict[str, Curve] = {}
    for curve in contents.curves:
        name = _unique_name(curve.name, taken)
        if name != curve.name:
            renamed.append(name)
            curve = Curve(curve.curve_id, name, curve.values, curve.link_id, curve.period)
        taken.add(name.casefold())
        resolved[curve.curve_id] = curve

    merged = [resolved.get(curve.curve_id, curve) for curve in existing_curves]
    existing_ids = {curve.curve_id for curve in existing_curves}
    new = [
        resolved[curve.curve_id]
        for curve in contents.curves
        if curve.curve_id not in existing_ids
    ]
    merged.extend(new)

    categories = {category.category_id: category for category in existing_categories}
    categories.update({category.category_id: category for category in contents.categories})
    return CurveDatabaseMerge(
        curves=tuple(merged),
        categories=tuple(sorted(categories.values(), key=category_sort_key)),
        added=len(new),
        replaced=len(existing_ids & resolved.keys()),
        renamed=tuple(renamed),
    )
