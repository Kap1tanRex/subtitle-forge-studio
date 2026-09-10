"""Команды над дорожками.

Дорожка субтитров — это слой ASS (см. :mod:`sfstudio.core.tracks`), поэтому
перенос реплики на другую дорожку здесь буквально меняет ``event.layer``.

Удаление дорожки — единственное место, где решение неочевидно: что делать с
её репликами. Удалять вместе с дорожкой опасно (один клик — и текста нет),
поэтому по умолчанию они переезжают на соседнюю. Удаление предлагается явным
параметром, а вызывающий код обязан спросить пользователя.
"""

from __future__ import annotations

from dataclasses import replace

from sfstudio.app.i18n import tr
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands.base import Command
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.tracks import Track

__all__ = [
    "AddTrack",
    "MoveEventsToLayer",
    "RemoveTrack",
    "SetTrackFlags",
    "UpdateTrack",
]


class AddTrack(Command):
    """Добавляет дорожку субтитров."""

    __slots__ = ("_layer", "label", "name")

    def __init__(self, name: str = "", layer: int | None = None) -> None:
        self.name = name
        self._layer = layer
        self.label = tr('Новая дорожка')

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if self._layer is None:
            self._layer = doc.tracks.next_layer()
        doc.tracks.add(self.name, self._layer)
        doc.bump_revision()
        return ChangeSet.tracks()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._layer is not None, tr('revert до apply')
        doc.tracks.remove(self._layer)
        doc.bump_revision()
        return ChangeSet.tracks()


class RemoveTrack(Command):
    """Удаляет дорожку. Её реплики переезжают или удаляются вместе с ней."""

    __slots__ = ("_moved", "_removed", "_saved", "delete_events", "label", "layer")

    def __init__(self, layer: int, *, delete_events: bool = False) -> None:
        self.layer = layer
        self.delete_events = delete_events
        self._removed: Track | None = None
        self._moved: list[tuple[int, int]] = []          # (eid, прежний слой)
        self._saved: list[tuple[int, SubtitleEvent]] = []  # для удаления
        self.label = tr('Удаление дорожки')

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        track = doc.tracks.by_layer(self.layer)
        if track is None or len(doc.tracks.subtitles) <= 1:
            return ChangeSet.EMPTY

        victims = doc.events_on_layer(self.layer)
        result = ChangeSet.tracks()

        if self.delete_events:
            self._saved = []
            for eid in sorted((e.eid for e in victims), key=doc.index_of, reverse=True):
                self._saved.append(doc.remove_event(eid))
            result = result.merged_with(ChangeSet.removed(*(e.eid for e in victims)))
        else:
            target = self._neighbour_layer(doc)
            self._moved = [(e.eid, e.layer) for e in victims]
            for event in victims:
                event.layer = target
            result = result.merged_with(ChangeSet.changed(*(e.eid for e in victims)))

        self._removed = doc.tracks.remove(self.layer)
        doc.bump_revision()
        return result

    def _neighbour_layer(self, doc: SubtitleDocument) -> int:
        """Куда переселять реплики: ближайшая дорожка снизу, иначе сверху."""
        others = [t.layer for t in doc.tracks.subtitles if t.layer != self.layer]
        below = [layer for layer in others if layer < self.layer]
        return max(below) if below else min(others)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if self._removed is None:
            return ChangeSet.EMPTY
        doc.tracks.put(self._removed)
        result = ChangeSet.tracks()

        for position, event in reversed(self._saved):
            doc.add_event(event, position)
        if self._saved:
            result = result.merged_with(ChangeSet.added(*(e.eid for _, e in self._saved)))
        self._saved = []

        for eid, layer in self._moved:
            event = doc.get(eid)
            if event is not None:
                event.layer = layer
        if self._moved:
            result = result.merged_with(ChangeSet.changed(*(eid for eid, _ in self._moved)))
        self._moved = []

        doc.bump_revision()
        return result

    def size_hint(self) -> int:
        return 128 + 256 * len(self._saved) + 16 * len(self._moved)


class UpdateTrack(Command):
    """Меняет имя, цвет или высоту дорожки."""

    __slots__ = ("_before", "after", "label", "layer")

    def __init__(self, layer: int, after: Track) -> None:
        self.layer = layer
        self.after = after
        self._before: Track | None = None
        self.label = tr('Правка дорожки')

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        current = doc.tracks.by_layer(self.layer)
        if current is None:
            return ChangeSet.EMPTY
        self._before = replace(current)
        doc.tracks.put(replace(self.after, layer=self.layer))
        doc.bump_revision()
        return ChangeSet.tracks()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if self._before is None:
            return ChangeSet.EMPTY
        doc.tracks.put(self._before)
        doc.bump_revision()
        return ChangeSet.tracks()


class SetTrackFlags(Command):
    """Переключает видимость, замок или заглушение.

    Отдельно от :class:`UpdateTrack`, потому что сливается: щелчки по «глазу»
    подряд не должны заполнять историю отмены.
    """

    __slots__ = ("_before", "label", "layer", "locked", "muted", "visible")

    def __init__(
        self,
        layer: int,
        *,
        visible: bool | None = None,
        locked: bool | None = None,
        muted: bool | None = None,
    ) -> None:
        self.layer = layer
        self.visible = visible
        self.locked = locked
        self.muted = muted
        self._before: tuple[bool, bool, bool] | None = None
        self.label = tr('Свойства дорожки')

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        track = doc.tracks.by_layer(self.layer)
        if track is None:
            return ChangeSet.EMPTY
        self._before = (track.visible, track.locked, track.muted)
        if self.visible is not None:
            track.visible = self.visible
        if self.locked is not None:
            track.locked = self.locked
        if self.muted is not None:
            track.muted = self.muted
        doc.bump_revision()
        return ChangeSet.tracks()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        track = doc.tracks.by_layer(self.layer)
        if track is None or self._before is None:
            return ChangeSet.EMPTY
        track.visible, track.locked, track.muted = self._before
        doc.bump_revision()
        return ChangeSet.tracks()

    def coalesce_with(self, previous: Command) -> Command | None:
        if not isinstance(previous, SetTrackFlags) or previous.layer != self.layer:
            return None
        merged = SetTrackFlags(
            self.layer,
            visible=self.visible if self.visible is not None else previous.visible,
            locked=self.locked if self.locked is not None else previous.locked,
            muted=self.muted if self.muted is not None else previous.muted,
        )
        merged._before = previous._before
        return merged


class MoveEventsToLayer(Command):
    """Переносит реплики на другую дорожку."""

    __slots__ = ("_before", "eids", "label", "layer")

    def __init__(self, eids: list[int], layer: int) -> None:
        self.eids = eids
        self.layer = layer
        self._before: list[tuple[int, int]] = []
        self.label = tr('Перенос на дорожку')

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        self._before = []
        for eid in self.eids:
            event = doc.get(eid)
            if event is None:
                continue
            self._before.append((eid, event.layer))
            event.layer = self.layer
        doc.bump_revision()
        return ChangeSet.changed(*(eid for eid, _ in self._before))

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        for eid, layer in self._before:
            event = doc.get(eid)
            if event is not None:
                event.layer = layer
        doc.bump_revision()
        return ChangeSet.changed(*(eid for eid, _ in self._before))

    def size_hint(self) -> int:
        return 64 + 16 * len(self.eids)
