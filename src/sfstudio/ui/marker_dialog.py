"""Окно маркера: время, имя, примечание, ключевое слово, цвет.

Устроено так же, как в монтажных программах, и намеренно: маркеры ставят
люди, пришедшие из Resolve или Premiere, и переучивать их ради оригинальности
незачем.

Три решения, которые стоит объяснить.

**Окно открывается сразу с именем в фокусе.** Маркер ставят одним движением и
тут же подписывают; лишний щелчок по полю на каждой отметке за смену
превращается в сотню лишних щелчков.

**«Готово» вместо «ОК» и «Отмена».** Маркер уже стоит на таймлайне — окно не
создаёт его, а описывает. Кнопка «Отмена» обещала бы, что отметка исчезнет, а
исчезает она по «Удалить маркер»: это разные действия, и путать их нельзя.

**Длительность в секундах, а не в кадрах.** В кадрах её показывают монтажные
программы, но у нас частота кадров — свойство проекта, которое может
смениться, а время маркера от этого меняться не должно.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.markers import (
    MARKER_COLORS,
    Marker,
    color_title,
)
from sfstudio.core.time import format_ass, parse_timecode

__all__ = ["ColorRow", "MarkerDialog"]

#: Сторона цветного кружка в ряду выбора.
SWATCH_PX = 16


def _swatch(value: str, *, ring: bool) -> QIcon:
    """Кружок цвета. ``ring`` обводит выбранный — цветом его не отличить.

    Полагаться на одну лишь заливку нельзя: у выбранного и невыбранного
    кружка она одинаковая, а системная подсветка нажатой кнопки на тёмной
    теме почти не видна.
    """
    size = SWATCH_PX + 6
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QColor(value))
    painter.setPen(Qt.NoPen)
    inset = 3
    painter.drawEllipse(inset, inset, SWATCH_PX, SWATCH_PX)
    if ring:
        pen = painter.pen()
        pen.setColor(QColor("#FFFFFF"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(1, 1, size - 3, size - 3)
    painter.end()
    return QIcon(pixmap)


class ColorRow(QWidget):
    """Ряд цветных кружков. Выбран ровно один."""

    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QToolButton] = {}
        self._current = MARKER_COLORS[0][0]

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        for key, title, value in MARKER_COLORS:
            button = QToolButton()
            button.setToolTip(title)
            button.setAutoRaise(True)
            button.setCheckable(True)
            button.setIconSize(QSize(SWATCH_PX + 6, SWATCH_PX + 6))
            button.setIcon(_swatch(value, ring=False))
            button.clicked.connect(lambda _checked, k=key: self.set_color(k))
            self._buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        self._refresh()

    def color(self) -> str:
        return self._current

    def set_color(self, key: str) -> None:
        if key not in self._buttons:
            return
        changed = key != self._current
        self._current = key
        self._refresh()
        if changed:
            self.changed.emit(key)

    def _refresh(self) -> None:
        for key, _title, value in MARKER_COLORS:
            button = self._buttons[key]
            chosen = key == self._current
            button.setChecked(chosen)
            button.setIcon(_swatch(value, ring=chosen))


class MarkerDialog(QDialog):
    """Правка одного маркера."""

    #: Нажали «Удалить маркер».
    delete_requested = Signal()

    def __init__(
        self,
        marker: Marker,
        parent: QWidget | None = None,
        *,
        keywords: list[str] | None = None,
        is_new: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Маркеры")
        self._marker = marker

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        time_row = QHBoxLayout()
        self.time_edit = QLineEdit(format_ass(marker.time))
        self.time_edit.setToolTip("Ч:ММ:СС.сс — как в таймкоде реплик")
        time_row.addWidget(self.time_edit, 1)

        time_row.addWidget(QLabel("Длительность"))
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.0, 24 * 60 * 60)
        self.duration_spin.setDecimals(2)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setSuffix(" с")
        self.duration_spin.setSpecialValueText("точка")
        self.duration_spin.setValue(marker.duration / 1000)
        self.duration_spin.setToolTip(
            "Ноль — отметка в одной точке. Больше нуля — отмеченный отрезок"
        )
        time_row.addWidget(self.duration_spin)
        form.addRow("Время", _wrap(time_row))

        self.name_edit = QLineEdit(marker.name)
        self.name_edit.setPlaceholderText("Имя маркера")
        form.addRow("Имя", self.name_edit)

        self.note_edit = QPlainTextEdit(marker.note)
        self.note_edit.setPlaceholderText("Для примечаний…")
        self.note_edit.setFixedHeight(80)
        form.addRow("Примечания", self.note_edit)

        self.keyword_edit = QLineEdit(marker.keyword)
        self.keyword_edit.setPlaceholderText("Одно слово для поиска")
        if keywords:
            from PySide6.QtWidgets import QCompleter

            completer = QCompleter(keywords, self.keyword_edit)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            self.keyword_edit.setCompleter(completer)
        form.addRow("Ключевое слово", self.keyword_edit)

        self.colors = ColorRow()
        self.colors.set_color(marker.color)
        form.addRow("Цвет", self.colors)
        root.addLayout(form)

        # Кнопки разложены вручную: «Удалить» слева, «Готово» справа — так же,
        # как в монтажных программах, откуда человек сюда и пришёл. Роли
        # QDialogButtonBox расставили бы их наоборот, а Enter отдали бы
        # удалению.
        row = QHBoxLayout()
        self.delete_button = QPushButton("Удалить маркер")
        self.delete_button.clicked.connect(self._delete)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        self.done_button = QPushButton("Готово")
        self.done_button.setDefault(True)
        self.done_button.clicked.connect(self.accept)
        row.addWidget(self.done_button)
        root.addLayout(row)

        self.setMinimumWidth(440)
        # Имя — первое, что дописывают к только что поставленной отметке.
        self.name_edit.setFocus()
        if is_new:
            self.name_edit.selectAll()

    def _delete(self) -> None:
        self.delete_requested.emit()
        self.reject()

    def marker(self) -> Marker:
        """Маркер с тем, что ввели. Испорченное время остаётся прежним."""
        time = parse_timecode(self.time_edit.text())
        return Marker(
            time=self._marker.time if time is None else max(0, time),
            duration=round(self.duration_spin.value() * 1000),
            name=self.name_edit.text().strip(),
            note=self.note_edit.toPlainText().strip(),
            keyword=self.keyword_edit.text().strip(),
            color=self.colors.color(),
        )

    def color_title(self) -> str:
        return color_title(self.colors.color())


def _wrap(layout) -> QWidget:
    holder = QWidget()
    holder.setLayout(layout)
    return holder
