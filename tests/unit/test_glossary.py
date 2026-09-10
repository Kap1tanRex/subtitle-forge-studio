"""Глоссарий: поиск терминов, проверка перевода, обмен через CSV."""

from __future__ import annotations

import pytest

from sfstudio.core.glossary import Glossary, Term, stem


@pytest.fixture
def glossary() -> Glossary:
    return Glossary([
        Term("Ashley", "Эшли"),
        Term("Death Star", "Звезда Смерти"),
        Term("lightsaber", "световой меч", required=False),
    ])


class TestFinding:
    def test_terms_are_found_in_the_original(self, glossary) -> None:
        found = glossary.find_in("Ashley saw the Death Star")
        assert {t.source for t in found} == {"Ashley", "Death Star"}

    def test_case_does_not_matter(self, glossary) -> None:
        assert glossary.find_in("ashley is here")

    def test_partial_word_is_not_a_match(self, glossary) -> None:
        """«Ashleys» — другое слово, и подставлять сюда термин неверно."""
        assert glossary.find_in("Ashleyson was here") == []

    def test_nothing_found_in_unrelated_text(self, glossary) -> None:
        assert glossary.find_in("Just a normal line") == []

    def test_empty_glossary_finds_nothing(self) -> None:
        assert Glossary().find_in("Ashley") == []


class TestChecking:
    def test_correct_translation_passes(self, glossary) -> None:
        assert glossary.missing_in("Ashley is here", "Эшли здесь") == []

    def test_declension_is_accepted(self, glossary) -> None:
        """«Звезду Смерти» — то же самое, что «Звезда Смерти».

        Полная морфология тянет словарь на десятки мегабайт ради одной
        проверки; сравнение по началу слова ловит то же самое.
        """
        assert glossary.missing_in("I saw the Death Star", "Я видел Звезду Смерти") == []

    def test_wrong_name_is_reported(self, glossary) -> None:
        missing = glossary.missing_in("Ashley is here", "Ашлей здесь")
        assert [t.source for t in missing] == ["Ashley"]

    def test_half_translated_term_is_reported(self, glossary) -> None:
        """«Звезда» без «Смерти» — это не она."""
        missing = glossary.missing_in("the Death Star", "звезда")
        assert [t.source for t in missing] == ["Death Star"]

    def test_terms_absent_from_the_original_are_not_required(self, glossary) -> None:
        assert glossary.missing_in("Nothing special", "Ничего особенного") == []

    def test_optional_terms_are_checked_too(self, glossary) -> None:
        """Необязательный термин тоже проверяется — разница в важности."""
        missing = glossary.missing_in("his lightsaber", "его меч-сабля")
        assert [t.source for t in missing] == ["lightsaber"]


class TestStems:
    def test_short_words_stay_whole(self) -> None:
        assert stem("меч") == "меч"

    def test_long_words_lose_the_ending(self) -> None:
        assert stem("Звезду").startswith("звез")
        assert stem("Звезда")[:4] == stem("Звезду")[:4]

    def test_different_words_do_not_collide(self) -> None:
        assert stem("переводчик") != stem("проводник")


class TestCsv:
    def test_round_trip(self, glossary) -> None:
        back = Glossary.from_csv(glossary.to_csv())
        assert [(t.source, t.target, t.required) for t in back] == [
            ("Ashley", "Эшли", True),
            ("Death Star", "Звезда Смерти", True),
            ("lightsaber", "световой меч", False),
        ]

    def test_comma_separated_file_is_read(self) -> None:
        """Присылают и с точкой с запятой, и с запятой."""
        made = Glossary.from_csv("Ashley,Эшли\nDeath Star,Звезда Смерти")
        assert len(made) == 2

    def test_header_is_skipped(self) -> None:
        made = Glossary.from_csv("оригинал;перевод\nAshley;Эшли")
        assert [t.source for t in made] == ["Ashley"]

    def test_file_without_header_keeps_the_first_row(self) -> None:
        """Терять термин из-за отсутствия шапки нельзя."""
        made = Glossary.from_csv("Ashley;Эшли\nDeath Star;Звезда Смерти")
        assert len(made) == 2

    def test_incomplete_rows_are_skipped(self) -> None:
        made = Glossary.from_csv("Ashley;Эшли\nОдинокий\n;пусто\n")
        assert len(made) == 1

    def test_empty_text_gives_empty_glossary(self) -> None:
        assert not Glossary.from_csv("")
        assert not Glossary.from_csv("   \n  ")


class TestQualityRule:
    """Правило проверок: термин есть в оригинале, а в переводе его нет."""

    def make(self, translation: str, original: str):
        from sfstudio.core.document import SubtitleDocument
        from sfstudio.core.reference import ReferenceLine, ReferenceTrack
        from sfstudio.services.qc import QcRunner

        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(1000, 4000, translation)

        runner = QcRunner()
        runner.reference = ReferenceTrack([ReferenceLine(1000, 4000, original)])
        runner.glossary = Glossary([Term("Ashley", "Эшли")])
        runner.run_all(doc)
        return runner, doc

    def test_wrong_translation_is_flagged(self) -> None:
        runner, doc = self.make("Ашлей здесь", "Ashley is here")
        found = runner.issues_for(doc.events[0].eid)
        assert any(issue.rule == "glossary" for issue in found)

    def test_correct_translation_is_silent(self) -> None:
        runner, doc = self.make("Эшли здесь", "Ashley is here")
        assert not [i for i in runner.issues_for(doc.events[0].eid) if i.rule == "glossary"]

    def test_rule_is_silent_without_reference(self) -> None:
        """Без оригинала сравнивать не с чем — и догадываться не нужно."""
        from sfstudio.core.document import SubtitleDocument
        from sfstudio.services.qc import QcRunner

        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(1000, 4000, "Ашлей здесь")

        runner = QcRunner()
        runner.glossary = Glossary([Term("Ashley", "Эшли")])
        runner.run_all(doc)
        assert not [i for i in runner.issues_for(doc.events[0].eid) if i.rule == "glossary"]

    def test_required_and_optional_differ_in_severity(self) -> None:
        from sfstudio.core.document import SubtitleDocument
        from sfstudio.core.reference import ReferenceLine, ReferenceTrack
        from sfstudio.services.qc import QcRunner, Severity

        doc = SubtitleDocument.blank()
        for event in list(doc.events):
            doc.remove_event(event.eid)
        doc.create_event(1000, 4000, "его меч-сабля")

        runner = QcRunner()
        runner.reference = ReferenceTrack([ReferenceLine(1000, 4000, "his lightsaber")])
        runner.glossary = Glossary([Term("lightsaber", "световой меч", required=False)])
        runner.run_all(doc)

        found = [i for i in runner.issues_for(doc.events[0].eid) if i.rule == "glossary"]
        assert found and found[0].severity is Severity.INFO


class TestInsideProject:
    def test_glossary_survives_save_and_open(self, tmp_path, glossary) -> None:
        from sfstudio.core.project import Project
        from sfstudio.io.project_file import load_project, save_project

        project = Project(name="с глоссарием")
        project.glossary = glossary
        back = load_project(save_project(project, tmp_path / "работа.sfproj"))
        assert len(back.glossary) == 3

    def test_project_without_glossary_opens(self, tmp_path) -> None:
        from sfstudio.core.project import Project
        from sfstudio.io.project_file import load_project, save_project

        project = Project(name="пустой")
        back = load_project(save_project(project, tmp_path / "пустой.sfproj"))
        assert back.glossary is None
