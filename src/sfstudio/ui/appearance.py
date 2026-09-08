"""Сборка внешнего вида: тема, акцент, анимации — в одном месте.

Раньше вид собирался дважды: при запуске в :mod:`sfstudio.ui.app` и заново в
главном окне. Пока слагаемым было одно название темы, это сходило с рук; с
темами из файлов, акцентным цветом и добавкой к таблице стилей два похожих,
но не одинаковых куска кода разошлись бы на первой же правке.
"""

from __future__ import annotations

from sfstudio.ui.motion import install_effects, resolve
from sfstudio.ui.theme import Palette, build_qss
from sfstudio.ui.theme_pack import ThemePack, available, palette_of

__all__ = ["apply", "build", "current_pack", "themes_for"]


def themes_for(settings) -> dict[str, ThemePack]:
    """Все доступные темы: встроенные плюс файлы из каталога тем.

    Каталог берётся из настроек хранения — там же, где модели и плагины.
    Если он недоступен (сетевой диск отвалился, прав нет), остаются
    встроенные: без тем программа обойдётся, без окна — нет.
    """
    folder = None
    try:
        from sfstudio.app.storage import themes_dir

        folder = themes_dir(settings)
    except Exception:
        folder = None
    return available(folder)


def current_pack(settings) -> ThemePack:
    """Выбранная тема. Незнакомое имя или сломанный файл — тёмная."""
    packs = themes_for(settings)
    name = str(settings.get("ui.theme", "dark") or "dark")
    pack = packs.get(name)
    if pack is None or pack.error:
        return packs.get("dark") or ThemePack(name="dark", title="Тёмная", base="dark")
    return pack


def build(settings) -> tuple[Palette, str, str]:
    """Палитра, таблица стилей и ключ, по которому видно смену вида.

    Ключ нужен, чтобы не ставить одну и ту же таблицу стилей дважды:
    ``setStyleSheet`` пересчитывает стиль каждого существующего виджета, и
    лишний вызов при открытии окна стоил секунд.
    """
    pack = current_pack(settings)
    accent = str(settings.get("ui.accent", "") or "")
    palette = palette_of(pack, accent)

    qss = build_qss(palette)
    if pack.qss:
        # Добавка темы идёт последней: правила Qt применяются по порядку, и
        # автор темы должен иметь возможность перекрыть наши.
        qss = f"{qss}\n/* тема: {pack.name} */\n{pack.qss}\n"

    key = f"{pack.name}|{accent}|{len(pack.qss)}"
    return palette, qss, key


def apply(app, settings) -> Palette:
    """Применяет тему и анимации к приложению. Возвращает палитру.

    Виджеты, рисующие себя сами, палитру получают не отсюда: им её раздаёт
    главное окно — только оно знает их список.
    """
    palette, qss, key = build(settings)
    if app is not None and app.property("sfstudio_theme") != key:
        app.setStyleSheet(qss)
        app.setProperty("sfstudio_theme", key)

    install_effects(app, resolve(settings.get("ui.animations", None)))
    return palette
