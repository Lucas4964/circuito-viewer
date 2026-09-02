"""Modo de obtenção das potências equivalentes dos ramais."""

from __future__ import annotations

from enum import Enum


class BranchPowerSource(str, Enum):
    """Fonte da potência usada para montar a carga equivalente de um ramal.

    ``TABLE`` soma as tabelas de patamares das cargas do ramal — o único
    caminho existente até aqui, e o que continua valendo por padrão. Uma carga
    sem os quatro ``NPAT`` torna a agregação incompleta e bloqueia a exportação
    simplificada.

    ``POWER_FLOW`` resolve o fluxo de potência do circuito completo e lê a
    potência que entra no ramal pelo seu primeiro elemento — chave ou trecho de
    rede. O ramal passa a ter valor mesmo quando cargas internas não têm
    tabela, ao preço de exigir o motor do OpenDSS.
    """

    TABLE = "table"
    POWER_FLOW = "power_flow"


DEFAULT_BRANCH_POWER_SOURCE = BranchPowerSource.TABLE


def parse_branch_power_source(value: object) -> BranchPowerSource:
    """Converte um valor persistido; ausência ou corrupção usam o padrão."""

    if isinstance(value, BranchPowerSource):
        return value
    try:
        normalized = str(value).strip().casefold()
        return BranchPowerSource(normalized)
    except (TypeError, ValueError):
        return DEFAULT_BRANCH_POWER_SOURCE


__all__ = [
    "DEFAULT_BRANCH_POWER_SOURCE",
    "BranchPowerSource",
    "parse_branch_power_source",
]
