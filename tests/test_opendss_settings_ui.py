"""Testes de interface das Configurações do OpenDSS.

Cobrem o diálogo, a persistência em ``QSettings`` e a integração com a janela
principal. Nenhum toca a DLL: o efeito no arquivo é verificado em
``test_opendss_export.py`` e o efeito no solve, em ``test_opendss_powerflow.py``.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox

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
    from circuit_viewer.opendss_mapping_store import (
        LibraryNameMapping,
        OpenDssLibraryMappings,
        read_arrangement_map,
        read_cable_map,
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

    def test_dialog_has_the_three_named_tabs(self) -> None:
        dialog = self._dialog()

        self.assertEqual(
            [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())],
            ["Tensão das cargas", "Mapa de Cabos", "Mapa de Arranjos"],
        )

    def test_maps_show_saved_values_and_canonical_library_names(self) -> None:
        dialog = OpenDssSettingsDialog(
            mappings=OpenDssLibraryMappings(
                cables=(LibraryNameMapping(" 115 ", "cabo a"),),
                arrangements=(LibraryNameMapping(" 1 ", "arranjo a"),),
            ),
            cable_names=("CABO A", "CABO B"),
            arrangement_names=("ARRANJO A",),
        )
        self.addCleanup(dialog.close)

        cable_combo = dialog.cable_map_editor.table.cellWidget(0, 1)
        arrangement_combo = dialog.arrangement_map_editor.table.cellWidget(0, 1)
        self.assertIsInstance(cable_combo, QComboBox)
        self.assertIsInstance(arrangement_combo, QComboBox)
        self.assertEqual(dialog.cable_map_editor.table.item(0, 0).text(), "115")
        self.assertEqual(cable_combo.currentData(), "CABO A")
        self.assertEqual(arrangement_combo.currentData(), "ARRANJO A")
        self.assertEqual(
            dialog.mappings(),
            OpenDssLibraryMappings(
                cables=(LibraryNameMapping("115", "CABO A"),),
                arrangements=(LibraryNameMapping("1", "ARRANJO A"),),
            ),
        )

    def test_incomplete_and_duplicate_rows_disable_ok_but_reusing_a_name_is_valid(self) -> None:
        dialog = OpenDssSettingsDialog(cable_names=("CABO A",))
        self.addCleanup(dialog.close)
        editor = dialog.cable_map_editor
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)

        editor.add_empty_row()
        self.assertFalse(ok.isEnabled())
        editor.table.item(0, 0).setText(" 115 ")
        combo = editor.table.cellWidget(0, 1)
        combo.setCurrentIndex(combo.findData("CABO A"))
        self.assertTrue(ok.isEnabled())

        editor.add_empty_row()
        editor.table.item(1, 0).setText("115")
        combo = editor.table.cellWidget(1, 1)
        combo.setCurrentIndex(combo.findData("CABO A"))
        self.assertFalse(ok.isEnabled())
        self.assertIn("duplicado", editor.issue_label.text())

        editor.table.item(1, 0).setText("116")
        self.assertTrue(ok.isEnabled())
        self.assertEqual(
            [entry.source_id for entry in dialog.mappings().cables],
            ["115", "116"],
        )

    def test_missing_reference_and_corrupt_file_block_confirmation_until_cleared(self) -> None:
        dialog = OpenDssSettingsDialog(
            mappings=OpenDssLibraryMappings(
                cables=(LibraryNameMapping("1", "REMOVIDO"),),
            ),
            cable_names=("CABO A",),
            arrangement_map_issue="mapa_arranjos.json não é um JSON válido",
        )
        self.addCleanup(dialog.close)
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.assertFalse(ok.isEnabled())
        self.assertIn("não existe", dialog.cable_map_editor.issue_label.text())
        self.assertIn("Arquivo inválido", dialog.arrangement_map_editor.issue_label.text())

        dialog.tabs.setCurrentIndex(1)
        dialog.restore_defaults()
        self.assertFalse(ok.isEnabled())
        dialog.tabs.setCurrentIndex(2)
        dialog.restore_defaults()
        self.assertTrue(ok.isEnabled())

    def test_restore_defaults_is_contextual(self) -> None:
        dialog = OpenDssSettingsDialog(
            OpenDssLoadSettings(True, 0.8, 1.2),
            mappings=OpenDssLibraryMappings(
                cables=(LibraryNameMapping("1", "CABO A"),),
                arrangements=(LibraryNameMapping("2", "ARRANJO A"),),
            ),
            cable_names=("CABO A",),
            arrangement_names=("ARRANJO A",),
        )
        self.addCleanup(dialog.close)

        dialog.tabs.setCurrentIndex(1)
        dialog.restore_defaults()
        self.assertEqual(dialog.cable_map_editor.table.rowCount(), 0)
        self.assertEqual(dialog.arrangement_map_editor.table.rowCount(), 1)
        self.assertEqual(dialog.settings(), OpenDssLoadSettings(True, 0.8, 1.2))

        dialog.tabs.setCurrentIndex(0)
        dialog.restore_defaults()
        self.assertEqual(dialog.settings(), DEFAULT_OPENDSS_LOAD_SETTINGS)

    def test_each_map_has_one_explicit_save_button_with_dirty_feedback(self) -> None:
        dialog = OpenDssSettingsDialog(
            cable_names=("CABO A",),
            arrangement_names=("ARRANJO A",),
        )
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.cable_map_editor.save_button.text(), "Salvar")
        self.assertEqual(dialog.arrangement_map_editor.save_button.text(), "Salvar")
        self.assertFalse(dialog.cable_map_editor.save_button.isEnabled())
        self.assertFalse(dialog.arrangement_map_editor.save_button.isEnabled())

        editor = dialog.cable_map_editor
        editor.add_empty_row()
        self.assertFalse(editor.save_button.isEnabled())
        editor.table.item(0, 0).setText("115")
        combo = editor.table.cellWidget(0, 1)
        combo.setCurrentIndex(combo.findData("CABO A"))
        self.assertTrue(editor.save_button.isEnabled())
        self.assertEqual(editor.issue_label.text(), "Alterações não salvas.")

        requested = []
        editor.saveRequested.connect(requested.append)
        editor.save_button.click()
        self.assertEqual(
            requested,
            [(LibraryNameMapping("115", "CABO A"),)],
        )
        editor.mark_saved(requested[0])
        self.assertFalse(editor.save_button.isEnabled())
        self.assertEqual(editor.issue_label.text(), "Mapa salvo.")

    def test_save_failure_keeps_valid_changes_pending(self) -> None:
        dialog = OpenDssSettingsDialog(cable_names=("CABO A",))
        self.addCleanup(dialog.close)
        editor = dialog.cable_map_editor
        editor.add_empty_row()
        editor.table.item(0, 0).setText("115")
        combo = editor.table.cellWidget(0, 1)
        combo.setCurrentIndex(combo.findData("CABO A"))

        editor.mark_save_failed("disco indisponível")

        self.assertTrue(editor.save_button.isEnabled())
        self.assertEqual(
            editor.issue_label.text(),
            "Falha ao salvar: disco indisponível",
        )

    def test_clearing_a_corrupt_map_becomes_a_valid_pending_repair(self) -> None:
        dialog = OpenDssSettingsDialog(
            cable_map_issue="JSON inválido",
            cable_names=("CABO A",),
        )
        self.addCleanup(dialog.close)
        editor = dialog.cable_map_editor
        self.assertFalse(editor.save_button.isEnabled())

        editor.clear_map()

        self.assertTrue(editor.save_button.isEnabled())
        self.assertEqual(editor.issue_label.text(), "Alterações não salvas.")


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class MainWindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, settings: QSettings | None = None, **paths) -> MainWindow:  # noqa: ANN003
        window = MainWindow(settings=settings or isolated_settings("window"), **paths)
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

    def test_accepting_maps_persists_them_without_invalidating_power_flow(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            window = self._window(
                cable_map_path=root / "mapa_cabos.json",
                arrangement_map_path=root / "mapa_arranjos.json",
                library_cables_path=root / "cabos.json",
                library_geometries_path=root / "geometrias.json",
            )
            marker = object()
            window._power_flow_result = marker

            def accept(dialog) -> int:  # noqa: ANN001
                editor = dialog.cable_map_editor
                editor.add_empty_row()
                editor.table.item(0, 0).setText(" 115 ")
                combo = editor.table.cellWidget(0, 1)
                combo.setCurrentIndex(1)
                return int(QDialog.DialogCode.Accepted)

            with patch.object(OpenDssSettingsDialog, "exec", accept):
                window._show_opendss_settings()

            self.assertIs(window._power_flow_result, marker)
            self.assertEqual(read_cable_map(root / "mapa_cabos.json")[0].source_id, "115")
            self.assertFalse((root / "mapa_arranjos.json").exists())
            self.assertIn("Mapas OpenDSS", window.statusBar().currentMessage())

    def test_cancel_discards_changes_in_all_three_tabs_and_writes_nothing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            storage = isolated_settings("cancel-all-tabs")
            self.addCleanup(storage.clear)
            window = self._window(
                storage,
                cable_map_path=root / "mapa_cabos.json",
                arrangement_map_path=root / "mapa_arranjos.json",
            )
            before = window._opendss_load_settings

            def reject(dialog) -> int:  # noqa: ANN001
                dialog.apply_limits_check.setChecked(True)
                dialog.vminpu_input.setValue(0.8)
                dialog.cable_map_editor.add_empty_row()
                return int(QDialog.DialogCode.Rejected)

            with patch.object(OpenDssSettingsDialog, "exec", reject):
                window._show_opendss_settings()

            self.assertEqual(window._opendss_load_settings, before)
            self.assertFalse((root / "mapa_cabos.json").exists())
            self.assertFalse((root / "mapa_arranjos.json").exists())

    def test_explicit_save_persists_only_active_map_and_cancel_discards_the_rest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            storage = isolated_settings("explicit-save-cancel")
            self.addCleanup(storage.clear)
            window = self._window(
                storage,
                cable_map_path=root / "mapa_cabos.json",
                arrangement_map_path=root / "mapa_arranjos.json",
                library_cables_path=root / "cabos.json",
                library_geometries_path=root / "geometrias.json",
            )
            marker = object()
            window._power_flow_result = marker
            captured = {}

            def save_cables_then_reject(dialog) -> int:  # noqa: ANN001
                dialog.apply_limits_check.setChecked(True)
                dialog.vminpu_input.setValue(0.8)

                cable_editor = dialog.cable_map_editor
                cable_editor.add_empty_row()
                cable_editor.table.item(0, 0).setText("115")
                cable_combo = cable_editor.table.cellWidget(0, 1)
                cable_combo.setCurrentIndex(1)

                arrangement_editor = dialog.arrangement_map_editor
                arrangement_editor.add_empty_row()
                arrangement_editor.table.item(0, 0).setText("1")
                arrangement_combo = arrangement_editor.table.cellWidget(0, 1)
                arrangement_combo.setCurrentIndex(1)

                cable_editor.save_button.click()
                captured["cable_status"] = cable_editor.issue_label.text()
                captured["cable_enabled"] = cable_editor.save_button.isEnabled()
                captured["arrangement_pending"] = (
                    arrangement_editor.save_button.isEnabled()
                )
                return int(QDialog.DialogCode.Rejected)

            with patch.object(
                OpenDssSettingsDialog,
                "exec",
                save_cables_then_reject,
            ):
                window._show_opendss_settings()

            self.assertEqual(captured["cable_status"], "Mapa salvo.")
            self.assertFalse(captured["cable_enabled"])
            self.assertTrue(captured["arrangement_pending"])
            self.assertEqual(read_cable_map(root / "mapa_cabos.json")[0].source_id, "115")
            self.assertFalse((root / "mapa_arranjos.json").exists())
            self.assertEqual(load_opendss_settings(storage), DEFAULT_OPENDSS_LOAD_SETTINGS)
            self.assertIs(window._power_flow_result, marker)

    def test_explicit_save_failure_keeps_changes_pending_and_old_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            window = self._window(
                cable_map_path=root / "mapa_cabos.json",
                arrangement_map_path=root / "mapa_arranjos.json",
                library_cables_path=root / "cabos.json",
                library_geometries_path=root / "geometrias.json",
            )
            original = LibraryNameMapping(
                "115",
                window.opendss_library_session.saved_cable_names[0],
            )
            window.opendss_mapping_session.save_cable_map((original,))
            previous = (root / "mapa_cabos.json").read_bytes()
            captured = {}

            def fail_then_reject(dialog) -> int:  # noqa: ANN001
                editor = dialog.cable_map_editor
                editor.table.item(0, 0).setText("116")
                editor.save_button.click()
                captured["status"] = editor.issue_label.text()
                captured["enabled"] = editor.save_button.isEnabled()
                return int(QDialog.DialogCode.Rejected)

            with (
                patch.object(
                    window.opendss_mapping_session,
                    "save_cable_map",
                    side_effect=OSError("disco indisponível"),
                ),
                patch("circuit_viewer.main_window.QMessageBox.warning") as warning,
                patch.object(OpenDssSettingsDialog, "exec", fail_then_reject),
            ):
                window._show_opendss_settings()

            self.assertTrue(captured["enabled"])
            self.assertEqual(
                captured["status"],
                "Falha ao salvar: disco indisponível",
            )
            self.assertTrue(warning.called)
            self.assertEqual((root / "mapa_cabos.json").read_bytes(), previous)
            self.assertEqual(window.opendss_mapping_session.mappings.cables, (original,))


if __name__ == "__main__":
    unittest.main()
