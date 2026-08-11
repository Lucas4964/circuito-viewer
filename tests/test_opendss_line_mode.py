from __future__ import annotations

import unittest

import circuit_viewer
from circuit_viewer.opendss_line_mode import (
    DEFAULT_OPENDSS_LINE_PARAMETER_MODE,
    OpenDssLineParameterMode,
    parse_opendss_line_parameter_mode,
)


class OpenDssLineParameterModeTests(unittest.TestCase):
    def test_package_facade_exposes_the_line_library_api(self) -> None:
        names = (
            "DEFAULT_OPENDSS_LINE_PARAMETER_MODE",
            "OpenDssLineParameterMode",
            "parse_opendss_line_parameter_mode",
            "OpenDssLibraryExportError",
            "OpenDssLibraryExportResult",
            "CABOS_FILENAME",
            "ARRANGEMENTS_FILENAME",
            "LINE_GEOMETRIES_FILENAME",
        )

        for name in names:
            with self.subTest(name=name):
                self.assertIn(name, circuit_viewer.__all__)
                self.assertTrue(hasattr(circuit_viewer, name))
        self.assertIs(
            circuit_viewer.OpenDssLineParameterMode,
            OpenDssLineParameterMode,
        )
        self.assertIs(
            circuit_viewer.DEFAULT_OPENDSS_LINE_PARAMETER_MODE,
            DEFAULT_OPENDSS_LINE_PARAMETER_MODE,
        )
        self.assertIs(
            circuit_viewer.parse_opendss_line_parameter_mode,
            parse_opendss_line_parameter_mode,
        )
        self.assertTrue(
            issubclass(circuit_viewer.OpenDssLibraryExportError, ValueError)
        )
        self.assertEqual(circuit_viewer.CABOS_FILENAME, "cabos.dss")
        self.assertEqual(circuit_viewer.ARRANGEMENTS_FILENAME, "arranjos.dss")
        self.assertEqual(
            circuit_viewer.LINE_GEOMETRIES_FILENAME,
            "geometria_linhas.dss",
        )

    def test_values_are_stable_strings_and_original_is_the_default(self) -> None:
        self.assertIsInstance(OpenDssLineParameterMode.ORIGINAL, str)
        self.assertEqual(OpenDssLineParameterMode.ORIGINAL.value, "original")
        self.assertEqual(OpenDssLineParameterMode.LIBRARY.value, "library")
        self.assertIs(
            DEFAULT_OPENDSS_LINE_PARAMETER_MODE,
            OpenDssLineParameterMode.ORIGINAL,
        )

    def test_parser_accepts_enum_and_normalized_text(self) -> None:
        self.assertIs(
            parse_opendss_line_parameter_mode(OpenDssLineParameterMode.LIBRARY),
            OpenDssLineParameterMode.LIBRARY,
        )
        self.assertIs(
            parse_opendss_line_parameter_mode("  LiBrArY  "),
            OpenDssLineParameterMode.LIBRARY,
        )

    def test_parser_falls_back_for_missing_or_corrupt_values(self) -> None:
        for value in (None, "", "desconhecido", 1, True):
            with self.subTest(value=value):
                self.assertIs(
                    parse_opendss_line_parameter_mode(value),
                    DEFAULT_OPENDSS_LINE_PARAMETER_MODE,
                )


if __name__ == "__main__":
    unittest.main()
