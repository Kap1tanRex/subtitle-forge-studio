"""Геометрия оверлея: системы координат, bbox, хит-тест, магниты.

Модуль намеренно не зависит от Qt — вся математика WYSIWYG-редактирования
тестируется без окна и без libass.

Измерение размеров текста вынесено за интерфейс :class:`TextMeasurer`.
В прототипе его реализует ``QFontMetrics``; на этапе 3 он заменяется на libass
(способ A из спецификации, §13.2) — совпадение с тем, что видит зритель, там
будет идеальным. Остальной код при замене не меняется.

Системы координат:

* **script** — координаты ASS, размер ``PlayResX × PlayResY``;
* **widget** — логические пиксели виджета;
* видео вписано в виджет с сохранением пропорций (letterbox/pillarbox),
  прямоугольник видео — :attr:`ViewTransform.video_rect`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle

__all__ = [
    "Handle",
    "HitResult",
    "Rect",
    "Size",
    "TextMeasurer",
    "ViewTransform",
    "anchor_point_of",
    "effective_anchor",
    "event_bbox",
    "hit_test",
    "snap_to_guides",
]

#: Насколько за пределы кадра разрешено уводить субтитр (script-пиксели).
OVERFLOW = 200.0

#: Радиус срабатывания магнита, в пикселях виджета.
SNAP_RADIUS_PX = 8.0


@dataclass(frozen=True, slots=True)
class Size:
    w: float
    h: float


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.right and self.y <= py <= self.bottom

    def inflated(self, amount: float) -> Rect:
        return Rect(self.x - amount, self.y - amount, self.w + 2 * amount, self.h + 2 * amount)

    def translated(self, dx: float, dy: float) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)


class Handle(Enum):
    """За что именно ухватились мышью."""

    BODY = "body"
    NW = "nw"
    N = "n"
    NE = "ne"
    E = "e"
    SE = "se"
    S = "s"
    SW = "sw"
    W = "w"
    ROTATE = "rotate"
    ORIGIN = "origin"


@dataclass(frozen=True, slots=True)
class HitResult:
    eid: int
    handle: Handle
    #: Смещение точки клика от якоря события, в script-координатах. Нужно,
    #: чтобы при перетаскивании субтитр не «прыгал» к курсору.
    grab_dx: float = 0.0
    grab_dy: float = 0.0


class TextMeasurer(Protocol):
    """Источник размеров отрисованного текста в script-координатах."""

    def measure(self, event: SubtitleEvent, style: SubtitleStyle) -> Size:
        ...


# --------------------------------------------------------------------------- #
# Преобразование координат
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ViewTransform:
    """Связь script-координат с пикселями виджета."""

    video_rect: Rect
    play_res: tuple[int, int]

    @staticmethod
    def fit(
        widget_w: float,
        widget_h: float,
        play_res: tuple[int, int],
        *,
        dar: float | None = None,
        rotation: int = 0,
    ) -> ViewTransform:
        """Вписывает кадр в виджет с сохранением пропорций.

        ``rotation`` (0/90/180/270) учитывается обменом сторон **до** расчёта:
        вертикальное видео, снятое телефоном, иначе разъезжается с субтитрами.
        Сам поворот кадра делает mpv; сюда приходит уже display-ориентация.
        """
        rx, ry = play_res
        aspect = dar if dar is not None else (rx / ry if ry else 16 / 9)
        if rotation in (90, 270):
            aspect = 1.0 / aspect if aspect else 1.0

        if widget_w <= 0 or widget_h <= 0:
            return ViewTransform(Rect(0, 0, 0, 0), play_res)

        if widget_w / widget_h > aspect:
            h = widget_h  # ограничивает высота → полосы слева и справа
            w = h * aspect
        else:
            w = widget_w  # ограничивает ширина → полосы сверху и снизу
            h = w / aspect

        return ViewTransform(Rect((widget_w - w) / 2, (widget_h - h) / 2, w, h), play_res)

    @property
    def scale_x(self) -> float:
        rx = self.play_res[0]
        return self.video_rect.w / rx if rx else 1.0

    @property
    def scale_y(self) -> float:
        ry = self.play_res[1]
        return self.video_rect.h / ry if ry else 1.0

    def script_to_widget(self, sx: float, sy: float) -> tuple[float, float]:
        return (self.video_rect.x + sx * self.scale_x, self.video_rect.y + sy * self.scale_y)

    def widget_to_script(self, wx: float, wy: float) -> tuple[float, float]:
        kx, ky = self.scale_x, self.scale_y
        if kx == 0 or ky == 0:
            return (0.0, 0.0)
        return ((wx - self.video_rect.x) / kx, (wy - self.video_rect.y) / ky)

    def delta_to_script(self, dx: float, dy: float) -> tuple[float, float]:
        kx, ky = self.scale_x, self.scale_y
        return (dx / kx if kx else 0.0, dy / ky if ky else 0.0)

    def rect_to_widget(self, rect: Rect) -> Rect:
        x, y = self.script_to_widget(rect.x, rect.y)
        return Rect(x, y, rect.w * self.scale_x, rect.h * self.scale_y)


# --------------------------------------------------------------------------- #
# Позиционирование
# --------------------------------------------------------------------------- #


def anchor_point_of(rect: Rect, alignment: int) -> tuple[float, float]:
    """Точка привязки прямоугольника для заданного ``\\an`` (1..9)."""
    col = (alignment - 1) % 3  # 0=слева, 1=центр, 2=справа
    row = (alignment - 1) // 3  # 0=снизу, 1=посередине, 2=сверху
    x = rect.left + rect.w * (0.0, 0.5, 1.0)[col]
    y = (rect.bottom, rect.center_y, rect.top)[row]
    return (x, y)


def rect_from_anchor(ax: float, ay: float, size: Size, alignment: int) -> Rect:
    """Обратная операция: прямоугольник по точке привязки и размеру."""
    col = (alignment - 1) % 3
    row = (alignment - 1) // 3
    x = ax - size.w * (0.0, 0.5, 1.0)[col]
    y = ay - size.h * (1.0, 0.5, 0.0)[row]
    return Rect(x, y, size.w, size.h)


def effective_alignment(event: SubtitleEvent, style: SubtitleStyle) -> int:
    """``\\an`` события, иначе выравнивание стиля."""
    return event.alignment_override() or style.alignment


def effective_anchor(
    event: SubtitleEvent, style: SubtitleStyle, size: Size, play_res: tuple[int, int]
) -> tuple[float, float]:
    """Точка привязки события в script-координатах.

    При наличии ``\\pos`` — прямо из тега. Иначе вычисляется из выравнивания и
    полей: поля события перекрывают поля стиля, нулевое поле означает
    «наследовать» (соглашение формата ASS).
    """
    pos = event.position()
    if pos is not None:
        return pos

    rx, ry = play_res
    alignment = effective_alignment(event, style)
    col = (alignment - 1) % 3
    row = (alignment - 1) // 3

    ml = event.margin_l or style.margin_l
    mr = event.margin_r or style.margin_r
    mv = event.margin_v or style.margin_v

    if col == 0:
        x = ml
    elif col == 1:
        # Центр области между полями, а не центр кадра: при разных ml/mr
        # libass центрует именно так.
        x = ml + (rx - ml - mr) / 2
    else:
        x = rx - mr

    y = ry - mv if row == 0 else (ry / 2 if row == 1 else mv)

    # Точка привязки соответствует размеру: для \an5 это центр блока текста.
    _ = size
    return (float(x), float(y))


def event_bbox(
    event: SubtitleEvent,
    doc: SubtitleDocument,
    measurer: TextMeasurer,
) -> Rect | None:
    """Ограничивающий прямоугольник события в script-координатах.

    Если измеритель умеет отдавать готовый прямоугольник (``bbox``), берётся
    он: libass считает положение сама, с учётом переноса строк, полей,
    выравнивания и inline-тегов — точнее, чем восстанавливать его из якоря.
    Восстановление остаётся запасным путём для измерителей, которые знают
    только размер (например, приближённый на ``QFontMetrics``).
    """
    exact = getattr(measurer, "bbox", None)
    if exact is not None:
        rect = exact(event)
        if rect is not None and rect.w > 0 and rect.h > 0:
            return rect

    style = doc.style_for(event)
    size = measurer.measure(event, style)
    if size.w <= 0 or size.h <= 0:
        return None

    anchor = effective_anchor(event, style, size, doc.script_info.play_res)
    alignment = effective_alignment(event, style)
    return rect_from_anchor(anchor[0], anchor[1], size, alignment)


# --------------------------------------------------------------------------- #
# Хит-тест
# --------------------------------------------------------------------------- #

#: Углы и стороны в порядке обхода, вместе с их относительными координатами.
_HANDLE_POINTS: tuple[tuple[Handle, float, float], ...] = (
    (Handle.NW, 0.0, 0.0),
    (Handle.N, 0.5, 0.0),
    (Handle.NE, 1.0, 0.0),
    (Handle.E, 1.0, 0.5),
    (Handle.SE, 1.0, 1.0),
    (Handle.S, 0.5, 1.0),
    (Handle.SW, 0.0, 1.0),
    (Handle.W, 0.0, 0.5),
)

HANDLE_HIT_PX = 10.0
ROTATE_OFFSET_PX = 26.0


def hit_test(
    wx: float,
    wy: float,
    at_ms: int,
    doc: SubtitleDocument,
    measurer: TextMeasurer,
    transform: ViewTransform,
    *,
    selected: int | None = None,
) -> HitResult | None:
    """Что находится под курсором.

    Порядок проверки:

    1. Манипуляторы выделенного события — у них приоритет, иначе до угловой
       ручки, лежащей поверх соседнего субтитра, было бы не добраться.
    2. Активные события, сверху вниз: сначала больший ``layer``, при равенстве —
       позже идущий в документе. Это тот же порядок, в котором их рисует libass.
    """
    active = doc.active_at(at_ms)
    if not active:
        return None

    if selected is not None:
        target = next((e for e in active if e.eid == selected), None)
        if target is not None:
            rect = event_bbox(target, doc, measurer)
            if rect is not None:
                handle = _handle_at(wx, wy, transform.rect_to_widget(rect))
                if handle is not None:
                    return HitResult(target.eid, handle)

    for event in sorted(active, key=lambda e: (e.layer, e.read_order, e.eid), reverse=True):
        rect = event_bbox(event, doc, measurer)
        if rect is None:
            continue
        widget_rect = transform.rect_to_widget(rect)
        if widget_rect.contains(wx, wy):
            sx, sy = transform.widget_to_script(wx, wy)
            style = doc.style_for(event)
            size = Size(rect.w, rect.h)
            ax, ay = effective_anchor(event, style, size, doc.script_info.play_res)
            return HitResult(event.eid, Handle.BODY, grab_dx=sx - ax, grab_dy=sy - ay)

    return None


def _handle_at(wx: float, wy: float, rect: Rect) -> Handle | None:
    """Ручка под курсором, либо ``None``, если курсор в свободном теле рамки.

    Зона захвата ручки ограничена третью соответствующей стороны. Без этого
    ограничения у тонкой рамки — а строка субтитра в уменьшенном превью почти
    всегда тонкая — зоны верхней и нижней ручек смыкаются посередине, покрывают
    рамку целиком, и субтитр становится невозможно потянуть за тело: любой клик
    попадает в ручку изменения размера.
    """
    grab_x = min(HANDLE_HIT_PX, rect.w / 3) if rect.w > 0 else 0.0
    grab_y = min(HANDLE_HIT_PX, rect.h / 3) if rect.h > 0 else 0.0

    for handle, fx, fy in _HANDLE_POINTS:
        hx = rect.left + rect.w * fx
        hy = rect.top + rect.h * fy
        if abs(wx - hx) <= grab_x and abs(wy - hy) <= grab_y:
            return handle

    rx = rect.center_x
    ry = rect.top - ROTATE_OFFSET_PX
    if math.hypot(wx - rx, wy - ry) <= HANDLE_HIT_PX:
        return Handle.ROTATE

    return None


# --------------------------------------------------------------------------- #
# Магниты
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Guide:
    """Магнитная направляющая."""

    axis: str  # 'x' или 'y'
    value: float  # координата в script-пространстве
    kind: str  # 'center' | 'margin' | 'edge' — для подсветки


def build_guides(doc: SubtitleDocument, style: SubtitleStyle) -> list[Guide]:
    """Направляющие: центры кадра и границы полей стиля."""
    rx, ry = doc.script_info.play_res
    return [
        Guide("x", rx / 2, "center"),
        Guide("y", ry / 2, "center"),
        Guide("x", float(style.margin_l), "margin"),
        Guide("x", float(rx - style.margin_r), "margin"),
        Guide("y", float(ry - style.margin_v), "margin"),
        Guide("y", float(style.margin_v), "margin"),
    ]


def snap_to_guides(
    sx: float,
    sy: float,
    guides: list[Guide],
    transform: ViewTransform,
    *,
    radius_px: float = SNAP_RADIUS_PX,
) -> tuple[float, float, list[Guide]]:
    """Притягивает точку к ближайшим направляющим по каждой оси.

    Радиус задан в пикселях виджета и переводится в script-координаты по
    текущему масштабу — иначе на маленьком окне магнит хватал бы половину кадра.
    """
    rx = radius_px / transform.scale_x if transform.scale_x else 0.0
    ry = radius_px / transform.scale_y if transform.scale_y else 0.0

    best_x: tuple[float, Guide] | None = None
    best_y: tuple[float, Guide] | None = None

    for guide in guides:
        if guide.axis == "x":
            dist = abs(sx - guide.value)
            if dist <= rx and (best_x is None or dist < best_x[0]):
                best_x = (dist, guide)
        else:
            dist = abs(sy - guide.value)
            if dist <= ry and (best_y is None or dist < best_y[0]):
                best_y = (dist, guide)

    hit: list[Guide] = []
    if best_x is not None:
        sx = best_x[1].value
        hit.append(best_x[1])
    if best_y is not None:
        sy = best_y[1].value
        hit.append(best_y[1])
    return sx, sy, hit


def clamp_to_canvas(sx: float, sy: float, play_res: tuple[int, int]) -> tuple[float, float]:
    """Не даёт увести субтитр бесконечно далеко за кадр."""
    rx, ry = play_res
    return (
        max(-OVERFLOW, min(sx, rx + OVERFLOW)),
        max(-OVERFLOW, min(sy, ry + OVERFLOW)),
    )
