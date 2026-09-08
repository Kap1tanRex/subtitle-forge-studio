"""Тесты парсера и писателя ASS-тегов.

Главные свойства, которые проверяются: ничего не теряется, ничего лишнего не
меняется, и повторное применение той же правки ничего не делает.
"""

from __future__ import annotations

import pytest

from sfstudio.core import tags as t


class TestParse:
    def test_plain_text_has_no_blocks(self) -> None:
        parsed = t.parse_tags("Просто текст")
        assert parsed.blocks == []
        assert parsed.render() == "Просто текст"

    def test_simple_block(self) -> None:
        parsed = t.parse_tags(r"{\pos(100,200)}Привет")
        assert len(parsed.blocks) == 1
        assert [tag.name for tag in parsed.blocks[0].tags] == ["pos"]
        assert parsed.blocks[0].tags[0].numbers() == (100.0, 200.0)

    def test_multiple_tags_in_block(self) -> None:
        parsed = t.parse_tags(r"{\pos(10,20)\an5\frz45.5\b1}Текст")
        names = [tag.name for tag in parsed.blocks[0].tags]
        assert names == ["pos", "an", "frz", "b"]
        assert parsed.blocks[0].tags[2].numbers() == (45.5,)

    def test_multiple_blocks(self) -> None:
        parsed = t.parse_tags(r"{\b1}жирный{\b0} обычный {\i1}курсив")
        assert len(parsed.blocks) == 3

    def test_nested_parens_in_transform(self) -> None:
        """``\\t`` содержит вложенные теги — скобки считаются по глубине."""
        parsed = t.parse_tags(r"{\t(0,1000,\frz360)}вращение")
        tags = parsed.blocks[0].tags
        assert len(tags) == 1
        assert tags[0].name == "t"
        assert tags[0].args_raw == r"0,1000,\frz360"

    def test_leading_digit_tag_names(self) -> None:
        parsed = t.parse_tags(r"{\1c&HFF0000&\3a&H80&}текст")
        assert [tag.name for tag in parsed.blocks[0].tags] == ["1c", "3a"]

    def test_comment_inside_block_preserved(self) -> None:
        src = r"{комментарий\b1}текст"
        parsed = t.parse_tags(src)
        assert parsed.blocks[0].comment == "комментарий"
        assert parsed.render() == src

    def test_unclosed_block_is_not_lost(self) -> None:
        src = r"{\pos(1,2)текст без закрывающей"
        assert t.parse_tags(src).render() == src

    def test_unclosed_paren_is_not_lost(self) -> None:
        src = r"{\pos(1,2}текст"
        assert t.parse_tags(src).render() == src

    def test_lone_backslash(self) -> None:
        src = r"{\}текст"
        assert t.parse_tags(src).render() == src

    @pytest.mark.parametrize(
        "src",
        [
            "",
            "{}",
            "{{}}",
            r"{\}",
            r"}{",
            r"{\pos}",
            r"{\pos()}",
            r"{\\\\}",
            "{" * 50,
            "}" * 50,
            r"{\t(\t(\t(\frz1)))}",
            "\x00\x01мусор",
        ],
    )
    def test_render_roundtrip_on_junk(self, src: str) -> None:
        """Любой ввод переживает parse → render без изменений и без исключений."""
        assert t.parse_tags(src).render() == src


class TestSetEventTag:
    def test_adds_when_missing(self) -> None:
        assert t.set_event_tag("Привет", "pos", (10, 20)) == r"{\pos(10,20)}Привет"

    def test_adds_to_existing_block(self) -> None:
        result = t.set_event_tag(r"{\b1}Привет", "pos", (10, 20))
        assert result == r"{\b1\pos(10,20)}Привет"

    def test_replaces_in_place_preserving_order(self) -> None:
        """Соседние теги не должны ни исчезнуть, ни поменяться местами."""
        result = t.set_event_tag(r"{\b1\pos(1,2)\i1}Текст", "pos", (99, 88))
        assert result == r"{\b1\pos(99,88)\i1}Текст"

    def test_idempotent(self) -> None:
        once = t.set_event_tag("Текст", "pos", (10, 20))
        twice = t.set_event_tag(once, "pos", (10, 20))
        assert once == twice

    def test_removes_duplicates_from_other_blocks(self) -> None:
        result = t.set_event_tag(r"{\pos(1,1)}a{\pos(2,2)}b", "pos", (9, 9))
        assert result == r"{\pos(9,9)}a{}b"
        assert t.parse_tags(result).get_tag("pos").numbers() == (9.0, 9.0)

    def test_unknown_tags_survive(self) -> None:
        """Незнакомый тег обязан пережить правку соседнего."""
        src = r"{\кастом123\pos(1,2)}Текст"
        result = t.set_event_tag(src, "pos", (5, 6))
        assert r"\кастом123" in result

    def test_non_paren_tag(self) -> None:
        assert t.set_event_tag("Текст", "an", 5) == r"{\an5}Текст"

    def test_float_without_trailing_zero(self) -> None:
        assert t.set_event_tag("x", "frz", 45.0) == r"{\frz45}x"
        assert t.set_event_tag("x", "frz", 45.5) == r"{\frz45.5}x"

    def test_text_untouched(self) -> None:
        src = "Текст {с фигурными} внутри"
        result = t.set_event_tag(src, "an", 8)
        assert "Текст " in result and " внутри" in result


class TestRemoveEventTag:
    def test_removes_and_drops_empty_block(self) -> None:
        assert t.remove_event_tag(r"{\pos(1,2)}Текст", "pos") == "Текст"

    def test_keeps_block_with_other_tags(self) -> None:
        assert t.remove_event_tag(r"{\b1\pos(1,2)}Текст", "pos") == r"{\b1}Текст"

    def test_noop_when_absent(self) -> None:
        src = r"{\b1}Текст"
        assert t.remove_event_tag(src, "pos") is src

    def test_set_then_remove_restores_original(self) -> None:
        for src in ["Текст", r"{\b1}Текст", r"{\b1\i1}A{\u1}B"]:
            assert t.remove_event_tag(t.set_event_tag(src, "pos", (1, 2)), "pos") == src


class TestPlainText:
    def test_strips_blocks(self) -> None:
        assert t.plain_text(r"{\b1}жирный{\b0} текст") == "жирный текст"

    def test_line_breaks(self) -> None:
        assert t.plain_text(r"Первая\NВторая") == "Первая\nВторая"
        assert t.plain_text(r"Первая\nВторая") == "Первая\nВторая"

    def test_hard_space(self) -> None:
        assert t.plain_text(r"A\hB") == "A B"

    def test_offset_map_points_into_source(self) -> None:
        src = r"{\b1}Привет{\b0} мир"
        plain, offsets = t.plain_with_map(src)
        assert plain == "Привет мир"
        assert len(offsets) == len(plain)
        # Каждое смещение должно указывать на тот же символ в исходной строке.
        for i, ch in enumerate(plain):
            if ch not in ("\n", " "):
                assert src[offsets[i]] == ch

    def test_offset_map_with_line_break(self) -> None:
        src = r"AB\NCD"
        plain, offsets = t.plain_with_map(src)
        assert plain == "AB\nCD"
        assert offsets == [0, 1, 2, 4, 5]  # \N занимает две позиции в исходнике


def test_strip_tags_keeps_escapes() -> None:
    assert t.strip_tags(r"{\b1}A\NB") == r"A\NB"


def test_event_tags_constant_covers_visual_editor() -> None:
    for name in ("pos", "move", "org", "an", "clip", "fad"):
        assert name in t.EVENT_TAGS


class TestTagNameBoundary:
    """Имя тега кончается там, где начинается аргумент.

    Жадный разбор «все буквы подряд» давал у ``\fnTimes New Roman`` имя тега
    ``fnTimes``: смена шрифта тегом не работала вовсе, а побайтовый round-trip
    при этом оставался целым, поэтому дефект и не был виден.
    """

    def test_font_name_with_spaces(self) -> None:
        tag = t.parse_tags(r"{\fnTimes New Roman}Текст").get_tag("fn")
        assert tag is not None
        assert tag.args_raw == "Times New Roman"

    def test_font_name_without_spaces(self) -> None:
        assert t.parse_tags(r"{\fnArial}Текст").get_tag("fn").args_raw == "Arial"

    def test_longest_known_name_wins(self) -> None:
        """``\fscx`` не должен разобраться как ``\fs`` с аргументом ``cx120``."""
        parsed = t.parse_tags(r"{\fscx120}Текст")
        assert parsed.get_tag("fscx") is not None
        assert parsed.get_tag("fs") is None

    def test_rnd_is_not_reset(self) -> None:
        parsed = t.parse_tags(r"{\rnd3}Текст")
        assert parsed.get_tag("rnd") is not None
        assert parsed.get_tag("r") is None

    def test_reset_with_style_name(self) -> None:
        assert t.parse_tags(r"{\rTitles}Текст").get_tag("r").args_raw == "Titles"

    def test_unknown_tag_is_kept_verbatim(self) -> None:
        """Незнакомый тег чужой программы терять нельзя."""
        parsed = t.parse_tags(r"{\zzz42}Текст")
        tag = parsed.get_tag("zzz")
        assert tag is not None
        assert tag.render() == r"\zzz42"

    @pytest.mark.parametrize(
        "source",
        [r"{\fnTimes New Roman}Текст", r"{\fn}Текст", r"{\b1\fnArial\fs40}Текст"],
    )
    def test_font_tag_renders_back_byte_for_byte(self, source: str) -> None:
        block = t.parse_tags(source).blocks[0]
        rebuilt = "{" + "".join(tag.render() for tag in block.tags) + "}"
        assert rebuilt == source[block.start : block.end]
