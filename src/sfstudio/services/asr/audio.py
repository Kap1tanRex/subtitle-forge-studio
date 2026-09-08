"""Подготовка звука для распознавания.

Готовим звук **мы**, а не движок. Причины две.

Первая: 16 кГц моно — общий знаменатель всех ASR-моделей семейства Whisper и
почти всех остальных. Если каждый адаптер будет декодировать сам, одна и та же
работа с PyAV окажется переписанной трижды, а расхождения в ресемплинге дадут
разные результаты на одном файле.

Вторая: движку нужен звук, а не контейнер. Внешние программы вроде whisper.cpp
принимают WAV и не умеют читать MKV; библиотеки принимают массив. Разделив
декодирование и распознавание, мы даём и то, и другое из одного места.

Отрезок задаётся временем, а не сэмплами: пользователь выделяет участок на
таймлайне, и переводить его в сэмплы в каждом адаптере значило бы разносить
одну и ту же арифметику по коду.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

from sfstudio.services.asr.base import (
    CancelToken,
    ProgressReporter,
    RecognitionError,
)

__all__ = ["SAMPLE_RATE", "AudioChunk", "extract_audio", "write_wav"]

#: Частота, которую ждут модели Whisper и совместимые. Ресемплинг к ней —
#: не оптимизация, а требование: на другой частоте модель выдаёт мусор.
SAMPLE_RATE = 16_000


@dataclass(slots=True)
class AudioChunk:
    """Моно-звук в формате, который принимают модели."""

    #: ``numpy.ndarray`` float32 в диапазоне -1..1. Тип не указан явно, чтобы
    #: модуль импортировался без NumPy — он нужен только при вызове.
    samples: object
    sample_rate: int = SAMPLE_RATE
    #: Смещение начала относительно файла, мс. Нужно, чтобы вернуть
    #: полученные тайминги в систему координат всего медиа.
    offset_ms: int = 0

    @property
    def duration_ms(self) -> int:
        length = len(self.samples)  # type: ignore[arg-type]
        return int(length * 1000 / self.sample_rate) if self.sample_rate else 0


def extract_audio(
    media: Path,
    *,
    start_ms: int = 0,
    end_ms: int | None = None,
    stream_index: int = 0,
    progress: ProgressReporter | None = None,
    cancel: CancelToken | None = None,
) -> AudioChunk:
    """Декодирует участок звука в моно 16 кГц float32."""
    try:
        import av
        import numpy as np
    except ImportError as exc:  # pragma: no cover - PyAV есть в поставке
        raise RecognitionError(f"нет PyAV или NumPy: {exc}") from exc

    media = Path(media)
    if not media.is_file():
        raise RecognitionError(f"файл не найден: {media}")

    try:
        container = av.open(str(media))
    except Exception as exc:
        raise RecognitionError(f"не удалось открыть {media.name}: {exc}") from exc

    try:
        streams = container.streams.audio
        if not streams:
            raise RecognitionError(f"в {media.name} нет звуковой дорожки")
        if stream_index >= len(streams):
            raise RecognitionError(f"звуковая дорожка {stream_index} не найдена")
        stream = streams[stream_index]

        # Перемотка к началу участка: декодировать час ради последних пяти
        # минут бессмысленно. Перемотка неточная (до ближайшего пакета),
        # поэтому лишнее отрезается ниже по времени кадра.
        if start_ms > 0:
            with _suppress_seek_errors():
                container.seek(
                    int(start_ms / 1000 / stream.time_base),
                    stream=stream,
                    any_frame=False,
                    backward=True,
                )

        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        blocks: list[object] = []
        span_ms = (end_ms - start_ms) if end_ms is not None else None

        for frame in container.decode(stream):
            if cancel is not None:
                cancel.raise_if_cancelled()

            frame_start_ms = _frame_time_ms(frame, stream)
            if end_ms is not None and frame_start_ms >= end_ms:
                break

            for resampled in resampler.resample(frame):
                block = resampled.to_ndarray().reshape(-1)
                if block.size:
                    blocks.append(block)

            if progress is not None and span_ms:
                done = max(0, frame_start_ms - start_ms) / span_ms
                progress(min(0.99, done), "чтение звука")

        # Хвост ресемплера: без слива теряется последняя доля секунды, а это
        # ровно та часть, где обычно и находится конец последней фразы.
        for resampled in resampler.resample(None):
            block = resampled.to_ndarray().reshape(-1)
            if block.size:
                blocks.append(block)
    finally:
        container.close()

    if not blocks:
        return AudioChunk(samples=np.zeros(0, dtype="float32"), offset_ms=start_ms)

    samples = np.concatenate(blocks).astype("float32", copy=False)
    samples = _trim(samples, start_ms, end_ms)
    if progress is not None:
        progress(1.0, "звук готов")
    return AudioChunk(samples=samples, offset_ms=start_ms)


def _trim(samples, start_ms: int, end_ms: int | None):
    """Отрезает лишнее, оставшееся после неточной перемотки."""
    if end_ms is None:
        return samples
    wanted = int((end_ms - start_ms) / 1000 * SAMPLE_RATE)
    return samples[:wanted] if 0 < wanted < len(samples) else samples


def _frame_time_ms(frame, stream) -> int:
    """Время кадра в миллисекундах. Кадры без метки считаем нулевыми."""
    if frame.pts is None:
        return 0
    base = frame.time_base or stream.time_base
    return int(float(frame.pts * base) * 1000)


class _suppress_seek_errors:  # noqa: N801 - контекстный менеджер, не класс-сущность
    """Неудачная перемотка не фатальна: читаем с начала, просто дольше."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, Exception)


def write_wav(chunk: AudioChunk, path: Path) -> Path:
    """Пишет WAV 16 бит — для движков, которые работают с файлом.

    Внешние программы (whisper.cpp и подобные) принимают именно WAV и не умеют
    читать контейнеры, поэтому промежуточный файл здесь неизбежен.
    """
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise RecognitionError(f"нет NumPy: {exc}") from exc

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Клип по -1..1 до умножения: значения вне диапазона после приведения к
    # int16 переполняются и превращают громкие места в треск.
    data = np.clip(np.asarray(chunk.samples, dtype="float32"), -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(chunk.sample_rate)
        handle.writeframes(pcm.tobytes())
    return path
