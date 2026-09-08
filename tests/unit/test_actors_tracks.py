"""Тесты реестра акторов и набора дорожек, включая сохранение в ASS."""

from __future__ import annotations

import pytest

from sfstudio.core.actors import ActorRegistry
from sfstudio.core.color import RGBA
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.tracks import TrackKind, TrackSet
from sfstudio.io.formats.ass import read_ass, write_ass


class TestHexColor:
    def test_round_trip(self) -> None:
        assert RGBA.from_hex("#3F8AC2").to_hex() == "#3F8AC2"

    def test_short_form(self) -> None:
        assert RGBA.from_hex("#F80") == RGBA(0xFF, 0x88, 0x00, 255)

    def test_without_hash(self) -> None:
        assert RGBA.from_hex("3F8AC2") == RGBA(0x3F, 0x8A, 0xC2, 255)

    def test_alpha_is_not_inverted(self) -> None:
        """Это веб-цвет, а не поле ASS: ``FF`` — непрозрачный."""
        assert RGBA.from_hex("#00000000").a == 0
        assert RGBA.from_hex("#000000FF").a == 255

    def test_garbage_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="RRGGBB"):
            RGBA.from_hex("не цвет")

    def test_contrasting_text(self) -> None:
        assert RGBA.from_hex("#FFFFFF").contrasting_text() == RGBA(0, 0, 0, 255)
        assert RGBA.from_hex("#101010").contrasting_text() == RGBA(255, 255, 255, 255)


class TestActorRegistry:
    def test_add_and_lookup(self) -> None:
        registry = ActorRegistry()
        registry.add("Анна")
        assert "Анна" in registry
        assert registry.get("Анна").name == "Анна"

    def test_colors_differ_for_new_actors(self) -> None:
        registry = ActorRegistry()
        for name in ("Анна", "Борис", "Виктор", "Галина"):
            registry.add(name)
        colors = {actor.color for actor in registry}
        assert len(colors) == 4

    def test_duplicate_is_refused(self) -> None:
        registry = ActorRegistry()
        registry.add("Анна")
        with pytest.raises(ValueError, match="уже есть"):
            registry.add("Анна")

    def test_empty_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="пустым"):
            ActorRegistry().add("   ")

    def test_unknown_actor_has_no_color(self) -> None:
        """Отличать «нет в реестре» от «есть без цвета» обязательно."""
        assert ActorRegistry().color_of("Кто-то") is None

    def test_rename_keeps_position(self) -> None:
        registry = ActorRegistry()
        for name in ("Анна", "Борис", "Виктор"):
            registry.add(name)
        registry.rename("Борис", "Борис Петрович")
        assert registry.names() == ["Анна", "Борис Петрович", "Виктор"]

    def test_rename_keeps_color(self) -> None:
        registry = ActorRegistry()
        registry.add("Анна")
        before = registry.get("Анна").color
        registry.rename("Анна", "Анна К.")
        assert registry.get("Анна К.").color == before

    def test_rename_onto_existing_is_refused(self) -> None:
        registry = ActorRegistry()
        registry.add("Анна")
        registry.add("Борис")
        with pytest.raises(ValueError, match="уже есть"):
            registry.rename("Анна", "Борис")

    def test_remove(self) -> None:
        registry = ActorRegistry()
        registry.add("Анна")
        assert registry.remove("Анна") is not None
        assert "Анна" not in registry


class TestActorSerialisation:
    def test_round_trip(self) -> None:
        registry = ActorRegistry()
        registry.add("Анна", RGBA.from_hex("#FF8800"))
        registry.add("Борис", note="говорит медленно")
        back = ActorRegistry.from_json(registry.to_json())
        assert back.names() == ["Анна", "Борис"]
        assert back.get("Анна").color.to_hex() == "#FF8800"
        assert back.get("Борис").note == "говорит медленно"

    def test_json_is_single_line(self) -> None:
        """Значение в [Script Info] не может содержать перевод строки."""
        registry = ActorRegistry()
        registry.add("Анна")
        assert "\n" not in registry.to_json()

    def test_names_with_separators_survive(self) -> None:
        """Ради этого и выбран JSON: самодельный разделитель тут сломался бы."""
        registry = ActorRegistry()
        registry.add("Иванов, А.: голос за кадром; дубль")
        back = ActorRegistry.from_json(registry.to_json())
        assert back.names() == ["Иванов, А.: голос за кадром; дубль"]

    def test_garbage_gives_empty_registry(self) -> None:
        assert len(ActorRegistry.from_json("не json")) == 0
        assert len(ActorRegistry.from_json("")) == 0
        assert len(ActorRegistry.from_json("{}")) == 0

    def test_partial_records_are_skipped_not_fatal(self) -> None:
        text = '[{"name":"Анна","color":"#FF8800"},{"нет":"имени"},{"name":"Борис"}]'
        registry = ActorRegistry.from_json(text)
        assert registry.names() == ["Анна", "Борис"]

    def test_bad_color_falls_back(self) -> None:
        registry = ActorRegistry.from_json('[{"name":"Анна","color":"зелёный"}]')
        assert registry.get("Анна") is not None


class TestTrackSet:
    def test_default_has_one_layer(self) -> None:
        tracks = TrackSet.default()
        assert tracks.layers() == [0]

    def test_add_takes_next_layer(self) -> None:
        tracks = TrackSet.default()
        assert tracks.add().layer == 1
        assert tracks.add().layer == 2

    def test_ensure_layers_from_events(self) -> None:
        tracks = TrackSet(subtitles=[])
        tracks.ensure_layers([0, 3, 3, 7])
        assert tracks.layers() == [0, 3, 7]

    def test_gaps_are_not_filled(self) -> None:
        """Файл со слоями 0 и 5 не должен порождать четыре пустые дорожки."""
        tracks = TrackSet(subtitles=[])
        tracks.ensure_layers([0, 5])
        assert tracks.layers() == [0, 5]

    def test_display_order_puts_media_below(self) -> None:
        tracks = TrackSet.default()
        tracks.add("Надписи")
        rows = tracks.display_order()
        kinds = [track.kind for track in rows]
        assert kinds[-2:] == [TrackKind.VIDEO, TrackKind.AUDIO]

    def test_display_order_stacks_higher_layer_on_top(self) -> None:
        tracks = TrackSet.default()
        tracks.add("Надписи")
        rows = tracks.display_order(with_media=False)
        assert [track.layer for track in rows] == [1, 0]

    def test_last_track_cannot_be_removed(self) -> None:
        """Документу нужна хотя бы одна дорожка, иначе реплику некуда класть."""
        tracks = TrackSet.default()
        assert tracks.remove(0) is None
        assert tracks.layers() == [0]

    def test_remove_works_when_several(self) -> None:
        tracks = TrackSet.default()
        tracks.add()
        assert tracks.remove(1) is not None
        assert tracks.layers() == [0]

    def test_duplicate_layer_is_refused(self) -> None:
        tracks = TrackSet.default()
        with pytest.raises(ValueError, match="уже есть"):
            tracks.add(layer=0)

    def test_display_name_falls_back_to_layer(self) -> None:
        tracks = TrackSet.default()
        assert tracks.by_layer(0).display_name() == "Субтитры 0"
        tracks.by_layer(0).name = "Диалоги"
        assert tracks.by_layer(0).display_name() == "Диалоги"

    def test_serialisation_round_trip(self) -> None:
        tracks = TrackSet.default()
        track = tracks.add("Надписи")
        track.locked = True
        track.visible = False
        back = TrackSet.from_json(tracks.to_json())
        assert back.layers() == [0, 1]
        restored = back.by_layer(1)
        assert restored.name == "Надписи"
        assert restored.locked and not restored.visible

    def test_garbage_gives_empty_set(self) -> None:
        assert TrackSet.from_json("не json").subtitles == []


class TestDocumentIntegration:
    def test_sync_creates_tracks_for_used_layers(self) -> None:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, "низ", layer=0)
        doc.create_event(0, 1000, "верх", layer=4)
        doc.sync_tracks()
        assert doc.tracks.layers() == [0, 4]

    def test_events_on_layer(self) -> None:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, "низ", layer=0)
        doc.create_event(0, 1000, "верх", layer=1)
        assert [e.text for e in doc.events_on_layer(1)] == ["верх"]

    def test_actors_in_use_counts_unregistered(self) -> None:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, "а", name="Анна")
        doc.create_event(0, 1000, "б", name="Анна")
        doc.create_event(0, 1000, "в", name="Борис")
        assert doc.actors_in_use() == {"Анна": 2, "Борис": 1}

    def test_actor_color_only_for_registered(self) -> None:
        doc = SubtitleDocument.blank()
        event = doc.create_event(0, 1000, "а", name="Анна")
        assert doc.actor_color(event) is None
        doc.actors.add("Анна", RGBA.from_hex("#FF8800"))
        assert doc.actor_color(event).to_hex() == "#FF8800"


class TestAssRoundTrip:
    def _document(self) -> SubtitleDocument:
        doc = SubtitleDocument.blank()
        doc.actors.add("Анна", RGBA.from_hex("#FF8800"))
        doc.actors.add("Борис", RGBA.from_hex("#3F8AC2"))
        track = doc.tracks.add("Надписи")
        track.locked = True
        doc.create_event(0, 1000, "Реплика Анны", name="Анна", layer=0)
        doc.create_event(1000, 2000, "Надпись", layer=track.layer)
        return doc

    def test_actors_survive(self) -> None:
        back = read_ass(write_ass(self._document()))
        assert back.actors.names() == ["Анна", "Борис"]
        assert back.actors.get("Анна").color.to_hex() == "#FF8800"

    def test_tracks_survive(self) -> None:
        back = read_ass(write_ass(self._document()))
        assert back.tracks.layers() == [0, 1]
        assert back.tracks.by_layer(1).name == "Надписи"
        assert back.tracks.by_layer(1).locked

    def test_event_layer_survives(self) -> None:
        back = read_ass(write_ass(self._document()))
        assert sorted(e.layer for e in back.events) == [0, 1]

    def test_stable_across_two_cycles(self) -> None:
        once = write_ass(self._document())
        assert write_ass(read_ass(once)) == once

    def test_keys_are_not_duplicated(self) -> None:
        """Ключ должен уйти из extra при чтении, иначе запишется дважды."""
        text = write_ass(read_ass(write_ass(self._document())))
        assert text.count("SFStudio Actors:") == 1
        assert text.count("SFStudio Tracks:") == 1

    def test_plain_file_stays_plain(self) -> None:
        """Обычные субтитры не должны обрастать нашими полями."""
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, "Обычная реплика")
        text = write_ass(doc)
        assert "SFStudio" not in text

    def test_foreign_file_gets_tracks_from_events(self) -> None:
        """Файл чужой программы: описания дорожек нет, слои есть."""
        doc = SubtitleDocument.blank()
        doc.create_event(0, 1000, "низ", layer=0)
        doc.create_event(0, 1000, "верх", layer=2)
        back = read_ass(write_ass(doc))
        assert back.tracks.layers() == [0, 2]
