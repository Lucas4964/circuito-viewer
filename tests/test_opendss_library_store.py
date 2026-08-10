from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from circuit_viewer.opendss_library import CableDefinition, LibraryFormatError
from circuit_viewer.opendss_library_store import (
    default_cables_path,
    default_geometries_path,
    load_cables,
    load_geometries,
    packaged_cables_defaults_path,
    packaged_geometries_defaults_path,
    read_cables_file,
    read_geometries_file,
    save_cables,
    save_geometries,
)


class LibraryDefaultsTests(unittest.TestCase):
    def test_packaged_defaults_preserve_reference_counts_and_values(self) -> None:
        cables = read_cables_file(packaged_cables_defaults_path())
        arrangements, geometries = read_geometries_file(packaged_geometries_defaults_path())

        self.assertEqual((len(cables), len(arrangements), len(geometries)), (58, 9, 12))
        aa_1000 = next(item for item in cables if item.cable_id == "aa_1000")
        self.assertEqual((aa_1000.rdc, aa_1000.resistance_units, aa_1000.gmr_units), (0.019886364, "kft", "in"))
        cn = next(item for item in cables if item.cable_id == "cn_250_1_3")
        self.assertTrue(cn.is_concentric)
        self.assertEqual(cn.strand_count, 13)
        underground = next(item for item in arrangements if item.arrangement_id == "trifolio_ug_ft")
        self.assertTrue(all(position.height == -4 for position in underground.positions))
        self.assertEqual(geometries[-1].cable_ids, ["cn_250_1_3"] * 3)

    def test_default_user_paths_live_in_ignored_data_directory(self) -> None:
        self.assertEqual(default_cables_path().parent.name, "dados")
        self.assertEqual(default_cables_path().name, "cabos.json")
        self.assertEqual(default_geometries_path().parent.name, "dados")
        self.assertEqual(default_geometries_path().name, "geometrias.json")


class LibraryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cables_path = self.root / "cabos.json"
        self.geometries_path = self.root / "geometrias.json"

    def test_missing_user_files_fall_back_to_defaults_independently(self) -> None:
        cables = load_cables(self.cables_path)
        geometries = load_geometries(self.geometries_path)

        self.assertTrue(cables.used_defaults)
        self.assertTrue(geometries.used_defaults)
        self.assertIsNone(cables.issue)
        self.assertIsNone(geometries.issue)
        self.assertEqual(len(cables.cables), 58)
        self.assertEqual(len(geometries.geometries), 12)

    def test_corrupted_cables_do_not_discard_valid_geometries(self) -> None:
        self.cables_path.write_text("{", encoding="utf-8")
        defaults_arrangements, defaults_geometries = read_geometries_file(packaged_geometries_defaults_path())
        save_geometries(defaults_arrangements[:1], defaults_geometries[:1], self.geometries_path)

        cables = load_cables(self.cables_path)
        geometries = load_geometries(self.geometries_path)

        self.assertTrue(cables.used_defaults)
        self.assertIn("padrões", cables.issue)
        self.assertFalse(geometries.used_defaults)
        self.assertEqual(len(geometries.arrangements), 1)

    def test_round_trip_preserves_compatible_portuguese_schema(self) -> None:
        cables = read_cables_file(packaged_cables_defaults_path())[:2]
        arrangements, geometries = read_geometries_file(packaged_geometries_defaults_path())
        save_cables(cables, self.cables_path)
        save_geometries(arrangements[:1], geometries[:1], self.geometries_path)

        cable_payload = json.loads(self.cables_path.read_text(encoding="utf-8"))
        geometry_payload = json.loads(self.geometries_path.read_text(encoding="utf-8"))
        self.assertEqual(cable_payload["versao"], 1)
        self.assertEqual(set(cable_payload), {"versao", "cabos"})
        self.assertIn("gmrac", cable_payload["cabos"][0])
        self.assertEqual(set(geometry_payload), {"versao", "arranjos", "montagens"})
        self.assertIn("arranjoId", geometry_payload["montagens"][0])
        self.assertEqual(read_cables_file(self.cables_path), cables)

    def test_incomplete_but_structural_cable_is_accepted(self) -> None:
        self.cables_path.write_text(
            json.dumps({"versao": 1, "cabos": [{"id": "x", "nome": "Incompleto", "tipo": "wire"}]}),
            encoding="utf-8",
        )
        cables = read_cables_file(self.cables_path)
        self.assertEqual(cables, (CableDefinition("x", "Incompleto", resistance_units="", gmr_units="", radius_units=""),))

    def test_duplicate_ids_or_names_cancel_the_whole_import(self) -> None:
        entries = [
            {"id": "same", "nome": "Primeiro", "tipo": "wire"},
            {"id": "same", "nome": "Segundo", "tipo": "wire"},
        ]
        self.cables_path.write_text(json.dumps({"cabos": entries}), encoding="utf-8")
        with self.assertRaisesRegex(LibraryFormatError, "ID duplicado"):
            read_cables_file(self.cables_path)

        entries[1]["id"] = "other"
        entries[1]["nome"] = "PRIMEIRO"
        self.cables_path.write_text(json.dumps({"cabos": entries}), encoding="utf-8")
        with self.assertRaisesRegex(LibraryFormatError, "Nome duplicado"):
            read_cables_file(self.cables_path)

    def test_invalid_geometry_structure_is_rejected_without_normalizing_silently(self) -> None:
        payload = {
            "arranjos": [
                {
                    "id": "a",
                    "nome": "A",
                    "nconds": 2,
                    "nphases": 1,
                    "unidades": "m",
                    "pos": [{"x": 0, "h": 1}],
                }
            ],
            "montagens": [],
        }
        self.geometries_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(LibraryFormatError, "nconds"):
            read_geometries_file(self.geometries_path)

    def test_atomic_save_leaves_no_temporary_file(self) -> None:
        save_cables([], self.cables_path)
        self.assertTrue(self.cables_path.exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])
