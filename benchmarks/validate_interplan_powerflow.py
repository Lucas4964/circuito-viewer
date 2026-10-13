"""Compara o fluxo de potência de um export OpenDSS com os relatórios do Interplan.

Uso:
    python benchmarks/validate_interplan_powerflow.py PASTA_EXPORT PASTA_INTERPLAN [--top N]

``PASTA_EXPORT`` é a pasta gravada pela exportação (a do ``<CODIGO>_Master.dss``).
``PASTA_INTERPLAN`` é uma de duas formas de resultado do Interplan:

- **organizado** (exportador de resultados 1.4 em diante): subpastas
  ``BARRAS_MT``, ``TRECHOS_MT``, ``ALIMENTADORES_MT`` e ``REGULADORES_MT``, com
  ``Vfase (pu).csv``, ``I (A).csv``, ``P (kW).csv``, ``Q (kVAr).csv``,
  ``Perdas de demanda.csv``, ``Tap ajustado.csv`` e ``V2-fase(pu).csv``;
- **relatórios** (formato antigo): subpastas ``TENSAO``, ``CORRENTE`` e
  ``POTENCIA`` (``VF.csv``, ``TRECHO_CORR.csv``, ``TRECHO_P.csv``,
  ``CHAVES_P.csv``, ``CHAVES_Q.csv``).

A forma é reconhecida pela presença de ``BARRAS_MT``.

Somente leitura: o export é copiado para uma pasta temporária de caminho ASCII
antes do ``Compile``, porque o OpenDSS só aceita caminhos ASCII e muda o
diretório de trabalho para a pasta do master. Cada patamar é resolvido à parte
(``number=1``, ``time=(h,0)``), na mesma convenção do fluxo interno: a hora
``h+1`` do ``LoadShape`` diário é o NPAT ``h``.

**Diferença conhecida e esperada.** O Interplan deixa o tap de um regulador no
neutro quando a corrente da primeira iteração do backward-forward já passa do
``INOM``, um efeito do algoritmo e não do equipamento. O export não reproduz
isso: o regulador continua regulando e o fluxo interno relata a sobrecarga. A
tabela de reguladores marca esses casos como "neutro no Interplan com tensão
baixa", para a diferença não ser perseguida como erro de modelagem.
"""

from __future__ import annotations

import argparse
import csv
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
# Tap neutro no Interplan com a saída do regulador abaixo disto é a trava por
# INOM, não regulação: um regulador livre já estaria subindo o tap.
LOCKED_NEUTRAL_V2_PU = 0.99
PHASES = "DEF"


def _rows(path: Path) -> list[list[str]]:
    """Linhas de dados de um relatório ``;``, sem o ``;`` final."""

    lines = path.read_text(encoding=CSV_ENCODING).splitlines()
    return [line.rstrip(";").split(";") for line in lines[1:] if line.strip()]


def _organized_rows(path: Path) -> list[dict[str, str]]:
    """Linhas do formato organizado: UTF-8 com BOM, aspas e cabeçalho nomeado."""

    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source, delimiter=";", quotechar='"'))


def _number(text: str) -> float:
    text = (text or "").strip()
    if not text or text in {"-", "..."}:
        return 0.0
    return float(text.replace(",", "."))


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
    # patamar -> (elemento OpenDSS da saída, sinal, P D/E/F, Q D/E/F). O sinal
    # converte a potência que o OpenDSS mede no elemento em potência entregue.
    head: dict[str, tuple[str, float, tuple[float, ...], tuple[float, ...]]] = field(
        default_factory=dict
    )
    # patamar -> perdas totais do alimentador em kW (só no formato organizado)
    losses: dict[str, float] = field(default_factory=dict)
    # (regulador, patamar) -> taps D/E/F em % e tensões de saída D/E/F em pu
    regulator_taps: dict[tuple[str, str], tuple[float, float, float]] = field(default_factory=dict)
    regulator_v2: dict[tuple[str, str], tuple[float, float, float]] = field(default_factory=dict)


def read_interplan(folder: Path) -> InterplanResults:
    if (folder / "BARRAS_MT").is_dir():
        return read_interplan_organized(folder)
    return read_interplan_reports(folder)


def read_interplan_organized(folder: Path) -> InterplanResults:
    voltage_rows = _organized_rows(folder / "BARRAS_MT" / "Vfase (pu).csv")
    periods: list[str] = []
    for row in voltage_rows:
        if row["Período"] not in periods:
            periods.append(row["Período"])
    results = InterplanResults(periods)
    for row in voltage_rows:
        results.voltages[(row["Cód.Barra"].casefold(), row["Período"])] = tuple(
            _phasor(row[f"V{phase}(pu)"], row[f"V{phase}(graus)"]) for phase in PHASES
        )
    for row in _organized_rows(folder / "TRECHOS_MT" / "I (A).csv"):
        results.currents[(row["Cód.Trecho"].casefold(), row["Período"])] = tuple(
            _number(row[f"Fase {phase}(A)"]) for phase in PHASES
        )
    for row in _organized_rows(folder / "TRECHOS_MT" / "P (kW).csv"):
        results.flows[(row["Cód.Trecho"].casefold(), row["Período"])] = (
            row["Barra 1"].casefold(),
            _number(row["P12-Tri.(kW)"]),
            _number(row["P21-Tri.(kW)"]),
        )
    active = {
        row["Período"]: tuple(_number(row[f"P12-Fase {phase}(kW)"]) for phase in PHASES)
        for row in _organized_rows(folder / "ALIMENTADORES_MT" / "P (kW).csv")
    }
    for row in _organized_rows(folder / "ALIMENTADORES_MT" / "Q (kVAr).csv"):
        period = row["Período"]
        reactive = tuple(_number(row[f"Q12-Fase {phase}(kVAr)"]) for phase in PHASES)
        # O Vsource mede a potência que entra nele: a entregue é o oposto.
        results.head[period] = ("vsource.source", -1.0, active[period], reactive)
    losses_path = folder / "ALIMENTADORES_MT" / "Perdas de demanda.csv"
    if losses_path.is_file():
        for row in _organized_rows(losses_path):
            results.losses[row["Período"]] = _number(row["Perda (kW)"])
    taps_path = folder / "REGULADORES_MT" / "Tap ajustado.csv"
    if taps_path.is_file():
        for row in _organized_rows(taps_path):
            results.regulator_taps[(row["Cód.Regulador"], row["Período"])] = tuple(
                _number(row[f"Fase {phase}(%)"]) for phase in PHASES
            )
        for row in _organized_rows(folder / "REGULADORES_MT" / "V2-fase(pu).csv"):
            results.regulator_v2[(row["Cód.Regulador"], row["Período"])] = tuple(
                _number(row[f"V{phase}(pu)"]) for phase in PHASES
            )
    return results


def read_interplan_reports(folder: Path) -> InterplanResults:
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
            f"line.{row[1].casefold()}",
            1.0,
            tuple(float(value) for value in row[12:15]),
            tuple(float(value) for value in q_row[12:15]),
        )
    return results


def _phasor(magnitude: str, angle: str) -> complex:
    try:
        value = _number(magnitude)
        radians = math.radians(_number(angle))
    except ValueError:
        return 0j
    return complex(value * math.cos(radians), value * math.sin(radians))


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
    # (regulador, taps OpenDSS %, taps Interplan %, travado no Interplan?)
    regulators: list[tuple[str, tuple[float, ...], tuple[float, ...], bool]] = field(
        default_factory=list
    )


def compare_period(engine, results: InterplanResults, period: str) -> PeriodReport:  # noqa: ANN001
    report = PeriodReport(period, bool(engine.solution.converged))

    head = results.head.get(period)
    if head is not None:
        element, sign, active, reactive = head
        engine.circuit.set_active_element(element)
        powers = engine.cktelement.powers
        report.head_dp = tuple(sign * powers[2 * k] - active[k] for k in range(3))
        report.head_dq = tuple(sign * powers[2 * k + 1] - reactive[k] for k in range(3))

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

    if results.losses:
        # O formato organizado traz a perda do alimentador inteiro, que inclui
        # chaves e reguladores; a soma dos trechos ficaria abaixo dela.
        report.losses_dss = engine.circuit.losses[0] / 1000.0
        report.losses_interplan = results.losses.get(period, 0.0)

    taps: dict[str, dict[str, float]] = {}
    if engine.transformers.first():
        while True:
            unit = engine.transformers.name
            code, _, phase = unit.upper().removeprefix("REG-").rpartition("-")
            if code and phase in PHASES:
                engine.transformers.wdg = 2
                taps.setdefault(code, {})[phase] = (engine.transformers.tap - 1.0) * 100.0
            if not engine.transformers.next():
                break
    for (code, item_period), expected in sorted(results.regulator_taps.items()):
        if item_period != period:
            continue
        measured = taps.get(code.upper())
        if measured is None:
            continue
        phases = [phase for phase in PHASES if phase in measured]
        v2 = results.regulator_v2.get((code, period), (0.0, 0.0, 0.0))
        live_v2 = [value for value in v2 if value > MIN_VOLTAGE_PU]
        locked = (
            all(abs(value) < 1e-9 for value in expected)
            and bool(live_v2)
            and max(live_v2) < LOCKED_NEUTRAL_V2_PU
        )
        report.regulators.append(
            (
                code,
                tuple(round(measured[phase], 3) for phase in phases),
                tuple(expected[PHASES.index(phase)] for phase in phases),
                locked,
            )
        )
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
    if any(item.regulators for item in reports):
        print("\nReguladores — tap OpenDSS x Interplan (%):")
        for item in reports:
            for code, measured, expected, locked in item.regulators:
                differs = any(
                    abs(a - b) > 1e-6 for a, b in zip(measured, expected, strict=True)
                )
                note = (
                    "neutro no Interplan com tensão baixa: trava por INOM, "
                    "diferença esperada"
                    if locked and differs
                    else ("DIFERENTE" if differs else "igual")
                )
                print(
                    f"  {item.period:10s} {code:12s} OpenDSS {measured} "
                    f"Interplan {expected}  {note}"
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
    parser.add_argument(
        "interplan",
        type=Path,
        help="pasta dos resultados do Interplan (organizado ou TENSAO/CORRENTE/POTENCIA)",
    )
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
