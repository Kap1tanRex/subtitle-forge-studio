"""Команды работы со стилями документа."""

from __future__ import annotations

from dataclasses import replace

from sfstudio.app.i18n import tr
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands.base import Command
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.style import SubtitleStyle

__all__ = ["ApplyStyleToEvents", "CreateStyle", "DeleteStyle", "RenameStyle", "UpdateStyle"]


class UpdateStyle(Command):
    """Заменяет параметры существующего стиля.

    Меняет вид сразу у всех событий, которые на него ссылаются, — поэтому
    ``ChangeSet`` помечается как затрагивающий стили: перерисовать нужно всё,
    а не отдельные строки.
    """

    __slots__ = ("_before", "label", "name", "new_style")

    def __init__(self, name: str, new_style: SubtitleStyle) -> None:
        self.name = name
        # Имя стиля не меняем: на него ссылаются события.
        self.new_style = replace(new_style, name=name)
        self._before: SubtitleStyle | None = None
        self.label = tr('Изменить стиль «{0}»').format(name)

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if self._before is None:
            existing = doc.styles.get(self.name)
            self._before = replace(existing) if existing else None
        doc.styles[self.name] = replace(self.new_style)
        doc.bump_revision()
        return ChangeSet(styles_changed=True)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if self._before is None:
            doc.styles.pop(self.name, None)
        else:
            doc.styles[self.name] = replace(self._before)
        doc.bump_revision()
        return ChangeSet(styles_changed=True)

    def coalesce_with(self, previous):
        """Сливает подряд идущие правки одного стиля в один шаг отмены.

        Оформление настраивают ползунками, и каждое их движение — отдельная
        правка стиля. Без слияния десяток движений кегля превращается в
        десяток шагов истории, из которой потом не выбраться: «Отменить»
        приходится жать столько же раз, сколько двигали мышью.

        Сливается только правка **того же** стиля: соседний шаг про другой
        стиль — это другое действие, и склеивать их значило бы отменять два
        изменения одним нажатием.
        """
        if not isinstance(previous, UpdateStyle) or previous.name != self.name:
            return None
        merged = UpdateStyle(self.name, self.new_style)
        # Возвращать надо к самому первому состоянию: середина жеста человека
        # не интересует, ему нужен вид «до того, как я начал крутить».
        merged._before = previous._before
        return merged


class CreateStyle(Command):
    """Добавляет новый стиль в документ."""

    __slots__ = ("label", "style")

    def __init__(self, style: SubtitleStyle) -> None:
        self.style = style
        self.label = tr('Новый стиль «{0}»').format(style.name)

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        doc.styles[self.style.name] = replace(self.style)
        doc.bump_revision()
        return ChangeSet(styles_changed=True)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        doc.styles.pop(self.style.name, None)
        doc.bump_revision()
        return ChangeSet(styles_changed=True)


class DeleteStyle(Command):
    """Удаляет стиль, переназначая ссылающиеся события.

    Просто убрать стиль нельзя: события продолжат на него ссылаться, и рендер
    молча свалится на ``Default``. Поэтому события переводятся на явно
    указанный стиль, и это же поведение отменяется целиком.
    """

    __slots__ = ("_moved", "_removed", "fallback", "label", "name")

    def __init__(self, name: str, fallback: str = "Default") -> None:
        self.name = name
        self.fallback = fallback
        self._removed: SubtitleStyle | None = None
        self._moved: list[int] = []
        self.label = tr('Удалить стиль «{0}»').format(name)

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        self._removed = replace(doc.styles[self.name]) if self.name in doc.styles else None
        doc.styles.pop(self.name, None)
        self._moved = [e.eid for e in doc.events if e.style == self.name]
        for eid in self._moved:
            doc.by_eid(eid).style = self.fallback
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self._moved), styles_changed=True)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        if self._removed is not None:
            doc.styles[self.name] = replace(self._removed)
        for eid in self._moved:
            doc.by_eid(eid).style = self.name
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self._moved), styles_changed=True)


class RenameStyle(Command):
    """Переименовывает стиль и переводит на него ссылающиеся события."""

    __slots__ = ("_moved", "label", "new_name", "old_name")

    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name
        self._moved: list[int] = []
        self.label = tr('Переименовать стиль в «{0}»').format(new_name)

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        style = doc.styles.pop(self.old_name, None)
        if style is None:
            return ChangeSet.EMPTY
        doc.styles[self.new_name] = replace(style, name=self.new_name)
        self._moved = [e.eid for e in doc.events if e.style == self.old_name]
        for eid in self._moved:
            doc.by_eid(eid).style = self.new_name
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self._moved), styles_changed=True)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        style = doc.styles.pop(self.new_name, None)
        if style is not None:
            doc.styles[self.old_name] = replace(style, name=self.old_name)
        for eid in self._moved:
            doc.by_eid(eid).style = self.old_name
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self._moved), styles_changed=True)


class ApplyStyleToEvents(Command):
    """Назначает стиль выделенным событиям."""

    __slots__ = ("_before", "eids", "label", "style_name")

    def __init__(self, eids: list[int], style_name: str) -> None:
        self.eids = eids
        self.style_name = style_name
        self._before: dict[int, str] = {}
        self.label = tr('Стиль «{0}» для {1} событий').format(style_name, len(eids))

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        if not self._before:
            self._before = {eid: doc.by_eid(eid).style for eid in self.eids}
        for eid in self.eids:
            doc.by_eid(eid).style = self.style_name
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self.eids))

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        for eid, name in self._before.items():
            doc.by_eid(eid).style = name
        doc.bump_revision()
        return ChangeSet(changed_eids=frozenset(self.eids))

    def size_hint(self) -> int:
        return 64 + 32 * len(self.eids)
