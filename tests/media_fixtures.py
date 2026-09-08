"""Генератор тестовых медиафайлов.

Тесты медиа-слоя должны работать на **известном** содержимом: если аудио —
чередование тона и тишины по расписанию, то и пики проверяются по конкретным
значениям, а не «что-то ненулевое». То же с ключевыми кадрами: отключённый
scene-cut даёт ровно один ключевой кадр на GOP.

Файлы генерируются один раз на сессию pytest и кладутся во временный каталог.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

__all__ = ["MediaSpec", "ToneSpan", "make_test_media"]

SAMPLE_RATE = 48_000


@dataclass(frozen=True, slots=True)
class ToneSpan:
    """Интервал с тоном. Вне таких интервалов — тишина."""

    start_ms: int
    end_ms: int
    freq: float = 440.0
    #: Амплитуда в долях от максимума int16.
    amplitude: float = 0.5


@dataclass(frozen=True, slots=True)
class MediaSpec:
    duration_ms: int = 3000
    width: int = 320
    height: int = 240
    fps: Fraction = Fraction(24, 1)
    #: Расстояние между ключевыми кадрами.
    gop: int = 12
    tones: tuple[ToneSpan, ...] = field(
        default_factory=lambda: (
            ToneSpan(500, 1000, 440.0, 0.8),
            ToneSpan(1500, 2000, 880.0, 0.4),
        )
    )
    with_audio: bool = True
    with_video: bool = True

    @property
    def expected_keyframe_count(self) -> int:
        total = int(self.duration_ms * float(self.fps) / 1000)
        return (total + self.gop - 1) // self.gop


def _build_audio(spec: MediaSpec):
    """Массив int16 моно на всю длительность."""
    import numpy as np

    n = int(SAMPLE_RATE * spec.duration_ms / 1000)
    out = np.zeros(n, dtype=np.int16)
    for tone in spec.tones:
        a = int(SAMPLE_RATE * tone.start_ms / 1000)
        b = min(n, int(SAMPLE_RATE * tone.end_ms / 1000))
        if b <= a:
            continue
        t = np.arange(b - a, dtype=np.float64) / SAMPLE_RATE
        wave = np.sin(2 * np.pi * tone.freq * t) * tone.amplitude * 32767
        out[a:b] = wave.astype(np.int16)
    return out


def make_test_media(path: Path, spec: MediaSpec | None = None) -> MediaSpec:
    """Пишет тестовый MKV. Возвращает использованную спецификацию."""
    import av
    import numpy as np

    spec = spec or MediaSpec()
    path.parent.mkdir(parents=True, exist_ok=True)

    container = av.open(str(path), mode="w")
    try:
        vstream = None
        if spec.with_video:
            vstream = container.add_stream("libx264", rate=spec.fps)
            vstream.width = spec.width
            vstream.height = spec.height
            vstream.pix_fmt = "yuv420p"
            # sc_threshold=0 отключает ключевые кадры по смене сцены, поэтому
            # они встают ровно через gop — иначе тест на их число был бы шатким.
            vstream.options = {
                "g": str(spec.gop),
                "keyint_min": str(spec.gop),
                "sc_threshold": "0",
                "preset": "ultrafast",
                "tune": "zerolatency",
            }

        astream = None
        if spec.with_audio:
            # PCM без потерь: пики тогда сравнимы с тем, что мы записали.
            # Сжатое аудио внесло бы погрешность, и тест стал бы нечётким.
            astream = container.add_stream("pcm_s16le", rate=SAMPLE_RATE)
            astream.layout = "mono"

        if vstream is not None:
            frame_count = int(spec.duration_ms * float(spec.fps) / 1000)
            for i in range(frame_count):
                # Градиент, меняющийся во времени: кадры различимы, но кодек
                # не находит смен сцены.
                img = np.zeros((spec.height, spec.width, 3), dtype=np.uint8)
                img[:, :, 0] = (i * 3) % 256
                img[:, :, 1] = np.linspace(0, 255, spec.width, dtype=np.uint8)[None, :]
                img[:, :, 2] = np.linspace(0, 255, spec.height, dtype=np.uint8)[:, None]
                frame = av.VideoFrame.from_ndarray(img, format="rgb24")
                frame.pts = i
                for packet in vstream.encode(frame):
                    container.mux(packet)
            for packet in vstream.encode(None):
                container.mux(packet)

        if astream is not None:
            samples = _build_audio(spec)
            chunk = 4800  # 100 мс
            pts = 0
            for start in range(0, len(samples), chunk):
                block = samples[start : start + chunk]
                frame = av.AudioFrame.from_ndarray(
                    block.reshape(1, -1), format="s16", layout="mono"
                )
                frame.sample_rate = SAMPLE_RATE
                frame.pts = pts
                pts += block.shape[0]
                for packet in astream.encode(frame):
                    container.mux(packet)
            for packet in astream.encode(None):
                container.mux(packet)
    finally:
        container.close()

    return spec
