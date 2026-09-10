"""Воспроизведение через libmpv.

Обёртка над ``python-mpv``: управление, свойства и события. Вывод картинки
живёт отдельно, в :mod:`sfstudio.ui.video_widget` — так логика плеера
тестируется без окна и без OpenGL.

Почему libmpv, а не Qt Multimedia или ручной цикл на PyAV: нужны аппаратное
декодирование, точный seek и покадровый шаг. Ручной цикл на Python упирается
в GIL уже на 1080p, а Qt Multimedia по-разному ведёт себя на разных бэкендах.

Субтитры mpv не рисует (``sub-visibility=no``): их рисует наш оверлей через
libass, иначе на экране оказались бы две копии — сохранённая и редактируемая.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from sfstudio.core.time import FpsModel
from sfstudio.platform.native import libmpv_spec

__all__ = ["MpvPlayer", "MpvUnavailableError", "VideoParams", "mpv_available"]


class MpvUnavailableError(RuntimeError):
    """libmpv или python-mpv недоступны."""


@dataclass(frozen=True, slots=True)
class VideoParams:
    width: int = 0
    height: int = 0
    fps: FpsModel | None = None
    rotation: int = 0
    aspect: float = 16 / 9


def _prepare_path() -> Path | None:
    """Добавляет каталог вендоринга в пути поиска DLL.

    ``python-mpv`` ищет libmpv в системном PATH и падает с ``OSError`` ещё на
    импорте, если не нашёл. Поэтому каталог добавляется **до** импорта, а не
    после — иначе получить понятную ошибку уже не выйдет.
    """
    spec = libmpv_spec()
    path = spec.locate()
    if path is None:
        return None
    directory = str(path.parent)
    if os.name == "nt":
        os.add_dll_directory(directory)
    os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
    return path


def mpv_available() -> bool:
    """Можно ли вообще создать плеер."""
    if libmpv_spec().locate() is None:
        return False
    try:
        _prepare_path()
        import mpv  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


class MpvPlayer:
    """Плеер. Не наследует QObject — Qt-обвязка снаружи.

    События приходят колбэками, которые вызывающий код обязан переправить
    в главный поток (mpv дёргает их из своих потоков).
    """

    __slots__ = (
        "_duration_ms",
        "_mpv",
        "_observers",
        "_params",
        "_path",
        "_pending_seek_ms",
        "_position_ms",
        "_video_output",
        "on_duration",
        "on_eof",
        "on_pause",
        "on_position",
        "on_video_params",
    )

    def __init__(self, *, video_output: str = "libmpv", **extra: object) -> None:
        """``video_output='null'`` — режим без картинки, для тестов и разбора."""
        path = _prepare_path()
        if path is None:
            raise MpvUnavailableError(
                "libmpv не найдена. Разложите её: python build/vendor_libs.py --check"
            )
        try:
            import mpv
        except ImportError as exc:
            raise MpvUnavailableError(f"нет пакета python-mpv: {exc}") from exc
        except OSError as exc:
            raise MpvUnavailableError(f"libmpv не загрузилась: {exc}") from exc

        options: dict[str, object] = {
            "vo": video_output,
            "hwdec": "auto-safe",
            "hr_seek": "yes",
            "keep_open": "always",
            # Субтитры рисует наш оверлей — иначе на экране две копии.
            "sub_visibility": "no",
            "osc": "no",
            "input_default_bindings": "no",
            "input_vo_keyboard": "no",
            "audio_display": "no",
            "idle": "yes",
            "terminal": "no",
            # Полная изоляция от пользовательской установки mpv. Иначе чужой
            # mpv.conf молча переопределяет наши настройки (тот же
            # sub-visibility — и субтитры задваиваются), а автозагруженный
            # скрипт может сам перематывать видео под редактором.
            "config": "no",
            "load_scripts": "no",
            "ytdl": "no",
        }
        options.update(extra)

        self._mpv = mpv.MPV(**options)
        self._video_output = str(options["vo"])
        self._path: Path | None = None
        #: Перемотка, запрошенная до готовности файла. Применится, когда mpv
        #: сообщит длительность — то есть когда файл действительно открыт.
        self._pending_seek_ms: int | None = None
        self._duration_ms = 0
        self._position_ms = 0
        self._params = VideoParams()

        self.on_position: Callable[[int], None] | None = None
        self.on_duration: Callable[[int], None] | None = None
        self.on_pause: Callable[[bool], None] | None = None
        self.on_video_params: Callable[[VideoParams], None] | None = None
        self.on_eof: Callable[[], None] | None = None

        #: Наблюдатели свойств. Их надо уметь снять до завершения mpv:
        #: событие, пришедшее в фоновом потоке уже после разрушения, роняет
        #: процесс целиком — access violation внутри libmpv.
        self._observers: list = []
        self._observe()

    # -- наблюдение за свойствами ---------------------------------------------- #

    def _observe(self) -> None:
        @self._mpv.property_observer("time-pos")
        def _time(_name: str, value: object) -> None:
            if value is None:
                return
            self._position_ms = int(float(value) * 1000)
            if self.on_position:
                self.on_position(self._position_ms)

        @self._mpv.property_observer("duration")
        def _duration(_name: str, value: object) -> None:
            if value is None:
                return
            self._duration_ms = int(float(value) * 1000)
            # Файл открыт — самое время выполнить перемотку, о которой
            # просили, пока он ещё грузился.
            self._flush_pending_seek()
            if self.on_duration:
                self.on_duration(self._duration_ms)

        @self._mpv.property_observer("pause")
        def _pause(_name: str, value: object) -> None:
            if value is not None and self.on_pause:
                self.on_pause(bool(value))

        @self._mpv.property_observer("video-params")
        def _params(_name: str, value: object) -> None:
            if not isinstance(value, dict):
                return
            self._params = _read_params(value, self._mpv)
            if self.on_video_params:
                self.on_video_params(self._params)

        self._observers = [_time, _duration, _pause, _params]

    # -- файлы ------------------------------------------------------------------ #

    def load(self, path: Path) -> None:
        self._path = path
        # Длительность нового файла ещё неизвестна: обнуляем, иначе is_ready
        # соврёт по остатку от предыдущего.
        self._duration_ms = 0
        self._mpv.play(str(path))

    def stop(self) -> None:
        self._mpv.command("stop")
        self._path = None
        self._duration_ms = 0
        self._pending_seek_ms = None

    def wait_until_loaded(self, timeout: float = 10.0) -> bool:
        """Ждёт готовности файла. **Только вне GUI.**

        В режиме ``vo=libmpv`` вызов приводит к взаимной блокировке: mpv ждёт,
        что главный поток отрисует кадр, а главный поток стоит здесь. Внешне
        это выглядит как молчаливое зависание процесса без единой строки в
        логе, и ищется такое долго — поэтому вместо ожидания сразу ошибка.

        В GUI нужный сценарий другой: подписаться на ``on_duration`` и
        продолжить работу, не блокируя цикл событий.
        """
        if self._video_output == "libmpv":
            raise RuntimeError(
                "wait_until_loaded() нельзя вызывать при vo=libmpv: главный поток "
                "обязан обслуживать рендер, иначе процесс встанет намертво. "
                "Используйте колбэк on_duration."
            )
        try:
            self._mpv.wait_until_playing(timeout=timeout)
        except Exception:
            return False
        return True

    # -- транспорт --------------------------------------------------------------- #

    @property
    def paused(self) -> bool:
        return bool(self._mpv.pause)

    @paused.setter
    def paused(self, value: bool) -> None:
        self._mpv.pause = bool(value)

    def toggle_pause(self) -> None:
        self._mpv.pause = not self._mpv.pause

    def seek_ms(self, ms: int) -> None:
        """Точная перемотка.

        ``exact`` обязателен: иначе mpv прыгает к ближайшему ключевому кадру,
        и правка таймингов становится неточной.

        Пока файл не открыт, перематывать нечего, и mpv отвечает на команду
        ошибкой. Раньше она поднималась наружу и роняла программу при открытии
        проекта: состояние работы восстанавливается сразу, а загрузка файла
        отложена на следующий тик цикла событий. Теперь запрос запоминается и
        выполняется, как только файл готов.
        """
        target = max(0, ms)
        if not self.is_ready:
            self._pending_seek_ms = target
            return
        try:
            self._mpv.command("seek", str(target / 1000.0), "absolute", "exact")
        except (SystemError, OSError, RuntimeError):
            # mpv отказал: файл ещё открывается, сменился или закончился.
            # Это не повод прерывать работу — повторим, когда он будет готов.
            self._pending_seek_ms = target

    @property
    def is_ready(self) -> bool:
        """Открыт ли файл настолько, чтобы принимать команды перемотки."""
        return self._path is not None and self._duration_ms > 0

    def _flush_pending_seek(self) -> None:
        """Выполняет отложенную перемотку после готовности файла."""
        target = self._pending_seek_ms
        if target is None:
            return
        self._pending_seek_ms = None
        # Второй попытки не будет: позиция уже неактуальна, а переставлять её
        # при каждом обновлении длительности — значит мешать пользователю,
        # который тем временем перемотал сам.
        with contextlib.suppress(SystemError, OSError, RuntimeError):
            self._mpv.command("seek", str(target / 1000.0), "absolute", "exact")

    def frame_step(self, forward: bool = True) -> None:
        self._mpv.command("frame-step" if forward else "frame-back-step")

    @property
    def position_ms(self) -> int:
        return self._position_ms

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def speed(self) -> float:
        return float(self._mpv.speed)

    @speed.setter
    def speed(self, value: float) -> None:
        self._mpv.speed = max(0.1, min(float(value), 8.0))

    @property
    def muted(self) -> bool:
        return bool(self._mpv.mute)

    @muted.setter
    def muted(self, value: bool) -> None:
        self._mpv.mute = bool(value)

    @property
    def volume(self) -> float:
        return float(self._mpv.volume or 0.0)

    @volume.setter
    def volume(self, value: float) -> None:
        self._mpv.volume = max(0.0, min(float(value), 100.0))

    @property
    def muted(self) -> bool:
        return bool(self._mpv.mute)

    @muted.setter
    def muted(self, value: bool) -> None:
        self._mpv.mute = bool(value)

    # -- луп по реплике ------------------------------------------------------------ #

    def loop_range(self, start_ms: int, end_ms: int) -> None:
        """Зацикливает участок — «проиграть текущую реплику»."""
        self._mpv.command("set", "ab-loop-a", str(start_ms / 1000.0))
        self._mpv.command("set", "ab-loop-b", str(end_ms / 1000.0))

    def clear_loop(self) -> None:
        self._mpv.command("set", "ab-loop-a", "no")
        self._mpv.command("set", "ab-loop-b", "no")

    # -- сведения -------------------------------------------------------------------- #

    @property
    def video_params(self) -> VideoParams:
        return self._params

    @property
    def path(self) -> Path | None:
        return self._path

    def property_or(self, name: str, default: object = None) -> object:
        """Свойство mpv с запасным значением.

        Обращение к свойству до загрузки файла бросает исключение, а это
        нормальная ситуация — плеер может простаивать.
        """
        try:
            return self._mpv._get_property(name)
        except Exception:
            return default

    @property
    def raw(self):
        """Доступ к объекту python-mpv — для видеовиджета и отладки."""
        return self._mpv

    def close(self) -> None:
        """Останавливает mpv, сняв перед этим всё, что может выстрелить.

        Порядок важен. Наблюдатели свойств живут в фоновом потоке mpv, и
        событие, пришедшее уже после ``terminate``, обращается к разрушенным
        структурам — процесс падает целиком, с access violation внутри
        libmpv, и тесты обрываются на середине без единого проваленного.
        Сначала снимаем наблюдателей и обнуляем обработчики, потом гасим.
        """
        for observer in self._observers:
            with contextlib.suppress(Exception):
                observer.unobserve_mpv_properties()
        self._observers.clear()

        self.on_position = None
        self.on_duration = None
        self.on_pause = None
        self.on_video_params = None
        self.on_eof = None

        # terminate() может бросить, если mpv уже завершился сам.
        with contextlib.suppress(Exception):
            self._mpv.terminate()


def _read_params(value: dict, handle: object) -> VideoParams:
    width = int(value.get("w") or value.get("dw") or 0)
    height = int(value.get("h") or value.get("dh") or 0)
    rotation = int(value.get("rotate") or 0) % 360

    fps: FpsModel | None = None
    try:
        raw_fps = handle.container_fps  # type: ignore[attr-defined]
        if raw_fps:
            fps = FpsModel.from_float(float(raw_fps))
    except Exception:
        fps = None

    aspect = 16 / 9
    try:
        display_w = int(value.get("dw") or width)
        display_h = int(value.get("dh") or height)
        if display_h:
            aspect = display_w / display_h
    except (TypeError, ZeroDivisionError):
        pass

    _ = Fraction  # используется через FpsModel
    return VideoParams(
        width=width, height=height, fps=fps,
        rotation=rotation if rotation in (0, 90, 180, 270) else 0,
        aspect=aspect,
    )
