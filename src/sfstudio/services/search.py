"""Поиск и замена по тексту реплик.

Логика отделена от окна: искать нужно и из окна, и из плагина, и из
консольного сценария массовой правки, а Qt для этого не требуется.

Две тонкости, из-за которых поиск по субтитрам не сводится к ``str.find``.

**Разметка — часть текста, но не часть речи.** В реплике
``{\\i1}Привет{\\i0}`` слово «Привет» одно, а строка содержит ещё и теги. Ищет
человек обычно по речи, поэтому совпадения, попавшие внутрь фигурных скобок,
по умолчанию отбрасываются: иначе поиск «i1» находил бы курсив, а замена
«1»→«2» ломала бы разметку по всему файлу.

**Замена идёт через команды.** Массовая замена — это правка сотен реплик, и
она обязана отменяться одним Ctrl+Z, а не тремястами. Поэтому функции здесь
только **находят и считают**, а изменение документа собирает вызывающий из
готовых команд: разделение позволяет показать результат до применения.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent

__all__ = [
    "Match",
    "SearchQuery",
    "SearchScope",
    "count_matches",
    "find_all",
    "replacements",
]


class SearchScope(StrEnum):
    """Где искать."""

    ALL = "all"
    SELECTION = "selection"
    FROM_CURSOR = "from_cursor"

    @property
    def title(self) -> str:
        return {
            SearchScope.ALL: "Везде",
            SearchScope.SELECTION: "В выделенных",
            SearchScope.FROM_CURSOR: "От текущей реплики",
        }[self]


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Что и как ищем."""

    text: str
    regex: bool = False
    case_sensitive: bool = False
    whole_word: bool = False
    #: Искать и внутри фигурных скобок. По умолчанию нет: там разметка, а не речь.
    include_tags: bool = False
    scope: SearchScope = SearchScope.ALL

    def compile(self) -> re.Pattern[str] | None:
        """Готовое выражение. ``None`` — искать нечего или образец неверен."""
        if not self.text:
            return None
        pattern = self.text if self.regex else re.escape(self.text)
        if self.whole_word:
            # \b вокруг экранированного образца работает и для кириллицы:
            # в Python \w по умолчанию юникодный.
            pattern = rf"\b{pattern}\b"
        flags = 0 if self.case_sensitive else re.IGNORECASE
        try:
            return re.compile(pattern, flags)
        except re.error:
            # Незаконченное регулярное выражение — обычное состояние поля,
            # пока его набирают. Это не ошибка, а «пока искать нечего».
            return None


@dataclass(frozen=True, slots=True)
class Match:
    """Одно совпадение внутри реплики."""

    eid: int
    #: Границы в строке ``event.text`` — именно в ней, а не в видимом тексте:
    #: по ним делается замена.
    start: int
    end: int
    #: Строка целиком — чтобы показать находку, не обращаясь к документу снова.
    line: str

    @property
    def fragment(self) -> str:
        return self.line[self.start : self.end]

    def preview(self, width: int = 40) -> str:
        """Совпадение с окружением — для списка находок."""
        left = max(0, self.start - width // 2)
        right = min(len(self.line), self.end + width // 2)
        prefix = "…" if left > 0 else ""
        suffix = "…" if right < len(self.line) else ""
        return prefix + self.line[left:right].replace("\n", " ") + suffix


def _tag_spans(text: str) -> list[tuple[int, int]]:
    """Границы блоков разметки ``{...}``.

    Вложенных скобок в ASS не бывает, а незакрытая скобка встречается в
    испорченных файлах — она считается тянущейся до конца строки, как её и
    трактует libass.
    """
    spans: list[tuple[int, int]] = []
    start = text.find("{")
    while start >= 0:
        end = text.find("}", start + 1)
        if end < 0:
            spans.append((start, len(text)))
            break
        spans.append((start, end + 1))
        start = text.find("{", end + 1)
    return spans


def _inside_tags(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(s < end and start < e for s, e in spans)


def _events_in_scope(
    doc: SubtitleDocument,
    query: SearchQuery,
    selection: Iterable[int] | None,
    cursor_eid: int | None,
) -> list[SubtitleEvent]:
    """Реплики, по которым идёт поиск, в порядке документа."""
    events = list(doc.events)
    if query.scope is SearchScope.SELECTION:
        chosen = set(selection or ())
        return [e for e in events if e.eid in chosen]
    if query.scope is SearchScope.FROM_CURSOR and cursor_eid is not None:
        for position, event in enumerate(events):
            if event.eid == cursor_eid:
                return events[position:]
    return events


def find_all(
    doc: SubtitleDocument,
    query: SearchQuery,
    *,
    selection: Iterable[int] | None = None,
    cursor_eid: int | None = None,
    limit: int | None = None,
) -> list[Match]:
    """Все совпадения в порядке следования реплик.

    ``limit`` ограничивает выдачу: список находок нужен человеку для выбора,
    и десять тысяч строк в нём никому не помогут, а память займут.
    """
    pattern = query.compile()
    if pattern is None:
        return []

    found: list[Match] = []
    for event in _events_in_scope(doc, query, selection, cursor_eid):
        text = event.text
        spans = () if query.include_tags else _tag_spans(text)
        for hit in pattern.finditer(text):
            if spans and _inside_tags(spans, hit.start(), hit.end()):
                continue
            # Пустое совпадение (например, образец «a*») сдвинуло бы поиск в
            # бесконечный цикл на замене — и заменять в нём нечего.
            if hit.end() == hit.start():
                continue
            found.append(Match(event.eid, hit.start(), hit.end(), text))
            if limit is not None and len(found) >= limit:
                return found
    return found


def count_matches(doc: SubtitleDocument, query: SearchQuery, **kwargs) -> int:
    """Сколько всего совпадений. Считается без сборки списка находок."""
    pattern = query.compile()
    if pattern is None:
        return 0
    total = 0
    selection = kwargs.get("selection")
    cursor_eid = kwargs.get("cursor_eid")
    for event in _events_in_scope(doc, query, selection, cursor_eid):
        spans = () if query.include_tags else _tag_spans(event.text)
        for hit in pattern.finditer(event.text):
            if hit.end() == hit.start():
                continue
            if spans and _inside_tags(spans, hit.start(), hit.end()):
                continue
            total += 1
    return total


def replacements(
    doc: SubtitleDocument,
    query: SearchQuery,
    replacement: str,
    *,
    selection: Iterable[int] | None = None,
    cursor_eid: int | None = None,
) -> Iterator[tuple[int, str, str]]:
    """Что получится после замены: ``(eid, было, стало)`` для каждой реплики.

    Возвращает **намерение**, а не изменённый документ: вызывающий соберёт из
    этого одну команду, и вся массовая замена отменится одним движением.

    Замена идёт справа налево, чтобы позиции ранних совпадений не поехали от
    правки поздних. В режиме регулярных выражений в замене работают ссылки на
    группы — ради них и берут этот режим.
    """
    pattern = query.compile()
    if pattern is None:
        return

    for event in _events_in_scope(doc, query, selection, cursor_eid):
        text = event.text
        spans = () if query.include_tags else _tag_spans(text)
        hits = [
            hit
            for hit in pattern.finditer(text)
            if hit.end() != hit.start()
            and not (spans and _inside_tags(spans, hit.start(), hit.end()))
        ]
        if not hits:
            continue

        updated = text
        for hit in reversed(hits):
            piece = hit.expand(replacement) if query.regex else replacement
            updated = updated[: hit.start()] + piece + updated[hit.end() :]
        if updated != text:
            yield event.eid, text, updated
