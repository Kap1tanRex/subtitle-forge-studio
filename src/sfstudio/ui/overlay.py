"""Оверлей субтитров: отрисовка и взаимодействие, без привязки к виджету.

Класс намеренно **не наследует** ``QWidget``. Причина архитектурная: оверлей
нужен в двух местах с разной подложкой — поверх шахматного фона (когда видео
нет) и поверх кадра mpv внутри ``QOpenGLWidget``. Второй случай не позволяет
положить сверху обычный виджет: перекрытый ``QOpenGLWidget`` в Qt 6 перестаёт
получать отрисовку, и видео оказывается чёрным. Единственный надёжный способ
смешать GL и ``QPainter`` — рисовать painter'ом **внутри** ``paintGL``.

Поэтому логика живёт здесь и вызывается обоими хостами:

* :class:`sfstudio.ui.preview.PreviewCanvas` — обычный виджет, режим без видео;
* :class:`sfstudio.ui.video_widget.VideoWidget` — GL-виджет, режим с видео.

Хост обязан предоставить :class:`OverlayHost`: обновление, курсор и оповещения.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

from sfstudio.core import tags as tagmod
from sfstudio.core.commands import ClearPosition, SetOverrideTags, SetPosition
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.effective import effective_style
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.undo import UndoStack
from sfstudio.render import geometry as geo
from sfstudio.render.geometry import Handle, Rect, Size, ViewTransform
from sfstudio.ui.theme import DARK, Palette

__all__ = ["OverlayController", "OverlayHost"]

try:
    from sfstudio.render.renderer import LibassMeasurer, LibassRenderer

    _LIBASS = True
except OSError:  # pragma: no cover — зависит от машины
    LibassMeasurer = LibassRenderer = None  # type: ignore[assignment]
    _LIBASS = False


class OverlayHost(Protocol):
    """Что оверлей ожидает от виджета-хоста."""

    def request_update(self) -> None: ...
    def set_cursor_shape(self, shape: Qt.CursorShape) -> None: ...
    def notify_selection(self, eid: int) -> None: ...
    def notify_edited(self) -> None: ...
    def notify_status(self, text: str) -> None: ...
    def overlay_size(self) -> tuple[int, int]: ...


#: Ручки, за которые меняют размер. Поворот и точка привязки сюда не входят:
#: у них своё поведение.
_RESIZE_HANDLES = frozenset({
    Handle.NW, Handle.N, Handle.NE, Handle.E,
    Handle.SE, Handle.S, Handle.SW, Handle.W,
})

_RESIZE_CURSORS = {
    Handle.NW: Qt.SizeFDiagCursor,
    Handle.SE: Qt.SizeFDiagCursor,
    Handle.NE: Qt.SizeBDiagCursor,
    Handle.SW: Qt.SizeBDiagCursor,
    Handle.N: Qt.SizeVerCursor,
    Handle.S: Qt.SizeVerCursor,
    Handle.E: Qt.SizeHorCursor,
    Handle.W: Qt.SizeHorCursor,
}

#: Пределы масштаба. Ниже нижнего текст исчезает, выше верхнего вылезает за
#: кадр целиком — и вернуть его мышью уже не выйдет.
_MIN_SCALE = 5.0
_MAX_SCALE = 800.0

#: Минимальное расстояние от привязки до ручки, при котором ещё можно делить.
_MIN_RESIZE_ARM = 4.0


def _ratio(current: float, start: float) -> float:
    """Во сколько раз изменилось плечо. Единица, если считать не от чего."""
    if abs(start) < _MIN_RESIZE_ARM:
        return 1.0
    return current / start


class OverlayController:
    """Рендер субтитров и вся работа мышью поверх кадра."""

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        host: OverlayHost,
        palette: Palette = DARK,
    ) -> None:
        self._doc = doc
        self._undo = undo
        self._host = host
        self._palette = palette

        self._measurer: object | None = None
        self._renderer: object | None = None
        self._backend = "none"
        self._init_backend()

        self._time_ms = 0
        self._selected: int | None = None
        self.show_guides = True
        self.show_safe_area = False
        self.video_mode = False

        self._dragging = False
        self._drag_eid: int | None = None
        self._grab = (0.0, 0.0)
        self._drag_origin_text: str | None = None
        self._active_guides: list[geo.Guide] = []
        #: Жест изменения размера: за какую ручку тянут и от чего считать.
        self._resize_handle: Handle | None = None
        self._resize_from: tuple[float, float] = (0.0, 0.0)
        self._resize_scale: tuple[float, float] = (100.0, 100.0)

    # -- бэкенд ------------------------------------------------------------------ #


    def set_palette(self, palette: Palette) -> None:
        """Меняет тему на лету — см. :meth:`TimelineWidget.set_palette`."""
        self._palette = palette
        self.request_update()

    def _init_backend(self) -> None:
        if not _LIBASS:
            self._backend = "none"
            return
        try:
            self._measurer = LibassMeasurer(self._doc)
            self._renderer = LibassRenderer()
            self._backend = "libass"
        except (OSError, RuntimeError):
            self._measurer = None
            self._renderer = None
            self._backend = "none"

    def attach_host(self, host: OverlayHost) -> None:
        """Переключает хост.

        Контроллер один на оба режима (с видео и без), а виджет-хост меняется:
        так выделение, время и состояние жеста не разъезжаются при переключении.
        """
        self._host = host

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def backend_version(self) -> str:
        """Версия отрисовщика — для окна «О программе».

        Спрашивается у уже созданного рендерера: заводить ради строки ещё
        один контекст libass значило бы оставлять его после каждого открытия
        окна.
        """
        renderer = self._renderer
        if renderer is None:
            return ""
        try:
            return str(renderer.version)
        except Exception:
            return ""

    @property
    def measurer(self) -> object | None:
        return self._measurer

    # -- состояние ---------------------------------------------------------------- #

    @property
    def document(self) -> SubtitleDocument:
        return self._doc

    @property
    def undo(self) -> UndoStack:
        return self._undo

    @property
    def time_ms(self) -> int:
        return self._time_ms

    @property
    def selected(self) -> int | None:
        return self._selected

    def set_document(self, doc: SubtitleDocument, undo: UndoStack) -> None:
        self._doc = doc
        self._undo = undo
        self._selected = None
        if self._measurer is not None:
            self._measurer.set_document(doc)  # type: ignore[union-attr]
        if self._renderer is not None:
            self._renderer.invalidate()  # type: ignore[union-attr]
        self._host.request_update()

    def set_time(self, ms: int) -> None:
        if ms != self._time_ms:
            self._time_ms = ms
            self._host.request_update()

    def set_selected(self, eid: int | None) -> None:
        if eid != self._selected:
            self._selected = eid
            self._host.request_update()

    def transform(self) -> ViewTransform:
        width, height = self._host.overlay_size()
        return ViewTransform.fit(width, height, self._doc.script_info.play_res)

    # -- отрисовка ------------------------------------------------------------------ #

    def paint(self, painter: QPainter) -> None:
        """Рисует субтитры и манипуляторы. Фон рисует хост."""
        vt = self.transform()
        if vt.video_rect.w <= 0:
            return

        if self.show_safe_area:
            self._paint_safe_area(painter, vt)

        self._paint_subtitles(painter, vt)

        if self._dragging and self._active_guides:
            self._paint_guides(painter, vt)

        if self._selected is not None:
            active = self._doc.active_at(self._time_ms)
            target = next((e for e in active if e.eid == self._selected), None)
            if target is not None:
                self._paint_handles(painter, vt, target)

        self._paint_hud(painter)

    def _paint_subtitles(self, painter: QPainter, vt: ViewTransform) -> None:
        """Настоящие битмапы libass, наложенные в порядке списка.

        Каждый слой — 8-битная альфа-маска одного цвета: собираем из неё
        ``QImage`` формата ``Alpha8``, красим через ``SourceIn`` и накладываем.
        Порядок списка (тень, обводка, глиф) менять нельзя.
        """
        if self._renderer is None:
            return
        rect = vt.video_rect
        width, height = int(rect.w), int(rect.h)
        if width <= 0 or height <= 0:
            return

        self._renderer.set_frame_size(width, height, self._doc.script_info.play_res)
        try:
            layers = self._renderer.render(self._doc, self._time_ms)
        except Exception:
            return  # сбой рендера не должен ронять окно

        painter.save()
        painter.setClipRect(_qrect(rect))
        for layer in layers:
            if layer.w <= 0 or layer.h <= 0:
                continue
            r, g, b, a = layer.rgba
            if a == 0:
                continue
            mask = QImage(layer.data, layer.w, layer.h, layer.stride, QImage.Format_Alpha8)
            tinted = QImage(layer.w, layer.h, QImage.Format_ARGB32_Premultiplied)
            tinted.fill(0)
            inner = QPainter(tinted)
            inner.drawImage(0, 0, mask)
            inner.setCompositionMode(QPainter.CompositionMode_SourceIn)
            inner.fillRect(tinted.rect(), QColor(r, g, b, a))
            inner.end()
            painter.drawImage(QPointF(rect.x + layer.x, rect.y + layer.y), tinted)
        painter.restore()

    def _paint_safe_area(self, painter: QPainter, vt: ViewTransform) -> None:
        rect = _qrect(vt.video_rect)
        painter.setPen(QPen(QColor(self._palette.text_muted), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        for fraction in (0.90, 0.93):
            dx = rect.width() * (1 - fraction) / 2
            dy = rect.height() * (1 - fraction) / 2
            painter.drawRect(rect.adjusted(dx, dy, -dx, -dy))

    def _paint_handles(
        self, painter: QPainter, vt: ViewTransform, event: SubtitleEvent
    ) -> None:
        rect = self._bbox(event)
        if rect is None:
            return
        widget_rect = _qrect(vt.rect_to_widget(rect))
        accent = QColor(self._palette.accent)

        painter.setPen(QPen(accent, 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(widget_rect)

        painter.setPen(QPen(accent, 1))
        painter.setBrush(QColor(self._palette.bg_base))
        for _handle, fx, fy in geo._HANDLE_POINTS:
            cx = widget_rect.left() + widget_rect.width() * fx
            cy = widget_rect.top() + widget_rect.height() * fy
            painter.drawRect(QRectF(cx - 3.5, cy - 3.5, 7, 7))

        style = self._doc.style_for(event)
        ax, ay = geo.effective_anchor(
            event, style, Size(rect.w, rect.h), self._doc.script_info.play_res
        )
        px, py = vt.script_to_widget(ax, ay)
        painter.setBrush(accent)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(px, py), 4, 4)

    def _paint_guides(self, painter: QPainter, vt: ViewTransform) -> None:
        painter.setPen(QPen(QColor(self._palette.guide), 1, Qt.DashLine))
        video = vt.video_rect
        for guide in self._active_guides:
            if guide.axis == "x":
                x, _ = vt.script_to_widget(guide.value, 0)
                painter.drawLine(QPointF(x, video.top), QPointF(x, video.bottom))
            else:
                _, y = vt.script_to_widget(0, guide.value)
                painter.drawLine(QPointF(video.left, y), QPointF(video.right, y))

    def _paint_hud(self, painter: QPainter) -> None:
        info = self._doc.script_info
        note = f"{info.play_res_x}×{info.play_res_y}"
        if info.play_res_inferred:
            note += "  (PlayRes не задан в файле)"
        note += f"   активных: {len(self._doc.active_at(self._time_ms))}"
        if self._backend != "libass":
            note += "   ·  libass недоступна"

        width, _ = self._host.overlay_size()
        font = QFont("Segoe UI")
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor(self._palette.text_muted))
        painter.drawText(QRectF(8, 6, width - 16, 18), Qt.AlignLeft, note)

    # -- геометрия -------------------------------------------------------------------- #

    def _bbox(self, event: SubtitleEvent) -> Rect | None:
        if self._measurer is None:
            return None
        return geo.event_bbox(event, self._doc, self._measurer)  # type: ignore[arg-type]

    def _hit(self, x: float, y: float) -> geo.HitResult | None:
        if self._measurer is None:
            return None
        return geo.hit_test(
            x, y, self._time_ms, self._doc, self._measurer,  # type: ignore[arg-type]
            self.transform(), selected=self._selected,
        )

    # -- мышь ---------------------------------------------------------------------------- #

    def eid_at(self, x: float, y: float) -> int | None:
        """Реплика под точкой кадра, если она там есть.

        Нужна хостам для контекстного меню: панель быстрого оформления —
        забота виджета, а контроллер только отвечает, по чему щёлкнули.
        """
        hit = self._hit(x, y)
        return hit.eid if hit is not None else None

    def on_press(self, x: float, y: float, button: Qt.MouseButton) -> None:
        if button != Qt.LeftButton:
            return
        hit = self._hit(x, y)
        if hit is None:
            self._selected = None
            self._host.notify_selection(-1)
            self._host.request_update()
            return

        self._selected = hit.eid
        self._host.notify_selection(hit.eid)
        if hit.handle is Handle.BODY:
            self._dragging = True
            self._drag_eid = hit.eid
            self._grab = (hit.grab_dx, hit.grab_dy)
            self._drag_origin_text = self._doc.by_eid(hit.eid).text
            self._host.set_cursor_shape(Qt.ClosedHandCursor)
        elif hit.handle in _RESIZE_HANDLES:
            self._begin_resize(hit.eid, hit.handle, x, y)
        self._host.request_update()

    def _begin_resize(self, eid: int, handle: Handle, x: float, y: float) -> None:
        """Начинает изменение размера за угол или сторону.

        Запоминаем исходное расстояние от точки привязки до курсора и текущий
        масштаб. Считать от привязки, а не от противоположного угла, —
        единственный честный способ: теги ``\fscx``/``\fscy`` растягивают
        текст именно от неё, и любая другая опорная точка означала бы, что
        ручка уезжает из-под курсора.
        """
        anchor = self._current_anchor(eid)
        if anchor is None:
            return
        event = self._doc.by_eid(eid)
        effective = effective_style(event, self._doc.style_for(event))

        script_x, script_y = self.transform().widget_to_script(x, y)
        dx = script_x - anchor[0]
        dy = script_y - anchor[1]
        # Слишком близко к привязке делить нельзя: коэффициент улетит в
        # бесконечность от дрожания руки на пару пикселей.
        if abs(dx) < _MIN_RESIZE_ARM and abs(dy) < _MIN_RESIZE_ARM:
            return

        self._dragging = True
        self._drag_eid = eid
        self._resize_handle = handle
        self._resize_from = (dx, dy)
        self._resize_scale = (effective.scale_x, effective.scale_y)
        self._drag_origin_text = event.text
        self._host.set_cursor_shape(_RESIZE_CURSORS.get(handle, Qt.SizeAllCursor))

    def on_move(self, x: float, y: float, modifiers: Qt.KeyboardModifier) -> None:
        if not self._dragging:
            hit = self._hit(x, y)
            if hit is None:
                self._host.set_cursor_shape(Qt.ArrowCursor)
            elif hit.handle in _RESIZE_HANDLES:
                # Курсор — единственный намёк на то, что за ручку можно
                # потянуть. Без него их принимают за украшение рамки.
                self._host.set_cursor_shape(
                    _RESIZE_CURSORS.get(hit.handle, Qt.SizeAllCursor)
                )
            else:
                self._host.set_cursor_shape(Qt.OpenHandCursor)
            return

        assert self._drag_eid is not None
        if self._resize_handle is not None:
            self._update_resize(x, y, modifiers)
            return

        vt = self.transform()
        sx, sy = vt.widget_to_script(x, y)
        # Вычитаем захват — иначе субтитр прыгнет центром под курсор.
        sx -= self._grab[0]
        sy -= self._grab[1]

        if modifiers & Qt.ShiftModifier:
            start = self._drag_start_anchor()
            if start is not None:
                if abs(sx - start[0]) > abs(sy - start[1]):
                    sy = start[1]
                else:
                    sx = start[0]

        self._active_guides = []
        if not (modifiers & Qt.AltModifier):
            event = self._doc.by_eid(self._drag_eid)
            guides = geo.build_guides(self._doc, self._doc.style_for(event))
            sx, sy, self._active_guides = geo.snap_to_guides(sx, sy, guides, vt)

        sx, sy = geo.clamp_to_canvas(sx, sy, self._doc.script_info.play_res)
        self._undo.run(SetPosition(self._drag_eid, sx, sy))
        self._host.notify_status(f"\\pos({round(sx)}, {round(sy)})")
        self._host.notify_edited()
        self._host.request_update()

    def on_release(self, button: Qt.MouseButton) -> None:
        if button == Qt.LeftButton and self._dragging:
            self._end_drag()

    def on_key(self, key: Qt.Key, modifiers: Qt.KeyboardModifier) -> bool:
        """``True``, если клавиша обработана."""
        if key == Qt.Key_Escape and self._dragging:
            self._cancel_drag()
            return True
        if self._selected is None:
            return False

        step = 10.0 if modifiers & Qt.ShiftModifier else 1.0
        deltas = {
            Qt.Key_Left: (-step, 0.0),
            Qt.Key_Right: (step, 0.0),
            Qt.Key_Up: (0.0, -step),
            Qt.Key_Down: (0.0, step),
        }
        delta = deltas.get(key)
        if delta is None:
            return False

        anchor = self._current_anchor(self._selected)
        if anchor is None:
            return False
        self._undo.run(SetPosition(self._selected, anchor[0] + delta[0], anchor[1] + delta[1]))
        self._host.notify_edited()
        self._host.request_update()
        return True

    def clear_position(self) -> None:
        if self._selected is None:
            return
        self._undo.run(ClearPosition(self._selected))
        self._host.notify_edited()
        self._host.request_update()

    # -- служебное -------------------------------------------------------------------------- #

    def _current_anchor(self, eid: int) -> tuple[float, float] | None:
        event = self._doc.get(eid)
        if event is None:
            return None
        rect = self._bbox(event)
        size = Size(rect.w, rect.h) if rect else Size(0, 0)
        return geo.effective_anchor(
            event, self._doc.style_for(event), size, self._doc.script_info.play_res
        )

    def _drag_start_anchor(self) -> tuple[float, float] | None:
        if self._drag_eid is None or self._drag_origin_text is None:
            return None
        tag = tagmod.parse_tags(self._drag_origin_text).get_tag("pos")
        if tag is not None:
            nums = tag.numbers()
            if len(nums) >= 2:
                return (nums[0], nums[1])
        return self._current_anchor(self._drag_eid)

    def _update_resize(self, x: float, y: float, modifiers: Qt.KeyboardModifier) -> None:
        """Пересчитывает масштаб под текущее положение курсора."""
        assert self._drag_eid is not None
        anchor = self._current_anchor(self._drag_eid)
        if anchor is None:
            return

        sx, sy = self.transform().widget_to_script(x, y)
        start_dx, start_dy = self._resize_from
        base_x, base_y = self._resize_scale
        handle = self._resize_handle

        factor_x = _ratio(sx - anchor[0], start_dx)
        factor_y = _ratio(sy - anchor[1], start_dy)

        horizontal = handle in (Handle.E, Handle.W, Handle.NE, Handle.NW,
                                Handle.SE, Handle.SW)
        vertical = handle in (Handle.N, Handle.S, Handle.NE, Handle.NW,
                              Handle.SE, Handle.SW)
        corner = horizontal and vertical

        # Углы тянут пропорционально: так текст не «плющит». Shift снимает
        # это ограничение — в графических редакторах он значит ровно это.
        if corner and not (modifiers & Qt.ShiftModifier):
            factor = (factor_x + factor_y) / 2
            factor_x = factor_y = factor

        scale_x = base_x * factor_x if horizontal else base_x
        scale_y = base_y * factor_y if vertical else base_y
        scale_x = max(_MIN_SCALE, min(scale_x, _MAX_SCALE))
        scale_y = max(_MIN_SCALE, min(scale_y, _MAX_SCALE))

        # Одной командой: масштаб — это пара тегов, и отмена по Escape должна
        # возвращать оба, а не оставлять текст растянутым по одной оси.
        self._undo.run(
            SetOverrideTags(
                self._drag_eid,
                {"fscx": round(scale_x), "fscy": round(scale_y)},
                label="Размер субтитра",
            )
        )
        self._host.notify_edited()
        self._host.notify_status(
            f"Размер: {scale_x:.0f} % × {scale_y:.0f} %"
        )
        self._host.request_update()

    def _end_drag(self) -> None:
        self._dragging = False
        self._drag_eid = None
        self._drag_origin_text = None
        self._resize_handle = None
        self._active_guides = []
        self._host.set_cursor_shape(Qt.OpenHandCursor)
        self._host.request_update()

    def _cancel_drag(self) -> None:
        if self._drag_eid is not None and self._drag_origin_text is not None:
            self._undo.undo()  # жест — одна команда благодаря coalescing
            self._host.notify_edited()
        self._end_drag()
        self._host.notify_status("Перетаскивание отменено")


def _qrect(rect: Rect) -> QRectF:
    return QRectF(rect.x, rect.y, rect.w, rect.h)
