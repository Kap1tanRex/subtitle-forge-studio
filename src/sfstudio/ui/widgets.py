"""Мелкие детали интерфейса, общие для панелей: значки, сегменты, подписи.

Цвет задаёт таблица стилей по свойству ``role`` (словарь ролей — в
:func:`sfstudio.ui.theme.build_qss`), здесь только сборка. Значки запечены в
пиксели, поэтому кнопка помнит имя значка и его роль, а :func:`recolor`
перекрашивает их все разом при смене темы.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sfstudio.ui.icons import make_icon
from sfstudio.ui.theme import DARK, Palette

__all__ = [
    "Segmented", "caption", "hbox", "icon_button", "kbd_button", "label",
    "recolor", "section", "set_icon", "vbox",
]

#: Какой цвет палитры берёт значок в зависимости от его роли.
_TONES = {
    "text": "text_primary",
    "muted": "text_muted",
    "accent": "accent_text",
    "on_accent": "on_accent",
    "warning": "warning_text",
    "danger": "danger_text",
    "success": "success",
}


def set_icon(button: QAbstractButton, name: str, tone: str = "text",
             size: int = 16, palette: Palette = DARK) -> None:
    """Ставит значок и запоминает, как его перекрасить."""
    button.setProperty("icon_name", name)
    button.setProperty("icon_tone", tone)
    button.setProperty("icon_size", size)
    button.setIcon(make_icon(name, getattr(palette, _TONES[tone]), size))
    button.setIconSize(QSize(size, size))


def recolor(root: QWidget, palette: Palette) -> None:
    """Перерисовывает все значки под ``root`` цветами новой темы."""
    for button in root.findChildren(QAbstractButton):
        name = button.property("icon_name")
        if name:
            tone = str(button.property("icon_tone") or "text")
            size = int(button.property("icon_size") or 16)
            button.setIcon(make_icon(str(name), getattr(palette, _TONES[tone]), size))


def icon_button(name: str, tip: str, *, tone: str = "muted", size: int = 16,
                side: int = 28, checkable: bool = False) -> QToolButton:
    """Кнопка-значок без рамки. Подпись — в подсказке и для экранного диктора."""
    button = QToolButton()
    set_icon(button, name, tone, size)
    button.setProperty("role", "icon")
    button.setToolTip(tip)
    button.setAccessibleName(tip)
    button.setFixedSize(side, side)
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    button.setCursor(Qt.PointingHandCursor)
    return button


class _LaidOutButton(QPushButton):
    """Кнопка, размер которой считается по разложенным в ней подписям.

    Обычная кнопка о своей раскладке не знает и меряет только собственный
    текст — а его нет, и подписи обрезались.
    """

    def sizeHint(self):  # noqa: N802
        hint = self.layout().sizeHint()
        hint.setHeight(max(hint.height(), 28))
        return hint

    def minimumSizeHint(self):  # noqa: N802
        return self.sizeHint()


def kbd_button(text: str, keys: str = "") -> QPushButton:
    """Кнопка с сочетанием клавиш приглушённым шрифтом справа от подписи."""
    button = _LaidOutButton()
    row = QHBoxLayout(button)
    row.setContentsMargins(10, 0, 10, 0)
    row.setSpacing(8)
    title = QLabel(text)
    row.addWidget(title)
    if keys:
        row.addWidget(label(keys, "kbd"))
    for child in button.findChildren(QLabel):
        child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    button.setAccessibleName(text)
    button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return button


def label(text: str = "", role: str = "") -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    return widget


def section(text: str) -> QLabel:
    """Заголовок раздела внутри вкладки."""
    return label(text, "section")


def caption(text: str) -> QLabel:
    """Подпись над полем — мелко и приглушённо."""
    return label(text, "caption")


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    """Строка из виджетов; ``None`` — растяжка, число — отступ."""
    row = QHBoxLayout()
    row.setContentsMargins(*margins)
    row.setSpacing(spacing)
    for item in items:
        if item is None:
            row.addStretch(1)
        elif isinstance(item, int):
            row.addSpacing(item)
        elif isinstance(item, QWidget):
            row.addWidget(item)
        else:
            row.addLayout(item)
    return row


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    column = QVBoxLayout()
    column.setContentsMargins(*margins)
    column.setSpacing(spacing)
    for item in items:
        if item is None:
            column.addStretch(1)
        elif isinstance(item, QWidget):
            column.addWidget(item)
        else:
            column.addLayout(item)
    return column


class Segmented(QFrame):
    """Переключатель из нескольких положений в углублении — как в макете.

    Кнопки растягиваются поровну: подписи разной длины, а ширина сегмента
    должна говорить «это равноправные варианты», а не «этот важнее».
    """

    #: Выбранное положение (номер). Только от действий человека.
    changed = Signal(int)

    def __init__(self, labels: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "segment")
        self.setFixedHeight(30)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for index, text in enumerate(labels):
            button = QToolButton()
            button.setText(text)
            button.setProperty("role", "seg")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            button.setCursor(Qt.PointingHandCursor)
            self._group.addButton(button, index)
            row.addWidget(button)
        self._group.idClicked.connect(self.changed)

    def button(self, index: int) -> QToolButton:
        return self._group.button(index)

    def index(self) -> int:
        return self._group.checkedId()

    def set_index(self, index: int) -> None:
        """Ставит положение, не сообщая об этом: так показывают состояние."""
        button = self._group.button(index)
        if button is not None:
            button.setChecked(True)
        elif (checked := self._group.checkedButton()) is not None:
            # «Ничего не выбрано» у исключающей группы иначе не показать.
            self._group.setExclusive(False)
            checked.setChecked(False)
            self._group.setExclusive(True)

    def set_text(self, index: int, text: str) -> None:
        button = self._group.button(index)
        if button is not None:
            button.setText(text)
