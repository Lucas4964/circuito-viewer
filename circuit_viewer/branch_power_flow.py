"""Medição da potência de cada ramal no seu elemento de conexão com o tronco.

Camada de núcleo: não importa Qt nem ``py_dss_interface``. Consome um
:class:`~circuit_viewer.opendss_powerflow.PowerFlowResult` já resolvido e
devolve os mesmos ``EquivalentLoadPatternRecord`` que a agregação por tabelas
produziria, de modo que ``equivalent_network`` não precise conhecer o OpenDSS.

**Qual elemento é medido.** O primeiro elemento do ramal — chave ou trecho de
rede — é o que liga o ramal ao tronco. Ele é identificado sem estrutura nova:
``BranchRecord.bar_indices`` guarda **só** as barras a jusante, então todo
trecho do ramal com um extremo fora dessa lista toca o tronco. Com uma única
conexão esse conjunto é exatamente ``{first_segment_index}``; havendo mais de
uma (``trunk_connection_count > 1``), a potência entra por vários pontos e as
parcelas são somadas — medir só a primeira subestimaria o ramal em silêncio.

**Sinal e orientação.** ``SegmentPowers`` traz a potência do **terminal 1**, que
é sempre o ``Bus1`` da ``Line``, isto é, a barra ``start`` do trecho. Quando o
extremo de tronco é a barra ``start``, o terminal 1 está do lado do tronco e a
potência já entra positiva no ramal. Quando é a barra ``end``, o terminal 1 está
a jusante e o valor é negado.

Ler sempre o terminal 1 tem um preço conhecido: no caso negado a medição fica do
lado de dentro do elemento e **exclui as perdas dele**. A diferença é de um vão
só — nula quando o primeiro elemento é uma chave, porque ``Switch=Yes`` zera os
parâmetros elétricos da linha — e em troca o valor do ramal é exatamente o que o
painel de fluxo de potência já mostra para aquele trecho, sem uma segunda
leitura que pudesse divergir dele.

**Corrente.** Do mesmo elemento sai a corrente medida, em ampères, que o motor
resolveu com a tensão real daquele ponto. Ela não é derivável das potências
daqui: ``S/V`` exigiria a tensão da barra, e usar a nominal subestimaria a
corrente na exata proporção da queda — justamente onde a queda importa. Por isso
a medição a devolve à parte, em vez de deixar a rede equivalente estimá-la.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, localcontext
import math

from .branch_analysis import BranchAnalysisResult
from .equivalent_network import PHASE_COLUMNS, EquivalentLoadPatternRecord
from .opendss_export import LOAD_PATTERN_COUNT, phase_letters_by_node
from .opendss_powerflow import PowerFlowResult


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]

def _accumulate_currents(
    readings,  # noqa: ANN001 - SegmentCurrents, sem importar o módulo do OpenDSS
    letters_by_node: dict[int, str],
    totals: list[dict[str, complex]],
    *,
    negate: bool,
    phasor: bool,
) -> bool:
    """Soma a corrente de um vão de conexão. ``False`` quando não dá para ler.

    ``phasor`` só é exigido quando há mais de um vão: com um único, o módulo
    basta, e ele existe mesmo em leituras que não guardaram o ângulo. Devolver
    ``False`` deixa o ramal sem corrente, não sem medição — a potência segue
    válida.
    """

    if readings is None:
        return False
    if len(readings.magnitudes) != len(totals):
        return False
    if phasor and len(readings.angles) != len(totals):
        return False
    for npat, row in enumerate(readings.magnitudes):
        if len(row) != len(readings.nodes):
            return False
        angles = readings.angles[npat] if phasor else None
        for offset, node in enumerate(readings.nodes):
            letter = letters_by_node.get(int(node))
            if letter is None or letter not in totals[npat]:
                continue
            magnitude = float(row[offset])
            if angles is None:
                totals[npat][letter] += complex(magnitude, 0.0)
                continue
            angle = math.radians(float(angles[offset]) + (180.0 if negate else 0.0))
            totals[npat][letter] += complex(
                magnitude * math.cos(angle),
                magnitude * math.sin(angle),
            )
    return True


def measure_branch_powers(
    branches: BranchAnalysisResult,
    power_flow: PowerFlowResult,
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[
    dict[int, tuple[EquivalentLoadPatternRecord, ...]],
    dict[int, str],
    dict[int, Decimal],
]:
    """Devolve ``(patamares, motivo da falha, maior corrente)`` por ``RAMAL_ID``.

    Um ramal aparece em exatamente um dos dois primeiros mapeamentos. O de
    falhas alimenta os diagnósticos da rede equivalente: um ramal sem medição
    fica com equivalência incompleta, como já acontece com a agregação por
    tabelas.

    O terceiro é a maior corrente de fase entre os quatro patamares, em
    ampères, e é **opcional**: um ramal medido cuja corrente não pôde ser lida
    fica de fora dele sem virar falha, porque a potência — que é o que a
    equivalência exige — continua válida.
    """

    catalog = branches.source_catalog
    if catalog is None:
        raise ValueError("A análise de ramais não informa seu catálogo de origem.")
    if power_flow.catalog is not catalog:
        raise ValueError("O fluxo de potência pertence a outro catálogo de circuitos.")
    configuration = branches.phase_configuration
    if configuration is None:
        raise ValueError("A análise de ramais não informa sua configuração de fases.")
    if power_flow.phase_configuration is not configuration:
        raise ValueError("O fluxo de potência usa outra configuração de fases.")
    if power_flow.step_count != LOAD_PATTERN_COUNT:
        raise ValueError(
            "O fluxo de potência deve trazer os quatro patamares para alimentar "
            "as cargas equivalentes."
        )

    segments = catalog.segments
    letters_by_node = phase_letters_by_node(configuration)
    solved = set(power_flow.solved_circuits)
    unconverged = {circuit_id for circuit_id, _ in power_flow.unconverged}
    inspected = 0

    def cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    def inspect() -> None:
        nonlocal inspected
        inspected += 1
        if inspected % 4_096 == 0 and cancelled():
            raise InterruptedError("Medição das potências dos ramais cancelada.")

    patterns: dict[int, tuple[EquivalentLoadPatternRecord, ...]] = {}
    failures: dict[int, str] = {}
    currents: dict[int, Decimal] = {}
    total = len(branches.records)

    for position, branch in enumerate(branches.records, start=1):
        if cancelled():
            raise InterruptedError("Medição das potências dos ramais cancelada.")
        failure: str | None = None
        if branch.circuit_id not in solved:
            failure = (
                f"O circuito {branch.circuit_id} não foi resolvido pelo fluxo de "
                "potência."
            )
        elif branch.circuit_id in unconverged:
            failure = (
                f"O fluxo de potência não convergiu no circuito "
                f"{branch.circuit_id}."
            )

        connections: list[tuple[int, bool]] = []
        if failure is None:
            downstream = {int(value) for value in branch.bar_indices}
            for raw_index in branch.segment_indices:
                inspect()
                segment_index = int(raw_index)
                start_at_trunk = (
                    int(segments.start_indices[segment_index]) not in downstream
                )
                end_at_trunk = (
                    int(segments.end_indices[segment_index]) not in downstream
                )
                if start_at_trunk and end_at_trunk:
                    failure = (
                        f"O trecho {segments.segment_ids[segment_index]} liga duas "
                        "barras do tronco; a conexão do ramal é ambígua."
                    )
                    break
                if start_at_trunk:
                    connections.append((segment_index, False))
                elif end_at_trunk:
                    connections.append((segment_index, True))
            if failure is None and not connections:
                failure = "O ramal não possui elemento de conexão com o tronco."

        if failure is None:
            with localcontext() as context:
                context.prec = 50
                totals = [
                    {letter: [Decimal(0), Decimal(0)] for letter in PHASE_COLUMNS}
                    for _ in range(LOAD_PATTERN_COUNT)
                ]
                # A corrente é somada como fasor, não como módulo: entrando o
                # ramal por dois vãos, somar módulos daria um número que não
                # existe. O ângulo vem de graça na mesma leitura do motor.
                current_totals = [
                    {letter: 0j for letter in PHASE_COLUMNS}
                    for _ in range(LOAD_PATTERN_COUNT)
                ]
                current_known = True
                for segment_index, negate in connections:
                    powers = power_flow.segment_powers.get(segment_index)
                    element_id = segments.segment_ids[segment_index]
                    if powers is None:
                        failure = (
                            f"O elemento {element_id} não teve potência medida pelo "
                            "fluxo de potência."
                        )
                        break
                    if (
                        len(powers.active) != LOAD_PATTERN_COUNT
                        or len(powers.reactive) != LOAD_PATTERN_COUNT
                    ):
                        failure = (
                            f"O elemento {element_id} não tem potência nos quatro "
                            "patamares."
                        )
                        break
                    letters = [letters_by_node.get(int(node)) for node in powers.nodes]
                    missing = next(
                        (
                            int(node)
                            for node, letter in zip(powers.nodes, letters, strict=True)
                            if letter is None
                        ),
                        None,
                    )
                    if missing is not None:
                        failure = (
                            f"O nó {missing} do elemento {element_id} não corresponde "
                            "a nenhuma fase de fases2.json."
                        )
                        break
                    current_known = current_known and _accumulate_currents(
                        power_flow.segment_currents.get(segment_index),
                        letters_by_node,
                        current_totals,
                        negate=negate,
                        phasor=len(connections) > 1,
                    )
                    for npat in range(LOAD_PATTERN_COUNT):
                        active_row = powers.active[npat]
                        reactive_row = powers.reactive[npat]
                        if len(active_row) != len(letters) or len(reactive_row) != len(
                            letters
                        ):
                            failure = (
                                f"O elemento {element_id} devolveu potências em "
                                "quantidade diferente dos seus nós."
                            )
                            break
                        for offset, letter in enumerate(letters):
                            inspect()
                            active = Decimal(str(active_row[offset]))
                            reactive = Decimal(str(reactive_row[offset]))
                            if negate:
                                active = -active
                                reactive = -reactive
                            totals[npat][letter][0] += active
                            totals[npat][letter][1] += reactive
                    if failure is not None:
                        break
                if failure is None:
                    load_id = f"RAMAL-{branch.branch_id}"
                    patterns[branch.branch_id] = tuple(
                        EquivalentLoadPatternRecord(
                            load_id,
                            npat,
                            **{
                                PHASE_COLUMNS[letter][0]: totals[npat][letter][0]
                                for letter in PHASE_COLUMNS
                            },
                            **{
                                PHASE_COLUMNS[letter][1]: totals[npat][letter][1]
                                for letter in PHASE_COLUMNS
                            },
                        )
                        for npat in range(LOAD_PATTERN_COUNT)
                    )
                    if current_known:
                        currents[branch.branch_id] = Decimal(
                            repr(
                                max(
                                    abs(value)
                                    for row in current_totals
                                    for value in row.values()
                                )
                            )
                        )

        if failure is not None:
            failures[branch.branch_id] = failure
        if progress is not None:
            progress(position, total)

    if cancelled():
        raise InterruptedError("Medição das potências dos ramais cancelada.")
    return patterns, failures, currents


__all__ = ["measure_branch_powers"]
