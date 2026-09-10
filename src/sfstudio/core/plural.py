"""Русское склонение числительных.

Нужно и в интерфейсе, и в сообщениях проверок, поэтому живёт в ядре: «1
знаков» в отчёте, который отдают заказчику, выглядит как небрежность, а
формула у всех одна.
"""

from __future__ import annotations

__all__ = ["plural"]


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 строка», «2 строки», «5 строк».

    Наивная формула («1 — one, 2–4 — few, иначе many») врёт на 11–14 и на
    всех числах, кончающихся на единицу: «21 строк» и «11 строка» одинаково
    выдают самоделку.
    """
    tail, hundreds = count % 10, count % 100
    if tail == 1 and hundreds != 11:
        return f"{count} {one}"
    if tail in (2, 3, 4) and hundreds not in (12, 13, 14):
        return f"{count} {few}"
    return f"{count} {many}"
