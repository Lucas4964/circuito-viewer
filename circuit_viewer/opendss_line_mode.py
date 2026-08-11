"""Modo de obtenção dos parâmetros elétricos das linhas OpenDSS."""

from __future__ import annotations

from enum import Enum


class OpenDssLineParameterMode(str, Enum):
    """Fonte dos parâmetros usada ao exportar elementos ``Line``."""

    ORIGINAL = "original"
    LIBRARY = "library"


DEFAULT_OPENDSS_LINE_PARAMETER_MODE = OpenDssLineParameterMode.ORIGINAL


def parse_opendss_line_parameter_mode(value: object) -> OpenDssLineParameterMode:
    """Converte um valor persistido; ausência ou corrupção usam o padrão."""

    if isinstance(value, OpenDssLineParameterMode):
        return value
    try:
        normalized = str(value).strip().casefold()
        return OpenDssLineParameterMode(normalized)
    except (TypeError, ValueError):
        return DEFAULT_OPENDSS_LINE_PARAMETER_MODE


__all__ = [
    "DEFAULT_OPENDSS_LINE_PARAMETER_MODE",
    "OpenDssLineParameterMode",
    "parse_opendss_line_parameter_mode",
]
