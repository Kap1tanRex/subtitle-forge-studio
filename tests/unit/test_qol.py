"""Удобство: метки говорящих, вставка из буфера, переключение плагинов."""

from __future__ import annotations

import pytest

from sfstudio.core.actor_label import (
    LABEL_PRESETS,
    LINE_BREAK,
    TEXT_FIELD,
    apply_label,
    relabel,
    relabel_events,
    strip_label,
    template_for,
)
from sfstudio.core.document import SubtitleDocument

KNOWN = ["Иван", "Пётр", "Анна Петровна"]


class TestLabelFormats:
    def test_every_preset_is_described(self) -> None:
        for fmt in LABEL_PRESETS:
            assert fmt.title
            assert fmt.key

    def test_off_leaves_text_alone(self) -> None:
        assert template_for("off") == TEXT_FIELD
        assert apply_label("Привет", "Иван", TEXT_FIELD, KNOWN) == "Привет"

    def test_brackets_above_uses_a_line_break(self) -> None:
        result = relabel("Привет", "Иван", "brackets_above", known=KNOWN)
        assert result == f"[Иван]{LINE_BREAK}Привет"

    def test_inline_stays_on_one_line(self) -> None:
        assert relabel("Привет", "Иван", "brackets_inline", known=KNOWN) == "[Иван] Привет"

    def test_colon_and_dash(self) -> None:
        assert relabel("Привет", "Иван", "colon", known=KNOWN) == "Иван: Привет"
        assert relabel("Привет", "Иван", "dash", known=KNOWN) == "Иван — Привет"

    def test_custom_template(self) -> None:
        result = relabel("Привет", "Иван", "custom", "({actor}) {text}", KNOWN)
        assert result == "(Иван) Привет"

    def test_custom_without_text_is_refused(self) -> None:
        """Шаблон без {text} потерял бы саму реплику, оставив одно имя."""
        assert template_for("custom", "[{actor}]") == TEXT_FIELD
        assert relabel("Привет", "Иван", "custom", "[{actor}]", KNOWN) == "Привет"


class TestRelabelling:
    """Второе назначение говорящего — вот где начинается настоящее."""

    def test_changing_the_actor_replaces_the_label(self) -> None:
        template = template_for("brackets_inline")
        first = apply_label("Привет", "Иван", template, KNOWN)
        second = apply_label(first, "Пётр", template, KNOWN)
        assert second == "[Пётр] Привет"
        assert "Иван" not in second

    def test_clearing_the_actor_removes_the_label(self) -> None:
        template = template_for("colon")
        labelled = apply_label("Привет", "Иван", template, KNOWN)
        assert apply_label(labelled, "", template, KNOWN) == "Привет"

    def test_changing_the_format_rebuilds_the_label(self) -> None:
        """Метку прежнего вида надо узнать, а не сложить с новой."""
        above = relabel("Привет", "Иван", "brackets_above", known=KNOWN)
        inline = relabel(above, "Иван", "colon", known=KNOWN)
        assert inline == "Иван: Привет"

    def test_repeated_application_is_idempotent(self) -> None:
        template = template_for("brackets_above")
        once = apply_label("Привет", "Иван", template, KNOWN)
        twice = apply_label(once, "Иван", template, KNOWN)
        assert once == twice

    def test_name_with_a_space_is_handled(self) -> None:
        template = template_for("colon")
        labelled = apply_label("Привет", "Анна Петровна", template, KNOWN)
        assert apply_label(labelled, "", template, KNOWN) == "Привет"


class TestLabelSafety:
    """Разбор должен быть осторожным: текст реплики важнее удобства."""

    def test_a_remark_is_not_mistaken_for_a_label(self) -> None:
        """«[смеётся]» — ремарка, а не имя, и пропадать она не должна."""
        template = template_for("brackets_inline")
        result = apply_label("[смеётся] Привет", "Иван", template, KNOWN)
        assert "[смеётся]" in result

    def test_unknown_name_is_left_alone(self) -> None:
        assert strip_label("[Некто] Привет", KNOWN) == "[Некто] Привет"

    def test_without_a_known_list_any_name_counts(self) -> None:
        """Актора могли удалить из реестра — метку всё равно надо уметь снять."""
        assert strip_label("[Некто] Привет", None) == "Привет"

    def test_plain_text_survives(self) -> None:
        for text in ("Обычная реплика", "Двоеточие: внутри текста", ""):
            assert strip_label(text, KNOWN) == text

    def test_colon_inside_a_sentence_is_not_a_label(self) -> None:
        """«Он сказал: привет» — не метка, потому что «Он сказал» не актор."""
        assert strip_label("Он сказал: привет", KNOWN) == "Он сказал: привет"


class TestRelabelEvents:
    @pytest.fixture
    def doc(self) -> SubtitleDocument:
        document = SubtitleDocument.blank()
        for i, name in enumerate(("Иван", "Пётр", "")):
            event = document.create_event(i * 3000, i * 3000 + 2000, f"Реплика {i}")
            event.name = name
        return document

    def test_only_changed_events_are_reported(self, doc) -> None:
        template = template_for("colon")
        changes = relabel_events(doc.events, template, KNOWN)
        # У третьей реплики говорящего нет — менять нечего.
        assert len(changes) == 2

    def test_repeat_changes_nothing(self, doc) -> None:
        template = template_for("colon")
        for eid, text in relabel_events(doc.events, template, KNOWN).items():
            doc.by_eid(eid).text = text
        assert relabel_events(doc.events, template, KNOWN) == {}

    def test_actor_of_overrides_the_field(self, doc) -> None:
        """Назначение сразу многим: поле ещё не изменено, а метка уже нужна."""
        template = template_for("colon")
        changes = relabel_events(
            doc.events, template, KNOWN, actor_of=lambda _e: "Пётр"
        )
        assert all(text.startswith("Пётр: ") for text in changes.values())

    def test_switching_off_strips_every_label(self, doc) -> None:
        template = template_for("colon")
        for eid, text in relabel_events(doc.events, template, KNOWN).items():
            doc.by_eid(eid).text = text

        cleared = relabel_events(doc.events, TEXT_FIELD, KNOWN)
        assert all(":" not in text for text in cleared.values())


# --------------------------------------------------------------------------- #
# Интерфейс
# --------------------------------------------------------------------------- #

pytest.importorskip("PySide6", reason="нужен PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sfstudio.core.undo import UndoStack  # noqa: E402
from sfstudio.ui.timeline import NEW_EVENT_MS, TimelineWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class TestPasteIntoTimeline:
    @pytest.fixture
    def timeline(self, qapp) -> TimelineWidget:
        doc = SubtitleDocument.blank()
        widget = TimelineWidget(doc, UndoStack(doc))
        widget.resize(1000, 400)
        return widget

    def test_text_becomes_a_new_event(self, timeline) -> None:
        created = timeline.create_event_with_text(5000, "Вставленный текст")
        assert created is not None
        assert timeline._doc.by_eid(created).text == "Вставленный текст"

    def test_duration_grows_with_the_text(self, timeline) -> None:
        """Длинный абзац за две секунды не прочитать — и он был бы «слишком
        быстрым» с первой же секунды."""
        short = timeline.create_event_with_text(0, "Коротко")
        long = timeline.create_event_with_text(120_000, "Очень " * 60)
        assert timeline._doc.by_eid(long).duration > timeline._doc.by_eid(short).duration

    def test_short_text_keeps_the_usual_duration(self, timeline) -> None:
        created = timeline.create_event_with_text(0, "Да")
        assert timeline._doc.by_eid(created).duration == NEW_EVENT_MS

    def test_insertion_is_undoable(self, timeline) -> None:
        before = len(timeline._doc.events)
        timeline.create_event_with_text(5000, "Текст")
        while not timeline._undo.is_clean:
            timeline._undo.undo()
        assert len(timeline._doc.events) == before

    def test_mouse_position_is_remembered(self, timeline) -> None:
        row = next(r for r in timeline.rows() if r.track.is_subtitle)
        timeline._remember_mouse(timeline.ms_to_x(7000), row.top + row.height / 2)
        position = timeline.mouse_position
        assert position is not None
        assert abs(position[0] - 7000) < 100

    def test_position_outside_the_tracks_is_not_remembered(self, timeline) -> None:
        """Иначе вставка «под курсором» попала бы в случайное место."""
        row = next(r for r in timeline.rows() if r.track.is_subtitle)
        timeline._remember_mouse(timeline.ms_to_x(7000), row.top + row.height / 2)
        timeline._remember_mouse(10.0, row.top + row.height / 2)  # колонка имён
        assert timeline.mouse_position is None


class TestPluginToggling:
    """Переключение плагина должно действовать сразу, а не после перезапуска."""

    @pytest.fixture
    def manager(self, tmp_path):
        from pathlib import Path

        from sfstudio.app.settings import Settings
        from sfstudio.plugins import PluginManager
        from sfstudio.services import qc

        examples = Path(__file__).resolve().parents[2] / "examples" / "plugins"
        if not examples.is_dir():
            pytest.skip("примеры плагинов не поставляются")

        from sfstudio.io import registry as io_registry

        formats = dict(io_registry.FORMATS)
        made = PluginManager(examples, Settings(tmp_path / "settings.json"))
        made.load_all()
        made.install()
        yield made
        io_registry.FORMATS.clear()
        io_registry.FORMATS.update(formats)
        qc.clear_extra_rules()

    def test_disabling_removes_the_rule(self, manager) -> None:
        from sfstudio.services import qc

        before = len(qc.EXTRA_RULES)
        manager.set_active("typography", False)
        assert len(qc.EXTRA_RULES) < before

    def test_disabling_removes_the_formats(self, manager) -> None:
        from sfstudio.io import registry as io_registry

        assert "transcript" in io_registry.FORMATS
        manager.set_active("transcript", False)
        assert "transcript" not in io_registry.FORMATS

    def test_disabling_removes_the_actions(self, manager) -> None:
        titles = {a.title for a in manager.actions()}
        assert any("Типографика" in t for t in titles)
        manager.set_active("typography", False)
        assert not any("Типографика" in a.title for a in manager.actions())

    def test_enabling_brings_everything_back(self, manager) -> None:
        from sfstudio.io import registry as io_registry

        manager.set_active("transcript", False)
        manager.set_active("transcript", True)
        assert "transcript" in io_registry.FORMATS
        assert manager.is_active("transcript")

    def test_choice_is_remembered(self, manager, tmp_path) -> None:
        """Выключенный плагин не должен воскреснуть при следующем запуске."""
        manager.set_active("typography", False)
        assert "typography" in manager._settings.get("plugins.disabled", [])

    def test_state_is_reported(self, manager) -> None:
        assert manager.is_active("typography")
        manager.set_active("typography", False)
        assert not manager.is_active("typography")

    def test_unknown_plugin_is_ignored(self, manager) -> None:
        assert manager.set_active("такого-нет", False) is None
