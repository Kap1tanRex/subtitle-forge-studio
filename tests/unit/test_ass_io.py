"""Тесты чтения и записи ASS.

Главное требование к формату — round-trip: ``read → write`` должно дать тот же
файл. Без этого чужие файлы деградируют при каждом сохранении, и редактор
становится опасен для чужой работы.
"""

from __future__ import annotations

import pytest

from sfstudio.core.color import RGBA
from sfstudio.io.formats.ass import AssParseError, read_ass, write_ass

MINIMAL = """[Script Info]
Title: Тест
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,2,2,20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Первая реплика
Dialogue: 0,0:00:03.50,0:00:05.00,Default,Анна,0,0,0,,{\\pos(960,900)}Вторая, с запятой
Comment: 0,0:00:06.00,0:00:07.00,Default,,0,0,0,,Это комментарий
"""


class TestRead:
    def test_script_info(self) -> None:
        doc = read_ass(MINIMAL)
        assert doc.script_info.title == "Тест"
        assert doc.script_info.play_res == (1920, 1080)
        assert doc.script_info.scaled_border_and_shadow is True
        assert doc.script_info.play_res_inferred is False

    def test_styles(self) -> None:
        doc = read_ass(MINIMAL)
        style = doc.styles["Default"]
        assert style.fontname == "Arial"
        assert style.fontsize == 48
        assert style.primary == RGBA(255, 255, 255, 255)
        assert style.back_color.a == 127  # &H80 → 255-128
        assert style.alignment == 2

    def test_events(self) -> None:
        doc = read_ass(MINIMAL)
        assert len(doc.events) == 3
        first = doc.events[0]
        assert first.start == 1000
        assert first.end == 3000
        assert first.text == "Первая реплика"
        assert first.comment is False

    def test_text_with_commas_survives(self) -> None:
        """Поле Text последнее и содержит запятые — разбиение ограничено."""
        doc = read_ass(MINIMAL)
        assert doc.events[1].text == "{\\pos(960,900)}Вторая, с запятой"

    def test_comment_flag(self) -> None:
        assert read_ass(MINIMAL).events[2].comment is True

    def test_actor_name(self) -> None:
        assert read_ass(MINIMAL).events[1].name == "Анна"

    def test_position_extracted(self) -> None:
        assert read_ass(MINIMAL).events[1].position() == (960.0, 900.0)

    def test_missing_playres_is_flagged(self) -> None:
        """Без PlayRes координаты \\pos не совпадут с плеером — надо предупредить."""
        text = MINIMAL.replace("PlayResX: 1920\n", "").replace("PlayResY: 1080\n", "")
        doc = read_ass(text)
        assert doc.script_info.play_res_inferred is True
        assert doc.script_info.play_res == (1920, 1080)

    def test_reordered_format_line(self) -> None:
        """Порядок полей задаётся строкой Format: и в реальных файлах разный."""
        text = MINIMAL.replace(
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            "Format: Start, End, Style, Layer, Name, MarginL, MarginR, MarginV, Effect, Text",
        ).replace(
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Первая реплика",
            "Dialogue: 0:00:01.00,0:00:03.00,Default,0,,0,0,0,,Первая реплика",
        )
        doc = read_ass(text)
        assert doc.events[0].start == 1000
        assert doc.events[0].text == "Первая реплика"

    def test_broken_line_skipped_not_fatal(self) -> None:
        text = MINIMAL + "Dialogue: мусор без таймингов\n"
        doc = read_ass(text)
        assert len(doc.events) == 3  # битая строка пропущена, остальные целы

    def test_unknown_info_key_preserved(self) -> None:
        doc = read_ass(MINIMAL.replace("[V4+ Styles]", "Original Script: Кто-то\n\n[V4+ Styles]"))
        assert doc.script_info.extra["Original Script"] == "Кто-то"

    def test_rejects_non_ass(self) -> None:
        with pytest.raises(AssParseError):
            read_ass("это просто текст\nбез секций")

    def test_ssa_tertiary_colour(self) -> None:
        """SSA v4 зовёт поле обводки TertiaryColour."""
        text = MINIMAL.replace("OutlineColour", "TertiaryColour")
        doc = read_ass(text)
        assert doc.styles["Default"].outline_color == RGBA(0, 0, 0, 255)


class TestRoundTrip:
    def test_byte_exact(self) -> None:
        """Основная гарантия: чтение и запись не меняют файл."""
        assert write_ass(read_ass(MINIMAL)) == MINIMAL

    def test_stable_across_two_cycles(self) -> None:
        once = write_ass(read_ass(MINIMAL))
        twice = write_ass(read_ass(once))
        assert once == twice

    def test_field_order_preserved(self) -> None:
        """Нестандартный порядок Format: должен сохраниться при записи."""
        text = MINIMAL.replace(
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            "Format: Start, End, Style, Layer, Name, MarginL, MarginR, MarginV, Effect, Text",
        ).replace(
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Первая реплика",
            "Dialogue: 0:00:01.00,0:00:03.00,Default,0,,0,0,0,,Первая реплика",
        ).replace(
            "Dialogue: 0,0:00:03.50,0:00:05.00,Default,Анна,0,0,0,,{\\pos(960,900)}Вторая, с запятой",
            "Dialogue: 0:00:03.50,0:00:05.00,Default,0,Анна,0,0,0,,{\\pos(960,900)}Вторая, с запятой",
        ).replace(
            "Comment: 0,0:00:06.00,0:00:07.00,Default,,0,0,0,,Это комментарий",
            "Comment: 0:00:06.00,0:00:07.00,Default,0,,0,0,0,,Это комментарий",
        )
        assert write_ass(read_ass(text)) == text

    def test_edit_changes_only_that_event(self) -> None:
        doc = read_ass(MINIMAL)
        doc.events[0].set_text("Изменено")
        result = write_ass(doc)
        assert "Изменено" in result
        assert "{\\pos(960,900)}Вторая, с запятой" in result
        assert "Это комментарий" in result

    def test_centisecond_rounding_does_not_shorten(self) -> None:
        """Экспорт в ASS теряет точность, но не должен укорачивать субтитр."""
        doc = read_ass(MINIMAL)
        doc.events[0].start = 1234
        doc.events[0].end = 3456
        out = write_ass(doc)
        assert "0:00:01.23" in out  # начало вниз
        assert "0:00:03.46" in out  # конец вверх


class TestWrite:
    def test_bold_written_as_minus_one(self) -> None:
        doc = read_ass(MINIMAL)
        doc.styles["Default"].bold = True
        assert ",-1,0,0,0," in write_ass(doc)

    def test_integer_numbers_have_no_decimal_point(self) -> None:
        assert "Arial,48," in write_ass(read_ass(MINIMAL))
