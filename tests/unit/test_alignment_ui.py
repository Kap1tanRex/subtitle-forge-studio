"""Окно выравнивания по речи и применение плана к документу.

Распознавание не запускается: в окно подаётся готовый результат — ровно то,
что пришло бы из движка. Проверяется поведение окна, а не движка.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication

from sfstudio.app.settings import Settings
from sfstudio.core.document import SubtitleDocument
from sfstudio.services.asr import RecognitionResult, Segment, WordTiming
from sfstudio.ui.alignment_dialog import AlignmentDialog, words_from

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
    for event in list(document.events):
        document.remove_event(event.eid)
    document.create_event(0, 1000, "Здравствуйте доктор")
    document.create_event(1000, 2000, "Как ваше самочувствие")
    document.create_event(2000, 3000, "Совсем скверно")
    return document


@pytest.fixture
def media(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mkv"
    path.write_bytes(b"fake")
    return path


@pytest.fixture
def dialog(qapp: QApplication, doc, settings, media) -> AlignmentDialog:
    return AlignmentDialog(doc, media, settings)


def heard(pairs) -> RecognitionResult:
    """Результат распознавания со словами: (время начала, текст фразы)."""
    segments = []
    for start, text in pairs:
        words = []
        for offset, piece in enumerate(text.split()):
            at = start + offset * 500
            words.append(WordTiming(at, at + 500, piece))
        segments.append(
            Segment(start, start + 500 * len(words), text, words=tuple(words))
        )
    return RecognitionResult(segments=segments, engine="fake", elapsed_s=1.0)


class TestLayout:
    def test_every_engine_is_listed(self, dialog: AlignmentDialog) -> None:
        """Недоступные тоже: пустой список ничего не объясняет."""
        assert dialog.engine_box.count() >= 3

    def test_engine_without_word_timings_is_marked(self, dialog) -> None:
        titles = [
            dialog.engine_box.itemText(i) for i in range(dialog.engine_box.count())
        ]
        assert any("тайминги слов" in title for title in titles)

    def test_engine_without_word_timings_cannot_run(self, dialog) -> None:
        """Выравнивать по целым фразам нечего — и это видно до запуска."""
        for index in range(dialog.engine_box.count()):
            dialog.engine_box.setCurrentIndex(index)
            info = dialog._current_info()
            if info is not None and info.available and not info.word_timings:
                assert not dialog.run_button.isEnabled()
                assert "тайминги" in dialog.engine_note.text()

    def test_no_media_disables_run(self, qapp, doc, settings) -> None:
        without = AlignmentDialog(doc, None, settings)
        assert not without.run_button.isEnabled()
        assert "видео" in without.status.text().lower()

    def test_apply_is_disabled_before_the_plan_exists(self, dialog) -> None:
        assert not dialog.apply_button.isEnabled()

    def test_selection_scope_needs_a_selection(self, dialog) -> None:
        assert not dialog.scope_selection.isEnabled()
        assert dialog.scope_all.isChecked()

    def test_language_comes_from_the_asr_settings(
        self, qapp, doc, media, tmp_path
    ) -> None:
        """Язык уже выбран в окне распознавания — спрашивать заново незачем."""
        path = tmp_path / "settings.json"
        first = Settings(path)
        first.set("asr.language", "en")
        first.save()
        again = AlignmentDialog(doc, media, Settings(path))
        assert again.language_box.currentData() == "en"


class TestWordsFromSegments:
    def test_words_are_collected_in_order(self) -> None:
        result = heard([(0, "раз два"), (5000, "три")])
        words = words_from(result.segments)
        assert [word.text for word in words] == ["раз", "два", "три"]

    def test_segments_without_words_give_nothing(self) -> None:
        assert words_from([Segment(0, 1000, "фраза целиком")]) == []


class TestResultHandling:
    def test_plan_appears_after_recognition(self, dialog: AlignmentDialog) -> None:
        dialog._on_finished(
            heard([(1000, "здравствуйте доктор"), (5000, "как ваше самочувствие"),
                   (9000, "совсем скверно")])
        )
        assert len(dialog.plan()) == 3
        assert dialog.apply_button.isEnabled()

    def test_plan_is_shown_row_by_row(self, dialog: AlignmentDialog) -> None:
        dialog._on_finished(heard([(1000, "здравствуйте доктор")]))
        assert dialog.preview.topLevelItemCount() == 3

    def test_result_without_words_is_explained(self, dialog: AlignmentDialog) -> None:
        """Молчаливый пустой список выглядел бы как поломка программы."""
        dialog._on_finished(
            RecognitionResult(segments=[Segment(0, 2000, "фраза без слов")])
        )
        assert not dialog.apply_button.isEnabled()
        assert "не по чему" in dialog.status.text()

    def test_poor_coverage_is_called_out(self, dialog: AlignmentDialog) -> None:
        dialog._on_finished(heard([(1000, "совершенно посторонние слова")]))
        assert "мало" in dialog.status.text()

    def test_shaky_rows_can_be_shown_alone(self, dialog: AlignmentDialog) -> None:
        dialog._on_finished(
            heard([(1000, "здравствуйте доктор"), (9000, "совсем скверно")])
        )
        dialog.only_shaky.setChecked(True)
        assert dialog.preview.topLevelItemCount() == 1

    def test_shaky_row_explains_itself(self, dialog: AlignmentDialog) -> None:
        dialog._on_finished(
            heard([(1000, "здравствуйте доктор"), (9000, "совсем скверно")])
        )
        dialog.only_shaky.setChecked(True)
        assert "проверьте" in dialog.preview.topLevelItem(0).toolTip(0).lower()

    def test_confidence_is_shown_for_every_row(self, dialog: AlignmentDialog) -> None:
        dialog._on_finished(
            heard([(1000, "здравствуйте доктор"), (5000, "как ваше самочувствие"),
                   (9000, "совсем скверно")])
        )
        texts = [
            dialog.preview.topLevelItem(i).text(3)
            for i in range(dialog.preview.topLevelItemCount())
        ]
        assert all(text for text in texts)
        assert "100 %" in texts

    def test_failure_is_shown_not_swallowed(self, dialog: AlignmentDialog) -> None:
        dialog._on_failed("модель не найдена")
        assert "модель не найдена" in dialog.status.text()
        assert dialog.progress.value() == 0

    def test_progress_updates(self, dialog: AlignmentDialog) -> None:
        dialog._on_progress(0.42, "распознавание")
        assert dialog.progress.value() == 42


class TestApplying:
    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def prepared(self, window):
        doc = window._doc
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(0, 1000, "первая реплика")
        doc.create_event(1000, 2000, "вторая реплика")
        doc.rebuild_lookup()
        return doc

    def test_menu_has_the_action(self, window) -> None:
        assert window._actions_registry.action("timing.align") is not None

    def test_plan_applies_as_one_undo_step(self, window, monkeypatch) -> None:
        """Тайминги всего файла — не сотня нажатий «Отменить»."""
        from sfstudio.core.commands.timing import ApplyTimings

        doc = self.prepared(window)
        eids = [event.eid for event in doc.events]
        before = window._undo.depth_used
        window._undo.run(
            ApplyTimings({eids[0]: (5000, 6000), eids[1]: (7000, 8000)},
                         label="Выравнивание по речи")
        )
        assert window._undo.depth_used == before + 1
        assert doc.by_eid(eids[0]).start == 5000

    def test_undo_restores_the_old_timings(self, window) -> None:
        from sfstudio.core.commands.timing import ApplyTimings

        doc = self.prepared(window)
        eid = doc.events[0].eid
        window._undo.run(ApplyTimings({eid: (5000, 6000)}))
        window._undo.undo()
        assert doc.by_eid(eid).start == 0

    def test_no_events_is_refused_with_a_word(self, window) -> None:
        for event in list(window._doc.events):
            window._doc.remove_event(event.eid)
        window.open_alignment()
        assert "Импорт текста" in window.statusBar().currentMessage()
