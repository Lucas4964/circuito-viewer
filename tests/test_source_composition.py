"""Restrição por circuito e composição de várias fontes numa cadeia só."""

from __future__ import annotations

import unittest

import numpy as np

from circuit_viewer.mdb_import import dataset_from_result, load_database
from circuit_viewer.model import (
    PARENT_PARAMETER,
    PROVENANCE_PARAMETERS,
    CableModel,
    CapacitorModel,
    CircuitCatalogModel,
    CircuitModel,
    GeneratorModel,
    LineNetworkModel,
    LoadModel,
    LoadPatternModel,
    RegulatorModel,
    SwitchModel,
    UtmCrs,
    constructor_columns,
)
from circuit_viewer.model import _column_parameters
from circuit_viewer.switch_types import load_switch_types
from circuit_viewer.block_analysis import analyze_blocks, boundary_segment_mask
from circuit_viewer.source_composition import (
    ENTITIES,
    ID_SEPARATOR,
    CompositionError,
    SourceDataset,
    SourceWorkspace,
    compose,
    restrict_to_circuits,
)

from tests.test_mdb_import import network_database


CRS = UtmCrs(zone=21, northern=False)

#: A chave de TIPOCHV_ID 2 é um Disjuntor, e ``tipos_chave.json`` declara ``DJ``
#: como manobrável — é o que dá uma fronteira de bloco à rede mínima.
SWITCH_TYPE_TABLES = {
    "TIPOCHAVE": (
        ["ID", "TIPO", "CODIGO", "ELO", "OPERACAO"],
        [
            (2, "Disjuntor", "DJ", 0, 0),
            (4, "Chave Faca", "CF", 0, 0),
            (5, "Chave Fusível", "CHFUSIVEL", 1, 0),
        ],
    ),
}


def dataset(tag: str = "F1", *, offset: int = 0, crs: UtmCrs = CRS, **overrides):
    """Uma fonte completa, opcionalmente com os ids deslocados por ``offset``.

    ``offset`` zero repete os ids da fonte original — é assim que os testes de
    colisão produzem duas fontes que se atropelam.
    """

    tables = dict(SWITCH_TYPE_TABLES)
    tables.update(_shifted_tables(offset))
    tables.update(overrides)
    result = load_database(
        network_database(**tables),
        crs,
        source_path=f"C:/dados/rede-{tag}.mdb",
        scale=10.0,
        # Sem isto as chaves nascem sem TIPO e sem MANOBRAVEL, e a suíte fica
        # cega justamente para as duas colunas que a composição já perdeu uma
        # vez — o que deixou a análise de blocos com um bloco só.
        switch_types=load_switch_types(),
    )
    return dataset_from_result(result, tag=tag, name=f"rede-{tag}.mdb")


def _shifted_tables(offset: int) -> dict:
    """As tabelas da rede mínima com todo identificador somado de ``offset``.

    Deslocar os ids é o que torna duas fontes disjuntas no espaço de nomes sem
    mudar a topologia — assim os testes comparam maçã com maçã.
    """

    if offset == 0:
        return {}
    o = offset
    return {
        "BARRA": (
            ["BARRA_ID", "BLOCO_ID", "CODIGO", "X", "Y", "PL_ANO"],
            [
                (7 + o, 2, f"COD-A{o}", 5989944, 82487703, 0),
                (8 + o, 2, f"COD-B{o}", 5990044, 82487803, 0),
                (9 + o, 2, f"COD-C{o}", 5990144, 82487903, 0),
            ],
        ),
        "TRECHO": (
            [
                "TRECHO_ID", "CODIGO", "FASES2", "BLOCO_ID", "BARRA1_ID",
                "BARRA2_ID", "ARRANJO_ID", "CABOF_ID", "CABON_ID", "COMPR",
            ],
            [
                (2 + o, f"TR-1{o}", 13, -1, 7 + o, 8 + o, 1, 115, -1, 41.297),
                (3 + o, f"TR-2{o}", 13, -1, 8 + o, 9 + o, 1, 115, -1, 50.0),
            ],
        ),
        "CARGA": (
            [
                "CARGA_ID", "BARRA_ID", "EXTERN_ID", "CODIGO", "SNOM", "SADM",
                "VLINHASEC", "FASES2", "TIPO_LIG", "FATDEM",
            ],
            [(2 + o, 9 + o, 34722450, f"CARGA-1{o}", 30.0, 30.0, 220.0, 13, 2, None)],
        ),
        "CAPACITOR": (
            [
                "CAPAC_ID", "BARRA_ID", "EXTERN_ID", "CODIGO", "VNOM",
                "Q1", "Q2", "Q3", "Q4", "FASES", "LIGACAO",
            ],
            [(239 + o, 9 + o, 34559653, f"CAP-1{o}", 13.8, 600.0, 600.0, 600.0,
              600.0, "DEFN", 0)],
        ),
        "MT_CONS": (
            ["ID", "CARGA_ID", "CODIGO", "EXTERN_ID", "NOME", "FASES2"],
            [(101 + o, 2 + o, f"GEN-COD{o}", "EXT-GEN", "Usina", 13)],
        ),
        "MT_GERADOR_CONS": (
            [
                "GERADOR_ID", "MT_CONS_ID", "CODIGO", "VNOM", "SNOM",
                "LIGACAO", "CURVA_ID", "GERACAO_KWH",
            ],
            [(201 + o, 101 + o, f"GEN-COD{o}", 13.8, 75.0, "Y", "CUR-1", 1000.5)],
        ),
        "MODELO_CARGA": (
            ["CENARIO_ID", "CARGA_ID", "NPAT", "PD", "PE", "PF", "QD", "QE", "QF"],
            [
                (1, 2 + o, 0, 0.9876, 0.0, 0.0, 0.1015, 0.0, 0.0),
                (1, 2 + o, 1, 1.0, 0.0, 0.0, 0.2, 0.0, 0.0),
                (1, 2 + o, 2, 1.5, 0.0, 0.0, 0.3, 0.0, 0.0),
                (1, 2 + o, 3, 2.0, 0.0, 0.0, 0.4, 0.0, 0.0),
            ],
        ),
        "CHAVE": (
            [
                "CHAVE_ID", "TIPOCHV_ID", "CIRC_ID", "TRECHO_ID", "CODIGO",
                "ESTADO", "ESTADO_NORMAL", "CORN", "ELO", "ELO_TIPO",
            ],
            [(4 + o, 2, 2 + o, 3 + o, f"CHV-1{o}", 1, 1, 400.0, 0.0, None)],
        ),
        "REGULADOR": (
            [
                "REGU_ID", "TRECHO_ID", "EXTERN_ID", "CODIGO", "LIGACAO",
                "SNOM", "FAIXA", "NPASSOS", "TAP", "INOM", "VNOM",
            ],
            [(2 + o, 2 + o, 34691885, f"REG-1{o}", 0, 333.0, 0.1, 32, 0.025,
              200.0, 13.8)],
        ),
        "CIRCUITO": (
            ["CIRC_ID", "SE_ID", "BARRA_ID", "CODIGO", "VNOM", "NOME"],
            [(2 + o, 2, 7 + o, f"00400{o}", 13.8, "")],
        ),
        "CIRCUITO_PATAMARES": (
            [
                "CIRC_ID", "NPAT", "NOME", "HORARIO_INI", "HORARIO_FIM",
                "HORARIO_REF", "PONTA", "HORARIO_OPC",
            ],
            [
                (2 + o, 0, "Madrugada", 22, 5, 23, 0, 0),
                (2 + o, 1, "Manhã", 5, 11, 11, 0, 0),
                (2 + o, 2, "Tarde", 11, 18, 12, 0, 0),
                (2 + o, 3, "Noite", 18, 22, 22, 1, 0),
            ],
        ),
    }


class ConstructorColumnsTests(unittest.TestCase):
    """O espelho de ``__init__`` é o que impede a composição de divergir."""

    MODELS = (
        CircuitModel,
        CableModel,
        LineNetworkModel,
        LoadModel,
        CapacitorModel,
        GeneratorModel,
        LoadPatternModel,
        SwitchModel,
        RegulatorModel,
    )

    def test_every_chained_model_declares_its_parent(self) -> None:
        import inspect

        for cls in self.MODELS:
            with self.subTest(cls.__name__):
                self.assertIn(cls.__name__, PARENT_PARAMETER)
                parent = PARENT_PARAMETER[cls.__name__]
                first = list(inspect.signature(cls.__init__).parameters)[1]
                if parent is None:
                    self.assertNotIn(first, ("bars", "segments", "loads"))
                else:
                    self.assertEqual(first, parent)

    def test_columns_cover_every_constructor_parameter(self) -> None:
        """Nenhuma coluna pode ficar de fora do espelho.

        É a guarda que faltava: ``type_names`` e ``switchable_values`` são
        keyword-only em ``SwitchModel`` — por compatibilidade com os vinte e
        cinco pontos que o constroem posicionalmente —, e o espelho descartava
        todo keyword-only. As chaves de uma fonte restrita saíam sem tipo e sem
        MANOBRAVEL, e a análise de blocos, sem nenhuma fronteira, devolvia a
        rede inteira num bloco só.

        O teste de ida e volta não pegava isso: ele compara o espelho com o
        espelho, e o espelho é cego exatamente às colunas que descarta.
        """

        import inspect

        for cls in self.MODELS:
            with self.subTest(cls.__name__):
                parameters = list(inspect.signature(cls.__init__).parameters)[1:]
                expected = {
                    name
                    for name in parameters
                    if name != PARENT_PARAMETER[cls.__name__]
                    and name not in PROVENANCE_PARAMETERS
                }
                self.assertEqual(set(_column_parameters(cls)), expected)

    def test_the_excluded_parameters_are_only_provenance(self) -> None:
        # Excluir por nome, e não por "ser keyword-only", é o que torna o padrão
        # seguro: coluna esquecida some em silêncio, metadado a mais estoura.
        self.assertEqual(
            PROVENANCE_PARAMETERS,
            frozenset({"source_path", "source_paths", "name_suffixes"}),
        )

    def test_columns_rebuild_the_model_unchanged(self) -> None:
        source = dataset()
        pairs = (
            (source.bars, None),
            (source.cables, None),
            (source.segments, source.bars),
            (source.loads, source.bars),
            (source.capacitors, source.bars),
            (source.switches, source.segments),
            (source.regulators, source.segments),
        )
        for model, parent in pairs:
            with self.subTest(type(model).__name__):
                columns = constructor_columns(model)
                arguments = () if parent is None else (parent,)
                rebuilt = type(model)(*arguments, **columns)
                self.assertEqual(len(rebuilt), len(model))
                for name, values in constructor_columns(rebuilt).items():
                    if isinstance(values, np.ndarray):
                        np.testing.assert_array_equal(values, columns[name])
                    else:
                        self.assertEqual(values, columns[name], name)


class RestrictTests(unittest.TestCase):
    def test_an_empty_choice_means_the_whole_database(self) -> None:
        source = dataset()
        self.assertIs(restrict_to_circuits(source, ()), source)

    def test_keeps_only_the_chosen_circuit(self) -> None:
        source = dataset()
        restricted = restrict_to_circuits(source, ("2",))
        self.assertEqual(len(restricted.catalog), 1)
        self.assertEqual(restricted.catalog.definition(0).circuit_id, "2")
        self.assertEqual(restricted.chosen_circuit_ids, ("2",))

    def test_the_restricted_chain_is_internally_consistent(self) -> None:
        restricted = restrict_to_circuits(dataset(), ("2",))
        self.assertIs(restricted.segments.bars, restricted.bars)
        self.assertIs(restricted.loads.bars, restricted.bars)
        self.assertIs(restricted.switches.segments, restricted.segments)
        self.assertIs(restricted.catalog.segments, restricted.segments)
        self.assertIs(restricted.catalog.switches, restricted.switches)
        self.assertIs(restricted.generators.loads, restricted.loads)

    def test_an_unknown_circuit_is_refused(self) -> None:
        with self.assertRaises(CompositionError):
            restrict_to_circuits(dataset(), ("nao-existe",))

    def test_keeps_both_endpoints_of_a_segment_held_by_an_open_switch(self) -> None:
        # A chave ABERTA impede o traçado de alcançar a barra distante, mas o
        # trecho dela entra no circuito por casamento de CIRC_ID. Manter só as
        # barras traçadas deixaria esse trecho apontando para barra removida.
        source = dataset(
            CHAVE=(
                [
                    "CHAVE_ID", "TIPOCHV_ID", "CIRC_ID", "TRECHO_ID", "CODIGO",
                    "ESTADO", "ESTADO_NORMAL", "CORN", "ELO", "ELO_TIPO",
                ],
                [(4, 2, 2, 3, "CHV-1", 0, 1, 400.0, 0.0, None)],
            ),
        )
        membership = source.catalog.membership(0)
        self.assertIn(1, membership.segment_indices.tolist())
        self.assertNotIn(2, membership.bar_indices.tolist())

        restricted = restrict_to_circuits(source, ("2",))

        # Se o extremo distante tivesse sido descartado, o LineNetworkModel
        # nem teria sido construído.
        self.assertEqual(len(restricted.segments), 2)
        self.assertEqual(len(restricted.bars), 3)


def interconnected_dataset(*, tie_circuit_id="-1", state="0"):
    """A--B--C, com duas interligações; importar A/B não deve carregar C."""
    return dataset(
        BARRA=(
            ["BARRA_ID", "CODIGO", "X", "Y"],
            [(7, "A1", 5989944, 82487703), (8, "A2", 5990044, 82487803),
             (9, "B1", 5990144, 82487903), (10, "C1", 5990244, 82488003)],
        ),
        TRECHO=(
            ["TRECHO_ID", "CODIGO", "FASES2", "BLOCO_ID", "BARRA1_ID",
             "BARRA2_ID", "ARRANJO_ID", "CABOF_ID", "CABON_ID", "COMPR"],
            [(2, "TRECHO-A", 13, -1, 7, 8, 1, 115, -1, 40),
             (3, "LIGACAO-AB", 13, -1, 8, 9, 1, 115, -1, 50),
             (4, "LIGACAO-BC", 13, -1, 9, 10, 1, 115, -1, 60)],
        ),
        CHAVE=(
            ["CHAVE_ID", "TIPOCHV_ID", "CIRC_ID", "TRECHO_ID", "CODIGO",
             "ESTADO", "ESTADO_NORMAL", "CORN", "ELO", "ELO_TIPO"],
            [(4, 2, tie_circuit_id, 3, "INTERLIGACAO-AB", state, 0, 400, 0, None),
             (5, 2, -1, 4, "INTERLIGACAO-BC", 0, 0, 400, 0, None)],
        ),
        CIRCUITO=(
            ["CIRC_ID", "BARRA_ID", "CODIGO", "VNOM"],
            [(2, 7, "ALIM-A", 13.8), (3, 9, "ALIM-B", 13.8), (4, 10, "ALIM-C", 13.8)],
        ),
    )


class InterconnectionSurvivalTests(unittest.TestCase):
    @staticmethod
    def interconnections(source):
        from circuit_viewer.block_graph import build_block_graph, resolve_block_circuit_indices
        result = analyze_blocks(source.catalog, source.switches, source.loads)
        graph = build_block_graph(result)
        owners = resolve_block_circuit_indices(result, source.catalog)
        return tuple(edge.switch_code for edge in graph.edges
                     if owners[edge.start_block_id] is not None
                     and owners[edge.end_block_id] is not None
                     and owners[edge.start_block_id] != owners[edge.end_block_id])

    def test_selecting_all_keeps_unassigned_ties_and_the_magenta_classification(self):
        source = interconnected_dataset()
        restricted = restrict_to_circuits(source, ("2", "3", "4"))
        self.assertEqual(self.interconnections(source), ("INTERLIGACAO-AB", "INTERLIGACAO-BC"))
        self.assertEqual(self.interconnections(restricted), self.interconnections(source))
        self.assertEqual(len(restricted.segments), len(source.segments))
        self.assertEqual(len(restricted.switches), len(source.switches))

    def test_partial_selection_keeps_internal_tie_without_expanding_to_third_feeder(self):
        restricted = restrict_to_circuits(interconnected_dataset(), ("2", "3"))
        self.assertEqual(set(restricted.bars.bar_ids), {"7", "8", "9"})
        self.assertEqual(set(restricted.segments.segment_ids), {"2", "3"})
        self.assertEqual(tuple(restricted.switches.switch_ids), ("4",))
        self.assertEqual(tuple(d.circuit_id for d in restricted.catalog.definitions), ("2", "3"))
        self.assertEqual(self.interconnections(restricted), ("INTERLIGACAO-AB",))

    def test_missing_foreign_and_negative_owners_preserve_open_and_closed_ties(self):
        for owner in ("", "-1", "999"):
            for state in ("0", "1"):
                with self.subTest(owner=owner, state=state):
                    source = interconnected_dataset(tie_circuit_id=owner, state=state)
                    restricted = restrict_to_circuits(source, ("2", "3"))
                    self.assertEqual(restricted.switches.record(0), source.switches.record(0))
                    self.assertEqual(self.interconnections(restricted), ("INTERLIGACAO-AB",))
                    self.assertEqual(restricted.catalog.membership(0).bar_indices.tolist(), [0, 1])
                    self.assertEqual(restricted.catalog.membership(1).bar_indices.tolist(), [2])

    def test_one_feeder_does_not_invent_external_nodes_or_circuit_owners(self):
        restricted = restrict_to_circuits(interconnected_dataset(), ("2",))
        self.assertEqual(set(restricted.bars.bar_ids), {"7", "8"})
        self.assertIsNone(restricted.switches)
        self.assertEqual(self.interconnections(restricted), ())

    def test_ties_survive_composition_with_a_second_source(self):
        first = restrict_to_circuits(interconnected_dataset(), ("2", "3", "4"))
        composed = compose([first, dataset("F2", offset=1000)])
        self.assertEqual(self.interconnections(composed), self.interconnections(first))


class SwitchTypeSurvivalTests(unittest.TestCase):
    """O tipo da chave e o MANOBRAVEL precisam sobreviver a reconstruir o modelo.

    Foi o defeito relatado: escolhendo um circuito só, toda chave voltava sem
    TIPO e sem MANOBRAVEL, e a ferramenta de blocos passava a ver a rede inteira
    como um bloco único.
    """

    def setUp(self) -> None:
        self.source = dataset()
        self.all_ids = tuple(
            item.circuit_id for item in self.source.catalog.definitions
        )

    def test_the_fixture_really_has_a_switchable_switch(self) -> None:
        # Sem isto, todo o resto desta classe passaria por vacuidade.
        record = self.source.switches.record(0)
        self.assertEqual(record.type_name, "Disjuntor")
        self.assertEqual(record.switchable, "1")

    def test_restricting_keeps_the_type_and_the_switchable_flag(self) -> None:
        restricted = restrict_to_circuits(self.source, ("2",))
        record = restricted.switches.record(0)
        self.assertEqual(record.type_name, "Disjuntor")
        self.assertEqual(record.switchable, "1")

    def test_restricting_to_every_circuit_preserves_every_record(self) -> None:
        """Ida e volta pelos ``record()``, e não pelas colunas do espelho.

        Restringir a **todos** os circuitos força a reconstrução sem mudar o
        conteúdo, então cada registro tem de sair idêntico. A comparação passa
        pelos ``*Record`` de propósito: eles expõem ``type_name`` e
        ``switchable``, que o espelho do construtor não expunha — comparar
        espelho com espelho é o que deixou o defeito passar.
        """

        restricted = restrict_to_circuits(self.source, self.all_ids)
        for name in (
            "bars",
            "cables",
            "segments",
            "loads",
            "capacitors",
            "generators",
            "switches",
            "regulators",
        ):
            original = getattr(self.source, name)
            rebuilt = getattr(restricted, name)
            with self.subTest(name):
                self.assertIsNotNone(rebuilt)
                self.assertEqual(len(rebuilt), len(original))
                for index in range(len(original)):
                    self.assertEqual(rebuilt.record(index), original.record(index))

    def test_the_block_boundary_survives_a_partial_choice(self) -> None:
        """O sintoma relatado, do jeito que o usuário o vê."""

        restricted = restrict_to_circuits(self.source, ("2",))
        boundary = boundary_segment_mask(restricted.segments, restricted.switches)
        self.assertEqual(int(boundary.sum()), 1)

        whole = analyze_blocks(
            self.source.catalog, self.source.switches, self.source.loads
        )
        partial = analyze_blocks(
            restricted.catalog, restricted.switches, restricted.loads
        )
        self.assertEqual(partial.switchable_switch_count, 1)
        self.assertEqual(len(partial.records), len(whole.records))
        self.assertNotIn("sem-fronteira", [issue.kind for issue in partial.issues])

    def test_composing_two_sources_keeps_both_switch_types(self) -> None:
        composed = compose([dataset("F1"), dataset("F2", offset=1000)])
        for index in range(len(composed.switches)):
            with self.subTest(index=index):
                record = composed.switches.record(index)
                self.assertEqual(record.type_name, "Disjuntor")
                self.assertEqual(record.switchable, "1")
        boundary = boundary_segment_mask(composed.segments, composed.switches)
        self.assertEqual(int(boundary.sum()), 2)


class SingleSourceTests(unittest.TestCase):
    def test_one_source_returns_the_very_same_objects(self) -> None:
        # É esta identidade que garante que abrir um banco só continue sendo,
        # byte a byte, o que sempre foi: os mesmos objetos atravessam os mesmos
        # setters da janela principal.
        source = dataset()
        composed = compose([source])
        for name in ("bars", "cables", "segments", "loads", "capacitors",
                     "generators", "patterns", "switches", "regulators",
                     "catalog", "circuit_levels"):
            with self.subTest(name):
                self.assertIs(getattr(composed, name), getattr(source, name))
        self.assertFalse(composed.report.has_warnings)
        self.assertTrue(composed.provenance.single_source)

    def test_the_provenance_answers_even_with_one_source(self) -> None:
        source = dataset("F7")
        provenance = compose([source]).provenance
        self.assertEqual(provenance.tag_of("bars", 0), "F7")
        self.assertEqual(provenance.key("circuits", 0), ("F7", "2"))

    def test_composing_nothing_is_refused(self) -> None:
        with self.assertRaises(CompositionError):
            compose([])


class ComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = dataset("F1")
        self.second = dataset("F2", offset=1000)
        self.composed = compose([self.first, self.second])

    def test_the_counts_add_up(self) -> None:
        for name in ("bars", "segments", "loads", "capacitors", "generators",
                     "switches", "regulators"):
            with self.subTest(name):
                self.assertEqual(
                    len(getattr(self.composed, name)),
                    self.first.count(name) + self.second.count(name),
                )
        self.assertEqual(len(self.composed.catalog), 2)

    def test_the_chain_is_bound_by_identity_as_always(self) -> None:
        composed = self.composed
        self.assertIs(composed.segments.bars, composed.bars)
        self.assertIs(composed.loads.bars, composed.bars)
        self.assertIs(composed.switches.segments, composed.segments)
        self.assertIs(composed.catalog.segments, composed.segments)
        self.assertIs(composed.catalog.switches, composed.switches)
        self.assertIs(composed.circuit_levels.circuits, composed.catalog)

    def test_indices_of_the_second_source_are_shifted(self) -> None:
        composed = self.composed
        offset = len(self.first.bars)
        second_segment = composed.segments.index_for_id(
            self.second.segments.segment_ids[0]
        )
        self.assertEqual(
            int(composed.segments.start_indices[second_segment]),
            int(self.second.segments.start_indices[0]) + offset,
        )

    def test_composed_memberships_equal_a_full_retrace(self) -> None:
        """Deslocar índices equivale a refazer o traçado — a afirmação central.

        Se esta igualdade cair, a composição deixou de ser um deslocamento e
        passou a ser outra coisa, e o catálogo composto não é mais o mesmo
        catálogo que o traçado produziria.
        """

        composed = self.composed
        rebuilt = CircuitCatalogModel.build(
            composed.segments, composed.switches, composed.catalog.definitions
        )
        for shifted, traced in zip(
            composed.catalog.memberships, rebuilt.memberships, strict=True
        ):
            for name in (
                "bar_indices",
                "common_segment_indices",
                "switch_segment_indices",
                "segment_indices",
            ):
                with self.subTest(name):
                    np.testing.assert_array_equal(
                        np.sort(getattr(shifted, name)),
                        np.sort(getattr(traced, name)),
                    )

    def test_the_provenance_says_where_each_row_came_from(self) -> None:
        composed = self.composed
        provenance = composed.provenance
        self.assertEqual(provenance.tag_of("bars", 0), "F1")
        self.assertEqual(provenance.tag_of("bars", len(self.first.bars)), "F2")
        self.assertEqual(provenance.key("circuits", 0), ("F1", "2"))
        self.assertEqual(provenance.key("circuits", 1), ("F2", "1002"))

    def test_identical_cables_are_merged_instead_of_duplicated(self) -> None:
        # Os dois bancos declaram o mesmo CABO_ID com o mesmo conteúdo:
        # qualificá-lo quebraria mapa_cabos.json para a segunda fonte.
        self.assertEqual(len(self.composed.cables), 1)
        self.assertEqual(self.composed.report.merged_cables, ("115",))
        self.assertEqual(self.composed.report.collisions, ())

    def test_patterns_and_levels_survive_the_composition(self) -> None:
        composed = self.composed
        self.assertEqual(len(composed.patterns), 2)
        self.assertEqual(len(composed.circuit_levels), 2)


class CollisionTests(unittest.TestCase):
    def setUp(self) -> None:
        # offset zero: a segunda fonte repete todos os ids da primeira.
        self.first = dataset("F1")
        self.second = dataset("F2")

    def test_strict_mode_refuses_instead_of_renaming(self) -> None:
        with self.assertRaises(CompositionError) as raised:
            compose([self.first, self.second], strict_ids=True)
        self.assertIn("BARRA_ID", str(raised.exception))

    def test_the_first_source_keeps_the_bare_identifier(self) -> None:
        composed = compose([self.first, self.second])
        self.assertEqual(composed.bars.bar_ids[0], "7")
        self.assertEqual(composed.bars.bar_ids[3], f"7{ID_SEPARATOR}F2")

    def test_every_collision_is_reported(self) -> None:
        report = compose([self.first, self.second]).report
        self.assertTrue(report.has_warnings)
        entities = {item.entity for item in report.collisions}
        self.assertLessEqual(
            {"BARRA_ID", "TRECHO_ID", "CARGA_ID", "CIRC_ID"}, entities
        )
        collision = next(
            item for item in report.collisions if item.entity == "BARRA_ID"
        )
        self.assertEqual(collision.keeper_tag, "F1")
        self.assertEqual(collision.renamed_tag, "F2")

    def test_the_circuit_root_bar_follows_the_renamed_bar(self) -> None:
        # Se a barra inicial não acompanhasse, o catálogo recusaria o circuito.
        composed = compose([self.first, self.second])
        self.assertEqual(
            composed.catalog.definition(1).root_bar_id, f"7{ID_SEPARATOR}F2"
        )
        self.assertEqual(
            composed.catalog.definition(1).circuit_id, f"2{ID_SEPARATOR}F2"
        )

    def test_the_switch_follows_the_renamed_circuit(self) -> None:
        """A pré-condição estrutural: chave e circuito casam por string.

        Sem esta reescrita, a chave da fonte F2 entraria no circuito homônimo
        de F1 e o catálogo composto deixaria de equivaler ao traçado.
        """

        composed = compose([self.first, self.second])
        self.assertEqual(composed.switches.circuit_ids[1], f"2{ID_SEPARATOR}F2")
        rebuilt = CircuitCatalogModel.build(
            composed.segments, composed.switches, composed.catalog.definitions
        )
        for shifted, traced in zip(
            composed.catalog.memberships, rebuilt.memberships, strict=True
        ):
            np.testing.assert_array_equal(
                np.sort(shifted.segment_indices), np.sort(traced.segment_indices)
            )

    def test_the_pattern_load_id_follows_the_renamed_load(self) -> None:
        composed = compose([self.first, self.second])
        records = composed.patterns.records_for_load(1)
        self.assertEqual(records[0].load_id, f"2{ID_SEPARATOR}F2")
        self.assertEqual(composed.loads.load_ids[1], f"2{ID_SEPARATOR}F2")

    def test_a_repeated_codigo_is_reported_but_never_altered(self) -> None:
        composed = compose([self.first, self.second])
        self.assertEqual(composed.bars.codes[0], composed.bars.codes[3])
        codes = {item.code for item in composed.report.code_collisions}
        self.assertIn("COD-A", codes)
        self.assertEqual(composed.bars.codes[0], "COD-A")

    def test_a_qualified_form_already_taken_gets_a_counter(self) -> None:
        taken = dataset(
            "F2",
            BARRA=(
                ["BARRA_ID", "BLOCO_ID", "CODIGO", "X", "Y", "PL_ANO"],
                [
                    (7, 2, "COD-A", 5989944, 82487703, 0),
                    (8, 2, "COD-B", 5990044, 82487803, 0),
                    (f"7{ID_SEPARATOR}F2", 2, "COD-C", 5990144, 82487903, 0),
                ],
            ),
        )
        composed = compose([self.first, taken])
        self.assertIn(f"7{ID_SEPARATOR}F2_2", composed.bars.bar_ids)


class ReprojectionTests(unittest.TestCase):
    def test_a_source_in_another_zone_is_brought_to_the_first_crs(self) -> None:
        first = dataset("F1")
        second = dataset("F2", offset=1000, crs=UtmCrs(zone=22, northern=False))

        composed = compose([first, second])

        self.assertEqual(composed.bars.crs, first.crs)
        self.assertEqual(len(composed.report.reprojections), 1)
        note = composed.report.reprojections[0]
        self.assertEqual(note.tag, "F2")
        self.assertEqual(note.target_crs, first.crs)
        self.assertGreater(note.max_shift_metres, 0.0)
        offset = len(first.bars)
        self.assertNotEqual(
            float(composed.bars.x[offset]), float(second.bars.x[0])
        )

    def test_the_same_zone_is_never_reprojected(self) -> None:
        composed = compose([dataset("F1"), dataset("F2", offset=1000)])
        self.assertEqual(composed.report.reprojections, ())


class WorkspaceTests(unittest.TestCase):
    def test_tags_are_never_reused(self) -> None:
        workspace = SourceWorkspace()
        self.assertEqual(workspace.next_tag(), "F1")
        workspace = workspace.added(dataset("F1"))
        self.assertEqual(workspace.next_tag(), "F2")
        workspace = workspace.added(dataset("F2", offset=1000))
        workspace = workspace.without("F1")
        # Remover F1 não pode renumerar F2 nem devolver F1 à fila: o estado de
        # sessão é chaveado por (etiqueta, id nativo).
        self.assertEqual([item.tag for item in workspace.datasets], ["F2"])
        self.assertEqual(workspace.next_tag(), "F3")

    def test_operations_return_new_instances(self) -> None:
        workspace = SourceWorkspace()
        added = workspace.added(dataset("F1"))
        self.assertEqual(len(workspace), 0)
        self.assertEqual(len(added), 1)
        self.assertIsNot(workspace, added)

    def test_replacing_drops_every_other_source(self) -> None:
        workspace = SourceWorkspace().added(dataset("F1"))
        workspace = workspace.added(dataset("F2", offset=1000))
        replaced = workspace.replaced_by(dataset("F3", offset=2000))
        self.assertEqual([item.tag for item in replaced.datasets], ["F3"])
        self.assertEqual(replaced.next_tag(), "F4")

    def test_removing_an_unknown_source_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            SourceWorkspace().without("F9")

    def test_renaming_keeps_the_order_and_the_tag(self) -> None:
        workspace = SourceWorkspace().added(dataset("F1"))
        renamed = workspace.renamed("F1", "Rede Norte")
        self.assertEqual(renamed.datasets[0].name, "Rede Norte")
        self.assertEqual(renamed.datasets[0].tag, "F1")


class EntityTableTests(unittest.TestCase):
    def test_every_entity_names_a_field_of_the_dataset(self) -> None:
        for entity in ENTITIES:
            with self.subTest(entity.name):
                self.assertTrue(hasattr(SourceDataset, "__dataclass_fields__"))
                self.assertIn(entity.name, SourceDataset.__dataclass_fields__)

    def test_every_index_column_points_at_a_known_entity(self) -> None:
        names = {entity.name for entity in ENTITIES}
        for entity in ENTITIES:
            for column, target in entity.index_columns.items():
                with self.subTest(f"{entity.name}.{column}"):
                    self.assertIn(target, names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
