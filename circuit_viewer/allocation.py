"""Agrega energia e geração por transformador para alocação OpenDSS.

O protótipo ``CODIGO_TRAFOS.py`` demonstrou a relação entre as cinco
tabelas de consumidores/geradores e ``CARGA``. Este módulo transforma aquela
ideia em um núcleo imutável, validado e independente de Qt ou de I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .model import LoadModel
from .opendss_export import parse_number
from .phase_config import PhaseConfiguration


PHASE_LETTERS = ("D", "E", "F")

BT_ET_HEADER = ("ID", "MT_CAR_ID")
BT_CONSUMER_HEADER = ("ET_ID", "FASES2", "CONSUMO")
BT_CONSUMER_DIAGNOSTIC_HEADER = ("ID", "CODIGO")
BT_GENERATOR_HEADER = ("ET_ID", "GERACAO_KWH")
MT_CONSUMER_ENERGY_HEADER = ("ID", "CARGA_ID", "FASES2", "CONSUMO")
MT_GENERATOR_ENERGY_HEADER = ("MT_CONS_ID", "GERACAO_KWH")

CancelCheck = Callable[[], bool]


class AllocationDataError(ValueError):
    """Falha estrutural que impede interpretar uma das tabelas de alocação."""


@dataclass(frozen=True, slots=True)
class PhaseValues:
    """Três valores na ordem elétrica D/E/F."""

    d: float = 0.0
    e: float = 0.0
    f: float = 0.0

    def for_phase(self, letter: str) -> float:
        try:
            return (self.d, self.e, self.f)[PHASE_LETTERS.index(letter)]
        except ValueError as exc:
            raise KeyError(letter) from exc

    def __add__(self, other: "PhaseValues") -> "PhaseValues":
        if not isinstance(other, PhaseValues):
            return NotImplemented
        return PhaseValues(
            self.d + other.d,
            self.e + other.e,
            self.f + other.f,
        )


@dataclass(frozen=True, slots=True)
class TransformerAllocationRecord:
    """Agregados de um ``CARGA_ID`` na mesma posição de ``LoadModel``."""

    load_id: str
    energy_bt: PhaseValues
    energy_mt: PhaseValues
    generation_bt_kwh: float
    generation_mt_kwh: float

    @property
    def total_energy(self) -> PhaseValues:
        return self.energy_bt + self.energy_mt


@dataclass(frozen=True, slots=True)
class AllocationDataIssue:
    """Dado recusado, opcionalmente atribuível a um transformador."""

    source: str
    row_number: int
    reason: str
    load_id: str | None = None

    @property
    def description(self) -> str:
        location = self.source
        if self.row_number > 0:
            location += f", linha {self.row_number}"
        return f"{location}: {self.reason}"


@dataclass(frozen=True, slots=True)
class TransformerAllocationModel:
    """Retrato imutável dos agregados e diagnósticos de todas as cargas."""

    loads: LoadModel
    phase_configuration: PhaseConfiguration
    records: tuple[TransformerAllocationRecord, ...]
    issues: tuple[AllocationDataIssue, ...] = ()
    source_path: str | None = None

    def __post_init__(self) -> None:
        if len(self.records) != len(self.loads):
            raise ValueError("Cada carga deve possuir um registro de alocação.")
        if any(
            record.load_id != self.loads.load_ids[index]
            for index, record in enumerate(self.records)
        ):
            raise ValueError("Os agregados não correspondem à ordem das cargas.")

    def record(self, load_index: int) -> TransformerAllocationRecord:
        if not 0 <= int(load_index) < len(self.records):
            raise IndexError(load_index)
        return self.records[int(load_index)]

    def issues_for_loads(
        self,
        load_ids: Iterable[str],
        *,
        include_unattributed: bool = False,
    ) -> tuple[AllocationDataIssue, ...]:
        """Ocorrências atribuíveis aos transformadores informados."""

        selected = frozenset(str(value) for value in load_ids)
        return tuple(
            issue
            for issue in self.issues
            if issue.load_id in selected
            or (include_unattributed and issue.load_id is None)
        )


@dataclass(frozen=True, slots=True)
class AllocationTable:
    """Uma tabela já convertida em linhas textuais canônicas."""

    header: Sequence[str]
    rows: Iterable[Sequence[str]]
    source: str
    first_row_number: int = 1


def _positions(
    header: Sequence[str],
    required: tuple[str, ...],
    source: str,
) -> dict[str, int]:
    normalized = tuple(str(value).strip().lstrip("\ufeff") for value in header)
    missing = [name for name in required if normalized.count(name) == 0]
    duplicated = [name for name in required if normalized.count(name) > 1]
    if missing or duplicated:
        details: list[str] = []
        if missing:
            details.append("ausentes: " + ", ".join(missing))
        if duplicated:
            details.append("duplicadas: " + ", ".join(duplicated))
        raise AllocationDataError(
            f"{source}: cabeçalho inválido; " + "; ".join(details) + "."
        )
    return {name: normalized.index(name) for name in required}


def _row_values(
    row: Sequence[str],
    positions: dict[str, int],
) -> dict[str, str] | None:
    if not row or not any(str(value).strip() for value in row):
        return None
    if len(row) <= max(positions.values()):
        return None
    return {name: str(row[index]).strip() for name, index in positions.items()}


def _optional_row_value(
    table: AllocationTable,
    row: Sequence[str],
    name: str,
) -> str:
    """Retorna uma coluna diagnóstica opcional sem torná-la obrigatória."""

    normalized = tuple(str(value).strip().lstrip("\ufeff") for value in table.header)
    if normalized.count(name) != 1:
        return ""
    index = normalized.index(name)
    if index >= len(row):
        return ""
    return str(row[index]).strip()


def build_transformer_allocations(
    loads: LoadModel,
    phase_configuration: PhaseConfiguration,
    *,
    bt_et: AllocationTable,
    bt_consumers: AllocationTable,
    bt_generators: AllocationTable,
    mt_consumers: AllocationTable,
    mt_generators: AllocationTable,
    source_path: str | None = None,
    cancel_check: CancelCheck | None = None,
) -> TransformerAllocationModel:
    """Valida as cinco tabelas e agrega seus valores por ``CARGA_ID``.

    Consumidores usam as próprias fases. Geradores conservam somente a energia
    total por transformador; a divisão pelas fases reais do transformador é
    feita no exportador, pois depende do patamar e da curva escolhida.
    """

    tables = (
        (bt_et, BT_ET_HEADER),
        (bt_consumers, BT_CONSUMER_HEADER),
        (bt_generators, BT_GENERATOR_HEADER),
        (mt_consumers, MT_CONSUMER_ENERGY_HEADER),
        (mt_generators, MT_GENERATOR_ENERGY_HEADER),
    )
    positions = {
        id(table): _positions(table.header, required, table.source)
        for table, required in tables
    }
    energy_bt = [[0.0, 0.0, 0.0] for _ in range(len(loads))]
    energy_mt = [[0.0, 0.0, 0.0] for _ in range(len(loads))]
    generation_bt = [0.0] * len(loads)
    generation_mt = [0.0] * len(loads)
    issues: list[AllocationDataIssue] = []

    transformer_phases: list[tuple[str, ...] | None] = []
    for load_index, raw_phases in enumerate(loads.phases):
        letters = phase_configuration.phase_letters_for_value(raw_phases)
        transformer_phases.append(letters)
        if letters is None:
            issues.append(
                AllocationDataIssue(
                    "CARGA",
                    load_index + 1,
                    f"FASES2 sem relação válida: {raw_phases or '<vazio>'}",
                    loads.load_ids[load_index],
                )
            )

    def report(
        table: AllocationTable,
        row_number: int,
        reason: str,
        load_id: str | None = None,
    ) -> None:
        issues.append(AllocationDataIssue(table.source, row_number, reason, load_id))

    def load_index_for(
        load_id: str,
        table: AllocationTable,
        row_number: int,
    ) -> int | None:
        index = loads.index_for_id(load_id)
        if index is None:
            report(
                table,
                row_number,
                f"CARGA_ID inexistente no modelo atual: {load_id or '<vazio>'}",
            )
        return index

    et_to_load: dict[str, tuple[str, int]] = {}
    invalid_et_ids: set[str] = set()
    et_positions = positions[id(bt_et)]
    for offset, row in enumerate(bt_et.rows, start=bt_et.first_row_number):
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Agregação de alocação cancelada.")
        values = _row_values(row, et_positions)
        if values is None:
            report(bt_et, offset, "faltam valores em colunas obrigatórias")
            continue
        et_id = values["ID"]
        load_id = values["MT_CAR_ID"]
        if not et_id or not load_id:
            report(bt_et, offset, "ID e MT_CAR_ID são obrigatórios")
            continue
        if et_id in et_to_load or et_id in invalid_et_ids:
            report(bt_et, offset, f"ID duplicado: {et_id}")
            et_to_load.pop(et_id, None)
            invalid_et_ids.add(et_id)
            continue
        load_index = load_index_for(load_id, bt_et, offset)
        if load_index is None:
            continue
        et_to_load[et_id] = (load_id, load_index)

    def add_consumer(
        table: AllocationTable,
        target: list[list[float]],
        *,
        bt: bool,
        mt_map: dict[str, tuple[str, int]] | None = None,
    ) -> dict[str, tuple[str, int]]:
        source_map: dict[str, tuple[str, int]] = {} if mt_map is None else mt_map
        invalid_ids: set[str] = set()
        table_positions = positions[id(table)]
        for offset, row in enumerate(table.rows, start=table.first_row_number):
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Agregação de alocação cancelada.")
            values = _row_values(row, table_positions)
            if values is None:
                report(table, offset, "faltam valores em colunas obrigatórias")
                continue
            source_id = values["ET_ID"] if bt else values["ID"]
            consumer_kind = "BT" if bt else "MT"
            consumer_parts = [f"consumidor {consumer_kind}"]
            if bt:
                consumer_id = _optional_row_value(table, row, "ID")
                consumer_code = _optional_row_value(table, row, "CODIGO")
                if consumer_id:
                    consumer_parts.append(f"ID={consumer_id}")
                if consumer_code:
                    consumer_parts.append(f"CODIGO={consumer_code}")
                consumer_parts.append(f"ET_ID={source_id or '<vazio>'}")
            else:
                consumer_parts.append(f"ID={source_id or '<vazio>'}")
            consumer_identity = ", ".join(consumer_parts)
            if bt:
                association = et_to_load.get(source_id)
                if association is None:
                    report(
                        table,
                        offset,
                        f"{consumer_identity}: ET_ID sem vínculo em BT_ET",
                    )
                    continue
            else:
                load_id = values["CARGA_ID"]
                load_index = load_index_for(load_id, table, offset)
                if load_index is None:
                    continue
                association = (load_id, load_index)
                if not source_id:
                    report(table, offset, "ID vazio", load_id)
                    continue
                if source_id in source_map or source_id in invalid_ids:
                    report(table, offset, f"ID duplicado: {source_id}", load_id)
                    source_map.pop(source_id, None)
                    invalid_ids.add(source_id)
                    continue
                source_map[source_id] = association

            load_id, load_index = association
            letters = phase_configuration.phase_letters_for_value(values["FASES2"])
            if letters is None:
                report(
                    table,
                    offset,
                    f"{consumer_identity}: FASES2 sem relação válida: "
                    f"{values['FASES2'] or '<vazio>'}",
                    load_id,
                )
                continue
            allowed = transformer_phases[load_index]
            consumer_parts.append(
                f"FASES2={values['FASES2'] or '<vazio>'} ({'-'.join(letters)})"
            )
            transformer_code = loads.codes[load_index] or "<vazio>"
            transformer_raw_phases = loads.phases[load_index] or "<vazio>"
            transformer_letters = (
                "sem relação" if allowed is None else "-".join(allowed)
            )
            incompatibility_reason = (
                "fases do consumidor incompatíveis com as fases do transformador; "
                + ", ".join(consumer_parts)
                + f"; transformador CARGA_ID={load_id}, CODIGO={transformer_code}, "
                + f"FASES2={transformer_raw_phases} ({transformer_letters})"
            )
            if allowed is None:
                report(
                    table,
                    offset,
                    incompatibility_reason,
                    load_id,
                )
                continue
            if bt and len(allowed) == 1:
                # BT_CONS.FASES2 describes the secondary connection.  Behind a
                # monophase transformer, all of that energy is supplied by the
                # transformer's single primary phase, even for biphase clients.
                destination_letters = allowed
            elif not set(letters).issubset(allowed):
                report(
                    table,
                    offset,
                    incompatibility_reason,
                    load_id,
                )
                continue
            else:
                destination_letters = letters
            energy = parse_number(values["CONSUMO"])
            if energy is None or energy < 0.0:
                report(
                    table,
                    offset,
                    f"{consumer_identity}: CONSUMO deve ser numérico e não negativo",
                    load_id,
                )
                continue
            per_phase = energy / len(destination_letters)
            for letter in destination_letters:
                target[load_index][PHASE_LETTERS.index(letter)] += per_phase
        return source_map

    add_consumer(
        bt_consumers,
        energy_bt,
        bt=True,
    )
    mt_to_load = add_consumer(
        mt_consumers,
        energy_mt,
        bt=False,
    )

    def add_generators(
        table: AllocationTable,
        association: dict[str, tuple[str, int]],
        target: list[float],
        key: str,
    ) -> None:
        table_positions = positions[id(table)]
        for offset, row in enumerate(table.rows, start=table.first_row_number):
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Agregação de alocação cancelada.")
            values = _row_values(row, table_positions)
            if values is None:
                report(table, offset, "faltam valores em colunas obrigatórias")
                continue
            source_id = values[key]
            linked = association.get(source_id)
            if linked is None:
                report(table, offset, f"{key} sem vínculo: {source_id or '<vazio>'}")
                continue
            load_id, load_index = linked
            energy = parse_number(values["GERACAO_KWH"])
            if energy is None or energy < 0.0:
                report(
                    table,
                    offset,
                    f"{key}={source_id or '<vazio>'}: "
                    "GERACAO_KWH deve ser numérico e não negativo",
                    load_id,
                )
                continue
            target[load_index] += energy

    add_generators(bt_generators, et_to_load, generation_bt, "ET_ID")
    add_generators(mt_generators, mt_to_load, generation_mt, "MT_CONS_ID")

    records = tuple(
        TransformerAllocationRecord(
            loads.load_ids[index],
            PhaseValues(*energy_bt[index]),
            PhaseValues(*energy_mt[index]),
            generation_bt[index],
            generation_mt[index],
        )
        for index in range(len(loads))
    )
    return TransformerAllocationModel(
        loads,
        phase_configuration,
        records,
        tuple(issues),
        source_path,
    )


__all__ = [
    "AllocationDataError",
    "AllocationDataIssue",
    "AllocationTable",
    "BT_CONSUMER_DIAGNOSTIC_HEADER",
    "BT_CONSUMER_HEADER",
    "BT_ET_HEADER",
    "BT_GENERATOR_HEADER",
    "MT_CONSUMER_ENERGY_HEADER",
    "MT_GENERATOR_ENERGY_HEADER",
    "PHASE_LETTERS",
    "PhaseValues",
    "TransformerAllocationModel",
    "TransformerAllocationRecord",
    "build_transformer_allocations",
]
