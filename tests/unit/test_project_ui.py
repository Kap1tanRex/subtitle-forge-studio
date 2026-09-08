"""Тесты стартового окна, диалога нового проекта, настроек и цикла работы."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sfstudio.app.settings import Settings
from sfstudio.core.project import PROJECT_SUFFIX, Project, ProjectState
from sfstudio.io.project_file import save_project
from sfstudio.ui.new_project_dialog import NewProjectDialog
from sfstudio.ui.preferences_dialog import PreferencesDialog
from sfstudio.ui.startup_dialog import (
    ACTION_IMPORT,
    ACTION_NEW,
    ACTION_OPEN,
    StartupDialog,
)

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path / "settings.json")


def make_project(folder: Path, name: str = "Проект") -> Path:
    project = Project.new(name, (1920, 1080), Fraction(25))
    project.document.create_event(0, 2000, "Реплика")
    return save_project(project, folder / f"{name}{PROJECT_SUFFIX}")


class TestStartupDialog:
    def test_lists_recent(self, qapp: QApplication, tmp_path: Path) -> None:
        paths = [make_project(tmp_path, "Первый"), make_project(tmp_path, "Второй")]
        dialog = StartupDialog(paths)
        assert dialog.list.count() == 2

    def test_shows_hint_when_empty(self, qapp: QApplication) -> None:
        dialog = StartupDialog([])
        assert dialog.empty_hint.isVisibleTo(dialog)
        assert dialog.list.count() == 0

    def test_missing_project_stays_but_is_disabled(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        """Файл мог уехать на съёмный диск — вычёркивать запись нельзя."""
        dialog = StartupDialog([tmp_path / "нет.sfproj"])
        item = dialog.list.item(0)
        assert item is not None
        assert not (item.flags() & Qt.ItemIsEnabled)
        assert "недоступен" in item.text()

    def test_open_button_is_off_without_a_valid_choice(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        dialog = StartupDialog([tmp_path / "нет.sfproj"])
        assert not dialog.open_button.isEnabled()

    def test_double_click_chooses_project(self, qapp: QApplication, tmp_path: Path) -> None:
        path = make_project(tmp_path)
        dialog = StartupDialog([path])
        dialog._open_item(dialog.list.item(0))
        assert dialog.action == ACTION_OPEN
        assert dialog.chosen_path == path

    def test_disabled_row_cannot_be_opened(self, qapp: QApplication, tmp_path: Path) -> None:
        dialog = StartupDialog([tmp_path / "нет.sfproj"])
        dialog._open_item(dialog.list.item(0))
        assert dialog.action is None

    @pytest.mark.parametrize(
        ("button", "expected"),
        [("new_button", ACTION_NEW), ("browse_button", ACTION_OPEN),
         ("import_button", ACTION_IMPORT)],
    )
    def test_buttons_report_their_action(
        self, qapp: QApplication, button: str, expected: str
    ) -> None:
        dialog = StartupDialog([])
        getattr(dialog, button).click()
        assert dialog.action == expected
        assert dialog.chosen_path is None

    def test_description_shows_folder(self, qapp: QApplication, tmp_path: Path) -> None:
        """Имени файла мало: «Серия 3» может лежать в трёх местах."""
        path = make_project(tmp_path)
        dialog = StartupDialog([path])
        assert str(tmp_path) in dialog.list.item(0).text()


class TestNewProjectDialog:
    def test_default_path_uses_name_and_folder(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        dialog = NewProjectDialog(tmp_path)
        dialog.name_edit.setText("Серия 1")
        assert dialog.project_path() == tmp_path / f"Серия 1{PROJECT_SUFFIX}"

    def test_suffix_is_not_doubled(self, qapp: QApplication, tmp_path: Path) -> None:
        dialog = NewProjectDialog(tmp_path)
        dialog.name_edit.setText(f"Серия 1{PROJECT_SUFFIX}")
        assert dialog.project_path().name == f"Серия 1{PROJECT_SUFFIX}"

    def test_no_path_without_a_name(self, qapp: QApplication, tmp_path: Path) -> None:
        dialog = NewProjectDialog(tmp_path)
        dialog.name_edit.setText("   ")
        assert dialog.project_path() is None

    def test_resolution_preset_fills_the_spins(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        dialog = NewProjectDialog(tmp_path)
        dialog.select_resolution(1280, 720)
        assert dialog.resolution() == (1280, 720)

    def test_custom_resolution_enables_the_spins(
        self, qapp: QApplication, tmp_path: Path
    ) -> None:
        dialog = NewProjectDialog(tmp_path)
        dialog.resolution_box.setCurrentIndex(dialog.resolution_box.count() - 1)
        assert dialog.width_spin.isEnabled()
        dialog.width_spin.setValue(1440)
        dialog.height_spin.setValue(1080)
        assert dialog.resolution() == (1440, 1080)

    def test_fps_is_an_exact_fraction(self, qapp: QApplication, tmp_path: Path) -> None:
        dialog = NewProjectDialog(tmp_path)
        assert dialog.select_fps(Fraction(24000, 1001))
        assert dialog.fps() == Fraction(24000, 1001)

    def test_build_applies_everything(self, qapp: QApplication, tmp_path: Path) -> None:
        dialog = NewProjectDialog(tmp_path)
        dialog.name_edit.setText("Серия 2")
        dialog.select_resolution(1280, 720)
        dialog.select_fps(Fraction(30))
        project = dialog.build()
        assert project.name == "Серия 2"
        assert project.resolution == (1280, 720)
        assert project.fps == Fraction(30)

    def test_hint_warns_about_overwrite(self, qapp: QApplication, tmp_path: Path) -> None:
        existing = make_project(tmp_path, "Занято")
        dialog = NewProjectDialog(tmp_path)
        dialog.name_edit.setText(existing.stem)
        assert "перезаписан" in dialog.path_hint.text()


class TestPreferencesDialog:
    def test_tabs(self, qapp: QApplication, settings: Settings) -> None:
        dialog = PreferencesDialog(settings)
        titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        assert titles == ["Проекты", "Субтитры", "Редактирование",
                          "Воспроизведение", "Проверки", "Интерфейс",
                          "Загрузки", "Плагины"]

    def test_opening_does_not_change_settings(
        self, qapp: QApplication, settings: Settings
    ) -> None:
        """Открыть окно — не значит что-то поменять."""
        before = settings.as_dict()
        PreferencesDialog(settings)
        assert settings.as_dict() == before

    def test_change_is_applied_immediately(
        self, qapp: QApplication, settings: Settings
    ) -> None:
        dialog = PreferencesDialog(settings)
        dialog.autosave_spin.setValue(15)
        assert settings.get("project.autosave_minutes") == 15

    def test_change_is_persisted(self, qapp: QApplication, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        dialog = PreferencesDialog(Settings(path))
        dialog.gap_spin.setValue(7)
        assert Settings(path).get("editing.min_gap_frames") == 7

    def test_applied_signal_fires(self, qapp: QApplication, settings: Settings) -> None:
        dialog = PreferencesDialog(settings)
        seen: list[int] = []
        dialog.applied.connect(lambda: seen.append(1))
        dialog.autoplay_check.setChecked(True)
        assert seen

    def test_loads_existing_values(self, qapp: QApplication, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        first = Settings(path)
        first.set("editing.min_gap_frames", 9)
        first.save()
        dialog = PreferencesDialog(Settings(path))
        assert dialog.gap_spin.value() == 9

    def test_profile_hint_describes_the_profile(
        self, qapp: QApplication, settings: Settings
    ) -> None:
        dialog = PreferencesDialog(settings)
        assert "знаков в секунду" in dialog.profile_hint.text()


class TestWindowProjectCycle:
    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        yield made
        # Перед закрытием помечаем документ сохранённым: closeEvent честно
        # спрашивает о несохранённых правках, и в прогоне без человека
        # модальный диалог повис бы навсегда.
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def test_save_and_reopen_restores_events(self, window, tmp_path: Path) -> None:
        path = tmp_path / f"работа{PROJECT_SUFFIX}"
        before = [e.plain for e in window._doc.events]
        assert window._write_project(path)

        window._doc.events.clear()
        window._doc.rebuild_lookup()
        assert window.open_project_file(path)
        assert [e.plain for e in window._doc.events] == before

    def test_progress_is_restored(self, window, tmp_path: Path) -> None:
        """Главное обещание проекта: вернуться туда, где остановился."""
        eid = window._doc.events[1].eid
        window._select_eid(eid)
        window.timeline.set_time(7200)
        window.timeline.zoom_tracks(1.5)
        path = tmp_path / f"прогресс{PROJECT_SUFFIX}"
        assert window._write_project(path)

        assert window.open_project_file(path)
        assert window.timeline.time_ms == 7200
        assert window.timeline.track_zoom == pytest.approx(1.5, rel=0.01)
        assert window._current_eid() == eid

    def test_saving_marks_the_document_clean(self, window, tmp_path: Path) -> None:
        from sfstudio.core.commands import SetText

        window._undo.run(SetText(window._doc.events[0].eid, "Правка"))
        assert not window._undo.is_clean
        window._write_project(tmp_path / f"чисто{PROJECT_SUFFIX}")
        assert window._undo.is_clean

    def test_project_goes_to_recent(self, window, tmp_path: Path) -> None:
        path = window._write_project(tmp_path / f"недавний{PROJECT_SUFFIX}")
        assert path
        recent = window._settings.recent("projects")
        assert (tmp_path / f"недавний{PROJECT_SUFFIX}") in recent

    def test_actors_and_tracks_survive_the_cycle(self, window, tmp_path: Path) -> None:
        from sfstudio.core.color import RGBA
        from sfstudio.core.commands import AddActor, AddTrack

        window._undo.run(AddTrack("Надписи"))
        window._undo.run(AddActor("Анна", RGBA.from_hex("#FF8800")))
        path = tmp_path / f"состав{PROJECT_SUFFIX}"
        window._write_project(path)

        assert window.open_project_file(path)
        assert window._doc.tracks.by_layer(1).name == "Надписи"
        assert window._doc.actors.get("Анна").color.to_hex() == "#FF8800"

    def test_broken_project_reports_and_keeps_working(
        self, window, tmp_path: Path, monkeypatch
    ) -> None:
        """Битый файл не должен ронять окно."""
        shown: list[tuple] = []
        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.critical",
            lambda *args, **_kwargs: shown.append(args),
        )
        broken = tmp_path / f"битый{PROJECT_SUFFIX}"
        broken.write_text("не архив", encoding="utf-8")
        assert not window.open_project_file(broken)
        assert shown
        assert len(window._doc) > 0

    def test_capture_state_reads_the_widgets(self, window) -> None:
        window.timeline.set_time(4321)
        state = window._capture_state()
        assert isinstance(state, ProjectState)
        assert state.time_ms == 4321

    def test_title_shows_the_project_name(self, window, tmp_path: Path) -> None:
        """У проекта нет своего файла субтитров — имя брать больше неоткуда."""
        path = tmp_path / f"Серия 7{PROJECT_SUFFIX}"
        window._write_project(path)
        assert window.open_project_file(path)
        assert "Серия 7" in window.windowTitle()

    def test_title_marks_unsaved_changes(self, window, tmp_path: Path) -> None:
        from sfstudio.core.commands import SetText

        window._write_project(tmp_path / f"метка{PROJECT_SUFFIX}")
        assert "•" not in window.windowTitle()
        window._undo.run(SetText(window._doc.events[0].eid, "Правка"))
        window._on_widget_edit()
        assert "•" in window.windowTitle()


class TestAutosave:
    """Настройка автосохранения обязана что-то делать.

    Раньше её можно было выставить, и она ничего не запускала: человек
    полагался на копию, которой не существовало. Настройка, на которую
    рассчитывают напрасно, хуже отсутствующей.
    """

    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def test_timer_runs_when_enabled(self, window) -> None:
        window._settings.set("project.autosave_minutes", 5)
        window._reschedule_autosave()
        assert window._autosave_timer.isActive()
        assert window._autosave_timer.interval() == 5 * 60_000

    def test_zero_means_off(self, window) -> None:
        window._settings.set("project.autosave_minutes", 0)
        window._reschedule_autosave()
        assert not window._autosave_timer.isActive()

    def test_copy_lands_next_to_the_project(self, window, tmp_path: Path) -> None:
        """Искать копию человек пойдёт туда же, где лежит проект."""
        path = tmp_path / f"работа{PROJECT_SUFFIX}"
        window._write_project(path)
        assert window.autosave_path().parent == tmp_path
        assert window.autosave_path() != path

    def test_nothing_to_save_before_the_first_save(self, window) -> None:
        assert window.autosave_path() is None

    def test_autosave_writes_a_copy_without_touching_the_project(
        self, window, tmp_path: Path
    ) -> None:
        path = tmp_path / f"работа{PROJECT_SUFFIX}"
        window._write_project(path)
        original = path.read_bytes()

        window._doc.create_event(9000, 11000, "Правка после сохранения")
        window._undo._clean_index = -1  # документ считается изменённым
        window._autosave()

        assert window.autosave_path().is_file()
        assert path.read_bytes() == original, "сам проект трогать нельзя"

    def test_clean_document_is_not_copied(self, window, tmp_path: Path) -> None:
        """Незачем плодить копии того, что и так сохранено."""
        path = tmp_path / f"работа{PROJECT_SUFFIX}"
        window._write_project(path)
        window._autosave()
        assert not window.autosave_path().is_file()

    def test_saving_removes_the_stale_copy(self, window, tmp_path: Path) -> None:
        """Иначе при следующем открытии предложат восстановить сохранённое."""
        path = tmp_path / f"работа{PROJECT_SUFFIX}"
        window._write_project(path)
        backup = window.autosave_path()
        backup.write_text("старая копия", encoding="utf-8")

        window._write_project(path)
        assert not backup.is_file()

    def test_older_copy_is_ignored_silently(self, window, tmp_path: Path) -> None:
        """Вопрос задаётся, только если копия новее — иначе она уже не нужна."""
        import os
        import time

        path = tmp_path / f"работа{PROJECT_SUFFIX}"
        window._write_project(path)
        backup = window.autosave_path()
        backup.write_text("копия", encoding="utf-8")
        old = time.time() - 3600
        os.utime(backup, (old, old))

        assert window._maybe_restore_autosave(path) == path

    def test_no_copy_means_no_question(self, window, tmp_path: Path) -> None:
        path = tmp_path / f"работа{PROJECT_SUFFIX}"
        window._write_project(path)
        assert window._maybe_restore_autosave(path) == path


class TestTheme:
    """Светлая тема была описана, но никогда не применялась."""

    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        # Настоящая установка таблицы стилей пересчитывает каждый виджет,
        # накопленный за прогон, — до десяти секунд на тест. Проверяем то,
        # что было сломано: раздачу палитры тем, кто рисует себя сам. Сам
        # вызов setStyleSheet проверяется отдельно, ниже.
        monkeypatch.setattr(qapp, "setStyleSheet", lambda _sheet: None)
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def test_widgets_get_the_light_palette(self, window) -> None:
        """Одной таблицы стилей мало: таймлайн рисует себя сам."""
        from sfstudio.ui.theme import LIGHT

        window.apply_theme("light")
        assert window.timeline._palette is LIGHT
        assert window.video_pane.overlay._palette is LIGHT
        assert window.model._palette is LIGHT

    def test_unknown_theme_falls_back_to_dark(self, window) -> None:
        from sfstudio.ui.theme import DARK

        window.apply_theme("чепуха")
        assert window.timeline._palette is DARK

    def test_switching_back_works(self, window) -> None:
        from sfstudio.ui.theme import DARK, LIGHT

        window.apply_theme("light")
        assert window.timeline._palette is LIGHT
        window.apply_theme("dark")
        assert window.timeline._palette is DARK

    def test_theme_is_applied_once(self, qapp: QApplication, window, monkeypatch) -> None:
        """Стиль ставится при смене темы и не ставится повторно.

        Повторная установка того же стиля не бесплатна: Qt пересчитывает
        каждый существующий виджет. Вызов «на всякий случай» при открытии
        окна растянул прогон тестов с 14 секунд до двух минут.
        """
        calls: list[str] = []
        monkeypatch.setattr(qapp, "setStyleSheet", lambda sheet: calls.append(sheet))

        window.apply_theme("light")
        assert len(calls) == 1, "смена темы обязана переставить стиль"
        window.apply_theme("light")
        assert len(calls) == 1, "тот же стиль второй раз не нужен"


class TestFontScale:
    """Масштаб интерфейса: системного масштабирования хватает не всем."""

    @pytest.fixture
    def app(self, qapp: QApplication):
        original = qapp.font()
        yield qapp
        qapp.setFont(original)
        qapp.setProperty("sfstudio_base_font_pt", None)

    def test_scale_changes_the_font(self, app) -> None:
        from sfstudio.ui.theme import apply_font_scale

        base = apply_font_scale(app, 100, base_pt=10.0)
        bigger = apply_font_scale(app, 150)
        assert bigger > base

    def test_repeated_application_does_not_compound(self, app) -> None:
        """Иначе вторая попытка выставить 150% давала бы 225%."""
        from sfstudio.ui.theme import apply_font_scale

        apply_font_scale(app, 100, base_pt=10.0)
        first = apply_font_scale(app, 150)
        second = apply_font_scale(app, 150)
        assert first == pytest.approx(second)

    def test_scale_is_clamped(self, app) -> None:
        from sfstudio.ui.theme import MAX_FONT_SCALE, apply_font_scale

        apply_font_scale(app, 100, base_pt=10.0)
        huge = apply_font_scale(app, 10_000)
        assert huge == pytest.approx(10.0 * MAX_FONT_SCALE / 100)

    def test_returning_to_hundred_restores_the_size(self, app) -> None:
        from sfstudio.ui.theme import apply_font_scale

        base = apply_font_scale(app, 100, base_pt=10.0)
        apply_font_scale(app, 180)
        assert apply_font_scale(app, 100) == pytest.approx(base)

    def test_preferences_expose_the_setting(self, qapp, settings) -> None:
        dialog = PreferencesDialog(settings)
        dialog.scale_spin.setValue(130)
        assert settings.get("ui.font_scale") == 130
