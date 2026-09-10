"""Выравнивание текста по речи: запуск распознавания и предпросмотр плана.

Окно решает задачу «текст есть, таймингов нет». Распознавание здесь —
не результат, а инструмент: нужен только ряд слов со временем, по которому
раскладывается уже написанный перевод.

Три вещи, ради которых окно устроено именно так:

**Ничего не применяется молча.** Сначала показывается план: какая реплика
куда встанет и насколько этому можно верить. Выравнивание трогает тайминги
всего файла разом — принимать такое вслепую нельзя.

**Уверенность видна по каждой реплике.** На шумном звуке привязок мало, и
часть времени просто угадана. Показать ровный список без пометок значило бы
выдать догадку за результат: ровное никто не перепроверяет.

**Движки без таймингов слов сюда не годятся.** whisper.cpp выдаёт только
фразы целиком, и выровнять по ним нечего. Такой движок показан в списке
и объяснён, а не молча пропущен.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.time import format_ass
from sfstudio.services.alignment import AlignmentPlan, align
from sfstudio.services.asr import EngineInfo, RecognitionRequest, engine_infos
from sfstudio.ui.combo import select_data
from sfstudio.ui.theme import repolish

__all__ = ["LANGUAGES", "AlignmentDialog", "words_from"]

#: Языки те же, что в окне распознавания: список намеренно короткий.
LANGUAGES: tuple[tuple[str, str | None], ...] = (
    ("Определить автоматически", None),
    ("Русский", "ru"),
    ("Английский", "en"),
    ("Немецкий", "de"),
    ("Французский", "fr"),
    ("Испанский", "es"),
    ("Японский", "ja"),
    ("Китайский", "zh"),
)

PREVIEW_ROWS = 200


def words_from(segments) -> list:
    """Собирает слова со временем из сегментов распознавания.

    Сегменты без слов пропускаются: движок мог не вернуть их вовсе, и тогда
    выравнивать не по чему — об этом окно скажет прямо.
    """
    words = []
    for segment in segments:
        words.extend(segment.words)
    return words


class _PlanTree(QTreeWidget):
    """План: куда встанет каждая реплика и насколько этому верить."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["Реплика", "Станет", "Длительность", "Уверенность"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setColumnWidth(0, 320)
        self.setColumnWidth(1, 170)
        self.setColumnWidth(2, 110)

    def show_plan(
        self, plan: AlignmentPlan, doc: SubtitleDocument, *, only_shaky: bool
    ) -> int:
        """Заполняет список. Возвращает, сколько строк показано."""
        self.setUpdatesEnabled(False)
        self.clear()
        shown = 0
        try:
            for change in plan.changes:
                if only_shaky and not change.shaky:
                    continue
                if shown >= PREVIEW_ROWS:
                    break
                event = doc.get(change.eid)
                if event is None:
                    continue
                item = QTreeWidgetItem([
                    (event.plain or "(пусто)").replace("\n", " ")[:70],
                    f"{format_ass(change.start)} – {format_ass(change.end)}",
                    f"{(change.end - change.start) / 1000:.2f} с",
                    _confidence_text(change),
                ])
                if change.shaky:
                    item.setToolTip(0, _why_shaky(change))
                    item.setToolTip(3, _why_shaky(change))
                self.addTopLevelItem(item)
                shown += 1
        finally:
            self.setUpdatesEnabled(True)
        return shown


def _confidence_text(change) -> str:
    if change.guessed:
        return "нет опоры"
    return f"{change.confidence * 100:.0f} %"


def _why_shaky(change) -> str:
    if change.guessed:
        return (
            "Ни одно слово этой реплики не нашлось в распознанном тексте. "
            "Время взято из соседних реплик — проверьте его."
        )
    return (
        "Совпало меньше половины слов. Время посчитано по тому, что нашлось, "
        "но проверить стоит."
    )


class AlignmentDialog(QDialog):
    """Окно выравнивания текста по речи."""

    def __init__(
        self,
        doc: SubtitleDocument,
        media: Path | None,
        settings,
        selection: list[SubtitleEvent] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Выровнять текст по речи")
        self.resize(900, 680)
        self._doc = doc
        self._media = media
        self._settings = settings
        self._selection = list(selection or [])
        self._task = None
        self._plan = AlignmentPlan()

        root = QVBoxLayout(self)
        root.addWidget(self._explain())
        root.addWidget(self._scope_group())
        root.addWidget(self._engine_group())
        root.addWidget(self._progress_group(), 1)

        self.buttons = QDialogButtonBox()
        self.run_button = self.buttons.addButton(
            "Выровнять", QDialogButtonBox.ActionRole
        )
        self.run_button.clicked.connect(self._start)
        self.cancel_button = self.buttons.addButton(
            "Прервать", QDialogButtonBox.ActionRole
        )
        self.cancel_button.clicked.connect(self._cancel)
        self.apply_button = self.buttons.addButton(
            "Применить тайминги", QDialogButtonBox.AcceptRole
        )
        self.apply_button.clicked.connect(self.accept)
        close_button = self.buttons.addButton("Закрыть", QDialogButtonBox.RejectRole)
        close_button.clicked.connect(self.reject)
        root.addWidget(self.buttons)

        self._load_settings()
        self._refresh_engine()
        self._refresh_buttons(running=False)

    # -- построение --------------------------------------------------------------- #

    def _explain(self) -> QLabel:
        label = QLabel(
            "Программа прослушает дорожку и разложит уже написанный текст по "
            "речи. Сам текст не меняется — меняются только тайминги."
        )
        label.setWordWrap(True)
        label.setProperty("role", "hint")
        return label

    def _scope_group(self) -> QWidget:
        box = QGroupBox("Что выравнивать")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        self.scope_all = QRadioButton(f"Все реплики ({len(self._doc)})")
        self.scope_selection = QRadioButton(
            f"Только выделенные ({len(self._selection)})"
        )
        self.scope_selection.setEnabled(len(self._selection) > 1)
        # По умолчанию — всё: выравнивание части файла оставляет соседей на
        # старых местах, и это осознанный выбор, а не то, что нужно чаще.
        self.scope_all.setChecked(True)
        for button in (self.scope_all, self.scope_selection):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self.media_label = QLabel(
            self._media.name if self._media else "видео или аудио не открыто"
        )
        self.media_label.setProperty("role", "hint")
        layout.addWidget(self.media_label)
        return box

    def _engine_group(self) -> QWidget:
        box = QGroupBox("Распознавание")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.engine_box = QComboBox()
        self._infos: list[EngineInfo] = engine_infos()
        for info in self._infos:
            # Отсутствие таймингов слов важнее, чем то, установлен ли движок:
            # ставить его незачем — выравнивать по целым фразам всё равно
            # нечего, и знать об этом надо до установки, а не после.
            if not info.word_timings:
                suffix = "  — не выдаёт тайминги слов"
            elif not info.available:
                suffix = "  — не установлен"
            else:
                suffix = ""
            self.engine_box.addItem(f"{info.title}{suffix}", info.key)
        self.engine_box.currentIndexChanged.connect(self._refresh_engine)
        form.addRow("Движок", self.engine_box)

        self.engine_note = QLabel("")
        self.engine_note.setWordWrap(True)
        self.engine_note.setProperty("role", "hint")
        form.addRow("", self.engine_note)

        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        form.addRow("Модель", self.model_box)

        self.language_box = QComboBox()
        for title, code in LANGUAGES:
            self.language_box.addItem(title, code)
        self.language_box.setToolTip(
            "Указанный язык надёжнее определения на слух: ошибка здесь "
            "оставит текст без единой привязки"
        )
        form.addRow("Язык", self.language_box)

        hint = QLabel(
            "Модели скачиваются и настраиваются в окне «Распознать речь». "
            "Здесь используются те же настройки."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        form.addRow("", hint)
        return box

    def _progress_group(self) -> QWidget:
        box = QGroupBox("План")
        layout = QVBoxLayout(box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status = QLabel("Готово к запуску.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.only_shaky = QCheckBox("Показывать только сомнительные")
        self.only_shaky.setToolTip(
            "Реплики, для которых опор не нашлось или нашлось меньше половины"
        )
        self.only_shaky.toggled.connect(self._show_plan)
        layout.addWidget(self.only_shaky)

        self.preview = _PlanTree()
        layout.addWidget(self.preview, 1)
        return box

    # -- настройки ----------------------------------------------------------------- #

    def _load_settings(self) -> None:
        get = self._settings.get
        select_data(self.engine_box, str(get("asr.engine", "") or ""))
        select_data(self.language_box, get("asr.language", None))

    def _current_info(self) -> EngineInfo | None:
        key = self.engine_box.currentData()
        return next((info for info in self._infos if info.key == key), None)

    def _refresh_engine(self) -> None:
        info = self._current_info()
        if info is None:
            return

        self.model_box.clear()
        self.model_box.addItems(list(info.models))
        saved = str(self._settings.get("asr.model", "") or "")
        if saved:
            self.model_box.setCurrentText(saved)

        if not info.word_timings:
            # Отказ объясняется до запуска: иначе человек ждёт минуты и
            # получает пустой план без единой понятной причины.
            self.engine_note.setText(
                "Этот движок выдаёт только целые фразы, без времени отдельных "
                "слов. Выравнивать по ним нечего — возьмите faster-whisper."
            )
            role = "warning"
        elif not info.available:
            self.engine_note.setText(f"{info.description}\n\n{info.hint}")
            role = "warning"
        else:
            self.engine_note.setText(info.description)
            role = "hint"
        self.engine_note.setProperty("role", role)
        repolish(self.engine_note)
        self._refresh_buttons(running=False)

    def _refresh_buttons(self, *, running: bool) -> None:
        info = self._current_info()
        can_run = (
            not running
            and info is not None
            and info.available
            and info.word_timings
            and self._media is not None
            and bool(self._targets())
        )
        self.run_button.setEnabled(can_run)
        self.cancel_button.setEnabled(running)
        self.apply_button.setEnabled(not running and not self._plan.is_empty)
        self.engine_box.setEnabled(not running)

        if self._media is None:
            self.status.setText("Сначала откройте видео или аудио.")
        elif info is not None and not info.available:
            self.status.setText("Выбранный движок не установлен.")

    def _targets(self) -> list[SubtitleEvent]:
        """Реплики в порядке документа — порядок здесь принципиален.

        Способ держится на том, что текст идёт в том же порядке, что и речь.
        Перемешанный список сломал бы сопоставление, а не просто ухудшил его.
        """
        if self.scope_selection.isChecked() and self._selection:
            chosen = {event.eid for event in self._selection}
            return [event for event in self._doc.events if event.eid in chosen]
        return list(self._doc.events)

    # -- запуск -------------------------------------------------------------------- #

    def _start(self) -> None:
        info = self._current_info()
        if info is None or self._media is None:
            return

        self._plan = AlignmentPlan()
        self.preview.clear()
        self.progress.setValue(0)
        self.status.setText("Запуск распознавания…")

        models_dir = str(self._settings.get("asr.models_dir", "") or "")
        options: dict[str, object] = {}
        device = str(self._settings.get("asr.device", "") or "")
        if device:
            options["device"] = device

        request = RecognitionRequest(
            media=self._media,
            language=self.language_box.currentData(),
            model=self.model_box.currentText().strip(),
            models_dir=Path(models_dir) if models_dir else None,
            word_timings=True,
            options=options,
        )

        from sfstudio.ui.asr_task import RecognitionTask

        self._task = RecognitionTask(info.key, request)
        self._task.signals.progress.connect(self._on_progress)
        self._task.signals.finished.connect(self._on_finished)
        self._task.signals.failed.connect(self._on_failed)
        self._refresh_buttons(running=True)
        QThreadPool.globalInstance().start(self._task)

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self.status.setText("Прерывание…")

    def _on_progress(self, fraction: float, note: str) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        if note:
            self.status.setText(note)

    def _on_finished(self, result: object) -> None:
        self._task = None
        self.progress.setValue(100)

        words = words_from(getattr(result, "segments", ()))
        if not words:
            self.status.setText(
                "Движок не вернул слова со временем — выравнивать не по чему. "
                "Проверьте, что звук в файле есть и выбран верный язык."
            )
            self._refresh_buttons(running=False)
            return

        self._plan = align(self._targets(), words)
        if self._plan.is_empty:
            self.status.setText("Реплик с текстом не нашлось.")
            self._refresh_buttons(running=False)
            return

        self._show_plan()
        self._refresh_buttons(running=False)

    def _show_plan(self) -> None:
        shown = self.preview.show_plan(
            self._plan, self._doc, only_shaky=self.only_shaky.isChecked()
        )
        text = self._plan.summary()
        if self._plan.skipped:
            text += f" · пропущено без текста: {len(self._plan.skipped)}"
        total = len(self._plan.shaky) if self.only_shaky.isChecked() else len(self._plan)
        if shown < total:
            text += f" · показаны первые {shown}"
        if self._plan.coverage < 0.5:
            text += (
                "\nОпор нашлось мало. Так бывает на шумном звуке, при неверном "
                "языке или если текст не совпадает с дорожкой."
            )
        self.status.setText(text)

    def _on_failed(self, message: str) -> None:
        self._task = None
        self.progress.setValue(0)
        self.status.setText(message)
        self._refresh_buttons(running=False)

    # -- результат ------------------------------------------------------------------ #

    def plan(self) -> AlignmentPlan:
        """Рассчитанный план — для вызывающего кода и тестов."""
        return self._plan

    def closeEvent(self, event) -> None:  # noqa: N802
        """Закрытие окна прерывает распознавание: фоновая задача ни к чему."""
        self._cancel()
        super().closeEvent(event)
