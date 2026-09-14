"""Busca exaustiva da configuracao otima de fases dos ramais monofasicos.

Enumera TODAS as 3^N combinacoes possiveis e devolve o minimo global, com
prova de otimalidade. Para N pequeno (ate ~10 ramais) isso custa o mesmo que
uma rodada heuristica e entrega uma garantia que a heuristica nao da.

Para N grande, use otimiza_ramal_optuna_i0_i1_i2.py.
"""

import csv
import itertools
import json
import random
import re
import time
from math import isfinite, sqrt
from multiprocessing import Pool, cpu_count
from pathlib import Path

import py_dss_interface


# ---- Constantes ----

NUM_PATAMARES = 4
DURACAO_PATAMAR_H = 1.0
PENALIDADE_NAO_CONVERGIU = 1e30
EPSILON = 1e-12

# Operador de rotacao de 120 graus usado nas componentes simetricas.
OPERADOR_A = complex(-0.5, sqrt(3) / 2)

# Em modo daily, cada Solve avanca 'number' passos de tempo. Com number > 1 o
# laco dos patamares le sempre o mesmo instante do LoadShape e o objetivo passa
# a medir um patamar repetido em vez da soma dos quatro.
NUMBER_EXIGIDO = 1

# Antes de varrer, o script projeta o tempo total. Acima deste valor a
# confirmacao passa a ter "nao" como resposta padrao.
MINUTOS_PARA_AVISAR = 15

# Portao de seguranca do reuso de instancia (ver validar_reuso_instancia).
AMOSTRAS_VALIDACAO_REUSO = 20
TOLERANCIA_RELATIVA_REUSO = 1e-9
SEMENTE_VALIDACAO = 20250818

NOME_CSV = "resultados_forca_bruta.csv"
INTERVALO_PROGRESSO = 250
TAMANHO_LOTE = 64
PROCESSOS_RESERVADOS = 2


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


# ---- Validacao da configuracao de solucao ----

def verificar_configuracao_solucao(dss, caminho_dss):
    """Aborta se cada Solve avancar mais de um passo de tempo.

    Com 'Set number=N' e N > 1, o laco de patamares le as correntes apenas no
    ultimo dos N passos de cada Solve. Como o LoadShape tem NUM_PATAMARES
    pontos e da a volta, as NUM_PATAMARES leituras caem todas no mesmo ponto:
    o objetivo vira NUM_PATAMARES vezes um unico patamar, e os demais somem da
    conta. Melhor parar do que otimizar um numero sem significado.
    """
    numero = dss.solution.number

    if numero != NUMBER_EXIGIDO:
        raise SystemExit(
            "\n"
            "ERRO: configuracao de solucao invalida para esta analise.\n"
            "\n"
            f"  O circuito esta com 'Set number={numero}', ou seja, cada Solve\n"
            f"  avanca {numero} passos de tempo. As {NUM_PATAMARES} leituras de\n"
            f"  corrente cairiam todas no mesmo patamar do LoadShape, e o\n"
            f"  objetivo mediria {NUM_PATAMARES}x um patamar so, em vez da soma\n"
            f"  dos {NUM_PATAMARES} patamares.\n"
            "\n"
            f"  Corrija para 'Set number={NUMBER_EXIGIDO}' em:\n"
            f"    {caminho_dss}\n"
            "\n"
            "  Depois rode este script de novo.\n"
        )


def compilar_circuito(caminho_dss):
    """Cria uma instancia DSS, compila o circuito e valida a configuracao."""
    dss = py_dss_interface.DSS()
    dss.text(f'compile "{caminho_dss}"')
    verificar_configuracao_solucao(dss, caminho_dss)
    return dss


# ---- Simulacao ----

def rodar_circuito(dss, fases, ramais, linhas_trifasicas, gerar_relatorio=False):
    """Avalia uma combinacao de fases numa instancia DSS ja compilada."""
    if len(fases) != len(ramais):
        raise ValueError("A quantidade de fases deve ser igual a quantidade de ramais.")

    # Toda avaliacao atribui explicitamente uma fase a cada ramal.
    for ramal, nova_fase in zip(ramais, fases):
        if nova_fase not in (1, 2, 3):
            raise ValueError(f"Fase invalida para {ramal['nome']}: {nova_fase}")

        dss.text(
            f"Edit Load.{ramal['nome']} "
            f"bus1={ramal['barra']}.{nova_fase}"
        )

    # Reancora o tempo para que toda combinacao percorra os mesmos patamares,
    # independentemente de quantas avaliacoes ja rodaram nesta instancia.
    dss.text("set time=(0,0)")

    parcela_desequilibrio_kwh = 0.0
    energia_perdida_kwh = 0.0
    valores_vuf = []
    tensoes_pu = []

    barras_trifasicas = (
        obter_barras_trifasicas(dss)
        if gerar_relatorio
        else []
    )

    for _ in range(NUM_PATAMARES):
        dss.solution.solve()

        if not dss.solution.converged:
            if gerar_relatorio:
                raise RuntimeError("O circuito nao convergiu durante a geracao do relatorio.")

            return PENALIDADE_NAO_CONVERGIU

        if gerar_relatorio:
            # Circuit.Losses retorna [W, var] para todo o circuito.
            perdas_circuito = dss.circuit.losses
            energia_perdida_kwh += (
                perdas_circuito[0]
                * DURACAO_PATAMAR_H
                / 1_000
            )

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

            # Parcela de perdas associada ao desequilibrio:
            # 3 * L * (R0 * |I0|^2 + R1 * |I2|^2).
            # R0 e R1 estao em ohm por unidade de comprimento e L usa a
            # unidade compativel definida na linha. O resultado esta em watts.
            perdas_desequilibrio = 3 * comprimento * (
                r0 * abs(corrente_0) ** 2
                + r1 * abs(corrente_2) ** 2
            )

            parcela_desequilibrio_kwh += (
                perdas_desequilibrio
                * DURACAO_PATAMAR_H
                / 1_000
            )

        if gerar_relatorio:
            for nome_barra in barras_trifasicas:
                dss.circuit.set_active_bus(nome_barra)

                tensoes_sequencia = dss.bus.cplx_sequence_voltages
                if len(tensoes_sequencia) >= 6:
                    tensao_1 = complex(
                        tensoes_sequencia[2],
                        tensoes_sequencia[3],
                    )
                    tensao_2 = complex(
                        tensoes_sequencia[4],
                        tensoes_sequencia[5],
                    )

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

    if gerar_relatorio:
        if not valores_vuf:
            raise RuntimeError("Nao foi possivel calcular o VUF nas barras trifasicas.")

        if not tensoes_pu:
            raise RuntimeError("Nao foi possivel obter as tensoes em pu.")

        return {
            "energia_perdida_kwh": energia_perdida_kwh,
            "parcela_desequilibrio_kwh": parcela_desequilibrio_kwh,
            "vuf_medio": sum(valores_vuf) / len(valores_vuf),
            "vuf_maximo": max(valores_vuf),
            "tensao_minima_pu": min(tensoes_pu),
            "tensao_maxima_pu": max(tensoes_pu),
        }

    return parcela_desequilibrio_kwh


def rodar_circuito_recompilando(
    caminho_dss, fases, ramais, linhas_trifasicas, gerar_relatorio=False
):
    """Mesma avaliacao, porem numa instancia DSS recem-compilada.

    E o caminho lento e sem memoria de estado, usado como referencia pelo
    portao de validacao e como plano B caso o reuso nao se prove seguro.
    """
    dss = compilar_circuito(caminho_dss)
    return rodar_circuito(dss, fases, ramais, linhas_trifasicas, gerar_relatorio)


def validar_reuso_instancia(caminho_dss, ramais, linhas_trifasicas):
    """Compara reusar a instancia contra recompilar, em combinacoes sorteadas.

    Reusar a instancia evita recompilar o circuito inteiro a cada avaliacao,
    mas herda o estado da solucao anterior. Em vez de supor que isso e
    inofensivo, medimos: as avaliacoes reusadas rodam em sequencia na MESMA
    instancia, justamente para acumular estado, e cada uma e conferida contra
    uma instancia limpa.

    Devolve o maior erro relativo observado.
    """
    sorteio = random.Random(SEMENTE_VALIDACAO)
    dss = compilar_circuito(caminho_dss)
    maior_erro = 0.0

    for _ in range(AMOSTRAS_VALIDACAO_REUSO):
        fases = tuple(sorteio.choice((1, 2, 3)) for _ in ramais)

        valor_reusado = rodar_circuito(dss, fases, ramais, linhas_trifasicas)
        valor_limpo = rodar_circuito_recompilando(
            caminho_dss, fases, ramais, linhas_trifasicas
        )

        erro = abs(valor_reusado - valor_limpo) / max(abs(valor_limpo), EPSILON)
        maior_erro = max(maior_erro, erro)

    return maior_erro


# ---- Avaliacao em paralelo ----

# Estado por processo trabalhador. Cada processo compila o circuito uma vez no
# initializer e reaproveita a instancia em todas as combinacoes que receber.
_ESTADO_WORKER = {}


def iniciar_worker(caminho_dss, ramais, linhas_trifasicas, reusar_instancia):
    _ESTADO_WORKER["caminho_dss"] = caminho_dss
    _ESTADO_WORKER["ramais"] = ramais
    _ESTADO_WORKER["linhas"] = linhas_trifasicas
    _ESTADO_WORKER["reusar"] = reusar_instancia
    _ESTADO_WORKER["dss"] = compilar_circuito(caminho_dss) if reusar_instancia else None


def avaliar_combinacao(fases):
    if _ESTADO_WORKER["reusar"]:
        valor = rodar_circuito(
            _ESTADO_WORKER["dss"],
            fases,
            _ESTADO_WORKER["ramais"],
            _ESTADO_WORKER["linhas"],
        )
    else:
        valor = rodar_circuito_recompilando(
            _ESTADO_WORKER["caminho_dss"],
            fases,
            _ESTADO_WORKER["ramais"],
            _ESTADO_WORKER["linhas"],
        )

    return fases, valor


# ---- Relatorio ----

def reducao_percentual(valor_original, valor_otimizado):
    if abs(valor_original) <= EPSILON:
        return 0.0

    return 100 * (valor_original - valor_otimizado) / valor_original


def imprimir_relatorio(relatorio_original, relatorio_otimizado):
    print("\n[Circuito otimizado]")
    print(
        f"  Energia perdida ....... "
        f"{relatorio_otimizado['energia_perdida_kwh']:.4f} kWh"
    )
    print(
        f"  Parcela desequilibrio . "
        f"{relatorio_otimizado['parcela_desequilibrio_kwh']:.4f} kWh"
    )
    print(
        f"  VUF medio ............. "
        f"{100 * relatorio_otimizado['vuf_medio']:.4f} %"
    )
    print(
        f"  VUF maximo ............ "
        f"{100 * relatorio_otimizado['vuf_maximo']:.4f} %"
    )
    print(
        f"  Tensao (pu) ........... "
        f"{relatorio_otimizado['tensao_minima_pu']:.4f} a "
        f"{relatorio_otimizado['tensao_maxima_pu']:.4f}"
    )

    print("\nComparacao com o circuito original:")
    print(
        f"  Energia perdida: "
        f"{relatorio_original['energia_perdida_kwh']:.4f} -> "
        f"{relatorio_otimizado['energia_perdida_kwh']:.4f} kWh "
        f"({reducao_percentual(relatorio_original['energia_perdida_kwh'], relatorio_otimizado['energia_perdida_kwh']):.2f}% de reducao)"
    )
    print(
        f"  Parcela desequilibrio: "
        f"{relatorio_original['parcela_desequilibrio_kwh']:.4f} -> "
        f"{relatorio_otimizado['parcela_desequilibrio_kwh']:.4f} kWh "
        f"({reducao_percentual(relatorio_original['parcela_desequilibrio_kwh'], relatorio_otimizado['parcela_desequilibrio_kwh']):.2f}% de reducao)"
    )
    print(
        f"  VUF medio: "
        f"{100 * relatorio_original['vuf_medio']:.4f}% -> "
        f"{100 * relatorio_otimizado['vuf_medio']:.4f}% "
        f"({reducao_percentual(relatorio_original['vuf_medio'], relatorio_otimizado['vuf_medio']):.2f}% de reducao)"
    )
    print(
        f"  VUF maximo: "
        f"{100 * relatorio_original['vuf_maximo']:.4f}% -> "
        f"{100 * relatorio_otimizado['vuf_maximo']:.4f}% "
        f"({reducao_percentual(relatorio_original['vuf_maximo'], relatorio_otimizado['vuf_maximo']):.2f}% de reducao)"
    )


def formatar_duracao(segundos):
    if segundos < 90:
        return f"{segundos:.1f} s"

    minutos = segundos / 60
    if minutos < 90:
        return f"{minutos:.1f} min"

    return f"{minutos / 60:.1f} h"


def salvar_csv(caminho_csv, ramais, resultados):
    """Grava todas as combinacoes avaliadas, da melhor para a pior."""
    cabecalho = [f"fase_ramal_{ramal['id']}" for ramal in ramais]
    cabecalho.append("parcela_desequilibrio_kwh")

    with open(caminho_csv, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(cabecalho)

        for fases, valor in resultados:
            escritor.writerow(list(fases) + [f"{valor:.10f}"])


# ---- Orcamento e confirmacao ----

def confirmar_varredura(total_combinacoes, segundos_por_avaliacao, num_processos):
    segundos_totais = total_combinacoes * segundos_por_avaliacao / num_processos

    print("\n[Orcamento da varredura]")
    print(f"  Combinacoes (3^N) ..... {total_combinacoes:,}".replace(",", "."))
    print(f"  Tempo por avaliacao ... {segundos_por_avaliacao * 1000:.1f} ms (medido)")
    print(f"  Processos ............. {num_processos}")
    print(f"  Tempo estimado ........ {formatar_duracao(segundos_totais)}")

    demorado = segundos_totais > MINUTOS_PARA_AVISAR * 60

    if demorado:
        print(
            f"\n  AVISO: acima de {MINUTOS_PARA_AVISAR} min. Para um espaco desse "
            f"tamanho,\n  considere usar otimiza_ramal_optuna_i0_i1_i2.py."
        )

    rotulo = "[s/N]" if demorado else "[S/n]"
    resposta = input(f"\nExecutar a varredura completa? {rotulo} ").strip().lower()

    if not resposta:
        return not demorado

    return resposta in ("s", "sim", "y", "yes")


# ---- Forca bruta ----

def executar_forca_bruta(caminho_dss, caminho_ramais, caminho_chaves, ids_ramais, caminho_csv):
    dss = compilar_circuito(caminho_dss)

    ramais = carregar_ramais(caminho_ramais, ids_ramais)
    linhas_trifasicas = obter_linhas_trifasicas(dss, caminho_chaves)
    fases_originais = tuple(ramal["fase_original"] for ramal in ramais)

    print(f"Ramais de interesse ... {len(ramais)}")
    print(f"Linhas trifasicas ..... {len(linhas_trifasicas)}")

    print("\nCalculando os indicadores do circuito original...")
    relatorio_original = rodar_circuito_recompilando(
        caminho_dss,
        fases_originais,
        ramais,
        linhas_trifasicas,
        gerar_relatorio=True,
    )

    print("\nValidando o reuso de instancia contra recompilar a cada avaliacao...")
    maior_erro = validar_reuso_instancia(caminho_dss, ramais, linhas_trifasicas)
    reusar_instancia = maior_erro <= TOLERANCIA_RELATIVA_REUSO

    print(
        f"  maior erro relativo em {AMOSTRAS_VALIDACAO_REUSO} combinacoes: "
        f"{maior_erro:.3e}"
    )

    if reusar_instancia:
        print("  OK: reusando a instancia (rapido).")
    else:
        print(
            f"  Erro acima da tolerancia ({TOLERANCIA_RELATIVA_REUSO:.0e}).\n"
            "  Recompilando a cada avaliacao (mais lento, porem seguro)."
        )

    # Cronometra uma avaliacao pela estrategia que sera de fato usada.
    inicio = time.perf_counter()
    if reusar_instancia:
        rodar_circuito(dss, fases_originais, ramais, linhas_trifasicas)
    else:
        rodar_circuito_recompilando(
            caminho_dss, fases_originais, ramais, linhas_trifasicas
        )
    segundos_por_avaliacao = time.perf_counter() - inicio

    total_combinacoes = 3 ** len(ramais)
    num_processos = max(1, min(cpu_count() - PROCESSOS_RESERVADOS, total_combinacoes))

    if not confirmar_varredura(total_combinacoes, segundos_por_avaliacao, num_processos):
        print("\nVarredura cancelada.")
        return

    print(f"\nVarrendo as {total_combinacoes} combinacoes...")
    combinacoes = itertools.product((1, 2, 3), repeat=len(ramais))
    resultados = []
    inicio = time.perf_counter()

    with Pool(
        processes=num_processos,
        initializer=iniciar_worker,
        initargs=(caminho_dss, ramais, linhas_trifasicas, reusar_instancia),
    ) as pool:
        for concluidas, resultado in enumerate(
            pool.imap_unordered(avaliar_combinacao, combinacoes, chunksize=TAMANHO_LOTE),
            start=1,
        ):
            resultados.append(resultado)

            if concluidas % INTERVALO_PROGRESSO == 0 or concluidas == total_combinacoes:
                decorrido = time.perf_counter() - inicio
                restante = decorrido / concluidas * (total_combinacoes - concluidas)
                print(
                    f"  {concluidas:>7}/{total_combinacoes} "
                    f"({100 * concluidas / total_combinacoes:5.1f}%) | "
                    f"decorrido {formatar_duracao(decorrido)} | "
                    f"falta {formatar_duracao(restante)}",
                    flush=True,
                )

    decorrido = time.perf_counter() - inicio
    print(f"\nVarredura concluida em {formatar_duracao(decorrido)}.")

    nao_convergiram = sum(
        1 for _, valor in resultados if valor >= PENALIDADE_NAO_CONVERGIU
    )
    if nao_convergiram:
        print(f"AVISO: {nao_convergiram} combinacoes nao convergiram e foram penalizadas.")

    resultados.sort(key=lambda item: item[1])
    salvar_csv(caminho_csv, ramais, resultados)
    print(f"Resultados completos em: {caminho_csv}")

    melhores_fases, melhor_valor = resultados[0]

    print("\nCalculando os indicadores do circuito otimizado...")
    relatorio_otimizado = rodar_circuito_recompilando(
        caminho_dss,
        melhores_fases,
        ramais,
        linhas_trifasicas,
        gerar_relatorio=True,
    )

    imprimir_relatorio(relatorio_original, relatorio_otimizado)

    print("\n[Otimo global - varredura exaustiva]")
    print(f"  Combinacoes avaliadas . {len(resultados)} de {total_combinacoes}")
    print(f"  Melhor objetivo ....... {melhor_valor:.6f} kWh")
    print(f"  Objetivo original ..... {resultados_do_original(resultados, fases_originais):.6f} kWh")

    print("\nDez melhores combinacoes:")
    print(f"  {'#':>3} | {'fases':<26} | objetivo (kWh)")
    print("  " + "-" * 58)
    for posicao, (fases, valor) in enumerate(resultados[:10], start=1):
        marca = "  <- original" if fases == fases_originais else ""
        print(f"  {posicao:>3} | {str(list(fases)):<26} | {valor:.6f}{marca}")

    print("\nFases encontradas:")

    for ramal, fase_otima in zip(ramais, melhores_fases):
        mudou = "SIM" if fase_otima != ramal["fase_original"] else "NAO"

        print(
            f"Ramal {ramal['id']:>3} | "
            f"{ramal['nome']:<18} | "
            f"original={ramal['fase_original']} | "
            f"otima={fase_otima} | mudou={mudou}"
        )

    print("\nVetor na ordem de IDS_RAMAIS:")
    print(list(melhores_fases))


def resultados_do_original(resultados, fases_originais):
    """Objetivo da configuracao atual, tal como saiu da propria varredura."""
    for fases, valor in resultados:
        if fases == fases_originais:
            return valor

    raise RuntimeError(
        "A configuracao original nao apareceu na varredura - a enumeracao "
        "esta incompleta."
    )


# ---- Entrada de dados ----

def main():
    # Entre caminho dss:
    caminho_1 = input("Informe a pasta do circuito: ").strip('"').strip("'")
    caminho_2 = input("Caminho .json ramais: ").strip('"').strip("'")

    caminho_pasta_dss = Path(caminho_1)

    # Localiza automaticamente o arquivo *_Master.dss
    arquivos_master = list(caminho_pasta_dss.glob("*_Master.dss"))

    if not arquivos_master:
        raise FileNotFoundError(
            f"Nenhum arquivo *_Master.dss encontrado em {caminho_pasta_dss}"
        )

    if len(arquivos_master) > 1:
        raise RuntimeError(
            f"Mais de um arquivo *_Master.dss encontrado: {arquivos_master}"
        )

    caminho_dss = arquivos_master[0]

    caminho_ramais = caminho_pasta_dss / "ramalmonofasico.dss"
    caminho_chaves = caminho_pasta_dss / "chaves.dss"

    with open(caminho_2, "r", encoding="utf-8") as arquivo:
        dados = json.load(arquivo)

    # Esta lista define explicitamente a ordem das variaveis de decisao.
    ids_ramais = dados["ramais_interesse"]

    caminho_csv = Path(__file__).resolve().parent / NOME_CSV

    executar_forca_bruta(
        caminho_dss,
        caminho_ramais,
        caminho_chaves,
        ids_ramais,
        caminho_csv,
    )


# Os caminhos sao lidos dentro de main(), e nao no nivel do modulo como no
# script do Optuna: no Windows o multiprocessing usa 'spawn' e cada processo
# filho reimporta este arquivo, o que dispararia os input() em todo worker.
if __name__ == "__main__":
    main()
