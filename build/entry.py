"""Точка входа для собранного бинарника.

Отдельным файлом, а не ``-m sfstudio``: PyInstaller запускает скрипт, а не
модуль, и при запуске пакета как скрипта ломается относительный импорт.

Здесь же — единственное, что нужно менять в замороженном режиме: каталог
с нативными библиотеками. В обычной установке он лежит внутри пакета и
находится по ``__file__``; после распаковки onefile тот же путь ведёт внутрь
временного каталога, поэтому специальной обработки не требуется — но PATH
всё равно стоит подготовить до первого импорта, чтобы зависимости libmpv
(их около сорока) искались рядом с ней, а не по всему PATH машины.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_console() -> None:
    """Приводит консоль и потоки вывода к UTF-8.

    Из исходников кириллица печатается верно, из собранного бинарника — нет:
    замороженный интерпретатор не подхватывает UTF-8 для ``stdout``, а консоль
    Windows остаётся в кодовой странице 866. В результате ``--version`` и
    ``--selftest`` выдают мохибейк — то есть диагностика становится
    нечитаемой. Чинится в две строки, поэтому чинится здесь, а не объясняется
    в README.
    """
    if os.name == "nt":
        import contextlib
        import ctypes

        # Вывод может быть перенаправлен в файл — тогда кодовая страница
        # консоли ни при чём, и попытка её сменить законно не удаётся.
        with contextlib.suppress(OSError, AttributeError):
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _redirect_output_to_log() -> Path | None:
    """Уводит вывод в файл журнала — для сборки без консоли.

    Окно консоли рядом с приложением пользователю не нужно, но и молча терять
    диагностику нельзя: без ``stdout`` падение выглядит как «программа не
    запустилась», и разбираться не с чем. Поэтому вместо консоли — файл.

    ``sys.stdout`` в оконной сборке PyInstaller равен ``None``; любой ``print``
    в таком процессе падает с ``AttributeError``, поэтому подменять потоки надо
    до того, как что-то попробует напечататься.
    """
    if not getattr(sys, "frozen", False):
        return None
    # Консольная сборка: потоки на месте, файл не нужен.
    if sys.stdout is not None and sys.stderr is not None:
        return None

    from sfstudio.platform.paths import logs_dir

    path = logs_dir() / "sfstudio.log"
    try:
        stream = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return None

    sys.stdout = stream
    sys.stderr = stream
    import datetime

    stream.write(f"\n=== запуск {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
    return path


def _install_qt_logging() -> None:
    """Направляет сообщения Qt в наш вывод вместе со стеком Python.

    Qt печатает предупреждения мимо ``sys.stderr``, поэтому в сборке без
    консоли они пропадали бы вовсе. А голое «QFont::setPointSize: Point size
    <= 0 (-1)» без места вызова бесполезно: непонятно, какой из десятка
    виджетов его вызвал. Поэтому к сообщению добавляется стек Python.
    """
    try:
        from PySide6.QtCore import qInstallMessageHandler
    except ImportError:
        return

    # Отметка в журнале: без неё пустой журнал нельзя прочитать однозначно —
    # то ли сообщений не было, то ли перехватчик не встал. В консольном
    # режиме она не нужна и только засоряет вывод --version и --selftest.
    if _LOG_PATH is not None:
        print("[qt] перехват сообщений Qt установлен", file=sys.stderr)

    seen: set[str] = set()

    def handler(_mode, _context, message: str) -> None:
        text = str(message)
        print(f"[qt] {text}", file=sys.stderr)
        # Стек печатаем один раз на каждое уникальное сообщение: повторы
        # ничего не добавляют, а журнал забивают.
        if text not in seen:
            seen.add(text)
            import traceback

            traceback.print_stack(file=sys.stderr)

    qInstallMessageHandler(handler)


def _bootstrap_native() -> None:
    """Кладёт каталог вендоринга в пути поиска DLL до импорта пакета."""
    if not getattr(sys, "frozen", False):
        return
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    native = base / "sfstudio" / "_native" / ("win64" if os.name == "nt" else "linux64")
    if not native.is_dir():
        return
    if os.name == "nt":
        os.add_dll_directory(str(native))
    os.environ["PATH"] = str(native) + os.pathsep + os.environ.get("PATH", "")


def _arm_hang_dump() -> None:
    """``SFSTUDIO_HANG_DUMP=<секунды>`` — печатает стек, если приложение зависло.

    В собранном виде зависание выглядит как «ничего не происходит»: окна нет,
    вывода нет, процесс жив. Отладчик к onefile-сборке подцепить непросто,
    а стек всех потоков отвечает на вопрос сразу.
    """
    raw = os.environ.get("SFSTUDIO_HANG_DUMP")
    if not raw:
        return
    try:
        seconds = float(raw)
    except ValueError:
        return
    import faulthandler

    faulthandler.dump_traceback_later(seconds, exit=True)


_bootstrap_console()
_arm_hang_dump()
_bootstrap_native()
_LOG_PATH = _redirect_output_to_log()

from sfstudio.__main__ import main  # noqa: E402

if __name__ == "__main__":
    _install_qt_logging()
    sys.exit(main())
