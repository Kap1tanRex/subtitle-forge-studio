"""Метаданные медиафайла: потоки, кадровая частота, дорожки субтитров.

Читается через PyAV — без запуска ffprobe как подпроцесса. Это заметно быстрее
(нет создания процесса и разбора JSON) и не требует внешнего бинаря: PyAV несёт
ffmpeg внутри колеса.

PyAV импортируется лениво, внутри функций. Модуль обязан импортироваться на
машине без PyAV — иначе приложение не сможет даже сообщить пользователю,
почему видео недоступно.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.core.time import FpsModel

__all__ = [
    "AudioStreamInfo",
    "MediaInfo",
    "MediaProbeError",
    "SubtitleTrackInfo",
    "VideoStreamInfo",
    "probe",
    "pyav_available",
]

#: Кодеки субтитров, которые являются картинками, а не текстом.
#: Их нельзя открыть на правку без OCR — только показать как подложку.
BITMAP_SUBTITLE_CODECS = frozenset(
    {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvbsub", "pgssub"}
)

#: Текстовые кодеки субтитров, которые мы умеем извлекать.
TEXT_SUBTITLE_CODECS = frozenset(
    {"ass", "ssa", "subrip", "srt", "webvtt", "mov_text", "text", "microdvd"}
)


class MediaProbeError(RuntimeError):
    """Файл не удалось прочитать как медиа."""


def pyav_available() -> bool:
    try:
        import av  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class VideoStreamInfo:
    index: int
    codec: str
    width: int
    height: int
    fps: FpsModel
    #: Отношение сторон пикселя. Для anamorphic-источников (DVD) не равно 1.
    sample_aspect: Fraction = Fraction(1, 1)
    #: Поворот из матрицы отображения: 0, 90, 180 или 270.
    rotation: int = 0

    @property
    def display_size(self) -> tuple[int, int]:
        """Размер кадра как его увидит зритель: с учётом SAR и поворота.

        Именно этот размер задаёт пропорции окна и передаётся в libass.
        Использовать вместо него `width`/`height` — значит растянуть субтитры
        не так, как это сделает плеер.
        """
        w = round(self.width * float(self.sample_aspect))
        h = self.height
        return (h, w) if self.rotation in (90, 270) else (w, h)

    @property
    def display_aspect(self) -> float:
        w, h = self.display_size
        return w / h if h else 16 / 9


@dataclass(frozen=True, slots=True)
class AudioStreamInfo:
    index: int
    codec: str
    sample_rate: int
    channels: int
    language: str | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class SubtitleTrackInfo:
    index: int
    codec: str
    language: str | None = None
    title: str | None = None
    is_default: bool = False
    is_forced: bool = False

    @property
    def is_bitmap(self) -> bool:
        return self.codec in BITMAP_SUBTITLE_CODECS

    @property
    def is_editable(self) -> bool:
        """Можно ли открыть дорожку на правку.

        Битмапные субтитры без OCR не редактируются — их надо показывать
        в списке, но помечать, а не молча прятать: пользователь должен видеть,
        что дорожка есть, и понимать, почему она недоступна.
        """
        return self.codec in TEXT_SUBTITLE_CODECS

    def display_name(self) -> str:
        parts: list[str] = []
        if self.title:
            parts.append(self.title)
        if self.language:
            parts.append(f"[{self.language}]")
        if not parts:
            parts.append(tr('Дорожка {0}').format(self.index))
        flags = []
        if self.is_default:
            flags.append(tr('по умолчанию'))
        if self.is_forced:
            flags.append("forced")
        if self.is_bitmap:
            flags.append(tr('картинка, правка недоступна'))
        suffix = f" ({', '.join(flags)})" if flags else ""
        return f"{' '.join(parts)} · {self.codec}{suffix}"


@dataclass(slots=True)
class MediaInfo:
    path: Path
    duration_ms: int = 0
    video: VideoStreamInfo | None = None
    audio: list[AudioStreamInfo] = field(default_factory=list)
    subtitles: list[SubtitleTrackInfo] = field(default_factory=list)
    container: str = ""

    @property
    def has_audio(self) -> bool:
        return bool(self.audio)

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def play_res(self) -> tuple[int, int]:
        """Разрешение, которое стоит подставить в PlayRes нового документа."""
        return self.video.display_size if self.video else (1920, 1080)

    def editable_subtitles(self) -> list[SubtitleTrackInfo]:
        return [t for t in self.subtitles if t.is_editable]


# --------------------------------------------------------------------------- #


def probe(path: Path) -> MediaInfo:
    """Читает метаданные файла."""
    try:
        import av
    except (ImportError, OSError) as exc:
        raise MediaProbeError(tr('PyAV недоступен: {0}').format(exc)) from exc

    try:
        container = av.open(str(path))
    except Exception as exc:
        raise MediaProbeError(tr('не удалось открыть {0}: {1}').format(path.name, exc)) from exc

    try:
        info = MediaInfo(path=path, container=container.format.name)

        if container.duration is not None:
            # container.duration в av.time_base (микросекунды).
            info.duration_ms = int(container.duration / 1000)

        for stream in container.streams:
            kind = stream.type
            if kind == "video" and info.video is None:
                info.video = _read_video(stream)
            elif kind == "audio":
                info.audio.append(_read_audio(stream))
            elif kind == "subtitle":
                info.subtitles.append(_read_subtitle(stream))

        # Длительность контейнера бывает не задана (потоковые форматы) —
        # берём максимум по потокам.
        if info.duration_ms <= 0:
            info.duration_ms = _duration_from_streams(container)

        return info
    finally:
        container.close()


def _read_video(stream: object) -> VideoStreamInfo:
    rate = getattr(stream, "average_rate", None) or getattr(stream, "guessed_rate", None)
    fps = FpsModel(Fraction(rate)) if rate else FpsModel()

    sar = getattr(stream, "sample_aspect_ratio", None)
    if not sar or sar <= 0:
        sar = Fraction(1, 1)

    codec_ctx = getattr(stream, "codec_context", None)
    return VideoStreamInfo(
        index=stream.index,  # type: ignore[attr-defined]
        codec=getattr(getattr(codec_ctx, "codec", None), "name", "?") or "?",
        width=int(getattr(codec_ctx, "width", 0) or 0),
        height=int(getattr(codec_ctx, "height", 0) or 0),
        fps=fps,
        sample_aspect=Fraction(sar),
        rotation=_read_rotation(stream),
    )


def _read_rotation(stream: object) -> int:
    """Поворот кадра из матрицы отображения либо из тега ``rotate``.

    Разные версии ffmpeg и разные контейнеры кладут поворот в разные места,
    поэтому проверяются оба, и результат нормализуется к 0/90/180/270.
    Молча вернуть 0 при непонятном значении безопаснее, чем повернуть кадр
    наугад: субтитры тогда точно не разъедутся.
    """
    raw: object = None

    metadata = getattr(stream, "metadata", None) or {}
    if isinstance(metadata, dict):
        raw = metadata.get("rotate")

    if raw is None:
        side_data = getattr(stream, "side_data", None)
        if side_data is not None:
            try:
                matrix = side_data.get("DISPLAYMATRIX")
            except (AttributeError, TypeError, KeyError):
                matrix = None
            if matrix is not None:
                raw = getattr(matrix, "rotation", matrix)

    if raw is None:
        return 0
    try:
        degrees = round(float(raw))
    except (TypeError, ValueError):
        return 0
    degrees %= 360
    return degrees if degrees in (0, 90, 180, 270) else 0


def _read_audio(stream: object) -> AudioStreamInfo:
    codec_ctx = getattr(stream, "codec_context", None)
    metadata = getattr(stream, "metadata", None) or {}
    channels = getattr(codec_ctx, "channels", None)
    if channels is None:
        layout = getattr(codec_ctx, "layout", None)
        channels = len(getattr(layout, "channels", []) or []) or 2
    return AudioStreamInfo(
        index=stream.index,  # type: ignore[attr-defined]
        codec=getattr(getattr(codec_ctx, "codec", None), "name", "?") or "?",
        sample_rate=int(getattr(codec_ctx, "sample_rate", 0) or 0),
        channels=int(channels),
        language=metadata.get("language"),
        title=metadata.get("title"),
    )


def _read_subtitle(stream: object) -> SubtitleTrackInfo:
    codec_ctx = getattr(stream, "codec_context", None)
    metadata = getattr(stream, "metadata", None) or {}
    disposition = getattr(stream, "disposition", 0) or 0
    return SubtitleTrackInfo(
        index=stream.index,  # type: ignore[attr-defined]
        codec=getattr(getattr(codec_ctx, "codec", None), "name", "?") or "?",
        language=metadata.get("language"),
        title=metadata.get("title"),
        is_default=bool(int(disposition) & 0x0001),
        is_forced=bool(int(disposition) & 0x0040),
    )


def _duration_from_streams(container: object) -> int:
    best = 0
    for stream in container.streams:  # type: ignore[attr-defined]
        duration = getattr(stream, "duration", None)
        time_base = getattr(stream, "time_base", None)
        if duration and time_base:
            best = max(best, round(float(duration * time_base) * 1000))
    return best
