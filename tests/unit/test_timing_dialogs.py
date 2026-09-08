"""Тесты диалогов правки таймингов."""

from __future__ import annotations

from fractions import Fraction

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication, QDialogButtonBox

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.time import FpsModel
from sfstudio.media.keyframes import KeyframeIndex
from sfstudio.ui.timing_dialog import AutoTimingDialog, ShiftTimesDialog

pytestmark = pytest.mark.needs_gui

FPS = FpsModel(Fraction(24, 1))


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    d = SubtitleDocument.blank()
    d.create_event(1000, 1200, "Короткая")
    d.create_event(2000, 2100, "Очень много букв для такого короткого срока")
    d.create_event(3000, 4000, "Обычная")
    d.create_event(4080, 5000, "После зазора")
    return d


class TestAutoTimingWithoutVideo:
    """Видео не открыто — самый частый случай при первом запуске."""

    def test_dialog_builds(self, qapp: QApplication, doc: SubtitleDocument) -> None:
        """Регрессия: диалог падал, когда не было ключевых кадров.

        ``setChecked(False)`` на флажке склеек дёргал пересчёт раньше, чем
        создавались остальные поля, и конструктор валился с AttributeError.
        """
        dialog = AutoTimingDialog(doc, [])
        assert dialog.plan() is not None

    def test_keyframe_option_disabled(self, qapp: QApplication, doc: SubtitleDocument) -> None:
        dialog = AutoTimingDialog(doc, [])
        assert not dialog.keyframe_check.isChecked()
        assert not dialog.keyframe_check.isEnabled()

    def test_frame_grid_disabled_without_fps(self, qapp, doc) -> None:
        dialog = AutoTimingDialog(doc, [])
        assert not dialog.frames_check.isChecked()
        assert not dialog.frames_check.isEnabled()

    def test_other_steps_still_work(self, qapp, doc) -> None:
        dialog = AutoTimingDialog(doc, [])
        dialog.duration_check.setChecked(True)
        dialog.min_duration.setValue(2000)
        assert not dialog.plan().is_empty


class TestAutoTimingWithVideo:
    def _dialog(self, doc: SubtitleDocument) -> AutoTimingDialog:
        return AutoTimingDialog(
            doc, [], fps=FPS, keyframes=KeyframeIndex(times_ms=[3000, 5000])
        )

    def test_keyframe_option_available(self, qapp, doc) -> None:
        assert self._dialog(doc).keyframe_check.isEnabled()

    def test_preview_is_filled(self, qapp, doc) -> None:
        dialog = self._dialog(doc)
        assert dialog.preview.topLevelItemCount() > 0

    def test_disabling_everything_empties_plan(self, qapp, doc) -> None:
        dialog = self._dialog(doc)
        for check in (
            dialog.lead_check, dialog.gap_check, dialog.duration_check,
            dialog.cps_check, dialog.keyframe_check, dialog.frames_check,
        ):
            check.setChecked(False)
        assert dialog.plan().is_empty

    def test_apply_button_follows_plan(self, qapp, doc) -> None:
        dialog = self._dialog(doc)
        assert dialog._ok.isEnabled()
        for check in (
            dialog.lead_check, dialog.gap_check, dialog.duration_check,
            dialog.cps_check, dialog.keyframe_check, dialog.frames_check,
        ):
            check.setChecked(False)
        assert not dialog._ok.isEnabled()

    def test_scope_all_covers_more_than_selection(self, qapp, doc) -> None:
        one = AutoTimingDialog(doc, [doc.events[0]], fps=FPS)
        one.scope_selection.setChecked(True)
        selected = len(one.plan().changes)
        one.scope_all.setChecked(True)
        assert len(one.plan().changes) >= selected


class TestShiftDialog:
    def test_preview_and_mapping(self, qapp, doc) -> None:
        dialog = ShiftTimesDialog(doc, list(doc.events), fps=FPS)
        assert len(dialog.mapping()) == len(doc.events)
        assert dialog.preview.topLevelItemCount() > 0

    def test_backwards_shift_clamps_at_zero(self, qapp, doc) -> None:
        """Сдвиг за начало файла не должен переворачивать реплики."""
        dialog = ShiftTimesDialog(doc, list(doc.events), fps=FPS)
        dialog.direction.setCurrentIndex(1)
        dialog.amount.setValue(60_000)
        for start, end in dialog.mapping().values():
            assert start >= 0
            assert end > start
        assert "упёрлись" in dialog.summary.text()

    def test_only_start_mode_keeps_end(self, qapp, doc) -> None:
        """Границу, которую просили не трогать, не трогаем.

        Раньше слишком большой сдвиг начала «подтягивал» конец, и реплика
        схлопывалась в миллисекунду — хотя пользователь просил конец не менять.
        """
        dialog = ShiftTimesDialog(doc, list(doc.events), fps=FPS)
        dialog.what.setCurrentIndex(1)  # только начало
        dialog.amount.setValue(5000)
        for eid, (start, end) in dialog.mapping().items():
            assert end == doc.by_eid(eid).end, "конец не должен меняться"
            assert start < end

    def test_scope_after_selection(self, qapp, doc) -> None:
        dialog = ShiftTimesDialog(doc, [doc.events[2]], fps=FPS)
        dialog.scope.setCurrentIndex(2)  # от выделенной и далее
        moved = set(dialog.mapping())
        assert doc.events[0].eid not in moved
        assert doc.events[3].eid in moved

    def test_zero_shift_disables_button(self, qapp, doc) -> None:
        dialog = ShiftTimesDialog(doc, list(doc.events), fps=FPS)
        dialog.amount.setValue(0)
        assert not dialog.mapping()
        assert not dialog._ok.isEnabled()

    def test_describe_mentions_direction(self, qapp, doc) -> None:
        dialog = ShiftTimesDialog(doc, list(doc.events), fps=FPS)
        dialog.direction.setCurrentIndex(1)
        dialog.amount.setValue(250)
        assert "-250" in dialog.describe()


class TestTranslation:
    def test_buttons_are_translated(self, qapp, doc) -> None:
        """Qt подставляет английские подписи, если их не задать явно."""
        for dialog in (
            AutoTimingDialog(doc, [], fps=FPS),
            ShiftTimesDialog(doc, list(doc.events), fps=FPS),
        ):
            box = dialog.findChild(QDialogButtonBox)
            assert box is not None
            for button in box.buttons():
                text = button.text().replace("&", "")
                assert text, "у кнопки должна быть подпись"
                assert not text.isascii(), f"кнопка не переведена: {text!r}"
