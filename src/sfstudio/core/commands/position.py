"""Команды визуального позиционирования — то, что пишет drag мышью по кадру.

Все они сводятся к точечной правке одного событийного тега через
:mod:`sfstudio.core.tags`, поэтому остальной текст и порядок прочих тегов
остаются нетронутыми.
"""

from __future__ import annotations

from sfstudio.app.i18n import tr
from sfstudio.core import tags as tagmod
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands.base import Command
from sfstudio.core.document import SubtitleDocument

__all__ = ["ClearPosition", "SetAlignment", "SetOverrideTag", "SetPosition", "SetRotation"]


class SetOverrideTag(Command):
    """Ставит или убирает один событийный тег.

    ``args is None`` означает удаление. Это единственная команда, через которую
    оверлей меняет ``\\pos``, ``\\an``, ``\\frz`` и прочее.
    """

    __slots__ = ("_before", "_had", "args", "eid", "label", "name")

    def __init__(self, eid: int, name: str, args: object, *, label: str | None = None) -> None:
        self.eid = eid
        self.name = name
        self.args = args
        self._before: str | None = None
        self._had = False
        self.label = label or tr('Тег \\{0}').format(name)

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        event = doc.by_eid(self.eid)
        if self._before is None:
            self._before = event.text
        if self.args is None:
            event.set_text(tagmod.remove_event_tag(event.text, self.name))
        else:
            event.set_text(tagmod.set_event_tag(event.text, self.name, self.args))
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._before is not None, tr('revert до apply')
        doc.by_eid(self.eid).set_text(self._before)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def coalesce_with(self, previous: Command) -> Command | None:
        """Жест мыши = одна отмена, независимо от числа промежуточных кадров."""
        if not isinstance(previous, SetOverrideTag):
            return None
        if previous.eid != self.eid or previous.name != self.name:
            return None
        merged = SetOverrideTag(self.eid, self.name, self.args, label=previous.label)
        merged._before = previous._before
        return merged

    def size_hint(self) -> int:
        return 64 + len(self._before or "")


class SetPosition(SetOverrideTag):
    """``\\pos(x, y)``.

    Координаты округляются до целых: спецификация ASS предполагает целые,
    и разные плееры округляют дробные по-разному. Ручной ввод дробного
    значения проходит через ``SetOverrideTag`` напрямую и не округляется.
    """

    def __init__(self, eid: int, x: float, y: float) -> None:
        super().__init__(eid, "pos", (round(x), round(y)), label=tr('Перемещение'))


class ClearPosition(SetOverrideTag):
    """Убирает ``\\pos`` — событие возвращается к позиционированию по стилю."""

    def __init__(self, eid: int) -> None:
        super().__init__(eid, "pos", None, label=tr('Сброс позиции'))


class SetRotation(SetOverrideTag):
    """``\\frz`` — поворот вокруг оси Z.

    В ASS угол отсчитывается против часовой стрелки, в отличие от экранных
    координат. Преобразование знака делает вызывающий код (оверлей), сюда
    приходит уже значение в нотации ASS.
    """

    def __init__(self, eid: int, degrees: float) -> None:
        normalized = round(degrees % 360.0, 2)
        super().__init__(eid, "frz", normalized, label=tr('Поворот'))


class SetAlignment(Command):
    """``\\an`` со сохранением визуального положения.

    Смена выравнивания меняет точку привязки, и текст с ``\\pos`` «прыгает».
    Профессиональное поведение — оставить его на месте, пересчитав ``\\pos``
    под новый якорь. Новая позиция вычисляется вызывающим кодом (у него есть
    bbox от libass) и передаётся сюда; если её нет, ``\\pos`` не трогается.
    """

    __slots__ = ("_before", "eid", "label", "new_an", "new_pos")

    def __init__(self, eid: int, an: int, new_pos: tuple[float, float] | None = None) -> None:
        if not 1 <= an <= 9:
            raise ValueError(tr('\\an вне диапазона 1..9: {0}').format(an))
        self.eid = eid
        self.new_an = an
        self.new_pos = new_pos
        self._before: str | None = None
        self.label = tr('Выравнивание')

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        event = doc.by_eid(self.eid)
        if self._before is None:
            self._before = event.text
        text = tagmod.set_event_tag(event.text, "an", self.new_an)
        if self.new_pos is not None:
            x, y = self.new_pos
            text = tagmod.set_event_tag(text, "pos", (round(x), round(y)))
        event.set_text(text)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._before is not None, tr('revert до apply')
        doc.by_eid(self.eid).set_text(self._before)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)


class SetOverrideTags(Command):
    """Ставит несколько событийных тегов **одной** операцией.

    Нужна там, где свойство физически состоит из пары: масштаб — это всегда
    ``\fscx`` и ``\fscy`` вместе. Двумя отдельными командами такой жест
    ложится в историю двумя шагами, и отмена по Escape возвращает половину
    изменения — текст остаётся растянутым по одной оси.

    Слияние работает по **набору имён**: перетаскивание ручки — один шаг
    отмены, сколько бы кадров мышь ни прошла.
    """

    __slots__ = ("_before", "eid", "label", "tags")

    def __init__(
        self, eid: int, tags: dict[str, object], *, label: str | None = None
    ) -> None:
        self.eid = eid
        self.tags = dict(tags)
        self._before: str | None = None
        self.label = label or tr('Теги реплики')

    def apply(self, doc: SubtitleDocument) -> ChangeSet:
        event = doc.by_eid(self.eid)
        if self._before is None:
            self._before = event.text
        text = event.text
        for name, args in self.tags.items():
            if args is None:
                text = tagmod.remove_event_tag(text, name)
            else:
                text = tagmod.set_event_tag(text, name, args)
        event.set_text(text)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def revert(self, doc: SubtitleDocument) -> ChangeSet:
        assert self._before is not None, tr('revert до apply')
        doc.by_eid(self.eid).set_text(self._before)
        doc.bump_revision()
        return ChangeSet.changed(self.eid)

    def coalesce_with(self, previous: Command) -> Command | None:
        if not isinstance(previous, SetOverrideTags):
            return None
        if previous.eid != self.eid or set(previous.tags) != set(self.tags):
            return None
        merged = SetOverrideTags(self.eid, self.tags, label=previous.label)
        merged._before = previous._before
        return merged
