# Visualizador de Circuitos Elétricos

Aplicação desktop em PyQt6 para importar e inspecionar barras elétricas com
coordenadas UTM. A visão ampla desenha todas as barras em lote; a visão detalhada
materializa apenas os itens próximos da tela, com limite de mil objetos ativos.
Trechos da rede são compilados em uma única camada vetorial cacheada, sempre
desenhada abaixo das barras.

## Requisitos

- Windows, Linux ou macOS;
- Python 3.11 ou mais recente;
- arquivo CSV contendo as colunas obrigatórias `BARRA_ID`, `CODIGO`, `X` e `Y`.

## Instalação e execução

No PowerShell, a partir da pasta do projeto:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m circuit_viewer
```

Ao importar, informe a zona e o hemisfério UTM. X e Y aceitam ponto ou vírgula
decimal. As colunas obrigatórias podem aparecer em qualquer ordem e todas as
colunas adicionais são ignoradas. Linhas inválidas são ignoradas e apresentadas
em um relatório; os dados anteriores só são substituídos quando a nova importação
termina com ao menos uma barra válida.

O mesmo diálogo pede a **unidade das coordenadas**. O modelo trabalha em metros
— a mesma unidade de `COMPR` —, e muitas bases guardam X e Y em decímetros ou
centímetros. A aplicação lê uma amostra do arquivo, deduz a unidade e já a
apresenta selecionada; basta confirmar ou escolher outra na lista. Coordenadas
que continuem fora da faixa UTM válida (easting entre 100.000 e 900.000;
northing entre 0 e 10.000.000) são aceitas, mas o relatório avisa — nesse caso a
imagem de satélite não consegue se posicionar corretamente.

Use **Arquivo > Importar…** e escolha entre barras, trechos, cargas, chaves ou
circuitos. A opção de
trechos fica disponível depois que as barras forem carregadas. O arquivo de
trechos deve conter `TRECHO_ID`, `CODIGO`, `FASES2`, `BARRA1_ID`, `BARRA2_ID`,
`ARRANJO_ID`, `CABOF_ID`, `CABON_ID` e `COMPR`. A ordem é livre e colunas
adicionais são ignoradas. Trechos que referenciam barras inexistentes são
omitidos e relatados.

Depois das barras, é possível escolher **Importar cargas…**. O CSV deve usar
`;` como separador e conter `CARGA_ID`, `BARRA_ID`, `EXTERN_ID`, `CODIGO`,
`SNOM`, `SADM`, `VLINHASEC`, `FASES2` e `TIPO_LIG`. Cada carga é ligada à barra
indicada por `BARRA_ID` e desenhada como um pequeno retângulo com terminal.
Várias cargas na mesma barra são distribuídas automaticamente. IDs duplicados
e referências a barras inexistentes são omitidos e incluídos no relatório de
importação; os demais campos são preservados como texto.

Após importar as cargas, a opção **Importar patamares de carga…** aceita um
segundo CSV com `CARGA_ID`, `NPAT`, `PD`, `PE`, `PF`, `QD`, `QE` e `QF`. Cada
carga presente nesse arquivo deve possuir exatamente os patamares `0`, `1`, `2`
e `3`. Grupos incompletos, duplicados ou com outros valores de `NPAT` são
descartados e relatados sem impedir a importação dos demais grupos completos.
Os valores de potência são preservados como texto, inclusive quando vazios.

Esses dados são exclusivamente informativos: ao selecionar uma carga com
patamares, o painel lateral exibe abaixo da tabela principal uma segunda tabela
com quatro linhas ordenadas por `NPAT`. A importação não altera símbolos,
filtros, circuitos ou resultados da busca global.

Depois dos trechos, a mesma janela permite **Importar chaves…**. O CSV deve
conter `CHAVE_ID`, `TIPOCHV_ID`, `CIRC_ID`, `TRECHO_ID`, `CODIGO`, `ESTADO`,
`ESTADO_NORMAL`, `CORN`, `ELO` e `ELO_TIPO`. Cada registro complementa o trecho
indicado por `TRECHO_ID`: ele passa a ser desenhado em vermelho e exibe uma
segunda tabela de propriedades quando selecionado. Trechos comuns usam linha
cosmética de 3 pixels; trechos-chave usam linha vermelha de 1 pixel.

Depois dos trechos, também é possível **Importar circuitos…** usando as colunas
`CIRC_ID`, `BARRA_ID`, `CODIGO` e `VNOM`. A aplicação executa a busca topológica
a partir da barra inicial. Trechos-chave são associados diretamente pelo
`CIRC_ID`: `ESTADO=0` bloqueia a passagem e `ESTADO=1` permite a passagem apenas
para o mesmo circuito.

## Controles

- Roda do mouse: zoom no cursor, limitado suavemente a `100 px/m` ou ao
  limite numérico seguro da cena (o menor dos dois). A barra de status informa
  quando a ampliação máxima é atingida.
- Ferramenta **Mover**, botão do meio ou `Espaço` + arraste: pan.
- Ferramenta **Selecionar**: clique próximo de uma barra ou trecho para
  inspecioná-lo no painel lateral. Barras têm prioridade nos pontos de conexão.
- **Visualizar > Mostrar barras**: alterna a visibilidade e a seleção das barras
  sem ocultar os trechos ou as chaves.
- **Visualizar > Mostrar cargas**: alterna somente a camada de cargas. Elas
  continuam acompanhando os filtros de circuito da barra associada, mas não são
  ocultadas pela opção **Mostrar barras**.
- **Visualizar > Colorir trechos por fases**: substitui temporariamente as cores
  dos circuitos pela classificação configurada em `FASES2`. Monofásicos usam
  azul, bifásicos verde, trifásicos vermelho e valores sem relação ficam cinza.
  O modo também colore as chaves, preservando sua espessura diferenciada. A
  legenda permanece fixa no canto inferior esquerdo durante zoom, pan e
  enquadramentos.
- **Visualizar > Rede simplificada por ramais**: após confirmação e análise,
  substitui cada ramal por uma carga equivalente na sua conexão com o tronco.
  Trechos, barras internas, chaves e cargas agregadas ficam ocultos somente na
  projeção visual; desativar o modo restaura imediatamente a rede original.
- **Visualizar > Exibir imagem de satélite**: exibe tiles georreferenciados como
  fundo do canvas. A opção pode ser ligada antes de importar barras e passa a
  desenhar automaticamente assim que existir uma referência UTM.
- **Visualizar > Provedor de satélite**: escolhe Esri World Imagery, Google
  Satélite ou Google Híbrido. Esri é o padrão. Os provedores Google usam
  endpoints não oficiais e exigem confirmação na primeira utilização da sessão.
- **Visualizar > Circuitos…**: abre a tabela não modal de circuitos. Desmarcar um
  circuito oculta suas barras, trechos e chaves sem apagar a associação calculada.
  A coluna **Cor** mostra a cor automática do circuito e abre um seletor ao ser
  clicada; alterar a cor não recalcula a topologia.
- **Visualizar > Sobreposições…**: lista os trechos associados a mais de um
  circuito. O relatório também é aberto automaticamente quando uma sobreposição
  é encontrada.
- **Enquadrar tudo** ou tecla `F`: mostra todo o conjunto.
- **Buscar** ou `Ctrl+F`: abre uma janela não modal que pode ser movida,
  redimensionada e fechada pelo `X`, pelo botão **Fechar** ou por `Esc`. No modo
  padrão, localiza barras, trechos, chaves, cargas e circuitos pelo campo
  `CODIGO`, aceitando prefixos e ignorando diferenças entre maiúsculas,
  minúsculas e acentos. A consulta e a posição da janela são preservadas durante
  a execução.
- **Buscar valor em qualquer coluna**: amplia a consulta para todas as colunas
  conhecidas dos cinco tipos importados. Esse modo usa correspondência por
  ocorrência, requer ao menos três caracteres e apresenta até 200 elementos,
  indicando o campo encontrado e solicitando refinamento quando necessário.
  Patamares, ramais, cargas equivalentes e colunas extras ignoradas na importação
  não participam desse índice.
- **Ferramentas > Ramais…**: identifica ramais monofásicos e bifásicos ligados
  ao tronco trifásico de cada circuito. Um clique na tabela destaca todo o
  ramal; duplo clique ou `Enter` também o enquadra no canvas.
- `S` e `M`: ativam Selecionar e Mover.

## Imagem de satélite

A camada requer conexão com a internet para tiles ainda ausentes do cache. Os
downloads são assíncronos e não bloqueiam zoom, pan ou seleção. Tiles recebidos
ficam em um cache LRU de memória e no diretório de cache padrão da aplicação,
separados por provedor. Falhas de conexão apenas deixam o fundo normal visível.

O nível de detalhe acompanha o zoom da tela. Em ampliações acima da cobertura
do provedor, o último nível disponível é ampliado; durante a navegação, tiles de
outros níveis já armazenados são usados temporariamente para evitar áreas
brancas. A atribuição da fonte permanece fixa no canto inferior direito e a
legenda de fases continua fixa no canto inferior esquerdo.

O alinhamento depende da zona, do hemisfério **e da unidade** informados ao
importar as barras. Coordenadas fora da faixa UTM válida fazem a projeção
saturar: os tiles são posicionados a milhares de quilômetros da rede e o fundo
parece vazio. Quando isso acontece, a barra de status informa o motivo. A aplicação transforma o CRS do modelo para WGS 84 e suporta zonas dos
hemisférios norte e sul. O mapa não altera os limites da cena, os dados
importados, os filtros nem o comportamento de **Enquadrar tudo**. A escolha do
provedor vale somente para a execução atual; cada inicialização começa com Esri.

## Configuração de fases

As relações entre `FASES2` e o número de fases ficam em
`circuit_viewer/config/fases2.json`. O arquivo é lido em UTF-8 durante a
inicialização; reinicie a aplicação depois de editá-lo. Cada item deve possuir
`FASES2` e `NUMERO_FASES`; `NOME` é uma descrição opcional:

```json
[
  {"FASES2": "1", "NOME": "D", "NUMERO_FASES": 1},
  {"FASES2": "2", "NOME": "E", "NUMERO_FASES": 1},
  {"FASES2": "3", "NOME": "F", "NUMERO_FASES": 1},
  {"FASES2": "9", "NOME": "DF", "NUMERO_FASES": 2},
  {"FASES2": "13", "NOME": "DEF", "NUMERO_FASES": 3}
]
```

`NUMERO_FASES` aceita somente `1`, `2` ou `3`. Valores de `FASES2` podem ser
texto ou número; espaços e diferenças entre maiúsculas e minúsculas são
ignorados. Relações duplicadas ou inválidas desabilitam apenas esse modo de
visualização e geram um aviso com o caminho e o problema encontrado.

As cores são fixas: `#0000FF` para uma fase, `#00FF00` para duas fases,
`#FF0000` para três fases e `#555555` quando não houver relação no JSON. Os
filtros de visibilidade dos circuitos continuam sendo respeitados.

## Análise de ramais

A ferramenta **Ramais** fica disponível depois da importação dos trechos e dos
circuitos, desde que `fases2.json` seja válido. A análise usa a topologia elétrica
energizada: chaves abertas interrompem o percurso e somente chaves fechadas do
próprio circuito são atravessadas. Cargas e chaves são opcionais.

Cada linha representa um ramal `MONOFASICO` ou `BIFASICO` conectado ao tronco.
`RAMAL_ID` é um inteiro global e sequencial no resultado atual; `TIPO_RAMAL`
informa a classificação, `FASES2` preserva o código original do primeiro trecho
e `FASE` mostra sua interpretação pelo JSON. Ramais bifásicos são reconhecidos
somente para valores configurados com `NUMERO_FASES=2` e incorporam integralmente
suas subárvores monofásicas a jusante, mesmo quando elas usam diferentes valores
`FASES2`.

Uma componente monofásica ligada a mais de um núcleo bifásico, ou ligada
simultaneamente ao tronco e a um núcleo bifásico por caminhos distintos, é
excluída de todos os ramais envolvidos e registrada nos diagnósticos. Isso evita
duplicar trechos, cargas e potência agregada. Transições entre códigos bifásicos
distintos também interrompem o ramal e são reportadas.

Além da conexão, primeiro trecho, quantidade, comprimento, cargas e fase, a
tabela informa barras, chaves, posição da primeira chave, conexões adicionais,
comprimentos ausentes e classificação da topologia. `REMANEJAVEL=1` significa
que há uma chave em até cinco níveis do início do conjunto completo do ramal,
inclusive em uma subárvore monofásica incorporada. Se algum `COMPR` estiver
vazio, o total é exibido como `—`.

A tabela pode ser ordenada e filtrada por circuito. Selecionar um ramal reativa
seu circuito caso ele esteja oculto, sem alterar o modo de coloração por fases.
Resultados são descartados automaticamente quando barras, trechos, cargas,
chaves ou circuitos forem substituídos.

### Rede simplificada e cargas equivalentes

O modo simplificado cria um snapshot lógico derivado, sem remover ou modificar
qualquer registro importado. Cada ramal recebe uma carga com `CARGA_ID` explícito
no formato `RAMAL-1`, `RAMAL-2`, etc., `ORIGEM=Ramal agregado` e `BARRA_ID` igual
à conexão principal. `SNOM` e `SADM` são somados com aritmética decimal; vazios ou
valores inválidos tornam somente o total correspondente indisponível e geram um
diagnóstico.

Quando os patamares estiverem carregados, a aplicação agrega `PD`, `PE`, `PF`,
`QD`, `QE` e `QF` por `NPAT`. A tabela equivalente é apresentada apenas quando
todas as cargas do ramal possuem os quatro patamares completos e numéricos. A
carga derivada é selecionável e o painel lateral informa sua origem, ramal,
`TIPO_RAMAL`, `REMANEJAVEL`, circuito, conexão, cargas de origem e totais.

Filtros de circuito também se aplicam à projeção. Em circuitos sobrepostos, um
elemento original permanece visível enquanto for necessário por outro circuito
visível. **Mostrar cargas** controla conjuntamente cargas originais preservadas e
cargas equivalentes, e **Enquadrar tudo** usa os limites da projeção ativa.

## Testes e benchmark

Os testes do núcleo usam apenas a biblioteca padrão e NumPy. Os testes gráficos
são executados quando PyQt6 estiver instalado.

```powershell
python -m unittest discover -s tests -v
python benchmarks\benchmark_100k.py --enforce
python benchmarks\benchmark_segments_17k.py --enforce
python benchmarks\benchmark_switches_17k.py --enforce
python benchmarks\benchmark_circuits.py --enforce
python benchmarks\benchmark_global_search.py --enforce
python benchmarks\benchmark_loads_100k.py --enforce
python benchmarks\benchmark_load_patterns_400k.py --enforce
python benchmarks\benchmark_branches_100k.py --enforce
```

Os benchmarks geram temporariamente 100 mil barras, 17 mil trechos e 17 mil
chaves, medem a importação/indexação, o desenho agregado em 1920×1080 e a
latência p95 da seleção geométrica de trechos. O benchmark de circuitos também
mede a geração da paleta, a categorização agregada e a troca de cor sem reconstruir
a geometria. O benchmark de ramais cobre 100 mil trechos, 100 circuitos, 100 mil
cargas e 400 mil registros de patamares, incluindo análise, agregação equivalente,
atualização das máscaras e construção do destaque vetorial.

## Organização

A documentação técnica completa — arquitetura em camadas, modelo de dados,
pipeline de renderização, fluxos de importação, concorrência, pontos de extensão
e decisões de projeto — está em [`ARQUITETURA.md`](ARQUITETURA.md). Este README
descreve o uso; aquele documento descreve o funcionamento interno e deve ser
atualizado junto com mudanças relevantes de arquitetura.

- `circuit_viewer/model.py`: modelo lógico e índice espacial.
- `circuit_viewer/csv_import.py`: importação transacional.
- `circuit_viewer/segment_import.py`: importação e vínculo dos trechos.
- `circuit_viewer/switch_import.py`: importação e associação das chaves.
- `circuit_viewer/circuit_import.py`: importação e associação topológica dos circuitos.
- `circuit_viewer/circuit_colors.py`: paleta contrastante e conversão OKLCH/sRGB.
- `circuit_viewer/circuits_window.py`: tabela de visibilidade e cores dos circuitos.
- `circuit_viewer/overlap_report.py`: relatório tabular das sobreposições.
- `circuit_viewer/branch_analysis.py`: análise topológica dos ramais.
- `circuit_viewer/equivalent_network.py`: projeção e agregação das cargas equivalentes.
- `circuit_viewer/branch_window.py`: tabela, filtro e avisos dos ramais.
- `circuit_viewer/mapa_tiles.py`: provedores, matemática XYZ, downloads e cache.
- `circuit_viewer/graphics.py`: canvas, visão agregada e virtualização.
- `circuit_viewer/main_window.py`: interface e integração assíncrona.

As pastas/fontes de referência `src/` e `script20.py` não são modificadas nem
usadas como dependências de runtime.
