"""Глоссарий: как переводить термины и имена.

В сериале имя ``Ashley`` — «Эшли», а не «Ашлей», и так во всех двенадцати
сериях. Заказчик присылает список терминов, держать его в голове невозможно,
а искать расхождения глазами дорого.

Здесь список пар «в оригинале → в переводе» и две операции над ним: найти
термины в строке оригинала и проверить, что перевод их учитывает.

**Про словоформы.** Русский текст склоняется: глоссарий говорит «Эшли», а в
реплике «Эшли» может стать «Эшли» (не меняется), зато «Звезда Смерти» —
«Звезду Смерти». Полноценная морфология тянет за собой словарь на десятки
мегабайт ради одной проверки, поэтому здесь сравнение по началу слова: от
термина берётся неизменяемая часть, окончание игнорируется. Это ловит
подавляющее большинство случаев и, что важнее, **не даёт ложных срабатываний
там, где переводчик прав**.

Хранится в проекте, обменивается через CSV — в таком виде глоссарии и
присылают.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

__all__ = ["Glossary", "Term", "stem"]

#: Сколько букв слова считать неизменяемой частью. Четыре — компромисс:
#: короче начинаются случайные совпадения («вода» и «водитель»), длиннее —
#: перестают ловиться короткие термины.
STEM_MIN = 4

#: Какую долю слова брать за основу, если оно длиннее минимума.
STEM_SHARE = 0.75

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def stem(word: str) -> str:
    """Неизменяемая часть слова — то, что не съедает склонение."""
    lowered = word.lower()
    if len(lowered) <= STEM_MIN:
        return lowered
    return lowered[: max(STEM_MIN, int(len(lowered) * STEM_SHARE))]


@dataclass(frozen=True, slots=True)
class Term:
    """Одна запись глоссария."""

    source: str
    target: str
    #: Обязательный термин нарушать нельзя; необязательный — пожелание.
    required: bool = True
    note: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.source.strip()) and bool(self.target.strip())


class Glossary:
    """Список терминов с поиском по тексту."""

    __slots__ = ("_pattern", "_terms")

    def __init__(self, terms: Iterable[Term] = ()) -> None:
        self._terms: list[Term] = [t for t in terms if t.valid]
        self._pattern = self._build_pattern()

    def _build_pattern(self) -> re.Pattern[str] | None:
        """Один разбор на все термины сразу.

        Правило проверки зовут на каждую реплику, а терминов бывает сотня:
        сто отдельных поисков по строке против одного — разница в порядок.
        """
        if not self._terms:
            return None
        parts = sorted(
            (re.escape(term.source.strip()) for term in self._terms),
            key=len,
            reverse=True,  # длинные раньше: «Звезда Смерти» важнее «Звезды»
        )
        return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)

    # -- список -------------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self._terms)

    def __bool__(self) -> bool:
        return bool(self._terms)

    def __iter__(self) -> Iterator[Term]:
        return iter(self._terms)

    @property
    def terms(self) -> list[Term]:
        return list(self._terms)

    def replaced(self, terms: Iterable[Term]) -> Glossary:
        return Glossary(terms)

    # -- поиск --------------------------------------------------------------- #

    def find_in(self, text: str) -> list[Term]:
        """Термины глоссария, встречающиеся в строке оригинала."""
        if self._pattern is None or not text:
            return []
        hits = {match.group(0).lower() for match in self._pattern.finditer(text)}
        if not hits:
            return []
        return [t for t in self._terms if t.source.strip().lower() in hits]

    def missing_in(self, original: str, translation: str) -> list[Term]:
        """Термины, которые есть в оригинале, но, похоже, не переведены.

        «Похоже» — потому что сравнение идёт по началам слов: точного
        совпадения от перевода никто не ждёт, там своё склонение.
        """
        found = self.find_in(original)
        if not found:
            return []

        stems = {stem(word) for word in _WORD.findall(translation)}
        missing = []
        for term in found:
            wanted = [stem(word) for word in _WORD.findall(term.target)]
            if not wanted:
                continue
            # Термин из нескольких слов считается переведённым, если найдены
            # все его слова: «Звезда Смерти» без «Смерти» — это не она.
            if not all(part in stems for part in wanted):
                missing.append(term)
        return missing

    # -- обмен --------------------------------------------------------------- #

    def to_csv(self) -> str:
        """CSV с заголовком: в таком виде глоссарии и передают."""
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
        writer.writerow(["оригинал", "перевод", "обязательно", "заметка"])
        for term in self._terms:
            writer.writerow([
                term.source, term.target, "да" if term.required else "нет", term.note,
            ])
        return buffer.getvalue()

    @classmethod
    def from_csv(cls, raw: str) -> Glossary:
        """Читает CSV. Разделитель определяется сам: присылают и «;», и «,».

        Строка заголовка распознаётся по первому полю и пропускается. Если
        заголовка нет, первая строка считается обычной записью: терять
        термин из-за отсутствия шапки нельзя.
        """
        if not raw.strip():
            return cls()

        sample = raw[:2000]
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        rows = list(csv.reader(io.StringIO(raw), delimiter=delimiter))

        terms: list[Term] = []
        for index, row in enumerate(rows):
            if len(row) < 2:
                continue
            source, target = row[0].strip(), row[1].strip()
            if not source or not target:
                continue
            if index == 0 and source.lower() in ("оригинал", "source", "term", "термин"):
                continue
            required = True
            if len(row) > 2:
                required = row[2].strip().lower() not in ("нет", "no", "false", "0")
            note = row[3].strip() if len(row) > 3 else ""
            terms.append(Term(source, target, required, note))
        return cls(terms)
