"""Demanda D e potência de GD G de cada UC a partir da energia do mês.

Camada de núcleo: não importa Qt nem toca o disco. É a porta do ``calcular_dg.py``
para dentro do programa, e é dela que o modo de cargas Energia tira a potência de
cada carga do secundário dos trafos.

**O problema.** Para cada UC com GD, achar D (demanda máxima, kW) e G (potência da
GD, kW) tais que, em 30 dias do dia típico::

    Σ max(D·c(t) − G·g(t), 0) = CONSUMO       (energia importada da rede)
    Σ max(G·g(t) − D·c(t), 0) = GERACAO_KWH   (energia injetada)

``c`` é a curva de carga e ``g`` a de geração, as duas em pu do **máximo**. As duas
somas são homogêneas de grau 1 em (D, G): com ``r = G/D``, a razão injetada/consumida
só depende de ``r`` e é monótona, então uma bissecção acha ``r`` e o consumo fixa D.
Sem GD, D = CONSUMO / Σc.

**A escolha das curvas** segue o Interplan, como o script:

- BT: categoria de consumo da CLASSE da UC com ``MIN <= CONSUMO < MAX``; entre as
  tipologias da faixa vale o sorteio determinístico (``DM_SORTCUR = 0``): semente
  zerada a cada ponto de serviço, UCs do PS em ID decrescente, gerador congruente
  do Delphi e tipologia pela ``PART_MERC`` acumulada. Faixa com uma tipologia só
  não consome sorteio; com exatamente uma GD e consumo ou geração zerados, há um
  sorteio a mais antes (a curva líquida recusada).
- MT (A4): CLASSE -1, a tipologia 1 da faixa em kW, que começa pela demanda média
  e é refeita com o D calculado até estabilizar.
- GD: a categoria do ``CURVA_ID``, se houver; senão a fonte (TIPO ``F``) com
  CLASSE igual ao ``TIPOGD``.
- IP: a categoria de iluminação pública (CLASSE 5), com D = ``DEMANDA`` ×
  ``NLAMP``. A ``DEMANDA`` é por lâmpada: é assim que o ``KWH_IP`` do
  ``CIRCUITO_INFO`` fecha (Σ DEMANDA·NLAMP × 12 h × 30 dias).

**Diferenças conscientes em relação ao script.** O ``CURVA_ID`` da GD é casado com o
``CAT_ID`` (o ``link_id`` das curvas), não com o ``CUR_ID``; uma ET e um EP na mesma
CARGA formam um trafo só. As contas usam o dia típico × 30, que dá as mesmas
somas das 720 horas concatenadas.

**Os patamares.** No patamar, a carga vale D·c(``HORARIO_REF``) e a GD,
G·g(``HORARIO_REF``), com a curva média (``PERIODO`` 4) — a mesma com que D e G foram
calculados. A reativa da carga sai do ``FAT_POT`` da categoria; a GD tem FP 1.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .calculation_levels import CalculationLevelSchedule
from .curvas import Curve
from .curvas_categorias import (
    CATEGORY_KIND_LOAD,
    CATEGORY_KIND_SOURCE,
    CLASS_A4,
    CLASS_PUBLIC_LIGHTING,
    CurveCategory,
    category_sort_key,
)
from .generator_update import curve_value_at_reference
from .model import CircuitCatalogModel
from .transformer_consumers import (
    CONSUMER_KIND_BT,
    CONSUMER_KIND_IP,
    CONSUMER_KIND_MT,
    ConsumerRecord,
    GenerationRecord,
    TransformerConsumersModel,
)


DAYS = 30
MAX_A4_ROUNDS = 10
# Fator de potência da carga quando a categoria não informa o FAT_POT.
DEFAULT_POWER_FACTOR = 0.92
GENERATION_POWER_FACTOR = 1.0
# Casas do CONSUMO e da GERACAO_KWH: o Access as guarda em precisão simples, e
# 901,9500122… é o 901,95 que o cadastro de fato diz.
ENERGY_DECIMALS = 2

STATUS_ADJUSTED = "AJUSTADO"
STATUS_GENERATION_ONLY = "SO_GERACAO"
STATUS_FULL_SELF_CONSUMPTION = "AUTOCONSUMO_TOTAL"
STATUS_NO_ENERGY = "SEM_ENERGIA"
STATUS_NO_GD = "SEM_GD"
STATUS_ZERO_CONSUMPTION = "CONSUMO_ZERO"
STATUS_INACTIVE = "INATIVO"
STATUS_NO_CURVE = "SEM_CURVA"
STATUS_NO_GD_CURVE = "SEM_CURVA_GD"
STATUS_LIGHTING = "IP"

STATUS_LABELS = {
    STATUS_ADJUSTED: "GD com consumo e injeção: D e G resolvidos",
    STATUS_GENERATION_ONLY: "GD sem consumo: só a geração",
    STATUS_FULL_SELF_CONSUMPTION: "GD sem injeção: G = 0",
    STATUS_NO_ENERGY: "GD sem consumo e sem injeção",
    STATUS_NO_GD: "sem GD: D = CONSUMO / Σc",
    STATUS_ZERO_CONSUMPTION: "sem GD e sem consumo",
    STATUS_INACTIVE: "UC inativa: sem cálculo",
    STATUS_NO_CURVE: "classe ou faixa sem curva de carga",
    STATUS_NO_GD_CURVE: "tipo de GD sem curva de geração",
    STATUS_LIGHTING: "iluminação pública: D = DEMANDA × NLAMP",
}

CancelCheck = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class TypicalCurve:
    """A curva média de uma categoria, em pu do máximo."""

    category: CurveCategory
    pu: tuple[float, ...]

    @property
    def category_id(self) -> str:
        return self.category.category_id

    @property
    def power_factor(self) -> float:
        value = self.category.power_factor
        return DEFAULT_POWER_FACTOR if value is None else value


class CurveSelector:
    """As curvas médias do catálogo, na ordem do Interplan, e as regras de escolha."""

    def __init__(self, curves: Iterable[Curve], categories: Iterable[CurveCategory]) -> None:
        average: dict[str, Curve] = {}
        for curve in curves:
            if curve.period == "MED" and curve.link_id and curve.link_id not in average:
                average[curve.link_id] = curve
        typical: list[TypicalCurve] = []
        for category in sorted(categories, key=category_sort_key):
            curve = average.get(category.category_id)
            if curve is None:
                continue
            peak = max(curve.values)
            # Curva sem valor positivo não normaliza; o script também a deixa fora
            # da pilha, e isso muda o sorteio — por isso fica fora aqui.
            if peak <= 0:
                continue
            typical.append(TypicalCurve(category, tuple(value / peak for value in curve.values)))
        self.curves = tuple(typical)
        self.by_category = {item.category_id: item for item in typical}

    def __len__(self) -> int:
        return len(self.curves)

    def bt_range(self, consumer_class: int, consumption: float) -> list[TypicalCurve]:
        return [
            item
            for item in self.curves
            if item.category.kind == CATEGORY_KIND_LOAD
            and item.category.consumer_class == consumer_class
            and item.category.pu_max == 0
            and item.category.demand_type == 0
            and item.category.contains(consumption)
        ]

    def a4(self, demand_kw: float) -> TypicalCurve | None:
        """Tipologia 1 da faixa A4 que contém a demanda arredondada, como o Interplan."""

        value = float(round(demand_kw))
        for item in self.curves:
            if (
                item.category.kind == CATEGORY_KIND_LOAD
                and item.category.consumer_class == CLASS_A4
                and item.category.pu_max == 0
                and item.category.contains(value)
            ):
                return item
        return None

    def lighting(self) -> TypicalCurve | None:
        return next(
            (
                item
                for item in self.curves
                if item.category.kind == CATEGORY_KIND_LOAD
                and item.category.consumer_class == CLASS_PUBLIC_LIGHTING
            ),
            None,
        )

    def generation(self, generation_type: int | None, curve_id: str) -> TypicalCurve | None:
        if curve_id and curve_id != "0" and curve_id in self.by_category:
            return self.by_category[curve_id]
        candidates = [
            item
            for item in self.curves
            if item.category.kind == CATEGORY_KIND_SOURCE
            and item.category.consumer_class == generation_type
        ]
        default = [
            item
            for item in candidates
            if item.category.demand_type == 0 and item.category.pu_max == 0
        ]
        return (default or candidates or [None])[0]


class Sorteador:
    """Sorteio determinístico do Interplan (``DM_SORTCUR = 0``).

    ``System.Random`` do Delphi: gerador congruente de 32 bits
    ``s = s·0x08088405 + 1``; ``RandomRange(0, 10000)`` é a parte alta de
    ``s·10000``; o seletor divide por 100. A semente é zerada a cada ponto de
    serviço.
    """

    def __init__(self) -> None:
        self.seed = 0
        self.last: float | None = None

    def percent(self) -> float:
        self.seed = (self.seed * 0x08088405 + 1) & 0xFFFFFFFF
        self.last = ((self.seed * 10000) >> 32) / 100.0
        return self.last


def select(candidates: Sequence[TypicalCurve], drawer: Sorteador) -> TypicalCurve | None:
    """Tipologia pela ``PART_MERC`` acumulada; uma só não consome sorteio."""

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    drawn = drawer.percent()
    accumulated = 0.0
    for item in candidates:
        accumulated += item.category.market_share
        if drawn <= accumulated:
            return item
    # Participações somam menos que o sorteio: o produto devolve nada.
    return None


def energies(c: Sequence[float], g: Sequence[float], d: float, gg: float) -> tuple[float, float]:
    """(importada, injetada) de D·c − G·g num dia, em kWh (Δt = 1 h)."""

    imported = injected = 0.0
    for ci, gi in zip(c, g):
        value = d * ci - gg * gi
        if value > 0:
            imported += value
        else:
            injected -= value
    return imported, injected


@dataclass(frozen=True, slots=True)
class _Solution:
    d: float
    g: float | None
    status: str
    g_max: float | None = None


def resolve_dg(c: Sequence[float], g: Sequence[float], consumed: float, injected: float) -> _Solution:
    """D e G de uma UC com GD; ``c`` e ``g`` são o dia típico em pu do máximo."""

    daily_g = sum(g)
    if consumed <= 0 and injected <= 0:
        return _Solution(0.0, 0.0, STATUS_NO_ENERGY)
    if consumed <= 0:
        return _Solution(0.0, injected / (DAYS * daily_g), STATUS_GENERATION_ONLY)

    # r0: maior G/D sem injeção em nenhuma hora.
    r0 = min(ci / gi for ci, gi in zip(c, g) if gi > 0)
    daily_c = sum(c)
    if injected <= 0:
        rest = daily_c - r0 * daily_g
        g_max = r0 * consumed / (DAYS * rest) if rest > 0 else None
        return _Solution(consumed / (DAYS * daily_c), 0.0, STATUS_FULL_SELF_CONSUMPTION, g_max)

    ratio = injected / consumed

    def f(r: float) -> tuple[float, float]:
        imported, exported = energies(c, g, 1.0, r)
        return exported - ratio * imported, imported

    low = r0
    high = max(2 * r0, 1.0)
    while f(high)[0] <= 0:
        high *= 2
    for _ in range(200):
        middle = (low + high) / 2
        if f(middle)[0] > 0:
            high = middle
        else:
            low = middle
        if high - low <= 1e-14 * high:
            break
    r = (low + high) / 2
    d = consumed / (DAYS * f(r)[1])
    return _Solution(d, r * d, STATUS_ADJUSTED)


@dataclass(frozen=True, slots=True)
class ConsumerDemand:
    """O resultado de uma carga do secundário: UC da BT, da MT ou ponto de IP.

    ``load_levels``/``reactive_levels`` são P e Q nos quatro patamares (kW,
    kvar); ``generation_levels`` é a geração bruta da GD da UC nos patamares,
    positiva. Vazios quando a UC não tem potência a modelar.
    """

    consumer: ConsumerRecord
    status: str
    demand_kw: float | None = None
    generation_kw: float | None = None
    max_generation_kw: float | None = None
    load_curve: TypicalCurve | None = None
    generation_curve: TypicalCurve | None = None
    draw_percent: float | None = None
    consumed_kwh: float | None = None
    injected_kwh: float | None = None
    total_load_kwh: float | None = None
    gross_generation_kwh: float | None = None
    error_kwh: float | None = None
    note: str = ""
    load_levels: tuple[float, ...] = ()
    reactive_levels: tuple[float, ...] = ()
    generation_levels: tuple[float, ...] = ()

    @property
    def power_factor(self) -> float:
        return DEFAULT_POWER_FACTOR if self.load_curve is None else self.load_curve.power_factor


@dataclass(frozen=True, slots=True)
class GeneratorDemand:
    """A parte de um gerador na GD da sua UC, em kW e nos patamares."""

    generator: GenerationRecord
    generation_kw: float
    levels: tuple[float, ...]
    curve: TypicalCurve | None = None


@dataclass(frozen=True, slots=True)
class TransformerDemand:
    load_index: int
    consumers: tuple[ConsumerDemand, ...]
    generators: tuple[GeneratorDemand, ...] = ()
    schedule: CalculationLevelSchedule | None = None

    @property
    def modeled(self) -> bool:
        """Há alguma carga ou GD com potência para o secundário."""

        return any(item.load_levels for item in self.consumers) or bool(self.generators)


@dataclass(frozen=True, slots=True)
class EnergyDemandModel:
    """Retrato imutável do cálculo, na ordem das cargas do modelo de UCs."""

    consumers: TransformerConsumersModel
    transformers: tuple[TransformerDemand | None, ...]
    issues: tuple[str, ...] = ()
    curve_count: int = 0

    def __post_init__(self) -> None:
        if len(self.transformers) != len(self.consumers.records):
            raise ValueError("Cada carga deve ter um resultado (ou None).")

    def transformer(self, load_index: int) -> TransformerDemand | None:
        return self.transformers[int(load_index)]

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for transformer in self.transformers:
            if transformer is None:
                continue
            for item in transformer.consumers:
                counts[item.status] += 1
        return dict(counts)


def _energy(value: float | None) -> float:
    return 0.0 if value is None else round(value, ENERGY_DECIMALS)


def _consumer_order(consumer: ConsumerRecord) -> int:
    try:
        return int(consumer.consumer_id)
    except ValueError:
        return 0


def _bt_curves(
    consumers: Sequence[ConsumerRecord],
    generators_by_consumer: dict[tuple[str, str], list[GenerationRecord]],
    selector: CurveSelector,
) -> dict[str, tuple[TypicalCurve | None, float | None]]:
    """Curva de carga de cada UC da BT pelo sorteio determinístico, PS a PS."""

    by_point: dict[str, list[ConsumerRecord]] = defaultdict(list)
    for consumer in consumers:
        if consumer.kind == CONSUMER_KIND_BT:
            by_point[consumer.service_point_id].append(consumer)
    chosen: dict[str, tuple[TypicalCurve | None, float | None]] = {}
    for point_consumers in by_point.values():
        drawer = Sorteador()
        for consumer in sorted(point_consumers, key=lambda item: -_consumer_order(item)):
            if consumer.consumer_class is None:
                chosen[consumer.consumer_id] = (None, None)
                continue
            consumption = _energy(consumer.consumption_kwh)
            candidates = selector.bt_range(consumer.consumer_class, consumption)
            generators = generators_by_consumer.get((CONSUMER_KIND_BT, consumer.consumer_id), [])
            if len(generators) == 1 and (
                consumption <= 0 or _energy(generators[0].generation_kwh) <= 0
            ):
                select(candidates, drawer)
            drawer.last = None
            chosen[consumer.consumer_id] = (select(candidates, drawer), drawer.last)
    return chosen


def _levels(
    value: float | None,
    curve: TypicalCurve | None,
    schedule: CalculationLevelSchedule | None,
) -> tuple[float, ...]:
    if value is None or curve is None or schedule is None or value <= 0:
        return ()
    shape = Curve("forma", "forma", curve.pu)
    return tuple(
        value * curve_value_at_reference(shape, schedule.level(row).reference_hour)
        for row in range(len(schedule))
    )


def _reactive(levels: Sequence[float], power_factor: float) -> tuple[float, ...]:
    factor = math.tan(math.acos(power_factor))
    return tuple(value * factor for value in levels)


def _consumer_demand(
    consumer: ConsumerRecord,
    generators: Sequence[GenerationRecord],
    selector: CurveSelector,
    bt_choice: tuple[TypicalCurve | None, float | None] | None,
    schedule: CalculationLevelSchedule | None,
) -> ConsumerDemand:
    if consumer.kind == CONSUMER_KIND_IP:
        curve = selector.lighting()
        lamps = consumer.lamp_count if consumer.lamp_count and consumer.lamp_count > 0 else 1
        demand = (consumer.demand_kw or 0.0) * lamps
        if curve is None:
            return ConsumerDemand(consumer, STATUS_NO_CURVE, note="sem curva de iluminação pública")
        levels = _levels(demand, curve, schedule)
        return ConsumerDemand(
            consumer,
            STATUS_LIGHTING,
            demand_kw=demand,
            load_curve=curve,
            total_load_kwh=demand * DAYS * sum(curve.pu),
            load_levels=levels,
            reactive_levels=_reactive(levels, curve.power_factor),
        )

    if not consumer.active:
        return ConsumerDemand(consumer, STATUS_INACTIVE)

    notes: list[str] = []
    consumed = max(_energy(consumer.consumption_kwh), 0.0)
    injected = sum(_energy(item.generation_kwh) for item in generators)

    generation_curve = None
    if generators:
        if len({item.generation_type for item in generators}) > 1:
            notes.append("GDs de tipos diferentes: usada a curva da primeira")
        generation_curve = selector.generation(generators[0].generation_type, generators[0].curve_id)
        if generation_curve is None:
            return ConsumerDemand(consumer, STATUS_NO_GD_CURVE, note="; ".join(notes))
    g = generation_curve.pu if generation_curve is not None else None

    def solve(curve: TypicalCurve | None) -> tuple[_Solution, tuple[float, ...] | None]:
        c = curve.pu if curve is not None else None
        if not generators:
            if consumed <= 0:
                return _Solution(0.0, None, STATUS_ZERO_CONSUMPTION), c
            return _Solution(consumed / (DAYS * sum(c)), None, STATUS_NO_GD), c
        if c is None:  # só com consumo zerado: a carga não entra na conta
            c = (0.0,) * len(g)
        return resolve_dg(c, g, consumed, injected), c

    needs_load = consumed > 0
    draw = None
    if consumer.kind == CONSUMER_KIND_BT:
        load_curve, draw = bt_choice if bt_choice is not None else (None, None)
    elif needs_load:
        # A4: faixa em kW, começa pela demanda média e segue o D calculado.
        load_curve = selector.a4(consumed / (DAYS * 24))
        seen: list[TypicalCurve] = []
        for _ in range(MAX_A4_ROUNDS):
            if load_curve is None:
                break
            solution, _ = solve(load_curve)
            new = selector.a4(solution.d)
            if new is None or new is load_curve:
                break
            if new in seen:
                notes.append("FAIXA_A4_INSTAVEL")
                break
            seen.append(load_curve)
            load_curve = new
        notes.append("A4: tipologia 1 da faixa (aproximação do sorteio)")
    else:
        load_curve = None

    if needs_load and load_curve is None:
        return ConsumerDemand(
            consumer,
            STATUS_NO_CURVE,
            generation_curve=generation_curve,
            draw_percent=draw,
            note="; ".join(notes),
        )

    solution, c = solve(load_curve if needs_load else None)
    d, gg = solution.d, solution.g
    total_load = gross = imported = exported = error = None
    if c is not None:
        total_load = d * DAYS * sum(c)
    if g is not None:
        daily_imported, daily_exported = energies(c, g, d, gg)
        imported, exported = DAYS * daily_imported, DAYS * daily_exported
        gross = gg * DAYS * sum(g)
        error = max(abs(imported - consumed), abs(exported - injected))
    elif c is not None:
        imported = total_load
        error = abs(total_load - consumed)
    effective_curve = load_curve if needs_load else None
    levels = _levels(d, effective_curve, schedule)
    return ConsumerDemand(
        consumer,
        solution.status,
        demand_kw=d,
        generation_kw=gg,
        max_generation_kw=solution.g_max,
        load_curve=effective_curve,
        generation_curve=generation_curve,
        draw_percent=draw,
        consumed_kwh=imported,
        injected_kwh=exported,
        total_load_kwh=total_load,
        gross_generation_kwh=gross,
        error_kwh=error,
        note="; ".join(notes),
        load_levels=levels,
        reactive_levels=_reactive(levels, effective_curve.power_factor) if levels else (),
        generation_levels=_levels(gg, generation_curve, schedule),
    )


def _generator_shares(
    demand: ConsumerDemand,
    generators: Sequence[GenerationRecord],
) -> list[GeneratorDemand]:
    """A G da UC repartida entre os geradores pela energia de cada um."""

    if not generators or not demand.generation_kw or not demand.generation_levels:
        return []
    weights = [_energy(item.generation_kwh) for item in generators]
    total = sum(weights)
    if total <= 0:
        weights, total = [1.0] * len(generators), float(len(generators))
    return [
        GeneratorDemand(
            generator=generator,
            generation_kw=demand.generation_kw * weight / total,
            levels=tuple(value * weight / total for value in demand.generation_levels),
            curve=demand.generation_curve,
        )
        for generator, weight in zip(generators, weights)
    ]


def calculate_energy_demands(
    consumers: TransformerConsumersModel,
    curves: Iterable[Curve],
    categories: Iterable[CurveCategory],
    schedules: Sequence[CalculationLevelSchedule | None],
    *,
    cancel_check: CancelCheck | None = None,
) -> EnergyDemandModel:
    """D, G e as potências nos patamares de todas as cargas dos secundários.

    ``schedules`` traz a agenda de patamares de cada carga, na ordem das cargas
    (veja :func:`schedules_for_loads`); ``None`` deixa a carga sem patamares,
    com D e G ainda calculados.
    """

    selector = CurveSelector(curves, categories)
    issues: list[str] = []
    if not len(selector):
        issues.append(
            "Nenhuma curva média (MED) com categoria: importe o banco de curvas na "
            "janela Curvas."
        )
    if len(schedules) != len(consumers.records):
        raise ValueError("Informe uma agenda (ou None) para cada carga.")

    results: list[TransformerDemand | None] = []
    for load_index, record in enumerate(consumers.records):
        if cancel_check is not None and load_index % 256 == 0 and cancel_check():
            raise InterruptedError("Cálculo do modo Energia cancelado.")
        if record.transformer is None or not (record.consumers or record.generators):
            results.append(None)
            continue
        schedule = schedules[load_index]
        generators_by_consumer: dict[tuple[str, str], list[GenerationRecord]] = defaultdict(list)
        for generator in record.generators:
            generators_by_consumer[(generator.kind, generator.consumer_id)].append(generator)
        bt_choices = _bt_curves(record.consumers, generators_by_consumer, selector)
        demands: list[ConsumerDemand] = []
        shares: list[GeneratorDemand] = []
        for consumer in record.consumers:
            generators = generators_by_consumer.get((consumer.kind, consumer.consumer_id), [])
            demand = _consumer_demand(
                consumer,
                generators,
                selector,
                bt_choices.get(consumer.consumer_id) if consumer.kind == CONSUMER_KIND_BT else None,
                schedule,
            )
            demands.append(demand)
            shares.extend(_generator_shares(demand, generators))
        results.append(
            TransformerDemand(load_index, tuple(demands), tuple(shares), schedule)
        )
    return EnergyDemandModel(
        consumers=consumers,
        transformers=tuple(results),
        issues=tuple(issues),
        curve_count=len(selector),
    )


def schedules_for_loads(
    consumers: TransformerConsumersModel,
    catalog: CircuitCatalogModel | None,
    schedules: Sequence[CalculationLevelSchedule],
    default: CalculationLevelSchedule | None = None,
) -> tuple[CalculationLevelSchedule | None, ...]:
    """A agenda de cada carga: a do circuito dono da barra, ou ``default``.

    Uma barra de mais de um circuito fica com o primeiro, como na atualização
    dos geradores.
    """

    loads = consumers.loads
    if catalog is None:
        return (default,) * len(loads)
    if catalog.segments.bars is not loads.bars:
        raise ValueError("Cargas e circuitos devem usar as mesmas barras.")
    if len(schedules) != len(catalog):
        raise ValueError("Informe uma agenda para cada circuito.")
    owner: dict[int, int] = {}
    for circuit_index, membership in enumerate(catalog.memberships):
        for bar_index in membership.bar_indices:
            owner.setdefault(int(bar_index), circuit_index)
    return tuple(
        schedules[owner[int(bar)]] if int(bar) in owner else default
        for bar in loads.bar_indices
    )
