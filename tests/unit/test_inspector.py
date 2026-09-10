"""Тесты инспектора: чтение действующего оформления и запись через команды."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication

from sfstudio.core.color import RGBA
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.inspector import Inspector

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(1000, 2000, "Первая реплика")
    document.create_event(3000, 4000, r"{\b1\fs72}Жирная")
    return document


@pytest.fixture
def inspector(qapp: QApplication, doc: SubtitleDocument) -> Inspector:
    return Inspector(doc, UndoStack(doc))


class TestTabs:
    def test_event_tabs_are_there(self, inspector: Inspector) -> None:
        titles = [inspector.tabText(i) for i in range(inspector.count())]
        assert titles == ["Реплика", "Формат", "Кадр", "Проверки"]

    def test_disabled_without_selection(self, inspector: Inspector) -> None:
        """Без выделенной реплики править нечего — вкладки о ней гаснут.

        Гаснут именно они, а не вся колонка: в ней живут и панели, которым
        выделение не нужно, — оформление, акторы, замечания.
        """
        inspector.set_event(None)
        assert not inspector.widget_for("event").isEnabled()

    def test_enabled_with_selection(self, inspector: Inspector, doc) -> None:
        inspector.set_event(doc.events[0].eid)
        assert inspector.widget_for("event").isEnabled()

    def test_missing_event_is_treated_as_no_selection(self, inspector: Inspector) -> None:
        inspector.set_event(9999)
        assert not inspector.widget_for("event").isEnabled()


class TestReading:
    def test_timings_are_shown(self, inspector: Inspector, doc) -> None:
        inspector.set_event(doc.events[0].eid)
        assert inspector.start_edit.text() == "00:00:01,000"
        assert inspector.end_edit.text() == "00:00:02,000"

    def test_shows_effective_formatting_not_style(self, inspector: Inspector, doc) -> None:
        """Реплика с ``{\\b1\\fs72}`` идёт жирной 72-м, что бы ни говорил стиль."""
        inspector.set_event(doc.events[1].eid)
        assert inspector.bold_check.isChecked()
        assert inspector.size_spin.value() == 72.0

    def test_plain_event_shows_style_values(self, inspector: Inspector, doc) -> None:
        inspector.set_event(doc.events[0].eid)
        assert not inspector.bold_check.isChecked()
        assert inspector.size_spin.value() == doc.styles["Default"].fontsize

    def test_track_is_shown(self, inspector: Inspector, doc) -> None:
        from sfstudio.core.commands import AddTrack, MoveEventsToLayer

        inspector._undo.run(AddTrack("Надписи"))
        inspector._undo.run(MoveEventsToLayer([doc.events[0].eid], 1))
        inspector.set_event(doc.events[0].eid)
        assert inspector.track_box.currentText() == "Надписи"

    def test_unregistered_actor_is_still_shown(self, inspector: Inspector, doc) -> None:
        """Имя из чужого файла не заведено в реестре, но показать его надо."""
        doc.events[0].name = "Неизвестный"
        inspector.set_event(doc.events[0].eid)
        assert inspector.actor_box.currentText() == "Неизвестный"


class TestWriting:
    def test_bold_writes_a_tag(self, inspector: Inspector, doc) -> None:
        event = doc.events[0]
        inspector.set_event(event.eid)
        inspector.bold_check.click()
        assert r"\b1" in doc.by_eid(event.eid).text

    def test_edit_is_undoable(self, inspector: Inspector, doc) -> None:
        event = doc.events[0]
        before = event.text
        inspector.set_event(event.eid)
        inspector.bold_check.click()
        inspector._undo.undo()
        assert doc.by_eid(event.eid).text == before

    def test_alignment_writes_an_tag(self, inspector: Inspector, doc) -> None:
        event = doc.events[0]
        inspector.set_event(event.eid)
        index = inspector.align_box.findData(8)
        inspector.align_box.setCurrentIndex(index)
        assert doc.by_eid(event.eid).alignment_override() == 8

    def test_actor_assignment(self, inspector: Inspector, doc) -> None:
        doc.actors.add("Анна", RGBA.from_hex("#FF8800"))
        event = doc.events[0]
        inspector.set_event(event.eid)
        inspector.actor_box.setCurrentText("Анна")
        assert doc.by_eid(event.eid).name == "Анна"

    def test_track_change_moves_the_event(self, inspector: Inspector, doc) -> None:
        from sfstudio.core.commands import AddTrack

        inspector._undo.run(AddTrack("Надписи"))
        event = doc.events[0]
        inspector.set_event(event.eid)
        inspector.track_box.setCurrentIndex(inspector.track_box.findData(1))
        assert doc.by_eid(event.eid).layer == 1

    def test_margins_are_one_undo_step(self, inspector: Inspector, doc) -> None:
        """Правка трёх полей подряд — один жест, а не три шага отмены."""
        event = doc.events[0]
        inspector.set_event(event.eid)
        before = inspector._undo.depth_used
        inspector.margin_l_spin.setValue(30)
        inspector.margin_r_spin.setValue(40)
        inspector.margin_v_spin.setValue(50)
        assert inspector._undo.depth_used == before + 1
        assert (event.margin_l, event.margin_r, event.margin_v) == (30, 40, 50)

    def test_bad_timecode_is_rejected(self, inspector: Inspector, doc) -> None:
        """Мусор в поле времени возвращает прежнее значение, а не ломает реплику."""
        event = doc.events[0]
        inspector.set_event(event.eid)
        inspector.start_edit.setText("не время")
        inspector.start_edit.editingFinished.emit()
        assert doc.by_eid(event.eid).start == 1000
        assert inspector.start_edit.text() == "00:00:01,000"

    def test_start_cannot_pass_the_end(self, inspector: Inspector, doc) -> None:
        event = doc.events[0]
        inspector.set_event(event.eid)
        inspector.start_edit.setText("00:00:09,000")
        inspector.start_edit.editingFinished.emit()
        assert doc.by_eid(event.eid).start < doc.by_eid(event.eid).end

    def test_loading_does_not_write(self, inspector: Inspector, doc) -> None:
        """Показ значений не должен считаться правкой — иначе файл «грязный»."""
        before = inspector._undo.depth_used
        inspector.set_event(doc.events[1].eid)
        inspector.set_event(doc.events[0].eid)
        assert inspector._undo.depth_used == before


class TestChecks:
    def test_metrics_are_shown(self, inspector: Inspector, doc) -> None:
        inspector.set_event(doc.events[0].eid)
        assert inspector.chars_label.text() == str(len("Первая реплика"))
        assert inspector.lines_label.text() == "1"

    def test_no_issues_message(self, inspector: Inspector, doc) -> None:
        inspector.set_event(doc.events[0].eid)
        inspector.set_qc_notes([])
        assert "нет" in inspector.qc_label.text().lower()

    def test_issues_are_listed(self, inspector: Inspector, doc) -> None:
        inspector.set_event(doc.events[0].eid)
        inspector.set_qc_notes(["слишком быстро", "перекрытие"])
        assert "слишком быстро" in inspector.qc_label.text()
