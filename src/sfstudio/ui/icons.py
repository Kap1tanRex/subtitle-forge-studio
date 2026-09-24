"""Значки интерфейса: контурные SVG из макета, раскрашенные под тему.

Не эмодзи и не файлы. Эмодзи (``⏮``, ``▶``) выглядят по-разному в разных
шрифтах, а без шрифта с этими символами превращаются в пустые прямоугольники —
что и происходило в собранном виде. Файлы пришлось бы класть рядом с
бинарником и искать во время работы.

Значок хранится строкой SVG прямо здесь и рисуется в момент запроса, поэтому
берёт цвет темы и нужный размер без отдельных наборов. Все значки — на сетке
24×24 с линией 1,8, как в макете: так они одного веса рядом друг с другом.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRect, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

__all__ = ["ICON_NAMES", "make_icon", "paint_icon"]

_SEARCH = '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>'
_TRASH = ('<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/>'
          '<path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/><path d="M9 7V4h6v3"/>')
_EYE = ('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/>'
        '<circle cx="12" cy="12" r="3"/>')
_SPEAKER = '<path d="M11 5L6 9H3v6h3l5 4z"/>'
_LOCK_BODY = '<rect x="5" y="11" width="14" height="10" rx="2"/>'

#: Тело значка — всё, что внутри ``<svg>``. Контур по умолчанию; залитые
#: части помечены ``fill="currentColor"`` сами.
_SHAPES: dict[str, str] = {
    "play": '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor" stroke="none"/>',
    "pause": ('<rect x="6.5" y="5" width="4" height="14" rx="1" fill="currentColor"'
              ' stroke="none"/><rect x="13.5" y="5" width="4" height="14" rx="1"'
              ' fill="currentColor" stroke="none"/>'),
    "start": '<path d="M6 5v14"/><path d="M18 6l-9 6 9 6z"/>',
    "end": '<path d="M18 5v14"/><path d="M6 6l9 6-9 6z"/>',
    "prev_frame": '<path d="M7 6v12"/><path d="M17 6l-6 6 6 6"/>',
    "next_frame": '<path d="M17 6v12"/><path d="M7 6l6 6-6 6"/>',
    "loop": ('<path d="M17 2l3 3-3 3"/><path d="M4 11V9a4 4 0 0 1 4-4h12"/>'
             '<path d="M7 22l-3-3 3-3"/><path d="M20 13v2a4 4 0 0 1-4 4H4"/>'),
    "volume": _SPEAKER + '<path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
    "mute": _SPEAKER + '<path d="M16 9l5 6M21 9l-5 6"/>',
    "event_add": '<path d="M12 5v14M5 12h14"/>',
    "event_duplicate": ('<rect x="8" y="8" width="12" height="12" rx="2"/>'
                        '<path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
    "event_delete": _TRASH,
    "track_add": '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M12 9v6M9 12h6"/>',
    "eye_open": _EYE,
    "eye_closed": _EYE + '<path d="M3 3l18 18"/>',
    "locked": _LOCK_BODY + '<path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "unlocked": _LOCK_BODY + '<path d="M8 11V7a4 4 0 0 1 7.5-2"/>',
    "warning": '<path d="M12 3L2 20h20z"/><path d="M12 10v4"/><path d="M12 17v.01"/>',
    "error": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "ok": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
    "search": _SEARCH,
    "popout": ('<rect x="3" y="7" width="14" height="14" rx="2"/><path d="M11 3h10v10"/>'
               '<path d="M21 3l-9 9"/>'),
    "chevron_down": '<path d="M6 9l6 6 6-6"/>',
    "chevron_up": '<path d="M6 15l6-6 6 6"/>',
    "chevron_left": '<path d="M15 6l-6 6 6 6"/>',
    "chevron_right": '<path d="M9 6l6 6-6 6"/>',
    "arrow_right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "pencil": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "note": '<path d="M5 4h14v12l-4 4H5z"/><path d="M9 9h6M9 13h4"/>',
    "flag": '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>',
    "magnet": ('<path d="M5 4h4v7a3 3 0 0 0 6 0V4h4v7a7 7 0 0 1-14 0z"/>'
               '<path d="M5 8h4M15 8h4"/>'),
}

ICON_NAMES = tuple(_SHAPES)


def _svg(name: str, color: str) -> bytes:
    body = _SHAPES[name].replace("currentColor", color)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    ).encode()


@lru_cache(maxsize=512)
def make_icon(name: str, color: str, size: int = 18) -> QIcon:
    """Иконка ``name`` цветом ``color``.

    Результат кэшируется: таймлайн зовёт значки из ``paintEvent`` — по
    одному на каждую дорожку, при каждом движении мыши. Набор сочетаний
    конечен (имя × цвет темы × размер), поэтому кэш не растёт.

    Рисуется с запасом по плотности пикселей: на экране со 150 % значок в
    размер логических пикселей выходил мыльным.
    """
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    QSvgRenderer(QByteArray(_svg(name, color))).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def paint_icon(painter: QPainter, name: str, color: str, rect: QRectF | QRect) -> None:
    """Рисует значок в прямоугольнике — замена ``drawText`` с эмодзи.

    Эмодзи в интерфейсе зависят от шрифта: на одной машине это картинка, на
    другой — пустой прямоугольник, и проверить это по своей машине нельзя.
    """
    target = rect.toRect() if isinstance(rect, QRectF) else rect
    side = max(8, min(target.width(), target.height()))
    make_icon(name, color, side).paint(painter, target, Qt.AlignCenter)
