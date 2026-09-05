"""Janela interativa com a topologia simplificada dos blocos elétricos."""

from __future__ import annotations

import math

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QSettings,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .block_analysis import BlockAnalysisResult, BlockRecord
from .block_graph import (
    BlockGraph,
    BlockGraphEdge,
    BlockGraphLayout,
    block_node_diameters,
    build_block_graph,
    layout_block_graph,
)
from .circuit_colors import contrasting_text_color, normalize_hex_color
from .model import switch_state_label


BLOCK_GRAPH_SCALE_SETTINGS_KEY = "block_graph/scale_nodes_by_power"
DEFAULT_SCALE_NODES_BY_POWER = False
CANVAS_BACKGROUND = "#FFFFFF"
DEFAULT_NODE_COLOR = "#D9D9D9"
NORMAL_EDGE_COLOR = "#555555"
INTERCIRCUIT_COLOR = "#FF00FF"


def parse_scale_nodes_by_power(value: object) -> bool:
    """Converte com segurança o valor heterogêneo devolvido por QSettings."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "sim", "on"}:
        return True
    if normalized in {"0", "false", "no", "não", "nao", "off", ""}:
        return False
    return DEFAULT_SCALE_NODES_BY_POWER


def load_scale_nodes_by_power(settings: QSettings) -> bool:
    stored = settings.value(BLOCK_GRAPH_SCALE_SETTINGS_KEY)
    if stored is None:
        return DEFAULT_SCALE_NODES_BY_POWER
    return parse_scale_nodes_by_power(stored)


def save_scale_nodes_by_power(settings: QSettings, enabled: bool) -> None:
    settings.setValue(BLOCK_GRAPH_SCALE_SETTINGS_KEY, bool(enabled))
    settings.sync()


def _power_text(record: BlockRecord) -> str:
    if record.total_power is None:
        return "— kVA"
    return f"{float(record.total_power):n} kVA"


class BlockNodeItem(QGraphicsObject):
    """Nó circular clicável; a legenda externa não altera a área de clique."""

    clicked = pyqtSignal(int)
    CAPTION_WIDTH = 132.0
    CAPTION_HEIGHT = 22.0

    def __init__(
        self,
        record: BlockRecord,
        diameter: float,
        fill_color: str = DEFAULT_NODE_COLOR,
    ) -> None:
        super().__init__()
        self.record = record
        self.diameter = float(diameter)
        self.fill_color = QColor(normalize_hex_color(fill_color))
        self._selected = False
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(2.0)
        switches = ", ".join(record.boundary_switch_codes) or "—"
        self.setToolTip(
            f"Bloco {record.block_id:n}\n"
            f"Potência instalada: {_power_text(record)}\n"
            f"Barras: {record.bar_count:n}\n"
            f"Trechos: {record.segment_count:n}\n"
            f"Cargas: {record.load_count:n}\n"
            f"Chaves de fronteira: {switches}\n"
            f"Bloco-fonte: {'sim' if record.contains_source else 'não'}"
        )

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        normalized = bool(selected)
        if normalized == self._selected:
            return
        self._selected = normalized
        self.update()

    def set_fill_color(self, color: str) -> None:
        normalized = QColor(normalize_hex_color(color))
        if normalized == self.fill_color:
            return
        self.fill_color = normalized
        self.update()

    @property
    def text_color(self) -> QColor:
        return QColor(contrasting_text_color(self.fill_color.name()))

    def boundingRect(self) -> QRectF:  # noqa: N802
        radius = self.diameter / 2.0
        half_width = max(radius, self.CAPTION_WIDTH / 2.0)
        return QRectF(
            -half_width - 3.0,
            -radius - 3.0,
            2.0 * half_width + 6.0,
            self.diameter + self.CAPTION_HEIGHT + 9.0,
        )

    def shape(self) -> QPainterPath:
        radius = self.diameter / 2.0
        path = QPainterPath()
        path.addEllipse(QRectF(-radius, -radius, self.diameter, self.diameter))
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        radius = self.diameter / 2.0
        circle = QRectF(-radius, -radius, self.diameter, self.diameter)
        fill = QColor(self.fill_color)
        text_color = self.text_color
        border = QColor("#111111") if (
            self._selected or self.record.contains_source
        ) else fill.darker(170)
        border_width = 5.0 if self._selected else (
            3.0 if self.record.contains_source else 2.0
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, border_width))
        painter.drawEllipse(circle)

        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(
            circle,
            Qt.AlignmentFlag.AlignCenter,
            f"B{self.record.block_id:n}",
        )

        caption = QRectF(
            -self.CAPTION_WIDTH / 2.0,
            radius + 4.0,
            self.CAPTION_WIDTH,
            self.CAPTION_HEIGHT,
        )
        font.setBold(False)
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        painter.setPen(QColor("#000000"))
        painter.drawText(
            caption,
            Qt.AlignmentFlag.AlignHCenter,
            _power_text(self.record),
        )

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.record.block_id)
            event.accept()
            return
        super().mousePressEvent(event)


class _EdgeLabelItem(QGraphicsObject):
    def __init__(
        self,
        text: str,
        *,
        highlighted: bool = False,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.text = text
        self.highlighted = bool(highlighted)
        metrics = QFontMetricsF(QApplication.font())
        bounds = metrics.boundingRect(text)
        self._bounds = QRectF(
            -bounds.width() / 2.0 - 5.0,
            -bounds.height() / 2.0 - 2.0,
            bounds.width() + 10.0,
            bounds.height() + 4.0,
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_highlighted(self, highlighted: bool) -> None:
        normalized = bool(highlighted)
        if normalized == self.highlighted:
            return
        self.highlighted = normalized
        self.update()

    @property
    def background_color(self) -> QColor:
        return QColor(
            INTERCIRCUIT_COLOR if self.highlighted else CANVAS_BACKGROUND
        )

    @property
    def text_color(self) -> QColor:
        return QColor(contrasting_text_color(self.background_color.name()))

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._bounds

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        background = self.background_color
        painter.setPen(
            QPen(
                QColor(INTERCIRCUIT_COLOR if self.highlighted else "#777777"),
                1.5 if self.highlighted else 1.0,
            )
        )
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(self._bounds, 3.0, 3.0)
        painter.setPen(self.text_color)
        painter.drawText(self._bounds, Qt.AlignmentFlag.AlignCenter, self.text)


class BlockEdgeItem(QGraphicsPathItem):
    def __init__(
        self,
        edge: BlockGraphEdge,
        path: QPainterPath,
        *,
        intercircuit: bool = False,
        start_circuit: str = "",
        end_circuit: str = "",
    ) -> None:
        super().__init__(path)
        self.edge = edge
        self.intercircuit = bool(intercircuit)
        self._start_circuit = start_circuit
        self._end_circuit = end_circuit
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(0.0)
        self.label_item = _EdgeLabelItem(
            edge.label,
            highlighted=self.intercircuit,
            parent=self,
        )
        self._update_tooltip()
        self.label_item.setPos(path.pointAtPercent(0.50))

    def _update_tooltip(self) -> None:
        state = switch_state_label(self.edge.state) or "—"
        circuit_text = (
            f"\nInterligação: {self._start_circuit} ↔ {self._end_circuit}"
            if self.intercircuit
            else ""
        )
        self.setToolTip(
            f"Chave: {self.edge.label}\n"
            f"ID: {self.edge.switch_id or '—'}\n"
            f"Estado: {state}\n"
            f"Blocos: B{self.edge.start_block_id:n} ↔ B{self.edge.end_block_id:n}"
            f"{circuit_text}"
        )

    @property
    def stroke_color(self) -> QColor:
        return QColor(
            INTERCIRCUIT_COLOR if self.intercircuit else NORMAL_EDGE_COLOR
        )

    @property
    def stroke_width(self) -> float:
        return 4.0 if self.intercircuit else 1.8

    def set_intercircuit(
        self,
        intercircuit: bool,
        start_circuit: str = "",
        end_circuit: str = "",
    ) -> None:
        normalized = bool(intercircuit)
        changed = (
            normalized != self.intercircuit
            or start_circuit != self._start_circuit
            or end_circuit != self._end_circuit
        )
        self.intercircuit = normalized
        self._start_circuit = start_circuit
        self._end_circuit = end_circuit
        if not changed:
            return
        self.label_item.set_highlighted(normalized)
        self._update_tooltip()
        self.update()

    def position_label_away_from(self, node_rects: tuple[QRectF, ...]) -> None:
        """Escolhe um ponto central da aresta cuja legenda não cubra nós."""

        candidates = (0.50, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74)
        label_bounds = self.label_item.boundingRect()
        for percent in candidates:
            point = self.path().pointAtPercent(percent)
            candidate = label_bounds.translated(point).adjusted(
                -3.0,
                -3.0,
                3.0,
                3.0,
            )
            if not any(candidate.intersects(rect) for rect in node_rects):
                self.label_item.setPos(point)
                return
        self.label_item.setPos(self.path().pointAtPercent(0.50))

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(
            QPen(self.stroke_color, self.stroke_width)
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())


def _edge_path(
    edge: BlockGraphEdge,
    start: QPointF,
    end: QPointF,
    offset: float,
    node_diameter: float,
) -> QPainterPath:
    path = QPainterPath(start)
    if edge.start_block_id == edge.end_block_id:
        radius = node_diameter / 2.0
        path.moveTo(start.x() + radius * 0.55, start.y() - radius * 0.7)
        path.cubicTo(
            start.x() + radius + 55.0 + offset,
            start.y() - radius - 60.0 - abs(offset),
            start.x() - radius - 55.0 - offset,
            start.y() - radius - 60.0 - abs(offset),
            start.x() - radius * 0.55,
            start.y() - radius * 0.7,
        )
        return path

    delta = end - start
    length = math.hypot(delta.x(), delta.y())
    if length <= 1.0e-9 or abs(offset) <= 1.0e-9:
        path.lineTo(end)
        return path
    middle = (start + end) / 2.0
    normal = QPointF(-delta.y() / length, delta.x() / length)
    control = middle + normal * offset
    path.quadTo(control, end)
    return path


class BlockGraphView(QGraphicsView):
    """Canvas do grafo com zoom, pan, enquadramento e seleção."""

    blockClicked = pyqtSignal(int)
    emptyClicked = pyqtSignal()
    MIN_ZOOM_LEVEL = -12.0
    MAX_ZOOM_LEVEL = 24.0
    ZOOM_FACTOR = 1.2

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.graph = BlockGraph((), ())
        self.layout_result = BlockGraphLayout({}, {}, (), frozenset())
        self.scale_by_power = False
        self._block_circuit_indices: dict[int, int | None] = {}
        self._circuit_colors: tuple[str, ...] = ()
        self._circuit_labels: tuple[str, ...] = ()
        self.node_items: dict[int, BlockNodeItem] = {}
        self.edge_items: list[BlockEdgeItem] = []
        self._selected_block_id: int | None = None
        self._content_rect = QRectF()
        self._fit_pending = False
        self._camera_modified = False
        self._zoom_level = 0.0
        self._dragging = False
        self._drag_moved = False
        self._last_mouse_position = QPointF()

        self.setObjectName("block_graph_view")
        self.setBackgroundBrush(QColor(CANVAS_BACKGROUND))
        viewport_palette = self.viewport().palette()
        viewport_palette.setColor(QPalette.ColorRole.Base, QColor(CANVAS_BACKGROUND))
        viewport_palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
        self.viewport().setPalette(viewport_palette)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "Roda: zoom sob o cursor · Arraste o fundo: mover · "
            "Duplo clique: enquadrar"
        )

    @property
    def selected_block_id(self) -> int | None:
        return self._selected_block_id

    @property
    def zoom_level(self) -> float:
        return self._zoom_level

    def _style_for_block(self, block_id: int) -> tuple[str, int | None]:
        circuit_index = self._block_circuit_indices.get(int(block_id))
        if (
            circuit_index is None
            or not 0 <= circuit_index < len(self._circuit_colors)
        ):
            return DEFAULT_NODE_COLOR, None
        return self._circuit_colors[circuit_index], circuit_index

    def _circuit_label(self, circuit_index: int | None) -> str:
        if circuit_index is None:
            return ""
        if 0 <= circuit_index < len(self._circuit_labels):
            return self._circuit_labels[circuit_index]
        return str(circuit_index)

    def _edge_style(self, edge: BlockGraphEdge) -> tuple[bool, str, str]:
        _, start_index = self._style_for_block(edge.start_block_id)
        _, end_index = self._style_for_block(edge.end_block_id)
        intercircuit = (
            start_index is not None
            and end_index is not None
            and start_index != end_index
        )
        return (
            intercircuit,
            self._circuit_label(start_index),
            self._circuit_label(end_index),
        )

    def set_graph(
        self,
        graph: BlockGraph,
        *,
        scale_by_power: bool,
        preserve_camera: bool = False,
    ) -> None:
        previous_selection = self._selected_block_id
        previous_transform = self.transform()
        previous_center = self.mapToScene(self.viewport().rect().center())
        previous_camera_modified = self._camera_modified
        self.graph = graph
        self.scale_by_power = bool(scale_by_power)
        self.layout_result = layout_block_graph(graph)
        self._scene.clear()
        self.node_items.clear()
        self.edge_items.clear()

        diameters = block_node_diameters(graph.nodes, self.scale_by_power)
        points = {
            block_id: QPointF(*position)
            for block_id, position in self.layout_result.positions.items()
        }
        parallel: dict[tuple[int, int], list[int]] = {}
        for edge_index, edge in enumerate(graph.edges):
            parallel.setdefault(edge.endpoint_key, []).append(edge_index)

        node_rects = tuple(
            QRectF(
                points[record.block_id].x() - diameters[record.block_id] / 2.0,
                points[record.block_id].y() - diameters[record.block_id] / 2.0,
                diameters[record.block_id],
                diameters[record.block_id],
            ).adjusted(-8.0, -8.0, 8.0, 8.0)
            for record in graph.nodes
        )

        for edge_index, edge in enumerate(graph.edges):
            group = parallel[edge.endpoint_key]
            position = group.index(edge_index)
            if edge.start_block_id == edge.end_block_id:
                offset = position * 18.0
            elif len(group) > 1:
                offset = (position - (len(group) - 1) / 2.0) * 34.0
            elif edge_index not in self.layout_result.tree_edge_indices:
                offset = 36.0
            else:
                offset = 0.0
            path = _edge_path(
                edge,
                points[edge.start_block_id],
                points[edge.end_block_id],
                offset,
                diameters[edge.start_block_id],
            )
            intercircuit, start_circuit, end_circuit = self._edge_style(edge)
            item = BlockEdgeItem(
                edge,
                path,
                intercircuit=intercircuit,
                start_circuit=start_circuit,
                end_circuit=end_circuit,
            )
            item.position_label_away_from(node_rects)
            self._scene.addItem(item)
            self.edge_items.append(item)

        for record in graph.nodes:
            fill_color, _ = self._style_for_block(record.block_id)
            item = BlockNodeItem(
                record,
                diameters[record.block_id],
                fill_color,
            )
            item.setPos(points[record.block_id])
            item.clicked.connect(self._node_clicked)
            self._scene.addItem(item)
            self.node_items[record.block_id] = item

        self._content_rect = self._scene.itemsBoundingRect()
        if self._content_rect.isValid() and not self._content_rect.isEmpty():
            span = max(self._content_rect.width(), self._content_rect.height(), 1.0)
            margin = max(1_000.0, span * 8.0)
            self.setSceneRect(
                self._content_rect.adjusted(-margin, -margin, margin, margin)
            )
        else:
            self.setSceneRect(QRectF(-1.0, -1.0, 2.0, 2.0))

        self._selected_block_id = None
        if previous_selection in self.node_items:
            self.select_block(previous_selection)
        if preserve_camera:
            self.setTransform(previous_transform)
            self.centerOn(previous_center)
            self._camera_modified = previous_camera_modified
            self._fit_pending = False
            self.viewport().update()
        else:
            self._camera_modified = False
            self._fit_pending = True
            self._schedule_fit()

    def set_scale_by_power(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if normalized == self.scale_by_power:
            return
        self.set_graph(
            self.graph,
            scale_by_power=normalized,
            preserve_camera=True,
        )

    def set_circuit_styles(
        self,
        block_circuit_indices: dict[int, int | None],
        circuit_colors: tuple[str, ...],
        circuit_labels: tuple[str, ...],
    ) -> None:
        normalized_colors = tuple(
            normalize_hex_color(color) for color in circuit_colors
        )
        normalized_indices = {
            int(block_id): None if index is None else int(index)
            for block_id, index in block_circuit_indices.items()
        }
        normalized_labels = tuple(str(label) for label in circuit_labels)
        if (
            normalized_indices == self._block_circuit_indices
            and normalized_colors == self._circuit_colors
            and normalized_labels == self._circuit_labels
        ):
            return
        self._block_circuit_indices = normalized_indices
        self._circuit_colors = normalized_colors
        self._circuit_labels = normalized_labels
        for block_id, item in self.node_items.items():
            fill_color, _ = self._style_for_block(block_id)
            item.set_fill_color(fill_color)
        for item in self.edge_items:
            intercircuit, start_circuit, end_circuit = self._edge_style(item.edge)
            item.set_intercircuit(intercircuit, start_circuit, end_circuit)
        self.viewport().update()

    def select_block(self, block_id: int) -> bool:
        normalized = int(block_id)
        item = self.node_items.get(normalized)
        if item is None:
            return False
        previous = self.node_items.get(self._selected_block_id or -1)
        if previous is not None and previous is not item:
            previous.set_selected(False)
        self._selected_block_id = normalized
        item.set_selected(True)
        self.ensureVisible(item, 48, 48)
        return True

    def clear_selection(self) -> None:
        item = self.node_items.get(self._selected_block_id or -1)
        if item is not None:
            item.set_selected(False)
        self._selected_block_id = None

    def _node_clicked(self, block_id: int) -> None:
        self.select_block(block_id)
        self.blockClicked.emit(block_id)

    def _schedule_fit(self) -> None:
        QTimer.singleShot(0, self._fit_if_pending)

    def _fit_if_pending(self) -> None:
        if (
            not self._fit_pending
            or not self.isVisible()
            or self.viewport().width() < 40
            or not self.node_items
        ):
            return
        self.fit_to_content()

    def fit_to_content(self) -> None:
        self.resetTransform()
        if (
            self.node_items
            and self.viewport().width() >= 2
            and self.viewport().height() >= 2
        ):
            bounds = self._content_rect.adjusted(-35.0, -35.0, 35.0, 35.0)
            self.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)
            if self.transform().m11() > 1.5:
                self.resetTransform()
                self.scale(1.5, 1.5)
                self.centerOn(bounds.center())
        self._zoom_level = 0.0
        self._camera_modified = False
        self._fit_pending = False
        self.viewport().update()

    def zoom_by_steps(
        self,
        steps: float,
        viewport_position: QPointF | QPoint,
    ) -> None:
        if not math.isfinite(steps) or steps == 0.0:
            return
        target = min(
            max(self._zoom_level + steps, self.MIN_ZOOM_LEVEL),
            self.MAX_ZOOM_LEVEL,
        )
        applied = target - self._zoom_level
        if abs(applied) < 1.0e-12:
            return
        position = (
            viewport_position.toPoint()
            if isinstance(viewport_position, QPointF)
            else viewport_position
        )
        before = self.mapToScene(position)
        self.scale(self.ZOOM_FACTOR**applied, self.ZOOM_FACTOR**applied)
        after = self.mapToScene(position)
        self.translate(after.x() - before.x(), after.y() - before.y())
        self._zoom_level = target
        self._camera_modified = True
        self._fit_pending = False

    def pan_by_pixels(self, delta: QPointF | QPoint) -> None:
        delta_point = QPointF(delta)
        if delta_point.isNull():
            return
        inverse, invertible = self.viewportTransform().inverted()
        if not invertible:
            return
        scene_origin = inverse.map(QPointF())
        scene_shifted = inverse.map(delta_point)
        scene_delta = scene_shifted - scene_origin
        self.translate(scene_delta.x(), scene_delta.y())
        self._camera_modified = True
        self._fit_pending = False

    @staticmethod
    def _node_ancestor(item) -> BlockNodeItem | None:  # noqa: ANN001
        current = item
        while current is not None:
            if isinstance(current, BlockNodeItem):
                return current
            current = current.parentItem()
        return None

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self.zoom_by_steps(event.angleDelta().y() / 120.0, event.position())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if self._node_ancestor(item) is None:
                self._dragging = True
                self._drag_moved = False
                self._last_mouse_position = QPointF(event.position())
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            current = QPointF(event.position())
            delta = current - self._last_mouse_position
            if abs(delta.x()) + abs(delta.y()) >= 1.0:
                self._drag_moved = True
            self.pan_by_pixels(delta)
            self._last_mouse_position = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            moved = self._drag_moved
            self._dragging = False
            self._drag_moved = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if not moved:
                self.clear_selection()
                self.emptyClicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.fit_to_content()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._camera_modified:
            self._fit_pending = True
            self._schedule_fit()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_fit()


class BlockGraphWindow(QDialog):
    """Janela não modal que mantém a seleção sincronizada externamente."""

    blockRequested = pyqtSignal(int)
    selectionCleared = pyqtSignal()
    scaleNodesByPowerChanged = pyqtSignal(bool)

    def __init__(
        self,
        *,
        scale_nodes_by_power: bool = DEFAULT_SCALE_NODES_BY_POWER,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setObjectName("block_graph_window")
        self.setWindowTitle("Grafo de blocos")
        self.setModal(False)
        self.resize(940, 680)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAutoFillBackground(True)
        light_palette = QPalette(self.palette())
        light_palette.setColor(QPalette.ColorRole.Window, QColor(CANVAS_BACKGROUND))
        light_palette.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
        light_palette.setColor(QPalette.ColorRole.Base, QColor(CANVAS_BACKGROUND))
        light_palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
        light_palette.setColor(QPalette.ColorRole.Button, QColor("#F2F2F2"))
        light_palette.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
        self.setPalette(light_palette)
        self.result: BlockAnalysisResult | None = None
        self.scale_nodes_by_power = bool(scale_nodes_by_power)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.hint_label = QLabel(
            "Roda: zoom · Arraste o fundo: mover · Duplo clique: enquadrar",
            self,
        )
        controls.addWidget(self.hint_label, 1)
        self.scale_by_power_checkbox = QCheckBox(
            "Dimensionar nós dos blocos por potência instalada",
            self,
        )
        self.scale_by_power_checkbox.setObjectName(
            "scale_block_graph_nodes_by_power_checkbox"
        )
        self.scale_by_power_checkbox.setToolTip(
            "Representar a potência instalada pela área dos nós no grafo"
        )
        self.scale_by_power_checkbox.setChecked(self.scale_nodes_by_power)
        controls.addWidget(self.scale_by_power_checkbox)
        self.fit_button = QPushButton("Enquadrar", self)
        self.fit_button.setObjectName("fit_block_graph_button")
        self.fit_button.setEnabled(False)
        controls.addWidget(self.fit_button)
        layout.addLayout(controls)

        self.view = BlockGraphView(self)
        layout.addWidget(self.view, 1)
        self.fit_button.clicked.connect(self.view.fit_to_content)
        self.scale_by_power_checkbox.toggled.connect(
            self._scale_by_power_toggled
        )
        self.view.blockClicked.connect(self.blockRequested)
        self.view.emptyClicked.connect(self.selectionCleared)

    def set_result(self, result: BlockAnalysisResult | None) -> None:
        self.result = result
        graph = BlockGraph((), ()) if result is None else build_block_graph(result)
        self.view.set_graph(graph, scale_by_power=self.scale_nodes_by_power)
        self.fit_button.setEnabled(bool(graph.nodes))

    def set_scale_nodes_by_power(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self.scale_by_power_checkbox.isChecked() != normalized:
            blocked = self.scale_by_power_checkbox.blockSignals(True)
            self.scale_by_power_checkbox.setChecked(normalized)
            self.scale_by_power_checkbox.blockSignals(blocked)
        self.scale_nodes_by_power = normalized
        self.view.set_scale_by_power(self.scale_nodes_by_power)

    def _scale_by_power_toggled(self, enabled: bool) -> None:
        self.set_scale_nodes_by_power(enabled)
        self.scaleNodesByPowerChanged.emit(self.scale_nodes_by_power)

    def set_circuit_styles(
        self,
        block_circuit_indices: dict[int, int | None],
        circuit_colors: tuple[str, ...],
        circuit_labels: tuple[str, ...],
    ) -> None:
        self.view.set_circuit_styles(
            block_circuit_indices,
            circuit_colors,
            circuit_labels,
        )

    def select_block(self, block: BlockRecord | int) -> bool:
        block_id = block.block_id if isinstance(block, BlockRecord) else int(block)
        return self.view.select_block(block_id)

    def clear_selection(self) -> None:
        self.view.clear_selection()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if (
            event.key() == Qt.Key.Key_Escape
            and self.view.selected_block_id is not None
        ):
            self.view.clear_selection()
            self.selectionCleared.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        } and hasattr(self, "view"):
            self.view.viewport().update()


__all__ = [
    "BLOCK_GRAPH_SCALE_SETTINGS_KEY",
    "DEFAULT_SCALE_NODES_BY_POWER",
    "BlockEdgeItem",
    "BlockGraphView",
    "BlockGraphWindow",
    "BlockNodeItem",
    "load_scale_nodes_by_power",
    "parse_scale_nodes_by_power",
    "save_scale_nodes_by_power",
]
