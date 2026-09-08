"""Сколько всего в документе.

Задача возникает каждый раз, когда работу надо оценить или сдать: сколько
реплик, сколько слов на перевод, сколько минут озвучивания, кто сколько
говорит. Считать это вручную — полдня, а вопрос задают до начала работы.

Отдельно считается **время речи**, а не длительность фильма: суммарная
длительность реплик с вычетом наложений. Для дубляжа платят за него, и
разница с хронометражем бывает вдвое.
"""

from collections import Counter


def collect(doc) -> dict:
    """Считает всё за один проход по документу."""
    events = [e for e in doc.events if not e.comment]

    words = 0
    characters = 0
    by_speaker: Counter[str] = Counter()
    speech_by_speaker: Counter[str] = Counter()
    longest = None
    fastest = None

    for event in events:
        text = event.plain
        count = len(text.split())
        words += count
        characters += len(text)

        speaker = event.name.strip() or "без говорящего"
        by_speaker[speaker] += 1
        speech_by_speaker[speaker] += max(0, event.duration)

        if longest is None or len(text) > len(longest.plain):
            longest = event
        cps = event.cps()
        if cps > 0 and (fastest is None or cps > fastest.cps()):
            fastest = event

    return {
        "реплик": len(events),
        "комментариев": len(doc.events) - len(events),
        "слов": words,
        "знаков": characters,
        "время речи": _speech_time(events),
        "хронометраж": max((e.end for e in events), default=0),
        "говорящих": len(by_speaker),
        "по говорящим": by_speaker,
        "речь по говорящим": speech_by_speaker,
        "самая длинная": longest,
        "самая быстрая": fastest,
        "стилей": len(doc.styles),
    }


def _speech_time(events) -> int:
    """Время речи: объединение отрезков, а не сумма длительностей.

    Реплики накладываются постоянно — двое говорят одновременно, вывеска
    висит поверх диалога. Простая сумма посчитала бы такие места дважды, и
    оценка объёма озвучивания вышла бы завышенной.
    """
    spans = sorted((e.start, e.end) for e in events if e.end > e.start)
    total = 0
    current_start = current_end = None
    for start, end in spans:
        if current_end is None or start > current_end:
            if current_end is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += current_end - current_start
    return total


def _duration(ms: int) -> str:
    ms = max(0, int(ms))
    hours, rest = divmod(ms, 3_600_000)
    minutes, seconds = divmod(rest // 1000, 60)
    if hours:
        return f"{hours} ч {minutes} мин {seconds} с"
    return f"{minutes} мин {seconds} с"


def render(data: dict) -> str:
    """Собирает отчёт в текст, готовый к вставке в письмо."""
    lines = [
        "Объём документа",
        "===============",
        "",
        f"Реплик:           {data['реплик']}",
        f"Слов:             {data['слов']}",
        f"Знаков:           {data['знаков']}",
        f"Стилей:           {data['стилей']}",
    ]
    if data["комментариев"]:
        lines.append(f"Комментариев:     {data['комментариев']} (в кадре не видны)")

    lines += [
        "",
        f"Время речи:       {_duration(data['время речи'])}",
        f"Хронометраж:      {_duration(data['хронометраж'])}",
    ]
    if data["хронометраж"]:
        share = data["время речи"] / data["хронометраж"]
        lines.append(f"Плотность речи:   {share:.0%} времени")

    if data["говорящих"]:
        lines += ["", f"Говорящих: {data['говорящих']}", ""]
        for name, count in data["по говорящим"].most_common():
            speech = _duration(data["речь по говорящим"][name])
            lines.append(f"  {name:<24} {count:>5} реплик, {speech}")

    longest = data["самая длинная"]
    fastest = data["самая быстрая"]
    if longest is not None:
        lines += ["", "Крайние случаи", ""]
        lines.append(f"  самая длинная: {len(longest.plain)} знаков")
        lines.append(f"    «{_shorten(longest.plain)}»")
    if fastest is not None:
        lines.append(f"  самая быстрая: {fastest.cps():.1f} знаков в секунду")
        lines.append(f"    «{_shorten(fastest.plain)}»")
    return "\n".join(lines)


def _shorten(text: str, width: int = 70) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


class _Slice:
    """Часть документа для подсчёта: ровно те поля, которые читает collect."""

    __slots__ = ("events", "styles")

    def __init__(self, events, styles) -> None:
        self.events = events
        self.styles = styles


def setup(context):
    def show() -> None:
        report = render(collect(context.document()))
        context.show_report("Статистика документа", report)

    def show_selection() -> None:
        doc = context.document()
        chosen = set(context.selection())
        if not chosen:
            context.report("Сначала выберите реплики")
            return

        # Считаем по срезу документа: сам документ трогать нельзя, а
        # собирать статистику отдельным путём — значит завести вторую
        # реализацию, которая со временем разойдётся с первой.
        part = _Slice([e for e in doc.events if e.eid in chosen], doc.styles)
        context.show_report(
            f"Статистика: выделено реплик {len(chosen)}", render(collect(part))
        )

    context.register_action("show", "Статистика документа…", show)
    context.register_action("show_selection", "Статистика выделенного…", show_selection)
