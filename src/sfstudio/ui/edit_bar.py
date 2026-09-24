"""Шапка списка реплик: поиск, новая реплика, дублировать, удалить.

Это те же команды, что в меню «Правка», но на виду. Работа с субтитрами —
это сотни однотипных действий, и лезть за каждым в меню или вспоминать
сочетание клавиш незачем.

Главная кнопка одна и с подписью — «Реплика»: её жмут в десятки раз чаще
остальных. Дублировать и удалить — значками рядом: они нужны реже, и место в
узкой колонке нужнее полю поиска.

Действия берутся из общего реестра, а не заводятся заново. Так кнопка,
пункт меню и горячая клавиша остаются одним и тем же действием: изменилось
сочетание в настройках — изменилась и подсказка на кнопке.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QSizePolicy, QToolButton, QWidget

from sfstudio.app.i18n import tr
from sfstudio.ui.icons import make_icon
from sfstudio.ui.theme import DARK, Palette
from sfstudio.ui.widgets import recolor, set_icon

__all__ = ["BAR_H", "EditBar"]

#: Высота шапки — как у всех шапок колонки в макете.
BAR_H = 40

#: Кнопки шапки: ключ действия, значок, подпись. Подпись есть только у
#: главной, остальные — значками с подсказкой.
_BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("edit.insert", "event_add", tr('Реплика')),
    ("edit.duplicate", "event_duplicate", ""),
    ("edit.delete", "event_delete", ""),
)


class EditBar(QWidget):
    """Шапка списка.

    Заполняется после того, как собран реестр действий: окно строит виджеты
    раньше, чем команды, и пустая шапка на это время — нормальное состояние.
    """

    #: Искать реплику с этим текстом начиная со следующей за текущей.
    search_requested = Signal(str)

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._buttons: dict[str, QToolButton] = {}

        # Без ``WA_StyledBackground`` обычный QWidget фон из таблицы стилей
        # не рисует — правило просто не сработает.
        self.setProperty("role", "toolhead")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(BAR_H)

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(8, 0, 6, 0)
        self._row.setSpacing(4)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('Найти в репликах'))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(80)
        self.search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._search_icon = self.search.addAction(
            make_icon("search", palette.text_muted, 14), QLineEdit.LeadingPosition
        )
        self.search.setToolTip(tr('Enter — следующая реплика с этим текстом'))
        self.search.returnPressed.connect(
            lambda: self.search_requested.emit(self.search.text())
        )
        self._row.addWidget(self.search, 1)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    # -- сборка ------------------------------------------------------------------ #

    def set_actions(self, actions: dict[str, QAction]) -> None:
        """Раскладывает кнопки по действиям из реестра.

        Пропущенные ключи не ошибка: плагин или урезанная сборка могут не
        дать какой-то команды, и шапка должна собраться из того, что есть.
        """
        for button in self._buttons.values():
            self._row.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()

        for key, icon, caption in _BUTTONS:
            if key in actions:
                button = self._button(actions[key], icon, caption)
                self._buttons[key] = button
                self._row.addWidget(button)

    def _button(self, action: QAction, icon: str, caption: str) -> QToolButton:
        button = QToolButton()
        # Действие приносит с собой обработчик, доступность и сочетание —
        # кнопке остаётся вид.
        button.setDefaultAction(action)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(_tip(action))
        if caption:
            button.setText(caption)
            button.setProperty("role", "outlined")
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setFixedHeight(28)
            set_icon(button, icon, "text", 16, self._palette)
        else:
            button.setProperty("role", "icon")
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setFixedSize(28, 28)
            set_icon(button, icon, "muted", 16, self._palette)
        button.setAccessibleName(caption or action.text())
        return button

    # -- тема --------------------------------------------------------------------- #

    def set_palette(self, palette: Palette) -> None:
        """Перерисовывает значки под новую тему.

        Значки рисуются кодом в цвет темы, то есть запечены в пиксели: сама
        по себе смена таблицы стилей их не перекрасит.
        """
        self._palette = palette
        recolor(self, palette)
        self._search_icon.setIcon(make_icon("search", palette.text_muted, 14))

    # -- для тестов и окна --------------------------------------------------------- #

    def button(self, key: str) -> QToolButton | None:
        return self._buttons.get(key)

    def keys(self) -> list[str]:
        return list(self._buttons)


def _tip(action: QAction) -> str:
    """Подсказка с сочетанием клавиш, если оно назначено."""
    shortcut = action.shortcut().toString()
    title = action.text()
    return f"{title} ({shortcut})" if shortcut else title
