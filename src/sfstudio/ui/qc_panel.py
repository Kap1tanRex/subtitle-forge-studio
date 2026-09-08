"""Панель контроля качества: список находок с переходом к событию."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import format_ass
from sfstudio.services.qc import PROFILES, QcRunner, Severity
from sfstudio.ui.theme import DARK, Palette

__all__ = ["QcPanel"]

_SEVERITY_COLOR = {
    Severity.ERROR: "danger",
    Severity.WARNING: "warning",
    Severity.INFO: "text_muted",
}


class QcPanel(QWidget):
    """Список проблем. Двойной клик — переход к событию."""

    issue_activated = Signal(int)  # eid
    profile_changed = Signal(str)  # ключ профиля

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
        self._min_severity = Severity.INFO

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Профиль:"))
        self.profile_box = QComboBox()
        for key, profile in PROFILES.items():
            self.profile_box.addItem(profile.name, key)
        self.profile_box.currentIndexChanged.connect(self._on_profile)
        controls.addWidget(self.profile_box, 1)

        controls.addWidget(QLabel("Показывать:"))
        self.filter_box = QComboBox()
        self.filter_box.addItem("всё", Severity.INFO)
        self.filter_box.addItem("внимание и ошибки", Severity.WARNING)
        self.filter_box.addItem("только ошибки", Severity.ERROR)
        self.filter_box.currentIndexChanged.connect(self._on_filter)
        controls.addWidget(self.filter_box, 1)
        layout.addLayout(controls)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Время", "Правило", "Что не так"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 130)
        self.tree.itemActivated.connect(self._on_activated)
        self.tree.itemDoubleClicked.connect(self._on_activated)
        layout.addWidget(self.tree, 1)

        self.summary = QLabel("")
        self.summary.setProperty("role", "hint")
        layout.addWidget(self.summary)

    # -- обновление ------------------------------------------------------------- #


    def set_palette(self, palette: Palette) -> None:
        """Меняет тему на лету — см. :meth:`TimelineWidget.set_palette`."""
        self._palette = palette
        self.update()

    def set_document(self, doc: SubtitleDocument) -> None:
        self._doc = doc
        self.refresh()

    def refresh(self) -> None:
        """Перестраивает список целиком.

        Панель показывает десятки строк, а не тысячи (проблемных событий
        обычно немного), поэтому полная перестройка дешевле точечных правок
        и не даёт рассинхронизации.
        """
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        try:
            for issue in self._runner.all_issues():
                if issue.severity < self._min_severity:
                    continue
                event = self._doc.get(issue.eid)
                if event is None:
                    continue
                item = QTreeWidgetItem(
                    [format_ass(event.start), issue.rule, issue.message]
                )
                item.setData(0, Qt.UserRole, issue.eid)
                colour = getattr(self._palette, _SEVERITY_COLOR[issue.severity])
                item.setForeground(2, QColor(colour))
                item.setToolTip(2, event.plain or "(пусто)")
                self.tree.addTopLevelItem(item)
        finally:
            self.tree.setUpdatesEnabled(True)
        self.summary.setText(self._runner.summary())

    # -- события ---------------------------------------------------------------- #

    def _on_activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        eid = item.data(0, Qt.UserRole)
        if eid is not None:
            self.issue_activated.emit(int(eid))

    def _on_filter(self, index: int) -> None:
        self._min_severity = self.filter_box.itemData(index) or Severity.INFO
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
            if i.severity >= self._min_severity and self._doc.has(i.eid)
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
