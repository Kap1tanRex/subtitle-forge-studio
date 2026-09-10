"""Дизайн-токены и генерация QSS.

Один источник правды для цветов: и таблица стилей Qt, и кастомная отрисовка
(превью, будущий таймлайн) берут значения отсюда. Иначе виджеты и рисование
разъезжаются при первой же правке палитры.
"""

from __future__ import annotations

from dataclasses import dataclass

from sfstudio.app.i18n import tr

__all__ = [
    "ACCENTS", "BUILTIN", "CONTRAST", "DARK", "LIGHT", "SEPIA", "THEMES", "Palette",
    "build_qss", "is_dark", "mix", "palette_by_name", "repolish", "with_accent",
]


@dataclass(frozen=True, slots=True)
class Palette:
    bg_base: str
    bg_elevated: str
    bg_sunken: str
    border: str
    text_primary: str
    text_muted: str
    accent: str
    accent_muted: str
    success: str
    warning: str
    danger: str
    playhead: str
    canvas_a: str  # шахматка «нет видео»
    canvas_b: str
    guide: str
    wave_fill: str  # огибающая min/max
    wave_rms: str   # среднеквадратичный уровень поверх огибающей


DARK = Palette(
    bg_base="#16181D",
    bg_elevated="#1E2128",
    bg_sunken="#101216",
    border="#2A2E37",
    text_primary="#E6E8EC",
    text_muted="#8A919E",
    accent="#4C8DFF",
    accent_muted="#1F3A63",
    success="#3FB950",
    warning="#D29922",
    danger="#F85149",
    playhead="#FF5F56",
    canvas_a="#22252C",
    canvas_b="#1C1F25",
    guide="#4C8DFF",
    wave_fill="#3A4453",
    wave_rms="#6E8BB5",
)

LIGHT = Palette(
    bg_base="#FFFFFF",
    bg_elevated="#F7F8FA",
    bg_sunken="#EEF0F4",
    border="#DFE3EA",
    text_primary="#14161A",
    text_muted="#5C6472",
    accent="#0B62E0",
    accent_muted="#D6E4FF",
    success="#1A7F37",
    warning="#9A6700",
    danger="#CF222E",
    playhead="#D9382F",
    canvas_a="#E8EAEE",
    canvas_b="#DFE2E7",
    guide="#0B62E0",
    wave_fill="#B7C0CE",
    wave_rms="#7C90AC",
)

#: Тёплая светлая тема. Чисто белый фон при долгой работе с текстом устаёт
#: читать; бумажный оттенок гасит контраст, не трогая разборчивость.
SEPIA = Palette(
    bg_base="#FBF6EC",
    bg_elevated="#F3EADA",
    bg_sunken="#EDE1CD",
    border="#DDCDB2",
    text_primary="#2A2318",
    text_muted="#6B5D48",
    accent="#A6612B",
    accent_muted="#EAD6BC",
    success="#4A7C2F",
    warning="#9A6700",
    danger="#B3402E",
    playhead="#C4502F",
    canvas_a="#E7DCC8",
    canvas_b="#DFD3BC",
    guide="#A6612B",
    wave_fill="#C4B396",
    wave_rms="#93866D",
)

#: Высококонтрастная. Для слабого зрения и для работы при ярком свете:
#: чистый чёрный фон, белый текст, границы видно без вглядывания.
CONTRAST = Palette(
    bg_base="#000000",
    bg_elevated="#0C0C0C",
    bg_sunken="#000000",
    border="#6E6E6E",
    text_primary="#FFFFFF",
    text_muted="#C8C8C8",
    accent="#FFD400",
    accent_muted="#4A3D00",
    success="#3DF06B",
    warning="#FFD400",
    danger="#FF5B5B",
    playhead="#FF5B5B",
    canvas_a="#141414",
    canvas_b="#0A0A0A",
    guide="#FFD400",
    wave_fill="#5A5A5A",
    wave_rms="#B4B4B4",
)

SPACE = {"1": 4, "2": 8, "3": 12, "4": 16, "5": 24}
RADIUS = {"sm": 4, "md": 6, "lg": 10}


def build_qss(p: Palette) -> str:
    """Собирает таблицу стилей из палитры."""
    return f"""
    QWidget {{
        background: {p.bg_base};
        color: {p.text_primary};
        font-family: "Segoe UI", "Inter", system-ui;
        font-size: 13px;
    }}
    QMainWindow::separator {{
        background: {p.border};
        width: 1px;
        height: 1px;
    }}
    QToolBar {{
        background: {p.bg_elevated};
        border-bottom: 1px solid {p.border};
        padding: {SPACE["1"]}px;
        spacing: {SPACE["1"]}px;
    }}
    QToolButton {{
        padding: 5px 10px;
        border-radius: {RADIUS["sm"]}px;
        color: {p.text_primary};
    }}
    QToolButton:hover  {{ background: {p.bg_sunken}; }}
    QToolButton:pressed,
    QToolButton:checked {{ background: {p.accent_muted}; color: {p.text_primary}; }}
    QToolButton:disabled {{ color: {p.text_muted}; }}

    QMenuBar {{ background: {p.bg_elevated}; border-bottom: 1px solid {p.border}; }}
    QMenuBar::item:selected {{ background: {p.accent_muted}; }}
    QMenu {{ background: {p.bg_elevated}; border: 1px solid {p.border}; padding: {SPACE["1"]}px; }}
    QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: {RADIUS["sm"]}px; }}
    QMenu::item:selected {{ background: {p.accent_muted}; }}
    QMenu::separator {{ height: 1px; background: {p.border}; margin: {SPACE["1"]}px 0; }}

    QTableView {{
        background: {p.bg_base};
        alternate-background-color: {p.bg_elevated};
        gridline-color: {p.border};
        border: none;
        selection-background-color: {p.accent_muted};
        selection-color: {p.text_primary};
    }}
    QTableView::item {{ padding: 3px 6px; }}
    QHeaderView::section {{
        background: {p.bg_elevated};
        color: {p.text_muted};
        border: none;
        border-right: 1px solid {p.border};
        border-bottom: 1px solid {p.border};
        padding: 5px 6px;
        font-weight: 600;
    }}

    QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {p.bg_sunken};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: {RADIUS["sm"]}px;
        padding: 4px 6px;
        selection-background-color: {p.accent_muted};
    }}
    QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus {{
        border: 1px solid {p.accent};
    }}
    QPlainTextEdit:disabled, QLineEdit:disabled, QSpinBox:disabled,
    QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {p.text_muted}; }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        background: {p.bg_elevated};
        border: none;
        width: 14px;
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    /* Выпадающий список — отдельное окно, и общий фон на него не
       распространяется: без этого правила он оставался системным белым. */
    QComboBox QAbstractItemView {{
        background: {p.bg_elevated};
        color: {p.text_primary};
        border: 1px solid {p.border};
        selection-background-color: {p.accent_muted};
        selection-color: {p.text_primary};
    }}
    QPlainTextEdit {{
        font-family: "JetBrains Mono", Consolas, monospace;
        font-size: 13px;
    }}

    QSplitter::handle {{ background: {p.border}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    QStatusBar {{
        background: {p.bg_elevated};
        border-top: 1px solid {p.border};
        color: {p.text_muted};
    }}
    QStatusBar::item {{ border: none; }}

    QLabel[role="hint"] {{ color: {p.text_muted}; }}
    /* Оригинал для перевода: чужой текст, только для чтения. Приглушён,
       чтобы не спорить за внимание с тем, что человек пишет сам. */
    QPlainTextEdit[role="reference"] {{
        background: {p.bg_base};
        color: {p.text_muted};
        border: 1px solid {p.border};
        /* Обычный шрифт, а не моноширинный: оригинал читают, а не правят. */
        font-family: "Segoe UI", "Inter", system-ui;
    }}
    QLabel[role="warning"] {{ color: {p.warning}; }}

    QScrollBar:vertical {{ background: {p.bg_base}; width: 12px; margin: 0; }}
    QScrollBar:horizontal {{ background: {p.bg_base}; height: 12px; margin: 0; }}
    QScrollBar::handle {{
        background: {p.border};
        border-radius: {RADIUS["sm"]}px;
        min-height: 24px;
    }}
    QScrollBar::handle:hover {{ background: {p.text_muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

    QGroupBox {{
        border: 1px solid {p.border};
        border-radius: {RADIUS["md"]}px;
        margin-top: {SPACE["3"]}px;
        padding-top: {SPACE["2"]}px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: {SPACE["2"]}px;
        padding: 0 {SPACE["1"]}px;
        color: {p.text_muted};
    }}

    /* Всё, что ниже, раньше рисовал системный стиль Windows. На машине со
       светлой системной темой это давало белые кнопки и белые вкладки с
       белым же текстом поверх — цвет текста брался отсюда, а фон нет. */

    QDialog, QMessageBox {{ background: {p.bg_base}; }}

    QTabWidget::pane {{
        border: 1px solid {p.border};
        border-radius: {RADIUS["sm"]}px;
        background: {p.bg_base};
        top: -1px;
    }}
    /* Полосу вкладок надо явно прижать влево: со своими правилами Qt
       начинает её сдвигать, и первая вкладка обрезается краем окна. */
    QTabWidget::tab-bar {{ left: 0; alignment: left; }}
    /* Полосу закрашиваем, а не оставляем прозрачной. Между вкладками есть
       зазор в два пикселя, и в нём — вместе со скруглёнными углами —
       просвечивала неокрашенная подложка виджета: белые клинья по краям
       каждой вкладки. */
    QTabBar {{ background: {p.bg_sunken}; }}
    QTabBar::tab {{
        background: {p.bg_elevated};
        color: {p.text_muted};
        border: 1px solid {p.border};
        border-bottom: none;
        border-top-left-radius: {RADIUS["sm"]}px;
        border-top-right-radius: {RADIUS["sm"]}px;
        padding: 6px 12px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{ background: {p.bg_base}; color: {p.text_primary}; }}
    QTabBar::tab:hover:!selected {{ background: {p.bg_sunken}; color: {p.text_primary}; }}
    QTabBar::tab:disabled {{ color: {p.text_muted}; }}

    QPushButton {{
        background: {p.bg_elevated};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: {RADIUS["sm"]}px;
        padding: 4px 10px;
        min-width: 56px;
    }}
    QPushButton:hover {{ background: {p.bg_sunken}; }}
    QPushButton:pressed {{ background: {p.accent_muted}; }}
    QPushButton:default {{ border: 1px solid {p.accent}; }}
    QPushButton:disabled {{ color: {p.text_muted}; background: {p.bg_base}; }}

    QCheckBox, QRadioButton {{ color: {p.text_primary}; spacing: 6px; background: transparent; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {p.text_muted}; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid {p.border};
        background: {p.bg_sunken};
    }}
    QCheckBox::indicator {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {p.accent};
        border: 1px solid {p.accent};
    }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
        border: 1px solid {p.accent};
    }}

    QListView, QListWidget, QTreeView, QTreeWidget {{
        background: {p.bg_base};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: {RADIUS["sm"]}px;
        selection-background-color: {p.accent_muted};
        selection-color: {p.text_primary};
        outline: none;
    }}
    QListView::item, QTreeView::item {{ padding: 3px 4px; }}
    QListView::item:hover, QTreeView::item:hover {{ background: {p.bg_elevated}; }}

    QDockWidget {{ color: {p.text_primary}; }}
    QDockWidget::title {{
        background: {p.bg_elevated};
        color: {p.text_muted};
        border-bottom: 1px solid {p.border};
        padding: 5px {SPACE["2"]}px;
    }}

    QProgressBar {{
        background: {p.bg_sunken};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: {RADIUS["sm"]}px;
        text-align: center;
    }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: {RADIUS["sm"]}px; }}

    QSlider::groove:horizontal {{
        height: 4px;
        background: {p.bg_sunken};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {p.accent};
        width: 12px;
        margin: -5px 0;
        border-radius: 6px;
    }}

    QToolTip {{
        background: {p.bg_elevated};
        color: {p.text_primary};
        border: 1px solid {p.border};
        padding: 4px 6px;
    }}
    """


#: Встроенные темы по именам, как они лежат в настройках. Темы из файлов
#: добавляются к ним в :mod:`sfstudio.ui.theme_pack`.
THEMES: dict[str, str] = {
    "dark": tr('Тёмная'),
    "light": tr('Светлая'),
    "sepia": tr('Тёплая'),
    "contrast": tr('Контрастная'),
}

#: Палитры встроенных тем.
BUILTIN: dict[str, Palette] = {
    "dark": DARK, "light": LIGHT, "sepia": SEPIA, "contrast": CONTRAST,
}


def palette_by_name(name: str | None) -> Palette:
    """Палитра встроенной темы по имени. Незнакомое имя — тёмная.

    Незнакомое имя означает либо конфиг от будущей версии, либо правку
    руками: в обоих случаях запуск с обычной темой лучше отказа.
    """
    return BUILTIN.get(str(name or "").strip().lower(), DARK)


# --------------------------------------------------------------------------- #
# Цвета
# --------------------------------------------------------------------------- #

#: Готовые акцентные цвета. Названия обычные, а не «Аквамарин №3»:
#: человек выбирает глазами, подпись нужна лишь чтобы отличить одно от
#: другого в списке.
ACCENTS: tuple[tuple[str, str], ...] = (
    (tr('Синий'), "#4C8DFF"),
    (tr('Голубой'), "#2BB3C0"),
    (tr('Зелёный'), "#3FB950"),
    (tr('Жёлтый'), "#D2A122"),
    (tr('Оранжевый'), "#E07B39"),
    (tr('Красный'), "#E05252"),
    (tr('Розовый'), "#DE5A9B"),
    (tr('Фиолетовый'), "#8B5CF6"),
)


def mix(first: str, second: str, share: float) -> str:
    """Смешивает два цвета. ``share`` — доля второго, от 0 до 1."""
    from sfstudio.core.color import RGBA

    a = RGBA.from_hex(first)
    b = RGBA.from_hex(second)
    share = max(0.0, min(1.0, share))
    return RGBA(
        round(a.r + (b.r - a.r) * share),
        round(a.g + (b.g - a.g) * share),
        round(a.b + (b.b - a.b) * share),
    ).to_hex()


def is_dark(palette: Palette) -> bool:
    """Тёмная ли тема. Решает яркость основного фона, а не её название."""
    from sfstudio.core.color import RGBA

    return RGBA.from_hex(palette.bg_base).luminance < 0.5


def with_accent(palette: Palette, accent: str | None) -> Palette:
    """Палитра с другим акцентным цветом.

    Меняется не одно поле: приглушённый акцент (фон выделения) и цвет
    направляющих выводятся из основного. Задавать их по отдельности значило
    бы просить человека подобрать три согласованных цвета вместо одного.

    Фон выделения — это акцент, на четыре пятых разбавленный фоном темы.
    Направление получается само: на тёмной теме выделение выходит темнее
    акцента, на светлой — светлее. Коэффициент один на все темы; пробовал
    разные для тёмных и светлых, но разница ни на читаемости, ни на виде
    не сказалась, а лишнее ветвление осталось бы навсегда.
    """
    from dataclasses import replace

    from sfstudio.core.color import RGBA

    if not accent:
        return palette
    try:
        RGBA.from_hex(accent)
    except (ValueError, IndexError):
        # Цвет правят руками в settings.json; мусор там не повод не
        # запуститься с обычной темой.
        return palette

    share = 0.8
    return replace(
        palette,
        accent=accent,
        accent_muted=mix(accent, palette.bg_base, share),
        guide=accent,
    )


#: Пределы масштаба интерфейса. Ниже 70% подписи перестают читаться, выше
#: 200% окно не помещается на экран целиком даже на большом мониторе.
MIN_FONT_SCALE = 70
MAX_FONT_SCALE = 200


def apply_font_scale(app, percent: int | float, *, base_pt: float = 0.0) -> float:
    """Меняет размер шрифта интерфейса. Возвращает получившийся кегль.

    Масштабируется шрифт приложения, а не вся отрисовка: размеры виджетов в
    Qt считаются от метрик шрифта, поэтому вслед за ним подтягиваются и
    отступы, и высота строк, и кнопки. Растровых ассетов в программе нет, так
    что размывать нечего.

    Исходный кегль запоминается на самом приложении: без этого повторное
    применение множило бы масштаб на уже увеличенный шрифт, и вторая попытка
    выставить 150% давала бы 225%.
    """
    if app is None:
        return 0.0

    stored = app.property("sfstudio_base_font_pt")
    if base_pt:
        base = float(base_pt)
    elif isinstance(stored, (int, float)) and stored > 0:
        base = float(stored)
    else:
        base = float(app.font().pointSizeF())
        if base <= 0:  # шрифт задан в пикселях — точки не спросить
            base = 9.0
        app.setProperty("sfstudio_base_font_pt", base)

    share = max(MIN_FONT_SCALE, min(MAX_FONT_SCALE, float(percent))) / 100.0
    font = app.font()
    font.setPointSizeF(base * share)
    app.setFont(font)
    return base * share


def repolish(widget) -> None:
    """Заставляет Qt перечитать свойство ``role`` у виджета.

    Тема задаёт цвет селектором по свойству, но смена свойства сама по себе
    стиль не пересчитывает: надпись остаётся прежнего цвета, и
    предупреждение выглядит как обычная подсказка. Функция здесь, а не в
    каждом окне: нужна она уже третьему.
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
