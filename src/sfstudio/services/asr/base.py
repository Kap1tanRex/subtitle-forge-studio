"""Контракт распознавания речи.

Здесь только договор: что мы просим у движка и что он обязан вернуть. Ни один
конкретный движок отсюда не импортируется, и модуль поднимается на голом
интерпретаторе — без torch, без моделей, без сети.

**Движок — это Protocol, а не базовый класс.** Адаптеры оборачивают чужие
библиотеки (faster-whisper, whisper.cpp, что угодно ещё), у которых свой
жизненный цикл: одни держат модель в памяти между вызовами, другие запускают
внешний процесс. Наследование навязало бы им наш ``__init__`` и наши поля, а
структурная типизация не навязывает ничего — достаточно совпадения методов.

**Распознавание не даёт готовых субтитров.** Движок возвращает сегменты речи:
как правило, длинные фразы по несколько секунд, иногда целые абзацы. Резать их
до читаемых реплик — отдельная работа, общая для всех движков, и живёт она в
:mod:`sfstudio.services.asr.segmentation`. Смешивать это с адаптерами значило
бы писать одну и ту же логику в каждом.

**Отмена и прогресс — часть контракта, а не удобство.** Распознавание часовой
дорожки идёт минуты; движок, который нельзя прервать и который молчит до
конца, в интерактивной программе неприменим.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from sfstudio.app.i18n import tr

__all__ = [
    "CancelToken",
    "EngineInfo",
    "ProgressReporter",
    "RecognitionCancelled",
    "RecognitionError",
    "RecognitionRequest",
    "RecognitionResult",
    "Segment",
    "SpeechRecognizer",
    "WordTiming",
]


class RecognitionError(RuntimeError):
    """Распознавание не удалось.

    Сообщение адресовано пользователю: оно попадёт в диалог, поэтому должно
    объяснять, что делать, а не только что сломалось.
    """


class RecognitionCancelled(RecognitionError):  # noqa: N818 - не ошибка по смыслу
    """Пользователь прервал распознавание.

    Имя без суффикса ``Error`` намеренно: прерывание пользователем — не
    сбой, и называть его ошибкой значит вводить в заблуждение того, кто
    читает обработчик.
    """


class EngineKind(StrEnum):
    """Как движок устроен — от этого зависит, что ему нужно для работы."""

    LIBRARY = "library"      # библиотека Python в том же процессе
    EXTERNAL = "external"    # внешний исполняемый файл
    REMOTE = "remote"        # сетевая служба; локальной модели не требует


@dataclass(frozen=True, slots=True)
class EngineInfo:
    """Описание движка для списка выбора.

    ``available`` и ``hint`` заполняются **без** импорта тяжёлых зависимостей:
    построение списка не должно занимать секунды и тянуть в память torch
    только ради того, чтобы показать пункт в выпадающем списке.
    """

    key: str
    title: str
    description: str = ""
    kind: EngineKind = EngineKind.LIBRARY
    available: bool = False
    #: Что сделать, чтобы движок появился. Показывается, когда его нет.
    hint: str = ""
    #: Поддерживаемые размеры модели, от быстрых к точным.
    models: tuple[str, ...] = ()
    #: Умеет ли выдавать тайминги отдельных слов.
    word_timings: bool = False


@dataclass(frozen=True, slots=True)
class WordTiming:
    """Слово с собственными границами.

    Нужны для точной нарезки: разрезать фразу по паузе между словами гораздо
    лучше, чем делить её длительность пропорционально символам.
    """

    start: int
    end: int
    text: str
    probability: float = 1.0


@dataclass(slots=True)
class Segment:
    """Отрезок распознанной речи."""

    start: int          # мс
    end: int            # мс
    text: str
    #: Средняя уверенность 0..1. Ниже порога — повод показать реплику как
    #: требующую проверки, а не молча принять её.
    confidence: float = 1.0
    words: tuple[WordTiming, ...] = ()

    @property
    def duration(self) -> int:
        return max(0, self.end - self.start)

    def cleaned(self) -> str:
        """Текст без лишних пробелов по краям и внутри."""
        return " ".join(self.text.split())


@dataclass(slots=True)
class RecognitionRequest:
    """Что распознать и как."""

    media: Path
    #: Границы участка, мс. ``None`` в конце — до конца файла.
    start_ms: int = 0
    end_ms: int | None = None
    #: Код языка (``ru``, ``en``) либо ``None`` — определить самостоятельно.
    language: str | None = None
    #: Размер или имя модели. Что именно принимается, говорит EngineInfo.models.
    model: str = ""
    #: Каталог, где лежат (или куда скачиваются) модели.
    models_dir: Path | None = None
    #: Индекс звуковой дорожки в контейнере.
    audio_stream: int = 0
    #: Просить ли тайминги слов, если движок умеет.
    word_timings: bool = True
    #: Дополнительные параметры конкретного движка. Намеренно свободные:
    #: втискивать особенности каждого движка в общий контракт — значит
    #: раздувать его до объединения всех возможных.
    options: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RecognitionResult:
    """Что вернул движок."""

    segments: list[Segment] = field(default_factory=list)
    #: Определённый язык, если движок его сообщил.
    language: str | None = None
    #: Какой движок и какая модель — попадёт в журнал и в отчёт пользователю.
    engine: str = ""
    model: str = ""
    #: Сколько заняло, секунды. Полезно для выбора модели в следующий раз.
    elapsed_s: float = 0.0

    def __len__(self) -> int:
        return len(self.segments)

    @property
    def is_empty(self) -> bool:
        return not self.segments


class CancelToken:
    """Флаг отмены, общий для вызывающего кода и движка.

    Отдельный объект, а не ``threading.Event``: движку нужен только вопрос
    «пора ли остановиться», и давать ему полноценный примитив синхронизации,
    которым можно случайно заблокировать поток, незачем.
    """

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise RecognitionCancelled(tr('распознавание прервано'))


class ProgressReporter(Protocol):
    """Куда движок сообщает о ходе работы."""

    def __call__(self, fraction: float, note: str = "") -> None:
        """``fraction`` — 0..1. ``note`` — что происходит сейчас."""
        ...


@runtime_checkable
class SpeechRecognizer(Protocol):
    """Движок распознавания речи."""

    #: Постоянный ключ. По нему движок запоминается в настройках, поэтому
    #: менять его нельзя — иначе выбор пользователя потеряется.
    key: str

    def info(self) -> EngineInfo:
        """Описание для списка выбора. Быстрое: без загрузки модели."""
        ...

    def transcribe(
        self,
        request: RecognitionRequest,
        progress: ProgressReporter | None = None,
        cancel: CancelToken | None = None,
    ) -> RecognitionResult:
        """Распознаёт речь.

        Обязана уважать ``cancel`` между кусками работы и бросать
        :class:`RecognitionCancelled`, а не возвращать половину результата
        молча: вызывающий должен отличать «прервано» от «речи не найдено».
        """
        ...
