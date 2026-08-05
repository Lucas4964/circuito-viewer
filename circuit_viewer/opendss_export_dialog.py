"""Seleção dos circuitos que entram na exportação para o OpenDSS."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from .model import CircuitCatalogModel


class OpenDssExportDialog(QDialog):
    """Lista os circuitos do catálogo com caixas de seleção."""

    def __init__(self, catalog: CircuitCatalogModel, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Exportar para OpenDSS")
        self.setModal(True)
        self.resize(460, 420)
        self._catalog = catalog

        layout = QVBoxLayout(self)
        instruction = QLabel(
            "Selecione os circuitos a exportar. Os trechos comuns e os que "
            "representam chaves vão para arquivos separados; as cargas "
            "monofásicas e as bifásicas saem em mais dois arquivos, quando "
            "cargas e patamares estiverem importados."
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        self.circuit_list = QListWidget(self)
        self.circuit_list.setObjectName("opendss_circuit_list")
        for index in range(len(catalog)):
            definition = catalog.definition(index)
            parts = [definition.circuit_id]
            if definition.code.strip():
                parts.append(definition.code.strip())
            caption = " — ".join(parts)
            if definition.nominal_voltage.strip():
                caption += f" ({definition.nominal_voltage.strip()} kV)"
            item = QListWidgetItem(caption, self.circuit_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, index)
        layout.addWidget(self.circuit_list, 1)

        selection_buttons = QHBoxLayout()
        self.check_all_button = QPushButton("Marcar todos", self)
        self.check_all_button.clicked.connect(lambda: self._set_all(True))
        self.uncheck_all_button = QPushButton("Desmarcar todos", self)
        self.uncheck_all_button.clicked.connect(lambda: self._set_all(False))
        selection_buttons.addWidget(self.check_all_button)
        selection_buttons.addWidget(self.uncheck_all_button)
        selection_buttons.addStretch(1)
        layout.addLayout(selection_buttons)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.circuit_list.itemChanged.connect(self._sync_ok_button)
        self._sync_ok_button()

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.circuit_list.count()):
            self.circuit_list.item(row).setCheckState(state)

    def _sync_ok_button(self, *args) -> None:  # noqa: ANN002
        del args
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            button.setEnabled(bool(self.selected_circuit_indices()))

    def selected_circuit_indices(self) -> tuple[int, ...]:
        return tuple(
            int(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self.circuit_list.count())
            if (item := self.circuit_list.item(row)).checkState()
            == Qt.CheckState.Checked
        )
