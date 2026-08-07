"""Janela não modal com o catálogo de cabos importado."""

from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from .model import CableModel, CableRecord


def _as_number(value: str) -> float:
    """Converte para ordenação aceitando ponto ou vírgula decimal.

    Valores vazios ou não numéricos vão para o fim da ordenação crescente, como
    já ocorre com os campos ausentes da tabela de ramais.
    """

    text = str(value).strip()
    # Mesma regra de _parse_coordinate/_parse_decimal: ponto OU vírgula, nunca
    # os dois, para não interpretar separador de milhar como decimal.
    if not text or ("," in text and "." in text):
        return float("inf")
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return float("inf")


def cable_summary(cable: CableRecord) -> str:
    """Rótulo curto do cabo para a coluna auxiliar dos trechos."""

    code = cable.code.strip()
    return code if code else "—"


def cable_tooltip(cable: CableRecord) -> str:
    """Detalhamento do cabo exibido ao passar o mouse."""

    fields = (
        ("CABO_ID", cable.cable_id),
        ("TIPO", cable.cable_type),
        ("CODIGO", cable.code),
        ("NOME", cable.name),
        ("IADM", cable.iadm),
        ("R", cable.r),
        ("X", cable.x),
    )
    return "\n".join(
        f"{caption}: {value.strip()}" for caption, value in fields if value.strip()
    )


class CableTableModel(QAbstractTableModel):
    HEADERS = (
        "CABO_ID",
        "TIPO",
        "CODIGO",
        "IADM",
        "GMR",
        "R",
        "X",
        "QCAP",
        "R0",
        "X0",
        "R1",
        "X1",
        "NOME",
        "EXTERN_ID",
    )
    NUMERIC_COLUMNS = frozenset(range(3, 12))

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.catalog: CableModel | None = None

    def set_catalog(self, catalog: CableModel | None) -> None:
        self.beginResetModel()
        self.catalog = catalog
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        if parent.isValid() or self.catalog is None:
            return 0
        return len(self.catalog)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(  # noqa: ANN001, ANN201, N802
        self,
        section,
        orientation,
        role=Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= int(section) < len(self.HEADERS)
        ):
            return self.HEADERS[int(section)]
        return None

    def record(self, row: int) -> CableRecord:
        if self.catalog is None or not 0 <= int(row) < len(self.catalog):
            raise IndexError(row)
        return self.catalog.record(int(row))

    @staticmethod
    def _raw_values(record: CableRecord) -> tuple[str, ...]:
        return (
            record.cable_id,
            record.cable_type,
            record.code,
            record.iadm,
            record.gmr,
            record.r,
            record.x,
            record.qcap,
            record.r0,
            record.x0,
            record.r1,
            record.x1,
            record.name,
            record.external_id,
        )

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid() or self.catalog is None:
            return None
        value = self._raw_values(self.record(index.row()))[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            if index.column() in self.NUMERIC_COLUMNS:
                return _as_number(value)
            return value.casefold()
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() in self.NUMERIC_COLUMNS:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return None
        if role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        return value or "—"


class CablesWindow(QDialog):
    """Consulta do catálogo; também oferece a importação quando está vazia."""

    importRequested = pyqtSignal()

    def __init__(self, table_model: CableTableModel, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Cabos")
        self.setModal(False)
        self.resize(1_040, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.import_button = QPushButton("Importar cabos…", self)
        self.import_button.setToolTip(
            "Carregar o CSV do catálogo de cabos (CABO_ID, TIPO, CODIGO, …)"
        )
        self.import_button.clicked.connect(self.importRequested)
        layout.addWidget(self.import_button)

        self.proxy_model = QSortFilterProxyModel(self)
        self.proxy_model.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy_model.setDynamicSortFilter(True)
        self.proxy_model.setSourceModel(table_model)

        self.table = QTableView(self)
        self.table.setObjectName("cables_table")
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.setStyleSheet("QTableView { gridline-color: palette(mid); }")
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            CableTableModel.HEADERS.index("NOME"),
            QHeaderView.ResizeMode.Stretch,
        )
        layout.addWidget(self.table, 1)

        self.refresh()

    def refresh(self) -> None:
        """Sincroniza resumo, visibilidade da tabela e botão de importação."""

        source = self.proxy_model.sourceModel()
        catalog = source.catalog if isinstance(source, CableTableModel) else None
        count = 0 if catalog is None else len(catalog)
        if count == 0:
            self.summary_label.setText(
                "Nenhum cabo importado. Use o botão abaixo ou "
                "Arquivo > Importar CSV… > Importar cabos…"
            )
        else:
            self.summary_label.setText(f"{count:n} cabo(s) no catálogo.")
        self.import_button.setVisible(count == 0)
        self.table.setVisible(count > 0)
