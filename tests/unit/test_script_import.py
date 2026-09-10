"""Импорт текста без таймингов: сценарий или готовый перевод из файла."""

from __future__ import annotations

from itertools import pairwise

from sfstudio.services.script_import import (
    guess_paragraphs,
    read_script,
    spread,
)

SCRIPT = """ИВАН: Ты вообще слушаешь?

МАРИЯ: Слушаю. Просто не согласна.

ИВАН: Оно и видно.
"""

PLAIN = """Первая реплика
Вторая реплика
Третья реплика
"""


class TestSplitting:
    def test_line_per_replica(self) -> None:
        assert len(read_script(PLAIN)) == 3

    def test_empty_lines_are_not_replicas(self) -> None:
        assert len(read_script("Первая\n\n\nВторая\n")) == 2

    def test_paragraph_per_replica(self) -> None:
        text = "Первая строка\nпродолжение\n\nВторая реплика\n"
        assert len(read_script(text, paragraphs=True)) == 2

    def test_paragraph_keeps_the_inner_break(self) -> None:
        """Внутренний перенос — это перенос строки в реплике, а не разделитель."""
        text = "Первая строка\nвторая строка\n\nДругая реплика\n"
        assert read_script(text, paragraphs=True)[0].text == r"Первая строка\Nвторая строка"

    def test_surrounding_spaces_go_away(self) -> None:
        assert read_script("   Реплика   \n")[0].text == "Реплика"


class TestGuessing:
    def test_paragraph_layout_is_recognised(self) -> None:
        text = "Первая\nстрока\n\nВторая\nстрока\n\nТретья\nстрока\n"
        assert guess_paragraphs(text)

    def test_line_layout_is_recognised(self) -> None:
        assert not guess_paragraphs(PLAIN)

    def test_single_block_is_not_paragraphs(self) -> None:
        assert not guess_paragraphs("Одна строка\n")

    def test_one_wrapped_replica_among_short_ones_is_enough(self) -> None:
        """Перенос длинной реплики — повод делить абзацами, а не строками."""
        text = "Короткая\n\nДлинная реплика\nс переносом\n\nЕщё короткая\n"
        assert guess_paragraphs(text)

    def test_scene_groups_are_not_paragraphs(self) -> None:
        """Пустая строка между сценами — не признак абзацной вёрстки.

        Склеить десяток реплик сцены в одну — хуже, чем разбить перенос.
        """
        scene = "Реплика\n" * 8
        assert not guess_paragraphs(scene + "\n" + scene)


class TestSpeakers:
    def test_name_goes_to_the_actor(self) -> None:
        lines = read_script(SCRIPT, paragraphs=True, speakers=True)
        assert [line.actor for line in lines] == ["ИВАН", "МАРИЯ", "ИВАН"]

    def test_name_is_removed_from_the_text(self) -> None:
        lines = read_script(SCRIPT, paragraphs=True, speakers=True)
        assert lines[0].text == "Ты вообще слушаешь?"

    def test_name_stays_in_the_text_when_not_asked(self) -> None:
        lines = read_script(SCRIPT, paragraphs=True)
        assert lines[0].text.startswith("ИВАН:")
        assert lines[0].actor == ""

    def test_time_is_not_a_speaker(self) -> None:
        """«19:30 в среду» — не реплика Ивана по имени 19."""
        line = read_script("19:30 в среду", speakers=True)[0]
        assert line.actor == ""
        assert line.text == "19:30 в среду"

    def test_a_colon_inside_a_sentence_is_not_a_speaker(self) -> None:
        line = read_script("Правило простое: не спорь", speakers=True)[0]
        assert line.actor == "Правило простое"

    def test_a_bare_name_is_not_a_replica(self) -> None:
        """«ИВАН:» отдельной строкой — заголовок реплики, а не текст."""
        lines = read_script("ИВАН:\nТы слушаешь?", speakers=True)
        assert len(lines) == 1
        assert lines[0].text == "Ты слушаешь?"

    def test_a_bare_name_names_the_next_replica(self) -> None:
        """В сценариях для озвучки имя чаще стоит отдельной строкой."""
        assert read_script("ИВАН:\nТы слушаешь?", speakers=True)[0].actor == "ИВАН"

    def test_the_name_does_not_leak_further(self) -> None:
        """Заголовок относится к одной реплике, а не ко всему, что ниже."""
        lines = read_script("ИВАН:\nТы слушаешь?\nОн отвернулся", speakers=True)
        assert lines[1].actor == ""


class TestNoise:
    def test_page_number_is_dropped(self) -> None:
        assert len(read_script("Реплика\n12\nДругая реплика\n")) == 2

    def test_stage_direction_is_dropped(self) -> None:
        assert len(read_script("Реплика\n(смеётся)\nДругая\n")) == 2

    def test_brackets_inside_a_line_stay(self) -> None:
        """Выбрасывается только ремарка целиком, а не текст со скобками."""
        line = read_script("Он (наконец) ответил\n")[0]
        assert "наконец" in line.text

    def test_noise_can_be_kept(self) -> None:
        assert len(read_script("Реплика\n12\n", skip_noise=False)) == 2


class TestTimes:
    def test_replicas_go_one_after_another(self) -> None:
        times = spread(read_script(PLAIN))
        assert times[0][0] == 0
        assert all(b[0] > a[1] for a, b in pairwise(times))

    def test_every_replica_has_a_length(self) -> None:
        assert all(end > start for start, end in spread(read_script(PLAIN)))

    def test_start_can_be_moved(self) -> None:
        assert spread(read_script(PLAIN), start_ms=5000)[0][0] == 5000

    def test_nothing_to_spread(self) -> None:
        assert spread([]) == []
