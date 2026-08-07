"""Curvas horárias de 24 pontos cadastradas pelo usuário.

Camada de núcleo: não importa Qt nem toca o disco. A gravação mora em
``curvas_store``, a tabela em ``curvas_table`` e a janela em ``curvas_window``.
Manter este módulo livre das duas dependências é o que permite testar todas as
regras — validação e interpretação do bloco colado do Excel — sem
``QApplication`` e sem ``tmp_path``, que é justamente onde mora o risco.

**Por que ``curve_id`` existe além do nome.** As curvas serão associadas a
cargas e geradores. Se a associação fosse pelo nome, renomear uma curva
quebraria o vínculo em silêncio, e trocar os nomes de duas curvas entre si
trocaria as associações sem nenhum aviso. O ``curve_id`` é um identificador
opaco gerado uma única vez, que a renomeação não toca; o nome fica sendo apenas
o rótulo da interface.

**Por que existem :class:`Curve` e :class:`CurveDraft`.** Uma curva em edição
pode estar incompleta — o usuário preenche as 24 horas aos poucos —, mas uma
curva gravada nunca pode. Separar os dois tipos faz o compilador de invariantes
trabalhar a nosso favor: tudo que é :class:`Curve` já passou pela validação, e
o momento da conversão (``to_curve``) é exatamente o momento de salvar.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .opendss_export import parse_number


# 24 pontos: uma curva horária, uma hora por ponto. **Não** é o
# ``LOAD_PATTERN_COUNT`` (=4) de ``opendss_export``: aquele descreve os patamares
# NPAT importados do CSV/MDB, é importado por ``opendss_powerflow`` e percorrido
# em laço na exportação. São conceitos distintos que por acaso vivem lado a lado;
# unificá-los quebraria a exportação inteira.
HOURLY_CURVE_POINT_COUNT = 24

# Limite de nome escolhido pela interface, não pelo OpenDSS: cabe na lista da
# janela sem elipse e ainda descreve a curva. O saneamento para a exportação
# (acentos, pontos) é problema de ``sanitize_dss_name``, no momento de exportar.
MAX_CURVE_NAME_LENGTH = 60

# Arredondamento **só de exibição**, como em ``load_pattern_table``: o valor
# guardado continua com a precisão que o usuário digitou.
CURVE_DISPLAY_DECIMALS = 4


def _clean_name(name: str) -> str:
    """Normaliza o nome para comparação e gravação."""

    return " ".join(str(name).split())


def _is_finite_number(value: object) -> bool:
    return isinstance(value, float) and math.isfinite(value)


def new_curve_id() -> str:
    """Gera um identificador estável para uma curva nova."""

    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class Curve:
    """Curva horária completa: 24 valores finitos e um nome do usuário.

    Este é o tipo que vai para o disco e o que um consumidor futuro (associação
    a cargas e geradores) recebe. Por construção não existe :class:`Curve`
    incompleta: quem está no meio da edição usa :class:`CurveDraft`.
    """

    curve_id: str
    name: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not str(self.curve_id).strip():
            raise ValueError("A curva precisa de um identificador.")
        if not _clean_name(self.name):
            raise ValueError("A curva precisa de um nome.")
        if len(self.values) != HOURLY_CURVE_POINT_COUNT:
            raise ValueError(
                f"A curva precisa de {HOURLY_CURVE_POINT_COUNT} valores."
            )
        for hour, value in enumerate(self.values, start=1):
            # ``nan`` e ``inf`` passam por ``isinstance(float)`` e sobrevivem ao
            # JSON de algumas bibliotecas; barrá-los aqui é o que impede um
            # gráfico sem escala e uma exportação sem sentido lá na frente.
            if not _is_finite_number(value):
                raise ValueError(
                    f"O valor da hora {hour} não é um número finito."
                )


@dataclass(slots=True)
class CurveDraft:
    """Curva em edição, na qual horas ainda não preenchidas são permitidas.

    Uma hora vazia é ``None`` — nunca ``0.0``, que é um valor legítimo que o
    usuário pode querer, e nunca ``""``, porque o rascunho guarda números já
    interpretados e não texto a reinterpretar a cada repintura do gráfico.
    """

    curve_id: str
    name: str
    values: list[float | None] = field(
        default_factory=lambda: [None] * HOURLY_CURVE_POINT_COUNT
    )

    def __post_init__(self) -> None:
        if len(self.values) != HOURLY_CURVE_POINT_COUNT:
            raise ValueError(
                f"O rascunho precisa de {HOURLY_CURVE_POINT_COUNT} posições."
            )

    @classmethod
    def new(cls, name: str = "") -> CurveDraft:
        """Rascunho vazio, com identificador novo."""

        return cls(new_curve_id(), _clean_name(name))

    @classmethod
    def from_curve(cls, curve: Curve) -> CurveDraft:
        """Rascunho editável a partir de uma curva gravada."""

        return cls(curve.curve_id, curve.name, list(curve.values))

    def set_value(self, hour_index: int, value: float | None) -> bool:
        """Grava (ou apaga, com ``None``) o valor de uma hora 0-based.

        Devolve ``False`` quando nada mudou, para o modelo Qt poder evitar um
        ``dataChanged`` inútil — a mesma disciplina de
        ``CircuitVisibilityController.set_visible``.
        """

        if not 0 <= hour_index < HOURLY_CURVE_POINT_COUNT:
            raise IndexError("Hora fora da faixa da curva.")
        if value is not None and not _is_finite_number(value):
            raise ValueError("O valor precisa ser um número finito.")
        if self.values[hour_index] == value:
            return False
        self.values[hour_index] = value
        return True

    def missing_hours(self) -> tuple[int, ...]:
        """Horas ainda vazias, em base 1 — a numeração que a tabela mostra."""

        return tuple(
            hour
            for hour, value in enumerate(self.values, start=1)
            if value is None
        )

    def is_complete(self) -> bool:
        return not self.missing_hours()

    def to_curve(self) -> Curve:
        """Converte em :class:`Curve`; levanta ``ValueError`` se incompleta."""

        missing = self.missing_hours()
        if missing:
            raise ValueError(
                f"Faltam valores nas horas {_join_hours(missing)}."
            )
        return Curve(self.curve_id, _clean_name(self.name), tuple(self.values))


def _join_hours(hours: Sequence[int]) -> str:
    """Lista horas em português: "3", "3 e 7", "3, 7 e 19"."""

    texts = [str(hour) for hour in hours]
    if len(texts) <= 1:
        return "".join(texts)
    return f"{', '.join(texts[:-1])} e {texts[-1]}"


class CurveCatalog:
    """Coleção mutável de curvas em edição.

    Guarda :class:`CurveDraft`, e não :class:`Curve`, de propósito: existe um
    único contêiner em memória durante toda a sessão de edição, e "Salvar" é o
    momento em que cada rascunho é convertido — falhando se algum não converter.
    O modelo Qt apenas consulta este catálogo, nunca guarda estado próprio, do
    mesmo jeito que ``CircuitTableModel`` delega ao seu controlador.
    """

    __slots__ = ("_drafts",)

    def __init__(self, drafts: Iterable[CurveDraft] = ()) -> None:
        self._drafts: list[CurveDraft] = list(drafts)

    @classmethod
    def from_curves(cls, curves: Iterable[Curve]) -> CurveCatalog:
        return cls(CurveDraft.from_curve(curve) for curve in curves)

    def __len__(self) -> int:
        return len(self._drafts)

    @property
    def drafts(self) -> tuple[CurveDraft, ...]:
        return tuple(self._drafts)

    def draft(self, index: int) -> CurveDraft:
        return self._drafts[index]

    def index_for_id(self, curve_id: str) -> int | None:
        """Posição da curva com este identificador, ou ``None``.

        É por aqui que uma futura associação carga→curva resolve o vínculo.
        """

        for index, draft in enumerate(self._drafts):
            if draft.curve_id == curve_id:
                return index
        return None

    def name_available(
        self,
        name: str,
        *,
        ignoring: str | None = None,
    ) -> bool:
        """Diz se o nome está livre, ignorando a curva de ``ignoring``.

        ``ignoring`` recebe o ``curve_id`` da própria curva sendo renomeada;
        sem ele, toda curva colidiria consigo mesma.
        """

        wanted = _clean_name(name).casefold()
        return not any(
            draft.curve_id != ignoring
            and _clean_name(draft.name).casefold() == wanted
            for draft in self._drafts
        )

    def add(self, draft: CurveDraft) -> int:
        """Acrescenta um rascunho ao fim e devolve o índice em que ficou."""

        self._drafts.append(draft)
        return len(self._drafts) - 1

    def remove(self, index: int) -> CurveDraft:
        return self._drafts.pop(index)

    def rename(self, index: int, name: str) -> bool:
        """Renomeia; devolve ``False`` quando o nome já era esse."""

        cleaned = _clean_name(name)
        if self._drafts[index].name == cleaned:
            return False
        self._drafts[index].name = cleaned
        return True

    def to_curves(self) -> tuple[Curve, ...]:
        """Converte tudo; levanta ``ValueError`` no primeiro rascunho inválido."""

        return tuple(draft.to_curve() for draft in self._drafts)


def validate_curve_name(
    name: str,
    catalog: CurveCatalog,
    *,
    ignoring: str | None = None,
) -> str | None:
    """Devolve o problema do nome em português, ou ``None`` se estiver válido.

    Não há restrição de caracteres: acentos, espaços e pontuação são bem-vindos
    num rótulo de interface. O saneamento exigido pelo OpenDSS é aplicado por
    ``sanitize_dss_name`` no momento da exportação, e antecipá-lo aqui obrigaria
    o usuário a conhecer uma regra de outra ferramenta para cadastrar uma curva.
    """

    cleaned = _clean_name(name)
    if not cleaned:
        return "Informe um nome para a curva."
    if len(cleaned) > MAX_CURVE_NAME_LENGTH:
        return (
            f"O nome deve ter no máximo {MAX_CURVE_NAME_LENGTH} caracteres."
        )
    if not catalog.name_available(cleaned, ignoring=ignoring):
        return f'Já existe uma curva chamada "{cleaned}".'
    return None


def validate_draft(draft: CurveDraft, catalog: CurveCatalog) -> str | None:
    """Devolve o primeiro problema do rascunho, ou ``None`` se puder salvar."""

    problem = validate_curve_name(
        draft.name,
        catalog,
        ignoring=draft.curve_id,
    )
    if problem is not None:
        return problem
    missing = draft.missing_hours()
    if missing:
        return f"Faltam valores nas horas {_join_hours(missing)}."
    return None


def validate_catalog(catalog: CurveCatalog) -> tuple[str, ...]:
    """Problemas de todas as curvas; vazio significa que dá para salvar."""

    problems: list[str] = []
    for draft in catalog.drafts:
        problem = validate_draft(draft, catalog)
        if problem is not None:
            label = _clean_name(draft.name) or "(sem nome)"
            problems.append(f'Curva "{label}": {problem}')
    return tuple(problems)


def split_clipboard_block(text: str) -> list[list[str]]:
    """Quebra o texto da área de transferência em linhas e colunas.

    Normaliza ``\\r\\n`` e ``\\r`` solitário para ``\\n``, quebra por linha e cada
    linha por tabulação — o formato que o Excel coloca na área de transferência.

    Linhas vazias **do final** são descartadas, porque o Excel sempre acrescenta
    uma ao copiar um intervalo. Uma linha vazia **no meio** é preservada: ali ela
    é uma célula que o usuário deixou em branco de propósito, e descartá-la
    deslocaria em uma hora todos os valores seguintes — um erro silencioso e
    difícil de perceber num bloco de 24 números.
    """

    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    rows = [line.split("\t") for line in normalized.split("\n")]
    while rows and all(not cell.strip() for cell in rows[-1]):
        rows.pop()
    return rows


def clipboard_column(block: Sequence[Sequence[str]]) -> tuple[list[str], int]:
    """Escolhe a coluna de valores do bloco colado.

    Devolve os textos da coluna escolhida e a largura do bloco, para a janela
    poder avisar quando a escolha foi ambígua.

    Com uma coluna só, não há dúvida. Com duas ou mais, usa a **última**: quem
    copia um par do Excel copia "Hora, Valor" nessa ordem, e o valor é a coluna
    da direita. Escolher "a coluna sob o cursor" degeneraria aqui, porque a
    tabela tem só duas colunas e "Hora" nem é editável — a colagem sempre
    alimenta "Valor". A regra pode errar, e por isso a janela informa qual
    coluna usou em vez de decidir em silêncio.
    """

    width = max((len(row) for row in block), default=0)
    if width <= 1:
        return [row[0] if row else "" for row in block], width
    return [row[-1] if len(row) == width else "" for row in block], width


def parse_clipboard_values(texts: Iterable[str]) -> list[tuple[float | None, bool]]:
    """Interpreta os textos colados como valores de curva.

    Cada item volta como ``(valor, reconhecido)``: um texto vazio vira
    ``(None, True)`` — apagar a célula é uma intenção legítima —, e um texto que
    não é número vira ``(None, False)``, para quem chamou poder contar quantos
    foram ignorados sem abortar a colagem inteira. Abortar seria pior: um
    cabeçalho copiado junto com os 24 valores invalidaria tudo.

    A conversão é ``parse_number``, a regra única de separador decimal do
    projeto — a mesma que aceita vírgula ou ponto e recusa os dois juntos.
    """

    parsed: list[tuple[float | None, bool]] = []
    for text in texts:
        stripped = str(text).strip()
        if not stripped:
            parsed.append((None, True))
            continue
        number = parse_number(stripped)
        parsed.append((number, number is not None))
    return parsed
