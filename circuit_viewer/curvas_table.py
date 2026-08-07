"""Grade editável das 24 horas de uma curva, no estilo do Excel.

Duas peças: :class:`CurveValuesTableModel`, um adaptador Qt fino sobre um
:class:`~circuit_viewer.curvas.CurveDraft` — o estado real continua sendo do
rascunho, como ``CircuitTableModel`` faz com o seu controlador —, e
:class:`CurveTableView`, a única view do projeto que copia e cola.

**A coluna "Hora" é sintética.** Não existe dado guardado para ela: a numeração
1..24 é consequência de ``rowCount``, exatamente como a coluna NPAT de
``PowerFlowTableModel``. Guardá-la seria criar 24 valores que nunca mudam e que
precisariam ser validados junto dos que importam.

**Não há delegate de edição.** ``setData`` com ``parse_number`` já recusa a
entrada inválida, e um ``QDoubleValidator`` seria pior do que nada aqui: ele é
preso ao locale, e sob pt-BR recusaria o ponto decimal enquanto sob C recusaria
a vírgula — contradizendo justamente a regra de separador que o projeto mantém
única.
"""

from __future__ import annotations

from typing import Sequence

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QTableView

from .curvas import (
    CURVE_DISPLAY_DECIMALS,
    HOURLY_CURVE_POINT_COUNT,
    CurveDraft,
    clipboard_column,
    parse_clipboard_values,
    split_clipboard_block,
)
from .opendss_export import parse_number


_HOUR_COLUMN = 0
_VALUE_COLUMN = 1


class CurveValuesTableModel(QAbstractTableModel):
    """Modelo das 24 horas de um rascunho de curva."""

    HEADERS = ("Hora", "Valor")

    # Hora 0-based e o valor novo (``float`` ou ``None``). A janela usa este
    # sinal para repintar o gráfico sem precisar reler o rascunho inteiro.
    valueChanged = pyqtSignal(int, object)
    # Texto já pronto para o rótulo de status da janela.
    validationFailed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._draft: CurveDraft | None = None

    @property
    def draft(self) -> CurveDraft | None:
        return self._draft

    def set_draft(self, draft: CurveDraft | None) -> None:
        self.beginResetModel()
        self._draft = draft
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        if parent.isValid() or self._draft is None:
            return 0
        return HOURLY_CURVE_POINT_COUNT

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN001
        if not index.isValid() or self._draft is None:
            return None
        row = index.row()
        if not 0 <= row < HOURLY_CURVE_POINT_COUNT:
            return None
        column = index.column()

        if role == Qt.ItemDataRole.TextAlignmentRole:
            # AlignVCenter é obrigatório: a altura da linha é fixa em 28 px e o
            # padding vertical do estilo compartilhado é zero.
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        if column == _HOUR_COLUMN:
            if role == Qt.ItemDataRole.DisplayRole:
                return str(row + 1)
            if role == Qt.ItemDataRole.ToolTipRole:
                return f"Hora {row + 1}"
            return None

        value = self._draft.values[row]
        if role == Qt.ItemDataRole.DisplayRole:
            return "" if value is None else f"{value:.{CURVE_DISPLAY_DECIMALS}f}"
        if role == Qt.ItemDataRole.EditRole:
            # Precisão cheia de propósito: com o texto de exibição, abrir a
            # célula e confirmar sem digitar nada truncaria 0,123456 para
            # 0,1235 em silêncio.
            return "" if value is None else f"{value:.12g}"
        if role == Qt.ItemDataRole.ToolTipRole:
            if value is None:
                return "Informe o valor desta hora."
            return f"Hora {row + 1}: {value:.12g}"
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role=Qt.ItemDataRole.DisplayRole,  # noqa: ANN001
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(
            self.HEADERS
        ):
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex):  # noqa: ANN201
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == _VALUE_COLUMN:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(  # noqa: N802
        self,
        index: QModelIndex,
        value,  # noqa: ANN001
        role=Qt.ItemDataRole.EditRole,  # noqa: ANN001
    ) -> bool:
        if (
            not index.isValid()
            or self._draft is None
            or index.column() != _VALUE_COLUMN
            or role != Qt.ItemDataRole.EditRole
        ):
            return False
        row = index.row()
        text = str(value).strip()
        if text:
            number = parse_number(text)
            if number is None:
                self.validationFailed.emit(
                    f'"{text}" não é um número válido. '
                    "Use ponto ou vírgula decimal."
                )
                return False
        else:
            # Apagar é uma intenção legítima: sem isso, um 12,5 digitado como
            # 125 só poderia ser corrigido por outro número, nunca por "ainda
            # não sei".
            number = None
        if not self._draft.set_value(row, number):
            return False
        self._emit_changed(row, row)
        self.valueChanged.emit(row, number)
        return True

    def apply_values(
        self,
        start_row: int,
        values: Sequence[float | None],
    ) -> int:
        """Aplica um bloco de valores a partir de ``start_row``.

        Devolve quantas horas mudaram e emite **um único** ``dataChanged``
        cobrindo a faixa inteira: 24 emissões separadas fariam o gráfico
        redesenhar e as colunas se reajustarem 24 vezes por colagem.
        """

        if self._draft is None or not values:
            return 0
        changed = 0
        last_row = start_row
        for offset, value in enumerate(values):
            row = start_row + offset
            if row >= HOURLY_CURVE_POINT_COUNT:
                break
            if self._draft.set_value(row, value):
                changed += 1
                last_row = row
        if changed:
            self._emit_changed(start_row, last_row)
            self.valueChanged.emit(start_row, self._draft.values[start_row])
        return changed

    def _emit_changed(self, first_row: int, last_row: int) -> None:
        self.dataChanged.emit(
            self.index(first_row, _VALUE_COLUMN),
            self.index(last_row, _VALUE_COLUMN),
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.EditRole,
                Qt.ItemDataRole.ToolTipRole,
            ],
        )


class CurveTableView(QTableView):
    """Grade com colar, copiar e limpar no formato de coluna do Excel."""

    # Resumo em português do que a colagem fez, para o rótulo de status.
    pasteReported = pyqtSignal(str)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_from_clipboard()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Delete):
            self.clear_selection_values()
            event.accept()
            return
        super().keyPressEvent(event)

    def _anchor_row(self) -> int:
        """Linha em que a colagem começa."""

        rows = [index.row() for index in self.selectedIndexes()]
        if rows:
            return min(rows)
        current = self.currentIndex()
        return current.row() if current.isValid() else 0

    def paste_from_clipboard(self) -> None:
        model = self.model()
        if not isinstance(model, CurveValuesTableModel) or model.draft is None:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:  # pragma: no cover - depende da plataforma
            return
        block = split_clipboard_block(clipboard.text())
        if not block:
            self.pasteReported.emit("A área de transferência está vazia.")
            return

        texts, width = clipboard_column(block)
        parsed = parse_clipboard_values(texts)
        start = self._anchor_row()
        room = HOURLY_CURVE_POINT_COUNT - start
        draft = model.draft

        rejected = sum(1 for _, recognized in parsed if not recognized)
        overflow = max(0, len(parsed) - room)
        # Um texto não numérico é pulado, e não interrompe a colagem: um
        # cabeçalho copiado junto dos 24 valores invalidaria o bloco inteiro.
        # Pular significa **repetir o valor atual daquela hora**, e não encolher
        # a lista: compactá-la deslocaria em uma hora todos os valores
        # seguintes, o mesmo erro silencioso que a linha vazia do meio provoca.
        applied: list[float | None] = [
            value if recognized else draft.values[start + offset]
            for offset, (value, recognized) in enumerate(parsed[:room])
        ]
        changed = model.apply_values(start, applied)

        notes = [f"{changed} valor(es) colados a partir da hora {start + 1}."]
        if width > 1:
            notes.append(f"Bloco com {width} colunas: foi usada a última.")
        if rejected:
            notes.append(f"{rejected} valor(es) não numéricos foram ignorados.")
        if overflow:
            notes.append(
                f"{overflow} valor(es) além da hora "
                f"{HOURLY_CURVE_POINT_COUNT} foram ignorados."
            )
        self.pasteReported.emit(" ".join(notes))

    def copy_selection(self) -> None:
        """Copia como TSV, o formato que o Excel espera de volta."""

        model = self.model()
        if not isinstance(model, CurveValuesTableModel) or model.draft is None:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:  # pragma: no cover - depende da plataforma
            return
        indexes = sorted(
            self.selectedIndexes(),
            key=lambda index: (index.row(), index.column()),
        )
        if not indexes:
            # Sem seleção, a coluna "Valor" inteira: é o que se quer levar de
            # volta para a planilha.
            lines = [
                model.data(model.index(row, _VALUE_COLUMN)) or ""
                for row in range(HOURLY_CURVE_POINT_COUNT)
            ]
            clipboard.setText("\n".join(lines))
            return
        rows: dict[int, list[str]] = {}
        for index in indexes:
            text = model.data(index, Qt.ItemDataRole.DisplayRole) or ""
            rows.setdefault(index.row(), []).append(str(text))
        clipboard.setText(
            "\n".join("\t".join(rows[row]) for row in sorted(rows))
        )

    def clear_selection_values(self) -> None:
        model = self.model()
        if not isinstance(model, CurveValuesTableModel) or model.draft is None:
            return
        rows = sorted(
            {
                index.row()
                for index in self.selectedIndexes()
                if index.column() == _VALUE_COLUMN
            }
        )
        if not rows:
            return
        for row in rows:
            model.setData(
                model.index(row, _VALUE_COLUMN),
                "",
                Qt.ItemDataRole.EditRole,
            )
