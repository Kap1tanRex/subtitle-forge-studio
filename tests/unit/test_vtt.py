"""Тесты WebVTT."""

from __future__ import annotations

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.io.formats.vtt import CueSettings, lossy_report, read_vtt, write_vtt
from sfstudio.io.registry import detect_format

SAMPLE = """WEBVTT

NOTE Комментарий автора файла

first-cue
00:00:01.000 --> 00:00:04.000
Первая реплика с <b>жирным</b>

00:00:05.500 --> 00:00:08.000 align:start line:10%
<v Анна>Реплика с голосом
и переносом строки

00:00:09.000 --> 00:00:11.000 align:end position:80% line:50%
Справа &amp; амперсанд
"""


class TestParsing:
    def test_reads_all_cues(self) -> None:
        assert len(read_vtt(SAMPLE)) == 3

    def test_timings(self) -> None:
        doc = read_vtt(SAMPLE)
        assert (doc.events[0].start, doc.events[0].end) == (1000, 4000)
        assert doc.events[1].start == 5500

    def test_hours_are_optional(self) -> None:
        doc = read_vtt("WEBVTT\n\n01:30.500 --> 02:00.000\nБез часов\n")
        assert doc.events[0].start == 90_500

    def test_comma_fraction_is_tolerated(self) -> None:
        """В дикой природе встречается запятая вместо точки."""
        doc = read_vtt("WEBVTT\n\n00:00:01,500 --> 00:00:02,000\nТекст\n")
        assert doc.events[0].start == 1500

    def test_missing_header_is_tolerated(self) -> None:
        doc = read_vtt("00:00:01.000 --> 00:00:02.000\nБез заголовка\n")
        assert len(doc) == 1

    def test_note_blocks_are_skipped(self) -> None:
        """NOTE — комментарий автора файла, а не реплика."""
        doc = read_vtt(SAMPLE)
        assert all("Комментарий автора" not in e.plain for e in doc.events)

    def test_style_and_region_blocks_are_skipped(self) -> None:
        text = (
            "WEBVTT\n\nSTYLE\n::cue { color: red }\n\n"
            "REGION\nid:r1 width:40%\n\n"
            "00:00:01.000 --> 00:00:02.000\nЕдинственная реплика\n"
        )
        doc = read_vtt(text)
        assert len(doc) == 1
        assert doc.events[0].plain == "Единственная реплика"

    def test_cue_identifier_is_kept(self) -> None:
        doc = read_vtt(SAMPLE)
        assert doc.events[0].effect == "first-cue"

    def test_broken_block_does_not_break_file(self) -> None:
        text = SAMPLE + "\nэто не время\nи не реплика\n"
        assert len(read_vtt(text)) == 3

    def test_empty_input(self) -> None:
        assert len(read_vtt("WEBVTT\n")) == 0


class TestMarkup:
    def test_bold_becomes_ass_tag(self) -> None:
        doc = read_vtt(SAMPLE)
        assert r"{\b1}" in doc.events[0].text
        assert r"{\b0}" in doc.events[0].text

    def test_italic_and_underline(self) -> None:
        doc = read_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>к</i><u>т</u>\n")
        assert r"{\i1}" in doc.events[0].text
        assert r"{\u1}" in doc.events[0].text

    def test_unknown_tags_are_dropped(self) -> None:
        """``<c.класс>`` и ``<ruby>`` в ASS не выражаются."""
        doc = read_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<c.loud>Громко</c>\n")
        assert doc.events[0].plain == "Громко"

    def test_voice_becomes_actor(self) -> None:
        doc = read_vtt(SAMPLE)
        assert doc.events[1].name == "Анна"
        assert "<v" not in doc.events[1].text

    def test_entities_are_decoded(self) -> None:
        doc = read_vtt(SAMPLE)
        assert "&" in doc.events[2].plain
        assert "&amp;" not in doc.events[2].plain

    def test_line_breaks(self) -> None:
        doc = read_vtt(SAMPLE)
        assert doc.events[1].plain.count("\n") == 1


class TestCueSettings:
    def test_parse_all_fields(self) -> None:
        settings = CueSettings.parse("align:start line:10% position:25% size:50%")
        assert settings.align == "start"
        assert settings.line == "10%"
        assert settings.position == "25%"

    def test_empty(self) -> None:
        assert CueSettings.parse("").is_empty

    def test_alignment_becomes_anchor(self) -> None:
        doc = read_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000 align:end\nСправа\n")
        assert doc.events[0].alignment_override() == 3

    def test_percentages_become_position(self) -> None:
        doc = read_vtt(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000 position:25% line:75%\nТекст\n"
        )
        position = doc.events[0].position()
        assert position is not None
        assert position[0] == pytest.approx(0.25 * 1920, abs=2)
        assert position[1] == pytest.approx(0.75 * 1080, abs=2)

    def test_default_settings_do_not_pin_position(self) -> None:
        """Без явных line/position плеер решает сам — прибивать \\pos нельзя."""
        doc = read_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nОбычная\n")
        assert doc.events[0].position() is None


class TestWriting:
    def test_header_is_written(self) -> None:
        doc = read_vtt(SAMPLE)
        assert write_vtt(doc).startswith("WEBVTT")

    def test_timing_uses_dot(self) -> None:
        """WebVTT требует точку, запятая недопустима."""
        doc = read_vtt(SAMPLE)
        out = write_vtt(doc)
        assert "00:00:01.000 --> 00:00:04.000" in out
        assert ",000 -->" not in out

    def test_markup_is_restored(self) -> None:
        doc = read_vtt(SAMPLE)
        assert "<b>" in write_vtt(doc)

    def test_voice_is_restored(self) -> None:
        doc = read_vtt(SAMPLE)
        assert "<v Анна>" in write_vtt(doc)

    def test_entities_are_escaped(self) -> None:
        doc = read_vtt(SAMPLE)
        assert "&amp;" in write_vtt(doc)

    def test_identifier_is_restored(self) -> None:
        doc = read_vtt(SAMPLE)
        assert "first-cue" in write_vtt(doc)

    def test_comments_become_notes(self) -> None:
        """Закомментированные события — единственный способ не показать их."""
        doc = SubtitleDocument.blank()
        doc.create_event(1000, 2000, "Видимая")
        hidden = doc.create_event(3000, 4000, "Скрытая")
        hidden.comment = True
        out = write_vtt(doc)
        assert "NOTE Скрытая" in out
        assert "00:00:03.000" not in out

    def test_position_can_be_disabled(self) -> None:
        doc = read_vtt(SAMPLE)
        out = write_vtt(doc, keep_position=False)
        assert "align:" not in out

    def test_markup_can_be_disabled(self) -> None:
        doc = read_vtt(SAMPLE)
        out = write_vtt(doc, keep_markup=False)
        assert "<b>" not in out


class TestRoundTrip:
    def test_text_survives(self) -> None:
        doc = read_vtt(SAMPLE)
        back = read_vtt(write_vtt(doc))
        assert [e.plain for e in back.events] == [e.plain for e in doc.events]

    def test_timings_survive(self) -> None:
        doc = read_vtt(SAMPLE)
        back = read_vtt(write_vtt(doc))
        assert [(e.start, e.end) for e in back.events] == [
            (e.start, e.end) for e in doc.events
        ]

    def test_actors_survive(self) -> None:
        doc = read_vtt(SAMPLE)
        back = read_vtt(write_vtt(doc))
        assert [e.name for e in back.events] == [e.name for e in doc.events]

    def test_alignment_survives(self) -> None:
        doc = read_vtt(SAMPLE)
        back = read_vtt(write_vtt(doc))
        assert [e.alignment_override() for e in back.events] == [
            e.alignment_override() for e in doc.events
        ]

    def test_stable_across_two_cycles(self) -> None:
        once = write_vtt(read_vtt(SAMPLE))
        twice = write_vtt(read_vtt(once))
        assert once == twice


class TestRegistry:
    def test_detected_by_header(self) -> None:
        assert detect_format(SAMPLE, ".vtt") == "vtt"

    def test_srt_not_confused_with_vtt(self) -> None:
        srt = "1\n00:00:01,000 --> 00:00:02,000\nТекст\n"
        assert detect_format(srt, ".srt") == "srt"

    def test_save_and_load(self, tmp_path) -> None:
        from sfstudio.io import registry

        doc = read_vtt(SAMPLE)
        path = tmp_path / "out.vtt"
        registry.save(doc, path)
        loaded = registry.load(path)
        assert len(loaded) == len(doc)
        assert loaded.source_format == "vtt"


class TestLossyReport:
    def test_reports_unsupported_tags(self) -> None:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, r"{\frz45\blur3}Повёрнутый")
        warnings = lossy_report(doc)
        assert any("frz" in w for w in warnings)

    def test_supported_tags_are_silent(self) -> None:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, r"{\b1}Жирный")
        assert not lossy_report(doc)
