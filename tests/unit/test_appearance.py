"""Внешний вид: темы из файлов, акцентный цвет, анимации."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication, QDialog

from sfstudio.app.settings import Settings
from sfstudio.app.storage import themes_dir
from sfstudio.ui import motion
from sfstudio.ui.appearance import build, current_pack, themes_for
from sfstudio.ui.theme import ACCENTS, BUILTIN, DARK, LIGHT, is_dark, mix, with_accent
from sfstudio.ui.theme_pack import SUFFIX, example_text, palette_of, read_pack


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path) -> Settings:
    store = Settings(tmp_path / "settings.json")
    store.set("storage.mode", "custom")
    store.set("storage.folder", str(tmp_path))
    return store


def contrast(first: str, second: str) -> float:
    """Отношение контраста по WCAG: 1 — цвета совпали, 21 — чёрный к белому."""
    from sfstudio.core.color import RGBA

    def channel(value: int) -> float:
        share = value / 255
        return share / 12.92 if share <= 0.04045 else ((share + 0.055) / 1.055) ** 2.4

    def luminance(hex_value: str) -> float:
        rgba = RGBA.from_hex(hex_value)
        return (0.2126 * channel(rgba.r) + 0.7152 * channel(rgba.g)
                + 0.0722 * channel(rgba.b))

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def write_theme(settings: Settings, name: str, data: dict) -> None:
    folder = themes_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + SUFFIX)).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class TestColours:
    def test_mixing_moves_towards_the_second_colour(self) -> None:
        assert mix("#000000", "#FFFFFF", 0.0) == "#000000"
        assert mix("#000000", "#FFFFFF", 1.0) == "#FFFFFF"
        assert mix("#000000", "#FFFFFF", 0.5) == "#808080"

    def test_darkness_is_decided_by_the_background(self) -> None:
        """Не названием темы: файловая тема может зваться как угодно."""
        assert is_dark(DARK)
        assert not is_dark(LIGHT)

    def test_accent_pulls_the_derived_colours_with_it(self) -> None:
        """Выделение и направляющие выводятся из акцента, а не задаются
        отдельно: иначе человеку пришлось бы подбирать три цвета вместо
        одного."""
        painted = with_accent(DARK, "#DE5A9B")
        assert painted.accent == "#DE5A9B"
        assert painted.guide == "#DE5A9B"
        assert painted.accent_muted not in (DARK.accent_muted, "#DE5A9B")

    def test_muted_accent_stays_on_the_side_of_the_background(self) -> None:
        """На тёмной теме выделение темнее акцента, на светлой — светлее."""
        from sfstudio.core.color import RGBA

        accent = "#4C8DFF"
        on_dark = RGBA.from_hex(with_accent(DARK, accent).accent_muted).luminance
        on_light = RGBA.from_hex(with_accent(LIGHT, accent).accent_muted).luminance
        pure = RGBA.from_hex(accent).luminance
        assert on_dark < pure < on_light

    @pytest.mark.parametrize("theme", sorted(BUILTIN))
    @pytest.mark.parametrize("accent", [value for _title, value in ACCENTS])
    def test_text_stays_readable_on_any_accent(self, theme, accent) -> None:
        """Выделенная строка должна читаться при любом сочетании.

        Выбор цвета — дело вкуса ровно до тех пор, пока текст на выделении
        виден. Проверяем каждый готовый акцент на каждой встроенной теме по
        обычному порогу доступности; сейчас худшая пара даёт 9,7 при
        требуемых 4,5, и запас нужен, чтобы правка формулы не съела его
        незаметно.
        """
        palette = with_accent(BUILTIN[theme], accent)
        assert contrast(palette.text_primary, palette.accent_muted) >= 4.5

    def test_broken_accent_leaves_the_palette_alone(self) -> None:
        """Цвет правят руками в settings.json; мусор там не повод падать."""
        assert with_accent(DARK, "не цвет") == DARK
        assert with_accent(DARK, "") == DARK


class TestThemeFiles:
    def test_file_theme_appears_next_to_builtin(self, settings) -> None:
        write_theme(settings, "океан", {
            "name": "ocean", "title": "Океан", "base": "dark",
            "colors": {"bg_base": "#0B1A24"},
        })
        packs = themes_for(settings)
        assert "ocean" in packs
        assert {"dark", "light", "sepia", "contrast"} <= set(packs)

    def test_unset_colours_come_from_the_base(self, settings) -> None:
        """Иначе автору темы пришлось бы перечислять все семнадцать полей."""
        write_theme(settings, "почти", {
            "name": "almost", "title": "Почти светлая", "base": "light",
            "colors": {"accent": "#A6612B"},
        })
        palette = palette_of(themes_for(settings)["almost"])
        assert palette.accent == "#A6612B"
        assert palette.bg_base == LIGHT.bg_base

    def test_bad_colour_marks_the_theme_broken(self, settings) -> None:
        write_theme(settings, "битая", {"name": "broken", "colors": {"accent": "синий"}})
        pack = themes_for(settings)["broken"]
        assert pack.error
        assert "accent" in pack.error

    def test_broken_theme_does_not_break_the_window(self, settings) -> None:
        """Сломанная тема — повод показать тёмную, а не отказаться работать.

        И не повод пропустить негодный цвет дальше: строка «синий» в таблице
        стилей молча превратила бы выделение в чёрный прямоугольник.
        """
        from sfstudio.core.color import RGBA

        write_theme(settings, "битая", {"name": "broken", "colors": {"accent": "синий"}})
        settings.set("ui.theme", "broken")
        palette = build(settings)[0]
        assert palette.bg_base == DARK.bg_base
        assert palette.accent == DARK.accent
        RGBA.from_hex(palette.accent)  # разбирается, значит цвет настоящий

    def test_unknown_theme_falls_back(self, settings) -> None:
        settings.set("ui.theme", "такой-нет")
        assert current_pack(settings).name == "dark"

    def test_unknown_keys_are_ignored(self, settings) -> None:
        """Тема может быть написана под будущую версию с новыми полями."""
        write_theme(settings, "будущая", {
            "name": "future", "colors": {"bg_base": "#101010", "sidebar_glow": "#FF0000"},
        })
        pack = themes_for(settings)["future"]
        assert not pack.error
        assert palette_of(pack).bg_base == "#101010"

    def test_theme_can_add_its_own_stylesheet(self, settings) -> None:
        write_theme(settings, "своя", {
            "name": "own", "base": "dark", "qss": "QToolBar { border-bottom: 0; }",
        })
        settings.set("ui.theme", "own")
        _palette, qss, _key = build(settings)
        assert "border-bottom: 0" in qss
        # Добавка идёт последней: автор темы должен уметь перекрыть наши правила.
        assert qss.index("border-bottom: 0") > qss.index("QMainWindow::separator")

    def test_a_file_theme_may_replace_a_builtin_one(self, settings) -> None:
        write_theme(settings, "тёмная", {
            "name": "dark", "title": "Моя тёмная", "colors": {"bg_base": "#000010"},
        })
        assert palette_of(themes_for(settings)["dark"]).bg_base == "#000010"

    def test_damaged_json_is_reported_not_raised(self, settings, tmp_path) -> None:
        folder = themes_dir(settings)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("рваная" + SUFFIX)).write_text("{ не json", encoding="utf-8")
        pack = next(p for p in themes_for(settings).values() if p.name == "рваная")
        assert "json" in pack.error

    def test_example_is_a_valid_theme(self, settings, tmp_path) -> None:
        """Образец кладут человеку, чтобы он правил его, а не читал описание."""
        target = tmp_path / ("образец" + SUFFIX)
        target.write_text(example_text(), encoding="utf-8")
        pack = read_pack(target)
        assert not pack.error
        assert pack.colors


class TestAppearanceKey:
    """Таблица стилей ставится, только когда вид действительно другой."""

    def test_same_settings_give_the_same_key(self, settings) -> None:
        assert build(settings)[2] == build(settings)[2]

    def test_accent_changes_the_key(self, settings) -> None:
        before = build(settings)[2]
        settings.set("ui.accent", "#3FB950")
        assert build(settings)[2] != before

    def test_theme_changes_the_key(self, settings) -> None:
        before = build(settings)[2]
        settings.set("ui.theme", "sepia")
        assert build(settings)[2] != before


class TestMotion:
    def test_setting_wins_over_the_system(self) -> None:
        assert motion.resolve(True) is True
        assert motion.resolve(False) is False

    def test_untouched_setting_asks_the_system(self) -> None:
        """Кто выключил анимации в Windows, не должен выключать их и здесь."""
        assert motion.resolve(None) == motion.system_animations_on()

    def test_dialogs_fade_in_when_enabled(self, qapp) -> None:
        motion.install_effects(qapp, True)
        dialog = QDialog()
        dialog.show()
        qapp.processEvents()
        assert dialog.windowOpacity() < 1.0
        dialog.close()

    def test_dialogs_appear_at_once_when_disabled(self, qapp) -> None:
        motion.install_effects(qapp, False)
        dialog = QDialog()
        dialog.show()
        qapp.processEvents()
        assert dialog.windowOpacity() == 1.0
        dialog.close()
        motion.install_effects(qapp, True)

    def test_switch_stops_each_animation_itself(self, qapp) -> None:
        """Выключатель проверяется в каждой анимации, а не только при
        установке фильтра: анимации зовут и напрямую, из кода окна.

        Фильтр здесь намеренно оставлен на месте — так проверяется, что и
        при показе окна выключенная анимация ничего не делает.
        """
        motion.set_enabled(False)
        dialog = QDialog()
        dialog.resize(120, 80)
        try:
            dialog.show()
            qapp.processEvents()
            assert dialog.windowOpacity() == 1.0
            assert motion.fade_in(dialog) is None
            assert motion.flash(dialog, "#FFFFFF") is None
        finally:
            motion.set_enabled(True)
            dialog.close()

    def test_scrolling_without_animation_still_arrives(self, qapp) -> None:
        """Выключенные анимации не должны означать «прокрутка не работает»."""
        from PySide6.QtWidgets import QListWidget

        listing = QListWidget()
        for i in range(200):
            listing.addItem(f"строка {i}")
        listing.resize(200, 100)
        listing.show()
        qapp.processEvents()

        motion.set_enabled(False)
        motion.smooth_scroll(listing, 50)
        assert listing.verticalScrollBar().value() == 50
        motion.set_enabled(True)
        listing.close()

    def test_durations_stay_short(self) -> None:
        """Анимация дольше четверти секунды начинает мешать работать."""
        assert all(ms <= 250 for key, ms in motion.DURATION.items() if key != "highlight")
