"""Edições de regulador que valem só enquanto a sessão dura.

O MDB é um retrato **somente leitura**: nada aqui volta para o banco. O que
existe é uma camada de sobreposição sobre o modelo importado, para o usuário
completar um cadastro incompleto — tipicamente ``VNOM``/``SNOM`` zeradas, que
impedem a exportação do regulador — sem editar o arquivo de origem.

A regra que rege o desenho é a do usuário: *o banco permanece inalterado, as
edições existem apenas durante a sessão atual e o OpenDSS deve sempre trabalhar
com a versão corrente apresentada na interface*. Por isso não há store, não há
JSON e não há caminho de gravação — reabrir o circuito ou o aplicativo restaura
o valor original por construção, e não por uma rotina de limpeza que alguém
possa esquecer de chamar.

A sobreposição é aplicada **reconstruindo** o :class:`RegulatorModel`, em vez de
consultada em cada ponto de leitura. Assim continua existindo um único modelo em
memória, e todo consumidor já existente — painel, exportações, fluxo de potência,
desenho no mapa — enxerga o valor editado sem saber que esta camada existe.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .model import RegulatorModel


# Só campos que a exportação OpenDSS consome e que fazem sentido mudar num
# estudo: a tensão, a potência, o VREG de cada patamar e o tap fixo (FIXO e TAP,
# para simular um regulador travado). FAIXA e NPASSOS também entram no modelo,
# mas descrevem o equipamento, e continuam só de leitura.
EDITABLE_FIELDS: tuple[str, ...] = (
    "vnom",
    "snom",
    "vreg_p1",
    "vreg_p2",
    "vreg_p3",
    "vreg_p4",
    "fixed",
    "tap",
)

# Nome do campo -> rótulo da coluna no MDB, para as mensagens da interface.
FIELD_LABELS: Mapping[str, str] = {
    "vnom": "VNOM",
    "snom": "SNOM",
    "vreg_p1": "VREG_P1",
    "vreg_p2": "VREG_P2",
    "vreg_p3": "VREG_P3",
    "vreg_p4": "VREG_P4",
    "fixed": "FIXO",
    "tap": "TAP",
}


class RegulatorOverrides:
    """Valores digitados pelo usuário, por ``REGU_ID`` e campo."""

    __slots__ = ("_values",)

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}

    def __len__(self) -> int:
        return sum(len(fields) for fields in self._values.values())

    @property
    def is_empty(self) -> bool:
        return not self._values

    def fields_for(self, regulator_id: str) -> Mapping[str, str]:
        """Campos sobrepostos de um regulador; vazio quando não há edição."""

        return dict(self._values.get(str(regulator_id), {}))

    def value_for(self, regulator_id: str, field: str) -> str | None:
        return self._values.get(str(regulator_id), {}).get(_checked_field(field))

    def set(self, regulator_id: str, field: str, value: str) -> bool:
        """Registra um valor. Devolve ``True`` se algo mudou de fato."""

        key = str(regulator_id)
        if not key:
            raise ValueError("O REGU_ID do regulador editado não pode ser vazio.")
        name = _checked_field(field)
        text = str(value).strip()
        current = self._values.get(key, {}).get(name)
        if current == text:
            return False
        self._values.setdefault(key, {})[name] = text
        return True

    def clear(self, regulator_id: str, field: str | None = None) -> bool:
        """Descarta a edição de um campo, ou de um regulador inteiro."""

        key = str(regulator_id)
        fields = self._values.get(key)
        if fields is None:
            return False
        if field is None:
            del self._values[key]
            return True
        name = _checked_field(field)
        if name not in fields:
            return False
        del fields[name]
        if not fields:
            del self._values[key]
        return True

    def clear_all(self) -> bool:
        if not self._values:
            return False
        self._values.clear()
        return True

    def retain(self, regulator_ids: Iterable[str]) -> bool:
        """Descarta edições de reguladores ausentes do modelo informado."""

        keep = {str(value) for value in regulator_ids}
        stale = [key for key in self._values if key not in keep]
        for key in stale:
            del self._values[key]
        return bool(stale)


def _checked_field(field: str) -> str:
    name = str(field).strip().casefold()
    if name not in EDITABLE_FIELDS:
        raise ValueError(f"Campo de regulador não editável: {field!r}")
    return name


def apply_overrides(
    model: RegulatorModel | None,
    overrides: RegulatorOverrides,
) -> RegulatorModel | None:
    """Devolve o modelo efetivo: o importado, com as edições da sessão.

    Devolve **o próprio** ``model`` quando nada há a sobrepor. A identidade
    importa: ``MainWindow._set_regulator_model()`` invalida o fluxo de potência
    ao ver um objeto diferente, e reconstruir à toa descartaria um resultado
    ainda válido a cada seleção de trecho.
    """

    if model is None or overrides.is_empty:
        return model

    # Cada campo editável é a coluna ``<campo>_values`` do modelo.
    columns = {
        field: list(getattr(model, f"{field}_values")) for field in EDITABLE_FIELDS
    }
    changed = False
    for index, regulator_id in enumerate(model.regulator_ids):
        for field, value in overrides.fields_for(regulator_id).items():
            column = columns[field]
            if column[index] != value:
                column[index] = value
                changed = True
    if not changed:
        return model

    return RegulatorModel(
        model.segments,
        model.regulator_ids,
        model.segment_indices,
        model.external_ids,
        model.codes,
        model.connections,
        columns["snom"],
        model.regulation_ranges,
        model.step_counts,
        columns["tap"],
        model.inom_values,
        columns["vnom"],
        vreg_p1_values=columns["vreg_p1"],
        vreg_p2_values=columns["vreg_p2"],
        vreg_p3_values=columns["vreg_p3"],
        vreg_p4_values=columns["vreg_p4"],
        fixed_values=columns["fixed"],
        source_path=model.source_path,
    )
