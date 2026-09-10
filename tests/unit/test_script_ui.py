"""Окно импорта текста без таймингов и вставка результата в документ."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication

from sfstudio.ui.script_dialog import ScriptImportDialog, read_text_file

pytestmark = pytest.mark.needs_gui

SCRIPT = """ИВАН: Ты вообще слушаешь?

МАРИЯ: Слушаю. Просто
не согласна.

ИВАН: Оно и видно.
"""

PLAIN = "Первая реплика\nВторая реплика\nТретья реплика\n"


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def plain_file(tmp_path: Path) -> Path:
    path = tmp_path / "перевод.txt"
    path.write_text(PLAIN, encoding="utf-8")
    return path


@pytest.fixture
def script_file(tmp_path: Path) -> Path:
    path = tmp_path / "сценарий.txt"
    path.write_text(SCRIPT, encoding="utf-8")
    return path


class TestReadingFiles:
    def test_utf8_is_read(self, plain_file: Path) -> None:
        assert "Первая" in read_text_file(plain_file)

    def test_cp1251_is_read(self, tmp_path: Path) -> None:
        """Сценарии из Word приходят в cp1251 чаще, чем в UTF-8."""
        path = tmp_path / "старый.txt"
        path.write_bytes("Реплика из прошлого\n".encode("cp1251"))
        assert "прошлого" in read_text_file(path)


class TestDialog:
    def test_lines_become_replicas(self, qapp, plain_file: Path) -> None:
        dialog = ScriptImportDialog(plain_file)
        assert len(dialog.lines()) == 3

    def test_preview_is_filled(self, qapp, plain_file: Path) -> None:
        dialog = ScriptImportDialog(plain_file)
        assert dialog.preview.topLevelItemCount() == 3

    def test_paragraph_layout_is_guessed(self, qapp, script_file: Path) -> None:
        """Реплика на два абзаца — частая вёрстка, и её стоит распознать."""
        dialog = ScriptImportDialog(script_file)
        assert dialog.by_paragraphs.isChecked()

    def test_switching_the_split_rebuilds_the_preview(
        self, qapp, script_file: Path
    ) -> None:
        dialog = ScriptImportDialog(script_file)
        before = len(dialog.lines())
        dialog.by_lines.setChecked(True)
        assert len(dialog.lines()) != before

    def test_speakers_go_to_the_actor_column(self, qapp, script_file: Path) -> None:
        dialog = ScriptImportDialog(script_file)
        dialog.speakers_check.setChecked(True)
        assert dialog.preview.topLevelItem(0).text(1) == "ИВАН"

    def test_speakers_are_off_by_default(self, qapp, script_file: Path) -> None:
        """Не всякий текст со двоеточиями — сценарий с именами."""
        dialog = ScriptImportDialog(script_file)
        assert not dialog.speakers_check.isChecked()
        assert dialog.preview.topLevelItem(0).text(1) == ""

    def test_import_is_refused_for_an_empty_file(self, qapp, tmp_path: Path) -> None:
        path = tmp_path / "пусто.txt"
        path.write_text("\n\n\n", encoding="utf-8")
        dialog = ScriptImportDialog(path)
        assert not dialog.lines()
        assert "не нашлось" in dialog.summary.text()

    def test_no_file_yet_says_so(self, qapp) -> None:
        dialog = ScriptImportDialog()
        assert not dialog.lines()
        assert "Выберите файл" in dialog.summary.text()

    def test_count_is_reported(self, qapp, plain_file: Path) -> None:
        assert "3 реплики" in ScriptImportDialog(plain_file).summary.text()

    def test_times_go_one_after_another(self, qapp, plain_file: Path) -> None:
        times = ScriptImportDialog(plain_file).times()
        assert len(times) == 3
        assert times[1][0] > times[0][1]


class TestInsertion:
    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def imported(self, window, path: Path):
        dialog = ScriptImportDialog(path)
        window._insert_script(dialog.lines(), dialog.times())
        return dialog

    def test_menu_has_the_action(self, window) -> None:
        assert window._actions_registry.action("file.import_script") is not None

    def test_replicas_are_added(self, window, plain_file: Path) -> None:
        before = len(window._doc)
        self.imported(window, plain_file)
        assert len(window._doc) == before + 3

    def test_import_is_a_single_undo_step(self, window, plain_file: Path) -> None:
        """Триста реплик — не триста нажатий «Отменить»."""
        before = window._undo.depth_used
        self.imported(window, plain_file)
        assert window._undo.depth_used == before + 1

    def test_undo_removes_everything(self, window, plain_file: Path) -> None:
        before = len(window._doc)
        self.imported(window, plain_file)
        window._undo.undo()
        assert len(window._doc) == before

    def test_existing_replicas_are_kept(self, window, plain_file: Path) -> None:
        """Импорт добавляет, а не стирает чужую работу."""
        window._doc.create_event(0, 2000, "Уже была")
        window._doc.rebuild_lookup()
        self.imported(window, plain_file)
        assert any(event.plain == "Уже была" for event in window._doc.events)

    def test_imported_replicas_do_not_land_on_existing_ones(
        self, window, plain_file: Path
    ) -> None:
        window._doc.create_event(0, 60_000, "Длинная существующая")
        window._doc.rebuild_lookup()
        self.imported(window, plain_file)
        added = [e for e in window._doc.events if e.plain == "Первая реплика"]
        assert added and added[0].start >= 60_000

    def test_actors_are_carried_over(self, window, script_file: Path) -> None:
        dialog = ScriptImportDialog(script_file)
        dialog.speakers_check.setChecked(True)
        window._insert_script(dialog.lines(), dialog.times())
        assert any(event.name == "ИВАН" for event in window._doc.events)

    def test_nothing_to_import_changes_nothing(self, window) -> None:
        before = len(window._doc)
        window._insert_script([], [])
        assert len(window._doc) == before
