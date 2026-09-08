"""Цвет и его представление в ASS.

Единственное место в проекте, где живёт инверсия альфы. Внутри приложения
``a = 255`` означает «непрозрачный» (как везде); в ASS наоборот — ``00`` это
непрозрачный, ``FF`` полностью прозрачный. Смешение этих соглашений — классический
источник багов «субтитры невидимые», поэтому конверсия инкапсулирована здесь.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["RGBA"]

# &HAABBGGRR& — альфа и амперсанд на конце необязательны, регистр любой.
_ASS_COLOR_RE = re.compile(r"^\s*&?H?([0-9a-fA-F]{1,8})&?\s*$")


@dataclass(frozen=True, slots=True)
class RGBA:
    """Цвет. ``a=255`` — непрозрачный, ``a=0`` — полностью прозрачный."""

    r: int = 255
    g: int = 255
    b: int = 255
    a: int = 255

    def __post_init__(self) -> None:
        for name in ("r", "g", "b", "a"):
            value = getattr(self, name)
            if not 0 <= value <= 255:
                raise ValueError(f"RGBA.{name} вне диапазона 0..255: {value}")

    # -- ASS ---------------------------------------------------------------- #

    def to_ass(self, *, with_alpha: bool = True) -> str:
        """``&HAABBGGRR`` (или ``&HBBGGRR`` без альфы)."""
        if with_alpha:
            return f"&H{255 - self.a:02X}{self.b:02X}{self.g:02X}{self.r:02X}"
        return f"&H{self.b:02X}{self.g:02X}{self.r:02X}"

    @staticmethod
    def from_ass(text: str) -> RGBA:
        """Разбирает ``&HAABBGGRR&``, ``&HBBGGRR``, ``&H0`` и прочие огрызки.

        ASS в дикой природе неряшлив: встречаются значения без альфы, без
        ведущих нулей и без амперсандов. Недостающие старшие разряды считаются
        нулями, что и даёт непрозрачный цвет — то же поведение, что у libass.
        """
        m = _ASS_COLOR_RE.match(text)
        if not m:
            raise ValueError(f"не похоже на цвет ASS: {text!r}")
        digits = m.group(1).rjust(8, "0")
        ass_alpha = int(digits[0:2], 16)
        return RGBA(
            r=int(digits[6:8], 16),
            g=int(digits[4:6], 16),
            b=int(digits[2:4], 16),
            a=255 - ass_alpha,
        )

    @staticmethod
    def alpha_from_ass(text: str) -> int:
        """Для тегов ``\\1a`` и подобных: ``&HRR&`` → внутренняя альфа."""
        m = _ASS_COLOR_RE.match(text)
        if not m:
            raise ValueError(f"не похоже на альфу ASS: {text!r}")
        return 255 - int(m.group(1)[-2:].rjust(2, "0"), 16)

    # -- прочее ------------------------------------------------------------- #

    def to_hex(self) -> str:
        """``#RRGGBB`` — для виджетов выбора цвета."""
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    @staticmethod
    def from_hex(text: str) -> RGBA:
        """``#RRGGBB``, ``#RGB`` или ``#RRGGBBAA``.

        Для цветов, которые задаёт не ASS, а мы сами: акторы, дорожки,
        сохранённые пользовательские настройки. Здесь альфа **не** инвертирована
        — это обычный веб-цвет, а не поле ASS.
        """
        raw = text.strip().lstrip("#")
        if len(raw) == 3:
            raw = "".join(char * 2 for char in raw)
        if len(raw) not in (6, 8):
            raise ValueError(f"не похоже на #RRGGBB: {text!r}")
        try:
            value = int(raw, 16)
        except ValueError as exc:
            raise ValueError(f"не похоже на #RRGGBB: {text!r}") from exc
        if len(raw) == 6:
            return RGBA((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF, 255)
        return RGBA(
            (value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
        )

    @property
    def luminance(self) -> float:
        """Воспринимаемая яркость 0..1 — по ней выбирают контрастный текст."""
        return (0.2126 * self.r + 0.7152 * self.g + 0.0722 * self.b) / 255.0

    def contrasting_text(self) -> RGBA:
        """Чёрный или белый — тот, что читается поверх этого цвета."""
        return RGBA(0, 0, 0, 255) if self.luminance > 0.55 else RGBA(255, 255, 255, 255)

    def with_alpha(self, a: int) -> RGBA:
        return RGBA(self.r, self.g, self.b, a)

    @property
    def is_opaque(self) -> bool:
        return self.a == 255
