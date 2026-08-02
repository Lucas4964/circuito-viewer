"""Benchmark de topologia e filtragem com 100 mil barras e 17 mil trechos."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication

from circuit_viewer.circuit_import import load_circuits_csv
from circuit_viewer.graphics import BarsOverviewItem, LineNetworkItem, SwitchNetworkItem
from circuit_viewer.model import (
    CircuitModel,
    CircuitVisibilityController,
    LineNetworkModel,
    SwitchModel,
    UtmCrs,
)


BAR_COUNT = 100_000
SEGMENT_COUNT = 17_000
CIRCUIT_COUNT = 10
IMPORT_TARGET_SECONDS = 5.0
UPDATE_TARGET_SECONDS = 0.5


def create_models() -> tuple[CircuitModel, LineNetworkModel, SwitchModel]:
    bars = CircuitModel(
        [f"B{index}" for index in range(BAR_COUNT)],
        [""] * BAR_COUNT,
        [500_000.0 + (index % 1_000) * 10.0 for index in range(BAR_COUNT)],
        [8_000_000.0 + (index // 1_000) * 10.0 for index in range(BAR_COUNT)],
        UtmCrs(21, northern=False),
    )
    segments = LineNetworkModel(
        bars,
        [f"T{index}" for index in range(SEGMENT_COUNT)],
        [""] * SEGMENT_COUNT,
        ["ABC"] * SEGMENT_COUNT,
        list(range(SEGMENT_COUNT)),
        list(range(1, SEGMENT_COUNT + 1)),
        [""] * SEGMENT_COUNT,
        [""] * SEGMENT_COUNT,
        [""] * SEGMENT_COUNT,
        [10.0] * SEGMENT_COUNT,
    )
    switch_indices = [
        (index + 1) * SEGMENT_COUNT // (CIRCUIT_COUNT + 1)
        for index in range(CIRCUIT_COUNT)
    ]
    switches = SwitchModel(
        segments,
        [f"CH{index}" for index in range(CIRCUIT_COUNT)],
        ["TIPO"] * CIRCUIT_COUNT,
        [f"C{index}" for index in range(CIRCUIT_COUNT)],
        switch_indices,
        [""] * CIRCUIT_COUNT,
        ["1"] * CIRCUIT_COUNT,
        ["1"] * CIRCUIT_COUNT,
        [""] * CIRCUIT_COUNT,
        [""] * CIRCUIT_COUNT,
        [""] * CIRCUIT_COUNT,
    )
    return bars, segments, switches


def create_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        target.write("CIRC_ID;BARRA_ID;CODIGO;VNOM\n")
        for index in range(CIRCUIT_COUNT):
            root = index * (SEGMENT_COUNT // CIRCUIT_COUNT)
            target.write(f"C{index};B{root};ALIM-{index};13.8\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    bars, segments, switches = create_models()
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "circuitos.csv"
        create_csv(path)
        started = time.perf_counter()
        result = load_circuits_csv(path, segments, switches)
        association_seconds = time.perf_counter() - started

    started = time.perf_counter()
    controller = CircuitVisibilityController(result.model)
    palette_seconds = time.perf_counter() - started
    bars_item = BarsOverviewItem(bars)
    lines_item = LineNetworkItem(segments)
    lines_item.set_switch_segment_indices(switches.segment_indices)
    switches_item = SwitchNetworkItem(switches)
    started = time.perf_counter()
    lines_item.set_circuit_rendering(
        controller.segment_visible_mask,
        controller.segment_style_indices,
        controller.colors,
    )
    switches_item.set_circuit_rendering(
        controller.segment_visible_mask,
        controller.segment_style_indices,
        controller.colors,
    )
    categorization_seconds = time.perf_counter() - started

    line_revision = lines_item.geometry_revision
    switch_revision = switches_item.geometry_revision
    controller.set_color(0, "#123456")
    started = time.perf_counter()
    lines_item.set_circuit_rendering(
        controller.segment_visible_mask,
        controller.segment_style_indices,
        controller.colors,
    )
    switches_item.set_circuit_rendering(
        controller.segment_visible_mask,
        controller.segment_style_indices,
        controller.colors,
    )
    color_update_seconds = time.perf_counter() - started
    assert lines_item.geometry_revision == line_revision
    assert switches_item.geometry_revision == switch_revision

    for index in range(len(result.model)):
        controller.set_visible(index, False)
    started = time.perf_counter()
    bars_item.set_visibility_mask(controller.bar_visible_mask)
    lines_item.set_circuit_rendering(
        controller.segment_visible_mask,
        controller.segment_style_indices,
        controller.colors,
    )
    switches_item.set_circuit_rendering(
        controller.segment_visible_mask,
        controller.segment_style_indices,
        controller.colors,
    )
    update_seconds = time.perf_counter() - started
    app.processEvents()

    print(f"Barras: {len(bars):n}")
    print(f"Trechos: {len(segments):n}")
    print(f"Circuitos: {len(result.model):n}")
    print(f"Paleta e estado visual: {palette_seconds:.3f} s")
    print(f"Categorizacao inicial: {categorization_seconds:.3f} s")
    print(f"Troca de cor sem geometria: {color_update_seconds:.6f} s")
    print(f"Importação + BFS: {association_seconds:.3f} s")
    print(f"Atualização das geometrias agregadas: {update_seconds:.3f} s")
    if args.enforce and (
        association_seconds > IMPORT_TARGET_SECONDS
        or palette_seconds + categorization_seconds > UPDATE_TARGET_SECONDS
        or color_update_seconds > UPDATE_TARGET_SECONDS
        or update_seconds > UPDATE_TARGET_SECONDS
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
