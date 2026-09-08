"""Рендер субтитров через libass: измерение и отрисовка кадра.

Здесь две разные задачи, и у каждой свой контекст libass:

* **Измерение** — точный bbox события для рамки выделения и хит-теста.
  Кадр выставлен ровно в ``PlayResX/Y``, поэтому координаты libass совпадают
  со script-координатами, и переводить ничего не нужно.
* **Отрисовка** — картинка для превью. Кадр равен размеру видео-области
  виджета, чтобы текст рисовался в нативном разрешении, а не масштабировался.

Два контекста, а не один с переключением размера: ``ass_set_frame_size``
сбрасывает внутренние кэши libass, а размер виджета и PlayRes различаются
почти всегда — переключение на каждое измерение обесценило бы кэш.

Двухтрековая схема (§12.3 спецификации): базовый трек пересобирается только
при смене ревизии документа, а измерение одного события идёт через отдельный
крошечный трек — он стоит десятки микросекунд независимо от размера документа.
"""

from __future__ import annotations

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle
from sfstudio.io.formats.ass import write_ass
from sfstudio.render.geometry import Rect, Size
from sfstudio.render.libass import AssContext, Bitmap, available

__all__ = ["LibassMeasurer", "LibassRenderer", "available"]

#: Синтетическое событие живёт с 0 до 10 с, измеряем в середине — так любые
#: анимации (\fad, \t) уже отработали вступление и дают устоявшийся размер.
_PROBE_MS = 1000
_PROBE_START = 0
_PROBE_END = 10_000

#: Больше этого числа записей кэш не растит: при 20 000 событий он занял бы
#: десятки мегабайт, а попадания дают только видимые события.
_CACHE_LIMIT = 4096


class LibassMeasurer:
    """Точные размеры и положение текста от libass.

    Реализует протокол :class:`sfstudio.render.geometry.TextMeasurer`
    (метод :meth:`measure`) и дополнительно отдаёт готовый прямоугольник
    через :meth:`bbox`. Второй путь точнее: libass сама учитывает перенос
    строк, поля, выравнивание и inline-теги, поэтому восстанавливать
    положение из якоря не требуется.
    """

    __slots__ = ("_cache", "_ctx", "_doc", "_play_res")

    def __init__(self, doc: SubtitleDocument | None = None) -> None:
        self._ctx = AssContext()
        self._doc = doc
        self._play_res = doc.script_info.play_res if doc else (1920, 1080)
        self._cache: dict[tuple, Rect | None] = {}
        self._apply_size()

    def _apply_size(self) -> None:
        width, height = self._play_res
        # Кадр == PlayRes, поэтому вывод libass сразу в script-координатах.
        self._ctx.set_frame_size(width, height)
        self._ctx.set_storage_size(width, height)

    def set_document(self, doc: SubtitleDocument) -> None:
        self._doc = doc
        self._cache.clear()
        if doc.script_info.play_res != self._play_res:
            self._play_res = doc.script_info.play_res
            self._apply_size()

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- измерение -------------------------------------------------------------- #

    def _key(self, event: SubtitleEvent) -> tuple:
        """Ключ кэша по всему, что влияет на раскладку события."""
        return (
            event.text,
            event.style,
            event.margin_l,
            event.margin_r,
            event.margin_v,
            self._play_res,
        )

    def bbox(self, event: SubtitleEvent) -> Rect | None:
        """Прямоугольник события в script-координатах, как его увидит зритель."""
        if self._doc is None:
            return None
        key = self._key(event)
        if key in self._cache:
            return self._cache[key]

        self._ctx.load_track(_single_event_script(event, self._doc))
        box = self._ctx.bbox(_PROBE_MS)
        rect = Rect(float(box[0]), float(box[1]), float(box[2]), float(box[3])) if box else None

        if len(self._cache) >= _CACHE_LIMIT:
            self._cache.clear()
        self._cache[key] = rect
        return rect

    def measure(self, event: SubtitleEvent, style: SubtitleStyle) -> Size:
        """Размер события. Часть протокола ``TextMeasurer``."""
        _ = style  # стиль берётся из документа: там он с учётом \r и наследования
        rect = self.bbox(event)
        return Size(rect.w, rect.h) if rect else Size(0.0, 0.0)

    def close(self) -> None:
        self._ctx.close()
        self._cache.clear()


def _single_event_script(event: SubtitleEvent, doc: SubtitleDocument) -> str:
    """Мини-скрипт: заголовок документа, все стили и одно событие.

    Стили копируются целиком, а не только используемый: событие может
    переключить стиль тегом ``\\r``, и без остальных измерение разойдётся
    с настоящим рендером.
    """
    probe = SubtitleDocument()
    probe.script_info = doc.script_info
    probe.styles = doc.styles
    probe.add_event(
        SubtitleEvent(
            eid=1,
            start=_PROBE_START,
            end=_PROBE_END,
            text=event.text,
            style=event.style,
            layer=event.layer,
            name=event.name,
            margin_l=event.margin_l,
            margin_r=event.margin_r,
            margin_v=event.margin_v,
            effect=event.effect,
        )
    )
    return write_ass(probe)


#: Запас по времени вокруг текущей позиции, попадающий в трек libass.
#: Полминуты в каждую сторону: при обычном воспроизведении трек пересобирается
#: раз в тридцать секунд, а при перемотке ползунком мелкие рывки почти всегда
#: остаются внутри окна.
WINDOW_PAD_MS = 30_000


class LibassRenderer:
    """Отрисовка кадра субтитров в размер виджета.

    В libass загружаются **не все** реплики документа, а только попадающие в
    окно вокруг текущего времени. Причина измерена: полная пересборка трека на
    документе в двадцать тысяч строк стоила 79 мс, и платить их приходилось на
    каждое нажатие клавиши — при том, что меняется обычно одна реплика, а
    видно на кадре не больше нескольких.

    Окно делает стоимость постоянной: она зависит от плотности субтитров, а не
    от длины фильма. Корректность при этом не страдает — libass укладывает
    пересекающиеся реплики, глядя только на одновременно видимые, а они все
    внутри окна по построению.
    """

    __slots__ = ("_ctx", "_frame", "_play_res", "_revision", "_window")

    def __init__(self) -> None:
        self._ctx = AssContext()
        self._frame = (0, 0)
        self._play_res = (0, 0)
        self._revision = -1
        #: Границы загруженного окна. Пустое — трек не собран.
        self._window: tuple[int, int] | None = None

    @property
    def version(self) -> str:
        return self._ctx.version

    def set_frame_size(self, width: int, height: int, play_res: tuple[int, int]) -> None:
        size = (max(1, int(width)), max(1, int(height)))
        if size != self._frame:
            self._frame = size
            self._ctx.set_frame_size(*size)
        if play_res != self._play_res:
            # storage_size — разрешение, для которого написан скрипт. Без него
            # обводка и тень масштабируются мимо при ScaledBorderAndShadow.
            self._play_res = play_res
            self._ctx.set_storage_size(*play_res)

    def sync(self, doc: SubtitleDocument, time_ms: int | None = None) -> None:
        """Пересобирает трек, если документ изменился или время ушло из окна.

        Сравнение по ревизии, а не по содержимому: сериализация даже одного
        окна стоит времени, и делать её на каждый кадр нельзя.

        ``time_ms=None`` означает «весь документ»: так рендерер вёл себя
        всегда, и на этом держатся замеры и проверки, которым окно не нужно.
        """
        if time_ms is None:
            if self._revision == doc.revision and self._window is None and self._ctx.has_track:
                return
            self._ctx.load_track(write_ass(doc))
            self._revision = doc.revision
            self._window = None
            return

        if (
            self._revision == doc.revision
            and self._window is not None
            and self._window[0] <= time_ms <= self._window[1]
            and self._ctx.has_track
        ):
            return

        # Окно строится вокруг запрошенного момента, а не вокруг прежнего:
        # после перемотки продолжать от старого центра значило бы собирать
        # трек дважды подряд.
        start = max(0, time_ms - WINDOW_PAD_MS)
        end = time_ms + WINDOW_PAD_MS
        self._ctx.load_track(write_ass(doc, events=doc.in_range(start, end)))
        self._revision = doc.revision
        self._window = (start, end)

    def invalidate(self) -> None:
        self._revision = -1
        self._window = None

    def render(self, doc: SubtitleDocument, time_ms: int) -> list[Bitmap]:
        self.sync(doc, time_ms)
        return self._ctx.render(time_ms)

    def close(self) -> None:
        self._ctx.close()
