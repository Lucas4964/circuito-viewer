import json
import re
from math import isfinite, sqrt
from pathlib import Path
from secrets import randbits

import py_dss_interface


# ---- Constantes ----

NUM_PATAMARES = 4
DURACAO_PATAMAR_H = 1.0
NUM_AVALIACOES = 5_000
SEMENTE = randbits(32)
PENALIDADE_NAO_CONVERGIU = 1e30
EPSILON = 1e-12

# Operador de rotacao de 120 graus usado nas componentes simetricas.
OPERADOR_A = complex(-0.5, sqrt(3) / 2)


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


def obter_linhas_trifasicas():
    dss = py_dss_interface.DSS()
    dss.text(f'compile "{CAMINHO_DSS}"')

    nomes_chaves = carregar_nomes_chaves(CAMINHO_CHAVES)
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


# ---- Simulacao ----

def rodar_circuito(fases, ramais, linhas_trifasicas, gerar_relatorio=False):
    if len(fases) != len(ramais):
        raise ValueError("A quantidade de fases deve ser igual a quantidade de ramais.")

    dss = py_dss_interface.DSS()
    dss.text(f'compile "{CAMINHO_DSS}"')

    # Toda avaliacao atribui explicitamente uma fase a cada ramal.
    for ramal, nova_fase in zip(ramais, fases):
        if nova_fase not in (1, 2, 3):
            raise ValueError(f"Fase invalida para {ramal['nome']}: {nova_fase}")

        dss.text(
            f"Edit Load.{ramal['nome']} "
            f"bus1={ramal['barra']}.{nova_fase}"
        )

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


# ---- Otimizacao ----

def executar_otimizacao():
    import optuna

    ramais = carregar_ramais(CAMINHO_RAMAIS, IDS_RAMAIS)
    linhas_trifasicas = obter_linhas_trifasicas()
    fases_originais = tuple(ramal["fase_original"] for ramal in ramais)

    print("Calculando os indicadores do circuito original...")
    relatorio_original = rodar_circuito(
        fases_originais,
        ramais,
        linhas_trifasicas,
        gerar_relatorio=True,
    )

    # Evita repetir uma simulacao se o Optuna sugerir a mesma combinacao.
    cache_avaliacoes = {
        fases_originais: relatorio_original["parcela_desequilibrio_kwh"]
    }

    def objetivo(trial):
        fases = tuple(
            trial.suggest_categorical(
                f"fase_ramal_{ramal['id']}",
                [1, 2, 3],
            )
            for ramal in ramais
        )

        if fases not in cache_avaliacoes:
            cache_avaliacoes[fases] = rodar_circuito(
                fases,
                ramais,
                linhas_trifasicas,
            )

        return cache_avaliacoes[fases]

    sampler = optuna.samplers.TPESampler(
        seed=SEMENTE,
        multivariate=True,
        n_startup_trials=100,
    )

    study = optuna.create_study(
        direction="minimize",
        sampler=sampler,
    )

    # Garante que a configuracao atual seja avaliada e sirva de referencia.
    study.enqueue_trial(
        {
            f"fase_ramal_{ramal['id']}": ramal["fase_original"]
            for ramal in ramais
        }
    )

    study.optimize(
        objetivo,
        n_trials=NUM_AVALIACOES,
        show_progress_bar=True,
    )

    melhores_fases = tuple(
        study.best_params[f"fase_ramal_{ramal['id']}"]
        for ramal in ramais
    )

    print("\nCalculando os indicadores do circuito otimizado...")
    relatorio_otimizado = rodar_circuito(
        melhores_fases,
        ramais,
        linhas_trifasicas,
        gerar_relatorio=True,
    )

    imprimir_relatorio(relatorio_original, relatorio_otimizado)

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


# ---- Entrada de dados ----

# Entre caminho dss:
caminho_1 = input("Informe a pasta do circuito: ").strip('"').strip("'")
caminho_2 = input("Caminho .json ramais: ").strip('"').strip("'")

CAMINHO_PASTA_DSS = Path(caminho_1)

# Localiza automaticamente o arquivo *_Master.dss
arquivos_master = list(CAMINHO_PASTA_DSS.glob("*_Master.dss"))

if not arquivos_master:
    raise FileNotFoundError(
        f"Nenhum arquivo *_Master.dss encontrado em {CAMINHO_PASTA_DSS}"
    )

if len(arquivos_master) > 1:
    raise RuntimeError(
        f"Mais de um arquivo *_Master.dss encontrado: {arquivos_master}"
    )

CAMINHO_DSS = arquivos_master[0]

CAMINHO_RAMAIS = CAMINHO_PASTA_DSS / "ramalmonofasico.dss"
CAMINHO_CHAVES = CAMINHO_PASTA_DSS / "chaves.dss"

with open(caminho_2, "r", encoding="utf-8") as arquivo:
    dados = json.load(arquivo)


# Esta lista define explicitamente a ordem das variaveis de decisao.
IDS_RAMAIS = dados["ramais_interesse"]

print(f"Seed utilizada: {SEMENTE}")


if __name__ == "__main__":
    executar_otimizacao()
