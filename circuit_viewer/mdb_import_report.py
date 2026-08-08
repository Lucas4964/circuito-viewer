"""Relatório consolidado de uma importação por banco de dados.

Uma importação por banco produz dez resultados lógicos. Encadear os dez
``QMessageBox`` dos importadores de CSV seria inaceitável, então o resultado
inteiro cabe numa tabela: uma linha por entidade, com a tabela de origem e as
contagens, mais as ocorrências agrupadas abaixo.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableView,
    QVBoxLayout,
)

from .mdb_import import MdbImportResult
from .mdb_mapping import ENTITY_ORDER
from .table_columns import enable_interactive_columns


# Teto de ocorrências mostradas no painel de texto. Cada importador já limita as
# suas em MAX_REPORTED_ISSUES; este é o teto do conjunto.
MAX_REPORTED_LINES = 400


def _result_for(result: MdbImportResult, entity: str):  # noqa: ANN202
    return {
        "barras": result.bars,
        "cabos": result.cables,
        "trechos": result.segments,
        "cargas": result.loads,
        "geradores": result.generators,
        "patamares": result.patterns,
        "chaves": result.switches,
        "reguladores": result.regulators,
        "circuitos": result.circuits,
        "patamares_circuitos": result.circuit_levels,
    }.get(entity)


def issue_lines(result: MdbImportResult) -> tuple[str, ...]:
    """Ocorrências de todas as entidades, prefixadas pelo rótulo de cada uma."""

    lines: list[str] = []
    omitted = 0
    for entity in ENTITY_ORDER:
        outcome = result.outcome_for(entity)
        if outcome is None:
            continue
        if outcome.error is not None:
            lines.append(f"{outcome.label}: {outcome.error}")
            continue
        loaded = _result_for(result, entity)
        if loaded is None:
            continue
        crs_warning = getattr(loaded, "crs_warning", None)
        if crs_warning:
            lines.append(f"{outcome.label}: {crs_warning}")
        for issue in getattr(loaded, "issues", ()):
            if len(lines) >= MAX_REPORTED_LINES:
                omitted += 1
                continue
            source = getattr(issue, "source", None)
            source_text = f" ({source})" if source else ""
            lines.append(
                f"{outcome.label}{source_text}, linha {issue.line_number}: "
                f"{issue.reason}"
            )
        omitted += int(getattr(loaded, "omitted_issues", 0))
    if omitted:
        lines.append(f"… e mais {omitted:n} ocorrência(s) não detalhada(s).")
    return tuple(lines)


class MdbImportReportTableModel(QAbstractTableModel):
    HEADERS = ("Entidade", "Tabela", "Lidas", "Válidas", "Inválidas", "Situação")

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._result: MdbImportResult | None = None

    def set_result(self, result: MdbImportResult | None) -> None:
        self.beginResetModel()
        self._result = result
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        if parent.isValid() or self._result is None:
            return 0
        return len(self._result.outcomes)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001, ANN201, N802
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= int(section) < len(self.HEADERS)
        ):
            return self.HEADERS[int(section)]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if (
            not index.isValid()
            or self._result is None
            or role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}
        ):
            return None
        outcome = self._result.outcomes[index.row()]
        if outcome.imported:
            situation = "Importada"
        elif outcome.error:
            situation = outcome.error
        else:  # pragma: no cover - todo desvio tem motivo
            situation = "Não importada"
        values = (
            outcome.label,
            outcome.table or "—",
            f"{outcome.total_rows:n}" if outcome.imported else "—",
            f"{outcome.valid_rows:n}" if outcome.imported else "—",
            f"{outcome.invalid_rows:n}" if outcome.imported else "—",
            situation,
        )
        return values[index.column()]


class MdbImportReportWindow(QDialog):
    """Janela com o resumo por entidade e as ocorrências do conjunto."""

    def __init__(
        self,
        result: MdbImportResult,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Relatório da importação")
        # Não modal, como o relatório de sobreposições: os dois abrem sozinhos
        # ao fim de uma operação, e o usuário precisa poder olhar a rede com o
        # relatório aberto ao lado.
        self.setModal(False)
        self.resize(820, 520)

        layout = QVBoxLayout(self)
        imported = len(result.imported_entities)
        self.summary_label = QLabel(
            f"{imported} de {len(ENTITY_ORDER)} entidades importadas de "
            f"{result.source_path}."
        )
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.table_model = MdbImportReportTableModel(self)
        self.table_model.set_result(result)
        self.table = QTableView(self)
        self.table.setObjectName("mdb_import_report_table")
        self.table.setModel(self.table_model)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setStyleSheet("QTableView { gridline-color: palette(mid); }")
        self.table.verticalHeader().setVisible(False)
        enable_interactive_columns(self.table)
        self.table.horizontalHeader().setSectionResizeMode(
            len(MdbImportReportTableModel.HEADERS) - 1,
            QHeaderView.ResizeMode.Stretch,
        )
        layout.addWidget(self.table)

        lines = issue_lines(result)
        self.issues_view = QPlainTextEdit(self)
        self.issues_view.setObjectName("mdb_import_report_issues")
        self.issues_view.setReadOnly(True)
        self.issues_view.setPlainText("\n".join(lines))
        self.issues_view.setVisible(bool(lines))
        layout.addWidget(self.issues_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
