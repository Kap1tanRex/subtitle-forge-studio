"""Загрузка whisper.cpp — второго движка распознавания.

Зачем он нужен рядом с faster-whisper: тот требует пакетов Python и
CTranslate2, а whisper.cpp — это один исполняемый файл и один файл модели.
Там, где ставить пакеты нельзя или не хочется, работает только он.

**Про видеокарты не-NVIDIA — прямо.** whisper.cpp умеет считать на Vulkan, то
есть на любой карте, но **готовых сборок с Vulkan проект не публикует**:
проверены двенадцать последних выпусков, среди файлов для Windows есть только
процессорные (обычная и с BLAS) и две с cuBLAS, то есть снова под NVIDIA.
Поэтому владельцу Radeon или Arc программа честно говорит, что готового пути
нет и нужен самостоятельно собранный бинарник, — а не обещает Vulkan, которого
взять неоткуда.

Загруженное кладётся в каталог из настроек, рядом с моделями и библиотеками:
всё скачанное живёт в одном месте, и удалить его можно одной папкой.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sfstudio.services.asr.base import (
    CancelToken,
    ProgressReporter,
    RecognitionCancelled,
    RecognitionError,
)

__all__ = [
    "WHISPER_CPP_BUILDS",
    "BinaryBuild",
    "download_whisper_cpp",
    "installed_whisper_cpp",
    "whisper_cpp_dir",
]

#: Где на диске лежит скачанный whisper.cpp — подпапка каталога загрузок.
FOLDER_NAME = "whisper-cpp"

#: Выпуск, в котором лежат сборки под Windows. Не «последний»: свежие теги
#: выходят без бинарников вовсе, и «взять последний» означало бы получить
#: релиз без единого нужного файла.
RELEASE_TAG = "b4938"

_RELEASE_URL = "https://api.github.com/repos/ggml-org/whisper.cpp/releases/tags/{tag}"

#: Куда допустимо ходить за файлом выпуска. Скачанное запускается как
#: программа, поэтому адрес из ответа сервера проверяется по списку.
_ALLOWED_HOSTS = frozenset({
    "github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com", "api.github.com",
})

_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class BinaryBuild:
    """Один вариант сборки."""

    key: str
    title: str
    asset: str
    size_mb: int
    note: str = ""
    #: Нужна ли к ней видеокарта NVIDIA.
    needs_cuda: bool = False


#: Что предлагаем скачать. Сборка с cuBLAS весит 640 МБ и нужна только тем,
#: у кого NVIDIA, — а у них уже работает faster-whisper, который быстрее.
#: Поэтому по умолчанию предлагается процессорная с BLAS.
WHISPER_CPP_BUILDS: tuple[BinaryBuild, ...] = (
    BinaryBuild(
        key="cpu",
        title="Для процессора",
        asset="whisper-bin-x64.zip",
        size_mb=8,
        note="Самая маленькая. Работает везде.",
    ),
    BinaryBuild(
        key="blas",
        title="Для процессора, с ускорением BLAS",
        asset="whisper-blas-bin-x64.zip",
        size_mb=20,
        note="Заметно быстрее обычной на многоядерном процессоре.",
    ),
    BinaryBuild(
        key="cuda",
        title="Для видеокарты NVIDIA (cuBLAS)",
        asset="whisper-cublas-12.4.0-bin-x64.zip",
        size_mb=640,
        note=(
            "Нужна только при отказе от faster-whisper: тот на той же карте "
            "обычно быстрее и весит меньше."
        ),
        needs_cuda=True,
    ),
)

#: Имена исполняемых файлов внутри архива, от нового к старому.
_EXECUTABLES = ("whisper-cli.exe", "main.exe", "whisper-cli", "main")


def whisper_cpp_dir(root: Path) -> Path:
    """Каталог, куда кладётся whisper.cpp."""
    return Path(root) / FOLDER_NAME


def installed_whisper_cpp(root: Path | None) -> Path | None:
    """Путь к скачанному бинарнику. ``None`` — не скачан."""
    if root is None:
        return None
    folder = whisper_cpp_dir(root)
    if not folder.is_dir():
        return None
    for name in _EXECUTABLES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    # Архивы разных выпусков раскладываются по-разному: где-то файлы лежат в
    # корне, где-то в подпапке. Ищем вглубь, а не гадаем про раскладку.
    for name in _EXECUTABLES:
        found = next(folder.rglob(name), None)
        if found is not None:
            return found
    return None


def download_whisper_cpp(
    root: Path,
    build: str = "blas",
    *,
    progress: ProgressReporter | None = None,
    cancel: CancelToken | None = None,
) -> Path:
    """Скачивает и распаковывает whisper.cpp. Возвращает путь к бинарнику."""
    chosen = next((b for b in WHISPER_CPP_BUILDS if b.key == build), None)
    if chosen is None:
        raise RecognitionError(
            f"неизвестный вариант сборки «{build}». Доступны: "
            + ", ".join(b.key for b in WHISPER_CPP_BUILDS)
        )

    folder = whisper_cpp_dir(root)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecognitionError(f"не удалось создать каталог {folder}: {exc}") from exc

    if progress is not None:
        progress(0.0, f"поиск сборки {chosen.title}")
    url = _asset_url(chosen.asset)

    archive = _download(url, folder, progress, cancel)
    try:
        if progress is not None:
            progress(0.95, "распаковка")
        _unpack(archive, folder)
    finally:
        archive.unlink(missing_ok=True)

    binary = installed_whisper_cpp(root)
    if binary is None:
        raise RecognitionError(
            "в архиве не нашлось исполняемого файла whisper.cpp. "
            "Возможно, изменился состав сборки."
        )
    # На не-Windows архив приходит без бита исполнения.
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    if progress is not None:
        progress(1.0, "whisper.cpp установлен")
    return binary


def _asset_url(asset: str) -> str:
    """Ссылка на файл выпуска. Берётся из описания релиза, а не собирается."""
    request = urllib.request.Request(
        _RELEASE_URL.format(tag=RELEASE_TAG),
        headers={"User-Agent": "SubtitleForgeStudio"},
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as answer:
            data = json.load(answer)
    except Exception as exc:
        raise RecognitionError(
            f"не удалось получить список файлов выпуска: {exc}\n"
            "Проверьте подключение к сети."
        ) from exc

    for item in data.get("assets", []):
        if item.get("name") != asset:
            continue
        url = str(item.get("browser_download_url", ""))
        # Адрес приходит из ответа сервера строкой и вести может куда
        # угодно. Файл потом запускается как программа, так что «откуда
        # скачали» — не мелочь.
        parts = urllib.parse.urlparse(url)
        if parts.scheme != "https" or parts.hostname not in _ALLOWED_HOSTS:
            raise RecognitionError(
                f"ссылка на файл выпуска ведёт на неожиданный адрес: {url}"
            )
        return url
    raise RecognitionError(
        f"в выпуске {RELEASE_TAG} нет файла «{asset}». "
        "Скачайте сборку вручную и укажите путь к ней в настройках."
    )


def _download(
    url: str,
    folder: Path,
    progress: ProgressReporter | None,
    cancel: CancelToken | None,
) -> Path:
    handle, name = tempfile.mkstemp(dir=folder, prefix=".download-", suffix=".zip")
    os.close(handle)
    temp = Path(name)
    request = urllib.request.Request(url, headers={"User-Agent": "SubtitleForgeStudio"})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer, temp.open("wb") as out:
            total = int(answer.headers.get("Content-Length") or 0)
            done = 0
            while chunk := answer.read(_CHUNK):
                if cancel is not None and cancel.cancelled:
                    raise RecognitionCancelled("загрузка прервана")
                out.write(chunk)
                done += len(chunk)
                if progress is not None and total:
                    progress(
                        0.9 * done / total,
                        f"загрузка: {done / 1024 / 1024:.0f} из "
                        f"{total / 1024 / 1024:.0f} МБ",
                    )
    except RecognitionCancelled:
        temp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise RecognitionError(f"не удалось скачать whisper.cpp: {exc}") from exc
    return temp


def _unpack(archive: Path, folder: Path) -> None:
    """Распаковывает архив, не выпуская его за пределы каталога.

    Имена внутри архива приходят из сети, и путь вида ``../../`` в них
    записал бы файл куда угодно. Поэтому каждый путь проверяется на выход за
    границу — распаковка «как лежит» тут недопустима.
    """
    root = folder.resolve()
    with zipfile.ZipFile(archive) as zf:
        for item in zf.infolist():
            if item.is_dir():
                continue
            target = (root / item.filename).resolve()
            if not target.is_relative_to(root):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(item) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out, _CHUNK)
