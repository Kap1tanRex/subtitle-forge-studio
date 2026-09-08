"""Тесты дорожек на таймлайне, транспорта, инспектора и раскраски по акторам."""

from __future__ import annotations

import itertools

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from sfstudio.core.color import RGBA
from sfstudio.core.commands import AddActor, AddTrack, AssignActor, SetTrackFlags
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.tracks import TrackKind
from sfstudio.core.undo import UndoStack
from sfstudio.ui.event_table import COL_ACTOR, COL_TEXT, EventTableModel
from sfstudio.ui.timeline import HEADER_W, RULER_H, TimelineWidget
from sfstudio.ui.transport import SPEED_PRESETS, TransportBar

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(1000, 2000, "Первая", layer=0)
    document.create_event(3000, 4000, "Вторая", layer=0)
    return document


@pytest.fixture
def timeline(qapp: QApplication, doc: SubtitleDocument) -> TimelineWidget:
    widget = TimelineWidget(doc, UndoStack(doc))
    widget.resize(1000, 320)
    widget.fit_all()
    return widget


def press(w: TimelineWidget, x: float, y: float, mods=Qt.NoModifier) -> None:
    w.mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, QPointF(x, y), QPointF(x, y),
                    Qt.LeftButton, Qt.LeftButton, mods)
    )


class TestLayout:
    def test_media_tracks_are_at_the_bottom(self, timeline: TimelineWidget) -> None:
        """Видео и звук — внизу, как в монтажном столе."""
        kinds = [row.track.kind for row in timeline.rows()]
        assert kinds[-2:] == [TrackKind.VIDEO, TrackKind.AUDIO]

    def test_subtitle_tracks_are_above_media(self, timeline: TimelineWidget) -> None:
        rows = timeline.rows()
        subtitle_bottom = max(r.bottom for r in rows if r.track.is_subtitle)
        video_top = next(r.top for r in rows if r.track.kind is TrackKind.VIDEO)
        assert subtitle_bottom <= video_top

    def test_higher_layer_is_drawn_higher(self, timeline: TimelineWidget) -> None:
        timeline._undo.run(AddTrack("Надписи"))
        rows = [r for r in timeline.rows() if r.track.is_subtitle]
        assert rows[0].track.layer > rows[1].track.layer

    def test_audio_takes_the_remaining_height(self, timeline: TimelineWidget) -> None:
        """Каждый лишний пиксель полезен волне, а не пустым полосам."""
        audio = timeline.rows()[-1]
        assert audio.bottom == pytest.approx(timeline.height(), abs=1)

    def test_rows_do_not_overlap(self, timeline: TimelineWidget) -> None:
        rows = timeline.rows()
        for upper, lower in itertools.pairwise(rows):
            assert upper.bottom == pytest.approx(lower.top, abs=0.01)

    def test_adding_a_track_adds_a_row(self, timeline: TimelineWidget) -> None:
        before = len(timeline.rows())
        timeline._undo.run(AddTrack())
        assert len(timeline.rows()) == before + 1


class TestCoordinates:
    def test_header_column_is_excluded_from_time(self, timeline: TimelineWidget) -> None:
        """Слева колонка заголовков: время начинается за ней."""
        assert timeline.ms_to_x(timeline._view_start_ms) == pytest.approx(HEADER_W)

    def test_ms_x_round_trip(self, timeline: TimelineWidget) -> None:
        for ms in (0, 500, 4000):
            assert timeline.x_to_ms(timeline.ms_to_x(ms)) == pytest.approx(ms)

    def test_click_in_header_does_not_seek(self, timeline: TimelineWidget) -> None:
        seen: list[int] = []
        timeline.time_changed.connect(seen.append)
        row = timeline._row_for_layer(0)
        press(timeline, 20, row.top + 5)
        assert not seen


class TestHitTesting:
    def test_event_is_found_on_its_own_track(self, timeline: TimelineWidget) -> None:
        event = timeline._doc.events[0]
        row = timeline._row_for_layer(0)
        x = timeline.ms_to_x((event.start + event.end) / 2)
        hit = timeline._event_at(x, row.top + row.height / 2)
        assert hit is not None
        assert hit[0].eid == event.eid

    def test_event_is_not_found_on_another_track(self, timeline: TimelineWidget) -> None:
        """Слой — это дорожка: реплика слоя 0 не должна ловиться на слое 1."""
        timeline._undo.run(AddTrack())
        event = timeline._doc.events[0]
        row = timeline._row_for_layer(1)
        x = timeline.ms_to_x((event.start + event.end) / 2)
        assert timeline._event_at(x, row.top + row.height / 2) is None

    def test_locked_track_is_not_hit(self, timeline: TimelineWidget) -> None:
        """Замок должен именно защищать: реплика не ловится мышью."""
        timeline._undo.run(SetTrackFlags(0, locked=True))
        event = timeline._doc.events[0]
        row = timeline._row_for_layer(0)
        x = timeline.ms_to_x((event.start + event.end) / 2)
        assert timeline._event_at(x, row.top + row.height / 2) is None

    def test_media_rows_are_not_events(self, timeline: TimelineWidget) -> None:
        video = next(r for r in timeline.rows() if r.track.kind is TrackKind.VIDEO)
        assert timeline._event_at(HEADER_W + 200, video.top + 5) is None


class TestTrackButtons:
    def test_eye_toggles_visibility(self, timeline: TimelineWidget) -> None:
        row = timeline._row_for_layer(0)
        glyph, rect = timeline._header_buttons(row)[0]
        press(timeline, rect.center().x(), rect.center().y())
        assert not timeline._doc.tracks.by_layer(0).visible

    def test_lock_button_toggles_lock(self, timeline: TimelineWidget) -> None:
        row = timeline._row_for_layer(0)
        _, rect = timeline._header_buttons(row)[1]
        press(timeline, rect.center().x(), rect.center().y())
        assert timeline._doc.tracks.by_layer(0).locked

    def test_toggle_is_undoable(self, timeline: TimelineWidget) -> None:
        row = timeline._row_for_layer(0)
        _, rect = timeline._header_buttons(row)[1]
        press(timeline, rect.center().x(), rect.center().y())
        timeline._undo.undo()
        assert not timeline._doc.tracks.by_layer(0).locked

    def test_audio_row_has_a_mute_button(self, timeline: TimelineWidget) -> None:
        audio = next(r for r in timeline.rows() if r.track.kind is TrackKind.AUDIO)
        assert len(timeline._header_buttons(audio)) == 1

    def test_video_row_has_no_buttons(self, timeline: TimelineWidget) -> None:
        video = next(r for r in timeline.rows() if r.track.kind is TrackKind.VIDEO)
        assert timeline._header_buttons(video) == []

    def test_plus_button_adds_a_track(self, timeline: TimelineWidget) -> None:
        rect = timeline._add_button_rect()
        press(timeline, rect.center().x(), rect.center().y())
        assert len(timeline._doc.tracks.subtitles) == 2

    def test_plus_button_is_inside_the_ruler(self, timeline: TimelineWidget) -> None:
        rect = timeline._add_button_rect()
        assert rect.bottom() <= RULER_H
        assert rect.right() <= HEADER_W


class TestPainting:
    def test_paints_without_error(self, timeline: TimelineWidget) -> None:
        """Отрисовка со всеми видами дорожек не должна падать."""
        from PySide6.QtGui import QPixmap

        timeline._undo.run(AddTrack("Надписи"))
        timeline._undo.run(SetTrackFlags(0, visible=False))
        pixmap = QPixmap(timeline.size())
        timeline.render(pixmap)

    def test_paints_with_actor_colours(self, timeline: TimelineWidget) -> None:
        from PySide6.QtGui import QPixmap

        timeline._undo.run(AddActor("Анна", RGBA.from_hex("#FF8800")))
        timeline._undo.run(AssignActor([timeline._doc.events[0].eid], "Анна"))
        pixmap = QPixmap(timeline.size())
        timeline.render(pixmap)


class TestTransport:
    @pytest.fixture
    def bar(self, qapp: QApplication) -> TransportBar:
        return TransportBar()

    def test_starts_disabled_without_video(self, bar: TransportBar) -> None:
        """Без видео кнопки не работают — гасим, а не делаем вид, что играем."""
        assert not bar.btn_play.isEnabled()

    def test_pause_state_is_reflected_not_stored(self, bar: TransportBar) -> None:
        """Кнопка отражает состояние плеера, а не своё представление о нём."""
        bar.set_paused(False)
        assert "Пауза" in bar.btn_play.toolTip()
        bar.set_paused(True)
        assert "Играть" in bar.btn_play.toolTip()

    def test_buttons_have_drawn_icons(self, bar: TransportBar) -> None:
        """Иконки рисуются кодом: эмодзи без нужного шрифта дают пустые рамки."""
        for button in (bar.btn_start, bar.btn_prev, bar.btn_play,
                       bar.btn_next, bar.btn_end, bar.btn_loop):
            assert not button.icon().isNull()
            assert button.toolTip()

    def test_speed_presets_include_slow_and_fast(self, bar: TransportBar) -> None:
        assert 0.25 in SPEED_PRESETS
        assert 4.0 in SPEED_PRESETS

    def test_selecting_speed_emits(self, bar: TransportBar) -> None:
        seen: list[float] = []
        bar.speed_selected.connect(seen.append)
        bar.set_enabled_transport(True)
        bar.speed_box.setCurrentIndex(SPEED_PRESETS.index(2.0))
        assert seen == [2.0]

    def test_setting_speed_from_player_does_not_echo(self, bar: TransportBar) -> None:
        """Показ значения плеера не должен отправлять его обратно плееру."""
        seen: list[float] = []
        bar.speed_selected.connect(seen.append)
        bar.set_speed(2.0)
        assert seen == []

    def test_unknown_speed_is_added_not_rounded(self, bar: TransportBar) -> None:
        """Плеер мог зажать значение — показываем его, а не ближайший пресет."""
        bar.set_speed(1.8)
        assert bar.speed_box.currentData() == pytest.approx(1.8)

    def test_step_speed_up_and_down(self, bar: TransportBar) -> None:
        bar.set_speed(1.0)
        assert bar.step_speed(True) == 1.25
        bar.set_speed(1.0)
        assert bar.step_speed(False) == 0.75

    def test_step_speed_stops_at_the_ends(self, bar: TransportBar) -> None:
        bar.set_speed(SPEED_PRESETS[-1])
        assert bar.step_speed(True) == SPEED_PRESETS[-1]
        bar.set_speed(SPEED_PRESETS[0])
        assert bar.step_speed(False) == SPEED_PRESETS[0]

    def test_position_is_shown_as_timecode(self, bar: TransportBar) -> None:
        bar.set_position(3_600_000)
        assert bar.time_label.text().startswith("01:00:00")


class TestActorColouring:
    def test_row_takes_actor_colour(self, qapp: QApplication, doc) -> None:
        model = EventTableModel(doc)
        doc.actors.add("Анна", RGBA.from_hex("#FF8800"))
        doc.events[0].name = "Анна"
        colour = model.data(model.index(0, COL_TEXT), Qt.BackgroundRole)
        assert colour is not None
        assert (colour.red(), colour.green(), colour.blue()) == (0xFF, 0x88, 0x00)

    def test_actor_column_is_more_saturated(self, qapp: QApplication, doc) -> None:
        """В столбце «Актор» цвет и есть содержание — там он ярче."""
        model = EventTableModel(doc)
        doc.actors.add("Анна", RGBA.from_hex("#FF8800"))
        doc.events[0].name = "Анна"
        actor_cell = model.data(model.index(0, COL_ACTOR), Qt.BackgroundRole)
        text_cell = model.data(model.index(0, COL_TEXT), Qt.BackgroundRole)
        assert actor_cell.alpha() > text_cell.alpha()

    def test_unregistered_actor_is_not_coloured(self, qapp: QApplication, doc) -> None:
        """Имя из чужого файла без записи в реестре цвета не получает."""
        model = EventTableModel(doc)
        doc.events[0].name = "Неизвестный"
        assert model.data(model.index(0, COL_TEXT), Qt.BackgroundRole) is None

    def test_row_without_actor_is_not_coloured(self, qapp: QApplication, doc) -> None:
        model = EventTableModel(doc)
        assert model.data(model.index(0, COL_TEXT), Qt.BackgroundRole) is None
