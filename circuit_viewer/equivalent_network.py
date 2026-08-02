"""Projeção lógica de ramais como cargas equivalentes, sem alterar as fontes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Literal

import numpy as np

from .branch_analysis import BranchAnalysisResult, BranchRecord, BranchType
from .model import (
    BoolArray,
    CircuitCatalogModel,
    IndexArray,
    LoadModel,
    LoadPatternModel,
    StaticPointIndex,
)


MAX_EQUIVALENT_ISSUES = 500
ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]
_DECIMAL_PATTERN = re.compile(
    r"^[+-]?(?:(?:\d+(?:[.,]\d*)?)|(?:[.,]\d+))(?:[eE][+-]?\d+)?$"
)
_PATTERN_FIELDS = ("pd", "pe", "pf", "qd", "qe", "qf")


def _readonly_indices(values: Sequence[int] | np.ndarray) -> IndexArray:
    result = np.ascontiguousarray(values, dtype=np.intp)
    if result.ndim != 1:
        raise ValueError("Os índices devem formar um vetor unidimensional.")
    result.setflags(write=False)
    return result


def _readonly_mask(values: Sequence[bool] | np.ndarray) -> BoolArray:
    result = np.ascontiguousarray(values, dtype=np.bool_)
    if result.ndim != 1:
        raise ValueError("A máscara deve formar um vetor unidimensional.")
    result.setflags(write=False)
    return result


def _parse_decimal(value: str) -> Decimal:
    text = str(value).strip()
    if not text:
        raise ValueError("valor vazio")
    if not _DECIMAL_PATTERN.fullmatch(text):
        raise ValueError("valor não numérico")
    if "," in text:
        if "." in text:
            raise ValueError("separadores decimais ambíguos")
        text = text.replace(",", ".")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("valor não numérico") from exc
    if not parsed.is_finite():
        raise ValueError("valor não finito")
    return parsed


@dataclass(frozen=True, slots=True)
class EquivalentNetworkIssue:
    """Diagnóstico de uma parcela que impediu uma agregação completa."""

    branch_id: int
    field: str
    message: str
    load_id: str | None = None


@dataclass(frozen=True, slots=True)
class EquivalentLoadPatternRecord:
    """Totais de potência de uma carga equivalente em um patamar."""

    load_id: str
    npat: int
    pd: Decimal
    pe: Decimal
    pf: Decimal
    qd: Decimal
    qe: Decimal
    qf: Decimal

    def __post_init__(self) -> None:
        if self.npat not in {0, 1, 2, 3}:
            raise ValueError("NPAT deve ser um inteiro entre 0 e 3.")
        if any(not value.is_finite() for value in self.values):
            raise ValueError("Os valores equivalentes devem ser decimais finitos.")

    @property
    def values(self) -> tuple[Decimal, ...]:
        return (self.pd, self.pe, self.pf, self.qd, self.qe, self.qf)


@dataclass(frozen=True, slots=True)
class EquivalentLoadRecord:
    """Carga derivada de um ramal, com proveniência explícita."""

    branch_id: int
    branch_type: BranchType
    load_id: str
    origin_kind: Literal["branch_aggregate"]
    circuit_index: int
    circuit_id: str
    bar_index: int
    bar_id: str
    first_segment_id: str
    phases2: str
    phase: str
    removable: bool
    source_load_indices: IndexArray
    snom: Decimal | None
    sadm: Decimal | None

    def __post_init__(self) -> None:
        if self.branch_id <= 0:
            raise ValueError("RAMAL_ID deve ser positivo.")
        if not isinstance(self.branch_type, BranchType):
            raise ValueError("TIPO_RAMAL equivalente deve ser conhecido.")
        if self.load_id != f"RAMAL-{self.branch_id}":
            raise ValueError("CARGA_ID equivalente deve usar o prefixo RAMAL-.")
        if self.origin_kind != "branch_aggregate":
            raise ValueError("A origem da carga equivalente deve ser um ramal.")
        if self.circuit_index < 0 or self.bar_index < 0:
            raise ValueError("A carga equivalente referencia índices inválidos.")
        if (
            self.source_load_indices.dtype != np.dtype(np.intp)
            or self.source_load_indices.ndim != 1
            or self.source_load_indices.flags.writeable
        ):
            raise ValueError("Os índices de proveniência devem ser imutáveis.")
        for value in (self.snom, self.sadm):
            if value is not None and not value.is_finite():
                raise ValueError("Os totais equivalentes devem ser finitos.")

    @property
    def source_load_count(self) -> int:
        return int(self.source_load_indices.size)


@dataclass(frozen=True, slots=True)
class EquivalentVisibilityMasks:
    """Máscaras resultantes da projeção simplificada para um estado visual."""

    bar_mask: BoolArray
    segment_mask: BoolArray
    source_load_mask: BoolArray | None
    equivalent_load_mask: BoolArray

    def __post_init__(self) -> None:
        for values in (self.bar_mask, self.segment_mask, self.equivalent_load_mask):
            if values.dtype != np.dtype(np.bool_) or values.flags.writeable:
                raise ValueError("As máscaras equivalentes devem ser booleanas e imutáveis.")
        if self.source_load_mask is not None and (
            self.source_load_mask.dtype != np.dtype(np.bool_)
            or self.source_load_mask.flags.writeable
        ):
            raise ValueError("A máscara de cargas originais deve ser imutável.")


class EquivalentNetworkModel:
    """Snapshot derivado para renderização, seleção e análise simplificada."""

    __slots__ = (
        "branches",
        "catalog",
        "source_loads",
        "source_patterns",
        "_records",
        "_patterns",
        "_load_ids",
        "_bar_indices",
        "_by_branch_id",
        "_by_load_id",
        "_spatial_index",
        "_retained_segments",
        "_retained_bars",
        "_reduced_loads",
        "_equivalents_by_circuit",
        "_bar_owner_counts",
    )

    def __init__(
        self,
        branches: BranchAnalysisResult,
        catalog: CircuitCatalogModel,
        source_loads: LoadModel | None,
        source_patterns: LoadPatternModel | None,
        records: Sequence[EquivalentLoadRecord],
        patterns: Sequence[Sequence[EquivalentLoadPatternRecord] | None],
    ) -> None:
        record_values = tuple(records)
        pattern_values = tuple(None if group is None else tuple(group) for group in patterns)
        if len(record_values) != len(branches.records) or len(pattern_values) != len(
            record_values
        ):
            raise ValueError("Cada ramal deve possuir uma carga equivalente.")
        if catalog is not branches.source_catalog:
            raise ValueError("O catálogo equivalente não corresponde à análise.")
        if source_loads is not branches.source_loads:
            raise ValueError("As cargas equivalentes não correspondem à análise.")
        if source_patterns is not None and source_patterns.loads is not source_loads:
            raise ValueError("Os patamares equivalentes não correspondem às cargas.")

        self.branches = branches
        self.catalog = catalog
        self.source_loads = source_loads
        self.source_patterns = source_patterns
        self._records = record_values
        self._patterns = pattern_values
        self._load_ids = tuple(record.load_id for record in record_values)
        self._bar_indices = _readonly_indices(
            [record.bar_index for record in record_values]
        )
        self._by_branch_id = {
            record.branch_id: index for index, record in enumerate(record_values)
        }
        self._by_load_id = {
            record.load_id: index for index, record in enumerate(record_values)
        }
        bars = catalog.segments.bars
        self._spatial_index = StaticPointIndex(
            bars.x[self._bar_indices],
            bars.y[self._bar_indices],
        )

        records_by_circuit: list[list[BranchRecord]] = [
            [] for _ in range(len(catalog))
        ]
        equivalents_by_circuit: list[list[int]] = [[] for _ in range(len(catalog))]
        for equivalent_index, branch in enumerate(branches.records):
            records_by_circuit[branch.circuit_index].append(branch)
            equivalents_by_circuit[branch.circuit_index].append(equivalent_index)

        retained_segments: list[IndexArray] = []
        retained_bars: list[IndexArray] = []
        reduced_loads: list[IndexArray] = []
        bar_owner_counts = np.zeros(len(bars), dtype=np.int32)
        for membership in catalog.memberships:
            bar_owner_counts[membership.bar_indices] += 1

        for circuit_index, circuit_records in enumerate(records_by_circuit):
            membership = catalog.membership(circuit_index)
            reduced_segment_values = np.unique(
                np.concatenate([record.segment_indices for record in circuit_records])
            ) if circuit_records else np.empty(0, dtype=np.intp)
            retained = np.setdiff1d(
                membership.segment_indices,
                reduced_segment_values,
                assume_unique=True,
            ).astype(np.intp, copy=False)
            retained_segments.append(_readonly_indices(retained))

            if retained.size:
                segment_model = catalog.segments
                retained_bar_values = np.concatenate(
                    (
                        segment_model.start_indices[retained],
                        segment_model.end_indices[retained],
                    )
                )
            else:
                retained_bar_values = np.empty(0, dtype=np.intp)
            connection_values = np.asarray(
                [record.connection_bar_index for record in circuit_records],
                dtype=np.intp,
            )
            root_index = bars.index_for_id(catalog.definition(circuit_index).root_bar_id)
            root_values = (
                np.empty(0, dtype=np.intp)
                if root_index is None
                else np.asarray([root_index], dtype=np.intp)
            )
            retained_bars.append(
                _readonly_indices(
                    np.unique(
                        np.concatenate(
                            (retained_bar_values, connection_values, root_values)
                        )
                    )
                )
            )
            reduced_loads.append(
                _readonly_indices(
                    np.unique(
                        np.concatenate(
                            [record.load_indices for record in circuit_records]
                        )
                    )
                    if circuit_records
                    else np.empty(0, dtype=np.intp)
                )
            )

        bar_owner_counts.setflags(write=False)
        self._retained_segments = tuple(retained_segments)
        self._retained_bars = tuple(retained_bars)
        self._reduced_loads = tuple(reduced_loads)
        self._equivalents_by_circuit = tuple(
            _readonly_indices(values) for values in equivalents_by_circuit
        )
        self._bar_owner_counts = bar_owner_counts

    def __len__(self) -> int:
        return len(self._records)

    @property
    def bars(self):  # noqa: ANN201 - interface gráfica compatível com cargas
        return self.catalog.segments.bars

    @property
    def records(self) -> tuple[EquivalentLoadRecord, ...]:
        return self._records

    @property
    def load_ids(self) -> tuple[str, ...]:
        return self._load_ids

    @property
    def bar_indices(self) -> IndexArray:
        return self._bar_indices

    @property
    def spatial_index(self) -> StaticPointIndex:
        return self._spatial_index

    def record(self, index: int) -> EquivalentLoadRecord:
        if not 0 <= int(index) < len(self):
            raise IndexError(index)
        return self._records[int(index)]

    def records_for_load(self, index: int) -> tuple[EquivalentLoadPatternRecord, ...]:
        if not 0 <= int(index) < len(self):
            raise IndexError(index)
        group = self._patterns[int(index)]
        return () if group is None else group

    def index_for_branch_id(self, branch_id: int) -> int | None:
        return self._by_branch_id.get(int(branch_id))

    def index_for_id(self, load_id: str) -> int | None:
        return self._by_load_id.get(str(load_id))

    def visibility_masks(self, checked: Sequence[bool]) -> EquivalentVisibilityMasks:
        checked_values = np.asarray(checked, dtype=np.bool_)
        if checked_values.ndim != 1 or checked_values.size != len(self.catalog):
            raise ValueError("A visibilidade deve corresponder aos circuitos.")

        segment_counts = np.zeros(len(self.catalog.segments), dtype=np.int32)
        bar_counts = np.zeros(len(self.bars), dtype=np.int32)
        equivalent_mask = np.zeros(len(self), dtype=np.bool_)
        reduced_load_counts = (
            None
            if self.source_loads is None
            else np.zeros(len(self.source_loads), dtype=np.int32)
        )
        for circuit_index in np.flatnonzero(checked_values):
            index = int(circuit_index)
            segment_counts[self._retained_segments[index]] += 1
            bar_counts[self._retained_bars[index]] += 1
            equivalent_mask[self._equivalents_by_circuit[index]] = True
            if reduced_load_counts is not None:
                reduced_load_counts[self._reduced_loads[index]] += 1

        segment_mask = (self.catalog.segment_owner_counts == 0) | (
            segment_counts > 0
        )
        bar_mask = (self._bar_owner_counts == 0) | (bar_counts > 0)
        source_load_mask = None
        if self.source_loads is not None and reduced_load_counts is not None:
            source_load_mask = bar_mask[self.source_loads.bar_indices] & (
                reduced_load_counts == 0
            )
        return EquivalentVisibilityMasks(
            _readonly_mask(bar_mask),
            _readonly_mask(segment_mask),
            None if source_load_mask is None else _readonly_mask(source_load_mask),
            _readonly_mask(equivalent_mask),
        )


@dataclass(frozen=True, slots=True)
class EquivalentNetworkResult:
    model: EquivalentNetworkModel
    issues: tuple[EquivalentNetworkIssue, ...]
    omitted_issue_count: int = 0

    def __post_init__(self) -> None:
        if self.omitted_issue_count < 0:
            raise ValueError("A contagem de diagnósticos omitidos não pode ser negativa.")


def build_equivalent_network(
    branches: BranchAnalysisResult,
    loads: LoadModel | None,
    patterns: LoadPatternModel | None = None,
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> EquivalentNetworkResult:
    """Agrega as cargas dos ramais em um snapshot lógico independente de Qt."""

    catalog = branches.source_catalog
    if catalog is None:
        raise ValueError("A análise de ramais não informa seu catálogo de origem.")
    if loads is not branches.source_loads:
        raise ValueError("As cargas não correspondem ao snapshot dos ramais.")
    if patterns is not None and patterns.loads is not loads:
        raise ValueError("Os patamares não correspondem às cargas informadas.")

    issues: list[EquivalentNetworkIssue] = []
    omitted_issues = 0
    inspected = 0

    def cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    def inspect() -> None:
        nonlocal inspected
        inspected += 1
        if inspected % 4_096 == 0 and cancelled():
            raise InterruptedError("Construção da rede equivalente cancelada.")

    def add_issue(
        branch_id: int,
        field: str,
        message: str,
        load_id: str | None = None,
    ) -> None:
        nonlocal omitted_issues
        if len(issues) < MAX_EQUIVALENT_ISSUES:
            issues.append(EquivalentNetworkIssue(branch_id, field, message, load_id))
        else:
            omitted_issues += 1

    def aggregate_load_field(branch: BranchRecord, field: str) -> Decimal | None:
        if branch.load_indices.size == 0:
            return Decimal(0)
        assert loads is not None
        values = loads.snom_values if field == "SNOM" else loads.sadm_values
        with localcontext() as context:
            context.prec = 50
            total = Decimal(0)
            for load_value in branch.load_indices:
                inspect()
                load_index = int(load_value)
                try:
                    total += _parse_decimal(values[load_index])
                except ValueError as exc:
                    add_issue(
                        branch.branch_id,
                        field,
                        f"{field} indisponível: {exc}.",
                        loads.load_ids[load_index],
                    )
                    return None
            return total

    def aggregate_patterns(
        branch: BranchRecord,
        load_id: str,
    ) -> tuple[EquivalentLoadPatternRecord, ...] | None:
        if patterns is None:
            return None
        totals = [[Decimal(0) for _ in _PATTERN_FIELDS] for _ in range(4)]
        with localcontext() as context:
            context.prec = 50
            for load_value in branch.load_indices:
                inspect()
                load_index = int(load_value)
                group = patterns.records_for_load(load_index)
                if len(group) != 4:
                    assert loads is not None
                    add_issue(
                        branch.branch_id,
                        "PATAMARES",
                        "Tabela equivalente indisponível: carga sem os quatro patamares.",
                        loads.load_ids[load_index],
                    )
                    return None
                for source in group:
                    for field_index, field in enumerate(_PATTERN_FIELDS):
                        try:
                            totals[source.npat][field_index] += _parse_decimal(
                                getattr(source, field)
                            )
                        except ValueError as exc:
                            assert loads is not None
                            add_issue(
                                branch.branch_id,
                                field.upper(),
                                "Tabela equivalente indisponível: "
                                f"NPAT {source.npat}, {field.upper()} {exc}.",
                                loads.load_ids[load_index],
                            )
                            return None
        return tuple(
            EquivalentLoadPatternRecord(load_id, npat, *totals[npat])
            for npat in range(4)
        )

    equivalent_records: list[EquivalentLoadRecord] = []
    equivalent_patterns: list[tuple[EquivalentLoadPatternRecord, ...] | None] = []
    total_branches = len(branches.records)
    for index, branch in enumerate(branches.records):
        if cancelled():
            raise InterruptedError("Construção da rede equivalente cancelada.")
        load_id = f"RAMAL-{branch.branch_id}"
        equivalent_records.append(
            EquivalentLoadRecord(
                branch_id=branch.branch_id,
                branch_type=branch.branch_type,
                load_id=load_id,
                origin_kind="branch_aggregate",
                circuit_index=branch.circuit_index,
                circuit_id=branch.circuit_id,
                bar_index=branch.connection_bar_index,
                bar_id=branch.connection_bar_id,
                first_segment_id=branch.first_segment_id,
                phases2=branch.phases2,
                phase=branch.phase,
                removable=branch.removable,
                source_load_indices=branch.load_indices,
                snom=aggregate_load_field(branch, "SNOM"),
                sadm=aggregate_load_field(branch, "SADM"),
            )
        )
        equivalent_patterns.append(aggregate_patterns(branch, load_id))
        if progress is not None:
            progress(index + 1, total_branches)

    if cancelled():
        raise InterruptedError("Construção da rede equivalente cancelada.")
    model = EquivalentNetworkModel(
        branches,
        catalog,
        loads,
        patterns,
        equivalent_records,
        equivalent_patterns,
    )
    return EquivalentNetworkResult(model, tuple(issues), omitted_issues)
