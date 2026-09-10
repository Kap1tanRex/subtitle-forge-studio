"""Проект: субтитры, привязанное видео и состояние работы в одном файле.

Зачем нужен отдельный формат, если субтитры и так лежат в ``.ass``. Файл
субтитров не помнит ничего, кроме реплик: ни какое видео к нему относится, ни
какая была частота кадров, ни где пользователь остановился. Всё это
приходится восстанавливать руками при каждом открытии, и на длинной работе это
главный источник раздражения. Проект хранит **обстановку**, а не только текст.

Здесь только **модель**: что такое проект и из чего он состоит. Чтение и
запись файла живут в :mod:`sfstudio.io.project_file` — ядро не имеет права
зависеть от слоя ввода-вывода, и это проверяется тестом ``test_layering``.
Разделение не формальность: благодаря ему модель проекта поднимается на голом
интерпретаторе, без разбора ASS и без работы с архивами.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from sfstudio.app.i18n import tr
from sfstudio.core.document import SubtitleDocument

__all__ = [
    "FORMAT_VERSION",
    "FPS_PRESETS",
    "GLOSSARY_NAME",
    "MANIFEST_NAME",
    "PROJECT_SUFFIX",
    "REFERENCE_NAME",
    "RESOLUTION_PRESETS",
    "SUBTITLES_NAME",
    "Project",
    "ProjectError",
    "ProjectState",
    "now_stamp",
]

PROJECT_SUFFIX = ".sfproj"
FORMAT_VERSION = 1

MANIFEST_NAME = "project.json"
SUBTITLES_NAME = "subtitles.ass"
#: Оригинал, с которого идёт перевод. Лежит в проекте отдельным
#: файлом: это чужой текст, и в наши субтитры он попадать не должен.
REFERENCE_NAME = "reference.ass"
#: Глоссарий проекта. CSV — в таком виде их и присылают.
GLOSSARY_NAME = "glossary.csv"

#: Готовые разрешения. Это ``PlayRes`` документа — система координат, в
#: которой заданы ``\pos``, а не размер видео: они могут не совпадать, и
#: подменять одно другим нельзя.
RESOLUTION_PRESETS: tuple[tuple[str, int, int], ...] = (
    ("HD 1920×1080", 1920, 1080),
    ("UHD 3840×2160", 3840, 2160),
    ("HD 1280×720", 1280, 720),
    ("SD 720×576 (PAL)", 720, 576),
    ("SD 720×480 (NTSC)", 720, 480),
    (tr('Кино 2K 2048×1080'), 2048, 1080),
    (tr('Кино 4K 4096×2160'), 4096, 2160),
    (tr('Вертикальное 1080×1920'), 1080, 1920),
)

#: Частоты кадров. Дробные записаны точно: 23.976 — это 24000/1001, и
#: округление до трёх знаков за два часа даёт расхождение почти в четыре
#: кадра.
FPS_PRESETS: tuple[tuple[str, Fraction], ...] = (
    ("23,976 (24000/1001)", Fraction(24000, 1001)),
    ("24", Fraction(24)),
    ("25 (PAL)", Fraction(25)),
    ("29,97 (30000/1001)", Fraction(30000, 1001)),
    ("30", Fraction(30)),
    ("50", Fraction(50)),
    ("59,94 (60000/1001)", Fraction(60000, 1001)),
    ("60", Fraction(60)),
)


class ProjectError(RuntimeError):
    """Проект нельзя прочитать или записать."""


@dataclass(slots=True)
class ProjectState:
    """Где пользователь остановился.

    Это и есть «прогресс», ради которого проект отличается от файла субтитров:
    вернуться к работе через неделю и оказаться на том же месте, с тем же
    масштабом и той же выделенной репликой.
    """

    #: Позиция воспроизведения, мс.
    time_ms: int = 0
    #: Выделенная реплика по ``eid``; ``None`` — ничего не выделено.
    selected_eid: int | None = None
    #: Масштаб таймлайна, пикселей на миллисекунду.
    px_per_ms: float = 0.0
    #: Левый край видимой области таймлайна, мс.
    view_start_ms: float = 0.0
    #: Масштаб высоты дорожек.
    track_zoom: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(raw: object) -> ProjectState:
        state = ProjectState()
        if not isinstance(raw, dict):
            return state
        state.time_ms = _as_int(raw.get("time_ms"), 0)
        selected = raw.get("selected_eid")
        state.selected_eid = _as_int(selected, 0) if selected is not None else None
        state.px_per_ms = _as_float(raw.get("px_per_ms"), 0.0)
        state.view_start_ms = _as_float(raw.get("view_start_ms"), 0.0)
        state.track_zoom = _as_float(raw.get("track_zoom"), 1.0) or 1.0
        return state


@dataclass(slots=True)
class Project:
    """Проект целиком."""

    name: str = tr('Без имени')
    document: SubtitleDocument = field(default_factory=SubtitleDocument.blank)
    #: Путь к видео или звуку. ``None`` — проект без медиа, это законно.
    media_path: Path | None = None
    fps: Fraction = field(default_factory=lambda: Fraction(25))
    state: ProjectState = field(default_factory=ProjectState)
    #: Оригинал для перевода: только для чтения, в экспорт не попадает.
    reference: object | None = None
    #: Глоссарий: как переводить термины и имена.
    glossary: object | None = None
    created: str = ""
    modified: str = ""
    #: Откуда прочитан или куда записан. Не сохраняется внутрь файла.
    path: Path | None = None

    # -- производные ------------------------------------------------------------ #

    @property
    def resolution(self) -> tuple[int, int]:
        """Разрешение проекта — это ``PlayRes`` документа, не копия рядом.

        Две записи одного и того же неизбежно разъезжаются: пользователь
        меняет PlayRes в свойствах документа, а в проекте остаётся старое.
        """
        return self.document.script_info.play_res

    def set_resolution(self, width: int, height: int) -> None:
        self.document.script_info.play_res_x = width
        self.document.script_info.play_res_y = height
        self.document.script_info.play_res_inferred = False

    @property
    def display_name(self) -> str:
        return self.name or (self.path.stem if self.path else tr('Без имени'))

    def snapshot(
        self,
        document: SubtitleDocument,
        media_path: Path | None,
        state: ProjectState,
    ) -> Project:
        """Копия проекта с текущим содержимым — для записи рядом.

        Нужна автосохранению: писать сам ``self`` нельзя, потому что запись
        проставляет ему путь и время правки, и проект начал бы считать своим
        файлом резервную копию. Документ при этом не копируется — он
        сериализуется сразу и переживать вызов не должен.
        """
        return Project(
            name=self.name,
            document=document,
            media_path=media_path,
            fps=self.fps,
            state=state,
            # Оригинал переносится в копию: без него восстановление из
            # автосохранения молча оставило бы переводчика без исходника.
            reference=self.reference,
            glossary=self.glossary,
            created=self.created,
            modified=self.modified,
        )

    @staticmethod
    def new(
        name: str,
        resolution: tuple[int, int] = (1920, 1080),
        fps: Fraction = Fraction(25),
        media_path: Path | None = None,
    ) -> Project:
        """Пустой проект с заданными параметрами."""
        document = SubtitleDocument.blank(resolution)
        now = now_stamp()
        return Project(
            name=name,
            document=document,
            media_path=media_path,
            fps=fps,
            created=now,
            modified=now,
        )


# --------------------------------------------------------------------------- #
# Разбор значений
#
# Живут здесь, а не в io: ими пользуется и разбор манифеста, и восстановление
# состояния из чего угодно, включая настройки.
# --------------------------------------------------------------------------- #


def now_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
