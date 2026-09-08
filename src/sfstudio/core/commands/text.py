"""Команды правки текста."""

from __future__ import annotations

import time

from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands.base import Command
from sfstudio.core.document import SubtitleDocument

__all__ = [
    "SetActor",
    "SetMargins",
    "SetStyle",
    "SetText",
    "ToggleComment",
]

#: Окно слияния последовательных правок текста одного события.
COALESCE_WINDOW_S = 0.8


class SetText(Command):
    """Заменяет текст события целиком."""

    __slots__ = ("_after", "_before", "_stamp", "eid", "label")

    def __init__(self, eid: int, new_text: str, *, label: str = "Правка текста") -> None:
        self.eid = eid
        self._after = new_text
        self._before: str | None = None
        self._stamp = time.monotonic()
        self.label = label

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        event = doc.by_eid(self.eid)
        if self._before is None:
            self._before = event.text
        event.set_text(self._after)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._before is not None, "revert до apply"
        doc.by_eid(self.eid).set_text(self._before)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def coalesce_with(self, previous: Command) -> Command | None:
        """Склеивает правки одного события, идущие подряд в пределах окна."""
        if not isinstance(previous, SetText) or previous.eid != self.eid:
            return None
        if self._stamp - previous._stamp > COALESCE_WINDOW_S:
            return None
        merged = SetText(self.eid, self._after, label=previous.label)
        merged._before = previous._before
        merged._stamp = self._stamp
        return merged

    def size_hint(self) -> int:
        return 64 + len(self._after) + len(self._before or "")


class _SetField(Command):
    """Общая база для команд, меняющих одно скалярное поле события."""

    __slots__ = ("_after", "_before", "eid", "label")

    #: Имя поля события. Константа класса, а не слот — иначе конфликт имён.
    FIELD: str = ""

    def __init__(self, eid: int, value: object, *, label: str) -> None:
        self.eid = eid
        self._after = value
        self._before: object = None
        self.label = label

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        event = doc.by_eid(self.eid)
        self._before = getattr(event, self.FIELD)
        setattr(event, self.FIELD, self._after)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        setattr(doc.by_eid(self.eid), self.FIELD, self._before)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)


class SetStyle(_SetField):
    FIELD = "style"

    def __init__(self, eid: int, style_name: str) -> None:
        super().__init__(eid, style_name, label="Смена стиля")


class SetActor(_SetField):
    FIELD = "name"

    def __init__(self, eid: int, actor: str) -> None:
        super().__init__(eid, actor, label="Смена актёра")


class ToggleComment(Command):
    """Переключает событие между ``Dialogue:`` и ``Comment:``."""

    __slots__ = ("eids", "label")

    def __init__(self, eids: list[int]) -> None:
        self.eids = eids
        self.label = "Комментарий"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        for eid in self.eids:
            event = doc.by_eid(eid)
            event.comment = not event.comment
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self.eids))

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        return self.apply(doc)  # операция самообратна


class SetMargins(Command):
    """Меняет поля реплики (MarginL/R/V) одной операцией.

    Три поля вместе, а не тремя командами: правка полей в инспекторе — один
    жест пользователя, и разваливать его на три шага отмены незачем. Ноль в
    ASS означает «взять из стиля», поэтому значения пишутся как есть, без
    попыток подставить туда числа из стиля.
    """

    __slots__ = ("_before", "eid", "label", "margins")

    def __init__(self, eid: int, left: int, right: int, vertical: int) -> None:
        self.eid = eid
        self.margins = (left, right, vertical)
        self._before: tuple[int, int, int] | None = None
        self.label = "Поля реплики"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        event = doc.by_eid(self.eid)
        if self._before is None:
            self._before = (event.margin_l, event.margin_r, event.margin_v)
        event.margin_l, event.margin_r, event.margin_v = self.margins
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._before is not None, "revert до apply"
        event = doc.by_eid(self.eid)
        event.margin_l, event.margin_r, event.margin_v = self._before
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def coalesce_with(self, previous: Command) -> Command | None:
        """Правка трёх полей подряд — один шаг отмены."""
        if not isinstance(previous, SetMargins) or previous.eid != self.eid:
            return None
        merged = SetMargins(self.eid, *self.margins)
        merged._before = previous._before
        return merged
