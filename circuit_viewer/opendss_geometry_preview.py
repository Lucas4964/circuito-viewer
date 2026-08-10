"""Gráfico cartesiano interativo para arranjos e montagens OpenDSS."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QShowEvent,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .opendss_library import (
    ArrangementDefinition,
    GeometryDefinition,
    OpenDssLibraryCatalog,
)


_PHASE_COLORS = (
    QColor("#e53935"),
    QColor("#43a047"),
    QColor("#1e88e5"),
)
_NEUTRAL_COLOR = QColor("#8e75d6")


def _coordinate(value: float) -> str:
    return f"{value:g}"


def _nice_grid_step(target: float) -> float:
    """Retorna um passo 1/2/5 × 10ⁿ próximo de ``target``."""

    if not math.isfinite(target) or target <= 0:
        return 1.0
    power = 10.0 ** math.floor(math.log10(target))
    fraction = target / power
    if fraction <= 1.0:
        nice = 1.0
    elif fraction <= 2.0:
        nice = 2.0
    elif fraction <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return nice * power


class _ConductorPointItem(QGraphicsItem):
    """Ponto e textos mantidos com tamanho constante na tela."""

    def __init__(
        self,
        view: "CartesianGeometryView",
        *,
        label: str,
        coordinate_label: str,
        color: QColor,
        tooltip: str,
    ) -> None:
        super().__init__()
        self.view = view
        self.label = label
        self.coordinate_label = coordinate_label
        self.color = color
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setToolTip(tooltip)

    def boundingRect(self) -> QRectF:  # noqa: N802
        # A largura generosa comporta nomes de cabos sem recortar o texto. O
        # item ignora transformações, portanto estes valores são pixels.
        return QRectF(-8.0, -29.0, 430.0, 52.0)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # noqa: ANN001
        palette = self.view.palette()
        outline = palette.color(QPalette.ColorRole.Base)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(outline, 2.0))
        painter.setBrush(self.color)
        painter.drawEllipse(QPointF(0.0, 0.0), 5.5, 5.5)

        label_font = painter.font()
        label_font.setBold(True)
        painter.setFont(label_font)
        painter.setPen(palette.color(QPalette.ColorRole.Text))
        painter.drawText(QPointF(10.0, -4.0), self.label)

        detail_font = painter.font()
        detail_font.setBold(False)
        detail_font.setPointSizeF(max(detail_font.pointSizeF() - 1.0, 7.0))
        painter.setFont(detail_font)
        secondary = palette.color(QPalette.ColorRole.Text)
        secondary.setAlpha(185)
        painter.setPen(secondary)
        painter.drawText(QPointF(10.0, 12.0), self.coordinate_label)


class CartesianGeometryView(QGraphicsView):
    """Visualização cartesiana com zoom, pan e reenquadramento.

    As coordenadas do modelo não são editadas pelo gráfico. A inversão de
    ``height`` para ``-Y`` é apenas a adaptação necessária para que o eixo Y
    cresça para cima na convenção cartesiana.
    """

    MIN_ZOOM_LEVEL = -12.0
    MAX_ZOOM_LEVEL = 30.0
    ZOOM_FACTOR = 1.2

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.catalog: OpenDssLibraryCatalog | None = None
        self.arrangement: ArrangementDefinition | None = None
        self.geometry: GeometryDefinition | None = None
        self._content_key: tuple[str | None, str | None] = (None, None)
        self._content_bounds = QRectF(-1.0, -1.0, 2.0, 2.0)
        self._point_items: list[_ConductorPointItem] = []
        self._fit_pending = True
        self._camera_modified = False
        self._zoom_level = 0.0
        self._dragging = False
        self._last_mouse_position = QPointF()

        self.setMinimumSize(340, 260)
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
            "Roda: zoom sob o cursor · Arraste: mover gráfico · Duplo clique: enquadrar"
        )

    @property
    def point_count(self) -> int:
        return len(self._point_items)

    @property
    def point_labels(self) -> tuple[str, ...]:
        return tuple(item.label for item in self._point_items)

    @property
    def zoom_level(self) -> float:
        return self._zoom_level

    @property
    def camera_modified(self) -> bool:
        return self._camera_modified

    @staticmethod
    def grid_step_for_scale(scale: float) -> float:
        return _nice_grid_step(72.0 / max(abs(scale), 1.0e-12))

    def set_content(
        self,
        catalog: OpenDssLibraryCatalog,
        arrangement: ArrangementDefinition | None,
        geometry: GeometryDefinition | None = None,
    ) -> None:
        """Atualiza pontos; preserva a câmera quando o item é o mesmo."""

        new_key = (
            None if arrangement is None else arrangement.arrangement_id,
            None if geometry is None else geometry.geometry_id,
        )
        same_content = new_key == self._content_key
        old_transform = self.transform()
        old_center = self.mapToScene(self.viewport().rect().center())

        self.catalog = catalog
        self.arrangement = arrangement
        self.geometry = geometry
        self._content_key = new_key
        self._scene.clear()
        self._point_items.clear()

        points: list[QPointF] = []
        if arrangement is not None:
            for index, position in enumerate(arrangement.positions):
                is_phase = index < arrangement.phase_count
                role = f"F{index + 1}" if is_phase else "N"
                cable_id = (
                    geometry.cable_ids[index]
                    if geometry is not None and index < len(geometry.cable_ids)
                    else None
                )
                cable = catalog.cable(cable_id)
                cable_name = None if cable is None else cable.name
                label = role if geometry is None or not cable_name else f"{role} — {cable_name}"
                coordinate_label = (
                    f"x={_coordinate(position.x)}; y={_coordinate(position.height)} "
                    f"{arrangement.units}"
                )
                tooltip_lines = [
                    f"Papel: {role}",
                    f"X: {_coordinate(position.x)} {arrangement.units}",
                    f"Y (h): {_coordinate(position.height)} {arrangement.units}",
                ]
                if geometry is not None:
                    tooltip_lines.append(
                        "Cabo: " + (cable_name or (f"{cable_id} (ausente)" if cable_id else "não associado"))
                    )
                    if cable is not None:
                        tooltip_lines.append("Tipo: CNData" if cable.is_concentric else "Tipo: WireData")
                color = _PHASE_COLORS[index % len(_PHASE_COLORS)] if is_phase else _NEUTRAL_COLOR
                item = _ConductorPointItem(
                    self,
                    label=label,
                    coordinate_label=coordinate_label,
                    color=color,
                    tooltip="\n".join(tooltip_lines),
                )
                item.setPos(position.x, -position.height)
                self._scene.addItem(item)
                self._point_items.append(item)
                points.append(QPointF(position.x, -position.height))

        self._content_bounds = self._bounds_for_points(points)
        scene_rect = self._roomy_scene_rect(self._content_bounds)
        if same_content:
            scene_rect = scene_rect.united(QRectF(old_center.x(), old_center.y(), 1.0, 1.0))
        self.setSceneRect(scene_rect)

        if same_content and arrangement is not None:
            self.setTransform(old_transform)
            self.centerOn(old_center)
            restored_center = self.mapToScene(self.viewport().rect().center())
            self.translate(
                restored_center.x() - old_center.x(),
                restored_center.y() - old_center.y(),
            )
            self._fit_pending = False
        else:
            self._camera_modified = False
            self._fit_pending = True
            self._schedule_fit()
        self.viewport().update()

    @staticmethod
    def _bounds_for_points(points: list[QPointF]) -> QRectF:
        # A origem sempre participa do primeiro enquadramento para deixar os
        # eixos X=0 e Y=0 imediatamente compreensíveis.
        xs = [0.0, *(point.x() for point in points)]
        ys = [0.0, *(point.y() for point in points)]
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        width = max(right - left, 1.0)
        height = max(bottom - top, 1.0)
        pad_x = max(width * 0.18, 0.5)
        pad_y = max(height * 0.18, 0.5)
        return QRectF(
            left - pad_x,
            top - pad_y,
            width + 2.0 * pad_x,
            height + 2.0 * pad_y,
        )

    @staticmethod
    def _roomy_scene_rect(content: QRectF) -> QRectF:
        span = max(content.width(), content.height(), 1.0)
        margin = max(span * 1000.0, 1000.0)
        return content.adjusted(-margin, -margin, margin, margin)

    def _schedule_fit(self) -> None:
        QTimer.singleShot(0, self._fit_if_pending)

    def _fit_if_pending(self) -> None:
        if not self._fit_pending or not self.isVisible() or self.viewport().width() < 40:
            return
        self.fit_to_content()

    def fit_to_content(self) -> None:
        """Enquadra todos os condutores e a origem cartesiana."""

        self.resetTransform()
        if self.viewport().width() >= 2 and self.viewport().height() >= 2:
            self.fitInView(self._content_bounds, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = 0.0
        self._camera_modified = False
        self._fit_pending = False
        self.viewport().update()

    def zoom_by_steps(self, steps: float, viewport_position: QPointF | QPoint) -> None:
        if not math.isfinite(steps) or steps == 0:
            return
        target = min(max(self._zoom_level + steps, self.MIN_ZOOM_LEVEL), self.MAX_ZOOM_LEVEL)
        applied = target - self._zoom_level
        if abs(applied) < 1.0e-12:
            return
        position = viewport_position.toPoint() if isinstance(viewport_position, QPointF) else viewport_position
        before = self.mapToScene(position)
        factor = self.ZOOM_FACTOR**applied
        self.scale(factor, factor)
        after = self.mapToScene(position)
        # Compensa no próprio transform, sem a quantização inteira das barras
        # de rolagem usada por ``centerOn``. Assim o ponto sob o cursor fica
        # estável inclusive em fatores de zoom altos.
        self.translate(after.x() - before.x(), after.y() - before.y())
        self._zoom_level = target
        self._camera_modified = True
        self._fit_pending = False
        self.viewport().update()

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
        # Transladar o transform conserva o deslocamento fracionário e faz o
        # conteúdo acompanhar exatamente a mão. ``centerOn`` usa barras de
        # rolagem inteiras e acrescentava um pixel espúrio nos dois eixos a
        # cada evento, criando a deriva diagonal observada durante o arraste.
        self.translate(scene_delta.x(), scene_delta.y())
        self._camera_modified = True
        self._fit_pending = False
        self.viewport().update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        degrees = event.angleDelta().y() / 120.0
        self.zoom_by_steps(degrees, event.position())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_mouse_position = QPointF(event.position())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            current = QPointF(event.position())
            self.pan_by_pixels(current - self._last_mouse_position)
            self._last_mouse_position = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.fit_to_content()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._fit_pending:
            self.fit_to_content()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        if self._fit_pending or not self._camera_modified:
            self._fit_pending = True
            self._schedule_fit()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        palette = self.palette()
        painter.fillRect(rect, palette.color(QPalette.ColorRole.Base))
        scale = abs(self.transform().m11())
        step = self.grid_step_for_scale(scale)
        if not math.isfinite(step) or step <= 0:
            return

        grid_color = palette.color(QPalette.ColorRole.Mid)
        grid_color.setAlpha(100)
        grid_pen = QPen(grid_color, 0.0)
        grid_pen.setCosmetic(True)
        painter.setPen(grid_pen)
        first_x = math.floor(rect.left() / step) * step
        first_y = math.floor(rect.top() / step) * step
        line_count = 0
        x = first_x
        while x <= rect.right() + step * 0.5 and line_count < 2000:
            if not math.isclose(x, 0.0, abs_tol=step * 1.0e-9):
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
            line_count += 1
        y = first_y
        while y <= rect.bottom() + step * 0.5 and line_count < 4000:
            if not math.isclose(y, 0.0, abs_tol=step * 1.0e-9):
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step
            line_count += 1

        axis_color = palette.color(QPalette.ColorRole.Text)
        axis_color.setAlpha(210)
        axis_pen = QPen(axis_color, 1.8)
        axis_pen.setCosmetic(True)
        painter.setPen(axis_pen)
        if rect.left() <= 0.0 <= rect.right():
            painter.drawLine(QPointF(0.0, rect.top()), QPointF(0.0, rect.bottom()))
        if rect.top() <= 0.0 <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), 0.0), QPointF(rect.right(), 0.0))

    def drawForeground(self, painter: QPainter, _rect: QRectF) -> None:  # noqa: N802
        painter.save()
        painter.resetTransform()
        palette = self.palette()
        text = palette.color(QPalette.ColorRole.Text)
        muted = QColor(text)
        muted.setAlpha(175)
        painter.setPen(text)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        units = "—" if self.arrangement is None else self.arrangement.units
        title = "Gráfico cartesiano"
        if self.geometry is not None:
            title += f" — {self.geometry.name}"
        elif self.arrangement is not None:
            title += f" — {self.arrangement.name}"
        painter.drawText(QPointF(12.0, 21.0), title)

        font.setBold(False)
        painter.setFont(font)
        painter.setPen(muted)
        painter.drawText(QPointF(12.0, 40.0), f"X [{units}]   ·   Y (h) [{units}]")
        hint = "Roda: zoom · Arraste: pan · Duplo clique: enquadrar"
        metrics = QFontMetrics(font)
        painter.drawText(
            QPointF(max(12.0, self.viewport().width() - metrics.horizontalAdvance(hint) - 12.0), self.viewport().height() - 12.0),
            hint,
        )
        if not self._point_items:
            message = "Nenhum condutor para exibir"
            painter.setPen(muted)
            painter.drawText(
                QPointF(
                    (self.viewport().width() - metrics.horizontalAdvance(message)) / 2.0,
                    self.viewport().height() / 2.0,
                ),
                message,
            )
        painter.restore()


class GeometryCutDialog(QDialog):
    """Janela ampliada do gráfico cartesiano de uma montagem."""

    def __init__(self, catalog: OpenDssLibraryCatalog, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.catalog = catalog
        self.setModal(False)
        self.setWindowTitle("Gráfico cartesiano da montagem")
        self.resize(920, 700)

        layout = QVBoxLayout(self)
        self.preview = CartesianGeometryView(self)
        self.preview.setObjectName("opendss_enlarged_geometry_graph")
        self.preview.setMinimumHeight(410)
        layout.addWidget(self.preview, 1)
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self.table = QTableWidget(self)
        self.table.setObjectName("opendss_geometry_parameters_table")
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(("#", "Papel", "X", "Y (h)", "Unidade", "Cabo"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

    def show_geometry(self, geometry_id: str) -> None:
        geometry = self.catalog.geometry(geometry_id)
        arrangement = None if geometry is None else self.catalog.arrangement(geometry.arrangement_id)
        if geometry is None or arrangement is None:
            self.preview.set_content(self.catalog, None)
            self.table.setRowCount(0)
            self.summary_label.setText("Montagem ou arranjo associado indisponível.")
            self.setWindowTitle("Gráfico cartesiano da montagem")
            self.show()
            self.raise_()
            return

        self.preview.set_content(self.catalog, arrangement, geometry)
        self.table.setRowCount(arrangement.conductor_count)
        for row, position in enumerate(arrangement.positions):
            role = f"F{row + 1}" if row < arrangement.phase_count else "N"
            cable_id = geometry.cable_ids[row] if row < len(geometry.cable_ids) else None
            cable = self.catalog.cable(cable_id)
            values = (
                str(row + 1),
                role,
                _coordinate(position.x),
                _coordinate(position.height),
                arrangement.units,
                cable.name if cable is not None else (cable_id or "—"),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.summary_label.setText(
            f"{geometry.name} — {arrangement.conductor_count} condutor(es), "
            f"{arrangement.phase_count} fase(s), unidades em {arrangement.units}."
        )
        self.setWindowTitle(f"Gráfico cartesiano — {geometry.name}")
        self.show()
        self.raise_()
        self.activateWindow()


# Compatibilidade interna com referências anteriores ao gráfico cartesiano.
CrossSectionWidget = CartesianGeometryView
