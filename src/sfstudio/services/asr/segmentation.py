"""Из сегментов распознавания — в читаемые реплики.

Движок возвращает речь так, как её услышал: фразами по пять-десять секунд, а
иногда целыми абзацами. Показывать это зрителю нельзя — получится стена текста,
которую не успеть прочитать. Нарезка и есть та работа, которая превращает
расшифровку в субтитры, и она одинакова для любого движка.

Порядок предпочтений при выборе места разреза — от лучшего к худшему:

1. **Пауза между словами.** Если движок дал тайминги слов, самая длинная пауза
   внутри фразы почти всегда совпадает с границей смысла. Это единственный
   способ резать, ничего не зная о языке.
2. **Знак конца предложения** (``.``, ``!``, ``?``, ``…``). Надёжно, но
   встречается не в каждой фразе — расшифровка часто идёт без пунктуации.
3. **Запятая или тире.** Слабее, но лучше, чем середина оборота.
4. **Ближайший пробел к середине.** Последнее средство: смысл при этом не
   учитывается, зато реплика остаётся читаемой по длине.

Разрезать посреди слова нельзя ни при каких условиях, поэтому даже слишком
длинное слово остаётся целым — пусть строка выйдет длиннее нормы, это лучше,
чем «предвари\\Nтельный».

Тайминги при разрезе распределяются **по словам**, если они есть, и
пропорционально длине текста, если нет. Пропорция по символам — грубое
приближение (речь неравномерна), но оно не хуже, чем делить пополам.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from sfstudio.services.asr.base import Segment, WordTiming

__all__ = ["SegmentationRules", "split_segment", "to_events"]

#: Конец предложения. Многоточие отдельно: точка внутри него не считается.
_SENTENCE_END = re.compile(r"[.!?…]+[\"»)\]]*\s")
#: Слабая граница — запятая, точка с запятой, двоеточие, тире.
_WEAK_BREAK = re.compile(r"[,;:—–]\s")


@dataclass(frozen=True, slots=True)
class SegmentationRules:
    """Пороги нарезки.

    Значения по умолчанию соответствуют профилю «Общий» проверок качества:
    расхождение между тем, как мы режем, и тем, на что потом ругается QC,
    сбивало бы с толку.
    """

    max_duration_ms: int = 7000
    min_duration_ms: int = 833
    max_line_length: int = 42
    max_lines: int = 2
    max_cps: float = 17.0
    #: Зазор между соседними репликами, мс. Слипшиеся строки читаются как одна.
    gap_ms: int = 80
    #: Пауза между словами, от которой она считается местом для разреза.
    pause_ms: int = 300
    #: Считать ли пробелы в CPS.
    cps_counts_spaces: bool = False

    @property
    def max_chars(self) -> int:
        """Сколько символов помещается в реплику целиком."""
        return self.max_line_length * self.max_lines

    @staticmethod
    def from_qc_profile(profile) -> SegmentationRules:
        """Берёт пороги из профиля проверок, подставляя разумное вместо ``None``.

        ``None`` в профиле означает «правило выключено». Для нарезки это не
        работает: резать всё равно надо, иначе получится одна реплика на весь
        фильм. Поэтому выключенное правило заменяется мягким значением.
        """
        return SegmentationRules(
            max_duration_ms=profile.max_duration_ms or 10_000,
            min_duration_ms=profile.min_duration_ms or 500,
            max_line_length=profile.max_line_length or 60,
            max_lines=profile.max_lines or 2,
            max_cps=profile.max_cps or 25.0,
            cps_counts_spaces=getattr(profile, "cps_counts_spaces", False),
        )


def _cps(text: str, duration_ms: int, rules: SegmentationRules) -> float:
    if duration_ms <= 0:
        return float("inf")
    counted = text if rules.cps_counts_spaces else text.replace(" ", "")
    return len(counted) * 1000.0 / duration_ms


def _needs_split(segment: Segment, rules: SegmentationRules) -> bool:
    """Надо ли резать. **Скорость чтения тут не при чём.**

    Деление не уменьшает скорость чтения: текст делится пополам, но и время
    делится пополам вместе с ним, а отношение остаётся прежним. Рекурсия по
    этому признаку не сходится и дробит фразу до отдельных слов — проверено на
    настоящей расшифровке: «или как мы там её обозвали» превращалось в шесть
    реплик по одному слову, наложенных друг на друга.

    Быстрая речь лечится не ножницами, а временем: реплику растягивают в
    ближайшую паузу (см. :func:`_relax_fast`). Если паузы нет, значит человек
    говорил быстро, и честнее оставить как есть — пусть об этом скажет
    проверка качества.
    """
    text = segment.cleaned()
    if not text:
        return False
    return (
        segment.duration > rules.max_duration_ms
        or len(text) > rules.max_chars
    )


def split_segment(segment: Segment, rules: SegmentationRules) -> list[Segment]:
    """Режет сегмент, пока каждая часть не станет читаемой.

    Рекурсия ограничена сама собой: каждая часть строго короче исходной по
    тексту, а часть без пробелов уже неделима и возвращается как есть.
    """
    text = segment.cleaned()
    if not text:
        return []
    if not _needs_split(segment, rules):
        return [Segment(segment.start, segment.end, text, segment.confidence,
                        segment.words)]

    if segment.duration < rules.min_duration_ms * 2:
        # Из этого выйдут две реплики короче допустимого. Лучше одна длинная
        # строка, чем два мелькнувших огрызка.
        return [Segment(segment.start, segment.end, text, segment.confidence,
                        segment.words)]

    position = _find_break(text, segment, rules)
    if position is None:
        # Делить нечем: одно длинное слово. Возвращаем как есть — резать
        # посреди слова хуже, чем нарушить норму длины.
        return [Segment(segment.start, segment.end, text, segment.confidence,
                        segment.words)]

    left_text = text[:position].strip()
    right_text = text[position:].strip()
    if not left_text or not right_text:
        return [Segment(segment.start, segment.end, text, segment.confidence,
                        segment.words)]

    boundary = _time_at(segment, text, position)
    left = Segment(segment.start, boundary, left_text, segment.confidence,
                   _words_in(segment, segment.start, boundary))
    right = Segment(
        min(boundary + rules.gap_ms, segment.end), segment.end, right_text,
        segment.confidence, _words_in(segment, boundary, segment.end),
    )

    return split_segment(left, rules) + split_segment(right, rules)


def _find_break(text: str, segment: Segment, rules: SegmentationRules) -> int | None:
    """Позиция разреза в тексте или ``None``, если делить негде."""
    middle = len(text) / 2

    pause = _pause_break(segment, text, rules)
    if pause is not None:
        return pause

    for pattern in (_SENTENCE_END, _WEAK_BREAK):
        best = _closest_match(pattern, text, middle)
        if best is not None:
            return best

    spaces = [m.start() for m in re.finditer(r"\s", text)]
    if not spaces:
        return None
    return min(spaces, key=lambda p: abs(p - middle)) + 1


#: Доля текста у краёв, где разрез не ищется. Граница предложения в самом
#: начале семантически верна, но даёт огрызок в два слова и всю прежнюю
#: длину во второй части — а её всё равно придётся резать снова.
_EDGE_MARGIN = 0.2


def _closest_match(pattern: re.Pattern[str], text: str, middle: float) -> int | None:
    """Ближайшая к середине граница по образцу.

    Ближайшая, а не первая: разрез в начале фразы оставил бы огрызок и всё ту
    же длинную вторую половину. Края отбрасываются по той же причине.
    """
    positions = [m.end() for m in pattern.finditer(text)]
    # Граница на самом конце ничего не делит.
    positions = [p for p in positions if 0 < p < len(text)]
    if not positions:
        return None

    low = len(text) * _EDGE_MARGIN
    high = len(text) * (1 - _EDGE_MARGIN)
    central = [p for p in positions if low <= p <= high]
    # Если подходящих границ нет, отвечаем «нет», а не хватаемся за краевую:
    # вызывающий перейдёт к следующему виду границы, и это даст лучший разрез,
    # чем точка после первого слова.
    return min(central, key=lambda p: abs(p - middle)) if central else None


def _pause_break(
    segment: Segment, text: str, rules: SegmentationRules
) -> int | None:
    """Разрез по самой заметной паузе между словами.

    Работает только когда движок дал тайминги слов; без них пауз мы не знаем.
    """
    if len(segment.words) < 2:
        return None

    middle_ms = (segment.start + segment.end) / 2
    candidates: list[tuple[int, float, int]] = []
    for previous, following in zip(segment.words, segment.words[1:], strict=False):
        pause = following.start - previous.end
        if pause < rules.pause_ms:
            continue
        # Сортируем по длине паузы, а при равных — по близости к середине:
        # так части выходят соразмерными.
        candidates.append((-pause, abs(following.start - middle_ms), following.start))

    if not candidates:
        return None

    boundary_ms = min(candidates)[2]
    spoken = " ".join(
        word.text.strip() for word in segment.words if word.start < boundary_ms
    ).strip()
    if not spoken:
        return None
    # Переводим границу из времени в позицию в тексте: тексты слов и склеенный
    # текст сегмента могут расходиться пробелами, поэтому ищем по длине.
    position = min(len(spoken), len(text))
    return position if 0 < position < len(text) else None


def _time_at(segment: Segment, text: str, position: int) -> int:
    """Момент, соответствующий позиции в тексте."""
    if segment.words:
        spoken_chars = 0
        for word in segment.words:
            spoken_chars += len(word.text.strip()) + 1
            if spoken_chars >= position:
                return max(segment.start + 1, min(word.end, segment.end - 1))

    share = position / len(text) if text else 0.5
    raw = segment.start + int(segment.duration * share)
    return max(segment.start + 1, min(raw, segment.end - 1))


def _words_in(segment: Segment, start: int, end: int) -> tuple[WordTiming, ...]:
    return tuple(w for w in segment.words if start <= w.start < end)


# --------------------------------------------------------------------------- #
# Сборка реплик
# --------------------------------------------------------------------------- #


def wrap_text(text: str, rules: SegmentationRules) -> str:
    """Расставляет переводы строк ``\\N`` по ширине.

    Балансирует строки: две по 30 символов читаются легче, чем 42 и 18.
    """
    words = text.split()
    if not words:
        return ""
    if len(text) <= rules.max_line_length:
        return text

    target = max(1, len(text) // max(1, min(rules.max_lines, 2)))
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        too_long = len(candidate) > rules.max_line_length
        past_target = len(current) >= target and len(lines) + 1 < rules.max_lines
        if current and (too_long or past_target):
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    # Лишние строки сверх нормы склеиваем в последнюю: обрезать текст нельзя.
    if len(lines) > rules.max_lines:
        head = lines[: rules.max_lines - 1]
        head.append(" ".join(lines[rules.max_lines - 1 :]))
        lines = head
    return "\\N".join(lines)


def to_events(
    segments: list[Segment],
    rules: SegmentationRules | None = None,
    *,
    wrap: bool = True,
) -> list[Segment]:
    """Готовые к вставке реплики: нарезанные, разведённые и с переносами.

    Возвращает те же :class:`Segment` — превращение в события документа
    оставлено вызывающему, потому что там же решается, на какую дорожку и с
    каким стилем их класть.
    """
    rules = rules or SegmentationRules()
    result: list[Segment] = []

    for segment in segments:
        for part in split_segment(segment, rules):
            text = wrap_text(part.cleaned(), rules) if wrap else part.cleaned()
            if not text:
                continue
            result.append(
                Segment(part.start, part.end, text, part.confidence, part.words)
            )

    _enforce_gaps(result, rules)
    _relax_fast(result, rules)
    return result


def _relax_fast(segments: list[Segment], rules: SegmentationRules) -> None:
    """Растягивает слишком быстрые реплики в свободное время после них.

    Единственный способ снизить скорость чтения, не трогая текст. Занимаем
    только паузу до следующей реплики и не двигаем её начало: сдвиг съел бы
    её собственное время и переложил проблему на соседа.
    """
    for index, segment in enumerate(segments):
        if _cps(segment.text.replace("\\N", " "), segment.duration, rules) <= rules.max_cps:
            continue

        counted = segment.text.replace("\\N", " ")
        if not rules.cps_counts_spaces:
            counted = counted.replace(" ", "")
        wanted = int(len(counted) * 1000 / rules.max_cps) if rules.max_cps else 0
        limit = (
            segments[index + 1].start - rules.gap_ms
            if index + 1 < len(segments)
            else segment.start + wanted
        )
        segment.end = max(segment.end, min(segment.start + wanted, limit))


def _enforce_gaps(segments: list[Segment], rules: SegmentationRules) -> None:
    """Разводит наложения и слишком короткие реплики.

    Соседи из разных исходных сегментов могут перекрываться — движки не
    гарантируют непересечения. Показывать две реплики одновременно там, где
    речь идёт последовательно, нельзя.
    """
    for previous, current in itertools.pairwise(segments):
        if current.start < previous.end + rules.gap_ms:
            current.start = previous.end + rules.gap_ms
        if current.end <= current.start:
            current.end = current.start + rules.min_duration_ms

    for segment in segments:
        if segment.duration < rules.min_duration_ms:
            segment.end = segment.start + rules.min_duration_ms
