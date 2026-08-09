"""Canvas híbrido: visão agregada e itens individuais virtualizados."""

from __future__ import annotations

import math
import traceback
from collections.abc import Iterable, Sequence

import numpy as np
from PyQt6.QtCore import (
    QLineF,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSignalBlocker,
    QSize,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QBrush,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
)

from .model import (
    BoolArray,
    Bounds,
    CircuitModel,
    FeatureSelection,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    RegulatorModel,
    SwitchModel,
)
from .equivalent_network import EquivalentNetworkModel
from .mapa_tiles import (
    GerenciadorTiles,
    cantos_lonlat_da_faixa,
    lonlat_para_tile,
    nivel_zoom,
    sub_rect_no_pai,
    tile_pai,
)

try:
    from pyproj import Transformer
except ModuleNotFoundError:  # pragma: no cover - dependência obrigatória do pacote
    Transformer = None  # type: ignore[assignment]


LoadRenderModel = LoadModel | EquivalentNetworkModel | GeneratorModel


POINT_DIAMETER_PX = 5.0
SELECTED_DIAMETER_PX = 9.0
CLICK_TOLERANCE_PX = 10.0
# Uma margem curta evita recriação em pequenos pans sem multiplicar por nove a
# quantidade de objetos ativos (como ocorria com uma tela inteira por lado).
VIRTUALIZATION_MARGIN = 0.25
VIRTUALIZATION_DEBOUNCE_MS = 120
# QGraphicsObject tem custo fixo por paint. Acima deste teto a seleção continua
# indexada, mas os pontos são desenhados pelo item agregado em uma única chamada.
MAX_ACTIVE_ITEMS = 1_000
MATERIALIZE_BATCH_SIZE = 250
MAX_POOL_SIZE = 1_000
MIN_ZOOM_SCALE = 1e-8
MAX_USEFUL_ZOOM_SCALE = 100.0
QT_SCROLLBAR_COORDINATE_LIMIT = float((1 << 31) - 1)
QT_SCROLLBAR_SAFETY_FACTOR = 0.5

POINT_COLOR = QColor("#202020")
SELECTED_COLOR = QColor("#ffcc00")
SELECTED_OUTLINE = QColor("#7a5a00")
CANVAS_BACKGROUND = QColor("#f7f7f7")
LINE_COLOR = QColor("#555555")
SWITCH_COLOR = QColor("#ff0000")
# Laranja: contrasta com o cinza do canvas, com o vermelho da chave, com o
# amarelo da seleção e com as três cores de fase.
REGULATOR_COLOR = QColor("#ff8800")
# Anel do regulador, no ponto médio do trecho. 9 px de diâmetro fica entre o
# ponto de barra (5) e o retângulo de carga (12x8): visível sem destoar.
REGULATOR_DIAMETER_PX = 9.0
REGULATOR_RING_WIDTH_PX = 2.0
SEGMENT_SELECTION_WIDTH_PX = 3.0
NORMAL_SEGMENT_WIDTH_PX = 3.0
SWITCH_SEGMENT_WIDTH_PX = 1.0
LOAD_WIDTH_PX = 12.0
LOAD_HEIGHT_PX = 8.0
LOAD_CONNECTOR_LENGTH_PX = 6.0
LOAD_HORIZONTAL_PITCH_PX = 15.0
LOAD_VERTICAL_PITCH_PX = 12.0
LOAD_OVERVIEW_DIAMETER_PX = 7.0
LOAD_COLOR = QColor("#202020")
GENERATOR_DIAMETER_PX = 10.0
ATTACHED_VERTICAL_PITCH_PX = 14.0


def _feature_ids(model: LoadRenderModel) -> tuple[str, ...]:
    if isinstance(model, GeneratorModel):
        return model.generator_ids
    return model.load_ids


def _scene_point(model: CircuitModel, index: int) -> QPointF:
    """Converte UTM para cena, invertendo Y para manter o norte para cima."""

    return QPointF(float(model.x[index]), -float(model.y[index]))


def _model_bounds_from_scene(rect: QRectF) -> Bounds:
    normalized = rect.normalized()
    return Bounds(
        normalized.left(),
        -normalized.bottom(),
        normalized.right(),
        -normalized.top(),
    )


def _visibility_mask(mask: BoolArray | None, size: int, label: str) -> BoolArray:
    if mask is None:
        return np.ones(size, dtype=np.bool_)
    values = np.asarray(mask, dtype=np.bool_)
    if values.ndim != 1 or values.size != size:
        raise ValueError(f"A máscara de {label} deve possuir {size:n} valores.")
    return np.ascontiguousarray(values)


def _style_indices(
    styles: Sequence[int] | None,
    size: int,
) -> np.ndarray:
    if styles is None:
        return np.full(size, -1, dtype=np.intp)
    values = np.asarray(styles, dtype=np.intp)
    if values.ndim != 1 or values.size != size:
        raise ValueError(f"Os estilos de trechos devem possuir {size:n} valores.")
    return np.ascontiguousarray(values)


def _render_colors(colors: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in colors:
        color = QColor(str(value))
        if not color.isValid():
            raise ValueError(f"Cor inválida: {value}")
        normalized.append(color.name().upper())
    return tuple(normalized)


def load_layout_offsets_for_models(
    models: Sequence[LoadRenderModel],
    include_masks: Sequence[Sequence[bool] | np.ndarray | None] | None = None,
) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    """Distribui conjuntamente modelos de cargas na mesma grade por barra."""

    if include_masks is None:
        masks = tuple(None for _ in models)
    else:
        masks = tuple(include_masks)
        if len(masks) != len(models):
            raise ValueError("Cada modelo deve possuir uma máscara de layout.")

    vertical_pitch = (
        ATTACHED_VERTICAL_PITCH_PX
        if any(isinstance(model, GeneratorModel) for model in models)
        else LOAD_VERTICAL_PITCH_PX
    )
    offsets = [
        (
            np.zeros(len(model), dtype=np.float64),
            np.full(len(model), LOAD_CONNECTOR_LENGTH_PX, dtype=np.float64),
        )
        for model in models
    ]
    by_bar: dict[int, list[tuple[int, int]]] = {}
    for model_index, model in enumerate(models):
        raw_mask = masks[model_index]
        mask = (
            None
            if raw_mask is None
            else _visibility_mask(raw_mask, len(model), "layout")
        )
        for index, bar_index in enumerate(model.bar_indices):
            if mask is not None and not bool(mask[index]):
                continue
            by_bar.setdefault(int(bar_index), []).append((model_index, index))
    for indices in by_bar.values():
        indices.sort(
            key=lambda value: (
                value[0],
                _feature_ids(models[value[0]])[value[1]].casefold(),
                value[0],
                value[1],
            )
        )
        columns = max(1, math.ceil(math.sqrt(len(indices))))
        for row, start in enumerate(range(0, len(indices), columns)):
            row_indices = indices[start : start + columns]
            row_size = len(row_indices)
            for column, (model_index, index) in enumerate(row_indices):
                x_offsets, y_offsets = offsets[model_index]
                x_offsets[index] = (
                    column - (row_size - 1) / 2.0
                ) * LOAD_HORIZONTAL_PITCH_PX
                y_offsets[index] = (
                    LOAD_CONNECTOR_LENGTH_PX + row * vertical_pitch
                )
    result: list[tuple[np.ndarray, np.ndarray]] = []
    for x_offsets, y_offsets in offsets:
        x_offsets.setflags(write=False)
        y_offsets.setflags(write=False)
        result.append((x_offsets, y_offsets))
    return tuple(result)


def _load_layout_offsets(model: LoadRenderModel) -> tuple[np.ndarray, np.ndarray]:
    """Distribui cargas da mesma barra em uma grade determinística."""

    return load_layout_offsets_for_models((model,))[0]


def _load_rect(x_offset: float, y_offset: float) -> QRectF:
    return QRectF(
        x_offset - LOAD_WIDTH_PX / 2.0,
        y_offset,
        LOAD_WIDTH_PX,
        LOAD_HEIGHT_PX,
    )


class BarsOverviewItem(QGraphicsItem):
    """Representação de todas as barras em uma única operação de pintura."""

    def __init__(self, model: CircuitModel) -> None:
        super().__init__()
        self._model = model
        self._visibility_mask = np.ones(len(model), dtype=np.bool_)
        self._points = QPolygonF()
        self._rebuild_points()
        bounds = model.bounds
        width = max(bounds.width, 1.0)
        height = max(bounds.height, 1.0)
        self._bounds = QRectF(bounds.left, -bounds.bottom, width, height).adjusted(
            -POINT_DIAMETER_PX,
            -POINT_DIAMETER_PX,
            POINT_DIAMETER_PX,
            POINT_DIAMETER_PX,
        )
        self.setZValue(-10.0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        # Entre alterações da máscara, o Qt reaproveita a rasterização em pan e
        # repaints subsequentes sem percorrer novamente todos os pontos.
        self.setCacheMode(
            QGraphicsItem.CacheMode.DeviceCoordinateCache,
            QSize(4_096, 4_096),
        )

    @property
    def visible_point_count(self) -> int:
        return self._points.size()

    def set_visibility_mask(self, mask: BoolArray | None) -> None:
        values = _visibility_mask(mask, len(self._model), "barras")
        if np.array_equal(values, self._visibility_mask):
            return
        self._visibility_mask = values.copy()
        self._rebuild_points()
        self.update()

    def _rebuild_points(self) -> None:
        indices = np.flatnonzero(self._visibility_mask)
        model = self._model
        self._points = QPolygonF(
            [
                QPointF(float(model.x[index]), -float(model.y[index]))
                for index in indices
            ]
        )

    def boundingRect(self) -> QRectF:  # noqa: N802 - API do Qt
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(POINT_COLOR)
        pen.setWidthF(POINT_DIAMETER_PX)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawPoints(self._points)
        painter.restore()


class LoadsOverviewItem(QGraphicsItem):
    """Marcadores agregados de cargas, desenhados sob as barras."""

    def __init__(
        self,
        model: LoadRenderModel,
        *,
        symbol_kind: str = "load",
        x_offsets: Sequence[float] | np.ndarray | None = None,
        y_offsets: Sequence[float] | np.ndarray | None = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._symbol_kind = symbol_kind
        self._visibility_mask = np.ones(len(model), dtype=np.bool_)
        self._indices = np.arange(len(model), dtype=np.intp)
        self._layout_applied = False
        self._x_offsets = np.zeros(len(model), dtype=np.float64)
        self._y_offsets = np.full(
            len(model), LOAD_CONNECTOR_LENGTH_PX, dtype=np.float64
        )
        self._rebuild_points()
        bounds = model.bars.bounds
        width = max(bounds.width, 1.0)
        height = max(bounds.height, 1.0)
        padding = max(width, height, 100.0)
        self._bounds = QRectF(bounds.left, -bounds.bottom, width, height).adjusted(
            -padding,
            -padding,
            padding,
            padding,
        )
        if x_offsets is not None and y_offsets is not None:
            self.set_layout_offsets(x_offsets, y_offsets)
        self.setZValue(-11.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setCacheMode(
            QGraphicsItem.CacheMode.DeviceCoordinateCache,
            QSize(4_096, 4_096),
        )

    @property
    def visible_point_count(self) -> int:
        return int(self._indices.size)

    @property
    def layout_applied(self) -> bool:
        return self._layout_applied

    def set_layout_offsets(
        self,
        x_offsets: Sequence[float] | np.ndarray,
        y_offsets: Sequence[float] | np.ndarray,
    ) -> None:
        x_values = np.ascontiguousarray(x_offsets, dtype=np.float64)
        y_values = np.ascontiguousarray(y_offsets, dtype=np.float64)
        if x_values.shape != (len(self._model),) or y_values.shape != (
            len(self._model),
        ):
            raise ValueError("O layout agregado deve corresponder ao modelo.")
        self._x_offsets = x_values
        self._y_offsets = y_values
        self._layout_applied = True
        self.update()

    def set_visibility_mask(self, mask: BoolArray | None) -> None:
        values = _visibility_mask(mask, len(self._model), "cargas")
        if np.array_equal(values, self._visibility_mask):
            return
        self._visibility_mask = values.copy()
        self._rebuild_points()
        self.update()

    def _rebuild_points(self) -> None:
        self._indices = np.flatnonzero(self._visibility_mask).astype(
            np.intp, copy=False
        )

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.save()
        if not self._layout_applied:
            points = QPolygonF()
            bars = self._model.bars
            for index in self._indices:
                bar_index = int(self._model.bar_indices[int(index)])
                points.append(
                    QPointF(float(bars.x[bar_index]), -float(bars.y[bar_index]))
                )
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(LOAD_COLOR)
            pen.setWidthF(LOAD_OVERVIEW_DIAMETER_PX)
            pen.setCapStyle(
                Qt.PenCapStyle.RoundCap
                if self._symbol_kind == "generator"
                else Qt.PenCapStyle.SquareCap
            )
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPoints(points)
            painter.restore()
            return
        world = painter.worldTransform()
        painter.resetTransform()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(LOAD_COLOR))
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            self._symbol_kind == "generator",
        )
        bars = self._model.bars
        for index in self._indices:
            bar_index = int(self._model.bar_indices[int(index)])
            anchor = world.map(
                QPointF(float(bars.x[bar_index]), -float(bars.y[bar_index]))
            )
            x = anchor.x() + float(self._x_offsets[index])
            y = anchor.y() + float(self._y_offsets[index])
            if self._symbol_kind == "generator":
                painter.drawEllipse(
                    QRectF(
                        x - GENERATOR_DIAMETER_PX / 2.0,
                        y,
                        GENERATOR_DIAMETER_PX,
                        GENERATOR_DIAMETER_PX,
                    )
                )
            else:
                painter.drawRect(
                    QRectF(
                        x - LOAD_WIDTH_PX / 2.0,
                        y,
                        LOAD_WIDTH_PX,
                        LOAD_HEIGHT_PX,
                    )
                )
        painter.restore()


class LineNetworkItem(QGraphicsItem):
    """Camada agregada e filtrável de trechos da rede.

    Os segmentos visíveis são compilados em subcaminhos desconectados de um único
    ``QPainterPath``. A máscara só recompila o caminho ao mudar a visibilidade;
    cada quadro continua exigindo somente um ``drawPath``.
    """

    def __init__(self, model: LineNetworkModel) -> None:
        super().__init__()
        self._model = model
        self._visibility_mask = np.ones(len(model), dtype=np.bool_)
        self._switch_segment_mask = np.zeros(len(model), dtype=np.bool_)
        self._segment_style_indices = np.full(len(model), -1, dtype=np.intp)
        self._colors: tuple[str, ...] = ()
        self._visible_segment_count = len(model)
        self._paths: dict[int, QPainterPath] = {}
        self._geometry_revision = 0
        self._rebuild_paths()

        bounds = model.bounds
        width = max(bounds.width, 1.0)
        height = max(bounds.height, 1.0)
        padding = max(max(width, height) * 0.001, 1.0)
        self._bounds = QRectF(bounds.left, -bounds.bottom, width, height).adjusted(
            -padding,
            -padding,
            padding,
            padding,
        )
        self.setZValue(-20.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setCacheMode(
            QGraphicsItem.CacheMode.DeviceCoordinateCache,
            QSize(4_096, 4_096),
        )

    def _rebuild_paths(self) -> None:
        paths: dict[int, QPainterPath] = {}
        bars = self._model.bars
        indices = np.flatnonzero(
            self._visibility_mask & ~self._switch_segment_mask
        )
        for segment_index in indices:
            style_index = int(self._segment_style_indices[segment_index])
            if style_index == -2:
                continue
            category = max(style_index, -1)
            path = paths.setdefault(category, QPainterPath())
            start = int(self._model.start_indices[segment_index])
            end = int(self._model.end_indices[segment_index])
            path.moveTo(float(bars.x[start]), -float(bars.y[start]))
            path.lineTo(float(bars.x[end]), -float(bars.y[end]))
        self._paths = paths
        self._visible_segment_count = sum(
            1
            for segment_index in indices
            if int(self._segment_style_indices[segment_index]) != -2
        )
        self._geometry_revision += 1

    @property
    def segment_count(self) -> int:
        return len(self._model)

    @property
    def visible_segment_count(self) -> int:
        return self._visible_segment_count

    @property
    def category_path_count(self) -> int:
        return len(self._paths)

    @property
    def geometry_revision(self) -> int:
        return self._geometry_revision

    def set_visibility_mask(self, mask: BoolArray | None) -> None:
        self.set_circuit_rendering(
            mask,
            self._segment_style_indices,
            self._colors,
        )

    def set_switch_segment_indices(
        self,
        segment_indices: Sequence[int] | None,
    ) -> None:
        switch_mask = np.zeros(len(self._model), dtype=np.bool_)
        if segment_indices is not None:
            indices = np.asarray(segment_indices, dtype=np.intp)
            if indices.ndim != 1:
                raise ValueError("Os índices de chaves devem formar um vetor.")
            if indices.size and (
                (indices < 0).any() or (indices >= len(self._model)).any()
            ):
                raise IndexError("Uma chave referencia um trecho inexistente.")
            switch_mask[indices] = True
        if np.array_equal(switch_mask, self._switch_segment_mask):
            return
        self._switch_segment_mask = switch_mask
        self._rebuild_paths()
        self.update()

    def set_circuit_rendering(
        self,
        mask: BoolArray | None,
        style_indices: Sequence[int] | None,
        colors: Sequence[str],
    ) -> None:
        self._set_rendering(mask, style_indices, colors)

    def set_phase_rendering(
        self,
        mask: BoolArray | None,
        style_indices: Sequence[int],
        colors: Sequence[str],
    ) -> None:
        self._set_rendering(mask, style_indices, colors)

    def _set_rendering(
        self,
        mask: BoolArray | None,
        style_indices: Sequence[int] | None,
        colors: Sequence[str],
    ) -> None:
        visibility = _visibility_mask(mask, len(self._model), "trechos")
        styles = _style_indices(style_indices, len(self._model))
        palette = _render_colors(colors)
        if styles.size:
            highest_style = int(styles.max(initial=-1))
            if highest_style >= len(palette):
                raise ValueError("Um estilo de trecho não possui cor correspondente.")
        geometry_changed = not np.array_equal(
            visibility, self._visibility_mask
        ) or not np.array_equal(styles, self._segment_style_indices)
        color_changed = palette != self._colors
        if not geometry_changed and not color_changed:
            return
        self._visibility_mask = visibility.copy()
        self._segment_style_indices = styles.copy()
        self._colors = palette
        if geometry_changed:
            self._rebuild_paths()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for category, path in self._paths.items():
            color = LINE_COLOR if category < 0 else QColor(self._colors[category])
            pen = QPen(color)
            pen.setWidthF(NORMAL_SEGMENT_WIDTH_PX)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.drawPath(path)
        painter.restore()


class SwitchNetworkItem(QGraphicsItem):
    """Simbologia agregada dos trechos classificados como chaves."""

    def __init__(self, model: SwitchModel) -> None:
        super().__init__()
        self._model = model
        self._segment_visibility_mask = np.ones(len(model.segments), dtype=np.bool_)
        self._segment_style_indices = np.full(
            len(model.segments), -1, dtype=np.intp
        )
        self._colors: tuple[str, ...] = ()
        self._phase_rendering = False
        self._visible_switch_count = len(model)
        self._red_path = QPainterPath()
        self._colored_paths: dict[int, QPainterPath] = {}
        self._geometry_revision = 0
        self._rebuild_paths()
        path_bounds = self._red_path.boundingRect()
        width = max(path_bounds.width(), 1.0)
        height = max(path_bounds.height(), 1.0)
        padding = max(max(width, height) * 0.001, 1.0)
        self._bounds = path_bounds.adjusted(-padding, -padding, padding, padding)
        self.setZValue(-15.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setCacheMode(
            QGraphicsItem.CacheMode.DeviceCoordinateCache,
            QSize(4_096, 4_096),
        )

    def _rebuild_paths(self) -> None:
        red_path = QPainterPath()
        colored_paths: dict[int, QPainterPath] = {}
        segments = self._model.segments
        bars = segments.bars
        visible_count = 0
        for segment_index in self._model.segment_indices:
            index = int(segment_index)
            style_index = int(self._segment_style_indices[index])
            if not self._segment_visibility_mask[index] or style_index == -2:
                continue
            visible_count += 1
            start = int(segments.start_indices[index])
            end = int(segments.end_indices[index])
            start_x = float(bars.x[start])
            start_y = -float(bars.y[start])
            end_x = float(bars.x[end])
            end_y = -float(bars.y[end])
            if self._phase_rendering:
                category = max(style_index, -1)
                path = colored_paths.setdefault(category, QPainterPath())
            else:
                path = red_path
            path.moveTo(start_x, start_y)
            path.lineTo(end_x, end_y)
        self._red_path = red_path
        self._colored_paths = colored_paths
        self._visible_switch_count = visible_count
        self._geometry_revision += 1

    @property
    def switch_count(self) -> int:
        return len(self._model)

    @property
    def visible_switch_count(self) -> int:
        return self._visible_switch_count

    @property
    def colored_path_count(self) -> int:
        return len(self._colored_paths)

    @property
    def geometry_revision(self) -> int:
        return self._geometry_revision

    def set_visibility_mask(self, segment_mask: BoolArray | None) -> None:
        if self._phase_rendering:
            self.set_phase_rendering(
                segment_mask,
                self._segment_style_indices,
                self._colors,
            )
        else:
            self.set_circuit_rendering(
                segment_mask,
                self._segment_style_indices,
                self._colors,
            )

    def set_circuit_rendering(
        self,
        segment_mask: BoolArray | None,
        style_indices: Sequence[int] | None,
        colors: Sequence[str],
    ) -> None:
        self._set_rendering(
            segment_mask,
            style_indices,
            colors,
            phase_rendering=False,
        )

    def set_phase_rendering(
        self,
        segment_mask: BoolArray | None,
        style_indices: Sequence[int],
        colors: Sequence[str],
    ) -> None:
        self._set_rendering(
            segment_mask,
            style_indices,
            colors,
            phase_rendering=True,
        )

    def _set_rendering(
        self,
        segment_mask: BoolArray | None,
        style_indices: Sequence[int] | None,
        colors: Sequence[str],
        *,
        phase_rendering: bool,
    ) -> None:
        visibility = _visibility_mask(
            segment_mask, len(self._model.segments), "trechos"
        )
        styles = _style_indices(style_indices, len(self._model.segments))
        palette = _render_colors(colors)
        if styles.size:
            highest_style = int(styles.max(initial=-1))
            if highest_style >= len(palette):
                raise ValueError("Um estilo de chave não possui cor correspondente.")
        geometry_changed = not np.array_equal(
            visibility, self._segment_visibility_mask
        ) or not np.array_equal(
            styles, self._segment_style_indices
        ) or phase_rendering != self._phase_rendering
        color_changed = palette != self._colors
        if not geometry_changed and not color_changed:
            return
        self._segment_visibility_mask = visibility.copy()
        self._segment_style_indices = styles.copy()
        self._colors = palette
        self._phase_rendering = phase_rendering
        if geometry_changed:
            self._rebuild_paths()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(SWITCH_COLOR)
        pen.setWidthF(SWITCH_SEGMENT_WIDTH_PX)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.drawPath(self._red_path)
        for category, path in self._colored_paths.items():
            color = LINE_COLOR if category < 0 else QColor(self._colors[category])
            pen.setColor(color)
            painter.setPen(pen)
            painter.drawPath(path)
        painter.restore()


class RegulatorNetworkItem(QGraphicsItem):
    """Anel no ponto médio de cada trecho que tem regulador.

    Um único item para todos os anéis, como o resto da camada agregada: com 100
    mil trechos, um item por símbolo seria inviável. Os pontos médios são
    recompilados só quando a máscara de visibilidade muda.

    O raio é fixo **em pixels**, derivado da escala do próprio ``painter``. O
    ``ItemIgnoresTransformations`` que os símbolos materializados usam não serve
    aqui: é uma flag por item, e forçaria um item por regulador.
    """

    def __init__(self, model: RegulatorModel) -> None:
        super().__init__()
        self._model = model
        self._segment_visibility_mask = np.ones(
            len(model.segments), dtype=np.bool_
        )
        self._centers: list[QPointF] = []
        self._geometry_revision = 0
        self._rebuild_centers()

        bounds = model.segments.bars.bounds
        width = max(bounds.width, 1.0)
        height = max(bounds.height, 1.0)
        padding = max(max(width, height) * 0.001, 1.0)
        self._bounds = QRectF(
            bounds.left, -bounds.bottom, width, height
        ).adjusted(-padding, -padding, padding, padding)
        self.setZValue(-12.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setCacheMode(
            QGraphicsItem.CacheMode.DeviceCoordinateCache,
            QSize(4_096, 4_096),
        )

    def _rebuild_centers(self) -> None:
        segments = self._model.segments
        bars = segments.bars
        centers: list[QPointF] = []
        for segment_index in self._model.segment_indices:
            index = int(segment_index)
            if not self._segment_visibility_mask[index]:
                continue
            start = int(segments.start_indices[index])
            end = int(segments.end_indices[index])
            # A inversão de Y é a mesma do resto da cena; o sinal negativo nunca
            # sai daqui.
            centers.append(
                QPointF(
                    (float(bars.x[start]) + float(bars.x[end])) / 2.0,
                    -(float(bars.y[start]) + float(bars.y[end])) / 2.0,
                )
            )
        self._centers = centers
        self._geometry_revision += 1

    @property
    def regulator_count(self) -> int:
        return len(self._model)

    @property
    def visible_regulator_count(self) -> int:
        return len(self._centers)

    @property
    def geometry_revision(self) -> int:
        return self._geometry_revision

    def set_visibility_mask(self, segment_mask: BoolArray | None) -> None:
        visibility = _visibility_mask(
            segment_mask, len(self._model.segments), "trechos"
        )
        if np.array_equal(visibility, self._segment_visibility_mask):
            return
        self._segment_visibility_mask = visibility.copy()
        self._rebuild_centers()
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        if not self._centers:
            return
        # O raio sai em unidades de cena para acompanhar a transformação do
        # painter, mas medindo sempre os mesmos pixels na tela — mesmo idioma do
        # hit-test, que converte CLICK_TOLERANCE_PX pela escala corrente.
        scale = abs(painter.worldTransform().m11())
        radius = REGULATOR_DIAMETER_PX / 2.0 / max(scale, 1e-12)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(REGULATOR_COLOR)
        pen.setWidthF(REGULATOR_RING_WIDTH_PX)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for center in self._centers:
            painter.drawEllipse(center, radius, radius)
        painter.restore()


class SegmentSelectionOverlayItem(QGraphicsLineItem):
    """Destaca somente o trecho selecionado sem invalidar a camada agregada."""

    def __init__(self) -> None:
        super().__init__()
        self.index = -1
        pen = QPen(SELECTED_COLOR)
        pen.setWidthF(SEGMENT_SELECTION_WIDTH_PX)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.setZValue(90.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setVisible(False)

    def bind(self, model: LineNetworkModel, index: int) -> None:
        if not 0 <= int(index) < len(model):
            raise IndexError(index)
        self.index = int(index)
        start = int(model.start_indices[self.index])
        end = int(model.end_indices[self.index])
        bars = model.bars
        self.setLine(
            QLineF(
                float(bars.x[start]),
                -float(bars.y[start]),
                float(bars.x[end]),
                -float(bars.y[end]),
            )
        )
        self.setToolTip(model.segment_ids[self.index])
        self.setVisible(True)
        self.update()

    def clear(self) -> None:
        self.index = -1
        self.setToolTip("")
        self.setVisible(False)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        super().paint(painter, option, widget)
        painter.restore()


class BranchHighlightOverlayItem(QGraphicsItem):
    """Destaca um conjunto de trechos em uma única operação vetorial."""

    def __init__(self) -> None:
        super().__init__()
        self._path = QPainterPath()
        self._bounds = QRectF()
        self._segment_indices: tuple[int, ...] = ()
        self.setZValue(95.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setVisible(False)

    @property
    def segment_indices(self) -> tuple[int, ...]:
        return self._segment_indices

    def bind(
        self,
        model: LineNetworkModel,
        segment_indices: Iterable[int],
    ) -> None:
        indices = tuple(int(value) for value in segment_indices)
        if not indices:
            raise ValueError("O destaque deve possuir ao menos um trecho.")
        if any(index < 0 or index >= len(model) for index in indices):
            raise IndexError("O destaque referencia um trecho inexistente.")
        path = QPainterPath()
        bars = model.bars
        for index in indices:
            start = int(model.start_indices[index])
            end = int(model.end_indices[index])
            path.moveTo(float(bars.x[start]), -float(bars.y[start]))
            path.lineTo(float(bars.x[end]), -float(bars.y[end]))
        self.prepareGeometryChange()
        self._path = path
        self._bounds = path.boundingRect().adjusted(-1.0, -1.0, 1.0, 1.0)
        self._segment_indices = indices
        self.setVisible(True)
        self.update()

    def clear(self) -> None:
        if not self._segment_indices:
            self.setVisible(False)
            return
        self.prepareGeometryChange()
        self._path = QPainterPath()
        self._bounds = QRectF()
        self._segment_indices = ()
        self.setVisible(False)
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(SELECTED_COLOR)
        pen.setWidthF(SEGMENT_SELECTION_WIDTH_PX)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)
        painter.restore()


class BarraItem(QGraphicsObject):
    """Representação interativa e reciclável de uma barra."""

    def __init__(self) -> None:
        super().__init__()
        self.index = -1
        self.setZValue(10.0)
        flags = (
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        self.setFlags(flags)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def bind(self, model: CircuitModel, index: int) -> None:
        self.index = int(index)
        self.setPos(_scene_point(model, self.index))
        self.setToolTip(model.bar_ids[self.index])
        self.setSelected(False)
        self.setVisible(True)
        self.update()

    def unbind(self) -> None:
        self.setSelected(False)
        self.setVisible(False)
        self.setToolTip("")
        self.index = -1

    def boundingRect(self) -> QRectF:  # noqa: N802 - API do Qt
        radius = SELECTED_DIAMETER_PX / 2.0 + 1.0
        return QRectF(-radius, -radius, radius * 2.0, radius * 2.0)

    def shape(self):  # noqa: ANN201
        path = QPainterPath()
        radius = SELECTED_DIAMETER_PX / 2.0
        path.addEllipse(QPointF(0.0, 0.0), radius, radius)
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        selected = self.isSelected()
        diameter = SELECTED_DIAMETER_PX if selected else POINT_DIAMETER_PX
        radius = diameter / 2.0
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, selected)
        painter.setPen(QPen(SELECTED_OUTLINE if selected else POINT_COLOR, 1.0))
        painter.setBrush(QBrush(SELECTED_COLOR if selected else POINT_COLOR))
        painter.drawEllipse(QPointF(0.0, 0.0), radius, radius)
        painter.restore()


class SelectionOverlayItem(QGraphicsItem):
    """Mantém a seleção visível mesmo sem um ``BarraItem`` materializado."""

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(100.0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)

    def boundingRect(self) -> QRectF:  # noqa: N802
        radius = SELECTED_DIAMETER_PX / 2.0 + 1.0
        return QRectF(-radius, -radius, radius * 2.0, radius * 2.0)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        radius = SELECTED_DIAMETER_PX / 2.0
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(SELECTED_OUTLINE, 1.0))
        painter.setBrush(QBrush(SELECTED_COLOR))
        painter.drawEllipse(QPointF(0.0, 0.0), radius, radius)
        painter.restore()


class LoadItem(QGraphicsObject):
    """Símbolo reciclável de uma carga, com geometria fixa em pixels."""

    def __init__(self, *, symbol_kind: str = "load") -> None:
        super().__init__()
        if symbol_kind not in {"load", "generator"}:
            raise ValueError(f"Símbolo associado desconhecido: {symbol_kind}")
        self._symbol_kind = symbol_kind
        self.index = -1
        self._x_offset = 0.0
        self._y_offset = LOAD_CONNECTOR_LENGTH_PX
        self.setZValue(20.0)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def bind(
        self,
        model: LoadRenderModel,
        index: int,
        x_offset: float,
        y_offset: float,
    ) -> None:
        self.prepareGeometryChange()
        self.index = int(index)
        self._x_offset = float(x_offset)
        self._y_offset = float(y_offset)
        bar_index = int(model.bar_indices[self.index])
        self.setPos(_scene_point(model.bars, bar_index))
        tooltip = _feature_ids(model)[self.index]
        record = model.record(self.index)
        if getattr(record, "origin_kind", None) == "branch_aggregate":
            tooltip += " — carga equivalente de ramal"
        self.setToolTip(tooltip)
        self.setSelected(False)
        self.setVisible(True)
        self.update()

    def unbind(self) -> None:
        self.setSelected(False)
        self.setVisible(False)
        self.setToolTip("")
        self.index = -1

    @property
    def symbol_rect(self) -> QRectF:
        if self._symbol_kind == "generator":
            return QRectF(
                self._x_offset - GENERATOR_DIAMETER_PX / 2.0,
                self._y_offset,
                GENERATOR_DIAMETER_PX,
                GENERATOR_DIAMETER_PX,
            )
        return _load_rect(self._x_offset, self._y_offset)

    def boundingRect(self) -> QRectF:  # noqa: N802
        rect = self.symbol_rect.united(
            QRectF(0.0, 0.0, self._x_offset, self._y_offset).normalized()
        )
        return rect.adjusted(-2.0, -2.0, 2.0, 2.0)

    def shape(self):  # noqa: ANN201
        path = QPainterPath()
        if self._symbol_kind == "generator":
            path.addEllipse(self.symbol_rect)
        else:
            path.addRect(self.symbol_rect)
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        selected = self.isSelected()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, selected)
        pen = QPen(SELECTED_OUTLINE if selected else LOAD_COLOR, 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(
            QPointF(0.0, 0.0),
            QPointF(self._x_offset, self._y_offset),
        )
        painter.setBrush(QBrush(SELECTED_COLOR if selected else CANVAS_BACKGROUND))
        if self._symbol_kind == "generator":
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.drawEllipse(self.symbol_rect)
        else:
            painter.drawRect(self.symbol_rect)
        painter.restore()


class LoadSelectionOverlayItem(LoadItem):
    """Mantém a carga selecionada visível fora da camada materializada."""

    def __init__(self, *, symbol_kind: str = "load") -> None:
        super().__init__(symbol_kind=symbol_kind)
        self.setZValue(110.0)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(SELECTED_OUTLINE, 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(
            QPointF(0.0, 0.0),
            QPointF(self._x_offset, self._y_offset),
        )
        painter.setBrush(QBrush(SELECTED_COLOR))
        if self._symbol_kind == "generator":
            painter.drawEllipse(self.symbol_rect)
        else:
            painter.drawRect(self.symbol_rect)
        painter.restore()


class DiagramView(QGraphicsView):
    """View geográfica com navegação e seleção indexada."""

    viewportChanged = pyqtSignal()
    zoomLimitReached = pyqtSignal()
    selectionRequested = pyqtSignal(object)
    mouseCoordinateChanged = pyqtSignal(float, float)
    satelliteUnavailable = pyqtSignal(str)

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:  # noqa: ANN001
        super().__init__(scene, parent)
        self._model: CircuitModel | None = None
        self._line_model: LineNetworkModel | None = None
        self._load_model: LoadModel | None = None
        self._load_layer: LoadVirtualizer | None = None
        self._generator_model: GeneratorModel | None = None
        self._generator_layer: LoadVirtualizer | None = None
        self._equivalent_load_model: EquivalentNetworkModel | None = None
        self._equivalent_load_layer: LoadVirtualizer | None = None
        self._bar_visibility_mask: BoolArray | None = None
        self._segment_visibility_mask: BoolArray | None = None
        self._bars_visible = True
        self._interaction_mode = "select"
        self._space_down = False
        self._panning = False
        self._pan_last = QPoint()
        self._press_pos = QPoint()
        self._pan_moved = False
        self._zoom_limit_notified = False
        self._satellite_enabled = False
        self._satellite_opacity = 1.0
        self._tile_manager: GerenciadorTiles | None = None
        self._satellite_last_frame: tuple[int, int, int, int, int] | None = None
        self._satellite_attribution_rect: QRect | None = None
        self._satellite_failure_notified = False
        self._model_to_geographic = None
        self._geographic_to_model = None
        self._transformer_epsg: int | None = None
        self._satellite_prefetch_timer = QTimer(self)
        self._satellite_prefetch_timer.setSingleShot(True)
        self._satellite_prefetch_timer.setInterval(250)
        self._satellite_prefetch_timer.timeout.connect(
            self._prefetch_neighboring_tiles
        )

        # Os pontos comuns são rasterizados sem suavização. O destaque amarelo
        # habilita antialiasing localmente no seu próprio paint().
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontSavePainterState, True
        )
        self.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setBackgroundBrush(QBrush(CANVAS_BACKGROUND))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._apply_cursor()

    @property
    def satellite_enabled(self) -> bool:
        return self._satellite_enabled

    @property
    def tile_manager(self) -> GerenciadorTiles | None:
        return self._tile_manager

    def set_satellite_enabled(self, enabled: bool) -> None:
        self._satellite_enabled = bool(enabled)
        if not self._satellite_enabled:
            self._satellite_prefetch_timer.stop()
            self._satellite_last_frame = None
        self.viewport().update()

    def set_tile_manager(self, manager: GerenciadorTiles | None) -> None:
        if manager is self._tile_manager:
            return
        previous = self._tile_manager
        self._tile_manager = manager
        self._satellite_last_frame = None
        self._satellite_prefetch_timer.stop()
        if previous is not None:
            previous.fechar()
            previous.deleteLater()
        self.viewport().update()

    def shutdown_satellite(self) -> None:
        """Interrompe downloads e solta o cache de memória da camada."""

        self._satellite_enabled = False
        self.set_tile_manager(None)

    def _reset_geographic_transformers(self) -> None:
        self._model_to_geographic = None
        self._geographic_to_model = None
        self._transformer_epsg = None
        self._satellite_failure_notified = False

    def _notify_satellite_failure(self, reason: str) -> None:
        """Denuncia UMA vez por modelo que o fundo não pôde ser posicionado.

        Sem isso, uma projeção saturada (coordenadas fora da faixa UTM) ou uma
        exceção no desenho viravam apenas um fundo branco silencioso.
        """

        if self._satellite_failure_notified:
            return
        self._satellite_failure_notified = True
        self.satelliteUnavailable.emit(reason)

    def _ensure_geographic_transformers(self) -> bool:
        model = self._model
        if model is None:
            return False
        epsg = model.crs.epsg
        if self._transformer_epsg == epsg:
            return True
        if Transformer is None:
            return False
        self._model_to_geographic = Transformer.from_crs(
            f"EPSG:{epsg}", "EPSG:4326", always_xy=True
        )
        self._geographic_to_model = Transformer.from_crs(
            "EPSG:4326", f"EPSG:{epsg}", always_xy=True
        )
        self._transformer_epsg = epsg
        return True

    def _scene_to_lonlat(self, x: float, y: float) -> tuple[float, float] | None:
        if not self._ensure_geographic_transformers():
            return None
        lon, lat = self._model_to_geographic.transform(float(x), -float(y))
        if not math.isfinite(lon) or not math.isfinite(lat):
            return None
        return float(lon), float(lat)

    def _lonlat_to_scene(self, lon: float, lat: float) -> QPointF | None:
        batch = self._lonlat_to_scene_batch((lon,), (lat,))
        if batch is None:
            return None
        return QPointF(batch[0][0], batch[1][0])

    def _lonlat_to_scene_batch(
        self,
        longitudes: Iterable[float],
        latitudes: Iterable[float],
    ) -> tuple[list[float], list[float]] | None:
        if not self._ensure_geographic_transformers():
            return None
        xs, ys = self._geographic_to_model.transform(
            list(longitudes), list(latitudes)
        )
        return [float(x) for x in xs], [-float(y) for y in ys]

    @property
    def model(self) -> CircuitModel | None:
        return self._model

    @property
    def line_model(self) -> LineNetworkModel | None:
        return self._line_model

    @property
    def load_model(self) -> LoadModel | None:
        return self._load_model

    @property
    def bars_visible(self) -> bool:
        return self._bars_visible

    @property
    def maximum_zoom_scale(self) -> float:
        """Maior escala útil que também preserva a faixa inteira do Qt."""

        rect = self.sceneRect().normalized()
        largest_coordinate = max(
            abs(rect.left()),
            abs(rect.right()),
            abs(rect.top()),
            abs(rect.bottom()),
            1.0,
        )
        safe_scale = (
            QT_SCROLLBAR_COORDINATE_LIMIT
            * QT_SCROLLBAR_SAFETY_FACTOR
            / largest_coordinate
        )
        return max(MIN_ZOOM_SCALE, min(MAX_USEFUL_ZOOM_SCALE, safe_scale))

    def set_model(self, model: CircuitModel | None) -> None:
        self._model = model
        self._reset_geographic_transformers()
        self._satellite_last_frame = None
        self._satellite_prefetch_timer.stop()
        self._bar_visibility_mask = None
        if self._line_model is not None and self._line_model.bars is not model:
            self._line_model = None
            self._segment_visibility_mask = None
        if self._load_model is not None and self._load_model.bars is not model:
            self._load_model = None
        if (
            self._generator_model is not None
            and self._generator_model.bars is not model
        ):
            self._generator_model = None
        if (
            self._equivalent_load_model is not None
            and self._equivalent_load_model.bars is not model
        ):
            self._equivalent_load_model = None
        if model is None:
            self.setSceneRect(QRectF(-500.0, -500.0, 1_000.0, 1_000.0))
            self.viewport().update()
            return
        bounds = model.bounds
        width = max(bounds.width, 100.0)
        height = max(bounds.height, 100.0)
        content = QRectF(bounds.left, -bounds.bottom, width, height)
        margin_x = max(width, 500.0)
        margin_y = max(height, 500.0)
        self.setSceneRect(content.adjusted(-margin_x, -margin_y, margin_x, margin_y))
        self.viewport().update()

    def set_line_model(self, model: LineNetworkModel | None) -> None:
        if model is not None and model.bars is not self._model:
            raise ValueError("Os trechos devem referenciar as barras exibidas na view.")
        self._line_model = model
        self._segment_visibility_mask = None

    def set_load_model(self, model: LoadModel | None) -> None:
        if model is not None and model.bars is not self._model:
            raise ValueError("As cargas devem referenciar as barras exibidas na view.")
        self._load_model = model

    def set_load_layer(self, layer: LoadVirtualizer | None) -> None:
        self._load_layer = layer

    def set_generator_model(self, model: GeneratorModel | None) -> None:
        if model is not None and model.bars is not self._model:
            raise ValueError("Os geradores devem referenciar as barras exibidas na view.")
        self._generator_model = model

    def set_generator_layer(self, layer: LoadVirtualizer | None) -> None:
        self._generator_layer = layer

    def set_equivalent_load_model(
        self,
        model: EquivalentNetworkModel | None,
    ) -> None:
        if model is not None and model.bars is not self._model:
            raise ValueError(
                "As cargas equivalentes devem referenciar as barras exibidas na view."
            )
        self._equivalent_load_model = model

    def set_equivalent_load_layer(self, layer: LoadVirtualizer | None) -> None:
        self._equivalent_load_layer = layer

    def set_feature_visibility_masks(
        self,
        bar_mask: BoolArray | None,
        segment_mask: BoolArray | None,
    ) -> None:
        self._bar_visibility_mask = (
            None
            if self._model is None
            else _visibility_mask(bar_mask, len(self._model), "barras").copy()
        )
        self._segment_visibility_mask = (
            None
            if self._line_model is None
            else _visibility_mask(
                segment_mask, len(self._line_model), "trechos"
            ).copy()
        )

    def set_bars_visible(self, visible: bool) -> None:
        self._bars_visible = bool(visible)

    def set_interaction_mode(self, mode: str) -> None:
        if mode not in {"select", "pan"}:
            raise ValueError(f"Modo de interação desconhecido: {mode}")
        self._interaction_mode = mode
        self._apply_cursor()

    def _apply_cursor(self) -> None:
        cursor = (
            Qt.CursorShape.OpenHandCursor
            if self._interaction_mode == "pan"
            else Qt.CursorShape.ArrowCursor
        )
        self.viewport().setCursor(cursor)

    def fit_model(self) -> None:
        if self._model is None:
            return
        bounds = self._model.bounds
        width = max(bounds.width, 10.0)
        height = max(bounds.height, 10.0)
        rect = QRectF(bounds.left, -bounds.bottom, width, height)
        pad_x = max(width * 0.05, 5.0)
        pad_y = max(height * 0.05, 5.0)
        padded_rect = rect.adjusted(-pad_x, -pad_y, pad_x, pad_y)
        self.fitInView(
            padded_rect,
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self._cap_current_scale(padded_rect.center())
        self.viewport().update()
        self.viewportChanged.emit()

    def fit_visible_features(
        self,
        bar_mask: BoolArray | None,
        segment_mask: BoolArray | None,
    ) -> None:
        """Enquadra apenas a projeção atualmente visível da rede."""

        if self._model is None:
            return
        indices: list[np.ndarray] = []
        if bar_mask is not None:
            values = _visibility_mask(bar_mask, len(self._model), "barras")
            indices.append(np.flatnonzero(values).astype(np.intp, copy=False))
        if self._line_model is not None and segment_mask is not None:
            values = _visibility_mask(
                segment_mask,
                len(self._line_model),
                "trechos",
            )
            segments = np.flatnonzero(values).astype(np.intp, copy=False)
            if segments.size:
                indices.extend(
                    (
                        self._line_model.start_indices[segments],
                        self._line_model.end_indices[segments],
                    )
                )
        if not indices:
            self.fit_model()
            return
        bar_indices = np.unique(np.concatenate(indices))
        if bar_indices.size == 0:
            self.fit_model()
            return
        x_values = self._model.x[bar_indices]
        y_values = -self._model.y[bar_indices]
        left = float(x_values.min())
        right = float(x_values.max())
        top = float(y_values.min())
        bottom = float(y_values.max())
        width = max(right - left, 10.0)
        height = max(bottom - top, 10.0)
        rect = QRectF(left, top, width, height)
        pad_x = max(width * 0.05, 5.0)
        pad_y = max(height * 0.05, 5.0)
        padded = rect.adjusted(-pad_x, -pad_y, pad_x, pad_y)
        self.fitInView(padded, Qt.AspectRatioMode.KeepAspectRatio)
        self._cap_current_scale(padded.center())
        self.viewport().update()
        self.viewportChanged.emit()

    def focus_bar(self, index: int) -> None:
        """Centraliza uma barra mantendo 500 metros de contexto em cada eixo."""

        if self._model is None or not 0 <= int(index) < len(self._model):
            raise IndexError(index)
        point = _scene_point(self._model, int(index))
        self._fit_focus_rect(
            QRectF(point.x() - 250.0, point.y() - 250.0, 500.0, 500.0)
        )

    def focus_segment(self, index: int) -> None:
        """Enquadra um trecho com margem geográfica e zoom máximo previsível."""

        if self._line_model is None or not 0 <= int(index) < len(self._line_model):
            raise IndexError(index)
        segment = int(index)
        bars = self._line_model.bars
        start = _scene_point(bars, int(self._line_model.start_indices[segment]))
        end = _scene_point(bars, int(self._line_model.end_indices[segment]))
        left = min(start.x(), end.x())
        top = min(start.y(), end.y())
        width = abs(start.x() - end.x())
        height = abs(start.y() - end.y())
        padding = max(max(width, height) * 0.2, 50.0)
        self._fit_focus_rect(
            QRectF(
                left - padding,
                top - padding,
                width + padding * 2.0,
                height + padding * 2.0,
            )
        )

    def focus_segments(self, indices: Iterable[int]) -> None:
        """Enquadra um conjunto de trechos com margem e zoom contextual."""

        if self._line_model is None:
            raise ValueError("Não há trechos disponíveis para enquadrar.")
        values = np.fromiter((int(index) for index in indices), dtype=np.intp)
        if values.size == 0:
            raise ValueError("Informe ao menos um trecho para enquadrar.")
        if (values < 0).any() or (values >= len(self._line_model)).any():
            raise IndexError("O enquadramento referencia um trecho inexistente.")
        model = self._line_model
        bars = model.bars
        starts = model.start_indices[values]
        ends = model.end_indices[values]
        x_values = np.concatenate((bars.x[starts], bars.x[ends]))
        y_values = -np.concatenate((bars.y[starts], bars.y[ends]))
        left = float(x_values.min())
        right = float(x_values.max())
        top = float(y_values.min())
        bottom = float(y_values.max())
        width = right - left
        height = bottom - top
        padding = max(max(width, height) * 0.2, 50.0)
        self._fit_focus_rect(
            QRectF(
                left - padding,
                top - padding,
                width + padding * 2.0,
                height + padding * 2.0,
            )
        )

    def focus_load(self, index: int) -> None:
        """Centraliza a barra associada à carga com 500 metros de contexto."""

        if self._load_model is None or not 0 <= int(index) < len(self._load_model):
            raise IndexError(index)
        bar_index = int(self._load_model.bar_indices[int(index)])
        point = _scene_point(self._load_model.bars, bar_index)
        self._fit_focus_rect(
            QRectF(point.x() - 250.0, point.y() - 250.0, 500.0, 500.0)
        )

    def _fit_focus_rect(self, rect: QRectF, *, maximum_scale: float = 4.0) -> None:
        self.fitInView(rect.normalized(), Qt.AspectRatioMode.KeepAspectRatio)
        current_scale = abs(self.transform().m11())
        effective_maximum = min(maximum_scale, self.maximum_zoom_scale)
        if current_scale > effective_maximum:
            self.scale(
                effective_maximum / current_scale,
                effective_maximum / current_scale,
            )
        self.centerOn(rect.center())
        self._zoom_limit_notified = False
        self.viewport().update()
        self.viewportChanged.emit()

    def _cap_current_scale(self, center: QPointF) -> None:
        current_scale = abs(self.transform().m11())
        maximum_scale = self.maximum_zoom_scale
        if current_scale > maximum_scale:
            factor = maximum_scale / current_scale
            self.scale(factor, factor)
            self.centerOn(center)
        self._zoom_limit_notified = False

    def model_point_at(self, viewport_position: QPoint) -> tuple[float, float]:
        point = self.mapToScene(viewport_position)
        return point.x(), -point.y()

    def _start_pan(self, position: QPoint) -> None:
        self._panning = True
        self._pan_last = position
        self._press_pos = position
        self._pan_moved = False
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        position = event.position().toPoint()
        pan_requested = (
            event.button() == Qt.MouseButton.MiddleButton
            or (
                event.button() == Qt.MouseButton.LeftButton
                and (self._interaction_mode == "pan" or self._space_down)
            )
        )
        if pan_requested:
            self._start_pan(position)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = position
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        position = event.position().toPoint()
        x, y = self.model_point_at(position)
        self.mouseCoordinateChanged.emit(x, y)
        if self._panning:
            delta = position - self._pan_last
            if delta.manhattanLength() > 0:
                self._pan_moved = True
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - delta.x()
                )
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta.y()
                )
                self._pan_last = position
                self.viewportChanged.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        position = event.position().toPoint()
        if self._panning:
            self._panning = False
            self._apply_cursor()
            self.viewportChanged.emit()
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._interaction_mode == "select"
            and (position - self._press_pos).manhattanLength() <= 4
        ):
            self._select_nearest(position)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _select_nearest(self, position: QPoint) -> None:
        if self._model is None:
            self.selectionRequested.emit(None)
            return
        x, y = self.model_point_at(position)
        scale = abs(self.transform().m11())
        tolerance = CLICK_TOLERANCE_PX / max(scale, 1e-12)
        load_layers = (
            ("generator", self._generator_layer, self._generator_model),
            (
                "equivalent_load",
                self._equivalent_load_layer,
                self._equivalent_load_model,
            ),
            ("load", self._load_layer, self._load_model),
        )
        for kind, layer, _ in load_layers:
            if layer is None:
                continue
            load_index = layer.hit_test(position, overview=False)
            if load_index is not None:
                self.selectionRequested.emit(FeatureSelection(kind, load_index))
                return
        overview_candidates: list[tuple[float, int, str, int, LoadRenderModel]] = []
        for priority, (kind, layer, model) in enumerate(load_layers):
            if layer is None or model is None:
                continue
            load_index = layer.hit_test(position, overview=True)
            if load_index is None:
                continue
            load_bar_index = int(model.bar_indices[load_index])
            anchor = self.mapFromScene(_scene_point(model.bars, load_bar_index))
            distance = float(
                (position.x() - anchor.x()) ** 2 + (position.y() - anchor.y()) ** 2
            )
            overview_candidates.append(
                (distance, priority, kind, load_index, model)
            )
        overview_candidate = (
            None if not overview_candidates else min(overview_candidates)
        )
        if self._bars_visible:
            bar_index = self._model.spatial_index.nearest(
                x, y, tolerance, self._bar_visibility_mask
            )
            if bar_index is not None:
                if overview_candidate is not None:
                    _, _, load_kind, overview_load_index, overview_model = (
                        overview_candidate
                    )
                    load_bar_index = int(overview_model.bar_indices[overview_load_index])
                    anchor = self.mapFromScene(_scene_point(overview_model.bars, load_bar_index))
                    dx = position.x() - anchor.x()
                    dy = position.y() - anchor.y()
                    center_radius = POINT_DIAMETER_PX / 2.0
                    if dx * dx + dy * dy > center_radius * center_radius:
                        self.selectionRequested.emit(
                            FeatureSelection(load_kind, overview_load_index)
                        )
                        return
                self.selectionRequested.emit(FeatureSelection("bar", bar_index))
                return
        if overview_candidate is not None:
            _, _, load_kind, overview_load_index, _ = overview_candidate
            self.selectionRequested.emit(
                FeatureSelection(load_kind, overview_load_index)
            )
            return
        if self._line_model is not None:
            segment_index = self._line_model.spatial_index.nearest(
                x, y, tolerance, self._segment_visibility_mask
            )
            if segment_index is not None:
                self.selectionRequested.emit(FeatureSelection("segment", segment_index))
                return
        self.selectionRequested.emit(None)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawBackground(painter, rect)
        if self._satellite_enabled:
            self._draw_satellite(painter, rect)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        super().paintEvent(event)
        if self._satellite_enabled and self._tile_manager is not None:
            self._draw_satellite_attribution()
        else:
            self._satellite_attribution_rect = None

    def _draw_satellite_attribution(self) -> None:
        manager = self._tile_manager
        if manager is None:
            return
        text = manager.provedor.atribuicao
        painter = QPainter(self.viewport())
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            font = QFont("Arial", 8)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            viewport = self.viewport().rect()
            width = metrics.horizontalAdvance(text) + 12
            height = metrics.height() + 6
            x = viewport.width() - width - 6
            y = viewport.height() - height - 6
            self._satellite_attribution_rect = QRect(x, y, width, height)
            painter.fillRect(x, y, width, height, QColor(0, 0, 0, 140))
            painter.setPen(QColor("#f0f0f0"))
            painter.drawText(x + 6, y + metrics.ascent() + 3, text)
        finally:
            painter.end()

    def _draw_satellite(self, painter: QPainter, rect: QRectF) -> None:
        manager = self._tile_manager
        if manager is None or self._model is None:
            return
        try:
            corners = (
                self._scene_to_lonlat(rect.left(), rect.top()),
                self._scene_to_lonlat(rect.right(), rect.top()),
                self._scene_to_lonlat(rect.left(), rect.bottom()),
                self._scene_to_lonlat(rect.right(), rect.bottom()),
            )
            if any(corner is None for corner in corners):
                self._notify_satellite_failure(
                    "não foi possível projetar a área visível para coordenadas "
                    "geográficas; confira a zona UTM e a unidade das coordenadas"
                )
                return
            values = [corner for corner in corners if corner is not None]
            longitudes = [corner[0] for corner in values]
            latitudes = [corner[1] for corner in values]
            lon_min, lon_max = min(longitudes), max(longitudes)
            lat_min, lat_max = min(latitudes), max(latitudes)
            lat_middle = (lat_min + lat_max) / 2.0

            zoom = abs(self.transform().m11())
            pixels_per_meter = zoom * self.devicePixelRatioF()
            level = nivel_zoom(
                pixels_per_meter,
                lat_middle,
                z_max=manager.provedor.zoom_max,
            )
            x0, y0 = lonlat_para_tile(lon_min, lat_max, level)
            x1, y1 = lonlat_para_tile(lon_max, lat_min, level)
            x_start, x_end = min(x0, x1), max(x0, x1)
            y_start, y_end = min(y0, y1), max(y0, y1)
            nx = x_end - x_start + 1
            ny = y_end - y_start + 1
            if nx * ny > 400:
                return

            keys = [
                (level, x, y)
                for x in range(x_start, x_end + 1)
                for y in range(y_start, y_end + 1)
            ]
            center = (
                level,
                (x_start + x_end) // 2,
                (y_start + y_end) // 2,
            )
            manager.definir_interesse(keys, center)
            self._satellite_last_frame = (
                level,
                x_start,
                x_end,
                y_start,
                y_end,
            )
            self._satellite_prefetch_timer.start()

            grid_lons, grid_lats = cantos_lonlat_da_faixa(
                x_start, y_start, nx, ny, level
            )
            batch = self._lonlat_to_scene_batch(grid_lons, grid_lats)
            if batch is None:
                return
            grid_x, grid_y = batch

            def grid_point(ix: int, iy: int) -> tuple[float, float]:
                position = iy * (nx + 1) + ix
                return grid_x[position], grid_y[position]

            painter.save()
            try:
                painter.setOpacity(self._satellite_opacity)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                native_size = manager.provedor.tam_tile
                for ix in range(nx):
                    for iy in range(ny):
                        x, y = x_start + ix, y_start + iy
                        top_left = grid_point(ix, iy)
                        top_right = grid_point(ix + 1, iy)
                        bottom_right = grid_point(ix + 1, iy + 1)
                        bottom_left = grid_point(ix, iy + 1)
                        if not all(
                            math.isfinite(value)
                            for point in (
                                top_left,
                                top_right,
                                bottom_right,
                                bottom_left,
                            )
                            for value in point
                        ):
                            continue
                        quad = QPolygonF(
                            [
                                QPointF(*top_left),
                                QPointF(*top_right),
                                QPointF(*bottom_right),
                                QPointF(*bottom_left),
                            ]
                        )
                        pixmap = manager.tile(level, x, y)
                        if pixmap is not None and not pixmap.isNull():
                            scene_width = math.hypot(
                                top_right[0] - top_left[0],
                                top_right[1] - top_left[1],
                            )
                            painter.setRenderHint(
                                QPainter.RenderHint.SmoothPixmapTransform,
                                abs(scene_width * zoom - native_size) > 2.0,
                            )
                            self._draw_pixmap_in_quad(
                                painter,
                                pixmap,
                                QRectF(pixmap.rect()),
                                quad,
                            )
                            continue
                        painter.setRenderHint(
                            QPainter.RenderHint.SmoothPixmapTransform, False
                        )
                        self._draw_fallback_tile(
                            painter, manager, quad, level, x, y
                        )
            finally:
                painter.restore()
        except Exception as exc:
            # Uma indisponibilidade do fundo nunca deve interromper a rede, mas
            # também não pode passar despercebida.
            traceback.print_exc()
            self._notify_satellite_failure(f"falha ao desenhar o fundo: {exc}")

    @staticmethod
    def _draw_pixmap_in_quad(
        painter: QPainter,
        pixmap,
        source: QRectF,
        quad: QPolygonF,
    ) -> bool:
        origin = QPolygonF(
            [
                QPointF(source.left(), source.top()),
                QPointF(source.right(), source.top()),
                QPointF(source.right(), source.bottom()),
                QPointF(source.left(), source.bottom()),
            ]
        )
        transform = QTransform()
        if not QTransform.quadToQuad(origin, quad, transform):
            return False
        painter.save()
        try:
            painter.setTransform(transform, True)
            painter.drawPixmap(source.topLeft(), pixmap, source)
        finally:
            painter.restore()
        return True

    @staticmethod
    def _sub_quad(
        quad: QPolygonF,
        x_fraction: float,
        y_fraction: float,
        size_fraction: float,
    ) -> QPolygonF:
        top_left, top_right, bottom_right, bottom_left = (
            quad[0],
            quad[1],
            quad[2],
            quad[3],
        )

        def point(u: float, v: float) -> QPointF:
            top_x = top_left.x() + (top_right.x() - top_left.x()) * u
            top_y = top_left.y() + (top_right.y() - top_left.y()) * u
            bottom_x = bottom_left.x() + (bottom_right.x() - bottom_left.x()) * u
            bottom_y = bottom_left.y() + (bottom_right.y() - bottom_left.y()) * u
            return QPointF(
                top_x + (bottom_x - top_x) * v,
                top_y + (bottom_y - top_y) * v,
            )

        return QPolygonF(
            [
                point(x_fraction, y_fraction),
                point(x_fraction + size_fraction, y_fraction),
                point(
                    x_fraction + size_fraction,
                    y_fraction + size_fraction,
                ),
                point(x_fraction, y_fraction + size_fraction),
            ]
        )

    @classmethod
    def _draw_fallback_tile(
        cls,
        painter: QPainter,
        manager: GerenciadorTiles,
        quad: QPolygonF,
        level: int,
        x: int,
        y: int,
    ) -> bool:
        for levels in (1, 2):
            parent_level = level - levels
            if parent_level < 0:
                break
            parent_x, parent_y, _ = tile_pai(x, y, level, levels)
            parent = manager.tile_do_cache(parent_level, parent_x, parent_y)
            if parent is None or parent.isNull():
                continue
            fx, fy, fraction = sub_rect_no_pai(
                x, y, level, parent_level
            )
            width, height = parent.width(), parent.height()
            source = QRectF(
                fx * width,
                fy * height,
                fraction * width,
                fraction * height,
            )
            return cls._draw_pixmap_in_quad(painter, parent, source, quad)

        painted = False
        for ix in (0, 1):
            for iy in (0, 1):
                child = manager.tile_do_cache(
                    level + 1,
                    x * 2 + ix,
                    y * 2 + iy,
                )
                if child is None or child.isNull():
                    continue
                child_quad = cls._sub_quad(quad, ix * 0.5, iy * 0.5, 0.5)
                if cls._draw_pixmap_in_quad(
                    painter, child, QRectF(child.rect()), child_quad
                ):
                    painted = True
        return painted

    def _prefetch_neighboring_tiles(self) -> None:
        manager = self._tile_manager
        frame = self._satellite_last_frame
        if manager is None or frame is None or not self._satellite_enabled:
            return
        level, x_start, x_end, y_start, y_end = frame
        width = x_end - x_start + 1
        height = y_end - y_start + 1
        if width * height > 100:
            return
        tile_count = 1 << level
        keys: list[tuple[int, int, int]] = []
        for x_offset in (-width, 0, width):
            for y_offset in (-height, 0, height):
                if x_offset == 0 and y_offset == 0:
                    continue
                for x in range(x_start + x_offset, x_end + x_offset + 1):
                    for y in range(y_start + y_offset, y_end + y_offset + 1):
                        if 0 <= x < tile_count and 0 <= y < tile_count:
                            keys.append((level, x, y))
        manager.prefetch(keys)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        steps = delta / 120.0
        factor = math.pow(1.15, steps)
        self.zoom_at(event.position().toPoint(), factor)
        event.accept()

    def zoom_at(self, viewport_position: QPoint, factor: float) -> None:
        """Aplica zoom mantendo imóvel o ponto de cena sob o cursor."""

        if factor <= 0 or not math.isfinite(factor):
            raise ValueError("O fator de zoom deve ser finito e positivo.")
        current_scale = abs(self.transform().m11())
        requested_scale = current_scale * factor
        maximum_scale = self.maximum_zoom_scale
        target_scale = min(max(requested_scale, MIN_ZOOM_SCALE), maximum_scale)
        reached_maximum = requested_scale > maximum_scale
        if reached_maximum:
            if not self._zoom_limit_notified:
                self._zoom_limit_notified = True
                self.zoomLimitReached.emit()
        elif target_scale < maximum_scale:
            self._zoom_limit_notified = False

        if math.isclose(target_scale, current_scale, rel_tol=1e-12, abs_tol=0.0):
            self.viewport().update()
            return

        effective_factor = target_scale / current_scale
        scene_before = self.mapToScene(viewport_position)
        self.scale(effective_factor, effective_factor)
        scene_after = self.mapToScene(viewport_position)
        correction = scene_after - scene_before
        self.translate(correction.x(), correction.y())
        self.viewport().update()
        self.viewportChanged.emit()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            self._apply_cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self.viewportChanged.emit()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        self.viewportChanged.emit()


class ItemVirtualizer(QObject):
    """Materializa apenas as barras próximas da viewport."""

    modeChanged = pyqtSignal(str)
    countsChanged = pyqtSignal(int)

    def __init__(
        self,
        scene: QGraphicsScene,
        view: DiagramView,
        *,
        max_active_items: int = MAX_ACTIVE_ITEMS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.scene = scene
        self.view = view
        self.max_active_items = max_active_items
        self.model: CircuitModel | None = None
        self.overview_item: BarsOverviewItem | None = None
        self._bars_visible = True
        self._visibility_mask: BoolArray | None = None
        self.selection_overlay = SelectionOverlayItem()
        self.scene.addItem(self.selection_overlay)

        self._active: dict[int, BarraItem] = {}
        self._pool: list[BarraItem] = []
        self._loaded_rect: QRectF | None = None
        self._last_view_rect: QRectF | None = None
        self._selected_index: int | None = None
        self._reveal_hidden_selection = False
        self._pending_indices: list[int] = []
        self._mode = "Visão geral"

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(VIRTUALIZATION_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self.refresh)

        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._materialize_next_batch)
        self.view.viewportChanged.connect(self.schedule_refresh)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def bars_visible(self) -> bool:
        return self._bars_visible

    def reset_model(self, model: CircuitModel | None) -> None:
        self._refresh_timer.stop()
        self._batch_timer.stop()
        self._pending_indices.clear()
        self._clear_active()
        if self.overview_item is not None:
            self.scene.removeItem(self.overview_item)
            self.overview_item = None

        self.model = model
        self._visibility_mask = (
            None if model is None else np.ones(len(model), dtype=np.bool_)
        )
        self._loaded_rect = None
        self._last_view_rect = None
        self._selected_index = None
        self._reveal_hidden_selection = False
        self.selection_overlay.setVisible(False)
        if model is not None:
            self.overview_item = BarsOverviewItem(model)
            self.scene.addItem(self.overview_item)
            self.overview_item.setVisible(self._bars_visible)
        self._set_mode("Visão geral")
        self.countsChanged.emit(0)

    def schedule_refresh(self) -> None:
        if self.model is not None and self._bars_visible:
            self._refresh_timer.start()

    def refresh(self, force: bool = False) -> None:
        if (
            not self._bars_visible
            or self.model is None
            or self.overview_item is None
        ):
            return
        viewport_rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        if not force and self._can_reuse_loaded_rect(viewport_rect):
            return

        margin_x = viewport_rect.width() * VIRTUALIZATION_MARGIN
        margin_y = viewport_rect.height() * VIRTUALIZATION_MARGIN
        loaded_rect = viewport_rect.adjusted(-margin_x, -margin_y, margin_x, margin_y)
        indices = self.model.spatial_index.query_rect(_model_bounds_from_scene(loaded_rect))
        if self._visibility_mask is not None:
            indices = indices[self._visibility_mask[indices]]

        self._loaded_rect = loaded_rect
        self._last_view_rect = viewport_rect
        self._pending_indices.clear()
        self._batch_timer.stop()

        if indices.size > self.max_active_items:
            self._show_overview()
            return

        desired = {int(index) for index in indices}
        blocker = QSignalBlocker(self.scene)
        try:
            for index in tuple(self._active):
                if index not in desired:
                    self._release_item(index)
                else:
                    self._active[index].setVisible(True)
        finally:
            del blocker

        self._pending_indices = [index for index in desired if index not in self._active]
        if self._pending_indices:
            # O agregado continua visível até o último lote, evitando um quadro vazio.
            self.overview_item.setVisible(True)
            self._set_mode("Detalhado")
            self._batch_timer.start(0)
        else:
            self.overview_item.setVisible(False)
            self._set_mode("Detalhado")
            self._sync_selection()
            self.countsChanged.emit(self.active_count)

    def _can_reuse_loaded_rect(self, viewport_rect: QRectF) -> bool:
        if self._loaded_rect is None or self._last_view_rect is None:
            return False
        inside = self._loaded_rect.contains(viewport_rect)
        zoomed_in_far = viewport_rect.width() < self._last_view_rect.width() * 0.5
        zoomed_out = (
            viewport_rect.width() > self._last_view_rect.width() * (1.0 + 1e-9)
            or viewport_rect.height()
            > self._last_view_rect.height() * (1.0 + 1e-9)
        )
        return inside and not zoomed_in_far and not zoomed_out

    def _materialize_next_batch(self) -> None:
        if self.model is None or not self._bars_visible:
            self._pending_indices.clear()
            return
        batch = self._pending_indices[:MATERIALIZE_BATCH_SIZE]
        del self._pending_indices[:MATERIALIZE_BATCH_SIZE]
        blocker = QSignalBlocker(self.scene)
        try:
            for index in batch:
                item = self._acquire_item()
                item.bind(self.model, index)
                self._active[index] = item
        finally:
            del blocker

        self.countsChanged.emit(self.active_count)
        self._sync_selection()
        if self._pending_indices:
            self._batch_timer.start(0)
        elif self.overview_item is not None:
            self.overview_item.setVisible(False)
            self.view.viewport().update()

    def _acquire_item(self) -> BarraItem:
        if self._pool:
            item = self._pool.pop()
            self.scene.addItem(item)
            return item
        item = BarraItem()
        self.scene.addItem(item)
        return item

    def _release_item(self, index: int) -> None:
        item = self._active.pop(index)
        self.scene.removeItem(item)
        item.unbind()
        if len(self._pool) < MAX_POOL_SIZE:
            self._pool.append(item)
        else:
            item.deleteLater()

    def _clear_active(self) -> None:
        blocker = QSignalBlocker(self.scene)
        try:
            for index in tuple(self._active):
                self._release_item(index)
        finally:
            del blocker

    def _show_overview(self) -> None:
        self._clear_active()
        if self.overview_item is not None:
            self.overview_item.setVisible(self._bars_visible)
        self._set_mode("Visão geral")
        self._sync_selection()
        self.countsChanged.emit(0)
        self.view.viewport().update()

    def set_selected_index(
        self,
        index: int | None,
        *,
        reveal_hidden: bool = False,
    ) -> None:
        previous = self._selected_index
        self._selected_index = index
        self._reveal_hidden_selection = index is not None and bool(reveal_hidden)
        if previous is not None and previous in self._active:
            self._active[previous].setSelected(False)
        self._sync_selection()

    def set_bars_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._bars_visible:
            return
        self._bars_visible = visible
        self._refresh_timer.stop()
        self._batch_timer.stop()
        self._pending_indices.clear()
        if not visible:
            if self.overview_item is not None:
                self.overview_item.setVisible(False)
            for item in self._active.values():
                item.setVisible(False)
            self._sync_selection()
            self.view.viewport().update()
            return

        self._loaded_rect = None
        self._last_view_rect = None
        self.refresh(force=True)
        self.view.viewport().update()

    def set_visibility_mask(self, mask: BoolArray | None) -> None:
        if self.model is None:
            if mask is not None:
                raise ValueError("Não há modelo de barras para receber a máscara.")
            self._visibility_mask = None
            return
        values = _visibility_mask(mask, len(self.model), "barras")
        if (
            self._visibility_mask is not None
            and np.array_equal(values, self._visibility_mask)
        ):
            return
        self._visibility_mask = values.copy()
        if self.overview_item is not None:
            self.overview_item.set_visibility_mask(values)
        self._refresh_timer.stop()
        self._batch_timer.stop()
        self._pending_indices.clear()
        blocker = QSignalBlocker(self.scene)
        try:
            for index in tuple(self._active):
                if not values[index]:
                    self._release_item(index)
        finally:
            del blocker
        self._loaded_rect = None
        self._last_view_rect = None
        self._sync_selection()
        self.countsChanged.emit(self.active_count)
        if self._bars_visible:
            self.refresh(force=True)
        else:
            self.view.viewport().update()

    def _sync_selection(self) -> None:
        if self.model is None or self._selected_index is None:
            self.selection_overlay.setVisible(False)
            return
        index = self._selected_index
        if not 0 <= index < len(self.model):
            self.selection_overlay.setVisible(False)
            return
        hidden = not self._bars_visible or (
            self._visibility_mask is not None and not self._visibility_mask[index]
        )
        if hidden:
            active_item = self._active.get(index)
            if active_item is not None:
                active_item.setSelected(False)
            if self._reveal_hidden_selection:
                self.selection_overlay.setPos(_scene_point(self.model, index))
                self.selection_overlay.setVisible(True)
            else:
                self.selection_overlay.setVisible(False)
            return
        active_item = self._active.get(index)
        if active_item is not None:
            active_item.setSelected(True)
            self.selection_overlay.setVisible(False)
        else:
            self.selection_overlay.setPos(_scene_point(self.model, index))
            self.selection_overlay.setVisible(True)

    def _set_mode(self, mode: str) -> None:
        if self._mode == mode:
            return
        self._mode = mode
        self.modeChanged.emit(mode)

    def active_indices(self) -> Iterable[int]:
        return self._active.keys()


class LoadVirtualizer(QObject):
    """Camada híbrida que materializa somente as cargas próximas da viewport."""

    countsChanged = pyqtSignal(int)

    def __init__(
        self,
        scene: QGraphicsScene,
        view: DiagramView,
        *,
        max_active_items: int = MAX_ACTIVE_ITEMS,
        symbol_kind: str = "load",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.scene = scene
        self.view = view
        self.max_active_items = max_active_items
        if symbol_kind not in {"load", "generator"}:
            raise ValueError(f"Símbolo associado desconhecido: {symbol_kind}")
        self.symbol_kind = symbol_kind
        self.model: LoadRenderModel | None = None
        self.overview_item: LoadsOverviewItem | None = None
        self._loads_visible = True
        self._visibility_mask: BoolArray | None = None
        self._x_offsets = np.empty(0, dtype=np.float64)
        self._y_offsets = np.empty(0, dtype=np.float64)
        self.selection_overlay = LoadSelectionOverlayItem(symbol_kind=symbol_kind)
        self.scene.addItem(self.selection_overlay)

        self._active: dict[int, LoadItem] = {}
        self._pool: list[LoadItem] = []
        self._loaded_rect: QRectF | None = None
        self._last_view_rect: QRectF | None = None
        self._selected_index: int | None = None
        self._reveal_hidden_selection = False
        self._pending_indices: list[int] = []
        self._mode = "Visão geral"

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(VIRTUALIZATION_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self.refresh)
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._materialize_next_batch)
        self.view.viewportChanged.connect(self.schedule_refresh)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def loads_visible(self) -> bool:
        return self._loads_visible

    @property
    def layout_offsets(self) -> tuple[np.ndarray, np.ndarray]:
        return self._x_offsets, self._y_offsets

    def reset_model(self, model: LoadRenderModel | None) -> None:
        self._refresh_timer.stop()
        self._batch_timer.stop()
        self._pending_indices.clear()
        self._clear_active()
        if self.overview_item is not None:
            self.scene.removeItem(self.overview_item)
            self.overview_item = None
        self.model = model
        self._visibility_mask = (
            None if model is None else np.ones(len(model), dtype=np.bool_)
        )
        if model is None:
            self._x_offsets = np.empty(0, dtype=np.float64)
            self._y_offsets = np.empty(0, dtype=np.float64)
        else:
            self._x_offsets, self._y_offsets = _load_layout_offsets(model)
        self._loaded_rect = None
        self._last_view_rect = None
        self._selected_index = None
        self._reveal_hidden_selection = False
        self.selection_overlay.unbind()
        if model is not None:
            self.overview_item = LoadsOverviewItem(
                model,
                symbol_kind=self.symbol_kind,
            )
            self.scene.addItem(self.overview_item)
            self.overview_item.setVisible(self._loads_visible)
        self._mode = "Visão geral"
        self.countsChanged.emit(0)

    def set_layout_offsets(
        self,
        x_offsets: Sequence[float] | np.ndarray,
        y_offsets: Sequence[float] | np.ndarray,
    ) -> None:
        if self.model is None:
            raise ValueError("Não há modelo de cargas para receber o layout.")
        x_values = np.ascontiguousarray(x_offsets, dtype=np.float64)
        y_values = np.ascontiguousarray(y_offsets, dtype=np.float64)
        if (
            x_values.ndim != 1
            or y_values.ndim != 1
            or x_values.size != len(self.model)
            or y_values.size != len(self.model)
        ):
            raise ValueError("O layout deve corresponder às cargas do modelo.")
        if not np.isfinite(x_values).all() or not np.isfinite(y_values).all():
            raise ValueError("O layout das cargas deve usar posições finitas.")
        x_values.setflags(write=False)
        y_values.setflags(write=False)
        self._x_offsets = x_values
        self._y_offsets = y_values
        if self.overview_item is not None:
            self.overview_item.set_layout_offsets(x_values, y_values)
        for index, item in self._active.items():
            item.bind(
                self.model,
                index,
                float(self._x_offsets[index]),
                float(self._y_offsets[index]),
            )
        self._sync_selection()
        self.view.viewport().update()

    def schedule_refresh(self) -> None:
        if self.model is not None and self._loads_visible:
            self._refresh_timer.start()

    def refresh(self, force: bool = False) -> None:
        if not self._loads_visible or self.model is None or self.overview_item is None:
            return
        viewport_rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        if not force and self._can_reuse_loaded_rect(viewport_rect):
            return
        margin_x = viewport_rect.width() * VIRTUALIZATION_MARGIN
        margin_y = viewport_rect.height() * VIRTUALIZATION_MARGIN
        loaded_rect = viewport_rect.adjusted(-margin_x, -margin_y, margin_x, margin_y)
        indices = self.model.spatial_index.query_rect(_model_bounds_from_scene(loaded_rect))
        if self._visibility_mask is not None:
            indices = indices[self._visibility_mask[indices]]
        self._loaded_rect = loaded_rect
        self._last_view_rect = viewport_rect
        self._pending_indices.clear()
        self._batch_timer.stop()
        if indices.size > self.max_active_items:
            self._show_overview()
            return

        desired = {int(index) for index in indices}
        blocker = QSignalBlocker(self.scene)
        try:
            for index in tuple(self._active):
                if index not in desired:
                    self._release_item(index)
                else:
                    self._active[index].setVisible(True)
        finally:
            del blocker
        self._pending_indices = [index for index in desired if index not in self._active]
        self._mode = "Detalhado"
        if self._pending_indices:
            self.overview_item.setVisible(True)
            self._batch_timer.start(0)
        else:
            self.overview_item.setVisible(False)
            self._sync_selection()
            self.countsChanged.emit(self.active_count)

    def _can_reuse_loaded_rect(self, viewport_rect: QRectF) -> bool:
        if self._loaded_rect is None or self._last_view_rect is None:
            return False
        inside = self._loaded_rect.contains(viewport_rect)
        zoomed_in_far = viewport_rect.width() < self._last_view_rect.width() * 0.5
        zoomed_out = (
            viewport_rect.width() > self._last_view_rect.width() * (1.0 + 1e-9)
            or viewport_rect.height()
            > self._last_view_rect.height() * (1.0 + 1e-9)
        )
        return inside and not zoomed_in_far and not zoomed_out

    def _materialize_next_batch(self) -> None:
        if self.model is None or not self._loads_visible:
            self._pending_indices.clear()
            return
        batch = self._pending_indices[:MATERIALIZE_BATCH_SIZE]
        del self._pending_indices[:MATERIALIZE_BATCH_SIZE]
        blocker = QSignalBlocker(self.scene)
        try:
            for index in batch:
                item = self._acquire_item()
                item.bind(
                    self.model,
                    index,
                    float(self._x_offsets[index]),
                    float(self._y_offsets[index]),
                )
                self._active[index] = item
        finally:
            del blocker
        self.countsChanged.emit(self.active_count)
        self._sync_selection()
        if self._pending_indices:
            self._batch_timer.start(0)
        elif self.overview_item is not None:
            self.overview_item.setVisible(False)
            self.view.viewport().update()

    def _acquire_item(self) -> LoadItem:
        if self._pool:
            item = self._pool.pop()
            self.scene.addItem(item)
            return item
        item = LoadItem(symbol_kind=self.symbol_kind)
        self.scene.addItem(item)
        return item

    def _release_item(self, index: int) -> None:
        item = self._active.pop(index)
        self.scene.removeItem(item)
        item.unbind()
        if len(self._pool) < MAX_POOL_SIZE:
            self._pool.append(item)
        else:
            item.deleteLater()

    def _clear_active(self) -> None:
        blocker = QSignalBlocker(self.scene)
        try:
            for index in tuple(self._active):
                self._release_item(index)
        finally:
            del blocker

    def _show_overview(self) -> None:
        self._clear_active()
        if self.overview_item is not None:
            self.overview_item.setVisible(self._loads_visible)
        self._mode = "Visão geral"
        self._sync_selection()
        self.countsChanged.emit(0)
        self.view.viewport().update()

    def set_selected_index(
        self,
        index: int | None,
        *,
        reveal_hidden: bool = False,
    ) -> None:
        previous = self._selected_index
        self._selected_index = index
        self._reveal_hidden_selection = index is not None and bool(reveal_hidden)
        if previous is not None and previous in self._active:
            self._active[previous].setSelected(False)
        self._sync_selection()

    def set_loads_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._loads_visible:
            return
        self._loads_visible = visible
        self._refresh_timer.stop()
        self._batch_timer.stop()
        self._pending_indices.clear()
        if not visible:
            if self.overview_item is not None:
                self.overview_item.setVisible(False)
            for item in self._active.values():
                item.setVisible(False)
            self._sync_selection()
            self.view.viewport().update()
            return
        self._loaded_rect = None
        self._last_view_rect = None
        self.refresh(force=True)
        self.view.viewport().update()

    def set_visibility_mask(self, mask: BoolArray | None) -> None:
        if self.model is None:
            if mask is not None:
                raise ValueError("Não há modelo de cargas para receber a máscara.")
            self._visibility_mask = None
            return
        values = _visibility_mask(mask, len(self.model), "cargas")
        if self._visibility_mask is not None and np.array_equal(
            values, self._visibility_mask
        ):
            return
        self._visibility_mask = values.copy()
        if self.overview_item is not None:
            self.overview_item.set_visibility_mask(values)
        self._refresh_timer.stop()
        self._batch_timer.stop()
        self._pending_indices.clear()
        blocker = QSignalBlocker(self.scene)
        try:
            for index in tuple(self._active):
                if not values[index]:
                    self._release_item(index)
        finally:
            del blocker
        self._loaded_rect = None
        self._last_view_rect = None
        self._sync_selection()
        self.countsChanged.emit(self.active_count)
        if self._loads_visible:
            self.refresh(force=True)
        else:
            self.view.viewport().update()

    def _sync_selection(self) -> None:
        if self.model is None or self._selected_index is None:
            self.selection_overlay.unbind()
            return
        index = self._selected_index
        if not 0 <= index < len(self.model):
            self.selection_overlay.unbind()
            return
        hidden = not self._loads_visible or (
            self._visibility_mask is not None and not self._visibility_mask[index]
        )
        active_item = self._active.get(index)
        if hidden:
            if active_item is not None:
                active_item.setSelected(False)
            if not self._reveal_hidden_selection:
                self.selection_overlay.unbind()
                return
        elif active_item is not None:
            active_item.setSelected(True)
            self.selection_overlay.unbind()
            return
        self.selection_overlay.bind(
            self.model,
            index,
            float(self._x_offsets[index]),
            float(self._y_offsets[index]),
        )
        self.selection_overlay.setVisible(True)

    def hit_test(self, position: QPoint, *, overview: bool) -> int | None:
        if self.model is None or not self._loads_visible:
            return None
        if not overview:
            candidates: list[tuple[float, int]] = []
            for index, item in self._active.items():
                if not item.isVisible():
                    continue
                bar_index = int(self.model.bar_indices[index])
                anchor = self.view.mapFromScene(
                    _scene_point(self.model.bars, bar_index)
                )
                local = QPointF(
                    float(position.x() - anchor.x()),
                    float(position.y() - anchor.y()),
                )
                contains = item.symbol_rect.contains(local)
                if contains and self.symbol_kind == "generator":
                    center = item.symbol_rect.center()
                    radius = item.symbol_rect.width() / 2.0
                    contains = (
                        (local.x() - center.x()) ** 2
                        + (local.y() - center.y()) ** 2
                        <= radius * radius
                    )
                if contains:
                    center = item.symbol_rect.center()
                    distance = (local.x() - center.x()) ** 2 + (
                        local.y() - center.y()
                    ) ** 2
                    candidates.append((distance, index))
            return None if not candidates else min(candidates)[1]
        if self.overview_item is None or not self.overview_item.isVisible():
            return None
        x, y = self.view.model_point_at(position)
        scale = abs(self.view.transform().m11())
        if not self.overview_item.layout_applied:
            tolerance = (LOAD_OVERVIEW_DIAMETER_PX / 2.0 + 2.0) / max(
                scale, 1e-12
            )
            return self.model.spatial_index.nearest(
                x, y, tolerance, self._visibility_mask
            )
        radius = max(LOAD_WIDTH_PX, GENERATOR_DIAMETER_PX) / 2.0
        max_offset = radius
        if self._x_offsets.size:
            max_offset += float(
                np.max(
                    np.hypot(self._x_offsets, self._y_offsets)
                )
            )
        tolerance = (max_offset + 2.0) / max(scale, 1e-12)
        candidates = self.model.spatial_index.query_rect(
            Bounds(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
        )
        if self._visibility_mask is not None:
            candidates = candidates[self._visibility_mask[candidates]]
        hits: list[tuple[float, int]] = []
        for candidate in candidates:
            index = int(candidate)
            bar_index = int(self.model.bar_indices[index])
            anchor = self.view.mapFromScene(
                _scene_point(self.model.bars, bar_index)
            )
            local_x = float(position.x() - anchor.x())
            local_y = float(position.y() - anchor.y())
            rect = (
                QRectF(
                    float(self._x_offsets[index]) - GENERATOR_DIAMETER_PX / 2.0,
                    float(self._y_offsets[index]),
                    GENERATOR_DIAMETER_PX,
                    GENERATOR_DIAMETER_PX,
                )
                if self.symbol_kind == "generator"
                else _load_rect(
                    float(self._x_offsets[index]),
                    float(self._y_offsets[index]),
                )
            )
            if not rect.contains(QPointF(local_x, local_y)):
                continue
            center = rect.center()
            distance = (local_x - center.x()) ** 2 + (local_y - center.y()) ** 2
            if self.symbol_kind == "generator" and distance > (
                GENERATOR_DIAMETER_PX / 2.0
            ) ** 2:
                continue
            hits.append((distance, index))
        return None if not hits else min(hits)[1]

    def active_indices(self) -> Iterable[int]:
        return self._active.keys()
