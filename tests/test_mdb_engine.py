from __future__ import annotations

import datetime
import struct
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from circuit_viewer import mdb_engine
from circuit_viewer.mdb_engine import (
    MdbEngineError,
    MdbPasswordError,
    cell_to_text,
    connection_string,
    is_password_error,
    mdb_available,
    mdb_import_error,
    open_database,
    quote_identifier,
    sniff_access_format,
)


def write_header(path: Path, magic: bytes, version_byte: int) -> Path:
    """Escreve os 32 bytes iniciais de um banco Access."""

    header = bytearray(32)
    header[4 : 4 + len(magic)] = magic
    header[0x14] = version_byte
    path.write_bytes(bytes(header))
    return path


def reset_import_cache() -> None:
    mdb_engine._import_checked = False
    mdb_engine._import_error = None


class CellToTextTests(unittest.TestCase):
    """A conversão de tipos é a fronteira mais perigosa do recurso."""

    def test_null_becomes_the_empty_field_of_a_csv(self) -> None:
        self.assertEqual(cell_to_text(None), "")

    def test_text_is_untouched(self) -> None:
        # O strip fica com os importadores, exatamente como no CSV.
        self.assertEqual(cell_to_text("  57SE004009 "), "  57SE004009 ")

    def test_integers_keep_their_exact_text(self) -> None:
        self.assertEqual(cell_to_text(13), "13")
        self.assertEqual(cell_to_text(0), "0")
        self.assertEqual(cell_to_text(-1), "-1")

    def test_integral_floats_lose_the_decimal_point(self) -> None:
        # É a regra central: ESTADO "1.0" faria trace() ler toda chave fechada
        # como aberta, e FASES2 "13.0" não casaria com o fases2.json.
        self.assertEqual(cell_to_text(1.0), "1")
        self.assertEqual(cell_to_text(13.0), "13")
        self.assertEqual(cell_to_text(0.0), "0")
        self.assertEqual(cell_to_text(-1.0), "-1")

    def test_fractional_floats_keep_full_precision(self) -> None:
        # COMPR e VNOM reais da base; perder dígitos aqui muda a impedância.
        self.assertEqual(cell_to_text(41.297000885009766), "41.297000885009766")
        self.assertEqual(cell_to_text(13.800000190734863), "13.800000190734863")
        self.assertEqual(cell_to_text(0.9764), "0.9764")

    def test_float_round_trips(self) -> None:
        for value in (41.297000885009766, 0.1, 1e-7, 123456.789):
            with self.subTest(value=value):
                self.assertEqual(float(cell_to_text(value)), value)

    def test_non_finite_floats_stay_visible_for_the_importer_to_reject(self) -> None:
        self.assertEqual(cell_to_text(float("nan")), "nan")
        self.assertEqual(cell_to_text(float("inf")), "inf")

    def test_booleans_become_one_and_zero(self) -> None:
        # Access Yes/No. "True" não casaria com ESTADO == "1".
        self.assertEqual(cell_to_text(True), "1")
        self.assertEqual(cell_to_text(False), "0")

    def test_decimal_drops_trailing_zeros_without_scientific_notation(self) -> None:
        self.assertEqual(cell_to_text(Decimal("30.00")), "30")
        self.assertEqual(cell_to_text(Decimal("13.80")), "13.8")
        self.assertEqual(cell_to_text(Decimal("0.10")), "0.1")
        self.assertEqual(cell_to_text(Decimal("1000")), "1000")
        self.assertEqual(cell_to_text(Decimal("0")), "0")

    def test_dates_use_iso_8601(self) -> None:
        self.assertEqual(
            cell_to_text(datetime.datetime(2026, 8, 6, 14, 30, 5)),
            "2026-08-06 14:30:05",
        )
        self.assertEqual(cell_to_text(datetime.date(2026, 8, 6)), "2026-08-06")

    def test_binary_becomes_empty(self) -> None:
        self.assertEqual(cell_to_text(b"\x00\x01OLE"), "")
        self.assertEqual(cell_to_text(bytearray(b"x")), "")

    def test_identifiers_of_both_sides_produce_the_same_text(self) -> None:
        # BARRA.BARRA_ID e TRECHO.BARRA1_ID precisam casar em index_for_id,
        # mesmo quando o ODBC devolve um como int e o outro como float.
        self.assertEqual(cell_to_text(7), cell_to_text(7.0))


class QuoteIdentifierTests(unittest.TestCase):
    def test_wraps_in_brackets(self) -> None:
        self.assertEqual(quote_identifier("MODELO_CARGA"), "[MODELO_CARGA]")

    def test_doubles_a_closing_bracket(self) -> None:
        self.assertEqual(quote_identifier("A]B"), "[A]]B]")


class SniffAccessFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def test_jet4_is_supported(self) -> None:
        path = write_header(self.root / "rede.mdb", b"Standard Jet DB", 0x01)
        detected = sniff_access_format(path)
        self.assertTrue(detected.supported)
        self.assertIn("Jet 4", detected.label)

    def test_access_97_is_refused_with_an_actionable_reason(self) -> None:
        path = write_header(self.root / "antigo.mdb", b"Standard Jet DB", 0x00)
        detected = sniff_access_format(path)
        self.assertFalse(detected.supported)
        self.assertIn("Access 97", detected.label)
        self.assertIn("Salvar Como", detected.reason or "")

    def test_ace_formats_are_supported(self) -> None:
        for version_byte, expected in ((0x02, "2007"), (0x03, "2010")):
            with self.subTest(version_byte=version_byte):
                path = write_header(
                    self.root / f"b{version_byte}.accdb",
                    b"Standard ACE DB",
                    version_byte,
                )
                detected = sniff_access_format(path)
                self.assertTrue(detected.supported)
                self.assertIn(expected, detected.label)

    def test_unknown_magic_is_left_to_the_driver(self) -> None:
        path = write_header(self.root / "outro.mdb", b"SQLite format 3", 0x00)
        # Recusar aqui bloquearia um arquivo que o driver talvez abrisse.
        self.assertTrue(sniff_access_format(path).supported)

    def test_unknown_version_byte_is_left_to_the_driver(self) -> None:
        path = write_header(self.root / "novo.accdb", b"Standard ACE DB", 0x09)
        self.assertTrue(sniff_access_format(path).supported)

    def test_truncated_file_is_rejected(self) -> None:
        path = self.root / "curto.mdb"
        path.write_bytes(b"abc")
        with self.assertRaises(MdbEngineError):
            sniff_access_format(path)

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(MdbEngineError):
            sniff_access_format(self.root / "inexistente.mdb")


class PasswordErrorTests(unittest.TestCase):
    def test_recognizes_the_ace_codes(self) -> None:
        # O driver é localizado; casar só pela palavra "senha" não bastaria.
        self.assertTrue(is_password_error(Exception("Senha inválida. (-1905)")))
        self.assertTrue(is_password_error(Exception("Not a valid password 1907")))
        self.assertTrue(is_password_error(Exception("Contraseña no válida")))

    def test_ignores_unrelated_errors(self) -> None:
        self.assertFalse(is_password_error(Exception("Tabela não encontrada")))


class ConnectionStringTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(
            mdb_engine,
            "_select_driver",
            return_value="Microsoft Access Driver (*.mdb, *.accdb)",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_is_read_only_and_carries_the_path(self) -> None:
        text = connection_string(r"C:\dados\rede.mdb")
        self.assertIn("ReadOnly=1", text)
        self.assertIn(r"DBQ=C:\dados\rede.mdb", text)
        self.assertNotIn("PWD=", text)

    def test_never_uses_the_oledb_mode_attribute(self) -> None:
        # Testado contra o driver: "Atributo de cadeia de conexão inválido Mode".
        self.assertNotIn("Mode=", connection_string(r"C:\dados\rede.mdb"))

    def test_no_attribute_of_the_driver_is_ever_braced(self) -> None:
        # Medido contra o driver: DBQ={...} responde "Nome de arquivo inválido
        # (-1044)" e PWD={...} faz a senha chegar com as chaves, rejeitada
        # sempre. Só o DRIVER é citado, e esse quem lê é o Gerenciador de
        # Driver do Windows, que segue o padrão ODBC.
        text = connection_string(r"C:\dados\rede.mdb", "MinhaSenha")
        self.assertNotIn("DBQ={", text)
        self.assertNotIn("PWD={", text)
        self.assertIn("DRIVER={", text)

    def test_the_password_goes_raw(self) -> None:
        text = connection_string(r"C:\dados\rede.mdb", "MinhaSenha")
        self.assertIn("PWD=MinhaSenha;", text)

    def test_matches_the_form_known_to_work(self) -> None:
        """Amarra a cadeia à do script que abre o banco real.

        A referência é ``mdb_viewer_app/database.py`` do projeto MDB_VIEWER, que
        conecta ao mesmo banco protegido com a mesma senha. A única diferença
        admitida é o ``ReadOnly=1``, que o driver aceita (medido) e que sustenta
        a garantia de somente leitura.
        """

        driver = "Microsoft Access Driver (*.mdb, *.accdb)"
        reference = f"DRIVER={{{driver}}};DBQ=C:\\dados\\rede.mdb;PWD=MinhaSenha;"
        generated = connection_string(r"C:\dados\rede.mdb", "MinhaSenha")
        self.assertEqual(generated.replace("ReadOnly=1;", ""), reference)

    def test_a_password_with_braces_is_accepted(self) -> None:
        # Sem citação, as chaves deixam de ter significado na cadeia.
        text = connection_string(r"C:\dados\rede.mdb", "ab}c{d")
        self.assertIn("PWD=ab}c{d;", text)

    def test_a_password_with_spaces_is_accepted(self) -> None:
        text = connection_string(r"C:\dados\rede.mdb", "duas palavras")
        self.assertIn("PWD=duas palavras;", text)

    def test_a_semicolon_in_the_path_is_refused(self) -> None:
        with self.assertRaises(MdbEngineError):
            connection_string(r"C:\da;dos\rede.mdb")

    def test_a_semicolon_in_the_password_is_refused(self) -> None:
        # É o único caractere inexprimível sem citação: encerraria o atributo.
        with self.assertRaises(MdbEngineError) as caught:
            connection_string(r"C:\dados\rede.mdb", "a;b")
        self.assertIn("PWD", str(caught.exception))

    def test_the_refusal_does_not_repeat_the_password(self) -> None:
        with self.assertRaises(MdbEngineError) as caught:
            connection_string(r"C:\dados\rede.mdb", "segredo;do;usuario")
        self.assertNotIn("segredo", str(caught.exception))


class ImportDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._checked = mdb_engine._import_checked
        self._error = mdb_engine._import_error
        reset_import_cache()

    def tearDown(self) -> None:
        mdb_engine._import_checked = self._checked
        mdb_engine._import_error = self._error

    def test_missing_library_reports_how_to_install_it(self) -> None:
        with patch.dict(sys.modules, {"pyodbc": None}):
            message = mdb_import_error()

        self.assertIsNotNone(message)
        self.assertIn("pyodbc", message)
        self.assertIn("pip install", message)

    def test_missing_driver_names_the_process_architecture(self) -> None:
        fake = type("FakePyodbc", (), {"drivers": staticmethod(lambda: ["SQL Server"])})
        with patch.dict(sys.modules, {"pyodbc": fake}):
            message = mdb_import_error()

        self.assertIsNotNone(message)
        bits = "64" if struct.calcsize("P") * 8 == 64 else "32"
        # É a causa nº 1 de "driver não encontrado" com o driver instalado.
        self.assertIn(f"{bits} bits", message)

    def test_available_when_an_access_driver_exists(self) -> None:
        fake = type(
            "FakePyodbc",
            (),
            {
                "drivers": staticmethod(
                    lambda: ["Microsoft Access Driver (*.mdb, *.accdb)"]
                )
            },
        )
        with patch.dict(sys.modules, {"pyodbc": fake}):
            self.assertIsNone(mdb_import_error())
            reset_import_cache()
            self.assertTrue(mdb_available())

    def test_result_is_memoized(self) -> None:
        with patch.dict(sys.modules, {"pyodbc": None}):
            first = mdb_import_error()
        # Fora do patch a biblioteca existe nesta máquina; o memo mantém a
        # primeira resposta, que é o que evita recarregar o gerenciador de
        # drivers a cada sincronização de menu.
        self.assertEqual(mdb_import_error(), first)


class OpenDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self._checked = mdb_engine._import_checked
        self._error = mdb_engine._import_error
        mdb_engine._import_checked = True
        mdb_engine._import_error = None

    def tearDown(self) -> None:
        mdb_engine._import_checked = self._checked
        mdb_engine._import_error = self._error

    def test_missing_file_is_reported_before_connecting(self) -> None:
        with self.assertRaises(MdbEngineError) as caught:
            with open_database(self.root / "inexistente.mdb"):
                pass
        self.assertIn("não encontrado", str(caught.exception))

    def test_access_97_is_refused_before_connecting(self) -> None:
        path = write_header(self.root / "antigo.mdb", b"Standard Jet DB", 0x00)
        with patch.object(mdb_engine, "_connect") as connect:
            with self.assertRaises(MdbEngineError) as caught:
                with open_database(path):
                    pass
        connect.assert_not_called()
        self.assertIn("Access 97", str(caught.exception))

    def test_unavailable_library_is_reported(self) -> None:
        mdb_engine._import_error = "pyodbc ausente"
        path = write_header(self.root / "rede.mdb", b"Standard Jet DB", 0x01)
        with self.assertRaises(MdbEngineError) as caught:
            with open_database(path):
                pass
        self.assertIn("pyodbc ausente", str(caught.exception))

    def test_closes_the_database_even_when_the_body_raises(self) -> None:
        path = write_header(self.root / "rede.mdb", b"Standard Jet DB", 0x01)
        closed: list[bool] = []

        class FakeDatabase:
            def close(self) -> None:
                closed.append(True)

        with patch.object(mdb_engine, "_connect", return_value=FakeDatabase()):
            with self.assertRaises(RuntimeError):
                with open_database(path):
                    raise RuntimeError("falha no meio da leitura")

        self.assertEqual(closed, [True])


class ConnectTests(unittest.TestCase):
    """Comportamento de :func:`_connect` sem um driver ODBC real."""

    def setUp(self) -> None:
        self.path = Path("C:/dados/rede.mdb")
        patcher = patch.object(
            mdb_engine,
            "_select_driver",
            return_value="Microsoft Access Driver (*.mdb, *.accdb)",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pyodbc(self, connect) -> object:  # noqa: ANN001
        return type("FakePyodbc", (), {"connect": staticmethod(connect)})

    def test_asks_for_a_read_only_connection(self) -> None:
        calls: list[dict] = []

        def connect(text, **kwargs):  # noqa: ANN001, ANN202
            calls.append({"text": text, **kwargs})
            return object()

        with patch.dict(sys.modules, {"pyodbc": self._pyodbc(connect)}):
            database = mdb_engine._connect(self.path, None)

        self.assertTrue(database.readonly_attribute)
        self.assertTrue(calls[0]["readonly"])
        self.assertTrue(calls[0]["autocommit"])
        self.assertIn("ReadOnly=1", calls[0]["text"])

    def test_falls_back_when_the_driver_refuses_the_read_only_attribute(self) -> None:
        attempts: list[bool] = []

        def connect(text, **kwargs):  # noqa: ANN001, ANN202
            readonly = bool(kwargs.get("readonly"))
            attempts.append(readonly)
            if readonly:
                raise RuntimeError("driver não aceita SQL_MODE_READ_ONLY")
            return object()

        with patch.dict(sys.modules, {"pyodbc": self._pyodbc(connect)}):
            database = mdb_engine._connect(self.path, None)

        # As outras três camadas de proteção continuam valendo.
        self.assertEqual(attempts, [True, False])
        self.assertFalse(database.readonly_attribute)

    def test_password_error_becomes_a_dedicated_exception(self) -> None:
        def connect(text, **kwargs):  # noqa: ANN001, ANN202
            raise RuntimeError("[42000] Senha inválida. (-1905)")

        with patch.dict(sys.modules, {"pyodbc": self._pyodbc(connect)}):
            with self.assertRaises(MdbPasswordError) as caught:
                mdb_engine._connect(self.path, None)

        self.assertIn("protegido por senha", str(caught.exception))

    def test_wrong_password_says_so(self) -> None:
        def connect(text, **kwargs):  # noqa: ANN001, ANN202
            raise RuntimeError("[42000] Senha inválida. (-1905)")

        with patch.dict(sys.modules, {"pyodbc": self._pyodbc(connect)}):
            with self.assertRaises(MdbPasswordError) as caught:
                mdb_engine._connect(self.path, "errada")

        self.assertIn("não confere", str(caught.exception))

    def test_the_password_never_reaches_the_message(self) -> None:
        def connect(text, **kwargs):  # noqa: ANN001, ANN202
            raise RuntimeError("[42000] Senha inválida. (-1905)")

        with patch.dict(sys.modules, {"pyodbc": self._pyodbc(connect)}):
            with self.assertRaises(MdbPasswordError) as caught:
                mdb_engine._connect(self.path, "segredo-do-usuario")

        self.assertNotIn("segredo-do-usuario", str(caught.exception))

    def test_other_failures_name_the_file(self) -> None:
        def connect(text, **kwargs):  # noqa: ANN001, ANN202
            raise RuntimeError("disco indisponível")

        with patch.dict(sys.modules, {"pyodbc": self._pyodbc(connect)}):
            with self.assertRaises(MdbEngineError) as caught:
                mdb_engine._connect(self.path, None)

        self.assertIn("rede.mdb", str(caught.exception))


class OdbcAccessDatabaseTests(unittest.TestCase):
    """A camada fina sobre o cursor, com uma conexão falsa."""

    class FakeCursor:
        def __init__(self, connection) -> None:  # noqa: ANN001
            self._connection = connection
            self.description = None
            self._rows: list[tuple] = []

        def tables(self, tableType=None):  # noqa: ANN001, N803
            return [
                type("Row", (), {"table_name": name})
                for name in self._connection.table_names
            ]

        def columns(self, table=None):  # noqa: ANN001
            if self._connection.columns_raise:
                raise RuntimeError("catálogo indisponível")
            return [
                type("Row", (), {"table_name": table, "column_name": name})
                for name in self._connection.column_names
            ]

        def execute(self, sql, *parameters):  # noqa: ANN001
            self._connection.statements.append(sql)
            if "COUNT(*)" in sql:
                self._rows = [(len(self._connection.rows),)]
            elif "WHERE 1=0" in sql:
                self.description = [
                    (name,) for name in self._connection.column_names
                ]
                self._rows = []
            else:
                self._rows = list(self._connection.rows)
            return self

        def fetchone(self):
            return self._rows.pop(0) if self._rows else None

        def fetchmany(self, size):  # noqa: ANN001
            batch, self._rows = self._rows[:size], self._rows[size:]
            return batch

        def close(self) -> None:
            self._connection.closed_cursors += 1

    class FakeConnection:
        def __init__(self, rows=(), table_names=(), column_names=()) -> None:  # noqa: ANN001
            self.rows = list(rows)
            self.table_names = list(table_names)
            self.column_names = list(column_names)
            self.statements: list[str] = []
            self.closed_cursors = 0
            self.columns_raise = False
            self.closed = False

        def cursor(self):
            return OdbcAccessDatabaseTests.FakeCursor(self)

        def close(self) -> None:
            self.closed = True

    def _database(self, connection):  # noqa: ANN001
        return mdb_engine._OdbcAccessDatabase(
            connection, Path("C:/dados/rede.mdb"), readonly_attribute=True
        )

    def test_tables_hides_the_access_system_tables(self) -> None:
        connection = self.FakeConnection(
            table_names=["TRECHO", "MSysObjects", "BARRA", "MSysACEs"]
        )
        self.assertEqual(self._database(connection).tables(), ("BARRA", "TRECHO"))

    def test_columns_uses_the_catalog(self) -> None:
        connection = self.FakeConnection(column_names=["BARRA_ID", "CODIGO"])
        self.assertEqual(
            self._database(connection).columns("BARRA"), ("BARRA_ID", "CODIGO")
        )

    def test_columns_falls_back_to_an_empty_select(self) -> None:
        connection = self.FakeConnection(column_names=["BARRA_ID", "X"])
        connection.columns_raise = True
        self.assertEqual(
            self._database(connection).columns("BARRA"), ("BARRA_ID", "X")
        )
        self.assertIn("WHERE 1=0", connection.statements[-1])

    def test_row_count(self) -> None:
        connection = self.FakeConnection(rows=[(1,), (2,), (3,)])
        self.assertEqual(self._database(connection).row_count("BARRA"), 3)

    def test_iter_rows_projects_and_converts(self) -> None:
        connection = self.FakeConnection(rows=[(7, 13.0, None), (8, 41.5, "ok")])
        rows = list(
            self._database(connection).iter_rows(
                "TRECHO", ["BARRA1_ID", "FASES2", "CODIGO"], batch_size=1
            )
        )
        self.assertEqual(rows, [("7", "13", ""), ("8", "41.5", "ok")])

    def test_iter_rows_only_selects_the_requested_columns(self) -> None:
        connection = self.FakeConnection(rows=[])
        list(self._database(connection).iter_rows("CARGA", ["CARGA_ID", "SNOM"]))
        statement = connection.statements[-1]
        # A tabela CARGA tem 43 colunas; projetar tudo seria desperdício.
        self.assertEqual(
            statement, "SELECT [CARGA_ID], [SNOM] FROM [CARGA]"
        )

    def test_iter_rows_without_columns_is_refused(self) -> None:
        connection = self.FakeConnection()
        with self.assertRaises(MdbEngineError):
            list(self._database(connection).iter_rows("CARGA", []))

    def test_only_select_statements_are_ever_issued(self) -> None:
        connection = self.FakeConnection(rows=[(1, 2)], column_names=["A", "B"])
        database = self._database(connection)
        database.row_count("BARRA")
        database.columns("BARRA")
        list(database.iter_rows("BARRA", ["A", "B"]))
        for statement in connection.statements:
            with self.subTest(statement=statement):
                self.assertTrue(statement.upper().startswith("SELECT"))

    def test_repr_does_not_leak_the_path_or_password(self) -> None:
        database = self._database(self.FakeConnection())
        self.assertEqual(repr(database), "<AccessDatabase rede.mdb>")


if __name__ == "__main__":
    unittest.main()
