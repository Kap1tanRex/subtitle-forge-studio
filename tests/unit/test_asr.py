"""Тесты слоя распознавания: реестр, контракт, подготовка звука."""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import pytest

from sfstudio.services.asr import (
    CancelToken,
    EngineInfo,
    RecognitionCancelled,
    RecognitionError,
    RecognitionRequest,
    RecognitionResult,
    Segment,
    SpeechRecognizer,
    available_engines,
    engine_infos,
    get_engine,
    has_any_engine,
    register,
    registered_keys,
)
from sfstudio.services.asr import registry as reg
from sfstudio.services.asr.audio import SAMPLE_RATE, AudioChunk, write_wav

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def clean_registry():
    """Реестр глобальный — восстанавливаем его после теста."""
    saved = dict(reg._FACTORIES)
    yield
    reg._FACTORIES.clear()
    reg._FACTORIES.update(saved)


class FakeEngine:
    """Движок из трёх строк — ровно то, что должен уметь сторонний автор."""

    key = "fake"

    def __init__(self, segments: list[Segment] | None = None, *, slow: bool = False) -> None:
        self._segments = segments or [Segment(0, 2000, "Привет"), Segment(2500, 4000, "Мир")]
        self._slow = slow

    def info(self) -> EngineInfo:
        return EngineInfo(key=self.key, title="Тестовый", available=True,
                          models=("small",))

    def transcribe(self, request, progress=None, cancel=None) -> RecognitionResult:
        for index, _segment in enumerate(self._segments):
            if cancel is not None:
                cancel.raise_if_cancelled()
            if progress is not None:
                progress((index + 1) / len(self._segments), "распознавание")
            if self._slow:
                time.sleep(0.01)
        return RecognitionResult(segments=list(self._segments), engine=self.key,
                                 model=request.model, language="ru")


class TestContract:
    def test_fake_engine_satisfies_the_protocol(self) -> None:
        """Структурная типизация: наследование не требуется."""
        assert isinstance(FakeEngine(), SpeechRecognizer)

    def test_segment_duration(self) -> None:
        assert Segment(1000, 3500, "x").duration == 2500

    def test_segment_cleans_whitespace(self) -> None:
        assert Segment(0, 1, "  два   пробела ").cleaned() == "два пробела"

    def test_result_is_falsy_when_empty(self) -> None:
        assert RecognitionResult().is_empty
        assert len(RecognitionResult(segments=[Segment(0, 1, "x")])) == 1

    def test_request_defaults_are_safe(self) -> None:
        request = RecognitionRequest(media=Path("clip.mkv"))
        assert request.start_ms == 0
        assert request.end_ms is None
        assert request.language is None
        assert request.options == {}


class TestCancelToken:
    def test_starts_active(self) -> None:
        assert not CancelToken().cancelled

    def test_raises_after_cancel(self) -> None:
        token = CancelToken()
        token.cancel()
        with pytest.raises(RecognitionCancelled):
            token.raise_if_cancelled()

    def test_silent_while_active(self) -> None:
        CancelToken().raise_if_cancelled()

    def test_engine_honours_cancellation(self) -> None:
        """Прерванное распознавание обязано бросать, а не возвращать половину."""
        token = CancelToken()
        token.cancel()
        with pytest.raises(RecognitionCancelled):
            FakeEngine().transcribe(RecognitionRequest(media=Path("x")), cancel=token)


class TestRegistry:
    def test_register_and_get(self, clean_registry) -> None:
        register("fake", FakeEngine)
        assert "fake" in registered_keys()
        assert get_engine("fake") is not None

    def test_unknown_key_gives_none(self) -> None:
        assert get_engine("такого-нет") is None

    def test_empty_key_is_refused(self, clean_registry) -> None:
        with pytest.raises(ValueError, match="пустым"):
            register("", FakeEngine)

    def test_third_party_engine_needs_no_core_changes(self, clean_registry) -> None:
        """Главное обещание точки расширения."""
        register("fake", FakeEngine)
        titles = [info.title for info in available_engines()]
        assert "Тестовый" in titles

    def test_unavailable_engines_are_still_listed(self) -> None:
        """Пользователь должен узнать о движке и о том, что для него нужно."""
        infos = engine_infos()
        assert infos
        for info in infos:
            if not info.available:
                assert info.hint, f"{info.key} без подсказки по установке"

    def test_broken_adapter_does_not_break_the_list(self, clean_registry) -> None:
        def explode():
            raise RuntimeError("адаптер сломан")

        register("broken", explode)
        register("fake", FakeEngine)
        infos = {info.key: info for info in engine_infos()}
        assert not infos["broken"].available
        assert infos["fake"].available

    def test_has_any_engine_reflects_availability(self, clean_registry) -> None:
        reg._FACTORIES.clear()
        assert not has_any_engine()
        register("fake", FakeEngine)
        assert has_any_engine()

    def test_factory_is_lazy(self, clean_registry) -> None:
        """Движок не создаётся, пока не понадобился: модель весит гигабайты."""
        created: list[int] = []

        class Counting(FakeEngine):
            def __init__(self) -> None:
                super().__init__()
                created.append(1)

        register("counting", Counting)
        assert created == []
        get_engine("counting")
        assert created == [1]


class TestBuiltinEngines:
    def test_all_three_are_registered(self) -> None:
        keys = set(registered_keys())
        assert {"faster-whisper", "openai-whisper", "whisper-cpp"} <= keys

    def test_listing_is_fast(self) -> None:
        """Список строится при каждом открытии диалога — секунды недопустимы."""
        started = time.perf_counter()
        engine_infos()
        assert time.perf_counter() - started < 1.0

    def test_no_heavy_module_is_imported(self) -> None:
        """Проверка доступности не должна тянуть torch в память."""
        engine_infos()
        assert "torch" not in sys.modules

    def test_missing_library_reports_how_to_install(self) -> None:
        info = next(i for i in engine_infos() if i.key == "faster-whisper")
        if not info.available:
            assert "pip install" in info.hint

    def test_engines_declare_models(self) -> None:
        info = next(i for i in engine_infos() if i.key == "faster-whisper")
        assert "small" in info.models


class TestModuleChecks:
    def test_known_module(self) -> None:
        assert reg.module_installed("json")

    def test_missing_module(self) -> None:
        assert not reg.module_installed("модуль_которого_нет_12345")

    def test_broken_name_is_not_fatal(self) -> None:
        assert not reg.module_installed("")

    def test_program_lookup(self) -> None:
        assert reg.program_on_path("программа-которой-нет-12345") is None


class TestAudioHelpers:
    def test_wav_is_written_correctly(self, tmp_path: Path) -> None:
        import numpy as np

        chunk = AudioChunk(samples=np.zeros(SAMPLE_RATE, dtype="float32"))
        path = write_wav(chunk, tmp_path / "out.wav")
        with wave.open(str(path)) as handle:
            assert handle.getnchannels() == 1
            assert handle.getframerate() == SAMPLE_RATE
            assert handle.getsampwidth() == 2
            assert handle.getnframes() == SAMPLE_RATE

    def test_loud_samples_do_not_wrap_around(self, tmp_path: Path) -> None:
        """Без клипа значения вне -1..1 переполняют int16 и дают треск."""
        import numpy as np

        chunk = AudioChunk(samples=np.full(100, 2.5, dtype="float32"))
        path = write_wav(chunk, tmp_path / "loud.wav")
        with wave.open(str(path)) as handle:
            data = np.frombuffer(handle.readframes(100), dtype="<i2")
        assert data.min() > 0, "переполнение превратило громкое в тихое"

    def test_duration_is_computed(self) -> None:
        import numpy as np

        chunk = AudioChunk(samples=np.zeros(SAMPLE_RATE * 2, dtype="float32"))
        assert chunk.duration_ms == 2000

    def test_missing_file_reports_clearly(self, tmp_path: Path) -> None:
        from sfstudio.services.asr.audio import extract_audio

        with pytest.raises(RecognitionError, match="не найден"):
            extract_audio(tmp_path / "нет.mkv")


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    """Настоящий файл с известным содержимым: тон на 500..1000 мс."""
    from media_fixtures import MediaSpec, make_test_media

    path = tmp_path_factory.mktemp("asr") / "clip.mkv"
    make_test_media(path, MediaSpec(duration_ms=4000))
    return path


class TestAudioExtraction:
    """Извлечение звука на настоящем файле — стык с PyAV."""

    def test_returns_mono_16k(self, clip: Path) -> None:
        from sfstudio.services.asr.audio import extract_audio

        chunk = extract_audio(clip)
        assert chunk.sample_rate == SAMPLE_RATE
        assert chunk.samples.ndim == 1
        assert len(chunk.samples) > 0

    def test_duration_matches_the_file(self, clip: Path) -> None:
        from sfstudio.services.asr.audio import extract_audio

        assert extract_audio(clip).duration_ms == pytest.approx(4000, abs=300)

    def test_range_is_respected(self, clip: Path) -> None:
        from sfstudio.services.asr.audio import extract_audio

        chunk = extract_audio(clip, start_ms=1000, end_ms=2000)
        assert chunk.duration_ms == pytest.approx(1000, abs=250)
        assert chunk.offset_ms == 1000

    def test_samples_are_normalised(self, clip: Path) -> None:
        """Модели ждут float32 в диапазоне -1..1."""
        from sfstudio.services.asr.audio import extract_audio

        chunk = extract_audio(clip)
        assert chunk.samples.dtype.name == "float32"
        assert abs(float(chunk.samples.max())) <= 1.0

    def test_tone_is_actually_there(self, clip: Path) -> None:
        """Фикстура кладёт тон на 500..1000 мс — он обязан попасть в срез."""
        from sfstudio.services.asr.audio import extract_audio

        loud = extract_audio(clip, start_ms=500, end_ms=1000)
        silent = extract_audio(clip, start_ms=2500, end_ms=3000)
        assert float(abs(loud.samples).mean()) > float(abs(silent.samples).mean())

    def test_cancellation_stops_extraction(self, clip: Path) -> None:
        from sfstudio.services.asr.audio import extract_audio

        token = CancelToken()
        token.cancel()
        with pytest.raises(RecognitionCancelled):
            extract_audio(clip, cancel=token)

    def test_progress_is_reported(self, clip: Path) -> None:
        from sfstudio.services.asr.audio import extract_audio

        seen: list[float] = []
        extract_audio(clip, end_ms=3000, progress=lambda f, _note="": seen.append(f))
        assert seen and seen[-1] == 1.0

    def test_wav_round_trip(self, clip: Path, tmp_path: Path) -> None:
        from sfstudio.services.asr.audio import extract_audio

        chunk = extract_audio(clip, start_ms=0, end_ms=1000)
        path = write_wav(chunk, tmp_path / "chunk.wav")
        with wave.open(str(path)) as handle:
            assert handle.getnframes() == pytest.approx(len(chunk.samples), rel=0.01)


class TestWhisperCppParsing:
    """Разбор SRT, который оставляет whisper.cpp.

    Единственная часть внешнего движка, проверяемая без его установки, — и
    самая хрупкая: формат мы не контролируем.
    """

    def test_parses_two_blocks(self) -> None:
        from sfstudio.services.asr.engines.whisper_cpp import _parse_srt

        text = (
            "1\n00:00:01,000 --> 00:00:03,500\nПервая реплика\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\nВторая реплика\n"
        )
        segments = _parse_srt(text, 0)
        assert [s.text for s in segments] == ["Первая реплика", "Вторая реплика"]
        assert segments[0].start == 1000
        assert segments[0].end == 3500

    def test_offset_is_applied(self) -> None:
        """Распознавали участок с 10-й секунды — тайминги должны это учесть."""
        from sfstudio.services.asr.engines.whisper_cpp import _parse_srt

        segments = _parse_srt("1\n00:00:01,000 --> 00:00:02,000\nТекст\n", 10_000)
        assert segments[0].start == 11_000

    def test_multiline_body_is_joined(self) -> None:
        from sfstudio.services.asr.engines.whisper_cpp import _parse_srt

        text = "1\n00:00:01,000 --> 00:00:02,000\nПервая строка\nвторая строка\n"
        assert _parse_srt(text, 0)[0].text == "Первая строка вторая строка"

    def test_dot_separator_is_tolerated(self) -> None:
        from sfstudio.services.asr.engines.whisper_cpp import _parse_srt

        segments = _parse_srt("1\n00:00:01.000 --> 00:00:02.000\nТекст\n", 0)
        assert segments and segments[0].start == 1000

    def test_crlf_is_handled(self) -> None:
        from sfstudio.services.asr.engines.whisper_cpp import _parse_srt

        text = "1\r\n00:00:01,000 --> 00:00:02,000\r\nТекст\r\n"
        assert len(_parse_srt(text, 0)) == 1

    def test_garbage_does_not_raise(self) -> None:
        from sfstudio.services.asr.engines.whisper_cpp import _parse_srt

        assert _parse_srt("это не субтитры\nвовсе\n", 0) == []

    def test_empty_body_is_skipped(self) -> None:
        from sfstudio.services.asr.engines.whisper_cpp import _parse_srt

        assert _parse_srt("1\n00:00:01,000 --> 00:00:02,000\n\n", 0) == []

    def test_missing_binary_is_reported_clearly(self) -> None:
        from sfstudio.services.asr.engines.whisper_cpp import WhisperCppEngine

        engine = WhisperCppEngine(binary=Path("нет-такой-программы"))
        info = engine.info()
        if not info.available:
            assert "PATH" in info.hint or "настройк" in info.hint

    def test_generic_binary_name_is_not_searched(self) -> None:
        """Имя «main» слишком общее: по нему находилась чужая программа.

        Движок объявлялся доступным, а распознавание падало при первом
        запуске — проверено на собранном бинарнике, где в PATH оказался
        посторонний main.
        """
        from sfstudio.services.asr.engines.whisper_cpp import BINARY_NAMES

        assert "main" not in BINARY_NAMES
