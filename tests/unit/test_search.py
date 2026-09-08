"""Поиск и замена: ядро и окно.

Главная тонкость — разметка. В реплике `{\\i1}Привет{\\i0}` слово одно, но
строка содержит и теги; ищет человек по речи. Замена, залезшая в фигурные
скобки, ломает оформление по всему файлу, и заметно это становится нескоро.
"""

from __future__ import annotations

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.services.search import (
    SearchQuery,
    SearchScope,
    count_matches,
    find_all,
    replacements,
)


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(0, 2000, r"{\i1}Привет{\i0}, мир")
    document.create_event(3000, 5000, "Привет ещё раз")
    document.create_event(6000, 8000, "Совсем другое")
    return document


class TestBasicSearch:
    def test_finds_every_occurrence(self, doc) -> None:
        assert len(find_all(doc, SearchQuery("Привет"))) == 2

    def test_empty_query_finds_nothing(self, doc) -> None:
        assert find_all(doc, SearchQuery("")) == []

    def test_case_insensitive_by_default(self, doc) -> None:
        assert len(find_all(doc, SearchQuery("привет"))) == 2

    def test_case_sensitive_when_asked(self, doc) -> None:
        assert find_all(doc, SearchQuery("привет", case_sensitive=True)) == []

    def test_whole_word(self, doc) -> None:
        doc.create_event(9000, 10_000, "Приветствие")
        assert len(find_all(doc, SearchQuery("Привет", whole_word=True))) == 2

    def test_count_matches_agrees_with_find(self, doc) -> None:
        query = SearchQuery("е")
        assert count_matches(doc, query) == len(find_all(doc, query))

    def test_limit_stops_early(self, doc) -> None:
        assert len(find_all(doc, SearchQuery("е"), limit=2)) == 2


class TestTagsAreNotSpeech:
    """Содержимое фигурных скобок — оформление, а не текст реплики."""

    def test_tags_are_skipped_by_default(self, doc) -> None:
        assert find_all(doc, SearchQuery("i1")) == []

    def test_tags_can_be_searched_explicitly(self, doc) -> None:
        assert len(find_all(doc, SearchQuery("i1", include_tags=True))) == 1

    def test_replacement_keeps_the_markup(self, doc) -> None:
        changes = {eid: after for eid, _b, after in
                   replacements(doc, SearchQuery("Привет"), "Здравствуй")}
        first = doc.events[0]
        assert changes[first.eid] == r"{\i1}Здравствуй{\i0}, мир"

    def test_unclosed_brace_is_treated_as_markup_to_end(self) -> None:
        """Испорченный файл: libass считает такую скобку тянущейся до конца."""
        document = SubtitleDocument.blank()
        document.create_event(0, 1000, r"текст {\i1 без закрытия")
        assert find_all(document, SearchQuery("без")) == []


class TestScope:
    def test_selection_only(self, doc) -> None:
        chosen = [doc.events[1].eid]
        found = find_all(
            doc, SearchQuery("Привет", scope=SearchScope.SELECTION), selection=chosen
        )
        assert [m.eid for m in found] == chosen

    def test_from_cursor_skips_earlier(self, doc) -> None:
        found = find_all(
            doc,
            SearchQuery("Привет", scope=SearchScope.FROM_CURSOR),
            cursor_eid=doc.events[1].eid,
        )
        assert [m.eid for m in found] == [doc.events[1].eid]

    def test_from_cursor_without_cursor_searches_everything(self, doc) -> None:
        found = find_all(doc, SearchQuery("Привет", scope=SearchScope.FROM_CURSOR))
        assert len(found) == 2

    def test_every_scope_has_a_title(self) -> None:
        for scope in SearchScope:
            assert scope.title


class TestRegex:
    def test_pattern_matches(self, doc) -> None:
        assert len(find_all(doc, SearchQuery(r"При\w+", regex=True))) == 2

    def test_broken_pattern_is_not_a_crash(self, doc) -> None:
        """Незаконченное выражение — обычное состояние поля при наборе."""
        assert find_all(doc, SearchQuery("При(", regex=True)) == []
        assert SearchQuery("При(", regex=True).compile() is None

    def test_groups_work_in_replacement(self, doc) -> None:
        changes = list(replacements(doc, SearchQuery(r"(При)(вет)", regex=True), r"\2\1"))
        assert any("ветПри" in after for _e, _b, after in changes)

    def test_plain_replacement_is_literal(self, doc) -> None:
        r"""Без режима выражений «\1» — это просто символы, а не ссылка."""
        changes = list(replacements(doc, SearchQuery("Привет"), r"\1"))
        assert all(r"\1" in after for _e, _b, after in changes)

    def test_empty_match_is_ignored(self, doc) -> None:
        """Образец «а*» совпадает с пустотой везде — заменять там нечего."""
        assert find_all(doc, SearchQuery("я*", regex=True)) == []


class TestReplacements:
    def test_only_changed_events_are_reported(self, doc) -> None:
        changed = [eid for eid, _b, _a in replacements(doc, SearchQuery("Привет"), "Привет")]
        assert changed == []

    def test_several_hits_in_one_line(self) -> None:
        document = SubtitleDocument.blank()
        document.create_event(0, 1000, "раз раз раз")
        result = list(replacements(document, SearchQuery("раз"), "два"))
        assert result[0][2] == "два два два"

    def test_replacement_of_different_length_keeps_positions(self) -> None:
        """Замена идёт справа налево — иначе ранние позиции поехали бы."""
        document = SubtitleDocument.blank()
        document.create_event(0, 1000, "аб аб")
        result = list(replacements(document, SearchQuery("аб"), "длиннее"))
        assert result[0][2] == "длиннее длиннее"


class TestPreview:
    def test_preview_shows_context(self) -> None:
        document = SubtitleDocument.blank()
        document.create_event(0, 1000, "начало " * 20 + "искомое" + " конец" * 20)
        match = find_all(document, SearchQuery("искомое"))[0]
        preview = match.preview()
        assert "искомое" in preview
        assert preview.startswith("…") and preview.endswith("…")

    def test_fragment_is_the_match_itself(self, doc) -> None:
        match = find_all(doc, SearchQuery("Привет"))[0]
        assert match.fragment == "Привет"


class TestFindDialog:
    """Окно: замена одной командой и поведение при пустом запросе."""

    @pytest.fixture
    def qapp(self):
        pytest.importorskip("PySide6", reason="нужен PySide6")
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() or QApplication([])

    @pytest.fixture
    def dialog(self, qapp, doc):
        from sfstudio.core.undo import UndoStack
        from sfstudio.ui.find_dialog import FindDialog

        return FindDialog(doc, UndoStack(doc))

    def test_typing_updates_the_list(self, dialog) -> None:
        dialog.find_edit.setText("Привет")
        assert dialog.list.count() == 2

    def test_status_explains_an_empty_query(self, dialog) -> None:
        assert "Введите" in dialog.status.text()

    def test_status_reports_nothing_found(self, dialog) -> None:
        dialog.find_edit.setText("такого точно нет")
        assert "Ничего не найдено" in dialog.status.text()

    def test_replace_button_is_off_without_matches(self, dialog) -> None:
        dialog.find_edit.setText("такого точно нет")
        assert not dialog.replace_button.isEnabled()

    def test_replace_all_is_a_single_undo(self, dialog, doc, monkeypatch) -> None:
        """Массовая замена обязана откатываться одним движением."""
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.Yes)
        before = [event.text for event in doc.events]

        dialog.find_edit.setText("Привет")
        dialog.replace_edit.setText("Здравствуй")
        dialog._replace_all()
        assert doc.events[1].text == "Здравствуй ещё раз"

        dialog._undo.undo()
        assert [event.text for event in doc.events] == before

    def test_refusing_the_question_changes_nothing(self, dialog, doc, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.No)
        before = [event.text for event in doc.events]
        dialog.find_edit.setText("Привет")
        dialog.replace_edit.setText("Здравствуй")
        dialog._replace_all()
        assert [event.text for event in doc.events] == before

    def test_activating_a_row_reports_the_event(self, dialog, doc) -> None:
        seen: list[int] = []
        dialog.event_activated.connect(seen.append)
        dialog.find_edit.setText("Привет")
        dialog.list.setCurrentRow(1)
        assert seen and seen[-1] == doc.events[1].eid
