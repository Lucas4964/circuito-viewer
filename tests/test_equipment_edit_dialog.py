from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QDialogButtonBox

    from circuit_viewer.equipment_edit_dialog import (
        EquipmentEditDialog,
        capacitor_form,
        generator_form,
        load_form,
        segment_form,
    )
    from circuit_viewer.model import LoadPatternRecord

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover - depende do ambiente
    PYQT_AVAILABLE = False


SEGMENT = {
    "segment_ids": "T1", "codes": "15027320", "phases": "13",
    "phase_cable_ids": "398", "neutral_cable_ids": "", "arrangement_ids": "1",
    "lengths": 13.88, "start_indices": "17659_AUX", "end_indices": "17660_AUX",
}
REGULATOR = {
    "codes": "58137022ME", "snom_values": "276", "vnom_values": "0", "inom_values": "200",
    "regulation_ranges": "0.1", "step_counts": "32", "fixed_values": "0", "tap_values": "0",
    "vreg_p1_values": "1.02", "vreg_p2_values": "1.02", "vreg_p3_values": "1.02",
    "vreg_p4_values": "1.02",
}
CABLES = (("398", "CBO CAA NU 266.8 MCM (398)"), ("115", "CBO CA 2 AWG (115)"))
PHASES = (("13", "13 — DEF"), ("1", "1 — D"))


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class EquipmentEditDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, form):  # noqa: ANN001, ANN201
        dialog = EquipmentEditDialog(form)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def save_enabled(self, dialog) -> bool:  # noqa: ANN001
        return dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()

    def test_unchanged_dialog_returns_nothing(self) -> None:
        dialog = self.dialog(segment_form("S", SEGMENT, regulator_id="R", regulator=REGULATOR,
                                          phase_choices=PHASES, cable_choices=CABLES))

        self.assertEqual(dialog.changes(), {})
        self.assertTrue(self.save_enabled(dialog))

    def test_segment_with_regulator_has_four_tabs_and_returns_only_changes(self) -> None:
        dialog = self.dialog(segment_form("S", SEGMENT, regulator_id="R", regulator=REGULATOR,
                                          phase_choices=PHASES, cable_choices=CABLES))
        self.assertEqual(
            [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())],
            ["Trecho", "Regulador", "Tap", "Tensão"],
        )

        dialog.findChild(type(dialog._widgets["S:lengths:"]), "edit_lengths").setText("20,5")
        cable = dialog._widgets["S:phase_cable_ids:"]
        cable.setCurrentIndex(cable.findData("115"))
        dialog._widgets["R:vreg_p1_values:"].setText("1.03")

        self.assertEqual(
            dialog.changes(),
            {"S": {"lengths": 20.5, "phase_cable_ids": "115"}, "R": {"vreg_p1_values": "1.03"}},
        )

    def test_invalid_number_blocks_saving(self) -> None:
        dialog = self.dialog(segment_form("S", SEGMENT, regulator_id="R", regulator=REGULATOR))

        dialog._widgets["R:snom_values:"].setText("muito")

        self.assertFalse(self.save_enabled(dialog))
        self.assertIn("SNOM", dialog.status_label.text())
        dialog._widgets["R:snom_values:"].setText("300")
        self.assertTrue(self.save_enabled(dialog))

    def test_fixed_tap_is_enabled_only_with_fixed_control(self) -> None:
        dialog = self.dialog(segment_form("S", SEGMENT, regulator_id="R", regulator=REGULATOR))
        control = dialog._widgets["R:fixed_values:"]
        tap = dialog._widgets["R:tap_values:"]

        self.assertFalse(tap.isEnabled())
        control.setCurrentIndex(control.findData("1"))
        self.assertTrue(tap.isEnabled())
        tap.setText("0.05")
        self.assertEqual(dialog.changes(), {"R": {"fixed_values": "1", "tap_values": "0.05"}})

    def test_segment_without_project_is_read_only_and_limits_the_regulator(self) -> None:
        dialog = self.dialog(segment_form(
            "S", SEGMENT, regulator_id="R", regulator=REGULATOR, segment_editable=False,
            regulator_columns=frozenset({"snom_values", "vnom_values"}),
        ))

        self.assertFalse(dialog._widgets["S:codes:"].isEnabled())
        self.assertFalse(dialog._widgets["R:inom_values:"].isEnabled())
        self.assertTrue(dialog._widgets["R:snom_values:"].isEnabled())

    def test_load_patterns_tab_edits_the_records(self) -> None:
        records = tuple(
            LoadPatternRecord("L1", npat, "1", "0", "0", "0.5", "0", "0") for npat in range(4)
        )
        dialog = self.dialog(load_form("L", {"load_ids": "L1", "codes": "C", "phases": "1"},
                                       patterns_id="P", patterns=records, phase_choices=PHASES))
        self.assertEqual(
            [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())], ["Dados", "Patamares"]
        )
        table = dialog._pattern_tables[0][1]

        table.item(2, 0).setText("3,5")
        changes = dialog.changes()

        self.assertEqual(list(changes), ["P"])
        self.assertEqual(changes["P"]["records"][2].pd, "3,5")
        self.assertIs(changes["P"]["records"][0], records[0])
        table.item(1, 3).setText("abc")
        self.assertFalse(self.save_enabled(dialog))

    def test_generator_level_powers_rebuild_the_tuple(self) -> None:
        dialog = self.dialog(generator_form(
            "G", {"generator_ids": "G1", "level_active_powers": ("", "", "", ""),
                  "level_reactive_powers": ("", "", "", "")},
        ))

        dialog._widgets["G:level_active_powers:2"].setText("150")

        self.assertEqual(dialog.changes(), {"G": {"level_active_powers": ("", "", "150", "")}})

    def test_capacitor_form_has_one_tab_without_tab_bar(self) -> None:
        dialog = self.dialog(capacitor_form("C", {"capacitor_ids": "C1", "q1_values": "100"}))

        self.assertEqual(dialog.tabs.count(), 1)
        self.assertTrue(dialog.tabs.tabBar().isHidden())
        dialog._widgets["C:q1_values:"].setText("200")
        self.assertEqual(dialog.changes(), {"C": {"q1_values": "200"}})


if __name__ == "__main__":
    unittest.main()
