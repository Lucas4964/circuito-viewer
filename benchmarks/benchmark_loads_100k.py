from __future__ import annotations

import argparse
import os
import statistics
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication, QGraphicsScene

from circuit_viewer.graphics import DiagramView, LoadVirtualizer
from circuit_viewer.load_import import load_loads_csv
from circuit_viewer.model import CircuitModel, UtmCrs
from circuit_viewer.search import GlobalSearchIndex


LOAD_COUNT = 100_000
IMPORT_TARGET_SECONDS = 5.0
LAYER_TARGET_SECONDS = 5.0
SEARCH_INDEX_TARGET_SECONDS = 1.0
QUERY_P95_TARGET_MS = 5.0


def write_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        target.write(
            "CARGA_ID;BARRA_ID;EXTERN_ID;CODIGO;SNOM;SADM;"
            "VLINHASEC;FASES2;TIPO_LIG\n"
        )
        for index in range(LOAD_COUNT):
            target.write(
                f"L{index};B{index};;CARGA-{index};10;8;220;ABC;Y\n"
            )


def build_bars() -> CircuitModel:
    return CircuitModel(
        [f"B{index}" for index in range(LOAD_COUNT)],
        [""] * LOAD_COUNT,
        [500_000.0 + index % 1_000 for index in range(LOAD_COUNT)],
        [8_000_000.0 + index // 1_000 for index in range(LOAD_COUNT)],
        UtmCrs(21, northern=False),
    )


def benchmark_layer(model) -> tuple[float, float]:  # noqa: ANN001
    app = QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    view = DiagramView(scene)
    view.resize(1_920, 1_080)
    view.set_model(model.bars)
    view.set_load_model(model)
    layer = LoadVirtualizer(scene, view)
    started = time.perf_counter()
    layer.reset_model(model)
    build_seconds = time.perf_counter() - started

    image = QImage(1_920, 1_080, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    started = time.perf_counter()
    scene.render(painter, QRectF(0, 0, 1_920, 1_080), scene.itemsBoundingRect())
    render_seconds = time.perf_counter() - started
    painter.end()
    app.processEvents()
    view.close()
    return build_seconds, render_seconds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()

    bars = build_bars()
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "cargas.csv"
        write_csv(path)
        started = time.perf_counter()
        result = load_loads_csv(path, bars)
        import_seconds = time.perf_counter() - started

    search = GlobalSearchIndex()
    started = time.perf_counter()
    search.set_loads(result.model)
    search_seconds = time.perf_counter() - started
    query_times_ms: list[float] = []
    for index in range(1_000):
        started = time.perf_counter()
        search.query(f"CARGA-{index % 100}")
        query_times_ms.append((time.perf_counter() - started) * 1_000.0)
    p95_ms = statistics.quantiles(query_times_ms, n=100)[94]
    layer_seconds, render_seconds = benchmark_layer(result.model)

    print(f"Importação + associação + índice espacial: {import_seconds:.3f} s")
    print(f"Índice da busca: {search_seconds:.3f} s")
    print(f"Consulta prefixada p95: {p95_ms:.3f} ms")
    print(f"Layout + camada agregada: {layer_seconds:.3f} s")
    print(f"Render agregado 1920×1080: {render_seconds:.3f} s")

    if args.enforce and (
        import_seconds > IMPORT_TARGET_SECONDS
        or search_seconds > SEARCH_INDEX_TARGET_SECONDS
        or p95_ms > QUERY_P95_TARGET_MS
        or layer_seconds > LAYER_TARGET_SECONDS
        or render_seconds > LAYER_TARGET_SECONDS
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
