"""Тесты автоматической доводки таймингов."""

from __future__ import annotations

from fractions import Fraction
from itertools import pairwise

import pytest

from sfstudio.core.commands import ApplyTimings
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import FpsModel
from sfstudio.core.undo import UndoStack
from sfstudio.media.keyframes import KeyframeIndex
from sfstudio.services.autotiming import TimingSettings, build_plan

FPS = FpsModel(Fraction(24, 1))
FRAME_MS = 1000 / 24


def make(*specs: tuple[int, int, str]) -> SubtitleDocument:
    doc = SubtitleDocument.blank()
    for start, end, text in specs:
        doc.create_event(start, end, text)
    return doc


def plan_for(doc: SubtitleDocument, settings: TimingSettings, **kwargs):
    return build_plan(doc.events, settings, all_events=doc.events, **kwargs)


def new_times(plan, doc: SubtitleDocument, index: int) -> tuple[int, int]:
    eid = doc.events[index].eid
    mapping = plan.as_mapping()
    if eid in mapping:
        return mapping[eid]
    event = doc.events[index]
    return (event.start, event.end)


class TestEmptySettings:
    def test_nothing_selected_changes_nothing(self) -> None:
        doc = make((1000, 2000, "Текст"))
        plan = plan_for(doc, TimingSettings())
        assert plan.is_empty
        assert plan.summary() == "Ничего менять не потребовалось"

    def test_no_events(self) -> None:
        doc = SubtitleDocument.blank()
        assert build_plan([], TimingSettings(lead_in_ms=100)).is_empty
        assert doc is not None


class TestLead:
    def test_lead_in_moves_start_earlier(self) -> None:
        doc = make((5000, 6000, "Текст"))
        plan = plan_for(doc, TimingSettings(lead_in_ms=120, min_gap_frames=0))
        start, _ = new_times(plan, doc, 0)
        assert start == 4880

    def test_lead_out_extends_end(self) -> None:
        doc = make((5000, 6000, "Текст"))
        plan = plan_for(doc, TimingSettings(lead_out_ms=300, min_gap_frames=0))
        _, end = new_times(plan, doc, 0)
        assert end == 6300

    def test_start_never_goes_negative(self) -> None:
        doc = make((50, 1000, "Текст"))
        plan = plan_for(doc, TimingSettings(lead_in_ms=500, min_gap_frames=0))
        start, _ = new_times(plan, doc, 0)
        assert start >= 0


class TestMinDuration:
    def test_stretches_short_event(self) -> None:
        doc = make((1000, 1200, "Миг"))
        plan = plan_for(doc, TimingSettings(min_duration_ms=1000, min_gap_frames=0))
        start, end = new_times(plan, doc, 0)
        assert end - start >= 1000

    def test_stretches_forward_not_backward(self) -> None:
        """Двигать начало назад нельзя — реплика разойдётся с речью."""
        doc = make((5000, 5200, "Миг"))
        plan = plan_for(doc, TimingSettings(min_duration_ms=1000, min_gap_frames=0))
        start, _ = new_times(plan, doc, 0)
        assert start == 5000

    def test_long_event_untouched(self) -> None:
        doc = make((1000, 5000, "Долгая"))
        plan = plan_for(doc, TimingSettings(min_duration_ms=1000))
        assert plan.is_empty


class TestTargetCps:
    def test_stretches_fast_event(self) -> None:
        doc = make((1000, 1100, "Очень много символов в этой реплике подряд"))
        plan = plan_for(doc, TimingSettings(target_cps=17.0, min_gap_frames=0))
        start, end = new_times(plan, doc, 0)
        length = len(doc.events[0].plain.replace(" ", ""))
        assert length * 1000 / (end - start) == pytest.approx(17.0, rel=0.05)

    def test_comfortable_event_untouched(self) -> None:
        doc = make((1000, 6000, "Коротко"))
        plan = plan_for(doc, TimingSettings(target_cps=17.0))
        assert plan.is_empty

    def test_empty_text_is_skipped(self) -> None:
        doc = make((1000, 1100, ""))
        plan = plan_for(doc, TimingSettings(target_cps=17.0))
        assert plan.is_empty


class TestCloseGaps:
    def test_small_gap_is_closed(self) -> None:
        doc = make((1000, 2000, "Первая"), (2080, 3000, "Вторая"))
        plan = plan_for(doc, TimingSettings(close_gap_below_ms=200, min_gap_frames=0))
        _, first_end = new_times(plan, doc, 0)
        second_start, _ = new_times(plan, doc, 1)
        assert first_end == second_start

    def test_real_pause_is_kept(self) -> None:
        """Настоящую паузу в диалоге смыкать нельзя."""
        doc = make((1000, 2000, "Первая"), (5000, 6000, "Вторая"))
        plan = plan_for(doc, TimingSettings(close_gap_below_ms=200))
        assert plan.is_empty

    def test_overlap_is_not_treated_as_gap(self) -> None:
        doc = make((1000, 3000, "Первая"), (2000, 4000, "Вторая"))
        plan = plan_for(doc, TimingSettings(close_gap_below_ms=200, min_gap_frames=0))
        _, first_end = new_times(plan, doc, 0)
        assert first_end <= 3000


class TestKeyframes:
    def test_snaps_to_nearby_keyframe(self) -> None:
        doc = make((5000, 6000, "Текст"))
        keyframes = KeyframeIndex(times_ms=[5042])
        plan = plan_for(
            doc,
            TimingSettings(snap_to_keyframes=True, keyframe_radius_frames=5, min_gap_frames=0),
            fps=FPS, keyframes=keyframes,
        )
        start, _ = new_times(plan, doc, 0)
        assert start == 5042

    def test_far_keyframe_is_ignored(self) -> None:
        doc = make((5000, 6000, "Текст"))
        keyframes = KeyframeIndex(times_ms=[9000])
        plan = plan_for(
            doc,
            TimingSettings(snap_to_keyframes=True, keyframe_radius_frames=5),
            fps=FPS, keyframes=keyframes,
        )
        assert plan.is_empty

    def test_radius_depends_on_fps(self) -> None:
        """Пять кадров при 24 и при 60 fps — разное время."""
        doc = make((5000, 6000, "Текст"))
        keyframes = KeyframeIndex(times_ms=[5150])
        settings = TimingSettings(
            snap_to_keyframes=True, keyframe_radius_frames=5, min_gap_frames=0
        )
        slow = plan_for(doc, settings, fps=FpsModel(Fraction(24, 1)), keyframes=keyframes)
        fast = plan_for(doc, settings, fps=FpsModel(Fraction(60, 1)), keyframes=keyframes)
        assert not slow.is_empty, "при 24 fps радиус 208 мс — должно притянуть"
        assert fast.is_empty, "при 60 fps радиус 83 мс — притягивать не должно"

    def test_frame_grid_does_not_undo_keyframe_snap(self) -> None:
        """Округление к сетке не должно сбивать точное попадание на склейку.

        Ключевой кадр — это и есть кадр; повторное округление может только
        сдвинуть границу с уже правильного места.
        """
        doc = make((5000, 6000, "Текст"))
        keyframes = KeyframeIndex(times_ms=[5042])
        plan = plan_for(
            doc,
            TimingSettings(
                snap_to_keyframes=True, keyframe_radius_frames=5,
                snap_to_frames=True, min_gap_frames=0,
            ),
            fps=FPS, keyframes=keyframes,
        )
        start, _ = new_times(plan, doc, 0)
        assert start == 5042

    def test_no_keyframes_is_safe(self) -> None:
        doc = make((5000, 6000, "Текст"))
        plan = plan_for(doc, TimingSettings(snap_to_keyframes=True), fps=FPS)
        assert plan.is_empty


class TestFrameGrid:
    def test_start_snaps_to_frame_boundary(self) -> None:
        doc = make((5017, 6000, "Текст"))
        plan = plan_for(doc, TimingSettings(snap_to_frames=True, min_gap_frames=0), fps=FPS)
        start, _ = new_times(plan, doc, 0)
        assert start % round(FRAME_MS) <= 1 or abs(start - 5000) <= 1

    def test_needs_fps(self) -> None:
        doc = make((5017, 6000, "Текст"))
        plan = plan_for(doc, TimingSettings(snap_to_frames=True))
        assert plan.is_empty, "без FPS сетки нет — округлять не по чему"


class TestNeighbourConflicts:
    def test_expansion_stops_before_neighbour(self) -> None:
        doc = make((1000, 2000, "Первая"), (2200, 3000, "Вторая"))
        plan = plan_for(doc, TimingSettings(lead_out_ms=1000, min_gap_frames=2), fps=FPS)
        _, first_end = new_times(plan, doc, 0)
        second_start, _ = new_times(plan, doc, 1)
        assert first_end <= second_start

    def test_minimum_gap_is_respected(self) -> None:
        doc = make((1000, 2000, "Первая"), (2200, 3000, "Вторая"))
        plan = plan_for(doc, TimingSettings(lead_out_ms=1000, min_gap_frames=2), fps=FPS)
        _, first_end = new_times(plan, doc, 0)
        second_start, _ = new_times(plan, doc, 1)
        assert second_start - first_end >= round(2 * FRAME_MS) - 2

    def test_events_outside_selection_are_not_moved(self) -> None:
        """Выделили одну реплику — соседние двигать нельзя."""
        doc = make((1000, 2000, "Выбрана"), (2200, 3000, "Не выбрана"))
        plan = build_plan(
            [doc.events[0]],
            TimingSettings(lead_out_ms=2000, min_gap_frames=2),
            fps=FPS, all_events=doc.events,
        )
        assert doc.events[1].eid not in plan.as_mapping()
        _, first_end = new_times(plan, doc, 0)
        assert first_end <= doc.events[1].start


class TestUnresolved:
    def test_reports_events_without_room(self) -> None:
        """Реплика, которую не удалось дотянуть, должна быть названа.

        Иначе пользователь решит, что доводка отработала, и не заметит
        оставшихся слишком быстрых реплик.
        """
        doc = make((1000, 1100, "Коротко"), (1400, 3000, "Следующая"))
        plan = plan_for(
            doc, TimingSettings(min_duration_ms=2000, min_gap_frames=2), fps=FPS
        )
        assert doc.events[0].eid in plan.unresolved
        assert "не хватило места" in plan.summary()

    def test_no_complaint_when_room_is_enough(self) -> None:
        doc = make((1000, 1100, "Коротко"), (9000, 10_000, "Далеко"))
        plan = plan_for(doc, TimingSettings(min_duration_ms=2000, min_gap_frames=2), fps=FPS)
        assert not plan.unresolved


class TestOrderMatters:
    def test_gap_closing_runs_after_expansion(self) -> None:
        """Сначала расширяем, потом смыкаем — иначе зазор появится заново."""
        doc = make((1000, 2000, "Первая"), (2500, 3500, "Вторая"))
        plan = plan_for(
            doc,
            TimingSettings(lead_out_ms=300, close_gap_below_ms=250, min_gap_frames=0),
        )
        _, first_end = new_times(plan, doc, 0)
        second_start, _ = new_times(plan, doc, 1)
        assert first_end == second_start, "зазор 200 мс после lead-out должен сомкнуться"

    def test_all_steps_together_produce_valid_document(self) -> None:
        doc = make(
            (1000, 1200, "Короткая"),
            (2000, 2100, "Очень много букв для такого короткого срока"),
            (3000, 4000, "Обычная"),
            (4080, 5000, "После мелкого зазора"),
        )
        settings = TimingSettings(
            lead_in_ms=120, lead_out_ms=300, close_gap_below_ms=200,
            min_duration_ms=1000, target_cps=17.0,
            snap_to_frames=True, min_gap_frames=2,
        )
        plan = plan_for(doc, settings, fps=FPS)
        mapping = plan.as_mapping()

        times = []
        for event in doc.events:
            start, end = mapping.get(event.eid, (event.start, event.end))
            times.append((start, end))
            assert end > start, "длительность обязана остаться положительной"
        for (_, end), (start, _) in pairwise(times):
            assert start >= end, "реплики не должны перекрываться после доводки"


class TestApplyCommand:
    def test_applies_and_reverts_as_one_step(self) -> None:
        doc = make((1000, 1200, "Первая"), (5000, 6000, "Вторая"))
        before = [(e.start, e.end) for e in doc.events]
        plan = plan_for(doc, TimingSettings(lead_out_ms=200, min_gap_frames=0))

        undo = UndoStack(doc)
        undo.run(ApplyTimings(plan.as_mapping()))
        assert undo.depth_used == 1
        assert [(e.start, e.end) for e in doc.events] != before

        undo.undo()
        assert [(e.start, e.end) for e in doc.events] == before

    def test_index_sees_new_timings(self) -> None:
        doc = make((1000, 2000, "Текст"))
        plan = plan_for(doc, TimingSettings(lead_out_ms=3000, min_gap_frames=0))
        UndoStack(doc).run(ApplyTimings(plan.as_mapping()))
        assert doc.active_at(4000) != []

    def test_missing_event_is_skipped(self) -> None:
        doc = make((1000, 2000, "Текст"))
        undo = UndoStack(doc)
        undo.run(ApplyTimings({999: (0, 100)}))  # не должно падать
        assert len(doc) == 1


class TestNeverShortens:
    """Доводка не должна укорачивать то, что было.

    Соседи расширяются навстречу друг другу, и без защиты расширение одной
    реплики отъедало бы конец у предыдущей: пользователь просил «сделать
    длиннее», а часть реплик становилась короче.
    """

    def test_expansion_of_neighbour_does_not_shorten_previous(self) -> None:
        doc = make((3000, 4000, "Первая"), (4200, 5000, "Вторая"))
        plan = plan_for(doc, TimingSettings(lead_in_ms=300, min_gap_frames=2), fps=FPS)
        _, first_end = new_times(plan, doc, 0)
        assert first_end >= 4000, "конец первой не должен уехать раньше исходного"

    def test_no_event_gets_shorter(self) -> None:
        doc = make(
            (1000, 1200, "Короткая"),
            (2000, 2100, "Очень много букв для такого короткого срока"),
            (3000, 4000, "Обычная"),
            (4080, 5000, "После зазора"),
        )
        settings = TimingSettings(
            lead_in_ms=120, lead_out_ms=300, close_gap_below_ms=200,
            min_duration_ms=1000, target_cps=17.0,
            snap_to_frames=True, min_gap_frames=2,
        )
        plan = plan_for(doc, settings, fps=FPS)
        for change in plan.changes:
            assert change.delta_duration >= -1, (
                f"реплика {change.eid} укоротилась на {-change.delta_duration} мс"
            )

    def test_original_overlap_may_still_be_trimmed(self) -> None:
        """Если реплики налезали друг на друга изначально, подрезать можно."""
        doc = make((1000, 3000, "Первая"), (2000, 4000, "Вторая"))
        plan = plan_for(doc, TimingSettings(lead_out_ms=100, min_gap_frames=2), fps=FPS)
        mapping = plan.as_mapping()
        first = mapping.get(doc.events[0].eid, (1000, 3000))
        second = mapping.get(doc.events[1].eid, (2000, 4000))
        assert first[1] <= second[0], "исходное перекрытие должно быть устранено"
