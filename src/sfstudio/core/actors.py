"""Акторы: кто произносит реплику.

В ASS для этого уже есть поле ``Name`` строки ``Dialogue:`` — оно и хранит имя
актора, и мы его читаем и пишем с самого начала. Не хватало только **реестра**:
списка действующих лиц с закреплённым за каждым цветом, чтобы строки в таблице
красились по говорящему и в длинном диалоге было видно, кто где.

Где это живёт в файле. Реестр пишется в ``[Script Info]`` одним ключом
``SFStudio Actors`` в виде JSON. Причины именно такие:

* ASS не имеет секции для действующих лиц, а изобретать свою — значит сломать
  файл для всех остальных программ;
* незнакомые ключи ``[Script Info]`` наш парсер (и Aegisub, и libass) сохраняет
  дословно и игнорирует, поэтому файл остаётся обычным ASS;
* JSON снимает вопрос экранирования: имя актора может содержать любые символы,
  включая запятые и двоеточия, на которых самодельный разделитель сломался бы.

Имя актора — ключ. Оно же лежит в ``event.name``, и отдельного идентификатора
у актора нет намеренно: иначе переименование пришлось бы разносить по всем
событиям, а при открытии чужого файла — сопоставлять неизвестно что с неизвестно
чем. Переименование актора здесь честно переписывает поле у его реплик.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace

from sfstudio.core.color import RGBA

__all__ = ["ACTOR_PALETTE", "Actor", "ActorRegistry"]

#: Ключ в ``[Script Info]``. С пробелом и префиксом — чтобы не столкнуться
#: с полями других программ.
INFO_KEY = "SFStudio Actors"

#: Цвета для новых акторов. Подобраны различимыми на тёмном фоне таблицы и
#: между собой; порядок — чтобы соседние по добавлению не сливались.
ACTOR_PALETTE: tuple[RGBA, ...] = (
    RGBA(0xE5, 0x7A, 0x44),  # тёплый оранжевый
    RGBA(0x5A, 0xB0, 0xE6),  # голубой
    RGBA(0x8B, 0xC7, 0x4A),  # зелёный
    RGBA(0xE0, 0x6C, 0x9A),  # розовый
    RGBA(0xC9, 0xA2, 0x27),  # охра
    RGBA(0x9B, 0x8B, 0xE0),  # лиловый
    RGBA(0x4A, 0xC0, 0xAE),  # бирюзовый
    RGBA(0xD9, 0x5A, 0x5A),  # красный
    RGBA(0x7F, 0xA8, 0x6E),  # приглушённый зелёный
    RGBA(0xB0, 0x8A, 0x6A),  # песочный
)


@dataclass(frozen=True, slots=True)
class Actor:
    """Действующее лицо."""

    name: str
    # default_factory, а не литерал: RGBA — вызов, и линтер справедливо
    # напоминает, что общий изменяемый объект в значении по умолчанию опасен.
    color: RGBA = field(default_factory=lambda: ACTOR_PALETTE[0])
    #: Свободная заметка — описание персонажа, особенности речи.
    note: str = ""

    def renamed(self, new_name: str) -> Actor:
        return replace(self, name=new_name)


class ActorRegistry:
    """Список акторов документа. Порядок — порядок добавления."""

    __slots__ = ("_actors",)

    def __init__(self, actors: Iterable[Actor] = ()) -> None:
        self._actors: dict[str, Actor] = {}
        for actor in actors:
            self._actors[actor.name] = actor

    # -- доступ -------------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self._actors)

    def __iter__(self) -> Iterator[Actor]:
        return iter(self._actors.values())

    def __contains__(self, name: str) -> bool:
        return name in self._actors

    def get(self, name: str) -> Actor | None:
        return self._actors.get(name)

    def names(self) -> list[str]:
        return list(self._actors)

    def color_of(self, name: str) -> RGBA | None:
        """Цвет актора или ``None``, если такого в реестре нет.

        ``None``, а не цвет по умолчанию: вызывающий код должен отличать
        «актор без цвета» от «актора нет», иначе строки неизвестных говорящих
        покрасятся так же, как заведённых, и смысл раскраски пропадёт.
        """
        actor = self._actors.get(name)
        return actor.color if actor is not None else None

    # -- изменение ----------------------------------------------------------- #

    def add(self, name: str, color: RGBA | None = None, note: str = "") -> Actor:
        """Добавляет актора. Цвет по умолчанию — следующий из палитры."""
        name = name.strip()
        if not name:
            raise ValueError("имя актора не может быть пустым")
        if name in self._actors:
            raise ValueError(f"актор {name!r} уже есть")
        actor = Actor(name=name, color=color or self.suggest_color(), note=note)
        self._actors[name] = actor
        return actor

    def put(self, actor: Actor) -> None:
        """Добавляет или заменяет — для команд отмены, где имя уже известно."""
        self._actors[actor.name] = actor

    def remove(self, name: str) -> Actor | None:
        return self._actors.pop(name, None)

    def rename(self, old: str, new: str) -> Actor:
        """Переименовывает, сохраняя позицию в списке.

        Позиция важна: цвета раздаются по порядку, и всплытие переименованного
        актора в конец списка меняло бы предлагаемый цвет следующему.
        """
        new = new.strip()
        if not new:
            raise ValueError("имя актора не может быть пустым")
        if old not in self._actors:
            raise KeyError(old)
        if new != old and new in self._actors:
            raise ValueError(f"актор {new!r} уже есть")

        rebuilt: dict[str, Actor] = {}
        for key, actor in self._actors.items():
            if key == old:
                rebuilt[new] = actor.renamed(new)
            else:
                rebuilt[key] = actor
        self._actors = rebuilt
        return self._actors[new]

    def set_color(self, name: str, color: RGBA) -> Actor:
        actor = self._actors[name]
        updated = replace(actor, color=color)
        self._actors[name] = updated
        return updated

    def suggest_color(self) -> RGBA:
        """Следующий неиспользованный цвет палитры, иначе — по кругу."""
        used = {actor.color for actor in self._actors.values()}
        for color in ACTOR_PALETTE:
            if color not in used:
                return color
        return ACTOR_PALETTE[len(self._actors) % len(ACTOR_PALETTE)]

    def copy(self) -> ActorRegistry:
        return ActorRegistry(self._actors.values())

    # -- сериализация --------------------------------------------------------- #

    def to_json(self) -> str:
        payload = [
            {"name": a.name, "color": a.color.to_hex(), **({"note": a.note} if a.note else {})}
            for a in self._actors.values()
        ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def from_json(text: str) -> ActorRegistry:
        """Разбирает реестр. Мусор даёт пустой реестр, а не исключение.

        Поле пишем не только мы: пользователь мог поправить файл руками, а
        другая программа — обрезать длинную строку. Терять из-за этого
        возможность открыть субтитры нельзя.
        """
        registry = ActorRegistry()
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return registry
        if not isinstance(payload, list):
            return registry

        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name in registry:
                continue
            try:
                color = RGBA.from_hex(str(item.get("color", "")))
            except ValueError:
                color = registry.suggest_color()
            registry.put(Actor(name=name, color=color, note=str(item.get("note", ""))))
        return registry
