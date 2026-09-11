"""Пакетная обработка: одно действие над многими файлами."""

from __future__ import annotations

import pytest

from sfstudio.services.batch import (
    convert,
    describe,
    run_batch,
    run_checks,
    shift,
)
from sfstudio.services.qc import PROFILES

SRT = "1\n00:00:01,000 --> 00:00:03,000\nПервая\n\n2\n00:00:05,000 --> 00:00:07,000\nВторая\n"


@pytest.fixture
def folder(tmp_path):
    for index in range(3):
        (tmp_path / f"серия{index + 1}.srt").write_text(SRT, encoding="utf-8")
    return tmp_path


def sources(folder):
    return sorted(folder.glob("*.srt"))


class TestTasks:
    def test_shift_moves_every_event(self, folder) -> None:
        results = run_batch(sources(folder), [shift(500)], suffix=" сдвиг")
        assert all(result.ok for result in results)

        from sfstudio.io import registry

        doc = registry.load(results[0].written)
        assert doc.events[0].start == 1500

    def test_negative_shift_does_not_go_below_zero(self, tmp_path) -> None:
        """Отрицательное время не примет ни один плеер."""
        (tmp_path / "начало.srt").write_text(
            "1\n00:00:00,500 --> 00:00:02,000\nРано\n", encoding="utf-8"
        )
        results = run_batch(sources(tmp_path), [shift(-5000)], suffix=" сдвиг")

        from sfstudio.io import registry

        doc = registry.load(results[0].written)
        assert doc.events[0].start == 0
        assert doc.events[0].end >= 0

    def test_negative_shift_clamps_the_document_itself(self) -> None:
        """Через файл этого не проверить: формат и сам не пишет минус.

        ``format_srt`` обрезает отрицательное время до нуля, так что запись и
        чтение дают ноль даже без зажима в самой задаче — и снятый зажим
        прошёл бы незамеченным, оставив отрицательное время в документе.
        """
        from sfstudio.core.document import SubtitleDocument

        doc = SubtitleDocument.blank()
        doc.create_event(500, 2000, "Рано")
        shift(-5000).apply(doc)
        assert doc.events[0].start == 0
        assert doc.events[0].end == 0

    def test_convert_changes_the_extension(self, folder) -> None:
        results = run_batch(sources(folder), [convert("ass")], output_dir=folder / "out")
        assert all(result.written.suffix == ".ass" for result in results)

    def test_checks_write_reports(self, folder) -> None:
        reports = folder / "отчёты"
        results = run_batch(
            sources(folder),
            [run_checks(PROFILES["netflix-ru"], reports)],
            output_dir=folder / "out",
        )
        assert all(result.ok for result in results)
        assert len(list(reports.glob("*.html"))) == 3


class TestSafety:
    def test_source_is_not_overwritten_by_default(self, folder) -> None:
        """Молча затирать файл, с которого работал человек, недопустимо."""
        results = run_batch(sources(folder), [shift(100)])
        assert all(not result.ok for result in results)
        assert "перезапись" in results[0].error

    def test_overwrite_is_explicit(self, folder) -> None:
        results = run_batch(sources(folder), [shift(100)], overwrite=True)
        assert all(result.ok for result in results)

    def test_one_bad_file_does_not_stop_the_rest(self, folder) -> None:
        """«Упало на седьмом из двенадцати» после получаса — худший исход."""
        (folder / "мусор.srt").write_bytes(bytes(range(200)))
        results = run_batch(sources(folder), [shift(100)], suffix=" сдвиг")

        assert sum(1 for result in results if result.ok) == 3
        assert sum(1 for result in results if not result.ok) == 1

    def test_file_without_events_is_refused(self, tmp_path) -> None:
        """Разбор всеяден: любые байты «читаются» как файл без реплик."""
        (tmp_path / "чужой.srt").write_text("совсем не субтитры", encoding="utf-8")
        results = run_batch(sources(tmp_path), [shift(100)], suffix=" сдвиг")
        assert not results[0].ok
        assert "нет реплик" in results[0].error

    def test_missing_file_is_reported(self, tmp_path) -> None:
        results = run_batch([tmp_path / "нет-такого.srt"], [shift(100)])
        assert not results[0].ok


class TestReporting:
    def test_progress_is_called_for_every_file(self, folder) -> None:
        seen = []
        run_batch(
            sources(folder), [shift(0)], suffix=" копия",
            progress=lambda index, total, _path: seen.append((index, total)),
        )
        assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_summary_counts_both_sides(self, folder) -> None:
        (folder / "мусор.srt").write_text("не субтитры", encoding="utf-8")
        results = run_batch(sources(folder), [shift(10)], suffix=" сдвиг")
        text = describe(results)
        assert "3 файла готовы" in text
        assert "не вышло" in text or "не вышел" in text


class TestCommandLine:
    def run(self, args) -> int:
        from sfstudio.__main__ import main

        return main(args)

    def test_batch_from_the_command_line(self, folder, capsys) -> None:
        code = self.run([
            "--batch", *[str(p) for p in sources(folder)],
            "--shift", "500", "--to", "ass",
            "--out", str(folder / "готово"),
        ])
        assert code == 0
        assert len(list((folder / "готово").glob("*.ass"))) == 3

    def test_unknown_format_is_refused(self, folder, capsys) -> None:
        code = self.run(["--batch", str(sources(folder)[0]), "--to", "чужой"])
        assert code == 2
        assert "неизвестный формат" in capsys.readouterr().out

    def test_nothing_to_do_is_refused(self, folder, capsys) -> None:
        code = self.run(["--batch", str(sources(folder)[0])])
        assert code == 2
        assert "нечего делать" in capsys.readouterr().out

    def test_failure_gives_a_nonzero_code(self, tmp_path, capsys) -> None:
        """Команду ставят в цепочку: молчаливый успех при провале хуже отказа."""
        (tmp_path / "мусор.srt").write_text("не субтитры", encoding="utf-8")
        code = self.run([
            "--batch", str(tmp_path / "мусор.srt"), "--shift", "10",
            "--out", str(tmp_path / "out"),
        ])
        assert code == 1
