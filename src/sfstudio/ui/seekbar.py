"""Полоса воспроизведения: где мы сейчас и куда можно перемотать.

Свой виджет, а не ``QSlider``. Причины по порядку важности:

* ползунок работает в целых «шагах», а нам нужны миллисекунды на файле в
  несколько часов — это миллионы шагов, и стандартная отрисовка ручки с таким
  диапазоном начинает врать на округлениях;
* на полосе нужны **отметки реплик**: видно, где в ролике вообще есть
  субтитры, и перемотка становится осмысленной, а не тыком наугад;
* при наведении нужен таймкод под курсором — до нажатия, а не после.

Полоса ведущей роли не играет: она отражает время, которое ей задают, и просит
перемотку сигналом. Кто на самом деле перемотал — плеер, таймлайн или таблица —
её не касается, поэтому одна и та же полоса верна и когда видео открыто, и
когда работа идёт по одной звуковой дорожке.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from sfstudio.core.time import format_srt
from sfstudio.ui.theme import DARK, Palette

__all__ = ["SeekBar"]

BAR_H = 6.0
KNOB_R = 7.0
MARK_H = 3.0
#: Зазор между отметками и полосой. Без него отметки читаются как продолжение
#: самой полосы — проверено на глаз, вплотную они сливаются.
MARK_GAP = 4.0
#: Высота виджета: полоса, ручка и место под отметки реплик.
WIDGET_H = 30


class SeekBar(QWidget):
    """Полоса положения с отметками реплик."""

    #: Перемотка на указанное время (мс).
    seek_requested = Signal(int)
    #: Перетаскивание началось и закончилось — плееру полезно знать.
    scrub_started = Signal()
    scrub_finished = Signal()

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._position_ms = 0
        self._duration_ms = 0
        self._marks: tuple[tuple[int, int], ...] = ()
        self._dragging = False
        self._hover_x: float | None = None

        self.setMinimumHeight(WIDGET_H)
        self.setMaximumHeight(WIDGET_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    # -- состояние ---------------------------------------------------------------- #


    def set_palette(self, palette: Palette) -> None:
        """Меняет тему на лету — см. :meth:`TimelineWidget.set_palette`."""
        self._palette = palette
        self.update()

    def set_position(self, ms: int) -> None:
        """Показывает позицию. Во время перетаскивания игнорируется.

        Иначе получается рывок: пользователь тянет ручку, плеер досылает
        позицию с прошлого места, и ручка прыгает назад под пальцем.
        """
        if self._dragging:
            return
        ms = max(0, ms)
        if ms != self._position_ms:
            self._position_ms = ms
            self.update()

    def set_duration(self, ms: int) -> None:
        self._duration_ms = max(0, ms)
        self.update()

    def set_marks(self, marks) -> None:
        """Отметки реплик: последовательность пар ``(начало, конец)`` в мс."""
        self._marks = tuple(marks)
        self.update()

    @property
    def position_ms(self) -> int:
        return self._position_ms

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    # -- геометрия ----------------------------------------------------------------- #

    def _track_rect(self) -> QRectF:
        margin = KNOB_R + 1
        # Полоса смещена вниз: сверху остаётся место под отметки реплик.
        top = self.height() - BAR_H - KNOB_R + 2
        return QRectF(margin, top, max(1.0, self.width() - margin * 2), BAR_H)

    def _x_of(self, ms: int) -> float:
        track = self._track_rect()
        if self._duration_ms <= 0:
            return track.left()
        ratio = min(1.0, max(0.0, ms / self._duration_ms))
        return track.left() + ratio * track.width()

    def _ms_at(self, x: float) -> int:
        track = self._track_rect()
        if track.width() <= 0 or self._duration_ms <= 0:
            return 0
        ratio = (x - track.left()) / track.width()
        return int(min(1.0, max(0.0, ratio)) * self._duration_ms)

    # -- отрисовка ------------------------------------------------------------------ #

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        track = self._track_rect()
        radius = BAR_H / 2

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._palette.bg_sunken))
        painter.drawRoundedRect(track, radius, radius)

        self._paint_marks(painter, track)

        if self._duration_ms > 0:
            played = QRectF(track)
            played.setRight(self._x_of(self._position_ms))
            painter.setBrush(QColor(self._palette.accent))
            painter.drawRoundedRect(played, radius, radius)

        # Ручка. Рисуется всегда, в том числе при нулевой длительности:
        # пустая полоса без ручки читается как «сломано», а не «нет файла».
        knob_x = self._x_of(self._position_ms)
        knob_y = track.center().y()
        painter.setBrush(QColor(self._palette.text_primary))
        painter.setPen(QPen(QColor(self._palette.bg_base), 1))
        painter.drawEllipse(QPointF(knob_x, knob_y), KNOB_R, KNOB_R)

        if self._hover_x is not None and self._duration_ms > 0:
            self._paint_hover(painter, track)

        painter.end()

    def _paint_marks(self, painter: QPainter, track: QRectF) -> None:
        """Тонкие штрихи там, где есть реплики."""
        if not self._marks or self._duration_ms <= 0:
            return
        # Приглушённый серый, а не оттенок синего: синим залита проигранная
        # часть полосы, и вторым синим отметки сливались бы с ней.
        color = QColor(self._palette.text_muted)
        color.setAlpha(150)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        top = track.top() - MARK_GAP - MARK_H
        for start, end in self._marks:
            x0 = self._x_of(start)
            x1 = self._x_of(end)
            painter.drawRect(QRectF(x0, top, max(1.0, x1 - x0), MARK_H))

    def _paint_hover(self, painter: QPainter, track: QRectF) -> None:
        painter.setPen(QPen(QColor(self._palette.text_muted), 1))
        painter.drawLine(
            QPointF(self._hover_x, track.top() - MARK_GAP - MARK_H),
            QPointF(self._hover_x, track.bottom() + 2),
        )

    # -- ввод ------------------------------------------------------------------------ #

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or self._duration_ms <= 0:
            return
        self._dragging = True
        self.scrub_started.emit()
        self._apply(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        self._hover_x = x
        if self._dragging:
            self._apply(x)
        else:
            self.update()
            if self._duration_ms > 0:
                QToolTip.showText(event.globalPosition().toPoint(),
                                  format_srt(self._ms_at(x)), self)

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.scrub_finished.emit()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        self._hover_x = None
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Колесо — шаг на секунду: привычно по видеоплеерам."""
        if self._duration_ms <= 0:
            return
        ticks = event.angleDelta().y() / 120.0
        target = max(0, min(self._duration_ms, self._position_ms + int(ticks * 1000)))
        self._position_ms = target
        self.seek_requested.emit(target)
        self.update()

    def _apply(self, x: float) -> None:
        ms = self._ms_at(x)
        self._position_ms = ms
        self.seek_requested.emit(ms)
        self.update()
