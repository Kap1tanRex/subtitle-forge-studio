"""Список маркеров: найти нужный и перейти к нему.

Флажки на линейке хороши, пока их десяток. На полнометражном фильме их
набирается сотня, и половина — вне видимого участка: чтобы дойти до отметки
«спросить у заказчика», пришлось бы прокручивать таймлайн и вглядываться в
цвета. Поэтому список.

Поиск идёт сразу по имени, примечанию и ключевому слову. Разделять их тремя
полями незачем: человек помнит слово, а не то, в какую графу он его тогда
вписал.

Окно немодальное по смыслу работы — по нему ходят, пока правят субтитры, —
но открывается обычным ``exec``: правка маркера идёт через своё окно, и два
модальных окна поверх немодального вели бы себя на разных системах
по-разному.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.markers import Marker, color_title, color_value
from sfstudio.core.time import format_ass

__all__ = ["MarkersDialog"]

_DOT_PX = 12


def _dot(value: str) -> QIcon:
    """Кружок цвета для строки списка."""
    pixmap = QPixmap(_DOT_PX + 4, _DOT_PX + 4)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(value))
    painter.drawEllipse(2, 2, _DOT_PX, _DOT_PX)
    painter.end()
    return QIcon(pixmap)


class MarkersDialog(QDialog):
    """Все маркеры документа списком."""

    def __init__(
        self,
        doc: SubtitleDocument,
        timeline,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Маркеры")
        self.resize(760, 520)
        self._doc = doc
        self._timeline = timeline

        root = QVBoxLayout(self)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Найти"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("по имени, примечанию или ключевому слову")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)
        search_row.addWidget(self.search, 1)
        root.addLayout(search_row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Время", "Имя", "Ключевое слово", "Примечание"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 160)
        self.tree.setColumnWidth(1, 200)
        self.tree.setColumnWidth(2, 140)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.itemActivated.connect(lambda *_: self._edit())
        root.addWidget(self.tree, 1)

        self.summary = QLabel("")
        self.summary.setProperty("role", "hint")
        root.addWidget(self.summary)

        # Кнопки разложены вручную, а не ролями QDialogButtonBox: тот ставит
        # «Удалить» первой и делает её кнопкой по умолчанию, то есть Enter в
        # списке уносил бы маркер вместо перехода к нему.
        row = QHBoxLayout()
        self.goto_button = QPushButton("Перейти")
        self.goto_button.setToolTip("Поставить курсор времени на этот маркер")
        self.goto_button.setDefault(True)
        self.goto_button.clicked.connect(self._goto)
        row.addWidget(self.goto_button)

        self.edit_button = QPushButton("Правка…")
        self.edit_button.clicked.connect(self._edit)
        row.addWidget(self.edit_button)

        self.delete_button = QPushButton("Удалить")
        self.delete_button.clicked.connect(self._delete)
        row.addWidget(self.delete_button)

        row.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.reject)
        row.addWidget(close_button)
        root.addLayout(row)

        self._refill()

    # -- список -------------------------------------------------------------- #

    def _refill(self) -> None:
        needle = self.search.text().strip().lower()
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        shown = 0
        try:
            for marker in self._doc.markers:
                if needle and not self._matches(marker, needle):
                    continue
                item = QTreeWidgetItem([
                    format_ass(marker.time),
                    marker.name,
                    marker.keyword,
                    marker.note.replace("\n", " / "),
                ])
                item.setIcon(0, _dot(color_value(marker.color)))
                item.setToolTip(0, color_title(marker.color))
                if marker.duration:
                    item.setText(0, f"{format_ass(marker.time)}  +{marker.duration / 1000:.1f} с")
                # Сам маркер, а не индекс: список фильтруется, и позиция в
                # нём не совпадает с позицией в документе.
                item.setData(0, Qt.UserRole, marker)
                self.tree.addTopLevelItem(item)
                shown += 1
        finally:
            self.tree.setUpdatesEnabled(True)

        total = len(self._doc.markers)
        if not total:
            self.summary.setText(
                "Маркеров нет. Ставятся флажком у линейки таймлайна или по Alt+M."
            )
        elif shown == total:
            self.summary.setText(f"Маркеров: {total}")
        else:
            self.summary.setText(f"Показано {shown} из {total}")
        self._on_selection()

    @staticmethod
    def _matches(marker: Marker, needle: str) -> bool:
        haystack = " ".join((marker.name, marker.keyword, marker.note)).lower()
        return needle in haystack

    def _current(self) -> Marker | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def _on_selection(self) -> None:
        has = self._current() is not None
        for button in (self.goto_button, self.edit_button, self.delete_button):
            button.setEnabled(has)

    # -- действия ------------------------------------------------------------- #

    def _goto(self) -> None:
        marker = self._current()
        if marker is not None:
            self._timeline.set_time(marker.time)
            self._timeline.time_changed.emit(marker.time)

    def _edit(self) -> None:
        marker = self._current()
        if marker is None:
            return
        # Переходим до правки: окно маркера показывает время числом, а
        # увидеть место в кадре полезнее, чем прочитать таймкод.
        self._goto()
        self._timeline.edit_marker(marker)
        self._refill()

    def _delete(self) -> None:
        marker = self._current()
        if marker is None:
            return
        self._timeline.remove_marker(marker)
        self._refill()
