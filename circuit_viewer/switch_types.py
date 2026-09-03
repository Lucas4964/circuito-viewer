"""Configuração externa de quais tipos de chave são manobráveis.

Módulo folha, no molde de :mod:`circuit_viewer.phase_config`: sem Qt, sem
``pyodbc``, sem dependências do pacote além do que precisa para ler um JSON.
Quem consome é o importador, que carimba a resposta em cada chave.

**Por que a decisão sai de um arquivo, e não do código.** O banco descreve o
*tipo* de cada chave (``TIPOCHAVE.TIPO``) mas não diz se ele é manobrável, e a
resposta não é derivável do cadastro sem embutir política. A coluna ``ELO``
chega perto — vale 1 nos três tipos de fusível e 0 nos outros catorze —, mas
erra nos dois sentidos: ``Jumper`` e ``Fly Tap`` são emenda permanente, não
dispositivo de manobra, e nada no banco os distingue de uma chave faca. Já a
coluna ``OPERACAO`` existe e está zerada até para ``Disjuntor``, o que a torna
inútil.

Fixar a lista no código transformaria uma decisão da distribuidora em release
do programa. Aqui ela é um arquivo que se edita, como o ``fases2.json``.

Um ``CODIGO`` ausente do arquivo não é erro: ele vira "sem relação", e a
interface mostra traço em vez de afirmar um 0 ou um 1 que ninguém declarou.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


CONFIG_FILENAME = "tipos_chave.json"


class SwitchTypeConfigurationError(ValueError):
    """Erro legível ao carregar ou validar ``tipos_chave.json``."""


@dataclass(frozen=True, slots=True)
class SwitchTypeEntry:
    """O que o arquivo declara sobre um tipo de chave.

    ``description`` é apenas para quem lê o arquivo: o nome exibido na interface
    vem do ``TIPOCHAVE.TIPO`` do banco, que é a fonte viva. Repeti-lo aqui como
    autoridade convidaria os dois a divergirem em silêncio.
    """

    code: str
    description: str
    switchable: bool


@dataclass(frozen=True, slots=True)
class SwitchTypeConfiguration:
    """As relações do arquivo, indexadas pelo ``CODIGO`` do tipo."""

    entries: tuple[SwitchTypeEntry, ...]
    _by_code: Mapping[str, SwitchTypeEntry] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        mapping = {entry.code: entry for entry in self.entries}
        if len(mapping) != len(self.entries):
            raise ValueError("A configuração contém CODIGO duplicado.")
        object.__setattr__(self, "_by_code", MappingProxyType(mapping))

    def entry_for(self, code: str) -> SwitchTypeEntry | None:
        """A relação de ``code``, ou ``None`` quando o arquivo não a declara."""

        return self._by_code.get(normalize_type_code(code))

    def switchable_text(self, code: str) -> str:
        """``"1"``, ``"0"`` ou ``""`` para tipo sem relação no arquivo.

        Texto porque é assim que o modelo guarda todos os campos de chave, e
        vazio em vez de um palpite: a interface troca isso por um traço, que diz
        "não declarado" sem afirmar nada.
        """

        entry = self.entry_for(code)
        if entry is None:
            return ""
        return "1" if entry.switchable else "0"


def normalize_type_code(value: object) -> str:
    """Forma canônica do ``CODIGO``: sem espaços nas pontas, em maiúsculas.

    O cadastro escreve ``CF``, e um arquivo editado à mão pode trazer ``cf`` ou
    ``" CF "``. Nenhuma das duas variações deveria custar a relação.
    """

    return str(value).strip().upper()


def default_switch_type_path() -> Path:
    return Path(__file__).resolve().parent / "config" / CONFIG_FILENAME


def load_switch_types(path: str | Path | None = None) -> SwitchTypeConfiguration:
    """Lê e valida o arquivo, levantando :class:`SwitchTypeConfigurationError`.

    A validação é a mesma disciplina de ``load_phase_configuration``: mensagem
    que diz qual relação está errada e por quê, porque quem edita o arquivo é
    uma pessoa e o erro precisa ser corrigível sem ler o código.
    """

    source = default_switch_type_path() if path is None else Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SwitchTypeConfigurationError(
            f"O arquivo {source} não está codificado em UTF-8."
        ) from exc
    except OSError as exc:
        raise SwitchTypeConfigurationError(
            f"Não foi possível ler {source}: {exc.strerror or exc}"
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SwitchTypeConfigurationError(
            f"JSON inválido em {source}, linha {exc.lineno}, coluna {exc.colno}."
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise SwitchTypeConfigurationError(
            f"A raiz de {CONFIG_FILENAME} deve ser uma lista não vazia."
        )

    entries: list[SwitchTypeEntry] = []
    seen: dict[str, int] = {}
    for row_number, raw_entry in enumerate(payload, start=1):
        if not isinstance(raw_entry, dict):
            raise SwitchTypeConfigurationError(
                f"Relação {row_number}: cada item deve ser um objeto JSON."
            )
        if "CODIGO" not in raw_entry or "MANOBRAVEL" not in raw_entry:
            raise SwitchTypeConfigurationError(
                f"Relação {row_number}: CODIGO e MANOBRAVEL são obrigatórios."
            )
        code = normalize_type_code(raw_entry["CODIGO"])
        if not code:
            raise SwitchTypeConfigurationError(
                f"Relação {row_number}: CODIGO não pode ser vazio."
            )
        if code in seen:
            raise SwitchTypeConfigurationError(
                f"Relação {row_number}: CODIGO duplicado; já aparece na relação "
                f"{seen[code]}."
            )
        switchable = raw_entry["MANOBRAVEL"]
        # bool é subclasse de int em Python: sem a checagem, ``true`` passaria
        # como 1 e o arquivo teria duas grafias para a mesma coisa.
        if isinstance(switchable, bool) or switchable not in {0, 1}:
            raise SwitchTypeConfigurationError(
                f"Relação {row_number}: MANOBRAVEL deve ser 0 ou 1 "
                f"(recebido {switchable!r})."
            )
        seen[code] = row_number
        entries.append(
            SwitchTypeEntry(
                code=code,
                description=str(raw_entry.get("TIPO", "")).strip(),
                switchable=switchable == 1,
            )
        )

    try:
        return SwitchTypeConfiguration(tuple(entries))
    except ValueError as exc:  # pragma: no cover - duplicatas já barradas acima
        raise SwitchTypeConfigurationError(str(exc)) from exc


__all__ = [
    "CONFIG_FILENAME",
    "SwitchTypeConfiguration",
    "SwitchTypeConfigurationError",
    "SwitchTypeEntry",
    "default_switch_type_path",
    "load_switch_types",
    "normalize_type_code",
]
