"""Дорожки таймлайна.

**Дорожка субтитров — это ``layer`` события ASS, а не наша надстройка.** Решение
принципиальное, поэтому объясняю. В ASS у каждой реплики есть целочисленный
``Layer``: при наложении реплик та, у которой номер больше, рисуется поверх.
Это ровно семантика дорожки в монтажном столе — и она уже сохраняется в файле
любым плеером и редактором. Если бы дорожки были отдельной сущностью «сбоку»,
получилось бы два независимых понятия порядка: наше и настоящее, — и они бы
разъезжались при каждом открытии файла в другой программе.

Что из этого следует:

* перенос реплики на другую дорожку меняет её ``layer`` — и это правильно
  отражается на наложении в кадре;
* дорожка не может «потеряться»: открыли чужой файл — дорожки восстановлены
  из встреченных ``layer``;
* номера дорожек не обязаны идти подряд. Файл может содержать ``layer`` 0 и 5,
  и придумывать несуществующие дорожки между ними мы не будем.

Имя, цвет и замок дорожки в ASS хранить негде, поэтому они, как и акторы,
уезжают в ``[Script Info]`` одним ключом JSON — см. :mod:`sfstudio.core.actors`,
там та же схема и те же причины.

Видео и звук — дорожки особого рода. Их нельзя создать или удалить: они
отражают открытый медиафайл, и было бы обманом рисовать кнопку «добавить
видеодорожку» в редакторе субтитров, где видео ровно одно. Они всегда внизу,
как в монтажных программах.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from enum import StrEnum

from sfstudio.core.color import RGBA

__all__ = ["TRACK_PALETTE", "Track", "TrackKind", "TrackSet"]

INFO_KEY = "SFStudio Tracks"

#: Приглушённые цвета: дорожка красит фон полосы, а не саму реплику,
#: и не должна перебивать цвет актора на ней.
TRACK_PALETTE: tuple[RGBA, ...] = (
    RGBA(0x4A, 0x6E, 0x8A),
    RGBA(0x6E, 0x5A, 0x8A),
    RGBA(0x4A, 0x8A, 0x6E),
    RGBA(0x8A, 0x6E, 0x4A),
    RGBA(0x8A, 0x4A, 0x5E),
    RGBA(0x5E, 0x7A, 0x4A),
)


class TrackKind(StrEnum):
    SUBTITLE = "subtitle"
    VIDEO = "video"
    AUDIO = "audio"


@dataclass(slots=True)
class Track:
    """Одна дорожка.

    ``layer`` осмыслен только для субтитров; у видео и звука он равен -1,
    потому что к слоям ASS они отношения не имеют.
    """

    kind: TrackKind
    layer: int = -1
    name: str = ""
    color: RGBA = field(default_factory=lambda: TRACK_PALETTE[0])
    #: Рисовать ли реплики дорожки в превью.
    visible: bool = True
    #: Запрет правки: реплики не двигаются мышью и не выделяются на таймлайне.
    locked: bool = False
    #: Только для звука.
    muted: bool = False
    #: Высота полосы в пикселях; пользователь тянет границу мышью.
    height: int = 0

    @property
    def is_subtitle(self) -> bool:
        return self.kind is TrackKind.SUBTITLE

    @property
    def key(self) -> str:
        """Стабильный ключ: у субтитров — слой, у прочих — род дорожки."""
        return f"sub:{self.layer}" if self.is_subtitle else self.kind.value

    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.kind is TrackKind.VIDEO:
            return "Видео"
        if self.kind is TrackKind.AUDIO:
            return "Звук"
        return f"Субтитры {self.layer}"


@dataclass(slots=True)
class TrackSet:
    """Дорожки документа.

    Хранит только дорожки субтитров: видео и звук постоянны и выдаются
    :meth:`display_order` на лету, чтобы их нельзя было случайно удалить
    или размножить.
    """

    subtitles: list[Track] = field(default_factory=list)
    video: Track = field(default_factory=lambda: Track(kind=TrackKind.VIDEO))
    audio: Track = field(default_factory=lambda: Track(kind=TrackKind.AUDIO))

    # -- построение ------------------------------------------------------------ #

    @staticmethod
    def default() -> TrackSet:
        """Один слой 0 — состояние обычного файла субтитров."""
        return TrackSet(subtitles=[Track(kind=TrackKind.SUBTITLE, layer=0,
                                         color=TRACK_PALETTE[0])])

    def ensure_layers(self, layers: Iterable[int]) -> list[Track]:
        """Заводит дорожки для слоёв, которых ещё нет. Возвращает добавленные.

        Вызывается после чтения файла: слои в нём заданы репликами, а описания
        дорожек может не быть вовсе — файл писала другая программа.
        """
        added: list[Track] = []
        known = {track.layer for track in self.subtitles}
        for layer in sorted(set(layers)):
            if layer in known:
                continue
            track = Track(
                kind=TrackKind.SUBTITLE,
                layer=layer,
                color=TRACK_PALETTE[len(self.subtitles) % len(TRACK_PALETTE)],
            )
            self.subtitles.append(track)
            known.add(layer)
            added.append(track)
        self.subtitles.sort(key=lambda t: t.layer)
        return added

    # -- доступ ---------------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self.subtitles)

    def __iter__(self) -> Iterator[Track]:
        return iter(self.subtitles)

    def by_layer(self, layer: int) -> Track | None:
        return next((t for t in self.subtitles if t.layer == layer), None)

    def layers(self) -> list[int]:
        return [t.layer for t in self.subtitles]

    def next_layer(self) -> int:
        """Номер для новой дорожки — на единицу выше самой верхней."""
        return max((t.layer for t in self.subtitles), default=-1) + 1

    def display_order(self, *, with_media: bool = True) -> list[Track]:
        """Дорожки сверху вниз, как в монтажном столе.

        Субтитры сверху и по убыванию слоя (верхняя рисуется поверх), под ними
        видео, в самом низу звук.
        """
        rows = sorted(self.subtitles, key=lambda t: t.layer, reverse=True)
        if with_media:
            rows = [*rows, self.video, self.audio]
        return rows

    def locked_layers(self) -> frozenset[int]:
        return frozenset(t.layer for t in self.subtitles if t.locked)

    def hidden_layers(self) -> frozenset[int]:
        return frozenset(t.layer for t in self.subtitles if not t.visible)

    # -- изменение -------------------------------------------------------------- #

    def add(self, name: str = "", layer: int | None = None) -> Track:
        """Добавляет дорожку субтитров."""
        target = self.next_layer() if layer is None else layer
        if self.by_layer(target) is not None:
            raise ValueError(f"дорожка со слоем {target} уже есть")
        track = Track(
            kind=TrackKind.SUBTITLE,
            layer=target,
            name=name,
            color=TRACK_PALETTE[len(self.subtitles) % len(TRACK_PALETTE)],
        )
        self.subtitles.append(track)
        self.subtitles.sort(key=lambda t: t.layer)
        return track

    def put(self, track: Track) -> None:
        """Вставляет или заменяет дорожку — для отката команд."""
        self.subtitles = [t for t in self.subtitles if t.layer != track.layer]
        self.subtitles.append(track)
        self.subtitles.sort(key=lambda t: t.layer)

    def remove(self, layer: int) -> Track | None:
        """Убирает дорожку. События не трогает — это забота команды.

        Последнюю дорожку убрать нельзя: документ без единой дорожки нельзя
        показать, и добавить реплику стало бы некуда.
        """
        if len(self.subtitles) <= 1:
            return None
        track = self.by_layer(layer)
        if track is None:
            return None
        self.subtitles = [t for t in self.subtitles if t.layer != layer]
        return track

    def copy(self) -> TrackSet:
        return TrackSet(
            subtitles=[replace(t) for t in self.subtitles],
            video=replace(self.video),
            audio=replace(self.audio),
        )

    # -- сериализация ------------------------------------------------------------ #

    def to_json(self) -> str:
        payload = []
        for track in self.subtitles:
            item: dict[str, object] = {"layer": track.layer, "color": track.color.to_hex()}
            if track.name:
                item["name"] = track.name
            if not track.visible:
                item["hidden"] = True
            if track.locked:
                item["locked"] = True
            if track.height:
                item["height"] = track.height
            payload.append(item)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def from_json(text: str) -> TrackSet:
        """Разбирает описание дорожек; мусор даёт пустой набор, не исключение."""
        result = TrackSet(subtitles=[])
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return result
        if not isinstance(payload, list):
            return result

        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                layer = int(item["layer"])
            except (KeyError, TypeError, ValueError):
                continue
            if result.by_layer(layer) is not None:
                continue
            try:
                color = RGBA.from_hex(str(item.get("color", "")))
            except ValueError:
                color = TRACK_PALETTE[len(result.subtitles) % len(TRACK_PALETTE)]
            result.subtitles.append(
                Track(
                    kind=TrackKind.SUBTITLE,
                    layer=layer,
                    name=str(item.get("name", "")),
                    color=color,
                    visible=not bool(item.get("hidden", False)),
                    locked=bool(item.get("locked", False)),
                    height=int(item.get("height", 0) or 0),
                )
            )
        result.subtitles.sort(key=lambda t: t.layer)
        return result
