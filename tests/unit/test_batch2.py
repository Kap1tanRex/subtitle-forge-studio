"""Тесты второй пачки правок: предпросмотр, высота дорожек, цвет по актору."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.services.asr.models import MODEL_CATALOG, folder_matches, installed_models
from sfstudio.ui.timeline import HEADER_W, RULER_H, TimelineWidget, _readable_on
from sfstudio.ui.video_pane import VideoPane

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(0, 2000, "Первая")
    document.create_event(3000, 5000, "Вторая")
    return document


@pytest.fixture
def timeline(qapp: QApplication, doc: SubtitleDocument) -> TimelineWidget:
    widget = TimelineWidget(doc, UndoStack(doc))
    widget.resize(900, 400)
    return widget


class TestPreviewRefresh:
    """Правка должна быть видна в кадре сразу, а не при следующем кадре видео.

    Панель — контейнер, кадр рисует вложенный виджет. ``update()``, посланный
    контейнеру, до ребёнка не доходит, и правка шрифта или цвета оставалась
    невидимой до тех пор, пока видео не сдвинется само.
    """

    def test_update_reaches_the_frame(self, qapp: QApplication, doc) -> None:
        pane = VideoPane(doc, UndoStack(doc))
        calls: list[int] = []
        pane.canvas.update = lambda *_a: calls.append(1)  # type: ignore[method-assign]
        pane.update()
        assert calls, "обновление не дошло до виджета с кадром"

    def test_document_change_repaints_the_frame(self, qapp: QApplication, doc) -> None:
        """Тот же путь, но целиком: правка документа — перерисовка кадра."""
        undo = UndoStack(doc)
        pane = VideoPane(doc, undo)
        calls: list[int] = []
        pane.canvas.update = lambda *_a: calls.append(1)  # type: ignore[method-assign]

        from sfstudio.core.commands import SetText

        undo.on_change = lambda _changes: pane.update()
        undo.run(SetText(next(iter(doc.events)).eid, "Изменённый текст"))
        assert calls


class TestTrackResizeByMouse:
    """Высоту дорожки тянут мышью, и не только в узкой колонке слева."""

    def test_edge_is_grabbable_in_the_header(self, timeline: TimelineWidget) -> None:
        row = next(r for r in timeline.rows() if r.track.is_subtitle)
        assert timeline._resize_edge_at(40.0, row.bottom) == row.track.layer

    def test_edge_is_grabbable_over_the_track_area(self, timeline: TimelineWidget) -> None:
        """Главное неудобство было здесь: справа жест не работал вообще."""
        row = next(r for r in timeline.rows() if r.track.is_subtitle)
        far_from_events = timeline.ms_to_x(60_000)
        assert timeline._resize_edge_at(far_from_events, row.bottom) == row.track.layer

    def test_event_edge_keeps_its_own_gesture(self, timeline, doc) -> None:
        """Над телом реплики жест остаётся за ней — отбирать его нельзя.

        Реплика нарисована с отступом от границы дорожки, поэтому зона
        захвата задевает её нижние пиксели. Там побеждает реплика: её
        перетаскивают несравнимо чаще, чем меняют высоту дорожки.
        """
        row = next(r for r in timeline.rows() if r.track.is_subtitle)
        event = next(iter(doc.events))
        middle_x = timeline.ms_to_x((event.start + event.end) / 2)
        inside_event = row.bottom - 3
        assert timeline._event_at(middle_x, inside_event) is not None, "точка не в реплике"
        assert timeline._resize_edge_at(middle_x, inside_event) is None

    def test_ruler_is_not_a_track_edge(self, timeline: TimelineWidget) -> None:
        assert timeline._resize_edge_at(40.0, RULER_H / 2) is None

    def test_grab_zone_is_wide_enough_to_hit(self) -> None:
        """Четыре пикселя человек не ловит: промах даёт другое действие."""
        from sfstudio.ui.timeline import RESIZE_GRAB_PX

        assert RESIZE_GRAB_PX >= 5.0

    def test_cursor_hints_the_gesture(self, timeline: TimelineWidget) -> None:
        from PySide6.QtCore import Qt

        row = next(r for r in timeline.rows() if r.track.is_subtitle)
        timeline._update_cursor(HEADER_W / 2, row.bottom)
        assert timeline.cursor().shape() == Qt.SizeVerCursor


class TestReadableText:
    """Цвет надписи считается от подложки: цвет актора выбирает человек."""

    def test_dark_text_on_light_background(self) -> None:
        assert _readable_on(QColor(240, 240, 240)).lightness() < 60

    def test_light_text_on_dark_background(self) -> None:
        assert _readable_on(QColor(20, 20, 60)).lightness() > 200

    def test_yellow_counts_as_light(self) -> None:
        """По среднему из каналов жёлтый вышел бы тёмным, по яркости — светлым."""
        assert _readable_on(QColor(255, 255, 0)).lightness() < 60

    def test_blue_counts_as_dark(self) -> None:
        assert _readable_on(QColor(0, 0, 255)).lightness() > 200


class TestModelFolderMatching:
    """Имя модели сравнивается целиком: «large-v3» — часть «large-v3-turbo»."""

    def test_exact_name(self) -> None:
        assert folder_matches("large-v3", "large-v3")
        assert folder_matches("faster-whisper-large-v3", "large-v3")

    def test_longer_name_is_not_a_match(self) -> None:
        assert not folder_matches("faster-whisper-large-v3-turbo", "large-v3")
        assert not folder_matches("faster-distil-whisper-large-v3", "large-v3")

    def test_turbo_matches_itself(self) -> None:
        assert folder_matches("faster-whisper-large-v3-turbo", "large-v3-turbo")

    def test_installed_does_not_confuse_neighbours(self, tmp_path: Path) -> None:
        """Скачан turbo — значит установлен turbo, а не large-v3."""
        folder = tmp_path / "faster-whisper-large-v3-turbo"
        folder.mkdir()
        (folder / "model.bin").write_bytes(b"x")
        assert installed_models(tmp_path) == {"large-v3-turbo"}


class TestTurboModel:
    """Замена выбывшей: качество large при весе medium, и многоязычная."""

    def test_is_offered(self) -> None:
        assert any(i.name == "large-v3-turbo" for i in MODEL_CATALOG)

    def test_understands_russian(self) -> None:
        info = next(i for i in MODEL_CATALOG if i.name == "large-v3-turbo")
        assert info.supports("ru")
        assert not info.english_only

    def test_is_lighter_than_large(self) -> None:
        catalog = {i.name: i for i in MODEL_CATALOG}
        assert catalog["large-v3-turbo"].size_mb < catalog["large-v3"].size_mb / 1.5
