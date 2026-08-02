"""Benchmark reproduzível da importação e da visão geral com 100 mil barras."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circuit_viewer.csv_import import load_csv
from circuit_viewer.model import UtmCrs


BAR_COUNT = 100_000
IMPORT_TARGET_SECONDS = 5.0


def create_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        target.write("BARRA_ID;CODIGO;X;Y\n")
        for index in range(BAR_COUNT):
            x = 500_000.0 + (index % 1_000) * 10.0
            y = 8_000_000.0 + (index // 1_000) * 10.0
            target.write(f"B{index};C{index};{x:.2f};{y:.2f}\n")


def render_overview(model) -> float:  # noqa: ANN001
    try:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtWidgets import QApplication, QGraphicsScene

        from circuit_viewer.graphics import BarsOverviewItem
    except ModuleNotFoundError:
        return -1.0

    app = QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    scene.addItem(BarsOverviewItem(model))
    image = QImage(1920, 1080, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0xFFF7F7F7)
    bounds = model.bounds
    source = QRectF(bounds.left, -bounds.bottom, max(bounds.width, 1), max(bounds.height, 1))
    painter = QPainter(image)
    started = time.perf_counter()
    scene.render(painter, QRectF(0, 0, 1920, 1080), source)
    painter.end()
    app.processEvents()
    return time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="retorna erro quando a meta de importação não for atingida",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path = Path(temp_dir) / "barras_100k.csv"
        create_csv(csv_path)
        started = time.perf_counter()
        result = load_csv(csv_path, UtmCrs(21, northern=False))
        import_seconds = time.perf_counter() - started
        render_seconds = render_overview(result.model)

    print(f"Barras: {len(result.model):n}")
    print(f"Importação + índice: {import_seconds:.3f} s")
    if render_seconds >= 0:
        print(f"Render agregado 1920×1080: {render_seconds:.3f} s")
    else:
        print("Render agregado: não executado (PyQt6 ausente)")

    if args.enforce and import_seconds > IMPORT_TARGET_SECONDS:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
