"""Поиск значения в выпадающем списке.

``QComboBox.findData`` сравнивает данные как ``QVariant``, и для обычных
объектов Python это не сравнение по значению: кортеж ``(1280, 720)``, лежащий в
списке, находится не всегда, а ``Fraction(24000, 1001)`` не находится никогда.
Ошибка при этом **тихая** — метод возвращает ``-1``, ``setCurrentIndex(-1)``
снимает выбор, и список просто показывает не то, что просили.

Поэтому поиск идёт перебором с обычным ``==``. Элементов в наших списках
десятки, стоимость незаметна, а поведение предсказуемо для любого типа данных.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox

__all__ = ["index_of_data", "select_data"]


def index_of_data(box: QComboBox, value: object) -> int:
    """Индекс пункта с такими данными или ``-1``."""
    for index in range(box.count()):
        if box.itemData(index, Qt.UserRole) == value:
            return index
    return -1


def select_data(box: QComboBox, value: object) -> bool:
    """Выбирает пункт по данным. ``False``, если такого нет.

    Возвращаемое значение важно: молча оставить список на прежнем пункте —
    значит показать пользователю не его настройку, и он об этом не узнает.
    """
    index = index_of_data(box, value)
    if index < 0:
        return False
    box.setCurrentIndex(index)
    return True
