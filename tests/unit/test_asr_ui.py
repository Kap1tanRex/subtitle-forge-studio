"""Тесты окна распознавания и вставки распознанного в документ."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication

from sfstudio.app.settings import Settings
from sfstudio.core.document import SubtitleDocument
from sfstudio.services.asr import RecognitionResult, Segment
from sfstudio.services.asr import registry as reg
from sfstudio.ui.asr_dialog import AsrDialog

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path / "settings.json")


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(0, 2000, "Существующая реплика")
    return document


@pytest.fixture
def dialog(qapp: QApplication, doc, settings, tmp_path: Path) -> AsrDialog:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"fake")
    return AsrDialog(doc, media, settings)


class TestDialogLayout:
    def test_lists_every_engine(self, dialog: AsrDialog) -> None:
        """Недоступные тоже: иначе список пуст и непонятно, что делать."""
        assert dialog.engine_box.count() >= 3

    def test_unavailable_engine_is_marked(self, dialog: AsrDialog) -> None:
        titles = [dialog.engine_box.itemText(i) for i in range(dialog.engine_box.count())]
        if not any(i.available for i in reg.engine_infos()):
            assert any("не установлен" in title for title in titles)

    def test_hint_is_shown_for_missing_engine(self, dialog: AsrDialog) -> None:
        info = dialog._current_info()
        if info is not None and not info.available:
            assert info.hint in dialog.engine_note.text()

    def test_run_is_disabled_without_an_engine(self, dialog: AsrDialog) -> None:
        info = dialog._current_info()
        if info is not None and not info.available:
            assert not dialog.run_button.isEnabled()

    def test_insert_is_disabled_before_recognition(self, dialog: AsrDialog) -> None:
        assert not dialog.insert_button.isEnabled()

    def test_no_media_disables_run(self, qapp, doc, settings) -> None:
        without = AsrDialog(doc, None, settings)
        assert not without.run_button.isEnabled()
        assert "видео" in without.status.text().lower()

    def test_selection_checkbox_reflects_the_range(
        self, qapp, doc, settings, tmp_path
    ) -> None:
        media = tmp_path / "clip.mkv"
        media.write_bytes(b"fake")
        with_range = AsrDialog(doc, media, settings, selection=(1000, 5000))
        assert with_range.selection_check.isEnabled()
        assert "00:00:01" in with_range.selection_check.text()

    def test_selection_disabled_without_a_range(self, dialog: AsrDialog) -> None:
        assert not dialog.selection_check.isEnabled()


class TestSettingsMemory:
    def test_choice_is_saved(self, dialog: AsrDialog, settings: Settings) -> None:
        dialog.model_box.setCurrentText("small")
        dialog.language_box.setCurrentIndex(1)  # русский
        dialog._save_settings()
        assert settings.get("asr.model") == "small"
        assert settings.get("asr.language") == "ru"

    def test_choice_is_restored(self, qapp, doc, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        first = Settings(path)
        first.set("asr.language", "en")
        first.save()

        media = tmp_path / "clip.mkv"
        media.write_bytes(b"fake")
        again = AsrDialog(doc, media, Settings(path))
        assert again.language_box.currentData() == "en"


class TestResultHandling:
    def test_segments_become_replicas(self, dialog: AsrDialog) -> None:
        result = RecognitionResult(
            segments=[Segment(0, 20_000, "Первое предложение. Второе предложение здесь.")],
            engine="fake", elapsed_s=1.0,
        )
        dialog._on_finished(result)
        assert len(dialog.prepared_segments()) > 1
        assert dialog.insert_button.isEnabled()

    def test_preview_shows_the_text(self, dialog: AsrDialog) -> None:
        """Принимать четыреста строк вслепую нельзя — показываем образец."""
        dialog._on_finished(
            RecognitionResult(segments=[Segment(0, 2000, "Проверка образца")])
        )
        assert "Проверка образца" in dialog.preview.toPlainText()

    def test_empty_result_is_explained(self, dialog: AsrDialog) -> None:
        dialog._on_finished(RecognitionResult(segments=[]))
        assert not dialog.insert_button.isEnabled()
        assert "не распознана" in dialog.status.text()

    def test_failure_is_shown_not_swallowed(self, dialog: AsrDialog) -> None:
        dialog._on_failed("модель не найдена")
        assert "модель не найдена" in dialog.status.text()
        assert dialog.progress.value() == 0

    def test_progress_updates(self, dialog: AsrDialog) -> None:
        dialog._on_progress(0.42, "распознавание")
        assert dialog.progress.value() == 42
        assert dialog.status.text() == "распознавание"

    def test_wrapping_can_be_switched_off(self, dialog: AsrDialog) -> None:
        dialog.wrap_check.setChecked(False)
        dialog._on_finished(
            RecognitionResult(segments=[Segment(0, 6000, "слово " * 14)])
        )
        assert all("\\N" not in s.text for s in dialog.prepared_segments())


class TestInsertion:
    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def test_replicas_are_added(self, window) -> None:
        before = len(window._doc)
        window._insert_recognised(
            {"segments": [Segment(20_000, 22_000, "Новая реплика")], "replace": False}
        )
        assert len(window._doc) == before + 1
        assert any(e.plain == "Новая реплика" for e in window._doc.events)

    def test_insertion_is_a_single_undo_step(self, window) -> None:
        """Четыреста реплик — не четыреста нажатий «Отменить»."""
        before = window._undo.depth_used
        segments = [Segment(i * 3000, i * 3000 + 2000, f"Реплика {i}") for i in range(10)]
        window._insert_recognised({"segments": segments, "replace": False})
        assert window._undo.depth_used == before + 1

    def test_undo_removes_everything(self, window) -> None:
        before = len(window._doc)
        segments = [Segment(i * 3000, i * 3000 + 2000, f"Реплика {i}") for i in range(5)]
        window._insert_recognised({"segments": segments, "replace": False})
        window._undo.undo()
        assert len(window._doc) == before

    def test_replace_clears_the_document_first(self, window) -> None:
        window._insert_recognised(
            {"segments": [Segment(0, 2000, "Единственная")], "replace": True}
        )
        assert len(window._doc) == 1
        assert window._doc.events[0].plain == "Единственная"

    def test_replace_is_undoable(self, window) -> None:
        before = len(window._doc)
        window._insert_recognised(
            {"segments": [Segment(0, 2000, "Единственная")], "replace": True}
        )
        window._undo.undo()
        assert len(window._doc) == before

    def test_empty_payload_changes_nothing(self, window) -> None:
        before = len(window._doc)
        window._insert_recognised({"segments": [], "replace": False})
        window._insert_recognised("мусор")
        assert len(window._doc) == before

    def test_replicas_land_on_the_first_track(self, window) -> None:
        window._insert_recognised(
            {"segments": [Segment(20_000, 22_000, "Новая")], "replace": False}
        )
        added = next(e for e in window._doc.events if e.plain == "Новая")
        assert added.layer == window._doc.tracks.subtitles[0].layer

    def test_selected_range_is_computed(self, window) -> None:
        window._select_eid(window._doc.events[0].eid)
        found = window._selected_range()
        assert found is not None
        assert found[0] <= found[1]


class TestLanguageMismatch:
    """Окно должно предупредить, что модель не знает выбранного языка.

    distil-large-v3 понимает только английский, но об этом нигде не
    говорилось. Русская речь превращалась в английский текст — без ошибки,
    без предупреждения, просто неверный результат после всего ожидания.
    """

    @staticmethod
    def _choose(dialog: AsrDialog, model: str, language: str) -> str:
        dialog.model_box.setCurrentText(model)
        index = dialog.language_box.findData(language)
        assert index >= 0, f"нет языка {language}"
        dialog.language_box.setCurrentIndex(index)
        dialog._refresh_model_note()
        return dialog.model_note.text()

    def test_warns_about_russian_with_english_model(self, dialog: AsrDialog) -> None:
        note = self._choose(dialog, "distil-large-v3", "ru")
        assert "только" in note.lower()
        assert dialog.model_note.property("role") == "warning"

    def test_names_a_replacement(self, dialog: AsrDialog) -> None:
        """Предупредить мало — надо сказать, что взять вместо неё."""
        note = self._choose(dialog, "distil-large-v3", "ru")
        assert "medium" in note or "large-v3" in note

    def test_silent_when_language_fits(self, dialog: AsrDialog) -> None:
        note = self._choose(dialog, "distil-large-v3", "en")
        assert "только" not in note.lower()
        assert dialog.model_note.property("role") == "hint"

    def test_silent_for_multilingual_model(self, dialog: AsrDialog) -> None:
        note = self._choose(dialog, "small", "ru")
        assert dialog.model_note.property("role") == "hint"
        assert note

    def test_changing_language_updates_the_note(self, dialog: AsrDialog) -> None:
        """Заметка обновляется сама: язык меняют после выбора модели."""
        dialog.model_box.setCurrentText("distil-large-v3")
        english = dialog.language_box.findData("en")
        russian = dialog.language_box.findData("ru")
        dialog.language_box.setCurrentIndex(english)
        assert dialog.model_note.property("role") == "hint"
        dialog.language_box.setCurrentIndex(russian)
        assert dialog.model_note.property("role") == "warning"
