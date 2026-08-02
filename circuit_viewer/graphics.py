"""Canvas híbrido: visão agregada e itens individuais virtualizados."""

from __future__ import annotations

import math
from collections.abc import Iterable

from PyQt6.QtCore import (
    QLineF,
    QObject,
    QPoint,
    QPointF,
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
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
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

from .model import Bounds, CircuitModel, FeatureSelection, LineNetworkModel, SwitchModel


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

POINT_COLOR = QColor("#202020")
SELECTED_COLOR = QColor("#ffcc00")
SELECTED_OUTLINE = QColor("#7a5a00")
CANVAS_BACKGROUND = QColor("#f7f7f7")
LINE_COLOR = QColor("#555555")
SWITCH_COLOR = QColor("#ff0000")
SEGMENT_SELECTION_WIDTH_PX = 3.0


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


class BarsOverviewItem(QGraphicsItem):
    """Representação de todas as barras em uma única operação de pintura."""

    def __init__(self, model: CircuitModel) -> None:
        super().__init__()
        self._model = model
        self._points = QPolygonF(
            [QPointF(float(x), -float(y)) for x, y in zip(model.x, model.y, strict=True)]
        )
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
        # O conteúdo é imutável. Em pan e repaints subsequentes, o Qt reaproveita
        # a rasterização em vez de percorrer novamente todos os pontos.
        self.setCacheMode(
            QGraphicsItem.CacheMode.DeviceCoordinateCache,
            QSize(4_096, 4_096),
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


class LineNetworkItem(QGraphicsItem):
    """Camada agregada e imutável de trechos da rede.

    Todos os segmentos são compilados uma vez em subcaminhos desconectados de um
    único ``QPainterPath``. Assim, cada quadro exige somente um ``drawPath``.
    """

    def __init__(self, model: LineNetworkModel) -> None:
        super().__init__()
        self._model = model
        path = QPainterPath()
        bars = model.bars
        for start_index, end_index in zip(
            model.start_indices,
            model.end_indices,
            strict=True,
        ):
            start = int(start_index)
            end = int(end_index)
            path.moveTo(float(bars.x[start]), -float(bars.y[start]))
            path.lineTo(float(bars.x[end]), -float(bars.y[end]))
        self._path = path

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

    @property
    def segment_count(self) -> int:
        return len(self._model)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(LINE_COLOR)
        pen.setWidthF(1.0)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)
        painter.restore()


class SwitchNetworkItem(QGraphicsItem):
    """Simbologia agregada dos trechos classificados como chaves."""

    def __init__(self, model: SwitchModel) -> None:
        super().__init__()
        self._model = model
        path = QPainterPath()
        segments = model.segments
        bars = segments.bars
        for segment_index in model.segment_indices:
            index = int(segment_index)
            start = int(segments.start_indices[index])
            end = int(segments.end_indices[index])
            path.moveTo(float(bars.x[start]), -float(bars.y[start]))
            path.lineTo(float(bars.x[end]), -float(bars.y[end]))
        self._path = path
        path_bounds = path.boundingRect()
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

    @property
    def switch_count(self) -> int:
        return len(self._model)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(SWITCH_COLOR)
        pen.setWidthF(1.0)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)
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


class DiagramView(QGraphicsView):
    """View geográfica com navegação e seleção indexada."""

    viewportChanged = pyqtSignal()
    selectionRequested = pyqtSignal(object)
    mouseCoordinateChanged = pyqtSignal(float, float)

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:  # noqa: ANN001
        super().__init__(scene, parent)
        self._model: CircuitModel | None = None
        self._line_model: LineNetworkModel | None = None
        self._bars_visible = True
        self._interaction_mode = "select"
        self._space_down = False
        self._panning = False
        self._pan_last = QPoint()
        self._press_pos = QPoint()
        self._pan_moved = False

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
    def model(self) -> CircuitModel | None:
        return self._model

    @property
    def line_model(self) -> LineNetworkModel | None:
        return self._line_model

    @property
    def bars_visible(self) -> bool:
        return self._bars_visible

    def set_model(self, model: CircuitModel | None) -> None:
        self._model = model
        if self._line_model is not None and self._line_model.bars is not model:
            self._line_model = None
        if model is None:
            self.setSceneRect(QRectF(-500.0, -500.0, 1_000.0, 1_000.0))
            return
        bounds = model.bounds
        width = max(bounds.width, 100.0)
        height = max(bounds.height, 100.0)
        content = QRectF(bounds.left, -bounds.bottom, width, height)
        margin_x = max(width, 500.0)
        margin_y = max(height, 500.0)
        self.setSceneRect(content.adjusted(-margin_x, -margin_y, margin_x, margin_y))

    def set_line_model(self, model: LineNetworkModel | None) -> None:
        if model is not None and model.bars is not self._model:
            raise ValueError("Os trechos devem referenciar as barras exibidas na view.")
        self._line_model = model

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
        self.fitInView(
            rect.adjusted(-pad_x, -pad_y, pad_x, pad_y),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.viewportChanged.emit()

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
        if self._bars_visible:
            bar_index = self._model.spatial_index.nearest(x, y, tolerance)
            if bar_index is not None:
                self.selectionRequested.emit(FeatureSelection("bar", bar_index))
                return
        if self._line_model is not None:
            segment_index = self._line_model.spatial_index.nearest(x, y, tolerance)
            if segment_index is not None:
                self.selectionRequested.emit(FeatureSelection("segment", segment_index))
                return
        self.selectionRequested.emit(None)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        steps = delta / 120.0
        factor = math.pow(1.15, steps)
        current_scale = abs(self.transform().m11())
        next_scale = current_scale * factor
        if not 1e-8 <= next_scale <= 1e6:
            event.accept()
            return

        self.zoom_at(event.position().toPoint(), factor)
        event.accept()

    def zoom_at(self, viewport_position: QPoint, factor: float) -> None:
        """Aplica zoom mantendo imóvel o ponto de cena sob o cursor."""

        if factor <= 0 or not math.isfinite(factor):
            raise ValueError("O fator de zoom deve ser finito e positivo.")
        scene_before = self.mapToScene(viewport_position)
        self.scale(factor, factor)
        scene_after = self.mapToScene(viewport_position)
        correction = scene_after - scene_before
        self.translate(correction.x(), correction.y())
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
        self.selection_overlay = SelectionOverlayItem()
        self.scene.addItem(self.selection_overlay)

        self._active: dict[int, BarraItem] = {}
        self._pool: list[BarraItem] = []
        self._loaded_rect: QRectF | None = None
        self._last_view_rect: QRectF | None = None
        self._selected_index: int | None = None
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
        self._loaded_rect = None
        self._last_view_rect = None
        self._selected_index = None
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
        return inside and not zoomed_in_far

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

    def set_selected_index(self, index: int | None) -> None:
        previous = self._selected_index
        self._selected_index = index
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
            self.selection_overlay.setVisible(False)
            self.view.viewport().update()
            return

        self._loaded_rect = None
        self._last_view_rect = None
        self.refresh(force=True)
        self.view.viewport().update()

    def _sync_selection(self) -> None:
        if (
            not self._bars_visible
            or self.model is None
            or self._selected_index is None
        ):
            self.selection_overlay.setVisible(False)
            return
        index = self._selected_index
        if not 0 <= index < len(self.model):
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
