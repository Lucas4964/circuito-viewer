"""Ajuda contextual das bibliotecas OpenDSS."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTabWidget, QTextBrowser, QVBoxLayout


_CABLE_HELP = """
<h2>Cabos</h2>
<p>Um cabo desta biblioteca descreve um condutor físico do OpenDSS. Cabos do
tipo <b>Fio nu</b> viram <code>WireData</code>; cabos com neutro concêntrico
viram <code>CNData</code>.</p>
<h3>GMR e dimensão física</h3>
<p>GMR não é o raio externo do cabo. O GMR participa da indutância; diâmetro ou
raio participa da capacitância e das distâncias entre superfícies. Mantenha as
unidades de R, GMR e dimensão exatamente como na ficha do condutor.</p>
<p>Quando o raio não estiver disponível, ele pode ser estimado por
<code>r = GMR / 0,7788</code> para condutores homogêneos não magnéticos, ou pela
seção nominal e pelo fator de preenchimento. Valores estimados são marcados com
<b>≈</b> e devem ser conferidos antes de uso elétrico.</p>
<h3>CNData</h3>
<p>Além do condutor central, CNData exige quantidade, diâmetro e resistência dos
fios do neutro, espessura da isolação e diâmetros construtivos. GMR do fio e
permissividade são opcionais porque o OpenDSS possui valores derivados/padrão.</p>
"""

_GEOMETRY_HELP = """
<h2>Geometrias</h2>
<p>Um <b>arranjo</b> corresponde a <code>LineSpacing</code>: contém apenas as
posições <code>x</code>/<code>h</code>. Uma <b>montagem</b> corresponde a
<code>LineGeometry</code>: escolhe um arranjo e o cabo de cada posição.</p>
<p>Os primeiros <code>nphases</code> condutores são fases; os restantes são
neutros. Altura zero é o solo e valores negativos representam cabos enterrados.
Duas posições idênticas tornam a matriz elétrica singular.</p>
<h3>Redução de Kron</h3>
<p>Com a redução habilitada, o OpenDSS elimina os condutores neutros da matriz
completa e preserva seu efeito na matriz equivalente das fases.</p>
<p>As fases de uma montagem devem usar todas fios nus ou todas cabos
concêntricos. O neutro pode ser de outro tipo. A ampacidade mostrada é o menor
valor declarado entre os cabos de fase.</p>
<h3>Gráfico cartesiano</h3>
<p>As prévias mostram cada condutor como um ponto: <code>x</code> é o eixo X e
<code>h</code> é o eixo Y. Os eixos zero aparecem destacados e a grade se adapta
ao zoom. Use a roda do mouse para ampliar sob o cursor, arraste com o botão
esquerdo para navegar e dê duplo clique para enquadrar todos os condutores. O
gráfico é somente para consulta; as coordenadas continuam sendo editadas na
tabela.</p>
"""


class OpenDssLibraryHelpDialog(QDialog):
    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Ajuda — Bibliotecas OpenDSS")
        self.setModal(False)
        self.resize(680, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.cables_help = QTextBrowser(self.tabs)
        self.cables_help.setOpenExternalLinks(True)
        self.cables_help.setHtml(_CABLE_HELP)
        self.geometries_help = QTextBrowser(self.tabs)
        self.geometries_help.setOpenExternalLinks(True)
        self.geometries_help.setHtml(_GEOMETRY_HELP)
        self.tabs.addTab(self.cables_help, "Cabos")
        self.tabs.addTab(self.geometries_help, "Geometrias")
        layout.addWidget(self.tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.hide)
        layout.addWidget(buttons)

    def show_section(self, section: str) -> None:
        self.tabs.setCurrentIndex(1 if section == "geometries" else 0)
        self.show()
        self.raise_()
        self.activateWindow()
