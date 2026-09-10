"""Выравнивание готового текста по речи.

Самый частый заказ после перевода: «вот текст, вот видео, сделайте тайминг».
Распознавание речи тут не помогает напрямую — оно выдаёт **свой** текст, а
нужен именно присланный, слово в слово.

Как это делается. Распознавание прогоняется ради одного: получить слова с их
временем. Дальше два ряда слов — наш и распознанный — сопоставляются, и
совпадения дают точки привязки. Между привязками время раскладывается
пропорционально длине текста.

**Про честность результата.** Точность упирается в распознавание: на шумном
звуке или плотном диалоге совпадений мало, и промежутки между ними
растягиваются догадкой. Поэтому у каждой реплики считается доля слов,
нашедших опору, и реплики с низкой долей помечаются как требующие проверки.
Молча выдать ровный, но неверный тайминг хуже, чем не выдать ничего: ровный
результат никто не станет перепроверять.

Сравнение слов огрубляется: регистр, пунктуация и «ё» не должны мешать
совпадению. «Привет!» и «привет» — одно слово, и считать иначе значит терять
половину привязок на ровном месте.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

__all__ = [
    "AlignedEvent",
    "AlignmentPlan",
    "align",
    "normalise",
    "words_of",
]

#: Ниже этой доли опор реплика считается сомнительной и помечается.
SHAKY_BELOW = 0.5

#: Наименьшая длительность реплики после выравнивания.
MIN_DURATION_MS = 700

#: Зазор между соседними репликами, если они сошлись вплотную.
MIN_GAP_MS = 40

#: Сколько времени отвести слову, когда опереться совсем не на что.
#: Обычный темп речи — около трёх слов в секунду.
MS_PER_WORD = 330

_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACES = re.compile(r"\s+")
_TAGS = re.compile(r"\{[^}]*\}")


def normalise(word: str) -> str:
    """Слово в виде, годном для сравнения.

    Регистр, пунктуация и «ё» уходят: «Привет!» и «привет» — одно слово.
    """
    return _PUNCT.sub("", word).strip().lower().replace("ё", "е")


def words_of(text: str) -> list[str]:
    """Слова строки для сравнения. Разметка ASS вырезается."""
    plain = _TAGS.sub(" ", text).replace("\\N", " ").replace("\\n", " ")
    return [word for raw in _SPACES.split(plain) if (word := normalise(raw))]


@dataclass(frozen=True, slots=True)
class AlignedEvent:
    """Новое время реплики и то, насколько ему можно верить."""

    eid: int
    start: int
    end: int
    #: Доля слов реплики, нашедших опору в распознанном тексте, от 0 до 1.
    confidence: float
    #: Опор не нашлось — время взято из соседей, то есть угадано.
    guessed: bool = False

    @property
    def shaky(self) -> bool:
        """Стоит ли проверить эту реплику глазами."""
        return self.guessed or self.confidence < SHAKY_BELOW


@dataclass(slots=True)
class AlignmentPlan:
    """Результат выравнивания. Документ не меняется — это только план."""

    changes: list[AlignedEvent] = field(default_factory=list)
    matched_words: int = 0
    total_words: int = 0
    #: Реплики без слов (только теги или пустые) — их не трогали.
    skipped: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.changes)

    @property
    def is_empty(self) -> bool:
        return not self.changes

    @property
    def coverage(self) -> float:
        """Какая доля слов текста нашла опору в распознанном."""
        return self.matched_words / self.total_words if self.total_words else 0.0

    @property
    def shaky(self) -> list[AlignedEvent]:
        """Реплики, время которых стоит проверить глазами."""
        return [change for change in self.changes if change.shaky]

    def as_mapping(self) -> dict[int, tuple[int, int]]:
        return {change.eid: (change.start, change.end) for change in self.changes}

    def summary(self) -> str:
        from sfstudio.core.plural import plural

        if self.is_empty:
            return "Выравнивать нечего"
        parts = [
            "время рассчитано для "
            + plural(len(self.changes), "реплики", "реплик", "реплик"),
            f"опора найдена у {self.coverage * 100:.0f} % слов",
        ]
        shaky = len(self.shaky)
        if shaky:
            parts.append(
                plural(shaky, "реплика требует", "реплики требуют", "реплик требуют")
                + " проверки"
            )
        return " · ".join(parts)


def align(
    events,
    words,
    *,
    min_duration_ms: int = MIN_DURATION_MS,
    min_gap_ms: int = MIN_GAP_MS,
) -> AlignmentPlan:
    """Раскладывает реплики по времени распознанных слов.

    ``events`` — реплики документа по порядку, ``words`` — слова из
    распознавания: объекты с ``start``, ``end`` и ``text``.

    Реплики без слов пропускаются: опереться в них не на что, а раздвигать
    ради них соседей значило бы портить то, что удалось выровнять.
    """
    plan = AlignmentPlan()
    usable = []
    for event in events:
        if words_of(event.text):
            usable.append(event)
        else:
            plan.skipped.append(event.eid)
    if not usable or not words:
        return plan

    # Наш текст одним потоком слов, с пометкой, какой реплике слово принадлежит.
    ours: list[str] = []
    owners: list[int] = []
    counts: list[int] = []
    for index, event in enumerate(usable):
        own = words_of(event.text)
        counts.append(len(own))
        ours.extend(own)
        owners.extend([index] * len(own))

    theirs = [normalise(getattr(word, "text", "")) for word in words]
    plan.total_words = len(ours)
    if not any(theirs):
        return plan

    # autojunk выбрасывает часто встречающиеся элементы. На списках слов это
    # выкидывает как раз самые обычные слова, по которым привязка и держится.
    #
    # Порядок привязок можно не проверять: совпадения идут строго по
    # возрастанию в обоих рядах, а слова распознавания отсортированы по
    # времени. Значит, слово из середины текста не может привязаться к концу
    # дорожки — оно просто не привяжется никуда и попадёт в догадку.
    matcher = SequenceMatcher(None, ours, theirs, autojunk=False)

    spans: list[list[tuple[int, int]]] = [[] for _ in usable]
    hits = [0] * len(usable)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            owner = owners[block.a + offset]
            word = words[block.b + offset]
            spans[owner].append((int(word.start), int(word.end)))
            hits[owner] += 1
            plan.matched_words += 1

    times: list[list] = []
    for index in range(len(usable)):
        if spans[index]:
            start = min(pair[0] for pair in spans[index])
            end = max(pair[1] for pair in spans[index])
            times.append([start, end, False])
        else:
            times.append([-1, -1, True])

    _fill_gaps(times, counts, min_gap_ms)
    _enforce_order(times, min_duration_ms, min_gap_ms)

    for index, event in enumerate(usable):
        start, end, guessed = times[index]
        confidence = 0.0 if guessed else hits[index] / counts[index]
        plan.changes.append(AlignedEvent(event.eid, start, end, confidence, guessed))
    return plan


def _fill_gaps(times: list[list], counts: list[int], min_gap_ms: int) -> None:
    """Расставляет время репликам, которым опор не досталось.

    Между двумя привязками время делится пропорционально числу слов: у
    длинной реплики и доля больше. Это догадка, и она помечена как догадка.

    Догадка не занимает промежуток целиком: перед следующей привязкой
    оставляется зазор. Иначе угаданная реплика упирается в привязанную и
    сдвигает её — то есть догадка портит то, что было известно точно.
    """
    total = len(times)
    index = 0
    while index < total:
        if not times[index][2]:
            index += 1
            continue

        run_start = index
        while index < total and times[index][2]:
            index += 1
        run_end = index  # первая реплика после пропуска

        weights = [max(1, counts[i]) for i in range(run_start, run_end)]
        left = times[run_start - 1][1] if run_start > 0 else 0
        if run_end < total:
            right = max(left, times[run_end][0] - min_gap_ms)
        else:
            # Хвост без опоры справа: раскладываем по обычному темпу речи.
            right = left + sum(weights) * MS_PER_WORD

        span = max(0, right - left)
        share = span / sum(weights) if span else MS_PER_WORD
        cursor = float(left)
        for offset, weight in enumerate(weights):
            width = share * weight
            times[run_start + offset] = [int(cursor), int(cursor + width), True]
            cursor += width


def _enforce_order(
    times: list[list],
    min_duration_ms: int,
    min_gap_ms: int,
) -> None:
    """Приводит времена в порядок: без наездов и слишком коротких реплик.

    Распознавание отдаёт слова с перехлёстом чаще, чем кажется, а плотная
    догадка между двумя близкими привязками вырождается в нулевую
    длительность. Файл с нулевыми и наезжающими репликами не примет ни один
    плеер, поэтому порядок наводится здесь, а не оставляется человеку.
    """
    previous_end = 0
    for index, (start, end, guessed) in enumerate(times):
        fixed_start = max(0, start, previous_end + (min_gap_ms if index else 0))
        fixed_end = max(end, fixed_start + min_duration_ms)
        times[index] = [fixed_start, fixed_end, guessed]
        previous_end = fixed_end
