"""Разговор после открытия видео: что делать с расхождением частоты кадров.

Открыв видео, программа знает про него больше человека: частоту кадров,
разрешение, длительность. Молчать об этом нельзя — субтитры, сделанные под
другую частоту, к концу серии уезжают на секунды, а понять причину по одному
лишь «отстают» тяжело. Но и чинить молча нельзя тем более: пересчёт трогает
все реплики, а человек мог открыть видео просто чтобы посмотреть.

Поэтому окно показывает, что известно, называет величину расхождения и
предлагает три ответа — оставить как есть, только запомнить частоту, или
пересчитать. По умолчанию выбран самый безобидный.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.services.fps_sync import FpsConversion, describe_rate

__all__ = ["FpsOfferChoice", "FpsOfferDialog"]


@dataclass(frozen=True, slots=True)
class FpsOfferChoice:
    """Что человек ответил."""

    #: Записать частоту видео в проект.
    adopt: bool = False
    #: Пересчитать тайминги под новую частоту.
    rescale: bool = False

    @property
    def is_nothing(self) -> bool:
        return not self.adopt and not self.rescale


class FpsOfferDialog(QDialog):
    """«Видео идёт с другой частотой — что делаем?»"""

    def __init__(
        self,
        *,
        media_name: str,
        video_fps: Fraction,
        project_fps: Fraction,
        span_ms: int,
        events: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Частота кадров не совпадает'))
        self.setMinimumWidth(560)

        conversion = FpsConversion(project_fps, video_fps)

        root = QVBoxLayout(self)

        head = QLabel(tr('<b>{0}</b><br>Видео: {1}. Проект: {2}.').format(
            media_name, describe_rate(video_fps), describe_rate(project_fps)
        ))
        head.setWordWrap(True)
        root.addWidget(head)

        # Числом, а не общими словами: «к концу 7 секунд» человек сверит со
        # своими ощущениями, а «×1.001» — нет.
        drift = QLabel(tr('Если субтитры делали под {0}, то {1}.').format(
            describe_rate(project_fps), conversion.describe_drift(span_ms)
        ))
        drift.setWordWrap(True)
        drift.setProperty("role", "warning" if not conversion.is_noop else "hint")
        root.addWidget(drift)

        self.nothing = QRadioButton(tr('Ничего не менять'))
        self.adopt = QRadioButton(
            tr('Запомнить частоту видео в проекте (тайминги не трогать)')
        )
        self.rescale = QRadioButton(
            tr('Запомнить и пересчитать тайминги всех {0} реплик').format(events)
        )
        # По умолчанию — самое безобидное: пересчёт трогает весь документ, и
        # выбирать его за человека нельзя.
        self.nothing.setChecked(True)
        for button in (self.nothing, self.adopt, self.rescale):
            root.addWidget(button)
        self.rescale.setEnabled(events > 0 and not conversion.is_noop)

        hint = QLabel(tr('Пересчёт всегда можно отменить, а позже повторить '
                         'через «Тайминг → Коррекция под частоту кадров».'))
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        root.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText(tr('Готово'))
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def choice(self) -> FpsOfferChoice:
        if self.rescale.isChecked():
            return FpsOfferChoice(adopt=True, rescale=True)
        if self.adopt.isChecked():
            return FpsOfferChoice(adopt=True)
        return FpsOfferChoice()
