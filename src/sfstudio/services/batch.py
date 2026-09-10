"""Пакетная обработка: одно и то же действие над многими файлами.

Сериал — это двенадцать файлов. Сдвинуть тайминги на полсекунды, перегнать в
SRT, прогнать проверки — руками это делается двенадцать раз, и на восьмом
человек ошибается.

Устройство простое: список задач, каждая знает, что сделать с документом.
Результат по каждому файлу — отдельная запись, и неудача одного файла не
останавливает остальные: узнать «упало на седьмом из двенадцати» после
получаса ожидания хуже, чем получить десять готовых и список из двух
проблемных.

Ничего не перезаписывается молча. Итог кладётся рядом с исходником с новым
именем, а перезапись включается отдельно и осознанно.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.core.document import SubtitleDocument

__all__ = [
    "BatchResult",
    "BatchTask",
    "convert",
    "run_batch",
    "run_checks",
    "shift",
]


@dataclass(frozen=True, slots=True)
class BatchTask:
    """Одно действие над документом.

    ``apply`` меняет документ на месте и может вернуть строку с пояснением —
    её человек увидит в отчёте по файлу.
    """

    title: str
    apply: Callable[[SubtitleDocument], str | None]


@dataclass(slots=True)
class BatchResult:
    """Что вышло с одним файлом."""

    source: Path
    written: Path | None = None
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def shift(delta_ms: int) -> BatchTask:
    """Сдвигает все реплики на заданное число миллисекунд.

    Отрицательный сдвиг не уводит реплики в минус: время до нуля обрезается,
    а не становится отрицательным — такой файл не примет ни один плеер.
    """

    def apply(doc: SubtitleDocument) -> str:
        moved = 0
        for event in doc.events:
            start = max(0, event.start + delta_ms)
            end = max(start, event.end + delta_ms)
            if (start, end) != (event.start, event.end):
                event.start, event.end = start, end
                moved += 1
        doc.rebuild_lookup()
        return tr('сдвинуто реплик: {0}').format(moved)

    sign = "+" if delta_ms >= 0 else ""
    return BatchTask(tr('сдвиг {0}{1} мс').format(sign, delta_ms), apply)


def convert(fid: str) -> BatchTask:
    """Меняет формат записи. Сам документ при этом не трогается."""

    def apply(doc: SubtitleDocument) -> str:
        doc.source_format = fid
        return tr('формат: {0}').format(fid)

    return BatchTask(tr('перевод в {0}').format(fid), apply)


def run_checks(profile, report_dir: Path | None = None) -> BatchTask:
    """Прогоняет проверки и, если попросили, кладёт отчёт рядом."""
    from sfstudio.services.qc import QcRunner
    from sfstudio.services.qc_report import report_html

    def apply(doc: SubtitleDocument) -> str:
        runner = QcRunner(profile=profile)
        runner.run_all(doc)
        issues = runner.all_issues()

        if report_dir is not None and doc.source_path is not None:
            report_dir.mkdir(parents=True, exist_ok=True)
            target = report_dir / tr('{0} — проверка.html').format(doc.source_path.stem)
            target.write_text(
                report_html(doc, issues, profile=profile), encoding="utf-8-sig"
            )
        return tr('замечаний: {0}').format(len(issues))

    return BatchTask(tr('проверка'), apply)


def run_batch(
    files: Iterable[Path],
    tasks: Sequence[BatchTask],
    *,
    output_dir: Path | None = None,
    suffix: str = "",
    overwrite: bool = False,
    encoding: str = "utf-8",
    progress: Callable[[int, int, Path], None] | None = None,
) -> list[BatchResult]:
    """Прогоняет задачи по файлам. Возвращает по записи на каждый файл.

    ``suffix`` дописывается к имени, ``output_dir`` уводит результат в другую
    папку. Без того и другого запись возможна только с ``overwrite``: молча
    затирать исходник, с которого работал человек, недопустимо.
    """
    from sfstudio.io import registry

    paths = [Path(item) for item in files]
    results: list[BatchResult] = []

    for index, path in enumerate(paths, start=1):
        if progress is not None:
            progress(index, len(paths), path)

        result = BatchResult(source=path)
        try:
            doc = registry.load(path)
        except Exception as exc:
            result.error = tr('не открылся: {0}').format(exc)
            results.append(result)
            continue

        if not doc.events and path.stat().st_size > 0:
            # Разбор субтитров всеяден: любой набор байтов «читается» как
            # файл без реплик. Записать пустышку и отчитаться об успехе —
            # худшее, что можно сделать с чужим файлом.
            result.error = tr('в файле нет реплик — похоже, это не субтитры')
            results.append(result)
            continue

        try:
            for task in tasks:
                note = task.apply(doc)
                if note:
                    result.notes.append(note)
        except Exception as exc:
            result.error = tr('ошибка на шаге «{0}»: {1}').format(task.title, exc)
            results.append(result)
            continue

        target = _target_path(path, doc, output_dir, suffix)
        if target == path and not overwrite:
            result.error = tr('перезапись исходника не разрешена')
            results.append(result)
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            registry.save(doc, target, encoding=encoding)
        except Exception as exc:
            result.error = tr('не записался: {0}').format(exc)
            results.append(result)
            continue

        result.written = target
        results.append(result)
    return results


def _target_path(
    source: Path,
    doc: SubtitleDocument,
    output_dir: Path | None,
    suffix: str,
) -> Path:
    """Куда писать итог: расширение берётся из формата документа."""
    from sfstudio.io.registry import FORMATS

    spec = FORMATS.get(doc.source_format)
    extension = spec.extensions[0] if spec and spec.extensions else source.suffix
    name = f"{source.stem}{suffix}{extension}"
    folder = output_dir if output_dir is not None else source.parent
    return Path(folder) / name


def describe(results: Sequence[BatchResult]) -> str:
    """Короткая сводка для строки состояния и для консоли."""
    from sfstudio.core.plural import plural

    good = sum(1 for r in results if r.ok)
    bad = len(results) - good
    parts = [plural(good, tr('файл готов'), tr('файла готовы'), tr('файлов готовы'))]
    if bad:
        parts.append(plural(bad, tr('не вышел'), tr('не вышло'), tr('не вышло')))
    return ", ".join(parts)
