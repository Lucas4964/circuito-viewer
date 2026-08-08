"""Diálogo de importação de um banco Access inteiro.

Reúne num passo só o que hoje exige dez importações: o arquivo, a senha
opcional, as tabelas detectadas — com ajuste manual quando a detecção não
serve — e os metadados UTM que o banco não tem.

A senha nunca sai daqui para disco nem para log: o campo é mascarado, o valor
vive na memória do diálogo e o ``__repr__`` da seleção o omite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .csv_import import COORDINATE_UNITS
from .mdb_mapping import (
    ENTITY_LABELS,
    ENTITY_ORDER,
    GENERATOR_CONSUMER_ENTITY,
    ResolvedMapping,
)
from .model import UtmCrs


# Item do combo de tabelas que devolve o controle à detecção automática.
AUTOMATIC_LABEL = "Detectar automaticamente"


@dataclass(frozen=True, slots=True)
class MdbImportSelection:
    """O que o usuário escolheu no diálogo."""

    crs: UtmCrs
    scale: float
    entities: tuple[str, ...]
    overrides: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - conveniência de depuração
        return (
            f"MdbImportSelection(crs={self.crs!r}, scale={self.scale!r}, "
            f"entities={self.entities!r})"
        )


class MdbPasswordDialog(QDialog):
    """Pede a senha de um banco protegido.

    Existe separado do diálogo principal porque a senha só é conhecida como
    necessária **depois** da primeira tentativa de conexão: o driver responde
    ``-1905`` e a aplicação repergunta, em vez de repassar o erro ODBC cru.
    """

    def __init__(
        self,
        file_name: str,
        parent=None,  # noqa: ANN001
        *,
        retry: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Banco protegido")
        self.setModal(True)

        layout = QVBoxLayout(self)
        message = QLabel(
            f"A senha informada para {file_name} não confere."
            if retry
            else f"O banco {file_name} é protegido por senha."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        form = QFormLayout()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setToolTip(
            "A senha é usada apenas para abrir o banco e não é gravada."
        )
        form.addRow("Senha:", self.password_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def password(self) -> str:
        return self.password_input.text()


class MdbImportDialog(QDialog):
    """Confirma as tabelas detectadas e coleta os metadados UTM."""

    def __init__(
        self,
        path: str,
        mapping: ResolvedMapping,
        table_names: tuple[str, ...],
        parent=None,  # noqa: ANN001
        *,
        suggested_scale: float = 1.0,
        row_counts: dict[str, int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Importar banco de dados")
        self.setModal(True)
        self._mapping = mapping
        self._table_names = tuple(table_names)
        self._row_counts = dict(row_counts or {})

        layout = QVBoxLayout(self)

        file_label = QLabel(Path(path).name)
        file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        file_form = QFormLayout()
        file_form.addRow("Arquivo:", file_label)
        layout.addLayout(file_form)

        layout.addWidget(self._build_entities_group())
        layout.addWidget(self._build_coordinates_group(suggested_scale))

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._sync_ok_enabled()

    # ------------------------------------------------------------------ #
    # Construção
    # ------------------------------------------------------------------ #

    def _build_entities_group(self) -> QWidget:
        group = QGroupBox("Tabelas")
        form = QFormLayout(group)
        self.entity_checks: dict[str, QCheckBox] = {}
        self.entity_combos: dict[str, QComboBox] = {}

        def create_combo(entity: str) -> QComboBox:
            resolved = self._mapping.get(entity)
            combo = QComboBox()
            combo.addItem(AUTOMATIC_LABEL, None)
            for name in self._table_names:
                count = self._row_counts.get(name)
                label = name if count is None else f"{name}  ({count:n})"
                combo.addItem(label, name)
            if resolved is not None:
                position = combo.findData(resolved.table)
                if position >= 0:
                    combo.setCurrentIndex(position)
            else:
                combo.setToolTip(
                    self._mapping.reason_for(entity) or "Não encontrada."
                )
            self.entity_combos[entity] = combo
            return combo

        for entity in ENTITY_ORDER:
            resolved = self._mapping.get(entity)
            consumer_resolved = (
                self._mapping.get(GENERATOR_CONSUMER_ENTITY)
                if entity == "geradores"
                else None
            )
            fully_resolved = resolved is not None and (
                entity != "geradores" or consumer_resolved is not None
            )
            check = QCheckBox(ENTITY_LABELS[entity])
            check.setChecked(fully_resolved)
            check.toggled.connect(self._sync_ok_enabled)

            combo = create_combo(entity)
            if fully_resolved:
                tooltip = f"Tabela detectada: {resolved.table}"
                if entity == "geradores":
                    tooltip += f"; MT_CONS: {consumer_resolved.table}"
                check.setToolTip(tooltip)
            else:
                reasons = [self._mapping.reason_for(entity)]
                if entity == "geradores":
                    reasons.append(
                        self._mapping.reason_for(GENERATOR_CONSUMER_ENTITY)
                    )
                reason = "; ".join(item for item in reasons if item) or "Não encontrada."
                check.setEnabled(False)
                check.setToolTip(reason)
                combo.setToolTip(reason)
            combo.currentIndexChanged.connect(
                lambda _index, name=entity: self._on_table_changed(name)
            )

            self.entity_checks[entity] = check
            if entity != "geradores":
                form.addRow(check, combo)
                continue

            consumer_combo = create_combo(GENERATOR_CONSUMER_ENTITY)
            consumer_combo.currentIndexChanged.connect(
                lambda _index: self._on_table_changed("geradores")
            )
            compound = QWidget(group)
            compound_form = QFormLayout(compound)
            compound_form.setContentsMargins(0, 0, 0, 0)
            compound_form.addRow("MT_GERADOR_CONS:", combo)
            compound_form.addRow("MT_CONS:", consumer_combo)
            form.addRow(check, compound)

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        form.addRow(self.warning_label)
        return group

    def _build_coordinates_group(self, suggested_scale: float) -> QWidget:
        group = QGroupBox("Coordenadas")
        form = QFormLayout(group)

        self.zone_input = QSpinBox()
        self.zone_input.setRange(1, 60)
        self.zone_input.setValue(21)
        self.zone_input.setToolTip("Zona longitudinal do sistema UTM")
        form.addRow("Zona UTM:", self.zone_input)

        self.hemisphere_input = QComboBox()
        self.hemisphere_input.addItem("Sul", False)
        self.hemisphere_input.addItem("Norte", True)
        form.addRow("Hemisfério:", self.hemisphere_input)

        self.unit_input = QComboBox()
        for factor, label in COORDINATE_UNITS:
            self.unit_input.addItem(label, factor)
        self.unit_input.setToolTip(
            "Divisor que converte X e Y do banco para metros. O modelo trabalha "
            "em metros, como o COMPR dos trechos."
        )
        position = self.unit_input.findData(suggested_scale)
        if position >= 0:
            self.unit_input.setCurrentIndex(position)
        form.addRow("Unidade das coordenadas:", self.unit_input)
        return group

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #

    def _on_table_changed(self, entity: str) -> None:
        combo = self.entity_combos[entity]
        check = self.entity_checks[entity]

        def source_available(source: str) -> bool:
            source_combo = self.entity_combos[source]
            return (
                source_combo.currentData() is not None
                or self._mapping.get(source) is not None
            )

        selected = source_available(entity)
        if entity == "geradores":
            selected = selected and source_available(GENERATOR_CONSUMER_ENTITY)
        if selected:
            # Escolher a tabela à mão é a forma de reabilitar uma entidade que a
            # detecção não encontrou.
            check.setEnabled(True)
            check.setChecked(True)
        else:
            check.setChecked(False)
            check.setEnabled(False)
        self._sync_ok_enabled()

    def _sync_ok_enabled(self, *_args: object) -> None:
        selected = self.selected_entities()
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        has_bars = "barras" in selected
        if ok is not None:
            ok.setEnabled(has_bars)
        if has_bars:
            missing = [
                ENTITY_LABELS[entity]
                for entity in ENTITY_ORDER
                if entity not in selected
            ]
            self.warning_label.setText(
                "Não serão importadas: " + ", ".join(missing) if missing else ""
            )
        else:
            self.warning_label.setText(
                "As barras são obrigatórias: sem elas não há o que desenhar."
            )

    # ------------------------------------------------------------------ #
    # Resultado
    # ------------------------------------------------------------------ #

    def selected_entities(self) -> tuple[str, ...]:
        return tuple(
            entity
            for entity in ENTITY_ORDER
            if self.entity_checks[entity].isChecked()
        )

    def overrides(self) -> dict[str, str]:
        """Tabelas escolhidas à mão, apenas onde diferem da detecção."""

        chosen: dict[str, str] = {}
        for entity in self.selected_entities():
            sources = (
                (entity, GENERATOR_CONSUMER_ENTITY)
                if entity == "geradores"
                else (entity,)
            )
            for source in sources:
                table = self.entity_combos[source].currentData()
                if table is None:
                    continue
                resolved = self._mapping.get(source)
                if resolved is None or resolved.table != table:
                    chosen[source] = table
        return chosen

    def coordinate_scale(self) -> float:
        return float(self.unit_input.currentData())

    def crs(self) -> UtmCrs:
        return UtmCrs(
            zone=self.zone_input.value(),
            northern=bool(self.hemisphere_input.currentData()),
        )

    def selection(self) -> MdbImportSelection:
        return MdbImportSelection(
            crs=self.crs(),
            scale=self.coordinate_scale(),
            entities=self.selected_entities(),
            overrides=self.overrides(),
        )
