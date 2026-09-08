"""Определение кодировки файла субтитров.

Порядок доверия: BOM → строгий UTF-8 → эвристика по кириллице → системная
кодовая страница. Файлы субтитров в дикой природе часто в cp1251/cp1252 без
всяких указаний, а неверная догадка портит текст молча — поэтому определённая
кодировка возвращается наружу и показывается в статус-баре, чтобы человек мог
переключить её вручную.

``charset-normalizer`` используется, если установлен; без него работает
встроенная эвристика — зависимость мягкая.
"""

from __future__ import annotations

__all__ = ["decode_bytes", "detect_encoding"]

_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xff\xfe", "utf-16-le"),
)

#: Кандидаты для перебора, в порядке убывания вероятности для наших пользователей.
_CANDIDATES = ("cp1251", "cp1252", "koi8-r", "cp866", "iso-8859-5", "cp932", "gb18030", "big5")

# Диапазоны cp1251, дающие кириллицу — маркер того, что догадка верна.
_CYRILLIC_BYTES = set(range(0xC0, 0x100)) | {0xA8, 0xB8}


def detect_encoding(raw: bytes) -> str:
    """Возвращает имя кодировки, которой стоит декодировать ``raw``."""
    for bom, name in _BOMS:
        if raw.startswith(bom):
            return name

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return "utf-8"

    try:
        from charset_normalizer import from_bytes  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        best = from_bytes(raw).best()
        if best is not None and best.encoding:
            return str(best.encoding)

    return _guess_legacy(raw)


def _guess_legacy(raw: bytes) -> str:
    """Эвристика без внешних зависимостей.

    Считаем долю байтов из кириллического диапазона среди старших. Много таких
    байтов — почти наверняка cp1251: у cp1252 текст на латинице просто не даёт
    столько старших байтов подряд.
    """
    sample = raw[:65536]
    high = [b for b in sample if b >= 0x80]
    if not high:
        return "utf-8"

    cyrillic_share = sum(1 for b in high if b in _CYRILLIC_BYTES) / len(high)
    if cyrillic_share > 0.6:
        return "cp1251"

    for candidate in _CANDIDATES:
        try:
            sample.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
        return candidate
    return "cp1252"  # декодирует любой байт, поэтому годится как последний рубеж


def decode_bytes(raw: bytes, *, forced: str | None = None) -> tuple[str, str]:
    """Декодирует, возвращая ``(текст, использованная кодировка)``.

    Ошибки декодирования заменяются, а не роняют открытие: показать файл с
    несколькими испорченными символами и дать переключить кодировку полезнее,
    чем отказаться его открывать.
    """
    encoding = forced or detect_encoding(raw)
    try:
        return raw.decode(encoding), encoding
    except (UnicodeDecodeError, LookupError):
        return raw.decode(encoding, errors="replace"), encoding
