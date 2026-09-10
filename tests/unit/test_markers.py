"""Маркеры: модель, хранение в файле и команды отмены."""

from __future__ import annotations

from sfstudio.core.commands import (
    AddMarker,
    ClearMarkers,
    MoveMarker,
    RemoveMarker,
    UpdateMarker,
)
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.markers import (
    DEFAULT_COLOR,
    MARKER_COLORS,
    Marker,
    MarkerList,
    color_title,
    color_value,
    is_known_color,
)
from sfstudio.core.undo import UndoStack
from sfstudio.io.formats.ass import read_ass, write_ass


def sample() -> MarkerList:
    return MarkerList([
        Marker(9000, name="третий"),
        Marker(1000, name="первый"),
        Marker(5000, 2000, name="второй"),
    ])


class TestMarker:
    def test_point_marker_has_no_length(self) -> None:
        assert Marker(1000).end == 1000

    def test_span_marker_ends_later(self) -> None:
        assert Marker(1000, 500).end == 1500

    def test_covers_its_own_moment(self) -> None:
        assert Marker(1000).covers(1000)

    def test_covers_the_whole_span(self) -> None:
        marker = Marker(1000, 500)
        assert marker.covers(1200)
        assert not marker.covers(1600)

    def test_title_falls_back_to_the_keyword(self) -> None:
        """Безымянная отметка в списке должна называться хоть как-то."""
        assert Marker(0, keyword="вывеска").title() == "вывеска"

    def test_title_falls_back_to_the_first_line_of_the_note(self) -> None:
        assert Marker(0, note="спросить\nпотом").title() == "спросить"

    def test_nameless_marker_says_so(self) -> None:
        assert Marker(0).title() == "Без имени"

    def test_move_does_not_go_negative(self) -> None:
        assert Marker(1000).moved_to(-500).time == 0


class TestColors:
    def test_palette_keys_are_unique(self) -> None:
        keys = [key for key, _title, _value in MARKER_COLORS]
        assert len(keys) == len(set(keys))

    def test_default_is_in_the_palette(self) -> None:
        assert is_known_color(DEFAULT_COLOR)

    def test_unknown_colour_still_draws(self) -> None:
        """Цвет из будущей версии не должен ронять отрисовку."""
        assert color_value("сиреневый в крапинку") == color_value(DEFAULT_COLOR)

    def test_title_is_shown_for_a_known_colour(self) -> None:
        assert color_title("red") == "Красный"


class TestList:
    def test_order_is_by_time(self) -> None:
        assert [m.name for m in sample()] == ["первый", "второй", "третий"]

    def test_added_marker_lands_in_order(self) -> None:
        markers = sample()
        markers.add(Marker(3000, name="новый"))
        assert [m.name for m in markers][1] == "новый"

    def test_two_markers_at_the_same_time_both_survive(self) -> None:
        """Конец одной задачи и начало другой попадают в один кадр."""
        markers = MarkerList([Marker(1000, name="а"), Marker(1000, name="б")])
        assert len(markers) == 2

    def test_remove_takes_exactly_one(self) -> None:
        markers = MarkerList([Marker(1000, name="а"), Marker(1000, name="б")])
        markers.remove(Marker(1000, name="а"))
        assert [m.name for m in markers] == ["б"]

    def test_removing_a_stranger_says_no(self) -> None:
        assert not sample().remove(Marker(777))

    def test_replace_keeps_the_order(self) -> None:
        markers = sample()
        markers.replace(Marker(1000, name="первый"), Marker(20_000, name="первый"))
        assert [m.name for m in markers][-1] == "первый"

    def test_at_finds_the_point(self) -> None:
        assert sample().at(1000).name == "первый"

    def test_at_respects_the_radius(self) -> None:
        assert sample().at(1100) is None
        assert sample().at(1100, radius=200).name == "первый"

    def test_at_prefers_the_closer_one(self) -> None:
        markers = MarkerList([Marker(1000, name="а"), Marker(1400, name="б")])
        assert markers.at(1350, radius=400).name == "б"

    def test_in_range_includes_overlapping_spans(self) -> None:
        """Маркер, начавшийся до участка, но идущий в него, — виден."""
        markers = MarkerList([Marker(0, 5000, name="длинный")])
        assert markers.in_range(3000, 4000)

    def test_after_skips_the_current_moment(self) -> None:
        assert sample().after(1000).name == "второй"

    def test_before_skips_the_current_moment(self) -> None:
        assert sample().before(5000).name == "первый"

    def test_no_marker_ahead(self) -> None:
        assert sample().after(100_000) is None

    def test_no_marker_behind(self) -> None:
        assert sample().before(0) is None

    def test_keywords_are_unique_and_sorted(self) -> None:
        markers = MarkerList([
            Marker(0, keyword="вывеска"),
            Marker(1, keyword="песня"),
            Marker(2, keyword="вывеска"),
            Marker(3),
        ])
        assert markers.keywords() == ["вывеска", "песня"]

    def test_items_is_a_copy(self) -> None:
        """Иначе правка в обход методов рушит порядок списка."""
        markers = sample()
        markers.items.clear()
        assert len(markers) == 3


class TestStorage:
    def test_round_trip_keeps_every_field(self) -> None:
        marker = Marker(1500, 500, "Смена сцены", "спросить", "вывеска", "red")
        back = MarkerList.from_json(MarkerList([marker]).to_json())
        assert back[0] == marker

    def test_defaults_are_not_written(self) -> None:
        """Файл не должен пухнуть от пустых полей."""
        assert MarkerList([Marker(1000)]).to_json() == '[{"t":1000}]'

    def test_damaged_json_gives_an_empty_list(self) -> None:
        assert len(MarkerList.from_json("{не json")) == 0

    def test_entry_without_time_is_skipped(self) -> None:
        assert len(MarkerList.from_json('[{"n":"без времени"}]')) == 0

    def test_unknown_colour_becomes_the_default(self) -> None:
        """Отметка важнее оттенка: терять её из-за цвета несоразмерно."""
        markers = MarkerList.from_json('[{"t":0,"p":"неоновый"}]')
        assert markers[0].color == DEFAULT_COLOR

    def test_negative_time_is_clamped(self) -> None:
        assert MarkerList.from_json('[{"t":-500}]')[0].time == 0


class TestInsideAss:
    def document(self) -> SubtitleDocument:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 2000, "Реплика")
        return doc

    def test_markers_survive_save_and_open(self) -> None:
        doc = self.document()
        doc.markers.add(Marker(1500, 500, "Смена сцены", "спросить", "вывеска", "red"))
        back = read_ass(write_ass(doc))
        assert back.markers[0].name == "Смена сцены"
        assert back.markers[0].color == "red"

    def test_file_without_markers_gets_no_key(self) -> None:
        """Обычные субтитры не должны обрастать нашими полями."""
        assert "SFStudio Markers" not in write_ass(self.document())

    def test_key_is_not_written_twice(self) -> None:
        doc = self.document()
        doc.markers.add(Marker(1000, name="раз"))
        text = write_ass(read_ass(write_ass(doc)))
        assert text.count("SFStudio Markers") == 1

    def test_foreign_file_opens_without_markers(self) -> None:
        doc = read_ass(write_ass(self.document()))
        assert len(doc.markers) == 0

    def test_damaged_key_does_not_block_opening(self) -> None:
        """Файл могли поправить руками или обрезать чужой программой."""
        doc = self.document()
        doc.markers.add(Marker(1000, name="раз"))
        broken = write_ass(doc).replace(
            doc.markers.to_json(), "вот тут был json, да весь вышел"
        )

        back = read_ass(broken)
        assert len(back.events) == 1
        assert len(back.markers) == 0

    def test_markers_survive_the_project_file(self, tmp_path) -> None:
        """Проект хранит субтитры как ASS — маркеры едут вместе с ними."""
        from sfstudio.core.project import Project
        from sfstudio.io.project_file import load_project, save_project

        doc = self.document()
        doc.markers.add(Marker(4000, name="Смена сцены", color="green"))
        path = save_project(Project(name="проба", document=doc), tmp_path / "п.sfs")

        back = load_project(path)
        assert [m.name for m in back.document.markers] == ["Смена сцены"]


class TestCommands:
    def prepared(self) -> tuple[SubtitleDocument, UndoStack]:
        doc = SubtitleDocument.blank()
        doc.create_event(0, 2000, "Реплика")
        return doc, UndoStack(doc)

    def test_add_and_undo(self) -> None:
        doc, undo = self.prepared()
        undo.run(AddMarker(Marker(1000, name="раз")))
        assert len(doc.markers) == 1
        undo.undo()
        assert len(doc.markers) == 0

    def test_remove_and_undo(self) -> None:
        doc, undo = self.prepared()
        marker = Marker(1000, name="раз")
        doc.markers.add(marker)
        undo.run(RemoveMarker(marker))
        assert len(doc.markers) == 0
        undo.undo()
        assert doc.markers[0] == marker

    def test_removing_a_stranger_changes_nothing(self) -> None:
        doc, undo = self.prepared()
        undo.run(RemoveMarker(Marker(999)))
        undo.undo()
        assert len(doc.markers) == 0

    def test_update_and_undo(self) -> None:
        doc, undo = self.prepared()
        before = Marker(1000, name="раз")
        doc.markers.add(before)
        undo.run(UpdateMarker(before, Marker(1000, name="два", color="green")))
        assert doc.markers[0].name == "два"
        undo.undo()
        assert doc.markers[0].name == "раз"

    def test_move_keeps_the_list_sorted(self) -> None:
        doc, undo = self.prepared()
        first, second = Marker(1000, name="а"), Marker(5000, name="б")
        doc.markers.add(first)
        doc.markers.add(second)
        undo.run(MoveMarker(first, first.moved_to(9000)))
        assert [m.name for m in doc.markers] == ["б", "а"]

    def test_a_drag_is_one_undo_step(self) -> None:
        """Одно движение мышью — не сорок нажатий «Отменить»."""
        doc, undo = self.prepared()
        marker = Marker(1000, name="а")
        doc.markers.add(marker)

        before = undo.depth_used
        current = marker
        for ms in (1100, 1200, 1300, 1400):
            moved = current.moved_to(ms)
            undo.run(MoveMarker(current, moved))
            current = moved

        assert undo.depth_used == before + 1
        undo.undo()
        assert doc.markers[0].time == 1000

    def test_separate_drags_do_not_merge(self) -> None:
        """Два разных жеста — два шага отмены.

        Слияние здесь склеило бы начало первого переноса с концом второго:
        одно «Отменить» вернуло бы оба маркера, а второе — ничего.
        """
        doc, undo = self.prepared()
        first, second = Marker(1000, name="а"), Marker(8000, name="б")
        doc.markers.add(first)
        doc.markers.add(second)

        before = undo.depth_used
        undo.run(MoveMarker(first, first.moved_to(2000)))
        undo.run(MoveMarker(second, second.moved_to(9000)))
        assert undo.depth_used == before + 2

        # Первое «Отменить» возвращает только второй маркер.
        undo.undo()
        assert doc.markers.at(8000) is not None
        assert doc.markers.at(2000) is not None
        assert doc.markers.at(1000) is None

    def test_clear_is_one_step(self) -> None:
        doc, undo = self.prepared()
        for ms in (1000, 2000, 3000):
            doc.markers.add(Marker(ms))

        before = undo.depth_used
        undo.run(ClearMarkers())
        assert len(doc.markers) == 0
        assert undo.depth_used == before + 1
        undo.undo()
        assert len(doc.markers) == 3

    def test_clearing_nothing_is_harmless(self) -> None:
        """Команда на пустом документе не должна ломать отмену.

        В историю она всё же попадает: стек кладёт туда всё, что запустили.
        Не доводить до этого — забота вызывающего кода, и он это делает.
        """
        doc, undo = self.prepared()
        undo.run(ClearMarkers())
        undo.undo()
        assert len(doc.markers) == 0

    def test_markers_mark_the_document_dirty(self) -> None:
        """Иначе программа закроется, не спросив о несохранённых отметках."""
        doc, undo = self.prepared()
        undo.mark_clean()
        undo.run(AddMarker(Marker(1000)))
        assert not undo.is_clean
