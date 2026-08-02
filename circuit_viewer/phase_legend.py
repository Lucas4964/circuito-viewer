"""Legenda compacta do modo de visualização por fases."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .phase_config import PHASE_COLORS, UNMAPPED_PHASE_COLOR


class PhaseLegend(QFrame):
    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setObjectName("phase_legend")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "QFrame#phase_legend {"
            " background: palette(window);"
            " border: 1px solid palette(mid);"
            " border-radius: 4px;"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        title = QLabel("Trechos por fases", self)
        title.setStyleSheet("font-weight: bold; border: 0px;")
        layout.addWidget(title)

        self.labels: list[QLabel] = []
        for color, caption in zip(
            (*PHASE_COLORS, UNMAPPED_PHASE_COLOR),
            ("1 fase", "2 fases", "3 fases", "Sem relação (0)"),
            strict=True,
        ):
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            swatch = QLabel(row)
            swatch.setFixedSize(14, 10)
            swatch.setStyleSheet(
                f"background-color: {color}; border: 1px solid palette(mid);"
            )
            label = QLabel(caption, row)
            row_layout.addWidget(swatch)
            row_layout.addWidget(label, 1)
            layout.addWidget(row)
            self.labels.append(label)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_unmapped_count(self, count: int) -> None:
        self.labels[3].setText(f"Sem relação ({max(0, int(count)):n})")
        self.adjustSize()
