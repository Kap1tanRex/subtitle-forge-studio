"""Подчёркивание опечаток в поле правки и меню замен.

Подсветка вешается на поле как подсветчик синтаксиса: Qt сам зовёт его для
изменившихся строк, и своего кода на отслеживание правок не нужно.

Замены ищутся **только по правому щелчку**. Поиск подсказки стоит от 150 до
300 миллисекунд на слово — на каждое нажатие клавиши этого делать нельзя, и
никакая программа так не делает.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QMenu, QPlainTextEdit

from sfstudio.app.i18n import tr
from sfstudio.services.spelling import SpellChecker

__all__ = ["SpellHighlighter", "attach_spellcheck"]


class SpellHighlighter(QSyntaxHighlighter):
    """Подчёркивает незнакомые слова волнистой линией."""

    def __init__(self, document, checker: SpellChecker, colour: str = "#F85149") -> None:
        super().__init__(document)
        self._checker = checker
        self._format = QTextCharFormat()
        self._format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        self._format.setUnderlineColor(QColor(colour))

    def set_colour(self, colour: str) -> None:
        self._format.setUnderlineColor(QColor(colour))
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        if not self._checker.enabled:
            return
        for found in self._checker.check(text):
            self.setFormat(found.start, found.end - found.start, self._format)


def attach_spellcheck(editor: QPlainTextEdit, checker: SpellChecker,
                      colour: str = "#F85149") -> SpellHighlighter:
    """Включает проверку в поле: подчёркивание плюс замены по правому щелчку.

    Меню строится поверх обычного меню поля, а не вместо него: «Отменить»,
    «Вставить» и «Выделить всё» человеку нужны не меньше замен.
    """
    highlighter = SpellHighlighter(editor.document(), checker, colour)

    def menu(point) -> None:
        built = editor.createStandardContextMenu()
        word, cursor = _word_at(editor, point)
        if word and checker.enabled and not checker.known(word):
            _prepend_suggestions(built, editor, checker, word, cursor, highlighter)
        built.exec(editor.mapToGlobal(point))

    editor.setContextMenuPolicy(editor.contextMenuPolicy().CustomContextMenu)
    editor.customContextMenuRequested.connect(menu)
    return highlighter


def _word_at(editor: QPlainTextEdit, point):
    """Слово под курсором мыши и курсор, который его выделяет."""
    cursor = editor.cursorForPosition(point)
    cursor.select(cursor.SelectionType.WordUnderCursor)
    return cursor.selectedText().strip(), cursor


def _prepend_suggestions(menu: QMenu, editor, checker, word, cursor,
                         highlighter) -> None:
    """Ставит замены в начало меню — туда, где их ищут."""
    first = menu.actions()[0] if menu.actions() else None
    header: list[QAction] = []

    for candidate in checker.suggest(word):
        action = QAction(candidate, menu)
        action.triggered.connect(
            lambda _=False, text=candidate, place=cursor: _replace(editor, place, text)
        )
        header.append(action)

    if not header:
        empty = QAction(tr('Замен не нашлось'), menu)
        empty.setEnabled(False)
        header.append(empty)

    remember = QAction(tr('Добавить «{0}» в словарь').format(word), menu)
    remember.triggered.connect(
        lambda: (checker.add_word(word), highlighter.rehighlight())
    )
    header.append(remember)

    separator = QAction(menu)
    separator.setSeparator(True)
    header.append(separator)

    for action in header:
        menu.insertAction(first, action)


def _replace(editor: QPlainTextEdit, cursor, text: str) -> None:
    cursor.insertText(text)
    editor.setFocus()
