"""Попадание мышью по репликам таймлайна.

Отдельный файл, потому что здесь одна конкретная беда, которую надо
удержать. Пересекающиеся реплики раскладываются по подстрокам внутри
дорожки, и **одной такой пары хватает, чтобы поделить всю дорожку**. У
остальных реплик — тех, что ни с кем не пересекаются, — под нарисованной
полоской остаётся пустая половина. Щелчок по ней раньше не попадал никуда:
со стороны это выглядело как «выделение мышью перестало работать, а
перетаскивание не работает вовсе».
"""

from __future__ import annotations

import statistics
import time

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.timeline import DragMode, TimelineWidget


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def timeline(qapp) -> TimelineWidget:
    """Дорожка, где первые две реплики наложены, а третья и четвёртая — нет.

    Так выглядит обычный переведённый фильм: пара реплик внахлёст на всю
    дорожку, остальные идут подряд.
    """
    doc = SubtitleDocument.blank()
    doc.create_event(0, 4000, "Первая")
    doc.create_event(2000, 6000, "Вторая, внахлёст с первой")
    doc.create_event(8000, 11000, "Третья, сама по себе")
    doc.create_event(13000, 16000, "Четвёртая, сама по себе")

    widget = TimelineWidget(doc, UndoStack(doc))
    widget.resize(1200, 320)
    widget.fit_all()
    widget._rebuild_sublanes_if_needed()
    return widget


def track_row(timeline: TimelineWidget):
    return next(r for r in timeline.rows() if r.track.is_subtitle)


def press(timeline: TimelineWidget, x: float, y: float,
          modifiers=Qt.NoModifier) -> None:
    timeline.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, QPointF(x, y),
            timeline.mapToGlobal(QPoint(int(x), int(y))),
            Qt.LeftButton, Qt.LeftButton, modifiers,
        )
    )


def middle_x(timeline: TimelineWidget, event) -> float:
    return timeline.ms_to_x((event.start + event.end) / 2)


class TestSublanesSplitTheTrack:
    def test_the_track_really_is_split(self, timeline) -> None:
        """Проверка предпосылки: без деления остальные тесты ничего не значат."""
        assert timeline._sublanes_on.get(0, 1) == 2

    @pytest.mark.parametrize("part", [0.15, 0.5, 0.85])
    def test_click_anywhere_in_the_track_selects_the_event(self, timeline, part) -> None:
        """Реплика без соседей ловится по всей высоте дорожки, а не только
        в своей половине: человек целится в полоску, а промахивается мимо
        пустого места, которого он не видит."""
        row = track_row(timeline)
        lonely = timeline._doc.events[2]
        press(timeline, middle_x(timeline, lonely), row.top + row.height * part)
        assert timeline.selected_eids == [lonely.eid]
        assert timeline._drag.mode == DragMode.BODY

    def test_overlapping_events_are_still_chosen_by_height(self, timeline) -> None:
        """Там, где реплики действительно наложены, выбирает вертикаль —
        иначе до нижней из них было бы не добраться."""
        row = track_row(timeline)
        first, second = timeline._doc.events[0], timeline._doc.events[1]
        x = timeline.ms_to_x(3000)  # общий для обеих участок

        press(timeline, x, row.top + row.height * 0.25)
        top_pick = timeline.selected_eids

        press(timeline, x, row.top + row.height * 0.75)
        bottom_pick = timeline.selected_eids

        assert top_pick != bottom_pick
        assert set(top_pick + bottom_pick) == {first.eid, second.eid}

    def test_empty_place_still_starts_a_rubber_band(self, timeline) -> None:
        """Прощать промах по вертикали — не то же самое, что ловить всё
        подряд: там, где реплики нет вовсе, должна начинаться рамка."""
        row = track_row(timeline)
        press(timeline, timeline.ms_to_x(7000), row.top + row.height / 2)
        assert timeline.selected_eids == []
        assert timeline._drag.mode == DragMode.RUBBER

    def test_edges_keep_their_meaning(self, timeline) -> None:
        """Захват края правит границу реплики, а не двигает её целиком."""
        row = track_row(timeline)
        lonely = timeline._doc.events[2]
        press(timeline, timeline.ms_to_x(lonely.end), row.top + row.height / 2)
        assert timeline._drag.mode == DragMode.END


class TestTrackResizeDoesNotStealClicks:
    """Полоса захвата высоты дорожки идёт и по области реплик."""

    def test_click_near_the_bottom_edge_still_selects(self, timeline) -> None:
        row = track_row(timeline)
        lonely = timeline._doc.events[3]
        press(timeline, middle_x(timeline, lonely), row.bottom - 2)
        assert timeline.selected_eids == [lonely.eid]
        assert timeline._drag.mode == DragMode.BODY

    def test_bottom_edge_without_an_event_still_resizes(self, timeline) -> None:
        """Растягивать дорожку мышью по-прежнему можно — на пустом месте."""
        row = track_row(timeline)
        press(timeline, timeline.ms_to_x(7000), row.bottom - 2)
        assert timeline._drag.mode == DragMode.RESIZE_TRACK


class TestDragging:
    def test_body_drag_moves_the_event(self, timeline) -> None:
        row = track_row(timeline)
        target = timeline._doc.events[2]
        before = (target.start, target.end)
        x, y = middle_x(timeline, target), row.bottom - 8

        press(timeline, x, y)
        timeline.mouseMoveEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseMove, QPointF(x + 80, y),
                timeline.mapToGlobal(QPoint(int(x + 80), int(y))),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier,
            )
        )
        moved = timeline._doc.by_eid(target.eid)
        assert (moved.start, moved.end) != before
        assert moved.end - moved.start == before[1] - before[0]


class TestHitTestCost:
    """Проверка попадания идёт на каждое движение мыши — и на всём фильме тоже."""

    @pytest.mark.slow
    def test_zoomed_out_hit_test_stays_cheap(self, qapp) -> None:
        """При обзоре всего документа перебор всех видимых реплик стоил
        9 мс на движение мыши. Спрашиваем документ про окрестность курсора,
        а не про весь экран; порог — с большим запасом к измеренным 0,1 мс.
        """
        doc = SubtitleDocument.blank()
        for i in range(20_000):
            doc.create_event(i * 1500, i * 1500 + 1200, f"Реплика {i}")

        widget = TimelineWidget(doc, UndoStack(doc))
        widget.resize(1920, 400)
        widget.fit_all()
        widget._rebuild_sublanes_if_needed()
        row = track_row(widget)
        y = row.top + row.height / 2

        runs = []
        for _ in range(5):
            started = time.perf_counter()
            for step in range(200):
                widget._event_at(200 + (step % 1500), y)
            runs.append((time.perf_counter() - started) * 1000 / 200)
        assert statistics.median(runs) < 2.0
