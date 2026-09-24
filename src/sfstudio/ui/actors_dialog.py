"""Управление акторами: список действующих лиц с цветами.

Отдельно стоит объяснить кнопку «Занести встреченных». Файл почти никогда не
приходит пустым: в нём уже есть имена в поле ``Name``, проставленные другой
программой или переводчиком. Заводить их в реестре по одному руками — работа
на полчаса там, где хватает одного нажатия, поэтому диалог показывает, сколько
имён встречается в репликах, но отсутствует в реестре, и предлагает добавить
их разом.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
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
from sfstudio.ui.widgets import hbox, icon_button, label, set_icon

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
    #: Переписать метки говорящих в тексте реплик.
    relabel_requested = Signal()

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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Шапка: завести актора, занести встреченных, правка выбранного.
        head = QWidget()
        head.setProperty("role", "toolhead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setFixedHeight(40)
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(8, 0, 8, 0)
        head_row.setSpacing(6)
        add = QToolButton()
        add.setText(tr('Актёр'))
        add.setProperty("role", "outlined")
        add.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        add.setFixedHeight(28)
        set_icon(add, "event_add", "text")
        add.setToolTip(tr('Завести актора'))
        add.clicked.connect(self._add)
        head_row.addWidget(add)
        self.import_button = QPushButton(tr('Занести встреченных'))
        self.import_button.clicked.connect(self._import_used)
        head_row.addWidget(self.import_button)
        head_row.addStretch(1)
        for icon, tip, slot in (
            ("pencil", tr('Переименовать'), self._rename),
            ("note", tr('Заметка…'), self._set_note),
            ("event_delete", tr('Удалить актора'), self._remove),
        ):
            button = icon_button(icon, tip)
            button.clicked.connect(slot)
            head_row.addWidget(button)
        self.total = label("", "hint")
        head_row.addWidget(self.total)
        layout.addWidget(head)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr('Цвет'), tr('Имя'), tr('Реплик'), tr('Заметка')])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setIconSize(QSize(18, 18))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setFixedHeight(28)
        header.setSectionResizeMode(COL_COLOR, QHeaderView.Fixed)
        header.resizeSection(COL_COLOR, 48)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_COUNT, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_NOTE, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        self.hint = QLabel("")
        self.hint.setProperty("role", "hint")
        self.hint.setWordWrap(True)
        self.hint.setContentsMargins(12, 6, 12, 6)
        layout.addWidget(self.hint)

        # Подвал: главное действие вкладки — назначить выбранного актора.
        foot = QWidget()
        foot.setProperty("role", "footer")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        foot_column = QVBoxLayout(foot)
        foot_column.setContentsMargins(16, 12, 16, 16)
        foot_column.setSpacing(10)
        self.relabel_button = QPushButton(tr('Обновить метки'))
        self.relabel_button.setToolTip(
            tr('Переписать имена говорящих в тексте реплик по настройкам')
        )
        self.relabel_button.clicked.connect(self.relabel_requested)
        foot_column.addLayout(hbox(label(tr('Имя в тексте — в настройках'), "hint"), None,
                                   self.relabel_button))
        self.assign_button = QPushButton(tr('Назначить выделенным репликам'))
        self.assign_button.setProperty("role", "primary")
        self.assign_button.setToolTip(
            tr('Выберите реплики в таблице или на таймлайне, затем актора здесь')
        )
        self.assign_button.clicked.connect(self._assign)
        foot_column.addWidget(self.assign_button)
        layout.addWidget(foot)

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
            swatch.setIcon(_swatch(QColor(actor.color.to_hex())))
            swatch.setToolTip(tr('Цвет — двойной щелчок, чтобы сменить'))
            swatch.setData(Qt.UserRole, actor.name)
            self.table.setItem(row, COL_COLOR, swatch)

            name_item = QTableWidgetItem(actor.name)
            name_item.setData(Qt.UserRole, actor.name)
            self.table.setItem(row, COL_NAME, name_item)

            count_item = QTableWidgetItem(str(counts.get(actor.name, 0)))
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, COL_COUNT, count_item)

            self.table.setItem(row, COL_NOTE, QTableWidgetItem(actor.note))

        self.total.setText(tr('Актёров: {0}').format(len(self._doc.actors)))
        unregistered = sorted(set(counts) - set(self._doc.actors.names()))
        self.import_button.setEnabled(bool(unregistered))
        if unregistered:
            preview = ", ".join(unregistered[:6])
            more = tr(' и ещё {0}').format(len(unregistered) - 6) if len(unregistered) > 6 else ""
            self.hint.setText(
                tr('В репликах встречается {0} имён без цвета: '
                       '{1}{2}.').format(len(unregistered), preview, more)
            )
        else:
            self.hint.setText(tr('Все встречающиеся имена заведены.'))

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
        name, ok = QInputDialog.getText(self, tr('Новый актор'), tr('Имя:'))
        if not ok or not name.strip():
            return
        if name.strip() in self._doc.actors:
            QMessageBox.information(self, tr('Актор '
                   'есть'), tr('«{0}» уже в списке.').format(name.strip()))
            return
        self._run(AddActor(name))

    def _rename(self) -> None:
        current = self._selected_name()
        if current is None:
            return
        name, ok = QInputDialog.getText(self, tr('Переименовать'), tr('Имя:'), text=current)
        if not ok or not name.strip() or name.strip() == current:
            return
        if name.strip() in self._doc.actors:
            QMessageBox.information(self, tr('Актор '
                   'есть'), tr('«{0}» уже в списке.').format(name.strip()))
            return
        # Переименование меняет и реплики — предупреждаем, если их много.
        count = self._doc.actors_in_use().get(current, 0)
        if count > 20:
            answer = QMessageBox.question(
                self, tr('Переименовать'),
                tr('Имя изменится в {0} репликах. Продолжить?').format(count),
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
        chosen = QColorDialog.getColor(QColor(actor.color.to_hex()), self, tr('Цвет актора'))
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
        note, ok = QInputDialog.getText(self, tr('Заметка'), tr('Заметка:'), text=actor.note)
        if ok:
            self._run(UpdateActor(name, note=note))

    def _remove(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        count = self._doc.actors_in_use().get(name, 0)
        answer = QMessageBox.question(
            self,
            tr('Удалить актора'),
            f"Убрать «{name}» из списка?\n\n"
            f"Имя останется в {count} репликах — удаляется только цвет."
            if count
            else tr('Убрать «{0}» из списка?').format(name),
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
            CompositeCommand(commands, label=tr('Занесено акторов: {0}').format(len(commands)))
        )

    def _on_double_click(self, _row: int, column: int) -> None:
        if column == COL_COLOR:
            self._recolor()
        elif column == COL_NOTE:
            self._set_note()
        else:
            self._rename()


def _swatch(colour: QColor) -> QIcon:
    """Квадратик цвета актора — как в списке реплик."""
    pixmap = QPixmap(36, 36)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(colour)
    painter.drawRoundedRect(0, 0, 36, 36, 8, 8)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


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
        self.setWindowTitle(tr('Акторы'))
        self.resize(560, 420)

        self.panel = ActorsPanel(doc, undo, self)
        self.panel.document_edited.connect(self.document_edited)
        # Назначать отсюда некому: окно не знает, что выделено в таблице.
        self.panel.assign_button.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self.panel, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.Close).setText(tr('Закрыть'))
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
