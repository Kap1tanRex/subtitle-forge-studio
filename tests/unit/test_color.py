"""Тесты цвета и инверсии альфы ASS."""

from __future__ import annotations

import pytest

from sfstudio.core.color import RGBA


def test_white_opaque() -> None:
    assert RGBA(255, 255, 255, 255).to_ass() == "&H00FFFFFF"


def test_alpha_is_inverted_relative_to_ass() -> None:
    """Внутри 255 = непрозрачный, в ASS 00 = непрозрачный."""
    assert RGBA(0, 0, 0, 255).to_ass() == "&H00000000"
    assert RGBA(0, 0, 0, 0).to_ass() == "&HFF000000"
    assert RGBA(0, 0, 0, 128).to_ass() == "&H7F000000"


def test_channel_order_is_bgr() -> None:
    """ASS пишет BBGGRR, а не RRGGBB — классический источник путаницы."""
    assert RGBA(255, 0, 0, 255).to_ass() == "&H000000FF"  # красный
    assert RGBA(0, 0, 255, 255).to_ass() == "&H00FF0000"  # синий


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("&H00FFFFFF", RGBA(255, 255, 255, 255)),
        ("&HFF000000", RGBA(0, 0, 0, 0)),
        ("&H000000FF&", RGBA(255, 0, 0, 255)),
        ("&HFFFFFF", RGBA(255, 255, 255, 255)),  # без альфы
        ("&H0", RGBA(0, 0, 0, 255)),             # огрызок
        ("00FFFFFF", RGBA(255, 255, 255, 255)),  # без префикса
    ],
)
def test_from_ass_tolerant(text: str, expected: RGBA) -> None:
    assert RGBA.from_ass(text) == expected


@pytest.mark.parametrize("value", [RGBA(1, 2, 3, 4), RGBA(255, 128, 0, 200), RGBA(0, 0, 0, 0)])
def test_roundtrip(value: RGBA) -> None:
    assert RGBA.from_ass(value.to_ass()) == value


def test_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        RGBA.from_ass("не цвет")


def test_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        RGBA(300, 0, 0, 0)


def test_alpha_tag_parsing() -> None:
    assert RGBA.alpha_from_ass("&H00&") == 255
    assert RGBA.alpha_from_ass("&HFF&") == 0


def test_hex_for_widgets() -> None:
    assert RGBA(255, 128, 0).to_hex() == "#FF8000"
