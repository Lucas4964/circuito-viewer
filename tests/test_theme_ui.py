from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QSettings
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer import graphics
    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.theme import (
        DEFAULT_THEME,
        THEME_LABELS,
        THEME_SETTINGS_KEY,
        AppTheme,
        apply_theme,
        build_palette,
        load_theme_preference,
        save_theme_preference,
    )

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # A troca de estilo e de paleta é global: sem restaurar o estado, os
        # demais arquivos de teste herdariam o tema aplicado aqui.
        self._original_style = self.app.style().objectName()
        self._original_palette = QPalette(self.app.palette())
        self._directory = tempfile.TemporaryDirectory()
        self.settings = QSettings(
            os.path.join(self._directory.name, "settings.ini"),
            QSettings.Format.IniFormat,
        )

    def tearDown(self) -> None:
        self.app.setStyle(self._original_style)
        self.app.setPalette(self._original_palette)
        self._directory.cleanup()

    def _make_window(self) -> MainWindow:
        window = MainWindow(settings=self.settings)
        self.addCleanup(window.close)
        self.addCleanup(window.deleteLater)
        return window

    def test_missing_and_invalid_preferences_fall_back_to_light(self) -> None:
        self.assertIs(DEFAULT_THEME, AppTheme.LIGHT)
        self.assertIs(load_theme_preference(self.settings), AppTheme.LIGHT)

        self.settings.setValue(THEME_SETTINGS_KEY, "roxo")
        self.assertIs(load_theme_preference(self.settings), AppTheme.LIGHT)

        save_theme_preference(self.settings, AppTheme.DARK)
        self.assertIs(load_theme_preference(self.settings), AppTheme.DARK)
        self.assertEqual(self.settings.value(THEME_SETTINGS_KEY), "dark")

    def test_menu_exposes_two_exclusive_actions_for_the_saved_theme(self) -> None:
        window = self._make_window()

        self.assertEqual(len(window.theme_actions), 2)
        self.assertEqual(
            [action.text() for action in window.theme_group.actions()],
            [THEME_LABELS[AppTheme.LIGHT], THEME_LABELS[AppTheme.DARK]],
        )
        self.assertTrue(window.theme_group.isExclusive())
        for action in window.theme_actions.values():
            self.assertTrue(action.isCheckable())
        self.assertTrue(window.theme_actions[AppTheme.LIGHT].isChecked())
        self.assertFalse(window.theme_actions[AppTheme.DARK].isChecked())

        view_menu = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Visualizar"
        )
        theme_menu = next(
            entry.menu()
            for entry in view_menu.actions()
            if entry.text() == "Tema"
        )
        self.assertEqual(
            theme_menu.actions(),
            [
                window.theme_actions[AppTheme.LIGHT],
                window.theme_actions[AppTheme.DARK],
            ],
        )

    def test_choosing_dark_applies_and_persists_the_theme(self) -> None:
        window = self._make_window()

        window.theme_actions[AppTheme.DARK].trigger()

        palette = self.app.palette()
        self.assertLess(palette.color(QPalette.ColorRole.Window).lightness(), 128)
        self.assertGreater(
            palette.color(QPalette.ColorRole.WindowText).lightness(), 128
        )
        self.assertEqual(self.app.style().objectName().lower(), "fusion")
        self.assertTrue(window.theme_actions[AppTheme.DARK].isChecked())
        self.assertFalse(window.theme_actions[AppTheme.LIGHT].isChecked())
        self.assertIs(load_theme_preference(self.settings), AppTheme.DARK)

    def test_saved_theme_is_restored_by_a_new_window(self) -> None:
        save_theme_preference(self.settings, AppTheme.DARK)

        window = self._make_window()

        self.assertTrue(window.theme_actions[AppTheme.DARK].isChecked())
        self.assertFalse(window.theme_actions[AppTheme.LIGHT].isChecked())

    def test_canvas_stays_light_under_the_dark_theme(self) -> None:
        window = self._make_window()

        window.theme_actions[AppTheme.DARK].trigger()

        self.assertEqual(
            window.view.backgroundBrush().color(),
            graphics.CANVAS_BACKGROUND,
        )
        self.assertEqual(graphics.POINT_COLOR.name().upper(), "#202020")
        self.assertEqual(graphics.LINE_COLOR.name().upper(), "#555555")

    def test_dark_palette_keeps_disabled_text_readable(self) -> None:
        palette = build_palette(AppTheme.DARK)
        window = palette.color(QPalette.ColorRole.Window)
        enabled = palette.color(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.WindowText,
        )
        disabled = palette.color(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
        )

        self.assertNotEqual(disabled, enabled)
        self.assertGreater(abs(disabled.lightness() - window.lightness()), 40)

    def test_apply_theme_switches_to_fusion_and_is_idempotent(self) -> None:
        apply_theme(self.app, AppTheme.DARK)
        self.assertEqual(self.app.style().objectName().lower(), "fusion")
        dark_window_color = self.app.palette().color(QPalette.ColorRole.Window)

        apply_theme(self.app, AppTheme.DARK)
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Window),
            dark_window_color,
        )

        apply_theme(self.app, AppTheme.LIGHT)
        self.assertGreater(
            self.app.palette().color(QPalette.ColorRole.Window).lightness(),
            128,
        )


if __name__ == "__main__":
    unittest.main()
