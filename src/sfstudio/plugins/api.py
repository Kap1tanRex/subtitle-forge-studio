"""Контракт плагинов: что программа обещает, а что просит взамен.

Плагин — это папка с манифестом и модулем Python. Модуль обязан определить
функцию ``setup(context)``; всё остальное он делает через переданный
:class:`PluginContext`, а не напрямую по внутренностям программы.

Почему через контекст, а не «импортируй что хочешь». Плагин, добравшийся до
внутренних объектов, ломается при первом же их изменении — а меняются они
постоянно, это обычная жизнь кода. Контекст же меняется редко и осознанно:
у него есть номер версии, и плагин, рассчитанный на другой, отказывается
загружаться с внятным объяснением вместо падения на середине работы.

Точки расширения выбраны по тому, что люди действительно хотят добавлять:

* **движок распознавания** — их много, все разные, и вкладывать в поставку
  каждый невозможно;
* **формат субтитров** — форматов десятки, и хвост из редких никому не нужен
  в основной программе;
* **проверка качества** — правила у студий свои и часто закрытые;
* **действие в меню** — всё остальное: экспорт по своему шаблону, выгрузка в
  чужую систему, массовая правка по своим правилам.

Ошибки плагинов **не роняют программу**. Плагин пишет третья сторона, и
считать его код надёжным нельзя; любое исключение при загрузке или в
обработчике превращается в запись о неисправном плагине.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sfstudio.app.i18n import tr

__all__ = [
    "API_VERSION",
    "MANIFEST_NAME",
    "Plugin",
    "PluginAction",
    "PluginContext",
    "PluginError",
    "PluginInfo",
    "PluginState",
]

#: Версия контракта. Повышается, когда меняется смысл уже существующего —
#: добавление новой точки расширения версию не двигает: плагины, о ней не
#: знающие, продолжают работать.
API_VERSION = 1

#: Имя файла манифеста внутри папки плагина.
MANIFEST_NAME = "plugin.json"


class PluginError(RuntimeError):
    """Плагин не удалось загрузить или он повёл себя неправильно."""


class PluginState(StrEnum):
    """Что стало с плагином при загрузке."""

    LOADED = "loaded"
    DISABLED = "disabled"
    INCOMPATIBLE = "incompatible"
    FAILED = "failed"

    @property
    def title(self) -> str:
        return {
            PluginState.LOADED: tr('работает'),
            PluginState.DISABLED: tr('выключен'),
            PluginState.INCOMPATIBLE: tr('несовместим'),
            PluginState.FAILED: tr('ошибка'),
        }[self]


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """Манифест плагина."""

    name: str
    title: str
    version: str = "1.0"
    author: str = ""
    description: str = ""
    api_version: int = API_VERSION
    entry: str = "main.py"
    folder: Path | None = None

    @property
    def compatible(self) -> bool:
        """Рассчитан ли плагин на эту версию контракта.

        Строгое равенство, а не «не старше»: плагин, написанный под будущую
        версию, может ждать чего-то, чего здесь ещё нет, и упадёт уже в
        работе — хуже, чем честный отказ при загрузке.
        """
        return self.api_version == API_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any], folder: Path) -> PluginInfo:
        name = str(data.get("name") or folder.name).strip()
        if not name:
            raise PluginError(tr('в манифесте не указано имя плагина'))
        return cls(
            name=name,
            title=str(data.get("title") or name),
            version=str(data.get("version") or "1.0"),
            author=str(data.get("author") or ""),
            description=str(data.get("description") or ""),
            api_version=int(data.get("api_version") or API_VERSION),
            entry=str(data.get("entry") or "main.py"),
            folder=folder,
        )


@dataclass(frozen=True, slots=True)
class PluginAction:
    """Пункт меню, добавленный плагином."""

    plugin: str
    action_id: str
    title: str
    callback: Callable[[], None]
    shortcut: str = ""
    tooltip: str = ""


@runtime_checkable
class Plugin(Protocol):
    """Что программа ищет в модуле плагина."""

    def setup(self, context: PluginContext) -> None:
        """Вызывается один раз при загрузке. Здесь плагин себя регистрирует."""
        ...


@runtime_checkable
class PluginHost(Protocol):
    """Мост к работающей программе.

    Появился, когда первый настоящий плагин уткнулся в стену: он умел
    исправлять типографику, но не мог узнать, **какой документ** править.
    Регистрации без этого хватало ровно на то, чтобы добавить пункт меню,
    который ничего не может сделать.

    Реализует его главное окно; в тестах и консольных сценариях подставляется
    свой. Плагин про это не знает — он видит только методы контекста.
    """

    def document(self):
        """Открытый документ."""
        ...

    def selection(self) -> list[int]:
        """Выделенные реплики, в порядке документа."""
        ...

    def run_command(self, command) -> None:
        """Выполняет команду через стек отмены."""
        ...

    def report(self, message: str) -> None:
        """Сообщение человеку — в строку состояния."""
        ...


@dataclass
class PluginContext:
    """Всё, что плагин вправе делать, и ничего сверх того.

    Экземпляр создаётся на каждый плагин отдельно и помнит, кто он: так
    запись в журнале и сообщение об ошибке всегда называют виновника, а не
    оставляют пользователя гадать, какой из плагинов сломал меню.
    """

    plugin: str
    #: Каталог самого плагина: рядом с ним он хранит свои файлы.
    folder: Path
    #: Настройки приложения. Плагин пишет только в свой раздел — см. ``store``.
    settings: Any = None
    #: Мост к работающей программе. Пусто при загрузке — окно подставляет его
    #: перед тем, как действия станут доступны: на момент ``setup`` документа
    #: может ещё не быть, а регистрироваться плагин обязан уже тогда.
    host: Any = None

    actions: list[PluginAction] = field(default_factory=list)
    asr_engines: list[Any] = field(default_factory=list)
    formats: list[Any] = field(default_factory=list)
    qc_rules: list[Any] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    # -- регистрация ------------------------------------------------------- #

    def register_action(
        self,
        action_id: str,
        title: str,
        callback: Callable[[], None],
        *,
        shortcut: str = "",
        tooltip: str = "",
    ) -> None:
        """Добавляет пункт в меню «Плагины».

        Идентификатор дополняется именем плагина: два плагина вправе завести
        по действию «export», и без приставки второе затёрло бы первое.
        """
        if not callable(callback):
            raise PluginError(tr('действие «{0}» не даёт функции-обработчика').format(action_id))
        self.actions.append(
            PluginAction(
                plugin=self.plugin,
                action_id=f"plugin.{self.plugin}.{action_id}",
                title=title,
                callback=callback,
                shortcut=shortcut,
                tooltip=tooltip,
            )
        )

    def register_asr_engine(self, engine: Any) -> None:
        """Добавляет движок распознавания.

        От движка требуется тот же протокол, что и от встроенных:
        ``info()`` и ``transcribe()``. Проверяем сразу — иначе несоответствие
        всплывёт через полчаса, посреди распознавания.
        """
        missing = [n for n in ("info", "transcribe") if not hasattr(engine, n)]
        if missing:
            # Перечисляем всё сразу: чинить по одному методу за прогон —
            # значит заставлять автора плагина искать ошибки по очереди.
            names = ", ".join(f"{name}()" for name in missing)
            raise PluginError(tr('движок распознавания без методов: {0}').format(names))
        self.asr_engines.append(engine)

    def register_format(self, handler: Any) -> None:
        """Добавляет формат чтения и записи субтитров."""
        if not hasattr(handler, "extensions"):
            raise PluginError(tr('формат не сообщает, какие расширения он читает'))
        self.formats.append(handler)

    def register_qc_rule(self, rule: Any) -> None:
        """Добавляет проверку качества."""
        if not callable(rule) and not hasattr(rule, "check"):
            raise PluginError(tr('проверка должна быть функцией либо иметь метод check()'))
        self.qc_rules.append(rule)

    def log(self, message: str) -> None:
        """Сообщение в журнал программы, с именем плагина в начале."""
        self.messages.append(str(message))

    # -- работа с документом ------------------------------------------------ #

    def _require_host(self):
        if self.host is None:
            raise PluginError(
                tr('действие вызвано вне работающей программы: документа нет')
            )
        return self.host

    def document(self):
        """Открытый документ. Читать можно свободно, менять — только командой.

        Прямая правка полей прошла бы мимо истории отмены и мимо перерисовки:
        человек увидел бы прежний кадр и не смог бы вернуть сделанное.
        """
        return self._require_host().document()

    def selection(self) -> list[int]:
        """Выделенные реплики. Пустой список — не выделено ничего."""
        return list(self._require_host().selection())

    def run(self, command) -> None:
        """Выполняет команду через стек отмены программы."""
        self._require_host().run_command(command)

    def report(self, message: str) -> None:
        """Показывает сообщение человеку в строке состояния."""
        self._require_host().report(str(message))

    def show_report(self, title: str, text: str) -> None:
        """Показывает многострочный отчёт в окне.

        Строки состояния хватает для «готово, исправлено двенадцать», но не
        для таблицы по говорящим. Без этого плагин лез бы за Qt сам — и
        каждый делал бы своё окно, по-своему кривое.

        Программа, которая окон не открывает (консольный прогон, тесты),
        вправе просто напечатать текст: метод описывает намерение, а не
        способ.
        """
        host = self._require_host()
        shower = getattr(host, "show_report", None)
        if callable(shower):
            shower(str(title), str(text))
            return
        # Хост без окон: отчёт не должен пропадать совсем.
        self.log(f"{title}\n{text}")

    def set_texts(self, changes: dict[int, str], *, label: str = "") -> int:
        """Меняет текст сразу многих реплик — одной записью в истории.

        Отдельный метод, а не «соберите CompositeCommand сами»: массовая
        правка текста — самое частое, ради чего пишут плагин, а собранная
        неправильно она даёт по нажатию Ctrl+Z на каждую реплику.

        Возвращает число изменённых реплик.
        """
        from sfstudio.core.commands import CompositeCommand, SetText

        if not changes:
            return 0
        commands = [SetText(eid, text) for eid, text in changes.items()]
        self.run(
            CompositeCommand(
                commands, label=label or tr('Правка {0} реплик').format(len(commands))
            )
        )
        return len(commands)

    # -- хранилище плагина ------------------------------------------------- #

    def store(self, key: str, default: Any = None) -> Any:
        """Читает настройку плагина из его собственного раздела."""
        if self.settings is None:
            return default
        return self.settings.get(f"plugins.data.{self.plugin}.{key}", default)

    def set_store(self, key: str, value: Any) -> None:
        """Пишет настройку плагина. Чужие разделы для него закрыты."""
        if self.settings is None:
            return
        self.settings.set(f"plugins.data.{self.plugin}.{key}", value)
