"""Загрузка модели в фоновом потоке.

Модель весит от 75 МБ до трёх гигабайт: качать её в главном потоке значит
заморозить окно на всё время загрузки. Отмена обязательна по той же причине —
передумать посреди трёхгигабайтной закачки пользователь вправе.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from sfstudio.app.i18n import tr
from sfstudio.services.asr import CancelToken, RecognitionCancelled, RecognitionError
from sfstudio.services.asr.models import download_model

__all__ = ["DownloadSignals", "DownloadTask", "EngineTask", "LibrariesTask"]


class DownloadSignals(QObject):
    progress = Signal(float, str)
    finished = Signal(str)   # путь к модели
    failed = Signal(str)


class DownloadTask(QRunnable):
    """Одна загрузка модели."""

    def __init__(self, model: str, models_dir: Path) -> None:
        super().__init__()
        self.signals = DownloadSignals()
        self._model = model
        self._dir = models_dir
        self._cancel = CancelToken()

    def cancel(self) -> None:
        self._cancel.cancel()

    @Slot()
    def run(self) -> None:
        try:
            path = download_model(
                self._model,
                self._dir,
                progress=lambda fraction, note="": self.signals.progress.emit(
                    float(fraction), str(note)
                ),
                cancel=self._cancel,
            )
        except RecognitionCancelled:
            self.signals.failed.emit(tr('Загрузка прервана.'))
        except RecognitionError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:
            # Сеть и hub бросают что угодно; необработанное исключение в
            # потоке пула убило бы его молча, и окно ждало бы вечно.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.finished.emit(str(path))


class LibrariesTask(QRunnable):
    """Загрузка библиотек для счёта на видеокарте.

    Отдельная задача, а не параметр к загрузке модели: источник другой (PyPI
    вместо HuggingFace), результат другой (набор файлов вместо каталога), а
    общего — только «качаем полгигабайта, не заморозив окно». Склеивать их
    ради этого значило бы получить одну задачу с двумя режимами.
    """

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.signals = DownloadSignals()
        self._dir = folder
        self._cancel = CancelToken()

    def cancel(self) -> None:
        self._cancel.cancel()

    @Slot()
    def run(self) -> None:
        from sfstudio.services.accel.libraries import download_cuda_libraries

        try:
            download_cuda_libraries(
                self._dir,
                progress=lambda fraction, note="": self.signals.progress.emit(
                    float(fraction), str(note)
                ),
                cancel=self._cancel,
            )
        except RecognitionCancelled:
            self.signals.failed.emit(tr('Загрузка прервана.'))
        except RecognitionError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.finished.emit(str(self._dir))


class EngineTask(QRunnable):
    """Загрузка готовой сборки whisper.cpp.

    Отдельная задача по той же причине, что и загрузка библиотек: источник,
    результат и способ проверки у неё свои, а общее с загрузкой модели —
    только «не заморозить окно».
    """

    def __init__(self, root: Path, build: str = "blas") -> None:
        super().__init__()
        self.signals = DownloadSignals()
        self._root = root
        self._build = build
        self._cancel = CancelToken()

    def cancel(self) -> None:
        self._cancel.cancel()

    @Slot()
    def run(self) -> None:
        from sfstudio.services.accel.binaries import download_whisper_cpp

        try:
            binary = download_whisper_cpp(
                self._root,
                self._build,
                progress=lambda fraction, note="": self.signals.progress.emit(
                    float(fraction), str(note)
                ),
                cancel=self._cancel,
            )
        except RecognitionCancelled:
            self.signals.failed.emit(tr('Загрузка прервана.'))
        except RecognitionError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.finished.emit(str(binary))
