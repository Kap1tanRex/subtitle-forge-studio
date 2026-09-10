"""Чтение и запись TTML (он же DFXP, он же IMSC).

Формат, в котором субтитры принимают Netflix, Apple и Amazon. Это XML, и
устроен он иначе, чем строчные форматы:

* реплика — элемент ``<p>`` с атрибутами ``begin`` и ``end``;
* перенос строки — элемент ``<br/>``, а не символ;
* оформление — атрибуты ``tts:`` либо ссылка на именованный ``<style>``;
* говорящий — ссылка на ``<ttm:agent>``, описанный в шапке;
* положение на экране — через ``<region>``.

Время пишется в четырёх видах сразу: часы с долями секунды, часы с кадрами,
смещение в секундах и смещение в кадрах. Читать надо все — присылают разное,
а частота кадров при этом объявлена в шапке (``ttp:frameRate``), и без неё
кадровая запись разбирается неправильно.

**Про безопасность.** Файл приходит от заказчика, а XML умеет разворачиваться
в гигабайты из десятка строк («billion laughs»). Разбор объявлений сущностей
здесь запрещён: файл с ``<!ENTITY`` отвергается сразу, а не читается «как
получится».

Что теряется при записи и почему — в :func:`lossy_report`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle
from sfstudio.core.time import FpsModel

__all__ = ["TtmlParseError", "lossy_report", "read_ttml", "write_ttml"]

TT_NS = "http://www.w3.org/ns/ttml"
TTP_NS = "http://www.w3.org/ns/ttml#parameter"
TTS_NS = "http://www.w3.org/ns/ttml#styling"
TTM_NS = "http://www.w3.org/ns/ttml#metadata"
XML_NS = "http://www.w3.org/XML/1998/namespace"

#: Частота кадров, если файл о ней молчит. Двадцать четыре — самая частая у
#: тех, кто присылает TTML: кино и потоковые платформы.
DEFAULT_FPS = 24.0


class TtmlParseError(ValueError):
    """Файл не разобрать как TTML."""


@dataclass(frozen=True, slots=True)
class LossWarning:
    """Что потеряется при записи."""

    eid: int
    kind: str
    detail: str


# --------------------------------------------------------------------------- #
# Время
# --------------------------------------------------------------------------- #

_CLOCK = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)(?:[.,](\d{1,3}))?$")
_FRAMES = re.compile(r"^(\d+):([0-5]\d):([0-5]\d):(\d+)$")
_OFFSET = re.compile(r"^(\d+(?:\.\d+)?)(h|m|s|ms|f|t)$")

#: Тиков в секунду, когда файл не сказал иного. Значение из спецификации.
DEFAULT_TICK_RATE = 10_000_000


def parse_time(text: str, fps: float = DEFAULT_FPS,
               tick_rate: int = DEFAULT_TICK_RATE) -> int | None:
    """Время TTML в миллисекундах. ``None`` — не разобрать."""
    value = (text or "").strip()
    if not value:
        return None

    frames = _FRAMES.match(value)
    if frames:
        hours, minutes, seconds, count = (int(x) for x in frames.groups())
        whole = (hours * 3600 + minutes * 60 + seconds) * 1000
        return whole + round(count * 1000 / max(fps, 1e-6))

    clock = _CLOCK.match(value)
    if clock:
        hours, minutes, seconds, fraction = clock.groups()
        millis = int((fraction or "0").ljust(3, "0"))
        return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000 + millis

    offset = _OFFSET.match(value)
    if offset:
        amount, unit = float(offset.group(1)), offset.group(2)
        factors = {
            "h": 3_600_000.0,
            "m": 60_000.0,
            "s": 1000.0,
            "ms": 1.0,
            "f": 1000.0 / max(fps, 1e-6),
            "t": 1000.0 / max(tick_rate, 1),
        }
        return round(amount * factors[unit])
    return None


def format_time(ms: int) -> str:
    """Время в виде ``чч:мм:сс.ммм`` — самый переносимый из вариантов."""
    ms = max(0, int(ms))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


# --------------------------------------------------------------------------- #
# Чтение
# --------------------------------------------------------------------------- #


def read_ttml(text: str) -> SubtitleDocument:
    """Читает TTML в документ."""
    _refuse_entities(text)

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise TtmlParseError(f"не удалось разобрать XML: {exc}") from exc

    if _local(root.tag) != "tt":
        raise TtmlParseError("это не TTML: корневой элемент не <tt>")

    doc = SubtitleDocument.blank()
    for event in list(doc.events):
        doc.remove_event(event.eid)

    fps = _read_fps(root)
    tick_rate = _int_attr(root, f"{{{TTP_NS}}}tickRate", DEFAULT_TICK_RATE)
    agents = _read_agents(root)
    styles = _read_styles(root, doc)

    body = _find(root, "body")
    if body is None:
        return doc

    order = 0
    for paragraph in body.iter(f"{{{TT_NS}}}p"):
        start = parse_time(paragraph.get("begin", ""), fps, tick_rate)
        end = parse_time(paragraph.get("end", ""), fps, tick_rate)
        if end is None:
            duration = parse_time(paragraph.get("dur", ""), fps, tick_rate)
            end = (start or 0) + duration if duration is not None else None
        if start is None or end is None:
            continue  # реплика без времени показу не подлежит

        text_value = _paragraph_text(paragraph)
        if not text_value.strip():
            continue

        event = SubtitleEvent(
            eid=doc.new_eid(),
            start=start,
            end=end,
            text=text_value,
            style=styles.get(paragraph.get("style", ""), "Default"),
            name=agents.get(paragraph.get(f"{{{TTM_NS}}}agent", ""), ""),
            read_order=order,
        )
        doc.add_event(event)
        order += 1

    doc.source_format = "ttml"
    if fps:
        doc.script_info.extra.setdefault("TTML FrameRate", str(int(fps)))
    return doc


def _refuse_entities(text: str) -> None:
    """Отвергает файл с объявлениями сущностей.

    Десять строк с вложенными сущностями разворачиваются в гигабайты и
    съедают память целиком. Субтитрам сущности не нужны ни для чего, поэтому
    честный отказ лучше попытки разобрать «осторожно».
    """
    head = text[:4096].lower()
    if "<!entity" in head or ("<!doctype" in head and "entity" in head):
        raise TtmlParseError(
            "в файле объявлены XML-сущности — такой файл не читается"
        )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(root: ET.Element, name: str) -> ET.Element | None:
    found = root.find(f"{{{TT_NS}}}{name}")
    return found if found is not None else root.find(name)


def _int_attr(element: ET.Element, name: str, default: int) -> int:
    try:
        return int(element.get(name, "")) or default
    except (TypeError, ValueError):
        return default


def _read_fps(root: ET.Element) -> float:
    """Частота кадров из шапки, с учётом множителя.

    Множитель — то, чем описывают 23,976: ``frameRate="24"`` плюс
    ``frameRateMultiplier="1000 1001"``. Без него кадровые тайминги уезжают
    на кадр за каждые сорок секунд.
    """
    rate = float(_int_attr(root, f"{{{TTP_NS}}}frameRate", 0)) or DEFAULT_FPS
    raw = root.get(f"{{{TTP_NS}}}frameRateMultiplier", "").split()
    if len(raw) == 2:
        try:
            numerator, denominator = float(raw[0]), float(raw[1])
            if numerator > 0 and denominator > 0:
                rate = rate * numerator / denominator
        except ValueError:
            pass
    return rate


def _read_agents(root: ET.Element) -> dict[str, str]:
    """Говорящие: идентификатор агента → имя."""
    agents: dict[str, str] = {}
    for agent in root.iter(f"{{{TTM_NS}}}agent"):
        ident = agent.get(f"{{{XML_NS}}}id", "")
        if not ident:
            continue
        name = ""
        for child in agent:
            if _local(child.tag) == "name" and (child.text or "").strip():
                name = child.text.strip()
                break
        agents[ident] = name or ident
    return agents


def _read_styles(root: ET.Element, doc: SubtitleDocument) -> dict[str, str]:
    """Заводит стили документа по описаниям TTML. Возвращает id → имя."""

    mapping: dict[str, str] = {}
    for style in root.iter(f"{{{TT_NS}}}style"):
        ident = style.get(f"{{{XML_NS}}}id", "")
        if not ident:
            continue
        name = ident
        made = SubtitleStyle(name=name)

        family = style.get(f"{{{TTS_NS}}}fontFamily", "")
        if family:
            made.fontname = family.split(",")[0].strip().strip("'\"")
        size = _font_size(style.get(f"{{{TTS_NS}}}fontSize", ""))
        if size:
            made.fontsize = size
        made.italic = style.get(f"{{{TTS_NS}}}fontStyle", "") == "italic"
        made.bold = style.get(f"{{{TTS_NS}}}fontWeight", "") == "bold"
        made.underline = "underline" in style.get(f"{{{TTS_NS}}}textDecoration", "")

        colour = _colour(style.get(f"{{{TTS_NS}}}color", ""))
        if colour is not None:
            made.primary = colour

        # Стиль с тем же именем уже мог прийти из другого места — тогда
        # оставляем первый: перезапись меняла бы вид уже разобранных реплик.
        if name not in doc.styles:
            doc.add_style(made)
        mapping[ident] = name
    if "Default" not in doc.styles:
        doc.add_style(SubtitleStyle(name="Default"))
    return mapping


def _font_size(raw: str) -> float | None:
    """Размер шрифта в пикселях. Проценты и em пропускаем — они относительны."""
    value = raw.strip()
    if not value or value.endswith(("%", "em", "c")):
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)(px)?$", value.split()[0])
    return float(match.group(1)) if match else None


def _colour(raw: str):
    """Цвет TTML: ``#rrggbb``, ``#rrggbbaa`` или название."""
    from sfstudio.core.color import RGBA

    value = raw.strip().lower()
    if not value:
        return None
    named = {
        "white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0),
        "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
        "cyan": (0, 255, 255), "magenta": (255, 0, 255), "gray": (128, 128, 128),
    }
    if value in named:
        return RGBA(*named[value])
    if not value.startswith("#"):
        return None
    digits = value[1:]
    try:
        if len(digits) == 6:
            return RGBA(int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
        if len(digits) == 8:
            return RGBA(
                int(digits[0:2], 16), int(digits[2:4], 16),
                int(digits[4:6], 16), int(digits[6:8], 16),
            )
    except ValueError:
        return None
    return None


def _paragraph_text(paragraph: ET.Element) -> str:
    """Собирает текст реплики с разметкой ASS.

    ``<br/>`` становится ``\\N``, ``<span>`` с курсивом или полужирным —
    парой тегов вокруг своего куска. Вложенность разбирается рекурсивно:
    курсив внутри полужирного встречается в реальных файлах.
    """
    parts: list[str] = []
    _collect(paragraph, parts)
    text = "".join(parts)
    # Пробелы по краям и повторы внутри: в XML перевод строки в исходнике —
    # это форматирование файла, а не текст реплики.
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    return re.sub(r"  +", " ", text).strip()


def _collect(element: ET.Element, out: list[str]) -> None:
    if element.text:
        out.append(element.text)
    for child in element:
        name = _local(child.tag)
        if name == "br":
            out.append("\\N")
        elif name == "span":
            opening, closing = _span_tags(child)
            out.append(opening)
            _collect(child, out)
            out.append(closing)
        else:
            _collect(child, out)
        if child.tail:
            out.append(child.tail)


def _span_tags(span: ET.Element) -> tuple[str, str]:
    opening: list[str] = []
    closing: list[str] = []
    if span.get(f"{{{TTS_NS}}}fontStyle", "") == "italic":
        opening.append(r"{\i1}")
        closing.append(r"{\i0}")
    if span.get(f"{{{TTS_NS}}}fontWeight", "") == "bold":
        opening.append(r"{\b1}")
        closing.append(r"{\b0}")
    if "underline" in span.get(f"{{{TTS_NS}}}textDecoration", ""):
        opening.append(r"{\u1}")
        closing.append(r"{\u0}")
    return "".join(opening), "".join(reversed(closing))


# --------------------------------------------------------------------------- #
# Запись
# --------------------------------------------------------------------------- #

_INLINE = {"i": ("fontStyle", "italic"), "b": ("fontWeight", "bold"),
           "u": ("textDecoration", "underline")}


def lossy_report(doc: SubtitleDocument) -> list[LossWarning]:
    """Что потеряется при записи в TTML.

    TTML богаче SRT, но беднее ASS в главном: у него нет произвольного
    позиционирования пикселями, поворотов и караоке. Молчать об этом нельзя —
    человек отдаёт файл заказчику и должен знать, что тот получит.
    """
    warnings: list[LossWarning] = []
    for event in doc.events:
        if event.comment:
            warnings.append(LossWarning(event.eid, "comment", "закомментированные строки"))
            continue
        for block in event.parsed.blocks:
            for tag in block.tags:
                if tag.name in _INLINE:
                    continue
                warnings.append(LossWarning(event.eid, "tag", f"\\{tag.name}"))
        if event.layer:
            warnings.append(LossWarning(event.eid, "layer", f"слой {event.layer}"))
        if event.effect:
            warnings.append(LossWarning(event.eid, "effect", event.effect))
    return warnings


def write_ttml(
    doc: SubtitleDocument,
    *,
    newline: str = "\n",
    language: str = "ru",
    fps: FpsModel | None = None,
) -> str:
    """Пишет документ в TTML.

    Стили выносятся в шапку и связываются по имени: так файл читается и
    людьми, и программами заказчика, а повторять оформление в каждой реплике
    не приходится.
    """
    rate = round(float(fps.rate)) if fps else 0
    lines: list[str] = ['<?xml version="1.0" encoding="utf-8"?>']

    attrs = [
        f'xmlns="{TT_NS}"',
        f'xmlns:tts="{TTS_NS}"',
        f'xmlns:ttm="{TTM_NS}"',
        f'xmlns:ttp="{TTP_NS}"',
        f'xml:lang="{escape(language)}"',
    ]
    if rate:
        attrs.append(f'ttp:frameRate="{rate}"')
    lines.append("<tt " + " ".join(attrs) + ">")

    lines.append("  <head>")
    lines.append("    <styling>")
    lines.extend("      " + _style_element(style) for style in doc.styles.values())
    lines.append("    </styling>")

    speakers = sorted({event.name for event in doc.events if event.name})
    if speakers:
        lines.append("    <metadata>")
        for index, name in enumerate(speakers, start=1):
            lines.append(f'      <ttm:agent xml:id="a{index}" type="person">')
            lines.append(f"        <ttm:name type=\"full\">{escape(name)}</ttm:name>")
            lines.append("      </ttm:agent>")
        lines.append("    </metadata>")
    lines.append("  </head>")

    agent_ids = {name: f"a{index}" for index, name in enumerate(speakers, start=1)}

    lines.append("  <body>")
    lines.append("    <div>")
    for event in doc.events:
        if event.comment:
            continue  # комментарии в кадре не показываются
        parts = [
            f'begin="{format_time(event.start)}"',
            f'end="{format_time(event.end)}"',
        ]
        if event.style and event.style in doc.styles:
            parts.append(f'style="{escape(event.style)}"')
        if event.name in agent_ids:
            parts.append(f'ttm:agent="{agent_ids[event.name]}"')
        body = _event_markup(event)
        lines.append(f"      <p {' '.join(parts)}>{body}</p>")
    lines.append("    </div>")
    lines.append("  </body>")
    lines.append("</tt>")
    return newline.join(lines) + newline


def _style_element(style: SubtitleStyle) -> str:
    colour = f"#{style.primary.r:02X}{style.primary.g:02X}{style.primary.b:02X}"
    parts = [
        f'xml:id="{escape(style.name)}"',
        f'tts:fontFamily="{escape(style.fontname)}"',
        f'tts:fontSize="{round(style.fontsize)}px"',
        f'tts:color="{colour}"',
    ]
    if style.italic:
        parts.append('tts:fontStyle="italic"')
    if style.bold:
        parts.append('tts:fontWeight="bold"')
    if style.underline:
        parts.append('tts:textDecoration="underline"')
    return "<style " + " ".join(parts) + "/>"


def _event_markup(event: SubtitleEvent) -> str:
    """Текст реплики в разметке TTML.

    Из тегов ASS переносятся курсив, полужирный и подчёркивание — их TTML
    понимает. Остальные отбрасываются: о них уже сказано в отчёте о потерях.

    Разбор идёт по позициям блоков ``{...}`` в исходном тексте, а не по
    «кускам»: у разобранной строки текст один, а блоки лишь указывают, где
    в нём стоят теги.
    """
    parsed = event.parsed
    text = parsed.text
    out: list[str] = []
    open_tags: list[str] = []
    cursor = 0

    for block in parsed.blocks:
        out.append(_piece(text[cursor:block.start]))
        for tag in block.tags:
            pair = _INLINE.get(tag.name)
            if pair is None:
                continue
            attribute, value = pair
            if tag.args_raw.strip() in ("0", ""):
                if open_tags:
                    out.append(open_tags.pop())
            else:
                out.append(f'<span tts:{attribute}="{value}">')
                open_tags.append("</span>")
        cursor = block.end

    out.append(_piece(text[cursor:]))
    while open_tags:
        out.append(open_tags.pop())
    return "".join(out)


def _piece(raw: str) -> str:
    """Кусок обычного текста: экранирование плюс переносы строк."""
    if not raw:
        return ""
    return escape(raw).replace(r"\N", "<br/>").replace(r"\n", "<br/>")
