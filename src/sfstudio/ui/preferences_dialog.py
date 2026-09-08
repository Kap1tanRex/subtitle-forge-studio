"""Настройки программы.

Разложены по вкладкам так, чтобы вопрос «где это менять» имел один ответ:

* **Проекты** — где хранить, что открывать при запуске, автосохранение;
* **Редактирование** — магниты, минимальный зазор, способ позиционирования;
* **Воспроизведение** — громкость, поведение при открытии;
* **Проверки** — профиль QC и порог важности;
* **Интерфейс** — тема, плотность таблицы, раскладка панелей.

Настройки применяются **сразу**, без кнопки «Применить». Кнопка нужна там, где
изменение дорого откатывать; здесь всё обратимо, а лишний шаг между «поменял» и
«увидел» только мешает подбирать значение. Кнопка «Закрыть» именно закрывает,
ничего не отменяя, — и это честно написано на ней.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sfstudio.app.i18n import available_languages, translation_progress
from sfstudio.app.settings import Settings
from sfstudio.app.storage import (
    StorageMode,
    beside_program,
    libraries_dir,
    models_dir,
    plugins_dir,
    writable,
)
from sfstudio.core.actor_label import (
    LABEL_PRESETS,
    LINE_BREAK,
    TEXT_FIELD,
    format_label,
    template_for,
)
from sfstudio.services.qc import PROFILES
from sfstudio.ui.appearance import themes_for
from sfstudio.ui.combo import select_data
from sfstudio.ui.font_box import FontComboBox, font_sample_html
from sfstudio.ui.layout import LayoutPreset
from sfstudio.ui.theme import ACCENTS, MAX_FONT_SCALE, MIN_FONT_SCALE, repolish
from sfstudio.ui.theme_pack import SUFFIX, example_text

__all__ = ["PreferencesDialog"]

#: Пункт «свой цвет» в списке акцентов. Строка, которой заведомо нет среди
#: цветов: сравнение идёт по данным пункта, а не по подписи.
CUSTOM_ACCENT = "__custom__"


def swatch(color: str):
    """Квадратик цвета для пункта списка."""
    from PySide6.QtGui import QColor, QIcon, QPixmap

    pixmap = QPixmap(14, 14)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)

#: Плотность таблицы: подпись и высота строки.
DENSITIES: tuple[tuple[str, str, int], ...] = (
    ("Компактная", "compact", 20),
    ("Обычная", "comfortable", 24),
    ("Просторная", "spacious", 30),
)


class PreferencesDialog(QDialog):
    """Тонкие настройки приложения."""

    #: Что-то изменилось и требует немедленного отклика окна.
    applied = Signal()
    layout_preset_requested = Signal(object)
    layout_reset_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.resize(600, 480)
        self._settings = settings
        # Пока идёт первичное заполнение, обработчики не пишут в настройки:
        # иначе открытие окна само по себе помечало бы их изменёнными.
        self._loading = True

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._projects_tab(), "Проекты")
        self.tabs.addTab(self._subtitles_tab(), "Субтитры")
        self.tabs.addTab(self._editing_tab(), "Редактирование")
        self.tabs.addTab(self._playback_tab(), "Воспроизведение")
        self.tabs.addTab(self._qc_tab(), "Проверки")
        self.tabs.addTab(self._interface_tab(), "Интерфейс")
        self.tabs.addTab(self._storage_tab(), "Загрузки")
        self.tabs.addTab(self._plugins_tab(), "Плагины")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close | QDialogButtonBox.RestoreDefaults)
        buttons.button(QDialogButtonBox.Close).setText("Закрыть")
        buttons.button(QDialogButtonBox.RestoreDefaults).setText("Сбросить раздел")
        buttons.rejected.connect(self.accept)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._reset_section)
        layout.addWidget(buttons)

        self._load()
        self._loading = False

    def show_tab(self, title: str) -> bool:
        """Открывает вкладку по названию. ``False`` — такой нет."""
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == title:
                self.tabs.setCurrentIndex(index)
                return True
        return False

    # -- вкладки -------------------------------------------------------------- #

    def _projects_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("по умолчанию — домашняя папка")
        self.folder_edit.editingFinished.connect(
            lambda: self._set("project.folder", self.folder_edit.text().strip())
        )
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(browse)
        form.addRow("Папка проектов", folder_row)

        self.startup_check = QCheckBox("Показывать окно выбора проекта при запуске")
        self.startup_check.toggled.connect(
            lambda on: self._set("project.show_startup_dialog", on)
        )
        form.addRow("", self.startup_check)

        self.restore_check = QCheckBox("Открывать последний проект сразу")
        self.restore_check.setToolTip(
            "Окно выбора при этом не показывается"
        )
        self.restore_check.toggled.connect(
            lambda on: self._set("project.restore_last_on_start", on)
        )
        form.addRow("", self.restore_check)

        self.autosave_spin = QSpinBox()
        self.autosave_spin.setRange(0, 120)
        self.autosave_spin.setSuffix(" мин")
        self.autosave_spin.setSpecialValueText("выключено")
        self.autosave_spin.setToolTip(
            "Автосохранение пишет копию рядом с проектом и не трогает сам файл"
        )
        self.autosave_spin.valueChanged.connect(
            lambda v: self._set("project.autosave_minutes", v)
        )
        form.addRow("Автосохранение", self.autosave_spin)

        hint = QLabel(
            "Проект хранит субтитры, привязанное видео и место, где вы "
            "остановились. Файл субтитров этого не помнит."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return page

    def _storage_tab(self) -> QWidget:
        """Куда складывать скачанное: модели, библиотеки, плагины."""
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.storage_box = QComboBox()
        for mode in StorageMode:
            self.storage_box.addItem(mode.title, mode.value)
        self.storage_box.currentIndexChanged.connect(self._on_storage_mode)
        form.addRow("Складывать", self.storage_box)

        self.storage_note = QLabel("")
        self.storage_note.setProperty("role", "hint")
        self.storage_note.setWordWrap(True)
        form.addRow("", self.storage_note)

        storage_row = QHBoxLayout()
        self.storage_edit = QLineEdit()
        self.storage_edit.setPlaceholderText("путь к папке")
        self.storage_edit.editingFinished.connect(
            lambda: self._set("storage.folder", self.storage_edit.text().strip())
        )
        storage_row.addWidget(self.storage_edit, 1)
        storage_browse = QPushButton("Обзор…")
        storage_browse.clicked.connect(self._choose_storage_folder)
        storage_row.addWidget(storage_browse)
        form.addRow("Своя папка", storage_row)

        self.storage_paths = QLabel("")
        self.storage_paths.setProperty("role", "hint")
        self.storage_paths.setWordWrap(True)
        self.storage_paths.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Получается", self.storage_paths)

        hint = QLabel(
            "Модель распознавания весит от 75 МБ до 3 ГБ, библиотеки для "
            "видеокарты — ещё 0,7 ГБ. Всё это качается только по вашей "
            "команде и лежит там, где вы укажете: папку можно удалить "
            "целиком, программа продолжит работать без неё."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return page

    def _plugins_tab(self) -> QWidget:
        """Список плагинов: что нашлось, что работает, что сломано."""
        page = QWidget()
        layout = QVBoxLayout(page)

        self.plugins_check = QCheckBox("Загружать плагины при запуске")
        self.plugins_check.setToolTip("Изменение вступит в силу после перезапуска")
        self.plugins_check.toggled.connect(
            lambda on: self._set("plugins.enabled", on)
        )
        layout.addWidget(self.plugins_check)

        warning = QLabel(
            "Плагин — это чужой код, который выполняется наравне с самой "
            "программой: он может читать и менять ваши файлы. Ставьте только "
            "те, чьему автору доверяете."
        )
        warning.setProperty("role", "warning")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.plugins_list = QListWidget()
        self.plugins_list.setAlternatingRowColors(True)
        layout.addWidget(self.plugins_list, 1)

        self.plugins_folder = QLabel("")
        self.plugins_folder.setProperty("role", "hint")
        self.plugins_folder.setWordWrap(True)
        self.plugins_folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.plugins_folder)

        hint = QLabel(
            "Плагин — папка с файлом plugin.json и модулем Python. Положите "
            "её в каталог выше и перезапустите программу."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _refresh_plugins(self) -> None:
        """Перечитывает каталог плагинов.

        Сломанные показываются вместе с причиной: плагин, который не
        загрузился и об этом не сказал, выглядит как несуществующий, и
        человек ищет ошибку в своей папке, а не в коде плагина.
        """
        from sfstudio.plugins import PluginManager, PluginState

        folder = plugins_dir(self._settings)
        self.plugins_folder.setText(f"Каталог плагинов: {folder}")
        self.plugins_list.clear()

        manager = PluginManager(folder, self._settings)
        try:
            found = manager.load_all(
                enabled=bool(self._settings.get("plugins.enabled", False))
            )
        except Exception as exc:
            self.plugins_list.addItem(f"Не удалось прочитать каталог: {exc}")
            return

        if not found:
            self.plugins_list.addItem("Плагинов нет.")
            return

        for plugin in found:
            text = plugin.caption
            if plugin.reason:
                text += f"\n    {plugin.reason}"
            item = QListWidgetItem(text)
            if plugin.state is not PluginState.LOADED:
                item.setForeground(self.palette().color(self.foregroundRole()).darker(140))
            self.plugins_list.addItem(item)

    def _on_language(self, index: int) -> None:
        self._set("ui.language", self.language_box.itemData(index))
        self._refresh_language_note()

    def _refresh_language_note(self) -> None:
        """Показывает, насколько перевод готов.

        Неполный перевод — обычное состояние: строки добавляются вместе с
        возможностями. Молчать о доле переведённого нельзя, иначе человек
        решит, что программа сломалась, увидев смесь двух языков.
        """
        code = str(self.language_box.currentData() or "ru")
        if code == "ru":
            self.language_note.setText("Язык оригинала.")
            return
        share = translation_progress(code)
        self.language_note.setText(
            f"Переведено {share:.0%} строк. Остальное показывается по-русски. "
            "Изменение применится после перезапуска."
        )

    def _on_storage_mode(self, index: int) -> None:
        mode = str(self.storage_box.itemData(index) or StorageMode.BESIDE)
        self._set("storage.mode", mode)
        self._refresh_storage()

    def _choose_storage_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Папка для загрузок", self.storage_edit.text() or str(Path.home())
        )
        if chosen:
            self.storage_edit.setText(chosen)
            self._set("storage.folder", chosen)
            self._refresh_storage()

    def _refresh_storage(self) -> None:
        """Показывает и режим, и то, какие пути из него получаются.

        Названия режимов человеку ничего не говорят до тех пор, пока он не
        увидит настоящие пути. Заодно видно, если выбран режим «рядом с
        программой», а писать туда нельзя.
        """
        mode = StorageMode(str(self.storage_box.currentData() or StorageMode.BESIDE))
        self.storage_note.setText(mode.note)
        self.storage_edit.setEnabled(mode is StorageMode.CUSTOM)

        models = models_dir(self._settings)
        libraries = libraries_dir(self._settings)
        lines = [f"модели: {models}", f"библиотеки: {libraries}"]
        if mode is StorageMode.BESIDE and not writable(beside_program()):
            lines.append(
                "Рядом с программой писать нельзя — выберите профиль "
                "пользователя или свою папку."
            )
        self.storage_paths.setText("\n".join(lines))

    def _subtitles_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.font_box = FontComboBox()
        self.font_box.setToolTip(
            "Наведите на шрифт в списке — появится образец русского и "
            "английского текста"
        )
        self.font_box.currentFontChanged.connect(
            lambda f: self._set("subtitles.default_font", f.family())
        )
        form.addRow("Шрифт по умолчанию", self.font_box)

        self.font_size_spin = QDoubleSpinBox()
        self.font_size_spin.setRange(8.0, 400.0)
        self.font_size_spin.setDecimals(0)
        self.font_size_spin.valueChanged.connect(
            lambda v: self._set("subtitles.default_size", float(v))
        )
        form.addRow("Кегль по умолчанию", self.font_size_spin)

        self.font_sample = QLabel("")
        self.font_sample.setWordWrap(True)
        self.font_sample.setTextFormat(Qt.RichText)
        form.addRow("Образец", self.font_sample)

        hint = QLabel(
            "Шрифт применяется к новым проектам и новым стилям. Уже созданные "
            "стили не меняются — иначе правка настройки переоформила бы "
            "чужие файлы."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        form.addRow("", hint)

        self.label_box = QComboBox()
        for fmt in LABEL_PRESETS:
            self.label_box.addItem(fmt.title, fmt.key)
        self.label_box.setToolTip(
            "Имя говорящего прямо в тексте реплики — его видит зритель"
        )
        self.label_box.currentIndexChanged.connect(self._on_label_format)
        form.addRow("Имя говорящего", self.label_box)

        self.label_custom = QLineEdit()
        self.label_custom.setPlaceholderText("[{actor}] {text}")
        self.label_custom.setToolTip(
            "Свой шаблон. {actor} — имя, {text} — сама реплика"
        )
        self.label_custom.textChanged.connect(self._on_label_custom)
        form.addRow("Свой шаблон", self.label_custom)

        self.label_sample = QLabel("")
        self.label_sample.setWordWrap(True)
        form.addRow("Получится", self.label_sample)

        label_hint = QLabel(
            "Метки не проставляются задним числом: настройка действует на "
            "будущие назначения говорящих. Уже готовый файл обновляет команда "
            "«Правка → Обновить метки говорящих» — одним шагом истории, и её "
            "можно отменить."
        )
        label_hint.setProperty("role", "hint")
        label_hint.setWordWrap(True)
        form.addRow("", label_hint)
        return page

    def _on_label_format(self, index: int) -> None:
        self._set("subtitles.actor_label", self.label_box.itemData(index))
        self._refresh_label_sample()

    def _on_label_custom(self, text: str) -> None:
        self._set("subtitles.actor_label_custom", text)
        self._refresh_label_sample()

    def _refresh_label_sample(self) -> None:
        """Показывает, что получится, — на живом примере.

        Названия форматов («в начале строки», «над репликой») понятны не
        всем и не сразу, а увиденный образец не требует объяснений. Заодно
        сразу видно ошибку в своём шаблоне.
        """
        key = str(self.label_box.currentData() or "off")
        custom = self.label_custom.text()
        self.label_custom.setEnabled(key == "custom")

        template = template_for(key, custom)
        if template == TEXT_FIELD:
            self.label_sample.setProperty("role", "hint")
            self.label_sample.setText(
                "Привет  — имя останется только в поле говорящего"
                if key == "off"
                else "В шаблоне нет {text} — реплика потерялась бы. "
                     "Пока метка не добавляется."
            )
            self.label_sample.setProperty(
                "role", "hint" if key == "off" else "warning"
            )
        else:
            sample = format_label(template, "Иван", "Привет")
            self.label_sample.setProperty("role", "hint")
            self.label_sample.setText(sample.replace(LINE_BREAK, "  ⏎  "))
        repolish(self.label_sample)

    def _editing_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.snap_frames = QCheckBox("К кадрам")
        self.snap_frames.toggled.connect(
            lambda on: self._set("editing.snap_to_frames", on)
        )
        form.addRow("Магниты", self.snap_frames)

        self.snap_keyframes = QCheckBox("К ключевым кадрам")
        self.snap_keyframes.toggled.connect(
            lambda on: self._set("editing.snap_to_keyframes", on)
        )
        form.addRow("", self.snap_keyframes)

        self.snap_events = QCheckBox("К границам соседних реплик")
        self.snap_events.toggled.connect(
            lambda on: self._set("editing.snap_to_events", on)
        )
        form.addRow("", self.snap_events)

        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(0, 30)
        self.gap_spin.setSuffix(" кадр(ов)")
        self.gap_spin.setToolTip(
            "Минимальный зазор между репликами: слипшиеся строки читаются как одна"
        )
        self.gap_spin.valueChanged.connect(
            lambda v: self._set("editing.min_gap_frames", v)
        )
        form.addRow("Зазор между репликами", self.gap_spin)

        self.position_box = QComboBox()
        self.position_box.addItem("Координатами \\pos", "pos")
        self.position_box.addItem("Полями и выравниванием", "margins")
        self.position_box.setToolTip(
            "Чем записывать перетаскивание субтитра в кадре"
        )
        self.position_box.currentIndexChanged.connect(
            lambda i: self._set("editing.position_mode", self.position_box.itemData(i))
        )
        form.addRow("Положение в кадре", self.position_box)

        self.paste_box = QComboBox()
        self.paste_box.addItem("На курсор таймлайна", "playhead")
        self.paste_box.addItem("Под указатель мыши", "mouse")
        self.paste_box.setToolTip(
            "Куда попадёт реплика, вставленная из буфера обмена (Ctrl+Shift+V)"
        )
        self.paste_box.currentIndexChanged.connect(
            lambda i: self._set("editing.paste_at", self.paste_box.itemData(i))
        )
        form.addRow("Вставка из буфера", self.paste_box)

        paste_hint = QLabel(
            "Под указателем мыши удобно, когда место выбирают глазами по волне; "
            "на курсоре — когда работают клавишами. Если мышь не над дорожкой "
            "субтитров, программа скажет об этом, а не станет угадывать место."
        )
        paste_hint.setProperty("role", "hint")
        paste_hint.setWordWrap(True)
        form.addRow("", paste_hint)
        return page

    def _playback_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(0.0, 100.0)
        self.volume_spin.setDecimals(0)
        self.volume_spin.setSuffix(" %")
        self.volume_spin.valueChanged.connect(
            lambda v: self._set("media.volume", v)
        )
        form.addRow("Громкость", self.volume_spin)

        self.autoplay_check = QCheckBox("Начинать воспроизведение при открытии видео")
        self.autoplay_check.toggled.connect(
            lambda on: self._set("media.autoplay", on)
        )
        form.addRow("", self.autoplay_check)
        return page

    def _qc_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.profile_box = QComboBox()
        for key, profile in PROFILES.items():
            self.profile_box.addItem(profile.name, key)
        self.profile_box.currentIndexChanged.connect(self._on_profile)
        form.addRow("Профиль", self.profile_box)

        self.severity_box = QComboBox()
        for title, value in (("Все", 0), ("Предупреждения и ошибки", 1), ("Только ошибки", 2)):
            self.severity_box.addItem(title, value)
        self.severity_box.currentIndexChanged.connect(
            lambda i: self._set("qc.min_severity", self.severity_box.itemData(i))
        )
        form.addRow("Показывать", self.severity_box)

        self.profile_hint = QLabel("")
        self.profile_hint.setProperty("role", "hint")
        self.profile_hint.setWordWrap(True)
        form.addRow("", self.profile_hint)
        return page

    def _interface_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.language_box = QComboBox()
        for code, title in available_languages().items():
            self.language_box.addItem(title, code)
        self.language_box.setToolTip("Изменение вступит в силу после перезапуска")
        self.language_box.currentIndexChanged.connect(self._on_language)
        form.addRow("Язык", self.language_box)

        self.language_note = QLabel("")
        self.language_note.setProperty("role", "hint")
        self.language_note.setWordWrap(True)
        form.addRow("", self.language_note)

        self.theme_box = QComboBox()
        self.theme_box.currentIndexChanged.connect(self._on_theme)
        form.addRow("Тема", self.theme_box)

        self.theme_note = QLabel("")
        self.theme_note.setProperty("role", "hint")
        self.theme_note.setWordWrap(True)
        form.addRow("", self.theme_note)

        themes_row = QHBoxLayout()
        open_themes = QPushButton("Папка тем")
        open_themes.setToolTip("Открыть каталог, куда кладут файлы тем")
        open_themes.clicked.connect(self._open_themes_folder)
        themes_row.addWidget(open_themes)
        make_theme = QPushButton("Создать образец")
        make_theme.setToolTip(
            "Положить в каталог тем готовый файл, который можно править"
        )
        make_theme.clicked.connect(self._make_theme_example)
        themes_row.addWidget(make_theme)
        themes_row.addStretch(1)
        form.addRow("", themes_row)

        self.accent_box = QComboBox()
        self.accent_box.addItem("Как в теме", "")
        for title, value in ACCENTS:
            self.accent_box.addItem(swatch(value), title, value)
        self.accent_box.addItem("Свой цвет…", CUSTOM_ACCENT)
        self.accent_box.currentIndexChanged.connect(self._on_accent)
        form.addRow("Акцентный цвет", self.accent_box)

        self.motion_box = QComboBox()
        self.motion_box.addItem("Как в системе", None)
        self.motion_box.addItem("Включены", True)
        self.motion_box.addItem("Выключены", False)
        self.motion_box.setToolTip(
            "Плавное появление окон, меню и прокрутки. «Как в системе» — "
            "смотреть настройку анимации Windows"
        )
        self.motion_box.currentIndexChanged.connect(
            lambda i: self._set("ui.animations", self.motion_box.itemData(i))
        )
        form.addRow("Анимации", self.motion_box)

        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(MIN_FONT_SCALE, MAX_FONT_SCALE)
        self.scale_spin.setSingleStep(10)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setToolTip(
            "Размер шрифта интерфейса. Размеры виджетов Qt считает от шрифта, "
            "поэтому вместе с ним растут отступы, строки и кнопки"
        )
        self.scale_spin.valueChanged.connect(
            lambda value: self._set("ui.font_scale", value)
        )
        form.addRow("Масштаб интерфейса", self.scale_spin)

        self.density_box = QComboBox()
        for title, key, _ in DENSITIES:
            self.density_box.addItem(title, key)
        self.density_box.currentIndexChanged.connect(
            lambda i: self._set("ui.table_density", self.density_box.itemData(i))
        )
        form.addRow("Плотность таблицы", self.density_box)

        self.preset_box = QComboBox()
        for preset in LayoutPreset:
            self.preset_box.addItem(preset.title, preset)
        apply_row = QHBoxLayout()
        apply_row.addWidget(self.preset_box, 1)
        apply_button = QPushButton("Применить")
        apply_button.clicked.connect(
            lambda: self.layout_preset_requested.emit(self.preset_box.currentData())
        )
        apply_row.addWidget(apply_button)
        form.addRow("Раскладка панелей", apply_row)

        reset_button = QPushButton("Сбросить расположение панелей")
        reset_button.clicked.connect(self.layout_reset_requested)
        form.addRow("", reset_button)

        hint = QLabel(
            "Панели можно перетаскивать за заголовок, отрывать в отдельные окна "
            "и менять их размер, ведя мышью по границе. Расположение "
            "запоминается между запусками."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return page

    # -- загрузка и запись ---------------------------------------------------- #

    def _load(self) -> None:
        get = self._settings.get

        self.folder_edit.setText(str(get("project.folder", "") or ""))
        self.startup_check.setChecked(bool(get("project.show_startup_dialog", True)))
        self.restore_check.setChecked(bool(get("project.restore_last_on_start", False)))
        self.autosave_spin.setValue(int(get("project.autosave_minutes", 5) or 0))

        self.snap_frames.setChecked(bool(get("editing.snap_to_frames", True)))
        self.snap_keyframes.setChecked(bool(get("editing.snap_to_keyframes", True)))
        self.snap_events.setChecked(bool(get("editing.snap_to_events", True)))
        self.gap_spin.setValue(int(get("editing.min_gap_frames", 2) or 0))
        self._select(self.position_box, get("editing.position_mode", "pos"))
        self._select(self.paste_box, get("editing.paste_at", "playhead"))

        self.volume_spin.setValue(float(get("media.volume", 80.0) or 0.0))
        self.autoplay_check.setChecked(bool(get("media.autoplay", False)))

        self._select(self.profile_box, get("qc.profile", "general"))
        self._select(self.severity_box, int(get("qc.min_severity", 0) or 0))
        self._refresh_profile_hint()

        self.scale_spin.setValue(int(get("ui.font_scale", 100) or 100))
        self._select(self.language_box, get("ui.language", "ru"))
        self._refresh_language_note()
        self._fill_themes()
        self._select(self.theme_box, get("ui.theme", "dark"))
        self._refresh_theme_note()
        self._select_accent(str(get("ui.accent", "") or ""))
        self._select(self.motion_box, get("ui.animations", None))
        self._select(self.density_box, get("ui.table_density", "comfortable"))

        self._select(self.storage_box, get("storage.mode", "beside"))
        self.storage_edit.setText(str(get("storage.folder", "") or ""))
        self._refresh_storage()

        self.plugins_check.setChecked(bool(get("plugins.enabled", False)))
        self._refresh_plugins()

        self._select(self.label_box, get("subtitles.actor_label", "off"))
        self.label_custom.setText(
            str(get("subtitles.actor_label_custom", "") or "")
        )
        self._refresh_label_sample()

        family = str(get("subtitles.default_font", "Arial") or "Arial")
        self.font_box.set_family(family)
        self.font_size_spin.setValue(float(get("subtitles.default_size", 54.0) or 54.0))
        self._refresh_font_sample()

    def _select(self, box: QComboBox, value: object) -> None:
        select_data(box, value)

    # -- темы и цвета --------------------------------------------------------- #

    def _fill_themes(self) -> None:
        """Наполняет список: встроенные темы и файлы из каталога тем.

        Сломанный файл не пропадает из списка, а показывается недоступным с
        причиной: тема, которая просто исчезла, выглядит как потеря файла, и
        человек будет искать её, вместо того чтобы исправить опечатку.
        """
        self.theme_box.blockSignals(True)
        self.theme_box.clear()
        for pack in sorted(
            themes_for(self._settings).values(),
            key=lambda p: (not p.builtin, p.title.lower()),
        ):
            title = pack.title if pack.builtin else f"{pack.title} (файл)"
            self.theme_box.addItem(title, pack.name)
            index = self.theme_box.count() - 1
            if pack.error:
                self.theme_box.setItemData(index, False, Qt.UserRole - 1)  # недоступен
                self.theme_box.setItemData(index, pack.error, Qt.ToolTipRole)
        self.theme_box.blockSignals(False)

    def _on_theme(self, index: int) -> None:
        self._set("ui.theme", self.theme_box.itemData(index))
        self._refresh_theme_note()

    def _refresh_theme_note(self) -> None:
        packs = themes_for(self._settings)
        pack = packs.get(str(self.theme_box.currentData() or ""))
        if pack is None:
            self.theme_note.setText("")
            return
        if pack.error:
            self.theme_note.setText(f"Тема не читается: {pack.error}")
            return
        if pack.builtin:
            self.theme_note.setText("Встроенная тема.")
            return
        author = f", автор {pack.author}" if pack.author else ""
        self.theme_note.setText(f"Из файла {pack.path.name}{author}.")

    def _themes_folder(self) -> Path:
        from sfstudio.app.storage import themes_dir

        return themes_dir(self._settings)

    def _open_themes_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        folder = self._themes_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Папка тем", f"Не удалось создать: {exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _make_theme_example(self) -> None:
        """Кладёт готовый файл темы, который остаётся только поправить.

        Пустой каталог и формат, описанный словами, — не то же самое, что
        рабочий файл под рукой: с образца тему делают правкой цветов, а не
        чтением документации.
        """
        folder = self._themes_folder()
        target = folder / ("образец" + SUFFIX)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if target.exists():
                # Второй вызов не должен затирать уже поправленный образец.
                target = folder / f"образец-{len(list(folder.glob('*' + SUFFIX)))}{SUFFIX}"
            target.write_text(example_text(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Образец темы", f"Не удалось записать: {exc}")
            return

        self._fill_themes()
        self._select(self.theme_box, self._settings.get("ui.theme", "dark"))
        QMessageBox.information(
            self, "Образец темы",
            f"Готово: {target}\n\nПоправьте цвета в файле и выберите тему в списке.",
        )

    def _select_accent(self, value: str) -> None:
        self.accent_box.blockSignals(True)
        if value and select_data(self.accent_box, value) is False:
            # Свой цвет: показываем его же квадратиком, чтобы выбор был виден.
            self.accent_box.insertItem(
                self.accent_box.count() - 1, swatch(value), value, value
            )
            select_data(self.accent_box, value)
        elif not value:
            select_data(self.accent_box, "")
        self.accent_box.blockSignals(False)

    def _on_accent(self, index: int) -> None:
        value = self.accent_box.itemData(index)
        if value != CUSTOM_ACCENT:
            self._set("ui.accent", value or "")
            return

        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog

        current = str(self._settings.get("ui.accent", "") or "#4C8DFF")
        chosen = QColorDialog.getColor(QColor(current), self, "Акцентный цвет")
        if not chosen.isValid():
            self._select_accent(str(self._settings.get("ui.accent", "") or ""))
            return
        self._select_accent(chosen.name())
        self._set("ui.accent", chosen.name())

    def _refresh_font_sample(self) -> None:
        """Образец прямо в окне: подсказка при наведении хороша для перебора,
        но выбранный шрифт должен быть виден и без наведения."""
        self.font_sample.setText(font_sample_html(self.font_box.currentFont().family()))

    def _set(self, key: str, value: object) -> None:
        if self._loading:
            return
        self._settings.set(key, value)
        self._settings.save()
        if key.startswith("subtitles."):
            self._refresh_font_sample()
        self.applied.emit()

    def _on_profile(self, index: int) -> None:
        self._set("qc.profile", self.profile_box.itemData(index))
        self._refresh_profile_hint()

    def _refresh_profile_hint(self) -> None:
        profile = PROFILES.get(str(self.profile_box.currentData() or ""))
        if profile is None:
            self.profile_hint.setText("")
            return
        self.profile_hint.setText(
            f"Не более {profile.max_cps:g} знаков в секунду, "
            f"{profile.max_line_length} символов в строке, "
            f"{profile.max_lines} строк(и); длительность "
            f"{profile.min_duration_ms / 1000:g}–{profile.max_duration_ms / 1000:g} с."
        )

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Папка проектов", self.folder_edit.text() or str(Path.home())
        )
        if chosen:
            self.folder_edit.setText(chosen)
            self._set("project.folder", chosen)

    def _reset_section(self) -> None:
        """Сбрасывает раздел открытой вкладки, а не все настройки разом."""
        sections = ("project", "subtitles", "editing", "media", "qc", "ui",
                    "storage", "plugins")
        section = sections[min(self.tabs.currentIndex(), len(sections) - 1)]
        title = self.tabs.tabText(self.tabs.currentIndex())
        answer = QMessageBox.question(
            self,
            "Сбросить раздел",
            f"Вернуть значения по умолчанию для раздела «{title}»?",
        )
        if answer != QMessageBox.Yes:
            return
        self._settings.reset(section)
        self._settings.save()
        self._loading = True
        try:
            self._load()
        finally:
            self._loading = False
        self.applied.emit()
