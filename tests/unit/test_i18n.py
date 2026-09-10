"""Перевод интерфейса: механизм и его устойчивость к неполноте.

Неполный каталог — нормальное состояние, а не ошибка: строки прибавляются
вместе с возможностями. Механизм обязан это переживать, иначе первый же новый
пункт меню ломал бы чужой перевод целиком.
"""

from __future__ import annotations

import json

import pytest

from sfstudio.app import i18n


@pytest.fixture(autouse=True)
def restore_language():
    """Язык глобальный — за собой надо убирать."""
    before = i18n.current_language()
    yield
    i18n.set_language(before)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """Подменяет каталог переводов на временный."""
    monkeypatch.setattr(i18n, "locale_dir", lambda: tmp_path)

    def write(code: str, data: dict) -> None:
        (tmp_path / f"{code}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    return write


class TestTranslation:
    def test_source_language_needs_no_catalog(self) -> None:
        i18n.set_language("ru")
        assert i18n.tr("Новый проект") == "Новый проект"

    def test_translation_is_applied(self, catalog) -> None:
        catalog("en", {"Новый проект": "New project"})
        assert i18n.set_language("en") == "en"
        assert i18n.tr("Новый проект") == "New project"

    def test_missing_string_stays_as_is(self, catalog) -> None:
        """Неполный каталог обязан работать — это его обычное состояние."""
        catalog("en", {"Новый проект": "New project"})
        i18n.set_language("en")
        assert i18n.tr("Настройки") == "Настройки"

    def test_empty_translation_is_ignored(self, catalog) -> None:
        """Пустое значение значит «ещё не переведено», а не «пустая подпись»."""
        catalog("en", {"Настройки": ""})
        i18n.set_language("en")
        assert i18n.tr("Настройки") == "Настройки"

    def test_switching_back(self, catalog) -> None:
        catalog("en", {"Новый проект": "New project"})
        i18n.set_language("en")
        i18n.set_language("ru")
        assert i18n.tr("Новый проект") == "Новый проект"


class TestFailureIsNotFatal:
    def test_unknown_language_falls_back(self, catalog) -> None:
        assert i18n.set_language("эльфийский") == "ru"

    def test_broken_file_falls_back(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(i18n, "locale_dir", lambda: tmp_path)
        (tmp_path / "en.json").write_text("{это не json", encoding="utf-8")
        assert i18n.set_language("en") == "ru"

    def test_wrong_shape_falls_back(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(i18n, "locale_dir", lambda: tmp_path)
        (tmp_path / "en.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert i18n.set_language("en") == "ru"

    def test_none_means_source(self) -> None:
        assert i18n.set_language(None) == "ru"


class TestProgress:
    def test_source_is_complete(self) -> None:
        assert i18n.translation_progress("ru") == 1.0

    def test_half_translated(self, catalog) -> None:
        catalog("en", {"раз": "one", "два": ""})
        assert i18n.translation_progress("en") == pytest.approx(0.5)

    def test_missing_catalog_is_zero(self) -> None:
        assert i18n.translation_progress("такого-нет") == 0.0

    def test_service_sections_do_not_count(self, catalog) -> None:
        """Раздел с устаревшими переводами — не часть работы переводчика."""
        catalog("en", {"раз": "one", "__устаревшие__": "{}"})
        assert i18n.translation_progress("en") == pytest.approx(1.0)


class TestCatalogInRepository:
    """Каталог, который лежит в поставке, должен быть пригоден."""

    def test_english_catalog_loads(self) -> None:
        path = i18n.locale_dir() / "en.json"
        if not path.is_file():
            pytest.skip("каталог ещё не собран")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and data

    def test_startup_strings_are_translated(self) -> None:
        """Стартовое окно переведено целиком — это образец для остальных."""
        path = i18n.locale_dir() / "en.json"
        if not path.is_file():
            pytest.skip("каталог ещё не собран")
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("Новый проект", "Открыть…", "Проекты"):
            assert data.get(key), f"не переведено: {key}"


class TestExtractor:
    """Сборщик строк: он дополняет каталог, а не переписывает его."""

    def test_existing_translations_survive(self, tmp_path, monkeypatch) -> None:
        import tools.extract_strings as extractor

        monkeypatch.setattr(extractor, "LOCALE", tmp_path)
        monkeypatch.setattr(extractor, "scan", lambda: ["раз", "два"])
        (tmp_path / "en.json").write_text(
            json.dumps({"раз": "one"}, ensure_ascii=False), encoding="utf-8"
        )

        extractor.update("en")
        data = json.loads((tmp_path / "en.json").read_text(encoding="utf-8"))
        assert data["раз"] == "one"
        assert data["два"] == ""

    def test_orphan_translations_are_kept(self, tmp_path, monkeypatch) -> None:
        """Строка могла переехать — терять чужую работу из-за этого нельзя."""
        import tools.extract_strings as extractor

        monkeypatch.setattr(extractor, "LOCALE", tmp_path)
        monkeypatch.setattr(extractor, "scan", lambda: ["раз"])
        (tmp_path / "en.json").write_text(
            json.dumps({"раз": "one", "исчезнувшая": "gone"}, ensure_ascii=False),
            encoding="utf-8",
        )

        extractor.update("en")
        data = json.loads((tmp_path / "en.json").read_text(encoding="utf-8"))
        assert "gone" in data.get("__устаревшие__", "")

    def test_only_tr_arguments_are_collected(self, tmp_path) -> None:
        """Каталог должен совпадать с тем, что программа ищет в нём.

        Строка, не прошедшая через ``tr``, не переведётся никогда, и её
        перевод в каталоге — обещание, которого никто не выполнит.
        """
        import tools.extract_strings as extractor

        source = tmp_path / "модуль.py"
        source.write_text(
            '"""Докстрока: её переводить не надо."""\n'
            'подпись = tr("А это подпись")\n'
            'мимо = "Эту строку никто не переведёт"\n',
            encoding="utf-8",
        )
        assert extractor.collect(source) == ["А это подпись"]

    def test_strings_without_tr_are_skipped(self, tmp_path) -> None:
        import tools.extract_strings as extractor

        source = tmp_path / "модуль.py"
        source.write_text('key = "layout_state"\n', encoding="utf-8")
        assert extractor.collect(source) == []


class TestEnglishCatalogue:
    """Английский каталог как часть поставки, а не как черновик."""

    def catalogue(self) -> dict:
        path = i18n.locale_dir() / "en.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_catalogue_is_complete(self) -> None:
        """Полупереведённый интерфейс — смесь двух языков в одном окне."""
        empty = [
            key for key, value in self.catalogue().items()
            if not key.startswith("__") and not str(value).strip()
        ]
        assert not empty, f"без перевода осталось {len(empty)}: {empty[:5]}"

    def test_every_string_in_code_is_in_the_catalogue(self) -> None:
        """Новая подпись без перевода должна ловиться здесь, а не глазами."""
        import tools.extract_strings as extractor

        catalogue = self.catalogue()
        missing = [text for text in extractor.scan() if text not in catalogue]
        assert not missing, (
            f"нет в каталоге {len(missing)} строк: {missing[:5]}. "
            "Соберите каталог: python tools/extract_strings.py en"
        )

    def test_placeholders_survive_translation(self) -> None:
        """Потерянный {0} — это исключение при форматировании, а не опечатка."""
        broken = []
        for key, value in self.catalogue().items():
            if key.startswith("__") or not isinstance(value, str):
                continue
            for index in range(4):
                mark = "{" + str(index)
                if mark in key and mark not in value:
                    broken.append((key, value))
                    break
        assert not broken, f"плейсхолдеры потеряны: {broken[:3]}"

    def test_translation_is_reported_as_complete(self) -> None:
        assert i18n.translation_progress("en") == 1.0

    def test_switching_to_english_changes_the_menu(self) -> None:
        i18n.set_language("en")
        assert i18n.tr("Файл") == "File"
        assert i18n.tr("Маркеры") == "Markers"


class TestStartupOrder:
    """Язык должен встать раньше, чем вычислятся подписи-константы.

    Часть подписей живёт в константах уровня модуля: названия выравниваний,
    цвета маркеров, имена профилей проверок. Они вычисляются один раз, при
    импорте, и язык, выбранный после этого, до них уже не доберётся — окно
    получилось бы наполовину русским. Проверяем в отдельном процессе: в этом
    модули уже импортированы, и порядок не воспроизвести.
    """

    def run_probe(self, tmp_path, code: str) -> str:
        import os
        import subprocess
        import sys

        environment = dict(os.environ, SFSTUDIO_HOME=str(tmp_path), PYTHONIOENCODING="utf-8")
        settings = tmp_path / "config" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        # Ключи в файле вложенные: «ui.language» — это ui → language.
        settings.write_text(
            json.dumps({"config_version": 1, "ui": {"language": "en"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=environment, encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def test_module_constants_speak_english(self, tmp_path) -> None:
        output = self.run_probe(tmp_path, (
            "import sfstudio.__main__\n"
            "from sfstudio.core.style import ALIGNMENT_NAMES\n"
            "from sfstudio.core.markers import MARKER_COLORS\n"
            "print(ALIGNMENT_NAMES[1], '|', MARKER_COLORS[0][1])"
        ))
        assert output == "bottom left | Blue"

    def test_command_line_speaks_english(self, tmp_path) -> None:
        """``--help`` и самопроверку читает тот же человек, что и окна."""
        output = self.run_probe(tmp_path, (
            "import sys\n"
            "sys.argv = ['sfstudio', '--selftest']\n"
            "from sfstudio.__main__ import main\n"
            "main(['--selftest'])"
        ))
        assert "Checks:" in output
