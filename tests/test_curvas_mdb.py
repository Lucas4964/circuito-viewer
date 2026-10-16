"""Leitura do banco de curvas típicas do Interplan com um banco falso."""

from __future__ import annotations

import os
import threading
import unittest
from contextlib import contextmanager
from unittest import mock

from circuit_viewer.curvas import HOURLY_CURVE_POINT_COUNT, MAX_CURVE_NAME_LENGTH, Curve
from circuit_viewer.curvas_categorias import CurveCategory
from circuit_viewer.curvas_mdb import (
    CurveDatabaseContents,
    CurveDatabaseError,
    interplan_curve_id,
    merge_curve_database,
    read_curve_database,
)
from tests.test_mdb_import import FakeDatabase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtWidgets import QApplication

    from circuit_viewer.workers import CurveDatabaseWorker

    PYQT_AVAILABLE = True
except ImportError:  # pragma: no cover - depende do ambiente
    PYQT_AVAILABLE = False


CATEGORY_COLUMNS = [
    "CAT_ID", "CAT_DESC", "CLASSE", "MIN", "MAX", "N_PONTOS", "TIPODEM",
    "PU_MAX", "PART_MERC", "TIPO", "GRUPO_ID",
]
TYPICAL_COLUMNS = ["CUR_ID", "CAT_ID", "FAT_NORM", "PERIODO", "FAT_POT"]
POINT_COLUMNS = ["CUR_ID", "PTONUM", "MEDIA_P", "DESVIO_P", "MEDIA_Q", "DESVIO_Q"]


def _points(curve_id: int, *, count: int = HOURLY_CURVE_POINT_COUNT) -> list[tuple]:
    return [
        (curve_id, hour, curve_id + hour / 100, 0.1, None, None)
        for hour in range(1, count + 1)
    ]


def curves_database(**overrides) -> FakeDatabase:
    """Três categorias como as do Curvas.mdb real, em tipos nativos do Access."""

    tables: dict[str, tuple[list[str], list[tuple]]] = {
        "CURVAS_CATEGORIA": (
            CATEGORY_COLUMNS,
            [
                (1, "Clientes BT IP", 5, -1, -1, 24, 0, 1, 100.0, "C", 1),
                (2, "Clientes BT res_0_100 kWh - Tipologia 1", 0, -1, 100, 24, 0, 0,
                 25.527344128043676, "C", 1),
                (387, "Geração Solar - Curva Default", 0, -1, -1, 24, 0, 0, 100.0, "F", 1),
            ],
        ),
        "CURVAS_CURVATIPICA": (
            TYPICAL_COLUMNS,
            [
                (1, 1, 23354.3, 1, 0.92),
                (4, 1, 32167.2, 4, 0.92),
                (5, 2, 1386.9, 1, 0.92),
                (6, 2, 263.4, 2, 0.92),
                (7, 2, 265.1, 3, 0.92),
                (8, 2, 1915.5, 4, 0.92),
                (9, 387, 313.67, 4, 1.0),
            ],
        ),
        "CURVAS_PONTOS": (
            POINT_COLUMNS,
            [row for curve_id in (1, 4, 5, 6, 7, 8, 9) for row in _points(curve_id)],
        ),
    }
    tables.update(overrides)
    return FakeDatabase(tables)


class ReadCurveDatabaseTests(unittest.TestCase):
    def test_every_typical_curve_becomes_a_curve(self) -> None:
        contents = read_curve_database(curves_database())
        self.assertEqual(
            [curve.curve_id for curve in contents.curves],
            [
                "interplan-cat1-p1", "interplan-cat1-p4",
                "interplan-cat2-p1", "interplan-cat2-p2",
                "interplan-cat2-p3", "interplan-cat2-p4",
                "interplan-cat387-p4",
            ],
        )
        self.assertEqual(contents.issues, ())

    def test_name_link_and_period_follow_the_category(self) -> None:
        curves = {curve.curve_id: curve for curve in read_curve_database(curves_database()).curves}
        saturday = curves["interplan-cat2-p2"]
        self.assertEqual(saturday.name, "Clientes BT res_0_100 kWh - Tipologia 1 - Sábado")
        self.assertEqual(saturday.link_id, "2")
        self.assertEqual(saturday.period, "SA")
        self.assertEqual(curves["interplan-cat387-p4"].name, "Geração Solar - Curva Default - Média")

    def test_values_are_media_p_in_point_order(self) -> None:
        curve = read_curve_database(curves_database()).curves[0]
        self.assertEqual(curve.values, tuple(1 + hour / 100 for hour in range(1, 25)))

    def test_categories_carry_the_selection_data(self) -> None:
        categories = {
            category.category_id: category
            for category in read_curve_database(curves_database()).categories
        }
        self.assertEqual(list(categories), ["1", "2", "387"])
        residential = categories["2"]
        self.assertEqual(residential.consumer_class, 0)
        self.assertEqual((residential.minimum, residential.maximum), (-1.0, 100.0))
        self.assertAlmostEqual(residential.market_share, 25.527344128043676)
        self.assertEqual(residential.kind, "C")
        self.assertEqual(residential.power_factor, 0.92)
        self.assertEqual(categories["1"].pu_max, 1)
        self.assertEqual(categories["387"].kind, "F")
        self.assertEqual(categories["387"].power_factor, 1.0)

    def test_category_ids_sort_as_numbers(self) -> None:
        database = curves_database(
            CURVAS_CATEGORIA=(
                CATEGORY_COLUMNS,
                [
                    (10, "Dez", 0, -1, -1, 24, 0, 0, 100.0, "C", 1),
                    (9, "Nove", 0, -1, -1, 24, 0, 0, 100.0, "C", 1),
                ],
            ),
            CURVAS_CURVATIPICA=(TYPICAL_COLUMNS, [(1, 10, 1.0, 4, 0.92), (4, 9, 1.0, 4, 0.92)]),
        )
        contents = read_curve_database(database)
        self.assertEqual([item.category_id for item in contents.categories], ["9", "10"])
        self.assertEqual([curve.link_id for curve in contents.curves], ["9", "10"])

    def test_a_category_without_24_points_is_left_out(self) -> None:
        database = curves_database(
            CURVAS_CATEGORIA=(
                CATEGORY_COLUMNS,
                [
                    (1, "Clientes BT IP", 5, -1, -1, 96, 0, 1, 100.0, "C", 1),
                    (2, "Residencial", 0, -1, 100, 24, 0, 0, 25.5, "C", 1),
                    (387, "Solar", 0, -1, -1, 24, 0, 0, 100.0, "F", 1),
                ],
            ),
        )
        contents = read_curve_database(database)
        self.assertNotIn("1", [item.category_id for item in contents.categories])
        self.assertFalse(any(curve.link_id == "1" for curve in contents.curves))
        self.assertTrue(any("N_PONTOS 96" in issue for issue in contents.issues))

    def test_a_curve_with_missing_points_is_left_out(self) -> None:
        points = [row for row in curves_database()._tables["CURVAS_PONTOS"][1] if not (row[0] == 5 and row[1] == 7)]
        contents = read_curve_database(
            curves_database(CURVAS_PONTOS=(POINT_COLUMNS, points))
        )
        self.assertNotIn(
            "interplan-cat2-p1", [curve.curve_id for curve in contents.curves]
        )
        self.assertTrue(any("23 ponto(s)" in issue for issue in contents.issues))

    def test_an_old_database_without_period_or_kind_reads_as_the_average(self) -> None:
        database = curves_database(
            CURVAS_CATEGORIA=(
                ["CAT_ID", "CAT_DESC", "CLASSE", "MIN", "MAX", "N_PONTOS", "PART_MERC"],
                [(2, "Residencial", 0, -1, 100, 24, 25.5)],
            ),
            CURVAS_CURVATIPICA=(["CUR_ID", "CAT_ID"], [(8, 2)]),
        )
        contents = read_curve_database(database)
        (curve,) = contents.curves
        self.assertEqual(curve.curve_id, "interplan-cat2-p4")
        self.assertEqual(curve.period, "MED")
        (category,) = contents.categories
        self.assertEqual((category.kind, category.pu_max, category.power_factor), ("C", 0, None))

    def test_a_long_description_is_cut_to_fit_the_name_limit(self) -> None:
        description = "Clientes A4 com uma descrição bem comprida que não cabe no limite"
        database = curves_database(
            CURVAS_CATEGORIA=(
                CATEGORY_COLUMNS,
                [(2, description, -1, 0, 50, 24, 0, 0, 100.0, "C", 1)],
            ),
            CURVAS_CURVATIPICA=(TYPICAL_COLUMNS, [(5, 2, 1.0, 1, 0.92)]),
        )
        (curve,) = read_curve_database(database).curves
        self.assertLessEqual(len(curve.name), MAX_CURVE_NAME_LENGTH)
        self.assertTrue(curve.name.endswith("… - Dia útil"))

    def test_a_repeated_description_gets_the_category_id(self) -> None:
        database = curves_database(
            CURVAS_CATEGORIA=(
                CATEGORY_COLUMNS,
                [
                    (2, "Residencial", 0, -1, 100, 24, 0, 0, 50.0, "C", 1),
                    (3, "Residencial", 0, -1, 100, 24, 0, 0, 50.0, "C", 1),
                ],
            ),
            CURVAS_CURVATIPICA=(TYPICAL_COLUMNS, [(5, 2, 1.0, 4, 0.92), (6, 3, 1.0, 4, 0.92)]),
        )
        contents = read_curve_database(database)
        self.assertEqual(
            [curve.name for curve in contents.curves],
            ["Residencial - Média", "Residencial #3 - Média"],
        )

    def test_a_missing_table_is_an_error(self) -> None:
        database = curves_database()
        del database._tables["CURVAS_PONTOS"]
        with self.assertRaises(CurveDatabaseError):
            read_curve_database(database)

    def test_a_missing_required_column_is_an_error(self) -> None:
        database = curves_database(
            CURVAS_PONTOS=(["CUR_ID", "PTONUM"], [(1, 1)]),
        )
        with self.assertRaisesRegex(CurveDatabaseError, "MEDIA_P"):
            read_curve_database(database)

    def test_cancellation_interrupts_the_reading(self) -> None:
        with self.assertRaises(InterruptedError):
            read_curve_database(curves_database(), cancel_check=lambda: True)


def _curve(curve_id: str, name: str, link_id: str = "", period: str = "") -> Curve:
    return Curve(curve_id, name, tuple(float(hour) for hour in range(24)), link_id, period)


class MergeCurveDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contents = CurveDatabaseContents(
            curves=(
                _curve(interplan_curve_id("2", "1"), "Residencial - Dia útil", "2", "DU"),
                _curve(interplan_curve_id("2", "4"), "Residencial - Média", "2", "MED"),
            ),
            categories=(CurveCategory("2", "Residencial", 0, -1.0, 100.0, 25.5),),
        )

    def test_new_curves_go_after_the_user_curves(self) -> None:
        user = _curve("u1", "Minha curva")
        merged = merge_curve_database((user,), (), self.contents)
        self.assertEqual(
            [curve.curve_id for curve in merged.curves],
            ["u1", "interplan-cat2-p1", "interplan-cat2-p4"],
        )
        self.assertEqual((merged.added, merged.replaced), (2, 0))

    def test_importing_again_replaces_in_place(self) -> None:
        old = _curve("interplan-cat2-p1", "Nome antigo", "2", "DU")
        user = _curve("u1", "Minha curva")
        merged = merge_curve_database((old, user), (), self.contents)
        self.assertEqual(
            [curve.name for curve in merged.curves],
            ["Residencial - Dia útil", "Minha curva", "Residencial - Média"],
        )
        self.assertEqual((merged.added, merged.replaced), (1, 1))

    def test_a_user_curve_keeps_its_name_and_the_imported_one_gets_a_suffix(self) -> None:
        user = _curve("u1", "Residencial - Média")
        merged = merge_curve_database((user,), (), self.contents)
        names = [curve.name for curve in merged.curves]
        self.assertEqual(names[0], "Residencial - Média")
        self.assertIn("Residencial - Média (2)", names)
        self.assertEqual(merged.renamed, ("Residencial - Média (2)",))

    def test_categories_are_merged_by_id(self) -> None:
        other = CurveCategory("9", "Outra", 1, -1.0, -1.0, 100.0)
        stale = CurveCategory("2", "Antiga", 0, -1.0, 50.0, 10.0)
        merged = merge_curve_database((), (stale, other), self.contents)
        self.assertEqual(
            [(item.category_id, item.description) for item in merged.categories],
            [("2", "Residencial"), ("9", "Outra")],
        )


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt6 não está instalado")
class CurveDatabaseWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def _run(self, open_database, *, password=None):  # noqa: ANN001, ANN202
        worker = CurveDatabaseWorker("Curvas.mdb", password=password)
        signals: dict[str, list] = {"finished": [], "failed": [], "password": []}
        worker.finished.connect(signals["finished"].append)
        worker.failed.connect(signals["failed"].append)
        worker.password_required.connect(lambda: signals["password"].append(True))
        with mock.patch("circuit_viewer.workers.open_database", open_database):
            worker.run()
        self.assertIsNone(worker._password)
        return signals

    def test_delivers_the_contents(self) -> None:
        @contextmanager
        def open_database(path, password):  # noqa: ANN001, ARG001
            yield curves_database()

        signals = self._run(open_database)
        (contents,) = signals["finished"]
        self.assertEqual(len(contents.curves), 7)

    def test_asks_for_the_password(self) -> None:
        from circuit_viewer.mdb_engine import MdbPasswordError

        def open_database(path, password):  # noqa: ANN001, ARG001
            raise MdbPasswordError("protegido")

        self.assertEqual(self._run(open_database)["password"], [True])

    def test_a_failure_never_shows_the_password(self) -> None:
        from circuit_viewer.mdb_engine import MdbEngineError

        def open_database(path, password):  # noqa: ANN001, ARG001
            raise MdbEngineError("DRIVER={Access};DBQ=x;PWD=segredo;")

        (message,) = self._run(open_database, password="segredo")["failed"]
        self.assertNotIn("segredo", message)

    def test_a_cancelled_worker_stays_silent(self) -> None:
        opened = threading.Event()

        @contextmanager
        def open_database(path, password):  # noqa: ANN001, ARG001
            opened.set()
            yield curves_database()

        worker = CurveDatabaseWorker("Curvas.mdb")
        finished = []
        worker.finished.connect(finished.append)
        worker.cancel()
        with mock.patch("circuit_viewer.workers.open_database", open_database):
            worker.run()
        self.assertFalse(opened.is_set())
        self.assertEqual(finished, [])


if __name__ == "__main__":
    unittest.main()
