"""Paleta flutuante de busca global."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from .search import GlobalSearchIndex, SearchResult


class SearchPalette(QFrame):
    """Campo e lista transitórios sobrepostos ao canvas."""

    resultActivated = pyqtSignal(object)
    closed = pyqtSignal()

    def __init__(
        self,
        search_index: GlobalSearchIndex,
        hidden_checker: Callable[[SearchResult], bool],
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.search_index = search_index
        self.hidden_checker = hidden_checker
        self._results: tuple[SearchResult, ...] = ()
        self._height_limit = 360

        self.setObjectName("global_search_palette")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "QFrame#global_search_palette {"
            " background: palette(window);"
            " border: 1px solid palette(mid);"
            " border-radius: 6px;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        self.input = QLineEdit(self)
        self.input.setObjectName("global_search_input")
        self.input.setPlaceholderText("Buscar por código…")
        self.input.setClearButtonEnabled(True)
        self.input.setAccessibleName("Buscar elemento por código")
        layout.addWidget(self.input)

        self.results_list = QListWidget(self)
        self.results_list.setObjectName("global_search_results")
        self.results_list.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        self.results_list.setUniformItemSizes(True)
        layout.addWidget(self.results_list, 1)

        self.summary = QLabel("Digite um código para buscar.", self)
        self.summary.setObjectName("global_search_summary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.input.textChanged.connect(self.refresh_results)
        self.results_list.itemClicked.connect(self._activate_item)
        self.results_list.itemActivated.connect(self._activate_item)
        self.input.installEventFilter(self)
        self.results_list.installEventFilter(self)
        self.hide()

    def open(self) -> None:
        self.refresh_results()
        self.show()
        self.raise_()
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.input.selectAll()

    def close_palette(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        self.closed.emit()

    def refresh_results(self) -> None:
        query = self.search_index.query(self.input.text())
        self._results = query.results
        self.results_list.clear()
        for index, result in enumerate(self._results):
            suffix = " · oculto pelos filtros" if self.hidden_checker(result) else ""
            item = QListWidgetItem(f"{result.display_text}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(item.text())
            self.results_list.addItem(item)

        if self._results:
            self.results_list.setCurrentRow(0)
        text = self.input.text().strip()
        if not text:
            message = "Digite um código para buscar."
        elif not self._results:
            message = "Nenhum elemento encontrado."
        elif query.truncated:
            message = (
                "Mostrando os 100 primeiros resultados parciais; "
                "continue digitando."
            )
        else:
            count = len(self._results)
            message = f"{count:n} resultado{'s' if count != 1 else ''}."
        self.summary.setText(message)
        self._adjust_height()

    def set_height_limit(self, height: int) -> None:
        self._height_limit = max(1, int(height))
        self._adjust_height()

    def _adjust_height(self) -> None:
        visible_rows = min(max(len(self._results), 1), 10)
        desired = max(150, 104 + visible_rows * 24)
        self.resize(self.width(), min(self._height_limit, desired))

    def _activate_item(self, item: QListWidgetItem) -> None:
        result_index = int(item.data(Qt.ItemDataRole.UserRole))
        if not 0 <= result_index < len(self._results):
            return
        result = self._results[result_index]
        self.close_palette()
        self.resultActivated.emit(result)

    def _activate_current(self) -> bool:
        item = self.results_list.currentItem()
        if item is None and self.results_list.count():
            item = self.results_list.item(0)
        if item is None:
            return False
        self._activate_item(item)
        return True

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return super().eventFilter(watched, event)
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close_palette()
            return True
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            return self._activate_current()
        if watched is self.input and key in {Qt.Key.Key_Down, Qt.Key.Key_Up}:
            count = self.results_list.count()
            if not count:
                return True
            row = self.results_list.currentRow()
            offset = 1 if key == Qt.Key.Key_Down else -1
            self.results_list.setCurrentRow((row + offset) % count)
            self.results_list.setFocus(Qt.FocusReason.ShortcutFocusReason)
            return True
        return super().eventFilter(watched, event)
