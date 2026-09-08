"""Тесты контроля качества."""

from __future__ import annotations

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import FpsModel
from sfstudio.services.qc import PROFILES, QcProfile, QcRunner, Severity


def make(*specs: tuple[int, int, str], **styles: object) -> SubtitleDocument:
    doc = SubtitleDocument.blank()
    for start, end, text in specs:
        doc.create_event(start, end, text)
    return doc


def rules_for(runner: QcRunner, doc: SubtitleDocument, position: int = 0) -> set[str]:
    return {i.rule for i in runner.issues_for(doc.events[position].eid)}


@pytest.fixture
def runner() -> QcRunner:
    return QcRunner(profile=PROFILES["netflix-ru"], fps=FpsModel())


class TestCps:
    def test_flags_fast_text(self, runner: QcRunner) -> None:
        doc = make((0, 500, "Очень много букв за очень короткое время, читать невозможно"))
        runner.run_all(doc)
        assert "cps" in rules_for(runner, doc)

    def test_ignores_comfortable_text(self, runner: QcRunner) -> None:
        doc = make((0, 4000, "Спокойная реплика"))
        runner.run_all(doc)
        assert "cps" not in rules_for(runner, doc)

    def test_spaces_excluded_by_default(self) -> None:
        """Netflix считает CPS без пробелов — с ними порог достигался бы раньше."""
        text = "а " * 30
        doc = make((0, 2000, text.strip()))
        without = QcRunner(profile=QcProfile(max_cps=17.0, cps_counts_spaces=False))
        with_spaces = QcRunner(profile=QcProfile(max_cps=17.0, cps_counts_spaces=True))
        without.run_all(doc)
        with_spaces.run_all(doc)
        assert len(with_spaces.all_issues()) >= len(without.all_issues())

    def test_disabled_by_profile(self) -> None:
        doc = make((0, 100, "Очень быстрая длинная реплика для проверки"))
        runner = QcRunner(profile=PROFILES["loose"])
        runner.run_all(doc)
        assert "cps" not in rules_for(runner, doc)

    def test_zero_duration_does_not_divide_by_zero(self, runner: QcRunner) -> None:
        doc = make((1000, 1000, "Текст"))
        runner.run_all(doc)  # не должно падать
        assert "duration" in rules_for(runner, doc)


class TestLines:
    def test_long_line(self, runner: QcRunner) -> None:
        doc = make((0, 5000, "к" * 60))
        runner.run_all(doc)
        assert "line_length" in rules_for(runner, doc)

    def test_measures_longest_line_not_total(self, runner: QcRunner) -> None:
        """Две короткие строки не должны считаться одной длинной."""
        doc = make((0, 5000, r"Короткая\NТоже короткая"))
        runner.run_all(doc)
        assert "line_length" not in rules_for(runner, doc)

    def test_too_many_lines(self, runner: QcRunner) -> None:
        doc = make((0, 5000, r"Раз\NДва\NТри"))
        runner.run_all(doc)
        assert "line_count" in rules_for(runner, doc)

    def test_tags_do_not_count_as_text(self, runner: QcRunner) -> None:
        """Длина считается по видимому тексту, теги не в счёт."""
        doc = make((0, 5000, r"{\pos(100,200)\an5\b1\i1\fs48}Коротко"))
        runner.run_all(doc)
        assert "line_length" not in rules_for(runner, doc)


class TestDuration:
    def test_too_short(self, runner: QcRunner) -> None:
        doc = make((0, 300, "Миг"))
        runner.run_all(doc)
        assert "min_duration" in rules_for(runner, doc)

    def test_too_long_is_only_info(self, runner: QcRunner) -> None:
        doc = make((0, 20_000, "Очень долго висит"))
        runner.run_all(doc)
        issues = runner.issues_for(doc.events[0].eid)
        long_ones = [i for i in issues if i.rule == "max_duration"]
        assert long_ones and long_ones[0].severity is Severity.INFO

    def test_negative_duration_is_error(self, runner: QcRunner) -> None:
        doc = SubtitleDocument.blank()
        event = doc.create_event(1000, 2000, "Текст")
        event.end = 500  # прямая порча, чтобы проверить устойчивость
        doc.touch()
        runner.run_all(doc)
        assert runner.worst_for(event.eid) is Severity.ERROR


class TestNeighbours:
    def test_gap_too_small(self, runner: QcRunner) -> None:
        doc = make((0, 1000, "Первая"), (1020, 2000, "Вторая"))
        runner.run_all(doc)
        assert "min_gap" in rules_for(runner, doc, 0)

    def test_comfortable_gap_is_fine(self, runner: QcRunner) -> None:
        doc = make((0, 1000, "Первая"), (1500, 2500, "Вторая"))
        runner.run_all(doc)
        assert "min_gap" not in rules_for(runner, doc, 0)

    def test_overlap_is_warning_not_error(self, runner: QcRunner) -> None:
        """В ASS перекрытие законно — ругаться как на ошибку неверно."""
        doc = make((0, 2000, "Первая"), (1500, 3000, "Вторая"))
        runner.run_all(doc)
        issues = [i for i in runner.issues_for(doc.events[0].eid) if i.rule == "overlap"]
        assert issues
        assert issues[0].severity is Severity.WARNING

    def test_overlap_ignored_across_layers(self, runner: QcRunner) -> None:
        """Надпись поверх диалога — обычный приём, а не проблема."""
        doc = make((0, 2000, "Диалог"), (1500, 3000, "Надпись"))
        doc.events[1].layer = 5
        doc.touch()
        runner.run_all(doc)
        assert "overlap" not in rules_for(runner, doc, 0)

    def test_last_event_has_no_gap_check(self, runner: QcRunner) -> None:
        doc = make((0, 1000, "Единственная"))
        runner.run_all(doc)
        assert "min_gap" not in rules_for(runner, doc)


class TestStructure:
    def test_missing_style_is_error(self, runner: QcRunner) -> None:
        doc = make((0, 3000, "Текст"))
        doc.events[0].style = "Которого нет"
        runner.run_all(doc)
        issues = runner.issues_for(doc.events[0].eid)
        assert any(i.rule == "missing_style" and i.severity is Severity.ERROR for i in issues)

    def test_unbalanced_braces(self, runner: QcRunner) -> None:
        doc = make((0, 3000, r"{\b1 текст без закрывающей"))
        runner.run_all(doc)
        assert "unbalanced_tags" in rules_for(runner, doc)

    def test_empty_text(self, runner: QcRunner) -> None:
        doc = make((0, 3000, "   "))
        runner.run_all(doc)
        assert "empty_text" in rules_for(runner, doc)

    def test_comment_may_be_empty(self, runner: QcRunner) -> None:
        doc = make((0, 3000, ""))
        doc.events[0].comment = True
        runner.run_all(doc)
        assert "empty_text" not in rules_for(runner, doc)


class TestTypography:
    def test_double_space(self, runner: QcRunner) -> None:
        doc = make((0, 3000, "Два  пробела"))
        runner.run_all(doc)
        assert "double_space" in rules_for(runner, doc)

    def test_trailing_space(self, runner: QcRunner) -> None:
        doc = make((0, 3000, "Хвост "))
        runner.run_all(doc)
        assert "trailing_space" in rules_for(runner, doc)

    def test_hyphen_instead_of_dash(self, runner: QcRunner) -> None:
        doc = make((0, 3000, "- Реплика с дефисом"))
        runner.run_all(doc)
        assert "wrong_dash" in rules_for(runner, doc)

    def test_typography_off_in_loose_profile(self) -> None:
        doc = make((0, 3000, "Два  пробела "))
        runner = QcRunner(profile=PROFILES["loose"])
        runner.run_all(doc)
        assert not rules_for(runner, doc) & {"double_space", "trailing_space"}


class TestIncremental:
    def test_recheck_updates_changed_event(self, runner: QcRunner) -> None:
        doc = make((0, 300, "Слишком коротко"))
        runner.run_all(doc)
        assert "min_duration" in rules_for(runner, doc)

        doc.events[0].end = 4000
        doc.touch()
        runner.recheck(doc, [doc.events[0].eid])
        assert "min_duration" not in rules_for(runner, doc)

    def test_recheck_updates_neighbour(self, runner: QcRunner) -> None:
        """Правка одного события меняет вердикт для предыдущего.

        Иначе предупреждение о зазоре осталось бы висеть после того, как
        проблему уже устранили.
        """
        doc = make((0, 1000, "Первая"), (1020, 2000, "Вторая"))
        runner.run_all(doc)
        assert "min_gap" in rules_for(runner, doc, 0)

        doc.events[1].start = 2000
        doc.events[1].end = 3000
        doc.touch()
        runner.recheck(doc, [doc.events[1].eid])
        assert "min_gap" not in rules_for(runner, doc, 0)

    def test_recheck_reports_changed_eids(self, runner: QcRunner) -> None:
        doc = make((0, 4000, "Нормально"), (5000, 9000, "Тоже"))
        runner.run_all(doc)
        doc.events[0].set_text("к" * 80)
        doc.touch()
        changed = runner.recheck(doc, [doc.events[0].eid])
        assert doc.events[0].eid in changed

    def test_removed_event_drops_issues(self, runner: QcRunner) -> None:
        doc = make((0, 200, "Коротко"), (2000, 5000, "Нормально"))
        runner.run_all(doc)
        eid = doc.events[0].eid
        assert runner.issues_for(eid)

        doc.remove_event(eid)
        runner.recheck(doc, [eid])
        assert runner.issues_for(eid) == []

    def test_recheck_matches_full_run(self, runner: QcRunner) -> None:
        """Инкрементальный результат обязан совпадать с полным прогоном."""
        doc = make(
            (0, 1000, "Первая"), (1020, 2000, "к" * 60),
            (2000, 2100, "Третья"), (5000, 9000, "Четвёртая  реплика"),
        )
        runner.run_all(doc)
        doc.events[1].set_text("Стало нормально")
        doc.events[1].end = 4000
        doc.touch()
        runner.recheck(doc, [doc.events[1].eid])
        incremental = {i.eid: sorted(x.rule for x in runner.issues_for(i.eid))
                       for i in doc.events}

        fresh = QcRunner(profile=runner.profile, fps=runner.fps)
        fresh.run_all(doc)
        full = {i.eid: sorted(x.rule for x in fresh.issues_for(i.eid)) for i in doc.events}
        assert incremental == full


class TestReporting:
    def test_summary_when_clean(self, runner: QcRunner) -> None:
        doc = make((0, 4000, "Всё хорошо"))
        runner.run_all(doc)
        assert runner.summary() == "Проблем не найдено"

    def test_counts_by_severity(self, runner: QcRunner) -> None:
        doc = make((0, 200, "Коротко"))
        doc.events[0].style = "Нет такого"
        runner.run_all(doc)
        counts = runner.counts()
        assert counts[Severity.ERROR] >= 1
        assert counts[Severity.WARNING] >= 1

    def test_all_issues_sorted_by_severity(self, runner: QcRunner) -> None:
        doc = make((0, 200, "Хвост "))
        doc.events[0].style = "Нет такого"
        runner.run_all(doc)
        severities = [i.severity for i in runner.all_issues()]
        assert severities == sorted(severities, reverse=True)

    def test_worst_for(self, runner: QcRunner) -> None:
        doc = make((0, 4000, "Норма"))
        doc.events[0].style = "Нет такого"
        runner.run_all(doc)
        assert runner.worst_for(doc.events[0].eid) is Severity.ERROR

    def test_worst_for_clean_event(self, runner: QcRunner) -> None:
        doc = make((0, 4000, "Всё хорошо"))
        runner.run_all(doc)
        assert runner.worst_for(doc.events[0].eid) is None


class TestProfiles:
    def test_all_profiles_are_usable(self) -> None:
        doc = make((0, 1000, "Проверка"), (1100, 3000, "Вторая реплика"))
        for key, profile in PROFILES.items():
            runner = QcRunner(profile=profile, fps=FpsModel())
            runner.run_all(doc)
            assert isinstance(runner.summary(), str), key

    def test_switching_profile_reruns(self, runner: QcRunner) -> None:
        doc = make((0, 300, "Коротко"))
        runner.run_all(doc)
        strict = len(runner.all_issues())
        runner.set_profile(PROFILES["loose"], doc)
        assert len(runner.all_issues()) < strict

    def test_gap_threshold_depends_on_fps(self) -> None:
        """Два кадра при 24 fps и при 60 fps — разное время."""
        from fractions import Fraction

        doc = make((0, 1000, "Первая"), (1050, 2000, "Вторая"))
        slow = QcRunner(profile=PROFILES["netflix-ru"], fps=FpsModel(Fraction(24, 1)))
        fast = QcRunner(profile=PROFILES["netflix-ru"], fps=FpsModel(Fraction(60, 1)))
        slow.run_all(doc)
        fast.run_all(doc)
        slow_rules = {i.rule for i in slow.issues_for(doc.events[0].eid)}
        fast_rules = {i.rule for i in fast.issues_for(doc.events[0].eid)}
        assert "min_gap" in slow_rules  # 2 кадра = 83 мс, зазор 50 мс мал
        assert "min_gap" not in fast_rules  # 2 кадра = 33 мс, зазор достаточен
