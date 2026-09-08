"""Сквозной тест жеста перетаскивания через настоящий виджет.

Проверяет то, ради чего затевался прототип: путь «мышь → хит-тест → геометрия →
команда → документ → отмена» работает целиком, а не по частям.

Тесты идут в offscreen-режиме, поэтому не требуют дисплея и годятся для CI.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.render import geometry as geo
from sfstudio.ui.preview import PreviewCanvas

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp: QApplication) -> PreviewCanvas:
    doc = SubtitleDocument.blank((1920, 1080))
    doc.styles["Default"].fontsize = 64
    doc.create_event(0, 5000, "Перетащи меня")
    widget = PreviewCanvas(doc, UndoStack(doc))
    widget.resize(960, 540)  # ровно 16:9 → без полей, масштаб 1:2
    widget.set_time(2000)
    if widget.overlay.measurer is None:
        pytest.skip("нет libass — измерять нечем")
    return widget


def press(widget: PreviewCanvas, x: float, y: float, mods=Qt.NoModifier) -> None:
    widget.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress, QPointF(x, y), QPointF(x, y),
            Qt.LeftButton, Qt.LeftButton, mods,
        )
    )


def move(widget: PreviewCanvas, x: float, y: float, mods=Qt.NoModifier) -> None:
    widget.mouseMoveEvent(
        QMouseEvent(
            QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
            Qt.NoButton, Qt.LeftButton, mods,
        )
    )


def release(widget: PreviewCanvas, x: float, y: float) -> None:
    widget.mouseReleaseEvent(
        QMouseEvent(
            QEvent.MouseButtonRelease, QPointF(x, y), QPointF(x, y),
            Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
        )
    )


def body_point(widget: PreviewCanvas) -> tuple[float, float]:
    """Точка внутри тела единственного события, в координатах виджета."""
    doc = widget.overlay.document
    rect = geo.event_bbox(doc.events[0], doc, widget.overlay.measurer)
    assert rect is not None
    wr = widget.overlay.transform().rect_to_widget(rect)
    return (wr.center_x, wr.center_y)


class TestDrag:
    def test_click_selects(self, canvas: PreviewCanvas) -> None:
        x, y = body_point(canvas)
        press(canvas, x, y)
        assert canvas.overlay.selected == canvas.overlay.document.events[0].eid

    def test_click_outside_deselects(self, canvas: PreviewCanvas) -> None:
        x, y = body_point(canvas)
        press(canvas, x, y)
        press(canvas, 5, 5)
        assert canvas.overlay.selected is None

    def test_drag_writes_pos_tag(self, canvas: PreviewCanvas) -> None:
        event = canvas.overlay.document.events[0]
        assert event.position() is None  # изначально позиционируется стилем

        x, y = body_point(canvas)
        press(canvas, x, y)
        move(canvas, x - 200, y - 100, Qt.AltModifier)  # Alt — без магнитов
        release(canvas, x - 200, y - 100)

        assert event.position() is not None
        assert r"\pos(" in event.text

    def test_drag_moves_by_exact_delta(self, canvas: PreviewCanvas) -> None:
        """Субтитр смещается ровно на столько, на сколько сдвинули мышь."""
        canvas.resize(960, 540)
        event = canvas.overlay.document.events[0]
        vt = canvas.overlay.transform()

        x, y = body_point(canvas)
        press(canvas, x, y)
        before = geo.effective_anchor(
            event, canvas.overlay.document.style_for(event), geo.Size(1, 1), (1920, 1080)
        )

        dx, dy = -120.0, -60.0
        move(canvas, x + dx, y + dy, Qt.AltModifier)
        release(canvas, x + dx, y + dy)

        after = event.position()
        assert after is not None
        expected_dx, expected_dy = vt.delta_to_script(dx, dy)
        assert after[0] == pytest.approx(before[0] + expected_dx, abs=1.5)
        assert after[1] == pytest.approx(before[1] + expected_dy, abs=1.5)

    def test_no_jump_when_grabbing_off_centre(self, canvas: PreviewCanvas) -> None:
        """Захват за край не должен телепортировать субтитр центром под курсор."""
        doc = canvas.overlay.document
        event = doc.events[0]
        rect = geo.event_bbox(event, doc, canvas.overlay.measurer)
        wr = canvas.overlay.transform().rect_to_widget(rect)

        grab_x = wr.left + 12  # у левого края, далеко от центра
        grab_y = wr.center_y
        press(canvas, grab_x, grab_y)
        move(canvas, grab_x, grab_y, Qt.AltModifier)  # мышь не двигалась
        release(canvas, grab_x, grab_y)

        moved = geo.event_bbox(event, doc, canvas.overlay.measurer)
        assert moved is not None
        assert moved.center_x == pytest.approx(rect.center_x, abs=2.0)
        assert moved.center_y == pytest.approx(rect.center_y, abs=2.0)

    def test_whole_gesture_is_one_undo(self, canvas: PreviewCanvas) -> None:
        x, y = body_point(canvas)
        press(canvas, x, y)
        for step in range(1, 20):
            move(canvas, x - step * 8, y - step * 4, Qt.AltModifier)
        release(canvas, x - 152, y - 76)

        assert canvas.overlay.undo.depth_used == 1, "жест должен давать одну отмену"
        canvas.overlay.undo.undo()
        assert canvas.overlay.document.events[0].position() is None

    def test_shift_constrains_to_axis(self, canvas: PreviewCanvas) -> None:
        event = canvas.overlay.document.events[0]
        x, y = body_point(canvas)
        press(canvas, x, y)
        before = geo.effective_anchor(
            event, canvas.overlay.document.style_for(event), geo.Size(1, 1), (1920, 1080)
        )
        # Сдвиг больше по X → вертикаль должна замереть.
        move(canvas, x + 200, y + 30, Qt.ShiftModifier | Qt.AltModifier)
        release(canvas, x + 200, y + 30)

        after = event.position()
        assert after is not None
        assert after[1] == pytest.approx(before[1], abs=1.0)
        assert after[0] != pytest.approx(before[0], abs=1.0)

    def test_snapping_pulls_to_centre(self, canvas: PreviewCanvas) -> None:
        """Без Alt магнит подтягивает к центру кадра."""
        event = canvas.overlay.document.events[0]
        vt = canvas.overlay.transform()
        # Целимся на 6 script-px левее центра — это 3 px виджета, внутри радиуса.
        target_wx, target_wy = vt.script_to_widget(954, 540)

        x, y = body_point(canvas)
        press(canvas, x, y)
        move(canvas, target_wx, target_wy)
        release(canvas, target_wx, target_wy)

        after = event.position()
        assert after is not None
        assert after[0] == pytest.approx(960, abs=1.0)

    def test_alt_disables_snapping(self, canvas: PreviewCanvas) -> None:
        event = canvas.overlay.document.events[0]
        vt = canvas.overlay.transform()
        target_wx, target_wy = vt.script_to_widget(954, 540)

        x, y = body_point(canvas)
        press(canvas, x, y)
        move(canvas, target_wx, target_wy, Qt.AltModifier)
        release(canvas, target_wx, target_wy)

        after = event.position()
        assert after is not None
        assert after[0] != pytest.approx(960, abs=0.5)

    def test_clamped_to_canvas(self, canvas: PreviewCanvas) -> None:
        event = canvas.overlay.document.events[0]
        x, y = body_point(canvas)
        press(canvas, x, y)
        move(canvas, -100_000, -100_000, Qt.AltModifier)
        release(canvas, -100_000, -100_000)

        after = event.position()
        assert after is not None
        assert after[0] >= -geo.OVERFLOW - 1
        assert after[1] >= -geo.OVERFLOW - 1


class TestKeyboardNudge:
    def test_arrow_moves_one_pixel(self, canvas: PreviewCanvas) -> None:
        from PySide6.QtGui import QKeyEvent

        event = canvas.overlay.document.events[0]
        x, y = body_point(canvas)
        press(canvas, x, y)
        before = geo.effective_anchor(
            event, canvas.overlay.document.style_for(event), geo.Size(1, 1), (1920, 1080)
        )

        canvas.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
        after = event.position()
        assert after is not None
        assert after[0] == pytest.approx(round(before[0] + 1))


class TestClearPosition:
    def test_returns_to_style_position(self, canvas: PreviewCanvas) -> None:
        event = canvas.overlay.document.events[0]
        x, y = body_point(canvas)
        press(canvas, x, y)
        move(canvas, x - 100, y - 100, Qt.AltModifier)
        release(canvas, x - 100, y - 100)
        assert event.position() is not None

        canvas.clear_position()
        assert event.position() is None
        assert event.text == "Перетащи меня"


def test_paint_does_not_crash(canvas: PreviewCanvas) -> None:
    """Отрисовка в offscreen — грубая, но действенная проверка на исключения."""
    from PySide6.QtGui import QImage

    canvas.set_selected(canvas.overlay.document.events[0].eid)
    image = QImage(canvas.size(), QImage.Format_ARGB32)
    image.fill(0)
    canvas.render(image)  # перегрузка с QPaintDevice, без targetOffset
    assert not image.isNull()
    # Хоть один непрозрачный пиксель — значит, что-то действительно нарисовалось.
    assert any(
        image.pixelColor(x, y).alpha() > 0
        for x in range(0, image.width(), 40)
        for y in range(0, image.height(), 40)
    )
