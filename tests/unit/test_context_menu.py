"""Контекстное меню над репликами: состав пунктов и их действие.

Меню строится отдельным методом (``context_menu``), а не внутри
``contextMenuEvent``, именно ради этих тестов: показ меню модален, и
проверить его состав, открыв окно, невозможно.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.event_menu import actor_submenu, plural_events
from sfstudio.ui.timeline import TimelineWidget


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    for event in list(document.events):
        document.remove_event(event.eid)
    for i in range(3):
        document.create_event(i * 5000, i * 5000 + 3000, f"Реплика {i}")
    document.actors.add("Иван")
    document.actors.add("Пётр")
    return document


@pytest.fixture
def timeline(qapp, doc) -> TimelineWidget:
    widget = TimelineWidget(doc, UndoStack(doc))
    widget.resize(1200, 400)
    widget.fit_all()
    return widget


def titles(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def item(menu: QMenu, prefix: str):
    return next(a for a in menu.actions() if a.text().startswith(prefix))


def point_on(timeline: TimelineWidget, event) -> QPoint:
    row = next(r for r in timeline.rows() if r.track.is_subtitle)
    return QPoint(
        int(timeline.ms_to_x(event.start + 500)), int(row.top + row.height / 2)
    )


class TestPluralForm:
    def test_counts_are_declined(self) -> None:
        assert plural_events(1) == "реплику"
        assert plural_events(2) == "2 реплики"
        assert plural_events(5) == "5 реплик"

    def test_teens_are_not_mistaken_for_units(self) -> None:
        """11 и 12 склоняются не как 1 и 2 — на них ломается наивная формула."""
        assert plural_events(11) == "11 реплик"
        assert plural_events(12) == "12 реплик"
        assert plural_events(21) == "21 реплику"


class TestTimelineMenu:
    def test_creation_and_removal_are_offered(self, timeline, doc) -> None:
        menu = timeline.context_menu(point_on(timeline, doc.events[1]))
        assert any(t.startswith("Новая реплика") for t in titles(menu))
        assert any(t.startswith("Удалить реплику") for t in titles(menu))

    def test_right_click_selects_what_the_menu_talks_about(self, timeline, doc) -> None:
        """Иначе пункт про семь реплик унесёт не то, на что человек показывает."""
        timeline.context_menu(point_on(timeline, doc.events[2]))
        assert timeline.selected_eids == [doc.events[2].eid]

    def test_existing_selection_survives_a_click_inside_it(self, timeline, doc) -> None:
        eids = [doc.events[0].eid, doc.events[1].eid]
        timeline.set_selection(eids)
        menu = timeline.context_menu(point_on(timeline, doc.events[0]))
        assert timeline.selected_eids == eids
        assert any(t.startswith("Удалить 2 реплики") for t in titles(menu))

    def test_delete_removes_and_undo_restores(self, timeline, doc) -> None:
        menu = timeline.context_menu(point_on(timeline, doc.events[1]))
        item(menu, "Удалить реплику").trigger()
        assert len(doc.events) == 2
        timeline._undo.undo()
        assert len(doc.events) == 3

    def test_duplicate_adds_a_copy(self, timeline, doc) -> None:
        menu = timeline.context_menu(point_on(timeline, doc.events[0]))
        item(menu, "Дублировать").trigger()
        assert len(doc.events) == 4

    def test_paste_is_disabled_without_clipboard_text(self, timeline, doc, qapp) -> None:
        """Пункт, который ничего не вставит, должен быть виден отключённым."""
        qapp.clipboard().clear()
        menu = timeline.context_menu(point_on(timeline, doc.events[0]))
        assert not item(menu, "Вставить из буфера").isEnabled()

    def test_no_dead_submenu_with_a_single_track(self, timeline, doc) -> None:
        """Пока дорожка одна, переносить реплику некуда."""
        menu = timeline.context_menu(point_on(timeline, doc.events[0]))
        assert not any(t.startswith("Перенести") for t in titles(menu))


class TestLockedTracks:
    """Замок на дорожке должен держать и меню, а не только мышь."""

    def lock(self, timeline, layer: int = 0) -> None:
        from sfstudio.core.commands import SetTrackFlags

        timeline._undo.run(SetTrackFlags(layer, locked=True))

    def test_locked_events_are_not_offered_for_removal(self, timeline, doc) -> None:
        eids = [e.eid for e in doc.events]
        timeline.set_selection(eids)
        self.lock(timeline)
        menu = timeline.context_menu(QPoint(300, 5))
        assert not any(t.startswith("Удалить реплик") for t in titles(menu))
        assert not any(t.startswith("Дублировать") for t in titles(menu))

    def test_locked_events_keep_their_actor_menu_closed(self, timeline, doc) -> None:
        """Назначение говорящего — тоже правка документа."""
        timeline.set_selection([e.eid for e in doc.events])
        self.lock(timeline)
        menu = timeline.context_menu(QPoint(300, 5))
        assert "Акторы" not in titles(menu)

    def test_unlocked_tracks_still_work(self, timeline, doc) -> None:
        timeline.set_selection([doc.events[0].eid])
        menu = timeline.context_menu(QPoint(300, 5))
        assert any(t.startswith("Удалить") for t in titles(menu))


class TestActorSubmenu:
    def test_project_actors_are_listed(self, timeline, doc) -> None:
        menu = actor_submenu(timeline, doc, [doc.events[0].eid], timeline._undo.run)
        assert "Иван" in titles(menu)
        assert "Пётр" in titles(menu)

    def test_registered_actors_show_their_colour(self, timeline, doc) -> None:
        menu = actor_submenu(timeline, doc, [doc.events[0].eid], timeline._undo.run)
        assert not item(menu, "Иван").icon().isNull()

    def test_names_only_found_in_events_are_offered_too(self, timeline, doc) -> None:
        """Файл со стороны: говорящие есть, реестра нет — меню не пустое."""
        doc.events[2].name = "Гость"
        menu = actor_submenu(timeline, doc, [doc.events[0].eid], timeline._undo.run)
        assert "Гость" in titles(menu)
        # Цвета у незаведённого нет, и меню не должно его выдумывать.
        assert item(menu, "Гость").icon().isNull()

    def test_choosing_assigns_the_whole_selection(self, timeline, doc) -> None:
        eids = [doc.events[0].eid, doc.events[1].eid]
        menu = actor_submenu(timeline, doc, eids, timeline._undo.run)
        item(menu, "Иван").trigger()
        assert [e.name for e in doc.events] == ["Иван", "Иван", ""]

    def test_assignment_gives_the_events_a_colour(self, timeline, doc) -> None:
        menu = actor_submenu(timeline, doc, [doc.events[0].eid], timeline._undo.run)
        assert doc.actor_color(doc.events[0]) is None
        item(menu, "Иван").trigger()
        assert doc.actor_color(doc.events[0]) == doc.actors.color_of("Иван")

    def test_current_actor_is_ticked(self, timeline, doc) -> None:
        doc.events[0].name = "Пётр"
        menu = actor_submenu(timeline, doc, [doc.events[0].eid], timeline._undo.run)
        assert item(menu, "Пётр").isChecked()
        assert not item(menu, "Иван").isChecked()

    def test_mixed_selection_ticks_nothing(self, timeline, doc) -> None:
        """Отметка при разнобое соврала бы: говорящий у реплик разный."""
        doc.events[0].name = "Иван"
        doc.events[1].name = "Пётр"
        eids = [doc.events[0].eid, doc.events[1].eid]
        menu = actor_submenu(timeline, doc, eids, timeline._undo.run)
        assert not any(a.isChecked() for a in menu.actions() if a.isCheckable())

    def test_clearing_is_offered_only_when_there_is_something_to_clear(
        self, timeline, doc
    ) -> None:
        menu = actor_submenu(timeline, doc, [doc.events[0].eid], timeline._undo.run)
        assert not item(menu, "Без говорящего").isEnabled()

        doc.events[0].name = "Иван"
        menu = actor_submenu(timeline, doc, [doc.events[0].eid], timeline._undo.run)
        clear = item(menu, "Без говорящего")
        assert clear.isEnabled()
        clear.trigger()
        assert doc.events[0].name == ""

    def test_assignment_is_one_undo_step(self, timeline, doc) -> None:
        eids = [e.eid for e in doc.events]
        menu = actor_submenu(timeline, doc, eids, timeline._undo.run)
        item(menu, "Иван").trigger()
        timeline._undo.undo()
        assert [e.name for e in doc.events] == ["", "", ""]

    def test_menu_without_targets_is_disabled(self, timeline, doc) -> None:
        assert not actor_submenu(timeline, doc, [], timeline._undo.run).isEnabled()

    def test_custom_command_is_used_when_given(self, timeline, doc) -> None:
        """Главное окно подставляет свою сборку — с меткой в тексте."""
        seen: list[tuple[list[int], str]] = []

        def build(eids, name):
            seen.append((list(eids), name))
            from sfstudio.core.commands import AssignActor

            return AssignActor(eids, name)

        menu = actor_submenu(
            timeline, doc, [doc.events[0].eid], timeline._undo.run,
            actor_command=build,
        )
        item(menu, "Иван").trigger()
        assert seen == [([doc.events[0].eid], "Иван")]
