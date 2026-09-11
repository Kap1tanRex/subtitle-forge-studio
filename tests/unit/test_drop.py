"""Разбор файлов, брошенных на окно."""

from __future__ import annotations

from pathlib import Path

import pytest

from sfstudio.ui.drop import DropKind, classify, sort_drop


class TestClassify:
    @pytest.mark.parametrize("name", ["кино.mkv", "серия.mp4", "clip.MOV", "x.webm"])
    def test_video_is_media(self, name: str) -> None:
        assert classify(Path(name)) is DropKind.MEDIA

    @pytest.mark.parametrize("name", ["звук.wav", "дорожка.flac", "音.opus"])
    def test_audio_is_media_too(self, name: str) -> None:
        """Звук открывают ради волны — это тот же путь, что и видео."""
        assert classify(Path(name)) is DropKind.MEDIA

    @pytest.mark.parametrize("name", ["перевод.ass", "t.srt", "web.vtt", "x.ttml"])
    def test_subtitles_are_recognised(self, name: str) -> None:
        assert classify(Path(name)) is DropKind.SUBTITLES

    def test_project_wins_over_everything(self) -> None:
        assert classify(Path("серия.sfproj")) is DropKind.PROJECT

    def test_case_does_not_matter(self) -> None:
        """Windows отдаёт путь как записано на диске, а там бывает «.ASS»."""
        assert classify(Path("ПЕРЕВОД.ASS")) is DropKind.SUBTITLES

    @pytest.mark.parametrize("name", ["заметка.txt", "папка", "архив.zip", "нет"])
    def test_the_rest_is_unknown(self, name: str) -> None:
        assert classify(Path(name)) is DropKind.UNKNOWN

    def test_long_path_is_fine(self) -> None:
        assert classify(Path(r"D:\Кино\Сезон 1\Серия 03 [1080p].mkv")) is DropKind.MEDIA


class TestSortDrop:
    def test_single_video(self) -> None:
        assert sort_drop([Path("кино.mkv")]).media == Path("кино.mkv")

    def test_video_and_subtitles_together(self) -> None:
        """Обычный случай: перетащили пару «серия и перевод к ней»."""
        files = sort_drop([Path("серия.mkv"), Path("серия.srt")])
        assert files.media is not None
        assert files.subtitles is not None

    def test_second_file_of_a_kind_is_counted_as_ignored(self) -> None:
        """Окно одно — два видео в нём не открыть, но и молчать об этом нельзя."""
        files = sort_drop([Path("a.mkv"), Path("b.mkv")])
        assert files.media == Path("a.mkv")
        assert files.ignored == 1

    def test_unknown_files_are_counted(self) -> None:
        files = sort_drop([Path("кино.mkv"), Path("readme.txt")])
        assert files.ignored == 1

    def test_nothing_useful_is_empty(self) -> None:
        assert sort_drop([Path("a.txt"), Path("b.zip")]).is_empty

    def test_empty_list_is_empty(self) -> None:
        assert sort_drop([]).is_empty

    def test_a_full_house(self) -> None:
        files = sort_drop([
            Path("p.sfproj"), Path("кино.mkv"), Path("перевод.ass"), Path("мусор.txt"),
        ])
        assert files.project is not None
        assert files.media is not None
        assert files.subtitles is not None
        assert files.ignored == 1
