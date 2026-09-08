"""Загрузка моделей распознавания.

Модель — это гигабайты на диске пользователя, и решать за него, какую и куда,
нельзя. Поэтому размер и каталог выбираются явно, а рядом с каждым вариантом
написано, сколько он весит и чего от него ждать.

Загрузка идёт **не в кэш HuggingFace по умолчанию**, а в указанный каталог.
Кэш прячется в профиле пользователя, растёт незаметно и после удаления
программы остаётся там навсегда; явный каталог видно, его можно положить на
другой диск и удалить, когда надоест.

Прогресс снимается с ``tqdm``, которым пользуется ``huggingface_hub``: своего
механизма отчёта у него нет, а качать полтора гигабайта без единого признака
жизни — то же самое, что зависнуть.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sfstudio.services.asr.base import (
    CancelToken,
    ProgressReporter,
    RecognitionCancelled,
    RecognitionError,
)

__all__ = ["MODEL_CATALOG", "ModelInfo", "download_model", "installed_models"]


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Что за модель и чего она стоит."""

    name: str
    title: str
    size_mb: int
    #: Число параметров, млн. Именно оно определяет вес: у Whisper на каждый
    #: параметр приходится два байта, поэтому medium (769 млн) весит втрое
    #: больше small (244 млн) — вопрос не в упаковке, а в размере сети.
    params_m: int
    note: str = ""
    #: Языки, которые модель знает; ``None`` — многоязычная.
    #: Не формальность: distil-версии дообучены **только на английском** и на
    #: русской речи выдают английский текст, ничем не сообщая об ошибке.
    #: Пользователь выбирает модель по весу и качеству, а получает молча не
    #: тот язык — поэтому ограничение указано в данных, а не в примечании.
    languages: tuple[str, ...] | None = None

    @property
    def english_only(self) -> bool:
        return self.languages is not None and set(self.languages) == {"en"}

    @property
    def caption(self) -> str:
        mark = " · только английский" if self.english_only else ""
        return f"{self.title} — {self.size_mb} МБ{mark}"

    def supports(self, language: str | None) -> bool:
        """Справится ли модель с этим языком.

        Пустой язык — автоопределение. Для одноязычной модели это тоже «нет»:
        определять ей нечего, она всё равно ответит на своём языке.
        """
        if self.languages is None:
            return True
        return bool(language) and language in self.languages


#: Размеры **измерены** по метаданным репозиториев HuggingFace, а не взяты из
#: документации: в ней они округлены, а разница между 1460 и 1530 МБ — это
#: место на диске, которое пользователь освобождал под модель.
#: Числа в мебибайтах, как их показывает проводник Windows.
MODEL_CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo("tiny", "tiny", 75, 39,
              "Самая быстрая. Годится проверить, что всё работает."),
    ModelInfo("base", "base", 141, 74,
              "Быстрая. Русский разбирает с ошибками."),
    ModelInfo("small", "small", 464, 244,
              "Разумный выбор для русского на машине без видеокарты."),
    ModelInfo("medium", "medium", 1460, 769,
              "Втрое больше small по числу параметров — отсюда и вес."),
    ModelInfo("large-v3-turbo", "large-v3-turbo", 1547, 809,
              "Лучшее соотношение качества и веса: точность почти как у "
              "large-v3 вдвое меньшим файлом. Декодер урезан с 32 слоёв до "
              "четырёх, поэтому она ещё и заметно быстрее. Многоязычная, "
              "русский понимает."),
    ModelInfo("distil-large-v3", "distil-large-v3", 1446, 756,
              "Сжатая large для английской речи: качество близко к ней, "
              "размер как у medium. Другие языки не понимает — на них выдаёт "
              "английский текст. Для русского берите medium или large-v3.",
              languages=("en",)),
    ModelInfo("large-v3", "large-v3", 2948, 1550,
              "Лучшее качество. Требует много памяти и времени."),
)

#: Имена репозиториев на HuggingFace для faster-whisper.
_REPO = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    # Имя репозитория взято из самой faster-whisper: она отображает
    # «large-v3-turbo» именно сюда, и расходиться с ней незачем.
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "large-v3": "Systran/faster-whisper-large-v3",
}

#: Файл, по которому видно, что модель скачана целиком.
_MARKER = "model.bin"


def folder_matches(folder: str, model: str) -> bool:
    """Лежит ли в папке с таким именем именно эта модель.

    Сравнение точное, а не по вхождению, и это не придирка: «large-v3» —
    часть «large-v3-turbo» и «distil-large-v3». По вхождению скачанный turbo
    выдавался бы за large-v3, а распознавание молча шло бы не на той модели,
    которую выбрал человек.
    """
    name = folder.casefold()
    wanted = model.casefold()
    return name == wanted or name == f"faster-whisper-{wanted}"


def installed_models(models_dir: Path | None) -> set[str]:
    """Какие модели уже лежат в каталоге.

    Ищем по файлу весов, а не по наличию папки: прерванная загрузка оставляет
    каталог с конфигом, и считать такую модель установленной — значит послать
    пользователя ждать распознавания, которое сразу упадёт.
    """
    if models_dir is None:
        return set()
    root = Path(models_dir)
    if not root.is_dir():
        return set()

    # Один обход вместо обхода на каждую модель: каталог моделей может лежать
    # на медленном диске, а список перечитывается при каждом открытии окна.
    folders = [weights.parent.name for weights in root.rglob(_MARKER)]
    return {
        info.name
        for info in MODEL_CATALOG
        if any(folder_matches(folder, info.name) for folder in folders)
    }


def download_model(
    model: str,
    models_dir: Path,
    *,
    progress: ProgressReporter | None = None,
    cancel: CancelToken | None = None,
) -> Path:
    """Скачивает модель в указанный каталог. Возвращает путь к ней."""
    repo = _REPO.get(model)
    if repo is None:
        raise RecognitionError(
            f"неизвестная модель «{model}». Доступны: "
            + ", ".join(info.name for info in MODEL_CATALOG)
        )

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RecognitionError(
            "нет huggingface_hub. Он ставится вместе с faster-whisper: "
            "pip install faster-whisper"
        ) from exc

    models_dir = Path(models_dir)
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecognitionError(f"не удалось создать каталог {models_dir}: {exc}") from exc

    if progress is not None:
        progress(0.0, f"загрузка модели {model}")

    tracker = _ProgressTqdm.factory(progress, cancel, model)
    try:
        path = snapshot_download(
            repo_id=repo,
            local_dir=str(models_dir / f"faster-whisper-{model}"),
            tqdm_class=tracker,
        )
    except RecognitionCancelled:
        raise
    except Exception as exc:  # сеть и hub бросают что угодно
        raise RecognitionError(
            f"не удалось скачать модель «{model}»: {exc}\n"
            "Проверьте подключение к сети и место на диске."
        ) from exc

    if progress is not None:
        progress(1.0, "модель загружена")
    return Path(path)


class _ProgressTqdm:
    """Подставной ``tqdm``: снимает прогресс и проверяет отмену.

    ``huggingface_hub`` принимает класс индикатора и создаёт его сам, поэтому
    единственный способ узнать о ходе загрузки — подменить этот класс.

    **Индикаторов несколько, и складывать их нельзя.** На одну загрузку hub
    заводит три: «Downloading bytes», «Reconstructing» (сборка файла из
    кусков) и «Fetching N files». Первые два считают одни и те же байты
    разными способами, третий считает файлы. Суммирование давало объём вдвое
    с лишним больше настоящего — модель на 1446 МБ показывалась как 2287 МБ,
    и это сразу заметили.

    Поэтому учёт ведётся по **ведущему** индикатору: тому из байтовых, что
    дальше всех продвинулся. Он один отражает реальный объём, а максимум
    вместо суммы делает прогресс монотонным и не завышает его.
    """

    #: Единица, по которой видно, что индикатор считает байты, а не файлы.
    _BYTE_UNIT = "B"

    _reporter: staticmethod | None = None
    _cancel: CancelToken | None = None
    _label: str = ""
    _instances: list[_ProgressTqdm] | None = None
    #: Наибольшая показанная доля. Ведущий индикатор по ходу загрузки
    #: меняется, а итог уточняется — без этого полоса дёргалась бы назад,
    #: и человек решил бы, что загрузка сорвалась и началась заново.
    _peak: float = 0.0

    def __init__(self, *_args, **kwargs) -> None:
        self.total = int(kwargs.get("total") or 0)
        self.n = 0
        self.unit = str(kwargs.get("unit") or "")
        if type(self)._instances is None:
            type(self)._instances = []
        type(self)._instances.append(self)

    @classmethod
    def factory(
        cls,
        reporter: ProgressReporter | None,
        cancel: CancelToken | None,
        label: str,
    ) -> type[_ProgressTqdm]:
        """Свежий класс на каждую загрузку: список экземпляров у него общий."""
        return type(
            "_BoundProgressTqdm",
            (cls,),
            {
                "_reporter": staticmethod(reporter) if reporter is not None else None,
                "_cancel": cancel,
                "_label": label,
                "_instances": [],
                "_peak": 0.0,
            },
        )

    @property
    def counts_bytes(self) -> bool:
        return self.unit == self._BYTE_UNIT

    def update(self, amount: int = 1) -> None:
        self.n += amount
        if self._cancel is not None and self._cancel.cancelled:
            raise RecognitionCancelled("загрузка модели прервана")
        if self._reporter is None:
            return

        leader = self._leader()
        if leader is None or not leader.total:
            return
        share = min(0.99, leader.n / leader.total)
        share = max(share, type(self)._peak)
        type(self)._peak = share
        volume = (
            f": {leader.n / 1024 / 1024:.0f} из {leader.total / 1024 / 1024:.0f} МБ"
            if leader.counts_bytes
            else ""
        )
        self._reporter(share, f"загрузка {self._label}{volume}")

    @classmethod
    def _leader(cls) -> _ProgressTqdm | None:
        """Индикатор, по которому судим о ходе загрузки.

        Байтовый и самый продвинутый: он показывает настоящий объём. Если
        байтовых нет (бывает при работе из кэша) — любой с известным итогом,
        чтобы прогресс всё же двигался.
        """
        instances = cls._instances or []
        byte_ones = [i for i in instances if i.counts_bytes and i.total]
        pool = byte_ones or [i for i in instances if i.total]
        return max(pool, key=lambda i: i.n, default=None)

    # -- остальное от tqdm требуется лишь формально ------------------------- #
    #
    # Набор методов задаёт не документация tqdm, а то, что реально вызывает
    # hub: он трогает и set_postfix_str, и format_dict — про них узнаёшь
    # только по ошибке посреди загрузки. Поэтому список расширяется по факту,
    # а не по догадкам, и каждый метод здесь безвреден.

    #: tqdm отдаёт словарь состояния; hub читает из него скорость.
    @property
    def format_dict(self) -> dict:
        return {"n": self.n, "total": self.total, "rate": None, "elapsed": 0.0}

    def set_postfix_str(self, *_args, **_kwargs) -> None:
        return None

    def clear(self, *_args, **_kwargs) -> None:
        return None

    def display(self, *_args, **_kwargs) -> None:
        return None

    def unpause(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> _ProgressTqdm:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def __iter__(self):
        return iter(())

    def set_description(self, *_args, **_kwargs) -> None:
        return None

    def set_postfix(self, *_args, **_kwargs) -> None:
        return None

    def refresh(self) -> None:
        return None

    def reset(self, total: int | None = None) -> None:
        self.n = 0
        if total:
            self.total = int(total)

    @property
    def disable(self) -> bool:
        return False


# --------------------------------------------------------------------------- #
# Модели для whisper.cpp
# --------------------------------------------------------------------------- #
#
# У него свой формат — GGML, один файл вместо каталога. Размеры те же самые:
# это те же веса Whisper, иначе упакованные, и число параметров от упаковки
# не меняется. Поэтому каталог строится из общего, а не заводится заново.

GGML_REPO = "ggerganov/whisper.cpp"

#: Имена файлов GGML по имени модели. Отличаются от имён faster-whisper:
#: turbo там «large-v3-turbo», а дистиллированной версии нет вовсе.
GGML_FILES = {
    "tiny": "ggml-tiny.bin",
    "base": "ggml-base.bin",
    "small": "ggml-small.bin",
    "medium": "ggml-medium.bin",
    "large-v3-turbo": "ggml-large-v3-turbo.bin",
    "large-v3": "ggml-large-v3.bin",
}


def ggml_models() -> tuple[ModelInfo, ...]:
    """Каталог моделей для whisper.cpp — те же веса в другом формате."""
    return tuple(info for info in MODEL_CATALOG if info.name in GGML_FILES)


def installed_ggml(models_dir: Path | None) -> set[str]:
    """Какие модели GGML уже скачаны."""
    if models_dir is None or not Path(models_dir).is_dir():
        return set()
    present = {p.name for p in Path(models_dir).rglob("ggml-*.bin")}
    return {name for name, filename in GGML_FILES.items() if filename in present}


def download_ggml_model(
    model: str,
    models_dir: Path,
    *,
    progress: ProgressReporter | None = None,
    cancel: CancelToken | None = None,
) -> Path:
    """Скачивает файл модели для whisper.cpp. Возвращает путь к нему.

    Качается **один файл**, а не снимок репозитория: в нём лежат все размеры
    сразу, и ``snapshot_download`` притащил бы двадцать гигабайт вместо
    нужных полутора.
    """
    filename = GGML_FILES.get(model)
    if filename is None:
        raise RecognitionError(
            f"для whisper.cpp нет модели «{model}». Доступны: "
            + ", ".join(GGML_FILES)
        )

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RecognitionError(
            "нет huggingface_hub — через него скачиваются модели. "
            "Он ставится вместе с faster-whisper."
        ) from exc

    models_dir = Path(models_dir)
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecognitionError(f"не удалось создать каталог {models_dir}: {exc}") from exc

    if progress is not None:
        progress(0.0, f"загрузка модели {model}")

    tracker = _ProgressTqdm.factory(progress, cancel, model)
    try:
        path = hf_hub_download(
            repo_id=GGML_REPO,
            filename=filename,
            local_dir=str(models_dir),
            tqdm_class=tracker,
        )
    except RecognitionCancelled:
        raise
    except Exception as exc:
        raise RecognitionError(
            f"не удалось скачать модель «{model}»: {exc}\n"
            "Проверьте подключение к сети и место на диске."
        ) from exc

    if progress is not None:
        progress(1.0, "модель загружена")
    return Path(path)
