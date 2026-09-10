"""Стиль субтитра (секция ``[V4+ Styles]``)."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sfstudio.app.i18n import tr
from sfstudio.core.color import RGBA

__all__ = ["ALIGNMENT_NAMES", "BorderStyle", "SubtitleStyle"]


class BorderStyle:
    """Значения поля ``BorderStyle`` в ASS."""

    OUTLINE = 1  # обводка + тень
    OPAQUE_BOX = 3  # непрозрачная плашка


#: Numpad-нотация выравнивания ASS.
ALIGNMENT_NAMES: dict[int, str] = {
    1: tr('снизу слева'),
    2: tr('снизу по центру'),
    3: tr('снизу справа'),
    4: tr('по центру слева'),
    5: tr('по центру'),
    6: tr('по центру справа'),
    7: tr('сверху слева'),
    8: tr('сверху по центру'),
    9: tr('сверху справа'),
}


@dataclass(slots=True)
class SubtitleStyle:
    """Именованный стиль. Поля соответствуют формату ASS v4.00+."""

    name: str = "Default"
    fontname: str = "Arial"
    fontsize: float = 48.0

    primary: RGBA = RGBA(255, 255, 255, 255)
    secondary: RGBA = RGBA(255, 0, 0, 255)
    outline_color: RGBA = RGBA(0, 0, 0, 255)
    back_color: RGBA = RGBA(0, 0, 0, 128)

    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False

    scale_x: float = 100.0
    scale_y: float = 100.0
    spacing: float = 0.0
    angle: float = 0.0

    border_style: int = BorderStyle.OUTLINE
    outline: float = 2.0
    shadow: float = 2.0

    alignment: int = 2
    margin_l: int = 20
    margin_r: int = 20
    margin_v: int = 20
    encoding: int = 1

    def copy_as(self, new_name: str) -> SubtitleStyle:
        return replace(self, name=new_name)

    @property
    def alignment_name(self) -> str:
        return ALIGNMENT_NAMES.get(self.alignment, tr('неизвестно'))

    @property
    def anchor_col(self) -> int:
        """Колонка привязки: 0 = слева, 1 = центр, 2 = справа."""
        return (self.alignment - 1) % 3

    @property
    def anchor_row(self) -> int:
        """Строка привязки: 0 = снизу, 1 = по центру, 2 = сверху."""
        return (self.alignment - 1) // 3
