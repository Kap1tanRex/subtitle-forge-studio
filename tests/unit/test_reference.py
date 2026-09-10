"""Оригинал рядом с переводом.

Проверяется то, на чём этот режим стоит: сопоставление по времени, а не по
номерам реплик. Номера расходятся с первой же объединённой фразой, и любая
проверка, опирающаяся на них, показывала бы переводчику чужой текст.
"""

from __future__ import annotations

from sfstudio.core.reference import LONG_LINE_MS, ReferenceLine, ReferenceTrack


def track(*spans) -> ReferenceTrack:
    """Дорожка из троек (начало, конец, текст)."""
    return ReferenceTrack(ReferenceLine(*span) for span in spans)


class TestSearchByTime:
    def test_finds_the_line_under_the_event(self) -> None:
        made = track((0, 2000, "первая"), (3000, 5000, "вторая"))
        assert [x.text for x in made.overlapping(3100, 4000)] == ["вторая"]

    def test_touching_edges_do_not_count(self) -> None:
        """Строка, кончающаяся ровно там, где реплика начинается, — соседняя.

        Иначе в плотном диалоге у каждой реплики появлялся бы лишний «сосед»
        из предыдущей фразы.
        """
        made = track((0, 3000, "предыдущая"), (3000, 6000, "наша"))
        assert [x.text for x in made.overlapping(3000, 4000)] == ["наша"]

    def test_one_event_may_cover_several_lines(self) -> None:
        """Обычное дело: две короткие фразы оригинала стали одной репликой."""
        made = track((0, 1000, "раз"), (1200, 2000, "два"), (2200, 3000, "три"))
        assert [x.text for x in made.overlapping(0, 3000)] == ["раз", "два", "три"]

    def test_long_line_reaching_from_far_away_is_found(self) -> None:
        """Надпись или песня на весь эпизод начинается задолго до реплики."""
        made = track((0, 600_000, "песня"), (300_000, 302_000, "реплика"))
        found = [x.text for x in made.overlapping(300_500, 301_000)]
        assert found == ["песня", "реплика"]

    def test_results_come_in_time_order(self) -> None:
        made = track((5000, 6000, "поздняя"), (0, 9000, "длинная"), (1000, 2000, "ранняя"))
        assert [x.text for x in made.overlapping(0, 9000)] == [
            "длинная", "ранняя", "поздняя",
        ]

    def test_silence_gives_nothing(self) -> None:
        made = track((0, 1000, "речь"))
        assert made.overlapping(5000, 6000) == []

    def test_empty_track_is_safe(self) -> None:
        assert ReferenceTrack().overlapping(0, 1000) == []
        assert not ReferenceTrack()

    def test_zero_length_range_gives_nothing(self) -> None:
        made = track((0, 1000, "речь"))
        assert made.overlapping(500, 500) == []


class TestText:
    def test_several_lines_are_joined(self) -> None:
        made = track((0, 1000, "раз"), (1200, 2000, "два"))
        assert made.text_for(0, 2000) == "раз два"

    def test_empty_lines_do_not_add_separators(self) -> None:
        made = track((0, 1000, "раз"), (1200, 2000, ""), (2200, 3000, "три"))
        assert made.text_for(0, 3000) == "раз три"

    def test_line_at_a_moment(self) -> None:
        made = track((0, 2000, "первая"), (3000, 5000, "вторая"))
        assert made.line_at(3500).text == "вторая"
        assert made.line_at(2500) is None


class TestBuilding:
    def test_comments_are_skipped(self) -> None:
        """Комментария в кадре не было — значит и в оригинале его нет."""
        from sfstudio.core.document import SubtitleDocument

        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(0, 1000, "видимая")
        hidden = doc.create_event(2000, 3000, "скрытая")
        hidden.comment = True

        made = ReferenceTrack.from_events(doc.events)
        assert [x.text for x in made] == ["видимая"]

    def test_markup_is_stripped(self) -> None:
        """Оригинал читают, а не оформляют: теги в нём только мешают."""
        from sfstudio.core.document import SubtitleDocument

        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(0, 1000, "{\\i1}курсив{\\i0} и текст")

        made = ReferenceTrack.from_events(doc.events)
        assert "{" not in next(iter(made)).text

    def test_source_and_language_are_kept(self) -> None:
        made = ReferenceTrack((), source="original.srt", language="en")
        assert made.source == "original.srt"
        assert made.language == "en"


class TestCoverage:
    """Доля совпадений показывает, тот ли файл подключили."""

    def test_matching_file_covers_everything(self) -> None:
        from sfstudio.core.document import SubtitleDocument

        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        for i in range(4):
            doc.create_event(i * 2000, i * 2000 + 1500, f"реплика {i}")

        made = track(*[(i * 2000, i * 2000 + 1500, f"line {i}") for i in range(4)])
        assert made.coverage(doc.events) == 1.0

    def test_wrong_episode_shows_almost_nothing(self) -> None:
        from sfstudio.core.document import SubtitleDocument

        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        for i in range(10):
            doc.create_event(i * 2000, i * 2000 + 1500, f"реплика {i}")

        # Оригинал от другой серии: всё далеко за пределами нашего файла.
        made = track((3_600_000, 3_601_000, "чужая"))
        assert made.coverage(doc.events) == 0.0

    def test_empty_document_gives_zero(self) -> None:
        assert track((0, 1000, "x")).coverage([]) == 0.0


class TestLongLines:
    def test_threshold_is_documented(self) -> None:
        assert LONG_LINE_MS == 30_000

    def test_long_and_short_are_both_returned(self) -> None:
        made = track((0, LONG_LINE_MS + 1000, "длинная"), (1000, 2000, "короткая"))
        assert len(made.overlapping(1200, 1800)) == 2

    def test_search_stays_fast_with_a_long_line(self) -> None:
        """Одна надпись на весь фильм не должна превращать поиск в перебор.

        Замерено: до разделения на длинные и короткие такой файл давал
        48 мкс на запрос против 0,4 мкс на обычном. Здесь сравниваем с
        обычной дорожкой на той же машине, а не с абсолютным числом.
        """
        import time

        plain = track(*[(i * 2000, i * 2000 + 1800, f"с {i}") for i in range(5000)])
        with_long = ReferenceTrack(
            [*plain, ReferenceLine(0, 20_000_000, "песня")]
        )

        def cost(made: ReferenceTrack) -> float:
            best = []
            for _ in range(5):
                start = time.perf_counter()
                for i in range(1000):
                    made.overlapping(i * 2000, i * 2000 + 1800)
                best.append(time.perf_counter() - start)
            return min(best)

        assert cost(with_long) < cost(plain) * 8
