"""Коррекция таймингов под другую частоту кадров.

Тот случай, когда субтитры «сползают»: в начале серии совпадают, к середине
отстают на секунду, к финалу — на десять. Сдвигом это не лечится, потому что
ошибка не постоянная, а растёт вместе со временем; лечится растяжением всей
дорожки. Диалог показывает и множитель, и — что важнее — на сколько уедет
последняя реплика: множитель 1.001 ни о чём не говорит, а «к концу 7 секунд»
говорит всё.

Предпросмотр обязателен по той же причине, что и в остальных диалогах правки
таймингов: массовую правку времени на глаз не оценить.
"""

from __future__ import annotations

from fractions import Fraction

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.time import FpsModel
from sfstudio.services.fps_sync import (
    COMMON_RATES,
    FpsConversion,
    describe_rate,
)
from sfstudio.ui.timing_dialog import PREVIEW_ROWS, PreviewTree

__all__ = ["FpsSyncDialog"]


class FpsSyncDialog(QDialog):
    """Выбор пары частот и предпросмотр пересчёта."""

    def __init__(
        self,
        doc: SubtitleDocument,
        selection: list[SubtitleEvent],
        *,
        video_fps: FpsModel | Fraction | None = None,
        source_fps: FpsModel | Fraction | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Коррекция под частоту кадров'))
        self.resize(820, 560)
        self._doc = doc
        self._selection = selection
        self._ready = False

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.source_box = _rate_box(source_fps, fallback=Fraction(25))
        self.source_box.currentIndexChanged.connect(self._recalculate)
        form.addRow(tr('Субтитры сделаны для:'), self.source_box)

        # Частота видео известна из метаданных — её и подставляем: именно к
        # этому видео субтитры и прикладывают.
        self.target_box = _rate_box(video_fps, fallback=Fraction(24000, 1001))
        self.target_box.currentIndexChanged.connect(self._recalculate)
        form.addRow(tr('Видео идёт с частотой:'), self.target_box)

        self.scope = QComboBox()
        self.scope.addItem(tr('все ({0})').format(len(doc)), "all")
        self.scope.addItem(tr('выделенные ({0})').format(len(selection)), "selection")
        self.scope.currentIndexChanged.connect(self._recalculate)
        form.addRow(tr('Что пересчитать:'), self.scope)
        root.addLayout(form)

        # Главная строка окна: множитель непонятен, а «к концу 7 секунд» —
        # ровно то, по чему человек и узнаёт свою беду.
        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        root.addWidget(self.verdict)

        root.addWidget(QLabel(tr('Предпросмотр:')))
        self.preview = PreviewTree()
        root.addWidget(self.preview, 1)

        self.summary = QLabel("")
        self.summary.setProperty("role", "hint")
        root.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText(tr('Пересчитать'))
        buttons.button(QDialogButtonBox.Cancel).setText(tr('Отмена'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        root.addWidget(buttons)

        self._mapping: dict[int, tuple[int, int]] = {}
        self._ready = True
        self._recalculate()

    # -- расчёт ------------------------------------------------------------------- #

    def conversion(self) -> FpsConversion:
        return FpsConversion(self.source_box.currentData(), self.target_box.currentData())

    def _targets(self) -> list[SubtitleEvent]:
        if self.scope.currentData() == "selection":
            return list(self._selection)
        return list(self._doc.events)

    def _recalculate(self, *_args) -> None:
        if not self._ready:
            return

        conversion = self.conversion()
        targets = self._targets()
        self._mapping = {}

        for event in targets:
            start = conversion.map_ms(event.start)
            end = max(conversion.map_ms(event.end), start + 1)
            if (start, end) != (event.start, event.end):
                self._mapping[event.eid] = (start, end)

        span = max((event.end for event in targets), default=0)
        self.verdict.setText(self._verdict_text(conversion, span))
        self.preview.show_plan(_plan(self._doc, self._mapping), self._doc)

        text = tr('затронуто реплик: {0}').format(len(self._mapping))
        if len(self._mapping) > PREVIEW_ROWS:
            text += tr(' · показаны первые {0}').format(PREVIEW_ROWS)
        self.summary.setText(text)
        # Пересчитывать нечего — кнопка выключена: нажатие, которое ничего не
        # делает, заставляет гадать, сработало ли оно.
        self._ok.setEnabled(bool(self._mapping))

    def _verdict_text(self, conversion: FpsConversion, span_ms: int) -> str:
        if conversion.is_noop:
            return tr('Частоты совпадают — пересчитывать нечего.')
        parts = [
            tr('Время умножается на {0:.6g}.').format(conversion.scale),
            conversion.describe_drift(span_ms).capitalize() + ".",
        ]
        if not conversion.same_family:
            # 25 против 29.97 — это не «замедленная копия», а другой источник;
            # пересчёт сработает, но, скорее всего, выбран не тот файл.
            parts.append(
                tr('Эти частоты не родственны: проверьте, то ли видео открыто.')
            )
        return " ".join(parts)

    # -- наружу ------------------------------------------------------------------- #

    def mapping(self) -> dict[int, tuple[int, int]]:
        return self._mapping

    def describe(self) -> str:
        return tr('Коррекция {0}').format(self.conversion().describe())


def _rate_box(preset: FpsModel | Fraction | None, *, fallback: Fraction) -> QComboBox:
    """Список частот; выбранной становится ``preset``, если она известна.

    Частота видео может не совпасть ни с одной из готовых — тогда она
    добавляется отдельным пунктом, а не подменяется ближайшей: подменять
    значение, от которого зависит весь пересчёт, нельзя.
    """
    box = QComboBox()
    chosen = preset.rate if isinstance(preset, FpsModel) else preset
    rates = list(COMMON_RATES)
    if chosen is not None and chosen not in rates:
        rates.append(Fraction(chosen))
        rates.sort()

    for rate in rates:
        box.addItem(describe_rate(rate), rate)

    wanted = chosen if chosen is not None else fallback
    for index in range(box.count()):
        if box.itemData(index) == wanted:
            box.setCurrentIndex(index)
            break
    return box


def _plan(doc: SubtitleDocument, mapping: dict[int, tuple[int, int]]):
    from sfstudio.services.autotiming import TimingChange, TimingPlan

    changes = []
    for eid, (start, end) in mapping.items():
        event = doc.by_eid(eid)
        changes.append(TimingChange(eid, event.start, event.end, start, end))
    return TimingPlan(changes=changes)
