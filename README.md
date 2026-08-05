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

Use **Arquivo > Importar…** e escolha entre barras, trechos, cargas, chaves,
circuitos ou cabos. A opção de
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

O item **Importar cabos…** está sempre disponível: o catálogo de cabos não
depende de barras nem de trechos. O CSV deve conter `CABO_ID`, `TIPO`, `CODIGO`,
`IADM`, `GMR`, `R`, `X`, `QCAP`, `R0`, `X0`, `R1`, `X1`, `NOME` e `EXTERN_ID`.
Todos os campos são preservados como texto, inclusive com vírgula decimal; só
`CABO_ID` é obrigatório e precisa ser único. O catálogo é consultado em
**Tabelas > Cabos…** e nunca altera a rede desenhada — importar cabos não
invalida nada, e importar qualquer outra entidade não invalida os cabos.

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
- **Visualizar > Tema**: alterna a aparência da interface entre **Claro** e
  **Escuro**. A escolha é manual e nunca é inferida do tema do sistema
  operacional; ela é lembrada entre execuções e vale para menus, barras,
  painel lateral, tabelas e diálogos. O canvas do diagrama permanece sempre
  claro, porque as cores dos circuitos são geradas para contrastar com fundo
  branco.
- **Visualizar > Circuitos…**: abre a tabela não modal de circuitos. Desmarcar um
  circuito oculta suas barras, trechos e chaves sem apagar a associação calculada.
  A coluna **Cor** mostra a cor automática do circuito e abre um seletor ao ser
  clicada; alterar a cor não recalcula a topologia.
- **Visualizar > Sobreposições…**: lista os trechos associados a mais de um
  circuito. O relatório também é aberto automaticamente quando uma sobreposição
  é encontrada.
- **Tabelas > Cabos…**: abre a tabela não modal do catálogo de cabos, com as
  catorze colunas na ordem do arquivo. Clicar em um cabeçalho ordena; as colunas
  numéricas (`IADM` a `X1`) ordenam por valor, aceitando ponto **ou** vírgula
  decimal. Sem catálogo importado, a janela mostra um botão **Importar cabos…**.
- **Coluna auxiliar no painel de detalhes**: trechos e cargas exibem uma terceira
  coluna que traduz os identificadores da linha ao lado. No **trecho**, o
  `FASES2` mostra o `NOME` configurado no `fases2.json`, `BARRA1_ID` e
  `BARRA2_ID` mostram o `CODIGO` da barra correspondente e, com o catálogo de
  cabos carregado, `CABOF_ID` e `CABON_ID` mostram o `CODIGO` do cabo. Na
  **carga**, o `BARRA_ID` mostra o `CODIGO` da barra e o `FASES2` mostra o
  `NOME`. Em todos os casos o tooltip do `FASES2` informa o `NUMERO_FASES`, e
  valores sem correspondência exibem `—`.
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
`FASES2` e `NUMERO_FASES`; `NOME` e `DSS` são opcionais, mas os dois passaram a
ser consumidos pela exportação para OpenDSS:

- `NOME` é o texto exibido no painel de detalhes e, na exportação, a **fonte das
  letras de fase**: a primeira letra de uma monofásica escolhe o par de colunas
  de patamar, e as duas letras de uma bifásica definem suas duas `Load`;
- `DSS` guarda a numeração de nós no formato do OpenDSS (ex.: `"1.2.3"`) e vira
  o sufixo de `Bus1`/`Bus2` dos trechos e chaves. Para as cargas bifásicas, o
  nó de cada fase vem do `DSS` da **entrada monofásica** daquela letra.

```json
[
  {"FASES2": "1", "NOME": "D", "NUMERO_FASES": 1, "DSS": "1"},
  {"FASES2": "2", "NOME": "E", "NUMERO_FASES": 1, "DSS": "2"},
  {"FASES2": "3", "NOME": "F", "NUMERO_FASES": 1, "DSS": "3"},
  {"FASES2": "9", "NOME": "FD", "NUMERO_FASES": 2, "DSS": "1.3"},
  {"FASES2": "13", "NOME": "DEF", "NUMERO_FASES": 3, "DSS": "1.2.3"}
]
```

`NUMERO_FASES` aceita somente `1`, `2` ou `3`. Valores de `FASES2` podem ser
texto ou número; espaços e diferenças entre maiúsculas e minúsculas são
ignorados. Relações duplicadas ou inválidas desabilitam apenas esse modo de
visualização e geram um aviso com o caminho e o problema encontrado.

As cores são fixas: `#0000FF` para uma fase, `#00FF00` para duas fases,
`#FF0000` para três fases e `#555555` quando não houver relação no JSON. Os
filtros de visibilidade dos circuitos continuam sendo respeitados.

## Exportação para OpenDSS

**Exportar > OpenDSS…** gera `trechos.dss`, `chaves.dss` e, quando houver cargas
e patamares importados, `cargasmonofasicas.dss` e `cargasbifasicas.dss`. A opção
só fica disponível com barras, trechos, chaves, circuitos e cabos importados e
um `fases2.json` válido — são as cinco fontes que os dois arquivos de rede
consomem. Cargas e patamares **não** entram nessa lista: sem eles a exportação
continua funcionando e apenas os arquivos de carga deixam de ser gerados. Quando
são gerados, saem os dois, mesmo que um fique só com o cabeçalho. Ao acionar,
uma janela lista os circuitos do catálogo para você escolher quais exportar, e
em seguida é pedida **uma única pasta de destino**, que recebe todos os arquivos
gerados; se algum deles já existir, a substituição é confirmada antes.

### `trechos.dss`

Um elemento `Line` por trecho que **não** representa chave. Cada trecho vira uma
linha no formato:

```
New Line.TR-1 Bus1=COD-A.1.2.3 Bus2=COD-B.1.2.3 Phases=3 R1=0.367 X1=0.42 R0=0.551 X0=1.232 C1=50.1433 C0=50.1433 Length=0.25 units=km
```

- **Nome** — `CODIGO` do trecho; quando vazio, cai no `TRECHO_ID` com aviso.
- **`Bus1`/`Bus2`** — `CODIGO` das barras inicial e final (não o `BARRA_ID`),
  seguidos do código `DSS` da configuração de fases do trecho. Barra sem código
  cai no `BARRA_ID`.
- **`Phases`** — `NUMERO_FASES` do mapeamento de `FASES2`.
- **`R1`, `X1`, `R0`, `X0`** — colunas homônimas do cabo de fase (`CABOF_ID`),
  em ohms por quilômetro.
- **`C1`, `C0`** — calculados a partir de `QCAP`. `C1` é a capacitância shunt de
  sequência positiva, entre **fase e neutro**, então a conversão usa a **tensão
  de fase**: como o circuito informa `VNOM` como tensão de **linha** em kV, ela
  é dividida por `√3` antes de entrar em `Q = 2·π·f·C·V²`, com `f = 60 Hz`.
  `C0` recebe o mesmo valor de `C1` — o catálogo tem um único `QCAP`, e sem
  emitir `C0` o OpenDSS assumiria um default sem relação com o cabo.
- **`Length`** — `COMPR` convertido de metros para quilômetros, com `units=km`.

O cálculo assume `QCAP` em **kvar por quilômetro e por fase**, coerente com R e
X da mesma tabela estarem em Ω/km e com a tensão de fase da fórmula.

Trechos sem relação de `FASES2`, sem código `DSS`, com cabo ausente do catálogo,
com campo elétrico não numérico, sem `COMPR`, com `VNOM` inválida ou com nome
repetido são descartados e listados no relatório final.

### `chaves.dss`

Um elemento `Line` com `Switch=Yes` por trecho que **representa** chave — o
complemento exato de `trechos.dss`:

```
New Line.CHV-001 Bus1=COD-B.1.2.3 Bus2=COD-C.1.2.3 Phases=3 Switch=Yes
```

- **Nome** — `CODIGO` da **chave** (de `chaves.csv`), não o do trecho; quando
  vazio, cai no `CHAVE_ID` com aviso.
- **`Bus1`/`Bus2`, `Phases` e o sufixo de nós** — mesmas regras de
  `trechos.dss`, lidos do trecho onde a chave está.
- **Sem `R1`, `Length` ou `units`**: `Switch=Yes` tem efeito colateral
  documentado no OpenDSS — define `r1`, `x1`, `r0`, `x0`, `c1`, `c0` e
  `length=0.001` por conta própria. Por isso ele é sempre a **última**
  propriedade da linha; qualquer parâmetro elétrico escrito depois dele seria
  sobrescrito.

Chaves abertas recebem, **no fim do arquivo**, depois de todas as definições:

```
Open Line.CHV-001 1
```

O `1` é o terminal (o comando abre todas as fases dele). O critério de abertura
é o campo `ESTADO`: só `1` é considerado fechada, exatamente como na topologia
energizada usada pelo restante da aplicação; qualquer outro valor exporta a
chave como aberta, e valores fora de `{0, 1}` ainda geram aviso. Como `Open` é
um comando executivo, o arquivo precisa ser executado com o circuito já
definido.

Esses dois arquivos criam objetos no mesmo namespace `Line.*`, então os nomes são
verificados em conjunto: uma chave cujo nome coincida com o de um trecho já
exportado é descartada e reportada, em vez de sobrescrever a definição anterior
silenciosamente. As cargas ficam fora dessa verificação por viverem em `Load.*`,
um namespace separado.

### `cargasmonofasicas.dss`

Gerado apenas quando cargas **e** patamares estiverem importados. Contém as
cargas cujo `FASES2` esteja mapeado com `NUMERO_FASES=1` — as seis combinações
do `fases2.json`, com e sem neutro explícito (`D`, `E`, `F`, `DN`, `EN`, `FN`).
Cargas de outras contagens de fases são ignoradas e contabilizadas no relatório,
sem virar aviso: as bifásicas têm arquivo próprio e as trifásicas ficam para uma
etapa posterior.

Todos os `LoadShape` vêm antes de todas as `Load`, porque o `daily` referencia
um perfil que o OpenDSS precisa já ter definido:

```
New LoadShape.PERFIL-CARGA-1 npts=4 interval=1 mult=[1.500000 2.500000 3.500000 4.500000] qmult=[0.250000 1.250000 2.250000 3.250000]
New Load.CARGA-1 phases=1 bus1=COD-B.1 conn=wye kV=7.96743 model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1 class=1
```

- **Nome** — `CODIGO` da carga; quando vazio, cai no `CARGA_ID` com aviso. O
  perfil recebe o mesmo nome com o prefixo `PERFIL-`.
- **`bus1`** — `CODIGO` da barra da carga (não o `BARRA_ID`), seguido do código
  `DSS` da configuração de fases. `FASES2=1` gera `.1`; `FASES2=4` gera `.1.0`,
  preservando o nó de neutro.
- **`kV`** — a **tensão de fase** do circuito: como `VNOM` é a tensão de linha,
  ela é dividida por `√3`. É a mesma conversão usada em `C1`, pela mesma razão —
  a carga é ligada entre fase e neutro (`conn=wye`).
- **`kW=1 kvar=1`** — fixos de propósito. A potência real de cada patamar vive
  no `LoadShape`, e o OpenDSS multiplica os dois.
- **`mult` e `qmult`** — os quatro patamares na ordem `NPAT` 0, 1, 2 e 3. As
  colunas vêm da fase da carga: `D` usa `PD`/`QD`, `E` usa `PE`/`QE` e `F` usa
  `PF`/`QF`. As variantes com neutro seguem a primeira letra, então `DN` também
  usa `PD`/`QD`. Todos os valores são arredondados para **seis casas
  decimais**.
- **`class=1`** — sempre `1`, ao final da linha; marca a carga como monofásica
  para identificação manual no arquivo, sem efeito elétrico no OpenDSS. As
  bifásicas usam `class=2`.

A carga é associada ao circuito pela barra em que está pendurada. Uma barra
compartilhada por dois circuitos selecionados exporta a carga uma vez só, com a
`VNOM` do primeiro circuito escolhido; divergência de `VNOM` entre eles vira
aviso sem descartar a carga.

Cargas sem os quatro patamares completos, com valor de patamar não numérico, com
`FASES2` sem relação no `fases2.json`, sem código `DSS`, com `NOME` fora de
`D`/`E`/`F`, com `VNOM` inválida ou com nome repetido são descartadas e listadas
no relatório final. Um patamar **zerado é válido** — só vazio e não numérico
invalidam.

### `cargasbifasicas.dss`

Contém as cargas de `NUMERO_FASES=2`, sob as mesmas condições do arquivo
monofásico. A modelagem, porém, é diferente: para preservar o **desequilíbrio
entre as fases**, cada carga bifásica vira **duas `Load` monofásicas
independentes**, uma por fase, cada uma com seu próprio `LoadShape`. Uma única
`Load` bifásica distribuiria a potência igualmente entre as duas fases.

Uma carga `DE` de código `CARGA-1` gera:

```
New LoadShape.PERFIL-CARGA-1-D npts=4 interval=1 mult=[<PD por NPAT>] qmult=[<QD por NPAT>]
New LoadShape.PERFIL-CARGA-1-E npts=4 interval=1 mult=[<PE por NPAT>] qmult=[<QE por NPAT>]

New Load.CARGA-1-D phases=1 bus1=COD-B.1 conn=wye kV=7.96743 model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-D class=2
New Load.CARGA-1-E phases=1 bus1=COD-B.2 conn=wye kV=7.96743 model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-E class=2
```

- **Nomes** — o nome da carga recebe o sufixo `-<FASE>`, e o perfil de cada fase
  acompanha. As duas fases saem na ordem em que as letras aparecem no `NOME`:
  `FD` gera `-F` antes de `-D`.
- **`bus1`** — o nó de cada fase vem do **terminal da fase isolada** no
  `fases2.json`: a entrada de `NUMERO_FASES=1` cujo `NOME` começa com aquela
  letra (`D`→`1`, `E`→`2`, `F`→`3`). O `DSS` da própria entrada bifásica **não**
  é usado, porque ele lista os nós em ordem crescente e não na ordem das letras
  — `FD` tem `DSS "1.3"`, então parear posicionalmente inverteria as fases.
- **`mult`/`qmult`** — cada fase lê só o seu par de colunas: `D` usa `PD`/`QD`,
  `E` usa `PE`/`QE` e `F` usa `PF`/`QF`.
- **`class=2`** — marca a carga como bifásica, o análogo do `class=1` das
  monofásicas.
- `phases`, `conn`, `kV`, `model`, `kW`, `kvar` e o arredondamento de seis casas
  seguem exatamente as regras do arquivo monofásico.

**Uma carga bifásica sai inteira ou não sai.** Se qualquer um dos oito valores de
qualquer uma das duas fases for inválido, ou se um dos dois nomes já estiver em
uso, nenhuma das duas `Load` é emitida e a carga entra nos diagnósticos — meia
carga no arquivo subestimaria a demanda em silêncio. Um `NOME` que não resolva
exatamente duas fases distintas entre `D`, `E` e `F`, ou uma fase sem terminal
definido no `fases2.json`, também descartam a carga.

Os dois arquivos de carga criam objetos no mesmo namespace `Load.*`, então os
nomes são verificados em conjunto, como acontece entre `trechos.dss` e
`chaves.dss`: uma carga bifásica cujo nome de fase coincida com o de uma carga
monofásica já exportada é descartada e reportada.

Em todos os arquivos os nomes são saneados para `[A-Za-z0-9_-]` com acentos
reduzidos a ASCII, porque no OpenDSS o ponto separa nós de barra e o espaço
separa propriedades.

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
