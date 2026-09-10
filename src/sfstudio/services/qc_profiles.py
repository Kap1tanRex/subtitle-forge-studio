"""Профили проверок из файлов.

Требования у каждого заказчика свои, и присылают их в виде списка чисел:
столько знаков в строке, столько в секунду, не ближе стольких кадров к
склейке. Держать это в коде программы неправильно — профиль меняется чаще,
чем выходят версии.

Формат — json в каталоге профилей, рядом с темами и плагинами. Устроен так же,
как файл темы: имя, название, значения. Незнакомые ключи пропускаются, чтобы
профиль, написанный под будущую версию, не оказался нечитаемым; неверные
значения — тоже, но о них сообщается в поле ``error``.

Файл можно отдать заказчику: по нему видно, по каким требованиям сдавалась
работа.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.services.qc import PROFILES, QcProfile

__all__ = [
    "SUFFIX",
    "ProfilePack",
    "available",
    "discover",
    "example_text",
    "read_pack",
]

#: Расширение файла профиля. Двойное, чтобы отличать от прочих json.
SUFFIX = ".sfsqc.json"

#: Поля профиля, которые файл вправе задавать.
_FIELDS = {f.name: f.type for f in fields(QcProfile)}

#: Поля, которые считаются числами; остальные — да/нет.
_NUMERIC = {
    "max_cps", "max_line_length", "max_lines", "min_duration_ms",
    "max_duration_ms", "min_gap_frames", "shot_change_frames",
    "min_line_length",
}


@dataclass(frozen=True, slots=True)
class ProfilePack:
    """Профиль, прочитанный из файла."""

    name: str
    profile: QcProfile
    path: Path | None = None
    #: Причина, если файл прочитать не удалось.
    error: str = ""

    @property
    def builtin(self) -> bool:
        return self.path is None

    @property
    def title(self) -> str:
        return self.profile.name


def read_pack(path: Path) -> ProfilePack:
    """Читает файл профиля. Исключений не бросает."""
    path = Path(path)
    stem = path.name[: -len(SUFFIX)] if path.name.endswith(SUFFIX) else path.stem

    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return ProfilePack(stem, QcProfile(name=stem), path,
                           tr('файл не читается: {0}').format(exc.strerror or exc))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return ProfilePack(stem, QcProfile(name=stem), path, tr('ошибка в json: {0}').format(exc))
    if not isinstance(data, dict):
        return ProfilePack(stem, QcProfile(name=stem), path, tr('ожидался объект json'))

    name = str(data.get("name") or stem).strip() or stem
    title = str(data.get("title") or name)

    values: dict[str, object] = {"name": title}
    bad: list[str] = []
    for key, value in data.items():
        if key in ("name", "title") or key not in _FIELDS:
            continue
        if value is None:
            values[key] = None  # «правило выключено» — законное значение
            continue
        if key in _NUMERIC:
            try:
                values[key] = float(value) if key == "max_cps" else int(value)
            except (TypeError, ValueError):
                bad.append(key)
        else:
            values[key] = bool(value)

    pack = ProfilePack(name, QcProfile(**values), path)
    if bad:
        return ProfilePack(name, pack.profile, path,
                           tr('не числа: ') + ", ".join(sorted(bad)))
    return pack


def discover(folder: Path | None) -> list[ProfilePack]:
    """Все профили из каталога, по алфавиту."""
    if folder is None or not Path(folder).is_dir():
        return []
    found = [read_pack(entry) for entry in sorted(Path(folder).glob("*" + SUFFIX))]
    return sorted(found, key=lambda p: p.title.lower())


def available(folder: Path | None) -> dict[str, ProfilePack]:
    """Встроенные профили плюс файловые. Файл с именем встроенного заменяет его."""
    packs: dict[str, ProfilePack] = {
        key: ProfilePack(key, profile) for key, profile in PROFILES.items()
    }
    for pack in discover(folder):
        packs[pack.name] = pack
    return packs


def example_text() -> str:
    """Образец файла профиля — его кладут человеку по кнопке в настройках."""
    sample = {
        "name": tr('мой-заказчик'),
        "title": tr('Требования заказчика'),
        "max_cps": 17,
        "max_line_length": 42,
        "max_lines": 2,
        "min_duration_ms": 1000,
        "max_duration_ms": 7000,
        "min_gap_frames": 2,
        "shot_change_frames": 2,
        "min_line_length": 8,
        "cps_counts_spaces": False,
        "check_repeats": True,
        tr('_подсказка'): (
            tr('null вместо числа выключает правило; shot_change_frames требует ключевых '
                   'кадров видео')
        ),
    }
    return json.dumps(sample, ensure_ascii=False, indent=2) + "\n"
