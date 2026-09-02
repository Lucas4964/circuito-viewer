"""Parâmetros de solução do OpenDSS definidos pelo usuário.

Módulo folha: sem Qt e sem dependências do pacote, no molde de
``opendss_line_mode``. Quem consome o valor são os três pontos que montam os
comandos de solução — o master exportado, o master da alocação e o fluxo de
potência interno.

**Por que o teto de iterações precisa ser configurável.** O padrão do OpenDSS é
``MaxIter=15``, e ele é o teto da iteração de **fluxo de potência**, não o do
laço de controle (esse é o ``MaxControlIter``, já elevado para 100 em
``opendss_export``). Num alimentador longo e carregado o algoritmo de ponto fixo
precisa de bem mais: num caso medido de 23.857 barras em 34,5 kV, o patamar de
madrugada exigiu 49 iterações e o da noite, 151.

Estourar o teto não é um aviso qualquer. A solução é abandonada **antes de
terminar a primeira passada**, então o laço de controle nunca roda: os
reguladores não comutam, os taps ficam onde estavam e as grandezas lidas
descrevem um circuito que não existe. Elevá-lo custa pouco — no mesmo caso, os
quatro patamares levam 0,60 s com teto de 500, e um circuito que divirja de
verdade leva 1,26 s para ser declarado inconvergente.

**O que aquela medição não diz, e precisa ser dito.** As 49 e 151 iterações
foram medidas com os limites de tensão das cargas no padrão do OpenDSS
(``Vminpu=0,95``), em que toda carga abaixo de 0,95 pu vira impedância
constante. Rebaixar o ``Vminpu`` mantém a carga em potência constante bem mais
fundo, e com isso o alimentador pode simplesmente **passar do seu limite de
carregamento**: no mesmo caso, com ``Vminpu=0,7``, dois dos quatro patamares
deixam de ter solução e não convergem nem com 20.000 iterações — a tensão
mínima oscila entre as tentativas em vez de se aproximar de um valor.

A conclusão prática é que este teto resolve **um** dos dois modos de falha. Ele
não distingue "faltou iteração" de "não existe solução", e por isso o relatório
do fluxo acompanha cada patamar reprovado da tensão mínima que ele alcançou
(ver ``StepVoltages`` em ``opendss_powerflow``): é o número que separa os dois
casos para quem lê.
"""

from __future__ import annotations


# Folgado de propósito, pela mesma razão de ``MAX_CONTROL_ITER = 100``: o custo
# de uma iteração a mais é desprezível perto do de um resultado silenciosamente
# errado. Dá 3,3x de margem sobre o pior patamar já medido.
DEFAULT_MAX_POWER_FLOW_ITER = 500

# O piso é o próprio padrão do OpenDSS: abaixo dele a configuração só pioraria o
# que já existe. O teto é generoso porque quem o alcança já está diagnosticando
# uma rede difícil, e a espera é o preço de descobrir que ela não converge.
MAX_POWER_FLOW_ITER_RANGE = (15, 10_000)


def parse_max_power_flow_iterations(value: object) -> int:
    """Converte um valor persistido; ausência ou corrupção usam o padrão.

    Nunca levanta, como os demais ``parse_*`` de preferência: um valor corrompido
    — de uma versão anterior, de edição manual do registro — não pode impedir a
    aplicação de abrir.
    """

    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_POWER_FLOW_ITER
    low, high = MAX_POWER_FLOW_ITER_RANGE
    if not low <= parsed <= high:
        return DEFAULT_MAX_POWER_FLOW_ITER
    return parsed


__all__ = [
    "DEFAULT_MAX_POWER_FLOW_ITER",
    "MAX_POWER_FLOW_ITER_RANGE",
    "parse_max_power_flow_iterations",
]
