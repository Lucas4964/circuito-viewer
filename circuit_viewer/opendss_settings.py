"""Parâmetros globais do OpenDSS definidos pelo usuário.

Camada de núcleo: não importa Qt, porque quem consome estes valores é
``opendss_export``. A persistência em ``QSettings`` mora do lado da interface,
em ``opendss_settings_dialog``.

Hoje há um único grupo — os limites de tensão das cargas — mas a forma foi
escolhida para crescer: um valor imutável, com invariantes próprias, que sabe
traduzir a si mesmo nos comandos do OpenDSS.

**Por que estes dois parâmetros importam.** ``Vminpu`` e ``Vmaxpu`` delimitam a
faixa em que a ``Load`` se comporta conforme o seu ``model``; fora dela o OpenDSS
a converte para impedância constante. Como o exportador emite ``model=1``
(potência constante), a faixa padrão do OpenDSS (0,95 a 1,05) faz toda barra
abaixo de 0,95 pu ter a carga convertida **em silêncio** — e o estudo passa a
subestimar a queda de tensão exatamente onde ela mais interessa.

**A validação é nossa por necessidade.** Medido contra a DLL: ``vminpu=0``,
``vminpu=2`` e ``vminpu=-1`` são aceitos sem erro nem aviso. Quem impede o
absurdo é a invariante de :class:`OpenDssLoadSettings`, reforçada pelas faixas
dos campos do diálogo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from .opendss_export import parse_number


# Os padrões do próprio OpenDSS, para o diálogo abrir mostrando o que já vale.
DEFAULT_VMINPU = 0.95
DEFAULT_VMAXPU = 1.05

# Faixas praticáveis, usadas pelos campos do diálogo. Mais estreitas que a
# invariante da dataclass de propósito: a invariante define o que é coerente, a
# faixa define o que é razoável oferecer.
VMINPU_RANGE = (0.100, 1.000)
VMAXPU_RANGE = (1.000, 2.000)

# `Load..*` é a classe `Load` com o nome casando com a expressão regular `.*`.
_BATCH_EDIT_TEMPLATE = "BatchEdit Load..* {property}={value}"


def _format_pu(value: float) -> str:
    """Formata um valor em pu sem locale.

    Mesma exigência do resto do exportador: o OpenDSS só entende ponto decimal,
    e um ``str(0.95)`` sob locale pt-BR não é seguro em toda a cadeia de
    formatação. Três casas cobrem a precisão que o diálogo oferece.
    """

    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


@dataclass(frozen=True, slots=True)
class OpenDssLoadSettings:
    """Parâmetros globais aplicados a **todas** as ``Load`` do modelo.

    ``voltage_limits_enabled`` desligado é o estado padrão e significa
    "não emitir comando algum": o arquivo sai idêntico ao que a exportação
    produzia antes desta configuração existir, e o OpenDSS aplica os padrões
    dele. Os valores continuam guardados enquanto desligados, para o usuário não
    precisar redigitá-los ao reativar.
    """

    voltage_limits_enabled: bool = False
    vminpu: float = DEFAULT_VMINPU
    vmaxpu: float = DEFAULT_VMAXPU

    def __post_init__(self) -> None:
        for name in ("vminpu", "vmaxpu"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} deve ser um número finito.")
        if self.vminpu <= 0.0:
            raise ValueError("vminpu deve ser positivo.")
        # A faixa precisa conter a tensão nominal: fora disso a carga estaria
        # sempre convertida para impedância constante, o oposto da intenção.
        if not self.vminpu <= 1.0 <= self.vmaxpu:
            raise ValueError(
                "a faixa deve conter a tensão nominal: exige-se "
                f"vminpu <= 1 <= vmaxpu (recebido {self.vminpu} e {self.vmaxpu})."
            )

    @property
    def is_default(self) -> bool:
        """``True`` quando nada será emitido no arquivo."""

        return not self.voltage_limits_enabled

    def batch_edit_commands(self) -> tuple[str, ...]:
        """Comandos que aplicam os limites a todas as cargas já definidas.

        ``BatchEdit`` é comando **executivo**: exige os objetos já criados, por
        isso quem o emite precisa colocá-lo depois dos ``Redirect`` dos arquivos
        de carga. Desligado, devolve vazio — e não os comandos com os valores
        padrão, para o arquivo não mudar sem o usuário pedir.
        """

        if not self.voltage_limits_enabled:
            return ()
        return (
            _BATCH_EDIT_TEMPLATE.format(
                property="vminpu",
                value=_format_pu(self.vminpu),
            ),
            _BATCH_EDIT_TEMPLATE.format(
                property="vmaxpu",
                value=_format_pu(self.vmaxpu),
            ),
        )

    def as_mapping(self) -> dict[str, str]:
        """Representação textual plana, para a camada de persistência.

        Texto, e não os tipos originais, porque o ``QSettings`` do Windows
        devolve tudo como ``str`` de qualquer forma; padronizar aqui evita que a
        leitura tenha de adivinhar o tipo que gravou.
        """

        return {
            "voltage_limits_enabled": "1" if self.voltage_limits_enabled else "0",
            "vminpu": _format_pu(self.vminpu),
            "vmaxpu": _format_pu(self.vmaxpu),
        }


DEFAULT_OPENDSS_LOAD_SETTINGS = OpenDssLoadSettings()


def settings_from_mapping(values: Mapping[str, object]) -> OpenDssLoadSettings:
    """Reconstrói a configuração a partir do mapeamento textual.

    Nunca levanta: chave ausente, texto não numérico ou faixa incoerente caem no
    padrão. Uma preferência corrompida — de uma versão anterior, de edição manual
    do registro — não pode impedir a aplicação de abrir.
    """

    raw_enabled = values.get("voltage_limits_enabled")
    enabled = str(raw_enabled).strip().lower() in {"1", "true", "yes", "sim"}
    vminpu = parse_number(str(values.get("vminpu", "")))
    vmaxpu = parse_number(str(values.get("vmaxpu", "")))
    if vminpu is None:
        vminpu = DEFAULT_VMINPU
    if vmaxpu is None:
        vmaxpu = DEFAULT_VMAXPU
    try:
        return OpenDssLoadSettings(
            voltage_limits_enabled=enabled,
            vminpu=vminpu,
            vmaxpu=vmaxpu,
        )
    except ValueError:
        return DEFAULT_OPENDSS_LOAD_SETTINGS


__all__ = [
    "DEFAULT_OPENDSS_LOAD_SETTINGS",
    "DEFAULT_VMAXPU",
    "DEFAULT_VMINPU",
    "OpenDssLoadSettings",
    "VMAXPU_RANGE",
    "VMINPU_RANGE",
    "settings_from_mapping",
]
