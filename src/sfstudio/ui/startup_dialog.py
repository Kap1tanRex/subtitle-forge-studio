"""Стартовое окно: недавние проекты и создание нового.

Появляется до главного окна и решает единственный вопрос — с чем работать.
Показывает недавние проекты с датой изменения и путём, потому что имени файла
недостаточно: «Серия 3» может лежать в трёх местах, и различить их можно
только по папке.

Пропавшие проекты из списка **не удаляются молча**. Файл мог лежать на съёмном
диске или в отключённой сетевой папке; вычеркнуть его — значит потерять запись
о работе, которая никуда не делась. Такие строки показываются неактивными и с
пояснением.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sfstudio import __version__
from sfstudio.app.i18n import tr
from sfstudio.core.project import PROJECT_SUFFIX

__all__ = ["StartupDialog"]

#: Что выбрал пользователь.
ACTION_NEW = "new"
ACTION_OPEN = "open"
ACTION_IMPORT = "import"


class StartupDialog(QDialog):
    """Выбор проекта при запуске."""

    #: Путь выбранного проекта — когда открывают существующий.
    project_chosen = Signal(object)

    def __init__(self, recent: list[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SubtitleForge Studio")
        self.resize(760, 480)
        self._action: str | None = None
        self._path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel(tr("Проекты"))
        title.setProperty("role", "title")
        layout.addWidget(title)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setAlternatingRowColors(True)
        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.itemSelectionChanged.connect(self._refresh_buttons)
        layout.addWidget(self.list, 1)

        self.empty_hint = QLabel(
            "Недавних проектов нет. Создайте новый или откройте файл "
            f"{PROJECT_SUFFIX}."
        )
        self.empty_hint.setProperty("role", "hint")
        self.empty_hint.setWordWrap(True)
        layout.addWidget(self.empty_hint)

        row = QHBoxLayout()
        self.version_label = QLabel(f"Версия {__version__}")
        self.version_label.setProperty("role", "hint")
        row.addWidget(self.version_label)
        row.addStretch(1)

        self.import_button = QPushButton(tr("Импорт субтитров…"))
        self.import_button.setToolTip(
            tr("Открыть .ass, .srt или .vtt как новый проект")
        )
        self.import_button.clicked.connect(lambda: self._finish(ACTION_IMPORT))
        row.addWidget(self.import_button)

        self.browse_button = QPushButton(tr("Открыть…"))
        self.browse_button.clicked.connect(lambda: self._finish(ACTION_OPEN))
        row.addWidget(self.browse_button)

        self.new_button = QPushButton(tr("Новый проект"))
        self.new_button.setDefault(True)
        self.new_button.clicked.connect(lambda: self._finish(ACTION_NEW))
        row.addWidget(self.new_button)

        self.open_button = QPushButton(tr("Продолжить"))
        self.open_button.setToolTip(tr("Открыть выбранный проект"))
        self.open_button.clicked.connect(self._open_selected)
        row.addWidget(self.open_button)

        layout.addLayout(row)

        self._fill(recent)
        self._refresh_buttons()

    # -- список ------------------------------------------------------------------- #

    def _fill(self, recent: list[Path]) -> None:
        self.list.clear()
        for path in recent:
            item = QListWidgetItem(self._describe(path))
            item.setData(Qt.UserRole, str(path))
            item.setSizeHint(QSize(0, 46))
            if not path.exists():
                # Не выбрасываем: диск может вернуться. Но и открыть нельзя.
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.list.addItem(item)

        has_items = self.list.count() > 0
        self.empty_hint.setVisible(not has_items)
        if has_items:
            self._select_first_enabled()

    def _describe(self, path: Path) -> str:
        if not path.exists():
            return f"{path.stem}\n{path}  ·  файл недоступен"
        try:
            changed = datetime.fromtimestamp(path.stat().st_mtime)
            when = changed.strftime("%d.%m.%Y %H:%M")
        except OSError:
            when = "—"
        return f"{path.stem}\n{path.parent}  ·  {when}"

    def _select_first_enabled(self) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).flags() & Qt.ItemIsEnabled:
                self.list.setCurrentRow(row)
                return

    def _refresh_buttons(self) -> None:
        item = self.list.currentItem()
        enabled = item is not None and bool(item.flags() & Qt.ItemIsEnabled)
        self.open_button.setEnabled(enabled)

    # -- результат ---------------------------------------------------------------- #

    def _open_item(self, item: QListWidgetItem) -> None:
        if not (item.flags() & Qt.ItemIsEnabled):
            return
        self._path = Path(str(item.data(Qt.UserRole)))
        self._action = ACTION_OPEN
        self.accept()

    def _open_selected(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._open_item(item)

    def _finish(self, action: str) -> None:
        self._action = action
        self._path = None
        self.accept()

    @property
    def action(self) -> str | None:
        """``new``, ``open``, ``import`` либо ``None``, если окно закрыли."""
        return self._action

    @property
    def chosen_path(self) -> Path | None:
        """Проект из списка. ``None``, если выбран не список, а кнопка."""
        return self._path
