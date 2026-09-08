"""Поиск нативных библиотек: libmpv и libass.

Порядок поиска — от самого предсказуемого к самому случайному:

1. переменная окружения (``SFSTUDIO_LIBMPV`` / ``SFSTUDIO_LIBASS``) — прямой
   путь, которым пользователь может перекрыть всё остальное;
2. вендоринг внутри пакета (``sfstudio/_native/<платформа>/``) — то, что
   кладёт ``build/vendor_libs.py`` и что попадёт в релизную сборку;
3. системные пути (``ctypes.util.find_library`` и типичные каталоги).

Зачем это отдельным модулем, а не парой строк в обёртке: отсутствие библиотеки
должно давать **точное** сообщение о том, где искали и что делать. «DLL not
found» без подробностей — это обращение в поддержку, а список проверенных
путей пользователь чинит сам за минуту.
"""

from __future__ import annotations

import ctypes.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

__all__ = ["NativeLibrary", "libass_spec", "libmpv_spec", "platform_tag", "vendor_dir"]


def platform_tag() -> str:
    """Каталог вендоринга для текущей платформы."""
    if sys.platform == "win32":
        return "win64" if sys.maxsize > 2**32 else "win32"
    if sys.platform == "darwin":
        return "macos"
    return "linux64"


def vendor_dir() -> Path:
    """Каталог с вендоренными библиотеками внутри пакета."""
    return Path(__file__).resolve().parent.parent / "_native" / platform_tag()


@dataclass(slots=True)
class NativeLibrary:
    """Описание искомой библиотеки и результат поиска."""

    name: str
    env_var: str
    #: Имена файлов по платформам, в порядке предпочтения.
    filenames: dict[str, tuple[str, ...]]
    #: Имя для ``ctypes.util.find_library`` (без префикса lib и расширения).
    soname: str
    #: Где искали — заполняется при поиске, попадает в сообщение об ошибке.
    searched: list[str] = field(default_factory=list)

    #: Сколько путей показывать в сообщении об ошибке.
    LOG_HEAD: ClassVar[int] = 6

    def candidates(self) -> tuple[str, ...]:
        return self.filenames.get(platform_tag(), ())

    def search_log(self) -> str:
        """Полный перечень проверенных путей — для «Скопировать диагностику»."""
        return "\n".join(self.searched)

    def locate(self) -> Path | None:
        """Ищет библиотеку. ``None``, если не нашлась."""
        self.searched = []

        override = os.environ.get(self.env_var)
        if override:
            path = Path(override)
            self.searched.append(f"{self.env_var}={override}")
            if path.is_file():
                return path

        directory = vendor_dir()
        for filename in self.candidates():
            candidate = directory / filename
            self.searched.append(str(candidate))
            if candidate.is_file():
                return candidate

        found = ctypes.util.find_library(self.soname)
        self.searched.append(f"find_library({self.soname!r}) -> {found or 'не найдено'}")
        if found:
            return Path(found)

        for directory in _system_dirs():
            for filename in self.candidates():
                candidate = directory / filename
                self.searched.append(str(candidate))
                if candidate.is_file():
                    return candidate

        return None

    def load(self):
        """Загружает библиотеку через ctypes.

        Бросает ``OSError`` с указанием, куда положить файл, и с коротким
        перечнем проверенных мест. Перечень намеренно усечён: на реальной
        машине PATH содержит десятки каталогов, и полный список превращает
        сообщение в стену текста, из которой ничего не выловить.
        Целиком его отдаёт :meth:`search_log`.
        """
        path = self.locate()
        if path is None:
            head = self.searched[: self.LOG_HEAD]
            listing = "\n  ".join(head)
            hidden = len(self.searched) - len(head)
            tail = f"\n  … и ещё {hidden} путей из PATH" if hidden > 0 else ""
            raise OSError(
                f"{self.name} не найдена.\n"
                f"Положите файл в {vendor_dir()} "
                f"или укажите путь в переменной {self.env_var}.\n"
                f"Проверено:\n  {listing}{tail}"
            )
        if sys.platform == "win32":
            # Соседние DLL (зависимости libmpv) должны находиться рядом:
            # без этого загрузка падает уже на них, а не на самой библиотеке.
            os.add_dll_directory(str(path.parent))
        return ctypes.CDLL(str(path))


def _system_dirs() -> tuple[Path, ...]:
    if sys.platform == "win32":
        raw = os.environ.get("PATH", "")
        return tuple(Path(p) for p in raw.split(os.pathsep) if p)
    if sys.platform == "darwin":
        return (
            Path("/opt/homebrew/lib"),
            Path("/usr/local/lib"),
            Path("/usr/lib"),
        )
    return (
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/usr/lib64"),
        Path("/usr/lib"),
        Path("/usr/local/lib"),
    )


def libmpv_spec() -> NativeLibrary:
    return NativeLibrary(
        name="libmpv",
        env_var="SFSTUDIO_LIBMPV",
        soname="mpv",
        filenames={
            "win64": ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll"),
            "win32": ("libmpv-2.dll", "mpv-1.dll"),
            "linux64": ("libmpv.so.2", "libmpv.so.1", "libmpv.so"),
            "macos": ("libmpv.2.dylib", "libmpv.dylib"),
        },
    )


def libass_spec() -> NativeLibrary:
    return NativeLibrary(
        name="libass",
        env_var="SFSTUDIO_LIBASS",
        soname="ass",
        filenames={
            "win64": ("libass-9.dll", "libass.dll", "ass.dll"),
            "win32": ("libass-9.dll", "libass.dll"),
            "linux64": ("libass.so.9", "libass.so.5", "libass.so"),
            "macos": ("libass.9.dylib", "libass.dylib"),
        },
    )


def diagnose() -> dict[str, str]:
    """Состояние нативных зависимостей — для ``--version`` и меню «Диагностика»."""
    report: dict[str, str] = {"платформа": platform_tag(), "каталог": str(vendor_dir())}
    for spec in (libmpv_spec(), libass_spec()):
        path = spec.locate()
        report[spec.name] = str(path) if path else "не найдена"
    return report
