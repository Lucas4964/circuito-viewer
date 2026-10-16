"""Unidades consumidoras e GD de cada transformador, para o modo de cargas Energia.

Camada de núcleo: não importa Qt nem toca o banco. Quem lê as tabelas é
``mdb_import``; aqui chegam as linhas já projetadas, como dicionários de texto.

**Um trafo é um ``CARGA_ID``**, o ponto de conexão na média tensão. Os vínculos
são os do Interplan, os mesmos de ``consumer_counts``::

    BT_CONS.ET_ID       → BT_ET.ID → BT_ET.MT_CAR_ID → CARGA.CARGA_ID
    BT_IP.ET_ID         → (o mesmo caminho)
    MT_CONS.CARGA_ID    → CARGA.CARGA_ID          (EP: consumidor da MT)
    BT_GERADOR_CONS.BT_CONS_ID → BT_CONS;  MT_GERADOR_CONS.MT_CONS_ID → MT_CONS

Uma ET e um EP na mesma CARGA são o **mesmo trafo físico** (na base de
referência, a CARGA 17495 tem a ET e o EP com o mesmo ``CODIGO``): viram um trafo
só, com as UCs das duas origens no mesmo secundário. É o que o ``calcular_dg``
separava em dois.

**De onde vem cada dado do trafo.** ``BT_UTC`` é a ficha técnica do trafo da ET
— kVA, tensões e fases —, sem carga nenhuma. O EP não tem UTC: o kVA e a tensão
do secundário vêm da própria ``CARGA`` (``SNOM`` e ``VLINHASEC``). Na CARGA com
ET e EP vale a UTC, e a divergência entra no relatório.

**A carga da CARGA não entra aqui.** ``MODELO_CARGA`` é a demanda que o Interplan
já agregou naquele trafo; no modo Energia ela é substituída pelas UCs, nunca
somada a elas.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from .model import LoadModel, StaticPointIndex
from .opendss_export import parse_number


CONSUMER_KIND_BT = "BT"
CONSUMER_KIND_MT = "MT"
CONSUMER_KIND_IP = "IP"

ORIGIN_ET = "ET"
ORIGIN_EP = "EP"
ORIGIN_ET_EP = "ET+EP"

# Colunas obrigatórias de cada tabela; as opcionais vêm vazias quando faltam.
TRANSFORMER_COLUMNS = ("ID", "MT_CAR_ID")
TRANSFORMER_OPTIONAL_COLUMNS = ("CODIGO",)
TRANSFORMER_UNIT_COLUMNS = ("ET_ID", "SNOM", "VPRIMARIO", "VSECUNDA", "FASES_BT2")
TRANSFORMER_UNIT_OPTIONAL_COLUMNS = ("FASES_MT2",)
BT_CONSUMER_COLUMNS = ("ID", "ET_ID", "CONSUMO", "FASES2", "CLASSE")
BT_CONSUMER_OPTIONAL_COLUMNS = ("PS_ID", "CODIGO", "NOME")
MT_CONSUMER_COLUMNS = ("ID", "CARGA_ID", "CONSUMO", "FASES2", "CLASSE")
MT_CONSUMER_OPTIONAL_COLUMNS = ("CODIGO", "NOME", "ATIVO")
PUBLIC_LIGHTING_COLUMNS = ("ID", "ET_ID", "DEMANDA", "FASES2")
PUBLIC_LIGHTING_OPTIONAL_COLUMNS = ("PS_ID", "CODIGO", "NLAMP")
BT_GENERATOR_COLUMNS = ("GERADOR_ID", "BT_CONS_ID", "GERACAO_KWH")
MT_GENERATOR_COLUMNS = ("GERADOR_ID", "MT_CONS_ID", "GERACAO_KWH")
GENERATOR_OPTIONAL_COLUMNS = ("CODIGO", "SNOM", "TIPOGD", "CURVA_ID")

MAX_REPORTED_ISSUES = 200

Row = Mapping[str, str]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class TransformerSpec:
    """O trafo de uma CARGA: de onde veio e os dados elétricos conhecidos.

    ``primary_kv`` é o ``VPRIMARIO`` da UTC — fase-neutro no trafo monofásico,
    de linha no trifásico —, ``None`` no EP, que usa a ``VNOM`` do circuito.
    ``secondary_v`` é a tensão de linha do secundário, em volts.
    ``secondary_phases`` é o ``FASES_BT2`` da UTC, vazio no EP.
    """

    origin: str
    codes: tuple[str, ...] = ()
    kva: float | None = None
    primary_kv: float | None = None
    secondary_v: float | None = None
    secondary_phases: str = ""


@dataclass(frozen=True, slots=True)
class ConsumerRecord:
    """Uma carga do secundário: UC da BT, UC da MT (EP) ou ponto de IP.

    ``consumption_kwh`` é o ``CONSUMO`` mensal das UCs; ``demand_kw`` e
    ``lamp_count`` são a ``DEMANDA`` e o ``NLAMP`` do ponto de IP. O
    ``service_point_id`` (``PS_ID``) é o que o sorteio de curvas do Interplan
    usa para reiniciar a semente.
    """

    kind: str
    consumer_id: str
    code: str = ""
    name: str = ""
    consumer_class: int | None = None
    phases: str = ""
    consumption_kwh: float | None = None
    demand_kw: float | None = None
    lamp_count: int | None = None
    active: bool = True
    service_point_id: str = ""


@dataclass(frozen=True, slots=True)
class GenerationRecord:
    """Um gerador distribuído pendurado numa UC da BT ou da MT.

    ``curve_id`` é o ``CURVA_ID`` do cadastro; vazio ou ``0`` significa que a
    curva é escolhida pelo ``TIPOGD`` (``generation_type``).
    """

    kind: str
    generator_id: str
    consumer_id: str
    code: str = ""
    generation_type: int | None = None
    curve_id: str = ""
    generation_kwh: float | None = None
    snom_kva: float | None = None


@dataclass(frozen=True, slots=True)
class TransformerConsumersRecord:
    """Tudo o que está pendurado numa CARGA, na mesma posição de ``LoadModel``.

    ``transformer`` ``None`` quer dizer que não há ET nem EP para a CARGA — ou
    que a fonte não trouxe os dados —, e ela segue no modelo do Interplan.
    """

    load_id: str
    transformer: TransformerSpec | None = None
    consumers: tuple[ConsumerRecord, ...] = ()
    generators: tuple[GenerationRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class ConsumerDataIssue:
    """Dado recusado, opcionalmente atribuível a uma CARGA."""

    source: str
    reason: str
    load_id: str | None = None

    @property
    def description(self) -> str:
        return f"{self.source}: {self.reason}"


@dataclass(frozen=True, slots=True)
class TransformerConsumersModel:
    """Retrato imutável das UCs e da GD de todas as cargas."""

    loads: LoadModel
    records: tuple[TransformerConsumersRecord, ...]
    issues: tuple[ConsumerDataIssue, ...] = ()
    omitted_issues: int = 0
    source_path: str | None = None

    def __post_init__(self) -> None:
        if len(self.records) != len(self.loads):
            raise ValueError("Cada carga deve possuir um registro de consumidores.")
        if any(
            record.load_id != self.loads.load_ids[index]
            for index, record in enumerate(self.records)
        ):
            raise ValueError("Os consumidores não correspondem à ordem das cargas.")

    def record(self, load_index: int) -> TransformerConsumersRecord:
        if not 0 <= int(load_index) < len(self.records):
            raise IndexError(load_index)
        return self.records[int(load_index)]

    @property
    def consumer_count(self) -> int:
        return sum(len(record.consumers) for record in self.records)

    @property
    def generator_count(self) -> int:
        return sum(len(record.generators) for record in self.records)

    def issues_for_loads(
        self,
        load_ids: Iterable[str],
        *,
        include_unattributed: bool = False,
    ) -> tuple[ConsumerDataIssue, ...]:
        selected = frozenset(str(value) for value in load_ids)
        return tuple(
            issue
            for issue in self.issues
            if issue.load_id in selected
            or (include_unattributed and issue.load_id is None)
        )


@dataclass(frozen=True, slots=True)
class TransformerGenerationRecord:
    """O agregado de GD de uma CARGA, como o símbolo do mapa o representa."""

    load_id: str
    generator_count: int
    generation_kwh: float
    # Lido pela camada de símbolos para compor a dica, como nas cargas equivalentes.
    origin_kind: str = "transformer_generation"


class TransformerGenerationModel:
    """Um símbolo de gerador por CARGA com GD: o agregado do modo Energia.

    Segue o contrato que a camada de símbolos usa do ``GeneratorModel`` —
    ``bars``, ``bar_indices``, ``spatial_index``, ``generator_ids``, ``record`` e
    ``len`` —, então o desenho, a seleção e o leiaute por barra são os mesmos.
    ``load_indices`` liga cada símbolo à sua CARGA, que é onde estão os
    geradores de fato.
    """

    __slots__ = (
        "consumers",
        "bars",
        "_load_indices",
        "_bar_indices",
        "_ids",
        "_records",
        "_spatial_index",
    )

    def __init__(self, consumers: TransformerConsumersModel) -> None:
        loads = consumers.loads
        indices = [
            index for index, record in enumerate(consumers.records) if record.generators
        ]
        load_indices = np.asarray(indices, dtype=np.intp)
        bar_indices = np.ascontiguousarray(
            np.asarray(loads.bar_indices, dtype=np.intp)[load_indices], dtype=np.intp
        )
        load_indices.setflags(write=False)
        bar_indices.setflags(write=False)
        self.consumers = consumers
        self.bars = loads.bars
        self._load_indices = load_indices
        self._bar_indices = bar_indices
        self._ids = tuple(
            f"GD-{loads.codes[index] or loads.load_ids[index]}" for index in indices
        )
        self._records = tuple(
            TransformerGenerationRecord(
                load_id=loads.load_ids[index],
                generator_count=len(consumers.records[index].generators),
                generation_kwh=sum(
                    item.generation_kwh or 0.0
                    for item in consumers.records[index].generators
                ),
            )
            for index in indices
        )
        self._spatial_index = StaticPointIndex(
            self.bars.x[bar_indices], self.bars.y[bar_indices]
        )

    def __len__(self) -> int:
        return len(self._ids)

    @property
    def generator_ids(self) -> tuple[str, ...]:
        return self._ids

    @property
    def load_indices(self) -> np.ndarray:
        return self._load_indices

    @property
    def bar_indices(self) -> np.ndarray:
        return self._bar_indices

    @property
    def spatial_index(self) -> StaticPointIndex:
        return self._spatial_index

    def record(self, index: int) -> TransformerGenerationRecord:
        return self._records[int(index)]

    def index_for_load(self, load_index: int) -> int | None:
        """O símbolo da CARGA, ou ``None`` se ela não tem GD."""

        positions = np.flatnonzero(self._load_indices == int(load_index))
        return int(positions[0]) if positions.size else None


class _Report:
    def __init__(self) -> None:
        self.issues: list[ConsumerDataIssue] = []
        self.omitted = 0

    def add(self, source: str, reason: str, load_id: str | None = None) -> None:
        if len(self.issues) < MAX_REPORTED_ISSUES:
            self.issues.append(ConsumerDataIssue(source, reason, load_id))
        else:
            self.omitted += 1


def _identifier(text: str) -> str:
    """Identificador sem casa decimal, como ``index_for_id`` compara."""

    text = str(text).strip()
    number = parse_number(text) if text else None
    if number is not None and number.is_integer():
        return str(int(number))
    return text


def _number(text: str) -> float | None:
    text = str(text).strip()
    return parse_number(text) if text else None


def _integer(text: str) -> int | None:
    number = _number(text)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _positive(value: float | None) -> float | None:
    return value if value is not None and value > 0 else None


def build_transformer_consumers(
    loads: LoadModel,
    *,
    transformers: Iterable[Row],
    transformer_units: Iterable[Row] = (),
    bt_consumers: Iterable[Row] = (),
    mt_consumers: Iterable[Row] = (),
    public_lighting: Iterable[Row] = (),
    bt_generators: Iterable[Row] = (),
    mt_generators: Iterable[Row] = (),
    sources: Mapping[str, str] | None = None,
    source_path: str | None = None,
    cancel_check: CancelCheck | None = None,
) -> TransformerConsumersModel:
    """Liga cada UC, ponto de IP e GD à sua CARGA e monta o trafo de cada uma.

    ``sources`` dá o rótulo de cada tabela no relatório (``BT_CONS`` →
    ``rede.mdb/BT_CONS``); sem ele, vale o nome da tabela.
    """

    labels = dict(sources or {})

    def label(table: str) -> str:
        return labels.get(table, table)

    report = _Report()
    count = 0

    def check() -> None:
        nonlocal count
        count += 1
        if cancel_check is not None and count % 4_096 == 0 and cancel_check():
            raise InterruptedError("Leitura dos consumidores cancelada.")

    def load_index_for(load_id: str) -> int | None:
        return loads.index_for_id(load_id) if load_id else None

    # --- ET → CARGA -------------------------------------------------------
    et_load: dict[str, int] = {}
    et_code: dict[str, str] = {}
    rejected_ets: set[str] = set()
    for row in transformers:
        check()
        et_id = _identifier(row.get("ID", ""))
        load_id = _identifier(row.get("MT_CAR_ID", ""))
        if not et_id or et_id in rejected_ets:
            continue
        if et_id in et_load:
            report.add(label("BT_ET"), f"ET {et_id} repetida; ignorada.", loads.load_ids[et_load[et_id]])
            del et_load[et_id]
            rejected_ets.add(et_id)
            continue
        load_index = load_index_for(load_id)
        if load_index is None:
            report.add(label("BT_ET"), f"ET {et_id}: MT_CAR_ID {load_id or '<vazio>'} sem CARGA na rede.")
            continue
        et_load[et_id] = load_index
        et_code[et_id] = row.get("CODIGO", "").strip()

    # --- UTC: ficha do trafo da ET ---------------------------------------
    units: dict[str, list[Row]] = defaultdict(list)
    for row in transformer_units:
        check()
        et_id = _identifier(row.get("ET_ID", ""))
        if et_id in et_load:
            units[et_id].append(row)

    # --- consumidores -----------------------------------------------------
    consumers_by_load: dict[int, list[ConsumerRecord]] = defaultdict(list)
    bt_load: dict[str, int] = {}
    mt_load: dict[str, int] = {}

    def through_et(table: str, consumer_id: str, raw_et: str) -> int | None:
        et_id = _identifier(raw_et)
        load_index = et_load.get(et_id)
        if load_index is None:
            report.add(label(table), f"{consumer_id}: ET {et_id or '<vazia>'} sem CARGA na rede; ignorado.")
        return load_index

    for row in bt_consumers:
        check()
        consumer_id = _identifier(row.get("ID", ""))
        if not consumer_id:
            report.add(label("BT_CONS"), "UC sem ID ignorada.")
            continue
        if consumer_id in bt_load:
            report.add(label("BT_CONS"), f"UC {consumer_id} repetida; vale a primeira.", loads.load_ids[bt_load[consumer_id]])
            continue
        load_index = through_et("BT_CONS", f"UC {consumer_id}", row.get("ET_ID", ""))
        if load_index is None:
            continue
        bt_load[consumer_id] = load_index
        consumers_by_load[load_index].append(
            ConsumerRecord(
                kind=CONSUMER_KIND_BT,
                consumer_id=consumer_id,
                code=row.get("CODIGO", "").strip(),
                name=" ".join(row.get("NOME", "").split()),
                consumer_class=_integer(row.get("CLASSE", "")),
                phases=row.get("FASES2", "").strip(),
                consumption_kwh=_number(row.get("CONSUMO", "")),
                service_point_id=_identifier(row.get("PS_ID", "")),
            )
        )

    for row in mt_consumers:
        check()
        consumer_id = _identifier(row.get("ID", ""))
        if not consumer_id:
            report.add(label("MT_CONS"), "UC sem ID ignorada.")
            continue
        if consumer_id in mt_load:
            report.add(label("MT_CONS"), f"UC {consumer_id} repetida; vale a primeira.", loads.load_ids[mt_load[consumer_id]])
            continue
        load_id = _identifier(row.get("CARGA_ID", ""))
        load_index = load_index_for(load_id)
        if load_index is None:
            report.add(label("MT_CONS"), f"UC {consumer_id}: CARGA {load_id or '<vazia>'} inexistente na rede; ignorada.")
            continue
        mt_load[consumer_id] = load_index
        active = _integer(row.get("ATIVO", ""))
        consumers_by_load[load_index].append(
            ConsumerRecord(
                kind=CONSUMER_KIND_MT,
                consumer_id=consumer_id,
                code=row.get("CODIGO", "").strip(),
                name=" ".join(row.get("NOME", "").split()),
                consumer_class=_integer(row.get("CLASSE", "")),
                phases=row.get("FASES2", "").strip(),
                consumption_kwh=_number(row.get("CONSUMO", "")),
                # ATIVO ausente ou ilegível não desliga ninguém: só o 0 explícito.
                active=active != 0,
            )
        )

    seen_lighting: set[str] = set()
    for row in public_lighting:
        check()
        point_id = _identifier(row.get("ID", ""))
        if not point_id or point_id in seen_lighting:
            if point_id:
                report.add(label("BT_IP"), f"IP {point_id} repetido; vale o primeiro.")
            continue
        load_index = through_et("BT_IP", f"IP {point_id}", row.get("ET_ID", ""))
        if load_index is None:
            continue
        seen_lighting.add(point_id)
        consumers_by_load[load_index].append(
            ConsumerRecord(
                kind=CONSUMER_KIND_IP,
                consumer_id=point_id,
                code=row.get("CODIGO", "").strip(),
                phases=row.get("FASES2", "").strip(),
                demand_kw=_number(row.get("DEMANDA", "")),
                lamp_count=_integer(row.get("NLAMP", "")),
                service_point_id=_identifier(row.get("PS_ID", "")),
            )
        )

    # --- geração distribuída ---------------------------------------------
    generators_by_load: dict[int, list[GenerationRecord]] = defaultdict(list)
    for table, rows, kind, owner_column, owners in (
        ("BT_GERADOR_CONS", bt_generators, CONSUMER_KIND_BT, "BT_CONS_ID", bt_load),
        ("MT_GERADOR_CONS", mt_generators, CONSUMER_KIND_MT, "MT_CONS_ID", mt_load),
    ):
        seen: set[str] = set()
        for row in rows:
            check()
            generator_id = _identifier(row.get("GERADOR_ID", ""))
            consumer_id = _identifier(row.get(owner_column, ""))
            if not generator_id or generator_id in seen:
                if generator_id:
                    report.add(label(table), f"GD {generator_id} repetida; vale a primeira.")
                continue
            load_index = owners.get(consumer_id)
            if load_index is None:
                report.add(label(table), f"GD {generator_id}: UC {consumer_id or '<vazia>'} sem trafo conhecido; ignorada.")
                continue
            seen.add(generator_id)
            generators_by_load[load_index].append(
                GenerationRecord(
                    kind=kind,
                    generator_id=generator_id,
                    consumer_id=consumer_id,
                    code=row.get("CODIGO", "").strip(),
                    generation_type=_integer(row.get("TIPOGD", "")),
                    curve_id=_identifier(row.get("CURVA_ID", "")),
                    generation_kwh=_number(row.get("GERACAO_KWH", "")),
                    snom_kva=_number(row.get("SNOM", "")),
                )
            )

    # --- o trafo de cada CARGA ------------------------------------------
    ets_by_load: dict[int, list[str]] = defaultdict(list)
    for et_id, load_index in et_load.items():
        ets_by_load[load_index].append(et_id)

    records: list[TransformerConsumersRecord] = []
    for load_index, load_id in enumerate(loads.load_ids):
        consumers = tuple(consumers_by_load.get(load_index, ()))
        has_ep = any(item.kind == CONSUMER_KIND_MT for item in consumers)
        et_ids = ets_by_load.get(load_index, [])
        spec = _transformer_spec(
            loads, load_index, et_ids, et_code, units, has_ep, report, label
        )
        records.append(
            TransformerConsumersRecord(
                load_id=load_id,
                transformer=spec,
                consumers=consumers,
                generators=tuple(generators_by_load.get(load_index, ())),
            )
        )

    return TransformerConsumersModel(
        loads=loads,
        records=tuple(records),
        issues=tuple(report.issues),
        omitted_issues=report.omitted,
        source_path=source_path,
    )


def _transformer_spec(
    loads: LoadModel,
    load_index: int,
    et_ids: Sequence[str],
    et_code: Mapping[str, str],
    units: Mapping[str, Sequence[Row]],
    has_ep: bool,
    report: _Report,
    label: Callable[[str], str],
) -> TransformerSpec | None:
    load_id = loads.load_ids[load_index]
    record = loads.record(load_index)
    registry_kva = _positive(_number(record.snom))
    registry_secondary = _positive(_number(record.secondary_line_voltage))
    if not et_ids:
        if not has_ep:
            return None
        return TransformerSpec(
            origin=ORIGIN_EP,
            codes=(record.code,) if record.code else (),
            kva=registry_kva,
            secondary_v=registry_secondary,
        )

    if len(et_ids) > 1:
        report.add(
            label("BT_ET"),
            f"CARGA {load_id}: {len(et_ids)} ETs no mesmo ponto; modeladas como um trafo só, "
            "com a soma dos kVA.",
            load_id,
        )
    rows = [row for et_id in sorted(et_ids) for row in units.get(et_id, ())]
    if not rows:
        report.add(label("BT_UTC"), f"CARGA {load_id}: ET sem UTC; kVA e tensões indisponíveis.", load_id)
    first = rows[0] if rows else {}
    for row in rows[1:]:
        if any(
            row.get(column, "").strip() != first.get(column, "").strip()
            for column in ("VPRIMARIO", "VSECUNDA", "FASES_BT2")
        ):
            report.add(
                label("BT_UTC"),
                f"CARGA {load_id}: UTCs com tensões ou fases diferentes; vale a primeira.",
                load_id,
            )
            break
    kva_values = [_positive(_number(row.get("SNOM", ""))) for row in rows]
    kva = sum(value for value in kva_values if value is not None) or None
    secondary_v = _positive(_number(first.get("VSECUNDA", "")))
    if has_ep:
        mismatched = [
            name
            for name, registry, unit in (
                ("kVA", registry_kva, kva),
                ("tensão do secundário", registry_secondary, secondary_v),
            )
            if registry is not None and unit is not None and abs(registry - unit) > 1e-6
        ]
        if mismatched:
            report.add(
                label("CARGA"),
                f"CARGA {load_id}: ET e EP com {' e '.join(mismatched)} diferentes; vale o da ET.",
                load_id,
            )
    return TransformerSpec(
        origin=ORIGIN_ET_EP if has_ep else ORIGIN_ET,
        codes=tuple(dict.fromkeys(code for code in (et_code.get(et_id, "") for et_id in sorted(et_ids)) if code)),
        kva=kva,
        primary_kv=_positive(_number(first.get("VPRIMARIO", ""))),
        secondary_v=secondary_v,
        secondary_phases=first.get("FASES_BT2", "").strip(),
    )
