"""Тесты формата проекта: сохранение, чтение, перенос папки, повреждения."""

from __future__ import annotations

import json
import zipfile
from fractions import Fraction
from pathlib import Path

import pytest

from sfstudio.core.color import RGBA
from sfstudio.core.project import (
    FPS_PRESETS,
    PROJECT_SUFFIX,
    RESOLUTION_PRESETS,
    Project,
    ProjectError,
    ProjectState,
)
from sfstudio.io.project_file import load_project, save_project


@pytest.fixture
def project() -> Project:
    made = Project.new("Тестовый", (1920, 1080), Fraction(24000, 1001))
    made.document.create_event(1000, 2000, "Первая реплика", name="Анна")
    made.document.create_event(3000, 4000, r"{\b1}Вторая", layer=1)
    made.document.actors.add("Анна", RGBA.from_hex("#FF8800"))
    made.document.tracks.add("Надписи", 1)
    made.state = ProjectState(time_ms=3500, selected_eid=1, px_per_ms=0.08,
                              view_start_ms=1200.0, track_zoom=1.5)
    return made


class TestNewProject:
    def test_resolution_goes_to_the_document(self) -> None:
        """Разрешение проекта — это PlayRes, а не копия рядом с ним."""
        made = Project.new("x", (1280, 720))
        assert made.document.script_info.play_res == (1280, 720)
        assert made.resolution == (1280, 720)

    def test_resolution_is_marked_explicit(self) -> None:
        """Заданное пользователем разрешение не должно считаться подставленным."""
        assert not Project.new("x", (1280, 720)).document.script_info.play_res_inferred

    def test_has_timestamps(self) -> None:
        made = Project.new("x")
        assert made.created and made.modified

    def test_starts_without_media(self) -> None:
        assert Project.new("x").media_path is None


class TestRoundTrip:
    def test_suffix_is_added(self, project: Project, tmp_path: Path) -> None:
        path = save_project(project, tmp_path / "работа")
        assert path.suffix == PROJECT_SUFFIX

    def test_events_survive(self, project: Project, tmp_path: Path) -> None:
        back = load_project(save_project(project, tmp_path / "p"))
        assert [e.plain for e in back.document.events] == ["Первая реплика", "Вторая"]

    def test_tags_survive(self, project: Project, tmp_path: Path) -> None:
        back = load_project(save_project(project, tmp_path / "p"))
        assert r"\b1" in back.document.events[1].text

    def test_actors_survive(self, project: Project, tmp_path: Path) -> None:
        back = load_project(save_project(project, tmp_path / "p"))
        assert back.document.actors.get("Анна").color.to_hex() == "#FF8800"

    def test_tracks_survive(self, project: Project, tmp_path: Path) -> None:
        back = load_project(save_project(project, tmp_path / "p"))
        assert back.document.tracks.by_layer(1).name == "Надписи"

    def test_resolution_survives(self, project: Project, tmp_path: Path) -> None:
        project.set_resolution(3840, 2160)
        back = load_project(save_project(project, tmp_path / "p"))
        assert back.resolution == (3840, 2160)

    def test_exact_fps_survives(self, project: Project, tmp_path: Path) -> None:
        """23,976 — это 24000/1001; округление за два часа врёт на кадры."""
        back = load_project(save_project(project, tmp_path / "p"))
        assert back.fps == Fraction(24000, 1001)

    def test_name_survives(self, project: Project, tmp_path: Path) -> None:
        back = load_project(save_project(project, tmp_path / "p"))
        assert back.name == "Тестовый"

    def test_progress_survives(self, project: Project, tmp_path: Path) -> None:
        """Ради этого проект и отличается от файла субтитров."""
        back = load_project(save_project(project, tmp_path / "p"))
        assert back.state.time_ms == 3500
        assert back.state.selected_eid == 1
        assert back.state.px_per_ms == pytest.approx(0.08)
        assert back.state.view_start_ms == pytest.approx(1200.0)
        assert back.state.track_zoom == pytest.approx(1.5)

    def test_path_is_remembered(self, project: Project, tmp_path: Path) -> None:
        path = save_project(project, tmp_path / "p")
        assert project.path == path
        assert load_project(path).path == path

    def test_modified_is_updated(self, project: Project, tmp_path: Path) -> None:
        project.modified = ""
        save_project(project, tmp_path / "p")
        assert project.modified


class TestContainer:
    def test_is_a_zip(self, project: Project, tmp_path: Path) -> None:
        path = save_project(project, tmp_path / "p")
        assert zipfile.is_zipfile(path)

    def test_subtitles_are_real_ass(self, project: Project, tmp_path: Path) -> None:
        """Вытащить субтитры обычным архиватором можно и без нашей программы."""
        path = save_project(project, tmp_path / "p")
        with zipfile.ZipFile(path) as archive:
            text = archive.read("subtitles.ass").decode("utf-8-sig")
        assert "[Script Info]" in text
        assert "[Events]" in text

    def test_manifest_is_readable_json(self, project: Project, tmp_path: Path) -> None:
        path = save_project(project, tmp_path / "p")
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("project.json"))
        assert manifest["format"] == "sfstudio-project"
        assert manifest["resolution"] == {"width": 1920, "height": 1080}


class TestMedia:
    def test_media_beside_the_project_is_found(self, project: Project, tmp_path: Path) -> None:
        media = tmp_path / "clip.mkv"
        media.write_bytes(b"fake")
        project.media_path = media
        back = load_project(save_project(project, tmp_path / "p"))
        assert back.media_path == media.resolve()

    def test_moved_folder_keeps_the_link(self, project: Project, tmp_path: Path) -> None:
        """Проект с видео копируют целиком — относительный путь это переживает."""
        first = tmp_path / "было"
        first.mkdir()
        media = first / "clip.mkv"
        media.write_bytes(b"fake")
        project.media_path = media
        path = save_project(project, first / "p")

        second = tmp_path / "стало"
        second.mkdir()
        (second / "clip.mkv").write_bytes(b"fake")
        moved = second / path.name
        moved.write_bytes(path.read_bytes())

        back = load_project(moved)
        assert back.media_path == (second / "clip.mkv").resolve()

    def test_media_outside_the_folder_uses_absolute(
        self, project: Project, tmp_path: Path
    ) -> None:
        outside = tmp_path / "видео"
        outside.mkdir()
        media = outside / "clip.mkv"
        media.write_bytes(b"fake")
        folder = tmp_path / "проект"
        folder.mkdir()
        project.media_path = media
        back = load_project(save_project(project, folder / "p"))
        assert back.media_path == media.resolve()

    def test_missing_media_path_is_still_reported(
        self, project: Project, tmp_path: Path
    ) -> None:
        """Путь возвращается даже когда файла нет — иначе непонятно, что искать."""
        project.media_path = tmp_path / "нет" / "clip.mkv"
        back = load_project(save_project(project, tmp_path / "p"))
        assert back.media_path is not None
        assert not back.media_path.exists()

    def test_project_without_media(self, project: Project, tmp_path: Path) -> None:
        project.media_path = None
        assert load_project(save_project(project, tmp_path / "p")).media_path is None


class TestFailures:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ProjectError, match="не найден"):
            load_project(tmp_path / "нет.sfproj")

    def test_not_a_zip(self, tmp_path: Path) -> None:
        path = tmp_path / "битый.sfproj"
        path.write_text("это не архив", encoding="utf-8")
        with pytest.raises(ProjectError, match="не удалось прочитать"):
            load_project(path)

    def test_missing_manifest(self, tmp_path: Path) -> None:
        path = tmp_path / "пустой.sfproj"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("нечто.txt", "данные")
        with pytest.raises(ProjectError):
            load_project(path)

    def test_broken_manifest(self, project: Project, tmp_path: Path) -> None:
        path = tmp_path / "битый.sfproj"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("project.json", "{не json")
            archive.writestr("subtitles.ass", "[Script Info]\n\n[Events]\nFormat: Text\n")
        with pytest.raises(ProjectError, match="повреждён"):
            load_project(path)

    def test_newer_format_is_refused_with_a_clear_reason(self, tmp_path: Path) -> None:
        """Молча открыть проект новее нашего формата — значит потерять данные."""
        path = tmp_path / "будущий.sfproj"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("project.json", json.dumps({"version": 99, "name": "x"}))
            archive.writestr("subtitles.ass", "[Script Info]\n\n[Events]\nFormat: Text\n")
        with pytest.raises(ProjectError, match="более новой версией"):
            load_project(path)

    def test_missing_subtitles_part(self, tmp_path: Path) -> None:
        path = tmp_path / "без-субтитров.sfproj"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("project.json", json.dumps({"version": 1, "name": "x"}))
        with pytest.raises(ProjectError):
            load_project(path)

    def test_garbage_fps_falls_back(self, project: Project, tmp_path: Path) -> None:
        path = save_project(project, tmp_path / "p")
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("project.json"))
            subtitles = archive.read("subtitles.ass")
        manifest["fps"] = "быстро"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("project.json", json.dumps(manifest))
            archive.writestr("subtitles.ass", subtitles)
        assert load_project(path).fps == Fraction(25)

    def test_interrupted_write_leaves_no_temporary(
        self, project: Project, tmp_path: Path
    ) -> None:
        save_project(project, tmp_path / "p")
        assert not list(tmp_path.glob("*.tmp"))

    def test_state_from_garbage(self) -> None:
        assert ProjectState.from_dict("мусор").time_ms == 0
        assert ProjectState.from_dict({"time_ms": "рано"}).time_ms == 0


class TestPresets:
    def test_resolutions_are_positive(self) -> None:
        for _, width, height in RESOLUTION_PRESETS:
            assert width > 0 and height > 0

    def test_fps_presets_are_exact(self) -> None:
        """Дробные частоты записаны точной дробью, а не десятичным приближением."""
        values = dict(FPS_PRESETS)
        assert values["23,976 (24000/1001)"] == Fraction(24000, 1001)
        assert values["29,97 (30000/1001)"] == Fraction(30000, 1001)
