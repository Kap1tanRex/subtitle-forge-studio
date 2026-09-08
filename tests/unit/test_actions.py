"""Тесты реестра действий и командной палитры."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication, QWidget

from sfstudio.ui.actions import ActionRegistry

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def parent(qapp: QApplication) -> QWidget:
    """Владелец действий.

    Держать ссылку обязательно: ``QAction`` живут ровно столько, сколько живёт
    родитель, и временный ``QWidget()`` в выражении уносит их с собой.
    """
    return QWidget()


def noop() -> None:
    pass


class TestRegistry:
    def test_register_and_lookup(self) -> None:
        registry = ActionRegistry()
        registry.add("a.b", "Действие", noop, shortcut="Ctrl+B")
        assert "a.b" in registry
        assert registry.spec("a.b").title == "Действие"

    def test_duplicate_key_is_refused(self) -> None:
        registry = ActionRegistry()
        registry.add("a.b", "Первое", noop)
        with pytest.raises(ValueError, match="уже зарегистрировано"):
            registry.add("a.b", "Второе", noop)

    def test_iteration_keeps_order(self) -> None:
        registry = ActionRegistry()
        for key in ("c", "a", "b"):
            registry.add(key, key.upper(), noop)
        assert [s.key for s in registry] == ["c", "a", "b"]


class TestConflicts:
    def test_detects_duplicate_shortcut(self) -> None:
        registry = ActionRegistry()
        registry.add("one", "Первое", noop, shortcut="Ctrl+S")
        registry.add("two", "Второе", noop, shortcut="Ctrl+S")
        conflicts = registry.conflicts()
        assert len(conflicts) == 1
        assert set(conflicts[0].keys) == {"one", "two"}

    def test_case_and_spacing_do_not_hide_conflict(self) -> None:
        """``Ctrl+Shift+S`` и ``ctrl+shift+s`` — одно сочетание.

        Глазами такое расхождение не заметить, поэтому сравнение идёт по
        нормализованному виду от Qt.
        """
        registry = ActionRegistry()
        registry.add("one", "Первое", noop, shortcut="Ctrl+Shift+S")
        registry.add("two", "Второе", noop, shortcut="ctrl+shift+s")
        assert registry.conflicts()

    def test_different_contexts_may_share_shortcut(self) -> None:
        """``Enter`` в таблице и в редакторе — законно разные действия."""
        registry = ActionRegistry()
        registry.add("table.confirm", "Подтвердить", noop, shortcut="Return", context="table")
        registry.add("editor.newline", "Перенос", noop, shortcut="Return", context="editor")
        assert not registry.conflicts()

    def test_actions_without_shortcut_never_conflict(self) -> None:
        registry = ActionRegistry()
        registry.add("one", "Первое", noop)
        registry.add("two", "Второе", noop)
        assert not registry.conflicts()

    def test_conflict_message_is_readable(self) -> None:
        registry = ActionRegistry()
        registry.add("one", "Первое", noop, shortcut="Ctrl+S")
        registry.add("two", "Второе", noop, shortcut="Ctrl+S")
        text = str(registry.conflicts()[0])
        assert "Ctrl+S" in text
        assert "one" in text and "two" in text


class TestBuild:
    def test_creates_actions(self, parent: QWidget) -> None:
        registry = ActionRegistry()
        registry.add("a", "Заголовок", noop, shortcut="Ctrl+B")
        actions = registry.build(parent)
        assert actions["a"].text() == "Заголовок"
        assert not actions["a"].shortcut().isEmpty()

    def test_plain_handler_receives_no_argument(self, parent: QWidget) -> None:
        """Qt передаёт ``checked`` в ``triggered``; обычный обработчик его не ждёт."""
        calls: list[tuple] = []
        registry = ActionRegistry()
        registry.add("a", "Действие", lambda: calls.append(()))
        actions = registry.build(parent)
        actions["a"].trigger()
        assert calls == [()]

    def test_checkable_handler_receives_state(self, parent: QWidget) -> None:
        states: list[bool] = []
        registry = ActionRegistry()
        registry.add("a", "Переключатель", states.append, checkable=True)
        actions = registry.build(parent)
        actions["a"].setChecked(True)
        assert states == [True]

    def test_initial_checked_state(self, parent: QWidget) -> None:
        registry = ActionRegistry()
        registry.add("a", "Вкл", noop, checkable=True, checked=True)
        assert registry.build(parent)["a"].isChecked()


class TestSearch:
    def _registry(self) -> ActionRegistry:
        registry = ActionRegistry()
        registry.add("timing.shift", "Сдвиг таймингов", noop, menu="Тайминг")
        registry.add("timing.auto", "Доводка таймингов", noop, menu="Тайминг")
        registry.add("file.save", "Сохранить", noop, menu="Файл")
        registry.add("hidden.one", "Служебное", noop, hidden=True)
        return registry

    def test_empty_query_returns_visible(self) -> None:
        found = self._registry().search("")
        assert len(found) == 3
        assert all(not s.hidden for s in found)

    def test_substring_match(self) -> None:
        found = self._registry().search("сдвиг")
        assert found and found[0].key == "timing.shift"

    def test_search_covers_menu_name(self) -> None:
        found = self._registry().search("тайминг")
        assert {s.key for s in found} == {"timing.shift", "timing.auto"}

    def test_substring_ranks_above_scattered_letters(self) -> None:
        """Набравший «сдвиг» ждёт «Сдвиг таймингов» первым."""
        found = self._registry().search("сдвиг")
        assert found[0].title == "Сдвиг таймингов"

    def test_hidden_actions_are_excluded(self) -> None:
        assert all(s.key != "hidden.one" for s in self._registry().search("служеб"))

    def test_no_match_returns_empty(self) -> None:
        assert self._registry().search("абракадабра") == []


class TestMainWindowActions:
    """Проверки на настоящем окне — там, где конфликты и появляются."""

    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        # Настоящий конфиг пользователя трогать нельзя: окно на старте
        # восстанавливает раскладку и читает шаблоны оформления.
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        window = MainWindow()
        yield window
        # Перед закрытием помечаем документ сохранённым: closeEvent честно
        # спрашивает о несохранённых правках, и в прогоне без человека
        # модальный диалог повис бы навсегда.
        window._undo.mark_clean()
        window.close()
        window.deleteLater()

    def test_no_shortcut_conflicts(self, window) -> None:
        """Главная проверка: два действия не должны делить сочетание.

        В исходной спецификации ``Ctrl+Shift+S`` значил одновременно
        «Сохранить как» и «Разбить событие», а ``Ctrl+Shift+P`` —
        «Сбросить \\pos» и «Командная палитра». Такие столкновения
        находятся только автоматически.
        """
        conflicts = window._actions_registry.conflicts()
        assert not conflicts, "конфликты сочетаний:\n" + "\n".join(map(str, conflicts))

    def test_save_shortcuts_belong_to_the_project(self, window) -> None:
        """``Ctrl+S`` и ``Ctrl+Shift+S`` — за главным документом.

        Главный документ здесь проект: он хранит и субтитры, и привязанное
        видео, и место, где пользователь остановился. Экспорт отдельного файла
        субтитров — вторичное действие, и занимать им привычные сочетания
        значит подставлять пользователя, который жмёт Ctrl+S не глядя.
        """
        registry = window._actions_registry
        assert registry.spec("file.save_project").shortcut == "Ctrl+S"
        assert registry.spec("file.save_project_as").shortcut == "Ctrl+Shift+S"

    def test_every_shortcut_is_understood_by_qt(self, window) -> None:
        """Нераспознанное сочетание Qt молча превращает в пустое.

        ``Ctrl+Shift+Plus`` выглядит правдоподобно, но Qt его не разбирает:
        действие остаётся вовсе без горячей клавиши, и понять это по виду
        меню нельзя — там просто пусто.
        """
        from PySide6.QtGui import QKeySequence

        broken = [
            spec.key
            for spec in window._actions_registry
            if spec.shortcut
            and not QKeySequence(spec.shortcut).toString(QKeySequence.PortableText)
        ]
        assert not broken, f"Qt не разобрал сочетания: {broken}"

    def test_every_action_has_title(self, window) -> None:
        for spec in window._actions_registry:
            assert spec.title.strip(), f"у {spec.key} нет названия"

    def test_titles_are_translated(self, window) -> None:
        for spec in window._actions_registry:
            letters = [c for c in spec.title if c.isalpha()]
            assert letters, spec.key
            assert not all(ord(c) < 128 for c in letters), f"не переведено: {spec.title!r}"

    def test_palette_finds_every_action(self, window) -> None:
        registry = window._actions_registry
        for spec in registry:
            if spec.hidden:
                continue
            found = registry.search(spec.title)
            assert any(s.key == spec.key for s in found), f"не находится: {spec.title}"

    def test_every_menu_action_is_laid_out(self, window) -> None:
        """Зарегистрировать действие и забыть добавить его в меню — легко."""
        placed = set()
        for top in window.menuBar().actions():
            menu = top.menu()
            if menu is None:
                continue
            for action in menu.actions():
                submenu = action.menu()
                if submenu is not None:
                    placed.update(a.data() for a in submenu.actions())
                else:
                    placed.add(action.data())

        expected = {s.key for s in window._actions_registry if s.menu}
        assert expected <= placed, f"не попали в меню: {sorted(expected - placed)}"


class TestShortcutOverrides:
    """Пользовательская раскладка: реестр уже знал команды и конфликты."""

    def make_registry(self):
        from sfstudio.ui.actions import ActionRegistry

        registry = ActionRegistry()
        registry.add("a.one", "Первая", lambda: None, shortcut="Ctrl+1", menu="Файл")
        registry.add("a.two", "Вторая", lambda: None, shortcut="Ctrl+2", menu="Файл")
        return registry

    def test_override_replaces_the_shortcut(self) -> None:
        registry = self.make_registry()
        registry.apply_overrides({"a.one": "Ctrl+9"})
        assert registry.spec("a.one").shortcut == "Ctrl+9"

    def test_empty_override_removes_the_shortcut(self) -> None:
        """Оставить команду только в меню — осмысленный выбор, а не пропуск."""
        registry = self.make_registry()
        registry.apply_overrides({"a.one": ""})
        assert registry.spec("a.one").shortcut == ""

    def test_unknown_keys_are_reported(self) -> None:
        """Настройки переживают обновление: команда могла исчезнуть."""
        registry = self.make_registry()
        assert registry.apply_overrides({"a.gone": "Ctrl+8"}) == ["a.gone"]

    def test_defaults_survive_overrides(self) -> None:
        registry = self.make_registry()
        registry.apply_overrides({"a.one": "Ctrl+9"})
        assert registry.default_shortcuts()["a.one"] == "Ctrl+1"

    def test_returning_to_defaults(self) -> None:
        registry = self.make_registry()
        registry.apply_overrides({"a.one": "Ctrl+9"})
        registry.apply_overrides(registry.default_shortcuts())
        assert registry.spec("a.one").shortcut == "Ctrl+1"

    def test_conflicts_are_seen_after_an_override(self, parent) -> None:
        registry = self.make_registry()
        registry.build(parent)
        assert not registry.conflicts()
        registry.apply_overrides({"a.two": "Ctrl+1"})
        assert len(registry.conflicts()) == 1

    def test_built_action_follows_the_override(self, parent) -> None:
        """Сочетание должно начать работать сразу, без пересборки меню."""
        from PySide6.QtGui import QKeySequence

        registry = self.make_registry()
        registry.build(parent)
        registry.apply_overrides({"a.one": "Ctrl+9"})
        assert registry.action("a.one").shortcut() == QKeySequence("Ctrl+9")
