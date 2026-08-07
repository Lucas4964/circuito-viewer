"""Acesso somente leitura a bancos Microsoft Access, via ``pyodbc``.

Este é o **único** módulo do pacote que conhece ``pyodbc``, pela mesma razão que
faz ``opendss_engine`` ser o único a conhecer ``py_dss_interface``: a biblioteca
é uma dependência opcional, e o driver que ela usa carrega restrições de
ambiente que precisam de contenção em um lugar só.

1. O driver ODBC do Access (ACE) precisa ter a **mesma arquitetura do processo**.
   Um Python de 64 bits não enxerga o driver de 32 bits, e o sintoma é
   "driver não encontrado" com o driver visivelmente instalado — daí
   :func:`mdb_import_error` citar a arquitetura na mensagem.
2. O ACE 2013 em diante **não abre** bancos Access 97 (Jet 3) e responde com um
   erro genérico de formato. :func:`sniff_access_format` lê o cabeçalho antes de
   conectar e troca isso por uma mensagem acionável.
3. Banco protegido responde ``-1905``/``1907``; :func:`is_password_error`
   reconhece o caso para a interface pedir a senha em vez de mostrar o erro cru.
4. **O driver não remove as chaves de citação dos seus próprios atributos.** O
   padrão ODBC prevê ``ATRIBUTO={valor}`` para valores com caracteres especiais,
   e o ``DRIVER={...}`` depende disso — mas quem lê o ``DRIVER`` é o Gerenciador
   de Driver do Windows. ``DBQ`` e ``PWD`` são lidos pelo driver do Access, que
   recebe as chaves como parte do valor. Citar a senha a torna sempre incorreta;
   citar o caminho faz o arquivo "não existir". Ver
   :func:`connection_string`.

**A conexão é somente leitura em quatro camadas**: ``ReadOnly=1`` na cadeia (que
o ACE honra), ``SQL_MODE_READ_ONLY`` pelo ``readonly=True`` do pyodbc,
``autocommit`` para nunca abrir transação, e uma API que só emite ``SELECT`` —
não há ``execute`` livre, ``commit`` nem cursor exposto para fora daqui.

Ressalva conhecida: enquanto a conexão existe, o ACE cria um arquivo de trava
``.ldb`` ao lado do banco e o remove ao fechar. É do motor, não da aplicação, e
não altera o ``.mdb``; mas numa pasta sem permissão de escrita o ACE se recusa a
abrir o banco, e é isso que a mensagem de erro correspondente explica.

Nada aqui importa Qt, e o import de ``pyodbc`` é tardio: importar este módulo é
sempre seguro, mesmo sem a biblioteca instalada.
"""

from __future__ import annotations

import datetime as _datetime
import math
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence


PACKAGE_NAME = "pyodbc"
PREFERRED_DRIVER = "Microsoft Access Driver (*.mdb, *.accdb)"
DRIVER_KEYWORD = "Access"
DEFAULT_BATCH_SIZE = 500

# Códigos com que o ACE denuncia senha ausente ou incorreta. O driver é
# localizado, então casar pela mensagem ("senha"/"password") não basta.
PASSWORD_ERROR_TOKENS = ("-1905", "1907", "senha", "password", "contrase")

_import_error: str | None = None
_import_checked = False


class MdbEngineError(RuntimeError):
    """Falha ao abrir ou ler um banco Access."""


class MdbPasswordError(MdbEngineError):
    """O banco exige senha, ou a senha informada não confere."""


class AccessDatabase(Protocol):
    """Superfície somente leitura que o importador consome.

    Existe para :mod:`circuit_viewer.mdb_import` ser testável com um banco
    falso, sem ``pyodbc`` nem driver ODBC instalados — a mesma razão de
    ``DssEngine`` existir em ``opendss_engine``.
    """

    def tables(self) -> tuple[str, ...]:
        """Nomes das tabelas de usuário, sem as ``MSys*`` do próprio Access."""

    def columns(self, table: str) -> tuple[str, ...]:
        """Nomes das colunas de ``table``, na ordem do banco."""

    def row_count(self, table: str) -> int:
        """Quantidade de registros de ``table``."""

    def iter_rows(
        self,
        table: str,
        columns: Sequence[str],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> Iterator[tuple[str, ...]]:
        """Percorre ``table`` projetando ``columns``, já convertidas em texto."""


# --------------------------------------------------------------------------- #
# Conversão de valores
# --------------------------------------------------------------------------- #


def cell_to_text(value: object) -> str:
    """Converte um valor do ODBC no texto que os importadores esperam.

    Não é formatação: é a fronteira em que um banco tipado vira as mesmas
    cadeias que o CSV entregaria, e **três comparações do núcleo são textuais e
    exatas**, então um deslize aqui quebra a aplicação em silêncio.

    - ``PhaseConfiguration.classify`` casa ``FASES2`` por texto: ``13`` precisa
      virar ``"13"``, porque ``"13.0"`` cai em "sem relação" e apaga a cor da
      rede inteira;
    - ``NetworkTopology.trace`` e o exportador de chaves testam ``ESTADO ==
      "1"``: ``"1.0"`` transformaria toda chave fechada em aberta, ilhando a
      rede sem nenhum aviso;
    - todo ``index_for_id`` casa identificador por texto, então o ``BARRA1_ID``
      de um trecho precisa produzir exatamente o mesmo texto que o ``BARRA_ID``
      da barra.

    Daí a regra central: **float e Decimal de valor inteiro saem sem casa
    decimal**. Os não inteiros usam ``repr``/``format(..., "f")``, que preservam
    o valor original — ``41.297000885009766`` chega íntegro ao ``COMPR``.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        # O ``.strip()`` é responsabilidade dos importadores, como no CSV.
        return value
    # bool antes de int: em Python bool É int, e "True" não casaria com nada.
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            # Deixa o texto passar: os parsers dos importadores recusam "nan" e
            # "inf" com um diagnóstico melhor do que um campo vazio daria.
            return repr(value)
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        # ``normalize`` remove zeros à direita mas produz notação científica
        # ("30.00" vira "3E+1"); o format "f" desfaz isso sem reintroduzi-los.
        return format(value.normalize(), "f")
    if isinstance(value, _datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (_datetime.date, _datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Campo OLE/binário não é dado de rede. Uma coluna obrigatória de tipo
        # binário é recusada na resolução do mapeamento, antes de chegar aqui.
        return ""
    return str(value)


def quote_identifier(name: str) -> str:
    """Escapa um identificador no dialeto do Access."""

    return "[" + str(name).replace("]", "]]") + "]"


# --------------------------------------------------------------------------- #
# Formato do arquivo
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AccessFormat:
    """Resultado da leitura do cabeçalho do arquivo."""

    label: str
    supported: bool
    reason: str | None = None


# Byte 0x14 do cabeçalho. O ACE 2013+ removeu o suporte a Jet 3; os demais
# continuam abrindo normalmente.
_FORMAT_BY_VERSION_BYTE: dict[int, tuple[str, bool, str | None]] = {
    0x00: (
        "Access 97 (Jet 3)",
        False,
        "O driver do Access 2013 em diante não abre bancos no formato do "
        "Access 97. Converta o arquivo em Banco de Dados > Salvar Como no "
        "próprio Access, ou exporte as tabelas para CSV.",
    ),
    0x01: ("Access 2000-2003 (Jet 4)", True, None),
    0x02: ("Access 2007 (ACE 12)", True, None),
    0x03: ("Access 2010 ou posterior (ACE 14)", True, None),
}


def sniff_access_format(path: str | Path) -> AccessFormat:
    """Identifica a versão do banco lendo os 32 primeiros bytes do arquivo.

    Serve para recusar o Access 97 com uma explicação em vez de deixar o driver
    responder um erro genérico de formato. Formatos desconhecidos passam: quem
    decide é o driver, e recusar aqui bloquearia um arquivo válido a mais.
    """

    source = Path(path)
    try:
        with source.open("rb") as handle:
            header = handle.read(32)
    except OSError as exc:
        raise MdbEngineError(
            f"Não foi possível ler {source}: {exc.strerror or exc}"
        ) from exc
    if len(header) < 32:
        raise MdbEngineError(
            f"{source.name} é pequeno demais para ser um banco Access."
        )

    magic = header[4:19].decode("ascii", errors="replace")
    if magic not in ("Standard Jet DB", "Standard ACE DB"):
        return AccessFormat("formato não reconhecido", True, None)

    label, supported, reason = _FORMAT_BY_VERSION_BYTE.get(
        header[0x14],
        (f"formato desconhecido (0x{header[0x14]:02X})", True, None),
    )
    return AccessFormat(label, supported, reason)


# --------------------------------------------------------------------------- #
# Disponibilidade da biblioteca
# --------------------------------------------------------------------------- #


def _process_bits() -> int:
    return 64 if struct.calcsize("P") * 8 == 64 else 32


def mdb_import_error() -> str | None:
    """Motivo de a leitura de bancos estar indisponível, ou ``None``.

    O resultado é memoizado porque a interface consulta esta função a cada
    ``_sync_*_availability()`` e o import carrega o gerenciador de drivers.
    """

    global _import_checked, _import_error
    if _import_checked:
        return _import_error
    try:
        import pyodbc
    except Exception as exc:  # noqa: BLE001 — ImportError e falhas de carga
        _import_error = (
            f"A biblioteca {PACKAGE_NAME} não está disponível: {exc}. "
            f'Instale com: pip install "{PACKAGE_NAME}"'
        )
    else:
        try:
            drivers = tuple(pyodbc.drivers())
        except Exception as exc:  # noqa: BLE001
            _import_error = f"Não foi possível listar os drivers ODBC: {exc}"
        else:
            if any(DRIVER_KEYWORD in driver for driver in drivers):
                _import_error = None
            else:
                bits = _process_bits()
                _import_error = (
                    "Nenhum driver ODBC do Microsoft Access foi encontrado. "
                    f"Este Python é de {bits} bits e só enxerga drivers de "
                    f"{bits} bits — instale o Microsoft Access Database Engine "
                    f"Redistributable de {bits} bits."
                )
    _import_checked = True
    return _import_error


def mdb_available() -> bool:
    """``True`` quando um banco Access pode ser aberto."""

    return mdb_import_error() is None


def is_password_error(exc: BaseException) -> bool:
    """``True`` quando a exceção aparenta ser de senha ausente ou incorreta."""

    message = str(exc).casefold()
    return any(token in message for token in PASSWORD_ERROR_TOKENS)


def _select_driver() -> str:
    import pyodbc

    drivers = [driver for driver in pyodbc.drivers() if DRIVER_KEYWORD in driver]
    if not drivers:
        raise MdbEngineError(mdb_import_error() or "Driver ODBC indisponível.")
    return PREFERRED_DRIVER if PREFERRED_DRIVER in drivers else drivers[0]


def _reject_semicolon(value: str, *, field: str, quote: bool = True) -> None:
    """Recusa um valor que encerraria o atributo na cadeia de conexão.

    Como nenhum atributo do ACE aceita citação (ver :func:`connection_string`),
    um ``;`` no valor é literalmente inexprimível: ele seria lido como o fim do
    atributo. Recusar aqui troca uma conexão silenciosamente truncada por uma
    mensagem. ``quote=False`` mantém o valor fora da mensagem — é o que impede a
    senha de vazar para um traceback.
    """

    if ";" in value:
        detail = f": {value}" if quote else "."
        raise MdbEngineError(
            f"O valor de {field} não pode conter ';', que separa atributos na "
            f"cadeia de conexão ODBC{detail}"
        )


def connection_string(path: str | Path, password: str | None = None) -> str:
    """Monta a cadeia de conexão somente leitura.

    Três detalhes foram medidos contra o driver, não deduzidos:

    - ``ReadOnly=1`` é o atributo que o ACE de fato honra. ``Mode=Read`` **não
      serve** — é do OLEDB, e o ODBC responde "Atributo de cadeia de conexão
      inválido Mode";
    - **nenhum atributo do ACE aceita citação entre chaves.**
      ``DBQ={C:\\...\\rede.mdb}`` faz o driver tratar as chaves como parte do
      nome e responder "Nome de arquivo inválido (-1044)", e ``PWD={senha}`` faz
      a senha chegar ao driver com as chaves — rejeitada como incorreta, sempre,
      independentemente do que o usuário digitar;
    - a exceção aparente é o ``DRIVER={...}``, e ela confirma a regra: quem lê
      esse atributo é o **Gerenciador de Driver do Windows**, que segue o padrão
      ODBC e remove as chaves. ``DBQ`` e ``PWD`` são lidos pelo **driver do
      Access**, que faz a própria análise e não as remove.

    Logo, caminho e senha vão crus, e um ``;`` em qualquer um dos dois é
    recusado antes de chegar ao driver.
    """

    driver = _select_driver()
    location = str(Path(path))
    _reject_semicolon(location, field="DBQ (caminho do banco)")
    parts = [f"DRIVER={{{driver}}}", f"DBQ={location}", "ReadOnly=1"]
    if password:
        # Sem chaves, deliberadamente: ver o docstring. Há teste amarrando esta
        # linha à forma comprovadamente funcional.
        _reject_semicolon(password, field="PWD (senha)", quote=False)
        parts.append(f"PWD={password}")
    return ";".join(parts) + ";"


class _OdbcAccessDatabase:
    """Implementação de :class:`AccessDatabase` sobre uma conexão pyodbc."""

    __slots__ = ("_connection", "_path", "readonly_attribute")

    def __init__(self, connection: Any, path: Path, readonly_attribute: bool) -> None:
        self._connection = connection
        self._path = path
        # Registra se o SQL_MODE_READ_ONLY pôde ser aplicado. As outras três
        # camadas de proteção valem de qualquer forma.
        self.readonly_attribute = readonly_attribute

    def __repr__(self) -> str:  # pragma: no cover - conveniência de depuração
        # Sem senha, deliberadamente: este repr pode acabar num traceback.
        return f"<AccessDatabase {self._path.name}>"

    def tables(self) -> tuple[str, ...]:
        cursor = self._connection.cursor()
        try:
            names = [item.table_name for item in cursor.tables(tableType="TABLE")]
        finally:
            cursor.close()
        return tuple(
            sorted(name for name in names if not name.upper().startswith("MSYS"))
        )

    def columns(self, table: str) -> tuple[str, ...]:
        cursor = self._connection.cursor()
        try:
            names = [
                item.column_name
                for item in cursor.columns(table=table)
                if item.table_name == table
            ]
        except Exception:  # noqa: BLE001 — o catálogo do ACE às vezes falha
            names = []
        finally:
            cursor.close()
        if names:
            return tuple(names)
        # Recurso final: um SELECT vazio sempre descreve as colunas.
        cursor = self._connection.cursor()
        try:
            cursor.execute(f"SELECT * FROM {quote_identifier(table)} WHERE 1=0")
            return tuple(item[0] for item in cursor.description)
        finally:
            cursor.close()

    def row_count(self, table: str) -> int:
        cursor = self._connection.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}")
            return int(cursor.fetchone()[0])
        finally:
            cursor.close()

    def iter_rows(
        self,
        table: str,
        columns: Sequence[str],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> Iterator[tuple[str, ...]]:
        if not columns:
            raise MdbEngineError("Nenhuma coluna foi solicitada.")
        projection = ", ".join(quote_identifier(name) for name in columns)
        sql = f"SELECT {projection} FROM {quote_identifier(table)}"
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql)
            while True:
                batch = cursor.fetchmany(max(1, batch_size))
                if not batch:
                    return
                for row in batch:
                    yield tuple(cell_to_text(value) for value in row)
        finally:
            cursor.close()

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 — fechar não pode mascarar o erro real
            pass


def _connect(path: Path, password: str | None) -> _OdbcAccessDatabase:
    import pyodbc

    text = connection_string(path, password)
    try:
        connection = pyodbc.connect(text, readonly=True, autocommit=True)
    except Exception as exc:  # noqa: BLE001
        if is_password_error(exc):
            raise MdbPasswordError(
                "O banco é protegido por senha."
                if not password
                else "A senha informada não confere."
            ) from exc
        # ``readonly=True`` aplica SQL_MODE_READ_ONLY depois de conectar, e nem
        # todo driver aceita. As outras camadas bastam, então uma recusa aqui
        # não pode impedir a leitura.
        try:
            connection = pyodbc.connect(text, autocommit=True)
        except Exception as fallback_error:  # noqa: BLE001
            if is_password_error(fallback_error):
                raise MdbPasswordError(
                    "O banco é protegido por senha."
                    if not password
                    else "A senha informada não confere."
                ) from fallback_error
            raise MdbEngineError(
                f"Não foi possível abrir {path.name}: {fallback_error}"
            ) from fallback_error
        return _OdbcAccessDatabase(connection, path, readonly_attribute=False)
    return _OdbcAccessDatabase(connection, path, readonly_attribute=True)


@contextmanager
def open_database(
    path: str | Path,
    password: str | None = None,
) -> Iterator[AccessDatabase]:
    """Abre um banco Access somente para leitura e o fecha ao final.

    Levanta :class:`MdbPasswordError` quando o banco é protegido, para a
    interface poder pedir a senha em vez de repassar o erro do driver.
    """

    error = mdb_import_error()
    if error is not None:
        raise MdbEngineError(error)

    source = Path(path)
    if not source.is_file():
        raise MdbEngineError(f"Arquivo não encontrado: {source}")

    detected = sniff_access_format(source)
    if not detected.supported:
        raise MdbEngineError(f"{source.name}: {detected.reason}")

    database = _connect(source, password)
    try:
        yield database
    finally:
        database.close()
