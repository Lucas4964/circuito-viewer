"""Modelo lógico e índice espacial, sem qualquer dependência de Qt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float64]
IndexArray = NDArray[np.intp]


@dataclass(frozen=True, slots=True)
class Bounds:
    """Retângulo no sistema de coordenadas do modelo."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def is_empty(self) -> bool:
        return self.right < self.left or self.bottom < self.top

    def expanded(self, x_margin: float, y_margin: float) -> "Bounds":
        return Bounds(
            self.left - x_margin,
            self.top - y_margin,
            self.right + x_margin,
            self.bottom + y_margin,
        )


@dataclass(frozen=True, slots=True)
class UtmCrs:
    """Identificação mínima de um CRS UTM WGS 84."""

    zone: int
    northern: bool

    def __post_init__(self) -> None:
        if not 1 <= self.zone <= 60:
            raise ValueError("A zona UTM deve estar entre 1 e 60.")

    @property
    def hemisphere(self) -> str:
        return "Norte" if self.northern else "Sul"

    @property
    def epsg(self) -> int:
        return (32600 if self.northern else 32700) + self.zone

    @property
    def label(self) -> str:
        suffix = "N" if self.northern else "S"
        return f"UTM {self.zone}{suffix} — EPSG:{self.epsg}"


@dataclass(frozen=True, slots=True)
class BarRecord:
    """Visão imutável de uma barra individual."""

    bar_id: str
    code: str
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class SegmentRecord:
    """Visão imutável de um trecho da rede."""

    segment_id: str
    code: str
    phases: str
    start_bar_id: str
    end_bar_id: str
    arrangement_id: str
    phase_cable_id: str
    neutral_cable_id: str
    length: float | None


@dataclass(frozen=True, slots=True)
class SwitchRecord:
    """Atributos de uma chave associada a um trecho da rede."""

    switch_id: str
    switch_type_id: str
    circuit_id: str
    segment_id: str
    code: str
    state: str
    normal_state: str
    corn: str
    elo: str
    elo_type: str


@dataclass(frozen=True, slots=True)
class FeatureSelection:
    """Referência leve para um elemento selecionado em um dos modelos."""

    kind: Literal["bar", "segment"]
    index: int

    def __post_init__(self) -> None:
        if self.kind not in {"bar", "segment"}:
            raise ValueError(f"Tipo de elemento desconhecido: {self.kind}")
        if self.index < 0:
            raise ValueError("O índice selecionado não pode ser negativo.")


class StaticPointIndex:
    """Índice estático para pontos, otimizado para retângulos e proximidade.

    Os pontos são ordenados uma única vez por X. As consultas delimitam os
    candidatos com ``searchsorted`` e filtram Y de forma vetorizada. A classe
    mantém apenas índices inteiros e não duplica os vetores de coordenadas.
    """

    __slots__ = ("_x", "_y", "_order", "_sorted_x")

    def __init__(self, x: FloatArray, y: FloatArray) -> None:
        x_array = np.ascontiguousarray(x, dtype=np.float64)
        y_array = np.ascontiguousarray(y, dtype=np.float64)
        if x_array.ndim != 1 or y_array.ndim != 1:
            raise ValueError("As coordenadas devem ser vetores unidimensionais.")
        if x_array.shape != y_array.shape:
            raise ValueError("X e Y devem possuir o mesmo tamanho.")
        if not np.isfinite(x_array).all() or not np.isfinite(y_array).all():
            raise ValueError("O índice não aceita coordenadas não finitas.")

        self._x = x_array
        self._y = y_array
        self._order = np.argsort(x_array, kind="stable").astype(np.intp, copy=False)
        self._sorted_x = x_array[self._order]
        self._order.setflags(write=False)
        self._sorted_x.setflags(write=False)

    def __len__(self) -> int:
        return int(self._x.size)

    def query_rect(self, bounds: Bounds | Sequence[float]) -> IndexArray:
        """Retorna índices dos pontos dentro de um retângulo inclusivo."""

        if isinstance(bounds, Bounds):
            left, top, right, bottom = (
                bounds.left,
                bounds.top,
                bounds.right,
                bounds.bottom,
            )
        else:
            if len(bounds) != 4:
                raise ValueError("O retângulo deve conter quatro valores.")
            left, top, right, bottom = (float(value) for value in bounds)

        if right < left:
            left, right = right, left
        if bottom < top:
            top, bottom = bottom, top

        start = int(np.searchsorted(self._sorted_x, left, side="left"))
        stop = int(np.searchsorted(self._sorted_x, right, side="right"))
        candidates = self._order[start:stop]
        if candidates.size == 0:
            return np.empty(0, dtype=np.intp)

        candidate_y = self._y[candidates]
        mask = (candidate_y >= top) & (candidate_y <= bottom)
        return candidates[mask].astype(np.intp, copy=False)

    def nearest(self, x: float, y: float, tolerance: float) -> int | None:
        """Retorna o índice do ponto mais próximo dentro da tolerância."""

        if tolerance < 0 or not np.isfinite(tolerance):
            raise ValueError("A tolerância deve ser finita e não negativa.")
        candidates = self.query_rect(
            Bounds(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
        )
        if candidates.size == 0:
            return None

        dx = self._x[candidates] - x
        dy = self._y[candidates] - y
        distances = dx * dx + dy * dy
        inside = distances <= tolerance * tolerance
        if not inside.any():
            return None

        candidates = candidates[inside]
        distances = distances[inside]
        # Distância é a chave principal; índice original desempata de forma estável.
        position = int(np.lexsort((candidates, distances))[0])
        return int(candidates[position])


class StaticSegmentIndex:
    """Índice estático vetorizado para caixas envolventes de segmentos."""

    __slots__ = (
        "_x1",
        "_y1",
        "_x2",
        "_y2",
        "_min_x",
        "_max_x",
        "_min_y",
        "_max_y",
        "_order",
        "_sorted_min_x",
    )

    def __init__(
        self,
        x1: FloatArray,
        y1: FloatArray,
        x2: FloatArray,
        y2: FloatArray,
    ) -> None:
        arrays = [np.ascontiguousarray(values, dtype=np.float64) for values in (x1, y1, x2, y2)]
        if any(values.ndim != 1 for values in arrays):
            raise ValueError("As coordenadas dos segmentos devem ser vetores.")
        if len({values.size for values in arrays}) != 1:
            raise ValueError("As coordenadas dos segmentos devem possuir o mesmo tamanho.")
        if any(not np.isfinite(values).all() for values in arrays):
            raise ValueError("O índice não aceita coordenadas não finitas.")

        first_x, first_y, second_x, second_y = arrays
        self._x1 = first_x
        self._y1 = first_y
        self._x2 = second_x
        self._y2 = second_y
        self._min_x = np.minimum(first_x, second_x)
        self._max_x = np.maximum(first_x, second_x)
        self._min_y = np.minimum(first_y, second_y)
        self._max_y = np.maximum(first_y, second_y)
        self._order = np.argsort(self._min_x, kind="stable").astype(np.intp, copy=False)
        self._sorted_min_x = self._min_x[self._order]
        for values in (
            self._x1,
            self._y1,
            self._x2,
            self._y2,
            self._min_x,
            self._max_x,
            self._min_y,
            self._max_y,
            self._order,
            self._sorted_min_x,
        ):
            values.setflags(write=False)

    def __len__(self) -> int:
        return int(self._min_x.size)

    def query_rect(self, bounds: Bounds | Sequence[float]) -> IndexArray:
        """Retorna segmentos cujas caixas intersectam o retângulo."""

        if isinstance(bounds, Bounds):
            left, top, right, bottom = (
                bounds.left,
                bounds.top,
                bounds.right,
                bounds.bottom,
            )
        else:
            if len(bounds) != 4:
                raise ValueError("O retângulo deve conter quatro valores.")
            left, top, right, bottom = (float(value) for value in bounds)
        if right < left:
            left, right = right, left
        if bottom < top:
            top, bottom = bottom, top

        stop = int(np.searchsorted(self._sorted_min_x, right, side="right"))
        candidates = self._order[:stop]
        if candidates.size == 0:
            return np.empty(0, dtype=np.intp)
        mask = (
            (self._max_x[candidates] >= left)
            & (self._min_y[candidates] <= bottom)
            & (self._max_y[candidates] >= top)
        )
        return candidates[mask].astype(np.intp, copy=False)

    def nearest(self, x: float, y: float, tolerance: float) -> int | None:
        """Retorna o segmento mais próximo dentro da tolerância informada.

        A caixa de busca reduz os candidatos antes do cálculo vetorizado da
        distância exata ao segmento. Segmentos degenerados são tratados como
        pontos e o índice original desempata distâncias iguais.
        """

        if tolerance < 0 or not np.isfinite(tolerance):
            raise ValueError("A tolerância deve ser finita e não negativa.")
        candidates = self.query_rect(
            Bounds(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
        )
        if candidates.size == 0:
            return None

        x1 = self._x1[candidates]
        y1 = self._y1[candidates]
        vx = self._x2[candidates] - x1
        vy = self._y2[candidates] - y1
        squared_lengths = vx * vx + vy * vy
        projection = np.zeros(candidates.size, dtype=np.float64)
        np.divide(
            (x - x1) * vx + (y - y1) * vy,
            squared_lengths,
            out=projection,
            where=squared_lengths > 0.0,
        )
        np.clip(projection, 0.0, 1.0, out=projection)
        dx = x - (x1 + projection * vx)
        dy = y - (y1 + projection * vy)
        distances = dx * dx + dy * dy
        inside = distances <= tolerance * tolerance
        if not inside.any():
            return None

        candidates = candidates[inside]
        distances = distances[inside]
        position = int(np.lexsort((candidates, distances))[0])
        return int(candidates[position])


class CircuitModel:
    """Armazena barras e metadados geográficos sem acoplamento com a cena."""

    __slots__ = (
        "_bar_ids",
        "_codes",
        "_x",
        "_y",
        "_by_id",
        "_bounds",
        "_spatial_index",
        "crs",
        "source_path",
    )

    def __init__(
        self,
        bar_ids: Iterable[str],
        codes: Iterable[str],
        x: Iterable[float] | FloatArray,
        y: Iterable[float] | FloatArray,
        crs: UtmCrs,
        *,
        source_path: str | None = None,
    ) -> None:
        ids = tuple(str(value) for value in bar_ids)
        code_values = tuple(str(value) for value in codes)
        x_array = np.ascontiguousarray(x, dtype=np.float64)
        y_array = np.ascontiguousarray(y, dtype=np.float64)

        size = len(ids)
        if len(code_values) != size or x_array.size != size or y_array.size != size:
            raise ValueError("Todos os campos da barra devem possuir o mesmo tamanho.")
        if size == 0:
            raise ValueError("O modelo deve conter ao menos uma barra.")
        if x_array.ndim != 1 or y_array.ndim != 1:
            raise ValueError("X e Y devem ser vetores unidimensionais.")
        if not np.isfinite(x_array).all() or not np.isfinite(y_array).all():
            raise ValueError("As coordenadas devem ser finitas.")

        by_id: dict[str, int] = {}
        for index, bar_id in enumerate(ids):
            if not bar_id:
                raise ValueError("BARRA_ID não pode ser vazio.")
            if bar_id in by_id:
                raise ValueError(f"BARRA_ID duplicado no modelo: {bar_id}")
            by_id[bar_id] = index

        x_array.setflags(write=False)
        y_array.setflags(write=False)
        self._bar_ids = ids
        self._codes = code_values
        self._x = x_array
        self._y = y_array
        self._by_id = by_id
        self._bounds = Bounds(
            float(x_array.min()),
            float(y_array.min()),
            float(x_array.max()),
            float(y_array.max()),
        )
        self._spatial_index = StaticPointIndex(x_array, y_array)
        self.crs = crs
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._bar_ids)

    @property
    def bar_ids(self) -> tuple[str, ...]:
        return self._bar_ids

    @property
    def codes(self) -> tuple[str, ...]:
        return self._codes

    @property
    def x(self) -> FloatArray:
        return self._x

    @property
    def y(self) -> FloatArray:
        return self._y

    @property
    def bounds(self) -> Bounds:
        return self._bounds

    @property
    def spatial_index(self) -> StaticPointIndex:
        return self._spatial_index

    def index_for_id(self, bar_id: str) -> int | None:
        return self._by_id.get(bar_id)

    def record(self, index: int) -> BarRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        return BarRecord(
            self._bar_ids[index],
            self._codes[index],
            float(self._x[index]),
            float(self._y[index]),
        )


class LineNetworkModel:
    """Trechos que referenciam as barras por índice, sem duplicar coordenadas."""

    __slots__ = (
        "bars",
        "_segment_ids",
        "_codes",
        "_phases",
        "_start_indices",
        "_end_indices",
        "_arrangement_ids",
        "_phase_cable_ids",
        "_neutral_cable_ids",
        "_lengths",
        "_by_id",
        "_bounds",
        "_spatial_index",
        "source_path",
    )

    def __init__(
        self,
        bars: CircuitModel,
        segment_ids: Iterable[str],
        codes: Iterable[str],
        phases: Iterable[str],
        start_indices: Iterable[int] | IndexArray,
        end_indices: Iterable[int] | IndexArray,
        arrangement_ids: Iterable[str],
        phase_cable_ids: Iterable[str],
        neutral_cable_ids: Iterable[str],
        lengths: Iterable[float] | FloatArray,
        *,
        source_path: str | None = None,
    ) -> None:
        ids = tuple(str(value) for value in segment_ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                codes,
                phases,
                arrangement_ids,
                phase_cable_ids,
                neutral_cable_ids,
            )
        )
        starts = np.ascontiguousarray(start_indices, dtype=np.intp)
        ends = np.ascontiguousarray(end_indices, dtype=np.intp)
        length_values = np.ascontiguousarray(lengths, dtype=np.float64)
        size = len(ids)
        if any(len(values) != size for values in text_columns):
            raise ValueError("Todos os campos do trecho devem possuir o mesmo tamanho.")
        if starts.size != size or ends.size != size or length_values.size != size:
            raise ValueError("Todos os campos do trecho devem possuir o mesmo tamanho.")
        if size == 0:
            raise ValueError("A rede deve conter ao menos um trecho.")
        if starts.ndim != 1 or ends.ndim != 1 or length_values.ndim != 1:
            raise ValueError("Os campos vetoriais dos trechos devem ser unidimensionais.")
        if (starts < 0).any() or (ends < 0).any() or (starts >= len(bars)).any() or (ends >= len(bars)).any():
            raise ValueError("Um trecho referencia uma barra inexistente.")
        if np.isinf(length_values).any() or (length_values[np.isfinite(length_values)] < 0).any():
            raise ValueError("COMPR deve ser vazio ou um número finito não negativo.")

        by_id: dict[str, int] = {}
        for index, segment_id in enumerate(ids):
            if not segment_id:
                raise ValueError("TRECHO_ID não pode ser vazio.")
            if segment_id in by_id:
                raise ValueError(f"TRECHO_ID duplicado no modelo: {segment_id}")
            by_id[segment_id] = index

        starts.setflags(write=False)
        ends.setflags(write=False)
        length_values.setflags(write=False)
        self.bars = bars
        self._segment_ids = ids
        (
            self._codes,
            self._phases,
            self._arrangement_ids,
            self._phase_cable_ids,
            self._neutral_cable_ids,
        ) = text_columns
        self._start_indices = starts
        self._end_indices = ends
        self._lengths = length_values
        self._by_id = by_id

        x1 = bars.x[starts]
        y1 = bars.y[starts]
        x2 = bars.x[ends]
        y2 = bars.y[ends]
        self._bounds = Bounds(
            float(min(x1.min(), x2.min())),
            float(min(y1.min(), y2.min())),
            float(max(x1.max(), x2.max())),
            float(max(y1.max(), y2.max())),
        )
        self._spatial_index = StaticSegmentIndex(x1, y1, x2, y2)
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._segment_ids)

    @property
    def segment_ids(self) -> tuple[str, ...]:
        return self._segment_ids

    @property
    def start_indices(self) -> IndexArray:
        return self._start_indices

    @property
    def end_indices(self) -> IndexArray:
        return self._end_indices

    @property
    def lengths(self) -> FloatArray:
        return self._lengths

    @property
    def bounds(self) -> Bounds:
        return self._bounds

    @property
    def spatial_index(self) -> StaticSegmentIndex:
        return self._spatial_index

    def index_for_id(self, segment_id: str) -> int | None:
        return self._by_id.get(segment_id)

    def record(self, index: int) -> SegmentRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        length = float(self._lengths[index])
        return SegmentRecord(
            segment_id=self._segment_ids[index],
            code=self._codes[index],
            phases=self._phases[index],
            start_bar_id=self.bars.bar_ids[int(self._start_indices[index])],
            end_bar_id=self.bars.bar_ids[int(self._end_indices[index])],
            arrangement_id=self._arrangement_ids[index],
            phase_cable_id=self._phase_cable_ids[index],
            neutral_cable_id=self._neutral_cable_ids[index],
            length=None if np.isnan(length) else length,
        )


class SwitchModel:
    """Metadados de chaves indexados pelos trechos que os representam."""

    __slots__ = (
        "segments",
        "_switch_ids",
        "_switch_type_ids",
        "_circuit_ids",
        "_segment_indices",
        "_codes",
        "_states",
        "_normal_states",
        "_corn_values",
        "_elo_values",
        "_elo_types",
        "_by_id",
        "_record_by_segment",
        "source_path",
    )

    def __init__(
        self,
        segments: LineNetworkModel,
        switch_ids: Iterable[str],
        switch_type_ids: Iterable[str],
        circuit_ids: Iterable[str],
        segment_indices: Iterable[int] | IndexArray,
        codes: Iterable[str],
        states: Iterable[str],
        normal_states: Iterable[str],
        corn_values: Iterable[str],
        elo_values: Iterable[str],
        elo_types: Iterable[str],
        *,
        source_path: str | None = None,
    ) -> None:
        ids = tuple(str(value) for value in switch_ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                switch_type_ids,
                circuit_ids,
                codes,
                states,
                normal_states,
                corn_values,
                elo_values,
                elo_types,
            )
        )
        associated_segments = np.ascontiguousarray(segment_indices, dtype=np.intp)
        size = len(ids)
        if size == 0:
            raise ValueError("O modelo deve conter ao menos uma chave.")
        if any(len(values) != size for values in text_columns):
            raise ValueError("Todos os campos da chave devem possuir o mesmo tamanho.")
        if associated_segments.ndim != 1 or associated_segments.size != size:
            raise ValueError("Os índices de trechos devem formar um vetor compatível.")
        if (associated_segments < 0).any() or (associated_segments >= len(segments)).any():
            raise ValueError("Uma chave referencia um trecho inexistente.")

        by_id: dict[str, int] = {}
        record_by_segment = np.full(len(segments), -1, dtype=np.intp)
        for index, (switch_id, segment_index) in enumerate(
            zip(ids, associated_segments, strict=True)
        ):
            if not switch_id:
                raise ValueError("CHAVE_ID não pode ser vazio.")
            if switch_id in by_id:
                raise ValueError(f"CHAVE_ID duplicado no modelo: {switch_id}")
            segment = int(segment_index)
            if record_by_segment[segment] >= 0:
                raise ValueError(
                    f"O trecho {segments.segment_ids[segment]} possui mais de uma chave."
                )
            by_id[switch_id] = index
            record_by_segment[segment] = index

        associated_segments.setflags(write=False)
        record_by_segment.setflags(write=False)
        self.segments = segments
        self._switch_ids = ids
        (
            self._switch_type_ids,
            self._circuit_ids,
            self._codes,
            self._states,
            self._normal_states,
            self._corn_values,
            self._elo_values,
            self._elo_types,
        ) = text_columns
        self._segment_indices = associated_segments
        self._by_id = by_id
        self._record_by_segment = record_by_segment
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._switch_ids)

    @property
    def switch_ids(self) -> tuple[str, ...]:
        return self._switch_ids

    @property
    def segment_indices(self) -> IndexArray:
        return self._segment_indices

    def index_for_id(self, switch_id: str) -> int | None:
        return self._by_id.get(switch_id)

    def record(self, index: int) -> SwitchRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        segment_index = int(self._segment_indices[index])
        return SwitchRecord(
            switch_id=self._switch_ids[index],
            switch_type_id=self._switch_type_ids[index],
            circuit_id=self._circuit_ids[index],
            segment_id=self.segments.segment_ids[segment_index],
            code=self._codes[index],
            state=self._states[index],
            normal_state=self._normal_states[index],
            corn=self._corn_values[index],
            elo=self._elo_values[index],
            elo_type=self._elo_types[index],
        )

    def record_for_segment(self, segment_index: int) -> SwitchRecord | None:
        if not 0 <= int(segment_index) < len(self.segments):
            raise IndexError(segment_index)
        record_index = int(self._record_by_segment[int(segment_index)])
        return None if record_index < 0 else self.record(record_index)

    def record_for_segment_id(self, segment_id: str) -> SwitchRecord | None:
        segment_index = self.segments.index_for_id(segment_id)
        if segment_index is None:
            return None
        return self.record_for_segment(segment_index)
