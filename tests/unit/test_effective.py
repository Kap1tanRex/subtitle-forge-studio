"""Тесты действующего оформления реплики."""

from __future__ import annotations

from sfstudio.core.color import RGBA
from sfstudio.core.effective import effective_style
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.style import SubtitleStyle


def make(text: str) -> SubtitleEvent:
    return SubtitleEvent(eid=1, start=0, end=1000, text=text)


STYLE = SubtitleStyle(
    name="Default", fontname="Arial", fontsize=48.0, bold=False, italic=False,
    primary=RGBA(255, 255, 255, 255), alignment=2,
)


class TestInheritance:
    def test_plain_text_takes_style(self) -> None:
        eff = effective_style(make("Обычный текст"), STYLE)
        assert eff.fontname == "Arial"
        assert eff.fontsize == 48.0
        assert not eff.bold

    def test_nothing_is_marked_overridden(self) -> None:
        assert effective_style(make("Текст"), STYLE).overridden == frozenset()


class TestOverrides:
    def test_bold_on(self) -> None:
        assert effective_style(make(r"{\b1}Текст"), STYLE).bold

    def test_bold_off_explicitly(self) -> None:
        bold_style = SubtitleStyle(bold=True)
        assert not effective_style(make(r"{\b0}Текст"), bold_style).bold

    def test_bare_flag_returns_to_style(self) -> None:
        """``\\b`` без аргумента означает «как в стиле», а не «выключить»."""
        bold_style = SubtitleStyle(bold=True)
        assert effective_style(make(r"{\b}Текст"), bold_style).bold

    def test_fontsize(self) -> None:
        assert effective_style(make(r"{\fs72}Текст"), STYLE).fontsize == 72.0

    def test_fontname(self) -> None:
        assert effective_style(make(r"{\fnTimes New Roman}Текст"), STYLE).fontname == (
            "Times New Roman"
        )

    def test_primary_colour(self) -> None:
        eff = effective_style(make(r"{\c&H0000FF&}Текст"), STYLE)
        assert (eff.primary.r, eff.primary.g, eff.primary.b) == (255, 0, 0)

    def test_1c_is_the_same_as_c(self) -> None:
        eff = effective_style(make(r"{\1c&H0000FF&}Текст"), STYLE)
        assert (eff.primary.r, eff.primary.g, eff.primary.b) == (255, 0, 0)
        assert eff.is_overridden("c")

    def test_alignment(self) -> None:
        assert effective_style(make(r"{\an8}Текст"), STYLE).alignment == 8

    def test_out_of_range_alignment_is_ignored(self) -> None:
        assert effective_style(make(r"{\an99}Текст"), STYLE).alignment == 2

    def test_broken_number_falls_back(self) -> None:
        """Битые теги в чужих файлах не должны ронять расчёт."""
        assert effective_style(make(r"{\fsбольшой}Текст"), STYLE).fontsize == 48.0

    def test_overridden_is_reported(self) -> None:
        eff = effective_style(make(r"{\b1\fs60}Текст"), STYLE)
        assert eff.is_overridden("b")
        assert eff.is_overridden("fs")
        assert not eff.is_overridden("i")


class TestSeveralLeadingBlocks:
    def test_all_leading_blocks_count(self) -> None:
        """``{\\an8}{\\b1}Текст`` — оба блока описывают начало реплики."""
        eff = effective_style(make(r"{\an8}{\b1}Текст"), STYLE)
        assert eff.alignment == 8
        assert eff.bold


class TestMixed:
    def test_change_inside_line_is_mixed(self) -> None:
        eff = effective_style(make(r"Обычный {\b1}жирный"), STYLE)
        assert eff.is_mixed("b")

    def test_leading_tag_is_not_mixed(self) -> None:
        assert not effective_style(make(r"{\b1}Весь жирный"), STYLE).is_mixed("b")

    def test_mixed_keeps_the_starting_value(self) -> None:
        """Показываем то, с чего реплика начинается, — но помечаем смешанным."""
        eff = effective_style(make(r"{\b1}жирный {\b0}обычный"), STYLE)
        assert eff.bold
        assert eff.is_mixed("b")

    def test_position_tag_is_not_formatting(self) -> None:
        """``\\pos`` в середине не делает оформление смешанным."""
        eff = effective_style(make(r"Текст {\pos(10,10)}ещё"), STYLE)
        assert eff.mixed == frozenset()
