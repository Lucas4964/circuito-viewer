"""Benchmark da composição de várias fontes de 100 mil barras cada.

Mede as três coisas que decidem se somar bancos é viável: restringir uma fonte
aos circuitos escolhidos, compor N fontes numa cadeia só, e — a regressão que
mais importa — que o catálogo composto seja montado por ``__init__``, que só
recalcula índices, e não por ``build()``, que refaz um BFS por circuito.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    CircuitVisibilityController,
    LineNetworkModel,
    LoadModel,
    SwitchModel,
    UtmCrs,
)
from circuit_viewer.source_composition import (
    SourceDataset,
    compose,
    restrict_to_circuits,
)


BAR_COUNT = 100_000
SEGMENT_COUNT = 17_000
LOAD_COUNT = 100_000
CIRCUIT_COUNT = 10
SOURCE_COUNT = 5

COMPOSE_TARGET_SECONDS = 12.0
RESTRICT_TARGET_SECONDS = 5.0
#: Montar o catálogo a partir de associações prontas precisa ser ao menos isto
#: mais rápido que refazer o traçado. Abaixo disso, a composição virou retraçado.
RETRACE_SPEEDUP_TARGET = 20.0


def build_source(tag: str) -> SourceDataset:
    """Uma fonte sintética com ids próprios, disjunta das demais."""

    bars = CircuitModel(
        [f"{tag}-B{index}" for index in range(BAR_COUNT)],
        [f"{tag}-COD{index}" for index in range(BAR_COUNT)],
        500_000.0 + np.remainder(np.arange(BAR_COUNT), 1_000) * 10.0,
        8_000_000.0 + (np.arange(BAR_COUNT) // 1_000) * 10.0,
        UtmCrs(21, northern=False),
    )
    segments = LineNetworkModel(
        bars,
        [f"{tag}-T{index}" for index in range(SEGMENT_COUNT)],
        [""] * SEGMENT_COUNT,
        ["ABC"] * SEGMENT_COUNT,
        np.arange(SEGMENT_COUNT),
        np.arange(1, SEGMENT_COUNT + 1),
        [""] * SEGMENT_COUNT,
        [""] * SEGMENT_COUNT,
        [""] * SEGMENT_COUNT,
        np.full(SEGMENT_COUNT, 10.0),
    )
    loads = LoadModel(
        bars,
        [f"{tag}-CA{index}" for index in range(LOAD_COUNT)],
        np.arange(LOAD_COUNT) % BAR_COUNT,
        [""] * LOAD_COUNT,
        [f"{tag}-CARGA{index}" for index in range(LOAD_COUNT)],
        ["30"] * LOAD_COUNT,
        ["30"] * LOAD_COUNT,
        ["220"] * LOAD_COUNT,
        ["13"] * LOAD_COUNT,
        ["2"] * LOAD_COUNT,
    )
    switch_indices = [
        (index + 1) * SEGMENT_COUNT // (CIRCUIT_COUNT + 1)
        for index in range(CIRCUIT_COUNT)
    ]
    switches = SwitchModel(
        segments,
        [f"{tag}-CH{index}" for index in range(CIRCUIT_COUNT)],
        ["TIPO"] * CIRCUIT_COUNT,
        [f"{tag}-C{index}" for index in range(CIRCUIT_COUNT)],
        switch_indices,
        [""] * CIRCUIT_COUNT,
        ["1"] * CIRCUIT_COUNT,
        ["1"] * CIRCUIT_COUNT,
        [""] * CIRCUIT_COUNT,
        [""] * CIRCUIT_COUNT,
        [""] * CIRCUIT_COUNT,
    )
    definitions = [
        CircuitDefinition(
            circuit_id=f"{tag}-C{index}",
            root_bar_id=f"{tag}-B{index * (SEGMENT_COUNT // CIRCUIT_COUNT)}",
            code=f"ALIM-{index}",
            nominal_voltage="13.8",
        )
        for index in range(CIRCUIT_COUNT)
    ]
    catalog = CircuitCatalogModel.build(segments, switches, definitions)
    return SourceDataset(
        tag=tag,
        name=f"rede-{tag}.mdb",
        source_path=f"C:/dados/rede-{tag}.mdb",
        bars=bars,
        segments=segments,
        loads=loads,
        switches=switches,
        catalog=catalog,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--sources", type=int, default=SOURCE_COUNT)
    args = parser.parse_args()

    started = time.perf_counter()
    sources = [build_source(f"F{index + 1}") for index in range(args.sources)]
    build_seconds = time.perf_counter() - started
    print(f"geração de {args.sources} fontes: {build_seconds:.2f} s")

    started = time.perf_counter()
    restricted = restrict_to_circuits(
        sources[0], [item.circuit_id for item in sources[0].catalog.definitions[:3]]
    )
    restrict_seconds = time.perf_counter() - started
    print(
        f"restrição a 3 de {CIRCUIT_COUNT} circuitos: {restrict_seconds:.2f} s "
        f"({len(restricted.bars):n} barras de {BAR_COUNT:n})"
    )

    compose_seconds = 0.0
    for count in range(2, args.sources + 1):
        started = time.perf_counter()
        composed = compose(sources[:count])
        elapsed = time.perf_counter() - started
        compose_seconds = elapsed
        print(
            f"composição de {count} fontes: {elapsed:.2f} s "
            f"({len(composed.bars):n} barras, {len(composed.catalog):n} circuitos)"
        )

    composed = compose(sources)

    # O ponto do desenho todo: montar o catálogo a partir das associações já
    # deslocadas, sem refazer um BFS por circuito.
    started = time.perf_counter()
    CircuitCatalogModel(
        composed.segments,
        composed.switches,
        composed.catalog.definitions,
        composed.catalog.memberships,
    )
    shifted_seconds = time.perf_counter() - started
    started = time.perf_counter()
    CircuitCatalogModel.build(
        composed.segments, composed.switches, composed.catalog.definitions
    )
    retrace_seconds = time.perf_counter() - started
    speedup = retrace_seconds / max(shifted_seconds, 1e-9)
    print(
        f"catálogo por associações prontas: {shifted_seconds:.3f} s; "
        f"por traçado: {retrace_seconds:.3f} s ({speedup:.0f}× mais rápido)"
    )

    started = time.perf_counter()
    CircuitVisibilityController(composed.catalog)
    visibility_seconds = time.perf_counter() - started
    print(f"controlador de visibilidade: {visibility_seconds:.2f} s")

    if not args.enforce:
        return 0
    failures: list[str] = []
    if compose_seconds > COMPOSE_TARGET_SECONDS:
        failures.append(
            f"compor {args.sources} fontes levou {compose_seconds:.2f} s "
            f"(limite {COMPOSE_TARGET_SECONDS:.0f} s)"
        )
    if restrict_seconds > RESTRICT_TARGET_SECONDS:
        failures.append(
            f"restringir levou {restrict_seconds:.2f} s "
            f"(limite {RESTRICT_TARGET_SECONDS:.0f} s)"
        )
    if speedup < RETRACE_SPEEDUP_TARGET:
        failures.append(
            f"montar o catálogo por associações prontas foi só {speedup:.0f}× "
            f"mais rápido que retraçar (mínimo {RETRACE_SPEEDUP_TARGET:.0f}×)"
        )
    for line in failures:
        print(f"FALHOU: {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
