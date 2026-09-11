"""Окно при открытии медиа: частота кадров и брошенные файлы.

Окно живёт одно на весь файл, а диалоги подменяются заглушками: настоящие
модальны и в прогоне без человека повисли бы навсегда.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import ClassVar

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from _pytest.monkeypatch import MonkeyPatch
from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.ui.media_offer import FpsOfferChoice

# Отдельным проходом: окно тянет за собой libass и плеер, а очередь событий
# в общем прогоне полна чужого добра — разбор её обрывает процесс.
pytestmark = [pytest.mark.needs_gui, pytest.mark.serial]

FILM = Fraction(24, 1)
NTSC_FILM = Fraction(24000, 1001)
PAL = Fraction(25, 1)
HOUR = 3_600_000


class _Video:
    def __init__(self, rate: Fraction) -> None:
        from sfstudio.core.time import FpsModel

        self.fps = FpsModel(rate)


class _Info:
    """Метаданные ровно в том объёме, в каком их читает окно."""

    def __init__(self, rate: Fraction | None) -> None:
        self.video = _Video(rate) if rate is not None else None


class _Offer:
    """Заглушка вместо модального окна: отвечает заранее заданным выбором."""

    answer = FpsOfferChoice()
    seen: ClassVar[dict] = {}

    def __init__(self, **kwargs) -> None:
        _Offer.seen = kwargs

    def exec(self) -> int:
        return 1

    def choice(self) -> FpsOfferChoice:
        return _Offer.answer


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def app_window(qapp: QApplication, tmp_path_factory):
    patch = MonkeyPatch()
    patch.setenv("SFSTUDIO_HOME", str(tmp_path_factory.mktemp("home")))
    from sfstudio.ui.main_window import MainWindow

    made = MainWindow()
    made.resize(1000, 700)
    made.video_pane.close_player()

    yield made

    made._undo.mark_clean()
    made.close()
    made.deleteLater()
    patch.undo()


@pytest.fixture
def window(app_window, monkeypatch, qapp: QApplication):
    document = SubtitleDocument.blank()
    document.create_event(0, 2000, "Начало")
    document.create_event(HOUR, HOUR + 2000, "Через час")
    app_window._load_document(document)
    app_window._source_fps = None
    app_window._project = None

    monkeypatch.setattr("sfstudio.ui.media_offer.FpsOfferDialog", _Offer)
    _Offer.answer = FpsOfferChoice()
    _Offer.seen = {}
    qapp.processEvents()
    return app_window


class TestKnownRate:
    def test_nobody_said_means_unknown(self, window) -> None:
        """Догадка «наверное, 25» — повод дёргать человека на пустом месте."""
        assert window._known_fps() is None

    def test_dialogs_still_get_a_starting_point(self, window) -> None:
        """Списку частот надо на чём-то стоять; 25 — самое частое начало."""
        assert window._project_fps() == PAL

    def test_answer_is_remembered(self, window) -> None:
        """Иначе тот же разговор повторялся бы при каждом открытии видео."""
        window._adopt_fps(NTSC_FILM)
        assert window._known_fps() == NTSC_FILM

    def test_rate_goes_into_the_project(self, window) -> None:
        from sfstudio.core.project import Project

        window._project = Project.new("проба", (1920, 1080))
        window._adopt_fps(NTSC_FILM)
        assert window._project.fps == NTSC_FILM


class TestOffer:
    def test_matching_rate_asks_nothing(self, window) -> None:
        window._adopt_fps(FILM)
        window._offer_fps(Path("кино.mkv"), _Info(FILM))
        assert _Offer.seen == {}

    def test_nothing_known_means_no_questions(self, window) -> None:
        """Первое открытие видео: частоту субтитров никто не объявлял."""
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert _Offer.seen == {}

    def test_unknown_rate_is_taken_from_the_video(self, window) -> None:
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert window._known_fps() == NTSC_FILM

    def test_video_rate_is_announced(self, window) -> None:
        """Молчать не надо: если субтитры поедут, человек вспомнит эту строку."""
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert "23.976" in window.statusBar().currentMessage()

    def test_different_rate_starts_the_talk(self, window) -> None:
        window._adopt_fps(PAL)
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert _Offer.seen["video_fps"] == NTSC_FILM

    def test_offer_carries_the_length(self, window) -> None:
        """По ней считается «на сколько уедет к концу» — главное число окна."""
        window._adopt_fps(PAL)
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert _Offer.seen["span_ms"] == HOUR + 2000

    def test_audio_only_file_asks_nothing(self, window) -> None:
        window._offer_fps(Path("звук.wav"), _Info(None))
        assert _Offer.seen == {}

    def test_empty_document_takes_the_rate_silently(self, window) -> None:
        """Пересчитывать нечего, а спрашивать не о чем — терять нечего."""
        for event in list(window._doc.events):
            window._doc.remove_event(event.eid)
        window._doc.rebuild_lookup()

        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert _Offer.seen == {}
        assert window._project_fps() == NTSC_FILM

    def test_setting_can_silence_the_talk(self, window) -> None:
        window._adopt_fps(PAL)
        window._settings.set("media.ask_fps", False)
        try:
            window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
            assert _Offer.seen == {}
        finally:
            window._settings.set("media.ask_fps", True)

    def test_doing_nothing_leaves_timings_alone(self, window) -> None:
        window._adopt_fps(PAL)
        before = [(e.start, e.end) for e in window._doc.events]
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert [(e.start, e.end) for e in window._doc.events] == before

    def test_refusal_does_not_record_the_rate(self, window) -> None:
        """«Ничего не менять» — это и про память тоже.

        Запиши мы частоту вопреки отказу, и в следующий раз программа решила
        бы, что вопрос уже решён, — а он решён не был.
        """
        window._adopt_fps(PAL)
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert window._project_fps() == PAL

    def test_adopt_only_leaves_timings_alone(self, window) -> None:
        window._adopt_fps(PAL)
        _Offer.answer = FpsOfferChoice(adopt=True)
        before = [(e.start, e.end) for e in window._doc.events]
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert [(e.start, e.end) for e in window._doc.events] == before
        assert window._project_fps() == NTSC_FILM

    def test_rescale_stretches_the_document(self, window) -> None:
        window._adopt_fps(PAL)
        _Offer.answer = FpsOfferChoice(adopt=True, rescale=True)
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert window._doc.events[1].start > HOUR

    def test_rescale_is_one_undo_step(self, window) -> None:
        window._adopt_fps(PAL)
        _Offer.answer = FpsOfferChoice(adopt=True, rescale=True)
        before = window._undo.depth_used
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        assert window._undo.depth_used == before + 1

    def test_rescale_can_be_undone(self, window) -> None:
        window._adopt_fps(PAL)
        _Offer.answer = FpsOfferChoice(adopt=True, rescale=True)
        window._offer_fps(Path("кино.mkv"), _Info(NTSC_FILM))
        window._undo.undo()
        assert window._doc.events[1].start == HOUR


class TestDroppedFiles:
    def test_video_is_opened(self, window, monkeypatch) -> None:
        opened = []
        monkeypatch.setattr(type(window), "load_media", lambda _self, path: opened.append(path))
        window.open_dropped([r"D:\кино.mkv"])
        assert opened == [Path(r"D:\кино.mkv")]

    def test_subtitles_are_opened(self, window, monkeypatch) -> None:
        opened = []
        monkeypatch.setattr(
            type(window), "open_subtitle_file", lambda _self, path: opened.append(path)
        )
        window.open_dropped([r"D:\перевод.ass"])
        assert opened == [Path(r"D:\перевод.ass")]

    def test_subtitles_are_opened_before_the_video(self, window, monkeypatch) -> None:
        """Открытие видео сверяет частоту — сверять надо с новым документом."""
        order = []
        monkeypatch.setattr(
            type(window), "open_subtitle_file", lambda _self, _path: order.append("субтитры")
        )
        monkeypatch.setattr(type(window), "load_media", lambda _self, _path: order.append("видео"))
        window.open_dropped([r"D:\кино.mkv", r"D:\перевод.srt"])
        assert order == ["субтитры", "видео"]

    def test_project_comes_alone(self, window, monkeypatch) -> None:
        """Проект приносит и субтитры, и видео — остальное только мешает."""
        order = []
        monkeypatch.setattr(
            type(window), "open_project_file", lambda _self, _path: order.append("проект")
        )
        monkeypatch.setattr(type(window), "load_media", lambda _self, _path: order.append("видео"))
        window.open_dropped([r"D:\кино.mkv", r"D:\серия.sfproj"])
        assert order == ["проект"]

    def test_junk_is_reported_not_swallowed(self, window) -> None:
        window.open_dropped([r"D:\заметка.txt"])
        assert "не видео" in window.statusBar().currentMessage()
