"""Выравнивание готового текста по речи.

Распознавание здесь подменено: настоящее в тестах не запускается — оно
требует модель, звук и минуты времени. Проверяется то, что пишем мы сами:
сопоставление слов, раскладка времени между опорами и честность оценки.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import pytest

from sfstudio.services.alignment import (
    MIN_DURATION_MS,
    AlignmentPlan,
    align,
    normalise,
    words_of,
)


@dataclass
class Word:
    """То, что отдаёт распознавание: слово со временем."""

    start: int
    end: int
    text: str


@dataclass
class Event:
    """Реплика документа — нужны только ``eid`` и ``text``."""

    eid: int
    text: str


def speech(pairs) -> list[Word]:
    """Слова подряд по 400 мс каждое, начиная с указанного времени."""
    words = []
    for start, text in pairs:
        for offset, piece in enumerate(text.split()):
            at = start + offset * 400
            words.append(Word(at, at + 400, piece))
    return words


class TestWords:
    def test_case_and_punctuation_do_not_matter(self) -> None:
        assert normalise("Привет!") == normalise("привет")

    def test_yo_matches_ye(self) -> None:
        """«Ещё» и «еще» — одно слово. Распознавание пишет то так, то этак."""
        assert normalise("Ещё") == normalise("еще")

    def test_markup_is_not_a_word(self) -> None:
        assert words_of(r"{\i1}Привет{\i0}, мир") == ["привет", "мир"]

    def test_line_break_separates_words(self) -> None:
        assert words_of(r"Первая\Nвторая") == ["первая", "вторая"]

    def test_text_without_words_gives_nothing(self) -> None:
        assert words_of(r"{\pos(10,10)}") == []


class TestExactMatch:
    def plan(self) -> AlignmentPlan:
        events = [Event(1, "Первая реплика"), Event(2, "Вторая реплика тут")]
        words = speech([(1000, "первая реплика"), (5000, "вторая реплика тут")])
        return align(events, words)

    def test_every_event_gets_a_time(self) -> None:
        assert len(self.plan()) == 2

    def test_time_comes_from_the_words(self) -> None:
        change = self.plan().changes[0]
        assert change.start == 1000
        assert change.end == 1800

    def test_full_match_is_confident(self) -> None:
        assert all(change.confidence == 1.0 for change in self.plan().changes)

    def test_nothing_is_flagged_for_checking(self) -> None:
        assert not self.plan().shaky

    def test_coverage_is_total(self) -> None:
        assert self.plan().coverage == 1.0

    def test_punctuation_in_the_document_does_not_break_matching(self) -> None:
        """Перевод приходит с пунктуацией, распознавание — почти без неё."""
        events = [Event(1, "Привет, мир!")]
        words = speech([(2000, "привет мир")])
        assert align(events, words).changes[0].start == 2000


class TestGaps:
    """Реплика, которую распознавание не услышало."""

    def plan(self) -> AlignmentPlan:
        events = [
            Event(1, "первая"),
            Event(2, "неразборчивая середина"),
            Event(3, "третья"),
        ]
        words = speech([(1000, "первая"), (9000, "третья")])
        return align(events, words)

    def test_middle_event_still_gets_a_time(self) -> None:
        middle = self.plan().changes[1]
        assert middle.start >= 1400
        assert middle.end <= 9000

    def test_guessed_time_is_marked(self) -> None:
        assert self.plan().changes[1].guessed

    def test_guessed_time_is_flagged_for_checking(self) -> None:
        """Ровный, но выдуманный тайминг никто не станет перепроверять."""
        assert self.plan().changes[1] in self.plan().shaky

    def test_anchored_neighbours_keep_their_time(self) -> None:
        changes = self.plan().changes
        assert changes[0].start == 1000
        assert changes[2].start == 9000

    def test_longer_text_gets_a_longer_share(self) -> None:
        events = [Event(1, "первая"), Event(2, "два"), Event(3, "раз два три четыре"),
                  Event(4, "последняя")]
        words = speech([(0, "первая"), (20000, "последняя")])
        changes = align(events, words).changes
        short = changes[1].end - changes[1].start
        long = changes[2].end - changes[2].start
        assert long > short

    def test_tail_without_an_anchor_is_laid_out_forward(self) -> None:
        events = [Event(1, "первая"), Event(2, "хвост без опоры")]
        words = speech([(1000, "первая")])
        changes = align(events, words).changes
        assert changes[1].start >= changes[0].end
        assert changes[1].end > changes[1].start

    def test_nothing_recognised_at_all(self) -> None:
        events = [Event(1, "первая"), Event(2, "вторая")]
        words = speech([(0, "совершенно другие слова")])
        plan = align(events, words)
        assert all(change.guessed for change in plan.changes)
        assert plan.coverage == 0.0


class TestConfidence:
    def test_partial_match_lowers_confidence(self) -> None:
        events = [Event(1, "раз два три четыре")]
        words = speech([(1000, "раз два")])
        change = align(events, words).changes[0]
        assert change.confidence == pytest.approx(0.5)

    def test_low_confidence_is_flagged(self) -> None:
        events = [Event(1, "раз два три четыре")]
        words = speech([(1000, "раз")])
        assert align(events, words).changes[0].shaky

    def test_coverage_counts_the_whole_text(self) -> None:
        events = [Event(1, "раз два"), Event(2, "три четыре")]
        words = speech([(1000, "раз два"), (5000, "совсем другое")])
        assert align(events, words).coverage == pytest.approx(0.5)


class TestOrder:
    """Слово из середины текста не должно привязаться к концу дорожки."""

    def scene(self):
        # Короткое «да» произнесено ещё раз в самом конце фильма. Соблазн
        # привязаться к нему велик: слово совпадает буквально.
        events = [Event(1, "первая"), Event(2, "да"), Event(3, "третья"),
                  Event(4, "четвёртая")]
        words = speech([
            (1000, "первая"), (2000, "третья"), (3000, "четвертая"), (600_000, "да"),
        ])
        return events, words

    def test_a_late_repeat_does_not_drag_the_rest(self) -> None:
        """Иначе всё, что идёт после, уезжает за ошибочную привязку."""
        changes = align(*self.scene()).changes
        assert changes[3].end < 100_000

    def test_the_unmatched_word_becomes_a_guess(self) -> None:
        assert align(*self.scene()).changes[1].guessed

    def test_extra_words_in_the_recognition_do_not_shift_anchors(self) -> None:
        """Распознавание слышит и то, чего в переводе нет: шум, оговорки."""
        events = [Event(1, "первая реплика"), Event(2, "вторая реплика")]
        words = speech([
            (1000, "первая реплика"), (3000, "э ну вот"), (5000, "вторая реплика"),
        ])
        changes = align(events, words).changes
        assert changes[0].start == 1000
        assert changes[1].start == 5000


class TestPlayableResult:
    def events_and_words(self):
        events = [Event(index, f"реплика{index}") for index in range(1, 6)]
        words = speech([(index * 300, f"реплика{index}") for index in range(1, 6)])
        return events, words

    def test_events_do_not_overlap(self) -> None:
        """Слова распознавания перехлёстываются чаще, чем кажется."""
        changes = align(*self.events_and_words()).changes
        for earlier, later in pairwise(changes):
            assert later.start >= earlier.end

    def test_no_event_is_shorter_than_the_minimum(self) -> None:
        changes = align(*self.events_and_words()).changes
        assert all(c.end - c.start >= MIN_DURATION_MS for c in changes)

    def test_time_never_goes_negative(self) -> None:
        events = [Event(1, "начало")]
        words = [Word(-500, 200, "начало")]
        assert align(events, words).changes[0].start >= 0

    def test_order_matches_the_document(self) -> None:
        changes = align(*self.events_and_words()).changes
        assert [change.eid for change in changes] == [1, 2, 3, 4, 5]


class TestNothingToDo:
    def test_no_events(self) -> None:
        assert align([], speech([(0, "речь")])).is_empty

    def test_no_words(self) -> None:
        assert align([Event(1, "текст")], []).is_empty

    def test_empty_words_from_recognition(self) -> None:
        """Движок может отдать слова без текста — пустые метки времени."""
        assert align([Event(1, "текст")], [Word(0, 100, "")]).is_empty

    def test_event_without_words_is_skipped_not_moved(self) -> None:
        """Раздвигать соседей ради реплики без текста нечестно."""
        events = [Event(1, "первая"), Event(2, r"{\pos(1,1)}"), Event(3, "третья")]
        plan = align(events, speech([(1000, "первая"), (5000, "третья")]))
        assert plan.skipped == [2]
        assert 2 not in plan.as_mapping()


class TestSummary:
    def test_empty_plan_says_so(self) -> None:
        assert "нечего" in align([], []).summary()

    def test_summary_counts_and_warns(self) -> None:
        events = [Event(1, "первая"), Event(2, "неуслышанная"), Event(3, "третья")]
        plan = align(events, speech([(1000, "первая"), (9000, "третья")]))
        text = plan.summary()
        assert "3 реплики" in text
        assert "проверки" in text

    def test_mapping_covers_every_change(self) -> None:
        events = [Event(1, "первая"), Event(2, "вторая")]
        plan = align(events, speech([(1000, "первая"), (5000, "вторая")]))
        assert set(plan.as_mapping()) == {1, 2}
