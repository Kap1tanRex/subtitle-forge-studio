"""Документ субтитров — единственный источник правды.

Мутируется **только через команды** (``core/commands``). Прямая запись в поля
из виджетов ломает undo, инкрементальный рендер и QC, поэтому запрещена
соглашением и проверяется на code review.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sfstudio.core.actors import ActorRegistry
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.index import TimeIndex
from sfstudio.core.style import SubtitleStyle
from sfstudio.core.tracks import TrackSet

__all__ = ["Attachment", "ScriptInfo", "SubtitleDocument"]


@dataclass(slots=True)
class ScriptInfo:
    """Секция ``[Script Info]``."""

    play_res_x: int = 1920
    play_res_y: int = 1080
    wrap_style: int = 0
    scaled_border_and_shadow: bool = True
    title: str = ""
    script_type: str = "v4.00+"
    ycbcr_matrix: str = "None"
    #: Незнакомые ключи заголовка — сохраняются дословно ради round-trip.
    extra: dict[str, str] = field(default_factory=dict)
    #: Комментарии (`;`) секции, в порядке появления, вместе с позицией.
    comments: list[tuple[int, str]] = field(default_factory=list)

    #: True, если PlayRes отсутствовал в файле и был подставлен нами.
    play_res_inferred: bool = False

    @property
    def play_res(self) -> tuple[int, int]:
        return self.play_res_x, self.play_res_y


@dataclass(slots=True)
class Attachment:
    """Вложение (шрифт, картинка) из ASS или MKV.

    Содержимое **не хранится в документе**: шрифт весом 10-20 МБ, размноженный
    по дельтам undo и по журналу восстановления, раздувает и память, и автосейвы.
    Байты лежат в файле кэша, здесь — только ссылка.
    """

    name: str
    mime: str = "application/octet-stream"
    source: str = "ass"  # 'ass' | 'mkv' | 'user'
    data_path: Path | None = None
    size: int = 0


class SubtitleDocument:
    """Стили + события + заголовок."""

    __slots__ = (
        "_by_eid",
        "_index",
        "_layout",
        "_next_eid",
        "_revision",
        "actors",
        "attachments",
        "events",
        "script_info",
        "source_encoding",
        "source_format",
        "source_path",
        "styles",
        "tracks",
    )

    def __init__(self) -> None:
        self.script_info = ScriptInfo()
        self.styles: dict[str, SubtitleStyle] = {}
        self.events: list[SubtitleEvent] = []
        self.attachments: list[Attachment] = []
        self.actors = ActorRegistry()
        self.tracks = TrackSet.default()

        #: Непрозрачные данные конкретного формата (порядок полей Format:,
        #: имя секции стилей) — нужны, чтобы запись дала тот же файл, что читали.
        self._layout: object | None = None

        self.source_path: Path | None = None
        self.source_format: str = "ass"
        self.source_encoding: str = "utf-8"

        self._next_eid = 1
        self._by_eid: dict[int, SubtitleEvent] = {}
        self._index = TimeIndex()
        self._revision = 0

    # -- идентификаторы ----------------------------------------------------- #

    def new_eid(self) -> int:
        eid = self._next_eid
        self._next_eid += 1
        return eid

    def reserve_eid(self, eid: int) -> None:
        """Учесть внешне заданный eid, чтобы счётчик не выдал его повторно."""
        if eid >= self._next_eid:
            self._next_eid = eid + 1

    # -- доступ ------------------------------------------------------------- #

    @property
    def revision(self) -> int:
        return self._revision

    def bump_revision(self) -> int:
        self._revision += 1
        return self._revision

    def by_eid(self, eid: int) -> SubtitleEvent:
        return self._by_eid[eid]

    def get(self, eid: int) -> SubtitleEvent | None:
        return self._by_eid.get(eid)

    def has(self, eid: int) -> bool:
        return eid in self._by_eid

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[SubtitleEvent]:
        return iter(self.events)

    def index_of(self, eid: int) -> int:
        for i, ev in enumerate(self.events):
            if ev.eid == eid:
                return i
        raise KeyError(eid)

    # -- изменение состава -------------------------------------------------- #

    def add_event(self, event: SubtitleEvent, at: int | None = None) -> None:
        self.reserve_eid(event.eid)
        if at is None:
            self.events.append(event)
        else:
            self.events.insert(at, event)
        self._by_eid[event.eid] = event
        self._index.mark_stale()

    def remove_event(self, eid: int) -> tuple[int, SubtitleEvent]:
        """Удаляет событие, возвращая ``(позиция, событие)`` для отмены."""
        pos = self.index_of(eid)
        event = self.events.pop(pos)
        del self._by_eid[eid]
        self._index.mark_stale()
        return pos, event

    def rebuild_lookup(self) -> None:
        """Полная пересборка карты eid → событие. После массовых операций."""
        self._by_eid = {e.eid: e for e in self.events}
        self._index.mark_stale()
        for event in self.events:
            self.reserve_eid(event.eid)

    def touch(self, _eid: int | None = None) -> None:
        """Сообщить документу, что тайминги события изменились."""
        self._index.mark_stale()

    # -- запросы по времени -------------------------------------------------- #

    @property
    def index(self) -> TimeIndex:
        self._index.ensure(self.events)
        return self._index

    def active_at(self, ms: int) -> list[SubtitleEvent]:
        return [self._by_eid[eid] for eid in self.index.active_at(ms)]

    def in_range(self, t0: int, t1: int) -> list[SubtitleEvent]:
        return [self._by_eid[eid] for eid in self.index.range(t0, t1)]

    def sorted_by_time(self) -> list[SubtitleEvent]:
        return sorted(self.events, key=lambda e: (e.start, e.eid))

    @property
    def duration(self) -> int:
        return max((e.end for e in self.events), default=0)

    # -- стили --------------------------------------------------------------- #

    def style_for(self, event: SubtitleEvent) -> SubtitleStyle:
        """Стиль события; при ссылке на несуществующий — ``Default`` или запасной.

        Молчаливый фолбэк намеренный: битая ссылка на стиль не должна ронять
        рендер. Сам факт ловится правилом QC ``missing_style``.
        """
        style = self.styles.get(event.style)
        if style is not None:
            return style
        default = self.styles.get("Default")
        if default is not None:
            return default
        if self.styles:
            return next(iter(self.styles.values()))
        fallback = SubtitleStyle()
        self.styles["Default"] = fallback
        return fallback

    def add_style(self, style: SubtitleStyle) -> None:
        self.styles[style.name] = style

    # -- дорожки и акторы ------------------------------------------------------ #

    def sync_tracks(self) -> None:
        """Заводит дорожки для слоёв, встреченных в событиях.

        Нужно после чтения файла и после любой массовой операции: слои задаёт
        файл, а описания дорожек в нём может не быть — его писала другая
        программа. Существующие дорожки не трогаются, лишние не удаляются:
        пустая дорожка это законное состояние, на неё просто ещё не положили
        реплик.
        """
        self.tracks.ensure_layers(event.layer for event in self.events)
        if not self.tracks.subtitles:
            self.tracks.ensure_layers([0])

    def events_on_layer(self, layer: int) -> list[SubtitleEvent]:
        return [event for event in self.events if event.layer == layer]

    def actor_color(self, event: SubtitleEvent):  # -> RGBA | None
        """Цвет актора реплики, если он заведён в реестре."""
        return self.actors.color_of(event.name) if event.name else None

    def actors_in_use(self) -> dict[str, int]:
        """Имена из реплик и сколько раз встретились — включая незаведённых.

        Незаведённые важны: файл мог прийти со стороны, и предложить занести
        встреченные имена в реестр можно только зная их.
        """
        counts: dict[str, int] = {}
        for event in self.events:
            if event.name:
                counts[event.name] = counts.get(event.name, 0) + 1
        return counts

    def styles_in_use(self) -> dict[str, int]:
        counts: dict[str, int] = dict.fromkeys(self.styles, 0)
        for event in self.events:
            counts[event.style] = counts.get(event.style, 0) + 1
        return counts

    # -- конструирование ------------------------------------------------------ #

    def create_event(self, start: int, end: int, text: str = "", **kwargs: object) -> SubtitleEvent:
        event = SubtitleEvent(eid=self.new_eid(), start=start, end=end, text=text, **kwargs)  # type: ignore[arg-type]
        if not event.style and self.styles:
            event.style = next(iter(self.styles))
        self.add_event(event)
        return event

    @classmethod
    def blank(cls, play_res: tuple[int, int] = (1920, 1080)) -> SubtitleDocument:
        doc = cls()
        doc.script_info.play_res_x, doc.script_info.play_res_y = play_res
        doc.add_style(SubtitleStyle())
        return doc

    def extend(self, events: Iterable[SubtitleEvent]) -> None:
        for event in events:
            self.add_event(event)
