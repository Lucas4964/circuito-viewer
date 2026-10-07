"""Compara o fluxo de potência de um export OpenDSS com os relatórios do Interplan.

Uso:
    python benchmarks/validate_interplan_powerflow.py PASTA_EXPORT PASTA_INTERPLAN [--top N]

``PASTA_EXPORT`` é a pasta gravada pela exportação (a do ``<CODIGO>_Master.dss``).
``PASTA_INTERPLAN`` é a que contém as subpastas ``TENSAO``, ``CORRENTE`` e
``POTENCIA`` dos relatórios de fluxo do Interplan (``VF.csv``,
``TRECHO_CORR.csv``, ``TRECHO_P.csv``, ``CHAVES_P.csv``, ``CHAVES_Q.csv``).

Somente leitura: o export é copiado para uma pasta temporária de caminho ASCII
antes do ``Compile``, porque o OpenDSS só aceita caminhos ASCII e muda o
diretório de trabalho para a pasta do master. Cada patamar é resolvido à parte
(``number=1``, ``time=(h,0)``), na mesma convenção do fluxo interno: a hora
``h+1`` do ``LoadShape`` diário é o NPAT ``h``.

Os relatórios do Interplan têm três casas em |V| (pu) e uma em ângulo, então um
modelo idêntico aparece com erro de até 0,05% e 0,05° — é o piso da comparação.
"""

from __future__ import annotations

import argparse
import math
import shutil
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circuit_viewer.opendss_engine import acquire_engine, ascii_workspace  # noqa: E402

CSV_ENCODING = "latin-1"
# Abaixo disto o Interplan relata a fase como inexistente (0,000), não como
# tensão baixa; a corrente abaixo de meio ampère tem erro relativo dominado pelo
# arredondamento de duas casas do relatório.
MIN_VOLTAGE_PU = 0.01
MIN_CURRENT_A = 0.5
MIN_FLOW_KW = 5.0


def _rows(path: Path) -> list[list[str]]:
    """Linhas de dados de um relatório ``;``, sem o ``;`` final."""

    lines = path.read_text(encoding=CSV_ENCODING).splitlines()
    return [line.rstrip(";").split(";") for line in lines[1:] if line.strip()]


@dataclass
class InterplanResults:
    """Os relatórios do Interplan indexados por nome (casefold) e patamar."""

    periods: list[str]
    # (barra, patamar) -> três fasores em pu, fases D/E/F
    voltages: dict[tuple[str, str], tuple[complex, complex, complex]] = field(default_factory=dict)
    # (trecho, patamar) -> correntes D/E/F em A
    currents: dict[tuple[str, str], tuple[float, float, float]] = field(default_factory=dict)
    # (trecho, patamar) -> (barra 1, P12 trifásica, P21 trifásica) em kW
    flows: dict[tuple[str, str], tuple[str, float, float]] = field(default_factory=dict)
    # patamar -> (nome do disjuntor, P D/E/F, Q D/E/F)
    head: dict[str, tuple[str, tuple[float, ...], tuple[float, ...]]] = field(default_factory=dict)


def read_interplan(folder: Path) -> InterplanResults:
    voltage_rows = _rows(folder / "TENSAO" / "VF.csv")
    # VF.csv traz coordenadas UTM e geográficas em dois campos cada, contra um
    # rótulo no cabeçalho: os índices são posicionais, não pelo nome.
    periods: list[str] = []
    for row in voltage_rows:
        if row[12] not in periods:
            periods.append(row[12])
    results = InterplanResults(periods)
    for row in voltage_rows:
        results.voltages[(row[1].casefold(), row[12])] = tuple(
            _phasor(row[13 + 2 * phase], row[14 + 2 * phase]) for phase in range(3)
        )
    for row in _rows(folder / "CORRENTE" / "TRECHO_CORR.csv"):
        results.currents[(row[1].casefold(), row[15])] = tuple(
            float(value) for value in row[16:19]
        )
    for row in _rows(folder / "POTENCIA" / "TRECHO_P.csv"):
        results.flows[(row[1].casefold(), row[15])] = (
            row[12].casefold(),
            float(row[19]),
            float(row[20]),
        )
    active = _rows(folder / "POTENCIA" / "CHAVES_P.csv")
    reactive = {(row[1], row[11]): row for row in _rows(folder / "POTENCIA" / "CHAVES_Q.csv")}
    for row in active:
        # O disjuntor de saída é o DJ do relatório; o primeiro de cada patamar.
        if row[4].strip().upper() != "DJ" or row[11] in results.head:
            continue
        q_row = reactive[(row[1], row[11])]
        results.head[row[11]] = (
            row[1].casefold(),
            tuple(float(value) for value in row[12:15]),
            tuple(float(value) for value in q_row[12:15]),
        )
    return results


def _phasor(magnitude: str, angle: str) -> complex:
    try:
        return complex(
            float(magnitude) * math.cos(math.radians(float(angle))),
            float(magnitude) * math.sin(math.radians(float(angle))),
        )
    except ValueError:
        return 0j


@dataclass
class PeriodReport:
    period: str
    converged: bool
    head_dp: tuple[float, ...] = ()
    head_dq: tuple[float, ...] = ()
    voltage_errors: list[tuple[float, str]] = field(default_factory=list)
    angle_errors: list[float] = field(default_factory=list)
    current_errors: list[tuple[float, str]] = field(default_factory=list)
    flow_errors: list[float] = field(default_factory=list)
    losses_dss: float = 0.0
    losses_interplan: float = 0.0


def compare_period(engine, results: InterplanResults, period: str) -> PeriodReport:  # noqa: ANN001
    report = PeriodReport(period, bool(engine.solution.converged))

    head = results.head.get(period)
    if head is not None:
        name, active, reactive = head
        engine.circuit.set_active_element(f"Line.{name}")
        powers = engine.cktelement.powers
        report.head_dp = tuple(powers[2 * k] - active[k] for k in range(3))
        report.head_dq = tuple(powers[2 * k + 1] - reactive[k] for k in range(3))

    names = engine.circuit.nodes_names
    magnitudes = engine.circuit.buses_vmag_pu
    volts = engine.circuit.buses_volts
    for index, node_name in enumerate(names):
        bus, _, node = node_name.partition(".")
        reference = results.voltages.get((bus.casefold(), period))
        if reference is None or not node.isdigit() or not 1 <= int(node) <= 3:
            continue
        expected = reference[int(node) - 1]
        if abs(expected) < MIN_VOLTAGE_PU:
            continue
        report.voltage_errors.append(
            (magnitudes[index] - abs(expected), node_name)
        )
        angle = math.degrees(math.atan2(volts[2 * index + 1], volts[2 * index]))
        delta = (angle - math.degrees(math.atan2(expected.imag, expected.real)) + 180.0) % 360.0 - 180.0
        report.angle_errors.append(delta)

    engine.lines.first()
    while True:
        name = engine.lines.name.casefold()
        currents = results.currents.get((name, period))
        flow = results.flows.get((name, period))
        if currents is not None or flow is not None:
            engine.circuit.set_active_element(f"Line.{name}")
            element = engine.cktelement
            terminal = element.bus_names[0]
            bus, *nodes = terminal.split(".")
            if currents is not None:
                measured = element.currents_mag_ang
                for position, node in enumerate(nodes[: element.num_phases]):
                    if node.isdigit() and 1 <= int(node) <= 3:
                        expected = currents[int(node) - 1]
                        if expected > MIN_CURRENT_A:
                            report.current_errors.append(
                                ((measured[2 * position] - expected) / expected, f"{name}.{node}")
                            )
            if flow is not None:
                powers = element.powers
                count = element.num_conductors
                sending = sum(powers[0 : 2 * count : 2])
                receiving = sum(powers[2 * count : 4 * count : 2])
                first_bus, p12, p21 = flow
                if first_bus != bus.casefold():
                    p12, p21 = p21, p12
                if abs(p12) > MIN_FLOW_KW:
                    report.flow_errors.append(sending - p12)
                report.losses_dss += sending + receiving
                report.losses_interplan += p12 + p21
        if not engine.lines.next():
            break
    return report


def _print_report(reports: list[PeriodReport], top: int) -> None:
    print(
        f"{'patamar':10s} {'conv':>4s} | {'dP saída kW':>11s} {'dQ saída kvar':>13s} | "
        f"{'|V| méd':>8s} {'|V| máx':>8s} {'ângulo máx':>10s} | "
        f"{'I méd':>6s} {'I máx':>6s} | {'fluxo P méd/máx kW':>18s} | perdas OpenDSS/Interplan kW"
    )
    for item in reports:
        voltage = [abs(value) for value, _ in item.voltage_errors] or [0.0]
        current = [abs(value) for value, _ in item.current_errors] or [0.0]
        flow = [abs(value) for value in item.flow_errors] or [0.0]
        angle = max((abs(value) for value in item.angle_errors), default=0.0)
        print(
            f"{item.period:10s} {('sim' if item.converged else 'NÃO'):>4s} | "
            f"{sum(item.head_dp):11.2f} {sum(item.head_dq):13.2f} | "
            f"{statistics.mean(voltage) * 100:7.4f}% {max(voltage) * 100:7.4f}% {angle:9.3f}° | "
            f"{statistics.mean(current) * 100:5.2f}% {max(current) * 100:5.2f}% | "
            f"{statistics.mean(flow):8.3f} / {max(flow):7.3f} | "
            f"{item.losses_dss:.2f} / {item.losses_interplan:.2f}"
        )
    for item in reports:
        if top <= 0:
            break
        worst_voltages = sorted(item.voltage_errors, key=lambda pair: -abs(pair[0]))[:top]
        worst_currents = sorted(item.current_errors, key=lambda pair: -abs(pair[0]))[:top]
        print(f"\n{item.period}: saída dP por fase {['%.2f' % v for v in item.head_dp]}, "
              f"dQ por fase {['%.2f' % v for v in item.head_dq]}")
        print("  maiores erros de |V| (OpenDSS - Interplan, pu): "
              + ", ".join(f"{name} {value:+.4f}" for value, name in worst_voltages))
        print("  maiores erros relativos de corrente: "
              + ", ".join(f"{name} {value * 100:+.2f}%" for value, name in worst_currents))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=Path, help="pasta do export OpenDSS")
    parser.add_argument("interplan", type=Path, help="pasta com TENSAO/CORRENTE/POTENCIA")
    parser.add_argument("--top", type=int, default=5, help="piores casos por patamar")
    args = parser.parse_args()

    masters = sorted(args.export.glob("*_Master.dss"))
    if len(masters) != 1:
        parser.error(f"esperado um *_Master.dss em {args.export}, achados {len(masters)}")
    results = read_interplan(args.interplan)

    reports: list[PeriodReport] = []
    with ascii_workspace() as workspace:
        copy = workspace / "export"
        shutil.copytree(args.export, copy)
        with acquire_engine() as engine:
            engine.text(f"Compile [{copy / masters[0].name}]")
            for command in ("Set mode=daily", "Set stepsize=1h", "Set number=1"):
                engine.text(command)
            for step, period in enumerate(results.periods):
                engine.text(f"Set time=({step}, 0)")
                engine.solution.solve()
                reports.append(compare_period(engine, results, period))
    _print_report(reports, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
