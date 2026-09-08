"""Три настоящих плагина как проверка системы расширений.

Плагины писались не для тестов, а под реальные задачи работы с субтитрами:
типографика расшифровки, выгрузка текста и монтажного листа, оценка объёма.
Именно поэтому они и оказались полезны как проверка — каждый уткнулся в то,
чего в контракте не было, и контракт пришлось дописывать:

* типографике понадобился доступ к документу и способ изменить много реплик
  одной командой — появились ``document()``, ``selection()``, ``set_texts()``;
* её же проверке качества понадобился способ завести находку, не импортируя
  внутренности проверок, — появился ``ctx.issue()``;
* статистике понадобилось показать таблицу, а не строку состояния, —
  появился ``show_report()``.

Тесты здесь двойные: и на сами правила плагинов, и на то, что система их
действительно поднимает и связывает с программой.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.undo import UndoStack
from sfstudio.plugins import PluginManager, PluginState
from sfstudio.services import qc

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"

pytestmark = pytest.mark.skipif(
    not EXAMPLES.is_dir(), reason="примеры плагинов не поставляются"
)


def load_module(name: str):
    """Загружает модуль плагина напрямую — для проверки его собственных правил."""
    import importlib.util

    path = EXAMPLES / name / "main.py"
    spec = importlib.util.spec_from_file_location(f"пример_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Bridge:
    """Подставная программа: ровно то, что плагин вправе от неё ждать."""

    def __init__(self, doc: SubtitleDocument, undo: UndoStack, chosen=()) -> None:
        self._doc = doc
        self._undo = undo
        self._chosen = list(chosen)
        self.messages: list[str] = []
        self.reports: list[tuple[str, str]] = []

    def document(self):
        return self._doc

    def selection(self):
        return list(self._chosen)

    def run_command(self, command):
        self._undo.run(command)

    def report(self, message: str):
        self.messages.append(message)

    def show_report(self, title: str, text: str):
        self.reports.append((title, text))


@pytest.fixture(autouse=True)
def clean_registries():
    """Реестры глобальные — за собой убираем."""
    from sfstudio.io import registry as io_registry
    from sfstudio.services.asr import registry as asr_registry

    formats = dict(io_registry.FORMATS)
    engines = set(asr_registry.registered_keys())
    yield
    io_registry.FORMATS.clear()
    io_registry.FORMATS.update(formats)
    for key in set(asr_registry.registered_keys()) - engines:
        asr_registry._FACTORIES.pop(key, None)
    qc.clear_extra_rules()


@pytest.fixture
def doc() -> SubtitleDocument:
    document = SubtitleDocument.blank()
    document.create_event(0, 3000, 'Он сказал "привет" и ушёл...')
    document.create_event(4000, 7000, "- Кто там? - спросил он .")
    document.create_event(8000, 11_000, "Всё в порядке с этой репликой")
    return document


# --------------------------------------------------------------------------- #
# Типографика
# --------------------------------------------------------------------------- #


class TestTypographyRules:
    """Сами правила: их проверяют без всякой системы плагинов."""

    @pytest.fixture(scope="class")
    @classmethod
    def plugin(cls):
        return load_module("typography")

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ('Он сказал "привет"', "Он сказал «привет»"),
            ("Пауза...", "Пауза…"),
            ("- Кто там?", "— Кто там?"),
            ("Слово  с  пробелами", "Слово с пробелами"),
            ("Пробел перед запятой , вот", "Пробел перед запятой, вот"),
            ("Кто там? - спросил он", "Кто там? — спросил он"),
        ],
    )
    def test_fixes(self, plugin, source: str, expected: str) -> None:
        assert plugin.improve(source) == expected

    @pytest.mark.parametrize(
        "source",
        [
            "Из-за угла кто-то вышел",
            "По-русски говоря",
            "Обычный текст без затей",
            "Уже «правильные» кавычки и тире — вот так",
        ],
    )
    def test_leaves_correct_text_alone(self, plugin, source: str) -> None:
        """Правило, которое портит правильный текст, хуже отсутствующего."""
        assert plugin.improve(source) == source
        assert plugin.find_problems(source) == []

    def test_markup_is_not_touched(self, plugin) -> None:
        """Внутри фигурных скобок — оформление, и «...» там законно."""
        source = "{" + chr(92) + "fs40}Текст..."
        result = plugin.improve(source)
        assert result.startswith("{" + chr(92) + "fs40}")
        assert result.endswith("Текст…")

    def test_line_break_marker_survives(self, plugin) -> None:
        """Перенос строки в ASS — это два символа, и ломать их нельзя."""
        source = "Первая" + chr(92) + "N- Вторая"
        assert plugin.improve(source) == "Первая" + chr(92) + "N— Вторая"

    def test_problems_are_named(self, plugin) -> None:
        problems = plugin.find_problems('Текст "в кавычках" и...')
        assert "прямые кавычки" in problems
        assert any("многоточ" in p for p in problems)


class TestTypographyThroughTheSystem:
    """Тот же плагин, но поднятый программой и связанный с документом."""

    @pytest.fixture
    def loaded(self, doc):
        undo = UndoStack(doc)
        bridge = Bridge(doc, undo, chosen=[doc.events[0].eid])
        manager = PluginManager(EXAMPLES)
        manager.load_all()
        manager.install()
        manager.set_host(bridge)
        return manager, bridge, undo

    def test_plugin_loads(self, loaded) -> None:
        manager, _bridge, _undo = loaded
        states = {p.info.name: p.state for p in manager.plugins}
        assert states["typography"] is PluginState.LOADED, (
            next(p.reason for p in manager.plugins if p.info.name == "typography")
        )

    def test_its_rule_finds_problems(self, loaded, doc) -> None:
        from sfstudio.services.qc import QcRunner

        runner = QcRunner()
        runner.run_all(doc)
        found = [i for i in runner.all_issues() if i.rule == "typography"]
        assert len(found) == 2, "правило плагина не сработало"

    def test_fix_all_changes_the_document(self, loaded, doc) -> None:
        manager, bridge, _undo = loaded
        action = next(a for a in manager.actions() if a.action_id.endswith("fix_all"))
        action.callback()

        assert doc.events[0].text == "Он сказал «привет» и ушёл…"
        assert bridge.messages, "плагин ничего не сообщил о результате"

    def test_fix_all_is_one_undo(self, loaded, doc) -> None:
        """Массовая правка обязана откатываться одним движением."""
        manager, _bridge, undo = loaded
        before = [e.text for e in doc.events]

        action = next(a for a in manager.actions() if a.action_id.endswith("fix_all"))
        action.callback()
        assert [e.text for e in doc.events] != before

        undo.undo()
        assert [e.text for e in doc.events] == before

    def test_fix_selected_touches_only_the_selection(self, loaded, doc) -> None:
        manager, _bridge, _undo = loaded
        untouched = doc.events[1].text

        action = next(
            a for a in manager.actions() if a.action_id.endswith("fix_selected")
        )
        action.callback()

        assert doc.events[0].text != 'Он сказал "привет" и ушёл...'
        assert doc.events[1].text == untouched

    def test_clean_document_reports_nothing_to_do(self, loaded, doc) -> None:
        manager, bridge, _undo = loaded
        action = next(a for a in manager.actions() if a.action_id.endswith("fix_all"))
        action.callback()
        bridge.messages.clear()
        action.callback()
        assert any("нечего" in m for m in bridge.messages)


# --------------------------------------------------------------------------- #
# Транскрипт и монтажный лист
# --------------------------------------------------------------------------- #


class TestTranscript:
    @pytest.fixture(scope="class")
    @classmethod
    def plugin(cls):
        return load_module("transcript")

    @pytest.fixture
    def dialogue(self) -> SubtitleDocument:
        document = SubtitleDocument.blank()
        for start, name, text in (
            (1000, "Иван", "Первая реплика"),
            (3200, "Иван", "её продолжение"),
            (6000, "Пётр", "А это уже другой"),
        ):
            event = document.create_event(start, start + 1800, text)
            event.name = name
        return document

    def test_same_speaker_becomes_one_paragraph(self, plugin, dialogue) -> None:
        """Реплики одного говорящего — это его речь, разбитая по кадрам."""
        text = plugin.write_transcript(dialogue)
        assert "Иван: Первая реплика её продолжение" in text
        assert "Пётр: А это уже другой" in text

    def test_speaker_change_starts_a_paragraph(self, plugin, dialogue) -> None:
        assert plugin.write_transcript(dialogue).count("\n\n") == 1

    def test_markup_is_stripped(self, plugin) -> None:
        document = SubtitleDocument.blank()
        document.create_event(0, 2000, "{" + chr(92) + "i1}Курсив{" + chr(92) + "i0}")
        assert plugin.write_transcript(document).strip() == "Курсив"

    def test_line_breaks_become_spaces(self, plugin) -> None:
        """Разрыв внутри реплики нужен ради ширины кадра, а не ради смысла."""
        document = SubtitleDocument.blank()
        document.create_event(0, 2000, "Первая" + chr(92) + "Nвторая")
        assert plugin.write_transcript(document).strip() == "Первая вторая"

    def test_comments_are_skipped(self, plugin) -> None:
        document = SubtitleDocument.blank()
        document.create_event(0, 2000, "Речь")
        note = document.create_event(3000, 4000, "Служебная пометка")
        note.comment = True
        assert "пометка" not in plugin.write_transcript(document)

    def test_sheet_is_readable_by_a_spreadsheet(self, plugin, dialogue) -> None:
        """Точка с запятой и метка порядка байтов — ради русского Excel."""
        text = plugin.write_sheet(dialogue)
        assert text.startswith("﻿")

        rows = list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";"))
        assert rows[0][0] == "№"
        assert len(rows) == 4
        assert rows[1][4] == "Иван"

    def test_sheet_uses_comma_for_decimals(self, plugin, dialogue) -> None:
        text = plugin.write_sheet(dialogue)
        assert "1,80" in text or "1,8" in text

    def test_timecode_format(self, plugin) -> None:
        assert plugin._timecode(3_661_123) == "01:01:01,123"
        assert plugin._timecode(-5) == "00:00:00,000"

    def test_formats_reach_the_registry(self, doc) -> None:
        from sfstudio.io import registry as io_registry

        manager = PluginManager(EXAMPLES)
        manager.load_all()
        manager.install()

        assert "transcript" in io_registry.FORMATS
        assert "sheet" in io_registry.FORMATS

    def test_write_only_formats_are_not_offered_for_opening(self, doc) -> None:
        """Из транскрипта тайминги не восстановить — и обещать этого нельзя."""
        from sfstudio.io import registry as io_registry

        manager = PluginManager(EXAMPLES)
        manager.load_all()
        manager.install()

        readable = {f.fid for f in io_registry.supported_read()}
        writable = {f.fid for f in io_registry.supported_write()}
        assert "transcript" not in readable
        assert "transcript" in writable


# --------------------------------------------------------------------------- #
# Статистика
# --------------------------------------------------------------------------- #


class TestStatistics:
    @pytest.fixture(scope="class")
    @classmethod
    def plugin(cls):
        return load_module("statistics")

    @pytest.fixture
    def sample(self) -> SubtitleDocument:
        document = SubtitleDocument.blank()
        for i, name in enumerate(("Иван", "Иван", "Пётр")):
            event = document.create_event(i * 4000, i * 4000 + 2500, f"Реплика {i}")
            event.name = name
        return document

    def test_counts_events_and_words(self, plugin, sample) -> None:
        data = plugin.collect(sample)
        assert data["реплик"] == 3
        assert data["слов"] == 6

    def test_speech_time_merges_overlaps(self, plugin) -> None:
        """Двое говорят одновременно — это одна секунда речи, а не две."""
        document = SubtitleDocument.blank()
        document.create_event(0, 2000, "Первый")
        document.create_event(1000, 3000, "Второй поверх")
        data = plugin.collect(document)
        assert data["время речи"] == 3000

    def test_speech_time_without_overlaps_is_the_sum(self, plugin, sample) -> None:
        data = plugin.collect(sample)
        assert data["время речи"] == 7500

    def test_speakers_are_counted(self, plugin, sample) -> None:
        data = plugin.collect(sample)
        assert data["говорящих"] == 2
        assert data["по говорящим"]["Иван"] == 2

    def test_comments_do_not_count(self, plugin, sample) -> None:
        note = sample.create_event(20_000, 21_000, "Пометка")
        note.comment = True
        data = plugin.collect(sample)
        assert data["реплик"] == 3
        assert data["комментариев"] == 1

    def test_empty_document_does_not_crash(self, plugin) -> None:
        data = plugin.collect(SubtitleDocument.blank())
        assert data["реплик"] == 0
        assert plugin.render(data)

    def test_report_mentions_the_speakers(self, plugin, sample) -> None:
        report = plugin.render(plugin.collect(sample))
        assert "Иван" in report and "Пётр" in report

    def test_report_goes_to_a_window(self, sample) -> None:
        """Таблицу по говорящим в строку состояния не уместить."""
        undo = UndoStack(sample)
        bridge = Bridge(sample, undo)
        manager = PluginManager(EXAMPLES)
        manager.load_all()
        manager.install()
        manager.set_host(bridge)

        action = next(
            a for a in manager.actions()
            if a.plugin == "statistics" and a.action_id.endswith(".show")
        )
        action.callback()

        assert bridge.reports, "отчёт никуда не попал"
        title, text = bridge.reports[0]
        assert "Статистика" in title
        assert "Реплик" in text


# --------------------------------------------------------------------------- #
# Контракт, дописанный под эти плагины
# --------------------------------------------------------------------------- #


class TestHostContract:
    def test_actions_fail_clearly_without_a_host(self, doc) -> None:
        """Действие вне программы — понятная ошибка, а не «None не имеет...»."""
        from sfstudio.plugins import PluginContext, PluginError

        context = PluginContext(plugin="п", folder=EXAMPLES)
        with pytest.raises(PluginError, match="вне работающей программы"):
            context.document()

    def test_set_texts_is_a_single_command(self, doc) -> None:
        from sfstudio.plugins import PluginContext

        undo = UndoStack(doc)
        context = PluginContext(
            plugin="п", folder=EXAMPLES, host=Bridge(doc, undo)
        )
        before = [e.text for e in doc.events]
        changed = context.set_texts(
            {e.eid: f"новый {i}" for i, e in enumerate(doc.events)}
        )

        assert changed == 3
        undo.undo()
        assert [e.text for e in doc.events] == before

    def test_set_texts_with_nothing_does_nothing(self, doc) -> None:
        from sfstudio.plugins import PluginContext

        undo = UndoStack(doc)
        context = PluginContext(plugin="п", folder=EXAMPLES, host=Bridge(doc, undo))
        assert context.set_texts({}) == 0
        assert undo.is_clean

    def test_show_report_falls_back_to_the_log(self, doc) -> None:
        """Программа без окон не должна терять отчёт молча."""
        from sfstudio.plugins import PluginContext

        class Silent(Bridge):
            show_report = None  # окон нет

        undo = UndoStack(doc)
        context = PluginContext(plugin="п", folder=EXAMPLES, host=Silent(doc, undo))
        context.show_report("Заголовок", "Текст отчёта")
        assert any("Текст отчёта" in m for m in context.messages)

    def test_qc_context_builds_issues(self, doc) -> None:
        """Правилу из плагина незачем знать, как устроена находка внутри."""
        from sfstudio.services.qc import QcRunner, register_rule

        def rule(event, ctx):
            yield ctx.issue(event, rule="проба", message="нашлось")

        register_rule(rule)
        runner = QcRunner()
        runner.run_all(doc)
        assert any(i.rule == "проба" for i in runner.all_issues())
