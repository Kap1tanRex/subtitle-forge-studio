"""Вынесенная вкладка: отдельное окно, из которого она возвращается назад.

Зачем это нужно. Боковая колонка экономит место, но за это платит
переключением: чтобы посмотреть замечания, надо уйти со свойств реплики. На
двух мониторах платить незачем — панель уезжает в своё окно и живёт там.

Почему ``QDockWidget``, а не обычное окно. Док умеет и плавать, и
пристыковаться обратно к любому краю: человек может вынести проверки в
плавающее окно, а потом прижать их к низу — и обе возможности достаются
даром. Обычное окно дало бы только первую.

**Закрытие возвращает вкладку на место.** Иначе панель просто исчезала бы, и
единственным способом её вернуть был бы перезапуск: во вкладках её уже нет, а
в меню «Вид» — только доки, заведённые при старте.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDockWidget, QMainWindow, QWidget

__all__ = ["DetachedPanel"]


class DetachedPanel(QDockWidget):
    """Окно с вынесенной вкладкой."""

    #: Окно закрыли — вкладку с этим ключом надо вернуть в колонку.
    returning = Signal(str)

    def __init__(self, key: str, title: str, widget: QWidget, parent: QMainWindow) -> None:
        super().__init__(title, parent)
        self._key = key
        # Имя объекта нужно saveState: без него Qt не сможет восстановить
        # раскладку, в которой это окно участвовало.
        self.setObjectName(f"detached_{key}")
        self.setWidget(widget)
        self.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self.setAllowedAreas(Qt.AllDockWidgetAreas)

    @property
    def key(self) -> str:
        return self._key

    def take_widget(self) -> QWidget | None:
        """Отдаёт виджет наружу, не удаляя его вместе с окном.

        Без этого закрытие окна утащило бы панель за собой: у виджета
        родителем остаётся док, и его уничтожение забирает и содержимое.
        """
        widget = self.widget()
        if widget is not None:
            self.setWidget(None)
            widget.setParent(None)
        return widget

    def closeEvent(self, event) -> None:  # noqa: N802
        self.returning.emit(self._key)
        super().closeEvent(event)
