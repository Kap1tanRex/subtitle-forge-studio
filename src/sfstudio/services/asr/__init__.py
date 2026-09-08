"""Распознавание речи: точка расширения и её штатные обитатели.

Пользоваться этим слоем **не обязательно**. Субтитры можно писать руками от
начала до конца; можно поставить один движок из трёх; можно подключить свой,
которого здесь нет. Поэтому ни один движок не входит в поставку, а отсутствие
всех — обычное рабочее состояние, а не поломка.

Как добавить свой движок::

    from sfstudio.services.asr import register

    class MyEngine:
        key = "my-engine"

        def info(self): ...
        def transcribe(self, request, progress=None, cancel=None): ...

    register(MyEngine.key, MyEngine)

Больше ничего менять не нужно: диалог и главное окно работают со списком из
реестра, а не с именами конкретных движков.

Устройство слоя:

* :mod:`~sfstudio.services.asr.base` — контракт: запрос, сегменты, отмена;
* :mod:`~sfstudio.services.asr.registry` — реестр и проверки доступности;
* :mod:`~sfstudio.services.asr.audio` — подготовка звука 16 кГц моно;
* :mod:`~sfstudio.services.asr.segmentation` — нарезка сегментов на реплики;
* :mod:`~sfstudio.services.asr.engines` — адаптеры к конкретным движкам.
"""

from __future__ import annotations

from sfstudio.services.asr.base import (
    CancelToken,
    EngineInfo,
    EngineKind,
    ProgressReporter,
    RecognitionCancelled,
    RecognitionError,
    RecognitionRequest,
    RecognitionResult,
    Segment,
    SpeechRecognizer,
    WordTiming,
)
from sfstudio.services.asr.registry import (
    available_engines,
    engine_infos,
    get_engine,
    has_any_engine,
    register,
    registered_keys,
)
from sfstudio.services.asr.segmentation import SegmentationRules, to_events

__all__ = [
    "CancelToken",
    "EngineInfo",
    "EngineKind",
    "ProgressReporter",
    "RecognitionCancelled",
    "RecognitionError",
    "RecognitionRequest",
    "RecognitionResult",
    "Segment",
    "SegmentationRules",
    "SpeechRecognizer",
    "WordTiming",
    "available_engines",
    "engine_infos",
    "get_engine",
    "has_any_engine",
    "register",
    "registered_keys",
    "to_events",
]


def _downloads():
    """Каталог загрузок из реестра.

    Читается в момент создания движка, а не регистрации: на момент
    регистрации настройки ещё не прочитаны, и запомнить было бы нечего.
    """
    from sfstudio.services.asr.registry import downloads_dir

    return downloads_dir()


def _register_builtin() -> None:
    """Ставит на учёт штатные адаптеры.

    Импортируются сами адаптеры, но **не** библиотеки, которые они оборачивают:
    внутри адаптеров тяжёлые импорты отложены до вызова ``transcribe``.
    Поэтому регистрация стоит доли миллисекунды даже без единой установленной
    модели.
    """
    from sfstudio.services.asr.engines.faster_whisper import FasterWhisperEngine
    from sfstudio.services.asr.engines.openai_whisper import OpenAiWhisperEngine
    from sfstudio.services.asr.engines.whisper_cpp import WhisperCppEngine

    register(FasterWhisperEngine.key, FasterWhisperEngine)
    register(OpenAiWhisperEngine.key, OpenAiWhisperEngine)
    # whisper.cpp — единственный, кому нужен каталог загрузок: остальные
    # движки берут модели через huggingface_hub, а он ищет файлы на диске.
    register(
        WhisperCppEngine.key,
        lambda: WhisperCppEngine(downloads=_downloads()),
    )


def _downloads():
    """Каталог загрузок из реестра. Отдельной функцией, чтобы он читался в
    момент создания движка, а не в момент регистрации: настройки к тому
    времени ещё не прочитаны."""
    from sfstudio.services.asr.registry import downloads_dir

    return downloads_dir()


_register_builtin()
