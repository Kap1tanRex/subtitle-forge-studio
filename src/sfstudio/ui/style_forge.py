"""Кузница стилей: настройка оформления субтитра при живом кадре.

Отличие от окна шаблонов (:mod:`sfstudio.ui.style_dialog`) в назначении.
Там — библиотека: сохранить, назвать, выбрать из списка. Здесь — рабочее
место: крутишь ползунок и сразу видишь, что получилось, не открывая ничего
и не закрывая.

Три решения, которые стоит объяснить.

**Предпросмотр рисует настоящая libass, а не Qt.** Своя отрисовка врала бы:
обводка, тень и плашка в ASS считаются иначе, чем в Qt, и «похоже» здесь
недостаточно — человек по этому кадру принимает решение. Проще говоря, это
тот же движок, что покажет плеер.

**Фон под предпросмотром переключается.** Белая обводка не видна на светлом,
чёрная — на тёмном, а настоящее видео пёстрое. Один фон означал бы, что
половину ошибок оформления видно не будет.

**Строка ``Style:`` показана как есть.** Кто правит ASS руками — сверит её
глазами; кто не правит — не заметит. Прятать то, что всё равно уйдёт в файл,
незачем.

Ширина. Виджет живёт и во вкладке инспектора шириной в треть экрана, и в
отдельном окне во весь экран. Поэтому раскладка переключается сама: узко —
кадр сверху, настройки под ним; широко — два столбца, как в макете.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.core.color import RGBA
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.style import ALIGNMENT_NAMES, BorderStyle, SubtitleStyle
from sfstudio.io.formats.ass import format_style_line
from sfstudio.ui.font_box import FontComboBox
from sfstudio.ui.overlay import blit_layers
from sfstudio.ui.theme import DARK, Palette
from sfstudio.ui.widgets import Segmented, caption, hbox, kbd_button, label, section

__all__ = ["AlignmentPad", "PresetCards", "PreviewBackground", "StyleForge"]

#: Текст в предпросмотре: две строки, чтобы видеть межстрочный интервал,
#: и кириллица с латиницей — шрифт может не знать одну из них.
PREVIEW_TEXT = tr('Пример кинематографичного субтитра\\Nс обводкой и тенью — 1080p')


class PreviewBackground:
    """Чем подложить кадр предпросмотра."""

    DARK = "dark"
    LIGHT = "light"
    GRADIENT = "gradient"

    TITLES: tuple[tuple[str, str], ...] = (
        (DARK, tr('Тёмный')),
        (LIGHT, tr('Светлый')),
        (GRADIENT, tr('Градиент')),
    )


class AlignmentPad(QWidget):
    """Девять кнопок как на цифровой клавиатуре — нотация ASS.

    Расположение кнопок повторяет numpad: 7 8 9 сверху, 1 2 3 снизу. Так же
    это записано в самом формате, и переучивать здесь нечего.
    """

    changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[int, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        for row, line in enumerate(((7, 8, 9), (4, 5, 6), (1, 2, 3))):
            for column, value in enumerate(line):
                button = QToolButton()
                button.setText(str(value))
                button.setCheckable(True)
                button.setFixedSize(34, 30)
                button.setToolTip(ALIGNMENT_NAMES[value])
                button.clicked.connect(
                    lambda _checked, v=value: self.set_alignment(v)
                )
                self._group.addButton(button, value)
                self._buttons[value] = button
                grid.addWidget(button, row, column)
        self.set_alignment(2)

    def alignment(self) -> int:
        return self._current

    def set_alignment(self, value: int) -> None:
        if value not in self._buttons:
            return
        changed = getattr(self, "_current", None) != value
        self._current = value
        for key, button in self._buttons.items():
            button.setChecked(key == value)
        if changed:
            self.changed.emit(value)


class PresetCards(QWidget):
    """Готовые наборы карточками, а не списком.

    Список прячет описание за наведением, а карточка показывает сразу и
    название, и шрифт, и цвет текста — то есть ровно то, по чему набор и
    выбирают. Ради шести пунктов это стоит места на экране.

    Число колонок считается по ширине: виджет живёт и в узкой вкладке, и в
    отдельном окне, а карточка уже 150 пикселей не читается.
    """

    #: Выбрали набор с таким именем.
    chosen = Signal(str)

    #: Наименьшая ширина карточки, при которой название ещё умещается.
    CARD_W = 190

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QToolButton] = []
        self._columns = 0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._build()

    def _build(self) -> None:
        from sfstudio.services.style_presets import BUILTIN_PRESETS

        for preset in BUILTIN_PRESETS:
            style = preset.style
            button = QToolButton()
            button.setText(preset.name)
            button.setToolTip(
                f"{preset.description}\n\nШрифт: {style.fontname}, "
                f"{style.fontsize:.0f} px"
            )
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setIcon(_dot_icon(style.primary))
            button.setIconSize(QSize(12, 12))
            button.setMinimumHeight(28)
            button.setProperty("role", "chip")
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda _c, name=preset.name: self.chosen.emit(name))
            self._buttons.append(button)
        self._relayout(2)

    def _relayout(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for button in self._buttons:
            self._grid.removeWidget(button)
        for index, button in enumerate(self._buttons):
            self._grid.addWidget(button, index // columns, index % columns)

    @classmethod
    def columns_for(cls, width: int) -> int:
        """Сколько карточек помещается в такую ширину. Минимум одна."""
        return max(1, width // cls.CARD_W)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout(self.columns_for(self.width()))


def _dot_icon(colour: RGBA) -> QIcon:
    """Кружок цвета текста для карточки набора."""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(QColor(120, 120, 120), 1))
    painter.setBrush(QColor(colour.r, colour.g, colour.b))
    painter.drawEllipse(1, 1, 11, 11)
    painter.end()
    return QIcon(pixmap)


class _Swatch(QPushButton):
    """Кнопка цвета: сам цвет плюс его запись, как её пишут в файле."""

    changed = Signal()

    def __init__(self, colour: RGBA, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = colour
        self.setMinimumHeight(28)
        self.clicked.connect(self._pick)
        self._sync()

    def _sync(self) -> None:
        colour = self._colour
        # Чёрный текст на светлом, белый на тёмном — иначе подпись пропадает
        # ровно на тех цветах, ради которых кнопку и нажимают.
        luminance = 0.299 * colour.r + 0.587 * colour.g + 0.114 * colour.b
        ink = "#101010" if luminance > 140 else "#F0F0F0"
        self.setText(f"#{colour.r:02X}{colour.g:02X}{colour.b:02X}")
        self.setStyleSheet(
            f"text-align: left; padding-left: 8px;"
            f"background: rgb({colour.r},{colour.g},{colour.b}); color: {ink};"
        )

    def _pick(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        current = QColor(self._colour.r, self._colour.g, self._colour.b, self._colour.a)
        chosen = QColorDialog.getColor(
            current, self, tr('Цвет'), QColorDialog.ShowAlphaChannel
        )
        if not chosen.isValid():
            return
        self._colour = RGBA(chosen.red(), chosen.green(), chosen.blue(), chosen.alpha())
        self._sync()
        self.changed.emit()

    def colour(self) -> RGBA:
        return self._colour

    def set_colour(self, value: RGBA) -> None:
        self._colour = value
        self._sync()


class _Preview(QWidget):
    """Кадр 16:9 с текстом, отрисованным настоящей libass."""

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._background = PreviewBackground.GRADIENT
        self._doc = SubtitleDocument.blank((1920, 1080))
        self._doc.create_event(0, 10_000, PREVIEW_TEXT)
        self._renderer = None
        self.setMinimumHeight(110)
        self._init_renderer()
        self.set_style(SubtitleStyle())

    def _init_renderer(self) -> None:
        try:
            from sfstudio.render.renderer import LibassRenderer

            self._renderer = LibassRenderer()
        except (OSError, RuntimeError):
            # Без libass остаётся честная заглушка: рисовать «примерно так»
            # своими силами значило бы обещать то, чего программа не покажет.
            self._renderer = None

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def set_style(self, style: SubtitleStyle) -> None:
        self._doc.styles["Default"] = replace(style, name="Default")
        self._doc.bump_revision()
        if self._renderer is not None:
            self._renderer.invalidate()
        self.update()

    def set_background(self, kind: str) -> None:
        self._background = kind
        self.update()

    def set_text(self, text: str) -> None:
        if self._doc.events:
            self._doc.events[0].set_text(text or PREVIEW_TEXT)
            self._doc.bump_revision()
            if self._renderer is not None:
                self._renderer.invalidate()
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(427, 150)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = self.rect()
        self._paint_background(painter, rect)

        if self._renderer is None:
            painter.setPen(QColor(self._palette.text_muted))
            painter.drawText(
                rect, Qt.AlignCenter,
                tr('libass недоступна — предпросмотр не рисуется'),
            )
            painter.end()
            return

        width, height = max(1, rect.width()), max(1, rect.height())
        self._renderer.set_frame_size(width, height, self._doc.script_info.play_res)
        try:
            layers = self._renderer.render(self._doc, 1000)
        except Exception:
            # Стиль правят на лету, и промежуточное значение бывает таким,
            # что libass отказывается его рисовать. Ронять окно из-за
            # полусекундного состояния нельзя.
            painter.end()
            return

        blit_layers(painter, layers)
        painter.end()

    def _paint_background(self, painter: QPainter, rect) -> None:
        if self._background == PreviewBackground.LIGHT:
            painter.fillRect(rect, QColor("#E8E8E8"))
            return
        if self._background == PreviewBackground.DARK:
            painter.fillRect(rect, QColor("#101014"))
            return

        # Градиент — грубая замена настоящему кадру: он даёт и светлое, и
        # тёмное разом, а на однотонном фоне половина ошибок оформления
        # просто не видна.
        from PySide6.QtGui import QLinearGradient

        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor("#2A2350"))
        gradient.setColorAt(0.55, QColor("#6E3B4E"))
        gradient.setColorAt(1.0, QColor("#C2703C"))
        painter.fillRect(rect, gradient)


class StyleForge(QWidget):
    """Настройка оформления с живым кадром."""

    #: Оформление изменилось — окно применяет его туда, куда смотрит
    #: переключатель «Применить к».
    style_changed = Signal(object)
    #: Переключили «Применить к»: ``True`` — к стилю, ``False`` — к выделенным.
    target_changed = Signal(bool)
    #: Ручное положение реплики: ``(x, y)`` или ``None`` — убрать.
    position_changed = Signal(object)

    def __init__(
        self,
        style: SubtitleStyle | None = None,
        palette: Palette = DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._syncing = True
        #: Выбирал ли человек шрифт в этом окне. См. :meth:`style`.
        self._font_chosen = False
        #: Раздали ли место между настройками и кадром. См. ``_share_space``.
        self._space_shared = False
        #: Сколько реплик выделено — от этого зависит, куда пойдёт правка.
        self._selected = 0
        self._style = replace(style) if style is not None else SubtitleStyle()
        self.setProperty("scrolls", True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())

        # Сплиттер, а не жёсткая раскладка: во вкладке колонки кадр нужен
        # сверху, в отдельном окне — сбоку, и границу между ними человек
        # двигает сам.
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.addWidget(self._preview_side())
        self._splitter.addWidget(self._controls())
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 4)
        self._splitter.setCollapsible(1, False)
        root.addWidget(self._splitter, 1)

        self._syncing = False
        self.set_style(self._style)
        self.set_target(0, self._style.name)

    # -- построение --------------------------------------------------------------- #

    def _header(self) -> QWidget:
        """«Применить к»: выделенным репликам или стилю целиком.

        Разные операции по сути. Выделенным — правка ложится тегами в текст
        реплик, стиль не трогается. Стилю — меняется именованный стиль, а с
        ним все реплики на нём.
        """
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(16, 12, 16, 8)
        column.setSpacing(6)
        self.target = Segmented(["", ""])
        self.target.changed.connect(self._on_target)
        column.addLayout(hbox(label(tr('Применить к'), "hint"), self.target, spacing=10))
        self.target_hint = label("", "hint")
        self.target_hint.setWordWrap(True)
        column.addWidget(self.target_hint)
        return box

    def _controls(self) -> QWidget:
        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(16, 8, 16, 12)
        column.setSpacing(14)
        column.addWidget(self._presets_group())
        column.addWidget(self._typography_group())
        column.addWidget(self._colors_group())
        column.addWidget(self._placement_group())

        # Строка ``Style:`` — как она уйдёт в файл. Кто правит ASS руками,
        # сверит её глазами; кто не правит — не заметит.
        self.line = QPlainTextEdit()
        self.line.setReadOnly(True)
        self.line.setFixedHeight(52)
        self.line.setToolTip(tr('Строка, которая уйдёт в секцию [V4+ Styles]'))
        self.line.setProperty("role", "styleline")
        column.addWidget(self.line)
        column.addStretch(1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(inner)
        return area

    def _section(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        """Заголовок раздела и его тело. Рамок нет намеренно: во вкладке
        шириной в треть экрана они съедают место, ничего не добавляя."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        column.addWidget(section(title))
        return box, column

    def _presets_group(self) -> QWidget:
        self.presets = PresetCards()
        self.presets.chosen.connect(self._apply_preset)
        return self.presets

    def _typography_group(self) -> QWidget:
        box, column = self._section(tr('Шрифт'))

        self.font_box = FontComboBox()
        self.font_box.setAccessibleName(tr('Гарнитура'))
        # Список шрифтов по умолчанию широк, как самое длинное имя в системе,
        # и растягивал всю вкладку за край колонки.
        self.font_box.setMinimumContentsLength(8)
        self.font_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.font_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.font_box.currentFontChanged.connect(self._on_font_chosen)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 400)
        self.size_spin.setPrefix(tr('Кегль') + "  ")
        self.size_spin.setAccessibleName(tr('Кегль'))
        self.size_spin.setFixedWidth(110)
        self.size_spin.valueChanged.connect(self._on_change)
        column.addLayout(hbox(self.font_box, self.size_spin, spacing=8))

        faces = QFrame()
        faces.setProperty("role", "segment")
        faces.setFixedHeight(30)
        faces_row = QHBoxLayout(faces)
        faces_row.setContentsMargins(2, 2, 2, 2)
        faces_row.setSpacing(2)
        self.bold_check = self._face(tr('Ж'), tr('Полужирный'), "font-weight: 700;")
        self.italic_check = self._face(tr('К'), tr('Курсив'), "font-style: italic;")
        self.underline_check = self._face(tr('Ч'), tr('Подчёркнутый'),
                                          "text-decoration: underline;")
        self.strike_check = self._face(tr('З'), tr('Зачёркнутый'),
                                       "text-decoration: line-through;")
        for button in (self.bold_check, self.italic_check,
                       self.underline_check, self.strike_check):
            faces_row.addWidget(button)

        self.spacing_spin = self._number(-50.0, 50.0, 0.5, 1, tr('Разрядка'))
        self.scale_x_spin = self._number(1.0, 1000.0, 5.0, 0, tr('Масштаб X'))
        self.scale_y_spin = self._number(1.0, 1000.0, 5.0, 0, "Y")
        column.addLayout(hbox(faces, None, spacing=6))
        numbers = QGridLayout()
        numbers.setSpacing(6)
        for index, spin in enumerate((self.spacing_spin, self.scale_x_spin,
                                      self.scale_y_spin)):
            spin.setMinimumWidth(60)
            numbers.addWidget(spin, 0, index)
            numbers.setColumnStretch(index, 1)
        column.addLayout(numbers)
        return box

    def _face(self, text: str, tip: str, css: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.setCheckable(True)
        button.setProperty("role", "seg")
        button.setFixedWidth(30)
        # Буква сама показывает, что делает кнопка: жирная, курсив, черта.
        button.setStyleSheet(f"font-family: Georgia, serif; font-size: 14px; {css}")
        button.toggled.connect(self._on_change)
        return button

    def _number(self, low: float, high: float, step: float, decimals: int,
                title: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setPrefix(title + " ")
        spin.setAccessibleName(title)
        spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        spin.valueChanged.connect(self._on_change)
        return spin

    def _colors_group(self) -> QWidget:
        box, column = self._section(tr('Цвет'))

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.primary_swatch = _Swatch(RGBA(255, 255, 255))
        self.outline_swatch = _Swatch(RGBA(0, 0, 0))
        self.shadow_swatch = _Swatch(RGBA(0, 0, 0, 180))
        self.outline_spin = self._thickness(tr('Толщина обводки'))
        self.shadow_spin = self._thickness(tr('Глубина тени'))
        for index, (title, swatch, extra) in enumerate((
            (tr('Текст'), self.primary_swatch, None),
            (tr('Обводка'), self.outline_swatch, self.outline_spin),
            (tr('Тень'), self.shadow_swatch, self.shadow_spin),
        )):
            grid.addWidget(caption(title), 0, index)
            swatch.changed.connect(self._on_change)
            grid.addWidget(swatch, 1, index)
            if extra is not None:
                grid.addWidget(extra, 2, index)
            grid.setColumnStretch(index, 1)
        column.addLayout(grid)

        self.box_check = QCheckBox(tr('Плашка вместо обводки'))
        self.box_check.setToolTip(
            tr('Непрозрачная подложка под текстом — для очень пёстрого видео')
        )
        self.box_check.toggled.connect(self._on_change)
        column.addWidget(self.box_check)
        return box

    def _thickness(self, title: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 20.0)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setSuffix(" px")
        spin.setToolTip(title)
        spin.setAccessibleName(title)
        spin.valueChanged.connect(self._on_change)
        return spin

    def _placement_group(self) -> QWidget:
        box, column = self._section(tr('Положение'))

        self.pad = AlignmentPad()
        self.pad.changed.connect(self._on_change)

        self.pos_x = self._coordinate("X")
        self.pos_y = self._coordinate("Y")
        self.angle_spin = self._number(-360.0, 360.0, 1.0, 1, tr('Угол'))
        self.angle_spin.setSuffix("°")
        self.margin_l = self._margin_spin(tr('Поле Л'))
        self.margin_r = self._margin_spin(tr('П'))
        self.margin_v = self._margin_spin(tr('В'))
        self.clear_pos_button = kbd_button(tr('Убрать ручное положение'), "Ctrl+Shift+P")
        self.clear_pos_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.clear_pos_button.clicked.connect(lambda: self.position_changed.emit(None))

        numbers = QGridLayout()
        numbers.setSpacing(6)
        for index, widget in enumerate((self.pos_x, self.pos_y, self.angle_spin,
                                        self.margin_l, self.margin_r, self.margin_v)):
            widget.setMinimumWidth(50)
            numbers.addWidget(widget, index // 3, index % 3)
        numbers.addWidget(self.clear_pos_button, 2, 0, 1, 3)
        column.addLayout(hbox(self.pad, numbers, spacing=14))
        return box

    def _coordinate(self, title: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(-10000, 10000)
        spin.setPrefix(title + " ")
        spin.setAccessibleName(tr('Положение {0}').format(title))
        spin.setButtonSymbols(QSpinBox.NoButtons)
        spin.setToolTip(tr('Ручное положение реплики — тег \\pos'))
        spin.editingFinished.connect(self._on_position)
        return spin

    def _margin_spin(self, title: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 2000)
        spin.setSingleStep(5)
        spin.setPrefix(title + " ")
        spin.setAccessibleName(title)
        spin.setButtonSymbols(QSpinBox.NoButtons)
        spin.valueChanged.connect(self._on_change)
        return spin

    def _preview_side(self) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(16, 0, 16, 8)
        column.setSpacing(6)

        self.preview = _Preview(self._palette)
        column.addWidget(self.preview, 1)

        # Фон под кадром — сегментами под ним: белую обводку не видно на
        # светлом, чёрную — на тёмном, и проверять надо на всех.
        self.background = Segmented([title for _key, title in PreviewBackground.TITLES])
        self.background.setToolTip(tr('Фон предпросмотра'))
        keys = [key for key, _title in PreviewBackground.TITLES]
        self.background.set_index(keys.index(PreviewBackground.GRADIENT))
        self.background.changed.connect(lambda i: self.preview.set_background(keys[i]))
        column.addWidget(self.background)
        return box

    # -- обмен значениями ---------------------------------------------------------- #

    def style(self) -> SubtitleStyle:
        """Стиль, собранный из полей окна."""
        return replace(
            self._style,
            # Имя шрифта берём из поля только когда его выбрали здесь.
            # Список показывает лишь установленные шрифты и молча подменяет
            # отсутствующий на похожий — а файл мог прийти с другой машины,
            # и переписывать в нём «Arial» на «Sans Serif» только потому,
            # что вкладку открыли, недопустимо.
            fontname=(
                self.font_box.currentFont().family() if self._font_chosen
                else self._style.fontname
            ),
            fontsize=float(self.size_spin.value()),
            primary=self.primary_swatch.colour(),
            outline_color=self.outline_swatch.colour(),
            back_color=self.shadow_swatch.colour(),
            bold=self.bold_check.isChecked(),
            italic=self.italic_check.isChecked(),
            underline=self.underline_check.isChecked(),
            strikeout=self.strike_check.isChecked(),
            spacing=self.spacing_spin.value(),
            scale_x=self.scale_x_spin.value(),
            scale_y=self.scale_y_spin.value(),
            angle=self.angle_spin.value(),
            outline=self.outline_spin.value(),
            shadow=self.shadow_spin.value(),
            border_style=(
                BorderStyle.OPAQUE_BOX if self.box_check.isChecked()
                else BorderStyle.OUTLINE
            ),
            alignment=self.pad.alignment(),
            margin_l=self.margin_l.value(),
            margin_r=self.margin_r.value(),
            margin_v=self.margin_v.value(),
        )

    def set_style(self, style: SubtitleStyle) -> None:
        """Заполняет поля из стиля, не рассылая сигналов об изменении."""
        self._syncing = True
        self._font_chosen = False
        try:
            self._style = replace(style)
            self.font_box.set_family(style.fontname)
            self.size_spin.setValue(round(style.fontsize))
            self.primary_swatch.set_colour(style.primary)
            self.outline_swatch.set_colour(style.outline_color)
            self.shadow_swatch.set_colour(style.back_color)
            self.bold_check.setChecked(style.bold)
            self.italic_check.setChecked(style.italic)
            self.underline_check.setChecked(style.underline)
            self.strike_check.setChecked(style.strikeout)
            self.spacing_spin.setValue(style.spacing)
            self.scale_x_spin.setValue(style.scale_x)
            self.scale_y_spin.setValue(style.scale_y)
            self.angle_spin.setValue(style.angle)
            self.outline_spin.setValue(style.outline)
            self.shadow_spin.setValue(style.shadow)
            self.box_check.setChecked(style.border_style == BorderStyle.OPAQUE_BOX)
            self.pad.set_alignment(style.alignment)
            self.margin_l.setValue(style.margin_l)
            self.margin_r.setValue(style.margin_r)
            self.margin_v.setValue(style.margin_v)
        finally:
            self._syncing = False
        self._refresh()

    def set_target(self, selected: int, style_name: str) -> None:
        """Сколько выделено и как зовётся стиль — подписи «Применить к».

        Положение переключателя следует за выделением: выделили реплики —
        правим их, сняли выделение — правим стиль. Так было и до
        переключателя, и переучивать никого не нужно.
        """
        self._selected = selected
        self.target.set_text(0, tr('Выделенным · {0}').format(selected))
        self.target.set_text(1, tr('Стилю {0}').format(style_name or "Default"))
        self.target.button(0).setEnabled(selected > 0)
        self.target.set_index(0 if selected else 1)
        self._show_target()

    def applies_to_style(self) -> bool:
        """Куда пойдёт правка: ``True`` — в стиль, ``False`` — выделенным."""
        return self.target.index() != 0

    def set_position(self, position: tuple[float, float] | None) -> None:
        """Ручное положение выделенной реплики, если оно задано."""
        for spin, value in ((self.pos_x, position[0] if position else 0),
                            (self.pos_y, position[1] if position else 0)):
            spin.blockSignals(True)
            spin.setValue(int(value))
            spin.blockSignals(False)
        self.clear_pos_button.setEnabled(position is not None and not self.applies_to_style())

    def _show_target(self) -> None:
        to_style = self.applies_to_style()
        self.target_hint.setText(
            tr('Меняется стиль — а с ним все реплики на нём') if to_style
            else tr('Правки пишутся тегами в текст реплики, стиль не меняется')
        )
        # Ручное положение бывает только у реплики, у стиля его нет.
        for widget in (self.pos_x, self.pos_y):
            widget.setEnabled(not to_style)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.preview.set_palette(palette)

    def set_preview_text(self, text: str) -> None:
        """Показывать в кадре настоящую реплику, а не образец.

        Оформление проверяют на своём тексте: у него другая длина, другие
        буквы и другое число строк, и образец про это ничего не скажет.
        """
        self.preview.set_text(text)

    # -- реакции ------------------------------------------------------------------- #

    def _on_target(self, _index: int) -> None:
        self._show_target()
        self.target_changed.emit(self.applies_to_style())

    def _on_position(self) -> None:
        if not self._syncing and not self.applies_to_style():
            self.position_changed.emit((self.pos_x.value(), self.pos_y.value()))

    def _on_change(self, *_args) -> None:
        if self._syncing:
            return
        self._refresh()
        self.style_changed.emit(self.style())

    def _on_font_chosen(self, *_args) -> None:
        """Шрифт выбрали руками — с этого мгновения он и уходит в стиль."""
        if self._syncing:
            return
        self._font_chosen = True
        self._on_change()

    def _apply_preset(self, name: str) -> None:
        from sfstudio.services.style_presets import BUILTIN_PRESETS

        preset = next((p for p in BUILTIN_PRESETS if p.name == name), None)
        if preset is None:
            return
        # Имя стиля сохраняем: набор задаёт оформление, а не переименовывает
        # стиль, на который уже ссылаются реплики.
        self.set_style(preset.apply_to(self._style))
        self.style_changed.emit(self.style())

    def _refresh(self) -> None:
        style = self.style()
        self.preview.set_style(style)
        self.line.setPlainText(format_style_line(style))

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Узко — кадр над настройками, широко — рядом.

        Порог в 900 пикселей: при меньшей ширине два столбца дают колонку
        настроек в 300 пикселей, где подписи начинают переноситься по слогам.
        """
        super().resizeEvent(event)
        wanted = Qt.Horizontal if self.width() >= 900 else Qt.Vertical
        if self._splitter.orientation() != wanted:
            self._splitter.setOrientation(wanted)
            self._share_space()
        elif not self._space_shared:
            self._share_space()

    def _share_space(self) -> None:
        """Делит место между кадром и настройками.

        Коэффициенты растяжения тут не работают — они управляют только тем,
        как расходится **добавленное** место, а первичную раскладку Qt берёт
        из подсказок размера. Узко — кадру полоса в полторы сотни пикселей,
        как в макете; широко — две пятых ширины.
        """
        vertical = self._splitter.orientation() == Qt.Vertical
        total = self._splitter.height() if vertical else self._splitter.width()
        if total <= 0:
            return
        first = min(170, total // 3) if vertical else total * 2 // 5
        self._splitter.setSizes([first, total - first])
        self._space_shared = True
