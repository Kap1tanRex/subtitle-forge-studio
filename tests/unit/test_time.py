"""Тесты времени, кадров и таймкодов."""

from __future__ import annotations

from fractions import Fraction

import pytest

from sfstudio.core import time as tm


class TestFpsModel:
    def test_exact_ntsc_fraction(self) -> None:
        fps = tm.FpsModel(tm.FPS_NTSC_FILM)
        assert fps.rate == Fraction(24000, 1001)
        assert fps.is_ntsc
        assert abs(fps.as_float - 23.976) < 0.001

    def test_from_float_snaps_to_known(self) -> None:
        """ffmpeg отдаёт 23.976023976 — должно стать точной дробью, а не мусором."""
        assert tm.FpsModel.from_float(23.976023976).rate == Fraction(24000, 1001)
        assert tm.FpsModel.from_float(29.97).rate == Fraction(30000, 1001)
        assert tm.FpsModel.from_float(25.0).rate == Fraction(25, 1)
        assert tm.FpsModel.from_float(60.0).rate == Fraction(60, 1)

    def test_frame_of_and_back(self) -> None:
        fps = tm.FpsModel(Fraction(25, 1))
        assert fps.frame_of(0) == 0
        assert fps.frame_of(39) == 0
        assert fps.frame_of(40) == 1
        assert fps.frame_start_ms(1) == 40
        assert fps.frame_start_ms(25) == 1000

    def test_no_drift_over_two_hours(self) -> None:
        """Точная дробь не накапливает ошибку — ради этого и не используется float."""
        fps = tm.FpsModel(tm.FPS_NTSC_FILM)
        two_hours_ms = 2 * 3600 * 1000
        frame = fps.frame_of(two_hours_ms)
        assert abs(fps.frame_start_ms(frame) - two_hours_ms) < 42

    def test_snap_modes(self) -> None:
        fps = tm.FpsModel(Fraction(25, 1))  # кадр = 40 мс
        assert fps.snap(50, tm.SnapMode.START) == 40
        assert fps.snap(50, tm.SnapMode.END) == 79
        assert fps.snap(50, tm.SnapMode.NEAREST) == 40
        assert fps.snap(70, tm.SnapMode.NEAREST) == 80


class TestFormatting:
    @pytest.mark.parametrize(
        ("ms", "expected"),
        [(0, "0:00:00.00"), (1000, "0:00:01.00"), (1234, "0:00:01.23"), (3_723_456, "1:02:03.45")],
    )
    def test_ass(self, ms: int, expected: str) -> None:
        assert tm.format_ass(ms) == expected

    @pytest.mark.parametrize(
        ("ms", "expected"),
        [(0, "00:00:00,000"), (1234, "00:00:01,234"), (3_723_456, "01:02:03,456")],
    )
    def test_srt(self, ms: int, expected: str) -> None:
        assert tm.format_srt(ms) == expected

    def test_vtt(self) -> None:
        assert tm.format_vtt(3_723_456) == "01:02:03.456"

    def test_negative_clamped(self) -> None:
        assert tm.format_srt(-500) == "00:00:00,000"


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("0:00:01.23", 1230),
            ("00:00:01,234", 1234),
            ("1:02:03.45", 3_723_450),
            ("01:02:03.456", 3_723_456),
            ("02:03.5", 123_500),
            ("  0:00:01.00  ", 1000),
        ],
    )
    def test_parses(self, text: str, expected: int) -> None:
        assert tm.parse_timecode(text) == expected

    @pytest.mark.parametrize("text", ["", "мусор", "99", "0:99:00.00", "0:00:99.00", "::"])
    def test_rejects(self, text: str) -> None:
        assert tm.parse_timecode(text) is None

    def test_accepts_short_form_from_manual_input(self) -> None:
        """``1:2`` — это 1 мин 2 с.

        Осознанное решение: парсер обслуживает и файлы, и поля ввода. Человек,
        набравший в поле «1:2», имеет в виду 1:02, и отвергать такой ввод хуже,
        чем принять. В файлах короткая форма не встречается, так что послабление
        ничего не ломает.
        """
        assert tm.parse_timecode("1:2") == 62_000
        assert tm.parse_timecode("0:5") == 5_000

    def test_smpte_uses_fps(self) -> None:
        fps = tm.FpsModel(Fraction(25, 1))
        assert tm.parse_timecode("00:00:01:12", fps) == 1000 + 480

    @pytest.mark.parametrize("ms", [0, 1, 999, 1000, 3_723_456, 86_399_999])
    def test_srt_roundtrip_is_exact(self, ms: int) -> None:
        assert tm.parse_timecode(tm.format_srt(ms)) == ms

    @pytest.mark.parametrize("ms", [0, 1230, 3_723_450])
    def test_ass_roundtrip_on_centisecond_grid(self, ms: int) -> None:
        assert tm.parse_timecode(tm.format_ass(ms)) == ms


def test_round_for_ass_never_shortens() -> None:
    """Начало вниз, конец вверх — субтитр не должен укорачиваться при экспорте."""
    start, end = tm.round_for_ass(1234, 5678)
    assert start == 1230 and end == 5680
    assert start <= 1234 and end >= 5678

def test_round_for_ass_exact_stays() -> None:
    assert tm.round_for_ass(1230, 5680) == (1230, 5680)
