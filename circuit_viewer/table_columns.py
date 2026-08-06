"""Colunas de tabela ajustáveis pelo usuário, no estilo do Excel.

``ResizeToContents`` recalcula a largura a cada mudança de dados e, por isso,
descarta silenciosamente qualquer arraste do usuário — o cabeçalho nem sequer
oferece a alça. Trocar por ``Interactive`` devolve o arraste, mas faz as colunas
nascerem todas com a largura padrão, ignorando o conteúdo.

Este módulo reconcilia as duas coisas: o ajuste automático continua valendo
**enquanto o usuário não tiver escolhido uma largura**; a partir do primeiro
arraste as larguras passam a ser dele, e uma nova seleção de elemento não desfaz
mais o trabalho.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHeaderView, QTableView


def enable_interactive_columns(table: QTableView) -> None:
    """Libera o arraste das colunas de ``table`` sem perder o auto-ajuste."""

    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(False)

    # ``following_contents`` só desliga quando o arraste parte do usuário;
    # ``applying`` distingue os resizes que nós mesmos disparamos, porque o Qt
    # emite ``sectionResized`` para os dois casos.
    state = {"following_contents": True, "applying": False}

    def resize(action) -> None:  # noqa: ANN001
        state["applying"] = True
        try:
            action()
        finally:
            state["applying"] = False

    def follow_contents() -> None:
        if state["following_contents"]:
            resize(table.resizeColumnsToContents)

    def on_section_resized(*_args: int) -> None:
        if not state["applying"]:
            state["following_contents"] = False

    def on_handle_double_clicked(column: int) -> None:
        # Pedir "caiba no conteúdo" é exatamente o que o modo automático faz,
        # então o duplo-clique não conta como largura escolhida a dedo.
        resize(lambda: table.resizeColumnToContents(column))

    header.sectionResized.connect(on_section_resized)
    header.sectionHandleDoubleClicked.connect(on_handle_double_clicked)

    model = table.model()
    if model is not None:
        model.modelReset.connect(follow_contents)
        model.layoutChanged.connect(follow_contents)

    follow_contents()
