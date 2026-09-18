"""Linhas, resumo e exportação CSV da seleção a jusante.

Sem Qt, no molde de ``block_table`` e ``branch_table_export``: a conversão de
uma :class:`~circuit_viewer.downstream_selection.DownstreamSelection` em linhas
mora aqui, para ser testável sem interface e para a janela não ser a dona da
verdade sobre o que a tabela e o CSV mostram.

**O CSV leva todas as colunas da tabela**, com as quatro da estrutura original
primeiro e na mesma ordem — ``OBJ_ID;BARRA_1;BARRA_2;TIPO`` — e as demais depois,
para quem já lê o arquivo pela posição delas não quebrar. Dois cuidados sobre
esse formato:

- **``OBJ_ID`` não é único entre tipos.** Não existe ``OBJ_ID`` no esquema: cada
  entidade traz o seu (``TRECHO_ID``, ``CHAVE_ID``, ``BARRA_ID``, ``CARGA_ID``,
  ``CAPAC_ID``, ``GERADOR_ID``, ``REGU_ID``), e um trecho 5 e uma chave 5
  coexistem. **A chave de uma linha é o par ``(TIPO, OBJ_ID)``** — é o que torna
  as quatro colunas suficientes.
- **``BARRA_2`` fica vazia** para o que pende de uma barra só (carga, capacitor,
  gerador) e para a própria barra. Vazia, e não zero nem repetição de
  ``BARRA_1``.

**``BARRA_1`` é a de montante.** Para trecho, chave e regulador, as barras saem
na ordem do fluxo — montante, depois jusante —, e não na ordem do cadastro. É a
informação que a orientação acrescenta e que o cadastro não tem: ``BARRA1_ID``
e ``BARRA2_ID`` do banco não dizem de que lado vem a energia. Onde não há
orientação (uma corda interna, com as duas pontas no mesmo nível), vale a ordem
do cadastro.

**Uma chave aparece duas vezes**, e isso é fiel ao banco: o trecho que ela ocupa
é uma linha ``TRECHO`` (com o ``TRECHO_ID``) e a chave é uma linha ``CHAVE``
(com o ``CHAVE_ID``). O mesmo vale para o regulador. Filtrar "só trechos" traz
todo trecho a jusante, com ou sem equipamento — que é o objetivo declarado da
ferramenta.

Delimitador ``;``, BOM UTF-8 e ``\\r\\n``: a convenção do projeto para abrir no
Excel em pt-BR, igual à dos ramais.

**``INFO`` pode ter mais de uma parte** — a chave traz estado e tipo, a carga
traz SNOM e UC. Na tela as partes se juntam com `` · ``; no CSV, com **``|``**,
sem espaços, para uma célula poder ser dividida depois (``split("|")``, ou no
Excel *Texto para colunas → Outro: |*). ``|`` porque não é ``;`` (colunas) nem
``,`` (decimal pt-BR), e não aparece em código, estado, fase ou tipo de chave. A
regra vale para todo ``INFO``, então um único separador serve ao arquivo inteiro.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import csv
from dataclasses import dataclass
import io
import math
import os
from pathlib import Path
import tempfile

import numpy as np

from .block_analysis import parse_power
from .branch_table_export import format_pt_br
from .downstream_selection import DownstreamSelection
from .dss_names import sanitize_dss_name
from .model import IndexArray
from .phase_config import PhaseConfiguration


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]

TRECHO = "TRECHO"
CHAVE = "CHAVE"
BARRA = "BARRA"
CARGA = "CARGA"
CAPACITOR = "CAPACITOR"
GERADOR = "GERADOR"
REGULADOR = "REGULADOR"

#: Ordem dos tipos na tabela, nos filtros e no CSV.
DOWNSTREAM_KINDS = (TRECHO, CHAVE, BARRA, CARGA, CAPACITOR, GERADOR, REGULADOR)

#: Rótulo de cada tipo nos filtros da janela.
DOWNSTREAM_KIND_LABELS = {
    TRECHO: "Trechos",
    CHAVE: "Chaves",
    BARRA: "Barras",
    CARGA: "Cargas",
    CAPACITOR: "Capacitores",
    GERADOR: "Geradores",
    REGULADOR: "Reguladores",
}

#: Os tipos que o destaque no mapa sabe desenhar: chave é trecho no modelo.
HIGHLIGHTABLE_KINDS = frozenset({TRECHO, CHAVE, REGULADOR})

DOWNSTREAM_TABLE_HEADERS = (
    "TIPO",
    "OBJ_ID",
    "CODIGO",
    "BARRA_1",
    "BARRA_2",
    "NIVEL",
    "COMPR",
    "INFO",
)
DOWNSTREAM_NUMERIC_COLUMNS = frozenset(
    DOWNSTREAM_TABLE_HEADERS.index(name) for name in ("NIVEL", "COMPR")
)
DOWNSTREAM_LEVEL_COLUMN = DOWNSTREAM_TABLE_HEADERS.index("NIVEL")
DOWNSTREAM_LENGTH_COLUMN = DOWNSTREAM_TABLE_HEADERS.index("COMPR")

#: Todas as colunas da tabela, com as quatro originais primeiro e na ordem
#: original: quem lê o arquivo pela posição delas continua funcionando.
DOWNSTREAM_CSV_HEADERS = (
    "OBJ_ID",
    "BARRA_1",
    "BARRA_2",
    "TIPO",
    "CODIGO",
    "NIVEL",
    "COMPR",
    "INFO",
)

#: Como as partes de ``INFO`` se juntam na tela.
INFO_DISPLAY_SEPARATOR = " · "
#: Como elas se juntam no CSV: sem espaço, para ``split`` devolver as partes
#: limpas.
INFO_CSV_SEPARATOR = "|"


@dataclass(frozen=True, slots=True)
class DownstreamRow:
    """Um elemento da região: o tipo, o índice no modelo dele e o que se exibe."""

    kind: str
    index: int
    obj_id: str
    code: str
    bar_1: str
    bar_2: str
    level: int
    length: float | None
    info_parts: tuple[str, ...]

    @property
    def info(self) -> str:
        """``INFO`` como a tela mostra."""

        return INFO_DISPLAY_SEPARATOR.join(self.info_parts)

    @property
    def csv_info(self) -> str:
        """``INFO`` como o CSV grava, com partes separáveis."""

        return INFO_CSV_SEPARATOR.join(self.info_parts)

    @property
    def segment_index(self) -> int | None:
        """O trecho que o elemento ocupa, quando ocupa um."""

        return self.index if self.kind == TRECHO else None


@dataclass(frozen=True, slots=True)
class DownstreamSummary:
    """Os totais da região, para o cabeçalho da janela."""

    counts: dict[str, int]
    total_length: float | None
    missing_length_count: int
    total_power: float | None
    consumer_count: int
    unknown_consumer_load_count: int


@dataclass(frozen=True, slots=True)
class DownstreamCsvExportResult:
    path: Path
    row_count: int


# -- linhas ------------------------------------------------------------------


def downstream_rows(
    selection: DownstreamSelection,
    kinds: Iterable[str] | None = None,
    *,
    phase_configuration: PhaseConfiguration | None = None,
) -> tuple[DownstreamRow, ...]:
    """As linhas da região, dos tipos pedidos, na ordem de :data:`DOWNSTREAM_KINDS`.

    Dentro de um tipo, as linhas saem por nível — do mais perto da origem para o
    mais longe —, que é a ordem em que o operador lê uma região.

    Com ``phase_configuration``, o ``INFO`` do trecho mostra o ``NOME`` da fase
    (``DEF``) em vez do código FASES2 (``13``). Sem ela, ou com um código sem
    relação no ``fases2.json``, fica o código — a informação nunca some.
    """

    def phase_name(value: str) -> str:
        name = (
            None
            if phase_configuration is None
            else phase_configuration.name_for_value(value)
        )
        return name or value.strip()

    wanted = set(DOWNSTREAM_KINDS if kinds is None else kinds)
    unknown = wanted - set(DOWNSTREAM_KINDS)
    if unknown:
        raise ValueError(f"Tipo desconhecido: {', '.join(sorted(unknown))}")

    segments = selection.segments
    bars = segments.bars
    level_by_bar = selection.level_by_bar()
    segment_level = dict(
        zip(
            selection.segment_indices.tolist(),
            selection.segment_levels.tolist(),
            strict=True,
        )
    )

    def oriented_bars(segment: int) -> tuple[str, str]:
        start = int(segments.start_indices[segment])
        end = int(segments.end_indices[segment])
        # Fora da região conta como -1: é o lado de montante do trecho de origem.
        if level_by_bar.get(end, -1) < level_by_bar.get(start, -1):
            start, end = end, start
        return bars.bar_ids[start], bars.bar_ids[end]

    rows: list[DownstreamRow] = []

    if TRECHO in wanted:
        lengths = segments.lengths
        for segment in selection.segment_indices.tolist():
            first, second = oriented_bars(segment)
            length = float(lengths[segment])
            rows.append(
                DownstreamRow(
                    TRECHO,
                    segment,
                    segments.segment_ids[segment],
                    segments.codes[segment],
                    first,
                    second,
                    segment_level[segment],
                    None if math.isnan(length) else length,
                    _parts(phase_name(segments.phases[segment])),
                )
            )

    switches = selection.switches
    if CHAVE in wanted and switches is not None:
        for record in selection.switch_indices.tolist():
            segment = int(switches.segment_indices[record])
            first, second = oriented_bars(segment)
            details = switches.record(record)
            rows.append(
                DownstreamRow(
                    CHAVE,
                    record,
                    switches.switch_ids[record],
                    switches.codes[record],
                    first,
                    second,
                    segment_level.get(segment, 0),
                    None,
                    _switch_info(details.state, details.type_name),
                )
            )

    if BARRA in wanted:
        for bar, level in zip(
            selection.bar_indices.tolist(),
            selection.bar_levels.tolist(),
            strict=True,
        ):
            rows.append(
                DownstreamRow(
                    BARRA,
                    bar,
                    bars.bar_ids[bar],
                    bars.codes[bar],
                    bars.bar_ids[bar],
                    "",
                    level,
                    None,
                    _parts("Origem" if bar == selection.start_bar_index else ""),
                )
            )

    loads = selection.loads
    if CARGA in wanted and loads is not None:
        for load in selection.load_indices.tolist():
            bar = int(loads.bar_indices[load])
            consumers = loads.consumer_counts[load]
            snom = loads.snom_values[load].strip()
            info = _parts(
                f"SNOM {snom}" if snom else "",
                "" if consumers is None else f"{consumers:n} UC",
            )
            rows.append(
                DownstreamRow(
                    CARGA,
                    load,
                    loads.load_ids[load],
                    loads.codes[load],
                    bars.bar_ids[bar],
                    "",
                    level_by_bar.get(bar, 0),
                    None,
                    info,
                )
            )

    capacitors = selection.capacitors
    if CAPACITOR in wanted and capacitors is not None:
        for capacitor in selection.capacitor_indices.tolist():
            bar = int(capacitors.bar_indices[capacitor])
            rows.append(
                DownstreamRow(
                    CAPACITOR,
                    capacitor,
                    capacitors.capacitor_ids[capacitor],
                    capacitors.codes[capacitor],
                    bars.bar_ids[bar],
                    "",
                    level_by_bar.get(bar, 0),
                    None,
                    _parts(capacitors.phases[capacitor]),
                )
            )

    generators = selection.generators
    if GERADOR in wanted and generators is not None:
        for generator in selection.generator_indices.tolist():
            bar = int(generators.bar_indices[generator])
            rows.append(
                DownstreamRow(
                    GERADOR,
                    generator,
                    generators.generator_ids[generator],
                    generators.generator_codes[generator],
                    bars.bar_ids[bar],
                    "",
                    level_by_bar.get(bar, 0),
                    None,
                    (),
                )
            )

    regulators = selection.regulators
    if REGULADOR in wanted and regulators is not None:
        for regulator in selection.regulator_indices.tolist():
            segment = int(regulators.segment_indices[regulator])
            first, second = oriented_bars(segment)
            snom = regulators.snom_values[regulator].strip()
            rows.append(
                DownstreamRow(
                    REGULADOR,
                    regulator,
                    regulators.regulator_ids[regulator],
                    regulators.codes[regulator],
                    first,
                    second,
                    segment_level.get(segment, 0),
                    None,
                    _parts(f"SNOM {snom}" if snom else ""),
                )
            )

    order = {kind: position for position, kind in enumerate(DOWNSTREAM_KINDS)}
    rows.sort(key=lambda row: (order[row.kind], row.level))
    return tuple(rows)


def _switch_info(state: str, type_name: str) -> tuple[str, ...]:
    text = state.strip()
    label = {"1": "Fechada", "0": "Aberta"}.get(text, f"ESTADO {text or '?'}")
    return _parts(label, type_name)


def _parts(*values: str) -> tuple[str, ...]:
    """As partes não vazias de um ``INFO``, sem espaço nas pontas."""

    return tuple(text for value in values if (text := str(value).strip()))


def downstream_table_values(row: DownstreamRow) -> tuple[object | None, ...]:
    """Os valores brutos de uma linha, na ordem de :data:`DOWNSTREAM_TABLE_HEADERS`.

    Brutos de propósito: número continua número, para a ordenação da tabela
    comparar grandeza e não texto. Quem formata é a janela.
    """

    return (
        row.kind,
        row.obj_id,
        row.code,
        row.bar_1,
        row.bar_2,
        row.level,
        row.length,
        row.info,
    )


def downstream_csv_values(row: DownstreamRow) -> tuple[str, ...]:
    """Uma linha do CSV, na ordem de :data:`DOWNSTREAM_CSV_HEADERS`."""

    return (
        row.obj_id,
        row.bar_1,
        row.bar_2,
        row.kind,
        row.code,
        format_pt_br(row.level),
        format_pt_br(row.length),
        row.csv_info,
    )


# -- resumo e destaque ---------------------------------------------------------


def downstream_summary(selection: DownstreamSelection) -> DownstreamSummary:
    """Contagens por tipo, comprimento, SNOM e unidades consumidoras da região.

    Comprimento e SNOM distinguem "zero" de "sem resposta" como os blocos
    fazem, e as UC somam só o que o cadastro sabe: uma carga sem contagem não
    vira zero, vira uma carga a mais em ``unknown_consumer_load_count``.
    """

    counts = {
        TRECHO: int(selection.segment_indices.size),
        CHAVE: int(selection.switch_indices.size),
        BARRA: int(selection.bar_indices.size),
        CARGA: int(selection.load_indices.size),
        CAPACITOR: int(selection.capacitor_indices.size),
        GERADOR: int(selection.generator_indices.size),
        REGULADOR: int(selection.regulator_indices.size),
    }

    lengths = selection.segments.lengths[selection.segment_indices]
    known = lengths[~np.isnan(lengths)]
    total_length = float(known.sum()) if known.size else None
    missing = int(lengths.size - known.size)

    powers: list[float] = []
    consumers = 0
    unknown_consumers = 0
    loads = selection.loads
    if loads is not None:
        for load in selection.load_indices.tolist():
            power = parse_power(loads.snom_values[load])
            if power is not None:
                powers.append(power)
            count = loads.consumer_counts[load]
            if count is None:
                unknown_consumers += 1
            else:
                consumers += int(count)

    return DownstreamSummary(
        counts=counts,
        total_length=total_length,
        missing_length_count=missing,
        total_power=float(sum(powers)) if powers else None,
        consumer_count=consumers,
        unknown_consumer_load_count=unknown_consumers,
    )


def highlight_segment_indices(
    selection: DownstreamSelection,
    kinds: Iterable[str],
) -> IndexArray:
    """Os trechos a destacar no mapa para os tipos marcados.

    O destaque é de trechos: "Trechos" acende a região inteira, "Chaves" e
    "Reguladores" acendem só os trechos que os carregam. Os demais tipos não
    têm o que acender, e aparecem na tabela.
    """

    wanted = set(kinds)
    parts: list[np.ndarray] = []
    if TRECHO in wanted:
        parts.append(selection.segment_indices)
    switches = selection.switches
    if CHAVE in wanted and switches is not None and selection.switch_indices.size:
        parts.append(switches.segment_indices[selection.switch_indices])
    regulators = selection.regulators
    if (
        REGULADOR in wanted
        and regulators is not None
        and selection.regulator_indices.size
    ):
        parts.append(regulators.segment_indices[selection.regulator_indices])
    if not parts:
        values = np.empty(0, dtype=np.intp)
    else:
        values = np.unique(np.concatenate(parts)).astype(np.intp, copy=False)
    values.setflags(write=False)
    return values


# -- CSV -----------------------------------------------------------------------


def suggested_downstream_csv_filename(selection: DownstreamSelection) -> str:
    segments = selection.segments
    origin = selection.origin
    if origin.kind == "segment":
        label = segments.segment_ids[origin.index]
    else:
        label = segments.bars.bar_ids[origin.index]
    normalized = sanitize_dss_name(label) or "origem"
    return f"jusante_{normalized}.csv"


def build_downstream_csv_bytes(
    rows: Sequence[DownstreamRow],
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> bytes:
    """Serializa na ordem recebida, sem depender do modelo Qt."""

    keys = [(row.kind, row.obj_id) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("A seleção contém o mesmo elemento duas vezes.")
    cancelled = cancel_check or (lambda: False)
    stream = io.StringIO(newline="")
    writer = csv.writer(
        stream,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writerow(DOWNSTREAM_CSV_HEADERS)
    total = len(rows)
    for position, row in enumerate(rows, start=1):
        if cancelled():
            raise InterruptedError("Exportação CSV da seleção a jusante cancelada.")
        writer.writerow(downstream_csv_values(row))
        if progress is not None:
            progress(position, total)
    if cancelled():
        raise InterruptedError("Exportação CSV da seleção a jusante cancelada.")
    return ("﻿" + stream.getvalue()).encode("utf-8")


def export_downstream_csv(
    path: str | Path,
    rows: Sequence[DownstreamRow],
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> DownstreamCsvExportResult:
    """Gera o CSV por completo e só então substitui o destino atomicamente."""

    cancelled = cancel_check or (lambda: False)
    content = build_downstream_csv_bytes(
        rows, cancel_check=cancelled, progress=progress
    )
    if cancelled():
        raise InterruptedError("Exportação CSV da seleção a jusante cancelada.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise InterruptedError("Exportação CSV da seleção a jusante cancelada.")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return DownstreamCsvExportResult(target, len(rows))
