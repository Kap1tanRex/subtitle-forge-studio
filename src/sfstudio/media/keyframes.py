"""Индекс ключевых кадров и магнитная привязка таймингов.

Зачем: профессиональная практика — начинать и заканчивать реплику на монтажной
склейке. Ключевой кадр это почти всегда склейка, поэтому индекс I-кадров даёт
готовый набор точек притяжения бесплатно.

Индекс строится **демуксом без декодирования**: пакеты разбираются, но кадры
не восстанавливаются, поэтому двухчасовой фильм обходится за секунды.

.. note::
   ``codec_context.skip_frame = "NONKEY"`` здесь намеренно не выставляется.
   Этот флаг управляет декодированием, а ``demux()`` кадры не декодирует —
   строка выглядела бы осмысленной, но не делала ничего, кроме введения
   читателя в заблуждение. Признак ключевого кадра берётся из ``packet.is_keyframe``.
"""

from __future__ import annotations

import bisect
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.core.time import FpsModel, SnapMode

__all__ = ["KeyframeIndex", "SnapContext", "SnapResult", "build_keyframe_index"]


class KeyframeError(RuntimeError):
    """Не удалось построить индекс."""


@dataclass(slots=True)
class KeyframeIndex:
    """Отсортированные позиции ключевых кадров в миллисекундах."""

    times_ms: list[int]

    def __len__(self) -> int:
        return len(self.times_ms)

    def __bool__(self) -> bool:
        return bool(self.times_ms)

    def nearest(self, ms: int) -> int | None:
        """Ближайший ключевой кадр к моменту ``ms``."""
        times = self.times_ms
        if not times:
            return None
        i = bisect.bisect_left(times, ms)
        if i == 0:
            return times[0]
        if i >= len(times):
            return times[-1]
        before, after = times[i - 1], times[i]
        return before if (ms - before) <= (after - ms) else after

    def in_range(self, t0: int, t1: int) -> Sequence[int]:
        """Ключевые кадры в диапазоне — для отрисовки штрихов на таймлайне."""
        lo = bisect.bisect_left(self.times_ms, t0)
        hi = bisect.bisect_right(self.times_ms, t1)
        return self.times_ms[lo:hi]

    def save(self, path: Path) -> None:
        import numpy as np

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            np.save(str(tmp), np.array(self.times_ms, dtype=np.int64))
            # numpy.save дописывает .npy, если его нет.
            produced = tmp if tmp.exists() else tmp.with_suffix(tmp.suffix + ".npy")
            produced.replace(path)
        finally:
            for leftover in (tmp, tmp.with_suffix(tmp.suffix + ".npy")):
                if leftover.exists():
                    leftover.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> KeyframeIndex:
        import numpy as np

        return cls(times_ms=np.load(str(path)).tolist())


def build_keyframe_index(
    media: Path,
    *,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> KeyframeIndex:
    """Собирает позиции ключевых кадров демуксом видеопотока."""
    try:
        import av
    except (ImportError, OSError) as exc:
        raise KeyframeError(tr('PyAV недоступен: {0}').format(exc)) from exc

    try:
        container = av.open(str(media))
    except Exception as exc:
        raise KeyframeError(tr('не удалось открыть {0}: {1}').format(media.name, exc)) from exc

    try:
        if not container.streams.video:
            return KeyframeIndex(times_ms=[])
        stream = container.streams.video[0]
        time_base = stream.time_base
        total_us = container.duration or 0

        found: list[int] = []
        for packet in container.demux(stream):
            if cancel is not None and cancel.is_set():
                raise KeyframeError(tr('построение индекса отменено'))
            if packet.pts is None:
                continue
            if packet.is_keyframe:
                found.append(int(float(packet.pts * time_base) * 1000))
            if progress is not None and total_us:
                done = float(packet.pts * time_base) * 1_000_000
                progress(min(1.0, done / total_us))

        found.sort()
        if progress is not None:
            progress(1.0)
        return KeyframeIndex(times_ms=found)
    finally:
        container.close()


# --------------------------------------------------------------------------- #
# Привязка
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SnapContext:
    """Настройки магнитов для правки таймингов."""

    fps: FpsModel | None = None
    keyframes: KeyframeIndex | None = None
    #: Границы соседних событий — чтобы реплики стыковались.
    event_boundaries: Sequence[int] = ()

    snap_to_frames: bool = True
    snap_to_keyframes: bool = True
    snap_to_events: bool = True

    #: Радиус притяжения в пикселях таймлайна; в миллисекунды переводится
    #: по текущему масштабу, чтобы поведение не зависело от зума.
    radius_px: float = 8.0
    px_per_ms: float = 0.05

    #: Минимальный зазор между соседними событиями, в кадрах.
    min_gap_frames: int = 2

    @property
    def radius_ms(self) -> float:
        return self.radius_px / self.px_per_ms if self.px_per_ms > 0 else 0.0


@dataclass(frozen=True, slots=True)
class SnapResult:
    ms: int
    #: К чему притянулось: 'keyframe' | 'event' | 'frame' | '' (никуда).
    kind: str = ""


def snap_time(ms: int, ctx: SnapContext, *, mode: SnapMode = SnapMode.NEAREST) -> SnapResult:
    """Притягивает момент времени к ближайшей значимой точке.

    Приоритет — от самого «содержательного» к самому механическому:
    смена сцены/склейка важнее стыка с соседней репликой, а тот важнее
    простого округления к сетке кадров. Округление к кадрам применяется
    в любом случае последним, чтобы результат всегда лежал на сетке.
    """
    radius = ctx.radius_ms

    if ctx.snap_to_keyframes and ctx.keyframes:
        candidate = ctx.keyframes.nearest(ms)
        if candidate is not None and abs(candidate - ms) <= radius:
            return SnapResult(_frame_align(candidate, ctx, mode), "keyframe")

    if ctx.snap_to_events and ctx.event_boundaries:
        candidate = _nearest(ctx.event_boundaries, ms)
        if candidate is not None and abs(candidate - ms) <= radius:
            return SnapResult(_frame_align(candidate, ctx, mode), "event")

    if ctx.snap_to_frames and ctx.fps is not None:
        return SnapResult(ctx.fps.snap(ms, mode), "frame")

    return SnapResult(ms, "")


def _frame_align(ms: int, ctx: SnapContext, mode: SnapMode) -> int:
    if ctx.snap_to_frames and ctx.fps is not None:
        return ctx.fps.snap(ms, mode)
    return ms


def _nearest(values: Sequence[int], ms: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    i = bisect.bisect_left(ordered, ms)
    if i == 0:
        return ordered[0]
    if i >= len(ordered):
        return ordered[-1]
    before, after = ordered[i - 1], ordered[i]
    return before if (ms - before) <= (after - ms) else after


def min_gap_ms(ctx: SnapContext) -> int:
    """Минимальный зазор между репликами в миллисекундах."""
    if ctx.fps is None:
        return 84  # ~2 кадра при 23.976
    return int(ctx.min_gap_frames * 1000 / float(ctx.fps.rate))
