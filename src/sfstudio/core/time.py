"""Время, кадры и таймкоды.

Правила модуля:
* Время внутри приложения — целые миллисекунды (``int``). Никаких float.
* Частота кадров — точная дробь (``Fraction``), а не float: 23.976 это 24000/1001,
  и накопление ошибки на двухчасовом фильме при float даёт расхождение в кадры.
* ASS хранит время с точностью до сантисекунд — потеря точности при экспорте
  неизбежна и обрабатывается явно (:func:`round_for_ass`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

__all__ = [
    "MS_PER_HOUR",
    "FpsModel",
    "SnapMode",
    "format_ass",
    "format_smpte",
    "format_srt",
    "format_vtt",
    "parse_timecode",
    "round_for_ass",
]

MS_PER_HOUR = 3_600_000

# Стандартные частоты. NTSC-варианты — именно дроби, не 23.98/29.97.
FPS_FILM = Fraction(24, 1)
FPS_NTSC_FILM = Fraction(24000, 1001)  # 23.976
FPS_PAL = Fraction(25, 1)
FPS_NTSC = Fraction(30000, 1001)  # 29.97
FPS_60 = Fraction(60, 1)

_KNOWN_FPS: dict[str, Fraction] = {
    "23.976": FPS_NTSC_FILM,
    "24": FPS_FILM,
    "25": FPS_PAL,
    "29.97": FPS_NTSC,
    "30": Fraction(30, 1),
    "50": Fraction(50, 1),
    "59.94": Fraction(60000, 1001),
    "60": FPS_60,
}


class SnapMode(Enum):
    """Как округлять время к сетке кадров.

    START — к началу кадра (субтитр появляется вместе с кадром).
    END   — к последней миллисекунде кадра, чтобы конец не «залезал» на следующий.
    NEAREST — к ближайшей границе.
    """

    START = "start"
    END = "end"
    NEAREST = "nearest"


@dataclass(frozen=True, slots=True)
class FpsModel:
    """Кадровая модель источника."""

    rate: Fraction = FPS_NTSC_FILM
    drop_frame: bool = False

    @classmethod
    def from_float(cls, value: float, *, drop_frame: bool = False) -> FpsModel:
        """Подбирает точную дробь по приблизительному float из метаданных.

        ffmpeg отдаёт 23.976023976..., и превращать это в Fraction напрямую нельзя —
        получится дробь с гигантским знаменателем. Ищем известную частоту в пределах
        0.5 %, иначе берём ограниченное рациональное приближение.
        """
        candidates = (*_KNOWN_FPS.values(), Fraction(48, 1), Fraction(120, 1))
        # Именно ближайшая, а не первая подходящая: 59.94 и 60 отличаются на 0.1 %,
        # и перебор «первый в пределах допуска» превращал ровные 60 в 60000/1001.
        best = min(candidates, key=lambda k: abs(float(k) - value))
        if abs(float(best) - value) < float(best) * 0.002:
            return cls(best, drop_frame=drop_frame)
        return cls(Fraction(value).limit_denominator(1001), drop_frame=drop_frame)

    @property
    def is_ntsc(self) -> bool:
        return self.rate.denominator == 1001

    @property
    def as_float(self) -> float:
        return float(self.rate)

    @property
    def frame_duration_ms(self) -> float:
        return 1000.0 / float(self.rate)

    def frame_of(self, ms: int) -> int:
        """Номер кадра, содержащего момент ``ms``. Целочисленно, без float."""
        return (ms * self.rate.numerator) // (1000 * self.rate.denominator)

    def frame_start_ms(self, frame: int) -> int:
        """Начало кадра в миллисекундах, округление вниз."""
        return (frame * 1000 * self.rate.denominator) // self.rate.numerator

    def snap(self, ms: int, mode: SnapMode = SnapMode.NEAREST) -> int:
        """Округляет время к сетке кадров."""
        frame = self.frame_of(ms)
        start = self.frame_start_ms(frame)
        if mode is SnapMode.START:
            return start
        next_start = self.frame_start_ms(frame + 1)
        if mode is SnapMode.END:
            # Последняя миллисекунда этого кадра.
            return max(start, next_start - 1)
        return start if (ms - start) * 2 <= (next_start - start) else next_start

    def ms_to_frames(self, ms: int) -> float:
        """Длительность в кадрах (дробных) — для отображения, не для арифметики."""
        return ms * float(self.rate) / 1000.0


# --------------------------------------------------------------------------- #
# Форматирование
# --------------------------------------------------------------------------- #


def _split(ms: int) -> tuple[int, int, int, int]:
    if ms < 0:
        ms = 0
    h, rem = divmod(ms, MS_PER_HOUR)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return h, m, s, msec


def format_ass(ms: int) -> str:
    """``H:MM:SS.cc`` — часы без ведущего нуля, сантисекунды.

    Внимание: формат теряет точность. Используйте :func:`round_for_ass` до вызова,
    если нужно контролировать направление округления.
    """
    h, m, s, msec = _split(ms)
    return f"{h:d}:{m:02d}:{s:02d}.{msec // 10:02d}"


def format_srt(ms: int) -> str:
    """``HH:MM:SS,mmm``."""
    h, m, s, msec = _split(ms)
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


def format_vtt(ms: int) -> str:
    """``HH:MM:SS.mmm``."""
    h, m, s, msec = _split(ms)
    return f"{h:02d}:{m:02d}:{s:02d}.{msec:03d}"


def format_smpte(ms: int, fps: FpsModel) -> str:
    """``HH:MM:SS:FF`` — для отображения в полях таймкода."""
    h, m, s, msec = _split(ms)
    frame = int(msec * float(fps.rate) / 1000.0)
    sep = ";" if fps.drop_frame else ":"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{frame:02d}"


def round_for_ass(start_ms: int, end_ms: int) -> tuple[int, int]:
    """Округляет пару к сантисекундной сетке ASS.

    Начало — вниз, конец — вверх, чтобы субтитр не укорачивался при экспорте.
    """
    return (start_ms // 10) * 10, ((end_ms + 9) // 10) * 10


# --------------------------------------------------------------------------- #
# Разбор
# --------------------------------------------------------------------------- #

_TC_RE = re.compile(
    r"""^\s*
    (?:(?P<h>\d{1,3}):)?             # часы необязательны
    (?P<m>\d{1,2}):
    (?P<s>\d{1,2})
    (?:
        [.,](?P<frac>\d{1,3})        # доли секунды
      | [:;](?P<frames>\d{1,3})      # SMPTE-кадры
    )?
    \s*$""",
    re.VERBOSE,
)


def parse_timecode(text: str, fps: FpsModel | None = None) -> int | None:
    """Терпимый разбор таймкода любого из поддерживаемых форматов.

    Понимает ``1:02:03.45``, ``01:02:03,456``, ``02:03.4``, ``00:00:05:12`` (SMPTE).

    Разделителем часов и минут служит только ``:``. Допускать здесь ещё и точку
    нельзя: ``02:03.5`` тогда читается и как «2 ч 3 мин 5 с», и как «2 мин 3.5 с»,
    причём жадный разбор выбирает первое — а пользователь имеет в виду второе.
    Возвращает ``None``, если строка не похожа на таймкод — вызывающий решает,
    считать это ошибкой ввода или нет.
    """
    if not text:
        return None
    m = _TC_RE.match(text)
    if not m:
        return None

    hours = int(m.group("h") or 0)
    minutes = int(m.group("m"))
    seconds = int(m.group("s"))
    if minutes > 59 or seconds > 59:
        return None

    ms = (hours * 3600 + minutes * 60 + seconds) * 1000

    if frac := m.group("frac"):
        # "5" → 500 мс, "45" → 450 мс, "456" → 456 мс
        ms += int(frac.ljust(3, "0"))
    elif frames := m.group("frames"):
        model = fps or FpsModel()
        ms += int(int(frames) * 1000 / float(model.rate))
    return ms
