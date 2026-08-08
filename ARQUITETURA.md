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
12. [Subsistemas analíticos](#12-subsistemas-analíticos) (ramais · rede simplificada · exportação OpenDSS · fluxo de potência · leitura de bancos Access)
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
atributos, filtra por circuito, executa análises topológicas (ramais e rede
simplificada por cargas equivalentes) e resolve o fluxo de potência dos
circuitos visíveis sem sair da aplicação.

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
│   │   ├── fases2.json        # mapeamento FASES2 → NUMERO_FASES (dado externo)
│   │   └── mdb_tabelas.json   # mapeamento tabela/coluna → entidade (dado externo)
│   ├── dados/                 # DADO DO USUÁRIO (gitignored, criado em runtime)
│   │   ├── curvas.json        # curvas horárias cadastradas na interface
│   │   └── patamares.json     # agenda dos quatro patamares de cálculo
│   │
│   ├── model.py               # NÚCLEO: entidades, índices espaciais, topologia
│   ├── circuit_colors.py      # paleta OKLCH contrastante
│   ├── phase_config.py        # carga e validação de fases2.json
│   │
│   ├── csv_import.py          # importação de barras (+ exceções e o seam comum)
│   ├── segment_import.py      # importação de trechos
│   ├── switch_import.py       # importação de chaves
│   ├── regulator_import.py    # importação de reguladores de tensão
│   ├── load_import.py         # importação de cargas
│   ├── generator_import.py    # associação MT_GERADOR_CONS + MT_CONS + cargas
│   ├── load_pattern_import.py # importação de patamares (NPAT 0–3)
│   ├── circuit_import.py      # importação de circuitos + build da topologia
│   ├── cable_import.py        # importação do catálogo de cabos (sem dependência)
│   │
│   ├── mdb_engine.py          # único acesso ao pyodbc (opcional) + conversão
│   ├── mdb_mapping.py         # tabela/coluna → entidade, a partir do JSON
│   ├── mdb_import.py          # importação encadeada das dez entidades lógicas
│   ├── circuit_level_import.py # parser CSV/MDB dos patamares por circuito
│   ├── circuit_calculation_levels.py # fonte imutável e cópia de sessão
│   │
│   ├── curvas.py              # NÚCLEO: curvas de 24 pontos, validação, colagem
│   ├── curvas_store.py        # NÚCLEO: leitura/gravação atômica de curvas.json
│   ├── calculation_levels.py  # NÚCLEO: agenda imutável + rascunhos editáveis
│   ├── calculation_levels_store.py # leitura/gravação atômica de patamares.json
│   ├── generator_update.py    # cálculo derivado das demandas dos geradores
│   │
│   ├── opendss_export.py      # geração dos .dss de rede e de cargas
│   ├── opendss_settings.py    # parâmetros globais das cargas (Vminpu/Vmaxpu)
│   ├── opendss_engine.py      # único acesso ao py_dss_interface (opcional)
│   ├── opendss_powerflow.py   # execução do fluxo e associação dos resultados
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
│   ├── cables_window.py       # tabela do catálogo de cabos
│   ├── opendss_export_dialog.py  # seleção dos circuitos a exportar
│   ├── opendss_settings_dialog.py # Configurações → OpenDSS… + QSettings
│   ├── mdb_import_dialog.py   # tabelas detectadas, senha e metadados UTM
│   ├── mdb_import_report.py   # relatório consolidado das dez entidades lógicas
│   ├── overlap_report.py      # relatório de trechos sobrepostos
│   ├── search_palette.py      # janela de busca não modal
│   ├── curvas_window.py       # Configurações → Curvas… (lista, grade, gráfico)
│   ├── curvas_table.py        # grade editável das 24 horas + colar/copiar
│   ├── curva_chart.py         # gráfico da curva, desenhado com QPainter
│   ├── patamares_window.py     # Configurações → Patamares…
│   ├── patamares_table.py      # grade editável fixa de quatro linhas
│   ├── generator_update_dialog.py # curva + origem dos patamares por circuito
│   ├── generator_update_table.py  # resultados do gerador no painel lateral
│   ├── load_pattern_table.py  # tabela de patamares no painel lateral
│   ├── power_flow_table.py    # tabela de grandezas do fluxo no painel lateral
│   ├── phase_legend.py        # legenda flutuante do modo por fases
│   └── theme.py               # tema claro/escuro escolhido manualmente
│
├── tests/                     # 53 arquivos de teste (unittest + pytest-qt)
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
│ cables_window · search_palette · load_pattern_table             │
│ phase_legend · theme                                            │
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
│ opendss_export · opendss_settings · opendss_powerflow           │
│ opendss_engine                                                  │
│ mdb_engine · mdb_mapping · mdb_import                           │
│ search · phase_config · circuit_colors                          │
│ (matemática de tiles em mapa_tiles também é pura)               │
└─────────────────────────────────────────────────────────────────┘
```

Uma segunda regra, análoga, vale para as dependências opcionais: **só
`opendss_engine.py` importa `py_dss_interface`** e **só `mdb_engine.py` importa
`pyodbc`**, e nos dois casos o import é tardio. `opendss_powerflow.py` recebe o
motor por parâmetro e `mdb_import.py` recebe o banco por parâmetro, o que os
mantém testáveis headless com um motor e um banco falsos, e faz a aplicação
inteira funcionar sem as bibliotecas instaladas — apenas com o botão de fluxo de
potência e o item de importação por banco desabilitados.

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
| `curvas.py` | `Curve`/`CurveDraft`/`CurveCatalog`, validação de nome e das 24 horas, interpretação do bloco colado | I/O, Qt |
| `curvas_store.py` | Ler/gravar `dados/curvas.json` de forma atômica e tolerante a arquivo corrompido | Qt, validar regras de negócio |
| `calculation_levels.py` | Agenda imutável dos NPAT 0–3, rascunhos isolados e validação do ciclo contínuo de 24 horas | I/O, Qt, executar cálculos |
| `calculation_levels_store.py` | Ler/gravar `dados/patamares.json` atomicamente; arquivo inválido recua integralmente aos padrões | Qt |
| `generator_update.py` | Resolver circuito/fases, aplicar curva e agenda e produzir demandas e potências por fase imutáveis | Qt, I/O, mutar geradores/curvas/patamares |
| `circuit_colors.py` | Gerar paleta contrastante em OKLCH; normalizar `#RRGGBB` | Aplicar cores |
| `search.py` | Índice por `CODIGO` e índice por todas as colunas; consultas canceláveis | Widgets |

### Importadores (um por entidade)

Todos seguem o mesmo contrato: `load_*_csv(path, dependência, *, cancel_event,
progress) -> *LoadResult`. São **transacionais** — ou retornam um modelo válido
completo, ou levantam exceção; nunca mutam estado externo.

Cada um expõe também `parse_*_rows(header, rows, dependência, *, source_label,
encoding, first_line_number, cancel_event, progress)`, a **mesma validação sem o
arquivo**: é o seam que a importação por banco consome (seção 6).

| Módulo | Entidade | Depende de | Colunas obrigatórias |
|---|---|---|---|
| `csv_import.py` | Barra | — (só do `UtmCrs`) | `BARRA_ID, CODIGO, X, Y` |
| `segment_import.py` | Trecho | `CircuitModel` | `TRECHO_ID, CODIGO, FASES2, BARRA1_ID, BARRA2_ID, ARRANJO_ID, CABOF_ID, CABON_ID, COMPR` |
| `switch_import.py` | Chave | `LineNetworkModel` | `CHAVE_ID, TIPOCHV_ID, CIRC_ID, TRECHO_ID, CODIGO, ESTADO, ESTADO_NORMAL, CORN, ELO, ELO_TIPO` |
| `regulator_import.py` | Regulador de tensão | `LineNetworkModel` | `REGU_ID, TRECHO_ID, EXTERN_ID, CODIGO, LIGACAO, SNOM, FAIXA, NPASSOS, TAP, INOM, VNOM` |
| `load_import.py` | Carga | `CircuitModel` | `CARGA_ID, BARRA_ID, EXTERN_ID, CODIGO, SNOM, SADM, VLINHASEC, FASES2, TIPO_LIG` |
| `generator_import.py` | Gerador | `LoadModel` | `MT_GERADOR_CONS` associado a `MT_CONS` por `CODIGO`; barra resolvida por `CARGA_ID` |
| `load_pattern_import.py` | Patamar | `LoadModel` | `CARGA_ID, NPAT, PD, PE, PF, QD, QE, QF` |
| `circuit_import.py` | Circuito | `LineNetworkModel` + `SwitchModel?` | `CIRC_ID, BARRA_ID, CODIGO, VNOM` |
| `cable_import.py` | Cabo | — (catálogo raiz) | `CABO_ID, TIPO, CODIGO, IADM, GMR, R, X, QCAP, R0, X0, R1, X1, NOME, EXTERN_ID` |

`csv_import.py` também exporta as exceções compartilhadas `CsvImportError`
(fatal) e `CsvImportCancelled` (interrupção do usuário), reutilizadas por todos
os demais importadores, mais os utilitários do seam: `normalize_header`,
`byte_progress`, `scale_from_ranges` e os tipos `RowProgress`/`TextRow`.

### Leitura de banco Access

| Módulo | Responsabilidade | Não faz |
|---|---|---|
| `mdb_engine.py` | Único acesso ao `pyodbc`; conexão somente leitura, sniff do formato, detecção de senha, `cell_to_text` | Conhecer entidades do modelo |
| `mdb_mapping.py` | Ler `mdb_tabelas.json`; casar entidade → tabela → colunas reais | Ler linhas |
| `mdb_import.py` | Encadear as dez entidades lógicas na ordem de dependência | Validar linhas (delega às `parse_*_rows`) |
| `circuit_level_import.py` | Compartilhar leitura e validação de `CIRCUITO_PATAMARES` entre CSV e MDB | Manter estado editável da sessão |
| `circuit_calculation_levels.py` | Vincular agendas importadas ao `CircuitCatalogModel` e manter a cópia virtual | Persistir dados em disco |

### Análise

| Módulo | Entrada | Saída |
|---|---|---|
| `branch_analysis.py` | `CircuitCatalogModel`, `PhaseConfiguration`, `LoadModel?` | `BranchAnalysisResult` (ramais + diagnósticos) |
| `opendss_export.py` | `CircuitCatalogModel`, `CableModel`, `PhaseConfiguration`, índices dos circuitos, cargas/patamares opcionais e `GeneratorUpdateModel?` | `OpenDssExportBundle` (rede, três arquivos de carga, três de geradores, master, coordenadas e diagnósticos) |
| `opendss_powerflow.py` | motor OpenDSS injetado + as mesmas entradas da exportação + pasta de trabalho | `PowerFlowResult` (correntes por trecho e tensões por barra, um retrato por patamar + diagnósticos) |
| `equivalent_network.py` | `BranchAnalysisResult`, `LoadModel?`, `LoadPatternModel?` | `EquivalentNetworkResult` (cargas equivalentes + máscaras) |
| `generator_update.py` | `GeneratorModel`, `CircuitCatalogModel`, `PhaseConfiguration`, uma `Curve` e agendas efetivas | `GeneratorUpdateResult` (demanda média, quatro demandas totais e quatro potências por fase com sinal elétrico + diagnósticos) |

### Gráfico e UI

| Módulo | Responsabilidade |
|---|---|
| `graphics.py` | Toda a pintura, virtualização, hit-test geométrico, zoom/pan, desenho do fundo de satélite |
| `opendss_settings.py` | Valor imutável dos parâmetros globais das cargas (`Vminpu`/`Vmaxpu`), com a invariante que o OpenDSS não impõe e a tradução para os comandos `BatchEdit` |
| `opendss_engine.py` | Contenção dos efeitos globais do `py_dss_interface`: singleton com trava, diretório corrente preservado, `SystemExit` capturado, pasta temporária ASCII |
| `workers.py` | 16 workers `QObject` que apenas encapsulam funções puras e emitem `progress/finished/failed/cancelled` |
| `mdb_import_dialog.py` | Tabelas detectadas com ajuste manual, senha mascarada e metadados UTM; `MdbPasswordDialog` é separado porque a senha só se sabe necessária **depois** da primeira tentativa de conexão |
| `mdb_import_report.py` | Relatório consolidado das dez entidades lógicas — não modal, como o de sobreposições, porque os dois abrem sozinhos ao fim de uma operação |
| `main_window.py` | Dono de todo o estado da aplicação; coordena importações, invalidações em cascata, máscaras efetivas, painel de detalhes e menus |
| `circuits_window.py` | `QAbstractTableModel` fino sobre `CircuitVisibilityController` + delegate de cor |
| `branch_window.py` | Tabela de ramais com `QSortFilterProxyModel` (ordenação por `UserRole`, filtro por circuito) |
| `overlap_report.py` | Tabela derivada de `overlapping_segment_indices` |
| `cables_window.py` | Tabela do catálogo de cabos (ordenação numérica por `UserRole`) + rótulos `cable_summary`/`cable_tooltip` reutilizados no painel de trechos |
| `opendss_export_dialog.py` | Lista de circuitos com caixas de seleção **mutuamente exclusivas**; devolve o índice escolhido para a exportação |
| `opendss_settings_dialog.py` | Diálogo dos parâmetros globais **e** a persistência em `QSettings` — que mora aqui, e não no núcleo, porque `QSettings` é Qt (mesma divisão de `theme.py`) |
| `curvas_window.py` | Mestre-detalhe das curvas: lista, nome, grade e gráfico; estado sujo e confirmação ao fechar. A persistência **não** mora aqui, porque JSON não é Qt — ao contrário do `QSettings`, ela fica no núcleo (`curvas_store.py`) |
| `curvas_table.py` | `QAbstractTableModel` fino sobre um `CurveDraft` (coluna "Hora" sintética, só "Valor" editável) + a única `QTableView` do projeto com colar/copiar |
| `curva_chart.py` | Gráfico das 24 horas com `QPainter`: um `QPainterPath` único, cores lidas da paleta a cada pintura, lacuna interrompe o traço |
| `patamares_window.py` | Rascunho privado, validação conjunta, salvamento e confirmação ao fechar; só emite o novo retrato após a gravação atômica |
| `patamares_table.py` | Quatro linhas e cinco campos editáveis, com delegates limitando NPAT a 0–3 e horários a 0–23 |
| `generator_update_dialog.py` | Escolha modal de uma curva e de `DEFAULT`/`Próprios` para cada circuito; entrega um retrato completo ao worker |
| `generator_update_table.py` | Duas grades somente leitura de quatro linhas para demanda total e potência por fase do gerador selecionado |
| `search_palette.py` | Diálogo não modal; roda indexação e consultas em `QThreadPool` com tokens de cancelamento |
| `load_pattern_table.py` | Modelo somente leitura de exatamente 4 linhas (NPAT 0–3) |
| `power_flow_table.py` | Modelo somente leitura da matriz `[patamar][nó]`; não sabe qual grandeza exibe, recebe a matriz já escolhida pelo combobox |
| `phase_legend.py` | `QFrame` filho do viewport, transparente a mouse, reposicionado a cada mudança de viewport |
| `theme.py` | Paletas claras/escuras fixas, leitura e gravação da preferência em `QSettings` e aplicação do tema à aplicação inteira |

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

    ┌─────────────────┐
    │ RegulatorModel  │ (1 regulador por trecho) ──► LineNetworkModel
    └─────────────────┘   folha: ninguém deriva dele
```

**`RegulatorModel` está fora da coluna do meio de propósito.** Ele se pendura em
`LineNetworkModel` pelo `TRECHO_ID`, com o mesmo vínculo 1:1 do `SwitchModel` —
mas, ao contrário das chaves, **não alimenta a topologia**. Reguladores não
interrompem nem energizam nada, então `NetworkTopology.trace()` e
`CircuitCatalogModel` não os consultam, e importá-los não invalida análise
alguma. É a diferença que faz `_set_regulator_model()` ser um setter simples,
enquanto `_set_switch_model()` reconstrói o catálogo inteiro.

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
| `CableRecord` | `cable_id, cable_type, code, iadm, gmr, r, x, qcap, r0, x0, r1, x1, name, external_id` (todos `str`) |
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

**`CableModel`** — único modelo **sem NumPy e sem geometria**: só tuplas de texto
e `_by_id`. É um catálogo raiz, fora do grafo de dependências entre entidades —
os trechos guardam `CABOF_ID`/`CABON_ID` como texto e nunca exigem que o cabo
exista. Por isso `_set_cable_model` não dispara cascata alguma, e nenhuma outra
importação o invalida. Também fica fora do índice de busca global: sem geometria,
não haveria o que enquadrar ao ativar um resultado.

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
          │   │    │                                       │
          ▼   ▼    ▼                                       ▼
 Reguladores Chaves Circuitos                          Patamares
                  │
                  ▼
          Patamares dos circuitos
                  ▲
                  └─ Circuitos usa Chaves (opcional) na topologia energizada
```

O `ImportChoiceDialog` habilita cada botão conforme o estado:
`segments/loads` exigem barras; `switches/regulators/circuits` exigem trechos;
`load_patterns` exige cargas; `circuit_levels` exige circuitos. `cables` é um catálogo isolado — o botão nunca
fica desabilitado e não aparece no diagrama acima.

**Reguladores são a única importação sem cascata.** Todas as demais invalidam
algo: trechos derrubam chaves, circuitos e ramais; chaves reconstroem o catálogo;
patamares refazem a rede equivalente. Reguladores não, porque nada deriva deles —
a seta no diagrama aponta só para dentro. `_set_regulator_model()` instala o
modelo, atualiza a busca e reaplica a seleção; e a única cascata que os atinge é
a inversa, `_set_line_model()` zerando-os quando os trechos mudam.

### O seam `parse_*_rows`: uma validação, duas fontes

Os adaptadores `_parse_file` têm a mesma forma — abrir, ler cabeçalho, resolver
posições de coluna, iterar linhas de texto, validar, montar o modelo. **Só os
três primeiros passos dependem do CSV.** A validação por linha, os `*Issue`, o
teto de `MAX_REPORTED_ISSUES` e a construção do `*Model` são idênticos venha a
linha de um arquivo ou de uma tabela.

Daí a separação em duas camadas por importador:

```
load_*_csv(path, …)        abre o arquivo, tenta utf-8-sig e depois cp1252
  └─ _parse_file(…)        lê o cabeçalho e delega
       └─ parse_*_rows(header, rows, …)   ← toda a validação mora aqui
```

Duas escolhas de assinatura sustentam isso:

- **`progress` encolhe para `Callable[[int], None]`** dentro das `parse_*_rows`:
  elas só sabem quantas linhas leram. O CSV reexpande para a tripla
  `(linhas, bytes, total)` com `byte_progress()`, que lê `source.buffer.tell()`;
  a importação por banco expande para linhas acumuladas da cadeia inteira. A
  `ProgressCallback` pública e o `_on_import_progress` da `MainWindow` não mudam.
- **`source_label`** substitui o `str(path.resolve())` que era montado lá dentro.
  O campo `source_path` dos modelos já era texto livre; o banco grava
  `"C:\...\rede.mdb::TRECHO"`, preservando a tabela de origem.

`first_line_number` existe porque o CSV numera a partir de 2 (a linha 1 é o
cabeçalho) e a tabela, a partir de 1.

A régua desta separação foi dura de propósito: a suíte inteira passa **sem
nenhuma edição em `tests/`**. Um teste que precisasse mudar denunciaria uma
mudança de comportamento escondida na refatoração.

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

**Reguladores** — mesma resolução de `TRECHO_ID` e a mesma invariante 1:1; o
segundo registro do mesmo trecho é descartado com diagnóstico. **Todos os campos
permanecem texto**, inclusive `SNOM`, `NPASSOS` e `TAP`: é a regra do projeto
para dados que só são exibidos, a mesma que mantém `SNOM`/`SADM` das cargas como
texto. `VNOM` e `SNOM` ganharam um consumidor numérico com a exportação (seção
12.3), mas a conversão acontece **lá**, com o `parse_number()` do exportador;
converter na importação seria inventar uma política de arredondamento sem
ninguém para aplicá-la aos demais campos;
e como texto, zeros à esquerda e vírgula decimal chegam ao painel exatamente como
estão no arquivo de origem.

**Cargas** — resolve `BARRA_ID`; todos os demais campos permanecem texto

**Geradores** — dependem do `LoadModel`. O importador indexa `MT_CONS.CODIGO`,
associa cada linha de `MT_GERADOR_CONS` por igualdade exata e resolve
`MT_CONS.CARGA_ID` no modelo de cargas. `GeneratorModel` preserva os dois
registros de origem, o índice da carga e o índice imutável da barra. Substituir
barras ou cargas invalida os geradores; falha ou cancelamento preserva o modelo
anterior. Graficamente, `LoadVirtualizer` é parametrizado pelo tipo de símbolo:
retângulo para carga e círculo para gerador, compartilhando o layout por barra.
(inclusive `SNOM`/`SADM`, convertidos para `Decimal` só na agregação
equivalente).

`parse_generator_rows()` é a única implementação da associação e das
validações. O adaptador CSV fornece dois leitores com fallback independente de
codificação; o MDB fornece dois iteradores ODBC. No diálogo CSV, uma janela
dedicada coleta os dois caminhos antes de iniciar o worker. No diálogo MDB,
`geradores` é uma entidade lógica única com dois seletores, sendo
`geradores_mt_cons` apenas a fonte auxiliar de `MT_CONS` no mapeamento.

**Patamares** — acumula em `dict[load_index][npat]` e só valida no fim:
o grupo de uma carga é aceito somente se tiver exatamente NPAT 0,1,2,3, sem
duplicatas e sem valores inválidos. Grupos rejeitados não impedem os demais.
Falha total apenas se nenhum grupo completo existir.

**Circuitos** — após parsear as definições, chama
`CircuitCatalogModel.build(segments, switches, definitions)`, que executa a busca
topológica por circuito. É o único importador cujo custo dominante não é o
parsing e sim o BFS; por isso propaga `cancel_check` para dentro do `trace`.

### Importação por banco Access

`load_database()` percorre `ENTITY_ORDER` — `barras → cabos → trechos → cargas →
geradores → patamares → chaves → reguladores → circuitos` — e entrega as linhas de cada
tabela à `parse_*_rows` correspondente. A ordem é a das dependências entre
modelos, com um caso não óbvio: **as chaves vêm antes dos circuitos** porque
`parse_circuit_rows` recebe o `SwitchModel` e a topologia energizada depende
dele.

Três decisões merecem registro:

- **Só as barras são fatais.** Qualquer outra entidade que falhe — tabela
  ausente, coluna faltando, nenhum registro válido — vira um `MdbEntityOutcome`
  com o motivo e não interrompe as demais. É coerente com o estado da aplicação
  hoje: trechos sem circuitos, ou cargas sem patamares, são estados válidos. O
  que dependia da entidade que falhou é pulado com a explicação, via
  `ENTITY_DEPENDENCIES`.
- **Só as colunas obrigatórias são projetadas.** `CARGA` tem 43 colunas na base
  de referência e 9 interessam; o `SELECT` lista exatamente essas. Colunas
  extras, como o `CENARIO_ID` de `MODELO_CARGA`, nunca são lidas — a importação
  usa as mesmas colunas do CSV.
- **A conversão de tipos é uma função só, `cell_to_text`.** É a fronteira mais
  perigosa do recurso: três comparações do núcleo são textuais e exatas —
  `FASES2` contra o `fases2.json`, `ESTADO == "1"` em `trace()`, e todo
  `index_for_id` entre tabelas. Um `float` de valor inteiro **precisa** sair sem
  casa decimal: `"1.0"` faria toda chave fechada virar aberta, ilhando a rede
  sem nenhum aviso. Há teste de regressão que importa `ESTADO` como inteiro e
  como `float` e confere que o circuito alcança as três barras.

`detect_database_scale()` espelha `detect_coordinate_scale()` do CSV: amostra o
início da tabela de barras e delega a `scale_from_ranges()`, que é a mesma
decisão para as duas fontes.

Na chegada, `_on_mdb_import_finished` instala os modelos pelos **setters
existentes** (`_set_line_model`, `_set_switch_model`, `_set_circuit_catalog`…),
na mesma ordem. Nenhuma cascata nova é escrita: é o que mantém as invalidações
descritas abaixo exatamente como estão.

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
       ├─ QApplication(sys.argv) + applicationName/organizationName
       ├─ apply_theme(app, load_theme_preference(QSettings()))
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
  │     -12 RegulatorNetworkItem
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
| `RegulatorNetworkItem` | −12 | pontos médios pré-calculados; um `drawEllipse` por regulador, raio derivado da escala do painter |

Todos usam `DeviceCoordinateCache(4096×4096)`: entre mudanças de máscara, o Qt
reaproveita a rasterização em pan e repaints, sem repercorrer os pontos.

O agrupamento por categoria em `LineNetworkItem` é a chave da performance com
cores: em vez de um `QPen` por trecho, há um `QPainterPath` por cor e uma troca
de pen por categoria. O caminho só é recompilado quando **a máscara ou os
estilos** mudam (`geometry_changed`); mudar apenas cores dispara só `update()`.

`RegulatorNetworkItem` é o primeiro símbolo desenhado **sobre** um trecho, e não
como o trecho. Ele obedece à mesma regra de item único: um anel por regulador
dentro de um `paint()` só, nunca um `QGraphicsItem` por símbolo. O raio precisa
ser fixo em pixels, e `ItemIgnoresTransformations` — usado por `BarraItem` e
`LoadItem` — **não serve aqui**, porque é uma flag por item e forçaria a
materialização de um item por regulador. A saída é derivar o raio da escala do
próprio painter (`worldTransform().m11()`), o mesmo idioma que o hit-test já usa
para converter `CLICK_TOLERANCE_PX`. O anel não tem cor por circuito nem por
fase: ele só some junto com o trecho que o hospeda.

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

`MainWindow` mantém quatro slots exclusivos de execução (`_import_thread`,
`_branch_thread`, `_equivalent_thread`, `_power_flow_thread`) e cada entrada de
menu verifica os quatro antes de iniciar — nunca há duas operações pesadas
simultâneas.

**A importação por banco ocupa o slot `_import_thread`**, como as de CSV. A
cadeia inteira roda num worker só porque cada importador recebe o modelo do
anterior: dividir em dez threads exigiria sequenciá-las de qualquer forma, e
ainda multiplicaria as revalidações de identidade na chegada. Uma restrição
física reforça a escolha — **uma conexão ODBC não é segura para atravessar
threads**, então `MdbImportWorker` abre a sua própria conexão dentro de `run()` e
a fecha antes de emitir o sinal. A conexão que a `MainWindow` usa para inspecionar
o banco (tabelas, contagens, amostra de coordenadas) é fechada assim que o
diálogo é montado.

**O fluxo de potência entra nessa exclusão, e a exportação não.** A diferença é
o destino do resultado: a exportação escreve em disco e pode conviver com
qualquer outra operação, enquanto o fluxo devolve grandezas para o estado da
aplicação e por isso precisa dos mesmos modelos do começo ao fim. Há ainda um
motivo físico: a DLL do OpenDSS é global ao processo, então duas execuções
simultâneas compartilhariam o mesmo circuito nativo — `opendss_engine` fecha
essa porta com uma trava própria, e a exclusão de threads evita que o usuário
sequer chegue lá.

**A exportação OpenDSS (`_export_thread`) é a exceção deliberada.** Ela não
entra nessa verificação mútua porque não produz estado compartilhado: o worker
guarda referências próprias aos modelos, que são imutáveis, e o arquivo sai como
um retrato consistente do estado no início da exportação — a mesma semântica de
snapshot da seção anterior, sem precisar de revalidação na chegada, já que o
resultado vai para disco e não para a aplicação. O único acoplamento é o
inverso: `_sync_export_availability()` desabilita o menu enquanto uma importação
ou outra exportação estiver correndo.

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

### 12.3 Exportação OpenDSS (`opendss_export.py`)

**Objetivo:** gerar `trechos.dss` (uma `Line` por trecho comum), `chaves.dss`
(uma `Line ... Switch=Yes` por trecho-chave), `reguladores.dss` (três
`Transformer` + três `RegControl` por regulador trifásico) e um arquivo de cargas
por contagem de fases — `cargasmonofasicas.dss`, `cargasbifasicas.dss` e
`cargastrifasicas.dss`, com `N` `Load` + `N` `LoadShape` por carga — mais o trio
de geradores `geradoresmonofasicos.dss`, `geradoresbifasicos.dss` e
`geradorestrifasicos.dss`, quando existir um `GeneratorUpdateModel`, e o par
`<CODIGO>_Master.dss` e `<CODIGO>_Buscoords.csv`, que cria o circuito, chama os
demais e resolve. `build_export()` monta tudo em sequência e devolve um
`OpenDssExportBundle`; a pasta escolhida na UI recebe todos.

Os três resultados de carga do bundle são **opcionais e andam juntos**. Cargas e
patamares não entram nas precondições do menu — exigi-los desabilitaria também a
exportação da rede, que não depende deles. Sem os dois modelos (ou com patamares
de outra importação, detectados pela regra de identidade) nenhum arquivo de
carga é gerado e `files` volta a ter dois elementos; havendo os dois modelos,
saem os cinco arquivos, mesmo que algum dos de carga fique só com o cabeçalho —
assim a lista de arquivos gerados não depende do conteúdo do CSV, e a
confirmação de substituição na UI pode ser montada antes de exportar.

Os três resultados de gerador também são opcionais e andam juntos. Havendo um
retrato vigente de **Atualizar Geradores**, os três arquivos são montados mesmo
quando uma contagem de fases não tem elementos. Geradores importados sem esse
retrato não são recalculados implicitamente: a UI confirma a operação e segue
sem eles ou cancela sem iniciar o worker.

`OpenDssLoadExportResult` é compartilhado pelos três arquivos de carga. Nele,
`exported_count` conta **cargas de origem**, não linhas `Load`: uma trifásica
rende três `Load` e ainda assim soma 1, para o relatório falar a mesma língua do
CSV importado. `skipped_other_phase_count` conta as cargas de outra contagem de
fases, que pertencem a outro arquivo.

#### Reguladores (`reguladores.dss`)

Um regulador trifásico vira **três transformadores monofásicos**, um por fase,
cada um com o seu `RegControl`. As unidades monofásicas dividem a grandeza
trifásica do CSV: `kV = VNOM/√3` — a mesma `phase_voltage_kv()` do `C1` dos
trechos e do `kV` das cargas — e `kVA = SNOM/3`.

A modelagem que faltava está fixada em constantes do módulo: transformador quase
ideal (`XHL` e `%LoadLoss` de 0,01 %), TP de 115 V, `vreg = 115/√3` e banda
fixa de 3 V — na mesma base de `vreg`, o mesmo jeito de especificar bandwidth
no dial de um regulador real. A relação de PT é `VNOM×1000/115`, e a
simplificação é exata: o √3 do primário e o do secundário se cancelam, de modo
que o controle lê exatamente `vreg` quando a barra está na tensão nominal —
isto é, regula em 1,0000 pu.

**O regulador ocupa o lugar do trecho.** Os transformadores ligam as duas barras
do trecho, exatamente como a `Line ... Switch=Yes` da chave faz, e é por isso que
o trecho **não** pode sair também em `trechos.dss`: duas ligações entre as mesmas
barras poriam a linha em paralelo com o regulador, e a linha curto-circuitaria a
injeção de tensão. Quem cumpre isso é `replaced_segments`, devolvido por
`build_regulator_export()` e recebido por `build_line_export()` como
`skip_segments`. Por isso os reguladores são construídos **antes** dos trechos.

O trecho só é omitido quando o regulador foi **de fato emitido**. Um regulador
descartado mantém a linha dele: apagá-la removeria um ramo inteiro da rede em
silêncio, que é um erro muito pior do que um regulador a menos.

**Só trecho trifásico**, por ora: o recorte é `entry.phase_count == 3`, e as
letras vêm de `_phase_letters(entry.name, 3)`, que absorve o neutro de `DEFN`.
O nó de cada fase sai de `_terminals_by_phase_letter()` — `D`→1, `E`→2, `F`→3 no
`fases2.json` distribuído.

**`VNOM` é conferida contra a do circuito.** A coluna não declara unidade e o
projeto já viu os dois formatos (`13800` e `13,8`); tratada como kV, uma tensão
em volts geraria `kVs` e `ptratio` absurdos que o OpenDSS aceitaria sem reclamar.
Divergência acima de `REGULATOR_VOLTAGE_TOLERANCE` descarta o regulador com
diagnóstico. Substituir um trecho longo também vira aviso — sem descarte —,
porque o transformador não tem comprimento e a impedância daquele trecho sai do
modelo.

**O tap volta do fluxo de potência.** Os reguladores são `Transformer` no modelo
exportado, então o laço de `lines` nunca os alcança: há um laço próprio sobre
`engine.transformers`, casado pelo índice reverso `exported_units` que o
exportador devolve — mesma disciplina do `exported_segments`, nunca uma segunda
implementação das regras de nome. O painel mostra o tap de cada fase e **avisa
quando ele está no fim do curso**: um regulador saturado parou de regular, e sem
esse aviso a interface exibiria uma posição de aparência normal enquanto a tensão
a jusante despenca.

**Consequência no fluxo de potência:** o trecho substituído deixa de ter corrente
no painel. `_harvest_line_currents()` itera `engine.lines`, e um `Transformer`
nunca aparece ali — o que é correto, já que naquele ponto não há mais linha.
`FAIXA`, `NPASSOS` e `TAP` do CSV continuam sem consumidor: a faixa de regulação
é a padrão do `Transformer` do OpenDSS (±10 %, 32 passos).

O filtro de chaves **não é implementado aqui**: os dois arquivos consomem as
duas metades que o `trace()` já separa — `membership.common_segment_indices`
(trechos comuns) e `membership.switch_segment_indices` (trechos-chave).
Reaproveitar essa separação evita uma segunda definição de "trecho é chave"
divergindo da usada na topologia.

Regras de conversão, todas amarradas ao `units=km` emitido em cada linha:

| Propriedade | Origem | Conversão |
|---|---|---|
| nome da `Line` | `CODIGO` do trecho | saneado; `TRECHO_ID` como fallback |
| `Bus1`/`Bus2` | **`CODIGO`** das barras + `DSS` do `FASES2` | saneado; `BARRA_ID` como fallback |
| `Phases` | `NUMERO_FASES` do `fases2.json` | — |
| `R1/X1/R0/X0` | colunas homônimas do cabo de `CABOF_ID` | Ω/km, direto |
| `C1`/`C0` | `QCAP` do cabo + `VNOM` do circuito | `C = Q/(2πf·V_f²)`, `V_f = VNOM/√3` |
| `Length` | `COMPR` | metros → km |

**A tensão da conversão de `C1` é a de fase, não a de linha.** `C1` é a
capacitância shunt entre fase e neutro; como `VNOM` é tensão de linha, a
implementação divide explicitamente por `√3` em vez de embutir o fator 3, para o
código continuar legível como a física que representa. Premissa documentada:
`QCAP` em kvar por km **e por fase**.

**Saneamento de nomes é obrigatório, não cosmético:** no OpenDSS o ponto separa
nós de barra (`bus.1.2.3`) e o espaço separa propriedades, então um `CODIGO` com
ponto, espaço ou acento geraria um arquivo inválido. `sanitize_dss_name()`
reduz por `unicodedata` NFKD para ASCII e troca o resto por `_`. Nomes repetidos
são descartados com diagnóstico — duas `Line` homônimas fariam o OpenDSS
redefinir a primeira silenciosamente.

Trecho pertencente a dois circuitos selecionados sai **uma vez só**: o primeiro
circuito selecionado que o contém é o dono e define a `VNOM` usada em `C1`;
divergência de `VNOM` entre donos vira aviso sem descartar o trecho.

#### Chaves (`chaves.dss`)

| Propriedade | Origem |
|---|---|
| nome da `Line` | **`CODIGO` da chave** (não o do trecho); `CHAVE_ID` como fallback |
| `Bus1`/`Bus2` + sufixo de nós | mesmas regras dos trechos, lidas do trecho onde a chave está |
| `Phases` | `NUMERO_FASES` do `FASES2` do trecho |
| `Switch=Yes` | sempre a **última** propriedade |
| `Open Line.<nome> 1` | emitido no fim do arquivo quando `ESTADO != "1"` |

**`Switch=Yes` é a última propriedade por obrigação, não por estilo.** O
`DSSHelp` documenta o efeito colateral: ele redefine `r1`, `x1`, `r0`, `x0`,
`c1`, `c0` e `length=0.001`. Emitir qualquer parâmetro elétrico depois dele o
apagaria — por isso a chave não recebe `R1`, `Length` nem `units`.

`Open` é comando executivo e exige o elemento já definido, então todas as
definições vêm antes de todos os `Open` no mesmo arquivo. O critério de abertura
é `ESTADO`, com a mesma regra do `trace()`: só `"1"` é fechada.

**Namespace `Line.*` compartilhado.** Os dois arquivos criam objetos no mesmo
espaço de nomes do OpenDSS, e como o nome do trecho vem do `CODIGO` do trecho
enquanto o da chave vem do `CODIGO` da chave, eles podem coincidir. Por isso
`build_export()` passa `line_result.used_names` como `reserved_names` para
`build_switch_export()`: sem isso, a segunda definição sobrescreveria a primeira
em silêncio. As cargas ficam fora dessa reserva: `Load.*` e `LoadShape.*` são
espaços de nomes distintos de `Line.*`.

#### Cargas, um arquivo por contagem de fases

| Propriedade | Origem |
|---|---|
| nome da `Load` | `<CODIGO>-<N>F-<FASE>`; `CARGA_ID` como fallback do `CODIGO` |
| `phases` | sempre `1` — a carga multifásica é decomposta, não declarada |
| `bus1` | **`CODIGO`** da barra + nó da fase (ver abaixo) |
| `conn` | fixo em `wye` |
| `kV` | `VNOM` do circuito dono **dividida por `√3`** |
| `kW`/`kvar` | fixos em `1` |
| `daily` | `PERFIL-<nome da Load>` |
| `class` | a contagem de fases: `1`, `2` ou `3` |
| `mult`/`qmult` do `LoadShape` | `PD`/`QD`, `PE`/`QE` ou `PF`/`QF` por `NPAT` 0–3 |

**Um builder só, parameterizado por `phase_count`.** `build_load_export()` é
chamado três vezes por `build_export()`, uma por entrada de `_LOAD_FILES`. As
três saídas diferem apenas em quantas fases o `NOME` precisa resolver, no
`class` emitido e no arquivo de destino — separá-las em funções distintas
significaria manter três cópias em sincronia.

**N `Load` monofásicas, não uma multifásica.** É a decisão que dá forma ao
módulo: uma única `Load` de `phases=2` ou `3` distribuiria a potência
igualmente entre as fases, apagando exatamente o desequilíbrio que os patamares
por fase (`PD`/`PE`/`PF`) descrevem. A monofásica segue a mesma forma com uma
fase só, por uniformidade.

**A nomenclatura carrega a contagem de fases.** `<CODIGO>-<N>F-<FASE>` deixa o
arquivo legível sem consultar o `fases2.json` e, de quebra, torna impossível que
duas cargas de contagens diferentes colidam de nome, mesmo com o mesmo `CODIGO`.
`build_export()` ainda encadeia `reserved_names` entre os três builders: o
namespace `Load.*` do OpenDSS é de fato compartilhado, e a unicidade atual é
propriedade emergente do esquema de nomes, não invariante imposta — se o esquema
mudar, a reserva continua protegendo contra a sobrescrita silenciosa.

**`_phase_letters()` filtra por letra, não por posição.** Extrai de `NOME` todos
os caracteres em `{D, E, F}` e exige exatamente `phase_count` distintos. Isso
absorve o neutro sem tratamento especial — `DN` → `D`, `DEFN` → `D`, `E`, `F` —
e recusa qualquer `NOME` que não descreva a contagem esperada.

**O nó da fase tem duas origens, e a diferença é deliberada**
(`_phase_nodes()`). Nas **multifásicas** ele vem do `DSS` da entrada
**monofásica** daquela letra, indexado por `_terminals_by_phase_letter()`. Usar
o `DSS` da entrada multifásica seria a escolha óbvia e está errada: ele lista os
nós em ordem crescente, não na ordem das letras do `NOME`. `FD` tem `DSS "1.3"`,
então parear posicionalmente daria `F`→`1` e `D`→`3`, invertendo as fases. Nas
**monofásicas** o `DSS` da própria entrada é usado inteiro, o que preserva o nó
de neutro explícito de `DN`/`EN`/`FN` (`bus.1.0`) — e é por isso que só elas
exigem esse campo preenchido.

**`kW=1 kvar=1` com a potência no `LoadShape`.** É o idioma do OpenDSS para
carga variável: o `mult`/`qmult` multiplica a potência nominal, então fixar a
nominal em 1 faz o perfil carregar os valores absolutos de cada patamar. Os
`LoadShape` são emitidos **antes** de todas as `Load` porque o `daily`
referencia um objeto que precisa já existir — mesma disciplina de ordenação dos
comandos `Open` em `chaves.dss`, e o motivo de o corpo ser montado em duas
seções.

**A carga chega ao circuito pela barra.** `CircuitMembership` associa barras e
trechos, nunca cargas. `_bar_owners()` resolve, em uma passada, o dono de cada
barra dos circuitos selecionados com a mesma regra dos trechos sobrepostos — o
primeiro circuito selecionado vence e define a `VNOM` — e guarda à parte as
barras de `VNOM` divergente, para o aviso só sair quando uma carga de fato usar
aquela barra. Sem isso, uma barra compartilhada sem carga alguma geraria ruído.

**A carga sai inteira ou não sai.** Todas as fases são resolvidas — os
`4 × 2 × N` valores parseados, todos os nomes checados contra `reserved_names` e
`used_names` — **antes** de qualquer linha ser emitida. Carga pela metade no
arquivo subestimaria a demanda em silêncio, que é o pior modo de falha possível
para um estudo de fluxo de potência.

**Zero é valor válido.** Toda a validação numérica usa
`parse_number(...) is None`, nunca teste de verdade: uma fase sem consumo tem
patamar `0`, que precisa ser exportado; só vazio e não numérico invalidam. Há
teste de regressão nos três arquivos de carga.

**Seis casas decimais nos patamares.** `_format_pattern` usa `.6f` em vez do
`.6g` do resto do módulo: os valores de origem têm precisão excessiva e o
arquivo ficaria ilegível com a notação científica que o `%g` escolhe para
magnitudes pequenas.

**`class` é só um rótulo manual.** Não tem efeito elétrico no OpenDSS — existe
para o usuário identificar, ao abrir o arquivo, de que contagem de fases aquela
`Load` veio. Por isso vem por último na linha, depois de `daily`.

#### Geradores como `Load` de potência negativa

`build_generator_export()` recebe somente o `GeneratorUpdateModel` vigente e é
parameterizado por `phase_count`, como o builder de cargas. O retrato já traz o
circuito resolvido, as letras de fase e quatro `GeneratorPhasePowerRecord` por
equipamento; uma posição omitida na atualização nunca chega ao arquivo. Cada
fase vira uma `Load` monofásica e um `LoadShape`, com `kW=1`, `kvar=1`,
`model=1`, `conn=wye` e `qmult` zerado.

O sinal não é transformado no exportador. `generator_update.py` armazena
`PD`/`PE`/`PF = -(DEMANDA/N)`, e esses valores seguem diretamente para `mult`.
Isso garante a convenção consumo positivo/geração negativa e preserva também o
caso de curva negativa, que resulta em potência por fase positiva. A demanda
total por NPAT permanece antes da inversão e continua disponível no painel.

Os nomes `GER-<CODIGO>-<N>F-<FASE>` usam `GERADOR_ID` como fallback e as classes
são `-1`, `-2` e `-3`. `build_export()` monta primeiro as cargas, reúne todos os
seus `used_names` e entrega essa reserva aos geradores; depois encadeia a reserva
entre os três arquivos de geração. Assim o namespace `Load.*` é único e uma
colisão descarta o gerador inteiro antes de emitir qualquer uma de suas fases.
No `element_files`, os três geradores aparecem depois dos três arquivos de
carga, fixando a mesma ordem nos `Redirect` do master.

#### Master e coordenadas (`<CODIGO>_Master.dss`, `<CODIGO>_Buscoords.csv`)

**O único arquivo executável.** Os arquivos anteriores só definem elementos; o
master cria o circuito, chama os demais por `Redirect` e resolve. É o último a
ser montado por `build_export()`, porque a lista de `Redirect` precisa refletir
os arquivos que de fato existem. Um retrato de geradores acrescenta seus três
arquivos mesmo sem cargas de consumo; sem nenhum dos dois, entram somente os
arquivos de rede e os reguladores que existirem.

**A ordem das seções é obrigatória, não estética.**
`Set DefaultBaseFrequency` precede o `New Circuit` porque a frequência base é
fixada na criação do circuito. Os `Redirect` precedem o `calcvoltagebases`, que
exige todas as barras já definidas. E são `Redirect`, não `Compile`: o `Compile`
troca o diretório corrente e quebraria o `Buscoords` relativo do fim.

**Um `New Circuit` energiza um alimentador só.** Por isso o master exige
**exatamente um** circuito selecionado, e `OpenDssExportDialog` passa a ser de
seleção única — marcar um item desmarca os demais. Com vários circuitos, os
arquivos de elementos saem mesclados e uma fonte só deixaria os outros
alimentadores ilhados, com carga não atendida. O caminho futuro é somar um
`New Vsource.<CODIGO>` na barra raiz de cada alimentador adicional; enquanto
isso, `build_master_export()` devolve `text` vazio com o motivo nos `issues`.

**`text` vazio em vez de exceção.** O master é o único builder que pode
legitimamente não produzir arquivo (seleção múltipla, `VNOM` inválida). Devolver
um resultado com `text=""` e o diagnóstico preserva o relatório de ocorrências;
`OpenDssExportBundle.files` simplesmente omite os dois arquivos. A barra raiz
não precisa de guarda: o construtor de `CircuitCatalogModel` já valida que todo
`root_bar_id` existe em `segments.bars`.

**`master_filenames()` é público por causa da confirmação de substituição.** A
UI precisa saber os nomes **antes** de exportar, e eles dependem do `CODIGO` do
circuito. A função aplica a mesma regra de nome do resultado, e um teste amarra
as duas para não divergirem.

**`_format_coordinate` existe porque `_format` estragaria as coordenadas.** O
`.6g` do resto do módulo guarda seis algarismos significativos: um northing UTM
de 8.000.000 viraria `8e+06` e o easting perderia as casas decimais. O CSV usa
`.3f`, e há teste travando exatamente esse caso.

**`BatchEdit` dos limites de tensão das cargas.** Quando o usuário configura
`Vminpu`/`Vmaxpu` em **Configurações → OpenDSS…**, duas linhas entram entre os
`Redirect` e o `Set Voltagebases`:

```
BatchEdit Load..* vminpu=0.8
BatchEdit Load..* vmaxpu=1.2
```

A posição é obrigatória: `BatchEdit` é comando **executivo** e exige as `Load`
já definidas, então não pode subir para antes dos `Redirect` — a mesma
disciplina que já rege os `Open` de `chaves.dss` e os `LoadShape` antes das
`Load`. Precisa igualmente preceder o `Solve`. Que a posição possa ser antes do
`calcvoltagebases` foi medido: `Vminpu` é per unit da `kV` da própria `Load`, e
não das bases de tensão das barras.

**Por que isso não é cosmético.** `Vminpu`/`Vmaxpu` delimitam a faixa em que a
`Load` respeita o seu `model`; fora dela o OpenDSS a converte para impedância
constante. Como o exportador emite `model=1`, a faixa padrão (0,95–1,05) faz
toda barra abaixo de 0,95 pu ter a carga convertida **em silêncio**, e o estudo
subestima a queda de tensão exatamente onde ela interessa. Medido num
alimentador de 20 km carregado: a ponta fica em **0,8970 pu** com o padrão e em
**0,8810 pu** com `vminpu=0.80` — 1,6 ponto percentual que o padrão escondia.

**A configuração é opt-in, e essa é a garantia de compatibilidade.** Sem a caixa
marcada nenhuma linha é acrescentada e o master sai byte a byte como antes de o
recurso existir — há um teste que trava o arquivo linha a linha para esse caso
(`test_master_follows_the_opendss_template`). E `build_export()` só repassa a
configuração ao master quando existe **arquivo de carga**: a DLL trata
`BatchEdit` sem alvo como no-op (`Elements edited: 0`, medido), mas um comando
que edita zero objetos só confundiria quem lesse o arquivo. A divisão é
deliberada — `build_master_export()` emite o que recebe, `build_export()` decide
se faz sentido.

**A validação é nossa porque o OpenDSS não a faz.** Medido: `vminpu=0`,
`vminpu=2` e `vminpu=-1` são aceitos sem erro nem aviso. Quem impede o absurdo
são dois níveis nossos — a invariante `0 < vminpu <= 1 <= vmaxpu` de
`OpenDssLoadSettings` (a faixa precisa conter a tensão nominal, senão a carga
estaria sempre convertida, o oposto da intenção) e as faixas dos campos do
diálogo, que impedem o usuário de sequer digitar o que a invariante recusaria.

**As coordenadas reusam `bus_namer`.** É o mesmo nome que aparece nos
`Bus1`/`Bus2`, e é isso que faz o OpenDSS casar cada ponto com o elemento. Como
`bus_namer` não é injetivo — dois `CODIGO` podem colidir após o saneamento —, a
segunda barra de um nome repetido é descartada com diagnóstico, já que no
arquivo ela apenas sobrescreveria a primeira. A função é pública exatamente por
ser essa definição única: a leitura dos resultados do fluxo de potência
(seção 12.4) precisa concordar com ela.

### 12.4 Fluxo de potência (`opendss_powerflow.py`, `opendss_engine.py`)

**Objetivo:** resolver o fluxo de potência dos circuitos **visíveis** e devolver
corrente por trecho e tensão por barra, um retrato por patamar, associados aos
elementos selecionáveis na tela. Fecha o ciclo que a exportação abria: o usuário
não precisa mais exportar, abrir o OpenDSS e ler os resultados por fora.

**O modelo executado é o modelo exportado.** `run_power_flow()` chama a mesma
`build_export()` da seção 12.3, grava o bundle numa pasta temporária e compila o
master. Não há um segundo gerador de `.dss`, e é isso que garante a equivalência
entre o que a aplicação mostra e o que o usuário obteria à mão — o roteiro de
verificação inclui a prova: o estado após o `Compile` do master coincide, com
divergência zero, com o último patamar colhido.

O `GeneratorUpdateModel` atravessa `PowerFlowWorker`, `run_power_flow()` e o
`PowerFlowResult` por identidade. A `MainWindow` também o inclui no snapshot da
execução: instalar, substituir ou invalidar a atualização de geradores cancela
o worker em andamento e descarta um resultado já exibido. Se há geradores
importados sem atualização vigente, o fluxo pede confirmação e pode executar a
rede sem eles; não existe atualização automática escondida nesse caminho. O
resultado acumula `exported_generators` e `discarded_generators`, e o relatório
apresenta essas contagens junto aos diagnósticos dos três builders de geração.

**`Compile`, não `Redirect`.** É a inversão exata da regra do master, que usa
`Redirect` internamente para não perder o `Buscoords` relativo. Aqui o `Compile`
é desejável justamente porque muda o diretório do OpenDSS para a pasta do
master — é o que faz os `Redirect` internos e o `Buscoords` resolverem.

**Os quatro patamares são reconduzidos pela API.** O master termina com um
`Solve` de `number=4`, que deixa o circuito no estado do **último** patamar. Ler
dali daria um quarto da informação. Depois de compilar, o módulo emite
`Set mode=daily`, `Set stepsize=1h`, `Set number=1` e `Set time=(0, 0)` e faz um
`solution.solve()` por patamar, colhendo entre eles — os passos alinham 1:1 com
`NPAT` 0–3. Um patamar que não converge é registrado em `unconverged` e o estudo
segue; abortar perderia os outros três.

**Um solve por circuito.** `New Circuit` energiza um alimentador só — a mesma
razão de o master exigir seleção única —, então o módulo itera circuito a
circuito. Em rede sobreposta o **primeiro** circuito processado é o dono do
resultado, a mesma regra que a exportação usa para escolher a `VNOM`. Um
circuito sem master (`VNOM` inválida, por exemplo) vai para `skipped_circuits`
com o motivo nos `issues`, e os demais continuam: falha de dado em um
alimentador não deve invalidar o estudo dos outros.

**A associação vem do índice reverso do exportador, nunca de uma segunda
implementação.** O OpenDSS devolve resultados por nome (`Line.xyz`, `barra.1`), e
os nomes nascem de regras não triviais — `CODIGO` saneado, fallback para
`TRECHO_ID`/`CHAVE_ID`, descarte de homônimos, dono do trecho sobreposto. Por
isso `OpenDssLineExportResult` e `OpenDssSwitchExportResult` ganharam
`exported_segments: tuple[tuple[str, int], ...]`, e `_bus_namer` virou público.
Recompor esses nomes aqui seria a mesma divergência silenciosa que a seção 12.3
evita ao reusar a separação de chaves do `trace()`.

**Comparação por `casefold()`, com colisão descartando os dois lados.** O
OpenDSS ignora a caixa dos nomes e devolve tudo em minúsculas; a checagem de
homônimos do exportador **é** sensível a caixa. Logo `TR-01a` e `TR-01A` passam
pela exportação e chegam aqui como um objeto nativo só. Nesse caso nenhuma das
duas entradas sobrevive, com diagnóstico: um resultado atribuído ao elemento
errado é pior que um resultado ausente.

**Colheita em duas formas, por custo.** As tensões saem de três vetores
paralelos que cobrem o sistema inteiro (`nodes_names`, `buses_vmag`,
`buses_vmag_pu`) — três chamadas por patamar, sem laço por elemento. As correntes
exigem o laço `lines.first()/next()` com `cktelement.currents_mag_ang` e
`node_order`, guardando o **terminal 1** (a corrente que entra pela barra de
montante). Chaves também são `Line` no modelo exportado, então vêm de graça no
mesmo laço; e o `IADM` usado no carregamento percentual vem do `CABOF_ID` do
trecho mesmo quando ele carrega uma chave: `Switch=Yes` apaga os parâmetros
elétricos da `Line`, mas o condutor físico daquele ponto continua sendo o do cabo.

**Nós de neutro não são tensão de fase.** Tanto na colheita de tensões quanto na
de correntes, nós `<= 0` são descartados — eles aparecem no `bus.1.0` das
entradas monofásicas com neutro explícito do `fases2.json`.

**Custo medido.** O laço por elemento era o risco da abordagem — uma chamada
`ctypes` por trecho e por patamar. Num alimentador radial sintético de 100 mil
trechos, 100 mil barras e 10 mil cargas, a execução completa (geração dos
arquivos, gravação, `Compile`, quatro solves e colheita de tudo) levou **13 s**,
ou ~32 µs por trecho e patamar. Está confortavelmente dentro do que um worker com
`QProgressDialog` e cancelamento absorve, e por isso a alternativa de trocar a
colheita por `Export Currents` + parse de CSV não foi necessária. Se um dia for,
a troca fica confinada a `_harvest_line_currents()`.

#### As três armadilhas do `py_dss_interface`, e onde cada uma é contida

Todas confirmadas lendo o fonte do pacote, e todas resolvidas em
`opendss_engine.py` para não vazarem para o resto do projeto:

| Armadilha | Contenção |
|---|---|
| `DSS.__init__` chama `os.chdir` para a pasta da DLL — muda o diretório corrente do processo | `acquire_engine()` salva e restaura `os.getcwd()` na entrada e na saída; sem isso todo `QFileDialog` da aplicação passaria a abrir na pasta da DLL |
| Se a DLL não inicia, o pacote imprime e chama `exit()` | `SystemExit` deriva de `BaseException` e escaparia do `except Exception` dos workers; a criação do motor captura `BaseException` e traduz para `PowerFlowEngineError`, e `PowerFlowWorker.run` repete a guarda |
| A DLL é global ao processo: dois `DSS` compartilham o estado nativo | motor **singleton** protegido por `threading.Lock`, com `Clear` na entrada de cada empréstimo |

Há ainda uma quarta restrição, de codificação: `dss.text()` codifica o comando em
ASCII. Um `Compile` apontando para caminho acentuado — o que acontece sempre que
o nome de usuário do Windows tem acento, porque o `TEMP` fica sob ele —
levantaria `UnicodeEncodeError` no meio da execução. `ascii_workspace()` testa as
raízes candidatas antes e falha com mensagem acionável em vez de com o erro de
codificação.

**Dependência opcional de verdade.** `power_flow_import_error()` faz o import
tardio e memoiza o resultado (importar carrega a DLL: caro demais para repetir a
cada `_sync_*_availability()`). Sem a biblioteca, o botão fica desabilitado com o
motivo na dica e o resto da aplicação não muda — e a suíte de testes roda inteira
sem ela, porque o núcleo é exercitado com um motor falso.

#### Apresentação no painel lateral

As páginas de barra e de trecho ganharam uma seção de resultados com um
`QComboBox` de grandeza e uma tabela de quatro linhas (`power_flow_table.py`).
No trecho: **Corrente por fase (A)**, **Carregamento (%)**, **Potência ativa
(kW)**, **Potência reativa (kvar)**, **Potência aparente (kVA)**, **Potência
trifásica**, **Fator de potência** e **Perdas**. Na barra: **Tensão de fase
(V)**, **Tensão de linha (V)**, **Tensão de fase (pu)**, **Tensão de linha
(pu)** e **Desequilíbrio de tensão (%)**. O combobox existe para não empilhar
uma tabela por grandeza num painel que já é denso; a chave da grandeza viaja no
`UserRole` do item, e não na posição.

**As potências são as do terminal 1, com o sinal do OpenDSS intacto** — positivo
entra pelo terminal, negativo sai. É o sinal que informa o sentido do fluxo, e
por isso nada no caminho usa valor absoluto. Os totais trifásicos somam as
potências **complexas** das fases (`P₃ᵩ = ΣPᶠ`, `Q₃ᵩ = ΣQᶠ`), e o módulo e o
ângulo saem de `√(P²+Q²)` e `atan2(Q, P)`.

**Uma armadilha de unidade, medida e não suposta:** `cktelement.powers` devolve
kW e kvar, mas `cktelement.losses` devolve **watts**. A conferência foi contra
`3·R·I²` de um trecho conhecido — a razão deu exatamente 1000. Sem a divisão a
coluna de perdas erraria por três ordens de grandeza sem nada denunciar.

**O desequilíbrio segue o PRODIST Módulo 8:** `FD% = |V₋|/|V₊| × 100`, por
componentes simétricas. Como os fasores completos já estão colhidos, essa é a
definição exata, e não a aproximação por desvio máximo em torno da média. Exige
as três fases; com menos, a grandeza é desabilitada.

**A pu de linha reusa a de fase.** A base pu do OpenDSS é fase-neutro, e a de
linha é `√3` maior — então a mesma `line_voltages()` roda sobre `per_unit` e o
resultado é dividido por `√3`. Nenhuma leitura nova do motor. Sem `IADM` numérico no cabo, o item de
carregamento fica desabilitado e uma nota explica o motivo — em vez de uma coluna
de traços sem explicação. A tensão de linha segue a mesma disciplina numa barra
monofásica, onde não existe par de fases.

**Módulo e ângulo dividem a tabela.** As grandezas que são fasores acrescentam
uma coluna de ângulo por fase (`Fase D` … `θD` …), em graus e no referencial do
OpenDSS — a fonte em 0°. O `pu` fica de fora porque o ângulo dele é o mesmo da
tensão de fase, e o carregamento porque é razão de módulos. Como módulo e ângulo
não têm a mesma precisão útil, `set_values()` aceita **casas decimais por
coluna**.

**A tensão de linha é subtração de fasores, feita em `line_voltages()`** —
função pública do núcleo, testável headless, e não do painel: `VDE = VD − VE`
com os dois em forma retangular. Compor a partir dos módulos daria errado sempre
que as fases não estivessem alinhadas, que é o caso normal; num sistema
equilibrado o resultado correto é `√3` da tensão de fase, adiantado de 30°. É
por isso que `BarVoltages` ganhou `angles`: o ângulo não é enfeite, é insumo. Ele
vem de `AllBusVolts` (dois doubles por nó, parte real e imaginária), porque não
existe um `AllBusVMagAngle` na interface. O ângulo da corrente sempre esteve
disponível — `currents_mag_ang` intercala módulo e ângulo, e o segundo era
descartado.

**Os rótulos `D`/`E`/`F` saem do `fases2.json`**, via
`phase_letters_by_node()` — o inverso do `_terminals_by_phase_letter()` que a
exportação usa para os `Bus1`/`Bus2`. Fixar `{1: "D", 2: "E", 3: "F"}` no painel
criaria uma segunda verdade que divergiria em silêncio de uma configuração
customizada. Nó sem letra, ou `fases2.json` inválido, cai em `Fase <n>`. Por isso
o modelo da tabela recebe **rótulos prontos** em vez de números de nó: nem toda
coluna é um nó — `VDE` é um par e `θD` é um ângulo.

A seção nasce invisível e só aparece quando o elemento selecionado tem resultado.
Qualquer reimportação chama `_invalidate_power_flow()` e a esconde: o resultado
deriva de catálogo, trechos, chaves, cabos, cargas e patamares, e sobreviver a
uma troca de qualquer um deles seria exibir número velho como se fosse novo.

### 12.5 Leitura de bancos Access (`mdb_engine.py`, `mdb_mapping.py`, `mdb_import.py`)

**Objetivo:** importar as dez entidades lógicas direto de um `.mdb`, em modo somente
leitura, sem duplicar nenhuma regra de validação. A mecânica da cadeia está na
seção 6; aqui ficam as decisões que a sustentam.

**A leitura é somente leitura em quatro camadas**, da mais forte para a mais
fraca: `ReadOnly=1` na cadeia de conexão (o atributo que o ACE de fato honra),
`SQL_MODE_READ_ONLY` pelo `readonly=True` do pyodbc, `autocommit` para nunca
abrir transação, e uma API que **só emite `SELECT`** — não há `execute` livre,
`commit` nem cursor exposto para fora do módulo. Se o driver recusar o
`SQL_MODE_READ_ONLY`, a conexão é refeita sem ele e `readonly_attribute` registra
o fato; as outras três camadas continuam valendo, e bloquear a leitura por causa
da camada mais fraca seria pior.

**Dois detalhes da cadeia de conexão foram medidos, não deduzidos.** `Mode=Read`
**não serve**: é atributo do OLEDB, e o ODBC responde "Atributo de cadeia de
conexão inválido Mode". E **nenhum atributo do ACE aceita citação entre chaves**:
`DBQ={C:\...\rede.mdb}` faz o driver tratá-las como parte do nome e responder
"Nome de arquivo inválido (-1044)", e `PWD={senha}` faz a senha chegar ao driver
com as chaves — rejeitada como incorreta **sempre**, independentemente do que o
usuário digite. Por isso caminho e senha vão crus, e um `;` em qualquer um dos
dois é recusado antes de chegar ao driver.

O `DRIVER={...}` é a exceção aparente, e ela confirma a regra: quem lê esse
atributo é o **Gerenciador de Driver do Windows**, que segue o padrão ODBC e
remove as chaves; `DBQ` e `PWD` são lidos pelo **driver do Access**, que faz a
própria análise e não as remove. A assimetria custou um defeito — a senha correta
era recusada em laço, e o teste que existia travava justamente a forma errada.
Hoje há um teste que compara a cadeia gerada com a do
`mdb_viewer_app/database.py` do projeto MDB_VIEWER, conhecido por abrir o mesmo
banco protegido, admitindo só o `ReadOnly=1` a mais.

**Efeito colateral documentado:** enquanto a conexão existe, o ACE cria um `.ldb`
ao lado do banco e o remove ao fechar. Não altera o `.mdb`, mas exige escrita na
pasta — numa pasta somente leitura o Access se recusa a abrir o arquivo.

**O sniff de formato precede a conexão.** O ACE 2013+ não abre Access 97 (Jet 3)
e responde um erro genérico de formato. `sniff_access_format()` lê 32 bytes do
arquivo — magia `Standard Jet DB`/`Standard ACE DB` e o byte 0x14 — e recusa o
Jet 3 com uma explicação acionável. Formato desconhecido **passa**: quem decide é
o driver, e recusar aqui bloquearia um arquivo válido a mais.

**A senha vira exceção própria.** O ACE responde `-1905`/`1907` e é localizado,
então `is_password_error()` casa por código **e** por palavra. `MdbPasswordError`
existe para a interface reperguntar em vez de mostrar o erro ODBC cru. A senha
nunca é gravada, nunca entra em mensagem de erro e o `__repr__` do banco a omite.

**O mapeamento é externo**, `config/mdb_tabelas.json`, pelo mesmo motivo do
`fases2.json`: nome de tabela é convenção da concessionária. As colunas
obrigatórias de cada entidade **vêm dos próprios importadores**
(`EXPECTED_*_HEADER`), e não de uma segunda lista no JSON — uma cópia poderia
divergir em silêncio. O casamento ignora caixa nos dois lados, porque o Access
também ignora.

**A resolução nunca falha por inteiro.** Cada entidade é resolvida
independentemente e a que não encontra tabela ou coluna vira uma
`UnavailableEntity` com o motivo. Uma tabela **forçada pelo usuário** que não
sirva é relatada em vez de cair de volta na detecção: passar por cima de uma
escolha explícita seria pior do que dizer que não deu.

**Geradores consomem duas tabelas como uma entidade.** `MAPPING_ORDER` inclui a
fonte auxiliar `geradores_mt_cons`, enquanto `ENTITY_ORDER` mantém apenas a
linha lógica `geradores`. Assim, detecção e override continuam independentes
por tabela, mas dependência, instalação, progresso e relatório permanecem uma
única operação. A leitura contabiliza primeiro `MT_CONS` e depois
`MT_GERADOR_CONS`, sem regressão na barra global.

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

`_SearchPartition` por tipo de entidade — barra, trecho, chave, **regulador**,
carga e circuito: `dict[código_normalizado →
tuple[SearchResult]]` mais uma tupla de chaves ordenadas. Busca exata é lookup
O(1); busca por prefixo é `bisect_left` + varredura. `normalize_code()` aplica
`casefold` + NFKD + remoção de diacríticos, então “SÃO” encontra “sao”.

Resultados exatos vêm primeiro, depois os por prefixo, ordenados por
`(código, tipo, entity_id, índice)`.

Chaves e reguladores não têm geometria própria: o `target` dos dois é
`FeatureSelection("segment", …)`, porque o que se enquadra e se seleciona é o
trecho onde estão. `_activate_search_result()` distingue os dois só para rolar o
painel até a seção certa.

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

### Externas (opcionais)

| Pacote | Versão | Uso | Extra |
|---|---|---|---|
| `py-dss-interface` | ≥2.3, <3 | motor do OpenDSS para o fluxo de potência | `opendss` |
| `pyodbc` | ≥5.1, <6 | leitura de bancos Access (`.mdb`/`.accdb`) | `mdb` |

Instalação: `pip install -e ".[opendss,mdb]"`. Sem `py-dss-interface`, apenas o
botão **Executar Fluxo de Potência** fica desabilitado; sem `pyodbc`, apenas
**Importar banco de dados…**. Os dois imports são tardios e vivem só em
`opendss_engine.py` (seção 12.4) e `mdb_engine.py` (seção 12.5).

`pyodbc` sozinho não basta: é preciso o **driver ODBC do Microsoft Access
Database Engine na mesma arquitetura do processo Python**. Um Python de 64 bits
não enxerga o driver de 32 bits, e o sintoma é "driver não encontrado" com o
driver visivelmente instalado — por isso `mdb_import_error()` cita a arquitetura
na mensagem.

### Desenvolvimento

`pytest` ≥8, `pytest-qt` ≥4.4 (extra `test`).

### Grafo interno (sem ciclos)

```
__main__ → main_window → {graphics, workers, *_window, *_table, search_palette,
                          model, phase_config, mapa_tiles, *_import,
                          opendss_engine, opendss_powerflow}
workers  → {*_import, branch_analysis, equivalent_network, model, phase_config,
            opendss_export, opendss_engine, opendss_powerflow,
            mdb_engine, mdb_import}
mdb_import   → {*_import (as parse_*_rows), csv_import, mdb_engine, mdb_mapping,
                model}
mdb_mapping  → {*_import (só os EXPECTED_*_HEADER), mdb_engine}
mdb_engine   → ∅  (pyodbc entra por import tardio)
graphics → {model, equivalent_network, mapa_tiles}
equivalent_network → {branch_analysis, model}
branch_analysis    → {model, phase_config}
opendss_powerflow  → {model, phase_config, opendss_export, opendss_settings,
                      opendss_engine}
opendss_settings   → opendss_export (só parse_number, para não haver duas
                     regras de leitura numérica)
opendss_export     → {model, phase_config}
opendss_engine     → ∅  (py_dss_interface entra por import tardio)
search   → model
phase_config → model (apenas o alias de tipo IndexArray)
model    → circuit_colors
*_import → {csv_import (exceções), model}
```

`opendss_engine.py` e `mdb_engine.py` são folhas do pacote de propósito: cada um
define o `Protocol` que o seu consumidor usa — `DssEngine` para
`opendss_powerflow`, `AccessDatabase` para `mdb_import` —, e importar qualquer
coisa do projeto ali arrastaria a contenção da dependência externa para dentro do
grafo do domínio.

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

**Uma validação, duas fontes (`parse_*_rows`).** Aceitar uma segunda fonte de
dados sem duplicar as regras exigia separar "de onde vêm as linhas" de "o que
faz uma linha ser válida". A alternativa — um importador de banco próprio —
criaria vários pares de regras capazes de divergir em silêncio, exatamente o modo
de falha que a seção 12.3 evita ao reusar a separação de chaves do `trace()`.

**Conversão de tipos numa função só (`cell_to_text`).** Um banco é tipado e o CSV
não; a fronteira entre os dois precisa de um lugar único, porque três comparações
do núcleo são textuais e exatas. A regra que parece um detalhe — `float` de valor
inteiro sai sem casa decimal — é a que impede `ESTADO` virar `"1.0"` e abrir toda
chave fechada da rede.

**Mapeamento de tabelas externo (`mdb_tabelas.json`).** Mesmo raciocínio do
`fases2.json`: nome de tabela e de coluna é convenção da concessionária. As
colunas obrigatórias, porém, vêm dos `EXPECTED_*_HEADER` dos importadores — essas
**são** regra da aplicação, e duplicá-las no JSON criaria uma segunda verdade.

**Configuração de fases externa (`fases2.json`).** O mapeamento `FASES2` →
número de fases é convenção da concessionária, não regra de negócio da
aplicação. Erro no arquivo desabilita **apenas** os modos que dependem dele
(coloração por fases, ramais, rede simplificada) — o resto continua funcionando.

**Paleta OKLCH.** Espaço perceptualmente uniforme com amostragem pelo ângulo
áureo e contraste mínimo 3:1 com branco: circuitos adjacentes ficam
distinguíveis mesmo em quantidade alta.

**Tema explícito com Fusion + paleta fixa.** O tema é escolha do usuário, nunca
inferência do sistema operacional. O estilo nativo (`windows11`) ignora a paleta
da aplicação e segue o SO, então `apply_theme` troca o estilo para `Fusion`, que
a honra. As duas paletas de `theme.py` têm valores fixos de propósito: a partir
do Qt 6.8 o `standardPalette()` do Fusion passou a acompanhar o esquema de cores
do sistema, o que reintroduziria a inferência que se quer evitar. O
`setColorScheme()` (Qt ≥ 6.8, chamada guardada por `hasattr` porque o
`pyproject.toml` admite PyQt6 6.7) fixa também a barra de título nativa. Como as
folhas de estilo dos widgets sempre usaram `palette(mid)`/`palette(window)` em
vez de cores literais, nenhuma delas precisou mudar — só um ciclo de
`unpolish`/`polish` para reavaliá-las. **O canvas fica fora do tema**: as cores
de `graphics.py` e a paleta OKLCH garantem contraste com fundo branco
(`MIN_WHITE_CONTRAST`), então o diagrama permanece claro nos dois temas.

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
   (`EXPECTED_*_HEADER`, `_column_positions`, `parse_*_rows`, `_parse_file`,
   `load_*_csv`, `*Issue`, `*LoadResult` com `has_warnings`). Toda a validação
   vai em `parse_*_rows`; `_parse_file` só abre o arquivo e delega.
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
8. Para que a entidade também venha de banco: acrescentar a entrada em
   `config/mdb_tabelas.json`, o nome em `ENTITY_ORDER`, `REQUIRED_COLUMNS`,
   `ENTITY_LABELS` e `ENTITY_DEPENDENCIES`, e o ramo em
   `mdb_import._import_entity`. O diálogo e o relatório se ajustam sozinhos,
   porque ambos percorrem `ENTITY_ORDER`.

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

O tema é a única preferência persistida (`QSettings`, chave
`appearance/theme`); cores, filtros e provedor de satélite valem só para a
execução atual. Um ponto natural seria serializar
`CircuitVisibilityController.colors` e `checked_states` indexados por
`circuit_id` no mesmo `QSettings` injetado em `MainWindow`, reaproveitando o
mecanismo de remapeamento já usado em `_set_switch_model`.

O `DEFAULT` dos patamares é dado estruturado, não preferência: vive em
`dados/patamares.json`. `MainWindow` mantém somente o último
`CalculationLevelSchedule` global salvo; a janela edita uma cópia em rascunho,
para que consumidores futuros nunca observem valores ainda não confirmados.

As agendas de `CIRCUITO_PATAMARES` seguem outra regra. O parser CSV/MDB monta
um `CircuitCalculationLevelsModel` imutável, denso e vinculado por identidade
ao `CircuitCatalogModel`. Cada posição é uma agenda completa ou `None`; apenas
grupos com `CIRC_ID` existente e os NPAT 0–3 válidos entram na lista da janela.
Ao instalar a fonte, a `MainWindow` cria um
`CircuitCalculationLevelsController`, cuja lista mutável é a cópia virtual da
sessão. Salvar um circuito troca uma posição dessa lista e não chama nenhuma
função de persistência. Uma nova importação bem-sucedida cria outro
controlador; falha ou cancelamento mantém o anterior. Substituir o catálogo
zera ambos por dependência de identidade.

Os dois cadastros de horários permanecem separados do `LoadPatternModel`, que
contém as potências importadas por carga para cada NPAT. As agendas alimentam
somente `generator_update.py`; a exportação e o fluxo consomem o resultado
derivado, nunca as agendas diretamente.

### Exportar dados

`BranchTableModel._raw_values()` e `OverlapReportTableModel` já expõem os dados
em forma tabular — um exportador CSV/XLSX é um consumidor direto desses modelos,
sem tocar no núcleo.

### Curvas e patamares no cálculo dos geradores

`generator_update.py` cria um retrato derivado sem tocar em `GeneratorModel`, no
`CURVA_ID` importado ou nos cadastros. `GeneratorUpdateModel` guarda por
identidade os geradores, circuitos e a configuração de fases usados, além da
curva escolhida, da agenda efetiva e da origem (`DEFAULT`/`circuit`) de cada
circuito. Arrays densos por índice de gerador guardam demanda média, circuito e
os dois grupos de quatro registros; uma posição `None` representa um gerador
omitido.

A associação gerador→circuito é invertida das `CircuitMembership.bar_indices`.
Exatamente um proprietário é obrigatório: zero ou mais de um gera diagnóstico.
`PhaseConfiguration.phase_letters_for_value()` interpreta o `NOME` de
`fases2.json`, extrai D/E/F e confere a quantidade declarada. A potência ativa é
dividida somente entre essas letras e invertida (`-(DEMANDA/N)`); as posições de
fases ausentes e todas as reativas começam em zero. A demanda total conserva o
sinal anterior à inversão, portanto o resultado já tem as duas formas tabulares
exibidas no painel e a convenção elétrica pronta para o OpenDSS.

O cálculo usa `parse_number(GERACAO_KWH) / 720` e multiplica pela curva na
`HORARIO_REF`. Como a grade de curvas é visualmente 1–24 e os patamares usam
0–23, `curve_value_at_reference()` concentra a convenção: 1–23 mantêm o mesmo
número e 0 consulta o ponto visual 24. Não há arredondamento no núcleo; quatro
casas são apenas apresentação no painel.

O resultado vive somente na `MainWindow`. O worker recebe todos os retratos
antes de começar e o novo valor só é instalado no sinal de sucesso. Cancelar,
falhar ou terminar sem geradores válidos mantém o anterior. Trocar geradores,
circuitos ou agendas invalida o resultado; ao salvar curvas, a janela compara
por `curve_id` e conteúdo e invalida somente se a curva usada mudou ou sumiu.
Uma edição ainda não salva não participa do cálculo.

O resultado continua exclusivamente em memória, mas agora é uma entrada
opcional de `build_export()` e `run_power_flow()`. O exportador não recalcula nem
inverte valores: ele transporta `PD`/`PE`/`PF` ao `LoadShape`, preservando o
retrato transacional produzido pelo worker.

Atenção a uma armadilha: `LOAD_PATTERN_COUNT` (=4) descreve os patamares NPAT
importados e é usado em laço na exportação e importado por `opendss_powerflow`.
As curvas horárias usam a constante própria `HOURLY_CURVE_POINT_COUNT` (=24);
unificar as duas quebraria a exportação inteira.

O formato em disco também está preparado: o leitor **ignora chaves
desconhecidas** dentro de cada curva, então acrescentar `"tipo"`, `"unidade"` ou
`"observacao"` não exige virar `CURVES_FILE_VERSION` nem invalida um arquivo
lido por uma build anterior.

---

## 18. Testes e benchmarks

### Testes (`tests/`, 53 arquivos)

| Arquivo | Foco |
|---|---|
| `test_model.py` | entidades, índices espaciais, topologia |
| `test_csv_import.py` · `test_segment_import.py` · `test_switch_import.py` · `test_regulator_import.py` · `test_load_import.py` · `test_generator_import.py` · `test_load_pattern_import.py` · `test_circuit_import.py` · `test_circuit_level_import.py` · `test_cable_import.py` | importadores e casos de erro; geradores cobrem a associação dos dois CSVs e patamares por circuito cobrem grupos completos, identidade, CSV/MDB e cópia de sessão |
| `test_calculation_levels.py` · `test_calculation_levels_store.py` · `test_patamares_ui.py` · `test_circuit_levels_ui.py` | validação horária, JSON atômico do DEFAULT, combo por circuito e garantia de salvamento exclusivamente em memória |
| `test_phase_config.py` | validação do `fases2.json` |
| `test_circuit_colors.py` | paleta e contraste |
| `test_branch_analysis.py` · `test_equivalent_network.py` | análises topológicas |
| `test_opendss_export.py` | linhas de trecho, de chave e das cargas de uma, duas e três fases, conversão de `C1` e do `kV` pela tensão de fase, ordem `New`/`Open` e `LoadShape`/`Load`, nomenclatura `-NF-<FASE>`, terminal por letra, neutro preservado só na monofásica, colunas de patamar por fase, patamar zerado, descarte integral da carga, reserva de nomes entre os arquivos, arredondamento, saneamento, master (ordem das seções, `Redirect` conforme os arquivos gerados, coordenadas em casas fixas) e diagnósticos |
| `test_opendss_generator_export.py` | três arquivos de geradores, perfis ativos negativos sem dupla inversão, classes negativas, terminais e tensão de fase, seleção por circuito, fallback, descarte integral, namespace `Load.*` compartilhado e ordem dos `Redirect` |
| `test_opendss_settings.py` | invariante da faixa (`0 < vminpu <= 1 <= vmaxpu`), comandos `BatchEdit` exatos e sem vírgula decimal, desabilitado não emite nada, ida e volta pelo mapeamento e recuperação de preferência corrompida |
| `test_opendss_engine.py` | detecção da biblioteca opcional, memoização do erro de import, reuso do motor único, diretório corrente restaurado (inclusive após falha) e escolha da pasta ASCII |
| `test_opendss_powerflow.py` | com um **motor falso**: arquivos gravados iguais aos da exportação, inclusão e identidade do retrato de geradores, ordem `Clear`/`Compile`/`Set …`, um `Solve` por patamar, corrente no trecho certo (inclusive em chaves), só o terminal 1, tensões e pu por nó, neutro descartado, `IADM` ausente, patamar não convergido, colisão de caixa em nome de linha e de barra, circuito sem master, sobreposição resolvida pelo primeiro circuito, progresso e cancelamento |
| `test_mdb_engine.py` | `cell_to_text` exaustivo (o inteiro sem `.0`, o decimal íntegro, Sim/Não, nulo, binário), sniff de formato por versão do Access, detecção de senha, cadeia de conexão somente leitura **sem chaves em `DBQ` nem em `PWD`** e comparada com a forma comprovadamente funcional, recuo do `SQL_MODE_READ_ONLY`, senha fora das mensagens, e a garantia de que só `SELECT` é emitido |
| `test_mdb_mapping.py` | apelidos de coluna, casamento sem caixa, tabela de reserva, tabela e coluna ausentes desabilitando só a própria entidade, tabela ilegível reportada, `MSys*` nunca escolhidas, override que não cai de volta na detecção, e o JSON distribuído conferido contra a base real |
| `test_mdb_import.py` | com um **banco falso**: ordem de dependência (chaves antes de circuitos), identidade encadeada dos modelos, projeção só das colunas obrigatórias, `CENARIO_ID` ignorado, dedução de decímetros, entidade ausente não derrubando as demais, barras fatais, cancelamento nunca virando falha de entidade, progresso único da cadeia, e a **regressão de tipos**: `ESTADO` inteiro e `float` mantendo a chave fechada e a topologia alcançando as três barras |
| `test_mdb_import_ui.py` | com um `MdbImportResult` injetado: instalação dos dez modelos pelos setters existentes, catálogo sobrevivendo à cascata das chaves, banco parcial, diálogo (pré-seleção, override manual reabilitando entidade, barras obrigatórias), senha mascarada, e o relatório não modal |
| `test_search.py` | índice de busca (sem Qt) |
| `test_curvas.py` | invariantes da curva (24 pontos, sem `nan`/`inf`, negativos e zero aceitos), `curve_id` sobrevivendo à renomeação, unicidade de nome sem caixa, horas faltantes em base 1, e o parser de colagem: `\r\n`/`\r`, linha vazia final descartada mas a do meio preservada, bloco de duas colunas, vírgula decimal e recusa de `1.234,56` |
| `test_curvas_store.py` | ida e volta preservando id/acentos/negativos, criação do diretório, regravação por cima (regressão de `os.rename` no Windows), nenhum `.tmp` remanescente, e a tolerância de leitura: JSON quebrado, raiz inesperada, entrada inválida entre válidas, versão mais nova, ids/nomes repetidos, id ausente gerado e chaves desconhecidas ignoradas |
| `test_curvas_table.py` | coluna "Hora" sintética e não editável, `EditRole` sem truncar a precisão, `setData` com ponto e vírgula, texto recusado sem apagar o valor anterior, célula esvaziada, colagem com âncora, truncamento na hora 24 e a **regressão de alinhamento** (um texto não numérico no meio não desloca as horas seguintes) |
| `test_curvas_ui.py` | estado vazio, criação/renomeação/exclusão com confirmação, recusa de salvar incompleta sem criar o arquivo, gravação e `curvesSaved`, fechar com Salvar/Descartar/Cancelar (Descartar relê o disco), gráfico acompanhando a edição, os casos-limite de pintura (vazio, todos iguais, todos zero, faixa negativa, lacunas) e a entrada de menu **Configurações → Curvas…** |
| `test_calculation_levels.py` · `test_calculation_levels_store.py` | padrões, invariantes do ciclo de 24 horas, referência nos limites, isolamento do rascunho, round-trip JSON, fallback integral e gravação atômica |
| `test_patamares_ui.py` | cinco colunas totalmente editáveis, limites dos delegates, estado sujo, validação conjunta, salvamento, ordenação, descarte/cancelamento, menu e persistência entre janelas principais |
| `test_generator_update.py` | fórmula e precisão, ponto/vírgula, hora 0→24, DEFAULT/próprios, inversão somente da potência por fase, curva negativa, omissões, identidade, progresso e cancelamento |
| `test_generator_update_ui.py` | diálogo por circuito, seleção inicial DEFAULT, demanda total positiva e potência por fase negativa no painel, mensagem de omissão, ação em Ferramentas, resolução de rascunhos e invalidação do fluxo |
| `test_graphics.py` · `test_main_window.py` · `test_branches_ui.py` · `test_circuits_ui.py` · `test_phase_ui.py` · `test_search_ui.py` · `test_map_tiles.py` · `test_satellite_ui.py` · `test_theme_ui.py` · `test_cables_ui.py` · `test_opendss_export_ui.py` · `test_powerflow_ui.py` · `test_opendss_settings_ui.py` · `test_regulators_ui.py` | camadas Qt (exigem PyQt6) |

Os testes do núcleo usam apenas a biblioteca padrão e NumPy; os gráficos rodam
quando PyQt6 está disponível. **Nenhum teste exige `py-dss-interface` nem
`pyodbc`:** o fluxo de potência é exercitado com um motor falso e um
`PowerFlowResult` injetado na UI, e a importação por banco com um banco falso e
um `MdbImportResult` injetado — o que também mantém a suíte rápida e
determinística.

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
| `REGULATOR_DIAMETER_PX` | 9.0 | anel do regulador, no meio do trecho |
| `REGULATOR_RING_WIDTH_PX` | 2.0 | espessura do anel |

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
