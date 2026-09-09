"""Validação explícita e somente leitura de um Access representativo.

Uso: python benchmarks/validate_project_access.py CAMINHO --output relatorio.json
A senha vem do cofre Windows já configurado, nunca dos argumentos ou do log.
"""
import argparse
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circuit_viewer.mdb_credentials import load_default_password
from circuit_viewer.mdb_engine import open_database
from circuit_viewer.mdb_import import load_database, dataset_from_result
from circuit_viewer.model import UtmCrs
from circuit_viewer.network_registry import FileIdentity
from circuit_viewer.phase_config import load_phase_configuration
from circuit_viewer.switch_types import load_switch_types
from circuit_viewer.project_state import ProjectState, ProjectChangeSet, propose_import, resolve_import
from circuit_viewer.block_analysis import analyze_blocks
from circuit_viewer.block_graph import build_block_graph
from circuit_viewer.block_graph import resolve_block_circuit_indices
from circuit_viewer.opendss_export import build_switch_export


def validate(path):
    start = time.monotonic()
    identity = FileIdentity.read(path)
    with open_database(path, load_default_password()) as database:
        result = load_database(database, UtmCrs(21, northern=False), scale=10, source_path=path,
                               phase_configuration=load_phase_configuration(), switch_types=load_switch_types())
    source = dataset_from_result(result, tag="F1")
    ids = tuple(item.circuit_id for item in source.catalog.definitions)
    def apply(project, selected):
        proposal = propose_import(project, source, identity, circuit_ids=selected)
        change = resolve_import(project, proposal, {item.circuit_id: "update" for item in proposal.feeders})
        assert isinstance(change, ProjectChangeSet)
        change.validate(project)
        return change
    def normalized(state):
        return {(k.entity, k.native_id): r.values for k, r in state.records.items()}
    all_at_once = apply(ProjectState(), ids)
    print("Importação conjunta validada", flush=True)
    scenarios = {}
    for name, groups in (("13+13", (ids[:13], ids[13:])),
                         ("9+9+8 inverso", (ids[18:], ids[9:18], ids[:9]))):
        state = ProjectState()
        for group in groups:
            change = apply(state, group)
            state = change.state
        actual, expected = normalized(state), normalized(all_at_once.state)
        if actual != expected:
            missing, extra = expected.keys() - actual.keys(), actual.keys() - expected.keys()
            changed = [key for key in actual.keys() & expected.keys() if actual[key] != expected[key]]
            print({"scenario": name, "missing": sorted(missing)[:20], "missing_count": len(missing),
                   "extra": sorted(extra)[:20], "extra_count": len(extra), "changed_count": len(changed),
                   "changed": [(key, actual[key], expected[key]) for key in sorted(changed)[:2]]}, flush=True)
            raise AssertionError(name)
        repeated = apply(state, ids)
        assert {k: r.equipment_id for k, r in repeated.state.records.items()} == {k: r.equipment_id for k, r in state.records.items()}
        scenarios[name] = {"registros_iguais": True, "identidades_preservadas_na_reimportacao": True,
                           "pendencias": len(state.pending)}
        print(name + " validado", flush=True)
    composed = all_at_once.composed
    blocks = analyze_blocks(composed.catalog, composed.switches, composed.loads)
    graph = build_block_graph(blocks)
    owners = resolve_block_circuit_indices(blocks, composed.catalog)
    ties = [edge for edge in graph.edges if owners[edge.start_block_id] is not None
            and owners[edge.end_block_id] is not None and owners[edge.start_block_id] != owners[edge.end_block_id]]
    exported = build_switch_export(composed.catalog, load_phase_configuration(), tuple(range(len(ids))))
    assert len(ties) == 90, len(ties)
    assert exported.exported_count + exported.discarded_count == len(composed.switches)
    assert FileIdentity.read(path) == identity, "O banco foi alterado externamente durante a verificação"
    return {"alimentadores": len(ids), "barras": len(composed.bars), "trechos": len(composed.segments),
            "chaves": len(composed.switches), "interligacoes_magenta": len(ties),
            "chaves_exportadas": exported.exported_count, "registros": len(all_at_once.state.records),
            "chaves_recusadas_com_diagnostico": exported.discarded_count,
            "diagnosticos_exportacao": [str(issue) for issue in exported.issues],
            "cenarios": scenarios, "conexao_fechada_antes_das_operacoes": True,
            "assinatura_do_banco_preservada": True, "segundos": round(time.monotonic() - start, 2)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bank")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = validate(args.bank)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
