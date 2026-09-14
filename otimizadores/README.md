# Otimizadores de fase dos ramais monofásicos

Três scripts autônomos que respondem à mesma pergunta: **em qual fase (A, B ou C)
ligar cada ramal monofásico** para minimizar o desequilíbrio do alimentador.

Eles não fazem parte do pacote `circuit_viewer` — rodam sozinhos, pela linha de
comando, e consomem o que o visualizador **exporta**. São ferramentas de estudo,
não parte da aplicação.

## Entradas

Os três perguntam as mesmas duas coisas ao iniciar:

1. **A pasta do circuito**, a que contém o `*_Master.dss`. É a saída de
   *Exportar para OpenDSS* do visualizador. Dentro dela os scripts também
   procuram `ramalmonofasico.dss` e `chaves.dss`.
2. **O `.json` dos ramais**, a saída de *Análise de ramais → exportar JSON*
   (`circuit_viewer/branch_json_export.py`).

Do JSON eles usam, por registro `RAMAL-<id>`: `barra_inicio`, `fase` e as oito
potências por patamar `P0..P3` / `Q0..Q3`.

O JSON é **conferido contra o DSS** antes de qualquer conta: se a barra ou a
fase de um ramal divergirem entre os dois arquivos, o script para e diz qual
ramal. É o que impede otimizar um circuito com um JSON de outra exportação.

## Função objetivo

A mesma nos três: a **parcela de perdas por componentes simétricas**,

```
3 * L * (R0*|I0|² + R1*|I2|²)
```

somada nas linhas trifásicas e nos quatro patamares. Trocar fases só ataca a
sequência zero e a negativa, então é essa parcela — e não a perda total — que
ordena os resultados. No circuito de referência a parcela varia num fator de
1,19 entre as candidatas, enquanto a perda total varia só 1,6%; ordenar pela
total esconderia o ganho. As duas são calculadas e reportadas.

## Qual usar

| Script | Método | Quando |
|---|---|---|
| `otimiza_ramal_forca_bruta.py` | Enumera **todas** as 3^N combinações, em `multiprocessing.Pool` | N pequeno (até ~10 ramais). Dá o **ótimo global com prova de otimalidade** |
| `otimiza_ramal_balanco_fases.py` | Duas etapas: peneira analítica + revalidação das melhores K por fluxo de potência | O caso geral, e o mais rápido com folga |
| `otimiza_ramal_optuna_i0_i1_i2.py` | Optuna `TPESampler` multivariado, 5.000 avaliações | N grande, onde a enumeração não cabe |

### A peneira de duas etapas

`otimiza_ramal_balanco_fases.py` é o que rende. A **etapa 1** roda o fluxo de
potência **uma vez**, com os ramais de interesse desativados, lê a potência por
fase na cabeceira e daí avalia as 3^N combinações por aritmética pura — somas
vetorizadas das potências próprias já disponíveis no JSON. A **etapa 2** pega só
as K melhores e as roda de verdade, no circuito completo, reordenando pela
função objetivo acima.

As duas etapas medem coisas diferentes de propósito: o índice analítico enxerga
a potência por fase em **um ponto**, a cabeceira; a função objetivo enxerga a
corrente de sequência zero e negativa em **cada linha trifásica**. O índice
peneira barato, a função objetivo decide.

Medido contra a varredura exaustiva no mesmo circuito (10 ramais, 59.049
combinações):

- a parcela calculada reproduz exatamente a da varredura — 222,08 kWh para a
  configuração original, 98,01 kWh para o ótimo;
- o ranking analítico **não** serve para decidir sozinho: a melhor pelo índice
  cai na posição 40 do ranking verdadeiro, e o ótimo verdadeiro cai na posição
  305 do analítico (Spearman 0,596). O topo do índice é confiável, o corpo não;
- revalidando o top-50, captura **97% da redução** que a varredura encontrou,
  com **52 fluxos de potência em vez de 59.049** — 0,2 s contra 5 horas.

## Saídas

CSV na pasta do circuito:

- `resultados_forca_bruta.csv` — o ranking completo da varredura;
- `resultados_balanco_fases.csv` — o ranking analítico;
- `resultados_balanco_fases_top.csv` — as K revalidadas por fluxo de potência;
- `resultados_balanco_fases_benchmark.csv` — a comparação contra um
  `resultados_forca_bruta.csv` já existente, quando informado.

## Dependências

- `py_dss_interface` — o mesmo extra opcional `[opendss]` do projeto;
- `numpy` — já é dependência do projeto;
- `optuna` — **só** para `otimiza_ramal_optuna_i0_i1_i2.py`, importado dentro da
  função, então os outros dois rodam sem ele.

```bash
pip install optuna
```

## Um detalhe do OpenDSS

Em modo `daily`, cada `Solve` avança `number` passos de tempo. Com `number > 1`
o laço dos patamares lê sempre o mesmo instante do LoadShape, e a função
objetivo passaria a medir um patamar repetido em vez da soma dos quatro. Os
scripts exigem `Set number=1` e verificam isso.
