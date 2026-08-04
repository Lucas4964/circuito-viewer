# Arquitetura Técnica — Visualizador de Circuitos Elétricos

Documento de referência técnica do projeto `circuit-viewer`. Descreve estrutura,
arquitetura, fluxos de execução, modelo de dados, renderização, interação e
pontos de extensão.

> **Manutenção:** este documento deve ser atualizado sempre que um módulo novo
> for adicionado, uma entidade do modelo mudar, o pipeline de renderização for
> alterado ou uma nova etapa de importação for introduzida. O `README.md`
> descreve *como usar*; este arquivo descreve *como funciona*.

**Estado de referência:** versão `0.1.0`, Python ≥ 3.11, PyQt6 6.7+,
NumPy 2.x, pyproj 3.5+.

---

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Estrutura de diretórios](#2-estrutura-de-diretórios)
3. [Arquitetura em camadas](#3-arquitetura-em-camadas)
4. [Responsabilidades por módulo](#4-responsabilidades-por-módulo)
5. [Modelo de dados e relacionamentos](#5-modelo-de-dados-e-relacionamentos)
6. [Fluxo de carregamento dos CSV](#6-fluxo-de-carregamento-dos-csv)
7. [Fluxo de execução da aplicação](#7-fluxo-de-execução-da-aplicação)
8. [Sistema de renderização](#8-sistema-de-renderização)
9. [Seleção, interação e navegação](#9-seleção-interação-e-navegação)
10. [Estruturas de dados e finalidades](#10-estruturas-de-dados-e-finalidades)
11. [Concorrência e ciclo de vida das threads](#11-concorrência-e-ciclo-de-vida-das-threads)
12. [Subsistemas analíticos](#12-subsistemas-analíticos)
13. [Camada de satélite](#13-camada-de-satélite)
14. [Busca global](#14-busca-global)
15. [Dependências](#15-dependências)
16. [Decisões de projeto e justificativas](#16-decisões-de-projeto-e-justificativas)
17. [Pontos de extensão](#17-pontos-de-extensão)
18. [Testes e benchmarks](#18-testes-e-benchmarks)

---

## 1. Visão geral

Aplicação desktop PyQt6 para importar, visualizar e analisar redes elétricas de
distribuição georreferenciadas em coordenadas UTM. O usuário importa até seis
arquivos CSV encadeados (barras → trechos → chaves/cargas → patamares →
circuitos), navega em um canvas com fundo de satélite opcional, inspeciona
atributos, filtra por circuito e executa análises topológicas (ramais e rede
simplificada por cargas equivalentes).

O projeto foi dimensionado para escala industrial: os benchmarks cobrem
100 mil barras, 100 mil trechos, 100 mil cargas e 400 mil registros de patamares.
Essa premissa explica quase todas as decisões arquiteturais — colunas NumPy em
vez de objetos por registro, índices espaciais estáticos, renderização agregada
com virtualização e todo trabalho pesado fora da thread de interface.

---

## 2. Estrutura de diretórios

```
CIRCUITO_VIEWER/
├── circuit_viewer/            # pacote da aplicação (único código de runtime)
│   ├── __init__.py            # fachada pública (re-exporta a API do pacote)
│   ├── __main__.py            # ponto de entrada (QApplication + MainWindow)
│   ├── config/
│   │   └── fases2.json        # mapeamento FASES2 → NUMERO_FASES (dado externo)
│   │
│   ├── model.py               # NÚCLEO: entidades, índices espaciais, topologia
│   ├── circuit_colors.py      # paleta OKLCH contrastante
│   ├── phase_config.py        # carga e validação de fases2.json
│   │
│   ├── csv_import.py          # importação de barras (+ exceções compartilhadas)
│   ├── segment_import.py      # importação de trechos
│   ├── switch_import.py       # importação de chaves
│   ├── load_import.py         # importação de cargas
│   ├── load_pattern_import.py # importação de patamares (NPAT 0–3)
│   ├── circuit_import.py      # importação de circuitos + build da topologia
│   │
│   ├── branch_analysis.py     # análise topológica de ramais
│   ├── equivalent_network.py  # projeção simplificada / cargas equivalentes
│   ├── search.py              # índice de busca global (sem Qt)
│   ├── mapa_tiles.py          # matemática XYZ + gerenciador de tiles
│   │
│   ├── graphics.py            # canvas, itens agregados, virtualizadores
│   ├── workers.py             # wrappers QObject para execução em QThread
│   ├── main_window.py         # orquestração da UI e do estado da aplicação
│   ├── circuits_window.py     # tabela de circuitos (visibilidade + cor)
│   ├── branch_window.py       # tabela de ramais (filtro, ordenação, avisos)
│   ├── overlap_report.py      # relatório de trechos sobrepostos
│   ├── search_palette.py      # janela de busca não modal
│   ├── load_pattern_table.py  # tabela de patamares no painel lateral
│   └── phase_legend.py        # legenda flutuante do modo por fases
│
├── tests/                     # 21 arquivos de teste (unittest + pytest-qt)
├── benchmarks/                # 8 benchmarks com modo --enforce
├── README.md                  # documentação de uso
├── ARQUITETURA.md             # este documento
└── pyproject.toml             # metadados, dependências, entry point
```

### Diretórios ignorados

`src/` (código-fonte do QGIS usado apenas como referência de leitura) e
`script20.py` (script monolítico legado) estão no `.gitignore` e **não são
dependências de runtime**. `.venv/`, `build/`, `dist/` e caches também são
ignorados. Nenhum código do pacote importa nada dessas pastas.

---

## 3. Arquitetura em camadas

A regra estruturante é: **o núcleo lógico não importa Qt**. Isso permite testar
modelo, importadores e análises sem display, e permite executá-los em threads
secundárias sem cuidados com afinidade de objetos Qt.

```
┌─────────────────────────────────────────────────────────────────┐
│ CAMADA DE APRESENTAÇÃO (PyQt6 Widgets)                          │
│ main_window · circuits_window · branch_window · overlap_report  │
│ search_palette · load_pattern_table · phase_legend              │
└───────────────────────────┬─────────────────────────────────────┘
                            │ sinais/slots
┌───────────────────────────┴─────────────────────────────────────┐
│ CAMADA GRÁFICA (QGraphicsScene/View)                            │
│ graphics.py: DiagramView, *OverviewItem, *NetworkItem,          │
│              ItemVirtualizer, LoadVirtualizer, overlays         │
│ mapa_tiles.py: GerenciadorTiles (QObject + QtNetwork)           │
└───────────────────────────┬─────────────────────────────────────┘
                            │ leitura de arrays / índices
┌───────────────────────────┴─────────────────────────────────────┐
│ CAMADA DE ADAPTAÇÃO ASSÍNCRONA                                  │
│ workers.py: QObject + pyqtSlot movidos para QThread             │
└───────────────────────────┬─────────────────────────────────────┘
                            │ chamadas puras
┌───────────────────────────┴─────────────────────────────────────┐
│ NÚCLEO LÓGICO — SEM QT                                          │
│ model.py · *_import.py · branch_analysis · equivalent_network   │
│ search · phase_config · circuit_colors                          │
│ (matemática de tiles em mapa_tiles também é pura)               │
└─────────────────────────────────────────────────────────────────┘
```

Exceções conhecidas e deliberadas:

- `phase_config.py` importa NumPy (não Qt) — aceitável, o núcleo é NumPy-based.
- `mapa_tiles.py` mistura as duas naturezas no mesmo arquivo: as funções de
  matemática XYZ (`lonlat_para_tile`, `tile_bbox`, `nivel_zoom`, …) são puras e
  testáveis headless; `GerenciadorTiles`/`_TransporteTiles` usam QtNetwork.
- `graphics.py` importa `EquivalentNetworkModel` apenas para tipagem do union
  `LoadRenderModel`.

---

## 4. Responsabilidades por módulo

### Núcleo

| Módulo | Responsabilidade | Não faz |
|---|---|---|
| `model.py` | Entidades imutáveis, modelos colunares, índices espaciais, adjacência CSR, busca elétrica, controlador de visibilidade | I/O, Qt |
| `phase_config.py` | Ler/validar `fases2.json`; classificar `FASES2` em categorias de renderização | Definir cores da UI (só as constantes) |
| `circuit_colors.py` | Gerar paleta contrastante em OKLCH; normalizar `#RRGGBB` | Aplicar cores |
| `search.py` | Índice por `CODIGO` e índice por todas as colunas; consultas canceláveis | Widgets |

### Importadores (um por entidade)

Todos seguem o mesmo contrato: `load_*_csv(path, dependência, *, cancel_event,
progress) -> *LoadResult`. São **transacionais** — ou retornam um modelo válido
completo, ou levantam exceção; nunca mutam estado externo.

| Módulo | Entidade | Depende de | Colunas obrigatórias |
|---|---|---|---|
| `csv_import.py` | Barra | — (só do `UtmCrs`) | `BARRA_ID, CODIGO, X, Y` |
| `segment_import.py` | Trecho | `CircuitModel` | `TRECHO_ID, CODIGO, FASES2, BARRA1_ID, BARRA2_ID, ARRANJO_ID, CABOF_ID, CABON_ID, COMPR` |
| `switch_import.py` | Chave | `LineNetworkModel` | `CHAVE_ID, TIPOCHV_ID, CIRC_ID, TRECHO_ID, CODIGO, ESTADO, ESTADO_NORMAL, CORN, ELO, ELO_TIPO` |
| `load_import.py` | Carga | `CircuitModel` | `CARGA_ID, BARRA_ID, EXTERN_ID, CODIGO, SNOM, SADM, VLINHASEC, FASES2, TIPO_LIG` |
| `load_pattern_import.py` | Patamar | `LoadModel` | `CARGA_ID, NPAT, PD, PE, PF, QD, QE, QF` |
| `circuit_import.py` | Circuito | `LineNetworkModel` + `SwitchModel?` | `CIRC_ID, BARRA_ID, CODIGO, VNOM` |

`csv_import.py` também exporta as exceções compartilhadas `CsvImportError`
(fatal) e `CsvImportCancelled` (interrupção do usuário), reutilizadas por todos
os demais importadores.

### Análise

| Módulo | Entrada | Saída |
|---|---|---|
| `branch_analysis.py` | `CircuitCatalogModel`, `PhaseConfiguration`, `LoadModel?` | `BranchAnalysisResult` (ramais + diagnósticos) |
| `equivalent_network.py` | `BranchAnalysisResult`, `LoadModel?`, `LoadPatternModel?` | `EquivalentNetworkResult` (cargas equivalentes + máscaras) |

### Gráfico e UI

| Módulo | Responsabilidade |
|---|---|
| `graphics.py` | Toda a pintura, virtualização, hit-test geométrico, zoom/pan, desenho do fundo de satélite |
| `workers.py` | 8 workers `QObject` que apenas encapsulam funções puras e emitem `progress/finished/failed/cancelled` |
| `main_window.py` | Dono de todo o estado da aplicação; coordena importações, invalidações em cascata, máscaras efetivas, painel de detalhes e menus |
| `circuits_window.py` | `QAbstractTableModel` fino sobre `CircuitVisibilityController` + delegate de cor |
| `branch_window.py` | Tabela de ramais com `QSortFilterProxyModel` (ordenação por `UserRole`, filtro por circuito) |
| `overlap_report.py` | Tabela derivada de `overlapping_segment_indices` |
| `search_palette.py` | Diálogo não modal; roda indexação e consultas em `QThreadPool` com tokens de cancelamento |
| `load_pattern_table.py` | Modelo somente leitura de exatamente 4 linhas (NPAT 0–3) |
| `phase_legend.py` | `QFrame` filho do viewport, transparente a mouse, reposicionado a cada mudança de viewport |

---

## 5. Modelo de dados e relacionamentos

### Diagrama de dependências entre entidades

```
                        UtmCrs
                          │
                          ▼
                   ┌─────────────┐
                   │ CircuitModel│  (barras — raiz de tudo)
                   └──┬───┬────┬─┘
        bar_indices   │   │    │  start_indices / end_indices
          ┌───────────┘   │    └──────────────┐
          ▼               │                   ▼
    ┌──────────┐          │          ┌──────────────────┐
    │ LoadModel│          │          │ LineNetworkModel │ (trechos)
    └────┬─────┘          │          └───────┬────┬─────┘
         │ 1:0..1         │  segment_indices │    │
         ▼                │                  ▼    │
 ┌──────────────────┐     │          ┌────────────┐│
 │ LoadPatternModel │     │          │ SwitchModel││ (1 chave por trecho)
 └──────────────────┘     │          └──────┬─────┘│
                          │                 │      │
                          │  ┌──────────────┴──────┘
                          │  ▼
                   ┌──────┴────────────────┐
                   │ NetworkTopology (CSR) │
                   └───────────┬───────────┘
                               ▼
                   ┌───────────────────────┐
                   │ CircuitCatalogModel   │ (circuitos + memberships)
                   └───────────┬───────────┘
                               ▼
                ┌──────────────────────────────┐
                │ CircuitVisibilityController  │ (estado visual mutável)
                └──────────────────────────────┘
```

### Regra de identidade

O vínculo entre modelos é por **identidade de objeto** (`is`), não por igualdade
de conteúdo. `LineNetworkModel.bars is CircuitModel`, `SwitchModel.segments is
LineNetworkModel`, etc. Toda a UI valida essa identidade antes de aceitar um
resultado de worker:

```python
if self._model is None or result.model.bars is not self._model:
    # as barras foram substituídas durante a importação → descarta
```

Isso torna impossível combinar modelos de importações diferentes e é o mecanismo
central de consistência transacional entre threads.

### Entidades (dataclasses imutáveis, `frozen=True, slots=True`)

| Dataclass | Campos-chave |
|---|---|
| `Bounds` | `left, top, right, bottom` (+ `width`, `height`, `expanded`) |
| `UtmCrs` | `zone` (1–60), `northern` → `epsg` (326xx/327xx), `label` |
| `BarRecord` | `bar_id, code, x, y` |
| `SegmentRecord` | `segment_id, code, phases, start_bar_id, end_bar_id, arrangement_id, phase_cable_id, neutral_cable_id, length` |
| `SwitchRecord` | `switch_id, switch_type_id, circuit_id, segment_id, code, state, normal_state, corn, elo, elo_type` |
| `LoadRecord` | `load_id, bar_id, external_id, code, snom, sadm, secondary_line_voltage, phases, connection_type` |
| `LoadPatternRecord` | `load_id, npat ∈ {0,1,2,3}, pd, pe, pf, qd, qe, qf` |
| `CircuitDefinition` | `circuit_id, root_bar_id, code, nominal_voltage` |
| `CircuitMembership` | `bar_indices, common_segment_indices, switch_segment_indices, segment_indices` |
| `FeatureSelection` | `kind ∈ {bar, segment, load, equivalent_load}`, `index` |

Os `*Record` são **views materializadas sob demanda** (`model.record(i)`), não o
armazenamento. O armazenamento real é colunar.

### Modelos colunares

Cada `*Model` guarda tuplas de `str` para colunas textuais e `ndarray` para
colunas numéricas/índices, mais um `dict[str, int]` de ID → índice. Arrays são
marcados `setflags(write=False)` após a construção; a imutabilidade é a base da
segurança entre threads.

**`CircuitModel`** — `_bar_ids`, `_codes` (tuplas), `_x`, `_y` (float64),
`_by_id`, `_bounds`, `_spatial_index` (`StaticPointIndex`). Rejeita IDs vazios,
IDs duplicados e coordenadas não finitas; exige ≥ 1 barra.

**`LineNetworkModel`** — referencia barras por `_start_indices`/`_end_indices`
(intp), nunca duplicando coordenadas. `_lengths` usa `NaN` para `COMPR` vazio
(o `SegmentRecord.length` converte `NaN` → `None`). Índice espacial:
`StaticSegmentIndex`.

**`SwitchModel`** — além de `_by_id`, mantém `_record_by_segment`: vetor de
tamanho `len(segments)` com o índice do registro de chave ou `-1`. Isso torna a
consulta “este trecho é uma chave?” O(1) durante travessias BFS, que é o caminho
crítico da análise topológica. Invariante: **no máximo uma chave por trecho**.

**`LoadModel`** — reutiliza as coordenadas das barras via `bar_indices`; o índice
espacial é construído sobre `bars.x[bar_indices]`, o que permite hit-test de
cargas sem duplicar geometria.

**`LoadPatternModel`** — armazenamento *denso por carga*:
`tuple[tuple[LoadPatternRecord,...] | None]` com um slot por carga, alinhado com
os índices de `LoadModel`. Grupos são obrigatoriamente completos (NPAT 0,1,2,3
ordenados) ou `None`; grupos parciais são descartados no importador.

### `NetworkTopology` — adjacência CSR

Estrutura compacta construída uma vez por rede:

```
incidence_offsets  : intp[n_barras + 1]   — offsets CSR por barra
incidence_segments : intp[2 * n_trechos]  — índice do trecho em cada incidência
incidence_neighbors: intp[2 * n_trechos]  — barra oposta em cada incidência
```

Iterar vizinhos de uma barra é `range(offsets[b], offsets[b+1])` — sem alocação,
sem dicionários, com localidade de cache. Cada trecho aparece duas vezes (uma
por extremidade), o que dá arestas não direcionadas com custo O(1).

**Marcação por geração:** `_bar_marks`/`_segment_marks` são vetores `int64`
comparados contra um contador `_generation` incrementado a cada busca. Isso
evita zerar vetores de 100 mil posições entre buscas — o custo de “limpar” é
zero. Há reset defensivo ao aproximar de `int64.max`. O mesmo padrão é replicado
em `branch_analysis.py` com ~12 vetores de marcação independentes.

**`trace(circuit_id, root_bar_index, direct_switch_indices)`** faz BFS elétrico:

- trecho **comum** → atravessa e registra em `common_segment_indices`;
- trecho **chave** → atravessa apenas se `ESTADO == "1"` **e**
  `CIRC_ID == circuit_id`; nunca é registrado como comum;
- chaves são associadas ao circuito **diretamente pelo `CIRC_ID`**
  (`switch_segment_indices`), independentemente de terem sido atravessadas.

`segment_indices` é a concatenação dos dois conjuntos.

### `CircuitCatalogModel`

Guarda definições + memberships e constrói um índice reverso CSR
segmento → circuitos:

```
_segment_circuit_offsets : intp[n_trechos + 1]
_segment_circuit_indices : intp[total de associações]
_segment_owner_counts    : intp[n_trechos]
_overlapping_segment_indices = flatnonzero(owner_counts > 1)
```

Sobreposição (um trecho pertencendo a mais de um circuito) é detectada
gratuitamente aqui e alimenta o relatório automático.

`CircuitCatalogModel.build()` é o construtor de alto nível: valida chaves
(`ESTADO` fora de `{0,1}`, `CIRC_ID` inexistente) acumulando `topology_warnings`
em vez de falhar, e roda um `trace` por circuito.

### `CircuitVisibilityController`

Separa o **estado visual mutável** das associações elétricas imutáveis. Mantém
contadores de referência para permitir toggles O(|membership|) em vez de
recomputar máscaras globais:

```
_bar_owner_counts / _bar_visible_counts        → _bar_mask
_segment_owner_counts / _segment_visible_counts → _segment_mask

visível = (owner_count == 0) or (visible_count > 0)
```

Elementos sem dono nenhum (não pertencentes a circuito algum) permanecem sempre
visíveis. `_segment_style_indices` guarda o circuito **efetivo** de cada trecho:
`-1` = estilo padrão, `-2` = oculto, `≥ 0` = primeiro circuito visível entre os
donos — é isso que resolve a cor de um trecho sobreposto.

---

## 6. Fluxo de carregamento dos CSV

### Ordem e dependências

```
                 ┌──── Barras (obrigatória, primeira) ────┐
                 │                                        │
                 ▼                                        ▼
            Trechos                                    Cargas
             │    │                                       │
             ▼    ▼                                       ▼
        Chaves   Circuitos                            Patamares
                  ▲
                  └─ usa Chaves (opcional) para a topologia energizada
```

O `ImportChoiceDialog` habilita cada botão conforme o estado:
`segments/loads` exigem barras; `switches/circuits` exigem trechos;
`load_patterns` exige cargas.

### Pipeline comum de cada importador

1. **Detecção de encoding** — tenta `utf-8-sig`; em `UnicodeDecodeError`,
   reprocessa o arquivo inteiro em `cp1252`. O encoding usado é reportado.
2. **Cabeçalho** — separador `;`; normaliza cada nome com `.strip()` e remoção de
   BOM. Colunas obrigatórias podem estar em qualquer ordem; extras são ignoradas;
   ausentes **ou duplicadas** geram `CsvImportError` fatal.
3. **Iteração por linha** (a partir da linha 2):
   - checa `cancel_event` a cada linha;
   - a cada 1.000 linhas emite progresso `(linhas, bytes_lidos, bytes_totais)`
     usando `source.buffer.tell()` — progresso por bytes, não por contagem
     estimada;
   - linhas vazias são puladas sem contar;
   - linhas curtas demais, IDs vazios/duplicados, referências inexistentes e
     valores inválidos viram `*Issue` e a linha é descartada.
4. **Limite de relatório** — no máximo `MAX_REPORTED_ISSUES = 200` ocorrências
   detalhadas; o excedente vira `omitted_issues`.
5. **Construção do modelo** — se zero linhas válidas, `CsvImportError`. Caso
   contrário instancia o `*Model`, que revalida invariantes (a validação do
   importador e a do modelo são independentes por design).
6. **Retorno** — `*LoadResult` com `model`, `encoding`, `total_rows`,
   `valid_rows`, `invalid_rows`, `issues`, `omitted_issues` e a propriedade
   `has_warnings` (usada para decidir entre barra de status e `QMessageBox`).

### Particularidades por arquivo

**Barras** — `X`/`Y` aceitam ponto ou vírgula decimal, mas **não os dois**
(`"1.234,5"` é rejeitado como “separadores decimal e de milhar misturados”).
Coordenadas devem ser finitas. Zona, hemisfério UTM e **unidade das coordenadas**
vêm do `UtmImportDialog`, não do arquivo.

**Unidade canônica: metro.** `load_csv(..., scale=)` divide X e Y ainda no
parsing, de modo que `CircuitModel` — e tudo que dele deriva — sempre guarda
metros, a mesma unidade de `COMPR`. `detect_coordinate_scale()` lê uma amostra
(5.000 linhas por padrão, para não pagar uma passada completa antes do diálogo)
e **devolve o divisor** — o menor de `COORDINATE_UNITS` que coloca as duas faixas
dentro do envelope UTM (`UTM_EASTING_RANGE`, `UTM_NORTHING_RANGE`), ou `1.0` se
o arquivo não pôde ser amostrado ou nenhuma unidade encaixar.

`scale` é um **divisor de unidade aplicado uma única vez**, não um fator de
renderização: a cena permanece em UTM cru (ver seção 8). É a diferença para o
`escala_atual` do `script20.py`, que reescala as coordenadas para dentro do
sistema da cena a cada conversão geo↔cena.

Depois de montar o modelo, `utm_range_warning()` compara os bounds com o mesmo
envelope e preenche `CsvLoadResult.crs_warning`. Isso importa porque coordenadas
fora da faixa **não falham de forma visível**: o `pyproj` satura, o ponto vai
parar no oceano e a transformação deixa de ser invertível — os tiles de satélite
são posicionados a milhões de unidades da rede e o fundo simplesmente parece
vazio.

**Trechos** — resolve `BARRA1_ID`/`BARRA2_ID` para índices via
`bars.index_for_id()`; referências inexistentes são omitidas e relatadas.
`COMPR` vazio → `NaN` (válido); negativo ou não finito → linha inválida.

**Chaves** — resolve `TRECHO_ID` para índice; rejeita `TRECHO_ID` já usado por
outra chave (invariante 1:1). `ESTADO` é preservado como texto e só interpretado
na topologia.

**Cargas** — resolve `BARRA_ID`; todos os demais campos permanecem texto
(inclusive `SNOM`/`SADM`, convertidos para `Decimal` só na agregação
equivalente).

**Patamares** — acumula em `dict[load_index][npat]` e só valida no fim:
o grupo de uma carga é aceito somente se tiver exatamente NPAT 0,1,2,3, sem
duplicatas e sem valores inválidos. Grupos rejeitados não impedem os demais.
Falha total apenas se nenhum grupo completo existir.

**Circuitos** — após parsear as definições, chama
`CircuitCatalogModel.build(segments, switches, definitions)`, que executa a busca
topológica por circuito. É o único importador cujo custo dominante não é o
parsing e sim o BFS; por isso propaga `cancel_check` para dentro do `trace`.

### Substituição em cascata

Importar uma entidade invalida tudo o que depende dela. Em `main_window.py`:

```
_set_line_model(m)     → invalida ramais, catálogo de circuitos e chaves
_set_load_model(m)     → invalida ramais e patamares
_set_switch_model(m)   → invalida ramais; RECONSTRÓI o catálogo preservando
                         estado visual (checked/cores) por CIRC_ID
_set_circuit_catalog() → invalida ramais; recria CircuitVisibilityController
_on_import_finished()  → barras: limpa cargas e trechos ANTES de assumir o novo
                         modelo (eles referenciam o modelo antigo)
```

O caso de `_set_switch_model` é o mais sutil: importar chaves depois de
circuitos muda a topologia energizada, então o catálogo é reconstruído com as
mesmas `definitions` e o estado visual é remapeado por `circuit_id` — o usuário
não perde as cores nem os filtros que escolheu.

---

## 7. Fluxo de execução da aplicação

### Inicialização

```
python -m circuit_viewer
  └─ __main__.main()
       ├─ importa PyQt6 (erro amigável se ausente)
       ├─ instala sys.excepthook (traceback no console, sem matar a app)
       ├─ QApplication(sys.argv)
       ├─ MainWindow()
       │    ├─ carrega fases2.json → PhaseConfiguration ou registra erro
       │    ├─ QGraphicsScene com ItemIndexMethod.NoIndex
       │    ├─ DiagramView(scene)
       │    ├─ ItemVirtualizer (barras) + 2 × LoadVirtualizer (cargas, equivalentes)
       │    ├─ overlays: seleção de trecho, destaque de ramal
       │    ├─ GlobalSearchIndex + SearchPalette + PhaseLegend
       │    ├─ modelos/janelas de tabela (circuitos, sobreposições, ramais)
       │    └─ ações, menus, toolbar, dock de detalhes, status bar
       └─ window.showMaximized(); app.exec()
```

`ItemIndexMethod.NoIndex` é deliberado: a cena tem poucos itens de fato
(agregados + no máximo ~1.000 itens materializados por camada), e o índice BSP
do Qt custaria mais do que economizaria. O índice espacial real é o do modelo.

### Ciclo de uma importação

```
_choose_import()
  → ImportChoiceDialog → QFileDialog → (barras: UtmImportDialog)
  → _start_*_import(path)
       ├─ QThread + *ImportWorker; worker.moveToThread(thread)
       ├─ QProgressDialog (WindowModal, cancelável)
       ├─ conecta: started→run, progress, finished/failed/cancelled→thread.quit
       ├─ desabilita import_action e branches_action
       └─ thread.start()
  → worker.run() executa a função pura no thread secundário
  → _on_*_import_finished(result)   [thread da UI]
       ├─ VALIDA IDENTIDADE do modelo-pai
       ├─ _set_*_model(result.model) → cascata de invalidação
       ├─ atualiza índice de busca, status bar, ações
       └─ _show_*_import_report(result)
  → _on_import_thread_finished()  → limpa referências, reabilita ações
```

### Ciclo de renderização por quadro

```
evento (wheel/pan/resize/scroll)
  └─ DiagramView.viewportChanged
       ├─ ItemVirtualizer.schedule_refresh()      (debounce 120 ms)
       ├─ LoadVirtualizer.schedule_refresh() × 2  (debounce 120 ms)
       └─ MainWindow._schedule_viewport_overlay_update() (timer 0 ms)
              └─ reposiciona a legenda de fases

repaint do viewport
  ├─ drawBackground → _draw_satellite (se habilitado)
  ├─ itens da cena por Z:
  │     -20 LineNetworkItem · -15 SwitchNetworkItem
  │     -11 LoadsOverviewItem · -10 BarsOverviewItem
  │      10 BarraItem ·  20 LoadItem
  │      90 SegmentSelectionOverlay · 95 BranchHighlightOverlay
  │     100 SelectionOverlay · 110 LoadSelectionOverlay
  └─ paintEvent → atribuição do provedor de satélite (canto inferior direito)
```

---

## 8. Sistema de renderização

### Estratégia híbrida agregado + virtualizado

O problema: `QGraphicsItem` tem custo fixo por item (paint, boundingRect,
transformações, hit-test). Com 100 mil barras, materializar tudo é inviável.
A solução é uma **camada agregada** sempre presente e uma **camada
materializada** ativada só quando a densidade permite.

```
indices_visiveis = spatial_index.query_rect(viewport + 25% de margem)
indices_visiveis = indices_visiveis[visibility_mask[indices_visiveis]]

se len(indices) > MAX_ACTIVE_ITEMS (1.000):
    modo "Visão geral"  → só o item agregado pinta (1 drawPoints)
senão:
    modo "Detalhado"    → materializa em lotes de 250 via QTimer(0)
```

Durante a materialização o agregado permanece visível e só é ocultado após o
último lote — isso evita um quadro em branco.

### Itens agregados

| Classe | Z | Técnica |
|---|---|---|
| `BarsOverviewItem` | −10 | `QPolygonF` de pontos + `drawPoints` com pen cosmético `RoundCap` |
| `LoadsOverviewItem` | −11 | idem, `SquareCap`, diâmetro 7 px |
| `LineNetworkItem` | −20 | `dict[categoria → QPainterPath]` com subcaminhos desconectados; 1 `drawPath` por cor |
| `SwitchNetworkItem` | −15 | `_red_path` único no modo circuito; `_colored_paths` por categoria no modo fases |

Todos usam `DeviceCoordinateCache(4096×4096)`: entre mudanças de máscara, o Qt
reaproveita a rasterização em pan e repaints, sem repercorrer os pontos.

O agrupamento por categoria em `LineNetworkItem` é a chave da performance com
cores: em vez de um `QPen` por trecho, há um `QPainterPath` por cor e uma troca
de pen por categoria. O caminho só é recompilado quando **a máscara ou os
estilos** mudam (`geometry_changed`); mudar apenas cores dispara só `update()`.

### Sistema de coordenadas

```
modelo (UTM, Y para o norte)  →  cena (Y invertido)
    cena_x = utm_x
    cena_y = -utm_y
```

A inversão mantém o norte para cima na tela (em Qt, Y cresce para baixo). Toda
conversão passa por `_scene_point()` e `_model_bounds_from_scene()`; nunca
espalhe o sinal negativo por outros módulos.

### Símbolos com tamanho fixo em pixels

`BarraItem` e `LoadItem` usam `ItemIgnoresTransformations`: o símbolo mantém o
tamanho em pixels independentemente do zoom. `LoadItem` desenha um conector do
ponto da barra até um retângulo 12×8 px.

Cargas na mesma barra são distribuídas por `load_layout_offsets_for_models()`:
agrupa por `bar_index`, ordena por `load_id.casefold()` (determinístico),
calcula `colunas = ceil(sqrt(n))` e posiciona em grade. A função aceita
**vários modelos simultaneamente** — é assim que cargas originais e cargas
equivalentes compartilham a mesma grade sem se sobrepor no modo simplificado.

### Overlays de seleção

Um item selecionado pode não estar materializado (modo “Visão geral”, ou fora do
retângulo carregado). Por isso cada camada tem um overlay dedicado que desenha o
destaque de forma independente:

- `SelectionOverlayItem` (Z 100) — barra selecionada;
- `LoadSelectionOverlayItem` (Z 110) — carga selecionada (herda de `LoadItem`);
- `SegmentSelectionOverlayItem` (Z 90) — trecho selecionado (`QGraphicsLineItem`);
- `BranchHighlightOverlayItem` (Z 95) — ramal inteiro em um único `QPainterPath`.

`_sync_selection()` decide entre destacar o item materializado ou exibir o
overlay. O parâmetro `reveal_hidden` permite manter o destaque de um elemento
oculto pelos filtros — usado pela busca global, para que o usuário veja onde
está o resultado mesmo com o circuito desmarcado.

### Reciclagem de itens

Ambos os virtualizadores mantêm um pool (`MAX_POOL_SIZE = 1.000`):
`_acquire_item()` reaproveita do pool ou cria; `_release_item()` remove da cena,
faz `unbind()` e devolve ao pool (ou `deleteLater()` se o pool estiver cheio).
Operações em lote são envolvidas por `QSignalBlocker(scene)` para evitar
tempestade de sinais `changed`.

### Reuso do retângulo carregado

`_can_reuse_loaded_rect()` evita recomputar quando o viewport ainda está dentro
da área carregada:

```python
inside and not zoomed_in_far and not zoomed_out
```

`zoomed_in_far` (viewport < 50% da largura anterior) força recarga porque um
zoom forte pode trazer itens antes filtrados por densidade. `zoomed_out` força
recarga porque a área cresceu.

---

## 9. Seleção, interação e navegação

### Modos de interação

| Modo | Ativação | Comportamento |
|---|---|---|
| `select` | `S`, botão **Selecionar** | clique seleciona o elemento mais próximo |
| `pan` | `M`, botão **Mover** | arrasto move a cena |
| pan temporário | botão do meio, `Espaço` + arrasto | pan sem trocar de ferramenta |

O clique só é tratado como seleção se o mouse moveu ≤ 4 px entre press e
release — arrastar não seleciona por acidente.

### Algoritmo de hit-test (`_select_nearest`)

A tolerância é convertida de pixels para unidades de modelo:
`tolerance = CLICK_TOLERANCE_PX / escala`. A ordem de prioridade é:

1. **Cargas materializadas** (equivalentes antes das originais) —
   `hit_test(overview=False)` testa `symbol_rect.contains()` em coordenadas de
   viewport, desempatando pelo centro mais próximo.
2. **Cargas agregadas** — `hit_test(overview=True)` usa o índice espacial com
   tolerância derivada do diâmetro do marcador; guarda o candidato.
3. **Barras** — `spatial_index.nearest(x, y, tolerance, bar_mask)`. Se houver
   candidato de carga agregada, a barra só vence quando o clique cai **dentro do
   raio do ponto da barra** (evita que a barra “roube” cliques do marcador da
   carga desenhado no mesmo lugar).
4. **Carga agregada** (se nenhuma barra qualificou).
5. **Trechos** — `spatial_index.nearest` com distância ponto-segmento exata.

O resultado é emitido como `FeatureSelection` pelo sinal `selectionRequested`;
`MainWindow._set_selection()` decide a página do painel, preenche os rótulos e
sincroniza os overlays.

Todo hit-test respeita as máscaras de visibilidade (`eligible_mask`), então um
elemento filtrado nunca é selecionado por clique.

### Zoom

```python
factor = 1.15 ** (angleDelta / 120)
target = clamp(escala_atual * factor, MIN_ZOOM_SCALE, maximum_zoom_scale)
```

`maximum_zoom_scale` é o **menor** entre:

- `MAX_USEFUL_ZOOM_SCALE = 100` px/m (limite de utilidade);
- `2³¹−1 × 0.5 / maior_coordenada_da_cena` (limite numérico das scrollbars do
  Qt, que são `int32`).

Sem esse segundo limite, coordenadas UTM grandes (ex.: 8.000.000 no norte)
estouram as scrollbars e a cena “salta”. Ao atingir o teto, `zoomLimitReached`
é emitido uma única vez (flag `_zoom_limit_notified`) e a status bar avisa.

O zoom é **ancorado no cursor**: mapeia o ponto para cena antes e depois da
escala e translada pela diferença. `ViewportAnchor.NoAnchor` é usado porque o
ancoramento nativo do Qt interage mal com o clamp.

### Enquadramentos

| Método | Uso |
|---|---|
| `fit_model()` | **Enquadrar tudo** / `F` — bounds do modelo com 5% de margem |
| `fit_visible_features(bar_mask, segment_mask)` | Enquadrar tudo no modo simplificado (usa só a projeção visível) |
| `focus_bar(i)` | Busca por barra — janela fixa de 500 m |
| `focus_load(i)` | Busca por carga — 500 m sobre a barra associada |
| `focus_segment(i)` | Busca por trecho — bbox + 20% de margem |
| `focus_segments(indices)` | Ramal inteiro — bbox do conjunto + 20% |

`_fit_focus_rect()` aplica um teto de escala 4.0 para que enquadrar um trecho
curtíssimo não leve o zoom ao máximo absoluto.

### Filtros de visibilidade

`MainWindow._apply_circuit_visibility()` é o **ponto único** onde todas as
máscaras são calculadas e propagadas. Vale a pena entender esse método antes de
mexer em qualquer coisa relacionada a visibilidade:

```
controller (CircuitVisibilityController)
   ├─ bar_mask, segment_mask, segment_style_indices
   └─ load_mask = bar_mask[load_model.bar_indices]

se modo simplificado ativo:
   substitui as quatro máscaras pelas de EquivalentNetworkModel.visibility_masks()

propaga para:
   view.set_feature_visibility_masks()   (hit-test)
   virtualizer / load_virtualizer / equivalent_load_virtualizer
   line_item / switch_item (modo circuito OU modo fases)
   phase_legend
   revalida a seleção atual (limpa se ficou oculta e não é destaque de busca)
   search_palette.refresh_results()
```

As máscaras efetivas ficam em `_effective_bar_mask`, `_effective_segment_mask`
e `_effective_load_mask`, consultadas por `_is_search_result_hidden()`.

Atualizações vindas da tabela de circuitos passam por um timer de 50 ms
(`_circuit_visibility_timer`), que coalesce cliques rápidos em vários
checkboxes.

---

## 10. Estruturas de dados e finalidades

| Estrutura | Onde | Finalidade | Complexidade |
|---|---|---|---|
| `StaticPointIndex` | barras, cargas, cargas equivalentes | consulta por retângulo e vizinho mais próximo | construção O(n log n); `query_rect` O(log n + k) |
| `StaticSegmentIndex` | trechos | idem, sobre caixas envolventes + distância exata ponto-segmento | idem |
| Adjacência CSR | `NetworkTopology` | percorrer vizinhos sem alocar | O(1) por vizinho |
| Marcação por geração | `NetworkTopology`, `branch_analysis` | reusar vetores de visita sem zerar | O(1) para “limpar” |
| Índice reverso CSR | `CircuitCatalogModel` | trecho → circuitos donos | O(1) + k |
| Contadores de referência | `CircuitVisibilityController` | toggle de circuito sem recomputar máscara global | O(\|membership\|) |
| `dict[str, int]` | todos os modelos | ID textual → índice | O(1) |
| Colunas paralelas | todos os modelos | localidade de cache, sem overhead por objeto | — |
| Trie implícita (lista ordenada + `bisect`) | `_SearchPartition` | busca por prefixo | O(log n + k) |
| `dict[categoria → QPainterPath]` | itens de rede | 1 draw call por cor | — |
| `OrderedDict` como LRU | `GerenciadorTiles._mem` | cache de tiles por bytes | O(1) |
| Pool de itens | virtualizadores | evitar alocação de `QGraphicsObject` | O(1) |

### Detalhes dos índices espaciais

Ambos usam a mesma técnica: ordenação única por X e `searchsorted` para delimitar
candidatos, seguida de filtragem vetorizada em Y.

`StaticPointIndex.nearest()` filtra por caixa, aplica a máscara de elegibilidade,
calcula distâncias ao quadrado e desempata com
`np.lexsort((candidatos, distâncias))` — o índice original desempata distâncias
iguais, tornando a seleção **determinística**.

`StaticSegmentIndex.nearest()` calcula a distância exata ao segmento de forma
vetorizada: projeta o ponto no segmento (`np.divide` com `where=` para tratar
segmentos degenerados como pontos), faz `clip(0,1)` e mede a distância ao ponto
projetado.

Nota de projeto: como `query_rect` de segmentos filtra apenas por `min_x`, um
conjunto com segmentos muito longos gera mais candidatos que o ideal. Na prática
os trechos de distribuição são curtos, então a simplicidade venceu uma árvore
R-tree — que exigiria dependência extra ou muito mais código.

---

## 11. Concorrência e ciclo de vida das threads

### Padrão QThread + worker

Todos os workers em `workers.py` seguem exatamente a mesma forma:

```python
class XWorker(QObject):
    progress  = pyqtSignal(...)
    finished  = pyqtSignal(object)
    failed    = pyqtSignal(str)
    cancelled = pyqtSignal()

    def cancel(self):              # chamável da thread da UI
        self._cancel_event.set()   # threading.Event é thread-safe

    @pyqtSlot()
    def run(self):
        try:    result = funcao_pura(..., cancel_event=self._cancel_event)
        except CsvImportCancelled: self.cancelled.emit()
        except Exception as exc:   self.failed.emit(str(exc))
        else:                      self.finished.emit(result)
```

O worker é `moveToThread(thread)`; `thread.started` → `worker.run`; os três
sinais terminais → `thread.quit`; `thread.finished` → `worker.deleteLater`,
handler de limpeza e `thread.deleteLater`.

`MainWindow` mantém três slots exclusivos de execução (`_import_thread`,
`_branch_thread`, `_equivalent_thread`) e cada entrada de menu verifica os três
antes de iniciar — nunca há duas operações pesadas simultâneas.

### Cancelamento cooperativo

Dois protocolos coexistem:

- importadores: `threading.Event` verificado a cada linha;
- análises: `cancel_check: Callable[[], bool]` verificado a cada 4.096 iterações
  (`inspect()`), levantando `InterruptedError`.

`QProgressDialog.canceled` → `worker.cancel()`.

### Consistência do resultado (snapshots)

Como o usuário pode reimportar durante uma análise, cada operação longa grava um
snapshot das suas entradas e o revalida na chegada:

```python
self._branch_analysis_snapshot = (catalog, phase_configuration, loads)
...
if any(esperado is not atual for esperado, atual in zip(snapshot, atual_agora)):
    # descarta silenciosamente com aviso na status bar
```

A comparação é por identidade (`is`), coerente com a regra de identidade do
modelo.

### Fechamento

`closeEvent` verifica as três threads em ordem; se alguma estiver rodando,
cancela o worker, marca `_close_after_*` e chama `event.ignore()`. O handler de
`thread.finished` relança o `close()`. Isso garante que nenhuma thread seja
abandonada. No fim, `search_palette.shutdown()` (cancela tarefas do pool,
`waitForDone(2s)`) e `view.shutdown_satellite()` (aborta downloads, libera
cache).

### QThreadPool da busca

A busca global usa um caminho separado, mais leve: `QThreadPool` com
`maxThreadCount = 2`, `QRunnable` para indexação e consulta, e
`threading.Event` como token de cancelamento. Resultados obsoletos são
descartados por comparação de `serial`, de `token` e de `revision` do índice.

---

## 12. Subsistemas analíticos

### 12.1 Análise de ramais (`branch_analysis.py`)

**Objetivo:** identificar ramais monofásicos e bifásicos ligados ao tronco
trifásico de cada circuito, na topologia **energizada** (chaves abertas
interrompem; só chaves fechadas do próprio circuito são atravessadas).

**Algoritmo por circuito** (`generation = circuit_index + 1`):

1. **Marcação de permissão** — `allowed_marks` recebe os trechos comuns do
   membership; chaves do circuito com `ESTADO=1` também entram.
2. **Descoberta do tronco** — BFS a partir da barra raiz atravessando **apenas
   trechos trifásicos** (`phase_counts == 3`); registra `trunk_bars`,
   `trunk_depths` e conta os trechos do tronco. Sem tronco → diagnóstico
   `missing-three-phase-trunk` e o circuito é pulado.
3. **Candidatos de fronteira** — para cada barra do tronco, examina incidências
   procurando trechos com 1 ou 2 fases. Trechos que ligam duas barras do tronco
   viram diagnóstico `*-trunk-chord` (não são ramais). A ordenação dos candidatos
   por `(profundidade_no_tronco, segment_id)` torna o resultado determinístico.
4. **Núcleos bifásicos** — BFS que agrega trechos bifásicos com o **mesmo
   `FASES2`**; mudança de código bifásico interrompe e gera
   `two-phase-transition`. Registra todas as conexões com o tronco.
5. **Componentes monofásicas** — para cada componente conexa monofásica,
   classifica em `single_status`:
   - `3` (excluída) — ligada a **mais de um** núcleo bifásico
     (`ambiguous-single-phase-subtree`) **ou** ligada simultaneamente ao tronco
     e a um núcleo (`single-phase-trunk-bridge`);
   - `2` (anexada) — ligada a exatamente um núcleo bifásico → incorporada a ele;
   - `1` (ramal próprio) — toca o tronco e nenhum núcleo.

   A exclusão evita contar o mesmo trecho/carga/potência em dois ramais.
6. **Emissão** — primeiro os núcleos bifásicos (já com subárvores incorporadas),
   depois os ramais monofásicos de status 1.

**Métricas por ramal** (`append_record`): BFS de distância a partir das conexões
com o tronco para obter `POS_PRIMEIRA_CHAVE`; `REMANEJAVEL = posição ≤ 5`;
comprimento total (`None` se algum `COMPR` faltar); cargas coletadas via CSR
`bar → cargas`; topologia classificada em `Linear` / `Bifurcado` / `Cíclico`
(+ `Múltiplas conexões`).

**Saída:** `BranchAnalysisResult` com `records` ordenados por
`(circuit_id, first_segment_id)` e `branch_id` reatribuído como sequência global
1..N, `issues` (deduplicados, teto de 500) e as fontes usadas.

### 12.2 Rede simplificada (`equivalent_network.py`)

**Objetivo:** substituir cada ramal por uma carga equivalente na sua conexão com
o tronco, **sem modificar nenhum dado importado**. É um snapshot derivado.

Para cada ramal gera um `EquivalentLoadRecord`:

- `load_id = f"RAMAL-{branch_id}"` (invariante verificada no `__post_init__`);
- `origin_kind = "branch_aggregate"`; `bar_index` = conexão com o tronco;
- `snom`/`sadm` somados com `Decimal` em contexto de precisão 50 — qualquer
  parcela inválida torna **aquele total** `None` e gera diagnóstico, sem abortar;
- patamares: soma `PD, PE, PF, QD, QE, QF` por `NPAT`; a tabela equivalente só
  existe se **todas** as cargas do ramal tiverem os 4 patamares completos e
  numéricos.

`_parse_decimal` aceita ponto ou vírgula decimal (não ambos) e notação
científica; `Decimal` foi escolhido em vez de `float` porque os totais são
exibidos ao usuário e somas de float acumulariam ruído visível.

`EquivalentNetworkModel` pré-computa, por circuito:

```
_retained_segments  = membership.segment_indices − trechos absorvidos por ramais
_retained_bars      = barras dos trechos retidos ∪ conexões ∪ barra raiz
_reduced_loads      = cargas absorvidas pelos ramais
_equivalents_by_circuit = índices das cargas equivalentes
```

`visibility_masks(checked)` combina essas listas com contadores por circuito e
devolve as quatro máscaras da projeção. Como usa contagem, um elemento continua
visível enquanto **algum** circuito visível precisar dele — o comportamento
correto para circuitos sobrepostos.

O modelo expõe `bars`, `bar_indices`, `load_ids`, `spatial_index` e `record()`,
ou seja, é **duck-type compatível com `LoadModel`** — por isso o mesmo
`LoadVirtualizer` renderiza as duas camadas sem código condicional.

---

## 13. Camada de satélite

### Matemática (pura, testável headless)

| Função | Papel |
|---|---|
| `lonlat_para_tile(lon, lat, z)` | ponto → índice de tile XYZ (lat clampada em ±85.0511) |
| `tile_bbox(xt, yt, z)` | tile → `(lon_min, lat_min, lon_max, lat_max)` |
| `nivel_zoom(px_por_metro, lat, z_max)` | escolhe z tal que o tile fique ~256 px |
| `cantos_lonlat_da_faixa(...)` | grade `(nx+1)×(ny+1)` de cantos compartilhados |
| `tile_pai(xt, yt, z, níveis)` | ancestral (shift) para fallback |
| `sub_rect_no_pai(...)` | fração do ancestral correspondente ao tile |
| `ordenar_chebyshev(chaves, centro)` | ordena carregamento em anéis quadrados |

`cantos_lonlat_da_faixa` retorna cantos **compartilhados** entre tiles vizinhos:
o canto direito de um tile é literalmente o mesmo elemento da grade que o canto
esquerdo do vizinho, o que elimina frestas de arredondamento no mosaico.

### Desenho (`DiagramView._draw_satellite`)

1. converte os 4 cantos do retângulo exposto para lon/lat via `pyproj`
   (`EPSG:<zona>` ↔ `EPSG:4326`, transformers cacheados por EPSG);
2. escolhe o nível pelo zoom efetivo (`m11 × devicePixelRatio`);
3. calcula a faixa de tiles; aborta se `nx*ny > 400` (proteção);
4. declara o conjunto de interesse ao gerenciador e agenda prefetch (250 ms);
5. projeta a grade de cantos de volta para a cena em **uma chamada em lote**;
6. para cada tile, monta um `QPolygonF` de 4 pontos e desenha via
   `QTransform.quadToQuad` — isso acomoda a distorção UTM↔Mercator;
7. tile ausente → fallback: 1–2 níveis acima recortando a região correspondente,
   ou os 4 filhos já em cache;
8. todo o método está dentro de `try/except` — indisponibilidade do fundo nunca
   pode derrubar a rede.

### Gerenciador de tiles

Disciplina inspirada no `QgsTileDownloadManager` do QGIS:

- fila pendente com teto de 6 downloads simultâneos (limite do Qt por host);
- drenagem ordenada por prioridade (visível antes de prefetch) e distância de
  Chebyshev ao tile central — os tiles chegam em anéis do centro para fora;
- pedidos que saíram do conjunto de interesse são descartados na drenagem
  (pan rápido não desperdiça banda);
- HTTP ≥ 400 e placeholder (detectado por md5) são memoizados como
  permanentemente indisponíveis; falha de rede tem retry até 3 vezes e então
  memoiza pela sessão;
- cache de memória LRU **por bytes** (96 MB) + cache de disco em
  `<CacheLocation>/mapa_tiles/<provedor>/z/x/y.png`.

Provedores: Esri World Imagery (padrão, sem chave, `zoom_max=17`) e dois
endpoints Google não oficiais (`zoom_max=20`), que exigem confirmação explícita
do usuário uma vez por sessão (`_authorize_google_satellite`).

### Backend TLS — requisito obrigatório

`garantir_backend_tls()` é chamado no início de `GerenciadorTiles.__init__`,
antes de qualquer requisição, e troca o backend TLS do Qt de `openssl` para
`schannel` quando este estiver disponível (Windows).

**Sem essa troca a aplicação sofre violação de acesso e morre** no primeiro
handshake HTTPS. Há duas cópias de OpenSSL no processo: `pyproj` (PROJ/curl,
importado por `graphics.py`) e o `hashlib` do CPython (importado por
`mapa_tiles.py`) carregam a própria `libcrypto`, enquanto o Qt é compilado
contra outra versão. O `schannel` é nativo do Windows e não depende de OpenSSL.

A função é defensiva e idempotente: só age quando o backend ativo é `openssl`
**e** `schannel` está em `availableBackends()`; em Linux/macOS nada muda.
`setActiveBackend()` devolve `False` se o TLS já foi inicializado — nesse caso o
backend atual é mantido em vez de interromper a aplicação.

> Cuidado ao alterar a ordem de import ou o ponto da chamada: a troca precisa
> preceder o primeiro handshake. Note também que `QT_TLS_BACKEND=schannel` **não**
> teve efeito nos testes; apenas a chamada de API funciona.

### Pré-requisito: coordenadas em metros UTM

Os tiles só se posicionam se `_scene_to_lonlat` produzir coordenadas geográficas
válidas, e isso exige que o modelo esteja em **metros** dentro do envelope UTM.
Com coordenadas em decímetros (northing ≈ 82.000.000) o `pyproj` satura, devolve
um ponto no oceano e perde a invertibilidade: os tiles são baixados para o lugar
errado e desenhados a milhões de unidades da rede — fundo aparentemente vazio,
sem nenhum erro. Ver a normalização de unidade na seção 6.

### Diagnóstico de falhas

A camada degrada em silêncio por design (indisponibilidade do fundo nunca pode
derrubar a rede), o que historicamente tornou defeitos invisíveis. Dois sinais
compensam isso:

- `GerenciadorTiles.falha_tiles` — emitido **uma vez por gerenciador** quando um
  tile é marcado como permanentemente indisponível (HTTP ≥ 400 ou retries de rede
  esgotados);
- `DiagramView.satelliteUnavailable` — emitido **uma vez por modelo** quando a
  projeção satura (`_scene_to_lonlat` devolve `None`) ou quando `_draw_satellite`
  captura exceção.

Ambos chegam à barra de status por `MainWindow._show_satellite_failure`.

O placeholder do provedor ("Map data not yet available") **não** dispara o
aviso: é ausência de cobertura, resolvida pelo overzoom, e não falha de acesso.

---

## 14. Busca global

### Modo rápido (por `CODIGO`)

`_SearchPartition` por tipo de entidade: `dict[código_normalizado →
tuple[SearchResult]]` mais uma tupla de chaves ordenadas. Busca exata é lookup
O(1); busca por prefixo é `bisect_left` + varredura. `normalize_code()` aplica
`casefold` + NFKD + remoção de diacríticos, então “SÃO” encontra “sao”.

Resultados exatos vêm primeiro, depois os por prefixo, ordenados por
`(código, tipo, entity_id, índice)`.

### Modo amplo (todas as colunas)

`FieldSearchPartition` guarda, por entidade, as colunas, os valores originais,
os valores normalizados e uma string `combined` com separador `\x1f`. A consulta
faz um teste rápido em `combined` antes de examinar campo a campo, classifica a
qualidade (`exact` > `prefix` > `contains`), prioriza a coluna `CODIGO` e usa
`heapq.nsmallest(200)` para selecionar sem ordenar tudo.

Exige ≥ 3 caracteres, roda em `QThreadPool` com debounce de 150 ms e é
cancelável a cada 2.048 documentos. Não indexa patamares, ramais, cargas
equivalentes nem colunas extras ignoradas na importação.

### Controle de obsolescência

`GlobalSearchIndex.revision` incrementa a cada troca de partição. Um resultado
só é aplicado se `result.revision == index.revision`, o serial for o corrente e
o texto do campo não tiver mudado. `install_field_partition()` só publica se a
fonte ainda for a atual (comparação por identidade).

A `MainWindow` sempre chama `set_*(model, build_fields=False)` e delega a
construção do índice amplo para `search_palette.schedule_field_index()`, que a
executa fora da thread da UI.

---

## 15. Dependências

### Externas (runtime)

| Pacote | Versão | Uso |
|---|---|---|
| `PyQt6` | ≥6.7, <7 | UI, gráficos, rede (QtNetwork), threads |
| `numpy` | ≥2.0, <3 | colunas, índices, máscaras, operações vetorizadas |
| `pyproj` | ≥3.5, <4 | transformação UTM ↔ WGS84 para os tiles |

`pyproj` é importado defensivamente em `graphics.py` (`Transformer = None` se
ausente): sem ele, apenas a camada de satélite deixa de funcionar.

### Desenvolvimento

`pytest` ≥8, `pytest-qt` ≥4.4 (extra `test`).

### Grafo interno (sem ciclos)

```
__main__ → main_window → {graphics, workers, *_window, *_table, search_palette,
                          model, phase_config, mapa_tiles, *_import}
workers  → {*_import, branch_analysis, equivalent_network, model, phase_config}
graphics → {model, equivalent_network, mapa_tiles}
equivalent_network → {branch_analysis, model}
branch_analysis    → {model, phase_config}
search   → model
phase_config → model (apenas o alias de tipo IndexArray)
model    → circuit_colors
*_import → {csv_import (exceções), model}
```

`model.py` e `circuit_colors.py` são folhas — não importam nada do pacote além
disso. `__init__.py` re-exporta a API pública (~70 nomes em `__all__`), o que
permite `from circuit_viewer import CircuitModel, analyze_branches, ...` nos
testes e benchmarks.

---

## 16. Decisões de projeto e justificativas

**Núcleo sem Qt.** Permite testar modelo/importadores/análises sem display e
executá-los em threads secundárias sem restrições de afinidade. É a decisão da
qual todas as outras dependem.

**Armazenamento colunar em vez de objetos por registro.** 100 mil objetos Python
custariam centenas de MB e destruiriam a localidade de cache. Colunas NumPy +
`record(i)` sob demanda dão o melhor dos dois mundos: eficiência no núcleo,
ergonomia na UI.

**Imutabilidade com `setflags(write=False)`.** Arrays imutáveis podem ser
compartilhados entre threads sem lock e evitam corrupção acidental por código de
UI.

**Identidade de objeto como chave de consistência.** Mais barato e mais seguro
que versionamento por hash ou timestamp; expressa exatamente a pergunta que
importa (“este resultado foi calculado sobre os dados que ainda estão na tela?”).

**Importação transacional.** O usuário nunca fica com um estado meio-importado:
ou o novo modelo existe inteiro, ou o anterior permanece intacto.

**Renderização híbrida com teto de 1.000 itens.** Abaixo desse limite, itens
individuais dão interação rica (tooltip, seleção, símbolo em pixels fixos).
Acima, o custo por item domina e o agregado é ordens de grandeza mais rápido.

**Agrupamento por categoria de cor.** Reduz N draw calls a K (número de cores
visíveis) e separa a recompilação de geometria da troca de cor.

**Marcação por geração.** Zerar vetores de 100 mil posições entre buscas
custaria mais do que as próprias buscas em circuitos pequenos.

**Referência contada na visibilidade.** Alternar um circuito toca apenas o seu
membership; sem contadores, seria necessário recomputar a máscara global
percorrendo todos os circuitos.

**`Decimal` na agregação de potência.** Os totais são exibidos ao usuário;
somas de float acumulariam erro visível em ramais com muitas cargas.

**Modo simplificado como projeção, não mutação.** Desligar o modo restaura a
rede original instantaneamente, sem reimportar nada — e nenhum dado importado é
perdido.

**Duck typing entre `LoadModel` e `EquivalentNetworkModel`.** Evita duplicar
todo o `LoadVirtualizer` para uma segunda camada de cargas.

**Configuração de fases externa (`fases2.json`).** O mapeamento `FASES2` →
número de fases é convenção da concessionária, não regra de negócio da
aplicação. Erro no arquivo desabilita **apenas** os modos que dependem dele
(coloração por fases, ramais, rede simplificada) — o resto continua funcionando.

**Paleta OKLCH.** Espaço perceptualmente uniforme com amostragem pelo ângulo
áureo e contraste mínimo 3:1 com branco: circuitos adjacentes ficam
distinguíveis mesmo em quantidade alta.

**Chaves como trechos, não como entidades geométricas próprias.** Chaves
existem fisicamente sobre um trecho; modelá-las como decoração do trecho
(`_record_by_segment`) evita uma quarta camada geométrica e dá lookup O(1) no
BFS.

**Limite de zoom pelas scrollbars do Qt.** Coordenadas UTM grandes estouram
`int32` nas scrollbars; o clamp evita saltos e artefatos visuais sem alterar a
cena.

**Backend TLS `schannel` forçado no Windows.** Não é preferência, é requisito:
com `pyproj` e `hashlib` carregando suas próprias cópias de OpenSSL, o backend
OpenSSL do Qt provoca violação de acesso no handshake e derruba o processo. A
troca é feita em `GerenciadorTiles.__init__` — único ponto por onde passam todas
as requisições, tardio o bastante para existir `QApplication` e cedo o bastante
para preceder o primeiro handshake. Chamar em `__main__.main()` foi descartado
por não cobrir testes nem uso programático de `MainWindow`.

**Camada de satélite só desenha com barras importadas.** Os tiles precisam da
zona/hemisfério UTM para serem georreferenciados. A opção continua podendo ser
ligada antes da importação (comportamento documentado), mas a barra de status
explica a pendência em vez de deixar um no-op mudo.

**Normalização da unidade na importação, não na renderização.** Converter X/Y
para metros no `load_csv` dá ao modelo uma unidade canônica única, igual à de
`COMPR`. A alternativa — guardar o valor bruto e aplicar um fator só na camada de
satélite — deixaria o resto do sistema inconsistente: `focus_bar`/`focus_load`
(500 m de contexto), o padding mínimo de `focus_segment` (50 m) e
`MAX_USEFUL_ZOOM_SCALE` (100 px/m) são todos expressos em metros e estavam 10×
errados enquanto o modelo guardava decímetros.

**Unidade deduzida, mas confirmada pelo usuário.** A dedução acerta o caso comum
sem exigir conhecimento do arquivo; a confirmação no diálogo cobre bases atípicas
e evita que uma heurística errada corrompa silenciosamente toda a importação.

---

## 17. Pontos de extensão

### Adicionar uma nova entidade importável

1. Criar `nova_entidade_import.py` seguindo o contrato dos importadores
   (`EXPECTED_*_HEADER`, `_column_positions`, `_parse_file`, `load_*_csv`,
   `*Issue`, `*LoadResult` com `has_warnings`).
2. Criar o `*Model` colunar em `model.py`, referenciando o modelo-pai por
   índices e expondo `__len__`, `index_for_id`, `record(i)` e — se for
   selecionável — `spatial_index`.
3. Adicionar o worker em `workers.py` (copiar o padrão; ~30 linhas).
4. Em `main_window.py`: botão no `ImportChoiceDialog`, `_choose_*_csv`,
   `_start_*_import`, `_on_*_import_finished`, `_set_*_model` (com a cascata de
   invalidação correta) e `_show_*_import_report`.
5. Se for renderizável, criar o item agregado em `graphics.py`.
6. Se for pesquisável, adicionar o `SearchKind`, o branch em `_source_rows()` e
   o `set_*` em `GlobalSearchIndex`.
7. Exportar em `__init__.py`; adicionar testes e (se for escala grande) benchmark.

### Adicionar um modo de coloração

`LineNetworkItem` e `SwitchNetworkItem` já aceitam qualquer
`(máscara, style_indices, cores)`. Basta produzir um vetor `intp` de categorias
(`-1` padrão, `-2` oculto, `≥0` índice na paleta) e chamar
`set_*_rendering()`. `_apply_circuit_visibility()` é o lugar de escolher entre
os modos — hoje há dois (`circuito` e `fases`) selecionados por
`phase_coloring_action`.

### Adicionar uma análise topológica

Escrever uma função pura com assinatura
`(catalog, ..., *, cancel_check, progress) -> Result`, usando `NetworkTopology`
e marcação por geração; envolver em um worker; adicionar snapshot de validação
em `MainWindow` e uma janela de resultado no padrão de `branch_window.py`.

### Adicionar um provedor de satélite

Instanciar um `Provedor` (template com `{z}/{x}/{y}`, atribuição, `zoom_max`, e
opcionalmente `hash_indisponivel`) e incluir em `PROVEDORES`. O menu, o
gerenciador e o cache por provedor se ajustam automaticamente. Provedores que
exigem consentimento devem passar por `_authorize_*` como os do Google.

### Persistir sessão

Hoje nada é persistido (cores, filtros e provedor valem só para a execução
atual). Um ponto natural seria serializar `CircuitVisibilityController.colors` e
`checked_states` indexados por `circuit_id`, reaproveitando o mesmo mecanismo de
remapeamento já usado em `_set_switch_model`.

### Exportar dados

`BranchTableModel._raw_values()` e `OverlapReportTableModel` já expõem os dados
em forma tabular — um exportador CSV/XLSX é um consumidor direto desses modelos,
sem tocar no núcleo.

---

## 18. Testes e benchmarks

### Testes (`tests/`, 21 arquivos)

| Arquivo | Foco |
|---|---|
| `test_model.py` | entidades, índices espaciais, topologia |
| `test_csv_import.py` · `test_segment_import.py` · `test_switch_import.py` · `test_load_import.py` · `test_load_pattern_import.py` · `test_circuit_import.py` | importadores e casos de erro |
| `test_phase_config.py` | validação do `fases2.json` |
| `test_circuit_colors.py` | paleta e contraste |
| `test_branch_analysis.py` · `test_equivalent_network.py` | análises topológicas |
| `test_search.py` | índice de busca (sem Qt) |
| `test_graphics.py` · `test_main_window.py` · `test_branches_ui.py` · `test_circuits_ui.py` · `test_phase_ui.py` · `test_search_ui.py` · `test_map_tiles.py` · `test_satellite_ui.py` | camadas Qt (exigem PyQt6) |

Os testes do núcleo usam apenas a biblioteca padrão e NumPy; os gráficos rodam
quando PyQt6 está disponível.

```bash
python -m unittest discover -s tests -v
```

### Benchmarks (`benchmarks/`, 8 arquivos)

Cada benchmark gera dados sintéticos em escala, mede tempos e aceita
`--enforce` para falhar quando os limiares são ultrapassados — útil em CI e
como guarda de regressão de performance.

```bash
python benchmarks\benchmark_100k.py --enforce
```

Cobertura: importação/indexação de 100 mil barras, desenho agregado em
1920×1080, p95 da seleção geométrica de trechos, paleta e categorização de
circuitos, busca global, 100 mil cargas, 400 mil patamares e a cadeia completa
de ramais (análise → agregação → máscaras → destaque vetorial).

---

## Apêndice A — Constantes de referência (`graphics.py`)

| Constante | Valor | Significado |
|---|---|---|
| `POINT_DIAMETER_PX` | 5.0 | diâmetro da barra normal |
| `SELECTED_DIAMETER_PX` | 9.0 | diâmetro da barra selecionada |
| `CLICK_TOLERANCE_PX` | 10.0 | raio de hit-test |
| `VIRTUALIZATION_MARGIN` | 0.25 | margem do retângulo carregado |
| `VIRTUALIZATION_DEBOUNCE_MS` | 120 | debounce do refresh |
| `MAX_ACTIVE_ITEMS` | 1.000 | teto de itens materializados por camada |
| `MATERIALIZE_BATCH_SIZE` | 250 | itens por lote |
| `MAX_POOL_SIZE` | 1.000 | tamanho do pool de reciclagem |
| `MAX_USEFUL_ZOOM_SCALE` | 100.0 | px por metro |
| `NORMAL_SEGMENT_WIDTH_PX` | 3.0 | espessura do trecho comum |
| `SWITCH_SEGMENT_WIDTH_PX` | 1.0 | espessura do trecho-chave |
| `LOAD_WIDTH/HEIGHT_PX` | 12.0 / 8.0 | símbolo da carga |

Outros tetos: `MAX_REPORTED_ISSUES = 200` (importadores),
`MAX_BRANCH_ISSUES = 500`, `MAX_EQUIVALENT_ISSUES = 500`,
`GerenciadorTiles`: 96 MB de cache, 6 downloads simultâneos, 3 retries.

## Apêndice B — Convenções de código

- Docstrings, mensagens de erro e identificadores de domínio em **português**;
  identificadores técnicos em inglês (exceção: `mapa_tiles.py`, integralmente em
  português, incluindo a API pública `GerenciadorTiles`, `tile()`, `prefetch()`).
- Colunas de CSV são referenciadas **sempre em maiúsculas** e como no arquivo
  (`BARRA_ID`, `FASES2`, `COMPR`).
- `from __future__ import annotations` em todos os módulos exceto `mapa_tiles.py`.
- Dataclasses de domínio: `frozen=True, slots=True`, com `__post_init__`
  validando invariantes.
- Modelos usam `__slots__`.
- Overrides da API do Qt marcados com `# noqa: N802` / `# noqa: ANN001`.
- Sentinelas de estilo de trecho: `-1` = padrão, `-2` = oculto, `≥ 0` = índice
  na paleta.
