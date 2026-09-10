"""Оформление применяется сразу, а куда — решает выделение."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from _pytest.monkeypatch import MonkeyPatch
from PySide6.QtWidgets import QApplication

from sfstudio.core.color import RGBA
from sfstudio.core.commands import UpdateStyle
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.effective import style_tags
from sfstudio.core.style import SubtitleStyle
from sfstudio.core.undo import UndoStack

# Отдельным проходом, в чистом процессе. Тесты крутят очередь событий, а в
# общем прогоне в ней копится чужое добро — окна, которые предыдущие файлы
# отправили на удаление, но так и не дождались. Разбор этой очереди обрывает
# процесс, и падает при этом тот, кто её крутит, а не тот, кто насорил.
pytestmark = [pytest.mark.needs_gui, pytest.mark.serial]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def app_window(qapp: QApplication, tmp_path_factory):
    """Окно на весь файл: каждое стоит десятков виджетов и рендерера libass.

    Заводить его на каждый тест — верный способ уронить общий прогон: в
    одном процессе таких окон набирается слишком много, и Qt в какой-то
    момент просто обрывает процесс.
    """
    patch = MonkeyPatch()
    patch.setenv("SFSTUDIO_HOME", str(tmp_path_factory.mktemp("home")))
    from sfstudio.ui.main_window import MainWindow

    made = MainWindow()
    made.resize(1200, 800)
    # Плеер здесь не нужен, а его фоновый поток живёт своей жизнью и в общем
    # прогоне, где таких окон много, обрывает процесс целиком.
    made.video_pane.close_player()

    yield made

    # Перед закрытием документ помечается сохранённым: closeEvent честно
    # спрашивает про несохранённые правки, и в прогоне без человека
    # модальный вопрос повис бы навсегда.
    made._undo.mark_clean()
    made.close()
    made.deleteLater()
    patch.undo()


@pytest.fixture
def window(app_window, qapp: QApplication):
    """Чистый документ и настройки перед каждым тестом."""
    document = SubtitleDocument.blank()
    document.create_event(0, 3000, "Первая")
    document.create_event(4000, 7000, "Вторая")
    app_window._load_document(document)

    # Кузница держит поля от прежнего теста; без сброса «поставить 90»
    # там, где уже стоит 90, не выдаст ни одного сигнала.
    app_window.style_forge.set_style(document.styles["Default"])
    app_window._settings.set("subtitles.actor_label", "off")
    app_window._label_template_applied = app_window.label_template()
    app_window._pending_style = None
    app_window._forge_timer.stop()
    qapp.processEvents()
    return app_window


class TestStyleTags:
    """Разница между стилями, выраженная тегами."""

    def test_same_style_gives_only_removals(self) -> None:
        """Совпало — значит тега быть не должно, даже если он там был."""
        base = SubtitleStyle()
        assert all(value is None for value in style_tags(base, base).values())

    def test_difference_becomes_a_tag(self) -> None:
        base = SubtitleStyle()
        tags = style_tags(replace(base, fontsize=72), base)
        assert tags["fs"] == "72"

    def test_colour_is_written_the_ass_way(self) -> None:
        base = SubtitleStyle()
        tags = style_tags(replace(base, primary=RGBA(255, 0, 0)), base)
        assert tags["c"] == "&H0000FF"

    def test_flags_are_numbers(self) -> None:
        base = SubtitleStyle()
        tags = style_tags(replace(base, bold=True), base)
        assert tags["b"] == 1

    def test_number_has_no_trailing_zero(self) -> None:
        """«bord2» читается, «bord2.0» — лишний шум в тексте реплики."""
        base = SubtitleStyle()
        tags = style_tags(replace(base, outline=2.0), SubtitleStyle(outline=1.0))
        assert tags["bord"] == "2"

    def test_missing_base_is_the_default_style(self) -> None:
        assert style_tags(SubtitleStyle(), None)["fs"] is None


class TestUndoCoalescing:
    def test_slider_moves_become_one_step(self) -> None:
        """Десять движений мышью — не десять нажатий «Отменить»."""
        doc = SubtitleDocument.blank()
        undo = UndoStack(doc)
        base = doc.styles["Default"]

        before = undo.depth_used
        for size in range(40, 90, 5):
            undo.run(UpdateStyle("Default", replace(base, fontsize=size)))

        assert undo.depth_used == before + 1

    def test_undo_returns_to_the_start_of_the_gesture(self) -> None:
        doc = SubtitleDocument.blank()
        undo = UndoStack(doc)
        was = doc.styles["Default"].fontsize

        for size in (40, 50, 60):
            undo.run(UpdateStyle("Default", replace(doc.styles["Default"], fontsize=size)))
        undo.undo()
        assert doc.styles["Default"].fontsize == was

    def test_another_style_is_a_separate_step(self) -> None:
        """Иначе одно «Отменить» вернуло бы правки двух разных стилей."""
        doc = SubtitleDocument.blank()
        doc.styles["Крупный"] = SubtitleStyle(name="Крупный")
        undo = UndoStack(doc)

        before = undo.depth_used
        undo.run(UpdateStyle("Default", replace(doc.styles["Default"], fontsize=40)))
        undo.run(UpdateStyle("Крупный", replace(doc.styles["Крупный"], fontsize=90)))
        assert undo.depth_used == before + 2


class TestPresetApplies:
    def test_choosing_a_preset_changes_the_document(self, window) -> None:
        """Раньше набор менял только поля окна, а субтитры не трогал."""
        was = window._doc.styles["Default"].fontsize
        window.table.clearSelection()
        window.style_forge.presets._buttons[1].click()
        window._flush_forge_edit()
        assert window._doc.styles["Default"].fontsize != was

    def test_preset_lands_in_the_history(self, window) -> None:
        before = window._undo.depth_used
        window.table.clearSelection()
        window.style_forge.presets._buttons[1].click()
        window._flush_forge_edit()
        assert window._undo.depth_used == before + 1

    def test_repeating_the_same_style_changes_nothing(self, window) -> None:
        """Повторное применение того же — не повод плодить шаги истории."""
        window.table.clearSelection()
        window.style_forge.size_spin.setValue(64)
        window._flush_forge_edit()

        before = window._undo.depth_used
        window._apply_forged_style(window.style_forge.style())
        assert window._undo.depth_used == before


class TestScope:
    def test_selection_gets_tags_not_a_new_style(self, window, qapp) -> None:
        """Стиль общий: перекрасить им одну реплику — перекрасить все."""
        window._select_row(0)
        qapp.processEvents()
        was = window._doc.styles["Default"].fontsize

        window.style_forge.size_spin.setValue(90)
        window._flush_forge_edit()

        assert "\\fs90" in window._doc.events[0].text
        assert window._doc.styles["Default"].fontsize == was

    def test_other_lines_are_left_alone(self, window, qapp) -> None:
        window._select_row(0)
        qapp.processEvents()
        window.style_forge.size_spin.setValue(90)
        window._flush_forge_edit()
        assert window._doc.events[1].text == "Вторая"

    def test_no_selection_edits_the_named_style(self, window) -> None:
        window.table.clearSelection()
        window.style_forge.size_spin.setValue(38)
        window._flush_forge_edit()

        assert window._doc.styles["Default"].fontsize == 38
        assert "\\fs" not in window._doc.events[0].text

    def test_buttons_still_force_the_scope(self, window, qapp) -> None:
        """Кнопка «ко всем» работает и при выделенной реплике."""
        window._select_row(0)
        qapp.processEvents()
        window.style_forge.size_spin.setValue(44)
        window._apply_forged_style(window.style_forge.style(), True)
        assert window._doc.styles["Default"].fontsize == 44

    def test_styling_a_selection_is_one_undo_step(self, window, qapp) -> None:
        window._select_rows([e.eid for e in window._doc.events])
        qapp.processEvents()

        before = window._undo.depth_used
        window.style_forge.size_spin.setValue(70)
        window._flush_forge_edit()
        assert window._undo.depth_used == before + 1

    def test_undo_removes_the_tags(self, window, qapp) -> None:
        window._select_row(0)
        qapp.processEvents()
        window.style_forge.size_spin.setValue(90)
        window._flush_forge_edit()
        window._undo.undo()
        assert window._doc.events[0].text == "Первая"


class TestLabelFormat:
    def speaking(self, window):
        event = window._doc.events[0]
        event.name = "ИВАН"
        return event

    def test_changing_the_format_relabels_at_once(self, window) -> None:
        """Раньше настройка действовала только на будущие назначения."""
        event = self.speaking(window)
        window._settings.set("subtitles.actor_label", "colon")
        window._apply_settings()
        assert event.text.startswith("ИВАН:")

    def test_turning_it_off_removes_the_labels(self, window) -> None:
        event = self.speaking(window)
        window._settings.set("subtitles.actor_label", "colon")
        window._apply_settings()
        window._settings.set("subtitles.actor_label", "off")
        window._apply_settings()
        assert event.text == "Первая"

    def test_relabelling_is_undoable(self, window) -> None:
        event = self.speaking(window)
        window._settings.set("subtitles.actor_label", "colon")
        window._apply_settings()
        window._undo.undo()
        assert event.text == "Первая"

    def test_unchanged_format_costs_nothing(self, window) -> None:
        """Настройки открывают часто; перебирать документ каждый раз незачем."""
        self.speaking(window)
        before = window._undo.depth_used
        window._apply_settings()
        window._apply_settings()
        assert window._undo.depth_used == before
