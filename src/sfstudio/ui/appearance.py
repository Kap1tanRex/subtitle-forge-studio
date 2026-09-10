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

__all__ = ["apply", "arrow_qss", "build", "current_pack", "qt_palette", "themes_for"]


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

    qss = build_qss(palette) + arrow_qss(palette)
    if pack.qss:
        # Добавка темы идёт последней: правила Qt применяются по порядку, и
        # автор темы должен иметь возможность перекрыть наши.
        qss = f"{qss}\n/* тема: {pack.name} */\n{pack.qss}\n"

    key = f"{pack.name}|{accent}|{len(pack.qss)}"
    return palette, qss, key


def arrow_qss(palette: Palette) -> str:
    """Правила для стрелок в списках и счётчиках.

    Как только у виджета появляется хоть одно правило, Qt перестаёт рисовать
    его стрелки системным стилем — а нарисовать их средствами самой таблицы
    стилей нельзя: приём с рамками, которым в CSS делают треугольник, Qt
    рисует прямоугольником (проверено). Поэтому стрелки делаются картинками
    и кладутся в кэш; файл зависит от цвета, так что смена темы его не
    портит, а повторный запуск переиспользует готовый.

    Не получилось нарисовать — правил не добавляем. Список без стрелки
    выглядит скучно, но работает; окно без списка не работает вовсе.
    """
    files = _arrow_files(palette.text_muted)
    if not files:
        return ""
    return f"""
    QComboBox::down-arrow {{
        image: url({files["down"]});
        width: 9px;
        height: 6px;
        margin-right: 6px;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url({files["up"]});
        width: 9px;
        height: 6px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: url({files["down"]});
        width: 9px;
        height: 6px;
    }}
    """


def _arrow_files(color: str) -> dict[str, str]:
    """Рисует пару стрелок нужного цвета. Возвращает пути для ``url(...)``."""
    import tempfile
    from pathlib import Path

    from PySide6.QtCore import QPoint, QStandardPaths, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap, QPolygon

    # Рисование требует графического приложения. Без него QPixmap роняет
    # процесс целиком — не исключением, а падением, и разбираться в этом
    # приходится по кодам возврата. Сборка таблицы стилей обязана работать
    # и когда рисовать нечем: тесты собирают её без окон.
    if QGuiApplication.instance() is None:
        return {}

    try:
        root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        ) or tempfile.gettempdir()
        folder = Path(root) / "arrows"
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}

    made: dict[str, str] = {}
    tint = color.lstrip("#")
    for name, points in (
        ("down", ((0, 0), (9, 0), (4, 6))),
        ("up", ((0, 6), (9, 6), (4, 0))),
    ):
        path = folder / f"{name}-{tint}.png"
        if not path.is_file():
            pixmap = QPixmap(9, 6)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
            painter.end()
            if not pixmap.save(str(path)):
                return {}
        made[name] = path.as_posix()
    return made


def qt_palette(palette: Palette):
    """Палитра Qt из нашей.

    Таблицы стилей мало. Часть цветов виджеты берут не из неё, а из палитры
    Qt, которая по умолчанию **системная**: на машине со светлой темой
    Windows это давало белый фон под тёмной темой программы — и белый текст
    поверх него, потому что цвет текста как раз задавала таблица стилей.
    Разошлись эти два источника только у людей с другой системной темой,
    поэтому у одних всё выглядело правильно, а у других — нет.
    """
    from PySide6.QtGui import QColor, QPalette

    qt = QPalette()
    base = QColor(palette.bg_base)
    text = QColor(palette.text_primary)
    muted = QColor(palette.text_muted)

    roles = {
        QPalette.ColorRole.Window: base,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: QColor(palette.bg_sunken),
        QPalette.ColorRole.AlternateBase: QColor(palette.bg_elevated),
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: QColor(palette.bg_elevated),
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.BrightText: QColor(palette.danger),
        QPalette.ColorRole.Highlight: QColor(palette.accent),
        QPalette.ColorRole.HighlightedText: QColor(palette.bg_base),
        QPalette.ColorRole.ToolTipBase: QColor(palette.bg_elevated),
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: muted,
        QPalette.ColorRole.Link: QColor(palette.accent),
        QPalette.ColorRole.LinkVisited: QColor(palette.accent_muted),
        QPalette.ColorRole.Mid: QColor(palette.border),
        QPalette.ColorRole.Midlight: QColor(palette.bg_elevated),
        QPalette.ColorRole.Dark: QColor(palette.bg_sunken),
        QPalette.ColorRole.Shadow: QColor(palette.bg_sunken),
    }
    for role, color in roles.items():
        qt.setColor(role, color)

    # Недоступное — приглушённым, иначе Qt считает его от системных цветов
    # и на тёмной теме получается серое по серому.
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        qt.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return qt


def apply(app, settings) -> Palette:
    """Применяет тему и анимации к приложению. Возвращает палитру.

    Виджеты, рисующие себя сами, палитру получают не отсюда: им её раздаёт
    главное окно — только оно знает их список.
    """
    palette, qss, key = build(settings)
    if app is not None and app.property("sfstudio_theme") != key:
        # Стиль задаётся явно. По умолчанию Qt берёт системный — «windows11»
        # на одиннадцатой Windows, «windowsvista» на десятой, — и рисует
        # часть виджетов сам, мимо нашей таблицы стилей. Fusion одинаков
        # везде и слушается и таблицу, и палитру.
        # Отметка, а не опрос стиля: после установки таблицы стилей Qt
        # подменяет стиль обёрткой QStyleSheetStyle, у которой имя пустое, и
        # опрос заставлял бы пересоздавать стиль при каждой смене темы.
        if not app.property("sfstudio_style_fixed"):
            app.setStyle("Fusion")
            app.setProperty("sfstudio_style_fixed", True)
        app.setPalette(qt_palette(palette))
        app.setStyleSheet(qss)
        app.setProperty("sfstudio_theme", key)

    install_effects(app, resolve(settings.get("ui.animations", None)))
    return palette
