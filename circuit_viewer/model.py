"""Modelo lógico e índice espacial, sem qualquer dependência de Qt."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from collections import deque
from collections.abc import Callable, Collection
from functools import lru_cache
from typing import Iterable, Literal, Sequence

import numpy as np
from numpy.typing import NDArray

from .circuit_colors import generate_circuit_palette, normalize_hex_color


FloatArray = NDArray[np.float64]
IndexArray = NDArray[np.intp]
BoolArray = NDArray[np.bool_]


#: O parâmetro de ``__init__`` que carrega o modelo-pai de cada modelo
#: encadeado, ou ``None`` para os modelos-raiz. É a única lista escrita à mão
#: desta mecânica; ``test_model.py`` a confere contra as assinaturas reais.
PARENT_PARAMETER: dict[str, str | None] = {
    "CircuitModel": None,
    "CableModel": None,
    "LineNetworkModel": "bars",
    "LoadModel": "bars",
    "CapacitorModel": "bars",
    "GeneratorModel": "loads",
    "LoadPatternModel": "loads",
    "SwitchModel": "segments",
    "RegulatorModel": "segments",
}


#: Parâmetros que descrevem o modelo **inteiro**, não uma coluna dele. Tudo o
#: mais que o construtor aceita é coluna, e a composição carrega.
#:
#: A exclusão é por nome, e não por "ser keyword-only", porque os dois erros não
#: custam o mesmo. Esquecer uma coluna some em silêncio: foi assim que
#: ``SwitchModel.type_names`` e ``switchable_values`` — keyword-only por causa
#: dos vinte e cinco pontos que constroem o modelo posicionalmente — sumiram ao
#: reconstruir um modelo restrito, deixando toda chave sem tipo e sem
#: ``MANOBRAVEL``, e a análise de blocos sem nenhuma fronteira. Carregar um
#: metadado por engano, ao contrário, estoura na construção.
PROVENANCE_PARAMETERS = frozenset({"source_path", "source_paths", "name_suffixes"})


@lru_cache(maxsize=None)
def _column_parameters(cls: type) -> tuple[str, ...]:
    parent = PARENT_PARAMETER.get(cls.__name__)
    parameters = list(inspect.signature(cls.__init__).parameters.values())[1:]
    return tuple(
        parameter.name
        for parameter in parameters
        if parameter.name != parent and parameter.name not in PROVENANCE_PARAMETERS
    )


def constructor_columns(model: object) -> dict[str, object]:
    """As colunas de um modelo, na forma dos argumentos de ``__init__``.

    Deriva da **própria assinatura** do construtor, não de uma segunda lista:
    é o que impede que acrescentar uma coluna a um modelo quebre em silêncio a
    composição de fontes, que é quem reconstrói os modelos a partir daqui.
    Uma cópia manual divergiria sem que nenhum teste percebesse.

    Entra tudo, menos o modelo-pai (``bars``, ``segments``, ``loads``) e os
    :data:`PROVENANCE_PARAMETERS`, porque quem recompõe é que escolhe os dois — o
    pai é o modelo novo, e a origem passa a ser a composição, não o arquivo. Ser
    keyword-only **não** exclui: uma coluna pode sê-lo por compatibilidade, como
    as duas de tipo de chave.

    Cada coluna é lida do slot ``_<nome>``, com recuo para o atributo público de
    mesmo nome: ``CircuitModel.crs`` é parâmetro do construtor e atributo
    público, não coluna privada.
    """

    values: dict[str, object] = {}
    for name in _column_parameters(type(model)):
        try:
            values[name] = getattr(model, f"_{name}")
        except AttributeError:
            values[name] = getattr(model, name)
    return values


@dataclass(frozen=True, slots=True)
class Bounds:
    """Retângulo no sistema de coordenadas do modelo."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def is_empty(self) -> bool:
        return self.right < self.left or self.bottom < self.top

    def expanded(self, x_margin: float, y_margin: float) -> "Bounds":
        return Bounds(
            self.left - x_margin,
            self.top - y_margin,
            self.right + x_margin,
            self.bottom + y_margin,
        )


@dataclass(frozen=True, slots=True)
class UtmCrs:
    """Identificação mínima de um CRS UTM WGS 84."""

    zone: int
    northern: bool

    def __post_init__(self) -> None:
        if not 1 <= self.zone <= 60:
            raise ValueError("A zona UTM deve estar entre 1 e 60.")

    @property
    def hemisphere(self) -> str:
        return "Norte" if self.northern else "Sul"

    @property
    def epsg(self) -> int:
        return (32600 if self.northern else 32700) + self.zone

    @property
    def label(self) -> str:
        suffix = "N" if self.northern else "S"
        return f"UTM {self.zone}{suffix} — EPSG:{self.epsg}"


def _checked_name_suffixes(
    values: tuple[str, ...] | None,
    size: int,
) -> tuple[str, ...] | None:
    """Valida o vetor de sufixos de rótulo.

    ``name_suffixes`` desambigua o **rótulo**, nunca a chave.

        ``None`` é "fonte única, nada a desambiguar", que é o que todo
        construtor produz hoje. Não-``None`` é um sufixo por linha — vazio para
        quem não colide, a etiqueta da fonte para quem colide com outra. O
        ``CODIGO`` em si nunca é alterado: ele é o que o cadastro afirma, e
        adulterá-lo mentiria para quem lê a tela. Quem usa isto é o espaço de
        nomes do OpenDSS, onde dois nomes iguais fazem o exportador **descartar**
        a segunda linha.

        Fica guardado, e não calculado sob demanda, porque ``bus_namer`` é
        construído dentro de laços sobre todas as barras: qualquer pré-cálculo
        por barra lá dentro viraria custo quadrático.
    """

    if values is None:
        return None
    suffixes = tuple(str(value) for value in values)
    if len(suffixes) != size:
        raise ValueError(
            "Os sufixos de desambiguação devem ter uma posição por linha."
        )
    return suffixes


@dataclass(frozen=True, slots=True)
class BarRecord:
    """Visão imutável de uma barra individual."""

    bar_id: str
    code: str
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class SegmentRecord:
    """Visão imutável de um trecho da rede."""

    segment_id: str
    code: str
    phases: str
    start_bar_id: str
    end_bar_id: str
    arrangement_id: str
    phase_cable_id: str
    neutral_cable_id: str
    length: float | None


# O que o ESTADO de uma chave significa. O literal "1" ja e testado em quatro
# pontos — o exportador, a travessia da topologia, a validacao do catalogo e a
# analise de ramais —, e nomea-lo aqui e o que impede uma quinta copia de
# divergir: um rotulo errado nao quebra teste de comportamento, so mente na tela.
CLOSED_SWITCH_STATE = "1"
OPEN_SWITCH_STATE = "0"

_SWITCH_STATE_LABELS = {
    CLOSED_SWITCH_STATE: "FECHADA",
    OPEN_SWITCH_STATE: "ABERTA",
}


def switch_state_label(value: str) -> str:
    """O ESTADO de uma chave por extenso, no feminino de *chave*.

    Vazio devolve vazio, para a interface mostrar o travessao que ja usa em
    campo sem valor.

    Um valor fora de ``{"0", "1"}`` nao vira travessao nem ``ABERTA`` seca: o
    programa **trata** esse caso como aberta — o exportador registra "a chave foi
    exportada como aberta" e a travessia bloqueia —, mas o cadastro esta errado.
    Dizer so ``ABERTA`` esconderia o defeito; dizer so ``—`` esconderia o
    comportamento. O rotulo diz as duas coisas.
    """

    text = str(value).strip()
    if not text:
        return ""
    label = _SWITCH_STATE_LABELS.get(text)
    if label is not None:
        return label
    return f"{_SWITCH_STATE_LABELS[OPEN_SWITCH_STATE]} (valor não reconhecido)"


@dataclass(frozen=True, slots=True)
class SwitchRecord:
    """Atributos de uma chave associada a um trecho da rede.

    ``type_name`` é o ``TIPOCHAVE.TIPO`` do banco — "Chave Faca", "Disjuntor" —
    e ``switchable`` é o ``"1"``/``"0"`` que ``tipos_chave.json`` declara para
    aquele tipo. Os dois saem vazios quando a fonte não os fornece, e a
    interface troca vazio por traço: um fusível sem relação no arquivo não vira
    manobrável por omissão.
    """

    switch_id: str
    switch_type_id: str
    circuit_id: str
    segment_id: str
    code: str
    state: str
    normal_state: str
    corn: str
    elo: str
    elo_type: str
    type_name: str = ""
    switchable: str = ""


@dataclass(frozen=True, slots=True)
class RegulatorRecord:
    """Atributos de um regulador de tensão associado a um trecho da rede.

    Todos os campos são texto, inclusive os numéricos por natureza (``SNOM``,
    ``NPASSOS``, ``TAP``): eles são apenas exibidos, e a regra do projeto é
    converter só onde há consumidor — como ``LoadRecord`` faz com ``snom``.
    """

    regulator_id: str
    segment_id: str
    external_id: str
    code: str
    connection: str
    snom: str
    regulation_range: str
    step_count: str
    tap: str
    inom: str
    vnom: str


@dataclass(frozen=True, slots=True)
class LoadRecord:
    """Atributos de uma carga associada a uma barra da rede."""

    load_id: str
    bar_id: str
    external_id: str
    code: str
    snom: str
    sadm: str
    secondary_line_voltage: str
    phases: str
    connection_type: str


@dataclass(frozen=True, slots=True)
class CapacitorRecord:
    """Atributos de um banco de capacitores associado a uma barra.

    ``phases`` guarda a coluna ``FASES`` do cadastro, que é a string de letras
    (``DEFN``) e não o código numérico ``FASES2`` das cargas. ``q1``..``q4`` são
    a potência reativa do **banco inteiro** em cada patamar, não por fase.
    """

    capacitor_id: str
    bar_id: str
    external_id: str
    code: str
    nominal_voltage: str
    q1: str
    q2: str
    q3: str
    q4: str
    phases: str
    connection_type: str

    @property
    def reactive_powers(self) -> tuple[str, str, str, str]:
        """Q1..Q4 na ordem dos patamares NPAT 0..3."""

        return (self.q1, self.q2, self.q3, self.q4)


@dataclass(frozen=True, slots=True)
class GeneratorRecord:
    """Gerador de MT, seu consumidor associado e a barra resolvida."""

    generator_id: str
    mt_cons_id: str
    generator_code: str
    nominal_voltage: str
    nominal_power: str
    connection: str
    curve_id: str
    generation_kwh: str
    consumer_id: str
    load_id: str
    consumer_code: str
    external_id: str
    name: str
    phases: str
    bar_id: str


@dataclass(frozen=True, slots=True)
class CableRecord:
    """Atributos de um cabo do catálogo referenciado pelos trechos."""

    cable_id: str
    cable_type: str
    code: str
    iadm: str
    gmr: str
    r: str
    x: str
    qcap: str
    r0: str
    x0: str
    r1: str
    x1: str
    name: str
    external_id: str


@dataclass(frozen=True, slots=True)
class LoadPatternRecord:
    """Valores complementares de uma carga em um patamar NPAT."""

    load_id: str
    npat: int
    pd: str
    pe: str
    pf: str
    qd: str
    qe: str
    qf: str

    def __post_init__(self) -> None:
        if type(self.npat) is not int or self.npat not in {0, 1, 2, 3}:
            raise ValueError("NPAT deve ser um inteiro entre 0 e 3.")


@dataclass(frozen=True, slots=True)
class CircuitDefinition:
    """Metadados de um circuito, sua barra de partida e sua origem.

    Os cinco campos de origem descrevem **de onde** o alimentador sai: a
    subestação e o transformador que o alimentam. Eles nascem do
    ``SE_ID``/``TRAFO_ID`` da tabela de circuitos, resolvidos contra ``SE`` e
    ``SE_TRAFO`` por quem conhece o banco — o parser de linhas os recebe
    prontos.

    Todos têm padrão vazio de propósito. São informativos: um circuito cuja
    chave não resolva, ou que venha de uma fonte que não as tenha, continua
    sendo um circuito, e o catálogo continua desenhável. Perder o alimentador
    por causa de uma coluna de referência seria trocar o essencial pelo
    acessório.
    """

    circuit_id: str
    root_bar_id: str
    code: str
    nominal_voltage: str
    substation_code: str = ""
    substation_name: str = ""
    transformer_id: str = ""
    transformer_code: str = ""
    transformer_power: str = ""


@dataclass(frozen=True, slots=True)
class CircuitMembership:
    """Índices associados a um circuito, separados pela origem da associação."""

    bar_indices: IndexArray
    common_segment_indices: IndexArray
    switch_segment_indices: IndexArray
    segment_indices: IndexArray

    def __post_init__(self) -> None:
        arrays = (
            self.bar_indices,
            self.common_segment_indices,
            self.switch_segment_indices,
            self.segment_indices,
        )
        for values in arrays:
            if values.dtype != np.dtype(np.intp) or values.ndim != 1:
                raise ValueError("As associações devem ser vetores de índices.")


@dataclass(frozen=True, slots=True)
class FeatureSelection:
    """Referência leve para um elemento selecionado em um dos modelos."""

    kind: Literal[
        "bar", "segment", "load", "equivalent_load", "generator", "capacitor"
    ]
    index: int

    def __post_init__(self) -> None:
        if self.kind not in {
            "bar",
            "segment",
            "load",
            "equivalent_load",
            "generator",
            "capacitor",
        }:
            raise ValueError(f"Tipo de elemento desconhecido: {self.kind}")
        if self.index < 0:
            raise ValueError("O índice selecionado não pode ser negativo.")


class StaticPointIndex:
    """Índice estático para pontos, otimizado para retângulos e proximidade.

    Os pontos são ordenados uma única vez por X. As consultas delimitam os
    candidatos com ``searchsorted`` e filtram Y de forma vetorizada. A classe
    mantém apenas índices inteiros e não duplica os vetores de coordenadas.
    """

    __slots__ = ("_x", "_y", "_order", "_sorted_x")

    def __init__(self, x: FloatArray, y: FloatArray) -> None:
        x_array = np.ascontiguousarray(x, dtype=np.float64)
        y_array = np.ascontiguousarray(y, dtype=np.float64)
        if x_array.ndim != 1 or y_array.ndim != 1:
            raise ValueError("As coordenadas devem ser vetores unidimensionais.")
        if x_array.shape != y_array.shape:
            raise ValueError("X e Y devem possuir o mesmo tamanho.")
        if not np.isfinite(x_array).all() or not np.isfinite(y_array).all():
            raise ValueError("O índice não aceita coordenadas não finitas.")

        self._x = x_array
        self._y = y_array
        self._order = np.argsort(x_array, kind="stable").astype(np.intp, copy=False)
        self._sorted_x = x_array[self._order]
        self._order.setflags(write=False)
        self._sorted_x.setflags(write=False)

    def __len__(self) -> int:
        return int(self._x.size)

    def query_rect(self, bounds: Bounds | Sequence[float]) -> IndexArray:
        """Retorna índices dos pontos dentro de um retângulo inclusivo."""

        if isinstance(bounds, Bounds):
            left, top, right, bottom = (
                bounds.left,
                bounds.top,
                bounds.right,
                bounds.bottom,
            )
        else:
            if len(bounds) != 4:
                raise ValueError("O retângulo deve conter quatro valores.")
            left, top, right, bottom = (float(value) for value in bounds)

        if right < left:
            left, right = right, left
        if bottom < top:
            top, bottom = bottom, top

        start = int(np.searchsorted(self._sorted_x, left, side="left"))
        stop = int(np.searchsorted(self._sorted_x, right, side="right"))
        candidates = self._order[start:stop]
        if candidates.size == 0:
            return np.empty(0, dtype=np.intp)

        candidate_y = self._y[candidates]
        mask = (candidate_y >= top) & (candidate_y <= bottom)
        return candidates[mask].astype(np.intp, copy=False)

    def nearest(
        self,
        x: float,
        y: float,
        tolerance: float,
        eligible_mask: BoolArray | Sequence[bool] | None = None,
    ) -> int | None:
        """Retorna o índice do ponto mais próximo dentro da tolerância."""

        if tolerance < 0 or not np.isfinite(tolerance):
            raise ValueError("A tolerância deve ser finita e não negativa.")
        candidates = self.query_rect(
            Bounds(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
        )
        if eligible_mask is not None:
            mask = np.asarray(eligible_mask, dtype=np.bool_)
            if mask.ndim != 1 or mask.size != len(self):
                raise ValueError("A máscara de pontos deve corresponder ao índice.")
            candidates = candidates[mask[candidates]]
        if candidates.size == 0:
            return None

        dx = self._x[candidates] - x
        dy = self._y[candidates] - y
        distances = dx * dx + dy * dy
        inside = distances <= tolerance * tolerance
        if not inside.any():
            return None

        candidates = candidates[inside]
        distances = distances[inside]
        # Distância é a chave principal; índice original desempata de forma estável.
        position = int(np.lexsort((candidates, distances))[0])
        return int(candidates[position])


class StaticSegmentIndex:
    """Índice estático vetorizado para caixas envolventes de segmentos."""

    __slots__ = (
        "_x1",
        "_y1",
        "_x2",
        "_y2",
        "_min_x",
        "_max_x",
        "_min_y",
        "_max_y",
        "_order",
        "_sorted_min_x",
    )

    def __init__(
        self,
        x1: FloatArray,
        y1: FloatArray,
        x2: FloatArray,
        y2: FloatArray,
    ) -> None:
        arrays = [np.ascontiguousarray(values, dtype=np.float64) for values in (x1, y1, x2, y2)]
        if any(values.ndim != 1 for values in arrays):
            raise ValueError("As coordenadas dos segmentos devem ser vetores.")
        if len({values.size for values in arrays}) != 1:
            raise ValueError("As coordenadas dos segmentos devem possuir o mesmo tamanho.")
        if any(not np.isfinite(values).all() for values in arrays):
            raise ValueError("O índice não aceita coordenadas não finitas.")

        first_x, first_y, second_x, second_y = arrays
        self._x1 = first_x
        self._y1 = first_y
        self._x2 = second_x
        self._y2 = second_y
        self._min_x = np.minimum(first_x, second_x)
        self._max_x = np.maximum(first_x, second_x)
        self._min_y = np.minimum(first_y, second_y)
        self._max_y = np.maximum(first_y, second_y)
        self._order = np.argsort(self._min_x, kind="stable").astype(np.intp, copy=False)
        self._sorted_min_x = self._min_x[self._order]
        for values in (
            self._x1,
            self._y1,
            self._x2,
            self._y2,
            self._min_x,
            self._max_x,
            self._min_y,
            self._max_y,
            self._order,
            self._sorted_min_x,
        ):
            values.setflags(write=False)

    def __len__(self) -> int:
        return int(self._min_x.size)

    def query_rect(self, bounds: Bounds | Sequence[float]) -> IndexArray:
        """Retorna segmentos cujas caixas intersectam o retângulo."""

        if isinstance(bounds, Bounds):
            left, top, right, bottom = (
                bounds.left,
                bounds.top,
                bounds.right,
                bounds.bottom,
            )
        else:
            if len(bounds) != 4:
                raise ValueError("O retângulo deve conter quatro valores.")
            left, top, right, bottom = (float(value) for value in bounds)
        if right < left:
            left, right = right, left
        if bottom < top:
            top, bottom = bottom, top

        stop = int(np.searchsorted(self._sorted_min_x, right, side="right"))
        candidates = self._order[:stop]
        if candidates.size == 0:
            return np.empty(0, dtype=np.intp)
        mask = (
            (self._max_x[candidates] >= left)
            & (self._min_y[candidates] <= bottom)
            & (self._max_y[candidates] >= top)
        )
        return candidates[mask].astype(np.intp, copy=False)

    def nearest(
        self,
        x: float,
        y: float,
        tolerance: float,
        eligible_mask: BoolArray | Sequence[bool] | None = None,
    ) -> int | None:
        """Retorna o segmento mais próximo dentro da tolerância informada.

        A caixa de busca reduz os candidatos antes do cálculo vetorizado da
        distância exata ao segmento. Segmentos degenerados são tratados como
        pontos e o índice original desempata distâncias iguais.
        """

        if tolerance < 0 or not np.isfinite(tolerance):
            raise ValueError("A tolerância deve ser finita e não negativa.")
        candidates = self.query_rect(
            Bounds(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
        )
        if eligible_mask is not None:
            mask = np.asarray(eligible_mask, dtype=np.bool_)
            if mask.ndim != 1 or mask.size != len(self):
                raise ValueError("A máscara de trechos deve corresponder ao índice.")
            candidates = candidates[mask[candidates]]
        if candidates.size == 0:
            return None

        x1 = self._x1[candidates]
        y1 = self._y1[candidates]
        vx = self._x2[candidates] - x1
        vy = self._y2[candidates] - y1
        squared_lengths = vx * vx + vy * vy
        projection = np.zeros(candidates.size, dtype=np.float64)
        np.divide(
            (x - x1) * vx + (y - y1) * vy,
            squared_lengths,
            out=projection,
            where=squared_lengths > 0.0,
        )
        np.clip(projection, 0.0, 1.0, out=projection)
        dx = x - (x1 + projection * vx)
        dy = y - (y1 + projection * vy)
        distances = dx * dx + dy * dy
        inside = distances <= tolerance * tolerance
        if not inside.any():
            return None

        candidates = candidates[inside]
        distances = distances[inside]
        position = int(np.lexsort((candidates, distances))[0])
        return int(candidates[position])


class CircuitModel:
    """Armazena barras e metadados geográficos sem acoplamento com a cena."""

    __slots__ = (
        "_bar_ids",
        "_codes",
        "_x",
        "_y",
        "_by_id",
        "_bounds",
        "_spatial_index",
        "_name_suffixes",
        "crs",
        "source_path",
    )

    def __init__(
        self,
        bar_ids: Iterable[str],
        codes: Iterable[str],
        x: Iterable[float] | FloatArray,
        y: Iterable[float] | FloatArray,
        crs: UtmCrs,
        *,
        source_path: str | None = None,
        name_suffixes: tuple[str, ...] | None = None,
    ) -> None:
        ids = tuple(str(value) for value in bar_ids)
        code_values = tuple(str(value) for value in codes)
        x_array = np.ascontiguousarray(x, dtype=np.float64)
        y_array = np.ascontiguousarray(y, dtype=np.float64)

        size = len(ids)
        if len(code_values) != size or x_array.size != size or y_array.size != size:
            raise ValueError("Todos os campos da barra devem possuir o mesmo tamanho.")
        if size == 0:
            raise ValueError("O modelo deve conter ao menos uma barra.")
        if x_array.ndim != 1 or y_array.ndim != 1:
            raise ValueError("X e Y devem ser vetores unidimensionais.")
        if not np.isfinite(x_array).all() or not np.isfinite(y_array).all():
            raise ValueError("As coordenadas devem ser finitas.")

        by_id: dict[str, int] = {}
        for index, bar_id in enumerate(ids):
            if not bar_id:
                raise ValueError("BARRA_ID não pode ser vazio.")
            if bar_id in by_id:
                raise ValueError(f"BARRA_ID duplicado no modelo: {bar_id}")
            by_id[bar_id] = index

        x_array.setflags(write=False)
        y_array.setflags(write=False)
        self._bar_ids = ids
        self._codes = code_values
        self._x = x_array
        self._y = y_array
        self._by_id = by_id
        self._bounds = Bounds(
            float(x_array.min()),
            float(y_array.min()),
            float(x_array.max()),
            float(y_array.max()),
        )
        self._spatial_index = StaticPointIndex(x_array, y_array)
        self._name_suffixes = _checked_name_suffixes(name_suffixes, size)
        self.crs = crs
        self.source_path = source_path

    @property
    def name_suffixes(self) -> tuple[str, ...] | None:
        """Sufixo de desambiguação por linha, ou ``None`` com fonte única."""

        return self._name_suffixes

    def __len__(self) -> int:
        return len(self._bar_ids)

    @property
    def bar_ids(self) -> tuple[str, ...]:
        return self._bar_ids

    @property
    def codes(self) -> tuple[str, ...]:
        return self._codes

    @property
    def x(self) -> FloatArray:
        return self._x

    @property
    def y(self) -> FloatArray:
        return self._y

    @property
    def bounds(self) -> Bounds:
        return self._bounds

    @property
    def spatial_index(self) -> StaticPointIndex:
        return self._spatial_index

    def index_for_id(self, bar_id: str) -> int | None:
        return self._by_id.get(bar_id)

    def record(self, index: int) -> BarRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        return BarRecord(
            self._bar_ids[index],
            self._codes[index],
            float(self._x[index]),
            float(self._y[index]),
        )


class LoadModel:
    """Cargas que reutilizam as coordenadas das barras associadas."""

    __slots__ = (
        "bars",
        "_load_ids",
        "_bar_indices",
        "_external_ids",
        "_codes",
        "_snom_values",
        "_sadm_values",
        "_secondary_line_voltages",
        "_phases",
        "_connection_types",
        "_by_id",
        "_spatial_index",
        "_name_suffixes",
        "source_path",
    )

    def __init__(
        self,
        bars: CircuitModel,
        load_ids: Iterable[str],
        bar_indices: Iterable[int] | IndexArray,
        external_ids: Iterable[str],
        codes: Iterable[str],
        snom_values: Iterable[str],
        sadm_values: Iterable[str],
        secondary_line_voltages: Iterable[str],
        phases: Iterable[str],
        connection_types: Iterable[str],
        *,
        source_path: str | None = None,
        name_suffixes: tuple[str, ...] | None = None,
    ) -> None:
        ids = tuple(str(value) for value in load_ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                external_ids,
                codes,
                snom_values,
                sadm_values,
                secondary_line_voltages,
                phases,
                connection_types,
            )
        )
        associated_bars = np.ascontiguousarray(bar_indices, dtype=np.intp)
        size = len(ids)
        if size == 0:
            raise ValueError("O modelo deve conter ao menos uma carga.")
        if any(len(values) != size for values in text_columns):
            raise ValueError("Todos os campos da carga devem possuir o mesmo tamanho.")
        if associated_bars.ndim != 1 or associated_bars.size != size:
            raise ValueError("Os índices de barras devem formar um vetor compatível.")
        if (associated_bars < 0).any() or (associated_bars >= len(bars)).any():
            raise ValueError("Uma carga referencia uma barra inexistente.")

        by_id: dict[str, int] = {}
        for index, load_id in enumerate(ids):
            if not load_id:
                raise ValueError("CARGA_ID não pode ser vazio.")
            if load_id in by_id:
                raise ValueError(f"CARGA_ID duplicado no modelo: {load_id}")
            by_id[load_id] = index

        associated_bars.setflags(write=False)
        self.bars = bars
        self._load_ids = ids
        self._bar_indices = associated_bars
        (
            self._external_ids,
            self._codes,
            self._snom_values,
            self._sadm_values,
            self._secondary_line_voltages,
            self._phases,
            self._connection_types,
        ) = text_columns
        self._by_id = by_id
        self._spatial_index = StaticPointIndex(
            bars.x[associated_bars],
            bars.y[associated_bars],
        )
        self._name_suffixes = _checked_name_suffixes(name_suffixes, size)
        self.source_path = source_path

    @property
    def name_suffixes(self) -> tuple[str, ...] | None:
        """Sufixo de desambiguação por linha, ou ``None`` com fonte única."""

        return self._name_suffixes

    def __len__(self) -> int:
        return len(self._load_ids)

    @property
    def load_ids(self) -> tuple[str, ...]:
        return self._load_ids

    @property
    def bar_indices(self) -> IndexArray:
        return self._bar_indices

    @property
    def codes(self) -> tuple[str, ...]:
        return self._codes

    @property
    def snom_values(self) -> tuple[str, ...]:
        return self._snom_values

    @property
    def sadm_values(self) -> tuple[str, ...]:
        return self._sadm_values

    @property
    def phases(self) -> tuple[str, ...]:
        return self._phases

    @property
    def spatial_index(self) -> StaticPointIndex:
        return self._spatial_index

    def index_for_id(self, load_id: str) -> int | None:
        return self._by_id.get(load_id)

    def record(self, index: int) -> LoadRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        return LoadRecord(
            load_id=self._load_ids[index],
            bar_id=self.bars.bar_ids[int(self._bar_indices[index])],
            external_id=self._external_ids[index],
            code=self._codes[index],
            snom=self._snom_values[index],
            sadm=self._sadm_values[index],
            secondary_line_voltage=self._secondary_line_voltages[index],
            phases=self._phases[index],
            connection_type=self._connection_types[index],
        )


class CapacitorModel:
    """Bancos de capacitores que reutilizam as coordenadas das barras.

    Estruturalmente igual ao :class:`LoadModel` — mesma associação por índice de
    barra e o mesmo ``StaticPointIndex`` herdado — porque os dois compartilham
    toda a camada de render e as máscaras de visibilidade por circuito.
    """

    __slots__ = (
        "bars",
        "_capacitor_ids",
        "_bar_indices",
        "_external_ids",
        "_codes",
        "_nominal_voltages",
        "_q1_values",
        "_q2_values",
        "_q3_values",
        "_q4_values",
        "_phases",
        "_connection_types",
        "_by_id",
        "_spatial_index",
        "source_path",
    )

    def __init__(
        self,
        bars: CircuitModel,
        capacitor_ids: Iterable[str],
        bar_indices: Iterable[int] | IndexArray,
        external_ids: Iterable[str],
        codes: Iterable[str],
        nominal_voltages: Iterable[str],
        q1_values: Iterable[str],
        q2_values: Iterable[str],
        q3_values: Iterable[str],
        q4_values: Iterable[str],
        phases: Iterable[str],
        connection_types: Iterable[str],
        *,
        source_path: str | None = None,
    ) -> None:
        ids = tuple(str(value) for value in capacitor_ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                external_ids,
                codes,
                nominal_voltages,
                q1_values,
                q2_values,
                q3_values,
                q4_values,
                phases,
                connection_types,
            )
        )
        associated_bars = np.ascontiguousarray(bar_indices, dtype=np.intp)
        size = len(ids)
        if size == 0:
            raise ValueError("O modelo deve conter ao menos um capacitor.")
        if any(len(values) != size for values in text_columns):
            raise ValueError(
                "Todos os campos do capacitor devem possuir o mesmo tamanho."
            )
        if associated_bars.ndim != 1 or associated_bars.size != size:
            raise ValueError("Os índices de barras devem formar um vetor compatível.")
        if (associated_bars < 0).any() or (associated_bars >= len(bars)).any():
            raise ValueError("Um capacitor referencia uma barra inexistente.")

        by_id: dict[str, int] = {}
        for index, capacitor_id in enumerate(ids):
            if not capacitor_id:
                raise ValueError("CAPAC_ID não pode ser vazio.")
            if capacitor_id in by_id:
                raise ValueError(f"CAPAC_ID duplicado no modelo: {capacitor_id}")
            by_id[capacitor_id] = index

        associated_bars.setflags(write=False)
        self.bars = bars
        self._capacitor_ids = ids
        self._bar_indices = associated_bars
        (
            self._external_ids,
            self._codes,
            self._nominal_voltages,
            self._q1_values,
            self._q2_values,
            self._q3_values,
            self._q4_values,
            self._phases,
            self._connection_types,
        ) = text_columns
        self._by_id = by_id
        self._spatial_index = StaticPointIndex(
            bars.x[associated_bars],
            bars.y[associated_bars],
        )
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._capacitor_ids)

    @property
    def capacitor_ids(self) -> tuple[str, ...]:
        return self._capacitor_ids

    @property
    def bar_indices(self) -> IndexArray:
        return self._bar_indices

    @property
    def codes(self) -> tuple[str, ...]:
        return self._codes

    @property
    def nominal_voltages(self) -> tuple[str, ...]:
        return self._nominal_voltages

    @property
    def phases(self) -> tuple[str, ...]:
        return self._phases

    @property
    def spatial_index(self) -> StaticPointIndex:
        return self._spatial_index

    def index_for_id(self, capacitor_id: str) -> int | None:
        return self._by_id.get(capacitor_id)

    def record(self, index: int) -> CapacitorRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        return CapacitorRecord(
            capacitor_id=self._capacitor_ids[index],
            bar_id=self.bars.bar_ids[int(self._bar_indices[index])],
            external_id=self._external_ids[index],
            code=self._codes[index],
            nominal_voltage=self._nominal_voltages[index],
            q1=self._q1_values[index],
            q2=self._q2_values[index],
            q3=self._q3_values[index],
            q4=self._q4_values[index],
            phases=self._phases[index],
            connection_type=self._connection_types[index],
        )


class GeneratorModel:
    """Geradores associados a cargas e posicionados nas barras dessas cargas."""

    __slots__ = (
        "loads",
        "bars",
        "_generator_ids",
        "_load_indices",
        "_bar_indices",
        "_mt_cons_ids",
        "_generator_codes",
        "_nominal_voltages",
        "_nominal_powers",
        "_connections",
        "_curve_ids",
        "_generation_kwh",
        "_consumer_ids",
        "_consumer_codes",
        "_external_ids",
        "_names",
        "_phases",
        "_by_id",
        "_spatial_index",
        "source_paths",
    )

    def __init__(
        self,
        loads: LoadModel,
        generator_ids: Iterable[str],
        load_indices: Iterable[int] | IndexArray,
        mt_cons_ids: Iterable[str],
        generator_codes: Iterable[str],
        nominal_voltages: Iterable[str],
        nominal_powers: Iterable[str],
        connections: Iterable[str],
        curve_ids: Iterable[str],
        generation_kwh: Iterable[str],
        consumer_ids: Iterable[str],
        consumer_codes: Iterable[str],
        external_ids: Iterable[str],
        names: Iterable[str],
        phases: Iterable[str],
        *,
        source_paths: tuple[str, str] | None = None,
    ) -> None:
        ids = tuple(str(value) for value in generator_ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                mt_cons_ids,
                generator_codes,
                nominal_voltages,
                nominal_powers,
                connections,
                curve_ids,
                generation_kwh,
                consumer_ids,
                consumer_codes,
                external_ids,
                names,
                phases,
            )
        )
        associated_loads = np.ascontiguousarray(load_indices, dtype=np.intp)
        size = len(ids)
        if size == 0:
            raise ValueError("O modelo deve conter ao menos um gerador.")
        if any(len(values) != size for values in text_columns):
            raise ValueError("Todos os campos do gerador devem possuir o mesmo tamanho.")
        if associated_loads.ndim != 1 or associated_loads.size != size:
            raise ValueError("Os índices de cargas devem formar um vetor compatível.")
        if (associated_loads < 0).any() or (associated_loads >= len(loads)).any():
            raise ValueError("Um gerador referencia uma carga inexistente.")

        by_id: dict[str, int] = {}
        for index, generator_id in enumerate(ids):
            if not generator_id:
                raise ValueError("GERADOR_ID não pode ser vazio.")
            if generator_id in by_id:
                raise ValueError(f"GERADOR_ID duplicado no modelo: {generator_id}")
            by_id[generator_id] = index

        bar_indices = np.ascontiguousarray(
            loads.bar_indices[associated_loads], dtype=np.intp
        )
        associated_loads.setflags(write=False)
        bar_indices.setflags(write=False)
        self.loads = loads
        self.bars = loads.bars
        self._generator_ids = ids
        self._load_indices = associated_loads
        self._bar_indices = bar_indices
        (
            self._mt_cons_ids,
            self._generator_codes,
            self._nominal_voltages,
            self._nominal_powers,
            self._connections,
            self._curve_ids,
            self._generation_kwh,
            self._consumer_ids,
            self._consumer_codes,
            self._external_ids,
            self._names,
            self._phases,
        ) = text_columns
        self._by_id = by_id
        self._spatial_index = StaticPointIndex(
            self.bars.x[bar_indices], self.bars.y[bar_indices]
        )
        self.source_paths = source_paths

    def __len__(self) -> int:
        return len(self._generator_ids)

    @property
    def generator_ids(self) -> tuple[str, ...]:
        return self._generator_ids

    @property
    def generator_codes(self) -> tuple[str, ...]:
        return self._generator_codes

    @property
    def load_indices(self) -> IndexArray:
        return self._load_indices

    @property
    def bar_indices(self) -> IndexArray:
        return self._bar_indices

    @property
    def spatial_index(self) -> StaticPointIndex:
        return self._spatial_index

    def index_for_id(self, generator_id: str) -> int | None:
        return self._by_id.get(generator_id)

    def record(self, index: int) -> GeneratorRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        load_index = int(self._load_indices[index])
        load_id = self.loads.load_ids[load_index]
        return GeneratorRecord(
            generator_id=self._generator_ids[index],
            mt_cons_id=self._mt_cons_ids[index],
            generator_code=self._generator_codes[index],
            nominal_voltage=self._nominal_voltages[index],
            nominal_power=self._nominal_powers[index],
            connection=self._connections[index],
            curve_id=self._curve_ids[index],
            generation_kwh=self._generation_kwh[index],
            consumer_id=self._consumer_ids[index],
            load_id=load_id,
            consumer_code=self._consumer_codes[index],
            external_id=self._external_ids[index],
            name=self._names[index],
            phases=self._phases[index],
            bar_id=self.bars.bar_ids[int(self._bar_indices[index])],
        )


class CableModel:
    """Catálogo de cabos, sem geometria e sem dependência de outros modelos.

    Os trechos referenciam cabos por `CABOF_ID`/`CABON_ID` apenas como texto, de
    modo que o catálogo é uma raiz independente: importá-lo não invalida nada e
    nenhuma outra importação o invalida.
    """

    __slots__ = (
        "_cable_ids",
        "_cable_types",
        "_codes",
        "_iadm_values",
        "_gmr_values",
        "_r_values",
        "_x_values",
        "_qcap_values",
        "_r0_values",
        "_x0_values",
        "_r1_values",
        "_x1_values",
        "_names",
        "_external_ids",
        "_by_id",
        "source_path",
    )

    def __init__(
        self,
        cable_ids: Iterable[str],
        cable_types: Iterable[str],
        codes: Iterable[str],
        iadm_values: Iterable[str],
        gmr_values: Iterable[str],
        r_values: Iterable[str],
        x_values: Iterable[str],
        qcap_values: Iterable[str],
        r0_values: Iterable[str],
        x0_values: Iterable[str],
        r1_values: Iterable[str],
        x1_values: Iterable[str],
        names: Iterable[str],
        external_ids: Iterable[str],
        *,
        source_path: str | None = None,
    ) -> None:
        ids = tuple(str(value) for value in cable_ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                cable_types,
                codes,
                iadm_values,
                gmr_values,
                r_values,
                x_values,
                qcap_values,
                r0_values,
                x0_values,
                r1_values,
                x1_values,
                names,
                external_ids,
            )
        )
        size = len(ids)
        if size == 0:
            raise ValueError("O modelo deve conter ao menos um cabo.")
        if any(len(values) != size for values in text_columns):
            raise ValueError("Todos os campos do cabo devem possuir o mesmo tamanho.")

        by_id: dict[str, int] = {}
        for index, cable_id in enumerate(ids):
            if not cable_id:
                raise ValueError("CABO_ID não pode ser vazio.")
            if cable_id in by_id:
                raise ValueError(f"CABO_ID duplicado no modelo: {cable_id}")
            by_id[cable_id] = index

        self._cable_ids = ids
        (
            self._cable_types,
            self._codes,
            self._iadm_values,
            self._gmr_values,
            self._r_values,
            self._x_values,
            self._qcap_values,
            self._r0_values,
            self._x0_values,
            self._r1_values,
            self._x1_values,
            self._names,
            self._external_ids,
        ) = text_columns
        self._by_id = by_id
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._cable_ids)

    @property
    def cable_ids(self) -> tuple[str, ...]:
        return self._cable_ids

    @property
    def codes(self) -> tuple[str, ...]:
        return self._codes

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    def index_for_id(self, cable_id: str) -> int | None:
        return self._by_id.get(cable_id)

    def record(self, index: int) -> CableRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        return CableRecord(
            cable_id=self._cable_ids[index],
            cable_type=self._cable_types[index],
            code=self._codes[index],
            iadm=self._iadm_values[index],
            gmr=self._gmr_values[index],
            r=self._r_values[index],
            x=self._x_values[index],
            qcap=self._qcap_values[index],
            r0=self._r0_values[index],
            x0=self._x0_values[index],
            r1=self._r1_values[index],
            x1=self._x1_values[index],
            name=self._names[index],
            external_id=self._external_ids[index],
        )

    def record_for_id(self, cable_id: str) -> CableRecord | None:
        index = self.index_for_id(cable_id)
        return None if index is None else self.record(index)


class LoadPatternModel:
    """Grupos completos de quatro patamares, indexados pela carga."""

    __slots__ = (
        "loads",
        "_records_by_load",
        "_load_count",
        "_record_count",
        "source_path",
    )

    def __init__(
        self,
        loads: LoadModel,
        records_by_load: Iterable[Sequence[LoadPatternRecord] | None],
        *,
        source_path: str | None = None,
    ) -> None:
        raw_groups = tuple(records_by_load)
        if len(raw_groups) != len(loads):
            raise ValueError("Os grupos de patamares devem corresponder às cargas.")

        groups: list[tuple[LoadPatternRecord, ...] | None] = []
        load_count = 0
        for load_index, raw_group in enumerate(raw_groups):
            if raw_group is None:
                groups.append(None)
                continue
            group = tuple(sorted(raw_group, key=lambda record: record.npat))
            if not group:
                groups.append(None)
                continue
            if len(group) != 4 or tuple(record.npat for record in group) != (
                0,
                1,
                2,
                3,
            ):
                raise ValueError(
                    "Cada carga deve possuir exatamente os patamares 0, 1, 2 e 3."
                )
            expected_load_id = loads.load_ids[load_index]
            if any(record.load_id != expected_load_id for record in group):
                raise ValueError("Um patamar referencia uma carga incompatível.")
            groups.append(group)
            load_count += 1

        if load_count == 0:
            raise ValueError("O modelo deve conter ao menos um grupo de patamares.")
        self.loads = loads
        self._records_by_load = tuple(groups)
        self._load_count = load_count
        self._record_count = load_count * 4
        self.source_path = source_path

    def __len__(self) -> int:
        return self._load_count

    @property
    def record_count(self) -> int:
        return self._record_count

    def records_for_load(self, load_index: int) -> tuple[LoadPatternRecord, ...]:
        if not 0 <= int(load_index) < len(self.loads):
            raise IndexError(load_index)
        group = self._records_by_load[int(load_index)]
        return () if group is None else group


class LineNetworkModel:
    """Trechos que referenciam as barras por índice, sem duplicar coordenadas."""

    __slots__ = (
        "bars",
        "_segment_ids",
        "_codes",
        "_phases",
        "_start_indices",
        "_end_indices",
        "_arrangement_ids",
        "_phase_cable_ids",
        "_neutral_cable_ids",
        "_lengths",
        "_by_id",
        "_bounds",
        "_spatial_index",
        "source_path",
    )

    def __init__(
        self,
        bars: CircuitModel,
        segment_ids: Iterable[str],
        codes: Iterable[str],
        phases: Iterable[str],
        start_indices: Iterable[int] | IndexArray,
        end_indices: Iterable[int] | IndexArray,
        arrangement_ids: Iterable[str],
        phase_cable_ids: Iterable[str],
        neutral_cable_ids: Iterable[str],
        lengths: Iterable[float] | FloatArray,
        *,
        source_path: str | None = None,
    ) -> None:
        ids = tuple(str(value) for value in segment_ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                codes,
                phases,
                arrangement_ids,
                phase_cable_ids,
                neutral_cable_ids,
            )
        )
        starts = np.ascontiguousarray(start_indices, dtype=np.intp)
        ends = np.ascontiguousarray(end_indices, dtype=np.intp)
        length_values = np.ascontiguousarray(lengths, dtype=np.float64)
        size = len(ids)
        if any(len(values) != size for values in text_columns):
            raise ValueError("Todos os campos do trecho devem possuir o mesmo tamanho.")
        if starts.size != size or ends.size != size or length_values.size != size:
            raise ValueError("Todos os campos do trecho devem possuir o mesmo tamanho.")
        if size == 0:
            raise ValueError("A rede deve conter ao menos um trecho.")
        if starts.ndim != 1 or ends.ndim != 1 or length_values.ndim != 1:
            raise ValueError("Os campos vetoriais dos trechos devem ser unidimensionais.")
        if (starts < 0).any() or (ends < 0).any() or (starts >= len(bars)).any() or (ends >= len(bars)).any():
            raise ValueError("Um trecho referencia uma barra inexistente.")
        if np.isinf(length_values).any() or (length_values[np.isfinite(length_values)] < 0).any():
            raise ValueError("COMPR deve ser vazio ou um número finito não negativo.")

        by_id: dict[str, int] = {}
        for index, segment_id in enumerate(ids):
            if not segment_id:
                raise ValueError("TRECHO_ID não pode ser vazio.")
            if segment_id in by_id:
                raise ValueError(f"TRECHO_ID duplicado no modelo: {segment_id}")
            by_id[segment_id] = index

        starts.setflags(write=False)
        ends.setflags(write=False)
        length_values.setflags(write=False)
        self.bars = bars
        self._segment_ids = ids
        (
            self._codes,
            self._phases,
            self._arrangement_ids,
            self._phase_cable_ids,
            self._neutral_cable_ids,
        ) = text_columns
        self._start_indices = starts
        self._end_indices = ends
        self._lengths = length_values
        self._by_id = by_id

        x1 = bars.x[starts]
        y1 = bars.y[starts]
        x2 = bars.x[ends]
        y2 = bars.y[ends]
        self._bounds = Bounds(
            float(min(x1.min(), x2.min())),
            float(min(y1.min(), y2.min())),
            float(max(x1.max(), x2.max())),
            float(max(y1.max(), y2.max())),
        )
        self._spatial_index = StaticSegmentIndex(x1, y1, x2, y2)
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._segment_ids)

    @property
    def segment_ids(self) -> tuple[str, ...]:
        return self._segment_ids

    @property
    def codes(self) -> tuple[str, ...]:
        return self._codes

    @property
    def phases(self) -> tuple[str, ...]:
        return self._phases

    @property
    def start_indices(self) -> IndexArray:
        return self._start_indices

    @property
    def end_indices(self) -> IndexArray:
        return self._end_indices

    @property
    def lengths(self) -> FloatArray:
        return self._lengths

    @property
    def bounds(self) -> Bounds:
        return self._bounds

    @property
    def spatial_index(self) -> StaticSegmentIndex:
        return self._spatial_index

    def index_for_id(self, segment_id: str) -> int | None:
        return self._by_id.get(segment_id)

    def record(self, index: int) -> SegmentRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        length = float(self._lengths[index])
        return SegmentRecord(
            segment_id=self._segment_ids[index],
            code=self._codes[index],
            phases=self._phases[index],
            start_bar_id=self.bars.bar_ids[int(self._start_indices[index])],
            end_bar_id=self.bars.bar_ids[int(self._end_indices[index])],
            arrangement_id=self._arrangement_ids[index],
            phase_cable_id=self._phase_cable_ids[index],
            neutral_cable_id=self._neutral_cable_ids[index],
            length=None if np.isnan(length) else length,
        )


class SwitchModel:
    """Metadados de chaves indexados pelos trechos que os representam."""

    __slots__ = (
        "segments",
        "_switch_ids",
        "_switch_type_ids",
        "_circuit_ids",
        "_segment_indices",
        "_codes",
        "_states",
        "_normal_states",
        "_corn_values",
        "_elo_values",
        "_elo_types",
        "_type_names",
        "_switchable_values",
        "_by_id",
        "_record_by_segment",
        "source_path",
    )

    def __init__(
        self,
        segments: LineNetworkModel,
        switch_ids: Iterable[str],
        switch_type_ids: Iterable[str],
        circuit_ids: Iterable[str],
        segment_indices: Iterable[int] | IndexArray,
        codes: Iterable[str],
        states: Iterable[str],
        normal_states: Iterable[str],
        corn_values: Iterable[str],
        elo_values: Iterable[str],
        elo_types: Iterable[str],
        *,
        type_names: Iterable[str] | None = None,
        switchable_values: Iterable[str] | None = None,
        source_path: str | None = None,
    ) -> None:
        ids = tuple(str(value) for value in switch_ids)
        # Keyword-only com padrão vazio: vinte e cinco pontos constroem este
        # modelo posicionalmente, e o tipo resolvido é informativo — exigi-lo
        # quebraria todos eles para acrescentar duas colunas de leitura.
        blank = ("",) * len(ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                switch_type_ids,
                circuit_ids,
                codes,
                states,
                normal_states,
                corn_values,
                elo_values,
                elo_types,
                blank if type_names is None else type_names,
                blank if switchable_values is None else switchable_values,
            )
        )
        associated_segments = np.ascontiguousarray(segment_indices, dtype=np.intp)
        size = len(ids)
        if size == 0:
            raise ValueError("O modelo deve conter ao menos uma chave.")
        if any(len(values) != size for values in text_columns):
            raise ValueError("Todos os campos da chave devem possuir o mesmo tamanho.")
        if associated_segments.ndim != 1 or associated_segments.size != size:
            raise ValueError("Os índices de trechos devem formar um vetor compatível.")
        if (associated_segments < 0).any() or (associated_segments >= len(segments)).any():
            raise ValueError("Uma chave referencia um trecho inexistente.")

        by_id: dict[str, int] = {}
        record_by_segment = np.full(len(segments), -1, dtype=np.intp)
        for index, (switch_id, segment_index) in enumerate(
            zip(ids, associated_segments, strict=True)
        ):
            if not switch_id:
                raise ValueError("CHAVE_ID não pode ser vazio.")
            if switch_id in by_id:
                raise ValueError(f"CHAVE_ID duplicado no modelo: {switch_id}")
            segment = int(segment_index)
            if record_by_segment[segment] >= 0:
                raise ValueError(
                    f"O trecho {segments.segment_ids[segment]} possui mais de uma chave."
                )
            by_id[switch_id] = index
            record_by_segment[segment] = index

        associated_segments.setflags(write=False)
        record_by_segment.setflags(write=False)
        self.segments = segments
        self._switch_ids = ids
        (
            self._switch_type_ids,
            self._circuit_ids,
            self._codes,
            self._states,
            self._normal_states,
            self._corn_values,
            self._elo_values,
            self._elo_types,
            self._type_names,
            self._switchable_values,
        ) = text_columns
        self._segment_indices = associated_segments
        self._by_id = by_id
        self._record_by_segment = record_by_segment
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._switch_ids)

    @property
    def switch_ids(self) -> tuple[str, ...]:
        return self._switch_ids

    @property
    def codes(self) -> tuple[str, ...]:
        return self._codes

    @property
    def segment_indices(self) -> IndexArray:
        return self._segment_indices

    @property
    def circuit_ids(self) -> tuple[str, ...]:
        return self._circuit_ids

    @property
    def states(self) -> tuple[str, ...]:
        return self._states

    @property
    def record_indices_by_segment(self) -> IndexArray:
        """Índice do registro de chave por trecho, ou -1 para trecho comum."""

        return self._record_by_segment

    def index_for_id(self, switch_id: str) -> int | None:
        return self._by_id.get(switch_id)

    def record(self, index: int) -> SwitchRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        segment_index = int(self._segment_indices[index])
        return SwitchRecord(
            switch_id=self._switch_ids[index],
            switch_type_id=self._switch_type_ids[index],
            circuit_id=self._circuit_ids[index],
            segment_id=self.segments.segment_ids[segment_index],
            code=self._codes[index],
            state=self._states[index],
            normal_state=self._normal_states[index],
            corn=self._corn_values[index],
            elo=self._elo_values[index],
            elo_type=self._elo_types[index],
            type_name=self._type_names[index],
            switchable=self._switchable_values[index],
        )

    def record_for_segment(self, segment_index: int) -> SwitchRecord | None:
        if not 0 <= int(segment_index) < len(self.segments):
            raise IndexError(segment_index)
        record_index = int(self._record_by_segment[int(segment_index)])
        return None if record_index < 0 else self.record(record_index)

    def record_for_segment_id(self, segment_id: str) -> SwitchRecord | None:
        segment_index = self.segments.index_for_id(segment_id)
        if segment_index is None:
            return None
        return self.record_for_segment(segment_index)


class RegulatorModel:
    """Metadados de reguladores de tensão indexados pelos trechos onde estão.

    Estruturalmente igual ao :class:`SwitchModel` — mesmo vínculo 1:1 com o
    trecho e o mesmo ``_record_by_segment`` que torna "este trecho tem
    regulador?" uma consulta O(1). A diferença está fora do modelo: reguladores
    **não participam da topologia**. Eles não interrompem nem energizam nada,
    então nem ``NetworkTopology.trace()`` nem ``CircuitCatalogModel`` os
    consultam, e importá-los não invalida análise alguma.
    """

    __slots__ = (
        "segments",
        "_regulator_ids",
        "_external_ids",
        "_segment_indices",
        "_codes",
        "_connections",
        "_snom_values",
        "_regulation_ranges",
        "_step_counts",
        "_tap_values",
        "_inom_values",
        "_vnom_values",
        "_by_id",
        "_record_by_segment",
        "source_path",
    )

    def __init__(
        self,
        segments: LineNetworkModel,
        regulator_ids: Iterable[str],
        segment_indices: Iterable[int] | IndexArray,
        external_ids: Iterable[str],
        codes: Iterable[str],
        connections: Iterable[str],
        snom_values: Iterable[str],
        regulation_ranges: Iterable[str],
        step_counts: Iterable[str],
        tap_values: Iterable[str],
        inom_values: Iterable[str],
        vnom_values: Iterable[str],
        *,
        source_path: str | None = None,
    ) -> None:
        ids = tuple(str(value) for value in regulator_ids)
        text_columns = tuple(
            tuple(str(value) for value in values)
            for values in (
                external_ids,
                codes,
                connections,
                snom_values,
                regulation_ranges,
                step_counts,
                tap_values,
                inom_values,
                vnom_values,
            )
        )
        associated_segments = np.ascontiguousarray(segment_indices, dtype=np.intp)
        size = len(ids)
        if size == 0:
            raise ValueError("O modelo deve conter ao menos um regulador.")
        if any(len(values) != size for values in text_columns):
            raise ValueError(
                "Todos os campos do regulador devem possuir o mesmo tamanho."
            )
        if associated_segments.ndim != 1 or associated_segments.size != size:
            raise ValueError("Os índices de trechos devem formar um vetor compatível.")
        if (
            (associated_segments < 0).any()
            or (associated_segments >= len(segments)).any()
        ):
            raise ValueError("Um regulador referencia um trecho inexistente.")

        by_id: dict[str, int] = {}
        record_by_segment = np.full(len(segments), -1, dtype=np.intp)
        for index, (regulator_id, segment_index) in enumerate(
            zip(ids, associated_segments, strict=True)
        ):
            if not regulator_id:
                raise ValueError("REGU_ID não pode ser vazio.")
            if regulator_id in by_id:
                raise ValueError(f"REGU_ID duplicado no modelo: {regulator_id}")
            segment = int(segment_index)
            if record_by_segment[segment] >= 0:
                raise ValueError(
                    f"O trecho {segments.segment_ids[segment]} possui mais de "
                    "um regulador."
                )
            by_id[regulator_id] = index
            record_by_segment[segment] = index

        associated_segments.setflags(write=False)
        record_by_segment.setflags(write=False)
        self.segments = segments
        self._regulator_ids = ids
        (
            self._external_ids,
            self._codes,
            self._connections,
            self._snom_values,
            self._regulation_ranges,
            self._step_counts,
            self._tap_values,
            self._inom_values,
            self._vnom_values,
        ) = text_columns
        self._segment_indices = associated_segments
        self._by_id = by_id
        self._record_by_segment = record_by_segment
        self.source_path = source_path

    def __len__(self) -> int:
        return len(self._regulator_ids)

    @property
    def regulator_ids(self) -> tuple[str, ...]:
        return self._regulator_ids

    @property
    def codes(self) -> tuple[str, ...]:
        return self._codes

    # As colunas restantes ficam expostas para permitir reconstruir o modelo com
    # um campo trocado (ver ``regulator_overrides.apply_overrides``), sem que
    # ninguém precise alcançar os atributos privados.
    @property
    def external_ids(self) -> tuple[str, ...]:
        return self._external_ids

    @property
    def connections(self) -> tuple[str, ...]:
        return self._connections

    @property
    def snom_values(self) -> tuple[str, ...]:
        return self._snom_values

    @property
    def regulation_ranges(self) -> tuple[str, ...]:
        return self._regulation_ranges

    @property
    def step_counts(self) -> tuple[str, ...]:
        return self._step_counts

    @property
    def tap_values(self) -> tuple[str, ...]:
        return self._tap_values

    @property
    def inom_values(self) -> tuple[str, ...]:
        return self._inom_values

    @property
    def vnom_values(self) -> tuple[str, ...]:
        return self._vnom_values

    @property
    def segment_indices(self) -> IndexArray:
        return self._segment_indices

    @property
    def record_indices_by_segment(self) -> IndexArray:
        """Índice do registro por trecho, ou -1 para trecho sem regulador."""

        return self._record_by_segment

    def index_for_id(self, regulator_id: str) -> int | None:
        return self._by_id.get(regulator_id)

    def record(self, index: int) -> RegulatorRecord:
        if not 0 <= index < len(self):
            raise IndexError(index)
        segment_index = int(self._segment_indices[index])
        return RegulatorRecord(
            regulator_id=self._regulator_ids[index],
            segment_id=self.segments.segment_ids[segment_index],
            external_id=self._external_ids[index],
            code=self._codes[index],
            connection=self._connections[index],
            snom=self._snom_values[index],
            regulation_range=self._regulation_ranges[index],
            step_count=self._step_counts[index],
            tap=self._tap_values[index],
            inom=self._inom_values[index],
            vnom=self._vnom_values[index],
        )

    def record_for_segment(self, segment_index: int) -> RegulatorRecord | None:
        if not 0 <= int(segment_index) < len(self.segments):
            raise IndexError(segment_index)
        record_index = int(self._record_by_segment[int(segment_index)])
        return None if record_index < 0 else self.record(record_index)

    def record_for_segment_id(self, segment_id: str) -> RegulatorRecord | None:
        segment_index = self.segments.index_for_id(segment_id)
        if segment_index is None:
            return None
        return self.record_for_segment(segment_index)


def _readonly_indices(values: Iterable[int] | IndexArray) -> IndexArray:
    result = np.ascontiguousarray(values, dtype=np.intp)
    if result.ndim != 1:
        raise ValueError("Os índices devem formar um vetor unidimensional.")
    result.setflags(write=False)
    return result


class NetworkTopology:
    """Adjacência CSR e busca elétrica sobre uma rede imutável de trechos."""

    __slots__ = (
        "segments",
        "switches",
        "_offsets",
        "_incidence_segments",
        "_incidence_neighbors",
        "_bar_marks",
        "_segment_marks",
        "_generation",
    )

    def __init__(
        self,
        segments: LineNetworkModel,
        switches: SwitchModel | None = None,
    ) -> None:
        if switches is not None and switches.segments is not segments:
            raise ValueError("As chaves devem pertencer à rede usada na topologia.")
        self.segments = segments
        self.switches = switches

        bar_count = len(segments.bars)
        segment_count = len(segments)
        starts = segments.start_indices
        ends = segments.end_indices
        degrees = np.bincount(
            np.concatenate((starts, ends)), minlength=bar_count
        ).astype(np.intp, copy=False)
        offsets = np.empty(bar_count + 1, dtype=np.intp)
        offsets[0] = 0
        np.cumsum(degrees, out=offsets[1:])
        incidence_segments = np.empty(segment_count * 2, dtype=np.intp)
        incidence_neighbors = np.empty(segment_count * 2, dtype=np.intp)
        cursor = offsets[:-1].copy()
        for segment_index, (start_value, end_value) in enumerate(
            zip(starts, ends, strict=True)
        ):
            start = int(start_value)
            end = int(end_value)
            start_position = int(cursor[start])
            incidence_segments[start_position] = segment_index
            incidence_neighbors[start_position] = end
            cursor[start] += 1
            end_position = int(cursor[end])
            incidence_segments[end_position] = segment_index
            incidence_neighbors[end_position] = start
            cursor[end] += 1

        for values in (offsets, incidence_segments, incidence_neighbors):
            values.setflags(write=False)
        self._offsets = offsets
        self._incidence_segments = incidence_segments
        self._incidence_neighbors = incidence_neighbors
        self._bar_marks = np.zeros(bar_count, dtype=np.int64)
        self._segment_marks = np.zeros(segment_count, dtype=np.int64)
        self._generation = 0

    def _next_generation(self) -> int:
        if self._generation >= np.iinfo(np.int64).max - 1:
            self._bar_marks.fill(0)
            self._segment_marks.fill(0)
            self._generation = 0
        self._generation += 1
        return self._generation

    @property
    def incidence_offsets(self) -> IndexArray:
        """Offsets CSR das incidências de cada barra."""

        return self._offsets

    @property
    def incidence_segments(self) -> IndexArray:
        """Índice do trecho em cada posição da adjacência CSR."""

        return self._incidence_segments

    @property
    def incidence_neighbors(self) -> IndexArray:
        """Barra oposta ao trecho em cada posição da adjacência CSR."""

        return self._incidence_neighbors

    def trace(
        self,
        circuit_id: str,
        root_bar_index: int,
        direct_switch_indices: Iterable[int] | IndexArray = (),
        *,
        cancel_check: Callable[[], bool] | None = None,
        respect_circuit_owner: bool = True,
    ) -> CircuitMembership:
        """Executa BFS, descobrindo barras e somente trechos que não são chaves."""

        if not 0 <= int(root_bar_index) < len(self.segments.bars):
            raise IndexError(root_bar_index)
        generation = self._next_generation()
        root = int(root_bar_index)
        queue: deque[int] = deque((root,))
        self._bar_marks[root] = generation
        bars: list[int] = [root]
        common_segments: list[int] = []
        inspected = 0

        switch_by_segment = (
            None
            if self.switches is None
            else self.switches.record_indices_by_segment
        )
        while queue:
            bar_index = queue.popleft()
            start = int(self._offsets[bar_index])
            stop = int(self._offsets[bar_index + 1])
            for position in range(start, stop):
                inspected += 1
                if (
                    cancel_check is not None
                    and inspected % 4_096 == 0
                    and cancel_check()
                ):
                    raise InterruptedError("Construção da topologia cancelada.")
                segment_index = int(self._incidence_segments[position])
                neighbor = int(self._incidence_neighbors[position])
                switch_record_index = (
                    -1
                    if switch_by_segment is None
                    else int(switch_by_segment[segment_index])
                )
                if switch_record_index >= 0:
                    assert self.switches is not None
                    traversable = (
                        self.switches.states[switch_record_index].strip() == "1"
                        and (not respect_circuit_owner or
                             self.switches.circuit_ids[switch_record_index].strip() == circuit_id)
                    )
                    if not traversable:
                        continue
                elif self._segment_marks[segment_index] != generation:
                    self._segment_marks[segment_index] = generation
                    common_segments.append(segment_index)

                if self._bar_marks[neighbor] != generation:
                    self._bar_marks[neighbor] = generation
                    bars.append(neighbor)
                    queue.append(neighbor)

        if cancel_check is not None and cancel_check():
            raise InterruptedError("Construção da topologia cancelada.")
        bar_array = _readonly_indices(bars)
        common_array = _readonly_indices(common_segments)
        if not respect_circuit_owner and self.switches is not None:
            direct_switch_indices = [int(segment) for segment in self.switches.segment_indices
                                     if self._bar_marks[self.segments.start_indices[segment]] == generation
                                     and self._bar_marks[self.segments.end_indices[segment]] == generation]
        switch_array = _readonly_indices(direct_switch_indices)
        if common_array.size and switch_array.size:
            all_segments = _readonly_indices(
                np.concatenate((common_array, switch_array))
            )
        elif common_array.size:
            all_segments = common_array
        else:
            all_segments = switch_array
        return CircuitMembership(
            bar_indices=bar_array,
            common_segment_indices=common_array,
            switch_segment_indices=switch_array,
            segment_indices=all_segments,
        )


def switch_circuit_assignments(
    switches: "SwitchModel | None",
    valid_circuit_ids: Collection[str],
) -> tuple[dict[str, list[int]], tuple[str, ...]]:
    """Trechos de chave por circuito, e os avisos do que não coube.

    A associação **não** é topológica: uma chave vai para o circuito cujo
    ``CIRC_ID`` casa com o seu, esteja ela conectada a ele ou não. É essa
    igualdade de string que obriga o ``CIRC_ID`` a ser único quando fontes
    diferentes convivem no mesmo modelo — sem isso, uma chave de uma fonte
    entraria no circuito homônimo de outra.

    Devolve as duas coisas juntas de propósito: ``CircuitCatalogModel.build``
    precisa das associações e dos avisos, e quem recompõe um catálogo a partir
    de associações já prontas precisa só dos avisos, recalculados para o
    subconjunto de chaves que sobrou. Duas funções divergiriam em silêncio.
    """

    ids = set(valid_circuit_ids)
    direct_switches: dict[str, list[int]] = {circuit_id: [] for circuit_id in ids}
    warnings: list[str] = []
    if switches is None:
        return direct_switches, ()
    for record_index, segment_value in enumerate(switches.segment_indices):
        switch_id = switches.switch_ids[record_index]
        circuit_id = switches.circuit_ids[record_index].strip()
        state = switches.states[record_index].strip()
        if state not in {"0", "1"}:
            warnings.append(
                f"Chave {switch_id}: ESTADO '{state or '<vazio>'}' inválido; "
                "a travessia foi bloqueada."
            )
        if circuit_id not in ids:
            warnings.append(
                f"Chave {switch_id}: CIRC_ID '{circuit_id or '<vazio>'}' "
                "não existe no catálogo; a chave ficou sem circuito."
            )
            continue
        direct_switches[circuit_id].append(int(segment_value))
    return direct_switches, tuple(warnings)


class CircuitCatalogModel:
    """Circuitos, associações calculadas e identidade da topologia utilizada."""

    __slots__ = (
        "segments",
        "switches",
        "_definitions",
        "_memberships",
        "_by_id",
        "_segment_circuit_offsets",
        "_segment_circuit_indices",
        "_segment_owner_counts",
        "_overlapping_segment_indices",
        "topology_warnings",
        "source_path",
    )

    def __init__(
        self,
        segments: LineNetworkModel,
        switches: SwitchModel | None,
        definitions: Iterable[CircuitDefinition],
        memberships: Iterable[CircuitMembership],
        *,
        topology_warnings: Iterable[str] = (),
        source_path: str | None = None,
    ) -> None:
        if switches is not None and switches.segments is not segments:
            raise ValueError("As chaves devem pertencer aos trechos do catálogo.")
        definition_values = tuple(definitions)
        membership_values = tuple(memberships)
        if not definition_values:
            raise ValueError("O catálogo deve conter ao menos um circuito.")
        if len(definition_values) != len(membership_values):
            raise ValueError("Cada circuito deve possuir uma associação.")
        by_id: dict[str, int] = {}
        for index, definition in enumerate(definition_values):
            if not definition.circuit_id:
                raise ValueError("CIRC_ID não pode ser vazio.")
            if definition.circuit_id in by_id:
                raise ValueError(f"CIRC_ID duplicado: {definition.circuit_id}")
            if segments.bars.index_for_id(definition.root_bar_id) is None:
                raise ValueError(
                    f"Barra inicial inexistente: {definition.root_bar_id}"
                )
            by_id[definition.circuit_id] = index
        self.segments = segments
        self.switches = switches
        self._definitions = definition_values
        self._memberships = membership_values
        self._by_id = by_id
        owner_counts = np.zeros(len(segments), dtype=np.intp)
        for membership in membership_values:
            if (
                (membership.segment_indices < 0).any()
                or (membership.segment_indices >= len(segments)).any()
            ):
                raise ValueError("Uma associação referencia trecho inexistente.")
            owner_counts[membership.segment_indices] += 1
        owner_offsets = np.empty(len(segments) + 1, dtype=np.intp)
        owner_offsets[0] = 0
        np.cumsum(owner_counts, out=owner_offsets[1:])
        owner_indices = np.empty(int(owner_offsets[-1]), dtype=np.intp)
        cursor = owner_offsets[:-1].copy()
        for circuit_index, membership in enumerate(membership_values):
            segment_values = membership.segment_indices
            positions = cursor[segment_values]
            owner_indices[positions] = circuit_index
            cursor[segment_values] += 1
        overlapping = np.flatnonzero(owner_counts > 1).astype(np.intp, copy=False)
        for values in (owner_counts, owner_offsets, owner_indices, overlapping):
            values.setflags(write=False)
        self._segment_owner_counts = owner_counts
        self._segment_circuit_offsets = owner_offsets
        self._segment_circuit_indices = owner_indices
        self._overlapping_segment_indices = overlapping
        self.topology_warnings = tuple(topology_warnings)
        self.source_path = source_path

    @classmethod
    def build(
        cls,
        segments: LineNetworkModel,
        switches: SwitchModel | None,
        definitions: Iterable[CircuitDefinition],
        *,
        source_path: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        operational: bool = False,
    ) -> "CircuitCatalogModel":
        definition_values = tuple(definitions)
        direct_switches, warnings = switch_circuit_assignments(
            switches,
            {definition.circuit_id for definition in definition_values},
        )
        if operational:
            warnings = tuple(warning for warning in warnings if "CIRC_ID" not in warning)

        topology = NetworkTopology(segments, switches)
        memberships: list[CircuitMembership] = []
        for definition in definition_values:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("Construção da topologia cancelada.")
            root_index = segments.bars.index_for_id(definition.root_bar_id)
            if root_index is None:
                raise ValueError(
                    f"Barra inicial inexistente: {definition.root_bar_id}"
                )
            memberships.append(
                topology.trace(
                    definition.circuit_id,
                    root_index,
                    direct_switches[definition.circuit_id],
                    cancel_check=cancel_check,
                    respect_circuit_owner=not operational,
                )
            )
        return cls(
            segments,
            switches,
            definition_values,
            memberships,
            topology_warnings=warnings,
            source_path=source_path,
        )

    def __len__(self) -> int:
        return len(self._definitions)

    @property
    def definitions(self) -> tuple[CircuitDefinition, ...]:
        return self._definitions

    @property
    def memberships(self) -> tuple[CircuitMembership, ...]:
        return self._memberships

    def index_for_id(self, circuit_id: str) -> int | None:
        return self._by_id.get(circuit_id)

    def definition(self, index: int) -> CircuitDefinition:
        if not 0 <= int(index) < len(self):
            raise IndexError(index)
        return self._definitions[int(index)]

    def membership(self, index: int) -> CircuitMembership:
        if not 0 <= int(index) < len(self):
            raise IndexError(index)
        return self._memberships[int(index)]

    @property
    def overlapping_segment_indices(self) -> IndexArray:
        return self._overlapping_segment_indices

    @property
    def segment_owner_counts(self) -> IndexArray:
        return self._segment_owner_counts

    def circuit_indices_for_segment(self, segment_index: int) -> IndexArray:
        if not 0 <= int(segment_index) < len(self.segments):
            raise IndexError(segment_index)
        index = int(segment_index)
        start = int(self._segment_circuit_offsets[index])
        stop = int(self._segment_circuit_offsets[index + 1])
        return self._segment_circuit_indices[start:stop]


class CircuitVisibilityController:
    """Estado visual mutável, separado das associações elétricas imutáveis."""

    __slots__ = (
        "catalog",
        "_checked",
        "_colors",
        "_bar_owner_counts",
        "_bar_visible_counts",
        "_segment_owner_counts",
        "_segment_visible_counts",
        "_bar_mask",
        "_segment_mask",
        "_bar_mask_view",
        "_segment_mask_view",
        "_segment_style_indices",
        "_segment_style_view",
    )

    def __init__(
        self,
        catalog: CircuitCatalogModel,
        checked: Sequence[bool] | None = None,
        colors: Sequence[str] | None = None,
    ) -> None:
        self.catalog = catalog
        if checked is None:
            checked_values = np.ones(len(catalog), dtype=np.bool_)
        else:
            checked_values = np.asarray(checked, dtype=np.bool_)
            if checked_values.ndim != 1 or checked_values.size != len(catalog):
                raise ValueError("O estado visual deve corresponder aos circuitos.")
            checked_values = checked_values.copy()
        self._checked = checked_values
        if colors is None:
            self._colors = list(generate_circuit_palette(len(catalog)))
        else:
            if len(colors) != len(catalog):
                raise ValueError("As cores devem corresponder aos circuitos.")
            self._colors = [normalize_hex_color(value) for value in colors]
        self._bar_owner_counts = np.zeros(len(catalog.segments.bars), dtype=np.int32)
        self._segment_owner_counts = catalog.segment_owner_counts.astype(
            np.int32, copy=True
        )
        for membership in catalog.memberships:
            self._bar_owner_counts[membership.bar_indices] += 1
        self._bar_visible_counts = np.zeros_like(self._bar_owner_counts)
        self._segment_visible_counts = np.zeros_like(self._segment_owner_counts)
        for index, membership in enumerate(catalog.memberships):
            if self._checked[index]:
                self._bar_visible_counts[membership.bar_indices] += 1
                self._segment_visible_counts[membership.segment_indices] += 1
        self._bar_mask = (self._bar_owner_counts == 0) | (
            self._bar_visible_counts > 0
        )
        self._segment_mask = (self._segment_owner_counts == 0) | (
            self._segment_visible_counts > 0
        )
        self._bar_mask_view = self._bar_mask.view()
        self._segment_mask_view = self._segment_mask.view()
        self._bar_mask_view.setflags(write=False)
        self._segment_mask_view.setflags(write=False)
        self._segment_style_indices = np.full(len(catalog.segments), -1, dtype=np.intp)
        for segment_index in range(len(catalog.segments)):
            owners = catalog.circuit_indices_for_segment(segment_index)
            if owners.size:
                self._segment_style_indices[segment_index] = self._first_visible_owner(
                    owners
                )
        self._segment_style_view = self._segment_style_indices.view()
        self._segment_style_view.setflags(write=False)

    @property
    def bar_visible_mask(self) -> BoolArray:
        return self._bar_mask_view

    @property
    def segment_visible_mask(self) -> BoolArray:
        return self._segment_mask_view

    @property
    def checked_states(self) -> tuple[bool, ...]:
        return tuple(bool(value) for value in self._checked)

    @property
    def colors(self) -> tuple[str, ...]:
        return tuple(self._colors)

    @property
    def segment_style_indices(self) -> IndexArray:
        """Circuito efetivo por trecho; -1 é padrão e -2 significa oculto."""

        return self._segment_style_view

    def color(self, index: int) -> str:
        if not 0 <= int(index) < len(self.catalog):
            raise IndexError(index)
        return self._colors[int(index)]

    def set_color(self, index: int, color: str) -> bool:
        if not 0 <= int(index) < len(self.catalog):
            raise IndexError(index)
        circuit_index = int(index)
        normalized = normalize_hex_color(color)
        if self._colors[circuit_index] == normalized:
            return False
        self._colors[circuit_index] = normalized
        return True

    def _first_visible_owner(self, owners: IndexArray) -> int:
        for owner in owners:
            circuit_index = int(owner)
            if self._checked[circuit_index]:
                return circuit_index
        return -2

    def is_visible(self, index: int) -> bool:
        if not 0 <= int(index) < len(self.catalog):
            raise IndexError(index)
        return bool(self._checked[int(index)])

    def set_visible(self, index: int, visible: bool) -> bool:
        if not 0 <= int(index) < len(self.catalog):
            raise IndexError(index)
        circuit_index = int(index)
        visible = bool(visible)
        if bool(self._checked[circuit_index]) == visible:
            return False
        self._checked[circuit_index] = visible
        delta = 1 if visible else -1
        membership = self.catalog.membership(circuit_index)
        bars = membership.bar_indices
        segments = membership.segment_indices
        self._bar_visible_counts[bars] += delta
        self._segment_visible_counts[segments] += delta
        self._bar_mask[bars] = (self._bar_owner_counts[bars] == 0) | (
            self._bar_visible_counts[bars] > 0
        )
        self._segment_mask[segments] = (
            self._segment_owner_counts[segments] == 0
        ) | (self._segment_visible_counts[segments] > 0)
        for segment_index in segments:
            index_value = int(segment_index)
            self._segment_style_indices[index_value] = self._first_visible_owner(
                self.catalog.circuit_indices_for_segment(index_value)
            )
        return True
