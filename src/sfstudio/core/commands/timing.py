"""Команды правки таймингов."""

from __future__ import annotations

from dataclasses import dataclass

from sfstudio.app.i18n import tr
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands.base import Command
from sfstudio.core.document import SubtitleDocument

__all__ = ["ApplyTimings", "LinearSync", "SetTiming", "ShiftTimes", "SyncError", "SyncPoints"]

#: Минимальная длительность, ниже которой команды не дают событию схлопнуться.
MIN_DURATION_MS = 100


class SyncError(ValueError):
    """Синхронизация невозможна с заданными точками."""


class SetTiming(Command):
    """Задаёт начало и/или конец одного события.

    ``None`` означает «не трогать». Инвариант ``end > start`` поддерживается:
    при попытке его нарушить противоположная граница подтягивается.
    """

    __slots__ = ("_after", "_before", "eid", "label")

    def __init__(
        self,
        eid: int,
        start: int | None = None,
        end: int | None = None,
        *,
        label: str = tr('Правка тайминга'),
    ) -> None:
        self.eid = eid
        self._after = (start, end)
        self._before: tuple[int, int] | None = None
        self.label = label

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        event = doc.by_eid(self.eid)
        if self._before is None:
            self._before = (event.start, event.end)

        start, end = self._after
        new_start = event.start if start is None else max(0, start)
        new_end = event.end if end is None else max(0, end)

        if new_end <= new_start:
            # Двигаем ту границу, которую пользователь не задавал.
            if end is None:
                new_end = new_start + MIN_DURATION_MS
            else:
                new_start = max(0, new_end - MIN_DURATION_MS)

        event.start, event.end = new_start, new_end
        doc.touch(self.eid)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._before is not None, tr('revert до apply')
        event = doc.by_eid(self.eid)
        event.start, event.end = self._before
        doc.touch(self.eid)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def coalesce_with(self, previous: Command) -> Command | None:
        """Перетаскивание границы даёт одну отмену на весь жест."""
        if not isinstance(previous, SetTiming) or previous.eid != self.eid:
            return None
        merged = SetTiming(self.eid, *self._after, label=previous.label)
        merged._before = previous._before
        return merged


class ShiftTimes(Command):
    """Сдвигает пачку событий на ``delta`` миллисекунд."""

    __slots__ = ("delta", "eids", "label", "shift_end", "shift_start")

    def __init__(
        self,
        eids: list[int],
        delta: int,
        *,
        shift_start: bool = True,
        shift_end: bool = True,
    ) -> None:
        self.eids = eids
        self.delta = delta
        self.shift_start = shift_start
        self.shift_end = shift_end
        sign = tr('вперёд') if delta >= 0 else tr('назад')
        self.label = tr('Сдвиг {0} событий {1}').format(len(eids), sign)

    def _move(self, doc: SubtitleDocument, delta: int) -> ChangeSet:
        for eid in self.eids:
            event = doc.by_eid(eid)
            if self.shift_start:
                event.start = max(0, event.start + delta)
            if self.shift_end:
                event.end = max(0, event.end + delta)
            if event.end <= event.start:
                event.end = event.start + MIN_DURATION_MS
        doc.touch()
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self.eids))

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        return self._move(doc, self.delta)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        # Обратный сдвиг неточен, если сработал clamp на нуле, поэтому
        # для больших отрицательных сдвигов пользуйтесь LinearSync.
        return self._move(doc, -self.delta)

    def size_hint(self) -> int:
        return 64 + 8 * len(self.eids)


@dataclass(frozen=True, slots=True)
class SyncPoints:
    """Две пары «было → стало» для линейной синхронизации."""

    src_a: int
    dst_a: int
    src_b: int
    dst_b: int


class LinearSync(Command):
    """Аффинное преобразование времени по двум опорным точкам.

    Защиты (без них команда либо падает, либо тихо портит документ):

    * ``src_a == src_b`` — деление на ноль;
    * ``dst_b <= dst_a`` — отрицательный или нулевой масштаб переворачивает
      порядок событий;
    * после преобразования длительность не опускается ниже ``MIN_DURATION_MS``.

    Экстремальный, но корректный масштаб (например, 25 → 23.976 fps) не блокируется:
    решение о его допустимости принимает вызывающий код, показывая подтверждение.
    """

    __slots__ = ("_before", "eids", "label", "offset", "points", "scale")

    def __init__(self, eids: list[int], points: SyncPoints) -> None:
        if points.src_a == points.src_b:
            raise SyncError(tr('Опорные точки источника совпадают — масштаб не определён.'))
        if points.dst_b <= points.dst_a:
            raise SyncError(tr('Целевые точки должны идти по возрастанию.'))

        self.points = points
        self.eids = eids
        self.scale = (points.dst_b - points.dst_a) / (points.src_b - points.src_a)
        if self.scale <= 0:
            raise SyncError(tr('Масштаб получился неположительным — проверьте порядок точек.'))
        self.offset = points.dst_a - points.src_a * self.scale
        self._before: dict[int, tuple[int, int]] = {}
        self.label = tr('Синхронизация ×{0:.4f}').format(self.scale)

    def _map(self, t: int) -> int:
        return max(0, round(t * self.scale + self.offset))

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if not self._before:
            self._before = {eid: (doc.by_eid(eid).start, doc.by_eid(eid).end) for eid in self.eids}
        for eid in self.eids:
            event = doc.by_eid(eid)
            event.start = self._map(event.start)
            event.end = max(self._map(event.end), event.start + MIN_DURATION_MS)
        doc.touch()
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self.eids))

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        for eid, (start, end) in self._before.items():
            event = doc.by_eid(eid)
            event.start, event.end = start, end
        doc.touch()
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self.eids))

    def size_hint(self) -> int:
        return 96 + 24 * len(self.eids)


class ApplyTimings(Command):
    """Применяет заранее рассчитанный набор таймингов.

    Отдельная команда, а не серия ``SetTiming``: доводка меняет десятки реплик
    сразу, и отменяться это должно одним шагом, а не сорока. План считается
    заранее (:mod:`sfstudio.services.autotiming`), поэтому диалог может
    показать предпросмотр до того, как документ изменится.
    """

    __slots__ = ("_before", "label", "timings")

    def __init__(
        self,
        timings: dict[int, tuple[int, int]],
        label: str = tr('Доводка таймингов'),
    ) -> None:
        self.timings = timings
        self._before: dict[int, tuple[int, int]] = {}
        self.label = label

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if not self._before:
            self._before = {
                eid: (doc.by_eid(eid).start, doc.by_eid(eid).end)
                for eid in self.timings
                if doc.has(eid)
            }
        for eid, (start, end) in self.timings.items():
            if not doc.has(eid):
                continue
            event = doc.by_eid(eid)
            event.start = max(0, start)
            event.end = max(event.start + MIN_DURATION_MS // 10, end)
        doc.touch()
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self.timings))

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        for eid, (start, end) in self._before.items():
            if not doc.has(eid):
                continue
            event = doc.by_eid(eid)
            event.start, event.end = start, end
        doc.touch()
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self._before))

    def size_hint(self) -> int:
        return 64 + 24 * len(self.timings)
