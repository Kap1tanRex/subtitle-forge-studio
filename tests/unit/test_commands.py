"""Тесты команд и стека отмены.

Ключевое свойство: после серии команд и стольких же отмен документ обязан
совпасть с исходным **побайтово при сериализации**. Сравнение по полям
пропустило бы расхождения в порядке событий и в тексте тегов.
"""

from __future__ import annotations

import pytest

from sfstudio.core.commands import (
    ClearPosition,
    DeleteEvents,
    DuplicateEvents,
    LinearSync,
    MergeEvents,
    SetPosition,
    SetText,
    SetTiming,
    ShiftTimes,
    SplitEvent,
    SyncError,
    SyncPoints,
    ToggleComment,
)
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.io.formats.ass import write_ass


@pytest.fixture
def doc() -> SubtitleDocument:
    d = SubtitleDocument.blank()
    d.create_event(1000, 3000, "Первая")
    d.create_event(4000, 6000, "Вторая")
    d.create_event(7000, 9000, r"{\b1}Третья")
    return d


@pytest.fixture
def stack(doc: SubtitleDocument) -> UndoStack:
    return UndoStack(doc)


class TestSetText:
    def test_apply_and_revert(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eid = doc.events[0].eid
        stack.run(SetText(eid, "Изменено"))
        assert doc.by_eid(eid).text == "Изменено"
        stack.undo()
        assert doc.by_eid(eid).text == "Первая"
        stack.redo()
        assert doc.by_eid(eid).text == "Изменено"

    def test_invalidates_plain_cache(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        event = doc.events[2]
        assert event.plain == "Третья"  # прогреваем кэш
        stack.run(SetText(event.eid, r"{\i1}Другая"))
        assert event.plain == "Другая"

    def test_coalesces_rapid_typing(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        """Набор текста даёт одну отмену, а не по одной на букву."""
        eid = doc.events[0].eid
        for text in ("П", "Пр", "При", "Прив"):
            stack.run(SetText(eid, text))
        assert stack.depth_used == 1
        stack.undo()
        assert doc.by_eid(eid).text == "Первая"

    def test_does_not_coalesce_across_events(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        stack.run(SetText(doc.events[0].eid, "A"))
        stack.run(SetText(doc.events[1].eid, "B"))
        assert stack.depth_used == 2


class TestTiming:
    def test_set_timing(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eid = doc.events[0].eid
        stack.run(SetTiming(eid, start=1500))
        assert doc.by_eid(eid).start == 1500
        assert doc.by_eid(eid).end == 3000
        stack.undo()
        assert doc.by_eid(eid).start == 1000

    def test_invariant_end_after_start(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        """Начало за концом не должно давать отрицательную длительность."""
        eid = doc.events[0].eid
        stack.run(SetTiming(eid, start=5000))
        assert doc.by_eid(eid).end > doc.by_eid(eid).start

    def test_index_sees_new_timing(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        """Индекс обязан перестроиться — иначе рендер покажет старое."""
        eid = doc.events[0].eid
        assert doc.active_at(2000) != []
        stack.run(SetTiming(eid, start=50_000, end=52_000))
        assert doc.active_at(2000) == []
        assert [e.eid for e in doc.active_at(51_000)] == [eid]

    def test_shift_times(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eids = [e.eid for e in doc.events]
        stack.run(ShiftTimes(eids, 1000))
        assert doc.events[0].start == 2000
        assert doc.events[2].end == 10_000
        stack.undo()
        assert doc.events[0].start == 1000


class TestLinearSync:
    def test_scales_and_offsets(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eids = [e.eid for e in doc.events]
        # 1000 → 2000, 7000 → 14000: масштаб ×2
        stack.run(LinearSync(eids, SyncPoints(1000, 2000, 7000, 14_000)))
        assert doc.events[0].start == 2000
        assert doc.events[2].start == 14_000

    def test_rejects_identical_source_points(self) -> None:
        """Без защиты здесь было бы деление на ноль."""
        with pytest.raises(SyncError, match="совпадают"):
            LinearSync([1], SyncPoints(1000, 2000, 1000, 5000))

    def test_rejects_inverted_targets(self) -> None:
        with pytest.raises(SyncError, match="возрастанию"):
            LinearSync([1], SyncPoints(1000, 5000, 7000, 2000))

    def test_never_collapses_duration(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        """Сильное сжатие не должно схлопнуть короткие реплики в ноль."""
        eids = [e.eid for e in doc.events]
        stack.run(LinearSync(eids, SyncPoints(1000, 1000, 7000, 1100)))
        for event in doc.events:
            assert event.duration > 0

    def test_revert_restores_exactly(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        before = [(e.start, e.end) for e in doc.events]
        eids = [e.eid for e in doc.events]
        stack.run(LinearSync(eids, SyncPoints(1000, 3333, 7000, 19_999)))
        stack.undo()
        assert [(e.start, e.end) for e in doc.events] == before


class TestPosition:
    def test_set_position_writes_pos_tag(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eid = doc.events[0].eid
        stack.run(SetPosition(eid, 960, 1010))
        assert doc.by_eid(eid).text == r"{\pos(960,1010)}Первая"
        assert doc.by_eid(eid).position() == (960.0, 1010.0)

    def test_position_rounds_to_integers(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        stack.run(SetPosition(doc.events[0].eid, 960.4, 1010.6))
        assert doc.events[0].text == r"{\pos(960,1011)}Первая"

    def test_preserves_neighbouring_tags(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eid = doc.events[2].eid  # у него уже есть {\b1}
        stack.run(SetPosition(eid, 100, 200))
        assert doc.by_eid(eid).text == r"{\b1\pos(100,200)}Третья"

    def test_drag_gesture_is_one_undo(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        """Перетаскивание — десятки промежуточных кадров, но одна отмена."""
        eid = doc.events[0].eid
        for x in range(100, 200, 10):
            stack.run(SetPosition(eid, x, 500))
        assert stack.depth_used == 1
        stack.undo()
        assert doc.by_eid(eid).text == "Первая"

    def test_clear_position(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eid = doc.events[0].eid
        stack.run(SetPosition(eid, 10, 20))
        stack.run(ClearPosition(eid))
        assert doc.by_eid(eid).text == "Первая"


class TestStructure:
    def test_delete_and_restore_position(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        """Отменённое удаление возвращает событие на прежнее место в списке."""
        eid = doc.events[1].eid
        stack.run(DeleteEvents([eid]))
        assert len(doc) == 2
        stack.undo()
        assert len(doc) == 3
        assert doc.events[1].eid == eid

    def test_delete_multiple(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eids = [doc.events[0].eid, doc.events[2].eid]
        stack.run(DeleteEvents(eids))
        assert [e.text for e in doc.events] == ["Вторая"]
        stack.undo()
        assert [e.text for e in doc.events] == ["Первая", "Вторая", r"{\b1}Третья"]

    def test_duplicate(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        stack.run(DuplicateEvents([doc.events[0].eid]))
        assert len(doc) == 4
        assert doc.events[1].text == "Первая"
        stack.undo()
        assert len(doc) == 3

    def test_split(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eid = doc.events[0].eid
        stack.run(SplitEvent(eid, text_pos=3))
        assert len(doc) == 4
        assert doc.events[0].text == "Пер"
        assert doc.events[1].text == "вая"
        assert doc.events[0].end == doc.events[1].start
        stack.undo()
        assert len(doc) == 3
        assert doc.events[0].text == "Первая"
        assert doc.events[0].end == 3000

    def test_merge(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eids = [doc.events[0].eid, doc.events[1].eid]
        stack.run(MergeEvents(eids))
        assert len(doc) == 2
        assert doc.events[0].text == r"Первая\NВторая"
        assert doc.events[0].start == 1000
        assert doc.events[0].end == 6000
        stack.undo()
        assert len(doc) == 3
        assert doc.events[0].text == "Первая"

    def test_merge_needs_two(self) -> None:
        with pytest.raises(ValueError, match="минимум два"):
            MergeEvents([1])

    def test_toggle_comment_is_self_inverse(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eid = doc.events[0].eid
        stack.run(ToggleComment([eid]))
        assert doc.by_eid(eid).comment is True
        stack.undo()
        assert doc.by_eid(eid).comment is False


class TestUndoStack:
    def test_redo_cleared_by_new_command(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        eid = doc.events[0].eid
        stack.run(SetText(eid, "A"))
        stack.undo()
        assert stack.can_redo
        stack.run(SetText(doc.events[1].eid, "B"))
        assert not stack.can_redo

    def test_transaction_is_one_undo(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        with stack.transaction("Массовая правка"):
            for event in doc.events:
                stack.run(SetText(event.eid, "X"))
        assert stack.depth_used == 1
        assert stack.undo_label == "Массовая правка"
        stack.undo()
        assert [e.text for e in doc.events] == ["Первая", "Вторая", r"{\b1}Третья"]

    def test_failed_transaction_rolls_back(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        with pytest.raises(RuntimeError, match="сбой"), stack.transaction("Ошибочная"):
            stack.run(SetText(doc.events[0].eid, "X"))
            raise RuntimeError("сбой посреди транзакции")
        assert doc.events[0].text == "Первая"
        assert stack.depth_used == 0

    def test_nested_transaction_rejected(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        with pytest.raises(RuntimeError, match="вложенные"), stack.transaction("A"):
            with stack.transaction("B"):
                pass

    def test_clean_flag_tracks_saves(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        assert stack.is_clean
        stack.run(SetText(doc.events[0].eid, "A"))
        assert not stack.is_clean
        stack.mark_clean()
        assert stack.is_clean
        stack.undo()
        assert not stack.is_clean

    def test_depth_limit(self, doc: SubtitleDocument) -> None:
        stack = UndoStack(doc, depth=5)
        for i in range(20):
            # Разные события, чтобы не сработало слияние.
            stack.run(SetText(doc.events[i % 3].eid, f"текст {i}"))
        assert stack.depth_used <= 5

    def test_on_change_receives_touched_eids(self, doc: SubtitleDocument, stack: UndoStack) -> None:
        seen: list[frozenset[int]] = []
        stack.on_change = lambda cs: seen.append(cs.touched)
        eid = doc.events[0].eid
        stack.run(SetText(eid, "A"))
        assert seen == [frozenset({eid})]


def test_full_roundtrip_through_serialization(doc: SubtitleDocument, stack: UndoStack) -> None:
    """Серия разнородных команд, столько же отмен — файл обязан совпасть."""
    before = write_ass(doc)
    eids = [e.eid for e in doc.events]

    stack.run(SetText(eids[0], "Новый текст"))
    stack.run(SetPosition(eids[0], 500, 600))
    stack.run(SetTiming(eids[1], start=4500, end=6500))
    stack.run(ShiftTimes(eids, 250))
    stack.run(DuplicateEvents([eids[2]]))
    stack.run(SplitEvent(eids[1], text_pos=2))
    stack.run(DeleteEvents([eids[0]]))
    stack.run(ToggleComment([eids[2]]))

    assert write_ass(doc) != before
    while stack.can_undo:
        stack.undo()
    assert write_ass(doc) == before
