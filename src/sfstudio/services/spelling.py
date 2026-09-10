"""Проверка орфографии.

Опечатка в субтитре — брак, за который снимают деньги, а проверять текст было
нечем: приходилось копировать реплики в текстовый редактор.

Словари — формата Hunspell, тех же, что в LibreOffice и Firefox. Читает их
``spylls`` — порт Hunspell на чистом Python: ни компиляции, ни внешних
библиотек, а значит и в собранном exe работает без плясок. Русский и
английский идут в комплекте с ней самой.

**Что важно про скорость.** Проверка одного слова стоит около 45 микросекунд —
для поля правки это ничто, а для таблицы на двадцать тысяч реплик уже секунды.
Поэтому здесь есть кэш «слово → знакомо ли», и он же делает повторные прогоны
почти бесплатными: разных слов в фильме тысячи, а не десятки тысяч.

Подсказки замен стоят от 150 до 300 миллисекунд на слово. Это осознанно
делается только по требованию человека — по правому щелчку, — и никогда на
каждое нажатие клавиши.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "LANGUAGES",
    "Misspelling",
    "SpellChecker",
    "available",
]

#: Языки, для которых словарь есть без загрузки. Ключ — как в настройках.
LANGUAGES: tuple[tuple[str, str], ...] = (
    ("", "Не проверять"),
    ("ru", "Русский"),
    ("en_US", "Английский"),
)

#: Слово: буквы, дефис и апостроф внутри. Цифры не берём — «2024» и «5-й»
#: не ошибки, а проверять их бессмысленно.
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.UNICODE)

#: Разметка ASS: ``{\pos(10,20)}``. Внутрь фигурных скобок не заглядываем —
#: это команды оформления, а не текст.
_TAGS = re.compile(r"\{[^}]*\}")


@dataclass(frozen=True, slots=True)
class Misspelling:
    """Незнакомое слово и где оно в строке."""

    word: str
    start: int
    end: int


def available() -> bool:
    """Установлена ли библиотека проверки."""
    try:
        import spylls.hunspell  # noqa: F401
    except ImportError:
        return False
    return True


class SpellChecker:
    """Проверка текста по словарю выбранного языка.

    Словарь загружается лениво, при первом обращении: полсекунды на чтение
    не должны задерживать открытие окна тому, кто проверкой не пользуется.
    """

    __slots__ = ("_cache", "_dictionary", "_extra", "_failed", "language")

    def __init__(self, language: str = "", extra: set[str] | None = None) -> None:
        self.language = language
        self._dictionary = None
        self._failed = False
        self._cache: dict[str, bool] = {}
        #: Свой словарь: имена, термины, названия. Хранится в проекте.
        self._extra: set[str] = {w.lower() for w in (extra or ())}

    # -- состояние ----------------------------------------------------------- #

    @property
    def enabled(self) -> bool:
        return bool(self.language) and not self._failed

    @property
    def extra_words(self) -> set[str]:
        return set(self._extra)

    def set_language(self, language: str) -> None:
        if language == self.language:
            return
        self.language = language
        self._dictionary = None
        self._failed = False
        self._cache.clear()

    def add_word(self, word: str) -> None:
        """Заносит слово в свой словарь — с этого момента оно знакомо."""
        cleaned = word.strip()
        if not cleaned:
            return
        self._extra.add(cleaned.lower())
        self._cache[cleaned.lower()] = True

    def set_extra_words(self, words) -> None:
        self._extra = {str(w).strip().lower() for w in words if str(w).strip()}
        self._cache.clear()

    # -- проверка ------------------------------------------------------------ #

    def _load(self):
        if self._dictionary is not None or self._failed or not self.language:
            return self._dictionary
        try:
            from spylls.hunspell import Dictionary

            self._dictionary = Dictionary.from_files(self.language)
        except Exception:
            # Нет библиотеки, нет словаря, битый файл — проверка просто не
            # работает. Ронять из-за этого редактор нельзя.
            self._failed = True
            self._dictionary = None
        return self._dictionary

    def known(self, word: str) -> bool:
        """Знакомо ли слово. Незагруженный словарь считает знакомым всё."""
        lowered = word.lower()
        if lowered in self._extra:
            return True
        cached = self._cache.get(lowered)
        if cached is not None:
            return cached

        dictionary = self._load()
        if dictionary is None:
            return True
        try:
            ok = bool(dictionary.lookup(word))
            if not ok and word != lowered:
                # «Привет» в начале строки и «привет» в середине — одно слово.
                # Hunspell учитывает регистр, и без этого каждое слово после
                # точки становилось бы ошибкой.
                ok = bool(dictionary.lookup(lowered))
        except Exception:
            self._failed = True
            return True
        self._cache[lowered] = ok
        return ok

    def check(self, text: str) -> list[Misspelling]:
        """Незнакомые слова строки, по порядку.

        Разметка ASS вырезается вместе с позициями: подчёркивать половину
        тега как «ошибку» — верный способ отучить человека смотреть на
        подчёркивания вообще.
        """
        if not self.enabled or not text:
            return []

        masked = _TAGS.sub(lambda m: " " * len(m.group(0)), text)
        found: list[Misspelling] = []
        for match in _WORD.finditer(masked):
            word = match.group(0)
            if len(word) < 2:
                continue  # «я», «в» и инициалы проверять нечем
            if not self.known(word):
                found.append(Misspelling(word, match.start(), match.end()))
        return found

    def suggest(self, word: str, limit: int = 7) -> list[str]:
        """Замены для слова. Дорого — зовут только по правому щелчку."""
        dictionary = self._load()
        if dictionary is None:
            return []
        try:
            out: list[str] = []
            for candidate in dictionary.suggest(word):
                out.append(candidate)
                if len(out) >= limit:
                    break
        except Exception:
            self._failed = True
            return []
        return out

    def count_in(self, events, limit: int = 0) -> int:
        """Сколько реплик содержат незнакомые слова.

        ``limit`` останавливает подсчёт: на большом файле полная проверка
        занимает секунды, а для строки состояния хватает и «больше сотни».
        """
        if not self.enabled:
            return 0
        found = 0
        for event in events:
            if self.check(event.plain):
                found += 1
                if limit and found >= limit:
                    break
        return found
