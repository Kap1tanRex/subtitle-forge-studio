"""Окно глоссария: как переводить термины и имена.

Таблица на две колонки плюс отметка «обязательно». Правка прямо в ячейке —
глоссарий пополняют по ходу работы, и открывать отдельное окно ради одного
слова никто не станет.

Импорт и экспорт CSV: в таком виде глоссарии присылают заказчики, и в таком
же их ждут обратно.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.core.glossary import Glossary, Term

__all__ = ["GlossaryDialog"]

CSV_FILTER = tr('Таблица CSV (*.csv);;Все файлы (*)')


class GlossaryDialog(QDialog):
    """Правка глоссария проекта."""

    #: Глоссарий изменился — окну нужно перепроверить реплики.
    changed = Signal(object)

    def __init__(self, glossary: Glossary | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Глоссарий'))
        self.resize(640, 460)

        layout = QVBoxLayout(self)

        hint = QLabel(
            tr('Термины проверяются по оригиналу: если он подключён и в нём есть слово из '
                   'левой колонки, а в переводе нет соответствия — реплика попадёт в '
                       'панель проверок.')
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [tr('В оригинале'), tr('Перевод'), tr('Обязательно'), tr('Заметка')]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 90)
        layout.addWidget(self.table, 1)

        buttons_row = QHBoxLayout()
        add = QPushButton(tr('Добавить'))
        add.clicked.connect(self._add_row)
        buttons_row.addWidget(add)

        remove = QPushButton(tr('Удалить'))
        remove.clicked.connect(self._remove_rows)
        buttons_row.addWidget(remove)

        buttons_row.addStretch(1)

        load = QPushButton(tr('Импорт CSV…'))
        load.clicked.connect(self._import_csv)
        buttons_row.addWidget(load)

        save = QPushButton(tr('Экспорт CSV…'))
        save.clicked.connect(self._export_csv)
        buttons_row.addWidget(save)
        layout.addLayout(buttons_row)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText(tr('Применить'))
        box.button(QDialogButtonBox.Cancel).setText(tr('Отмена'))
        box.accepted.connect(self._apply)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self._fill(glossary or Glossary())

    # -- содержимое ----------------------------------------------------------- #

    def _fill(self, glossary: Glossary) -> None:
        self.table.setRowCount(0)
        for term in glossary:
            self._add_row(term)

    def _add_row(self, term: Term | None = None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        source = QTableWidgetItem(term.source if term else "")
        target = QTableWidgetItem(term.target if term else "")
        note = QTableWidgetItem(term.note if term else "")

        required = QTableWidgetItem()
        required.setFlags(
            Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable
        )
        # Обязательным по умолчанию: глоссарий присылают, чтобы ему следовали,
        # а не чтобы держать в уме.
        checked = term.required if term else True
        required.setCheckState(Qt.Checked if checked else Qt.Unchecked)

        for column, item in enumerate((source, target, required, note)):
            self.table.setItem(row, column, item)

        if term is None:
            self.table.setCurrentCell(row, 0)
            self.table.editItem(source)

    def _remove_rows(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def glossary(self) -> Glossary:
        """Собирает глоссарий из таблицы, пропуская незаполненные строки."""
        terms: list[Term] = []
        for row in range(self.table.rowCount()):
            source = self._text(row, 0)
            target = self._text(row, 1)
            if not source or not target:
                continue
            required = self.table.item(row, 2).checkState() == Qt.Checked
            terms.append(Term(source, target, required, self._text(row, 3)))
        return Glossary(terms)

    def _text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item else ""

    # -- обмен ---------------------------------------------------------------- #

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr('Импорт глоссария'), "", CSV_FILTER)
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.warning(self, tr('Импорт'), f"Не удалось прочитать:\n{exc}")
            return

        imported = Glossary.from_csv(raw)
        if not imported:
            QMessageBox.warning(
                self, tr('Импорт'),
                tr('В файле не нашлось ни одной пары «оригинал — перевод».'),
            )
            return

        # Дописываем к тому, что есть: обычно присылают дополнение к
        # существующему списку, а не замену ему.
        for term in imported:
            self._add_row(term)

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr('Экспорт глоссария'), "", CSV_FILTER)
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".csv")
        try:
            target.write_text(self.glossary().to_csv(), encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.warning(self, tr('Экспорт'), f"Не удалось записать:\n{exc}")

    def _apply(self) -> None:
        self.changed.emit(self.glossary())
        self.accept()
