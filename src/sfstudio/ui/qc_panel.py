"""Вкладка «Проверки»: замечания текущей реплики, всего файла и вопросы.

Сверху — то, на что человек смотрит прямо сейчас: выделенная реплика. Ниже —
весь файл, по строке на находку. Внизу — пометки «вопрос»: это не ошибки
проверок, а то, что человек сам отложил, но разбирают их в тот же проход.

Двойной щелчок (или Enter) по находке ведёт к реплике.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import format_short
from sfstudio.services.qc import PROFILES, QcRunner, Severity
from sfstudio.ui.icons import make_icon, paint_icon
from sfstudio.ui.theme import DARK, MONO_FONT, Palette
from sfstudio.ui.widgets import Segmented, hbox, kbd_button, label, section, vbox

__all__ = ["QcPanel"]

#: Положения фильтра и какие серьёзности они показывают.
_FILTERS: tuple[frozenset[Severity], ...] = (
    frozenset(Severity),
    frozenset({Severity.ERROR}),
    frozenset({Severity.WARNING}),
)

_ROLE_ISSUE = Qt.UserRole + 1
ROW_H = 46


class _IssueDelegate(QStyledItemDelegate):
    """Строка находки в две строки: значок, правило и место — сверху,
    что не так — ниже приглушённо. Рисуется, а не собирается виджетами:
    на плохом файле находок тысячи."""

    def __init__(self, panel: QcPanel) -> None:
        super().__init__(panel)
        self._panel = panel

    def sizeHint(self, _option, _index) -> QSize:  # noqa: N802
        return QSize(0, ROW_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        data = index.data(_ROLE_ISSUE)
        if not data:
            return
        severity, rule, place, message = data
        p = self._panel.palette_colors
        painter.save()
        rect = option.rect
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, QColor(p.accent_muted))
        painter.setPen(QColor(p.line))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        tone = p.danger_text if severity == Severity.ERROR else (
            p.warning_text if severity == Severity.WARNING else p.text_muted)
        paint_icon(painter, "error" if severity == Severity.ERROR else "warning", tone,
                   QRect(rect.left() + 10, rect.top() + 8, 15, 15))

        left = rect.left() + 34
        width = rect.width() - 44
        font = QFont(option.font)
        mono = QFont(MONO_FONT.split(",")[0].strip('"'))
        mono.setPixelSize(12)
        painter.setFont(mono)
        painter.setPen(QColor(p.text_muted))
        top_line = QRect(left, rect.top() + 6, width, 18)
        painter.drawText(top_line, Qt.AlignRight | Qt.AlignVCenter, place)
        place_w = painter.fontMetrics().horizontalAdvance(place) + 12

        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(p.text_primary))
        painter.drawText(top_line.adjusted(0, 0, -place_w, 0), Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(rule, Qt.ElideRight,
                                                          width - place_w))
        small = QFont(option.font)
        small.setPixelSize(12)
        painter.setFont(small)
        painter.setPen(QColor(p.text_muted))
        painter.drawText(QRect(left, rect.top() + 24, width, 16), Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(message, Qt.ElideRight, width))
        painter.restore()


class QcPanel(QWidget):
    """Замечания проверок. Двойной щелчок — переход к событию."""

    issue_activated = Signal(int)  # eid
    profile_changed = Signal(str)  # ключ профиля
    report_requested = Signal()
    next_requested = Signal()

    def __init__(
        self,
        runner: QcRunner,
        doc: SubtitleDocument,
        palette: Palette = DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._doc = doc
        self._palette = palette
        self._filter = _FILTERS[0]
        self._eid: int | None = None

        self.profile_box = QComboBox()
        for key, profile in PROFILES.items():
            self.profile_box.addItem(profile.name, key)
        self.profile_box.setToolTip(tr('Профиль проверок'))
        self.profile_box.currentIndexChanged.connect(self._on_profile)
        report = kbd_button(tr('Отчёт'))
        report.clicked.connect(self.report_requested)
        following = kbd_button(tr('Следующая'), "F9")
        following.clicked.connect(self.next_requested)
        self.filter = Segmented(["", "", ""])
        self.filter.set_index(0)
        self.filter.changed.connect(self._on_filter)

        # -- реплика --------------------------------------------------------- #
        self.cue_title = section("")
        self._cue_box = QVBoxLayout()
        self._cue_box.setSpacing(6)
        cue = vbox(self.cue_title, self._cue_box, spacing=6)

        # -- весь файл ------------------------------------------------------- #
        self.summary = label("", "hint")
        self.list = QListWidget()
        self.list.setItemDelegate(_IssueDelegate(self))
        self.list.setUniformItemSizes(True)
        self.list.setMinimumHeight(ROW_H * 3)
        self.list.itemActivated.connect(self._on_activated)
        self.list.itemDoubleClicked.connect(self._on_activated)
        whole = vbox(hbox(section(tr('Весь файл')), None, self.summary), self.list,
                     spacing=6)

        # -- вопросы --------------------------------------------------------- #
        self.questions = QListWidget()
        self.questions.setMaximumHeight(120)
        self.questions.itemActivated.connect(self._on_activated)
        self.questions.itemDoubleClicked.connect(self._on_activated)
        asked = vbox(hbox(section(tr('Пометки «вопрос»')), None, label("Shift+F9", "kbd")),
                     self.questions, spacing=6)

        column = vbox(
            vbox(hbox(self.profile_box, report, following, spacing=8), self.filter,
                 spacing=8),
            cue, whole, asked,
            spacing=16, margins=(16, 12, 16, 12),
        )
        self.setLayout(column)
        column.setStretchFactor(whole, 1)

    # -- обновление ------------------------------------------------------------- #

    @property
    def palette_colors(self) -> Palette:
        return self._palette

    def set_palette(self, palette: Palette) -> None:
        """Меняет тему на лету — см. :meth:`TimelineWidget.set_palette`."""
        self._palette = palette
        self.refresh()

    def set_document(self, doc: SubtitleDocument) -> None:
        self._doc = doc
        self._eid = None
        self.refresh()

    def set_event(self, eid: int | None) -> None:
        """Выделенная реплика — её замечания показываются сверху."""
        self._eid = eid if eid is not None and self._doc.has(eid) else None
        self._refresh_cue()

    def refresh(self) -> None:
        """Перестраивает списки целиком.

        Находок обычно десятки, а не тысячи, поэтому полная перестройка
        дешевле точечных правок и не даёт рассинхронизации.
        """
        issues = [i for i in self._runner.all_issues() if self._doc.has(i.eid)]
        counts = {level: sum(1 for i in issues if i.severity == level) for level in Severity}
        self.filter.set_text(0, tr('Все · {0}').format(len(issues)))
        self.filter.set_text(1, tr('Ошибки · {0}').format(counts[Severity.ERROR]))
        self.filter.set_text(2, tr('Предупреждения · {0}').format(counts[Severity.WARNING]))
        self.summary.setText(tr('{0} реплик').format(len(self._doc.events)))

        positions = {e.eid: n for n, e in enumerate(self._doc.events)}
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        try:
            for issue in issues:
                if issue.severity not in self._filter:
                    continue
                event = self._doc.by_eid(issue.eid)
                item = QListWidgetItem()
                item.setData(Qt.UserRole, issue.eid)
                place = f"#{positions.get(issue.eid, 0) + 1} · {format_short(event.start)}"
                item.setData(_ROLE_ISSUE, (issue.severity, issue.rule, place, issue.message))
                item.setToolTip(event.plain or tr('(пусто)'))
                self.list.addItem(item)
        finally:
            self.list.setUpdatesEnabled(True)

        from sfstudio.core.workflow import questions

        self.questions.clear()
        for eid in questions(self._doc.events):
            event = self._doc.by_eid(eid)
            text = event.plain.replace("\n", " ") or tr('(пусто)')
            item = QListWidgetItem(
                f"{text}   ·   #{positions.get(eid, 0) + 1} · {format_short(event.start)}"
            )
            item.setData(Qt.UserRole, eid)
            if event.note:
                item.setToolTip(event.note)
            self.questions.addItem(item)
        self.questions.setVisible(self.questions.count() > 0)
        self._refresh_cue()

    def _refresh_cue(self) -> None:
        while self._cue_box.count():
            widget = self._cue_box.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        if self._eid is None:
            self.cue_title.setText(tr('Реплика не выбрана'))
            return
        self.cue_title.setText(tr('Реплика #{0}').format(self._doc.index_of(self._eid) + 1))
        found = self._runner.issues_for(self._eid)
        for issue in found:
            box = QFrame()
            box.setProperty("role", "warning-box")
            text = label(issue.message)
            text.setWordWrap(True)
            error = issue.severity == Severity.ERROR
            mark = label()
            mark.setPixmap(make_icon(
                "error" if error else "warning",
                self._palette.danger_text if error else self._palette.warning_text, 15,
            ).pixmap(15, 15))
            mark.setAlignment(Qt.AlignTop)
            box.setLayout(hbox(mark, text, margins=(10, 8, 10, 8)))
            self._cue_box.addWidget(box)
        note = label(tr('Замечаний нет') if not found else tr('Остальные правила без замечаний'),
                     "hint")
        self._cue_box.addWidget(note)

    # -- события ---------------------------------------------------------------- #

    def _on_activated(self, item: QListWidgetItem) -> None:
        eid = item.data(Qt.UserRole)
        if eid is not None:
            self.issue_activated.emit(int(eid))

    def _on_filter(self, index: int) -> None:
        self._filter = _FILTERS[index]
        self.refresh()

    def _on_profile(self, index: int) -> None:
        key = self.profile_box.itemData(index)
        if key:
            self.profile_changed.emit(str(key))

    def select_profile(self, key: str) -> None:
        position = self.profile_box.findData(key)
        if position >= 0:
            self.profile_box.setCurrentIndex(position)

    def go_to_next(self, after_eid: int | None) -> int | None:
        """Следующая проблема после указанного события — для F9."""
        issues = [
            i for i in self._runner.all_issues()
            if i.severity in self._filter and self._doc.has(i.eid)
        ]
        if not issues:
            return None
        ordered = sorted(issues, key=lambda i: (self._doc.by_eid(i.eid).start, i.eid))
        if after_eid is None:
            return ordered[0].eid
        current_start = self._doc.by_eid(after_eid).start if self._doc.has(after_eid) else -1
        for issue in ordered:
            event = self._doc.by_eid(issue.eid)
            if (event.start, issue.eid) > (current_start, after_eid):
                return issue.eid
        return ordered[0].eid  # по кругу
