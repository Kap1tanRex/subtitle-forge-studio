"""Командная палитра: поиск по всем действиям приложения.

Реализуется поверх :class:`~sfstudio.ui.actions.ActionRegistry` практически
даром и заметно снижает нагрузку на меню: редкое действие проще вспомнить по
названию, чем найти в подменю третьего уровня.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.ui.actions import ActionRegistry, ActionSpec

__all__ = ["CommandPalette"]

VISIBLE_ROWS = 12


class CommandPalette(QDialog):
    """Список команд с поиском по названию."""

    def __init__(self, registry: ActionRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Команды")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.resize(640, 420)
        self._registry = registry
        self._chosen: ActionSpec | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Начните вводить название команды…")
        self.search.textChanged.connect(self._refresh)
        # Стрелки должны листать список, не двигая курсор в поле ввода:
        # руки остаются на клавиатуре, и палитра работает без мыши.
        self.search.installEventFilter(self)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.itemActivated.connect(self._accept_item)
        self.list.itemDoubleClicked.connect(self._accept_item)
        layout.addWidget(self.list, 1)

        self.hint = QLabel("↑↓ — выбор · Enter — выполнить · Esc — закрыть")
        self.hint.setProperty("role", "hint")
        layout.addWidget(self.hint)

        self._refresh("")
        self.search.setFocus()

    # -- список ------------------------------------------------------------------- #

    def _refresh(self, query: str) -> None:
        self.list.clear()
        for spec in self._registry.search(query):
            label = spec.title
            if spec.menu:
                label = f"{spec.menu}: {label}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, spec.key)
            if spec.shortcut:
                item.setText(
                    f"{label}\t{QKeySequence(spec.shortcut).toString(QKeySequence.NativeText)}"
                )
            action = self._registry.action(spec.key)
            if action is not None and not action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.list.addItem(item)

        if self.list.count():
            self._select_first_enabled()

    def _select_first_enabled(self) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).flags() & Qt.ItemIsEnabled:
                self.list.setCurrentRow(row)
                return
        self.list.setCurrentRow(0)

    # -- ввод ---------------------------------------------------------------------- #

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.search and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._step(1 if key == Qt.Key_Down else -1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._accept_item(self.list.currentItem())
                return True
        return super().eventFilter(watched, event)

    def _step(self, delta: int) -> None:
        count = self.list.count()
        if not count:
            return
        row = (self.list.currentRow() + delta) % count
        self.list.setCurrentRow(row)

    def _accept_item(self, item: QListWidgetItem | None) -> None:
        if item is None or not (item.flags() & Qt.ItemIsEnabled):
            return
        self._chosen = self._registry.spec(str(item.data(Qt.UserRole)))
        self.accept()

    def chosen(self) -> ActionSpec | None:
        """Выбранное действие, если пользователь его подтвердил."""
        return self._chosen
