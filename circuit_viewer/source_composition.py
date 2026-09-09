"""Composição de vários bancos numa única cadeia de modelos.

Na importação Access, ``network_registry`` reúne primeiro os arquivos da mesma
rede e reconstrói seu catálogo. As fontes recebidas aqui são redes independentes;
a hipótese de disjunção abaixo vale entre redes, não entre arquivos vinculados.

O aplicativo continua tendo **um** modelo de barras, **um** de trechos, **um**
catálogo de circuitos — a *Regra de identidade* da seção 5 da arquitetura fica
intacta. O que muda é que essa cadeia passa a poder ser **composta**: os
circuitos escolhidos de N fontes são concatenados numa cadeia válida, deslocando
os arrays de índice. Todo o resto do programa (topologia, catálogo, visibilidade,
ramais, blocos, exportação, fluxo, busca, satélite) continua enxergando uma
cadeia só e não precisa saber que houve mais de um banco.

Isso funciona porque o armazenamento já é colunar, imutável e referenciado por
índice: concatenar-e-deslocar é a operação natural sobre ele. E como cada modelo
revalida tudo no construtor, um erro de composição estoura alto, na construção,
em vez de baixo, no desenho.

**As associações de circuito são deslocadas, não retraçadas.**
``CircuitCatalogModel.__init__`` aceita as ``memberships`` prontas e nunca
constrói um ``NetworkTopology`` — só ``build()`` o faz, e é lá que mora o BFS por
circuito. Deslocar índices equivale a refazer o traçado porque as redes de
fontes diferentes são subgrafos disjuntos: concatenar linhas de barra garante que
nenhuma barra é compartilhada, e ``trace`` só caminha por vizinhos de incidência,
que após o deslocamento permanecem dentro do bloco da fonte. A exceção é
``switch_circuit_assignments``, que casa chave e circuito por igualdade de
``CIRC_ID`` e não por conectividade — é por isso que a qualificação de
``CIRC_ID`` é estrutural aqui, e não cosmética.

Este módulo não importa Qt, nem pyodbc, nem o motor do OpenDSS: é núcleo lógico,
exercitado headless.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import chain
from typing import TYPE_CHECKING, Callable, Mapping, Sequence

import numpy as np

if TYPE_CHECKING:
    from .network_registry import NetworkRegistry

from .allocation import TransformerAllocationModel
from .circuit_calculation_levels import CircuitCalculationLevelsModel
from .dss_names import sanitize_dss_name
from .model import (
    Bounds,
    CableModel,
    CapacitorModel,
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitMembership,
    CircuitModel,
    GeneratorModel,
    IndexArray,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
    SwitchModel,
    UtmCrs,
    constructor_columns,
    switch_circuit_assignments,
)


#: Separador da forma qualificada de um identificador em colisão.
#:
#: Sobrevive a ``sanitize_dss_name``: ``_`` não é caractere inválido, e o
#: separador nunca fica na borda, então o ``strip("_")`` final não o remove.
#: ``12345__F2`` chega ao arquivo do OpenDSS exatamente assim.
ID_SEPARATOR = "__"

#: Prefixo da etiqueta de cada fonte, na coluna "Fonte" e nos ids qualificados.
TAG_PREFIX = "F"


class CompositionError(ValueError):
    """A composição não pôde ser feita, e o motivo está na mensagem."""


# ---------------------------------------------------------------------------
# Descrição das entidades
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Entity:
    """O que a mecânica genérica precisa saber sobre um modelo colunar.

    Uma tabela em vez de dez funções escritas à mão: as colunas de cada modelo
    vêm de ``constructor_columns``, derivadas da assinatura do próprio
    construtor, então acrescentar uma coluna a um modelo não exige tocar aqui.
    O que sobra de conhecimento específico são estes campos.
    """

    name: str
    #: Entidade que carrega este modelo, ``None`` para os modelos-raiz.
    parent: str | None
    #: Coluna com os identificadores, a que a qualificação reescreve.
    id_column: str
    #: Nome do identificador no cadastro, para as mensagens do relatório.
    id_label: str
    #: Coluna de índices → entidade que ela indexa.
    index_columns: Mapping[str, str] = field(default_factory=dict)
    #: Coluna de texto que **cita** o id de outra entidade → essa entidade.
    #:
    #: É por aqui que uma qualificação se propaga: a chave cita o CIRC_ID do
    #: circuito e o trecho cita o CABO_ID do cabo, os dois por texto. Deixar
    #: uma dessas citações para trás não estoura em lugar nenhum — só faz a
    #: chave mudar de circuito em silêncio.
    reference_columns: Mapping[str, str] = field(default_factory=dict)


#: Ordem de dependência: um modelo só é composto depois do seu pai.
ENTITIES: tuple[_Entity, ...] = (
    _Entity("bars", None, "bar_ids", "BARRA_ID"),
    _Entity("cables", None, "cable_ids", "CABO_ID"),
    _Entity(
        "segments",
        "bars",
        "segment_ids",
        "TRECHO_ID",
        {"start_indices": "bars", "end_indices": "bars"},
        {"phase_cable_ids": "cables", "neutral_cable_ids": "cables"},
    ),
    _Entity("loads", "bars", "load_ids", "CARGA_ID", {"bar_indices": "bars"}),
    _Entity(
        "capacitors", "bars", "capacitor_ids", "CAPAC_ID", {"bar_indices": "bars"}
    ),
    _Entity(
        "generators",
        "loads",
        "generator_ids",
        "GERADOR_ID",
        {"load_indices": "loads"},
    ),
    _Entity(
        "switches",
        "segments",
        "switch_ids",
        "CHAVE_ID",
        {"segment_indices": "segments"},
        {"circuit_ids": "circuits"},
    ),
    _Entity(
        "regulators",
        "segments",
        "regulator_ids",
        "REGU_ID",
        {"segment_indices": "segments"},
    ),
)

ENTITY_BY_NAME: dict[str, _Entity] = {entity.name: entity for entity in ENTITIES}

MODEL_TYPES: dict[str, type] = {
    "bars": CircuitModel,
    "cables": CableModel,
    "segments": LineNetworkModel,
    "loads": LoadModel,
    "capacitors": CapacitorModel,
    "generators": GeneratorModel,
    "switches": SwitchModel,
    "regulators": RegulatorModel,
}

#: A entidade "circuitos" não é colunar, mas os seus ids também são qualificados.
CIRCUIT_ID_LABEL = "CIRC_ID"


# ---------------------------------------------------------------------------
# Registros
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceDataset:
    """Um banco já lido e restrito aos circuitos escolhidos.

    Os modelos aqui dentro formam uma cadeia própria e coerente, ligada por
    identidade como qualquer outra: é o retrato daquela fonte, e é dele que a
    composição extrai colunas. Guardar o retrato por fonte é o que permite
    remover uma fonte sem reler as outras.
    """

    tag: str
    name: str
    source_path: str
    applied_scale: float = 1.0
    bars: CircuitModel | None = None
    cables: CableModel | None = None
    segments: LineNetworkModel | None = None
    loads: LoadModel | None = None
    capacitors: CapacitorModel | None = None
    generators: GeneratorModel | None = None
    patterns: LoadPatternModel | None = None
    switches: SwitchModel | None = None
    regulators: RegulatorModel | None = None
    catalog: CircuitCatalogModel | None = None
    circuit_levels: CircuitCalculationLevelsModel | None = None
    allocations: TransformerAllocationModel | None = None
    #: Vazio significa "o banco inteiro", não "nenhum circuito".
    chosen_circuit_ids: tuple[str, ...] = ()
    registry: NetworkRegistry | None = field(default=None, repr=False, compare=False)
    provided_entities: frozenset[str] | None = None
    source_tables: tuple[tuple[str, str], ...] = ()
    omitted_fields: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def crs(self) -> UtmCrs:
        """O CRS da fonte é o das suas barras, nunca um campo à parte.

        Duas verdades sobre a mesma coisa acabam divergindo, e aqui a divergência
        seria silenciosa: a composição decidiria reprojetar por um valor e
        reprojetaria pelo outro, produzindo um deslocamento de zero metro.
        """

        if self.bars is None:
            raise CompositionError(
                f"A fonte {self.tag} ({self.name}) não tem barras, e é delas que "
                "vem o CRS."
            )
        return self.bars.crs

    def count(self, entity: str) -> int:
        model = getattr(self, entity, None)
        return 0 if model is None else len(model)

    @property
    def circuit_count(self) -> int:
        return 0 if self.catalog is None else len(self.catalog)


@dataclass(frozen=True, slots=True)
class IdCollision:
    """Um identificador que duas fontes reivindicaram, e o que se fez."""

    entity: str
    native_id: str
    keeper_tag: str
    renamed_tag: str
    qualified_id: str

    @property
    def description(self) -> str:
        return (
            f"{self.entity} {self.native_id} existe em {self.keeper_tag} e em "
            f"{self.renamed_tag}; na fonte {self.renamed_tag} passou a "
            f"{self.qualified_id}."
        )


@dataclass(frozen=True, slots=True)
class CodeCollision:
    """Um CODIGO repetido entre fontes — rótulo, não chave."""

    entity: str
    code: str
    tags: tuple[str, ...]

    @property
    def description(self) -> str:
        fontes = ", ".join(self.tags)
        return (
            f"CODIGO '{self.code}' de {self.entity} aparece nas fontes {fontes}. "
            "O cadastro não foi alterado; a exportação para o OpenDSS desambigua "
            "o nome."
        )


@dataclass(frozen=True, slots=True)
class Reprojection:
    """Uma fonte trazida para o CRS do espaço de trabalho."""

    tag: str
    name: str
    source_crs: UtmCrs
    target_crs: UtmCrs
    max_shift_metres: float

    @property
    def description(self) -> str:
        shift = f"{self.max_shift_metres:,.0f}".replace(",", ".")
        return (
            f"Fonte {self.tag} ({self.name}) estava em {self.source_crs.label} e "
            f"foi reprojetada para {self.target_crs.label}, o CRS da primeira "
            f"fonte. Deslocamento máximo aplicado: {shift} m."
        )


@dataclass(frozen=True, slots=True)
class CompositionReport:
    """O que a composição precisou fazer, e o usuário precisa saber."""

    collisions: tuple[IdCollision, ...] = ()
    code_collisions: tuple[CodeCollision, ...] = ()
    reprojections: tuple[Reprojection, ...] = ()
    merged_cables: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    crs_warning: str | None = None

    @property
    def has_warnings(self) -> bool:
        return bool(
            self.collisions
            or self.code_collisions
            or self.reprojections
            or self.notes
            or self.crs_warning
        )


@dataclass(frozen=True, slots=True)
class ComposedProvenance:
    """Fonte e id nativo de cada linha, alinhados por índice com os modelos.

    É a ponte entre o modelo composto — cujos ids podem ter sido qualificados —
    e tudo que é chaveado pelo id **nativo**: os JSON globais
    (``mapa_cabos.json``, ``curvas.json``, ``fases2.json``) e o estado de sessão
    do usuário, que precisa sobreviver a uma recomposição.

    Com uma fonte só, ``sources`` fica vazio e todo acesso devolve a única
    etiqueta: é o caminho de hoje, sem array nenhum alocado.
    """

    tags: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    sources: Mapping[str, IndexArray] = field(default_factory=dict)
    native_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    network_ids: tuple[str, ...] = ()

    @property
    def single_source(self) -> bool:
        return len(self.tags) <= 1

    def _ordinal(self, entity: str, index: int) -> int:
        values = self.sources.get(entity)
        return 0 if values is None else int(values[int(index)])

    def tag_of(self, entity: str, index: int) -> str:
        if not self.tags:
            return ""
        return self.tags[self._ordinal(entity, index)]

    def name_of(self, entity: str, index: int) -> str:
        if not self.names:
            return ""
        return self.names[self._ordinal(entity, index)]

    def native_id(self, entity: str, index: int) -> str:
        values = self.native_ids.get(entity)
        return "" if values is None else values[int(index)]

    def key(self, entity: str, index: int) -> tuple[str, str]:
        """A chave estável de uma linha: ``(etiqueta da fonte, id nativo)``.

        Nunca o id composto — ele é valor derivado e muda quando uma colisão
        aparece ou desaparece.
        """

        return self.tag_of(entity, index), self.native_id(entity, index)

    def equipment_key(self, entity: str, index: int):
        """Identidade física no cadastro, independente de aliases de exportação."""
        from .network_registry import EquipmentKey

        network_id = (self.network_ids[self._ordinal(entity, index)] if self.network_ids
                      else self.tag_of(entity, index))
        return EquipmentKey(network_id, entity, self.native_id(entity, index))


@dataclass(frozen=True, slots=True)
class ComposedModels:
    """A cadeia composta, pronta para ser instalada pela janela principal."""

    bars: CircuitModel
    cables: CableModel | None = None
    segments: LineNetworkModel | None = None
    loads: LoadModel | None = None
    capacitors: CapacitorModel | None = None
    generators: GeneratorModel | None = None
    patterns: LoadPatternModel | None = None
    switches: SwitchModel | None = None
    regulators: RegulatorModel | None = None
    catalog: CircuitCatalogModel | None = None
    circuit_levels: CircuitCalculationLevelsModel | None = None
    allocations: TransformerAllocationModel | None = None
    provenance: ComposedProvenance = field(default_factory=ComposedProvenance)
    report: CompositionReport = field(default_factory=CompositionReport)


# ---------------------------------------------------------------------------
# Espaço de trabalho
# ---------------------------------------------------------------------------


def _ordinal_of(tag: str) -> int:
    digits = tag[len(TAG_PREFIX) :] if tag.startswith(TAG_PREFIX) else tag
    try:
        return int(digits)
    except ValueError:
        return 0


class SourceWorkspace:
    """Fontes carregadas, na ordem em que entraram.

    Imutável: cada operação devolve uma instância nova. É o que permite montar
    um espaço de trabalho **candidato** dentro do worker e só comprometê-lo se a
    composição der certo — a mesma disciplina transacional dos importadores.
    """

    __slots__ = ("_datasets", "_next_ordinal")

    def __init__(
        self,
        datasets: Sequence[SourceDataset] = (),
        next_ordinal: int = 1,
    ) -> None:
        self._datasets = tuple(datasets)
        tags = [dataset.tag for dataset in self._datasets]
        if len(set(tags)) != len(tags):
            raise ValueError("Duas fontes com a mesma etiqueta.")
        highest = max((_ordinal_of(tag) for tag in tags), default=0)
        self._next_ordinal = max(1, int(next_ordinal), highest + 1)

    def __len__(self) -> int:
        return len(self._datasets)

    def __iter__(self):  # noqa: ANN204 - protocolo de iteração
        return iter(self._datasets)

    @property
    def datasets(self) -> tuple[SourceDataset, ...]:
        return self._datasets

    def next_tag(self) -> str:
        """A etiqueta da próxima fonte.

        Monotônica e **nunca reutilizada** na sessão: remover ``F1`` não
        renumera ``F2``, senão o estado de sessão chaveado por ``(tag, id)``
        mudaria de dono sem ninguém pedir.
        """

        return f"{TAG_PREFIX}{self._next_ordinal}"

    def dataset_for(self, tag: str) -> SourceDataset | None:
        for dataset in self._datasets:
            if dataset.tag == tag:
                return dataset
        return None

    def added(self, dataset: SourceDataset) -> "SourceWorkspace":
        if self.dataset_for(dataset.tag) is not None:
            raise ValueError(f"A etiqueta {dataset.tag} já está em uso.")
        return SourceWorkspace((*self._datasets, dataset), self._next_ordinal)

    def registered(self, dataset: SourceDataset) -> "SourceWorkspace":
        """Instala o retrato candidato de uma rede, mantendo sua etiqueta."""
        if self.dataset_for(dataset.tag) is None:
            return self.added(dataset)
        return SourceWorkspace(
            tuple(dataset if item.tag == dataset.tag else item for item in self),
            self._next_ordinal,
        )

    def equipment(self, key):
        """Consulta por (rede, entidade, ID), independente dos índices do mapa."""
        for dataset in self:
            if dataset.registry is not None and dataset.registry.network_id == key.network_id:
                return dataset.registry.records.get(key)
        return None

    def without(self, tag: str) -> "SourceWorkspace":
        remaining = tuple(item for item in self._datasets if item.tag != tag)
        if len(remaining) == len(self._datasets):
            raise ValueError(f"Fonte desconhecida: {tag}")
        return SourceWorkspace(remaining, self._next_ordinal)

    def replaced_by(self, dataset: SourceDataset) -> "SourceWorkspace":
        """O "Importar banco de dados…" clássico: uma fonte no lugar de todas."""

        return SourceWorkspace((dataset,), self._next_ordinal)

    def cleared(self) -> "SourceWorkspace":
        """Vazio, mas **sem** rebobinar o contador de etiquetas.

        Fechar tudo e abrir de novo não pode devolver ``F1`` a uma fonte
        diferente: o estado de sessão é chaveado por ``(etiqueta, id nativo)``.
        """

        return SourceWorkspace((), self._next_ordinal)

    def renamed(self, tag: str, name: str) -> "SourceWorkspace":
        if self.dataset_for(tag) is None:
            raise ValueError(f"Fonte desconhecida: {tag}")
        datasets = tuple(
            replace(item, name=name) if item.tag == tag else item
            for item in self._datasets
        )
        return SourceWorkspace(datasets, self._next_ordinal)


# ---------------------------------------------------------------------------
# Mecânica colunar comum à restrição e à composição
# ---------------------------------------------------------------------------


def _gather_text(values: Sequence[str], keep: Sequence[int]) -> tuple[str, ...]:
    return tuple(map(values.__getitem__, keep))


def _index_map(size: int, keep: IndexArray) -> IndexArray:
    """Índice antigo → novo, com ``-1`` para o que ficou de fora."""

    mapping = np.full(size, -1, dtype=np.intp)
    mapping[keep] = np.arange(keep.size, dtype=np.intp)
    return mapping


def _restricted_columns(
    model: object,
    entity: _Entity,
    keep: IndexArray,
    index_maps: Mapping[str, IndexArray],
) -> dict[str, object]:
    """As colunas de ``model`` reduzidas a ``keep``, com os índices remapeados."""

    keep_list = keep.tolist()
    restricted: dict[str, object] = {}
    for name, values in constructor_columns(model).items():
        target = entity.index_columns.get(name)
        if target is not None:
            mapped = index_maps[target][np.asarray(values, dtype=np.intp)[keep]]
            if mapped.size and (mapped < 0).any():
                raise CompositionError(
                    f"{entity.name}: a coluna {name} aponta para um "
                    f"{target} que não foi mantido."
                )
            restricted[name] = mapped
        elif isinstance(values, np.ndarray):
            restricted[name] = values[keep]
        elif isinstance(values, tuple):
            restricted[name] = _gather_text(values, keep_list)
        else:
            # Escalares do construtor, como ``CircuitModel.crs``.
            restricted[name] = values
    return restricted


def _merged_columns(
    entity: _Entity,
    parts: Sequence[tuple[int, object]],
    offsets: Mapping[str, Sequence[int]],
    alias_plans: Mapping[str, "_AliasPlan"],
    overrides: Mapping[int, Mapping[str, object]],
) -> dict[str, object]:
    """Concatena as colunas de várias fontes, deslocando os índices.

    ``parts`` são pares ``(ordinal da fonte, modelo)``, e é o ordinal que
    escolhe o deslocamento: uma fonte sem cargas contribui zero para o
    deslocamento de cargas, mas continua contribuindo com as suas barras.

    ``overrides`` substitui colunas já preparadas por quem chamou — as
    coordenadas reprojetadas das barras, as linhas de cabo que sobraram depois
    da deduplicação.
    """

    per_part = [
        {**constructor_columns(model), **overrides.get(ordinal, {})}
        for ordinal, model in parts
    ]
    ordinals = [ordinal for ordinal, _ in parts]
    merged: dict[str, object] = {}
    for name in per_part[0]:
        values = [columns[name] for columns in per_part]
        target = entity.index_columns.get(name)
        if target is not None:
            merged[name] = np.concatenate(
                [
                    np.asarray(item, dtype=np.intp) + offsets[target][ordinal]
                    for ordinal, item in zip(ordinals, values, strict=True)
                ]
            )
        elif name == entity.id_column or name in entity.reference_columns:
            plan = alias_plans[
                entity.reference_columns.get(name, entity.name)
            ]
            merged[name] = tuple(
                chain.from_iterable(
                    _applied_aliases(item, plan.for_source(ordinal))
                    for ordinal, item in zip(ordinals, values, strict=True)
                )
            )
        elif isinstance(values[0], np.ndarray):
            merged[name] = np.concatenate([np.asarray(item) for item in values])
        elif isinstance(values[0], tuple):
            merged[name] = tuple(chain.from_iterable(values))
        else:
            merged[name] = values[0]
    return merged


def _applied_aliases(
    values: Sequence[str],
    aliases: Mapping[str, str],
) -> tuple[str, ...]:
    if not aliases:
        return tuple(values)
    return tuple(aliases.get(value, value) for value in values)


def _model_keyword(name: str, model: object) -> dict[str, object]:
    """O keyword-only de origem que cada construtor aceita."""

    if name == "generators":
        return {"source_paths": getattr(model, "source_paths", None)}
    return {"source_path": getattr(model, "source_path", None)}


# ---------------------------------------------------------------------------
# Restrição aos circuitos escolhidos
# ---------------------------------------------------------------------------


def restrict_to_circuits(
    dataset: SourceDataset,
    circuit_ids: Sequence[str],
) -> SourceDataset:
    """Reduz um banco recém-lido aos circuitos que o usuário escolheu.

    Uma lista vazia devolve o retrato intacto: "vazio" quer dizer *o banco
    inteiro*. O diálogo só usa esse contrato no modo explícito sem seleção.

    A poda **não** pode se guiar só por ``membership.bar_indices``. Uma chave
    aberta faz ``NetworkTopology.trace`` desistir do vizinho antes de
    enfileirá-lo, mas o trecho dessa chave entra em ``switch_segment_indices``
    por casamento de ``CIRC_ID``: manter só as barras traçadas deixaria um
    trecho apontando para barra removida. Por isso as barras mantidas são a
    união das traçadas com os **dois extremos** de cada trecho mantido.

    Chaves entre barras mantidas também sobrevivem, mesmo sem um ``CIRC_ID``
    válido: interligações costumam declarar ``-1`` e não pertencem a nenhuma
    associação individual. Preservá-las não expande a rede para outros
    alimentadores nem altera suas associações elétricas.

    Todo modelo que esvazia vira ``None``, nunca um modelo vazio: os
    construtores recusam zero linhas, e é essa recusa que impede um modelo sem
    sentido de circular pelo programa.
    """

    chosen = tuple(str(value) for value in circuit_ids)
    if not chosen:
        return dataset
    catalog = dataset.catalog
    segments = dataset.segments
    bars = dataset.bars
    if catalog is None or segments is None or bars is None:
        raise CompositionError(
            "Escolher circuitos exige que o banco tenha barras, trechos e o "
            "catálogo de circuitos importados."
        )

    circuit_indices: list[int] = []
    for circuit_id in chosen:
        index = catalog.index_for_id(circuit_id)
        if index is None:
            raise CompositionError(f"Circuito inexistente no banco: {circuit_id}")
        circuit_indices.append(int(index))

    memberships = [catalog.membership(index) for index in circuit_indices]
    segment_parts = [item.segment_indices for item in memberships]
    kept_segments = np.unique(np.concatenate(segment_parts)).astype(
        np.intp, copy=False
    )
    bar_parts = [item.bar_indices for item in memberships]
    if kept_segments.size:
        bar_parts.append(np.asarray(segments.start_indices)[kept_segments])
        bar_parts.append(np.asarray(segments.end_indices)[kept_segments])
    kept_bars = np.unique(np.concatenate(bar_parts)).astype(np.intp, copy=False)
    if kept_bars.size == 0:
        raise CompositionError(
            "Os circuitos escolhidos não possuem nenhuma barra associada."
        )

    bar_map = _index_map(len(bars), kept_bars)
    if dataset.switches is not None:
        switch_segments = np.asarray(dataset.switches.segment_indices)
        # O traçado não inclui chaves sem dono, mas elas continuam ligando
        # fisicamente os blocos importados. Testar as duas pontas evita trazer
        # barras/circuitos externos só por causa de uma interligação.
        internal = (
            (bar_map[np.asarray(segments.start_indices)[switch_segments]] >= 0)
            & (bar_map[np.asarray(segments.end_indices)[switch_segments]] >= 0)
        )
        kept_segments = np.unique(
            np.concatenate((kept_segments, switch_segments[internal]))
        ).astype(np.intp, copy=False)

    index_maps: dict[str, IndexArray] = {
        "bars": bar_map,
        "segments": _index_map(len(segments), kept_segments),
    }
    updates: dict[str, object] = {}

    new_bars = CircuitModel(
        **_restricted_columns(bars, ENTITY_BY_NAME["bars"], kept_bars, index_maps),
        source_path=bars.source_path,
    )
    updates["bars"] = new_bars

    new_segments = None
    if kept_segments.size:
        new_segments = LineNetworkModel(
            new_bars,
            **_restricted_columns(
                segments, ENTITY_BY_NAME["segments"], kept_segments, index_maps
            ),
            source_path=segments.source_path,
        )
    updates["segments"] = new_segments

    # As cargas vêm antes: geradores, patamares e alocações se penduram nelas.
    kept_loads = np.empty(0, dtype=np.intp)
    new_loads = None
    if dataset.loads is not None:
        kept_loads = np.flatnonzero(
            index_maps["bars"][np.asarray(dataset.loads.bar_indices)] >= 0
        ).astype(np.intp, copy=False)
        index_maps["loads"] = _index_map(len(dataset.loads), kept_loads)
        if kept_loads.size:
            new_loads = LoadModel(
                new_bars,
                **_restricted_columns(
                    dataset.loads, ENTITY_BY_NAME["loads"], kept_loads, index_maps
                ),
                source_path=dataset.loads.source_path,
            )
    updates["loads"] = new_loads

    parents: dict[str, object | None] = {
        "bars": new_bars,
        "segments": new_segments,
        "loads": new_loads,
    }
    for name in ("capacitors", "generators", "switches", "regulators"):
        entity = ENTITY_BY_NAME[name]
        model = getattr(dataset, name)
        parent = parents.get(entity.parent)
        if model is None or parent is None:
            updates[name] = None
            continue
        column, target = next(iter(entity.index_columns.items()))
        kept = np.flatnonzero(
            index_maps[target][np.asarray(getattr(model, column))] >= 0
        ).astype(np.intp, copy=False)
        if kept.size == 0:
            updates[name] = None
            continue
        updates[name] = MODEL_TYPES[name](
            parent,
            **_restricted_columns(model, entity, kept, index_maps),
            **_model_keyword(name, model),
        )

    new_patterns = None
    if dataset.patterns is not None and new_loads is not None:
        groups = [dataset.patterns.records_for_load(int(i)) for i in kept_loads]
        if any(groups):
            new_patterns = LoadPatternModel(
                new_loads, groups, source_path=dataset.patterns.source_path
            )
    updates["patterns"] = new_patterns

    new_allocations = None
    if dataset.allocations is not None and new_loads is not None:
        kept_load_ids = frozenset(new_loads.load_ids)
        new_allocations = TransformerAllocationModel(
            loads=new_loads,
            phase_configuration=dataset.allocations.phase_configuration,
            records=tuple(
                dataset.allocations.record(int(i)) for i in kept_loads
            ),
            issues=tuple(
                issue
                for issue in dataset.allocations.issues
                if issue.load_id is None or issue.load_id in kept_load_ids
            ),
            source_path=dataset.allocations.source_path,
        )
    updates["allocations"] = new_allocations

    new_catalog = None
    if new_segments is not None:
        definitions = tuple(catalog.definition(index) for index in circuit_indices)
        remapped = tuple(
            _remapped_membership(item, index_maps["bars"], index_maps["segments"])
            for item in memberships
        )
        _, warnings = switch_circuit_assignments(
            updates["switches"], {item.circuit_id for item in definitions}
        )
        new_catalog = CircuitCatalogModel(
            new_segments,
            updates["switches"],
            definitions,
            remapped,
            topology_warnings=warnings,
            source_path=catalog.source_path,
        )
    updates["catalog"] = new_catalog

    new_levels = None
    if dataset.circuit_levels is not None and new_catalog is not None:
        schedules = [
            dataset.circuit_levels.schedule(index) for index in circuit_indices
        ]
        if any(item is not None for item in schedules):
            new_levels = CircuitCalculationLevelsModel(
                new_catalog,
                schedules,
                source_path=dataset.circuit_levels.source_path,
            )
    updates["circuit_levels"] = new_levels

    return replace(dataset, chosen_circuit_ids=chosen, **updates)


def _remapped_membership(
    membership: CircuitMembership,
    bar_map: IndexArray,
    segment_map: IndexArray,
) -> CircuitMembership:
    def remap(values: IndexArray, mapping: IndexArray, what: str) -> IndexArray:
        mapped = mapping[np.asarray(values, dtype=np.intp)]
        if mapped.size and (mapped < 0).any():
            raise CompositionError(
                f"Uma associação de circuito referencia {what} que não foi mantido."
            )
        return np.ascontiguousarray(mapped, dtype=np.intp)

    return CircuitMembership(
        remap(membership.bar_indices, bar_map, "uma barra"),
        remap(membership.common_segment_indices, segment_map, "um trecho"),
        remap(membership.switch_segment_indices, segment_map, "um trecho"),
        remap(membership.segment_indices, segment_map, "um trecho"),
    )


# ---------------------------------------------------------------------------
# Unicidade de identificadores
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _AliasPlan:
    """Como cada fonte renomeia os ids de uma entidade, e o que colidiu."""

    #: ordinal da fonte → ``{id nativo: id composto}``, só o que mudou.
    by_source: Mapping[int, Mapping[str, str]] = field(default_factory=dict)
    collisions: tuple[IdCollision, ...] = ()

    def for_source(self, ordinal: int) -> Mapping[str, str]:
        return self.by_source.get(ordinal, {})


def _plan_aliases(
    label: str,
    native_by_source: Sequence[tuple[int, str, Sequence[str]]],
    *,
    strict: bool,
) -> _AliasPlan:
    """Qualifica só o que colide, na ordem das fontes.

    Otimista de propósito: a primeira fonte a reivindicar um id o mantém **nu**,
    e com uma fonte só nada colide — é o que garante que uma sessão de um banco
    seja byte a byte a de hoje, e que os JSON globais chaveados por id nativo
    (``mapa_cabos.json``, ``curvas.json``) continuem casando no caso comum.

    ``native_by_source`` são triplas ``(ordinal, etiqueta, ids nativos)``.
    """

    owner: dict[str, str] = {}
    by_source: dict[int, Mapping[str, str]] = {}
    collisions: list[IdCollision] = []
    for ordinal, tag, native in native_by_source:
        own = set(native)
        aliases: dict[str, str] = {}
        assigned: set[str] = set()
        for value in native:
            keeper = owner.get(value)
            if keeper is None:
                continue
            if strict:
                raise CompositionError(
                    f"Colisão de identificadores entre fontes: {label} {value} "
                    f"existe em {keeper} e em {tag}."
                )
            candidate = f"{value}{ID_SEPARATOR}{tag}"
            suffix = 1
            # A forma qualificada pode, ela própria, já existir — um id nativo
            # pode legitimamente terminar em "__F2". O laço só para quando o
            # candidato não colide com nada: nem com o que já foi composto, nem
            # com os ids nativos desta mesma fonte, nem com outra qualificação
            # dela.
            while candidate in owner or candidate in own or candidate in assigned:
                suffix += 1
                candidate = f"{value}{ID_SEPARATOR}{tag}_{suffix}"
            aliases[value] = candidate
            assigned.add(candidate)
            collisions.append(IdCollision(label, value, keeper, tag, candidate))
        if aliases:
            by_source[ordinal] = aliases
        for value in native:
            owner[aliases.get(value, value)] = tag
    return _AliasPlan(by_source, tuple(collisions))


def _code_disambiguation(
    entity_label: str,
    codes_by_source: Sequence[tuple[str, Sequence[str]]],
) -> tuple[tuple[CodeCollision, ...], tuple[str, ...] | None]:
    """CODIGO repetido entre fontes, e o sufixo que desempata cada linha.

    Case-folded porque o OpenDSS não distingue caixa, e saneado porque é o nome
    saneado que vai para o arquivo. O cadastro **não** é alterado: CODIGO é
    rótulo, não chave, e adulterá-lo mentiria para quem lê a tela — o desempate
    vive à parte, em ``name_suffixes``, e só o exportador o usa.

    A primeira fonte a usar um código o mantém limpo, pela mesma razão da
    qualificação de identificadores: um mapa de uma fonte só nunca ganha sufixo,
    e acrescentar uma segunda não renomeia o que já estava exportado.

    Devolve ``None`` no lugar dos sufixos quando nada colide, que é o valor que
    diz aos modelos "não há o que desambiguar".
    """

    tags_by_code: dict[str, list[str]] = {}
    readable: dict[str, str] = {}
    for tag, codes in codes_by_source:
        seen: set[str] = set()
        for code in codes:
            key = sanitize_dss_name(code).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            readable.setdefault(key, code)
            tags_by_code.setdefault(key, []).append(tag)

    contested = {key: tags for key, tags in tags_by_code.items() if len(tags) > 1}
    collisions = tuple(
        CodeCollision(entity_label, readable[key], tuple(tags))
        for key, tags in sorted(contested.items())
    )
    if not contested:
        return collisions, None

    suffixes: list[str] = []
    for tag, codes in codes_by_source:
        for code in codes:
            key = sanitize_dss_name(code).casefold()
            tags = contested.get(key)
            suffixes.append("" if tags is None or tags[0] == tag else tag)
    return collisions, tuple(suffixes)


# ---------------------------------------------------------------------------
# Reprojeção
# ---------------------------------------------------------------------------


def _reprojected_coordinates(
    bars: CircuitModel,
    target: UtmCrs,
    *,
    tag: str,
    name: str,
) -> tuple[np.ndarray, np.ndarray, Reprojection]:
    """Traz as coordenadas de uma fonte para o CRS do espaço de trabalho.

    ``always_xy=True`` devolve ``(easting, northing)``, que é exatamente o par
    ``bars.x``/``bars.y``: a inversão de sinal do Y pertence à **cena**, que
    nega no desenho, não ao modelo, que guarda o northing direto.
    """

    try:
        from pyproj import Transformer
    except Exception as exc:  # noqa: BLE001 - pyproj é obrigatório, mas um
        # ambiente quebrado precisa dizer o que falhou, não sumir com a fonte.
        raise CompositionError(
            f"A fonte {tag} está em {bars.crs.label} e o espaço de trabalho em "
            f"{target.label}, mas a reprojeção não pôde ser feita: {exc}"
        ) from exc

    transformer = Transformer.from_crs(
        f"EPSG:{bars.crs.epsg}", f"EPSG:{target.epsg}", always_xy=True
    )
    source_x = np.asarray(bars.x)
    source_y = np.asarray(bars.y)
    x, y = transformer.transform(source_x, source_y)
    x = np.ascontiguousarray(x, dtype=np.float64)
    y = np.ascontiguousarray(y, dtype=np.float64)
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise CompositionError(
            f"A fonte {tag} ({name}) tem barras fora do domínio válido de "
            f"{target.label}; a reprojeção produziu coordenadas inválidas."
        )
    shift = float(np.max(np.hypot(x - source_x, y - source_y)))
    return x, y, Reprojection(tag, name, bars.crs, target, shift)


# ---------------------------------------------------------------------------
# Cabos
# ---------------------------------------------------------------------------


def _cable_fingerprint(model: CableModel, index: int) -> tuple:
    record = model.record(index)
    return tuple(
        getattr(record, name) for name in record.__slots__ if name != "cable_id"
    )


def _plan_cables(
    datasets: Sequence[SourceDataset],
    *,
    strict: bool,
) -> tuple[dict[int, IndexArray], _AliasPlan, tuple[str, ...]]:
    """Deduplica cabos por conteúdo antes de pensar em qualificar.

    Dois bancos da mesma concessionária compartilham ``CABO_ID`` para o mesmo
    cabo. Qualificar esses ids quebraria ``dados/mapa_cabos.json``, que casa
    ``CABO_ID`` com o nome da biblioteca do OpenDSS, para toda fonte além da
    primeira. Conteúdo idêntico vira uma linha só; conteúdo diferente sob o
    mesmo id é que é colisão de verdade.
    """

    known: dict[tuple[str, tuple], int] = {}
    shared: list[tuple[int, str, int]] = []
    keep_by_source: dict[int, IndexArray] = {}
    native_by_source: list[tuple[int, str, list[str]]] = []
    merged: list[str] = []
    for ordinal, dataset in enumerate(datasets):
        model = dataset.cables
        if model is None:
            continue
        keep: list[int] = []
        native: list[str] = []
        for index, cable_id in enumerate(model.cable_ids):
            fingerprint = _cable_fingerprint(model, index)
            previous = known.get((cable_id, fingerprint))
            if previous is not None:
                merged.append(cable_id)
                shared.append((ordinal, cable_id, previous))
                continue
            keep.append(index)
            native.append(cable_id)
            known[(cable_id, fingerprint)] = ordinal
        keep_by_source[ordinal] = np.asarray(keep, dtype=np.intp)
        native_by_source.append((ordinal, dataset.tag, native))

    plan = _plan_aliases("CABO_ID", native_by_source, strict=strict)
    aliases = {ordinal: dict(values) for ordinal, values in plan.by_source.items()}
    for ordinal, cable_id, owner in shared:
        aliases.setdefault(ordinal, {})[cable_id] = plan.for_source(owner).get(cable_id, cable_id)
    plan = _AliasPlan(aliases, plan.collisions)
    return keep_by_source, plan, tuple(sorted(set(merged)))


def _isolate_missing_references(datasets, plans):
    """Uma referência sem destino nunca pode ser resolvida por outra rede."""
    for entity in ENTITIES:
        for column, target in entity.reference_columns.items():
            plan = plans[target]
            aliases = {ordinal: dict(values) for ordinal, values in plan.by_source.items()}
            occupied = set()
            for ordinal, dataset in enumerate(datasets):
                model = dataset.catalog if target == "circuits" else getattr(dataset, target)
                ids = (() if model is None else
                       tuple(row.circuit_id for row in model.definitions) if target == "circuits" else
                       getattr(model, ENTITY_BY_NAME[target].id_column))
                occupied.update(plan.for_source(ordinal).get(value, value) for value in ids)
            for ordinal, dataset in enumerate(datasets):
                model = getattr(dataset, entity.name)
                target_model = dataset.catalog if target == "circuits" else getattr(dataset, target)
                valid = set(() if target_model is None else
                            (row.circuit_id for row in target_model.definitions) if target == "circuits" else
                            getattr(target_model, ENTITY_BY_NAME[target].id_column))
                if model is None:
                    continue
                for value in constructor_columns(model)[column]:
                    if value in valid or value in aliases.get(ordinal, {}) or value not in occupied:
                        continue
                    candidate = f"{value}__AUSENTE_{dataset.tag}_{target}"
                    while candidate in occupied:
                        candidate += "_"
                    occupied.add(candidate)
                    aliases.setdefault(ordinal, {})[value] = candidate
            plans[target] = _AliasPlan(aliases, plan.collisions)


# ---------------------------------------------------------------------------
# Composição
# ---------------------------------------------------------------------------


def _single_source_provenance(dataset: SourceDataset) -> ComposedProvenance:
    """Proveniência sem nenhum array: com uma fonte, tudo vem dela."""

    native: dict[str, tuple[str, ...]] = {}
    for entity in ENTITIES:
        model = getattr(dataset, entity.name)
        if model is not None:
            native[entity.name] = tuple(getattr(model, entity.id_column))
    if dataset.catalog is not None:
        native["circuits"] = tuple(
            item.circuit_id for item in dataset.catalog.definitions
        )
    return ComposedProvenance((dataset.tag,), (dataset.name,), {}, native,
                             (dataset.registry.network_id if dataset.registry else dataset.tag,))


def compose(
    datasets: Sequence[SourceDataset],
    *,
    strict_ids: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> ComposedModels:
    """Funde as fontes numa cadeia só, na ordem em que foram carregadas.

    Com uma fonte, devolve os **próprios** modelos dela, sem cópia: os mesmos
    objetos atravessam os mesmos setters da janela principal, na mesma ordem, e
    é isso que garante que abrir um banco só continue sendo, byte a byte, o que
    sempre foi.

    ``strict_ids`` recusa em vez de qualificar. Serve a quem prefere um erro
    explícito a um identificador reescrito.
    """

    values = tuple(datasets)
    if not values:
        raise CompositionError("O espaço de trabalho não tem nenhuma fonte.")
    for dataset in values:
        if dataset.bars is None:
            raise CompositionError(
                f"A fonte {dataset.tag} ({dataset.name}) não tem barras."
            )
    if len(values) == 1:
        dataset = values[0]
        return ComposedModels(
            bars=dataset.bars,
            cables=dataset.cables,
            segments=dataset.segments,
            loads=dataset.loads,
            capacitors=dataset.capacitors,
            generators=dataset.generators,
            patterns=dataset.patterns,
            switches=dataset.switches,
            regulators=dataset.regulators,
            catalog=dataset.catalog,
            circuit_levels=dataset.circuit_levels,
            allocations=dataset.allocations,
            provenance=_single_source_provenance(dataset),
            report=CompositionReport(notes=dataset.diagnostics),
        )

    def check() -> None:
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Composição das fontes cancelada.")

    target_crs = values[0].crs
    tags = tuple(dataset.tag for dataset in values)
    names = tuple(dataset.name for dataset in values)

    # --- coordenadas: o CRS do espaço de trabalho é o da primeira fonte ------
    overrides: dict[str, dict[int, dict[str, object]]] = {"bars": {}}
    reprojections: list[Reprojection] = []
    for ordinal, dataset in enumerate(values):
        check()
        columns: dict[str, object] = {"crs": target_crs}
        if dataset.crs.epsg != target_crs.epsg:
            x, y, note = _reprojected_coordinates(
                dataset.bars, target_crs, tag=dataset.tag, name=dataset.name
            )
            columns["x"] = x
            columns["y"] = y
            reprojections.append(note)
        overrides["bars"][ordinal] = columns

    # --- cabos: deduplicar antes de qualificar -------------------------------
    check()
    cable_keep, cable_aliases, merged_cables = _plan_cables(values, strict=strict_ids)
    overrides["cables"] = {
        ordinal: _restricted_columns(
            values[ordinal].cables, ENTITY_BY_NAME["cables"], keep, {}
        )
        for ordinal, keep in cable_keep.items()
        if keep.size
    }

    # --- identificadores -----------------------------------------------------
    # Todos os planos saem antes de qualquer fusão: a chave cita o CIRC_ID e o
    # trecho cita o CABO_ID, então fundir uma entidade exige já saber como a
    # entidade citada foi renomeada.
    alias_plans: dict[str, _AliasPlan] = {"cables": cable_aliases}
    collisions: list[IdCollision] = list(cable_aliases.collisions)
    for entity in ENTITIES:
        if entity.name == "cables":
            continue
        check()
        native_by_source = [
            (ordinal, dataset.tag, tuple(getattr(model, entity.id_column)))
            for ordinal, dataset in enumerate(values)
            if (model := getattr(dataset, entity.name)) is not None
        ]
        plan = _plan_aliases(entity.id_label, native_by_source, strict=strict_ids)
        alias_plans[entity.name] = plan
        collisions.extend(plan.collisions)

    circuit_plan = _plan_aliases(
        CIRCUIT_ID_LABEL,
        [
            (
                ordinal,
                dataset.tag,
                tuple(item.circuit_id for item in dataset.catalog.definitions),
            )
            for ordinal, dataset in enumerate(values)
            if dataset.catalog is not None
        ],
        strict=strict_ids,
    )
    alias_plans["circuits"] = circuit_plan
    collisions.extend(circuit_plan.collisions)
    _isolate_missing_references(values, alias_plans)
    circuit_plan = alias_plans["circuits"]

    # --- deslocamentos, por entidade e por fonte ----------------------------
    counts: dict[str, list[int]] = {}
    offsets: dict[str, list[int]] = {}
    for entity in ENTITIES:
        sizes = [
            (
                len(overrides["cables"].get(ordinal, {}).get("cable_ids", ()))
                if entity.name == "cables"
                else dataset.count(entity.name)
            )
            for ordinal, dataset in enumerate(values)
        ]
        counts[entity.name] = sizes
        running = 0
        cumulative: list[int] = []
        for size in sizes:
            cumulative.append(running)
            running += size
        offsets[entity.name] = cumulative

    # --- desempate de rótulo, no espaço de nomes do OpenDSS ------------------
    # Precisa vir antes da construção: ``name_suffixes`` é argumento dos
    # modelos, não algo que se acrescente depois.
    bar_collisions, bar_suffixes = _code_disambiguation(
        "barra",
        [
            (dataset.tag, dataset.bars.codes)
            for dataset in values
            if dataset.bars is not None
        ],
    )
    load_collisions, load_suffixes = _code_disambiguation(
        "carga",
        [
            (dataset.tag, dataset.loads.codes)
            for dataset in values
            if dataset.loads is not None
        ],
    )
    extra_keywords: dict[str, dict[str, object]] = {
        "bars": {"name_suffixes": bar_suffixes},
        "loads": {"name_suffixes": load_suffixes},
    }

    # --- os modelos colunares, em ordem de dependência -----------------------
    composed: dict[str, object | None] = {}
    for entity in ENTITIES:
        check()
        parts = [
            (ordinal, model)
            for ordinal, dataset in enumerate(values)
            if (model := getattr(dataset, entity.name)) is not None
            and counts[entity.name][ordinal] > 0
        ]
        if not parts:
            composed[entity.name] = None
            continue
        parent = composed.get(entity.parent) if entity.parent else None
        if entity.parent is not None and parent is None:
            composed[entity.name] = None
            continue
        columns = _merged_columns(
            entity, parts, offsets, alias_plans, overrides.get(entity.name, {})
        )
        # A origem de um modelo composto não é a de nenhuma fonte em
        # particular; quem quiser saber de onde veio uma linha usa a
        # proveniência, que responde linha a linha.
        keyword = dict.fromkeys(_model_keyword(entity.name, parts[0][1]))
        keyword.update(extra_keywords.get(entity.name, {}))
        arguments = () if parent is None else (parent,)
        composed[entity.name] = MODEL_TYPES[entity.name](
            *arguments, **columns, **keyword
        )

    check()
    loads = composed.get("loads")
    patterns = _merged_patterns(values, loads, alias_plans["loads"], counts)
    allocations = _merged_allocations(values, loads, alias_plans["loads"], counts)
    catalog, notes = _merged_catalog(
        values, composed, offsets, counts, alias_plans, circuit_plan
    )
    incomplete = [dataset.tag for dataset in values
                  if dataset.loads is not None and dataset.allocations is None]
    if incomplete and any(dataset.allocations is not None for dataset in values):
        notes = (*notes, "Alocações indisponíveis: faltam dados nas fontes " + ", ".join(incomplete) + ".")
    notes = (*notes, *(note for dataset in values for note in dataset.diagnostics))
    circuit_levels = _merged_circuit_levels(values, catalog, counts)

    # --- proveniência --------------------------------------------------------
    sources: dict[str, IndexArray] = {}
    native_ids: dict[str, tuple[str, ...]] = {}
    for entity in ENTITIES:
        sizes = counts[entity.name]
        if not any(sizes):
            continue
        sources[entity.name] = np.repeat(
            np.arange(len(values), dtype=np.intp), sizes
        )
        native_ids[entity.name] = tuple(
            chain.from_iterable(
                (
                    overrides["cables"][ordinal]["cable_ids"]
                    if entity.name == "cables"
                    else tuple(getattr(getattr(dataset, entity.name), entity.id_column))
                )
                for ordinal, dataset in enumerate(values)
                if sizes[ordinal] > 0
            )
        )
    if catalog is not None:
        circuit_sizes = [dataset.circuit_count for dataset in values]
        sources["circuits"] = np.repeat(
            np.arange(len(values), dtype=np.intp), circuit_sizes
        )
        native_ids["circuits"] = tuple(
            chain.from_iterable(
                tuple(item.circuit_id for item in dataset.catalog.definitions)
                for dataset in values
                if dataset.catalog is not None
            )
        )
    provenance = ComposedProvenance(tags, names, sources, native_ids,
                                   tuple(item.registry.network_id if item.registry else item.tag for item in values))

    # --- relatório -----------------------------------------------------------
    report = CompositionReport(
        collisions=tuple(collisions),
        code_collisions=bar_collisions + load_collisions,
        reprojections=tuple(reprojections),
        merged_cables=merged_cables,
        notes=tuple(notes),
        crs_warning=_composed_crs_warning(composed["bars"]),
    )

    return ComposedModels(
        bars=composed["bars"],
        cables=composed.get("cables"),
        segments=composed.get("segments"),
        loads=loads,
        capacitors=composed.get("capacitors"),
        generators=composed.get("generators"),
        patterns=patterns,
        switches=composed.get("switches"),
        regulators=composed.get("regulators"),
        catalog=catalog,
        circuit_levels=circuit_levels,
        allocations=allocations,
        provenance=provenance,
        report=report,
    )


def _composed_crs_warning(bars: CircuitModel) -> str | None:
    from .csv_import import utm_range_warning

    bounds: Bounds = bars.bounds
    return utm_range_warning(
        (bounds.left, bounds.right), (bounds.bottom, bounds.top)
    )


def _merged_patterns(
    values: Sequence[SourceDataset],
    loads: LoadModel | None,
    load_aliases: _AliasPlan,
    counts: Mapping[str, Sequence[int]],
) -> LoadPatternModel | None:
    """Patamares na ordem das cargas compostas; ``None`` por carga é legítimo."""

    if loads is None or all(dataset.patterns is None for dataset in values):
        return None
    groups: list[tuple | None] = []
    for ordinal, dataset in enumerate(values):
        if counts["loads"][ordinal] == 0:
            continue
        aliases = load_aliases.for_source(ordinal)
        patterns = dataset.patterns
        for index in range(len(dataset.loads)):
            if patterns is None:
                groups.append(None)
                continue
            records = patterns.records_for_load(index)
            if not records:
                groups.append(None)
                continue
            if aliases:
                records = tuple(
                    replace(item, load_id=aliases.get(item.load_id, item.load_id))
                    for item in records
                )
            groups.append(records)
    if not any(groups):
        return None
    return LoadPatternModel(loads, groups, source_path=None)


def _merged_allocations(
    values: Sequence[SourceDataset],
    loads: LoadModel | None,
    load_aliases: _AliasPlan,
    counts: Mapping[str, Sequence[int]],
) -> TransformerAllocationModel | None:
    """Só compõe quando **toda** fonte com cargas trouxe alocação.

    Completar a fonte que não trouxe com zeros diria que a energia dela é zero,
    o que é diferente de "não veio". O relatório avisa em vez de mentir.
    """

    contributors = [
        dataset for ordinal, dataset in enumerate(values) if counts["loads"][ordinal]
    ]
    if loads is None or not contributors:
        return None
    if any(dataset.allocations is None for dataset in contributors):
        return None
    records = []
    for ordinal, dataset in enumerate(values):
        if counts["loads"][ordinal] == 0:
            continue
        aliases = load_aliases.for_source(ordinal)
        for index in range(len(dataset.loads)):
            record = dataset.allocations.record(index)
            if aliases and record.load_id in aliases:
                record = replace(record, load_id=aliases[record.load_id])
            records.append(record)
    issues = tuple(
        chain.from_iterable(
            _aliased_issues(
                dataset.allocations.issues,
                load_aliases.for_source(ordinal),
            )
            for ordinal, dataset in enumerate(values)
            if counts["loads"][ordinal]
        )
    )
    return TransformerAllocationModel(
        loads=loads,
        phase_configuration=contributors[0].allocations.phase_configuration,
        records=tuple(records),
        issues=issues,
        source_path=None,
    )


def _aliased_issues(issues, aliases: Mapping[str, str]):  # noqa: ANN001, ANN201
    if not aliases:
        return issues
    return tuple(
        replace(issue, load_id=aliases[issue.load_id])
        if issue.load_id in aliases
        else issue
        for issue in issues
    )


def _merged_catalog(
    values: Sequence[SourceDataset],
    composed: Mapping[str, object | None],
    offsets: Mapping[str, Sequence[int]],
    counts: Mapping[str, Sequence[int]],
    alias_plans: Mapping[str, _AliasPlan],
    circuit_plan: _AliasPlan,
) -> tuple[CircuitCatalogModel | None, tuple[str, ...]]:
    """Catálogo composto: memberships deslocadas, nunca retraçadas."""

    segments = composed.get("segments")
    if segments is None:
        return None, ()
    definitions: list[CircuitDefinition] = []
    memberships: list[CircuitMembership] = []
    notes: list[str] = []
    bar_aliases = alias_plans["bars"]
    for ordinal, dataset in enumerate(values):
        catalog = dataset.catalog
        if catalog is None:
            continue
        if counts["segments"][ordinal] == 0:
            notes.append(
                f"A fonte {dataset.tag} tem circuitos mas nenhum trecho; o "
                "catálogo dela ficou de fora."
            )
            continue
        circuit_map = circuit_plan.for_source(ordinal)
        bar_map = bar_aliases.for_source(ordinal)
        bar_offset = offsets["bars"][ordinal]
        segment_offset = offsets["segments"][ordinal]
        for index, definition in enumerate(catalog.definitions):
            circuit_id = circuit_map.get(definition.circuit_id, definition.circuit_id)
            root_bar_id = bar_map.get(definition.root_bar_id, definition.root_bar_id)
            if (
                circuit_id != definition.circuit_id
                or root_bar_id != definition.root_bar_id
            ):
                definition = replace(
                    definition, circuit_id=circuit_id, root_bar_id=root_bar_id
                )
            definitions.append(definition)
            memberships.append(
                _shifted_membership(
                    catalog.membership(index), bar_offset, segment_offset
                )
            )
    if not definitions:
        return None, tuple(notes)

    switches = composed.get("switches")
    _, warnings = switch_circuit_assignments(
        switches, {item.circuit_id for item in definitions}
    )
    bar_count = len(segments.bars)
    for membership in memberships:
        # ``CircuitCatalogModel.__init__`` só confere a faixa dos trechos; um
        # deslocamento errado nas barras passaria e corromperia os contadores
        # de visibilidade por índice negativo.
        if membership.bar_indices.size and (
            (membership.bar_indices < 0).any()
            or (membership.bar_indices >= bar_count).any()
        ):
            raise CompositionError(
                "Uma associação de circuito referencia barra inexistente após a "
                "composição."
            )
    return (
        CircuitCatalogModel(
            segments,
            switches,
            definitions,
            memberships,
            topology_warnings=warnings,
            source_path=None,
        ),
        tuple(notes),
    )


def _shifted_membership(
    membership: CircuitMembership,
    bar_offset: int,
    segment_offset: int,
) -> CircuitMembership:
    """Desloca as quatro arrays.

    As três de trecho são deslocadas **independentemente**, e não recalculadas
    como concatenação das outras duas: o deslocamento uniforme preserva a
    identidade entre elas, e re-derivar mudaria em silêncio a ordem de que o
    ``CircuitVisibilityController`` depende.
    """

    def shift(values: IndexArray, offset: int) -> IndexArray:
        return np.ascontiguousarray(
            np.asarray(values, dtype=np.intp) + offset, dtype=np.intp
        )

    return CircuitMembership(
        shift(membership.bar_indices, bar_offset),
        shift(membership.common_segment_indices, segment_offset),
        shift(membership.switch_segment_indices, segment_offset),
        shift(membership.segment_indices, segment_offset),
    )


def _merged_circuit_levels(
    values: Sequence[SourceDataset],
    catalog: CircuitCatalogModel | None,
    counts: Mapping[str, Sequence[int]],
) -> CircuitCalculationLevelsModel | None:
    """Agendas na ordem dos circuitos compostos; ``None`` por circuito é legítimo."""

    if catalog is None:
        return None
    schedules: list[object | None] = []
    for ordinal, dataset in enumerate(values):
        if dataset.catalog is None or counts["segments"][ordinal] == 0:
            continue
        levels = dataset.circuit_levels
        for index in range(len(dataset.catalog)):
            schedules.append(None if levels is None else levels.schedule(index))
    if len(schedules) != len(catalog) or not any(
        item is not None for item in schedules
    ):
        return None
    return CircuitCalculationLevelsModel(catalog, schedules, source_path=None)
