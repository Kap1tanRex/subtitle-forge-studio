"""Загрузка библиотек для счёта на видеокарте.

Библиотеки CUDA лежат на PyPI обычными пакетами, и берём мы их оттуда же,
откуда взял бы ``pip`` — только без самого pip: в собранной программе его нет,
а требовать от человека установленный Python ради галочки «считать на
видеокарте» неправильно. Пакет — это zip; нужные файлы из него достаются
напрямую.

Качается **только cuBLAS**. cuDNN ставится тем же набором пакетов и весит ещё
гигабайт, но CTranslate2 для Whisper к нему не обращается — проверено запуском
без него. Полтора гигабайта разницы того стоят.

Загрузка идёт во временный файл рядом с целевым каталогом и переносится
на место только целиком: оборванная закачка не должна оставить обрубок,
который потом сойдёт за установленную библиотеку и упадёт посреди работы.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.services.asr.base import (
    CancelToken,
    ProgressReporter,
    RecognitionCancelled,
    RecognitionError,
)

__all__ = [
    "CUDA_PACKAGES",
    "LibraryPackage",
    "download_cuda_libraries",
    "installed_libraries",
]


@dataclass(frozen=True, slots=True)
class LibraryPackage:
    """Пакет с PyPI и файлы, которые из него нужны."""

    project: str
    files: tuple[str, ...]
    size_mb: int
    note: str = ""


#: Что качаем ради счёта на видеокарте NVIDIA.
CUDA_PACKAGES: tuple[LibraryPackage, ...] = (
    LibraryPackage(
        project="nvidia-cublas-cu12",
        files=("cublas64_12.dll", "cublasLt64_12.dll"),
        size_mb=735,
        note=tr('Матричные операции на видеокарте. Без неё CUDA не работает.'),
    ),
)

#: Метка платформы в имени файла пакета. Пакеты собираются под конкретную
#: систему, и брать линуксовый на Windows бессмысленно.
_WHEEL_TAG = "win_amd64"

_PYPI = "https://pypi.org/pypi/{project}/json"

#: Откуда допустимо качать. Адрес приходит из указателя строкой и
#: вести может куда угодно; на деле файлы PyPI лежат только здесь.
_ALLOWED_HOSTS = frozenset({"files.pythonhosted.org", "pypi.org"})

#: Размер куска при чтении. Мелкие куски — частые отчёты о прогрессе и
#: быстрая отмена; слишком мелкие — накладные расходы на каждый вызов.
_CHUNK = 1 << 20


def installed_libraries(folder: Path | None) -> set[str]:
    """Какие файлы библиотек уже лежат в каталоге."""
    if folder is None or not Path(folder).is_dir():
        return set()
    return {entry.name for entry in Path(folder).glob("*.dll")}


def cuda_ready(folder: Path | None) -> bool:
    """Все ли нужные файлы на месте."""
    have = installed_libraries(folder)
    return all(name in have for pkg in CUDA_PACKAGES for name in pkg.files)


def download_cuda_libraries(
    folder: Path,
    *,
    progress: ProgressReporter | None = None,
    cancel: CancelToken | None = None,
) -> list[Path]:
    """Скачивает библиотеки CUDA в каталог. Возвращает пути к файлам."""
    folder = Path(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecognitionError(tr('не удалось создать каталог {0}: {1}')
            .format(folder, exc)) from exc

    written: list[Path] = []
    for index, package in enumerate(CUDA_PACKAGES):
        base = index / len(CUDA_PACKAGES)
        span = 1.0 / len(CUDA_PACKAGES)

        def report(share: float, note: str, _b=base, _s=span) -> None:
            if progress is not None:
                progress(_b + _s * share, note)

        url, digest = _wheel_url(package.project)
        report(0.0, tr('загрузка {0}').format(package.project))
        archive = _download(url, folder, report, cancel, digest)
        try:
            report(0.95, tr('распаковка'))
            written.extend(_extract(archive, package.files, folder))
        finally:
            archive.unlink(missing_ok=True)

    if progress is not None:
        progress(1.0, tr('библиотеки установлены'))
    return written


def _wheel_url(project: str) -> tuple[str, str]:
    """Ссылка на пакет под эту платформу — из указателя PyPI.

    Адрес не собирается из шаблона: у файлов на PyPI в пути стоит хэш
    содержимого, и предсказать его нельзя. Указатель заодно даёт номер
    последней версии, так что зашивать её в программу тоже не нужно.
    """
    try:
        with urllib.request.urlopen(_PYPI.format(project=project), timeout=30) as answer:
            data = json.load(answer)
    except Exception as exc:
        raise RecognitionError(
            f"не удалось узнать адрес пакета «{project}»: {exc}\n"
            "Проверьте подключение к сети."
        ) from exc

    version = (data.get("info") or {}).get("version", "")
    for entry in data.get("urls") or []:
        found = _pick(entry)
        if found is not None:
            return found

    # Свежая версия может выйти без сборки под Windows — тогда ищем в архиве
    # прошлых выпусков, а не сдаёмся: работающая старая версия лучше отказа.
    for files in reversed(list((data.get("releases") or {}).values())):
        for entry in files:
            found = _pick(entry)
            if found is not None:
                return found

    raise RecognitionError(
        tr('для «{0}» нет сборки под Windows (версия {1}). Установите библиотеку вручную: '
               'pip install ').format(project, version) + project
    )


def _pick(entry: dict) -> tuple[str, str] | None:
    """Ссылка и sha256 из записи указателя, если это нужный нам пакет.

    Адрес проверяется на схему и на то, что он ведёт к хранилищу пакетов:
    в указателе он приходит строкой, и вести она может куда угодно.
    """
    name = str(entry.get("filename", ""))
    if _WHEEL_TAG not in name or not name.endswith(".whl"):
        return None

    url = str(entry.get("url", ""))
    host = urllib.parse.urlparse(url)
    if host.scheme != "https" or host.hostname not in _ALLOWED_HOSTS:
        return None

    digest = str((entry.get("digests") or {}).get("sha256", ""))
    return url, digest


def _download(
    url: str,
    folder: Path,
    report,
    cancel: CancelToken | None,
    digest: str = "",
) -> Path:
    """Качает файл во временный рядом с целью. Возвращает путь к нему.

    Скачанное сверяется с ``sha256`` из указателя PyPI. Это не подпись — обе
    величины приходят из одного источника, — но зато ловит и повреждение при
    передаче, и подмену файла на зеркале, а стоит одного прохода по уже
    прочитанным байтам.
    """
    handle, temp_name = tempfile.mkstemp(dir=folder, prefix=".download-", suffix=".zip")
    os.close(handle)  # mkstemp отдаёт открытый дескриптор, а пишем мы по пути
    temp = Path(temp_name)
    digest = digest.strip().lower()
    running = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as answer, temp.open("wb") as out:
            total = int(answer.headers.get("Content-Length") or 0)
            done = 0
            while chunk := answer.read(_CHUNK):
                if cancel is not None and cancel.cancelled:
                    raise RecognitionCancelled(tr('загрузка библиотек прервана'))
                out.write(chunk)
                running.update(chunk)
                done += len(chunk)
                if total:
                    report(
                        0.9 * done / total,
                        tr('загрузка: {0:.0f} из {1:.0f} МБ').format(
                            done / 1024 / 1024, total / 1024 / 1024),
                    )
    except RecognitionCancelled:
        temp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise RecognitionError(tr('не удалось скачать библиотеку: {0}').format(exc)) from exc

    if digest and running.hexdigest() != digest:
        temp.unlink(missing_ok=True)
        raise RecognitionError(
            tr('скачанный файл не совпал с контрольной суммой из указателя PyPI. Повторите '
                   'загрузку; если повторяется — установите библиотеку командой pip install.')
        )
    return temp


def _extract(archive: Path, wanted: tuple[str, ...], folder: Path) -> list[Path]:
    """Достаёт нужные файлы из пакета.

    Имена внутри пакета сопоставляются по последнему сегменту пути: раскладка
    внутри у разных версий разная, а имя файла библиотеки постоянно. Путь при
    этом не используется — распаковка «как лежит» позволила бы архиву с
    подделанными именами записать файл куда угодно.
    """
    written: list[Path] = []
    with zipfile.ZipFile(archive) as zf:
        by_name = {Path(item.filename).name: item for item in zf.infolist()}
        missing = [name for name in wanted if name not in by_name]
        if missing:
            raise RecognitionError(
                tr('в пакете нет ожидаемых файлов: ') + ", ".join(missing)
            )
        for name in wanted:
            target = folder / name
            staging = folder / (name + ".part")
            with zf.open(by_name[name]) as source, staging.open("wb") as out:
                shutil.copyfileobj(source, out, _CHUNK)
            staging.replace(target)
            written.append(target)
    return written
