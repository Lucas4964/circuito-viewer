"""Edição das faixas de níveis de tensão e das cores reservadas.

A tabela é uma lista **ordenada**, e a ordem é significativa: a primeira faixa
que contém o valor é a que vence. É o que deixa "adequada" fechada nos dois
extremos, como o PRODIST a define, sem inventar uma inclusividade por ponta —
daí os botões de subir e descer serem parte do editor e não um enfeite.

Cor e gravidade são colunas independentes de propósito: as duas pontas críticas
têm a mesma gravidade e precisam de cores diferentes, senão o mapa não responde
a pergunta que motivou o modo — alta demais ou baixa demais.
"""

from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .voltage_bands import (
    RESERVED_LABELS,
    ReservedColors,
    VoltageBand,
    VoltageBandTable,
    VoltageBandsError,
    default_voltage_band_table,
)
from .voltage_bands_store import save_voltage_bands


#: Texto que representa o infinito na célula de máximo. O usuário digita um
#: número ou deixa no teto do spin, que vira infinito na leitura.
_INFINITY_THRESHOLD = 9.99


class _ColorButton(QPushButton):
    """Botão que mostra e escolhe uma cor ``#RRGGBB``."""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFlat(True)
        self.setFixedHeight(22)
        self._color = "#000000"
        self.set_color(color)
        self.clicked.connect(self._choose)

    @property
    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        value = QColor(str(color))
        if not value.isValid():
            return
        self._color = value.name().upper()
        self.setText(self._color)
        # Texto claro sobre fundo escuro, e vice-versa, para o hex continuar
        # legível em qualquer cor escolhida.
        ink = "#000000" if value.lightness() > 127 else "#FFFFFF"
        self.setStyleSheet(
            f"background-color: {self._color}; color: {ink};"
            " border: 1px solid palette(mid); border-radius: 3px;"
        )

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Cor da faixa")
        if chosen.isValid():
            self.set_color(chosen.name())


class VoltageBandsDialog(QDialog):
    """Editor modal da tabela de faixas."""

    tableSaved = pyqtSignal(object)

    COLUMNS = ("Nome", "Cor", "Mínimo (pu)", "Máximo (pu)", "Gravidade")

    def __init__(
        self,
        table: VoltageBandTable,
        *,
        storage_path: str | Path | None = None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Faixas de níveis de tensão")
        self.resize(720, 460)
        self._storage_path = storage_path

        layout = QVBoxLayout(self)
        note = QLabel(
            "Os intervalos são fechados nas duas pontas e a primeira faixa que "
            "contém o valor é a que vale — por isso a ordem importa. As faixas "
            "precisam cobrir de 0 pu ao infinito, sem lacuna.",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setObjectName("voltage_bands_table")
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        buttons_row = QHBoxLayout()
        self.add_button = QPushButton("Adicionar", self)
        self.add_button.clicked.connect(self._add_row)
        self.remove_button = QPushButton("Remover", self)
        self.remove_button.clicked.connect(self._remove_row)
        self.up_button = QPushButton("Subir", self)
        self.up_button.clicked.connect(lambda: self._move_row(-1))
        self.down_button = QPushButton("Descer", self)
        self.down_button.clicked.connect(lambda: self._move_row(1))
        self.defaults_button = QPushButton("Restaurar padrão (PRODIST)", self)
        self.defaults_button.clicked.connect(self._restore_defaults)
        for button in (
            self.add_button,
            self.remove_button,
            self.up_button,
            self.down_button,
        ):
            buttons_row.addWidget(button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.defaults_button)
        layout.addLayout(buttons_row)

        reserved_box = QGroupBox("Categorias sem faixa", self)
        reserved_form = QFormLayout(reserved_box)
        self._reserved_buttons: dict[str, _ColorButton] = {}
        hints = {
            "no_line_voltage": (
                "Modo de tensão de linha: a barra tem menos de duas fases "
                "energizadas, então não participa desta medida."
            ),
            "de_energized": "Nenhuma fase energizada: a barra está sem tensão.",
            "no_data": "A barra não entrou no fluxo de potência resolvido.",
        }
        for field_name, label in RESERVED_LABELS.items():
            button = _ColorButton(getattr(table.reserved, field_name), reserved_box)
            button.setObjectName(f"voltage_reserved_{field_name}")
            button.setToolTip(hints[field_name])
            self._reserved_buttons[field_name] = button
            reserved_form.addRow(f"{label}:", button)
        layout.addWidget(reserved_box)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._load(table)

    # -- montagem da tabela ------------------------------------------------

    def _load(self, table: VoltageBandTable) -> None:
        self.table.setRowCount(0)
        for band in table.bands:
            self._append_row(band)
        for field_name, button in self._reserved_buttons.items():
            button.set_color(getattr(table.reserved, field_name))

    def _append_row(self, band: VoltageBand) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(band.label))
        self.table.setCellWidget(row, 1, _ColorButton(band.color, self.table))
        self.table.setCellWidget(row, 2, self._pu_spin(band.minimum_pu))
        self.table.setCellWidget(row, 3, self._pu_spin(band.maximum_pu))
        self.table.setCellWidget(row, 4, self._severity_spin(band.severity))

    def _pu_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self.table)
        spin.setDecimals(4)
        spin.setRange(0.0, 10.0)
        spin.setSingleStep(0.01)
        # O teto do spin é o infinito: é como a faixa mais alta se escreve, e
        # digitar "infinito" numa caixa de número não seria melhor.
        spin.setSpecialValueText("")
        spin.setSuffix(" pu")
        spin.setValue(10.0 if math.isinf(value) else float(value))
        spin.setToolTip(
            "10 pu significa 'sem teto': é assim que a faixa mais alta se escreve."
        )
        return spin

    def _severity_spin(self, value: int) -> QSpinBox:
        spin = QSpinBox(self.table)
        spin.setRange(0, 9)
        spin.setValue(int(value))
        spin.setToolTip(
            "Desempate entre as medidas da mesma barra: a pior vence. Não tem "
            "relação com a cor — subtensão e sobretensão críticas têm a mesma "
            "gravidade e cores diferentes de propósito."
        )
        return spin

    # -- edição ------------------------------------------------------------

    def _add_row(self) -> None:
        self._append_row(VoltageBand("Nova faixa", "#888888", 0.0, 1.0, 0))
        self.table.selectRow(self.table.rowCount() - 1)

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _move_row(self, offset: int) -> None:
        row = self.table.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        band = self._row_band(row)
        if band is None:
            return
        self.table.removeRow(row)
        self.table.insertRow(target)
        self.table.setItem(target, 0, QTableWidgetItem(band.label))
        self.table.setCellWidget(target, 1, _ColorButton(band.color, self.table))
        self.table.setCellWidget(target, 2, self._pu_spin(band.minimum_pu))
        self.table.setCellWidget(target, 3, self._pu_spin(band.maximum_pu))
        self.table.setCellWidget(target, 4, self._severity_spin(band.severity))
        self.table.selectRow(target)

    def _restore_defaults(self) -> None:
        self._load(default_voltage_band_table())

    # -- leitura e gravação ------------------------------------------------

    def _row_band(self, row: int) -> VoltageBand | None:
        name_item = self.table.item(row, 0)
        label = "" if name_item is None else name_item.text()
        color = self.table.cellWidget(row, 1).color
        minimum = float(self.table.cellWidget(row, 2).value())
        raw_maximum = float(self.table.cellWidget(row, 3).value())
        maximum = math.inf if raw_maximum > _INFINITY_THRESHOLD else raw_maximum
        severity = int(self.table.cellWidget(row, 4).value())
        try:
            return VoltageBand(label, color, minimum, maximum, severity)
        except VoltageBandsError as exc:
            QMessageBox.warning(self, "Faixas de níveis de tensão", str(exc))
            return None

    def current_table(self) -> VoltageBandTable | None:
        """A tabela montada a partir da tela, ou ``None`` se ela não vale."""

        bands: list[VoltageBand] = []
        for row in range(self.table.rowCount()):
            band = self._row_band(row)
            if band is None:
                return None
            bands.append(band)
        reserved = ReservedColors(
            **{
                field_name: button.color
                for field_name, button in self._reserved_buttons.items()
            }
        )
        try:
            return VoltageBandTable(tuple(bands), reserved)
        except VoltageBandsError as exc:
            QMessageBox.warning(self, "Faixas de níveis de tensão", str(exc))
            return None

    def _save(self) -> None:
        table = self.current_table()
        if table is None:
            # A janela continua aberta com o que o usuário digitou: fechar aqui
            # apagaria a edição junto com o erro.
            return
        try:
            save_voltage_bands(table, self._storage_path)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Faixas de níveis de tensão",
                f"Não foi possível gravar o arquivo: {exc}",
            )
            return
        self.tableSaved.emit(table)
        self.accept()
