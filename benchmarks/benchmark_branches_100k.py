"""Benchmark da análise de ramais em uma rede sintética de grande porte."""

from __future__ import annotations

import argparse
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from circuit_viewer.branch_analysis import analyze_branches
from circuit_viewer.graphics import BranchHighlightOverlayItem
from circuit_viewer.model import (
    CircuitCatalogModel,
    CircuitDefinition,
    CircuitModel,
    LineNetworkModel,
    LoadModel,
    UtmCrs,
)
from circuit_viewer.phase_config import PhaseConfiguration, PhaseMappingEntry


CIRCUIT_COUNT = 100
SEGMENTS_PER_CIRCUIT = 1_000
ANALYSIS_TARGET_SECONDS = 5.0
HIGHLIGHT_TARGET_SECONDS = 0.1


def build_models():  # noqa: ANN201
    bar_ids: list[str] = []
    x_values: list[float] = []
    y_values: list[float] = []
    starts: list[int] = []
    ends: list[int] = []
    phases: list[str] = []
    definitions: list[CircuitDefinition] = []
    for circuit in range(CIRCUIT_COUNT):
        base = len(bar_ids)
        definitions.append(CircuitDefinition(f"C{circuit}", f"B{base}", "", ""))
        for local_bar in range(SEGMENTS_PER_CIRCUIT + 1):
            bar_ids.append(f"B{base + local_bar}")
            x_values.append(500_000.0 + local_bar)
            y_values.append(8_000_000.0 + circuit * 10.0)
        for local_segment in range(SEGMENTS_PER_CIRCUIT):
            starts.append(base + local_segment)
            ends.append(base + local_segment + 1)
            phases.append("DEF" if local_segment < 500 else "D")
    bars = CircuitModel(
        bar_ids,
        [""] * len(bar_ids),
        x_values,
        y_values,
        UtmCrs(21, northern=False),
    )
    segments = LineNetworkModel(
        bars,
        [f"T{index}" for index in range(len(starts))],
        [""] * len(starts),
        phases,
        starts,
        ends,
        [""] * len(starts),
        [""] * len(starts),
        [""] * len(starts),
        [1.0] * len(starts),
    )
    catalog = CircuitCatalogModel.build(segments, None, definitions)
    load_count = 100_000
    loads = LoadModel(
        bars,
        [f"L{index}" for index in range(load_count)],
        list(range(1, load_count + 1)),
        [""] * load_count,
        [""] * load_count,
        [""] * load_count,
        [""] * load_count,
        [""] * load_count,
        [""] * load_count,
        [""] * load_count,
    )
    configuration = PhaseConfiguration(
        (
            PhaseMappingEntry("d", "D", 1),
            PhaseMappingEntry("def", "DEF", 3),
        )
    )
    return catalog, loads, configuration


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])

    catalog, loads, configuration = build_models()
    started = time.perf_counter()
    result = analyze_branches(catalog, configuration, loads)
    analysis_seconds = time.perf_counter() - started

    overlay = BranchHighlightOverlayItem()
    started = time.perf_counter()
    overlay.bind(catalog.segments, result.records[0].segment_indices)
    highlight_seconds = time.perf_counter() - started

    print(f"Barras: {len(catalog.segments.bars):n}")
    print(f"Trechos: {len(catalog.segments):n}")
    print(f"Cargas: {len(loads):n}")
    print(f"Circuitos: {len(catalog):n}")
    print(f"Ramais encontrados: {len(result.records):n}")
    print(f"Análise: {analysis_seconds:.3f} s")
    print(f"Construção do destaque: {highlight_seconds * 1_000:.3f} ms")
    app.processEvents()
    if args.enforce and (
        analysis_seconds > ANALYSIS_TARGET_SECONDS
        or highlight_seconds > HIGHLIGHT_TARGET_SECONDS
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
