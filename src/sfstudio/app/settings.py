"""Пользовательские настройки: хранение, версионирование, миграции.

JSON, а не ``QSettings``: настройки нужны и там, где Qt не поднят (тесты,
консольные сценарии), а формат должен быть читаемым — чтобы пользователь мог
поправить или перенести конфиг руками.

Три правила, без которых хранилище рано или поздно теряет данные:

1. **Запись атомарная.** Сначала во временный файл рядом, потом переименование.
   Прерванная запись не оставит обрезанный конфиг, из-за которого приложение
   не запустится.
2. **Версия и миграции.** У конфига есть ``config_version``; при повышении
   выполняются пошаговые миграторы. Понижение версии — отказ с сообщением,
   а не молчаливое затирание: конфиг от новой версии может содержать данные,
   которые старая не понимает и потеряет при первом сохранении.
3. **Битый файл не роняет запуск.** Повреждённый JSON откладывается в сторону
   с суффиксом, работа продолжается со значениями по умолчанию.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sfstudio.platform.paths import config_dir

__all__ = ["CONFIG_VERSION", "Settings", "SettingsError"]

CONFIG_VERSION = 1
FILENAME = "settings.json"

#: Значения по умолчанию. Всё, чего нет в файле, берётся отсюда.
DEFAULTS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "ui": {
        "theme": "dark",
        # Акцентный цвет поверх темы. Пусто — тот, что задан в самой теме.
        "accent": "",
        # Плавные появления и переходы. По умолчанию берётся из системной
        # настройки: тот, кто выключил анимации в Windows, не должен
        # выключать их ещё и здесь.
        "animations": None,
        "layout_state": None,        # base64 из QMainWindow.saveState
        "layout_geometry": None,     # base64 из QMainWindow.saveGeometry
        "layout_preset": "default",
        "table_density": "comfortable",
        # Масштаб шрифта интерфейса в процентах. Системного масштабирования
        # Windows хватает не всем: на 4K оно часто выставлено на 150%, а
        # программа всё равно мелкая — там масштабируется не всё.
        "font_scale": 100,
        # Язык интерфейса. Пусто и "ru" — оригинал, каталог не нужен.
        "language": "ru",
        # Только отличия от умолчаний: полный список пришлось бы
        # чинить при каждом переименовании команды.
        "shortcuts": {},
    },
    "qc": {
        "profile": "general",
        "panel_visible": False,
        "min_severity": 0,
    },
    "subtitles": {
        # Шрифт новых стилей и новых проектов. Пусто — Arial: он есть
        # практически везде и содержит кириллицу, а подставлять что-то
        # экзотическое значило бы получить прямоугольники на чужой машине.
        "default_font": "Arial",
        "default_size": 54.0,
        # Имя говорящего прямо в тексте реплики: зритель поля Name не видит,
        # а в готовом файле метка иногда нужна. Ключ заготовки из
        # sfstudio.core.actor_label; "custom" берёт шаблон из соседнего поля.
        "actor_label": "off",
        "actor_label_custom": "[{actor}] {text}",
    },
    "spelling": {
        # Язык словаря: "" — не проверять, "ru", "en_US". Выключено по
        # умолчанию: словарь читается полсекунды, и навязывать это тем, кто
        # проверкой не пользуется, незачем.
        "language": "",
        # Свой словарь: имена, термины, названия. Общий для всех проектов —
        # персонажи повторяются из серии в серию.
        "words": [],
    },
    "editing": {
        "snap_to_frames": True,
        "snap_to_keyframes": True,
        "snap_to_events": True,
        "min_gap_frames": 2,
        "position_mode": "pos",
        # Куда вставлять реплику из буфера: "playhead" — на курсор
        # таймлайна, "mouse" — туда, где указатель мыши. Второе удобно,
        # когда место выбирают глазами; первое — когда работают клавишами.
        "paste_at": "playhead",
    },
    "media": {
        "volume": 80.0,
        "autoplay": False,
    },
    "recent": {
        "subtitles": [],
        "media": [],
        "projects": [],
    },
    "asr": {
        # Пусто — движок не выбран. Подставлять здесь конкретный нельзя:
        # ни один не входит в поставку, и выбор за пользователем.
        "engine": "",
        "model": "",
        "language": None,
        "models_dir": "",
        # Пусто — определить автоматически. Видеокарта не всегда быстрее:
        # модель нужно загрузить в её память, и на коротких отрезках это
        # съедает выигрыш.
        "device": "",
    },
    "plugins": {
        # Выполнять чужой код без ведома человека нельзя, поэтому загрузка
        # включается явно. Список выключенных — по именам из манифестов;
        # данные самих плагинов лежат в "data" и программой не трактуются.
        "enabled": False,
        "disabled": [],
        "data": {},
    },
    "storage": {
        # Куда складывать скачанное: модели, библиотеки для видеокарты,
        # плагины. Подробности о режимах — в sfstudio.app.storage.
        # По умолчанию рядом с программой: переносную сборку тогда можно
        # унести целиком, вместе с моделями.
        "mode": "beside",
        "folder": "",
    },
    "project": {
        # Куда предлагать сохранять новые проекты. Пусто — домашняя папка:
        # подставлять её в момент чтения настроек нельзя, файл настроек
        # переносят между машинами, и чужой путь там бесполезен.
        "folder": "",
        "autosave_minutes": 5,
        "restore_last_on_start": False,
        "show_startup_dialog": True,
    },
}

MAX_RECENT = 12


class SettingsError(RuntimeError):
    """Настройки нельзя прочитать или сохранить."""


def _deep_merge(base: dict, extra: dict) -> dict:
    """Накладывает сохранённые значения на умолчания, не теряя новых ключей.

    Простое ``base.update(extra)`` затёрло бы целые разделы: конфиг прошлой
    версии не знает про ключи, добавленные позже, и после обновления
    приложения они бы исчезли.
    """
    result = dict(base)
    for key, value in extra.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = value
    return result


#: Миграции между версиями конфига. Ключ — версия, из которой мигрируем.
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


class Settings:
    """Настройки приложения. Доступ по пути вида ``"ui.theme"``."""

    __slots__ = ("_data", "_dirty", "_path")

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config_dir() / FILENAME)
        self._data = dict(DEFAULTS)
        self._dirty = False
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    # -- чтение и запись --------------------------------------------------------- #

    def load(self) -> None:
        if not self._path.is_file():
            self._data = json.loads(json.dumps(DEFAULTS))  # глубокая копия
            return

        try:
            # utf-8-sig, а не utf-8: файл настроек читаемый, и его правят
            # руками — а «Блокнот» и PowerShell в Windows пишут UTF-8 с
            # меткой порядка байтов. Для json.loads эта метка — мусор в
            # начале, и настройки уезжали в карантин целиком: человек
            # поправил одну строчку и остался без всех своих настроек.
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            # Битый конфиг не должен мешать запуску: отодвигаем и стартуем с нуля.
            self._quarantine()
            self._data = json.loads(json.dumps(DEFAULTS))
            return

        if not isinstance(raw, dict):
            self._quarantine()
            self._data = json.loads(json.dumps(DEFAULTS))
            return

        version = int(raw.get("config_version", 0) or 0)
        if version > CONFIG_VERSION:
            raise SettingsError(
                f"Файл настроек от более новой версии программы "
                f"(версия {version}, поддерживается {CONFIG_VERSION}). "
                f"Обновите программу или удалите {self._path.name}."
            )
        raw = self._migrate(raw, version)
        self._data = _deep_merge(DEFAULTS, raw)
        self._data["config_version"] = CONFIG_VERSION

    def _migrate(self, raw: dict, version: int) -> dict:
        while version < CONFIG_VERSION:
            migrator = MIGRATIONS.get(version)
            if migrator is None:
                break
            raw = migrator(raw)
            version += 1
        return raw

    def _quarantine(self) -> None:
        """Отодвигает повреждённый файл, чтобы не потерять его содержимое."""
        broken = self._path.with_suffix(self._path.suffix + ".broken")
        # Не вышло отодвинуть — не беда: настройки всё равно возьмутся
        # из умолчаний, а запуск важнее сохранности испорченного файла.
        with contextlib.suppress(OSError):
            self._path.replace(broken)

    def save(self, *, force: bool = False) -> None:
        """Пишет настройки атомарно. Без изменений ничего не делает."""
        if not self._dirty and not force:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(self._path.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self._path)
            self._dirty = False
        except OSError as exc:
            raise SettingsError(f"не удалось сохранить настройки: {exc}") from exc
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    # -- доступ ------------------------------------------------------------------- #

    def get(self, path: str, default: Any = None) -> Any:
        """Значение по пути ``"раздел.ключ"``."""
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self._data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        if node.get(parts[-1]) != value:
            node[parts[-1]] = value
            self._dirty = True

    def __contains__(self, path: str) -> bool:
        sentinel = object()
        return self.get(path, sentinel) is not sentinel

    @property
    def dirty(self) -> bool:
        return self._dirty

    def as_dict(self) -> dict:
        return json.loads(json.dumps(self._data))

    def reset(self, section: str | None = None) -> None:
        """Сбрасывает раздел или всё целиком."""
        if section is None:
            self._data = json.loads(json.dumps(DEFAULTS))
        else:
            default = DEFAULTS.get(section)
            if isinstance(default, dict):
                self._data[section] = json.loads(json.dumps(default))
        self._dirty = True

    # -- недавние файлы ------------------------------------------------------------ #

    def push_recent(self, kind: str, path: Path | str) -> None:
        """Добавляет файл в начало списка недавних, без дубликатов."""
        key = f"recent.{kind}"
        items = [str(p) for p in (self.get(key) or [])]
        text = str(path)
        if text in items:
            items.remove(text)
        items.insert(0, text)
        self.set(key, items[:MAX_RECENT])

    def recent(self, kind: str, *, existing_only: bool = True) -> list[Path]:
        """Недавние файлы.

        Исчезнувшие записи не удаляются молча: файл может лежать на съёмном
        диске и вернуться. Их просто не показывают, пока путь недоступен.
        """
        items = [Path(p) for p in (self.get(f"recent.{kind}") or [])]
        return [p for p in items if p.exists()] if existing_only else items
