"""Índice global, independente de Qt, para busca por ``CODIGO``."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterable, Literal
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


def normalize_code(value: str) -> str:
    """Normaliza um código para comparação amigável e determinística."""

    decomposed = unicodedata.normalize("NFKD", str(value).strip().casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Referência imutável a uma entidade pesquisável e ao seu alvo gráfico."""

    kind: SearchKind
    index: int
    code: str
    entity_id: str
    target: FeatureSelection
    related_id: str | None = None

    @property
    def type_label(self) -> str:
        return _KIND_LABELS[self.kind]

    @property
    def display_text(self) -> str:
        if self.kind == "switch":
            return (
                f"{self.code} — Chave · {self.entity_id} · "
                f"{self.related_id or 'trecho desconhecido'}"
            )
        if self.kind == "circuit":
            return (
                f"{self.code} — Circuito · {self.entity_id} · "
                f"origem {self.related_id or 'desconhecida'}"
            )
        if self.kind == "load":
            return (
                f"{self.code} — Carga · {self.entity_id} · "
                f"{self.related_id or 'barra desconhecida'}"
            )
        return f"{self.code} — {self.type_label} · {self.entity_id}"


@dataclass(frozen=True, slots=True)
class SearchQueryResult:
    results: tuple[SearchResult, ...]
    truncated: bool = False


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
                has_more = position < len(self.keys) and self.keys[position].startswith(prefix)
                return found, has_more
        return found, False


def _result_identity_key(result: SearchResult) -> tuple[int, str, int]:
    return (_KIND_ORDER[result.kind], result.entity_id.casefold(), result.index)


def _result_prefix_key(result: SearchResult) -> tuple[str, int, str, int]:
    return (
        normalize_code(result.code),
        _KIND_ORDER[result.kind],
        result.entity_id.casefold(),
        result.index,
    )


class GlobalSearchIndex:
    """Mantém partições substituíveis para evitar reindexações desnecessárias."""

    def __init__(self) -> None:
        self._partitions: dict[SearchKind, _SearchPartition] = {}
        self._sources: dict[SearchKind, object] = {}

    def __len__(self) -> int:
        return sum(
            len(bucket)
            for partition in self._partitions.values()
            for bucket in partition.buckets.values()
        )

    def _set_partition(
        self,
        kind: SearchKind,
        source: object | None,
        values: Iterable[SearchResult],
    ) -> None:
        if source is not None and self._sources.get(kind) is source:
            return
        if source is None:
            self._sources.pop(kind, None)
            self._partitions.pop(kind, None)
            return
        self._sources[kind] = source
        self._partitions[kind] = _SearchPartition.build(values)

    def set_bars(self, model: CircuitModel | None) -> None:
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
        self._set_partition("bar", model, values)

    def set_segments(self, model: LineNetworkModel | None) -> None:
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
        self._set_partition("segment", model, values)

    def set_switches(self, model: SwitchModel | None) -> None:
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
        self._set_partition("switch", model, values)

    def set_loads(self, model: LoadModel | None) -> None:
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
        self._set_partition("load", model, values)

    def set_circuits(self, model: CircuitCatalogModel | None) -> None:
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

        self._set_partition("circuit", model, circuit_results())

    def query(self, text: str, *, limit: int = 100) -> SearchQueryResult:
        if limit < 0:
            raise ValueError("O limite de sugestões não pode ser negativo.")
        key = normalize_code(text)
        if not key:
            return SearchQueryResult(())

        exact = [
            result
            for kind in _KIND_ORDER
            for result in self._partitions.get(kind, _SearchPartition({}, ())).exact(key)
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
        return SearchQueryResult(tuple((*exact, *partial[:limit])), truncated)
