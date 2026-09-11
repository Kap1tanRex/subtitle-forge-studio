"""Пересчёт таймингов при несовпадении частоты кадров.

Субтитры почти всегда делают под конкретную копию фильма. Если копия была
24 fps, а на руках 23.976 (те же кадры, но чуть медленнее), то каждая реплика
уезжает на одну тысячную своего времени. В начале серии это незаметно —
четыре кадра за первые пять минут, — а к финалу набегает несколько секунд, и
реплики идут с явным опозданием. Это и есть «накопленный сдвиг»: ошибка не
постоянная, она растёт вместе со временем.

Лечится он растяжением всей дорожки, а не сдвигом: сдвиг увёл бы начало,
оставив конец на месте. Новое время реплики — старое, умноженное на
отношение частот::

    новое = старое × (частота, под которую делали) / (частота видео)

Числа берутся точными дробями (``24000/1001``, а не 23.976): при округлении
до трёх знаков ошибка сама по себе даёт полсекунды на двухчасовом фильме —
ровно то, от чего мы тут лечим.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from sfstudio.app.i18n import tr
from sfstudio.core.commands.timing import SyncPoints
from sfstudio.core.time import FpsModel

__all__ = [
    "COMMON_RATES",
    "FAMILIES",
    "FpsConversion",
    "describe_rate",
    "likely_sources",
]

#: Частоты, между которыми и случаются перепутывания. Дробями, не числами.
COMMON_RATES: tuple[Fraction, ...] = (
    Fraction(24000, 1001),   # 23.976 — «фильм» в NTSC
    Fraction(24, 1),
    Fraction(25, 1),         # PAL
    Fraction(30000, 1001),   # 29.97
    Fraction(30, 1),
    Fraction(48000, 1001),
    Fraction(48, 1),
    Fraction(50, 1),
    Fraction(60000, 1001),   # 59.94
    Fraction(60, 1),
)

#: Частоты, которые путают друг с другом чаще всего: одно семейство — одни и
#: те же кадры, разница только в NTSC-замедлении на 0,1 %.
FAMILIES: tuple[tuple[Fraction, ...], ...] = (
    (Fraction(24000, 1001), Fraction(24, 1), Fraction(25, 1)),
    (Fraction(30000, 1001), Fraction(30, 1)),
    (Fraction(48000, 1001), Fraction(48, 1), Fraction(50, 1)),
    (Fraction(60000, 1001), Fraction(60, 1)),
)

#: Ниже этого масштаб считаем единичным. 1e-9 — это миллисекунда на двенадцать
#: суток: такое «расхождение» чинить нечего.
NEGLIGIBLE = 1e-9


def describe_rate(rate: Fraction | FpsModel) -> str:
    """«23.976 fps» вместо «24000/1001» — читают люди, а не машины."""
    value = rate.rate if isinstance(rate, FpsModel) else rate
    return tr('{0:g} fps').format(round(float(value), 3))


@dataclass(frozen=True, slots=True)
class FpsConversion:
    """Из какой частоты в какую пересчитываем.

    ``source`` — частота, под которую субтитры делали; ``target`` — частота
    видео, к которому их прикладывают. Порядок именно такой, и перепутать его
    нельзя: обратное преобразование уводит реплики в другую сторону ровно на
    столько же.
    """

    source: Fraction
    target: Fraction

    def __post_init__(self) -> None:
        if self.source <= 0 or self.target <= 0:
            raise ValueError(tr('Частота кадров должна быть положительной'))

    @classmethod
    def between(cls, source: Fraction | FpsModel, target: Fraction | FpsModel) -> FpsConversion:
        """Принимает и дроби, и кадровые модели — вызывающему так удобнее."""
        return cls(_rate(source), _rate(target))

    @property
    def scale(self) -> float:
        """Во сколько раз растягивается время."""
        return float(self.source / self.target)

    @property
    def is_noop(self) -> bool:
        return abs(self.scale - 1.0) < NEGLIGIBLE

    @property
    def same_family(self) -> bool:
        """Частоты из одного семейства — тот самый случай NTSC-замедления.

        Различать стоит: 23.976 против 24 — почти наверняка перепутанная
        копия, а 25 против 60 — скорее ошибка в выборе файла, и предлагать
        такое исправление без спроса не надо.
        """
        return any(
            self.source in family and self.target in family for family in FAMILIES
        )

    def drift_ms(self, at_ms: int) -> int:
        """Насколько уедет реплика, стоящая на ``at_ms``.

        Положительное — субтитры отстают от речи и их надо растянуть.
        """
        return round(at_ms * (self.scale - 1.0))

    def map_ms(self, at_ms: int) -> int:
        """Куда переедет момент времени. Время до нуля не опускается."""
        return max(0, round(at_ms * self.scale))

    def points(self, span_ms: int = 3_600_000) -> SyncPoints:
        """Опорные точки для :class:`LinearSync`.

        Ноль остаётся нулём: начало дорожки — общая точка отсчёта для любой
        копии, а растягивать надо то, что после него.

        ``span_ms`` — любая заметная длина; от неё результат не зависит, она
        нужна лишь чтобы задать вторую пару точек с достаточной точностью.
        """
        span = max(1000, int(span_ms))
        return SyncPoints(src_a=0, dst_a=0, src_b=span, dst_b=self.map_ms(span))

    def describe(self) -> str:
        return tr('{0} → {1} (×{2:.6g})').format(
            describe_rate(self.source), describe_rate(self.target), self.scale
        )

    def describe_drift(self, span_ms: int) -> str:
        """Человеческое «к концу уедет на столько-то»."""
        drift = self.drift_ms(span_ms)
        if drift == 0:
            return tr('расхождения нет')
        seconds = abs(drift) / 1000
        where = tr('субтитры отстают') if drift > 0 else tr('субтитры спешат')
        if seconds < 60:
            amount = tr('{0:.1f} с').format(seconds)
        else:
            amount = tr('{0:.0f} мин {1:.0f} с').format(seconds // 60, seconds % 60)
        return tr('{0}: к концу {1}').format(where, amount)


def likely_sources(target: Fraction | FpsModel) -> list[Fraction]:
    """Частоты, из которых стоит предложить пересчёт для данного видео.

    Не весь список подряд: для видео 23.976 осмысленны 24 и 25, а 59.94
    в этом окне только мешает. Порядок — по убыванию правдоподобия: сперва
    соседи по семейству, потом остальное.
    """
    rate = _rate(target)
    near = [
        candidate
        for family in FAMILIES
        if rate in family
        for candidate in family
        if candidate != rate
    ]
    rest = [c for c in COMMON_RATES if c != rate and c not in near]
    return near + rest


def _rate(value: Fraction | FpsModel) -> Fraction:
    return value.rate if isinstance(value, FpsModel) else Fraction(value)
