"""Janela de cadastro e edição das curvas horárias.

Composição mestre-detalhe: a lista das curvas à esquerda, e à direita o nome, a
grade das 24 horas e o gráfico do que está selecionado.

**As alterações ficam pendentes até "Salvar".** É o que permite desistir de uma
edição errada — inclusive de uma exclusão. A contrapartida é que fechar a janela
com pendências tem de perguntar, e que "Descartar" precisa **reler o arquivo**:
como ``WA_DeleteOnClose`` é ``False``, a mesma instância é reaproveitada na
próxima abertura, e sem a releitura ela reapareceria mostrando exatamente as
edições que o usuário acabou de descartar.

A janela nunca chama a janela principal: o que precisa subir sobe pelo sinal
:attr:`CurvesWindow.curvesSaved`, como nas demais janelas secundárias.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .curva_chart import CurveChartWidget
from .curvas import (
    MAX_CURVE_NAME_LENGTH,
    CurveCatalog,
    CurveDraft,
    validate_catalog,
    validate_curve_name,
)
from .curvas_store import load_curves, save_curves
from .curvas_table import CurveTableView, CurveValuesTableModel
from .table_columns import EXCEL_LIKE_TABLE_STYLE, enable_interactive_columns


_EMPTY_TEXT = 'Nenhuma curva cadastrada. Use "Nova curva" para começar.'
_NEW_CURVE_NAME = "Nova curva"


class CurvesWindow(QDialog):
    """Cadastro das curvas horárias de 24 pontos."""

    # Quantidade gravada, para a janela principal informar na barra de status.
    curvesSaved = pyqtSignal(int)

    def __init__(  # noqa: ANN001
        self,
        catalog: CurveCatalog,
        *,
        storage_path: str | Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Curvas")
        self.setModal(False)
        self.resize(980, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self.catalog = catalog
        self._storage_path = storage_path
        self._dirty = False
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
        panel.setFixedWidth(220)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)

        column.addWidget(QLabel("Curvas", panel))
        self.curve_list = QListWidget(panel)
        self.curve_list.setObjectName("curves_list")
        self.curve_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        column.addWidget(self.curve_list, 1)

        self.new_button = QPushButton("Nova curva", panel)
        self.new_button.setObjectName("curve_new_button")
        self.new_button.setToolTip("Criar uma curva de 24 horas em branco")
        column.addWidget(self.new_button)

        self.delete_button = QPushButton("Excluir…", panel)
        self.delete_button.setObjectName("curve_delete_button")
        self.delete_button.setToolTip("Excluir a curva selecionada")
        column.addWidget(self.delete_button)
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
        self.curve_list.currentRowChanged.connect(self._on_curve_selected)
        self.new_button.clicked.connect(self._on_new_curve)
        self.delete_button.clicked.connect(self._on_delete_curve)
        self.name_edit.textEdited.connect(self._on_name_edited)
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
            self.curve_list.clear()
            for draft in self.catalog.drafts:
                self.curve_list.addItem(draft.name or "(sem nome)")
            count = len(self.catalog)
            if count:
                row = min(max(current, 0), count - 1)
                self.curve_list.setCurrentRow(row)
        finally:
            self._loading = False
        self._sync_visibility()
        self._load_selected()

    def _sync_visibility(self) -> None:
        has_curves = len(self.catalog) > 0
        self.empty_label.setVisible(not has_curves)
        for widget in (
            self.name_label,
            self.name_edit,
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

    def _on_curve_selected(self, row: int) -> None:  # noqa: ARG002
        if self._loading:
            return
        self._load_selected()
        self._clear_status()

    def _on_new_curve(self) -> None:
        name = self._unique_new_name()
        index = self.catalog.add(CurveDraft.new(name))
        self._mark_dirty()
        self.refresh()
        self.curve_list.setCurrentRow(index)
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
        item = self.curve_list.item(row)
        if item is not None:
            item.setText(draft.name or "(sem nome)")
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
        if not self._dirty:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "Alterações não salvas",
            "Há alterações não salvas nas curvas. Salvar antes de fechar?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return
        if answer == QMessageBox.StandardButton.Save:
            if not self._save():
                # A validação recusou: manter a janela aberta é a única forma
                # de o usuário corrigir o que falta.
                event.ignore()
                return
        else:
            self._reload_from_disk()
        event.accept()
