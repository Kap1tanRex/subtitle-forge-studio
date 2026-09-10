"""Маркеры в интерфейсе: полоса на линейке, окно маркера, список.

Окно маркера модальное, поэтому в тестах оно не открывается: вместо
``edit_marker`` подставляется заглушка там, где проверяется не оно само.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.markers import Marker
from sfstudio.core.undo import UndoStack
from sfstudio.ui.marker_dialog import ColorRow, MarkerDialog
from sfstudio.ui.markers_dialog import MarkersDialog
from sfstudio.ui.timeline import (
    HEADER_W,
    MARKER_LANE_H,
    RULER_H,
    TIME_LANE_H,
    TimelineWidget,
)

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(0, 2000, "Реплика")
    return document


@pytest.fixture
def timeline(qapp: QApplication, doc: SubtitleDocument) -> TimelineWidget:
    widget = TimelineWidget(doc, UndoStack(doc))
    widget.resize(900, 320)
    return widget


def press(widget: TimelineWidget, x: float, y: float) -> None:
    """Щелчок левой кнопкой в точке виджета."""
    event = QMouseEvent(
        QMouseEvent.MouseButtonPress,
        QPointF(x, y),
        QPointF(x, y),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    widget.mousePressEvent(event)


def lane_y() -> float:
    """Середина полосы маркеров."""
    return TIME_LANE_H + MARKER_LANE_H / 2


class TestLane:
    def test_marker_lane_sits_under_the_time_scale(self) -> None:
        assert TIME_LANE_H < RULER_H
        assert RULER_H - TIME_LANE_H == MARKER_LANE_H

    def test_button_is_inside_the_lane(self, timeline: TimelineWidget) -> None:
        """Иначе кнопка накрывает подписи времени."""
        rect = timeline._marker_button_rect()
        assert rect.top() >= TIME_LANE_H
        assert rect.bottom() <= RULER_H

    def test_button_does_not_overlap_the_track_buttons(
        self, timeline: TimelineWidget
    ) -> None:
        assert not timeline._marker_button_rect().intersects(
            timeline._add_button_rect()
        )

    def test_fit_all_reaches_a_marker_past_the_last_event(
        self, timeline: TimelineWidget, doc: SubtitleDocument
    ) -> None:
        """Иначе отметку за концом файла можно найти только наугад."""
        doc.markers.add(Marker(90_000, name="далеко"))
        timeline.fit_all()
        assert timeline.view_end_ms >= 90_000

    def test_painting_with_markers_does_not_raise(
        self, timeline: TimelineWidget, doc: SubtitleDocument
    ) -> None:
        doc.markers.add(Marker(1000, name="точка"))
        doc.markers.add(Marker(5000, 3000, name="отрезок", color="green"))
        assert not timeline.grab().isNull()


class TestHitting:
    def placed(self, timeline: TimelineWidget, doc: SubtitleDocument) -> Marker:
        marker = Marker(5000, name="проба")
        doc.markers.add(marker)
        return marker

    def test_flag_is_hit(self, timeline, doc) -> None:
        marker = self.placed(timeline, doc)
        assert timeline._marker_at(timeline.ms_to_x(marker.time)) == marker

    def test_far_away_is_a_miss(self, timeline, doc) -> None:
        marker = self.placed(timeline, doc)
        assert timeline._marker_at(timeline.ms_to_x(marker.time) - 60) is None

    def test_body_of_the_flag_is_hit(self, timeline, doc) -> None:
        """Флажок нарисован вправо от времени — щёлкают по нарисованному."""
        marker = self.placed(timeline, doc)
        assert timeline._marker_at(timeline.ms_to_x(marker.time) + 5) == marker

    def test_span_is_hit_along_its_length(self, timeline, doc) -> None:
        marker = Marker(5000, 4000, name="отрезок")
        doc.markers.add(marker)
        assert timeline._marker_at(timeline.ms_to_x(7000)) == marker

    def test_tolerance_is_in_pixels_not_milliseconds(self, timeline, doc) -> None:
        """При обзоре всего фильма допуск в мс был бы долей пикселя."""
        marker = self.placed(timeline, doc)
        timeline.zoom_at(0.05, HEADER_W + 10)
        x = timeline.ms_to_x(marker.time)
        assert timeline._marker_at(x + 3) == marker


class TestPlacing:
    def test_the_flag_button_places_a_marker(
        self, timeline: TimelineWidget, doc: SubtitleDocument, monkeypatch
    ) -> None:
        monkeypatch.setattr(timeline, "edit_marker", lambda *_a, **_k: None)
        timeline.set_time(4000)
        rect = timeline._marker_button_rect()
        press(timeline, rect.center().x(), rect.center().y())
        assert [m.time for m in doc.markers] == [4000]

    def test_clicking_the_empty_lane_places_a_marker_there(
        self, timeline: TimelineWidget, doc: SubtitleDocument, monkeypatch
    ) -> None:
        monkeypatch.setattr(timeline, "edit_marker", lambda *_a, **_k: None)
        press(timeline, timeline.ms_to_x(6000), lane_y())
        assert len(doc.markers) == 1
        assert abs(doc.markers[0].time - 6000) < 100

    def test_clicking_the_lane_does_not_move_the_playhead(
        self, timeline: TimelineWidget, monkeypatch
    ) -> None:
        """Промах по флажку не должен стоить места, на котором стоял курсор."""
        monkeypatch.setattr(timeline, "edit_marker", lambda *_a, **_k: None)
        timeline.set_time(1000)
        press(timeline, timeline.ms_to_x(6000), lane_y())
        assert timeline.time_ms == 1000

    def test_clicking_the_time_scale_still_seeks(
        self, timeline: TimelineWidget
    ) -> None:
        press(timeline, timeline.ms_to_x(6000), TIME_LANE_H / 2)
        assert abs(timeline.time_ms - 6000) < 100

    def test_placing_is_undoable(
        self, timeline: TimelineWidget, doc: SubtitleDocument, monkeypatch
    ) -> None:
        monkeypatch.setattr(timeline, "edit_marker", lambda *_a, **_k: None)
        timeline.add_marker_at(3000)
        timeline._undo.undo()
        assert len(doc.markers) == 0

    def test_the_colour_of_the_last_marker_is_reused(
        self, timeline: TimelineWidget, doc: SubtitleDocument, monkeypatch
    ) -> None:
        """Маркеры ставят сериями: выбирать цвет каждый раз — лишнее движение."""
        monkeypatch.setattr(timeline, "edit_marker", lambda *_a, **_k: None)
        first = Marker(1000)
        doc.markers.add(first)
        timeline._recolor_marker(first, "green")
        timeline.add_marker_at(5000)
        assert doc.markers.at(5000).color == "green"


class TestDragging:
    def dragged(self, timeline: TimelineWidget, marker: Marker, to_ms: int) -> None:
        timeline._begin_marker_drag(marker, timeline.ms_to_x(marker.time))
        timeline._update_marker_drag(timeline.ms_to_x(to_ms), alt=True)

    def test_drag_moves_the_marker(self, timeline, doc) -> None:
        marker = Marker(2000, name="а")
        doc.markers.add(marker)
        self.dragged(timeline, marker, 7000)
        assert abs(doc.markers[0].time - 7000) < 100

    def test_a_whole_drag_is_one_undo_step(self, timeline, doc) -> None:
        marker = Marker(2000, name="а")
        doc.markers.add(marker)
        before = timeline._undo.depth_used

        timeline._begin_marker_drag(marker, timeline.ms_to_x(marker.time))
        for ms in (3000, 4000, 5000, 6000):
            timeline._update_marker_drag(timeline.ms_to_x(ms), alt=True)

        assert timeline._undo.depth_used == before + 1
        timeline._undo.undo()
        assert doc.markers[0].time == 2000

    def test_a_click_without_movement_opens_the_dialog(
        self, timeline, doc, monkeypatch
    ) -> None:
        opened = []
        monkeypatch.setattr(timeline, "edit_marker", lambda m, **_k: opened.append(m))
        marker = Marker(2000, name="а")
        doc.markers.add(marker)

        press(timeline, timeline.ms_to_x(2000), lane_y())
        timeline._finish_marker_drag()
        assert opened == [marker]

    def test_a_marker_does_not_go_below_zero(self, timeline, doc) -> None:
        marker = Marker(2000, name="а")
        doc.markers.add(marker)
        self.dragged(timeline, marker, -50_000)
        assert doc.markers[0].time == 0


class TestNavigation:
    def prepared(self, doc: SubtitleDocument) -> None:
        for ms in (1000, 5000, 9000):
            doc.markers.add(Marker(ms))

    def test_next_moves_forward(self, timeline, doc) -> None:
        self.prepared(doc)
        timeline.set_time(0)
        assert timeline.goto_next_marker()
        assert timeline.time_ms == 1000

    def test_previous_moves_back(self, timeline, doc) -> None:
        self.prepared(doc)
        timeline.set_time(9000)
        assert timeline.goto_previous_marker()
        assert timeline.time_ms == 5000

    def test_nothing_ahead_says_no(self, timeline, doc) -> None:
        self.prepared(doc)
        timeline.set_time(20_000)
        assert not timeline.goto_next_marker()

    def test_marker_at_playhead_is_found(self, timeline, doc) -> None:
        self.prepared(doc)
        timeline.set_time(5000)
        assert timeline.marker_at_playhead().time == 5000

    def test_no_marker_at_playhead(self, timeline, doc) -> None:
        self.prepared(doc)
        timeline.set_time(5001)
        assert timeline.marker_at_playhead() is None


class TestLaneMenu:
    def titles(self, menu) -> list[str]:
        return [a.text() for a in menu.actions() if a.text()]

    def test_menu_on_a_flag_is_about_that_flag(self, timeline, doc) -> None:
        doc.markers.add(Marker(5000, name="проба"))
        menu = timeline._marker_menu(timeline.ms_to_x(5000))
        assert any("проба" in title for title in self.titles(menu))

    def test_menu_on_empty_space_offers_a_new_one(self, timeline, doc) -> None:
        menu = timeline._marker_menu(timeline.ms_to_x(5000))
        assert any("Новый маркер" in title for title in self.titles(menu))

    def test_navigation_is_disabled_without_markers(self, timeline) -> None:
        menu = timeline._marker_menu(timeline.ms_to_x(5000))
        following = next(a for a in menu.actions() if a.text() == "Следующий маркер")
        assert not following.isEnabled()

    def test_lane_menu_has_no_event_items(self, timeline, doc) -> None:
        """На полосе маркеров реплик нет — предлагать про них нечего."""
        menu = timeline.context_menu(QPoint(int(timeline.ms_to_x(5000)), int(lane_y())))
        assert not any("реплик" in title.lower() for title in self.titles(menu))

    def test_recolouring_is_undoable(self, timeline, doc) -> None:
        marker = Marker(5000, name="проба")
        doc.markers.add(marker)
        timeline._recolor_marker(marker, "red")
        assert doc.markers[0].color == "red"
        timeline._undo.undo()
        assert doc.markers[0].color == marker.color

    def test_clearing_nothing_adds_no_undo_step(self, timeline) -> None:
        """Пустой шаг в истории выглядит как сломанная отмена."""
        before = timeline._undo.depth_used
        timeline.clear_markers()
        assert timeline._undo.depth_used == before


class TestMarkerDialog:
    def test_fields_show_the_marker(self, qapp) -> None:
        marker = Marker(38_170, 1000, "Маркер 1", "заметка", "вывеска", "red")
        dialog = MarkerDialog(marker)
        assert "38" in dialog.time_edit.text()
        assert dialog.name_edit.text() == "Маркер 1"
        assert dialog.note_edit.toPlainText() == "заметка"
        assert dialog.keyword_edit.text() == "вывеска"
        assert dialog.colors.color() == "red"

    def test_edited_fields_come_back(self, qapp) -> None:
        dialog = MarkerDialog(Marker(1000))
        dialog.name_edit.setText("Смена сцены")
        dialog.keyword_edit.setText("монтаж")
        dialog.colors.set_color("green")
        dialog.duration_spin.setValue(2.5)

        result = dialog.marker()
        assert result.name == "Смена сцены"
        assert result.keyword == "монтаж"
        assert result.color == "green"
        assert result.duration == 2500

    def test_time_can_be_typed(self, qapp) -> None:
        dialog = MarkerDialog(Marker(1000))
        dialog.time_edit.setText("0:00:38.17")
        assert dialog.marker().time == 38_170

    def test_broken_time_keeps_the_old_one(self, qapp) -> None:
        """Мусор в поле не должен уносить маркер в начало файла."""
        dialog = MarkerDialog(Marker(1000))
        dialog.time_edit.setText("когда-то потом")
        assert dialog.marker().time == 1000

    def test_name_gets_the_focus(self, qapp) -> None:
        """Отметку ставят и тут же подписывают: лишний щелчок — на каждой."""
        dialog = MarkerDialog(Marker(0), is_new=True)
        assert dialog.focusWidget() is dialog.name_edit

    def test_zero_duration_reads_as_a_point(self, qapp) -> None:
        dialog = MarkerDialog(Marker(1000))
        assert dialog.duration_spin.text() == "точка"

    def test_delete_button_asks_for_removal(self, qapp) -> None:
        dialog = MarkerDialog(Marker(1000))
        asked = []
        dialog.delete_requested.connect(lambda: asked.append(True))
        dialog.delete_button.click()
        assert asked == [True]

    def test_colour_row_keeps_one_chosen(self, qapp) -> None:
        row = ColorRow()
        row.set_color("green")
        row.set_color("red")
        chosen = [key for key, button in row._buttons.items() if button.isChecked()]
        assert chosen == ["red"]


class TestMarkersList:
    def filled(self, doc: SubtitleDocument) -> None:
        doc.markers.add(Marker(1000, name="Начало", keyword="структура"))
        doc.markers.add(Marker(5000, name="Вывеска", note="перевести надпись"))
        doc.markers.add(Marker(9000, 2000, name="Песня", keyword="музыка"))

    def test_every_marker_is_listed(self, timeline, doc) -> None:
        self.filled(doc)
        dialog = MarkersDialog(doc, timeline)
        assert dialog.tree.topLevelItemCount() == 3

    def test_search_covers_the_note_too(self, timeline, doc) -> None:
        """Человек помнит слово, а не графу, в которую его вписал."""
        self.filled(doc)
        dialog = MarkersDialog(doc, timeline)
        dialog.search.setText("надпись")
        assert dialog.tree.topLevelItemCount() == 1

    def test_search_covers_the_keyword(self, timeline, doc) -> None:
        self.filled(doc)
        dialog = MarkersDialog(doc, timeline)
        dialog.search.setText("музык")
        assert dialog.tree.topLevelItemCount() == 1

    def test_filtered_count_is_reported(self, timeline, doc) -> None:
        self.filled(doc)
        dialog = MarkersDialog(doc, timeline)
        dialog.search.setText("музык")
        assert "1 из 3" in dialog.summary.text()

    def test_empty_list_explains_how_to_add(self, timeline, doc) -> None:
        dialog = MarkersDialog(doc, timeline)
        assert "Alt+M" in dialog.summary.text()

    def test_buttons_need_a_selection(self, timeline, doc) -> None:
        self.filled(doc)
        dialog = MarkersDialog(doc, timeline)
        assert not dialog.goto_button.isEnabled()
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
        assert dialog.goto_button.isEnabled()

    def test_goto_moves_the_playhead(self, timeline, doc) -> None:
        self.filled(doc)
        dialog = MarkersDialog(doc, timeline)
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))
        dialog._goto()
        assert timeline.time_ms == 5000

    def test_delete_removes_the_row(self, timeline, doc) -> None:
        self.filled(doc)
        dialog = MarkersDialog(doc, timeline)
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
        dialog._delete()
        assert dialog.tree.topLevelItemCount() == 2
        assert len(doc.markers) == 2

    def test_row_carries_the_marker_itself(self, timeline, doc) -> None:
        """Фильтр меняет порядок строк — индекс в списке ничего не значит."""
        self.filled(doc)
        dialog = MarkersDialog(doc, timeline)
        dialog.search.setText("Песня")
        item = dialog.tree.topLevelItem(0)
        assert item.data(0, Qt.UserRole).name == "Песня"


class TestMainWindow:
    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def test_every_action_is_registered(self, window) -> None:
        for key in (
            "marker.add", "marker.edit", "marker.next",
            "marker.previous", "marker.list", "marker.clear",
        ):
            assert window._actions_registry.action(key) is not None, key

    def test_add_places_a_marker(self, window, monkeypatch) -> None:
        monkeypatch.setattr(window.timeline, "edit_marker", lambda *_a, **_k: None)
        window.timeline.set_time(7000)
        window.add_marker()
        assert [m.time for m in window._doc.markers] == [7000]

    def test_edit_on_empty_space_places_one(self, window, monkeypatch) -> None:
        """Пункт, который на пустом месте молчит, выглядит поломкой."""
        monkeypatch.setattr(window.timeline, "edit_marker", lambda *_a, **_k: None)
        window.timeline.set_time(3000)
        window.edit_marker_at_playhead()
        assert len(window._doc.markers) == 1

    def test_navigation_reports_the_end(self, window) -> None:
        window.goto_next_marker()
        assert "нет" in window.statusBar().currentMessage()

    def test_clearing_without_markers_says_so(self, window) -> None:
        window.clear_markers()
        assert "Маркеров нет" in window.statusBar().currentMessage()

    def test_marker_count_reaches_the_status_bar(self, window, monkeypatch) -> None:
        monkeypatch.setattr(window.timeline, "edit_marker", lambda *_a, **_k: None)
        window.add_marker()
        assert "1 маркер" in window.statusBar().currentMessage()
