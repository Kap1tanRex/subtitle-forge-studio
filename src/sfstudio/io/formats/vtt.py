"""Чтение и запись WebVTT.

WebVTT — формат субтитров для веба. От SRT он отличается не только точкой
вместо запятой:

* обязательный заголовок ``WEBVTT``;
* необязательный идентификатор перед строкой времени;
* **настройки cue** после времени (``align``, ``line``, ``position``, ``size``,
  ``vertical``) — собственная система позиционирования в процентах;
* блоки ``NOTE``, ``STYLE`` и ``REGION``, которые не являются репликами;
* разметка тегами (``<b>``, ``<i>``, ``<u>``, ``<v Голос>``, ``<c.класс>``)
  и HTML-мнемоники (``&amp;``, ``&lt;``).

Позиционирование переводится в ASS с потерями в обе стороны, и это неизбежно:
WebVTT задаёт положение в процентах от кадра относительно строки текста, ASS —
якорем ``\\an`` и координатами ``\\pos`` в пикселях ``PlayRes``. Точного
соответствия нет, поэтому берётся ближайшее по смыслу: проценты становятся
координатами ``\\pos``, а выравнивание — якорем.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sfstudio.app.i18n import tr
from sfstudio.core import time as timemod
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle

__all__ = ["read_vtt", "write_vtt"]

_TIMING = re.compile(
    r"(\d{1,3}:)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})\s*-->\s*"
    r"(\d{1,3}:)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})(.*)"
)
_SETTING = re.compile(r"(\w+):([^\s]+)")
_VOICE = re.compile(r"<v(?:\.[^\s>]+)?\s+([^>]*)>", re.IGNORECASE)
_TAG = re.compile(r"</?[^>]+>")

#: Мнемоники, которые реально встречаются в субтитрах.
_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&nbsp;": " ", "&quot;": '"', "&#39;": "'",
}

#: Блоки, которые не являются репликами.
_BLOCK_KEYWORDS = ("NOTE", "STYLE", "REGION")


@dataclass(frozen=True, slots=True)
class CueSettings:
    """Настройки положения одной реплики."""

    align: str = ""       # start | center | end | left | right
    line: str = ""        # проценты или номер строки
    position: str = ""    # проценты по горизонтали
    size: str = ""
    vertical: str = ""

    @classmethod
    def parse(cls, text: str) -> CueSettings:
        found = dict(_SETTING.findall(text or ""))
        return cls(
            align=found.get("align", ""),
            line=found.get("line", ""),
            position=found.get("position", ""),
            size=found.get("size", ""),
            vertical=found.get("vertical", ""),
        )

    @property
    def is_empty(self) -> bool:
        return not (self.align or self.line or self.position or self.size or self.vertical)


def _percent(value: str) -> float | None:
    """Число из ``"80%"`` или ``"80%,start"``. ``None``, если это не проценты."""
    if not value:
        return None
    head = value.split(",")[0].strip()
    if not head.endswith("%"):
        return None
    try:
        return float(head[:-1])
    except ValueError:
        return None


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return text


def _escape(text: str) -> str:
    # Амперсанд первым: иначе он испортит уже вставленные мнемоники.
    text = text.replace("&", "&amp;")
    return text.replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# Чтение
# --------------------------------------------------------------------------- #


def read_vtt(text: str) -> SubtitleDocument:
    """Разбирает WebVTT.

    Битые блоки пропускаются: потерять одну реплику лучше, чем отказаться
    открыть файл целиком.
    """
    doc = SubtitleDocument.blank()
    doc.source_format = "vtt"

    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    order = 0

    # Заголовок WEBVTT: если его нет, всё равно пробуем разобрать —
    # файлы в дикой природе часто без него.
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        index = 1

    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue

        # NOTE / STYLE / REGION — не реплики, пропускаем блок до пустой строки.
        if any(line.upper().startswith(word) for word in _BLOCK_KEYWORDS):
            while index < len(lines) and lines[index].strip():
                index += 1
            continue

        match = _TIMING.match(line)
        identifier = ""
        if match is None and index < len(lines):
            # Возможно, это идентификатор, а время — следующей строкой.
            candidate = _TIMING.match(lines[index].strip())
            if candidate is not None:
                identifier = line
                match = candidate
                index += 1
        if match is None:
            continue

        start = _to_ms(match.group(1), match.group(2), match.group(3), match.group(4))
        end = _to_ms(match.group(5), match.group(6), match.group(7), match.group(8))
        settings = CueSettings.parse(match.group(9))

        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index])
            index += 1

        raw = "\n".join(body)
        speaker = ""
        voice = _VOICE.search(raw)
        if voice is not None:
            speaker = voice.group(1).strip()

        event = SubtitleEvent(
            eid=doc.new_eid(),
            start=start,
            end=end,
            text=_body_to_ass(raw, settings),
            name=speaker,
            effect=identifier,  # идентификатор храним, чтобы вернуть при записи
            read_order=order,
        )
        doc.add_event(event)
        order += 1

    if not doc.styles:
        doc.add_style(SubtitleStyle())
    return doc


def _to_ms(hours: str | None, minutes: str, seconds: str, fraction: str) -> int:
    total = int(minutes) * 60 + int(seconds)
    if hours:
        total += int(hours.rstrip(":")) * 3600
    return total * 1000 + int(fraction.ljust(3, "0"))


def _body_to_ass(raw: str, settings: CueSettings) -> str:
    """Текст реплики: разметка в теги ASS, настройки положения — в префикс."""
    text = _VOICE.sub("", raw)
    text = re.sub(r"</v>", "", text, flags=re.IGNORECASE)

    text = re.sub(r"<b>", r"{\\b1}", text, flags=re.IGNORECASE)
    text = re.sub(r"</b>", r"{\\b0}", text, flags=re.IGNORECASE)
    text = re.sub(r"<i>", r"{\\i1}", text, flags=re.IGNORECASE)
    text = re.sub(r"</i>", r"{\\i0}", text, flags=re.IGNORECASE)
    text = re.sub(r"<u>", r"{\\u1}", text, flags=re.IGNORECASE)
    text = re.sub(r"</u>", r"{\\u0}", text, flags=re.IGNORECASE)

    text = _TAG.sub("", text)  # <c.класс>, <ruby> и прочее — не переносим
    text = _unescape(text)
    text = text.replace("\n", "\\N")

    prefix = _settings_to_tags(settings)
    return prefix + text if prefix else text


def _settings_to_tags(settings: CueSettings) -> str:
    """Настройки cue → теги ASS.

    Выравнивание переносится всегда, а положение — только когда оно задано
    явно: ``line``/``position`` по умолчанию означают «как обычно», и
    превращать их в жёсткий ``\\pos`` значило бы прибить гвоздями то, что
    автор оставил на усмотрение плеера.
    """
    if settings.is_empty:
        return ""

    column = {"start": 1, "left": 1, "center": 2, "middle": 2, "end": 3, "right": 3}.get(
        settings.align.lower(), 2
    )

    line = _percent(settings.line)
    position = _percent(settings.position)

    if line is None and position is None:
        if not settings.align:
            return ""
        return f"{{\\an{column}}}"  # снизу, но с нужным выравниванием

    # Проценты WebVTT считаются от кадра; PlayRes у нового документа 1920x1080.
    x = (position if position is not None else 50.0) / 100.0 * 1920
    y = (line if line is not None else 90.0) / 100.0 * 1080
    anchor = {1: 4, 2: 5, 3: 6}[column]  # по вертикали центрируем на точке
    return f"{{\\an{anchor}\\pos({round(x)},{round(y)})}}"


# --------------------------------------------------------------------------- #
# Запись
# --------------------------------------------------------------------------- #


def write_vtt(
    doc: SubtitleDocument,
    *,
    keep_markup: bool = True,
    keep_position: bool = True,
    newline: str = "\n",
) -> str:
    """Сериализует документ в WebVTT.

    Закомментированные события выводятся блоками ``NOTE``: в WebVTT это
    единственный способ сохранить их, не показывая зрителю.
    """
    out: list[str] = ["WEBVTT", ""]

    for event in sorted(doc.events, key=lambda e: (e.start, e.eid)):
        if event.comment:
            note = event.plain.replace("\n", " ") or tr('(пусто)')
            out.append(f"NOTE {note}")
            out.append("")
            continue

        if event.effect:
            out.append(event.effect)  # сохранённый идентификатор реплики

        settings = _tags_to_settings(event, doc) if keep_position else ""
        timing = f"{_format(event.start)} --> {_format(event.end)}"
        out.append(timing + (f" {settings}" if settings else ""))

        body = _ass_to_body(event, keep_markup=keep_markup)
        if event.name:
            body = f"<v {_escape(event.name)}>{body}"
        out.append(body)
        out.append("")

    return newline.join(out)


def _format(ms: int) -> str:
    """``HH:MM:SS.mmm`` — WebVTT требует точку, а не запятую."""
    return timemod.format_vtt(ms)


def _tags_to_settings(event: SubtitleEvent, doc: SubtitleDocument) -> str:
    """Положение из ASS → настройки cue.

    Переносится только то, что заведомо выразимо: выравнивание и положение
    в процентах. Повороты, масштаб и обводка в WebVTT не выражаются вовсе —
    о таких потерях сообщает :func:`lossy_report`.
    """
    alignment = event.alignment_override() or doc.style_for(event).alignment
    column = (alignment - 1) % 3
    align = ("start", "center", "end")[column]

    parts = []
    if align != "center":
        parts.append(f"align:{align}")

    position = event.position()
    if position is not None:
        width, height = doc.script_info.play_res
        if width and height:
            parts.append(f"position:{round(position[0] / width * 100)}%")
            parts.append(f"line:{round(position[1] / height * 100)}%")
    else:
        row = (alignment - 1) // 3
        if row == 2:
            parts.append("line:10%")
        elif row == 1:
            parts.append("line:50%")

    return " ".join(parts)


def _ass_to_body(event: SubtitleEvent, *, keep_markup: bool) -> str:
    """Текст события → тело реплики WebVTT."""
    if not keep_markup:
        return _escape(event.plain).replace("\n", "\n")

    parsed = event.parsed
    out: list[str] = []
    cursor = 0
    for block in parsed.blocks:
        out.append(_escape(event.text[cursor : block.start]))
        for tag in block.tags:
            if tag.name in ("b", "i", "u"):
                on = tag.args_raw.strip() not in ("", "0")
                out.append(f"<{tag.name}>" if on else f"</{tag.name}>")
        cursor = block.end
    out.append(_escape(event.text[cursor:]))

    text = "".join(out)
    return text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")


def lossy_report(doc: SubtitleDocument) -> list[str]:
    """Что теряется при записи в WebVTT."""
    warnings: list[str] = []
    seen: set[str] = set()
    for event in doc.events:
        for block in event.parsed.blocks:
            for tag in block.tags:
                if tag.name in ("b", "i", "u", "an", "pos"):
                    continue
                if tag.name not in seen:
                    seen.add(tag.name)
                    warnings.append(tr('тег \\{0} не выражается в WebVTT').format(tag.name))
    if len(doc.styles) > 1:
        warnings.append(tr('именованные стили ASS в WebVTT не переносятся'))
    return warnings
