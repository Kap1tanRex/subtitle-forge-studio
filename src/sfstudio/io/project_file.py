"""Чтение и запись файла проекта.

Формат — ZIP, внутри:

* ``project.json`` — манифест: версия, имя, разрешение, частота кадров, путь
  к медиа и состояние работы;
* ``subtitles.ass`` — сами субтитры со стилями, дорожками и акторами.

ZIP, а не один JSON с текстом внутри: субтитры остаются **настоящим ASS**
внутри контейнера, и вытащить их обычным архиватором можно без нашей
программы. Это же оставляет место для вложений (шрифты, заметки), не ломая
формат — старая версия просто не увидит новых записей.

Путь к медиа хранится дважды: относительно файла проекта и абсолютно.
Относительный делает папку с проектом переносимой — самый частый случай, когда
проект отдают другому человеку вместе с видео. Абсолютный спасает, когда
проект лежит в одном месте, а видео в другом, и первый путь ведёт в никуда.

Сама модель проекта — в :mod:`sfstudio.core.project`: ядро не зависит от
ввода-вывода, и это проверяется тестом ``test_layering``.
"""

from __future__ import annotations

import contextlib
import json
import zipfile
from fractions import Fraction
from pathlib import Path

from sfstudio.core.project import (
    FORMAT_VERSION,
    MANIFEST_NAME,
    PROJECT_SUFFIX,
    SUBTITLES_NAME,
    Project,
    ProjectError,
    ProjectState,
    _as_int,
    now_stamp,
)
from sfstudio.io.formats.ass import read_ass, write_ass

__all__ = ["load_project", "save_project"]


# --------------------------------------------------------------------------- #
# Запись
# --------------------------------------------------------------------------- #


def save_project(project: Project, path: Path) -> Path:
    """Записывает проект. Возвращает итоговый путь.

    Запись атомарная: сначала во временный файл рядом, затем переименование.
    Прерванная запись (нет места, выключили питание) иначе оставила бы
    обрезанный архив на месте рабочего проекта — то есть потерю всей работы,
    а не одного сеанса.
    """
    path = Path(path)
    if path.suffix.lower() != PROJECT_SUFFIX:
        path = path.with_suffix(PROJECT_SUFFIX)
    path.parent.mkdir(parents=True, exist_ok=True)

    project.modified = now_stamp()
    if not project.created:
        project.created = project.modified

    manifest = {
        "format": "sfstudio-project",
        "version": FORMAT_VERSION,
        "name": project.name,
        "created": project.created,
        "modified": project.modified,
        "resolution": {"width": project.resolution[0], "height": project.resolution[1]},
        "fps": {
            "numerator": project.fps.numerator,
            "denominator": project.fps.denominator,
        },
        "media": _media_entry(project, path),
        "state": project.state.to_dict(),
        "subtitles": SUBTITLES_NAME,
    }

    temporary = path.with_name(path.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            archive.writestr(SUBTITLES_NAME, write_ass(project.document))
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ProjectError(f"не удалось записать проект: {exc}") from exc

    project.path = path
    return path


def _media_entry(project: Project, project_path: Path) -> dict | None:
    """Путь к медиа: относительный, если получится, и абсолютный всегда."""
    if project.media_path is None:
        return None
    absolute = Path(project.media_path).resolve()
    entry: dict[str, str] = {"absolute": str(absolute)}
    # Медиа вне папки проекта — законный случай, относительного пути тогда
    # просто не будет. На разных дисках он тем более невозможен.
    with contextlib.suppress(ValueError):
        entry["relative"] = str(absolute.relative_to(project_path.resolve().parent))
    return entry


# --------------------------------------------------------------------------- #
# Чтение
# --------------------------------------------------------------------------- #


def load_project(path: Path) -> Project:
    """Читает проект."""
    path = Path(path)
    if not path.is_file():
        raise ProjectError(f"файл проекта не найден: {path}")

    try:
        with zipfile.ZipFile(path) as archive:
            manifest_raw = archive.read(MANIFEST_NAME).decode("utf-8")
            names = set(archive.namelist())
            subtitles_name = _subtitles_name(manifest_raw, names)
            subtitles = archive.read(subtitles_name).decode("utf-8-sig")
    except KeyError as exc:
        raise ProjectError(f"в проекте нет обязательной части: {exc}") from exc
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError) as exc:
        raise ProjectError(f"не удалось прочитать проект: {exc}") from exc

    try:
        manifest = json.loads(manifest_raw)
    except ValueError as exc:
        raise ProjectError(f"манифест проекта повреждён: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ProjectError("манифест проекта повреждён: ожидался объект")

    version = _as_int(manifest.get("version"), 0)
    if version > FORMAT_VERSION:
        raise ProjectError(
            f"проект создан более новой версией программы (формат {version}, "
            f"поддерживается {FORMAT_VERSION}). Обновите программу."
        )

    document = read_ass(subtitles)
    project = Project(
        name=str(manifest.get("name") or path.stem),
        document=document,
        fps=_fps_from(manifest.get("fps")),
        state=ProjectState.from_dict(manifest.get("state")),
        created=str(manifest.get("created") or ""),
        modified=str(manifest.get("modified") or ""),
        path=path,
    )

    resolution = manifest.get("resolution")
    if isinstance(resolution, dict):
        width = _as_int(resolution.get("width"), 0)
        height = _as_int(resolution.get("height"), 0)
        if width > 0 and height > 0:
            project.set_resolution(width, height)

    project.media_path = _resolve_media(manifest.get("media"), path)
    return project


def _subtitles_name(manifest_raw: str, names: set[str]) -> str:
    """Имя файла субтитров внутри архива.

    Берётся из манифеста, но с проверкой: битое или подложное имя не должно
    уводить чтение к произвольному пути внутри архива.
    """
    try:
        manifest = json.loads(manifest_raw)
        candidate = str(manifest.get("subtitles") or SUBTITLES_NAME)
    except ValueError:
        candidate = SUBTITLES_NAME
    if candidate in names:
        return candidate
    if SUBTITLES_NAME in names:
        return SUBTITLES_NAME
    raise ProjectError("в проекте нет файла субтитров")


def _resolve_media(raw: object, project_path: Path) -> Path | None:
    """Находит медиа: сначала рядом с проектом, потом по абсолютному пути.

    Порядок именно такой. Проект вместе с видео часто копируют в другое место,
    и тогда абсолютный путь ведёт на старую машину, а относительный — на
    настоящий файл. Обратный порядок открывал бы чужую копию, если она
    осталась на прежнем месте.
    """
    if not isinstance(raw, dict):
        return None

    relative = raw.get("relative")
    if relative:
        candidate = (project_path.parent / str(relative)).resolve()
        if candidate.is_file():
            return candidate

    absolute = raw.get("absolute")
    if absolute:
        candidate = Path(str(absolute))
        if candidate.is_file():
            return candidate
        # Файла нет ни там, ни там. Путь всё равно возвращаем: окно покажет
        # его в сообщении «медиа не найдено», и станет понятно, что искать.
        return candidate
    return None


# --------------------------------------------------------------------------- #
# Мелочи
# --------------------------------------------------------------------------- #


def _fps_from(raw: object) -> Fraction:
    """Частота кадров из манифеста. Мусор даёт 25, а не исключение."""
    if isinstance(raw, dict):
        numerator = _as_int(raw.get("numerator"), 0)
        denominator = _as_int(raw.get("denominator"), 0)
        if numerator > 0 and denominator > 0:
            return Fraction(numerator, denominator)
    if isinstance(raw, (int, float)) and raw > 0:
        return Fraction(raw).limit_denominator(100000)
    return Fraction(25)
