"""Правка реплики прямо в таблице.

Раньше текст правился только в отдельном поле под таблицей: выбрать строку,
перевести взгляд вниз, поправить, вернуться. На тысяче реплик это тысяча
лишних движений — и это было главное неудобство работы со списком.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import Qt

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.event_table import (
    COL_ACTOR,
    COL_CPS,
    COL_DURATION,
    COL_END,
    COL_INDEX,
    COL_START,
    COL_STYLE,
    COL_TEXT,
    EventTableModel,
)


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(1000, 3000, "Первая реплика")
    document.create_event(4000, 6000, "Вторая реплика")
    document.actors.add("Иван")
    return document


@pytest.fixture
def undo(doc: SubtitleDocument) -> UndoStack:
    return UndoStack(doc)


@pytest.fixture
def model(doc: SubtitleDocument, undo: UndoStack) -> EventTableModel:
    return EventTableModel(doc, undo=undo)


def cell(model: EventTableModel, row: int, col: int):
    return model.index(row, col)


class TestEditableColumns:
    @pytest.mark.parametrize("col", [COL_TEXT, COL_START, COL_END, COL_STYLE, COL_ACTOR])
    def test_editable(self, model: EventTableModel, col: int) -> None:
        assert model.flags(cell(model, 0, col)) & Qt.ItemIsEditable

    @pytest.mark.parametrize("col", [COL_INDEX, COL_DURATION, COL_CPS])
    def test_computed_columns_are_not_editable(self, model: EventTableModel, col: int) -> None:
        """Длительность и CPS следуют из других полей — править их нечем."""
        assert not model.flags(cell(model, 0, col)) & Qt.ItemIsEditable

    def test_nothing_is_editable_without_undo(self, doc: SubtitleDocument) -> None:
        """Правка в обход истории отмены недопустима даже для самой таблицы."""
        read_only = EventTableModel(doc)
        assert not read_only.flags(cell(read_only, 0, COL_TEXT)) & Qt.ItemIsEditable
        assert not read_only.setData(cell(read_only, 0, COL_TEXT), "новое")


class TestEditingText:
    def test_text_is_changed(self, model, doc, undo) -> None:
        assert model.setData(cell(model, 0, COL_TEXT), "Исправленная", Qt.EditRole)
        assert doc.events[0].text == "Исправленная"

    def test_change_can_be_undone(self, model, doc, undo) -> None:
        """Ctrl+Z обязан вернуть правку — иначе она мимо истории."""
        before = doc.events[0].text
        model.setData(cell(model, 0, COL_TEXT), "Другое", Qt.EditRole)
        undo.undo()
        assert doc.events[0].text == before

    def test_same_value_is_not_a_change(self, model, doc, undo) -> None:
        """Открыть редактор и закрыть, ничего не поменяв, — не правка."""
        assert not model.setData(cell(model, 0, COL_TEXT), doc.events[0].text, Qt.EditRole)
        assert undo.is_clean

    def test_edit_role_returns_raw_text(self, model, doc) -> None:
        """В поле правки идёт разметка, а не готовый к показу текст."""
        doc.events[0].text = r"Первая\NВторая"
        assert model.data(cell(model, 0, COL_TEXT), Qt.EditRole) == r"Первая\NВторая"


class TestEditingTiming:
    def test_start_accepts_a_timecode(self, model, doc) -> None:
        assert model.setData(cell(model, 0, COL_START), "0:00:02.00", Qt.EditRole)
        assert doc.events[0].start == 2000

    def test_end_accepts_a_timecode(self, model, doc) -> None:
        assert model.setData(cell(model, 0, COL_END), "0:00:09.50", Qt.EditRole)
        assert doc.events[0].end == 9500

    def test_nonsense_is_refused(self, model, doc) -> None:
        """Неразобранное время не должно молча превращаться в ноль."""
        before = doc.events[0].start
        assert not model.setData(cell(model, 0, COL_START), "когда-нибудь", Qt.EditRole)
        assert doc.events[0].start == before


class TestEditingStyleAndActor:
    def test_actor_is_assigned(self, model, doc) -> None:
        assert model.setData(cell(model, 0, COL_ACTOR), "Иван", Qt.EditRole)
        assert doc.events[0].name == "Иван"

    def test_empty_actor_clears_the_assignment(self, model, doc, undo) -> None:
        """Снять говорящего надо уметь — это осмысленное состояние."""
        model.setData(cell(model, 0, COL_ACTOR), "Иван", Qt.EditRole)
        assert model.setData(cell(model, 0, COL_ACTOR), "", Qt.EditRole)
        assert doc.events[0].name == ""

    def test_existing_style_is_applied(self, model, doc) -> None:
        name = next(iter(doc.styles))
        doc.events[0].style = "другой"
        assert model.setData(cell(model, 0, COL_STYLE), name, Qt.EditRole)
        assert doc.events[0].style == name

    def test_unknown_style_is_refused(self, model, doc) -> None:
        """Иначе реплика нарисовалась бы умолчанием, и это выглядит как сбой."""
        before = doc.events[0].style
        assert not model.setData(cell(model, 0, COL_STYLE), "Такого-нет", Qt.EditRole)
        assert doc.events[0].style == before


class TestChoiceDelegate:
    def test_options_are_read_when_the_editor_opens(self, qapp_maybe) -> None:
        """Список берётся при открытии: стили заводят по ходу работы."""
        from sfstudio.ui.event_table import ChoiceDelegate

        names = ["первый"]
        delegate = ChoiceDelegate(lambda: names)
        names.append("второй")
        editor = delegate.createEditor(None, None, None)
        assert [editor.itemText(i) for i in range(editor.count())] == ["первый", "второй"]


@pytest.fixture(scope="module")
def qapp_maybe():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
