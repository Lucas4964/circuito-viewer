"""Tabelas das cargas e da GD agregadas num trafo, no modo de cargas Energia.

O símbolo de carga e o de gerador na barra de MT são só o agregado. Cada um abre,
pelo painel de propriedades, uma tabela com uma linha por carga conectada naquele
ponto — UC da BT, UC da MT e ponto de iluminação pública — ou por gerador.

As linhas são montadas por funções puras (:func:`consumer_rows`,
:func:`generator_rows`), testáveis sem janela; a janela só as exibe, com
ordenação numérica e ``Ctrl+C`` no formato das planilhas.
"""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from .blocks_window import BlockTableView
from .calculation_levels import CalculationLevelSchedule
from .energy_demand import STATUS_LABELS, ConsumerDemand, TransformerDemand
from .phase_config import PhaseConfiguration
from .table_columns import EXCEL_LIKE_TABLE_STYLE, enable_interactive_columns
from .transformer_consumers import (
    CONSUMER_KIND_IP,
    TransformerConsumersRecord,
)


CLASS_LABELS = {-1: "A4", 0: "RES", 1: "COM", 2: "IND", 3: "RUR", 4: "OUT", 5: "IP"}

# (texto exibido, valor de ordenação): número ordena como número.
Cell = tuple[str, object]
Row = tuple[Cell, ...]


def _text(value: object) -> Cell:
    text = "" if value is None else str(value)
    return text, text.casefold()


def _number(value: float | None, decimals: int) -> Cell:
    if value is None:
        return "", float("-inf")
    return f"{value:.{decimals}f}", float(value)


def _class_id(value: int | None) -> Cell:
    """A ``CLASSE`` do MDB, só o número."""

    if value is None:
        return "", float("-inf")
    return str(value), float(value)


def _class_label(value: int | None) -> Cell:
    return _text(None if value is None else CLASS_LABELS.get(value, ""))


def _demand_curve(demand: ConsumerDemand | None, *, generation: bool):  # noqa: ANN202
    if demand is None:
        return None
    return demand.generation_curve if generation else demand.load_curve


def _curve_id(demand: ConsumerDemand | None, *, generation: bool = False) -> Cell:
    """O ``CAT_ID`` da categoria de curva do MDB, só o número."""

    curve = _demand_curve(demand, generation=generation)
    if curve is None:
        return "", float("-inf")
    category_id = curve.category_id
    try:
        return category_id, float(category_id)
    except ValueError:
        return category_id, float("inf")


def _curve_name(demand: ConsumerDemand | None, *, generation: bool = False) -> Cell:
    """O ``CAT_DESC`` da categoria de curva."""

    curve = _demand_curve(demand, generation=generation)
    return _text(None if curve is None else curve.category.description)


def _phases(raw: str, phase_configuration: PhaseConfiguration | None) -> Cell:
    """As fases do consumidor pelo ``fases2.json`` (``7`` → ``DE``)."""

    letters = (
        None
        if phase_configuration is None
        else phase_configuration.phase_letters_for_value(raw)
    )
    return _text("".join(letters) if letters else "")


def level_names(schedule: CalculationLevelSchedule | None) -> tuple[str, ...]:
    if schedule is None:
        return ("Patamar 0", "Patamar 1", "Patamar 2", "Patamar 3")
    return tuple(schedule.level(row).name for row in range(len(schedule)))


def _levels(values: Sequence[float], count: int) -> tuple[Cell, ...]:
    if not values:
        return tuple(("", float("-inf")) for _ in range(count))
    return tuple(_number(value, 3) for value in values)


def consumer_rows(
    record: TransformerConsumersRecord,
    transformer: TransformerDemand | None,
    phase_configuration: PhaseConfiguration | None = None,
) -> tuple[tuple[str, ...], tuple[Row, ...]]:
    """Uma linha por carga do secundário, com o cálculo quando houver.

    ``CLASSE`` e ``CAT_ID`` trazem só o código, com o nome da coluna do MDB;
    a descrição de cada um vem na coluna seguinte. ``Fases`` é o ``FASES2``
    traduzido pelo ``fases2.json`` (a tabela dos consumidores).
    """

    names = level_names(None if transformer is None else transformer.schedule)
    headers = (
        "Tipo", "ID", "Código", "Nome", "CLASSE", "Descrição da classe",
        "FASES2", "Fases", "Consumo (kWh)", "CAT_ID", "Curva de carga",
        "Sorteio (%)", "D (kW)", "Status",
        *(f"P {name} (kW)" for name in names),
    )
    demands = {} if transformer is None else {
        (item.consumer.kind, item.consumer.consumer_id): item for item in transformer.consumers
    }
    rows: list[Row] = []
    for consumer in record.consumers:
        demand = demands.get((consumer.kind, consumer.consumer_id))
        consumer_class = 5 if consumer.kind == CONSUMER_KIND_IP else consumer.consumer_class
        rows.append(
            (
                _text(consumer.kind),
                _text(consumer.consumer_id),
                _text(consumer.code),
                _text(consumer.name),
                _class_id(consumer_class),
                _class_label(consumer_class),
                _text(consumer.phases),
                _phases(consumer.phases, phase_configuration),
                _number(consumer.consumption_kwh, 2),
                _curve_id(demand),
                _curve_name(demand),
                _number(None if demand is None else demand.draw_percent, 2),
                _number(None if demand is None else demand.demand_kw, 3),
                _text("" if demand is None else demand.status),
                *_levels(() if demand is None else demand.load_levels, len(names)),
            )
        )
    return headers, tuple(rows)


def generator_rows(
    record: TransformerConsumersRecord,
    transformer: TransformerDemand | None,
) -> tuple[tuple[str, ...], tuple[Row, ...]]:
    """Uma linha por gerador da CARGA, com a G repartida quando houver cálculo."""

    names = level_names(None if transformer is None else transformer.schedule)
    headers = (
        "Tipo", "GERADOR_ID", "Código", "UC", "TIPOGD", "Injetada (kWh)",
        "CAT_ID", "Curva de geração", "G (kW)", "Status da UC",
        *(f"G {name} (kW)" for name in names),
    )
    shares = {} if transformer is None else {
        (item.generator.kind, item.generator.generator_id): item for item in transformer.generators
    }
    demands = {} if transformer is None else {
        (item.consumer.kind, item.consumer.consumer_id): item for item in transformer.consumers
    }
    rows: list[Row] = []
    for generator in record.generators:
        share = shares.get((generator.kind, generator.generator_id))
        demand = demands.get((generator.kind, generator.consumer_id))
        rows.append(
            (
                _text(generator.kind),
                _text(generator.generator_id),
                _text(generator.code),
                _text(generator.consumer_id),
                _number(None if generator.generation_type is None else float(generator.generation_type), 0),
                _number(generator.generation_kwh, 2),
                _curve_id(demand, generation=True),
                _curve_name(demand, generation=True),
                _number(None if share is None else share.generation_kw, 3),
                _text("" if demand is None else demand.status),
                *_levels(() if share is None else share.levels, len(names)),
            )
        )
    return headers, tuple(rows)


def status_summary(transformer: TransformerDemand | None) -> str:
    if transformer is None:
        return "Sem cálculo do modo Energia para este ponto."
    counts: dict[str, int] = {}
    for item in transformer.consumers:
        counts[item.status] = counts.get(item.status, 0) + 1
    return "; ".join(
        f"{count:n} {status}" for status, count in sorted(counts.items())
    )


class EnergyRowsTableModel(QAbstractTableModel):
    """Linhas prontas: o texto exibido e o valor que ordena."""

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._headers: tuple[str, ...] = ()
        self._rows: tuple[Row, ...] = ()

    def set_rows(self, headers: Sequence[str], rows: Sequence[Row]) -> None:
        self.beginResetModel()
        self._headers = tuple(headers)
        self._rows = tuple(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self._headers)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201
        if not index.isValid():
            return None
        text, key = self._rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return text
        if role == Qt.ItemDataRole.UserRole:
            return key
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() < len(self._headers) and self._headers[index.column()].startswith("Status"):
                return STATUS_LABELS.get(text, text) or None
            return text or None
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(key, float):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self._headers[section] if 0 <= section < len(self._headers) else None
        return None


class _SortProxy(QSortFilterProxyModel):
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        a = self.sourceModel().data(left, Qt.ItemDataRole.UserRole)
        b = self.sourceModel().data(right, Qt.ItemDataRole.UserRole)
        if type(a) is type(b):
            return bool(a < b)
        return str(a) < str(b)


class TransformerTableWindow(QDialog):
    """Janela não modal reaproveitada a cada "Abrir tabela…"."""

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setModal(False)
        self.resize(1100, 460)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        layout = QVBoxLayout(self)
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary_label)
        self.table_model = EnergyRowsTableModel(self)
        self.proxy = _SortProxy(self)
        self.proxy.setSourceModel(self.table_model)
        self.table = BlockTableView(self)
        self.table.setObjectName("transformer_table")
        self.table.setModel(self.proxy)
        enable_interactive_columns(self.table, always_refit=True)
        self.table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fechar")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def show_rows(
        self,
        title: str,
        summary: str,
        headers: Sequence[str],
        rows: Sequence[Row],
    ) -> None:
        self.setWindowTitle(title)
        self.summary_label.setText(summary)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        self.table_model.set_rows(headers, rows)
        self.show()
        self.raise_()
        self.activateWindow()
