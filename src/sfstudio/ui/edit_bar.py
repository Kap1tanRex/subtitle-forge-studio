"""Полоса правки под кадром: создать реплику, дублировать, удалить, дорожка.

Это те же команды, что в меню «Правка», но на виду. Работа с субтитрами —
это сотни однотипных действий, и лезть за каждым в меню третьего уровня или
вспоминать сочетание клавиш незачем.

Кнопки крупные и с подписями. В программах монтажа полоса инструментов
собрана из одних значков, и каждый раз приходится вспоминать, который из
них нужен, а потом ещё и попадать по нему мышью. Здесь подпись написана
рядом со значком, а кнопка ростом с палец: по ней промахнуться трудно.

Действия берутся из общего реестра, а не заводятся заново. Так кнопка,
пункт меню и горячая клавиша остаются одним и тем же действием: изменилось
сочетание в настройках — изменилась и подсказка на кнопке.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.ui.icons import make_icon
from sfstudio.ui.theme import DARK, Palette

__all__ = ["BUTTON_H", "EditBar"]

#: Высота кнопки. Меньше 32 px попадать мышью уже заметно тяжелее, а ниже
#: рекомендаций по размеру цели не опускаемся сознательно.
BUTTON_H = 34

#: Значок внутри кнопки. Крупнее, чем в транспорте: там кнопки идут плотным
#: рядом и узнаются по месту, здесь у каждой своя роль.
ICON_SIZE = 20


class EditBar(QWidget):
    """Кнопки правки под кадром.

    Заполняется после того, как собран реестр действий: окно строит виджеты
    раньше, чем команды, и пустая полоса на это время — нормальное состояние.
    """

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._buttons: dict[str, QToolButton] = {}

        # Своё оформление из темы: полоса должна читаться отдельной панелью,
        # а не пустым местом между кадром и таймлайном. Без
        # ``WA_StyledBackground`` обычный QWidget фон из таблицы стилей не
        # рисует — правило просто не сработает.
        self.setProperty("role", "editbar")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(10, 5, 10, 5)
        self._row.setSpacing(6)
        self._row.addStretch(1)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    # -- сборка ------------------------------------------------------------------ #

    def set_actions(self, actions: dict[str, QAction]) -> None:
        """Раскладывает кнопки по действиям из реестра.

        Пропущенные ключи не ошибка: плагин или урезанная сборка могут не
        дать какой-то команды, и полоса должна собраться из того, что есть.
        """
        self._clear()

        groups: tuple[tuple[tuple[str, str, str], ...], ...] = (
            (
                ("edit.insert", "event_add", tr('Реплика')),
                ("edit.duplicate", "event_duplicate", tr('Дублировать')),
                ("edit.delete", "event_delete", tr('Удалить')),
            ),
            (
                ("edit.add_track", "track_add", tr('Дорожка')),
            ),
        )

        first = True
        for group in groups:
            present = [item for item in group if item[0] in actions]
            if not present:
                continue
            if not first:
                self._row.insertWidget(self._row.count() - 1, self._separator())
            first = False
            for key, icon, caption in present:
                button = self._button(actions[key], icon, caption)
                self._buttons[key] = button
                self._row.insertWidget(self._row.count() - 1, button)

    def _button(self, action: QAction, icon: str, caption: str) -> QToolButton:
        button = QToolButton()
        # Действие приносит с собой обработчик, доступность и сочетание —
        # кнопке остаётся вид.
        button.setDefaultAction(action)
        button.setText(caption)
        button.setIcon(make_icon(icon, self._palette.text_primary, ICON_SIZE))
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setMinimumHeight(BUTTON_H)
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoRaise(True)
        button.setToolTip(_tip(action))
        button.setProperty("icon_name", icon)
        return button

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFixedWidth(1)
        line.setFixedHeight(BUTTON_H - 10)
        line.setStyleSheet(f"background: {self._palette.border}; border: none;")
        return line

    def _clear(self) -> None:
        self._buttons.clear()
        while self._row.count() > 1:
            item = self._row.takeAt(0)
            if (widget := item.widget()) is not None:
                widget.deleteLater()

    # -- тема --------------------------------------------------------------------- #

    def set_palette(self, palette: Palette) -> None:
        """Перерисовывает значки под новую тему.

        Значки рисуются кодом в цвет темы, то есть запечены в пиксели: сама
        по себе смена таблицы стилей их не перекрасит, и на светлой теме
        остался бы светлый значок на светлой кнопке.
        """
        self._palette = palette
        for button in self._buttons.values():
            name = button.property("icon_name")
            if name:
                button.setIcon(make_icon(str(name), palette.text_primary, ICON_SIZE))
        for index in range(self._row.count()):
            widget = self._row.itemAt(index).widget()
            if isinstance(widget, QFrame):
                widget.setStyleSheet(f"background: {palette.border}; border: none;")

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
