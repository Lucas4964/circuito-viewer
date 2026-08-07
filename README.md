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

Para habilitar também o [fluxo de potência](#fluxo-de-potência), troque a linha
de instalação por `python -m pip install -e ".[opendss]"`. Para a
[importação por banco de dados](#importação-por-banco-de-dados-mdb), use
`".[mdb]"`; para os dois, `".[opendss,mdb]"`. Sem esses extras a aplicação
funciona por inteiro, apenas com os itens correspondentes desabilitados.

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

Use **Arquivo > Importar CSV…** e escolha entre barras, trechos, cargas, chaves,
reguladores, circuitos ou cabos. A opção de
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

Ainda depois dos trechos, **Importar reguladores…** carrega os reguladores de
tensão. O CSV deve conter `REGU_ID`, `TRECHO_ID`, `EXTERN_ID`, `CODIGO`,
`LIGACAO`, `SNOM`, `FAIXA`, `NPASSOS`, `TAP`, `INOM` e `VNOM`. Cada registro é
vinculado ao trecho indicado por `TRECHO_ID`, com **no máximo um regulador por
trecho**: o segundo registro do mesmo trecho é descartado e relatado, assim como
`REGU_ID` vazio ou duplicado e trecho inexistente. Todos os campos são
preservados como texto, inclusive os numéricos — zeros à esquerda e vírgula
decimal chegam ao painel exatamente como estão no arquivo.

Ao selecionar um trecho com regulador, o painel lateral exibe a tabela **Dados do
regulador**, abaixo da tabela de chave quando as duas existirem. Os reguladores
também entram na busca global: procurar pelo `REGU_ID` ou pelo `CODIGO` enquadra
o trecho e rola o painel até a seção.

No diagrama, cada trecho com regulador ganha um **anel laranja no seu ponto
médio** — é como se localiza o equipamento na rede sem precisar clicar trecho a
trecho. O anel tem tamanho fixo em pixels, então continua legível em qualquer
zoom, e desaparece junto com o trecho quando o circuito é filtrado.

A importação não altera filtros de circuito nem a topologia: reguladores não
interrompem nem energizam nada, então importá-los não invalida ramais nem rede
simplificada. Um resultado de fluxo de potência já calculado, esse **é**
descartado: reguladores mudam a tensão resolvida.

> **Exportados quando o trecho é trifásico.** Cada regulador vira três
> transformadores monofásicos e três `RegControl` em `reguladores.dss`, e o
> trecho onde ele está deixa de sair como `Line` — veja
> [Exportação para OpenDSS](#exportação-para-opendss). Reguladores em trecho
> não trifásico ainda não são exportados; eles aparecem na lista de ocorrências
> e o trecho segue como linha comum.

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

## Importação por banco de dados (`.mdb`)

**Arquivo > Importar banco de dados…** lê um banco Microsoft Access e importa as
oito entidades de uma vez, dispensando exportar cada tabela para CSV. Os dados
são os mesmos e passam pelas mesmas validações — a única diferença é a fonte.

O banco é aberto **somente para leitura**, em quatro camadas: `ReadOnly=1` na
cadeia de conexão, `SQL_MODE_READ_ONLY` no driver, conexão sem transação e uma
API que só emite `SELECT`. Nenhum comando de escrita existe no código.

> Enquanto a conexão está aberta, o próprio motor do Access cria um arquivo de
> trava `.ldb` ao lado do banco e o remove ao fechar. Ele não altera o `.mdb`,
> mas exige **permissão de escrita na pasta**: num diretório somente leitura o
> Access se recusa a abrir o arquivo, e a mensagem de erro explica isso. Nesse
> caso, copie o banco para uma pasta gravável.

### Requisitos

- `pyodbc` (`python -m pip install -e ".[mdb]"`);
- o **Microsoft Access Database Engine Redistributable**, na **mesma
  arquitetura do Python**. Um Python de 64 bits não enxerga o driver de 32 bits,
  e o sintoma é "driver não encontrado" com o driver visivelmente instalado — a
  mensagem da aplicação diz explicitamente qual arquitetura ela procura.

Sem qualquer um dos dois, a aplicação roda inteira e apenas este item de menu
fica desabilitado, com o motivo na dica.

| Formato | Situação |
|---|---|
| Access 97 (Jet 3) | **Não suportado** pelo driver atual. A aplicação lê o cabeçalho do arquivo e recusa com essa explicação, em vez de repassar o erro genérico do driver; converta em **Salvar Como** no próprio Access |
| Access 2000–2003 (Jet 4) | Suportado |
| Access 2007 (ACE 12) | Suportado |
| Access 2010 ou posterior, `.accdb` | Suportado |

### O diálogo

Escolhido o arquivo, um diálogo mostra as tabelas detectadas, uma por entidade,
com a contagem de registros. Cada linha tem uma caixa de seleção e uma lista com
todas as tabelas do banco: **a detecção automática é sempre ajustável à mão**, e
escolher uma tabela reabilita uma entidade que não foi encontrada. As barras são
obrigatórias — sem elas não há o que desenhar — e o botão de confirmação fica
desabilitado enquanto não estiverem marcadas.

O mesmo diálogo pede zona, hemisfério e **unidade das coordenadas**, como na
importação de barras por CSV. A unidade é deduzida de uma amostra da tabela de
barras e já vem selecionada.

**Se o banco tiver senha**, a aplicação a pede antes de abrir e repergunta
quando não confere, em vez de mostrar o erro do driver. A senha é usada apenas
para abrir a conexão: não é gravada em disco, não vai para log e não aparece em
mensagem de erro.

### Ordem e dependências

As entidades são importadas numa passada só, na ordem imposta pelas dependências
entre elas:

```
barras → cabos → trechos → cargas → patamares → chaves → reguladores → circuitos
```

As chaves vêm antes dos circuitos porque a topologia energizada depende delas.
Uma entidade que falhe — tabela ausente, coluna faltando, nenhum registro
válido — **não interrompe as demais**: ela é listada no relatório com o motivo, e
o que dependia dela é pulado com a explicação. Só as barras são fatais.

Ao final abre-se um relatório único, com uma linha por entidade (tabela de
origem, lidas, válidas, inválidas, situação) e as ocorrências agrupadas. Sem
nada a relatar, apenas a barra de status resume a importação.

### Correspondência entre tabelas e entidades

Fica em `circuit_viewer/config/mdb_tabelas.json`, no mesmo espírito de
`fases2.json`: nome de tabela e de coluna é convenção da concessionária, não
regra da aplicação. O arquivo é lido na inicialização; reinicie depois de
editá-lo. Um erro nele desabilita **apenas** a importação por banco, com o
caminho e o problema na dica.

```json
[
  {"entidade": "barras", "tabelas": ["BARRA"],
   "colunas": {"BARRA_ID": [], "CODIGO": [], "X": [], "Y": []}},
  {"entidade": "cabos", "tabelas": ["CABOS", "CABO"], "colunas": {"...": []}}
]
```

- `tabelas` lista candidatos em ordem de preferência; o primeiro que existir no
  banco vence. Maiúsculas e minúsculas são ignoradas, como no próprio Access.
- Cada coluna obrigatória lista **apelidos aceitos**; a lista vazia significa
  "mesmo nome do CSV". É por aqui que se acomoda uma base cujas colunas tenham
  outro nome, sem tocar em código.
- Só as colunas obrigatórias são lidas. A tabela `CARGA` da base de referência
  tem 43 colunas e apenas 9 interessam; as demais nunca chegam a ser
  consultadas.
- Colunas extras são simplesmente ignoradas. O `CENARIO_ID` de `MODELO_CARGA` é
  um exemplo: a importação usa exatamente as mesmas colunas do CSV.

### Conversão de tipos

Um banco é tipado e o CSV não. A conversão para texto é única e deliberada,
porque três comparações do núcleo são textuais e exatas — `FASES2` contra o
`fases2.json`, `ESTADO` contra `"1"` na topologia, e todo identificador entre
tabelas:

| Valor no banco | Vira |
|---|---|
| Nulo | campo vazio |
| Texto | inalterado |
| Sim/Não | `1` / `0` |
| Número inteiro, ou decimal de valor inteiro | sem casa decimal (`1.0` → `1`) |
| Número decimal | preservado por inteiro (`41.297000885009766`) |
| Data e hora | ISO 8601 |

A regra do valor inteiro não é cosmética: `ESTADO` lido como `"1.0"` faria toda
chave fechada virar aberta, e `FASES2` como `"13.0"` não casaria com o
`fases2.json`.

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
  letras de fase**: cada letra `D`, `E` ou `F` vira uma `Load` com seu par de
  colunas de patamar. Letras fora dessas três são ignoradas, então o `N` de
  `DN` e `DEFN` não conta como fase;
- `DSS` guarda a numeração de nós no formato do OpenDSS (ex.: `"1.2.3"`) e vira
  o sufixo de `Bus1`/`Bus2` dos trechos e chaves. Nas cargas monofásicas ele é
  usado inteiro, preservando o nó de neutro; nas bi e trifásicas o nó de cada
  fase vem do `DSS` da **entrada monofásica** daquela letra.

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

**Exportar > OpenDSS…** gera `trechos.dss`, `chaves.dss`, `reguladores.dss`
(quando houver regulador exportável) e, quando houver cargas
e patamares importados, um arquivo de cargas por contagem de fases:
`cargasmonofasicas.dss`, `cargasbifasicas.dss` e `cargastrifasicas.dss`. Por
cima deles saem `<CODIGO>_Master.dss` e `<CODIGO>_Buscoords.csv`, o arquivo
principal que cria o circuito, chama os demais e resolve, mais as coordenadas
das barras.

A opção só fica disponível com barras, trechos, chaves, circuitos e cabos
importados e um `fases2.json` válido — são as cinco fontes que os dois arquivos
de rede consomem. Cargas e patamares **não** entram nessa lista: sem eles a
exportação continua funcionando e apenas os arquivos de carga deixam de ser
gerados. Quando são gerados, saem os três, mesmo que algum fique só com o
cabeçalho.

Ao acionar, uma janela lista os circuitos do catálogo para você escolher **um**
— marcar outro desmarca o anterior, porque o master cria um `New Circuit`, que
energiza um alimentador só. Em seguida é pedida **uma única pasta de destino**,
que recebe todos os arquivos gerados; se algum deles já existir, a substituição
é confirmada antes.

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

### reguladores.dss

Um regulador trifásico vira **três transformadores monofásicos**, um por fase,
cada um com o seu `RegControl`. Para um regulador de 34,5 kV e 333 kVA nas fases
`D`, `E` e `F`, entre as barras `BARRA1` e `BARRA2`:

```
New Transformer.REG-X-D phases=1 windings=2 XHL=0.01 %LoadLoss=0.01 Buses=[BARRA1.1.0, BARRA2.1.0] conns=[wye, wye] kVs=[19.9186, 19.9186] kVAs=[111, 111]
New Transformer.REG-X-E phases=1 windings=2 XHL=0.01 %LoadLoss=0.01 Buses=[BARRA1.2.0, BARRA2.2.0] conns=[wye, wye] kVs=[19.9186, 19.9186] kVAs=[111, 111]
New Transformer.REG-X-F phases=1 windings=2 XHL=0.01 %LoadLoss=0.01 Buses=[BARRA1.3.0, BARRA2.3.0] conns=[wye, wye] kVs=[19.9186, 19.9186] kVAs=[111, 111]

New RegControl.CTRL-X-D transformer=REG-X-D winding=2 vreg=66.3953 band=3 ptratio=300
New RegControl.CTRL-X-E transformer=REG-X-E winding=2 vreg=66.3953 band=3 ptratio=300
New RegControl.CTRL-X-F transformer=REG-X-F winding=2 vreg=66.3953 band=3 ptratio=300
```

- **Nome** — `REG-<CODIGO>-<FASE>` e `CTRL-<CODIGO>-<FASE>`, com o `CODIGO` do
  regulador saneado; vazio cai no `REGU_ID`, com aviso.
- **Fase → nó** — `D`→1, `E`→2, `F`→3, lido das entradas monofásicas do
  `fases2.json`. O `.0` fecha o neutro do enrolamento em estrela.
- **`kVs`** — `VNOM/√3`, a tensão de fase, porque cada unidade é monofásica
  entre fase e neutro. **`kVAs`** — `SNOM/3`.
- **`vreg`/`band`/`ptratio`** — TP de 115 V: `vreg = 115/√3`, banda fixa de
  3 V (mesma base de `vreg`) e `ptratio = VNOM×1000/115`. Os √3 do primário e
  do secundário se cancelam, então o controle regula a barra em **1,0000 pu**.
- **`XHL` e `%LoadLoss` de 0,01 %** — transformador quase ideal: o regulador
  injeta tensão em série, não impedância.
- Todas as definições de `Transformer` vêm **antes** de todos os `RegControl`,
  porque cada controle referencia o seu transformador pelo nome.

**O trecho onde o regulador está deixa de sair em `trechos.dss`** — o regulador
ocupa o lugar dele, como a chave faz. Ligar os transformadores às mesmas duas
barras *e* manter a linha os deixaria em paralelo, e a linha curto-circuitaria a
injeção de tensão. Consequência: aquele trecho não tem corrente no painel de
fluxo de potência, e a impedância dele sai do modelo — se for longo, um aviso
avisa.

Não são exportados, com o motivo na lista de ocorrências: reguladores em trecho
**não trifásico** (por ora), sem `VNOM`/`SNOM` numéricos positivos, em trecho que
já representa uma chave, ou com `VNOM` incompatível com a do circuito — esta
última pega a troca de unidade (volts em vez de kV), que geraria um modelo aceito
pelo OpenDSS e completamente errado. Em todos esses casos **o trecho continua
saindo como linha comum**. `FAIXA`, `NPASSOS` e `TAP` ainda não são usados: a
faixa de regulação é a padrão do OpenDSS (±10 %, 32 passos).

### Arquivos de carga

São três, um por contagem de fases, e só saem quando cargas **e** patamares
estiverem importados. Cada carga vai para o arquivo do seu `NUMERO_FASES`
mapeado no `fases2.json`; cargas de outra contagem são contabilizadas no
relatório sem virar aviso, porque pertencem a outro arquivo.

A modelagem é a mesma nos três: cada carga vira **uma `Load` monofásica por
fase**, com seu próprio `LoadShape`. Nas multifásicas é o que preserva o
**desequilíbrio entre as fases** — uma única `Load` de `phases=2` ou `3`
distribuiria a potência igualmente, apagando justamente o que os patamares por
fase descrevem. Todos os `LoadShape` vêm antes de todas as `Load`, porque o
`daily` referencia um perfil que o OpenDSS precisa já ter definido.

Uma carga trifásica `DEF` de código `CARGA-1` gera:

```
New LoadShape.PERFIL-CARGA-1-3F-D npts=4 interval=1 mult=[<PD por NPAT>] qmult=[<QD por NPAT>]
New LoadShape.PERFIL-CARGA-1-3F-E npts=4 interval=1 mult=[<PE por NPAT>] qmult=[<QE por NPAT>]
New LoadShape.PERFIL-CARGA-1-3F-F npts=4 interval=1 mult=[<PF por NPAT>] qmult=[<QF por NPAT>]

New Load.CARGA-1-3F-D phases=1 bus1=COD-B.1 conn=wye kV=7.96743 model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-3F-D class=3
New Load.CARGA-1-3F-E phases=1 bus1=COD-B.2 conn=wye kV=7.96743 model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-3F-E class=3
New Load.CARGA-1-3F-F phases=1 bus1=COD-B.3 conn=wye kV=7.96743 model=1 kW=1 kvar=1 daily=PERFIL-CARGA-1-3F-F class=3
```

#### Nome

Toda carga exportada segue `<CODIGO>-<N>F-<FASE>`, onde `N` é a contagem de
fases (`1F`, `2F` ou `3F`) e `FASE` é a letra `D`, `E` ou `F`. O perfil recebe o
mesmo nome com o prefixo `PERFIL-`. Uma carga monofásica `D` de código
`CARGA-1` vira `CARGA-1-1F-D`; uma bifásica `DE`, `CARGA-1-2F-D` e
`CARGA-1-2F-E`. Quando o `CODIGO` está vazio, o nome cai no `CARGA_ID` com
aviso.

As fases saem na ordem em que as letras aparecem no `NOME` do `fases2.json`:
`FD` gera `-2F-F` antes de `-2F-D`. Letras fora de `D`/`E`/`F` são ignoradas, o
que faz `DN` valer como `D` e `DEFN` como `DEF`.

#### Demais parâmetros

- **`bus1`** — `CODIGO` da barra da carga (não o `BARRA_ID`), seguido do nó da
  fase. Nas **multifásicas** o nó vem do `DSS` da entrada **monofásica** daquela
  letra (`D`→`1`, `E`→`2`, `F`→`3`); o `DSS` da entrada multifásica não é usado,
  porque lista os nós em ordem crescente e não na ordem das letras — `FD` tem
  `DSS "1.3"`, então parear posicionalmente inverteria as fases. Nas
  **monofásicas** o `DSS` da própria entrada é usado inteiro, o que preserva o
  nó de neutro explícito: `DN` gera `bus.1.0`.
- **`kV`** — a **tensão de fase** do circuito: como `VNOM` é a tensão de linha,
  ela é dividida por `√3`. É a mesma conversão usada em `C1`, pela mesma razão —
  a carga é ligada entre fase e neutro (`conn=wye`).
- **`kW=1 kvar=1`** — fixos de propósito. A potência real de cada patamar vive
  no `LoadShape`, e o OpenDSS multiplica os dois.
- **`mult` e `qmult`** — os quatro patamares na ordem `NPAT` 0, 1, 2 e 3, com
  seis casas decimais. Cada fase lê só o seu par de colunas: `D` usa `PD`/`QD`,
  `E` usa `PE`/`QE` e `F` usa `PF`/`QF`.
- **`class`** — `1`, `2` ou `3` conforme a contagem de fases. Não tem efeito
  elétrico no OpenDSS: serve para você identificar a origem da carga ao abrir o
  arquivo.

#### Descartes

Uma carga **sai inteira ou não sai**. Se qualquer valor de qualquer uma das
fases for inválido, ou se algum dos nomes já estiver em uso, nenhuma `Load`
daquela carga é emitida e ela entra nos diagnósticos — carga pela metade
subestimaria a demanda em silêncio. Um patamar **zerado é válido**; só vazio e
não numérico invalidam.

Também são descartadas e listadas no relatório as cargas sem os quatro patamares
completos, com `FASES2` sem relação no `fases2.json`, com `NOME` que não resolva
exatamente `N` fases distintas entre `D`, `E` e `F`, com uma fase sem terminal
definido nas entradas monofásicas, ou com `VNOM` inválida. As monofásicas ainda
exigem o `DSS` da própria entrada, já que é dele que sai o nó.

A carga é associada ao circuito pela barra em que está pendurada. Uma barra
compartilhada por dois circuitos selecionados exporta a carga uma vez só, com a
`VNOM` do primeiro circuito escolhido; divergência de `VNOM` entre eles vira
aviso sem descartar a carga.

Os três arquivos de carga criam objetos no mesmo namespace `Load.*`, então os
nomes são verificados em conjunto, como acontece entre `trechos.dss` e
`chaves.dss`. Na prática o infixo `-1F-`/`-2F-`/`-3F-` já impede a coincidência
entre arquivos: duas cargas de contagens diferentes nunca geram o mesmo nome,
mesmo com o mesmo `CODIGO`.

Em todos os arquivos os nomes são saneados para `[A-Za-z0-9_-]` com acentos
reduzidos a ASCII, porque no OpenDSS o ponto separa nós de barra e o espaço
separa propriedades.

### `<CODIGO>_Master.dss` e `<CODIGO>_Buscoords.csv`

O master é o único arquivo executável: os demais só definem elementos. Ele leva
o `CODIGO` do circuito no nome, com o `CIRC_ID` de reserva quando o código está
vazio.

```
Clear
Set DefaultBaseFrequency=60

New Circuit.ALIMENTADOR
~ bus1=COD-A.1.2.3 phases=3 basekv=13.8 pu=1 angle=0 frequency=60
~ MVAsc3=999999 MVAsc1=999999

Redirect trechos.dss
Redirect chaves.dss
Redirect cargasmonofasicas.dss
Redirect cargasbifasicas.dss
Redirect cargastrifasicas.dss

Set Voltagebases=[13.8]
calcvoltagebases
Set mode=daily
Set stepsize=1h
Set number=4
Set time=(0, 0)
Solve

Buscoords ALIMENTADOR_Buscoords.csv
```

A ordem das seções não é estética:

- `Set DefaultBaseFrequency` vem **antes** do `New Circuit` porque a frequência
  base é fixada na criação do circuito; depois seria tarde.
- Os `Redirect` vêm **antes** do `calcvoltagebases`, que precisa de todas as
  barras já definidas. São `Redirect`, não `Compile`, porque o `Compile` trocaria
  o diretório corrente e quebraria o `Buscoords` relativo do fim. A lista reflete
  exatamente os arquivos gerados: sem cargas importadas, só os dois de rede
  aparecem.
- `basekv` e `Set Voltagebases` são tensões de **linha** (o `VNOM` do circuito),
  enquanto as cargas `conn=wye` usam `kV` de **fase**. As duas convenções
  convivem: é assim que o OpenDSS espera cada grandeza.
- `stepsize=1h` e `number=4` casam com os `LoadShape` de `npts=4 interval=1` —
  o `interval` do OpenDSS é em **horas** —, então o `mode=daily` percorre
  exatamente os quatro patamares.
- `MVAsc3`/`MVAsc1` altíssimos dão a barra infinita usual de um estudo de
  alimentador: a rede a montante da subestação não é modelada.

O `<CODIGO>_Buscoords.csv` tem uma linha por barra do circuito, com o **mesmo
nome** usado nos `Bus1`/`Bus2` dos trechos — é isso que faz o OpenDSS casar cada
ponto com o elemento. As coordenadas saem em UTM, na unidade canônica do modelo
(metros), com três casas decimais:

```
COD-A,500000.000,8000000.000
```

Duas barras cujo `CODIGO` colida após o saneamento geram uma coordenada só, com
a segunda descartada e reportada — no OpenDSS a segunda linha apenas
sobrescreveria a primeira.

## Configurações do OpenDSS

O menu **Configurações → OpenDSS…** define parâmetros globais aplicados a **todas
as cargas** do modelo, tanto na exportação quanto no fluxo de potência — os dois
caminhos geram o mesmo arquivo master.

| Parâmetro | Padrão do OpenDSS | Efeito |
|---|---|---|
| `Vminpu` | 0,95 | Abaixo desta tensão a carga deixa de respeitar o seu `model` |
| `Vmaxpu` | 1,05 | Acima desta tensão, idem |

**Por que isso importa.** O exportador emite todas as cargas com `model=1`
(potência constante). Fora da faixa `Vminpu`–`Vmaxpu`, o OpenDSS converte a carga
para **impedância constante** — e não avisa. Num alimentador carregado, isso faz
o estudo subestimar a queda de tensão justamente nas barras críticas. Num caso de
20 km medido aqui, a barra de ponta aparece com **0,897 pu** usando o padrão e
com **0,881 pu** ao baixar `Vminpu` para 0,80, que mantém a carga como potência
constante.

**A configuração é opcional.** A caixa *Aplicar limites de tensão às cargas*
nasce **desmarcada**: nesse estado nenhum comando é acrescentado e o arquivo sai
exatamente como saía antes — o OpenDSS aplica os padrões dele. Marcando-a, o
master ganha duas linhas logo após os `Redirect`:

```
BatchEdit Load..* vminpu=0.8
BatchEdit Load..* vmaxpu=1.2
```

A posição não é livre: `BatchEdit` é comando executivo e exige as `Load` já
definidas pelos `Redirect`, além de ter de vir antes do `Solve`. O diálogo mostra
as linhas exatas em uma pré-visualização antes de você confirmar.

Os campos aceitam `Vminpu` entre 0,100 e 1,000 e `Vmaxpu` entre 1,000 e 2,000. A
faixa precisa conter a tensão nominal — fora disso a carga estaria *sempre*
convertida, o oposto da intenção. Vale notar que o OpenDSS **não** faz essa
verificação: ele aceita `vminpu=-1` em silêncio, e é a aplicação que impede.

Os valores ficam guardados entre sessões, como a preferência de tema. Alterá-los
descarta um resultado de fluxo de potência já calculado, porque ele descreveria o
modelo anterior.

## Curvas horárias

O menu **Configurações → Curvas…** abre o cadastro das curvas de 24 pontos — uma
por hora do dia. Elas são independentes de qualquer importação: existem antes de
haver barras carregadas e sobrevivem a uma nova importação.

A janela é dividida em três partes: a lista das curvas à esquerda, a grade das 24
horas no centro e o gráfico da curva selecionada à direita. A coluna **Hora** é
preenchida sozinha de 1 a 24 e não é editável; só a coluna **Valor** recebe
dados. O gráfico acompanha cada alteração imediatamente.

**Preenchendo os valores.** Dá para digitar célula por célula — com ponto ou
vírgula decimal, a mesma regra do resto da aplicação — ou colar uma coluna
inteira copiada do Excel:

| Atalho | Efeito |
|---|---|
| `Ctrl+V` | Cola a partir da linha selecionada |
| `Ctrl+C` | Copia as células selecionadas; sem seleção, a coluna inteira |
| `Delete` | Esvazia as células selecionadas |

A colagem é tolerante de propósito. Se o bloco tiver duas colunas, a **última** é
usada — é a ordem em que se copia "Hora, Valor" de uma planilha — e a janela
informa isso. Um texto que não seja número (um cabeçalho copiado junto, por
exemplo) é pulado sem interromper o resto, e a hora correspondente fica como
estava: o bloco **não** é compactado, para que as horas seguintes não escorreguem
uma posição. O que passar da hora 24 é descartado, também com aviso. Uma linha
vazia no meio esvazia aquela hora; a linha vazia que o Excel sempre acrescenta ao
final é ignorada.

**Valores aceitos.** Qualquer número finito, inclusive zero e negativos — uma
curva de geração injeta potência e precisa do sinal. Quando a curva cruza o zero,
o gráfico marca a linha de base para os dois lados ficarem distinguíveis.

**Salvando.** As alterações — inclusive exclusões — ficam pendentes até o botão
**Salvar**; até lá dá para desistir. Salvar exige que o nome seja válido e que as
24 horas estejam preenchidas, e a mensagem diz exatamente quais horas faltam.
Fechar a janela com pendências pergunta se você quer salvar, descartar ou
continuar editando.

As curvas ficam em `circuit_viewer/dados/curvas.json`, dentro da própria pasta do
programa, em JSON legível e com acentos preservados. A gravação é atômica: uma
interrupção no meio do caminho não deixa o arquivo pela metade. Se o arquivo for
editado à mão e ficar inválido, a aplicação **abre mesmo assim** — o que não
puder ser lido é descartado e um aviso explica o que aconteceu, em vez de
impedir o programa de iniciar.

Cada curva guarda um identificador interno que a renomeação não altera. É esse
identificador que permitirá associá-las a cargas e geradores nos cálculos, sem
que trocar o nome de uma curva quebre o vínculo.

## Fluxo de potência

O botão **Executar Fluxo de Potência** (na barra de ferramentas e em
**Ferramentas**) resolve a rede sem sair da aplicação. Ele gera internamente
exatamente os mesmos arquivos da exportação acima, compila o master no OpenDSS e
traz as grandezas de volta para o painel lateral — o passo de exportar, abrir o
OpenDSS e ler os resultados por fora deixa de ser necessário.

### Instalação do motor

O motor é uma dependência **opcional**. Sem ele a aplicação roda normalmente,
apenas com o botão desabilitado e o motivo na dica:

```powershell
python -m pip install -e ".[opendss]"
```

### O que é executado

- **Escopo:** um solve por circuito **visível** (os marcados em
  **Visualizar > Circuitos…**). O `New Circuit` do OpenDSS energiza um
  alimentador só, então cada circuito é resolvido na sua vez e os resultados são
  acumulados. Trecho ou barra que pertença a mais de um circuito fica com o
  resultado do **primeiro** processado — a mesma regra de dono usada na
  exportação.
- **Patamares:** os quatro (`NPAT` 0 a 3), colhidos um a um. O `Solve` do master
  deixaria só o último; a aplicação reconduz a solução com `number=1` e resolve
  patamar por patamar.
- **Pré-requisitos:** os mesmos da exportação (barras, trechos, chaves,
  circuitos, cabos e `fases2.json`). Cargas e patamares são opcionais, mas sem os
  dois o modelo sai sem carga alguma e as correntes tendem a zero — a aplicação
  pede confirmação antes de executar nesse caso.

Os arquivos `.dss` gerados vão para uma pasta temporária e são apagados no fim.
Quem quiser os arquivos deve usar **Exportar > OpenDSS…**, que continua
inalterado.

### Onde os resultados aparecem

Selecionando um **trecho** ou uma **barra**, o painel lateral direito ganha a
seção *Resultados do fluxo de potência*, com um seletor de grandeza e uma tabela
de quatro linhas — uma por patamar — e uma coluna por fase:

| Elemento | Grandezas no seletor |
|---|---|
| Trecho | **Corrente por fase (A)**; **Carregamento (%)** — a corrente sobre o `IADM` do cabo de `CABOF_ID`; **Potência ativa (kW)**; **Potência reativa (kvar)**; **Potência aparente (kVA)** com ângulo; **Potência trifásica** — `P`, `Q`, `S` e `θS` totais; **Fator de potência** por fase e trifásico; **Perdas** do elemento |
| Barra | **Tensão de fase (V)** — fase-neutro; **Tensão de linha (V)** — `VDE`, `VEF`, `VFD`; **Tensão de fase (pu)**; **Tensão de linha (pu)**; **Desequilíbrio de tensão (%)** |

As colunas são nomeadas pelas fases do projeto — **Fase D**, **Fase E**,
**Fase F** —, lidas do `fases2.json`: uma configuração que numere as fases de
outro jeito muda os rótulos junto.

**Ângulo dos fasores.** A tensão de fase, a tensão de linha e a corrente trazem,
ao lado dos módulos, uma coluna de ângulo por fase (`θD`, `θE`, `θF`; `θDE`,
`θEF`, `θFD`), em graus e no mesmo referencial do OpenDSS — a fonte em 0°. O
**pu** não repete o ângulo, que é o mesmo da tensão de fase, e o **carregamento**
não tem ângulo por ser uma razão de módulos.

A tensão de linha é a subtração dos **fasores** de duas fases, não a diferença
dos módulos: num sistema equilibrado ela dá `√3` vezes a tensão de fase,
adiantada de 30°. Numa barra monofásica não existe par de fases, então a grandeza
fica desabilitada com o motivo na nota.

Chaves aparecem como trechos comuns nesta leitura: no modelo exportado elas são
`Line` como as demais, e o `IADM` usado no carregamento é o do cabo do trecho
onde a chave está.

A seção só aparece quando aquele elemento tem resultado. Quando o cabo não tem
`IADM` numérico, a opção de carregamento fica desabilitada e uma nota explica o
motivo. Qualquer reimportação (trechos, chaves, circuitos, cabos, cargas,
patamares ou reguladores) descarta o resultado e esconde a seção — número velho
não sobrevive a uma troca de dado.

O trecho substituído por um regulador **não** tem corrente aqui: naquele ponto o
modelo tem um `Transformer`, não uma `Line`.

### Avisos

Ao fim, a barra de status resume quantos circuitos foram resolvidos e quantos
trechos e barras receberam resultado. Havendo ocorrências, abre-se um relatório
com os detalhes:

- circuitos que não puderam ser resolvidos (por exemplo, `VNOM` inválida, que
  impede a geração do master) — os demais continuam sendo resolvidos;
- patamares que não convergiram, identificados por circuito e `NPAT`;
- nomes que diferem apenas em maiúsculas/minúsculas. O OpenDSS não distingue a
  caixa dos nomes, então `TR-01a` e `TR-01A` seriam o mesmo objeto lá dentro; os
  dois são descartados com aviso, porque atribuir a corrente ao trecho errado
  seria pior do que não mostrar corrente alguma.

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
são executados quando PyQt6 estiver instalado. Nenhum teste exige
`py-dss-interface` nem `pyodbc`: o fluxo de potência é exercitado com um motor
OpenDSS falso, e a importação por banco com um banco Access falso.

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
- `circuit_viewer/csv_import.py`: importação transacional e a validação de
  barras compartilhada entre CSV e banco.
- `circuit_viewer/mdb_engine.py`: único acesso ao `pyodbc` (opcional), leitura
  somente leitura e conversão de tipos.
- `circuit_viewer/mdb_mapping.py`: correspondência tabela/coluna → entidade.
- `circuit_viewer/mdb_import.py`: importação encadeada das oito entidades.
- `circuit_viewer/mdb_import_dialog.py`: escolha de tabelas, senha e UTM.
- `circuit_viewer/mdb_import_report.py`: relatório consolidado da importação.
- `circuit_viewer/segment_import.py`: importação e vínculo dos trechos.
- `circuit_viewer/switch_import.py`: importação e associação das chaves.
- `circuit_viewer/regulator_import.py`: importação e associação dos reguladores.
- `circuit_viewer/circuit_import.py`: importação e associação topológica dos circuitos.
- `circuit_viewer/circuit_colors.py`: paleta contrastante e conversão OKLCH/sRGB.
- `circuit_viewer/circuits_window.py`: tabela de visibilidade e cores dos circuitos.
- `circuit_viewer/overlap_report.py`: relatório tabular das sobreposições.
- `circuit_viewer/branch_analysis.py`: análise topológica dos ramais.
- `circuit_viewer/equivalent_network.py`: projeção e agregação das cargas equivalentes.
- `circuit_viewer/branch_window.py`: tabela, filtro e avisos dos ramais.
- `circuit_viewer/opendss_export.py`: geração dos arquivos `.dss` e do master.
- `circuit_viewer/opendss_settings.py`: parâmetros globais das cargas e seus `BatchEdit`.
- `circuit_viewer/opendss_settings_dialog.py`: diálogo de configurações e persistência.
- `circuit_viewer/opendss_engine.py`: único acesso ao `py_dss_interface` (opcional).
- `circuit_viewer/opendss_powerflow.py`: execução do fluxo e associação dos resultados.
- `circuit_viewer/power_flow_table.py`: tabela de grandezas no painel lateral.
- `circuit_viewer/mapa_tiles.py`: provedores, matemática XYZ, downloads e cache.
- `circuit_viewer/graphics.py`: canvas, visão agregada e virtualização.
- `circuit_viewer/main_window.py`: interface e integração assíncrona.

As pastas/fontes de referência `src/` e `script20.py` não são modificadas nem
usadas como dependências de runtime.
