"""Базовый контракт команды.

Команда хранит **дельту** (значения «до» и «после»), а не снимок документа.
Снимок события весит сотни байт, дельта — десятки; на истории в 200 шагов
разница определяет, укладываемся мы в лимит памяти или нет.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sfstudio.app.i18n import tr
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.document import SubtitleDocument

__all__ = ["Command", "CompositeCommand"]


class Command(ABC):
    """Атомарное изменение документа."""

    #: Текст для меню «Отменить: …»
    label: str = tr('Изменение')

    @abstractmethod
    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        """Применить. Обязана сохранить всё нужное для точного отката."""

    @abstractmethod
    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        """Откатить, вернув документ ровно в состояние до ``apply``."""

    def coalesce_with(self, previous: Command) -> Command | None:
        """Слияние с предыдущей командой.

        Возвращает объединённую команду или ``None``, если сливать нельзя.
        Используется для набора текста (одна отмена на слово, а не на букву)
        и для перетаскивания (одна отмена на жест).
        """
        return None

    def size_hint(self) -> int:
        """Приблизительный вес в байтах — для лимита памяти стека отмены."""
        return 128


class CompositeCommand(Command):
    """Несколько команд как одна единица отмены."""

    __slots__ = ("commands", "label")

    def __init__(self, commands: list[Command], label: str = tr('Изменение')) -> None:
        self.commands = commands
        self.label = label

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        result = ChangeSet.EMPTY
        for cmd in self.commands:
            result = result.merged_with(cmd.apply(doc))
        return result

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        result = ChangeSet.EMPTY
        # Откат в обратном порядке — иначе индексы вставки/удаления разъедутся.
        for cmd in reversed(self.commands):
            result = result.merged_with(cmd.revert(doc))
        return result

    def size_hint(self) -> int:
        return sum(c.size_hint() for c in self.commands)

    def __len__(self) -> int:
        return len(self.commands)
