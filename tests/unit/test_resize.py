"""Тесты изменения размера субтитра за ручки рамки."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import Qt

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.effective import effective_style
from sfstudio.core.undo import UndoStack
from sfstudio.render.geometry import Handle
from sfstudio.ui.overlay import (
    _MAX_SCALE,
    _MIN_SCALE,
    _RESIZE_CURSORS,
    _RESIZE_HANDLES,
    OverlayController,
    _ratio,
)

pytestmark = pytest.mark.needs_gui


class FakeHost:
    """Хост, который только считает вызовы."""

    def __init__(self) -> None:
        self.cursor = Qt.ArrowCursor
        self.updates = 0
        self.edits = 0
        self.status: list[str] = []
        self.selection: list[int] = []

    def request_update(self) -> None:
        self.updates += 1

    def set_cursor_shape(self, shape) -> None:
        self.cursor = shape

    def notify_selection(self, eid: int) -> None:
        self.selection.append(eid)

    def notify_edited(self) -> None:
        self.edits += 1

    def notify_status(self, text: str) -> None:
        self.status.append(text)

    def overlay_size(self) -> tuple[int, int]:
        return 960, 540


@pytest.fixture
def controller() -> OverlayController:
    doc = SubtitleDocument.blank()
    doc.create_event(0, 5000, r"{\pos(960,540)}Реплика")
    return OverlayController(doc, UndoStack(doc), FakeHost())


def scale_of(controller: OverlayController, eid: int) -> tuple[float, float]:
    event = controller.document.by_eid(eid)
    effective = effective_style(event, controller.document.style_for(event))
    return effective.scale_x, effective.scale_y


class TestHandleSets:
    def test_all_eight_handles_resize(self) -> None:
        assert {
            Handle.NW, Handle.N, Handle.NE, Handle.E,
            Handle.SE, Handle.S, Handle.SW, Handle.W,
        } == _RESIZE_HANDLES

    def test_body_and_rotate_are_not_resize(self) -> None:
        """У них своё поведение: перенос и поворот."""
        assert Handle.BODY not in _RESIZE_HANDLES
        assert Handle.ROTATE not in _RESIZE_HANDLES

    def test_every_handle_has_a_cursor(self) -> None:
        """Курсор — единственный намёк, что ручку можно тянуть."""
        for handle in _RESIZE_HANDLES:
            assert handle in _RESIZE_CURSORS

    def test_diagonal_cursors_match_the_corner(self) -> None:
        assert _RESIZE_CURSORS[Handle.NW] == _RESIZE_CURSORS[Handle.SE]
        assert _RESIZE_CURSORS[Handle.NE] == _RESIZE_CURSORS[Handle.SW]
        assert _RESIZE_CURSORS[Handle.NW] != _RESIZE_CURSORS[Handle.NE]


class TestRatio:
    def test_proportional(self) -> None:
        assert _ratio(200.0, 100.0) == pytest.approx(2.0)

    def test_shrinking(self) -> None:
        assert _ratio(50.0, 100.0) == pytest.approx(0.5)

    def test_tiny_arm_gives_one(self) -> None:
        """Деление на почти ноль улетело бы в бесконечность от дрожания руки."""
        assert _ratio(100.0, 0.001) == 1.0

    def test_negative_arm_works(self) -> None:
        """Ручка слева от привязки: плечо отрицательное, отношение — нет."""
        assert _ratio(-200.0, -100.0) == pytest.approx(2.0)


class TestResizeGesture:
    def _grab(self, controller: OverlayController, handle: Handle,
              x: float, y: float) -> int:
        eid = controller.document.events[0].eid
        controller.set_selected(eid)
        controller._begin_resize(eid, handle, x, y)
        return eid

    def test_corner_scales_both_axes(self, controller: OverlayController) -> None:
        eid = self._grab(controller, Handle.SE, 600, 400)
        controller._update_resize(700, 450, Qt.NoModifier)
        scale_x, scale_y = scale_of(controller, eid)
        assert scale_x > 100
        assert scale_y > 100

    def test_corner_keeps_proportions(self, controller: OverlayController) -> None:
        """Углы тянут пропорционально — иначе текст «плющит»."""
        eid = self._grab(controller, Handle.SE, 600, 400)
        controller._update_resize(900, 420, Qt.NoModifier)
        scale_x, scale_y = scale_of(controller, eid)
        assert scale_x == pytest.approx(scale_y, rel=0.01)

    def test_shift_frees_the_proportions(self, controller: OverlayController) -> None:
        eid = self._grab(controller, Handle.SE, 600, 400)
        controller._update_resize(900, 420, Qt.ShiftModifier)
        scale_x, scale_y = scale_of(controller, eid)
        assert scale_x != pytest.approx(scale_y, rel=0.05)

    def test_side_handle_touches_one_axis(self, controller: OverlayController) -> None:
        eid = self._grab(controller, Handle.E, 600, 400)
        before_y = scale_of(controller, eid)[1]
        controller._update_resize(800, 500, Qt.NoModifier)
        scale_x, scale_y = scale_of(controller, eid)
        assert scale_x > 100
        assert scale_y == pytest.approx(before_y)

    def test_vertical_handle_touches_one_axis(self, controller: OverlayController) -> None:
        eid = self._grab(controller, Handle.S, 600, 400)
        before_x = scale_of(controller, eid)[0]
        controller._update_resize(800, 500, Qt.NoModifier)
        scale_x, scale_y = scale_of(controller, eid)
        assert scale_y > 100
        assert scale_x == pytest.approx(before_x)

    def test_scale_is_clamped(self, controller: OverlayController) -> None:
        """За пределами текст либо исчезает, либо уходит из кадра навсегда."""
        eid = self._grab(controller, Handle.SE, 600, 400)
        controller._update_resize(100_000, 100_000, Qt.NoModifier)
        scale_x, scale_y = scale_of(controller, eid)
        assert scale_x <= _MAX_SCALE
        assert scale_y <= _MAX_SCALE

        controller._update_resize(480, 270, Qt.NoModifier)
        scale_x, scale_y = scale_of(controller, eid)
        assert scale_x >= _MIN_SCALE
        assert scale_y >= _MIN_SCALE

    def test_writes_tags_not_style(self, controller: OverlayController) -> None:
        """Правка стиля переоформила бы все реплики с этим стилем."""
        eid = self._grab(controller, Handle.SE, 600, 400)
        controller._update_resize(700, 450, Qt.NoModifier)
        text = controller.document.by_eid(eid).text
        assert "\\fscx" in text
        assert "\\fscy" in text
        assert controller.document.styles["Default"].scale_x == 100.0

    def test_resize_is_undoable(self, controller: OverlayController) -> None:
        eid = self._grab(controller, Handle.SE, 600, 400)
        before = controller.document.by_eid(eid).text
        controller._update_resize(700, 450, Qt.NoModifier)
        while controller.undo.can_undo:
            controller.undo.undo()
        assert controller.document.by_eid(eid).text == before

    def test_status_reports_the_size(self, controller: OverlayController) -> None:
        self._grab(controller, Handle.SE, 600, 400)
        controller._update_resize(700, 450, Qt.NoModifier)
        assert controller._host.status
        assert "%" in controller._host.status[-1]

    def test_gesture_ends(self, controller: OverlayController) -> None:
        self._grab(controller, Handle.SE, 600, 400)
        assert controller._resize_handle is not None
        controller.on_release(Qt.LeftButton)
        assert controller._resize_handle is None

    def test_escape_cancels(self, controller: OverlayController) -> None:
        eid = self._grab(controller, Handle.SE, 600, 400)
        before = controller.document.by_eid(eid).text
        controller._update_resize(700, 450, Qt.NoModifier)
        controller.on_key(Qt.Key_Escape, Qt.NoModifier)
        assert controller.document.by_eid(eid).text == before

    def test_grab_next_to_anchor_is_ignored(self, controller: OverlayController) -> None:
        """От нулевого плеча коэффициент улетел бы в бесконечность."""
        eid = controller.document.events[0].eid
        controller.set_selected(eid)
        # Точка привязки реплики — ровно центр кадра, то есть центр виджета.
        controller._begin_resize(eid, Handle.SE, 480, 270)
        assert controller._resize_handle is None
