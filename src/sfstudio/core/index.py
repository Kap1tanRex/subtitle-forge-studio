"""Временной индекс событий.

Отвечает на два вопроса за O(log n + k):
* какие события активны в момент ``t``  — для рендера кадра;
* какие события попадают в диапазон     — для отрисовки видимой части таймлайна.

Структура: параллельные отсортированные массивы + префиксный максимум концов.
Полноценное дерево отрезков здесь избыточно — субтитры почти не перекрываются,
и префиксный максимум обрывает поиск после одного-двух шагов.

.. warning::
   Префиксный максимум — **только условие раннего выхода**. Из ``max_end[i] > t``
   не следует, что событие ``i`` активно: максимум мог быть достигнут на любом
   из предшествующих событий. Каждый кандидат проверяется отдельно по
   собственному ``end``. Пропуск этой проверки — источник ложных срабатываний
   (найдено при аудите спецификации, см. ``docs/AUDIT_v1.1.md``, пункт B1).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence

from sfstudio.core.event import SubtitleEvent

__all__ = ["TimeIndex"]


class TimeIndex:
    """Индекс по времени. Перестраивается целиком; это дёшево (~2 мс на 20k)."""

    __slots__ = ("_eids", "_ends", "_max_end", "_position", "_stale", "_starts")

    def __init__(self, events: Iterable[SubtitleEvent] | None = None) -> None:
        self._starts: list[int] = []
        self._ends: list[int] = []
        self._eids: list[int] = []
        self._max_end: list[int] = []
        self._position: dict[int, int] = {}
        self._stale = True
        if events is not None:
            self.rebuild(events)

    def __len__(self) -> int:
        return len(self._starts)

    @property
    def stale(self) -> bool:
        return self._stale

    def mark_stale(self) -> None:
        """Пометить индекс устаревшим — пересборка отложится до первого запроса."""
        self._stale = True

    def rebuild(self, events: Iterable[SubtitleEvent]) -> None:
        ordered = sorted(events, key=lambda e: (e.start, e.eid))
        self._starts = [e.start for e in ordered]
        self._ends = [e.end for e in ordered]
        self._eids = [e.eid for e in ordered]
        self._position = {eid: i for i, eid in enumerate(self._eids)}

        max_end: list[int] = []
        running = -(2**62)
        for end in self._ends:
            if end > running:
                running = end
            max_end.append(running)
        self._max_end = max_end
        self._stale = False

    def ensure(self, events: Iterable[SubtitleEvent]) -> None:
        if self._stale:
            self.rebuild(events)

    # -- запросы ------------------------------------------------------------ #

    def active_at(self, t: int) -> list[int]:
        """``eid`` событий, для которых ``start <= t < end``.

        Порядок — по возрастанию ``start``.
        """
        starts = self._starts
        ends = self._ends
        max_end = self._max_end

        # Все события с start <= t лежат в [0, right).
        right = bisect_right(starts, t)
        out: list[int] = []
        i = right - 1
        while i >= 0:
            if max_end[i] <= t:
                # Ни одно событие из [0..i] не дотягивает до t — левее искать нечего.
                break
            if ends[i] > t:  # собственная проверка кандидата, см. warning модуля
                out.append(self._eids[i])
            i -= 1
        out.reverse()
        return out

    def range(self, t0: int, t1: int) -> list[int]:
        """``eid`` событий, пересекающих полуинтервал ``[t0, t1)``."""
        if t1 <= t0:
            return []
        starts = self._starts
        ends = self._ends
        max_end = self._max_end

        right = bisect_left(starts, t1)  # события с start < t1
        out: list[int] = []
        i = right - 1
        while i >= 0:
            if max_end[i] <= t0:
                break
            if ends[i] > t0:
                out.append(self._eids[i])
            i -= 1
        out.reverse()
        return out

    def next_start_after(self, t: int) -> int | None:
        """Время начала ближайшего события строго после ``t``."""
        i = bisect_right(self._starts, t)
        return self._starts[i] if i < len(self._starts) else None

    def prev_end_before(self, t: int) -> int | None:
        """Наибольший конец среди событий, завершившихся до ``t``."""
        best: int | None = None
        for i in range(bisect_left(self._starts, t) - 1, -1, -1):
            end = self._ends[i]
            if end <= t and (best is None or end > best):
                best = end
            if self._max_end[i] <= (best if best is not None else -(2**62)):
                break
        return best

    def ordered_eids(self) -> Sequence[int]:
        """``eid`` в порядке возрастания времени начала."""
        return self._eids

    def position_of(self, eid: int) -> int | None:
        """Место события в порядке времени, либо ``None``.

        Словарь строится при пересборке индекса, поэтому запрос — O(1).
        Линейный поиск здесь обошёлся бы дорого: соседей спрашивают на каждое
        изменение документа, то есть на каждое нажатие клавиши.
        """
        return self._position.get(eid)

    def neighbours(self, eid: int) -> tuple[int | None, int | None]:
        """``eid`` предыдущего и следующего события по времени."""
        position = self._position.get(eid)
        if position is None:
            return (None, None)
        before = self._eids[position - 1] if position > 0 else None
        after = self._eids[position + 1] if position + 1 < len(self._eids) else None
        return (before, after)

    def boundaries(self) -> Sequence[int]:
        """Отсортированные уникальные моменты начал и концов — для магнитов."""
        return sorted(set(self._starts) | set(self._ends))
