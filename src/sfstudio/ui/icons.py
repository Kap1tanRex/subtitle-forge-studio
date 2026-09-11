"""Иконки транспорта, нарисованные кодом.

Не эмодзи и не файлы. Эмодзи (``⏮``, ``▶``) выглядят по-разному в разных
шрифтах, а без шрифта с этими символами превращаются в пустые прямоугольники —
что и происходило в собранном виде. Файлы пришлось бы класть рядом с
бинарником и искать во время работы.

Рисование даёт и то, чего иначе не получить даром: иконка строится в момент
запроса, поэтому берёт цвет темы и нужный размер без отдельных наборов.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

__all__ = ["ICON_NAMES", "make_icon", "paint_icon"]

ICON_NAMES = (
    "play", "pause", "start", "end", "prev_frame", "next_frame", "loop",
    "volume", "mute",
    "event_add", "event_duplicate", "event_delete", "track_add",
    "eye_open", "eye_closed", "locked", "unlocked", "warning",
)


@lru_cache(maxsize=256)
def make_icon(name: str, color: str, size: int = 18) -> QIcon:
    """Иконка ``name`` цветом ``color``.

    Результат кэшируется: значки рисуются пиксель за пикселем, а таймлайн
    зовёт их из ``paintEvent`` — по одному на каждую дорожку, при каждом
    движении мыши. Набор сочетаний конечен (имя × цвет темы × размер),
    поэтому кэш не растёт.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    _draw(painter, name, size, QColor(color))
    painter.end()

    return QIcon(pixmap)


def _triangle(x: float, y: float, w: float, h: float, *, pointing_right: bool) -> QPainterPath:
    path = QPainterPath()
    if pointing_right:
        path.moveTo(x, y)
        path.lineTo(x + w, y + h / 2)
        path.lineTo(x, y + h)
    else:
        path.moveTo(x + w, y)
        path.lineTo(x, y + h / 2)
        path.lineTo(x + w, y + h)
    path.closeSubpath()
    return path


def _fill(painter: QPainter, color: QColor) -> None:
    """Режим сплошной заливки без контура."""
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)


def _draw(painter: QPainter, name: str, size: int, color: QColor) -> None:
    unit = size / 18.0          # все размеры подобраны для 18 px
    pad = 3.0 * unit
    inner = size - pad * 2

    _fill(painter, color)

    if name == "play":
        painter.drawPath(_triangle(pad + unit, pad, inner - unit, inner,
                                   pointing_right=True))

    elif name == "pause":
        bar = inner / 3.2
        painter.drawRect(QRectF(pad + unit * 0.5, pad, bar, inner))
        painter.drawRect(QRectF(size - pad - bar - unit * 0.5, pad, bar, inner))

    elif name in ("start", "end"):
        bar = 2.0 * unit
        # Планка у края и треугольник, упирающийся в неё.
        if name == "end":
            painter.drawRect(QRectF(size - pad - bar, pad, bar, inner))
            painter.drawPath(_triangle(pad, pad, inner - bar - unit, inner,
                                       pointing_right=True))
        else:
            painter.drawRect(QRectF(pad, pad, bar, inner))
            painter.drawPath(_triangle(pad + bar + unit, pad, inner - bar - unit, inner,
                                       pointing_right=False))

    elif name in ("prev_frame", "next_frame"):
        bar = 1.8 * unit
        if name == "next_frame":
            painter.drawPath(_triangle(pad, pad + unit, inner - bar - unit, inner - unit * 2,
                                       pointing_right=True))
            painter.drawRect(QRectF(size - pad - bar, pad + unit, bar, inner - unit * 2))
        else:
            painter.drawRect(QRectF(pad, pad + unit, bar, inner - unit * 2))
            painter.drawPath(_triangle(pad + bar + unit, pad + unit,
                                       inner - bar - unit, inner - unit * 2,
                                       pointing_right=False))

    elif name == "loop":
        _draw_loop(painter, color, unit, pad, inner)

    elif name in ("volume", "mute"):
        _draw_speaker(painter, color, unit, pad, inner, muted=(name == "mute"))

    elif name == "event_add":
        _draw_event_add(painter, color, unit, pad, inner)

    elif name == "event_duplicate":
        _draw_event_duplicate(painter, color, unit, pad, inner)

    elif name == "event_delete":
        _draw_event_delete(painter, color, unit, pad, inner)

    elif name == "track_add":
        _draw_track_add(painter, color, unit, pad, inner)

    elif name in ("eye_open", "eye_closed"):
        _draw_eye(painter, color, unit, pad, inner, open_=(name == "eye_open"))

    elif name in ("locked", "unlocked"):
        _draw_lock(painter, color, unit, pad, inner, closed=(name == "locked"))

    elif name == "warning":
        _draw_warning(painter, color, unit, pad, inner)


def _draw_loop(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float
) -> None:
    """Разомкнутое кольцо со стрелкой.

    Рисуется штрихом, а не заливкой с вычитанием внутреннего контура: на 18 px
    тонкое кольцо после ``subtracted()`` вырождалось в невнятную точку —
    видно на глаз, тестом такое не поймать.
    """
    pen = QPen(color)
    pen.setWidthF(max(1.6, 2.0 * unit))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    ring = QRectF(pad, pad + unit * 1.5, inner, inner - unit * 3)
    # Разрыв в правом верхнем секторе — там встаёт стрелка.
    painter.drawArc(ring, 40 * 16, 290 * 16)

    _fill(painter, color)
    arrow = QPainterPath()
    tip = QPointF(ring.right() - unit * 0.5, ring.top() + ring.height() * 0.18)
    wing = 2.6 * unit
    arrow.moveTo(tip.x() + wing * 0.7, tip.y() + wing * 0.2)
    arrow.lineTo(tip.x() - wing * 0.5, tip.y() - wing * 0.8)
    arrow.lineTo(tip.x() - wing * 0.8, tip.y() + wing * 0.9)
    arrow.closeSubpath()
    painter.drawPath(arrow)


def _draw_speaker(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float,
    *, muted: bool,
) -> None:
    """Динамик с волнами либо перечёркнутый."""
    _fill(painter, color)
    body = QPainterPath()
    left = pad
    mid_y = pad + inner / 2
    box_h = inner * 0.42
    # Прямоугольная часть и раструб — один контур, иначе на стыке видна щель.
    body.moveTo(left, mid_y - box_h / 2)
    body.lineTo(left + inner * 0.28, mid_y - box_h / 2)
    body.lineTo(left + inner * 0.55, pad)
    body.lineTo(left + inner * 0.55, pad + inner)
    body.lineTo(left + inner * 0.28, mid_y + box_h / 2)
    body.lineTo(left, mid_y + box_h / 2)
    body.closeSubpath()
    painter.drawPath(body)

    pen = QPen(color)
    pen.setWidthF(max(1.3, 1.6 * unit))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    if muted:
        cross = left + inner * 0.68
        size = inner * 0.26
        painter.drawLine(QPointF(cross, mid_y - size), QPointF(cross + size * 2, mid_y + size))
        painter.drawLine(QPointF(cross, mid_y + size), QPointF(cross + size * 2, mid_y - size))
    else:
        for radius in (inner * 0.30, inner * 0.48):
            arc = QRectF(
                left + inner * 0.45 - radius / 2, mid_y - radius,
                radius * 1.6, radius * 2,
            )
            # Дуги справа от раструба: чем дальше, тем шире — так читается
            # «звук идёт наружу», а не «две скобки».
            painter.drawArc(arc, -55 * 16, 110 * 16)


def _plus(painter: QPainter, color: QColor, cx: float, cy: float, arm: float,
          thickness: float) -> None:
    """Плюс из двух планок. Общий для всех кнопок «добавить»."""
    _fill(painter, color)
    painter.drawRect(QRectF(cx - arm, cy - thickness / 2, arm * 2, thickness))
    painter.drawRect(QRectF(cx - thickness / 2, cy - arm, thickness, arm * 2))


def _draw_event_add(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float
) -> None:
    """Реплика и плюс рядом с ней.

    Плюс стоит сбоку, а не поверх прямоугольника: наложенный, он на 18 px
    сливался с углом и вся фигура читалась как лупа.
    """
    pen = QPen(color)
    pen.setWidthF(max(1.3, 1.7 * unit))
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    box = QRectF(pad, pad + inner * 0.24, inner * 0.52, inner * 0.52)
    painter.drawRect(box)

    _plus(painter, color, pad + inner * 0.82, box.center().y(),
          2.6 * unit, max(1.5, 1.9 * unit))


def _draw_event_duplicate(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float
) -> None:
    """Две реплики внахлёст — задняя контуром, передняя залитая."""
    pen = QPen(color)
    pen.setWidthF(max(1.3, 1.6 * unit))
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    side = inner * 0.62
    painter.drawRect(QRectF(pad, pad, side, side))

    _fill(painter, color)
    painter.drawRect(QRectF(pad + inner * 0.34, pad + inner * 0.34, side, side))


def _draw_event_delete(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float
) -> None:
    """Корзина: крышка, ручка и бак с рёбрами."""
    _fill(painter, color)
    lid_y = pad + inner * 0.18
    painter.drawRect(QRectF(pad, lid_y, inner, 1.6 * unit))

    pen = QPen(color)
    pen.setWidthF(max(1.2, 1.5 * unit))
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    handle = inner * 0.26
    painter.drawLine(
        QPointF(pad + inner / 2 - handle, lid_y - unit * 1.2),
        QPointF(pad + inner / 2 + handle, lid_y - unit * 1.2),
    )

    body = QRectF(pad + inner * 0.13, lid_y + 2.2 * unit,
                  inner * 0.74, inner * 0.62)
    painter.drawRect(body)
    for share in (0.36, 0.64):
        x = body.left() + body.width() * share
        painter.drawLine(
            QPointF(x, body.top() + unit),
            QPointF(x, body.bottom() - unit),
        )


def _draw_track_add(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float
) -> None:
    """Две дорожки и плюс под ними.

    Плюс внутри третьей, пустой дорожки выглядел двумя точками: на такой
    высоте ему просто негде развернуться.
    """
    _fill(painter, color)
    height = inner * 0.19
    for index in (0, 1):
        painter.drawRect(QRectF(pad, pad + index * inner * 0.29, inner, height))

    _plus(painter, color, pad + inner / 2, pad + inner * 0.8,
          3.0 * unit, max(1.5, 1.9 * unit))


def _draw_eye(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float,
    *, open_: bool,
) -> None:
    """Глаз: видимость дорожки. Скрытая — тот же глаз, перечёркнутый.

    Перечёркнутый, а не «закрытый»: опущенное веко на 18 px превращается в
    невнятную загогулину, а косая черта поверх узнаётся сразу и означает
    ровно «этого не видно».
    """
    pen = QPen(color)
    pen.setWidthF(max(1.3, 1.6 * unit))
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    mid_y = pad + inner / 2
    lid = inner * 0.32

    # Миндалевидный контур из двух встречных изгибов: овал с обводкой на этом
    # размере читается кольцом, а не глазом.
    shape = QPainterPath()
    shape.moveTo(pad, mid_y)
    shape.quadTo(pad + inner / 2, mid_y - lid * 2, pad + inner, mid_y)
    shape.quadTo(pad + inner / 2, mid_y + lid * 2, pad, mid_y)
    painter.drawPath(shape)

    _fill(painter, color)
    radius = inner * 0.16
    painter.drawEllipse(QPointF(pad + inner / 2, mid_y), radius, radius)

    if open_:
        return

    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(
        QPointF(pad + inner * 0.08, pad + inner * 0.92),
        QPointF(pad + inner * 0.92, pad + inner * 0.08),
    )


def _draw_lock(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float,
    *, closed: bool,
) -> None:
    """Замок: дужка сверху, корпус снизу. У открытого дужка отведена вбок."""
    body_h = inner * 0.46
    body = QRectF(pad + inner * 0.14, pad + inner - body_h, inner * 0.72, body_h)

    pen = QPen(color)
    pen.setWidthF(max(1.3, 1.7 * unit))
    pen.setCapStyle(Qt.FlatCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    shackle_w = inner * 0.44
    shackle_x = body.center().x() - shackle_w / 2 + (0 if closed else inner * 0.2)
    shackle = QRectF(shackle_x, pad + unit * 0.5, shackle_w, inner * 0.5)
    painter.drawArc(shackle, 0, 180 * 16)
    # Ножки дужки. У открытого замка правая короче — она «вынута».
    painter.drawLine(
        QPointF(shackle.left(), shackle.center().y()),
        QPointF(shackle.left(), body.top()),
    )
    painter.drawLine(
        QPointF(shackle.right(), shackle.center().y()),
        QPointF(shackle.right(), body.top() - (0 if closed else inner * 0.22)),
    )

    _fill(painter, color)
    painter.drawRoundedRect(body, 1.5 * unit, 1.5 * unit)


def _draw_warning(
    painter: QPainter, color: QColor, unit: float, pad: float, inner: float
) -> None:
    """Треугольник с восклицательным знаком."""
    triangle = QPainterPath()
    triangle.moveTo(pad + inner / 2, pad)
    triangle.lineTo(pad + inner, pad + inner)
    triangle.lineTo(pad, pad + inner)
    triangle.closeSubpath()

    pen = QPen(color)
    pen.setWidthF(max(1.3, 1.6 * unit))
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(triangle)

    _fill(painter, color)
    stem = max(1.2, 1.5 * unit)
    painter.drawRect(QRectF(
        pad + inner / 2 - stem / 2, pad + inner * 0.38, stem, inner * 0.32,
    ))
    painter.drawRect(QRectF(
        pad + inner / 2 - stem / 2, pad + inner * 0.78, stem, stem,
    ))


def paint_icon(painter: QPainter, name: str, color: str, rect: QRectF | QRect) -> None:
    """Рисует значок в прямоугольнике — замена ``drawText`` с эмодзи.

    Эмодзи в интерфейсе зависят от шрифта: на одной машине это картинка, на
    другой — пустой прямоугольник, и проверить это по своей машине нельзя.
    """
    target = rect.toRect() if isinstance(rect, QRectF) else rect
    side = max(8, min(target.width(), target.height()))
    make_icon(name, color, side).paint(painter, target, Qt.AlignCenter)
