"""Вкладка «Реплика» и блок текста под списком: чтение и запись командами."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication

from sfstudio.core.color import RGBA
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.core.workflow import STATUS_DONE, STATUS_QUESTION
from sfstudio.ui.cue_editor import CueEditor, CuePanel, gap_note

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(1000, 2000, "Первая реплика")
    document.create_event(2040, 4000, r"{\b1\fs72}Жирная")
    return document


@pytest.fixture
def panel(qapp: QApplication, doc: SubtitleDocument) -> CuePanel:
    return CuePanel(doc, UndoStack(doc))


class TestGap:
    FRAME = 1000 / 25

    def test_short_gap_is_an_alarm(self) -> None:
        text, alarm = gap_note(40, self.FRAME)
        assert alarm and "2 кадров" in text

    def test_overlap_is_an_alarm(self) -> None:
        text, alarm = gap_note(-120, self.FRAME)
        assert alarm and "перекрытие" in text

    def test_normal_gap_is_quiet(self) -> None:
        assert gap_note(500, self.FRAME) == ("0,500", False)


class TestReading:
    def test_nothing_selected(self, panel: CuePanel) -> None:
        panel.set_event(None)
        assert not panel.start_edit.isEnabled()
        assert panel.start_edit.text() == ""

    def test_missing_event_is_treated_as_no_selection(self, panel: CuePanel) -> None:
        panel.set_event(9999)
        assert not panel.start_edit.isEnabled()

    def test_timings_are_shown(self, panel: CuePanel, doc) -> None:
        panel.set_event(doc.events[0].eid)
        assert panel.start_edit.text() == "00:00:01,000"
        assert panel.end_edit.text() == "00:00:02,000"
        assert panel.duration_edit.text() == "1,000"
        assert panel.title.text() == "#1"

    def test_gap_to_the_next_cue(self, panel: CuePanel, doc) -> None:
        """40 мс между репликами — один кадр: это мигание, а не пауза."""
        panel.set_event(doc.events[0].eid)
        assert panel.gap_caption.text() == "Пауза до #2"
        assert "меньше 2 кадров" in panel.gap.text()
        assert panel.gap.property("role") == "chip-warning"

    def test_last_cue_has_no_gap(self, panel: CuePanel, doc) -> None:
        panel.set_event(doc.events[1].eid)
        assert panel.gap.text() == "—"

    def test_track_is_shown(self, panel: CuePanel, doc) -> None:
        from sfstudio.core.commands import AddTrack, MoveEventsToLayer

        panel._undo.run(AddTrack("Надписи"))
        panel._undo.run(MoveEventsToLayer([doc.events[0].eid], 1))
        panel.set_event(doc.events[0].eid)
        assert panel.track_box.currentText() == "Надписи"

    def test_unregistered_actor_is_still_shown(self, panel: CuePanel, doc) -> None:
        """Имя из чужого файла не заведено в реестре, но показать его надо."""
        doc.events[0].name = "Неизвестный"
        panel.set_event(doc.events[0].eid)
        assert panel.actor_box.currentText() == "Неизвестный"

    def test_status_is_shown(self, panel: CuePanel, doc) -> None:
        doc.events[0].status = STATUS_QUESTION
        panel.set_event(doc.events[0].eid)
        assert panel.status.index() == 2

    def test_loading_does_not_write(self, panel: CuePanel, doc) -> None:
        """Показ значений не должен считаться правкой — иначе файл «грязный»."""
        before = panel._undo.depth_used
        panel.set_event(doc.events[1].eid)
        panel.set_event(doc.events[0].eid)
        assert panel._undo.depth_used == before


class TestWriting:
    def test_actor_assignment(self, panel: CuePanel, doc) -> None:
        doc.actors.add("Анна", RGBA.from_hex("#FF8800"))
        event = doc.events[0]
        panel.set_event(event.eid)
        panel.actor_box.setCurrentText("Анна")
        panel.actor_box.lineEdit().editingFinished.emit()
        assert doc.by_eid(event.eid).name == "Анна"

    def test_track_change_moves_the_event(self, panel: CuePanel, doc) -> None:
        from sfstudio.core.commands import AddTrack

        panel._undo.run(AddTrack("Надписи"))
        event = doc.events[0]
        panel.set_event(event.eid)
        panel.track_box.setCurrentIndex(panel.track_box.findData(1))
        assert doc.by_eid(event.eid).layer == 1

    def test_bad_timecode_is_rejected(self, panel: CuePanel, doc) -> None:
        """Мусор в поле времени возвращает прежнее значение, а не ломает реплику."""
        event = doc.events[0]
        panel.set_event(event.eid)
        panel.start_edit.setText("не время")
        panel.start_edit.editingFinished.emit()
        assert doc.by_eid(event.eid).start == 1000
        assert panel.start_edit.text() == "00:00:01,000"

    def test_start_cannot_pass_the_end(self, panel: CuePanel, doc) -> None:
        event = doc.events[0]
        panel.set_event(event.eid)
        panel.start_edit.setText("00:00:09,000")
        panel.start_edit.editingFinished.emit()
        assert doc.by_eid(event.eid).start < doc.by_eid(event.eid).end

    def test_duration_moves_the_end(self, panel: CuePanel, doc) -> None:
        event = doc.events[0]
        panel.set_event(event.eid)
        panel.duration_edit.setText("2,5")
        panel.duration_edit.editingFinished.emit()
        assert doc.by_eid(event.eid).end == 3500

    def test_frame_step_moves_by_one_frame(self, panel: CuePanel, doc) -> None:
        event = doc.events[0]
        panel.frame_ms = 40.0
        panel.set_event(event.eid)
        panel.end_field.stepped.emit(1)
        assert doc.by_eid(event.eid).end == 2040

    def test_status_is_written_and_toggled_off(self, panel: CuePanel, doc) -> None:
        """Повторный щелчок по выбранной пометке снимает её."""
        event = doc.events[0]
        panel.set_event(event.eid)
        panel.status.button(1).click()
        assert doc.by_eid(event.eid).status == STATUS_DONE
        panel.status.button(1).click()
        assert doc.by_eid(event.eid).status == ""

    def test_note_is_one_undo_step(self, panel: CuePanel, doc) -> None:
        """Заметку пишут фразой — в историю она ложится одним шагом."""
        event = doc.events[0]
        panel.set_event(event.eid)
        before = panel._undo.depth_used
        panel.note.setPlainText("Уточнить имя")
        panel.note.moveCursor(panel.note.textCursor().MoveOperation.End)
        panel.note.insertPlainText(" у заказчика")
        panel._commit_note()
        assert doc.by_eid(event.eid).note == "Уточнить имя у заказчика"
        assert panel._undo.depth_used == before + 1

    def test_edit_is_undoable(self, panel: CuePanel, doc) -> None:
        event = doc.events[0]
        panel.set_event(event.eid)
        panel.status.button(0).click()
        panel._undo.undo()
        assert doc.by_eid(event.eid).status == ""


class TestCueEditor:
    def test_header_describes_the_cue(self, qapp, doc) -> None:
        editor = CueEditor()
        editor.show_event(doc, doc.events[0].eid)
        assert editor.number.text() == "#1"
        assert "00:00:01,000 → 00:00:02,000" in editor.times.text()
        assert editor.cps.text().startswith("CPS")

    def test_fast_cue_is_flagged(self, qapp, doc) -> None:
        doc.create_event(5000, 5300, "Очень длинная фраза за мгновение")
        editor = CueEditor()
        editor.show_event(doc, doc.events[-1].eid)
        assert editor.cps.property("role") == "chip-warning"

    def test_line_lengths_follow_the_text(self, qapp) -> None:
        editor = CueEditor()
        editor.editor.setPlainText("Четыре\nпять")
        assert editor.lengths.text() == "6\n4"


class TestEffective:
    def test_forge_sees_what_the_viewer_sees(self, doc) -> None:
        """Реплика с ``{\\b1\\fs72}`` идёт жирной 72-м, что бы ни говорил стиль."""
        from sfstudio.ui.main_window import _as_effective

        event = doc.events[1]
        shown = _as_effective(event, doc.style_for(event))
        assert shown.bold
        assert shown.fontsize == 72.0
        assert shown.name == event.style
