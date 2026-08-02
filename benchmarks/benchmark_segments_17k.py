"""Benchmark da importação e renderização de aproximadamente 17 mil trechos."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circuit_viewer.model import CircuitModel, UtmCrs
from circuit_viewer.phase_config import PHASE_COLORS, load_phase_configuration
from circuit_viewer.segment_import import load_segments_csv


SEGMENT_COUNT = 17_000
TARGET_SECONDS = 5.0
SELECTION_P95_TARGET_SECONDS = 0.010


def create_bars() -> CircuitModel:
    columns = 131
    count = SEGMENT_COUNT + 1
    return CircuitModel(
        [f"B{i}" for i in range(count)],
        [""] * count,
        [500_000.0 + (i % columns) * 10.0 for i in range(count)],
        [8_000_000.0 + (i // columns) * 10.0 for i in range(count)],
        UtmCrs(21, northern=False),
    )


def create_segments_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        target.write(
            "TRECHO_ID;CODIGO;FASES2;BARRA1_ID;BARRA2_ID;"
            "ARRANJO_ID;CABOF_ID;CABON_ID;COMPR\n"
        )
        phase_values = ("1", "2", "13", "X")
        for index in range(SEGMENT_COUNT):
            target.write(
                f"T{index};C{index};{phase_values[index % 4]};"
                f"B{index};B{index + 1};A1;CF1;CN1;10\n"
            )


def render_network(model, phase_styles) -> tuple[float, float, float, float]:  # noqa: ANN001
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QApplication, QGraphicsScene

    from circuit_viewer.graphics import LineNetworkItem

    app = QApplication.instance() or QApplication([])
    started = time.perf_counter()
    item = LineNetworkItem(model)
    build_seconds = time.perf_counter() - started
    started = time.perf_counter()
    item.set_phase_rendering(None, phase_styles, PHASE_COLORS)
    phase_rebuild_seconds = time.perf_counter() - started
    scene = QGraphicsScene()
    scene.addItem(item)
    image = QImage(1920, 1080, QImage.Format.Format_ARGB32_Premultiplied)
    bounds = model.bounds
    source = QRectF(bounds.left, -bounds.bottom, max(bounds.width, 1), max(bounds.height, 1))
    times: list[float] = []
    for _ in range(2):
        image.fill(0xFFF7F7F7)
        painter = QPainter(image)
        started = time.perf_counter()
        scene.render(painter, QRectF(0, 0, 1920, 1080), source)
        painter.end()
        times.append(time.perf_counter() - started)
    app.processEvents()
    return build_seconds, phase_rebuild_seconds, times[0], times[1]


def benchmark_selection(model, query_count: int = 1_000) -> float:  # noqa: ANN001
    rng = np.random.default_rng(20260802)
    indices = rng.integers(0, len(model), query_count)
    bars = model.bars
    x1 = bars.x[model.start_indices[indices]]
    y1 = bars.y[model.start_indices[indices]]
    x2 = bars.x[model.end_indices[indices]]
    y2 = bars.y[model.end_indices[indices]]
    query_x = (x1 + x2) * 0.5 + rng.uniform(-1.0, 1.0, query_count)
    query_y = (y1 + y2) * 0.5 + rng.uniform(-1.0, 1.0, query_count)
    times = np.empty(query_count, dtype=np.float64)
    for position, (x, y) in enumerate(zip(query_x, query_y, strict=True)):
        started = time.perf_counter()
        model.spatial_index.nearest(float(x), float(y), 5.0)
        times[position] = time.perf_counter() - started
    return float(np.percentile(times, 95))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    bars = create_bars()
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "trechos_17k.csv"
        create_segments_csv(path)
        started = time.perf_counter()
        result = load_segments_csv(path, bars)
        import_seconds = time.perf_counter() - started
    started = time.perf_counter()
    phase_classification = load_phase_configuration().classify(result.model.phases)
    classification_seconds = time.perf_counter() - started
    build_seconds, phase_rebuild_seconds, first_render, cached_render = render_network(
        result.model,
        phase_classification.style_indices,
    )
    selection_p95 = benchmark_selection(result.model)
    print(f"Trechos: {len(result.model):n}")
    print(f"Importação + índice: {import_seconds:.3f} s")
    print(f"Compilação do caminho: {build_seconds:.3f} s")
    print(f"Classificação por fases: {classification_seconds:.3f} s")
    print(f"Reconstrução em quatro categorias: {phase_rebuild_seconds:.3f} s")
    print(f"Primeiro render 1920×1080: {first_render:.3f} s")
    print(f"Render em cache 1920×1080: {cached_render:.3f} s")
    print(f"Seleção p95 (1.000 consultas): {selection_p95 * 1_000:.3f} ms")
    if args.enforce:
        if max(
            import_seconds,
            classification_seconds,
            build_seconds,
            phase_rebuild_seconds,
            first_render,
        ) > TARGET_SECONDS:
            return 1
        if selection_p95 > SELECTION_P95_TARGET_SECONDS:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
