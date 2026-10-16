"""Janela de cadastro e edição das curvas horárias.

Composição mestre-detalhe: a tabela das curvas (ID de vínculo, período e nome)
à esquerda, e à direita o nome, o ID, o período, a grade das 24 horas e o
gráfico do que está selecionado.

**As alterações ficam pendentes até "Salvar".** É o que permite desistir de uma
edição errada — inclusive de uma exclusão. A contrapartida é que fechar a janela
com pendências tem de perguntar, e que "Descartar" precisa **reler o arquivo**:
como ``WA_DeleteOnClose`` é ``False``, a mesma instância é reaproveitada na
próxima abertura, e sem a releitura ela reapareceria mostrando exatamente as
edições que o usuário acabou de descartar.

A janela nunca chama a janela principal: o que precisa subir sobe pelo sinal
:attr:`CurvesWindow.curvesSaved`, como nas demais janelas secundárias.

**"Importar banco…" não fica pendente.** Ler o Curvas.mdb do Interplan é uma
importação, como as da janela principal: com o rascunho resolvido antes, as
curvas e as categorias vão direto para o disco e a janela relê o resultado. As
curvas do banco têm id fixo, então importar de novo atualiza as mesmas.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .curva_chart import CurveChartWidget
from .curvas import (
    CURVE_PERIOD_LABELS,
    CURVE_PERIODS,
    MAX_CURVE_LINK_ID_LENGTH,
    MAX_CURVE_NAME_LENGTH,
    CurveCatalog,
    CurveDraft,
    validate_catalog,
    validate_curve_name,
)
from .curvas_mdb import CurveDatabaseContents, merge_curve_database
from .curvas_store import (
    categories_path_for,
    load_curve_categories,
    load_curves,
    save_curve_categories,
    save_curves,
)
from .curvas_table import CurveTableView, CurveValuesTableModel
from .table_columns import EXCEL_LIKE_TABLE_STYLE, enable_interactive_columns


_EMPTY_TEXT = 'Nenhuma curva cadastrada. Use "Nova curva" para começar.'
_NEW_CURVE_NAME = "Nova curva"
_ID_COLUMN = 0
_PERIOD_COLUMN = 1
_NAME_COLUMN = 2
# Quantas ocorrências da leitura do banco a mensagem final lista.
_SHOWN_IMPORT_ISSUES = 15


class CurvesWindow(QDialog):
    """Cadastro das curvas horárias de 24 pontos."""

    # Quantidade gravada, para a janela principal informar na barra de status.
    curvesSaved = pyqtSignal(int)

    def __init__(  # noqa: ANN001
        self,
        catalog: CurveCatalog,
        *,
        storage_path: str | Path | None = None,
        categories_path: str | Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Curvas")
        self.setModal(False)
        self.resize(980, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self.catalog = catalog
        self._storage_path = storage_path
        self._categories_path = (
            categories_path_for(storage_path)
            if categories_path is None
            else Path(categories_path)
        )
        self._dirty = False
        # Importação do banco de curvas: uma por vez, e o resultado só é lido
        # quando a thread termina, para nunca haver duas conexões abertas.
        self._import_path: str | None = None
        self._import_thread: QThread | None = None
        self._import_worker = None
        self._import_progress: QProgressDialog | None = None
        self._import_outcome: tuple[str, object] | None = None
        self._import_cancelled = False
        self._import_password_attempts = 0
        self._default_password_tried = False
        # Guarda de reentrância: repovoar a lista dispara currentRowChanged, e
        # sem ela a troca de seleção gravaria o nome de uma curva em outra.
        self._loading = False

        layout = QHBoxLayout(self)
        layout.addWidget(self._build_side_panel())
        layout.addWidget(self._build_editor(), 1)

        self._connect_signals()
        self.refresh()

    # ------------------------------------------------------------------ UI --

    def _build_side_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setFixedWidth(360)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)

        column.addWidget(QLabel("Curvas", panel))
        # Tabela só de leitura: nome e ID se editam nos campos ao lado, que já
        # validam e marcam a pendência. A ordem das linhas é a do catálogo —
        # sem ordenação por clique, que desalinharia linha e índice.
        self.curve_list = QTableWidget(0, 3, panel)
        self.curve_list.setObjectName("curves_list")
        self.curve_list.setHorizontalHeaderLabels(("ID", "Per.", "Nome"))
        self.curve_list.horizontalHeaderItem(_PERIOD_COLUMN).setToolTip(
            "Tipo de dia: DU dia útil, SA sábado, DO domingo, MED média do mês"
        )
        self.curve_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.curve_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.curve_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.curve_list.verticalHeader().hide()
        self.curve_list.setWordWrap(False)
        # Nomes longos perdem o meio, não o fim: é no fim que fica o período
        # (dia útil, sábado…) que distingue as curvas de uma mesma categoria.
        self.curve_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.curve_list.horizontalHeader()
        for section in (_ID_COLUMN, _PERIOD_COLUMN):
            header.setSectionResizeMode(section, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(_NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.curve_list.setColumnWidth(_ID_COLUMN, 48)
        self.curve_list.setColumnWidth(_PERIOD_COLUMN, 44)
        column.addWidget(self.curve_list, 1)

        self.new_button = QPushButton("Nova curva", panel)
        self.new_button.setObjectName("curve_new_button")
        self.new_button.setToolTip("Criar uma curva de 24 horas em branco")
        column.addWidget(self.new_button)

        self.delete_button = QPushButton("Excluir…", panel)
        self.delete_button.setObjectName("curve_delete_button")
        self.delete_button.setToolTip("Excluir a curva selecionada")
        column.addWidget(self.delete_button)

        self.import_button = QPushButton("Importar banco de curvas…", panel)
        self.import_button.setObjectName("curve_import_button")
        self.import_button.setToolTip(
            "Ler as curvas típicas e as categorias de um banco do Interplan "
            "(Curvas.mdb); importar de novo atualiza as mesmas curvas"
        )
        column.addWidget(self.import_button)
        return panel

    def _build_editor(self) -> QWidget:
        editor = QWidget(self)
        column = QVBoxLayout(editor)
        column.setContentsMargins(0, 0, 0, 0)

        self.empty_label = QLabel(_EMPTY_TEXT, editor)
        self.empty_label.setObjectName("curves_empty_label")
        self.empty_label.setWordWrap(True)
        column.addWidget(self.empty_label)

        name_row = QHBoxLayout()
        self.name_label = QLabel("Nome:", editor)
        name_row.addWidget(self.name_label)
        self.name_edit = QLineEdit(editor)
        self.name_edit.setObjectName("curve_name_edit")
        self.name_edit.setMaxLength(MAX_CURVE_NAME_LENGTH)
        self.name_edit.setPlaceholderText("Ex.: Residencial típica")
        name_row.addWidget(self.name_edit, 1)
        self.link_id_label = QLabel("ID:", editor)
        name_row.addWidget(self.link_id_label)
        self.link_id_edit = QLineEdit(editor)
        self.link_id_edit.setObjectName("curve_link_id_edit")
        self.link_id_edit.setMaxLength(MAX_CURVE_LINK_ID_LENGTH)
        self.link_id_edit.setFixedWidth(72)
        self.link_id_edit.setToolTip(
            "Identificador para vínculos e mapas — nas curvas do Interplan, o "
            "CAT_ID da categoria referenciado pelo CURVA_ID da rede"
        )
        name_row.addWidget(self.link_id_edit)
        self.period_label = QLabel("Período:", editor)
        name_row.addWidget(self.period_label)
        self.period_combo = QComboBox(editor)
        self.period_combo.setObjectName("curve_period_combo")
        self.period_combo.addItem("—", "")
        for period in CURVE_PERIODS:
            self.period_combo.addItem(
                f"{period} — {CURVE_PERIOD_LABELS[period]}", period
            )
        self.period_combo.setToolTip(
            "Tipo de dia da curva; nas curvas típicas do Interplan, o PERIODO 1 a 4"
        )
        name_row.addWidget(self.period_combo)
        column.addLayout(name_row)

        self.table_model = CurveValuesTableModel(self)
        self.table = CurveTableView(editor)
        self.table.setObjectName("curve_values_table")
        self.table.setModel(self.table_model)
        # enable_interactive_columns lê table.model(): chamar antes de setModel
        # deixaria as colunas sem o auto-ajuste, em silêncio.
        enable_interactive_columns(self.table)
        self.table.setStyleSheet(EXCEL_LIKE_TABLE_STYLE)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(28)
        # Sem altura fixa: 24 linhas rolam, ao contrário das 4 do painel de
        # patamares, que cabem inteiras na tela.
        self.table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        # Duas colunas estreitas ocupariam uma faixa larga e vazia se a grade
        # fosse de largura inteira; ao lado do gráfico, cada um fica com a
        # dimensão de que precisa — números altos e estreitos, curva larga.
        header = self.table.horizontalHeader()
        header.sectionResized.connect(self._sync_table_width)
        self.table_model.modelReset.connect(self._sync_table_width)

        self.chart = CurveChartWidget(editor)
        self.chart.setObjectName("curve_chart")

        content_row = QHBoxLayout()
        content_row.addWidget(self.table, 0)
        content_row.addWidget(self.chart, 1)
        column.addLayout(content_row, 1)
        self._sync_table_width()

        self.status_label = QLabel(editor)
        self.status_label.setObjectName("curve_status_label")
        self.status_label.setWordWrap(True)
        column.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.save_button = QPushButton("Salvar", editor)
        self.save_button.setObjectName("curve_save_button")
        self.save_button.setToolTip("Gravar as curvas em disco")
        button_row.addWidget(self.save_button)
        self.close_button = QPushButton("Fechar", editor)
        self.close_button.setObjectName("curve_close_button")
        button_row.addWidget(self.close_button)
        column.addLayout(button_row)
        return editor

    def _connect_signals(self) -> None:
        self.curve_list.currentCellChanged.connect(self._on_curve_selected)
        self.new_button.clicked.connect(self._on_new_curve)
        self.delete_button.clicked.connect(self._on_delete_curve)
        self.name_edit.textEdited.connect(self._on_name_edited)
        self.link_id_edit.textEdited.connect(self._on_link_id_edited)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        self.import_button.clicked.connect(self._on_import_clicked)
        self.save_button.clicked.connect(self._on_save_clicked)
        self.close_button.clicked.connect(self.close)
        self.table_model.dataChanged.connect(self._on_values_changed)
        self.table_model.modelReset.connect(self._sync_chart)
        self.table_model.validationFailed.connect(self._show_error)
        self.table.pasteReported.connect(self._on_paste_reported)

    # --------------------------------------------------------------- estado --

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def refresh(self) -> None:
        """Repovoa a lista e ajusta o que fica visível."""

        self._loading = True
        try:
            current = self.curve_list.currentRow()
            drafts = self.catalog.drafts
            self.curve_list.setRowCount(0)
            self.curve_list.setRowCount(len(drafts))
            for row, draft in enumerate(drafts):
                self.curve_list.setItem(
                    row, _ID_COLUMN, QTableWidgetItem(draft.link_id)
                )
                self.curve_list.setItem(
                    row, _PERIOD_COLUMN, self._period_item(draft.period)
                )
                name_item = QTableWidgetItem(draft.name or "(sem nome)")
                name_item.setToolTip(draft.name)
                self.curve_list.setItem(row, _NAME_COLUMN, name_item)
            count = len(drafts)
            if count:
                row = min(max(current, 0), count - 1)
                self.curve_list.setCurrentCell(row, _NAME_COLUMN)
        finally:
            self._loading = False
        self._sync_visibility()
        self._load_selected()

    def confirm_pending_changes(self) -> bool:
        """Resolve o rascunho antes de um consumidor usar as curvas salvas."""

        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Alterações não salvas",
            "Há alterações não salvas nas curvas. Salvar agora?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        self._reload_from_disk()
        return True

    def _sync_visibility(self) -> None:
        has_curves = len(self.catalog) > 0
        self.empty_label.setVisible(not has_curves)
        for widget in (
            self.name_label,
            self.name_edit,
            self.link_id_label,
            self.link_id_edit,
            self.period_label,
            self.period_combo,
            self.table,
            self.chart,
            self.save_button,
        ):
            widget.setVisible(has_curves)
        self.delete_button.setEnabled(has_curves)

    def _current_draft(self) -> CurveDraft | None:
        row = self.curve_list.currentRow()
        if not 0 <= row < len(self.catalog):
            return None
        return self.catalog.draft(row)

    def _load_selected(self) -> None:
        draft = self._current_draft()
        self._loading = True
        try:
            self.name_edit.setText("" if draft is None else draft.name)
            self.link_id_edit.setText("" if draft is None else draft.link_id)
            self.period_combo.setCurrentIndex(
                max(0, self.period_combo.findData("" if draft is None else draft.period))
            )
        finally:
            self._loading = False
        self.table_model.set_draft(draft)
        self._sync_chart()

    def _sync_table_width(self, *args) -> None:  # noqa: ANN002, ARG002
        """Deixa a grade exatamente da largura das suas duas colunas.

        Sem isso sobraria uma faixa vazia dentro da tabela, entre a coluna
        "Valor" e a borda — e essa faixa roubaria do gráfico justamente a
        largura que ele aproveita. Acompanha o arraste do usuário, então
        alargar uma coluna alarga a grade em vez de criar uma barra horizontal.
        """

        width = self.table.horizontalHeader().length()
        width += 2 * self.table.frameWidth()
        scrollbar = self.table.verticalScrollBar()
        if scrollbar is not None:
            # A barra vertical existe sempre: 24 linhas não cabem na altura.
            width += scrollbar.sizeHint().width()
        self.table.setFixedWidth(width)

    def _sync_chart(self) -> None:
        draft = self.table_model.draft
        if draft is None:
            self.chart.clear()
        else:
            self.chart.set_values(draft.values)

    def _mark_dirty(self) -> None:
        self._dirty = True

    # --------------------------------------------------------------- ações --

    def _on_curve_selected(self, *args) -> None:  # noqa: ANN002, ARG002
        if self._loading:
            return
        self._load_selected()
        self._clear_status()

    def _on_new_curve(self) -> None:
        name = self._unique_new_name()
        index = self.catalog.add(CurveDraft.new(name))
        self._mark_dirty()
        self.refresh()
        self.curve_list.setCurrentCell(index, _NAME_COLUMN)
        self._load_selected()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _unique_new_name(self) -> str:
        if self.catalog.name_available(_NEW_CURVE_NAME):
            return _NEW_CURVE_NAME
        suffix = 2
        while not self.catalog.name_available(f"{_NEW_CURVE_NAME} {suffix}"):
            suffix += 1
        return f"{_NEW_CURVE_NAME} {suffix}"

    def _on_delete_curve(self) -> None:
        row = self.curve_list.currentRow()
        draft = self._current_draft()
        if draft is None:
            return
        label = draft.name or "(sem nome)"
        answer = QMessageBox.question(
            self,
            "Excluir curva",
            f'Excluir a curva "{label}"? '
            "A exclusão só será gravada ao salvar.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.catalog.remove(row)
        self._mark_dirty()
        self.refresh()
        self._show_info(f'Curva "{label}" excluída. Salve para confirmar.')

    def _on_name_edited(self, text: str) -> None:
        if self._loading:
            return
        row = self.curve_list.currentRow()
        draft = self._current_draft()
        if draft is None:
            return
        if self.catalog.rename(row, text):
            self._mark_dirty()
        item = self.curve_list.item(row, _NAME_COLUMN)
        if item is not None:
            item.setText(draft.name or "(sem nome)")
            item.setToolTip(draft.name)
        # A unicidade avisa mas não bloqueia a digitação: bloquear caractere a
        # caractere impediria até trocar os nomes de duas curvas entre si.
        problem = validate_curve_name(
            text,
            self.catalog,
            ignoring=draft.curve_id,
        )
        if problem is None:
            self._clear_status()
        else:
            self._show_error(problem)

    def _on_link_id_edited(self, text: str) -> None:
        if self._loading:
            return
        row = self.curve_list.currentRow()
        draft = self._current_draft()
        if draft is None:
            return
        if self.catalog.set_link_id(row, text):
            self._mark_dirty()
        item = self.curve_list.item(row, _ID_COLUMN)
        if item is not None:
            item.setText(draft.link_id)

    @staticmethod
    def _period_item(period: str) -> QTableWidgetItem:
        item = QTableWidgetItem(period)
        if period:
            item.setToolTip(CURVE_PERIOD_LABELS[period])
        return item

    def _on_period_changed(self, index: int) -> None:
        if self._loading:
            return
        row = self.curve_list.currentRow()
        draft = self._current_draft()
        if draft is None:
            return
        period = self.period_combo.itemData(index) or ""
        if self.catalog.set_period(row, period):
            self._mark_dirty()
        self.curve_list.setItem(row, _PERIOD_COLUMN, self._period_item(draft.period))

    def _on_values_changed(self, *args) -> None:  # noqa: ANN002, ARG002
        self._mark_dirty()
        self._sync_chart()

    def _on_paste_reported(self, message: str) -> None:
        self._show_info(message)

    def _on_save_clicked(self) -> None:
        self._save()

    def _save(self) -> bool:
        problems = validate_catalog(self.catalog)
        if problems:
            self._show_error(" ".join(problems))
            return False
        try:
            save_curves(self.catalog.to_curves(), self._storage_path)
        except OSError as exc:
            self._show_error(
                f"Não foi possível gravar as curvas: {exc.strerror or exc}"
            )
            return False
        self._dirty = False
        count = len(self.catalog)
        self._show_info(f"{count} curva(s) salva(s).")
        self.curvesSaved.emit(count)
        return True

    def _reload_from_disk(self) -> None:
        """Devolve o catálogo ao que está gravado, descartando as edições."""

        result = load_curves(self._storage_path)
        self.catalog = CurveCatalog.from_curves(result.curves)
        self._dirty = False
        self.refresh()
        self._clear_status()

    # ------------------------------------------------- importação do banco --

    @property
    def is_importing(self) -> bool:
        return self._import_thread is not None

    def _on_import_clicked(self) -> None:
        if self.is_importing or not self.confirm_pending_changes():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar banco de curvas",
            "",
            "Bancos Access (*.mdb *.accdb);;Todos os arquivos (*)",
        )
        if not path:
            return
        self._import_path = path
        self._import_cancelled = False
        self._import_password_attempts = 0
        self._default_password_tried = False
        progress = QProgressDialog("Lendo o banco de curvas…", "Cancelar", 0, 0, self)
        progress.setWindowTitle("Importar banco de curvas")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self._cancel_import)
        progress.setValue(0)
        self._import_progress = progress
        self._start_import(None)

    def _start_import(self, password: str | None) -> None:
        from .workers import CurveDatabaseWorker

        self._import_outcome = None
        thread = QThread(self)
        worker = CurveDatabaseWorker(self._import_path, password=password)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda contents: self._set_import_outcome("finished", contents))
        worker.failed.connect(lambda message: self._set_import_outcome("failed", message))
        worker.password_required.connect(lambda: self._set_import_outcome("password", None))
        worker.completed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_import_thread_finished)
        self._import_thread = thread
        self._import_worker = worker
        self.import_button.setEnabled(False)
        thread.start()

    def _set_import_outcome(self, kind: str, value: object) -> None:
        self._import_outcome = (kind, value)

    def _cancel_import(self) -> None:
        self._import_cancelled = True
        if self._import_worker is not None:
            self._import_worker.cancel()

    def _on_import_thread_finished(self) -> None:
        """Decide o passo seguinte só depois que a conexão já foi fechada."""

        self._import_thread = None
        self._import_worker = None
        outcome, self._import_outcome = self._import_outcome, None
        if self._import_cancelled or outcome is None:
            self._finish_import()
            self._show_info("Importação do banco de curvas cancelada.")
            return
        kind, value = outcome
        if kind == "password":
            password = self._ask_import_password()
            if password is None:
                self._finish_import()
                self._show_info("Importação do banco de curvas cancelada.")
            else:
                self._start_import(password)
            return
        self._finish_import()
        if kind == "failed":
            self._show_error(str(value))
        else:
            self.install_imported_database(value)

    def _ask_import_password(self) -> str | None:
        """Senha padrão do cofre primeiro; depois, a digitada pelo usuário."""

        from .mdb_credentials import load_default_password
        from .mdb_import_dialog import MdbPasswordDialog

        if not self._default_password_tried:
            self._default_password_tried = True
            password = load_default_password()
            if password:
                return password
        progress = self._import_progress
        if progress is not None:
            progress.hide()
        dialog = MdbPasswordDialog(
            Path(self._import_path or "").name,
            self,
            retry=self._import_password_attempts > 0,
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            self._import_password_attempts += 1
            return dialog.password()
        finally:
            dialog.deleteLater()
            if progress is not None:
                progress.show()

    def _finish_import(self) -> None:
        progress, self._import_progress = self._import_progress, None
        if progress is not None:
            progress.canceled.disconnect(self._cancel_import)
            progress.close()
            progress.deleteLater()
        self.import_button.setEnabled(True)

    def install_imported_database(self, contents: CurveDatabaseContents) -> bool:
        """Junta o banco lido às curvas gravadas e grava curvas e categorias."""

        categories = load_curve_categories(self._categories_path)
        merged = merge_curve_database(
            self.catalog.to_curves(), categories.categories, contents
        )
        try:
            # As categorias primeiro: sem as curvas elas não atrapalham nada,
            # mas curvas sem as categorias não serviriam ao modo Energia.
            save_curve_categories(merged.categories, self._categories_path)
            save_curves(merged.curves, self._storage_path)
        except OSError as exc:
            self._show_error(
                f"Não foi possível gravar as curvas importadas: {exc.strerror or exc}"
            )
            return False
        self.catalog = CurveCatalog.from_curves(merged.curves)
        self._dirty = False
        self.refresh()
        summary = (
            f"Banco de curvas importado: {merged.added:n} curva(s) nova(s), "
            f"{merged.replaced:n} atualizada(s) e {len(contents.categories):n} "
            "categoria(s)."
        )
        if merged.renamed:
            summary += (
                f" {len(merged.renamed):n} curva(s) do banco ganharam sufixo no "
                "nome por coincidir com curvas suas."
            )
        self._show_info(summary)
        self.curvesSaved.emit(len(self.catalog))
        if contents.issues:
            lines = list(contents.issues[:_SHOWN_IMPORT_ISSUES])
            hidden = len(contents.issues) - len(lines) + contents.omitted_issues
            if hidden:
                lines.append(f"… e mais {hidden:n}.")
            QMessageBox.information(
                self,
                "Importar banco de curvas",
                summary + "\n\nFicaram de fora:\n" + "\n".join(lines),
            )
        return True

    # -------------------------------------------------------------- status --

    def _show_error(self, message: str) -> None:
        # palette(bright-text) é vermelho nas duas paletas do projeto; uma cor
        # literal ficaria ilegível no tema escuro.
        self.status_label.setStyleSheet("color: palette(bright-text);")
        self.status_label.setText(message)

    def _show_info(self, message: str) -> None:
        self.status_label.setStyleSheet("")
        self.status_label.setText(message)

    def _clear_status(self) -> None:
        self.status_label.setStyleSheet("")
        self.status_label.clear()

    # ------------------------------------------------------------ fechamento --

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.is_importing:
            # O diálogo de progresso é modal; fechar por fora só cancela.
            self._cancel_import()
            event.ignore()
            return
        if self.confirm_pending_changes():
            event.accept()
        else:
            event.ignore()
