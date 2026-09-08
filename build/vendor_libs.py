"""Раскладка нативных библиотек (libmpv, libass) в каталог пакета.

Скрипт **не скачивает ничего сам по умолчанию**. Исполняемые библиотеки — это
код, который будет загружен в процесс приложения, поэтому источник и содержимое
должен подтвердить человек. Основной режим работы — распаковка архива, который
вы скачали и проверили самостоятельно.

Использование::

    # что есть и чего не хватает
    python build/vendor_libs.py --check

    # разложить из локально скачанного архива
    python build/vendor_libs.py --from-archive ~/Downloads/mpv-dev-x86_64.7z
    python build/vendor_libs.py --from-archive ~/Downloads/libass.zip

    # посчитать SHA256, чтобы вписать в манифест
    python build/vendor_libs.py --hash ~/Downloads/mpv-dev-x86_64.7z

Где брать (проверьте актуальность и подпись сами):

* **libmpv, Windows** — сборки shinchiro:
  https://github.com/shinchiro/mpv-winbuild-cmake/releases
  (файл вида ``mpv-dev-x86_64-*.7z``; нужна ``libmpv-2.dll``).
  Важно: для сохранения LGPL берите вариант без GPL-компонентов —
  см. риск R6 в спецификации.
* **libass, Windows** — обычно поставляется внутри той же сборки mpv либо
  ставится из MSYS2: ``pacman -S mingw-w64-x86_64-libass`` и затем
  ``mingw64/bin/libass-9.dll``.
* **Linux** — системные пакеты: ``libmpv2`` / ``libmpv-dev`` и ``libass9``.
  Вендоринг не нужен, библиотеки найдутся через ``find_library``.
* **macOS** — ``brew install mpv libass``.

Проверка целостности. Если в ``native_manifest.json`` для файла записан
SHA256, он сверяется. Пустое поле означает «хеш ещё не зафиксирован» — скрипт
посчитает его и предложит вписать, но не станет делать это молча: подставлять
хеш от того же файла, который проверяем, бессмысленно.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).resolve().parent / "native_manifest.json"

#: Какие файлы нужны на каждой платформе.
REQUIRED: dict[str, tuple[str, ...]] = {
    "win64": ("libmpv-2.dll", "libass-9.dll"),
    "linux64": ("libmpv.so.2", "libass.so.9"),
    "macos": ("libmpv.2.dylib", "libass.9.dylib"),
}

#: Имена, под которыми файл может лежать в архиве.
ALIASES: dict[str, tuple[str, ...]] = {
    "libmpv-2.dll": ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll", "libmpv.dll"),
    "libass-9.dll": ("libass-9.dll", "libass.dll", "ass.dll"),
    "libmpv.so.2": ("libmpv.so.2", "libmpv.so"),
    "libass.so.9": ("libass.so.9", "libass.so"),
    "libmpv.2.dylib": ("libmpv.2.dylib", "libmpv.dylib"),
    "libass.9.dylib": ("libass.9.dylib", "libass.dylib"),
}


def platform_tag() -> str:
    if sys.platform == "win32":
        return "win64"
    if sys.platform == "darwin":
        return "macos"
    return "linux64"


def target_dir(tag: str | None = None) -> Path:
    return ROOT / "src" / "sfstudio" / "_native" / (tag or platform_tag())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"version": 1, "files": {}}


def save_manifest(data: dict) -> None:
    MANIFEST.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #


def cmd_check(tag: str) -> int:
    directory = target_dir(tag)
    manifest = load_manifest()
    required = REQUIRED.get(tag, ())

    print(f"Платформа: {tag}")
    print(f"Каталог:   {directory}")
    print()

    missing = 0
    for name in required:
        path = directory / name
        if not path.is_file():
            print(f"  [нет ] {name}")
            missing += 1
            continue

        digest = sha256(path)
        expected = manifest.get("files", {}).get(tag, {}).get(name, {}).get("sha256")
        if not expected:
            print(f"  [есть] {name}  sha256={digest}  (в манифесте не зафиксирован)")
        elif expected == digest:
            print(f"  [ok  ] {name}  хеш совпадает")
        else:
            print(f"  [ПЛОХО] {name}  хеш не совпадает с манифестом!")
            print(f"          ожидался {expected}")
            print(f"          получен  {digest}")
            missing += 1

    print()
    if missing:
        print(f"Не хватает или повреждено: {missing}.")
        print("Разложите библиотеки: python build/vendor_libs.py --from-archive <архив>")
    else:
        print("Все нативные библиотеки на месте.")
    return 1 if missing else 0


def _bsdtar() -> str | None:
    """Путь к bsdtar (libarchive), если он есть.

    На Windows 10+ ``C:\\Windows\\System32\\tar.exe`` — это bsdtar, который
    читает 7z из коробки. Проверять надо именно его, а не первый ``tar`` в
    PATH: в Git Bash первым идёт GNU tar, а он формат 7z не понимает.
    """
    candidates = [Path(r"C:\Windows\System32\tar.exe")] if sys.platform == "win32" else []
    found = shutil.which("bsdtar")
    if found:
        candidates.append(Path(found))
    found = shutil.which("tar")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            out = subprocess.run(
                [str(candidate), "--version"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if "bsdtar" in out.stdout.lower() or "libarchive" in out.stdout.lower():
            return str(candidate)
    return None


def _extract_7z(archive: Path):
    """Распаковывает .7z. Сначала bsdtar, при неудаче — py7zr.

    py7zr не поддерживает фильтр BCJ2, которым сжаты официальные сборки mpv,
    поэтому он именно запасной вариант, а не основной.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tool = _bsdtar()
        extracted = False

        if tool:
            result = subprocess.run(
                [tool, "-xf", str(archive), "-C", tmp], capture_output=True, text=True
            )
            extracted = result.returncode == 0
            if not extracted:
                print(f"  bsdtar не справился: {result.stderr.strip()[:200]}")

        if not extracted:
            try:
                import py7zr
            except ImportError as exc:
                raise SystemExit(
                    "Не удалось распаковать .7z.\n"
                    "Нужен bsdtar (в Windows 10+ это C:\\Windows\\System32\\tar.exe) "
                    "либо py7zr (pip install py7zr).\n"
                    "Либо распакуйте архив сами и укажите каталог: --from-dir <каталог>"
                ) from exc
            with py7zr.SevenZipFile(archive) as sz:
                sz.extractall(path=tmp)

        for path in sorted(Path(tmp).rglob("*")):
            if path.is_file():
                yield str(path.relative_to(tmp)), lambda p=path: p.read_bytes()


def _iter_archive_members(archive: Path):
    """Отдаёт пары (имя внутри архива, функция извлечения)."""
    suffix = archive.suffix.lower()

    if suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    yield info.filename, lambda i=info, z=zf: z.read(i)
        return

    if suffix in (".gz", ".xz", ".bz2", ".tar", ".tgz"):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.isfile():
                    yield member.name, lambda m=member, t=tf: t.extractfile(m).read()
        return

    if suffix == ".7z":
        yield from _extract_7z(archive)
        return

    raise SystemExit(f"неизвестный формат архива: {archive.name}")


def cmd_from_archive(archive: Path, tag: str) -> int:
    if not archive.is_file():
        raise SystemExit(f"файл не найден: {archive}")

    directory = target_dir(tag)
    directory.mkdir(parents=True, exist_ok=True)
    wanted = {alias.lower(): canonical
              for canonical in REQUIRED.get(tag, ())
              for alias in ALIASES.get(canonical, (canonical,))}

    print(f"Архив:   {archive}")
    print(f"SHA256:  {sha256(archive)}")
    print(f"Каталог: {directory}")
    print()

    placed: list[str] = []
    for member_name, read in _iter_archive_members(archive):
        base = Path(member_name).name.lower()
        canonical = wanted.get(base)
        if canonical is None:
            continue
        data = read()
        out = directory / canonical
        out.write_bytes(data)
        placed.append(f"{canonical}  ({len(data) / 1024 / 1024:.1f} МБ, из {member_name})")

    if not placed:
        print("В архиве не нашлось нужных файлов. Ожидались:")
        for canonical in REQUIRED.get(tag, ()):
            print(f"  {canonical}  (или {', '.join(ALIASES.get(canonical, ()))})")
        return 1

    for line in placed:
        print(f"  положено: {line}")
    print()
    return cmd_check(tag)


def cmd_from_dir(source: Path, tag: str) -> int:
    if not source.is_dir():
        raise SystemExit(f"каталог не найден: {source}")

    directory = target_dir(tag)
    directory.mkdir(parents=True, exist_ok=True)
    placed = 0
    for canonical in REQUIRED.get(tag, ()):
        for alias in ALIASES.get(canonical, (canonical,)):
            for candidate in source.rglob(alias):
                shutil.copy2(candidate, directory / canonical)
                print(f"  положено: {canonical} <- {candidate}")
                placed += 1
                break
            else:
                continue
            break
    if not placed:
        print("Нужных файлов в каталоге не нашлось.")
        return 1
    print()
    return cmd_check(tag)


def cmd_hash(path: Path) -> int:
    print(f"{sha256(path)}  {path.name}")
    return 0


def cmd_freeze(tag: str) -> int:
    """Записывает текущие хеши в манифест — после того, как вы их проверили."""
    manifest = load_manifest()
    files = manifest.setdefault("files", {}).setdefault(tag, {})
    directory = target_dir(tag)
    for name in REQUIRED.get(tag, ()):
        path = directory / name
        if path.is_file():
            files[name] = {"sha256": sha256(path), "size": path.stat().st_size}
            print(f"  зафиксирован {name}")
    save_manifest(manifest)
    print(f"\nМанифест обновлён: {MANIFEST}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Раскладка libmpv и libass в каталог пакета",
        epilog="Скрипт ничего не скачивает: источник вы выбираете и проверяете сами.",
    )
    parser.add_argument("--platform", default=platform_tag(), choices=sorted(REQUIRED))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="что есть и чего не хватает")
    group.add_argument("--from-archive", type=Path, metavar="АРХИВ")
    group.add_argument("--from-dir", type=Path, metavar="КАТАЛОГ")
    group.add_argument("--hash", type=Path, metavar="ФАЙЛ", help="посчитать SHA256")
    group.add_argument("--freeze", action="store_true", help="записать хеши в манифест")
    args = parser.parse_args(argv)

    if args.hash:
        return cmd_hash(args.hash)
    if args.from_archive:
        return cmd_from_archive(args.from_archive, args.platform)
    if args.from_dir:
        return cmd_from_dir(args.from_dir, args.platform)
    if args.freeze:
        return cmd_freeze(args.platform)
    return cmd_check(args.platform)


if __name__ == "__main__":
    sys.exit(main())
