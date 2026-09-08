"""Стек отмены.

Хранит **дельты команд**, а не снимки документа. Лимит памяти считается по
суммарному ``size_hint`` команд; при превышении вытесняются самые старые шаги.

Транзакция складывает несколько команд в одну единицу отмены — так «сдвинуть
42 события» отменяется одним нажатием, а не сорока двумя.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands.base import Command, CompositeCommand
from sfstudio.core.document import SubtitleDocument

__all__ = ["UndoStack"]

DEFAULT_DEPTH = 200
DEFAULT_MEMORY_LIMIT = 64 * 1024 * 1024


class UndoStack:
    """Плоская история без ветвления."""

    __slots__ = (
        "_clean_index",
        "_depth",
        "_doc",
        "_done",
        "_memory_limit",
        "_txn",
        "_txn_label",
        "_undone",
        "on_change",
    )

    def __init__(
        self,
        doc: SubtitleDocument,
        *,
        depth: int = DEFAULT_DEPTH,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
    ) -> None:
        self._doc = doc
        self._depth = depth
        self._memory_limit = memory_limit
        self._done: list[Command] = []
        self._undone: list[Command] = []
        self._txn: list[Command] | None = None
        self._txn_label = ""
        #: Индекс в истории, соответствующий сохранённому состоянию файла.
        self._clean_index = 0
        #: Колбэк для UI. Синхронный: подписчику важно обновиться до отрисовки.
        self.on_change: Callable[[ChangeSet], None] | None = None

    # -- состояние ---------------------------------------------------------- #

    @property
    def can_undo(self) -> bool:
        return bool(self._done)

    @property
    def can_redo(self) -> bool:
        return bool(self._undone)

    @property
    def undo_label(self) -> str:
        return self._done[-1].label if self._done else ""

    @property
    def redo_label(self) -> str:
        return self._undone[-1].label if self._undone else ""

    @property
    def is_clean(self) -> bool:
        """True, если документ совпадает с последним сохранением."""
        return len(self._done) == self._clean_index

    def mark_clean(self) -> None:
        self._clean_index = len(self._done)

    @property
    def depth_used(self) -> int:
        return len(self._done)

    def memory_used(self) -> int:
        return sum(c.size_hint() for c in self._done)

    # -- выполнение --------------------------------------------------------- #

    def run(self, command: Command) -> ChangeSet:
        """Выполняет команду и кладёт её в историю."""
        changes = command.apply(self._doc)

        if self._txn is not None:
            self._txn.append(command)
            self._emit(changes)
            return changes

        self._undone.clear()

        # Слияние с предыдущей — набор текста и drag не должны засорять историю.
        if self._done:
            merged = command.coalesce_with(self._done[-1])
            if merged is not None:
                self._done[-1] = merged
                self._adjust_clean_after_coalesce()
                self._emit(changes)
                return changes

        self._done.append(command)
        self._trim()
        self._emit(changes)
        return changes

    def undo(self) -> ChangeSet:
        if not self._done:
            return ChangeSet.EMPTY
        command = self._done.pop()
        changes = command.revert(self._doc)
        self._undone.append(command)
        self._emit(changes)
        return changes

    def redo(self) -> ChangeSet:
        if not self._undone:
            return ChangeSet.EMPTY
        command = self._undone.pop()
        changes = command.apply(self._doc)
        self._done.append(command)
        self._emit(changes)
        return changes

    def clear(self) -> None:
        self._done.clear()
        self._undone.clear()
        self._clean_index = 0

    # -- транзакции --------------------------------------------------------- #

    @contextmanager
    def transaction(self, label: str) -> Iterator[None]:
        """Складывает все команды внутри блока в одну единицу отмены.

        Вложенные транзакции не поддерживаются намеренно: они делают семантику
        отката неочевидной. Попытка вложить — ошибка, а не тихое поведение.
        """
        if self._txn is not None:
            raise RuntimeError("вложенные транзакции не поддерживаются")
        self._txn = []
        self._txn_label = label
        try:
            yield
        except Exception:
            # Откатываем уже выполненное внутри неудавшейся транзакции.
            failed = self._txn
            self._txn = None
            for cmd in reversed(failed):
                cmd.revert(self._doc)
            raise
        else:
            collected = self._txn
            self._txn = None
            if not collected:
                return
            self._undone.clear()
            command = (
                collected[0]
                if len(collected) == 1
                else CompositeCommand(collected, label=self._txn_label)
            )
            if len(collected) == 1:
                command.label = self._txn_label
            self._done.append(command)
            self._trim()

    # -- служебное ---------------------------------------------------------- #

    def _trim(self) -> None:
        """Вытеснение по глубине и по памяти."""
        while len(self._done) > self._depth:
            self._done.pop(0)
            self._clean_index = max(0, self._clean_index - 1)

        if self._memory_limit <= 0:
            return
        used = self.memory_used()
        while used > self._memory_limit and len(self._done) > 1:
            used -= self._done[0].size_hint()
            self._done.pop(0)
            self._clean_index = max(0, self._clean_index - 1)

    def _adjust_clean_after_coalesce(self) -> None:
        """После слияния верхняя команда изменилась — «чистым» состояние быть перестало."""
        if self._clean_index == len(self._done):
            self._clean_index = -1

    def _emit(self, changes: ChangeSet) -> None:
        if self.on_change is not None and not changes.is_empty:
            self.on_change(changes)
