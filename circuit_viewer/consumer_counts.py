"""Contagem de UC cadastradas, independente da alocação de energia e das fases.

BT_CONS.ID -> ET_ID -> BT_ET.ID -> MT_CAR_ID -> CARGA.CARGA_ID;
MT_CONS.ID -> CARGA_ID. Os IDs de BT e MT pertencem a domínios distintos.
Não usamos consumo nem ATIVO como filtro: esta é uma contagem cadastral.
"""
from collections import Counter
from dataclasses import dataclass

from .csv_import import CsvImportCancelled


CONSUMER_TABLES = (
    ("BT_ET", ("ID", "MT_CAR_ID")),
    ("BT_CONS", ("ID", "ET_ID")),
    ("MT_CONS", ("ID", "CARGA_ID")),
)


@dataclass(frozen=True, slots=True)
class ConsumerCounts:
    counts: tuple[int | None, ...]
    sources: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()


def read_consumer_counts(database, loads, *, cancel_event=None, mt_table=None):
    """Lê uma vez por importação, fecha cursores e nunca consulta o MDB no grafo.

    Cadastro incompleto/ambíguo não vira zero. Sem uma associação confiável,
    conserva-se a rede e informa-se a indisponibilidade da contagem.
    """
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise CsvImportCancelled("Importação cancelada.")

    def unique_links(table, columns):
        links = {}
        rows = database.iter_rows(table, columns)
        try:
            for row in rows:
                check()
                identity, target = (str(value).strip() for value in row)
                if not identity or not target:
                    raise ValueError(f"{table}: consumidor/transformador sem ID ou vínculo.")
                if identity in links and links[identity] != target:
                    raise ValueError(f"{table}: ID duplicado com vínculos diferentes.")
                links[identity] = target
        finally:
            close = getattr(rows, "close", None)
            if close is not None:
                close()
        return links

    try:
        check()
        tables = {name.casefold(): name for name in database.tables()}
        resolved = []
        for name, required in CONSUMER_TABLES:
            check()
            if name == "MT_CONS" and mt_table is not None:
                name = mt_table
            table = tables.get(name.casefold())
            if table is None:
                raise ValueError(f"tabela {name} ausente.")
            columns = {name.casefold(): name for name in database.columns(table)}
            missing = [name for name in required if name.casefold() not in columns]
            if missing:
                raise ValueError(f"{table}: colunas ausentes: {', '.join(missing)}.")
            resolved.append((table, tuple(columns[name.casefold()] for name in required)))
        transformers, bt, mt = (unique_links(*item) for item in resolved)
        if any(transformer not in transformers for transformer in bt.values()):
            raise ValueError("BT_CONS: ET_ID sem correspondência em BT_ET.")
        counts = Counter(transformers[transformer] for transformer in bt.values())
        counts.update(mt.values())
        known = set(loads.load_ids)
        unassigned = sum(count for load_id, count in counts.items() if load_id not in known)
        diagnostics = (() if not unassigned else (
            f"{unassigned} UC sem carga válida importada; não atribuídas a blocos.",))
        source = (f"{resolved[1][0]}.ID via {resolved[0][0]}.MT_CAR_ID + "
                  f"{resolved[2][0]}.ID via CARGA_ID; IDs distintos por tabela, sem filtro de ATIVO")
        check()
        return ConsumerCounts(tuple(counts[load_id] for load_id in loads.load_ids),
                              (source,) * len(loads), diagnostics)
    except (CsvImportCancelled, InterruptedError):
        raise CsvImportCancelled("Importação cancelada.")
    except Exception as exc:
        return ConsumerCounts((None,) * len(loads), ("",) * len(loads),
                              (f"Contagem de UC indisponível: {exc}",))
