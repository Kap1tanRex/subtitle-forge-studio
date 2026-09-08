"""Иконки транспорта, нарисованные кодом.

Не эмодзи и не файлы. Эмодзи (``⏮``, ``▶``) выглядят по-разному в разных
шрифтах, а без шрифта с этими символами превращаются в пустые прямоугольники —
что и происходило в собранном виде. Файлы пришлось бы класть рядом с
бинарником и искать во время работы.

Рисование даёт и то, чего иначе не получить даром: иконка строится в момент
запроса, поэтому берёт цвет темы и нужный размер без отдельных наборов.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

__all__ = ["ICON_NAMES", "make_icon"]

ICON_NAMES = (
    "play", "pause", "start", "end", "prev_frame", "next_frame", "loop",
    "volume", "mute",
)


def make_icon(name: str, color: str, size: int = 18) -> QIcon:
    """Иконка ``name`` цветом ``color``."""
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
