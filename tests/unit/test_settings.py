"""Тесты настроек и шаблонов стилей."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sfstudio.app.settings import CONFIG_VERSION, DEFAULTS, Settings, SettingsError
from sfstudio.core.color import RGBA
from sfstudio.core.style import SubtitleStyle
from sfstudio.services.style_presets import (
    BUILTIN_PRESETS,
    PresetError,
    PresetLibrary,
    StylePreset,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path / "settings.json")


class TestSettingsBasics:
    def test_defaults_available_before_save(self, settings: Settings) -> None:
        assert settings.get("ui.theme") == "dark"
        assert settings.get("editing.min_gap_frames") == 2

    def test_missing_key_returns_default(self, settings: Settings) -> None:
        assert settings.get("нет.такого", "запасное") == "запасное"

    def test_set_and_get(self, settings: Settings) -> None:
        settings.set("ui.theme", "light")
        assert settings.get("ui.theme") == "light"

    def test_set_creates_missing_section(self, settings: Settings) -> None:
        settings.set("новый.раздел.ключ", 42)
        assert settings.get("новый.раздел.ключ") == 42

    def test_dirty_flag(self, settings: Settings) -> None:
        assert not settings.dirty
        settings.set("ui.theme", "light")
        assert settings.dirty
        settings.save()
        assert not settings.dirty

    def test_setting_same_value_is_not_dirty(self, settings: Settings) -> None:
        settings.set("ui.theme", settings.get("ui.theme"))
        assert not settings.dirty

    def test_contains(self, settings: Settings) -> None:
        assert "ui.theme" in settings
        assert "ui.нет" not in settings


class TestPersistence:
    def test_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        first = Settings(path)
        first.set("ui.theme", "light")
        first.set("media.volume", 55.0)
        first.save()

        second = Settings(path)
        assert second.get("ui.theme") == "light"
        assert second.get("media.volume") == 55.0

    def test_save_is_atomic(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        settings = Settings(path)
        settings.set("ui.theme", "light")
        settings.save()
        assert not list(tmp_path.glob("*.tmp")), "остался временный файл"

    def test_unknown_keys_do_not_erase_new_defaults(self, tmp_path: Path) -> None:
        """Конфиг прошлой версии не должен уносить появившиеся позже ключи."""
        path = tmp_path / "s.json"
        path.write_text(
            json.dumps({"config_version": CONFIG_VERSION, "ui": {"theme": "light"}}),
            encoding="utf-8",
        )
        settings = Settings(path)
        assert settings.get("ui.theme") == "light"
        assert settings.get("editing.min_gap_frames") == DEFAULTS["editing"]["min_gap_frames"]

    def test_broken_file_does_not_break_startup(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text("{это не json", encoding="utf-8")
        settings = Settings(path)
        assert settings.get("ui.theme") == "dark"
        assert (tmp_path / "s.json.broken").exists(), "битый файл должен сохраниться"

    def test_non_object_json_is_quarantined(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        settings = Settings(path)
        assert settings.get("ui.theme") == "dark"

    def test_newer_version_is_refused(self, tmp_path: Path) -> None:
        """Конфиг от будущей версии нельзя молча перезаписать — данные потеряются."""
        path = tmp_path / "s.json"
        path.write_text(
            json.dumps({"config_version": CONFIG_VERSION + 5}), encoding="utf-8"
        )
        with pytest.raises(SettingsError, match="новой версии"):
            Settings(path)

    def test_save_without_changes_does_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        Settings(path).save()
        assert not path.exists(), "пустое сохранение не должно создавать файл"


class TestRecentFiles:
    def test_push_and_read(self, settings: Settings, tmp_path: Path) -> None:
        real = tmp_path / "film.ass"
        real.write_text("x", encoding="utf-8")
        settings.push_recent("subtitles", real)
        assert settings.recent("subtitles") == [real]

    def test_most_recent_first(self, settings: Settings, tmp_path: Path) -> None:
        paths = []
        for i in range(3):
            p = tmp_path / f"{i}.ass"
            p.write_text("x", encoding="utf-8")
            paths.append(p)
            settings.push_recent("subtitles", p)
        assert settings.recent("subtitles")[0] == paths[-1]

    def test_no_duplicates(self, settings: Settings, tmp_path: Path) -> None:
        p = tmp_path / "a.ass"
        p.write_text("x", encoding="utf-8")
        settings.push_recent("subtitles", p)
        settings.push_recent("subtitles", p)
        assert len(settings.get("recent.subtitles")) == 1

    def test_missing_files_hidden_but_kept(self, settings: Settings, tmp_path: Path) -> None:
        """Файл на съёмном диске может вернуться — запись не удаляем."""
        missing = tmp_path / "нет.ass"
        settings.push_recent("subtitles", missing)
        assert settings.recent("subtitles") == []
        assert settings.recent("subtitles", existing_only=False) == [missing]

    def test_list_is_capped(self, settings: Settings, tmp_path: Path) -> None:
        for i in range(40):
            settings.push_recent("subtitles", tmp_path / f"{i}.ass")
        assert len(settings.get("recent.subtitles")) <= 12


class TestReset:
    def test_reset_section(self, settings: Settings) -> None:
        settings.set("ui.theme", "light")
        settings.reset("ui")
        assert settings.get("ui.theme") == "dark"

    def test_reset_all(self, settings: Settings) -> None:
        settings.set("ui.theme", "light")
        settings.set("media.volume", 1.0)
        settings.reset()
        assert settings.get("ui.theme") == "dark"
        assert settings.get("media.volume") == DEFAULTS["media"]["volume"]


# --------------------------------------------------------------------------- #


@pytest.fixture
def library(tmp_path: Path) -> PresetLibrary:
    return PresetLibrary(directory=tmp_path / "presets")


class TestPresetLibrary:
    def test_builtins_present(self, library: PresetLibrary) -> None:
        names = library.names()
        assert len(names) == len(BUILTIN_PRESETS)
        assert "Классические белые" in names

    def test_builtins_are_marked(self, library: PresetLibrary) -> None:
        assert all(p.builtin for p in library.all())

    def test_save_and_reload(self, library: PresetLibrary) -> None:
        preset = StylePreset.from_style(
            SubtitleStyle(name="Мой", fontname="Georgia", fontsize=52),
            description="Проверка",
        )
        library.save(preset)

        fresh = PresetLibrary(directory=library.directory)
        loaded = fresh.get("Мой")
        assert loaded is not None
        assert loaded.style.fontname == "Georgia"
        assert loaded.style.fontsize == 52
        assert loaded.description == "Проверка"
        assert not loaded.builtin

    def test_colours_survive_roundtrip(self, library: PresetLibrary) -> None:
        style = SubtitleStyle(
            name="Цветной",
            primary=RGBA(12, 34, 56, 200),
            outline_color=RGBA(200, 100, 50),
        )
        library.save(StylePreset.from_style(style))
        loaded = PresetLibrary(directory=library.directory).get("Цветной")
        assert loaded.style.primary == RGBA(12, 34, 56, 200)
        assert loaded.style.outline_color == RGBA(200, 100, 50)

    def test_user_preset_overrides_builtin(self, library: PresetLibrary) -> None:
        custom = StylePreset.from_style(
            SubtitleStyle(name="Жёлтые", fontsize=99), description="Мой вариант"
        )
        library.save(custom)
        got = library.get("Жёлтые")
        assert got.style.fontsize == 99
        assert not got.builtin
        assert len(library.all()) == len(BUILTIN_PRESETS), "перекрытие не должно плодить"

    def test_delete_user_preset(self, library: PresetLibrary) -> None:
        library.save(StylePreset.from_style(SubtitleStyle(name="Временный")))
        assert library.delete("Временный")
        assert library.get("Временный") is None

    def test_builtin_cannot_be_deleted(self, library: PresetLibrary) -> None:
        """Встроенный вернулся бы при перезапуске — удаление выглядело бы сломанным."""
        assert not library.delete("Жёлтые")
        assert library.get("Жёлтые") is not None

    def test_empty_name_refused(self, library: PresetLibrary) -> None:
        with pytest.raises(PresetError, match="имя"):
            library.save(StylePreset.from_style(SubtitleStyle(name="   ")))

    def test_unsafe_name_becomes_valid_filename(self, library: PresetLibrary) -> None:
        preset = StylePreset.from_style(SubtitleStyle(name='Мой/стиль: "новый"'))
        path = library.save(preset)
        assert path.exists()
        assert PresetLibrary(directory=library.directory).get('Мой/стиль: "новый"')

    def test_cyrillic_names_stay_readable(self, library: PresetLibrary) -> None:
        path = library.save(StylePreset.from_style(SubtitleStyle(name="Крупные жёлтые")))
        assert "Крупные" in path.name

    def test_broken_file_is_skipped(self, library: PresetLibrary) -> None:
        """Один испорченный шаблон не должен уносить всю библиотеку."""
        library.save(StylePreset.from_style(SubtitleStyle(name="Хороший")))
        (library.directory / "broken.json").write_text("{не json", encoding="utf-8")

        fresh = PresetLibrary(directory=library.directory)
        assert fresh.get("Хороший") is not None

    def test_missing_directory_is_fine(self, tmp_path: Path) -> None:
        library = PresetLibrary(directory=tmp_path / "нет" / "такого")
        assert library.names()  # встроенные всё равно доступны


class TestPresetApplication:
    def test_apply_keeps_style_name(self) -> None:
        """Шаблон меняет вид, но не переименовывает стиль.

        Иначе события, ссылающиеся на стиль по имени, остались бы без него.
        """
        preset = StylePreset.from_style(SubtitleStyle(name="Шаблон", fontsize=72))
        target = SubtitleStyle(name="Default", fontsize=20)
        result = preset.apply_to(target)
        assert result.name == "Default"
        assert result.fontsize == 72

    def test_as_style_uses_given_name(self) -> None:
        preset = StylePreset.from_style(SubtitleStyle(name="Шаблон", fontsize=72))
        assert preset.as_style("Новый").name == "Новый"

    def test_from_style_copies(self) -> None:
        """Шаблон не должен меняться вслед за стилем документа."""
        style = SubtitleStyle(name="Исходный", fontsize=40)
        preset = StylePreset.from_style(style)
        style.fontsize = 90
        assert preset.style.fontsize == 40

    def test_invalid_alignment_is_clamped(self, library: PresetLibrary) -> None:
        path = library.directory
        path.mkdir(parents=True, exist_ok=True)
        (path / "weird.json").write_text(
            json.dumps({"name": "Странный", "style": {"alignment": 42}}),
            encoding="utf-8",
        )
        loaded = PresetLibrary(directory=path).get("Странный")
        assert loaded is not None
        assert 1 <= loaded.style.alignment <= 9

    def test_missing_style_section_is_error(self) -> None:
        with pytest.raises(PresetError, match="style"):
            StylePreset.from_dict({"name": "Пустой"})


class TestByteOrderMark:
    """Файл настроек правят руками, а «Блокнот» пишет метку порядка байтов.

    Без учёта этой метки настройки уезжали в карантин целиком: человек
    поправил одну строку в блокноте — и остался без всех своих настроек,
    причём без единого сообщения.
    """

    def test_settings_with_bom_are_read(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(
            '{"config_version": 1, "ui": {"theme": "light"}}', encoding="utf-8-sig"
        )
        assert Settings(path).get("ui.theme") == "light"

    def test_file_with_bom_is_not_quarantined(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text('{"config_version": 1}', encoding="utf-8-sig")
        Settings(path)
        assert not path.with_suffix(".json.broken").exists()

    def test_plain_utf8_still_works(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text('{"config_version": 1, "ui": {"theme": "light"}}',
                        encoding="utf-8")
        assert Settings(path).get("ui.theme") == "light"

    def test_genuinely_broken_file_is_still_quarantined(self, tmp_path: Path) -> None:
        """Терпимость к метке не должна превращаться в терпимость к мусору."""
        path = tmp_path / "settings.json"
        path.write_text("{это не json", encoding="utf-8-sig")
        Settings(path)
        assert path.with_suffix(".json.broken").exists()
