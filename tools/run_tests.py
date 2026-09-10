"""Прогон тестов: два захода вместо одного.

Почему не одной командой. Полторы тысячи тестов в одном процессе перестали
помещаться: сессия обрывалась падением интерпретатора в конце, при том что
все тесты проходили. Параллельный прогон это вылечил — но тесты, создающие
экземпляры libmpv и libass, при нескольких одновременных копиях дают то
падение рабочего процесса, то зависание всего прогона.

Отсюда разделение: основная часть идёт в четыре процесса, медийная —
последовательно, в одиночку. Обе части считаются вместе, и код возврата
общий: если упало хоть что-то, упало всё.

    python tools/run_tests.py            # обе части
    python tools/run_tests.py -k имя     # аргументы уходят в обе

Для отдельного прогона медийной части: ``pytest -m serial -n 0``.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(title: str, extra: list[str], args: list[str]) -> tuple[int, float]:
    print(f"\n=== {title} ===", flush=True)
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *extra, *args], cwd=ROOT, check=False
    )
    return result.returncode, time.perf_counter() - started


def main(argv: list[str]) -> int:
    quick, quick_time = run("основные тесты, четыре процесса", [], argv)
    # -n 0 отменяет параллельность из настроек проекта; -m serial берёт то,
    # что основной прогон намеренно пропустил.
    serial, serial_time = run(
        "медийные тесты, последовательно", ["-n", "0", "-m", "serial"], argv
    )

    print(
        f"\nитого: основные {quick_time:.0f} с (код {quick}), "
        f"медийные {serial_time:.0f} с (код {serial})"
    )
    # Код 5 — «не найдено ни одного теста»: законно, когда прогон сузили
    # фильтром, и не повод считать это провалом.
    return max(0 if code in (0, 5) else code for code in (quick, serial))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
