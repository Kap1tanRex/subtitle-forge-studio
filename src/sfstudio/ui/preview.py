"""Холст превью без видео: шахматный фон плюс оверлей субтитров.

Режим «только субтитры» из §9.5 спецификации — когда открыт файл субтитров,
но не открыто видео. Вся логика отрисовки и мыши живёт в
:class:`sfstudio.ui.overlay.OverlayController`; здесь только подложка и
проброс событий, чтобы поведение совпадало с режимом поверх видео до мелочей.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.overlay import OverlayController
from sfstudio.ui.theme import DARK, Palette

__all__ = ["PreviewCanvas"]


class PreviewCanvas(QWidget):
    """Шахматный фон и оверлей субтитров поверх него."""

    selection_changed = Signal(int)
    document_edited = Signal()
    status_message = Signal(str)
    #: (eid, глобальная точка) — запрос панели оформления.
    context_requested = Signal(int, object)

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        palette: Palette = DARK,
        parent: QWidget | None = None,
        controller: OverlayController | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        if controller is None:
            self.overlay = OverlayController(doc, undo, host=self, palette=palette)
        else:
            # Общий контроллер с видеовиджетом: состояние не дублируется.
            self.overlay = controller
            controller.attach_host(self)

        # Минимум по видеовиджету, а не вдвое больше: центральный виджет
        # не даёт окну сжаться уже своего минимума, и лишние пиксели
        # здесь отнимаются у списка реплик справа.
        self.setMinimumSize(160, 90)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor)

    # -- контракт OverlayHost ---------------------------------------------------- #


    def set_palette(self, palette: Palette) -> None:
        """Меняет тему на лету — см. :meth:`TimelineWidget.set_palette`."""
        self._palette = palette
        self.update()

    def request_update(self) -> None:
        self.update()

    def set_cursor_shape(self, shape: Qt.CursorShape) -> None:
        self.setCursor(shape)

    def notify_selection(self, eid: int) -> None:
        self.selection_changed.emit(eid)

    def notify_edited(self) -> None:
        self.document_edited.emit()

    def notify_status(self, text: str) -> None:
        self.status_message.emit(text)

    def overlay_size(self) -> tuple[int, int]:
        return (self.width(), self.height())

    # -- удобные прокси ------------------------------------------------------------ #

    def set_document(self, doc: SubtitleDocument, undo: UndoStack) -> None:
        self.overlay.set_document(doc, undo)

    def set_time(self, ms: int) -> None:
        self.overlay.set_time(ms)

    def set_selected(self, eid: int | None) -> None:
        self.overlay.set_selected(eid)

    def clear_position(self) -> None:
        self.overlay.clear_position()

    def toggle_guides(self, on: bool) -> None:
        self.overlay.show_guides = on
        self.update()

    def toggle_safe_area(self, on: bool) -> None:
        self.overlay.show_safe_area = on
        self.update()

    @property
    def time_ms(self) -> int:
        return self.overlay.time_ms

    @property
    def backend(self) -> str:
        return self.overlay.backend

    # -- отрисовка --------------------------------------------------------------------- #

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._paint_background(painter)
        self.overlay.paint(painter)
        painter.end()

    def _paint_background(self, painter: QPainter) -> None:
        """Шахматка вместо кадра: сразу видно, что видео не открыто."""
        painter.fillRect(self.rect(), QColor(self._palette.bg_sunken))
        rect = self.overlay.transform().video_rect
        area = QRectF(rect.x, rect.y, rect.w, rect.h)

        cell = 24
        painter.save()
        painter.setClipRect(area)
        painter.fillRect(area, QColor(self._palette.canvas_a))
        dark = QColor(self._palette.canvas_b)
        y = area.top()
        row = 0
        while y < area.bottom():
            x = area.left() + (cell if row % 2 else 0)
            while x < area.right():
                painter.fillRect(QRectF(x, y, cell, cell), dark)
                x += cell * 2
            y += cell
            row += 1
        painter.restore()

        painter.setPen(QPen(QColor(self._palette.border), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(area)

    # -- ввод ------------------------------------------------------------------------------ #

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        self.overlay.on_press(pos.x(), pos.y(), event.button())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        self.overlay.on_move(pos.x(), pos.y(), event.modifiers())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.overlay.on_release(event.button())

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """Правый щелчок по реплике — панель быстрого оформления.

        Само меню собирает окно: виджет кадра знает только, по чему щёлкнули.
        """
        pos = event.pos()
        eid = self.overlay.eid_at(float(pos.x()), float(pos.y()))
        if eid is None:
            return
        self.overlay.set_selected(eid)
        self.selection_changed.emit(eid)
        self.context_requested.emit(eid, event.globalPos())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self.overlay.on_key(event.key(), event.modifiers()):
            super().keyPressEvent(event)
