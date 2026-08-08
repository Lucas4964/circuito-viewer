"""Modelo Qt editável da grade fixa de quatro patamares."""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import QSpinBox, QStyledItemDelegate, QWidget

from .calculation_levels import CalculationLevelCatalog


class PatamaresTableModel(QAbstractTableModel):
    HEADERS = ("NPAT", "NOME", "HORARIO_INI", "HORARIO_FIM", "HORARIO_REF")
    contentChanged = pyqtSignal()
    validationFailed = pyqtSignal(str)

    def __init__(self, catalog: CalculationLevelCatalog, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.catalog = catalog

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.catalog)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: ANN001, N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(  # noqa: ANN001, ANN201, N802
        self, section, orientation, role=Qt.ItemDataRole.DisplayRole
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= int(section) < len(self.HEADERS)
        ):
            return self.HEADERS[int(section)]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: ANN201
        if not index.isValid() or role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.EditRole,
            Qt.ItemDataRole.ToolTipRole,
        }:
            return None
        draft = self.catalog.draft(index.row())
        values = (
            draft.npat,
            draft.name,
            draft.start_hour,
            draft.end_hour,
            draft.reference_hour,
        )
        value = values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Editar {self.HEADERS[index.column()]} do patamar."
        return "" if value is None else value

    def flags(self, index: QModelIndex):  # noqa: ANN201
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

    def setData(  # noqa: N802
        self,
        index: QModelIndex,
        value,  # noqa: ANN001
        role=Qt.ItemDataRole.EditRole,  # noqa: ANN001
    ) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        draft = self.catalog.draft(index.row())
        column = index.column()
        if column == 1:
            converted: object = "" if value is None else str(value)
            attribute = "name"
        else:
            if value is None or (isinstance(value, str) and not value.strip()):
                converted = None
            else:
                try:
                    if isinstance(value, bool):
                        raise ValueError
                    if isinstance(value, int):
                        converted = value
                    elif isinstance(value, str):
                        converted = int(value.strip())
                    else:
                        raise ValueError
                except (TypeError, ValueError):
                    self.validationFailed.emit(
                        f"{self.HEADERS[column]} deve ser um número inteiro."
                    )
                    return False
            maximum = 3 if column == 0 else 23
            if converted is not None and not 0 <= converted <= maximum:
                self.validationFailed.emit(
                    f"{self.HEADERS[column]} deve estar entre 0 e {maximum}."
                )
                return False
            attribute = (
                "npat",
                "name",
                "start_hour",
                "end_hour",
                "reference_hour",
            )[column]
        if getattr(draft, attribute) == converted:
            return False
        setattr(draft, attribute, converted)
        self.dataChanged.emit(
            index,
            index,
            [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole],
        )
        self.contentChanged.emit()
        return True

    def set_catalog(self, catalog: CalculationLevelCatalog) -> None:
        self.beginResetModel()
        self.catalog = catalog
        self.endResetModel()


class PatamarNumberDelegate(QStyledItemDelegate):
    """Spin box com limite dependente da coluna numérica."""

    def createEditor(self, parent: QWidget, option, index):  # noqa: ANN001, ANN201, N802
        editor = QSpinBox(parent)
        editor.setFrame(False)
        # -1 não é um horário/NPAT: é apenas o estado visual vazio permitido
        # enquanto o usuário rearranja a grade. O salvamento continua exigindo
        # todos os valores entre os limites reais.
        editor.setRange(-1, 3 if index.column() == 0 else 23)
        editor.setSpecialValueText("")
        return editor

    def setEditorData(self, editor: QSpinBox, index: QModelIndex) -> None:  # noqa: N802
        value = index.model().data(index, Qt.ItemDataRole.EditRole)
        editor.setValue(-1 if value == "" or value is None else int(value))

    def setModelData(  # noqa: ANN001, N802
        self, editor: QSpinBox, model, index: QModelIndex
    ) -> None:
        editor.interpretText()
        value = editor.value()
        model.setData(index, "" if value < 0 else value, Qt.ItemDataRole.EditRole)
