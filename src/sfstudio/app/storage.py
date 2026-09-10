"""Куда складывать скачанное: модели, библиотеки, плагины.

Речь о гигабайтах. Модель — от 75 МБ до 3 ГБ, библиотеки CUDA — ещё 0,7 ГБ,
и решать за человека, на каком диске они окажутся, нельзя: у него может быть
маленький системный SSD и большой второй диск, а может быть переносная сборка
на флешке, которую надо унести целиком.

Поэтому три режима, и все три осмысленны:

* **рядом с программой** — всё в подпапках возле исполняемого файла. Удобно
  для переносной установки: скопировал папку — унёс с собой вместе с
  моделями. По умолчанию именно он, если рядом можно писать;
* **в профиле пользователя** — стандартное место для программ, установленных
  в Program Files, где писать рядом с собой нельзя;
* **свой каталог** — когда человек хочет положить всё на второй диск.

Выбор по умолчанию делается **проверкой записи**, а не догадкой по пути.
Программу запускают откуда угодно, включая сетевые диски и флешки, и
единственный надёжный способ узнать, можно ли писать рядом, — попробовать.
"""

from __future__ import annotations

import os
import sys
import tempfile
from enum import StrEnum
from pathlib import Path

from sfstudio.app.i18n import tr

__all__ = [
    "StorageMode",
    "beside_program",
    "data_root",
    "libraries_dir",
    "models_dir",
    "plugins_dir",
    "profiles_dir",
    "resolve_root",
    "themes_dir",
    "writable",
]

#: Имена подпапок. Латиницей: пути с кириллицей ломают часть сторонних
#: библиотек, а это как раз каталоги, куда те библиотеки и кладутся.
MODELS_FOLDER = "models"
LIBRARIES_FOLDER = "cuda"
PLUGINS_FOLDER = "plugins"
THEMES_FOLDER = "themes"
PROFILES_FOLDER = "profiles"


class StorageMode(StrEnum):
    """Где держать скачанное."""

    BESIDE = "beside"
    PROFILE = "profile"
    CUSTOM = "custom"

    @property
    def title(self) -> str:
        return _TITLES[self]

    @property
    def note(self) -> str:
        return _NOTES[self]


_TITLES = {
    StorageMode.BESIDE: tr('Рядом с программой'),
    StorageMode.PROFILE: tr('В профиле пользователя'),
    StorageMode.CUSTOM: tr('В указанной папке'),
}

_NOTES = {
    StorageMode.BESIDE: (
        tr('Модели и библиотеки лягут в подпапки возле файла программы. Всю папку можно '
               'скопировать на другой компьютер целиком.')
    ),
    StorageMode.PROFILE: (
        tr('Стандартное место для программ. Подходит, если программа установлена туда, '
               'где нельзя писать.')
    ),
    StorageMode.CUSTOM: (
        tr('Свой путь — например, на втором диске, где больше места.')
    ),
}


def beside_program() -> Path:
    """Папка рядом с исполняемым файлом.

    В собранном виде это папка с ``.exe``. При запуске из исходников —
    корень проекта, а не каталог ``src``: складывать гигабайты внутрь
    дерева с кодом незачем.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def writable(folder: Path) -> bool:
    """Можно ли писать в этот каталог.

    Проверяем созданием файла, а не правами: права на сетевых дисках и в
    контейнерах говорят одно, а запись кончается отказом. Каталог при этом
    создаётся — если создать его нельзя, ответ и так «нет».
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".sfstudio-", delete=True):
            return True
    except OSError:
        return False


def resolve_root(mode: StorageMode | str, custom: str | os.PathLike[str] | None) -> Path:
    """Корневой каталог для выбранного режима.

    Свой путь, оказавшийся пустым, — не ошибка настроек, а обычное «выбрал
    режим, но папку ещё не указал». В этом случае откатываемся к профилю:
    ронять распознавание из-за незаполненного поля неправильно.
    """
    from sfstudio.platform.paths import data_dir

    try:
        mode = StorageMode(str(mode))
    except ValueError:
        mode = StorageMode.PROFILE

    if mode is StorageMode.CUSTOM:
        text = str(custom or "").strip()
        return Path(text) if text else data_dir()
    if mode is StorageMode.BESIDE:
        return beside_program()
    return data_dir()


def data_root(settings) -> Path:
    """Корень хранилища по настройкам приложения."""
    return resolve_root(
        str(settings.get("storage.mode", StorageMode.BESIDE)),
        settings.get("storage.folder", ""),
    )


def models_dir(settings) -> Path:
    """Куда качать модели распознавания.

    Явно указанный каталог моделей имеет приоритет: он появился раньше
    общей настройки хранилища, и у людей он уже заполнен — переезжать
    молча, потеряв из виду скачанные гигабайты, программа не вправе.
    """
    explicit = str(settings.get("asr.models_dir", "") or "").strip()
    if explicit:
        return Path(explicit)
    return data_root(settings) / MODELS_FOLDER


def libraries_dir(settings) -> Path:
    """Куда качать библиотеки для счёта на видеокарте."""
    return data_root(settings) / LIBRARIES_FOLDER


def plugins_dir(settings) -> Path:
    """Где искать плагины пользователя."""
    return data_root(settings) / PLUGINS_FOLDER


def themes_dir(settings) -> Path:
    """Где искать темы оформления."""
    return data_root(settings) / THEMES_FOLDER


def profiles_dir(settings) -> Path:
    """Где искать профили проверок заказчиков."""
    return data_root(settings) / PROFILES_FOLDER
