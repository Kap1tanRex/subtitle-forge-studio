"""Документ: то, на чём стоит всё остальное.

Файла с такими тестами не было: документ проверялся через команды, чтение
файлов и окно. Так покрыты все обычные пути, но не края — и мутационный
разбор это показал: в ``duration`` можно было поменять ``max`` на ``min``, а
в ``create_event`` вывернуть условие, и полторы тысячи тестов не замечали.
"""

from __future__ import annotations

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle


@pytest.fixture
def doc() -> SubtitleDocument:
    return SubtitleDocument.blank()


class TestCreateEvent:
    def test_style_is_filled_in(self, doc: SubtitleDocument) -> None:
        """Реплика без стиля сослалась бы в пустоту, и её нечем рисовать."""
        assert doc.create_event(0, 1000, "Текст").style == next(iter(doc.styles))

    def test_given_style_is_kept(self, doc: SubtitleDocument) -> None:
        """Иначе «положить реплику в свой стиль» не работало бы вовсе."""
        doc.add_style(SubtitleStyle(name="Надпись"))
        made = doc.create_event(0, 1000, "Текст", style="Надпись")
        assert made.style == "Надпись"

    def test_event_lands_in_the_document(self, doc: SubtitleDocument) -> None:
        made = doc.create_event(0, 1000, "Текст")
        assert doc.has(made.eid)

    def test_each_event_gets_its_own_eid(self, doc: SubtitleDocument) -> None:
        first = doc.create_event(0, 1000, "Раз")
        second = doc.create_event(2000, 3000, "Два")
        assert first.eid != second.eid


class TestDuration:
    def test_empty_document_lasts_nothing(self, doc: SubtitleDocument) -> None:
        assert doc.duration == 0

    def test_duration_reaches_the_last_end(self, doc: SubtitleDocument) -> None:
        doc.create_event(0, 1000, "Раз")
        doc.create_event(5000, 9000, "Два")
        assert doc.duration == 9000

    def test_order_in_the_list_does_not_matter(self, doc: SubtitleDocument) -> None:
        """Реплики лежат в порядке файла, а он не обязан быть по времени."""
        doc.create_event(5000, 9000, "Поздняя")
        doc.create_event(0, 1000, "Ранняя")
        assert doc.duration == 9000

    def test_longest_wins_even_if_it_starts_first(self, doc: SubtitleDocument) -> None:
        doc.create_event(0, 12000, "Длинная")
        doc.create_event(1000, 2000, "Короткая")
        assert doc.duration == 12000


class TestEids:
    def test_reserved_eid_is_not_handed_out(self, doc: SubtitleDocument) -> None:
        """При чтении файла номера приходят готовыми — и новые не должны с
        ними совпадать, иначе две реплики окажутся одной."""
        doc.reserve_eid(500)
        assert doc.new_eid() > 500

    def test_reserving_a_small_number_changes_nothing(self, doc: SubtitleDocument) -> None:
        doc.reserve_eid(500)
        before = doc.new_eid()
        doc.reserve_eid(3)
        assert doc.new_eid() >= before

    def test_reserving_the_very_next_number_moves_the_counter(
        self, doc: SubtitleDocument
    ) -> None:
        """Граница: занят ровно тот номер, который счётчик собирался выдать.

        Промахнуться тут легко, а цена промаха велика: две реплики с одним
        номером — это документ, в котором правка одной меняет другую.
        """
        first = doc.new_eid()
        doc.reserve_eid(first + 1)
        assert doc.new_eid() > first + 1

    def test_added_event_reserves_its_own_number(self, doc: SubtitleDocument) -> None:
        doc.add_event(SubtitleEvent(eid=77, start=0, end=1000, text="Гость"))
        assert doc.new_eid() > 77


class TestStylesInUse:
    def test_unused_styles_are_listed_with_zero(self, doc: SubtitleDocument) -> None:
        """Ноль — это ответ: стиль есть, реплик на нём нет."""
        doc.add_style(SubtitleStyle(name="Надпись"))
        assert doc.styles_in_use()["Надпись"] == 0

    def test_events_are_counted(self, doc: SubtitleDocument) -> None:
        doc.create_event(0, 1000, "Раз")
        doc.create_event(2000, 3000, "Два")
        assert doc.styles_in_use()["Default"] == 2

    def test_style_from_a_foreign_file_is_counted_too(self, doc: SubtitleDocument) -> None:
        """Ссылку на несуществующий стиль надо видеть, а не прятать."""
        doc.add_event(SubtitleEvent(eid=1, start=0, end=1000, text="", style="Чужой"))
        assert doc.styles_in_use()["Чужой"] == 1


class TestAlignmentOverride:
    """``\\an`` в тексте реплики: перекрывает выравнивание её стиля."""

    def test_number_is_read(self) -> None:
        event = SubtitleEvent(eid=1, start=0, end=1000, text=r"{\an8}Сверху")
        assert event.alignment_override() == 8

    def test_tag_without_a_number_is_not_a_crash(self) -> None:
        """Пустой тег — обычное дело в файлах, собранных чужими руками."""
        event = SubtitleEvent(eid=1, start=0, end=1000, text=r"{\an}Текст")
        assert event.alignment_override() is None

    def test_number_outside_the_keypad_is_refused(self) -> None:
        event = SubtitleEvent(eid=1, start=0, end=1000, text=r"{\an12}Текст")
        assert event.alignment_override() is None

    def test_legacy_tag_is_converted(self) -> None:
        """``\\a`` из SSA кодирует ряд битами, а не номер на клавиатуре.

        5 — это «верх, первый столбец», то есть 7 по цифровой клавиатуре.
        """
        event = SubtitleEvent(eid=1, start=0, end=1000, text=r"{\a5}Текст")
        assert event.alignment_override() == 7

    def test_legacy_middle_row_is_converted(self) -> None:
        event = SubtitleEvent(eid=1, start=0, end=1000, text=r"{\a9}Текст")
        assert event.alignment_override() == 4

    def test_legacy_bottom_row_stays_as_is(self) -> None:
        """Нижний ряд в SSA и ASS нумеруется одинаково."""
        event = SubtitleEvent(eid=1, start=0, end=1000, text=r"{\a2}Текст")
        assert event.alignment_override() == 2

    def test_legacy_tag_without_a_column_is_refused(self) -> None:
        """Без разряда столбца выравнивание не определено — берётся стиль."""
        event = SubtitleEvent(eid=1, start=0, end=1000, text=r"{\a4}Текст")
        assert event.alignment_override() is None

    def test_plain_text_has_no_override(self) -> None:
        event = SubtitleEvent(eid=1, start=0, end=1000, text="Просто текст")
        assert event.alignment_override() is None
