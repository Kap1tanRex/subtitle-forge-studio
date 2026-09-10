"""Двуязычный режим в окне: колонка, поле над переводом, жизнь в проекте."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sfstudio.app.settings import Settings
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.reference import ReferenceLine, ReferenceTrack
from sfstudio.ui.event_table import COL_REFERENCE, COL_TEXT
from sfstudio.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path) -> Settings:
    store = Settings(tmp_path / "settings.json")
    store.set("storage.mode", "custom")
    store.set("storage.folder", str(tmp_path))
    return store


@pytest.fixture
def window(qapp, settings) -> MainWindow:
    win = MainWindow(settings=settings)
    doc = win._doc
    for event in list(doc.events):
        doc.remove_event(event.eid)
    doc.create_event(1000, 4000, "Привет.")
    doc.create_event(5000, 8000, "А ты храбрец.")
    doc.create_event(9000, 12000, "")
    win.model.reset_document(doc)
    return win


@pytest.fixture
def original() -> ReferenceTrack:
    return ReferenceTrack(
        [
            ReferenceLine(1000, 4000, "Hello there."),
            ReferenceLine(5000, 8000, "You are a bold one."),
            ReferenceLine(9000, 12000, "Your move."),
        ],
        source="original.srt",
        language="en",
    )


def shown(window: MainWindow, row: int, column: int) -> str:
    return window.model.data(window.model.index(row, column), Qt.DisplayRole) or ""


class TestColumn:
    def test_column_is_hidden_until_an_original_is_loaded(self, window) -> None:
        """Пустой столбец на четверть ширины отнимал бы место у текста."""
        assert window.table.isColumnHidden(COL_REFERENCE)

    def test_loading_shows_the_column(self, window, original) -> None:
        window.set_reference(original)
        assert not window.table.isColumnHidden(COL_REFERENCE)

    def test_closing_hides_it_again(self, window, original) -> None:
        window.set_reference(original)
        window.close_reference()
        assert window.table.isColumnHidden(COL_REFERENCE)
        assert window.reference is None

    def test_original_is_shown_next_to_the_translation(self, window, original) -> None:
        window.set_reference(original)
        assert shown(window, 0, COL_TEXT) == "Привет."
        assert shown(window, 0, COL_REFERENCE) == "Hello there."
        assert shown(window, 2, COL_REFERENCE) == "Your move."

    def test_original_is_visually_left_of_the_translation(self, window, original) -> None:
        """Читают слева направо: смотреть исходник после перевода неудобно."""
        window.set_reference(original)
        header = window.table.horizontalHeader()
        assert header.visualIndex(COL_REFERENCE) < header.visualIndex(COL_TEXT)

    def test_untranslated_line_still_shows_its_original(self, window, original) -> None:
        """Ровно тот случай, ради которого режим и нужен: перевода ещё нет."""
        window.set_reference(original)
        assert shown(window, 2, COL_TEXT) == ""
        assert shown(window, 2, COL_REFERENCE) == "Your move."


class TestEditorField:
    def test_field_appears_with_the_original(self, window, original) -> None:
        assert window.original.isHidden()
        window.set_reference(original)
        assert not window.original.isHidden()

    def test_field_follows_the_selection(self, window, original) -> None:
        window.set_reference(original)
        window._select_eid(window._doc.events[1].eid)
        assert window.original.toPlainText() == "You are a bold one."

    def test_several_lines_are_shown_separately(self, window) -> None:
        """Реплика накрыла две фразы — переводить надо обе, и это видно."""
        window.set_reference(
            ReferenceTrack([
                ReferenceLine(1000, 2000, "First half."),
                ReferenceLine(2000, 4000, "Second half."),
            ])
        )
        window._select_eid(window._doc.events[0].eid)
        assert window.original.toPlainText() == "First half.\nSecond half."

    def test_field_is_read_only(self, window, original) -> None:
        window.set_reference(original)
        assert window.original.isReadOnly()


class TestRetiming:
    def test_moving_an_event_changes_its_original(self, window, original) -> None:
        """Кэш обязан сбрасываться: иначе под репликой остался бы чужой текст."""
        from sfstudio.core.commands import SetTiming

        window.set_reference(original)
        assert shown(window, 0, COL_REFERENCE) == "Hello there."

        first = window._doc.events[0]
        window._undo.run(SetTiming(first.eid, start=5000, end=8000))
        assert shown(window, 0, COL_REFERENCE) == "You are a bold one."


class TestInsideProject:
    def test_original_survives_save_and_open(self, window, original, tmp_path, settings) -> None:
        window.set_reference(original)
        path = tmp_path / "работа.sfproj"
        assert window._write_project(path)

        again = MainWindow(settings=settings)
        assert again.open_project_file(path)
        assert again.reference is not None
        assert len(again.reference) == 3
        assert again.reference.source == "original.srt"
        assert not again.table.isColumnHidden(COL_REFERENCE)

    def test_project_without_original_opens_fine(self, window, tmp_path, settings) -> None:
        path = tmp_path / "без-оригинала.sfproj"
        assert window._write_project(path)

        again = MainWindow(settings=settings)
        assert again.open_project_file(path)
        assert again.reference is None
        assert again.table.isColumnHidden(COL_REFERENCE)

    def test_original_does_not_leak_into_exported_subtitles(self, window, original) -> None:
        """Оригинал — чужой текст. В файл, который сдают, он попасть не должен."""
        from sfstudio.io.formats.ass import write_ass

        window.set_reference(original)
        written = write_ass(window._doc)
        assert "Hello there." not in written
        assert "Привет." in written

    def test_autosave_copy_keeps_the_original(self, window, original, tmp_path) -> None:
        """Иначе восстановление из копии молча оставит переводчика без исходника."""
        from sfstudio.core.project import Project
        from sfstudio.io.project_file import load_project, save_project

        window.set_reference(original)
        window._project = Project.new("проба", (1920, 1080))
        window._project.reference = window.reference
        copy = window._project.snapshot(
            window._doc, None, window._capture_state()
        )
        path = save_project(copy, tmp_path / "копия.sfproj")
        assert len(load_project(path).reference) == 3


class TestSubtitleDocumentUntouched:
    def test_reference_is_not_part_of_the_document(self, window, original) -> None:
        """Оригинал не правят и не отменяют: он вне истории изменений."""
        window.set_reference(original)
        assert len(window._doc.events) == 3
        assert not hasattr(SubtitleDocument.blank(), "reference")
