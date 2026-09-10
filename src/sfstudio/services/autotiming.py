"""Автоматическая доводка таймингов.

Набор операций, которые переводчик иначе делает руками на каждой реплике:
подтянуть начало пораньше, продлить конец, сомкнуть мелкие зазоры, растянуть
слишком быстрые реплики, притянуть границы к монтажным склейкам.

**Порядок операций фиксирован и важен.** Каждый шаг меняет вход следующего:
сначала реплики расширяются (lead-in/out, минимальная длительность, скорость
чтения), затем смыкаются зазоры, затем границы притягиваются к склейкам и
сетке кадров, и только в самом конце разрешаются конфликты с соседями.
Переставить шаги местами — значит получить перекрытия там, где их только что
убрали.

Модуль не трогает документ: он считает **план** — какие тайминги должны стать
какими. Применением занимается команда, поэтому вся доводка отменяется одним
Ctrl+Z, а диалог может показать предпросмотр до того, как что-то изменится.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from sfstudio.app.i18n import tr
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.time import FpsModel, SnapMode
from sfstudio.media.keyframes import KeyframeIndex

__all__ = ["TimingChange", "TimingPlan", "TimingSettings", "build_plan"]


@dataclass(frozen=True, slots=True)
class TimingSettings:
    """Что именно делать. Ноль или ``False`` — шаг выключен."""

    #: Насколько раньше начинать реплику.
    lead_in_ms: int = 0
    #: Насколько дольше держать после конца речи.
    lead_out_ms: int = 0
    #: Зазор меньше этого смыкается: две реплики стыкуются вплотную.
    close_gap_below_ms: int = 0
    #: Растянуть реплики короче этого, если есть куда.
    min_duration_ms: int = 0
    #: Растянуть конец, пока скорость чтения не станет комфортной.
    target_cps: float | None = None
    #: Притянуть границы к ближайшему ключевому кадру.
    snap_to_keyframes: bool = False
    keyframe_radius_frames: int = 5
    #: Округлить все тайминги к сетке кадров.
    snap_to_frames: bool = False
    #: Минимальный зазор между соседями, в кадрах.
    min_gap_frames: int = 2

    @property
    def is_empty(self) -> bool:
        """Ничего не выбрано — применять нечего."""
        return not (
            self.lead_in_ms
            or self.lead_out_ms
            or self.close_gap_below_ms
            or self.min_duration_ms
            or self.target_cps
            or self.snap_to_keyframes
            or self.snap_to_frames
        )


@dataclass(frozen=True, slots=True)
class TimingChange:
    """Одно изменение тайминга — для предпросмотра и применения."""

    eid: int
    old_start: int
    old_end: int
    new_start: int
    new_end: int

    @property
    def moved(self) -> bool:
        return (self.new_start, self.new_end) != (self.old_start, self.old_end)

    @property
    def delta_duration(self) -> int:
        return (self.new_end - self.new_start) - (self.old_end - self.old_start)


@dataclass(slots=True)
class TimingPlan:
    """Результат расчёта: что и как изменится."""

    changes: list[TimingChange] = field(default_factory=list)
    #: Реплики, которые не удалось растянуть до нормы — места не хватило.
    unresolved: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.changes)

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def as_mapping(self) -> dict[int, tuple[int, int]]:
        return {c.eid: (c.new_start, c.new_end) for c in self.changes}

    def summary(self) -> str:
        if not self.changes:
            return tr('Ничего менять не потребовалось')
        stretched = sum(1 for c in self.changes if c.delta_duration > 0)
        shrunk = sum(1 for c in self.changes if c.delta_duration < 0)
        parts = [tr('затронуто реплик: {0}').format(len(self.changes))]
        if stretched:
            parts.append(tr('удлинено {0}').format(stretched))
        if shrunk:
            parts.append(tr('укорочено {0}').format(shrunk))
        if self.unresolved:
            parts.append(tr('не хватило места: {0}').format(len(self.unresolved)))
        return " · ".join(parts)


def _cps_of(text_length: int, duration_ms: int) -> float:
    return text_length * 1000.0 / duration_ms if duration_ms > 0 else 0.0


def build_plan(
    events: Sequence[SubtitleEvent],
    settings: TimingSettings,
    *,
    fps: FpsModel | None = None,
    keyframes: KeyframeIndex | None = None,
    all_events: Sequence[SubtitleEvent] | None = None,
) -> TimingPlan:
    """Считает план доводки для ``events``.

    ``all_events`` — полный документ: соседи нужны, чтобы не наехать на реплики
    вне выделения. Если не передан, соседями считаются только сами ``events``.
    """
    plan = TimingPlan()
    if settings.is_empty or not events:
        return plan

    ordered = sorted(events, key=lambda e: (e.start, e.eid))
    context = sorted(all_events or events, key=lambda e: (e.start, e.eid))
    selected = {e.eid for e in ordered}

    gap = _min_gap_ms(settings, fps)
    # Рабочие копии таймингов: шаги применяются последовательно к ним.
    times: dict[int, list[int]] = {e.eid: [e.start, e.end] for e in ordered}

    _apply_lead(ordered, times, settings)
    _apply_min_duration(ordered, times, settings)
    _apply_target_cps(ordered, times, settings)
    _apply_close_gaps(ordered, times, settings)
    pinned = _apply_keyframes(ordered, times, settings, fps, keyframes)
    _apply_frame_grid(ordered, times, settings, fps, pinned)
    unresolved = _resolve_conflicts(ordered, times, context, selected, gap)
    unresolved += _unmet_targets(ordered, times, settings, unresolved)

    for event in ordered:
        start, end = times[event.eid]
        if (start, end) != (event.start, event.end):
            plan.changes.append(
                TimingChange(event.eid, event.start, event.end, start, end)
            )
    plan.unresolved = unresolved
    return plan


def _unmet_targets(
    events, times, settings: TimingSettings, already: list[int]
) -> list[int]:
    """Реплики, которым не хватило места до заданной нормы.

    Проверяется **после** разрешения конфликтов: расширение могло упереться
    в соседа, и цель оказалась недостижимой. Молчать об этом нельзя — иначе
    пользователь решит, что доводка отработала, и не заметит оставшихся
    слишком быстрых реплик.
    """
    unmet: list[int] = []
    for event in events:
        if event.eid in already:
            continue
        start, end = times[event.eid]
        duration = end - start
        if settings.min_duration_ms and duration < settings.min_duration_ms:
            unmet.append(event.eid)
            continue
        if settings.target_cps:
            length = len(event.plain.replace(" ", "").replace("\n", ""))
            if length and _cps_of(length, duration) > settings.target_cps * 1.02:
                unmet.append(event.eid)
    return unmet


def _min_gap_ms(settings: TimingSettings, fps: FpsModel | None) -> int:
    rate = float(fps.rate) if fps else 23.976
    return int(settings.min_gap_frames * 1000 / rate) if settings.min_gap_frames else 0


def _apply_lead(events, times, settings: TimingSettings) -> None:
    """Расширяет реплику в обе стороны на заданные величины."""
    if not (settings.lead_in_ms or settings.lead_out_ms):
        return
    for event in events:
        pair = times[event.eid]
        pair[0] = max(0, pair[0] - settings.lead_in_ms)
        pair[1] = pair[1] + settings.lead_out_ms


def _apply_min_duration(events, times, settings: TimingSettings) -> None:
    """Растягивает слишком короткие реплики — только вперёд.

    Двигать начало назад нельзя: реплика начнётся до того, как её произнесли,
    и рассинхронизуется с речью. Конец сдвинуть безопаснее.
    """
    if settings.min_duration_ms <= 0:
        return
    for event in events:
        pair = times[event.eid]
        if pair[1] - pair[0] < settings.min_duration_ms:
            pair[1] = pair[0] + settings.min_duration_ms


def _apply_target_cps(events, times, settings: TimingSettings) -> None:
    """Продлевает реплики, которые читаются слишком быстро."""
    target = settings.target_cps
    if not target or target <= 0:
        return
    for event in events:
        length = len(event.plain.replace(" ", "").replace("\n", ""))
        if not length:
            continue
        pair = times[event.eid]
        duration = pair[1] - pair[0]
        if _cps_of(length, duration) <= target:
            continue
        needed = int(length * 1000 / target)
        pair[1] = pair[0] + needed


def _apply_close_gaps(events, times, settings: TimingSettings) -> None:
    """Смыкает мелкие зазоры: две реплики стыкуются вплотную.

    Крошечный зазор между репликами читается как мигание, и его убирают
    вручную на каждой паре. Порог задаётся, потому что настоящую паузу
    в диалоге смыкать нельзя.
    """
    threshold = settings.close_gap_below_ms
    if threshold <= 0:
        return
    for current, following in pairwise(events):
        left = times[current.eid]
        right = times[following.eid]
        space = right[0] - left[1]
        if 0 < space <= threshold:
            left[1] = right[0]


def _apply_keyframes(
    events, times, settings: TimingSettings, fps: FpsModel | None,
    keyframes: KeyframeIndex | None,
) -> set[tuple[int, int]]:
    """Притягивает границы к монтажным склейкам.

    Радиус задан в кадрах, а не в миллисекундах: на 24 и 60 кадрах «пять
    кадров» — это разное время, а профессиональная норма формулируется
    именно в кадрах.

    Возвращает пары ``(eid, граница)``, которые уже стоят на склейке. Их не
    должен трогать следующий шаг: ключевой кадр — это и есть кадр, повторное
    округление может только сдвинуть границу с точного попадания.
    """
    pinned: set[tuple[int, int]] = set()
    if not settings.snap_to_keyframes or not keyframes:
        return pinned
    rate = float(fps.rate) if fps else 23.976
    radius = int(settings.keyframe_radius_frames * 1000 / rate)
    if radius <= 0:
        return pinned

    for event in events:
        pair = times[event.eid]
        for index in (0, 1):
            nearest = keyframes.nearest(pair[index])
            if nearest is not None and abs(nearest - pair[index]) <= radius:
                pair[index] = nearest
                pinned.add((event.eid, index))
        if pair[1] <= pair[0]:
            # Обе границы притянулись к одной склейке — откатываем конец.
            pair[1] = pair[0] + 1
            pinned.discard((event.eid, 1))
    return pinned


def _apply_frame_grid(
    events, times, settings: TimingSettings, fps: FpsModel | None,
    pinned: set[tuple[int, int]] | None = None,
) -> None:
    """Округляет тайминги к сетке кадров.

    Начало — к началу кадра, конец — к последней миллисекунде кадра: иначе
    субтитр мигает на границе, появляясь и исчезая внутри одного кадра.
    """
    if not settings.snap_to_frames or fps is None:
        return
    pinned = pinned or set()
    for event in events:
        pair = times[event.eid]
        if (event.eid, 0) not in pinned:
            pair[0] = fps.snap(pair[0], SnapMode.START)
        if (event.eid, 1) not in pinned:
            pair[1] = fps.snap(pair[1], SnapMode.END)
        if pair[1] <= pair[0]:
            pair[1] = pair[0] + 1


def _resolve_conflicts(
    events, times, context, selected: set[int], gap: int
) -> list[int]:
    """Убирает наезды на соседей, возникшие при расширении.

    Соседи вне выделения не двигаются: пользователь выбрал конкретные реплики,
    и менять остальные без спроса нельзя. Поэтому расширение просто
    ограничивается — а те, кому не хватило места, возвращаются списком,
    чтобы диалог мог о них сказать.

    **Доводка не укорачивает то, что было.** Соседи расширяются навстречу друг
    другу, и без этого правила расширение одной реплики отъедало бы конец у
    предыдущей: пользователь просил «сделать длиннее», а часть реплик
    становилась короче. Поэтому исходные границы — нижняя планка: ужать реплику
    можно только до них и только если она и раньше налезала на соседа.
    """
    unresolved: list[int] = []
    positions = {event.eid: i for i, event in enumerate(context)}

    for event in events:
        pair = times[event.eid]
        index = positions.get(event.eid)
        if index is None:
            continue

        if index > 0:
            previous = context[index - 1]
            limit = times[previous.eid][1] if previous.eid in selected else previous.end
            if pair[0] < limit + gap:
                pair[0] = min(pair[1] - 1, limit + gap)
            # Не двигаем начало позже исходного, если исходное конфликта не давало.
            if pair[0] > event.start >= previous.end:
                pair[0] = event.start

        if index + 1 < len(context):
            following = context[index + 1]
            limit = times[following.eid][0] if following.eid in selected else following.start
            if pair[1] > limit - gap:
                pair[1] = max(pair[0] + 1, limit - gap)
            # Не укорачиваем конец, если исходный на соседа не налезал.
            if pair[1] < event.end <= following.start:
                pair[1] = event.end

        pair[0] = max(0, pair[0])
        if pair[1] <= pair[0]:
            pair[1] = pair[0] + 1
            unresolved.append(event.eid)

    return unresolved
