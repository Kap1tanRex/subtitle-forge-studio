"""Поиск и замена по репликам.

Окно **немодальное**: искать приходится по ходу правки, и запирать программу
на время поиска значило бы заставлять открывать и закрывать его на каждое
слово. Поэтому найденное подсвечивается в списке, а править можно не закрывая.

Замена всех совпадений — **одна команда**. Триста отдельных правок в истории
означали бы триста нажатий Ctrl+Z, чтобы вернуться, и человек, ошибшийся в
образце, не смог бы откатиться разумным способом.

Перед массовой заменой показывается число: «заменить 412 совпадений в 380
репликах» — это то, что позволяет заметить неверный образец до, а не после.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.commands import CompositeCommand, SetText
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import format_srt
from sfstudio.core.undo import UndoStack
from sfstudio.services.search import (
    SearchQuery,
    SearchScope,
    find_all,
    replacements,
)
from sfstudio.ui.combo import select_data

__all__ = ["FindDialog"]

#: Сколько находок показывать. Список нужен для выбора, а не для отчёта:
#: десять тысяч строк в нём не помогут никому, а память займут.
MAX_SHOWN = 500


class FindDialog(QDialog):
    """Окно поиска и замены."""

    #: Пользователь выбрал находку — окну надо перейти к этой реплике.
    event_activated = Signal(int)
    document_edited = Signal()

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        parent: QWidget | None = None,
        *,
        selection: list[int] | None = None,
        cursor_eid: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Найти и заменить")
        self.resize(560, 420)
        # Немодальное: искать надо по ходу правки.
        self.setModal(False)
        self._doc = doc
        self._undo = undo
        self._selection = list(selection or ())
        self._cursor_eid = cursor_eid
        self._matches: list = []

        layout = QVBoxLayout(self)
        layout.addWidget(self._query_form())
        layout.addWidget(self._results(), 1)
        layout.addWidget(self._buttons())
        self._refresh()

    # -- построение ---------------------------------------------------------- #

    def _query_form(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("что искать")
        self.find_edit.textChanged.connect(self._refresh)
        form.addRow("Найти", self.find_edit)

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("чем заменить")
        form.addRow("Заменить на", self.replace_edit)

        options = QHBoxLayout()
        self.case_check = QCheckBox("Учитывать регистр")
        self.word_check = QCheckBox("Слово целиком")
        self.regex_check = QCheckBox("Регулярное выражение")
        self.tags_check = QCheckBox("Искать и в разметке")
        self.tags_check.setToolTip(
            "По умолчанию содержимое фигурных скобок пропускается: там теги "
            "оформления, а не речь"
        )
        for check in (self.case_check, self.word_check, self.regex_check,
                      self.tags_check):
            check.toggled.connect(self._refresh)
            options.addWidget(check)
        options.addStretch(1)
        form.addRow("", _wrap(options))

        self.scope_box = QComboBox()
        for scope in SearchScope:
            self.scope_box.addItem(scope.title, scope)
        self.scope_box.currentIndexChanged.connect(self._refresh)
        form.addRow("Область", self.scope_box)
        return box

    def _results(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        layout.addWidget(self.status)

        self.list = QListWidget()
        self.list.itemActivated.connect(self._go_to)
        self.list.currentItemChanged.connect(self._go_to)
        layout.addWidget(self.list, 1)
        return box

    def _buttons(self) -> QWidget:
        buttons = QDialogButtonBox()
        self.replace_button = buttons.addButton("Заменить всё",
                                                QDialogButtonBox.ActionRole)
        self.replace_button.clicked.connect(self._replace_all)
        close = buttons.addButton("Закрыть", QDialogButtonBox.RejectRole)
        close.clicked.connect(self.close)
        return buttons

    # -- работа -------------------------------------------------------------- #

    def query(self) -> SearchQuery:
        return SearchQuery(
            text=self.find_edit.text(),
            regex=self.regex_check.isChecked(),
            case_sensitive=self.case_check.isChecked(),
            whole_word=self.word_check.isChecked(),
            include_tags=self.tags_check.isChecked(),
            scope=self.scope_box.currentData() or SearchScope.ALL,
        )

    def set_scope(self, scope: SearchScope) -> None:
        select_data(self.scope_box, scope)

    def _refresh(self) -> None:
        """Пересчитывает находки. Зовётся на каждый символ в поле поиска."""
        query = self.query()
        self._matches = find_all(
            self._doc,
            query,
            selection=self._selection,
            cursor_eid=self._cursor_eid,
            limit=MAX_SHOWN,
        )

        self.list.clear()
        for match in self._matches:
            event = self._doc.get(match.eid)
            if event is None:
                continue
            item = QListWidgetItem(f"{format_srt(event.start)}   {match.preview()}")
            item.setData(Qt.UserRole, match.eid)
            self.list.addItem(item)

        self._update_status(query)
        self.replace_button.setEnabled(bool(self._matches))

    def _update_status(self, query: SearchQuery) -> None:
        if not query.text:
            self.status.setText("Введите, что искать.")
            return
        if query.compile() is None:
            # Незаконченное выражение — обычное состояние поля при наборе.
            self.status.setText("Выражение пока незакончено.")
            return
        if not self._matches:
            self.status.setText("Ничего не найдено.")
            return

        shown = len(self._matches)
        tail = f" (показаны первые {MAX_SHOWN})" if shown >= MAX_SHOWN else ""
        self.status.setText(f"Найдено: {shown}{tail}")

    def _go_to(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        eid = item.data(Qt.UserRole)
        if isinstance(eid, int):
            self.event_activated.emit(eid)

    def _replace_all(self) -> None:
        """Заменяет всё найденное одной командой — после подтверждения."""
        query = self.query()
        changes = list(
            replacements(
                self._doc,
                query,
                self.replace_edit.text(),
                selection=self._selection,
                cursor_eid=self._cursor_eid,
            )
        )
        if not changes:
            self.status.setText("Заменять нечего.")
            return

        answer = QMessageBox.question(
            self,
            "Заменить всё",
            f"Изменить реплик: {len(changes)}.\n\n"
            f"Например:\n«{_shorten(changes[0][1])}»\nстанет\n«{_shorten(changes[0][2])}»"
            "\n\nПродолжить?",
        )
        if answer != QMessageBox.Yes:
            return

        # Одна команда на всю замену: иначе возврат стоил бы столько нажатий
        # Ctrl+Z, сколько реплик задето.
        command = CompositeCommand(
            [SetText(eid, updated) for eid, _before, updated in changes],
            label=f"Замена в {len(changes)} репликах",
        )
        self._undo.run(command)
        self.document_edited.emit()
        self._refresh()
        self.status.setText(f"Заменено в {len(changes)} репликах.")


def _wrap(layout) -> QWidget:
    holder = QWidget()
    holder.setLayout(layout)
    return holder


def _shorten(text: str, width: int = 60) -> str:
    flat = text.replace("\n", " ")
    return flat if len(flat) <= width else flat[: width - 1] + "…"
