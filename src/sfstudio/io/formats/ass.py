"""Чтение и запись ASS/SSA.

Почему собственный парсер, а не ``pysubs2``: он нормализует документ при чтении —
теряет порядок секций, комментарии и незнакомые ключи заголовка. Для заявленной
гарантии побайтового round-trip этого достаточно, чтобы не подходить. ``pysubs2``
остаётся в зависимостях как резервный ридер для экзотических форматов.

Ключевые тонкости формата:

* Поля в ``[Events]`` и ``[V4+ Styles]`` идут в порядке, объявленном строкой
  ``Format:``. Порядок не фиксирован и в реальных файлах отличается — читать
  по позициям вслепую нельзя.
* Поле ``Text`` всегда последнее и может содержать запятые, поэтому разбор идёт
  с ограничением на число разбиений.
* Время в ASS — сантисекунды. Внутри работаем в миллисекундах, при записи
  округляем: начало вниз, конец вверх, чтобы субтитр не укорачивался.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sfstudio.core import time as timemod
from sfstudio.core.actors import INFO_KEY as ACTORS_KEY
from sfstudio.core.actors import ActorRegistry
from sfstudio.core.color import RGBA
from sfstudio.core.document import ScriptInfo, SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle
from sfstudio.core.tracks import INFO_KEY as TRACKS_KEY
from sfstudio.core.tracks import TrackSet

__all__ = ["AssParseError", "read_ass", "write_ass"]

DEFAULT_STYLE_FORMAT = (
    "Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
    "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
)
DEFAULT_EVENT_FORMAT = "Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"

# Ключи [Script Info], которые мы понимаем; всё прочее уходит в extra дословно.
_KNOWN_INFO_KEYS = {
    "playresx",
    "playresy",
    "wrapstyle",
    "scaledborderandshadow",
    "title",
    "scripttype",
    "ycbcr matrix",
}


class AssParseError(ValueError):
    """Файл не удалось разобрать как ASS."""


@dataclass(slots=True)
class _Layout:
    """Порядок полей из строки ``Format:``, сохраняется ради round-trip."""

    style_format: list[str] = field(default_factory=lambda: _split_format(DEFAULT_STYLE_FORMAT))
    event_format: list[str] = field(default_factory=lambda: _split_format(DEFAULT_EVENT_FORMAT))
    #: Заголовок секции стилей как он был в файле: "V4+ Styles" или "V4 Styles".
    styles_section: str = "V4+ Styles"


def _split_format(line: str) -> list[str]:
    return [p.strip() for p in line.split(",")]


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return default


def _to_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return default


def _to_bool(value: str) -> bool:
    """В ASS «истина» — это -1, но в дикой природе встречается и 1."""
    return _to_int(value) != 0


def _color(value: str, fallback: RGBA) -> RGBA:
    try:
        return RGBA.from_ass(value)
    except ValueError:
        return fallback


# --------------------------------------------------------------------------- #
# Чтение
# --------------------------------------------------------------------------- #


def read_ass(text: str) -> SubtitleDocument:
    """Разбирает содержимое ASS/SSA-файла."""
    doc = SubtitleDocument()
    doc.source_format = "ass"
    layout = _Layout()

    # Порядок секций сохраняем, чтобы запись дала тот же файл.
    section = ""
    seen_sections: list[str] = []
    read_order = 0
    seen_info: set[str] = set()
    saw_events_format = False
    saw_styles_format = False

    text = text.lstrip("﻿")
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            if section not in seen_sections:
                seen_sections.append(section)
            if section.lower().endswith("styles"):
                layout.styles_section = section
            continue

        low = section.lower()

        if low == "script info":
            _read_info_line(doc.script_info, stripped, seen_info)
        elif low.endswith("styles"):
            key, _, rest = stripped.partition(":")
            key = key.strip().lower()
            if key == "format":
                layout.style_format = _split_format(rest)
                saw_styles_format = True
            elif key == "style":
                style = _read_style(rest, layout.style_format)
                if style is not None:
                    doc.add_style(style)
        elif low == "events":
            key, _, rest = stripped.partition(":")
            key = key.strip().lower()
            if key == "format":
                layout.event_format = _split_format(rest)
                saw_events_format = True
            elif key in ("dialogue", "comment"):
                event = _read_event(
                    doc, rest, layout.event_format, comment=(key == "comment"), order=read_order
                )
                if event is not None:
                    read_order += 1
                    doc.add_event(event)
        # Прочие секции (Fonts, Graphics, Aegisub Project Garbage) в прототипе
        # пропускаются; модуль вложений подключается на этапе 6.

    if not seen_sections:
        raise AssParseError("не найдено ни одной секции — файл не похож на ASS")
    if not saw_events_format and not doc.events:
        raise AssParseError("секция [Events] отсутствует или пуста")
    _ = saw_styles_format

    if not doc.styles:
        doc.add_style(SubtitleStyle())

    _normalize_play_res(doc, seen_info)
    _read_registries(doc)
    doc._layout = layout  # type: ignore[attr-defined]  # для writer
    return doc


def _tracks_worth_saving(doc: SubtitleDocument) -> bool:
    """Есть ли в дорожках то, что не восстановится из самих реплик.

    Единственная безымянная дорожка со слоем 0 — это состояние любого обычного
    файла субтитров, и записывать его отдельным полем незачем: при следующем
    открытии оно построится из событий.
    """
    tracks = doc.tracks.subtitles
    if len(tracks) != 1:
        return bool(tracks)
    only = tracks[0]
    return bool(
        only.layer != 0 or only.name or not only.visible or only.locked or only.height
    )


def _read_registries(doc: SubtitleDocument) -> None:
    """Достаёт акторов и дорожки из ``[Script Info]``.

    Ключи **удаляются** из ``extra`` после разбора. Иначе они уехали бы в файл
    дважды: один раз дословно из ``extra``, второй — из реестров при записи.
    """
    raw_actors = doc.script_info.extra.pop(ACTORS_KEY, None)
    if raw_actors:
        doc.actors = ActorRegistry.from_json(raw_actors)

    raw_tracks = doc.script_info.extra.pop(TRACKS_KEY, None)
    if raw_tracks:
        parsed = TrackSet.from_json(raw_tracks)
        if parsed.subtitles:
            doc.tracks = parsed

    # Слои из самих реплик — источник правды: описание дорожек могло не
    # сохраниться, а реплики со своими layer никуда не делись.
    doc.sync_tracks()


def _read_info_line(info: ScriptInfo, line: str, seen: set[str]) -> None:
    if line.startswith(";"):
        info.comments.append((len(info.extra), line))
        return
    key, sep, value = line.partition(":")
    if not sep:
        return
    key = key.strip()
    value = value.strip()
    low = key.lower()
    seen.add(low)

    if low == "playresx":
        info.play_res_x = _to_int(value, 1920)
    elif low == "playresy":
        info.play_res_y = _to_int(value, 1080)
    elif low == "wrapstyle":
        info.wrap_style = _to_int(value)
    elif low == "scaledborderandshadow":
        info.scaled_border_and_shadow = value.strip().lower() in ("yes", "1", "true")
    elif low == "title":
        info.title = value
    elif low == "scripttype":
        info.script_type = value
    elif low == "ycbcr matrix":
        info.ycbcr_matrix = value
    else:
        info.extra[key] = value


def _normalize_play_res(doc: SubtitleDocument, seen: set[str]) -> None:
    """Отсутствующий PlayRes подставляем, но помечаем.

    Флаг ставится по факту отсутствия ключа в файле, а не по значению поля:
    значение по умолчанию в ``ScriptInfo`` — те же 1920x1080, и отличить по нему
    «ключа не было» от «ключ был и равен 1920» невозможно.

    Различать важно: без PlayRes координаты ``\\pos`` не совпадут с тем, что
    увидит зритель в своём плеере, и об этом нужно предупредить в статус-баре.
    """
    info = doc.script_info
    missing = "playresx" not in seen or "playresy" not in seen
    if missing or info.play_res_x <= 0 or info.play_res_y <= 0:
        if info.play_res_x <= 0:
            info.play_res_x = 1920
        if info.play_res_y <= 0:
            info.play_res_y = 1080
        info.play_res_inferred = True


def _read_style(rest: str, fields: list[str]) -> SubtitleStyle | None:
    parts = rest.split(",", len(fields) - 1)
    if len(parts) < 2:
        return None
    data = dict(zip(fields, parts, strict=False))
    style = SubtitleStyle(name=data.get("Name", "Default").strip())

    style.fontname = data.get("Fontname", style.fontname).strip()
    style.fontsize = _to_float(data.get("Fontsize", ""), style.fontsize)
    style.primary = _color(data.get("PrimaryColour", ""), style.primary)
    style.secondary = _color(data.get("SecondaryColour", ""), style.secondary)
    # SSA v4 называет это поле TertiaryColour.
    style.outline_color = _color(
        data.get("OutlineColour") or data.get("TertiaryColour") or "", style.outline_color
    )
    style.back_color = _color(data.get("BackColour", ""), style.back_color)
    style.bold = _to_bool(data.get("Bold", "0"))
    style.italic = _to_bool(data.get("Italic", "0"))
    style.underline = _to_bool(data.get("Underline", "0"))
    style.strikeout = _to_bool(data.get("StrikeOut", "0"))
    style.scale_x = _to_float(data.get("ScaleX", ""), style.scale_x)
    style.scale_y = _to_float(data.get("ScaleY", ""), style.scale_y)
    style.spacing = _to_float(data.get("Spacing", ""), style.spacing)
    style.angle = _to_float(data.get("Angle", ""), style.angle)
    style.border_style = _to_int(data.get("BorderStyle", "1"), 1)
    style.outline = _to_float(data.get("Outline", ""), style.outline)
    style.shadow = _to_float(data.get("Shadow", ""), style.shadow)
    style.alignment = _to_int(data.get("Alignment", "2"), 2)
    if not 1 <= style.alignment <= 9:
        style.alignment = 2
    style.margin_l = _to_int(data.get("MarginL", "20"), 20)
    style.margin_r = _to_int(data.get("MarginR", "20"), 20)
    style.margin_v = _to_int(data.get("MarginV", "20"), 20)
    style.encoding = _to_int(data.get("Encoding", "1"), 1)
    return style


def _read_event(
    doc: SubtitleDocument, rest: str, fields: list[str], *, comment: bool, order: int
) -> SubtitleEvent | None:
    # Text — последнее поле и содержит запятые, поэтому ограничиваем разбиение.
    parts = rest.split(",", len(fields) - 1)
    if len(parts) < len(fields):
        parts += [""] * (len(fields) - len(parts))
    data = dict(zip(fields, parts, strict=False))

    start = timemod.parse_timecode(data.get("Start", ""))
    end = timemod.parse_timecode(data.get("End", ""))
    if start is None or end is None:
        return None  # битая строка — пропускаем, не роняя весь файл

    return SubtitleEvent(
        eid=doc.new_eid(),
        start=start,
        end=end,
        text=data.get("Text", ""),
        style=data.get("Style", "Default").strip(),
        layer=_to_int(data.get("Layer") or data.get("Marked") or "0"),
        name=data.get("Name", "").strip(),
        margin_l=_to_int(data.get("MarginL", "0")),
        margin_r=_to_int(data.get("MarginR", "0")),
        margin_v=_to_int(data.get("MarginV", "0")),
        effect=data.get("Effect", "").strip(),
        comment=comment,
        read_order=order,
    )


# --------------------------------------------------------------------------- #
# Запись
# --------------------------------------------------------------------------- #


def write_ass(
    doc: SubtitleDocument,
    *,
    newline: str = "\n",
    events: Iterable[SubtitleEvent] | None = None,
) -> str:
    """Сериализует документ в текст ASS.

    ``events`` позволяет записать не весь документ, а только заданные реплики
    — заголовок, стили и реестры при этом пишутся целиком. Нужно это не для
    сохранения файла (там всегда всё), а для отрисовки кадра: libass незачем
    знать про реплики, до которых зрителю ещё десять минут, а пересборка
    полного трека на каждую правку стоила 79 мс на документе в двадцать
    тысяч строк.
    """
    layout: _Layout = getattr(doc, "_layout", None) or _Layout()
    out: list[str] = []

    # [Script Info]
    out.append("[Script Info]")
    info = doc.script_info
    if info.title:
        out.append(f"Title: {info.title}")
    out.append(f"ScriptType: {info.script_type}")
    out.append(f"WrapStyle: {info.wrap_style}")
    out.append(
        "ScaledBorderAndShadow: " + ("yes" if info.scaled_border_and_shadow else "no")
    )
    out.append(f"YCbCr Matrix: {info.ycbcr_matrix}")
    out.append(f"PlayResX: {info.play_res_x}")
    out.append(f"PlayResY: {info.play_res_y}")
    out.extend(f"{key}: {value}" for key, value in info.extra.items())

    # Реестры пишем, только когда в них есть непустое содержимое: файл с одной
    # дорожкой и без акторов — обычные субтитры, и обрастать нашими полями он
    # не должен.
    if len(doc.actors):
        out.append(f"{ACTORS_KEY}: {doc.actors.to_json()}")
    if _tracks_worth_saving(doc):
        out.append(f"{TRACKS_KEY}: {doc.tracks.to_json()}")

    # [V4+ Styles]
    out.append("")
    out.append(f"[{layout.styles_section}]")
    out.append("Format: " + ", ".join(layout.style_format))
    out.extend(
        "Style: " + _format_style(style, layout.style_format)
        for style in doc.styles.values()
    )

    # [Events]
    out.append("")
    out.append("[Events]")
    out.append("Format: " + ", ".join(layout.event_format))
    for event in (doc.events if events is None else events):
        kind = "Comment" if event.comment else "Dialogue"
        out.append(f"{kind}: " + _format_event(event, layout.event_format))

    return newline.join(out) + newline


def _format_style(style: SubtitleStyle, fields: list[str]) -> str:
    values = {
        "Name": style.name,
        "Fontname": style.fontname,
        "Fontsize": _num(style.fontsize),
        "PrimaryColour": style.primary.to_ass(),
        "SecondaryColour": style.secondary.to_ass(),
        "OutlineColour": style.outline_color.to_ass(),
        "TertiaryColour": style.outline_color.to_ass(),
        "BackColour": style.back_color.to_ass(),
        "Bold": "-1" if style.bold else "0",
        "Italic": "-1" if style.italic else "0",
        "Underline": "-1" if style.underline else "0",
        "StrikeOut": "-1" if style.strikeout else "0",
        "ScaleX": _num(style.scale_x),
        "ScaleY": _num(style.scale_y),
        "Spacing": _num(style.spacing),
        "Angle": _num(style.angle),
        "BorderStyle": str(style.border_style),
        "Outline": _num(style.outline),
        "Shadow": _num(style.shadow),
        "Alignment": str(style.alignment),
        "MarginL": str(style.margin_l),
        "MarginR": str(style.margin_r),
        "MarginV": str(style.margin_v),
        "Encoding": str(style.encoding),
    }
    return ",".join(values.get(name, "") for name in fields)


def _format_event(event: SubtitleEvent, fields: list[str]) -> str:
    start_ms, end_ms = timemod.round_for_ass(event.start, event.end)
    values = {
        "Layer": str(event.layer),
        "Marked": f"Marked={event.layer}",
        "Start": timemod.format_ass(start_ms),
        "End": timemod.format_ass(end_ms),
        "Style": event.style,
        "Name": event.name,
        "Actor": event.name,
        "MarginL": f"{event.margin_l:04d}" if event.margin_l else "0",
        "MarginR": f"{event.margin_r:04d}" if event.margin_r else "0",
        "MarginV": f"{event.margin_v:04d}" if event.margin_v else "0",
        "Effect": event.effect,
        "Text": event.text,
    }
    return ",".join(values.get(name, "") for name in fields)


def _num(value: float) -> str:
    """Целые печатаем без ``.0`` — так делает Aegisub, и так ждут golden-файлы."""
    return str(int(value)) if float(value).is_integer() else str(value)
