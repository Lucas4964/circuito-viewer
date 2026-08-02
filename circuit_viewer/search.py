"""Índice global, independente de Qt, para busca de elementos da rede."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from heapq import nsmallest
from typing import Literal, TypeAlias
import unicodedata

from .model import (
    CircuitCatalogModel,
    CircuitModel,
    FeatureSelection,
    LineNetworkModel,
    LoadModel,
    SwitchModel,
)


SearchKind = Literal["bar", "segment", "switch", "load", "circuit"]
SearchMode = Literal["code", "all_fields"]
MatchQuality = Literal["exact", "prefix", "contains"]
SearchSource: TypeAlias = (
    CircuitModel | LineNetworkModel | SwitchModel | LoadModel | CircuitCatalogModel
)
CancelCheck = Callable[[], bool]

_KIND_ORDER: dict[SearchKind, int] = {
    "bar": 0,
    "segment": 1,
    "switch": 2,
    "load": 3,
    "circuit": 4,
}
_KIND_LABELS: dict[SearchKind, str] = {
    "bar": "Barra",
    "segment": "Trecho",
    "switch": "Chave",
    "load": "Carga",
    "circuit": "Circuito",
}
_MATCH_ORDER: dict[MatchQuality, int] = {
    "exact": 0,
    "prefix": 1,
    "contains": 2,
}
_FIELD_SEPARATOR = "\x1f"


class SearchCancelled(RuntimeError):
    """Indica que uma preparação ou consulta foi cancelada pelo chamador."""


def normalize_code(value: str) -> str:
    """Normaliza texto para comparação amigável e determinística."""

    decomposed = unicodedata.normalize("NFKD", str(value).strip().casefold())
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


@dataclass(frozen=True, slots=True)
class SearchFieldMatch:
    """Campo original que justificou um resultado da busca ampla."""

    column: str
    value: str
    quality: MatchQuality


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Referência imutável a uma entidade pesquisável e ao seu alvo gráfico."""

    kind: SearchKind
    index: int
    code: str
    entity_id: str
    target: FeatureSelection
    related_id: str | None = None
    field_matches: tuple[SearchFieldMatch, ...] = ()

    @property
    def type_label(self) -> str:
        return _KIND_LABELS[self.kind]

    @property
    def identity_text(self) -> str:
        prefix = f"{self.code} — " if self.code else ""
        if self.kind == "switch":
            return (
                f"{prefix}Chave · {self.entity_id} · "
                f"{self.related_id or 'trecho desconhecido'}"
            )
        if self.kind == "circuit":
            return (
                f"{prefix}Circuito · {self.entity_id} · "
                f"origem {self.related_id or 'desconhecida'}"
            )
        if self.kind == "load":
            return (
                f"{prefix}Carga · {self.entity_id} · "
                f"{self.related_id or 'barra desconhecida'}"
            )
        return f"{prefix}{self.type_label} · {self.entity_id}"

    @property
    def display_text(self) -> str:
        if not self.field_matches:
            return self.identity_text
        match = self.field_matches[0]
        return f"{self.identity_text} — {match.column}: {match.value or '—'}"

    @property
    def tooltip_text(self) -> str:
        if not self.field_matches:
            return self.identity_text
        fields = "\n".join(
            f"{match.column}: {match.value or '—'}"
            for match in self.field_matches
        )
        return f"{self.identity_text}\n\nCorrespondências:\n{fields}"


@dataclass(frozen=True, slots=True)
class SearchQueryResult:
    results: tuple[SearchResult, ...]
    truncated: bool = False
    total_matches: int = 0
    revision: int = 0


@dataclass(frozen=True, slots=True)
class _SearchPartition:
    buckets: dict[str, tuple[SearchResult, ...]]
    keys: tuple[str, ...]

    @classmethod
    def build(cls, values: Iterable[SearchResult]) -> "_SearchPartition":
        mutable: dict[str, list[SearchResult]] = {}
        for result in values:
            key = normalize_code(result.code)
            if key:
                mutable.setdefault(key, []).append(result)
        buckets = {
            key: tuple(sorted(bucket, key=_result_identity_key))
            for key, bucket in mutable.items()
        }
        return cls(buckets, tuple(sorted(buckets)))

    def exact(self, key: str) -> tuple[SearchResult, ...]:
        return self.buckets.get(key, ())

    def prefix(
        self,
        prefix: str,
        *,
        exclude: str,
        limit: int,
    ) -> tuple[list[SearchResult], bool]:
        if limit <= 0:
            return [], bool(self.keys)
        found: list[SearchResult] = []
        position = bisect_left(self.keys, prefix)
        while position < len(self.keys):
            key = self.keys[position]
            if not key.startswith(prefix):
                break
            position += 1
            if key == exclude:
                continue
            bucket = self.buckets[key]
            available = limit - len(found)
            if len(bucket) > available:
                found.extend(bucket[:available])
                return found, True
            found.extend(bucket)
            if len(found) >= limit:
                has_more = (
                    position < len(self.keys)
                    and self.keys[position].startswith(prefix)
                )
                return found, has_more
        return found, False


@dataclass(frozen=True, slots=True)
class _FieldDocument:
    result: SearchResult
    columns: tuple[str, ...]
    values: tuple[str, ...]
    normalized_values: tuple[str, ...]
    combined: str


@dataclass(frozen=True, slots=True)
class FieldSearchPartition:
    """Partição pré-normalizada, segura para consulta em outra thread."""

    kind: SearchKind
    source: SearchSource
    documents: tuple[_FieldDocument, ...]


@dataclass(frozen=True, slots=True)
class FieldSearchSnapshot:
    """Snapshot consistente das partições disponíveis em uma revisão."""

    revision: int
    partitions: tuple[FieldSearchPartition, ...]


def _result_identity_key(result: SearchResult) -> tuple[int, str, int]:
    return (_KIND_ORDER[result.kind], result.entity_id.casefold(), result.index)


def _result_prefix_key(result: SearchResult) -> tuple[str, int, str, int]:
    return (
        normalize_code(result.code),
        _KIND_ORDER[result.kind],
        result.entity_id.casefold(),
        result.index,
    )


def _quality(value: str, query: str) -> MatchQuality:
    if value == query:
        return "exact"
    if value.startswith(query):
        return "prefix"
    return "contains"


def _format_float(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _source_rows(
    kind: SearchKind,
    source: SearchSource,
) -> Iterable[tuple[SearchResult, tuple[str, ...], tuple[str, ...]]]:
    if kind == "bar":
        if not isinstance(source, CircuitModel):
            raise TypeError("A partição de barras requer CircuitModel.")
        columns = ("BARRA_ID", "CODIGO", "X", "Y")
        for index in range(len(source)):
            record = source.record(index)
            yield (
                SearchResult(
                    "bar",
                    index,
                    record.code,
                    record.bar_id,
                    FeatureSelection("bar", index),
                ),
                columns,
                (
                    record.bar_id,
                    record.code,
                    _format_float(record.x),
                    _format_float(record.y),
                ),
            )
        return

    if kind == "segment":
        if not isinstance(source, LineNetworkModel):
            raise TypeError("A partição de trechos requer LineNetworkModel.")
        columns = (
            "TRECHO_ID",
            "CODIGO",
            "FASES2",
            "BARRA1_ID",
            "BARRA2_ID",
            "ARRANJO_ID",
            "CABOF_ID",
            "CABON_ID",
            "COMPR",
        )
        for index in range(len(source)):
            record = source.record(index)
            yield (
                SearchResult(
                    "segment",
                    index,
                    record.code,
                    record.segment_id,
                    FeatureSelection("segment", index),
                ),
                columns,
                (
                    record.segment_id,
                    record.code,
                    record.phases,
                    record.start_bar_id,
                    record.end_bar_id,
                    record.arrangement_id,
                    record.phase_cable_id,
                    record.neutral_cable_id,
                    _format_float(record.length),
                ),
            )
        return

    if kind == "switch":
        if not isinstance(source, SwitchModel):
            raise TypeError("A partição de chaves requer SwitchModel.")
        columns = (
            "CHAVE_ID",
            "TIPOCHV_ID",
            "CIRC_ID",
            "TRECHO_ID",
            "CODIGO",
            "ESTADO",
            "ESTADO_NORMAL",
            "CORN",
            "ELO",
            "ELO_TIPO",
        )
        for index in range(len(source)):
            record = source.record(index)
            segment_index = int(source.segment_indices[index])
            yield (
                SearchResult(
                    "switch",
                    index,
                    record.code,
                    record.switch_id,
                    FeatureSelection("segment", segment_index),
                    record.segment_id,
                ),
                columns,
                (
                    record.switch_id,
                    record.switch_type_id,
                    record.circuit_id,
                    record.segment_id,
                    record.code,
                    record.state,
                    record.normal_state,
                    record.corn,
                    record.elo,
                    record.elo_type,
                ),
            )
        return

    if kind == "load":
        if not isinstance(source, LoadModel):
            raise TypeError("A partição de cargas requer LoadModel.")
        columns = (
            "CARGA_ID",
            "BARRA_ID",
            "EXTERN_ID",
            "CODIGO",
            "SNOM",
            "SADM",
            "VLINHASEC",
            "FASES2",
            "TIPO_LIG",
        )
        for index in range(len(source)):
            record = source.record(index)
            yield (
                SearchResult(
                    "load",
                    index,
                    record.code,
                    record.load_id,
                    FeatureSelection("load", index),
                    record.bar_id,
                ),
                columns,
                (
                    record.load_id,
                    record.bar_id,
                    record.external_id,
                    record.code,
                    record.snom,
                    record.sadm,
                    record.secondary_line_voltage,
                    record.phases,
                    record.connection_type,
                ),
            )
        return

    if kind != "circuit" or not isinstance(source, CircuitCatalogModel):
        raise TypeError("A partição de circuitos requer CircuitCatalogModel.")
    columns = ("CIRC_ID", "BARRA_ID", "CODIGO", "VNOM")
    bars = source.segments.bars
    for index, definition in enumerate(source.definitions):
        root_index = bars.index_for_id(definition.root_bar_id)
        if root_index is None:
            continue
        yield (
            SearchResult(
                "circuit",
                index,
                definition.code,
                definition.circuit_id,
                FeatureSelection("bar", root_index),
                definition.root_bar_id,
            ),
            columns,
            (
                definition.circuit_id,
                definition.root_bar_id,
                definition.code,
                definition.nominal_voltage,
            ),
        )


def build_field_search_partition(
    kind: SearchKind,
    source: SearchSource,
    *,
    cancel_check: CancelCheck | None = None,
) -> FieldSearchPartition:
    """Prepara uma categoria sem Qt e sem alterar o índice ativo."""

    documents: list[_FieldDocument] = []
    for position, (result, columns, values) in enumerate(_source_rows(kind, source)):
        if position % 2_048 == 0 and cancel_check is not None and cancel_check():
            raise SearchCancelled("Preparação da busca cancelada.")
        normalized = tuple(normalize_code(value) for value in values)
        documents.append(
            _FieldDocument(
                result,
                columns,
                values,
                normalized,
                _FIELD_SEPARATOR.join(normalized),
            )
        )
    if cancel_check is not None and cancel_check():
        raise SearchCancelled("Preparação da busca cancelada.")
    return FieldSearchPartition(kind, source, tuple(documents))


def query_field_snapshot(
    snapshot: FieldSearchSnapshot,
    text: str,
    *,
    limit: int = 200,
    cancel_check: CancelCheck | None = None,
) -> SearchQueryResult:
    """Consulta todas as colunas de um snapshot usando semântica ``contém``."""

    if limit < 0:
        raise ValueError("O limite de resultados não pode ser negativo.")
    query = normalize_code(text)
    if not query:
        return SearchQueryResult((), total_matches=0, revision=snapshot.revision)

    total_matches = 0

    def candidates() -> Iterable[SearchResult]:
        nonlocal total_matches
        visited = 0
        for partition in snapshot.partitions:
            for document in partition.documents:
                visited += 1
                if visited % 2_048 == 0 and cancel_check is not None and cancel_check():
                    raise SearchCancelled("Consulta cancelada.")
                if query not in document.combined:
                    continue
                matches_with_order: list[tuple[int, int, SearchFieldMatch]] = []
                for field_index, (column, raw_value, normalized_value) in enumerate(
                    zip(
                        document.columns,
                        document.values,
                        document.normalized_values,
                        strict=True,
                    )
                ):
                    if query not in normalized_value:
                        continue
                    quality = _quality(normalized_value, query)
                    matches_with_order.append(
                        (
                            _MATCH_ORDER[quality],
                            0 if column == "CODIGO" else field_index + 1,
                            SearchFieldMatch(column, raw_value, quality),
                        )
                    )
                if not matches_with_order:
                    continue
                matches_with_order.sort(key=lambda value: (value[0], value[1]))
                total_matches += 1
                yield replace(
                    document.result,
                    field_matches=tuple(value[2] for value in matches_with_order),
                )
        if cancel_check is not None and cancel_check():
            raise SearchCancelled("Consulta cancelada.")

    def result_key(result: SearchResult) -> tuple[int, int, int, str, int]:
        best = result.field_matches[0]
        return (
            _MATCH_ORDER[best.quality],
            0 if best.column == "CODIGO" else 1,
            _KIND_ORDER[result.kind],
            result.entity_id.casefold(),
            result.index,
        )

    if limit == 0:
        for _ in candidates():
            pass
        selected: list[SearchResult] = []
    else:
        selected = nsmallest(limit, candidates(), key=result_key)
    return SearchQueryResult(
        tuple(selected),
        truncated=total_matches > limit,
        total_matches=total_matches,
        revision=snapshot.revision,
    )


class GlobalSearchIndex:
    """Mantém partições substituíveis e snapshots consistentes por categoria."""

    def __init__(self) -> None:
        self._partitions: dict[SearchKind, _SearchPartition] = {}
        self._sources: dict[SearchKind, SearchSource] = {}
        self._entity_counts: dict[SearchKind, int] = {}
        self._field_partitions: dict[SearchKind, FieldSearchPartition] = {}
        self._revision = 0

    def __len__(self) -> int:
        """Quantidade de registros com ``CODIGO`` pesquisável."""

        return sum(
            len(bucket)
            for partition in self._partitions.values()
            for bucket in partition.buckets.values()
        )

    @property
    def entity_count(self) -> int:
        """Quantidade total de elementos, inclusive com ``CODIGO`` vazio."""

        return sum(self._entity_counts.values())

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def fields_ready(self) -> bool:
        return all(
            self._field_partitions.get(kind) is not None
            and self._field_partitions[kind].source is source
            for kind, source in self._sources.items()
        )

    def needs_field_partition(
        self,
        kind: SearchKind,
        source: SearchSource | None,
    ) -> bool:
        if source is None or self._sources.get(kind) is not source:
            return False
        partition = self._field_partitions.get(kind)
        return partition is None or partition.source is not source

    def _set_partition(
        self,
        kind: SearchKind,
        source: SearchSource | None,
        values: Iterable[SearchResult],
        *,
        build_fields: bool,
    ) -> bool:
        if source is not None and self._sources.get(kind) is source:
            if build_fields and self.needs_field_partition(kind, source):
                self._field_partitions[kind] = build_field_search_partition(
                    kind,
                    source,
                )
                self._revision += 1
            return False
        self._revision += 1
        self._field_partitions.pop(kind, None)
        if source is None:
            self._sources.pop(kind, None)
            self._partitions.pop(kind, None)
            self._entity_counts.pop(kind, None)
            return True
        result_values = tuple(values)
        self._sources[kind] = source
        self._entity_counts[kind] = len(result_values)
        self._partitions[kind] = _SearchPartition.build(result_values)
        if build_fields:
            self._field_partitions[kind] = build_field_search_partition(kind, source)
            self._revision += 1
        return True

    def install_field_partition(self, partition: FieldSearchPartition) -> bool:
        """Publica uma preparação apenas se a fonte ainda for a atual."""

        if self._sources.get(partition.kind) is not partition.source:
            return False
        current = self._field_partitions.get(partition.kind)
        if current is partition:
            return True
        self._field_partitions[partition.kind] = partition
        self._revision += 1
        return True

    def field_snapshot(self) -> FieldSearchSnapshot | None:
        if not self.fields_ready:
            return None
        return FieldSearchSnapshot(
            self._revision,
            tuple(
                self._field_partitions[kind]
                for kind in _KIND_ORDER
                if kind in self._sources
            ),
        )

    def set_bars(
        self,
        model: CircuitModel | None,
        *,
        build_fields: bool = True,
    ) -> bool:
        values = () if model is None else (
            SearchResult(
                "bar",
                index,
                code,
                model.bar_ids[index],
                FeatureSelection("bar", index),
            )
            for index, code in enumerate(model.codes)
        )
        return self._set_partition(
            "bar", model, values, build_fields=build_fields
        )

    def set_segments(
        self,
        model: LineNetworkModel | None,
        *,
        build_fields: bool = True,
    ) -> bool:
        values = () if model is None else (
            SearchResult(
                "segment",
                index,
                code,
                model.segment_ids[index],
                FeatureSelection("segment", index),
            )
            for index, code in enumerate(model.codes)
        )
        return self._set_partition(
            "segment", model, values, build_fields=build_fields
        )

    def set_switches(
        self,
        model: SwitchModel | None,
        *,
        build_fields: bool = True,
    ) -> bool:
        values = () if model is None else (
            SearchResult(
                "switch",
                index,
                code,
                model.switch_ids[index],
                FeatureSelection("segment", int(model.segment_indices[index])),
                model.segments.segment_ids[int(model.segment_indices[index])],
            )
            for index, code in enumerate(model.codes)
        )
        return self._set_partition(
            "switch", model, values, build_fields=build_fields
        )

    def set_loads(
        self,
        model: LoadModel | None,
        *,
        build_fields: bool = True,
    ) -> bool:
        values = () if model is None else (
            SearchResult(
                "load",
                index,
                code,
                model.load_ids[index],
                FeatureSelection("load", index),
                model.bars.bar_ids[int(model.bar_indices[index])],
            )
            for index, code in enumerate(model.codes)
        )
        return self._set_partition(
            "load", model, values, build_fields=build_fields
        )

    def set_circuits(
        self,
        model: CircuitCatalogModel | None,
        *,
        build_fields: bool = True,
    ) -> bool:
        def circuit_results() -> Iterable[SearchResult]:
            if model is None:
                return
            bars = model.segments.bars
            for index, definition in enumerate(model.definitions):
                root_index = bars.index_for_id(definition.root_bar_id)
                if root_index is None:
                    continue
                yield SearchResult(
                    "circuit",
                    index,
                    definition.code,
                    definition.circuit_id,
                    FeatureSelection("bar", root_index),
                    definition.root_bar_id,
                )

        return self._set_partition(
            "circuit", model, circuit_results(), build_fields=build_fields
        )

    def query(self, text: str, *, limit: int = 100) -> SearchQueryResult:
        if limit < 0:
            raise ValueError("O limite de sugestões não pode ser negativo.")
        key = normalize_code(text)
        if not key:
            return SearchQueryResult((), revision=self._revision)

        empty_partition = _SearchPartition({}, ())
        exact = [
            result
            for kind in _KIND_ORDER
            for result in self._partitions.get(kind, empty_partition).exact(key)
        ]
        exact.sort(key=_result_identity_key)

        partial: list[SearchResult] = []
        partition_truncated = False
        per_partition_limit = limit + 1
        for kind in _KIND_ORDER:
            partition = self._partitions.get(kind)
            if partition is None:
                continue
            candidates, truncated = partition.prefix(
                key,
                exclude=key,
                limit=per_partition_limit,
            )
            partial.extend(candidates)
            partition_truncated = partition_truncated or truncated
        partial.sort(key=_result_prefix_key)
        truncated = partition_truncated or len(partial) > limit
        results = tuple((*exact, *partial[:limit]))
        return SearchQueryResult(
            results,
            truncated,
            total_matches=len(results),
            revision=self._revision,
        )

    def query_any_field(
        self,
        text: str,
        *,
        limit: int = 200,
        cancel_check: CancelCheck | None = None,
    ) -> SearchQueryResult:
        snapshot = self.field_snapshot()
        if snapshot is None:
            raise RuntimeError("O índice de todas as colunas ainda não está pronto.")
        return query_field_snapshot(
            snapshot,
            text,
            limit=limit,
            cancel_check=cancel_check,
        )
