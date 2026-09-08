"""Множественное выделение на таймлайне: рамка, Ctrl и групповой сдвиг.

`_selected` было одним числом: сдвинуть десяток реплик, обведя их рамкой,
было нельзя — при том, что в таблице множественное выделение работало, и
команды давно принимали списки.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.timeline import HEADER_W, TimelineWidget

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    for i in range(5):
        document.create_event(i * 2000, i * 2000 + 1500, f"Реплика {i}")
    return document


@pytest.fixture
def timeline(qapp: QApplication, doc: SubtitleDocument) -> TimelineWidget:
    widget = TimelineWidget(doc, UndoStack(doc))
    widget.resize(1000, 400)
    return widget


def _mouse(kind, x, y, button, buttons, modifiers) -> QMouseEvent:
    """Событие мыши с обеими координатами: без глобальной Qt ругается на
    устаревший конструктор, а тридцать предупреждений в прогоне прячут
    настоящие."""
    point = QPointF(x, y)
    return QMouseEvent(kind, point, point, button, buttons, modifiers)


def press(widget, x, y, *, modifiers=Qt.NoModifier) -> None:
    widget.mousePressEvent(
        _mouse(QEvent.MouseButtonPress, x, y, Qt.LeftButton, Qt.LeftButton, modifiers)
    )


def move(widget, x, y) -> None:
    widget.mouseMoveEvent(
        _mouse(QEvent.MouseMove, x, y, Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    )


def release(widget, x, y) -> None:
    widget.mouseReleaseEvent(
        _mouse(QEvent.MouseButtonRelease, x, y, Qt.LeftButton, Qt.NoButton,
               Qt.NoModifier)
    )


def event_point(widget, event) -> tuple[float, float]:
    """Точка в середине реплики."""
    row = widget._row_for_layer(event.layer)
    x = widget.ms_to_x((event.start + event.end) / 2)
    return x, row.top + row.height / 2


class TestSingleSelection:
    def test_click_selects_one(self, timeline, doc) -> None:
        press(timeline, *event_point(timeline, doc.events[1]))
        assert timeline.selected_eids == [doc.events[1].eid]

    def test_click_on_empty_space_clears(self, timeline, doc) -> None:
        press(timeline, *event_point(timeline, doc.events[1]))
        row = timeline._row_for_layer(0)
        empty = timeline.ms_to_x(50_000)
        press(timeline, empty, row.top + row.height / 2)
        release(timeline, empty, row.top + row.height / 2)
        assert timeline.selected_eids == []

    def test_selection_from_outside_replaces(self, timeline, doc) -> None:
        """Выбрали одну строку в таблице — на таймлайне тоже одна."""
        timeline.set_selection([e.eid for e in doc.events[:3]])
        timeline.set_selected(doc.events[4].eid)
        assert timeline.selected_eids == [doc.events[4].eid]


class TestCtrlClick:
    def test_ctrl_adds_to_selection(self, timeline, doc) -> None:
        press(timeline, *event_point(timeline, doc.events[0]))
        press(timeline, *event_point(timeline, doc.events[2]),
              modifiers=Qt.ControlModifier)
        assert timeline.selected_eids == [doc.events[0].eid, doc.events[2].eid]

    def test_ctrl_removes_when_already_selected(self, timeline, doc) -> None:
        """Промахнулись на одну строку — этим же движением её и убирают."""
        press(timeline, *event_point(timeline, doc.events[0]))
        press(timeline, *event_point(timeline, doc.events[1]),
              modifiers=Qt.ControlModifier)
        press(timeline, *event_point(timeline, doc.events[1]),
              modifiers=Qt.ControlModifier)
        assert timeline.selected_eids == [doc.events[0].eid]

    def test_leader_follows_the_last_click(self, timeline, doc) -> None:
        press(timeline, *event_point(timeline, doc.events[0]))
        press(timeline, *event_point(timeline, doc.events[3]),
              modifiers=Qt.ControlModifier)
        assert timeline._selected == doc.events[3].eid


class TestRubberBand:
    def test_dragging_over_empty_space_selects_a_range(self, timeline, doc) -> None:
        row = timeline._row_for_layer(0)
        y = row.top + 3
        # Начинаем в середине промежутка между репликами: у самого края
        # реплики нажатие означало бы правку её границы — зона захвата края
        # при этом масштабе занимает около 120 мс.
        press(timeline, timeline.ms_to_x(1750), y)
        move(timeline, timeline.ms_to_x(5700), row.bottom - 3)
        release(timeline, timeline.ms_to_x(5700), row.bottom - 3)

        chosen = timeline.selected_eids
        assert chosen == [doc.events[1].eid, doc.events[2].eid]

    def test_click_without_moving_still_seeks(self, timeline, doc) -> None:
        """Порог сдвига обязателен: дрогнувшая рука не должна выделять."""
        row = timeline._row_for_layer(0)
        y = row.top + 3
        seen: list[int] = []
        timeline.time_changed.connect(seen.append)

        empty = timeline.ms_to_x(12_000)  # после последней реплики
        press(timeline, empty, y)
        release(timeline, empty, y)
        assert seen, "перемотка по щелчку пропала"
        assert timeline.selected_eids == []

    def test_partial_overlap_counts(self, timeline, doc) -> None:
        """Реплику шире экрана иначе было бы не выделить вовсе."""
        row = timeline._row_for_layer(0)
        y = row.top + 3
        second = doc.events[1]
        # Рамка начинается в промежутке и лишь чуть задевает реплику.
        press(timeline, timeline.ms_to_x(1750), y)
        move(timeline, timeline.ms_to_x(second.start + 100), row.bottom - 3)
        release(timeline, timeline.ms_to_x(second.start + 100), row.bottom - 3)
        assert second.eid in timeline.selected_eids

    def test_shift_extends_existing_selection(self, timeline, doc) -> None:
        """Добавляет рамка по Shift: Ctrl по пустому месту создаёт реплику."""
        press(timeline, *event_point(timeline, doc.events[4]))
        row = timeline._row_for_layer(0)
        y = row.top + 3
        press(timeline, timeline.ms_to_x(1750), y, modifiers=Qt.ShiftModifier)
        move(timeline, timeline.ms_to_x(3600), row.bottom - 3)
        release(timeline, timeline.ms_to_x(3600), row.bottom - 3)

        chosen = set(timeline.selected_eids)
        assert doc.events[4].eid in chosen, "прежнее выделение потерялось"
        assert doc.events[1].eid in chosen

    def test_header_column_does_not_start_a_band(self, timeline) -> None:
        row = timeline._row_for_layer(0)
        press(timeline, HEADER_W / 2, row.top + 3)
        assert timeline._drag.mode != 7  # RUBBER


class TestGroupDrag:
    def test_group_moves_together(self, timeline, doc) -> None:
        first, second = doc.events[0], doc.events[1]
        gap = second.start - first.start
        timeline.set_selection([first.eid, second.eid])

        x, y = event_point(timeline, first)
        press(timeline, x, y)
        shift_ms = 3000
        move(timeline, timeline.ms_to_x(first.start + 750 + shift_ms), y)
        release(timeline, timeline.ms_to_x(first.start + 750 + shift_ms), y)

        assert second.start - first.start == gap, "расстояние внутри группы поехало"
        assert first.start > 0

    def test_group_does_not_pile_up_at_zero(self, timeline, doc) -> None:
        """Уехав в минус, реплики слиплись бы у нуля и потеряли расстояния."""
        first, second = doc.events[0], doc.events[1]
        gap = second.start - first.start
        timeline.set_selection([first.eid, second.eid])

        x, y = event_point(timeline, second)
        press(timeline, x, y)
        move(timeline, timeline.ms_to_x(-50_000), y)
        release(timeline, timeline.ms_to_x(-50_000), y)

        assert first.start >= 0
        assert second.start - first.start == gap

    def test_group_drag_is_one_undo(self, timeline, doc) -> None:
        """Иначе возврат стоил бы по нажатию Ctrl+Z на каждую реплику."""
        undo = timeline._undo
        timeline.set_selection([e.eid for e in doc.events[:3]])
        before = [(e.start, e.end) for e in doc.events]

        x, y = event_point(timeline, doc.events[0])
        press(timeline, x, y)
        move(timeline, x + 200, y)
        release(timeline, x + 200, y)

        undo.undo()
        assert [(e.start, e.end) for e in doc.events] == before

    def test_dragging_outside_the_selection_moves_only_one(self, timeline, doc) -> None:
        timeline.set_selection([doc.events[0].eid, doc.events[1].eid])
        third = doc.events[2]
        untouched = doc.events[0].start

        x, y = event_point(timeline, third)
        press(timeline, x, y)
        move(timeline, x + 150, y)
        release(timeline, x + 150, y)

        assert doc.events[0].start == untouched
        assert timeline.selected_eids == [third.eid]


class TestCtrlStillCreates:
    def test_ctrl_on_empty_space_creates_an_event(self, timeline, doc) -> None:
        """Прежнее назначение Ctrl не отобрано рамкой выделения."""
        before = len(doc.events)
        row = timeline._row_for_layer(0)
        press(timeline, timeline.ms_to_x(20_000), row.top + row.height / 2,
              modifiers=Qt.ControlModifier)
        release(timeline, timeline.ms_to_x(20_000), row.top + row.height / 2)
        assert len(doc.events) == before + 1
