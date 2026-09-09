"""Saneamento de nomes do OpenDSS, isolado de quem exporta.

Mora aqui, e não em ``opendss_export``, porque a composição de fontes precisa da
mesma função para detectar colisão de ``CODIGO`` no espaço de nomes que o
OpenDSS vai enxergar — e fazer a camada de composição importar a camada de
exportação seria de trás para frente. ``opendss_export`` reexporta o nome, então
todo importador existente continua valendo.
"""

from __future__ import annotations

import re
import unicodedata


_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def sanitize_dss_name(value: str) -> str:
    """Reduz um código a um nome aceito pelo OpenDSS.

    O ponto separa nós de barra e o espaço separa propriedades, então nenhum dos
    dois pode sobreviver em um nome. Acentos são reduzidos a ASCII para o
    arquivo continuar legível por instalações que não leem UTF-8.
    """

    decomposed = unicodedata.normalize("NFKD", str(value))
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _INVALID_NAME_CHARS.sub("_", ascii_only).strip("_")
