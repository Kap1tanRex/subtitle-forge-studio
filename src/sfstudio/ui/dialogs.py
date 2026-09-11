"""Диалоги: выбор вшитой дорожки и настройки записи в контейнер."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.io.container import CONTAINER_CODEC, MuxOptions, lossy_warning
from sfstudio.media.probe import SubtitleTrackInfo
from sfstudio.ui.icons import make_icon
from sfstudio.ui.theme import DARK


def warning_row(text: str, palette=DARK) -> QWidget:
    """Предупреждение: нарисованный знак и текст рядом.

    Знак рисуется, а не ставится символом ``⚠``: символьный вариант зависит
    от шрифта, и там, где его нет, на месте предупреждения оказывается пустой
    прямоугольник — то есть заметнее всего пропадает именно то, что важнее
    всего заметить.
    """
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    mark = QLabel()
    mark.setPixmap(make_icon("warning", palette.warning, 18).pixmap(QSize(18, 18)))
    mark.setAlignment(Qt.AlignTop)
    layout.addWidget(mark)

    label = QLabel(text)
    label.setProperty("role", "warning")
    label.setWordWrap(True)
    layout.addWidget(label, 1)
    return row

__all__ = ["MuxDialog", "TrackPickerDialog", "warning_row"]


class TrackPickerDialog(QDialog):
    """Выбор субтитровой дорожки из контейнера.

    Битмапные дорожки показываются, но выбрать их нельзя. Прятать их было бы
    хуже: пользователь видел бы «субтитров нет» там, где они есть, и не понимал
    почему.
    """

    def __init__(
        self,
        tracks: list[SubtitleTrackInfo],
        media_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Дорожки субтитров'))
        self.resize(560, 320)
        self._tracks = tracks

        layout = QVBoxLayout(self)
        header = QLabel(tr('В файле «{0}» найдено дорожек: {1}').format(media_name, len(tracks)))
        header.setWordWrap(True)
        layout.addWidget(header)

        self.list = QListWidget()
        for track in tracks:
            item = QListWidgetItem(track.display_name())
            item.setData(Qt.UserRole, track.index)
            if not track.is_editable:
                # Оставляем видимой, но невыбираемой — с пояснением в тексте.
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.list.addItem(item)

        first_editable = next(
            (i for i, t in enumerate(tracks) if t.is_editable), None
        )
        if first_editable is not None:
            self.list.setCurrentRow(first_editable)
        layout.addWidget(self.list)

        hint = QLabel(
            tr('Дорожки-картинки (PGS, VobSub) нельзя открыть на правку без OCR.')
            if any(t.is_bitmap for t in tracks)
            else tr('Выберите дорожку для правки.')
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Open | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Open).setText(tr('Открыть дорожку'))
        buttons.button(QDialogButtonBox.Cancel).setText(tr('Без субтитров'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        self._ok = buttons.button(QDialogButtonBox.Open)
        self.list.currentRowChanged.connect(self._sync_ok)
        self._sync_ok(self.list.currentRow())

    def _sync_ok(self, row: int) -> None:
        enabled = 0 <= row < len(self._tracks) and self._tracks[row].is_editable
        self._ok.setEnabled(enabled)

    def selected_index(self) -> int | None:
        """Индекс потока выбранной дорожки."""
        item = self.list.currentItem()
        if item is None or not (item.flags() & Qt.ItemIsEnabled):
            return None
        return int(item.data(Qt.UserRole))


class MuxDialog(QDialog):
    """Параметры записи субтитров обратно в контейнер."""

    def __init__(
        self,
        output: Path,
        tracks: list[SubtitleTrackInfo],
        parent: QWidget | None = None,
        *,
        current_index: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Сохранить в контейнер'))
        self.resize(520, 300)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.target = QComboBox()
        self.target.addItem(tr('Добавить новую дорожку'), None)
        for track in tracks:
            self.target.addItem(tr('Заменить: {0}').format(track.display_name()), track.index)
        if current_index is not None:
            position = self.target.findData(current_index)
            if position >= 0:
                self.target.setCurrentIndex(position)
        form.addRow(tr('Куда:'), self.target)

        self.language = QLineEdit("rus")
        self.language.setMaxLength(3)
        self.language.setPlaceholderText(tr('ISO 639-2, например rus или eng'))
        form.addRow(tr('Язык:'), self.language)

        self.title = QLineEdit()
        self.title.setPlaceholderText(tr('Необязательно'))
        form.addRow(tr('Название:'), self.title)

        self.default = QCheckBox(tr('Дорожка по умолчанию'))
        self.forced = QCheckBox(tr('Forced (только надписи)'))
        form.addRow("", self.default)
        form.addRow("", self.forced)
        layout.addLayout(form)

        codec = CONTAINER_CODEC.get(output.suffix.lower(), "?")
        codec_label = QLabel(tr('Формат субтитров в контейнере: <b>{0}</b>').format(codec))
        layout.addWidget(codec_label)

        warning = lossy_warning(output)
        if warning:
            layout.addWidget(warning_row(warning))

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Save).setText(tr('Записать'))
        buttons.button(QDialogButtonBox.Cancel).setText(tr('Отмена'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> MuxOptions:
        return MuxOptions(
            language=self.language.text().strip() or None,
            title=self.title.text().strip() or None,
            default=self.default.isChecked(),
            forced=self.forced.isChecked(),
            replace_index=self.target.currentData(),
        )
