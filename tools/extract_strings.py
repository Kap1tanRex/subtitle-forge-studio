"""Сбор строк интерфейса в каталог перевода.

Обходит исходники, собирает строковые литералы с кириллицей и складывает их в
JSON, где ключ — русская строка, а значение — перевод. Уже переведённое
сохраняется: файл дополняется, а не переписывается, иначе каждый запуск
стирал бы чужую работу.

Запуск::

    python tools/extract_strings.py en          # обновить locale/en.json
    python tools/extract_strings.py en --report # показать, сколько осталось

Что **не** попадает в каталог: докстроки и комментарии (это документация для
разработчика), строки без кириллицы (имена полей, ключи настроек, форматы) и
многострочные литералы длиннее абзаца — такие тексты переводят целиком в
файле, а не по строчке.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "sfstudio"
LOCALE = SOURCE / "locale"

CYRILLIC = re.compile(r"[А-Яа-яЁё]")

#: Длиннее этого строку в каталог не берём: обычно это абзац документации,
#: случайно оказавшийся не докстрокой.
MAX_LENGTH = 400


def docstrings(tree: ast.AST) -> set[int]:
    """Позиции докстрок — их переводить не нужно."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def collect(path: Path) -> list[str]:
    """Строки одного файла, которые увидит человек."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    skip = docstrings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        text = node.value
        if not CYRILLIC.search(text) or len(text) > MAX_LENGTH:
            continue
        found.append(text)
    return found


def scan() -> list[str]:
    """Все строки проекта, без повторов, в устойчивом порядке."""
    seen: dict[str, None] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        if "locale" in path.parts:
            continue
        for text in collect(path):
            seen.setdefault(text, None)
    return list(seen)


def update(language: str, *, report_only: bool = False) -> int:
    """Дополняет каталог. Возвращает число непереведённых строк."""
    LOCALE.mkdir(parents=True, exist_ok=True)
    target = LOCALE / f"{language}.json"

    existing: dict[str, str] = {}
    if target.is_file():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = {str(k): str(v) for k, v in loaded.items()}
        except (OSError, ValueError):
            print(f"каталог {target.name} повреждён — начинаем заново")

    strings = scan()
    catalog = {text: existing.get(text, "") for text in strings}
    # Переводы строк, которых больше нет в коде, сохраняются отдельно: текст
    # мог просто переехать, и терять чужую работу из-за перестановки нельзя.
    orphans = {k: v for k, v in existing.items() if k not in catalog and v}

    missing = sum(1 for value in catalog.values() if not value)
    print(f"строк в коде: {len(strings)}")
    print(f"переведено:   {len(strings) - missing}")
    print(f"осталось:     {missing}")
    if orphans:
        print(f"переводы без строки в коде: {len(orphans)} (сохранены в разделе устаревших)")

    if report_only:
        return missing

    if orphans:
        catalog["__устаревшие__"] = json.dumps(orphans, ensure_ascii=False)
    target.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"записано: {target}")
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сбор строк интерфейса")
    parser.add_argument("language", help="код языка, например en")
    parser.add_argument("--report", action="store_true",
                        help="только посчитать, ничего не записывать")
    args = parser.parse_args(argv)
    update(args.language, report_only=args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
