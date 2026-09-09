"""Correspondência entre as tabelas de um banco Access e as entidades do modelo.

O mapeamento é externo — ``config/mdb_tabelas.json`` — pelo mesmo motivo de
``fases2.json`` ser: nome de tabela e de coluna é convenção da concessionária,
não regra de negócio da aplicação. Um banco com ``CABO`` no lugar de ``CABOS``
se resolve editando um JSON, sem tocar em código.

A resolução nunca falha por inteiro. Cada entidade é resolvida
**independentemente**, e a que não encontra tabela ou coluna vira uma
:class:`UnavailableEntity` com o motivo. Isso reflete o grafo de dependências
real do projeto: cabos e barras são raízes independentes, patamares só
desabilitam os arquivos de carga do OpenDSS, e derrubar a importação inteira por
causa de uma tabela ausente seria pior do que importar o que existe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .cable_import import EXPECTED_CABLE_HEADER
from .capacitor_import import EXPECTED_CAPACITOR_HEADER
from .circuit_import import EXPECTED_CIRCUIT_HEADER
from .circuit_level_import import EXPECTED_CIRCUIT_LEVEL_HEADER
from .csv_import import EXPECTED_HEADER as EXPECTED_BAR_HEADER
from .generator_import import CONSUMER_HEADER, GENERATOR_HEADER
from .load_import import EXPECTED_LOAD_HEADER
from .load_pattern_import import EXPECTED_LOAD_PATTERN_HEADER
from .regulator_import import EXPECTED_REGULATOR_HEADER
from .segment_import import EXPECTED_SEGMENT_HEADER
from .switch_import import EXPECTED_SWITCH_HEADER


# Ordem de importação, ditada pelas dependências entre modelos: as barras são a
# raiz, os trechos precisam delas, e os circuitos precisam das chaves para a
# topologia energizada. O catálogo de cabos é independente e vem cedo por ser
# barato. Mexer nesta tupla muda a ordem real da importação.
ENTITY_ORDER: tuple[str, ...] = (
    "barras",
    "cabos",
    "trechos",
    "cargas",
    "capacitores",
    "geradores",
    "patamares",
    "chaves",
    "reguladores",
    "circuitos",
    "patamares_circuitos",
)

# ``geradores_mt_cons`` é uma fonte auxiliar da entidade lógica ``geradores``.
# Ela participa da resolução de tabelas, mas não ganha checkbox nem linha própria
# no relatório consolidado.
GENERATOR_CONSUMER_ENTITY = "geradores_mt_cons"
MAPPING_ORDER: tuple[str, ...] = (
    *ENTITY_ORDER[: ENTITY_ORDER.index("geradores") + 1],
    GENERATOR_CONSUMER_ENTITY,
    *ENTITY_ORDER[ENTITY_ORDER.index("geradores") + 1 :],
)

# Colunas que cada entidade exige, reaproveitadas dos importadores de CSV para
# não existir uma segunda lista capaz de divergir em silêncio.
REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "barras": EXPECTED_BAR_HEADER,
    "cabos": EXPECTED_CABLE_HEADER,
    "trechos": EXPECTED_SEGMENT_HEADER,
    "cargas": EXPECTED_LOAD_HEADER,
    "capacitores": EXPECTED_CAPACITOR_HEADER,
    "geradores": GENERATOR_HEADER,
    GENERATOR_CONSUMER_ENTITY: CONSUMER_HEADER,
    "patamares": EXPECTED_LOAD_PATTERN_HEADER,
    "chaves": EXPECTED_SWITCH_HEADER,
    "reguladores": EXPECTED_REGULATOR_HEADER,
    "circuitos": EXPECTED_CIRCUIT_HEADER,
    "patamares_circuitos": EXPECTED_CIRCUIT_LEVEL_HEADER,
}

# Rótulos para relatórios e para o diálogo.
ENTITY_LABELS: Mapping[str, str] = {
    "barras": "Barras",
    "cabos": "Cabos",
    "trechos": "Trechos",
    "cargas": "Cargas",
    "capacitores": "Capacitores",
    "geradores": "Geradores",
    GENERATOR_CONSUMER_ENTITY: "MT_CONS dos geradores",
    "patamares": "Patamares de carga",
    "chaves": "Chaves",
    "reguladores": "Reguladores",
    "circuitos": "Circuitos",
    "patamares_circuitos": "Patamares dos circuitos",
}

# Entidades sem as quais não há o que desenhar; as demais são complementares.
MANDATORY_ENTITIES: frozenset[str] = frozenset({"barras"})


class MdbMappingError(ValueError):
    """Erro legível ao carregar ou validar ``mdb_tabelas.json``."""


class DatabaseMetadata(Protocol):
    """A resolução usa metadados, vindos de conexão ou retrato de inspeção."""

    def tables(self) -> tuple[str, ...]: ...

    def columns(self, table: str) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class EntityMapping:
    """Candidatos de tabela e apelidos de coluna de uma entidade."""

    entity: str
    tables: tuple[str, ...]
    aliases: Mapping[str, tuple[str, ...]]

    def candidates_for(self, column: str) -> tuple[str, ...]:
        """Nomes aceitos para ``column``, do mais ao menos preferido.

        A lista vazia no JSON significa "mesmo nome do CSV", que é o caso comum
        e evita repetir os catorze nomes de coluna dos cabos só para dizer que
        não mudaram.
        """

        return (column, *self.aliases.get(column, ()))


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    """Entidade encontrada no banco, com a tabela e as colunas escolhidas."""

    entity: str
    table: str
    # Na ordem de ``REQUIRED_COLUMNS[entity]``: ``columns[i]`` é o nome real da
    # coluna no banco e ``header[i]`` é o nome canônico que o importador espera.
    columns: tuple[str, ...]
    header: tuple[str, ...]

    @property
    def label(self) -> str:
        return ENTITY_LABELS.get(self.entity, self.entity)


@dataclass(frozen=True, slots=True)
class UnavailableEntity:
    """Entidade que não pôde ser resolvida, e por quê."""

    entity: str
    reason: str

    @property
    def label(self) -> str:
        return ENTITY_LABELS.get(self.entity, self.entity)


@dataclass(frozen=True, slots=True)
class ResolvedMapping:
    """Resultado da resolução de todas as entidades contra um banco."""

    resolved: tuple[ResolvedEntity, ...]
    unavailable: tuple[UnavailableEntity, ...]

    def get(self, entity: str) -> ResolvedEntity | None:
        for item in self.resolved:
            if item.entity == entity:
                return item
        return None

    def reason_for(self, entity: str) -> str | None:
        for item in self.unavailable:
            if item.entity == entity:
                return item.reason
        return None

    @property
    def has_mandatory(self) -> bool:
        """``True`` quando as entidades sem as quais nada pode ser desenhado existem."""

        found = {item.entity for item in self.resolved}
        return MANDATORY_ENTITIES.issubset(found)


def default_mapping_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "mdb_tabelas.json"


def load_table_mapping(path: str | Path | None = None) -> tuple[EntityMapping, ...]:
    """Lê e valida ``mdb_tabelas.json``.

    Diferente da resolução, aqui um erro **é** fatal: um mapeamento malformado
    não tem interpretação parcial razoável, e a interface desabilita a
    importação por banco citando o caminho e o problema.
    """

    source = default_mapping_path() if path is None else Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MdbMappingError(f"O arquivo {source} não está codificado em UTF-8.") from exc
    except OSError as exc:
        raise MdbMappingError(
            f"Não foi possível ler {source}: {exc.strerror or exc}"
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MdbMappingError(
            f"JSON inválido em {source}, linha {exc.lineno}, coluna {exc.colno}."
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise MdbMappingError(
            f"A raiz de {source.name} deve ser uma lista não vazia."
        )

    entries: list[EntityMapping] = []
    seen: dict[str, int] = {}
    for row_number, raw in enumerate(payload, start=1):
        if not isinstance(raw, dict):
            raise MdbMappingError(
                f"Entrada {row_number}: cada item deve ser um objeto JSON."
            )
        entity = raw.get("entidade")
        if not isinstance(entity, str) or not entity.strip():
            raise MdbMappingError(
                f"Entrada {row_number}: 'entidade' é obrigatória e deve ser texto."
            )
        entity = entity.strip()
        if entity not in REQUIRED_COLUMNS:
            known = ", ".join(MAPPING_ORDER)
            raise MdbMappingError(
                f"Entrada {row_number}: entidade desconhecida '{entity}'. "
                f"Esperadas: {known}."
            )
        if entity in seen:
            raise MdbMappingError(
                f"Entrada {row_number}: entidade '{entity}' duplicada; já aparece "
                f"na entrada {seen[entity]}."
            )

        raw_tables = raw.get("tabelas")
        if not isinstance(raw_tables, list) or not raw_tables:
            raise MdbMappingError(
                f"Entrada {row_number}: 'tabelas' deve ser uma lista não vazia."
            )
        tables: list[str] = []
        for candidate in raw_tables:
            if not isinstance(candidate, str) or not candidate.strip():
                raise MdbMappingError(
                    f"Entrada {row_number}: cada tabela deve ser texto não vazio."
                )
            tables.append(candidate.strip())

        raw_columns = raw.get("colunas", {})
        if not isinstance(raw_columns, dict):
            raise MdbMappingError(
                f"Entrada {row_number}: 'colunas' deve ser um objeto JSON."
            )
        required = REQUIRED_COLUMNS[entity]
        aliases: dict[str, tuple[str, ...]] = {}
        for column, raw_alias in raw_columns.items():
            if column not in required:
                expected = ", ".join(required)
                raise MdbMappingError(
                    f"Entrada {row_number}: a coluna '{column}' não pertence a "
                    f"'{entity}'. Esperadas: {expected}."
                )
            if not isinstance(raw_alias, list):
                raise MdbMappingError(
                    f"Entrada {row_number}: os apelidos de '{column}' devem ser "
                    "uma lista."
                )
            names: list[str] = []
            for alias in raw_alias:
                if not isinstance(alias, str) or not alias.strip():
                    raise MdbMappingError(
                        f"Entrada {row_number}: cada apelido de '{column}' deve "
                        "ser texto não vazio."
                    )
                names.append(alias.strip())
            aliases[column] = tuple(names)

        seen[entity] = row_number
        entries.append(EntityMapping(entity, tuple(tables), aliases))

    return tuple(entries)


def _index_without_case(names: Sequence[str]) -> dict[str, str]:
    """Mapa nome-normalizado → nome real, preservando a primeira ocorrência.

    O Access não diferencia caixa em identificadores, então a resolução também
    não pode: uma base com ``Barra`` precisa casar com ``BARRA`` do JSON.
    """

    index: dict[str, str] = {}
    for name in names:
        index.setdefault(str(name).strip().casefold(), str(name))
    return index


def resolve_mapping(
    database: DatabaseMetadata,
    mapping: Sequence[EntityMapping] | None = None,
    *,
    overrides: Mapping[str, str] | None = None,
) -> ResolvedMapping:
    """Casa cada entidade com uma tabela e as colunas reais do banco.

    ``overrides`` força a tabela de uma entidade — é por onde o diálogo entrega
    a escolha manual do usuário quando a detecção automática não serve. Uma
    tabela forçada que não exista, ou à qual falte coluna obrigatória, é
    relatada como indisponível em vez de cair de volta na detecção: escolher por
    cima da decisão explícita do usuário seria pior do que dizer que não deu.
    """

    entries = load_table_mapping() if mapping is None else tuple(mapping)
    by_entity = {entry.entity: entry for entry in entries}
    forced = dict(overrides or {})

    tables_by_key = _index_without_case(database.tables())
    resolved: list[ResolvedEntity] = []
    unavailable: list[UnavailableEntity] = []

    for entity in MAPPING_ORDER:
        entry = by_entity.get(entity)
        if entry is None:
            unavailable.append(
                UnavailableEntity(
                    entity,
                    f"Sem correspondência configurada em {default_mapping_path().name}.",
                )
            )
            continue

        chosen = forced.get(entity)
        if chosen:
            table = tables_by_key.get(chosen.strip().casefold())
            if table is None:
                unavailable.append(
                    UnavailableEntity(
                        entity,
                        f"A tabela escolhida '{chosen}' não existe no banco.",
                    )
                )
                continue
            candidates = (table,)
        else:
            candidates = tuple(
                found
                for found in (
                    tables_by_key.get(name.strip().casefold())
                    for name in entry.tables
                )
                if found is not None
            )
            if not candidates:
                expected = ", ".join(entry.tables)
                unavailable.append(
                    UnavailableEntity(
                        entity, f"Nenhuma das tabelas {expected} existe no banco."
                    )
                )
                continue

        required = REQUIRED_COLUMNS[entity]
        failures: list[str] = []
        for table in candidates:
            try:
                columns_by_key = _index_without_case(database.columns(table))
            except Exception as exc:  # noqa: BLE001 — tabela ilegível não derruba as outras
                failures.append(f"{table}: não foi possível ler as colunas ({exc})")
                continue
            selected: list[str] = []
            missing: list[str] = []
            for column in required:
                for candidate in entry.candidates_for(column):
                    real = columns_by_key.get(candidate.strip().casefold())
                    if real is not None:
                        selected.append(real)
                        break
                else:
                    missing.append(column)
            if missing:
                failures.append(
                    f"{table}: colunas ausentes: " + ", ".join(missing)
                )
                continue
            resolved.append(
                ResolvedEntity(entity, table, tuple(selected), tuple(required))
            )
            break
        else:
            unavailable.append(UnavailableEntity(entity, "; ".join(failures)))

    return ResolvedMapping(tuple(resolved), tuple(unavailable))
