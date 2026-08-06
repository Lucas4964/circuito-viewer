"""Seleção dos circuitos que entram na exportação para o OpenDSS."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSignalBlocker
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from .model import CircuitCatalogModel


class OpenDssExportDialog(QDialog):
    """Lista os circuitos do catálogo para escolher **um** a exportar.

    A seleção é única porque o master cria um ``New Circuit``, que energiza um
    alimentador só. Marcar um circuito desmarca o anterior.
    """

    def __init__(self, catalog: CircuitCatalogModel, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Exportar para OpenDSS")
        self.setModal(True)
        self.resize(460, 420)
        self._catalog = catalog

        layout = QVBoxLayout(self)
        instruction = QLabel(
            "Selecione o circuito a exportar. Os trechos comuns e os que "
            "representam chaves vão para arquivos separados; as cargas saem em "
            "mais três arquivos, um por contagem de fases, quando cargas e "
            "patamares estiverem importados. O arquivo master criado no fim "
            "cria o circuito e chama todos eles."
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
            # Só o primeiro nasce marcado: a seleção é única.
            item.setCheckState(
                Qt.CheckState.Checked if index == 0 else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
        layout.addWidget(self.circuit_list, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.circuit_list.itemChanged.connect(self._on_item_changed)
        self._sync_ok_button()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Mantém no máximo um circuito marcado."""

        if item.checkState() != Qt.CheckState.Checked:
            self._sync_ok_button()
            return
        # Desmarcar os demais reentraria neste handler pelo itemChanged.
        blocker = QSignalBlocker(self.circuit_list)
        for row in range(self.circuit_list.count()):
            other = self.circuit_list.item(row)
            if other is not item:
                other.setCheckState(Qt.CheckState.Unchecked)
        del blocker
        self._sync_ok_button()

    def _sync_ok_button(self) -> None:
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
