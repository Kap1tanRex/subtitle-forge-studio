"""Тесты работы с субтитрами внутри контейнеров.

Файлы собираются ffmpeg'ом прямо в тестах, поэтому проверяется настоящий MKV
с настоящей вшитой дорожкой, а не имитация.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sfstudio.platform.ffmpeg import ffmpeg_available, find_ffmpeg

if not ffmpeg_available():
    pytest.skip("ffmpeg не найден", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_fixtures import MediaSpec, make_test_media

from sfstudio.io import registry
from sfstudio.io.container import (
    CONTAINER_CODEC,
    ContainerError,
    MuxOptions,
    UnsupportedTrackError,
    extract_subtitles,
    list_tracks,
    lossy_warning,
    mux_subtitles,
)

pytestmark = pytest.mark.needs_native

ASS_SOURCE = """[Script Info]
Title: Встроенные
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,2,2,20,20,20,1
Style: Надпись,Arial,36,&H0000FFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,2,8,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.50,Default,Анна,0,0,0,,Первая вшитая реплика
Dialogue: 2,0:00:03.00,0:00:04.50,Надпись,,10,20,30,знак,{\\pos(500,300)}Надпись, с запятой
Comment: 0,0:00:05.00,0:00:05.50,Default,,0,0,0,,Закомментировано
"""

SRT_SOURCE = """1
00:00:01,000 --> 00:00:02,500
Первая строка

2
00:00:03,000 --> 00:00:04,500
Вторая строка
"""


def _mux(video: Path, subtitles: Path, out: Path, codec: str, **meta: str) -> None:
    args = [
        str(find_ffmpeg()), "-hide_banner", "-nostdin", "-y",
        "-i", str(video), "-i", str(subtitles),
        "-map", "0:v", "-map", "0:a", "-map", "1:0",
        "-c", "copy", "-c:s", codec,
    ]
    for key, value in meta.items():
        args += ["-metadata:s:s:0", f"{key}={value}"]
    args += ["-disposition:s:0", "default", str(out)]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-400:])


@pytest.fixture(scope="module")
def plain_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("container") / "video.mkv"
    make_test_media(path, MediaSpec(duration_ms=6000, width=320, height=240))
    return path


@pytest.fixture(scope="module")
def with_ass(tmp_path_factory: pytest.TempPathFactory, plain_video: Path) -> Path:
    folder = tmp_path_factory.mktemp("with_ass")
    subs = folder / "subs.ass"
    subs.write_text(ASS_SOURCE, encoding="utf-8")
    out = folder / "movie.mkv"
    _mux(plain_video, subs, out, "ass", language="rus", title="Русские")
    return out


@pytest.fixture(scope="module")
def with_srt(tmp_path_factory: pytest.TempPathFactory, plain_video: Path) -> Path:
    folder = tmp_path_factory.mktemp("with_srt")
    subs = folder / "subs.srt"
    subs.write_text(SRT_SOURCE, encoding="utf-8")
    out = folder / "movie.mkv"
    _mux(plain_video, subs, out, "srt", language="eng")
    return out


def _subtitle_index(path: Path) -> int:
    tracks = list_tracks(path)
    assert tracks, "в файле нет субтитровых дорожек"
    return tracks[0].index


class TestListTracks:
    def test_finds_embedded_track(self, with_ass: Path) -> None:
        tracks = list_tracks(with_ass)
        assert len(tracks) == 1
        assert tracks[0].codec in ("ass", "ssa")

    def test_reads_metadata(self, with_ass: Path) -> None:
        track = list_tracks(with_ass)[0]
        assert track.language == "rus"
        assert track.title == "Русские"
        assert track.is_default is True

    def test_editable_flag(self, with_ass: Path) -> None:
        assert list_tracks(with_ass)[0].is_editable
        assert not list_tracks(with_ass)[0].is_bitmap

    def test_display_name_is_informative(self, with_ass: Path) -> None:
        name = list_tracks(with_ass)[0].display_name()
        assert "Русские" in name
        assert "rus" in name

    def test_no_tracks_in_plain_video(self, plain_video: Path) -> None:
        assert list_tracks(plain_video) == []


class TestExtractAss:
    def test_events_from_packets_and_comments_from_header(self, with_ass: Path) -> None:
        """Comment-строки лежат в extradata, а не в пакетах — их легко потерять."""
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        assert len(doc) == 3
        comments = [e for e in doc.events if e.comment]
        assert len(comments) == 1
        assert "Закомментировано" in comments[0].text

    def test_styles_survive(self, with_ass: Path) -> None:
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        assert set(doc.styles) == {"Default", "Надпись"}
        assert doc.styles["Надпись"].alignment == 8

    def test_script_info_survives(self, with_ass: Path) -> None:
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        assert doc.script_info.play_res == (1920, 1080)
        assert doc.script_info.title == "Встроенные"

    def test_timing_from_packet_pts(self, with_ass: Path) -> None:
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        first = doc.events[0]
        assert first.start == pytest.approx(1000, abs=20)
        assert first.end == pytest.approx(2500, abs=20)

    def test_all_packet_fields_are_mapped(self, with_ass: Path) -> None:
        """Транспортный порядок полей отличается от файлового — легко сдвинуть."""
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        second = next(e for e in doc.events if e.style == "Надпись")
        assert second.layer == 2
        assert second.margin_l == 10
        assert second.margin_r == 20
        assert second.margin_v == 30
        assert second.effect == "знак"

    def test_actor_survives(self, with_ass: Path) -> None:
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        assert doc.events[0].name == "Анна"

    def test_text_with_commas_is_not_split(self, with_ass: Path) -> None:
        """Поле Text последнее и содержит запятые — разбиение ограничено."""
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        second = next(e for e in doc.events if e.style == "Надпись")
        assert second.text == r"{\pos(500,300)}Надпись, с запятой"

    def test_pos_tag_survives(self, with_ass: Path) -> None:
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        second = next(e for e in doc.events if e.style == "Надпись")
        assert second.position() == (500.0, 300.0)

    def test_events_sorted_by_time(self, with_ass: Path) -> None:
        doc = extract_subtitles(with_ass, _subtitle_index(with_ass))
        starts = [e.start for e in doc.events]
        assert starts == sorted(starts)


class TestExtractSrt:
    def test_reads_text_track(self, with_srt: Path) -> None:
        doc = extract_subtitles(with_srt, _subtitle_index(with_srt))
        assert len(doc) == 2
        assert doc.events[0].plain == "Первая строка"

    def test_timing(self, with_srt: Path) -> None:
        doc = extract_subtitles(with_srt, _subtitle_index(with_srt))
        assert doc.events[0].start == pytest.approx(1000, abs=20)


class TestErrors:
    def test_missing_index(self, with_ass: Path) -> None:
        with pytest.raises(ContainerError, match="нет"):
            extract_subtitles(with_ass, 99)

    def test_video_stream_index_is_rejected(self, with_ass: Path) -> None:
        with pytest.raises(ContainerError):
            extract_subtitles(with_ass, 0)

    def test_unsupported_is_its_own_error(self) -> None:
        assert issubclass(UnsupportedTrackError, ContainerError)


class TestMux:
    def test_adds_new_track(self, plain_video: Path, tmp_path: Path) -> None:
        subs = tmp_path / "new.ass"
        subs.write_text(ASS_SOURCE, encoding="utf-8")
        out = tmp_path / "added.mkv"
        mux_subtitles(plain_video, subs, out, MuxOptions(language="deu", title="Deutsch"))

        tracks = list_tracks(out)
        assert len(tracks) == 1
        assert tracks[0].language == "deu"
        assert tracks[0].title == "Deutsch"

    def test_replaces_existing_track(self, with_ass: Path, tmp_path: Path) -> None:
        index = _subtitle_index(with_ass)
        doc = extract_subtitles(with_ass, index)
        doc.events[0].set_text("ЗАМЕНЕНО")
        edited = tmp_path / "edited.ass"
        registry.save(doc, edited)

        out = tmp_path / "replaced.mkv"
        mux_subtitles(
            with_ass, edited, out,
            MuxOptions(language="rus", title="Правленые", replace_index=index),
        )

        tracks = list_tracks(out)
        assert len(tracks) == 1, "замена не должна плодить дорожки"
        assert tracks[0].title == "Правленые"

    def test_round_trip_preserves_content(self, with_ass: Path, tmp_path: Path) -> None:
        index = _subtitle_index(with_ass)
        doc = extract_subtitles(with_ass, index)
        edited = tmp_path / "same.ass"
        registry.save(doc, edited)

        out = tmp_path / "round.mkv"
        mux_subtitles(with_ass, edited, out, MuxOptions(replace_index=index))
        back = extract_subtitles(out, _subtitle_index(out))

        assert len(back) == len(doc)
        assert set(back.styles) == set(doc.styles)
        assert [e.plain for e in back.events] == [e.plain for e in doc.events]

    def test_edit_survives_round_trip(self, with_ass: Path, tmp_path: Path) -> None:
        index = _subtitle_index(with_ass)
        doc = extract_subtitles(with_ass, index)
        doc.events[0].set_text("Новый текст реплики")
        edited = tmp_path / "changed.ass"
        registry.save(doc, edited)

        out = tmp_path / "changed.mkv"
        mux_subtitles(with_ass, edited, out, MuxOptions(replace_index=index))
        back = extract_subtitles(out, _subtitle_index(out))
        assert any("Новый текст реплики" in e.text for e in back.events)

    def test_video_and_audio_are_preserved(self, with_ass: Path, tmp_path: Path) -> None:
        """Ремукс не должен трогать видео и звук."""
        from sfstudio.media.probe import probe

        before = probe(with_ass)
        index = _subtitle_index(with_ass)
        doc = extract_subtitles(with_ass, index)
        edited = tmp_path / "keep.ass"
        registry.save(doc, edited)
        out = tmp_path / "keep.mkv"
        mux_subtitles(with_ass, edited, out, MuxOptions(replace_index=index))

        after = probe(out)
        assert after.video is not None
        assert (after.video.width, after.video.height) == (
            before.video.width, before.video.height
        )
        assert len(after.audio) == len(before.audio)
        assert abs(after.duration_ms - before.duration_ms) < 200

    def test_progress_reaches_one(self, plain_video: Path, tmp_path: Path) -> None:
        subs = tmp_path / "p.ass"
        subs.write_text(ASS_SOURCE, encoding="utf-8")
        out = tmp_path / "p.mkv"
        seen: list[float] = []
        mux_subtitles(plain_video, subs, out, progress=seen.append)
        assert seen and seen[-1] == 1.0

    def test_unsupported_container_is_refused(self, plain_video: Path, tmp_path: Path) -> None:
        subs = tmp_path / "x.ass"
        subs.write_text(ASS_SOURCE, encoding="utf-8")
        with pytest.raises(ContainerError, match="не поддерживается"):
            mux_subtitles(plain_video, subs, tmp_path / "out.avi")

    def test_no_leftover_temp_file(self, plain_video: Path, tmp_path: Path) -> None:
        subs = tmp_path / "t.ass"
        subs.write_text(ASS_SOURCE, encoding="utf-8")
        out = tmp_path / "t.mkv"
        mux_subtitles(plain_video, subs, out)
        leftovers = list(tmp_path.glob("*.tmp-*"))
        assert not leftovers, f"остались временные файлы: {leftovers}"


class TestLossyWarnings:
    def test_mp4_warns_about_styles(self) -> None:
        warning = lossy_warning(Path("film.mp4"))
        assert warning is not None
        assert "mov_text" in warning

    def test_webm_warns(self) -> None:
        assert lossy_warning(Path("film.webm")) is not None

    def test_mkv_is_lossless(self) -> None:
        assert lossy_warning(Path("film.mkv")) is None

    def test_codec_map_covers_common_containers(self) -> None:
        for suffix in (".mkv", ".mp4", ".webm", ".mov"):
            assert suffix in CONTAINER_CODEC
