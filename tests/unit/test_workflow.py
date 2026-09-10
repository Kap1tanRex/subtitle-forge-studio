"""Рабочие пометки: черновик, готово, вопрос — и заметки к репликам."""

from __future__ import annotations

import pytest

from sfstudio.core.commands import SetNote, SetStatus
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.core.workflow import (
    INFO_KEY,
    STATUS_DONE,
    STATUS_DRAFT,
    STATUS_QUESTION,
    apply_notes,
    collect_notes,
    is_known_status,
    progress,
    questions,
    status_title,
)
from sfstudio.io.formats.ass import read_ass, write_ass


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    for event in list(document.events):
        document.remove_event(event.eid)
    for i in range(5):
        document.create_event(i * 2000, i * 2000 + 1500, f"Реплика {i}")
    return document


class TestStatuses:
    def test_every_status_has_a_title(self) -> None:
        for key in (STATUS_DRAFT, STATUS_DONE, STATUS_QUESTION):
            assert status_title(key)
            assert is_known_status(key)

    def test_unknown_status_is_not_accepted(self) -> None:
        assert not is_known_status("сделано-как-нибудь")

    def test_progress_counts_only_done(self, doc) -> None:
        doc.events[0].status = STATUS_DONE
        doc.events[1].status = STATUS_DRAFT
        doc.events[2].status = STATUS_DONE
        assert progress(doc.events) == (2, 5)

    def test_questions_come_in_document_order(self, doc) -> None:
        doc.events[3].status = STATUS_QUESTION
        doc.events[1].status = STATUS_QUESTION
        assert questions(doc.events) == [doc.events[1].eid, doc.events[3].eid]

    def test_empty_document_has_no_progress(self) -> None:
        assert progress([]) == (0, 0)


class TestCommands:
    def test_status_applies_to_the_whole_group(self, doc) -> None:
        """Пометки ставят пачкой — и отменять их надо одним шагом."""
        undo = UndoStack(doc)
        eids = [event.eid for event in doc.events[:3]]
        undo.run(SetStatus(eids, STATUS_DONE))
        assert progress(doc.events) == (3, 5)

        undo.undo()
        assert progress(doc.events) == (0, 5)

    def test_note_is_undoable(self, doc) -> None:
        undo = UndoStack(doc)
        eid = doc.events[0].eid
        undo.run(SetNote(eid, "уточнить термин"))
        assert doc.by_eid(eid).note == "уточнить термин"
        undo.undo()
        assert doc.by_eid(eid).note == ""

    def test_removing_a_mark_is_a_normal_case(self, doc) -> None:
        undo = UndoStack(doc)
        eid = doc.events[0].eid
        undo.run(SetStatus([eid], STATUS_QUESTION))
        undo.run(SetStatus([eid], ""))
        assert doc.by_eid(eid).status == ""


class TestStorage:
    """Пометки живут в файле и не портят его для других программ."""

    def test_marks_survive_a_round_trip(self, doc) -> None:
        doc.events[0].status = STATUS_DONE
        doc.events[1].status = STATUS_QUESTION
        doc.events[1].note = "спросить, как переводить имя"

        back = read_ass(write_ass(doc))
        assert back.events[0].status == STATUS_DONE
        assert back.events[1].status == STATUS_QUESTION
        assert back.events[1].note == "спросить, как переводить имя"
        assert back.events[2].status == ""

    def test_clean_file_does_not_grow_a_key(self, doc) -> None:
        """Файл без пометок должен остаться обычным ASS."""
        assert INFO_KEY not in write_ass(doc)

    def test_note_with_commas_survives(self, doc) -> None:
        """Ровно поэтому пометки не в поле Effect: там запятая рвёт строку."""
        doc.events[0].note = "первое, второе, третье"
        back = read_ass(write_ass(doc))
        assert back.events[0].note == "первое, второе, третье"

    def test_effect_field_is_left_alone(self, doc) -> None:
        """Effect бывает занят настоящим эффектом, и затирать его нельзя."""
        doc.events[0].effect = "Banner;0;0;100"
        doc.events[0].status = STATUS_DONE
        back = read_ass(write_ass(doc))
        assert back.events[0].effect == "Banner;0;0;100"
        assert back.events[0].status == STATUS_DONE

    def test_broken_json_is_ignored(self, doc) -> None:
        """Испорченные пометки — не повод отказаться открыть файл."""
        assert apply_notes("{не json", doc.events) == 0
        assert apply_notes('{"i": 0}', doc.events) == 0
        assert all(event.status == "" for event in doc.events)

    def test_index_outside_the_document_is_skipped(self, doc) -> None:
        applied = apply_notes('[{"i": 99, "s": "done"}]', doc.events)
        assert applied == 0

    def test_unknown_status_is_dropped_but_note_survives(self, doc) -> None:
        """Пометка из будущей версии: рисовать её нечем, а заметку жалко."""
        apply_notes('[{"i": 0, "s": "утверждено", "n": "текст"}]', doc.events)
        assert doc.events[0].status == ""
        assert doc.events[0].note == "текст"

    def test_collect_skips_untouched_events(self, doc) -> None:
        doc.events[2].status = STATUS_DRAFT
        raw = collect_notes(doc.events)
        assert raw.count('"i"') == 1
        assert '"i":2' in raw
