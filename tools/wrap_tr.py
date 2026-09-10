"""Оборачивает строки интерфейса в ``tr()``.

Перевод устроен так, что каждая видимая человеку строка должна пройти через
:func:`sfstudio.app.i18n.tr`. Строк таких две тысячи с лишним, и расставить
вызовы руками — значит гарантированно где-то ошибиться и потратить неделю.

Что делает. Разбирает файл в дерево, находит строковые литералы с кириллицей
и вставляет вокруг них ``tr(...)``. Правка **текстовая, по позициям узлов**, а
не через ``ast.unparse``: тот вернул бы файл без комментариев, с
переформатированными строками и потерянными пустыми строками — то есть
переписал бы весь исходник ради одной вставки.

f-строки превращаются в ``tr("шаблон {0}").format(выражение)``. Иначе их
нельзя перевести вовсе: к моменту вызова ``tr`` от шаблона уже ничего не
осталось, есть только готовый текст с подставленными значениями.

Что **не** трогает:

* докстроки и комментарии — это документация для разработчика;
* строки без кириллицы — ключи настроек, имена полей, форматы;
* то, что уже внутри ``tr(...)``;
* файлы, перечисленные в :data:`SKIP` — там строки уходят в файл, а не на
  экран, и переводить их значило бы менять формат данных.

Запуск::

    python tools/wrap_tr.py core io            # обернуть эти слои
    python tools/wrap_tr.py ui --dry-run       # только показать, сколько
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "sfstudio"

CYRILLIC = re.compile(r"[А-Яа-яЁё]")

IMPORT_LINE = "from sfstudio.app.i18n import tr"

#: Файлы, которые переводить нельзя.
#:
#: ``app/i18n.py`` — сам механизм перевода, обернуть его значило бы получить
#: бесконечную рекурсию. Остальное — места, где кириллица уходит в файл или в
#: настройки: перевод там сменил бы формат данных, а не подпись на экране.
SKIP: frozenset[str] = frozenset({
    "app/i18n.py",
    "core/tags.py",          # разметка ASS
    "io/charset.py",         # названия кодировок
})

#: Длиннее этого не трогаем: обычно это абзац, случайно не ставший докстрокой.
MAX_LENGTH = 400

#: Вызовы, чьи строковые аргументы трогать нельзя ни при каких условиях.
#: Регулярное выражение с русскими комментариями внутри — не подпись, а код,
#: и ``repr`` от него ломает и смысл, и разбор файла.
OPAQUE_CALLS: frozenset[str] = frozenset({
    # Регулярные выражения: русские комментарии внутри — часть кода.
    "compile", "match", "search", "sub", "fullmatch",
    # Операции над строками: аргумент там — данные, а не подпись. Перевести
    # «ё» в вызове replace значит сломать нормализацию, а перевести образец
    # в startswith — сравнение, которое перестанет совпадать.
    "replace", "split", "rsplit", "strip", "lstrip", "rstrip",
    "startswith", "endswith", "count", "find", "rfind", "index", "partition",
})


@dataclass(frozen=True, slots=True)
class Edit:
    """Одна вставка: что и на какие позиции в тексте файла."""

    start: tuple[int, int]
    end: tuple[int, int]
    replacement: str


def translatable(value: str) -> bool:
    """Годится ли строка в подпись на экране.

    Отсекаются два вида «строк», которые подписями не являются, но кириллицу
    содержат: многострочные куски (обычно текст письма или regex с
    комментариями) и слишком длинные абзацы.

    Обратный слэш больше не признак разметки: подписи вроде «Убрать
    позицию» и «перевод строки» содержат его наравне с обычным текстом,
    и прежняя проверка их отбрасывала. Настоящую разметку отсекает список
    непереводимых вызовов: там строка идёт данными, а не текстом на экран.
    """
    if len(value) > MAX_LENGTH:
        return False
    return not (chr(10) in value or chr(13) in value)


def opaque_nodes(tree: ast.AST) -> set[int]:
    """Аргументы вызовов, которые переводить нельзя. См. OPAQUE_CALLS."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func
        called = (
            name.attr if isinstance(name, ast.Attribute)
            else name.id if isinstance(name, ast.Name)
            else ""
        )
        if called not in OPAQUE_CALLS:
            continue
        for argument in node.args:
            for inner in ast.walk(argument):
                found.add(id(inner))
    return found


def inside_fstrings(tree: ast.AST) -> set[int]:
    """Всё, что лежит внутри f-строк.

    В Python 3.12 и новее куски f-строки — самостоятельные узлы дерева со
    своими позициями. Обход находит их наравне с обычными литералами, и
    попытка обернуть такой кусок вставляет ``tr(`` внутрь самой f-строки,
    ломая и её, и файл. Целиком f-строка обрабатывается отдельно.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for inner in ast.walk(node):
            if inner is not node:
                found.add(id(inner))
    return found


def docstring_nodes(tree: ast.AST) -> set[int]:
    """Узлы докстрок — их не переводим."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
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


def already_wrapped(tree: ast.AST) -> set[int]:
    """Узлы, уже стоящие внутри ``tr(...)`` — второй раз не оборачиваем."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func
        if isinstance(name, ast.Name) and name.id == "tr" and node.args:
            found.add(id(node.args[0]))
    return found


def template_of(node: ast.JoinedStr, source: list[str]) -> tuple[str, list[str]] | None:
    """Разбирает f-строку на шаблон с номерами и список выражений.

    ``f"осталось {n} из {total}"`` даёт ``("осталось {0} из {1}", ["n", "total"])``.
    Спецификатор формата сохраняется: ``f"{v:.1f} с"`` — это ``"{0:.1f} с"``.
    """
    parts: list[str] = []
    args: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            # Фигурные скобки в тексте надо удвоить: дальше идёт .format().
            parts.append(value.value.replace("{", "{{").replace("}", "}}"))
            continue
        if not isinstance(value, ast.FormattedValue):
            return None

        index = len(args)
        args.append(text_at(source, value.value))
        spec = ""
        if value.format_spec is not None:
            if not isinstance(value.format_spec, ast.JoinedStr):
                return None
            for piece in value.format_spec.values:
                if not (isinstance(piece, ast.Constant) and isinstance(piece.value, str)):
                    # Вложенная подстановка в спецификаторе: {v:.{digits}f}.
                    # Такое встречается редко и разбирается руками.
                    return None
                spec += piece.value
        conversion = ""
        if value.conversion is not None and value.conversion != -1:
            conversion = "!" + chr(value.conversion)
        parts.append("{" + str(index) + conversion + (":" + spec if spec else "") + "}")
    return "".join(parts), args


def text_at(source: list[bytes], node: ast.AST) -> str:
    """Исходный текст узла — чтобы перенести выражение в ``.format()``.

    Строки хранятся байтами, потому что ``col_offset`` в дереве разбора —
    смещение **в байтах UTF-8**, а не в символах. На русских подписях это не
    мелочь: каждая буква занимает два байта, и резать по символам значит
    промахиваться мимо конца литерала на его длину.
    """
    start_line, start_col = node.lineno - 1, node.col_offset
    end_line, end_col = node.end_lineno - 1, node.end_col_offset
    if start_line == end_line:
        chunk = source[start_line][start_col:end_col]
    else:
        chunks = [source[start_line][start_col:]]
        chunks.extend(source[start_line + 1:end_line])
        chunks.append(source[end_line][:end_col])
        chunk = b"".join(chunks)
    return chunk.decode("utf-8").strip()


def literal_source(value: str) -> str:
    """Литерал для каталога: обычные кавычки, без сюрпризов."""
    return repr(value)


def edits_for(path: Path) -> list[Edit]:
    """Что надо вставить в этот файл."""
    text = path.read_text(encoding="utf-8")
    lines = text.encode("utf-8").splitlines(keepends=True)
    tree = ast.parse(text)
    skip = (
        docstring_nodes(tree)
        | already_wrapped(tree)
        | opaque_nodes(tree)
        | inside_fstrings(tree)
    )

    edits: list[Edit] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip or not CYRILLIC.search(node.value):
                continue
            if not translatable(node.value):
                continue
            edits.append(
                Edit(
                    (node.lineno - 1, node.col_offset),
                    (node.end_lineno - 1, node.end_col_offset),
                    f"tr({literal_source(node.value)})",
                )
            )
        elif isinstance(node, ast.JoinedStr):
            joined = "".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if not CYRILLIC.search(joined) or not translatable(joined):
                continue
            parsed = template_of(node, lines)
            if parsed is None:
                continue
            template, args = parsed
            call = f"tr({literal_source(template)})"
            if args:
                call += ".format(" + ", ".join(args) + ")"
            edits.append(
                Edit(
                    (node.lineno - 1, node.col_offset),
                    (node.end_lineno - 1, node.end_col_offset),
                    call,
                )
            )
    return edits


def apply_edits(text: str, edits: list[Edit]) -> str:
    """Применяет вставки с конца файла — иначе позиции разъезжаются.

    Всё режется по байтам: смещения в дереве разбора байтовые (см.
    :func:`text_at`).
    """
    lines = text.encode("utf-8").splitlines(keepends=True)
    for edit in sorted(edits, key=lambda e: (e.start[0], e.start[1]), reverse=True):
        start_line, start_col = edit.start
        end_line, end_col = edit.end
        head = lines[start_line][:start_col]
        tail = lines[end_line][end_col:]
        lines[start_line:end_line + 1] = [head + edit.replacement.encode("utf-8") + tail]
    return b"".join(lines).decode("utf-8")


def ensure_import(text: str) -> str:
    """Дописывает импорт ``tr``, если его ещё нет.

    Место ищется по дереву разбора, а не поиском слова «import» в тексте: в
    интерфейсе полно импортов **внутри методов** — их делают лениво, чтобы не
    тянуть тяжёлые модули при старте. Вставка после такого импорта попадает
    в тело функции и ломает файл отступом.
    """
    if IMPORT_LINE in text:
        return text

    tree = ast.parse(text)
    last = 0
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            module = getattr(node, "module", "")
            if module == "__future__":
                last = max(last, node.end_lineno)
                continue
            last = max(last, node.end_lineno)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and last == 0:
            # Докстрока модуля: если импортов нет вовсе, встанем после неё.
            last = node.end_lineno

    lines = text.splitlines(keepends=True)
    prefix = "" if last else chr(10)
    lines.insert(last, prefix + IMPORT_LINE + chr(10))
    return "".join(lines)


def targets(areas: list[str]) -> list[Path]:
    found: list[Path] = []
    for area in areas:
        base = SOURCE / area if area != "." else SOURCE
        if base.is_file():
            found.append(base)
            continue
        pattern = "*.py" if area == "." else "**/*.py"
        found.extend(sorted(base.glob(pattern)))
    return [p for p in found if str(p.relative_to(SOURCE)).replace("\\", "/") not in SKIP]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("areas", nargs="+", help="папки внутри src/sfstudio")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать")
    args = parser.parse_args(argv)

    total = 0
    touched = 0
    for path in targets(args.areas):
        edits = edits_for(path)
        if not edits:
            continue
        total += len(edits)
        touched += 1
        rel = path.relative_to(ROOT)
        if args.dry_run:
            print(f"{rel}: {len(edits)}")
            continue

        text = path.read_text(encoding="utf-8")
        updated = ensure_import(apply_edits(text, edits))
        try:
            ast.parse(updated)
        except SyntaxError as exc:
            print(f"{rel}: правка сломала бы разбор ({exc}); файл не тронут")
            total -= len(edits)
            touched -= 1
            continue
        path.write_text(updated, encoding="utf-8")
        print(f"{rel}: {len(edits)}")

    print(f"\nстрок: {total}, файлов: {touched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
