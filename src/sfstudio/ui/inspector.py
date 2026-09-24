"""Боковая колонка: пять вкладок, каждая — отдельная панель.

«Список», «Реплика», «Оформление», «Проверки», «Актёры» — одной стопкой
вкладок вместо пяти панелей, спорящих за правый край экрана. Порядок — порядок
работы: список реплик и их текст, потом свойства от частного к общему.

Колонка сама ничего не показывает и **не хранит состояния реплики**: это
только вкладки и место для панелей. Панели добавляет главное окно.

Любую вкладку можно **вынести в отдельное окно**: на двух мониторах держать
проверки и актёров раскрытыми одновременно удобнее, чем переключаться.
Закрытие вынесенного окна возвращает вкладку на прежнее место — потерять
панель насовсем нельзя.

Не все вкладки зависят от выделенной реплики: оформление и актёры работают и
без неё. Поэтому при пустом выделении гаснут только те вкладки, которым
нечего показать, а не вся колонка.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabBar,
    QTabWidget,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.ui.widgets import icon_button

__all__ = ["MIN_COLUMN_W", "Inspector", "PanelSlot"]


#: Ниже этой ширины колонка бесполезна: подписи вкладок уходят под
#: стрелки, а таблица показывает одну колонку из семи.
MIN_COLUMN_W = 240


def _scrollable(widget: QWidget) -> QWidget:
    """Заворачивает панель в прокручиваемую область, если ей это нужно.

    Зачем вообще. Колонку иначе нельзя ужать: ``QTabWidget`` берёт свою
    наименьшую ширину как максимум по **всем** вкладкам, включая закрытые, и
    одна широкая панель не давала сузить колонку до разумного. Область
    прокрутки разрывает эту связь.

    Кому не нужно. Таблица реплик и прочие наследники
    ``QAbstractScrollArea`` прокручиваются сами; панели, сами разложенные
    на прокручиваемые части, помечают себя свойством ``scrolls``.
    """
    if isinstance(widget, QAbstractScrollArea) or widget.property("scrolls"):
        return widget

    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(widget)
    return area


@dataclass(slots=True)
class PanelSlot:
    """Вкладка боковой колонки: чем она была и где стояла.

    Место запоминается, чтобы вынесенная и возвращённая вкладка встала туда
    же, где была. Иначе после каждого выноса порядок вкладок перемешивался бы.
    """

    key: str
    title: str
    #: Само содержимое панели.
    widget: QWidget
    #: То, что лежит во вкладке: прокручиваемая обёртка вокруг содержимого.
    holder: QWidget
    #: Гаснет ли вкладка, когда ничего не выделено.
    event_bound: bool
    #: Порядковый номер среди всех вкладок, включая вынесенные.
    order: int
    detached: bool = False
    #: Счётчик на вкладке: замечания проверок. Пусто — не показывать.
    badge: str = ""


class Inspector(QTabWidget):
    """Боковая колонка: вкладки-панели и кнопка «вынести в окно»."""

    #: Просят вынести вкладку с этим ключом в отдельное окно.
    detach_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._eid: int | None = None
        self._panels: dict[str, PanelSlot] = {}

        # Шапка колонки — приподнятая полоса с акцентной чертой сверху,
        # вкладки в ней лежат сегментами в углублении. Цвета — в теме.
        self.setObjectName("inspector")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setDocumentMode(True)
        bar = self.tabBar()
        bar.setExpanding(True)
        bar.setDrawBase(False)
        # Подписи целиком: «Ре…» и «Пр…» не различить, а выбирать приходится
        # именно по ним. Не влезли — уезжают под стрелки прокрутки.
        self.setElideMode(Qt.ElideNone)
        bar.setUsesScrollButtons(True)
        # Колонку должно быть можно ужать: у кого-то она справочная и стоит
        # узкой полосой, у кого-то в ней идёт вся работа.
        self.setMinimumWidth(MIN_COLUMN_W)

        self.detach_button = icon_button(
            "popout",
            tr('Вынести эту вкладку в отдельное окно.') + "\n"
            + tr('Закрытие окна вернёт её сюда'),
            size=15,
        )
        self.detach_button.clicked.connect(self._detach_current)
        # Кнопка в своей рамке с отступами: вплотную к краю она срезалась,
        # а вплотную к вкладкам читалась как ещё одна вкладка.
        corner = QWidget(self)
        corner_row = QHBoxLayout(corner)
        corner_row.setContentsMargins(4, 0, 6, 0)
        corner_row.addWidget(self.detach_button)
        self.setCornerWidget(corner, Qt.TopRightCorner)

    # -- вкладки как панели --------------------------------------------------- #

    def _detach_current(self) -> None:
        key = self.current_key()
        if key is not None:
            self.detach_requested.emit(key)

    def current_key(self) -> str | None:
        """Ключ открытой вкладки."""
        holder = self.currentWidget()
        for slot in self._panels.values():
            if slot.holder is holder:
                return slot.key
        return None

    def add_panel(
        self,
        key: str,
        title: str,
        widget: QWidget,
        *,
        event_bound: bool = False,
        at: int | None = None,
    ) -> None:
        """Добавляет вкладку. ``at`` — куда именно, иначе в конец."""
        order = len(self._panels) if at is None else at
        for slot in self._panels.values():
            if slot.order >= order:
                slot.order += 1

        slot = PanelSlot(key, title, widget, _scrollable(widget), event_bound, order)
        self._panels[key] = slot
        self._insert(slot)
        if event_bound:
            widget.setEnabled(self._eid is not None)

    def _insert(self, slot: PanelSlot) -> None:
        index = self.insertTab(self._place_for(slot), slot.holder, slot.title)
        self._paint_badge(slot, index)

    def set_badge(self, key: str, text: str) -> None:
        """Счётчик на вкладке. Пустая строка убирает его."""
        slot = self._panels.get(key)
        if slot is None or slot.badge == text:
            return
        slot.badge = text
        index = self.indexOf(slot.holder)
        if index >= 0:
            self._paint_badge(slot, index)

    def badge(self, key: str) -> str:
        slot = self._panels.get(key)
        return slot.badge if slot is not None else ""

    def _paint_badge(self, slot: PanelSlot, index: int) -> None:
        mark = None
        if slot.badge:
            mark = QLabel(slot.badge)
            mark.setProperty("role", "badge")
            mark.setAlignment(Qt.AlignCenter)
        self.tabBar().setTabButton(index, QTabBar.RightSide, mark)

    def panel_keys(self) -> list[str]:
        """Ключи всех панелей по порядку — вместе с вынесенными."""
        return [s.key for s in sorted(self._panels.values(), key=lambda s: s.order)]

    def panel_title(self, key: str) -> str:
        slot = self._panels.get(key)
        return slot.title if slot is not None else key

    def widget_for(self, key: str) -> QWidget | None:
        """Содержимое панели по ключу."""
        slot = self._panels.get(key)
        return slot.widget if slot is not None else None

    def holder_for(self, key: str) -> QWidget | None:
        """Обёртка панели — то, что лежит во вкладке или уехало в окно."""
        slot = self._panels.get(key)
        return slot.holder if slot is not None else None

    def is_detached(self, key: str) -> bool:
        slot = self._panels.get(key)
        return bool(slot is not None and slot.detached)

    def detached_keys(self) -> list[str]:
        return [s.key for s in self._panels.values() if s.detached]

    def take_panel(self, key: str) -> QWidget | None:
        """Убирает вкладку из колонки и отдаёт её обёртку.

        Возвращает ``None``, если такой вкладки нет или она уже вынесена:
        два окна с одним виджетом Qt не поддерживает.
        """
        slot = self._panels.get(key)
        if slot is None or slot.detached:
            return None
        index = self.indexOf(slot.holder)
        if index >= 0:
            self.removeTab(index)
        slot.detached = True
        return slot.holder

    def restore_panel(self, key: str) -> bool:
        """Возвращает вынесенную вкладку на её прежнее место."""
        slot = self._panels.get(key)
        if slot is None or not slot.detached:
            return False
        slot.detached = False
        self._insert(slot)
        self.setCurrentWidget(slot.holder)
        return True

    def _place_for(self, slot: PanelSlot) -> int:
        """Куда вставить вкладку: столько, сколько на месте соседей раньше неё.

        Вынесенные не занимают позиций, и слепая вставка по сохранённому
        индексу промахнулась бы мимо.
        """
        return sum(
            1 for other in self._panels.values()
            if not other.detached and other.order < slot.order
        )

    # -- выделение -------------------------------------------------------------- #

    def set_event(self, eid: int | None) -> None:
        """Гасит вкладки о реплике, когда ничего не выделено.

        Только их: оформление и актёры работают и без выделения, и гасить их
        заодно значило бы запирать половину колонки всякий раз, когда
        человек снял выделение.
        """
        self._eid = eid
        for slot in self._panels.values():
            if slot.event_bound:
                slot.widget.setEnabled(eid is not None)
