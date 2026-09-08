"""Тесты медиа-слоя на настоящем сгенерированном файле.

Файл создаётся с известным содержимым: тон 0.8 амплитуды на 500-1000 мс,
тон 0.4 на 1500-2000 мс, остальное тишина; ключевые кадры строго через GOP.
Поэтому проверяются конкретные значения, а не «что-то ненулевое» — иначе
тест пропустил бы, например, перепутанные min и max.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("av", reason="нужен PyAV")
pytest.importorskip("numpy", reason="нужен NumPy")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_fixtures import MediaSpec, make_test_media

from sfstudio.core.time import FpsModel, SnapMode
from sfstudio.media import keyframes as kf
from sfstudio.media import peaks as pk
from sfstudio.media.probe import MediaProbeError, probe

pytestmark = pytest.mark.needs_media

SPEC = MediaSpec()


@pytest.fixture(scope="session")
def media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("media") / "sample.mkv"
    make_test_media(path, SPEC)
    return path


@pytest.fixture(scope="session")
def silent_media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("media") / "silent.mkv"
    make_test_media(path, MediaSpec(duration_ms=1000, with_audio=False))
    return path


@pytest.fixture(scope="session")
def peaks_file(media: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("peaks") / "sample.peaks"
    pk.extract_peaks(media, out)
    return out


# --------------------------------------------------------------------------- #


class TestProbe:
    def test_reads_video_stream(self, media: Path) -> None:
        info = probe(media)
        assert info.video is not None
        assert (info.video.width, info.video.height) == (SPEC.width, SPEC.height)
        assert info.video.codec == "h264"

    def test_fps_is_exact_fraction(self, media: Path) -> None:
        """FPS обязан быть точной дробью, иначе на длинном файле уплывут кадры."""
        info = probe(media)
        assert info.video.fps.rate == SPEC.fps
        assert info.video.fps.rate.denominator == 1

    def test_duration(self, media: Path) -> None:
        info = probe(media)
        assert abs(info.duration_ms - SPEC.duration_ms) < 100

    def test_audio_stream(self, media: Path) -> None:
        info = probe(media)
        assert info.has_audio
        assert info.audio[0].sample_rate == 48_000
        assert info.audio[0].channels == 1

    def test_no_audio_detected(self, silent_media: Path) -> None:
        assert not probe(silent_media).has_audio

    def test_display_size_without_anamorphic(self, media: Path) -> None:
        info = probe(media)
        assert info.video.display_size == (SPEC.width, SPEC.height)

    def test_play_res_suggestion(self, media: Path) -> None:
        assert probe(media).play_res == (SPEC.width, SPEC.height)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(MediaProbeError):
            probe(tmp_path / "нет-такого.mkv")

    def test_no_subtitle_tracks(self, media: Path) -> None:
        assert probe(media).subtitles == []


class TestDisplayGeometry:
    """Проверки на синтетике: реальный anamorphic-файл не нужен."""

    def _info(self, *, sar, rotation):
        from fractions import Fraction

        from sfstudio.media.probe import VideoStreamInfo

        return VideoStreamInfo(
            index=0, codec="h264", width=720, height=576,
            fps=FpsModel(), sample_aspect=Fraction(sar), rotation=rotation,
        )

    def test_anamorphic_widens_frame(self) -> None:
        """DVD 720x576 с SAR 16:11 должен показываться широким."""
        from fractions import Fraction

        info = self._info(sar=Fraction(16, 11), rotation=0)
        w, h = info.display_size
        assert w > 720
        assert h == 576
        assert info.display_aspect > 1.3

    def test_rotation_swaps_sides(self) -> None:
        from fractions import Fraction

        info = self._info(sar=Fraction(1, 1), rotation=90)
        assert info.display_size == (576, 720)

    def test_rotation_180_keeps_sides(self) -> None:
        from fractions import Fraction

        info = self._info(sar=Fraction(1, 1), rotation=180)
        assert info.display_size == (720, 576)


class TestPeakExtraction:
    def test_produces_file(self, peaks_file: Path) -> None:
        assert peaks_file.exists()
        assert peaks_file.stat().st_size > 64

    def test_duration_matches(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        assert abs(data.duration_ms - SPEC.duration_ms) < 100

    def test_sample_rate(self, peaks_file: Path) -> None:
        assert pk.PeakData.open(peaks_file).sample_rate == 48_000

    def test_pyramid_has_multiple_levels(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        assert data.level_count >= 3
        for i in range(1, data.level_count):
            assert data.samples_per_bucket(i) == data.samples_per_bucket(i - 1) * 4
            assert data.bucket_count(i) < data.bucket_count(i - 1)

    def test_no_audio_raises_dedicated_error(self, silent_media: Path, tmp_path: Path) -> None:
        """Отсутствие звука — отдельный класс ошибки, а не общий сбой."""
        with pytest.raises(pk.NoAudioError):
            pk.extract_peaks(silent_media, tmp_path / "out.peaks")

    def test_cancel_stops_extraction(self, media: Path, tmp_path: Path) -> None:
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(pk.PeaksError):
            pk.extract_peaks(media, tmp_path / "cancelled.peaks", cancel=cancel)
        assert not (tmp_path / "cancelled.peaks").exists(), "отменённое извлечение оставило файл"

    def test_progress_reaches_one(self, media: Path, tmp_path: Path) -> None:
        seen: list[float] = []
        pk.extract_peaks(media, tmp_path / "p.peaks", progress=seen.append)
        assert seen and seen[-1] == 1.0
        assert all(0.0 <= v <= 1.0 for v in seen)


class TestPeakValues:
    """Значения должны соответствовать тому, что мы записали в аудио."""

    def test_silence_is_zero(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        _, mx, rms = data.slice(0, 400, 20)
        assert int(mx.max()) == 0
        assert int(rms.max()) == 0

    def test_loud_tone_matches_amplitude(self, peaks_file: Path) -> None:
        """Тон амплитудой 0.8 → пик около 0.8 * 32767."""
        data = pk.PeakData.open(peaks_file)
        _, mx, _ = data.slice(550, 950, 20)
        assert int(mx.max()) == pytest.approx(0.8 * 32767, rel=0.02)

    def test_quiet_tone_matches_amplitude(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        _, mx, _ = data.slice(1550, 1950, 20)
        assert int(mx.max()) == pytest.approx(0.4 * 32767, rel=0.02)

    def test_loud_is_louder_than_quiet(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        loud = int(data.slice(550, 950, 20)[1].max())
        quiet = int(data.slice(1550, 1950, 20)[1].max())
        assert loud > quiet * 1.5

    def test_min_is_negative_for_tone(self, peaks_file: Path) -> None:
        """Синус симметричен: минимум обязан быть отрицательным.

        Если min и max перепутаны местами, этот тест падает.
        """
        data = pk.PeakData.open(peaks_file)
        mn, mx, _ = data.slice(550, 950, 20)
        assert int(mn.min()) < 0
        assert int(mx.max()) > 0
        assert int(mn.min()) == pytest.approx(-int(mx.max()), rel=0.05)

    def test_loudness_at_distinguishes_tone_from_silence(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        assert data.loudness_at(750) > 0.3
        assert data.loudness_at(200) < 0.01


class TestPeakSlice:
    def test_returns_exact_width(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        for width in (1, 7, 100, 1920):
            mn, mx, rms = data.slice(0, 3000, width)
            assert mn.shape == mx.shape == rms.shape == (width,)

    def test_zoomed_in_still_returns_width(self, peaks_file: Path) -> None:
        """Сильный зум: бакетов меньше, чем колонок — данные растягиваются."""
        data = pk.PeakData.open(peaks_file)
        mn, mx, _ = data.slice(700, 710, 500)
        assert mn.shape == (500,)
        assert int(mx.max()) > 0

    def test_empty_range(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        _, mx, _ = data.slice(100, 100, 10)
        assert int(mx.max()) == 0

    def test_range_beyond_end_is_safe(self, peaks_file: Path) -> None:
        data = pk.PeakData.open(peaks_file)
        mn, mx, rms = data.slice(2900, 60_000, 50)
        assert mn.shape == (50,)

    def test_level_selection_grows_with_zoom_out(self, peaks_file: Path) -> None:
        """Берётся самый детальный уровень, чей бакет помещается в пиксель.

        При 300 отсчётах на пиксель это уровень 0 (бакет 256), а не 1:
        бакет 1024 не влез бы в пиксель и волна стала бы ступенчатой.
        """
        data = pk.PeakData.open(peaks_file)
        assert data.level_for(100) == 0
        assert data.level_for(300) == 0
        assert data.level_for(1024) == 1
        assert data.level_for(5000) >= 2
        assert data.level_for(10_000) > data.level_for(1000)

    def test_zoom_levels_agree_on_loud_region(self, peaks_file: Path) -> None:
        """Разные уровни пирамиды не должны противоречить друг другу."""
        data = pk.PeakData.open(peaks_file)
        narrow = int(data.slice(500, 1000, 400)[1].max())
        wide = int(data.slice(0, 3000, 20)[1].max())
        assert wide == pytest.approx(narrow, rel=0.05)


class TestCacheKey:
    def test_stable_for_same_file(self, media: Path) -> None:
        assert pk.cache_key(media) == pk.cache_key(media)

    def test_differs_per_audio_stream(self, media: Path) -> None:
        """Иначе после смены аудиодорожки показывалась бы чужая волна."""
        assert pk.cache_key(media, 0) != pk.cache_key(media, 1)

    def test_differs_for_different_files(self, media: Path, silent_media: Path) -> None:
        assert pk.cache_key(media) != pk.cache_key(silent_media)

    def test_rejects_stale_version(self, peaks_file: Path, tmp_path: Path) -> None:
        """Кэш от другой версии экстрактора должен отвергаться, а не читаться."""
        corrupted = tmp_path / "old.peaks"
        raw = bytearray(peaks_file.read_bytes())
        raw[12:16] = (99).to_bytes(4, "little")  # extractor_v
        corrupted.write_bytes(bytes(raw))
        with pytest.raises(pk.PeaksError, match="версия"):
            pk.PeakData.open(corrupted)

    def test_rejects_foreign_file(self, tmp_path: Path) -> None:
        alien = tmp_path / "alien.peaks"
        alien.write_bytes(b"x" * 200)
        with pytest.raises(pk.PeaksError):
            pk.PeakData.open(alien)


class TestKeyframes:
    def test_finds_expected_count(self, media: Path) -> None:
        """scene-cut отключён, значит ключевой кадр строго через GOP."""
        index = kf.build_keyframe_index(media)
        assert len(index) == pytest.approx(SPEC.expected_keyframe_count, abs=1)

    def test_starts_at_zero(self, media: Path) -> None:
        index = kf.build_keyframe_index(media)
        assert index.times_ms[0] == 0

    def test_sorted(self, media: Path) -> None:
        index = kf.build_keyframe_index(media)
        assert index.times_ms == sorted(index.times_ms)

    def test_spacing_matches_gop(self, media: Path) -> None:
        index = kf.build_keyframe_index(media)
        expected_ms = SPEC.gop * 1000 / float(SPEC.fps)
        gaps = [b - a for a, b in zip(index.times_ms, index.times_ms[1:], strict=False)]
        for gap in gaps:
            assert gap == pytest.approx(expected_ms, abs=5)

    def test_video_without_stream_returns_empty(self, tmp_path: Path) -> None:
        audio_only = tmp_path / "audio.mkv"
        make_test_media(audio_only, MediaSpec(duration_ms=500, with_video=False))
        assert len(kf.build_keyframe_index(audio_only)) == 0

    def test_nearest(self, media: Path) -> None:
        index = kf.build_keyframe_index(media)
        first, second = index.times_ms[0], index.times_ms[1]
        assert index.nearest(first + 1) == first
        assert index.nearest(second - 1) == second
        assert index.nearest(-5000) == first
        assert index.nearest(10**9) == index.times_ms[-1]

    def test_in_range(self, media: Path) -> None:
        index = kf.build_keyframe_index(media)
        subset = index.in_range(0, 1000)
        assert all(0 <= t <= 1000 for t in subset)
        assert len(subset) < len(index)

    def test_save_and_load(self, media: Path, tmp_path: Path) -> None:
        index = kf.build_keyframe_index(media)
        path = tmp_path / "sample.kfidx"
        index.save(path)
        assert kf.KeyframeIndex.load(path).times_ms == index.times_ms

    def test_empty_index_nearest_is_none(self) -> None:
        assert kf.KeyframeIndex(times_ms=[]).nearest(100) is None


class TestSnapping:
    def _ctx(self, **kw) -> kf.SnapContext:
        base = {
            "fps": FpsModel(SPEC.fps),
            "keyframes": kf.KeyframeIndex(times_ms=[0, 500, 1000, 1500]),
            "event_boundaries": (2000, 2400),
            "px_per_ms": 0.05,  # радиус 8 px = 160 мс
        }
        base.update(kw)
        return kf.SnapContext(**base)

    def test_snaps_to_keyframe(self) -> None:
        result = kf.snap_time(1040, self._ctx())
        assert result.kind == "keyframe"
        assert result.ms == pytest.approx(1000, abs=42)

    def test_keyframe_beats_event_boundary(self) -> None:
        """Склейка важнее стыка с соседней репликой."""
        ctx = self._ctx(keyframes=kf.KeyframeIndex(times_ms=[2010]))
        assert kf.snap_time(2005, ctx).kind == "keyframe"

    def test_snaps_to_event_boundary(self) -> None:
        ctx = self._ctx(keyframes=kf.KeyframeIndex(times_ms=[]))
        result = kf.snap_time(2380, ctx)
        assert result.kind == "event"
        assert result.ms == pytest.approx(2400, abs=42)

    def test_falls_back_to_frame_grid(self) -> None:
        ctx = self._ctx(keyframes=kf.KeyframeIndex(times_ms=[]), event_boundaries=())
        result = kf.snap_time(5017, ctx)
        assert result.kind == "frame"
        # 24 fps → кадр каждые ~41.67 мс
        assert result.ms % 1000 in {0, 41, 83, 125, 166, 208, 250, 291, 333, 375,
                                    416, 458, 500, 541, 583, 625, 666, 708, 750,
                                    791, 833, 875, 916, 958}

    def test_disabled_snapping_returns_input(self) -> None:
        ctx = self._ctx(snap_to_frames=False, snap_to_keyframes=False, snap_to_events=False)
        result = kf.snap_time(1234, ctx)
        assert result.ms == 1234
        assert result.kind == ""

    def test_far_point_is_not_pulled(self) -> None:
        ctx = self._ctx(keyframes=kf.KeyframeIndex(times_ms=[0]), event_boundaries=())
        result = kf.snap_time(9000, ctx)
        assert result.kind == "frame"  # к нулю не притянуло

    def test_radius_scales_with_zoom(self) -> None:
        """Радиус задан в пикселях, значит в миллисекундах зависит от масштаба."""
        near = self._ctx(px_per_ms=0.5)  # 8 px = 16 мс
        far = self._ctx(px_per_ms=0.01)  # 8 px = 800 мс
        assert kf.snap_time(1300, near).kind != "keyframe"
        assert kf.snap_time(1300, far).kind == "keyframe"

    def test_end_mode_does_not_bleed_into_next_frame(self) -> None:
        ctx = self._ctx(keyframes=kf.KeyframeIndex(times_ms=[]), event_boundaries=())
        start = kf.snap_time(1000, ctx, mode=SnapMode.START).ms
        end = kf.snap_time(1000, ctx, mode=SnapMode.END).ms
        assert end >= start

    def test_min_gap_from_fps(self) -> None:
        ctx = self._ctx()
        assert kf.min_gap_ms(ctx) == pytest.approx(83, abs=2)  # 2 кадра при 24 fps
