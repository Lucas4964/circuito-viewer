"""Escolha do modelo de cargas entre a leitura do banco e a instalação no mapa.

O padrão Interplan é o comportamento de sempre: escolhê-lo não muda nada no
resultado. O modo Energia ainda é só a escolha registrada; o que ele altera na
modelagem será definido depois.
"""

from __future__ import annotations

from enum import Enum

from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)


class LoadModelMode(str, Enum):
    INTERPLAN = "interplan"
    ENERGIA = "energia"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    LoadModelMode.INTERPLAN: "Padrão Interplan",
    LoadModelMode.ENERGIA: "Energia",
}

_DESCRIPTIONS = {
    LoadModelMode.INTERPLAN: "Mantém a modelagem atual das cargas.",
    LoadModelMode.ENERGIA: "Modelagem das cargas por energia.",
}


class LoadModelModeDialog(QDialog):
    """Pergunta o modelo de cargas; começa sempre no padrão Interplan."""

    def __init__(
        self,
        parent=None,  # noqa: ANN001
        *,
        initial: LoadModelMode = LoadModelMode.INTERPLAN,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modelo de cargas")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Qual modelo de cargas deseja usar?", self))

        self._group = QButtonGroup(self)
        self.buttons: dict[LoadModelMode, QRadioButton] = {}
        for mode in LoadModelMode:
            button = QRadioButton(mode.label, self)
            button.setToolTip(_DESCRIPTIONS[mode])
            button.setChecked(mode is initial)
            self._group.addButton(button)
            self.buttons[mode] = button
            layout.addWidget(button)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def selected_mode(self) -> LoadModelMode:
        for mode, button in self.buttons.items():
            if button.isChecked():
                return mode
        return LoadModelMode.INTERPLAN
