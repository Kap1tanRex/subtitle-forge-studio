"""Тесты нарезки распознанной речи на реплики."""

from __future__ import annotations

import itertools

import pytest

from sfstudio.services.asr.base import Segment, WordTiming
from sfstudio.services.asr.segmentation import (
    SegmentationRules,
    split_segment,
    to_events,
    wrap_text,
)
from sfstudio.services.qc import PROFILES

RULES = SegmentationRules()


def words(*items: tuple[int, int, str]) -> tuple[WordTiming, ...]:
    return tuple(WordTiming(start, end, text) for start, end, text in items)


class TestNoSplitNeeded:
    def test_short_segment_is_kept(self) -> None:
        segment = Segment(0, 2000, "Короткая реплика")
        assert len(split_segment(segment, RULES)) == 1

    def test_whitespace_is_normalised(self) -> None:
        segment = Segment(0, 2000, "  много   пробелов  ")
        assert split_segment(segment, RULES)[0].text == "много пробелов"

    def test_empty_segment_disappears(self) -> None:
        assert split_segment(Segment(0, 2000, "   "), RULES) == []


class TestSplitTriggers:
    def test_too_long_in_time(self) -> None:
        segment = Segment(0, 20_000, "Первое предложение. Второе предложение.")
        assert len(split_segment(segment, RULES)) > 1

    def test_too_many_characters(self) -> None:
        text = "слово " * 40
        segment = Segment(0, 6000, text)
        assert len(split_segment(segment, RULES)) > 1

    def test_fast_speech_is_not_chopped(self) -> None:
        """Скорость чтения делением не лечится — она от него не меняется.

        Текст делится пополам, но и время делится пополам вместе с ним.
        Рекурсия по этому признаку не сходится: на настоящей расшифровке
        фраза «или как мы там её обозвали» превращалась в шесть реплик по
        одному слову, наложенных друг на друга.
        """
        segment = Segment(0, 1000, "Довольно длинная фраза, которую не успеть")
        assert len(split_segment(segment, RULES)) == 1

    def test_short_segment_is_not_halved(self) -> None:
        """Из секундной реплики две по полсекунды — два мелькнувших огрызка."""
        segment = Segment(0, 1200, "слово " * 20)
        assert len(split_segment(segment, RULES)) == 1

    def test_parts_fit_the_rules(self) -> None:
        segment = Segment(0, 18_000, "Первая часть речи. Вторая часть речи. Третья часть.")
        for part in split_segment(segment, RULES):
            assert part.duration <= RULES.max_duration_ms
            assert len(part.text) <= RULES.max_chars


class TestBreakChoice:
    def test_sentence_end_is_preferred(self) -> None:
        segment = Segment(0, 12_000, "Первое предложение. Второе предложение здесь")
        parts = split_segment(segment, RULES)
        assert parts[0].text.endswith(".")

    def test_comma_used_when_no_sentence_end(self) -> None:
        segment = Segment(0, 12_000, "Первая половина фразы, вторая половина фразы")
        parts = split_segment(segment, RULES)
        assert parts[0].text.endswith(",")

    def test_pause_beats_punctuation(self) -> None:
        """Пауза в речи — более верная граница, чем знак в расшифровке."""
        segment = Segment(
            0, 12_000,
            "Первое предложение. Второе предложение здесь",
            words=words(
                (0, 1500, "Первое"), (1500, 3000, "предложение."),
                (9000, 10_000, "Второе"), (10_000, 11_000, "предложение"),
                (11_000, 12_000, "здесь"),
            ),
        )
        parts = split_segment(segment, RULES)
        assert len(parts) >= 2
        # Разрез приходится на паузу 3000..9000, а не на середину текста.
        assert parts[0].end <= 3100

    def test_never_splits_inside_a_word(self) -> None:
        for part in split_segment(Segment(0, 20_000, "слово " * 30), RULES):
            assert "слово" in part.text
            assert not part.text.startswith("лово")

    def test_single_long_word_survives_whole(self) -> None:
        """Резать посреди слова хуже, чем нарушить норму длины."""
        long_word = "а" * 120
        parts = split_segment(Segment(0, 9000, long_word), RULES)
        assert len(parts) == 1
        assert parts[0].text == long_word

    def test_break_is_near_the_middle(self) -> None:
        """Разрез в начале оставил бы огрызок и всё ту же длинную половину."""
        text = "Раз. " + "слово " * 30
        parts = split_segment(Segment(0, 16_000, text.strip()), RULES)
        first = len(parts[0].text)
        assert first > 10


class TestTiming:
    def test_parts_stay_inside_the_original(self) -> None:
        segment = Segment(1000, 15_000, "Первая часть речи. Вторая часть речи здесь.")
        parts = split_segment(segment, RULES)
        assert parts[0].start >= 1000
        assert parts[-1].end <= 15_000

    def test_parts_do_not_overlap(self) -> None:
        segment = Segment(0, 18_000, "Раз предложение. Два предложение. Три предложение.")
        parts = split_segment(segment, RULES)
        for previous, current in itertools.pairwise(parts):
            assert current.start >= previous.end

    def test_word_timings_drive_the_boundary(self) -> None:
        segment = Segment(
            0, 10_000, "Раз два три четыре",
            words=words((0, 900, "Раз"), (900, 1800, "два"),
                        (6000, 7000, "три"), (7000, 8000, "четыре")),
        )
        parts = split_segment(segment, SegmentationRules(max_duration_ms=5000))
        assert parts[0].end == pytest.approx(1800, abs=200)

    def test_duration_is_positive_everywhere(self) -> None:
        parts = split_segment(Segment(0, 20_000, "слово " * 40), RULES)
        assert all(part.duration > 0 for part in parts)


class TestWrapping:
    def test_short_text_is_not_wrapped(self) -> None:
        assert "\\N" not in wrap_text("Короткая строка", RULES)

    def test_long_text_gets_a_break(self) -> None:
        text = "слово " * 12
        assert "\\N" in wrap_text(text.strip(), RULES)

    def test_lines_respect_the_limit(self) -> None:
        wrapped = wrap_text("слово " * 12, RULES)
        assert all(len(line) <= RULES.max_line_length for line in wrapped.split("\\N"))

    def test_line_count_is_capped(self) -> None:
        wrapped = wrap_text("слово " * 40, SegmentationRules(max_lines=2))
        assert len(wrapped.split("\\N")) <= 2

    def test_no_word_is_lost(self) -> None:
        source = "один два три четыре пять шесть семь восемь девять десять"
        wrapped = wrap_text(source, SegmentationRules(max_line_length=20))
        assert wrapped.replace("\\N", " ").split() == source.split()

    def test_lines_are_balanced(self) -> None:
        """Две строки по 30 читаются легче, чем 42 и 18."""
        source = "слово " * 11
        lines = wrap_text(source.strip(), RULES).split("\\N")
        assert len(lines) == 2
        assert abs(len(lines[0]) - len(lines[1])) < 25


class TestToEvents:
    def test_produces_ready_replicas(self) -> None:
        segments = [Segment(0, 20_000, "Первое предложение. Второе предложение здесь.")]
        events = to_events(segments, RULES)
        assert len(events) > 1
        assert all(event.text for event in events)

    def test_gap_is_enforced_between_segments(self) -> None:
        """Движки не гарантируют непересечения соседних сегментов."""
        segments = [Segment(0, 3000, "Первая"), Segment(2500, 6000, "Вторая")]
        events = to_events(segments, RULES)
        assert events[1].start >= events[0].end + RULES.gap_ms

    def test_short_replica_is_extended(self) -> None:
        events = to_events([Segment(0, 100, "Да")], RULES)
        assert events[0].duration >= RULES.min_duration_ms

    def test_fast_replica_is_stretched_into_the_pause(self) -> None:
        """Быстрая речь лечится временем: занимаем паузу до следующей реплики."""
        segments = [
            Segment(0, 1000, "Довольно длинная фраза, которую не успеть"),
            Segment(9000, 11_000, "Следующая"),
        ]
        events = to_events(segments, RULES)
        assert events[0].duration > 1000

    def test_stretching_does_not_touch_the_neighbour(self) -> None:
        segments = [
            Segment(0, 1000, "Довольно длинная фраза, которую не успеть"),
            Segment(2000, 4000, "Следующая"),
        ]
        events = to_events(segments, RULES)
        assert events[0].end <= events[1].start - RULES.gap_ms
        assert events[1].start == 2000

    def test_last_replica_can_be_stretched_freely(self) -> None:
        events = to_events([Segment(0, 500, "Довольно длинная фраза тут")], RULES)
        assert events[0].duration > 500

    def test_empty_segments_are_dropped(self) -> None:
        assert to_events([Segment(0, 1000, "   ")], RULES) == []

    def test_wrapping_can_be_disabled(self) -> None:
        events = to_events([Segment(0, 5000, "слово " * 12)], RULES, wrap=False)
        assert all("\\N" not in event.text for event in events)

    def test_order_is_preserved(self) -> None:
        segments = [Segment(0, 2000, "Раз"), Segment(3000, 5000, "Два"),
                    Segment(6000, 8000, "Три")]
        events = to_events(segments, RULES)
        assert [e.text for e in events] == ["Раз", "Два", "Три"]


class TestRulesFromProfile:
    def test_takes_thresholds_from_qc(self) -> None:
        rules = SegmentationRules.from_qc_profile(PROFILES["netflix-ru"])
        profile = PROFILES["netflix-ru"]
        assert rules.max_cps == profile.max_cps
        assert rules.max_line_length == profile.max_line_length

    def test_disabled_rule_gets_a_soft_value(self) -> None:
        """«Правило выключено» не значит «резать не надо»."""
        from sfstudio.services.qc import QcProfile

        rules = SegmentationRules.from_qc_profile(
            QcProfile(name="Свободный", max_cps=None, max_line_length=None,
                      max_lines=None, min_duration_ms=None, max_duration_ms=None)
        )
        assert rules.max_cps > 0
        assert rules.max_line_length > 0
        assert rules.max_duration_ms > 0
