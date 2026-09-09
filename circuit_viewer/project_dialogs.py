"""Revisão explícita das propostas do projeto; nenhuma mutação ocorre aqui."""
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
)
from .network_registry_dialog import ENTITY_NAMES


FIELD_NAMES = {
    "codes": "Código", "code": "Código", "states": "Estado atual", "normal_states": "Estado normal",
    "vnom_values": "Tensão nominal (kV)", "snom_values": "Potência nominal (kVA)",
    "nominal_voltage": "Tensão nominal (kV)", "root_bar_id": "Barra inicial",
    "start_indices": "Barra inicial", "end_indices": "Barra final", "bar_indices": "Barra",
    "segment_indices": "Trecho", "load_indices": "Carga", "phases": "Fases",
    "phase_cable_ids": "Cabo de fase", "neutral_cable_ids": "Cabo de neutro",
    "x": "Coordenada X (m)", "y": "Coordenada Y (m)", "lengths": "Comprimento (m)",
    "circuit_ids": "Alimentador nominal", "schedule": "Patamares", "records": "Patamares de carga",
}


def field_label(name):
    return FIELD_NAMES.get(name, name.removesuffix("_values").replace("_", " ").upper())


def equipment_label(key):
    return f"{ENTITY_NAMES.get(key.entity, key.entity)}: {key.native_id}"


class ImportProposalDialog(QDialog):
    def __init__(self, proposal, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Revisar importação no projeto")
        self.resize(1000, 520)
        self.proposal = proposal
        layout = QVBoxLayout(self)
        label = QLabel("O projeto atual será preservado até a confirmação de todas as alterações. "
                       "Atualizar combina os campos e preserva edições locais. Substituir pode remover "
                       "itens importados ausentes, preservando equipamentos manuais e compartilhados.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QTableWidget(len(proposal.feeders), 6)
        self.table.setHorizontalHeaderLabels(("Alimentador", "Situação", "Diferentes", "Novos", "Excluídos ao substituir", "Decisão"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.choices = {}
        for i, feeder in enumerate(proposal.feeders):
            status = "Novo" if not feeder.exists else "Já existe, sem alterações" if not feeder.changed_count and not feeder.added_count else "Já existe"
            if feeder.possible_matches:
                status = "Possível correspondência: " + ", ".join(feeder.possible_matches)
            for column, value in enumerate((feeder.label, status, str(feeder.changed_count),
                                           str(feeder.added_count), str(len(feeder.removed_keys)))):
                item = QTableWidgetItem(value)
                if column == 4:
                    item.setToolTip("Ausência fora das tabelas importadas não significa exclusão.\n" +
                                    "\n".join(equipment_label(key) for key in feeder.removed_keys))
                else:
                    item.setToolTip(value)
                self.table.setItem(i, column, item)
            combo = QComboBox()
            for text, value in (("Escolha…", None), ("Manter atual / ignorar", "keep"),
                                ("Atualizar / incluir", "update"), ("Substituir", "replace")):
                combo.addItem(text, value)
            if feeder.possible_matches:
                combo.setItemText(2, "Incluir como rede independente")
                combo.setToolTip("Para vincular ao equipamento existente, cancele e selecione a rede de destino na importação.")
            if not feeder.exists and not feeder.possible_matches:
                combo.setCurrentIndex(2)
            self.choices[feeder.circuit_id] = combo
            self.table.setCellWidget(i, 5, combo)
            combo.currentIndexChanged.connect(self._validate)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        for label, value in (("Manter existentes", "keep"), ("Atualizar existentes", "update")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=value: self._choose_existing(value))
            actions.addWidget(button)
        layout.addLayout(actions)
        self.impact = QPlainTextEdit()
        self.impact.setReadOnly(True)
        self.impact.setMaximumHeight(110)
        layout.addWidget(self.impact)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Revisar e aplicar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()

    def _choose_existing(self, value):
        for feeder in self.proposal.feeders:
            if feeder.exists:
                combo = self.choices[feeder.circuit_id]
                combo.setCurrentIndex(combo.findData(value))

    def _validate(self):
        if hasattr(self, "buttons"):
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
                all(combo.currentData() for combo in self.choices.values()))
            removed = {key for feeder in self.proposal.feeders
                       if self.choices[feeder.circuit_id].currentData() == "replace" for key in feeder.removed_keys}
            self.impact.setPlainText("Exclusões propostas: " + str(len(removed)) + "\n" +
                                     "\n".join(equipment_label(key) for key in sorted(removed)))

    def decisions(self):
        return {key: combo.currentData() for key, combo in self.choices.items()}

    def accept(self):
        if all(self.decisions().values()):
            super().accept()


class FieldConflictDialog(QDialog):
    def __init__(self, conflicts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resolver alterações por campo")
        self.resize(1100, 560)
        layout = QVBoxLayout(self)
        label = QLabel("O projeto e a origem possuem alterações diferentes. Escolha explicitamente o valor de cada campo.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QTableWidget(len(conflicts), 6)
        self.table.setHorizontalHeaderLabels(("Equipamento", "Campo", "Importação anterior", "Projeto atual", "Recebido", "Decisão"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.choices = {}
        for i, conflict in enumerate(conflicts):
            for column, value in enumerate((equipment_label(conflict.key), field_label(conflict.field),
                                           conflict.baseline, conflict.current, conflict.incoming)):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(i, column, item)
            combo = QComboBox()
            for label, value in (("Escolha…", None), ("Manter atual", "existing"), ("Usar recebido", "incoming")):
                combo.addItem(label, value)
            self.choices[conflict.decision_key] = combo
            self.table.setCellWidget(i, 5, combo)
            combo.currentIndexChanged.connect(self._validate)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar escolhas")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()

    def _validate(self):
        if hasattr(self, "buttons"):
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(all(self.decisions().values()))

    def decisions(self):
        return {key: combo.currentData() for key, combo in self.choices.items()}

    def accept(self):
        if all(self.decisions().values()):
            super().accept()


class ProjectProvenanceDialog(QDialog):
    def __init__(self, project, records=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Origem e histórico do projeto")
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        label = QLabel(f"Projeto {project.project_id} • revisão {project.revision}")
        label.setWordWrap(True)
        layout.addWidget(label)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        lines = []
        for record in records:
            lines.extend((equipment_label(record.key), f"Identidade interna: {record.equipment_id}"))
            if record.key.entity == "switches":
                values = dict(record.values)
                lines.append(f"Estado normal: {values['normal_states']}; estado no estudo: {values['states']}")
            for origin in record.origins:
                lines.append(f"  {origin.timestamp} • {origin.kind} • {origin.file.path or 'Criado/editado no projeto'}\n"
                             f"  Tabela: {origin.table} • ID original: {origin.native_id} • lote: {origin.batch_id}\n"
                             f"  Transformação: {origin.transformation or 'nenhuma'}")
            for name, value in record.values:
                origin = dict(record.field_origins).get(name, record.origins[0])
                lines.append(f"  {field_label(name)} = {value} — {origin.kind}: {origin.file.path or 'projeto'}")
            lines.append("")
        lines.append(f"Conexões pendentes: {len(project.pending)}")
        from .project_state import _dependencies
        current = project.records
        for key, row in project.pending.items():
            missing = [equipment_label(dep) for dep in _dependencies(key, row) if dep not in current]
            lines.append(f"  {equipment_label(key)} — aguardando {', '.join(missing)}; origem: {row.origins[0].file.path}")
        lines.append("\nOperações confirmadas:")
        lines.extend(f"  r{event.revision} • {event.timestamp} • {event.operation} • {len(event.equipment_ids)} equipamento(s)"
                     for event in project.history)
        text.setPlainText("\n".join(lines))
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fechar")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
