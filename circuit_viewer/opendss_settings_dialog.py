"""Diálogo e persistência das configurações globais do OpenDSS.

A leitura e a gravação em ``QSettings`` vivem aqui, e não junto de
:mod:`circuit_viewer.opendss_settings`, porque ``QSettings`` é Qt e aquele
módulo é consumido pelo núcleo — que não importa Qt. É a mesma divisão que
``theme.py`` faz para a preferência de tema, com o par
``load_*``/``save_*`` sobre um ``QSettings`` recebido por parâmetro, o que
permite aos testes injetarem um armazenamento limpo.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .opendss_settings import (
    DEFAULT_OPENDSS_LOAD_SETTINGS,
    OpenDssLoadSettings,
    VMAXPU_RANGE,
    VMINPU_RANGE,
    settings_from_mapping,
)


SETTINGS_PREFIX = "opendss/load_"


def load_opendss_settings(settings: QSettings) -> OpenDssLoadSettings:
    """Lê a configuração salva; ausência ou corrupção caem no padrão.

    A tolerância é deliberada e vem de :func:`settings_from_mapping`: uma
    preferência estragada — por edição manual do registro ou por uma versão
    anterior do formato — não pode impedir a aplicação de abrir.
    """

    stored = {
        key: settings.value(f"{SETTINGS_PREFIX}{key}")
        for key in DEFAULT_OPENDSS_LOAD_SETTINGS.as_mapping()
    }
    return settings_from_mapping(
        {key: value for key, value in stored.items() if value is not None}
    )


def save_opendss_settings(
    settings: QSettings,
    value: OpenDssLoadSettings,
) -> None:
    for key, text in value.as_mapping().items():
        settings.setValue(f"{SETTINGS_PREFIX}{key}", text)
    settings.sync()


class OpenDssSettingsDialog(QDialog):
    """Define os parâmetros globais aplicados a todas as cargas do modelo.

    Os campos nascem desabilitados junto com a caixa de aplicação: enquanto ela
    estiver desmarcada nenhum comando é emitido e o arquivo sai exatamente como
    saía antes desta configuração existir. Os valores continuam guardados, para
    o usuário não ter de redigitá-los ao reativar.

    As faixas dos campos são o segundo nível de validação. O primeiro é a
    invariante de :class:`OpenDssLoadSettings`; o OpenDSS não é nível nenhum —
    ele aceita ``vminpu=-1`` em silêncio.
    """

    def __init__(
        self,
        settings: OpenDssLoadSettings | None = None,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configurações do OpenDSS")
        self.setModal(True)

        current = settings or DEFAULT_OPENDSS_LOAD_SETTINGS
        layout = QVBoxLayout(self)

        explanation = QLabel(
            "Vminpu e Vmaxpu delimitam a faixa em que cada carga se comporta "
            "como potência constante. Fora dela o OpenDSS converte a carga "
            "para impedância constante, o que reduz a queda de tensão "
            "calculada — baixar Vminpu mantém o modelo de potência constante "
            "em alimentadores mais carregados."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.apply_limits_check = QCheckBox(
            "Aplicar limites de tensão às cargas"
        )
        self.apply_limits_check.setObjectName("opendss_apply_voltage_limits")
        self.apply_limits_check.setToolTip(
            "Desmarcado, o modelo usa os padrões do OpenDSS (0,95 e 1,05) e "
            "nenhum comando é acrescentado ao arquivo."
        )
        self.apply_limits_check.setChecked(current.voltage_limits_enabled)
        layout.addWidget(self.apply_limits_check)

        self.fields = QWidget(self)
        form = QFormLayout(self.fields)
        form.setContentsMargins(0, 0, 0, 0)
        self.vminpu_input = self._spin_box(
            "opendss_vminpu",
            VMINPU_RANGE,
            current.vminpu,
            "Abaixo desta tensão a carga deixa de ser potência constante.",
        )
        form.addRow("vminpu:", self.vminpu_input)
        self.vmaxpu_input = self._spin_box(
            "opendss_vmaxpu",
            VMAXPU_RANGE,
            current.vmaxpu,
            "Acima desta tensão a carga deixa de ser potência constante.",
        )
        form.addRow("vmaxpu:", self.vmaxpu_input)
        layout.addWidget(self.fields)

        preview_caption = QLabel("Comandos acrescentados ao arquivo master:")
        layout.addWidget(preview_caption)
        self.preview_label = QLabel()
        self.preview_label.setObjectName("opendss_settings_preview")
        self.preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.preview_label.setStyleSheet(
            "font-family: monospace; padding: 6px;"
            "border: 1px solid palette(mid);"
        )
        layout.addWidget(self.preview_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        restore = self.buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults
        )
        if restore is not None:
            restore.clicked.connect(self.restore_defaults)
        layout.addWidget(self.buttons)

        self.apply_limits_check.toggled.connect(self._sync_fields)
        self.vminpu_input.valueChanged.connect(self._sync_preview)
        self.vmaxpu_input.valueChanged.connect(self._sync_preview)
        self._sync_fields(self.apply_limits_check.isChecked())

    def _spin_box(
        self,
        object_name: str,
        limits: tuple[float, float],
        value: float,
        tooltip: str,
    ) -> QDoubleSpinBox:
        box = QDoubleSpinBox(self)
        box.setObjectName(object_name)
        box.setDecimals(3)
        box.setSingleStep(0.01)
        box.setRange(*limits)
        box.setValue(value)
        box.setToolTip(tooltip)
        return box

    def _sync_fields(self, enabled: bool) -> None:
        self.fields.setEnabled(bool(enabled))
        self._sync_preview()

    def _sync_preview(self) -> None:
        """Mostra exatamente as linhas que irão para o master.

        A pré-visualização vem do mesmo ``batch_edit_commands()`` que gera o
        arquivo — não de uma segunda formatação — para não haver como o diálogo
        prometer uma coisa e o arquivo trazer outra.
        """

        commands = self.settings().batch_edit_commands()
        self.preview_label.setText(
            "\n".join(commands)
            if commands
            else "— nenhum; o OpenDSS usará 0,95 e 1,05 —"
        )

    def restore_defaults(self) -> None:
        self.apply_limits_check.setChecked(
            DEFAULT_OPENDSS_LOAD_SETTINGS.voltage_limits_enabled
        )
        self.vminpu_input.setValue(DEFAULT_OPENDSS_LOAD_SETTINGS.vminpu)
        self.vmaxpu_input.setValue(DEFAULT_OPENDSS_LOAD_SETTINGS.vmaxpu)

    def settings(self) -> OpenDssLoadSettings:
        """Configuração escolhida, ou o padrão se os campos forem incoerentes.

        As faixas dos campos garantem ``vminpu <= 1 <= vmaxpu``, então o
        ``except`` não deveria disparar; ele existe para que uma faixa futura
        mais frouxa não transforme um erro de digitação em exceção no meio do
        diálogo.
        """

        try:
            return OpenDssLoadSettings(
                voltage_limits_enabled=self.apply_limits_check.isChecked(),
                vminpu=self.vminpu_input.value(),
                vmaxpu=self.vmaxpu_input.value(),
            )
        except ValueError:
            return DEFAULT_OPENDSS_LOAD_SETTINGS
