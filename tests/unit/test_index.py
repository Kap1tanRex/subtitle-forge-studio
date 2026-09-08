"""Тесты временного индекса.

Ключевой тест — сравнение с наивной O(n) реализацией на случайных данных.
Именно он ловит баг из аудита спецификации (B1): без собственной проверки
``end > t`` префиксный максимум даёт ложные срабатывания.
"""

from __future__ import annotations

import random

import pytest

from sfstudio.core.event import SubtitleEvent
from sfstudio.core.index import TimeIndex


def make(*spans: tuple[int, int]) -> list[SubtitleEvent]:
    return [SubtitleEvent(eid=i + 1, start=s, end=e) for i, (s, e) in enumerate(spans)]


def naive_active(events: list[SubtitleEvent], t: int) -> set[int]:
    return {e.eid for e in events if e.start <= t < e.end}


def naive_range(events: list[SubtitleEvent], t0: int, t1: int) -> set[int]:
    return {e.eid for e in events if e.start < t1 and e.end > t0}


def test_active_at_basic() -> None:
    events = make((0, 1000), (1000, 2000), (1500, 2500))
    idx = TimeIndex(events)

    assert idx.active_at(0) == [1]
    assert idx.active_at(999) == [1]
    assert idx.active_at(1000) == [2]  # конец полуоткрыт: 1000 уже не в первом
    assert set(idx.active_at(1600)) == {2, 3}
    assert idx.active_at(2500) == []


def test_active_at_ignores_gap_under_long_event() -> None:
    """Регрессия на баг B1.

    Длинное событие поднимает префиксный максимум, но само в точке запроса
    неактивно. Без проверки собственного ``end`` индекс вернул бы его.
    """
    # Событие 1 длинное и уже закончилось; событие 2 активно.
    events = make((0, 5000), (10_000, 20_000))
    idx = TimeIndex(events)

    assert idx.active_at(7000) == []
    assert idx.active_at(15_000) == [2]

    # Короткое событие внутри диапазона длинного, запрос между ними.
    events = make((0, 100_000), (200, 300), (400, 500))
    idx = TimeIndex(events)
    assert set(idx.active_at(350)) == {1}  # только длинное, не 2 и не 3


def test_range_query() -> None:
    events = make((0, 1000), (2000, 3000), (5000, 6000))
    idx = TimeIndex(events)

    assert idx.range(0, 1) == [1]
    assert idx.range(1000, 2000) == []  # зазор
    assert set(idx.range(500, 2500)) == {1, 2}
    assert idx.range(3000, 5000) == []
    assert idx.range(100, 100) == []  # пустой интервал


def test_empty_index() -> None:
    idx = TimeIndex([])
    assert idx.active_at(0) == []
    assert idx.range(0, 1000) == []
    assert len(idx) == 0


def test_zero_length_events_are_never_active() -> None:
    """``start == end`` — вырожденный интервал, полуинтервал пуст."""
    idx = TimeIndex(make((1000, 1000)))
    assert idx.active_at(1000) == []
    assert idx.active_at(999) == []


@pytest.mark.parametrize("seed", range(30))
def test_matches_naive_implementation(seed: int) -> None:
    """Случайные наборы интервалов, включая вложенные и перекрывающиеся."""
    rng = random.Random(seed)
    events: list[SubtitleEvent] = []
    for i in range(rng.randint(1, 60)):
        start = rng.randint(0, 20_000)
        # Смесь коротких и очень длинных — длинные и создают ловушку B1.
        length = rng.choice([rng.randint(0, 500), rng.randint(0, 30_000)])
        events.append(SubtitleEvent(eid=i + 1, start=start, end=start + length))

    idx = TimeIndex(events)

    for _ in range(120):
        t = rng.randint(-100, 55_000)
        assert set(idx.active_at(t)) == naive_active(events, t), f"active_at({t}) seed={seed}"

    for _ in range(60):
        a = rng.randint(-100, 50_000)
        b = a + rng.randint(0, 8000)
        assert set(idx.range(a, b)) == naive_range(events, a, b), f"range({a},{b}) seed={seed}"


def test_active_at_result_is_time_ordered() -> None:
    events = make((5000, 9000), (1000, 9000), (3000, 9000))
    idx = TimeIndex(events)
    result = idx.active_at(6000)
    starts = [next(e.start for e in events if e.eid == eid) for eid in result]
    assert starts == sorted(starts)


def test_next_start_and_prev_end() -> None:
    idx = TimeIndex(make((0, 1000), (2000, 3000), (5000, 6000)))
    assert idx.next_start_after(0) == 2000
    assert idx.next_start_after(2500) == 5000
    assert idx.next_start_after(9000) is None
    assert idx.prev_end_before(2500) == 1000


def test_stale_rebuild() -> None:
    events = make((0, 1000))
    idx = TimeIndex(events)
    assert idx.active_at(500) == [1]

    events.append(SubtitleEvent(eid=99, start=400, end=800))
    idx.mark_stale()
    assert idx.stale
    idx.ensure(events)
    assert set(idx.active_at(500)) == {1, 99}
