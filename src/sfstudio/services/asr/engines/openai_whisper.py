"""Адаптер оригинального ``openai-whisper``.

Эталонная реализация: медленнее faster-whisper и тянет torch (около двух
гигабайт), но её ставят чаще всего, и у неё бывает уже загруженная модель.
Раз пакет установлен — не пользоваться им из-за того, что есть вариант лучше,
неразумно.

Прогресс здесь **грубый**. Библиотека не сообщает о ходе работы и возвращает
всё разом, поэтому честно показать проценты нельзя: вместо выдуманной шкалы
показывается один шаг «распознавание» на всё время. Врать индикатором хуже,
чем не показывать деления.
"""

from __future__ import annotations

import time

from sfstudio.app.i18n import tr
from sfstudio.services.asr.audio import extract_audio
from sfstudio.services.asr.base import (
    CancelToken,
    EngineInfo,
    EngineKind,
    ProgressReporter,
    RecognitionError,
    RecognitionRequest,
    RecognitionResult,
    Segment,
    WordTiming,
)
from sfstudio.services.asr.registry import module_installed

__all__ = ["OpenAiWhisperEngine"]

MODULE = "whisper"
MODELS = ("tiny", "base", "small", "medium", "large")
DEFAULT_MODEL = "small"


class OpenAiWhisperEngine:
    """Распознавание через openai-whisper."""

    key = "openai-whisper"

    def info(self) -> EngineInfo:
        return EngineInfo(
            key=self.key,
            title=tr('Whisper (оригинальный)'),
            description=(
                tr('Эталонная реализация OpenAI. Работает медленнее, чем faster-whisper, и '
                       'требует PyTorch.')
            ),
            kind=EngineKind.LIBRARY,
            available=module_installed(MODULE),
            hint=tr('Установите: pip install openai-whisper'),
            models=MODELS,
            word_timings=True,
        )

    def transcribe(
        self,
        request: RecognitionRequest,
        progress: ProgressReporter | None = None,
        cancel: CancelToken | None = None,
    ) -> RecognitionResult:
        started = time.monotonic()
        try:
            import whisper
        except ImportError as exc:
            raise RecognitionError(
                tr('openai-whisper не установлен. Установите его командой pip install '
                       'openai-whisper либо выберите другой движок.')
            ) from exc

        if progress is not None:
            progress(0.02, tr('загрузка модели'))
        name = request.model or DEFAULT_MODEL
        try:
            model = whisper.load_model(
                name,
                download_root=str(request.models_dir) if request.models_dir else None,
            )
        except Exception as exc:
            raise RecognitionError(tr('не удалось загрузить модель «{0}»: {1}').format(
                name, exc)) from exc

        if progress is not None:
            progress(0.1, tr('чтение звука'))
        chunk = extract_audio(
            request.media,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            stream_index=request.audio_stream,
            cancel=cancel,
        )
        if len(chunk.samples) == 0:  # type: ignore[arg-type]
            return RecognitionResult(engine=self.key, model=name)

        if cancel is not None:
            cancel.raise_if_cancelled()
        if progress is not None:
            # Точку прогресса дальше двигать нечем: библиотека вернёт всё
            # сразу. Показываем стадию, а не выдуманные проценты.
            progress(0.2, tr('распознавание (без индикации)'))

        try:
            raw = model.transcribe(
                chunk.samples,
                language=request.language,
                word_timestamps=request.word_timings,
                verbose=False,
            )
        except Exception as exc:
            raise RecognitionError(tr('whisper не справился: {0}').format(exc)) from exc

        if cancel is not None:
            cancel.raise_if_cancelled()

        segments = [
            _convert(item, chunk.offset_ms) for item in raw.get("segments", [])
        ]
        if progress is not None:
            progress(1.0, tr('готово'))

        return RecognitionResult(
            segments=segments,
            language=raw.get("language"),
            engine=self.key,
            model=name,
            elapsed_s=time.monotonic() - started,
        )


def _convert(item: dict, offset_ms: int) -> Segment:
    words = tuple(
        WordTiming(
            start=offset_ms + int(float(word.get("start", 0)) * 1000),
            end=offset_ms + int(float(word.get("end", 0)) * 1000),
            text=str(word.get("word", "")),
            probability=float(word.get("probability", 1.0) or 1.0),
        )
        for word in (item.get("words") or [])
    )
    logprob = float(item.get("avg_logprob", 0.0) or 0.0)
    return Segment(
        start=offset_ms + int(float(item.get("start", 0)) * 1000),
        end=offset_ms + int(float(item.get("end", 0)) * 1000),
        text=str(item.get("text", "")),
        confidence=max(0.0, min(1.0, 1.0 + logprob)),
        words=words,
    )
