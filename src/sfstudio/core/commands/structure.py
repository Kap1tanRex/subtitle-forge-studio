"""Команды, меняющие состав и порядок событий."""

from __future__ import annotations

from dataclasses import replace

from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands.base import Command
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent

__all__ = ["DeleteEvents", "DuplicateEvents", "InsertEvent", "MergeEvents", "SplitEvent"]


class InsertEvent(Command):
    """Вставляет новое событие."""

    __slots__ = ("_eid", "at", "label", "template")

    def __init__(self, template: SubtitleEvent, at: int | None = None) -> None:
        self.template = template
        self.at = at
        self._eid: int | None = None
        self.label = "Новое событие"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if self._eid is None:
            self._eid = self.template.eid if self.template.eid > 0 else doc.new_eid()
        event = replace(self.template, eid=self._eid)
        event.invalidate()
        doc.add_event(event, self.at)
        doc.bump_revision()
        return ChangeSet.added(self._eid)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._eid is not None, "revert до apply"
        doc.remove_event(self._eid)
        doc.bump_revision()
        return ChangeSet.removed(self._eid)


class DeleteEvents(Command):
    """Удаляет события, запоминая их позиции для точного восстановления."""

    __slots__ = ("_saved", "eids", "label")

    def __init__(self, eids: list[int]) -> None:
        self.eids = eids
        self._saved: list[tuple[int, SubtitleEvent]] = []
        self.label = f"Удаление {len(eids)} событий" if len(eids) > 1 else "Удаление события"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        self._saved = []
        # С конца — иначе позиции последующих удалений съезжают.
        for eid in sorted(self.eids, key=doc.index_of, reverse=True):
            self._saved.append(doc.remove_event(eid))
        doc.bump_revision()
        return ChangeSet.removed(*self.eids)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        for pos, event in reversed(self._saved):
            doc.add_event(event, pos)
        doc.bump_revision()
        return ChangeSet.added(*self.eids)

    def size_hint(self) -> int:
        return 256 * max(1, len(self.eids))


class DuplicateEvents(Command):
    """Копирует события, вставляя дубликат сразу после оригинала."""

    __slots__ = ("_new_eids", "label", "src_eids")

    def __init__(self, eids: list[int]) -> None:
        self.src_eids = eids
        self._new_eids: list[int] = []
        self.label = "Дублирование"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        reuse = bool(self._new_eids)
        created: list[int] = []
        for i, src_eid in enumerate(sorted(self.src_eids, key=doc.index_of, reverse=True)):
            src = doc.by_eid(src_eid)
            eid = self._new_eids[i] if reuse else doc.new_eid()
            copy = replace(src, eid=eid)
            copy.invalidate()
            doc.add_event(copy, doc.index_of(src_eid) + 1)
            created.append(eid)
        if not reuse:
            self._new_eids = created
        doc.bump_revision()
        return ChangeSet.added(*self._new_eids)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        for eid in self._new_eids:
            doc.remove_event(eid)
        doc.bump_revision()
        return ChangeSet.removed(*self._new_eids)


class SplitEvent(Command):
    """Разбивает событие на два по позиции в тексте и/или моменту времени.

    Если ``at_ms`` не задан, время делится пропорционально длине половин —
    так реплика, разрезанная посередине фразы, получает соразмерные тайминги.
    """

    __slots__ = ("_before", "_new_eid", "at_ms", "eid", "label", "text_pos")

    def __init__(self, eid: int, text_pos: int, at_ms: int | None = None) -> None:
        self.eid = eid
        self.text_pos = text_pos
        self.at_ms = at_ms
        self._new_eid: int | None = None
        self._before: tuple[str, int, int] | None = None
        self.label = "Разбить событие"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        event = doc.by_eid(self.eid)
        if self._before is None:
            self._before = (event.text, event.start, event.end)

        head = event.text[: self.text_pos]
        tail = event.text[self.text_pos :]

        if self.at_ms is not None:
            split_ms = self.at_ms
        else:
            total = max(1, len(event.text))
            ratio = self.text_pos / total
            split_ms = event.start + int(event.duration * ratio)
        split_ms = max(event.start + 1, min(split_ms, event.end - 1))

        if self._new_eid is None:
            self._new_eid = doc.new_eid()

        second = replace(event, eid=self._new_eid, text=tail, start=split_ms, end=event.end)
        second.invalidate()

        event.set_text(head)
        event.end = split_ms
        doc.add_event(second, doc.index_of(self.eid) + 1)
        doc.touch()
        doc.bump_revision()
        return ChangeSet(
            changed_eids=frozenset({self.eid}),
            added_eids=frozenset({self._new_eid}),
            structural=True,
        )

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._before is not None and self._new_eid is not None, "revert до apply"
        doc.remove_event(self._new_eid)
        event = doc.by_eid(self.eid)
        event.set_text(self._before[0])
        event.start, event.end = self._before[1], self._before[2]
        doc.touch()
        doc.bump_revision()
        return ChangeSet(
            changed_eids=frozenset({self.eid}),
            removed_eids=frozenset({self._new_eid}),
            structural=True,
        )


class MergeEvents(Command):
    """Сливает события в одно: текст через ``\\N``, тайминги — объединением."""

    __slots__ = ("_before", "_removed", "eids", "keep", "label", "separator")

    def __init__(self, eids: list[int], separator: str = "\\N") -> None:
        if len(eids) < 2:
            raise ValueError("для слияния нужно минимум два события")
        self.eids = eids
        self.separator = separator
        self.keep = eids[0]
        self._before: tuple[str, int, int] | None = None
        self._removed: list[tuple[int, SubtitleEvent]] = []
        self.label = f"Слияние {len(eids)} событий"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        ordered = sorted(self.eids, key=lambda e: (doc.by_eid(e).start, e))
        self.keep = ordered[0]
        target = doc.by_eid(self.keep)
        if self._before is None:
            self._before = (target.text, target.start, target.end)

        texts = [doc.by_eid(eid).text for eid in ordered]
        start = min(doc.by_eid(eid).start for eid in ordered)
        end = max(doc.by_eid(eid).end for eid in ordered)

        self._removed = []
        for eid in sorted(ordered[1:], key=doc.index_of, reverse=True):
            self._removed.append(doc.remove_event(eid))

        target.set_text(self.separator.join(t for t in texts if t))
        target.start, target.end = start, end
        doc.touch()
        doc.bump_revision()
        return ChangeSet(
            changed_eids=frozenset({self.keep}),
            removed_eids=frozenset(ordered[1:]),
            structural=True,
        )

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._before is not None, "revert до apply"
        for pos, event in reversed(self._removed):
            doc.add_event(event, pos)
        target = doc.by_eid(self.keep)
        target.set_text(self._before[0])
        target.start, target.end = self._before[1], self._before[2]
        doc.touch()
        doc.bump_revision()
        return ChangeSet(
            changed_eids=frozenset({self.keep}),
            added_eids=frozenset(e for e, _ in ((ev.eid, ev) for _, ev in self._removed)),
            structural=True,
        )
