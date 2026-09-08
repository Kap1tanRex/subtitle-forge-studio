"""Тесты плеера на настоящем медиафайле.

Идут в режиме ``vo=null``: логика транспорта не зависит от вывода картинки,
а без окна тесты работают в CI. Проверка самого Render API живёт отдельно —
она требует GL-контекста и реального экрана.

**Про «Windows fatal exception: code 0xe24c4a02» в выводе.** Эти дампы печатает
faulthandler, и они не означают падения: ``0xE24C4A02`` — это ``\\xE2`` + ``LJ``
+ версия, служебное SEH-исключение LuaJIT, на котором libmpv раскручивает стек
внутри себя. Оно первично-перехваченное и обрабатывается самой libmpv; прогон
доходит до конца, все тесты проходят. Отключением скриптов оно не убирается —
проверено, LuaJIT поднимается независимо от ``load-scripts``. Гоняться за ним
не нужно; чтобы вывод не засоряло, запускайте с ``-p no:faulthandler``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from sfstudio.media.player import mpv_available

if not mpv_available():
    pytest.skip("libmpv недоступна", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_fixtures import MediaSpec, make_test_media

from sfstudio.media.player import MpvPlayer, MpvUnavailableError

pytestmark = pytest.mark.needs_native

SPEC = MediaSpec(duration_ms=4000)


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("player") / "clip.mkv"
    make_test_media(path, SPEC)
    return path


@pytest.fixture
def player() -> MpvPlayer:
    instance = MpvPlayer(video_output="null")
    yield instance
    instance.close()


@pytest.fixture
def loaded(player: MpvPlayer, media: Path) -> MpvPlayer:
    player.load(media)
    player.wait_until_loaded(timeout=20)
    player.paused = True
    _settle(player)
    return player


def _settle(player: MpvPlayer, seconds: float = 0.6) -> None:
    """Даёт mpv время обработать команду и обновить свойства."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.02)


class TestLifecycle:
    def test_creates_and_closes(self) -> None:
        instance = MpvPlayer(video_output="null")
        instance.close()

    def test_subtitles_are_disabled(self, player: MpvPlayer) -> None:
        """Субтитры рисует наш оверлей; mpv не должен рисовать свои."""
        assert player.property_or("sub-visibility") is False

    def test_idle_player_has_no_path(self, player: MpvPlayer) -> None:
        assert player.path is None
        assert player.duration_ms == 0

    @pytest.mark.parametrize("option", ["config", "load-scripts", "ytdl"])
    def test_isolated_from_user_mpv_install(self, player: MpvPlayer, option: str) -> None:
        """Плеер не должен зависеть от того, что у пользователя стоит mpv.

        Чужой ``mpv.conf`` молча переопределяет наши настройки — тот же
        ``sub-visibility``, и субтитры задваиваются, — а автозагруженный
        скрипт может сам перематывать видео прямо под редактором.
        """
        assert player.property_or(option) is False


class TestPlayback:
    def test_duration(self, loaded: MpvPlayer) -> None:
        assert abs(loaded.duration_ms - SPEC.duration_ms) < 200

    def test_video_params(self, loaded: MpvPlayer) -> None:
        params = loaded.video_params
        assert (params.width, params.height) == (SPEC.width, SPEC.height)
        assert params.fps is not None
        assert params.fps.rate == SPEC.fps

    def test_exact_seek(self, loaded: MpvPlayer) -> None:
        """Без exact mpv прыгал бы к ключевому кадру, и тайминги поехали бы."""
        loaded.seek_ms(2000)
        _settle(loaded)
        assert abs(loaded.position_ms - 2000) < 60

    def test_frame_step_advances_one_frame(self, loaded: MpvPlayer) -> None:
        loaded.seek_ms(1000)
        _settle(loaded)
        before = loaded.position_ms
        loaded.frame_step(True)
        _settle(loaded, 0.4)
        delta = loaded.position_ms - before
        expected = 1000 / float(SPEC.fps)
        assert delta == pytest.approx(expected, abs=8)

    def test_pause_toggle(self, loaded: MpvPlayer) -> None:
        assert loaded.paused is True
        loaded.toggle_pause()
        assert loaded.paused is False
        loaded.toggle_pause()
        assert loaded.paused is True

    def test_speed_is_clamped(self, player: MpvPlayer) -> None:
        player.speed = 100.0
        assert player.speed <= 8.0
        player.speed = 0.0
        assert player.speed >= 0.1

    def test_volume_is_clamped(self, player: MpvPlayer) -> None:
        player.volume = 500
        assert player.volume <= 100
        player.volume = -20
        assert player.volume >= 0

    def test_seek_never_negative(self, loaded: MpvPlayer) -> None:
        loaded.seek_ms(-5000)
        _settle(loaded)
        assert loaded.position_ms >= 0


class TestCallbacks:
    def test_duration_callback_fires(self, player: MpvPlayer, media: Path) -> None:
        seen: list[int] = []
        player.on_duration = seen.append
        player.load(media)
        player.wait_until_loaded(timeout=20)
        _settle(player)
        assert seen and abs(seen[-1] - SPEC.duration_ms) < 200

    def test_video_params_callback_fires(self, player: MpvPlayer, media: Path) -> None:
        seen: list[object] = []
        player.on_video_params = seen.append
        player.load(media)
        player.wait_until_loaded(timeout=20)
        _settle(player)
        assert seen
        assert seen[-1].width == SPEC.width  # type: ignore[attr-defined]


class TestDeadlockGuard:
    def test_blocking_wait_is_refused_in_render_mode(self) -> None:
        """Регрессия: ``wait_until_loaded`` при ``vo=libmpv`` вешает процесс.

        mpv ждёт, что главный поток отрисует кадр, а тот стоит в ожидании.
        Внешне это молчаливое зависание без единой строки в логе — поэтому
        вызов должен отказывать сразу, а не «иногда работать».
        """
        instance = MpvPlayer()  # vo=libmpv
        try:
            with pytest.raises(RuntimeError, match="vo=libmpv"):
                instance.wait_until_loaded(timeout=0.1)
        finally:
            instance.close()

    def test_allowed_without_render_output(self, player: MpvPlayer) -> None:
        assert player.wait_until_loaded(timeout=0.2) in (True, False)


class TestUnavailable:
    def test_error_type_is_exported(self) -> None:
        assert issubclass(MpvUnavailableError, RuntimeError)
