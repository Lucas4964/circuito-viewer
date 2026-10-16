"""Compara o cálculo de D e G do modo Energia com a tabela do ``calcular_dg.py``.

Uso:
    python benchmarks/validate_energy_demand.py REDE.mdb DG.csv [--password-env VAR]

``REDE.mdb`` é o banco da rede; as curvas e categorias são as do programa
(``dados/curvas.json`` e ``dados/categorias_curvas.json``, gravadas por
"Importar banco de curvas…"). ``DG.csv`` é a saída do ``calcular_dg.py`` para o
mesmo banco, em qualquer um dos dois formatos (Excel pt-BR ou CSV padrão).

A senha do banco, quando houver, vem da variável de ambiente indicada por
``--password-env`` ou do cofre do Windows; ela nunca aparece na linha de comando.

Somente leitura. Compara, por UC (tensão + ``CONS_ID``): status, curva de carga,
sorteio, curva de GD, D e G. As diferenças esperadas são as conscientes do
módulo ``energy_demand``: o ``CURVA_ID`` da GD casado com o ``CAT_ID`` e o EP e a
ET da mesma CARGA num trafo só (o que não muda D nem G).
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circuit_viewer.calculation_levels import default_calculation_levels  # noqa: E402
from circuit_viewer.curvas_store import load_curve_categories, load_curves  # noqa: E402
from circuit_viewer.energy_demand import calculate_energy_demands  # noqa: E402
from circuit_viewer.mdb_credentials import load_default_password  # noqa: E402
from circuit_viewer.mdb_engine import MdbPasswordError, open_database  # noqa: E402
from circuit_viewer.mdb_import import load_database  # noqa: E402
from circuit_viewer.model import UtmCrs  # noqa: E402
from circuit_viewer.phase_config import load_phase_configuration  # noqa: E402

RELATIVE_TOLERANCE = 1e-6
ABSOLUTE_TOLERANCE = 1e-6


def read_dg(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    header = text.split("\n", 1)[0]
    excel = header.count(";") > header.count(",")
    rows = csv.DictReader(io.StringIO(text), delimiter=";" if excel else ",")
    return {(row["TENSAO"], row["CONS_ID"]): row for row in rows}


def number(text: str | None) -> float | None:
    if text is None or not str(text).strip():
        return None
    return float(str(text).strip().replace(",", "."))


def close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return (a or 0.0) == (b or 0.0)
    return abs(a - b) <= max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * max(abs(a), abs(b)))


def open_network(path: Path, password_env: str | None):  # noqa: ANN201
    candidates = [None]
    if password_env and os.environ.get(password_env):
        candidates.append(os.environ[password_env])
    vault = load_default_password()
    if vault:
        candidates.append(vault)
    for password in candidates:
        try:
            with open_database(path, password) as database:
                return load_database(
                    database,
                    UtmCrs(21, northern=False),
                    source_path=str(path),
                    scale=10.0,
                    phase_configuration=load_phase_configuration(),
                )
        except MdbPasswordError:
            continue
    raise SystemExit("Banco protegido: informe a senha por --password-env ou no cofre do Windows.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("network", type=Path, help="rede.mdb")
    parser.add_argument("dg", type=Path, help="CSV do calcular_dg.py")
    parser.add_argument("--password-env", help="variável de ambiente com a senha do banco")
    parser.add_argument("--top", type=int, default=8, help="diferenças listadas por coluna")
    arguments = parser.parse_args()

    result = open_network(arguments.network, arguments.password_env)
    if result.consumers is None:
        raise SystemExit(f"Sem dados de UC no banco: {result.consumers_error or 'BT_ET ausente'}")
    curves = load_curves()
    categories = load_curve_categories()
    if curves.issue or categories.issue:
        print("Aviso nas curvas:", curves.issue or categories.issue)
    model = calculate_energy_demands(
        result.consumers,
        curves.curves,
        categories.categories,
        (default_calculation_levels(),) * len(result.consumers.records),
    )
    reference = read_dg(arguments.dg)

    compared = 0
    missing: list[tuple[str, str]] = []
    differences: dict[str, list[str]] = {name: [] for name in (
        "STATUS_DG", "CURVA_CARGA_ID", "SORTEIO_PCT", "CURVA_GD_ID", "D_KW", "G_KW",
    )}
    for transformer in model.transformers:
        if transformer is None:
            continue
        for demand in transformer.consumers:
            consumer = demand.consumer
            if consumer.kind == "IP":
                continue
            key = (consumer.kind, consumer.consumer_id)
            row = reference.get(key)
            if row is None:
                missing.append(key)
                continue
            compared += 1
            label = f"{consumer.kind} {consumer.consumer_id} ({consumer.code})"
            if demand.status != row["STATUS_DG"]:
                differences["STATUS_DG"].append(f"{label}: {demand.status} x {row['STATUS_DG']}")
            load_curve = demand.load_curve.category_id if demand.load_curve else ""
            if load_curve != (row["CURVA_CARGA_ID"] or ""):
                differences["CURVA_CARGA_ID"].append(f"{label}: {load_curve or '-'} x {row['CURVA_CARGA_ID'] or '-'}")
            if not close(demand.draw_percent, number(row["SORTEIO_PCT"])):
                differences["SORTEIO_PCT"].append(f"{label}: {demand.draw_percent} x {row['SORTEIO_PCT']}")
            generation_curve = demand.generation_curve.category_id if demand.generation_curve else ""
            if generation_curve != (row["CURVA_GD_ID"] or ""):
                differences["CURVA_GD_ID"].append(f"{label}: {generation_curve or '-'} x {row['CURVA_GD_ID'] or '-'}")
            if not close(demand.demand_kw, number(row["D_KW"])):
                differences["D_KW"].append(f"{label}: {demand.demand_kw} x {row['D_KW']}")
            if not close(demand.generation_kw, number(row["G_KW"])):
                differences["G_KW"].append(f"{label}: {demand.generation_kw} x {row['G_KW']}")

    lighting = sum(
        1 for transformer in model.transformers if transformer
        for demand in transformer.consumers if demand.consumer.kind == "IP"
    )
    print(f"UCs comparadas: {compared:n} | sem par no CSV: {len(missing):n} | IP (fora do script): {lighting:n}")
    print("Status do modo Energia:", dict(sorted(model.status_counts().items())))
    print("Status no CSV:", dict(sorted(Counter(row["STATUS_DG"] for row in reference.values()).items())))
    for name, items in differences.items():
        print(f"{name}: {len(items):n} diferença(s)")
        for item in items[: arguments.top]:
            print("   ", item)
    if missing:
        print("Sem par no CSV (primeiros):", missing[: arguments.top])
    if model.issues:
        print("Ocorrências:", model.issues)


if __name__ == "__main__":
    main()
