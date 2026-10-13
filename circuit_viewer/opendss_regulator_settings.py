"""Parâmetros dos reguladores de tensão definidos pelo usuário.

Módulo folha: sem Qt e sem dependências do pacote, no molde de
``opendss_solution``. Quem consome os valores é ``build_regulator_export``, e
por ele o master exportado, a exportação simplificada, a da alocação e o fluxo
de potência interno.

São dois valores, ambos em pu da tensão de fase do regulador:

- **VREG**, a tensão que o controle persegue no lado regulado. O padrão,
  1,02 pu, é o que o Interplan adota quando o cadastro não traz um VREG
  utilizável (``LR_VREG`` do ``Geral.mdb``). Antes desta configuração o export
  fixava 1,00 pu.
- **Banda**, a largura **total** da faixa morta: o tap só muda quando a tensão
  sai de ``VREG ± banda/2``. O padrão, 0,0068 pu (≈ 0,45 V na base de 66,4 V
  do TP do export), é pouco mais que um degrau de 0,625 % — o controle chega ao
  tap mais próximo do VREG, que é o que o Interplan calcula. O padrão do
  OpenDSS, 3 V na mesma base (≈ 4,5 %), deixava o tap até dois degraus antes.

**Por que a banda nunca pode ser menor que um degrau.** Cada comutação move a
tensão de cerca de um degrau. Com a faixa morta mais estreita que isso, a
tensão salta de um lado ao outro dela a cada comutação e o tap oscila até
estourar o ``MaxControlIter``. :func:`effective_band_pu` impede isso por
regulador, porque o degrau depende de ``FAIXA`` e ``NPASSOS`` de cada um.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


DEFAULT_REGULATOR_VREG_PU = 1.02
DEFAULT_REGULATOR_BAND_PU = 0.0068

# Faixa em que um VREG é aceito como dado. É a regra do próprio Interplan
# (Flow.dll troca VREG fora de [0,5; 1,5] pelo padrão), e por isso vale tanto
# para o cadastro quanto para a preferência.
VALID_REGULATOR_VREG_RANGE = (0.5, 1.5)
# Faixas oferecidas pelo diálogo: mais estreitas que a validade de propósito.
REGULATOR_VREG_RANGE = (0.900, 1.100)
REGULATOR_BAND_RANGE = (0.0010, 0.0500)

# Faixa de regulação e número de passos quando o cadastro não os informa: os
# mesmos valores que o Interplan adota (±10 % em 32 passos de 0,625 %).
DEFAULT_REGULATION_RANGE = 0.10
DEFAULT_STEP_COUNT = 32

# Folga sobre um degrau no piso da banda: na banda exatamente igual ao degrau,
# o arredondamento da tensão já basta para o tap oscilar.
BAND_STEP_MARGIN = 1.05


def regulator_step_pu(regulation_range: float, step_count: int) -> float:
    """Tamanho de um degrau, em pu: a faixa total dividida pelos passos."""

    return 2.0 * regulation_range / step_count


def effective_band_pu(
    band_pu: float,
    regulation_range: float,
    step_count: int,
) -> float:
    """A banda configurada, ou o piso de um degrau com folga se ela for menor."""

    floor = BAND_STEP_MARGIN * regulator_step_pu(regulation_range, step_count)
    return max(band_pu, floor)


def is_valid_vreg(value: float | None) -> bool:
    low, high = VALID_REGULATOR_VREG_RANGE
    return value is not None and math.isfinite(value) and low <= value <= high


def _format(value: float, decimals: int) -> str:
    """Sem locale: o mapeamento vai para o ``QSettings`` como texto."""

    return f"{value:.{decimals}f}".rstrip("0").rstrip(".") or "0"


def _parse(value: object) -> float | None:
    """Lê o que :meth:`OpenDssRegulatorSettings.as_mapping` gravou.

    Só ponto decimal, de propósito: o texto é sempre o que ``_format`` produziu,
    e a regra de vírgula do projeto (``parse_number``) é para dado digitado.
    """

    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True, slots=True)
class OpenDssRegulatorSettings:
    """VREG e banda aplicados aos reguladores exportados, em pu."""

    vreg_pu: float = DEFAULT_REGULATOR_VREG_PU
    band_pu: float = DEFAULT_REGULATOR_BAND_PU

    def __post_init__(self) -> None:
        for name in ("vreg_pu", "band_pu"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} deve ser um número.")
            if not math.isfinite(value):
                raise ValueError(f"{name} deve ser um número finito.")
        if not is_valid_vreg(self.vreg_pu):
            low, high = VALID_REGULATOR_VREG_RANGE
            raise ValueError(f"vreg_pu deve estar entre {low} e {high} pu.")
        if not 0.0 < self.band_pu < 0.2:
            raise ValueError("band_pu deve ser positiva e menor que 0,2 pu.")

    def as_mapping(self) -> dict[str, str]:
        """Representação textual plana, para a camada de persistência."""

        return {
            "vreg_pu": _format(self.vreg_pu, 4),
            "band_pu": _format(self.band_pu, 5),
        }


DEFAULT_OPENDSS_REGULATOR_SETTINGS = OpenDssRegulatorSettings()


def regulator_settings_from_mapping(
    values: Mapping[str, object],
) -> OpenDssRegulatorSettings:
    """Reconstrói a configuração; ausência ou corrupção caem no padrão.

    Nunca levanta, como os demais ``parse_*`` de preferência: um valor
    corrompido não pode impedir a aplicação de abrir. Cada campo cai sozinho no
    seu padrão, para um VREG inválido não descartar uma banda válida.
    """

    vreg = _parse(values.get("vreg_pu", ""))
    band = _parse(values.get("band_pu", ""))
    if not is_valid_vreg(vreg):
        vreg = DEFAULT_REGULATOR_VREG_PU
    if band is None or not 0.0 < band < 0.2:
        band = DEFAULT_REGULATOR_BAND_PU
    return OpenDssRegulatorSettings(vreg_pu=vreg, band_pu=band)


__all__ = [
    "BAND_STEP_MARGIN",
    "DEFAULT_OPENDSS_REGULATOR_SETTINGS",
    "DEFAULT_REGULATION_RANGE",
    "DEFAULT_REGULATOR_BAND_PU",
    "DEFAULT_REGULATOR_VREG_PU",
    "DEFAULT_STEP_COUNT",
    "OpenDssRegulatorSettings",
    "REGULATOR_BAND_RANGE",
    "REGULATOR_VREG_RANGE",
    "VALID_REGULATOR_VREG_RANGE",
    "effective_band_pu",
    "is_valid_vreg",
    "regulator_settings_from_mapping",
    "regulator_step_pu",
]
