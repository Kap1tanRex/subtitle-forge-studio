"""Импорт текста без таймингов: сценарий, расшифровка, готовый перевод.

Заказчик присылает не субтитры, а текст — файл из редактора, где реплики идут
подряд. Открыть его как субтитры нельзя: таймингов там нет вовсе. Отсюда
отдельный путь — прочитать текст, разбить на реплики и выложить их подряд,
чтобы дальше разложить по речи (:mod:`sfstudio.services.alignment`).

**Тайминги здесь заведомо неправильные.** Реплики ставятся подряд, по
условной длительности, — просто чтобы они существовали и шли в нужном
порядке. Настоящее время придёт из выравнивания, и делать вид, что импорт
даёт готовый файл, было бы обманом.

Разбиение отдаётся человеку, а не угадывается молча: в одних файлах реплика —
строка, в других — абзац, и ошибка здесь портит весь дальнейший тайминг.
Программа предлагает то, что похоже на правду, и показывает результат до
того, как что-то попадёт в документ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "MIN_DURATION_MS",
    "ScriptLine",
    "guess_paragraphs",
    "read_script",
    "spread",
]

#: Условная длительность реплики после импорта, до выравнивания.
MIN_DURATION_MS = 2000

#: Условный зазор между репликами.
GAP_MS = 100

#: «ИВАН:», «Иван Петрович:», «NARRATOR:» — но не «Итак:» и не «19:30».
_SPEAKER = re.compile(
    r"^\s*([^\W\d_][\w '’.-]{0,40})\s*:\s+(?=\S)", re.UNICODE
)

#: «ИВАН:» отдельной строкой, без текста после двоеточия.
_HEADER = re.compile(r"^\s*([^\W\d_][\w '’.-]{0,40})\s*:\s*$", re.UNICODE)

#: Служебные строки сценариев: номера страниц, ремарки в скобках целиком.
_NOISE = re.compile(r"^\s*(?:\d{1,4}|\(.*\)|\[.*\])\s*$")


@dataclass(frozen=True, slots=True)
class ScriptLine:
    """Одна будущая реплика."""

    text: str
    #: Имя перед двоеточием, если его просили выделять.
    actor: str = ""


#: Больше этого числа строк в блоке — это не абзац, а группа реплик.
_PARAGRAPH_MAX_LINES = 4


def guess_paragraphs(text: str) -> bool:
    """Похоже ли, что реплика здесь — абзац, а не строка.

    Когда пустые строки разделяют блоки по одной строке, оба разбиения дают
    одно и то же, и угадывать нечего. Вопрос возникает только там, где в
    блоке несколько строк, и тогда важен размер блока: две-три строки — это
    перенос длинной реплики, а десяток — группа реплик, разделённая пустой
    строкой по сценам. Склеить такую группу в одну реплику хуже, чем разбить
    перенос на две.

    Угадывание не окончательное: человек видит переключатель и предпросмотр.
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) < 2:
        return False
    sizes = [len(block.splitlines()) for block in blocks]
    return max(sizes) > 1 and max(sizes) <= _PARAGRAPH_MAX_LINES


def read_script(
    text: str,
    *,
    paragraphs: bool = False,
    speakers: bool = False,
    skip_noise: bool = True,
) -> list[ScriptLine]:
    """Разбирает текст на будущие реплики.

    ``paragraphs`` — реплика это абзац, иначе строка. ``speakers`` — выносить
    имя перед двоеточием в поле актора. ``skip_noise`` — выбрасывать номера
    страниц и ремарки, целиком заключённые в скобки: в субтитры они не идут.
    """
    pieces = re.split(r"\n\s*\n", text) if paragraphs else text.splitlines()

    lines: list[ScriptLine] = []
    pending = ""  # имя, объявленное отдельной строкой, — для следующей реплики
    for piece in pieces:
        body = piece.strip()
        if not body:
            continue
        if skip_noise and _NOISE.match(body):
            continue

        actor = ""
        if speakers:
            # «ИВАН:» отдельной строкой — заголовок реплики, а не реплика.
            # В сценариях для озвучки так пишут чаще, чем в одну строку.
            header = _HEADER.match(body)
            if header is not None:
                pending = header.group(1).strip()
                continue

            match = _SPEAKER.match(body)
            if match is not None:
                actor = match.group(1).strip()
                body = body[match.end():].strip()
            elif pending:
                actor = pending
            pending = ""

        # Внутренние переносы абзаца становятся переносами строки в реплике:
        # так их увидит и человек, и формат.
        body = "\\N".join(part.strip() for part in body.splitlines() if part.strip())
        lines.append(ScriptLine(body, actor))
    return lines


def spread(
    lines,
    *,
    start_ms: int = 0,
    duration_ms: int = MIN_DURATION_MS,
    gap_ms: int = GAP_MS,
) -> list[tuple[int, int]]:
    """Выкладывает реплики подряд. Время условное — до выравнивания.

    Ровный шаг выбран намеренно: любая попытка «умно» посчитать длительность
    по числу знаков создала бы видимость настоящего тайминга. Его здесь нет.
    """
    times: list[tuple[int, int]] = []
    cursor = max(0, start_ms)
    for _ in lines:
        times.append((cursor, cursor + duration_ms))
        cursor += duration_ms + gap_ms
    return times
