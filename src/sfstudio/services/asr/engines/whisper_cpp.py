"""Адаптер whisper.cpp — внешней программы.

Отдельный вид движка: не библиотека Python, а исполняемый файл. Нужен там, где
ставить пакеты нельзя или нежелательно, — whisper.cpp это один бинарник и один
файл модели, без зависимостей.

Общение идёт через файлы: на вход WAV 16 кГц, на выход SRT, который программа
кладёт рядом. Разбирать её вывод из консоли было бы хрупко — формат сообщений
меняется от версии к версии, а SRT стабилен.

Путь к бинарнику берётся из настроек либо ищется в ``PATH`` под несколькими
именами: проект переименовывал главный исполняемый файл (``main`` →
``whisper-cli``), и обе версии ещё встречаются.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.services.asr.audio import extract_audio, write_wav
from sfstudio.services.asr.base import (
    CancelToken,
    EngineInfo,
    EngineKind,
    ProgressReporter,
    RecognitionCancelled,
    RecognitionError,
    RecognitionRequest,
    RecognitionResult,
    Segment,
)
from sfstudio.services.asr.registry import program_on_path

__all__ = ["WhisperCppEngine"]

#: Исполняемые файлы, под которыми проект встречается в дикой природе.
#: Прежнее имя ``main`` здесь намеренно отсутствует: оно слишком общее, и
#: поиск по PATH находил под ним постороннюю программу — движок объявлялся
#: доступным, а распознавание падало при первом запуске. Старую сборку можно
#: указать явным путём в настройках.
BINARY_NAMES = ("whisper-cli", "whisper-cpp", "whisper")

_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)

#: Без окна консоли: иначе при каждом запуске мигает чёрный прямоугольник.
_NO_WINDOW = 0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0


class WhisperCppEngine:
    """Распознавание через внешний whisper.cpp."""

    key = "whisper-cpp"

    def __init__(self, binary: Path | None = None, downloads: Path | None = None) -> None:
        self._binary = Path(binary) if binary else None
        #: Каталог загрузок программы: туда кладётся скачанный whisper.cpp.
        self._downloads = Path(downloads) if downloads else None

    # -- описание ---------------------------------------------------------------- #

    def binary(self) -> Path | None:
        """Путь к программе: заданный явно, скачанный нами либо из PATH.

        Порядок такой намеренно. Указанный руками путь главнее всего: человек
        мог собрать свою версию — например с Vulkan, ради видеокарты не от
        NVIDIA, — и подменять её нашей скачанной было бы самоуправством.
        """
        if self._binary is not None and self._binary.is_file():
            return self._binary

        if self._downloads is not None:
            from sfstudio.services.accel.binaries import installed_whisper_cpp

            downloaded = installed_whisper_cpp(self._downloads)
            if downloaded is not None:
                return downloaded

        for name in BINARY_NAMES:
            found = program_on_path(name)
            if found:
                return Path(found)
        return None

    def info(self) -> EngineInfo:
        found = self.binary()
        where = f"\nНайден: {found}" if found is not None else ""
        return EngineInfo(
            key=self.key,
            title=tr('Whisper.cpp (внешняя программа)'),
            description=(
                tr('Локальное распознавание без установки пакетов Python. Нужен исполняемый '
                       'файл whisper.cpp и файл модели .bin.{0}').format(where)
            ),
            kind=EngineKind.EXTERNAL,
            available=found is not None,
            hint=(
                tr('Скачайте сборку whisper.cpp и укажите путь к ней в настройках или '
                       'добавьте её в PATH.')
            ),
            models=(),  # модель задаётся файлом, а не именем из списка
            word_timings=False,
        )

    # -- работа ------------------------------------------------------------------- #

    def transcribe(
        self,
        request: RecognitionRequest,
        progress: ProgressReporter | None = None,
        cancel: CancelToken | None = None,
    ) -> RecognitionResult:
        started = time.monotonic()
        binary = self.binary()
        if binary is None:
            raise RecognitionError(
                tr('не найден исполняемый файл whisper.cpp. Укажите путь к нему в '
                    'настройках или добавьте в PATH.')
            )

        model_file = self._model_file(request)
        if progress is not None:
            progress(0.05, tr('чтение звука'))

        chunk = extract_audio(
            request.media,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            stream_index=request.audio_stream,
            cancel=cancel,
        )
        if len(chunk.samples) == 0:  # type: ignore[arg-type]
            return RecognitionResult(engine=self.key, model=model_file.name)

        with tempfile.TemporaryDirectory(prefix="sfstudio-asr-") as folder:
            wav = write_wav(chunk, Path(folder) / "audio.wav")
            if cancel is not None:
                cancel.raise_if_cancelled()
            if progress is not None:
                progress(0.15, tr('распознавание'))

            self._run(binary, model_file, wav, request, cancel)
            srt = wav.with_suffix(".wav.srt")
            if not srt.is_file():
                srt = wav.with_suffix(".srt")
            if not srt.is_file():
                raise RecognitionError(
                    tr('whisper.cpp отработал, но файл субтитров не появился. Проверьте, '
                           'что сборка поддерживает вывод --output-srt.')
                )
            segments = _parse_srt(srt.read_text(encoding="utf-8", errors="replace"),
                                  chunk.offset_ms)

        if progress is not None:
            progress(1.0, tr('готово'))
        return RecognitionResult(
            segments=segments,
            language=request.language,
            engine=self.key,
            model=model_file.name,
            elapsed_s=time.monotonic() - started,
        )

    def _model_file(self, request: RecognitionRequest) -> Path:
        """Файл модели: явный путь, имя файла или имя модели из каталога.

        Имя модели («small») принимается наравне с именем файла: человек
        выбирает размер в одном и том же списке независимо от движка, и
        требовать от него помнить, что для whisper.cpp это «ggml-small.bin»,
        значит перекладывать на него нашу внутреннюю разницу.
        """
        from sfstudio.services.asr.models import GGML_FILES

        raw = str(request.options.get("model_file") or request.model or "")
        if not raw:
            raise RecognitionError(
                tr('не выбран файл модели. whisper.cpp требует файл .bin — например '
                       'ggml-small.bin.')
            )

        filename = GGML_FILES.get(raw, raw)
        path = Path(filename)
        if path.is_absolute() and path.is_file():
            return path

        if request.models_dir is not None:
            direct = request.models_dir / filename
            if direct.is_file():
                return direct
            # Модель могла лечь в подпапку — hf_hub_download повторяет
            # раскладку репозитория, а она меняется.
            found = next(Path(request.models_dir).rglob(Path(filename).name), None)
            if found is not None:
                return found

        if path.is_file():
            return path
        raise RecognitionError(
            tr('файл модели не найден: {0}. Скачайте модель для whisper.cpp в окне '
                'распознавания.').format(filename)
        )

    def _run(
        self,
        binary: Path,
        model: Path,
        wav: Path,
        request: RecognitionRequest,
        cancel: CancelToken | None,
    ) -> None:
        command = [
            str(binary), "-m", str(model), "-f", str(wav),
            "--output-srt", "--output-file", str(wav),
        ]
        if request.language:
            command += ["-l", request.language]
        threads = request.options.get("threads")
        if threads:
            command += ["-t", str(threads)]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            raise RecognitionError(tr('не удалось запустить {0}: '
                   '{1}').format(binary.name, exc)) from exc

        # Ждём короткими интервалами, чтобы отмена срабатывала сразу, а не
        # после завершения многоминутного распознавания.
        while True:
            try:
                process.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.cancelled:
                    process.terminate()
                    raise RecognitionCancelled(tr('распознавание прервано')) from None

        if process.returncode != 0:
            output = (process.stdout.read() if process.stdout else "") or ""
            tail = "\n".join(output.strip().splitlines()[-5:])
            raise RecognitionError(
                f"{binary.name} завершился с кодом {process.returncode}.\n{tail}"
            )


def _parse_srt(text: str, offset_ms: int) -> list[Segment]:
    """Разбирает SRT, который оставил whisper.cpp.

    Свой разбор, а не общий из ``io.formats.srt``: тот возвращает документ со
    стилями и событиями, а нам нужны сегменты. Формат здесь заведомо простой —
    его писала программа, а не человек.
    """
    segments: list[Segment] = []
    block: list[str] = []

    def flush() -> None:
        if len(block) < 2:
            return
        match = _SRT_TIME.search(block[1] if _SRT_TIME.search(block[1]) else block[0])
        if match is None:
            return
        start = _to_ms(match.groups()[:4]) + offset_ms
        end = _to_ms(match.groups()[4:]) + offset_ms
        body = " ".join(line.strip() for line in block[2:] if line.strip())
        if body:
            segments.append(Segment(start=start, end=end, text=body))

    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip():
            block.append(line)
        else:
            flush()
            block = []
    flush()
    return segments


def _to_ms(parts) -> int:
    hours, minutes, seconds, millis = (int(p) for p in parts)
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis
