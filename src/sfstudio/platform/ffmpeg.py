"""Поиск и запуск ffmpeg.

Чтение медиа идёт через PyAV (он несёт ffmpeg внутри колеса), а вот запись в
контейнер — через внешний ffmpeg. Причина: ремукс с сохранением всех дорожек,
метаданных и disposition через API PyAV пришлось бы собирать вручную, дорожку
за дорожкой, и любая забытая мелочь молча теряется в результирующем файле.
Командная строка ffmpeg делает это одной командой и проверена миллионами
пользователей.

ffmpeg — мягкая зависимость: без него редактор работает, недоступна только
запись в контейнер. Поэтому отсутствие бинаря не ошибка импорта, а понятное
сообщение в момент, когда пользователь действительно попросил ремукс.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.platform.native import vendor_dir

__all__ = [
    "FfmpegError",
    "FfmpegNotFoundError",
    "ffmpeg_available",
    "find_ffmpeg",
    "find_ffprobe",
    "run_ffmpeg",
    "version_of",
]

#: На Windows подпроцесс не должен мигать консольным окном.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

_PROGRESS_TIME = re.compile(r"out_time_ms=(\d+)")


class FfmpegError(RuntimeError):
    """ffmpeg завершился с ошибкой."""


class FfmpegNotFoundError(FfmpegError):
    """Бинарь ffmpeg не найден."""


def _locate(name: str) -> Path | None:
    """Ищет бинарь: сначала рядом с вендоренными библиотеками, потом в PATH."""
    for candidate in (vendor_dir() / f"{name}.exe", vendor_dir() / name):
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def find_ffmpeg() -> Path | None:
    return _locate("ffmpeg")


def find_ffprobe() -> Path | None:
    return _locate("ffprobe")


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def version_of(path: Path | None = None) -> str:
    """Первая строка ``ffmpeg -version`` или пояснение, почему её нет."""
    binary = path or find_ffmpeg()
    if binary is None:
        return tr('не найден')
    try:
        result = subprocess.run(
            [str(binary), "-version"],
            capture_output=True, text=True, timeout=15,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return tr('не запускается: {0}').format(exc)
    return result.stdout.splitlines()[0] if result.stdout else tr('неизвестно')


@dataclass(frozen=True, slots=True)
class FfmpegResult:
    returncode: int
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_ffmpeg(
    args: list[str],
    *,
    total_ms: int = 0,
    progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float = 3600.0,
) -> FfmpegResult:
    """Запускает ffmpeg, разбирая прогресс из ``-progress pipe:1``.

    ``-nostdin`` обязателен: без него ffmpeg при неоднозначности задаёт вопрос
    в консоль и ждёт ответа, которого в GUI никто не даст — процесс повисает
    навсегда, и снаружи это выглядит как зависшее приложение.
    """
    binary = find_ffmpeg()
    if binary is None:
        raise FfmpegNotFoundError(
            tr('ffmpeg не найден. Установите его или положите рядом с нативными библиотеками: {0}')
                .format(vendor_dir())
        )

    command = [str(binary), "-hide_banner", "-nostdin", "-y", *args]
    if progress is not None:
        command += ["-progress", "pipe:1", "-nostats"]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )

    stderr_tail: list[str] = []
    try:
        if progress is not None and process.stdout is not None:
            for line in process.stdout:
                if cancel is not None and cancel():
                    process.terminate()
                    raise FfmpegError(tr('операция отменена'))
                match = _PROGRESS_TIME.search(line)
                if match and total_ms > 0:
                    done = int(match.group(1)) / 1000.0
                    progress(max(0.0, min(1.0, done / total_ms)))
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        raise FfmpegError(tr('ffmpeg не завершился за {0:.0f} с').format(timeout)) from exc
    finally:
        if process.stderr is not None:
            stderr_tail = process.stderr.read().splitlines()[-20:]

    if progress is not None:
        progress(1.0)
    return FfmpegResult(process.returncode, "\n".join(stderr_tail))


def iter_scene_changes(
    media: Path, threshold: float = 0.35, timeout: float = 1800.0
) -> Iterator[int]:
    """Моменты смены сцены в миллисекундах.

    Разбирается ``metadata=print``, а не ``showinfo``: первый пишет в stdout
    строку вида ``frame:N pts:N pts_time:S``, второй — в stderr вперемешку с
    логом. ``-fps_mode passthrough`` нужен, чтобы ``select`` не дублировал метки
    (в ffmpeg 6+ это замена устаревшему ``-vsync 0``).
    """
    binary = find_ffmpeg()
    if binary is None:
        raise FfmpegNotFoundError(tr('ffmpeg не найден'))

    command = [
        str(binary), "-hide_banner", "-nostdin", "-i", str(media),
        "-filter:v", f"select='gt(scene,{threshold})',metadata=print:file=-",
        "-fps_mode", "passthrough", "-an", "-sn", "-f", "null", "-",
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    pattern = re.compile(r"pts_time:([0-9.]+)")
    try:
        if process.stdout is not None:
            for line in process.stdout:
                match = pattern.search(line)
                if match:
                    yield int(float(match.group(1)) * 1000)
        process.wait(timeout=timeout)
    finally:
        if process.poll() is None:
            process.kill()
