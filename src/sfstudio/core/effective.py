"""Действующее оформление реплики: стиль плюс наложенные на него теги.

Панель быстрого форматирования и инспектор обязаны показывать то, что зритель
увидит на экране, а не то, что записано в стиле. Реплика ``{\\b1\\fs60}Текст``
идёт жирной шестидесятым кеглем, даже если её стиль говорит обратное, — и
переключатель «жирный» должен стоять во включённом положении.

Считается только **первый** блок тегов. Причина в том, что оформление может
меняться по ходу строки: ``Обычный {\\b1}жирный{\\b0} снова обычный``. Единого
ответа «какой у реплики кегль» в этом случае нет, и делать вид, что есть,
нельзя. Поэтому наложение берётся из тегов до начала текста — это то, с чего
реплика начинается, — а про остальные вызывающий код узнаёт из
:attr:`Effective.mixed`, чтобы показать смешанное состояние, а не соврать.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sfstudio.core import tags as tagmod
from sfstudio.core.color import RGBA
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle

__all__ = ["Effective", "effective_style", "style_tags"]

#: Теги, меняющие оформление по ходу строки. Их наличие после начала текста
#: означает, что одним значением реплику не описать.
_FORMATTING_TAGS = frozenset(
    {"b", "i", "u", "s", "fn", "fs", "c", "1c", "2c", "3c", "4c",
     "alpha", "1a", "2a", "3a", "4a", "fscx", "fscy", "fsp"}
)


@dataclass(slots=True)
class Effective:
    """Оформление, с которым реплика начинается."""

    fontname: str
    fontsize: float
    bold: bool
    italic: bool
    underline: bool
    strikeout: bool
    primary: RGBA
    outline_color: RGBA
    alignment: int
    scale_x: float
    scale_y: float
    spacing: float
    angle: float

    #: Свойства, которые меняются внутри реплики: показывать их одним
    #: значением нельзя — переключатель должен быть в третьем состоянии.
    mixed: frozenset[str] = field(default_factory=frozenset)

    #: Свойства, заданные тегом, а не унаследованные из стиля. Инспектор
    #: помечает их, чтобы было видно, что именно переопределено вручную.
    overridden: frozenset[str] = field(default_factory=frozenset)

    def is_overridden(self, name: str) -> bool:
        return name in self.overridden

    def is_mixed(self, name: str) -> bool:
        return name in self.mixed


def _flag(raw: str, fallback: bool) -> bool:
    """``\\b1`` → True, ``\\b0`` → False, ``\\b`` без аргумента → из стиля.

    Пустой аргумент в ASS означает «вернуть значение стиля», а не «выключить».
    """
    text = raw.strip()
    if not text:
        return fallback
    try:
        return int(float(text)) != 0
    except ValueError:
        return fallback


def _number(raw: str, fallback: float) -> float:
    try:
        return float(raw.strip())
    except ValueError:
        return fallback


def _color(raw: str, fallback: RGBA) -> RGBA:
    try:
        return RGBA.from_ass(raw)
    except ValueError:
        return fallback


def effective_style(event: SubtitleEvent, style: SubtitleStyle) -> Effective:
    """Оформление реплики с учётом её тегов."""
    parsed = event.parsed
    result = Effective(
        fontname=style.fontname,
        fontsize=style.fontsize,
        bold=style.bold,
        italic=style.italic,
        underline=style.underline,
        strikeout=style.strikeout,
        primary=style.primary,
        outline_color=style.outline_color,
        alignment=style.alignment,
        scale_x=style.scale_x,
        scale_y=style.scale_y,
        spacing=style.spacing,
        angle=style.angle,
    )

    leading, trailing = _split_blocks(parsed, event.text)
    overridden: set[str] = set()

    for tag in leading:
        name = tag.name
        raw = tag.args_raw
        if name == "b":
            result.bold = _flag(raw, style.bold)
        elif name == "i":
            result.italic = _flag(raw, style.italic)
        elif name == "u":
            result.underline = _flag(raw, style.underline)
        elif name == "s":
            result.strikeout = _flag(raw, style.strikeout)
        elif name == "fn":
            result.fontname = raw.strip() or style.fontname
        elif name == "fs":
            result.fontsize = _number(raw, style.fontsize)
        elif name in ("c", "1c"):
            result.primary = _color(raw, style.primary)
            name = "c"
        elif name == "3c":
            result.outline_color = _color(raw, style.outline_color)
        elif name == "an":
            value = int(_number(raw, style.alignment))
            result.alignment = value if 1 <= value <= 9 else style.alignment
        elif name == "fscx":
            result.scale_x = _number(raw, style.scale_x)
        elif name == "fscy":
            result.scale_y = _number(raw, style.scale_y)
        elif name == "fsp":
            result.spacing = _number(raw, style.spacing)
        elif name == "frz":
            result.angle = _number(raw, style.angle)
        else:
            continue
        overridden.add(name)

    result.overridden = frozenset(overridden)
    result.mixed = frozenset(
        tag.name if tag.name != "1c" else "c"
        for tag in trailing
        if tag.name in _FORMATTING_TAGS
    )
    return result


def _split_blocks(
    parsed: tagmod.ParsedTags, text: str
) -> tuple[list[tagmod.Tag], list[tagmod.Tag]]:
    """Теги до начала видимого текста и все последующие.

    Граница именно по видимому тексту, а не по номеру блока: реплика может
    начинаться с нескольких блоков подряд (``{\\an8}{\\b1}Текст``), и все они
    описывают её начало.
    """
    leading: list[tagmod.Tag] = []
    trailing: list[tagmod.Tag] = []
    seen_text = False
    cursor = 0

    for block in parsed.blocks:
        if text[cursor : block.start].strip():
            seen_text = True
        (trailing if seen_text else leading).extend(block.tags)
        cursor = block.end

    return leading, trailing


def style_tags(
    wanted: SubtitleStyle, base: SubtitleStyle | None
) -> dict[str, object]:
    """Теги, которыми ``wanted`` отличается от стиля реплики.

    Нужно, когда оформление применяют не ко всему стилю, а к выбранным
    репликам: стиль общий, и перекрасить через него одну реплику нельзя, не
    перекрасив остальные. Разница ложится тегами — тем самым способом, каким
    ASS и описывает исключение из стиля.

    Совпадающие свойства дают ``None`` — это указание **снять** тег, если он
    там был. Иначе прежнее оформление осталось бы поверх нового: человек
    вернул кегль к стилевому, а в реплике по-прежнему стоит ``\fs72``.
    """
    if base is None:
        base = SubtitleStyle()

    tags: dict[str, object] = {}

    def put(name: str, value: object, same: bool) -> None:
        tags[name] = None if same else value

    put("fn", wanted.fontname, wanted.fontname == base.fontname)
    put("fs", _num(wanted.fontsize), wanted.fontsize == base.fontsize)
    put("c", wanted.primary.to_ass(with_alpha=False), wanted.primary == base.primary)
    put(
        "3c",
        wanted.outline_color.to_ass(with_alpha=False),
        wanted.outline_color == base.outline_color,
    )
    put(
        "4c",
        wanted.back_color.to_ass(with_alpha=False),
        wanted.back_color == base.back_color,
    )
    put("b", 1 if wanted.bold else 0, wanted.bold == base.bold)
    put("i", 1 if wanted.italic else 0, wanted.italic == base.italic)
    put("u", 1 if wanted.underline else 0, wanted.underline == base.underline)
    put("s", 1 if wanted.strikeout else 0, wanted.strikeout == base.strikeout)
    put("bord", _num(wanted.outline), wanted.outline == base.outline)
    put("shad", _num(wanted.shadow), wanted.shadow == base.shadow)
    put("fscx", _num(wanted.scale_x), wanted.scale_x == base.scale_x)
    put("fscy", _num(wanted.scale_y), wanted.scale_y == base.scale_y)
    put("fsp", _num(wanted.spacing), wanted.spacing == base.spacing)
    put("frz", _num(wanted.angle), wanted.angle == base.angle)
    put("an", wanted.alignment, wanted.alignment == base.alignment)
    return tags


def _num(value: float) -> str:
    """Число для тега: без хвостового нуля, как это принято в ASS."""
    return f"{value:g}"
