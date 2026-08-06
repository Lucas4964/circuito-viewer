"""Acesso isolado à DLL do OpenDSS, via ``py_dss_interface``.

Este é o **único** módulo do pacote que conhece a biblioteca. A separação não é
organizacional: ``py_dss_interface`` é uma dependência opcional e carrega três
efeitos globais ao processo que precisam de contenção em um lugar só.

1. ``DSS.__init__`` chama ``os.chdir`` para a pasta da DLL — o diretório
   corrente da aplicação inteira muda como efeito colateral de instanciar o
   motor. :func:`acquire_engine` salva e restaura o diretório.
2. Se a DLL não iniciar, a biblioteca imprime e chama ``exit()``. ``SystemExit``
   deriva de ``BaseException``, então passaria batido pelo ``except Exception``
   dos workers e derrubaria a thread sem mensagem. A inicialização aqui captura
   ``BaseException`` e traduz para :class:`PowerFlowEngineError`.
3. A DLL é global ao processo: dois objetos ``DSS`` compartilham o mesmo estado
   nativo. Daí o motor ser um **singleton** protegido por ``threading.Lock``,
   e não um objeto criado por execução.

Há ainda uma restrição de codificação: ``dss.text()`` codifica o comando em
ASCII, então qualquer caminho passado ao OpenDSS precisa ser ASCII puro — o que
:func:`ascii_workspace` garante.

Nada aqui importa Qt, e o import de ``py_dss_interface`` é tardio: importar este
módulo é sempre seguro, mesmo sem a biblioteca instalada.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol


WORKSPACE_PREFIX = "circuit_viewer_dss_"
PACKAGE_NAME = "py-dss-interface"

# Uma única trava para tudo: a DLL não distingue "criar o motor" de "usar o
# motor", então serializar as duas coisas com a mesma trava é o que impede duas
# threads de compilarem circuitos diferentes sobre o mesmo estado nativo.
_lock = threading.Lock()
_engine: Any | None = None
_import_error: str | None = None
_import_checked = False


class PowerFlowEngineError(RuntimeError):
    """Falha ao preparar ou usar o motor do OpenDSS."""


class DssEngine(Protocol):
    """Superfície do ``py_dss_interface`` que o núcleo do fluxo consome.

    Existe para o núcleo poder ser testado com um motor falso: tudo o que
    :func:`circuit_viewer.opendss_powerflow.run_power_flow` precisa está aqui, e
    nada mais.
    """

    circuit: Any
    lines: Any
    cktelement: Any
    solution: Any
    # Os reguladores são Transformer no modelo exportado, e o tap resolvido só
    # sai por esta coleção — o laço de ``lines`` nunca os alcança.
    transformers: Any

    def text(self, command: str) -> str:
        """Envia um comando à interface de texto do OpenDSS."""


def power_flow_import_error() -> str | None:
    """Motivo de o motor não estar disponível, ou ``None`` se estiver.

    O resultado é memoizado porque importar ``py_dss_interface`` carrega a DLL
    e é caro demais para repetir a cada ``_sync_*_availability()`` da interface.
    """

    global _import_checked, _import_error
    if _import_checked:
        return _import_error
    try:
        import py_dss_interface  # noqa: F401
    except Exception as exc:  # ImportError, mas também falhas de carga da DLL
        _import_error = (
            f"A biblioteca {PACKAGE_NAME} não está disponível: {exc}. "
            f'Instale com: pip install "{PACKAGE_NAME}"'
        )
    else:
        _import_error = None
    _import_checked = True
    return _import_error


def power_flow_available() -> bool:
    """``True`` quando o fluxo de potência pode ser executado."""

    return power_flow_import_error() is None


def _create_engine() -> Any:
    """Instancia o ``DSS``, contendo os efeitos globais do construtor."""

    error = power_flow_import_error()
    if error is not None:
        raise PowerFlowEngineError(error)
    from py_dss_interface import DSS  # import tardio: ver o docstring do módulo

    working_directory = os.getcwd()
    try:
        engine = DSS()
    except BaseException as exc:  # noqa: BLE001 — inclui o exit() da biblioteca
        raise PowerFlowEngineError(
            f"Não foi possível iniciar o OpenDSS: {exc or type(exc).__name__}"
        ) from exc
    finally:
        # O construtor faz os.chdir para a pasta da DLL; sem isto, todo diálogo
        # de arquivo da aplicação passaria a abrir lá.
        os.chdir(working_directory)
    if not getattr(engine, "started", False):
        raise PowerFlowEngineError(
            "O OpenDSS não iniciou; verifique a instalação de "
            f"{PACKAGE_NAME}."
        )
    return engine


@contextmanager
def acquire_engine() -> Iterator[Any]:
    """Empresta o motor único, com exclusão mútua e diretório preservado.

    O ``Clear`` de entrada é deliberado: o motor é reusado entre execuções e
    carregaria o circuito da execução anterior.
    """

    with _lock:
        global _engine
        working_directory = os.getcwd()
        try:
            if _engine is None:
                _engine = _create_engine()
            _engine.text("Clear")
            yield _engine
        finally:
            # Compile muda o diretório do processo para a pasta do master.
            os.chdir(working_directory)


def _ascii_temp_roots() -> tuple[Path, ...]:
    """Candidatos a raiz do diretório de trabalho, do mais ao menos usual."""

    candidates = [Path(tempfile.gettempdir())]
    system_drive = os.environ.get("SystemDrive")
    if system_drive:
        candidates.append(Path(f"{system_drive}\\Temp"))
    candidates.append(Path.home() / ".circuit_viewer_temp")
    return tuple(candidates)


def _is_ascii(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


@contextmanager
def ascii_workspace() -> Iterator[Path]:
    """Cria e remove um diretório temporário de caminho garantidamente ASCII.

    ``dss.text()`` codifica o comando em ASCII, então um ``Compile`` apontando
    para um caminho acentuado — o que acontece sempre que o nome de usuário do
    Windows tem acento, porque o ``TEMP`` fica sob ele — levantaria
    ``UnicodeEncodeError`` no meio da execução. Testar antes e cair para outra
    raiz troca uma falha obscura por uma mensagem acionável.
    """

    failures: list[str] = []
    for root in _ascii_temp_roots():
        if not _is_ascii(root):
            failures.append(f"{root}: caminho com caracteres não ASCII")
            continue
        try:
            root.mkdir(parents=True, exist_ok=True)
            workspace = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=root))
        except OSError as exc:
            failures.append(f"{root}: {exc.strerror or exc}")
            continue
        try:
            yield workspace
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        return
    raise PowerFlowEngineError(
        "Nenhuma pasta temporária utilizável: o OpenDSS só aceita caminhos "
        "ASCII. Tentativas: " + "; ".join(failures)
    )
