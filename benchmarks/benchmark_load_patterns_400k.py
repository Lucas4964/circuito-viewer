from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from pathlib import Path

from circuit_viewer.load_pattern_import import load_load_patterns_csv
from circuit_viewer.model import CircuitModel, LoadModel, UtmCrs


LOAD_COUNT = 100_000
PATTERN_COUNT = LOAD_COUNT * 4
IMPORT_TARGET_SECONDS = 5.0
QUERY_P95_TARGET_MS = 1.0


def build_loads() -> LoadModel:
    bars = CircuitModel(
        [f"B{index}" for index in range(LOAD_COUNT)],
        [""] * LOAD_COUNT,
        [500_000.0 + index % 1_000 for index in range(LOAD_COUNT)],
        [8_000_000.0 + index // 1_000 for index in range(LOAD_COUNT)],
        UtmCrs(21, northern=False),
    )
    return LoadModel(
        bars,
        [f"L{index}" for index in range(LOAD_COUNT)],
        range(LOAD_COUNT),
        [""] * LOAD_COUNT,
        [""] * LOAD_COUNT,
        [""] * LOAD_COUNT,
        [""] * LOAD_COUNT,
        [""] * LOAD_COUNT,
        [""] * LOAD_COUNT,
        [""] * LOAD_COUNT,
    )


def write_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        target.write("CARGA_ID;NPAT;PD;PE;PF;QD;QE;QF\n")
        for load_index in range(LOAD_COUNT):
            for npat in range(4):
                value = load_index / 10_000.0 + npat
                target.write(
                    f"L{load_index};{npat};{value:.8f};0;0;{value / 10:.8f};0;0\n"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()

    loads = build_loads()
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "patamares.csv"
        write_csv(path)
        started = time.perf_counter()
        result = load_load_patterns_csv(path, loads)
        import_seconds = time.perf_counter() - started

    query_times_ms: list[float] = []
    checksum = 0
    for query_index in range(10_000):
        started = time.perf_counter()
        records = result.model.records_for_load(query_index % LOAD_COUNT)
        query_times_ms.append((time.perf_counter() - started) * 1_000.0)
        checksum += records[0].npat
    p95_ms = statistics.quantiles(query_times_ms, n=100)[94]

    print(f"Cargas com patamares: {len(result.model):n}")
    print(f"Registros complementares: {result.model.record_count:n}")
    print(f"Importação + associação: {import_seconds:.3f} s")
    print(f"Consulta por carga p95: {p95_ms:.6f} ms")
    if checksum != 0:
        raise AssertionError(checksum)
    if args.enforce and (
        import_seconds > IMPORT_TARGET_SECONDS
        or p95_ms > QUERY_P95_TARGET_MS
        or result.model.record_count != PATTERN_COUNT
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
