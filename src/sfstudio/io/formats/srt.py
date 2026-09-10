"""Чтение и запись SubRip (.srt).

SRT беднее ASS, поэтому экспорт всегда с потерями. Потери не замалчиваются:
:func:`lossy_report` перечисляет, что именно будет отброшено, — диалог экспорта
показывает этот список **до** записи файла.

Разметка, которую понимают плееры (VLC, mpv) и которую мы поддерживаем:
``<b>``, ``<i>``, ``<u>``, ``<font color=...>`` и позиционирование ``{\\anN}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sfstudio.app.i18n import tr
from sfstudio.core import tags as tagmod
from sfstudio.core import time as timemod
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle

__all__ = ["LossWarning", "lossy_report", "read_srt", "write_srt"]

_TIMING_RE = re.compile(
    r"(\d{1,3}:\d{1,2}:\d{1,2}[.,]\d{1,3})\s*-->\s*(\d{1,3}:\d{1,2}:\d{1,2}[.,]\d{1,3})"
)
_TAG_RE = re.compile(r"</?(?:b|i|u|font)(?:\s[^>]*)?>", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LossWarning:
    """Что теряется при экспорте в SRT."""

    eid: int
    kind: str
    detail: str


def read_srt(text: str) -> SubtitleDocument:
    """Разбирает SRT, пропуская битые блоки вместо падения.

    Нумерация блоков игнорируется: в реальных файлах она сбита ровно так же
    часто, как правильна, и опираться на неё нельзя.
    """
    doc = SubtitleDocument()
    doc.source_format = "srt"
    doc.add_style(SubtitleStyle())

    lines = text.lstrip("﻿").splitlines()
    i = 0
    n = len(lines)
    order = 0

    while i < n:
        match = _TIMING_RE.search(lines[i])
        if match is None:
            i += 1
            continue

        start = timemod.parse_timecode(match.group(1))
        end = timemod.parse_timecode(match.group(2))
        i += 1

        body: list[str] = []
        while i < n and lines[i].strip():
            body.append(lines[i].rstrip("\r"))
            i += 1

        if start is None or end is None:
            continue

        # Номер блока попал бы в текст предыдущего события — убираем его.
        if body and body[-1].strip().isdigit() and i < n:
            body.pop()

        doc.add_event(
            SubtitleEvent(
                eid=doc.new_eid(),
                start=start,
                end=end,
                text=_srt_to_ass("\\N".join(body)),
                read_order=order,
            )
        )
        order += 1

    return doc


def _srt_to_ass(text: str) -> str:
    """HTML-разметка SRT → inline-теги ASS."""
    text = re.sub(r"<b>", r"{\\b1}", text, flags=re.IGNORECASE)
    text = re.sub(r"</b>", r"{\\b0}", text, flags=re.IGNORECASE)
    text = re.sub(r"<i>", r"{\\i1}", text, flags=re.IGNORECASE)
    text = re.sub(r"</i>", r"{\\i0}", text, flags=re.IGNORECASE)
    text = re.sub(r"<u>", r"{\\u1}", text, flags=re.IGNORECASE)
    text = re.sub(r"</u>", r"{\\u0}", text, flags=re.IGNORECASE)
    return _TAG_RE.sub("", text)


def _ass_to_srt(text: str, *, keep_markup: bool) -> str:
    """Inline-теги ASS → HTML SRT, всё остальное отбрасывается."""
    if not keep_markup:
        return tagmod.plain_text(text)

    parsed = tagmod.parse_tags(text)
    out: list[str] = []
    cursor = 0
    for block in parsed.blocks:
        out.append(text[cursor : block.start])
        for tag in block.tags:
            name = tag.name
            if name in ("b", "i", "u"):
                on = tag.args_raw.strip() not in ("", "0")
                out.append(f"<{name}>" if on else f"</{name}>")
        cursor = block.end
    out.append(text[cursor:])

    joined = "".join(out)
    return joined.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")


def lossy_report(doc: SubtitleDocument) -> list[LossWarning]:
    """Что именно будет потеряно при записи в SRT."""
    warnings: list[LossWarning] = []
    for event in doc.events:
        if event.comment:
            warnings.append(LossWarning(event.eid, "comment", tr('закомментированные строки')))
            continue
        for block in event.parsed.blocks:
            for tag in block.tags:
                name = tag.name
                if name in ("b", "i", "u"):
                    continue
                if name == "an":
                    continue  # переносится как {\anN}
                warnings.append(LossWarning(event.eid, "tag", f"\\{name}"))
        if event.layer:
            warnings.append(LossWarning(event.eid, "layer", tr('слой {0}').format(event.layer)))
        if event.effect:
            warnings.append(LossWarning(event.eid, "effect", event.effect))
    return warnings


def write_srt(
    doc: SubtitleDocument,
    *,
    keep_markup: bool = True,
    keep_alignment: bool = True,
    newline: str = "\n",
) -> str:
    """Сериализует документ в SRT.

    Закомментированные события не экспортируются — в SRT нет способа их выразить.
    """
    out: list[str] = []
    number = 0

    for event in sorted(doc.events, key=lambda e: (e.start, e.eid)):
        if event.comment:
            continue
        number += 1
        body = _ass_to_srt(event.text, keep_markup=keep_markup)

        if keep_alignment:
            an = event.alignment_override()
            if an is None:
                style = doc.style_for(event)
                an = style.alignment if style.alignment != 2 else None
            if an is not None and an != 2:
                body = f"{{\\an{an}}}{body}"

        out.append(str(number))
        out.append(f"{timemod.format_srt(event.start)} --> {timemod.format_srt(event.end)}")
        out.append(body)
        out.append("")

    return newline.join(out)
