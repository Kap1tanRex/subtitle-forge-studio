"""Управление акторами: список действующих лиц с цветами.

Отдельно стоит объяснить кнопку «Занести встреченных». Файл почти никогда не
приходит пустым: в нём уже есть имена в поле ``Name``, проставленные другой
программой или переводчиком. Заводить их в реестре по одному руками — работа
на полчаса там, где хватает одного нажатия, поэтому диалог показывает, сколько
имён встречается в репликах, но отсутствует в реестре, и предлагает добавить
их разом.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.color import RGBA
from sfstudio.core.commands import (
    AddActor,
    CompositeCommand,
    RemoveActor,
    RenameActor,
    UpdateActor,
)
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack

__all__ = ["ActorsDialog", "ActorsPanel"]

COL_COLOR = 0
COL_NAME = 1
COL_COUNT = 2
COL_NOTE = 3


class ActorsPanel(QWidget):
    """Список акторов документа — панель, которую можно держать открытой.

    Раньше это было модальное окно: назначить говорящих десятку реплик
    значило десять раз открыть и закрыть его. При разметке многоголосого
    диалога это и есть основная работа, поэтому список живёт рядом с
    таблицей, а назначение делается одной кнопкой.
    """

    document_edited = Signal()
    #: Назначить этого актора выделенным репликам.
    assign_requested = Signal(str)

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._doc = doc
        self._undo = undo

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Цвет", "Имя", "Реплик", "Заметка"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_COLOR, QHeaderView.Fixed)
        header.resizeSection(COL_COLOR, 56)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_COUNT, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_NOTE, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        for caption, slot in (
            ("Добавить", self._add),
            ("Переименовать", self._rename),
            ("Цвет…", self._recolor),
            ("Заметка…", self._set_note),
            ("Удалить", self._remove),
        ):
            button = QPushButton(caption)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.assign_button = QPushButton("Назначить выделенным репликам")
        self.assign_button.setToolTip(
            "Выберите реплики в таблице или на таймлайне, затем актора здесь"
        )
        self.assign_button.clicked.connect(self._assign)
        layout.addWidget(self.assign_button)

        self.import_button = QPushButton("Занести встреченных")
        self.import_button.clicked.connect(self._import_used)
        layout.addWidget(self.import_button)

        self.hint = QLabel("")
        self.hint.setProperty("role", "hint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.refresh()

    def set_document(self, doc: SubtitleDocument, undo: UndoStack) -> None:
        """Панель живёт дольше документа: она в доке, а файлы открываются."""
        self._doc = doc
        self._undo = undo
        self.refresh()

    def _assign(self) -> None:
        name = self._selected_name()
        if name is not None:
            self.assign_requested.emit(name)

    # -- наполнение ------------------------------------------------------------- #

    def refresh(self) -> None:
        counts = self._doc.actors_in_use()
        self.table.setRowCount(0)

        for actor in self._doc.actors:
            row = self.table.rowCount()
            self.table.insertRow(row)

            swatch = QTableWidgetItem("")
            swatch.setBackground(QColor(actor.color.to_hex()))
            swatch.setData(Qt.UserRole, actor.name)
            self.table.setItem(row, COL_COLOR, swatch)

            name_item = QTableWidgetItem(actor.name)
            name_item.setData(Qt.UserRole, actor.name)
            self.table.setItem(row, COL_NAME, name_item)

            count_item = QTableWidgetItem(str(counts.get(actor.name, 0)))
            count_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, COL_COUNT, count_item)

            self.table.setItem(row, COL_NOTE, QTableWidgetItem(actor.note))

        unregistered = sorted(set(counts) - set(self._doc.actors.names()))
        self.import_button.setEnabled(bool(unregistered))
        if unregistered:
            preview = ", ".join(unregistered[:6])
            more = f" и ещё {len(unregistered) - 6}" if len(unregistered) > 6 else ""
            self.hint.setText(
                f"В репликах встречается {len(unregistered)} имён без цвета: "
                f"{preview}{more}."
            )
        else:
            self.hint.setText("Все встречающиеся имена заведены.")

    def _selected_name(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, COL_NAME)
        return item.data(Qt.UserRole) if item is not None else None

    # -- действия ---------------------------------------------------------------- #

    def _run(self, command) -> None:
        self._undo.run(command)
        self.document_edited.emit()
        self.refresh()

    def _add(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый актор", "Имя:")
        if not ok or not name.strip():
            return
        if name.strip() in self._doc.actors:
            QMessageBox.information(self, "Актор есть", f"«{name.strip()}» уже в списке.")
            return
        self._run(AddActor(name))

    def _rename(self) -> None:
        current = self._selected_name()
        if current is None:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Имя:", text=current)
        if not ok or not name.strip() or name.strip() == current:
            return
        if name.strip() in self._doc.actors:
            QMessageBox.information(self, "Актор есть", f"«{name.strip()}» уже в списке.")
            return
        # Переименование меняет и реплики — предупреждаем, если их много.
        count = self._doc.actors_in_use().get(current, 0)
        if count > 20:
            answer = QMessageBox.question(
                self, "Переименовать",
                f"Имя изменится в {count} репликах. Продолжить?",
            )
            if answer != QMessageBox.Yes:
                return
        self._run(RenameActor(current, name))

    def _recolor(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        actor = self._doc.actors.get(name)
        if actor is None:
            return
        chosen = QColorDialog.getColor(QColor(actor.color.to_hex()), self, "Цвет актора")
        if not chosen.isValid():
            return
        self._run(
            UpdateActor(name, color=RGBA(chosen.red(), chosen.green(), chosen.blue(), 255))
        )

    def _set_note(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        actor = self._doc.actors.get(name)
        if actor is None:
            return
        note, ok = QInputDialog.getText(self, "Заметка", "Заметка:", text=actor.note)
        if ok:
            self._run(UpdateActor(name, note=note))

    def _remove(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        count = self._doc.actors_in_use().get(name, 0)
        answer = QMessageBox.question(
            self,
            "Удалить актора",
            f"Убрать «{name}» из списка?\n\n"
            f"Имя останется в {count} репликах — удаляется только цвет."
            if count
            else f"Убрать «{name}» из списка?",
        )
        if answer == QMessageBox.Yes:
            self._run(RemoveActor(name))

    def _import_used(self) -> None:
        """Заводит всех, кто встречается в репликах, но не заведён."""
        unregistered = sorted(set(self._doc.actors_in_use()) - set(self._doc.actors.names()))
        if not unregistered:
            return
        commands = [AddActor(name) for name in unregistered]
        self._run(
            CompositeCommand(commands, label=f"Занесено акторов: {len(commands)}")
        )

    def _on_double_click(self, _row: int, column: int) -> None:
        if column == COL_COLOR:
            self._recolor()
        elif column == COL_NOTE:
            self._set_note()
        else:
            self._rename()


class ActorsDialog(QDialog):
    """Окно вокруг той же панели.

    Оставлено ради пункта меню и привычки: панель можно закрыть, а список
    акторов иногда нужен разово — открыть, поправить цвет, закрыть.
    """

    document_edited = Signal()

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Акторы")
        self.resize(560, 420)

        self.panel = ActorsPanel(doc, undo, self)
        self.panel.document_edited.connect(self.document_edited)
        # Назначать отсюда некому: окно не знает, что выделено в таблице.
        self.panel.assign_button.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self.panel, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.Close).setText("Закрыть")
        layout.addWidget(box)

    # -- то, чем пользуются снаружи ------------------------------------------ #

    @property
    def table(self):
        return self.panel.table

    @property
    def hint(self):
        return self.panel.hint

    @property
    def import_button(self):
        return self.panel.import_button

    def refresh(self) -> None:
        self.panel.refresh()
