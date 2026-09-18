"""Janela não modal da seleção a jusante de um ponto da rede.

A janela só apresenta: a região vem pronta de
:func:`~circuit_viewer.downstream_selection.select_downstream`, sempre completa,
e os filtros por tipo escolhem o que se mostra — na tabela, no CSV e no destaque
do mapa. Filtrar aqui, e não na travessia, é o que permite trocar de filtro sem
recalcular nada e sem que um consumidor futuro herde a omissão.
"""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .blocks_window import BlockTableView
from .downstream_selection import DownstreamSelection
from .downstream_table import (
    BARRA,
    CAPACITOR,
    CARGA,
    CHAVE,
    DOWNSTREAM_KIND_LABELS,
    DOWNSTREAM_KINDS,
    DOWNSTREAM_LENGTH_COLUMN,
    DOWNSTREAM_NUMERIC_COLUMNS,
    DOWNSTREAM_TABLE_HEADERS,
    GERADOR,
    HIGHLIGHTABLE_KINDS,
    REGULADOR,
    TRECHO,
    DownstreamRow,
    downstream_rows,
    downstream_summary,
    downstream_table_values,
)
from .phase_config import PhaseConfiguration
from .table_columns import EXCEL_LIKE_TABLE_STYLE, enable_interactive_columns

#: Fração da tela que a janela pode ocupar ao se ajustar à tabela.
_MAX_SCREEN_FRACTION = 0.9


class DownstreamTableModel(QAbstractTableModel):
    HEADERS = DOWNSTREAM_TABLE_HEADERS
    NUMERIC_COLUMNS = DOWNSTREAM_NUMERIC_COLUMNS

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._rows: tuple[DownstreamRow, ...] = ()

    def set_rows(self, rows: Iterable[DownstreamRow]) -> None:
        self.beginResetModel()
        self._rows = tuple(rows)
        self.endResetModel()

    @property
    def rows(self) -> tuple[DownstreamRow, ...]:
        return self._rows

    def row_at(self, row: int) -> DownstreamRow:
        return self._rows[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):  # noqa: ANN201
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid():
            return None
        column = index.column()
        value = downstream_table_values(self._rows[index.row()])[column]
        # UserRole é a chave de ordenação: valor cru, para o proxy comparar
        # grandeza e não texto formatado.
        if role == Qt.ItemDataRole.UserRole:
            if value is None:
                return float("inf") if column in self.NUMERIC_COLUMNS else ""
            return value
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in self.NUMERIC_COLUMNS:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return None
        if role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        if value is None or value == "":
            return "—"
        if column == DOWNSTREAM_LENGTH_COLUMN:
            return f"{float(value):.2f}"
        if isinstance(value, int):
            return f"{value:n}"
        return str(value)


class DownstreamSortProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setDynamicSortFilter(True)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        source = self.sourceModel()
        if (
            isinstance(source, DownstreamTableModel)
            and left.column() == right.column()
            and left.column() in source.NUMERIC_COLUMNS
        ):
            left_value = source.data(left, Qt.ItemDataRole.UserRole)
            right_value = source.data(right, Qt.ItemDataRole.UserRole)
            return bool(left_value < right_value)
        return super().lessThan(left, right)


class DownstreamWindow(QDialog):
    """A região a jusante: origem, filtros, resumo, tabela e ocorrências."""

    useMapSelectionRequested = pyqtSignal()
    beyondOpenSwitchesChanged = pyqtSignal(bool)
    kindsChanged = pyqtSignal()
    highlightRequested = pyqtSignal()
    frameRequested = pyqtSignal()
    exportCsvRequested = pyqtSignal()
    rowActivated = pyqtSignal(object)
    selectionCleared = pyqtSignal()
    closed = pyqtSignal()

    def __init__(
        self,
        parent=None,  # noqa: ANN001
        *,
        phase_configuration: PhaseConfiguration | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("downstream_window")
        self.setWindowTitle("Seleção a jusante")
        self.setModal(False)
        # Largura provisória: na primeira região a janela se ajusta à tabela.
        self.resize(640, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._selection: DownstreamSelection | None = None
        self._phase_configuration = phase_configuration
        self._width_fitted = False

        layout = QVBoxLayout(self)

        origin_row = QHBoxLayout()
        self.origin_label = QLabel(
            "Selecione uma barra ou um trecho no mapa, ou clique com o botão "
            "direito sobre um deles e escolha \"Selecionar a jusante daqui\"."
        )
        self.origin_label.setWordWrap(True)
        origin_row.addWidget(self.origin_label, 1)
        self.use_map_button = QPushButton("Usar seleção do mapa", self)
        self.use_map_button.setObjectName("downstream_use_map_button")
        self.use_map_button.setToolTip(
            "Tomar como origem a barra ou o trecho selecionado no mapa"
        )
        self.use_map_button.clicked.connect(self.useMapSelectionRequested)
        origin_row.addWidget(self.use_map_button)
        layout.addLayout(origin_row)

        self.beyond_check = QCheckBox("Incluir além de chaves abertas", self)
        self.beyond_check.setObjectName("downstream_beyond_check")
        self.beyond_check.setToolTip(
            "Estende a região pelas partes desenergizadas atrás das chaves "
            "abertas que a limitam — o que a origem alimentaria se elas "
            "fechassem. Nunca entra em trecho já alimentado por outra fonte."
        )
        self.beyond_check.toggled.connect(self.beyondOpenSwitchesChanged)
        layout.addWidget(self.beyond_check)

        filters = QGroupBox("Tipos considerados", self)
        filters_layout = QHBoxLayout(filters)
        self.kind_checks: dict[str, QCheckBox] = {}
        for kind in DOWNSTREAM_KINDS:
            check = QCheckBox(DOWNSTREAM_KIND_LABELS[kind], filters)
            check.setObjectName(f"downstream_kind_{kind.lower()}")
            check.setChecked(True)
            check.toggled.connect(self._kinds_toggled)
            filters_layout.addWidget(check)
            self.kind_checks[kind] = check
        filters_layout.addStretch(1)
        layout.addWidget(filters)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("downstream_summary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.summary_label)

        self.table_model = DownstreamTableModel(self)
        self.proxy_model = DownstreamSortProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)
        self.table = BlockTableView(self)
        self.table.setObjectName("downstream_table")
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        # Cada coluna na largura do próprio conteúdo, sem a última esticada
        # para ocupar a sobra; o usuário pode arrastar, e aí a largura é dele.
        enable_interactive_columns(self.table)
        # Ligar a ordenação faz o Qt ordenar na hora pela coluna 0. A ordem da
        # fonte — tipo, depois nível — é a que se quer ler e exportar; ela vale
        # até o usuário escolher outra clicando num cabeçalho.
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.activated.connect(self._activate_index)
        layout.addWidget(self.table, 1)

        self.issues_text = QPlainTextEdit(self)
        self.issues_text.setObjectName("downstream_issues")
        self.issues_text.setReadOnly(True)
        self.issues_text.setMaximumHeight(80)
        self.issues_text.setVisible(False)
        layout.addWidget(self.issues_text)

        buttons = QHBoxLayout()
        self.highlight_button = QPushButton("Destacar no mapa", self)
        self.highlight_button.setObjectName("downstream_highlight_button")
        self.highlight_button.setToolTip(
            "Pintar de amarelo os trechos dos tipos marcados"
        )
        self.highlight_button.clicked.connect(self.highlightRequested)
        buttons.addWidget(self.highlight_button)
        self.frame_button = QPushButton("Enquadrar no mapa", self)
        self.frame_button.setObjectName("downstream_frame_button")
        self.frame_button.clicked.connect(self.frameRequested)
        buttons.addWidget(self.frame_button)
        buttons.addStretch(1)
        self.export_csv_button = QPushButton("Exportar CSV", self)
        self.export_csv_button.setObjectName("downstream_export_csv_button")
        self.export_csv_button.setToolTip(
            "Exportar os elementos exibidos: OBJ_ID;BARRA_1;BARRA_2;TIPO"
        )
        self.export_csv_button.clicked.connect(self.exportCsvRequested)
        buttons.addWidget(self.export_csv_button)
        layout.addLayout(buttons)

        self._sync_buttons()

    # -- estado ---------------------------------------------------------------

    @property
    def selection(self) -> DownstreamSelection | None:
        return self._selection

    def set_selection(self, selection: DownstreamSelection | None) -> None:
        self._selection = selection
        self._sync_available_kinds()
        self._rebuild()

    def set_beyond_open_switches(self, enabled: bool) -> None:
        blocked = self.beyond_check.blockSignals(True)
        try:
            self.beyond_check.setChecked(bool(enabled))
        finally:
            self.beyond_check.blockSignals(blocked)

    @property
    def beyond_open_switches(self) -> bool:
        return self.beyond_check.isChecked()

    def checked_kinds(self) -> tuple[str, ...]:
        return tuple(
            kind
            for kind in DOWNSTREAM_KINDS
            if self.kind_checks[kind].isChecked()
            and self.kind_checks[kind].isEnabled()
        )

    def highlighted_kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind in self.checked_kinds() if kind in HIGHLIGHTABLE_KINDS)

    def visible_rows_in_display_order(self) -> tuple[DownstreamRow, ...]:
        """As linhas na ordem em que aparecem — é a ordem do CSV."""

        rows: list[DownstreamRow] = []
        for row in range(self.proxy_model.rowCount()):
            source = self.proxy_model.mapToSource(self.proxy_model.index(row, 0))
            rows.append(self.table_model.row_at(source.row()))
        return tuple(rows)

    def clear_selection(self) -> None:
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())

    # -- montagem ---------------------------------------------------------------

    def _sync_available_kinds(self) -> None:
        """Tipo sem modelo carregado fica desabilitado, com o motivo no tooltip."""

        selection = self._selection
        available = {
            TRECHO: True,
            BARRA: True,
            CHAVE: selection is not None and selection.switches is not None,
            CARGA: selection is not None and selection.loads is not None,
            CAPACITOR: selection is not None and selection.capacitors is not None,
            GERADOR: selection is not None and selection.generators is not None,
            REGULADOR: selection is not None and selection.regulators is not None,
        }
        for kind, check in self.kind_checks.items():
            enabled = selection is None or available[kind]
            check.setEnabled(enabled)
            label = DOWNSTREAM_KIND_LABELS[kind].lower()
            check.setToolTip(
                "" if enabled else f"Não há {label} importados nesta rede."
            )

    def _rebuild(self) -> None:
        selection = self._selection
        if selection is None:
            self.table_model.set_rows(())
            self.origin_label.setText(
                "Selecione uma barra ou um trecho no mapa, ou clique com o botão "
                "direito sobre um deles e escolha \"Selecionar a jusante daqui\"."
            )
            self.summary_label.setText("")
            self.issues_text.clear()
            self.issues_text.setVisible(False)
            self._sync_buttons()
            return

        self.table_model.set_rows(
            downstream_rows(
                selection,
                self.checked_kinds(),
                phase_configuration=self._phase_configuration,
            )
        )
        self.origin_label.setText(self._origin_text(selection))
        self.summary_label.setText(self._summary_text(selection))
        issues = "\n".join(issue.message for issue in selection.issues)
        self.issues_text.setPlainText(issues)
        self.issues_text.setVisible(bool(issues))
        self._sync_buttons()
        self._fit_width_to_table()

    def _fit_width_to_table(self) -> None:
        """Na primeira região, a janela assume a largura da tabela.

        Só uma vez: depois disso o tamanho é do usuário, e uma nova seleção não
        desfaz o ajuste que ele fez. Nunca menor que o necessário para a linha
        dos filtros, nem maior que quase a tela inteira.
        """

        if self._width_fitted or self.table_model.rowCount() == 0:
            return
        self._width_fitted = True
        table = self.table
        table_width = (
            table.horizontalHeader().length()
            + table.verticalScrollBar().sizeHint().width()
            + 2 * table.frameWidth()
        )
        margins = self.layout().contentsMargins()
        wanted = max(
            table_width + margins.left() + margins.right(),
            self.minimumSizeHint().width(),
        )
        screen = self.screen()
        if screen is not None:
            limit = int(screen.availableGeometry().width() * _MAX_SCREEN_FRACTION)
            wanted = min(wanted, limit)
        self.resize(wanted, self.height())

    def _sync_buttons(self) -> None:
        has_rows = self.table_model.rowCount() > 0
        has_selection = self._selection is not None
        self.export_csv_button.setEnabled(has_rows)
        self.highlight_button.setEnabled(has_selection)
        self.frame_button.setEnabled(
            has_selection and self._selection.segment_indices.size > 0
        )

    @staticmethod
    def _origin_text(selection: DownstreamSelection) -> str:
        segments = selection.segments
        origin = selection.origin
        if origin.kind == "segment":
            record = segments.record(origin.index)
            switches = selection.switches
            is_switch = (
                switches is not None
                and int(switches.record_indices_by_segment[origin.index]) >= 0
            )
            noun = "chave no trecho" if is_switch else "trecho"
            text = f"Origem: {noun} {record.segment_id}"
            if record.code:
                text += f" ({record.code})"
        else:
            bars = segments.bars
            text = f"Origem: barra {bars.bar_ids[origin.index]}"
            if bars.codes[origin.index]:
                text += f" ({bars.codes[origin.index]})"
        if selection.beyond_open_switches:
            text += " — incluindo além de chaves abertas"
        return text

    @staticmethod
    def _summary_text(selection: DownstreamSelection) -> str:
        summary = downstream_summary(selection)
        counts = ", ".join(
            f"{summary.counts[kind]:n} {DOWNSTREAM_KIND_LABELS[kind].lower()}"
            for kind in DOWNSTREAM_KINDS
            if summary.counts[kind]
        )
        parts = [counts or "Nada a jusante."]
        if summary.total_length is not None:
            length = f"Comprimento: {summary.total_length:.1f} m"
            if summary.missing_length_count:
                length += f" ({summary.missing_length_count:n} sem comprimento)"
            parts.append(length)
        if summary.total_power is not None:
            parts.append(f"SNOM: {summary.total_power:n} kVA")
        if summary.counts[CARGA]:
            consumers = f"UC: {summary.consumer_count:n}"
            if summary.unknown_consumer_load_count:
                consumers += (
                    f" ({summary.unknown_consumer_load_count:n} carga(s) sem "
                    "contagem)"
                )
            parts.append(consumers)
        if selection.boundary_switch_indices.size:
            parts.append(
                f"Chaves abertas na fronteira: "
                f"{selection.boundary_switch_indices.size:n}"
            )
        return " · ".join(parts)

    # -- sinais -----------------------------------------------------------------

    def _kinds_toggled(self) -> None:
        self._rebuild()
        self.kindsChanged.emit()

    def _activate_index(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        source = self.proxy_model.mapToSource(index)
        if source.isValid():
            self.rowActivated.emit(self.table_model.row_at(source.row()))

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        """Esc desfaz um nível por vez, como no mapa."""

        if (
            event.key() == Qt.Key.Key_Escape
            and self.table.currentIndex().isValid()
        ):
            self.clear_selection()
            self.selectionCleared.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.clear_selection()
        self.closed.emit()
        super().closeEvent(event)
