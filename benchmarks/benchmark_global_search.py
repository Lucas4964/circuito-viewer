"""Benchmark reproduzível do índice global com o volume de referência."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import statistics
import sys
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circuit_viewer.model import (
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.search import (
    GlobalSearchIndex,
    build_field_search_partition,
)


BAR_COUNT = 100_000
NETWORK_COUNT = 17_000
LOAD_COUNT = 100_000
BAR_INDEX_TARGET_SECONDS = 1.0
QUERY_P95_TARGET_MS = 5.0
ALL_FIELD_QUERY_P95_TARGET_MS = 100.0


def make_models() -> tuple[CircuitModel, LineNetworkModel, SwitchModel, LoadModel]:
    bar_indices = np.arange(BAR_COUNT, dtype=np.float64)
    bars = CircuitModel(
        (f"B{index}" for index in range(BAR_COUNT)),
        (f"BARRA-{index:06d}" for index in range(BAR_COUNT)),
        500_000.0 + np.remainder(bar_indices, 1_000) * 10.0,
        8_000_000.0 + np.floor_divide(bar_indices, 1_000) * 10.0,
        UtmCrs(21, northern=False),
    )
    segments = LineNetworkModel(
        bars,
        (f"T{index}" for index in range(NETWORK_COUNT)),
        (f"TRECHO-{index:05d}" for index in range(NETWORK_COUNT)),
        ("ABC" for _ in range(NETWORK_COUNT)),
        np.arange(NETWORK_COUNT, dtype=np.intp),
        np.arange(1, NETWORK_COUNT + 1, dtype=np.intp),
        ("" for _ in range(NETWORK_COUNT)),
        ("" for _ in range(NETWORK_COUNT)),
        ("" for _ in range(NETWORK_COUNT)),
        np.full(NETWORK_COUNT, 10.0, dtype=np.float64),
    )
    switches = SwitchModel(
        segments,
        (f"CH{index}" for index in range(NETWORK_COUNT)),
        ("TC" for _ in range(NETWORK_COUNT)),
        ("" for _ in range(NETWORK_COUNT)),
        np.arange(NETWORK_COUNT, dtype=np.intp),
        (f"CHAVE-{index:05d}" for index in range(NETWORK_COUNT)),
        ("1" for _ in range(NETWORK_COUNT)),
        ("1" for _ in range(NETWORK_COUNT)),
        ("" for _ in range(NETWORK_COUNT)),
        ("" for _ in range(NETWORK_COUNT)),
        ("" for _ in range(NETWORK_COUNT)),
    )
    loads = LoadModel(
        bars,
        (f"L{index}" for index in range(LOAD_COUNT)),
        np.arange(LOAD_COUNT, dtype=np.intp),
        (f"EXTERNO-{index:06d}" for index in range(LOAD_COUNT)),
        (f"CARGA-{index:06d}" for index in range(LOAD_COUNT)),
        ("10.5" for _ in range(LOAD_COUNT)),
        ("8.2" for _ in range(LOAD_COUNT)),
        ("220" for _ in range(LOAD_COUNT)),
        ("ABC" for _ in range(LOAD_COUNT)),
        ("Y" for _ in range(LOAD_COUNT)),
    )
    return bars, segments, switches, loads


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="retorna erro se as metas do índice ou das consultas não forem atingidas",
    )
    args = parser.parse_args()

    bars, segments, switches, loads = make_models()
    index = GlobalSearchIndex()
    started = time.perf_counter()
    index.set_bars(bars, build_fields=False)
    bar_seconds = time.perf_counter() - started

    started = time.perf_counter()
    index.set_segments(segments, build_fields=False)
    index.set_switches(switches, build_fields=False)
    index.set_loads(loads, build_fields=False)
    network_seconds = time.perf_counter() - started

    started = time.perf_counter()
    for kind, source in (
        ("bar", bars),
        ("segment", segments),
        ("switch", switches),
        ("load", loads),
    ):
        index.install_field_partition(build_field_search_partition(kind, source))
    all_field_index_seconds = time.perf_counter() - started

    query_times_ms: list[float] = []
    for number in range(2_000):
        code_suffix = f"{(number * 47) % BAR_COUNT:06d}"
        query = f"barra-{code_suffix[:4]}"
        started = time.perf_counter()
        result = index.query(query)
        query_times_ms.append((time.perf_counter() - started) * 1_000.0)
        if not result.results:
            raise RuntimeError(f"Consulta de referência sem resultado: {query}")
    p95_ms = statistics.quantiles(query_times_ms, n=100)[94]

    all_field_times_ms: list[float] = []
    for number in range(100):
        query = f"externo-{(number * 977) % LOAD_COUNT:06d}"
        started = time.perf_counter()
        result = index.query_any_field(query)
        all_field_times_ms.append((time.perf_counter() - started) * 1_000.0)
        if not result.results:
            raise RuntimeError(f"Consulta ampla de referência sem resultado: {query}")
    all_field_p95_ms = statistics.quantiles(all_field_times_ms, n=100)[94]

    print(f"Elementos indexados: {len(index):n}")
    print(f"Índice de {BAR_COUNT:n} barras: {bar_seconds:.3f} s")
    print(f"Índice de trechos + chaves: {network_seconds:.3f} s")
    print(f"Consulta prefixada p95: {p95_ms:.3f} ms")
    print(
        "Índice de todas as colunas "
        f"({LOAD_COUNT:n} cargas incluídas): {all_field_index_seconds:.3f} s"
    )
    print(f"Consulta em qualquer coluna p95: {all_field_p95_ms:.3f} ms")

    if args.enforce and (
        bar_seconds > BAR_INDEX_TARGET_SECONDS
        or p95_ms > QUERY_P95_TARGET_MS
        or all_field_p95_ms > ALL_FIELD_QUERY_P95_TARGET_MS
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
