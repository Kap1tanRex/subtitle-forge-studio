"""Выбор шрифта с образцом при наведении.

Названия шрифтов почти ничего не говорят о том, как они выглядят, а
кириллицу поддерживают далеко не все: половина установленных гарнитур на
русском тексте покажет прямоугольники вместо букв. Узнать это, применив шрифт
ко всему файлу субтитров, — плохой способ.

Поэтому при наведении на пункт списка появляется подсказка с двумя строками
образца: русской и английской, набранными этим самым шрифтом. Два языка, а не
один: латиница есть везде, и по ней невозможно понять, есть ли кириллица.

Подсказка ставится **на элемент списка**, а не на весь виджет: у выпадающего
списка своё окно, и подсказка родителя в нём не появляется.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QFontComboBox, QToolTip, QWidget

from sfstudio.app.i18n import tr

__all__ = ["SAMPLE_EN", "SAMPLE_RU", "FontComboBox", "font_sample_html"]

#: Панграммы: в каждой встречаются почти все буквы алфавита, поэтому по ним
#: видно и начертание, и наличие нужных знаков.
SAMPLE_RU = tr('Съешь ещё этих мягких французских булок, да выпей чаю.')
SAMPLE_EN = "The quick brown fox jumps over the lazy dog."


def font_sample_html(family: str, size: int = 15) -> str:
    """Подсказка с образцом текста выбранным шрифтом.

    HTML, а не простой текст: только так подсказка Qt может показать сам
    образец нужной гарнитурой — ради этого всё и затевается.

    О поддержке кириллицы сообщается отдельной строкой. Молча показать
    прямоугольники значило бы оставить пользователя гадать, шрифт ли это
    такой или сломался редактор.
    """
    safe = _escape(family)
    supports_ru = _supports_cyrillic(family)
    note = (
        ""
        if supports_ru
        else tr('<div style="color:#D29922">Нет кириллицы — русский текст покажется '
               'прямоугольниками</div>')
    )
    return (
        f'<div style="font-weight:600">{safe}</div>'
        f'<div style="font-family:\'{safe}\'; font-size:{size}pt">{_escape(SAMPLE_RU)}</div>'
        f'<div style="font-family:\'{safe}\'; font-size:{size}pt">{_escape(SAMPLE_EN)}</div>'
        f"{note}"
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _supports_cyrillic(family: str) -> bool:
    """Есть ли в шрифте кириллица.

    Спрашиваем систему о наборе письменностей, а не пытаемся отрисовать
    строку и сравнить: отрисовка сработала бы и на подменённом шрифте,
    который система подставляет вместо отсутствующих знаков.
    """
    try:
        systems = QFontDatabase.writingSystems(family)
    except (TypeError, AttributeError):  # pragma: no cover - разные версии Qt
        return True
    return QFontDatabase.Cyrillic in systems


class FontComboBox(QFontComboBox):
    """Список шрифтов, показывающий образец при наведении."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        view = self.view()
        view.setMouseTracking(True)
        # Событие наведения приходит окну списка, а не самому полю: у
        # выпадающего списка отдельное окно верхнего уровня.
        view.viewport().installEventFilter(self)
        self._last_row = -1

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.view().viewport() and event.type() == QEvent.MouseMove:
            index = self.view().indexAt(event.position().toPoint())
            if index.isValid() and index.row() != self._last_row:
                self._last_row = index.row()
                family = index.data(Qt.DisplayRole)
                if family:
                    QToolTip.showText(
                        event.globalPosition().toPoint(),
                        font_sample_html(str(family)),
                        self.view().viewport(),
                    )
        elif watched is self.view().viewport() and event.type() == QEvent.Leave:
            self._last_row = -1
            QToolTip.hideText()
        return super().eventFilter(watched, event)

    def set_family(self, family: str) -> None:
        """Выбирает семейство по имени, не поднимая сигналов."""
        blocked = self.blockSignals(True)
        try:
            self.setCurrentFont(QFont(family))
        finally:
            self.blockSignals(blocked)
