"""Профили проверок из файлов, новые правила и отчёт о проверке."""

from __future__ import annotations

import json

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import FpsModel
from sfstudio.services.qc import PROFILES, QcProfile, QcRunner, Severity
from sfstudio.services.qc_profiles import (
    SUFFIX,
    available,
    example_text,
    read_pack,
)
from sfstudio.services.qc_report import report_csv, report_html, summary


@pytest.fixture
def document() -> SubtitleDocument:
    doc = SubtitleDocument.blank()
    for event in list(doc.events):
        doc.remove_event(event.eid)
    return doc


def write_profile(folder, name: str, data: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + SUFFIX)).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class TestProfileFiles:
    def test_file_profile_joins_the_builtin_ones(self, tmp_path) -> None:
        write_profile(tmp_path, "заказчик", {
            "name": "customer", "title": "Требования заказчика", "max_cps": 15,
        })
        packs = available(tmp_path)
        assert "customer" in packs
        assert set(PROFILES) <= set(packs)
        assert packs["customer"].profile.max_cps == 15.0

    def test_null_switches_a_rule_off(self, tmp_path) -> None:
        """«Не проверять» — законное требование, и оно должно быть выразимо."""
        write_profile(tmp_path, "мягкий", {"name": "soft", "max_cps": None})
        assert available(tmp_path)["soft"].profile.max_cps is None

    def test_unknown_keys_are_ignored(self, tmp_path) -> None:
        """Профиль мог быть написан под будущую версию."""
        write_profile(tmp_path, "будущий", {
            "name": "future", "max_cps": 17, "проверять_настроение": True,
        })
        pack = available(tmp_path)["future"]
        assert not pack.error
        assert pack.profile.max_cps == 17.0

    def test_bad_number_is_reported(self, tmp_path) -> None:
        write_profile(tmp_path, "битый", {"name": "broken", "max_cps": "быстро"})
        assert "max_cps" in available(tmp_path)["broken"].error

    def test_damaged_json_does_not_raise(self, tmp_path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / ("сломан" + SUFFIX)).write_text("{не json", encoding="utf-8")
        packs = available(tmp_path)
        assert any(pack.error for pack in packs.values())

    def test_file_may_replace_a_builtin(self, tmp_path) -> None:
        write_profile(tmp_path, "общий", {"name": "general", "max_cps": 9})
        assert available(tmp_path)["general"].profile.max_cps == 9.0

    def test_missing_folder_leaves_builtins(self, tmp_path) -> None:
        packs = available(tmp_path / "нет-такой")
        assert set(packs) == set(PROFILES)

    def test_example_is_readable(self, tmp_path) -> None:
        """Образец должен читаться самой программой, а не только человеком."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        path = tmp_path / ("образец" + SUFFIX)
        path.write_text(example_text(), encoding="utf-8")
        pack = read_pack(path)
        assert not pack.error
        assert pack.profile.max_cps == 17.0


class TestNewRules:
    def test_repeat_is_caught(self, document) -> None:
        document.create_event(0, 2000, "Одна и та же")
        document.create_event(2500, 4500, "Одна и та же")

        runner = QcRunner()
        runner.run_all(document)
        found = runner.issues_for(document.events[1].eid)
        assert any(issue.rule == "repeat" for issue in found)

    def test_repeat_ignores_markup_differences(self, document) -> None:
        """Для зрителя реплика та же — разница только в тегах."""
        document.create_event(0, 2000, "Текст")
        document.create_event(2500, 4500, r"{\i1}Текст{\i0}")

        runner = QcRunner()
        runner.run_all(document)
        assert any(
            issue.rule == "repeat"
            for issue in runner.issues_for(document.events[1].eid)
        )

    def test_repeat_can_be_switched_off(self, document) -> None:
        document.create_event(0, 2000, "Текст")
        document.create_event(2500, 4500, "Текст")

        runner = QcRunner(profile=QcProfile(name="без повторов", check_repeats=False))
        runner.run_all(document)
        assert not [
            i for i in runner.issues_for(document.events[1].eid) if i.rule == "repeat"
        ]

    def test_short_second_line_is_caught(self, document) -> None:
        document.create_event(0, 3000, "Длинная первая строка тут" + chr(92) + "Nи")

        runner = QcRunner(profile=QcProfile(name="строгий", min_line_length=8))
        runner.run_all(document)
        found = runner.issues_for(document.events[0].eid)
        assert any(issue.rule == "short_line" for issue in found)

    def test_single_short_line_is_fine(self, document) -> None:
        """Короткая реплика в одну строку — это нормально."""
        document.create_event(0, 3000, "Да.")

        runner = QcRunner(profile=QcProfile(name="строгий", min_line_length=8))
        runner.run_all(document)
        assert not [
            i for i in runner.issues_for(document.events[0].eid)
            if i.rule == "short_line"
        ]


class TestShotChange:
    class Keyframes:
        def __init__(self, times) -> None:
            self.times = list(times)

        def nearest(self, ms: int) -> int:
            return min(self.times, key=lambda t: abs(t - ms))

    def runner(self, keyframes=None) -> QcRunner:
        made = QcRunner(
            profile=QcProfile(name="вещание", shot_change_frames=3),
            fps=FpsModel.from_float(25.0),
        )
        made.keyframes = keyframes
        return made

    def test_edge_near_a_cut_is_flagged(self, document) -> None:
        document.create_event(10_000, 12_000, "Рядом со склейкой")
        runner = self.runner(self.Keyframes([9_950, 30_000]))
        runner.run_all(document)
        assert any(
            issue.rule == "shot_change"
            for issue in runner.issues_for(document.events[0].eid)
        )

    def test_far_from_a_cut_is_silent(self, document) -> None:
        document.create_event(10_000, 12_000, "Далеко от склейки")
        runner = self.runner(self.Keyframes([1_000, 30_000]))
        runner.run_all(document)
        assert not runner.issues_for(document.events[0].eid)

    def test_rule_is_silent_without_keyframes(self, document) -> None:
        """Без данных о склейках правило молчит, а не гадает."""
        document.create_event(10_000, 12_000, "Реплика")
        runner = self.runner(None)
        runner.run_all(document)
        assert not runner.issues_for(document.events[0].eid)

    def test_rule_is_off_by_default(self, document) -> None:
        """Обычному переводчику склейки не нужны — это требование вещателей."""
        assert PROFILES["general"].shot_change_frames is None


class TestReport:
    def prepared(self, document) -> tuple[SubtitleDocument, list]:
        document.create_event(0, 400, "Очень быстрая реплика с длинным текстом")
        document.create_event(1000, 3000, "Обычная")
        runner = QcRunner(profile=PROFILES["netflix-ru"])
        runner.run_all(document)
        return document, runner.all_issues()

    def test_csv_has_a_header_and_a_row_per_issue(self, document) -> None:
        doc, issues = self.prepared(document)
        lines = report_csv(doc, issues).strip().splitlines()
        assert lines[0].startswith("№;Начало;Конец")
        assert len(lines) == len(issues) + 1

    def test_html_names_the_profile_and_its_limits(self, document) -> None:
        doc, issues = self.prepared(document)
        page = report_html(doc, issues, profile=PROFILES["netflix-ru"])
        assert "Netflix" in page
        assert "знаков в секунду" in page
        assert "<table>" in page

    def test_clean_document_says_so(self, document) -> None:
        document.create_event(0, 3000, "Спокойная реплика")
        runner = QcRunner()
        runner.run_all(document)
        page = report_html(document, runner.all_issues())
        assert "Замечаний нет" in page

    def test_text_is_escaped(self, document) -> None:
        """Текст реплики приходит из файла и разметкой быть не должен."""
        document.create_event(0, 200, "<b>очень быстро</b> и с тегами")
        runner = QcRunner(profile=PROFILES["netflix-ru"])
        runner.run_all(document)

        page = report_html(document, runner.all_issues())
        assert "&lt;b&gt;" in page
        assert "<b>очень" not in page

    def test_summary_counts_by_severity(self, document) -> None:
        doc, issues = self.prepared(document)
        counts = summary(issues)
        assert sum(counts.values()) == len(issues)
        assert counts[Severity.WARNING] >= 1
