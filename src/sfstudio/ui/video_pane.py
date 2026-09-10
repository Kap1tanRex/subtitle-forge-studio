"""Видеопанель: переключатель между режимом с видео и без.

Внутри два взаимоисключающих виджета и **один** общий
:class:`~sfstudio.ui.overlay.OverlayController`:

* :class:`~sfstudio.ui.preview.PreviewCanvas` — шахматный фон, когда видео нет;
* :class:`~sfstudio.ui.video_widget.VideoWidget` — кадр mpv, когда видео есть.

Контроллер один, а хост у него меняется. Иначе выделение, текущее время и
состояние жеста существовали бы в двух копиях и расходились при переключении.

Наложить оверлей на видео отдельным виджетом нельзя: перекрытый
``QOpenGLWidget`` в Qt 6 перестаёт отдавать кадр (проверено — видео чернеет),
поэтому в режиме с видео оверлей рисуется внутри ``paintGL``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QStackedLayout, QWidget

from sfstudio.app.i18n import tr
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.preview import PreviewCanvas

__all__ = ["VideoPane"]


class VideoPane(QWidget):
    """Кадр (видео или шахматка) с интерактивным оверлеем субтитров."""

    selection_changed = Signal(int)
    document_edited = Signal()
    status_message = Signal(str)
    position_changed = Signal(int)
    duration_changed = Signal(int)
    #: True — на паузе. Панель транспорта не может опрашивать плеер по таймеру:
    #: паузу ставит и сам mpv (конец файла, буферизация), и об этом надо узнать.
    pause_changed = Signal(bool)
    speed_changed = Signal(float)
    #: (eid, глобальная точка) — правый щелчок по реплике в кадре.
    context_requested = Signal(int, object)

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._player = None
        self._video: QWidget | None = None
        self._has_video = False

        self.canvas = PreviewCanvas(doc, undo, parent=self)
        self.overlay = self.canvas.overlay
        self._connect(self.canvas)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.canvas)
        self.setMinimumSize(160, 90)

    def _connect(self, widget: QWidget) -> None:
        widget.selection_changed.connect(self.selection_changed)
        widget.document_edited.connect(self.document_edited)
        widget.status_message.connect(self.status_message)
        widget.context_requested.connect(self.context_requested)

    # -- подключение видео ------------------------------------------------------- #

    def attach_player(self) -> str:
        """Создаёт плеер и видеовиджет. Возвращает пояснение о результате."""
        if self._video is not None:
            return tr('плеер уже подключён')

        from sfstudio.media.player import MpvPlayer, MpvUnavailableError, mpv_available

        if not mpv_available():
            return tr('libmpv недоступна — работаем без видео')

        try:
            self._player = MpvPlayer()
        except MpvUnavailableError as exc:
            return str(exc)

        from sfstudio.ui.video_widget import VideoWidget

        self._video = VideoWidget(
            self._player,
            self.overlay.document,
            self.overlay.undo,
            parent=self,
            controller=self.overlay,
        )
        self._connect(self._video)
        self._video.render_failed.connect(self._on_render_failed)
        self._stack.addWidget(self._video)

        # Колбэки приходят из потоков mpv; сигналы Qt доставят их в главный.
        self._player.on_position = self.position_changed.emit
        self._player.on_duration = self.duration_changed.emit
        self._player.on_pause = self.pause_changed.emit
        return tr('видео подключено')

    def load_media(self, path: Path) -> bool:
        """Открывает файл в плеере. ``False``, если видео недоступно.

        Порядок здесь принципиален. Сначала виджет выводится на экран, и лишь
        затем, следующим тиком цикла событий, файл отдаётся плееру.

        Причина: ``initializeGL`` (а с ним и создание render-контекста mpv)
        происходит только когда Qt действительно рисует виджет. Если файл
        загрузить раньше, mpv выбирает декодер, не имея GL-контекста, остаётся
        без аппаратного декодирования и больше не переинициализируется —
        кадр остаётся чёрным, хотя воспроизведение идёт и время бежит.
        """
        if self._player is None or self._video is None:
            return False
        self._has_video = True
        self._show_video(True)
        player, video = self._player, self._video
        QTimer.singleShot(0, lambda: self._deferred_load(player, video, path))
        return True

    def _deferred_load(self, player, video, path: Path) -> None:
        """Отдаёт файл плееру после того, как GL-контекст создан."""
        if player is not self._player or video is not self._video:
            return  # за это время открыли что-то другое
        player.load(path)

    def _show_video(self, on: bool) -> None:
        target = self._video if (on and self._video is not None) else self.canvas
        self._stack.setCurrentWidget(target)
        # Хост меняется вместе с виджетом: отрисовку и курсор запрашивает тот,
        # кто сейчас на экране.
        self.overlay.attach_host(target)
        target.setFocus()
        target.update()

    def _on_render_failed(self, message: str) -> None:
        """Сбой GL — возвращаемся к режиму без видео, а не роняем окно."""
        self._has_video = False
        self._show_video(False)
        self.status_message.emit(tr('Видео недоступно: {0}').format(message))

    # -- транспорт ---------------------------------------------------------------- #

    @property
    def player(self):
        return self._player

    @property
    def has_video(self) -> bool:
        return self._has_video

    def seek_ms(self, ms: int) -> None:
        """Перемотка. Безопасна и до того, как файл открылся.

        Плеер сам отложит запрос, если mpv ещё не готов, — окно не должно
        знать о таких тонкостях и тем более падать из-за них.
        """
        if self._player is not None and self._has_video:
            self._player.seek_ms(ms)
        self.overlay.set_time(ms)

    def toggle_pause(self) -> None:
        if self._player is not None and self._has_video:
            self._player.toggle_pause()

    def frame_step(self, forward: bool = True) -> None:
        if self._player is not None and self._has_video:
            self._player.frame_step(forward)

    def set_paused(self, paused: bool) -> None:
        if self._player is not None and self._has_video:
            self._player.paused = paused

    @property
    def is_paused(self) -> bool:
        """Без видео считаем, что на паузе: играть всё равно нечего."""
        if self._player is None or not self._has_video:
            return True
        return bool(self._player.paused)

    @property
    def speed(self) -> float:
        if self._player is None or not self._has_video:
            return 1.0
        return self._player.speed

    @property
    def volume(self) -> float:
        if self._player is None or not self._has_video:
            return 0.0
        return self._player.volume

    def set_volume(self, value: float) -> None:
        if self._player is not None and self._has_video:
            self._player.volume = value

    def set_muted(self, muted: bool) -> None:
        """Заглушение — свойство mpv, а не громкость нулём.

        Разница существенная: mute запоминает прежний уровень, и снятие
        возвращает именно его, а не то значение, которое было по умолчанию.
        """
        if self._player is not None and self._has_video:
            self._player.muted = muted

    def set_speed(self, value: float) -> None:
        """Скорость воспроизведения. Плеер сам зажмёт её в разумные пределы."""
        if self._player is None or not self._has_video:
            return
        self._player.speed = value
        # Сигнал шлём после записи: плеер мог зажать значение, и панель должна
        # показать то, что получилось, а не то, что просили.
        self.speed_changed.emit(self._player.speed)

    # -- прокси к оверлею ------------------------------------------------------------ #

    def set_time(self, ms: int) -> None:
        self.overlay.set_time(ms)

    def set_selected(self, eid: int | None) -> None:
        self.overlay.set_selected(eid)

    def set_document(self, doc: SubtitleDocument, undo: UndoStack) -> None:
        self.overlay.set_document(doc, undo)

    def clear_position(self) -> None:
        self.overlay.clear_position()

    def toggle_guides(self, on: bool) -> None:
        self.overlay.show_guides = on
        self._current().update()

    def toggle_safe_area(self, on: bool) -> None:
        self.overlay.show_safe_area = on
        self._current().update()

    @property
    def time_ms(self) -> int:
        return self.overlay.time_ms

    @property
    def backend(self) -> str:
        return self.overlay.backend

    def update(self) -> None:
        """Перерисовывает кадр, а не только пустую рамку вокруг него.

        Панель — контейнер: сам по себе она ничего не рисует, кадр рисует
        вложенный виджет. ``QWidget.update()`` помечает грязным только тот
        виджет, которому послан, а непрозрачного ребёнка не трогает — и
        обновление, пришедшее из главного окна, до кадра не доходило.

        Проявлялось это как правка, которой не видно: меняешь шрифт или цвет
        реплики, в таблице всё поменялось, а в кадре стоит прежнее. При
        воспроизведении картинка обновлялась сама и правка «появлялась» —
        отчего казалось, что дело в плеере.

        ``update`` в Qt не виртуальный, так что переопределение перехватывает
        вызовы из Python и не мешает внутренним вызовам самого Qt.
        """
        super().update()
        self._current().update()

    def _current(self) -> QWidget:
        return self._stack.currentWidget() or self.canvas

    def close_player(self) -> None:
        if self._video is not None:
            self._video.close()
            self._video = None
        if self._player is not None:
            self._player.close()
            self._player = None
        self._has_video = False
