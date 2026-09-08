"""Адаптер faster-whisper.

Рекомендуемый вариант для локальной работы: та же модель Whisper, но на
CTranslate2 — заметно быстрее оригинальной реализации и без torch, то есть без
пары гигабайт зависимостей.

Библиотека **не входит в поставку** и импортируется только в момент
распознавания. Проверка доступности идёт через ``find_spec``: импорт крупного
пакета занимает секунды, а список движков строится при каждом открытии диалога.

Модель скачивается при первом использовании и кладётся в каталог, который
задаёт пользователь. Явный каталог, а не кэш HuggingFace по умолчанию: модели
весят от 75 МБ до 3 ГБ, и человек вправе знать и выбирать, где они лежат.
"""

from __future__ import annotations

import time
from pathlib import Path

from sfstudio.services.asr.audio import extract_audio
from sfstudio.services.asr.base import (
    CancelToken,
    EngineInfo,
    EngineKind,
    ProgressReporter,
    RecognitionError,
    RecognitionRequest,
    RecognitionResult,
    Segment,
    WordTiming,
)
from sfstudio.services.asr.registry import module_installed

__all__ = ["FasterWhisperEngine"]

MODULE = "faster_whisper"

#: От быстрых к точным. ``large-v3`` требует около 3 ГБ и заметного времени,
#: поэтому по умолчанию предлагается ``small``: разумный компромисс на
#: типичной машине без видеокарты.
MODELS = ("tiny", "base", "small", "medium", "large-v3-turbo",
          "distil-large-v3", "large-v3")
DEFAULT_MODEL = "small"

#: Как называть устройство человеку. «cuda» ему ни о чём не говорит.
_DEVICE_TITLE = {"cuda": "(видеокарта)", "cpu": "(процессор)"}


class FasterWhisperEngine:
    """Распознавание через faster-whisper."""

    key = "faster-whisper"

    def info(self) -> EngineInfo:
        return EngineInfo(
            key=self.key,
            title="Whisper (faster-whisper)",
            description=(
                "Локальное распознавание на CPU или видеокарте. Модель "
                "скачивается один раз при первом запуске."
            ),
            kind=EngineKind.LIBRARY,
            available=module_installed(MODULE),
            hint="Установите: pip install faster-whisper",
            models=MODELS,
            word_timings=True,
        )

    def transcribe(
        self,
        request: RecognitionRequest,
        progress: ProgressReporter | None = None,
        cancel: CancelToken | None = None,
    ) -> RecognitionResult:
        started = time.monotonic()
        enable_cuda_libraries()
        device = str(request.options.get("device", "") or pick_device())
        try:
            return self._run(request, device, started, progress, cancel)
        except RecognitionError:
            raise
        except Exception as exc:
            # Видеокарта подвела уже в работе — считаем на процессоре, а не
            # сообщаем об ошибке: пользователю нужен результат, а не разбор
            # того, каких библиотек CUDA не хватает.
            if device != "cuda" or not _looks_like_cuda_failure(exc):
                raise RecognitionError(f"faster-whisper не справился: {exc}") from exc
            if progress is not None:
                progress(0.05, "видеокарта недоступна, считаем на процессоре")
            return self._run(request, "cpu", started, progress, cancel)

    def _run(
        self,
        request: RecognitionRequest,
        device: str,
        started: float,
        progress: ProgressReporter | None,
        cancel: CancelToken | None,
    ) -> RecognitionResult:
        model = self._load(request, device, progress)

        if progress is not None:
            progress(0.05, "чтение звука")
        chunk = extract_audio(
            request.media,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            stream_index=request.audio_stream,
            cancel=cancel,
        )
        if len(chunk.samples) == 0:  # type: ignore[arg-type]
            return RecognitionResult(engine=self.key, model=request.model or DEFAULT_MODEL)

        if cancel is not None:
            cancel.raise_if_cancelled()
        # Устройство в подписи не для красоты: первый сегмент появляется не
        # сразу, и без него замерший прогресс выглядит как поломка. Написано
        # «на процессоре» — понятно, почему долго; написано «на видеокарте» —
        # видно, что видеокарта действительно взята в работу.
        stage = "распознавание " + _DEVICE_TITLE.get(device, f"({device})")
        if progress is not None:
            progress(0.1, stage)

        raw_segments, info = self._transcribe(model, chunk, request)

        segments: list[Segment] = []
        total_ms = chunk.duration_ms or 1
        # Генератор: faster-whisper отдаёт сегменты по мере распознавания,
        # поэтому прогресс честный, а отмена срабатывает без ожидания конца.
        for item in raw_segments:
            if cancel is not None:
                cancel.raise_if_cancelled()
            segments.append(_convert(item, chunk.offset_ms))
            if progress is not None:
                done = segments[-1].end - chunk.offset_ms
                progress(min(0.99, 0.1 + 0.89 * done / total_ms), stage)

        if progress is not None:
            progress(1.0, "готово")

        return RecognitionResult(
            segments=segments,
            language=getattr(info, "language", None),
            engine=self.key,
            model=request.model or DEFAULT_MODEL,
            elapsed_s=time.monotonic() - started,
        )

    def _transcribe(self, model, chunk, request: RecognitionRequest):
        """Запускает распознавание, при нужде отключив определение речи.

        Фильтр VAD опирается на отдельный файл модели рядом с библиотекой. В
        собранном виде такие файлы легко не доехать, и тогда распознавание
        падает целиком — хотя сам фильтр всего лишь отбрасывает тишину.
        Поэтому его отсутствие понижается до работы без него: результат будет
        чуть хуже на длинных паузах, но он будет.
        """
        use_vad = bool(request.options.get("vad", True))
        try:
            return model.transcribe(
                chunk.samples,
                language=request.language,
                word_timestamps=request.word_timings,
                vad_filter=use_vad,
            )
        except Exception as exc:
            if not use_vad or not _looks_like_missing_vad(exc):
                raise
            return model.transcribe(
                chunk.samples,
                language=request.language,
                word_timestamps=request.word_timings,
                vad_filter=False,
            )

    def _load(
        self,
        request: RecognitionRequest,
        device: str,
        progress: ProgressReporter | None,
    ):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RecognitionError(
                "faster-whisper не установлен. Установите его командой "
                "pip install faster-whisper либо выберите другой движок."
            ) from exc

        if progress is not None:
            progress(0.01, "загрузка модели")

        name = request.model or DEFAULT_MODEL
        source, directory = _resolve_source(name, request.models_dir)
        compute = str(
            request.options.get("compute_type", "")
            or ("float16" if device == "cuda" else "int8")
        )

        try:
            return WhisperModel(
                source, device=device, compute_type=compute, download_root=directory
            )
        except Exception as exc:
            raise RecognitionError(
                f"не удалось загрузить модель «{name}»: {exc}\n"
                "Проверьте название модели и доступ к каталогу моделей."
            ) from exc


#: Библиотеки CUDA, которые приходится загружать своими руками. Порядок —
#: порядок зависимости: cublas тянет cublasLt, старшие модули cuDNN тянут
#: младшие. Загрузи их не в том порядке — и Windows пойдёт искать зависимость
#: обычным путём, то есть мимо нужного каталога.
_CUDA_LIBRARIES = (
    "cublasLt64_12",
    "cublas64_12",
    "cudnn_graph64_9",
    "cudnn_ops64_9",
    "cudnn_cnn64_9",
    "cudnn_engines_precompiled64_9",
    "cudnn64_9",
)

#: Уже загруженные библиотеки. Ссылки хранятся, чтобы их не закрыл сборщик
#: мусора: выгруженная DLL — это снова «библиотека не найдена».
_cuda_loaded: dict[str, object] = {}

#: Каталоги, уже открытые для поиска зависимостей.
_dll_dirs_added: set[str] = set()


#: Куда пользователь может положить библиотеки CUDA для собранной программы.
CUDA_DIR_ENV = "SFSTUDIO_CUDA_DIR"
CUDA_DIR_NAME = "cuda"


def _cuda_search_roots() -> list[Path]:
    """Где искать библиотеки CUDA, от установленного к положенному руками.

    В собранной программе ``site-packages`` распакован во временную папку, и
    пакетов ``nvidia`` там нет. Это не упущение: cuBLAS и cuDNN вместе весят
    около двух гигабайт — вдесятеро больше самой программы, притом нужны они
    только тем, у кого есть подходящая видеокарта. Класть их в дистрибутив,
    который качают все, — плохой обмен.

    Поэтому у собранной версии два пути: папка ``cuda`` рядом с исполняемым
    файлом либо путь в переменной окружения. Ни того ни другого нет —
    считаем на процессоре, это работает всегда.
    """
    import os
    import site
    import sys

    roots: list[Path] = []
    try:
        bases = (*site.getsitepackages(), site.getusersitepackages())
    except AttributeError:  # обрезанный site в собранном виде
        bases = ()
    roots.extend(Path(base) / "nvidia" for base in bases)

    if custom := os.environ.get(CUDA_DIR_ENV, "").strip():
        roots.append(Path(custom))
    roots.append(Path(sys.executable).resolve().parent / CUDA_DIR_NAME)

    seen: set[Path] = set()
    found: list[Path] = []
    for root in roots:
        if root.is_dir() and root not in seen:
            seen.add(root)
            found.append(root)
    return found


def enable_cuda_libraries() -> list[str]:
    """Загружает библиотеки CUDA, поставленные через pip.

    Пакеты ``nvidia-cublas-cu12`` и ``nvidia-cudnn-cu12`` кладут DLL в
    ``site-packages/nvidia/<модуль>/bin`` — каталог, которого нет ни в PATH,
    ни в путях поиска Windows.

    Одного ``os.add_dll_directory`` для этого **мало**, и это стоило неверного
    диагноза. Добавленные им каталоги просматриваются только при загрузке с
    флагами ``LOAD_LIBRARY_SEARCH_*``, а CTranslate2 зовёт ``LoadLibrary`` по
    голому имени файла — такой вызов ищет по PATH и системным каталогам и
    добавленных не видит. Со стороны это неотличимо от неподдерживаемой
    видеокарты: «cublas64_12.dll is not found», молчаливый откат на процессор,
    прогресс замирает на десятой доле, карта не загружена.

    Поэтому библиотеки загружаются явно, по полному пути. Загруженная DLL
    попадает в таблицу модулей процесса, и последующий поиск по имени находит
    уже открытую копию — искать её по каталогам больше не нужно.

    Возвращает имена загруженных **в этот раз**: повторный вызов вернёт
    пустой список.
    """
    import ctypes
    import os

    if os.name != "nt":
        return []  # на Linux библиотеки ищутся по ld.so, вмешиваться незачем

    folders: list[Path] = []
    for root in _cuda_search_roots():
        if any(root.glob("*.dll")):
            folders.append(root)
        folders.extend(
            child for child in root.glob("*/bin") if any(child.glob("*.dll"))
        )
    if not folders:
        return []

    # Ядра под архитектуры новее сборки CTranslate2 (Blackwell и далее)
    # драйвер собирает из PTX при первом запуске — несколько секунд, потом
    # берёт из кэша. Кэш по умолчанию невелик, и большая библиотека вытесняет
    # оттуда сама себя: без этой строки те секунды платятся каждый раз.
    os.environ.setdefault("CUDA_CACHE_MAXSIZE", str(2 * 1024**3))

    for folder in folders:
        key = str(folder)
        if key in _dll_dirs_added:
            continue
        try:
            os.add_dll_directory(key)
        except OSError:
            continue
        _dll_dirs_added.add(key)

    loaded: list[str] = []
    for name in _CUDA_LIBRARIES:
        if name in _cuda_loaded:
            continue
        for folder in folders:
            dll = folder / f"{name}.dll"
            if not dll.is_file():
                continue
            try:
                _cuda_loaded[name] = ctypes.WinDLL(str(dll))
            except OSError:
                pass  # версия не та или не хватает зависимости
            else:
                loaded.append(name)
            break
    return loaded


def pick_device() -> str:
    """Где считать: на видеокарте, если она действительно готова.

    Проверка — попытка загрузить cuBLAS, а не вопрос к CTranslate2.
    ``get_supported_compute_types("cuda")`` отвечает по возможностям карты, а
    не по наличию библиотек: на машине с RTX 5070 Ti он уверенно перечислял
    float16 и int8, после чего распознавание падало на первом же умножении
    матриц. Загрузка библиотеки такой прослойки не имеет: либо открылась,
    либо нет.

    Проверка всё равно неполная — окончательный ответ даёт только попытка, и
    она подстрахована откатом на процессор в :meth:`FasterWhisperEngine.transcribe`.
    """
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() <= 0:
            return "cpu"
    except Exception:  # библиотека может отсутствовать или не открыться
        return "cpu"

    enable_cuda_libraries()
    return "cuda" if _cublas_ready() else "cpu"


def cuda_status(libraries_dir=None) -> tuple[bool, str]:
    """Готов ли счёт на видеокарте и почему нет, если не готов.

    Ответ «недоступна» без причины бесполезен: причин четыре, и лечатся они
    по-разному — нет самой карты, нет сборки CTranslate2 с поддержкой CUDA,
    нет библиотек, либо библиотеки есть, но не открываются. Здесь они
    различаются, чтобы окно могло предложить именно то, что нужно.
    """
    if libraries_dir is not None:
        import os

        os.environ.setdefault(CUDA_DIR_ENV, str(libraries_dir))

    try:
        import ctranslate2
    except Exception:
        return False, ("Не установлен CTranslate2 — библиотека, которая считает "
                       "модель. Он ставится вместе с faster-whisper.")

    try:
        count = ctranslate2.get_cuda_device_count()
    except Exception:
        count = 0
    if count <= 0:
        return False, ("Видеокарта NVIDIA не найдена либо драйвер не отвечает. "
                       "Счёт пойдёт на процессоре.")

    enable_cuda_libraries()
    if _cublas_ready():
        return True, "Готова к работе."
    return False, ("Нет библиотеки cuBLAS — без неё видеокарта считать не может. "
                   "Её можно скачать прямо отсюда (около 0,7 ГБ) либо "
                   "установить командой pip install nvidia-cublas-cu12.")


def _cublas_ready() -> bool:
    """Открывается ли cuBLAS: без него на видеокарте не посчитать ничего."""
    import ctypes
    import os

    if os.name != "nt":
        return True  # загрузку берёт на себя ld.so, проверять нечего
    if any(name.startswith("cublas64") for name in _cuda_loaded):
        return True

    # Библиотека может стоять не из pip, а из CUDA Toolkit — она тогда в PATH.
    # Ищем файл сами и грузим по полному пути. Загрузка по голому имени
    # означала бы поиск по правилам Windows, а в них входит текущий рабочий
    # каталог: программу нередко запускают из папки с чужими материалами, и
    # положенная туда «cublas64_12.dll» выполнилась бы при первом же взгляде
    # в окно распознавания.
    for name in ("cublas64_12", "cublas64_11"):
        dll = _find_in_path(f"{name}.dll")
        if dll is None:
            continue
        try:
            _cuda_loaded[name] = ctypes.WinDLL(str(dll))
        except OSError:
            continue
        return True
    return False


def _find_in_path(filename: str) -> Path | None:
    """Ищет файл в каталогах PATH и CUDA_PATH. Текущий каталог не в счёт."""
    import os

    roots: list[Path] = []
    for variable in ("CUDA_PATH", "CUDA_PATH_V12_0"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value) / "bin")
    roots.extend(
        Path(part) for part in os.environ.get("PATH", "").split(os.pathsep) if part
    )
    for root in roots:
        if not root.is_absolute():
            continue  # «.» в PATH — это опять текущий каталог
        candidate = root / filename
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue  # недоступный каталог в PATH — не повод падать
    return None


def _looks_like_missing_vad(exc: BaseException) -> bool:
    """Похоже ли, что не хватает именно файла модели определения речи."""
    text = str(exc).lower()
    return "vad" in text and ("no_suchfile" in text or "doesn't exist" in text
                              or "not exist" in text or "load model" in text)


def _looks_like_cuda_failure(exc: BaseException) -> bool:
    """Похоже ли, что виновата именно видеокарта, а не файл или модель."""
    text = str(exc).lower()
    return any(
        mark in text
        for mark in ("cublas", "cudnn", "cuda", "gpu", "no kernel image")
    )


def _resolve_source(name: str, models_dir: Path | None) -> tuple[str, str | None]:
    """Что передать библиотеке: путь к скачанной модели либо её имя.

    Если модель уже лежит в каталоге, отдаём **путь**. Иначе библиотека скачает
    её заново в свой кэш рядом — и на диске окажутся две копии по полгигабайта,
    о чём пользователь не просил.
    """
    if models_dir is None:
        return name, None

    root = Path(models_dir)
    for candidate in (root, root / f"faster-whisper-{name}", root / name):
        if (candidate / "model.bin").is_file():
            return str(candidate), None

    # Пользователь мог указать каталог выше того, где лежит модель — например
    # общую папку «Модели». Ищем вглубь, но только по точному совпадению
    # имени: по вхождению для «large-v3» подошла бы папка «large-v3-turbo».
    if root.is_dir():
        from sfstudio.services.asr.models import folder_matches

        for weights in root.rglob("model.bin"):
            if folder_matches(weights.parent.name, name):
                return str(weights.parent), None

    return name, str(root)


def _convert(item, offset_ms: int) -> Segment:
    """Сегмент библиотеки → наш. Секунды в миллисекунды, смещение обратно."""
    start = offset_ms + int(item.start * 1000)
    end = offset_ms + int(item.end * 1000)
    words = tuple(
        WordTiming(
            start=offset_ms + int(word.start * 1000),
            end=offset_ms + int(word.end * 1000),
            text=word.word,
            probability=float(getattr(word, "probability", 1.0) or 1.0),
        )
        for word in (getattr(item, "words", None) or [])
    )
    # avg_logprob — логарифм вероятности, обычно от -1 до 0. Переводим в
    # понятную шкалу 0..1, чтобы порог уверенности задавался единообразно
    # для любого движка.
    logprob = float(getattr(item, "avg_logprob", 0.0) or 0.0)
    confidence = max(0.0, min(1.0, 1.0 + logprob))

    return Segment(start=start, end=end, text=item.text, confidence=confidence,
                   words=words)
