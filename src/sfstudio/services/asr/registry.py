"""Реестр движков распознавания.

Точка расширения: чтобы добавить движок, достаточно написать адаптер и вызвать
:func:`register`. Ни диалог, ни главное окно при этом не меняются — они
работают со списком, а не с конкретными именами.

Два правила, ради которых реестр вообще существует отдельно от движков:

**Список строится без импорта тяжёлых зависимостей.** Проверка «доступен ли
faster-whisper» — это ``importlib.util.find_spec``, а не ``import``. Импорт
torch занимает секунды и сотни мегабайт памяти; платить это при каждом
открытии диалога, чтобы показать пункт в списке, недопустимо.

**Отсутствие движка — не ошибка.** Пользователь вправе не ставить ничего и
писать субтитры руками; вправе поставить один движок из трёх. Поэтому реестр
всегда возвращает полный список с пометкой доступности и подсказкой, как
получить недостающее, — а не молча прячет то, чего нет.
"""

from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Callable
from pathlib import Path

from sfstudio.services.asr.base import EngineInfo, SpeechRecognizer

__all__ = [
    "available_engines",
    "downloads_dir",
    "engine_infos",
    "get_engine",
    "has_any_engine",
    "module_installed",
    "program_on_path",
    "register",
    "registered_keys",
    "set_downloads_dir",
    "unregister",
]

#: Ключ → фабрика движка. Фабрика, а не готовый объект: движок может держать
#: модель в памяти, и создавать его до того, как он понадобился, значит
#: занимать сотни мегабайт ради списка в диалоге.
_FACTORIES: dict[str, Callable[[], SpeechRecognizer]] = {}

#: Каталог, куда программа складывает скачанное. Движкам он нужен, чтобы
#: найти то, что она для них скачала; глобальный, потому что фабрика
#: вызывается из мест, которые о настройках не знают и знать не должны —
#: реестр движков не место для протаскивания настроек через три слоя.
_DOWNLOADS: Path | None = None


def set_downloads_dir(folder: Path | None) -> None:
    """Сообщает реестру, где лежит скачанное программой."""
    global _DOWNLOADS
    _DOWNLOADS = Path(folder) if folder is not None else None


def downloads_dir() -> Path | None:
    return _DOWNLOADS


def register(key: str, factory: Callable[[], SpeechRecognizer]) -> None:
    """Добавляет движок в реестр. Повторная регистрация ключа заменяет его."""
    if not key:
        raise ValueError("ключ движка не может быть пустым")
    _FACTORIES[key] = factory


def unregister(key: str) -> bool:
    """Убирает движок из реестра. ``False`` — такого не было."""
    return _FACTORIES.pop(key, None) is not None


def registered_keys() -> list[str]:
    return list(_FACTORIES)


def get_engine(key: str) -> SpeechRecognizer | None:
    """Создаёт движок по ключу. ``None``, если такого нет."""
    factory = _FACTORIES.get(key)
    return factory() if factory is not None else None


def engine_infos() -> list[EngineInfo]:
    """Описания всех движков — и доступных, и нет.

    Сломанный адаптер не должен ронять список: он попадёт в него с пометкой
    о неисправности, а остальные останутся выбираемыми.
    """
    infos: list[EngineInfo] = []
    for key, factory in _FACTORIES.items():
        try:
            infos.append(factory().info())
        except Exception as exc:
            infos.append(
                EngineInfo(
                    key=key,
                    title=key,
                    description="адаптер не отвечает",
                    available=False,
                    hint=f"ошибка адаптера: {exc}",
                )
            )
    return infos


def available_engines() -> list[EngineInfo]:
    return [info for info in engine_infos() if info.available]


def has_any_engine() -> bool:
    """Есть ли хоть один рабочий движок.

    По этому вопросу решается, показывать ли пункт «Распознать речь» активным.
    Прятать его совсем не стоит: пользователь должен узнать о возможности и о
    том, что для неё нужно.
    """
    return any(info.available for info in engine_infos())


# --------------------------------------------------------------------------- #
# Проверки доступности — общие для адаптеров
# --------------------------------------------------------------------------- #


def module_installed(name: str) -> bool:
    """Установлен ли модуль, **без** его импорта.

    ``find_spec`` только ищет файл; ``import`` у крупных пакетов выполняет
    инициализацию на секунды. Для вопроса «показывать ли пункт в списке»
    первого достаточно.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # Битая установка: пакет числится, но спека не строится. Для нас это
        # то же самое, что его нет.
        return False


def program_on_path(name: str) -> str | None:
    """Путь к исполняемому файлу в PATH или ``None``."""
    return shutil.which(name)
