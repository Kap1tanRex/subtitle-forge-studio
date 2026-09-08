"""Тесты таймлайна: координаты, зум, раскладка дорожек, правка таймингов мышью."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import FpsModel
from sfstudio.core.undo import UndoStack
from sfstudio.media.keyframes import KeyframeIndex
from sfstudio.ui.timeline import HEADER_W, TimelineWidget

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def timeline(qapp: QApplication) -> TimelineWidget:
    doc = SubtitleDocument.blank()
    doc.create_event(1000, 2000, "Первая")
    doc.create_event(3000, 4000, "Вторая")
    doc.create_event(3500, 5000, "Перекрытие")
    widget = TimelineWidget(doc, UndoStack(doc))
    widget.resize(1000, 240)
    widget.set_fps(FpsModel())
    widget.fit_all()
    return widget


def press(w: TimelineWidget, x: float, y: float, mods=Qt.AltModifier) -> None:
    w.mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, QPointF(x, y), QPointF(x, y),
                    Qt.LeftButton, Qt.LeftButton, mods)
    )


def move(w: TimelineWidget, x: float, y: float, mods=Qt.AltModifier) -> None:
    w.mouseMoveEvent(
        QMouseEvent(QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
                    Qt.NoButton, Qt.LeftButton, mods)
    )


def release(w: TimelineWidget, x: float, y: float) -> None:
    w.mouseReleaseEvent(
        QMouseEvent(QEvent.MouseButtonRelease, QPointF(x, y), QPointF(x, y),
                    Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    )


def track_y(w: TimelineWidget, eid: int) -> float:
    """Вертикаль центра реплики: её дорожка плюс подстрока внутри дорожки."""
    w._rebuild_sublanes_if_needed()
    event = w._doc.by_eid(eid)
    row = w._row_for_layer(event.layer)
    assert row is not None, f"нет дорожки для слоя {event.layer}"
    lanes = max(1, w._sublanes_on.get(event.layer, 1))
    lane = min(w._sublane_of.get(eid, 0), lanes - 1)
    lane_h = (row.height - 4) / lanes
    return row.top + 2 + lane * lane_h + lane_h / 2


class TestCoordinates:
    def test_ms_x_roundtrip(self, timeline: TimelineWidget) -> None:
        for ms in (0, 500, 2500, 9000):
            assert timeline.x_to_ms(timeline.ms_to_x(ms)) == pytest.approx(ms)

    def test_fit_all_shows_whole_document(self, timeline: TimelineWidget) -> None:
        timeline.fit_all()
        assert timeline._view_start_ms == 0
        assert timeline.view_end_ms >= timeline._doc.duration

    def test_fit_selection_zooms_in(self, timeline: TimelineWidget) -> None:
        before = timeline._px_per_ms
        timeline.set_selected(timeline._doc.events[0].eid)
        timeline.fit_selection()
        assert timeline._px_per_ms > before
        assert timeline._view_start_ms <= timeline._doc.events[0].start


class TestZoom:
    def test_zoom_keeps_anchor_point_fixed(self, timeline: TimelineWidget) -> None:
        """Точка под курсором не должна уезжать при зуме."""
        anchor_x = 400.0
        before = timeline.x_to_ms(anchor_x)
        timeline.zoom_at(2.0, anchor_x)
        assert timeline.x_to_ms(anchor_x) == pytest.approx(before, abs=1.0)

    def test_zoom_out_is_bounded(self, timeline: TimelineWidget) -> None:
        """Нельзя отдалиться так, чтобы документ стал точкой."""
        for _ in range(50):
            timeline.zoom_at(0.5, 500)
        assert timeline.view_end_ms <= timeline.content_duration * 3

    def test_zoom_in_is_bounded(self, timeline: TimelineWidget) -> None:
        for _ in range(80):
            timeline.zoom_at(2.0, 500)
        assert timeline._px_per_ms <= 40.0

    def test_wheel_scrolls(self, timeline: TimelineWidget) -> None:
        timeline.zoom_at(4.0, 500)
        before = timeline._view_start_ms
        timeline.wheelEvent(
            # pixelDelta и angleDelta — целочисленные QPoint, не QPointF.
            QWheelEvent(QPointF(500, 100), QPointF(500, 100), QPoint(0, 0),
                        QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
        )
        assert timeline._view_start_ms > before

    def test_view_never_goes_negative(self, timeline: TimelineWidget) -> None:
        timeline.scroll_by(-100_000)
        assert timeline._view_start_ms >= 0


class TestSublaneLayout:
    """Внутри одной дорожки пересекающиеся реплики разводятся по подстрокам."""

    def test_non_overlapping_share_a_sublane(self, timeline: TimelineWidget) -> None:
        timeline._rebuild_sublanes_if_needed()
        doc = timeline._doc
        assert timeline._sublane_of[doc.events[0].eid] == timeline._sublane_of[doc.events[1].eid]

    def test_overlapping_go_to_separate_sublanes(self, timeline: TimelineWidget) -> None:
        timeline._rebuild_sublanes_if_needed()
        doc = timeline._doc
        assert timeline._sublane_of[doc.events[1].eid] != timeline._sublane_of[doc.events[2].eid]

    def test_layout_recomputed_after_edit(self, timeline: TimelineWidget) -> None:
        """Раскладка кэшируется по ревизии — она обязана обновиться после правки."""
        timeline._rebuild_sublanes_if_needed()
        doc = timeline._doc
        third = doc.events[2]
        from sfstudio.core.commands import SetTiming

        timeline._undo.run(SetTiming(third.eid, start=6000, end=7000))
        timeline._rebuild_sublanes_if_needed()
        assert timeline._sublane_of[third.eid] == 0, "после разведения подстрока одна"

    def test_events_on_different_layers_do_not_share_sublanes(
        self, timeline: TimelineWidget
    ) -> None:
        """Слой — это дорожка: реплики разных слоёв не толкаются между собой."""
        doc = timeline._doc
        doc.create_event(1000, 2000, "Надпись", layer=1)
        doc.sync_tracks()
        doc.bump_revision()
        timeline._rebuild_sublanes_if_needed()
        assert timeline._sublanes_on[0] >= 1
        assert timeline._sublanes_on[1] == 1


class TestHitTest:
    def test_edge_and_body_are_distinguished(self, timeline: TimelineWidget) -> None:
        event = timeline._doc.events[0]
        y = track_y(timeline, event.eid)
        assert timeline._event_at(timeline.ms_to_x(event.start), y)[1] == "start"
        assert timeline._event_at(timeline.ms_to_x(event.end), y)[1] == "end"
        mid = (event.start + event.end) // 2
        assert timeline._event_at(timeline.ms_to_x(mid), y)[1] == "body"

    def test_short_event_keeps_a_body(self, timeline: TimelineWidget) -> None:
        """У короткой реплики зоны краёв не должны съесть всё тело.

        Иначе её нельзя было бы сдвинуть целиком — только тянуть за края.
        """
        doc = timeline._doc
        short = doc.create_event(8000, 8120, "Кратко")
        timeline._layout_revision = -1
        timeline.fit_all()
        y = track_y(timeline, short.eid)
        mid = timeline.ms_to_x((short.start + short.end) / 2)
        hit = timeline._event_at(mid, y)
        assert hit is not None
        assert hit[1] == "body"

    def test_click_on_waveform_is_not_an_event(self, timeline: TimelineWidget) -> None:
        audio = timeline.rows()[-1]
        assert timeline._event_at(HEADER_W + 400, audio.top + 5) is None


class TestDragTimings:
    def test_drag_end_changes_only_end(self, timeline: TimelineWidget) -> None:
        event = timeline._doc.events[0]
        start_before = event.start
        y = track_y(timeline, event.eid)

        press(timeline, timeline.ms_to_x(event.end), y)
        move(timeline, timeline.ms_to_x(event.end + 800), y)
        release(timeline, timeline.ms_to_x(event.end + 800), y)

        assert event.start == start_before
        assert event.end == pytest.approx(2800, abs=60)

    def test_drag_start_changes_only_start(self, timeline: TimelineWidget) -> None:
        event = timeline._doc.events[0]
        end_before = event.end
        y = track_y(timeline, event.eid)

        press(timeline, timeline.ms_to_x(event.start), y)
        move(timeline, timeline.ms_to_x(event.start - 500), y)
        release(timeline, timeline.ms_to_x(event.start - 500), y)

        assert event.end == end_before
        assert event.start == pytest.approx(500, abs=60)

    def test_drag_body_keeps_duration(self, timeline: TimelineWidget) -> None:
        event = timeline._doc.events[0]
        duration = event.duration
        y = track_y(timeline, event.eid)
        mid_x = timeline.ms_to_x((event.start + event.end) / 2)

        press(timeline, mid_x, y)
        move(timeline, mid_x + 200, y)
        release(timeline, mid_x + 200, y)

        assert event.duration == pytest.approx(duration, abs=2)
        assert event.start > 1000

    def test_drag_is_one_undo_step(self, timeline: TimelineWidget) -> None:
        event = timeline._doc.events[0]
        y = track_y(timeline, event.eid)
        x = timeline.ms_to_x(event.end)
        press(timeline, x, y)
        for i in range(1, 25):
            move(timeline, x + i * 4, y)
        release(timeline, x + 96, y)

        assert timeline._undo.depth_used == 1
        timeline._undo.undo()
        assert timeline._doc.events[0].end == 2000

    def test_event_cannot_be_inverted(self, timeline: TimelineWidget) -> None:
        """Протаскивание конца за начало не должно давать отрицательную длину."""
        event = timeline._doc.events[0]
        y = track_y(timeline, event.eid)
        press(timeline, timeline.ms_to_x(event.end), y)
        move(timeline, timeline.ms_to_x(-5000), y)
        release(timeline, timeline.ms_to_x(-5000), y)
        assert event.end > event.start

    def test_start_cannot_go_negative(self, timeline: TimelineWidget) -> None:
        event = timeline._doc.events[0]
        y = track_y(timeline, event.eid)
        mid_x = timeline.ms_to_x((event.start + event.end) / 2)
        press(timeline, mid_x, y)
        move(timeline, timeline.ms_to_x(-50_000), y)
        release(timeline, timeline.ms_to_x(-50_000), y)
        assert event.start >= 0


class TestSnapping:
    def test_snaps_to_keyframe(self, timeline: TimelineWidget) -> None:
        timeline.set_keyframes(KeyframeIndex(times_ms=[2500]))
        event = timeline._doc.events[0]
        y = track_y(timeline, event.eid)
        target = timeline.ms_to_x(2480)

        press(timeline, timeline.ms_to_x(event.end), y, mods=Qt.NoModifier)
        move(timeline, target, y, mods=Qt.NoModifier)
        release(timeline, target, y)

        assert event.end == pytest.approx(2500, abs=45)

    def test_alt_disables_snapping(self, timeline: TimelineWidget) -> None:
        timeline.set_keyframes(KeyframeIndex(times_ms=[2500]))
        event = timeline._doc.events[0]
        y = track_y(timeline, event.eid)
        # Далеко от ключевого кадра, чтобы магнит точно не сработал по радиусу.
        target_ms = 2900
        press(timeline, timeline.ms_to_x(event.end), y)
        move(timeline, timeline.ms_to_x(target_ms), y)
        release(timeline, timeline.ms_to_x(target_ms), y)
        assert event.end == pytest.approx(target_ms, abs=60)

    def test_snapping_can_be_switched_off(self, timeline: TimelineWidget) -> None:
        timeline.set_snapping(False)
        timeline.set_keyframes(KeyframeIndex(times_ms=[2500]))
        event = timeline._doc.events[0]
        y = track_y(timeline, event.eid)
        press(timeline, timeline.ms_to_x(event.end), y, mods=Qt.NoModifier)
        move(timeline, timeline.ms_to_x(2480), y, mods=Qt.NoModifier)
        release(timeline, timeline.ms_to_x(2480), y)
        assert event.end == pytest.approx(2480, abs=5)


class TestSeekAndCreate:
    def test_click_on_wave_seeks(self, timeline: TimelineWidget) -> None:
        seen: list[int] = []
        timeline.time_changed.connect(seen.append)
        audio = timeline.rows()[-1]
        x = HEADER_W + 300
        press(timeline, x, audio.top + 10)
        assert seen and seen[-1] == pytest.approx(int(timeline.x_to_ms(x)), abs=2)

    def test_ctrl_click_on_empty_track_creates_event(self, timeline: TimelineWidget) -> None:
        before = len(timeline._doc)
        y = timeline._row_for_layer(0).top + 8
        x = timeline.ms_to_x(9000)
        press(timeline, x, y, mods=Qt.ControlModifier | Qt.AltModifier)
        move(timeline, timeline.ms_to_x(10_500), y)
        release(timeline, timeline.ms_to_x(10_500), y)

        assert len(timeline._doc) == before + 1
        created = timeline._doc.events[-1]
        assert created.start == pytest.approx(9000, abs=80)
        assert created.duration > 500

    def test_created_event_is_undoable(self, timeline: TimelineWidget) -> None:
        before = len(timeline._doc)
        y = timeline._row_for_layer(0).top + 8
        press(timeline, timeline.ms_to_x(9000), y, mods=Qt.ControlModifier | Qt.AltModifier)
        release(timeline, timeline.ms_to_x(9000), y)
        while timeline._undo.can_undo:
            timeline._undo.undo()
        assert len(timeline._doc) == before


class TestPaintSafety:
    def _render(self, widget: TimelineWidget):
        from PySide6.QtGui import QImage

        image = QImage(widget.size(), QImage.Format_ARGB32)
        image.fill(0)
        widget.render(image)
        return image

    def test_paints_without_peaks(self, timeline: TimelineWidget) -> None:
        assert not self._render(timeline).isNull()

    def test_paints_with_empty_document(self, qapp: QApplication) -> None:
        doc = SubtitleDocument.blank()
        widget = TimelineWidget(doc, UndoStack(doc))
        widget.resize(600, 200)
        assert not self._render(widget).isNull()

    def test_paints_with_broken_peaks(self, timeline: TimelineWidget) -> None:
        """Повреждённый кэш не должен ронять окно."""

        class Broken:
            duration_ms = 5000

            def slice(self, *args, **kwargs):
                raise RuntimeError("битый кэш")

        timeline.set_peaks(Broken())
        assert not self._render(timeline).isNull()

    def test_grid_step_grows_when_zoomed_out(self, timeline: TimelineWidget) -> None:
        timeline.zoom_at(20.0, 0)
        fine = timeline._grid_step()
        timeline.zoom_at(0.001, 0)
        coarse = timeline._grid_step()
        assert coarse > fine
