"""ctypes-обёртка над libass.

Готового поддерживаемого биндинга нет, поэтому обёртка своя. Покрыта только
та часть API, которая нужна редактору: инициализация, размер кадра, шрифты,
загрузка трека из памяти и рендер кадра.

Три правила, нарушение которых даёт падения, которые потом трудно поймать:

1. **Всё — из главного потока.** ``ass_render_frame`` не потокобезопасен
   относительно одного ``ASS_Renderer``. Параллельный рендер превью и
   измерения из воркера запрещён.
2. **Битмапы живут до следующего вызова.** Список ``ASS_Image`` действителен
   только до очередного ``ass_render_frame`` того же рендерера. Указатели
   хранить нельзя — либо копируем сразу, либо сразу заливаем в текстуру.
3. **Порядок наложения — по связному списку.** Он идёт от дальнего к ближнему
   (тень → обводка → глиф), и переставлять его нельзя.

Тонкость с цветом: в ``ASS_Image.color`` младший байт — это **прозрачность**
(0 = непрозрачный), обратная привычной альфе. Преобразование инкапсулировано
в :meth:`Bitmap.rgba`.
"""

from __future__ import annotations

import contextlib
import ctypes
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sfstudio.app.i18n import tr
from sfstudio.platform.native import libass_spec

__all__ = [
    "IMAGE_TYPE_CHARACTER",
    "IMAGE_TYPE_OUTLINE",
    "IMAGE_TYPE_SHADOW",
    "AssContext",
    "AssImage",
    "AssLibraryError",
    "Bitmap",
    "available",
]

IMAGE_TYPE_CHARACTER = 0
IMAGE_TYPE_OUTLINE = 1
IMAGE_TYPE_SHADOW = 2

#: Значения ASS_DefaultFontProvider.
FONTPROVIDER_NONE = 0
FONTPROVIDER_AUTODETECT = 1


class AssLibraryError(RuntimeError):
    """libass недоступна или вернула ошибку."""


class AssImage(ctypes.Structure):
    """Структура ``ASS_Image`` из ass.h."""


AssImage._fields_ = [
    ("w", ctypes.c_int),
    ("h", ctypes.c_int),
    ("stride", ctypes.c_int),
    ("bitmap", ctypes.POINTER(ctypes.c_ubyte)),
    ("color", ctypes.c_uint32),
    ("dst_x", ctypes.c_int),
    ("dst_y", ctypes.c_int),
    ("next", ctypes.POINTER(AssImage)),
    ("type", ctypes.c_int),
]


@dataclass(frozen=True, slots=True)
class Bitmap:
    """Один слой отрисовки, скопированный из ``ASS_Image``.

    Копия сделана намеренно: исходные данные libass переиспользует под
    следующий кадр, и держать на них указатель нельзя.
    """

    x: int
    y: int
    w: int
    h: int
    stride: int
    #: 8-битная альфа-маска, ``h * stride`` байт.
    data: bytes
    #: Цвет как ``0xRRGGBBAA`` в исходной нотации libass.
    raw_color: int
    kind: int

    @property
    def rgba(self) -> tuple[int, int, int, int]:
        """Цвет в привычном виде: ``a=255`` — непрозрачный."""
        c = self.raw_color
        return ((c >> 24) & 0xFF, (c >> 16) & 0xFF, (c >> 8) & 0xFF, 255 - (c & 0xFF))

    @property
    def is_shadow(self) -> bool:
        return self.kind == IMAGE_TYPE_SHADOW


def available() -> bool:
    """Есть ли libass на машине."""
    return libass_spec().locate() is not None


_MSG_CB = ctypes.CFUNCTYPE(
    None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p
)


class AssContext:
    """Библиотека + рендерер + текущий трек.

    Использование::

        ctx = AssContext()
        ctx.set_frame_size(1920, 1080)
        ctx.set_storage_size(1920, 1080)
        ctx.load_track(ass_text)
        for bitmap in ctx.render(2000):
            ...
    """

    __slots__ = ("_fonts_ready", "_lib", "_library", "_log", "_renderer", "_track")

    def __init__(self, *, quiet: bool = True) -> None:
        self._lib = self._load()
        self._library = self._lib.ass_library_init()
        if not self._library:
            raise AssLibraryError(tr('ass_library_init вернула NULL'))

        # Держим ссылку на колбэк: если её потерять, ctypes соберёт объект,
        # и libass вызовет освобождённую память.
        self._log = _MSG_CB(self._on_message)
        if quiet:
            self._lib.ass_set_message_cb(self._library, self._log, None)

        self._renderer = self._lib.ass_renderer_init(self._library)
        if not self._renderer:
            self._lib.ass_library_done(self._library)
            raise AssLibraryError(tr('ass_renderer_init вернула NULL'))

        self._track: Any = None
        self._fonts_ready = False

    # -- загрузка библиотеки -------------------------------------------------- #

    @staticmethod
    def _load():
        spec = libass_spec()
        lib = spec.load()  # бросает OSError с понятным текстом
        _declare(lib)
        return lib

    def _on_message(self, level: int, fmt: bytes, args: Any, data: Any) -> None:
        """Гасим болтовню libass. Уровни 0-2 — реальные ошибки."""
        _ = (fmt, args, data)
        if level <= 2:
            pass  # сюда можно подключить logging, когда появится

    # -- версия ---------------------------------------------------------------- #

    @property
    def version(self) -> str:
        """Версия libass.

        Значение закодировано BCD-подобно: 0x01705001 читается как 0.17.5,
        то есть ``0x17`` означает 17, а не 23. Прямая арифметика по битам
        даёт неверный ответ — поэтому разбор идёт по hex-строке.
        """
        raw = self._lib.ass_library_version()
        text = f"{raw & 0xFFFFFFFF:08x}"
        return f"{int(text[0])}.{int(text[1:3])}.{int(text[3:5])}"

    # -- настройка -------------------------------------------------------------- #

    def set_frame_size(self, width: int, height: int) -> None:
        """Размер кадра, в который рисуем."""
        self._lib.ass_set_frame_size(self._renderer, int(width), int(height))

    def set_storage_size(self, width: int, height: int) -> None:
        """Разрешение, для которого написан скрипт (``PlayResX/Y``).

        Без него обводка и тень масштабируются неправильно при
        ``ScaledBorderAndShadow``, и превью расходится с плеером зрителя.
        """
        self._lib.ass_set_storage_size(self._renderer, int(width), int(height))

    def set_pixel_aspect(self, par: float) -> None:
        """Отношение сторон пикселя — для anamorphic-источников."""
        self._lib.ass_set_pixel_aspect(self._renderer, ctypes.c_double(par))

    def set_fonts(
        self,
        *,
        default_family: str = "Sans",
        fontconfig: bool = True,
        update: bool = True,
    ) -> None:
        """Инициализирует подбор шрифтов.

        Первый вызов сканирует системные шрифты и может занять сотни
        миллисекунд, поэтому делается один раз и лениво — при первом рендере.
        """
        provider = FONTPROVIDER_AUTODETECT if fontconfig else FONTPROVIDER_NONE
        self._lib.ass_set_fonts(
            self._renderer,
            None,
            default_family.encode("utf-8"),
            provider,
            None,
            1 if update else 0,
        )
        self._fonts_ready = True

    def add_font(self, name: str, data: bytes) -> None:
        """Регистрирует шрифт из памяти — для вложений ASS и MKV."""
        self._lib.ass_add_font(
            self._library, name.encode("utf-8"), data, len(data)
        )
        self._fonts_ready = False  # состав шрифтов изменился

    # -- трек -------------------------------------------------------------------- #

    def load_track(self, ass_text: str) -> None:
        """Загружает трек из сериализованного ASS.

        Кодировка всегда UTF-8, поэтому codepage передаётся как NULL:
        просить libass угадывать кодировку там, где мы её точно знаем,
        значит подставляться под ошибочную эвристику.
        """
        self.free_track()
        payload = ass_text.encode("utf-8")
        buffer = ctypes.create_string_buffer(payload, len(payload))
        self._track = self._lib.ass_read_memory(
            self._library, buffer, len(payload), None
        )
        if not self._track:
            raise AssLibraryError(tr('ass_read_memory вернула NULL'))

    def free_track(self) -> None:
        if self._track:
            self._lib.ass_free_track(self._track)
            self._track = None

    @property
    def has_track(self) -> bool:
        return bool(self._track)

    # -- рендер ------------------------------------------------------------------ #

    def render(self, time_ms: int) -> list[Bitmap]:
        """Отрисовывает кадр и возвращает копии слоёв.

        Копирование обязательно: указатели libass живут только до следующего
        вызова. Порядок списка сохраняется — он и есть порядок наложения.
        """
        return list(self.iter_render(time_ms))

    def iter_render(self, time_ms: int) -> Iterator[Bitmap]:
        if not self._track:
            return
        if not self._fonts_ready:
            self.set_fonts()

        change = ctypes.c_int(0)
        head = self._lib.ass_render_frame(
            self._renderer, self._track, ctypes.c_longlong(int(time_ms)),
            ctypes.byref(change),
        )
        node = head
        while node:
            image = node.contents
            size = image.h * image.stride
            if size > 0 and image.bitmap:
                data = ctypes.string_at(image.bitmap, size)
                yield Bitmap(
                    x=image.dst_x,
                    y=image.dst_y,
                    w=image.w,
                    h=image.h,
                    stride=image.stride,
                    data=data,
                    raw_color=image.color,
                    kind=image.type,
                )
            node = image.next

    def bbox(
        self, time_ms: int, *, include_shadow: bool = False
    ) -> tuple[int, int, int, int] | None:
        """Ограничивающий прямоугольник всего кадра: ``(x, y, w, h)``.

        Тень по умолчанию не входит: пользователь тянет за текст, а не за его
        тень, и рамка выделения по тени выглядела бы смещённой.
        """
        x0 = y0 = 1 << 30
        x1 = y1 = -(1 << 30)
        for bitmap in self.iter_render(time_ms):
            if bitmap.is_shadow and not include_shadow:
                continue
            x0 = min(x0, bitmap.x)
            y0 = min(y0, bitmap.y)
            x1 = max(x1, bitmap.x + bitmap.w)
            y1 = max(y1, bitmap.y + bitmap.h)
        if x1 < x0:
            return None
        return (x0, y0, x1 - x0, y1 - y0)

    # -- жизненный цикл ------------------------------------------------------------ #

    def close(self) -> None:
        self.free_track()
        if getattr(self, "_renderer", None):
            self._lib.ass_renderer_done(self._renderer)
            self._renderer = None
        if getattr(self, "_library", None):
            self._lib.ass_library_done(self._library)
            self._library = None

    def __enter__(self) -> AssContext:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        # На выходе интерпретатор мог уже разобрать модули, и close() упадёт
        # на обращении к ctypes. Ронять процесс из-за этого незачем.
        with contextlib.suppress(Exception):
            self.close()


def _declare(lib: ctypes.CDLL) -> None:
    """Объявляет сигнатуры.

    Без явных ``argtypes``/``restype`` ctypes считает, что всё возвращает
    ``int``, и на 64-битной платформе указатели обрезаются до 32 бит —
    падение происходит не там, где ошибка, и ищется долго.
    """
    void_p = ctypes.c_void_p

    lib.ass_library_init.restype = void_p
    lib.ass_library_init.argtypes = []

    lib.ass_library_done.restype = None
    lib.ass_library_done.argtypes = [void_p]

    lib.ass_library_version.restype = ctypes.c_int
    lib.ass_library_version.argtypes = []

    lib.ass_set_message_cb.restype = None
    lib.ass_set_message_cb.argtypes = [void_p, _MSG_CB, void_p]

    lib.ass_renderer_init.restype = void_p
    lib.ass_renderer_init.argtypes = [void_p]

    lib.ass_renderer_done.restype = None
    lib.ass_renderer_done.argtypes = [void_p]

    lib.ass_set_frame_size.restype = None
    lib.ass_set_frame_size.argtypes = [void_p, ctypes.c_int, ctypes.c_int]

    lib.ass_set_storage_size.restype = None
    lib.ass_set_storage_size.argtypes = [void_p, ctypes.c_int, ctypes.c_int]

    lib.ass_set_pixel_aspect.restype = None
    lib.ass_set_pixel_aspect.argtypes = [void_p, ctypes.c_double]

    lib.ass_set_fonts.restype = None
    lib.ass_set_fonts.argtypes = [
        void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
        ctypes.c_char_p, ctypes.c_int,
    ]

    lib.ass_add_font.restype = None
    lib.ass_add_font.argtypes = [void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]

    lib.ass_read_memory.restype = void_p
    lib.ass_read_memory.argtypes = [void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]

    lib.ass_free_track.restype = None
    lib.ass_free_track.argtypes = [void_p]

    lib.ass_render_frame.restype = ctypes.POINTER(AssImage)
    lib.ass_render_frame.argtypes = [
        void_p, void_p, ctypes.c_longlong, ctypes.POINTER(ctypes.c_int)
    ]
