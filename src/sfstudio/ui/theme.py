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
    "build_qss", "is_dark", "mix", "repolish", "with_accent",
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
    #: Кнопки и всё, что приподнято над панелью.
    bg_raised: str
    #: Поля ввода. Отдельно от «углубления»: на светлых темах поле белое,
    #: а углубление под сегментами — серое.
    bg_field: str
    #: Рамка, которую надо заметить: выбранный сегмент, ручка, засечки.
    border_strong: str
    #: Разделитель строк — тише обычной рамки, иначе таблица рябит.
    line: str
    #: Акцент текстом и значком. На тёмных темах светлее самого акцента:
    #: синий #4C8DFF на почти чёрном читается хуже, чем как заливка.
    accent_text: str
    #: Текст на заливке акцентом.
    on_accent: str
    warning_text: str
    #: Подложка предупреждения: пауза меньше двух кадров, замечание QC.
    warning_bg: str
    on_warning: str
    danger_text: str
    #: Надпись на плашке курсора времени.
    on_playhead: str


#: Токены взяты из макета редизайна один в один.
DARK = Palette(
    bg_base="#17191E",
    bg_elevated="#1F2229",
    bg_sunken="#0F1114",
    border="#2B2F38",
    text_primary="#E6E8EC",
    text_muted="#A3AAB6",
    accent="#4C8DFF",
    accent_muted="#1E3A66",
    success="#3FB950",
    warning="#D29922",
    danger="#F85149",
    playhead="#F2F4F7",
    canvas_a="#22252C",
    canvas_b="#1C1F25",
    guide="#4C8DFF",
    wave_fill="#34404F",
    wave_rms="#6E8BB5",
    bg_raised="#2A2E37",
    bg_field="#111317",
    border_strong="#3B404B",
    line="#22252B",
    accent_text="#7FAEFF",
    on_accent="#0B1220",
    warning_text="#E3B341",
    warning_bg="#312B1F",
    on_warning="#1A1204",
    danger_text="#FF7B72",
    on_playhead="#0F1114",
)

LIGHT = Palette(
    bg_base="#FFFFFF",
    bg_elevated="#F2F4F7",
    bg_sunken="#E6E9EE",
    border="#D5DAE2",
    text_primary="#14161A",
    text_muted="#525A67",
    accent="#0B62E0",
    accent_muted="#D6E4FF",
    success="#1A7F37",
    warning="#9A6700",
    danger="#CF222E",
    playhead="#14161A",
    canvas_a="#E8EAEE",
    canvas_b="#DFE2E7",
    guide="#0B62E0",
    wave_fill="#C3CAD6",
    wave_rms="#7C90AC",
    bg_raised="#E4E8EE",
    bg_field="#FFFFFF",
    border_strong="#AEB6C2",
    line="#EDF0F3",
    accent_text="#0B62E0",
    on_accent="#FFFFFF",
    warning_text="#7A5200",
    warning_bg="#FFF4D6",
    on_warning="#FFFFFF",
    danger_text="#CF222E",
    on_playhead="#FFFFFF",
)

#: Тёплая светлая тема. Чисто белый фон при долгой работе с текстом устаёт
#: читать; бумажный оттенок гасит контраст, не трогая разборчивость.
SEPIA = Palette(
    bg_base="#FBF6EC",
    bg_elevated="#F1E6D3",
    bg_sunken="#E7D9C1",
    border="#D9C7A8",
    text_primary="#2A2318",
    text_muted="#5E503C",
    accent="#93531F",
    accent_muted="#EBD3B5",
    success="#4A7C2F",
    warning="#8A5D00",
    danger="#B3402E",
    playhead="#2A2318",
    canvas_a="#E7DCC8",
    canvas_b="#DFD3BC",
    guide="#93531F",
    wave_fill="#CDBDA0",
    wave_rms="#93866D",
    bg_raised="#E8DAC2",
    bg_field="#FFFCF5",
    border_strong="#B8A07A",
    line="#F0E6D4",
    accent_text="#93531F",
    on_accent="#FFFFFF",
    warning_text="#7A5200",
    warning_bg="#F6E3B8",
    on_warning="#FFFFFF",
    danger_text="#B3402E",
    on_playhead="#FBF6EC",
)

#: Высококонтрастная. Для слабого зрения и для работы при ярком свете:
#: чистый чёрный фон, белый текст, границы видно без вглядывания.
CONTRAST = Palette(
    bg_base="#000000",
    bg_elevated="#121212",
    bg_sunken="#000000",
    border="#7A7A7A",
    text_primary="#FFFFFF",
    text_muted="#D0D0D0",
    accent="#3DD6FF",
    accent_muted="#003B4A",
    success="#3DF06B",
    warning="#FFD400",
    danger="#FF6B6B",
    playhead="#FFFFFF",
    canvas_a="#141414",
    canvas_b="#0A0A0A",
    guide="#3DD6FF",
    wave_fill="#5A5A5A",
    wave_rms="#B4B4B4",
    bg_raised="#262626",
    bg_field="#000000",
    border_strong="#BDBDBD",
    line="#333333",
    accent_text="#3DD6FF",
    on_accent="#000000",
    warning_text="#FFD400",
    warning_bg="#2E2600",
    on_warning="#000000",
    danger_text="#FF6B6B",
    on_playhead="#000000",
)

SPACE = {"1": 4, "2": 8, "3": 12, "4": 16, "5": 24}
RADIUS = {"sm": 4, "md": 5, "lg": 6}

#: Шрифты. Inter — как в макете; на машине без него Qt возьмёт Segoe UI,
#: метрики у них близкие, и раскладка не поедет.
UI_FONT = '"Inter", "Segoe UI", system-ui'
MONO_FONT = '"JetBrains Mono", Consolas, monospace'


def build_qss(p: Palette) -> str:
    """Собирает таблицу стилей из палитры.

    Словарь ролей (свойство ``role`` у виджета) — чтобы окна не заводили
    свои цвета по месту:

    * ``panelhead`` — шапка колонки: приподнятый фон, акцентная черта сверху;
    * ``bar`` — служебная полоса: транспорт, шапка таблицы, подвал;
    * ``segment`` + кнопки ``seg`` — переключатель из нескольких положений;
    * ``icon`` — кнопка-значок 28×28 без рамки, ``outlined`` — с рамкой;
    * ``primary`` — главная кнопка, залитая акцентом;
    * ``section`` — заголовок раздела, ``caption`` — подпись над полем,
      ``hint`` — пояснение, ``kbd`` — сочетание клавиш рядом с кнопкой;
    * ``chip-warning`` — плашка предупреждения, ``badge`` — счётчик.
    """
    return f"""
    QWidget {{
        background: {p.bg_base};
        color: {p.text_primary};
        font-family: {UI_FONT};
        font-size: 13px;
    }}
    QMainWindow::separator {{
        background: {p.border_strong};
        width: 1px;
        height: 1px;
    }}

    /* -- меню ------------------------------------------------------------ */
    QMenuBar {{
        background: {p.bg_elevated};
        border-bottom: 1px solid {p.border};
        padding: 3px 6px;
        spacing: 2px;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 3px 9px;
        border-radius: {RADIUS["sm"]}px;
    }}
    QMenuBar::item:selected, QMenuBar::item:pressed {{ background: {p.bg_raised}; }}
    QMenu {{
        background: {p.bg_elevated};
        border: 1px solid {p.border};
        padding: {SPACE["1"]}px;
    }}
    QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: {RADIUS["sm"]}px; }}
    QMenu::item:selected {{ background: {p.accent_muted}; }}
    QMenu::item:disabled {{ color: {p.text_muted}; }}
    QMenu::separator {{ height: 1px; background: {p.border}; margin: {SPACE["1"]}px 6px; }}

    QToolBar {{
        background: {p.bg_elevated};
        border-bottom: 1px solid {p.border};
        padding: {SPACE["1"]}px;
        spacing: {SPACE["1"]}px;
    }}

    /* -- кнопки ------------------------------------------------------------ */
    QPushButton {{
        background: {p.bg_raised};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: {RADIUS["md"]}px;
        padding: 0 10px;
        min-height: 26px;
        min-width: 48px;
    }}
    QPushButton:hover {{ border-color: {p.border_strong}; }}
    QPushButton:pressed, QPushButton:checked {{ background: {p.accent_muted}; }}
    QPushButton:default {{ border: 1px solid {p.accent}; }}
    QPushButton:disabled {{ color: {p.text_muted}; background: {p.bg_elevated}; }}
    QPushButton[role="primary"] {{
        background: {p.accent};
        color: {p.on_accent};
        border: 0;
        border-radius: {RADIUS["lg"]}px;
        font-weight: 600;
        min-height: 34px;
    }}
    QPushButton[role="primary"]:hover {{ background: {p.accent_text}; }}
    QPushButton[role="primary"]:disabled {{ background: {p.bg_raised}; color: {p.text_muted}; }}
    QPushButton[role="link"] {{
        background: transparent;
        border: 0;
        color: {p.accent_text};
        font-weight: 500;
        padding: 0 8px;
        min-width: 0;
    }}

    QToolButton {{
        background: transparent;
        color: {p.text_primary};
        border: 1px solid transparent;
        border-radius: {RADIUS["md"]}px;
        padding: 4px 8px;
    }}
    QToolButton:hover {{ background: {p.bg_raised}; }}
    QToolButton:pressed,
    QToolButton:checked {{ background: {p.accent_muted}; color: {p.accent_text}; }}
    QToolButton:disabled {{ color: {p.text_muted}; }}
    QToolButton[role="icon"] {{ padding: 0; }}
    /* Играть — круглая, залитая акцентом: главная кнопка транспорта. */
    QToolButton[role="play"] {{
        background: {p.accent};
        border: 0;
        border-radius: 19px;
        padding: 0;
    }}
    QToolButton[role="play"]:hover {{ background: {p.accent_text}; }}
    QToolButton[role="play"]:disabled {{ background: {p.bg_raised}; }}
    QFrame[role="divider"] {{ background: {p.border}; border: 0; }}
    /* Готовые наборы — фишками: их выбирают, а не нажимают как команду. */
    QToolButton[role="chip"] {{
        border: 1px solid {p.border};
        border-radius: 14px;
        padding: 0 10px;
        color: {p.text_primary};
    }}
    QToolButton[role="chip"]:hover {{ border-color: {p.accent}; background: {p.accent_muted}; }}
    QPlainTextEdit[role="styleline"] {{
        background: {p.bg_elevated};
        color: {p.text_muted};
        border: 0;
        font-size: 11px;
    }}
    /* Вход в командную палитру — выглядит полем поиска. */
    QPushButton[role="command"] {{
        background: {p.bg_field};
        border: 1px solid {p.border};
        border-radius: {RADIUS["md"]}px;
        padding: 0;
        min-width: 280px;
        max-width: 280px;
        min-height: 24px;
        max-height: 24px;
    }}
    QPushButton[role="command"]:hover {{ border-color: {p.border_strong}; }}
    QLabel[role="keycap"] {{
        color: {p.text_muted};
        font-family: {MONO_FONT};
        font-size: 11px;
        border: 1px solid {p.border};
        border-radius: 3px;
        padding: 0 5px;
    }}
    /* Кнопка с рамкой: «+ Реплика», шаг кадра у полей времени. */
    QToolButton[role="outlined"] {{
        background: {p.bg_raised};
        border: 1px solid {p.border};
        padding: 0 10px 0 7px;
        min-height: 26px;
        font-weight: 500;
    }}
    QToolButton[role="outlined"]:hover {{ border-color: {p.border_strong}; }}

    /* -- переключатель сегментами ------------------------------------------- */
    QFrame[role="segment"] {{
        background: {p.bg_sunken};
        border: 0;
        border-radius: {RADIUS["lg"]}px;
    }}
    QToolButton[role="seg"] {{
        background: transparent;
        color: {p.text_muted};
        border: 1px solid transparent;
        border-radius: {RADIUS["md"]}px;
        padding: 0 8px;
        min-height: 24px;
    }}
    QToolButton[role="seg"]:hover {{ color: {p.text_primary}; }}
    QToolButton[role="seg"]:checked {{
        background: {p.bg_elevated};
        color: {p.text_primary};
        border: 1px solid {p.border_strong};
        font-weight: 600;
    }}

    /* -- поля ввода --------------------------------------------------------- */
    QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {p.bg_field};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: {RADIUS["md"]}px;
        padding: 4px 8px;
        selection-background-color: {p.accent_muted};
        selection-color: {p.text_primary};
    }}
    /* Список выбора выглядит кнопкой, как в макете: его не набирают, а
       выбирают. Редактируемый остаётся полем. */
    QComboBox {{ background: {p.bg_raised}; }}
    QComboBox:editable {{ background: {p.bg_field}; }}
    QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus {{
        border: 1px solid {p.accent};
    }}
    QPlainTextEdit:disabled, QLineEdit:disabled, QSpinBox:disabled,
    QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {p.text_muted}; }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        background: {p.bg_elevated};
        border: none;
        border-left: 1px solid {p.border};
        width: 16px;
    }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    /* Выпадающий список — отдельное окно, и общий фон на него не
       распространяется: без этого правила он оставался системным белым. */
    QComboBox QAbstractItemView {{
        background: {p.bg_elevated};
        color: {p.text_primary};
        border: 1px solid {p.border};
        selection-background-color: {p.accent_muted};
        selection-color: {p.text_primary};
        outline: none;
    }}
    QPlainTextEdit {{
        font-family: {MONO_FONT};
        font-size: 14px;
    }}
    /* Заметку пишут фразами, а не правят по символу — обычным шрифтом. */
    QPlainTextEdit[role="note"] {{ font-family: {UI_FONT}; font-size: 13px; }}
    QLineEdit[role="timecode"], QLabel[role="timecode"] {{
        font-family: {MONO_FONT};
    }}
    QLabel[role="timecode"] {{ font-size: 15px; font-weight: 500; }}
    /* Поле поиска в шапке: значок лупы стоит внутри, рамка общая. */
    QLineEdit[role="search"] {{ padding-left: 26px; }}

    QSplitter::handle {{ background: {p.border}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}
    /* Граница между списком и текстом — толще, её тянут чаще остальных. */
    QSplitter#list_split::handle {{
        background: {p.bg_elevated};
        border-top: 1px solid {p.border};
        border-bottom: 1px solid {p.border};
    }}

    /* -- таблица ------------------------------------------------------------ */
    QTableView {{
        background: {p.bg_base};
        alternate-background-color: {p.bg_base};
        gridline-color: {p.line};
        border: none;
        selection-background-color: {p.accent_muted};
        selection-color: {p.text_primary};
        outline: none;
    }}
    QTableView::item {{
        padding: 0 4px;
        border: 0;
        border-bottom: 1px solid {p.line};
    }}
    QTableView::item:selected {{ background: {p.accent_muted}; color: {p.text_primary}; }}
    QHeaderView {{ background: {p.bg_elevated}; }}
    QHeaderView::section {{
        background: {p.bg_elevated};
        color: {p.text_muted};
        border: none;
        border-bottom: 1px solid {p.border};
        padding: 0 4px;
        min-height: 26px;
        font-size: 12px;
        font-weight: 600;
    }}

    /* -- подписи -------------------------------------------------------------- */
    QLabel {{ background: transparent; }}
    QLabel[role="hint"] {{ color: {p.text_muted}; font-size: 12px; }}
    QLabel[role="caption"] {{ color: {p.text_muted}; font-size: 11px; }}
    QLabel[role="section"] {{ font-size: 13px; font-weight: 600; }}
    QLabel[role="title"] {{ font-size: 15px; font-weight: 600; }}
    QLabel[role="kbd"] {{
        color: {p.text_muted};
        font-family: {MONO_FONT};
        font-size: 11px;
    }}
    QLabel[role="mono"] {{ font-family: {MONO_FONT}; font-size: 12px; }}
    QLabel[role="warning"] {{ color: {p.warning_text}; }}
    QLabel[role="chip-warning"] {{
        background: {p.warning_bg};
        color: {p.warning_text};
        border-radius: {RADIUS["md"]}px;
        padding: 0 8px;
        font-family: {MONO_FONT};
        min-height: 28px;
    }}
    QLabel[role="badge"] {{
        background: {p.warning};
        color: {p.on_warning};
        border-radius: 8px;
        padding: 0 5px;
        font-size: 11px;
        font-weight: 600;
        min-height: 16px;
        max-height: 16px;
    }}
    QLabel[role="pill"] {{
        background: {p.bg_raised};
        border-radius: {RADIUS["sm"]}px;
        padding: 0 7px;
        font-family: {MONO_FONT};
        font-size: 11px;
        min-height: 20px;
    }}

    /* Оригинал для перевода: чужой текст, только для чтения. Приглушён,
       чтобы не спорить за внимание с тем, что человек пишет сам. */
    QLabel[role="reference"] {{
        background: {p.bg_elevated};
        color: {p.text_muted};
        border-radius: {RADIUS["sm"]}px;
        padding: 6px 10px;
    }}
    QPlainTextEdit[role="reference"] {{
        background: {p.bg_elevated};
        color: {p.text_muted};
        border: 0;
        font-family: {UI_FONT};
        font-size: 13px;
    }}

    /* -- полосы и шапки ------------------------------------------------------- */
    QWidget[role="bar"] {{ background: {p.bg_elevated}; }}
    QWidget[role="seekstrip"] {{
        background: {p.bg_elevated};
        border-top: 1px solid {p.border};
    }}
    QWidget[role="toolhead"] {{
        background: {p.bg_base};
        border-bottom: 1px solid {p.border};
    }}
    QWidget[role="footer"] {{
        background: {p.bg_elevated};
        border-top: 1px solid {p.border};
    }}
    QWidget[role="sunken"] {{ background: {p.bg_sunken}; }}
    QWidget[role="plain"] {{ background: transparent; }}
    QFrame[role="card"] {{
        background: transparent;
        border: 1px solid {p.border};
        border-radius: {RADIUS["lg"]}px;
    }}
    QFrame[role="warning-box"] {{
        background: {p.warning_bg};
        border: 0;
        border-radius: {RADIUS["md"]}px;
    }}
    QFrame[role="warning-box"] QLabel {{ color: {p.text_primary}; }}

    QStatusBar {{
        background: {p.bg_elevated};
        border-top: 1px solid {p.border};
        color: {p.text_muted};
        font-size: 12px;
    }}
    QStatusBar::item {{ border: none; }}
    QStatusBar QLabel {{ color: {p.text_muted}; font-size: 12px; padding: 0 6px; }}
    QStatusBar QToolButton {{ padding: 0 6px; font-size: 12px; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle {{
        background: {p.border_strong};
        border-radius: {RADIUS["sm"]}px;
        min-height: 24px;
        min-width: 24px;
        margin: 2px;
    }}
    QScrollBar::handle:hover {{ background: {p.text_muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    QScrollArea {{ border: none; }}

    QGroupBox {{
        border: 1px solid {p.border};
        border-radius: {RADIUS["lg"]}px;
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

    /* Вкладки — сегментами в углублении, как переключатель: полосу видно
       целиком, и выбранная читается по рамке, а не по оттенку. */
    QTabWidget::pane {{
        border: 1px solid {p.border};
        border-radius: {RADIUS["md"]}px;
        background: {p.bg_base};
        top: -1px;
    }}
    /* Полосу вкладок надо явно прижать влево: со своими правилами Qt
       начинает её сдвигать, и первая вкладка обрезается краем окна. */
    QTabWidget::tab-bar {{ left: 0; alignment: left; }}
    /* Полосу закрашиваем, а не оставляем прозрачной: в зазорах между
       вкладками иначе просвечивала неокрашенная подложка. */
    QTabBar {{ background: {p.bg_sunken}; }}
    QTabBar::tab {{
        background: transparent;
        color: {p.text_muted};
        border: 1px solid transparent;
        border-radius: {RADIUS["md"]}px;
        padding: 4px 10px;
        margin: 2px 1px;
    }}
    QTabBar::tab:selected {{
        background: {p.bg_elevated};
        color: {p.text_primary};
        border: 1px solid {p.border_strong};
        font-weight: 600;
    }}
    QTabBar::tab:hover:!selected {{ color: {p.text_primary}; }}
    QTabBar::tab:disabled {{ color: {p.text_muted}; }}
    QTabBar QToolButton {{ background: {p.bg_sunken}; padding: 0; border: 0; }}

    /* Колонка справа: шапка приподнята, сверху акцентная черта — видно,
       какая панель рабочая. Сами вкладки лежат в углублении. */
    QWidget[role="panelhead"] {{
        background: {p.bg_elevated};
        border-top: 2px solid {p.accent};
        border-bottom: 1px solid {p.border};
    }}
    QTabWidget#inspector {{
        background: {p.bg_elevated};
        border-top: 2px solid {p.accent};
    }}
    QTabWidget#inspector::pane {{
        border: 0;
        border-top: 1px solid {p.border};
        background: {p.bg_base};
    }}
    QTabWidget#inspector > QTabBar {{
        background: {p.bg_sunken};
        border-radius: {RADIUS["lg"]}px;
        margin: 5px 0;
    }}
    QTabWidget#inspector > QTabBar::tab {{ padding: 3px 6px; min-height: 18px; }}

    QCheckBox, QRadioButton {{ color: {p.text_primary}; spacing: 6px; background: transparent; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {p.text_muted}; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid {p.border_strong};
        background: {p.bg_field};
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
        border-radius: {RADIUS["lg"]}px;
        selection-background-color: {p.accent_muted};
        selection-color: {p.text_primary};
        outline: none;
    }}
    QListView::item, QTreeView::item {{ padding: 4px 6px; }}
    QListView::item:hover, QTreeView::item:hover {{ background: {p.bg_elevated}; }}
    QListView::item:selected, QTreeView::item:selected {{
        background: {p.accent_muted};
        color: {p.text_primary};
    }}

    QDockWidget {{ color: {p.text_primary}; }}
    QDockWidget::title {{
        background: {p.bg_elevated};
        color: {p.text_muted};
        border-bottom: 1px solid {p.border};
        padding: 5px {SPACE["2"]}px;
    }}

    QProgressBar {{
        background: {p.bg_raised};
        color: {p.text_primary};
        border: 0;
        border-radius: 2px;
        text-align: center;
    }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 2px; }}
    QProgressBar[role="done"]::chunk {{ background: {p.success}; }}

    QSlider {{ background: transparent; }}
    QSlider::groove:horizontal {{
        height: 4px;
        background: {p.bg_raised};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {p.accent};
        width: 12px;
        margin: -4px 0;
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
        # Текстом акцент на тёмной теме светлее заливки — как в макете.
        accent_text=mix(accent, "#FFFFFF", 0.3) if is_dark(palette) else accent,
        on_accent=RGBA.from_hex(accent).contrasting_text().to_hex(),
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
