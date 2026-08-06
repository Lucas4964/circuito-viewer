"""Testes de interface das Configurações do OpenDSS.

Cobrem o diálogo, a persistência em ``QSettings`` e a integração com a janela
principal. Nenhum toca a DLL: o efeito no arquivo é verificado em
``test_opendss_export.py`` e o efeito no solve, em ``test_opendss_powerflow.py``.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox

    from circuit_viewer.main_window import MainWindow
    from circuit_viewer.opendss_settings import (
        DEFAULT_OPENDSS_LOAD_SETTINGS,
        DEFAULT_VMAXPU,
        DEFAULT_VMINPU,
        OpenDssLoadSettings,
    )
    from circuit_viewer.opendss_settings_dialog import (
        OpenDssSettingsDialog,
        load_opendss_settings,
        save_opendss_settings,
    )

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


def isolated_settings(name: str) -> QSettings:
    """QSettings próprio do teste, para não tocar a preferência real."""

    settings = QSettings("CircuitViewerTests", name)
    settings.clear()
    return settings


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class PersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.settings = isolated_settings("persistence")
        self.addCleanup(self.settings.clear)

    def test_empty_storage_yields_the_default(self) -> None:
        self.assertEqual(
            load_opendss_settings(self.settings),
            DEFAULT_OPENDSS_LOAD_SETTINGS,
        )

    def test_round_trip(self) -> None:
        chosen = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.82,
            vmaxpu=1.18,
        )

        save_opendss_settings(self.settings, chosen)

        self.assertEqual(load_opendss_settings(self.settings), chosen)

    def test_corrupted_storage_falls_back_without_raising(self) -> None:
        self.settings.setValue("opendss/load_vminpu", "não é número")
        self.settings.setValue("opendss/load_vmaxpu", "1.2")
        self.settings.setValue("opendss/load_voltage_limits_enabled", "1")

        result = load_opendss_settings(self.settings)

        self.assertEqual(result.vminpu, DEFAULT_VMINPU)

    def test_an_incoherent_stored_band_falls_back_entirely(self) -> None:
        # Faixa que não contém a tensão nominal: gravada à mão ou por uma
        # versão anterior do formato.
        self.settings.setValue("opendss/load_voltage_limits_enabled", "1")
        self.settings.setValue("opendss/load_vminpu", "1.3")
        self.settings.setValue("opendss/load_vmaxpu", "1.4")

        self.assertEqual(
            load_opendss_settings(self.settings),
            DEFAULT_OPENDSS_LOAD_SETTINGS,
        )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, settings: OpenDssLoadSettings | None = None):  # noqa: ANN202
        dialog = OpenDssSettingsDialog(settings)
        self.addCleanup(dialog.close)
        return dialog

    def test_opens_showing_the_current_values(self) -> None:
        dialog = self._dialog(
            OpenDssLoadSettings(
                voltage_limits_enabled=True,
                vminpu=0.8,
                vmaxpu=1.2,
            )
        )

        self.assertTrue(dialog.apply_limits_check.isChecked())
        self.assertAlmostEqual(dialog.vminpu_input.value(), 0.8)
        self.assertAlmostEqual(dialog.vmaxpu_input.value(), 1.2)

    def test_opens_on_the_defaults_without_a_stored_value(self) -> None:
        dialog = self._dialog()

        self.assertFalse(dialog.apply_limits_check.isChecked())
        self.assertAlmostEqual(dialog.vminpu_input.value(), DEFAULT_VMINPU)
        self.assertAlmostEqual(dialog.vmaxpu_input.value(), DEFAULT_VMAXPU)

    def test_the_checkbox_governs_the_fields(self) -> None:
        dialog = self._dialog()

        self.assertFalse(dialog.fields.isEnabled())
        dialog.apply_limits_check.setChecked(True)
        self.assertTrue(dialog.fields.isEnabled())
        dialog.apply_limits_check.setChecked(False)
        self.assertFalse(dialog.fields.isEnabled())

    def test_the_ranges_forbid_a_band_excluding_the_nominal_voltage(self) -> None:
        dialog = self._dialog()

        # Os campos recortam o valor: nem digitando é possível sair da faixa
        # que a invariante da dataclass exige.
        dialog.vminpu_input.setValue(1.5)
        dialog.vmaxpu_input.setValue(0.5)

        self.assertLessEqual(dialog.vminpu_input.value(), 1.0)
        self.assertGreaterEqual(dialog.vmaxpu_input.value(), 1.0)
        self.assertEqual(
            dialog.settings(),
            OpenDssLoadSettings(
                voltage_limits_enabled=False,
                vminpu=dialog.vminpu_input.value(),
                vmaxpu=dialog.vmaxpu_input.value(),
            ),
        )

    def test_preview_shows_the_commands_that_will_be_emitted(self) -> None:
        dialog = self._dialog()

        # Desmarcado, a pré-visualização diz que nada será acrescentado.
        self.assertIn("nenhum", dialog.preview_label.text())

        dialog.apply_limits_check.setChecked(True)
        dialog.vminpu_input.setValue(0.8)
        dialog.vmaxpu_input.setValue(1.2)

        self.assertEqual(
            dialog.preview_label.text().splitlines(),
            [
                "BatchEdit Load..* vminpu=0.8",
                "BatchEdit Load..* vmaxpu=1.2",
            ],
        )

    def test_preview_comes_from_the_same_source_as_the_file(self) -> None:
        dialog = self._dialog()
        dialog.apply_limits_check.setChecked(True)
        dialog.vminpu_input.setValue(0.875)

        self.assertEqual(
            dialog.preview_label.text().splitlines(),
            list(dialog.settings().batch_edit_commands()),
        )

    def test_restore_defaults(self) -> None:
        dialog = self._dialog(
            OpenDssLoadSettings(
                voltage_limits_enabled=True,
                vminpu=0.7,
                vmaxpu=1.3,
            )
        )

        button = dialog.buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults
        )
        button.click()

        self.assertEqual(dialog.settings(), DEFAULT_OPENDSS_LOAD_SETTINGS)


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class MainWindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, settings: QSettings | None = None) -> MainWindow:
        window = MainWindow(settings=settings or isolated_settings("window"))
        self.addCleanup(window.close)
        window.show()
        return window

    def test_the_menu_entry_exists_and_needs_no_imported_data(self) -> None:
        window = self._window()

        settings_menu = next(
            entry.menu()
            for entry in window.menuBar().actions()
            if entry.text() == "Configurações"
        )

        self.assertIn(window.opendss_settings_action, settings_menu.actions())
        # É preferência, não análise: nunca depende de dado importado.
        self.assertTrue(window.opendss_settings_action.isEnabled())

    def test_the_window_starts_from_the_stored_preference(self) -> None:
        storage = isolated_settings("stored")
        self.addCleanup(storage.clear)
        chosen = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.85,
            vmaxpu=1.15,
        )
        save_opendss_settings(storage, chosen)

        window = self._window(storage)

        self.assertEqual(window._opendss_load_settings, chosen)

    def test_accepting_the_dialog_stores_the_choice(self) -> None:
        storage = isolated_settings("accept")
        self.addCleanup(storage.clear)
        window = self._window(storage)

        def accept(dialog) -> int:  # noqa: ANN001
            dialog.apply_limits_check.setChecked(True)
            dialog.vminpu_input.setValue(0.8)
            dialog.vmaxpu_input.setValue(1.2)
            return int(QDialog.DialogCode.Accepted)

        with patch.object(OpenDssSettingsDialog, "exec", accept):
            window._show_opendss_settings()

        expected = OpenDssLoadSettings(
            voltage_limits_enabled=True,
            vminpu=0.8,
            vmaxpu=1.2,
        )
        self.assertEqual(window._opendss_load_settings, expected)
        # E persiste: uma janela nova sobre o mesmo armazenamento já nasce com
        # os valores.
        self.assertEqual(load_opendss_settings(storage), expected)

    def test_cancelling_changes_nothing(self) -> None:
        window = self._window()
        before = window._opendss_load_settings

        def reject(dialog) -> int:  # noqa: ANN001
            dialog.apply_limits_check.setChecked(True)
            dialog.vminpu_input.setValue(0.7)
            return int(QDialog.DialogCode.Rejected)

        with patch.object(OpenDssSettingsDialog, "exec", reject):
            window._show_opendss_settings()

        self.assertEqual(window._opendss_load_settings, before)

    def test_changing_the_limits_discards_a_power_flow_result(self) -> None:
        window = self._window()
        # Um resultado qualquer basta: o que se verifica é a invalidação.
        window._power_flow_result = object()

        def accept(dialog) -> int:  # noqa: ANN001
            dialog.apply_limits_check.setChecked(True)
            dialog.vminpu_input.setValue(0.8)
            return int(QDialog.DialogCode.Accepted)

        with patch.object(OpenDssSettingsDialog, "exec", accept):
            window._show_opendss_settings()

        # O resultado antigo descreveria um modelo que já não é o configurado.
        self.assertIsNone(window._power_flow_result)
        self.assertFalse(window.segment_power_flow_section.isVisible())
        self.assertFalse(window.bar_power_flow_section.isVisible())

    def test_reopening_the_dialog_without_changes_keeps_the_result(self) -> None:
        window = self._window()
        marker = object()
        window._power_flow_result = marker

        with patch.object(
            OpenDssSettingsDialog,
            "exec",
            lambda dialog: int(QDialog.DialogCode.Accepted),
        ):
            window._show_opendss_settings()

        # Aceitar sem mudar nada não invalida: o modelo continua o mesmo.
        self.assertIs(window._power_flow_result, marker)

    def test_the_status_message_describes_the_choice(self) -> None:
        window = self._window()

        def accept(dialog) -> int:  # noqa: ANN001
            dialog.apply_limits_check.setChecked(True)
            dialog.vminpu_input.setValue(0.8)
            return int(QDialog.DialogCode.Accepted)

        with patch.object(OpenDssSettingsDialog, "exec", accept):
            window._show_opendss_settings()

        self.assertIn("vminpu", window.statusBar().currentMessage())

    def test_turning_the_limits_off_says_so(self) -> None:
        storage = isolated_settings("off")
        self.addCleanup(storage.clear)
        save_opendss_settings(
            storage,
            OpenDssLoadSettings(
                voltage_limits_enabled=True,
                vminpu=0.8,
                vmaxpu=1.2,
            ),
        )
        window = self._window(storage)

        with patch.object(
            OpenDssSettingsDialog,
            "exec",
            lambda dialog: (
                dialog.apply_limits_check.setChecked(False),
                int(QDialog.DialogCode.Accepted),
            )[1],
        ):
            window._show_opendss_settings()

        self.assertTrue(window._opendss_load_settings.is_default)
        self.assertIn("0,95", window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
