"""Balanceamento de fases dos ramais em duas etapas.

ETAPA 1, busca analitica. Roda o fluxo de potencia UMA vez, com os ramais de
interesse desativados, e le a potencia por fase na saida do alimentador em cada
patamar. Dai em diante cada uma das 3^N combinacoes e avaliada por aritmetica
pura: somam-se as potencias proprias de cada ramal (P0..P3 e Q0..Q3, ja
disponiveis no JSON) na fase que aquela combinacao lhe atribui, e mede-se o
desequilibrio da potencia resultante na cabeceira. Sao 3^N somas vetorizadas em
vez de 3^N fluxos de potencia: na rede de exemplo, 0,2 s contra as 5 horas que
a varredura exaustiva de otimiza_ramal_forca_bruta.py levou.

ETAPA 2, revalidacao. O ranking analitico NAO decide nada sozinho: ele so
escolhe quais K combinacoes merecem um fluxo de potencia. Essas K rodam no
circuito completo e sao reordenadas pela MESMA funcao objetivo do script de
forca bruta, a parcela de perdas por componentes simetricas,
3 * L * (R0*|I0|^2 + R1*|I2|^2) somada nas linhas trifasicas. E esse segundo
ranking que produz a recomendacao.

As duas etapas medem coisas diferentes de proposito. O indice analitico enxerga
so a potencia por fase num ponto, a cabeceira; a funcao objetivo enxerga a
corrente de sequencia zero e negativa em cada linha trifasica da rede. O indice
serve para peneirar barato, a funcao objetivo serve para decidir.

Medido contra a varredura exaustiva ja executada (resultados_forca_bruta.csv,
10 ramais, 59.049 combinacoes, mesmo circuito e mesmo Set number=1):

  - a parcela calculada aqui reproduz a da varredura: 222,08 kWh para a
    configuracao original e 98,01 kWh para o otimo FFFFFFFFFF, iguais ao CSV;
  - a melhor pelo indice analitico cai na posicao 40 de 59.049 do ranking
    verdadeiro, e o otimo verdadeiro cai na posicao 305 do ranking analitico.
    Spearman entre os dois rankings: 0,596. O topo do indice e confiavel, o
    corpo dele nao e;
  - revalidando o top-50, o metodo captura 97% da reducao de desequilibrio que
    a varredura encontrou, com 52 fluxos de potencia em vez de 59.049.

Por que a ordenacao final usa a parcela de desequilibrio e nao a energia
perdida total, embora as duas sejam calculadas e reportadas: entre as 48
candidatas validas do benchmark, a parcela varia num fator de 1,19 (98,0 a
117,0 kWh) enquanto a perda total varia num fator de 1,016 (1384,4 a
1406,9 kWh). Trocar fases so ataca a parcela de sequencia zero e negativa; a de
sequencia positiva, que domina a perda total, nao muda com o arranjo de fases.
Ordenar pela perda total seria ordenar por 1,6% de sinal misturado ao ruido dos
taps de regulador. Vale registrar que, nesta rede, as duas escolhem a mesma
vencedora: FFFFFFFFFF tem ao mesmo tempo a menor parcela e a menor perda total.

Qual indice usar na etapa 1. Como a etapa 2 reordena tudo com a funcao
objetivo real, o que importa no indice nao e acertar o primeiro lugar: e a boa
combinacao ESTAR entre as K escolhidas. Medindo a posicao do otimo da varredura
em cada indice, que e o numero que decide quanto K precisa ser:

  S ...... 55        PQ ..... 56
  FD .... 195        P ..... 305

O modo TODOS nao escolhe: pega o top-K de cada um dos quatro e roda a uniao. No
pior caso sao 4*K fluxos, mas os indices se repetem bastante e sobra bem menos
(K=60 deu 116 unicos em vez de 240 nesta rede). Ele nao e a peneira mais barata
aqui, e a que nao depende de adivinhar qual indice peneira melhor nesta rede em
particular, coisa que so da para saber tendo o gabarito.

Sobre o FD e empates. O FD e um maximo de maximos e por isso assume poucos
valores distintos: 2462 para as 59.049 combinacoes, com o maior bloco empatado
somando 542. O otimo da varredura tem apenas 26 combinacoes estritamente
melhores nesse indice, mas empata com outras 542, e sem desempate cai na
posicao 568 do ordenamento. Por isso ordenar_por_indice() desempata todos os
indices pelo SSE da ativa, que e continuo: com o desempate o FD leva o otimo da
posicao 568 para a 195. Um indice grosseiro sem desempate nao peneira, sorteia.

Duas armadilhas que este script trata explicitamente, ambas encontradas ao
medir nesta rede:

  - ESTADO HERDADO. Reaproveitar a instancia DSS entre candidatas carrega o
    estado dos taps de uma para a outra e corrompe a comparacao: EFEFFFFEFF
    marcou 1149,7 kWh numa instancia reusada contra 1392,6 kWh recompilando,
    17% abaixo do valor correto. Por isso TODA avaliacao recompila o circuito,
    e o 'Set number=1' e exigido do arquivo em vez de corrigido em memoria: o
    Solve final do proprio master ja define o estado de partida de tudo.
  - NAO CONVERGENCIA. Configuracoes extremas estouram o limite de iteracoes no
    patamar de ponta. O OpenDSS devolve a solucao mesmo assim, e ela exibe
    valores ARTIFICIALMENTE BAIXOS nas DUAS metricas: no benchmark, as 3
    candidatas que nao convergiram marcaram 60,7 a 70,4 kWh de parcela contra
    98,0 a 117,0 kWh das validas, e venceriam a ordenacao se fossem aceitas. O
    script as marca e as deixa fora da recomendacao.

Uma limitacao que a etapa 1 nao enxerga: na rede de exemplo o RAMAL-188 esta no
secundario do regulador REG-58180294BG, entao mudar a fase dele altera o
carregamento por fase do regulador. So a etapa 2 captura isso.
"""

import csv
import json
import re
import time
from math import isfinite, sqrt
from multiprocessing import Pool, cpu_count
from pathlib import Path

import numpy as np
import py_dss_interface


# ---- Constantes ----

NUM_PATAMARES = 4
DURACAO_PATAMAR_H = 1.0
EPSILON = 1e-12

# Operador de rotacao de 120 graus usado nas componentes simetricas.
OPERADOR_A = complex(-0.5, sqrt(3) / 2)

# Exigido do arquivo do circuito, nao corrigido em memoria: ver
# verificar_configuracao_solucao.
NUMBER_EXIGIDO = 1

FASE_PARA_NUMERO = {"D": 1, "E": 2, "F": 3}
NUMERO_PARA_FASE = {1: "D", 2: "E", 3: "F"}

# Cada modo simples e um indice de desequilibrio calculado sobre a potencia de
# cabeceira. TODOS nao e um indice: e a uniao dos top-K dos quatro, usada quando
# nao se quer apostar em qual deles peneira melhor nesta rede.
MODOS_SIMPLES = ("P", "S", "PQ", "FD")
MODO_UNIAO = "TODOS"
MODOS = MODOS_SIMPLES + (MODO_UNIAO,)
MODO_PADRAO = "P"

CHAVE_DO_MODO = {
    "P": "sse_p",
    "S": "sse_s",
    "PQ": "sse_pq",
    "FD": "fd_max_pct",
}

# Peso da reativa no modo PQ. P e Q estao na mesma unidade, entao 1.0 trata as
# duas com a mesma importancia.
PESO_REATIVA = 1.0

K_PADRAO = 50

# So para anunciar uma previsao antes de comecar; o tempo real e medido
# durante a execucao. Vem de 1,2 s por candidata medidos nesta rede.
SEGUNDOS_POR_CANDIDATA = 1.2

TOLERANCIA_JSON_DSS = 1e-4
TOLERANCIA_PONTO_MEDICAO = 1e-6

# A busca guarda so os indices de cada combinacao; as potencias por fase sao
# calculadas e descartadas bloco a bloco, o que mantem a memoria proporcional
# a 3^N e nao a 3^N vezes patamares vezes fases.
TAMANHO_BLOCO = 200_000
COMBINACOES_PARA_CONFIRMAR = 1_500_000
COMBINACOES_MAXIMAS = 3 ** 15

LINHAS_CSV_COMPLETO = 1_000_000
LINHAS_CSV_REDUZIDO = 1_000

NOME_CSV_RANKING = "resultados_balanco_fases.csv"
NOME_CSV_TOP = "resultados_balanco_fases_top.csv"
# Nome proprio para o benchmark, senao ele sobrescreveria as candidatas da
# ultima analise de producao gravadas na mesma pasta.
NOME_CSV_TOP_BENCHMARK = "resultados_balanco_fases_benchmark.csv"


# ---- Leitura dos arquivos do circuito ----

def carregar_ramais(caminho_ramais, ids_ramais):
    """Le os ramais e devolve os dados na mesma ordem de ids_ramais."""
    ids_desejados = set(ids_ramais)
    ramais_por_id = {}

    with open(caminho_ramais, "r", encoding="utf-8") as arquivo:
        for numero_linha, linha in enumerate(arquivo, start=1):
            match_nome = re.search(r"\bNew\s+Load\.([^\s]+)", linha, flags=re.IGNORECASE)
            match_bus = re.search(r"\bbus1=([^\s]+)", linha, flags=re.IGNORECASE)

            if not match_nome or not match_bus:
                continue

            nome_carga = match_nome.group(1)
            match_id = re.match(r"RAMAL-(\d+)-", nome_carga, flags=re.IGNORECASE)

            if not match_id:
                continue

            id_ramal = int(match_id.group(1))
            if id_ramal not in ids_desejados:
                continue

            if id_ramal in ramais_por_id:
                raise ValueError(
                    f"O ramal {id_ramal} aparece mais de uma vez em {caminho_ramais}."
                )

            bus = match_bus.group(1)
            partes_bus = bus.split(".")
            if len(partes_bus) < 2 or not partes_bus[1].isdigit():
                raise ValueError(
                    f"Nao foi possivel identificar a fase do ramal {id_ramal} "
                    f"na linha {numero_linha}: {bus}"
                )

            ramais_por_id[id_ramal] = {
                "id": id_ramal,
                "nome": nome_carga,
                "barra": partes_bus[0],
                "fase_original": int(partes_bus[1]),
            }

    ids_ausentes = [id_ramal for id_ramal in ids_ramais if id_ramal not in ramais_por_id]
    if ids_ausentes:
        raise ValueError(f"Ramais nao encontrados no arquivo DSS: {ids_ausentes}")

    # Aqui a ordem do arquivo deixa de importar.
    return [ramais_por_id[id_ramal] for id_ramal in ids_ramais]


def carregar_perfis_ramais(caminho_ramais, ids_ramais):
    """Le mult e qmult dos LoadShape PERFIL-RAMAL-<id>-* do arquivo de ramais.

    Serve para conferir o JSON contra o circuito: como as cargas equivalentes
    tem kW=1 e kvar=1, o mult do LoadShape e a propria potencia do ramal em
    cada patamar.
    """
    ids_desejados = set(ids_ramais)
    perfis = {}

    padrao_nome = re.compile(r"\bNew\s+LoadShape\.PERFIL-RAMAL-(\d+)-", re.IGNORECASE)
    # '\bmult' nao casa dentro de 'qmult': q e m sao ambos caracteres de
    # palavra, entao nao ha fronteira entre eles.
    padrao_mult = re.compile(r"\bmult\s*=\s*\[([^\]]*)\]", re.IGNORECASE)
    padrao_qmult = re.compile(r"\bqmult\s*=\s*\[([^\]]*)\]", re.IGNORECASE)

    with open(caminho_ramais, "r", encoding="utf-8") as arquivo:
        for linha in arquivo:
            match_nome = padrao_nome.search(linha)
            if not match_nome:
                continue

            id_ramal = int(match_nome.group(1))
            if id_ramal not in ids_desejados:
                continue

            match_mult = padrao_mult.search(linha)
            match_qmult = padrao_qmult.search(linha)
            if not match_mult or not match_qmult:
                continue

            perfis[id_ramal] = (
                np.array([float(valor) for valor in match_mult.group(1).split()]),
                np.array([float(valor) for valor in match_qmult.group(1).split()]),
            )

    return perfis


def carregar_nomes_chaves(caminho_chaves):
    """Le os elementos Line declarados como Switch no arquivo de chaves."""
    nomes_chaves = set()

    with open(caminho_chaves, "r", encoding="utf-8") as arquivo:
        for linha in arquivo:
            match_nome = re.search(
                r"\bNew\s+Line\.([^\s]+)",
                linha,
                flags=re.IGNORECASE,
            )
            match_switch = re.search(
                r"\bSwitch\s*=\s*(Yes|True|Y)\b",
                linha,
                flags=re.IGNORECASE,
            )

            if match_nome and match_switch:
                nomes_chaves.add(match_nome.group(1).lower())

    return nomes_chaves


def obter_linhas_trifasicas(dss, caminho_chaves):
    """Linhas trifasicas que nao sao chaves, com os dados para as perdas.

    A topologia nao muda entre as avaliacoes, entao esta lista e montada uma
    unica vez e reaproveitada em todas as candidatas.
    """
    nomes_chaves = carregar_nomes_chaves(caminho_chaves)
    linhas_trifasicas = []

    for nome_linha in dss.lines.names:
        dss.lines.name = nome_linha

        if dss.lines.phases != 3:
            continue

        if nome_linha.lower() in nomes_chaves:
            continue

        linhas_trifasicas.append(
            (
                nome_linha,
                dss.lines.r0,
                dss.lines.r1,
                dss.lines.length,
            )
        )

    return linhas_trifasicas


def obter_barras_trifasicas(dss):
    barras_trifasicas = []

    for nome_barra in dss.circuit.buses_names:
        dss.circuit.set_active_bus(nome_barra)

        if {1, 2, 3}.issubset(set(dss.bus.nodes)):
            barras_trifasicas.append(nome_barra)

    return barras_trifasicas


# ---- Circuito ----

def compilar_circuito(caminho_dss):
    """Compila o circuito do zero e exige um unico passo de tempo por Solve.

    Toda avaliacao deste script passa por aqui: nenhuma instancia DSS e
    reaproveitada entre configuracoes, porque o estado dos taps de regulador
    vaza de uma solucao para a proxima e corrompe a comparacao.
    """
    dss = py_dss_interface.DSS()
    dss.text(f'compile "{caminho_dss}"')

    # Um compile que falha nao levanta excecao: devolve um circuito vazio, e
    # todas as leituras seguintes viram zeros silenciosos.
    if dss.circuit.num_buses < 2 or dss.lines.count < 2:
        raise RuntimeError(
            f"A compilacao de {caminho_dss} nao produziu um circuito valido "
            f"({dss.circuit.num_buses} barras, {dss.lines.count} linhas)."
        )

    verificar_configuracao_solucao(dss, caminho_dss)
    return dss


def verificar_configuracao_solucao(dss, caminho_dss):
    """Aborta se cada Solve avancar mais de um passo de tempo.

    Nao adianta corrigir com 'set number=1' depois do compile. O Solve final do
    proprio master ja rodou com o valor do arquivo, e o estado de taps que ele
    deixa e o ponto de partida de todas as avaliacoes seguintes. Medindo na rede
    de exemplo, esse ponto de partida decide ate se uma configuracao converge:
    com number=4 no master, FFFFFFFFFF estoura o limite de iteracoes no patamar
    de ponta; com number=1, nao. Duas rodadas com masters diferentes nao sao
    comparaveis, entao o arquivo tem que estar certo.
    """
    numero = dss.solution.number

    if numero == NUMBER_EXIGIDO:
        return

    raise SystemExit(
        "\n"
        "ERRO: configuracao de solucao invalida para esta analise.\n"
        "\n"
        f"  O circuito esta com 'Set number={numero}', ou seja, cada Solve\n"
        f"  avanca {numero} passos de tempo. As {NUM_PATAMARES} leituras de\n"
        f"  potencia cairiam todas no mesmo ponto do LoadShape, e o resultado\n"
        f"  mediria {NUM_PATAMARES}x um patamar so, em vez dos {NUM_PATAMARES}\n"
        "  patamares.\n"
        "\n"
        f"  Corrija para 'Set number={NUMBER_EXIGIDO}' em:\n"
        f"    {caminho_dss}\n"
        "\n"
        "  Depois rode este script de novo.\n"
    )


def localizar_saida_alimentador(dss):
    """Escolhe onde a potencia de saida do alimentador e medida.

    Preferencia para o disjuntor de cabeceira (Line.DJ_*) que sai da barra da
    fonte; na falta dele, a unica linha trifasica que sai dessa barra; em
    ultimo caso a propria Vsource, cuja potencia entra com sinal invertido.
    Devolve (nome do elemento, sinal).
    """
    dss.circuit.set_active_element("Vsource.source")
    barra_fonte = dss.cktelement.bus_names[0].split(".")[0].lower()

    candidatos = []
    for nome_linha in dss.lines.names:
        dss.lines.name = nome_linha

        if dss.lines.phases != 3:
            continue

        if dss.lines.bus1.split(".")[0].lower() == barra_fonte:
            candidatos.append(nome_linha)

    disjuntores = [nome for nome in candidatos if nome.lower().startswith("dj_")]
    escolhidos = disjuntores or candidatos

    if len(escolhidos) == 1:
        return f"Line.{escolhidos[0]}", 1.0

    return "Vsource.source", -1.0


def medir_cabeceira(dss, elemento, sinal):
    """Devolve (P, Q) por fase D/E/F no terminal 1 do elemento, em kW e kvar."""
    dss.circuit.set_active_element(elemento)

    ordem = list(dss.cktelement.node_order[:3])
    if ordem != [1, 2, 3]:
        raise RuntimeError(
            f"O elemento {elemento} expoe os nos na ordem {ordem} e nao 1,2,3; "
            "a leitura por fase sairia trocada."
        )

    potencias = dss.cktelement.powers[:6]
    ativa = sinal * np.array([potencias[0], potencias[2], potencias[4]])
    reativa = sinal * np.array([potencias[1], potencias[3], potencias[5]])
    return ativa, reativa


def conferir_ponto_de_medicao(dss, elemento, sinal):
    """Confere o disjuntor de cabeceira contra a propria fonte."""
    if elemento == "Vsource.source":
        return

    do_elemento, _ = medir_cabeceira(dss, elemento, sinal)
    da_fonte, _ = medir_cabeceira(dss, "Vsource.source", -1.0)

    referencia = max(float(np.abs(da_fonte).max()), EPSILON)
    diferenca = float(np.abs(do_elemento - da_fonte).max()) / referencia

    if diferenca > TOLERANCIA_PONTO_MEDICAO:
        raise RuntimeError(
            f"A potencia medida em {elemento} difere da fonte em "
            f"{diferenca:.3e} (relativo). O elemento escolhido nao representa "
            "a saida do alimentador."
        )


# ---- Caso base: circuito sem os ramais de interesse ----

def potencias_de_base(caminho_dss, ramais, elemento, sinal):
    """Potencia por fase na cabeceira com os ramais de interesse desativados.

    Desativa a carga equivalente de cada ramal, e nao a chave de inicio: na
    rede simplificada o ramal inteiro ja e essa carga unica, e ela fica a
    montante da chave.
    """
    dss = compilar_circuito(caminho_dss)
    conferir_ponto_de_medicao(dss, elemento, sinal)

    for ramal in ramais:
        dss.text(f"Edit Load.{ramal['nome']} enabled=no")

    dss.text("set time=(0,0)")

    base_p = np.zeros((NUM_PATAMARES, 3))
    base_q = np.zeros((NUM_PATAMARES, 3))

    for patamar in range(NUM_PATAMARES):
        dss.solution.solve()

        if not dss.solution.converged:
            raise RuntimeError(
                f"O circuito sem os ramais nao convergiu no patamar {patamar}."
            )

        base_p[patamar], base_q[patamar] = medir_cabeceira(dss, elemento, sinal)

    return base_p, base_q


def montar_matrizes_ramais(dados, ramais, perfis):
    """Matrizes (N, NUM_PATAMARES) com a potencia propria de cada ramal.

    Confere de passagem que o JSON e o DSS descrevem a mesma rede. Se
    divergirem, o resultado seria sobre uma rede que nao existe.
    """
    ramais_p = np.zeros((len(ramais), NUM_PATAMARES))
    ramais_q = np.zeros((len(ramais), NUM_PATAMARES))

    for posicao, ramal in enumerate(ramais):
        registro = dados.get(f"RAMAL-{ramal['id']}")

        if registro is None:
            raise ValueError(f"O JSON nao tem o registro RAMAL-{ramal['id']}.")

        if registro["barra_inicio"].lower() != ramal["barra"].lower():
            raise ValueError(
                f"Ramal {ramal['id']}: barra {registro['barra_inicio']} no JSON "
                f"e {ramal['barra']} no DSS."
            )

        if FASE_PARA_NUMERO[registro["fase"]] != ramal["fase_original"]:
            raise ValueError(
                f"Ramal {ramal['id']}: fase {registro['fase']} no JSON e "
                f"{NUMERO_PARA_FASE[ramal['fase_original']]} no DSS."
            )

        ramais_p[posicao] = [registro[f"P{k}"] for k in range(NUM_PATAMARES)]
        ramais_q[posicao] = [registro[f"Q{k}"] for k in range(NUM_PATAMARES)]

        perfil = perfis.get(ramal["id"])
        if perfil is None:
            raise ValueError(
                f"Ramal {ramal['id']}: LoadShape PERFIL-RAMAL-{ramal['id']}-* "
                "nao encontrado no arquivo de ramais."
            )

        for grandeza, do_json, do_dss in (
            ("P", ramais_p[posicao], perfil[0]),
            ("Q", ramais_q[posicao], perfil[1]),
        ):
            escala = max(float(np.abs(do_dss).max()), EPSILON)
            erro = float(np.abs(do_json - do_dss).max()) / escala

            if erro > TOLERANCIA_JSON_DSS:
                raise ValueError(
                    f"Ramal {ramal['id']}: {grandeza}0..{grandeza}3 do JSON "
                    f"diferem do LoadShape do DSS em {erro:.3e} (relativo). "
                    "JSON e circuito estao dessincronizados."
                )

    return ramais_p, ramais_q


# ---- Busca analitica ----

def digitos_das_combinacoes(numeros, numero_ramais):
    """Converte numeros de combinacao em fases 0..2, uma coluna por ramal.

    A posicao j do vetor de ramais tem peso 3^(N-1-j), a mesma ordem que
    itertools.product((0, 1, 2), repeat=N) produz.
    """
    pesos = 3 ** np.arange(numero_ramais - 1, -1, -1, dtype=np.int64)
    return ((np.asarray(numeros, dtype=np.int64)[:, None] // pesos) % 3).astype(np.int8)


def numero_da_combinacao(fases):
    """Caminho inverso de digitos_das_combinacoes, para uma unica combinacao."""
    numero_ramais = len(fases)
    return int(
        sum(
            int(fase) * 3 ** (numero_ramais - 1 - posicao)
            for posicao, fase in enumerate(fases)
        )
    )


def avaliar_bloco(digitos, base_p, base_q, ramais_p, ramais_q):
    """Indices de desequilibrio de um bloco de combinacoes.

    Para cada fase, a matriz booleana de quem esta nela multiplicada pelas
    potencias dos ramais da, de uma vez, a parcela que aquela fase recebe em
    todos os patamares e em todas as combinacoes do bloco.
    """
    tamanho = digitos.shape[0]
    ativa = np.empty((tamanho, NUM_PATAMARES, 3))
    reativa = np.empty((tamanho, NUM_PATAMARES, 3))

    for fase in range(3):
        pertence = (digitos == fase).astype(np.float64)
        ativa[:, :, fase] = base_p[:, fase] + pertence @ ramais_p
        reativa[:, :, fase] = base_q[:, fase] + pertence @ ramais_q

    aparente = np.hypot(ativa, reativa)

    media_ativa = ativa.mean(axis=2, keepdims=True)
    media_reativa = reativa.mean(axis=2, keepdims=True)
    media_aparente = aparente.mean(axis=2, keepdims=True)

    return {
        "sse_p": ((ativa - media_ativa) ** 2).sum(axis=(1, 2)),
        "sse_q": ((reativa - media_reativa) ** 2).sum(axis=(1, 2)),
        "sse_s": ((aparente - media_aparente) ** 2).sum(axis=(1, 2)),
        "fd_max_pct": 100 * (
            np.abs(ativa - media_ativa).max(axis=2)
            / np.maximum(np.abs(media_ativa[:, :, 0]), EPSILON)
        ).max(axis=1),
    }


def objetivo_do_modo(indices, modo):
    """Indice que ordena as combinacoes no modo dado.

    No modo uniao nao existe um objetivo unico; devolve o do modo padrao, que
    serve so para os relatorios que precisam de um numero por combinacao.
    """
    if modo == MODO_UNIAO:
        modo = MODO_PADRAO

    return indices[CHAVE_DO_MODO[modo]]


def buscar(base_p, base_q, ramais_p, ramais_q, modo):
    """Percorre as 3^N combinacoes em blocos e devolve os indices de cada uma.

    So os indices ficam na memoria: as potencias por fase de um bloco sao
    reduzidas e descartadas antes do bloco seguinte.
    """
    numero_ramais = ramais_p.shape[0]
    total = 3 ** numero_ramais

    indices = {
        chave: np.empty(total)
        for chave in ("sse_p", "sse_q", "sse_s", "fd_max_pct")
    }

    for inicio in range(0, total, TAMANHO_BLOCO):
        fim = min(inicio + TAMANHO_BLOCO, total)
        digitos = digitos_das_combinacoes(np.arange(inicio, fim), numero_ramais)
        bloco = avaliar_bloco(digitos, base_p, base_q, ramais_p, ramais_q)

        for chave, valores in bloco.items():
            indices[chave][inicio:fim] = valores

    # O indice do modo PQ e combinacao dos outros dois, entao sai de uma vez no
    # fim em vez de bloco a bloco.
    indices["sse_pq"] = indices["sse_p"] + PESO_REATIVA * indices["sse_q"]
    indices["objetivo"] = objetivo_do_modo(indices, modo)
    return indices


def ordenar_por_indice(indices, modo):
    """Ordem das combinacoes segundo um indice, sempre com desempate.

    Empate nao e detalhe aqui. O FD e um maximo de maximos e assume poucos
    valores distintos: nesta rede, 2462 para 59.049 combinacoes, com o maior
    bloco empatado somando 542. Sem desempate, o top-K do FD sai praticamente
    sorteado de dentro de um empate, e uma boa combinacao pode cair em qualquer
    posicao dele. Medindo: o otimo da varredura tem so 26 combinacoes
    estritamente melhores no FD, mas empata com outras 542 e acaba na posicao
    568; desempatando pelo SSE da ativa ele sobe para a 195.

    O SSE da ativa serve de desempate para todos os indices porque e continuo e
    quase nunca empata. Para o proprio modo P a regra nao muda nada.
    """
    if modo == MODO_UNIAO:
        modo = MODO_PADRAO

    return np.lexsort((indices["sse_p"], indices[CHAVE_DO_MODO[modo]]))


def selecionar_candidatas(indices, modo, quantidade_k, obrigatorias=()):
    """Escolhe quais combinacoes merecem um fluxo de potencia.

    Num modo simples e o top-K daquele indice. No modo uniao e o top-K de cada
    um dos quatro indices, intercalado por posicao: 1o de cada indice, depois 2o
    de cada, e assim por diante, descartando repetidas. A intercalacao poe as
    mais promissoras no comeco da fila, o que importa se a execucao for
    interrompida no meio.

    No pior caso a uniao tem 4*K candidatas, mas os indices concordam bastante e
    na pratica sobram bem menos: medindo nesta rede, K=60 por indice deu 116
    unicas em vez de 240.

    Cada candidata sai anotada com o indice que a colocou na peneira e com a
    posicao que ela ocupava nele. E o que mostra, depois, se cada indice pagou o
    lugar que ocupou.

    'obrigatorias' sao combinacoes que o chamador quer avaliar de qualquer
    jeito, como a configuracao original. Elas vao para a frente da fila, mas
    mantem a anotacao real se tambem tiverem entrado pela peneira.
    """
    modos = MODOS_SIMPLES if modo == MODO_UNIAO else (modo,)

    ordens = {
        nome: ordenar_por_indice(indices, nome)[:quantidade_k] for nome in modos
    }

    peneira = {}
    for posicao in range(quantidade_k):
        for nome in modos:
            ordem = ordens[nome]

            if posicao < len(ordem):
                peneira.setdefault(int(ordem[posicao]), (nome, posicao + 1))

    fila = []
    vistos = set()

    for numero in list(obrigatorias) + list(peneira):
        numero = int(numero)

        if numero in vistos:
            continue

        vistos.add(numero)
        origem, posicao = peneira.get(numero, ("fora da peneira", 0))
        fila.append(
            {
                "numero": numero,
                "indice_de_origem": origem,
                "posicao_no_indice": posicao,
            }
        )

    return fila


def descrever_peneira(modo, quantidade_k, candidatas):
    """Frase que explica de onde saiu a lista de candidatas."""
    if modo != MODO_UNIAO:
        return (
            f"Validando {len(candidatas)} configuracoes com fluxo de potencia real "
            f"(top-{quantidade_k} do indice {modo})..."
        )

    contagem = {}
    for candidata in candidatas:
        origem = candidata["indice_de_origem"]
        contagem[origem] = contagem.get(origem, 0) + 1

    detalhe = ", ".join(
        f"{contagem[nome]} de {nome}" for nome in MODOS_SIMPLES if nome in contagem
    )
    pior_caso = len(MODOS_SIMPLES) * quantidade_k

    return (
        f"Validando {len(candidatas)} configuracoes com fluxo de potencia real.\n"
        f"  Uniao dos top-{quantidade_k} de {len(MODOS_SIMPLES)} indices: no pior caso "
        f"{pior_caso}, sobraram {len(candidatas)} apos remover as repetidas.\n"
        f"  Origem de cada uma: {detalhe}."
    )


def confirmar_tamanho(numero_ramais):
    """Barra buscas que nao cabem na memoria, antes de tentar aloca-las."""
    total = 3 ** numero_ramais

    if total > COMBINACOES_MAXIMAS:
        raise SystemExit(
            f"\nERRO: {numero_ramais} ramais dao {total:,} combinacoes, acima do "
            f"limite de {COMBINACOES_MAXIMAS:,}.\n"
            "Reduza 'ramais_interesse' no JSON ou use uma busca heuristica.\n"
            .replace(",", ".")
        )

    if total > COMBINACOES_PARA_CONFIRMAR:
        # Cinco vetores de float64 por combinacao: quatro indices e o objetivo.
        memoria_gb = total * 5 * 8 / 1024 ** 3
        print(
            f"\nAVISO: {total:,} combinacoes ocupam cerca de {memoria_gb:.1f} GB "
            "so nos indices.".replace(",", ".")
        )

        resposta = input("Continuar? [s/N] ").strip().lower()
        if resposta not in ("s", "sim", "y", "yes"):
            raise SystemExit("\nBusca cancelada.")

    return total


# ---- Avaliacao com fluxo de potencia real ----

def parcela_de_desequilibrio(dss, linhas_trifasicas):
    """Perdas do patamar atual associadas ao desequilibrio, em kWh.

    Mesma formula do script de forca bruta, 3 * L * (R0*|I0|^2 + R1*|I2|^2),
    somada nas linhas trifasicas. Mantida para que os numeros deste script
    sejam diretamente comparaveis com resultados_forca_bruta.csv.
    """
    total_watts = 0.0

    for nome_linha, r0, r1, comprimento in linhas_trifasicas:
        dss.lines.name = nome_linha
        correntes = dss.cktelement.currents

        corrente_a = complex(correntes[0], correntes[1])
        corrente_b = complex(correntes[2], correntes[3])
        corrente_c = complex(correntes[4], correntes[5])

        corrente_0 = (corrente_a + corrente_b + corrente_c) / 3
        corrente_2 = (
            corrente_a
            + OPERADOR_A**2 * corrente_b
            + OPERADOR_A * corrente_c
        ) / 3

        total_watts += 3 * comprimento * (
            r0 * abs(corrente_0) ** 2
            + r1 * abs(corrente_2) ** 2
        )

    return total_watts * DURACAO_PATAMAR_H / 1_000


def coletar_tensoes(dss, barras_trifasicas, valores_vuf, tensoes_pu):
    for nome_barra in barras_trifasicas:
        dss.circuit.set_active_bus(nome_barra)

        tensoes_sequencia = dss.bus.cplx_sequence_voltages
        if len(tensoes_sequencia) >= 6:
            tensao_1 = complex(tensoes_sequencia[2], tensoes_sequencia[3])
            tensao_2 = complex(tensoes_sequencia[4], tensoes_sequencia[5])

            if abs(tensao_1) > EPSILON:
                valores_vuf.append(abs(tensao_2) / abs(tensao_1))

        nos = dss.bus.nodes
        tensoes_mag_ang_pu = dss.bus.vmag_angle_pu

        for indice, no in enumerate(nos):
            if no not in (1, 2, 3):
                continue

            posicao_magnitude = 2 * indice
            if posicao_magnitude >= len(tensoes_mag_ang_pu):
                continue

            tensao_pu = tensoes_mag_ang_pu[posicao_magnitude]
            if isfinite(tensao_pu) and tensao_pu > 0:
                tensoes_pu.append(tensao_pu)


def avaliar_com_fluxo(
    caminho_dss,
    ramais,
    fases,
    elemento,
    sinal,
    linhas_trifasicas=(),
    barras_trifasicas=(),
):
    """Roda os patamares com as fases dadas, num circuito recem-compilado.

    Recompila em vez de reusar a instancia. Reusar carrega o estado dos taps de
    uma candidata para a seguinte: medindo nesta rede, EFEFFFFEFF marcou
    1149,7 kWh numa instancia reusada contra 1392,6 kWh recompilando, 17%
    abaixo do valor correto. Como as candidatas se separam por poucos por
    cento, esse erro inverteria a ordenacao inteira.
    """
    dss = compilar_circuito(caminho_dss)

    for ramal, fase in zip(ramais, fases):
        dss.text(f"Edit Load.{ramal['nome']} bus1={ramal['barra']}.{int(fase) + 1}")

    dss.text("set time=(0,0)")

    ativa = np.zeros((NUM_PATAMARES, 3))
    reativa = np.zeros((NUM_PATAMARES, 3))
    energia_perdida_kwh = 0.0
    parcela_desequilibrio_kwh = 0.0
    valores_vuf = []
    tensoes_pu = []

    convergiu = True

    for patamar in range(NUM_PATAMARES):
        dss.solution.solve()

        # Uma candidata que nao converge nao para a validacao das outras: os
        # numeros dela sao registrados, marcados como invalidos e deixados de
        # fora da recomendacao. Configuracoes extremas, com quase toda a carga
        # numa fase so, e que costumam cair aqui.
        convergiu = convergiu and bool(dss.solution.converged)

        ativa[patamar], reativa[patamar] = medir_cabeceira(dss, elemento, sinal)
        energia_perdida_kwh += dss.circuit.losses[0] * DURACAO_PATAMAR_H / 1_000

        if linhas_trifasicas:
            parcela_desequilibrio_kwh += parcela_de_desequilibrio(dss, linhas_trifasicas)

        if barras_trifasicas:
            coletar_tensoes(dss, barras_trifasicas, valores_vuf, tensoes_pu)

    media = ativa.mean(axis=1, keepdims=True)

    relatorio = {
        "fases": tuple(int(fase) for fase in fases),
        "convergiu": convergiu,
        "ativa": ativa,
        "reativa": reativa,
        "energia_perdida_kwh": energia_perdida_kwh,
        "parcela_desequilibrio_kwh": parcela_desequilibrio_kwh,
        "sse_p": float(((ativa - media) ** 2).sum()),
        "fd_max_pct": float(
            100 * (np.abs(ativa - media).max(axis=1) / np.abs(media[:, 0])).max()
        ),
    }

    if valores_vuf:
        relatorio["vuf_medio"] = sum(valores_vuf) / len(valores_vuf)
        relatorio["vuf_maximo"] = max(valores_vuf)

    if tensoes_pu:
        relatorio["tensao_minima_pu"] = min(tensoes_pu)
        relatorio["tensao_maxima_pu"] = max(tensoes_pu)

    return relatorio


# ---- Relatorio ----

def fases_como_texto(fases):
    return "".join(NUMERO_PARA_FASE[int(fase) + 1] for fase in fases)


def formatar_duracao(segundos):
    if segundos < 90:
        return f"{segundos:.1f} s"

    minutos = segundos / 60
    if minutos < 90:
        return f"{minutos:.1f} min"

    return f"{minutos / 60:.1f} h"


def reducao_percentual(valor_original, valor_otimizado):
    if abs(valor_original) <= EPSILON:
        return 0.0

    return 100 * (valor_original - valor_otimizado) / valor_original


def formatar_milhar(numero):
    return f"{numero:,}".replace(",", ".")


def posicao_no_ranking(valores, numero):
    """Posicao de uma combinacao, contando quantas sao estritamente melhores."""
    return int((valores < valores[numero]).sum()) + 1


def imprimir_potencias(titulo, ativa, reativa=None):
    print(f"\n{titulo}")
    print(f"  {'patamar':<9} {'PD':>10} {'PE':>10} {'PF':>10} {'desvio max':>11}")

    for patamar in range(NUM_PATAMARES):
        media = ativa[patamar].mean()
        desvio = 100 * np.abs(ativa[patamar] - media).max() / max(abs(media), EPSILON)
        print(
            f"  NPAT{patamar:<5} "
            f"{ativa[patamar][0]:>10.1f} {ativa[patamar][1]:>10.1f} "
            f"{ativa[patamar][2]:>10.1f} {desvio:>10.1f}%"
        )

    if reativa is None:
        return

    print(f"  {'patamar':<9} {'QD':>10} {'QE':>10} {'QF':>10}")
    for patamar in range(NUM_PATAMARES):
        print(
            f"  NPAT{patamar:<5} "
            f"{reativa[patamar][0]:>10.1f} {reativa[patamar][1]:>10.1f} "
            f"{reativa[patamar][2]:>10.1f}"
        )


def salvar_ranking(caminho_csv, ramais, ordem, indices):
    """Grava as combinacoes avaliadas, da melhor para a pior."""
    digitos = digitos_das_combinacoes(ordem, len(ramais))

    cabecalho = [f"fase_ramal_{ramal['id']}" for ramal in ramais]
    cabecalho += ["objetivo", "sse_p", "sse_q", "sse_s", "fd_max_pct"]

    with open(caminho_csv, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(cabecalho)

        for posicao, numero in enumerate(ordem):
            escritor.writerow(
                [NUMERO_PARA_FASE[int(fase) + 1] for fase in digitos[posicao]]
                + [
                    f"{indices['objetivo'][numero]:.6f}",
                    f"{indices['sse_p'][numero]:.6f}",
                    f"{indices['sse_q'][numero]:.6f}",
                    f"{indices['sse_s'][numero]:.6f}",
                    f"{indices['fd_max_pct'][numero]:.6f}",
                ]
            )


def salvar_top(caminho_csv, ramais, avaliadas):
    """Grava as candidatas que passaram pelo fluxo de potencia real."""
    cabecalho = ["rotulo", "indice_de_origem", "posicao_no_indice", "posicao_analitica"]
    cabecalho += [f"fase_ramal_{ramal['id']}" for ramal in ramais]
    cabecalho += [
        "objetivo_estimado",
        "sse_p_estimado",
        "fd_max_pct_estimado",
        "energia_perdida_kwh",
        "parcela_desequilibrio_kwh",
        "sse_p_real",
        "fd_max_pct_real",
        "convergiu",
    ]
    cabecalho += [
        f"{grandeza}_npat{patamar}_{fase}"
        for patamar in range(NUM_PATAMARES)
        for grandeza in ("p", "q")
        for fase in ("D", "E", "F")
    ]

    with open(caminho_csv, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(cabecalho)

        for item in avaliadas:
            relatorio = item["relatorio"]

            linha = [
                item["rotulo"],
                item["indice_de_origem"],
                item["posicao_no_indice"],
                item["posicao_analitica"],
            ]
            linha += [NUMERO_PARA_FASE[fase + 1] for fase in relatorio["fases"]]
            linha += [
                f"{item['objetivo_estimado']:.6f}",
                f"{item['sse_p_estimado']:.6f}",
                f"{item['fd_max_pct_estimado']:.6f}",
                f"{relatorio['energia_perdida_kwh']:.6f}",
                f"{relatorio['parcela_desequilibrio_kwh']:.6f}",
                f"{relatorio['sse_p']:.6f}",
                f"{relatorio['fd_max_pct']:.6f}",
                "sim" if relatorio["convergiu"] else "nao",
            ]

            for patamar in range(NUM_PATAMARES):
                for grandeza in ("ativa", "reativa"):
                    linha += [f"{valor:.4f}" for valor in relatorio[grandeza][patamar]]

            escritor.writerow(linha)


# ---- Etapas de alto nivel ----

def preparar(caminho_pasta, dados, ids_ramais):
    """Reune tudo que depende do circuito e do JSON, antes da busca."""
    # Criar a instancia DSS muda o diretorio de trabalho do processo, entao
    # todo caminho precisa virar absoluto antes do primeiro compile: com um
    # caminho relativo o OpenDSS compila um circuito vazio e nao acusa erro.
    caminho_pasta = Path(caminho_pasta).resolve()

    arquivos_master = list(caminho_pasta.glob("*_Master.dss"))

    if not arquivos_master:
        raise FileNotFoundError(
            f"Nenhum arquivo *_Master.dss encontrado em {caminho_pasta}"
        )

    if len(arquivos_master) > 1:
        raise RuntimeError(
            f"Mais de um arquivo *_Master.dss encontrado: {arquivos_master}"
        )

    caminho_dss = arquivos_master[0]
    caminho_ramais = caminho_pasta / "ramalmonofasico.dss"
    caminho_chaves = caminho_pasta / "chaves.dss"

    ramais = carregar_ramais(caminho_ramais, ids_ramais)
    perfis = carregar_perfis_ramais(caminho_ramais, ids_ramais)
    ramais_p, ramais_q = montar_matrizes_ramais(dados, ramais, perfis)

    dss = compilar_circuito(caminho_dss)

    return {
        "caminho_dss": caminho_dss,
        "ramais": ramais,
        "ramais_p": ramais_p,
        "ramais_q": ramais_q,
        "elemento_e_sinal": localizar_saida_alimentador(dss),
        "linhas_trifasicas": obter_linhas_trifasicas(dss, caminho_chaves),
    }


def processos_padrao():
    """Quantos processos usar na revalidacao, se o usuario nao disser outro.

    Metade das CPUs logicas, e nao 'todas menos duas': cada processo segura um
    OpenDSS por minutos, e ocupar quase todos os nucleos deixa a maquina
    inutilizavel enquanto a revalidacao roda. Le cpu_count() na hora, entao se
    adapta a qualquer maquina; o max(1, ...) cobre o caso de uma ou duas CPUs,
    em que o script cai no caminho serial e nem chega a criar um Pool.
    """
    return max(1, cpu_count() // 2)


def relatorio_de_falha(fases, erro):
    """Marcador para uma candidata que nem chegou a produzir numeros.

    Um erro numa candidata nao pode derrubar o lote inteiro: ela e registrada
    como invalida, com os valores em infinito para cair no fim de qualquer
    ordenacao, e a validacao segue nas outras.
    """
    vazio = np.zeros((NUM_PATAMARES, 3))

    return {
        "fases": tuple(int(fase) for fase in fases),
        "convergiu": False,
        "erro": str(erro),
        "ativa": vazio,
        "reativa": vazio.copy(),
        "energia_perdida_kwh": float("inf"),
        "parcela_desequilibrio_kwh": float("inf"),
        "sse_p": float("inf"),
        "fd_max_pct": float("inf"),
    }


# Estado por processo trabalhador. Guarda so o que nao muda entre candidatas; a
# instancia DSS NAO entra aqui, porque cada avaliacao recompila o circuito.
_ESTADO_WORKER = {}


def iniciar_worker(caminho_dss, ramais, elemento, sinal, linhas_trifasicas):
    _ESTADO_WORKER["caminho_dss"] = caminho_dss
    _ESTADO_WORKER["ramais"] = ramais
    _ESTADO_WORKER["elemento"] = elemento
    _ESTADO_WORKER["sinal"] = sinal
    _ESTADO_WORKER["linhas"] = linhas_trifasicas


def avaliar_candidata_worker(tarefa):
    numero, fases = tarefa

    try:
        relatorio = avaliar_com_fluxo(
            _ESTADO_WORKER["caminho_dss"],
            _ESTADO_WORKER["ramais"],
            fases,
            _ESTADO_WORKER["elemento"],
            _ESTADO_WORKER["sinal"],
            linhas_trifasicas=_ESTADO_WORKER["linhas"],
        )
    except Exception as erro:
        relatorio = relatorio_de_falha(fases, erro)

    return numero, relatorio


def validar_candidatas(contexto, candidatas, indices, rotulos, processos):
    """Roda o fluxo de potencia real em cada candidata e reordena o resultado.

    Esta e a segunda etapa do metodo: o indice analitico so escolhe QUEM entra
    aqui. A ordenacao final sai do circuito completo, com a funcao objetivo de
    componentes simetricas.

    As candidatas sao independentes, entao rodam em paralelo. Cada processo
    recompila o circuito a cada candidata, exatamente como no caminho serial: o
    ganho vem de compilar em paralelo, nunca de reaproveitar instancia. Medido
    nesta rede, o paralelo devolve resultados identicos ao serial.
    """
    ramais = contexto["ramais"]
    elemento, sinal = contexto["elemento_e_sinal"]
    numeros = [candidata["numero"] for candidata in candidatas]
    digitos = digitos_das_combinacoes(numeros, len(ramais))

    tarefas = [
        (numero, tuple(int(fase) for fase in digitos[posicao]))
        for posicao, numero in enumerate(numeros)
    ]
    por_numero = {candidata["numero"]: candidata for candidata in candidatas}

    num_processos = max(1, min(processos, len(tarefas)))
    previsao = len(tarefas) * SEGUNDOS_POR_CANDIDATA / num_processos
    print(
        f"  {len(tarefas)} candidatas em {num_processos} processos, "
        f"previsao de {formatar_duracao(previsao)}"
    )

    argumentos = (contexto["caminho_dss"], ramais, elemento, sinal,
                  contexto["linhas_trifasicas"])

    avaliadas = []
    inicio = time.perf_counter()

    if num_processos == 1:
        iniciar_worker(*argumentos)
        resultados = map(avaliar_candidata_worker, tarefas)
        contexto_pool = None
    else:
        contexto_pool = Pool(
            processes=num_processos,
            initializer=iniciar_worker,
            initargs=argumentos,
        )
        # chunksize=1: cada tarefa custa mais de um segundo, entao balancear a
        # carga vale muito mais que economizar despacho.
        resultados = contexto_pool.imap_unordered(
            avaliar_candidata_worker, tarefas, chunksize=1
        )

    try:
        for concluidas, (numero, relatorio) in enumerate(resultados, start=1):
            candidata = por_numero[numero]

            avaliadas.append(
                {
                    "numero": numero,
                    "rotulo": rotulos.get(numero, ""),
                    "indice_de_origem": candidata["indice_de_origem"],
                    "posicao_no_indice": candidata["posicao_no_indice"],
                    "posicao_analitica": posicao_no_ranking(indices["objetivo"], numero),
                    "objetivo_estimado": float(indices["objetivo"][numero]),
                    "sse_p_estimado": float(indices["sse_p"][numero]),
                    "fd_max_pct_estimado": float(indices["fd_max_pct"][numero]),
                    "relatorio": relatorio,
                }
            )

            decorrido = time.perf_counter() - inicio
            restante = decorrido / concluidas * (len(tarefas) - concluidas)

            if relatorio["convergiu"]:
                situacao = ""
            elif "erro" in relatorio:
                situacao = f" (FALHOU: {relatorio['erro'][:40]})"
            else:
                situacao = " (NAO CONVERGIU)"

            print(
                f"  {concluidas:>4}/{len(tarefas)} "
                f"{fases_como_texto(relatorio['fases']):<20} "
                f"desequil. {relatorio['parcela_desequilibrio_kwh']:>7.1f} kWh | "
                f"perdas {relatorio['energia_perdida_kwh']:>8.1f} kWh"
                f"{situacao:<17} | falta {formatar_duracao(restante)}",
                flush=True,
            )
    except BaseException:
        # close() esperaria a fila inteira drenar. Num lote de centenas de
        # candidatas isso prenderia a maquina por minutos depois de um Ctrl+C
        # ou de um erro; terminate() corta na hora.
        if contexto_pool is not None:
            contexto_pool.terminate()
            contexto_pool.join()
        raise
    else:
        if contexto_pool is not None:
            contexto_pool.close()
            contexto_pool.join()

    # A reordenacao usa a MESMA funcao objetivo do script de forca bruta, a
    # parcela de perdas por componentes simetricas. Ordenar por energia perdida
    # total compararia laranja com maca: o indice analitico e a varredura
    # exaustiva falam de desequilibrio, e a perda total e dominada pela parcela
    # de sequencia positiva, que nenhum arranjo de fases altera.
    # As que nao convergiram vao para o fim: seus numeros nao significam nada.
    avaliadas.sort(
        key=lambda item: (
            not item["relatorio"]["convergiu"],
            item["relatorio"]["parcela_desequilibrio_kwh"],
        )
    )
    return avaliadas


def executar_analise(caminho_pasta, dados, modo, quantidade_k, processos, caminho_saida):
    ids_ramais = dados["ramais_interesse"]
    total = confirmar_tamanho(len(ids_ramais))

    contexto = preparar(caminho_pasta, dados, ids_ramais)
    ramais = contexto["ramais"]
    elemento, sinal = contexto["elemento_e_sinal"]

    print(f"Circuito .............. {contexto['caminho_dss'].name}")
    print(f"Ramais de interesse ... {len(ramais)}")
    print(f"Combinacoes (3^N) ..... {formatar_milhar(total)}")
    print(f"Medicao da cabeceira .. {elemento}")
    print(f"Linhas trifasicas ..... {len(contexto['linhas_trifasicas'])}")
    print(f"Grandeza equilibrada .. {modo}")

    print("\nRodando o fluxo do circuito sem os ramais de interesse...")
    base_p, base_q = potencias_de_base(contexto["caminho_dss"], ramais, elemento, sinal)
    imprimir_potencias(
        "[Potencia de base na saida do alimentador, sem os ramais]", base_p, base_q
    )

    print(f"\nAvaliando as {formatar_milhar(total)} combinacoes...")
    inicio = time.perf_counter()
    indices = buscar(base_p, base_q, contexto["ramais_p"], contexto["ramais_q"], modo)
    print(f"  concluido em {formatar_duracao(time.perf_counter() - inicio)}")

    fases_originais = [ramal["fase_original"] - 1 for ramal in ramais]
    numero_original = numero_da_combinacao(fases_originais)
    ordem = ordenar_por_indice(indices, modo)

    print("\n[Configuracao original]")
    print(f"  Fases ................. {fases_como_texto(fases_originais)}")
    print(f"  Objetivo ({modo:<2}) ........ {indices['objetivo'][numero_original]:.4g}")
    print(f"  Desequilibrio maximo .. {indices['fd_max_pct'][numero_original]:.2f}%")
    print(
        f"  Posicao no ranking .... "
        f"{formatar_milhar(posicao_no_ranking(indices['objetivo'], numero_original))}"
        f" de {formatar_milhar(total)}"
    )

    print("\n[Dez melhores pelo indice analitico]")
    print(f"  {'#':>3} | {'fases':<20} | {'objetivo':>12} | {'FD max':>8}")
    print("  " + "-" * 52)
    for posicao, numero in enumerate(ordem[:10], start=1):
        fases = digitos_das_combinacoes([numero], len(ramais))[0]
        print(
            f"  {posicao:>3} | {fases_como_texto(fases):<20} | "
            f"{indices['objetivo'][numero]:>12.4g} | "
            f"{indices['fd_max_pct'][numero]:>7.2f}%"
        )

    caminho_ranking = caminho_saida / NOME_CSV_RANKING
    if total <= LINHAS_CSV_COMPLETO:
        salvar_ranking(caminho_ranking, ramais, ordem, indices)
        print(f"\nRanking completo em: {caminho_ranking}")
    else:
        print(
            f"\nAVISO: {formatar_milhar(total)} combinacoes excedem o limite do CSV; "
            f"gravadas as {LINHAS_CSV_REDUZIDO} melhores mais a original."
        )
        salvar_ranking(
            caminho_ranking,
            ramais,
            np.concatenate([ordem[:LINHAS_CSV_REDUZIDO], [numero_original]]),
            indices,
        )
        print(f"Ranking reduzido em: {caminho_ranking}")

    quantidade_k = min(quantidade_k, total)
    candidatas = selecionar_candidatas(
        indices, modo, quantidade_k, obrigatorias=[numero_original]
    )

    print(f"\n{descrever_peneira(modo, quantidade_k, candidatas)}")
    avaliadas = validar_candidatas(
        contexto, candidatas, indices, {numero_original: "ORIGINAL"}, processos
    )

    caminho_top = caminho_saida / NOME_CSV_TOP
    salvar_top(caminho_top, ramais, avaliadas)
    print(f"\nCandidatas validadas em: {caminho_top}")

    original = next(item for item in avaliadas if item["numero"] == numero_original)
    convergidas = [item for item in avaliadas if item["relatorio"]["convergiu"]]

    if len(convergidas) < len(avaliadas):
        print(
            f"\nAVISO: {len(avaliadas) - len(convergidas)} de {len(avaliadas)} "
            "candidatas nao convergiram e ficaram fora da recomendacao.\n"
            "       Repare que elas costumam exibir os MENORES valores da lista: "
            "sao numeros de\n       uma solucao que nao existe, e venceriam a "
            "ordenacao se fossem aceitas."
        )

    if not convergidas:
        print("\nNenhuma candidata convergiu; nao ha configuracao a recomendar.")
        return

    melhor = convergidas[0]

    print(
        "\n[Candidatas reordenadas pelo fluxo real, por parcela de desequilibrio]"
    )
    cabecalho = (
        f"  {'#':>3} | {'fases':<20} | {'desequil. kWh':>13} | "
        f"{'perdas kWh':>11} | {'FD real':>8} | {'veio de':>16}"
    )
    print(cabecalho)
    print("  " + "-" * (len(cabecalho) - 2))
    for posicao, item in enumerate(avaliadas[:10], start=1):
        relatorio = item["relatorio"]
        marca = "  <- ORIGINAL" if item["numero"] == numero_original else ""
        if not relatorio["convergiu"]:
            marca += "  (NAO CONVERGIU)"
        print(
            f"  {posicao:>3} | {fases_como_texto(relatorio['fases']):<20} | "
            f"{relatorio['parcela_desequilibrio_kwh']:>13.1f} | "
            f"{relatorio['energia_perdida_kwh']:>11.1f} | "
            f"{relatorio['fd_max_pct']:>7.2f}% | "
            f"{item['indice_de_origem'] + ' #' + str(item['posicao_no_indice']):>16}"
            f"{marca}"
        )

    if melhor["numero"] == numero_original:
        print("\nNenhuma candidata superou a configuracao original em perdas.")
        return

    if melhor["numero"] != int(ordem[0]):
        melhor_analitica = digitos_das_combinacoes([ordem[0]], len(ramais))[0]
        print(
            f"\nA melhor no indice analitico ({fases_como_texto(melhor_analitica)}) nao e "
            "a melhor no fluxo real.\nO indice ve so a potencia por fase na cabeceira; a "
            "parcela de desequilibrio ve a\ncorrente de sequencia zero e negativa em cada "
            "linha trifasica. E por isso que a\nrevalidacao existe."
        )

    imprimir_potencias(
        "[Potencia na cabeceira com a configuracao recomendada]",
        melhor["relatorio"]["ativa"],
        melhor["relatorio"]["reativa"],
    )

    print("\n[Comparacao com a configuracao original]")
    for rotulo, chave, unidade in (
        ("Parcela desequilibrio", "parcela_desequilibrio_kwh", "kWh"),
        ("Desequilibrio maximo", "fd_max_pct", "%"),
        ("Energia perdida", "energia_perdida_kwh", "kWh"),
    ):
        antes = original["relatorio"][chave]
        depois = melhor["relatorio"][chave]
        print(
            f"  {rotulo:<22}: {antes:.2f} -> {depois:.2f} {unidade} "
            f"({reducao_percentual(antes, depois):.2f}% de reducao)"
        )

    print("\n[Fases recomendadas]")
    for ramal, fase in zip(ramais, melhor["relatorio"]["fases"]):
        mudou = "SIM" if fase + 1 != ramal["fase_original"] else "NAO"
        print(
            f"  Ramal {ramal['id']:>4} | {ramal['nome']:<20} | "
            f"barra={ramal['barra']:<12} | "
            f"original={NUMERO_PARA_FASE[ramal['fase_original']]} | "
            f"recomendada={NUMERO_PARA_FASE[fase + 1]} | mudou={mudou}"
        )

    print("\nVetor na ordem de ramais_interesse:")
    print([NUMERO_PARA_FASE[fase + 1] for fase in melhor["relatorio"]["fases"]])


# ---- Benchmark contra a varredura exaustiva ----

def carregar_varredura(caminho_csv):
    """Le o CSV da forca bruta: ids na ordem do cabecalho e o objetivo de cada
    combinacao, com as fases convertidas para 0..2."""
    with open(caminho_csv, "r", encoding="utf-8") as arquivo:
        leitor = csv.reader(arquivo)
        cabecalho = next(leitor)
        ids_ramais = [int(coluna.split("_")[-1]) for coluna in cabecalho[:-1]]
        registros = [
            (tuple(int(valor) - 1 for valor in linha[:-1]), float(linha[-1]))
            for linha in leitor
        ]

    return ids_ramais, registros


def postos_medios(valores):
    """Postos 1..n com media nos empates, como o Spearman exige."""
    _, inverso, contagens = np.unique(valores, return_inverse=True, return_counts=True)
    inicio = np.cumsum(contagens) - contagens
    return (inicio + (contagens + 1) / 2.0)[inverso]


def correlacao_spearman(primeiro, segundo):
    return float(np.corrcoef(postos_medios(primeiro), postos_medios(segundo))[0, 1])


def executar_benchmark(caminho_pasta, dados, modo, quantidade_k, processos, caminho_csv):
    """Mede o metodo analitico contra uma varredura exaustiva ja executada.

    O conjunto e a ordem dos ramais vem do cabecalho do CSV, e nao do JSON: a
    varredura pode ter sido rodada com outra selecao de ramais.
    """
    ids_ramais, registros = carregar_varredura(caminho_csv)
    total = 3 ** len(ids_ramais)

    if len(registros) != total:
        raise ValueError(
            f"{caminho_csv} tem {len(registros)} combinacoes, mas {len(ids_ramais)} "
            f"ramais dariam {total}. A varredura esta incompleta."
        )

    print(f"Varredura de referencia  {caminho_csv}")
    print(f"Ramais (ordem do CSV) .. {ids_ramais}")
    print(f"Combinacoes ............ {formatar_milhar(total)}")

    contexto = preparar(caminho_pasta, dados, ids_ramais)
    ramais = contexto["ramais"]
    elemento, sinal = contexto["elemento_e_sinal"]

    base_p, base_q = potencias_de_base(contexto["caminho_dss"], ramais, elemento, sinal)
    imprimir_potencias("[Potencia de base, sem os ramais da varredura]", base_p)

    indices = buscar(base_p, base_q, contexto["ramais_p"], contexto["ramais_q"], modo)

    verdadeiro = np.empty(total)
    for fases, valor in registros:
        verdadeiro[numero_da_combinacao(fases)] = valor

    fases_originais = [ramal["fase_original"] - 1 for ramal in ramais]
    numero_original = numero_da_combinacao(fases_originais)
    melhor_analitico = int(np.argmin(indices["objetivo"]))
    otimo_verdadeiro = int(np.argmin(verdadeiro))

    print("\n[Ranking analitico contra o ranking verdadeiro]")
    print(
        f"  Spearman ....................... "
        f"{correlacao_spearman(indices['objetivo'], verdadeiro):.3f}"
    )
    print(f"  {'configuracao':<30} {'fases':<14} {'analitico':>12} {'verdadeiro':>12}")
    for rotulo, numero in (
        ("Original", numero_original),
        ("Melhor pelo indice analitico", melhor_analitico),
        ("Otimo da varredura exaustiva", otimo_verdadeiro),
    ):
        fases = digitos_das_combinacoes([numero], len(ramais))[0]
        print(
            f"  {rotulo:<30} {fases_como_texto(fases):<14} "
            f"{formatar_milhar(posicao_no_ranking(indices['objetivo'], numero)):>12} "
            f"{formatar_milhar(posicao_no_ranking(verdadeiro, numero)):>12}"
        )

    ordem = ordenar_por_indice(indices, modo)
    quantidade_k = min(quantidade_k, total)
    candidatas = selecionar_candidatas(
        indices, modo, quantidade_k, obrigatorias=[numero_original, otimo_verdadeiro]
    )

    print(f"\n{descrever_peneira(modo, quantidade_k, candidatas)}")
    print("  (a original e o otimo da varredura entram forcados, para comparacao)")
    rotulos = {numero_original: "ORIGINAL", otimo_verdadeiro: "OTIMO-VARREDURA"}
    avaliadas = validar_candidatas(contexto, candidatas, indices, rotulos, processos)

    por_numero = {item["numero"]: item for item in avaliadas}
    original = por_numero[numero_original]
    otimo = por_numero[otimo_verdadeiro]

    do_top = [
        item
        for item in avaliadas
        if item["numero"] not in rotulos and item["relatorio"]["convergiu"]
    ]

    if not do_top:
        print("\nNenhuma candidata do top-K convergiu; nada a comparar.")
        return

    melhor_do_top = do_top[0]

    def parcela(item):
        return item["relatorio"]["parcela_desequilibrio_kwh"]

    objetivo_original = parcela(original)
    objetivo_otimo = parcela(otimo)
    objetivo_top = parcela(melhor_do_top)

    print("\n[O que o metodo simplificado entrega, na funcao objetivo da varredura]")
    print(
        f"  {'configuracao':<36} {'fases':<14} {'desequil. kWh':>13} "
        f"{'reducao':>9} {'perdas kWh':>11}"
    )
    for rotulo, item in (
        ("Original", original),
        (f"Melhor do top-{quantidade_k}, por perdas", melhor_do_top),
        ("Otimo da varredura de referencia", otimo),
    ):
        relatorio = item["relatorio"]
        aviso = "" if relatorio["convergiu"] else "   NAO CONVERGIU"
        print(
            f"  {rotulo:<36} {fases_como_texto(relatorio['fases']):<14} "
            f"{relatorio['parcela_desequilibrio_kwh']:>13.1f} "
            f"{reducao_percentual(objetivo_original, parcela(item)):>8.1f}% "
            f"{relatorio['energia_perdida_kwh']:>11.1f}"
            f"{aviso}"
        )

    if not otimo["relatorio"]["convergiu"]:
        print(
            "\n  O otimo da varredura de referencia nao convergiu aqui, entao os "
            "numeros dele\n  acima nao servem de referencia."
        )
    elif abs(objetivo_original - objetivo_otimo) > EPSILON:
        capturado = (objetivo_original - objetivo_top) / (objetivo_original - objetivo_otimo)
        print(
            f"\n  O top-{quantidade_k} captura {100 * capturado:.0f}% da reducao de "
            f"desequilibrio que a varredura exaustiva encontrou,\n"
            f"  com {len(candidatas)} fluxos de potencia em vez de "
            f"{formatar_milhar(total)}."
        )

    caminho_top = caminho_csv.parent / NOME_CSV_TOP_BENCHMARK
    salvar_top(caminho_top, ramais, avaliadas)
    print(f"\nCandidatas validadas em: {caminho_top}")


# ---- Entrada de dados ----

def perguntar_caminho(rotulo, deve_ser_pasta):
    """Pergunta um caminho e insiste ate receber um que exista.

    Aspas em volta sao removidas, porque arrastar um arquivo para o terminal ou
    usar 'copiar como caminho' no Windows entrega o caminho ja entre aspas.
    """
    while True:
        resposta = input(f"{rotulo}: ").strip().strip('"').strip("'")

        if not resposta:
            print("  Caminho vazio.")
            continue

        caminho = Path(resposta).expanduser()

        if deve_ser_pasta and not caminho.is_dir():
            print(f"  Pasta nao encontrada: {caminho}")
            continue

        if not deve_ser_pasta and not caminho.is_file():
            print(f"  Arquivo nao encontrado: {caminho}")
            continue

        return caminho.resolve()


def perguntar_opcao(titulo, opcoes, indice_padrao=0):
    """Menu numerado. opcoes e uma lista de (valor, descricao)."""
    largura = max(len(str(valor)) for valor, _ in opcoes)

    print(f"\n{titulo}")
    for numero, (valor, descricao) in enumerate(opcoes, start=1):
        padrao = "  (padrao)" if numero - 1 == indice_padrao else ""
        print(f"  {numero} - {valor:<{largura}}  {descricao}{padrao}")

    while True:
        resposta = input(f"Escolha [{indice_padrao + 1}]: ").strip()

        if not resposta:
            return opcoes[indice_padrao][0]

        if resposta.isdigit() and 1 <= int(resposta) <= len(opcoes):
            return opcoes[int(resposta) - 1][0]

        print(f"  Responda com um numero de 1 a {len(opcoes)}.")


def perguntar_inteiro(rotulo, padrao, minimo=1):
    while True:
        resposta = input(f"{rotulo} [{padrao}]: ").strip()

        if not resposta:
            return padrao

        if resposta.isdigit() and int(resposta) >= minimo:
            return int(resposta)

        print(f"  Responda com um inteiro maior ou igual a {minimo}.")


def perguntar_tudo():
    """Conduz o dialogo do inicio ao fim e devolve o que foi escolhido."""
    print("\n=== Balanceamento de fases dos ramais ===")

    tarefa = perguntar_opcao(
        "O que rodar:",
        [
            ("analise", "busca as fases recomendadas para os ramais de interesse"),
            ("benchmark", "compara com uma varredura exaustiva ja executada"),
        ],
    )

    print()
    caminho_pasta = perguntar_caminho(
        "Pasta do circuito (a que tem o *_Master.dss)", deve_ser_pasta=True
    )
    caminho_json = perguntar_caminho("Arquivo .json dos ramais", deve_ser_pasta=False)

    caminho_benchmark = None
    if tarefa == "benchmark":
        caminho_benchmark = perguntar_caminho(
            "CSV da varredura exaustiva (resultados_forca_bruta.csv)",
            deve_ser_pasta=False,
        )

    modo = perguntar_opcao(
        "Indice que peneira as combinacoes:",
        [
            ("P", "desvio quadratico da potencia ativa por fase"),
            ("S", "idem para a potencia aparente, leva P e Q juntos"),
            ("PQ", "ativa e reativa somadas, com pesos iguais"),
            ("FD", "fator de desequilibrio maximo: relativo e minimax"),
            (MODO_UNIAO, f"uniao dos {len(MODOS_SIMPLES)} indices, no pior caso 4xK fluxos"),
        ],
        indice_padrao=MODOS.index(MODO_PADRAO),
    )

    print(
        "\nO indice so peneira: as escolhidas sao revalidadas com fluxo de potencia\n"
        "real e reordenadas pela parcela de desequilibrio. Por isso o que importa\n"
        "nele e a boa combinacao ESTAR na peneira, nao estar em primeiro lugar."
    )

    if modo == MODO_UNIAO:
        print(
            "Na uniao, K e por indice: no pior caso sao 4xK fluxos, mas os indices\n"
            "se repetem bastante e na pratica sobra bem menos."
        )

    quantidade_k = perguntar_inteiro(
        "K (candidatas por indice)" if modo == MODO_UNIAO
        else "Quantas candidatas revalidar com fluxo de potencia",
        K_PADRAO,
    )

    padrao = processos_padrao()
    print(
        f"\nA revalidacao roda em paralelo. Esta maquina tem {cpu_count()} CPUs; o\n"
        f"padrao usa {padrao} e deixa o resto livre, porque cada processo segura um\n"
        "OpenDSS por minutos e ocupar tudo trava a maquina. Use 1 para desligar."
    )
    processos = perguntar_inteiro("Processos paralelos", padrao)

    return caminho_pasta, caminho_json, modo, quantidade_k, processos, caminho_benchmark


def main():
    (
        caminho_pasta,
        caminho_json,
        modo,
        quantidade_k,
        processos,
        caminho_benchmark,
    ) = perguntar_tudo()

    with open(caminho_json, "r", encoding="utf-8") as arquivo:
        dados = json.load(arquivo)

    print()
    inicio = time.perf_counter()

    if caminho_benchmark is not None:
        executar_benchmark(
            caminho_pasta, dados, modo, quantidade_k, processos, caminho_benchmark
        )
    else:
        executar_analise(
            caminho_pasta,
            dados,
            modo,
            quantidade_k,
            processos,
            Path(__file__).resolve().parent,
        )

    print(f"\nTempo total: {formatar_duracao(time.perf_counter() - inicio)}")


if __name__ == "__main__":
    main()
