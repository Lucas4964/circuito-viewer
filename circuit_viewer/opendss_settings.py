"""Parâmetros globais do OpenDSS definidos pelo usuário.

Camada de núcleo: não importa Qt, porque quem consome estes valores é
``opendss_export``. A persistência em ``QSettings`` mora do lado da interface,
em ``opendss_settings_dialog``.

São dois grupos, ambos aplicados às cargas: a faixa de tensão
(``Vminpu``/``Vmaxpu``) e o **modelo de carga** (potência constante ou ZIPV).

**Por que a faixa importa, e por que ela vale nos dois modelos.**
``Vminpu`` e ``Vmaxpu`` delimitam a janela em que a ``Load`` se comporta conforme
o seu ``model``; fora dela o OpenDSS a converte para impedância constante. Isso
não é exclusivo da potência constante: no ``DoZIPVModel`` do ``Load.pas``, os
coeficientes ZIPV só entram no ramo entre ``VBase95`` e ``VBase105`` — derivados
justamente destes dois parâmetros. Com a faixa padrão (0,95 a 1,05), toda barra
abaixo de 0,95 pu tem a carga convertida **em silêncio**, e o estudo passa a
subestimar a queda de tensão exatamente onde ela mais interessa.

**A validação é nossa por necessidade.** Medido contra a DLL: ``vminpu=0``,
``vminpu=2`` e ``vminpu=-1`` são aceitos sem erro nem aviso. O ZIPV é ainda mais
permissivo — o setter do OpenDSS é só ``SetZIPVSize(7)`` seguido de
``ParseAsVector(7, ZIPV)``, sem conferir sequer se os pesos somam 1. Quem impede
o absurdo são as invariantes daqui, reforçadas pelos campos do diálogo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
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

# Pesos ZIP: negativo e acima de 1 existem em modelagens reais (um peso pode
# compensar outro), então a faixa é generosa e quem garante a coerência é a
# soma.
ZIPV_WEIGHT_RANGE = (-2.000, 2.000)
# Tensão de corte em pu. Zero desliga o mecanismo: o ``Load.pas`` só aplica o
# sigmoide de corte quando ``ZIPV[7] > 0``.
ZIPV_CUTOFF_RANGE = (0.000, 1.000)
# Tolerância da soma dos pesos. A documentação do OpenDSS diz "should sum to 1";
# quatro casas é a precisão que o diálogo oferece.
ZIPV_SUM_TOLERANCE = 1e-4

# `Load..*` é a classe `Load` com o nome casando com a expressão regular `.*`.
_BATCH_EDIT_TEMPLATE = "BatchEdit Load..* {property}={value}"


class OpenDssLoadModel(str, Enum):
    """Modelo de carga aplicado às cargas de consumo do circuito.

    Os valores são os nomes persistidos; o número do ``model`` do OpenDSS vem de
    :attr:`dss_model`.
    """

    CONSTANT_POWER = "constant_power"
    ZIPV = "zipv"

    @property
    def dss_model(self) -> int:
        return 8 if self is OpenDssLoadModel.ZIPV else 1


DEFAULT_OPENDSS_LOAD_MODEL = OpenDssLoadModel.CONSTANT_POWER


def parse_opendss_load_model(value: object) -> OpenDssLoadModel:
    """Converte um valor persistido; ausência ou corrupção usam o padrão."""

    if isinstance(value, OpenDssLoadModel):
        return value
    try:
        return OpenDssLoadModel(str(value).strip().casefold())
    except (TypeError, ValueError):
        return DEFAULT_OPENDSS_LOAD_MODEL


def _format_pu(value: float) -> str:
    """Formata um valor em pu sem locale.

    Mesma exigência do resto do exportador: o OpenDSS só entende ponto decimal,
    e um ``str(0.95)`` sob locale pt-BR não é seguro em toda a cadeia de
    formatação. Três casas cobrem a precisão que o diálogo oferece.
    """

    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _format_weight(value: float) -> str:
    """Formata um coeficiente ZIP. Quatro casas para caber um terço."""

    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


@dataclass(frozen=True, slots=True)
class ZipvCoefficients:
    """Os sete valores da propriedade ``ZIPV``, na ordem exata do OpenDSS.

    A documentação define o vetor como: *"First 3 are ZIP weighting factors for
    real power (should sum to 1). Next 3 are ZIP weighting factors for reactive
    power (should sum to 1). Last 1 is cut-off voltage in p.u. of base kV; load
    is 0 below this cut-off."*

    O corte é **suave e opcional**: o ``Load.pas`` só o aplica quando o valor é
    maior que zero, e através de um sigmoide, não de um degrau. Zero, o padrão
    daqui, desliga o mecanismo.

    A soma dos pesos **não** é invariante desta classe de propósito — ela é
    verificada por :func:`zipv_sum_error`. O diálogo reconstrói o valor a cada
    tecla para atualizar a pré-visualização, e uma invariante que levantasse
    faria a pré-visualização cair no padrão no meio da digitação.
    """

    z_p: float = 0.0
    i_p: float = 0.0
    p_p: float = 1.0
    z_q: float = 0.0
    i_q: float = 0.0
    p_q: float = 1.0
    cutoff: float = 0.0

    def __post_init__(self) -> None:
        for name in ("z_p", "i_p", "p_p", "z_q", "i_q", "p_q", "cutoff"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} deve ser um número finito.")
        low, high = ZIPV_CUTOFF_RANGE
        if not low <= self.cutoff <= high:
            raise ValueError(
                "a tensão de corte deve estar entre "
                f"{low} e {high} pu (recebido {self.cutoff})."
            )

    @property
    def active_sum(self) -> float:
        return self.z_p + self.i_p + self.p_p

    @property
    def reactive_sum(self) -> float:
        return self.z_q + self.i_q + self.p_q

    def as_tuple(self) -> tuple[float, float, float, float, float, float, float]:
        """Os sete valores na ordem em que o OpenDSS os lê."""

        return (
            self.z_p,
            self.i_p,
            self.p_p,
            self.z_q,
            self.i_q,
            self.p_q,
            self.cutoff,
        )

    def as_dss_vector(self) -> str:
        """O vetor pronto para a propriedade ``ZIPV``."""

        return "[" + ", ".join(_format_weight(v) for v in self.as_tuple()) + "]"


DEFAULT_ZIPV_COEFFICIENTS = ZipvCoefficients()


def zipv_sum_error(coefficients: ZipvCoefficients) -> str | None:
    """Mensagem quando os pesos não somam 1, ou ``None`` quando somam.

    Separada da invariante para poder ser usada tanto pelo diálogo — que bloqueia
    o OK — quanto pela leitura da preferência, que descarta um valor incoerente
    em vez de exportá-lo.
    """

    problems: list[str] = []
    for label, total in (
        ("ativa", coefficients.active_sum),
        ("reativa", coefficients.reactive_sum),
    ):
        if abs(total - 1.0) > ZIPV_SUM_TOLERANCE:
            problems.append(f"{label} soma {_format_weight(total)}")
    if not problems:
        return None
    return (
        "Os coeficientes ZIP devem somar 1 em cada potência: "
        + " e ".join(problems)
        + "."
    )


@dataclass(frozen=True, slots=True)
class OpenDssLoadSettings:
    """Parâmetros globais aplicados às cargas do modelo.

    ``voltage_limits_enabled`` desligado e ``load_model`` em potência constante
    é o estado padrão e significa "não mudar nada": o arquivo sai idêntico ao que
    a exportação produzia antes desta configuração existir, e o OpenDSS aplica os
    padrões dele. Os valores continuam guardados enquanto desligados, para o
    usuário não precisar redigitá-los ao reativar.

    O modelo vale **só para as cargas de consumo**. Geradores, capacitores,
    ramais equivalentes e as cargas de energia da alocação continuam em potência
    constante — eles são ``Load`` por dialeto do exportador, não por natureza.
    """

    voltage_limits_enabled: bool = False
    vminpu: float = DEFAULT_VMINPU
    vmaxpu: float = DEFAULT_VMAXPU
    load_model: OpenDssLoadModel = DEFAULT_OPENDSS_LOAD_MODEL
    zipv: ZipvCoefficients = field(default_factory=ZipvCoefficients)

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
        if not isinstance(self.load_model, OpenDssLoadModel):
            raise ValueError("o modelo de carga deve ser um OpenDssLoadModel.")
        if not isinstance(self.zipv, ZipvCoefficients):
            raise ValueError("os coeficientes ZIPV devem ser um ZipvCoefficients.")

    @property
    def is_default(self) -> bool:
        """``True`` quando nem o master nem os arquivos de carga mudam."""

        return (
            not self.voltage_limits_enabled
            and self.load_model is OpenDssLoadModel.CONSTANT_POWER
        )

    def load_model_directive(self) -> str:
        """O trecho ``model=…`` da linha ``New Load`` de uma carga de consumo.

        Em potência constante devolve exatamente o que o exportador sempre
        emitiu, para o arquivo não mudar sem o usuário pedir. Em ZIPV acrescenta
        o vetor de sete valores, que só tem efeito com ``model=8``.
        """

        if self.load_model is OpenDssLoadModel.ZIPV:
            return f"model=8 ZIPV={self.zipv.as_dss_vector()}"
        return "model=1"

    def batch_edit_commands(self) -> tuple[str, ...]:
        """Comandos que aplicam os limites a todas as cargas já definidas.

        ``BatchEdit`` é comando **executivo**: exige os objetos já criados, por
        isso quem o emite precisa colocá-lo depois dos ``Redirect`` dos arquivos
        de carga. Desligado, devolve vazio — e não os comandos com os valores
        padrão, para o arquivo não mudar sem o usuário pedir.

        O ``Load..*`` atinge **toda** ``Load``, inclusive as sintéticas de
        gerador e de capacitor. Para os limites de tensão isso é aceitável e é o
        comportamento histórico; é justamente por isso que o modelo de carga
        **não** usa este caminho, e sim a emissão por elemento.
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

        values = {
            "voltage_limits_enabled": "1" if self.voltage_limits_enabled else "0",
            "vminpu": _format_pu(self.vminpu),
            "vmaxpu": _format_pu(self.vmaxpu),
            "load_model": self.load_model.value,
        }
        for name, value in zip(_ZIPV_FIELDS, self.zipv.as_tuple(), strict=True):
            values[f"zipv_{name}"] = _format_weight(value)
        return values


_ZIPV_FIELDS = ("z_p", "i_p", "p_p", "z_q", "i_q", "p_q", "cutoff")

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
    load_model = parse_opendss_load_model(values.get("load_model"))

    defaults = DEFAULT_ZIPV_COEFFICIENTS.as_tuple()
    parsed: list[float] = []
    for name, fallback in zip(_ZIPV_FIELDS, defaults, strict=True):
        number = parse_number(str(values.get(f"zipv_{name}", "")))
        parsed.append(fallback if number is None else number)
    try:
        zipv = ZipvCoefficients(*parsed)
    except ValueError:
        zipv = DEFAULT_ZIPV_COEFFICIENTS
    # Um vetor cuja soma não fecha nunca chegaria ao arquivo pelo diálogo, que
    # bloqueia o OK. Vindo do registro, ele é descartado aqui pela mesma razão:
    # exportá-lo mudaria a potência de todo o circuito em silêncio.
    if zipv_sum_error(zipv) is not None:
        zipv = DEFAULT_ZIPV_COEFFICIENTS
        load_model = DEFAULT_OPENDSS_LOAD_MODEL

    try:
        return OpenDssLoadSettings(
            voltage_limits_enabled=enabled,
            vminpu=vminpu,
            vmaxpu=vmaxpu,
            load_model=load_model,
            zipv=zipv,
        )
    except ValueError:
        return DEFAULT_OPENDSS_LOAD_SETTINGS


__all__ = [
    "DEFAULT_OPENDSS_LOAD_MODEL",
    "DEFAULT_OPENDSS_LOAD_SETTINGS",
    "DEFAULT_VMAXPU",
    "DEFAULT_VMINPU",
    "DEFAULT_ZIPV_COEFFICIENTS",
    "OpenDssLoadModel",
    "OpenDssLoadSettings",
    "VMAXPU_RANGE",
    "VMINPU_RANGE",
    "ZIPV_CUTOFF_RANGE",
    "ZIPV_SUM_TOLERANCE",
    "ZIPV_WEIGHT_RANGE",
    "ZipvCoefficients",
    "parse_opendss_load_model",
    "settings_from_mapping",
    "zipv_sum_error",
]
