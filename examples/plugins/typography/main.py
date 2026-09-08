"""Типографика русских субтитров.

Задача настоящая: расшифровка распознавания приходит с прямыми кавычками,
тремя точками вместо многоточия и дефисом вместо тире. Править это руками по
тысяче реплик — работа на вечер, а правила простые и механические.

Плагин делает две вещи, и обе полезны по отдельности:

* **проверка** отмечает реплики, где типографика нарушена, — её видно в
  панели контроля качества, и можно решать по одной;
* **действие** исправляет всё разом, одной командой в истории отмены.

Почему не просто «заменить всё» поиском: правила зависят от места в строке.
Открывающая кавычка отличается от закрывающей, тире ставится только в начале
реплики диалога, а дефис внутри слова («кто-то») трогать нельзя.
"""

import re

# Прямые кавычки. Открывающая — та, перед которой начало строки или пробел.
_OPEN_QUOTE = re.compile(r'(^|[\s(\[{—-])"')
_CLOSE_QUOTE = re.compile(r'"')

# Три и больше точек — многоточие. Именно одним знаком: так его правильно
# переносит libass и правильно считает проверка длины строки.
_ELLIPSIS = re.compile(r"\.{3,}")

# Дефис в начале строки — это реплика диалога, там нужно тире. Внутри слова
# дефис остаётся дефисом: «из-за», «кто-то», «по-русски».
#
# Обратный слэш здесь удвоен намеренно: перенос строки в ASS записывается как
# \N, а в регулярном выражении Python одиночный \N — это начало имени
# юникод-символа, и выражение просто не собирается.
_DIALOGUE_DASH = re.compile(r"(^|\\N)\s*[-–]\s+", re.MULTILINE)

# Дефис, окружённый пробелами, — это тире: «Кто там? - спросил он». Внутри
# слова дефис пробелами не окружён, поэтому «из-за» под правило не подпадает.
_SPACED_DASH = re.compile(r"(?<=\s)[-–](?=\s)")

# Пробел перед знаком препинания — почти всегда опечатка.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:…])")

# Два и больше пробелов подряд.
_DOUBLE_SPACE = re.compile(r"  +")

#: Разметка ASS: внутрь фигурных скобок лезть нельзя, там теги оформления.
_TAGS = re.compile(r"\{[^}]*\}")


def improve(text: str) -> str:
    """Приводит текст реплики к типографским нормам.

    Разметка вырезается на время правки и возвращается на место: замена
    внутри ``{\fs40}`` испортила бы оформление, а «...» в теге эффекта —
    вполне законная запись.
    """
    parts = []
    last = 0
    for match in _TAGS.finditer(text):
        parts.append(("текст", text[last:match.start()]))
        parts.append(("тег", match.group(0)))
        last = match.end()
    parts.append(("текст", text[last:]))

    result = []
    for kind, piece in parts:
        result.append(piece if kind == "тег" else _improve_piece(piece))
    return "".join(result)


def _improve_piece(piece: str) -> str:
    piece = _ELLIPSIS.sub("…", piece)
    piece = _DOUBLE_SPACE.sub(" ", piece)
    piece = _SPACE_BEFORE_PUNCT.sub(r"\1", piece)
    piece = _DIALOGUE_DASH.sub(r"\1— ", piece)
    # После начала строки — дефисы в середине: «Кто там? - спросил он».
    piece = _SPACED_DASH.sub("—", piece)

    # Кавычки: сначала открывающие по контексту, остальные — закрывающие.
    piece = _OPEN_QUOTE.sub(r"\1«", piece)
    return _CLOSE_QUOTE.sub("»", piece)


def find_problems(text: str) -> list[str]:
    """Чем именно плоха типографика этой реплики. Пустой список — всё хорошо."""
    stripped = _TAGS.sub("", text)
    problems = []
    if '"' in stripped:
        problems.append("прямые кавычки")
    if _ELLIPSIS.search(stripped):
        problems.append("три точки вместо многоточия")
    if _DIALOGUE_DASH.search(stripped) or _SPACED_DASH.search(stripped):
        problems.append("дефис вместо тире")
    if _DOUBLE_SPACE.search(stripped):
        problems.append("двойной пробел")
    if _SPACE_BEFORE_PUNCT.search(stripped):
        problems.append("пробел перед знаком препинания")
    return problems


def setup(context):
    """Точка входа: программа зовёт её один раз при загрузке."""

    def check(event, ctx):
        """Проверка качества. Её вердикты видны в панели наравне со штатными."""
        problems = find_problems(event.text)
        if problems:
            yield ctx.issue(
                event,
                rule="typography",
                message="Типографика: " + ", ".join(problems),
            )

    def fix_all():
        """Исправляет типографику во всём документе одной командой."""
        doc = context.document()
        changes = {}
        for event in doc.events:
            improved = improve(event.text)
            if improved != event.text:
                changes[event.eid] = improved

        if not changes:
            context.report("Типографика: исправлять нечего")
            return

        context.set_texts(changes, label=f"Типографика в {len(changes)} репликах")
        context.report(f"Типографика исправлена в {len(changes)} репликах")

    def fix_selected():
        """То же, но только для выделенных реплик."""
        doc = context.document()
        chosen = set(context.selection())
        if not chosen:
            context.report("Сначала выберите реплики")
            return

        changes = {}
        for event in doc.events:
            if event.eid not in chosen:
                continue
            improved = improve(event.text)
            if improved != event.text:
                changes[event.eid] = improved

        if not changes:
            context.report("В выделенных репликах исправлять нечего")
            return
        context.set_texts(changes, label=f"Типографика в {len(changes)} репликах")
        context.report(f"Исправлено реплик: {len(changes)}")

    context.register_qc_rule(check)
    context.register_action("fix_all", "Типографика: исправить всё", fix_all)
    context.register_action(
        "fix_selected", "Типографика: исправить выделенное", fix_selected
    )
