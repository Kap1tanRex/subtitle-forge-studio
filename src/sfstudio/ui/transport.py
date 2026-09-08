"""Панель транспорта: полоса положения, воспроизведение, скорость.

Состояние панель **не хранит** — она отражает то, что ей сообщают. Причина
простая: паузу ставит не только пользователь. Её ставит и сам mpv — на конце
файла, при потере устройства вывода, при буферизации. Панель с собственным
флагом «играет» в этих случаях врёт, а кнопка перестаёт соответствовать тому,
что происходит на экране.

То же и с положением. Время меняют трое: плеер во время воспроизведения,
таймлайн при перемотке и таблица при выборе реплики. Панель одинаково
принимает его от всех и никого из них не выделяет, поэтому полоса под кадром
всегда показывает то же время, что курсор на таймлайне.

Скорость задаётся набором готовых значений, а не свободным полем. При правке
субтитров скорость нужна для двух вещей: замедлить, чтобы попасть в реплику, и
ускорить, чтобы проскочить длинный кусок. Обе решаются пресетами, а поле ввода
только заставляет целиться мышью.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sfstudio.core.time import format_srt
from sfstudio.ui.combo import index_of_data
from sfstudio.ui.icons import make_icon
from sfstudio.ui.seekbar import SeekBar
from sfstudio.ui.theme import DARK, Palette

__all__ = ["SPEED_PRESETS", "TransportBar"]

#: Готовые скорости. 0.25 — разбор быстрой речи по слогам, 4 — проскочить
#: паузу между сценами; между ними значения, которыми реально пользуются.
SPEED_PRESETS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 4.0)

BUTTON_SIZE = 30


def _format_speed(value: float) -> str:
    return f"{value:g}×"


class TransportBar(QWidget):
    """Полоса положения и кнопки воспроизведения."""

    play_pause = Signal()
    step_frame = Signal(bool)      # True — вперёд
    seek_edge = Signal(bool)       # True — в конец
    seek_requested = Signal(int)   # перемотка с полосы
    speed_selected = Signal(float)
    loop_toggled = Signal(bool)
    volume_changed = Signal(float)
    mute_toggled = Signal(bool)

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        # Пока идёт программное обновление, сигналы виджетов игнорируются:
        # иначе выставление скорости из плеера тут же отправило бы её обратно.
        self._syncing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 4, 10, 6)
        outer.setSpacing(2)

        self.seek = SeekBar(palette)
        self.seek.seek_requested.connect(self.seek_requested)
        outer.addWidget(self.seek)

        row = QHBoxLayout()
        row.setSpacing(3)

        self.btn_start = self._button("start", "В начало",
                                      lambda: self.seek_edge.emit(False))
        self.btn_prev = self._button("prev_frame", "Кадр назад (←)",
                                     lambda: self.step_frame.emit(False))
        self.btn_play = self._button("play", "Играть / Пауза (Пробел)",
                                     self.play_pause.emit)
        self.btn_next = self._button("next_frame", "Кадр вперёд (→)",
                                     lambda: self.step_frame.emit(True))
        self.btn_end = self._button("end", "В конец", lambda: self.seek_edge.emit(True))

        for button in (self.btn_start, self.btn_prev, self.btn_play,
                       self.btn_next, self.btn_end):
            row.addWidget(button)

        row.addSpacing(6)
        self.btn_loop = self._button("loop", "Зациклить текущую реплику", None)
        self.btn_loop.setCheckable(True)
        self.btn_loop.toggled.connect(self._on_loop)
        row.addWidget(self.btn_loop)

        row.addSpacing(12)

        self.time_label = QLabel("00:00:00,000")
        self.time_label.setProperty("role", "timecode")
        self.time_label.setToolTip("Текущее положение")
        row.addWidget(self.time_label)

        self.total_label = QLabel("/ 00:00:00,000")
        self.total_label.setProperty("role", "hint")
        self.total_label.setToolTip("Длительность")
        row.addWidget(self.total_label)

        row.addStretch(1)

        # -- громкость ---------------------------------------------------- #
        self.btn_mute = self._button("volume", "Заглушить (M)", None)
        self.btn_mute.setCheckable(True)
        self.btn_mute.toggled.connect(self._on_mute)
        row.addWidget(self.btn_mute)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(96)
        self.volume_slider.setToolTip("Громкость")
        self.volume_slider.valueChanged.connect(self._on_volume)
        row.addWidget(self.volume_slider)

        self.volume_label = QLabel("80 %")
        self.volume_label.setProperty("role", "hint")
        self.volume_label.setFixedWidth(38)
        row.addWidget(self.volume_label)

        row.addSpacing(10)

        speed_caption = QLabel("Скорость")
        speed_caption.setProperty("role", "hint")
        row.addWidget(speed_caption)

        self.speed_box = QComboBox()
        for value in SPEED_PRESETS:
            self.speed_box.addItem(_format_speed(value), value)
        self.speed_box.setCurrentIndex(SPEED_PRESETS.index(1.0))
        self.speed_box.setToolTip("Скорость воспроизведения ([ и ])")
        self.speed_box.currentIndexChanged.connect(self._on_speed)
        self.speed_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row.addWidget(self.speed_box)

        outer.addLayout(row)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.set_enabled_transport(False)


    def set_palette(self, palette: Palette) -> None:
        """Меняет тему на лету — см. :meth:`TimelineWidget.set_palette`."""
        self._palette = palette
        self.update()

    def _button(self, icon: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setIcon(make_icon(icon, self._palette.text_primary))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoRaise(True)
        if slot is not None:
            button.clicked.connect(slot)
        return button

    # -- приём состояния ------------------------------------------------------------ #

    def set_paused(self, paused: bool) -> None:
        """Обновляет вид кнопки. Ничего не переключает — только отражает."""
        self.btn_play.setIcon(
            make_icon("play" if paused else "pause", self._palette.text_primary)
        )
        self.btn_play.setToolTip("Играть (Пробел)" if paused else "Пауза (Пробел)")

    def set_position(self, ms: int) -> None:
        """Положение. Приходит и от плеера, и от таймлайна — источник неважен."""
        self.seek.set_position(ms)
        self.time_label.setText(format_srt(max(0, ms)))

    def set_duration(self, ms: int) -> None:
        self.seek.set_duration(ms)
        self.total_label.setText("/ " + format_srt(max(0, ms)))

    def set_marks(self, marks) -> None:
        """Отметки реплик на полосе."""
        self.seek.set_marks(marks)

    def set_volume(self, value: float) -> None:
        """Показывает громкость плеера, не отправляя её обратно."""
        self._syncing = True
        try:
            self.volume_slider.setValue(round(max(0.0, min(100.0, value))))
            self.volume_label.setText(f"{self.volume_slider.value()} %")
            # Ползунок на нуле и есть тишина: отдельная кнопка при этом
            # должна выглядеть нажатой, иначе состояние показано дважды и
            # по-разному.
            self.btn_mute.setChecked(self.volume_slider.value() == 0)
            self._paint_mute_icon()
        finally:
            self._syncing = False

    def volume(self) -> float:
        return float(self.volume_slider.value())

    def _on_volume(self, value: int) -> None:
        self.volume_label.setText(f"{value} %")
        if self._syncing:
            return
        self.btn_mute.setChecked(value == 0)
        self._paint_mute_icon()
        self.volume_changed.emit(float(value))

    def _on_mute(self, muted: bool) -> None:
        self._paint_mute_icon()
        if not self._syncing:
            self.mute_toggled.emit(muted)

    def _paint_mute_icon(self) -> None:
        name = "mute" if self.btn_mute.isChecked() else "volume"
        self.btn_mute.setIcon(make_icon(name, self._palette.text_primary))
        self.btn_mute.setToolTip(
            "Включить звук (M)" if self.btn_mute.isChecked() else "Заглушить (M)"
        )

    def set_speed(self, value: float) -> None:
        """Показывает скорость плеера.

        Значение может не совпасть ни с одним пресетом — например, плеер
        зажал его по своим границам. Тогда пресет добавляется в список, а не
        подменяется ближайшим: показывать 2×, когда идёт 1.8×, нельзя.
        """
        self._syncing = True
        try:
            index = index_of_data(self.speed_box, value)
            if index < 0:
                self.speed_box.addItem(_format_speed(value), value)
                index = self.speed_box.count() - 1
            self.speed_box.setCurrentIndex(index)
        finally:
            self._syncing = False

    def set_enabled_transport(self, on: bool) -> None:
        """Без видео кнопки не работают — гасим, а не врём.

        Полоса при этом остаётся живой: время идёт и без картинки, по звуковой
        дорожке или просто по документу, и перемотка по ней осмысленна.
        """
        for widget in (self.btn_start, self.btn_prev, self.btn_play, self.btn_next,
                       self.btn_end, self.btn_loop, self.speed_box,
                       self.btn_mute, self.volume_slider):
            widget.setEnabled(on)
        if not on:
            self.set_paused(True)

    def set_looping(self, on: bool) -> None:
        self._syncing = True
        try:
            self.btn_loop.setChecked(on)
        finally:
            self._syncing = False

    # -- обработчики ------------------------------------------------------------- #

    def _on_speed(self, index: int) -> None:
        if self._syncing or index < 0:
            return
        value = self.speed_box.itemData(index)
        if value is not None:
            self.speed_selected.emit(float(value))

    def _on_loop(self, on: bool) -> None:
        if not self._syncing:
            self.loop_toggled.emit(on)

    # -- шаг скорости с клавиатуры ------------------------------------------------- #

    def step_speed(self, faster: bool) -> float:
        """Соседний пресет скорости. Возвращает выбранное значение."""
        values = [self.speed_box.itemData(i) for i in range(self.speed_box.count())]
        values = sorted(float(v) for v in values if v is not None)
        current = float(self.speed_box.currentData() or 1.0)
        if faster:
            nxt = next((v for v in values if v > current), values[-1])
        else:
            nxt = next((v for v in reversed(values) if v < current), values[0])
        self.speed_selected.emit(nxt)
        return nxt
