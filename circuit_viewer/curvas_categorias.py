"""Categorias das curvas típicas do Interplan (``CURVAS_CATEGORIA``).

Camada de núcleo: não importa Qt nem toca o disco. A gravação mora em
``curvas_store`` e a leitura do banco de curvas em ``curvas_mdb``.

**Para que serve a categoria.** É o que o Interplan usa para escolher a curva de
cada consumidor: a ``CLASSE`` do consumidor e o consumo mensal dentro da faixa
``[MIN, MAX)`` apontam as tipologias candidatas, e a participação de mercado
(``PART_MERC``) decide o sorteio entre elas. A curva da categoria é achada pelo
``link_id`` das curvas, que guarda o ``CAT_ID``.

**CLASSE.** Nas categorias de consumo (``TIPO`` ``C``): 0 residencial,
1 comercial, 2 industrial, 3 rural, 4 outros, 5 iluminação pública e -1 os
clientes A4 da média tensão, cuja faixa é em kW, não em kWh. Nas categorias de
fonte (``TIPO`` ``F``) a CLASSE é o tipo de geração, o ``TIPOGD`` do gerador.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# TIPO da categoria: curva de consumo ou de fonte (geração).
CATEGORY_KIND_LOAD = "C"
CATEGORY_KIND_SOURCE = "F"
CATEGORY_KINDS = (CATEGORY_KIND_LOAD, CATEGORY_KIND_SOURCE)

# CLASSE das categorias de consumo que não são de consumidor BT.
CLASS_PUBLIC_LIGHTING = 5
CLASS_A4 = -1


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


@dataclass(frozen=True, slots=True)
class CurveCategory:
    """Uma linha de ``CURVAS_CATEGORIA``, já validada.

    ``minimum`` e ``maximum`` seguem o banco: um limite negativo é aberto.
    ``power_factor`` é o ``FAT_POT`` das curvas da categoria, ``None`` quando o
    banco não o informa.
    """

    category_id: str
    description: str
    consumer_class: int
    minimum: float
    maximum: float
    market_share: float
    kind: str = CATEGORY_KIND_LOAD
    pu_max: int = 0
    demand_type: int = 0
    power_factor: float | None = None

    def __post_init__(self) -> None:
        if not str(self.category_id).strip():
            raise ValueError("A categoria precisa de um identificador.")
        if not str(self.description).strip():
            raise ValueError("A categoria precisa de uma descrição.")
        if self.kind not in CATEGORY_KINDS:
            raise ValueError(f"TIPO de categoria desconhecido: {self.kind}.")
        for label, value in (
            ("CLASSE", self.consumer_class),
            ("PU_MAX", self.pu_max),
            ("TIPODEM", self.demand_type),
        ):
            if type(value) is not int:
                raise ValueError(f"{label} precisa ser um número inteiro.")
        for label, value in (
            ("MIN", self.minimum),
            ("MAX", self.maximum),
            ("PART_MERC", self.market_share),
        ):
            if not _is_finite_number(value):
                raise ValueError(f"{label} precisa ser um número finito.")
        if self.power_factor is not None and not (
            _is_finite_number(self.power_factor) and 0 < self.power_factor <= 1
        ):
            raise ValueError("FAT_POT precisa estar entre 0 e 1.")

    def contains(self, value: float) -> bool:
        """Como o Interplan: ``MIN <= valor < MAX``; limite negativo é aberto."""

        if self.minimum >= 0 and value < self.minimum:
            return False
        if self.maximum >= 0 and value >= self.maximum:
            return False
        return True


def category_sort_key(category: CurveCategory) -> tuple[int, int | str]:
    """Ordem do Interplan: ``CAT_ID`` numérico; o que não for número, depois."""

    text = category.category_id.strip()
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)
