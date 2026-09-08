"""Имя говорящего в тексте реплики.

В расшифровках и переводах имя часто пишут прямо в субтитре: «[Иван] Привет»
или отдельной строкой над репликой. Программа знает говорящего и без этого —
он лежит в поле ``Name``, — но зритель поля не видит, а в готовом файле для
чужого плеера метка должна быть текстом.

Задача выглядит простой ровно до второго назначения актора. Первое добавление
делается в одну строку; дальше начинается настоящее:

* сменили говорящего — старую метку надо **убрать**, иначе получится
  «[Иван] [Пётр] Привет»;
* сняли говорящего — метку убрать целиком;
* сменили формат в настройках — прежние метки надо перестроить, а не
  добавить к ним новые;
* формат может быть своим, придуманным человеком, — и его метки тоже надо
  уметь узнавать.

Поэтому здесь не «добавить строку», а разбор: по шаблону строится выражение,
которое узнаёт уже проставленную метку. Узнаёт **осторожно** — имя должно
совпасть с одним из известных акторов. Иначе реплика «[смеётся] Привет»
лишилась бы ремарки при первом же переназначении.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "LABEL_PRESETS",
    "LabelFormat",
    "apply_label",
    "preset",
    "relabel",
    "relabel_events",
    "strip_label",
    "template_for",
]

#: Разметка переноса строки в ASS.
LINE_BREAK = "\\N"

#: Что подставляется в шаблон. Больше подстановок сознательно нет: шаблон
#: должен оставаться понятным без документации.
ACTOR_FIELD = "{actor}"
TEXT_FIELD = "{text}"


@dataclass(frozen=True, slots=True)
class LabelFormat:
    """Готовый вид метки."""

    key: str
    title: str
    template: str
    note: str = ""

    @property
    def sample(self) -> str:
        """Как это будет выглядеть — для показа в настройках."""
        return format_label(self.template, "Иван", "Привет").replace(LINE_BREAK, " ⏎ ")


#: Заготовки. Порядок — от самого частого к редкому.
LABEL_PRESETS: tuple[LabelFormat, ...] = (
    LabelFormat("off", "Не добавлять", TEXT_FIELD,
                "Имя остаётся только в поле говорящего."),
    LabelFormat("brackets_above", "[Имя] над репликой",
                f"[{{actor}}]{LINE_BREAK}{{text}}",
                "Отдельной строкой сверху — так делают в театральных списках."),
    LabelFormat("brackets_inline", "[Имя] в начале строки",
                "[{actor}] {text}",
                "Не занимает строку, но съедает место в кадре."),
    LabelFormat("plain_above", "Имя над репликой без скобок",
                f"{{actor}}{LINE_BREAK}{{text}}"),
    LabelFormat("colon", "Имя с двоеточием",
                "{actor}: {text}",
                "Привычно для расшифровок интервью."),
    LabelFormat("dash", "Имя через тире",
                "{actor} — {text}"),
    LabelFormat("custom", "Свой шаблон", "",
                "Задаётся в настройках. Доступны {actor} и {text}."),
)


def preset(key: str) -> LabelFormat | None:
    return next((p for p in LABEL_PRESETS if p.key == key), None)


def template_for(key: str, custom: str = "") -> str:
    """Шаблон по имени заготовки. Для «своего» берётся заданный человеком.

    Шаблон без ``{text}`` отвергается: он потерял бы саму реплику, оставив от
    неё одно имя. Такую ошибку легко сделать в поле ввода, а заметить — уже
    по испорченному файлу.
    """
    if key == "custom":
        return custom if TEXT_FIELD in custom else TEXT_FIELD
    found = preset(key)
    return found.template if found else TEXT_FIELD


def format_label(template: str, actor: str, text: str) -> str:
    """Подставляет имя и текст в шаблон."""
    return template.replace(ACTOR_FIELD, actor).replace(TEXT_FIELD, text)


def _pattern(template: str, known: Iterable[str] | None) -> re.Pattern[str] | None:
    """Выражение, узнающее метку этого формата.

    Собирается из шаблона: всё, кроме подстановок, экранируется дословно.
    Имя ограничивается списком известных акторов, когда он есть, — так
    ремарка «[смеётся]» не будет принята за метку и не пропадёт.
    """
    if TEXT_FIELD not in template or ACTOR_FIELD not in template:
        return None

    names = [re.escape(name) for name in (known or ()) if name]
    actor_part = f"(?:{'|'.join(names)})" if names else r".+?"

    parts = []
    for piece in re.split(r"(\{actor\}|\{text\})", template):
        if piece == ACTOR_FIELD:
            parts.append(actor_part)
        elif piece == TEXT_FIELD:
            parts.append(r"(?P<text>.*)")
        else:
            parts.append(re.escape(piece))
    try:
        return re.compile("^" + "".join(parts) + "$", re.DOTALL)
    except re.error:
        return None


def strip_label(text: str, known: Iterable[str] | None = None,
                templates: Iterable[str] | None = None) -> str:
    """Убирает метку говорящего, если она есть.

    Проверяются **все** известные форматы, а не только текущий: человек мог
    проставить метки одним видом, а потом сменить настройку, и старые метки
    надо узнать, чтобы не сложить их с новыми.
    """
    candidates = list(templates) if templates is not None else [
        p.template for p in LABEL_PRESETS if p.key != "off"
    ]
    known = list(known) if known is not None else None

    for template in candidates:
        pattern = _pattern(template, known)
        if pattern is None:
            continue
        match = pattern.match(text)
        if match is not None:
            return match.group("text")
    return text


def apply_label(text: str, actor: str, template: str,
                known: Iterable[str] | None = None) -> str:
    """Текст с меткой говорящего. Пустое имя — текст без метки.

    Прежняя метка снимается всегда, даже когда новую ставить не нужно: иначе
    снятие говорящего оставляло бы его имя в тексте навсегда.
    """
    bare = strip_label(text, known)
    actor = actor.strip()
    if not actor or TEXT_FIELD not in template or ACTOR_FIELD not in template:
        return bare
    return format_label(template, actor, bare)


def relabel(text: str, actor: str, key: str, custom: str = "",
            known: Iterable[str] | None = None) -> str:
    """То же, но формат задаётся именем заготовки."""
    return apply_label(text, actor, template_for(key, custom), known)


def relabel_events(
    events,
    template: str,
    known: Iterable[str] | None = None,
    *,
    actor_of=None,
) -> dict[int, str]:
    """Что должно измениться в тексте реплик. ``{eid: новый текст}``.

    Возвращает **намерение**, а не изменённый документ: вызывающий соберёт из
    этого одну команду, и вся правка отменится одним движением. Реплики, где
    ничего не поменялось, в ответ не попадают — незачем плодить пустые шаги
    в истории.

    ``actor_of`` позволяет подставить имя, отличное от записанного в реплике:
    так работает назначение говорящего сразу многим, когда поле ``Name`` ещё
    не изменено.
    """
    known = list(known) if known is not None else None
    changes: dict[int, str] = {}
    for event in events:
        actor = actor_of(event) if actor_of is not None else event.name
        updated = apply_label(event.text, actor or "", template, known)
        if updated != event.text:
            changes[event.eid] = updated
    return changes
