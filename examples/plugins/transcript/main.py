"""Выгрузка субтитров в текст и в таблицу.

Две настоящие задачи, ради которых субтитры регулярно выносят из редактора.

**Транскрипт** — сплошной текст без таймингов. Его отдают на вычитку
редактору, отправляют переводчику, кладут в описание к видео. Формат ASS для
этого непригоден: в нём разметка, тайминги и служебные поля, а нужен текст.

**Монтажный лист** — таблица с временем, говорящим и репликой. По ней сводят
звук, считают объём дубляжа, согласовывают правки. Открывается в любой
программе для таблиц.

Оба формата **только на запись**: восстановить из транскрипта тайминги
невозможно, и делать вид, что можно, — значит обещать несбыточное. Программа
это понимает: формат без читателя просто не появляется в списке открытия.
"""

import csv
import io


def write_transcript(doc, **_kwargs) -> str:
    """Сплошной текст: одна реплика — один абзац.

    Разметка убирается (``event.plain``), перенос внутри реплики становится
    пробелом: в тексте для чтения разрыв строки нёс бы смысл, которого в нём
    нет — он там ради ширины кадра.

    Реплики одного говорящего подряд склеиваются в абзац: в расшифровке
    диалога это и есть его речь, разбитая по кадрам, а не десять отдельных
    высказываний.
    """
    paragraphs = []
    current_speaker = None
    current: list[str] = []

    for event in doc.events:
        if event.comment:
            continue  # комментарии — служебные пометки, не речь
        text = " ".join(event.plain.split())
        if not text:
            continue

        speaker = event.name.strip()
        if speaker != current_speaker and current:
            paragraphs.append((current_speaker, " ".join(current)))
            current = []
        current_speaker = speaker
        current.append(text)

    if current:
        paragraphs.append((current_speaker, " ".join(current)))

    lines = []
    for speaker, text in paragraphs:
        lines.append(f"{speaker}: {text}" if speaker else text)
    return "\n\n".join(lines) + "\n"


def write_sheet(doc, **_kwargs) -> str:
    """Монтажный лист: время, говорящий, текст, длительность, скорость.

    Разделитель — точка с запятой, а не запятая: русский Excel по умолчанию
    ждёт именно её, и файл с запятыми открывается одним столбцом. Перед
    строками ставится метка порядка байтов — без неё тот же Excel читает
    UTF-8 как однобайтовую кодировку и показывает кракозябры.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["№", "Начало", "Конец", "Длит., с", "Говорит", "Текст", "Симв/с"])

    for number, event in enumerate(doc.events, start=1):
        writer.writerow([
            number,
            _timecode(event.start),
            _timecode(event.end),
            f"{event.duration / 1000:.2f}".replace(".", ","),
            event.name,
            " ".join(event.plain.split()),
            f"{event.cps():.1f}".replace(".", ","),
        ])
    return "\ufeff" + buffer.getvalue()


def _timecode(ms: int) -> str:
    """Час:минута:секунда,доли — как принято в монтажных листах."""
    ms = max(0, int(ms))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def setup(context):
    from sfstudio.io.registry import FormatSpec

    context.register_format(
        FormatSpec(
            fid="transcript",
            title="Транскрипт (сплошной текст)",
            extensions=(".txt",),
            reader=None,   # обратно не собирается: таймингов в тексте нет
            writer=write_transcript,
        )
    )
    context.register_format(
        FormatSpec(
            fid="sheet",
            title="Монтажный лист (таблица)",
            extensions=(".csv",),
            reader=None,
            writer=write_sheet,
        )
    )
