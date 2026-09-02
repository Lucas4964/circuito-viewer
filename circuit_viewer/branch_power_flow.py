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
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, localcontext

from .branch_analysis import BranchAnalysisResult
from .equivalent_network import EquivalentLoadPatternRecord
from .opendss_export import LOAD_PATTERN_COUNT, phase_letters_by_node
from .opendss_powerflow import PowerFlowResult


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]

# Ordem dos campos de EquivalentLoadPatternRecord, por letra de fase.
_ACTIVE_FIELD = {"D": "pd", "E": "pe", "F": "pf"}
_REACTIVE_FIELD = {"D": "qd", "E": "qe", "F": "qf"}


def measure_branch_powers(
    branches: BranchAnalysisResult,
    power_flow: PowerFlowResult,
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[
    dict[int, tuple[EquivalentLoadPatternRecord, ...]],
    dict[int, str],
]:
    """Devolve ``(patamares por RAMAL_ID, motivo da falha por RAMAL_ID)``.

    Um ramal aparece em exatamente um dos dois mapeamentos. O segundo alimenta
    os diagnósticos da rede equivalente: um ramal sem medição fica com
    equivalência incompleta, como já acontece com a agregação por tabelas.
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
                    {letter: [Decimal(0), Decimal(0)] for letter in _ACTIVE_FIELD}
                    for _ in range(LOAD_PATTERN_COUNT)
                ]
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
                                _ACTIVE_FIELD[letter]: totals[npat][letter][0]
                                for letter in _ACTIVE_FIELD
                            },
                            **{
                                _REACTIVE_FIELD[letter]: totals[npat][letter][1]
                                for letter in _REACTIVE_FIELD
                            },
                        )
                        for npat in range(LOAD_PATTERN_COUNT)
                    )

        if failure is not None:
            failures[branch.branch_id] = failure
        if progress is not None:
            progress(position, total)

    if cancelled():
        raise InterruptedError("Medição das potências dos ramais cancelada.")
    return patterns, failures


__all__ = ["measure_branch_powers"]
