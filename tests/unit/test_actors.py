"""Панель акторов: список говорящих рядом с таблицей, а не в модальном окне."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(0, 2000, "Первая")
    document.create_event(3000, 5000, "Вторая")
    return document


@pytest.fixture
def undo(doc: SubtitleDocument) -> UndoStack:
    return UndoStack(doc)


class TestActorsPanel:
    """Панель вместо модального окна: назначать говорящих надо по ходу дела."""

    @pytest.fixture
    def panel(self, qapp, doc, undo):
        from sfstudio.ui.actors_dialog import ActorsPanel

        return ActorsPanel(doc, undo)

    def test_panel_lists_actors(self, panel, doc) -> None:
        doc.actors.add("Иван")
        panel.refresh()
        assert panel.table.rowCount() >= 1

    def test_assign_reports_the_chosen_actor(self, panel, doc) -> None:
        doc.actors.add("Иван")
        panel.refresh()
        seen: list[str] = []
        panel.assign_requested.connect(seen.append)

        panel.table.selectRow(0)
        panel.assign_button.click()
        assert seen == [panel._selected_name()]

    def test_assign_without_selection_is_silent(self, panel, doc) -> None:
        """Ничего не выбрано — просто ничего не происходит, без ошибки."""
        seen: list[str] = []
        panel.assign_requested.connect(seen.append)
        panel.assign_button.click()
        assert seen == []

    def test_switching_documents(self, panel, undo) -> None:
        """Панель в доке переживает открытие файла."""
        from sfstudio.core.document import SubtitleDocument
        from sfstudio.core.undo import UndoStack

        other = SubtitleDocument.blank()
        other.actors.add("Пётр")
        panel.set_document(other, UndoStack(other))
        names = [panel.table.item(r, 1).text() for r in range(panel.table.rowCount())]
        assert "Пётр" in names

    def test_dialog_still_works(self, qapp, doc, undo) -> None:
        """Пункт меню никуда не делся — окно осталось обёрткой над панелью."""
        from sfstudio.ui.actors_dialog import ActorsDialog

        doc.actors.add("Иван")
        dialog = ActorsDialog(doc, undo)
        dialog.refresh()
        assert dialog.table.rowCount() >= 1
        assert not dialog.panel.assign_button.isVisibleTo(dialog)
