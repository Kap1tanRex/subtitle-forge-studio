"""Поиск и загрузка плагинов.

Плагин лежит папкой в каталоге плагинов: манифест ``plugin.json`` плюс
модуль Python, названный в манифесте. Такая раскладка выбрана ради того,
чтобы плагин можно было отдать одним архивом и поставить распаковкой — без
установщиков, реестров и команд в консоли.

**Ни одна ошибка плагина не должна ронять программу.** Код пишет третья
сторона; он может импортировать несуществующее, падать при загрузке,
зацикливаться в ``setup``. Поэтому каждый шаг обёрнут, а результат — не
исключение, а запись о состоянии: пользователь видит список с пометкой,
какой плагин сломан и почему, и продолжает работать без него.

**Загрузка не автоматическая по умолчанию.** Положить папку в каталог
плагинов — уже осознанное действие, но выполнять чужой код без ведома
человека всё равно нельзя: список выключен, пока его не включат в
настройках. Это не безопасность в строгом смысле — от вредоносного плагина
такое не защитит, — а честность: программа не запускает чужой код молча.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sfstudio.plugins.api import (
    MANIFEST_NAME,
    PluginContext,
    PluginError,
    PluginInfo,
    PluginState,
)

__all__ = ["LoadedPlugin", "PluginManager", "discover", "load_plugin"]


@dataclass(slots=True)
class _Installed:
    """Что плагин добавил в реестры — чтобы уметь это снять.

    Формат хранится вместе с прежним значением: плагин вправе заменить
    встроенный, и при выключении такого плагина программа должна вернуть
    себе умение читать этот формат, а не остаться без него.
    """

    engines: list[str] = field(default_factory=list)
    formats: list[tuple[str, object]] = field(default_factory=list)
    rules: list[object] = field(default_factory=list)


@dataclass(slots=True)
class LoadedPlugin:
    """Плагин и что с ним стало."""

    info: PluginInfo
    state: PluginState
    #: Причина, если состояние не «работает».
    reason: str = ""
    context: PluginContext | None = None

    @property
    def caption(self) -> str:
        return f"{self.info.title} {self.info.version} — {self.state.title}"


def discover(folder: Path | None) -> list[PluginInfo]:
    """Читает манифесты всех плагинов в каталоге.

    Папка без манифеста молча пропускается: в каталоге плагинов рядом
    оказываются и распакованные архивы, и папки с заметками, и это не повод
    сообщать об ошибке.
    """
    if folder is None or not Path(folder).is_dir():
        return []

    found: list[PluginInfo] = []
    for entry in sorted(Path(folder).iterdir()):
        manifest = entry / MANIFEST_NAME
        if not entry.is_dir() or not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            found.append(PluginInfo.from_dict(data, entry))
        except (OSError, ValueError, PluginError):
            # Битый манифест — это тоже плагин, о котором надо сказать.
            found.append(
                PluginInfo(
                    name=entry.name,
                    title=entry.name,
                    api_version=-1,  # заведомо несовместим: манифест не прочли
                    folder=entry,
                )
            )
    return found


def load_plugin(info: PluginInfo, settings: Any = None) -> LoadedPlugin:
    """Загружает один плагин. Исключений не бросает — возвращает состояние."""
    if info.folder is None:
        return LoadedPlugin(info, PluginState.FAILED, "неизвестно, где лежит плагин")
    if not info.compatible:
        return LoadedPlugin(
            info,
            PluginState.INCOMPATIBLE,
            f"плагин рассчитан на версию контракта {info.api_version}, "
            f"а программа предоставляет {_api_version()}",
        )

    # Имя модуля берётся из манифеста, а тот пишет автор плагина. Путь вида
    # «../../скачанное.py» — и запущен будет файл, о котором человек думал
    # как о данных, а не как о плагине. От вредоносного плагина это не
    # спасёт (его код и так исполняется), но выполнять посторонний файл,
    # лежащий вне каталога плагинов, программа не должна.
    root = info.folder.resolve()
    entry = (info.folder / info.entry).resolve()
    if not entry.is_relative_to(root):
        return LoadedPlugin(
            info,
            PluginState.FAILED,
            f"модуль «{info.entry}» лежит за пределами папки плагина",
        )
    if not entry.is_file():
        return LoadedPlugin(info, PluginState.FAILED, f"нет файла {info.entry}")

    try:
        module = _import(info, entry)
    except Exception as exc:
        return LoadedPlugin(info, PluginState.FAILED, _describe(exc))

    setup = getattr(module, "setup", None)
    if not callable(setup):
        return LoadedPlugin(
            info, PluginState.FAILED, "в модуле нет функции setup(context)"
        )

    context = PluginContext(plugin=info.name, folder=info.folder, settings=settings)
    try:
        setup(context)
    except Exception as exc:
        return LoadedPlugin(info, PluginState.FAILED, _describe(exc), context)
    return LoadedPlugin(info, PluginState.LOADED, "", context)


def _import(info: PluginInfo, entry: Path):
    """Импортирует модуль плагина по пути, под собственным именем.

    Имя с приставкой ``sfstudio_plugin_``: без неё модуль ``utils`` из
    плагина занял бы место любого другого ``utils`` в ``sys.modules`` — и
    сломал бы не себя, а программу.

    Каталог плагина добавляется в пути поиска на время импорта, чтобы плагин
    мог разложить себя на несколько файлов; после импорта путь убирается,
    чтобы он не влиял на остальную программу.
    """
    module_name = f"sfstudio_plugin_{info.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise PluginError("не удалось прочитать модуль плагина")

    module = importlib.util.module_from_spec(spec)
    folder = str(info.folder)
    sys.path.insert(0, folder)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    finally:
        with_removed = [p for p in sys.path if p != folder]
        sys.path[:] = with_removed
    return module


def _describe(exc: BaseException) -> str:
    """Короткая причина плюс место в коде плагина.

    Полная трассировка человеку не нужна, а вот файл и строка нужны автору
    плагина, которому это сообщение и перешлют.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    where = ""
    if frames:
        last = frames[-1]
        where = f" ({Path(last.filename).name}, строка {last.lineno})"
    return f"{type(exc).__name__}: {exc}{where}"


def _api_version() -> int:
    from sfstudio.plugins.api import API_VERSION

    return API_VERSION


class PluginManager:
    """Все плагины: нашли, загрузили, знаем состояние каждого.

    Управление списком отделено от главного окна намеренно: плагины нужны и
    там, где окна нет вовсе — в проверке сборки, в консольных сценариях, в
    тестах. Окно берёт отсюда готовый список действий и добавляет их в меню.
    """

    def __init__(self, folder: Path | None, settings: Any = None) -> None:
        self._folder = Path(folder) if folder is not None else None
        self._settings = settings
        self._plugins: list[LoadedPlugin] = []
        #: Имя плагина → что он добавил в реестры.
        self._installed: dict[str, _Installed] = {}
        #: Мост к программе — нужен, когда плагин включают на лету.
        self._host = None

    @property
    def folder(self) -> Path | None:
        return self._folder

    @property
    def plugins(self) -> list[LoadedPlugin]:
        return list(self._plugins)

    @property
    def working(self) -> list[LoadedPlugin]:
        return [p for p in self._plugins if p.state is PluginState.LOADED]

    def load_all(self, *, enabled: bool = True) -> list[LoadedPlugin]:
        """Загружает всё, что найдено. ``enabled=False`` — только перечислить.

        Выключенные плагины всё равно перечисляются: список, который при
        выключенных плагинах становится пустым, не даёт их включить —
        человек просто не увидит, что они есть.
        """
        disabled = set(self._disabled_names())
        for name in list(self._installed):
            self._uninstall_one(name)
        self._plugins = []
        for info in discover(self._folder):
            if not enabled or info.name in disabled:
                self._plugins.append(LoadedPlugin(info, PluginState.DISABLED))
                continue
            self._plugins.append(load_plugin(info, self._settings))
        return self.plugins

    def actions(self) -> list:
        """Пункты меню от всех работающих плагинов."""
        return self._collect("actions")

    def asr_engines(self) -> list:
        """Движки распознавания от плагинов."""
        return self._collect("asr_engines")

    def formats(self) -> list:
        """Форматы субтитров от плагинов."""
        return self._collect("formats")

    def qc_rules(self) -> list:
        """Проверки качества от плагинов."""
        return self._collect("qc_rules")

    def set_host(self, host) -> None:
        """Даёт плагинам доступ к работающей программе.

        Отдельным шагом после загрузки: на момент ``setup`` окна ещё нет, а
        регистрироваться плагин обязан именно тогда. Разделение позволяет
        загрузить плагины до сборки интерфейса и не гадать про порядок.
        """
        self._host = host
        for plugin in self._plugins:
            if plugin.context is not None:
                plugin.context.host = host

    def _collect(self, field_name: str) -> list:
        result: list = []
        for plugin in self.working:
            if plugin.context is not None:
                result.extend(getattr(plugin.context, field_name, ()))
        return result

    def install(self) -> list[str]:
        """Вносит зарегистрированное плагинами в реестры программы.

        Отдельным шагом, а не прямо в ``setup``: пока плагин выполняется, его
        код ещё может упасть, и записывать его движок в общий реестр рано —
        реестр глобальный, и убрать оттуда половину сломанного плагина
        неоткуда. Сюда доходит только то, что загрузилось целиком.

        Возвращает описания добавленного — для журнала и окна настроек.
        """
        added: list[str] = []
        for plugin in self.working:
            added.extend(self._install_one(plugin))
        return added

    def _install_one(self, plugin: LoadedPlugin) -> list[str]:
        """Регистрирует один плагин и запоминает, что именно от него пришло.

        Запоминать обязательно: без этого выключить плагин можно было бы
        только перезапуском программы — а выключают их как раз тогда, когда
        плагин мешает прямо сейчас.
        """
        from sfstudio.io import registry as io_registry
        from sfstudio.services import qc
        from sfstudio.services.asr import registry as asr_registry

        context = plugin.context
        if context is None or plugin.info.name in self._installed:
            return []

        record = _Installed()
        added: list[str] = []

        for engine in context.asr_engines:
            key = str(getattr(engine, "key", "") or "")
            if not key:
                continue
            # Фабрика возвращает готовый объект: плагин уже создал его сам, и
            # заставлять его писать ещё и фабрику ради нашего устройства
            # реестра — лишняя работа на ровном месте.
            asr_registry.register(key, lambda e=engine: e)
            record.engines.append(key)
            added.append(f"движок распознавания «{key}»")

        for spec in context.formats:
            fid = str(getattr(spec, "fid", "") or "")
            if not fid:
                continue
            # Прежний формат запоминается: плагин вправе заменить встроенный,
            # и при выключении такого плагина ASS должен снова читаться.
            previous = io_registry.FORMATS.get(fid)
            try:
                io_registry.register_format(spec)
            except (ValueError, TypeError) as exc:
                added.append(f"формат «{fid}» отклонён: {exc}")
                continue
            record.formats.append((fid, previous))
            added.append(f"формат «{fid}»")

        for rule in context.qc_rules:
            check = rule if callable(rule) else getattr(rule, "check", None)
            try:
                qc.register_rule(check)
            except TypeError as exc:
                added.append(f"проверка отклонена: {exc}")
                continue
            record.rules.append(check)
            name = getattr(rule, "__name__", None) or type(rule).__name__
            added.append(f"проверка качества «{name}»")

        self._installed[plugin.info.name] = record
        return added

    def _uninstall_one(self, name: str) -> None:
        """Снимает всё, что зарегистрировал этот плагин."""
        from sfstudio.io import registry as io_registry
        from sfstudio.services import qc
        from sfstudio.services.asr import registry as asr_registry

        record = self._installed.pop(name, None)
        if record is None:
            return

        for key in record.engines:
            asr_registry.unregister(key)
        for fid, previous in record.formats:
            io_registry.unregister_format(fid, previous)
        for rule in record.rules:
            qc.unregister_rule(rule)

    # -- включение и выключение на лету -------------------------------------- #

    def set_active(self, name: str, active: bool, host=None) -> LoadedPlugin | None:
        """Включает или выключает плагин **сейчас**, без перезапуска.

        Плагины выключают тогда, когда они мешают прямо сейчас: чей-то пункт
        меню перехватил сочетание, чья-то проверка засыпала панель ложными
        находками. Ответ «перезапустите программу» в такой момент — плохой
        ответ, поэтому здесь и загрузка, и снятие регистраций.

        Выбор запоминается в настройках: выключенный плагин не должен
        воскреснуть при следующем запуске.
        """
        current = next((p for p in self._plugins if p.info.name == name), None)
        if current is None:
            return None

        self._remember_choice(name, active)

        if not active:
            self._uninstall_one(name)
            updated = LoadedPlugin(current.info, PluginState.DISABLED)
        else:
            updated = load_plugin(current.info, self._settings)
            if updated.state is PluginState.LOADED and updated.context is not None:
                updated.context.host = host if host is not None else self._host
                self._plugins = [
                    updated if p.info.name == name else p for p in self._plugins
                ]
                self._install_one(updated)
                return updated

        self._plugins = [updated if p.info.name == name else p for p in self._plugins]
        return updated

    def is_active(self, name: str) -> bool:
        plugin = next((p for p in self._plugins if p.info.name == name), None)
        return plugin is not None and plugin.state is PluginState.LOADED

    def _remember_choice(self, name: str, active: bool) -> None:
        if self._settings is None:
            return
        disabled = set(self._disabled_names())
        if active:
            disabled.discard(name)
        else:
            disabled.add(name)
        self._settings.set("plugins.disabled", sorted(disabled))
        self._settings.save()

    def _disabled_names(self) -> list[str]:
        if self._settings is None:
            return []
        value = self._settings.get("plugins.disabled", [])
        return [str(name) for name in value] if isinstance(value, list) else []

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Включает или выключает плагин. Действует со следующего запуска."""
        if self._settings is None:
            return
        disabled = set(self._disabled_names())
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        self._settings.set("plugins.disabled", sorted(disabled))
        self._settings.save()
