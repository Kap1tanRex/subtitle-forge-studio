"""Окна коррекции частоты кадров и приём перетаскивания."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.fps_dialog import FpsSyncDialog
from sfstudio.ui.media_offer import FpsOfferDialog
from sfstudio.ui.video_pane import VideoPane

pytestmark = pytest.mark.needs_gui

FILM = Fraction(24, 1)
NTSC_FILM = Fraction(24000, 1001)
PAL = Fraction(25, 1)
HOUR = 3_600_000


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(0, 2000, "Начало")
    document.create_event(HOUR, HOUR + 2000, "Через час")
    return document


@pytest.fixture
def dialog(qapp: QApplication, doc: SubtitleDocument) -> FpsSyncDialog:
    return FpsSyncDialog(doc, [], video_fps=NTSC_FILM, source_fps=PAL)


class TestSyncDialog:
    def test_video_rate_is_preselected(self, dialog: FpsSyncDialog) -> None:
        """Частота видео известна из метаданных — её и предлагаем."""
        assert dialog.target_box.currentData() == NTSC_FILM

    def test_known_source_is_preselected(self, dialog: FpsSyncDialog) -> None:
        assert dialog.source_box.currentData() == PAL

    def test_unusual_video_rate_is_added_not_replaced(self, qapp, doc) -> None:
        """Подменять частоту ближайшей готовой нельзя — на ней весь расчёт."""
        odd = Fraction(100, 7)
        made = FpsSyncDialog(doc, [], video_fps=odd)
        assert made.target_box.currentData() == odd

    def test_preview_lists_the_changes(self, dialog: FpsSyncDialog) -> None:
        assert dialog.preview.topLevelItemCount() == 2

    def test_mapping_moves_the_later_line_further(self, dialog: FpsSyncDialog, doc) -> None:
        """Сползание тем больше, чем дальше от начала."""
        mapping = dialog.mapping()
        first, second = doc.events
        assert mapping[second.eid][0] - second.start > mapping[first.eid][0] - first.start

    def test_same_rate_disables_the_button(self, qapp, doc) -> None:
        made = FpsSyncDialog(doc, [], video_fps=PAL, source_fps=PAL)
        assert not made._ok.isEnabled()

    def test_same_rate_says_so(self, qapp, doc) -> None:
        made = FpsSyncDialog(doc, [], video_fps=PAL, source_fps=PAL)
        assert "совпада" in made.verdict.text()

    def test_verdict_names_the_drift(self, dialog: FpsSyncDialog) -> None:
        """Множитель ни о чём не говорит, а «к концу столько-то» — говорит."""
        assert "конц" in dialog.verdict.text()

    def test_unrelated_rates_are_questioned(self, qapp, doc) -> None:
        made = FpsSyncDialog(doc, [], video_fps=Fraction(30000, 1001), source_fps=PAL)
        assert "не родственны" in made.verdict.text()

    def test_selection_scope_limits_the_work(self, qapp, doc) -> None:
        made = FpsSyncDialog(doc, [doc.events[0]], video_fps=NTSC_FILM, source_fps=PAL)
        made.scope.setCurrentIndex(1)
        assert set(made.mapping()) == {doc.events[0].eid}

    def test_switching_rates_recalculates(self, dialog: FpsSyncDialog) -> None:
        before = dict(dialog.mapping())
        dialog.source_box.setCurrentIndex(0)
        assert dialog.mapping() != before


class TestOfferDialog:
    @pytest.fixture
    def offer(self, qapp: QApplication) -> FpsOfferDialog:
        return FpsOfferDialog(
            media_name="серия.mkv", video_fps=NTSC_FILM, project_fps=PAL,
            span_ms=HOUR, events=120,
        )

    def test_nothing_is_the_default(self, offer: FpsOfferDialog) -> None:
        """Пересчёт трогает весь документ — выбирать его за человека нельзя."""
        assert offer.choice().is_nothing

    def test_adopt_only_records_the_rate(self, offer: FpsOfferDialog) -> None:
        offer.adopt.setChecked(True)
        choice = offer.choice()
        assert choice.adopt
        assert not choice.rescale

    def test_rescale_also_records_the_rate(self, offer: FpsOfferDialog) -> None:
        """Пересчитать и не запомнить — значит спросить снова завтра."""
        offer.rescale.setChecked(True)
        assert offer.choice().adopt

    def test_rescale_is_offered_when_there_are_lines(self, offer: FpsOfferDialog) -> None:
        assert offer.rescale.isEnabled()

    def test_rescale_is_pointless_without_lines(self, qapp) -> None:
        made = FpsOfferDialog(
            media_name="x.mkv", video_fps=NTSC_FILM, project_fps=PAL,
            span_ms=0, events=0,
        )
        assert not made.rescale.isEnabled()


class TestDropOnFrame:
    @pytest.fixture
    def pane(self, qapp: QApplication, doc: SubtitleDocument) -> VideoPane:
        widget = VideoPane(doc, UndoStack(doc))
        widget.resize(640, 360)
        return widget

    def test_pane_accepts_drops(self, pane: VideoPane) -> None:
        assert pane.acceptDrops()

    def test_video_is_accepted(self, pane: VideoPane) -> None:
        assert _drag_enter(pane, ["кино.mkv"])

    def test_subtitles_are_accepted(self, pane: VideoPane) -> None:
        assert _drag_enter(pane, ["перевод.ass"])

    def test_junk_is_refused(self, pane: VideoPane) -> None:
        """Курсор с плюсом над файлом, который не откроется, — обман."""
        assert not _drag_enter(pane, ["readme.txt"])

    def test_hint_appears_while_hovering(self, pane: VideoPane) -> None:
        _drag_enter(pane, ["кино.mkv"])
        assert "кино.mkv" in pane._drop_hint

    def test_hint_goes_to_the_overlay(self, pane: VideoPane) -> None:
        """Рисовать её должен оверлей, а не сама панель.

        Панель — контейнер: её собственная отрисовка оказывается под кадром,
        который рисует вложенный виджет, и подсказки не видно совсем.
        """
        _drag_enter(pane, ["кино.mkv"])
        assert pane.overlay.drop_hint == pane._drop_hint

    def test_overlay_hint_clears_too(self, pane: VideoPane) -> None:
        _drag_enter(pane, ["кино.mkv"])
        _drop(pane, ["кино.mkv"])
        assert not pane.overlay.drop_hint

    def test_hint_says_what_will_happen(self, pane: VideoPane) -> None:
        _drag_enter(pane, ["перевод.ass"])
        assert "субтитры" in pane._drop_hint

    def test_hint_clears_on_leave(self, pane: VideoPane) -> None:
        from PySide6.QtGui import QDragLeaveEvent

        _drag_enter(pane, ["кино.mkv"])
        pane.dragLeaveEvent(QDragLeaveEvent())
        assert not pane._drop_hint

    def test_drop_reports_the_paths(self, pane: VideoPane) -> None:
        seen = []
        pane.files_dropped.connect(seen.append)
        _drop(pane, ["кино.mkv"])
        assert [Path(p).name for p in seen[0]] == ["кино.mkv"]

    def test_hint_clears_after_the_drop(self, pane: VideoPane) -> None:
        _drag_enter(pane, ["кино.mkv"])
        _drop(pane, ["кино.mkv"])
        assert not pane._drop_hint

    def test_frame_paints_with_a_hint(self, pane: VideoPane) -> None:
        """Подсказка рисуется поверх кадра — она не должна ронять отрисовку."""
        from PySide6.QtGui import QPixmap

        _drag_enter(pane, ["кино.mkv"])
        pane.render(QPixmap(pane.size()))


def _mime(names: list[str]) -> QMimeData:
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(Path.cwd() / name)) for name in names])
    return data


def _drag_enter(pane: VideoPane, names: list[str]) -> bool:
    """Проводит перетаскивание над кадром. Возвращает «приняли ли».

    Возвращается именно ``bool``, а не само событие: событие держит указатель
    на данные, которыми владеет вызывающий, и стоит им разойтись по времени
    жизни, как обращение к событию роняет процесс целиком — вместе с
    прогоном, в котором его же и печатали в отчёт об ошибке.
    """
    data = _mime(names)
    event = QDragEnterEvent(
        QPoint(10, 10), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier
    )
    pane.dragEnterEvent(event)
    accepted = event.isAccepted()
    del event, data
    return accepted


def _drop(pane: VideoPane, names: list[str]) -> None:
    data = _mime(names)
    event = QDropEvent(
        QPoint(10, 10), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier
    )
    pane.dropEvent(event)
    del event, data
