from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from circuit_viewer.opendss_library import (
    ArrangementDefinition,
    CableDefinition,
    ConductorPosition,
)
from circuit_viewer.opendss_library_session import OpenDssLibrarySession
from circuit_viewer.opendss_library_store import save_cables, save_geometries
from circuit_viewer.opendss_mapping_session import (
    MappedLibraryItemError,
    OpenDssMappingSession,
)
from circuit_viewer.opendss_mapping_store import (
    LibraryNameMapping,
    OpenDssLibraryMappings,
    OpenDssMappingFormatError,
    default_arrangement_map_path,
    default_cable_map_path,
    load_arrangement_map,
    load_cable_map,
    read_arrangement_map,
    read_cable_map,
    save_arrangement_map,
    save_cable_map,
    write_json_files_atomically,
)


class MappingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cable_path = self.root / "mapa_cabos.json"
        self.arrangement_path = self.root / "mapa_arranjos.json"

    def test_default_paths_live_in_persistent_data_directory(self) -> None:
        self.assertEqual(default_cable_map_path().parent.name, "dados")
        self.assertEqual(default_cable_map_path().name, "mapa_cabos.json")
        self.assertEqual(default_arrangement_map_path().parent.name, "dados")
        self.assertEqual(default_arrangement_map_path().name, "mapa_arranjos.json")

    def test_missing_files_are_valid_empty_maps(self) -> None:
        self.assertEqual(load_cable_map(self.cable_path).entries, ())
        self.assertIsNone(load_cable_map(self.cable_path).issue)
        self.assertEqual(load_arrangement_map(self.arrangement_path).entries, ())

    def test_round_trip_uses_versioned_public_schema_and_uppercase_names(self) -> None:
        cables = (
            LibraryNameMapping(" 115 ", " acsr 556 "),
            LibraryNameMapping("9", "cabo cn"),
        )
        arrangements = (LibraryNameMapping("1", "cruzeta 3f"),)
        save_cable_map(cables, self.cable_path)
        save_arrangement_map(arrangements, self.arrangement_path)

        self.assertEqual(
            read_cable_map(self.cable_path),
            (
                LibraryNameMapping("115", "ACSR 556"),
                LibraryNameMapping("9", "CABO CN"),
            ),
        )
        self.assertEqual(read_arrangement_map(self.arrangement_path), arrangements)
        payload = json.loads(self.cable_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["versao"], 1)
        self.assertEqual(
            payload["mapa_cabos"][0],
            {"CABO_ID": "115", "NOME": "ACSR 556"},
        )

    def test_duplicate_ids_and_invalid_structure_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicado"):
            OpenDssLibraryMappings(
                cables=(
                    LibraryNameMapping("1", "A"),
                    LibraryNameMapping("1", "B"),
                )
            )
        self.cable_path.write_text(
            json.dumps(
                {
                    "versao": 1,
                    "mapa_cabos": [
                        {"CABO_ID": "1", "NOME": "A"},
                        {"CABO_ID": "1", "NOME": "B"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(OpenDssMappingFormatError):
            read_cable_map(self.cable_path)

    def test_corruption_is_reported_independently(self) -> None:
        self.cable_path.write_text("{", encoding="utf-8")
        save_arrangement_map(
            (LibraryNameMapping("7", "ARRANJO"),), self.arrangement_path
        )
        cable_result = load_cable_map(self.cable_path)
        arrangement_result = load_arrangement_map(self.arrangement_path)
        self.assertTrue(cable_result.issue)
        self.assertEqual(cable_result.entries, ())
        self.assertIsNone(arrangement_result.issue)
        self.assertEqual(len(arrangement_result.entries), 1)

    def test_multi_file_write_restores_previous_files_after_replace_failure(self) -> None:
        self.cable_path.write_text("cabo-antigo", encoding="utf-8")
        self.arrangement_path.write_text("arranjo-antigo", encoding="utf-8")
        real_replace = os.replace
        failed = False

        def fail_second(source, target):  # noqa: ANN001, ANN202
            nonlocal failed
            if Path(target) == self.arrangement_path and not failed:
                failed = True
                raise OSError("falha simulada")
            return real_replace(source, target)

        with patch("circuit_viewer.opendss_mapping_store.os.replace", fail_second):
            with self.assertRaises(OSError):
                write_json_files_atomically(
                    {
                        self.cable_path: {"novo": "cabo"},
                        self.arrangement_path: {"novo": "arranjo"},
                    }
                )
        self.assertEqual(self.cable_path.read_text(encoding="utf-8"), "cabo-antigo")
        self.assertEqual(
            self.arrangement_path.read_text(encoding="utf-8"), "arranjo-antigo"
        )


class MappingSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cable_map_path = self.root / "mapa_cabos.json"
        self.arrangement_map_path = self.root / "mapa_arranjos.json"
        self.library_path = self.root / "cabos.json"
        save_cable_map(
            (LibraryNameMapping("115", "CABO ANTIGO"),), self.cable_map_path
        )
        self.session = OpenDssMappingSession(
            cable_map_path=self.cable_map_path,
            arrangement_map_path=self.arrangement_map_path,
        )

    def test_library_rename_migrates_map_by_stable_id(self) -> None:
        old = (CableDefinition("stable", "Cabo antigo"),)
        new = (CableDefinition("stable", "Cabo novo"),)
        self.session.save_cable_library(old, new, self.library_path)
        self.assertEqual(
            read_cable_map(self.cable_map_path),
            (LibraryNameMapping("115", "CABO NOVO"),),
        )
        self.assertEqual(self.session.mappings.cables[0].library_name, "CABO NOVO")

    def test_name_swap_still_follows_the_stable_item_id(self) -> None:
        old = (
            CableDefinition("stable-a", "Cabo antigo"),
            CableDefinition("stable-b", "Outro cabo"),
        )
        new = (
            CableDefinition("stable-a", "Outro cabo"),
            CableDefinition("stable-b", "Cabo antigo"),
        )

        self.session.save_cable_library(old, new, self.library_path)

        self.assertEqual(
            read_cable_map(self.cable_map_path),
            (LibraryNameMapping("115", "OUTRO CABO"),),
        )

    def test_import_with_a_new_internal_id_keeps_an_existing_mapped_name(self) -> None:
        old = (CableDefinition("old-id", "Cabo antigo"),)
        imported = (CableDefinition("new-id", "Cabo antigo"),)

        self.session.save_cable_library(old, imported, self.library_path)

        self.assertEqual(
            read_cable_map(self.cable_map_path),
            (LibraryNameMapping("115", "CABO ANTIGO"),),
        )

    def test_removing_mapped_library_item_is_blocked_without_writes(self) -> None:
        old = (CableDefinition("stable", "Cabo antigo"),)
        with self.assertRaises(MappedLibraryItemError) as raised:
            self.session.save_cable_library(old, (), self.library_path)
        self.assertIn("115", str(raised.exception))
        self.assertFalse(self.library_path.exists())
        self.assertEqual(
            read_cable_map(self.cable_map_path),
            (LibraryNameMapping("115", "CABO ANTIGO"),),
        )

    def test_repairing_corrupted_map_writes_a_valid_empty_file(self) -> None:
        self.cable_map_path.write_text("{", encoding="utf-8")
        session = OpenDssMappingSession(
            cable_map_path=self.cable_map_path,
            arrangement_map_path=self.arrangement_map_path,
        )
        self.assertTrue(session.cable_issue)
        self.assertTrue(session.save_cable_map(()))
        self.assertEqual(read_cable_map(self.cable_map_path), ())
        self.assertIsNone(session.cable_issue)

    def test_saving_one_group_does_not_write_the_other_map(self) -> None:
        save_arrangement_map(
            (LibraryNameMapping("1", "ARRANJO A"),),
            self.arrangement_map_path,
        )
        session = OpenDssMappingSession(
            cable_map_path=self.cable_map_path,
            arrangement_map_path=self.arrangement_map_path,
        )
        arrangement_before = self.arrangement_map_path.read_bytes()

        self.assertTrue(
            session.save_cable_map(
                (LibraryNameMapping("116", "CABO NOVO"),)
            )
        )

        self.assertEqual(
            read_cable_map(self.cable_map_path),
            (LibraryNameMapping("116", "CABO NOVO"),),
        )
        self.assertEqual(self.arrangement_map_path.read_bytes(), arrangement_before)
        self.assertEqual(
            session.mappings.arrangements,
            (LibraryNameMapping("1", "ARRANJO A"),),
        )

    def test_saving_cables_leaves_a_corrupt_arrangement_map_untouched(self) -> None:
        self.arrangement_map_path.write_text("{", encoding="utf-8")
        session = OpenDssMappingSession(
            cable_map_path=self.cable_map_path,
            arrangement_map_path=self.arrangement_map_path,
        )
        self.assertTrue(session.arrangement_issue)

        session.save_cable_map((LibraryNameMapping("116", "CABO NOVO"),))

        self.assertEqual(self.arrangement_map_path.read_text(encoding="utf-8"), "{")
        self.assertTrue(session.arrangement_issue)

    def test_single_group_failure_preserves_file_and_pending_session_state(self) -> None:
        previous = self.cable_map_path.read_bytes()
        previous_mappings = self.session.mappings

        with patch(
            "circuit_viewer.opendss_mapping_store.os.replace",
            side_effect=OSError("falha simulada"),
        ):
            with self.assertRaises(OSError):
                self.session.save_cable_map(
                    (LibraryNameMapping("116", "CABO NOVO"),)
                )

        self.assertEqual(self.cable_map_path.read_bytes(), previous)
        self.assertEqual(self.session.mappings, previous_mappings)

    def test_import_and_restore_cannot_remove_a_mapped_cable(self) -> None:
        custom = CableDefinition("custom", "Cabo antigo")
        save_cables((custom,), self.library_path)
        session = OpenDssLibrarySession(
            cables_path=self.library_path,
            geometries_path=self.root / "geometrias.json",
            mapping_session=self.session,
        )
        replacement_path = self.root / "substituto.json"
        save_cables((CableDefinition("other", "Outro"),), replacement_path)

        with self.assertRaises(MappedLibraryItemError):
            session.replace_cables_from_file(replacement_path)
        with self.assertRaises(MappedLibraryItemError):
            session.restore_default_cables()

        self.assertEqual([item.cable_id for item in session.catalog.cables], ["custom"])

    def test_import_and_restore_cannot_remove_a_mapped_arrangement(self) -> None:
        arrangement = ArrangementDefinition(
            "custom",
            "Arranjo antigo",
            1,
            "m",
            [ConductorPosition(0.0, 8.0)],
        )
        geometry_path = self.root / "geometrias.json"
        save_geometries((arrangement,), (), geometry_path)
        save_arrangement_map(
            (LibraryNameMapping("1", arrangement.name),),
            self.arrangement_map_path,
        )
        mapping_session = OpenDssMappingSession(
            cable_map_path=self.cable_map_path,
            arrangement_map_path=self.arrangement_map_path,
        )
        session = OpenDssLibrarySession(
            cables_path=self.library_path,
            geometries_path=geometry_path,
            mapping_session=mapping_session,
        )
        replacement_path = self.root / "geometrias_substitutas.json"
        replacement = ArrangementDefinition(
            "other",
            "Outro arranjo",
            1,
            "m",
            [ConductorPosition(0.0, 9.0)],
        )
        save_geometries((replacement,), (), replacement_path)

        with self.assertRaises(MappedLibraryItemError):
            session.replace_geometries_from_file(replacement_path)
        with self.assertRaises(MappedLibraryItemError):
            session.restore_default_geometries()

        self.assertEqual(
            [item.arrangement_id for item in session.catalog.arrangements],
            ["custom"],
        )


if __name__ == "__main__":
    unittest.main()
