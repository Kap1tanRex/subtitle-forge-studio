"""Справочная дорожка: оригинал рядом с переводом.

Переводчику оригинал нужен постоянно и в той же строке, где он пишет перевод.
Держать его во втором окне — значит переключаться на каждой реплике; писать
перевод поверх оригинала — значит потерять оригинал.

Поэтому здесь отдельный набор реплик, **доступный только для чтения**. Он не
часть документа: его не правят, не отменяют, не сохраняют в файл субтитров. У
него одна задача — по времени нашей реплики отдать текст, который в этот
момент звучал в исходнике.

Сопоставление по времени, а не по номерам. Номера расходятся сразу же: одна
реплика оригинала часто становится двумя в переводе и наоборот. Время — то
единственное, что у двух версий общее.

Поиск перекрывающихся строк идёт двоичным поиском по началам. Простой
перебор здесь недопустим: таблица спрашивает оригинал для каждой видимой
строки при каждой перерисовке, и на файле в двадцать тысяч реплик перебор
давал бы миллисекунды на ячейку.

Длинные строки — надпись или песня на весь эпизод — хранятся отдельно и
проверяются перебором. Их единицы, а в общем списке одна такая заставляла бы
двоичный поиск просматривать файл до начала: замерено 48 мкс против 0,4 мкс
на обычном файле.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

__all__ = ["LONG_LINE_MS", "ReferenceLine", "ReferenceTrack"]

#: Начиная с этой длительности строка считается длинной и хранится отдельно.
#: Тридцать секунд — заведомо не реплика: столько держат надпись, титр или
#: строку песни.
LONG_LINE_MS = 30_000


@dataclass(frozen=True, slots=True)
class ReferenceLine:
    """Одна строка оригинала."""

    start: int
    end: int
    text: str
    #: Говорящий, если он был указан в исходном файле.
    name: str = ""

    @property
    def duration(self) -> int:
        return max(0, self.end - self.start)

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and self.end > start


class ReferenceTrack:
    """Набор строк оригинала с быстрым поиском по времени."""

    __slots__ = ("_lines", "_long", "_short", "_span", "_starts", "language", "source")

    def __init__(
        self,
        lines: Iterable[ReferenceLine] = (),
        *,
        source: str = "",
        language: str = "",
    ) -> None:
        #: Откуда взят — имя файла. Показывается человеку, чтобы он видел,
        #: какой именно оригинал сейчас подключён.
        self.source = source
        #: Язык оригинала, если известен. Пока справочно.
        self.language = language

        self._lines: list[ReferenceLine] = sorted(
            (line for line in lines), key=lambda line: (line.start, line.end)
        )
        # Длинные — отдельно: в общем списке одна надпись на весь фильм
        # заставляет двоичный поиск идти до начала файла.
        self._short = [x for x in self._lines if x.duration < LONG_LINE_MS]
        self._long = [x for x in self._lines if x.duration >= LONG_LINE_MS]
        self._starts: list[int] = [line.start for line in self._short]
        # Насколько далеко влево может дотянуться обычная строка. Берём
        # настоящий максимум, а не порог: на живом файле реплики длятся
        # секунды, и перебор сокращается с полутора десятков строк до двух.
        self._span: int = max((x.duration for x in self._short), default=0)

    # -- сведения ------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._lines)

    def __bool__(self) -> bool:
        return bool(self._lines)

    def __iter__(self) -> Iterator[ReferenceLine]:
        return iter(self._lines)

    @property
    def duration(self) -> int:
        """Конец последней строки — по нему видно, тот ли это фильм."""
        return max((line.end for line in self._lines), default=0)

    # -- поиск --------------------------------------------------------------- #

    def overlapping(self, start: int, end: int) -> list[ReferenceLine]:
        """Строки оригинала, пересекающиеся с промежутком, по порядку.

        Границы полуоткрыты: строка, кончающаяся ровно там, где начинается
        наша реплика, не считается пересечением. Иначе у каждой реплики
        в плотном диалоге появлялся бы лишний «сосед» из чужой фразы.
        """
        if not self._lines or end <= start:
            return []

        # Обычные строки короче порога, поэтому влево достаточно отойти на
        # этот порог: то, что началось раньше, до нас уже не дотянется.
        index = bisect_right(self._starts, end - 1)
        edge = start - self._span
        found: list[ReferenceLine] = []
        for i in range(index - 1, -1, -1):
            line = self._short[i]
            if line.start <= edge:
                break
            if line.end > start:
                found.append(line)
        found.reverse()  # шли справа налево, а отдаём по времени

        if not self._long:
            return found

        long_hits = [line for line in self._long if line.overlaps(start, end)]
        if not long_hits:
            return found
        # Сортировка только когда длинные строки в файле есть: в обычном она
        # обошлась бы втрое дороже самого поиска.
        found.extend(long_hits)
        found.sort(key=lambda line: (line.start, line.end))
        return found

    def text_for(self, start: int, end: int, separator: str = " ") -> str:
        """Текст оригинала за промежуток, склеенный в одну строку.

        Несколько строк склеиваются: в колонке таблицы и в поле над
        переводом нужен именно текст, а не разбор, из скольких реплик он
        собрался.
        """
        parts = [line.text for line in self.overlapping(start, end) if line.text]
        return separator.join(parts)

    def line_at(self, ms: int) -> ReferenceLine | None:
        """Строка, звучащая в этот момент. Их может быть несколько — берём
        первую по порядку: остальные видны в колонке целиком."""
        found = self.overlapping(ms, ms + 1)
        return found[0] if found else None

    # -- сборка -------------------------------------------------------------- #

    @classmethod
    def from_events(
        cls,
        events: Iterable,
        *,
        source: str = "",
        language: str = "",
    ) -> ReferenceTrack:
        """Собирает дорожку из реплик прочитанного документа.

        Берётся ``plain`` — текст без разметки: оригинал показывается для
        чтения, и теги оформления в нём только мешают. Комментарии
        пропускаются: в кадре их не было, значит и в оригинале их нет.
        """
        lines = [
            ReferenceLine(
                start=event.start,
                end=event.end,
                text=getattr(event, "plain", "") or "",
                name=getattr(event, "name", "") or "",
            )
            for event in events
            if not getattr(event, "comment", False)
        ]
        return cls(lines, source=source, language=language)

    def coverage(self, events: Iterable) -> float:
        """Какая доля реплик нашла себе оригинал, от 0 до 1.

        По ней сразу видно, что подключили не тот файл: у чужой дорожки
        совпадений почти нет, и человеку об этом надо сказать до того, как он
        переведёт полсерии вслепую.
        """
        total = matched = 0
        for event in events:
            total += 1
            if self.overlapping(event.start, event.end):
                matched += 1
        return matched / total if total else 0.0
