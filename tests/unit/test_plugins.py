"""Тесты фундамента плагинов: загрузка, изоляция ошибок, точки расширения."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sfstudio.app.settings import Settings
from sfstudio.plugins import (
    API_VERSION,
    MANIFEST_NAME,
    PluginContext,
    PluginError,
    PluginManager,
    PluginState,
    discover,
    load_plugin,
)


def make_plugin(
    root: Path,
    name: str,
    code: str,
    *,
    api_version: int = API_VERSION,
    entry: str = "main.py",
    manifest: dict | None = None,
) -> Path:
    """Раскладывает плагин на диске так, как это сделал бы человек."""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    data = manifest if manifest is not None else {
        "name": name,
        "title": f"Плагин {name}",
        "version": "1.0",
        "api_version": api_version,
        "entry": entry,
    }
    (folder / MANIFEST_NAME).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    (folder / entry).write_text(code, encoding="utf-8")
    return folder


GOOD = """
def setup(context):
    context.register_action("hello", "Привет", lambda: None)
    context.log("плагин на месте")
"""


class TestDiscovery:
    def test_finds_a_plugin(self, tmp_path: Path) -> None:
        make_plugin(tmp_path, "demo", GOOD)
        found = discover(tmp_path)
        assert [p.name for p in found] == ["demo"]
        assert found[0].title == "Плагин demo"

    def test_missing_folder_is_not_an_error(self, tmp_path: Path) -> None:
        assert discover(tmp_path / "нет-такой") == []
        assert discover(None) == []

    def test_folder_without_manifest_is_skipped(self, tmp_path: Path) -> None:
        """Рядом с плагинами лежат и обычные папки — это не повод ругаться."""
        (tmp_path / "просто-папка").mkdir()
        assert discover(tmp_path) == []

    def test_broken_manifest_is_still_listed(self, tmp_path: Path) -> None:
        """О сломанном плагине надо сказать, а не сделать вид, что его нет."""
        folder = tmp_path / "сломанный"
        folder.mkdir()
        (folder / MANIFEST_NAME).write_text("{это не json", encoding="utf-8")
        found = discover(tmp_path)
        assert len(found) == 1
        assert not found[0].compatible


class TestLoading:
    def test_loads_and_registers(self, tmp_path: Path) -> None:
        info = discover(make_plugin(tmp_path, "demo", GOOD).parent)[0]
        loaded = load_plugin(info)
        assert loaded.state is PluginState.LOADED
        assert loaded.context is not None
        assert [a.title for a in loaded.context.actions] == ["Привет"]

    def test_action_id_carries_the_plugin_name(self, tmp_path: Path) -> None:
        """Два плагина вправе завести действие «export» — не затирая друг друга."""
        info = discover(make_plugin(tmp_path, "demo", GOOD).parent)[0]
        loaded = load_plugin(info)
        assert loaded.context.actions[0].action_id == "plugin.demo.hello"

    def test_broken_code_does_not_raise(self, tmp_path: Path) -> None:
        """Ошибка плагина — запись о состоянии, а не падение программы."""
        info = discover(make_plugin(tmp_path, "bad", "1 / 0\n").parent)[0]
        loaded = load_plugin(info)
        assert loaded.state is PluginState.FAILED
        assert "ZeroDivisionError" in loaded.reason

    def test_failure_names_the_line(self, tmp_path: Path) -> None:
        """Автору плагина нужно место ошибки, а не только её название."""
        info = discover(make_plugin(tmp_path, "bad", "\n\nraise ValueError('ой')\n").parent)[0]
        loaded = load_plugin(info)
        assert "main.py" in loaded.reason and "3" in loaded.reason

    def test_setup_failure_is_caught(self, tmp_path: Path) -> None:
        code = "def setup(context):\n    raise RuntimeError('не вышло')\n"
        info = discover(make_plugin(tmp_path, "bad", code).parent)[0]
        loaded = load_plugin(info)
        assert loaded.state is PluginState.FAILED
        assert "не вышло" in loaded.reason

    def test_module_without_setup_is_rejected(self, tmp_path: Path) -> None:
        info = discover(make_plugin(tmp_path, "empty", "value = 1\n").parent)[0]
        loaded = load_plugin(info)
        assert loaded.state is PluginState.FAILED
        assert "setup" in loaded.reason

    def test_missing_entry_file(self, tmp_path: Path) -> None:
        folder = make_plugin(tmp_path, "gone", GOOD)
        (folder / "main.py").unlink()
        loaded = load_plugin(discover(tmp_path)[0])
        assert loaded.state is PluginState.FAILED

    def test_future_api_version_is_refused(self, tmp_path: Path) -> None:
        """Плагин под будущий контракт упал бы позже и непонятнее."""
        make_plugin(tmp_path, "future", GOOD, api_version=API_VERSION + 5)
        loaded = load_plugin(discover(tmp_path)[0])
        assert loaded.state is PluginState.INCOMPATIBLE
        assert str(API_VERSION) in loaded.reason


class TestIsolation:
    def test_module_name_does_not_shadow_others(self, tmp_path: Path) -> None:
        """Модуль «utils» из плагина не должен подменять чужой «utils»."""
        import sys

        make_plugin(tmp_path, "demo", GOOD, entry="utils.py")
        load_plugin(discover(tmp_path)[0])
        assert "utils" not in sys.modules
        assert "sfstudio_plugin_demo" in sys.modules

    def test_plugin_folder_leaves_no_trace_in_path(self, tmp_path: Path) -> None:
        import sys

        before = list(sys.path)
        load_plugin(discover(make_plugin(tmp_path, "demo", GOOD).parent)[0])
        assert sys.path == before

    def test_path_is_cleaned_after_failure_too(self, tmp_path: Path) -> None:
        import sys

        before = list(sys.path)
        load_plugin(discover(make_plugin(tmp_path, "bad", "1/0\n").parent)[0])
        assert sys.path == before


class TestExtensionPoints:
    def test_engine_must_look_like_an_engine(self) -> None:
        """Несоответствие должно всплыть при загрузке, а не через полчаса."""
        context = PluginContext(plugin="p", folder=Path())
        with pytest.raises(PluginError, match="transcribe"):
            context.register_asr_engine(object())

    def test_valid_engine_is_accepted(self) -> None:
        class Engine:
            def info(self): ...
            def transcribe(self, request, progress=None, cancel=None): ...

        context = PluginContext(plugin="p", folder=Path())
        context.register_asr_engine(Engine())
        assert len(context.asr_engines) == 1

    def test_action_needs_a_callable(self) -> None:
        context = PluginContext(plugin="p", folder=Path())
        with pytest.raises(PluginError):
            context.register_action("x", "X", "не функция")  # type: ignore[arg-type]

    def test_format_must_declare_extensions(self) -> None:
        context = PluginContext(plugin="p", folder=Path())
        with pytest.raises(PluginError, match="расширения"):
            context.register_format(object())

    def test_store_is_scoped_to_the_plugin(self, tmp_path: Path) -> None:
        """Плагин пишет в свой раздел и не может залезть в чужой."""
        settings = Settings(tmp_path / "settings.json")
        context = PluginContext(plugin="demo", folder=tmp_path, settings=settings)
        context.set_store("key", 42)
        assert settings.get("plugins.data.demo.key") == 42
        assert context.store("key") == 42
        assert context.store("нет-такого", "по умолчанию") == "по умолчанию"


class TestManager:
    def test_collects_actions_from_working_plugins(self, tmp_path: Path) -> None:
        make_plugin(tmp_path, "good", GOOD)
        make_plugin(tmp_path, "bad", "1/0\n")
        manager = PluginManager(tmp_path)
        manager.load_all()

        assert len(manager.plugins) == 2
        assert len(manager.working) == 1
        assert [a.plugin for a in manager.actions()] == ["good"]

    def test_disabled_by_default(self, tmp_path: Path) -> None:
        """Чужой код не выполняется, пока его не разрешили."""
        make_plugin(tmp_path, "good", GOOD)
        manager = PluginManager(tmp_path)
        manager.load_all(enabled=False)
        assert manager.plugins[0].state is PluginState.DISABLED
        assert manager.actions() == []

    def test_disabled_plugins_stay_visible(self, tmp_path: Path) -> None:
        """Иначе выключенный плагин не найти, чтобы включить обратно."""
        make_plugin(tmp_path, "good", GOOD)
        manager = PluginManager(tmp_path)
        manager.load_all(enabled=False)
        assert [p.info.name for p in manager.plugins] == ["good"]

    def test_individual_plugin_can_be_switched_off(self, tmp_path: Path) -> None:
        make_plugin(tmp_path, "one", GOOD)
        make_plugin(tmp_path, "two", GOOD)
        settings = Settings(tmp_path / "settings.json")
        manager = PluginManager(tmp_path, settings)
        manager.set_enabled("one", False)
        manager.load_all()

        states = {p.info.name: p.state for p in manager.plugins}
        assert states["one"] is PluginState.DISABLED
        assert states["two"] is PluginState.LOADED

    def test_no_folder_is_not_an_error(self) -> None:
        manager = PluginManager(None)
        assert manager.load_all() == []


ENGINE_PLUGIN = """
class Engine:
    key = "плагинный-движок"

    def info(self):
        return None

    def transcribe(self, request, progress=None, cancel=None):
        return None


def setup(context):
    context.register_asr_engine(Engine())
"""

FORMAT_PLUGIN = """
from sfstudio.io.registry import FormatSpec


def setup(context):
    context.register_format(
        FormatSpec("выдуманный", "Выдуманный формат", (".xyz",),
                   lambda text: None, lambda doc: "")
    )
"""

RULE_PLUGIN = """
from sfstudio.services.qc import Issue, Severity


def проверка(event, ctx):
    if "плохо" in event.plain:
        yield Issue(event.eid, "плагин", Severity.WARNING, "нашлось слово «плохо»")


def setup(context):
    context.register_qc_rule(проверка)
"""


class TestInstallation:
    """Зарегистрированное плагином должно попасть в реестры программы.

    Раньше контекст всё собирал, а реестры об этом не знали: плагин считал,
    что его движок работает, и нигде не появлялся.
    """

    @pytest.fixture(autouse=True)
    def clean_registries(self):
        """Реестры глобальные — за собой надо убирать, иначе тесты потекут."""
        from sfstudio.io import registry as io_registry
        from sfstudio.services import qc
        from sfstudio.services.asr import registry as asr_registry

        formats_before = dict(io_registry.FORMATS)
        engines_before = set(asr_registry.registered_keys())
        yield
        io_registry.FORMATS.clear()
        io_registry.FORMATS.update(formats_before)
        for key in set(asr_registry.registered_keys()) - engines_before:
            asr_registry._FACTORIES.pop(key, None)
        qc.clear_extra_rules()

    def test_engine_reaches_the_registry(self, tmp_path: Path) -> None:
        from sfstudio.services.asr import registry as asr_registry

        make_plugin(tmp_path, "движок", ENGINE_PLUGIN)
        manager = PluginManager(tmp_path)
        manager.load_all()
        assert manager.working, manager.plugins[0].reason
        manager.install()

        assert "плагинный-движок" in asr_registry.registered_keys()
        assert asr_registry.get_engine("плагинный-движок") is not None

    def test_format_reaches_the_registry(self, tmp_path: Path) -> None:
        from sfstudio.io import registry as io_registry

        make_plugin(tmp_path, "формат", FORMAT_PLUGIN)
        manager = PluginManager(tmp_path)
        manager.load_all()
        assert manager.working, manager.plugins[0].reason
        manager.install()

        assert "выдуманный" in io_registry.FORMATS
        assert any(f.fid == "выдуманный" for f in io_registry.supported_read())

    def test_qc_rule_actually_runs(self, tmp_path: Path) -> None:
        """Проверка из плагина должна находить то, ради чего написана."""
        from sfstudio.core.document import SubtitleDocument
        from sfstudio.services.qc import QcRunner

        make_plugin(tmp_path, "правило", RULE_PLUGIN)
        manager = PluginManager(tmp_path)
        manager.load_all()
        assert manager.working, manager.plugins[0].reason
        manager.install()

        doc = SubtitleDocument.blank()
        event = doc.create_event(0, 3000, "Тут написано плохо")
        runner = QcRunner()
        runner.run_all(doc)
        assert any(i.rule == "плагин" for i in runner.issues_for(event.eid))

    def test_install_reports_what_it_added(self, tmp_path: Path) -> None:
        make_plugin(tmp_path, "движок", ENGINE_PLUGIN)
        manager = PluginManager(tmp_path)
        manager.load_all()
        added = manager.install()
        assert any("плагинный-движок" in line for line in added)

    def test_broken_plugin_registers_nothing(self, tmp_path: Path) -> None:
        """Половина упавшего плагина в общем реестре — хуже, чем ничего."""
        from sfstudio.services.asr import registry as asr_registry

        code = ENGINE_PLUGIN.replace(
            "    context.register_asr_engine(Engine())",
            "    context.register_asr_engine(Engine())\n    raise RuntimeError('поздний сбой')",
        )
        make_plugin(tmp_path, "сбойный", code)
        manager = PluginManager(tmp_path)
        manager.load_all()
        manager.install()

        assert manager.plugins[0].state is PluginState.FAILED
        assert "плагинный-движок" not in asr_registry.registered_keys()

    def test_failing_rule_does_not_hide_other_findings(self, tmp_path: Path) -> None:
        """Исключение в чужой проверке не должно съедать встроенные находки."""
        from sfstudio.core.document import SubtitleDocument
        from sfstudio.services import qc
        from sfstudio.services.qc import QcRunner

        def падучая(event, ctx):
            raise ValueError("я всегда падаю")
            yield  # pragma: no cover - до сюда не доходит

        qc.register_rule(падучая)

        doc = SubtitleDocument.blank()
        # Заведомо слишком быстрая реплика: встроенное правило обязано сработать.
        event = doc.create_event(0, 300, "Очень длинный текст за очень малое время")
        runner = QcRunner()
        runner.run_all(doc)

        rules = {i.rule for i in runner.issues_for(event.eid)}
        assert "plugin_rule" in rules, "о сбое проверки надо сообщить"
        assert rules - {"plugin_rule"}, "встроенные находки пропали"
