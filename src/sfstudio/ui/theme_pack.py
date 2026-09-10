"""Темы из файлов: как плагины, только без кода.

Тема — это один файл ``.json`` в каталоге тем. Внутри название, от какой
встроенной темы отталкиваться и список цветов, которые нужно переопределить.
Можно дописать и свой кусок таблицы стилей — тогда меняются не только цвета,
но и скругления, отступы, рамки.

**Почему данные, а не код.** Плагин — это программа, и включать его надо
осознанно: он может всё, что может сама программа. Тема не должна требовать
такого доверия, поэтому она описывается данными. Худшее, что сделает
испорченная тема, — покажет некрасивое окно; ни файл прочитать, ни в сеть
выйти она не может. Поэтому темы, в отличие от плагинов, работают сразу, без
разрешения в настройках.

Неизвестные ключи в списке цветов пропускаются, а не считаются ошибкой: тема
может быть написана под будущую версию, где полей больше, и это не повод
отказываться её показывать.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.ui.theme import BUILTIN, DARK, THEMES, Palette, with_accent

__all__ = [
    "SUFFIX",
    "ThemePack",
    "available",
    "discover",
    "example_text",
    "palette_of",
    "read_pack",
]

#: Расширение файла темы. Двойное, чтобы тему было видно среди прочих json.
SUFFIX = ".sfstheme.json"

#: Поля палитры, которые тема вправе переопределять.
COLOR_KEYS = frozenset(f.name for f in fields(Palette))


@dataclass(frozen=True, slots=True)
class ThemePack:
    """Тема, прочитанная из файла."""

    name: str
    title: str
    author: str = ""
    version: str = "1.0"
    #: На какой встроенной теме основана: от неё берутся цвета, не заданные
    #: явно. Без этого автору темы пришлось бы перечислять все семнадцать.
    base: str = "dark"
    colors: dict[str, str] = field(default_factory=dict)
    #: Дописывается в конец таблицы стилей. Ошибка в нём испортит вид, но не
    #: помешает работе: Qt молча пропускает правила, которых не понимает.
    qss: str = ""
    path: Path | None = None
    #: Причина, если файл прочитать не удалось. Тема с причиной показывается
    #: в списке отключённой — молча пропасть она не должна.
    error: str = ""

    @property
    def builtin(self) -> bool:
        return self.path is None

    @property
    def caption(self) -> str:
        parts = [self.title]
        if self.author:
            parts.append(f"— {self.author}")
        return " ".join(parts)


def palette_of(pack: ThemePack, accent: str = "") -> Palette:
    """Собирает палитру темы: основа, поверх неё цвета темы, потом акцент.

    Порядок именно такой. Акцент выбирает человек и уже после того, как
    выбрал тему, поэтому его слово последнее.
    """
    palette = BUILTIN.get(pack.base, DARK)
    known = {k: v for k, v in pack.colors.items() if k in COLOR_KEYS}
    if known:
        palette = replace(palette, **known)
    return with_accent(palette, accent) if accent else palette


def read_pack(path: Path) -> ThemePack:
    """Читает файл темы. Исключений не бросает — возвращает причину в поле."""
    path = Path(path)
    stem = path.name[: -len(SUFFIX)] if path.name.endswith(SUFFIX) else path.stem
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return ThemePack(name=stem, title=stem, path=path,
                         error=tr('файл не читается: {0}').format(exc.strerror or exc))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return ThemePack(name=stem, title=stem, path=path,
                         error=tr('ошибка в json: {0}').format(exc))
    if not isinstance(data, dict):
        return ThemePack(name=stem, title=stem, path=path,
                         error=tr('ожидался объект json'))

    name = str(data.get("name") or stem).strip() or stem
    base = str(data.get("base") or "dark").strip().lower()
    if base not in BUILTIN:
        base = "dark"

    colors = data.get("colors")
    if not isinstance(colors, dict):
        colors = {}
    clean = {
        key: str(value)
        for key, value in colors.items()
        if key in COLOR_KEYS and isinstance(value, str)
    }

    pack = ThemePack(
        name=name,
        title=str(data.get("title") or name),
        author=str(data.get("author") or ""),
        version=str(data.get("version") or "1.0"),
        base=base,
        colors=clean,
        qss=str(data.get("qss") or ""),
        path=path,
    )
    # Цвета проверяются сразу: тема с опечаткой в одном цвете должна быть
    # видна как сломанная здесь, а не разъехаться в окне.
    broken = _bad_colors(clean)
    if broken:
        return replace(pack, error=tr('неверные цвета: ') + ", ".join(broken))
    return pack


def _bad_colors(colors: dict[str, str]) -> list[str]:
    from sfstudio.core.color import RGBA

    bad: list[str] = []
    for key, value in colors.items():
        try:
            RGBA.from_hex(value)
        except (ValueError, IndexError):
            bad.append(f"{key}={value}")
    return bad


def discover(folder: Path | None) -> list[ThemePack]:
    """Все темы из каталога, по алфавиту. Нет каталога — пустой список."""
    if folder is None or not Path(folder).is_dir():
        return []
    found = [read_pack(entry) for entry in sorted(Path(folder).glob("*" + SUFFIX))]
    return sorted(found, key=lambda p: p.title.lower())


def builtin_packs() -> list[ThemePack]:
    """Встроенные темы в том же виде, что и файловые."""
    return [
        ThemePack(name=key, title=title, author="", base=key)
        for key, title in THEMES.items()
    ]


def available(folder: Path | None) -> dict[str, ThemePack]:
    """Все темы по именам: встроенные плюс найденные в каталоге.

    Файловая тема с именем встроенной перекрывает её — так же, как шаблон
    оформления. Это осознанный способ подправить стандартную тему, не
    выдумывая ей новое название.
    """
    packs: dict[str, ThemePack] = {p.name: p for p in builtin_packs()}
    for pack in discover(folder):
        packs[pack.name] = pack
    return packs


def example_text() -> str:
    """Образец файла темы — его кладут человеку, когда он просит «свою тему»."""
    sample = {
        "name": tr('моя-тема'),
        "title": tr('Моя тема'),
        "author": "",
        "version": "1.0",
        "base": "dark",
        "colors": {
            "bg_base": "#14161C",
            "bg_elevated": "#1C1F27",
            "bg_sunken": "#0E1014",
            "border": "#2B303B",
            "text_primary": "#E8EAF0",
            "text_muted": "#8C93A1",
            "accent": "#7C5CFF",
        },
        "qss": "",
    }
    # Пояснение лежит внутри объекта, а не комментарием перед ним: json
    # комментариев не знает, и файл с ними не открылся бы ни здесь, ни в
    # редакторе, куда его понесут править.
    sample[tr('_подсказка')] = (
        tr('base — основа (dark или light); colors — что в ней поменять; qss — '
               'необязательная добавка к таблице стилей Qt')
    )
    return json.dumps(sample, ensure_ascii=False, indent=2) + "\n"
