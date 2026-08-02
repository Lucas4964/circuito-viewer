"""Janela não modal de busca global."""

from __future__ import annotations

from collections.abc import Callable
import threading

from PyQt6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from .search import (
    FieldSearchPartition,
    FieldSearchSnapshot,
    GlobalSearchIndex,
    SearchCancelled,
    SearchKind,
    SearchQueryResult,
    SearchResult,
    SearchSource,
    build_field_search_partition,
    normalize_code,
    query_field_snapshot,
)


class _IndexTaskSignals(QObject):
    finished = pyqtSignal(str, object, object, object, object)


class _IndexTask(QRunnable):
    def __init__(
        self,
        kind: SearchKind,
        source: SearchSource,
        token: threading.Event,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.source = source
        self.token = token
        self.signals = _IndexTaskSignals()

    def run(self) -> None:
        partition: FieldSearchPartition | None = None
        error: Exception | None = None
        try:
            partition = build_field_search_partition(
                self.kind,
                self.source,
                cancel_check=self.token.is_set,
            )
        except SearchCancelled:
            pass
        except Exception as exc:  # pragma: no cover - proteção da thread Qt
            error = exc
        self.signals.finished.emit(
            self.kind,
            self.source,
            self.token,
            partition,
            error,
        )


class _QueryTaskSignals(QObject):
    finished = pyqtSignal(int, str, object, object, object)


class _QueryTask(QRunnable):
    def __init__(
        self,
        serial: int,
        text: str,
        snapshot: FieldSearchSnapshot,
        token: threading.Event,
    ) -> None:
        super().__init__()
        self.serial = serial
        self.text = text
        self.snapshot = snapshot
        self.token = token
        self.signals = _QueryTaskSignals()

    def run(self) -> None:
        result: SearchQueryResult | None = None
        error: Exception | None = None
        try:
            result = query_field_snapshot(
                self.snapshot,
                self.text,
                limit=200,
                cancel_check=self.token.is_set,
            )
        except SearchCancelled:
            pass
        except Exception as exc:  # pragma: no cover - proteção da thread Qt
            error = exc
        self.signals.finished.emit(
            self.serial,
            self.text,
            self.token,
            result,
            error,
        )


class SearchPalette(QDialog):
    """Diálogo móvel que mantém o modo rápido e a busca em todas as colunas."""

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
        self._positioned_once = False
        self._query_serial = 0
        self._query_token: threading.Event | None = None
        self._index_tokens: dict[SearchKind, threading.Event] = {}
        self._index_errors: dict[SearchKind, str] = {}

        self.setObjectName("global_search_palette")
        self.setWindowTitle("Buscar elementos")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setSizeGripEnabled(True)
        self.resize(560, 400)
        self.setMinimumSize(420, 240)

        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(2)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._start_any_field_query)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        self.input = QLineEdit(self)
        self.input.setObjectName("global_search_input")
        self.input.setPlaceholderText("Buscar por código…")
        self.input.setClearButtonEnabled(True)
        self.input.setAccessibleName("Buscar elemento por código")
        layout.addWidget(self.input)

        self.any_column_checkbox = QCheckBox(
            "Buscar valor em qualquer coluna",
            self,
        )
        self.any_column_checkbox.setObjectName("global_search_any_column")
        self.any_column_checkbox.setToolTip(
            "Pesquisa ocorrências em todas as colunas dos elementos importados."
        )
        layout.addWidget(self.any_column_checkbox)

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

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close,
            parent=self,
        )
        close_button = self.buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("Fechar")
        close_button.setObjectName("global_search_close")
        self.buttons.rejected.connect(self.close_palette)
        layout.addWidget(self.buttons)

        self.input.textChanged.connect(self.refresh_results)
        self.any_column_checkbox.toggled.connect(self._change_mode)
        self.results_list.itemClicked.connect(self._activate_item)
        self.results_list.itemActivated.connect(self._activate_item)
        self.input.installEventFilter(self)
        self.results_list.installEventFilter(self)
        self.any_column_checkbox.installEventFilter(self)
        self.hide()

    @property
    def searching_all_fields(self) -> bool:
        return self.any_column_checkbox.isChecked()

    def open(self) -> None:
        self.refresh_results()
        if not self._positioned_once:
            self._center_on_parent()
            self._positioned_once = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.input.selectAll()

    def _center_on_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        center = parent.frameGeometry().center()
        geometry = self.frameGeometry()
        geometry.moveCenter(center)
        self.move(geometry.topLeft())

    def close_palette(self) -> None:
        if self.isVisible():
            self.close()

    def schedule_field_index(
        self,
        kind: SearchKind,
        source: SearchSource | None,
    ) -> None:
        previous = self._index_tokens.pop(kind, None)
        if previous is not None:
            previous.set()
        self._index_errors.pop(kind, None)
        if source is None or not self.search_index.needs_field_partition(kind, source):
            self.refresh_results()
            return
        token = threading.Event()
        self._index_tokens[kind] = token
        task = _IndexTask(kind, source, token)
        task.signals.finished.connect(self._finish_field_index)
        self._thread_pool.start(task)
        self.refresh_results()

    def _finish_field_index(
        self,
        kind: str,
        source: object,
        token: threading.Event,
        partition: FieldSearchPartition | None,
        error: Exception | None,
    ) -> None:
        typed_kind = kind  # PyQt entrega o Literal como str.
        if self._index_tokens.get(typed_kind) is not token:
            return
        self._index_tokens.pop(typed_kind, None)
        if error is not None:
            self._index_errors[typed_kind] = str(error)
        elif partition is not None:
            self.search_index.install_field_partition(partition)
        self.refresh_results()

    def refresh_results(self) -> None:
        self._cancel_pending_query()
        self.results_list.clear()
        self._results = ()
        text = self.input.text().strip()

        if not self.searching_all_fields:
            query = self.search_index.query(text)
            self._show_results(query)
            return

        normalized = normalize_code(text)
        if not normalized:
            self.summary.setText("Digite um valor para buscar em qualquer coluna.")
            return
        if len(normalized) < 3:
            self.summary.setText("Digite pelo menos 3 caracteres para buscar em qualquer coluna.")
            return
        if self._index_errors:
            self.summary.setText(
                "Não foi possível preparar a busca em todas as colunas. "
                "Reimporte os dados e tente novamente."
            )
            return
        if not self.search_index.fields_ready:
            self.summary.setText("Preparando a busca em todas as colunas…")
            return
        self.summary.setText("Buscando em todas as colunas…")
        self._debounce.start()

    def _cancel_pending_query(self) -> None:
        self._debounce.stop()
        self._query_serial += 1
        if self._query_token is not None:
            self._query_token.set()
            self._query_token = None

    def _start_any_field_query(self) -> None:
        snapshot = self.search_index.field_snapshot()
        text = self.input.text()
        if (
            snapshot is None
            or not self.searching_all_fields
            or len(normalize_code(text)) < 3
        ):
            self.refresh_results()
            return
        serial = self._query_serial
        token = threading.Event()
        self._query_token = token
        task = _QueryTask(serial, text, snapshot, token)
        task.signals.finished.connect(self._finish_any_field_query)
        self._thread_pool.start(task)

    def _finish_any_field_query(
        self,
        serial: int,
        text: str,
        token: threading.Event,
        result: SearchQueryResult | None,
        error: Exception | None,
    ) -> None:
        if serial != self._query_serial or self._query_token is not token:
            return
        self._query_token = None
        if (
            token.is_set()
            or not self.searching_all_fields
            or text != self.input.text()
        ):
            return
        if error is not None:
            self.summary.setText("Não foi possível concluir a busca.")
            return
        if result is None or result.revision != self.search_index.revision:
            self.refresh_results()
            return
        self._show_results(result)

    def _show_results(self, query: SearchQueryResult) -> None:
        self._results = query.results
        self.results_list.clear()
        for index, result in enumerate(self._results):
            hidden = self.hidden_checker(result)
            suffix = " · oculto pelos filtros" if hidden else ""
            item = QListWidgetItem(f"{result.display_text}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            tooltip = result.tooltip_text
            if hidden:
                tooltip += "\n\nOculto pelos filtros atuais."
            item.setToolTip(tooltip)
            self.results_list.addItem(item)

        if self._results:
            self.results_list.setCurrentRow(0)
        text = self.input.text().strip()
        if not text:
            message = (
                "Digite um valor para buscar em qualquer coluna."
                if self.searching_all_fields
                else "Digite um código para buscar."
            )
        elif not self._results:
            message = "Nenhum elemento encontrado."
        elif query.truncated:
            if self.searching_all_fields:
                message = (
                    f"Mostrando {len(self._results):n} de "
                    f"{query.total_matches:n} resultados; refine a busca."
                )
            else:
                message = (
                    "Mostrando os 100 primeiros resultados parciais; "
                    "continue digitando."
                )
        else:
            count = query.total_matches or len(self._results)
            message = f"{count:n} resultado{'s' if count != 1 else ''}."
        self.summary.setText(message)

    def _change_mode(self, enabled: bool) -> None:
        if enabled:
            self.input.setPlaceholderText("Buscar em qualquer coluna…")
            self.input.setAccessibleName("Buscar elemento em qualquer coluna")
        else:
            self.input.setPlaceholderText("Buscar por código…")
            self.input.setAccessibleName("Buscar elemento por código")
        self.refresh_results()
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)

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

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._cancel_pending_query()
        super().closeEvent(event)
        self.closed.emit()

    def shutdown(self) -> None:
        """Cancela tarefas antes da destruição da janela principal."""

        self._cancel_pending_query()
        for token in self._index_tokens.values():
            token.set()
        self._index_tokens.clear()
        self._thread_pool.waitForDone(2_000)
