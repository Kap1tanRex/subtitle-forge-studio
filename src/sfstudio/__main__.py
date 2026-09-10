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
from sfstudio.app.i18n import set_language, tr


def _use_saved_language() -> None:
    """Ставит язык **до** импорта всего остального.

    Часть подписей живёт в константах уровня модуля — списки цветов, названия
    выравниваний, заголовки таблиц. Они вычисляются один раз, при импорте, и
    язык, выбранный после этого, до них уже не доберётся: половина окна
    осталась бы русской.

    Поэтому вызов стоит здесь, в единственной точке входа программы, и до
    любого ``from sfstudio.core…``. Читаем настройки молча: испорченный файл
    не должен мешать запуску с ключом ``--version``, которым как раз и
    выясняют, что сломалось.
    """
    try:
        from sfstudio.app.settings import Settings

        set_language(Settings().get("ui.language", "ru"))
    except Exception:
        pass


_use_saved_language()


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
            rows.append((label, tr('не установлено')))
        except OSError as exc:
            # python-mpv бросает OSError (не ImportError), если не нашёл
            # libmpv-2.dll. Ловить только ImportError здесь недостаточно:
            # на машине без нативных библиотек падал бы даже --version.
            rows.append((label, tr('нет нативной библиотеки: {0}').format(_short(exc))))
    rows.append((tr('проверка орфографии'), _spelling_state()))
    return rows


def _spelling_state() -> str:
    """Есть ли словари. В собранной программе они лежат внутри exe, и без
    этой строки узнать об их пропаже можно было бы только на глаз."""
    try:
        from sfstudio.services.spelling import SpellChecker, available
    except ImportError:
        return tr('не установлена')
    if not available():
        return tr('не установлена')
    checker = SpellChecker("ru")
    # Пробуем настоящее слово: библиотека может стоять, а словари — нет.
    checker.known(tr('проверка'))
    return tr('недоступна') if checker._failed else tr('русский, английский')


def _short(exc: BaseException, limit: int = 60) -> str:
    text = str(exc).splitlines()[0]
    return text if len(text) <= limit else text[: limit - 1] + "…"


def cmd_version() -> int:
    rows = _versions()
    from sfstudio.platform.native import diagnose

    rows += [("", ""), (tr('--- нативные ---'), "")]
    rows += list(diagnose().items())

    # Движки распознавания: в собранном виде доставить их через pip нельзя,
    # поэтому важно видеть, что попало внутрь.
    rows += [("", ""), (tr('--- распознавание ---'), "")]
    try:
        from sfstudio.services.asr import engine_infos

        for info in engine_infos():
            rows.append((info.key, tr('доступен') if info.available else tr('нет')))
    except Exception as exc:  # слой необязательный, его отсутствие не ошибка
        rows.append((tr('движки'), tr('недоступны: {0}').format(_short(exc))))

    # Путь к журналу: в оконной сборке консоли нет, и найти, куда смотреть
    # при неполадке, иначе неоткуда.
    from sfstudio.platform.paths import logs_dir

    rows += [("", ""), (tr('журнал'), str(logs_dir() / "sfstudio.log"))]

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
    check(tr('таймкод round-trip'), parse_timecode(format_ass(1230)) == 1230)
    fps = FpsModel.from_float(23.976023976)
    check(tr('точная дробь FPS'), fps.rate.denominator == 1001, str(fps.rate))

    # Цвет
    check(tr('инверсия альфы ASS'), RGBA(0, 0, 0, 255).to_ass() == "&H00000000")

    # Документ и индекс
    doc = SubtitleDocument.blank()
    for i in range(1000):
        doc.create_event(i * 1000, i * 1000 + 800, tr('Реплика {0}').format(i))
    check(tr('документ построен'), len(doc) == 1000)
    check(tr('активные в точке'), [e.text for e in doc.active_at(5100)] == [tr('Реплика 5')])
    check(tr('пусто в зазоре'), doc.active_at(5900) == [])

    idx = TimeIndex(doc.events)
    check(tr('диапазон индекса'), len(idx.range(0, 10_000)) == 10)

    # Команды и отмена
    stack = UndoStack(doc)
    eid = doc.events[0].eid
    before = doc.by_eid(eid).text
    stack.run(SetText(eid, tr('Изменено')))
    stack.run(SetPosition(eid, 960, 1010))
    check(tr('позиция записана'), doc.by_eid(eid).position() == (960.0, 1010.0))
    while stack.can_undo:
        stack.undo()
    check(tr('отмена восстановила'), doc.by_eid(eid).text == before, doc.by_eid(eid).text)

    # Форматы
    text = write_ass(doc)
    reread = read_ass(text)
    check("ASS round-trip", write_ass(reread) == text)
    check(tr('события уцелели'), len(reread) == len(doc))

    width = max(len(name) for name, _, _ in checks)
    failed = 0
    for name, ok, detail in checks:
        mark = "ok  " if ok else tr('СБОЙ')
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{mark}] {name:<{width}}{suffix}")
        failed += not ok

    print()
    print(tr('Проверок: {0}, сбоев: {1}').format(len(checks), failed))
    return 1 if failed else 0


def cmd_gui(path: Path | None) -> int:
    try:
        from sfstudio.ui.app import run
    except ImportError as exc:
        print(tr('GUI недоступен: {0}').format(exc), file=sys.stderr)
        print(tr('Установите зависимости: pip install -e ".[gui]"'), file=sys.stderr)
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
        print(tr('движок faster-whisper не зарегистрирован'), file=sys.stderr)
        return 2

    info = engine.info()
    print(tr('движок: {0}, доступен: {1}').format(info.title, info.available))
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
        print(tr('ошибка: {0}').format(exc), file=sys.stderr)
        return 1

    events = to_events(result.segments)
    print(tr('язык: {0}  сегментов: {1}  реплик: {2}  время: {3:.1f} '
           'с').format(result.language, len(result.segments), len(events), result.elapsed_s))
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

    print(tr('каталог плагинов: {0}').format(folder))
    print(tr('загрузка плагинов: {0}').format(tr('включена') if enabled else tr('выключена')))
    if not enabled:
        print(tr('  (в этом режиме плагины перечисляются, но не выполняются)'))
    print()

    manager = PluginManager(folder, settings)
    found = manager.load_all(enabled=enabled)
    if not found:
        print(tr('плагинов не найдено'))
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
        print(tr('зарегистрировано:'))
        for line in added:
            print(f"  {line}")

    actions = manager.actions()
    if actions:
        print()
        print(tr('пункты меню:'))
        for action in actions:
            print(f"  {action.title}")

    broken = [p for p in found if p.state is PluginState.FAILED]
    return 1 if broken else 0


def cmd_batch(args) -> int:
    """Пакетная обработка без графики.

    Возвращает единицу, если хоть один файл не вышел: команду ставят в
    цепочку, и молчаливый успех при половине упавших файлов хуже отказа.
    """
    from sfstudio.services.batch import convert, describe, run_batch, run_checks, shift

    tasks = []
    if args.shift:
        tasks.append(shift(args.shift))
    if args.to:
        from sfstudio.io.registry import FORMATS

        if args.to not in FORMATS:
            print(tr('неизвестный формат: {0}. Есть: {1}').format(args.to, ', '.join(FORMATS)))
            return 2
        tasks.append(convert(args.to))
    if args.check:
        from sfstudio.services.qc import PROFILES, default_profile

        profile = PROFILES.get(args.check, default_profile())
        tasks.append(run_checks(profile, args.out or Path.cwd()))

    if not tasks:
        print(tr('нечего делать: укажите --shift, --to или --check'))
        return 2

    def report(index: int, total: int, path: Path) -> None:
        print(f"[{index}/{total}] {path.name}", flush=True)

    results = run_batch(
        args.batch, tasks,
        output_dir=args.out,
        suffix=args.suffix,
        overwrite=args.overwrite,
        progress=report,
    )

    for result in results:
        if result.ok:
            notes = "; ".join(result.notes)
            print(tr('  готово: {0} ({1})').format(result.written, notes))
        else:
            print(tr('  не вышло: {0} — {1}').format(result.source.name, result.error))

    print(describe(results))
    return 0 if all(result.ok for result in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sfstudio", description=tr('Редактор субтитров'))
    parser.add_argument("file", nargs="?", type=Path, help=tr('файл субтитров или видео'))
    parser.add_argument("--version", action="store_true", help=tr('версии компонентов'))
    parser.add_argument("--selftest", action="store_true", help=tr('самопроверка ядра'))
    parser.add_argument("--plugins", action="store_true",
                        help=tr('список плагинов и что каждый добавил'))
    parser.add_argument("--asr-test", metavar=tr('ФАЙЛ'), type=Path,
                        help=tr('прогнать распознавание речи на файле, без графики'))
    parser.add_argument("--asr-model", default="small", help=tr('размер модели'))
    parser.add_argument("--asr-models-dir", type=Path, help=tr('каталог моделей'))
    parser.add_argument("--asr-seconds", type=int, default=30,
                        help=tr('сколько секунд распознать (0 — весь файл)'))

    batch = parser.add_argument_group(tr('пакетная обработка'))
    batch.add_argument("--batch", nargs="+", metavar=tr('ФАЙЛ'), type=Path,
                       help=tr('файлы субтитров для обработки без графики'))
    batch.add_argument("--shift", type=int, metavar=tr('МС'), default=0,
                       help=tr('сдвинуть тайминги на столько миллисекунд'))
    batch.add_argument("--to", metavar=tr('ФОРМАТ'),
                       help=tr('перевести в формат: ass, srt, vtt, ttml'))
    batch.add_argument("--check", metavar=tr('ПРОФИЛЬ'), nargs="?", const="general",
                       help=tr('прогнать проверки и положить отчёт рядом'))
    batch.add_argument("--out", type=Path, metavar=tr('ПАПКА'),
                       help=tr('куда класть результат'))
    batch.add_argument("--suffix", default="", metavar=tr('ТЕКСТ'),
                       help=tr('приписка к имени файла'))
    batch.add_argument("--overwrite", action="store_true",
                       help=tr('разрешить перезапись исходников'))
    args = parser.parse_args(argv)

    if args.version:
        return cmd_version()
    if args.selftest:
        return cmd_selftest()
    if args.plugins:
        return cmd_plugins()
    if args.batch:
        return cmd_batch(args)
    if args.asr_test is not None:
        return cmd_asr_test(
            args.asr_test, args.asr_model, args.asr_models_dir, args.asr_seconds
        )
    return cmd_gui(args.file)


if __name__ == "__main__":
    sys.exit(main())
