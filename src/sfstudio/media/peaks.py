"""Пики аудио: извлечение, формат ``.peaks``, пирамида мип-уровней.

Задача: рисовать волну двухчасового файла с плавным зумом. При 48 кГц это
~500 млн отсчётов — читать их на каждую перерисовку нельзя. Поэтому один раз
строится пирамида: уровень 0 усредняет по 256 отсчётов, каждый следующий — по
4 предыдущих. Отрисовка берёт уровень, ближайший к текущему масштабу, и
работает с тысячами чисел вместо миллионов.

Файл читается через ``numpy.memmap``: страницы подтягивает ОС по мере надобности,
в памяти процесса пирамида целиком не лежит.

Формат файла (little-endian)::

    Header, 64 байта
        magic        char[8]   "SFPEAK\\x01\\x00"
        version      uint32    версия структуры файла
        extractor_v  uint32    версия алгоритма извлечения
        sample_rate  uint32
        channels     uint32    всегда 1: микшируем в моно
        duration_ms  uint64
        levels       uint32
        stream_index uint32    какая аудиодорожка была источником
        reserved     byte[24]

    LevelTable, levels * 16 байт
        samples_per_bucket uint32
        bucket_count       uint32
        offset             uint64   смещение блока уровня

    Блок уровня: три непрерывных массива подряд
        min  int16  * bucket_count
        max  int16  * bucket_count
        rms  uint16 * bucket_count

Массивы разделены, а не чередуются: срез по времени тогда читает непрерывную
память, и numpy отдаёт его без копирования.
"""

from __future__ import annotations

import hashlib
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sfstudio.app.i18n import tr

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "BUCKET_SAMPLES",
    "LEVELS",
    "NoAudioError",
    "PeakData",
    "PeaksError",
    "cache_key",
    "extract_peaks",
]

MAGIC = b"SFPEAK\x01\x00"
FORMAT_VERSION = 1

#: Версия алгоритма. Поднимать при любом изменении BUCKET_SAMPLES, числа
#: уровней или способа расчёта — иначе старый кэш будет прочитан как новый.
EXTRACTOR_VERSION = 1

#: Отсчётов в бакете нулевого уровня. При 48 кГц это 5.33 мс — достаточно
#: мелко, чтобы на максимальном зуме волна не выглядела ступенькой.
BUCKET_SAMPLES = 256
LEVELS = 6  # 256, 1024, 4096, 16384, 65536, 262144

_HEADER = struct.Struct("<8sIIIIQII24s")
_LEVEL_ENTRY = struct.Struct("<IIQ")
_HEADER_SIZE = 64


class PeaksError(RuntimeError):
    """Не удалось построить или прочитать пики."""


class NoAudioError(PeaksError):
    """В файле нет аудиодорожки.

    Отдельный класс, потому что это **не ошибка**, а штатная ситуация:
    таймлайн рисует плоскую линию с пометкой, а редактор продолжает работать.
    Обрабатывать её как сбой открытия файла было бы неверно.
    """


def cache_key(media: Path, stream_index: int = 0) -> str:
    """Имя файла кэша для данной пары «медиа + аудиодорожка».

    Поля сериализуются с указанием длины, а не склеиваются: конкатенация
    ``path + size + mtime`` даёт одинаковый результат для разных наборов
    входных данных. Номер дорожки входит в ключ обязательно — иначе после
    переключения аудиодорожки показывалась бы волна от предыдущей.
    """
    h = hashlib.sha256()
    for part in (
        str(media.resolve()).encode("utf-8"),
        str(_safe_stat(media, "st_size")).encode(),
        str(_safe_stat(media, "st_mtime_ns")).encode(),
        str(stream_index).encode(),
        str(EXTRACTOR_VERSION).encode(),
    ):
        h.update(len(part).to_bytes(4, "little"))
        h.update(part)
    return h.hexdigest()


def _safe_stat(path: Path, field: str) -> int:
    try:
        return int(getattr(path.stat(), field))
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# Извлечение
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExtractResult:
    path: Path
    duration_ms: int
    sample_rate: int
    bucket_counts: tuple[int, ...]


def extract_peaks(
    media: Path,
    out: Path,
    *,
    stream_index: int = 0,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> ExtractResult:
    """Строит пирамиду пиков и пишет её в ``out``.

    Запись атомарная: сначала во временный файл рядом, затем переименование.
    Прерванное извлечение не оставит полуфабрикат, который потом прочитается
    как валидный кэш.
    """
    try:
        import av
        import numpy as np
    except (ImportError, OSError) as exc:
        raise PeaksError(tr('нужны PyAV и NumPy: {0}').format(exc)) from exc

    try:
        container = av.open(str(media))
    except Exception as exc:
        raise PeaksError(tr('не удалось открыть {0}: {1}').format(media.name, exc)) from exc

    try:
        streams = container.streams.audio
        if not streams:
            raise NoAudioError(tr('в {0} нет аудиодорожки').format(media.name))
        if stream_index >= len(streams):
            raise PeaksError(tr('аудиодорожка {0} не найдена').format(stream_index))
        stream = streams[stream_index]

        sample_rate = int(stream.codec_context.sample_rate or 48_000)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)

        total_us = container.duration or 0
        mins: list[np.ndarray] = []
        maxs: list[np.ndarray] = []
        rmss: list[np.ndarray] = []
        carry = np.empty(0, dtype=np.int16)
        decoded_samples = 0

        for frame in container.decode(stream):
            if cancel is not None and cancel.is_set():
                raise PeaksError(tr('извлечение отменено'))

            for resampled in resampler.resample(frame):
                block = resampled.to_ndarray().reshape(-1)
                buf = np.concatenate((carry, block)) if carry.size else block
                usable = (buf.size // BUCKET_SAMPLES) * BUCKET_SAMPLES
                if usable:
                    grid = buf[:usable].reshape(-1, BUCKET_SAMPLES)
                    mins.append(grid.min(axis=1))
                    maxs.append(grid.max(axis=1))
                    # int32, иначе квадраты int16 переполняются.
                    squares = grid.astype(np.int32) ** 2
                    rmss.append(np.sqrt(squares.mean(axis=1)).astype(np.uint16))
                carry = buf[usable:]

            decoded_samples += block.shape[0] if block.size else 0
            if progress is not None and total_us:
                done = decoded_samples / sample_rate * 1_000_000
                progress(min(1.0, done / total_us))

        # Хвост короче бакета — дополняем нулями, чтобы не потерять концовку.
        if carry.size:
            tail = np.zeros(BUCKET_SAMPLES, dtype=np.int16)
            tail[: carry.size] = carry
            mins.append(np.array([tail.min()], dtype=np.int16))
            maxs.append(np.array([tail.max()], dtype=np.int16))
            rmss.append(
                np.array(
                    [np.sqrt((tail.astype(np.int32) ** 2).mean())], dtype=np.uint16
                )
            )
            decoded_samples += carry.size

        if not mins:
            raise NoAudioError(tr('в {0} не удалось декодировать звук').format(media.name))

        level0 = (
            np.concatenate(mins),
            np.concatenate(maxs),
            np.concatenate(rmss),
        )
        pyramid = _build_pyramid(level0)
        duration_ms = int(decoded_samples * 1000 / sample_rate)

        _write_file(out, pyramid, sample_rate, duration_ms, stream_index)
        if progress is not None:
            progress(1.0)

        return ExtractResult(
            path=out,
            duration_ms=duration_ms,
            sample_rate=sample_rate,
            bucket_counts=tuple(len(lvl[0]) for lvl in pyramid),
        )
    finally:
        container.close()


def _build_pyramid(level0: tuple) -> list[tuple]:
    """Достраивает уровни редукцией по 4.

    min берётся минимумом, max максимумом, а rms — **максимумом**, а не
    средним: на грубом уровне важно не потерять громкий фрагмент, иначе
    короткая реплика исчезнет из волны при отдалении.
    """
    pyramid = [level0]
    for _ in range(LEVELS - 1):
        prev_min, prev_max, prev_rms = pyramid[-1]
        n = len(prev_min) // 4
        if n < 1:
            break
        usable = n * 4
        pyramid.append(
            (
                prev_min[:usable].reshape(-1, 4).min(axis=1),
                prev_max[:usable].reshape(-1, 4).max(axis=1),
                prev_rms[:usable].reshape(-1, 4).max(axis=1),
            )
        )
    return pyramid


def _write_file(
    out: Path,
    pyramid: list[tuple],
    sample_rate: int,
    duration_ms: int,
    stream_index: int,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + f".tmp-{id(pyramid):x}")

    table_size = _LEVEL_ENTRY.size * len(pyramid)
    offset = _HEADER_SIZE + table_size

    entries: list[bytes] = []
    for i, (level_min, _, _) in enumerate(pyramid):
        count = len(level_min)
        entries.append(
            _LEVEL_ENTRY.pack(BUCKET_SAMPLES * (4**i), count, offset)
        )
        offset += count * (2 + 2 + 2)

    header = _HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        EXTRACTOR_VERSION,
        sample_rate,
        1,
        duration_ms,
        len(pyramid),
        stream_index,
        b"\x00" * 24,
    )

    try:
        with tmp.open("wb") as fh:
            fh.write(header)
            assert fh.tell() == _HEADER_SIZE, tr('размер заголовка разъехался')
            for entry in entries:
                fh.write(entry)
            for level_min, level_max, level_rms in pyramid:
                fh.write(level_min.astype("<i2").tobytes())
                fh.write(level_max.astype("<i2").tobytes())
                fh.write(level_rms.astype("<u2").tobytes())
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Чтение
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Level:
    samples_per_bucket: int
    count: int
    offset: int


class PeakData:
    """Читатель пирамиды. Данные подгружаются через memmap по требованию."""

    __slots__ = ("_arrays", "_levels", "_path", "duration_ms", "sample_rate", "stream_index")

    def __init__(self, path: Path) -> None:
        self._path = path
        raw = path.read_bytes()[:_HEADER_SIZE]
        if len(raw) < _HEADER_SIZE:
            raise PeaksError(tr('{0}: файл короче заголовка').format(path.name))

        magic, version, extractor, rate, channels, duration, levels, stream, _ = _HEADER.unpack(raw)
        if magic != MAGIC:
            raise PeaksError(tr('{0}: не файл пиков').format(path.name))
        if version != FORMAT_VERSION or extractor != EXTRACTOR_VERSION:
            raise PeaksError(
                tr('{0}: версия кэша {1}/{2}, ожидалась '
                       '{3}/{4}').format(
                           path.name, version, extractor, FORMAT_VERSION, EXTRACTOR_VERSION)
            )
        _ = channels

        self.sample_rate = int(rate)
        self.duration_ms = int(duration)
        self.stream_index = int(stream)

        table = path.read_bytes()[_HEADER_SIZE : _HEADER_SIZE + _LEVEL_ENTRY.size * levels]
        self._levels: list[_Level] = []
        for i in range(levels):
            chunk = table[i * _LEVEL_ENTRY.size : (i + 1) * _LEVEL_ENTRY.size]
            spb, count, offset = _LEVEL_ENTRY.unpack(chunk)
            self._levels.append(_Level(spb, count, offset))

        self._arrays: dict[int, tuple] = {}

    @classmethod
    def open(cls, path: Path) -> PeakData:
        return cls(path)

    @property
    def level_count(self) -> int:
        return len(self._levels)

    def bucket_count(self, level: int) -> int:
        return self._levels[level].count

    def samples_per_bucket(self, level: int) -> int:
        return self._levels[level].samples_per_bucket

    def _level_arrays(self, level: int) -> tuple:
        cached = self._arrays.get(level)
        if cached is not None:
            return cached

        import numpy as np

        info = self._levels[level]
        n = info.count
        base = info.offset
        arrays = (
            np.memmap(self._path, dtype="<i2", mode="r", offset=base, shape=(n,)),
            np.memmap(self._path, dtype="<i2", mode="r", offset=base + 2 * n, shape=(n,)),
            np.memmap(self._path, dtype="<u2", mode="r", offset=base + 4 * n, shape=(n,)),
        )
        self._arrays[level] = arrays
        return arrays

    def level_for(self, samples_per_pixel: float) -> int:
        """Самый детальный уровень, не превышающий требуемую плотность.

        Берётся именно «не превышающий», чтобы на пиксель приходился хотя бы
        один бакет: если взять уровень грубее, волна станет ступенчатой.
        """
        best = 0
        for i, level in enumerate(self._levels):
            if level.samples_per_bucket <= max(1.0, samples_per_pixel):
                best = i
            else:
                break
        return best

    def ms_per_bucket(self, level: int) -> float:
        return self._levels[level].samples_per_bucket * 1000.0 / self.sample_rate

    def slice(self, t0_ms: float, t1_ms: float, width: int) -> tuple:
        """``(min, max, rms)`` длиной ровно ``width`` для диапазона времени.

        Уровень выбирается автоматически по требуемой плотности. Свёртка
        бакетов в колонки делается через ``reduceat`` — один проход по
        непрерывной памяти, без циклов на Python.
        """
        import numpy as np

        width = max(1, int(width))
        empty = (
            np.zeros(width, dtype=np.int16),
            np.zeros(width, dtype=np.int16),
            np.zeros(width, dtype=np.uint16),
        )
        if t1_ms <= t0_ms or not self._levels:
            return empty

        span_ms = t1_ms - t0_ms
        samples_per_px = span_ms / 1000.0 * self.sample_rate / width
        level = self.level_for(samples_per_px)
        info = self._levels[level]
        bucket_ms = self.ms_per_bucket(level)

        b0 = int(max(0, t0_ms // bucket_ms))
        b1 = int(min(info.count, -(-t1_ms // bucket_ms)))  # ceil
        if b1 <= b0:
            return empty

        level_min, level_max, level_rms = self._level_arrays(level)
        seg_min = level_min[b0:b1]
        seg_max = level_max[b0:b1]
        seg_rms = level_rms[b0:b1]
        n = seg_min.shape[0]
        if n == 0:
            return empty

        # Границы групп. При n < width группы вырождаются в повторы одного
        # индекса — reduceat тогда отдаёт сам элемент, что даёт растяжение.
        edges = np.linspace(0, n, width + 1)[:-1].astype(np.int64)
        np.clip(edges, 0, n - 1, out=edges)

        return (
            np.minimum.reduceat(seg_min, edges),
            np.maximum.reduceat(seg_max, edges),
            np.maximum.reduceat(seg_rms, edges),
        )

    def loudness_at(self, ms: float) -> float:
        """Громкость в точке, 0..1. Для поиска границ речи."""
        import numpy as np

        if not self._levels:
            return 0.0
        bucket = int(ms // self.ms_per_bucket(0))
        _, _, rms = self._level_arrays(0)
        if not 0 <= bucket < rms.shape[0]:
            return 0.0
        return float(np.clip(rms[bucket] / 32767.0, 0.0, 1.0))
