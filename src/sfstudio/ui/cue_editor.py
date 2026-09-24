"""Реплика крупным планом: блок текста под списком и вкладка «Реплика».

Два виджета про одно и то же, но для разных минут работы.

* :class:`CueEditor` стоит под таблицей во вкладке «Список». Там пишут текст:
  сверху номер, время и скорость чтения, ниже оригинал, ещё ниже поле с
  длиной каждой строки справа, а под ним стиль и говорящий — то, что при
  переводе меняют, не отрываясь от текста.
* :class:`CuePanel` — отдельная вкладка: время с шагом по кадру, пауза до
  следующей реплики, свойства, пометка и заметка. Сюда приходят доводить.

Состояния реплики виджеты **не хранят**: читают документ при каждом
обновлении и пишут командами — по тому же правилу, что и весь интерфейс.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.core.commands import AssignActor, MoveEventsToLayer, SetStyle, SetTiming
from sfstudio.core.commands.text import SetNote, SetStatus
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import format_srt, parse_timecode
from sfstudio.core.undo import UndoStack
from sfstudio.core.workflow import STATUS_DONE, STATUS_DRAFT, STATUS_QUESTION
from sfstudio.ui.combo import index_of_data
from sfstudio.ui.widgets import (
    Segmented,
    caption,
    hbox,
    icon_button,
    kbd_button,
    label,
    section,
    vbox,
)

__all__ = ["CPS_WARNING", "CueEditor", "CuePanel", "gap_note"]

#: Скорость чтения, выше которой плашка желтеет. Та же, что в таблице.
CPS_WARNING = 17.0

#: Пометки в порядке сегментов вкладки «Реплика».
_STATUSES = (STATUS_DRAFT, STATUS_DONE, STATUS_QUESTION)


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}".replace(".", ",")


def gap_note(gap_ms: int, frame_ms: float) -> tuple[str, bool]:
    """Подпись паузы до следующей реплики и признак «тревожно».

    Меньше двух кадров — это не пауза, а мигание: глаз не успевает заметить,
    что субтитр сменился, и читает две реплики как одну.
    """
    if gap_ms < 0:
        return tr('{0} · перекрытие').format(_seconds(-gap_ms)), True
    if gap_ms < 2 * frame_ms:
        return tr('{0} · меньше 2 кадров').format(_seconds(gap_ms)), True
    return _seconds(gap_ms), False


def _next_after(doc: SubtitleDocument, eid: int):
    """Следующая реплика той же дорожки — к ней считается пауза."""
    event = doc.by_eid(eid)
    later = [
        (e.start, i, e) for i, e in enumerate(doc.events)
        if e.layer == event.layer and e.eid != eid and not e.comment
        and e.start >= event.start
    ]
    if not later:
        return None, -1
    _start, index, found = min(later, key=lambda item: (item[0], item[1]))
    return found, index


class CueEditor(QWidget):
    """Текст выделенной реплики под списком."""

    style_chosen = Signal(str)
    actor_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._syncing = False

        self.number = label("", "section")
        self.times = label("", "mono")
        self.times.setProperty("role", "hint")
        self.duration = label("", "hint")
        self.cps = label("", "pill")
        head = hbox(self.number, self.times, self.duration, None, self.cps, spacing=10)

        # Оригинал над переводом: взгляд идёт сверху вниз, и читать исходник
        # после собственного текста неудобно. Только для чтения.
        self.original = QPlainTextEdit()
        self.original.setReadOnly(True)
        self.original.setProperty("role", "reference")
        self.original.setMaximumHeight(52)
        self.original.setPlaceholderText(tr('Оригинал'))
        self._original_row = QFrame()
        self._original_row.setProperty("role", "plain")
        row = hbox(caption(tr('Оригинал')), self.original, spacing=10)
        self._original_row.setLayout(row)
        self.set_reference_visible(False)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(tr('Текст реплики (\\N — перевод строки)'))
        self.editor.setAccessibleName(tr('Текст реплики'))
        # Две-три строки реплики и не больше: остальное место — списку.
        self.editor.setMinimumHeight(52)
        self.editor.setMaximumHeight(96)
        # Длина каждой строки справа от текста: норма считается по строкам,
        # и сравнивать её глазами с числом в углу неудобно.
        self.lengths = QLabel("")
        self.lengths.setProperty("role", "hint")
        self.lengths.setAlignment(Qt.AlignTop | Qt.AlignRight)
        self.lengths.setFixedWidth(36)
        self.lengths.setToolTip(tr('Знаков в строке'))
        self.editor.textChanged.connect(self._count_lines)
        text_row = hbox(self.editor, self.lengths, spacing=4)

        self.style_box = QComboBox()
        self.style_box.setToolTip(tr('Стиль'))
        self.style_box.currentTextChanged.connect(self._on_style)
        self.actor_box = QComboBox()
        self.actor_box.setEditable(True)
        self.actor_box.setToolTip(tr('Говорящий'))
        self.actor_box.lineEdit().setPlaceholderText(tr('Актёр'))
        self.actor_box.lineEdit().editingFinished.connect(self._on_actor)
        self.actor_box.activated.connect(lambda _i: self._on_actor())
        self.spelling = label("", "hint")
        foot = hbox(self.style_box, self.actor_box, None, self.spelling, spacing=6)

        column = vbox(head, self._original_row, text_row, foot,
                      spacing=8, margins=(12, 10, 12, 12))
        self.setLayout(column)
        self.clear()

    # -- наполнение --------------------------------------------------------------- #

    def clear(self) -> None:
        self.number.setText("")
        self.times.setText("")
        self.duration.setText("")
        self.cps.setVisible(False)
        self.setEnabled(False)

    def show_event(self, doc: SubtitleDocument, eid: int) -> None:
        """Шапка и выпадающие списки — по реплике. Текст ставит окно."""
        event = doc.by_eid(eid)
        self.setEnabled(True)
        self._syncing = True
        try:
            self.number.setText(f"#{doc.index_of(eid) + 1}")
            self.times.setText(f"{format_srt(event.start)} → {format_srt(event.end)}")
            self.duration.setText(tr('{0} с').format(f"{event.duration / 1000:.2f}"
                                                     .replace(".", ",")))
            cps = event.cps()
            self.cps.setText(f"CPS {cps:.1f}".replace(".", ","))
            self.cps.setProperty("role", "chip-warning" if cps > CPS_WARNING else "pill")
            self.cps.style().unpolish(self.cps)
            self.cps.style().polish(self.cps)
            self.cps.setVisible(True)
            _fill(self.style_box, list(doc.styles), event.style)
            names = doc.actors.names()
            if event.name and event.name not in names:
                names = [*names, event.name]
            _fill(self.actor_box, ["", *names], event.name)
        finally:
            self._syncing = False

    def set_reference_visible(self, on: bool) -> None:
        self._original_row.setVisible(on)
        self.original.setVisible(on)

    def set_spelling(self, count: int | None) -> None:
        """Сколько слов с ошибкой. ``None`` — проверка выключена."""
        self.spelling.setText("" if count is None else tr('Орфография: {0}').format(count))

    def _count_lines(self) -> None:
        lines = self.editor.toPlainText().split("\n")
        self.lengths.setText("\n".join(str(len(line)) for line in lines))

    def _on_style(self, name: str) -> None:
        if not self._syncing and name:
            self.style_chosen.emit(name)

    def _on_actor(self) -> None:
        if not self._syncing:
            self.actor_chosen.emit(self.actor_box.currentText().strip())


def _fill(box: QComboBox, items: list[str], current: str) -> None:
    if [box.itemText(i) for i in range(box.count())] != items:
        box.clear()
        box.addItems(items)
    index = box.findText(current)
    if index >= 0:
        box.setCurrentIndex(index)
    elif box.isEditable():
        box.setEditText(current)


class _TimeField(QWidget):
    """Поле времени с шагом по кадру: ‹ поле ›."""

    committed = Signal(str)
    stepped = Signal(int)

    def __init__(self, title: str, minus: str, plus: str) -> None:
        super().__init__()
        self.edit = QLineEdit()
        self.edit.setProperty("role", "timecode")
        self.edit.setAccessibleName(title)
        self.edit.editingFinished.connect(lambda: self.committed.emit(self.edit.text()))
        back = icon_button("chevron_left", minus, size=13)
        forward = icon_button("chevron_right", plus, size=13)
        back.clicked.connect(lambda: self.stepped.emit(-1))
        forward.clicked.connect(lambda: self.stepped.emit(1))
        self.setLayout(vbox(caption(title), hbox(self.edit, back, forward, spacing=2),
                            spacing=4))


class CuePanel(QWidget):
    """Вкладка «Реплика»: время, свойства, пометка, заметка."""

    document_edited = Signal()
    #: Перейти к соседней реплике: -1 — к предыдущей, 1 — к следующей.
    step_requested = Signal(int)

    def __init__(self, doc: SubtitleDocument, undo: UndoStack, actor_command=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc = doc
        self._undo = undo
        self._actor_command = actor_command
        self._eid: int | None = None
        self._syncing = False
        #: Длительность кадра. Окно ставит её по видео; без видео — 23,976.
        self.frame_ms = 1000 / 23.976

        self.title = label("", "title")
        self.who = label("", "hint")
        prev_button = icon_button("chevron_up", tr('Предыдущая реплика'), tone="text")
        next_button = icon_button("chevron_down", tr('Следующая реплика'), tone="text")
        for button in (prev_button, next_button):
            button.setProperty("role", "outlined")
            button.setFixedSize(28, 28)
        prev_button.clicked.connect(lambda: self.step_requested.emit(-1))
        next_button.clicked.connect(lambda: self.step_requested.emit(1))
        head = hbox(self.title, self.who, None, prev_button, next_button, spacing=8)

        # -- время ------------------------------------------------------------- #
        self.start_field = _TimeField(tr('Начало'), tr('Начало на кадр раньше'),
                                      tr('Начало на кадр позже'))
        self.end_field = _TimeField(tr('Конец'), tr('Конец на кадр раньше'),
                                    tr('Конец на кадр позже'))
        self.duration_field = _TimeField(tr('Длительность'), tr('Короче на кадр'),
                                         tr('Длиннее на кадр'))
        self.start_edit = self.start_field.edit
        self.end_edit = self.end_field.edit
        self.duration_edit = self.duration_field.edit
        self.start_field.committed.connect(lambda t: self._commit("start", t))
        self.end_field.committed.connect(lambda t: self._commit("end", t))
        self.duration_field.committed.connect(lambda t: self._commit("duration", t))
        self.start_field.stepped.connect(lambda d: self._step("start", d))
        self.end_field.stepped.connect(lambda d: self._step("end", d))
        self.duration_field.stepped.connect(lambda d: self._step("end", d))

        self.gap_caption = caption(tr('Пауза до следующей'))
        self.gap = label("", "pill")
        self.gap.setMinimumHeight(28)
        gap_box = QWidget()
        gap_box.setLayout(vbox(self.gap_caption, self.gap, spacing=4))

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(self.start_field, 0, 0)
        grid.addWidget(self.end_field, 0, 1)
        grid.addWidget(self.duration_field, 1, 0)
        grid.addWidget(gap_box, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._tools = hbox(None, spacing=8)
        timing = vbox(section(tr('Время')), grid, self._tools, spacing=10)

        # -- свойства ---------------------------------------------------------- #
        self.style_box = QComboBox()
        self.style_box.currentTextChanged.connect(self._commit_style)
        self.actor_box = QComboBox()
        self.actor_box.setEditable(True)
        self.actor_box.lineEdit().editingFinished.connect(self._commit_actor)
        self.actor_box.activated.connect(lambda _i: self._commit_actor())
        self.track_box = QComboBox()
        self.track_box.currentIndexChanged.connect(self._commit_track)
        props = QGridLayout()
        props.setHorizontalSpacing(8)
        props.setVerticalSpacing(6)
        props.setColumnMinimumWidth(0, 88)
        for row, (title, box) in enumerate((
            (tr('Стиль'), self.style_box),
            (tr('Актёр'), self.actor_box),
            (tr('Дорожка'), self.track_box),
        )):
            props.addWidget(label(title, "hint"), row, 0)
            props.addWidget(box, row, 1)
        props.setColumnStretch(1, 1)
        properties = vbox(section(tr('Свойства')), props, spacing=10)

        # -- пометка ----------------------------------------------------------- #
        self.status = Segmented([tr('Черновик'), tr('Готово'), tr('Вопрос')])
        self.status.changed.connect(self._commit_status)
        self.note = QPlainTextEdit()
        self.note.setProperty("role", "note")
        self.note.setPlaceholderText(tr('Для редактора или заказчика'))
        self.note.setFixedHeight(72)
        self.note.setAccessibleName(tr('Заметка'))
        # Заметку пишут фразой; сохранять на каждую букву — сотня шагов
        # отмены на одно предложение. Пауза в полсекунды укладывает её в один.
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(500)
        self._note_timer.timeout.connect(self._commit_note)
        self.note.textChanged.connect(self._on_note_typed)
        mark = vbox(section(tr('Пометка')), self.status,
                    vbox(caption(tr('Заметка')), self.note, spacing=4), spacing=10)

        column = vbox(head, timing, properties, mark, None,
                      spacing=20, margins=(16, 14, 16, 14))
        self.setLayout(column)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.set_event(None)

    # -- внешнее ----------------------------------------------------------------- #

    def set_actions(self, actions: dict[str, QAction]) -> None:
        """Кнопки «Доводка…» и «Сдвиг…» — те же действия, что в меню."""
        while self._tools.count() > 1:
            item = self._tools.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for key, title in (("timing.auto", tr('Доводка…')), ("timing.shift", tr('Сдвиг…'))):
            action = actions.get(key)
            if action is None:
                continue
            button = kbd_button(title, action.shortcut().toString())
            button.setToolTip(action.text())
            button.clicked.connect(action.trigger)
            self._tools.insertWidget(self._tools.count() - 1, button)

    def set_document(self, doc: SubtitleDocument, undo: UndoStack) -> None:
        self._doc = doc
        self._undo = undo
        self.set_event(None)

    def set_event(self, eid: int | None) -> None:
        self._eid = eid if eid is not None and self._doc.has(eid) else None
        for widget in self.findChildren(QWidget):
            widget.setEnabled(self._eid is not None)
        if self._eid is None:
            self._syncing = True
            try:
                self.title.setText(tr('Нет выделенной реплики'))
                self.who.setText("")
                for edit in (self.start_edit, self.end_edit, self.duration_edit):
                    edit.clear()
                self.gap.setText("—")
                self.status.set_index(-1)
                self.note.clear()
            finally:
                self._syncing = False
            return
        self.refresh()

    def refresh(self) -> None:
        """Перечитывает всё из документа. Зовётся после любой правки."""
        if self._eid is None or not self._doc.has(self._eid):
            self.set_event(None)
            return
        doc = self._doc
        event = doc.by_eid(self._eid)
        self._syncing = True
        try:
            self.title.setText(f"#{doc.index_of(self._eid) + 1}")
            track = doc.tracks.by_layer(event.layer)
            where = track.display_name() if track is not None else ""
            self.who.setText(" · ".join(p for p in (event.name, where) if p))

            self.start_edit.setText(format_srt(event.start))
            self.end_edit.setText(format_srt(event.end))
            self.duration_edit.setText(_seconds(event.duration))

            following, index = _next_after(doc, self._eid)
            if following is None:
                self.gap_caption.setText(tr('Пауза до следующей'))
                self.gap.setText("—")
                alarm = False
            else:
                self.gap_caption.setText(tr('Пауза до #{0}').format(index + 1))
                text, alarm = gap_note(following.start - event.end, self.frame_ms)
                self.gap.setText(text)
            self.gap.setProperty("role", "chip-warning" if alarm else "pill")
            self.gap.style().unpolish(self.gap)
            self.gap.style().polish(self.gap)

            _fill(self.style_box, list(doc.styles), event.style)
            names = doc.actors.names()
            if event.name and event.name not in names:
                names = [*names, event.name]
            _fill(self.actor_box, ["", *names], event.name)
            self._fill_tracks(event.layer)

            status = event.status
            self.status.set_index(_STATUSES.index(status) if status in _STATUSES else -1)
            if self.note.toPlainText() != event.note and not self.note.hasFocus():
                self.note.setPlainText(event.note)
        finally:
            self._syncing = False

    def _fill_tracks(self, layer: int) -> None:
        tracks = self._doc.tracks.display_order(with_media=False)
        wanted = [(t.display_name(), t.layer) for t in tracks]
        current = [(self.track_box.itemText(i), self.track_box.itemData(i))
                   for i in range(self.track_box.count())]
        if current != wanted:
            self.track_box.clear()
            for name, value in wanted:
                self.track_box.addItem(name, value)
        index = index_of_data(self.track_box, layer)
        if index >= 0:
            self.track_box.setCurrentIndex(index)

    # -- запись ------------------------------------------------------------------- #

    def _run(self, command) -> None:
        self._undo.run(command)
        self.document_edited.emit()
        self.refresh()

    def _commit(self, field: str, text: str) -> None:
        if self._syncing or self._eid is None:
            return
        event = self._doc.by_eid(self._eid)
        if field == "duration":
            try:
                seconds = float(text.strip().replace(",", "."))
            except ValueError:
                self.refresh()
                return
            end = event.start + max(1, round(seconds * 1000))
            if end != event.end:
                self._run(SetTiming(self._eid, end=end))
            return
        # parse_timecode возвращает None на мусор: возвращаем в поле прежнее
        # значение, чтобы оно не оставалось на экране как принятый ввод.
        value = parse_timecode(text)
        if value is None:
            self.refresh()
            return
        if field == "start" and value != event.start:
            self._run(SetTiming(self._eid, start=min(value, event.end - 1)))
        elif field == "end" and value != event.end:
            self._run(SetTiming(self._eid, end=max(value, event.start + 1)))

    def _step(self, field: str, direction: int) -> None:
        if self._eid is None:
            return
        event = self._doc.by_eid(self._eid)
        delta = round(self.frame_ms) * direction
        if field == "start":
            self._run(SetTiming(self._eid, start=min(event.start + delta, event.end - 1)))
        else:
            self._run(SetTiming(self._eid, end=max(event.end + delta, event.start + 1)))

    def _commit_style(self, name: str) -> None:
        if (not self._syncing and self._eid is not None and name
                and name != self._doc.by_eid(self._eid).style):
            self._run(SetStyle(self._eid, name))

    def _commit_actor(self) -> None:
        if self._syncing or self._eid is None:
            return
        name = self.actor_box.currentText().strip()
        if name == self._doc.by_eid(self._eid).name:
            return
        make = self._actor_command or AssignActor
        self._run(make([self._eid], name))

    def _commit_track(self, index: int) -> None:
        if self._syncing or self._eid is None or index < 0:
            return
        layer = self.track_box.itemData(index)
        if layer is not None and layer != self._doc.by_eid(self._eid).layer:
            self._run(MoveEventsToLayer([self._eid], int(layer)))

    def _commit_status(self, index: int) -> None:
        if self._syncing or self._eid is None:
            return
        status = _STATUSES[index]
        # Повторный щелчок по выбранной пометке снимает её: иначе вернуть
        # реплику в «без пометки» можно было бы только из контекстного меню.
        if self._doc.by_eid(self._eid).status == status:
            status = ""
        self._run(SetStatus([self._eid], status))

    def _on_note_typed(self) -> None:
        if not self._syncing:
            self._note_timer.start()

    def _commit_note(self) -> None:
        if self._eid is None or not self._doc.has(self._eid):
            return
        text = self.note.toPlainText().strip()
        if text != self._doc.by_eid(self._eid).note:
            self._undo.run(SetNote(self._eid, text))
            self.document_edited.emit()
