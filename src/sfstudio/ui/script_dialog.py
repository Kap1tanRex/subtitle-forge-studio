"""Импорт текста без таймингов: выбор разбиения и предпросмотр.

Заказчик присылает не субтитры, а текст: сценарий, расшифровку, готовый
перевод из редактора. Реплик в нём нет — есть строки и абзацы, и как их
делить, программа знать не может.

Поэтому окно устроено так: разбиение предлагается, но выбирает человек, а
результат виден до того, как что-то попадёт в документ. Ошибка в разбиении
рушит весь дальнейший тайминг, а заметить её после импорта трёхсот реплик
гораздо дороже, чем до.

Тайминги здесь заведомо условные — реплики просто ложатся подряд. Настоящее
время придёт из «Выровнять текст по речи», и окно говорит об этом прямо.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.services.script_import import (
    ScriptLine,
    guess_paragraphs,
    read_script,
    spread,
)

__all__ = ["ScriptImportDialog", "read_text_file"]

PREVIEW_ROWS = 200


def read_text_file(path: Path) -> str:
    """Читает текстовый файл, не полагаясь на кодировку.

    Сценарии приходят и в UTF-8, и в cp1251, и с BOM. Определение кодировки
    в программе уже есть — то же самое, что для субтитров.
    """
    from sfstudio.io.charset import decode_bytes

    text, _encoding = decode_bytes(path.read_bytes())
    return text


class ScriptImportDialog(QDialog):
    """Окно импорта текста без таймингов."""

    def __init__(
        self,
        path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Импорт текста без таймингов")
        self.resize(820, 640)
        self._text = ""
        self._lines: list[ScriptLine] = []
        self._ready = False

        root = QVBoxLayout(self)
        root.addWidget(self._explain())
        root.addWidget(self._file_row())
        root.addWidget(self._options())
        root.addWidget(self._preview_group(), 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("Импортировать")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._ready = True
        if path is not None:
            self.load(path)
        else:
            self._refresh()

    # -- построение --------------------------------------------------------------- #

    def _explain(self) -> QLabel:
        label = QLabel(
            "Реплики лягут подряд с условной длительностью. Настоящий тайминг "
            "делается потом: Тайминг → «Выровнять текст по речи»."
        )
        label.setWordWrap(True)
        label.setProperty("role", "hint")
        return label

    def _file_row(self) -> QWidget:
        box = QGroupBox("Файл")
        row = QHBoxLayout(box)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("текстовый файл со сценарием или переводом")
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._choose)
        row.addWidget(browse)
        return box

    def _options(self) -> QWidget:
        box = QGroupBox("Как делить на реплики")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        self.by_lines = QRadioButton("Каждая строка — реплика")
        self.by_paragraphs = QRadioButton("Каждый абзац — реплика")
        self.by_lines.setChecked(True)
        for button in (self.by_lines, self.by_paragraphs):
            button.toggled.connect(self._refresh)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self.speakers_check = QCheckBox("Имя перед двоеточием — это актор")
        self.speakers_check.setToolTip(
            "«ИВАН: Ты слушаешь?» станет репликой «Ты слушаешь?» актора ИВАН"
        )
        self.speakers_check.toggled.connect(self._refresh)
        layout.addWidget(self.speakers_check)

        self.noise_check = QCheckBox("Убрать номера страниц и ремарки в скобках")
        self.noise_check.setChecked(True)
        self.noise_check.setToolTip(
            "Строки вида «12» или «(смеётся)» целиком — в субтитры они не идут"
        )
        self.noise_check.toggled.connect(self._refresh)
        layout.addWidget(self.noise_check)
        return box

    def _preview_group(self) -> QWidget:
        box = QGroupBox("Что получится")
        layout = QVBoxLayout(box)

        self.summary = QLabel("Выберите файл.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.preview = QTreeWidget()
        self.preview.setColumnCount(3)
        self.preview.setHeaderLabels(["№", "Актор", "Текст"])
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.setAlternatingRowColors(True)
        self.preview.setColumnWidth(0, 50)
        self.preview.setColumnWidth(1, 130)
        self.preview.header().setStretchLastSection(True)
        layout.addWidget(self.preview, 1)
        return box

    # -- работа ------------------------------------------------------------------- #

    def _choose(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self,
            "Текст без таймингов",
            "",
            "Текстовые файлы (*.txt *.text *.md);;Все файлы (*)",
        )
        if chosen:
            self.load(Path(chosen))

    def load(self, path: Path) -> None:
        """Читает файл и предлагает разбиение, похожее на правду."""
        try:
            self._text = read_text_file(path)
        except OSError as exc:
            self._text = ""
            self.summary.setText(f"Файл не прочитался: {exc}")
            self._refresh()
            return

        self.path_edit.setText(str(path))
        # Угадывание — предложение, а не решение: переключатель на месте, и
        # результат виден сразу.
        self.by_paragraphs.setChecked(guess_paragraphs(self._text))
        self.by_lines.setChecked(not self.by_paragraphs.isChecked())
        self._refresh()

    def _refresh(self, *_args) -> None:
        if not self._ready:
            return
        self._lines = read_script(
            self._text,
            paragraphs=self.by_paragraphs.isChecked(),
            speakers=self.speakers_check.isChecked(),
            skip_noise=self.noise_check.isChecked(),
        )
        self._fill_preview()

        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(bool(self._lines))
        if not self._text:
            self.summary.setText("Выберите файл.")
            return
        if not self._lines:
            self.summary.setText(
                "В файле не нашлось ни одной реплики. Попробуйте другое "
                "разбиение или снимите отбрасывание служебных строк."
            )
            return

        from sfstudio.core.plural import plural

        note = plural(len(self._lines), "реплика", "реплики", "реплик")
        actors = {line.actor for line in self._lines if line.actor}
        if actors:
            note += f", акторов: {len(actors)}"
        if len(self._lines) > PREVIEW_ROWS:
            note += f" · показаны первые {PREVIEW_ROWS}"
        self.summary.setText(f"Получится {note}.")

    def _fill_preview(self) -> None:
        self.preview.setUpdatesEnabled(False)
        self.preview.clear()
        try:
            for index, line in enumerate(self._lines[:PREVIEW_ROWS], start=1):
                self.preview.addTopLevelItem(
                    QTreeWidgetItem([
                        str(index),
                        line.actor,
                        line.text.replace("\\N", " / "),
                    ])
                )
        finally:
            self.preview.setUpdatesEnabled(True)

    # -- результат ------------------------------------------------------------------ #

    def lines(self) -> list[ScriptLine]:
        """Разобранные реплики — для вызывающего кода и тестов."""
        return list(self._lines)

    def times(self) -> list[tuple[int, int]]:
        """Условные тайминги: реплики подряд, до выравнивания по речи."""
        return spread(self._lines)
