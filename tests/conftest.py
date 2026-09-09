"""Um único QApplication vivo durante toda a regressão de interface.

Os TestCase antigos guardavam o aplicativo em cada instância de teste. Ao
coletar uma instância, o Qt podia destruir o QApplication enquanto widgets de
outros testes ainda existiam (falha nativa em sender/findChildren). A aplicação
real já possui esse proprietário único; a suíte deve reproduzir seu ciclo.
"""
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def application_lifetime():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        yield None
        return
    application = QApplication.instance() or QApplication([])
    yield application
