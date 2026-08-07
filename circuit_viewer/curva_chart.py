"""Gráfico das 24 horas da curva em edição, desenhado à mão.

O projeto não tem biblioteca de gráficos — as dependências são apenas numpy,
PyQt6 e pyproj —, e acrescentar uma por causa de 24 pontos seria desproporcional.
O desenho segue o idioma de ``graphics.py``: ``QPainter`` dentro de
``try/finally``, um único ``QPainterPath`` para a linha, e ``drawLine`` para
eixos e grades.

**Nenhuma cor literal.** Todas saem de ``self.palette()`` no momento de pintar.
O tema é aplicado por paleta (``theme.apply_theme``), não por folha de estilo:
uma cor fixa aqui ficaria ilegível no tema escuro, e é justamente o tipo de
detalhe que passa despercebido até alguém trocar o tema.
"""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QPainter, QPainterPath, QPalette, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from .curvas import HOURLY_CURVE_POINT_COUNT


_MARGIN_LEFT = 56
_MARGIN_TOP = 12
_MARGIN_RIGHT = 14
_MARGIN_BOTTOM = 28

# Abaixo desta largura por hora, os marcadores viram uma mancha e atrapalham
# mais do que ajudam.
_MARKER_MIN_SPACING = 8.0
_MARKER_RADIUS = 3.0

_EMPTY_TEXT = "Preencha os valores para ver o gráfico."


class CurveChartWidget(QWidget):
    """Desenha os 24 pontos da curva selecionada."""

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._values: list[float | None] = [None] * HOURLY_CURVE_POINT_COUNT
        self.setMinimumHeight(180)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

    @property
    def values(self) -> tuple[float | None, ...]:
        return tuple(self._values)

    def set_values(self, values: Sequence[float | None]) -> None:
        """Troca os valores e repinta apenas se algo mudou.

        ``update()`` e não ``repaint()``: o Qt agrupa os pedidos, então uma
        colagem — que emite um único ``dataChanged`` — pinta uma vez só.
        """

        new_values = list(values)
        if new_values == self._values:
            return
        self._values = new_values
        self.update()

    def clear(self) -> None:
        self.set_values([None] * HOURLY_CURVE_POINT_COUNT)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        # ``apply_theme`` já chama update() em todos os widgets, mas uma paleta
        # trocada por outro caminho também precisa redesenhar.
        if event.type() == QEvent.Type.PaletteChange:
            self.update()
        super().changeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            palette = self.palette()
            plot = self.rect().adjusted(
                _MARGIN_LEFT,
                _MARGIN_TOP,
                -_MARGIN_RIGHT,
                -_MARGIN_BOTTOM,
            )
            if plot.width() <= 0 or plot.height() <= 0:
                return
            painter.fillRect(plot, palette.color(QPalette.ColorRole.Base))

            finite = [value for value in self._values if value is not None]
            if not finite:
                self._draw_axes(painter, palette, plot)
                painter.setPen(
                    palette.color(QPalette.ColorRole.PlaceholderText)
                )
                painter.drawText(
                    plot,
                    Qt.AlignmentFlag.AlignCenter,
                    _EMPTY_TEXT,
                )
                return

            low, high = self._scale(finite)
            self._draw_axes(painter, palette, plot)
            self._draw_grid(painter, palette, plot, low, high)
            self._draw_hour_labels(painter, palette, plot)
            self._draw_zero_line(painter, palette, plot, low, high)
            self._draw_curve(painter, palette, plot, low, high)
        finally:
            painter.end()

    @staticmethod
    def _scale(finite: Sequence[float]) -> tuple[float, float]:
        """Faixa vertical do gráfico, com folga.

        Quando todos os valores são iguais — inclusive todos zero — não há faixa
        alguma, e dividir por ela seria uma divisão por zero. Nesse caso a reta
        é centralizada numa faixa artificial proporcional ao próprio valor.
        """

        low = min(finite)
        high = max(finite)
        if low == high:
            span = abs(low) * 0.5 or 1.0
            return low - span, high + span
        margin = (high - low) * 0.05
        return low - margin, high + margin

    def _y_for(self, plot, value: float, low: float, high: float) -> float:  # noqa: ANN001
        ratio = (value - low) / (high - low)
        return plot.bottom() - ratio * plot.height()

    def _x_for(self, plot, hour_index: int) -> float:  # noqa: ANN001
        steps = HOURLY_CURVE_POINT_COUNT - 1
        return plot.left() + plot.width() * hour_index / steps

    def _draw_axes(self, painter: QPainter, palette: QPalette, plot) -> None:  # noqa: ANN001
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
        painter.drawRect(plot)

    def _draw_grid(  # noqa: ANN001
        self,
        painter: QPainter,
        palette: QPalette,
        plot,
        low: float,
        high: float,
    ) -> None:
        pen = QPen(palette.color(QPalette.ColorRole.Mid), 1, Qt.PenStyle.DotLine)
        painter.setPen(pen)
        metrics = painter.fontMetrics()
        for ratio in (0.0, 0.5, 1.0):
            value = low + (high - low) * ratio
            y = self._y_for(plot, value, low, high)
            if 0.0 < ratio < 1.0:
                painter.drawLine(plot.left(), int(y), plot.right(), int(y))
            label = f"{value:.4g}"
            painter.setPen(palette.color(QPalette.ColorRole.Text))
            painter.drawText(
                plot.left() - _MARGIN_LEFT + 4,
                int(y) + metrics.ascent() // 2,
                label,
            )
            painter.setPen(pen)

    def _draw_hour_labels(  # noqa: ANN001
        self,
        painter: QPainter,
        palette: QPalette,
        plot,
    ) -> None:
        painter.setPen(palette.color(QPalette.ColorRole.Text))
        metrics = painter.fontMetrics()
        # Com pouca largura, cinco rótulos se sobrepõem; três ainda situam.
        hours = (1, 6, 12, 18, 24)
        if metrics.horizontalAdvance("24") * len(hours) * 2 > plot.width():
            hours = (1, 12, 24)
        for hour in hours:
            x = self._x_for(plot, hour - 1)
            painter.drawLine(int(x), plot.bottom(), int(x), plot.bottom() + 4)
            text = str(hour)
            painter.drawText(
                int(x) - metrics.horizontalAdvance(text) // 2,
                plot.bottom() + 6 + metrics.ascent(),
                text,
            )

    def _draw_zero_line(  # noqa: ANN001
        self,
        painter: QPainter,
        palette: QPalette,
        plot,
        low: float,
        high: float,
    ) -> None:
        """Marca o zero quando a curva atravessa para valores negativos.

        Sem essa referência, uma curva de geração e uma de consumo têm o mesmo
        desenho — o sinal só apareceria nos rótulos do eixo.
        """

        if not low < 0.0 < high:
            return
        y = self._y_for(plot, 0.0, low, high)
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
        painter.drawLine(plot.left(), int(y), plot.right(), int(y))

    def _draw_curve(  # noqa: ANN001
        self,
        painter: QPainter,
        palette: QPalette,
        plot,
        low: float,
        high: float,
    ) -> None:
        highlight = palette.color(QPalette.ColorRole.Highlight)
        path = QPainterPath()
        started = False
        points: list[QPointF] = []
        for hour_index, value in enumerate(self._values):
            if value is None:
                # Uma hora vazia **interrompe** o traço: emendar por cima dela
                # desenharia um trecho reto que a curva não tem.
                started = False
                continue
            point = QPointF(
                self._x_for(plot, hour_index),
                self._y_for(plot, value, low, high),
            )
            points.append(point)
            if started:
                path.lineTo(point)
            else:
                path.moveTo(point)
                started = True

        painter.setPen(QPen(highlight, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Um único drawPath, como as camadas de trechos do mapa.
        painter.drawPath(path)

        spacing = plot.width() / (HOURLY_CURVE_POINT_COUNT - 1)
        if spacing >= _MARKER_MIN_SPACING:
            painter.setBrush(highlight)
            for point in points:
                painter.drawEllipse(point, _MARKER_RADIUS, _MARKER_RADIUS)
