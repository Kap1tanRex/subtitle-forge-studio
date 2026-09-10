"""Боковая колонка: вкладки со всем функционалом и их вынос в своё окно."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication, QLabel

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.ui.detached import DetachedPanel
from sfstudio.ui.inspector import Inspector
from sfstudio.ui.layout import LayoutPreset

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(1000, 3000, "Реплика")
    return document


@pytest.fixture
def inspector(qapp: QApplication, doc: SubtitleDocument) -> Inspector:
    return Inspector(doc, UndoStack(doc))


class TestPanelHost:
    def test_builtin_tabs_are_registered(self, inspector: Inspector) -> None:
        assert inspector.panel_keys() == ["event", "format", "frame", "checks"]

    def test_added_panel_becomes_a_tab(self, inspector: Inspector) -> None:
        before = inspector.count()
        inspector.add_panel("style", "Оформление", QLabel("кузница"))
        assert inspector.count() == before + 1
        assert inspector.tabText(before) == "Оформление"

    def test_current_key_names_the_open_tab(self, inspector: Inspector) -> None:
        inspector.setCurrentIndex(2)
        assert inspector.current_key() == "frame"

    def test_widget_can_be_found_by_key(self, inspector: Inspector) -> None:
        widget = QLabel("кузница")
        inspector.add_panel("style", "Оформление", widget)
        assert inspector.widget_for("style") is widget

    def test_unknown_key_gives_nothing(self, inspector: Inspector) -> None:
        assert inspector.widget_for("такого нет") is None


class TestNarrowing:
    """Колонку должно быть можно ужать: не у всех она — рабочее место."""

    def test_column_has_a_modest_minimum(self, inspector: Inspector) -> None:
        from sfstudio.ui.inspector import MIN_COLUMN_W

        assert inspector.minimumWidth() == MIN_COLUMN_W

    def test_a_wide_panel_does_not_hold_the_column(self, inspector) -> None:
        """QTabWidget берёт минимум как максимум по всем вкладкам, включая
        закрытые: одна широкая панель запирала ширину всей колонки."""
        wide = QLabel("очень длинная строка, задающая ширину " * 4)
        inspector.add_panel("wide", "Широкая", wide)
        assert inspector.minimumSizeHint().width() < 400

    def test_forms_get_a_scroll_area(self, inspector: Inspector) -> None:
        from PySide6.QtWidgets import QScrollArea

        assert isinstance(inspector.holder_for("event"), QScrollArea)

    def test_self_scrolling_widgets_are_left_alone(self, inspector) -> None:
        """Две полосы прокрутки одна над другой — не то, чего ждут."""
        from PySide6.QtWidgets import QPlainTextEdit

        editor = QPlainTextEdit()
        inspector.add_panel("editor", "Текст", editor)
        assert inspector.holder_for("editor") is editor


class TestEnabling:
    def test_event_tabs_go_dim_without_a_selection(self, inspector: Inspector) -> None:
        inspector.set_event(None)
        assert not inspector.widget_for("event").isEnabled()

    def test_other_tabs_keep_working(self, inspector: Inspector, doc) -> None:
        """Оформление и акторы не зависят от того, что выделено."""
        inspector.add_panel("style", "Оформление", QLabel("кузница"))
        inspector.set_event(None)
        assert inspector.widget_for("style").isEnabled()

    def test_selection_lights_the_event_tabs(self, inspector: Inspector, doc) -> None:
        inspector.set_event(doc.events[0].eid)
        assert inspector.widget_for("event").isEnabled()

    def test_column_itself_stays_usable(self, inspector: Inspector) -> None:
        """Раньше гасла вся колонка — вместе с тем, что от реплики не зависит."""
        inspector.set_event(None)
        assert inspector.isEnabled()


class TestDetaching:
    def test_taking_a_panel_removes_the_tab(self, inspector: Inspector) -> None:
        before = inspector.count()
        widget = inspector.take_panel("checks")
        assert widget is not None
        assert inspector.count() == before - 1

    def test_taken_panel_is_marked(self, inspector: Inspector) -> None:
        inspector.take_panel("checks")
        assert inspector.is_detached("checks")
        assert inspector.detached_keys() == ["checks"]

    def test_the_same_panel_is_not_given_twice(self, inspector: Inspector) -> None:
        """Два окна с одним виджетом Qt не держит — второе забрало бы его."""
        inspector.take_panel("checks")
        assert inspector.take_panel("checks") is None

    def test_unknown_panel_gives_nothing(self, inspector: Inspector) -> None:
        assert inspector.take_panel("такого нет") is None

    def test_returned_panel_lands_in_its_old_place(self, inspector: Inspector) -> None:
        inspector.take_panel("format")
        inspector.restore_panel("format")
        assert [inspector.tabText(i) for i in range(inspector.count())] == [
            "Реплика", "Формат", "Кадр", "Проверки"
        ]

    def test_several_detached_return_in_order(self, inspector: Inspector) -> None:
        """Вынесенные не занимают мест, и слепой индекс промахнулся бы."""
        inspector.take_panel("format")
        inspector.take_panel("frame")
        inspector.restore_panel("frame")
        inspector.restore_panel("format")
        assert [inspector.tabText(i) for i in range(inspector.count())] == [
            "Реплика", "Формат", "Кадр", "Проверки"
        ]

    def test_returning_what_is_not_out_changes_nothing(self, inspector) -> None:
        assert not inspector.restore_panel("event")

    def test_button_asks_for_the_open_tab(self, inspector: Inspector) -> None:
        asked = []
        inspector.detach_requested.connect(asked.append)
        inspector.setCurrentIndex(1)
        inspector._detach_current()
        assert asked == ["format"]


class TestDetachedWindow:
    def test_window_keeps_the_key(self, qapp) -> None:
        from PySide6.QtWidgets import QMainWindow

        parent = QMainWindow()
        panel = DetachedPanel("qc", "Замечания", QLabel("панель"), parent)
        assert panel.key == "qc"
        assert panel.windowTitle() == "Замечания"

    def test_closing_asks_to_return(self, qapp) -> None:
        from PySide6.QtWidgets import QMainWindow

        parent = QMainWindow()
        panel = DetachedPanel("qc", "Замечания", QLabel("панель"), parent)
        seen = []
        panel.returning.connect(seen.append)
        panel.close()
        assert seen == ["qc"]

    def test_widget_survives_the_window(self, qapp) -> None:
        """Иначе закрытие окна утащило бы панель за собой навсегда."""
        from PySide6.QtWidgets import QMainWindow

        parent = QMainWindow()
        widget = QLabel("панель")
        panel = DetachedPanel("qc", "Замечания", widget, parent)
        taken = panel.take_widget()
        panel.deleteLater()
        assert taken is widget
        assert widget.parent() is None


class TestInMainWindow:
    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        made.resize(1400, 900)
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def test_all_panels_live_in_the_column(self, window) -> None:
        assert window.inspector.panel_keys() == [
            "table", "editor", "event", "format", "frame", "checks",
            "style", "actors", "qc",
        ]

    def test_every_panel_is_reachable_by_name(self, window) -> None:
        """Вкладок семь, и часть уезжает под стрелки — меню обязано их дать."""
        for key in window.inspector.panel_keys():
            window.show_panel(key)
            assert window.inspector.current_key() == key

    def test_detach_moves_the_panel_out(self, window) -> None:
        window.detach_panel("qc")
        assert window.inspector.is_detached("qc")
        assert "qc" in window._detached

    def test_detached_panel_has_its_own_window(self, window) -> None:
        window.detach_panel("qc")
        assert window._detached["qc"].isFloating()

    def test_closing_the_window_returns_the_tab(self, window) -> None:
        window.detach_panel("qc")
        window._detached["qc"].close()
        assert not window.inspector.is_detached("qc")
        assert window.inspector.widget_for("qc") is window.qc_panel

    def test_collect_brings_everything_back(self, window) -> None:
        window.detach_panel("qc")
        window.detach_panel("actors")
        window.collect_panels()
        assert window.inspector.detached_keys() == []

    def test_detaching_twice_just_raises_the_window(self, window) -> None:
        window.detach_panel("qc")
        window.detach_panel("qc")
        assert len(window._detached) == 1

    def test_detached_panels_are_remembered(self, window) -> None:
        window.detach_panel("actors")
        window._save_session()
        assert window._settings.get("ui.detached_panels") == ["actors"]

    def test_open_tab_is_remembered(self, window) -> None:
        window.show_panel("style")
        window._save_session()
        assert window._settings.get("ui.inspector_tab") == "style"

    def test_events_and_text_are_tabs_now(self, window) -> None:
        """Список реплик и их текст — там же, где остальное, и выносятся так же."""
        assert window.inspector.widget_for("table") is window.table
        window.detach_panel("editor")
        assert window.inspector.is_detached("editor")
        window.attach_panel("editor")
        assert window.inspector.current_key() == "editor"

    def test_only_two_docks_are_left(self, window) -> None:
        """Колонка и таймлайн: остальное живёт вкладками внутри колонки."""
        assert set(window._layout._docks) == {"inspector", "timeline"}

    def test_column_can_be_squeezed(self, window) -> None:
        """Проверяем сам предел, а не результат перетаскивания.

        Фактическая ширина после ``resizeDocks`` зависит от того, успело ли
        окно разложиться, и от оконного менеджера — на общем прогоне это
        давало то успех, то провал. Сломано было именно ограничение: колонка
        не пускала себя уже 471 пикселя.
        """
        assert window.inspector_dock.minimumSizeHint().width() <= 300


class TestStudioLayout:
    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        made.resize(1400, 900)
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def test_preset_exists(self) -> None:
        assert LayoutPreset("studio").title == "Монтажная"

    def test_video_stays_in_the_middle(self, window) -> None:
        """Вокруг кадра построено всё: он не панель и не закрывается."""
        window.apply_layout_preset(LayoutPreset.STUDIO)
        assert window.centralWidget() is not None

    def test_column_is_on_the_right(self, window) -> None:
        from PySide6.QtCore import Qt

        window.apply_layout_preset(LayoutPreset.STUDIO)
        dock = window._layout.dock("inspector")
        assert window.dockWidgetArea(dock) == Qt.RightDockWidgetArea

    def test_timeline_is_at_the_bottom(self, window) -> None:
        from PySide6.QtCore import Qt

        window.apply_layout_preset(LayoutPreset.STUDIO)
        dock = window._layout.dock("timeline")
        assert window.dockWidgetArea(dock) == Qt.BottomDockWidgetArea

    def test_preset_is_remembered(self, window) -> None:
        window.apply_layout_preset(LayoutPreset.STUDIO)
        assert window._settings.get("ui.layout_preset") == "studio"


class TestStyleApplication:
    @pytest.fixture
    def window(self, qapp: QApplication, tmp_path, monkeypatch):
        monkeypatch.setenv("SFSTUDIO_HOME", str(tmp_path))
        from sfstudio.ui.main_window import MainWindow

        made = MainWindow()
        for event in list(made._doc.events):
            made._doc.remove_event(event.eid)
        made._doc.create_event(0, 2000, "Первая")
        made._doc.create_event(3000, 5000, "Вторая")
        made._doc.rebuild_lookup()
        made.model.reset_document(made._doc)
        yield made
        made._undo.mark_clean()
        made.close()
        made.deleteLater()

    def test_forge_edits_the_named_style(self, window) -> None:
        """Так задуман ASS: правка стиля меняет всё, что на него ссылается."""
        from dataclasses import replace

        style = replace(window.style_forge.style(), fontsize=72)
        window._apply_forged_style(style, True)
        assert window._doc.styles[style.name].fontsize == 72

    def test_applying_is_one_undo_step(self, window) -> None:
        from dataclasses import replace

        before = window._undo.depth_used
        style = replace(window.style_forge.style(), fontsize=64)
        window._apply_forged_style(style, True)
        assert window._undo.depth_used == before + 1

    def test_undo_restores_the_old_style(self, window) -> None:
        from dataclasses import replace

        name = window.style_forge.style().name
        was = window._doc.styles[name].fontsize
        window._apply_forged_style(replace(window.style_forge.style(), fontsize=80), True)
        window._undo.undo()
        assert window._doc.styles[name].fontsize == was

    def test_forge_follows_the_selected_event(self, window) -> None:
        window._doc.styles["Крупный"] = window._doc.styles["Default"].__class__(
            name="Крупный", fontsize=90
        )
        window._doc.events[1].style = "Крупный"
        window._select_row(1)
        assert window.style_forge.style().name == "Крупный"

    def test_forge_shows_the_selected_text(self, window) -> None:
        window._select_row(1)
        assert window.style_forge.preview._doc.events[0].text == "Вторая"
