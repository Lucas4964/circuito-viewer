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

Use **Arquivo > Importar…** e escolha entre barras, trechos, chaves ou circuitos. A opção de
trechos fica disponível depois que as barras forem carregadas. O arquivo de
trechos deve conter `TRECHO_ID`, `CODIGO`, `FASES2`, `BARRA1_ID`, `BARRA2_ID`,
`ARRANJO_ID`, `CABOF_ID`, `CABON_ID` e `COMPR`. A ordem é livre e colunas
adicionais são ignoradas. Trechos que referenciam barras inexistentes são
omitidos e relatados.

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

- Roda do mouse: zoom no cursor.
- Ferramenta **Mover**, botão do meio ou `Espaço` + arraste: pan.
- Ferramenta **Selecionar**: clique próximo de uma barra ou trecho para
  inspecioná-lo no painel lateral. Barras têm prioridade nos pontos de conexão.
- **Visualizar > Mostrar barras**: alterna a visibilidade e a seleção das barras
  sem ocultar os trechos ou as chaves.
- **Visualizar > Circuitos…**: abre a tabela não modal de circuitos. Desmarcar um
  circuito oculta suas barras, trechos e chaves sem apagar a associação calculada.
  A coluna **Cor** mostra a cor automática do circuito e abre um seletor ao ser
  clicada; alterar a cor não recalcula a topologia.
- **Visualizar > Sobreposições…**: lista os trechos associados a mais de um
  circuito. O relatório também é aberto automaticamente quando uma sobreposição
  é encontrada.
- **Enquadrar tudo** ou tecla `F`: mostra todo o conjunto.
- `S` e `M`: ativam Selecionar e Mover.

## Testes e benchmark

Os testes do núcleo usam apenas a biblioteca padrão e NumPy. Os testes gráficos
são executados quando PyQt6 estiver instalado.

```powershell
python -m unittest discover -s tests -v
python benchmarks\benchmark_100k.py --enforce
python benchmarks\benchmark_segments_17k.py --enforce
python benchmarks\benchmark_switches_17k.py --enforce
python benchmarks\benchmark_circuits.py --enforce
```

Os benchmarks geram temporariamente 100 mil barras, 17 mil trechos e 17 mil
chaves, medem a importação/indexação, o desenho agregado em 1920×1080 e a
latência p95 da seleção geométrica de trechos. O benchmark de circuitos também
mede a geração da paleta, a categorização agregada e a troca de cor sem reconstruir
a geometria.

## Organização

- `circuit_viewer/model.py`: modelo lógico e índice espacial.
- `circuit_viewer/csv_import.py`: importação transacional.
- `circuit_viewer/segment_import.py`: importação e vínculo dos trechos.
- `circuit_viewer/switch_import.py`: importação e associação das chaves.
- `circuit_viewer/circuit_import.py`: importação e associação topológica dos circuitos.
- `circuit_viewer/circuit_colors.py`: paleta contrastante e conversão OKLCH/sRGB.
- `circuit_viewer/circuits_window.py`: tabela de visibilidade e cores dos circuitos.
- `circuit_viewer/overlap_report.py`: relatório tabular das sobreposições.
- `circuit_viewer/graphics.py`: canvas, visão agregada e virtualização.
- `circuit_viewer/main_window.py`: interface e integração assíncrona.

As pastas/fontes de referência `src/` e `script20.py` não são modificadas nem
usadas como dependências de runtime.
