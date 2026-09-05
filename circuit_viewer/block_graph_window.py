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
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from .block_analysis import BlockAnalysisResult, BlockRecord
from .block_graph import (
    BlockGraph,
    BlockGraphEdge,
    BlockGraphLayout,
    BlockGraphLayoutMode,
    block_coordinate_anchors,
    block_node_diameters,
    build_block_graph,
    direct_circuit_neighbors,
    filter_block_graph,
    layout_block_graph,
    layout_block_graph_by_coordinates,
)
from .circuit_colors import contrasting_text_color, normalize_hex_color
from .display_identity import BlockDisplayIdentity
from .model import CLOSED_SWITCH_STATE, OPEN_SWITCH_STATE, switch_state_label


BLOCK_GRAPH_SCALE_SETTINGS_KEY = "block_graph/scale_nodes_by_power"
DEFAULT_SCALE_NODES_BY_POWER = False
CANVAS_BACKGROUND = "#FFFFFF"
DEFAULT_NODE_COLOR = "#D9D9D9"
NORMAL_EDGE_COLOR = "#555555"
INTERCIRCUIT_COLOR = "#FF00FF"
SWITCH_CLOSED_COLOR = "#00FF00"
SWITCH_OPEN_COLOR = "#FF0000"
SWITCH_UNKNOWN_COLOR = "#FFFFFF"
UNRESOLVED_CIRCUIT_INDEX = -1


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


def _color_icon(color: str) -> QIcon:
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor("#555555"), 1.0))
    painter.setBrush(QBrush(QColor(normalize_hex_color(color))))
    painter.drawRect(1, 1, 13, 13)
    painter.end()
    return QIcon(pixmap)


class CircuitSelectionPopup(QFrame):
    """Popup persistente durante a marcação dos circuitos do grafo."""

    selectionChanged = pyqtSignal(object, bool)
    includeNeighborsRequested = pyqtSignal()

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("block_graph_circuit_popup")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.summary_label = QLabel("Circuitos exibidos", self)
        layout.addWidget(self.summary_label)

        self.circuit_list = QListWidget(self)
        self.circuit_list.setObjectName("block_graph_circuit_list")
        self.circuit_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.circuit_list.setMinimumHeight(210)
        self.circuit_list.setMaximumHeight(360)
        layout.addWidget(self.circuit_list)

        actions = QHBoxLayout()
        self.select_all_button = QPushButton("Selecionar todos", self)
        self.clear_button = QPushButton("Limpar", self)
        actions.addWidget(self.select_all_button)
        actions.addWidget(self.clear_button)
        layout.addLayout(actions)

        self.include_neighbors_button = QPushButton(
            "Incluir vizinhos diretos",
            self,
        )
        self.include_neighbors_button.setObjectName(
            "include_block_graph_circuit_neighbors_button"
        )
        self.include_neighbors_button.setToolTip(
            "Marcar circuitos ligados aos exibidos por uma chave magenta"
        )
        layout.addWidget(self.include_neighbors_button)

        self.circuit_list.itemChanged.connect(self._item_changed)
        self.select_all_button.clicked.connect(self._select_all)
        self.clear_button.clicked.connect(self._clear_all)
        self.include_neighbors_button.clicked.connect(
            self.includeNeighborsRequested.emit
        )
        self._sync_actions()

    def set_entries(
        self,
        circuit_labels: tuple[str, ...],
        circuit_colors: tuple[str, ...],
        selected: frozenset[int],
        *,
        has_unresolved: bool,
        include_unresolved: bool,
    ) -> None:
        blocked = self.circuit_list.blockSignals(True)
        self.circuit_list.clear()
        for circuit_index, label in enumerate(circuit_labels):
            color = (
                circuit_colors[circuit_index]
                if circuit_index < len(circuit_colors)
                else DEFAULT_NODE_COLOR
            )
            item = QListWidgetItem(_color_icon(color), label)
            item.setData(Qt.ItemDataRole.UserRole, circuit_index)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if circuit_index in selected
                else Qt.CheckState.Unchecked
            )
            self.circuit_list.addItem(item)
        if has_unresolved:
            item = QListWidgetItem(
                _color_icon(DEFAULT_NODE_COLOR),
                "Sem circuito definido",
            )
            item.setData(
                Qt.ItemDataRole.UserRole,
                UNRESOLVED_CIRCUIT_INDEX,
            )
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if include_unresolved
                else Qt.CheckState.Unchecked
            )
            self.circuit_list.addItem(item)
        self.circuit_list.blockSignals(blocked)
        self._sync_actions()

    def checked_selection(self) -> tuple[frozenset[int], bool]:
        selected: set[int] = set()
        include_unresolved = False
        for row in range(self.circuit_list.count()):
            item = self.circuit_list.item(row)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            circuit_index = int(item.data(Qt.ItemDataRole.UserRole))
            if circuit_index == UNRESOLVED_CIRCUIT_INDEX:
                include_unresolved = True
            else:
                selected.add(circuit_index)
        return frozenset(selected), include_unresolved

    def set_checked_selection(
        self,
        selected: frozenset[int],
        *,
        include_unresolved: bool,
    ) -> None:
        blocked = self.circuit_list.blockSignals(True)
        for row in range(self.circuit_list.count()):
            item = self.circuit_list.item(row)
            circuit_index = int(item.data(Qt.ItemDataRole.UserRole))
            checked = (
                include_unresolved
                if circuit_index == UNRESOLVED_CIRCUIT_INDEX
                else circuit_index in selected
            )
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
        self.circuit_list.blockSignals(blocked)
        self._sync_actions()

    def _emit_selection(self) -> None:
        selected, include_unresolved = self.checked_selection()
        self._sync_actions()
        self.selectionChanged.emit(selected, include_unresolved)

    def _item_changed(self, _item: QListWidgetItem) -> None:
        self._emit_selection()

    def _select_all(self) -> None:
        blocked = self.circuit_list.blockSignals(True)
        for row in range(self.circuit_list.count()):
            self.circuit_list.item(row).setCheckState(Qt.CheckState.Checked)
        self.circuit_list.blockSignals(blocked)
        self._emit_selection()

    def _clear_all(self) -> None:
        blocked = self.circuit_list.blockSignals(True)
        for row in range(self.circuit_list.count()):
            self.circuit_list.item(row).setCheckState(Qt.CheckState.Unchecked)
        self.circuit_list.blockSignals(blocked)
        self._emit_selection()

    def _sync_actions(self) -> None:
        selected, include_unresolved = self.checked_selection()
        checked_count = len(selected) + int(include_unresolved)
        total = self.circuit_list.count()
        self.summary_label.setText(
            f"Circuitos exibidos: {len(selected):n}"
        )
        self.select_all_button.setEnabled(total > 0 and checked_count < total)
        self.clear_button.setEnabled(checked_count > 0)
        self.include_neighbors_button.setEnabled(bool(selected))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)


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
        display_label: str | None = None,
    ) -> None:
        super().__init__()
        self.record = record
        self.diameter = float(diameter)
        self.fill_color = QColor(normalize_hex_color(fill_color))
        self.display_label = str(display_label or f"B{record.block_id:n}")
        self._selected = False
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(2.0)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        switches = ", ".join(self.record.boundary_switch_codes) or "—"
        self.setToolTip(
            f"Bloco {self.display_label}\n"
            f"Potência instalada: {_power_text(self.record)}\n"
            f"Barras: {self.record.bar_count:n}\n"
            f"Trechos: {self.record.segment_count:n}\n"
            f"Cargas: {self.record.load_count:n}\n"
            f"Chaves de fronteira: {switches}\n"
            f"Bloco-fonte: {'sim' if self.record.contains_source else 'não'}"
        )

    def set_display_label(self, label: str) -> None:
        normalized = str(label)
        if normalized == self.display_label:
            return
        self.display_label = normalized
        self._update_tooltip()
        self.update()

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
        available_width = max(8.0, self.diameter - 6.0)
        while (
            font.pointSizeF() > 5.0
            and QFontMetricsF(font).horizontalAdvance(self.display_label)
            > available_width
        ):
            font.setPointSizeF(max(5.0, font.pointSizeF() - 0.5))
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(
            circle,
            Qt.AlignmentFlag.AlignCenter,
            self.display_label,
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
        state: str,
        highlighted: bool = False,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.text = text
        self.state = str(state).strip()
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
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_highlighted(self, highlighted: bool) -> None:
        normalized = bool(highlighted)
        if normalized == self.highlighted:
            return
        self.highlighted = normalized
        self.update()

    @property
    def background_color(self) -> QColor:
        if self.state == CLOSED_SWITCH_STATE:
            return QColor(SWITCH_CLOSED_COLOR)
        if self.state == OPEN_SWITCH_STATE:
            return QColor(SWITCH_OPEN_COLOR)
        return QColor(SWITCH_UNKNOWN_COLOR)

    @property
    def border_color(self) -> QColor:
        return QColor(INTERCIRCUIT_COLOR if self.highlighted else "#777777")

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
                self.border_color,
                2.0 if self.highlighted else 1.0,
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
        start_block_label: str | None = None,
        end_block_label: str | None = None,
    ) -> None:
        super().__init__(path)
        self.edge = edge
        self.intercircuit = bool(intercircuit)
        self._start_circuit = start_circuit
        self._end_circuit = end_circuit
        self._start_block_label = str(
            start_block_label or f"B{edge.start_block_id:n}"
        )
        self._end_block_label = str(
            end_block_label or f"B{edge.end_block_id:n}"
        )
        self._selected = False
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(0.0)
        self.label_item = _EdgeLabelItem(
            edge.label,
            state=edge.state,
            highlighted=self.intercircuit,
            parent=self,
        )
        self._update_tooltip()
        self.label_item.setPos(path.pointAtPercent(0.50))

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        normalized = bool(selected)
        if normalized == self._selected:
            return
        self._selected = normalized
        self.update()

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(12.0, self.stroke_width + 6.0))
        clickable = stroker.createStroke(self.path())
        clickable.addRect(
            self.label_item.mapRectToParent(self.label_item.boundingRect())
        )
        return clickable

    def boundingRect(self) -> QRectF:  # noqa: N802
        return super().boundingRect().adjusted(-8.0, -8.0, 8.0, 8.0)

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
            f"Blocos: {self._start_block_label} ↔ {self._end_block_label}"
            f"{circuit_text}"
        )
        self.label_item.setToolTip(self.toolTip())

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

    def set_block_labels(self, start_label: str, end_label: str) -> None:
        normalized_start = str(start_label)
        normalized_end = str(end_label)
        if (
            normalized_start == self._start_block_label
            and normalized_end == self._end_block_label
        ):
            return
        self._start_block_label = normalized_start
        self._end_block_label = normalized_end
        self._update_tooltip()

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
        if self._selected:
            selection_pen = QPen(
                QColor("#111111"),
                self.stroke_width + 5.0,
            )
            selection_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(selection_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())
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
    blockActivated = pyqtSignal(int)
    switchClicked = pyqtSignal(int)
    switchActivated = pyqtSignal(int)
    emptyClicked = pyqtSignal()
    resetRequested = pyqtSignal()
    MIN_ZOOM_SCALE = 1.0e-6
    MAX_ZOOM_SCALE = 8.0
    ZOOM_FACTOR = 1.2

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.graph = BlockGraph((), ())
        self.layout_result = BlockGraphLayout({}, {}, (), frozenset())
        self.layout_mode = BlockGraphLayoutMode.TREE
        self._coordinate_positions: dict[int, tuple[float, float]] = {}
        self.scale_by_power = False
        self._block_circuit_indices: dict[int, int | None] = {}
        self._block_identities: dict[int, BlockDisplayIdentity] = {}
        self._circuit_colors: tuple[str, ...] = ()
        self._circuit_labels: tuple[str, ...] = ()
        self.node_items: dict[int, BlockNodeItem] = {}
        self.edge_items: list[BlockEdgeItem] = []
        self._selected_block_id: int | None = None
        self._selected_switch_index: int | None = None
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
            "Duplo clique em um item: localizar no mapa"
        )
        self._empty_label = QLabel(self.viewport())
        self._empty_label.setObjectName("block_graph_empty_label")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet(
            "color: #333333; background: transparent; padding: 18px;"
        )
        self._empty_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._empty_label.hide()

    @property
    def selected_block_id(self) -> int | None:
        return self._selected_block_id

    @property
    def selected_switch_index(self) -> int | None:
        return self._selected_switch_index

    @property
    def zoom_level(self) -> float:
        return self._zoom_level

    def set_empty_message(self, message: str) -> None:
        self._empty_label.setText(message)
        self._empty_label.setVisible(bool(message))
        self._position_empty_label()

    def _position_empty_label(self) -> None:
        rect = self.viewport().rect().adjusted(40, 40, -40, -40)
        self._empty_label.setGeometry(rect)

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

    def _block_label(self, block_id: int) -> str:
        identity = self._block_identities.get(int(block_id))
        return (
            f"B{int(block_id):n}"
            if identity is None
            else identity.graph_label
        )

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
        layout_mode: BlockGraphLayoutMode = BlockGraphLayoutMode.TREE,
        coordinate_positions: dict[int, tuple[float, float]] | None = None,
        preserve_camera: bool = False,
    ) -> None:
        previous_selection = self._selected_block_id
        previous_switch_selection = self._selected_switch_index
        previous_transform = self.transform()
        previous_center = self.mapToScene(self.viewport().rect().center())
        previous_camera_modified = self._camera_modified
        self.graph = graph
        self.scale_by_power = bool(scale_by_power)
        self.layout_mode = BlockGraphLayoutMode(layout_mode)
        self._coordinate_positions = dict(coordinate_positions or {})
        if (
            self.layout_mode is BlockGraphLayoutMode.COORDINATES
            and all(
                block_id in self._coordinate_positions
                for block_id in graph.node_ids
            )
        ):
            self.layout_result = BlockGraphLayout(
                positions={
                    block_id: self._coordinate_positions[block_id]
                    for block_id in graph.node_ids
                },
                depths={},
                root_ids=tuple(
                    record.block_id
                    for record in graph.nodes
                    if record.contains_source
                ),
                tree_edge_indices=frozenset(range(len(graph.edges))),
            )
        else:
            self.layout_mode = BlockGraphLayoutMode.TREE
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
            elif (
                self.layout_mode is BlockGraphLayoutMode.TREE
                and edge_index not in self.layout_result.tree_edge_indices
            ):
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
                start_block_label=self._block_label(edge.start_block_id),
                end_block_label=self._block_label(edge.end_block_id),
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
                self._block_label(record.block_id),
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
        self._selected_switch_index = None
        if previous_selection in self.node_items:
            self.select_block(previous_selection)
        elif previous_switch_selection is not None:
            self.select_switch(previous_switch_selection)
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
            layout_mode=self.layout_mode,
            coordinate_positions=self._coordinate_positions,
            preserve_camera=True,
        )

    def set_circuit_styles(
        self,
        block_circuit_indices: dict[int, int | None],
        circuit_colors: tuple[str, ...],
        circuit_labels: tuple[str, ...],
        block_identities: dict[int, BlockDisplayIdentity] | None = None,
    ) -> None:
        normalized_colors = tuple(
            normalize_hex_color(color) for color in circuit_colors
        )
        normalized_indices = {
            int(block_id): None if index is None else int(index)
            for block_id, index in block_circuit_indices.items()
        }
        normalized_labels = tuple(str(label) for label in circuit_labels)
        normalized_identities = {
            int(block_id): identity
            for block_id, identity in (block_identities or {}).items()
        }
        if (
            normalized_indices == self._block_circuit_indices
            and normalized_colors == self._circuit_colors
            and normalized_labels == self._circuit_labels
            and normalized_identities == self._block_identities
        ):
            return
        self._block_circuit_indices = normalized_indices
        self._circuit_colors = normalized_colors
        self._circuit_labels = normalized_labels
        self._block_identities = normalized_identities
        for block_id, item in self.node_items.items():
            fill_color, _ = self._style_for_block(block_id)
            item.set_fill_color(fill_color)
            item.set_display_label(self._block_label(block_id))
        for item in self.edge_items:
            intercircuit, start_circuit, end_circuit = self._edge_style(item.edge)
            item.set_intercircuit(intercircuit, start_circuit, end_circuit)
            item.set_block_labels(
                self._block_label(item.edge.start_block_id),
                self._block_label(item.edge.end_block_id),
            )
        self.viewport().update()

    def select_block(self, block_id: int) -> bool:
        normalized = int(block_id)
        item = self.node_items.get(normalized)
        if item is None:
            return False
        self._clear_switch_selection()
        previous = self.node_items.get(self._selected_block_id or -1)
        if previous is not None and previous is not item:
            previous.set_selected(False)
        self._selected_block_id = normalized
        item.set_selected(True)
        self.ensureVisible(item, 48, 48)
        return True

    def select_switch(self, switch_index: int) -> bool:
        normalized = int(switch_index)
        item = next(
            (
                candidate
                for candidate in self.edge_items
                if candidate.edge.switch_index == normalized
            ),
            None,
        )
        if item is None:
            return False
        previous_node = self.node_items.get(self._selected_block_id or -1)
        if previous_node is not None:
            previous_node.set_selected(False)
        self._selected_block_id = None
        self._clear_switch_selection()
        self._selected_switch_index = normalized
        item.set_selected(True)
        self.ensureVisible(item.label_item, 48, 48)
        return True

    def _clear_switch_selection(self) -> None:
        if self._selected_switch_index is not None:
            for item in self.edge_items:
                if item.edge.switch_index == self._selected_switch_index:
                    item.set_selected(False)
                    break
        self._selected_switch_index = None

    def clear_selection(self) -> None:
        item = self.node_items.get(self._selected_block_id or -1)
        if item is not None:
            item.set_selected(False)
        self._selected_block_id = None
        self._clear_switch_selection()

    def _node_clicked(self, block_id: int) -> None:
        self.select_block(block_id)
        self.blockClicked.emit(block_id)

    def _switch_clicked(self, switch_index: int) -> None:
        self.select_switch(switch_index)
        self.switchClicked.emit(switch_index)

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
        current_scale = abs(self.transform().m11())
        if current_scale <= 0.0 or not math.isfinite(current_scale):
            return
        requested_scale = current_scale * self.ZOOM_FACTOR**steps
        target_scale = min(
            max(requested_scale, self.MIN_ZOOM_SCALE),
            self.MAX_ZOOM_SCALE,
        )
        if math.isclose(
            target_scale,
            current_scale,
            rel_tol=1.0e-12,
            abs_tol=0.0,
        ):
            return
        position = (
            viewport_position.toPoint()
            if isinstance(viewport_position, QPointF)
            else viewport_position
        )
        before = self.mapToScene(position)
        self.scale(target_scale / current_scale, target_scale / current_scale)
        after = self.mapToScene(position)
        self.translate(after.x() - before.x(), after.y() - before.y())
        self._zoom_level += math.log(
            target_scale / current_scale,
            self.ZOOM_FACTOR,
        )
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

    @staticmethod
    def _edge_ancestor(item) -> BlockEdgeItem | None:  # noqa: ANN001
        current = item
        while current is not None:
            if isinstance(current, BlockEdgeItem):
                return current
            current = current.parentItem()
        return None

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self.zoom_by_steps(event.angleDelta().y() / 120.0, event.position())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            node = self._node_ancestor(item)
            edge = self._edge_ancestor(item)
            if node is None and edge is not None:
                self._switch_clicked(edge.edge.switch_index)
                event.accept()
                return
            if node is None:
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
            item = self.itemAt(event.position().toPoint())
            node = self._node_ancestor(item)
            if node is not None:
                self.select_block(node.record.block_id)
                self.blockActivated.emit(node.record.block_id)
                event.accept()
                return
            edge = self._edge_ancestor(item)
            if edge is not None:
                self.select_switch(edge.edge.switch_index)
                self.switchActivated.emit(edge.edge.switch_index)
                event.accept()
                return
            self.fit_to_content()
            self.resetRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_empty_label()
        if not self._camera_modified:
            self._fit_pending = True
            self._schedule_fit()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_fit()


class BlockGraphWindow(QDialog):
    """Janela não modal que mantém a seleção sincronizada externamente."""

    blockRequested = pyqtSignal(int)
    blockActivated = pyqtSignal(int)
    switchRequested = pyqtSignal(int)
    switchActivated = pyqtSignal(int)
    selectionCleared = pyqtSignal()
    resetRequested = pyqtSignal()
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
        self._full_graph = BlockGraph((), ())
        self._block_circuit_indices: dict[int, int | None] = {}
        self._block_identities: dict[int, BlockDisplayIdentity] = {}
        self._circuit_colors: tuple[str, ...] = ()
        self._circuit_labels: tuple[str, ...] = ()
        self._selected_circuit_indices: frozenset[int] = frozenset()
        self._include_unresolved = False
        self._selection_needs_initialization = False
        self.scale_nodes_by_power = bool(scale_nodes_by_power)
        self.layout_mode = BlockGraphLayoutMode.TREE
        self._coordinate_anchors: dict[int, tuple[float, float]] = {}
        self._coordinate_layout = BlockGraphLayout({}, {}, (), frozenset())

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.hint_label = QLabel(
            "Roda: zoom · Arraste: mover",
            self,
        )
        controls.addWidget(self.hint_label, 1)
        self.circuit_selector_button = QPushButton(
            "Circuitos exibidos: 0/0",
            self,
        )
        self.circuit_selector_button.setObjectName(
            "block_graph_circuit_selector_button"
        )
        self.circuit_selector_button.setEnabled(False)
        controls.addWidget(self.circuit_selector_button)
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
        self.fit_button = QPushButton("Enquadrar", self)
        self.fit_button.setObjectName("fit_block_graph_button")
        self.fit_button.setEnabled(False)
        controls.addWidget(self.fit_button)
        layout.addLayout(controls)

        display_options = QHBoxLayout()
        self.layout_mode_label = QLabel("Posicionamento:", self)
        self.layout_mode_combo = QComboBox(self)
        self.layout_mode_combo.setObjectName("block_graph_layout_mode_combo")
        self.layout_mode_combo.addItem("Árvore", BlockGraphLayoutMode.TREE.value)
        self.layout_mode_combo.addItem(
            "Coordenadas da rede",
            BlockGraphLayoutMode.COORDINATES.value,
        )
        self.layout_mode_combo.setEnabled(False)
        self.layout_mode_combo.setToolTip(
            "Escolha entre a organização topológica e a disposição espacial da rede"
        )
        display_options.addWidget(self.layout_mode_label)
        display_options.addWidget(self.layout_mode_combo)
        display_options.addStretch(1)
        display_options.addWidget(self.scale_by_power_checkbox)
        layout.addLayout(display_options)

        self.view = BlockGraphView(self)
        layout.addWidget(self.view, 1)
        self.circuit_selector_popup = CircuitSelectionPopup(self)
        self.fit_button.clicked.connect(self.view.fit_to_content)
        self.circuit_selector_button.clicked.connect(
            self._toggle_circuit_selector
        )
        self.circuit_selector_popup.selectionChanged.connect(
            self._circuit_selection_changed
        )
        self.circuit_selector_popup.includeNeighborsRequested.connect(
            self._include_direct_neighbors
        )
        self.scale_by_power_checkbox.toggled.connect(
            self._scale_by_power_toggled
        )
        self.layout_mode_combo.currentIndexChanged.connect(
            self._layout_mode_changed
        )
        self.view.blockClicked.connect(self.blockRequested)
        self.view.blockActivated.connect(self.blockActivated)
        self.view.switchClicked.connect(self.switchRequested)
        self.view.switchActivated.connect(self.switchActivated)
        self.view.emptyClicked.connect(self.selectionCleared)
        self.view.resetRequested.connect(self.resetRequested)
        self.view.set_empty_message(
            "Carregue uma rede para visualizar o grafo de blocos."
        )

    @property
    def selected_circuit_indices(self) -> frozenset[int]:
        return self._selected_circuit_indices

    @property
    def include_unresolved(self) -> bool:
        return self._include_unresolved

    def set_result(self, result: BlockAnalysisResult | None) -> None:
        if result is self.result:
            return
        self.result = result
        self._block_identities = {}
        self._full_graph = (
            BlockGraph((), ()) if result is None else build_block_graph(result)
        )
        self._coordinate_anchors = (
            {} if result is None else block_coordinate_anchors(result)
        )
        self._rebuild_coordinate_layout()
        self._sync_layout_mode_control()
        self._selected_circuit_indices = frozenset()
        self._include_unresolved = False
        self._selection_needs_initialization = result is not None
        self.circuit_selector_popup.close()
        if result is None:
            self._block_circuit_indices = {}
            self._circuit_colors = ()
            self._circuit_labels = ()
            self._selection_needs_initialization = False
        self.view.set_circuit_styles(
            self._block_circuit_indices,
            self._circuit_colors,
            self._circuit_labels,
            self._block_identities,
        )
        self._initialize_circuit_selection()
        self._sync_circuit_selector()
        self._refresh_filtered_graph()

    def set_scale_nodes_by_power(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if (
            self.scale_nodes_by_power == normalized
            and self.scale_by_power_checkbox.isChecked() == normalized
        ):
            return
        if self.scale_by_power_checkbox.isChecked() != normalized:
            blocked = self.scale_by_power_checkbox.blockSignals(True)
            self.scale_by_power_checkbox.setChecked(normalized)
            self.scale_by_power_checkbox.blockSignals(blocked)
        self.scale_nodes_by_power = normalized
        self._rebuild_coordinate_layout()
        self._refresh_filtered_graph(preserve_camera=True)

    def _scale_by_power_toggled(self, enabled: bool) -> None:
        self.set_scale_nodes_by_power(enabled)
        self.scaleNodesByPowerChanged.emit(self.scale_nodes_by_power)

    @property
    def coordinate_layout_available(self) -> bool:
        return bool(self._full_graph.nodes) and all(
            block_id in self._coordinate_layout.positions
            for block_id in self._full_graph.node_ids
        )

    def _rebuild_coordinate_layout(self) -> None:
        if not self._full_graph.nodes or not all(
            block_id in self._coordinate_anchors
            for block_id in self._full_graph.node_ids
        ):
            self._coordinate_layout = BlockGraphLayout({}, {}, (), frozenset())
            return
        diameters = block_node_diameters(
            self._full_graph.nodes,
            self.scale_nodes_by_power,
        )
        try:
            self._coordinate_layout = layout_block_graph_by_coordinates(
                self._full_graph,
                self._coordinate_anchors,
                diameters,
            )
        except ValueError:
            self._coordinate_layout = BlockGraphLayout({}, {}, (), frozenset())

    def _sync_layout_mode_control(self) -> None:
        available = self.coordinate_layout_available
        if (
            self.result is not None
            and self.layout_mode is BlockGraphLayoutMode.COORDINATES
            and not available
        ):
            self.layout_mode = BlockGraphLayoutMode.TREE
        position = self.layout_mode_combo.findData(self.layout_mode.value)
        blocked = self.layout_mode_combo.blockSignals(True)
        if position >= 0:
            self.layout_mode_combo.setCurrentIndex(position)
        self.layout_mode_combo.blockSignals(blocked)
        self.layout_mode_combo.setEnabled(available)
        self.layout_mode_combo.setToolTip(
            "Escolha entre a organização topológica e a disposição espacial da rede"
            if available
            else "O modo espacial requer coordenadas válidas para todos os blocos"
        )

    def set_layout_mode(self, mode: BlockGraphLayoutMode | str) -> None:
        normalized = BlockGraphLayoutMode(mode)
        if (
            normalized is BlockGraphLayoutMode.COORDINATES
            and not self.coordinate_layout_available
        ):
            normalized = BlockGraphLayoutMode.TREE
        if normalized is self.layout_mode:
            self._sync_layout_mode_control()
            return
        self.layout_mode = normalized
        self._sync_layout_mode_control()
        self._refresh_filtered_graph()

    def _layout_mode_changed(self, _index: int) -> None:
        value = self.layout_mode_combo.currentData()
        if value is not None:
            self.set_layout_mode(str(value))

    def set_circuit_styles(
        self,
        block_circuit_indices: dict[int, int | None],
        circuit_colors: tuple[str, ...],
        circuit_labels: tuple[str, ...],
        block_identities: dict[int, BlockDisplayIdentity] | None = None,
    ) -> None:
        normalized_indices = {
            int(block_id): None if index is None else int(index)
            for block_id, index in block_circuit_indices.items()
        }
        normalized_colors = tuple(
            normalize_hex_color(color) for color in circuit_colors
        )
        normalized_labels = tuple(str(label) for label in circuit_labels)
        normalized_identities = {
            int(block_id): identity
            for block_id, identity in (block_identities or {}).items()
        }
        filter_context_changed = (
            normalized_indices != self._block_circuit_indices
            or normalized_labels != self._circuit_labels
        )
        self._block_circuit_indices = normalized_indices
        self._circuit_colors = normalized_colors
        self._circuit_labels = normalized_labels
        self._block_identities = normalized_identities
        valid_indices = frozenset(range(len(normalized_labels)))
        self._selected_circuit_indices &= valid_indices
        self._initialize_circuit_selection()
        self.view.set_circuit_styles(
            normalized_indices,
            normalized_colors,
            normalized_labels,
            normalized_identities,
        )
        self._sync_circuit_selector()
        if filter_context_changed:
            self._refresh_filtered_graph()

    def _initialize_circuit_selection(self) -> None:
        if not self._selection_needs_initialization or not self._circuit_labels:
            return
        self._selected_circuit_indices = (
            frozenset({0}) if len(self._circuit_labels) == 1 else frozenset()
        )
        self._include_unresolved = False
        self._selection_needs_initialization = False

    def _has_unresolved_blocks(self) -> bool:
        return any(
            self._block_circuit_indices.get(record.block_id) is None
            for record in self._full_graph.nodes
        )

    def _sync_circuit_selector(self, *, rebuild_entries: bool = True) -> None:
        has_unresolved = self._has_unresolved_blocks()
        if rebuild_entries:
            self.circuit_selector_popup.set_entries(
                self._circuit_labels,
                self._circuit_colors,
                self._selected_circuit_indices,
                has_unresolved=has_unresolved,
                include_unresolved=self._include_unresolved,
            )
        else:
            self.circuit_selector_popup.set_checked_selection(
                self._selected_circuit_indices,
                include_unresolved=self._include_unresolved,
            )
        selected_count = len(self._selected_circuit_indices)
        total = len(self._circuit_labels)
        self.circuit_selector_button.setText(
            f"Circuitos exibidos: {selected_count:n}/{total:n}"
        )
        selected_labels = [
            self._circuit_labels[index]
            for index in sorted(self._selected_circuit_indices)
            if 0 <= index < total
        ]
        if self._include_unresolved:
            selected_labels.append("Sem circuito definido")
        tooltip = (
            "Selecionados: " + ", ".join(selected_labels)
            if selected_labels
            else "Nenhum circuito selecionado"
        )
        self.circuit_selector_button.setToolTip(
            tooltip + "\nClique para alterar os circuitos do grafo."
        )
        self.circuit_selector_button.setEnabled(
            self.result is not None and bool(self._circuit_labels)
        )

    def _refresh_filtered_graph(self, *, preserve_camera: bool = False) -> None:
        previous_block = self.view.selected_block_id
        previous_switch = self.view.selected_switch_index
        graph = filter_block_graph(
            self._full_graph,
            self._block_circuit_indices,
            self._selected_circuit_indices,
            include_unresolved=self._include_unresolved,
        )
        self.view.set_graph(
            graph,
            scale_by_power=self.scale_nodes_by_power,
            layout_mode=self.layout_mode,
            coordinate_positions=self._coordinate_layout.positions,
            preserve_camera=preserve_camera,
        )
        self.fit_button.setEnabled(bool(graph.nodes))
        if self.result is None:
            message = "Carregue uma rede para visualizar o grafo de blocos."
        elif not self._selected_circuit_indices and not self._include_unresolved:
            message = (
                "Selecione os circuitos no botão acima para montar o grafo."
            )
        elif not graph.nodes:
            message = "Nenhum bloco foi encontrado para a seleção atual."
        else:
            message = ""
        self.view.set_empty_message(message)
        selection_disappeared = (
            previous_block is not None
            and self.view.selected_block_id is None
        ) or (
            previous_switch is not None
            and self.view.selected_switch_index is None
        )
        if selection_disappeared:
            self.selectionCleared.emit()

    def _circuit_selection_changed(
        self,
        selected: object,
        include_unresolved: bool,
    ) -> None:
        values = frozenset(
            index
            for index in (int(value) for value in selected)
            if 0 <= index < len(self._circuit_labels)
        )
        normalized_unresolved = bool(
            include_unresolved and self._has_unresolved_blocks()
        )
        if (
            values == self._selected_circuit_indices
            and normalized_unresolved == self._include_unresolved
        ):
            return
        self._selected_circuit_indices = values
        self._include_unresolved = normalized_unresolved
        self._sync_circuit_selector(rebuild_entries=False)
        self._refresh_filtered_graph()

    def _include_direct_neighbors(self) -> None:
        neighbors = direct_circuit_neighbors(
            self._full_graph,
            self._block_circuit_indices,
            self._selected_circuit_indices,
        )
        expanded = self._selected_circuit_indices | neighbors
        if expanded == self._selected_circuit_indices:
            return
        self._selected_circuit_indices = expanded
        self._sync_circuit_selector(rebuild_entries=False)
        self._refresh_filtered_graph()

    def _toggle_circuit_selector(self) -> None:
        popup = self.circuit_selector_popup
        if popup.isVisible():
            popup.close()
            return
        popup.adjustSize()
        size = popup.sizeHint()
        width = max(size.width(), self.circuit_selector_button.width(), 350)
        height = size.height()
        anchor = self.circuit_selector_button.mapToGlobal(
            QPoint(0, self.circuit_selector_button.height())
        )
        screen = QApplication.screenAt(anchor) or self.screen()
        available = screen.availableGeometry()
        x = min(max(anchor.x(), available.left()), available.right() - width + 1)
        y = anchor.y()
        if y + height > available.bottom() + 1:
            top = self.circuit_selector_button.mapToGlobal(QPoint()).y()
            y = max(available.top(), top - height)
        popup.resize(width, height)
        popup.move(x, y)
        popup.show()
        popup.raise_()
        popup.circuit_list.setFocus(Qt.FocusReason.PopupFocusReason)

    def select_block(self, block: BlockRecord | int) -> bool:
        block_id = block.block_id if isinstance(block, BlockRecord) else int(block)
        selected = self.view.select_block(block_id)
        if not selected:
            self.view.clear_selection()
        return selected

    def select_switch(self, switch_index: int) -> bool:
        return self.view.select_switch(switch_index)

    def clear_selection(self) -> None:
        self.view.clear_selection()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if (
            event.key() == Qt.Key.Key_Escape
            and (
                self.view.selected_block_id is not None
                or self.view.selected_switch_index is not None
            )
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
    "CircuitSelectionPopup",
    "SWITCH_CLOSED_COLOR",
    "SWITCH_OPEN_COLOR",
    "SWITCH_UNKNOWN_COLOR",
    "UNRESOLVED_CIRCUIT_INDEX",
    "load_scale_nodes_by_power",
    "parse_scale_nodes_by_power",
    "save_scale_nodes_by_power",
]
