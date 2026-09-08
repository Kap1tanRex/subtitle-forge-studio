"""Команды над акторами.

Имя актора живёт в двух местах сразу: в реестре документа и в поле ``Name``
каждой его реплики. Поэтому переименование — операция над обоими: правка только
реестра оставила бы реплики привязанными к исчезнувшему имени, и они молча
потеряли бы цвет. Обе части идут одной командой, то есть отменяются вместе.
"""

from __future__ import annotations

from dataclasses import replace

from sfstudio.core.actors import Actor
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.color import RGBA
from sfstudio.core.commands.base import Command
from sfstudio.core.document import SubtitleDocument

__all__ = ["AddActor", "AssignActor", "RemoveActor", "RenameActor", "UpdateActor"]


class AddActor(Command):
    """Заводит актора в реестре."""

    __slots__ = ("_added", "color", "label", "name", "note")

    def __init__(self, name: str, color: RGBA | None = None, note: str = "") -> None:
        self.name = name.strip()
        self.color = color
        self.note = note
        self._added: Actor | None = None
        self.label = "Новый актор"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if not self.name or self.name in doc.actors:
            return ChangeSet.EMPTY
        # Цвет фиксируется при первом применении: иначе повтор после отмены
        # выдал бы другой цвет, и «вернуть» перекрасило бы строки.
        if self.color is None:
            self.color = doc.actors.suggest_color()
        self._added = doc.actors.add(self.name, self.color, self.note)
        doc.bump_revision()
        return ChangeSet.actors()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if self._added is None:
            return ChangeSet.EMPTY
        doc.actors.remove(self._added.name)
        doc.bump_revision()
        return ChangeSet.actors()


class RemoveActor(Command):
    """Убирает актора из реестра. Реплики сохраняют имя в поле ``Name``.

    Имя намеренно остаётся: удаление из реестра — это «перестать выделять его
    цветом», а не «стереть, кто это говорит». Стирать текстовые данные молча,
    вместе с настройкой отображения, недопустимо.
    """

    __slots__ = ("_removed", "label", "name")

    def __init__(self, name: str) -> None:
        self.name = name
        self._removed: Actor | None = None
        self.label = "Удаление актора"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        self._removed = doc.actors.remove(self.name)
        if self._removed is None:
            return ChangeSet.EMPTY
        doc.bump_revision()
        return ChangeSet.actors()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if self._removed is None:
            return ChangeSet.EMPTY
        doc.actors.put(self._removed)
        doc.bump_revision()
        return ChangeSet.actors()


class RenameActor(Command):
    """Переименовывает актора и его реплики одной операцией."""

    __slots__ = ("_touched", "label", "new_name", "old_name")

    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name.strip()
        self._touched: list[int] = []
        self.label = "Переименование актора"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if not self.new_name or self.old_name not in doc.actors:
            return ChangeSet.EMPTY
        if self.new_name != self.old_name and self.new_name in doc.actors:
            return ChangeSet.EMPTY

        doc.actors.rename(self.old_name, self.new_name)
        self._touched = [e.eid for e in doc.events if e.name == self.old_name]
        for eid in self._touched:
            doc.by_eid(eid).name = self.new_name
        doc.bump_revision()
        return ChangeSet.actors(*self._touched)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if self.new_name not in doc.actors:
            return ChangeSet.EMPTY
        doc.actors.rename(self.new_name, self.old_name)
        for eid in self._touched:
            event = doc.get(eid)
            if event is not None:
                event.name = self.old_name
        doc.bump_revision()
        return ChangeSet.actors(*self._touched)

    def size_hint(self) -> int:
        return 128 + 8 * len(self._touched)


class UpdateActor(Command):
    """Меняет цвет или заметку актора, не трогая имя."""

    __slots__ = ("_before", "color", "label", "name", "note")

    def __init__(self, name: str, color: RGBA | None = None, note: str | None = None) -> None:
        self.name = name
        self.color = color
        self.note = note
        self._before: Actor | None = None
        self.label = "Правка актора"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        current = doc.actors.get(self.name)
        if current is None:
            return ChangeSet.EMPTY
        self._before = current
        doc.actors.put(
            replace(
                current,
                color=self.color if self.color is not None else current.color,
                note=self.note if self.note is not None else current.note,
            )
        )
        doc.bump_revision()
        return ChangeSet.actors()

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if self._before is None:
            return ChangeSet.EMPTY
        doc.actors.put(self._before)
        doc.bump_revision()
        return ChangeSet.actors()

    def coalesce_with(self, previous: Command) -> Command | None:
        """Слияние: перетаскивание ползунка цвета — один шаг отмены."""
        if not isinstance(previous, UpdateActor) or previous.name != self.name:
            return None
        merged = UpdateActor(
            self.name,
            color=self.color if self.color is not None else previous.color,
            note=self.note if self.note is not None else previous.note,
        )
        merged._before = previous._before
        return merged


class AssignActor(Command):
    """Назначает актора нескольким репликам разом.

    Для одной реплики есть :class:`~sfstudio.core.commands.text.SetActor` —
    он умеет сливаться при правке поля в таблице. Эта команда для другого
    случая: выделили десяток строк и назначили говорящего всем.

    Пустое имя снимает назначение — это отдельный осмысленный случай, а не
    «ничего не делать»: реплику без говорящего надо уметь вернуть в это
    состояние.
    """

    __slots__ = ("_before", "eids", "label", "name")

    def __init__(self, eids: list[int], name: str) -> None:
        self.eids = eids
        self.name = name.strip()
        self._before: list[tuple[int, str]] = []
        self.label = "Назначение актора" if self.name else "Снятие актора"

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        self._before = []
        for eid in self.eids:
            event = doc.get(eid)
            if event is None:
                continue
            self._before.append((eid, event.name))
            event.name = self.name
        doc.bump_revision()
        return ChangeSet.changed(*(eid for eid, _ in self._before))

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        for eid, name in self._before:
            event = doc.get(eid)
            if event is not None:
                event.name = name
        doc.bump_revision()
        return ChangeSet.changed(*(eid for eid, _ in self._before))

    def size_hint(self) -> int:
        return 64 + 32 * len(self.eids)
