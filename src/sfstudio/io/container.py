"""Субтитры внутри медиаконтейнеров: чтение дорожек и запись обратно.

Закрывает сценарий S3 спецификации — открыть MKV, поправить вшитую дорожку,
сохранить.

Чтение идёт через PyAV, запись — через ffmpeg CLI (см. :mod:`sfstudio.platform.ffmpeg`).

Как устроены ASS-субтитры внутри MKV
------------------------------------

Это не просто вложенный ``.ass``-файл, и наивное склеивание даёт мусор:

* **Заголовок** (``[Script Info]``, ``[V4+ Styles]``, строка ``Format:`` секции
  ``[Events]``) лежит в ``codec_context.extradata`` — то есть один раз на всю
  дорожку.
* **Закомментированные строки** ffmpeg тоже кладёт в extradata: у ``Comment:``
  нет пакета, потому что нечего показывать. Их легко потерять, если разбирать
  extradata как «просто заголовок».
* **Пакеты** содержат поля в *транспортном* порядке
  ``ReadOrder,Layer,Style,Name,MarginL,MarginR,MarginV,Effect,Text`` — без
  времени и без ``Start``/``End``, хотя строка ``Format:`` в заголовке
  описывает файловый порядок с ними. Время берётся из ``pts`` и ``duration``
  пакета.

Поэтому извлечение собирается в два шага: заголовок разбирается нашим же
ридером ASS (он заодно подхватывает стили и комментарии), а события из пакетов
добавляются к результату.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sfstudio.core.document import SubtitleDocument
from sfstudio.core.event import SubtitleEvent
from sfstudio.io.formats.ass import read_ass
from sfstudio.io.formats.srt import read_srt
from sfstudio.media.probe import MediaInfo, SubtitleTrackInfo, probe
from sfstudio.platform.ffmpeg import FfmpegError, run_ffmpeg

__all__ = [
    "ContainerError",
    "MuxOptions",
    "UnsupportedTrackError",
    "extract_subtitles",
    "list_tracks",
    "mux_subtitles",
]

#: Порядок полей в пакете ASS внутри контейнера. Отличается от файлового.
_PACKET_FIELDS = 9  # ReadOrder, Layer, Style, Name, ML, MR, MV, Effect, Text

#: Какой кодек субтитров допускает контейнер.
CONTAINER_CODEC = {
    ".mkv": "ass",
    ".mka": "ass",
    ".webm": "webvtt",
    ".mp4": "mov_text",
    ".m4v": "mov_text",
    ".mov": "mov_text",
}


class ContainerError(RuntimeError):
    """Не удалось прочитать или записать контейнер."""


class UnsupportedTrackError(ContainerError):
    """Дорожка есть, но её нельзя открыть на правку."""


def list_tracks(path: Path) -> list[SubtitleTrackInfo]:
    """Все субтитровые дорожки файла, включая неподдерживаемые.

    Битмапные (PGS, VobSub) тоже возвращаются: пользователь должен видеть, что
    дорожка существует, и понимать, почему она недоступна. Прятать их — значит
    выглядеть так, будто субтитров в файле нет.
    """
    return probe(path).subtitles


def extract_subtitles(path: Path, stream_index: int) -> SubtitleDocument:
    """Извлекает дорожку в документ."""
    try:
        import av
    except (ImportError, OSError) as exc:
        raise ContainerError(f"PyAV недоступен: {exc}") from exc

    info = probe(path)
    track = next((t for t in info.subtitles if t.index == stream_index), None)
    if track is None:
        raise ContainerError(f"дорожки #{stream_index} нет в {path.name}")
    if track.is_bitmap:
        raise UnsupportedTrackError(
            f"дорожка #{stream_index} ({track.codec}) — картинка, "
            "для правки нужен OCR"
        )
    if not track.is_editable:
        raise UnsupportedTrackError(f"кодек {track.codec} не поддерживается")

    container = av.open(str(path))
    try:
        stream = next(s for s in container.streams.subtitles if s.index == stream_index)
        codec = getattr(getattr(stream.codec_context, "codec", None), "name", "")
        extradata = getattr(stream.codec_context, "extradata", None) or b""
        time_base = stream.time_base

        rows: list[tuple[int, int, str]] = []
        for packet in container.demux(stream):
            if packet.pts is None:
                continue
            payload = bytes(packet).decode("utf-8", errors="replace").strip("\x00")
            if not payload:
                continue
            start = int(float(packet.pts * time_base) * 1000)
            duration = int(float((packet.duration or 0) * time_base) * 1000)
            rows.append((start, start + duration, payload))
    finally:
        container.close()

    is_ass = codec in ("ass", "ssa")
    doc = _build_from_ass(extradata, rows) if is_ass else _build_from_text(rows)

    doc.source_path = path
    doc.source_format = "ass" if is_ass else "srt"
    return doc


def _build_from_ass(extradata: bytes, rows: list[tuple[int, int, str]]) -> SubtitleDocument:
    """Заголовок из extradata + события из пакетов."""
    header = extradata.decode("utf-8", errors="replace")
    try:
        # Наш ридер подхватывает Script Info, стили и Comment-строки, которые
        # ffmpeg тоже держит в extradata — у них нет пакетов.
        doc = read_ass(header)
    except Exception:
        doc = SubtitleDocument.blank()

    for order, (start, end, payload) in enumerate(rows):
        event = _parse_packet(payload, start, end, doc, order)
        if event is not None:
            doc.add_event(event)

    # Порядок в документе — по времени: ReadOrder в контейнере отражает
    # исходную нумерацию, но соседние дорожки могут её не соблюдать.
    doc.events.sort(key=lambda e: (e.start, e.read_order))
    doc.rebuild_lookup()
    return doc


def _parse_packet(
    payload: str, start: int, end: int, doc: SubtitleDocument, order: int
) -> SubtitleEvent | None:
    """Разбирает строку пакета транспортного формата.

    ``ReadOrder,Layer,Style,Name,MarginL,MarginR,MarginV,Effect,Text`` —
    ровно девять полей, последнее может содержать запятые.
    """
    parts = payload.split(",", _PACKET_FIELDS - 1)
    if len(parts) < _PACKET_FIELDS:
        # Битый пакет пропускаем: терять одну реплику лучше, чем всю дорожку.
        return None

    def as_int(value: str, default: int = 0) -> int:
        try:
            return int(value.strip() or default)
        except ValueError:
            return default

    return SubtitleEvent(
        eid=doc.new_eid(),
        start=start,
        end=end,
        text=parts[8],
        style=parts[2].strip() or "Default",
        layer=as_int(parts[1]),
        name=parts[3].strip(),
        margin_l=as_int(parts[4]),
        margin_r=as_int(parts[5]),
        margin_v=as_int(parts[6]),
        effect=parts[7].strip(),
        read_order=as_int(parts[0], order),
    )


def _build_from_text(rows: list[tuple[int, int, str]]) -> SubtitleDocument:
    """Текстовые дорожки без заголовка: subrip, webvtt, mov_text."""
    blocks: list[str] = []
    for index, (start, end, payload) in enumerate(rows, start=1):
        blocks.append(
            f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{payload}\n"
        )
    return read_srt("\n".join(blocks))


def _srt_time(ms: int) -> str:
    from sfstudio.core.time import format_srt

    return format_srt(ms)


# --------------------------------------------------------------------------- #
# Запись
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MuxOptions:
    """Настройки записи субтитров в контейнер."""

    language: str | None = None
    title: str | None = None
    default: bool = False
    forced: bool = False
    #: Индекс заменяемой дорожки. ``None`` — добавить новую.
    replace_index: int | None = None


def mux_subtitles(
    media: Path,
    subtitle_file: Path,
    output: Path,
    options: MuxOptions | None = None,
    *,
    progress=None,
    cancel=None,
) -> None:
    """Пересобирает контейнер с новой дорожкой субтитров.

    Видео и аудио копируются без перекодирования (``-c copy``), поэтому
    операция быстрая и не теряет качество.

    Запись атомарная: сначала во временный файл рядом, затем переименование.
    Прерванный ремукс не оставит обрезанный файл на месте исходного — а для
    файла, над которым человек работал час, это не мелочь.
    """
    options = options or MuxOptions()
    suffix = output.suffix.lower()
    codec = CONTAINER_CODEC.get(suffix)
    if codec is None:
        raise ContainerError(
            f"контейнер {suffix or '?'} не поддерживается для записи субтитров"
        )

    info = probe(media)
    args = ["-i", str(media), "-i", str(subtitle_file)]

    if options.replace_index is None:
        args += ["-map", "0", "-map", "1:0"]
    else:
        # -map 0 берёт всё, затем минус-маппинг убирает заменяемую дорожку.
        position = _subtitle_position(info, options.replace_index)
        args += ["-map", "0", "-map", f"-0:s:{position}", "-map", "1:0"]

    args += ["-c", "copy", "-c:s", codec]

    # Новая дорожка идёт последней среди субтитровых.
    new_position = len(info.subtitles) if options.replace_index is None else len(info.subtitles) - 1
    new_position = max(0, new_position)
    if options.language:
        args += [f"-metadata:s:s:{new_position}", f"language={options.language}"]
    if options.title:
        args += [f"-metadata:s:s:{new_position}", f"title={options.title}"]

    flags = []
    if options.default:
        flags.append("default")
    if options.forced:
        flags.append("forced")
    args += [f"-disposition:s:{new_position}", "+".join(flags) if flags else "0"]

    temporary = output.with_name(output.name + f".tmp-{id(options):x}{suffix}")
    args.append(str(temporary))

    try:
        result = run_ffmpeg(
            args, total_ms=info.duration_ms, progress=progress, cancel=cancel
        )
        if not result.ok:
            raise ContainerError(
                f"ffmpeg вернул код {result.returncode}:\n{result.stderr}"
            )
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise ContainerError("ffmpeg отработал, но файл пустой")
        temporary.replace(output)
    except FfmpegError as exc:
        raise ContainerError(str(exc)) from exc
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _subtitle_position(info: MediaInfo, stream_index: int) -> int:
    """Порядковый номер дорожки среди субтитровых.

    ffmpeg в ``-map -0:s:N`` ждёт номер внутри своего типа, а не глобальный
    индекс потока. Спутать их — значит удалить не ту дорожку.
    """
    for position, track in enumerate(info.subtitles):
        if track.index == stream_index:
            return position
    raise ContainerError(f"дорожки #{stream_index} нет в файле")


def lossy_warning(output: Path) -> str | None:
    """Предупреждение о потерях для выбранного контейнера.

    MP4 умеет только ``mov_text`` — это простой текст без стилей и позиций.
    Молча уронить всё оформление нельзя: пользователь должен решить сам.
    """
    codec = CONTAINER_CODEC.get(output.suffix.lower())
    if codec == "mov_text":
        return (
            "MP4 хранит субтитры только как простой текст (mov_text): "
            "стили, цвета и позиции \\pos будут потеряны. "
            "Для сохранения оформления выберите MKV."
        )
    if codec == "webvtt":
        return (
            "WebM хранит субтитры в WebVTT: сложное оформление ASS "
            "будет упрощено."
        )
    return None
