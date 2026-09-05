"""Paleta perceptual para circuitos, sem dependência de Qt."""

from __future__ import annotations

import math
import random
import re
import secrets


MIN_WHITE_CONTRAST = 3.0
GOLDEN_ANGLE_DEGREES = 137.50776405003785
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_hex_color(value: str) -> str:
    color = str(value).strip()
    if not _HEX_COLOR.fullmatch(color):
        raise ValueError("A cor deve usar o formato #RRGGBB.")
    return color.upper()


def _oklch_to_linear_srgb(
    lightness: float,
    chroma: float,
    hue_degrees: float,
) -> tuple[float, float, float]:
    hue = math.radians(hue_degrees)
    a = chroma * math.cos(hue)
    b = chroma * math.sin(hue)
    l_root = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_root = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_root = lightness - 0.0894841775 * a - 1.2914855480 * b
    l_value = l_root**3
    m_value = m_root**3
    s_value = s_root**3
    return (
        4.0767416621 * l_value
        - 3.3077115913 * m_value
        + 0.2309699292 * s_value,
        -1.2684380046 * l_value
        + 2.6097574011 * m_value
        - 0.3413193965 * s_value,
        -0.0041960863 * l_value
        - 0.7034186147 * m_value
        + 1.7076147010 * s_value,
    )


def _linear_to_srgb(value: float) -> float:
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def _srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    red, green, blue = (_srgb_to_linear(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio_with_white(color: str) -> float:
    normalized = normalize_hex_color(color)
    rgb = tuple(int(normalized[index : index + 2], 16) / 255.0 for index in (1, 3, 5))
    return 1.05 / (_relative_luminance(rgb) + 0.05)


def contrasting_text_color(background: str) -> str:
    """Escolhe preto ou branco com o maior contraste WCAG sobre ``background``."""

    normalized = normalize_hex_color(background)
    rgb = tuple(
        int(normalized[index : index + 2], 16) / 255.0
        for index in (1, 3, 5)
    )
    luminance = _relative_luminance(rgb)
    white_contrast = 1.05 / (luminance + 0.05)
    black_contrast = (luminance + 0.05) / 0.05
    return "#FFFFFF" if white_contrast >= black_contrast else "#000000"


def _oklch_candidate(
    lightness: float,
    chroma: float,
    hue_degrees: float,
) -> tuple[str, tuple[float, float, float]]:
    current_lightness = lightness
    current_chroma = chroma
    while True:
        linear = _oklch_to_linear_srgb(
            current_lightness,
            current_chroma,
            hue_degrees,
        )
        if all(0.0 <= value <= 1.0 for value in linear):
            rgb = tuple(_linear_to_srgb(value) for value in linear)
            if 1.05 / (_relative_luminance(rgb) + 0.05) >= MIN_WHITE_CONTRAST:
                break
            current_lightness -= 0.012
        else:
            current_chroma *= 0.92
        if current_lightness < 0.40:
            current_lightness = 0.40
            current_chroma = min(current_chroma, 0.10)
    channels = tuple(max(0, min(255, round(value * 255))) for value in rgb)
    color = "#{:02X}{:02X}{:02X}".format(*channels)
    hue = math.radians(hue_degrees)
    lab = (
        current_lightness,
        current_chroma * math.cos(hue),
        current_chroma * math.sin(hue),
    )
    return color, lab


def _distance(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second, strict=True)))


def generate_circuit_palette(
    count: int,
    *,
    seed: int | None = None,
) -> tuple[str, ...]:
    """Gera cores sRGB contrastantes usando amostragem gulosa em OKLCH."""

    if count < 0:
        raise ValueError("A quantidade de cores não pode ser negativa.")
    if count == 0:
        return ()
    generator = random.Random(secrets.randbits(64) if seed is None else seed)
    phase = generator.random() * 360.0
    lightness_bands = (0.56, 0.62, 0.50)
    chroma_bands = (0.17, 0.14, 0.20)
    colors: list[str] = []
    labs: list[tuple[float, float, float]] = []
    used: set[str] = set()

    for index in range(count):
        base_hue = (phase + index * GOLDEN_ANGLE_DEGREES) % 360.0
        best: tuple[float, str, tuple[float, float, float]] | None = None
        # Diversos candidatos próximos à sequência áurea evitam cores quase
        # coincidentes sem transformar a geração em uma busca combinatória.
        for attempt in range(36):
            hue = (base_hue + attempt * GOLDEN_ANGLE_DEGREES) % 360.0
            band = (index + attempt) % len(lightness_bands)
            color, lab = _oklch_candidate(
                lightness_bands[band],
                chroma_bands[band],
                hue,
            )
            if color in used:
                continue
            score = min((_distance(lab, other) for other in labs), default=1.0)
            candidate = (score, color, lab)
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is None:
            # Só é alcançado em paletas excepcionalmente grandes. Pequenas
            # variações de matiz mantêm a cor válida e evitam repetição exata.
            offset = 0
            while True:
                color, lab = _oklch_candidate(
                    0.50,
                    0.12,
                    (base_hue + offset * 0.61803398875) % 360.0,
                )
                if color not in used:
                    best = (0.0, color, lab)
                    break
                offset += 1
        _, color, lab = best
        colors.append(color)
        labs.append(lab)
        used.add(color)
    return tuple(colors)
