"""Шапка списка реплик: поиск, кнопки, их размер и связь с действиями."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from sfstudio.ui.edit_bar import BAR_H, EditBar

pytestmark = pytest.mark.needs_gui

KEYS = ("edit.insert", "edit.duplicate", "edit.delete")


def _widgets(bar: EditBar) -> int:
    """Сколько виджетов лежит в раскладке полосы — кнопки и черты."""
    return sum(
        1 for index in range(bar.layout().count())
        if bar.layout().itemAt(index).widget() is not None
    )


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(qapp: QApplication) -> QWidget:
    """Родитель для действий: без него QAction некому держать."""
    return QWidget()


@pytest.fixture
def actions(host: QWidget) -> dict[str, QAction]:
    made = {}
    for key in KEYS:
        action = QAction(key, host)
        action.setData(key)
        made[key] = action
    return made


@pytest.fixture
def bar(qapp: QApplication, actions: dict[str, QAction]) -> EditBar:
    widget = EditBar()
    widget.set_actions(actions)
    # Раскладку надо заставить посчитаться: до этого у кнопок нет реальной
    # геометрии, а проверяем мы именно её — по ней человек и попадает мышью.
    widget.resize(900, widget.sizeHint().height())
    widget.layout().activate()
    return widget


class TestComposition:
    def test_all_three_commands_are_there(self, bar: EditBar) -> None:
        assert bar.keys() == list(KEYS)

    def test_missing_commands_are_skipped(self, qapp, actions) -> None:
        """Урезанная сборка или плагин могут не дать команды."""
        widget = EditBar()
        widget.set_actions({"edit.insert": actions["edit.insert"]})
        assert widget.keys() == ["edit.insert"]

    def test_nothing_to_show_is_not_a_crash(self, qapp) -> None:
        widget = EditBar()
        widget.set_actions({})
        assert widget.keys() == []

    def test_rebuilding_does_not_double_the_buttons(self, bar, actions) -> None:
        """Полосу пересобирают при смене языка — кнопки не должны множиться.

        Считаем то, что в раскладке: словарь кнопок переписался бы теми же
        ключами и вторую копию каждой кнопки не показал.
        """
        before = _widgets(bar)
        bar.set_actions(actions)
        assert _widgets(bar) == before

    def test_new_track_lives_on_the_timeline(self, qapp, actions, host) -> None:
        """Дорожка — про таймлайн, и кнопка её там, а не в шапке списка."""
        widget = EditBar()
        widget.set_actions({**actions, "edit.add_track": QAction("t", host)})
        assert widget.button("edit.add_track") is None


class TestSearch:
    def test_enter_asks_to_search(self, bar: EditBar) -> None:
        asked = []
        bar.search_requested.connect(asked.append)
        bar.search.setText("маяк")
        bar.search.returnPressed.emit()
        assert asked == ["маяк"]

    def test_search_comes_first(self, bar: EditBar) -> None:
        """Поиск слева и растягивается: место в узкой колонке — ему."""
        assert bar.layout().itemAt(0).widget() is bar.search


class TestHitArea:
    """Мимо кнопки промахиваться не должно."""

    @pytest.mark.parametrize("key", KEYS)
    def test_button_is_big_enough(self, bar: EditBar, key: str) -> None:
        assert bar.button(key).height() >= 28
        assert bar.button(key).width() >= 28

    def test_main_button_is_captioned_by_the_bar(self, bar, actions) -> None:
        """Подпись своя, короткая — «Реплика», а не «Новое событие» из меню."""
        caption = bar.button("edit.insert").text().strip()
        assert caption
        assert caption != actions["edit.insert"].text()

    @pytest.mark.parametrize("key", ("edit.duplicate", "edit.delete"))
    def test_rare_buttons_are_icons_with_a_name(self, bar, key: str) -> None:
        """Без подписи на экране — но с именем для экранного диктора."""
        assert bar.button(key).accessibleName()

    @pytest.mark.parametrize("key", KEYS)
    def test_button_has_an_icon(self, bar: EditBar, key: str) -> None:
        assert not bar.button(key).icon().isNull()


class TestActions:
    def test_button_runs_the_registry_action(self, bar, actions) -> None:
        fired = []
        actions["edit.insert"].triggered.connect(lambda: fired.append(1))
        bar.button("edit.insert").click()
        assert fired == [1]

    def test_disabled_action_disables_the_button(self, bar, actions) -> None:
        """Кнопка живёт состоянием действия, а не своим собственным."""
        actions["edit.delete"].setEnabled(False)
        assert not bar.button("edit.delete").isEnabled()

    def test_tip_carries_the_shortcut(self, qapp, host, actions) -> None:
        actions["edit.insert"].setShortcut(QKeySequence("Ctrl+Return"))
        widget = EditBar()
        widget.set_actions(actions)
        assert "Ctrl+Return" in widget.button("edit.insert").toolTip()

    def test_tip_without_a_shortcut_is_just_the_name(self, bar) -> None:
        assert bar.button("edit.delete").toolTip() == "edit.delete"


class TestTheme:
    def test_icons_follow_the_theme(self, bar: EditBar) -> None:
        """Значки рисуются в цвет темы и запекаются в пиксели.

        Одна таблица стилей их не перекрасит: на светлой теме остался бы
        светлый значок на светлой кнопке.
        """
        from sfstudio.ui.theme import LIGHT

        before = bar.button("edit.insert").icon().pixmap(20, 20).toImage()
        bar.set_palette(LIGHT)
        after = bar.button("edit.insert").icon().pixmap(20, 20).toImage()
        assert before != after

    def test_bar_paints_its_own_background(self, bar: EditBar) -> None:
        """Без этого свойства правило темы до полосы не доходит."""
        from PySide6.QtCore import Qt

        assert bar.property("role") == "toolhead"
        assert bar.height() == BAR_H
        assert bar.testAttribute(Qt.WA_StyledBackground)
