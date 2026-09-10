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
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
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
from sfstudio.ui.theme import DARK, Palette

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
    CARD_W = 168

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
            button.setMinimumHeight(30)
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
        self.setMinimumHeight(200)
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
        return QSize(480, 270)

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

        for layer in layers:
            if layer.w <= 0 or layer.h <= 0:
                continue
            r, g, b, a = layer.rgba
            if a == 0:
                continue
            mask = QImage(
                layer.data, layer.w, layer.h, layer.stride, QImage.Format_Alpha8
            )
            tinted = QImage(layer.w, layer.h, QImage.Format_ARGB32_Premultiplied)
            tinted.fill(0)
            inner = QPainter(tinted)
            inner.drawImage(0, 0, mask)
            inner.setCompositionMode(QPainter.CompositionMode_SourceIn)
            inner.fillRect(tinted.rect(), QColor(r, g, b, a))
            inner.end()
            painter.drawImage(layer.x, layer.y, tinted)
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
    """Настройка стиля с живым кадром."""

    #: Стиль изменился — вызывающий решает, куда его применить.
    style_changed = Signal(object)
    #: Нажали «Применить»: стиль и признак «ко всем, а не к выделенным».
    apply_requested = Signal(object, bool)

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
        self._style = replace(style) if style is not None else SubtitleStyle()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())

        # Сплиттер, а не жёсткая раскладка: во вкладке инспектора кадр нужен
        # сверху, в отдельном окне — сбоку, и границу между ними человек
        # двигает сам.
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.addWidget(self._controls())
        self._splitter.addWidget(self._preview_side())
        # Настройкам места больше: их много и они мелкие, а кадр читается и
        # в половинном размере. Поровну делить нельзя — в узкой колонке
        # половина настроек уходила под прокрутку сразу при открытии.
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        root.addWidget(self._splitter, 1)

        self._syncing = False
        self.set_style(self._style)

    # -- построение --------------------------------------------------------------- #

    def _header(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(8, 6, 8, 6)

        title = QLabel(tr('Оформление'))
        font = QFont(title.font())
        font.setBold(True)
        title.setFont(font)
        row.addWidget(title)
        row.addStretch(1)

        self.apply_selected_button = QPushButton(tr('К выделенным'))
        self.apply_selected_button.setToolTip(
            tr('Присвоить этот стиль выделенным репликам')
        )
        self.apply_selected_button.clicked.connect(
            lambda: self.apply_requested.emit(self.style(), False)
        )
        row.addWidget(self.apply_selected_button)

        self.apply_all_button = QPushButton(tr('Ко всем репликам'))
        self.apply_all_button.setToolTip(tr('Переписать стиль всего документа'))
        self.apply_all_button.clicked.connect(
            lambda: self.apply_requested.emit(self.style(), True)
        )
        row.addWidget(self.apply_all_button)
        return box

    def _controls(self) -> QWidget:
        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(8, 4, 8, 8)
        column.setSpacing(10)
        column.addWidget(self._presets_group())
        column.addWidget(self._typography_group())
        column.addWidget(self._colors_group())
        column.addWidget(self._placement_group())
        column.addStretch(1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setWidget(inner)
        return area

    def _section(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        """Заголовок раздела и его тело. Рамок нет намеренно: во вкладке
        шириной в треть экрана они съедают место, ничего не добавляя."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        label = QLabel(title.upper())
        label.setProperty("role", "hint")
        font = QFont(label.font())
        font.setBold(True)
        label.setFont(font)
        column.addWidget(label)
        return box, column

    def _presets_group(self) -> QWidget:
        box, column = self._section(tr('Готовый набор'))
        self.presets = PresetCards()
        self.presets.chosen.connect(self._apply_preset)
        column.addWidget(self.presets)
        return box

    def _typography_group(self) -> QWidget:
        box, column = self._section(tr('Шрифт и начертание'))

        self.font_box = FontComboBox()
        self.font_box.currentFontChanged.connect(self._on_font_chosen)
        column.addWidget(self.font_box)

        size_row = QHBoxLayout()
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(8, 200)
        self.size_slider.valueChanged.connect(self._on_size_slider)
        size_row.addWidget(self.size_slider, 1)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 200)
        self.size_spin.setSuffix(" px")
        # Ширину задаём явно: в колонке шириной в треть экрана поле иначе
        # растягивается и уезжает за край вместе со стрелками.
        self.size_spin.setFixedWidth(86)
        self.size_spin.valueChanged.connect(self._on_size_spin)
        size_row.addWidget(self.size_spin)
        column.addLayout(size_row)

        face_row = QHBoxLayout()
        self.bold_check = QToolButton()
        self.bold_check.setText(tr('Ж'))
        self.italic_check = QToolButton()
        self.italic_check.setText(tr('К'))
        self.underline_check = QToolButton()
        self.underline_check.setText(tr('Ч'))
        for button, tip in (
            (self.bold_check, tr('Полужирный')),
            (self.italic_check, tr('Курсив')),
            (self.underline_check, tr('Подчёркнутый')),
        ):
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setMinimumWidth(38)
            button.toggled.connect(self._on_change)
            face_row.addWidget(button)
        face_row.addStretch(1)
        column.addLayout(face_row)
        return box

    def _colors_group(self) -> QWidget:
        box, column = self._section(tr('Цвет, обводка и тень'))

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.primary_swatch = _Swatch(RGBA(255, 255, 255))
        self.outline_swatch = _Swatch(RGBA(0, 0, 0))
        self.shadow_swatch = _Swatch(RGBA(0, 0, 0, 180))
        for column_index, (title, swatch) in enumerate((
            (tr('Основной'), self.primary_swatch),
            (tr('Обводка'), self.outline_swatch),
            (tr('Тень'), self.shadow_swatch),
        )):
            caption = QLabel(title)
            caption.setProperty("role", "hint")
            grid.addWidget(caption, 0, column_index)
            swatch.changed.connect(self._on_change)
            grid.addWidget(swatch, 1, column_index)
        column.addLayout(grid)

        self.outline_spin = self._thickness_row(
            column, tr('Толщина обводки'), 0.0, 20.0, 0.5
        )
        self.shadow_spin = self._thickness_row(column, tr('Глубина тени'), 0.0, 20.0, 0.5)

        self.box_check = QCheckBox(tr('Плашка вместо обводки'))
        self.box_check.setToolTip(
            tr('Непрозрачная подложка под текстом — для очень пёстрого видео')
        )
        self.box_check.toggled.connect(self._on_change)
        column.addWidget(self.box_check)
        return box

    def _thickness_row(
        self, column: QVBoxLayout, title: str, low: float, high: float, step: float
    ) -> QDoubleSpinBox:
        row = QHBoxLayout()
        caption = QLabel(title)
        caption.setProperty("role", "hint")
        caption.setMinimumWidth(120)
        row.addWidget(caption)

        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setDecimals(1)
        spin.setSuffix(" px")
        spin.valueChanged.connect(self._on_change)
        row.addWidget(spin)
        row.addStretch(1)
        column.addLayout(row)
        return spin

    def _placement_group(self) -> QWidget:
        box, column = self._section(tr('Положение в кадре'))

        row = QHBoxLayout()
        self.pad = AlignmentPad()
        self.pad.changed.connect(self._on_change)
        row.addWidget(self.pad)

        margins = QGridLayout()
        margins.setHorizontalSpacing(6)
        self.margin_l = self._margin_spin()
        self.margin_r = self._margin_spin()
        self.margin_v = self._margin_spin()
        for index, (title, spin) in enumerate((
            (tr('Слева'), self.margin_l),
            (tr('Справа'), self.margin_r),
            (tr('По верт.'), self.margin_v),
        )):
            caption = QLabel(title)
            caption.setProperty("role", "hint")
            margins.addWidget(caption, 0, index)
            margins.addWidget(spin, 1, index)
        row.addLayout(margins, 1)
        column.addLayout(row)
        return box

    def _margin_spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 2000)
        spin.setSingleStep(5)
        spin.setMaximumWidth(96)
        spin.valueChanged.connect(self._on_change)
        return spin

    def _preview_side(self) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(8, 4, 8, 8)
        column.setSpacing(6)

        head = QHBoxLayout()
        caption = QLabel(tr('ПРЕДПРОСМОТР'))
        caption.setProperty("role", "hint")
        font = QFont(caption.font())
        font.setBold(True)
        caption.setFont(font)
        head.addWidget(caption)
        head.addStretch(1)

        self.background_group = QButtonGroup(self)
        self.background_group.setExclusive(True)
        for key, title in PreviewBackground.TITLES:
            button = QToolButton()
            button.setText(title)
            button.setCheckable(True)
            button.setChecked(key == PreviewBackground.GRADIENT)
            button.clicked.connect(lambda _c, k=key: self.preview.set_background(k))
            self.background_group.addButton(button)
            head.addWidget(button)
        column.addLayout(head)

        self.preview = _Preview(self._palette)
        column.addWidget(self.preview, 1)

        self.line = QPlainTextEdit()
        self.line.setReadOnly(True)
        self.line.setFixedHeight(58)
        self.line.setToolTip(tr('Строка, которая уйдёт в секцию [V4+ Styles]'))
        font = QFont("Consolas")
        font.setPixelSize(11)
        self.line.setFont(font)
        column.addWidget(self.line)
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
            self.size_slider.setValue(round(style.fontsize))
            self.primary_swatch.set_colour(style.primary)
            self.outline_swatch.set_colour(style.outline_color)
            self.shadow_swatch.set_colour(style.back_color)
            self.bold_check.setChecked(style.bold)
            self.italic_check.setChecked(style.italic)
            self.underline_check.setChecked(style.underline)
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

    def _on_size_slider(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.size_spin.setValue(value)
        self._syncing = False
        self._on_change()

    def _on_size_spin(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.size_slider.setValue(value)
        self._syncing = False
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
        """Узко — кадр под настройками, широко — рядом.

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
        """Делит место между настройками и кадром: три к двум.

        Коэффициенты растяжения тут не работают — они управляют только тем,
        как расходится **добавленное** место, а первичную раскладку Qt берёт
        из подсказок размера. Кадр просит двести пикселей минимум и в узкой
        колонке забирал бы себе половину, оставляя настройки под прокруткой.
        """
        total = (
            self._splitter.width() if self._splitter.orientation() == Qt.Horizontal
            else self._splitter.height()
        )
        if total <= 0:
            return
        self._splitter.setSizes([total * 3 // 5, total * 2 // 5])
        self._space_shared = True
