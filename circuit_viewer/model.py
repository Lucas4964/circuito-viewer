"""Modelo lógico e índice espacial, sem qualquer dependência de Qt."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from collections.abc import Callable
from typing import Iterable, Literal, Sequence

import numpy as np
from numpy.typing import NDArray

from .circuit_colors import generate_circuit_palette, normalize_hex_color


FloatArray = NDArray[np.float64]
IndexArray = NDArray[np.intp]
BoolArray = NDArray[np.bool_]


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
class CircuitDefinition:
    """Metadados de um circuito e sua barra de partida."""

    circuit_id: str
    root_bar_id: str
    code: str
    nominal_voltage: str


@dataclass(frozen=True, slots=True)
class CircuitMembership:
    """Índices associados a um circuito, separados pela origem da associação."""

    bar_indices: IndexArray
    common_segment_indices: IndexArray
    switch_segment_indices: IndexArray
    segment_indices: IndexArray

    def __post_init__(self) -> None:
        arrays = (
            self.bar_indices,
            self.common_segment_indices,
            self.switch_segment_indices,
            self.segment_indices,
        )
        for values in arrays:
            if values.dtype != np.dtype(np.intp) or values.ndim != 1:
                raise ValueError("As associações devem ser vetores de índices.")


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

    def nearest(
        self,
        x: float,
        y: float,
        tolerance: float,
        eligible_mask: BoolArray | Sequence[bool] | None = None,
    ) -> int | None:
        """Retorna o índice do ponto mais próximo dentro da tolerância."""

        if tolerance < 0 or not np.isfinite(tolerance):
            raise ValueError("A tolerância deve ser finita e não negativa.")
        candidates = self.query_rect(
            Bounds(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
        )
        if eligible_mask is not None:
            mask = np.asarray(eligible_mask, dtype=np.bool_)
            if mask.ndim != 1 or mask.size != len(self):
                raise ValueError("A máscara de pontos deve corresponder ao índice.")
            candidates = candidates[mask[candidates]]
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

    def nearest(
        self,
        x: float,
        y: float,
        tolerance: float,
        eligible_mask: BoolArray | Sequence[bool] | None = None,
    ) -> int | None:
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
        if eligible_mask is not None:
            mask = np.asarray(eligible_mask, dtype=np.bool_)
            if mask.ndim != 1 or mask.size != len(self):
                raise ValueError("A máscara de trechos deve corresponder ao índice.")
            candidates = candidates[mask[candidates]]
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

    @property
    def circuit_ids(self) -> tuple[str, ...]:
        return self._circuit_ids

    @property
    def states(self) -> tuple[str, ...]:
        return self._states

    @property
    def record_indices_by_segment(self) -> IndexArray:
        """Índice do registro de chave por trecho, ou -1 para trecho comum."""

        return self._record_by_segment

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


def _readonly_indices(values: Iterable[int] | IndexArray) -> IndexArray:
    result = np.ascontiguousarray(values, dtype=np.intp)
    if result.ndim != 1:
        raise ValueError("Os índices devem formar um vetor unidimensional.")
    result.setflags(write=False)
    return result


class NetworkTopology:
    """Adjacência CSR e busca elétrica sobre uma rede imutável de trechos."""

    __slots__ = (
        "segments",
        "switches",
        "_offsets",
        "_incidence_segments",
        "_incidence_neighbors",
        "_bar_marks",
        "_segment_marks",
        "_generation",
    )

    def __init__(
        self,
        segments: LineNetworkModel,
        switches: SwitchModel | None = None,
    ) -> None:
        if switches is not None and switches.segments is not segments:
            raise ValueError("As chaves devem pertencer à rede usada na topologia.")
        self.segments = segments
        self.switches = switches

        bar_count = len(segments.bars)
        segment_count = len(segments)
        starts = segments.start_indices
        ends = segments.end_indices
        degrees = np.bincount(
            np.concatenate((starts, ends)), minlength=bar_count
        ).astype(np.intp, copy=False)
        offsets = np.empty(bar_count + 1, dtype=np.intp)
        offsets[0] = 0
        np.cumsum(degrees, out=offsets[1:])
        incidence_segments = np.empty(segment_count * 2, dtype=np.intp)
        incidence_neighbors = np.empty(segment_count * 2, dtype=np.intp)
        cursor = offsets[:-1].copy()
        for segment_index, (start_value, end_value) in enumerate(
            zip(starts, ends, strict=True)
        ):
            start = int(start_value)
            end = int(end_value)
            start_position = int(cursor[start])
            incidence_segments[start_position] = segment_index
            incidence_neighbors[start_position] = end
            cursor[start] += 1
            end_position = int(cursor[end])
            incidence_segments[end_position] = segment_index
            incidence_neighbors[end_position] = start
            cursor[end] += 1

        for values in (offsets, incidence_segments, incidence_neighbors):
            values.setflags(write=False)
        self._offsets = offsets
        self._incidence_segments = incidence_segments
        self._incidence_neighbors = incidence_neighbors
        self._bar_marks = np.zeros(bar_count, dtype=np.int64)
        self._segment_marks = np.zeros(segment_count, dtype=np.int64)
        self._generation = 0

    def _next_generation(self) -> int:
        if self._generation >= np.iinfo(np.int64).max - 1:
            self._bar_marks.fill(0)
            self._segment_marks.fill(0)
            self._generation = 0
        self._generation += 1
        return self._generation

    def trace(
        self,
        circuit_id: str,
        root_bar_index: int,
        direct_switch_indices: Iterable[int] | IndexArray = (),
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> CircuitMembership:
        """Executa BFS, descobrindo barras e somente trechos que não são chaves."""

        if not 0 <= int(root_bar_index) < len(self.segments.bars):
            raise IndexError(root_bar_index)
        generation = self._next_generation()
        root = int(root_bar_index)
        queue: deque[int] = deque((root,))
        self._bar_marks[root] = generation
        bars: list[int] = [root]
        common_segments: list[int] = []
        inspected = 0

        switch_by_segment = (
            None
            if self.switches is None
            else self.switches.record_indices_by_segment
        )
        while queue:
            bar_index = queue.popleft()
            start = int(self._offsets[bar_index])
            stop = int(self._offsets[bar_index + 1])
            for position in range(start, stop):
                inspected += 1
                if (
                    cancel_check is not None
                    and inspected % 4_096 == 0
                    and cancel_check()
                ):
                    raise InterruptedError("Construção da topologia cancelada.")
                segment_index = int(self._incidence_segments[position])
                neighbor = int(self._incidence_neighbors[position])
                switch_record_index = (
                    -1
                    if switch_by_segment is None
                    else int(switch_by_segment[segment_index])
                )
                if switch_record_index >= 0:
                    assert self.switches is not None
                    traversable = (
                        self.switches.states[switch_record_index].strip() == "1"
                        and self.switches.circuit_ids[switch_record_index].strip()
                        == circuit_id
                    )
                    if not traversable:
                        continue
                elif self._segment_marks[segment_index] != generation:
                    self._segment_marks[segment_index] = generation
                    common_segments.append(segment_index)

                if self._bar_marks[neighbor] != generation:
                    self._bar_marks[neighbor] = generation
                    bars.append(neighbor)
                    queue.append(neighbor)

        if cancel_check is not None and cancel_check():
            raise InterruptedError("Construção da topologia cancelada.")
        bar_array = _readonly_indices(bars)
        common_array = _readonly_indices(common_segments)
        switch_array = _readonly_indices(direct_switch_indices)
        if common_array.size and switch_array.size:
            all_segments = _readonly_indices(
                np.concatenate((common_array, switch_array))
            )
        elif common_array.size:
            all_segments = common_array
        else:
            all_segments = switch_array
        return CircuitMembership(
            bar_indices=bar_array,
            common_segment_indices=common_array,
            switch_segment_indices=switch_array,
            segment_indices=all_segments,
        )


class CircuitCatalogModel:
    """Circuitos, associações calculadas e identidade da topologia utilizada."""

    __slots__ = (
        "segments",
        "switches",
        "_definitions",
        "_memberships",
        "_by_id",
        "_segment_circuit_offsets",
        "_segment_circuit_indices",
        "_segment_owner_counts",
        "_overlapping_segment_indices",
        "topology_warnings",
        "source_path",
    )

    def __init__(
        self,
        segments: LineNetworkModel,
        switches: SwitchModel | None,
        definitions: Iterable[CircuitDefinition],
        memberships: Iterable[CircuitMembership],
        *,
        topology_warnings: Iterable[str] = (),
        source_path: str | None = None,
    ) -> None:
        if switches is not None and switches.segments is not segments:
            raise ValueError("As chaves devem pertencer aos trechos do catálogo.")
        definition_values = tuple(definitions)
        membership_values = tuple(memberships)
        if not definition_values:
            raise ValueError("O catálogo deve conter ao menos um circuito.")
        if len(definition_values) != len(membership_values):
            raise ValueError("Cada circuito deve possuir uma associação.")
        by_id: dict[str, int] = {}
        for index, definition in enumerate(definition_values):
            if not definition.circuit_id:
                raise ValueError("CIRC_ID não pode ser vazio.")
            if definition.circuit_id in by_id:
                raise ValueError(f"CIRC_ID duplicado: {definition.circuit_id}")
            if segments.bars.index_for_id(definition.root_bar_id) is None:
                raise ValueError(
                    f"Barra inicial inexistente: {definition.root_bar_id}"
                )
            by_id[definition.circuit_id] = index
        self.segments = segments
        self.switches = switches
        self._definitions = definition_values
        self._memberships = membership_values
        self._by_id = by_id
        owner_counts = np.zeros(len(segments), dtype=np.intp)
        for membership in membership_values:
            if (
                (membership.segment_indices < 0).any()
                or (membership.segment_indices >= len(segments)).any()
            ):
                raise ValueError("Uma associação referencia trecho inexistente.")
            owner_counts[membership.segment_indices] += 1
        owner_offsets = np.empty(len(segments) + 1, dtype=np.intp)
        owner_offsets[0] = 0
        np.cumsum(owner_counts, out=owner_offsets[1:])
        owner_indices = np.empty(int(owner_offsets[-1]), dtype=np.intp)
        cursor = owner_offsets[:-1].copy()
        for circuit_index, membership in enumerate(membership_values):
            segment_values = membership.segment_indices
            positions = cursor[segment_values]
            owner_indices[positions] = circuit_index
            cursor[segment_values] += 1
        overlapping = np.flatnonzero(owner_counts > 1).astype(np.intp, copy=False)
        for values in (owner_counts, owner_offsets, owner_indices, overlapping):
            values.setflags(write=False)
        self._segment_owner_counts = owner_counts
        self._segment_circuit_offsets = owner_offsets
        self._segment_circuit_indices = owner_indices
        self._overlapping_segment_indices = overlapping
        self.topology_warnings = tuple(topology_warnings)
        self.source_path = source_path

    @classmethod
    def build(
        cls,
        segments: LineNetworkModel,
        switches: SwitchModel | None,
        definitions: Iterable[CircuitDefinition],
        *,
        source_path: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> "CircuitCatalogModel":
        definition_values = tuple(definitions)
        valid_ids = {definition.circuit_id for definition in definition_values}
        direct_switches: dict[str, list[int]] = {
            circuit_id: [] for circuit_id in valid_ids
        }
        warnings: list[str] = []
        if switches is not None:
            for record_index, segment_value in enumerate(switches.segment_indices):
                switch_id = switches.switch_ids[record_index]
                circuit_id = switches.circuit_ids[record_index].strip()
                state = switches.states[record_index].strip()
                if state not in {"0", "1"}:
                    warnings.append(
                        f"Chave {switch_id}: ESTADO '{state or '<vazio>'}' inválido; "
                        "a travessia foi bloqueada."
                    )
                if circuit_id not in valid_ids:
                    warnings.append(
                        f"Chave {switch_id}: CIRC_ID '{circuit_id or '<vazio>'}' "
                        "não existe no catálogo; a chave ficou sem circuito."
                    )
                    continue
                direct_switches[circuit_id].append(int(segment_value))

        topology = NetworkTopology(segments, switches)
        memberships: list[CircuitMembership] = []
        for definition in definition_values:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Construção da topologia cancelada.")
            root_index = segments.bars.index_for_id(definition.root_bar_id)
            if root_index is None:
                raise ValueError(
                    f"Barra inicial inexistente: {definition.root_bar_id}"
                )
            memberships.append(
                topology.trace(
                    definition.circuit_id,
                    root_index,
                    direct_switches[definition.circuit_id],
                    cancel_check=cancel_check,
                )
            )
        return cls(
            segments,
            switches,
            definition_values,
            memberships,
            topology_warnings=warnings,
            source_path=source_path,
        )

    def __len__(self) -> int:
        return len(self._definitions)

    @property
    def definitions(self) -> tuple[CircuitDefinition, ...]:
        return self._definitions

    @property
    def memberships(self) -> tuple[CircuitMembership, ...]:
        return self._memberships

    def index_for_id(self, circuit_id: str) -> int | None:
        return self._by_id.get(circuit_id)

    def definition(self, index: int) -> CircuitDefinition:
        if not 0 <= int(index) < len(self):
            raise IndexError(index)
        return self._definitions[int(index)]

    def membership(self, index: int) -> CircuitMembership:
        if not 0 <= int(index) < len(self):
            raise IndexError(index)
        return self._memberships[int(index)]

    @property
    def overlapping_segment_indices(self) -> IndexArray:
        return self._overlapping_segment_indices

    @property
    def segment_owner_counts(self) -> IndexArray:
        return self._segment_owner_counts

    def circuit_indices_for_segment(self, segment_index: int) -> IndexArray:
        if not 0 <= int(segment_index) < len(self.segments):
            raise IndexError(segment_index)
        index = int(segment_index)
        start = int(self._segment_circuit_offsets[index])
        stop = int(self._segment_circuit_offsets[index + 1])
        return self._segment_circuit_indices[start:stop]


class CircuitVisibilityController:
    """Estado visual mutável, separado das associações elétricas imutáveis."""

    __slots__ = (
        "catalog",
        "_checked",
        "_colors",
        "_bar_owner_counts",
        "_bar_visible_counts",
        "_segment_owner_counts",
        "_segment_visible_counts",
        "_bar_mask",
        "_segment_mask",
        "_bar_mask_view",
        "_segment_mask_view",
        "_segment_style_indices",
        "_segment_style_view",
    )

    def __init__(
        self,
        catalog: CircuitCatalogModel,
        checked: Sequence[bool] | None = None,
        colors: Sequence[str] | None = None,
    ) -> None:
        self.catalog = catalog
        if checked is None:
            checked_values = np.ones(len(catalog), dtype=np.bool_)
        else:
            checked_values = np.asarray(checked, dtype=np.bool_)
            if checked_values.ndim != 1 or checked_values.size != len(catalog):
                raise ValueError("O estado visual deve corresponder aos circuitos.")
            checked_values = checked_values.copy()
        self._checked = checked_values
        if colors is None:
            self._colors = list(generate_circuit_palette(len(catalog)))
        else:
            if len(colors) != len(catalog):
                raise ValueError("As cores devem corresponder aos circuitos.")
            self._colors = [normalize_hex_color(value) for value in colors]
        self._bar_owner_counts = np.zeros(len(catalog.segments.bars), dtype=np.int32)
        self._segment_owner_counts = catalog.segment_owner_counts.astype(
            np.int32, copy=True
        )
        for membership in catalog.memberships:
            self._bar_owner_counts[membership.bar_indices] += 1
        self._bar_visible_counts = np.zeros_like(self._bar_owner_counts)
        self._segment_visible_counts = np.zeros_like(self._segment_owner_counts)
        for index, membership in enumerate(catalog.memberships):
            if self._checked[index]:
                self._bar_visible_counts[membership.bar_indices] += 1
                self._segment_visible_counts[membership.segment_indices] += 1
        self._bar_mask = (self._bar_owner_counts == 0) | (
            self._bar_visible_counts > 0
        )
        self._segment_mask = (self._segment_owner_counts == 0) | (
            self._segment_visible_counts > 0
        )
        self._bar_mask_view = self._bar_mask.view()
        self._segment_mask_view = self._segment_mask.view()
        self._bar_mask_view.setflags(write=False)
        self._segment_mask_view.setflags(write=False)
        self._segment_style_indices = np.full(len(catalog.segments), -1, dtype=np.intp)
        for segment_index in range(len(catalog.segments)):
            owners = catalog.circuit_indices_for_segment(segment_index)
            if owners.size:
                self._segment_style_indices[segment_index] = self._first_visible_owner(
                    owners
                )
        self._segment_style_view = self._segment_style_indices.view()
        self._segment_style_view.setflags(write=False)

    @property
    def bar_visible_mask(self) -> BoolArray:
        return self._bar_mask_view

    @property
    def segment_visible_mask(self) -> BoolArray:
        return self._segment_mask_view

    @property
    def checked_states(self) -> tuple[bool, ...]:
        return tuple(bool(value) for value in self._checked)

    @property
    def colors(self) -> tuple[str, ...]:
        return tuple(self._colors)

    @property
    def segment_style_indices(self) -> IndexArray:
        """Circuito efetivo por trecho; -1 é padrão e -2 significa oculto."""

        return self._segment_style_view

    def color(self, index: int) -> str:
        if not 0 <= int(index) < len(self.catalog):
            raise IndexError(index)
        return self._colors[int(index)]

    def set_color(self, index: int, color: str) -> bool:
        if not 0 <= int(index) < len(self.catalog):
            raise IndexError(index)
        circuit_index = int(index)
        normalized = normalize_hex_color(color)
        if self._colors[circuit_index] == normalized:
            return False
        self._colors[circuit_index] = normalized
        return True

    def _first_visible_owner(self, owners: IndexArray) -> int:
        for owner in owners:
            circuit_index = int(owner)
            if self._checked[circuit_index]:
                return circuit_index
        return -2

    def is_visible(self, index: int) -> bool:
        if not 0 <= int(index) < len(self.catalog):
            raise IndexError(index)
        return bool(self._checked[int(index)])

    def set_visible(self, index: int, visible: bool) -> bool:
        if not 0 <= int(index) < len(self.catalog):
            raise IndexError(index)
        circuit_index = int(index)
        visible = bool(visible)
        if bool(self._checked[circuit_index]) == visible:
            return False
        self._checked[circuit_index] = visible
        delta = 1 if visible else -1
        membership = self.catalog.membership(circuit_index)
        bars = membership.bar_indices
        segments = membership.segment_indices
        self._bar_visible_counts[bars] += delta
        self._segment_visible_counts[segments] += delta
        self._bar_mask[bars] = (self._bar_owner_counts[bars] == 0) | (
            self._bar_visible_counts[bars] > 0
        )
        self._segment_mask[segments] = (
            self._segment_owner_counts[segments] == 0
        ) | (self._segment_visible_counts[segments] > 0)
        for segment_index in segments:
            index_value = int(segment_index)
            self._segment_style_indices[index_value] = self._first_visible_owner(
                self.catalog.circuit_indices_for_segment(index_value)
            )
        return True
