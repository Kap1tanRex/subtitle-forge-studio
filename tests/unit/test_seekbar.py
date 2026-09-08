"""Тесты полосы положения, высоты дорожек и синхронизации времени."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from sfstudio.core.commands import AddTrack
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.icons import ICON_NAMES, make_icon
from sfstudio.ui.seekbar import SeekBar
from sfstudio.ui.timeline import (
    HEADER_W,
    MAX_TRACK_ZOOM,
    MIN_TRACK_H,
    MIN_TRACK_ZOOM,
    TimelineWidget,
)

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bar(qapp: QApplication) -> SeekBar:
    widget = SeekBar()
    widget.resize(400, 26)
    widget.set_duration(60_000)
    return widget


def click(w: SeekBar, x: float) -> None:
    point = QPointF(x, w.height() / 2)
    w.mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, point, point,
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    )


class TestSeekBar:
    def test_position_maps_to_x(self, bar: SeekBar) -> None:
        bar.set_position(30_000)
        middle = bar._x_of(30_000)
        track = bar._track_rect()
        assert middle == pytest.approx(track.center().x(), abs=1)

    def test_click_seeks_to_that_time(self, bar: SeekBar) -> None:
        seen: list[int] = []
        bar.seek_requested.connect(seen.append)
        track = bar._track_rect()
        click(bar, track.center().x())
        assert seen and seen[0] == pytest.approx(30_000, rel=0.05)

    def test_click_at_the_start(self, bar: SeekBar) -> None:
        seen: list[int] = []
        bar.seek_requested.connect(seen.append)
        click(bar, bar._track_rect().left())
        assert seen == [0]

    def test_position_is_clamped_to_duration(self, bar: SeekBar) -> None:
        assert bar._ms_at(bar.width() * 2) == 60_000
        assert bar._ms_at(-100) == 0

    def test_no_seek_without_duration(self, qapp: QApplication) -> None:
        """Пока файл не открыт, перематывать некуда — клик ничего не делает."""
        empty = SeekBar()
        empty.resize(400, 26)
        seen: list[int] = []
        empty.seek_requested.connect(seen.append)
        click(empty, 200)
        assert seen == []

    def test_drag_ignores_incoming_position(self, bar: SeekBar) -> None:
        """Иначе ручка прыгает назад под пальцем от досланной позиции плеера."""
        click(bar, bar._track_rect().center().x())
        before = bar.position_ms
        bar.set_position(0)
        assert bar.position_ms == before

    def test_position_resumes_after_drag(self, bar: SeekBar) -> None:
        click(bar, bar._track_rect().center().x())
        bar.mouseReleaseEvent(
            QMouseEvent(QEvent.MouseButtonRelease, QPointF(0, 0), QPointF(0, 0),
                        Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        )
        bar.set_position(1234)
        assert bar.position_ms == 1234

    def test_marks_are_accepted(self, bar: SeekBar) -> None:
        bar.set_marks([(0, 1000), (2000, 3000)])
        assert len(bar._marks) == 2

    def test_paints_with_and_without_duration(self, qapp: QApplication) -> None:
        for duration in (0, 60_000):
            widget = SeekBar()
            widget.resize(400, 26)
            widget.set_duration(duration)
            widget.set_marks([(0, 1000)])
            widget.set_position(duration // 2)
            widget.render(QPixmap(widget.size()))


class TestIcons:
    @pytest.mark.parametrize("name", ICON_NAMES)
    def test_every_icon_renders(self, qapp: QApplication, name: str) -> None:
        """Иконки рисуются кодом, а не берутся из шрифта эмодзи."""
        icon = make_icon(name, "#FFFFFF")
        assert not icon.isNull()
        assert not icon.pixmap(18, 18).isNull()

    def test_icon_is_not_blank(self, qapp: QApplication) -> None:
        """Пустая картинка прошла бы проверку isNull, но выглядела бы дырой."""
        image = make_icon("play", "#FFFFFF", 18).pixmap(18, 18).toImage()
        opaque = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        )
        assert opaque > 20


class TestTrackHeight:
    @pytest.fixture
    def timeline(self, qapp: QApplication) -> TimelineWidget:
        doc = SubtitleDocument.blank()
        doc.create_event(1000, 2000, "Реплика")
        widget = TimelineWidget(doc, UndoStack(doc))
        widget.resize(1000, 400)
        return widget

    def test_default_height_is_readable(self, timeline: TimelineWidget) -> None:
        """Полоса должна вмещать текст реплики, а не быть ниткой."""
        row = timeline._row_for_layer(0)
        assert row.height >= 40

    def test_custom_height_is_used(self, timeline: TimelineWidget) -> None:
        timeline._doc.tracks.by_layer(0).height = 80
        assert timeline._row_for_layer(0).height == pytest.approx(80)

    def test_zoom_scales_all_tracks(self, timeline: TimelineWidget) -> None:
        before = timeline._row_for_layer(0).height
        timeline.zoom_tracks(2.0)
        assert timeline._row_for_layer(0).height == pytest.approx(before * 2)

    def test_zoom_is_bounded(self, timeline: TimelineWidget) -> None:
        for _ in range(20):
            timeline.zoom_tracks(2.0)
        assert timeline.track_zoom == pytest.approx(MAX_TRACK_ZOOM)
        for _ in range(40):
            timeline.zoom_tracks(0.5)
        assert timeline.track_zoom == pytest.approx(MIN_TRACK_ZOOM)

    def test_reset_zoom(self, timeline: TimelineWidget) -> None:
        timeline.zoom_tracks(2.0)
        timeline.reset_track_zoom()
        assert timeline.track_zoom == 1.0

    def test_edge_is_detected_in_the_header(self, timeline: TimelineWidget) -> None:
        row = timeline._row_for_layer(0)
        assert timeline._resize_edge_at(40, row.bottom) == 0

    def test_edge_is_detected_in_the_track_area_too(
        self, timeline: TimelineWidget
    ) -> None:
        """Границу тянут и в самой дорожке, не только в колонке имён.

        Раньше жест работал лишь слева, в полосе шириной полтора сантиметра,
        и высота дорожки выглядела неизменяемой мышью. Реплике жест
        по-прежнему уступает — это проверяется отдельно.
        """
        row = timeline._row_for_layer(0)
        empty_spot = HEADER_W + 100
        assert timeline._event_at(empty_spot, row.bottom) is None, "точка занята репликой"
        assert timeline._resize_edge_at(empty_spot, row.bottom) == 0

    def test_dragging_the_edge_changes_height(self, timeline: TimelineWidget) -> None:
        row = timeline._row_for_layer(0)
        point = QPointF(40, row.bottom)
        timeline.mousePressEvent(
            QMouseEvent(QEvent.MouseButtonPress, point, point,
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        )
        moved = QPointF(40, row.bottom + 40)
        timeline.mouseMoveEvent(
            QMouseEvent(QEvent.MouseMove, moved, moved,
                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        )
        assert timeline._row_for_layer(0).height > row.height

    def test_resize_is_one_undo_step(self, timeline: TimelineWidget) -> None:
        """Команда на каждое движение мыши забила бы историю сотнями записей."""
        row = timeline._row_for_layer(0)
        before = timeline._undo.depth_used
        point = QPointF(40, row.bottom)
        timeline.mousePressEvent(
            QMouseEvent(QEvent.MouseButtonPress, point, point,
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        )
        for offset in (10, 20, 30, 40):
            moved = QPointF(40, row.bottom + offset)
            timeline.mouseMoveEvent(
                QMouseEvent(QEvent.MouseMove, moved, moved,
                            Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            )
        end = QPointF(40, row.bottom + 40)
        timeline.mouseReleaseEvent(
            QMouseEvent(QEvent.MouseButtonRelease, end, end,
                        Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        )
        assert timeline._undo.depth_used == before + 1

    def test_resize_is_undoable(self, timeline: TimelineWidget) -> None:
        row = timeline._row_for_layer(0)
        original = timeline._doc.tracks.by_layer(0).height
        point = QPointF(40, row.bottom)
        timeline.mousePressEvent(
            QMouseEvent(QEvent.MouseButtonPress, point, point,
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        )
        moved = QPointF(40, row.bottom + 50)
        timeline.mouseMoveEvent(
            QMouseEvent(QEvent.MouseMove, moved, moved,
                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        )
        timeline.mouseReleaseEvent(
            QMouseEvent(QEvent.MouseButtonRelease, moved, moved,
                        Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        )
        timeline._undo.undo()
        assert timeline._doc.tracks.by_layer(0).height == original

    def test_height_never_collapses(self, timeline: TimelineWidget) -> None:
        """Нулевую высоту дорожки уже не ухватить обратно."""
        row = timeline._row_for_layer(0)
        point = QPointF(40, row.bottom)
        timeline.mousePressEvent(
            QMouseEvent(QEvent.MouseButtonPress, point, point,
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        )
        moved = QPointF(40, row.bottom - 500)
        timeline.mouseMoveEvent(
            QMouseEvent(QEvent.MouseMove, moved, moved,
                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        )
        assert timeline._row_for_layer(0).height >= MIN_TRACK_H

    def test_height_survives_a_save(self, timeline: TimelineWidget) -> None:
        from sfstudio.io.formats.ass import read_ass, write_ass

        timeline._undo.run(AddTrack("Надписи"))
        timeline._doc.tracks.by_layer(1).height = 72
        back = read_ass(write_ass(timeline._doc))
        assert back.tracks.by_layer(1).height == 72


class TestTimeSync:
    """Время едино для кадра, полосы и таймлайна."""

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

    def test_timeline_moves_the_seek_bar(self, window) -> None:
        """Главное требование: курсор таймлайна и полоса под кадром сходятся."""
        window._on_timeline_time(4321)
        assert window.transport.seek.position_ms == 4321

    def test_seek_bar_moves_the_timeline(self, window) -> None:
        window._on_seek_requested(2500)
        assert window.timeline.time_ms == 2500

    def test_selecting_a_row_moves_the_seek_bar(self, window) -> None:
        event = window._doc.events[0]
        window._select_eid(event.eid)
        middle = (event.start + event.end) // 2
        assert window.transport.seek.position_ms == middle

    def test_duration_without_video_comes_from_the_document(self, window) -> None:
        """Без видео полоса нулевой длины была бы бесполезна."""
        assert window.transport.seek.duration_ms == window._doc.duration

    def test_marks_match_the_events(self, window) -> None:
        assert len(window.transport.seek._marks) == len(window._doc)
