"""Что делать с файлом, брошенным на окно.

Перетаскивание — самый короткий путь открыть видео: файл уже под курсором в
проводнике, и заставлять человека идти в меню, диалог и папку ради того же
самого незачем.

Разбор вынесен из виджета в отдельный модуль намеренно: «какого рода этот
файл» — вопрос про имя файла, а не про мышь, и проверяется он без окна,
курсора и очереди событий.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from sfstudio.core.project import PROJECT_SUFFIX

__all__ = ["DropKind", "DroppedFiles", "classify", "sort_drop"]

#: Что умеет открывать плеер. Список нарочно шире, чем у диалога открытия:
#: отказать в перетаскивании файла, который потом прекрасно открывается через
#: меню, — худший из возможных ответов.
MEDIA_SUFFIXES = frozenset({
    ".mkv", ".mp4", ".mov", ".webm", ".avi", ".m4v", ".ts", ".m2ts", ".mpg",
    ".mpeg", ".wmv", ".flv", ".ogv",
    ".wav", ".mp3", ".aac", ".flac", ".ogg", ".opus", ".m4a", ".ac3", ".dts",
})

SUBTITLE_SUFFIXES = frozenset({
    ".ass", ".ssa", ".srt", ".vtt", ".ttml", ".dfxp", ".xml", ".sub", ".sbv",
})


class DropKind(Enum):
    """Род брошенного файла."""

    MEDIA = "media"
    SUBTITLES = "subtitles"
    PROJECT = "project"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DroppedFiles:
    """Разобранная пачка: по одному файлу каждого рода.

    Бросить могут и десяток файлов разом. Открывать десять видео бессмысленно
    — окно одно, — поэтому от каждого рода берётся первый, а об остальных
    честно сообщается числом: молча проглотить их хуже, чем сказать, что
    открыт один.
    """

    media: Path | None = None
    subtitles: Path | None = None
    project: Path | None = None
    ignored: int = 0

    @property
    def is_empty(self) -> bool:
        return self.media is None and self.subtitles is None and self.project is None


def classify(path: Path) -> DropKind:
    """Род файла по расширению.

    Только по имени, без чтения: ответ нужен в ``dragEnterEvent``, пока файл
    ещё висит над окном, и лезть в него на каждое движение мыши нельзя.
    """
    suffix = path.suffix.lower()
    if suffix == PROJECT_SUFFIX.lower():
        return DropKind.PROJECT
    if suffix in MEDIA_SUFFIXES:
        return DropKind.MEDIA
    if suffix in SUBTITLE_SUFFIXES:
        return DropKind.SUBTITLES
    return DropKind.UNKNOWN


def sort_drop(paths: list[Path]) -> DroppedFiles:
    """Разбирает пачку файлов по родам, беря от каждого первый."""
    media = subtitles = project = None
    ignored = 0

    for path in paths:
        kind = classify(path)
        if kind is DropKind.PROJECT and project is None:
            project = path
        elif kind is DropKind.MEDIA and media is None:
            media = path
        elif kind is DropKind.SUBTITLES and subtitles is None:
            subtitles = path
        else:
            ignored += 1

    return DroppedFiles(media=media, subtitles=subtitles, project=project, ignored=ignored)
