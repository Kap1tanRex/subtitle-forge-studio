"""Главное окно.

Собирает кадр с перетаскиванием субтитров, панель транспорта, таблицу событий,
редактор текста, инспектор и многодорожечный таймлайн. Видео играет через
libmpv; когда её нет, кадр рисуется на шахматном фоне, а время задаётся
таймлайном — правка таймингов и текста работает и так.

Три правила, которые здесь важно не нарушить:

* **виджеты не мутируют документ напрямую**, только через ``UndoStack.run``;
* синхронизация «таблица ↔ кадр ↔ таймлайн ↔ инспектор» идёт через один флаг
  ``_syncing``, иначе сигналы зацикливаются: выбор строки двигает таймлайн, тот
  сообщает о выборе обратно в таблицу, и так до переполнения стека;
* **инспектор не хранит состояние реплики** — после любой правки он перечитывает
  документ. Своя копия значений разъезжается при отмене и при правке мышью.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from sfstudio import __version__
from sfstudio.app.settings import Settings
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands import (
    AddTrack,
    ApplyTimings,
    CompositeCommand,
    DeleteEvents,
    DuplicateEvents,
    InsertEvent,
    SetOverrideTag,
    SetText,
    UpdateStyle,
)
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.project import (
    PROJECT_SUFFIX,
    Project,
    ProjectError,
    ProjectState,
)
from sfstudio.core.reference import ReferenceTrack
from sfstudio.core.undo import UndoStack
from sfstudio.io import registry
from sfstudio.io.project_file import load_project, save_project
from sfstudio.plugins import PluginState
from sfstudio.services.qc import PROFILES, QcRunner, default_profile
from sfstudio.services.style_presets import PresetLibrary
from sfstudio.ui.actions import ActionRegistry
from sfstudio.ui.event_table import (
    COL_ACTOR,
    COL_STYLE,
    ChoiceDelegate,
    EventTableModel,
    EventTableView,
)
from sfstudio.ui.inspector import Inspector
from sfstudio.ui.layout import DockSpec, LayoutManager, LayoutPreset
from sfstudio.ui.motion import flash
from sfstudio.ui.qc_panel import QcPanel
from sfstudio.ui.quick_format import QuickFormatPanel
from sfstudio.ui.timeline import TimelineWidget
from sfstudio.ui.transport import TransportBar
from sfstudio.ui.video_pane import VideoPane

SUBTITLE_FILTER = (
    "Субтитры (*.ass *.ssa *.srt *.vtt *.ttml *.dfxp);;"
    "ASS (*.ass *.ssa);;SubRip (*.srt);;WebVTT (*.vtt);;"
    "TTML / DFXP (*.ttml *.dfxp *.xml);;Все файлы (*)"
)
PROJECT_FILTER = f"Проект SubtitleForge (*{PROJECT_SUFFIX});;Все файлы (*)"
MEDIA_FILTER = (
    "Видео и аудио (*.mkv *.mp4 *.mov *.webm *.avi *.m4v *.ts *.wav *.mp3 *.aac);;"
    "Все файлы (*)"
)


class MainWindow(QMainWindow):
    def __init__(
        self,
        doc: SubtitleDocument | None = None,
        settings: Settings | None = None,
    ) -> None:
        super().__init__()
        self._doc = doc or _demo_document()
        self._undo = UndoStack(self._doc)
        self._undo.on_change = self._on_document_changed

        self._syncing = False
        self._suppress_editor_signal = False
        self._media_path: Path | None = None
        self._media_token = 0
        self._embedded_index: int | None = None
        self._settings = settings or Settings()
        self._qc = QcRunner(profile=PROFILES.get(
            str(self._settings.get('qc.profile', 'general')), default_profile()
        ))
        self._presets = PresetLibrary()
        self._pool = QThreadPool.globalInstance()
        #: Открытый проект. None — работа идёт с одним файлом субтитров:
        #: это законный режим, и заставлять заводить проект ради правки
        #: одного .srt было бы навязчиво.
        self._project: Project | None = None
        #: Оригинал, с которого идёт перевод. ``None`` — режим обычный.
        self._reference = None
        #: Глоссарий проекта: как переводить термины и имена.
        self._glossary = None

        # Движки распознавания ищут скачанное сами — им нужен только путь.
        from sfstudio.app.storage import data_root
        from sfstudio.services.asr.registry import set_downloads_dir

        set_downloads_dir(data_root(self._settings))

        self._plugins = self._load_plugins()

        self.resize(1440, 900)
        self._build_ui()
        self._build_actions()
        self._refresh_title()
        # Проверки считаются сразу: иначе метки в таблице появлялись бы только
        # после первой правки, и открытый из командной строки файл выглядел бы
        # чистым, даже если в нём есть проблемы.
        self._qc.run_all(self._doc)
        self.model.refresh_qc()
        self._refresh_seek_marks()
        self._restore_layout()

        # Мост плагинам ставится после сборки интерфейса: до неё нет ни
        # таблицы, чтобы спросить выделение, ни строки состояния.
        self._plugins.set_host(_PluginBridge(self))

        from sfstudio.app.storage import data_root
        from sfstudio.services.asr.registry import set_downloads_dir

        set_downloads_dir(data_root(self._settings))
        self.apply_theme()
        self._reschedule_autosave()
        self._select_row(0)

    def _load_plugins(self) -> object:
        """Поднимает плагины пользователя. Ни один сбой сюда не долетает.

        Плагины загружаются до сборки меню: их действия становятся обычными
        пунктами общего реестра, попадают в командную палитру и проверяются
        на конфликт сочетаний наравне со встроенными.
        """
        from sfstudio.app.storage import plugins_dir
        from sfstudio.plugins import PluginManager

        try:
            manager = PluginManager(plugins_dir(self._settings), self._settings)
            manager.load_all(enabled=bool(self._settings.get("plugins.enabled", False)))
            # Регистрация в реестрах — отдельным шагом, после успешной
            # загрузки: движок из плагина, упавшего на середине setup, в
            # общий реестр попасть не должен.
            manager.install()
        except Exception:
            # Каталог может быть недоступен, настройки — испорчены. Программа
            # без плагинов полноценна, а без запуска — нет.
            manager = PluginManager(None, self._settings)
        return manager

    def _restore_layout(self) -> None:
        """Возвращает раскладку прошлого сеанса или применяет пресет."""
        if not self._layout.restore():
            preset = LayoutPreset(
                str(self._settings.get("ui.layout_preset", "default"))
                if str(self._settings.get("ui.layout_preset", "default"))
                in {p.value for p in LayoutPreset}
                else "default"
            )
            self._layout.apply_preset(preset)
        if self.qc_dock is not None:
            self.qc_dock.setVisible(bool(self._settings.get("qc.panel_visible", False)))

    def apply_theme(self, name: str | None = None) -> None:
        """Применяет тему ко всему окну, включая нарисованное вручную.

        Одной таблицы стилей мало: таймлайн, волна, кадр и раскраска строк
        рисуются кодом по своей палитре. Пока её не раздать, светлая тема
        давала окно, наполовину оставшееся тёмным.
        """
        from sfstudio.ui.appearance import apply as apply_appearance
        from sfstudio.ui.theme import apply_font_scale

        app = QApplication.instance()
        apply_font_scale(app, self._settings.get("ui.font_scale", 100))
        if name is not None:
            self._settings.set("ui.theme", str(name))
        # Вся сборка вида — в одном месте: тема, цвета темы, акцент,
        # добавка к таблице стилей и анимации.
        palette = apply_appearance(app, self._settings)
        # Держим у себя: вспышки и прочая своя отрисовка спрашивают цвет
        # именно у окна, а собирается он в одном месте и только здесь.
        self._palette = palette

        for widget in (self.timeline, self.transport, self.qc_panel,
                       self.video_pane.canvas, self.video_pane.overlay, self.model):
            with contextlib.suppress(AttributeError):
                widget.set_palette(palette)
        if hasattr(self, "_speller"):
            self._speller.set_colour(palette.danger)
        self.update()

    def apply_layout_preset(self, preset: LayoutPreset) -> None:
        self._layout.apply_preset(preset)
        self._show_status(f"Раскладка: {preset.title}")

    def reset_layout(self) -> None:
        self._layout.reset()
        self._show_status("Раскладка сброшена")

    # -- построение интерфейса ------------------------------------------------ #

    def _build_ui(self) -> None:
        self.video_pane = VideoPane(self._doc, self._undo)
        self.preview = self.video_pane
        self.video_pane.selection_changed.connect(self._on_preview_selection)
        self.video_pane.document_edited.connect(self._on_widget_edit)
        self.video_pane.status_message.connect(self._show_status)
        self.video_pane.position_changed.connect(self._on_player_position)
        self.video_pane.context_requested.connect(self._on_frame_context)

        self.timeline = TimelineWidget(self._doc, self._undo)
        # Назначение говорящего из контекстного меню таймлайна идёт тем же
        # путём, что и из таблицы: с меткой в тексте, если она включена.
        self.timeline.actor_command = self.actor_command
        self.timeline.time_changed.connect(self._on_timeline_time)
        self.timeline.selection_changed.connect(self._on_timeline_selection)
        self.timeline.document_edited.connect(self._on_widget_edit)
        self.timeline.status_message.connect(self._show_status)

        self.transport = TransportBar()
        self.transport.play_pause.connect(self.video_pane.toggle_pause)
        self.transport.step_frame.connect(self.video_pane.frame_step)
        self.transport.seek_edge.connect(self._seek_edge)
        self.transport.speed_selected.connect(self._on_speed_selected)
        self.transport.seek_requested.connect(self._on_seek_requested)
        self.transport.loop_toggled.connect(self._on_loop_toggled)
        self.transport.volume_changed.connect(self._on_volume_changed)
        self.transport.mute_toggled.connect(self.video_pane.set_muted)
        self.video_pane.pause_changed.connect(self.transport.set_paused)
        self.video_pane.speed_changed.connect(self.transport.set_speed)
        self.video_pane.duration_changed.connect(self.transport.set_duration)
        self.video_pane.position_changed.connect(self.transport.set_position)

        self.quick_format = QuickFormatPanel(self._doc, self._undo, parent=self)
        self.quick_format.tag_requested.connect(self._on_quick_tag)

        self.inspector = Inspector(self._doc, self._undo)
        self.inspector.document_edited.connect(self._on_widget_edit)
        self.inspector.actors_requested.connect(self.open_actors)

        self.model = EventTableModel(
            self._doc, qc=self._qc, undo=self._undo,
            actor_command=self.actor_command,
        )
        # Вся настройка таблицы живёт в самом виджете: он ещё и подбирает
        # состав колонок под свою ширину, а для этого ему нужно знать про
        # них больше, чем уместно держать в главном окне.
        self.table = EventTableView()
        self.table.setModel(self.model)
        # Стиль и говорящий выбираются из существующих: набирать их руками —
        # верный способ завести актора «Иван » с пробелом на конце.
        self.table.setItemDelegateForColumn(
            COL_STYLE,
            ChoiceDelegate(lambda: list(self._doc.styles), self.table, editable=False),
        )
        self.table.setItemDelegateForColumn(
            COL_ACTOR,
            ChoiceDelegate(self._actor_names, self.table),
        )
        self.table.selectionModel().selectionChanged.connect(self._on_table_selection)
        self.table.insert_requested.connect(self.insert_event)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Текст реплики (\\N — перевод строки)")
        self.editor.setMaximumHeight(96)
        self.editor.textChanged.connect(self._on_editor_changed)
        self._setup_spellcheck()

        # Подсказка в одну строку, подробности — по наведению. Двух строк она
        # стоила постоянно, а читают её один раз: место под редактором нужнее
        # тексту реплики.
        self.hint = QLabel(
            "Кадр: тяните субтитр · Таймлайн: края реплики, Ctrl+колесо — зум"
        )
        self.hint.setProperty("role", "hint")
        self.hint.setToolTip(
            "Кадр: тяните субтитр мышью, Shift — вдоль оси, Alt — без "
            "магнитов, Esc — отмена жеста.\n"
            "Таймлайн: тяните края реплики, Ctrl+колесо — зум по времени, "
            "Alt+колесо — высота дорожек, Ctrl+клик по пустому — новая "
            "реплика.\n"
            "Высоту дорожки тяните за её нижнюю границу — и в колонке имён, "
            "и в самой дорожке.\n"
            "Выделение: обведите реплики рамкой по пустому месту, Shift — "
            "добавить к выделенному, Ctrl+клик по реплике — включить или "
            "исключить её. Выделенную группу двигают как одну."
        )

        # Оригинал над переводом: взгляд идёт сверху вниз, и читать исходник
        # после собственного текста неудобно. Только для чтения — это чужой
        # текст, и править его здесь нечего.
        self.original = QPlainTextEdit()
        self.original.setReadOnly(True)
        self.original.setMaximumHeight(64)
        self.original.setPlaceholderText("Оригинал")
        self.original.setProperty("role", "reference")
        self.original.setVisible(False)

        editor_box = QWidget()
        editor_layout = QVBoxLayout(editor_box)
        editor_layout.setContentsMargins(6, 4, 6, 4)
        editor_layout.setSpacing(4)
        editor_layout.addWidget(self.original)
        editor_layout.addWidget(self.editor)
        editor_layout.addWidget(self.hint)

        from sfstudio.ui.actors_dialog import ActorsPanel

        self.actors_panel = ActorsPanel(self._doc, self._undo)
        self.actors_panel.document_edited.connect(self._on_widget_edit)
        self.actors_panel.assign_requested.connect(self._assign_actor)

        self.qc_panel = QcPanel(self._qc, self._doc)
        self.qc_panel.issue_activated.connect(self._select_eid)
        self.qc_panel.profile_changed.connect(self._on_qc_profile)

        # Кадр — центральный виджет, а не панель: он не закрывается и не
        # отрывается, потому что вокруг него всё и построено. Под ним —
        # транспорт: он относится к кадру, а не к таймлайну, и уезжать
        # вместе с панелями не должен.
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.video_pane, 1)
        center_layout.addWidget(self.transport)
        self.setCentralWidget(center)
        self.setDockNestingEnabled(True)

        self._layout = LayoutManager(self, self._settings)
        self._layout.add(DockSpec("table", "События", self.table, Qt.RightDockWidgetArea))
        self._layout.add(DockSpec("editor", "Текст", editor_box, Qt.RightDockWidgetArea))
        self._layout.add(
            DockSpec("inspector", "Инспектор", self.inspector, Qt.RightDockWidgetArea)
        )
        self._layout.add(
            DockSpec("timeline", "Таймлайн", self.timeline, Qt.BottomDockWidgetArea)
        )
        self._layout.add(
            DockSpec("qc", "Контроль качества", self.qc_panel, Qt.RightDockWidgetArea)
        )
        self._layout.add(
            DockSpec("actors", "Акторы", self.actors_panel, Qt.RightDockWidgetArea)
        )
        self.qc_dock = self._layout.dock("qc")

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.status_label = QLabel("")
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.status_label)
        self._show_status("Готово")

    def _build_actions(self) -> None:
        """Собирает все действия в реестр, затем раскладывает по меню.

        Реестр, а не создание ``QAction`` по месту: он видит все сочетания
        разом, поэтому конфликты ловятся тестом, а командная палитра получает
        полный список команд без отдельного реестра.
        """
        registry = self._actions_registry = ActionRegistry()
        add = registry.add

        add("file.new_project", "Новый проект…", self.new_project,
            shortcut="Ctrl+N", menu="Файл")
        add("file.open_project", "Открыть проект…", self.open_project,
            shortcut="Ctrl+O", menu="Файл")
        add("file.save_project", "Сохранить проект", self.save_project_file,
            shortcut="Ctrl+S", menu="Файл")
        add("file.save_project_as", "Сохранить проект как…", self.save_project_as,
            shortcut="Ctrl+Shift+S", menu="Файл")

        add("file.open_subtitles", "Импорт субтитров…", self.open_subtitles,
            shortcut="Ctrl+I", menu="Файл")
        add("file.open_reference", "Открыть оригинал…", self.open_reference,
            menu="Файл",
            tip="Второй файл субтитров, с которого идёт перевод. "
                "Он показывается рядом и не меняется")
        add("file.close_reference", "Убрать оригинал", self.close_reference,
            menu="Файл")
        add("file.open_media", "Открыть видео…", self.open_media,
            shortcut="Ctrl+Shift+O", menu="Файл")
        add("file.save", "Экспорт субтитров", self.save_file,
            shortcut="Ctrl+E", menu="Файл")
        add("file.save_as", "Экспорт субтитров как…", self.save_file_as,
            shortcut="Ctrl+Shift+E", menu="Файл")
        add("file.mux", "Записать в контейнер…", self.save_into_container,
            shortcut="Ctrl+M", menu="Файл")
        add("file.quit", "Выход", self.close, shortcut="Ctrl+Q", menu="Файл")

        add("edit.undo", "Отменить", self.undo, shortcut="Ctrl+Z", menu="Правка")
        add("edit.redo", "Вернуть", self.redo, shortcut="Ctrl+Y", menu="Правка")
        add("edit.insert", "Новое событие", self.insert_event,
            shortcut="Ctrl+Return", menu="Правка")
        add("edit.duplicate", "Дублировать", self.duplicate_event,
            shortcut="Ctrl+D", menu="Правка")
        add("edit.delete", "Удалить", self.delete_events,
            shortcut="Ctrl+Delete", menu="Правка")
        add("edit.clear_position", "Сбросить позицию", self.preview.clear_position,
            shortcut="Ctrl+Shift+0", menu="Правка")
        add("edit.style_presets", "Шаблоны оформления…", self.open_style_presets,
            shortcut="Ctrl+Shift+Y", menu="Правка")

        add("edit.recognise", "Распознать речь…", self.open_asr,
            shortcut="Ctrl+R", menu="Правка")
        add("edit.relabel", "Обновить метки говорящих", self.relabel_all,
            menu="Правка",
            tip="Проставить или убрать имена акторов в тексте по настройке")
        add("edit.glossary", "Глоссарий…", self.open_glossary,
            menu="Правка",
            tip="Как переводить термины и имена. Проверяется по оригиналу")
        add("edit.actors", "Акторы…", self.open_actors,
            shortcut="Ctrl+Shift+A", menu="Правка")
        add("edit.add_track", "Новая дорожка", self.add_track,
            shortcut="Ctrl+Shift+N", menu="Правка")

        add("timing.auto", "Доводка таймингов…", self.open_auto_timing,
            shortcut="Ctrl+T", menu="Тайминг")
        add("timing.shift", "Сдвиг таймингов…", self.open_shift_times,
            shortcut="Ctrl+Shift+T", menu="Тайминг")

        add("view.guides", "Направляющие", self.preview.toggle_guides,
            shortcut="F6", menu="Вид", checkable=True, checked=True)
        add("view.safe_area", "Безопасные зоны", self.preview.toggle_safe_area,
            shortcut="F7", menu="Вид", checkable=True)
        add("view.snapping", "Магниты таймлайна", self.timeline.set_snapping,
            shortcut="F8", menu="Вид", checkable=True, checked=True)
        add("view.tracks_taller", "Дорожки выше",
            lambda: self.timeline.zoom_tracks(1.25),
            shortcut="Ctrl+Alt+Up", menu="Вид")
        add("view.tracks_shorter", "Дорожки ниже",
            lambda: self.timeline.zoom_tracks(0.8),
            shortcut="Ctrl+Alt+Down", menu="Вид")
        add("view.tracks_reset", "Обычная высота дорожек",
            self.timeline.reset_track_zoom, menu="Вид")

        add("view.fit_all", "Уместить всё", self.timeline.fit_all,
            shortcut="Ctrl+Home", menu="Вид")
        # Ctrl+F по всем привычкам — это поиск. «Уместить выделение» жило на
        # нём только потому, что поиска не было.
        add("view.fit_selection", "Уместить выделение", self.timeline.fit_selection,
            shortcut="Ctrl+Shift+F", menu="Вид")
        add("view.qc_panel", "Панель контроля качества", self._toggle_qc,
            shortcut="F4", menu="Вид", checkable=True,
            checked=bool(self._settings.get("qc.panel_visible", False)))
        add("view.next_issue", "Следующая проблема", self.goto_next_issue,
            shortcut="F9", menu="Вид")
        add("view.qc_report", "Отчёт о проверке…", self.save_qc_report,
            menu="Вид",
            tip="Список замечаний и требования профиля — для сдачи работы")
        add("view.next_question", "Следующий вопрос", self.goto_next_question,
            shortcut="Shift+F9", menu="Вид",
            tip="Реплики, помеченные вопросом в контекстном меню")
        add("view.reset_layout", "Сбросить раскладку", self.reset_layout, menu="Вид")

        add("play.toggle", "Играть / Пауза", self.video_pane.toggle_pause,
            shortcut="Space", menu="Воспроизведение")
        add("play.frame_forward", "Кадр вперёд",
            lambda: self.video_pane.frame_step(True),
            shortcut="Right", menu="Воспроизведение")
        add("play.frame_back", "Кадр назад",
            lambda: self.video_pane.frame_step(False),
            shortcut="Left", menu="Воспроизведение")
        add("play.loop_event", "Играть текущую реплику", self.play_current_event,
            shortcut="Ctrl+Space", menu="Воспроизведение")
        add("play.volume_up", "Громче", lambda: self.step_volume(True),
            shortcut="Ctrl+Up", menu="Воспроизведение")
        add("play.volume_down", "Тише", lambda: self.step_volume(False),
            shortcut="Ctrl+Down", menu="Воспроизведение")
        add("play.mute", "Заглушить", self.toggle_mute,
            shortcut="M", menu="Воспроизведение")

        add("play.faster", "Быстрее", lambda: self.step_speed(True),
            shortcut="]", menu="Воспроизведение")
        add("play.slower", "Медленнее", lambda: self.step_speed(False),
            shortcut="[", menu="Воспроизведение")
        add("play.normal_speed", "Обычная скорость", lambda: self._on_speed_selected(1.0),
            shortcut="Ctrl+]", menu="Воспроизведение")

        add("file.preferences", "Настройки…", self.open_preferences,
            shortcut="Ctrl+,", menu="Файл")

        add("help.palette", "Командная палитра…", self.open_command_palette,
            shortcut="Ctrl+P", menu="Справка")
        add("help.about", "О программе", self.about, menu="Справка")
        add("help.shortcuts", "Горячие клавиши…", self.open_shortcuts, menu="Справка")

        add("edit.paste_event", "Вставить реплику из буфера", self.paste_event,
            shortcut="Ctrl+Shift+V", menu="Правка",
            tip="Текст из буфера обмена станет новой репликой")
        add("edit.find", "Найти и заменить…", self.open_find,
            shortcut="Ctrl+F", menu="Правка")
        add("edit.find_selection", "Найти в выделенных…", self.find_in_selection,
            shortcut="Ctrl+Shift+H", menu="Правка")

        for preset in LayoutPreset:
            add(f"layout.{preset.value}", f"Раскладка: {preset.title}",
                lambda p=preset: self.apply_layout_preset(p), menu="Вид")

        self._add_plugin_actions(add)

        # Пользовательские сочетания накладываются до сборки меню: иначе в
        # меню осталось бы то, что записано в коде, и подпись разошлась бы с
        # тем, что реально срабатывает.
        saved = self._settings.get("ui.shortcuts", {})
        if isinstance(saved, dict) and saved:
            registry.apply_overrides({str(k): str(v) for k, v in saved.items()})

        actions = registry.build(self)
        self._install_menus(registry, actions)

        self.mux_action = actions["file.mux"]
        self.mux_action.setEnabled(False)
        self.undo_action = actions["edit.undo"]
        self.redo_action = actions["edit.redo"]
        # «Убрать оригинал» недоступно, пока оригинала нет: пункт, который
        # ничего не делает, только заставляет гадать, сработал ли он.
        self.reference_close_action = actions["file.close_reference"]
        self.reference_close_action.setEnabled(self._reference is not None)
        self._sync_undo_actions()

    def _add_plugin_actions(self, add) -> None:
        """Переносит действия плагинов в общий реестр.

        Обработчик оборачивается: ошибка в плагине должна кончаться строкой в
        статусе, а не закрытым окном. Отменить её потом нельзя — плагин мог
        успеть что-то сделать, — но это уже его ответственность, а не повод
        уронить программу.
        """
        for item in self._plugins.actions():
            def run(callback=item.callback, title=item.title) -> None:
                try:
                    callback()
                except Exception as exc:
                    self._show_status(f"Плагин: «{title}» — {exc}")

            add(
                item.action_id,
                item.title,
                run,
                shortcut=item.shortcut,
                menu="Плагины",
                tip=item.tooltip,
            )

    def _install_menus(self, registry: ActionRegistry, actions: dict) -> None:
        """Раскладывает действия по меню в порядке регистрации."""
        menus: dict[str, object] = {}
        names = ["Файл", "Правка", "Тайминг", "Вид", "Воспроизведение"]
        # Меню плагинов появляется, только когда плагины вообще есть: пустой
        # пункт в строке меню сообщал бы о возможности, которой человек не
        # просил и не увидит.
        if self._plugins.plugins:
            names.append("Плагины")
        names.append("Справка")
        for name in names:
            menus[name] = self.menuBar().addMenu(f"&{name}")

        layout_menu = None
        for spec in registry:
            menu = menus.get(spec.menu)
            if menu is None:
                continue
            if spec.key.startswith("layout."):
                if layout_menu is None:
                    layout_menu = menu.addMenu("Раскладка")
                layout_menu.addAction(actions[spec.key])
                continue
            menu.addAction(actions[spec.key])

        panels_menu = menus["Вид"].addMenu("Панели")
        for action in self._layout.toggle_actions():
            panels_menu.addAction(action)

        plugins_menu = menus.get("Плагины")
        if plugins_menu is not None:
            self._plugins_menu = plugins_menu
            # Меню собирается при каждом открытии: состав действий меняется
            # вместе с включением плагинов, и статичный список разошёлся бы
            # с тем, что работает на самом деле.
            plugins_menu.aboutToShow.connect(self._rebuild_plugins_menu)
            self._rebuild_plugins_menu()

    def _rebuild_plugins_menu(self) -> None:
        """Заново собирает меню «Плагины».

        Сверху — переключатели: по одному на плагин, галочка означает
        «работает». Включение и выключение действуют сразу, без перезапуска:
        выключают плагин обычно тогда, когда он мешает прямо сейчас.

        Ниже — команды работающих плагинов. Неработающие команд не дают, и
        показывать их серыми незачем: список бы рос, а толку ноль.
        """
        menu = getattr(self, "_plugins_menu", None)
        if menu is None:
            return
        menu.clear()

        found = self._plugins.plugins
        if not found:
            empty = menu.addAction("Плагинов не найдено")
            empty.setEnabled(False)
            return

        for plugin in found:
            item = menu.addAction(self._plugin_caption(plugin))
            item.setCheckable(True)
            item.setChecked(self._plugins.is_active(plugin.info.name))
            # Сломанный не включить: он упадёт снова, и галочка обманет.
            broken = plugin.state in (PluginState.FAILED, PluginState.INCOMPATIBLE)
            item.setEnabled(not broken)
            if plugin.reason:
                item.setToolTip(plugin.reason)
            item.toggled.connect(
                lambda active, name=plugin.info.name: self._toggle_plugin(name, active)
            )

        commands = self._plugins.actions()
        if commands:
            menu.addSeparator()
            for action in commands:
                item = menu.addAction(action.title)
                if action.tooltip:
                    item.setToolTip(action.tooltip)
                if action.shortcut:
                    item.setShortcut(action.shortcut)
                item.triggered.connect(
                    lambda _checked=False, spec=action: self._run_plugin_action(spec)
                )

        menu.addSeparator()
        manage = menu.addAction("Настройка плагинов…")
        manage.triggered.connect(lambda: self.open_preferences("Плагины"))

    @staticmethod
    def _plugin_caption(plugin) -> str:
        """Подпись в меню: название и, при беде, чем именно оно кончилось."""
        if plugin.state is PluginState.LOADED:
            return plugin.info.title
        if plugin.state is PluginState.DISABLED:
            return plugin.info.title
        return f"{plugin.info.title} — {plugin.state.title}"

    def _toggle_plugin(self, name: str, active: bool) -> None:
        """Включает или выключает плагин из меню."""
        updated = self._plugins.set_active(name, active, _PluginBridge(self))
        if updated is None:
            return

        if active and updated.state is not PluginState.LOADED:
            self._show_status(f"Плагин «{name}» не запустился: {updated.reason}")
        else:
            self._show_status(
                f"Плагин «{updated.info.title}» "
                + ("включён" if active else "выключен")
            )

        # Проверки пересчитываются: правило плагина только что появилось или
        # исчезло, и панель обязана показать это сразу, а не после правки.
        self._qc.run_all(self._doc)
        self.model.refresh_qc()
        if self.qc_dock is not None and self.qc_dock.isVisible():
            self.qc_panel.refresh()

    def _run_plugin_action(self, spec) -> None:
        """Выполняет команду плагина, не давая его ошибке уронить окно."""
        try:
            spec.callback()
        except Exception as exc:
            self._show_status(f"Плагин: «{spec.title}» — {exc}")

    def open_command_palette(self) -> None:
        """Поиск по всем командам приложения."""
        from sfstudio.ui.palette_dialog import CommandPalette

        palette = CommandPalette(self._actions_registry, parent=self)
        if palette.exec() != CommandPalette.Accepted:
            return
        spec = palette.chosen()
        if spec is None:
            return
        action = self._actions_registry.action(spec.key)
        if action is not None and action.isCheckable():
            action.toggle()
        else:
            spec.handler()

    # -- документ -------------------------------------------------------------- #

    def _load_document(self, doc: SubtitleDocument) -> None:
        self._doc = doc
        self._undo = UndoStack(doc)
        self._undo.on_change = self._on_document_changed
        self.model.reset_document(doc)
        self.video_pane.set_document(doc, self._undo)
        self.timeline.set_document(doc, self._undo)
        self.inspector.set_document(doc, self._undo)
        self.quick_format.set_document(doc, self._undo)
        # Дорожки восстанавливаются из слоёв реплик: файл мог прийти от другой
        # программы, где описания дорожек нет, а слои проставлены.
        doc.sync_tracks()
        self._qc.run_all(doc)
        self._refresh_seek_marks()
        self.qc_panel.set_document(doc)
        # Панель в доке переживает открытие файла — ей надо сообщить, что
        # документ теперь другой, иначе она покажет акторов из прежнего.
        self.actors_panel.set_document(doc, self._undo)
        self.model.refresh_qc()
        self._refresh_title()
        self._select_row(0)

    # -- орфография ---------------------------------------------------------------- #

    def _setup_spellcheck(self) -> None:
        """Заводит проверку и вешает её на поле правки.

        Словарь при этом не читается: загрузка ленивая, и человек, который
        проверкой не пользуется, не должен платить за неё полсекунды при
        каждом запуске.
        """
        from sfstudio.services.spelling import SpellChecker
        from sfstudio.ui.spellcheck import attach_spellcheck

        self.spelling = SpellChecker(
            str(self._settings.get("spelling.language", "") or ""),
            set(self._settings.get("spelling.words", []) or ()),
        )
        # Цвет берётся из темы позже, в apply_theme: сборка интерфейса идёт
        # раньше, и палитры окна на этот момент ещё нет.
        from sfstudio.ui.theme import DARK

        self._speller = attach_spellcheck(self.editor, self.spelling, DARK.danger)

    def apply_spelling_settings(self) -> None:
        """Перечитывает язык и свой словарь после настроек."""
        if not hasattr(self, "spelling"):
            return
        self.spelling.set_language(
            str(self._settings.get("spelling.language", "") or "")
        )
        self.spelling.set_extra_words(self._settings.get("spelling.words", []) or ())
        self._speller.rehighlight()

    def remember_spelling_words(self) -> None:
        """Складывает пополнившийся свой словарь в настройки.

        Слово добавляют из меню поля правки — а живёт оно между проектами:
        имена персонажей повторяются из серии в серию.
        """
        if not hasattr(self, "spelling"):
            return
        words = sorted(self.spelling.extra_words)
        if words != list(self._settings.get("spelling.words", []) or ()):
            self._settings.set("spelling.words", words)
            self._settings.save()

    # -- проверки ------------------------------------------------------------------ #

    def _profile_by_name(self, name: str):
        """Профиль по имени: встроенный или из файла в каталоге профилей."""
        from sfstudio.app.storage import profiles_dir
        from sfstudio.services.qc_profiles import available

        try:
            packs = available(profiles_dir(self._settings))
        except Exception:
            packs = {}
        pack = packs.get(name)
        if pack is not None and not pack.error:
            return pack.profile
        return PROFILES.get(name, default_profile())

    def save_qc_report(self) -> bool:
        """Сохраняет отчёт о проверке — то, что прикладывают к сдаче.

        Перед выгрузкой проверки прогоняются заново: отчёт должен описывать
        то, что в документе сейчас, а не то, что было до последних правок.
        """
        from sfstudio.services.qc_report import report_csv, report_html

        self._qc.run_all(self._doc)
        self.model.refresh_qc()
        self.qc_panel.refresh()
        issues = self._qc.all_issues()

        suggested = "проверка.html"
        if self._doc.source_path is not None:
            suggested = f"{self._doc.source_path.stem} — проверка.html"
        elif self._project is not None and self._project.path is not None:
            suggested = f"{self._project.path.stem} — проверка.html"

        path, _ = QFileDialog.getSaveFileName(
            self, "Отчёт о проверке", suggested,
            "Страница HTML (*.html);;Таблица CSV (*.csv)",
        )
        if not path:
            return False

        target = Path(path)
        if target.suffix.lower() == ".csv":
            text = report_csv(self._doc, issues)
        else:
            if not target.suffix:
                target = target.with_suffix(".html")
            text = report_html(self._doc, issues, profile=self._qc.profile)

        try:
            # BOM: Excel иначе открывает CSV с кириллицей крякозябрами.
            target.write_text(text, encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.warning(self, "Отчёт", f"Не удалось записать:\n{exc}")
            return False

        from sfstudio.ui.safe_text import plural

        found = plural(len(issues), "замечание", "замечания", "замечаний")
        self._show_status(f"Отчёт сохранён: {target.name} · {found}")
        return True

    # -- глоссарий ----------------------------------------------------------------- #

    @property
    def glossary(self):
        return self._glossary

    def open_glossary(self) -> None:
        """Окно правки глоссария."""
        from sfstudio.ui.glossary_dialog import GlossaryDialog

        dialog = GlossaryDialog(self._glossary, self)
        dialog.changed.connect(self.set_glossary)
        dialog.exec()

    def set_glossary(self, glossary) -> None:
        """Применяет глоссарий и перепроверяет реплики.

        Полный прогон проверок, а не точечный: правило про термины касается
        каждой реплики сразу, и оставить прежние находки значило бы
        показывать замечания по списку, которого больше нет.
        """
        self._glossary = glossary or None
        self._qc.glossary = self._glossary
        self._qc.reference = self._reference
        if self._project is not None:
            self._project.glossary = self._glossary

        self._qc.run_all(self._doc)
        self.model.refresh_qc()
        self.qc_panel.refresh()

        if self._glossary is None:
            self._show_status("Глоссарий пуст")
            return

        from sfstudio.ui.safe_text import plural

        count = plural(len(self._glossary), "термин", "термина", "терминов")
        note = "" if self._reference else " · оригинал не подключён"
        self._show_status(f"Глоссарий: {count}{note}")

    # -- оригинал для перевода ---------------------------------------------------- #

    @property
    def reference(self):
        return self._reference

    def open_reference(self) -> bool:
        """Подключает файл оригинала, с которого идёт перевод."""
        from sfstudio.io import registry

        start = str(self._doc.source_path or self._settings.get("project.folder", "") or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть оригинал", start,
            "Субтитры (*.ass *.ssa *.srt *.vtt *.ttml *.dfxp);;Все файлы (*)",
        )
        if not path:
            return False

        target = Path(path)
        try:
            document = registry.load(target)
        except Exception as exc:
            QMessageBox.critical(
                self, "Не удалось открыть оригинал",
                f"{target}\n\n{exc}",
            )
            return False

        if not document.events:
            QMessageBox.warning(
                self, "Оригинал", f"В файле нет ни одной реплики:\n{target}"
            )
            return False

        track = ReferenceTrack.from_events(document.events, source=target.name)
        self.set_reference(track)
        self._warn_if_mismatched(track)
        return True

    def _warn_if_mismatched(self, track) -> None:
        """Предупреждает, если подключён явно не тот файл.

        Совпадений почти нет — значит открыли оригинал от другой серии или
        со сдвинутыми таймингами. Сказать об этом надо сразу: иначе человек
        переведёт полсерии, сверяясь с пустой колонкой.
        """
        if not self._doc.events:
            return
        share = track.coverage(self._doc.events)
        if share >= 0.25:
            return
        QMessageBox.warning(
            self, "Оригинал почти не совпадает",
            f"Совпадений по времени: {share * 100:.0f} %.\n\n"
            "Похоже, это оригинал от другой серии или с другими таймингами. "
            "Файл подключён — проверьте, тот ли он.",
        )

    def close_reference(self) -> None:
        self.set_reference(None)

    def set_reference(self, track, *, announce: bool = True) -> None:
        """Подключает или снимает оригинал и раздаёт его тем, кто показывает."""
        self._reference = track or None
        # Правило про термины сравнивает перевод с оригиналом — значит
        # оригинал нужен и проверкам, а не только таблице.
        self._qc.reference = self._reference
        self.model.set_reference(self._reference)
        self.table.show_reference(self._reference is not None)
        self.original.setVisible(self._reference is not None)
        self.original.clear()

        current = self._current_eid()
        if current is not None and self._doc.has(current):
            self._load_original(self._doc.by_eid(current))

        if hasattr(self, "reference_close_action"):
            self.reference_close_action.setEnabled(self._reference is not None)

        if not announce:
            return
        if self._reference is None:
            self._show_status("Оригинал отключён")
        else:
            from sfstudio.ui.safe_text import plural

            source = self._reference.source or "оригинал"
            count = plural(len(self._reference), "строка", "строки", "строк")
            self._show_status(f"Оригинал: {source} · {count}")

    # -- проект ------------------------------------------------------------------ #

    @property
    def project(self) -> Project | None:
        return self._project

    def open_project_file(self, path: Path) -> bool:
        """Открывает проект и восстанавливает состояние работы."""
        path = self._maybe_restore_autosave(path)
        try:
            project = load_project(path)
        except ProjectError as exc:
            QMessageBox.critical(self, "Не удалось открыть проект", str(exc))
            return False

        self._project = project
        self._load_document(project.document)
        # Субтитры живут внутри проекта: отдельного файла у них нет, и
        # «Сохранить» не должно молча писать куда-то рядом.
        self._doc.source_path = None
        self._settings.push_recent("projects", path)
        self._settings.save()

        self.set_reference(project.reference, announce=False)
        self._glossary = project.glossary
        self._qc.glossary = self._glossary

        if project.media_path is not None:
            if project.media_path.is_file():
                self.load_media(project.media_path)
            else:
                self._show_status(f"Видео не найдено: {project.media_path}")

        self._restore_state(project.state)
        self._undo.mark_clean()
        self._refresh_title()
        self._show_status(
            f"Проект «{project.display_name}» · {len(self._doc)} событий"
        )
        return True

    def _restore_state(self, state: ProjectState) -> None:
        """Возвращает пользователя туда, где он остановился."""
        if state.px_per_ms > 0:
            self.timeline._px_per_ms = state.px_per_ms
            self.timeline._view_start_ms = state.view_start_ms
        if state.track_zoom > 0 and self.timeline.track_zoom > 0:
            self.timeline.zoom_tracks(state.track_zoom / self.timeline.track_zoom)

        # Время задаётся после масштаба: прокрутка к нему считается по
        # текущему масштабу, и в обратном порядке вид уехал бы не туда.
        self.timeline.set_time(state.time_ms, follow=True)
        self.transport.set_position(state.time_ms)
        self.video_pane.seek_ms(state.time_ms)

        if state.selected_eid is not None and self._doc.has(state.selected_eid):
            self._select_eid(state.selected_eid)

    def _capture_state(self) -> ProjectState:
        return ProjectState(
            time_ms=self.timeline.time_ms,
            selected_eid=self._current_eid(),
            px_per_ms=self.timeline._px_per_ms,
            view_start_ms=self.timeline._view_start_ms,
            track_zoom=self.timeline.track_zoom,
        )

    def save_project_file(self) -> bool:
        """Сохраняет открытый проект. Без проекта спрашивает, куда его класть."""
        if self._project is None or self._project.path is None:
            return self.save_project_as()
        return self._write_project(self._project.path)

    # -- автосохранение ------------------------------------------------------ #

    def _reschedule_autosave(self) -> None:
        """Заводит или останавливает таймер автосохранения по настройке.

        Настройка была, а таймера не было: человек ставил «каждые 5 минут»,
        полагался на это и терял работу при сбое. Настройка, которая ничего
        не делает, хуже отсутствующей — на неё рассчитывают.
        """
        minutes = int(self._settings.get("project.autosave_minutes", 5) or 0)
        timer = getattr(self, "_autosave_timer", None)
        if timer is None:
            timer = self._autosave_timer = QTimer(self)
            timer.timeout.connect(self._autosave)

        if minutes <= 0:
            timer.stop()
            return
        timer.start(minutes * 60_000)

    def autosave_path(self) -> Path | None:
        """Куда пишется копия. ``None`` — проект ещё не сохранён ни разу.

        Рядом с самим проектом, с приставкой в имени: искать копию человек
        пойдёт туда же, где лежит проект, а не в скрытый каталог профиля.
        """
        if self._project is None or self._project.path is None:
            return None
        path = self._project.path
        return path.with_name(f"{path.stem}.автосохранение{path.suffix}")

    def _autosave(self) -> None:
        """Пишет копию проекта, не трогая сам файл и не помечая его чистым.

        Копия, а не сам файл: автосохранение не должно подменять решение
        человека сохраниться. Оно и историю отмены не трогает — документ
        по-прежнему считается изменённым, а «Сохранить» делает то же, что
        и делало.
        """
        target = self.autosave_path()
        if target is None or self._undo.is_clean:
            return

        try:
            self._project.reference = self._reference
            self._project.glossary = self._glossary
            snapshot = self._project.snapshot(
                self._doc, self._media_path, self._capture_state()
            )
            save_project(snapshot, target)
        except (ProjectError, OSError) as exc:
            # Диск мог отвалиться, папка — стать недоступной. Автосохранение
            # не повод прерывать работу: сообщаем в строке состояния и живём
            # дальше, а следующий тик попробует снова.
            self._show_status(f"Автосохранение не удалось: {exc}")
            return
        self._show_status(f"Автосохранение: {target.name}")

    def save_project_as(self) -> bool:
        if self._project is not None and self._project.path is not None:
            suggested = str(self._project.path)
        else:
            suggested = str(Path(self._default_project_folder()) / "Новый проект.sfproj")
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить проект", suggested, PROJECT_FILTER
        )
        return self._write_project(Path(path)) if path else False

    def _write_project(self, path: Path) -> bool:
        if self._project is None:
            # Проекта не было — заводим его из того, что открыто сейчас.
            self._project = Project.new(path.stem, self._doc.script_info.play_res)
        self._project.document = self._doc
        self._project.media_path = self._media_path
        self._project.state = self._capture_state()
        self._project.reference = self._reference
        self._project.glossary = self._glossary

        try:
            written = save_project(self._project, path)
        except ProjectError as exc:
            QMessageBox.critical(self, "Не удалось сохранить проект", str(exc))
            return False

        self._settings.push_recent("projects", written)
        self._settings.save()
        self._undo.mark_clean()
        # Копия пережила своё назначение: оставить её значит при следующем
        # открытии предложить восстановить работу, которая уже сохранена.
        stale = self.autosave_path()
        if stale is not None:
            with contextlib.suppress(OSError):
                stale.unlink(missing_ok=True)
        self._refresh_title()
        self._show_status(f"Проект сохранён: {written.name}")
        return True

    def _maybe_restore_autosave(self, path: Path) -> Path:
        """Спрашивает, открыть ли копию, если она новее самого проекта.

        Копия новее означает, что после последнего сохранения работа шла и
        оборвалась. Молча открывать её нельзя — человек мог отказаться от
        тех правок намеренно; молча игнорировать тоже нельзя, иначе смысл
        автосохранения теряется. Поэтому вопрос, и по умолчанию — проект.
        """
        backup = path.with_name(f"{path.stem}.автосохранение{path.suffix}")
        try:
            if not backup.is_file() or backup.stat().st_mtime <= path.stat().st_mtime:
                return path
        except OSError:
            return path

        from datetime import datetime

        when = datetime.fromtimestamp(backup.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        answer = QMessageBox.question(
            self,
            "Найдено автосохранение",
            f"Рядом с проектом лежит копия, сделанная позже последнего "
            f"сохранения ({when}).\n\n"
            f"Похоже, прошлый сеанс завершился неожиданно.\n\n"
            f"Открыть копию вместо проекта?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return backup if answer == QMessageBox.Yes else path

    def _default_project_folder(self) -> str:
        folder = str(self._settings.get("project.folder", "") or "")
        return folder if folder and Path(folder).is_dir() else str(Path.home())

    def new_project(self) -> bool:
        """Создаёт проект через диалог параметров."""
        if not self._confirm_discard():
            return False
        from sfstudio.ui.new_project_dialog import NewProjectDialog

        dialog = NewProjectDialog(
            Path(self._default_project_folder()),
            parent=self,
            default_font=str(self._settings.get("subtitles.default_font", "Arial") or "Arial"),
            default_size=float(self._settings.get("subtitles.default_size", 54.0) or 54.0),
        )
        if dialog.exec() != NewProjectDialog.Accepted:
            return False

        path = dialog.project_path()
        if path is None:
            QMessageBox.warning(self, "Новый проект", "Укажите название и папку.")
            return False

        project = dialog.build()
        self._project = project
        self._load_document(project.document)
        self._media_path = None

        media = dialog.media_path()
        if media is not None and media.is_file():
            self.load_media(media)
        elif media is not None:
            self._show_status(f"Видео не найдено: {media}")

        return self._write_project(path)

    def adopt_project(self, project: Project, path: Path) -> bool:
        """Принимает готовый проект и записывает его.

        Нужен потому, что диалог параметров показывается **до** главного окна:
        отказ в нём должен возвращать к выбору проекта, а не оставлять открытым
        окно с документом, которого никто не просил.
        """
        self._project = project
        self._load_document(project.document)
        self._media_path = None

        media = project.media_path
        if media is not None and media.is_file():
            self.load_media(media)
        elif media is not None:
            self._show_status(f"Видео не найдено: {media}")

        return self._write_project(path)

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть проект", self._default_project_folder(), PROJECT_FILTER
        )
        if path:
            self.open_project_file(Path(path))

    def _confirm_discard(self) -> bool:
        """Спрашивает о несохранённых правках. True — можно продолжать.

        Отдельным методом, потому что спросить надо в трёх местах: при создании
        проекта, при открытии другого и при закрытии окна. Забыть спросить хотя
        бы в одном — потерять работу пользователя.
        """
        if self._undo.is_clean:
            return True
        answer = QMessageBox.question(
            self,
            "Несохранённые изменения",
            "В проекте есть несохранённые изменения. Сохранить их?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            return self.save_project_file()
        return True

    def open_preferences(self, tab: str = "") -> None:
        """Настройки. ``tab`` открывает нужную вкладку сразу.

        Приходить из меню плагинов в общие настройки и искать там нужную
        вкладку глазами — лишний шаг там, где ясно, куда человек шёл.
        """
        from sfstudio.ui.preferences_dialog import PreferencesDialog

        dialog = PreferencesDialog(self._settings, parent=self)
        dialog.applied.connect(self._apply_settings)
        dialog.layout_preset_requested.connect(self.apply_layout_preset)
        dialog.layout_reset_requested.connect(self.reset_layout)
        if tab:
            dialog.show_tab(tab)
        dialog.exec()

    def paste_event(self) -> None:
        """Создаёт реплику из буфера обмена.

        Куда именно — решает настройка: под курсором таймлайна или под
        указателем мыши. Оба способа осмысленны, и выбор за человеком: одни
        работают клавишами и смотрят на курсор, другие ведут мышью по волне
        и вставляют туда, куда смотрят.

        Многострочный текст сохраняет переносы: в ASS они записываются
        отдельной разметкой. Слепить его в одну строку значило бы потерять
        разбивку, которую человек уже сделал в другой программе.
        """
        clipboard = QApplication.clipboard()
        text = clipboard.text() if clipboard is not None else ""
        text = text.strip()
        if not text:
            self._show_status("Буфер обмена пуст")
            return

        where = str(self._settings.get("editing.paste_at", "playhead"))
        position = None
        if where == "mouse":
            position = self.timeline.mouse_position
            if position is None:
                # Мышь не над дорожками — не место придумывать за человека.
                self._show_status(
                    "Наведите указатель на дорожку субтитров или переключите "
                    "вставку на курсор в настройках"
                )
                return

        if position is None:
            ms, layer = self.timeline.time_ms, None
        else:
            ms, layer = position

        # Переносы строк — разметкой ASS, лишние пробелы по краям строк убираем:
        # из браузера и документов текст приходит с отступами.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        prepared = "\\N".join(lines)

        created = self.timeline.create_event_with_text(ms, prepared, layer)
        if created is None:
            return
        self._select_eid(created)
        self._show_status(f"Вставлена реплика: {len(prepared)} знаков")

    def open_find(self) -> None:
        """Открывает поиск. Второй вызов поднимает уже открытое окно.

        Окно немодальное и живёт, пока его не закрыли: искать одно и то же
        слово по десять раз, каждый раз набирая его заново, — работа впустую.
        """
        from sfstudio.ui.find_dialog import FindDialog

        existing = getattr(self, "_find_dialog", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            existing.find_edit.setFocus()
            existing.find_edit.selectAll()
            return

        dialog = self._find_dialog = FindDialog(
            self._doc,
            self._undo,
            parent=self,
            selection=self._selected_eids(),
            cursor_eid=self._current_eid(),
        )
        dialog.event_activated.connect(self._select_eid)
        dialog.show()
        dialog.find_edit.setFocus()

    def find_in_selection(self) -> None:
        """То же окно, но сразу с областью «в выделенных»."""
        from sfstudio.services.search import SearchScope

        self.open_find()
        dialog = getattr(self, "_find_dialog", None)
        if dialog is not None:
            dialog.set_scope(SearchScope.SELECTION)

    def open_shortcuts(self) -> None:
        """Окно настройки сочетаний."""
        from sfstudio.ui.shortcuts_dialog import ShortcutsDialog

        saved = self._settings.get("ui.shortcuts", {})
        dialog = ShortcutsDialog(
            self._actions_registry,
            {str(k): str(v) for k, v in saved.items()} if isinstance(saved, dict) else {},
            parent=self,
        )
        dialog.changed.connect(self._save_shortcuts)
        dialog.exec()

    def _save_shortcuts(self, _overrides: dict) -> None:
        """Пишет отличия от умолчаний. Полный список стал бы обузой при
        первом же переименовании команды."""
        defaults = self._actions_registry.default_shortcuts()
        changed = {
            spec.key: spec.shortcut
            for spec in self._actions_registry
            if spec.shortcut != defaults.get(spec.key, "")
        }
        self._settings.set("ui.shortcuts", changed)
        self._settings.save()

    def _assign_actor(self, name: str) -> None:
        """Назначает говорящего всем выделенным репликам одной командой."""
        chosen = self._selected_eids()
        if not chosen:
            self._show_status("Сначала выберите реплики в таблице или на таймлайне")
            return
        self._undo.run(self.actor_command(chosen, name))
        self._show_status(f"«{name}» назначен {len(chosen)} репликам")

    def label_template(self) -> str:
        """Шаблон метки говорящего по настройкам. Пустая — метки выключены."""
        from sfstudio.core.actor_label import TEXT_FIELD, template_for

        template = template_for(
            str(self._settings.get("subtitles.actor_label", "off")),
            str(self._settings.get("subtitles.actor_label_custom", "")),
        )
        return "" if template == TEXT_FIELD else template

    def actor_command(self, eids: list[int], name: str):
        """Команда «назначить говорящего», при нужде вместе с меткой в тексте.

        Назначение и правка текста идут **одной** командой: это одно действие
        с точки зрения человека, и разваливать его на два шага истории значило
        бы заставлять нажимать Ctrl+Z дважды, чтобы вернуться.
        """
        from sfstudio.core.actor_label import relabel_events
        from sfstudio.core.commands import AssignActor, CompositeCommand, SetText

        assign = AssignActor(eids, name)
        template = self.label_template()
        if not template:
            return assign

        chosen = set(eids)
        changes = relabel_events(
            [e for e in self._doc.events if e.eid in chosen],
            template,
            self._doc.actors.names(),
            actor_of=lambda _event: name,
        )
        if not changes:
            return assign
        return CompositeCommand(
            [assign, *(SetText(eid, text) for eid, text in changes.items())],
            label=assign.label,
        )

    def relabel_all(self) -> None:
        """Перестраивает метки говорящих во всём документе.

        Нужна после смены формата в настройках и для файлов, где акторы
        расставлены, а меток нет. Отдельная команда, потому что менять
        готовый текст молча, по факту открытия настроек, нельзя.
        """
        from sfstudio.core.actor_label import relabel_events
        from sfstudio.core.commands import CompositeCommand, SetText

        template = self.label_template()
        changes = relabel_events(
            list(self._doc.events),
            template or "{text}",
            self._doc.actors.names(),
        )
        if not changes:
            self._show_status("Метки говорящих уже в порядке")
            return

        self._undo.run(
            CompositeCommand(
                [SetText(eid, text) for eid, text in changes.items()],
                label=f"Метки говорящих: {len(changes)} реплик",
            )
        )
        action = "убраны из" if not template else "проставлены в"
        self._show_status(f"Метки говорящих {action} {len(changes)} репликах")

    def _actor_names(self) -> list[str]:
        """Имена акторов для выпадающего списка, с пустым в начале.

        Пустая строка — не заглушка: снять назначение говорящего надо уметь,
        и делать это отдельной командой меню было бы лишним шагом.
        """
        return ["", *(actor.name for actor in self._doc.actors)]

    def _apply_settings(self) -> None:
        """Применяет настройки, которые видны сразу."""
        density = str(self._settings.get("ui.table_density", "comfortable"))
        heights = {"compact": 20, "comfortable": 24, "spacious": 30}
        self.table.verticalHeader().setDefaultSectionSize(heights.get(density, 24))

        self.apply_theme()
        self._reschedule_autosave()
        self.apply_spelling_settings()

        profile = self._profile_by_name(
            str(self._settings.get("qc.profile", "general"))
        )
        if profile is not self._qc.profile:
            self._qc.profile = profile
            self._qc.run_all(self._doc)
            self.model.refresh_qc()

        player = self.video_pane.player
        if player is not None and self.video_pane.has_video:
            player.volume = float(self._settings.get("media.volume", 80.0) or 0.0)

    def open_subtitles(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Открыть субтитры", "", SUBTITLE_FILTER)
        if not path:
            return
        try:
            doc = registry.load(Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось открыть", f"{type(exc).__name__}: {exc}")
            return
        self._load_document(doc)
        note = f"Открыт {Path(path).name} · {len(doc)} событий · {doc.source_encoding}"
        if doc.script_info.play_res_inferred:
            note += " · PlayRes не задан, подставлен 1920×1080"
        self._show_status(note)

    def open_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Открыть видео или аудио", "", MEDIA_FILTER)
        if path:
            self.load_media(Path(path))

    def load_media(self, path: Path) -> None:
        """Читает метаданные синхронно, а пики и ключевые кадры — в фоне."""
        from sfstudio.media.probe import MediaProbeError, probe

        try:
            info = probe(path)
        except MediaProbeError as exc:
            QMessageBox.critical(self, "Не удалось открыть медиа", str(exc))
            return

        self._media_path = path
        self._media_token += 1
        token = self._media_token
        self.timeline.set_media_name(path.name)
        self.transport.set_enabled_transport(True)
        # Без этого имя видео в заголовок не попадает: _refresh_title его
        # подставляет, но звать её после смены _media_path было некому, и
        # открытое видео никак не отражалось в окне.
        self._refresh_title()

        if info.video is not None:
            self.timeline.set_fps(info.video.fps)
            # Зазор между репликами задан в кадрах, поэтому проверки зависят
            # от частоты кадров источника.
            self._qc.fps = info.video.fps
            self._qc.run_all(self._doc)
            self.model.refresh_qc()
            # PlayRes подставляется из видео, только если в файле субтитров
            # его не было: перезаписывать заданный автором PlayRes нельзя —
            # это сдвинуло бы все существующие \pos.
            if self._doc.script_info.play_res_inferred:
                w, h = info.play_res
                self._doc.script_info.play_res_x = w
                self._doc.script_info.play_res_y = h
                self.preview.update()

        self.timeline.set_peaks(None, "чтение звука…")
        self.timeline.set_keyframes(None)

        # Видео подключаем лениво: libmpv может отсутствовать, и это не повод
        # отказывать в открытии файла — волна и тайминги работают и без неё.
        note = self.video_pane.attach_player()
        if info.has_video and not self.video_pane.load_media(path):
            self._show_status(f"Видео не воспроизводится: {note}")
        else:
            # Открытие файла — не команда «играй». Пользователь открывает
            # видео, чтобы расставлять по нему субтитры, и уехавшая с места
            # картинка только мешает. Автозапуск включается настройкой.
            self._apply_playback_defaults()

        if info.has_audio:
            self._start_peaks(path, token)
        else:
            self.timeline.set_peaks(None, "в файле нет аудиодорожки")

        if info.has_video:
            self._start_keyframes(path, token)

        self.timeline.fit_all()
        self._offer_embedded_tracks(path, info)
        size = f"{info.video.display_size[0]}×{info.video.display_size[1]}" if info.video else "—"
        self._show_status(
            f"Медиа: {path.name} · {info.duration_ms / 1000:.1f} с · {size} · "
            f"{'звук есть' if info.has_audio else 'без звука'}"
        )

    def _apply_playback_defaults(self) -> None:
        """Ставит громкость и паузу по настройкам после открытия файла."""
        player = self.video_pane.player
        if player is None or not self.video_pane.has_video:
            return
        player.volume = float(self._settings.get("media.volume", 80.0) or 0.0)
        if not bool(self._settings.get("media.autoplay", False)):
            player.paused = True
        self.transport.set_volume(player.volume)

    def _start_peaks(self, path: Path, token: int) -> None:
        from sfstudio.ui.tasks import PeaksTask

        task = PeaksTask(path, token)
        task.signals.progress.connect(self._on_task_progress)
        task.signals.finished.connect(self._on_peaks_ready)
        task.signals.no_audio.connect(self._on_no_audio)
        task.signals.failed.connect(self._on_task_failed)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self._pool.start(task)

    def _start_keyframes(self, path: Path, token: int) -> None:
        from sfstudio.ui.tasks import KeyframeTask

        task = KeyframeTask(path, token)
        task.signals.finished.connect(self._on_keyframes_ready)
        task.signals.failed.connect(self._on_task_failed)
        self._pool.start(task)

    def _offer_embedded_tracks(self, path: Path, info) -> None:
        """Предлагает открыть вшитую дорожку, если она есть.

        Спрашиваем, а не открываем молча: у пользователя мог быть открыт свой
        файл субтитров, и подменять его без спроса — потерять его работу.
        """
        # Запись доступна при любом открытом медиа: в файл без субтитров
        # дорожку можно добавить, а не только заменить существующую.
        self.mux_action.setEnabled(True)

        editable = [t for t in info.subtitles if t.is_editable]
        if not info.subtitles:
            return

        from sfstudio.ui.dialogs import TrackPickerDialog

        if not editable:
            self._show_status(
                f"В файле есть дорожки субтитров ({len(info.subtitles)}), "
                "но все они — картинки: для правки нужен OCR"
            )
            return

        if not self._undo.is_clean:
            answer = QMessageBox.question(
                self,
                "Несохранённые изменения",
                "В файле есть вшитые субтитры, но текущий документ изменён.\n"
                "Открыть дорожку из контейнера, потеряв правки?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        dialog = TrackPickerDialog(info.subtitles, path.name, parent=self)
        if dialog.exec() != TrackPickerDialog.Accepted:
            return
        index = dialog.selected_index()
        if index is not None:
            self.open_embedded_track(path, index)

    def open_embedded_track(self, path: Path, stream_index: int) -> None:
        from sfstudio.io.container import ContainerError, extract_subtitles

        try:
            doc = extract_subtitles(path, stream_index)
        except ContainerError as exc:
            QMessageBox.critical(self, "Не удалось извлечь дорожку", str(exc))
            return
        self._load_document(doc)
        self._embedded_index = stream_index
        # Документ пришёл из контейнера, а не из файла: сохранять «поверх»
        # некуда, поэтому путь не выставляем — Ctrl+S спросит имя.
        doc.source_path = None
        self._show_status(
            f"Открыта вшитая дорожка #{stream_index}: {len(doc)} событий"
        )

    def save_into_container(self) -> None:
        """Записывает текущий документ обратно в медиаконтейнер."""
        from sfstudio.io.container import CONTAINER_CODEC, ContainerError, mux_subtitles
        from sfstudio.platform.ffmpeg import ffmpeg_available
        from sfstudio.ui.dialogs import MuxDialog

        if self._media_path is None:
            QMessageBox.information(
                self, "Нет медиафайла",
                "Сначала откройте видео: субтитры записываются в него."
            )
            return
        if not ffmpeg_available():
            QMessageBox.warning(
                self, "ffmpeg не найден",
                "Запись в контейнер требует ffmpeg. Установите его или положите "
                "рядом с нативными библиотеками."
            )
            return

        suggested = str(self._media_path.with_name(
            self._media_path.stem + ".subs" + self._media_path.suffix
        ))
        filters = ";;".join(
            f"{ext.upper().lstrip('.')} (*{ext})" for ext in sorted(CONTAINER_CODEC)
        )
        target, _ = QFileDialog.getSaveFileName(
            self, "Сохранить контейнер как", suggested, filters
        )
        if not target:
            return
        output = Path(target)

        from sfstudio.media.probe import probe

        try:
            tracks = probe(self._media_path).subtitles
        except Exception:
            tracks = []

        dialog = MuxDialog(
            output, tracks, parent=self, current_index=self._embedded_index
        )
        if dialog.exec() != MuxDialog.Accepted:
            return

        temporary = output.with_name(output.stem + ".subs-tmp.ass")
        try:
            registry.save(self._doc, temporary, fid="ass")
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.show()
            mux_subtitles(
                self._media_path, temporary, output, dialog.options(),
                progress=lambda f: self.progress.setValue(int(f * 100)),
            )
        except ContainerError as exc:
            QMessageBox.critical(self, "Не удалось записать контейнер", str(exc))
            return
        finally:
            self.progress.hide()
            temporary.unlink(missing_ok=True)

        self._show_status(f"Записано: {output.name}")

    # -- результаты фоновых задач ---------------------------------------------- #

    def _stale(self, token: int) -> bool:
        """Результат задачи от предыдущего файла игнорируется."""
        return token != self._media_token

    def _on_task_progress(self, token: int, fraction: float) -> None:
        if self._stale(token):
            return
        self.progress.setValue(int(fraction * 100))
        if fraction >= 1.0:
            QTimer.singleShot(400, self.progress.hide)

    def _on_peaks_ready(self, token: int, data: object) -> None:
        if self._stale(token):
            return
        self.progress.hide()
        self.timeline.set_peaks(data)
        self._show_status("Волна построена")

    def _on_keyframes_ready(self, token: int, index: object) -> None:
        if self._stale(token):
            return
        self.timeline.set_keyframes(index)  # type: ignore[arg-type]
        self._show_status(f"Ключевых кадров: {len(index)}")  # type: ignore[arg-type]

    def _on_no_audio(self, token: int) -> None:
        if self._stale(token):
            return
        self.progress.hide()
        self.timeline.set_peaks(None, "в файле нет аудиодорожки")

    def _on_task_failed(self, token: int, message: str) -> None:
        if self._stale(token):
            return
        self.progress.hide()
        self.timeline.set_peaks(None, "не удалось прочитать звук")
        self._show_status(f"Ошибка фоновой задачи: {message}")

    # -- сохранение ------------------------------------------------------------- #

    def save_file(self) -> None:
        if self._doc.source_path is None:
            self.save_file_as()
        else:
            self._save_to(self._doc.source_path)

    def save_file_as(self) -> None:
        suggested = str(self._doc.source_path or "subtitles.ass")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить как", suggested, SUBTITLE_FILTER)
        if path:
            self._save_to(Path(path))

    def _save_to(self, path: Path) -> None:
        try:
            registry.save(self._doc, path)
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось сохранить", f"{type(exc).__name__}: {exc}")
            return
        self._undo.mark_clean()
        self._refresh_title()
        self._show_status(f"Сохранено: {path.name}")

    # -- правка ------------------------------------------------------------------ #

    def undo(self) -> None:
        self._undo.undo()
        self._sync_undo_actions()

    def redo(self) -> None:
        self._undo.redo()
        self._sync_undo_actions()

    def insert_event(self) -> None:
        eid = self._current_eid()
        if eid is not None:
            prev = self._doc.by_eid(eid)
            start, end, style = prev.end, prev.end + 2000, prev.style
            at = self._doc.index_of(eid) + 1
        else:
            start, end = self.timeline.time_ms, self.timeline.time_ms + 2000
            style = next(iter(self._doc.styles), "Default")
            at = None
        self._undo.run(InsertEvent(SubtitleEvent(eid=0, start=start, end=end, style=style), at))

    def play_current_event(self) -> None:
        """Зацикливает выделенную реплику — основной приём при правке таймингов."""
        eid = self._current_eid()
        player = self.video_pane.player
        if eid is None or player is None or not self.video_pane.has_video:
            return
        event = self._doc.by_eid(eid)
        player.loop_range(event.start, event.end)
        player.seek_ms(event.start)
        player.paused = False

    def duplicate_event(self) -> None:
        if eids := self._editable_selection():
            self._undo.run(DuplicateEvents(eids))

    def delete_events(self) -> None:
        if eids := self._editable_selection():
            self._undo.run(DeleteEvents(eids))

    def _editable_selection(self) -> list[int]:
        """Выделенные реплики за вычетом тех, чья дорожка заблокирована.

        Замок держал только мышь на таймлайне: команды правки о нём не
        спрашивали, а выделение приходит из таблицы, где дорожек не видно, —
        и заблокированная реплика удалялась как обычная. Пропущенные не
        замалчиваются: молчаливое «сделал не всё» хуже отказа.
        """
        from sfstudio.ui.event_menu import editable_eids, plural_events

        chosen = self._selected_eids()
        allowed = editable_eids(self._doc, chosen)
        if len(allowed) < len(chosen):
            skipped = len(chosen) - len(allowed)
            self._show_status(
                f"Пропущено {plural_events(skipped)} на заблокированных дорожках"
            )
        return allowed

    # -- синхронизация ------------------------------------------------------------ #

    def _on_document_changed(self, changes: ChangeSet) -> None:
        self._update_qc(changes)
        self.model.apply_changes(changes)
        self.preview.update()
        self.timeline.update()
        self._sync_undo_actions()
        self._refresh_title()

        current = self._current_eid()
        if current is not None and current in changes.changed_eids:
            self._load_editor(current)

    def _update_qc(self, changes: ChangeSet) -> None:
        """Пересчитывает проверки только по затронутым событиям.

        Структурные изменения требуют полного прогона: порядок мог измениться,
        а от него зависят правила про зазор и перекрытие.
        """
        if changes.structural or changes.styles_changed:
            self._qc.run_all(self._doc)
        elif changes.touched:
            self._qc.recheck(self._doc, changes.touched)
        else:
            return
        self.model.refresh_qc()
        if self.qc_dock.isVisible():
            self.qc_panel.refresh()

    def _toggle_qc(self, visible: bool) -> None:
        self.qc_dock.setVisible(visible)
        if visible:
            self._qc.run_all(self._doc)
            self.qc_panel.refresh()
            self.model.refresh_qc()

    def _on_qc_profile(self, key: str) -> None:
        self._settings.set("qc.profile", key)
        self._qc.set_profile(PROFILES[key], self._doc)
        self.qc_panel.refresh()
        self.model.refresh_qc()
        self._show_status(f"Профиль QC: {PROFILES[key].name} · {self._qc.summary()}")

    def goto_next_question(self) -> None:
        """К следующей реплике с пометкой «вопрос», по кругу.

        По кругу, а не до конца файла: вопросы разбросаны, и упереться в
        последний, когда в начале ещё три, — не то, чего ждут от «следующего».
        """
        from sfstudio.core.workflow import questions

        marked = questions(self._doc.events)
        if not marked:
            self._show_status("Реплик с вопросом нет")
            return

        current = self._current_eid()
        order = [e.eid for e in self._doc.events]
        position = order.index(current) if current in order else -1
        after = [eid for eid in marked if order.index(eid) > position]
        target = after[0] if after else marked[0]

        self._select_eid(target)
        flash(self.table.viewport(), self._palette.warning)
        note = self._doc.by_eid(target).note
        place = marked.index(target) + 1
        self._show_status(
            f"Вопрос {place} из {len(marked)}" + (f": {note}" if note else "")
        )

    def goto_next_issue(self) -> None:
        eid = self.qc_panel.go_to_next(self._current_eid())
        if eid is None:
            self._show_status("Проблем не найдено")
            return
        self._select_eid(eid)
        # Короткая вспышка по таблице: строка выбрана, но при переходе через
        # пол-документа глаз не успевает найти, какая именно.
        flash(self.table.viewport(), self._palette.warning)
        issues = self._qc.issues_for(eid)
        if issues:
            self._show_status(str(issues[0]))

    def _on_table_selection(self) -> None:
        if self._syncing:
            return
        eid = self._current_eid()
        self._syncing = True
        try:
            self.preview.set_selected(eid)
            # Таймлайну отдаём всё выделение, а не одну строку: выделив в
            # таблице десяток реплик, человек ждёт увидеть их выделенными и
            # там — иначе непонятно, что затронет следующая команда.
            chosen = self._selected_eids()
            if len(chosen) > 1:
                self.timeline.set_selection(chosen)
            else:
                self.timeline.set_selected(eid)
            self._sync_inspector(eid)
            if eid is None:
                self._set_editor_text("")
            else:
                event = self._doc.by_eid(eid)
                middle = (event.start + event.end) // 2
                self.preview.set_time(middle)
                self.timeline.set_time(middle)
                self.transport.set_position(middle)
                self._load_editor(eid)
        finally:
            self._syncing = False

    def _on_preview_selection(self, eid: int) -> None:
        self._select_eid(eid)

    def _on_timeline_selection(self, eid: int) -> None:
        """Выделение на таймлайне переносится в таблицу целиком."""
        chosen = self.timeline.selected_eids
        if len(chosen) > 1:
            self._select_rows(chosen, leader=eid)
            return
        self._select_eid(eid)

    def _select_rows(self, eids: list[int], *, leader: int | None = None) -> None:
        """Отмечает в таблице несколько строк, не трогая таймлайн обратно."""
        from PySide6.QtCore import QItemSelection, QItemSelectionModel

        positions = {event.eid: row for row, event in enumerate(self._doc.events)}
        model = self.table.selectionModel()
        if model is None:
            return

        self._syncing = True
        try:
            selection = QItemSelection()
            last = self.model.columnCount() - 1
            for eid in eids:
                row = positions.get(eid)
                if row is None:
                    continue
                selection.select(self.model.index(row, 0), self.model.index(row, last))
            model.select(
                selection,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )
            if leader is not None and leader in positions:
                model.setCurrentIndex(
                    self.model.index(positions[leader], 0),
                    QItemSelectionModel.NoUpdate,
                )
                self._sync_inspector(leader)
                self._load_editor(leader)
        finally:
            self._syncing = False

    def _reveal_row(self, row: int) -> None:
        """Показывает строку таблицы, при включённых анимациях — с прокруткой.

        Строку, которая и так на экране, не трогаем: дёргать список при
        каждом щелчке по соседней реплике незачем.
        """
        from sfstudio.ui.motion import animations_enabled, smooth_scroll

        index = self.model.index(row, 0)
        if not animations_enabled():
            self.table.scrollTo(index)
            return

        bar = self.table.verticalScrollBar()
        top = self.table.rowViewportPosition(row)
        height = self.table.rowHeight(row) or 22
        visible = self.table.viewport().height()
        if top >= 0 and top + height <= visible:
            return  # уже видна целиком

        # Ведём строку к середине: у края она через мгновение снова уедет
        # за границу, стоит нажать «следующая».
        target = bar.value() + top - max(0, (visible - height) // 2)
        smooth_scroll(self.table, target)

    def _select_eid(self, eid: int) -> None:
        if self._syncing:
            return
        if eid < 0:
            self._syncing = True
            try:
                self.table.clearSelection()
                self.preview.set_selected(None)
                self.timeline.set_selected(None)
                self._sync_inspector(None)
            finally:
                self._syncing = False
            return
        row = self.model.row_of_eid(eid)
        if row < 0:
            return
        self._syncing = True
        try:
            # Автопрокрутку Qt отключаем на время выбора: она прыгает к
            # строке мгновенно, а мы хотим доехать — иначе непонятно, куда
            # уехал список.
            self.table.setAutoScroll(False)
            self.table.selectRow(row)
            self.table.setAutoScroll(True)
            self._reveal_row(row)
            self.preview.set_selected(eid)
            self.timeline.set_selected(eid)
            self._sync_inspector(eid)
            self._load_editor(eid)
        finally:
            self._syncing = False

    def _on_timeline_time(self, ms: int) -> None:
        """Перемотка с таймлайна: двигаем плеер, оверлей и полосу под кадром.

        Полоса обновляется здесь напрямую, а не ждёт ответа плеера: плеера
        может не быть вовсе (нет видео, только звук или один файл субтитров),
        и тогда позиция на ней осталась бы стоять, пока курсор на таймлайне
        уехал. Это и есть рассинхрон, который видно сразу.
        """
        self.video_pane.seek_ms(ms)
        self.transport.set_position(ms)

    def _on_seek_requested(self, ms: int) -> None:
        """Перемотка с полосы под кадром: ведём за собой плеер и таймлайн."""
        self.video_pane.seek_ms(ms)
        self.timeline.set_time(ms)
        self.transport.set_position(ms)

    def _on_player_position(self, ms: int) -> None:
        """Позиция от плеера. Курсор таймлайна и оверлей идут за ней.

        Флаг ``_syncing`` не трогаем: сигнал приходит из воспроизведения,
        а не из действия пользователя, и зацикливаться тут нечему —
        ``TimelineWidget.set_time`` сигнала о времени не испускает.
        """
        self.preview.set_time(ms)
        self.timeline.set_time(ms, follow=True)

    def _on_widget_edit(self) -> None:
        self._sync_undo_actions()
        self._refresh_title()
        self._refresh_seek_marks()
        # Инспектор не хранит своё состояние: после правки из любого места
        # он обязан перечитать документ, иначе покажет устаревшие значения.
        self.inspector.refresh()

    def _refresh_seek_marks(self) -> None:
        """Отметки реплик и длительность на полосе.

        Длительность берётся из документа, только когда её не дал плеер: без
        видео полоса иначе была бы нулевой длины и бесполезной, а с видео
        документ короче ролика и обрезал бы его конец.
        """
        self.transport.set_marks((e.start, e.end) for e in self._doc.events)
        if not self.video_pane.has_video:
            self.transport.set_duration(self._doc.duration)

    def _sync_inspector(self, eid: int | None) -> None:
        self.inspector.set_event(eid)
        if eid is not None:
            self.inspector.set_qc_notes([str(i) for i in self._qc.issues_for(eid)])

    # -- транспорт ------------------------------------------------------------- #

    def _seek_edge(self, to_end: bool) -> None:
        """В начало или в конец материала."""
        target = self._doc.duration if to_end else 0
        self.video_pane.seek_ms(target)
        self.timeline.set_time(target)

    def _on_speed_selected(self, value: float) -> None:
        self.video_pane.set_speed(value)
        self._show_status(f"Скорость: {value:g}×")

    def step_speed(self, faster: bool) -> None:
        self.transport.step_speed(faster)

    def _on_volume_changed(self, value: float) -> None:
        """Громкость меняется сразу и запоминается на следующий сеанс."""
        self.video_pane.set_volume(value)
        self._settings.set("media.volume", float(value))

    def step_volume(self, louder: bool) -> None:
        """Шаг громкости с клавиатуры."""
        step = 5.0 if louder else -5.0
        target = max(0.0, min(100.0, self.transport.volume() + step))
        self.transport.set_volume(target)
        self._on_volume_changed(target)
        self._show_status(f"Громкость: {target:.0f} %")

    def toggle_mute(self) -> None:
        self.transport.btn_mute.toggle()

    def _on_loop_toggled(self, on: bool) -> None:
        """Зацикливание выделенной реплики."""
        player = self.video_pane.player
        if player is None or not self.video_pane.has_video:
            return
        eid = self._current_eid()
        if on and eid is not None:
            event = self._doc.by_eid(eid)
            player.loop_range(event.start, event.end)
            player.seek_ms(event.start)
            player.paused = False
        else:
            player.clear_loop()

    # -- быстрое оформление ---------------------------------------------------- #

    def _on_frame_context(self, eid: int, global_pos) -> None:
        """Правый щелчок по реплике в кадре — панель оформления у курсора."""
        self.quick_format.set_document(self._doc, self._undo)
        self.quick_format.show_for(eid, global_pos)

    def _on_quick_tag(self, name: str, args: object) -> None:
        eid = self._current_eid()
        if eid is None:
            return
        self._undo.run(SetOverrideTag(eid, name, args))
        self._on_widget_edit()
        self.preview.set_selected(eid)

    def add_track(self) -> None:
        """Новая дорожка субтитров."""
        self._undo.run(AddTrack())
        self._on_widget_edit()
        self.timeline.update()
        self._show_status(
            f"Дорожек: {len(self._doc.tracks.subtitles)}"
        )

    # -- распознавание речи -------------------------------------------------- #

    def open_asr(self) -> None:
        """Окно распознавания речи.

        Открывается и без установленного движка: пользователь должен узнать о
        возможности и о том, что для неё нужно. Прятать пункт значило бы
        оставить его наедине с догадками.
        """
        from sfstudio.ui.asr_dialog import AsrDialog

        dialog = AsrDialog(
            self._doc,
            self._media_path,
            self._settings,
            parent=self,
            selection=self._selected_range(),
        )
        dialog.events_ready.connect(self._insert_recognised)
        dialog.exec()

    def _selected_range(self) -> tuple[int, int] | None:
        """Диапазон выделенных реплик — чтобы распознать только его."""
        eids = self._selected_eids()
        if len(eids) < 1:
            return None
        events = [self._doc.by_eid(eid) for eid in eids if self._doc.has(eid)]
        if not events:
            return None
        return min(e.start for e in events), max(e.end for e in events)

    def _insert_recognised(self, payload: object) -> None:
        """Вставляет распознанные реплики одной операцией отмены.

        Одной: четыреста реплик, добавленных по одной, потребовали бы
        четырёхсот нажатий «Отменить», чтобы передумать.
        """
        if not isinstance(payload, dict):
            return
        segments = payload.get("segments") or []
        if not segments:
            return

        style = next(iter(self._doc.styles), "Default")
        layer = self._doc.tracks.subtitles[0].layer if self._doc.tracks.subtitles else 0

        commands = []
        if payload.get("replace") and len(self._doc):
            commands.append(DeleteEvents([event.eid for event in self._doc.events]))
        commands.extend(
            InsertEvent(
                SubtitleEvent(
                    eid=0,
                    start=int(segment.start),
                    end=int(segment.end),
                    text=segment.text,
                    style=style,
                    layer=layer,
                )
            )
            for segment in segments
        )

        self._undo.run(
            CompositeCommand(commands, label=f"Распознано реплик: {len(segments)}")
        )
        self._on_widget_edit()
        self.model.reset_document(self._doc)
        self._qc.run_all(self._doc)
        self.model.refresh_qc()
        self._select_row(0)
        self._show_status(f"Вставлено реплик: {len(segments)}")

    def open_actors(self) -> None:
        """Диалог управления акторами."""
        from sfstudio.ui.actors_dialog import ActorsDialog

        dialog = ActorsDialog(self._doc, self._undo, parent=self)
        dialog.document_edited.connect(self._on_actors_changed)
        dialog.exec()
        self._on_actors_changed()

    def _on_actors_changed(self) -> None:
        """Цвета акторов красят строки — таблице нужна полная перерисовка."""
        self._on_widget_edit()
        self.model.refresh_colors()
        self.timeline.update()

    def _on_editor_changed(self) -> None:
        if self._suppress_editor_signal:
            return
        eid = self._current_eid()
        if eid is None:
            return
        text = self.editor.toPlainText().replace("\n", "\\N")
        if text != self._doc.by_eid(eid).text:
            self._undo.run(SetText(eid, text))

    def _load_editor(self, eid: int) -> None:
        event = self._doc.by_eid(eid)
        self._set_editor_text(event.text.replace("\\N", "\n"))
        self._load_original(event)

    def _load_original(self, event) -> None:
        """Показывает оригинал этой реплики над полем перевода."""
        if self._reference is None:
            return
        lines = self._reference.overlapping(event.start, event.end)
        # Каждая строка со своей: в диалоге сразу видно, что реплика накрыла
        # две фразы оригинала и переводить надо обе.
        self.original.setPlainText("\n".join(line.text for line in lines))

    def _set_editor_text(self, text: str) -> None:
        if self.editor.toPlainText() == text:
            return
        self._suppress_editor_signal = True
        cursor = self.editor.textCursor()
        position = cursor.position()
        self.editor.setPlainText(text)
        cursor.setPosition(min(position, len(text)))
        self.editor.setTextCursor(cursor)
        self._suppress_editor_signal = False

    # -- вспомогательное ---------------------------------------------------------- #

    def _select_row(self, row: int) -> None:
        if self._doc.events:
            self.table.selectRow(max(0, min(row, len(self._doc.events) - 1)))

    def _current_eid(self) -> int | None:
        indexes = self.table.selectionModel().selectedRows()
        return self.model.eid_at_row(indexes[0].row()) if indexes else None

    def _selected_eids(self) -> list[int]:
        rows = self.table.selectionModel().selectedRows()
        eids = [self.model.eid_at_row(i.row()) for i in rows]
        return [e for e in eids if e is not None]

    def _sync_undo_actions(self) -> None:
        self.undo_action.setEnabled(self._undo.can_undo)
        self.redo_action.setEnabled(self._undo.can_redo)
        self.undo_action.setText(
            f"Отменить: {self._undo.undo_label}" if self._undo.can_undo else "Отменить"
        )
        self.redo_action.setText(
            f"Вернуть: {self._undo.redo_label}" if self._undo.can_redo else "Вернуть"
        )

    def _refresh_title(self) -> None:
        """Заголовок: что открыто, изменено ли, какое видео привязано.

        Имя проекта имеет приоритет над именем файла субтитров: у проекта
        своего ``source_path`` нет (субтитры лежат внутри контейнера), и без
        этой ветки открытый проект показывался бы как «Без имени».
        """
        if self._project is not None:
            name = self._project.display_name
        elif self._doc.source_path is not None:
            name = self._doc.source_path.name
        else:
            name = "Без имени"
        media = f" — {self._media_path.name}" if self._media_path else ""
        dirty = "" if self._undo.is_clean else " •"
        self.setWindowTitle(f"{name}{dirty}{media} — SubtitleForge Studio")

    def _show_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 6000)
        self.status_label.setText(
            f"{len(self._doc)} событий{self._progress_note()}   |   "
            f"{self._doc.script_info.play_res_x}×{self._doc.script_info.play_res_y}   |   "
            f"{self._doc.source_format.upper()}"
        )

    def _progress_note(self) -> str:
        """«готово 120 из 480» — если пометки вообще расставлены.

        Пока не отмечено ни одной реплики, счётчика нет: он мешал бы тем,
        кто пометками не пользуется.
        """
        from sfstudio.core.workflow import progress

        done, total = progress(self._doc.events)
        return f"   |   готово {done} из {total}" if done else ""

    def _timing_selection(self) -> list:
        """Выделенные события, либо пусто."""
        return [self._doc.by_eid(eid) for eid in self._selected_eids() if self._doc.has(eid)]

    def open_auto_timing(self) -> None:
        """Диалог автоматической доводки таймингов."""
        from sfstudio.ui.timing_dialog import AutoTimingDialog

        if not self._doc.events:
            self._show_status("Нет событий для доводки")
            return

        dialog = AutoTimingDialog(
            self._doc,
            self._timing_selection(),
            fps=self.timeline._fps,
            keyframes=self.timeline._keyframes,
            parent=self,
        )
        if dialog.exec() != AutoTimingDialog.Accepted:
            return
        plan = dialog.plan()
        if plan.is_empty:
            return
        self._undo.run(ApplyTimings(plan.as_mapping(), label="Доводка таймингов"))
        self._show_status(plan.summary())

    def open_shift_times(self) -> None:
        """Диалог сдвига таймингов."""
        from sfstudio.ui.timing_dialog import ShiftTimesDialog

        if not self._doc.events:
            self._show_status("Нет событий для сдвига")
            return

        dialog = ShiftTimesDialog(
            self._doc, self._timing_selection(), fps=self.timeline._fps, parent=self
        )
        if dialog.exec() != ShiftTimesDialog.Accepted:
            return
        mapping = dialog.mapping()
        if not mapping:
            return
        self._undo.run(ApplyTimings(mapping, label=dialog.describe()))
        self._show_status(f"{dialog.describe()} · реплик: {len(mapping)}")

    def open_style_presets(self) -> None:
        """Библиотека шаблонов. Применяет выбранное оформление к стилю события."""
        from sfstudio.ui.style_dialog import StylePresetDialog

        eid = self._current_eid()
        style_name = self._doc.by_eid(eid).style if eid is not None else None
        current = self._doc.styles.get(style_name) if style_name else None

        dialog = StylePresetDialog(self._presets, current, parent=self)
        if dialog.exec() != StylePresetDialog.Accepted:
            return
        chosen = dialog.chosen_style()
        if chosen is None:
            return

        target = style_name or next(iter(self._doc.styles), "Default")
        if target not in self._doc.styles:
            from dataclasses import replace as _replace

            from sfstudio.core.commands import CreateStyle

            self._undo.run(CreateStyle(_replace(chosen, name=target)))
        else:
            self._undo.run(UpdateStyle(target, chosen))
        self._show_status(f"Оформление применено к стилю «{target}»")

    def _save_session(self) -> None:
        """Складывает раскладку и предпочтения в настройки перед выходом."""
        self._layout.save()
        self._settings.set("qc.panel_visible", bool(self.qc_dock and self.qc_dock.isVisible()))
        for key, profile in PROFILES.items():
            if profile is self._qc.profile:
                self._settings.set("qc.profile", key)
                break
        # Не смогли сохранить настройки — не повод мешать выходу: потеря
        # раскладки неприятна, зависшее при закрытии окно хуже.
        with contextlib.suppress(Exception):
            self._settings.save()

    def about(self) -> None:
        """Что это за программа и что она умеет — коротко и по делу.

        Прежний текст описывал прототип и обещал воспроизведение видео как
        «следующий этап». Оно давно работает, а окно всё ещё сообщало
        обратное — такое расхождение подрывает доверие ко всему остальному,
        что программа говорит о себе.
        """
        from sfstudio.render import renderer as render_mod

        engine = "libass недоступна"
        try:
            if render_mod.available():
                # Версию спрашиваем у уже работающего рендерера, а не заводим
                # новый: каждый заводит свой контекст libass, и «О программе»
                # оставляла бы их после себя при каждом открытии.
                version = self.video_pane.overlay.backend_version
                engine = f"libass {version}" if version else "libass"
        except Exception:
            pass

        QMessageBox.about(
            self,
            "О программе",
            f"<h3>SubtitleForge Studio {__version__}</h3>"
            "<p>Редактор субтитров: ASS и SubRip, покадровая правка таймингов, "
            "оформление, контроль качества и распознавание речи.</p>"
            "<p><b>Что умеет</b><br>"
            "• видео через libmpv, субтитры поверх кадра — их можно двигать и "
            "менять размер мышью;<br>"
            "• таймлайн с дорожками, волной звука и привязкой к ключевым кадрам;<br>"
            "• проекты со своим форматом: помнят видео, положение и обстановку;<br>"
            "• распознавание речи Whisper — на процессоре или видеокарте;<br>"
            "• акторы с цветами, проверки качества, поиск и замена;<br>"
            "• плагины: свои проверки, форматы и команды;<br>"
            "• темы оформления, свой акцентный цвет, отключаемые анимации.</p>"
            f"<p><b>Отрисовка:</b> {engine}<br>"
            "<b>Формат субтитров:</b> ASS 4+ (Advanced SubStation Alpha)</p>"
            "<p style='color:gray'>Версии компонентов и пути к библиотекам "
            "показывает запуск с ключом <tt>--version</tt>.</p>",
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_session()
        # Слова, занесённые в словарь из меню поля правки, живут в настройках:
        # имена персонажей нужны и в следующей серии.
        self.remember_spelling_words()
        self.video_pane.close_player()
        if self._undo.is_clean:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "Несохранённые изменения",
            "Есть несохранённые изменения. Сохранить перед выходом?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if answer == QMessageBox.Save:
            # Проект, а не файл субтитров: он хранит и текст, и обстановку.
            # Отказ от записи (закрыли диалог выбора файла) не должен
            # приводить к выходу с потерей работы.
            if self.save_project_file():
                event.accept()
            else:
                event.ignore()
        elif answer == QMessageBox.Discard:
            event.accept()
        else:
            event.ignore()


class _PluginBridge:
    """То, чем плагин видит программу.

    Отдельный маленький класс, а не сам ``MainWindow``: плагину незачем
    видеть три десятка методов окна, и сужение здесь не формальность. Через
    этот объект проходит всё, что плагин делает с документом, и по нему
    видно, что именно ему позволено.
    """

    __slots__ = ("_window",)

    def __init__(self, window: MainWindow) -> None:
        self._window = window

    def document(self) -> SubtitleDocument:
        return self._window._doc

    def selection(self) -> list[int]:
        return self._window._selected_eids()

    def run_command(self, command) -> None:
        self._window._undo.run(command)

    def report(self, message: str) -> None:
        self._window._show_status(str(message))

    def show_report(self, title: str, text: str) -> None:
        """Окно с отчётом плагина. Текст можно выделить и скопировать —
        ради этого его обычно и просят."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

        dialog = QDialog(self._window)
        dialog.setWindowTitle(str(title))
        dialog.resize(640, 520)

        view = QPlainTextEdit(str(text))
        view.setReadOnly(True)
        # Моноширинный: в отчётах колонки, и пропорциональный шрифт их развалит.
        font = view.font()
        font.setFamily("Consolas")
        font.setStyleHint(font.StyleHint.Monospace)
        view.setFont(font)

        layout = QVBoxLayout(dialog)
        layout.addWidget(view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.rejected.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()


def _demo_document() -> SubtitleDocument:
    """Демо-документ, чтобы окно не открывалось пустым."""
    from sfstudio.core.style import SubtitleStyle

    doc = SubtitleDocument.blank((1920, 1080))
    doc.styles["Default"].fontsize = 54

    note = SubtitleStyle(name="Надпись", fontsize=44, alignment=8)
    note.primary = doc.styles["Default"].primary
    doc.add_style(note)

    doc.create_event(0, 4000, "Перетащите эту строку мышью")
    doc.create_event(0, 4000, r"{\pos(300,220)}Надпись сверху слева", style="Надпись")
    doc.create_event(4500, 8000, r"Вторая реплика,\Nв две строки")
    doc.create_event(8500, 12_000, r"{\pos(960,540)\an5}По центру кадра")
    return doc

