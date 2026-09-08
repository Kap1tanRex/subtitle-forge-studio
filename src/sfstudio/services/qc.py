"""Контроль качества субтитров.

Набор правил, каждое из которых умеет проверить одно событие и сказать, что
с ним не так. Правила независимы и подключаются списком, поэтому добавить своё
или отключить чужое можно без правки движка.

Два решения, определяющих устройство модуля:

* **Проверка инкрементальная.** Полный прогон 20 000 событий занимает сотни
  миллисекунд — делать его на каждое нажатие клавиши нельзя. Поэтому
  :meth:`QcRunner.recheck` пересчитывает только изменённые события и их
  соседей (соседи нужны правилам про зазор и перекрытие).
* **Строгость задаётся профилем, а не правилом.** Один и тот же CPS
  недопустим для Netflix и нормален для любительского перевода. Правило
  считает величину, профиль решает, ругаться ли.

Перекрытие событий — **предупреждение, а не ошибка**: в ASS оно совершенно
законно, libass рисует обе реплики по слоям. Считать это ошибкой значило бы
ругаться на корректные файлы с надписями поверх диалога.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from enum import IntEnum

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.core.time import FpsModel

__all__ = ["PROFILES", "Issue", "QcProfile", "QcRunner", "Severity", "default_profile"]


class Severity(IntEnum):
    """Насколько серьёзна находка. Порядок важен: используется для сортировки."""

    INFO = 0
    WARNING = 1
    ERROR = 2

    @property
    def label(self) -> str:
        return {Severity.INFO: "инфо", Severity.WARNING: "внимание", Severity.ERROR: "ошибка"}[self]


@dataclass(frozen=True, slots=True)
class Issue:
    """Одна находка."""

    eid: int
    rule: str
    severity: Severity
    message: str
    #: Значение, из-за которого сработало правило — для показа в панели.
    value: float | None = None

    def __str__(self) -> str:
        return f"[{self.severity.label}] {self.message}"


@dataclass(frozen=True, slots=True)
class QcProfile:
    """Пороги проверок.

    ``None`` в поле означает «правило выключено» — так профиль «Свободный»
    отключает лишнее, не убирая правила из списка.
    """

    name: str = "Общий"
    max_cps: float | None = 17.0
    max_line_length: int | None = 42
    max_lines: int | None = 2
    min_duration_ms: int | None = 833
    max_duration_ms: int | None = 7000
    min_gap_frames: int | None = 2
    check_overlap: bool = True
    check_tags: bool = True
    check_styles: bool = True
    check_typography: bool = True
    check_empty: bool = True
    #: Считать ли пробелы в CPS. Netflix — не считает.
    cps_counts_spaces: bool = False


PROFILES: dict[str, QcProfile] = {
    "general": QcProfile(name="Общий"),
    "netflix-ru": QcProfile(
        name="Netflix (русский)",
        max_cps=17.0,
        max_line_length=42,
        max_lines=2,
        min_duration_ms=833,
        max_duration_ms=7000,
        min_gap_frames=2,
    ),
    "netflix-en": QcProfile(
        name="Netflix (английский)",
        max_cps=20.0,
        max_line_length=42,
    ),
    "loose": QcProfile(
        name="Свободный",
        max_cps=None,
        max_line_length=None,
        max_lines=None,
        min_duration_ms=200,
        max_duration_ms=None,
        min_gap_frames=None,
        check_typography=False,
    ),
}


def default_profile() -> QcProfile:
    return PROFILES["general"]


# --------------------------------------------------------------------------- #
# Правила
# --------------------------------------------------------------------------- #

_DOUBLE_SPACE = re.compile(r"  +")
_HYPHEN_AS_DASH = re.compile(r"(?:^|\n)\s*-\s+")


@dataclass(slots=True)
class _Context:
    """Что правилу нужно знать помимо самого события."""

    doc: SubtitleDocument
    profile: QcProfile
    fps: FpsModel | None
    previous: SubtitleEvent | None
    following: SubtitleEvent | None

    @property
    def min_gap_ms(self) -> int:
        frames = self.profile.min_gap_frames
        if frames is None:
            return 0
        rate = float(self.fps.rate) if self.fps else 23.976
        return int(frames * 1000 / rate)

    def issue(
        self,
        event: SubtitleEvent,
        rule: str,
        message: str,
        *,
        severity: Severity = Severity.WARNING,
        value: float | None = None,
    ) -> Issue:
        """Создаёт находку. Удобство для правил из плагинов.

        Без него плагин собирал бы ``Issue`` сам, а для этого — импортировал
        внутренние классы проверок. Контекст правило и так получает; пусть
        через него и заводится находка, тогда плагин не зависит от того, как
        она устроена внутри.
        """
        return Issue(event.eid, rule, severity, message, value)


def _check_cps(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    limit = ctx.profile.max_cps
    if limit is None or event.duration <= 0:
        return
    value = event.cps(count_spaces=ctx.profile.cps_counts_spaces)
    if value > limit:
        yield Issue(
            event.eid, "cps", Severity.WARNING,
            f"Слишком быстро: {value:.1f} симв/с при норме {limit:.0f}",
            value,
        )


def _check_line_length(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    limit = ctx.profile.max_line_length
    if limit is None:
        return
    longest = event.longest_line
    if longest > limit:
        yield Issue(
            event.eid, "line_length", Severity.WARNING,
            f"Строка длиннее нормы: {longest} симв. при норме {limit}",
            float(longest),
        )


def _check_line_count(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    limit = ctx.profile.max_lines
    if limit is None:
        return
    count = event.line_count
    if count > limit:
        yield Issue(
            event.eid, "line_count", Severity.WARNING,
            f"Строк в реплике: {count} при норме {limit}", float(count),
        )


def _check_duration(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    low = ctx.profile.min_duration_ms
    high = ctx.profile.max_duration_ms
    if event.duration <= 0:
        yield Issue(
            event.eid, "duration", Severity.ERROR,
            "Нулевая или отрицательная длительность", float(event.duration),
        )
        return
    if low is not None and event.duration < low:
        yield Issue(
            event.eid, "min_duration", Severity.WARNING,
            f"Слишком коротко: {event.duration} мс при минимуме {low} мс",
            float(event.duration),
        )
    if high is not None and event.duration > high:
        yield Issue(
            event.eid, "max_duration", Severity.INFO,
            f"Долго висит: {event.duration / 1000:.1f} с", float(event.duration),
        )


def _check_gap(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    if ctx.following is None or ctx.profile.min_gap_frames is None:
        return
    gap = ctx.following.start - event.end
    minimum = ctx.min_gap_ms
    if 0 < gap < minimum:
        yield Issue(
            event.eid, "min_gap", Severity.WARNING,
            f"Зазор до следующей реплики {gap} мс, нужно не меньше {minimum} мс",
            float(gap),
        )


def _check_overlap(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    """Перекрытие — предупреждение, а не ошибка.

    В ASS перекрывающиеся события легальны: libass рисует их по слоям, и
    надпись поверх диалога — обычный приём. Ошибкой это считать нельзя.
    """
    if not ctx.profile.check_overlap or ctx.following is None:
        return
    if event.layer != ctx.following.layer:
        return  # разные слои — так и задумано
    if event.end > ctx.following.start:
        overlap = event.end - ctx.following.start
        yield Issue(
            event.eid, "overlap", Severity.WARNING,
            f"Перекрывает следующую реплику на {overlap} мс", float(overlap),
        )


def _check_tags(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    if not ctx.profile.check_tags:
        return
    text = event.text
    if text.count("{") != text.count("}"):
        yield Issue(
            event.eid, "unbalanced_tags", Severity.ERROR,
            "Непарные фигурные скобки в тексте",
        )
        return
    # Пустой блок {} безвреден, но обычно это след удалённого тега.
    for block in event.parsed.blocks:
        if not block.tags and not block.comment:
            yield Issue(
                event.eid, "empty_tag_block", Severity.INFO,
                "Пустой блок тегов {}",
            )
            break


def _check_style(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    if not ctx.profile.check_styles:
        return
    if event.style not in ctx.doc.styles:
        yield Issue(
            event.eid, "missing_style", Severity.ERROR,
            f"Стиль «{event.style}» не определён в документе",
        )


def _check_empty(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    if not ctx.profile.check_empty or event.comment:
        return
    if not event.plain.strip():
        yield Issue(event.eid, "empty_text", Severity.WARNING, "Пустая реплика")


def _check_typography(event: SubtitleEvent, ctx: _Context) -> Iterator[Issue]:
    if not ctx.profile.check_typography:
        return
    plain = event.plain
    if _DOUBLE_SPACE.search(plain):
        yield Issue(event.eid, "double_space", Severity.INFO, "Двойной пробел")
    for line in plain.split("\n"):
        if line != line.rstrip():
            yield Issue(event.eid, "trailing_space", Severity.INFO, "Пробел в конце строки")
            break
    if _HYPHEN_AS_DASH.search(plain):
        yield Issue(
            event.eid, "wrong_dash", Severity.INFO,
            "Дефис вместо тире в начале реплики",
        )


#: Порядок правил определяет порядок находок внутри одного события.
RULES = (
    _check_style,
    _check_tags,
    _check_duration,
    _check_cps,
    _check_line_length,
    _check_line_count,
    _check_overlap,
    _check_gap,
    _check_empty,
    _check_typography,
)

#: Правила, добавленные извне — плагинами. Отдельный список, а не дополнение
#: к ``RULES``: встроенный набор должен оставаться тем же самым независимо от
#: того, что стоит у пользователя, иначе воспроизвести чужую жалобу на
#: проверки становится нельзя.
EXTRA_RULES: list = []


def register_rule(rule) -> None:
    """Добавляет проверку. Правило — функция ``(event, ctx) -> Iterator[Issue]``.

    Проверяем вызываемость сразу: правило, оказавшееся не функцией, иначе
    сломало бы прогон на первом же событии — с сообщением, по которому
    невозможно понять, чей это плагин.
    """
    if not callable(rule):
        raise TypeError("правило проверки должно быть функцией")
    EXTRA_RULES.append(rule)


def unregister_rule(rule) -> bool:
    """Убирает одно внешнее правило. ``False`` — такого не было.

    Нужно для выключения плагина без перезапуска: правило, оставшееся в
    списке, продолжало бы отмечать реплики от имени плагина, который человек
    только что выключил.
    """
    try:
        EXTRA_RULES.remove(rule)
    except ValueError:
        return False
    return True


def clear_extra_rules() -> None:
    """Убирает все внешние правила. Нужно тестам и перезагрузке плагинов."""
    EXTRA_RULES.clear()


# --------------------------------------------------------------------------- #
# Движок
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class QcRunner:
    """Хранит находки и умеет пересчитывать их точечно."""

    profile: QcProfile = field(default_factory=default_profile)
    fps: FpsModel | None = None
    _issues: dict[int, list[Issue]] = field(default_factory=dict)

    # -- полный и инкрементальный прогон ------------------------------------- #

    def run_all(self, doc: SubtitleDocument) -> None:
        """Полный прогон. Для открытия файла и смены профиля."""
        self._issues.clear()
        for eid in list(doc.index.ordered_eids()):
            found = list(self._check(doc.by_eid(eid), doc))
            if found:
                self._issues[eid] = found

    def recheck(self, doc: SubtitleDocument, eids: Iterable[int]) -> set[int]:
        """Пересчитывает указанные события и их соседей.

        Соседи обязательны: правила про зазор и перекрытие смотрят на
        следующую реплику, поэтому правка одного события меняет вердикт для
        предыдущего. Возвращает множество eid, чьи находки изменились —
        по нему панель обновляет только нужные строки.
        """
        index = doc.index
        targets: set[int] = set()
        changed: set[int] = set()

        for eid in eids:
            if not doc.has(eid):
                # Событие удалено — находки тоже.
                if self._issues.pop(eid, None) is not None:
                    changed.add(eid)
                continue
            targets.add(eid)
            before, after = index.neighbours(eid)
            if before is not None:
                targets.add(before)
            if after is not None:
                targets.add(after)

        for eid in targets:
            if not doc.has(eid):
                continue
            found = list(self._check(doc.by_eid(eid), doc))
            previous = self._issues.get(eid, [])
            if found != previous:
                changed.add(eid)
            if found:
                self._issues[eid] = found
            else:
                self._issues.pop(eid, None)
        return changed

    def _check(self, event: SubtitleEvent, doc: SubtitleDocument) -> Iterator[Issue]:
        """Проверяет одно событие.

        Соседи берутся из ``TimeIndex`` за O(1), а не пересортировкой
        документа: правку проверяют на каждое нажатие клавиши, и сортировка
        двадцати тысяч событий на каждый символ съедала бы весь бюджет.
        """
        before_eid, after_eid = doc.index.neighbours(event.eid)
        ctx = _Context(
            doc=doc,
            profile=self.profile,
            fps=self.fps,
            previous=doc.get(before_eid) if before_eid is not None else None,
            following=doc.get(after_eid) if after_eid is not None else None,
        )
        for rule in RULES:
            yield from rule(event, ctx)

        # Внешние правила — после встроенных и каждое под своей защитой:
        # исключение в проверке из плагина не должно лишать человека
        # остальных находок по этой реплике.
        for rule in EXTRA_RULES:
            try:
                yield from rule(event, ctx)
            except Exception as exc:
                yield Issue(
                    event.eid,
                    "plugin_rule",
                    Severity.WARNING,
                    f"Проверка из плагина не сработала: {type(exc).__name__}: {exc}",
                )

    # -- доступ к находкам ---------------------------------------------------- #

    def issues_for(self, eid: int) -> list[Issue]:
        return self._issues.get(eid, [])

    def worst_for(self, eid: int) -> Severity | None:
        found = self._issues.get(eid)
        return max((i.severity for i in found), default=None) if found else None

    def all_issues(self) -> list[Issue]:
        """Все находки, отсортированные по серьёзности, затем по событию."""
        flat = [issue for group in self._issues.values() for issue in group]
        flat.sort(key=lambda i: (-int(i.severity), i.eid, i.rule))
        return flat

    def counts(self) -> dict[Severity, int]:
        result = dict.fromkeys(Severity, 0)
        for group in self._issues.values():
            for issue in group:
                result[issue.severity] += 1
        return result

    def summary(self) -> str:
        counts = self.counts()
        if not any(counts.values()):
            return "Проблем не найдено"
        parts = [
            f"{counts[level]} {level.label}"
            for level in (Severity.ERROR, Severity.WARNING, Severity.INFO)
            if counts[level]
        ]
        return " · ".join(parts)

    def set_profile(self, profile: QcProfile, doc: SubtitleDocument) -> None:
        self.profile = profile
        self.run_all(doc)

    def with_threshold(self, **changes: object) -> QcProfile:
        """Копия профиля с изменёнными порогами — для панели настроек."""
        return replace(self.profile, **changes)  # type: ignore[arg-type]
