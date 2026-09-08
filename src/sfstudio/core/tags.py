"""Парсер и писатель inline-тегов ASS.

Это фундамент WYSIWYG-редактирования: когда пользователь тянет субтитр мышью,
надо заменить ``\\pos`` и **не тронуть больше ничего** — ни порядок остальных
тегов, ни незнакомые теги, ни пробелы в тексте.

Главный принцип: **никогда не терять то, что не понимаем.** Незнакомый тег
сохраняется дословно и переживает цикл чтение → правка → запись без изменений.

Терминология:
* *блок* — фрагмент ``{...}`` в тексте;
* *событийный тег* — действует на всё событие независимо от места (``\\pos``,
  ``\\an``, ``\\move``, ``\\fad``, ``\\clip``, ``\\org``);
* *форматирующий тег* — действует с места появления и дальше (``\\b``, ``\\c``, ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "EVENT_TAGS",
    "ParsedTags",
    "Tag",
    "TagBlock",
    "parse_tags",
    "plain_text",
    "plain_with_map",
    "remove_event_tag",
    "set_event_tag",
    "strip_tags",
]

#: Теги, действующие на событие целиком. Визуальный редактор пишет только их.
EVENT_TAGS: frozenset[str] = frozenset(
    {"pos", "move", "org", "an", "a", "fad", "fade", "clip", "iclip", "q"}
)

# Имя тега: необязательная цифра впереди (\1c, \3a), затем буквы.
# Используется только как запасной вариант — см. _match_tag_name.
_TAG_NAME_RE = re.compile(r"(\d?[a-zA-Z]+)")

#: Все теги ASS/VSFilter. Нужны, чтобы отделить имя тега от аргумента там, где
#: аргумент — текст: у ``\fnTimes New Roman`` жадный разбор дал бы имя
#: ``fnTimes``, и смена шрифта не работала бы вовсе. Разбор идёт по самому
#: длинному совпадению, поэтому ``\fscx`` не путается с ``\fs``, а ``\rnd`` —
#: с ``\r``.
KNOWN_TAGS: frozenset[str] = frozenset({
    "a", "alpha", "an", "b", "be", "blur", "bord", "c", "clip",
    "fad", "fade", "fax", "fay", "fe", "fn", "fr", "frx", "fry", "frz",
    "fs", "fscx", "fscy", "fsp", "i", "iclip", "k", "K", "kf", "ko", "kt",
    "move", "org", "p", "pbo", "pos", "q", "r", "rnd", "rndx", "rndy", "rndz",
    "s", "shad", "t", "u", "xbord", "xshad", "ybord", "yshad",
    "1a", "2a", "3a", "4a", "1c", "2c", "3c", "4c",
})

#: Отсортированы по убыванию длины — так первое совпадение и есть длиннейшее.
_TAG_NAMES_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(KNOWN_TAGS, key=len, reverse=True)
)

#: Теги, аргумент которых — произвольный текст, а не число.
TEXT_ARGUMENT_TAGS: frozenset[str] = frozenset({"fn", "r"})


def _match_tag_name(body: str, i: int) -> tuple[str, int] | None:
    """Имя тега в позиции ``i`` и позиция сразу за ним.

    Сначала пробуем известные имена от длинных к коротким, и только потом —
    жадную регулярку: незнакомые теги из чужих файлов терять нельзя, их надо
    сохранить дословно ради round-trip.
    """
    for name in _TAG_NAMES_BY_LENGTH:
        if body.startswith(name, i):
            return name, i + len(name)
    m = _TAG_NAME_RE.match(body, i)
    return (m.group(1), m.end()) if m else None


@dataclass(slots=True)
class Tag:
    """Один тег внутри блока."""

    name: str
    args_raw: str = ""
    paren: bool = False

    def render(self) -> str:
        if self.paren:
            return f"\\{self.name}({self.args_raw})"
        return f"\\{self.name}{self.args_raw}"

    def numbers(self) -> tuple[float, ...]:
        """Аргументы как числа. Пустой кортеж, если разобрать не удалось.

        Не бросает исключение намеренно: теги в чужих файлах бывают битыми,
        и это не повод ронять открытие документа.
        """
        out: list[float] = []
        for part in self.args_raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(float(part))
            except ValueError:
                return ()
        return tuple(out)


@dataclass(slots=True)
class TagBlock:
    """Блок ``{...}``: позиция в исходной строке и разобранные теги."""

    start: int  # индекс '{'
    end: int  # индекс после '}'
    tags: list[Tag] = field(default_factory=list)
    #: Мусор внутри блока, не начинающийся с '\' — в ASS это комментарий.
    comment: str = ""

    def render(self) -> str:
        return "{" + self.comment + "".join(t.render() for t in self.tags) + "}"

    def find(self, name: str) -> int:
        for i, tag in enumerate(self.tags):
            if tag.name == name:
                return i
        return -1


@dataclass(slots=True)
class ParsedTags:
    """Результат разбора текста события."""

    text: str
    blocks: list[TagBlock] = field(default_factory=list)

    def render(self) -> str:
        """Собирает строку обратно. Без изменений даёт исходный текст побайтово."""
        if not self.blocks:
            return self.text
        out: list[str] = []
        cursor = 0
        for block in self.blocks:
            out.append(self.text[cursor : block.start])
            out.append(block.render())
            cursor = block.end
        out.append(self.text[cursor:])
        return "".join(out)

    def find_tag(self, name: str) -> tuple[int, int]:
        """``(индекс блока, индекс тега)`` первого вхождения или ``(-1, -1)``."""
        for bi, block in enumerate(self.blocks):
            ti = block.find(name)
            if ti >= 0:
                return bi, ti
        return -1, -1

    def get_tag(self, name: str) -> Tag | None:
        bi, ti = self.find_tag(name)
        return self.blocks[bi].tags[ti] if bi >= 0 else None


# --------------------------------------------------------------------------- #
# Разбор
# --------------------------------------------------------------------------- #


def _parse_block_body(body: str) -> tuple[list[Tag], str]:
    """Разбирает содержимое блока (без фигурных скобок)."""
    tags: list[Tag] = []
    comment_parts: list[str] = []
    i = 0
    n = len(body)

    while i < n:
        if body[i] != "\\":
            # Текст вне тега внутри блока — по спецификации ASS это комментарий.
            j = body.find("\\", i)
            j = n if j < 0 else j
            comment_parts.append(body[i:j])
            i = j
            continue

        i += 1  # пропускаем '\'
        matched = _match_tag_name(body, i)
        if matched is None:
            # Одинокий '\' — сохраняем как есть, чтобы не потерять данные.
            comment_parts.append("\\")
            continue

        name, i = matched

        if i < n and body[i] == "(":
            # Аргументы в скобках. Считаем вложенность: \t(\frz30) содержит '\'.
            depth = 0
            j = i
            while j < n:
                if body[j] == "(":
                    depth += 1
                elif body[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j < n:
                tags.append(Tag(name, body[i + 1 : j], paren=True))
                i = j + 1
            else:
                # Незакрытая скобка. Записываем остаток как обычный аргумент
                # вместе с самой скобкой — тогда render() вернёт исходную
                # строку побайтово, а не «починит» её, дописав ')'.
                tags.append(Tag(name, body[i:], paren=False))
                i = n
        else:
            j = body.find("\\", i)
            j = n if j < 0 else j
            tags.append(Tag(name, body[i:j], paren=False))
            i = j

    return tags, "".join(comment_parts)


def parse_tags(text: str) -> ParsedTags:
    """Разбирает текст события на блоки тегов."""
    parsed = ParsedTags(text=text)
    i = 0
    n = len(text)
    while i < n:
        start = text.find("{", i)
        if start < 0:
            break
        end = text.find("}", start + 1)
        if end < 0:
            break  # незакрытый блок — остаток считаем обычным текстом
        body = text[start + 1 : end]
        tags, comment = _parse_block_body(body)
        parsed.blocks.append(TagBlock(start=start, end=end + 1, tags=tags, comment=comment))
        i = end + 1
    return parsed


# --------------------------------------------------------------------------- #
# Правка
# --------------------------------------------------------------------------- #


def _format_args(args: object) -> str:
    """Числа без хвостовых нулей: 100.0 → ``100``, 100.5 → ``100.5``."""
    if isinstance(args, (str, bytes)):
        return args if isinstance(args, str) else args.decode()
    if not isinstance(args, (tuple, list)):
        args = (args,)
    parts: list[str] = []
    for value in args:
        if isinstance(value, float) and value.is_integer():
            parts.append(str(int(value)))
        else:
            parts.append(str(value))
    return ",".join(parts)


def set_event_tag(text: str, name: str, args: object, *, paren: bool | None = None) -> str:
    """Ставит или заменяет событийный тег, сохраняя всё остальное.

    Поведение:
    * тег уже есть → заменяется **на месте**, порядок соседей не меняется;
    * дубликаты в других блоках → удаляются (последний выигрывал бы у libass,
      а мы только что задали значение явно);
    * тега нет → добавляется в конец первого блока;
    * блоков нет → создаётся ``{...}`` в начале строки.

    Идемпотентна: повторный вызов с теми же аргументами не меняет строку.
    """
    if paren is None:
        paren = name in {"pos", "move", "org", "clip", "iclip", "fad", "fade", "t"}

    parsed = parse_tags(text)
    new_tag = Tag(name, _format_args(args), paren=paren)

    bi, ti = parsed.find_tag(name)
    if bi >= 0:
        parsed.blocks[bi].tags[ti] = new_tag
        # Вычищаем дубликаты, начиная со следующего за найденным.
        for b_index, block in enumerate(parsed.blocks):
            keep: list[Tag] = []
            for t_index, tag in enumerate(block.tags):
                is_the_one = b_index == bi and t_index == ti
                if tag.name == name and not is_the_one:
                    continue
                keep.append(tag)
            block.tags = keep
        return parsed.render()

    if parsed.blocks:
        parsed.blocks[0].tags.append(new_tag)
        return parsed.render()

    return "{" + new_tag.render() + "}" + text


def remove_event_tag(text: str, name: str) -> str:
    """Удаляет все вхождения тега. Опустевшие блоки убираются целиком."""
    parsed = parse_tags(text)
    changed = False
    for block in parsed.blocks:
        before = len(block.tags)
        block.tags = [t for t in block.tags if t.name != name]
        changed |= len(block.tags) != before

    if not changed:
        return text

    # Собираем вручную, чтобы выбросить блоки, ставшие пустыми.
    out: list[str] = []
    cursor = 0
    for block in parsed.blocks:
        out.append(parsed.text[cursor : block.start])
        if block.tags or block.comment:
            out.append(block.render())
        cursor = block.end
    out.append(parsed.text[cursor:])
    return "".join(out)


# --------------------------------------------------------------------------- #
# Текст без тегов
# --------------------------------------------------------------------------- #


def plain_with_map(text: str) -> tuple[str, list[int]]:
    """Текст без тегов + карта смещений.

    ``offsets[i]`` — индекс в исходной строке, соответствующий ``plain[i]``.
    Нужна для поиска/замены «по видимому тексту»: нашли совпадение в plain,
    перевели границы обратно в исходные индексы и правим там.

    ``\\N`` и ``\\n`` становятся переводом строки, ``\\h`` — неразрывным пробелом.
    """
    parsed = parse_tags(text)
    spans = [(b.start, b.end) for b in parsed.blocks]

    out: list[str] = []
    offsets: list[int] = []
    i = 0
    n = len(text)
    span_idx = 0

    while i < n:
        if span_idx < len(spans) and i == spans[span_idx][0]:
            i = spans[span_idx][1]
            span_idx += 1
            continue
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in "Nn":
                out.append("\n")
                offsets.append(i)
                i += 2
                continue
            if nxt == "h":
                # Обычный пробел, не U+00A0: plain_text — это проекция для
                # измерений и поиска. NBSP ломал бы поиск по фразе, набранной
                # с обычным пробелом. Настоящий hard space живёт в исходном text.
                out.append(" ")
                offsets.append(i)
                i += 2
                continue
        out.append(ch)
        offsets.append(i)
        i += 1

    return "".join(out), offsets


def plain_text(text: str) -> str:
    """Видимый текст события: без тегов, с настоящими переводами строк."""
    return plain_with_map(text)[0]


def strip_tags(text: str) -> str:
    """Только удаление блоков ``{...}``; ``\\N`` остаётся как есть."""
    parsed = parse_tags(text)
    if not parsed.blocks:
        return text
    out: list[str] = []
    cursor = 0
    for block in parsed.blocks:
        out.append(text[cursor : block.start])
        cursor = block.end
    out.append(text[cursor:])
    return "".join(out)
