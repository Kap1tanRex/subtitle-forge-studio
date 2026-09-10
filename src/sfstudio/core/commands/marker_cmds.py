"""Команды над маркерами.

Маркер неизменяем (``frozen``), поэтому правка — это замена одного объекта
другим. Команда хранит оба: этого достаточно и для применения, и для точного
отката, и весит пара сотен байт вместо снимка документа.

Перетаскивание маркера по линейке сливается в один шаг отмены: жест мышью —
одно действие в глазах человека, и требовать сорок нажатий «Отменить» после
одного движения нельзя.
"""

from __future__ import annotations

from sfstudio.app.i18n import tr
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands.base import Command
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.markers import Marker

__all__ = ["AddMarker", "ClearMarkers", "MoveMarker", "RemoveMarker", "UpdateMarker"]


class AddMarker(Command):
    """Ставит маркер."""

    __slots__ = ("label", "marker")

    def __init__(self, marker: Marker, label: str = tr('Новый маркер')) -> None:
        self.marker = marker
        self.label = label

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        doc.markers.add(self.marker)
        doc.touch()
        doc.bump_revision()
        return ChangeSet.markers()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        doc.markers.remove(self.marker)
        doc.touch()
        doc.bump_revision()
        return ChangeSet.markers()

    def size_hint(self) -> int:
        return 128 + len(self.marker.name) + len(self.marker.note)


class RemoveMarker(Command):
    """Убирает маркер."""

    __slots__ = ("_removed", "label", "marker")

    def __init__(self, marker: Marker, label: str = tr('Удаление маркера')) -> None:
        self.marker = marker
        self._removed = False
        self.label = label

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        self._removed = doc.markers.remove(self.marker)
        if not self._removed:
            return ChangeSet.EMPTY
        doc.touch()
        doc.bump_revision()
        return ChangeSet.markers()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if not self._removed:
            return ChangeSet.EMPTY
        doc.markers.add(self.marker)
        doc.touch()
        doc.bump_revision()
        return ChangeSet.markers()


class UpdateMarker(Command):
    """Заменяет маркер изменённым: имя, примечание, цвет, длительность."""

    __slots__ = ("after", "before", "label")

    def __init__(
        self, before: Marker, after: Marker, label: str = tr('Правка маркера')
    ) -> None:
        self.before = before
        self.after = after
        self.label = label

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if not doc.markers.replace(self.before, self.after):
            return ChangeSet.EMPTY
        doc.touch()
        doc.bump_revision()
        return ChangeSet.markers()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if not doc.markers.replace(self.after, self.before):
            return ChangeSet.EMPTY
        doc.touch()
        doc.bump_revision()
        return ChangeSet.markers()

    def size_hint(self) -> int:
        return 256 + len(self.after.name) + len(self.after.note)


class MoveMarker(UpdateMarker):
    """Перенос маркера по времени. Отличается от правки только слиянием.

    Тянут маркер плавно, десятками промежуточных положений. Каждое из них
    отдельным шагом отмены превратило бы одно движение мышью в длинную
    историю, из которой не выбраться.
    """

    __slots__ = ()

    def __init__(self, before: Marker, after: Marker) -> None:
        super().__init__(before, after, label=tr('Перенос маркера'))

    def coalesce_with(self, previous: Command) -> Command | None:
        if not isinstance(previous, MoveMarker):
            return None
        # Сливаем только продолжение того же жеста: конец предыдущего
        # переноса должен быть началом этого.
        if previous.after != self.before:
            return None
        return MoveMarker(previous.before, self.after)


class ClearMarkers(Command):
    """Убирает все маркеры сразу.

    Отдельная команда, а не серия удалений: маркеров бывают сотни, и
    «убрать все» должно отменяться одним шагом, а не сотней.
    """

    __slots__ = ("_before", "label")

    def __init__(self) -> None:
        self._before: list[Marker] = []
        self.label = tr('Убрать все маркеры')

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if not len(doc.markers):
            return ChangeSet.EMPTY
        self._before = doc.markers.items
        doc.markers.clear()
        doc.touch()
        doc.bump_revision()
        return ChangeSet.markers()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if not self._before:
            return ChangeSet.EMPTY
        for marker in self._before:
            doc.markers.add(marker)
        doc.touch()
        doc.bump_revision()
        return ChangeSet.markers()

    def size_hint(self) -> int:
        return 128 * max(1, len(self._before))
