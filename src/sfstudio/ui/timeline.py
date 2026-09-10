"""Таймлайн: дорожки субтитров над видео и звуком, правка таймингов мышью.

Компоновка как в монтажном столе:

* слева — колонка заголовков дорожек с именем, цветом, «глазом» и замком;
* сверху — шкала времени;
* дальше сверху вниз: дорожки субтитров (чем больше слой, тем выше), под ними
  видео с ключевыми кадрами, в самом низу звук с волной.

**Дорожка субтитров — это слой ASS.** Почему именно так, подробно объяснено в
:mod:`sfstudio.core.tracks`; здесь важно следствие: перетащить реплику на
соседнюю дорожку значит поменять её ``layer``, и это честно отразится на
наложении в кадре.

Подстроки. На одной дорожке реплики могут пересекаться по времени — в ASS это
законно и встречается постоянно. Рисовать их друг поверх друга нельзя, поэтому
пересекающиеся раскладываются по подстрокам внутри полосы дорожки. Полоса не
растёт: она делится. Так видно и то, что реплики наложены, и то, что они на
одной дорожке.

Производительность. Волна рисуется **одним полигоном-огибающей** на срез, а не
набором линий: для ширины 1920 это один вызов ``drawPolygon`` вместо почти двух
тысяч ``drawLine``. Данные приходят из пирамиды пиков уже свёрнутыми до ширины
виджета, поэтому длина файла на стоимость отрисовки не влияет — двухчасовой
фильм рисуется столько же, сколько трёхсекундный.

Работа без данных. Нет аудиодорожки или пики ещё не построены — таймлайн рисует
ровную линию и подпись о причине, а правка таймингов продолжает работать.
Отсутствие звука это не ошибка открытия файла.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QInputDialog, QMenu, QToolTip, QWidget

from sfstudio.core.commands import (
    AddTrack,
    CompositeCommand,
    DeleteEvents,
    DuplicateEvents,
    InsertEvent,
    MoveEventsToLayer,
    RemoveTrack,
    SetText,
    SetTiming,
    SetTrackFlags,
    UpdateTrack,
)
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.time import FpsModel, SnapMode, format_srt
from sfstudio.core.tracks import Track, TrackKind
from sfstudio.core.undo import UndoStack
from sfstudio.media.keyframes import KeyframeIndex, SnapContext, snap_time
from sfstudio.ui.event_menu import (
    actor_submenu,
    editable_eids,
    note_action,
    plural_events,
    status_submenu,
)
from sfstudio.ui.safe_text import menu_label, plain_tooltip
from sfstudio.ui.theme import DARK, Palette

#: Ширина колонки заголовков. Влезает «Субтитры 10» и две иконки справа.
HEADER_W = 148.0
RULER_H = 22.0
SUB_TRACK_H = 44.0
#: Пределы высоты одной дорожки при растягивании мышью.
MIN_TRACK_H = 18.0
MAX_TRACK_H = 240.0
#: Ширина полосы у границы дорожки, за которую её можно потянуть.
#: Четырёх пикселей человеку мало: в них надо попасть, а промах
#: означает не «мимо», а другое действие — щелчок по дорожке.
RESIZE_GRAB_PX = 6.0
#: Пределы общего масштаба высоты дорожек.
MIN_TRACK_ZOOM = 0.5
MAX_TRACK_ZOOM = 4.0
VIDEO_TRACK_H = 26.0
MIN_AUDIO_H = 44.0
#: Сколько подстрок помещаем в дорожку, прежде чем перестать дробить полосу.
MAX_SUBLANES = 4
EDGE_GRAB_PX = 6.0
MIN_EVENT_MS = 100
#: Длительность новой реплики. Нулевая была бы невидима на
#: таймлайне, и создание выглядело бы как «ничего не произошло».
NEW_EVENT_MS = 2000
#: Зазор при сдвиге за уже занятое место.
MIN_GAP_MS = 40
#: Ширина, ниже которой у реплики не рисуются грани: они всё равно
#: сливаются с заливкой, а стоят двух вызовов рисования из трёх.
_THIN_EVENT_PX = 3.0

#: Шаги сетки времени в миллисекундах, от мелкого к крупному.
_GRID_STEPS = (
    10, 20, 50, 100, 200, 500,
    1_000, 2_000, 5_000, 10_000, 15_000, 30_000,
    60_000, 120_000, 300_000, 600_000, 900_000, 1_800_000, 3_600_000,
)


#: Признак «в кэше ещё не считали». ``None`` занят и значит «актора нет».
_MISSING = object()


def _readable_on(background: QColor) -> QColor:
    """Чёрный или белый — смотря что читается на этом фоне.

    Порог по воспринимаемой яркости, а не по среднему из трёх каналов:
    глаз чувствителен к зелёному вчетверо сильнее, чем к синему, и по
    среднему белый текст оказывался бы на жёлтом фоне.
    """
    luminance = (0.299 * background.red()
                 + 0.587 * background.green()
                 + 0.114 * background.blue())
    return QColor(20, 20, 20) if luminance > 140 else QColor(245, 245, 245)


#: Скорость чтения, знаков в секунду. Норма для субтитров — от 15 до 17;
#: берём нижнюю границу, чтобы вставленный текст сразу не помечался слишком
#: быстрым.
_READING_CPS = 15.0


def _reading_time(text: str) -> int:
    """Сколько времени нужно, чтобы прочитать этот текст."""
    from sfstudio.core.tags import plain_text

    # Разметка переноса в ASS — два символа, обратный слэш и N; в строке
    # Python его надо удвоить, иначе получится начало юникод-имени.
    visible = plain_text(text).replace("\\N", " ")
    needed = int(len(visible) / _READING_CPS * 1000)
    return max(NEW_EVENT_MS, needed)


class DragMode:
    NONE = 0
    SEEK = 1
    START = 2
    END = 3
    BODY = 4
    CREATE = 5
    RESIZE_TRACK = 6
    RUBBER = 7


@dataclass(slots=True)
class _Drag:
    mode: int = DragMode.NONE
    eid: int | None = None
    grab_offset_ms: int = 0
    origin_ms: int = 0
    original: tuple[int, int] = (0, 0)
    #: Слой, на котором реплика была в начале жеста — чтобы вернуть при отмене.
    origin_layer: int = 0
    #: Растягиваемая дорожка, её высота на экране и значение поля до жеста.
    #: Значение поля хранится отдельно от экранной высоты: ноль означает
    #: «высота по умолчанию», и пересчёт из пикселей потерял бы этот признак —
    #: отмена вернула бы явное число вместо «как было».
    resize_layer: int = -1
    origin_height: float = 0.0
    origin_raw_height: int = 0
    origin_y: float = 0.0
    #: Точка начала рамки выделения и её текущий угол, в пикселях.
    origin_point: tuple[float, float] = (0.0, 0.0)
    current_point: tuple[float, float] = (0.0, 0.0)
    #: Выделение до начала рамки: Shift добавляет к нему, а не заменяет.
    base_selection: frozenset[int] = frozenset()
    #: Сдвиг группы: сколько уже применено к каждой реплике.
    group: tuple[int, ...] = ()


@dataclass(slots=True)
class _Row:
    """Полоса на экране: дорожка и занятая ею вертикаль."""

    track: Track
    top: float
    height: float

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def contains(self, y: float) -> bool:
        return self.top <= y < self.bottom


class TimelineWidget(QWidget):
    """Дорожки, волна и правка таймингов."""

    time_changed = Signal(int)
    selection_changed = Signal(int)
    document_edited = Signal()
    status_message = Signal(str)
    #: Изменился состав или свойства дорожек — окну нужно обновить меню.
    tracks_changed = Signal()

    def __init__(
        self,
        doc: SubtitleDocument,
        undo: UndoStack,
        palette: Palette = DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._doc = doc
        self._undo = undo
        self._palette = palette

        self._peaks: object | None = None
        self._peaks_note = "аудио не загружено"
        self._keyframes: KeyframeIndex | None = None
        self._fps: FpsModel | None = None
        self._media_name = ""

        self._view_start_ms = 0.0
        self._px_per_ms = 0.05  # 20 секунд на 1000 px
        self._gain = 1.0
        self._time_ms = 0
        #: Ведущая реплика: та, чьи свойства показывает инспектор. Остаётся
        #: отдельно от множества — «выделено двенадцать» и «правим вот эту»
        #: это разные вопросы, и панелям нужен ответ на второй.
        self._selected: int | None = None
        #: Все выделенные. Групповые операции работают по нему.
        self._selection: set[int] = set()

        self._drag = _Drag()
        #: Общий масштаб высоты дорожек. Настройка вида, а не документа:
        #: «приблизить дорожки» не должно попадать в файл субтитров и
        #: засорять историю отмены.
        self._track_zoom = 1.0
        self._snap_enabled = True
        self._last_snap_kind = ""
        #: eid → подстрока внутри своей дорожки.
        self._sublane_of: dict[int, int] = {}
        self._sublanes_on: dict[int, int] = {}
        self._layout_revision = -1
        #: Где последний раз была мышь над дорожками, в миллисекундах.
        #: None — курсор ни разу не заходил или ушёл за пределы дорожек.
        self._mouse_ms: int | None = None
        self._mouse_layer: int | None = None
        #: Чем назначать говорящего. Главное окно подставляет свою функцию,
        #: которая заодно правит метку в тексте реплики; без неё берётся
        #: обычное назначение — таймлайн должен работать и сам по себе.
        self.actor_command = None

        # Минимум — шкала, одна дорожка субтитров и видеополоса. Звук в него
        # не входит: с ним таймлайн требовал 136 пикселей высоты, и в
        # сжатом окне это отнималось у списка реплик.
        self.setMinimumHeight(int(RULER_H + MIN_TRACK_H + VIDEO_TRACK_H))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

    def set_palette(self, palette: Palette) -> None:
        """Меняет тему на лету.

        Без этого смена темы требовала перезапуска: таблица стилей Qt
        применяется сразу, а всё нарисованное вручную — таймлайн, волна,
        кадр — остаётся в прежних цветах. Половина окна светлая, половина
        тёмная выглядит не как настройка, а как поломка.
        """
        self._palette = palette
        self.update()

    # -- внешнее API --------------------------------------------------------- #

    def set_document(self, doc: SubtitleDocument, undo: UndoStack) -> None:
        self._doc = doc
        self._undo = undo
        self._selected = None
        self._layout_revision = -1
        self.update()

    def set_peaks(self, peaks: object | None, note: str = "") -> None:
        self._peaks = peaks
        self._peaks_note = note
        self.update()

    def set_keyframes(self, index: KeyframeIndex | None) -> None:
        self._keyframes = index
        self.update()

    def set_fps(self, fps: FpsModel | None) -> None:
        self._fps = fps

    def set_media_name(self, name: str) -> None:
        """Имя открытого файла — подпись на видеодорожке."""
        self._media_name = name
        self.update()

    def set_time(self, ms: int, *, follow: bool = True) -> None:
        if ms == self._time_ms:
            return
        self._time_ms = max(0, ms)
        if follow:
            self._ensure_visible(self._time_ms)
        self.update()

    def set_selected(self, eid: int | None) -> None:
        """Выделяет одну реплику, снимая прочее выделение.

        Приходит извне — из таблицы или инспектора, где выбрана одна строка.
        Групповое выделение при этом сбрасывается намеренно: показывать на
        таймлайне двенадцать выделенных, когда в таблице выбрана одна, значит
        врать о том, что затронет следующая команда.
        """
        if eid == self._selected and self._selection <= {eid}:
            return
        self._selected = eid
        self._selection = {eid} if eid is not None else set()
        self.update()

    def set_selection(self, eids: Iterable[int]) -> None:
        """Выделяет несколько реплик разом — например, вслед за таблицей."""
        chosen = {eid for eid in eids if self._doc.has(eid)}
        if chosen == self._selection:
            return
        self._selection = chosen
        if self._selected not in chosen:
            self._selected = next(iter(chosen), None)
        self.update()

    @property
    def selected_eids(self) -> list[int]:
        """Выделенные реплики в порядке документа — так их и ждут команды."""
        if not self._selection:
            return []
        return [e.eid for e in self._doc.events if e.eid in self._selection]

    def _apply_selection(self, eids: set[int], leader: int | None) -> None:
        """Меняет выделение и сообщает о ведущей реплике."""
        self._selection = eids
        self._selected = leader if leader in eids else next(iter(eids), None)
        self.selection_changed.emit(
            self._selected if self._selected is not None else -1
        )

    def set_snapping(self, on: bool) -> None:
        self._snap_enabled = on

    @property
    def time_ms(self) -> int:
        return self._time_ms

    # -- преобразование координат --------------------------------------------- #

    def ms_to_x(self, ms: float) -> float:
        return HEADER_W + (ms - self._view_start_ms) * self._px_per_ms

    def x_to_ms(self, x: float) -> float:
        return self._view_start_ms + (x - HEADER_W) / self._px_per_ms

    @property
    def _content_w(self) -> float:
        """Ширина области дорожек — без колонки заголовков."""
        return max(1.0, self.width() - HEADER_W)

    @property
    def view_end_ms(self) -> float:
        return self._view_start_ms + self._content_w / self._px_per_ms

    @property
    def content_duration(self) -> int:
        doc_end = self._doc.duration
        peaks = self._peaks
        media_end = int(getattr(peaks, "duration_ms", 0) or 0)
        return max(doc_end, media_end, 1000)

    # -- геометрия полос -------------------------------------------------------- #

    def rows(self) -> list[_Row]:
        """Полосы сверху вниз. Звук забирает остаток высоты.

        Остаток именно звуку: волна — единственное, что выигрывает от каждого
        лишнего пикселя, а полосы субтитров при росте только пустеют.
        """
        tracks = self._doc.tracks
        subtitle_rows = tracks.display_order(with_media=False)

        rows: list[_Row] = []
        y = RULER_H
        for track in subtitle_rows:
            rows.append(_Row(track, y, self.track_height(track)))
            y += rows[-1].height

        video_h = VIDEO_TRACK_H * self._track_zoom
        rows.append(_Row(tracks.video, y, video_h))
        y += video_h

        # Остаток, но не в минус: при сжатом окне звуку места не остаётся, и
        # прежний жёсткий минимум просто выталкивал волну за нижний край —
        # вместе с ней уезжали и дорожки субтитров. Лучше узкая полоса звука,
        # чем невидимые реплики: правят их, а не волну.
        audio_h = max(0.0, self.height() - y)
        rows.append(_Row(tracks.audio, y, audio_h))
        return rows

    def _row_at(self, y: float) -> _Row | None:
        return next((row for row in self.rows() if row.contains(y)), None)

    def _row_for_layer(self, layer: int) -> _Row | None:
        return next(
            (r for r in self.rows() if r.track.is_subtitle and r.track.layer == layer),
            None,
        )

    def track_height(self, track: Track) -> float:
        """Высота дорожки: собственная либо стандартная, умноженная на масштаб."""
        base = float(track.height) if track.height else SUB_TRACK_H
        return max(MIN_TRACK_H, base * self._track_zoom)

    def preferred_height(self) -> int:
        subs = sum(self.track_height(t) for t in self._doc.tracks.subtitles)
        return int(RULER_H + subs + VIDEO_TRACK_H * self._track_zoom + MIN_AUDIO_H + 30)

    # -- масштаб дорожек по вертикали ---------------------------------------------- #

    @property
    def track_zoom(self) -> float:
        return self._track_zoom

    def zoom_tracks(self, factor: float) -> None:
        """Приближает или отдаляет дорожки по вертикали."""
        self._track_zoom = max(MIN_TRACK_ZOOM, min(MAX_TRACK_ZOOM,
                                                   self._track_zoom * factor))
        self.updateGeometry()
        self.update()

    def reset_track_zoom(self) -> None:
        self._track_zoom = 1.0
        self.updateGeometry()
        self.update()

    def _resize_edge_at(self, x: float, y: float) -> int | None:
        """Слой, чью нижнюю границу можно потянуть в этой точке.

        В колонке заголовков — всегда. В области дорожек — везде, где нет
        реплики: у самой реплики тот же жест правит её границы, и отбирать
        его нельзя. Но пустого места на дорожке обычно больше, чем занятого,
        и гонять мышь в узкую колонку слева ради высоты — работа на ровном
        месте. Раньше только колонка и работала, отчего казалось, что высота
        мышью не меняется вовсе.
        """
        if y < RULER_H:
            return None
        for row in self.rows():
            if not row.track.is_subtitle:
                continue
            if abs(y - row.bottom) > RESIZE_GRAB_PX:
                continue
            if x >= HEADER_W and self._event_at(x, y) is not None:
                return None
            return row.track.layer
        return None

    # -- зум и прокрутка --------------------------------------------------------- #

    def zoom_at(self, factor: float, anchor_x: float) -> None:
        """Масштабирует, удерживая точку под курсором на месте."""
        anchor_x = max(anchor_x, HEADER_W)
        anchor_ms = self.x_to_ms(anchor_x)
        new_scale = self._px_per_ms * factor
        min_scale = max(self._content_w / max(self.content_duration * 1.2, 1000), 1e-6)
        self._px_per_ms = max(min_scale, min(new_scale, 40.0))
        self._view_start_ms = anchor_ms - (anchor_x - HEADER_W) / self._px_per_ms
        self._clamp_view()
        self.update()

    def scroll_by(self, delta_px: float) -> None:
        self._view_start_ms += delta_px / self._px_per_ms
        self._clamp_view()
        self.update()

    def fit_all(self) -> None:
        self._view_start_ms = 0.0
        self._px_per_ms = max(self._content_w / max(self.content_duration * 1.05, 1000), 1e-6)
        self.update()

    def fit_selection(self) -> None:
        if self._selected is None or not self._doc.has(self._selected):
            self.fit_all()
            return
        event = self._doc.by_eid(self._selected)
        pad = max(500, event.duration // 2)
        span = max(event.duration + pad * 2, 200)
        self._px_per_ms = max(self._content_w / span, 1e-6)
        self._view_start_ms = event.start - pad
        self._clamp_view()
        self.update()

    def _clamp_view(self) -> None:
        limit = self.content_duration + 2000
        max_start = max(0.0, limit - self._content_w / self._px_per_ms)
        self._view_start_ms = max(0.0, min(self._view_start_ms, max_start))

    def _ensure_visible(self, ms: int) -> None:
        span = self._content_w / self._px_per_ms
        if ms < self._view_start_ms or ms > self._view_start_ms + span * 0.9:
            self._view_start_ms = ms - span * 0.4
            self._clamp_view()

    # -- отрисовка ------------------------------------------------------------ #

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(self._palette.bg_sunken))

        self._rebuild_sublanes_if_needed()
        rows = self.rows()

        self._paint_row_backgrounds(painter, rows)
        self._paint_ruler(painter)
        for row in rows:
            if row.track.kind is TrackKind.AUDIO:
                self._paint_wave(painter, row)
            elif row.track.kind is TrackKind.VIDEO:
                self._paint_video(painter, row)
        self._paint_events(painter, rows)
        self._paint_rubber(painter)
        self._paint_headers(painter, rows)
        self._paint_playhead(painter)
        painter.end()

    def _paint_row_backgrounds(self, painter: QPainter, rows: list[_Row]) -> None:
        for index, row in enumerate(rows):
            area = QRectF(HEADER_W, row.top, self._content_w, row.height)
            shade = self._palette.bg_base if index % 2 == 0 else self._palette.bg_sunken
            painter.fillRect(area, QColor(shade))
            painter.setPen(QPen(QColor(self._palette.border), 1))
            painter.drawLine(QPointF(HEADER_W, row.bottom), QPointF(self.width(), row.bottom))

    def _paint_ruler(self, painter: QPainter) -> None:
        rect = QRectF(0, 0, self.width(), RULER_H)
        painter.fillRect(rect, QColor(self._palette.bg_elevated))
        painter.setPen(QPen(QColor(self._palette.border), 1))
        painter.drawLine(QPointF(0, RULER_H), QPointF(self.width(), RULER_H))

        step = self._grid_step()
        font = QFont("Segoe UI")
        font.setPixelSize(10)
        painter.setFont(font)

        start = int(self._view_start_ms // step) * step
        t = start
        end = self.view_end_ms
        while t <= end:
            x = self.ms_to_x(t)
            if x >= HEADER_W - 60:
                painter.setPen(QPen(QColor(self._palette.border), 1))
                painter.drawLine(QPointF(x, RULER_H - 6), QPointF(x, RULER_H))
                painter.setPen(QColor(self._palette.text_muted))
                painter.drawText(QPointF(x + 3, RULER_H - 8), _short_time(t))
                painter.setPen(QPen(QColor(self._palette.border), 1, Qt.DotLine))
                painter.drawLine(QPointF(x, RULER_H), QPointF(x, self.height()))
            t += step

    def _grid_step(self) -> int:
        """Шаг сетки, при котором подписи не наезжают друг на друга."""
        min_px = 70.0
        for step in _GRID_STEPS:
            if step * self._px_per_ms >= min_px:
                return step
        return _GRID_STEPS[-1]

    def _paint_video(self, painter: QPainter, row: _Row) -> None:
        area = QRectF(HEADER_W, row.top + 2, self._content_w, row.height - 4)
        painter.fillRect(area, QColor(self._palette.accent_muted))

        if self._keyframes:
            painter.setPen(QPen(QColor(self._palette.accent), 1))
            for t in self._keyframes.in_range(int(self._view_start_ms), int(self.view_end_ms)):
                x = self.ms_to_x(t)
                painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))

        if self._media_name:
            font = QFont("Segoe UI")
            font.setPixelSize(10)
            painter.setFont(font)
            painter.setPen(QColor(self._palette.text_primary))
            painter.drawText(area.adjusted(6, 0, -6, 0),
                             Qt.AlignVCenter | Qt.AlignLeft, self._media_name)

    def _paint_wave(self, painter: QPainter, row: _Row) -> None:
        top = row.top + 2
        height = row.height - 4
        if height <= 4:
            return

        area = QRectF(HEADER_W, top, self._content_w, height)
        mid = top + height / 2

        if self._peaks is None or row.track.muted:
            painter.setPen(QPen(QColor(self._palette.border), 1))
            painter.drawLine(QPointF(HEADER_W, mid), QPointF(self.width(), mid))
            painter.setPen(QColor(self._palette.text_muted))
            font = QFont("Segoe UI")
            font.setPixelSize(11)
            painter.setFont(font)
            note = "звук отключён" if row.track.muted else self._peaks_note
            painter.drawText(area.adjusted(10, 0, -10, 0), Qt.AlignCenter, note)
            return

        width = max(1, int(self._content_w))
        try:
            mins, maxs, rms = self._peaks.slice(  # type: ignore[union-attr]
                self._view_start_ms, self.view_end_ms, width
            )
        except Exception:  # повреждённый кэш не должен ронять окно
            painter.setPen(QColor(self._palette.danger))
            painter.drawText(area, Qt.AlignCenter, "не удалось прочитать пики")
            return

        half = height / 2 * 0.92 * self._gain
        scale = half / 32768.0

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._palette.wave_fill))
        painter.drawPolygon(_envelope(maxs, mins, mid, scale, HEADER_W))

        painter.setBrush(QColor(self._palette.wave_rms))
        painter.drawPolygon(_envelope(rms, -rms.astype("int32"), mid, scale, HEADER_W))

    def _paint_events(self, painter: QPainter, rows: list[_Row]) -> None:
        t0 = int(self._view_start_ms)
        t1 = int(self.view_end_ms)
        font = QFont("Segoe UI")
        font.setPixelSize(10)
        painter.setFont(font)

        by_layer = {r.track.layer: r for r in rows if r.track.is_subtitle}

        # Цвета готовим один раз на кадр. Раньше QColor и to_hex() считались
        # для каждой реплики: на документе в три тысячи строк это тридцать
        # тысяч разборов строки за перерисовку — треть времени отрисовки.
        track_colors: dict[int, QColor] = {}
        for layer, row in by_layer.items():
            color = QColor(row.track.color.to_hex())
            color.setAlpha(80 if not row.track.visible else 190)
            track_colors[layer] = color
        # Цвета акторов тоже считаем один раз на кадр, а не на реплику: у
        # одного говорящего их сотни, и разбор строки повторялся бы для
        # каждой. Кэш живёт кадр — за это время документ не меняется.
        actor_colors: dict[int, QColor | None] = {}
        selected_color = QColor(self._palette.accent)
        selected_color.setAlpha(150)
        comment_color = QColor(self._palette.bg_elevated)
        edge_pen = QPen(QColor(self._palette.accent), 1)
        edge_pen_selected = QPen(QColor(self._palette.accent), 2)
        text_color = QColor(self._palette.text_primary)

        for event in self._doc.in_range(t0, t1):
            row = by_layer.get(event.layer)
            if row is None:
                continue

            lanes = max(1, self._sublanes_on.get(event.layer, 1))
            lane = min(self._sublane_of.get(event.eid, 0), lanes - 1)
            lane_h = (row.height - 4) / lanes
            top = row.top + 2 + lane * lane_h
            bottom = top + lane_h - 1

            x0 = self.ms_to_x(event.start)
            x1 = self.ms_to_x(event.end)
            rect = QRectF(x0, top, max(2.0, x1 - x0), bottom - top)

            selected = event.eid in self._selection
            # Цвет актора заливает реплику целиком. Полоской по краю он был
            # незаметен: на фильме из тысяч реплик разглядеть три пикселя
            # слева невозможно, а понять с одного взгляда, кто где говорит, —
            # ровно то, ради чего акторов и заводят. Выделение при этом
            # показывается рамкой, а не заливкой, иначе оно затирало бы цвет.
            actor_color = actor_colors.get(event.eid, _MISSING)
            if actor_color is _MISSING:
                color = self._doc.actor_color(event)
                actor_color = self._actor_brush(color, event.layer) if color else None
                actor_colors[event.eid] = actor_color

            if event.comment:
                brush = comment_color
            elif actor_color is not None:
                brush = actor_color
            elif selected:
                brush = selected_color
            else:
                brush = track_colors.get(event.layer, comment_color)

            painter.setPen(Qt.NoPen)
            painter.setBrush(brush)
            painter.drawRect(rect)

            # Реплика уже пары пикселей: боковые грани сливаются с заливкой, а
            # стоят двух вызовов рисования из трёх. При обзоре всего фильма
            # таких реплик — почти все.
            if rect.width() < _THIN_EVENT_PX:
                continue

            if selected and actor_color is not None:
                # У выделенной реплики с актором рамка идёт по всему
                # периметру: иначе выделение потерялось бы среди одноцветных
                # соседей той же роли.
                painter.setPen(edge_pen_selected)
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            else:
                painter.setPen(edge_pen_selected if selected else edge_pen)
                painter.drawLine(QPointF(rect.left(), top), QPointF(rect.left(), bottom))
                painter.drawLine(QPointF(rect.right(), top),
                                 QPointF(rect.right(), bottom))

            if rect.width() > 28 and lane_h >= 11:
                # Текст поверх цвета актора: тот выбирается человеком и бывает
                # каким угодно, поэтому светлая надпись на светлом фоне —
                # вопрос времени. Цвет надписи считаем от яркости подложки.
                painter.setPen(_readable_on(brush) if actor_color is not None
                               else text_color)
                painter.drawText(rect.adjusted(6, 0, -4, 0),
                                 Qt.AlignVCenter | Qt.AlignLeft,
                                 event.plain.replace("\n", " "))

    def _actor_brush(self, color, layer: int) -> QColor:
        """Цвет актора с прозрачностью скрытой дорожки.

        Скрытая дорожка гасится и здесь: иначе яркая заливка по говорящему
        сообщала бы, что реплика видна в кадре, тогда как она выключена.
        """
        brush = QColor(color.to_hex())
        track = self._doc.tracks.by_layer(layer)
        brush.setAlpha(190 if track is None or track.visible else 80)
        return brush

    def _event_brush(self, event: SubtitleEvent, track: Track, selected: bool) -> QColor:
        if event.comment:
            return QColor(self._palette.bg_elevated)
        if selected:
            color = QColor(self._palette.accent)
            color.setAlpha(150)
            return color
        color = QColor(track.color.to_hex())
        # Скрытая дорожка заметно бледнее: она не рисуется в кадре, и путать
        # её с обычной нельзя.
        color.setAlpha(80 if not track.visible else 190)
        return color

    def _paint_headers(self, painter: QPainter, rows: list[_Row]) -> None:
        painter.fillRect(QRectF(0, RULER_H, HEADER_W, self.height() - RULER_H),
                         QColor(self._palette.bg_elevated))

        font = QFont("Segoe UI")
        font.setPixelSize(11)
        painter.setFont(font)

        for row in rows:
            track = row.track
            painter.setPen(QPen(QColor(self._palette.border), 1))
            painter.drawLine(QPointF(0, row.bottom), QPointF(HEADER_W, row.bottom))

            # Цветная метка дорожки слева — та же, что заливка её реплик.
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(track.color.to_hex()))
            painter.drawRect(QRectF(0, row.top + 1, 4.0, row.height - 2))

            painter.setPen(QColor(
                self._palette.text_primary if track.visible else self._palette.text_muted
            ))
            name_rect = QRectF(10, row.top, HEADER_W - 52, row.height)
            painter.drawText(name_rect, Qt.AlignVCenter | Qt.AlignLeft, track.display_name())

            for glyph, rect in self._header_buttons(row):
                painter.setPen(QColor(self._palette.text_muted))
                painter.drawText(rect, Qt.AlignCenter, glyph)

            # Хваталка на границе: без неё возможность потянуть высоту
            # существует, но узнать о ней можно только случайно проведя
            # мышью по нужным пяти пикселям.
            if track.is_subtitle:
                painter.setPen(QPen(QColor(self._palette.text_muted), 1))
                middle = HEADER_W / 2
                for offset in (-6.0, 0.0, 6.0):
                    painter.drawLine(QPointF(middle + offset - 2, row.bottom - 2),
                                     QPointF(middle + offset + 2, row.bottom - 2))

        # Кнопки в углу над заголовками: слева — новая реплика, справа —
        # новая дорожка. Реплику создают несравнимо чаще, поэтому она первая.
        painter.setPen(QColor(self._palette.accent))
        painter.drawText(self._new_event_button_rect(), Qt.AlignCenter, "✎+")
        painter.setPen(QColor(self._palette.text_muted))
        painter.drawText(self._add_button_rect(), Qt.AlignCenter, "+")

    def _add_button_rect(self) -> QRectF:
        """Кнопка «добавить дорожку»."""
        return QRectF(HEADER_W - 22, 2, 18, RULER_H - 4)

    def _new_event_button_rect(self) -> QRectF:
        """Кнопка «новая реплика» — слева от кнопки дорожки."""
        return QRectF(HEADER_W - 44, 2, 18, RULER_H - 4)

    def _header_buttons(self, row: _Row) -> list[tuple[str, QRectF]]:
        """Иконки справа в заголовке: видимость и замок (у звука — заглушение)."""
        track = row.track
        size = 18.0
        top = row.top + (row.height - size) / 2
        if track.kind is TrackKind.AUDIO:
            return [("🔇" if track.muted else "🔊", QRectF(HEADER_W - 26, top, size, size))]
        if track.kind is TrackKind.VIDEO:
            return []
        return [
            ("👁" if track.visible else "—", QRectF(HEADER_W - 48, top, size, size)),
            ("🔒" if track.locked else "🔓", QRectF(HEADER_W - 26, top, size, size)),
        ]

    def _paint_rubber(self, painter: QPainter) -> None:
        """Рамка выделения. Полупрозрачная заливка плюс контур."""
        if self._drag.mode != DragMode.RUBBER or not self._rubber_moved():
            return
        rect = self._rubber_rect()
        fill = QColor(self._palette.accent)
        fill.setAlpha(50)
        painter.setPen(QPen(QColor(self._palette.accent), 1, Qt.DashLine))
        painter.setBrush(fill)
        painter.drawRect(rect)

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self.ms_to_x(self._time_ms)
        if HEADER_W - 2 <= x <= self.width() + 2:
            painter.setPen(QPen(QColor(self._palette.playhead), 1))
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            painter.setBrush(QColor(self._palette.playhead))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(
                QPolygonF([QPointF(x - 4, 0), QPointF(x + 4, 0), QPointF(x, 6)])
            )

    def _rebuild_sublanes_if_needed(self) -> None:
        """Раскладывает пересекающиеся реплики по подстрокам внутри дорожки.

        Пересчитывается только при изменении документа, а не на каждый кадр:
        на 20 000 событий это заметная работа, а зависит она лишь от таймингов
        и слоёв.
        """
        if self._layout_revision == self._doc.revision:
            return
        self._layout_revision = self._doc.revision

        assignment: dict[int, int] = {}
        counts: dict[int, int] = {}
        ends: dict[int, list[int]] = {}

        for event in sorted(self._doc.events, key=lambda e: (e.start, e.eid)):
            lane_ends = ends.setdefault(event.layer, [])
            placed = False
            for index, end in enumerate(lane_ends):
                if event.start >= end:
                    lane_ends[index] = event.end
                    assignment[event.eid] = index
                    placed = True
                    break
            if not placed:
                if len(lane_ends) < MAX_SUBLANES:
                    lane_ends.append(event.end)
                    assignment[event.eid] = len(lane_ends) - 1
                else:
                    # Переполнение: кладём в последнюю подстроку. Реплики в ней
                    # наложатся, но дробить полосу дальше бессмысленно — при
                    # пяти подстроках в ней уже ничего не разобрать.
                    assignment[event.eid] = MAX_SUBLANES - 1
            counts[event.layer] = len(lane_ends)

        self._sublane_of = assignment
        self._sublanes_on = counts

    # -- мышь ------------------------------------------------------------------ #

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MiddleButton:
            self._drag = _Drag(mode=DragMode.SEEK)
            return
        if event.button() != Qt.LeftButton:
            return

        pos = event.position()

        if pos.y() < RULER_H:
            if self._new_event_button_rect().contains(pos):
                self.create_event_at(self._time_ms, self._default_layer())
                return
            if self._add_button_rect().contains(pos):
                self._add_track()
                return
            if pos.x() >= HEADER_W:
                self._drag = _Drag(mode=DragMode.SEEK)
                self._seek_to(int(self.x_to_ms(pos.x())))
            return

        # Высоту проверяем раньше колонки: граница дорожки тянется и в области
        # реплик, а не только слева.
        layer = self._resize_edge_at(pos.x(), pos.y())
        if layer is not None:
            track = self._doc.tracks.by_layer(layer)
            self._drag = _Drag(
                mode=DragMode.RESIZE_TRACK,
                resize_layer=layer,
                origin_height=self.track_height(track),
                origin_raw_height=track.height,
                origin_y=pos.y(),
            )
            return

        if pos.x() < HEADER_W:
            self._click_header(pos.x(), pos.y())
            return

        ms = int(self.x_to_ms(pos.x()))
        hit = self._event_at(pos.x(), pos.y())
        modifiers = event.modifiers()
        additive = bool(modifiers & (Qt.ControlModifier | Qt.ShiftModifier))

        if hit is None:
            if modifiers & Qt.ControlModifier and self._row_at(pos.y()) is not None:
                self._begin_create(ms, pos.y())
                self.update()
                return
            # Пустое место на дорожке субтитров — начало рамки выделения.
            # Сам по себе щелчок без движения останется перемоткой: рамка
            # включается только когда мышь действительно повели, иначе она
            # отняла бы у пустого места привычный переход по времени.
            #
            # Добавляет к выделению здесь **Shift**, а не Ctrl: Ctrl по
            # пустому месту давно означает «создать реплику», и отнимать у
            # него это значение ради рамки было бы хуже, чем занять Shift.
            row = self._row_at(pos.y())
            if row is not None and row.track.is_subtitle:
                extend = bool(modifiers & Qt.ShiftModifier)
                self._drag = _Drag(
                    mode=DragMode.RUBBER,
                    origin_point=(pos.x(), pos.y()),
                    current_point=(pos.x(), pos.y()),
                    base_selection=frozenset(self._selection if extend else ()),
                )
                if not extend:
                    self._apply_selection(set(), None)
                self.update()
                return

            self._apply_selection(set(), None)
            self._drag = _Drag(mode=DragMode.SEEK)
            self._seek_to(ms)
            self.update()
            return

        target, edge = hit
        if additive:
            # Ctrl по выделенной реплике снимает именно её: это то, чем
            # правят состав выделения, промахнувшись на одну строку.
            chosen = set(self._selection)
            if target.eid in chosen:
                chosen.discard(target.eid)
            else:
                chosen.add(target.eid)
            self._apply_selection(chosen, target.eid)
            self.update()
            return

        if target.eid not in self._selection:
            self._apply_selection({target.eid}, target.eid)
        else:
            # Щелчок внутри выделения его не рушит: за него берутся, чтобы
            # тащить всю группу, и сброс до одной реплики сделал бы
            # групповое перетаскивание недостижимым.
            self._selected = target.eid
            self.selection_changed.emit(target.eid)

        if edge == "start":
            mode = DragMode.START
        elif edge == "end":
            mode = DragMode.END
        else:
            mode = DragMode.BODY

        self._drag = _Drag(
            mode=mode,
            eid=target.eid,
            grab_offset_ms=ms - target.start,
            origin_ms=ms,
            original=(target.start, target.end),
            origin_layer=target.layer,
            group=tuple(self.selected_eids) if mode == DragMode.BODY else (),
        )
        self.update()

    def _click_header(self, x: float, y: float) -> None:
        row = self._row_at(y)
        if row is None:
            return
        for glyph, rect in self._header_buttons(row):
            if not rect.contains(x, y):
                continue
            track = row.track
            if track.kind is TrackKind.AUDIO:
                track.muted = not track.muted
            elif glyph in ("👁", "—"):
                self._undo.run(SetTrackFlags(track.layer, visible=not track.visible))
                self.document_edited.emit()
            else:
                self._undo.run(SetTrackFlags(track.layer, locked=not track.locked))
                self.document_edited.emit()
            self.update()
            return

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        self._remember_mouse(pos.x(), pos.y())

        if self._drag.mode == DragMode.NONE:
            self._update_cursor(pos.x(), pos.y())
            return

        if self._drag.mode == DragMode.RESIZE_TRACK:
            self._update_resize(pos.y())
            return

        if self._drag.mode == DragMode.RUBBER:
            self._drag.current_point = (pos.x(), pos.y())
            self._update_rubber()
            self.update()
            return

        ms = int(self.x_to_ms(pos.x()))
        if self._drag.mode == DragMode.SEEK:
            self._seek_to(ms)
            return

        alt = bool(event.modifiers() & Qt.AltModifier)
        snapped = self._snap(ms, disabled=alt)

        if self._drag.mode == DragMode.CREATE:
            self._update_create(snapped)
        elif self._drag.eid is not None and self._doc.has(self._drag.eid):
            self._update_event_drag(snapped, pos.y())
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag.mode == DragMode.RESIZE_TRACK:
            self._commit_resize()
        if self._drag.mode == DragMode.RUBBER and not self._rubber_moved():
            # Рамку начали, но мышь не повели — это обычный щелчок по пустому
            # месту, и он должен перематывать, как перематывал всегда.
            self._seek_to(int(self.x_to_ms(self._drag.origin_point[0])))
        if self._drag.mode != DragMode.NONE:
            self._drag = _Drag()
            self._last_snap_kind = ""
            self.update()

    #: Насколько надо повести мышь, чтобы щелчок стал рамкой. Меньше — и
    #: дрогнувшая рука при обычном щелчке выделяла бы случайные реплики.
    RUBBER_THRESHOLD_PX = 4.0

    def _rubber_moved(self) -> bool:
        dx = abs(self._drag.current_point[0] - self._drag.origin_point[0])
        dy = abs(self._drag.current_point[1] - self._drag.origin_point[1])
        return max(dx, dy) >= self.RUBBER_THRESHOLD_PX

    def _rubber_rect(self) -> QRectF:
        (x0, y0), (x1, y1) = self._drag.origin_point, self._drag.current_point
        return QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def _update_rubber(self) -> None:
        """Пересчитывает выделение под рамкой.

        Захватывается реплика, **пересекающая** рамку, а не целиком в неё
        попавшая: при обзоре всего фильма реплика шире экрана — обычное дело,
        и требовать обвести её целиком значило бы не дать её выделить.
        """
        if not self._rubber_moved():
            return
        rect = self._rubber_rect()
        t0 = int(self.x_to_ms(rect.left()))
        t1 = int(self.x_to_ms(rect.right()))
        rows = {r.track.layer: r for r in self.rows() if r.track.is_subtitle}

        chosen = set(self._drag.base_selection)
        for event in self._doc.in_range(t0, t1 + 1):
            row = rows.get(event.layer)
            if row is None:
                continue
            if row.bottom > rect.top() and row.top < rect.bottom():
                chosen.add(event.eid)

        if chosen != self._selection:
            self._apply_selection(chosen, self._selected)

    def _update_resize(self, y: float) -> None:
        """Тянет границу дорожки.

        Во время жеста высота меняется напрямую — это нужно для отклика под
        курсором. В историю отмены попадёт **один** шаг при отпускании кнопки:
        команда на каждое движение мыши забила бы её сотнями записей.
        """
        track = self._doc.tracks.by_layer(self._drag.resize_layer)
        if track is None or self._track_zoom <= 0:
            return
        delta = y - self._drag.origin_y
        wanted = self._drag.origin_height + delta
        base = max(MIN_TRACK_H, min(MAX_TRACK_H, wanted)) / self._track_zoom
        track.height = round(base)
        self.updateGeometry()
        self.update()

    def _commit_resize(self) -> None:
        """Записывает новую высоту командой, чтобы её можно было отменить."""
        track = self._doc.tracks.by_layer(self._drag.resize_layer)
        if track is None:
            return
        final = track.height
        original = self._drag.origin_raw_height
        if final == original:
            return
        # Возвращаем исходное значение: команда сама запомнит его как «до».
        track.height = original
        updated = Track(
            kind=track.kind, layer=track.layer, name=track.name, color=track.color,
            visible=track.visible, locked=track.locked, height=final,
        )
        self._undo.run(UpdateTrack(track.layer, updated))
        self.document_edited.emit()
        self.status_message.emit(
            f"{track.display_name()}: высота {final} px"
        )

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Двойной щелчок по имени дорожки переименовывает её."""
        pos = event.position()
        if pos.x() >= HEADER_W or pos.y() < RULER_H:
            return
        row = self._row_at(pos.y())
        if row is not None and row.track.is_subtitle:
            self._rename_track(row.track)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        ticks = event.angleDelta().y() / 120.0
        mods = event.modifiers()
        if mods & Qt.ControlModifier:
            self.zoom_at(1.18**ticks, event.position().x())
        elif mods & Qt.AltModifier:
            # Alt+колесо — масштаб дорожек по вертикали, как в монтажных
            # программах. Shift занят усилением волны.
            self.zoom_tracks(1.12**ticks)
        elif mods & Qt.ShiftModifier:
            self._gain = max(0.2, min(8.0, self._gain * (1.15**ticks)))
            self.update()
        else:
            self.scroll_by(-ticks * 90)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_F:
            self.fit_selection()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_at(1.3, self.width() / 2)
        elif key == Qt.Key_Minus:
            self.zoom_at(1 / 1.3, self.width() / 2)
        elif key == Qt.Key_Home:
            self.fit_all()
        else:
            super().keyPressEvent(event)

    # -- контекстное меню ---------------------------------------------------------- #

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.context_menu(event.pos()).exec(event.globalPos())

    def context_menu(self, position) -> QMenu:
        """Собирает меню для точки виджета.

        Отдельно от :meth:`contextMenuEvent`, потому что тот показывает меню
        модально: проверить его состав, не открывая окно, иначе невозможно.
        """
        row = self._row_at(position.y())

        # Щелчок по невыделенной реплике выделяет её. Пункты меню называют,
        # к скольким репликам они применятся, и это число должно совпадать
        # с тем, что человек видит выделенным, — иначе «Удалить 7 реплик»
        # унесёт не то, на что он показывает.
        hit = self._event_at(position.x(), position.y())
        if hit is not None and hit[0].eid not in self._selection:
            self._apply_selection({hit[0].eid}, hit[0].eid)
            self.update()
        # Замок на дорожке значит «здесь ничего не менять»: реплика
        # оттуда могла попасть в выделение из таблицы, где дорожек не
        # видно.
        targets = editable_eids(self._doc, self.selected_eids)

        menu = QMenu(self)

        # Первым — создание реплики: это самое частое действие на таймлайне,
        # и искать его под пунктами про дорожки неудобно.
        where_ms = int(self.x_to_ms(position.x())) if position.x() >= HEADER_W else self._time_ms
        target_layer = (
            row.track.layer if row is not None and row.track.is_subtitle
            else self._default_layer()
        )
        editable = self._layer_is_editable(target_layer)
        new_event = QAction(f"Новая реплика с {_short_time(where_ms)}", menu)
        new_event.setEnabled(editable)
        new_event.triggered.connect(
            lambda: self.create_event_at(where_ms, target_layer)
        )
        menu.addAction(new_event)

        clipboard = QGuiApplication.clipboard()
        clip = clipboard.text().strip() if clipboard is not None else ""
        paste = QAction("Вставить из буфера сюда", menu)
        paste.setEnabled(editable and bool(clip))
        paste.triggered.connect(
            lambda: self.create_event_with_text(where_ms, clip, target_layer)
        )
        menu.addAction(paste)

        if targets:
            menu.addSeparator()
            menu.addMenu(
                actor_submenu(
                    self, self._doc, targets, self._run_edit,
                    actor_command=self.actor_command,
                )
            )
            menu.addMenu(status_submenu(self, self._doc, targets, self._run_edit))
            note = QAction("Заметка…", menu)
            note.setEnabled(len(targets) == 1)
            if len(targets) == 1:
                note.triggered.connect(
                    note_action(self, self._doc, targets[0], self._run_edit)
                )
            menu.addAction(note)

            menu.addSeparator()
            what = plural_events(len(targets))
            duplicate = QAction(f"Дублировать {what}", menu)
            duplicate.triggered.connect(
                lambda: self._run_edit(DuplicateEvents(list(targets)))
            )
            menu.addAction(duplicate)

            delete = QAction(f"Удалить {what}", menu)
            delete.setShortcut("Del")
            delete.triggered.connect(
                lambda: self._run_edit(DeleteEvents(list(targets)))
            )
            menu.addAction(delete)

        menu.addSeparator()
        add = QAction("Новая дорожка субтитров", menu)
        add.triggered.connect(self._add_track)
        menu.addAction(add)

        if row is not None and row.track.is_subtitle:
            track = row.track
            menu.addSeparator()

            rename = QAction(f"Переименовать «{menu_label(track.display_name())}»…", menu)
            rename.triggered.connect(lambda: self._rename_track(track))
            menu.addAction(rename)

            visible = QAction("Показывать в кадре", menu)
            visible.setCheckable(True)
            visible.setChecked(track.visible)
            visible.triggered.connect(
                lambda on, layer=track.layer: self._set_flags(layer, visible=on)
            )
            menu.addAction(visible)

            locked = QAction("Заблокировать", menu)
            locked.setCheckable(True)
            locked.setChecked(track.locked)
            locked.triggered.connect(
                lambda on, layer=track.layer: self._set_flags(layer, locked=on)
            )
            menu.addAction(locked)

            remove = QAction("Удалить дорожку", menu)
            remove.setEnabled(len(self._doc.tracks.subtitles) > 1)
            remove.triggered.connect(lambda: self._remove_track(track))
            menu.addAction(remove)

            others = [
                other for other in self._doc.tracks.display_order(with_media=False)
                if other.layer != track.layer
            ]
            # Пункт, открывающий пустое подменю, выглядит поломкой: пока
            # дорожка одна, переносить реплику некуда, и говорить об этом
            # нечего.
            if hit is not None and others:
                menu.addSeparator()
                move = menu.addMenu("Перенести реплику на дорожку")
                for other in others:
                    action = QAction(menu_label(other.display_name()), move)
                    action.triggered.connect(
                        lambda _=False, eid=hit[0].eid, layer=other.layer:
                        self._move_event(eid, layer)
                    )
                    move.addAction(action)

        return menu

    def create_event_at(self, ms: int, layer: int | None = None) -> int | None:
        """Создаёт реплику в указанном месте. Возвращает её ``eid``.

        Длительность берётся с запасом (две секунды): пустая реплика нулевой
        длины на таймлайне невидима, и пользователь решил бы, что ничего не
        произошло. Границы потом всё равно подгоняются мышью.

        Если на этом месте уже есть реплика, новая ставится **после** неё:
        накладывать их друг на друга по неосторожному щелчку не нужно.
        """
        layer = self._default_layer() if layer is None else layer
        if not self._layer_is_editable(layer):
            self.status_message.emit("Дорожка заблокирована")
            return None

        start = max(0, ms)
        start = self._free_start(start, layer)
        style = next(iter(self._doc.styles), "Default")
        template = SubtitleEvent(
            eid=0, start=start, end=start + NEW_EVENT_MS, text="", style=style,
            layer=layer,
        )
        self._undo.run(InsertEvent(template))
        created = self._doc.events[-1].eid

        self._selected = created
        self.selection_changed.emit(created)
        self.document_edited.emit()
        self._ensure_visible(start)
        self.update()
        self.status_message.emit(f"Новая реплика с {format_srt(start)}")
        return created

    def _remember_mouse(self, x: float, y: float) -> None:
        """Запоминает место под курсором — для вставки «сюда»."""
        row = self._row_at(y)
        if x < HEADER_W or row is None or not row.track.is_subtitle:
            self._mouse_ms = None
            self._mouse_layer = None
            return
        self._mouse_ms = max(0, int(self.x_to_ms(x)))
        self._mouse_layer = row.track.layer

    def leaveEvent(self, event) -> None:  # noqa: N802
        """Курсор ушёл — забываем его место.

        Иначе вставка «под курсором» попала бы туда, где мышь была час
        назад, и человек не понял бы, откуда взялось это время.
        """
        self._mouse_ms = None
        self._mouse_layer = None
        super().leaveEvent(event)

    @property
    def mouse_position(self) -> tuple[int, int] | None:
        """``(время, дорожка)`` под курсором либо ``None``."""
        if self._mouse_ms is None or self._mouse_layer is None:
            return None
        return self._mouse_ms, self._mouse_layer

    def create_event_with_text(
        self,
        ms: int,
        text: str,
        layer: int | None = None,
        *,
        duration_ms: int | None = None,
    ) -> int | None:
        """Создаёт реплику с готовым текстом.

        Длительность по умолчанию считается от объёма текста: вставленный
        абзац в две секунды не прочитать, и реплика сразу оказалась бы
        помечена как слишком быстрая. Берём пятнадцать знаков в секунду —
        привычную норму — и не меньше обычной длительности новой реплики.
        """
        created = self.create_event_at(ms, layer)
        if created is None or not text:
            return created

        event = self._doc.by_eid(created)
        span = duration_ms or _reading_time(text)
        self._undo.run(
            CompositeCommand(
                [
                    SetText(created, text),
                    SetTiming(created, end=event.start + span),
                ],
                label="Вставка реплики",
            )
        )
        self.document_edited.emit()
        self.update()
        return created

    def _default_layer(self) -> int:
        """Куда класть реплику, когда дорожка не указана.

        Дорожка выделенной реплики, иначе самая нижняя незаблокированная:
        продолжать работу там же, где она шла, естественнее, чем всегда
        сваливать в нулевой слой.
        """
        if self._selected is not None and self._doc.has(self._selected):
            layer = self._doc.by_eid(self._selected).layer
            if self._layer_is_editable(layer):
                return layer
        for track in self._doc.tracks.subtitles:
            if not track.locked:
                return track.layer
        return self._doc.tracks.subtitles[0].layer if self._doc.tracks.subtitles else 0

    def _layer_is_editable(self, layer: int) -> bool:
        track = self._doc.tracks.by_layer(layer)
        return track is not None and not track.locked

    def _free_start(self, start: int, layer: int) -> int:
        """Сдвигает начало за конец реплики, которая уже занимает это место."""
        for event in self._doc.events_on_layer(layer):
            if event.start <= start < event.end:
                return event.end + MIN_GAP_MS
        return start

    def _add_track(self) -> None:
        self._undo.run(AddTrack())
        self.document_edited.emit()
        self.tracks_changed.emit()
        self.updateGeometry()
        self.update()

    def _rename_track(self, track: Track) -> None:
        name, ok = QInputDialog.getText(
            self, "Имя дорожки", "Название:", text=track.name or track.display_name()
        )
        if not ok:
            return
        updated = Track(
            kind=track.kind, layer=track.layer, name=name.strip(), color=track.color,
            visible=track.visible, locked=track.locked, height=track.height,
        )
        self._undo.run(UpdateTrack(track.layer, updated))
        self.document_edited.emit()
        self.tracks_changed.emit()
        self.update()

    def _remove_track(self, track: Track) -> None:
        self._undo.run(RemoveTrack(track.layer))
        self.document_edited.emit()
        self.tracks_changed.emit()
        self.updateGeometry()
        self.update()

    def _set_flags(self, layer: int, **flags: bool) -> None:
        self._undo.run(SetTrackFlags(layer, **flags))
        self.document_edited.emit()
        self.update()

    def _move_event(self, eid: int, layer: int) -> None:
        self._undo.run(MoveEventsToLayer([eid], layer))
        self.document_edited.emit()
        self.update()

    def _run_edit(self, command) -> None:
        """Исполняет команду из контекстного меню и обновляет вид."""
        self._undo.run(command)
        self.document_edited.emit()
        self.update()

    # -- логика правки ---------------------------------------------------------- #

    def _seek_to(self, ms: int) -> None:
        self._time_ms = max(0, ms)
        self.time_changed.emit(self._time_ms)
        self.update()

    def _snap(self, ms: int, *, disabled: bool) -> int:
        if disabled or not self._snap_enabled:
            self._last_snap_kind = ""
            return ms
        boundaries = [
            t
            for e in self._doc.in_range(int(self._view_start_ms), int(self.view_end_ms))
            if e.eid != self._drag.eid
            for t in (e.start, e.end)
        ]
        ctx = SnapContext(
            fps=self._fps,
            keyframes=self._keyframes,
            event_boundaries=tuple(boundaries),
            px_per_ms=self._px_per_ms,
        )
        mode = SnapMode.END if self._drag.mode == DragMode.END else SnapMode.START
        result = snap_time(ms, ctx, mode=mode)
        self._last_snap_kind = result.kind
        return result.ms

    def _update_event_drag(self, ms: int, y: float) -> None:
        assert self._drag.eid is not None
        event = self._doc.by_eid(self._drag.eid)
        start, end = self._drag.original

        if self._drag.mode == DragMode.START:
            new_start = min(ms, end - MIN_EVENT_MS)
            self._undo.run(SetTiming(event.eid, start=new_start, label="Начало реплики"))
        elif self._drag.mode == DragMode.END:
            new_end = max(ms, start + MIN_EVENT_MS)
            self._undo.run(SetTiming(event.eid, end=new_end, label="Конец реплики"))
        else:
            shifted = ms - self._drag.grab_offset_ms
            duration = end - start
            new_start = max(0, shifted)
            group = [eid for eid in self._drag.group if eid != event.eid]
            if group:
                self._drag_group(event, new_start, duration, group)
            else:
                self._undo.run(
                    SetTiming(event.eid, start=new_start, end=new_start + duration,
                              label="Сдвиг реплики")
                )
                self._maybe_change_layer(event, y)

        self.document_edited.emit()
        self._emit_drag_status(event)

    def _drag_group(
        self,
        leader: SubtitleEvent,
        new_start: int,
        duration: int,
        group: list[int],
    ) -> None:
        """Сдвигает всю выделенную группу вслед за ведущей репликой.

        Сдвиг общий и считается от ведущей: сохранять взаимные расстояния
        внутри группы — весь смысл такого перетаскивания, иначе проще было бы
        двигать реплики поодиночке.

        В ноль группа не уезжает: если крайняя слева упёрлась бы в начало
        отсчёта, весь сдвиг подрезается. Иначе реплики слипались бы у нуля,
        теряя расстояния безвозвратно — отмена вернула бы времена, но человек
        уже отпустил бы кнопку.
        """
        shift = new_start - leader.start
        events = [self._doc.by_eid(eid) for eid in group if self._doc.has(eid)]
        earliest = min((e.start for e in events), default=leader.start)
        shift = max(shift, -min(earliest, leader.start))
        if shift == 0:
            return

        moves = [
            SetTiming(leader.eid, start=leader.start + shift,
                      end=leader.start + shift + duration)
        ]
        moves.extend(
            SetTiming(e.eid, start=e.start + shift, end=e.end + shift)
            for e in events
        )
        self._undo.run(
            CompositeCommand(moves, label=f"Сдвиг {len(moves)} реплик")
        )

    def _maybe_change_layer(self, event: SubtitleEvent, y: float) -> None:
        """Перетаскивание вверх-вниз меняет дорожку реплики.

        Только при перемещении целиком: тянуть край и случайно сменить слой
        было бы неожиданно.
        """
        row = self._row_at(y)
        if row is None or not row.track.is_subtitle or row.track.locked:
            return
        if row.track.layer == event.layer:
            return
        self._undo.run(MoveEventsToLayer([event.eid], row.track.layer))

    def _begin_create(self, ms: int, y: float) -> None:
        row = self._row_at(y)
        layer = row.track.layer if row is not None and row.track.is_subtitle else 0
        style = next(iter(self._doc.styles), "Default")
        template = SubtitleEvent(eid=0, start=ms, end=ms + 1000, text="",
                                 style=style, layer=layer)
        self._undo.run(InsertEvent(template))
        new_eid = self._doc.events[-1].eid
        self._selected = new_eid
        self.selection_changed.emit(new_eid)
        self.document_edited.emit()
        self._drag = _Drag(
            mode=DragMode.CREATE, eid=new_eid, origin_ms=ms,
            original=(ms, ms + 1000), origin_layer=layer,
        )

    def _update_create(self, ms: int) -> None:
        if self._drag.eid is None or not self._doc.has(self._drag.eid):
            return
        origin = self._drag.origin_ms
        start, end = (origin, ms) if ms > origin else (ms, origin)
        if end - start < MIN_EVENT_MS:
            end = start + MIN_EVENT_MS
        self._undo.run(
            SetTiming(self._drag.eid, start=start, end=end, label="Новая реплика")
        )
        self.document_edited.emit()

    def _emit_drag_status(self, event: SubtitleEvent) -> None:
        suffix = {"keyframe": " · ключевой кадр", "event": " · стык реплик",
                  "frame": "", "": ""}[self._last_snap_kind]
        track = self._doc.tracks.by_layer(event.layer)
        where = f" · {track.display_name()}" if track is not None else ""
        self.status_message.emit(
            f"{format_srt(event.start)} → {format_srt(event.end)}  "
            f"({event.duration / 1000:.2f} с){suffix}{where}"
        )

    # -- хит-тест ------------------------------------------------------------- #

    def _event_at(self, x: float, y: float) -> tuple[SubtitleEvent, str] | None:
        if x < HEADER_W or y < RULER_H:
            return None
        row = self._row_at(y)
        if row is None or not row.track.is_subtitle or row.track.locked:
            return None

        layer = row.track.layer
        lanes = max(1, self._sublanes_on.get(layer, 1))
        lane_h = (row.height - 4) / lanes
        lane = int((y - row.top - 2) // lane_h) if lane_h > 0 else 0
        lane = max(0, min(lane, lanes - 1))

        # Сначала собираем всё, что попадает по времени, вместе с подстрокой
        # каждой реплики. Отбор по вертикали идёт **после**: одной пары
        # наложенных реплик хватает, чтобы поделить всю дорожку пополам, и
        # тогда у остальных — непересекающихся — под нарисованной полоской
        # остаётся пустая половина. Промах по ней читается как «щелчок
        # перестал выделять реплику», хотя человек целится ровно в неё.
        # Спрашиваем документ про окрестность курсора, а не про всю видимую
        # область. При обзоре целого фильма видимых реплик двадцать тысяч, и
        # перебор их всех стоил 9 мс — на каждое движение мыши, потому что
        # от попадания зависит и вид курсора. По окрестности выходит 13 мкс.
        ms = self.x_to_ms(x)
        slack = EDGE_GRAB_PX / self._px_per_ms if self._px_per_ms > 0 else 0.0
        candidates: list[tuple[int, SubtitleEvent, float, float]] = []
        for event in self._doc.in_range(int(ms - slack), int(ms + slack)):
            if event.layer != layer:
                continue
            x0 = self.ms_to_x(event.start)
            x1 = self.ms_to_x(event.end)
            if x < x0 - EDGE_GRAB_PX or x > x1 + EDGE_GRAB_PX:
                continue
            candidates.append(
                (min(self._sublane_of.get(event.eid, 0), lanes - 1), event, x0, x1)
            )

        if not candidates:
            return None

        # Своя подстрока важнее: там, где реплики действительно наложены,
        # выбор делает вертикаль, и промахнуться по соседней нельзя.
        exact = [item for item in candidates if item[0] == lane]
        _lane, target, x0, x1 = (
            exact[0] if exact else min(candidates, key=lambda item: abs(item[0] - lane))
        )

        # Зона края не должна съедать всё событие: у короткой реплики
        # иначе не останется тела, за которое её можно сдвинуть целиком.
        edge = min(EDGE_GRAB_PX, max(1.0, (x1 - x0) / 3))
        if abs(x - x0) <= edge:
            return target, "start"
        if abs(x - x1) <= edge:
            return target, "end"
        return target, "body"

    def event(self, event) -> bool:
        """Подсказка с текстом реплики под курсором.

        Подпись на самой реплике помещается, только когда та шире 28 пикселей
        — при обзоре всего фильма это почти никогда. Без подсказки таймлайн в
        таком масштабе превращается в набор безымянных полосок, и найти нужное
        место можно только тыкая наугад.

        Обрабатываем ``ToolTip``, а не показываем подсказку из движения мыши:
        Qt сам знает, сколько курсор должен постоять на месте, и не мигает
        подсказкой при каждом сдвиге.
        """
        if event.type() == QEvent.ToolTip:
            point = event.position() if hasattr(event, "position") else event.pos()
            text = self._tooltip_at(float(point.x()), float(point.y()))
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def _tooltip_at(self, x: float, y: float) -> str:
        """Что рассказать о точке под курсором."""
        hit = self._event_at(x, y)
        if hit is None:
            return ""
        target, _edge = hit
        lines = [
            f"{format_srt(target.start)} → {format_srt(target.end)}"
            f"   ({target.duration / 1000:.1f} с)",
            target.plain or "(пусто)",
        ]
        if target.name:
            lines.insert(1, f"Говорит: {target.name}")
        if target.comment:
            lines.append("Комментарий — в кадре не показывается")
        # Текст реплики пришёл из файла: показывать его как разметку нельзя.
        return plain_tooltip("\n".join(lines))

    def _update_cursor(self, x: float, y: float) -> None:
        if self._resize_edge_at(x, y) is not None:
            self.setCursor(Qt.SizeVerCursor)
            return
        if x < HEADER_W:
            self.setCursor(Qt.ArrowCursor)
            return
        hit = self._event_at(x, y)
        if hit is None:
            self.setCursor(Qt.ArrowCursor)
        elif hit[1] in ("start", "end"):
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)


# --------------------------------------------------------------------------- #


def _envelope(upper, lower, mid: float, scale: float, x0: float = 0.0) -> QPolygonF:
    """Полигон-огибающая: слева направо по верху, справа налево по низу.

    Один полигон вместо тысяч отрезков — главный приём, за счёт которого
    волна перерисовывается за доли миллисекунды независимо от длины файла.
    """
    n = len(upper)
    points = [QPointF(x0 + i, mid - float(upper[i]) * scale) for i in range(n)]
    points += [
        QPointF(x0 + i, mid - float(lower[i]) * scale) for i in range(n - 1, -1, -1)
    ]
    return QPolygonF(points)


def _short_time(ms: float) -> str:
    """Компактная подпись шкалы: часы показываются только когда они есть."""
    total = int(max(0, ms)) // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    frac = int(max(0, ms)) % 1000
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    if frac and ms < 10_000:
        return f"{m}:{s:02d}.{frac // 100}"
    return f"{m}:{s:02d}"
