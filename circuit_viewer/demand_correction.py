"""Correção de demanda: um fator de P e um de Q por patamar, contra a medição.

Camada de núcleo: não importa Qt, nem ``py_dss_interface``, nem o exportador. O
exportador importa este módulo para aplicar a correção, e o adaptador do motor
(:mod:`circuit_viewer.opendss_demand_correction`) a calcula; os dois dependem
daqui, e daqui não se depende de nenhum dos dois.

**O problema.** Dados ``P`` e ``Q`` medidos na saída do alimentador num patamar,
achar ``fP`` e ``fQ`` tais que, multiplicando o P das cargas do escopo por ``fP``
e o Q por ``fQ``, a saída calculada pelo OpenDSS bata com a medição. Os dois
fatores são **independentes**: cada um é ajustado pelo seu próprio resíduo, e só
o fluxo os acopla (as perdas reativas dependem do P, a tensão de ambos).

**O método** é o ponto fixo multiplicativo do ``CORRECAO_DEMANDA`` (PQ3F,
``correcao.py``)::

    f ← f · (alvo − fixo) / (calculado − fixo)

``fixo`` é o que não escala: geração, capacitores e as cargas fora do escopo. A
referência usava o Q **nominal** dos capacitores e a GD declarada; aqui ``fixo``
é **medido** no próprio solve, então um capacitor (impedância constante) entra
com o Q que de fato injeta na tensão daquele patamar. As perdas não são
separadas: ficam em ``calculado − fixo`` e são tratadas como se escalassem com a
carga, e as iterações absorvem o erro dessa hipótese.

**Duas diferenças conscientes em relação à referência:**

- a tolerância é fixa. A referência a relaxava para 5% e depois 10%, e o seu
  ``VALIDACAO.md`` mostra o efeito: um alvo inviável era "aceito" com resíduo de
  milhares por cento. Aqui o status sai sempre do resíduo real;
- ``fP ≤ 0`` interrompe o patamar: o fixo já explica mais que o alvo e só uma
  carga negativa fecharia a conta, o que é sinal de medição ou GD errada, não de
  demanda. ``fQ ≤ 0`` é permitido — um alimentador capacitivo pode exigi-lo —,
  mas sai com aviso.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Mapping, Sequence

# Os quatro NPAT do Interplan. O exportador tem a mesma constante
# (``LOAD_PATTERN_COUNT``); ela não é importada daqui para baixo porque o
# exportador é quem importa este módulo.
PATAMAR_COUNT = 4

DEFAULT_RELATIVE_TOLERANCE = 0.001
DEFAULT_ABSOLUTE_TOLERANCE = 0.5
DEFAULT_MAX_ITERATIONS = 50

# Mesmo piso do denominador da referência (``EPS_DENOM``, kW ou kvar).
_DENOMINATOR_EPSILON = 1e-3


class DemandScope(str, Enum):
    """Grupo de cargas que um fator pode escalar.

    ``CARGA`` é a carga agregada do Interplan (``<CODIGO>-<N>F-<L>``), que
    existe nos dois modos — no modo Energia, para a CARGA cujo trafo não pôde
    ser montado. Os outros três só existem no modo Energia.
    """

    CARGA = "carga"
    UC_BT = "uc_bt"
    UC_MT = "uc_mt"
    IP = "ip"

    @property
    def label(self) -> str:
        return _SCOPE_LABELS[self]


_SCOPE_LABELS = {
    DemandScope.CARGA: "Cargas (modelo Interplan)",
    DemandScope.UC_BT: "UCs de baixa tensão",
    DemandScope.UC_MT: "UCs de média tensão",
    DemandScope.IP: "Iluminação pública",
}

# Prefixo do nome da ``Load`` no modo Energia, como o exportador o escreve. O
# desempate de nomes do exportador só acrescenta sufixos, então o prefixo é
# estável.
ENERGY_NAME_PREFIXES: Mapping[DemandScope, str] = {
    DemandScope.UC_BT: "UC-BT-",
    DemandScope.UC_MT: "UC-MT-",
    DemandScope.IP: "IP-",
}

DEFAULT_SCOPE = frozenset({DemandScope.CARGA, DemandScope.UC_BT, DemandScope.UC_MT})


def energy_scope_for_kind(kind: str) -> DemandScope | None:
    """Grupo de uma UC do modo Energia pelo tipo (``BT``, ``MT`` ou ``IP``)."""

    return {
        "BT": DemandScope.UC_BT,
        "MT": DemandScope.UC_MT,
        "IP": DemandScope.IP,
    }.get(str(kind).strip().upper())


def energy_scope_for_name(name: str) -> DemandScope | None:
    """Grupo de uma ``Load`` do modo Energia pelo nome; ``None`` para a GD."""

    folded = name.casefold()
    for scope, prefix in ENERGY_NAME_PREFIXES.items():
        if folded.startswith(prefix.casefold()):
            return scope
    return None


@dataclass(frozen=True, slots=True)
class DemandCorrection:
    """Fatores aplicados a um circuito: ``(fP, fQ)`` por patamar.

    ``energy_mode`` registra a base sobre a qual os fatores foram calculados:
    eles multiplicam a potência daquele modo de cargas, e não valem no outro.
    """

    circuit_id: str
    scope: frozenset[DemandScope]
    factors: tuple[tuple[float, float], ...]
    energy_mode: bool = False

    def __post_init__(self) -> None:
        if len(self.factors) != PATAMAR_COUNT:
            raise ValueError(
                f"A correção precisa de {PATAMAR_COUNT} pares de fatores."
            )
        for active, reactive in self.factors:
            if not (math.isfinite(active) and math.isfinite(reactive)):
                raise ValueError("Os fatores da correção devem ser finitos.")
        if not self.scope:
            raise ValueError("A correção precisa de ao menos um grupo de cargas.")

    def applies_to(self, scope: DemandScope) -> bool:
        return scope in self.scope

    def scale(
        self,
        active: Sequence[float],
        reactive: Sequence[float],
    ) -> tuple[list[float], list[float]]:
        """Multiplica cada patamar pelo seu fator: ``P[k]·fP[k]``, ``Q[k]·fQ[k]``."""

        if len(active) != PATAMAR_COUNT or len(reactive) != PATAMAR_COUNT:
            raise ValueError(
                f"A correção vale para séries de {PATAMAR_COUNT} patamares."
            )
        return (
            [value * self.factors[step][0] for step, value in enumerate(active)],
            [value * self.factors[step][1] for step, value in enumerate(reactive)],
        )

    @property
    def is_identity(self) -> bool:
        return all(pair == (1.0, 1.0) for pair in self.factors)


DemandCorrections = Mapping[str, DemandCorrection]


def correction_for(
    corrections: DemandCorrections | None,
    circuit_id: str,
    scope: DemandScope,
) -> DemandCorrection | None:
    """A correção que vale para uma carga daquele circuito e grupo, se houver."""

    if not corrections:
        return None
    correction = corrections.get(circuit_id)
    if correction is None or not correction.applies_to(scope):
        return None
    return correction


@dataclass(frozen=True, slots=True)
class CorrectionSettings:
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE
    absolute_tolerance: float = DEFAULT_ABSOLUTE_TOLERANCE
    max_iterations: int = DEFAULT_MAX_ITERATIONS

    def __post_init__(self) -> None:
        if not (self.relative_tolerance >= 0.0 and self.absolute_tolerance >= 0.0):
            raise ValueError("As tolerâncias não podem ser negativas.")
        if self.relative_tolerance == 0.0 and self.absolute_tolerance == 0.0:
            raise ValueError("Ao menos uma tolerância deve ser positiva.")
        if type(self.max_iterations) is not int or self.max_iterations < 1:
            raise ValueError("O máximo de iterações deve ser um inteiro positivo.")

    def tolerance(self, target: float) -> float:
        return max(self.relative_tolerance * abs(target), self.absolute_tolerance)


@dataclass(frozen=True, slots=True)
class PatamarTarget:
    """Medição da saída num patamar; ``None`` deixa aquele fator em 1."""

    active: float | None = None
    reactive: float | None = None

    def __post_init__(self) -> None:
        for value in (self.active, self.reactive):
            if value is not None and not math.isfinite(value):
                raise ValueError("O alvo deve ser um número finito.")

    @property
    def is_empty(self) -> bool:
        return self.active is None and self.reactive is None


@dataclass(frozen=True, slots=True)
class HeadReading:
    """O que um solve diz da saída, em kW e kvar.

    ``fixed_*`` é a parcela que não escala com os fatores — geração,
    capacitores e cargas fora do escopo —, medida no mesmo solve.
    """

    active: float
    reactive: float
    fixed_active: float = 0.0
    fixed_reactive: float = 0.0
    converged: bool = True
    active_losses: float = 0.0
    reactive_losses: float = 0.0


class PatamarStatus(str, Enum):
    CONVERGED = "converged"
    NOT_CONVERGED = "not_converged"
    FLOW_DIVERGED = "flow_diverged"
    INVALID = "invalid"
    SKIPPED = "skipped"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    PatamarStatus.CONVERGED: "convergiu",
    PatamarStatus.NOT_CONVERGED: "não convergiu",
    PatamarStatus.FLOW_DIVERGED: "fluxo não convergiu",
    PatamarStatus.INVALID: "alvo inviável",
    PatamarStatus.SKIPPED: "sem alvo",
}


@dataclass(frozen=True, slots=True)
class PatamarCorrection:
    """Desfecho de um patamar.

    ``final`` é a leitura feita **com** ``active_factor``/``reactive_factor``: o
    último incremento não é aplicado quando o resíduo já basta, como na
    referência, para que o fluxo relatado seja o dos fatores relatados.
    """

    step: int
    target: PatamarTarget
    status: PatamarStatus
    active_factor: float = 1.0
    reactive_factor: float = 1.0
    iterations: int = 0
    initial: HeadReading | None = None
    final: HeadReading | None = None
    message: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def converged(self) -> bool:
        return self.status is PatamarStatus.CONVERGED

    @property
    def active_residual(self) -> float | None:
        if self.final is None or self.target.active is None:
            return None
        return self.final.active - self.target.active

    @property
    def reactive_residual(self) -> float | None:
        if self.final is None or self.target.reactive is None:
            return None
        return self.final.reactive - self.target.reactive


class _InvalidRatio(ValueError):
    pass


def _ratio(target: float, calculated: float, fixed: float) -> float:
    """``(alvo − fixo)/(calculado − fixo)``, com o recurso da referência.

    Denominador quase nulo é a parcela escalável sem potência: a razão simples
    ``alvo/calculado`` ainda dá uma direção; se também ela não existir, não há
    fator que leve ao alvo.
    """

    denominator = calculated - fixed
    if abs(denominator) >= _DENOMINATOR_EPSILON:
        return (target - fixed) / denominator
    if abs(calculated) >= _DENOMINATOR_EPSILON:
        return target / calculated
    raise _InvalidRatio(
        "a parcela escalável não tem potência, então nenhum fator a altera"
    )


Evaluate = Callable[[float, float], HeadReading]


def solve_patamar(
    evaluate: Evaluate,
    target: PatamarTarget,
    settings: CorrectionSettings = CorrectionSettings(),
    *,
    step: int = 0,
    cancel_check: Callable[[], bool] | None = None,
) -> PatamarCorrection:
    """Itera ``evaluate(fP, fQ)`` até a saída bater com ``target``.

    Patamar sem alvo é avaliado uma vez com fatores 1, só para o relatório
    mostrar a saída de base.
    """

    if target.is_empty:
        reading = evaluate(1.0, 1.0)
        return PatamarCorrection(
            step,
            target,
            PatamarStatus.SKIPPED,
            iterations=0,
            initial=reading,
            final=reading,
        )

    active_factor = 1.0
    reactive_factor = 1.0
    initial: HeadReading | None = None
    reading: HeadReading | None = None
    for iteration in range(1, settings.max_iterations + 1):
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Correção de demanda cancelada.")
        reading = evaluate(active_factor, reactive_factor)
        if initial is None:
            initial = reading

        def outcome(status: PatamarStatus, message: str = "") -> PatamarCorrection:
            return PatamarCorrection(
                step,
                target,
                status,
                active_factor,
                reactive_factor,
                iteration,
                initial,
                reading,
                message,
                _warnings(reactive_factor),
            )

        if not reading.converged:
            return outcome(
                PatamarStatus.FLOW_DIVERGED,
                f"o fluxo não convergiu com fP={active_factor:.6g} e "
                f"fQ={reactive_factor:.6g}",
            )
        active_ok = target.active is None or abs(
            reading.active - target.active
        ) <= settings.tolerance(target.active)
        reactive_ok = target.reactive is None or abs(
            reading.reactive - target.reactive
        ) <= settings.tolerance(target.reactive)
        if active_ok and reactive_ok:
            # O incremento que já basta não é aplicado: o fluxo relatado é o
            # destes fatores.
            return outcome(PatamarStatus.CONVERGED)
        if iteration == settings.max_iterations:
            break
        try:
            active_ratio = (
                1.0
                if target.active is None
                else _ratio(target.active, reading.active, reading.fixed_active)
            )
            reactive_ratio = (
                1.0
                if target.reactive is None
                else _ratio(target.reactive, reading.reactive, reading.fixed_reactive)
            )
        except _InvalidRatio as exc:
            return outcome(PatamarStatus.INVALID, str(exc))
        if active_ratio <= 0.0:
            return outcome(
                PatamarStatus.INVALID,
                "o P fixo (geração e cargas fora do escopo) já supera o alvo; "
                "só uma carga negativa fecharia a conta",
            )
        active_factor *= active_ratio
        reactive_factor *= reactive_ratio

    assert reading is not None and initial is not None
    return PatamarCorrection(
        step,
        target,
        PatamarStatus.NOT_CONVERGED,
        active_factor,
        reactive_factor,
        settings.max_iterations,
        initial,
        reading,
        f"o resíduo não ficou dentro da tolerância em {settings.max_iterations} "
        "iterações",
        _warnings(reactive_factor),
    )


def _warnings(reactive_factor: float) -> tuple[str, ...]:
    if reactive_factor <= 0.0:
        return (
            "fQ não positivo: as cargas do escopo passam a fornecer reativo; "
            "confira o Q medido e os capacitores",
        )
    return ()


@dataclass(frozen=True, slots=True)
class DemandCorrectionResult:
    """Uma execução sobre um circuito, com os modelos para revalidação.

    Os modelos viajam no resultado pelo mesmo motivo do ``PowerFlowResult``: a
    interface os compara por identidade na chegada, e uma reimportação durante
    a execução torna o resultado obsoleto.
    """

    circuit_index: int
    circuit_id: str
    scope: frozenset[DemandScope]
    settings: CorrectionSettings
    patamares: tuple[PatamarCorrection, ...]
    energy_mode: bool = False
    scoped_load_count: int = 0
    fixed_load_count: int = 0
    models: tuple[object, ...] = field(default=(), compare=False)

    @property
    def converged_steps(self) -> tuple[int, ...]:
        return tuple(item.step for item in self.patamares if item.converged)

    @property
    def failed_steps(self) -> tuple[int, ...]:
        return tuple(
            item.step
            for item in self.patamares
            if item.status not in (PatamarStatus.CONVERGED, PatamarStatus.SKIPPED)
        )

    def correction(self) -> DemandCorrection | None:
        """Os fatores a aplicar: só os patamares que convergiram.

        Um patamar que não convergiu fica em 1 — aplicar um fator que não leva
        ao alvo seria trocar um erro conhecido por um desconhecido. ``None``
        quando nenhum patamar tem fator a aplicar.
        """

        factors = tuple(
            (item.active_factor, item.reactive_factor)
            if item.converged
            else (1.0, 1.0)
            for item in sorted(self.patamares, key=lambda item: item.step)
        )
        if len(factors) != PATAMAR_COUNT or all(pair == (1.0, 1.0) for pair in factors):
            return None
        return DemandCorrection(
            self.circuit_id,
            self.scope,
            factors,
            energy_mode=self.energy_mode,
        )


def parse_target(text: str) -> float | None:
    """Valor digitado: vazio é ``None``; aceita vírgula decimal e milhar com ponto."""

    raw = str(text).strip().replace(" ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("O alvo deve ser um número finito.")
    return value


def targets_from_pairs(
    pairs: Iterable[tuple[float | None, float | None]],
) -> tuple[PatamarTarget, ...]:
    targets = tuple(PatamarTarget(active, reactive) for active, reactive in pairs)
    if len(targets) != PATAMAR_COUNT:
        raise ValueError(f"São necessários {PATAMAR_COUNT} patamares.")
    return targets
