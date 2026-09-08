"""Модель выделения.

Оперирует ``eid``, а не индексами строк: индексы меняются при сортировке,
вставке и удалении, а ``eid`` стабилен. Это единственный способ, при котором
выделение переживает undo и перестроение таблицы.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from sfstudio.core.changeset import ChangeSet
from sfstudio.core.document import SubtitleDocument

__all__ = ["SelectionModel"]


class SelectionModel:
    """Множество выделенных событий плюс «первичное» (в фокусе)."""

    __slots__ = ("_primary", "_selected", "on_change")

    def __init__(self) -> None:
        self._selected: set[int] = set()
        self._primary: int | None = None
        self.on_change: Callable[[], None] | None = None

    def __contains__(self, eid: int) -> bool:
        return eid in self._selected

    def __len__(self) -> int:
        return len(self._selected)

    def __iter__(self):
        return iter(self._selected)

    @property
    def primary(self) -> int | None:
        """Событие в фокусе — то, что показывают панель свойств и оверлей."""
        return self._primary

    @property
    def eids(self) -> frozenset[int]:
        return frozenset(self._selected)

    @property
    def is_empty(self) -> bool:
        return not self._selected

    # -- изменение ---------------------------------------------------------- #

    def select(self, eids: Iterable[int], *, primary: int | None = None) -> None:
        self._selected = set(eids)
        if primary is not None and primary in self._selected:
            self._primary = primary
        elif self._primary not in self._selected:
            self._primary = next(iter(self._selected), None)
        self._notify()

    def select_one(self, eid: int) -> None:
        self._selected = {eid}
        self._primary = eid
        self._notify()

    def add(self, eid: int) -> None:
        self._selected.add(eid)
        self._primary = eid
        self._notify()

    def toggle(self, eid: int) -> None:
        if eid in self._selected:
            self._selected.discard(eid)
            if self._primary == eid:
                self._primary = next(iter(self._selected), None)
        else:
            self._selected.add(eid)
            self._primary = eid
        self._notify()

    def clear(self) -> None:
        if not self._selected and self._primary is None:
            return
        self._selected.clear()
        self._primary = None
        self._notify()

    # -- реакция на изменения документа -------------------------------------- #

    def remap(self, changes: ChangeSet) -> None:
        """Убирает исчезнувшие события. Появившиеся не выделяет автоматически."""
        if not changes.removed_eids:
            return
        before = len(self._selected)
        self._selected -= changes.removed_eids
        if self._primary in changes.removed_eids:
            self._primary = next(iter(self._selected), None)
        if len(self._selected) != before:
            self._notify()

    def ordered_by_time(self, doc: SubtitleDocument) -> list[int]:
        present = [eid for eid in self._selected if doc.has(eid)]
        return sorted(present, key=lambda e: (doc.by_eid(e).start, e))

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()
