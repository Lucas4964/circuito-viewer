"""Faixas de níveis de tensão e a classificação das barras por faixa.

Este módulo não depende de Qt e não toca o disco: recebe o resultado do fluxo de
potência já pronto e devolve, por barra, um índice de categoria e a cor
correspondente. Quem desenha é :mod:`circuit_viewer.graphics`, que só enxerga
``(máscara, índices, paleta)`` — o mesmo contrato do modo por fases.

**Gravidade e cor são independentes.** ``severity`` existe só para responder
"qual das medidas desta barra é a pior"; a cor é livre por faixa. As duas pontas
críticas do PRODIST — subtensão e sobretensão — têm a *mesma* gravidade e cores
*diferentes* de propósito: pintar as duas de vermelho tornaria o mapa ambíguo
justamente onde ele precisa ser imediato, que é bater o olho e saber se a tensão
está alta demais ou baixa demais.

**Intervalos são fechados e a primeira faixa que contém o valor vence.** É a
única regra simples que reproduz o PRODIST nas duas bordas de "adequada", que é
fechada nos dois extremos: 0,93 pertence a ela (e não à precária, que termina
em 0,93) e 1,05 também (e não à crítica, que começa em 1,05). Daí a ordem da
tupla ser significativa, e a tabela padrão listar "Adequada" primeiro.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Mapping, Sequence

import numpy as np

from .circuit_colors import normalize_hex_color
from .model import FloatArray, IndexArray
from .opendss_powerflow import (
    DEAD_NODE_PU,
    LINE_VOLTAGE_PU_BASE,
    BarVoltages,
    line_voltages,
)


class VoltageBandsError(ValueError):
    """Erro legível ao montar ou validar uma tabela de faixas."""


class VoltageQuantity(Enum):
    """Qual tensão a cor representa."""

    #: Fase-neutro, o pu que o OpenDSS devolve direto.
    PHASE = "phase"
    #: Fase-fase, composta fasorialmente e renormalizada por ``√3``.
    LINE = "line"

    @property
    def label(self) -> str:
        return "Tensão de linha" if self is VoltageQuantity.LINE else "Tensão de fase"


@dataclass(frozen=True, slots=True)
class VoltageBand:
    """Uma faixa: o intervalo fechado em pu, a cor e a gravidade."""

    label: str
    color: str
    minimum_pu: float
    maximum_pu: float
    severity: int

    def __post_init__(self) -> None:
        label = " ".join(str(self.label).split())
        if not label:
            raise VoltageBandsError("Informe o nome da faixa.")
        object.__setattr__(self, "label", label)
        try:
            object.__setattr__(self, "color", normalize_hex_color(self.color))
        except ValueError as exc:
            raise VoltageBandsError(f"Faixa {label!r}: {exc}") from exc
        minimum = float(self.minimum_pu)
        maximum = float(self.maximum_pu)
        if not math.isfinite(minimum) or minimum < 0.0:
            raise VoltageBandsError(
                f"Faixa {label!r}: o mínimo deve ser um número a partir de zero."
            )
        if math.isnan(maximum) or maximum <= minimum:
            raise VoltageBandsError(
                f"Faixa {label!r}: o máximo deve ser maior que o mínimo."
            )
        object.__setattr__(self, "minimum_pu", minimum)
        object.__setattr__(self, "maximum_pu", maximum)
        if isinstance(self.severity, bool) or not isinstance(self.severity, int):
            raise VoltageBandsError(
                f"Faixa {label!r}: a gravidade deve ser um número inteiro."
            )
        if self.severity < 0:
            raise VoltageBandsError(
                f"Faixa {label!r}: a gravidade não pode ser negativa."
            )

    def contains(self, value: float) -> bool:
        """Intervalo **fechado**: as duas pontas pertencem à faixa."""

        return self.minimum_pu <= value <= self.maximum_pu


@dataclass(frozen=True, slots=True)
class ReservedColors:
    """As três categorias que não são faixas, e por isso não têm intervalo.

    Acromáticas por padrão, para nunca competirem com a escala das faixas: o
    olho tem de distinguir "esta barra está fora da medida" de "esta barra está
    na ponta ruim da medida".
    """

    #: Modo de linha, barra com menos de duas fases energizadas.
    no_line_voltage: str = "#000000"
    #: Nenhum nó acima de :data:`DEAD_NODE_PU` — a barra está sem tensão.
    de_energized: str = "#9E9E9E"
    #: A barra não aparece em ``bar_voltages``: seu circuito não foi resolvido.
    no_data: str = "#E0E0E0"

    def __post_init__(self) -> None:
        for field_name in ("no_line_voltage", "de_energized", "no_data"):
            try:
                value = normalize_hex_color(getattr(self, field_name))
            except ValueError as exc:
                raise VoltageBandsError(f"{RESERVED_LABELS[field_name]}: {exc}") from exc
            object.__setattr__(self, field_name, value)

    @property
    def colors(self) -> tuple[str, str, str]:
        """Na ordem em que entram na paleta, logo depois das faixas."""

        return (self.no_line_voltage, self.de_energized, self.no_data)


#: Rótulo de cada categoria reservada, na mesma ordem de :attr:`ReservedColors.colors`.
RESERVED_LABELS: Mapping[str, str] = {
    "no_line_voltage": "Sem tensão de linha",
    "de_energized": "Sem tensão",
    "no_data": "Sem resultado",
}


@dataclass(frozen=True, slots=True)
class VoltageClassification:
    """Categoria de cada barra, com o que a legenda e o tooltip precisam.

    ``style_indices`` indexa :attr:`colors` e :attr:`labels`, e é sempre não
    negativo: as categorias reservadas ocupam os últimos índices da paleta, em
    vez de valores negativos, para o renderizador não precisar de nenhum ramo
    especial. ``-1`` continua reservado ao "modo desligado", que não passa por
    aqui.
    """

    style_indices: IndexArray
    colors: tuple[str, ...]
    labels: tuple[str, ...]
    counts: tuple[int, ...]
    values_pu: FloatArray
    quantity: VoltageQuantity

    def __post_init__(self) -> None:
        styles = self.style_indices
        if styles.dtype != np.dtype(np.intp) or styles.ndim != 1:
            raise ValueError("As categorias de tensão devem ser um vetor de índices.")
        if styles.flags.writeable:
            raise ValueError("As categorias de tensão devem ser imutáveis.")
        values = self.values_pu
        if values.dtype != np.dtype(np.float64) or values.ndim != 1:
            raise ValueError("Os valores em pu devem ser um vetor de ponto flutuante.")
        if values.flags.writeable:
            raise ValueError("Os valores em pu devem ser imutáveis.")
        if values.size != styles.size:
            raise ValueError("Deve haver um valor em pu para cada barra.")
        if len(self.colors) != len(self.labels) or len(self.colors) != len(self.counts):
            raise ValueError("Cores, rótulos e contagens devem ter o mesmo tamanho.")
        if styles.size and int(styles.max(initial=-1)) >= len(self.colors):
            raise ValueError("Uma categoria de tensão não possui cor correspondente.")
        if styles.size and int(styles.min(initial=0)) < 0:
            raise ValueError("As categorias de tensão não podem ser negativas.")

    def describe(self, bar_index: int) -> str:
        """Linha do tooltip: a categoria e, quando há, o pu que a determinou."""

        category = int(self.style_indices[bar_index])
        label = self.labels[category]
        value = float(self.values_pu[bar_index])
        if math.isnan(value):
            return label
        return f"{label} — {value:.3f} pu ({self.quantity.label.lower()})"


@dataclass(frozen=True, slots=True)
class VoltageBandTable:
    """As faixas, as cores reservadas, e a paleta que sai das duas.

    É a **única** fonte da correspondência índice↔cor: a legenda, o diálogo e o
    renderizador leem :attr:`colors` daqui, e nenhum deles monta a sua própria.
    """

    bands: tuple[VoltageBand, ...]
    reserved: ReservedColors = ReservedColors()

    def __post_init__(self) -> None:
        bands = tuple(self.bands)
        if not bands:
            raise VoltageBandsError("A tabela deve conter ao menos uma faixa.")
        object.__setattr__(self, "bands", bands)
        _check_coverage(bands)

    # -- a paleta ---------------------------------------------------------

    @property
    def colors(self) -> tuple[str, ...]:
        return tuple(band.color for band in self.bands) + self.reserved.colors

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(band.label for band in self.bands) + tuple(
            RESERVED_LABELS[name]
            for name in ("no_line_voltage", "de_energized", "no_data")
        )

    @property
    def no_line_voltage_index(self) -> int:
        return len(self.bands)

    @property
    def de_energized_index(self) -> int:
        return len(self.bands) + 1

    @property
    def no_data_index(self) -> int:
        return len(self.bands) + 2

    # -- classificação ----------------------------------------------------

    def band_index_of(self, value: float) -> int:
        """Índice da **primeira** faixa que contém o valor.

        A tabela cobre ``[0, +inf)``, então um valor finito não negativo sempre
        encontra faixa; um NaN não encontraria, e por isso nunca chega aqui.
        """

        for index, band in enumerate(self.bands):
            if band.contains(value):
                return index
        raise VoltageBandsError(
            f"Nenhuma faixa contém {value:.4f} pu; a tabela tem uma lacuna."
        )

    def worst_index(self, values: Sequence[float]) -> tuple[int, float]:
        """A pior medida da barra: ``(índice da faixa, valor)``.

        A gravidade decide. O desempate entre faixas de mesma gravidade — o caso
        das duas críticas — fica com o valor mais distante do nominal, que é o
        que faz a cor corresponder ao número exibido no tooltip em vez de a uma
        outra medida da mesma barra.
        """

        best_index = -1
        best_value = math.nan
        best_key = (-1, -1.0)
        for value in values:
            index = self.band_index_of(value)
            key = (self.bands[index].severity, abs(value - 1.0))
            if key > best_key:
                best_key = key
                best_index = index
                best_value = value
        if best_index < 0:
            raise VoltageBandsError("Não há valor a classificar.")
        return best_index, best_value

    def classify(
        self,
        bar_count: int,
        bar_voltages: Mapping[int, BarVoltages],
        quantity: VoltageQuantity,
        step: int,
    ) -> VoltageClassification:
        """Categoria de cada uma das ``bar_count`` barras, no patamar ``step``."""

        count = int(bar_count)
        if count < 0:
            raise VoltageBandsError("O número de barras não pode ser negativo.")
        if int(step) < 0:
            raise VoltageBandsError("O patamar não pode ser negativo.")
        step = int(step)

        styles = np.full(count, self.no_data_index, dtype=np.intp)
        values = np.full(count, math.nan, dtype=np.float64)
        for bar_index, voltages in bar_voltages.items():
            index = int(bar_index)
            if not 0 <= index < count:
                continue
            category, value = self._classify_bar(voltages, quantity, step)
            styles[index] = category
            values[index] = value

        counts = tuple(
            int(np.count_nonzero(styles == category))
            for category in range(len(self.colors))
        )
        styles.setflags(write=False)
        values.setflags(write=False)
        return VoltageClassification(
            styles,
            self.colors,
            self.labels,
            counts,
            values,
            quantity,
        )

    def _classify_bar(
        self,
        voltages: BarVoltages,
        quantity: VoltageQuantity,
        step: int,
    ) -> tuple[int, float]:
        rows = voltages.per_unit
        if step >= len(rows):
            return self.no_data_index, math.nan

        # O corte de barra morta vem primeiro, e sempre sobre a tensão
        # fase-neutro: sem ele uma barra desenergizada daria 0,0 pu e seria
        # pintada de "crítica", que é outra coisa — lá há tensão, e ela afundou.
        live = tuple(
            position
            for position, value in enumerate(rows[step])
            if float(value) > DEAD_NODE_PU
        )
        if not live:
            return self.de_energized_index, math.nan

        if quantity is VoltageQuantity.PHASE:
            return self.worst_index([float(rows[step][position]) for position in live])

        if len(voltages.angles) != len(rows):
            # Sem os ângulos não há como compor a tensão de linha, que é uma
            # subtração de fasores. Dizer isso é melhor do que devolver a
            # fase-neutro com outro nome.
            return self.no_line_voltage_index, math.nan

        # Só os nós energizados entram no par: uma barra trifásica com uma fase
        # aberta tem **uma** tensão de linha, não três, e as outras duas seriam
        # medidas contra uma fase morta.
        nodes = tuple(voltages.nodes[position] for position in live)
        per_unit = tuple(
            tuple(row[position] for position in live) for row in voltages.per_unit
        )
        angles = tuple(
            tuple(row[position] for position in live) for row in voltages.angles
        )
        pairs, magnitudes, _ = line_voltages(nodes, per_unit, angles)
        if not pairs or step >= len(magnitudes):
            return self.no_line_voltage_index, math.nan
        # A pu do OpenDSS é na base de fase; a de linha é √3 maior, e é essa
        # renormalização que faz o nominal dar 1,0.
        return self.worst_index(
            [float(value) / LINE_VOLTAGE_PU_BASE for value in magnitudes[step]]
        )


def _check_coverage(bands: Sequence[VoltageBand]) -> None:
    """A união das faixas tem de cobrir ``[0, +inf)`` sem lacuna.

    Sobreposição é permitida — é o que deixa "adequada" fechada nos dois
    extremos — e a ordem da tupla resolve quem vence.
    """

    ordered = sorted(bands, key=lambda band: band.minimum_pu)
    if ordered[0].minimum_pu > 0.0:
        raise VoltageBandsError(
            "As faixas devem começar em 0 pu; nenhuma cobre "
            f"os valores abaixo de {ordered[0].minimum_pu:.4g} pu."
        )
    reach = ordered[0].maximum_pu
    for band in ordered[1:]:
        if band.minimum_pu > reach:
            raise VoltageBandsError(
                f"As faixas têm uma lacuna entre {reach:.4g} e "
                f"{band.minimum_pu:.4g} pu."
            )
        reach = max(reach, band.maximum_pu)
    if not math.isinf(reach):
        raise VoltageBandsError(
            f"A faixa mais alta deve terminar no infinito; ela para em {reach:.4g} pu."
        )


def default_voltage_band_table() -> VoltageBandTable:
    """As faixas do PRODIST Módulo 8 para média tensão (1 kV < V ≤ 69 kV).

    A ordem é significativa: "Adequada" vem primeiro para ficar com as duas
    bordas que o PRODIST lhe dá, 0,93 e 1,05.

    As cores são quentes do lado que **cai** e frias do lado que **sobe**, e por
    isso as duas críticas não se confundem apesar da mesma gravidade.
    """

    return VoltageBandTable(
        (
            VoltageBand("Adequada", "#2E7D32", 0.93, 1.05, 0),
            VoltageBand("Precária (subtensão)", "#F9A825", 0.90, 0.93, 1),
            VoltageBand("Crítica (subtensão)", "#C62828", 0.00, 0.90, 2),
            VoltageBand("Crítica (sobretensão)", "#6A1B9A", 1.05, math.inf, 2),
        )
    )
