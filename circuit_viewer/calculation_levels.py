"""Definições dos quatro patamares horários usados nos cálculos elétricos.

Este módulo não depende de Qt nem toca o disco. O cadastro salvo é imutável;
somente a janela trabalha com :class:`CalculationLevelDraft`, impedindo que
edições ainda não confirmadas vazem para consumidores futuros.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


CALCULATION_LEVEL_COUNT = 4
MAX_CALCULATION_LEVEL_NAME_LENGTH = 60


def _clean_name(value: object) -> str:
    return " ".join(str(value).split())


def _validate_integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} deve ser um número inteiro.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} deve estar entre {minimum} e {maximum}.")
    return value


@dataclass(frozen=True, slots=True)
class CalculationLevel:
    """Um patamar completo e individualmente válido."""

    npat: int
    name: str
    start_hour: int
    end_hour: int
    reference_hour: int

    def __post_init__(self) -> None:
        npat = _validate_integer(self.npat, "NPAT", 0, 3)
        if not isinstance(self.name, str):
            raise ValueError(f"NOME do patamar {npat} deve ser texto.")
        name = _clean_name(self.name)
        if not name:
            raise ValueError(f"Informe o NOME do patamar {npat}.")
        if len(name) > MAX_CALCULATION_LEVEL_NAME_LENGTH:
            raise ValueError(
                f"O NOME do patamar {npat} deve ter no máximo "
                f"{MAX_CALCULATION_LEVEL_NAME_LENGTH} caracteres."
            )
        start = _validate_integer(self.start_hour, "HORARIO_INI", 0, 23)
        end = _validate_integer(self.end_hour, "HORARIO_FIM", 0, 23)
        reference = _validate_integer(
            self.reference_hour, "HORARIO_REF", 0, 23
        )
        if start == end:
            raise ValueError(
                f"O patamar {npat} deve possuir horários inicial e final diferentes."
            )
        duration = (end - start) % 24
        reference_offset = (reference - start) % 24
        if reference_offset > duration:
            raise ValueError(
                f"HORARIO_REF do patamar {npat} deve pertencer ao intervalo "
                f"de {start} a {end}."
            )
        object.__setattr__(self, "name", name)

    @property
    def duration(self) -> int:
        """Duração circular em horas, sempre entre 1 e 23."""

        return (self.end_hour - self.start_hour) % 24


@dataclass(frozen=True, slots=True)
class CalculationLevelSchedule:
    """Grade imutável de quatro patamares contíguos cobrindo um dia."""

    levels: tuple[CalculationLevel, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(tuple(self.levels), key=lambda item: item.npat))
        if len(ordered) != CALCULATION_LEVEL_COUNT:
            raise ValueError("A configuração deve conter exatamente quatro patamares.")
        if tuple(item.npat for item in ordered) != tuple(
            range(CALCULATION_LEVEL_COUNT)
        ):
            raise ValueError("NPAT deve conter exatamente os valores 0, 1, 2 e 3.")
        names = [item.name.casefold() for item in ordered]
        if len(set(names)) != len(names):
            raise ValueError("Os nomes dos patamares não podem se repetir.")
        for index, current in enumerate(ordered):
            following = ordered[(index + 1) % len(ordered)]
            if current.end_hour != following.start_hour:
                raise ValueError(
                    f"Há uma lacuna ou sobreposição entre os patamares "
                    f"{current.npat} e {following.npat}: "
                    f"{current.end_hour} é diferente de {following.start_hour}."
                )
        if sum(item.duration for item in ordered) != 24:
            raise ValueError("Os quatro patamares devem cobrir exatamente 24 horas.")
        object.__setattr__(self, "levels", ordered)

    def __len__(self) -> int:
        return len(self.levels)

    def level(self, row: int) -> CalculationLevel:
        return self.levels[row]


@dataclass(slots=True)
class CalculationLevelDraft:
    """Linha mutável da tabela; só vira dado oficial no salvamento."""

    npat: int | None
    name: str
    start_hour: int | None
    end_hour: int | None
    reference_hour: int | None

    @classmethod
    def from_level(cls, level: CalculationLevel) -> CalculationLevelDraft:
        return cls(
            level.npat,
            level.name,
            level.start_hour,
            level.end_hour,
            level.reference_hour,
        )

    def to_level(self) -> CalculationLevel:
        return CalculationLevel(
            self.npat,  # type: ignore[arg-type]
            self.name,
            self.start_hour,  # type: ignore[arg-type]
            self.end_hour,  # type: ignore[arg-type]
            self.reference_hour,  # type: ignore[arg-type]
        )


class CalculationLevelCatalog:
    """Quatro rascunhos editáveis, independentes do conjunto salvo."""

    __slots__ = ("_drafts",)

    def __init__(self, drafts: Iterable[CalculationLevelDraft]) -> None:
        self._drafts = list(drafts)
        if len(self._drafts) != CALCULATION_LEVEL_COUNT:
            raise ValueError("O rascunho deve conter exatamente quatro patamares.")

    @classmethod
    def from_schedule(
        cls, schedule: CalculationLevelSchedule
    ) -> CalculationLevelCatalog:
        return cls(CalculationLevelDraft.from_level(item) for item in schedule.levels)

    def __len__(self) -> int:
        return len(self._drafts)

    @property
    def drafts(self) -> tuple[CalculationLevelDraft, ...]:
        return tuple(self._drafts)

    def draft(self, row: int) -> CalculationLevelDraft:
        return self._drafts[row]

    def to_schedule(self) -> CalculationLevelSchedule:
        return CalculationLevelSchedule(tuple(item.to_level() for item in self._drafts))

    def replace(self, schedule: CalculationLevelSchedule) -> None:
        self._drafts = [
            CalculationLevelDraft.from_level(item) for item in schedule.levels
        ]


def default_calculation_levels() -> CalculationLevelSchedule:
    """Grade inicial reproduzindo o cadastro de referência da aplicação."""

    return CalculationLevelSchedule(
        (
            CalculationLevel(0, "Madrugada", 22, 5, 23),
            CalculationLevel(1, "Manhã", 5, 11, 11),
            CalculationLevel(2, "Tarde", 11, 18, 12),
            CalculationLevel(3, "Noite", 18, 22, 22),
        )
    )
