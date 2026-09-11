"""Диалоги правки таймингов: доводка и сдвиг.

Оба показывают предпросмотр до применения. Массовая правка таймингов —
операция, которую трудно оценить на глаз: «сдвинуть на 400 мс» звучит
безобидно, пока не выяснится, что половина реплик уехала за начало файла.
Поэтому диалог сначала считает результат и показывает первые строки
«было → стало», и лишь потом применяет.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.time import FpsModel, format_ass
from sfstudio.media.keyframes import KeyframeIndex
from sfstudio.services.autotiming import TimingPlan, TimingSettings, build_plan

__all__ = ["PREVIEW_ROWS", "AutoTimingDialog", "PreviewTree", "ShiftTimesDialog"]

PREVIEW_ROWS = 40


class PreviewTree(QTreeWidget):
    """Список «было → стало»."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels([tr('Реплика'), tr('Было'), tr('Станет'), tr('Длительность')])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setColumnWidth(0, 240)
        self.setColumnWidth(1, 150)
        self.setColumnWidth(2, 150)

    def show_plan(self, plan: TimingPlan, doc: SubtitleDocument) -> None:
        self.setUpdatesEnabled(False)
        self.clear()
        try:
            for change in plan.changes[:PREVIEW_ROWS]:
                event = doc.get(change.eid)
                if event is None:
                    continue
                delta = change.delta_duration
                length = tr('{0:.2f} с').format((change.new_end - change.new_start) / 1000)
                if delta:
                    length += tr(' ({0:+d} мс)').format(delta)
                item = QTreeWidgetItem([
                    (event.plain or tr('(пусто)')).replace("\n", " ")[:60],
                    f"{format_ass(change.old_start)} – {format_ass(change.old_end)}",
                    f"{format_ass(change.new_start)} – {format_ass(change.new_end)}",
                    length,
                ])
                if change.eid in plan.unresolved:
                    item.setToolTip(0, tr('Не хватило места: мешает соседняя реплика'))
                self.addTopLevelItem(item)
        finally:
            self.setUpdatesEnabled(True)


class AutoTimingDialog(QDialog):
    """Доводка таймингов с предпросмотром."""

    def __init__(
        self,
        doc: SubtitleDocument,
        selection: list[SubtitleEvent],
        *,
        fps: FpsModel | None = None,
        keyframes: KeyframeIndex | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Доводка таймингов'))
        self.resize(880, 620)
        self._doc = doc
        self._selection = selection
        self._fps = fps
        self._keyframes = keyframes
        self._plan = TimingPlan()
        # Поля создаются по очереди, а каждое сразу подключает пересчёт.
        # Без этого флага изменение раннего поля дёргало бы _recalculate,
        # когда поздние ещё не существуют — падение при отсутствии
        # ключевых кадров, то есть каждый раз, когда видео не открыто.
        self._ready = False

        root = QVBoxLayout(self)

        scope = QGroupBox(tr('К чему применить'))
        scope_layout = QHBoxLayout(scope)
        self.scope_selection = QRadioButton(tr('К выделенным ({0})').format(len(selection)))
        self.scope_all = QRadioButton(tr('Ко всем ({0})').format(len(doc)))
        (self.scope_selection if len(selection) > 1 else self.scope_all).setChecked(True)
        self.scope_selection.setEnabled(len(selection) > 0)
        for button in (self.scope_selection, self.scope_all):
            button.toggled.connect(self._recalculate)
            scope_layout.addWidget(button)
        scope_layout.addStretch(1)
        root.addWidget(scope)

        options = QGroupBox(tr('Что сделать'))
        form = QFormLayout(options)

        self.lead_in = _spin(0, 3000, 120, self._recalculate, tr(' мс'))
        self.lead_out = _spin(0, 3000, 300, self._recalculate, tr(' мс'))
        lead_row = QHBoxLayout()
        lead_row.addWidget(QLabel(tr('раньше на')))
        lead_row.addWidget(self.lead_in)
        lead_row.addWidget(QLabel(tr('дольше на')))
        lead_row.addWidget(self.lead_out)
        lead_row.addStretch(1)
        self.lead_check = _group_check(
            tr('Расширить реплики'), form, _wrap(lead_row), self._recalculate
        )

        self.close_gap = _spin(0, 2000, 200, self._recalculate, tr(' мс'))
        self.gap_check = _group_check(
            tr('Смыкать зазоры короче'), form, self.close_gap, self._recalculate
        )

        self.min_duration = _spin(0, 10_000, 1000, self._recalculate, tr(' мс'))
        self.duration_check = _group_check(
            tr('Минимальная длительность'), form, self.min_duration, self._recalculate
        )

        self.target_cps = QDoubleSpinBox()
        self.target_cps.setRange(1.0, 60.0)
        self.target_cps.setValue(17.0)
        self.target_cps.setSuffix(tr(' симв/с'))
        self.target_cps.valueChanged.connect(self._recalculate)
        self.cps_check = _group_check(
            tr('Растянуть под скорость чтения'), form, self.target_cps, self._recalculate
        )

        self.keyframe_radius = _spin(1, 60, 5, self._recalculate, tr(' кадр.'))
        self.keyframe_check = _group_check(
            tr('Притянуть к монтажным склейкам'), form, self.keyframe_radius, self._recalculate
        )
        has_keyframes = bool(keyframes)
        self.keyframe_check.setEnabled(has_keyframes)
        if not has_keyframes:
            self.keyframe_check.setChecked(False)
            self.keyframe_check.setToolTip(
                tr('Индекс ключевых кадров не построен — откройте видео')
            )

        self.frames_check = QCheckBox(tr('Округлить к сетке кадров'))
        self.frames_check.setChecked(fps is not None)
        self.frames_check.setEnabled(fps is not None)
        if fps is None:
            self.frames_check.setToolTip(tr('Частота кадров неизвестна — откройте видео'))
        self.frames_check.stateChanged.connect(self._recalculate)
        form.addRow("", self.frames_check)

        self.min_gap = _spin(0, 30, 2, self._recalculate, tr(' кадр.'))
        form.addRow(tr('Зазор между репликами:'), self.min_gap)
        root.addWidget(options)

        root.addWidget(QLabel(tr('Предпросмотр:')))
        self.preview = PreviewTree()
        root.addWidget(self.preview, 1)

        self.summary = QLabel("")
        self.summary.setProperty("role", "hint")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText(tr('Применить'))
        buttons.button(QDialogButtonBox.Cancel).setText(tr('Отмена'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        root.addWidget(buttons)

        self._ready = True
        self._recalculate()

    # -- расчёт ------------------------------------------------------------------- #

    def settings(self) -> TimingSettings:
        return TimingSettings(
            lead_in_ms=self.lead_in.value() if self.lead_check.isChecked() else 0,
            lead_out_ms=self.lead_out.value() if self.lead_check.isChecked() else 0,
            close_gap_below_ms=self.close_gap.value() if self.gap_check.isChecked() else 0,
            min_duration_ms=(
                self.min_duration.value() if self.duration_check.isChecked() else 0
            ),
            target_cps=self.target_cps.value() if self.cps_check.isChecked() else None,
            snap_to_keyframes=self.keyframe_check.isChecked(),
            keyframe_radius_frames=self.keyframe_radius.value(),
            snap_to_frames=self.frames_check.isChecked(),
            min_gap_frames=self.min_gap.value(),
        )

    def _targets(self) -> list[SubtitleEvent]:
        if self.scope_selection.isChecked() and self._selection:
            return self._selection
        return list(self._doc.events)

    def _recalculate(self, *_args) -> None:
        if not self._ready:
            return
        self._plan = build_plan(
            self._targets(),
            self.settings(),
            fps=self._fps,
            keyframes=self._keyframes,
            all_events=self._doc.events,
        )
        self.preview.show_plan(self._plan, self._doc)

        text = self._plan.summary()
        if len(self._plan.changes) > PREVIEW_ROWS:
            text += tr(' · показаны первые {0}').format(PREVIEW_ROWS)
        if self._plan.unresolved:
            text += "\n" + tr(
                'Подсвеченным репликам не хватило места: мешают соседние.'
            )
        self.summary.setText(text)
        self._ok.setEnabled(not self._plan.is_empty)

    def plan(self) -> TimingPlan:
        return self._plan


class ShiftTimesDialog(QDialog):
    """Сдвиг таймингов на фиксированную величину."""

    def __init__(
        self,
        doc: SubtitleDocument,
        selection: list[SubtitleEvent],
        *,
        fps: FpsModel | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Сдвиг таймингов'))
        self.resize(760, 520)
        self._doc = doc
        self._selection = selection
        self._fps = fps
        self._ready = False

        root = QVBoxLayout(self)
        form = QFormLayout()

        amount_row = QHBoxLayout()
        self.amount = QSpinBox()
        self.amount.setRange(0, 10 * 60 * 60 * 1000)
        self.amount.setValue(500)
        self.amount.setSuffix(tr(' мс'))
        self.amount.valueChanged.connect(self._recalculate)
        self.direction = QComboBox()
        self.direction.addItem(tr('вперёд'), 1)
        self.direction.addItem(tr('назад'), -1)
        self.direction.currentIndexChanged.connect(self._recalculate)
        amount_row.addWidget(self.amount)
        amount_row.addWidget(self.direction)
        amount_row.addStretch(1)
        form.addRow(tr('Сдвинуть на:'), _wrap(amount_row))

        self.scope = QComboBox()
        self.scope.addItem(tr('выделенные ({0})').format(len(selection)), "selection")
        self.scope.addItem(tr('все ({0})').format(len(doc)), "all")
        self.scope.addItem(tr('от выделенной и далее'), "after")
        if len(selection) <= 1:
            self.scope.setCurrentIndex(1)
        self.scope.currentIndexChanged.connect(self._recalculate)
        form.addRow(tr('Что двигать:'), self.scope)

        self.what = QComboBox()
        self.what.addItem(tr('начало и конец'), "both")
        self.what.addItem(tr('только начало'), "start")
        self.what.addItem(tr('только конец'), "end")
        self.what.currentIndexChanged.connect(self._recalculate)
        form.addRow(tr('Границы:'), self.what)
        root.addLayout(form)

        root.addWidget(QLabel(tr('Предпросмотр:')))
        self.preview = PreviewTree()
        root.addWidget(self.preview, 1)

        self.summary = QLabel("")
        self.summary.setProperty("role", "hint")
        root.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText(tr('Сдвинуть'))
        buttons.button(QDialogButtonBox.Cancel).setText(tr('Отмена'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        root.addWidget(buttons)

        self._mapping: dict[int, tuple[int, int]] = {}
        self._ready = True
        self._recalculate()

    def _targets(self) -> list[SubtitleEvent]:
        mode = self.scope.currentData()
        if mode == "all":
            return list(self._doc.events)
        if mode == "after" and self._selection:
            first = min(e.start for e in self._selection)
            return [e for e in self._doc.events if e.start >= first]
        return list(self._selection)

    def _recalculate(self, *_args) -> None:
        if not self._ready:
            return
        delta = self.amount.value() * int(self.direction.currentData())
        what = self.what.currentData()
        self._mapping = {}
        clamped = 0

        for event in self._targets():
            move_start = what in ("both", "start")
            move_end = what in ("both", "end")
            start = event.start + delta if move_start else event.start
            end = event.end + delta if move_end else event.end

            if start < 0:
                # Сдвиг за начало файла: реплика упирается в ноль, но не
                # переворачивается. Об этом надо сказать — иначе часть реплик
                # молча слипнется в начале.
                start = 0
                clamped += 1

            if end <= start:
                # Границу, которую пользователь просил не трогать, не трогаем:
                # в режиме «только начало» конец неприкосновенен, поэтому
                # упирается начало, а не растягивается конец.
                if move_start and not move_end:
                    start = max(0, end - 1)
                    clamped += 1
                elif move_end and not move_start:
                    end = start + 1
                    clamped += 1
                else:
                    end = start + 1

            if (start, end) != (event.start, event.end):
                self._mapping[event.eid] = (start, end)

        plan = TimingPlan(changes=[
            _change(self._doc, eid, times) for eid, times in self._mapping.items()
        ])
        self.preview.show_plan(plan, self._doc)

        text = tr('затронуто реплик: {0}').format(len(self._mapping))
        if len(self._mapping) > PREVIEW_ROWS:
            text += tr(' · показаны первые {0}').format(PREVIEW_ROWS)
        if clamped:
            text += tr(' · упёрлись в ограничение: {0}').format(clamped)
        self.summary.setText(text)
        self._ok.setEnabled(bool(self._mapping))

    def mapping(self) -> dict[int, tuple[int, int]]:
        return self._mapping

    def describe(self) -> str:
        delta = self.amount.value() * int(self.direction.currentData())
        return tr('Сдвиг на {0:+d} мс').format(delta)


def _change(doc: SubtitleDocument, eid: int, times: tuple[int, int]):
    from sfstudio.services.autotiming import TimingChange

    event = doc.by_eid(eid)
    return TimingChange(eid, event.start, event.end, times[0], times[1])


def _spin(low: int, high: int, value: int, slot, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setValue(value)
    spin.setSingleStep(10 if high > 100 else 1)
    if suffix:
        spin.setSuffix(suffix)
    spin.valueChanged.connect(slot)
    return spin


def _group_check(title: str, form: QFormLayout, field: QWidget, slot) -> QCheckBox:
    """Флажок слева, настройка справа. Флажок включает и выключает поле."""
    check = QCheckBox(title)
    check.setChecked(True)
    check.stateChanged.connect(lambda state: field.setEnabled(bool(state)))
    check.stateChanged.connect(slot)
    form.addRow(check, field)
    return check


def _wrap(layout) -> QWidget:
    holder = QWidget()
    holder.setLayout(layout)
    holder.setContentsMargins(0, 0, 0, 0)
    return holder
