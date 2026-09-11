"""Тесты главного окна на уровне поведения, а не отдельных виджетов."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")
pytest.importorskip("av", reason="нужен PyAV")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_fixtures import MediaSpec, make_test_media
from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.io.formats.ass import write_ass

pytestmark = [pytest.mark.needs_gui, pytest.mark.needs_media]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("window") / "clip.mkv"
    make_test_media(path, MediaSpec(duration_ms=4000))
    return path


@pytest.fixture
def window(qapp: QApplication, tmp_path, monkeypatch):
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


class TestTitle:
    """Заголовок — единственное место, где видно, что вообще открыто."""

    def test_blank_document(self, window) -> None:
        assert "Без имени" in window.windowTitle()
        assert "SubtitleForge Studio" in window.windowTitle()

    def test_media_name_appears(self, window, clip: Path) -> None:
        """Открытое видео должно быть видно в заголовке.

        ``_refresh_title`` подставляет имя медиа, но звать её после смены
        ``_media_path`` было некому: видео открывалось, а окно продолжало
        показывать «Без имени», в том числе при запуске с файлом в аргументах.
        """
        window.load_media(clip)
        assert clip.name in window.windowTitle()

    def test_media_path_is_recorded(self, window, clip: Path) -> None:
        window.load_media(clip)
        assert window._media_path == clip

    def test_subtitle_name_appears(self, window, tmp_path: Path) -> None:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, "Реплика")
        path = tmp_path / "перевод.ass"
        path.write_text(write_ass(doc), encoding="utf-8-sig")

        from sfstudio.io import registry

        window._load_document(registry.load(path))
        assert "перевод.ass" in window.windowTitle()

    def test_both_names_shown_together(self, window, clip: Path, tmp_path: Path) -> None:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, "Реплика")
        path = tmp_path / "вместе.ass"
        path.write_text(write_ass(doc), encoding="utf-8-sig")

        from sfstudio.io import registry

        window._load_document(registry.load(path))
        window.load_media(clip)
        title = window.windowTitle()
        assert "вместе.ass" in title
        assert clip.name in title


class TestEditBar:
    """Полоса правки под кадром — она же команды меню «Правка»."""

    def test_bar_sits_under_the_frame(self, window) -> None:
        centre = window.centralWidget().layout()
        order = [centre.itemAt(i).widget() for i in range(centre.count())]
        assert order.index(window.edit_bar) > order.index(window.video_pane)

    def test_buttons_are_filled_from_the_registry(self, window) -> None:
        assert window.edit_bar.keys() == [
            "edit.insert", "edit.duplicate", "edit.delete", "edit.add_track",
        ]

    def test_new_line_button_adds_a_line(self, window) -> None:
        before = len(window._doc.events)
        window.edit_bar.button("edit.insert").click()
        assert len(window._doc.events) == before + 1

    def test_new_track_button_adds_a_track(self, window) -> None:
        before = len(window._doc.tracks.subtitles)
        window.edit_bar.button("edit.add_track").click()
        assert len(window._doc.tracks.subtitles) == before + 1

    def test_buttons_carry_their_shortcuts(self, window) -> None:
        """Подсказка учит сочетанию, а не просто повторяет надпись."""
        tip = window.edit_bar.button("edit.insert").toolTip()
        assert "Ctrl" in tip

    def test_captions_are_short(self, window) -> None:
        """В меню «Новое событие», на кнопке «Реплика»: полоса узкая.

        Сравниваем через ``tr``, а не с русской строкой: подпись переводится,
        и на английском интерфейсе дословное сравнение развалилось бы.
        """
        from sfstudio.app.i18n import tr

        assert window.edit_bar.button("edit.insert").text() == tr('Реплика')
        assert len(window.edit_bar.button("edit.insert").text()) <= 12
