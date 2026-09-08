"""Тесты команд над дорожками и акторами — прежде всего их отката."""

from __future__ import annotations

import pytest

from sfstudio.core.color import RGBA
from sfstudio.core.commands import (
    AddActor,
    AddTrack,
    AssignActor,
    MoveEventsToLayer,
    RemoveActor,
    RemoveTrack,
    RenameActor,
    SetTrackFlags,
    UpdateActor,
    UpdateTrack,
)
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.tracks import Track, TrackKind
from sfstudio.core.undo import UndoStack


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(0, 1000, "первая", layer=0)
    document.create_event(1000, 2000, "вторая", layer=0)
    return document


@pytest.fixture
def stack(doc: SubtitleDocument) -> UndoStack:
    return UndoStack(doc)


class TestAddTrack:
    def test_adds(self, doc, stack) -> None:
        stack.run(AddTrack("Надписи"))
        assert doc.tracks.layers() == [0, 1]
        assert doc.tracks.by_layer(1).name == "Надписи"

    def test_undo_removes(self, doc, stack) -> None:
        stack.run(AddTrack("Надписи"))
        stack.undo()
        assert doc.tracks.layers() == [0]

    def test_redo_reuses_same_layer(self, doc, stack) -> None:
        """Повтор должен вернуть ту же дорожку, а не завести соседнюю."""
        stack.run(AddTrack("Надписи"))
        stack.undo()
        stack.redo()
        assert doc.tracks.layers() == [0, 1]

    def test_reports_track_change(self, doc) -> None:
        assert AddTrack("x").apply(doc).tracks_changed


class TestRemoveTrack:
    def test_moves_events_down_by_default(self, doc, stack) -> None:
        """Удаление дорожки не должно молча уносить текст."""
        stack.run(AddTrack("Надписи"))
        stack.run(MoveEventsToLayer([doc.events[0].eid], 1))
        stack.run(RemoveTrack(1))
        assert len(doc) == 2
        assert all(event.layer == 0 for event in doc.events)

    def test_undo_restores_track_and_layers(self, doc, stack) -> None:
        stack.run(AddTrack("Надписи"))
        eid = doc.events[0].eid
        stack.run(MoveEventsToLayer([eid], 1))
        stack.run(RemoveTrack(1))
        stack.undo()
        assert doc.tracks.layers() == [0, 1]
        assert doc.by_eid(eid).layer == 1

    def test_can_delete_events_when_asked(self, doc, stack) -> None:
        stack.run(AddTrack("Надписи"))
        stack.run(MoveEventsToLayer([doc.events[0].eid], 1))
        stack.run(RemoveTrack(1, delete_events=True))
        assert len(doc) == 1

    def test_undo_restores_deleted_events(self, doc, stack) -> None:
        stack.run(AddTrack("Надписи"))
        eid = doc.events[0].eid
        stack.run(MoveEventsToLayer([eid], 1))
        stack.run(RemoveTrack(1, delete_events=True))
        stack.undo()
        assert len(doc) == 2
        assert doc.get(eid) is not None

    def test_last_track_is_kept(self, doc, stack) -> None:
        stack.run(RemoveTrack(0))
        assert doc.tracks.layers() == [0]
        assert len(doc) == 2

    def test_events_move_up_when_no_track_below(self, doc, stack) -> None:
        """Удаляем самую нижнюю: переселять некуда вниз, значит вверх."""
        stack.run(AddTrack())
        stack.run(RemoveTrack(0))
        assert all(event.layer == 1 for event in doc.events)


class TestTrackFlags:
    def test_toggle_lock(self, doc, stack) -> None:
        stack.run(SetTrackFlags(0, locked=True))
        assert doc.tracks.by_layer(0).locked
        stack.undo()
        assert not doc.tracks.by_layer(0).locked

    def test_hidden_layers_query(self, doc, stack) -> None:
        stack.run(SetTrackFlags(0, visible=False))
        assert doc.tracks.hidden_layers() == frozenset({0})

    def test_repeated_toggles_coalesce(self, doc, stack) -> None:
        """Щелчки по «глазу» не должны заполнять историю отмены."""
        before = stack.depth_used
        stack.run(SetTrackFlags(0, visible=False))
        stack.run(SetTrackFlags(0, visible=True))
        stack.run(SetTrackFlags(0, visible=False))
        assert stack.depth_used == before + 1

    def test_coalesced_undo_restores_original(self, doc, stack) -> None:
        stack.run(SetTrackFlags(0, visible=False))
        stack.run(SetTrackFlags(0, visible=True))
        stack.undo()
        assert doc.tracks.by_layer(0).visible

    def test_different_tracks_do_not_coalesce(self, doc, stack) -> None:
        stack.run(AddTrack())
        before = stack.depth_used
        stack.run(SetTrackFlags(0, locked=True))
        stack.run(SetTrackFlags(1, locked=True))
        assert stack.depth_used == before + 2


class TestUpdateTrack:
    def test_rename(self, doc, stack) -> None:
        track = doc.tracks.by_layer(0)
        stack.run(UpdateTrack(0, Track(kind=TrackKind.SUBTITLE, layer=0,
                                       name="Диалоги", color=track.color)))
        assert doc.tracks.by_layer(0).name == "Диалоги"

    def test_undo(self, doc, stack) -> None:
        track = doc.tracks.by_layer(0)
        stack.run(UpdateTrack(0, Track(kind=TrackKind.SUBTITLE, layer=0,
                                       name="Диалоги", color=track.color)))
        stack.undo()
        assert doc.tracks.by_layer(0).name == ""


class TestMoveEventsToLayer:
    def test_moves(self, doc, stack) -> None:
        stack.run(AddTrack())
        eids = [event.eid for event in doc.events]
        stack.run(MoveEventsToLayer(eids, 1))
        assert all(event.layer == 1 for event in doc.events)

    def test_undo_restores_each_original_layer(self, doc, stack) -> None:
        """Реплики могли лежать на разных дорожках — вернуть надо каждую свою."""
        stack.run(AddTrack())
        doc.events[1].layer = 1
        eids = [event.eid for event in doc.events]
        stack.run(MoveEventsToLayer(eids, 0))
        stack.undo()
        assert [event.layer for event in doc.events] == [0, 1]


class TestActorCommands:
    def test_add(self, doc, stack) -> None:
        stack.run(AddActor("Анна"))
        assert "Анна" in doc.actors

    def test_undo_add(self, doc, stack) -> None:
        stack.run(AddActor("Анна"))
        stack.undo()
        assert "Анна" not in doc.actors

    def test_redo_keeps_colour(self, doc, stack) -> None:
        """Повтор не должен выдать другой цвет — строки перекрасились бы."""
        stack.run(AddActor("Анна"))
        colour = doc.actors.get("Анна").color
        stack.undo()
        stack.redo()
        assert doc.actors.get("Анна").color == colour

    def test_duplicate_is_a_no_op(self, doc, stack) -> None:
        stack.run(AddActor("Анна"))
        stack.run(AddActor("Анна"))
        assert len(doc.actors) == 1

    def test_remove_keeps_name_on_events(self, doc, stack) -> None:
        """Удаление из реестра — про цвет, а не про то, кто говорит."""
        eid = doc.events[0].eid
        doc.events[0].name = "Анна"
        stack.run(AddActor("Анна"))
        stack.run(RemoveActor("Анна"))
        assert doc.by_eid(eid).name == "Анна"
        assert "Анна" not in doc.actors

    def test_undo_remove(self, doc, stack) -> None:
        stack.run(AddActor("Анна", RGBA.from_hex("#FF8800")))
        stack.run(RemoveActor("Анна"))
        stack.undo()
        assert doc.actors.get("Анна").color.to_hex() == "#FF8800"


class TestRenameActor:
    def test_renames_registry_and_events(self, doc, stack) -> None:
        doc.events[0].name = "Анна"
        doc.events[1].name = "Анна"
        stack.run(AddActor("Анна"))
        stack.run(RenameActor("Анна", "Анна Петровна"))
        assert "Анна Петровна" in doc.actors
        assert [event.name for event in doc.events] == ["Анна Петровна"] * 2

    def test_undo_restores_both(self, doc, stack) -> None:
        doc.events[0].name = "Анна"
        stack.run(AddActor("Анна"))
        stack.run(RenameActor("Анна", "Анна Петровна"))
        stack.undo()
        assert "Анна" in doc.actors
        assert doc.events[0].name == "Анна"

    def test_other_actors_are_untouched(self, doc, stack) -> None:
        doc.events[0].name = "Анна"
        doc.events[1].name = "Борис"
        stack.run(AddActor("Анна"))
        stack.run(AddActor("Борис"))
        stack.run(RenameActor("Анна", "Анна К."))
        assert doc.events[1].name == "Борис"

    def test_collision_is_refused(self, doc, stack) -> None:
        stack.run(AddActor("Анна"))
        stack.run(AddActor("Борис"))
        stack.run(RenameActor("Анна", "Борис"))
        assert doc.actors.names() == ["Анна", "Борис"]


class TestAssignActor:
    def test_assigns_to_all(self, doc, stack) -> None:
        stack.run(AssignActor([e.eid for e in doc.events], "Анна"))
        assert all(event.name == "Анна" for event in doc.events)

    def test_empty_name_clears(self, doc, stack) -> None:
        stack.run(AssignActor([e.eid for e in doc.events], "Анна"))
        stack.run(AssignActor([e.eid for e in doc.events], ""))
        assert all(event.name == "" for event in doc.events)

    def test_undo_restores_previous_names(self, doc, stack) -> None:
        doc.events[0].name = "Борис"
        stack.run(AssignActor([e.eid for e in doc.events], "Анна"))
        stack.undo()
        assert [event.name for event in doc.events] == ["Борис", ""]


class TestUpdateActor:
    def test_colour_change(self, doc, stack) -> None:
        stack.run(AddActor("Анна"))
        stack.run(UpdateActor("Анна", color=RGBA.from_hex("#123456")))
        assert doc.actors.get("Анна").color.to_hex() == "#123456"

    def test_undo(self, doc, stack) -> None:
        stack.run(AddActor("Анна", RGBA.from_hex("#FF8800")))
        stack.run(UpdateActor("Анна", color=RGBA.from_hex("#123456")))
        stack.undo()
        assert doc.actors.get("Анна").color.to_hex() == "#FF8800"

    def test_dragging_colour_is_one_undo_step(self, doc, stack) -> None:
        stack.run(AddActor("Анна", RGBA.from_hex("#FF8800")))
        before = stack.depth_used
        for shade in ("#110000", "#220000", "#330000"):
            stack.run(UpdateActor("Анна", color=RGBA.from_hex(shade)))
        assert stack.depth_used == before + 1
        stack.undo()
        assert doc.actors.get("Анна").color.to_hex() == "#FF8800"
