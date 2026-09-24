"""Строка состояния и кнопка поиска команд в строке меню.

Строка состояния отвечает на вопросы, которые задают, не отрываясь от
работы: сохранено ли, сколько замечаний, сколько готово, что за файл.
Слева — сохранность (её перекрывают временные сообщения окна), справа —
постоянные сведения.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QToolButton,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.ui.icons import make_icon
from sfstudio.ui.theme import DARK, Palette
from sfstudio.ui.widgets import label

__all__ = ["CommandButton", "StatusStrip"]


class CommandButton(QPushButton):
    """«Найти команду · Ctrl+P» справа в строке меню — вход в палитру.

    Выглядит полем поиска: так понятнее, что туда пишут, а не жмут.
    """

    def __init__(self, keys: str = "Ctrl+P", palette: Palette = DARK) -> None:
        super().__init__()
        self.setProperty("role", "command")
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(tr('Командная палитра'))
        self.setToolTip(tr('Командная палитра') + f" ({keys})")
        self.setFixedSize(280, 24)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 4, 0)
        row.setSpacing(8)
        self._glass = QLabel()
        row.addWidget(self._glass)
        row.addWidget(label(tr('Найти команду'), "hint"), 1)
        self._keys = label(keys, "keycap")
        row.addWidget(self._keys)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.set_palette(palette)

    def set_keys(self, keys: str) -> None:
        """Сочетание могли переназначить в настройках — подпись за ним."""
        self._keys.setText(keys)
        self.setToolTip(tr('Командная палитра') + f" ({keys})")

    def set_palette(self, palette: Palette) -> None:
        self._glass.setPixmap(make_icon("search", palette.text_muted, 14).pixmap(14, 14))


class StatusStrip(QWidget):
    """Правая часть строки состояния."""

    #: Щелчок по счётчику замечаний — открыть вкладку «Проверки».
    issues_clicked = Signal()

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._counts = (0, 0)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(18)

        #: Слева строки: «Сохранено в 14:32». Окно ставит её отдельно.
        self.saved = QLabel("")

        self.issues = QToolButton()
        self.issues.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.issues.setCursor(Qt.PointingHandCursor)
        self.issues.setToolTip(tr('Открыть вкладку «Проверки»'))
        self.issues.clicked.connect(self.issues_clicked)
        issues_row = QHBoxLayout(self.issues)
        issues_row.setContentsMargins(6, 0, 6, 0)
        issues_row.setSpacing(4)
        #: Значок и число для ошибок и для предупреждений.
        self._marks: list[tuple[QLabel, QLabel]] = []
        for _ in range(2):
            icon, number = QLabel(), QLabel()
            issues_row.addWidget(icon)
            issues_row.addWidget(number)
            issues_row.addSpacing(4)
            self._marks.append((icon, number))
        self._caption = QLabel(tr('Замечания'))
        issues_row.addWidget(self._caption)
        for child in self.issues.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(self.issues)

        self.progress_text = QLabel("")
        self.progress = QProgressBar()
        self.progress.setProperty("role", "done")
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(64, 4)
        done_row = QHBoxLayout()
        done_row.setSpacing(8)
        done_row.addWidget(self.progress_text)
        done_row.addWidget(self.progress)
        row.addLayout(done_row)

        self.resolution = QLabel("")
        self.fps = QLabel("")
        self.format = QLabel("")
        for widget in (self.resolution, self.fps, self.format):
            row.addWidget(widget)
        self.set_issues(0, 0)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.set_issues(*self._counts)

    def set_saved(self, text: str) -> None:
        self.saved.setText(text)

    def set_issues(self, errors: int, warnings: int) -> None:
        """Счётчики ошибок и предупреждений значками, как на вкладке."""
        self._counts = (errors, warnings)
        p = self._palette
        for (icon, number), name, colour, count in zip(
            self._marks,
            ("error", "warning"),
            (p.danger_text, p.warning_text),
            (errors, warnings),
            strict=True,
        ):
            icon.setPixmap(make_icon(name, colour, 13).pixmap(13, 13))
            number.setText(str(count))
            number.setStyleSheet(f"color: {colour}; padding: 0;")
            icon.setVisible(count > 0)
            number.setVisible(count > 0)
        self._caption.setText(tr('Замечания') if errors or warnings else tr('Замечаний нет'))
        self.issues.setMinimumWidth(self.issues.layout().sizeHint().width())

    def set_progress(self, done: int, total: int) -> None:
        """«Готово 96 из 312» — только когда пометки вообще расставлены."""
        shown = done > 0
        self.progress_text.setVisible(shown)
        self.progress.setVisible(shown)
        if shown:
            self.progress_text.setText(tr('Готово {0} из {1}').format(done, total))
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)

    def set_info(self, resolution: str, fps: str, fmt: str) -> None:
        self.resolution.setText(resolution)
        self.fps.setText(fps)
        self.fps.setVisible(bool(fps))
        self.format.setText(fmt)

