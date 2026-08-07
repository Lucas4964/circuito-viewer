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


# Estilo compartilhado pelas tabelas "estilo Excel" do painel de detalhes
# (fluxo de potência e patamares de carga). O ``padding`` horizontal entra no
# ``sizeFromContents()`` que ``resizeColumnsToContents()`` consulta, então a
# largura calculada já nasce maior — sem tocar no texto nem em ``paint()``, e
# sem o risco de um delegate feito à mão: se o retângulo de pintura fosse
# encolhido para desenhar só o texto, o destaque de seleção passaria a
# preencher apenas essa área menor, deixando uma faixa sem destaque na borda
# da célula. O padding vertical fica em 0px de propósito: a altura da linha já
# é fixa (``verticalHeader().setDefaultSectionSize(28)``) e o texto já
# centraliza verticalmente via ``AlignVCenter`` nos modelos dessas tabelas: um
# padding vertical não nulo brigaria com essa conta.
EXCEL_LIKE_TABLE_STYLE = (
    "QTableView { gridline-color: palette(mid); }"
    "QTableView::item { padding: 0px 10px; }"
)


def enable_interactive_columns(
    table: QTableView,
    *,
    always_refit: bool = False,
) -> None:
    """Libera o arraste das colunas de ``table`` sem perder o auto-ajuste.

    ``always_refit`` desliga a memória de "o usuário já escolheu uma largura"
    para tabelas cujo conjunto de colunas muda por completo a cada atualização
    — fluxo de potência e patamares de carga, onde trocar de elemento ou de
    grandeza no combobox troca as colunas inteiras (``Fase D`` vs ``θD``,
    ``P (kW)``…). Preservar uma largura escolhida a dedo nesses casos deixaria
    a coluna curta ou larga demais para o próximo conteúdo; com esta flag o
    reajuste acontece sempre que o modelo é reposto, e um arraste manual só
    dura até a atualização seguinte.
    """

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
        if always_refit or state["following_contents"]:
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
