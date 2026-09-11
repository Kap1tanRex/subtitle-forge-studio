"""Пересчёт таймингов под другую частоту кадров."""

from __future__ import annotations

from fractions import Fraction

import pytest

from sfstudio.core.time import FpsModel
from sfstudio.services.fps_sync import (
    COMMON_RATES,
    FpsConversion,
    describe_rate,
    likely_sources,
)

FILM = Fraction(24, 1)
NTSC_FILM = Fraction(24000, 1001)
PAL = Fraction(25, 1)
NTSC = Fraction(30000, 1001)

HOUR = 3_600_000


class TestScale:
    def test_same_rate_changes_nothing(self) -> None:
        assert FpsConversion(PAL, PAL).scale == 1.0

    def test_same_rate_is_a_noop(self) -> None:
        assert FpsConversion(PAL, PAL).is_noop

    def test_slower_video_stretches_time(self) -> None:
        """24 → 23.976: те же кадры идут дольше, значит время растёт."""
        assert FpsConversion(FILM, NTSC_FILM).scale > 1.0

    def test_faster_video_squeezes_time(self) -> None:
        assert FpsConversion(NTSC_FILM, FILM).scale < 1.0

    def test_pal_to_film_is_the_classic_four_percent(self) -> None:
        """25 → 24 — то самое «PAL speedup», 4 % на всю длину."""
        assert FpsConversion(PAL, FILM).scale == pytest.approx(25 / 24)

    def test_exact_fractions_not_rounded_decimals(self) -> None:
        """23.976 округлённо даёт полсекунды ошибки на двухчасовом фильме.

        Это ровно та беда, от которой лечит сама команда, так что считать
        её приблизительно нельзя.
        """
        exact = FpsConversion(FILM, NTSC_FILM).map_ms(2 * HOUR)
        rough = round(2 * HOUR * (24 / 23.976))
        assert exact != rough

    def test_zero_rate_is_refused(self) -> None:
        with pytest.raises(ValueError):
            FpsConversion(Fraction(0), PAL)

    def test_negative_rate_is_refused(self) -> None:
        with pytest.raises(ValueError):
            FpsConversion(PAL, Fraction(-25))


class TestMapping:
    def test_zero_stays_zero(self) -> None:
        """Начало дорожки — общая точка отсчёта для любой копии."""
        assert FpsConversion(PAL, NTSC_FILM).map_ms(0) == 0

    def test_hour_moves_by_the_known_amount(self) -> None:
        """24 → 23.976 даёт 3.6 секунды за час — это и есть «сползание»."""
        assert FpsConversion(FILM, NTSC_FILM).map_ms(HOUR) == HOUR + 3600

    def test_time_never_goes_negative(self) -> None:
        assert FpsConversion(PAL, FILM).map_ms(-5000) == 0

    def test_round_trip_returns_roughly_home(self) -> None:
        there = FpsConversion(PAL, NTSC_FILM).map_ms(HOUR)
        back = FpsConversion(NTSC_FILM, PAL).map_ms(there)
        assert abs(back - HOUR) <= 1


class TestDrift:
    def test_drift_grows_with_time(self) -> None:
        """Главная примета: в начале совпадает, к концу расходится."""
        conversion = FpsConversion(FILM, NTSC_FILM)
        assert conversion.drift_ms(HOUR) > conversion.drift_ms(HOUR // 4)

    def test_drift_at_zero_is_zero(self) -> None:
        assert FpsConversion(FILM, NTSC_FILM).drift_ms(0) == 0

    def test_slower_video_means_subtitles_lag(self) -> None:
        assert FpsConversion(FILM, NTSC_FILM).drift_ms(HOUR) > 0

    def test_faster_video_means_subtitles_hurry(self) -> None:
        assert FpsConversion(NTSC_FILM, FILM).drift_ms(HOUR) < 0

    def test_description_names_the_direction(self) -> None:
        text = FpsConversion(FILM, NTSC_FILM).describe_drift(2 * HOUR)
        assert "отста" in text

    def test_description_gives_seconds_for_small_drift(self) -> None:
        """«7.2 с» человек сверит со своими ощущениями, «×1.001» — нет."""
        assert "с" in FpsConversion(FILM, NTSC_FILM).describe_drift(2 * HOUR)

    def test_description_switches_to_minutes_when_large(self) -> None:
        assert "мин" in FpsConversion(PAL, NTSC_FILM).describe_drift(2 * HOUR)

    def test_no_drift_is_said_plainly(self) -> None:
        assert FpsConversion(PAL, PAL).describe_drift(HOUR) == "расхождения нет"


class TestFamilies:
    def test_ntsc_slowdown_is_a_family(self) -> None:
        """23.976 против 24 — почти наверняка перепутанная копия."""
        assert FpsConversion(FILM, NTSC_FILM).same_family

    def test_pal_and_film_are_relatives(self) -> None:
        assert FpsConversion(PAL, FILM).same_family

    def test_unrelated_rates_are_flagged(self) -> None:
        """25 против 29.97 — скорее не тот файл, чем не та копия."""
        assert not FpsConversion(PAL, NTSC).same_family


class TestSuggestions:
    def test_video_rate_is_not_suggested_as_source(self) -> None:
        assert NTSC_FILM not in likely_sources(NTSC_FILM)

    def test_relatives_come_first(self) -> None:
        assert likely_sources(NTSC_FILM)[0] in (FILM, PAL)

    def test_model_is_accepted_as_well_as_fraction(self) -> None:
        assert likely_sources(FpsModel(NTSC_FILM)) == likely_sources(NTSC_FILM)

    def test_every_common_rate_is_offered(self) -> None:
        assert len(likely_sources(PAL)) == len(COMMON_RATES) - 1


class TestPoints:
    def test_points_start_at_zero(self) -> None:
        points = FpsConversion(FILM, NTSC_FILM).points()
        assert (points.src_a, points.dst_a) == (0, 0)

    def test_points_carry_the_scale(self) -> None:
        points = FpsConversion(FILM, NTSC_FILM).points()
        assert points.dst_b / points.src_b == pytest.approx(float(FILM / NTSC_FILM))

    def test_points_work_as_a_command(self) -> None:
        """Ими пользуется LinearSync — она и проверяет, что они годные."""
        from sfstudio.core.commands.timing import LinearSync
        from sfstudio.core.document import SubtitleDocument

        doc = SubtitleDocument.blank()
        event = doc.create_event(HOUR, HOUR + 2000, "Через час")
        LinearSync([event.eid], FpsConversion(FILM, NTSC_FILM).points()).apply(doc)
        assert event.start == HOUR + 3600


class TestNaming:
    def test_fraction_is_shown_as_a_decimal(self) -> None:
        """«24000/1001» знает машина, человек знает «23.976»."""
        assert describe_rate(NTSC_FILM) == "23.976 fps"

    def test_whole_rate_has_no_decimals(self) -> None:
        assert describe_rate(PAL) == "25 fps"

    def test_model_is_accepted(self) -> None:
        assert describe_rate(FpsModel(PAL)) == "25 fps"

    def test_conversion_describes_both_ends(self) -> None:
        text = FpsConversion(PAL, NTSC_FILM).describe()
        assert "25 fps" in text
        assert "23.976 fps" in text
