"""Кузница стилей: поля, предпросмотр и применение к документу."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sfstudio.core.color import RGBA
from sfstudio.core.style import BorderStyle, SubtitleStyle
from sfstudio.ui.style_forge import (
    AlignmentPad,
    PresetCards,
    PreviewBackground,
    StyleForge,
)

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def style() -> SubtitleStyle:
    return SubtitleStyle(
        name="Default", fontname="Arial", fontsize=48,
        primary=RGBA(255, 255, 255), outline_color=RGBA(0, 0, 0),
        outline=2.5, shadow=1.5,
    )


@pytest.fixture
def forge(qapp: QApplication, style: SubtitleStyle) -> StyleForge:
    return StyleForge(style)


def shown(widget, width: int, height: int, qapp: QApplication):
    """Показывает виджет нужного размера.

    Без show Qt не доставляет resizeEvent, а вся отзывчивая раскладка
    построена именно на нём — проверять её на невидимом виджете бессмысленно.
    """
    widget.resize(width, height)
    widget.show()
    qapp.processEvents()
    return widget


class TestAlignmentPad:
    def test_layout_matches_the_numpad(self, qapp) -> None:
        """Нотация ASS — та же, что на цифровой клавиатуре."""
        pad = AlignmentPad()
        assert set(pad._buttons) == set(range(1, 10))

    def test_bottom_centre_is_the_default(self, qapp) -> None:
        assert AlignmentPad().alignment() == 2

    def test_choice_is_exclusive(self, qapp) -> None:
        pad = AlignmentPad()
        pad.set_alignment(9)
        checked = [key for key, b in pad._buttons.items() if b.isChecked()]
        assert checked == [9]

    def test_change_is_announced(self, qapp) -> None:
        pad = AlignmentPad()
        seen = []
        pad.changed.connect(seen.append)
        pad.set_alignment(7)
        assert seen == [7]

    def test_same_value_is_not_announced_twice(self, qapp) -> None:
        """Иначе каждое обновление полей считалось бы правкой."""
        pad = AlignmentPad()
        pad.set_alignment(7)
        seen = []
        pad.changed.connect(seen.append)
        pad.set_alignment(7)
        assert seen == []

    def test_unknown_value_is_ignored(self, qapp) -> None:
        pad = AlignmentPad()
        pad.set_alignment(42)
        assert pad.alignment() == 2

    def test_every_button_explains_itself(self, qapp) -> None:
        """Цифра сама по себе ничего не говорит тому, кто не знает ASS."""
        pad = AlignmentPad()
        assert all(button.toolTip() for button in pad._buttons.values())


class TestPresetCards:
    def test_every_builtin_has_a_card(self, qapp) -> None:
        from sfstudio.services.style_presets import BUILTIN_PRESETS

        assert len(PresetCards()._buttons) == len(BUILTIN_PRESETS)

    def test_choice_names_the_preset(self, qapp) -> None:
        cards = PresetCards()
        seen = []
        cards.chosen.connect(seen.append)
        cards._buttons[0].click()
        assert len(seen) == 1

    def test_narrow_column_holds_one_card(self, qapp) -> None:
        """Во вкладке инспектора карточки идут в столбик."""
        assert PresetCards.columns_for(150) == 1

    def test_wide_window_holds_several(self, qapp) -> None:
        assert PresetCards.columns_for(700) >= 3

    def test_zero_width_still_gives_a_column(self, qapp) -> None:
        """Ноль колонок обрушил бы раскладку делением на ноль."""
        assert PresetCards.columns_for(0) == 1

    def test_resize_rebuilds_the_grid(self, qapp) -> None:
        cards = shown(PresetCards(), 700, 200, qapp)
        assert cards._columns == PresetCards.columns_for(cards.width())


class TestFields:
    def test_fields_show_the_style(self, forge: StyleForge) -> None:
        assert forge.size_spin.value() == 48
        assert forge.outline_spin.value() == pytest.approx(2.5)
        assert forge.pad.alignment() == 2

    def test_edited_fields_come_back(self, forge: StyleForge) -> None:
        forge.size_spin.setValue(72)
        forge.pad.set_alignment(8)
        forge.box_check.setChecked(True)

        result = forge.style()
        assert result.fontsize == 72
        assert result.alignment == 8
        assert result.border_style == BorderStyle.OPAQUE_BOX

    def test_slider_and_field_stay_together(self, forge: StyleForge) -> None:
        forge.size_slider.setValue(64)
        assert forge.size_spin.value() == 64
        forge.size_spin.setValue(30)
        assert forge.size_slider.value() == 30

    def test_style_name_is_not_lost(self, forge: StyleForge) -> None:
        """Имя стиля — то, на что ссылаются реплики; менять его тут нечего."""
        forge.size_spin.setValue(60)
        assert forge.style().name == "Default"

    def test_changes_are_announced(self, forge: StyleForge) -> None:
        seen = []
        forge.style_changed.connect(seen.append)
        forge.size_spin.setValue(50)
        assert seen and seen[-1].fontsize == 50

    def test_loading_a_style_is_silent(self, forge: StyleForge) -> None:
        """Иначе подстановка значений считалась бы правкой человека."""
        seen = []
        forge.style_changed.connect(seen.append)
        forge.set_style(SubtitleStyle(fontsize=99))
        assert seen == []


class TestPresetApplication:
    def test_preset_fills_the_fields(self, forge: StyleForge) -> None:
        forge._apply_preset("Крупные для телевизора")
        assert forge.size_spin.value() == 68

    def test_preset_keeps_the_style_name(self, forge: StyleForge) -> None:
        forge._apply_preset("Жёлтые")
        assert forge.style().name == "Default"

    def test_unknown_preset_changes_nothing(self, forge: StyleForge) -> None:
        before = forge.style()
        forge._apply_preset("такого набора нет")
        assert forge.style() == before


class TestPreview:
    def test_style_line_is_shown(self, forge: StyleForge) -> None:
        assert forge.line.toPlainText().startswith("Style: Default,Arial,48")

    def test_unknown_font_is_not_replaced(self, qapp) -> None:
        """Файл мог прийти с машины, где этот шрифт есть, а здесь его нет.

        Список шрифтов показывает только установленные и молча подменяет
        отсутствующий на похожий. Записать эту подмену в файл значило бы
        испортить чужое оформление одним открытием вкладки.
        """
        forge = StyleForge(SubtitleStyle(name="Default", fontname="Небывалый Гротеск"))
        assert forge.style().fontname == "Небывалый Гротеск"

    def test_style_line_follows_the_fields(self, forge: StyleForge) -> None:
        forge.size_spin.setValue(77)
        assert ",77," in forge.line.toPlainText()

    def test_background_switches(self, forge: StyleForge) -> None:
        forge.preview.set_background(PreviewBackground.LIGHT)
        assert forge.preview._background == PreviewBackground.LIGHT

    def test_preview_draws_without_crashing(self, forge: StyleForge) -> None:
        forge.resize(900, 600)
        assert not forge.grab().isNull()

    def test_preview_shows_the_given_text(self, forge: StyleForge) -> None:
        """Оформление проверяют на своей реплике, а не на образце."""
        forge.set_preview_text("Своя реплика")
        assert forge.preview._doc.events[0].text == "Своя реплика"

    def test_empty_text_falls_back_to_the_sample(self, forge: StyleForge) -> None:
        forge.set_preview_text("")
        assert forge.preview._doc.events[0].text


class TestResponsiveLayout:
    def test_narrow_puts_the_frame_below(self, forge: StyleForge, qapp) -> None:
        shown(forge, 420, 800, qapp)
        assert forge._splitter.orientation() == Qt.Vertical

    def test_wide_puts_the_frame_beside(self, forge: StyleForge, qapp) -> None:
        shown(forge, 1100, 700, qapp)
        assert forge._splitter.orientation() == Qt.Horizontal


class TestApplying:
    def test_apply_to_all_is_announced(self, forge: StyleForge) -> None:
        seen = []
        forge.apply_requested.connect(lambda _style, to_all: seen.append(to_all))
        forge.apply_all_button.click()
        assert seen == [True]

    def test_apply_to_selection_is_announced(self, forge: StyleForge) -> None:
        seen = []
        forge.apply_requested.connect(lambda _style, to_all: seen.append(to_all))
        forge.apply_selected_button.click()
        assert seen == [False]

    def test_applied_style_carries_the_fields(self, forge: StyleForge) -> None:
        forge.size_spin.setValue(64)
        seen = []
        forge.apply_requested.connect(lambda style, _to_all: seen.append(style))
        forge.apply_all_button.click()
        assert seen[0].fontsize == 64
