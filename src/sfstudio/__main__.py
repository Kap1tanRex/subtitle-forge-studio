"""Точка входа.

``--version``, ``--selftest``, ``--plugins`` и ``--asr-test`` работают без GUI —
это нужно, чтобы CI мог проверять сборку smoke-тестом на машине без дисплея.
Импорт Qt происходит только при реальном запуске окна.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sfstudio import __version__


def _versions() -> list[tuple[str, str]]:
    """Версии компонентов. Отсутствующие показываются как «не установлено»."""
    rows: list[tuple[str, str]] = [
        ("SubtitleForge Studio", __version__),
        ("Python", sys.version.split()[0]),
    ]
    for label, module, attr in (
        ("PySide6", "PySide6", "__version__"),
        ("PyAV", "av", "__version__"),
        ("NumPy", "numpy", "__version__"),
        ("python-mpv", "mpv", "MPV_VERSION"),
    ):
        try:
            mod = __import__(module)
            rows.append((label, str(getattr(mod, attr, "?"))))
        except ImportError:
            rows.append((label, "не установлено"))
        except OSError as exc:
            # python-mpv бросает OSError (не ImportError), если не нашёл
            # libmpv-2.dll. Ловить только ImportError здесь недостаточно:
            # на машине без нативных библиотек падал бы даже --version.
            rows.append((label, f"нет нативной библиотеки: {_short(exc)}"))
    rows.append(("проверка орфографии", _spelling_state()))
    return rows


def _spelling_state() -> str:
    """Есть ли словари. В собранной программе они лежат внутри exe, и без
    этой строки узнать об их пропаже можно было бы только на глаз."""
    try:
        from sfstudio.services.spelling import SpellChecker, available
    except ImportError:
        return "не установлена"
    if not available():
        return "не установлена"
    checker = SpellChecker("ru")
    # Пробуем настоящее слово: библиотека может стоять, а словари — нет.
    checker.known("проверка")
    return "недоступна" if checker._failed else "русский, английский"


def _short(exc: BaseException, limit: int = 60) -> str:
    text = str(exc).splitlines()[0]
    return text if len(text) <= limit else text[: limit - 1] + "…"


def cmd_version() -> int:
    rows = _versions()
    from sfstudio.platform.native import diagnose

    rows += [("", ""), ("--- нативные ---", "")]
    rows += list(diagnose().items())

    # Движки распознавания: в собранном виде доставить их через pip нельзя,
    # поэтому важно видеть, что попало внутрь.
    rows += [("", ""), ("--- распознавание ---", "")]
    try:
        from sfstudio.services.asr import engine_infos

        for info in engine_infos():
            rows.append((info.key, "доступен" if info.available else "нет"))
    except Exception as exc:  # слой необязательный, его отсутствие не ошибка
        rows.append(("движки", f"недоступны: {_short(exc)}"))

    # Путь к журналу: в оконной сборке консоли нет, и найти, куда смотреть
    # при неполадке, иначе неоткуда.
    from sfstudio.platform.paths import logs_dir

    rows += [("", ""), ("журнал", str(logs_dir() / "sfstudio.log"))]

    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name:<{width}}  {value}" if name else "")
    return 0


def cmd_selftest() -> int:
    """Прогон ядра на синтетических данных. Возвращает 0 при успехе."""
    from sfstudio.core.color import RGBA
    from sfstudio.core.commands import SetPosition, SetText
    from sfstudio.core.document import SubtitleDocument
    from sfstudio.core.index import TimeIndex
    from sfstudio.core.time import FpsModel, format_ass, parse_timecode
    from sfstudio.core.undo import UndoStack
    from sfstudio.io.formats.ass import read_ass, write_ass

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, condition, detail))

    # Время
    check("таймкод round-trip", parse_timecode(format_ass(1230)) == 1230)
    fps = FpsModel.from_float(23.976023976)
    check("точная дробь FPS", fps.rate.denominator == 1001, str(fps.rate))

    # Цвет
    check("инверсия альфы ASS", RGBA(0, 0, 0, 255).to_ass() == "&H00000000")

    # Документ и индекс
    doc = SubtitleDocument.blank()
    for i in range(1000):
        doc.create_event(i * 1000, i * 1000 + 800, f"Реплика {i}")
    check("документ построен", len(doc) == 1000)
    check("активные в точке", [e.text for e in doc.active_at(5100)] == ["Реплика 5"])
    check("пусто в зазоре", doc.active_at(5900) == [])

    idx = TimeIndex(doc.events)
    check("диапазон индекса", len(idx.range(0, 10_000)) == 10)

    # Команды и отмена
    stack = UndoStack(doc)
    eid = doc.events[0].eid
    before = doc.by_eid(eid).text
    stack.run(SetText(eid, "Изменено"))
    stack.run(SetPosition(eid, 960, 1010))
    check("позиция записана", doc.by_eid(eid).position() == (960.0, 1010.0))
    while stack.can_undo:
        stack.undo()
    check("отмена восстановила", doc.by_eid(eid).text == before, doc.by_eid(eid).text)

    # Форматы
    text = write_ass(doc)
    reread = read_ass(text)
    check("ASS round-trip", write_ass(reread) == text)
    check("события уцелели", len(reread) == len(doc))

    width = max(len(name) for name, _, _ in checks)
    failed = 0
    for name, ok, detail in checks:
        mark = "ok  " if ok else "СБОЙ"
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{mark}] {name:<{width}}{suffix}")
        failed += not ok

    print()
    print(f"Проверок: {len(checks)}, сбоев: {failed}")
    return 1 if failed else 0


def cmd_gui(path: Path | None) -> int:
    try:
        from sfstudio.ui.app import run
    except ImportError as exc:
        print(f"GUI недоступен: {exc}", file=sys.stderr)
        print('Установите зависимости: pip install -e ".[gui]"', file=sys.stderr)
        return 2
    return run(path)


def cmd_asr_test(
    media: Path, model: str, models_dir: Path | None, seconds: int
) -> int:
    """Прогон распознавания без графики.

    Нужен, чтобы проверить самое хрупкое место — стык с чужой библиотекой и её
    файлами — прямо в том окружении, где программа работает у пользователя.
    В собранном виде запустить свой скрипт нельзя, а GUI для такой проверки
    слишком много: тут важен ответ «работает или нет», а не окно.
    """
    from sfstudio.services.asr import RecognitionError, RecognitionRequest, get_engine, to_events

    engine = get_engine("faster-whisper")
    if engine is None:
        print("движок faster-whisper не зарегистрирован", file=sys.stderr)
        return 2

    info = engine.info()
    print(f"движок: {info.title}, доступен: {info.available}")
    if not info.available:
        print(info.hint, file=sys.stderr)
        return 2

    request = RecognitionRequest(
        media=media,
        end_ms=seconds * 1000 if seconds > 0 else None,
        language="ru",
        model=model,
        models_dir=models_dir,
    )

    last = [-1.0]

    def report(fraction: float, note: str = "") -> None:
        if fraction - last[0] >= 0.2 or fraction >= 1.0:
            last[0] = fraction
            print(f"  {fraction * 100:5.1f}%  {note}")

    try:
        result = engine.transcribe(request, progress=report)
    except RecognitionError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1

    events = to_events(result.segments)
    print(f"язык: {result.language}  сегментов: {len(result.segments)}  "
          f"реплик: {len(events)}  время: {result.elapsed_s:.1f} с")
    for event in events[:5]:
        print(f"  [{event.start:6d} → {event.end:6d}] "
              f"{event.text.replace(chr(92) + 'N', ' | ')[:70]}")
    return 0 if events else 1


def cmd_plugins() -> int:
    """Перечисляет плагины и то, что каждый добавил.

    Нужна для разбора «мой плагин не работает»: без графики видно, нашёлся ли
    он вообще, загрузился ли, что помешало и что он в итоге зарегистрировал.
    В окне настроек это тоже есть, но в окно ошибку загрузки ещё надо суметь
    прочитать — а сюда её можно скопировать одной строкой.
    """
    from sfstudio.app.settings import Settings
    from sfstudio.app.storage import plugins_dir
    from sfstudio.plugins import PluginManager, PluginState

    settings = Settings()
    folder = plugins_dir(settings)
    enabled = bool(settings.get("plugins.enabled", False))

    print(f"каталог плагинов: {folder}")
    print(f"загрузка плагинов: {'включена' if enabled else 'выключена'}")
    if not enabled:
        print("  (в этом режиме плагины перечисляются, но не выполняются)")
    print()

    manager = PluginManager(folder, settings)
    found = manager.load_all(enabled=enabled)
    if not found:
        print("плагинов не найдено")
        return 0

    for plugin in found:
        mark = "ok  " if plugin.state is PluginState.LOADED else "!   "
        print(f"[{mark}] {plugin.info.title} {plugin.info.version} "
              f"({plugin.info.name}) — {plugin.state.title}")
        if plugin.reason:
            print(f"        {plugin.reason}")

    added = manager.install()
    if added:
        print()
        print("зарегистрировано:")
        for line in added:
            print(f"  {line}")

    actions = manager.actions()
    if actions:
        print()
        print("пункты меню:")
        for action in actions:
            print(f"  {action.title}")

    broken = [p for p in found if p.state is PluginState.FAILED]
    return 1 if broken else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sfstudio", description="Редактор субтитров")
    parser.add_argument("file", nargs="?", type=Path, help="файл субтитров или видео")
    parser.add_argument("--version", action="store_true", help="версии компонентов")
    parser.add_argument("--selftest", action="store_true", help="самопроверка ядра")
    parser.add_argument("--plugins", action="store_true",
                        help="список плагинов и что каждый добавил")
    parser.add_argument("--asr-test", metavar="ФАЙЛ", type=Path,
                        help="прогнать распознавание речи на файле, без графики")
    parser.add_argument("--asr-model", default="small", help="размер модели")
    parser.add_argument("--asr-models-dir", type=Path, help="каталог моделей")
    parser.add_argument("--asr-seconds", type=int, default=30,
                        help="сколько секунд распознать (0 — весь файл)")
    args = parser.parse_args(argv)

    if args.version:
        return cmd_version()
    if args.selftest:
        return cmd_selftest()
    if args.plugins:
        return cmd_plugins()
    if args.asr_test is not None:
        return cmd_asr_test(
            args.asr_test, args.asr_model, args.asr_models_dir, args.asr_seconds
        )
    return cmd_gui(args.file)


if __name__ == "__main__":
    sys.exit(main())
