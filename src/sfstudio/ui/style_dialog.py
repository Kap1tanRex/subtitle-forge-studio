"""Диалог шаблонов оформления с живым превью.

Превью рисуется настоящей libass на текущем кадре документа, а не абстрактной
картинкой: стиль выглядит ровно так, как будет выглядеть в работе, вместе с
обводкой, тенью и переносом строк. Проверять оформление на белом прямоугольнике
бессмысленно — субтитры живут поверх видео.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.core.color import RGBA
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.style import ALIGNMENT_NAMES, SubtitleStyle
from sfstudio.services.style_presets import PresetLibrary, StylePreset
from sfstudio.ui.theme import DARK, Palette

__all__ = ["StylePresetDialog"]

PREVIEW_TEXT = "Пример текста субтитра\\NВторая строка"


class PreviewStrip(QWidget):
    """Кадр с текстом, отрисованным настоящей libass."""

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._style = SubtitleStyle()
        self._renderer = None
        self._doc = SubtitleDocument.blank((1920, 1080))
        self._doc.create_event(0, 10_000, PREVIEW_TEXT)
        self.setMinimumHeight(190)
        self._init_renderer()

    def _init_renderer(self) -> None:
        try:
            from sfstudio.render.renderer import LibassRenderer

            self._renderer = LibassRenderer()
        except (OSError, RuntimeError):
            self._renderer = None  # без libass покажем текстовую заглушку

    def set_style(self, style: SubtitleStyle) -> None:
        self._style = replace(style, name="Default")
        self._doc.styles["Default"] = self._style
        self._doc.bump_revision()
        if self._renderer is not None:
            self._renderer.invalidate()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(420, 210)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = self.rect()

        # Шахматка: на однотонном фоне не видно, как работает обводка.
        painter.fillRect(rect, QColor(self._palette.canvas_a))
        dark = QColor(self._palette.canvas_b)
        cell = 18
        for row, y in enumerate(range(rect.top(), rect.bottom(), cell)):
            for x in range(rect.left() + (cell if row % 2 else 0), rect.right(), cell * 2):
                painter.fillRect(x, y, cell, cell, dark)

        if self._renderer is None:
            painter.setPen(QColor(self._palette.text_muted))
            painter.drawText(rect, Qt.AlignCenter, tr('libass недоступна — превью нет'))
            painter.end()
            return

        width, height = max(1, rect.width()), max(1, rect.height())
        self._renderer.set_frame_size(width, height, self._doc.script_info.play_res)
        try:
            layers = self._renderer.render(self._doc, 1000)
        except Exception:
            painter.end()
            return

        for layer in layers:
            if layer.w <= 0 or layer.h <= 0:
                continue
            r, g, b, a = layer.rgba
            if a == 0:
                continue
            mask = QImage(layer.data, layer.w, layer.h, layer.stride, QImage.Format_Alpha8)
            tinted = QImage(layer.w, layer.h, QImage.Format_ARGB32_Premultiplied)
            tinted.fill(0)
            inner = QPainter(tinted)
            inner.drawImage(0, 0, mask)
            inner.setCompositionMode(QPainter.CompositionMode_SourceIn)
            inner.fillRect(tinted.rect(), QColor(r, g, b, a))
            inner.end()
            painter.drawImage(layer.x, layer.y, tinted)
        painter.end()


class _ColourButton(QPushButton):
    """Кнопка выбора цвета с образцом прямо на ней."""

    changed = Signal()

    def __init__(self, colour: RGBA, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = colour
        self.setFixedWidth(84)
        self.clicked.connect(self._pick)
        self._sync()

    def _sync(self) -> None:
        c = self._colour
        self.setText(f"{c.a * 100 // 255}%")
        self.setStyleSheet(
            f"background: rgb({c.r},{c.g},{c.b}); "
            f"color: {'#000' if (c.r + c.g + c.b) > 380 else '#fff'};"
        )

    def _pick(self) -> None:
        current = QColor(self._colour.r, self._colour.g, self._colour.b, self._colour.a)
        chosen = QColorDialog.getColor(
            current, self, tr('Выбор цвета'), QColorDialog.ShowAlphaChannel
        )
        if chosen.isValid():
            self._colour = RGBA(chosen.red(), chosen.green(), chosen.blue(), chosen.alpha())
            self._sync()
            self.changed.emit()

    def colour(self) -> RGBA:
        return self._colour

    def set_colour(self, value: RGBA) -> None:
        self._colour = value
        self._sync()


class StylePresetDialog(QDialog):
    """Библиотека шаблонов: выбор, правка, сохранение, применение."""

    def __init__(
        self,
        library: PresetLibrary,
        current: SubtitleStyle | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Шаблоны оформления'))
        self.resize(940, 560)
        self._library = library
        self._result: SubtitleStyle | None = None
        self._loading = False

        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        # Слева — список шаблонов
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        left_layout.addWidget(self.list, 1)

        buttons_row = QHBoxLayout()
        self.save_button = QPushButton(tr('Сохранить как…'))
        self.save_button.clicked.connect(self._save_as)
        self.delete_button = QPushButton(tr('Удалить'))
        self.delete_button.clicked.connect(self._delete)
        buttons_row.addWidget(self.save_button)
        buttons_row.addWidget(self.delete_button)
        left_layout.addLayout(buttons_row)
        splitter.addWidget(left)

        # Справа — параметры и превью
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.preview = PreviewStrip()
        right_layout.addWidget(self.preview)

        form = QFormLayout()
        self.font_box = QFontComboBox()
        self.font_box.currentFontChanged.connect(self._on_change)
        form.addRow(tr('Шрифт:'), self.font_box)

        self.size_box = QDoubleSpinBox()
        self.size_box.setRange(4, 400)
        self.size_box.setDecimals(0)
        self.size_box.valueChanged.connect(self._on_change)
        form.addRow(tr('Размер:'), self.size_box)

        colours = QHBoxLayout()
        self.primary_button = _ColourButton(RGBA(255, 255, 255))
        self.outline_button = _ColourButton(RGBA(0, 0, 0))
        self.back_button = _ColourButton(RGBA(0, 0, 0, 128))
        for label, button in (
            (tr('Текст'), self.primary_button),
            (tr('Обводка'), self.outline_button),
            (tr('Плашка'), self.back_button),
        ):
            colours.addWidget(QLabel(label))
            colours.addWidget(button)
            button.changed.connect(self._on_change)
        colours.addStretch(1)
        form.addRow(tr('Цвета:'), _wrap(colours))

        flags = QHBoxLayout()
        self.bold_box = QCheckBox(tr('Жирный'))
        self.italic_box = QCheckBox(tr('Курсив'))
        self.underline_box = QCheckBox(tr('Подчёркнутый'))
        for box in (self.bold_box, self.italic_box, self.underline_box):
            box.stateChanged.connect(self._on_change)
            flags.addWidget(box)
        flags.addStretch(1)
        form.addRow(tr('Начертание:'), _wrap(flags))

        edges = QHBoxLayout()
        self.outline_size = QDoubleSpinBox()
        self.outline_size.setRange(0, 40)
        self.outline_size.setDecimals(1)
        self.outline_size.valueChanged.connect(self._on_change)
        self.shadow_size = QDoubleSpinBox()
        self.shadow_size.setRange(0, 40)
        self.shadow_size.setDecimals(1)
        self.shadow_size.valueChanged.connect(self._on_change)
        self.border_box = QComboBox()
        self.border_box.addItem(tr('Обводка и тень'), 1)
        self.border_box.addItem(tr('Непрозрачная плашка'), 3)
        self.border_box.currentIndexChanged.connect(self._on_change)
        edges.addWidget(QLabel(tr('Обводка')))
        edges.addWidget(self.outline_size)
        edges.addWidget(QLabel(tr('Тень')))
        edges.addWidget(self.shadow_size)
        edges.addWidget(self.border_box, 1)
        form.addRow(tr('Контур:'), _wrap(edges))

        self.alignment_box = QComboBox()
        for value in range(1, 10):
            self.alignment_box.addItem(f"{value} — {ALIGNMENT_NAMES[value]}", value)
        self.alignment_box.currentIndexChanged.connect(self._on_change)
        form.addRow(tr('Выравнивание:'), self.alignment_box)

        margins = QHBoxLayout()
        self.margin_l = _margin_spin(self._on_change)
        self.margin_r = _margin_spin(self._on_change)
        self.margin_v = _margin_spin(self._on_change)
        for label, spin in ((tr('слева'), self.margin_l), (tr('справа'), self.margin_r),
                            (tr('по вертикали'), self.margin_v)):
            margins.addWidget(QLabel(label))
            margins.addWidget(spin)
        margins.addStretch(1)
        form.addRow(tr('Поля:'), _wrap(margins))

        right_layout.addLayout(form)
        self.description = QLabel("")
        self.description.setProperty("role", "hint")
        self.description.setWordWrap(True)
        right_layout.addWidget(self.description)
        right_layout.addStretch(1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        box = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close, parent=self)
        box.button(QDialogButtonBox.Apply).setText(tr('Применить к выделению'))
        box.button(QDialogButtonBox.Close).setText(tr('Закрыть'))
        box.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._reload_list()
        if current is not None:
            self._load_style(current)
            self.description.setText(tr('Текущий стиль документа'))
        elif self.list.count():
            self.list.setCurrentRow(0)

    # -- список ------------------------------------------------------------------- #

    def _reload_list(self, select: str | None = None) -> None:
        self.list.clear()
        for preset in self._library.all():
            item = QListWidgetItem(
                f"{preset.name}{'' if not preset.builtin else '  ·  встроенный'}"
            )
            item.setData(Qt.UserRole, preset.name)
            self.list.addItem(item)
            if preset.name == select:
                self.list.setCurrentItem(item)

    def _on_select(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        preset = self._library.get(str(item.data(Qt.UserRole)))
        if preset is None:
            return
        self._load_style(preset.style)
        self.description.setText(preset.description or tr('Без описания'))
        self.delete_button.setEnabled(not preset.builtin)

    # -- форма -------------------------------------------------------------------- #

    def _load_style(self, style: SubtitleStyle) -> None:
        """Заполняет форму. Флаг ``_loading`` глушит сигналы полей.

        Без него каждое поле дёргало бы пересчёт превью, и загрузка одного
        шаблона перерисовывала бы кадр полтора десятка раз.
        """
        self._loading = True
        try:
            self.font_box.setCurrentText(style.fontname)
            self.size_box.setValue(style.fontsize)
            self.primary_button.set_colour(style.primary)
            self.outline_button.set_colour(style.outline_color)
            self.back_button.set_colour(style.back_color)
            self.bold_box.setChecked(style.bold)
            self.italic_box.setChecked(style.italic)
            self.underline_box.setChecked(style.underline)
            self.outline_size.setValue(style.outline)
            self.shadow_size.setValue(style.shadow)
            self.border_box.setCurrentIndex(0 if style.border_style != 3 else 1)
            self.alignment_box.setCurrentIndex(max(0, style.alignment - 1))
            self.margin_l.setValue(style.margin_l)
            self.margin_r.setValue(style.margin_r)
            self.margin_v.setValue(style.margin_v)
        finally:
            self._loading = False
        self._on_change()

    def current_style(self) -> SubtitleStyle:
        """Стиль, собранный из полей формы."""
        return SubtitleStyle(
            name="Default",
            fontname=self.font_box.currentFont().family(),
            fontsize=self.size_box.value(),
            primary=self.primary_button.colour(),
            outline_color=self.outline_button.colour(),
            back_color=self.back_button.colour(),
            bold=self.bold_box.isChecked(),
            italic=self.italic_box.isChecked(),
            underline=self.underline_box.isChecked(),
            border_style=int(self.border_box.currentData()),
            outline=self.outline_size.value(),
            shadow=self.shadow_size.value(),
            alignment=int(self.alignment_box.currentData()),
            margin_l=self.margin_l.value(),
            margin_r=self.margin_r.value(),
            margin_v=self.margin_v.value(),
        )

    def _on_change(self, *_args) -> None:
        if self._loading:
            return
        self.preview.set_style(self.current_style())

    # -- действия ------------------------------------------------------------------ #

    def _save_as(self) -> None:
        suggested = ""
        item = self.list.currentItem()
        if item is not None:
            name = str(item.data(Qt.UserRole))
            suggested = name if not self._library.is_builtin(name) else tr('{0} (мой)').format(name)

        name, ok = QInputDialog.getText(
            self, tr('Сохранить шаблон'), tr('Название шаблона:'), QLineEdit.Normal, suggested
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        if self._library.get(name) is not None and not self._library.is_builtin(name):
            answer = QMessageBox.question(
                self, tr('Заменить шаблон'),
                tr('Шаблон «{0}» уже есть. Заменить?').format(name),
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        preset = StylePreset(name=name, style=self.current_style())
        try:
            self._library.save(preset)
        except Exception as exc:
            QMessageBox.critical(self, tr('Не удалось сохранить'), str(exc))
            return
        self._reload_list(select=name)

    def _delete(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        name = str(item.data(Qt.UserRole))
        if self._library.is_builtin(name):
            QMessageBox.information(
                self, tr('Встроенный шаблон'),
                tr('Встроенные шаблоны удалить нельзя — они вернутся при следующем запуске. '
                       'Сохраните свой вариант под тем же именем, и он перекроет встроенный.'),
            )
            return
        if QMessageBox.question(
            self, tr('Удалить шаблон'), tr('Удалить «{0}»?').format(name),
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self._library.delete(name)
            self._reload_list()

    def _apply(self) -> None:
        self._result = self.current_style()
        self.accept()

    def chosen_style(self) -> SubtitleStyle | None:
        """Стиль, который пользователь попросил применить."""
        return self._result


def _wrap(layout) -> QWidget:
    holder = QWidget()
    holder.setLayout(layout)
    return holder


def _margin_spin(slot) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(0, 2000)
    spin.setSingleStep(5)
    spin.valueChanged.connect(slot)
    return spin
