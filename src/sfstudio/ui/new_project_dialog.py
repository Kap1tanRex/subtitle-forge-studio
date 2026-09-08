"""Параметры нового проекта: разрешение, частота кадров, место хранения.

Разрешение здесь — это ``PlayRes``, система координат субтитров, а не размер
видео. Их легко перепутать, поэтому в окне это сказано прямо: от PlayRes
зависит, куда попадут координаты ``\\pos``, и менять его потом, когда реплики
уже расставлены, значит сдвинуть их все.

Частота кадров задаётся точной дробью, а не десятичным числом. 23,976 — это
24000/1001; за два часа округление до трёх знаков расходится почти на четыре
кадра, и покадровая привязка перестаёт совпадать с видео.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.project import (
    FPS_PRESETS,
    PROJECT_SUFFIX,
    RESOLUTION_PRESETS,
    Project,
)
from sfstudio.ui.combo import select_data
from sfstudio.ui.font_box import FontComboBox

__all__ = ["NewProjectDialog"]

CUSTOM = "Произвольное…"


class NewProjectDialog(QDialog):
    """Создание проекта."""

    def __init__(
        self,
        default_folder: Path,
        parent: QWidget | None = None,
        media_path: Path | None = None,
        *,
        default_font: str = "Arial",
        default_size: float = 54.0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новый проект")
        self.setMinimumWidth(520)
        self._folder = Path(default_folder)
        self._media = media_path
        self._default_font = default_font
        self._default_size = default_size

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.name_edit = QLineEdit("Новый проект")
        self.name_edit.textChanged.connect(self._refresh_path_hint)
        form.addRow("Название", self.name_edit)

        # -- место хранения ------------------------------------------------- #
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(str(self._folder))
        self.folder_edit.textChanged.connect(self._refresh_path_hint)
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(browse)
        form.addRow("Папка", folder_row)

        self.path_hint = QLabel("")
        self.path_hint.setProperty("role", "hint")
        self.path_hint.setWordWrap(True)
        form.addRow("", self.path_hint)

        # -- разрешение ------------------------------------------------------ #
        self.resolution_box = QComboBox()
        for title, width, height in RESOLUTION_PRESETS:
            self.resolution_box.addItem(title, (width, height))
        self.resolution_box.addItem(CUSTOM, None)
        self.resolution_box.currentIndexChanged.connect(self._on_resolution)
        form.addRow("Разрешение", self.resolution_box)

        size_row = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(16, 16384)
        self.width_spin.setValue(1920)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(16, 16384)
        self.height_spin.setValue(1080)
        for spin in (self.width_spin, self.height_spin):
            spin.setEnabled(False)
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.height_spin)
        size_row.addStretch(1)
        form.addRow("", size_row)

        note = QLabel(
            "Разрешение задаёт систему координат субтитров (PlayRes). "
            "Менять его после расстановки реплик — значит сдвинуть их все."
        )
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        form.addRow("", note)

        # -- частота кадров --------------------------------------------------- #
        self.fps_box = QComboBox()
        for title, value in FPS_PRESETS:
            self.fps_box.addItem(title, value)
        self.fps_box.setCurrentIndex(2)  # 25
        form.addRow("Частота кадров", self.fps_box)

        # -- оформление ---------------------------------------------------- #
        self.font_box = FontComboBox()
        self.font_box.setToolTip(
            "Наведите на шрифт в списке — появится образец русского и "
            "английского текста"
        )
        form.addRow("Шрифт субтитров", self.font_box)

        # -- медиа ------------------------------------------------------------- #
        media_row = QHBoxLayout()
        self.media_edit = QLineEdit(str(media_path) if media_path else "")
        self.media_edit.setPlaceholderText("необязательно — можно открыть позже")
        media_row.addWidget(self.media_edit, 1)
        pick_media = QPushButton("Выбрать…")
        pick_media.clicked.connect(self._choose_media)
        media_row.addWidget(pick_media)
        form.addRow("Видео", media_row)

        self.match_media = QCheckBox(
            "Взять разрешение и частоту кадров из видео"
        )
        self.match_media.setChecked(True)
        self.match_media.setToolTip(
            "Параметры будут уточнены при открытии файла — так они точно "
            "совпадут с исходником"
        )
        form.addRow("", self.match_media)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Создать")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.font_box.set_family(default_font)
        self._refresh_path_hint()

    # -- обработчики -------------------------------------------------------------- #

    def _on_resolution(self, index: int) -> None:
        custom = self.resolution_box.itemData(index) is None
        self.width_spin.setEnabled(custom)
        self.height_spin.setEnabled(custom)
        if not custom:
            width, height = self.resolution_box.itemData(index)
            self.width_spin.setValue(width)
            self.height_spin.setValue(height)

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Папка проекта", self.folder_edit.text()
        )
        if chosen:
            self.folder_edit.setText(chosen)

    def _choose_media(self) -> None:
        from sfstudio.ui.main_window import MEDIA_FILTER

        chosen, _ = QFileDialog.getOpenFileName(
            self, "Видео проекта", self.folder_edit.text(), MEDIA_FILTER
        )
        if chosen:
            self.media_edit.setText(chosen)

    def _refresh_path_hint(self) -> None:
        path = self.project_path()
        if path is None:
            self.path_hint.setText("Укажите название и папку.")
            return
        exists = " — файл уже существует и будет перезаписан" if path.exists() else ""
        self.path_hint.setText(f"Файл: {path}{exists}")

    # -- результат ----------------------------------------------------------------- #

    def select_resolution(self, width: int, height: int) -> None:
        """Выбирает пресет разрешения либо переходит в «Произвольное»."""
        if not select_data(self.resolution_box, (width, height)):
            self.resolution_box.setCurrentIndex(self.resolution_box.count() - 1)
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)

    def select_fps(self, value) -> bool:
        return select_data(self.fps_box, value)

    def project_path(self) -> Path | None:
        """Куда ляжет файл проекта. ``None``, если данных не хватает."""
        name = self.name_edit.text().strip()
        folder = self.folder_edit.text().strip()
        if not name or not folder:
            return None
        # Пользователь мог набрать имя с расширением — второй раз его не добавляем.
        stem = name[: -len(PROJECT_SUFFIX)] if name.lower().endswith(PROJECT_SUFFIX) else name
        return Path(folder) / f"{stem}{PROJECT_SUFFIX}"

    def media_path(self) -> Path | None:
        text = self.media_edit.text().strip()
        return Path(text) if text else None

    def fps(self) -> Fraction:
        return self.fps_box.currentData() or Fraction(25)

    def resolution(self) -> tuple[int, int]:
        return self.width_spin.value(), self.height_spin.value()

    def match_media_params(self) -> bool:
        return self.match_media.isChecked()

    def font_family(self) -> str:
        return self.font_box.currentFont().family()

    def build(self) -> Project:
        """Собирает проект по введённым параметрам."""
        project = Project.new(
            name=self.name_edit.text().strip() or "Новый проект",
            resolution=self.resolution(),
            fps=self.fps(),
            media_path=self.media_path(),
        )
        # Стиль по умолчанию правим сразу: менять его потом руками в каждом
        # новом проекте — ровно та работа, которой настройка и должна избавить.
        default = project.document.styles.get("Default")
        if default is not None:
            default.fontname = self.font_family()
            default.fontsize = self._default_size
        return project
