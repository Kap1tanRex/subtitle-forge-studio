"""Запуск GUI.

Порядок такой: сначала стартовое окно (с чем работать), потом главное. Обратный
порядок — сначала показать пустой редактор, потом спросить — заставляет
пользователя смотреть на демонстрационный документ, который он всё равно
закроет.

**Отказ на любом шаге возвращает к выбору проекта, а не закрывает программу.**
Нажать «Новый проект», передумать в диалоге параметров и обнаружить, что
приложение закрылось, — поведение, которое не прощают: это выглядит как сбой,
а не как результат своего действия. Выход происходит только когда пользователь
закрывает само стартовое окно.

Стартовое окно пропускается, когда файл передан в аргументах командной строки
(намерение уже выражено), когда включено «открывать последний проект сразу», и
когда пользователь выключил его в настройках.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from sfstudio.app.i18n import set_language
from sfstudio.app.settings import Settings
from sfstudio.core.project import PROJECT_SUFFIX
from sfstudio.ui.main_window import MainWindow
from sfstudio.ui.theme import apply_font_scale, build_qss, palette_by_name

#: По расширению решаем, чем является переданный файл. Разбирать содержимое
#: медиа ради этого не нужно: ошибка расширения здесь стоит лишь одного
#: неудачного открытия, о котором пользователь сразу узнает из диалога.
MEDIA_SUFFIXES = frozenset(
    {".mkv", ".mp4", ".mov", ".webm", ".avi", ".m4v", ".ts",
     ".wav", ".mp3", ".aac", ".flac", ".opus", ".ogg"}
)


class _Plan:
    """Что делать после запуска."""

    __slots__ = ("document", "media_path", "new_project", "project_path")

    def __init__(self) -> None:
        self.project_path: Path | None = None
        self.media_path: Path | None = None
        self.document = None
        #: Готовый проект и путь, куда его записать. Создаётся до главного
        #: окна: отказ в диалоге параметров должен вернуть к выбору проекта,
        #: а не оставить открытым окно с пустым документом.
        self.new_project: tuple[object, Path] | None = None


def run(path: Path | None = None) -> int:
    # PassThrough: все размеры заданы в логических пикселях, растровых
    # ассетов в проекте нет, поэтому округлять масштаб DPI не требуется.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("SubtitleForge Studio")

    settings = Settings()
    # Тема читается до создания окон: виджеты получают палитру при сборке,
    # и применять её потом пришлось бы обходом всего дерева.
    theme = str(settings.get("ui.theme", "dark"))
    app.setStyleSheet(build_qss(palette_by_name(theme)))
    # Отметка для окна: оно не станет пересчитывать тот же стиль заново.
    # Язык ставится раньше окон: подписи читаются при их сборке, и
    # переключение после этого потребовало бы пересобрать интерфейс.
    set_language(settings.get("ui.language", "ru"))
    app.setProperty("sfstudio_theme", theme)
    apply_font_scale(app, settings.get("ui.font_scale", 100))
    plan = _decide(path, settings)
    if plan is None:
        return 0  # стартовое окно закрыли, не выбрав ничего

    window = MainWindow(plan.document, settings)
    window.show()

    if plan.project_path is not None:
        window.open_project_file(plan.project_path)
    elif plan.new_project is not None:
        project, target = plan.new_project
        window.adopt_project(project, target)
    elif plan.media_path is not None:
        # После show(): загрузка медиа опирается на размеры таймлайна,
        # а до первой раскладки они ещё нулевые.
        window.load_media(plan.media_path)

    return app.exec()


def _decide(path: Path | None, settings: Settings) -> _Plan | None:
    """Что открывать. ``None`` — пользователь отказался и от выбора тоже."""
    plan = _Plan()

    if path is not None:
        problem = _argument_plan(path, plan)
        if not problem:
            return plan
        # Плохой аргумент — не повод закрываться: человеку сказали, что не
        # так, и дальше он работает как при обычном запуске. Раньше здесь
        # вылетало необработанное исключение, и программа падала целиком —
        # достаточно было передать имя папки вместо файла.
        QMessageBox.warning(None, "Не удалось открыть", problem)

    if bool(settings.get("project.restore_last_on_start", False)):
        recent = settings.recent("projects")
        if recent:
            plan.project_path = recent[0]
            return plan

    if not bool(settings.get("project.show_startup_dialog", True)):
        return plan

    # Цикл: отказ на шаге после стартового окна возвращает к нему же.
    while True:
        answer = _ask(settings)
        if answer is None:
            return None      # закрыли стартовое окно — это и есть выход
        if answer is not _RETRY:
            return answer


def _argument_plan(path: Path, plan: _Plan) -> str:
    """Разбирает файл, переданный в командной строке.

    Возвращает пустую строку, если открывать есть что, иначе — причину
    человеческими словами. Причин ровно три, и все встречаются: имя набрано
    с опечаткой, вместо файла указана папка, файл не читается или не
    разбирается.
    """
    if not path.exists():
        return f"Файл не найден:\n{path}"
    if not path.is_file():
        return (
            f"Это папка, а не файл:\n{path}\n\n"
            "Откройте файл субтитров, проект или видео."
        )

    suffix = path.suffix.lower()
    if suffix == PROJECT_SUFFIX:
        plan.project_path = path
        return ""
    if suffix in MEDIA_SUFFIXES:
        plan.media_path = path
        return ""

    from sfstudio.io import registry

    try:
        plan.document = registry.load(path)
    except OSError as exc:
        return f"Файл не открывается:\n{path}\n\n{exc.strerror or exc}"
    except Exception as exc:
        # Неизвестный формат, битая кодировка, обрыв на середине — читателю
        # важно, что именно не так, а не тип исключения.
        return f"Файл не удалось прочитать:\n{path}\n\n{exc}"

    # Разбор субтитров всеяден: любой набор байтов «читается» как файл без
    # единой реплики. Молча открыть пустой редактор — значит соврать, что
    # файл открыт. Пустой .srt или .ass при этом законен: человек мог сам
    # создать заготовку, и мешать ему не за что.
    if not plan.document.events and _is_not_subtitles(path, suffix):
        plan.document = None
        return (
            f"В файле нет ни одной реплики:\n{path}\n\n"
            "Похоже, это не файл субтитров."
        )
    return ""


def _is_not_subtitles(path: Path, suffix: str) -> bool:
    """Непустой файл с чужим расширением — почти наверняка не субтитры."""
    from sfstudio.io.registry import FORMATS

    known = {ext for spec in FORMATS.values() for ext in spec.extensions}
    if suffix in known:
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


#: Часовой: «пользователь передумал, спросим ещё раз».
_RETRY = object()


def _ask(settings: Settings):
    """Один проход стартового окна.

    Возвращает :class:`_Plan`, ``None`` (выход) либо ``_RETRY`` — когда
    пользователь отказался от следующего шага и должен снова увидеть выбор.
    """
    from sfstudio.ui.startup_dialog import (
        ACTION_IMPORT,
        ACTION_NEW,
        ACTION_OPEN,
        StartupDialog,
    )

    dialog = StartupDialog(settings.recent("projects", existing_only=False))
    if dialog.exec() != StartupDialog.Accepted or dialog.action is None:
        return None

    plan = _Plan()

    if dialog.chosen_path is not None:
        plan.project_path = dialog.chosen_path
        return plan

    if dialog.action == ACTION_NEW:
        created = _ask_new_project(settings)
        if created is None:
            return _RETRY
        plan.new_project = created
        return plan

    if dialog.action == ACTION_OPEN:
        chosen, _ = QFileDialog.getOpenFileName(
            None, "Открыть проект", _default_folder(settings),
            f"Проект SubtitleForge (*{PROJECT_SUFFIX});;Все файлы (*)",
        )
        if not chosen:
            return _RETRY
        plan.project_path = Path(chosen)
        return plan

    if dialog.action == ACTION_IMPORT:
        from sfstudio.ui.main_window import SUBTITLE_FILTER

        chosen, _ = QFileDialog.getOpenFileName(
            None, "Импорт субтитров", "", SUBTITLE_FILTER
        )
        if not chosen:
            return _RETRY
        from sfstudio.io import registry

        try:
            plan.document = registry.load(Path(chosen))
        except Exception as exc:
            QMessageBox.critical(
                None, "Не удалось открыть", f"{type(exc).__name__}: {exc}"
            )
            return _RETRY
        return plan

    return plan


def _ask_new_project(settings: Settings):
    """Диалог параметров нового проекта. ``None`` — отказались.

    Показывается **до** главного окна: иначе отказ оставлял бы открытым окно
    с демонстрационным документом, которого пользователь не просил.
    """
    from sfstudio.ui.new_project_dialog import NewProjectDialog

    dialog = NewProjectDialog(
        Path(_default_folder(settings)),
        default_font=str(settings.get("subtitles.default_font", "Arial") or "Arial"),
        default_size=float(settings.get("subtitles.default_size", 54.0) or 54.0),
    )
    if dialog.exec() != NewProjectDialog.Accepted:
        return None

    target = dialog.project_path()
    if target is None:
        QMessageBox.warning(None, "Новый проект", "Укажите название и папку.")
        return None

    project = dialog.build()
    media = dialog.media_path()
    if media is not None and media.is_file():
        project.media_path = media
    return project, target


def _default_folder(settings: Settings) -> str:
    folder = str(settings.get("project.folder", "") or "")
    return folder if folder and Path(folder).is_dir() else str(Path.home())
