"""Проверка орфографии: разбор текста, кэш, свой словарь, поле правки."""

from __future__ import annotations

import pytest

from sfstudio.services.spelling import LANGUAGES, SpellChecker, available

pytestmark = pytest.mark.skipif(
    not available(), reason="нет библиотеки проверки орфографии"
)


@pytest.fixture(scope="module")
def russian() -> SpellChecker:
    """Один словарь на модуль: чтение файла стоит полсекунды."""
    return SpellChecker("ru")


class TestLanguages:
    def test_off_is_the_first_choice(self) -> None:
        """Проверка не должна включаться сама: словарь читается полсекунды."""
        assert LANGUAGES[0][0] == ""

    def test_disabled_checker_finds_nothing(self) -> None:
        assert SpellChecker("").check("Превет мир") == []
        assert not SpellChecker("").enabled

    def test_unknown_language_does_not_break_anything(self) -> None:
        """Язык могли вписать руками в settings.json."""
        checker = SpellChecker("клингонский")
        assert checker.check("что угодно") == []


class TestChecking:
    def test_misspelling_is_found_with_its_place(self, russian) -> None:
        found = russian.check("Превет, мир!")
        assert [(m.word, m.start, m.end) for m in found] == [("Превет", 0, 6)]

    def test_correct_text_is_clean(self, russian) -> None:
        assert russian.check("Привет, мир!") == []

    def test_capitalised_word_is_not_an_error(self, russian) -> None:
        """Hunspell учитывает регистр: без поправки каждое слово после точки
        становилось бы ошибкой."""
        assert russian.check("Привет. Мир. Субтитры.") == []

    def test_markup_is_skipped(self, russian) -> None:
        """Подчёркнутая половина тега отучает смотреть на подчёркивания."""
        assert russian.check(r"{\pos(10,20)}Привет") == []

    def test_positions_survive_markup(self, russian) -> None:
        text = r"{\i1}Превет"
        found = russian.check(text)
        assert text[found[0].start:found[0].end] == "Превет"

    def test_numbers_are_not_words(self, russian) -> None:
        assert russian.check("В 2024 году") == []

    def test_single_letters_are_skipped(self, russian) -> None:
        """«я», «в» и инициалы проверять нечем."""
        assert russian.check("я в А") == []


class TestOwnDictionary:
    def test_added_word_becomes_known(self) -> None:
        checker = SpellChecker("ru")
        assert checker.check("Кеноби")
        checker.add_word("Кеноби")
        assert checker.check("Кеноби") == []

    def test_case_does_not_matter(self) -> None:
        checker = SpellChecker("ru")
        checker.add_word("Кеноби")
        assert checker.check("кеноби") == []

    def test_words_can_be_replaced_wholesale(self) -> None:
        checker = SpellChecker("ru", {"Кеноби"})
        assert checker.check("Кеноби") == []
        checker.set_extra_words(["Скайуокер"])
        assert checker.check("Кеноби")
        assert checker.check("Скайуокер") == []

    def test_empty_word_is_ignored(self) -> None:
        checker = SpellChecker("ru")
        checker.add_word("   ")
        assert checker.extra_words == set()


class TestSuggestions:
    def test_suggestions_contain_the_right_word(self, russian) -> None:
        assert "привет" in [s.lower() for s in russian.suggest("превет")]

    def test_limit_is_respected(self, russian) -> None:
        assert len(russian.suggest("превет", limit=3)) <= 3

    def test_disabled_checker_suggests_nothing(self) -> None:
        assert SpellChecker("").suggest("превет") == []


class TestSpeed:
    def test_repeat_checks_come_from_cache(self, russian) -> None:
        """Проверка слова стоит 45 мкс: на таблице без кэша это секунды."""
        import time

        text = "Проверка скорости словаря на обычной реплике фильма"
        russian.check(text)  # прогрев

        start = time.perf_counter()
        for _ in range(200):
            russian.check(text)
        each = (time.perf_counter() - start) / 200
        assert each < 0.001, f"{each * 1000:.2f} мс на строку"


pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from sfstudio.ui.spellcheck import attach_spellcheck  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeChecker:
    """Подставной словарь: знает ровно то, что ему сказали.

    Настоящий словарь здесь не нужен — проверяется подсветка, а не Hunspell.
    Заодно тест не платит полсекунды за чтение файла и не тянет в процесс
    десятки мегабайт данных.
    """

    def __init__(self, unknown: set[str], enabled: bool = True) -> None:
        self._unknown = {w.lower() for w in unknown}
        self.enabled = enabled

    def known(self, word: str) -> bool:
        return word.lower() not in self._unknown

    def check(self, text: str):
        from sfstudio.services.spelling import _WORD, Misspelling

        if not self.enabled:
            return []
        return [
            Misspelling(m.group(0), m.start(), m.end())
            for m in _WORD.finditer(text)
            if not self.known(m.group(0))
        ]

    def suggest(self, word: str, limit: int = 7):
        return ["замена"]

    def add_word(self, word: str) -> None:
        self._unknown.discard(word.lower())


@pytest.fixture
def field(qapp):
    """Поле правки с подсветкой, живущее ровно один тест.

    Подсветчик принадлежит документу поля, а поле здесь никому: если дать
    сборщику мусора разобрать их в произвольном порядке, Qt обратится к уже
    удалённому объекту и уронит процесс целиком. Поэтому оба держим и
    разбираем сами, в понятном порядке.
    """
    made: list = []

    def build(checker: SpellChecker):
        editor = QPlainTextEdit()
        highlighter = attach_spellcheck(editor, checker)
        made.append((editor, highlighter))
        return editor, highlighter

    yield build

    for editor, highlighter in made:
        highlighter.setDocument(None)
        editor.setParent(None)
    made.clear()


class TestEditorHighlight:
    def underlines(self, editor: QPlainTextEdit) -> list[tuple[int, int]]:
        block = editor.document().firstBlock()
        return [(r.start, r.length) for r in block.layout().formats()]

    def test_misspelling_is_underlined(self, qapp, field) -> None:
        editor, _ = field(FakeChecker({"Превет"}))
        editor.setPlainText("Превет, мир")
        qapp.processEvents()
        assert self.underlines(editor) == [(0, 6)]

    def test_correct_text_has_no_underlines(self, qapp, field) -> None:
        editor, _ = field(FakeChecker({"Превет"}))
        editor.setPlainText("Привет, мир")
        qapp.processEvents()
        assert self.underlines(editor) == []

    def test_disabled_checker_underlines_nothing(self, qapp, field) -> None:
        editor, _ = field(FakeChecker({"Превет"}, enabled=False))
        editor.setPlainText("Превет, мир")
        qapp.processEvents()
        assert self.underlines(editor) == []

    def test_adding_a_word_clears_the_underline(self, qapp, field) -> None:
        """Слово занесли в свой словарь — подчёркивание должно уйти сразу."""
        checker = FakeChecker({"Кеноби"})
        editor, highlighter = field(checker)
        editor.setPlainText("Кеноби здесь")
        qapp.processEvents()
        assert self.underlines(editor)

        checker.add_word("Кеноби")
        highlighter.rehighlight()
        qapp.processEvents()
        assert self.underlines(editor) == []
