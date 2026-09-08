"""Единый реестр действий: меню, горячие клавиши, командная палитра.

Пока действия создаются россыпью по коду окна, конфликты сочетаний находятся
только руками — и находятся плохо: в исходной спецификации ``Ctrl+Shift+S``
одновременно значил «Сохранить как» и «Разбить событие», а ``Ctrl+Shift+P`` —
«Сбросить \\pos» и «Командная палитра». Реестр делает такие столкновения
видимыми: :func:`ActionRegistry.conflicts` проверяется тестом, и добавить
второе действие на занятое сочетание молча уже не выйдет.

Из того же реестра бесплатно получается командная палитра: список всех команд
с поиском по имени. Она заметно снижает нагрузку на меню — редкое действие
проще вспомнить по названию, чем найти в подменю третьего уровня.

**Контекст** нужен, чтобы одно сочетание могло значить разное в разных местах.
``Enter`` в таблице и в редакторе текста — законно разные действия, а вот два
глобальных действия на одном сочетании — всегда ошибка.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget

__all__ = ["ActionRegistry", "ActionSpec", "Conflict"]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Описание одного действия."""

    key: str
    title: str
    handler: Callable[[], None]
    shortcut: str = ""
    #: Где действует сочетание: ``global`` либо имя виджета-владельца.
    context: str = "global"
    menu: str = ""
    checkable: bool = False
    checked: bool = False
    #: Не показывать в командной палитре (например, служебные переключатели).
    hidden: bool = False
    tip: str = ""

    @property
    def searchable(self) -> str:
        return f"{self.title} {self.menu} {self.key}".lower()


@dataclass(frozen=True, slots=True)
class Conflict:
    """Два действия претендуют на одно сочетание в одном контексте."""

    shortcut: str
    context: str
    keys: tuple[str, ...]

    def __str__(self) -> str:
        where = "глобально" if self.context == "global" else f"в контексте «{self.context}»"
        return f"{self.shortcut} {where}: {', '.join(self.keys)}"


@dataclass
class ActionRegistry:
    """Хранит описания действий и создаёт из них ``QAction``."""

    _specs: dict[str, ActionSpec] = field(default_factory=dict)
    _actions: dict[str, QAction] = field(default_factory=dict)
    #: Сочетания как они заданы в коде. Нужны, чтобы «вернуть по умолчанию»
    #: возвращало именно к ним, а не к тому, что было до последней правки.
    _defaults: dict[str, str] = field(default_factory=dict)

    def register(self, spec: ActionSpec) -> ActionSpec:
        if spec.key in self._specs:
            raise ValueError(f"действие {spec.key!r} уже зарегистрировано")
        self._specs[spec.key] = spec
        self._defaults[spec.key] = spec.shortcut
        return spec

    def add(self, key: str, title: str, handler: Callable[[], None], **kwargs) -> ActionSpec:
        return self.register(ActionSpec(key=key, title=title, handler=handler, **kwargs))

    def extend(self, specs: Iterable[ActionSpec]) -> None:
        for spec in specs:
            self.register(spec)

    # -- доступ -------------------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self._specs)

    def __iter__(self) -> Iterator[ActionSpec]:
        return iter(self._specs.values())

    def __contains__(self, key: str) -> bool:
        return key in self._specs

    def spec(self, key: str) -> ActionSpec | None:
        return self._specs.get(key)

    def action(self, key: str) -> QAction | None:
        return self._actions.get(key)

    def specs(self) -> list[ActionSpec]:
        return list(self._specs.values())

    # -- пользовательские сочетания -------------------------------------------------- #

    def apply_overrides(self, overrides: dict[str, str]) -> list[str]:
        """Заменяет сочетания на заданные пользователем.

        Пустая строка означает «снять сочетание» — это осмысленный выбор, а
        не пропуск: команду, которую человек не хочет ловить случайным
        нажатием, надо уметь оставить только в меню.

        Возвращает ключи, которых в реестре нет: настройки переживают
        обновление программы, и команда из них могла исчезнуть — сообщить об
        этом полезнее, чем промолчать.
        """
        unknown: list[str] = []
        for key, shortcut in overrides.items():
            spec = self._specs.get(key)
            if spec is None:
                unknown.append(key)
                continue
            self._specs[key] = replace(spec, shortcut=str(shortcut or ""))
            action = self._actions.get(key)
            if action is not None:
                action.setShortcut(QKeySequence(str(shortcut or "")))
        return unknown

    def default_shortcuts(self) -> dict[str, str]:
        """Сочетания, заданные в коде, — чтобы было к чему возвращаться."""
        return dict(self._defaults)

    # -- проверка ------------------------------------------------------------------ #

    def conflicts(self) -> list[Conflict]:
        """Сочетания, назначенные больше чем одному действию в одном контексте.

        Сравнение идёт по нормализованному виду от Qt: ``Ctrl+Shift+S`` и
        ``ctrl+shift+s`` — одно и то же сочетание, и глазами такое расхождение
        не заметить.
        """
        buckets: dict[tuple[str, str], list[str]] = {}
        for spec in self._specs.values():
            if not spec.shortcut:
                continue
            normalized = QKeySequence(spec.shortcut).toString(QKeySequence.PortableText)
            buckets.setdefault((normalized, spec.context), []).append(spec.key)

        return [
            Conflict(shortcut=shortcut, context=context, keys=tuple(keys))
            for (shortcut, context), keys in sorted(buckets.items())
            if len(keys) > 1
        ]

    # -- создание QAction ----------------------------------------------------------- #

    def build(self, parent: QWidget) -> dict[str, QAction]:
        """Создаёт ``QAction`` для всех зарегистрированных действий."""
        for spec in self._specs.values():
            action = QAction(spec.title, parent)
            if spec.shortcut:
                action.setShortcut(QKeySequence(spec.shortcut))
            if spec.tip:
                action.setStatusTip(spec.tip)
            action.setCheckable(spec.checkable)
            action.setChecked(spec.checked)
            action.setData(spec.key)
            _connect(action, spec)
            self._actions[spec.key] = action
        return dict(self._actions)

    # -- поиск для палитры ------------------------------------------------------------ #

    def search(self, query: str, limit: int = 40) -> list[ActionSpec]:
        """Действия, подходящие под запрос.

        Совпадение подстрокой ранжируется выше, чем разрозненные буквы:
        человек, набравший «сдвиг», ждёт «Сдвиг таймингов» первым, а не
        случайное действие, где эти буквы разбросаны по названию.
        """
        text = query.strip().lower()
        visible = [s for s in self._specs.values() if not s.hidden]
        if not text:
            return visible[:limit]

        scored: list[tuple[int, int, ActionSpec]] = []
        for spec in visible:
            haystack = spec.searchable
            position = haystack.find(text)
            if position >= 0:
                scored.append((0, position, spec))
                continue
            if _subsequence(text, haystack):
                scored.append((1, len(haystack), spec))

        scored.sort(key=lambda item: (item[0], item[1], item[2].title.lower()))
        return [spec for _, _, spec in scored[:limit]]


def _connect(action: QAction, spec: ActionSpec) -> None:
    """Подключает обработчик.

    У переключаемых действий обработчик получает состояние, у обычных —
    вызывается без аргументов. Передавать флаг туда, где его не ждут, значит
    ловить ``TypeError`` в рантайме на каждом втором пункте меню.
    """
    if spec.checkable:
        action.toggled.connect(spec.handler)
    else:
        action.triggered.connect(lambda _checked=False: spec.handler())


def _subsequence(needle: str, haystack: str) -> bool:
    """Все буквы запроса встречаются по порядку — нестрогое совпадение."""
    iterator = iter(haystack)
    return all(char in iterator for char in needle)
