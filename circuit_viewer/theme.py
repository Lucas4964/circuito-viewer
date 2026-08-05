"""Tema claro/escuro escolhido manualmente pelo usuário.

O estilo nativo do Qt (``windows11``) ignora a paleta da aplicação e segue o
tema do sistema operacional. Para que a escolha seja realmente manual, o tema é
aplicado trocando o estilo para ``Fusion`` e instalando uma paleta explícita.

As duas paletas têm valores fixos de propósito: a partir do Qt 6.8 o
``standardPalette()`` do Fusion passou a acompanhar o esquema de cores do
sistema, o que reintroduziria exatamente a inferência que queremos evitar.
"""

from __future__ import annotations

from enum import StrEnum

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


class AppTheme(StrEnum):
    LIGHT = "light"
    DARK = "dark"


DEFAULT_THEME = AppTheme.LIGHT
THEME_SETTINGS_KEY = "appearance/theme"
THEME_LABELS: dict[AppTheme, str] = {
    AppTheme.LIGHT: "Claro",
    AppTheme.DARK: "Escuro",
}

# Cada tema declara os papéis do grupo ativo e, separadamente, os papéis do
# grupo desabilitado. Sem o grupo desabilitado as ações inativas (Enquadrar
# tudo, Ramais…, Buscar) ficam ilegíveis no tema escuro.
_LIGHT_ROLES: dict[QPalette.ColorRole, str] = {
    QPalette.ColorRole.Window: "#EFEFEF",
    QPalette.ColorRole.WindowText: "#000000",
    QPalette.ColorRole.Base: "#FFFFFF",
    QPalette.ColorRole.AlternateBase: "#F7F7F7",
    QPalette.ColorRole.ToolTipBase: "#FFFFDC",
    QPalette.ColorRole.ToolTipText: "#000000",
    QPalette.ColorRole.Text: "#000000",
    QPalette.ColorRole.PlaceholderText: "#808080",
    QPalette.ColorRole.Button: "#EFEFEF",
    QPalette.ColorRole.ButtonText: "#000000",
    QPalette.ColorRole.BrightText: "#FF0000",
    QPalette.ColorRole.Light: "#FFFFFF",
    QPalette.ColorRole.Midlight: "#F5F5F5",
    QPalette.ColorRole.Mid: "#B8B8B8",
    QPalette.ColorRole.Dark: "#9F9F9F",
    QPalette.ColorRole.Shadow: "#6E6E6E",
    QPalette.ColorRole.Link: "#0000FF",
    QPalette.ColorRole.LinkVisited: "#800080",
    QPalette.ColorRole.Highlight: "#308CC6",
    QPalette.ColorRole.HighlightedText: "#FFFFFF",
}

_LIGHT_DISABLED_ROLES: dict[QPalette.ColorRole, str] = {
    QPalette.ColorRole.WindowText: "#A0A0A0",
    QPalette.ColorRole.Text: "#A0A0A0",
    QPalette.ColorRole.ButtonText: "#A0A0A0",
    QPalette.ColorRole.Highlight: "#D0D0D0",
    QPalette.ColorRole.HighlightedText: "#7A7A7A",
}

_DARK_ROLES: dict[QPalette.ColorRole, str] = {
    QPalette.ColorRole.Window: "#1E1E1E",
    QPalette.ColorRole.WindowText: "#F0F0F0",
    QPalette.ColorRole.Base: "#2A2A2A",
    QPalette.ColorRole.AlternateBase: "#3A3A3A",
    QPalette.ColorRole.ToolTipBase: "#3A3A3A",
    QPalette.ColorRole.ToolTipText: "#F0F0F0",
    QPalette.ColorRole.Text: "#F0F0F0",
    QPalette.ColorRole.PlaceholderText: "#909090",
    QPalette.ColorRole.Button: "#3A3A3A",
    QPalette.ColorRole.ButtonText: "#F0F0F0",
    QPalette.ColorRole.BrightText: "#FF5555",
    QPalette.ColorRole.Light: "#5A5A5A",
    QPalette.ColorRole.Midlight: "#484848",
    QPalette.ColorRole.Mid: "#6A6A6A",
    QPalette.ColorRole.Dark: "#232323",
    QPalette.ColorRole.Shadow: "#141414",
    QPalette.ColorRole.Link: "#4FA3E3",
    QPalette.ColorRole.LinkVisited: "#B08CD9",
    QPalette.ColorRole.Highlight: "#2A82DA",
    QPalette.ColorRole.HighlightedText: "#101010",
}

_DARK_DISABLED_ROLES: dict[QPalette.ColorRole, str] = {
    QPalette.ColorRole.WindowText: "#8A8A8A",
    QPalette.ColorRole.Text: "#8A8A8A",
    QPalette.ColorRole.ButtonText: "#8A8A8A",
    QPalette.ColorRole.Highlight: "#4A4A4A",
    QPalette.ColorRole.HighlightedText: "#9A9A9A",
}

_THEME_ROLES: dict[
    AppTheme,
    tuple[dict[QPalette.ColorRole, str], dict[QPalette.ColorRole, str]],
] = {
    AppTheme.LIGHT: (_LIGHT_ROLES, _LIGHT_DISABLED_ROLES),
    AppTheme.DARK: (_DARK_ROLES, _DARK_DISABLED_ROLES),
}


def load_theme_preference(settings: QSettings) -> AppTheme:
    """Lê o tema salvo; valores ausentes ou inválidos caem no padrão."""

    stored = settings.value(THEME_SETTINGS_KEY)
    if stored is None:
        return DEFAULT_THEME
    try:
        return AppTheme(str(stored).strip().lower())
    except ValueError:
        return DEFAULT_THEME


def save_theme_preference(settings: QSettings, theme: AppTheme) -> None:
    settings.setValue(THEME_SETTINGS_KEY, AppTheme(theme).value)
    settings.sync()


def build_palette(theme: AppTheme) -> QPalette:
    """Monta a paleta completa do tema, sem consultar o sistema operacional."""

    active_roles, disabled_roles = _THEME_ROLES[AppTheme(theme)]
    palette = QPalette()
    for role, color in active_roles.items():
        palette.setColor(role, QColor(color))
    for role, color in disabled_roles.items():
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            role,
            QColor(color),
        )
    return palette


def apply_theme(app: QApplication, theme: AppTheme) -> None:
    """Aplica o tema à aplicação inteira, incluindo widgets já construídos."""

    selected = AppTheme(theme)
    # Fusion honra a paleta da aplicação; os estilos nativos, não.
    if (app.style().objectName() or "").lower() != "fusion":
        app.setStyle("Fusion")
    app.setPalette(build_palette(selected))

    # Qt ≥ 6.8: fixar o esquema deixa a barra de título nativa coerente e
    # impede que o Qt reaplique a paleta padrão quando o SO mudar de tema.
    style_hints = app.styleHints()
    color_scheme = getattr(Qt, "ColorScheme", None)
    if color_scheme is not None and hasattr(style_hints, "setColorScheme"):
        style_hints.setColorScheme(
            color_scheme.Dark if selected is AppTheme.DARK else color_scheme.Light
        )

    # As folhas de estilo que referenciam palette(...) só são reavaliadas depois
    # de um ciclo de unpolish/polish. Usa-se o estilo do próprio widget porque
    # quem define setStyleSheet() recebe um QStyleSheetStyle dedicado.
    for widget in app.allWidgets():
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()
