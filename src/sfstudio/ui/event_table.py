"""Модель таблицы событий.

Обязательно ``QAbstractTableModel`` + ``QTableView``, а не ``QTableWidget``:
последний создаёт объект-виджет на каждую ячейку и на 20 000 строк съедает
сотни мегабайт и секунды на открытие.

Обновление точечное, по ``ChangeSet``: полный ``reset`` сбрасывает позицию
прокрутки и выделение, и на большом файле заметен глазом.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QMenu,
    QStyledItemDelegate,
    QTableView,
    QWidget,
)

from sfstudio.app.i18n import tr
from sfstudio.core import time as timemod
from sfstudio.core.changeset import ChangeSet
from sfstudio.core.commands import DeleteEvents, DuplicateEvents
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.workflow import (
    STATUS_DONE,
    STATUS_DRAFT,
    STATUS_QUESTION,
    status_title,
)
from sfstudio.ui.event_menu import (
    actor_submenu,
    editable_eids,
    note_action,
    plural_events,
    status_submenu,
)
from sfstudio.ui.safe_text import plain_tooltip
from sfstudio.ui.theme import DARK, Palette

COL_INDEX = 0
COL_START = 1
COL_END = 2
COL_DURATION = 3
COL_CPS = 4
COL_STYLE = 5
COL_ACTOR = 6
COL_TEXT = 7
#: Оригинал, с которого идёт перевод. Логически последний, а показывается
#: слева от текста: читают слева направо, и оригинал идёт первым.
COL_REFERENCE = 8
#: Рабочая пометка: черновик, готово, вопрос. Показывается кружком слева,
#: рядом с номером — там же, где взгляд ищет состояние строки.
COL_STATUS = 9

HEADERS = ("#", tr('Начало'), tr('Конец'), tr('Длит.'), "CPS", tr('Стиль'), tr('Актёр'),
    tr('Текст'),
           tr('Оригинал'), tr('Пометка'))

#: Порог CPS, выше которого строка подсвечивается как «слишком быстрая».
CPS_WARNING = 17.0
CPS_DANGER = 25.0

#: Роли Qt числами. Каждое сравнение перечисления Qt с перечислением Qt стоит
#: 1,9 мкс против 0,027 мкс у целых — разница в семьдесят раз, и на отрисовке
#: экрана таблицы она выливалась в 12 мс.
_ROLE_DISPLAY = int(Qt.DisplayRole)
_ROLE_EDIT = int(Qt.EditRole)
_ROLE_ALIGNMENT = int(Qt.TextAlignmentRole)
_ROLE_BACKGROUND = int(Qt.BackgroundRole)
_ROLE_FOREGROUND = int(Qt.ForegroundRole)
_ROLE_DECORATION = int(Qt.DecorationRole)
_ROLE_TOOLTIP = int(Qt.ToolTipRole)

#: Готовое выравнивание и набор колонок под него — тоже считаются один раз.
_ALIGN_RIGHT = int(Qt.AlignRight | Qt.AlignVCenter)
_NUMERIC_COLUMNS = frozenset({COL_INDEX, COL_START, COL_END, COL_DURATION, COL_CPS})

#: Отличает «в кэше лежит None» от «в кэше нет записи»: у актора без цвета
#: ответ тоже None, и без часового он пересчитывался бы каждый раз.
_MISSING = object()


class EventTableModel(QAbstractTableModel):
    def __init__(
        self,
        doc: SubtitleDocument,
        palette: Palette = DARK,
        qc: object | None = None,
        undo: object | None = None,
        actor_command=None,
    ) -> None:
        super().__init__()
        self._doc = doc
        #: Чем назначать говорящего. Окно подставляет сюда свою сборку — ту
        #: же, что работает в панели акторов: иначе метка в тексте
        #: появлялась бы при одном способе назначения и не появлялась при
        #: другом, и человек считал бы это случайностью.
        self._actor_command = actor_command
        #: Стек отмены. Без него таблица только показывает: править документ
        #: в обход истории нельзя никому, включая её саму.
        self._undo = undo
        self._palette = palette
        self._tc_cache: dict[int, str] = {}
        #: Готовые кисти по имени актора. Акторов единицы, реплик тысячи, а
        #: цвет для каждой ячейки строился заново — с разбором hex-строки и
        #: созданием QColor. На видимой таблице это была самая дорогая роль.
        self._actor_cache: dict[tuple[str, bool], QColor | None] = {}
        #: Источник вердиктов QC. Необязателен: таблица работает и без него.
        self._qc = qc
        #: Оригинал для перевода. ``None`` — колонка пуста и скрыта.
        self._reference = None
        #: Текст оригинала по eid. Поиск дешёвый, но колонку спрашивают для
        #: каждой видимой строки при каждой перерисовке — и на каждую роль.
        self._ref_cache: dict[int, str] = {}

    @property
    def document(self) -> SubtitleDocument:
        return self._doc

    @property
    def undo(self):
        return self._undo

    @property
    def actor_command(self):
        return self._actor_command

    @property
    def reference(self):
        return self._reference

    def set_reference(self, track) -> None:
        """Подключает или снимает оригинал. ``None`` — снять."""
        self._reference = track or None
        self._ref_cache.clear()
        if self.rowCount():
            top = self.index(0, COL_REFERENCE)
            bottom = self.index(self.rowCount() - 1, COL_REFERENCE)
            self.dataChanged.emit(top, bottom)

    def reference_for(self, event) -> str:
        """Текст оригинала для реплики. Пусто — оригинала нет или тишина."""
        if self._reference is None:
            return ""
        cached = self._ref_cache.get(event.eid)
        if cached is None:
            cached = self._reference.text_for(event.start, event.end)
            self._ref_cache[event.eid] = cached
        return cached

    def set_palette(self, palette: Palette) -> None:
        """Меняет тему. Кэш кистей сбрасывается: он построен под прежние цвета."""
        self._palette = palette
        self._actor_cache.clear()
        if self.rowCount():
            top = self.index(0, 0)
            bottom = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom)

    # -- обязательные методы модели ------------------------------------------ #

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._doc.events)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return HEADERS[section]

    #: Роли, на которые модель вообще отвечает. Qt спрашивает у ячейки два
    #: десятка ролей — шрифт, подсказку, размер, состояние флажка. Проходить
    #: ради каждой всю цепочку проверок незачем: на видимой таблице это
    #: тысячи лишних сравнений при каждой перерисовке.
    #:
    #: Хранятся **числа**, а не перечисления Qt. Замерено: сравнение двух
    #: Qt-ролей стоит 1,9 мкс, сравнение двух целых — 0,027 мкс, в семьдесят
    #: раз меньше. В ``data`` таких сравнений до восьми на вызов, а вызовов —
    #: по три на ячейку; на экране из сорока строк это выходило 12 мс, то
    #: есть заметный глазу рывок при каждой прокрутке.
    _ROLES = frozenset({
        int(Qt.DisplayRole), int(Qt.TextAlignmentRole), int(Qt.BackgroundRole),
        int(Qt.ForegroundRole), int(Qt.DecorationRole), int(Qt.ToolTipRole),
        int(Qt.EditRole),
    })

    #: Колонки, которые можно править прямо в таблице. Расчётные — длительность
    #: и CPS — сюда не входят: они следуют из таймингов и текста, и «править»
    #: их значило бы гадать, что именно человек хотел изменить.
    EDITABLE = frozenset({COL_START, COL_END, COL_STYLE, COL_ACTOR, COL_TEXT})

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        # Роль сразу приводится к числу: дальше идут сравнения, и на
        # перечислениях Qt каждое из них стоит в семьдесят раз дороже.
        role = int(role)
        if role not in self._ROLES or not index.isValid():
            return None
        row = index.row()
        if row >= len(self._doc.events):
            return None
        event = self._doc.events[row]
        col = index.column()

        if role == _ROLE_DISPLAY:
            return self._display(event, row, col)

        if role == _ROLE_EDIT:
            # В поле правки попадает исходное значение, а не показанное: у
            # текста это строка с разметкой переноса, а не с настоящими
            # переводами строк — иначе правка превратила бы разрыв в пробел.
            return self._edit_value(event, col)

        if role == _ROLE_ALIGNMENT and col in _NUMERIC_COLUMNS:
            return _ALIGN_RIGHT

        if role == _ROLE_BACKGROUND:
            return self._actor_background(event, col)

        if role == _ROLE_FOREGROUND:
            if col == COL_REFERENCE:
                # Приглушённым: это чужой текст, который не правят, и он не
                # должен спорить за внимание с тем, что человек пишет сам.
                return QColor(self._palette.text_muted)
            if event.comment:
                return QColor(self._palette.text_muted)
            if col == COL_ACTOR:
                color = self._doc.actor_color(event)
                if color is not None:
                    return QColor(color.to_hex())
            if col == COL_CPS:
                cps = event.cps()
                if cps >= CPS_DANGER:
                    return QColor(self._palette.danger)
                if cps >= CPS_WARNING:
                    return QColor(self._palette.warning)
            return None

        if role == _ROLE_DECORATION and col == COL_STATUS:
            return self._status_marker(event)

        if role == _ROLE_DECORATION:
            return self._qc_marker(event.eid) if col == COL_INDEX else None

        if role == _ROLE_TOOLTIP:
            if col == COL_INDEX:
                problems = self._qc_messages(event.eid)
                return plain_tooltip("\n".join(problems)) if problems else None
            if col == COL_TEXT:
                # Текст из файла: подсказка не должна принять его за разметку.
                return plain_tooltip(event.plain)
            if col == COL_REFERENCE:
                original = self.reference_for(event)
                return plain_tooltip(original) if original else None
            if col == COL_STATUS:
                return plain_tooltip(self._status_tooltip(event))

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        # Править можно, только когда есть куда записать отмену: без стека
        # правка прошла бы мимо истории, и Ctrl+Z её не вернул бы.
        if self._undo is not None and index.column() in self.EDITABLE:
            return base | Qt.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:  # noqa: N802
        """Применяет правку ячейки через стек отмены.

        Модель не меняет документ сама — только командой. Это то же правило,
        по которому живут все виджеты: правка мышью, правка в инспекторе и
        правка здесь должны одинаково отменяться и одинаково доходить до кадра.
        """
        if role != Qt.EditRole or not index.isValid() or self._undo is None:
            return False
        row, col = index.row(), index.column()
        if row >= len(self._doc.events) or col not in self.EDITABLE:
            return False

        command = self._command_for(self._doc.events[row], col, value)
        if command is None:
            return False
        self._undo.run(command)
        return True

    def _command_for(self, event, col: int, value):
        """Команда, отвечающая правке этой ячейки. ``None`` — менять нечего."""
        from sfstudio.core.commands import (
            ApplyStyleToEvents,
            AssignActor,
            SetText,
            SetTiming,
        )

        text = "" if value is None else str(value)

        if col == COL_TEXT:
            return SetText(event.eid, text) if text != event.text else None

        if col == COL_ACTOR:
            name = text.strip()
            # Поле актора в ASS называется Name — так же оно зовётся и у нас.
            if name == event.name:
                return None
            if self._actor_command is not None:
                return self._actor_command([event.eid], name)
            return AssignActor([event.eid], name)

        if col == COL_STYLE:
            name = text.strip()
            if not name or name == event.style:
                return None
            # Несуществующий стиль сделал бы реплику непохожей на соседей:
            # libass не нашёл бы его и нарисовал умолчанием, а человек решил
            # бы, что оформление слетело само.
            if name not in self._doc.styles:
                return None
            return ApplyStyleToEvents([event.eid], name)

        if col in (COL_START, COL_END):
            parsed = timemod.parse_timecode(text)
            if parsed is None:
                return None
            if col == COL_START:
                return SetTiming(event.eid, start=parsed) if parsed != event.start else None
            return SetTiming(event.eid, end=parsed) if parsed != event.end else None
        return None

    def _edit_value(self, event, col: int):
        """Что показать в поле правки."""
        if col == COL_TEXT:
            return event.text
        if col == COL_ACTOR:
            return event.name
        if col == COL_STYLE:
            return event.style
        if col == COL_START:
            return timemod.format_ass(event.start)
        if col == COL_END:
            return timemod.format_ass(event.end)
        return None

    # -- отображение --------------------------------------------------------- #

    def _display(self, event, row: int, col: int) -> str:
        if col == COL_INDEX:
            return str(row + 1)
        if col == COL_START:
            return self._timecode(event.start)
        if col == COL_END:
            return self._timecode(event.end)
        if col == COL_DURATION:
            return f"{event.duration / 1000:.2f}"
        if col == COL_CPS:
            cps = event.cps()
            return f"{cps:.1f}" if cps else "—"
        if col == COL_STYLE:
            return event.style
        if col == COL_ACTOR:
            return event.name
        if col == COL_TEXT:
            # Одна строка: переводы показываем символом, чтобы не растягивать ряд.
            return event.plain.replace("\n", " ⏎ ")
        if col == COL_REFERENCE:
            return self.reference_for(event).replace("\n", " ⏎ ")
        return ""

    def _actor_background(self, event, col: int) -> QColor | None:
        """Подложка строки цветом актора.

        Цвет сильно разбавлен: он должен опознаваться боковым зрением при
        прокрутке, но не спорить с выделением строки и не мешать читать текст.
        Насыщенным остаётся только сам столбец «Актёр» — там цвет и есть
        содержание ячейки.
        """
        name = event.name
        if not name:
            return None

        key = (name, col == COL_ACTOR)
        cached = self._actor_cache.get(key, _MISSING)
        if cached is not _MISSING:
            return cached

        color = self._doc.actors.color_of(name)
        if color is None:
            self._actor_cache[key] = None
            return None
        tint = QColor(color.to_hex())
        tint.setAlpha(70 if key[1] else 28)
        self._actor_cache[key] = tint
        return tint

    def _status_marker(self, event) -> object | None:
        """Кружок рабочей пометки. Цвет говорит, что со строкой не так."""
        status = getattr(event, "status", "")
        if not status:
            return None
        colours = {
            STATUS_DRAFT: self._palette.text_muted,
            STATUS_DONE: self._palette.success,
            STATUS_QUESTION: self._palette.warning,
        }
        colour = colours.get(status)
        return _dot(QColor(colour)) if colour else None

    def _status_tooltip(self, event) -> str:
        """Подпись пометки и заметка, если она есть."""
        parts = []
        status = getattr(event, "status", "")
        if status:
            parts.append(status_title(status))
        note = getattr(event, "note", "")
        if note:
            parts.append(note)
        return "\n".join(parts)

    def _qc_marker(self, eid: int) -> object | None:
        """Цветная метка серьёзности в колонке номера.

        Цвет, а не только текст: строку с ошибкой надо замечать боковым
        зрением при прокрутке. Подробности — в подсказке и в панели QC.
        """
        if self._qc is None:
            return None
        worst = self._qc.worst_for(eid)
        if worst is None:
            return None
        from sfstudio.services.qc import Severity

        colour = {
            Severity.ERROR: self._palette.danger,
            Severity.WARNING: self._palette.warning,
            Severity.INFO: self._palette.text_muted,
        }[worst]
        return _dot(QColor(colour))

    def _qc_messages(self, eid: int) -> list[str]:
        if self._qc is None:
            return []
        return [str(issue) for issue in self._qc.issues_for(eid)]

    def set_qc(self, qc: object | None) -> None:
        self._qc = qc
        self.refresh_qc()

    def refresh_qc(self) -> None:
        """Перерисовывает колонку номера после пересчёта проверок."""
        if not self._doc.events:
            return
        top = self.index(0, COL_INDEX)
        bottom = self.index(len(self._doc.events) - 1, COL_INDEX)
        self.dataChanged.emit(top, bottom, [Qt.DecorationRole, Qt.ToolTipRole])

    def refresh_colors(self) -> None:
        """Перекрашивает все строки — после смены состава или цветов акторов."""
        # Кэш цветов теперь врёт: актора могли переименовать или перекрасить.
        self._actor_cache.clear()
        if not self._doc.events:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._doc.events) - 1, len(HEADERS) - 1),
            [Qt.BackgroundRole, Qt.ForegroundRole, Qt.DisplayRole],
        )

    def _timecode(self, ms: int) -> str:
        """Форматирование таймкодов заметно в профиле — поэтому с кэшем."""
        cached = self._tc_cache.get(ms)
        if cached is None:
            cached = timemod.format_ass(ms)
            if len(self._tc_cache) > 20_000:
                self._tc_cache.clear()
            self._tc_cache[ms] = cached
        return cached

    # -- реакция на изменения ------------------------------------------------- #

    def apply_changes(self, changes: ChangeSet) -> None:
        """Точечное обновление вместо полного reset."""
        if changes.structural or changes.styles_changed:
            self.beginResetModel()
            self.endResetModel()
            return

        if changes.actors_changed:
            # Цвет актора красит строку целиком, и смена цвета затрагивает все
            # реплики этого говорящего, а не только те, где менялось имя.
            # Точечный список тут не построить дёшево — перекрашиваем всё.
            self.refresh_colors()
            if not changes.changed_eids:
                return

        if not changes.changed_eids:
            return

        # Тайминги могли сдвинуться — значит оригинал под репликой уже другой.
        for eid in changes.changed_eids:
            self._ref_cache.pop(eid, None)

        rows = [
            i for i, event in enumerate(self._doc.events) if event.eid in changes.changed_eids
        ]
        if not rows:
            return
        # Один сигнал на непрерывный диапазон — так дешевле, чем построчно.
        top, bottom = min(rows), max(rows)
        self.dataChanged.emit(
            self.index(top, 0), self.index(bottom, len(HEADERS) - 1)
        )

    def reset_document(self, doc: SubtitleDocument) -> None:
        self.beginResetModel()
        self._doc = doc
        self._tc_cache.clear()
        self._actor_cache.clear()
        self._ref_cache.clear()
        self.endResetModel()

    def row_of_eid(self, eid: int) -> int:
        for i, event in enumerate(self._doc.events):
            if event.eid == eid:
                return i
        return -1

    def eid_at_row(self, row: int) -> int | None:
        if 0 <= row < len(self._doc.events):
            return self._doc.events[row].eid
        return None


def _dot(colour: QColor) -> QIcon:
    """Кружок заданного цвета. Кэшируется: иконок всего три на всю таблицу."""
    cached = _DOT_CACHE.get(colour.name())
    if cached is not None:
        return cached
    pixmap = QPixmap(10, 10)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(colour)
    painter.drawEllipse(1, 1, 8, 8)
    painter.end()
    icon = QIcon(pixmap)
    _DOT_CACHE[colour.name()] = icon
    return icon


_DOT_CACHE: dict[str, QIcon] = {}


#: Ширины колонок по умолчанию. Компактнее прежних: список реплик — главное
#: рабочее место, и каждый лишний пиксель в служебной колонке отнят у текста.
COLUMN_WIDTHS = {
    COL_INDEX: 40,
    COL_STATUS: 30,
    COL_START: 78,
    COL_END: 78,
    COL_DURATION: 54,
    COL_CPS: 42,
    COL_STYLE: 84,
    COL_ACTOR: 76,
}

#: Порядок, в котором колонки уступают место при нехватке ширины. Первой
#: уходит та, без которой обходятся чаще всего. «Начало» и «Текст» не
#: скрываются никогда: без них таблица перестаёт быть списком реплик.
HIDE_ORDER = (COL_STYLE, COL_DURATION, COL_CPS, COL_STATUS, COL_INDEX,
              COL_ACTOR, COL_END)

#: Сколько оставить тексту, чтобы в нём читалась хотя бы фраза.
MIN_TEXT_W = 150


class EventTableView(QTableView):
    """Таблица реплик, сама подстраивающаяся под отведённую ей ширину.

    Колонок восемь, и в узкой панели они не помещаются: появлялась
    горизонтальная прокрутка, текст реплики уезжал за край, и главный
    инструмент работы превращался в неудобный. Поэтому при нехватке места
    служебные колонки уступают её тексту — по одной, в порядке
    :data:`HIDE_ORDER`, и возвращаются, когда место снова есть.

    Решение программы не окончательное: правый щелчок по шапке открывает
    список колонок. Колонка, которой человек распорядился сам, из
    автоматического подбора выходит — своё решение важнее нашего.
    """

    #: Просьба создать реплику: где именно, знает главное окно — оно ставит
    #: её после текущей, а таблице такие подробности не нужны.
    insert_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: Колонки, о которых пользователь высказался явно.
        self._pinned: dict[int, bool] = {}

        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setWordWrap(False)

        vertical = self.verticalHeader()
        vertical.setVisible(False)
        vertical.setDefaultSectionSize(22)
        vertical.setMinimumSectionSize(16)

        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(COL_TEXT, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_REFERENCE, QHeaderView.Stretch)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._column_menu)
        for column, width in COLUMN_WIDTHS.items():
            self.setColumnWidth(column, width)

    def setModel(self, model) -> None:  # noqa: N802
        """Ставит модель и заново раскладывает колонки.

        Порядок и скрытие приходится задавать здесь, а не в конструкторе:
        до появления модели колонок ещё нет, и всё, что им назначили,
        пропадает молча — колонка оригинала оставалась видимой и стояла
        последней.
        """
        super().setModel(model)
        if model is None:
            return

        header = self.horizontalHeader()
        # Оригинал показывается слева от перевода: читают слева направо, и
        # смотреть на исходник после результата неудобно. Логический номер
        # при этом остаётся последним — так новая колонка ничего не сдвинула.
        header.moveSection(
            header.visualIndex(COL_REFERENCE), header.visualIndex(COL_TEXT)
        )
        self.show_reference(bool(getattr(model, "reference", None)))

    def show_reference(self, on: bool) -> None:
        """Показывает или прячет колонку оригинала.

        Прячется именно колонка, а не её содержимое: пустой столбец на
        четверть ширины таблицы отнимал бы место у текста без всякой пользы.
        """
        self.setColumnHidden(COL_REFERENCE, not on)
        self.adapt_columns()

    # -- подбор колонок под ширину ----------------------------------------- #

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.adapt_columns()

    def adapt_columns(self) -> None:
        """Прячет служебные колонки, пока тексту не хватает места."""
        available = self.viewport().width()
        if available <= 0:
            return

        # С оригиналом текст делит место пополам, и служебным колонкам надо
        # уступить раньше: иначе обе текстовые превращаются в полоски.
        needed = MIN_TEXT_W * (2 if not self.isColumnHidden(COL_REFERENCE) else 1)
        visible = [c for c in HIDE_ORDER if self._pinned.get(c, True)]
        # Считаем от полного набора и убираем по одной, а не наоборот:
        # иначе колонка, спрятанная при сужении, не вернулась бы при
        # расширении — ширины-то у скрытой колонки уже нет.
        used = sum(COLUMN_WIDTHS.get(c, 0) for c in visible)
        hidden: set[int] = set()
        for column in HIDE_ORDER:
            if available - used >= needed:
                break
            if column not in visible:
                continue
            hidden.add(column)
            used -= COLUMN_WIDTHS.get(column, 0)

        for column in HIDE_ORDER:
            if column in self._pinned:
                self.setColumnHidden(column, not self._pinned[column])
            else:
                self.setColumnHidden(column, column in hidden)

    def _column_menu(self, point) -> None:
        """Список колонок по правому щелчку на шапке."""
        menu = QMenu(self)
        for column, title in enumerate(HEADERS):
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(column))
            # «Начало» и «Текст» не выключаются: без них таблица бессмысленна.
            action.setEnabled(column not in (COL_START, COL_TEXT))
            action.triggered.connect(
                lambda checked, c=column: self._set_column(c, checked)
            )
        menu.addSeparator()
        reset = menu.addAction(tr('Подбирать автоматически'))
        reset.triggered.connect(self._unpin_all)
        menu.exec(self.horizontalHeader().mapToGlobal(point))

    def _set_column(self, column: int, visible: bool) -> None:
        self._pinned[column] = visible
        self.setColumnHidden(column, not visible)
        if visible and self.columnWidth(column) == 0:
            self.setColumnWidth(column, COLUMN_WIDTHS.get(column, 60))

    def _unpin_all(self) -> None:
        self._pinned.clear()
        self.adapt_columns()

    # -- меню по правому щелчку на строках ---------------------------------- #

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = self.context_menu(event.pos())
        if menu is not None:
            menu.exec(event.globalPos())

    def context_menu(self, position) -> QMenu | None:
        """Те же действия над репликами, что и на таймлайне.

        Правый щелчок по невыделенной строке сначала выделяет её: иначе
        «Удалить 5 реплик» относилось бы к строкам, на которые человек в этот
        момент не показывает.

        Отдельно от :meth:`contextMenuEvent`: тот показывает меню модально,
        и состав пунктов иначе не проверить.
        """
        model = self.model()
        doc = getattr(model, "document", None)
        undo = getattr(model, "undo", None)
        if doc is None or undo is None:
            return None

        index = self.indexAt(position)
        if index.isValid() and not self.selectionModel().isSelected(index):
            self.selectRow(index.row())

        eids = editable_eids(doc, self.selected_eids())
        menu = QMenu(self)

        def run(command) -> None:
            undo.run(command)

        insert = menu.addAction(tr('Новая реплика'))
        insert.triggered.connect(lambda: self.insert_requested.emit())

        if eids:
            menu.addSeparator()
            menu.addMenu(
                actor_submenu(
                    self, doc, eids, run,
                    actor_command=getattr(model, "actor_command", None),
                )
            )
            menu.addMenu(status_submenu(self, doc, eids, run))
            note = menu.addAction(tr('Заметка…'))
            note.setEnabled(len(eids) == 1)
            if len(eids) == 1:
                note.triggered.connect(note_action(self, doc, eids[0], run))

            menu.addSeparator()
            what = plural_events(len(eids))
            duplicate = menu.addAction(tr('Дублировать {0}').format(what))
            duplicate.triggered.connect(lambda: run(DuplicateEvents(list(eids))))
            delete = menu.addAction(tr('Удалить {0}').format(what))
            delete.setShortcut("Del")
            delete.triggered.connect(lambda: run(DeleteEvents(list(eids))))

        return menu

    def selected_eids(self) -> list[int]:
        """Выделенные реплики в порядке документа."""
        model = self.model()
        if model is None:
            return []
        rows = sorted(i.row() for i in self.selectionModel().selectedRows())
        eids = [model.eid_at_row(row) for row in rows]
        return [eid for eid in eids if eid is not None]


class ChoiceDelegate(QStyledItemDelegate):
    """Выпадающий список вместо поля ввода — для стиля и говорящего.

    Оба значения выбираются из уже существующих, и набирать их руками значит
    ошибаться в написании: стиль с опечаткой не применится, актор с опечаткой
    заведёт нового. Список при этом остаётся редактируемым — новое имя
    говорящего вводят прямо здесь, это обычный случай при разметке диалога.
    """

    def __init__(self, options, parent=None, *, editable: bool = True) -> None:
        super().__init__(parent)
        #: Функция, а не готовый список: стили и акторы заводят по ходу
        #: работы, и список, снятый при создании делегата, устареет к первому
        #: же открытию редактора.
        self._options = options
        self._editable = editable

    def createEditor(self, parent, _option, _index):  # noqa: N802
        box = QComboBox(parent)
        box.setEditable(self._editable)
        box.addItems(list(self._options()))
        return box

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        current = str(index.data(Qt.EditRole) or "")
        position = editor.findText(current)
        if position >= 0:
            editor.setCurrentIndex(position)
        elif editor.isEditable():
            editor.setEditText(current)

    def setModelData(self, editor, model, index) -> None:  # noqa: N802
        model.setData(index, editor.currentText(), Qt.EditRole)
