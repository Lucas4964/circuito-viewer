"""Legenda e controles do modo de visualização por níveis de tensão.

Diferente da :class:`~circuit_viewer.phase_legend.PhaseLegend`, esta é
**interativa**: carrega o seletor de grandeza e o de patamar. Um filho do
``viewport()`` recebe cliques normalmente — a legenda de fases só não recebe
porque pediu, com ``WA_TransparentForMouseEvents``.

Duas armadilhas da sobreposição sobre o canvas, tratadas aqui e não em quem usa:
a roda do mouse sobre um ``QComboBox`` trocaria o patamar em vez de dar zoom, e
o foco de teclado num combo tiraria o pan com Espaço da vista. Os dois combos
recusam foco e a roda é engolida antes de chegar neles.
"""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .voltage_bands import VoltageQuantity


class VoltageLegend(QFrame):
    """Faixas, contagens e os dois seletores do modo."""

    quantityChanged = pyqtSignal(object)
    stepChanged = pyqtSignal(int)

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setObjectName("voltage_legend")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "QFrame#voltage_legend {"
            " background: palette(window);"
            " border: 1px solid palette(mid);"
            " border-radius: 4px;"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        title = QLabel("Níveis de tensão", self)
        title.setStyleSheet("font-weight: bold; border: 0px;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 2, 0, 4)
        form.setSpacing(4)
        self.quantity_combo = self._combo("voltage_legend_quantity")
        for quantity in VoltageQuantity:
            self.quantity_combo.addItem(quantity.label, quantity)
        self.quantity_combo.setToolTip(
            "Qual tensão a cor representa. No modo de linha, a barra sem par de "
            "fases não participa da medida e fica com a cor reservada."
        )
        self.quantity_combo.currentIndexChanged.connect(self._emit_quantity)
        form.addRow("Grandeza:", self.quantity_combo)

        self.step_combo = self._combo("voltage_legend_step")
        self.step_combo.setToolTip("Patamar de carga que a cor representa.")
        self.step_combo.currentIndexChanged.connect(self._emit_step)
        form.addRow("Patamar:", self.step_combo)
        layout.addLayout(form)

        self._entries = QWidget(self)
        self._entries_layout = QVBoxLayout(self._entries)
        self._entries_layout.setContentsMargins(0, 0, 0, 0)
        self._entries_layout.setSpacing(3)
        layout.addWidget(self._entries)
        self._rows: list[tuple[QWidget, QLabel, QLabel]] = []
        self.hide()

    # -- construção dos controles -----------------------------------------

    def _combo(self, object_name: str) -> QComboBox:
        combo = QComboBox(self)
        combo.setObjectName(object_name)
        # Sem foco o Espaço continua sendo o pan da vista, e não a abertura do
        # popup; o clique e o popup seguem funcionando.
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        combo.installEventFilter(self)
        return combo

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel and watched in (
            self.quantity_combo,
            self.step_combo,
        ):
            # A roda sobre o painel é zoom do mapa, nunca troca de patamar em
            # silêncio.
            return True
        return super().eventFilter(watched, event)

    # -- estado -----------------------------------------------------------

    def set_step_names(self, names: Sequence[str]) -> None:
        """Rótulos dos patamares, na ordem de NPAT."""

        previous = self.step_combo.currentIndex()
        blocked = self.step_combo.blockSignals(True)
        try:
            self.step_combo.clear()
            for step, name in enumerate(names):
                self.step_combo.addItem(name, step)
            if 0 <= previous < self.step_combo.count():
                self.step_combo.setCurrentIndex(previous)
        finally:
            self.step_combo.blockSignals(blocked)

    @property
    def quantity(self) -> VoltageQuantity:
        value = self.quantity_combo.currentData()
        return value if isinstance(value, VoltageQuantity) else VoltageQuantity.PHASE

    @property
    def step(self) -> int:
        return max(0, self.step_combo.currentIndex())

    def set_entries(
        self,
        labels: Sequence[str],
        colors: Sequence[str],
        counts: Sequence[int],
    ) -> None:
        """Uma linha por categoria; as vazias somem para o painel não crescer."""

        visible = [
            (label, color, int(count))
            for label, color, count in zip(labels, colors, counts, strict=True)
            if count
        ]
        while len(self._rows) < len(visible):
            self._rows.append(self._create_row())
        for index, (row, swatch, caption) in enumerate(self._rows):
            if index >= len(visible):
                row.setVisible(False)
                continue
            label, color, count = visible[index]
            swatch.setStyleSheet(
                f"background-color: {color}; border: 1px solid palette(mid);"
            )
            caption.setText(f"{label} ({count:n})")
            row.setVisible(True)
        self.adjustSize()

    def _create_row(self) -> tuple[QWidget, QLabel, QLabel]:
        row = QWidget(self._entries)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        swatch = QLabel(row)
        swatch.setFixedSize(14, 10)
        caption = QLabel(row)
        row_layout.addWidget(swatch)
        row_layout.addWidget(caption, 1)
        self._entries_layout.addWidget(row)
        return row, swatch, caption

    # -- sinais -----------------------------------------------------------

    def _emit_quantity(self) -> None:
        self.quantityChanged.emit(self.quantity)

    def _emit_step(self) -> None:
        self.stepChanged.emit(self.step)
