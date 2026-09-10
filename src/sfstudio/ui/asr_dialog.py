"""Распознавание речи: выбор движка и параметров, ход работы, вставка реплик.

Окно устроено вокруг того, что распознавание — **долгая операция с
неопределённым результатом**. Отсюда три решения:

**Реплики не вставляются молча.** Сначала показывается, сколько их получилось
и как выглядят первые из них. Расшифровка бывает и мусорной — на плохом звуке,
с неверно выбранным языком, — и обнаруживать это после того, как в документ
уже добавлено четыреста строк, поздно.

**Отмена доступна всегда.** Час звука на медленной модели распознаётся
десятки минут; окно, которое нельзя закрыть, в это время равносильно
зависшей программе.

**Недоступные движки видны и объяснены.** Прятать их — значит оставить
пользователя с пустым списком и без понимания, что делать. Каждый показан с
подсказкой, как его получить.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.app.storage import libraries_dir, models_dir
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import format_srt
from sfstudio.services.accel import detect_accelerators
from sfstudio.services.asr import (
    EngineInfo,
    RecognitionRequest,
    RecognitionResult,
    SegmentationRules,
    engine_infos,
    to_events,
)
from sfstudio.services.asr.models import MODEL_CATALOG, installed_models
from sfstudio.ui.combo import select_data
from sfstudio.ui.theme import repolish

__all__ = ["AsrDialog"]

#: Языки, которые чаще всего нужны. Список не исчерпывающий намеренно:
#: «Определить» покрывает остальные, а длинный перечень только мешает.
LANGUAGES: tuple[tuple[str, str | None], ...] = (
    (tr('Определить автоматически'), None),
    (tr('Русский'), "ru"),
    (tr('Английский'), "en"),
    (tr('Немецкий'), "de"),
    (tr('Французский'), "fr"),
    (tr('Испанский'), "es"),
    (tr('Японский'), "ja"),
    (tr('Китайский'), "zh"),
)


class AsrDialog(QDialog):
    """Окно распознавания речи."""

    #: Готовые реплики приняты пользователем.
    events_ready = Signal(object)

    def __init__(
        self,
        doc: SubtitleDocument,
        media: Path | None,
        settings,
        parent: QWidget | None = None,
        *,
        selection: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Распознавание речи'))
        self.resize(640, 620)
        self._doc = doc
        self._media = media
        self._settings = settings
        self._selection = selection
        self._task = None
        self._download = None
        # Список устройств снимается один раз на открытие окна: он не
        # меняется, пока окно открыто, а чтение реестра и проверка библиотек
        # повторяются при каждой перерисовке подсказки.
        self._accelerators = detect_accelerators(
            libraries_dir=libraries_dir(settings)
        )
        self._result: RecognitionResult | None = None
        self._prepared: list = []

        layout = QVBoxLayout(self)
        layout.addWidget(self._engine_group())
        layout.addWidget(self._scope_group())
        layout.addWidget(self._progress_group(), 1)

        self.buttons = QDialogButtonBox()
        self.run_button = self.buttons.addButton(tr('Распознать'), QDialogButtonBox.ActionRole)
        self.run_button.clicked.connect(self._start)
        self.cancel_button = self.buttons.addButton(tr('Прервать'), QDialogButtonBox.ActionRole)
        self.cancel_button.clicked.connect(self._cancel)
        self.insert_button = self.buttons.addButton(tr('Вставить реплики'),
                                                    QDialogButtonBox.AcceptRole)
        self.insert_button.clicked.connect(self._insert)
        close_button = self.buttons.addButton(tr('Закрыть'), QDialogButtonBox.RejectRole)
        close_button.clicked.connect(self.reject)
        layout.addWidget(self.buttons)

        self._load_settings()
        self._refresh_engine()
        self._refresh_buttons(running=False)
        self._refresh_hint()

    # -- построение --------------------------------------------------------------- #

    def _engine_group(self) -> QWidget:
        box = QGroupBox(tr('Движок'))
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.engine_box = QComboBox()
        self._infos: list[EngineInfo] = engine_infos()
        for info in self._infos:
            suffix = "" if info.available else tr('  — не установлен')
            self.engine_box.addItem(f"{info.title}{suffix}", info.key)
        self.engine_box.currentIndexChanged.connect(self._refresh_engine)
        form.addRow(tr('Движок'), self.engine_box)

        self.engine_note = QLabel("")
        self.engine_note.setWordWrap(True)
        self.engine_note.setProperty("role", "hint")
        form.addRow("", self.engine_note)

        model_row = QHBoxLayout()
        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.setToolTip(
            tr('Крупные модели точнее, но требуют больше памяти и времени')
        )
        self.model_box.currentTextChanged.connect(self._refresh_model_note)
        model_row.addWidget(self.model_box, 1)

        self.download_button = QPushButton(tr('Скачать'))
        self.download_button.setToolTip(
            tr('Скачать выбранную модель в указанный каталог')
        )
        self.download_button.clicked.connect(self._download_model)
        model_row.addWidget(self.download_button)
        form.addRow(tr('Модель'), model_row)

        # Кнопка появляется, только когда выбран whisper.cpp и его нет:
        # у остальных движков ставить нечего.
        self.engine_setup_button = QPushButton(tr('Установить whisper.cpp'))
        self.engine_setup_button.setToolTip(
            tr('Скачать готовую сборку whisper.cpp (20 МБ) в папку загрузок')
        )
        self.engine_setup_button.clicked.connect(self._download_engine)
        self.engine_setup_button.hide()
        form.addRow("", self.engine_setup_button)

        self.model_note = QLabel("")
        self.model_note.setWordWrap(True)
        self.model_note.setProperty("role", "hint")
        form.addRow("", self.model_note)

        self.language_box = QComboBox()
        for title, code in LANGUAGES:
            self.language_box.addItem(title, code)
        # Заметка о модели зависит и от языка: одноязычной модели русская речь
        # не по силам, и сказать об этом надо до запуска, а не после.
        self.language_box.currentIndexChanged.connect(self._refresh_model_note)
        form.addRow(tr('Язык'), self.language_box)

        device_row = QHBoxLayout()
        self.device_box = QComboBox()
        self.device_box.addItem(tr('Определить автоматически'), "")
        # Остальное — то, что действительно нашлось на этой машине, с
        # названиями карт. «Видеокарта (CUDA)» в списке у человека без
        # видеокарты — обещание, которое программа не выполнит.
        for accel in self._accelerators:
            self.device_box.addItem(accel.caption, accel.key)
        self.device_box.setToolTip(
            tr('Видеокарта не всегда быстрее: модель нужно загрузить в её память, и на '
                   'коротких отрезках это съедает выигрыш')
        )
        self.device_box.currentIndexChanged.connect(self._refresh_device_note)
        device_row.addWidget(self.device_box, 1)

        self.libraries_button = QPushButton(tr('Включить видеокарту'))
        self.libraries_button.setToolTip(
            tr('Скачать библиотеку cuBLAS (около 0,7 ГБ) в папку загрузок')
        )
        self.libraries_button.clicked.connect(self._download_libraries)
        device_row.addWidget(self.libraries_button)
        form.addRow(tr('Считать на'), device_row)

        self.device_note = QLabel("")
        self.device_note.setWordWrap(True)
        self.device_note.setProperty("role", "hint")
        form.addRow("", self.device_note)

        models_row = QHBoxLayout()
        self.models_dir_edit = QLineEdit()
        # В подсказке — настоящий путь из настроек, а не общие слова: пустое
        # поле не значит «некуда качать», значит «туда, куда сказано в
        # настройках», и это стоит показать.
        self.models_dir_edit.setPlaceholderText(str(models_dir(self._settings)))
        self.models_dir_edit.textChanged.connect(self._refresh_model_note)
        models_row.addWidget(self.models_dir_edit, 1)
        browse = QPushButton(tr('Обзор…'))
        browse.clicked.connect(self._choose_models_dir)
        models_row.addWidget(browse)
        form.addRow(tr('Модели'), models_row)
        return box

    def _scope_group(self) -> QWidget:
        box = QGroupBox(tr('Что распознавать'))
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.media_label = QLabel(self._media.name if self._media else tr('видео не открыто'))
        form.addRow(tr('Файл'), self.media_label)

        self.selection_check = QCheckBox(tr('Только выделенный участок'))
        self.selection_check.setEnabled(self._selection is not None)
        if self._selection is not None:
            start, end = self._selection
            self.selection_check.setText(
                tr('Только участок {0} — {1}').format(format_srt(start), format_srt(end))
            )
        form.addRow("", self.selection_check)

        self.replace_check = QCheckBox(tr('Заменить существующие реплики'))
        self.replace_check.setToolTip(
            tr('Иначе распознанное добавится к тому, что уже есть в документе')
        )
        form.addRow("", self.replace_check)

        self.wrap_check = QCheckBox(tr('Переносить длинные строки'))
        self.wrap_check.setChecked(True)
        form.addRow("", self.wrap_check)
        return box

    def _progress_group(self) -> QWidget:
        box = QGroupBox(tr('Ход работы'))
        layout = QVBoxLayout(box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status = QLabel(tr('Готово к запуску.'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(
            tr('Здесь появится образец распознанного текста — до того, как он попадёт в документ.')
        )
        layout.addWidget(self.preview, 1)
        return box

    # -- настройки ----------------------------------------------------------------- #

    def _load_settings(self) -> None:
        get = self._settings.get
        select_data(self.engine_box, str(get("asr.engine", "") or ""))
        self.models_dir_edit.setText(str(get("asr.models_dir", "") or ""))
        select_data(self.language_box, get("asr.language", None))
        select_data(self.device_box, str(get("asr.device", "") or ""))
        self._refresh_device_note()

    def _save_settings(self) -> None:
        self._settings.set("asr.engine", self.engine_box.currentData())
        self._settings.set("asr.model", self.model_box.currentText())
        self._settings.set("asr.language", self.language_box.currentData())
        self._settings.set("asr.models_dir", self.models_dir_edit.text().strip())
        self._settings.set("asr.device", self.device_box.currentData())
        self._settings.save()

    def _choose_models_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, tr('Каталог моделей'), self.models_dir_edit.text() or str(Path.home())
        )
        if chosen:
            self.models_dir_edit.setText(chosen)

    def _current_info(self) -> EngineInfo | None:
        key = self.engine_box.currentData()
        return next((i for i in self._infos if i.key == key), None)

    def _refresh_hint(self) -> None:
        """Подсказка о готовности. Зовётся отдельно от обновления кнопок.

        Раньше текст выставлялся прямо в ``_refresh_buttons``, и вызов после
        неудачи затирал сообщение об ошибке общей фразой «движок не
        установлен» — то есть скрывал именно то, что нужно было прочитать.
        """
        info = self._current_info()
        if self._media is None:
            self.status.setText(tr('Сначала откройте видео или аудио.'))
        elif info is not None and not info.available:
            self.status.setText(tr('Выбранный движок не установлен.'))
        else:
            self.status.setText(tr('Готово к запуску.'))

    def _refresh_engine(self) -> None:
        info = self._current_info()
        if info is None:
            return

        self.model_box.clear()
        self.model_box.addItems(list(info.models))
        saved = str(self._settings.get("asr.model", "") or "")
        if saved:
            self.model_box.setCurrentText(saved)
        self._refresh_model_note()

        if info.available:
            self.engine_note.setText(info.description)
        else:
            # Подсказка вместо описания: сейчас важнее, как его получить.
            self.engine_note.setText(f"{info.description}\n\n{info.hint}")

        # whisper.cpp — единственный движок, который программа умеет
        # доставить сама: это один архив, а не пакеты Python.
        self.engine_setup_button.setVisible(
            info.key == "whisper-cpp" and not info.available
        )
        self._refresh_buttons(running=False)

    def _download_engine(self) -> None:
        """Скачивает готовую сборку whisper.cpp в папку загрузок."""
        from sfstudio.app.storage import data_root
        from sfstudio.services.accel.binaries import WHISPER_CPP_BUILDS

        build = next(b for b in WHISPER_CPP_BUILDS if b.key == "blas")
        folder = data_root(self._settings)
        answer = QMessageBox.question(
            self,
            tr('Установить whisper.cpp'),
            f"Скачать сборку «{build.title}» ({build.size_mb} МБ) в папку:\n"
            f"{folder}\n\nЭто отдельная программа распознавания: один файл, "
            "без пакетов Python. Модель к ней качается отдельно.\n\nСкачивать?",
        )
        if answer != QMessageBox.Yes:
            return

        from sfstudio.ui.asr_download_task import EngineTask

        self._download = EngineTask(folder, build.key)
        self._download.signals.progress.connect(self._on_progress)
        self._download.signals.finished.connect(self._on_engine_ready)
        self._download.signals.failed.connect(self._on_failed)
        self.engine_setup_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status.setText(tr('Загрузка whisper.cpp…'))
        QThreadPool.globalInstance().start(self._download)

    def _on_engine_ready(self, path: str) -> None:
        self._download = None
        self.engine_setup_button.setEnabled(True)
        self.status.setText(tr('whisper.cpp установлен: {0}').format(path))
        # Список движков перечитывается: доступность считается по факту
        # наличия файла, и после загрузки она уже другая.
        self._infos = engine_infos()
        self._refresh_engine()

    def _refresh_device_note(self) -> None:
        """Сообщает, что доступно на самом деле и чего не хватает.

        Обещать видеокарту, которой нет или для которой не хватает библиотек,
        нельзя: пользователь выберет её и получит откат на процессор без
        объяснений — ровно то, что выглядит как зависший на десяти процентах
        прогресс.
        """
        chosen = str(self.device_box.currentData() or "")
        accel = next((a for a in self._accelerators if a.key == chosen), None)

        if accel is not None:
            devices = ", ".join(accel.devices)
            self.device_note.setText(
                f"{accel.note}\nНайдено: {devices}" if devices else accel.note
            )
        else:
            from sfstudio.services.asr.engines.faster_whisper import pick_device

            detected = pick_device()
            self.device_note.setText(
                tr('Выбрано: ') + (tr('видеокарта') if detected == "cuda" else tr('процессор'))
            )

        # Кнопка загрузки показывается ровно тогда, когда есть что чинить:
        # видеокарта NVIDIA найдена, а библиотек к ней нет.
        cuda = next((a for a in self._accelerators if a.key == "cuda"), None)
        self.libraries_button.setVisible(cuda is not None and not cuda.available)

    def _download_libraries(self) -> None:
        """Качает cuBLAS в папку загрузок — по явному согласию на объём."""
        folder = libraries_dir(self._settings)
        answer = QMessageBox.question(
            self,
            tr('Включить видеокарту'),
            f"Скачать библиотеку cuBLAS (около 0,7 ГБ) в папку:\n{folder}\n\n"
            "Без неё распознавание считается на процессоре — в 2–3 раза "
            "медленнее. Папку можно будет удалить в любой момент.\n\n"
            "Скачивать?",
        )
        if answer != QMessageBox.Yes:
            return

        from sfstudio.ui.asr_download_task import LibrariesTask

        self._download = LibrariesTask(folder)
        self._download.signals.progress.connect(self._on_progress)
        self._download.signals.finished.connect(self._on_libraries_ready)
        self._download.signals.failed.connect(self._on_failed)
        self.libraries_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status.setText(tr('Загрузка библиотек для видеокарты…'))
        QThreadPool.globalInstance().start(self._download)

    def _on_libraries_ready(self, folder: str) -> None:
        """Библиотеки на месте — пересобираем список устройств."""
        self._download = None
        self.libraries_button.setEnabled(True)
        self._accelerators = detect_accelerators(
            libraries_dir=libraries_dir(self._settings)
        )
        cuda = next((a for a in self._accelerators if a.key == "cuda"), None)
        if cuda is not None and cuda.available:
            index = self.device_box.findData("cuda")
            if index >= 0:
                self.device_box.setCurrentIndex(index)
            self.status.setText(tr('Видеокарта включена. Библиотеки: {0}').format(folder))
        else:
            self.status.setText(
                tr('Библиотеки скачаны, но видеокарта всё ещё недоступна. Перезапустите '
                       'программу — библиотеки подхватываются при старте.')
            )
        self._refresh_device_note()
        self._refresh_buttons(running=False)

    def _models_dir(self) -> Path | None:
        """Куда качать модели: поле окна, иначе путь из настроек.

        Пустое поле — не «никуда»: у программы есть каталог загрузок, и
        требовать заполнить его ещё раз здесь значило бы спрашивать дважды.
        """
        text = self.models_dir_edit.text().strip()
        if text:
            return Path(text)
        try:
            return models_dir(self._settings)
        except (OSError, ValueError):
            return None

    def _refresh_model_note(self) -> None:
        """Что за модель выбрана, сколько весит, скачана ли и тот ли язык."""
        name = self.model_box.currentText().strip()
        info = next((m for m in MODEL_CATALOG if m.name == name), None)
        if info is None:
            self.model_note.setText("")
            self.model_note.setProperty("role", "hint")
            repolish(self.model_note)
            self.download_button.setEnabled(False)
            return

        have = name in installed_models(self._models_dir())
        status = tr('уже скачана') if have else tr('примерно {0} МБ').format(info.size_mb)

        # Несовпадение языка важнее веса и важнее примечания: модель молча
        # выдаст текст на своём языке, и понять это можно будет только по
        # готовым субтитрам — после всего ожидания.
        language = self.language_box.currentData()
        mismatch = not info.supports(language)
        if mismatch:
            allowed = ", ".join(info.languages or ())
            chosen = self.language_box.currentText().lower()
            self.model_note.setText(
                tr('Эта модель знает только: {0}. Выбран язык «{1}» — субтитры получатся на '
                       'английском. Возьмите medium или large-v3. '
                           '({2})').format(allowed, chosen, status)
            )
        else:
            self.model_note.setText(f"{info.note} ({status})")
        self.model_note.setProperty("role", "warning" if mismatch else "hint")
        repolish(self.model_note)
        # Скачивать заново незачем, но и запрещать не будем: файл мог побиться.
        self.download_button.setText(tr('Скачать заново') if have else tr('Скачать'))
        self.download_button.setEnabled(bool(self._models_dir()))

    def _download_model(self) -> None:
        models_dir = self._models_dir()
        if models_dir is None:
            QMessageBox.information(
                self, tr('Каталог моделей'),
                tr('Сначала укажите каталог, куда скачивать модели.'),
            )
            return

        name = self.model_box.currentText().strip()
        from sfstudio.ui.asr_download_task import DownloadTask

        self._download = DownloadTask(name, models_dir)
        self._download.signals.progress.connect(self._on_progress)
        self._download.signals.finished.connect(self._on_downloaded)
        self._download.signals.failed.connect(self._on_failed)
        self.download_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status.setText(tr('Загрузка модели {0}…').format(name))
        QThreadPool.globalInstance().start(self._download)

    def _on_downloaded(self, path: str) -> None:
        self._download = None
        self.progress.setValue(100)
        self.status.setText(tr('Модель загружена: {0}').format(path))
        self._refresh_model_note()
        self._refresh_buttons(running=False)

    def _refresh_buttons(self, *, running: bool) -> None:
        info = self._current_info()
        can_run = (
            not running
            and info is not None
            and info.available
            and self._media is not None
        )
        self.run_button.setEnabled(can_run)
        self.cancel_button.setEnabled(running)
        self.insert_button.setEnabled(not running and bool(self._prepared))
        self.engine_box.setEnabled(not running)

    # -- запуск -------------------------------------------------------------------- #

    def _start(self) -> None:
        info = self._current_info()
        if info is None or self._media is None:
            return

        self._save_settings()
        self._prepared = []
        self._result = None
        self.preview.clear()
        self.progress.setValue(0)
        self.status.setText(tr('Запуск…'))

        start_ms, end_ms = 0, None
        if self.selection_check.isChecked() and self._selection is not None:
            start_ms, end_ms = self._selection

        models_dir = self.models_dir_edit.text().strip()
        options: dict[str, object] = {}
        device = str(self.device_box.currentData() or "")
        if device:
            options["device"] = device
        request = RecognitionRequest(
            media=self._media,
            start_ms=start_ms,
            end_ms=end_ms,
            language=self.language_box.currentData(),
            model=self.model_box.currentText().strip(),
            models_dir=Path(models_dir) if models_dir else None,
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
        for task in (self._task, self._download):
            if task is not None:
                task.cancel()
        if self._task is not None or self._download is not None:
            self.status.setText(tr('Прерывание…'))

    # -- события задачи ------------------------------------------------------------- #

    def _on_progress(self, fraction: float, note: str) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        if note:
            self.status.setText(note)

    def _on_finished(self, result: object) -> None:
        self._task = None
        self._result = result  # type: ignore[assignment]
        assert isinstance(result, RecognitionResult)

        rules = SegmentationRules.from_qc_profile(self._profile())
        self._prepared = to_events(
            result.segments, rules, wrap=self.wrap_check.isChecked()
        )

        self.progress.setValue(100)
        if not self._prepared:
            self.status.setText(tr('Речь не распознана. Проверьте звук и выбор языка.'))
            self._refresh_buttons(running=False)
            return

        language = tr(', язык: {0}').format(result.language) if result.language else ""
        self.status.setText(
            tr('Готово: {0} реплик из {1} фрагментов за '
                '{2:.0f} '
                   'с{3}.').format(
                       len(self._prepared), len(result.segments), result.elapsed_s, language)
        )
        self.preview.setPlainText(self._preview_text())
        self._refresh_buttons(running=False)

    def _preview_text(self) -> str:
        """Образец: первые реплики целиком, дальше — многоточие.

        Показать всё нельзя (их бывают сотни), а не показать ничего — значит
        предложить принять вслепую.
        """
        lines = [
            f"{format_srt(item.start)} → {format_srt(item.end)}\n"
            f"{item.text.replace(chr(92) + 'N', chr(10))}"
            for item in self._prepared[:12]
        ]
        if len(self._prepared) > 12:
            lines.append(tr('… и ещё {0}').format(len(self._prepared) - 12))
        return "\n\n".join(lines)

    def _on_failed(self, message: str) -> None:
        self._task = None
        self._download = None
        self._refresh_model_note()
        self.progress.setValue(0)
        self.status.setText(message)
        self._refresh_buttons(running=False)

    def _profile(self):
        from sfstudio.services.qc import PROFILES, default_profile

        return PROFILES.get(
            str(self._settings.get("qc.profile", "general")), default_profile()
        )

    # -- результат ------------------------------------------------------------------ #

    def _insert(self) -> None:
        if not self._prepared:
            return
        if self.replace_check.isChecked() and len(self._doc):
            answer = QMessageBox.question(
                self,
                tr('Заменить '
                       'реплики'),
                tr('Удалить {0} существующих реплик и вставить {1} '
                       'новых?').format(len(self._doc), len(self._prepared)),
            )
            if answer != QMessageBox.Yes:
                return

        self.events_ready.emit(
            {"segments": self._prepared, "replace": self.replace_check.isChecked()}
        )
        self.accept()

    def prepared_segments(self) -> list:
        """Подготовленные реплики — для вызывающего кода и тестов."""
        return list(self._prepared)

    def closeEvent(self, event) -> None:  # noqa: N802
        """Закрытие окна прерывает работу: оставлять задачу в фоне нечестно."""
        self._cancel()
        super().closeEvent(event)
