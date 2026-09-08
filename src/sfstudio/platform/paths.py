"""Каталоги приложения: настройки, кэш, данные.

Пути определяются по правилам платформы, но их можно переопределить
переменной ``SFSTUDIO_HOME`` — это нужно тестам (чтобы не трогать настоящий
конфиг пользователя) и портативной сборке (всё рядом с exe).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["app_home", "cache_dir", "config_dir", "data_dir", "logs_dir", "presets_dir"]

APP_NAME = "SubtitleForge"

#: Переопределение всех каталогов разом. Тесты и портативный режим.
HOME_ENV = "SFSTUDIO_HOME"


def app_home() -> Path | None:
    """Единый корень, если задан. Иначе каталоги раскладываются по платформе."""
    raw = os.environ.get(HOME_ENV)
    return Path(raw) if raw else None


def _base(kind: str) -> Path:
    override = app_home()
    if override is not None:
        return override / kind

    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        local = os.environ.get("LOCALAPPDATA")
        if kind == "cache":
            root = Path(local) if local else Path.home() / "AppData" / "Local"
            return root / APP_NAME / "Cache"
        root = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
        return root / APP_NAME

    if sys.platform == "darwin":
        if kind == "cache":
            return Path.home() / "Library" / "Caches" / APP_NAME
        return Path.home() / "Library" / "Application Support" / APP_NAME

    # XDG
    if kind == "cache":
        root = os.environ.get("XDG_CACHE_HOME")
        return (Path(root) if root else Path.home() / ".cache") / "subforge"
    root = os.environ.get("XDG_CONFIG_HOME")
    return (Path(root) if root else Path.home() / ".config") / "subforge"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    """Настройки: конфиг, раскладка окна, шаблоны стилей."""
    return _ensure(_base("config"))


def cache_dir() -> Path:
    """Кэш: пики аудио, индексы кадров, миниатюры. Можно удалять безболезненно."""
    return _ensure(_base("cache"))


def data_dir() -> Path:
    """Данные, которые терять нельзя: автосохранения, журнал восстановления."""
    return _ensure(_base("config") / "data")


def logs_dir() -> Path:
    return _ensure(_base("config") / "logs")


def presets_dir() -> Path:
    """Шаблоны стилей — отдельным каталогом, чтобы их можно было переносить."""
    return _ensure(_base("config") / "presets")
