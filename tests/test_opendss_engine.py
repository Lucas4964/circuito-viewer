from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from circuit_viewer import opendss_engine
from circuit_viewer.opendss_engine import (
    PowerFlowEngineError,
    acquire_engine,
    ascii_workspace,
    power_flow_available,
    power_flow_import_error,
)


class FakeEngine:
    """Motor mínimo: registra os comandos e finge ter iniciado."""

    started = True

    def __init__(self) -> None:
        self.commands: list[str] = []

    def text(self, command: str) -> str:
        self.commands.append(command)
        return ""


def reset_import_cache() -> None:
    opendss_engine._import_checked = False
    opendss_engine._import_error = None


class ImportDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._checked = opendss_engine._import_checked
        self._error = opendss_engine._import_error
        reset_import_cache()

    def tearDown(self) -> None:
        opendss_engine._import_checked = self._checked
        opendss_engine._import_error = self._error

    def test_missing_library_reports_how_to_install_it(self) -> None:
        # Um None em sys.modules faz o import levantar ImportError.
        with patch.dict(sys.modules, {"py_dss_interface": None}):
            message = power_flow_import_error()

        self.assertIsNotNone(message)
        self.assertIn("py-dss-interface", message)
        self.assertIn("pip install", message)

    def test_availability_follows_the_import(self) -> None:
        with patch.dict(sys.modules, {"py_dss_interface": None}):
            self.assertFalse(power_flow_available())

    def test_result_is_memoized(self) -> None:
        with patch.dict(sys.modules, {"py_dss_interface": None}):
            first = power_flow_import_error()
        # Fora do patch a biblioteca poderia estar disponível; o memo mantém a
        # primeira resposta, que é o que evita recarregar a DLL a cada
        # sincronização de menu.
        self.assertEqual(power_flow_import_error(), first)

    def test_engine_creation_fails_without_the_library(self) -> None:
        with patch.dict(sys.modules, {"py_dss_interface": None}):
            with self.assertRaises(PowerFlowEngineError):
                opendss_engine._create_engine()


class AcquireEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._engine = opendss_engine._engine
        opendss_engine._engine = None

    def tearDown(self) -> None:
        opendss_engine._engine = self._engine

    def test_clears_the_previous_circuit_on_entry(self) -> None:
        fake = FakeEngine()
        with patch.object(opendss_engine, "_create_engine", return_value=fake):
            with acquire_engine() as engine:
                self.assertIs(engine, fake)

        self.assertEqual(fake.commands, ["Clear"])

    def test_reuses_the_same_engine(self) -> None:
        fake = FakeEngine()
        with patch.object(
            opendss_engine,
            "_create_engine",
            return_value=fake,
        ) as create:
            with acquire_engine():
                pass
            with acquire_engine():
                pass

        # A DLL é global ao processo: um motor só, criado uma vez.
        self.assertEqual(create.call_count, 1)

    def test_restores_the_working_directory(self) -> None:
        fake = FakeEngine()
        before = os.getcwd()
        with patch.object(opendss_engine, "_create_engine", return_value=fake):
            with acquire_engine():
                # Compile faz exatamente isto dentro do OpenDSS.
                os.chdir(os.path.dirname(before) or before)

        self.assertEqual(os.getcwd(), before)

    def test_restores_the_working_directory_after_a_failure(self) -> None:
        fake = FakeEngine()
        before = os.getcwd()
        with patch.object(opendss_engine, "_create_engine", return_value=fake):
            with self.assertRaises(RuntimeError):
                with acquire_engine():
                    os.chdir(os.path.dirname(before) or before)
                    raise RuntimeError("falha no meio da execução")

        self.assertEqual(os.getcwd(), before)


class AsciiWorkspaceTests(unittest.TestCase):
    def test_creates_an_ascii_directory_and_removes_it(self) -> None:
        with ascii_workspace() as workspace:
            self.assertTrue(workspace.is_dir())
            str(workspace).encode("ascii")  # não pode levantar
            created = workspace
            (workspace / "arquivo.dss").write_text("New Line.a", encoding="utf-8")

        self.assertFalse(created.exists())

    def test_falls_back_when_the_first_root_is_not_ascii(self) -> None:
        accented = Path("C:/tmp/usuário")
        with ascii_workspace() as reference:
            usable = reference.parent
        with patch.object(
            opendss_engine,
            "_ascii_temp_roots",
            return_value=(accented, usable),
        ):
            with ascii_workspace() as workspace:
                self.assertEqual(workspace.parent, usable)

    def test_reports_every_attempt_when_no_root_works(self) -> None:
        with patch.object(
            opendss_engine,
            "_ascii_temp_roots",
            return_value=(Path("C:/tmp/usuário"),),
        ):
            with self.assertRaises(PowerFlowEngineError) as caught:
                with ascii_workspace():
                    pass

        self.assertIn("ASCII", str(caught.exception))
        self.assertIn("usuário", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
