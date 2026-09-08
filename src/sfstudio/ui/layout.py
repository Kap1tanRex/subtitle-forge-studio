"""Раскладка окна: панели, пресеты, сохранение между запусками.

Панели сделаны ``QDockWidget``, а не ячейками ``QSplitter``. Разница
существенная: сплиттер даёт только перетаскивание границы, а док можно
оторвать в отдельное окно, переставить к другому краю, спрятать по одному и
собрать вкладками. Плюс Qt умеет сериализовать всю раскладку разом
(``saveState``), чего для сплиттеров пришлось бы писать руками.

Пресеты — не украшение. У трёх основных занятий разные требования к экрану:

* **Тайминг** — важна волна: таймлайн во всю ширину и повыше.
* **Перевод** — важен текст: широкая таблица и крупный редактор.
* **Оформление** — важен кадр: видео занимает почти всё, остальное по краям.

Переключение между ними одним пунктом меню избавляет от перетаскивания
границ по десять раз за сеанс.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import QDockWidget, QMainWindow, QWidget

__all__ = ["DockSpec", "LayoutManager", "LayoutPreset"]


class LayoutPreset(Enum):
    """Готовые раскладки под разные занятия."""

    DEFAULT = "default"
    TIMING = "timing"
    TRANSLATION = "translation"
    STYLING = "styling"

    @property
    def title(self) -> str:
        return {
            LayoutPreset.DEFAULT: "Обычная",
            LayoutPreset.TIMING: "Тайминг",
            LayoutPreset.TRANSLATION: "Перевод",
            LayoutPreset.STYLING: "Оформление",
        }[self]


@dataclass(frozen=True, slots=True)
class DockSpec:
    """Описание панели."""

    key: str
    title: str
    widget: QWidget
    area: Qt.DockWidgetArea
    #: Можно ли закрыть панель. Кадр закрывать бессмысленно — там идёт работа.
    closable: bool = True
    #: Начальный размер вдоль главной оси области.
    preferred: int = 0


class LayoutManager:
    """Создаёт панели, применяет пресеты, хранит раскладку в настройках."""

    def __init__(self, window: QMainWindow, settings: object | None = None) -> None:
        self._window = window
        self._settings = settings
        self._docks: dict[str, QDockWidget] = {}
        self._specs: dict[str, DockSpec] = {}

    # -- построение -------------------------------------------------------------- #

    def add(self, spec: DockSpec) -> QDockWidget:
        dock = QDockWidget(spec.title, self._window)
        dock.setObjectName(f"dock_{spec.key}")  # без имени saveState не работает
        dock.setWidget(spec.widget)

        features = QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
        if spec.closable:
            features |= QDockWidget.DockWidgetClosable
        dock.setFeatures(features)

        self._window.addDockWidget(spec.area, dock)
        self._docks[spec.key] = dock
        self._specs[spec.key] = spec
        return dock

    def dock(self, key: str) -> QDockWidget | None:
        return self._docks.get(key)

    def toggle_actions(self) -> list:
        """Готовые действия «показать/скрыть панель» для меню «Вид»."""
        actions = []
        for key in self._specs:
            dock = self._docks[key]
            action = dock.toggleViewAction()
            action.setText(self._specs[key].title)
            actions.append(action)
        return actions

    # -- пресеты ------------------------------------------------------------------ #

    def apply_preset(self, preset: LayoutPreset) -> None:
        """Переставляет панели под выбранное занятие."""
        window = self._window
        for dock in self._docks.values():
            dock.setFloating(False)
            dock.show()

        table = self._docks.get("table")
        editor = self._docks.get("editor")
        timeline = self._docks.get("timeline")
        qc = self._docks.get("qc")

        if qc is not None:
            qc.hide()  # панель проверок вызывается по F4, не мешает по умолчанию

        if preset is LayoutPreset.TIMING:
            self._place(timeline, Qt.BottomDockWidgetArea)
            self._place(table, Qt.RightDockWidgetArea)
            self._place(editor, Qt.RightDockWidgetArea)
            self._resize_vertical({timeline: 380})
            self._resize_horizontal({table: 380})

        elif preset is LayoutPreset.TRANSLATION:
            self._place(table, Qt.RightDockWidgetArea)
            self._place(editor, Qt.RightDockWidgetArea)
            self._place(timeline, Qt.BottomDockWidgetArea)
            self._resize_vertical({timeline: 150})
            self._resize_horizontal({table: 720})

        elif preset is LayoutPreset.STYLING:
            self._place(table, Qt.RightDockWidgetArea)
            self._place(editor, Qt.BottomDockWidgetArea)
            self._place(timeline, Qt.BottomDockWidgetArea)
            self._resize_vertical({timeline: 160, editor: 120})
            self._resize_horizontal({table: 300})

        else:  # DEFAULT
            self._place(table, Qt.RightDockWidgetArea)
            self._place(editor, Qt.RightDockWidgetArea)
            self._place(timeline, Qt.BottomDockWidgetArea)
            self._resize_vertical({timeline: 240})
            self._resize_horizontal({table: 560})

        if self._settings is not None:
            self._settings.set("ui.layout_preset", preset.value)
        _ = window

    def _place(self, dock: QDockWidget | None, area: Qt.DockWidgetArea) -> None:
        if dock is not None:
            self._window.addDockWidget(area, dock)

    def _resize_vertical(self, sizes: dict[QDockWidget | None, int]) -> None:
        docks = [d for d in sizes if d is not None]
        if docks:
            self._window.resizeDocks(docks, [sizes[d] for d in docks], Qt.Vertical)

    def _resize_horizontal(self, sizes: dict[QDockWidget | None, int]) -> None:
        docks = [d for d in sizes if d is not None]
        if docks:
            self._window.resizeDocks(docks, [sizes[d] for d in docks], Qt.Horizontal)

    # -- сохранение ---------------------------------------------------------------- #

    def save(self) -> None:
        """Складывает раскладку и геометрию окна в настройки."""
        if self._settings is None:
            return
        state = bytes(self._window.saveState().toBase64()).decode("ascii")
        geometry = bytes(self._window.saveGeometry().toBase64()).decode("ascii")
        self._settings.set("ui.layout_state", state)
        self._settings.set("ui.layout_geometry", geometry)

    def restore(self) -> bool:
        """Восстанавливает раскладку. ``False``, если сохранённой нет.

        Повреждённая или несовместимая строка не должна мешать запуску: если
        восстановление не удалось, окно просто откроется с пресетом по
        умолчанию.
        """
        if self._settings is None:
            return False
        state = self._settings.get("ui.layout_state")
        geometry = self._settings.get("ui.layout_geometry")
        if not state:
            return False
        try:
            if geometry:
                self._window.restoreGeometry(
                    QByteArray.fromBase64(geometry.encode("ascii"))
                )
            return bool(
                self._window.restoreState(QByteArray.fromBase64(state.encode("ascii")))
            )
        except (ValueError, TypeError):
            return False

    def reset(self) -> None:
        """Возвращает раскладку по умолчанию и забывает сохранённую."""
        if self._settings is not None:
            self._settings.set("ui.layout_state", None)
            self._settings.set("ui.layout_geometry", None)
        self.apply_preset(LayoutPreset.DEFAULT)
