"""TTML: чтение, запись и то, на чём такие файлы обычно спотыкаются."""

from __future__ import annotations

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.io.formats.ttml import (
    TtmlParseError,
    format_time,
    lossy_report,
    parse_time,
    read_ttml,
    write_ttml,
)
from sfstudio.io.registry import FORMATS, detect_format

HEAD = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:tts="http://www.w3.org/ns/ttml#styling" '
    'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
    'xmlns:ttp="http://www.w3.org/ns/ttml#parameter"'
)


def document(body: str, head: str = "", extra: str = "") -> str:
    return f"{HEAD}{extra}><head>{head}</head><body><div>{body}</div></body></tt>"


class TestTime:
    def test_clock_with_milliseconds(self) -> None:
        assert parse_time("00:00:12.500") == 12500

    def test_comma_is_accepted_as_a_separator(self) -> None:
        """Присылают и с запятой — привычка из SubRip."""
        assert parse_time("00:00:12,500") == 12500

    def test_frames_need_the_frame_rate(self) -> None:
        assert parse_time("00:00:01:12", fps=24) == 1500
        assert parse_time("00:00:01:12", fps=25) == 1480

    def test_offsets_in_every_unit(self) -> None:
        assert parse_time("12.5s") == 12500
        assert parse_time("500ms") == 500
        assert parse_time("2m") == 120_000
        assert parse_time("30f", fps=25) == 1200

    def test_ticks(self) -> None:
        assert parse_time("10000000t") == 1000

    def test_nonsense_gives_nothing(self) -> None:
        assert parse_time("никак") is None
        assert parse_time("") is None

    def test_formatting_round_trips(self) -> None:
        assert parse_time(format_time(3_723_456)) == 3_723_456


class TestReading:
    def test_simple_file(self) -> None:
        doc = read_ttml(document(
            '<p begin="00:00:01.000" end="00:00:03.000">Привет</p>'
        ))
        assert len(doc.events) == 1
        assert doc.events[0].start == 1000
        assert doc.events[0].end == 3000
        assert doc.events[0].text == "Привет"

    def test_duration_instead_of_end(self) -> None:
        doc = read_ttml(document('<p begin="5s" dur="2s">Текст</p>'))
        assert (doc.events[0].start, doc.events[0].end) == (5000, 7000)

    def test_line_break_becomes_markup(self) -> None:
        doc = read_ttml(document('<p begin="0s" end="1s">Первая<br/>вторая</p>'))
        assert doc.events[0].text == r"Первая\Nвторая"

    def test_inline_styles_become_ass_tags(self) -> None:
        doc = read_ttml(document(
            '<p begin="0s" end="1s">Со <span tts:fontStyle="italic">курсивом</span></p>'
        ))
        assert doc.events[0].text == r"Со {\i1}курсивом{\i0}"

    def test_nested_spans(self) -> None:
        """Курсив внутри полужирного встречается в настоящих файлах."""
        doc = read_ttml(document(
            '<p begin="0s" end="1s">'
            '<span tts:fontWeight="bold">жирный <span tts:fontStyle="italic">и косой</span></span>'
            "</p>"
        ))
        assert r"{\b1}" in doc.events[0].text
        assert r"{\i1}" in doc.events[0].text

    def test_frame_rate_multiplier_is_respected(self) -> None:
        """24 с множителем 1000/1001 — это 23,976, и кадры считаются иначе.

        На целых секундах разницы нет, она вся в кадровой части: пятнадцатый
        кадр при 24 кадрах в секунду это 10 625 мс, при 23,976 — 10 626.
        Разница в миллисекунду за десять секунд накапливается в кадр за сорок
        и в полтора кадра за час.
        """
        marks = '<p begin="00:00:10:15" end="00:00:11:00">Т</p>'
        plain = read_ttml(document(marks, extra=' ttp:frameRate="24"'))
        pulled = read_ttml(document(
            marks, extra=' ttp:frameRate="24" ttp:frameRateMultiplier="1000 1001"'
        ))
        assert plain.events[0].start == 10_625
        assert pulled.events[0].start == 10_626

    def test_speaker_comes_from_the_agent(self) -> None:
        doc = read_ttml(document(
            '<p begin="0s" end="1s" ttm:agent="a1">Реплика</p>',
            head='<metadata><ttm:agent xml:id="a1"><ttm:name>Иван</ttm:name></ttm:agent></metadata>',
        ))
        assert doc.events[0].name == "Иван"

    def test_named_style_is_created(self) -> None:
        doc = read_ttml(document(
            '<p begin="0s" end="1s" style="main">Реплика</p>',
            head='<styling><style xml:id="main" tts:fontFamily="Verdana" '
                 'tts:fontSize="36px" tts:color="#FFCC00"/></styling>',
        ))
        assert doc.events[0].style == "main"
        style = doc.styles["main"]
        assert style.fontname == "Verdana"
        assert style.fontsize == 36.0
        assert style.primary.to_hex().upper() == "#FFCC00"

    def test_events_without_time_are_skipped(self) -> None:
        """Реплика без времени показу не подлежит."""
        doc = read_ttml(document("<p>Без времени</p>"))
        assert len(doc.events) == 0

    def test_empty_paragraphs_are_skipped(self) -> None:
        doc = read_ttml(document('<p begin="0s" end="1s">   </p>'))
        assert len(doc.events) == 0

    def test_xml_formatting_does_not_leak_into_text(self) -> None:
        """Перевод строки в исходнике — форматирование файла, а не текст."""
        doc = read_ttml(document(
            '<p begin="0s" end="1s">\n      Строка\n      продолжается\n    </p>'
        ))
        assert doc.events[0].text == "Строка продолжается"


class TestRefusals:
    def test_entities_are_refused(self) -> None:
        """Десять строк вложенных сущностей разворачиваются в гигабайты."""
        bomb = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE tt [<!ENTITY a "aaaaaaaaaa">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            f'{HEAD}><body><div><p begin="0s" end="1s">&b;</p></div></body></tt>'
        )
        with pytest.raises(TtmlParseError, match="сущност"):
            read_ttml(bomb)

    def test_broken_xml_is_reported(self) -> None:
        with pytest.raises(TtmlParseError):
            read_ttml("<tt><body><div><p>не закрыт")

    def test_other_xml_is_refused(self) -> None:
        with pytest.raises(TtmlParseError, match="не TTML"):
            read_ttml('<?xml version="1.0"?><rss><channel/></rss>')


class TestWriting:
    def sample(self) -> SubtitleDocument:
        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        first = doc.create_event(1000, 3000, r"Первая\Nв две строки")
        first.name = "Иван"
        doc.create_event(5000, 7000, r"Со {\i1}курсивом{\i0}")
        return doc

    def test_round_trip_keeps_the_essentials(self) -> None:
        doc = self.sample()
        back = read_ttml(write_ttml(doc))
        assert [(e.start, e.end, e.text) for e in back.events] == [
            (1000, 3000, r"Первая\Nв две строки"),
            (5000, 7000, r"Со {\i1}курсивом{\i0}"),
        ]

    def test_speakers_become_agents(self) -> None:
        written = write_ttml(self.sample())
        assert "ttm:agent" in written
        assert "Иван" in written
        assert read_ttml(written).events[0].name == "Иван"

    def test_special_characters_are_escaped(self) -> None:
        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(0, 1000, "Кот & пёс <здесь>")

        written = write_ttml(doc)
        assert "&amp;" in written and "&lt;" in written
        assert read_ttml(written).events[0].text == "Кот & пёс <здесь>"

    def test_comments_are_not_written(self) -> None:
        """Закомментированную реплику в кадре не показывают."""
        doc = self.sample()
        doc.events[0].comment = True
        assert len(read_ttml(write_ttml(doc)).events) == 1

    def test_frame_rate_is_declared_when_known(self) -> None:
        from sfstudio.core.time import FpsModel

        written = write_ttml(self.sample(), fps=FpsModel.from_float(25.0))
        assert 'ttp:frameRate="25"' in written


class TestLosses:
    def test_positioning_is_reported(self) -> None:
        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(0, 1000, r"{\pos(100,200)}Текст")

        losses = lossy_report(doc)
        assert any(w.detail == r"\pos" for w in losses)

    def test_italics_are_not_a_loss(self) -> None:
        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(0, 1000, r"{\i1}Текст{\i0}")
        assert lossy_report(doc) == []


class TestRegistry:
    def test_format_is_registered(self) -> None:
        assert "ttml" in FORMATS
        spec = FORMATS["ttml"]
        assert spec.can_read and spec.can_write
        assert ".ttml" in spec.extensions and ".dfxp" in spec.extensions

    def test_detected_by_content_not_extension(self) -> None:
        """Присылают и .ttml, и .dfxp, и просто .xml."""
        sample = document('<p begin="0s" end="1s">Текст</p>')
        assert detect_format(sample, hint=".xml") == "ttml"
        assert detect_format(sample) == "ttml"

    def test_plain_srt_is_still_srt(self) -> None:
        assert detect_format("1\n00:00:01,000 --> 00:00:02,000\nТекст\n") == "srt"
