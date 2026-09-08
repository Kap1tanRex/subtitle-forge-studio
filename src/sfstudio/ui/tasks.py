"""Фоновые задачи: извлечение пиков и индекса ключевых кадров.

Слой ``media`` намеренно не знает про Qt — там чистые функции, которые
тестируются без ``QApplication``. Qt-обвязка живёт здесь.

Правила, которым подчиняются все задачи (§18 спецификации):

* документ мутируется только в главном потоке; воркеры отдают результат
  сигналом и ничего не трогают сами;
* каждая задача отменяема через ``threading.Event``;
* у задачи есть **токен поколения** — если пользователь успел открыть другой
  файл, результат устаревшей задачи отбрасывается по несовпадению токена.
  Без этого волна от предыдущего фильма могла бы приехать поверх нового.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from sfstudio.media.keyframes import KeyframeIndex, build_keyframe_index
from sfstudio.media.peaks import NoAudioError, PeakData, PeaksError, cache_key, extract_peaks

__all__ = ["KeyframeTask", "PeaksTask", "TaskSignals", "cache_dir"]


def cache_dir() -> Path:
    """Каталог кэша приложения. Создаётся при первом обращении."""
    import os

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    path = root / "SubtitleForge" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


class TaskSignals(QObject):
    """Сигналы задачи.

    Отдельный ``QObject``, потому что ``QRunnable`` не наследует ``QObject``
    и своих сигналов иметь не может.
    """

    progress = Signal(int, float)  # токен, 0..1
    finished = Signal(int, object)  # токен, результат
    failed = Signal(int, str)  # токен, сообщение
    no_audio = Signal(int)  # токен


@dataclass(slots=True)
class _Base:
    media: Path
    token: int


class PeaksTask(QRunnable):
    """Строит пики, переиспользуя кэш, если он актуален."""

    def __init__(self, media: Path, token: int, *, stream_index: int = 0) -> None:
        super().__init__()
        self.media = media
        self.token = token
        self.stream_index = stream_index
        self.signals = TaskSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            target = cache_dir() / f"{cache_key(self.media, self.stream_index)}.peaks"

            if target.exists():
                try:
                    data = PeakData.open(target)
                except PeaksError:
                    # Кэш от старой версии или повреждён — перестраиваем.
                    # Молча падать здесь нельзя: волна нужна пользователю
                    # больше, чем сохранность устаревшего файла.
                    target.unlink(missing_ok=True)
                else:
                    self.signals.progress.emit(self.token, 1.0)
                    self.signals.finished.emit(self.token, data)
                    return

            extract_peaks(
                self.media,
                target,
                stream_index=self.stream_index,
                progress=lambda f: self.signals.progress.emit(self.token, f),
                cancel=self._cancel,
            )
            if self._cancel.is_set():
                return
            self.signals.finished.emit(self.token, PeakData.open(target))

        except NoAudioError:
            self.signals.no_audio.emit(self.token)
        except Exception as exc:
            if not self._cancel.is_set():
                self.signals.failed.emit(self.token, f"{type(exc).__name__}: {exc}")


class KeyframeTask(QRunnable):
    """Строит индекс ключевых кадров демуксом."""

    def __init__(self, media: Path, token: int) -> None:
        super().__init__()
        self.media = media
        self.token = token
        self.signals = TaskSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            cached = cache_dir() / f"{cache_key(self.media)}.kfidx"
            if cached.exists():
                try:
                    self.signals.finished.emit(self.token, KeyframeIndex.load(cached))
                    return
                except Exception:
                    cached.unlink(missing_ok=True)

            index = build_keyframe_index(self.media, cancel=self._cancel)
            if self._cancel.is_set():
                return
            # Кэш не обязателен: индекс уже построен и будет отдан в любом случае.
            with contextlib.suppress(OSError):
                index.save(cached)
            self.signals.finished.emit(self.token, index)
        except Exception as exc:
            if not self._cancel.is_set():
                self.signals.failed.emit(self.token, f"{type(exc).__name__}: {exc}")
