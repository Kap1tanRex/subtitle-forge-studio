"""Отчёт о проверке — то, что прикладывают к сданной работе.

Заказчику мало услышать «я проверил»: он хочет видеть, по каким требованиям
проверялось и что осталось. Отчёт отвечает на оба вопроса и делается из уже
посчитанных находок, без повторного прогона.

Два вида. HTML — чтобы открыть и прочитать: сводка по важности, таблица
находок с временем реплики и её текстом. CSV — чтобы вложить в таблицу или
прогнать своим скриптом.

Оформление намеренно скупое и без внешних файлов: отчёт уходит по почте, и
он должен открываться у получателя, а не тянуть за собой стили.
"""

from __future__ import annotations

import csv
import html
import io
from collections.abc import Iterable

from sfstudio.app.i18n import tr
from sfstudio.core.document import SubtitleDocument
from sfstudio.core.plural import plural
from sfstudio.core.time import format_srt
from sfstudio.services.qc import Issue, QcProfile, Severity

__all__ = ["report_csv", "report_html", "summary"]


def summary(issues: Iterable[Issue]) -> dict[Severity, int]:
    """Сколько находок каждой важности."""
    counts = dict.fromkeys(Severity, 0)
    for issue in issues:
        counts[issue.severity] += 1
    return counts


def _rows(doc: SubtitleDocument, issues: Iterable[Issue]) -> list[tuple]:
    """Строки отчёта: номер, время, важность, правило, сообщение, текст."""
    positions = {event.eid: index for index, event in enumerate(doc.events, start=1)}
    rows = []
    for issue in issues:
        event = doc.get(issue.eid)
        if event is None:
            continue
        rows.append((
            positions.get(issue.eid, 0),
            format_srt(event.start),
            format_srt(event.end),
            issue.severity.label,
            issue.rule,
            issue.message,
            event.plain.replace("\n", " ⏎ "),
        ))
    rows.sort(key=lambda row: row[0])
    return rows


HEADERS = (
    "№", tr('Начало'), tr('Конец'), tr('Важность'),
    tr('Правило'), tr('Замечание'), tr('Текст'),
)


def report_csv(doc: SubtitleDocument, issues: Iterable[Issue]) -> str:
    """Отчёт таблицей. Разделитель — точка с запятой, как ждёт Excel."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(_rows(doc, issues))
    return buffer.getvalue()


def report_html(
    doc: SubtitleDocument,
    issues: Iterable[Issue],
    *,
    profile: QcProfile | None = None,
    title: str = tr('Отчёт о проверке'),
) -> str:
    """Отчёт страницей: сводка, требования и таблица находок."""
    issues = list(issues)
    counts = summary(issues)
    rows = _rows(doc, issues)

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="ru"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)}</title>",
        "<style>",
        "body{font:14px/1.5 'Segoe UI',system-ui,sans-serif;margin:24px;color:#16181d}",
        "h1{font-size:20px;margin:0 0 4px}",
        "p.meta{color:#5c6472;margin:0 0 20px}",
        "table{border-collapse:collapse;width:100%}",
        "th,td{border-bottom:1px solid #dfe3ea;padding:6px 8px;text-align:left;"
        "vertical-align:top}",
        "th{background:#f7f8fa;font-weight:600}",
        "td.num{text-align:right;white-space:nowrap}",
        "td.time{white-space:nowrap;font-variant-numeric:tabular-nums}",
        ".error{color:#cf222e;font-weight:600}",
        ".warning{color:#9a6700}",
        ".info{color:#5c6472}",
        "dl{display:grid;grid-template-columns:auto auto;gap:2px 16px;margin:0 0 20px}",
        "dt{color:#5c6472}",
        "dd{margin:0}",
        "</style></head><body>",
        f"<h1>{html.escape(title)}</h1>",
    ]

    where = doc.source_path.name if doc.source_path else tr('без имени')
    parts.append(
        tr('<p class="meta">{0} · {1} · найдено: {2}</p>')
            .format(
                html.escape(where),
                plural(len(doc.events), tr('реплика'), tr('реплики'), tr('реплик')),
                plural(len(issues), tr('замечание'), tr('замечания'), tr('замечаний')),
            )
    )

    parts.append("<dl>")
    parts.extend(
        f"<dt>{severity.label}</dt><dd>{counts[severity]}</dd>"
        for severity in (Severity.ERROR, Severity.WARNING, Severity.INFO)
    )
    if profile is not None:
        parts.append(tr('<dt>профиль</dt>'))
        parts.append(f"<dd>{html.escape(profile.name)}</dd>")
        for label, value in _profile_rows(profile):
            parts.append(f"<dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd>")
    parts.append("</dl>")

    if not rows:
        parts.append(tr('<p>Замечаний нет.</p>'))
    else:
        parts.append("<table><thead><tr>")
        parts.extend(f"<th>{html.escape(name)}</th>" for name in HEADERS)
        parts.append("</tr></thead><tbody>")
        for number, start, end, label, rule, message, text in rows:
            css = {tr('ошибка'): "error", tr('внимание'): "warning"}.get(label, "info")
            parts.append(
                f'<tr><td class="num">{number}</td>'
                f'<td class="time">{html.escape(start)}</td>'
                f'<td class="time">{html.escape(end)}</td>'
                f'<td class="{css}">{html.escape(label)}</td>'
                f"<td>{html.escape(rule)}</td>"
                f"<td>{html.escape(message)}</td>"
                f"<td>{html.escape(text)}</td></tr>"
            )
        parts.append("</tbody></table>")

    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def _profile_rows(profile: QcProfile) -> list[tuple[str, str]]:
    """Требования профиля человеческими словами.

    Только то, что действует: выключенные правила в отчёте лишь путают —
    получатель ищет, что значит «не задано», вместо того чтобы читать
    находки.
    """
    named = (
        ("max_cps", tr('знаков в секунду, не более')),
        ("max_line_length", tr('знаков в строке, не более')),
        ("max_lines", tr('строк в реплике, не более')),
        ("min_duration_ms", tr('длительность, не менее (мс)')),
        ("max_duration_ms", tr('длительность, не более (мс)')),
        ("min_gap_frames", tr('зазор, не менее (кадров)')),
        ("shot_change_frames", tr('до склейки, не ближе (кадров)')),
        ("min_line_length", tr('знаков в строке, не менее')),
    )
    rows = []
    for field_name, label in named:
        value = getattr(profile, field_name, None)
        if value is None:
            continue
        rows.append((label, f"{value:g}" if isinstance(value, float) else str(value)))
    return rows
