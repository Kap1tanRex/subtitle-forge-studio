"""Дельта изменений документа.

Каждая команда возвращает ``ChangeSet``. Подписчики (таблица, оверлей, таймлайн,
QC) обновляют **только затронутое**, а не перерисовывают всё: полный reset модели
таблицы на 20 000 строк стоит десятки миллисекунд и сбрасывает позицию прокрутки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

__all__ = ["ChangeSet"]


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Что именно изменилось."""

    changed_eids: frozenset[int] = field(default_factory=frozenset)
    added_eids: frozenset[int] = field(default_factory=frozenset)
    removed_eids: frozenset[int] = field(default_factory=frozenset)

    styles_changed: bool = False
    script_info_changed: bool = False
    #: Состав или свойства дорожек — заголовкам таймлайна нужна перерисовка.
    tracks_changed: bool = False
    #: Состав или цвета акторов — таблице нужно перекрасить строки целиком,
    #: а не только те, где сменилось имя говорящего.
    actors_changed: bool = False
    #: Изменился состав или порядок событий — модели нужен полный reset.
    structural: bool = False

    @property
    def is_empty(self) -> bool:
        return not (
            self.changed_eids
            or self.added_eids
            or self.removed_eids
            or self.styles_changed
            or self.script_info_changed
            or self.tracks_changed
            or self.actors_changed
            or self.structural
        )

    @property
    def touched(self) -> frozenset[int]:
        return self.changed_eids | self.added_eids | self.removed_eids

    def merged_with(self, other: ChangeSet) -> ChangeSet:
        """Объединение — для составных команд и транзакций."""
        return ChangeSet(
            changed_eids=self.changed_eids | other.changed_eids,
            added_eids=self.added_eids | other.added_eids,
            removed_eids=self.removed_eids | other.removed_eids,
            styles_changed=self.styles_changed or other.styles_changed,
            script_info_changed=self.script_info_changed or other.script_info_changed,
            tracks_changed=self.tracks_changed or other.tracks_changed,
            actors_changed=self.actors_changed or other.actors_changed,
            structural=self.structural or other.structural,
        )

    @staticmethod
    def changed(*eids: int) -> ChangeSet:
        return ChangeSet(changed_eids=frozenset(eids))

    @staticmethod
    def added(*eids: int) -> ChangeSet:
        return ChangeSet(added_eids=frozenset(eids), structural=True)

    @staticmethod
    def removed(*eids: int) -> ChangeSet:
        return ChangeSet(removed_eids=frozenset(eids), structural=True)

    @staticmethod
    def tracks() -> ChangeSet:
        return ChangeSet(tracks_changed=True)

    @staticmethod
    def actors(*eids: int) -> ChangeSet:
        return ChangeSet(actors_changed=True, changed_eids=frozenset(eids))

    #: Пустая дельта. ClassVar, а не поле: без этой пометки dataclass принял бы
    #: EMPTY за обязательное поле без значения по умолчанию.
    EMPTY: ClassVar[ChangeSet]


ChangeSet.EMPTY = ChangeSet()
