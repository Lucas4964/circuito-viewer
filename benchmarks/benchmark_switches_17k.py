"""Benchmark do pior caso: todos os 17 mil trechos modelados como chaves."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark_segments_17k import SEGMENT_COUNT, create_bars, create_segments_csv
from circuit_viewer.segment_import import load_segments_csv
from circuit_viewer.switch_import import load_switches_csv


TARGET_SECONDS = 0.5


def create_switches_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        target.write(
            "CHAVE_ID;TIPOCHV_ID;CIRC_ID;TRECHO_ID;CODIGO;ESTADO;"
            "ESTADO_NORMAL;CORN;ELO;ELO_TIPO\n"
        )
        for index in range(SEGMENT_COUNT):
            target.write(
                f"CH{index};TC;CIR1;T{index};C{index};A;F;N;E{index};FUSIVEL\n"
            )


def render_models(segments, switches) -> tuple[float, float]:  # noqa: ANN001
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QApplication, QGraphicsScene

    from circuit_viewer.graphics import LineNetworkItem, SwitchNetworkItem

    app = QApplication.instance() or QApplication([])
    started = time.perf_counter()
    switch_item = SwitchNetworkItem(switches)
    build_seconds = time.perf_counter() - started
    scene = QGraphicsScene()
    scene.addItem(LineNetworkItem(segments))
    scene.addItem(switch_item)
    image = QImage(1920, 1080, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0xFFF7F7F7)
    bounds = segments.bounds
    source = QRectF(
        bounds.left,
        -bounds.bottom,
        max(bounds.width, 1),
        max(bounds.height, 1),
    )
    painter = QPainter(image)
    started = time.perf_counter()
    scene.render(painter, QRectF(0, 0, 1920, 1080), source)
    painter.end()
    render_seconds = time.perf_counter() - started
    app.processEvents()
    return build_seconds, render_seconds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    bars = create_bars()
    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        segment_path = directory / "trechos_17k.csv"
        switch_path = directory / "chaves_17k.csv"
        create_segments_csv(segment_path)
        create_switches_csv(switch_path)
        segments = load_segments_csv(segment_path, bars).model
        started = time.perf_counter()
        switches = load_switches_csv(switch_path, segments).model
        import_seconds = time.perf_counter() - started

    build_seconds, render_seconds = render_models(segments, switches)
    print(f"Chaves: {len(switches):n}")
    print(f"Importação + associação: {import_seconds:.3f} s")
    print(f"Compilação da camada vermelha: {build_seconds:.3f} s")
    print(f"Render combinado 1920×1080: {render_seconds:.3f} s")
    if args.enforce and max(import_seconds, build_seconds, render_seconds) > TARGET_SECONDS:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
