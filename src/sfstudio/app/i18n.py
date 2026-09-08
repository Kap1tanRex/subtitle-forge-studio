"""Перевод интерфейса.

Механизм намеренно простой: словарь «русская строка → перевод», лежащий в
JSON. Не Qt-переводы с ``.ts`` и ``.qm``, хотя они и штатные, — по двум
причинам.

Во-первых, Qt требует, чтобы каждая строка проходила через ``self.tr()``
внутри класса, унаследованного от ``QObject``. У нас переводимые строки есть
и в ядре, и в службах, где Qt нет вовсе, и ради этого пришлось бы либо
тащить Qt в ядро, либо держать два механизма.

Во-вторых, ``.ts`` правится в Qt Linguist, а ``.qm`` надо собирать
``lrelease``. Для перевода нужен был бы установленный Qt и умение им
пользоваться; JSON правится в блокноте, и файл можно прислать письмом.

**Ключ перевода — сама русская строка.** Отдельные идентификаторы («ok_button»)
экономят на переименованиях, но взамен делают код нечитаемым и требуют
русского каталога наравне с прочими. Пока язык оригинала один и он же
язык разработки, ключ-строка честнее: в коде видно, что увидит человек.

Отсутствие перевода — **не ошибка**: строка возвращается как есть. Неполный
каталог обязан работать, иначе первый же новый пункт меню ломал бы чужой
перевод целиком.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "AVAILABLE",
    "available_languages",
    "current_language",
    "locale_dir",
    "set_language",
    "tr",
    "translation_progress",
]

#: Язык оригинала. Для него каталог не нужен: строки уже на нём.
SOURCE_LANGUAGE = "ru"

#: Языки, для которых есть каталоги. Русский всегда первый — он оригинал.
AVAILABLE: dict[str, str] = {
    "ru": "Русский",
    "en": "English",
}

_catalog: dict[str, str] = {}
_language = SOURCE_LANGUAGE


def locale_dir() -> Path:
    """Каталог с файлами переводов.

    Рядом с кодом, а не в данных пользователя: перевод — часть поставки, и
    его версия должна совпадать с версией программы. Свой файл можно
    положить туда же.
    """
    return Path(__file__).resolve().parent.parent / "locale"


def available_languages() -> dict[str, str]:
    """Языки, для которых каталог действительно есть на диске."""
    found = {SOURCE_LANGUAGE: AVAILABLE[SOURCE_LANGUAGE]}
    folder = locale_dir()
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            code = path.stem
            found[code] = AVAILABLE.get(code, code)
    return found


def set_language(code: str | None) -> str:
    """Переключает язык. Возвращает язык, который получился.

    Неизвестный код или битый файл — не повод падать: возвращаемся к
    оригиналу, программа продолжает работать по-русски.
    """
    global _catalog, _language

    code = str(code or SOURCE_LANGUAGE).strip().lower()
    if code == SOURCE_LANGUAGE:
        _catalog = {}
        _language = SOURCE_LANGUAGE
        return _language

    path = locale_dir() / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _catalog = {}
        _language = SOURCE_LANGUAGE
        return _language

    if not isinstance(data, dict):
        _catalog = {}
        _language = SOURCE_LANGUAGE
        return _language

    # Пустые значения отбрасываются: в каталоге они означают «ещё не
    # переведено», и подставлять пустоту вместо подписи нельзя.
    _catalog = {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(value, str) and value.strip()
    }
    _language = code
    return _language


def current_language() -> str:
    return _language


def tr(text: str) -> str:
    """Переводит строку. Без перевода возвращает её же.

    Функция короткая и вызывается на каждую подпись, поэтому в ней ровно
    один поиск по словарю: заворачивать сюда форматирование или проверки
    значило бы платить за них при каждой перерисовке.
    """
    return _catalog.get(text, text)


def translation_progress(code: str) -> float:
    """Какая доля строк переведена. Ноль — каталога нет или он пуст.

    Показывается в настройках: неполный перевод — обычное состояние, строки
    прибавляются вместе с возможностями. Человек, выбравший язык и увидевший
    смесь двух, должен понимать, что это не поломка.
    """
    if str(code).lower() == SOURCE_LANGUAGE:
        return 1.0
    path = locale_dir() / f"{str(code).lower()}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    if not isinstance(data, dict) or not data:
        return 0.0
    # Служебные разделы каталога в счёт не идут.
    entries = {k: v for k, v in data.items() if not k.startswith("__")}
    if not entries:
        return 0.0
    done = sum(1 for value in entries.values() if isinstance(value, str) and value.strip())
    return done / len(entries)
