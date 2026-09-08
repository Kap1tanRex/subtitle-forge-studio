"""Видеовиджет: кадр mpv и оверлей субтитров в одном GL-контексте.

mpv рисует кадр в FBO нашего ``QOpenGLWidget``, а сразу после этого мы рисуем
поверх него субтитры и манипуляторы обычным ``QPainter``.

**Почему оверлей рисуется здесь, а не отдельным виджетом сверху.** Перекрытый
``QOpenGLWidget`` в Qt 6 перестаёт отдавать кадр: композиция уходит на путь
с текстурой, mpv в неё не попадает, и видео получается чёрным. Проверено —
именно так и вышло при первой попытке. Рисование ``QPainter``-ом внутри
``paintGL`` — штатный и единственный надёжный способ смешать GL и Qt.

**Про потоки.** ``update_cb`` mpv дёргает из своего потока рендера. Трогать
оттуда Qt нельзя ничем, даже ``update()``. Колбэк только испускает сигнал,
а перерисовку делает главный поток через очередь событий.

**Про время жизни.** Ссылки на ctypes-колбэки хранятся в атрибутах: если их
потерять, сборщик мусора уничтожит объекты, а mpv продолжит вызывать
освобождённую память.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QOpenGLContext, QPainter, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.media.player import MpvPlayer
from sfstudio.ui.overlay import OverlayController

__all__ = ["VideoWidget", "configure_surface_format"]


def configure_surface_format() -> None:
    """Задаёт формат GL по умолчанию.

    Вызывать **до** создания ``QApplication``: формат подхватывается при
    создании первого контекста, позже менять уже поздно.
    """
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(0)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)


class VideoWidget(QOpenGLWidget):
    """Кадр видео с интерактивным оверлеем субтитров."""

    _frame_ready = Signal()

    render_failed = Signal(str)
    selection_changed = Signal(int)
    document_edited = Signal()
    status_message = Signal(str)
    #: (eid, глобальная точка) — запрос панели оформления.
    context_requested = Signal(int, object)

    def __init__(
        self,
        player: MpvPlayer,
        doc: SubtitleDocument,
        undo: UndoStack,
        parent=None,
        controller: OverlayController | None = None,
    ) -> None:
        super().__init__(parent)
        self._player = player
        self._ctx = None
        self._proc_address_fn = None  # держим ссылку: см. docstring модуля
        self._update_fn = None
        self._failed = False

        if controller is None:
            self.overlay = OverlayController(doc, undo, host=self)
        else:
            self.overlay = controller
            controller.attach_host(self)

        self._frame_ready.connect(self.update, Qt.QueuedConnection)
        self.setMinimumSize(160, 90)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- контракт OverlayHost ---------------------------------------------------- #

    def request_update(self) -> None:
        self.update()

    def set_cursor_shape(self, shape: Qt.CursorShape) -> None:
        self.setCursor(shape)

    def notify_selection(self, eid: int) -> None:
        self.selection_changed.emit(eid)

    def notify_edited(self) -> None:
        self.document_edited.emit()

    def notify_status(self, text: str) -> None:
        self.status_message.emit(text)

    def overlay_size(self) -> tuple[int, int]:
        return (self.width(), self.height())

    # -- жизненный цикл GL --------------------------------------------------------- #

    def initializeGL(self) -> None:  # noqa: N802 (Qt API)
        try:
            import mpv
        except (ImportError, OSError) as exc:
            self._fail(f"python-mpv недоступен: {exc}")
            return

        proc = mpv.MpvGlGetProcAddressFn(_make_proc_address())
        self._proc_address_fn = proc

        try:
            self._ctx = mpv.MpvRenderContext(
                self._player.raw,
                "opengl",
                opengl_init_params={"get_proc_address": proc},
            )
        except Exception as exc:
            self._fail(f"не удалось создать render-контекст mpv: {exc}")
            return

        self._update_fn = self._frame_ready.emit
        self._ctx.update_cb = self._update_fn

    def paintGL(self) -> None:  # noqa: N802
        """Кадр mpv, затем оверлей субтитров поверх него.

        Порядок и обрамление принципиальны. mpv рисует «сырым» OpenGL, а
        оверлей — средствами ``QPainter``, и у них несовместимые ожидания о
        состоянии контекста. Смешивать их без ``beginNativePainting()`` /
        ``endNativePainting()`` нельзя: Qt не знает, что состояние GL ему
        больше не принадлежит, и результат — либо чёрный кадр, либо падение
        процесса внутри драйвера без единой строки в логе. Проверено на обоих
        исходах, прежде чем появилось это обрамление.
        """
        ratio = self.devicePixelRatioF()
        width = max(1, int(self.width() * ratio))
        height = max(1, int(self.height() * ratio))

        painter = QPainter(self)
        try:
            if self._ctx is not None:
                painter.beginNativePainting()
                try:
                    self._ctx.render(
                        flip_y=True,
                        opengl_fbo={
                            "fbo": int(self.defaultFramebufferObject()),
                            "w": width,
                            "h": height,
                        },
                    )
                except Exception as exc:
                    self._fail(f"сбой рендера кадра: {exc}")
                finally:
                    painter.endNativePainting()

            self.overlay.paint(painter)
        finally:
            painter.end()

    # -- мышь и клавиатура ------------------------------------------------------------ #

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        self.overlay.on_press(pos.x(), pos.y(), event.button())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        self.overlay.on_move(pos.x(), pos.y(), event.modifiers())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.overlay.on_release(event.button())

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """Правый щелчок по реплике — панель быстрого оформления.

        Само меню собирает окно: виджет кадра знает только, по чему щёлкнули.
        """
        pos = event.pos()
        eid = self.overlay.eid_at(float(pos.x()), float(pos.y()))
        if eid is None:
            return
        self.overlay.set_selected(eid)
        self.selection_changed.emit(eid)
        self.context_requested.emit(eid, event.globalPos())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self.overlay.on_key(event.key(), event.modifiers()):
            super().keyPressEvent(event)

    # -- завершение --------------------------------------------------------------------- #

    def closeEvent(self, event) -> None:  # noqa: N802
        self._release()
        super().closeEvent(event)

    def _release(self) -> None:
        """Освобождает render-контекст mpv.

        Два требования, нарушение любого роняет процесс без сообщения:

        1. **Сначала снять колбэк**, иначе mpv дёрнет его уже после
           освобождения контекста.
        2. **GL-контекст должен быть текущим.** ``mpv_render_context_free``
           удаляет созданные им GL-объекты, и без ``makeCurrent`` это уходит
           в чужой (или отсутствующий) контекст.

        Отдельно важен порядок снаружи: контекст обязан быть освобождён
        **до** завершения самого плеера. Обратный порядок оставляет контекст
        со ссылкой на уничтоженный ``mpv_handle``.
        """
        if self._ctx is not None:
            with contextlib.suppress(Exception):
                self._ctx.update_cb = None
            with contextlib.suppress(Exception):
                self.makeCurrent()
            with contextlib.suppress(Exception):
                self._ctx.free()
            with contextlib.suppress(Exception):
                self.doneCurrent()
            self._ctx = None
        self._proc_address_fn = None
        self._update_fn = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._release()

    @property
    def ready(self) -> bool:
        return self._ctx is not None and not self._failed

    @property
    def failed(self) -> bool:
        return self._failed

    def _fail(self, message: str) -> None:
        if not self._failed:
            self._failed = True
            self.render_failed.emit(message)


def _make_proc_address() -> Callable[[object, bytes], int]:
    """Адресация функций OpenGL через текущий контекст Qt.

    mpv спрашивает адреса функций GL по имени; отдаёт их Qt, потому что именно
    он владеет контекстом. Ноль в ответ — легальное «нет такой функции»,
    mpv это обрабатывает сам.
    """

    def get_proc_address(_ctx: object, name: bytes) -> int:
        gl = QOpenGLContext.currentContext()
        if gl is None:
            return 0
        address = gl.getProcAddress(name)
        if address is None:
            return 0
        try:
            return int(address)
        except TypeError:
            # В некоторых сборках PySide возвращается VoidPtr, а не int.
            return int(getattr(address, "toTuple", lambda: (0,))()[0] or 0)

    return get_proc_address
