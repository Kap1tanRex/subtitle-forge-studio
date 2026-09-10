"""Рабочее состояние реплики: черновик, готово, вопрос — и заметка к ней.

Зачем. В работе на тысячу с лишним реплик нужно знать три вещи: где ты
остановился, что требует второго взгляда и о чём договорились с заказчиком.
Держать это в отдельном файле бессмысленно — он разъедется с субтитрами
после первой же вставленной реплики.

Где живёт в файле. Одним ключом ``SFStudio Notes`` в ``[Script Info]``, тем
же способом, что и реестр акторов: незнакомые ключи этой секции наш парсер,
Aegisub и libass сохраняют дословно и игнорируют. Файл остаётся обычным ASS.

**Почему не поле ``Effect``.** Оно на первый взгляд подходит — стандартное и
обычно пустое, — но, во-первых, иногда занято настоящим эффектом (``Banner``,
``Scroll up``), и затирать чужое нельзя; во-вторых, ``Effect`` не последнее
поле строки ``Dialogue``, и запятая в заметке разорвала бы строку при разборе.

Привязка — по порядковому номеру реплики. Это слабое место, и оно осознанное:
стабильного идентификатора у строки ASS нет вообще. Пока файл правится в этой
программе, номера сходятся; если между сеансами его перекроят в другой,
заметки сместятся. Поэтому здесь только вспомогательные пометки, а всё, что
жалко потерять, — в самом тексте.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

__all__ = [
    "INFO_KEY",
    "STATUSES",
    "STATUS_DONE",
    "STATUS_DRAFT",
    "STATUS_NONE",
    "STATUS_QUESTION",
    "apply_notes",
    "collect_notes",
    "is_known_status",
    "progress",
    "status_title",
]

#: Ключ в ``[Script Info]``. С префиксом — чтобы не столкнуться с полями
#: других программ.
INFO_KEY = "SFStudio Notes"

STATUS_NONE = ""
STATUS_DRAFT = "draft"
STATUS_DONE = "done"
STATUS_QUESTION = "question"

#: Состояния и их подписи. Порядок — как в меню: от «ничего не отмечено» к
#: «требует внимания».
STATUSES: tuple[tuple[str, str], ...] = (
    (STATUS_NONE, "Без пометки"),
    (STATUS_DRAFT, "Черновик"),
    (STATUS_DONE, "Готово"),
    (STATUS_QUESTION, "Вопрос"),
)

_TITLES = dict(STATUSES)


def is_known_status(status: str) -> bool:
    return status in _TITLES


def status_title(status: str) -> str:
    return _TITLES.get(status, status)


def progress(events: Iterable) -> tuple[int, int]:
    """Сколько реплик отмечено готовыми и сколько всего.

    Пустые реплики считаются наравне с остальными: непереведённая строка —
    это тоже работа, которую ещё предстоит сделать.
    """
    done = total = 0
    for event in events:
        total += 1
        if getattr(event, "status", "") == STATUS_DONE:
            done += 1
    return done, total


def questions(events: Iterable) -> list[int]:
    """``eid`` реплик, помеченных вопросом, по порядку документа."""
    return [
        event.eid for event in events
        if getattr(event, "status", "") == STATUS_QUESTION
    ]


# --------------------------------------------------------------------------- #
# Хранение в файле
# --------------------------------------------------------------------------- #


def collect_notes(events: Sequence) -> str:
    """Собирает пометки в JSON для ``[Script Info]``.

    Реплики без пометок пропускаются: у обычного файла ключа не появится
    вовсе, и он останется таким же, каким был.
    """
    items = []
    for index, event in enumerate(events):
        status = getattr(event, "status", "") or ""
        note = getattr(event, "note", "") or ""
        if not status and not note:
            continue
        entry: dict[str, object] = {"i": index}
        if status:
            entry["s"] = status
        if note:
            entry["n"] = note
        items.append(entry)
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def apply_notes(raw: str, events: Sequence) -> int:
    """Раскладывает прочитанные пометки по репликам. Возвращает их число.

    Испорченный или чужой JSON молча пропускается: пометки вспомогательны, и
    отказ открыть файл из-за них был бы несоразмерен.
    """
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return 0
    if not isinstance(items, list):
        return 0

    applied = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("i")
        if not isinstance(index, int) or not 0 <= index < len(events):
            continue
        status = str(item.get("s") or "")
        note = str(item.get("n") or "")
        if status and not is_known_status(status):
            # Состояние из будущей версии: заметку сохраним, а пометку — нет,
            # рисовать её всё равно нечем.
            status = ""
        if not status and not note:
            continue
        events[index].status = status
        events[index].note = note
        applied += 1
    return applied
