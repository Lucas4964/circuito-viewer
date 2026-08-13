from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication, QLabel

    from circuit_viewer.allocation_measurements import (
        parse_allocation_measurement_rows,
    )
    from circuit_viewer.calculation_levels import default_calculation_levels
    from circuit_viewer.circuit_calculation_levels import (
        CircuitCalculationLevelsController,
        CircuitCalculationLevelsModel,
    )
    from circuit_viewer.curvas import Curve
    from circuit_viewer.model import (
        CircuitCatalogModel,
        CircuitDefinition,
        CircuitModel,
        LineNetworkModel,
        UtmCrs,
    )
    from circuit_viewer.opendss_allocation_dialog import (
        OpenDssAllocationDialog,
        load_opendss_allocation_settings,
        save_opendss_allocation_settings,
    )
    from circuit_viewer.opendss_allocation_settings import OpenDssAllocationSettings

    PYQT_AVAILABLE = True
except ModuleNotFoundError:
    PYQT_AVAILABLE = False


def make_catalog():  # noqa: ANN201
    bars = CircuitModel(
        ["B0", "B1"],
        ["ROOT", "END"],
        [500_000.0, 500_100.0],
        [8_000_000.0, 8_000_000.0],
        UtmCrs(21, northern=False),
    )
    lines = LineNetworkModel(
        bars,
        ["S1"],
        ["L1"],
        ["13"],
        [0],
        [1],
        [""],
        ["CB1"],
        [""],
        [100.0],
    )
    return CircuitCatalogModel.build(
        lines,
        None,
        [CircuitDefinition("C1", "B0", "ALIM", "13,8")],
    )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class OpenDssAllocationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_dialog_offers_measured_circuit_curve_and_own_schedule(self):
        catalog = make_catalog()
        measurements = parse_allocation_measurement_rows(
            ("CODIGO", "NPAT", "ID", "IE", "IF"),
            [("ALIM", str(npat), "1", "2", "3") for npat in range(4)],
            catalog,
            source_label="m.csv",
            encoding="utf-8",
        ).model
        schedule = default_calculation_levels()
        controller = CircuitCalculationLevelsController(
            CircuitCalculationLevelsModel(catalog, (schedule,))
        )
        curve = Curve("CURVE", "GD", (1.0,) * 24)
        dialog = OpenDssAllocationDialog(
            catalog,
            measurements,
            (curve,),
            schedule,
            controller,
            OpenDssAllocationSettings(),
        )
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.circuit_combo.count(), 1)
        self.assertEqual(dialog.curve_combo.count(), 1)
        self.assertEqual(dialog.schedule_combo.count(), 2)
        dialog.schedule_combo.setCurrentIndex(1)
        dialog.kwh_days.setValue(45)
        dialog.cfactor.setValue(3.5)
        dialog.pf.setValue(0.95)
        dialog.iterations.setValue(5)

        self.assertEqual(dialog.selected_circuit_index(), 0)
        self.assertIs(dialog.selected_curve(), curve)
        self.assertIs(dialog.selected_schedule(), schedule)
        self.assertEqual(
            dialog.selected_settings(),
            OpenDssAllocationSettings(45, 3.5, 0.95, 5),
        )
        warning = dialog.findChild(
            QLabel, "opendss_allocation_reverse_flow_warning"
        )
        self.assertIsNotNone(warning)
        self.assertIn("PeakCurrent", warning.text())

    def test_settings_are_persisted_and_reloaded(self):
        settings = QSettings(
            str(self.root / "settings.ini"),
            QSettings.Format.IniFormat,
        )
        expected = OpenDssAllocationSettings(31.5, 2.25, 0.91, 7)

        save_opendss_allocation_settings(settings, expected)

        self.assertEqual(load_opendss_allocation_settings(settings), expected)


if __name__ == "__main__":
    unittest.main()
