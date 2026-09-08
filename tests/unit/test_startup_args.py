"""Файл, переданный в командной строке.

Сюда попадает всё, что человек бросил на ярлык программы или набрал в
консоли, — включая опечатки и имена папок. Ни один из этих случаев не должен
заканчиваться падением: раньше имя папки вместо файла давало необработанное
исключение и окно «Failed to execute script».
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication

from sfstudio.ui.app import _argument_plan, _Plan

ASS_SAMPLE = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,Первая реплика
"""


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def plan_for(path: Path) -> tuple[_Plan, str]:
    plan = _Plan()
    return plan, _argument_plan(path, plan)


class TestBadArguments:
    def test_a_folder_is_reported_not_crashed(self, qapp, tmp_path) -> None:
        """Тот самый случай: в аргументе оказалась папка."""
        plan, problem = plan_for(tmp_path)
        assert "папка" in problem
        assert plan.document is None

    def test_missing_file_is_reported(self, qapp, tmp_path) -> None:
        plan, problem = plan_for(tmp_path / "опечатка.ass")
        assert "не найден" in problem
        assert plan.document is None

    def test_unreadable_file_is_reported(self, qapp, tmp_path, monkeypatch) -> None:
        """Права, занятый файл, отвалившийся сетевой диск — всё сюда."""
        from sfstudio.io import registry

        target = tmp_path / "закрытый.srt"
        target.write_text("1\n00:00:01,000 --> 00:00:02,000\nТекст\n", encoding="utf-8")

        def refuse(*_a, **_k):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(registry, "load", refuse)
        plan, problem = plan_for(target)
        assert "не открывается" in problem
        assert plan.document is None

    def test_broken_file_is_reported(self, qapp, tmp_path, monkeypatch) -> None:
        from sfstudio.io import registry

        target = tmp_path / "битый.srt"
        target.write_text("что угодно", encoding="utf-8")

        def refuse(*_a, **_k):
            raise ValueError("формат 'xyz' не поддерживается для чтения")

        monkeypatch.setattr(registry, "load", refuse)
        plan, problem = plan_for(target)
        assert "не удалось прочитать" in problem

    def test_binary_file_is_not_passed_off_as_empty_subtitles(
        self, qapp, tmp_path
    ) -> None:
        """Разбор всеяден: любые байты «читаются» как файл без реплик."""
        target = tmp_path / "видео.bin"
        target.write_bytes(bytes(range(256)) * 8)
        plan, problem = plan_for(target)
        assert "нет ни одной реплики" in problem
        assert plan.document is None


class TestGoodArguments:
    def test_subtitles_are_loaded(self, qapp, tmp_path) -> None:
        target = tmp_path / "перевод.ass"
        target.write_text(ASS_SAMPLE, encoding="utf-8")
        plan, problem = plan_for(target)
        assert problem == ""
        assert len(plan.document.events) == 1

    def test_empty_subtitle_file_is_still_opened(self, qapp, tmp_path) -> None:
        """Пустая заготовка — законный случай, мешать человеку не за что."""
        target = tmp_path / "заготовка.srt"
        target.write_text("", encoding="utf-8")
        plan, problem = plan_for(target)
        assert problem == ""
        assert plan.document is not None

    def test_project_goes_to_the_project_slot(self, qapp, tmp_path) -> None:
        from sfstudio.core.project import PROJECT_SUFFIX

        target = tmp_path / f"работа{PROJECT_SUFFIX}"
        target.write_bytes(b"PK")
        plan, problem = plan_for(target)
        assert problem == ""
        assert plan.project_path == target

    def test_media_goes_to_the_media_slot(self, qapp, tmp_path) -> None:
        target = tmp_path / "фильм.mp4"
        target.write_bytes(b"\x00\x00\x00\x18ftyp")
        plan, problem = plan_for(target)
        assert problem == ""
        assert plan.media_path == target
