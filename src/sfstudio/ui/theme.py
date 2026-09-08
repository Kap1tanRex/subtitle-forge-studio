"""Дизайн-токены и генерация QSS.

Один источник правды для цветов: и таблица стилей Qt, и кастомная отрисовка
(превью, будущий таймлайн) берут значения отсюда. Иначе виджеты и рисование
разъезжаются при первой же правке палитры.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DARK", "LIGHT", "THEMES", "Palette", "build_qss", "palette_by_name", "repolish"]


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

    QPlainTextEdit, QLineEdit, QSpinBox, QComboBox {{
        background: {p.bg_sunken};
        border: 1px solid {p.border};
        border-radius: {RADIUS["sm"]}px;
        padding: 4px 6px;
        selection-background-color: {p.accent_muted};
    }}
    QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
        border: 1px solid {p.accent};
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
    """


#: Темы по именам, как они лежат в настройках.
THEMES: dict[str, str] = {"dark": "Тёмная", "light": "Светлая"}


def palette_by_name(name: str | None) -> Palette:
    """Палитра по имени темы. Незнакомое имя — тёмная.

    Незнакомое имя означает либо конфиг от будущей версии, либо правку
    руками: в обоих случаях запуск с обычной темой лучше отказа.
    """
    return LIGHT if str(name or "").strip().lower() == "light" else DARK


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
