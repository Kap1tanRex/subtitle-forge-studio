"""Пункты контекстного меню, общие для таймлайна и таблицы реплик.

Меню в двух местах должно быть одним и тем же меню. Разъехавшиеся списки —
частая беда: в таблице «Удалить», на таймлайне «Убрать», где-то есть акторы,
где-то нет, и человек перестаёт понимать, чего ждать от правой кнопки.
Поэтому состав пунктов собран здесь, а виджеты передают только то, чего
модуль знать не может: над чем щёлкнули и как исполнять команды.

Подменю «Акторы» показывает и **незаведённых** — имена, встреченные в поле
``Name`` реплик, которых нет в реестре. Иначе для файла, пришедшего со
стороны, подменю оказалось бы пустым, хотя говорящие в проекте очевидно
есть; у таких имён нет цвета, и это видно по отсутствию квадратика.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QInputDialog, QMenu, QWidget

from sfstudio.core.color import RGBA
from sfstudio.core.commands import (
    AddActor,
    AssignActor,
    Command,
    CompositeCommand,
    SetNote,
    SetStatus,
)
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.workflow import STATUSES
from sfstudio.ui.safe_text import menu_label, plural

__all__ = [
    "actor_submenu", "color_icon", "editable_eids", "note_action",
    "plural_events", "status_submenu",
]

#: Сторона квадратика с цветом актора в меню.
ICON_PX = 12


def color_icon(color: RGBA) -> QIcon:
    """Квадратик цвета для пункта меню."""
    pixmap = QPixmap(ICON_PX, ICON_PX)
    pixmap.fill(QColor(color.r, color.g, color.b))
    return QIcon(pixmap)


def plural_events(count: int) -> str:
    """«реплику» / «2 реплики» / «7 реплик» — для подписи пункта меню."""
    if count == 1:
        return "реплику"  # «1 реплику» звучит как счёт, а не как действие
    return plural(count, "реплику", "реплики", "реплик")


def editable_eids(doc: SubtitleDocument, eids: Sequence[int]) -> list[int]:
    """Оставляет реплики, чью дорожку не заблокировали.

    Замок на дорожке значит «не менять здесь ничего». Таймлайн это уже
    соблюдает — заблокированную реплику нельзя ни подвинуть, ни зацепить
    мышью, — а вот команды правки его не спрашивали: выделение приходит из
    таблицы, где дорожек не видно, и замок обходился незаметно для человека.
    """
    keep: list[int] = []
    for eid in eids:
        event = doc.get(eid)
        if event is None:
            continue
        track = doc.tracks.by_layer(event.layer)
        if track is None or not track.locked:
            keep.append(eid)
    return keep


def status_submenu(
    parent: QWidget | QMenu,
    doc: SubtitleDocument,
    eids: Sequence[int],
    run: Callable[[Command], object],
    *,
    title: str = "Пометка",
) -> QMenu:
    """Подменю рабочего состояния: черновик, готово, вопрос.

    Ставится сразу всей выделенной группе одной командой: состояние
    отмечают пачкой, и двадцать шагов истории за одно действие человека
    сделали бы отмену бесполезной.
    """
    menu = QMenu(title, parent if isinstance(parent, QWidget) else None)
    targets = [eid for eid in eids if doc.has(eid)]
    if not targets:
        menu.setEnabled(False)
        return menu

    current = {doc.by_eid(eid).status for eid in targets}
    for key, label in STATUSES:
        action = menu.addAction(label)
        action.setCheckable(True)
        # Галочка — только когда пометка одна на всю группу: при разнобое
        # любая отметка была бы неправдой.
        action.setChecked(current == {key})
        action.triggered.connect(
            lambda _=False, value=key: run(SetStatus(list(targets), value))
        )
    return menu


def note_action(
    parent,
    doc: SubtitleDocument,
    eid: int,
    run: Callable[[Command], object],
) -> Callable[[], None]:
    """Обработчик пункта «Заметка…» для одной реплики."""

    def edit() -> None:
        event = doc.get(eid)
        if event is None:
            return
        widget = parent if isinstance(parent, QWidget) else None
        text, accepted = QInputDialog.getText(
            widget, "Заметка к реплике",
            "О чём помнить (пусто — убрать заметку):",
            text=event.note,
        )
        if accepted and text.strip() != event.note:
            run(SetNote(eid, text.strip()))

    return edit

def actor_submenu(
    parent: QWidget | QMenu,
    doc: SubtitleDocument,
    eids: Sequence[int],
    run: Callable[[Command], object],
    *,
    actor_command: Callable[[list[int], str], Command] | None = None,
    title: str = "Акторы",
) -> QMenu:
    """Подменю со списком акторов проекта.

    ``run`` исполняет команду (обычно ``UndoStack.run``). ``actor_command``
    строит команду назначения: главное окно подставляет сюда свою, которая
    заодно правит метку говорящего в тексте. Без него берётся простое
    :class:`AssignActor` — модуль остаётся пригодным и в отрыве от окна.
    """
    menu = QMenu(title, parent if isinstance(parent, QWidget) else None)
    targets = [eid for eid in eids if doc.has(eid)]
    if not targets:
        # Пустое меню, открывающееся в никуда, хуже отключённого пункта:
        # по нему непонятно, сломано оно или сказать действительно нечего.
        menu.setEnabled(False)
        return menu

    current = {doc.by_eid(eid).name for eid in targets}

    def assign(name: str) -> None:
        command = (
            actor_command(list(targets), name) if actor_command is not None
            else AssignActor(list(targets), name)
        )
        run(command)

    def add_choice(name: str, icon: QIcon | None) -> None:
        # Имя приходит из файла: амперсанд Qt считает признаком горячей
        # буквы и съедает, поэтому в надписи он удваивается.
        label = menu_label(name)
        action = menu.addAction(label) if icon is None else menu.addAction(icon, label)
        action.setCheckable(True)
        # Галочка — только если говорящий один на все выделенные реплики.
        # При разнобое ни одна отметка не была бы правдой.
        action.setChecked(current == {name})
        action.triggered.connect(lambda _=False, chosen=name: assign(chosen))

    for actor in doc.actors:
        add_choice(actor.name, color_icon(actor.color))

    unregistered = sorted(n for n in doc.actors_in_use() if n not in doc.actors)
    if unregistered:
        menu.addSeparator()
        for name in unregistered:
            add_choice(name, None)

    menu.addSeparator()
    clear = menu.addAction("Без говорящего")
    clear.setEnabled(any(current))
    clear.triggered.connect(lambda: assign(""))

    fresh = menu.addAction("Новый актор…")
    fresh.triggered.connect(
        lambda: _new_actor(parent, doc, targets, run, actor_command)
    )
    return menu


def _new_actor(
    parent,
    doc: SubtitleDocument,
    eids: list[int],
    run: Callable[[Command], object],
    actor_command: Callable[[list[int], str], Command] | None,
) -> None:
    """Заводит актора и сразу назначает его — одной командой отмены.

    Раздельные шаги означали бы, что Ctrl+Z снимает назначение, оставляя в
    реестре актора, которого человек не заводил отдельно.
    """
    widget = parent if isinstance(parent, QWidget) else None
    name, accepted = QInputDialog.getText(widget, "Новый актор", "Имя:")
    name = name.strip()
    if not accepted or not name:
        return

    assign = (
        actor_command(list(eids), name) if actor_command is not None
        else AssignActor(list(eids), name)
    )
    if name in doc.actors:
        run(assign)
        return
    run(CompositeCommand([AddActor(name), assign], label="Новый актор"))
