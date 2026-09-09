"""Diálogo e persistência dos parâmetros geométricos do Graphviz ``dot``."""

from __future__ import annotations

from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .graphviz_layout import (
    DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS,
    GRAPHVIZ_CIRCUIT_SEPARATION_RANGE,
    GRAPHVIZ_CROSSING_MINIMIZATION_RANGE,
    GRAPHVIZ_NODE_SEPARATION_RANGE,
    GRAPHVIZ_RANK_SEPARATION_RANGE,
    GRAPHVIZ_TREE_EDGE_MINLEN_RANGE,
    GRAPHVIZ_TREE_EDGE_WEIGHT_RANGE,
    GraphvizEdgeRouting,
    GraphvizLayoutSettings,
    graphviz_layout_settings_from_mapping,
)


GRAPHVIZ_SETTINGS_PREFIX = "block_graph/graphviz/"


def load_graphviz_layout_settings(settings: QSettings) -> GraphvizLayoutSettings:
    """Lê cada preferência com recuperação independente para o padrão."""

    stored = {
        key: settings.value(f"{GRAPHVIZ_SETTINGS_PREFIX}{key}")
        for key in DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS.as_mapping()
    }
    return graphviz_layout_settings_from_mapping(
        {key: value for key, value in stored.items() if value is not None}
    )


def save_graphviz_layout_settings(
    settings: QSettings,
    value: GraphvizLayoutSettings,
) -> None:
    """Persiste apenas opções suportadas pelo diálogo de ajuste fino."""

    for key, stored in value.as_mapping().items():
        settings.setValue(f"{GRAPHVIZ_SETTINGS_PREFIX}{key}", stored)
    settings.sync()


class GraphvizSettingsDialog(QDialog):
    """Editor modal com aplicação incremental e opções avançadas recolhíveis."""

    settingsApplied = pyqtSignal(object)

    def __init__(
        self,
        settings: GraphvizLayoutSettings = DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setObjectName("graphviz_settings_dialog")
        self.setWindowTitle("Configurações do Graphviz dot")
        self.setModal(True)
        self.setMinimumWidth(470)
        self._applied_settings = settings

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Estes parâmetros alteram somente a geometria calculada pelo dot. "
            "Cores, seleção e desenho continuam sob responsabilidade do Qt.",
            self,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        main_form = QFormLayout()
        self.circuit_separation_input = self._distance_input(
            "graphviz_circuit_separation_input",
            GRAPHVIZ_CIRCUIT_SEPARATION_RANGE,
        )
        self.circuit_separation_input.setToolTip(
            "Distância visual mínima entre circuitos após o cálculo do dot."
        )
        main_form.addRow(
            "Espaçamento mínimo entre circuitos:",
            self.circuit_separation_input,
        )

        self.node_separation_input = self._distance_input(
            "graphviz_node_separation_input",
            GRAPHVIZ_NODE_SEPARATION_RANGE,
        )
        self.node_separation_input.setToolTip(
            "Distância mínima horizontal entre nós do mesmo nível (nodesep)."
        )
        main_form.addRow(
            "Espaçamento horizontal entre nós:",
            self.node_separation_input,
        )

        self.rank_separation_input = self._distance_input(
            "graphviz_rank_separation_input",
            GRAPHVIZ_RANK_SEPARATION_RANGE,
        )
        self.rank_separation_input.setToolTip(
            "Distância mínima vertical entre níveis hierárquicos (ranksep)."
        )
        main_form.addRow(
            "Espaçamento vertical entre níveis:",
            self.rank_separation_input,
        )

        self.edge_routing_combo = QComboBox(self)
        self.edge_routing_combo.setObjectName("graphviz_edge_routing_combo")
        self.edge_routing_combo.addItem("Curvas", GraphvizEdgeRouting.SPLINE.value)
        self.edge_routing_combo.addItem(
            "Poligonal", GraphvizEdgeRouting.POLYLINE.value
        )
        self.edge_routing_combo.addItem("Reta", GraphvizEdgeRouting.LINE.value)
        self.edge_routing_combo.setToolTip(
            "Define como o dot calcula o caminho das arestas (splines)."
        )
        main_form.addRow("Traçado das arestas:", self.edge_routing_combo)

        self.equal_rank_spacing_checkbox = QCheckBox(
            "Uniformizar distância entre níveis",
            self,
        )
        self.equal_rank_spacing_checkbox.setObjectName(
            "graphviz_equal_rank_spacing_checkbox"
        )
        self.equal_rank_spacing_checkbox.setToolTip(
            "Acrescenta 'equally' ao ranksep para alinhar os centros dos níveis."
        )
        main_form.addRow("", self.equal_rank_spacing_checkbox)
        layout.addLayout(main_form)

        self.advanced_button = QPushButton("Opções avançadas ▸", self)
        self.advanced_button.setObjectName("graphviz_advanced_options_button")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setFlat(True)
        layout.addWidget(self.advanced_button)

        self.advanced_panel = QWidget(self)
        self.advanced_panel.setObjectName("graphviz_advanced_options_panel")
        advanced_form = QFormLayout(self.advanced_panel)
        self.switches_as_nodes_checkbox = QCheckBox(
            "Considerar chaves como nós no cálculo (experimental)",
            self.advanced_panel,
        )
        self.switches_as_nodes_checkbox.setObjectName(
            "graphviz_switches_as_nodes_checkbox"
        )
        self.switches_as_nodes_checkbox.setToolTip(
            "Faz o dot reservar um nó invisível com o tamanho da etiqueta para "
            "cada chave. No restante da aplicação, as chaves continuam sendo arestas."
        )
        advanced_form.addRow("", self.switches_as_nodes_checkbox)

        self.tree_edge_weight_input = QSpinBox(self.advanced_panel)
        self.tree_edge_weight_input.setObjectName("graphviz_tree_edge_weight_input")
        self.tree_edge_weight_input.setRange(*GRAPHVIZ_TREE_EDGE_WEIGHT_RANGE)
        self.tree_edge_weight_input.setToolTip(
            "Pesos maiores tendem a deixar relações pai-filho mais curtas e retas."
        )
        advanced_form.addRow(
            "Peso das arestas hierárquicas:",
            self.tree_edge_weight_input,
        )

        self.tree_edge_minlen_input = QSpinBox(self.advanced_panel)
        self.tree_edge_minlen_input.setObjectName(
            "graphviz_tree_edge_minlen_input"
        )
        self.tree_edge_minlen_input.setRange(*GRAPHVIZ_TREE_EDGE_MINLEN_RANGE)
        self.tree_edge_minlen_input.setToolTip(
            "Quantidade mínima de níveis atravessados por uma relação hierárquica."
        )
        advanced_form.addRow(
            "Distância mínima em níveis:",
            self.tree_edge_minlen_input,
        )

        self.crossing_minimization_input = QDoubleSpinBox(self.advanced_panel)
        self.crossing_minimization_input.setObjectName(
            "graphviz_crossing_minimization_input"
        )
        self.crossing_minimization_input.setRange(
            *GRAPHVIZ_CROSSING_MINIMIZATION_RANGE
        )
        self.crossing_minimization_input.setDecimals(1)
        self.crossing_minimization_input.setSingleStep(0.1)
        self.crossing_minimization_input.setToolTip(
            "Multiplica o esforço do dot para reduzir cruzamentos. Valores maiores "
            "podem aumentar o tempo de cálculo."
        )
        advanced_form.addRow(
            "Esforço para reduzir cruzamentos:",
            self.crossing_minimization_input,
        )
        self.advanced_panel.hide()
        layout.addWidget(self.advanced_panel)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.RestoreDefaults,
            parent=self,
        )
        restore = self.buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults
        )
        if restore is not None:
            restore.setText("Restaurar configurações originais")
            restore.clicked.connect(self.restore_defaults)
        apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.setText("Aplicar")
            apply_button.clicked.connect(self.apply_settings)
        cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setText("Cancelar")
        self.buttons.accepted.connect(self.accept_settings)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.advanced_button.toggled.connect(self._set_advanced_visible)
        self.set_settings(settings)

    @staticmethod
    def _distance_input(
        object_name: str,
        limits: tuple[float, float],
    ) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setObjectName(object_name)
        field.setRange(*limits)
        field.setDecimals(1)
        field.setSingleStep(2.0)
        field.setSuffix(" px")
        return field

    @property
    def applied_settings(self) -> GraphvizLayoutSettings:
        return self._applied_settings

    def settings(self) -> GraphvizLayoutSettings:
        routing = self.edge_routing_combo.currentData()
        return GraphvizLayoutSettings(
            circuit_separation_px=self.circuit_separation_input.value(),
            node_separation_px=self.node_separation_input.value(),
            rank_separation_px=self.rank_separation_input.value(),
            edge_routing=GraphvizEdgeRouting(str(routing)),
            equal_rank_spacing=self.equal_rank_spacing_checkbox.isChecked(),
            switches_as_nodes=self.switches_as_nodes_checkbox.isChecked(),
            tree_edge_weight=self.tree_edge_weight_input.value(),
            tree_edge_minlen=self.tree_edge_minlen_input.value(),
            crossing_minimization=self.crossing_minimization_input.value(),
        )

    def set_settings(self, settings: GraphvizLayoutSettings) -> None:
        self.circuit_separation_input.setValue(settings.circuit_separation_px)
        self.node_separation_input.setValue(settings.node_separation_px)
        self.rank_separation_input.setValue(settings.rank_separation_px)
        routing_index = self.edge_routing_combo.findData(
            settings.edge_routing.value
        )
        self.edge_routing_combo.setCurrentIndex(max(0, routing_index))
        self.equal_rank_spacing_checkbox.setChecked(settings.equal_rank_spacing)
        self.switches_as_nodes_checkbox.setChecked(settings.switches_as_nodes)
        self.tree_edge_weight_input.setValue(settings.tree_edge_weight)
        self.tree_edge_minlen_input.setValue(settings.tree_edge_minlen)
        self.crossing_minimization_input.setValue(
            settings.crossing_minimization
        )

    def restore_defaults(self) -> None:
        """Restaura os valores originais nos campos sem recalcular ainda."""

        self.set_settings(DEFAULT_GRAPHVIZ_LAYOUT_SETTINGS)

    def apply_settings(self) -> None:
        value = self.settings()
        if value == self._applied_settings:
            return
        self._applied_settings = value
        self.settingsApplied.emit(value)

    def accept_settings(self) -> None:
        self.apply_settings()
        self.accept()

    def _set_advanced_visible(self, visible: bool) -> None:
        self.advanced_panel.setVisible(bool(visible))
        self.advanced_button.setText(
            "Opções avançadas ▾" if visible else "Opções avançadas ▸"
        )
        self.adjustSize()


__all__ = [
    "GRAPHVIZ_SETTINGS_PREFIX",
    "GraphvizSettingsDialog",
    "load_graphviz_layout_settings",
    "save_graphviz_layout_settings",
]
